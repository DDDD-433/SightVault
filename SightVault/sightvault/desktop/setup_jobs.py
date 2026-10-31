"""Explicit, user-started setup tasks run in a separate process.

Model downloads and conversions are the only network-capable operations. They
run in a child process with the Hugging Face offline flags switched off for that
child only; the app process keeps them on, so normal inference stays offline.
The runner only ever stops the child process it started itself.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
import uuid

from .paths import is_frozen

ACTIONS = {
    'download': 'Download required models',
    'verify': 'Verify model checksums',
    'convert-mlx': 'Prepare Apple Silicon MLX checkpoints',
}
MLX_CONVERSIONS = ('minicpm-ocr', 'minicpm-v')


def mlx_supported(system=None, machine=None):
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    return system == 'Darwin' and machine in ('arm64', 'aarch64')


def worker_command(action, model_root, *, frozen=None, executable=None):
    """argv that re-enters this app (or the source checkout) in setup-worker mode."""
    if action not in ACTIONS:
        raise ValueError('Unknown setup action: ' + str(action))
    frozen = is_frozen() if frozen is None else frozen
    executable = executable or sys.executable
    prefix = [executable] if frozen else [executable, '-m', 'sightvault.desktop']
    return prefix + ['--setup-worker', action, '--model-root', str(model_root)]


def worker_environment(base=None):
    env = dict(os.environ if base is None else base)
    # Only the explicit setup child may reach the network.
    env.update(HF_HUB_OFFLINE='0', TRANSFORMERS_OFFLINE='0', HF_HUB_DISABLE_TELEMETRY='1',
               PYTHONUNBUFFERED='1', SIGHTVAULT_SETUP_WORKER='1')
    return env


def _expected_model_files(manifest, model_root):
    """Yield (group, path, size) for every pinned model file listed in the manifest."""
    for item in manifest.get('files', []):
        parts = Path(item['path']).parts
        if len(parts) < 3 or parts[0] != '.models':
            continue
        relative = Path(*parts[1:])
        path = (Path(model_root) / relative)
        yield parts[1], path, item['size']


def model_inventory(manifest, model_root):
    """Cheap presence check (existence and size only). Checksums need the verify task."""
    groups = {}
    for name, source in manifest.get('sources', {}).items():
        optional = bool(source.get('optional', False)) or source.get('required') is False
        groups[name] = {'name': name, 'repo': source.get('repo'), 'revision': source.get('revision'),
                        'required': not optional, 'platforms': source.get('platforms'),
                        'files': 0, 'present': 0, 'bytes': 0, 'present_bytes': 0}
    for name, path, size in _expected_model_files(manifest, model_root):
        group = groups.get(name)
        if group is None:
            continue
        group['files'] += 1
        group['bytes'] += size
        try:
            if path.is_file() and path.stat().st_size == size:
                group['present'] += 1
                group['present_bytes'] += size
        except OSError:
            pass
    for group in groups.values():
        group['complete'] = group['files'] > 0 and group['present'] == group['files']
    return list(groups.values())


def mlx_inventory(model_root):
    rows = []
    for name in MLX_CONVERSIONS:
        folder = Path(model_root) / (name + '-mlx-4bit')
        rows.append({'name': name + '-mlx-4bit', 'source': name,
                     'present': (folder / 'config.json').is_file() and (folder / 'model.safetensors').is_file()})
    return rows


def required_models_present(manifest, model_root):
    return all(group['complete'] for group in model_inventory(manifest, model_root) if group['required'])


def _tail(path, lines=40, limit=64 * 1024):
    try:
        with open(path, 'rb') as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            text = stream.read().decode('utf-8', errors='replace')
    except OSError:
        return ''
    return '\n'.join(text.splitlines()[-lines:])


class SetupJobRunner:
    def __init__(self, paths, *, command_factory=worker_command, popen=subprocess.Popen, env=None):
        self.paths = paths
        self._command_factory = command_factory
        self._popen = popen
        self._env = env
        self._lock = threading.Lock()
        self._job = None

    def _running(self):
        return self._job is not None and self._job['process'].poll() is None

    @property
    def running(self):
        with self._lock:
            return self._running()

    def start(self, action):
        if action not in ACTIONS:
            raise ValueError('Unknown setup action.')
        if action == 'convert-mlx' and not mlx_supported():
            raise ValueError('MLX checkpoints are only used on Apple Silicon Macs.')
        if action in ('download', 'convert-mlx') and not self.paths.models_writable:
            raise ValueError(f'The model folder is not writable: {self.paths.models}')
        with self._lock:
            if self._running():
                raise RuntimeError('A setup task is already running.')
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            log_dir = self.paths.setup_log_dir
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f'{stamp}-{action}-{uuid.uuid4().hex[:8]}.log'
            command = self._command_factory(action, self.paths.models)
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            child_env = worker_environment(self._env)
            child_env['SIGHTVAULT_SETUP_LOG'] = str(log_path)
            child_env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
            with open(log_path, 'ab') as log:
                process = self._popen(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                      env=child_env, cwd=str(self.paths.resources),
                                      creationflags=flags, close_fds=True)
            self._job = {'action': action, 'label': ACTIONS[action], 'process': process, 'log': log_path,
                         'started_at': time.time(), 'finished_at': None, 'cancelled': False}
            return self._public(self._job)

    def _public(self, job):
        if job is None:
            return {'state': 'idle'}
        code = job['process'].poll()
        if code is None:
            state = 'running'
        elif job['cancelled']:
            state = 'cancelled'
        else:
            state = 'succeeded' if code == 0 else 'failed'
        if code is not None and job['finished_at'] is None:
            job['finished_at'] = time.time()
        return {'state': state, 'action': job['action'], 'label': job['label'], 'returncode': code,
                'log_path': str(job['log']), 'log_tail': _tail(job['log']),
                'started_at': job['started_at'], 'finished_at': job['finished_at']}

    def status(self):
        with self._lock:
            return self._public(self._job)

    def cancel(self, timeout=10.0):
        """Stop the running setup child started by this runner. Never touches other processes."""
        with self._lock:
            job = self._job
            if job is None or job['process'].poll() is not None:
                return self._public(job)
            job['cancelled'] = True
            process = job['process']
        process.terminate()
        try:
            process.wait(timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout)
        with self._lock:
            return self._public(job)

    def shutdown(self):
        if self.running:
            self.cancel()


def load_manifest(resources):
    return json.loads((Path(resources) / 'handoff' / 'MODEL_MANIFEST.json').read_text(encoding='utf-8'))
