"""Owned local llama.cpp server for the opt-in LFM3-Omni vision backend."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time
import uuid

MODEL_ID = 'LiquidAI/LFM3-Omni-2B-GGUF'
MODEL_REVISION = '3d29af2ad166f8299c2d820937e47dc0add20ffc'
RUNTIME_REPO = 'ggml-org/llama.cpp'
RUNTIME_COMMIT = 'e0f142d5ddc1461ce721644d92af33db23545105'
RUNTIME_COMMIT_SHORT = 'e0f142d5d'
RUNTIME_MANIFEST_NAME = 'sightvault-llama-runtime.json'
MODEL_RELATIVE_DIR = Path('lfm3-omni-2b-gguf')
MODEL_FILE = 'LFM3-Omni-2B-Q4_K_M.gguf'
PROJECTOR_FILE = 'mmproj-LFM3-Omni-2B-Q4_K_M.gguf'
MODEL_FILES = {
    MODEL_FILE: {
        'bytes': 1_099_630_080,
        'sha256': '9f8d30e9d4fd7a9349d586e077988229b3ca0a0e4ad730897563d1eccc9893e2',
    },
    PROJECTOR_FILE: {
        'bytes': 291_586_720,
        'sha256': '0a746c0794782a16d4456b230c2a0b5888a2d94a6677211edbed7d7a4177a39a',
    },
}
MAX_FINAL_TOKENS = 384


class OmniRuntimeError(RuntimeError):
    """An unavailable or failed local Omni runtime."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def offload_evidence_from_line(line: str) -> tuple[str, str] | None:
    """Parse source-backed llama.cpp layer-assignment messages for status evidence."""
    summary = re.search(
        r'\boffloaded\s+(\d+)\s*/\s*(\d+)\s+layers?\s+to\s+(Metal|GPU|CUDA|Vulkan)\b',
        str(line), re.IGNORECASE,
    )
    if summary:
        count, total, backend = summary.groups()
        if int(count) <= 0:
            return None
        evidence = f'offloaded {count}/{total} layers to {backend}'
        return _offload_label(count, total, backend), evidence

    # The pinned llama.cpp commit also logs this form from llama_model_base::load_tensors.
    # It is emitted when GPU offload is supported, before the N/N summary line.
    repeating = re.search(
        r'\boffloading\s+(\d+)\s+repeating\s+layers?\s+to\s+(Metal|GPU|CUDA|Vulkan)\b',
        str(line), re.IGNORECASE,
    )
    if repeating:
        count, backend = repeating.groups()
        if int(count) <= 0:
            return None
        evidence = f'offloading {count} repeating layers to {backend}'
        return _offload_label(count, 'repeating', backend), evidence
    return None


def _offload_label(count: str, total: str, backend: str) -> str:
    backend = backend.casefold()
    extent = f'{count}/{total} layers' if total != 'repeating' else f'{count} repeating layers'
    if backend == 'metal':
        return f'Apple Metal GPU ({extent} offloaded)'
    if backend == 'gpu':
        return f'GPU ({extent} offloaded)'
    return f'{backend.upper()} GPU ({extent} offloaded)'


def offload_device_from_log(lines) -> str | None:
    """Return a GPU label only when llama.cpp explicitly reports layer assignment."""
    for line in lines:
        evidence = offload_evidence_from_line(str(line))
        if evidence:
            return evidence[0]
    return None


