"""One-image, explicit MPS smoke using the official Transformers architecture."""
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
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT.parents[1]
REPO = ROOT.parents[2]
MODEL_DIR = ROOT.parents[3] / "ocr-v2-models" / "ovisocr3"
DEFAULT_IMAGE = ROOT / "images" / "T05.png"
DEFAULT_OUTPUT = ROOT / "results" / "raw_outputs" / "ovisocr3-smoke.json"
COMMON_PROMPT_PATH = ROOT / "prompts" / "plain_ocr_shared.txt"


def sha256_bytes(data: bytes):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def shell_read(*argv):
    try:
        return subprocess.run(argv, check=False, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return "unavailable"


class MemorySampler:
    def __init__(self, psutil, torch, interval=0.05):
        self.psutil, self.torch, self.interval = psutil, torch, interval
        self.process = psutil.Process()
        self.peak_rss = 0
        self.peak_mps_current = 0
        self.peak_mps_driver = 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="ocr-memory-sampler", daemon=True)

    def _run(self):
        while not self.stop.is_set():
            try:
                self.peak_rss = max(self.peak_rss, self.process.memory_info().rss)
            except Exception:
                pass
            try:
                self.peak_mps_current = max(self.peak_mps_current, int(self.torch.mps.current_allocated_memory()))
                self.peak_mps_driver = max(self.peak_mps_driver, int(self.torch.mps.driver_allocated_memory()))
            except Exception:
                pass
            self.stop.wait(self.interval)

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=2)
        return {
            "peak_rss_bytes": self.peak_rss,
            "peak_mps_current_allocated_bytes": self.peak_mps_current,
            "peak_mps_driver_allocated_bytes": self.peak_mps_driver,
            "sample_interval_ms": int(self.interval * 1000),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", choices=("mps", "cpu"), default="mps")
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR)
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    result = {
        "experiment": "SightVault OCR_V2 OvisOCR3 smoke",
        "baseline_commit": shell_read("git", "-C", str(REPO), "rev-parse", "HEAD"),
        "script_sha256": sha256_file(Path(__file__)),
        "command": "sandbox-exec offline one-image inference via smoke_ovis.py --device mps; snapshot downloaded and checksum-verified before this call",
        "started_at_utc": started_at,
        "platform": {
            "os": shell_read("sw_vers", "-productVersion"), "arch": platform.machine(),
            "hardware": shell_read("sysctl", "-n", "machdep.cpu.brand_string"),
            "ram_bytes": int(shell_read("sysctl", "-n", "hw.memsize")) if shell_read("sysctl", "-n", "hw.memsize").isdigit() else None,
            "python": sys.version,
        },
        "model": {
            "repo": "ATH-MaaS/OvisOCR3", "revision": "4833d5d2afa6e769ee933f024504d67ace80a24c",
            "format": "Safetensors", "quantization": "none (official BF16 checkpoint; Transformers dtype=auto)",
            "license": "Apache-2.0", "remote_code": False,
        },
        "image": {"path": str(args.image), "sha256": None, "bytes": None, "ground_truth_id": "T05"},
        "runtime": {"name": "Transformers/PyTorch", "device_requested": args.device,
                    "offline_flags": {k: os.environ.get(k) for k in (
                        "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "USE_HUB_KERNELS",
                        "PYTORCH_ENABLE_MPS_FALLBACK")}},
        "timing_seconds": {}, "memory": {}, "offline": {
            "isolation_method": "macOS sandbox-exec outbound deny with loopback allowed, plus in-process socket/getaddrinfo guard; not a firewall",
            "mac_sandbox_profile": os.environ.get("PHASE2_OCR_SANDBOX_PROFILE"),
            "mac_sandbox_command": "/usr/bin/sandbox-exec -p '(version 1)(allow default)(deny network-outbound)(allow network-outbound (remote ip \"localhost:*\"))'",
        },
        "raw_output": None, "quality": None, "failure": None,
    }
    model = processor = inputs = outputs = image = None
    sampler = None
    attempts, uninstall_guard = [], lambda: None
    try:
        for key, expected in (("HF_HUB_OFFLINE", "1"), ("TRANSFORMERS_OFFLINE", "1"),
                              ("HF_HUB_DISABLE_TELEMETRY", "1"), ("USE_HUB_KERNELS", "NO")):
            os.environ[key] = expected
        if os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK", "0").lower() not in ("0", "false", "no"):
            raise RuntimeError("MPS operator fallback must remain disabled for this smoke")
        attempts, uninstall_guard = __import__("offline_guard").install_socket_guard()
        import psutil
        import torch
        from PIL import Image
        from transformers import AutoModelForMultimodalLM, AutoProcessor
        result["runtime"].update({
            "python": sys.version,
            "torch": torch.__version__,
            "transformers": __import__("transformers").__version__,
            "accelerate": __import__("accelerate").__version__,
            "mps_built": torch.backends.mps.is_built(),
            "mps_available": torch.backends.mps.is_available(),
        })
        if args.device == "mps" and not (torch.backends.mps.is_built() and torch.backends.mps.is_available()):
            raise RuntimeError("Explicit MPS request but torch MPS is not built and available")
        if not (args.model_dir / "model.safetensors").is_file():
            raise FileNotFoundError("OvisOCR3 weights are missing from the isolated model directory")
        if not args.image.is_file():
            raise FileNotFoundError(args.image)
        result["model"].update({
            "local_dir": os.path.relpath(args.model_dir, REPO) + " (external to the Git worktree)",
            "artifact_bytes": (args.model_dir / "model.safetensors").stat().st_size,
            "artifact_sha256": sha256_file(args.model_dir / "model.safetensors"),
        })
        image_bytes = args.image.read_bytes()
        result["image"].update({"bytes": len(image_bytes), "sha256": sha256_bytes(image_bytes)})
        with Image.open(args.image) as source:
            rgba = source.convert("RGBA")
            # Alpha normalization is fixed across all OCR adapters: composite on white.
            canvas = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            canvas.alpha_composite(rgba)
            image = canvas.convert("RGB")
        result["image"].update({"width": image.width, "height": image.height, "normalized_mode": image.mode,
                                "alpha_policy": "RGBA composited onto white"})
        prompt = COMMON_PROMPT_PATH.read_text(encoding="utf-8").strip()
        result["prompt"] = {"path": str(COMMON_PROMPT_PATH), "sha256": sha256_bytes(prompt.encode("utf-8")), "text": prompt}
        sampler = MemorySampler(psutil, torch)
        sampler.start()
        load_started = time.perf_counter()
        processor = AutoProcessor.from_pretrained(str(args.model_dir), local_files_only=True, trust_remote_code=False)
        model = AutoModelForMultimodalLM.from_pretrained(
            str(args.model_dir), local_files_only=True, trust_remote_code=False, dtype="auto"
        )
        model.to(args.device)
        model.eval()
        if args.device == "mps":
            torch.mps.synchronize()
        result["timing_seconds"]["cold_load"] = round(time.perf_counter() - load_started, 3)
        param_devices = sorted({str(param.device) for param in model.parameters()})
        param_dtypes = sorted({str(param.dtype) for param in model.parameters()})
        result["runtime"].update({"parameter_devices": param_devices, "parameter_dtypes": param_dtypes,
                                  "model_class": model.__class__.__name__})
        if args.device == "mps" and any(not device.startswith("mps") for device in param_devices):
            raise RuntimeError("Explicit MPS model placement failed; observed devices: " + repr(param_devices))
        messages = [{"role": "user", "content": [
            {"type": "image", "image": image}, {"type": "text", "text": prompt},
        ]}]
        request_started = time.perf_counter()
        inputs = processor.apply_chat_template(
            messages, tokenize=True, add_generation_prompt=True, return_dict=True,
            return_tensors="pt", enable_thinking=False,
        ).to(args.device)
        with torch.inference_mode():
            outputs = model.generate(**inputs, do_sample=False, max_new_tokens=768, use_cache=True)
        if args.device == "mps":
            torch.mps.synchronize()
        result["timing_seconds"]["first_request"] = round(time.perf_counter() - request_started, 3)
        text = processor.decode(outputs[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True).strip()
        result["raw_output"] = text
        from metrics import evaluate_plain
        gt = next(json.loads(line) for line in (ROOT / "ground_truth.jsonl").read_text(encoding="utf-8").splitlines()
                  if json.loads(line)["id"] == "T05")
        result["quality"] = evaluate_plain(gt, text)
        result["status"] = "completed"
    except Exception as exc:
        result["failure"] = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
        result["status"] = "failed"
    finally:
        if sampler:
            result["memory"] = sampler.close()
        result["offline"]["blocked_external_attempts"] = attempts
        result["offline"]["verified"] = not attempts and result["offline"].get("mac_sandbox_profile") == "macOS sandbox-exec outbound deny with loopback allowed" and all(
            result["runtime"].get("offline_flags", {}).get(k) == expected
            for k, expected in (("HF_HUB_OFFLINE", "1"), ("TRANSFORMERS_OFFLINE", "1"),
                                 ("HF_HUB_DISABLE_TELEMETRY", "1"), ("USE_HUB_KERNELS", "NO"))
        )
        result["timing_seconds"]["wall_total"] = round(time.perf_counter() - t0, 3)
        result["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        if model is not None:
            result["runtime"]["actual_parameter_devices_before_release"] = sorted(
                {str(param.device) for param in model.parameters()}
            )
        uninstall_guard()
        del outputs, inputs, model, processor, image
        gc.collect()
        try:
            if "torch" in locals() and torch.backends.mps.is_available():
                torch.mps.empty_cache()
        except Exception:
            pass
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({
            "status": result.get("status"), "output": str(args.output),
            "timing_seconds": result["timing_seconds"], "memory": result["memory"],
            "offline": result["offline"], "failure": result["failure"],
        }, indent=2), flush=True)
    return 0 if result.get("status") == "completed" and result["offline"]["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
