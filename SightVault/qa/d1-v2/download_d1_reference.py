#!/usr/bin/env python3
"""Download and hash only the pinned official d1-2B reference snapshot.

This preparation command does not import PyTorch, construct a model, or run
inference. It uses an isolated caller-supplied cache and model directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


REPO = "LiquidAI/d1-2B"
REVISION = "10e7671d44e4b37700020b1eed3f594c5f2af0b4"
MODEL_SHA256 = "b5e573687315e82ea80f4f7dd8d76b5cec3b6ee97e3509fd0493cfebb203719f"
MODEL_BYTES = 3836417920


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--cache-dir", required=True, type=Path)
    args = parser.parse_args()
    model_dir = args.model_dir.expanduser().resolve()
    cache_dir = args.cache_dir.expanduser().resolve()
    model_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(cache_dir)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)

    from huggingface_hub import snapshot_download

    started = time.perf_counter()
    snapshot = Path(
        snapshot_download(
            repo_id=REPO,
            revision=REVISION,
            local_dir=str(model_dir),
            cache_dir=str(cache_dir),
            token=False,
        )
    )
    elapsed = time.perf_counter() - started
    weight_path = snapshot / "model.safetensors"
    if not weight_path.is_file():
        raise SystemExit("Pinned reference snapshot is missing model.safetensors")
    if weight_path.stat().st_size != MODEL_BYTES:
        raise SystemExit(f"Unexpected model.safetensors size: {weight_path.stat().st_size}")
    actual_hash = sha256(weight_path)
    if actual_hash != MODEL_SHA256:
        raise SystemExit(f"model.safetensors SHA-256 mismatch: {actual_hash}")
    for required in ("modeling_d1_lfm3_vl.py", "modeling_d1.py", "runner.py", "prompt.py", "processing_lfm3_vl.py", "processor_config.json", "tokenizer.json", "config.json"):
        if not (snapshot / required).is_file():
            raise SystemExit(f"Pinned reference snapshot is missing required file {required}")

    files = {}
    total_bytes = 0
    for path in sorted(snapshot.rglob("*")):
        if not path.is_file() or ".cache" in path.parts:
            continue
        if path.is_symlink():
            raise SystemExit(f"Unexpected symlink in isolated model snapshot: {path}")
        relative = str(path.relative_to(snapshot))
        size = path.stat().st_size
        total_bytes += size
        files[relative] = {"bytes": size, "sha256": sha256(path)}

    provenance = {
        "repo": REPO,
        "revision": REVISION,
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "download_duration_s": elapsed,
        "format": "BF16 safetensors reference checkpoint",
        "snapshot_total_file_bytes": total_bytes,
        "snapshot_file_count": len(files),
        "model_file": {"path": "model.safetensors", **files["model.safetensors"]},
        "download_runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "huggingface_hub": __import__("huggingface_hub").__version__,
            "token_used": False,
            "offline_for_download": False,
        },
        "files": files,
    }
    (snapshot / "source.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "snapshot_dir": str(snapshot),
        "repo": REPO,
        "revision": REVISION,
        "file_count": len(files),
        "total_file_bytes": total_bytes,
        "model_bytes": MODEL_BYTES,
        "model_sha256": actual_hash,
        "download_duration_s": round(elapsed, 2),
        "token_used": False,
    }))


if __name__ == "__main__":
    main()
