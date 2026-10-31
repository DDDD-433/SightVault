"""Bounded LFM3-VL thinking parser. Hidden reasoning text is never read or saved."""
from __future__ import annotations

import re
import time

THINK_START = '<think>'
THINK_END = '</think>'
TOTAL_TOKEN_CAP = 520
RUNTIME_THINKING_BUDGET = 126


class ThinkingOutputError(RuntimeError):
    pass


def _marker_id(tokenizer, marker: str) -> int:
    ids = tokenizer.encode(marker, add_special_tokens=False)
    if len(ids) != 1:
        raise ThinkingOutputError(
            f'Pinned tokenizer encodes {marker!r} as {len(ids)} tokens; safe boundary parsing is unavailable.')
    return int(ids[0])


def _template_preopens_thinking(tokenizer, formatted: str, start_id: int, end_id: int) -> bool:
    """Require the enabled assistant template to end inside a thinking block.

    Looking only for a marker anywhere would accept a user-provided ``<think>``
    inside OCR or prompt text. The pinned MLX-VLM template leaves its assistant
    prefix open at the end of the formatted prompt, so check only its final
    tokens and reject a later closing marker.
    """
    ids = tokenizer.encode(formatted, add_special_tokens=False)
    suffix = ids[-8:]
    positions = [index for index, token in enumerate(suffix) if int(token) == start_id]
    if not positions:
        return False
    last_start = positions[-1]
    return end_id not in [int(token) for token in suffix[last_start + 1:]]


def generate_bounded_thinking(model, processor, image, prompt: str, *,
                              thinking_cap: int = 128, final_cap: int = 384) -> tuple[str, dict]:
    """Generate one final answer with independently bounded hidden/final tokens.

    Only token ids are inspected while streaming. In particular, the runtime's
    ``GenerationResult.text`` is never accessed, because it may include hidden
    reasoning. Returned metadata contains counts and finish state only.
    """
    if thinking_cap != 128 or final_cap < 1 or final_cap > 384:
        raise ValueError('Thinking cap must be 128 and final cap must be between 1 and 384.')

    from mlx_vlm import stream_generate
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.utils import StoppingCriteria

    formatted = apply_chat_template(
        processor, model.config, prompt, num_images=1, enable_thinking=True)
    tokenizer = processor.tokenizer if hasattr(processor, 'tokenizer') else processor
    start_id = _marker_id(tokenizer, THINK_START)
    end_id = _marker_id(tokenizer, THINK_END)
    if not _template_preopens_thinking(tokenizer, formatted, start_id, end_id):
        raise ThinkingOutputError(
            'The enabled-thinking chat template does not end with an open assistant <think> block.')

    eos_token_ids = getattr(model.config, 'eos_token_id', None)
    tokenizer.stopping_criteria = StoppingCriteria(eos_token_ids, tokenizer=tokenizer)
    stop_ids = set(tokenizer.stopping_criteria.eos_token_ids)
    stream = stream_generate(
        model, processor, formatted, image=[image], max_tokens=TOTAL_TOKEN_CAP,
        temperature=0, seed=1, verbose=False, enable_thinking=True,
        thinking_start_token=THINK_START, thinking_end_token=THINK_END,
        thinking_budget=RUNTIME_THINKING_BUDGET)
    started = time.perf_counter()
    model_tokens = thinking_tokens = 0
    final_ids: list[int] = []
    in_thinking = True
    saw_end = False
    forced_terminator = False
    runtime_finish_reason = None
    finish_reason = 'total_cap'
    try:
        for item in stream:
            token = getattr(item, 'token', None)
            if token is None:
                continue
            token_id = int(token.item()) if hasattr(token, 'item') else int(token)
            model_tokens += 1
            if token_id in stop_ids:
                runtime_finish_reason = 'stop'
                finish_reason = 'natural_stop'
                break
            criteria = getattr(tokenizer, 'thinking_budget_criteria', None)
            forced_terminator = forced_terminator or bool(
                criteria is not None and getattr(criteria, 'forced_token_id', None) is not None)
            if token_id == start_id:
                in_thinking = True
            elif token_id == end_id:
                if in_thinking:
                    in_thinking = False
                    saw_end = True
            elif in_thinking:
                thinking_tokens += 1
            elif saw_end:
                final_ids.append(token_id)

            if saw_end and len(final_ids) >= final_cap:
                finish_reason = 'final_answer_cap'
                break
            item_reason = getattr(item, 'finish_reason', None)
            if item_reason:
                runtime_finish_reason = item_reason
                finish_reason = 'natural_stop' if item_reason == 'stop' else 'total_cap'
    finally:
        stream.close()

    respected = thinking_tokens <= thinking_cap
    if not saw_end:
        finish_reason = 'incomplete_thinking_block'
    elif not final_ids:
        finish_reason = 'no_final_answer'
    elif not respected:
        finish_reason = 'thinking_cap_violation'
    usable = saw_end and bool(final_ids) and respected
    answer_tokens = final_ids[:final_cap]
    answer = tokenizer.decode(answer_tokens, skip_special_tokens=True).strip() if usable else ''
    answer = re.sub(r'\n{3,}', '\n\n', answer)
    metadata = {
        'reasoning_mode': 'bounded-thinking',
        'thinking_token_cap': thinking_cap,
        'thinking_tokens_observed': thinking_tokens,
        'thinking_cap_respected': respected,
        'runtime_thinking_budget_argument': RUNTIME_THINKING_BUDGET,
        'runtime_budget_note': 'Pinned MLX-VLM criterion forces a newline after budget overflow; argument 126 bounds content plus newline at 128 tokens.',
        'thinking_terminator': 'forced' if forced_terminator else ('natural' if saw_end else 'missing'),
        'final_answer_token_cap': final_cap,
        'final_answer_tokens': min(len(final_ids), final_cap),
        'total_model_output_tokens_observed': model_tokens,
        'finish_reason': finish_reason,
        'runtime_finish_reason': runtime_finish_reason,
        'truncated_final_answer': len(final_ids) >= final_cap,
        'usable_final_answer': usable,
        'latency_s': round(time.perf_counter() - started, 3),
        'hidden_reasoning_read_or_saved': False,
    }
    if not usable:
        error = ThinkingOutputError(
            f'LFM3-VL bounded generation produced no usable final answer ({finish_reason}).')
        error.generation_metadata = metadata
        raise error
    return answer, metadata
