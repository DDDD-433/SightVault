#!/usr/bin/env python3
"""One-process, offline, real-pixel proof for the pinned d1-2B Transformers API.

This is intentionally bounded: a single 10-question classification pass and a
positive/negative query-conditioned relevance probe. It does not read dataset
labels, retrieval judgments, OCR, filenames, or EmbeddingGemma outputs.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import ipaddress
import json
import os
import platform
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = Path(__file__).resolve().parent
PIN = "10e7671d44e4b37700020b1eed3f594c5f2af0b4"
REPO = "LiquidAI/d1-2B"
CONTENT_TYPES = {
    "photo": "a real photographed scene",
    "document_page": "a text document or notes page",
    "lecture_slide": "a lecture or presentation slide",
    "illustration": "a drawn or stylized object illustration",
    "project_board": "a software project board with task cards",
    "receipt": "an itemized purchase receipt",
    "terminal_screenshot": "a terminal or command-line screenshot",
    "chart": "a plotted data chart with axes, bars, lines, or a pie plot",
    "table": "a grid of cells with aligned rows and columns",
}


def install_network_guard() -> list[dict[str, str]]:
    """Block and record DNS and non-loopback socket attempts in this process."""
    attempts: list[dict[str, str]] = []
    original_getaddrinfo = socket.getaddrinfo
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_sendto = socket.socket.sendto

    def record_and_block(kind: str, address) -> None:
        target = str(address[0] if isinstance(address, tuple) and address else address)
        attempts.append({"kind": kind, "target": target})
        raise OSError(f"Offline smoke blocked {kind} to {target}")

    def resolve(host, *args, **kwargs):
        if isinstance(host, str):
            try:
                ip = ipaddress.ip_address(host.split("%", 1)[0])
            except ValueError:
                record_and_block("dns", host)
            if not ip.is_loopback:
                record_and_block("resolve_non_loopback", host)
        elif isinstance(host, bytes):
            record_and_block("dns", host.decode("utf-8", errors="replace"))
        return original_getaddrinfo(host, *args, **kwargs)

    def is_loopback_address(address) -> bool:
        if not isinstance(address, tuple) or not address:
            return True  # Unix-domain sockets are local.
        host = address[0]
        try:
            return ipaddress.ip_address(str(host).split("%", 1)[0]).is_loopback
        except ValueError:
            return False

    def connect(sock, address):
        if not is_loopback_address(address):
            record_and_block("connect", address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if not is_loopback_address(address):
            record_and_block("connect_ex", address)
        return original_connect_ex(sock, address)

    def sendto(sock, data, *args):
        address = args[-1] if len(args) > 1 else args[0] if args else None
        if not is_loopback_address(address):
            record_and_block("sendto", address)
        return original_sendto(sock, data, *args)

    socket.getaddrinfo = resolve
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket.sendto = sendto
    return attempts


def _memory_pressure() -> str | None:
    try:
        result = subprocess.run(["memory_pressure", "-Q"], capture_output=True, text=True, timeout=8, check=False)
        return (result.stdout + result.stderr).strip() or f"exit={result.returncode}"
    except Exception as exc:
        return f"unavailable: {type(exc).__name__}: {exc}"


class RssPeak:
    def __init__(self) -> None:
        import psutil

        self.process = psutil.Process()
        self.peak = self.process.memory_info().rss
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self.stop.wait(0.1):
            try:
                self.peak = max(self.peak, self.process.memory_info().rss)
            except Exception:
                return

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        try:
            self.peak = max(self.peak, self.process.memory_info().rss)
        finally:
            self.stop.set()
            self.thread.join(timeout=2)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _mps_memory(torch) -> dict[str, int | None]:
    out: dict[str, int | None] = {"current_allocated_bytes": None, "driver_allocated_bytes": None}
    try:
        if torch.backends.mps.is_available():
            out["current_allocated_bytes"] = int(torch.mps.current_allocated_memory())
            out["driver_allocated_bytes"] = int(torch.mps.driver_allocated_memory())
    except Exception:
        pass
    return out


def _binary_questions() -> dict:
    definitions = {
        "contains_error": "an explicit visible failure, error, traceback, or out-of-memory event",
        "contains_terminal": "a terminal or command-line window as the primary content",
        "contains_code": "visible executable command/query or source code; shell commands at a prompt count",
        "contains_receipt": "a point-of-sale receipt or itemized purchase record",
        "contains_document": "a document-like page, slide, or receipt; not a terminal, board, photo, or illustration",
        "contains_chart": "a plotted data chart with axes, bars, lines, or a pie plot; diagrams do not count",
        "contains_table": "an explicit grid of cells with aligned rows and columns; receipt lists and kanban cards do not count",
        "contains_ui": "visible software/window interface chrome or a terminal window",
        "contains_person": "at least one human figure visibly present, even if small",
    }
    return {
        name: {
            "type": "noul",
            "instructions": f"Does this image visibly contain {description}? Answer yes only when supported by pixels.",
            "criteria": {
                "true": f"The pixels visibly show {description}.",
                "false": f"The pixels do not visibly show {description}.",
            },
        }
        for name, description in definitions.items()
    }


def _classify_questions() -> dict:
    questions = _binary_questions()
    questions["content_type"] = {
        "type": "choice",
        "instructions": "Which single content type best describes the primary visible content?",
        "criteria": CONTENT_TYPES,
    }
    return questions


def _relevance_question() -> dict:
    return {
        "type": "noul",
        "instructions": (
            "Does the visible image provide a direct, useful match to the retrieval request in the state? "
            "Answer yes only when the pixels directly depict the requested content or visibly contain the requested text."
        ),
        "criteria": {
            "true": "The image directly satisfies the retrieval request.",
            "false": "The image does not directly satisfy the retrieval request.",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", required=True, type=Path)
    parser.add_argument("--image", default=ROOT / "samples" / "terminal_cuda_01.png", type=Path)
    parser.add_argument("--negative-image", default=ROOT / "samples" / "document_02.png", type=Path)
    parser.add_argument("--output", default=EXPERIMENT / "runtime_smoke.json", type=Path)
    parser.add_argument("--device", choices=("auto", "mps", "cpu"), default="auto")
    parser.add_argument("--query", default="Find a terminal screenshot showing a CUDA out-of-memory failure.")
    args = parser.parse_args()
    model_path = args.model_path.expanduser().resolve()
    image = args.image.expanduser().resolve()
    negative_image = args.negative_image.expanduser().resolve()
    for path in (model_path / "config.json", model_path / "model.safetensors", image, negative_image):
        if not path.is_file():
            raise SystemExit(f"Required local file is missing: {path}")
    if not (model_path / "modeling_d1_lfm3_vl.py").is_file():
        raise SystemExit("Pinned d1 model code is missing from the local snapshot.")

    network_attempts = install_network_guard()

    before_pressure = _memory_pressure()
    rss_sampler = RssPeak()
    with rss_sampler:
        import torch
        import transformers
        import psutil
        from transformers import AutoModel

        mps_available = bool(torch.backends.mps.is_built() and torch.backends.mps.is_available())
        device = ("mps" if mps_available else "cpu") if args.device == "auto" else args.device
        if device == "mps" and not mps_available:
            raise SystemExit("MPS was requested but this PyTorch build/device does not report MPS available.")
        dtype = torch.bfloat16 if device == "mps" else torch.float32
        start = time.perf_counter()
        model = AutoModel.from_pretrained(
            str(model_path),
            trust_remote_code=True,
            local_files_only=True,
            dtype=dtype,
        ).to(device).eval()
        model_load_s = time.perf_counter() - start

        start = time.perf_counter()
        engine = model.engine
        engine.token_budget = 12000
        _ = engine.media
        processor_load_s = time.perf_counter() - start

        forward_calls = 0
        original_slot_logits = engine._slot_logits

        def counted_slot_logits(seqs, batch, slots, pointer):
            nonlocal forward_calls
            forward_calls += 1
            return original_slot_logits(seqs, batch, slots, pointer)

        engine._slot_logits = counted_slot_logits

        def ask(state, questions, path):
            started = time.perf_counter()
            before = forward_calls
            with torch.inference_mode():
                answer = model.system_one(state, questions, media=[{"type": "image", "path": str(path)}])
            elapsed = time.perf_counter() - started
            return {
                "elapsed_s": elapsed,
                "forward_passes": forward_calls - before,
                "usage": answer.get("usage"),
                "answers": answer.get("answers"),
                "mps_memory_after": _mps_memory(torch),
            }

        start = time.perf_counter()
        class_result = ask(None, _classify_questions(), image)
        first_request_s = time.perf_counter() - start
        if class_result["forward_passes"] != 1:
            raise RuntimeError(f"Expected the 10 named questions to share one forward pass; saw {class_result['forward_passes']}.")

        positive = ask(f"Retrieval request: {args.query}", {"relevance": _relevance_question()}, image)
        negative = ask(f"Retrieval request: {args.query}", {"relevance": _relevance_question()}, negative_image)
        positive_probability = positive["answers"]["relevance"]["noul"]
        negative_probability = negative["answers"]["relevance"]["noul"]

        peak_rss = int(rss_sampler.peak)
        mps_samples = [class_result["mps_memory_after"], positive["mps_memory_after"], negative["mps_memory_after"]]
        accelerator_memory = {
            name: max((sample.get(name) or 0 for sample in mps_samples), default=0) or None
            for name in ("current_allocated_bytes", "driver_allocated_bytes")
        }
        active_device = str(next(model.parameters()).device)
        # `counted_slot_logits` closes over a bound method which retains engine
        # and model. Restore the class method and drop that closure before
        # measuring the unloaded process state.
        del engine._slot_logits
        del counted_slot_logits, original_slot_logits, ask
        del engine, model
        gc.collect()
        if device == "mps":
            torch.mps.synchronize()
            torch.mps.empty_cache()
        post_model_unload_mps = _mps_memory(torch)
        post_model_unload_rss = int(psutil.Process().memory_info().rss)

    after_pressure = _memory_pressure()
    model_size = (model_path / "model.safetensors").stat().st_size
    raw = {
        "experiment": "d1-v2-reference-runtime-smoke",
        "baseline_commit": "766027c79d4c6f17ba7b88b1df3bc079dfbbd141",
        "model": {
            "repo": REPO,
            "revision": PIN,
            "format": "BF16 safetensors reference checkpoint",
            "model_file_bytes": model_size,
            "model_file_sha256": _sha(model_path / "model.safetensors"),
            "image": str(image.relative_to(ROOT)) if image.is_relative_to(ROOT) else image.name,
            "image_sha256": _sha(image),
            "negative_image": str(negative_image.relative_to(ROOT)) if negative_image.is_relative_to(ROOT) else negative_image.name,
            "negative_image_sha256": _sha(negative_image),
        },
        "platform": {
            "os": platform.platform(),
            "arch": platform.machine(),
            "hardware": platform.processor(),
            "ram_bytes": psutil.virtual_memory().total,
            "python": platform.python_version(),
        },
        "runtime": {
            "name": "Transformers + PyTorch reference SystemOne API",
            "transformers_version": transformers.__version__,
            "torch_version": torch.__version__,
            "device": active_device,
            "dtype": str(dtype).removeprefix("torch."),
            "offline_flags": {
                "HF_HUB_OFFLINE": os.environ["HF_HUB_OFFLINE"],
                "TRANSFORMERS_OFFLINE": os.environ["TRANSFORMERS_OFFLINE"],
                "HF_HUB_DISABLE_TELEMETRY": os.environ["HF_HUB_DISABLE_TELEMETRY"],
            },
            "mps_built": bool(torch.backends.mps.is_built()),
            "mps_available": mps_available,
            "network_guard": "DNS and non-loopback sockets blocked and recorded; loopback allowed",
            "blocked_network_attempts": network_attempts,
        },
        "performance": {
            "cold_model_load_s": model_load_s,
            "processor_and_tokenizer_init_s": processor_load_s,
            "first_request_s": first_request_s,
            "peak_process_rss_bytes": peak_rss,
            "max_mps_memory_sample_after_request_bytes": accelerator_memory,
            "memory_pressure_before": before_pressure,
            "memory_pressure_after_model_unload_in_process": after_pressure,
            "mps_memory_after_model_unload_in_process_bytes": post_model_unload_mps,
            "process_rss_after_model_unload_in_process_bytes": post_model_unload_rss,
        },
        "classification_probe": {
            "image_id": "I053",
            "question_count": 10,
            "forward_passes": class_result["forward_passes"],
            "elapsed_s": class_result["elapsed_s"],
            "raw_output": class_result["answers"],
            "usage": class_result["usage"],
        },
        "query_conditioned_relevance_probe": {
            "method": "one typed noul P(yes) for query plus one candidate image; no filenames, OCR, labels, or lexical score",
            "query": args.query,
            "positive": {
                "image_id": "I053",
                "elapsed_s": positive["elapsed_s"],
                "forward_passes": positive["forward_passes"],
                "p_yes": positive_probability,
                "raw_output": positive["answers"],
            },
            "negative": {
                "image_id": "I003",
                "elapsed_s": negative["elapsed_s"],
                "forward_passes": negative["forward_passes"],
                "p_yes": negative_probability,
                "raw_output": negative["answers"],
            },
            "positive_minus_negative_p_yes": positive_probability - negative_probability,
        },
        "stability": {"requests": 3, "failures": 0, "ooms": 0, "notes": "Three bounded real-image requests; no concurrency."},
    }
    out = args.output.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        display_output = str(out.relative_to(ROOT))
    except ValueError:
        display_output = out.name
    print(json.dumps({
        "output": display_output,
        "device": active_device,
        "dtype": str(dtype).removeprefix("torch."),
        "model_load_s": round(model_load_s, 2),
        "first_request_s": round(first_request_s, 2),
        "classification_forward_passes": class_result["forward_passes"],
        "positive_p_yes": round(positive_probability, 4),
        "negative_p_yes": round(negative_probability, 4),
        "peak_rss_gib": round(peak_rss / (1024**3), 2),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
