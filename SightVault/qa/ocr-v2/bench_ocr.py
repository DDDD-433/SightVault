"""Offline OCR benchmark runner. Imports and loads only the selected backend.

Plain OCR is compared with a shared prompt. Structured capability probes use
model-specific official prompts and are scored separately. Returned markup is
stored as inert text and parsed only with Python parsers; it is never rendered,
executed, or passed to another model.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import platform
import re
import statistics
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
MODEL_ROOT = ROOT.parents[3] / "ocr-v2-models"
GT_PATH = ROOT / "ground_truth.jsonl"
SHARED_PROMPTS = {
    "plain": ROOT / "prompts/plain_ocr_shared.txt",
}
SPOTTING_IDS = ("T01", "I01", "F01", "TB02", "MC01", "ROT01")
KIE_PROBE_IDS = ("R01", "I01", "F01", "A01")
TABLE_PROBE_IDS = ("TB01", "TB02", "TB03")
CHART_PROBE_IDS = ("C01", "C02", "C03", "C04")
DOCUMENT_PARSE_IDS = ("I01", "F01", "TB02", "MC01")
CANDIDATES = {
    "ovis": {"model_id": "ATH-MaaS/OvisOCR3", "revision": "4833d5d2afa6e769ee933f024504d67ace80a24c",
        "source": "https://huggingface.co/ATH-MaaS/OvisOCR3/tree/4833d5d2afa6e769ee933f024504d67ace80a24c",
        "license": "Apache-2.0", "checkpoint_precision": "official BF16", "quantization": "none"},
    "minicpm": {"model_id": "openbmb/MiniCPM-OCR-1.0", "revision": "c5833cddb363aa62f1cb60ddde48c832e8f1a92c",
        "source": "https://huggingface.co/openbmb/MiniCPM-OCR-1.0/tree/c5833cddb363aa62f1cb60ddde48c832e8f1a92c",
        "license": "Apache-2.0", "runtime_conversion": "SightVault isolated MLX-VLM conversion",
        "quantization": "affine 4-bit group64, mixed BF16 layers"},
    "rapid": {"model_id": "rapidocr-onnxruntime==1.4.4", "revision": "PyPI release 1.4.4; RapidAI/RapidOCR source commit 86ae3f5079df3422c1829cd84baf19bc8a7a9453",
        "source": "https://github.com/RapidAI/RapidOCR/tree/86ae3f5079df3422c1829cd84baf19bc8a7a9453",
        "license": "Apache-2.0", "quantization": "ONNX checkpoint; precision not altered by this harness"},
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def json_safe(value):
    if hasattr(value, "tolist"):
        return json_safe(value.tolist())
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def load_cases():
    return [json.loads(line) for line in GT_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]


def balanced_category_order(cases):
    """Interleave categories for early 10-call milestones; preserve within-category order."""
    groups = {}
    for case in cases:
        groups.setdefault(case["category"], []).append(case)
    order = []
    categories = list(groups)
    while any(groups.values()):
        for category in categories:
            if groups[category]:
                order.append(groups[category].pop(0))
    return order


def _bbox_rect(box):
    try:
        if len(box) == 4 and all(isinstance(x, (int, float)) for x in box):
            x1, y1, x2, y2 = map(float, box)
        elif len(box) >= 4 and all(isinstance(point, (list, tuple)) and len(point) >= 2 for point in box):
            points = [(float(point[0]), float(point[1])) for point in box]
            x1 = min(x for x, _ in points); x2 = max(x for x, _ in points)
            y1 = min(y for _, y in points); y2 = max(y for _, y in points)
        else:
            return None
        return [x1, y1, x2, y2]
    except (TypeError, ValueError):
        return None


def _atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _shell_read(*argv):
    try:
        return subprocess.run(argv, check=False, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return "unavailable"


def _repo_relative(path):
    return os.path.relpath(Path(path), REPO)


def _recorded_argv():
    argv = list(sys.argv)
    argv[0] = _repo_relative(argv[0])
    for index, value in enumerate(argv[1:], 1):
        if Path(value).is_absolute():
            argv[index] = _repo_relative(value)
    return argv


class MemorySampler:
    def __init__(self, psutil, backend, runtime=None):
        self.psutil, self.backend, self.runtime = psutil, backend, runtime
        self.process = psutil.Process()
        self.peak_rss = self.process.memory_info().rss
        self.peak_device = 0
        self.stop = threading.Event()
        self.started = False
        self.thread = threading.Thread(target=self._sample, name="ocr-v2-memory", daemon=True)

    def _sample(self):
        while not self.stop.is_set():
            try:
                self.peak_rss = max(self.peak_rss, self.process.memory_info().rss)
            except Exception:
                pass
            try:
                if self.backend == "ovis":
                    value = int(self.runtime.mps.driver_allocated_memory())
                elif self.backend == "minicpm":
                    value = int(self.runtime.metal.get_active_memory())
                else:
                    value = 0
                self.peak_device = max(self.peak_device, value)
            except Exception:
                pass
            self.stop.wait(0.05)

    def start(self):
        self.started = True
        self.thread.start()

    def snapshot(self):
        try:
            self.peak_rss = max(self.peak_rss, self.process.memory_info().rss)
        except Exception:
            pass
        result = {"peak_process_rss_bytes": self.peak_rss, "sample_interval_ms": 50}
        if self.backend == "ovis":
            try:
                result["peak_mps_driver_allocated_bytes_sampled"] = self.peak_device
                result["mps_current_allocated_bytes_at_end"] = int(self.runtime.mps.current_allocated_memory())
                result["mps_driver_allocated_bytes_at_end"] = int(self.runtime.mps.driver_allocated_memory())
            except Exception as exc:
                result["device_memory_error"] = f"{type(exc).__name__}: {exc}"
        elif self.backend == "minicpm":
            try:
                result["peak_mlx_active_memory_bytes_sampled"] = self.peak_device
                result["mlx_active_memory_bytes_at_end"] = int(self.runtime.metal.get_active_memory())
                result["mlx_peak_memory_bytes"] = int(self.runtime.metal.get_peak_memory())
            except Exception as exc:
                result["device_memory_error"] = f"{type(exc).__name__}: {exc}"
        return result

    def close(self):
        self.stop.set()
        if self.started:
            self.thread.join(timeout=2)
        return self.snapshot()


def _read_mem_pressure():
    try:
        p = subprocess.run(["memory_pressure", "-Q"], capture_output=True, text=True, timeout=5, check=False)
        return {"exit_code": p.returncode, "stdout": p.stdout.strip(), "stderr": p.stderr.strip()}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _sum_files(path: Path):
    path = path.resolve()
    files = [p for p in path.rglob("*") if p.is_file() and ".cache" not in p.parts]
    return sum(p.stat().st_size for p in files), len(files)


class Backend:
    def __init__(self, name: str, task: str, model_dir: Path):
        self.name, self.task, self.model_dir = name, task, model_dir
        self.runtime = None
        self.engine = None
        self.processor = None
        self.model = None
        self.engine_details = {}

    def load(self):
        if self.name == "ovis":
            import torch
            from transformers import AutoModelForMultimodalLM, AutoProcessor
            if not (torch.backends.mps.is_built() and torch.backends.mps.is_available()):
                raise RuntimeError("Explicit MPS request but PyTorch MPS is unavailable")
            if not (self.model_dir / "model.safetensors").is_file():
                raise FileNotFoundError(f"Missing Ovis checkpoint: {self.model_dir}")
            self.runtime = torch
            self.processor = AutoProcessor.from_pretrained(
                str(self.model_dir), local_files_only=True, trust_remote_code=False)
            self.model = AutoModelForMultimodalLM.from_pretrained(
                str(self.model_dir), local_files_only=True, trust_remote_code=False, dtype="auto")
            self.model.to("mps")
            self.model.eval()
            self.runtime.mps.synchronize()
            devices = sorted({str(parameter.device) for parameter in self.model.parameters()})
            dtypes = sorted({str(parameter.dtype) for parameter in self.model.parameters()})
            if any(not device.startswith("mps") for device in devices):
                raise RuntimeError(f"Expected all Ovis parameters on MPS; observed {devices}")
            self.engine_details = {"runtime": "Transformers/PyTorch", "device": "mps",
                "torch": torch.__version__, "transformers": __import__("transformers").__version__,
                "accelerate": __import__("accelerate").__version__, "model_class": self.model.__class__.__name__,
                "parameter_devices": devices, "parameter_dtypes": dtypes,
                "mps_built": torch.backends.mps.is_built(), "mps_available": torch.backends.mps.is_available(),
                "mps_fallback_disabled": os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") == "0"}
        elif self.name == "minicpm":
            import mlx.core as mx
            from mlx_vlm import generate, load
            from mlx_vlm.prompt_utils import apply_chat_template
            if not (self.model_dir / "model.safetensors").is_file():
                raise FileNotFoundError(f"Missing MiniCPM MLX checkpoint: {self.model_dir}")
            self.runtime = mx
            self.engine = {"generate": generate, "apply_chat_template": apply_chat_template}
            self.model, self.processor = load(str(self.model_dir), trust_remote_code=False)
            self.engine_details = {"runtime": "MLX-VLM/MLX", "device": "Apple Metal GPU",
                "mlx_vlm": __import__("importlib.metadata", fromlist=["version"]).version("mlx-vlm"),
                "mlx_vlm_git_revision": "918763c6875c80e82f9f48344dfad4763346fea8",
                "mlx": mx.__version__, "model_class": self.model.__class__.__name__,
                "device_info": json_safe(mx.metal.device_info()) if hasattr(mx.metal, "device_info") else None}
        elif self.name == "rapid":
            import onnxruntime as ort
            from rapidocr_onnxruntime import RapidOCR
            self.runtime = ort
            self.model = RapidOCR(intra_op_num_threads=4, inter_op_num_threads=2)
            self.engine_details = {"runtime": "RapidOCR / ONNX Runtime", "device": "CPU",
                "rapidocr_onnxruntime": __import__("importlib.metadata", fromlist=["version"]).version("rapidocr-onnxruntime"),
                "onnxruntime": ort.__version__, "execution_providers_available": ort.get_available_providers(),
                "thread_configuration": {"intra_op_num_threads": 4, "inter_op_num_threads": 2},
                "word_boxes_requested": self.task == "spotting"}
        else:
            raise ValueError(f"Unknown backend: {self.name}")

    def predict(self, image, prompt: str | None, *, max_new_tokens: int):
        if self.name == "rapid":
            import numpy as np
            image_bgr = np.asarray(image)[:, :, ::-1].copy()
            raw, elapse = self.model(image_bgr, return_word_box=(self.task == "spotting"))
            lines = []
            words = []
            for item in raw or []:
                # v1.4.4 standard output: [line_quad, text, score]. With return_word_box,
                # the trailing fields hold word quads and word strings.
                quad, text, score = item[:3]
                lines.append({"bbox": json_safe(quad), "text": str(text), "score": float(score)})
                if self.task == "spotting" and len(item) >= 5:
                    word_quads, word_texts = item[3], item[4]
                    confs = item[5] if len(item) >= 6 else []
                    for index, (word_quad, word_text) in enumerate(zip(word_quads or [], word_texts or [])):
                        words.append({"bbox": json_safe(word_quad), "text": str(word_text),
                                      "score": float(confs[index]) if index < len(confs) else None})
            raw_text = "\n".join(item["text"] for item in lines)
            return {"text": raw_text, "lines": lines, "words": words,
                    "engine_stage_seconds": json_safe(elapse)}

        if prompt is None:
            raise RuntimeError(f"No prompt supplied for {self.name}/{self.task}")
        if self.name == "ovis":
            from PIL import Image
            messages = [{"role": "user", "content": [
                {"type": "image", "image": image}, {"type": "text", "text": prompt},
            ]}]
            inputs = self.processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                return_dict=True, return_tensors="pt", enable_thinking=False).to("mps")
            with self.runtime.inference_mode():
                outputs = self.model.generate(**inputs, do_sample=False,
                    max_new_tokens=max_new_tokens, use_cache=True)
            self.runtime.mps.synchronize()
            generated = outputs[0][inputs["input_ids"].shape[-1]:]
            generation_tokens = int(generated.shape[-1])
            text = self.processor.decode(generated,
                skip_special_tokens=True).strip()
            return {"text": text, "lines": [], "words": [],
                "generation_tokens": generation_tokens, "max_new_tokens": max_new_tokens,
                "at_token_budget": generation_tokens >= max_new_tokens,
                "finish_reason": None}

        apply_template = self.engine["apply_chat_template"]
        formatted = apply_template(self.processor, self.model.config, prompt,
            num_images=1, enable_thinking=False)
        result = self.engine["generate"](self.model, self.processor, formatted,
            image=[image], max_tokens=max_new_tokens, temperature=0, verbose=False)
        generation_tokens = getattr(result, "generation_tokens", None)
        return {"text": str(result.text).strip(), "lines": [], "words": [],
                "generation_tokens": generation_tokens, "max_new_tokens": max_new_tokens,
                "at_token_budget": generation_tokens is not None
                    and int(generation_tokens) >= max_new_tokens,
                "finish_reason": getattr(result, "finish_reason", None)}

    def release(self):
        if self.name == "ovis" and self.runtime is not None:
            self.model = self.processor = None
            gc.collect()
            try:
                if self.runtime.backends.mps.is_available():
                    self.runtime.mps.empty_cache()
                    self.runtime.mps.synchronize()
            except Exception:
                pass
        elif self.name == "minicpm" and self.runtime is not None:
            self.model = self.processor = self.engine = None
            gc.collect()
            try:
                self.runtime.clear_cache()
            except Exception:
                pass
        else:
            self.model = None


def _task_prompt(name: str, task: str, case: dict):
    if task == "plain":
        path = SHARED_PROMPTS["plain"]
        return path.read_text(encoding="utf-8").strip(), {"path": _repo_relative(path), "kind": "shared_plain_ocr"}
    if task == "spotting":
        if name == "minicpm":
            return None, {"unsupported_reason": "The tested MiniCPM MLX model card/runtime does not expose a word-bbox output task."}
        if name == "ovis":
            path = ROOT / "provenance/ovisocr3/prompts/word_spotting.txt"
            return path.read_text(encoding="utf-8").strip(), {"path": _repo_relative(path),
                "kind": "official_model_prompt", "bbox_coordinate_contract": _bbox_contract(name)}
        return None, {"kind": "native_return_word_box", "path": None,
            "bbox_coordinate_contract": _bbox_contract(name)}
    if task == "kie":
        keys = list(case.get("fields", {}).keys())
        if not keys:
            return None, {"skip_reason": "No authored key-value ground truth for this fixture."}
        if name == "rapid":
            return None, {"unsupported_reason": "RapidOCR 1.4.4 is a text detection/recognition engine without KIE generation."}
        if name == "ovis":
            path = ROOT / "provenance/ovisocr3/prompts/kie_fields.txt"
            text = path.read_text(encoding="utf-8").replace("{keys_json}", json.dumps(keys, ensure_ascii=False))
            return text.strip(), {"path": _repo_relative(path), "kind": "official_model_prompt", "keys": keys}
        path = ROOT / "prompts/kie_minicpm.txt"
        text = path.read_text(encoding="utf-8").replace("{keys_json}", json.dumps(keys, ensure_ascii=False))
        return text.strip(), {"path": _repo_relative(path), "kind": "benchmark_adaptation_of_official_model_card_task_template", "keys": keys}
    if task == "table":
        if not case.get("table_headers"):
            return None, {"skip_reason": "No authored table ground truth for this fixture."}
        if name == "rapid":
            return None, {"unsupported_reason": "RapidOCR provides text and geometry but not table structure."}
        if name == "ovis":
            path = ROOT / "provenance/ovisocr3/prompts/document_structured.txt"
            return path.read_text(encoding="utf-8").strip(), {"path": _repo_relative(path), "kind": "official_model_prompt", "output_markup_inert": True}
        return "Table Recognition:", {"path": None, "kind": "official_model_card_task_prompt", "output_markup_inert": True}
    if task == "chart":
        if not case.get("chart"):
            return None, {"skip_reason": "No authored chart ground truth for this fixture."}
        if name == "rapid":
            return None, {"unsupported_reason": "RapidOCR provides text and geometry but not chart understanding."}
        if name == "ovis":
            path = ROOT / "provenance/ovisocr3/prompts/chart_parsing.txt"
            return path.read_text(encoding="utf-8").strip(), {"path": _repo_relative(path), "kind": "official_model_prompt", "output_markup_inert": True}
        return "Chart to Table:", {"path": None, "kind": "official_model_card_task_prompt", "output_markup_inert": True}
    if task == "document_parse":
        if name == "ovis":
            path = ROOT / "provenance/ovisocr3/prompts/document_structured.txt"
            return path.read_text(encoding="utf-8").strip(), {"path": _repo_relative(path),
                "kind": "official_model_prompt", "bbox_coordinate_contract": _bbox_contract(name),
                "output_markup_inert": True}
        if name == "minicpm":
            return None, {"unsupported_reason": "The measured MiniCPM MLX adapter is direct image recognition; the documented full-document SDK path also requires a separate PP-DocLayoutV3 layout stage that is not part of this tested runtime."}
        return None, {"unsupported_reason": "RapidOCR 1.4.4 returns OCR text/geometry but does not implement full-page document layout parsing."}
    raise ValueError(f"Unsupported task: {task}")


def _normalized_image(path: Path):
    from PIL import Image, ImageOps
    with Image.open(path) as source:
        oriented = ImageOps.exif_transpose(source)
        rgba = oriented.convert("RGBA")
        canvas = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        canvas.alpha_composite(rgba)
        return canvas.convert("RGB")


def _eval_attempt(task, case, prediction, backend_name, bbox_contract=None):
    from metrics import evaluate_plain
    from structured_metrics import (evaluate_boxes, evaluate_chart, evaluate_kie,
        evaluate_ovis_document_parse, evaluate_table)
    text = prediction.get("text", "")
    if task == "plain":
        return evaluate_plain(case, text)
    if task == "document_parse":
        return evaluate_ovis_document_parse(case, text, bbox_contract)
    if task == "kie":
        return evaluate_kie(case.get("fields", {}), text)
    if task == "table":
        return evaluate_table(case.get("table_headers", []), case.get("table_rows", []), text)
    if task == "chart":
        return evaluate_chart(case.get("chart", {}), text)
    if task == "spotting":
        contract = bbox_contract or _bbox_contract(backend_name)
        frame = contract.get("coordinate_frame") if contract.get("supported") else "unknown"
        if backend_name == "rapid":
            predicted = prediction.get("words", [])
            metrics = evaluate_boxes(case.get("word_boxes_xyxy_px", []), predicted,
                case["image"]["width"], case["image"]["height"], coordinate_frame=frame)
            metrics.update({"serialization_valid": True, "schema_valid": True,
                "output_format": "native RapidOCR word-box API", "parse_error": None})
            return metrics
        try:
            items = json.loads(text)
            parse_error = None
        except Exception as exc:
            items, parse_error = [], f"{type(exc).__name__}: {exc}"
        schema_valid = (isinstance(items, list) and all(isinstance(item, dict)
            and set(item) == {"bbox", "text"} and isinstance(item.get("text"), str)
            and isinstance(item.get("bbox"), list) and len(item["bbox"]) == 4
            and all(isinstance(value, int) and not isinstance(value, bool) for value in item["bbox"])
            for item in items))
        if not isinstance(items, list):
            items = []
        metrics = evaluate_boxes(case.get("word_boxes_xyxy_px", []), items,
            case["image"]["width"], case["image"]["height"], coordinate_frame=frame)
        metrics.update({"serialization_valid": parse_error is None,
            "schema_valid": schema_valid, "output_format": "Ovis official JSON word-spotting schema",
            "parse_error": parse_error})
        return metrics
    return {}


def _aggregate_prf(metrics_rows, key):
    parts = [row.get(key, {}) for row in metrics_rows if row.get(key)]
    tp = sum(x.get("tp", 0) for x in parts)
    fp = sum(x.get("fp", 0) for x in parts)
    fn = sum(x.get("fn", 0) for x in parts)
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = (2*p*r/(p+r) if p is not None and r is not None and p+r
          else (0.0 if tp + fp + fn else None))
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f1,
            "ground_truth_support": tp + fn, "predicted_support": tp + fp}


def _validity_summary(metrics_rows, key):
    values = [row.get(key) for row in metrics_rows if key in row and row.get(key) is not None]
    valid = sum(value is True for value in values)
    return {"valid_cases": valid, "evaluable_cases": len(values),
        "rate": valid / len(values) if values else None}


def _refresh_schema_record(run):
    """Keep the pack's result schema populated in every checkpoint and final record."""
    attempts = [row for row in run.get("cases", []) if row.get("status") in ("completed", "failed")]
    failures = [row for row in attempts if row.get("status") == "failed"]
    ooms = [row for row in failures if any(token in json.dumps(row.get("failure", {})).lower()
        for token in ("out of memory", "oom", "cuda_error_out_of_memory", "mps backend out of memory"))]
    run["stability"] = {"requests": len(attempts), "failures": len(failures), "ooms": len(ooms),
        "notes": "Counts include declared repeats; quality uses unique images only. Unsupported/skipped capability cases are reported separately."}
    timing = run.get("timing_seconds", {})
    warm = [row.get("timing_seconds", {}).get("image_total") for row in attempts
        if row.get("status") == "completed" and row.get("index", 0) > 0
        and row.get("timing_seconds", {}).get("image_total") is not None]
    warm_sorted = sorted(warm)
    warm_p95 = warm_sorted[max(0, math.ceil(0.95 * len(warm_sorted)) - 1)] if warm_sorted else None
    memory = run.get("memory", {})
    rss = memory.get("peak_process_rss_bytes")
    accel = (memory.get("peak_mps_driver_allocated_bytes_sampled") if run.get("backend") == "ovis" else
        memory.get("peak_mlx_active_memory_bytes_sampled") if run.get("backend") == "minicpm" else None)
    run["performance"] = {"cold_load_s": timing.get("model_load"),
        "first_request_s": timing.get("first_request"),
        "warm_median_s": statistics.median(warm) if warm else None,
        "warm_p95_s": warm_p95,
        "peak_rss_mb": round(rss / (1024**2), 3) if rss is not None else None,
        "peak_accelerator_mb": round(accel / (1024**2), 3) if accel is not None and run.get("backend") != "rapid" else None}