class OmniVisionRuntime:
    """Manage only a child llama-server bound to 127.0.0.1.

    This object never downloads or compiles. It validates the local model,
    projector, runtime manifest and binary before starting an owned child.
    """

    def __init__(self, config):
        self.config = config
        self.model_dir = Path(config.models) / MODEL_RELATIVE_DIR
        self.model_path = self.model_dir / MODEL_FILE
        self.projector_path = self.model_dir / PROJECTOR_FILE
        if config.llama_server:
            binary = Path(config.llama_server).expanduser()
            self.binary = binary if binary.is_absolute() else Path(config.root) / binary
        else:
            suffix = '.exe' if os.name == 'nt' else ''
            self.binary = Path(config.root) / '.runtime' / 'llama.cpp' / 'build' / 'bin' / f'llama-server{suffix}'
        self.runtime_manifest_path = self.binary.parent / RUNTIME_MANIFEST_NAME
        self.alias = None
        self.port = None
        self._process = None
        self._client = None
        self._state_lock = threading.RLock()
        self._log_stop = threading.Event()
        self._log_thread = None
        self._log_lines = []
        self._projector_loaded = False
        self._device_label = None
        self._device_evidence = None
        self._runtime_version = None
        self._last_error = None

    @property
    def is_running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    @property
    def device_label(self) -> str | None:
        if not self.is_running:
            return None
        if self._device_label:
            return self._device_label
        if str(self.config.device).casefold() == 'cpu':
            return 'CPU (configured; --gpu-layers 0)'
        return 'unverified (no GPU offload evidence)'

    def _read_source(self):
        source_path = self.model_dir / 'source.json'
        try:
            source = json.loads(source_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if (source.get('repo') != MODEL_ID
                or source.get('revision') != MODEL_REVISION
                or source.get('runtime_commit') != RUNTIME_COMMIT
                or not isinstance(source.get('files'), dict)):
            return None
        return source

    def _read_runtime_manifest(self):
        try:
            manifest = json.loads(self.runtime_manifest_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if (manifest.get('repo') != RUNTIME_REPO
                or manifest.get('commit') != RUNTIME_COMMIT
                or not isinstance(manifest.get('binary_sha256'), str)
                or not isinstance(manifest.get('version'), str)):
            return None
        return manifest

    def status(self) -> dict:
        reason = None
        source = self._read_source()
        runtime = self._read_runtime_manifest()
        if source is None:
            reason = ('The pinned Omni model provenance is missing or mismatched. Run '
                      'python scripts/prepare_omni_vision.py --download while online.')
        else:
            for name, expected in MODEL_FILES.items():
                path = self.model_dir / name
                record = source.get('files', {}).get(name, {})
                if not path.is_file():
                    reason = (f'Omni model file {name} is missing. Run '
                              'python scripts/prepare_omni_vision.py --download while online.')
                    break
                if (path.stat().st_size != expected['bytes']
                        or record.get('bytes') != expected['bytes']
                        or record.get('sha256') != expected['sha256']):
                    reason = (f'Omni model file {name} does not match the pinned size/hash manifest. '
                              'Rerun the explicit online preparation command.')
                    break
        if reason is None and not self.binary.is_file():
            reason = (f'Pinned llama-server binary is missing at {self.binary}. Build upstream '
                      f'llama.cpp at {RUNTIME_COMMIT} and run scripts/prepare_omni_runtime.py.')
        if reason is None and not self.runtime_manifest_path.is_file():
            reason = (f'Runtime artifact manifest is missing at {self.runtime_manifest_path}. '
                      'Run scripts/prepare_omni_runtime.py after building the pinned runtime.')
        if reason is None and runtime is None:
            reason = ('llama-server artifact manifest does not pin the required upstream commit '
                      f'{RUNTIME_COMMIT}; rebuild and verify the runtime.')
        if reason is None and os.name != 'nt' and not os.access(self.binary, os.X_OK):
            reason = f'llama-server is not executable: {self.binary}'
        return {
            'available': reason is None,
            'loaded': self.is_running,
            'name': 'LFM3-Omni 2B',
            'model_id': MODEL_ID,
            'model_revision': MODEL_REVISION,
            'model_quantization': 'Q4_K_M',
            'projector_id': PROJECTOR_FILE,
            'projector_quantization': 'Q4_K_M',
            'runtime': 'upstream llama.cpp',
            'runtime_commit': RUNTIME_COMMIT,
            'runtime_version': self._runtime_version or (runtime or {}).get('version'),
            'precision': 'GGUF Q4_K_M with matching Q4_K_M vision projector',
            'backend': 'omni',
            'availability_reason': reason,
            'device': self.device_label,
            'device_evidence': (
                self._device_evidence
                or ('SIGHTVAULT_DEVICE=cpu; owned server command uses --gpu-layers 0'
                    if self.is_running and str(self.config.device).casefold() == 'cpu'
                    else 'no layer-offload line observed in the owned server startup log; execution device unverified'
                    if self.is_running
                    else 'owned llama-server is not loaded; execution device unverified')
            ),
            'reasoning_mode': 'off',
            'thinking_token_budget': 0,
            'error': self._last_error,
            'model_path': str(self.model_path),
            'projector_path': str(self.projector_path),
        }

    def _verify_artifacts(self) -> dict:
        state = self.status()
        if not state['available']:
            raise OmniRuntimeError(state['availability_reason'])
        source = self._read_source()
        runtime = self._read_runtime_manifest()
        for name, expected in MODEL_FILES.items():
            path = self.model_dir / name
            if sha256_file(path) != expected['sha256']:
                raise OmniRuntimeError(f'Pinned Omni checksum mismatch for {name}; rerun the explicit preparation command.')
        if runtime.get('binary_sha256') != sha256_file(self.binary):
            raise OmniRuntimeError('llama-server binary changed since its pinned local runtime manifest was written.')
        try:
            version = subprocess.run([str(self.binary), '--version'], cwd=str(self.config.root),
                                     capture_output=True, text=True, timeout=10, check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise OmniRuntimeError(f'Could not verify the pinned llama-server binary: {exc}') from exc
        actual_version = (version.stdout + version.stderr).strip()
        if (actual_version != runtime.get('version')
                or f'commit {RUNTIME_COMMIT_SHORT}' not in actual_version):
            raise OmniRuntimeError('llama-server version output does not match the pinned runtime manifest.')
        self._runtime_version = actual_version
        return {'source': source, 'runtime': runtime}

    def _new_port(self) -> int:
        # The kernel selects a free loopback port. The owned child must still
        # bind it; /props identity is checked before any image request is sent.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(('127.0.0.1', 0))
            return int(probe.getsockname()[1])

    def _server_command(self, alias: str, port: int) -> list[str]:
        gpu_layers = '0' if str(self.config.device).casefold() == 'cpu' else '99'
        return [
            str(self.binary), '-m', str(self.model_path), '--mmproj', str(self.projector_path),
            '--alias', alias, '--host', '127.0.0.1', '--port', str(port),
            '--no-webui', '--parallel', '1', '--ctx-size', '4096', '--predict', str(MAX_FINAL_TOKENS),
            '--threads', '6', '--gpu-layers', gpu_layers, '--image-min-tokens', '1024',
            '--temp', '0', '--seed', '1', '--repeat-penalty', '1.0', '--no-warmup',
            '--reasoning', 'off', '--offline',
        ]

    def _drain_logs(self, process):
        stream = process.stdout
        if stream is None:
            return
        for raw_line in stream:
            line = raw_line.rstrip('\r\n')
            if self._log_stop.is_set() and process.poll() is not None:
                break
            if 'loaded multimodal model' in line and str(self.projector_path) in line:
                self._projector_loaded = True
            offload = offload_evidence_from_line(line)
            if offload:
                self._device_label, self._device_evidence = offload
            if any(marker in line.casefold() for marker in ('error', 'failed', 'cannot', 'unable')):
                self._log_lines.append(line[:400])
                self._log_lines = self._log_lines[-20:]

    def _wait_ready(self, process, client, alias: str, port: int, timeout_s: float = 240):
        deadline = time.monotonic() + timeout_s
        base = f'http://127.0.0.1:{port}'
        while time.monotonic() < deadline:
            if process.poll() is not None:
                detail = '; '.join(self._log_lines[-4:])
                raise OmniRuntimeError(f'llama-server exited during startup ({process.returncode}). {detail}')
            try:
                response = client.get(base + '/health', timeout=2)
                if response.status_code == 200:
                    break
            except Exception:
                time.sleep(0.2)
        else:
            raise OmniRuntimeError('Pinned llama-server did not become healthy within 240 seconds.')

        try:
            response = client.get(base + '/props', params={'model': alias}, timeout=5)
            response.raise_for_status()
            props = response.json()
        except Exception as exc:
            raise OmniRuntimeError(f'Could not verify llama-server model identity from /props: {exc}') from exc
        reported = props.get('model_path')
        if not isinstance(reported, str) or Path(reported).resolve() != self.model_path.resolve():
            raise OmniRuntimeError('llama-server /props model path does not match the requested pinned Omni GGUF.')
        if props.get('model_alias') != alias:
            raise OmniRuntimeError('llama-server /props alias does not match this owned process.')
        if not props.get('modalities', {}).get('vision'):
            raise OmniRuntimeError('llama-server /props does not report vision capability for the selected model.')
        projector_deadline = time.monotonic() + 10
        while not self._projector_loaded and time.monotonic() < projector_deadline:
            if process.poll() is not None:
                raise OmniRuntimeError('llama-server exited before the requested projector load was confirmed.')
            time.sleep(0.05)
        if not self._projector_loaded:
            raise OmniRuntimeError('llama-server log did not confirm loading the pinned vision projector path.')

    def _start(self):
        with self._state_lock:
            if self.is_running:
                return
            self._verify_artifacts()
            import httpx

            port = self._new_port()
            alias = f'sightvault-omni-{os.getpid()}-{uuid.uuid4().hex[:8]}'
            env = os.environ.copy()
            for key in tuple(env):
                normalized = key.casefold()
                if (any(marker in normalized for marker in ('api_key', 'token', 'secret', 'password', 'credential', 'authorization'))
                        or normalized in ('http_proxy', 'https_proxy', 'all_proxy', 'no_proxy')):
                    env.pop(key, None)
            env.update({
                'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                'HF_HUB_DISABLE_TELEMETRY': '1', 'LLAMA_ARG_OFFLINE': '1',
                'TOKENIZERS_PARALLELISM': 'false',
            })
            command = self._server_command(alias, port)
            try:
                process = subprocess.Popen(
                    command, cwd=str(self.config.root), env=env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1,
                )
            except OSError as exc:
                raise OmniRuntimeError(f'Could not start the configured llama-server binary: {exc}') from exc
            self._process = process
            self.port, self.alias = port, alias
            self._device_label = None
            self._device_evidence = None
            self._projector_loaded = False
            self._log_lines = []
            self._log_stop = threading.Event()
            self._log_thread = threading.Thread(target=self._drain_logs, args=(process,),
                                                name='sightvault-llama-log', daemon=True)
            self._log_thread.start()
            client = httpx.Client(
                trust_env=False,
                timeout=httpx.Timeout(connect=3, read=180, write=30, pool=5),
            )
            self._client = client
            try:
                self._wait_ready(process, client, alias, port)
            except Exception:
                self._last_error = 'llama-server startup or model identity verification failed.'
                self._stop_locked()
                raise

    def _stop_locked(self):
        process, client = self._process, self._client
        self._process = None
        self._client = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        self._log_stop.set()
        if process is not None and process.stdout is not None:
            try:
                process.stdout.close()
            except OSError:
                pass
        if self._log_thread is not None:
            self._log_thread.join(timeout=2)
        self._log_thread = None
        self.port = self.alias = None
        self._projector_loaded = False
        self._device_label = None
        self._device_evidence = None

    def close(self):
        with self._state_lock:
            self._stop_locked()

    def analyze(self, image, prompt: str, max_tokens: int = MAX_FINAL_TOKENS) -> str:
        if not prompt:
            raise OmniRuntimeError('The Omni prompt is empty.')
        if max_tokens < 1 or max_tokens > MAX_FINAL_TOKENS:
            raise OmniRuntimeError(f'Omni final answer cap must be between 1 and {MAX_FINAL_TOKENS} tokens.')
        self._start()
        try:
            with io.BytesIO() as buffer:
                image.convert('RGB').save(buffer, format='PNG')
                image_data = base64.b64encode(buffer.getvalue()).decode('ascii')
            payload = {
                'model': self.alias,
                'messages': [
                    {'role': 'system', 'content': 'Respond in text.'},
                    {'role': 'user', 'content': [
                        {'type': 'text', 'text': prompt},
                        {'type': 'image_url', 'image_url': {
                            'url': 'data:image/png;base64,' + image_data,
                        }},
                    ]},
                ],
                'max_tokens': min(int(max_tokens), MAX_FINAL_TOKENS),
                'temperature': 0,
                'seed': 1,
                'repeat_penalty': 1.0,
                'stream': False,
                'chat_template_kwargs': {'enable_thinking': False},
            }
            response = self._client.post(
                f'http://127.0.0.1:{self.port}/v1/chat/completions',
                json=payload, timeout=180,
            )
            response.raise_for_status()
            body = response.json()
            answer = body['choices'][0]['message']['content']
            if isinstance(answer, list):
                answer = ''.join(part.get('text', '') for part in answer if isinstance(part, dict))
            if not isinstance(answer, str) or not answer.strip():
                raise OmniRuntimeError('The pinned Omni runtime returned an empty text answer.')
            self._last_error = None
            return answer.strip()
        except Exception as exc:
            self._last_error = f'{type(exc).__name__}: {str(exc)[:240]}'
            self.close()
            if isinstance(exc, OmniRuntimeError):
                raise
            raise OmniRuntimeError(f'Local Omni analysis failed: {type(exc).__name__}: {str(exc)[:240]}') from exc
