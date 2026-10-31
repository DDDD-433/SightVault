#!/usr/bin/env python3
"""Freeze real semantic top-20 candidate sets with SightVault's existing query path.

This stage reads the copied SQLite snapshot and query text only. It never reads
classification labels or relevance judgments and never writes the database.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sqlite3
import sys
import time
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EMBEDDING_ID = "google/embeddinggemma-2:768:text-image"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--image-manifest", default=HERE / "image_manifest.json", type=Path)
    parser.add_argument("--queries", default=HERE / "retrieval_queries.json", type=Path)
    parser.add_argument("--output", default=HERE / "embedding_top20.json", type=Path)
    parser.add_argument("--device", choices=("mps", "cpu"), default="mps")
    args = parser.parse_args()
    model_path = args.model_path.expanduser().resolve()
    database = args.database.expanduser().resolve()
    if not (model_path / "model.safetensors").is_file():
        raise SystemExit(f"EmbeddingGemma model files are missing: {model_path}")
    if not database.is_file():
        raise SystemExit(f"Copied SQLite snapshot is missing: {database}")

    from smoke_d1 import RssPeak, _memory_pressure, _mps_memory, install_network_guard

    network_attempts = install_network_guard()
    import numpy as np
    import torch
    import sentence_transformers
    from sentence_transformers import SentenceTransformer

    before_pressure = _memory_pressure()
    if args.device == "mps" and not (torch.backends.mps.is_built() and torch.backends.mps.is_available()):
        raise SystemExit("MPS was requested but this PyTorch build/device does not report MPS available")
    torch.set_num_threads(min(6, os.cpu_count() or 4))
    dtype = torch.float32  # Matches SightVault's existing Models._dtype() on MPS and CPU.

    manifest = json.loads(args.image_manifest.read_text(encoding="utf-8"))
    id_by_filename = {record["file"]: record["id"] for record in manifest["images"]}
    query_payload = json.loads(args.queries.read_text(encoding="utf-8"))
    queries = query_payload["queries"]
    top_k = int(query_payload["candidate_count"])

    db = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT id, filename, timestamp, embedding, embedding_model FROM assets ORDER BY id"
    ).fetchall()
    db.close()
    if len(rows) != len(id_by_filename):
        raise SystemExit(f"Expected {len(id_by_filename)} indexed rows, found {len(rows)}")
    candidates = []
    for row in rows:
        if row["filename"] not in id_by_filename:
            raise SystemExit(f"Indexed file is not in the frozen image manifest: {row['filename']}")
        if row["embedding_model"] != EMBEDDING_ID:
            raise SystemExit(f"Unexpected image embedding model: {row['embedding_model']}")
        vector = np.frombuffer(row["embedding"], dtype="<f4")
        if vector.shape != (768,) or not np.isfinite(vector).all() or abs(float(np.linalg.norm(vector)) - 1.0) > 0.002:
            raise SystemExit(f"Invalid normalized 768d embedding for {row['filename']}")
        candidates.append(
            {
                "id": id_by_filename[row["filename"]],
                "asset_id": int(row["id"]),
                "filename": row["filename"],
                "timestamp": row["timestamp"],
                "vector": vector.copy(),
            }
        )

    started = time.perf_counter()
    rss_peak = RssPeak()
    with rss_peak:
        model = SentenceTransformer(
            str(model_path),
            device=args.device,
            local_files_only=True,
            config_kwargs={"audio_config": None},
            model_kwargs={"dtype": dtype, "attn_implementation": "sdpa"},
        )
        active_device = str(next(model.parameters()).device)
        model_load_s = time.perf_counter() - started
        per_query = []
        started_queries = time.perf_counter()
        for query in queries:
            query_start = time.perf_counter()
            with torch.inference_mode():
                qvec = model.encode(
                    [query["text"]],
                    prompt_name="SearchQuery",
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                    batch_size=1,
                )[0]
            qvec = np.asarray(qvec, dtype=np.float32)
            if qvec.shape != (768,) or not np.isfinite(qvec).all():
                raise SystemExit(f"EmbeddingGemma returned an invalid query vector for {query['id']}")
            norm = float(np.linalg.norm(qvec))
            if abs(norm - 1.0) > 0.002:
                raise SystemExit(f"EmbeddingGemma query vector is not normalized for {query['id']}: norm={norm}")
            scored = []
            for candidate in candidates:
                score = float(np.clip(np.dot(qvec, candidate["vector"]), -1.0, 1.0))
                scored.append(
                    {
                        "image_id": candidate["id"],
                        "asset_id": candidate["asset_id"],
                        "filename": candidate["filename"],
                        "timestamp": candidate["timestamp"],
                        "semantic_score": score,
                    }
                )
            scored.sort(key=lambda record: (-record["semantic_score"], record["timestamp"], record["asset_id"]))
            per_query.append(
                {
                    "query_id": query["id"],
                    "query_text": query["text"],
                    "query_embedding_norm": norm,
                    "query_embedding_sha256": hashlib.sha256(qvec.astype("<f4").tobytes()).hexdigest(),
                    "embedding_latency_s": time.perf_counter() - query_start,
                    "corpus_count": len(scored),
                    "top20": scored[:top_k],
                }
            )
        total_query_s = time.perf_counter() - started_queries
        mps_memory = _mps_memory(torch)
        peak_rss = int(rss_peak.peak)
        del model
        import gc

        gc.collect()
        if args.device == "mps":
            torch.mps.empty_cache()

    after_pressure = _memory_pressure()
    provenance_file = model_path / "source.json"
    model_provenance = json.loads(provenance_file.read_text(encoding="utf-8")) if provenance_file.is_file() else {}
    model_file = model_path / "model.safetensors"
    output = {
        "experiment": "d1-v2-embeddinggemma-baseline-top20",
        "baseline_commit": "766027c79d4c6f17ba7b88b1df3bc079dfbbd141",
        "input_isolation": {
            "query_source": str(args.queries),
            "image_manifest": str(args.image_manifest),
            "database_snapshot": str(database),
            "database_snapshot_sha256": json.loads((HERE / "data_snapshot_provenance.json").read_text(encoding="utf-8"))["snapshot_database_sha256"],
            "read_only_database": True,
            "labels_read": False,
            "relevance_judgments_read": False,
            "ocr_or_filenames_in_query": False,
            "retrieval_mode": "semantic only; exact app query embedding and normalized 768d cosine score",
            "sort_order": "semantic_score descending, timestamp ascending, actual SQLite assets.id ascending (matches Search.run)",
        },
        "model": {
            "repo": model_provenance.get("repo", "google/embeddinggemma-2"),
            "revision": model_provenance.get("revision"),
            "model_file_bytes": model_file.stat().st_size,
            "model_file_sha256": sha256(model_file),
            "embedding_id": EMBEDDING_ID,
            "prompt_name": "SearchQuery",
            "pooling_and_normalization": "SentenceTransformer model pipeline; normalize_embeddings=True",
        },
        "runtime": {
            "name": "SentenceTransformers / PyTorch existing app query path",
            "sentence_transformers_version": sentence_transformers.__version__,
            "transformers_version": __import__("transformers").__version__,
            "torch_version": torch.__version__,
            "numpy_version": np.__version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "device": active_device,
            "dtype": str(dtype).removeprefix("torch."),
            "offline_flags": {
                "HF_HUB_OFFLINE": os.environ["HF_HUB_OFFLINE"],
                "TRANSFORMERS_OFFLINE": os.environ["TRANSFORMERS_OFFLINE"],
                "HF_HUB_DISABLE_TELEMETRY": os.environ["HF_HUB_DISABLE_TELEMETRY"],
            },
            "network_guard": "DNS and non-loopback sockets blocked and recorded; loopback allowed",
            "blocked_network_attempts": network_attempts,
        },
        "performance": {
            "model_load_s": model_load_s,
            "total_query_embedding_and_ranking_s": total_query_s,
            "mean_query_embedding_s": sum(row["embedding_latency_s"] for row in per_query) / len(per_query),
            "peak_process_rss_bytes": peak_rss,
            "mps_memory_after_run_bytes": mps_memory,
            "memory_pressure_before": before_pressure,
            "memory_pressure_after_unload": after_pressure,
            "query_count": len(per_query),
            "unique_images": len(candidates),
        },
        "queries": per_query,
    }
    out = args.output.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(out),
        "query_count": len(per_query),
        "corpus_count": len(candidates),
        "top_k": top_k,
        "model_load_s": round(model_load_s, 2),
        "total_query_s": round(total_query_s, 2),
        "peak_rss_gib": round(peak_rss / (1024**3), 2),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