def _preflight_fixture_set():
    import PIL
    from PIL import Image, ImageOps
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    rows = load_cases()
    if len(rows) != manifest["image_count"]:
        raise RuntimeError(f"Manifest expects {manifest['image_count']} fixtures, found {len(rows)} labels")
    if sha256_file(GT_PATH) != manifest["ground_truth_file"]["sha256"]:
        raise RuntimeError("Ground-truth JSONL hash does not match the frozen manifest")
    row_by_id = {row["id"]: row for row in rows}
    byte_total = 0
    for image_meta in manifest["images"]:
        image_path = ROOT / image_meta["path"]
        if not image_path.is_file():
            raise FileNotFoundError(f"Missing frozen fixture image: {image_meta['path']}")
        image_bytes = image_path.read_bytes()
        if len(image_bytes) != image_meta["bytes"] or sha256_bytes(image_bytes) != image_meta["sha256"]:
            raise RuntimeError(f"Frozen fixture bytes changed: {image_meta['id']}")
        with Image.open(image_path) as source:
            oriented = ImageOps.exif_transpose(source)
            rgba = oriented.convert("RGBA")
            canvas = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            canvas.alpha_composite(rgba)
            normalized = canvas.convert("RGB")
        if [normalized.width, normalized.height] != [image_meta["width"], image_meta["height"]]:
            raise RuntimeError(f"Normalized image geometry differs from the manifest: {image_meta['id']}")
        row = row_by_id.get(image_meta["id"])
        if row is None or row["image"]["sha256"] != image_meta["sha256"]:
            raise RuntimeError(f"Ground truth does not reference the frozen image: {image_meta['id']}")
        byte_total += len(image_bytes)
    return {"fixture_count": len(rows), "total_image_bytes": byte_total, "pillow_version": PIL.__version__,
        "manifest_sha256": sha256_file(ROOT / "manifest.json"),
        "ground_truth_sha256": sha256_file(GT_PATH)}


