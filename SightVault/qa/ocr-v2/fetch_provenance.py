"""Capture pinned official OCR source records and small documentation files only.

Model weights are never fetched by this script. OvisOCR3 weights were downloaded
separately by download_ovis_model.py after fit/source review.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import shutil
import socket
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "provenance"
MODEL_ROOT = ROOT.parents[3] / "ocr-v2-models"
OVIS_MODEL = MODEL_ROOT / "ovisocr3"
MINICPM_MLX_SNAPSHOT = MODEL_ROOT / "minicpm-ocr-mlx-4bit"
RAPID_SITE = Path(importlib.metadata.distribution("rapidocr-onnxruntime").locate_file("rapidocr_onnxruntime")).parent

OVIS_REPO = "ATH-MaaS/OvisOCR3"
OVIS_REV = "4833d5d2afa6e769ee933f024504d67ace80a24c"
MINICPM_REPO = "openbmb/MiniCPM-OCR-1.0"
MINICPM_REV = "c5833cddb363aa62f1cb60ddde48c832e8f1a92c"
MINICPM_GITHUB_REV = "8a16a6ad007b1105ac7811ab238808587615e4c7"
RAPID_GITHUB_REV = "86ae3f5079df3422c1829cd84baf19bc8a7a9453"
RAPID_VERSION = "1.4.4"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url: str, destination: Path, max_bytes: int = 250_000, expected_hub_commit: str | None = None):
    request = urllib.request.Request(url, headers={"User-Agent": "SightVault-OCR-V2-provenance/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f"Refusing oversized source record ({len(data)} bytes): {url}")
        resolved = response.headers.get("X-Repo-Commit")
        etag = response.headers.get("ETag", "").strip('"')
    if expected_hub_commit and resolved and resolved != expected_hub_commit:
        raise RuntimeError(f"Expected Hub revision {expected_hub_commit}, got {resolved}: {url}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return {"path": str(destination.relative_to(ROOT)), "source_url": url,
            "bytes": len(data), "sha256": sha256_bytes(data), "resolved_revision": resolved,
            "etag": etag}


def copy_local(source: Path, destination: Path, source_url: str, revision: str):
    # Reject weight artifacts even if a caller accidentally broadens a copy list.
    if source.suffix.lower() in {".safetensors", ".bin", ".onnx", ".gguf", ".pt", ".pth"}:
        raise ValueError(f"Refusing to copy model weight into provenance: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    return {"path": str(destination.relative_to(ROOT)), "source_url": source_url,
            "source_revision": revision, "bytes": destination.stat().st_size,
            "sha256": sha256_file(destination)}


def json_fetch(url: str, destination: Path, max_bytes: int = 500_000):
    request = urllib.request.Request(url, headers={"User-Agent": "SightVault-OCR-V2-provenance/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"Refusing oversized JSON source record: {url}")
    json.loads(data)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return {"path": str(destination.relative_to(ROOT)), "source_url": url,
            "bytes": len(data), "sha256": sha256_bytes(data)}


def main():
    # On this host IPv6 to the model CDN stalled at SYN; prefer IPv4, retaining
    # IPv6 as fallback. These are explicit, small provenance downloads only.
    original_getaddrinfo = socket.getaddrinfo

    def ipv4_first(*args, **kwargs):
        rows = original_getaddrinfo(*args, **kwargs)
        return sorted(rows, key=lambda row: 0 if row[0] == socket.AF_INET else 1)

    socket.getaddrinfo = ipv4_first
    records = []
    started = datetime.now(timezone.utc).isoformat()

    # Preserve the exact Ovis snapshot documentation/prompts already verified at
    # the pinned model commit. The large checkpoint stays outside the repository.
    ovis_source = OUT / "ovisocr3"
    for rel in (
        "README.md", "LICENSE", "config.json", "generation_config.json", "chat_template.jinja",
        "preprocessor_config.json", "video_preprocessor_config.json", "merges.txt",
        "tokenizer_config.json", "prompts/plain_ocr.txt", "prompts/word_spotting.txt",
        "prompts/reference_grounding.txt", "prompts/region_ocr.txt", "prompts/document_layout.txt",
        "prompts/document_structured.txt", "prompts/document_markdown.txt", "prompts/kie_fields.txt",
        "prompts/kie_open.txt", "prompts/kie_schema.txt", "prompts/chart_parsing.txt",
        "prompts/crosspage_table.txt", "prompts/figure_captioning.txt",
    ):
        source = OVIS_MODEL / rel
        if source.is_file():
            records.append(copy_local(source, ovis_source / rel,
                f"https://huggingface.co/{OVIS_REPO}/tree/{OVIS_REV}/{rel}", OVIS_REV))

    # Capture the pinned MiniCPM model card and the local source files from the
    # existing read-only official snapshot. No MiniCPM model weights are copied.
    mini_source = OUT / "minicpm-ocr-1.0"
    records.append(fetch(
        f"https://huggingface.co/{MINICPM_REPO}/resolve/{MINICPM_REV}/README.md",
        mini_source / "README.md", expected_hub_commit=MINICPM_REV))
    for rel in ("config.json", "generation_config.json", "preprocessor_config.json",
                "processor_config.json", "tokenizer_config.json", "chat_template.jinja"):
        records.append(fetch(
            f"https://huggingface.co/{MINICPM_REPO}/resolve/{MINICPM_REV}/{rel}",
            mini_source / rel, expected_hub_commit=MINICPM_REV))
    local_source = MINICPM_MLX_SNAPSHOT / "source.json"
    if local_source.is_file():
        records.append(copy_local(local_source, mini_source / "local-source.json",
            "Local read-only acquisition metadata for the official MiniCPM snapshot", MINICPM_REV))
    converted_dir = mini_source / "converted-mlx-4bit"
    for rel in ("README.md", "config.json", "tokenizer_config.json", "processor_config.json", "source.json"):
        source = MINICPM_MLX_SNAPSHOT / rel
        if source.is_file():
            records.append(copy_local(source, converted_dir / rel,
                "Local read-only SightVault MLX conversion snapshot; mlx-vlm commit 918763c6875c80e82f9f48344dfad4763346fea8",
                "mlx-vlm@918763c6875c80e82f9f48344dfad4763346fea8"))
    records.append(fetch(
        f"https://raw.githubusercontent.com/OpenBMB/MiniCPM-OCR/{MINICPM_GITHUB_REV}/LICENSE",
        mini_source / "LICENSE", max_bytes=50_000))
    records.append(fetch(
        f"https://raw.githubusercontent.com/OpenBMB/MiniCPM-OCR/{MINICPM_GITHUB_REV}/docs/minicpm_ocr_1dot0_en.md",
        mini_source / "minicpm_ocr_1dot0_en.md", max_bytes=100_000))

    # RapidOCR is the actual in-product 1.4.4 fallback. Preserve its release
    # source, exact PyPI artifact metadata, installed package metadata, and model
    # asset hashes. Current merged-package migration remains a separate follow-up.
    rapid_source = OUT / "rapidocr-onnxruntime-1.4.4"
    for rel in ("README.md", "LICENSE"):
        records.append(fetch(
            f"https://raw.githubusercontent.com/RapidAI/RapidOCR/{RAPID_GITHUB_REV}/{rel}",
            rapid_source / rel, max_bytes=100_000))
    records.append(json_fetch(
        f"https://pypi.org/pypi/rapidocr-onnxruntime/{RAPID_VERSION}/json",
        rapid_source / "pypi-release.json"))
    metadata = Path(importlib.metadata.distribution("rapidocr-onnxruntime").locate_file(
        "rapidocr_onnxruntime-1.4.4.dist-info/METADATA"))
    if metadata.is_file():
        records.append(copy_local(metadata, rapid_source / "installed-METADATA.txt",
            f"https://pypi.org/project/rapidocr-onnxruntime/{RAPID_VERSION}/", RAPID_VERSION))
    # Retain the exact installed release code that establishes the bbox frame:
    # detector points are mapped to raw input dimensions, and word boxes are
    # reverse-mapped from recognition crops before that final origin transform.
    for rel in ("main.py", "cal_rec_boxes/main.py"):
        source = RAPID_SITE / "rapidocr_onnxruntime" / rel
        if source.is_file():
            records.append(copy_local(source, rapid_source / "source" / rel,
                f"https://pypi.org/project/rapidocr-onnxruntime/{RAPID_VERSION}/",
                f"rapidocr-onnxruntime=={RAPID_VERSION} exact installed wheel source"))
    assets = []
    package_models = RAPID_SITE / "rapidocr_onnxruntime/models"
    for path in sorted(package_models.glob("*.onnx")):
        assets.append({"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)})

    # Capture a pinned current-maintainer install guide to distinguish this
    # benchmarked legacy runtime from the new merged package.
    docs_api = "https://api.github.com/repos/RapidAI/RapidOCRDocs/commits/main"
    with urllib.request.urlopen(urllib.request.Request(docs_api, headers={"User-Agent": "SightVault-OCR-V2-provenance/1.0"}), timeout=30) as response:
        docs_commit = json.load(response)["sha"]
    docs_url = f"https://raw.githubusercontent.com/RapidAI/RapidOCRDocs/{docs_commit}/docs/install_usage/rapidocr/install.en.md"
    records.append(fetch(docs_url, OUT / "rapidocr-current-docs" / "install.en.md", max_bytes=100_000))

    manifest = {
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Official source documents/revision metadata and small local package assets only; no model weights copied by this script.",
        "models": {
            "ovisocr3": {"repo": OVIS_REPO, "revision": OVIS_REV,
                "license": "Apache-2.0", "runtime": "official Transformers; explicit PyTorch MPS in the measured Mac smoke",
                "source": f"https://huggingface.co/{OVIS_REPO}/tree/{OVIS_REV}"},
            "minicpm_ocr_1_0": {"repo": MINICPM_REPO, "revision": MINICPM_REV,
                "license": "Apache-2.0", "runtime": "MLX-VLM 0.7.11 / MLX 0.32.4; official source model card lists other runtimes",
                "source": f"https://huggingface.co/{MINICPM_REPO}/tree/{MINICPM_REV}",
                "license_source_revision": MINICPM_GITHUB_REV},
            "rapidocr_legacy": {"package": "rapidocr-onnxruntime", "version": RAPID_VERSION,
                "license": "Apache-2.0", "runtime": "ONNX Runtime CPU; official in-product fallback",
                "source_tag_commit": RAPID_GITHUB_REV, "model_assets": assets,
                "current_maintained_package_note": "Official docs state legacy rapidocr_onnxruntime is gradually retired in favor of rapidocr; migration is outside this benchmark arm."},
        },
        "files": records,
    }
    target = OUT / "official-source-manifest.json"
    target.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"captured_at_utc": started, "record_count": len(records),
        "manifest": str(target), "rapidocr_asset_count": len(assets)}, indent=2))


if __name__ == "__main__":
    main()
