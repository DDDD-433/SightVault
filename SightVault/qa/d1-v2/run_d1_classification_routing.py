#!/usr/bin/env python3
"""Run frozen d1 label and routing prompts on baseline and diagnostic images.

This inference stage reads only label-free image manifests and routing prompts.
Gold classification/routing labels stay in separate files for later analysis.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import platform
import subprocess
import sys
import threading
import time
import weakref
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PIN = "10e7671d44e4b37700020b1eed3f594c5f2af0b4"
REPO = "LiquidAI/d1-2B"
ROUTE_POLICY = {
    "fast_ocr": "A local OCR path for literal transcription or lookup of ordinary screenshot or page text.",
    "document_specialist": "A local document and layout path for structured fields in an itemized receipt.",
    "vision_language_model": "A local VLM path for scene, object, color, or appearance questions that require visual grounding.",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def memory_pressure() -> str | None:
    try:
        result = subprocess.run(["memory_pressure", "-Q"], capture_output=True, text=True, timeout=8, check=False)
        return (result.stdout + result.stderr).strip() or f"exit={result.returncode}"
    except Exception as exc:
        return f"unavailable: {type(exc).__name__}: {exc}"


def system_memory_snapshot() -> dict[str, str | None]:
    snapshot: dict[str, str | None] = {"vm_stat": None, "swap_usage": None}
    commands = {"vm_stat": ["vm_stat"], "swap_usage": ["sysctl", "vm.swapusage"]}
    for key, command in commands.items():
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=8, check=False)
            snapshot[key] = (result.stdout + result.stderr).strip() or f"exit={result.returncode}"
        except Exception as exc:
            snapshot[key] = f"unavailable: {type(exc).__name__}: {exc}"
    return snapshot


def git_head() -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True, timeout=8, check=True)
        return result.stdout.strip()
    except Exception:
        return None


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


def mps_memory(torch) -> dict[str, int | None]:
    result: dict[str, int | None] = {"current_allocated_bytes": None, "driver_allocated_bytes": None}
    try:
        if torch.backends.mps.is_available():
            result["current_allocated_bytes"] = int(torch.mps.current_allocated_memory())
            result["driver_allocated_bytes"] = int(torch.mps.driver_allocated_memory())
    except Exception:
        pass
    return result


def classification_questions() -> dict:
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
    questions = {
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
    questions["content_type"] = {
        "type": "choice",
        "instructions": "Which single content type best describes the primary visible content?",
        "criteria": {
            "photo": "a real photographed scene",
            "document_page": "a text document or notes page",
            "lecture_slide": "a lecture or presentation slide",
            "illustration": "a drawn or stylized object illustration",
            "project_board": "a software project board with task cards",
            "receipt": "an itemized purchase receipt",
            "terminal_screenshot": "a terminal or command-line screenshot",
            "chart": "a plotted data chart with axes, bars, lines, or a pie plot",
            "table": "a grid of cells with aligned rows and columns",
        },
    }
    return questions


def route_question(task: str) -> dict:
    return {
        "type": "choice",
        "instructions": (
            f"For the user's request {task!r}, which local processing path should handle it? "
            "Select the path suited to the requested operation using the visible pixels."
        ),
        "criteria": ROUTE_POLICY,
    }


def read_manifest(path: Path, dataset: str) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    records = payload["images"]
    normalized = []
    for record in records:
        image_path = (ROOT / ("samples" if dataset == "baseline" else "qa/d1-v2/diagnostic_supplement") / record["file"]).resolve()
        if not image_path.is_file():
            raise SystemExit(f"Image file is missing: {image_path}")
        if sha256(image_path) != record["sha256"]:
            raise SystemExit(f"Image SHA-256 mismatch: {image_path}")
        normalized.append({**record, "dataset": dataset, "path": image_path})
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", required=True, type=Path)
    parser.add_argument("--baseline-manifest", default=HERE / "image_manifest.json", type=Path)
    parser.add_argument("--supplement-manifest", default=HERE / "diagnostic_supplement_image_manifest.json", type=Path)
    parser.add_argument("--routing-prompts", default=HERE / "routing_prompts.json", type=Path)
    parser.add_argument("--output", default=HERE / "classification_routing_results.json", type=Path)
    parser.add_argument("--device", choices=("mps", "cpu"), default="mps")
    parser.add_argument("--empty-cache-every", type=int, default=0, help="collect Python garbage after each request and release unused MPS cache after every N successful requests; 0 disables cache release")
    parser.add_argument("--max-requests", type=int, default=None, help="run a bounded prefix of the label-free manifest for memory-policy probing; output will not cover the full fixture")
    parser.add_argument("--repeat-to-100", action="store_true", help="append 32 fixed baseline replays after the 68 label-free image IDs")
    args = parser.parse_args()
    output_path = args.output.expanduser().resolve()
    partial_path = output_path.with_name(output_path.stem + ".partial.json")
    model_path = args.model_path.expanduser().resolve()
    if not (model_path / "config.json").is_file() or not (model_path / "model.safetensors").is_file():
        raise SystemExit(f"Pinned local d1 snapshot is incomplete: {model_path}")

    from smoke_d1 import install_network_guard

    network_attempts = install_network_guard()
    import torch
    import transformers
    import psutil
    from transformers import AutoModel

    mps_available = bool(torch.backends.mps.is_built() and torch.backends.mps.is_available())
    if args.device == "mps" and not mps_available:
        raise SystemExit("MPS was requested but this PyTorch build/device does not report MPS available")
    if args.empty_cache_every < 0:
        raise SystemExit("--empty-cache-every must be zero or a positive integer")
    if args.max_requests is not None and args.max_requests < 1:
        raise SystemExit("--max-requests must be positive")
    dtype = torch.bfloat16 if args.device == "mps" else torch.float32
    baseline = read_manifest(args.baseline_manifest, "baseline")
    supplement = read_manifest(args.supplement_manifest, "supplement")
    if len(baseline) != 62 or len(supplement) != 6:
        raise SystemExit(f"Unexpected fixture sizes: baseline={len(baseline)} supplement={len(supplement)}")
    routing = json.loads(args.routing_prompts.read_text(encoding="utf-8"))
    routing_by_image: dict[str, list[dict]] = defaultdict(list)
    for case in routing["cases"]:
        routing_by_image[case["image_id"]].append(case)

    before_pressure = memory_pressure()
    before_system_memory = system_memory_snapshot()
    run_started_at_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rss_peak = RssPeak()
    with rss_peak:
        model_started = time.perf_counter()
        model = AutoModel.from_pretrained(
            str(model_path), trust_remote_code=True, local_files_only=True, dtype=dtype
        ).to(args.device).eval()
        model_load_s = time.perf_counter() - model_started
        engine = model.engine
        engine.token_budget = 12000
        processor_started = time.perf_counter()
        _ = engine.media
        processor_load_s = time.perf_counter() - processor_started

        forward_calls = 0
        original_slot_logits = engine._slot_logits

        def counted_slot_logits(seqs, batch, slots, pointer):
            nonlocal forward_calls
            forward_calls += 1
            return original_slot_logits(seqs, batch, slots, pointer)

        engine._slot_logits = counted_slot_logits
        predictions: dict[str, list[dict]] = {"baseline": [], "supplement": []}
        seen_hashes: set[str] = set()
        started_eval = time.perf_counter()
        all_records = baseline + supplement
        if args.repeat_to_100:
            all_records = all_records + baseline[:32]
        if args.max_requests is not None:
            all_records = all_records[:args.max_requests]
        execution = []
        failures = []
        mps_samples = []
        mps_after_answer_release_samples = []
        mps_post_cache_samples = []
        pressure_checkpoints = []
        expected_requests = len(all_records)

        def save_partial(request_index: int) -> None:
            partial = {
                "experiment": "d1-v2-full-classification-routing-partial",
                "status": "in_progress",
                "completed_request_count": request_index,
                "expected_request_count": expected_requests,
                "forward_passes": forward_calls,
                "failures": failures,
                "execution_order": execution,
                "baseline_predictions": predictions["baseline"],
                "supplement_predictions": predictions["supplement"],
                "resource_checkpoint": {
                    "process_rss_current_bytes": rss_peak.process.memory_info().rss,
                    "process_rss_peak_bytes": int(rss_peak.peak),
                    "memory_pressure_before_model_load": before_pressure,
                    "mps_after_last_request_bytes": mps_samples[-1] if mps_samples else None,
                    "mps_after_last_answer_release_bytes": mps_after_answer_release_samples[-1] if mps_after_answer_release_samples else None,
                    "mps_after_last_cache_release_bytes": mps_post_cache_samples[-1] if mps_post_cache_samples else None,
                    "max_mps_sample_after_request_bytes": {
                        name: max((sample.get(name) or 0 for sample in mps_samples), default=0) or None
                        for name in ("current_allocated_bytes", "driver_allocated_bytes")
                    },
                    "max_mps_sample_after_answer_release_bytes": {
                        name: max((sample.get(name) or 0 for sample in mps_after_answer_release_samples), default=0) or None
                        for name in ("current_allocated_bytes", "driver_allocated_bytes")
                    },
                    "max_mps_sample_after_cache_release_bytes": {
                        name: max((sample.get(name) or 0 for sample in mps_post_cache_samples), default=0) or None
                        for name in ("current_allocated_bytes", "driver_allocated_bytes")
                    },
                    "memory_pressure": memory_pressure(),
                    "memory_pressure_checkpoints": pressure_checkpoints,
                },
            }
            temp = partial_path.with_suffix(".tmp")
            temp.write_text(json.dumps(partial, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temp, partial_path)

        for request_index, record in enumerate(all_records, start=1):
            image_id = record["id"]
            content_hash = record["sha256"]
            is_repeat = content_hash in seen_hashes
            seen_hashes.add(content_hash)
            questions = classification_questions()
            cases = routing_by_image.get(image_id, []) if record["dataset"] == "baseline" else []
            for case in cases:
                key = f"route_{case['id']}"
                questions[key] = route_question(case["task"])
            if len(questions) > 16:
                raise SystemExit(f"{image_id} has {len(questions)} questions; d1-2B set size is 16")

            started = time.perf_counter()
            before_calls = forward_calls
            inference_completed = False
            try:
                with torch.inference_mode():
                    answer = model.system_one(None, questions, media=[{"type": "image", "path": str(record["path"])}])
                inference_completed = True
                inference_elapsed = time.perf_counter() - started
                pass_count = forward_calls - before_calls
                after_request_memory = mps_memory(torch)
                prediction = {
                    "request_index": request_index,
                    "image_id": image_id,
                    "filename": record["file"],
                    "sha256": content_hash,
                    "dataset": record["dataset"],
                    "repeat_pixel_content": is_repeat,
                    "forward_passes": pass_count,
                    "question_count": len(questions),
                    "elapsed_s": inference_elapsed,
                    "usage": answer.get("usage"),
                    "raw_output": answer.get("answers"),
                }
                execution.append({
                    "request_index": request_index,
                    "image_id": image_id,
                    "sha256": content_hash,
                    "dataset": record["dataset"],
                    "repeat_pixel_content": is_repeat,
                    "elapsed_s": inference_elapsed,
                    "mps_memory_after_request": after_request_memory,
                    "process_rss_after_request_bytes": int(rss_peak.process.memory_info().rss),
                    "forward_passes": pass_count,
                })
                if request_index <= len(baseline) + len(supplement):
                    predictions[record["dataset"]].append(prediction)
                mps_samples.append(after_request_memory)
                cleanup_started = time.perf_counter()
                answer = None
                gc.collect()
                after_answer_release = mps_memory(torch)
                mps_after_answer_release_samples.append(after_answer_release)
                execution[-1]["mps_memory_after_answer_release"] = after_answer_release
                execution[-1]["process_rss_after_answer_release_bytes"] = int(rss_peak.process.memory_info().rss)
                if args.empty_cache_every and request_index % args.empty_cache_every == 0:
                    cache_clear_started = time.perf_counter()
                    if args.device == "mps":
                        torch.mps.empty_cache()
                    cache_clear_elapsed = time.perf_counter() - cache_clear_started
                    post_cache_memory = mps_memory(torch)
                    mps_post_cache_samples.append(post_cache_memory)
                    execution[-1]["mps_cache_clear_elapsed_s"] = cache_clear_elapsed
                    execution[-1]["mps_memory_after_cache_release"] = post_cache_memory
                    execution[-1]["process_rss_after_cache_release_bytes"] = int(rss_peak.process.memory_info().rss)
                cleanup_elapsed = time.perf_counter() - cleanup_started
                if args.empty_cache_every and request_index % args.empty_cache_every == 0:
                    execution[-1]["mps_cache_clear_performed"] = args.device == "mps"
                execution[-1]["answer_release_and_cache_policy_s"] = cleanup_elapsed
                execution[-1]["elapsed_with_memory_policy_s"] = inference_elapsed + cleanup_elapsed
                prediction["answer_release_and_cache_policy_s"] = cleanup_elapsed
                prediction["elapsed_with_memory_policy_s"] = inference_elapsed + cleanup_elapsed
                if pass_count != 1:
                    failures.append({"image_id": image_id, "error": f"expected one forward, got {pass_count}"})
            except Exception as exc:
                elapsed = time.perf_counter() - started
                failure_phase = "post_inference_output_or_memory_policy" if inference_completed else "model_inference"
                failures.append({"request_index": request_index, "image_id": image_id, "phase": failure_phase, "elapsed_s": elapsed, "error": f"{type(exc).__name__}: {exc}"})
                after_request_memory = mps_memory(torch)
                mps_samples.append(after_request_memory)
                if execution and execution[-1].get("request_index") == request_index:
                    execution[-1].update({"error": f"{type(exc).__name__}: {exc}", "failure_phase": failure_phase})
                else:
                    execution.append({
                        "request_index": request_index,
                        "image_id": image_id,
                        "sha256": content_hash,
                        "dataset": record["dataset"],
                        "repeat_pixel_content": is_repeat,
                        "elapsed_s": elapsed,
                        "mps_memory_after_request": after_request_memory,
                        "process_rss_after_request_bytes": int(rss_peak.process.memory_info().rss),
                        "forward_passes": forward_calls - before_calls,
                        "error": f"{type(exc).__name__}: {exc}",
                        "failure_phase": failure_phase,
                    })
                # An accelerator or memory failure should terminate this bounded run.
                message = str(exc).lower()
                if any(term in message for term in ("out of memory", "mps backend out of memory", "metal command buffer")):
                    break
            if request_index % 5 == 0 or request_index == len(all_records):
                pressure_checkpoints.append({
                    "completed_request_count": request_index,
                    "memory_pressure": memory_pressure(),
                    "system_memory": system_memory_snapshot(),
                })
                save_partial(request_index)

        eval_wall_s = time.perf_counter() - started_eval
        peak_rss = int(rss_peak.peak)
        active_device = str(next(model.parameters()).device)
        model_ref = weakref.ref(model)
        engine_ref = weakref.ref(engine)
        max_mps = {
            name: max((sample.get(name) or 0 for sample in mps_samples), default=0) or None
            for name in ("current_allocated_bytes", "driver_allocated_bytes")
        }
        max_mps_after_cache = {
            name: max((sample.get(name) or 0 for sample in mps_post_cache_samples), default=0) or None
            for name in ("current_allocated_bytes", "driver_allocated_bytes")
        }
        max_mps_after_answer_release = {
            name: max((sample.get(name) or 0 for sample in mps_after_answer_release_samples), default=0) or None
            for name in ("current_allocated_bytes", "driver_allocated_bytes")
        }
        # Remove the counting override and its captured bound method so neither
        # keeps the engine/model alive during the post-run memory sample.
        del engine._slot_logits
        del counted_slot_logits, original_slot_logits
        del engine, model
        gc.collect()
        if args.device == "mps":
            torch.mps.synchronize()
            torch.mps.empty_cache()
        post_model_unload_mps = mps_memory(torch)
        post_model_unload_rss = int(rss_peak.process.memory_info().rss)
        post_model_unload_refs = {
            "model_object_alive_after_cleanup": model_ref() is not None,
            "engine_object_alive_after_cleanup": engine_ref() is not None,
        }

    after_pressure = memory_pressure()
    after_system_memory = system_memory_snapshot()
    completed = len(execution) == expected_requests and not failures
    full_fixture_coverage = (
        len(predictions["baseline"]) == 62
        and len(predictions["supplement"]) == 6
        and (args.max_requests is None or args.max_requests >= 68)
        and completed
    )

    counts = [10, 50, 62, 68, 100]
    throughput = []
    for count in counts:
        if count > len(execution):
            continue
        selected = execution[:count]
        elapsed = sum(item.get("elapsed_with_memory_policy_s", item["elapsed_s"]) for item in selected)
        unique_ids = len({item["image_id"] for item in selected})
        unique_hashes = len({item["sha256"] for item in selected})
        throughput.append({
            "requests": count,
            "unique_image_ids": unique_ids,
            "unique_pixel_contents": unique_hashes,
            "repeated_pixel_requests": count - unique_hashes,
            "d1_question_count_per_request": "10 classification questions plus a route choice on routed cases",
            "inference_wall_seconds_sum": elapsed,
            "requests_per_second_after_load": count / elapsed if elapsed else None,
            "images_per_second_after_load": count / elapsed if elapsed else None,
            "scope": "serial one-image system_one calls; model load excluded; includes local image preprocessing, answer readout, and the recorded cache policy overhead",
        })
    source = json.loads((model_path / "source.json").read_text(encoding="utf-8")) if (model_path / "source.json").is_file() else {}
    output = {
        "experiment": "d1-v2-full-classification-routing",
        "baseline_commit": "766027c79d4c6f17ba7b88b1df3bc079dfbbd141",
        "experiment_commit": git_head(),
        "runner_script_sha256": sha256(Path(__file__).resolve()),
        "run_started_at_utc": run_started_at_utc,
        "command_arguments": sys.argv,
        "model": {
            "repo": REPO,
            "revision": PIN,
            "format": "BF16 safetensors reference checkpoint",
            "model_file_bytes": (model_path / "model.safetensors").stat().st_size,
            "model_file_sha256": sha256(model_path / "model.safetensors"),
            "source_snapshot_file_count": source.get("snapshot_file_count"),
        },
        "input_isolation": {
            "baseline_manifest": str(args.baseline_manifest),
            "supplement_manifest": str(args.supplement_manifest),
            "routing_prompts": str(args.routing_prompts),
            "classification_labels_read": False,
            "routing_targets_read": False,
            "retrieval_relevance_read": False,
            "image_order": "baseline manifest I001-I062, then separate label-free supplement D01-D06",
            "baseline_dataset_separate_from_supplement": True,
        },
        "runtime": {
            "name": "Transformers + PyTorch reference SystemOne API",
            "transformers_version": transformers.__version__,
            "torch_version": torch.__version__,
            "device": active_device,
            "dtype": str(dtype).removeprefix("torch."),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "ram_bytes": psutil.virtual_memory().total,
            "offline_flags": {
                "HF_HUB_OFFLINE": os.environ["HF_HUB_OFFLINE"],
                "TRANSFORMERS_OFFLINE": os.environ["TRANSFORMERS_OFFLINE"],
                "HF_HUB_DISABLE_TELEMETRY": os.environ["HF_HUB_DISABLE_TELEMETRY"],
            },
            "network_guard": "DNS and non-loopback sockets blocked and recorded; loopback allowed",
            "blocked_network_attempts": network_attempts,
        },
        "performance": {
            "cold_model_load_s": model_load_s,
            "processor_and_tokenizer_init_s": processor_load_s,
            "evaluation_wall_seconds": eval_wall_s,
            "peak_process_rss_bytes": peak_rss,
            "max_mps_memory_sample_after_request_bytes": max_mps,
            "max_mps_memory_sample_after_answer_release_bytes": max_mps_after_answer_release,
            "max_mps_memory_sample_after_cache_release_bytes": max_mps_after_cache,
            "python_gc_after_each_successful_request": True,
            "empty_cache_every_successful_requests": args.empty_cache_every,
            "memory_pressure_checkpoints": pressure_checkpoints,
            "memory_pressure_before": before_pressure,
            "system_memory_before": before_system_memory,
            "memory_pressure_after_model_unload_in_process": after_pressure,
            "system_memory_after_model_unload_in_process": after_system_memory,
            "mps_memory_after_model_unload_in_process_bytes": post_model_unload_mps,
            "process_rss_after_model_unload_in_process_bytes": post_model_unload_rss,
            "python_object_refs_after_model_unload": post_model_unload_refs,
            "model_loaded_once": True,
            "concurrency": 1,
            "throughput": throughput,
        },
        "fixture_counts": {
            "baseline_image_ids": len(baseline),
            "baseline_unique_pixel_contents": len({record["sha256"] for record in baseline}),
            "supplement_image_ids": len(supplement),
            "combined_image_ids": len(baseline) + len(supplement),
            "combined_unique_pixel_contents": len({record["sha256"] for record in baseline + supplement}),
            "routing_prompts": len(routing["cases"]),
        },
        "evaluation_plan": {
            "requested_image_calls": expected_requests,
            "full_68_image_fixture_coverage": full_fixture_coverage,
            "bounded_memory_probe": args.max_requests is not None,
            "max_requests_argument": args.max_requests,
            "repeat_to_100": args.repeat_to_100,
        },
        "forward_passes": forward_calls,
        "completed": completed,
        "failures": failures,
        "execution_order": execution,
        "baseline_predictions": predictions["baseline"],
        "supplement_predictions": predictions["supplement"],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    partial_path.unlink(missing_ok=True)
    print(json.dumps({
        "output": str(output_path),
        "completed": completed,
        "baseline_predictions": len(predictions["baseline"]),
        "supplement_predictions": len(predictions["supplement"]),
        "forward_passes": forward_calls,
        "failures": len(failures),
        "model_load_s": round(model_load_s, 2),
        "evaluation_wall_s": round(eval_wall_s, 2),
        "peak_rss_gib": round(peak_rss / (1024**3), 2),
    }))
    return 0 if completed else 2


if __name__ == "__main__":
    raise SystemExit(main())