def _preflight_adapter(name, model_dir):
    result = {"adapter": name, "model_path": _repo_relative(model_dir), "model_files": {}}
    if name == "ovis":
        import torch
        import transformers
        import accelerate
        checkpoint = model_dir / "model.safetensors"
        config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
        if not checkpoint.is_file():
            raise FileNotFoundError("Ovis safetensors checkpoint missing")
        if config.get("architectures") != ["Qwen3_5ForConditionalGeneration"] or config.get("auto_map"):
            raise RuntimeError("Pinned Ovis config no longer matches reviewed official architecture/no-remote-code assumptions")
        result.update({"imports": {"torch": torch.__version__, "transformers": transformers.__version__,
            "accelerate": accelerate.__version__}, "device_available": {"mps_built": torch.backends.mps.is_built(),
            "mps_available": torch.backends.mps.is_available()}, "configured_dtype": config.get("text_config", {}).get("dtype"),
            "model_files": {"model.safetensors": {"bytes": checkpoint.stat().st_size},
                "config.json": {"bytes": (model_dir / "config.json").stat().st_size}}})
    elif name == "minicpm":
        import mlx.core as mx
        import mlx_vlm
        from mlx_vlm import generate, load
        checkpoint = model_dir / "model.safetensors"
        if not checkpoint.is_file():
            raise FileNotFoundError("MiniCPM MLX safetensors checkpoint missing")
        result.update({"imports": {"mlx": mx.__version__,
            "mlx-vlm": __import__("importlib.metadata", fromlist=["version"]).version("mlx-vlm")},
            "device_available": {"metal_device_info": json_safe(mx.metal.device_info()) if hasattr(mx.metal, "device_info") else None},
            "model_files": {"model.safetensors": {"bytes": checkpoint.stat().st_size},
                "source.json": {"bytes": (model_dir / "source.json").stat().st_size}}})
        _ = mlx_vlm, generate, load
    else:
        import onnxruntime as ort
        import rapidocr_onnxruntime
        from rapidocr_onnxruntime import RapidOCR
        import importlib.metadata
        result.update({"imports": {"rapidocr_onnxruntime": importlib.metadata.version("rapidocr-onnxruntime"),
            "onnxruntime": ort.__version__}, "device_available": {"providers": ort.get_available_providers()},
            "module_path": _repo_relative(Path(rapidocr_onnxruntime.__file__).resolve()),
            "model_files": {"onnx_models_present_in_package": (Path(rapidocr_onnxruntime.__file__).parent / "models").is_dir()}})
        _ = RapidOCR
    return result


