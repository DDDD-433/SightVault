"""State shared by the native window, desktop routes and lifecycle code."""
from importlib.metadata import PackageNotFoundError, version
import json
import logging
import os
from pathlib import Path
import platform
import sys
import threading
from urllib.parse import urlsplit
import webbrowser

from . import APP_NAME, APP_VERSION
from . import demo
from .paths import is_frozen
from .setup_jobs import load_manifest, mlx_inventory, mlx_supported, model_inventory, required_models_present

log = logging.getLogger(__name__)
# External pages the desktop UI may open in the system browser. Everything else is refused.
EXTERNAL_HOSTS = frozenset({'huggingface.co', 'github.com'})
WEB = Path(__file__).resolve().parent / 'web'


def safe_external_url(url):
    try:
        parts = urlsplit(url)
        port = parts.port
    except (TypeError, ValueError) as exc:
        raise ValueError('Invalid link.') from exc
    if parts.scheme != 'https' or parts.hostname not in EXTERNAL_HOSTS or parts.username or parts.password \
            or port not in (None, 443) or any(ord(c) < 33 for c in url):
        raise ValueError('Only https links to ' + ', '.join(sorted(EXTERNAL_HOSTS)) + ' can be opened.')
    return url


def same_origin(url, origin):
    try:
        a, b = urlsplit(url or ''), urlsplit(origin or '')
        return (a.scheme, a.hostname, a.port) == (b.scheme, b.hostname, b.port) and a.scheme == 'http'
    except ValueError:
        return False


def _distribution(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return None


class DesktopController:
    def __init__(self, paths, config, app, token, runner, *, browser_open=None):
        self.paths = paths
        self.config = config
        self.app = app
        self.token = token
        self.runner = runner
        self.url = None
        self.window = None
        self.ready = threading.Event()
        self.window_ready = threading.Event()
        self.window_renderer = None
        self._browser_open = browser_open or (lambda url: webbrowser.open(url, new=2))
        self._accelerators = None
        self._accelerator_lock = threading.Lock()

    # Window integration -------------------------------------------------
    def attach_window(self, window):
        self.window = window

    def focus(self):
        window = self.window
        if window is None:
            return
        for method in ('restore', 'show'):
            action = getattr(window, method, None)
            if callable(action):
                try:
                    action()
                except Exception:
                    log.exception('Window %s failed', method)

    def choose_folder(self):
        window = self.window
        if window is None:
            raise RuntimeError('The desktop window is not available.')
        import webview
        dialog = getattr(webview, 'FileDialog', None)
        kind = dialog.FOLDER if dialog is not None else webview.FOLDER_DIALOG
        result = window.create_file_dialog(kind, directory=str(Path.home()))
        if not result:
            return None
        chosen = result[0] if isinstance(result, (list, tuple)) else result
        return str(Path(chosen))

    def open_external(self, url):
        self._browser_open(safe_external_url(url))

    def start_url(self):
        """First-run setup page until the required pinned models are present."""
        try:
            present = required_models_present(load_manifest(self.paths.resources), self.paths.models)
        except (OSError, ValueError, KeyError):
            present = False
        return self.url + ('/' if present else '/desktop/setup')

    def inject_script(self):
        source = (WEB / 'inject.js').read_text(encoding='utf-8')
        return source.strip().rstrip(';') + '(' + json.dumps({'token': self.token, 'version': APP_VERSION}) + ');'

    # Data -----------------------------------------------------------------
    def accelerators(self):
        """Backends PyTorch reports as available. This is not proof that a model runs there."""
        with self._accelerator_lock:
            if self._accelerators is None:
                info = {'torch': _distribution('torch'), 'note': 'Availability is not proof that a model runs on that device; '
                        'the model status shows the device each model actually loaded on.'}
                try:
                    import torch
                    info.update(cuda_available=bool(torch.cuda.is_available()),
                                mps_built=bool(torch.backends.mps.is_built()),
                                mps_available=bool(torch.backends.mps.is_available()))
                except Exception as exc:
                    info['error'] = str(exc)[:300]
                self._accelerators = info
            return self._accelerators

    def diagnostics(self):
        manifest = load_manifest(self.paths.resources)
        models = getattr(self.app.state, 'models', None)
        db = getattr(self.app.state, 'db', None)
        try:
            runtime_status = models.status() if models is not None else {}
        except Exception as exc:
            runtime_status = {'error': str(exc)[:300]}
        from .offline_guard import snapshot
        inventory = model_inventory(manifest, self.paths.models)
        return {
            'app': {'name': APP_NAME, 'version': APP_VERSION, 'frozen': is_frozen(),
                    'python': sys.version.split()[0], 'platform': platform.platform(),
                    'machine': platform.machine(), 'executable': sys.executable},
            'paths': {**self.paths.as_dict(), 'log_file': str(self.paths.log_file),
                      'setup_logs': str(self.paths.setup_log_dir)},
            'server': {'url': self.url, 'loopback_only': True},
            'window': {'attached': self.window is not None, 'bridge_ready': self.window_ready.is_set(),
                       'renderer': self.window_renderer},
            'offline': {**{name: os.environ.get(name) for name in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE')},
                        'socket_guard': snapshot()},
            'device_setting': self.config.device,
            'vision_backend': self.config.vision,
            'packages': {name: _distribution(name) for name in
                         ('torch', 'transformers', 'sentence-transformers', 'mlx', 'mlx-vlm', 'onnxruntime',
                          'rapidocr-onnxruntime', 'fastapi', 'uvicorn', 'pywebview')},
            'accelerators': self.accelerators(),
            'models': inventory,
            'required_models_present': all(g['complete'] for g in inventory if g['required']),
            'mlx': {'supported': mlx_supported(), 'conversions': mlx_inventory(self.paths.models)},
            'runtime_status': runtime_status,
            'demo': {'bundled': demo.bundled_demo(self.paths.resources) is not None,
                     'library_assets': db.stats()['total'] if db is not None else None},
            'setup_job': self.runner.status(),
        }

    def import_demo(self):
        if self.runner.running:
            raise ValueError('Wait for the running setup task to finish first.')
        result = demo.import_demo(self.paths.resources, self.config.data, self.config.db_path)
        self.config.demo_folder = Path(result['folder'])
        return result

    # Lifecycle ------------------------------------------------------------
    def busy_message(self):
        if self.runner.running:
            return 'A setup task is still running. Quitting stops it; you can start it again later.'
        return None

    def shutdown(self):
        try:
            self.runner.shutdown()
        except Exception:
            log.exception('Stopping the setup task failed')
