"""Explicit, revision-pinned OvisOCR3 model download; never runs inference."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODEL_DIR = ROOT.parents[3] / "ocr-v2-models" / "ovisocr3"
MODEL_ID = "ATH-MaaS/OvisOCR3"
REVISION = "4833d5d2afa6e769ee933f024504d67ace80a24c"
WEIGHT_NAME = "model.safetensors"
WEIGHT_BYTES = 1_706_030_528
# Official Hugging Face Hub API metadata with `?blobs=true` reports this LFS SHA-256.
# The raw Xet HTTP ETag observed during transfer was 51a37b... and is not the file hash.
WEIGHT_SHA256 = "aa2311c6b22b07cf3afe9aac6e8bcb02a2b19dd8671b2a91d0b144071577e600"
WEIGHT_HTTP_ETAG = "51a37b0ef5ac07483ebca13d1ca6ffd3824a722fe9b1a4675093482fd4637ffd"
ALLOW_PATTERNS = [
    "README.md", "LICENSE", "config.json", "generation_config.json", "chat_template.jinja",
    "preprocessor_config.json", "video_preprocessor_config.json", "model.safetensors",
    "merges.txt", "tokenizer.json", "tokenizer_config.json", "vocab.json", "prompts/*.txt",
]


def sha256(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    # This is the only networking command in the OCR benchmark; inference scripts stay offline.
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)
    # This host's IPv6 route stalls at the HF CDN. Prefer IPv4 for explicit setup downloads,
    # while retaining IPv6 records as a fallback for hosts without IPv4.
    original_getaddrinfo = socket.getaddrinfo

    def ipv4_first(*args, **kwargs):
        records = original_getaddrinfo(*args, **kwargs)
        return sorted(records, key=lambda row: 0 if row[0] == socket.AF_INET else 1)

    socket.getaddrinfo = ipv4_first
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    from huggingface_hub import snapshot_download

    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    MODEL_DIR.parent.mkdir(parents=True, exist_ok=True)
    print(json.dumps({
        "event": "explicit_model_download_start", "repo": MODEL_ID, "revision": REVISION,
        "destination": str(MODEL_DIR), "allow_patterns": ALLOW_PATTERNS,
        "expected_weight_bytes": WEIGHT_BYTES, "timestamp_utc": started_at,
    }), flush=True)
    path = Path(snapshot_download(
        repo_id=MODEL_ID, revision=REVISION, local_dir=str(MODEL_DIR),
        allow_patterns=ALLOW_PATTERNS,
    ))
    weights = path / WEIGHT_NAME
    if not weights.is_file():
        raise FileNotFoundError(f"Expected {weights} after explicit snapshot download")
    observed_bytes = weights.stat().st_size
    observed_sha = sha256(weights)
    if observed_bytes != WEIGHT_BYTES or observed_sha != WEIGHT_SHA256:
        raise RuntimeError(
            f"Pinned artifact mismatch: bytes {observed_bytes} (wanted {WEIGHT_BYTES}), "
            f"sha256 {observed_sha} (wanted {WEIGHT_SHA256})"
        )
    config = json.loads((path / "config.json").read_text(encoding="utf-8"))
    if config.get("architectures") != ["Qwen3_5ForConditionalGeneration"]:
        raise RuntimeError(f"Unexpected model architecture in pinned config: {config.get('architectures')}")
    if config.get("auto_map"):
        raise RuntimeError("Pinned model requests custom remote code; review before use")
    if not (path / "LICENSE").is_file():
        raise RuntimeError("Pinned Apache license file is missing")
    file_records = []
    for item in sorted(path.rglob("*")):
        if item.is_file() and ".cache" not in item.parts:
            file_records.append({"path": str(item.relative_to(path)), "bytes": item.stat().st_size, "sha256": sha256(item)})
    result = {
        "model_id": MODEL_ID, "revision": REVISION, "source": "https://huggingface.co/ATH-MaaS/OvisOCR3/tree/" + REVISION,
        "license": "Apache-2.0", "runtime_route": "official Transformers API, explicit PyTorch MPS device",
        "download_transport": "explicit Hugging Face Hub HTTP transfer with Xet disabled and IPv4 preferred after observed IPv6 SYN stall",
        "weights": {"file": WEIGHT_NAME, "bytes": observed_bytes, "sha256": observed_sha,
                    "official_hub_lfs_sha256": WEIGHT_SHA256, "observed_http_etag": WEIGHT_HTTP_ETAG,
                    "config_dtype": config.get("text_config", {}).get("dtype")},
        "architecture": config["architectures"][0], "remote_code": False,
        "download_started_at_utc": started_at, "download_completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "download_wall_seconds": round(time.perf_counter() - started, 3),
        "destination": "../ocr-v2-models/ovisocr3 (outside the Git worktree)",
        "files": file_records,
    }
    out = ROOT / "provenance" / "ovisocr3-download-manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"event": "explicit_model_download_verified", "wall_seconds": result["download_wall_seconds"],
                      "weights": result["weights"], "file_count": len(file_records), "manifest": str(out)}, indent=2), flush=True)


if __name__ == "__main__":
    main()