def _bbox_contract(name):
    contracts = json.loads((ROOT / "provenance/bbox-coordinate-contracts.json").read_text(encoding="utf-8"))
    return contracts.get(name, {"supported": False,
        "reason": "No pinned source established a coordinate unit and image frame."})


def _unsupported_result(args, output, candidate, model, platform_record, runtime_name,
        mac_path, windows_path, reason, case_ids=()):
    return {"experiment": "SightVault OCR_V2", "backend": args.model,
        "baseline_commit_or_hash": "766027c79d4c6f17ba7b88b1df3bc079dfbbd141",
        "experiment_commit": _shell_read("git", "-C", str(REPO), "rev-parse", "HEAD"),
        "platform": platform_record, "model": model,
        "runtime": {"name": runtime_name, "version": "not loaded; capability unsupported by selected package/runtime",
            "device": "not loaded", "offline_verified": False},
        "performance": {"cold_load_s": None, "first_request_s": None, "warm_median_s": None,
            "warm_p95_s": None, "peak_rss_mb": None, "peak_accelerator_mb": None},
        "quality": {"supported": False, "reason": reason},
        "stability": {"requests": 0, "failures": 0, "ooms": 0,
            "notes": "No inference request was sent because the selected API does not support this capability."},
        "portability": {"mac_measured": False, "windows_measured": False,
            "mac_supported_path": mac_path, "windows_supported_path": windows_path,
            "notes": "Capability support was classified from the measured package/API; no runtime call was made."},
        "raw_outputs_path": _repo_relative(output),
        "probe_case_ids": list(case_ids),
        "notes": "The capability was explicitly reported unsupported; it was not simulated.",
        "candidate": candidate, "task": args.task, "status": "unsupported", "reason": reason,
        "timestamp_utc": datetime.now(timezone.utc).isoformat()}


