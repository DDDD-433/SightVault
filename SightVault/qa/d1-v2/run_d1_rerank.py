#!/usr/bin/env python3
"""Score each frozen query/candidate pair with one query-conditioned d1 noul question.

The frozen top-20 candidate output and label-free image manifest are the only
experiment inputs. Relevance judgments and classification labels are withheld.
Each of the 16 queries makes 20 separate one-question/one-image calls so the
measured latency corresponds to the interactive top-20 reranking path.
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
from datetime import datetime, timezone
from pathlib import Path
import weakref

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PIN = "10e7671d44e4b37700020b1eed3f594c5f2af0b4"
REPO = "LiquidAI/d1-2B"


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


def relevance_question(query: str) -> dict:
    return {
        "type": "noul",
        "instructions": (
            f"Does the visible image directly and usefully satisfy this retrieval request: {query!r}? "
            "Answer yes only when the pixels depict the requested content or visibly contain the requested text."
        ),
        "criteria": {
            "true": "The pixels directly satisfy the retrieval request.",
            "false": "The pixels do not directly satisfy the retrieval request.",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", required=True, type=Path)
    parser.add_argument("--embedding-results", default=HERE / "embedding_top20.json", type=Path)
    parser.add_argument("--image-manifest", default=HERE / "image_manifest.json", type=Path)
    parser.add_argument("--output", default=HERE / "d1_rerank_scores.json", type=Path)
    parser.add_argument("--device", choices=("mps", "cpu"), default="mps")
    parser.add_argument("--empty-cache-every", type=int, default=0, help="collect Python garbage after each pair and release unused MPS cache after every N successful pairs; 0 disables cache release")
    parser.add_argument("--preflight-only", action="store_true", help="validate all 320 query/candidate pairs and image hashes without importing/loading a model")
    args = parser.parse_args()
    output_path = args.output.expanduser().resolve()
    partial_path = output_path.with_name(output_path.stem + ".partial.json")
    model_path = args.model_path.expanduser().resolve()
    if not (model_path / "config.json").is_file() or not (model_path / "model.safetensors").is_file():
        raise SystemExit(f"Pinned local d1 snapshot is incomplete: {model_path}")

    embedding_results = json.loads(args.embedding_results.read_text(encoding="utf-8"))
    query_rows = embedding_results["queries"]
    candidate_rows = {}
    for query in query_rows:
        if len(query["top20"]) != 20:
            raise SystemExit(f"Expected exactly 20 frozen candidates for {query['query_id']}")
        for candidate in query["top20"]:
            key = candidate["image_id"]
            previous = candidate_rows.setdefault(key, {"image_id": key, "filename": candidate["filename"], "sha256": None})
            if previous["filename"] != candidate["filename"]:
                raise SystemExit(f"Conflicting candidate file mapping for {key}")
    image_payload = json.loads(args.image_manifest.read_text(encoding="utf-8"))
    image_by_id = {record["id"]: record for record in image_payload["images"]}
    for image_id, candidate in candidate_rows.items():
        record = image_by_id.get(image_id)
        if not record or record["file"] != candidate["filename"]:
            raise SystemExit(f"Candidate does not match the frozen image manifest: {image_id}")
        image_path = (ROOT / "samples" / record["file"]).resolve()
        if not image_path.is_file() or sha256(image_path) != record["sha256"]:
            raise SystemExit(f"Candidate image SHA-256 mismatch: {image_path}")
        candidate.update({"sha256": record["sha256"], "path": image_path})
    if len(query_rows) != 16:
        raise SystemExit("Expected the frozen 16-query, 20-candidate-per-query baseline")
    ordered_pairs = []
    for query in query_rows:
        seen_for_query = set()
        for rank, candidate in enumerate(query["top20"], start=1):
            image_id = candidate["image_id"]
            if image_id in seen_for_query:
                raise SystemExit(f"Duplicate candidate in {query['query_id']} top-20: {image_id}")
            seen_for_query.add(image_id)
            image = candidate_rows[image_id]
            ordered_pairs.append({
                "query_id": query["query_id"],
                "query_text": query["query_text"],
                "candidate_rank": rank,
                "image_id": image_id,
                "filename": image["filename"],
                "sha256": image["sha256"],
                "path": image["path"],
            })
    if len(ordered_pairs) != 16 * 20:
        raise SystemExit(f"Expected exactly 320 unique query/candidate pairs, found {len(ordered_pairs)}")
    preflight = {
        "input_pairs": len(ordered_pairs),
        "queries": len(query_rows),
        "candidates_per_query": 20,
        "unique_candidate_images": len(candidate_rows),
        "labels_read": False,
        "relevance_judgments_read": False,
        "all_candidate_image_hashes_verified": True,
        "scoring_plan": "320 independent one-question/one-image calls; no cross-query batching",
        "candidate_input_order": "frozen EmbeddingGemma query order, then baseline rank 1..20",
    }
    if args.preflight_only:
        print(json.dumps({"preflight_passed": True, **preflight}, sort_keys=True))
        return 0

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
    dtype = torch.bfloat16 if args.device == "mps" else torch.float32

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
        candidate_results = []
        failures = []
        mps_samples = []
        mps_after_answer_release_samples = []
        mps_post_cache_samples = []
        pressure_checkpoints = []
        eval_started = time.perf_counter()
        query_timings = []

        def save_partial(pair_count: int) -> None:
            partial = {
                "experiment": "d1-v2-query-conditioned-rerank-partial",
                "status": "in_progress",
                "attempted_pair_count": pair_count,
                "successful_pair_count": len(candidate_results),
                "expected_pair_count": len(ordered_pairs),
                "unique_candidate_images": len(candidate_rows),
                "forward_passes": forward_calls,
                "failures": failures,
                "candidate_results": candidate_results,
                "query_timings": query_timings,
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

        pair_count = 0
        successful_pair_count = 0
        stop_after_query = False
        for query in query_rows:
            query_started = time.perf_counter()
            query_call_seconds = 0.0
            query_completed = 0
            query_pairs = [pair for pair in ordered_pairs if pair["query_id"] == query["query_id"]]
            for pair in query_pairs:
                pair_count += 1
                questions = {pair["query_id"]: relevance_question(pair["query_text"])}
                started = time.perf_counter()
                previous_calls = forward_calls
                inference_completed = False
                failed_this_pair = False
                try:
                    with torch.inference_mode():
                        answer = model.system_one(None, questions, media=[{"type": "image", "path": str(pair["path"])}])
                    inference_completed = True
                    inference_elapsed = time.perf_counter() - started
                    passes = forward_calls - previous_calls
                    raw_answers = answer.get("answers", {})
                    if set(raw_answers) != {pair["query_id"]}:
                        raise RuntimeError(f"Returned relevance names differ for {pair['query_id']}/{pair['image_id']}")
                    raw = raw_answers[pair["query_id"]]
                    if raw.get("type") != "noul" or not isinstance(raw.get("noul"), (int, float)):
                        raise RuntimeError(f"Unexpected typed relevance output for {pair['query_id']}/{pair['image_id']}: {raw}")
                    after_request_memory = mps_memory(torch)
                    row = {
                        "query_id": pair["query_id"],
                        "image_id": pair["image_id"],
                        "filename": pair["filename"],
                        "sha256": pair["sha256"],
                        "baseline_rank": pair["candidate_rank"],
                        "forward_passes": passes,
                        "question_count": 1,
                        "elapsed_s": inference_elapsed,
                        "usage": answer.get("usage"),
                        "p_yes": float(raw["noul"]),
                        "raw_output": raw,
                        "mps_memory_after_request": after_request_memory,
                    }
                    candidate_results.append(row)
                    successful_pair_count += 1
                    mps_samples.append(after_request_memory)
                    cleanup_started = time.perf_counter()
                    answer = None
                    raw_answers = None
                    raw = None
                    gc.collect()
                    after_answer_release = mps_memory(torch)
                    mps_after_answer_release_samples.append(after_answer_release)
                    row["mps_memory_after_answer_release"] = after_answer_release
                    row["process_rss_after_answer_release_bytes"] = int(rss_peak.process.memory_info().rss)
                    if args.empty_cache_every and successful_pair_count % args.empty_cache_every == 0:
                        cache_clear_started = time.perf_counter()
                        if args.device == "mps":
                            torch.mps.empty_cache()
                        cache_clear_elapsed = time.perf_counter() - cache_clear_started
                        post_cache_memory = mps_memory(torch)
                        row["mps_memory_after_cache_release"] = post_cache_memory
                        row["mps_cache_clear_elapsed_s"] = cache_clear_elapsed
                        row["process_rss_after_cache_release_bytes"] = int(rss_peak.process.memory_info().rss)
                        mps_post_cache_samples.append(post_cache_memory)
                    cleanup_elapsed = time.perf_counter() - cleanup_started
                    if args.empty_cache_every and successful_pair_count % args.empty_cache_every == 0:
                        row["mps_cache_clear_performed"] = args.device == "mps"
                    row["answer_release_and_cache_policy_s"] = cleanup_elapsed
                    row["elapsed_with_memory_policy_s"] = inference_elapsed + cleanup_elapsed
                    query_call_seconds += row["elapsed_with_memory_policy_s"]
                    query_completed += 1
                    if passes != 1:
                        failures.append({"query_id": pair["query_id"], "image_id": pair["image_id"], "phase": "forward_count", "error": f"expected one forward, got {passes}"})
                        failed_this_pair = True
                except Exception as exc:
                    elapsed = time.perf_counter() - started
                    failure_phase = "post_inference_output_or_memory_policy" if inference_completed else "model_inference"
                    failures.append({"query_id": pair["query_id"], "image_id": pair["image_id"], "phase": failure_phase, "elapsed_s": elapsed, "error": f"{type(exc).__name__}: {exc}"})
                    failed_this_pair = True
                    answer = None
                    raw_answers = None
                    raw = None
                    gc.collect()
                    after_request_memory = mps_memory(torch)
                    mps_samples.append(after_request_memory)
                    if candidate_results and candidate_results[-1].get("query_id") == pair["query_id"] and candidate_results[-1].get("image_id") == pair["image_id"]:
                        candidate_results[-1].update({"error": f"{type(exc).__name__}: {exc}", "failure_phase": failure_phase})
                    if any(term in str(exc).lower() for term in ("out of memory", "mps backend out of memory", "metal command buffer")):
                        stop_after_query = True
                if pair_count % 5 == 0 or pair_count == len(ordered_pairs) or failed_this_pair:
                    pressure_checkpoints.append({
                        "completed_pair_count": pair_count,
                        "memory_pressure": memory_pressure(),
                        "system_memory": system_memory_snapshot(),
                    })
                    save_partial(pair_count)
                if stop_after_query:
                    break
            query_timings.append({
                "query_id": query["query_id"],
                "candidate_count": len(query_pairs),
                "successful_calls": query_completed,
                "serial_candidate_call_seconds_including_memory_policy": query_call_seconds,
                "query_loop_wall_seconds_including_checkpoints": time.perf_counter() - query_started,
            })
            if stop_after_query:
                break
        eval_wall_s = time.perf_counter() - eval_started
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
    completed = len(candidate_results) == len(ordered_pairs) and not failures
    source = json.loads((model_path / "source.json").read_text(encoding="utf-8")) if (model_path / "source.json").is_file() else {}
    candidate_union_size = len(candidate_rows)
    output = {
        "experiment": "d1-v2-query-conditioned-rerank-pair-scores",
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
            "snapshot_file_count": source.get("snapshot_file_count"),
        },
        "input_isolation": {
            "embedding_top20_file": str(args.embedding_results),
            "image_manifest": str(args.image_manifest),
            "classification_labels_read": False,
            "retrieval_relevance_read": False,
            "ocr_or_filename_text_sent_to_d1": False,
            "d1_input": "Raw candidate pixels plus the one query-conditioned noul question for this query/candidate pair; each of the 320 pairs is an independent one-question/one-image call.",
            "candidate_selection": "For each frozen query, score exactly its original EmbeddingGemma semantic top-20 list; no d1 score is used to choose candidates.",
            "per_query_candidate_count": 20,
            "query_candidate_pair_count": len(ordered_pairs),
            "unique_candidate_images_scored": candidate_union_size,
            "cross_query_batching": False,
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
            "query_candidate_pair_evaluation_wall_s": eval_wall_s,
            "query_timings": query_timings,
            "peak_process_rss_bytes": peak_rss,
            "max_mps_memory_sample_after_request_bytes": max_mps,
            "max_mps_memory_sample_after_answer_release_bytes": max_mps_after_answer_release,
            "max_mps_memory_sample_after_cache_release_bytes": max_mps_after_cache,
            "python_gc_after_each_successful_pair": True,
            "empty_cache_every_successful_pairs": args.empty_cache_every,
            "memory_pressure_checkpoints": pressure_checkpoints,
            "memory_pressure_before": before_pressure,
            "system_memory_before": before_system_memory,
            "memory_pressure_after_model_unload_in_process": after_pressure,
            "system_memory_after_model_unload_in_process": after_system_memory,
            "mps_memory_after_model_unload_in_process_bytes": post_model_unload_mps,
            "process_rss_after_model_unload_in_process_bytes": post_model_unload_rss,
            "python_object_refs_after_model_unload": post_model_unload_refs,
            "forward_passes": forward_calls,
            "serial_one_query_candidate_pair_per_call": True,
            "model_loaded_once": True,
            "concurrency": 1,
        },
        "completed": completed,
        "failures": failures,
        "candidate_union_size": candidate_union_size,
        "query_candidate_pair_count": len(ordered_pairs),
        "query_timings": query_timings,
        "candidate_results": candidate_results,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    partial_path.unlink(missing_ok=True)
    print(json.dumps({
        "output": str(output_path),
        "completed": completed,
        "queries": len(query_rows),
        "query_candidate_pairs": len(ordered_pairs),
        "unique_candidate_images": candidate_union_size,
        "forward_passes": forward_calls,
        "failures": len(failures),
        "model_load_s": round(model_load_s, 2),
        "evaluation_wall_s": round(eval_wall_s, 2),
        "peak_rss_gib": round(peak_rss / (1024**3), 2),
    }))
    return 0 if completed else 2


if __name__ == "__main__":
    raise SystemExit(main())