def _quality_summary(task, attempts):
    unique = []
    seen = set()
    for row in attempts:
        if row.get("image_id") not in seen and row.get("metrics"):
            seen.add(row["image_id"])
            unique.append(row["metrics"])
    if task == "plain":
        from metrics import summarize_plain
        return summarize_plain([{"metrics": row} for row in unique])
    if task == "kie":
        return {"unique_cases": len(unique), "exact_cases": sum(bool(x.get("exact_match")) for x in unique),
                "serialization": _validity_summary(unique, "serialization_valid"),
                "schema": _validity_summary(unique, "schema_valid"),
                "field_micro": _aggregate_prf(unique, "fields"),
                "json_parse_failures": sum(bool(x.get("parse_error")) for x in unique)}
    if task == "table":
        return {"unique_cases": len(unique), "exact_tables": sum(bool(x.get("exact_table")) for x in unique),
                "serialization": _validity_summary(unique, "serialization_valid"),
                "schema": _validity_summary(unique, "schema_valid"),
                "cell_micro": _aggregate_prf(unique, "cells"),
                "numeric_value_micro": _aggregate_prf(unique, "numeric_values"),
                "parse_failures": sum(bool(x.get("parse_error")) for x in unique)}
    if task == "chart":
        return {"unique_cases": len(unique), "exact_chart_header_cases": sum(x.get("exact_headers") is True for x in unique),
                "serialization": _validity_summary(unique, "serialization_valid"),
                "schema": _validity_summary(unique, "schema_valid"),
                "value_pair_micro": _aggregate_prf(unique, "value_pairs"),
                "numeric_value_micro": _aggregate_prf(unique, "numeric_values"),
                "exact_charts": sum(x.get("exact_chart") is True for x in unique),
                "json_parse_failures": sum(bool(x.get("parse_error")) for x in unique)}
    if task == "spotting":
        scored = [x for x in unique if x.get("status") == "scored"]
        return {"unique_cases": len(unique), "supported_cases": len(scored),
                "unsupported_cases": sum(x.get("status") != "scored" for x in unique),
                "serialization": _validity_summary(unique, "serialization_valid"),
                "schema": _validity_summary(unique, "schema_valid"),
                "bbox_micro": _aggregate_prf(unique, "exact_text_and_bbox"),
                "text_only_micro": _aggregate_prf(unique, "text_only"),
                "mean_case_matched_iou": (sum(x["matched_mean_iou"] for x in unique if x.get("matched_mean_iou") is not None)
                    / max(1, sum(x.get("matched_mean_iou") is not None for x in unique)))}
    if task == "document_parse":
        metrics = unique
        text_rows = [{"metrics": x["text_metrics"]} for x in metrics]
        from metrics import summarize_plain
        bbox_count = sum(x["bbox_geometry"]["bbox_count"] for x in metrics)
        valid_bbox_count = sum(x["bbox_geometry"]["valid_bbox_count"] for x in metrics)
        return {"unique_cases": len(unique), "supported_cases": len(metrics),
            "serialization": _validity_summary(metrics, "serialization_valid"),
            "schema": _validity_summary(metrics, "schema_valid"),
            "bbox_geometry": {"valid_bbox_count": valid_bbox_count,
                "bbox_count": bbox_count, "all_bboxes_valid": bool(metrics)
                    and all(x["bbox_geometry"]["all_bboxes_valid"] for x in metrics)},
            "content_text": summarize_plain(text_rows) if text_rows else None}
    return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=("ovis", "minicpm", "rapid"), required=True)
    parser.add_argument("--task", choices=("plain", "spotting", "kie", "table", "chart", "document_parse"), default="plain")
    parser.add_argument("--throughput-count", type=int, default=100,
        help="Continuous inference calls for plain task; unique corpus is traversed then starts again.")
    parser.add_argument("--max-new-tokens", type=int, default=384)
    parser.add_argument("--case-ids", default=None,
        help="Comma-separated fixture IDs for a bounded capability probe; not allowed for plain throughput runs.")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--preflight-only", action="store_true",
        help="Check fixture hashes and import the selected runtime without loading model weights or inferring")
    args = parser.parse_args()
    if args.throughput_count < 1:
        parser.error("--throughput-count must be positive")
    if args.max_new_tokens < 1:
        parser.error("--max-new-tokens must be positive")
    if args.task == "plain" and args.case_ids:
        parser.error("case-ids is only for bounded capability probes, not the frozen plain corpus")
    if args.task != "plain" and args.throughput_count != 100:
        # Capability tasks use each eligible unique fixture once; don't repeat them silently.
        parser.error("throughput-count is only configurable for plain OCR capability runs")

    os.environ.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1", "USE_HUB_KERNELS": "NO", "TOKENIZERS_PARALLELISM": "false"})
    if os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK", "0").lower() not in ("0", "false", "no"):
        parser.error("Set PYTORCH_ENABLE_MPS_FALLBACK=0 before running; MPS fallback is prohibited")
    from offline_guard import install_socket_guard
    blocked, uninstall_guard = install_socket_guard()

    all_cases = load_cases()
    cases = all_cases
    if args.task == "plain":
        cases = balanced_category_order(cases)
    if args.task == "spotting":
        cases = [x for x in cases if x["id"] in SPOTTING_IDS]
    elif args.task == "kie":
        cases = [x for x in cases if x.get("fields") and x["id"] in KIE_PROBE_IDS]
    elif args.task == "table":
        cases = [x for x in cases if x.get("table_headers") and x["id"] in TABLE_PROBE_IDS]
    elif args.task == "chart":
        cases = [x for x in cases if (x.get("chart") or {}).get("values") and x["id"] in CHART_PROBE_IDS]
    elif args.task == "document_parse":
        cases = [x for x in cases if x["id"] in DOCUMENT_PARSE_IDS]
    if args.case_ids:
        requested_ids = [part.strip() for part in args.case_ids.split(",") if part.strip()]
        if not requested_ids:
            parser.error("case-ids must contain at least one fixture ID")
        if len(requested_ids) != len(set(requested_ids)):
            parser.error("case-ids must not contain duplicates")
        known = {case["id"] for case in all_cases}
        unknown = set(requested_ids) - known
        if unknown:
            parser.error(f"Unknown fixture IDs: {', '.join(sorted(unknown))}")
        eligible = {case["id"] for case in cases}
        ineligible = set(requested_ids) - eligible
        if ineligible:
            parser.error(f"Fixture IDs do not support {args.task}: {', '.join(sorted(ineligible))}")
        by_id = {case["id"]: case for case in cases}
        cases = [by_id[case_id] for case_id in requested_ids]
    if not cases:
        parser.error(f"No fixture cases support task {args.task}")

    if args.model == "ovis":
        model_dir = MODEL_ROOT / "ovisocr3"
    elif args.model == "minicpm":
        model_dir = MODEL_ROOT / "minicpm-ocr-mlx-4bit"
    else:
        model_dir = Path(__import__("importlib.metadata", fromlist=["distribution"]).distribution(
            "rapidocr-onnxruntime").locate_file("rapidocr_onnxruntime"))

    if args.preflight_only:
        try:
            fixture_preflight = _preflight_fixture_set()
            adapter_preflight = _preflight_adapter(args.model, model_dir)
            flags_ok = all(os.environ.get(key) == value for key, value in (
                ("HF_HUB_OFFLINE", "1"), ("TRANSFORMERS_OFFLINE", "1"),
                ("HF_HUB_DISABLE_TELEMETRY", "1"), ("USE_HUB_KERNELS", "NO"),
                ("PYTORCH_ENABLE_MPS_FALLBACK", "0")))
            offline_verified = not blocked and flags_ok and os.environ.get("PHASE2_OCR_SANDBOX_PROFILE") == "macOS sandbox-exec outbound deny with loopback allowed"
            result = {"status": "preflight_passed", "adapter": adapter_preflight,
                "fixtures": fixture_preflight, "offline_verified": offline_verified,
                "blocked_external_attempts": blocked,
                "weights_loaded": False, "inference_calls": 0}
            if not offline_verified:
                raise RuntimeError("Preflight did not satisfy offline flags/sandbox or recorded an external access attempt")
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            return 0
        except Exception as exc:
            print(json.dumps({"status": "preflight_failed", "backend": args.model,
                "failure": {"type": type(exc).__name__, "message": str(exc)},
                "blocked_external_attempts": blocked, "weights_loaded": False,
                "inference_calls": 0}, ensure_ascii=False, indent=2), flush=True)
            return 1
        finally:
            uninstall_guard()

    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or ROOT / "results/raw_outputs" / f"{args.model}-{args.task}-{run_id}.json"
    output = output.resolve()
    candidates = CANDIDATES[args.model]
    if args.model == "rapid":
        provenance = json.loads((ROOT/"provenance/official-source-manifest.json").read_text(encoding="utf-8"))
        artifact_bytes = sum(item["bytes"] for item in provenance["models"]["rapidocr_legacy"]["model_assets"])
        model_format = "ONNX detector, angle classifier, and recognizer pipeline"
        model_quantization = "Upstream ONNX precision unchanged by this experiment"
    else:
        artifact_bytes = (model_dir/"model.safetensors").stat().st_size
        model_format = "Safetensors"
        model_quantization = candidates["quantization"]
    schema_model = {"repo": candidates["model_id"], "revision": candidates["revision"],
        "format": model_format, "quantization": model_quantization, "artifact_bytes": artifact_bytes}
    experiment_commit = _shell_read("git", "-C", str(REPO), "rev-parse", "HEAD")
    ram_bytes_text = _shell_read("sysctl", "-n", "hw.memsize")
    platform_record = {"os": _shell_read("sw_vers", "-productVersion"), "arch": platform.machine(),
        "hardware": _shell_read("sysctl", "-n", "machdep.cpu.brand_string"),
        "ram_gb": round(int(ram_bytes_text) / (1024**3), 3) if ram_bytes_text.isdigit() else None,
        "python": sys.version}
    runtime_name = {"ovis": "Transformers/PyTorch", "minicpm": "MLX-VLM/MLX",
        "rapid": "RapidOCR/ONNX Runtime CPU"}[args.model]
    mac_path = {"ovis": "Transformers/PyTorch on Apple MPS; explicit BF16, operator fallback disabled",
        "minicpm": "MLX-VLM on Apple Metal; affine 4-bit group64 conversion",
        "rapid": "ONNX Runtime CPU with RapidOCR 1.4.4"}[args.model]
    windows_path = {"ovis": "Transformers/PyTorch CPU or CUDA path; Windows not measured",
        "minicpm": "The tested MLX conversion is Apple-only; official source model lists other runtimes, Windows not measured",
        "rapid": "ONNX Runtime CPU path; Windows not measured"}[args.model]
    if args.model == "rapid" and args.task == "spotting":
        # Legacy v1.4.4 exposes word boxes through return_word_box=True.
        pass
    if args.model == "minicpm" and args.task == "spotting":
        result = _unsupported_result(args, output, candidates, schema_model, platform_record,
            runtime_name, mac_path, windows_path,
            "The measured MiniCPM MLX runtime does not expose a word-bbox task.",
            case_ids=[case["id"] for case in cases])
        _atomic_json(output, result)
        print(json.dumps({"status": result["status"], "reason": result["reason"], "output": _repo_relative(output)}), flush=True)
        uninstall_guard()
        return 0
    if args.model == "rapid" and args.task in ("kie", "table", "chart", "document_parse"):
        result = _unsupported_result(args, output, candidates, schema_model, platform_record,
            runtime_name, mac_path, windows_path,
            "RapidOCR 1.4.4 is line/word OCR with geometry, not a generative document-understanding engine.",
            case_ids=[case["id"] for case in cases])
        _atomic_json(output, result)
        print(json.dumps({"status": result["status"], "reason": result["reason"], "output": _repo_relative(output)}), flush=True)
        uninstall_guard()
        return 0
    if args.model == "minicpm" and args.task == "document_parse":
        result = _unsupported_result(args, output, candidates, schema_model, platform_record,
            runtime_name, mac_path, windows_path,
            "The measured MiniCPM MLX adapter is direct image recognition. The official full-document SDK route uses a separate PP-DocLayoutV3 layout stage, which is not included in this tested runtime.",
            case_ids=[case["id"] for case in cases])
        _atomic_json(output, result)
        print(json.dumps({"status": result["status"], "reason": result["reason"], "output": _repo_relative(output)}), flush=True)
        uninstall_guard()
        return 0

    model_artifacts, artifact_file_count = _sum_files(model_dir)
    run = {
        "experiment": "SightVault OCR_V2", "backend": args.model,
        "baseline_commit_or_hash": "766027c79d4c6f17ba7b88b1df3bc079dfbbd141",
        "experiment_commit": experiment_commit,
        "model": schema_model,
        "platform": platform_record,
        "runtime": {"name": runtime_name, "version": "pending exact runtime load record",
            "device": "pending selected backend load", "offline_verified": False},
        "performance": {"cold_load_s": None, "first_request_s": None, "warm_median_s": None,
            "warm_p95_s": None, "peak_rss_mb": None, "peak_accelerator_mb": None},
        "stability": {"requests": 0, "failures": 0, "ooms": 0,
            "notes": "Counts include declared repeats; quality uses unique images only."},
        "portability": {"mac_measured": False, "windows_measured": False,
            "mac_supported_path": mac_path, "windows_supported_path": windows_path,
            "notes": "Windows is untested on hardware in this experiment."},
        "raw_outputs_path": _repo_relative(output),
        "notes": "Plain OCR uses one shared prompt. Capability prompts are candidate-specific. Returned HTML/Markdown/Mermaid is never rendered or executed.",
        "experiment_code_sha256": {"bench_ocr.py": sha256_file(ROOT/"bench_ocr.py"),
            "offline_guard.py": sha256_file(ROOT/"offline_guard.py"), "smoke_ovis.py": sha256_file(ROOT/"smoke_ovis.py")},
        "tracked_worktree_dirty_at_start": bool(_shell_read("git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no")),
        "candidate": candidates, "task": args.task, "status": "running", "run_id": run_id,
        "dataset": {"fixture_count": len(load_cases()), "manifest_sha256": sha256_file(ROOT / "manifest.json"),
            "ground_truth_sha256": sha256_file(GT_PATH), "label_source": "authored literals/fields; independent of model outputs"},
        "metric_implementation_sha256": {"plain": sha256_file(ROOT/"metrics.py"),
            "structured": sha256_file(ROOT/"structured_metrics.py"),
            "bbox_contracts": sha256_file(ROOT/"provenance/bbox-coordinate-contracts.json")},
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "command": {"python_argv": _recorded_argv(), "sandbox_wrapper": "sh SightVault/qa/ocr-v2/run_ocr_benchmark.sh",
            "sandbox_profile": "(version 1)(allow default)(deny network-outbound)(allow network-outbound (remote ip \\\"localhost:*\\\"))"},
        "throughput_plan": {"requested_calls": args.throughput_count if args.task == "plain" else len(cases),
            "unique_corpus_images": len(cases), "repeat_calls_after_unique_corpus": max(0, args.throughput_count-len(cases)) if args.task == "plain" else 0,
            "repetition_order": "cycle from first image in deterministic category-interleaved order after all unique images" if args.task == "plain" else "each eligible unique fixture once",
            "image_order": [x["id"] for x in cases]},
        "model_artifacts": {"local_dir": _repo_relative(model_dir) + " (external to the Git worktree)",
            "disk_bytes_included_files": model_artifacts,
            "file_count_included": artifact_file_count},
        "offline": {
            "isolation_method": "macOS sandbox-exec outbound deny with loopback allowed plus in-process socket/DNS guard; not a firewall",
            "mac_sandbox_profile": os.environ.get("PHASE2_OCR_SANDBOX_PROFILE"),
            "blocked_external_attempts": blocked, "offline_flags": {k: os.environ.get(k) for k in (
                "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "USE_HUB_KERNELS", "PYTORCH_ENABLE_MPS_FALLBACK")}},
        "memory_pressure_start": _read_mem_pressure(), "timing_seconds": {}, "memory": {},
        "prompt_policy": {"plain_is_shared": args.task == "plain", "capability_prompts_model_specific": args.task != "plain",
            "model_markup_is_inert": args.task in ("table", "chart", "document_parse")},
        "cases": [], "quality": None, "failure": None,
    }

    backend = Backend(args.model, args.task, model_dir)
    sampler = None
    load_started = time.perf_counter()
    run_started = time.perf_counter()
    first_call_started = None
    milestone_counts = (10, 50, 100) if args.task == "plain" else ()
    milestones = {}
    try:
        # Start process RSS sampling before importing/loading the selected runtime.
        try:
            import psutil
            sampler = MemorySampler(psutil, args.model, None)
            if args.model == "ovis":
                import torch
                sampler.runtime = torch
            elif args.model == "minicpm":
                import mlx.core as mx
                sampler.runtime = mx
            sampler.start()
        except Exception as exc:
            run["memory_sampler_error"] = f"{type(exc).__name__}: {exc}"
        # Runtime imports happen inside load; timer includes model/model-processor loading.
        backend.load()
        if sampler:
            sampler.runtime = backend.runtime if args.model != "rapid" else None
        run["timing_seconds"]["model_load"] = round(time.perf_counter() - load_started, 3)
        run["runtime"].update(backend.engine_details)
        runtime_versions = []
        if backend.engine_details.get("transformers"):
            runtime_versions.extend((f"transformers={backend.engine_details['transformers']}",
                f"torch={backend.engine_details.get('torch', 'unknown')}",
                f"accelerate={backend.engine_details.get('accelerate', 'unknown')}"))
        elif backend.engine_details.get("mlx_vlm"):
            runtime_versions.extend((f"mlx-vlm={backend.engine_details['mlx_vlm']}",
                f"mlx={backend.engine_details.get('mlx', 'unknown')}"))
        elif backend.engine_details.get("rapidocr_onnxruntime"):
            runtime_versions.extend((f"rapidocr-onnxruntime={backend.engine_details['rapidocr_onnxruntime']}",
                f"onnxruntime={backend.engine_details.get('onnxruntime', 'unknown')}"))
        run["runtime"].update({"name": runtime_name, "version": "; ".join(runtime_versions) or "loaded; version details recorded below",
            "device": backend.engine_details.get("device", "unknown")})
        if args.model == "ovis":
            source_config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
            verification = json.loads((ROOT/"provenance/ovisocr3-download-manifest.json").read_text(encoding="utf-8"))
            run["runtime"]["configured_dtype"] = source_config.get("text_config", {}).get("dtype")
            run["model_artifacts"].update({"weight_file": "model.safetensors",
                "weight_bytes": (model_dir/"model.safetensors").stat().st_size,
                "weight_sha256": verification["weights"]["sha256"],
                "weight_verification_manifest": _repo_relative(ROOT/"provenance/ovisocr3-download-manifest.json"),
                "quantization": "none"})
        elif args.model == "minicpm":
            source_meta = json.loads((model_dir / "source.json").read_text(encoding="utf-8"))
            run["model_artifacts"].update({"source": source_meta,
                "weight_file": "model.safetensors", "weight_bytes": (model_dir/"model.safetensors").stat().st_size,
                "weight_sha256": source_meta.get("files", {}).get("model.safetensors", {}).get("sha256"),
                "quantization": "affine 4-bit group64; mixed BF16 layers per pinned MLX-VLM converter"})
        else:
            import importlib.metadata
            provenance = json.loads((ROOT/"provenance/official-source-manifest.json").read_text(encoding="utf-8"))
            rapid_meta = provenance["models"]["rapidocr_legacy"]
            pypi_record = json.loads((ROOT/"provenance/rapidocr-onnxruntime-1.4.4/pypi-release.json").read_text(encoding="utf-8"))
            wheel = next((item for item in pypi_record["urls"] if item["filename"].endswith(".whl")), None)
            run["model_artifacts"].update({"package": "rapidocr-onnxruntime",
                "package_version": importlib.metadata.version("rapidocr-onnxruntime"),
                "wheel": ({"filename": wheel["filename"], "bytes": wheel["size"],
                    "sha256": wheel["digests"]["sha256"]} if wheel else None),
                "onnx_model_assets": rapid_meta["model_assets"]})
            import numpy, cv2
            run["runtime"].update({"numpy": numpy.__version__, "opencv": cv2.__version__})
        _atomic_json(output, run)
        import PIL
        run["runtime"]["pillow"] = PIL.__version__
        call_count = args.throughput_count if args.task == "plain" else len(cases)
        for index in range(call_count):
            case = cases[index % len(cases)]
            repeated = index >= len(cases)
            prompt_text, prompt_meta = _task_prompt(args.model, args.task, case)
            if prompt_meta.get("skip_reason"):
                run["cases"].append({"index": index, "image_id": case["id"], "status": "skipped",
                    "reason": prompt_meta["skip_reason"]})
                continue
            if prompt_meta.get("unsupported_reason"):
                run["cases"].append({"index": index, "image_id": case["id"], "status": "unsupported",
                    "reason": prompt_meta["unsupported_reason"]})
                continue
            image_path = ROOT / case["image"]["path"]
            total_started = time.perf_counter()
            if first_call_started is None:
                first_call_started = total_started
            raw_bytes = image_path.read_bytes()
            if sha256_bytes(raw_bytes) != case["image"]["sha256"]:
                raise RuntimeError(f"Fixture hash changed on disk for {case['id']}")
            image = _normalized_image(image_path)
            preprocessed_at = time.perf_counter()
            try:
                prediction = backend.predict(image, prompt_text, max_new_tokens=args.max_new_tokens)
            except Exception as exc:
                run["cases"].append({"index": index, "image_id": case["id"], "repeat": repeated,
                    "status": "failed", "failure": {"type": type(exc).__name__, "message": str(exc),
                        "traceback": traceback.format_exc()}})
                raise
            ended = time.perf_counter()
            if "first_request" not in run["timing_seconds"]:
                run["timing_seconds"]["first_request"] = round(ended - total_started, 3)
            attempt = {
                "index": index, "image_id": case["id"], "category": case["category"], "repeat": repeated,
                "ground_truth_id": case["id"], "image_path": str(image_path.relative_to(ROOT)),
                "image_sha256": sha256_bytes(raw_bytes), "image_bytes": len(raw_bytes),
                "image_dimensions": [image.width, image.height], "alpha_policy": "RGBA composited onto white",
                "prompt": {**prompt_meta, "sha256": sha256_bytes((prompt_text or "").encode("utf-8")), "text": prompt_text},
                "timing_seconds": {"preprocess": round(preprocessed_at-total_started, 4),
                    "inference": round(ended-preprocessed_at, 4), "image_total": round(ended-total_started, 4)},
                "prediction": json_safe(prediction), "bbox_coordinate_contract": prompt_meta.get("bbox_coordinate_contract"),
                "metrics": None, "quality_eligible": False, "status": "completed",
            }
            run["cases"].append(attempt)
            if args.task == "plain" and index + 1 in milestone_counts:
                unique_count = min(index + 1, len(cases))
                repeat_count = max(0, index + 1 - len(cases))
                completed = [x for x in run["cases"] if x.get("status") == "completed"]
                pipeline_elapsed = sum(x["timing_seconds"]["image_total"] for x in completed)
                inference_elapsed = sum(x["timing_seconds"]["inference"] for x in completed)
                stream_wall = ended - first_call_started
                milestones[str(index + 1)] = {
                    "measured_calls": index + 1, "unique_images": unique_count, "repeat_calls": repeat_count,
                    "image_pipeline_seconds_excluding_quality_scoring_and_output_writes": round(pipeline_elapsed, 3),
                    "inference_seconds_sum": round(inference_elapsed, 3),
                    "pipeline_images_per_second_excluding_quality_scoring_and_output_writes": round(len(completed) / max(pipeline_elapsed, 1e-9), 5),
                    "inference_calls_per_second": round(len(completed) / max(inference_elapsed, 1e-9), 5),
                    "continuous_stream_wall_seconds_excluding_quality_scoring_including_output_writes": round(stream_wall, 3),
                    "continuous_stream_images_per_second_excluding_quality_scoring": round(len(completed) / max(stream_wall, 1e-9), 5),
                    "process_elapsed_including_harness_overhead_seconds": round(ended - run_started, 3),
                    "unique_image_ids": list(dict.fromkeys(x["image_id"] for x in run["cases"] if x.get("status") == "completed")),
                }
            run["timing_seconds"]["throughput_milestones"] = milestones
            if sampler:
                run["memory"] = sampler.snapshot()
            _refresh_schema_record(run)
            _atomic_json(output, run)
            print(json.dumps({"index": index+1, "total": call_count, "image_id": case["id"],
                "repeat": repeated, "inference_seconds": attempt["timing_seconds"]["inference"],
                "milestone": milestones.get(str(index+1)), "output": _repo_relative(output)}, ensure_ascii=False), flush=True)

        run["status"] = "completed"
        run["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        run["timing_seconds"]["model_load_seconds"] = run["timing_seconds"].get("model_load")
        warm = [x["timing_seconds"]["image_total"] for x in run["cases"]
                if x.get("status") == "completed" and x.get("index", 0) > 0]
        if warm:
            ordered = sorted(warm)
            run["timing_seconds"]["warm_image_total_p50_seconds"] = round(statistics.median(warm), 4)
            run["timing_seconds"]["warm_image_total_p95_seconds"] = round(ordered[max(0, math.ceil(0.95*len(ordered))-1)], 4)
    except Exception as exc:
        run["status"] = "failed"
        run["failure"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
        run["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    except KeyboardInterrupt:
        run["status"] = "interrupted"
        run["failure"] = {"type": "KeyboardInterrupt", "message": "Interrupted by coordinator; partial raw outputs preserved."}
        run["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    finally:
        if sampler:
            run["memory"] = sampler.close()
        run["memory_pressure_end"] = _read_mem_pressure()
        run["offline"]["blocked_external_attempts"] = blocked
        run["offline"]["verified"] = (not blocked and
            run["offline"]["mac_sandbox_profile"] == "macOS sandbox-exec outbound deny with loopback allowed" and
            all(run["offline"]["offline_flags"].get(k) == v for k, v in (
                ("HF_HUB_OFFLINE", "1"), ("TRANSFORMERS_OFFLINE", "1"),
                ("HF_HUB_DISABLE_TELEMETRY", "1"), ("USE_HUB_KERNELS", "NO"),
                ("PYTORCH_ENABLE_MPS_FALLBACK", "0"))))
        run["runtime"]["offline_verified"] = run["offline"]["verified"]
        run["portability"]["mac_measured"] = any(row.get("status") in ("completed", "failed") for row in run["cases"])
        run["portability"]["windows_measured"] = False
        if run["portability"]["mac_measured"]:
            run["portability"]["notes"] = "This benchmark was executed on macOS Apple Silicon; Windows was not measured on hardware."
        backend.release()
        run["runtime_released"] = True
        scoring_started = time.perf_counter()
        scoring_cpu_started = time.process_time()
        seen_for_quality = set()
        cases_by_id = {case["id"]: case for case in load_cases()}
        for attempt in run["cases"]:
            if attempt.get("status") != "completed":
                continue
            image_id = attempt.get("image_id")
            if image_id in seen_for_quality:
                continue
            seen_for_quality.add(image_id)
            attempt["metrics"] = _eval_attempt(args.task, cases_by_id[image_id],
                attempt.get("prediction", {}), args.model, attempt.get("bbox_coordinate_contract"))
            attempt["quality_eligible"] = True
        if run["cases"]:
            run["quality"] = _quality_summary(args.task, run["cases"])
        run["timing_seconds"]["quality_scoring_wall_seconds_after_model_release"] = round(time.perf_counter()-scoring_started, 4)
        run["timing_seconds"]["quality_scoring_cpu_seconds_after_model_release"] = round(time.process_time()-scoring_cpu_started, 4)
        run["timing_seconds"]["wall_total_including_load_scoring_and_output"] = round(time.perf_counter()-run_started, 3)
        _refresh_schema_record(run)
        uninstall_guard()
        _atomic_json(output, run)
        print(json.dumps({"status": run["status"], "output": _repo_relative(output),
            "timing_seconds": run["timing_seconds"], "memory": run["memory"],
            "quality": run["quality"], "offline": run["offline"], "failure": run["failure"]}, ensure_ascii=False, indent=2), flush=True)
    return 0 if run["status"] == "completed" and run["offline"]["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
