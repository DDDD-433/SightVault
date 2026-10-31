"""Desktop shell lifecycle, storage and path-safety tests. No GUI, network or model weights."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from sightvault.database import Database
from sightvault.desktop import demo as demo_import
from sightvault.desktop import instance, paths as desktop_paths
from sightvault.desktop.controller import DesktopController, safe_external_url, same_origin
from sightvault.desktop.loopback import LoopbackServer, probe_status
from sightvault.desktop.routes import install_desktop_routes
from sightvault.desktop.setup_jobs import (ACTIONS, SetupJobRunner, model_inventory, worker_command,
                                           worker_environment)
from sightvault.desktop.worker import conversion_failures
from sightvault.server import create_app
from test_core import env, record  # noqa: F401  (pytest fixture)

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'ab' * 32


def _paths(tmp_path, resources=ROOT, models_writable=True):
    base = tmp_path / 'home'
    built = desktop_paths.DesktopPaths(base=base, data=base / 'data', models=base / 'models', logs=base / 'logs',
                                       resources=Path(resources), models_writable=models_writable)
    for folder in (built.base, built.data, built.models, built.logs, built.setup_log_dir):
        folder.mkdir(parents=True, exist_ok=True)
    return built


# Storage locations ---------------------------------------------------------------------------

def test_default_locations_are_per_user_on_each_platform(tmp_path):
    home = tmp_path / 'user'
    base, logs = desktop_paths.default_locations('darwin', {}, home)
    assert base == home / 'Library' / 'Application Support' / 'SightVault'
    assert logs == home / 'Library' / 'Logs' / 'SightVault'
    local = tmp_path / 'LocalAppData'
    base, logs = desktop_paths.default_locations('win32', {'LOCALAPPDATA': str(local)}, home)
    assert base == local / 'SightVault' and logs == local / 'SightVault' / 'logs'
    base, _ = desktop_paths.default_locations('linux', {}, home)
    assert base == home / '.local' / 'share' / 'SightVault'


def test_resolve_paths_honours_overrides_and_keeps_folders_separate(tmp_path):
    home = tmp_path / 'user'
    home.mkdir()
    resolved = desktop_paths.resolve_paths({'SIGHTVAULT_HOME': str(tmp_path / 'sv')}, 'darwin', home, ROOT, frozen=False)
    assert resolved.base == Path(os.path.realpath(tmp_path / 'sv'))
    assert resolved.data == resolved.base / 'data' and resolved.models == resolved.base / 'models'
    assert resolved.logs == resolved.base / 'logs'
    weights = tmp_path / 'existing-weights'
    resolved = desktop_paths.resolve_paths({'SIGHTVAULT_MODELS': str(weights)}, 'darwin', home, ROOT, frozen=False)
    assert resolved.models == Path(os.path.realpath(weights))
    assert resolved.data.is_relative_to(Path(os.path.realpath(home)) / 'Library')


@pytest.mark.parametrize('value', ['relative/folder', '/'])
def test_storage_rejects_relative_and_root_folders(tmp_path, value):
    with pytest.raises(desktop_paths.UnsafePathError):
        desktop_paths.resolve_paths({'SIGHTVAULT_HOME': value}, 'darwin', tmp_path, ROOT, frozen=False)


def test_storage_rejects_home_folder_and_paths_inside_the_app_bundle(tmp_path):
    home = tmp_path / 'user'
    home.mkdir()
    with pytest.raises(desktop_paths.UnsafePathError, match='home folder'):
        desktop_paths.resolve_paths({'SIGHTVAULT_DATA': str(home)}, 'darwin', home, ROOT, frozen=False)
    bundle = tmp_path / 'SightVault.app'
    resources = bundle / 'Contents' / 'Frameworks'
    resources.mkdir(parents=True)
    with pytest.raises(desktop_paths.UnsafePathError, match='bundle'):
        desktop_paths.resolve_paths({'SIGHTVAULT_HOME': str(bundle / 'Contents' / 'Resources' / 'data')},
                                    'darwin', home, resources, frozen=True, bundle=bundle)
    # A symlink into the bundle is resolved before the check.
    link = tmp_path / 'innocent'
    link.symlink_to(bundle / 'Contents', target_is_directory=True)
    with pytest.raises(desktop_paths.UnsafePathError, match='bundle'):
        desktop_paths.resolve_paths({'SIGHTVAULT_HOME': str(link / 'home')}, 'darwin', home, resources,
                                    frozen=True, bundle=bundle)


def test_storage_rejects_a_file_in_place_of_a_folder(tmp_path):
    target = tmp_path / 'not-a-folder'
    target.write_text('x')
    with pytest.raises(desktop_paths.UnsafePathError, match='not a folder'):
        desktop_paths.resolve_paths({'SIGHTVAULT_HOME': str(target)}, 'darwin', tmp_path / 'u', ROOT, frozen=False)


def test_prepare_creates_folders_and_reports_read_only_models(tmp_path):
    models = tmp_path / 'shared-models'
    models.mkdir()
    resolved = desktop_paths.resolve_paths({'SIGHTVAULT_HOME': str(tmp_path / 'sv'), 'SIGHTVAULT_MODELS': str(models)},
                                           'darwin', tmp_path / 'u', ROOT, frozen=False)
    if os.name != 'nt' and os.geteuid() != 0:
        models.chmod(0o500)
    try:
        prepared = desktop_paths.prepare(resolved)
    finally:
        models.chmod(0o700)
    assert prepared.data.is_dir() and prepared.logs.is_dir() and prepared.setup_log_dir.is_dir()
    if os.name != 'nt' and os.geteuid() != 0:
        assert prepared.models_writable is False


@pytest.mark.skipif(os.name == 'nt' or (hasattr(os, 'geteuid') and os.geteuid() == 0), reason='POSIX permissions')
def test_prepare_refuses_an_unwritable_data_folder(tmp_path):
    resolved = desktop_paths.resolve_paths({'SIGHTVAULT_HOME': str(tmp_path / 'sv')}, 'darwin', tmp_path / 'u', ROOT,
                                           frozen=False)
    resolved.data.mkdir(parents=True)
    resolved.data.chmod(0o500)
    try:
        with pytest.raises(desktop_paths.UnsafePathError, match='cannot write'):
            desktop_paths.prepare(resolved)
    finally:
        resolved.data.chmod(0o700)


# Single instance ----------------------------------------------------------------------------

def test_instance_lock_is_exclusive_and_released(tmp_path):
    lock_path = tmp_path / 'instance.lock'
    first, second = instance.InstanceLock(lock_path), instance.InstanceLock(lock_path)
    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()


def test_instance_record_is_owner_only_loopback_and_validated(tmp_path):
    path = tmp_path / 'instance.json'
    instance.write_record(path, port=54321, token=TOKEN, pid=123)
    if os.name != 'nt':
        assert (path.stat().st_mode & 0o777) == 0o600
    assert instance.read_record(path) == {'pid': 123, 'url': 'http://127.0.0.1:54321', 'token': TOKEN}
    for bad in ({'pid': 1, 'url': 'http://example.com:80', 'token': TOKEN},
                {'pid': 1, 'url': 'http://127.0.0.1:80', 'token': 'short'},
                {'pid': 1, 'url': 'http://user:pw@127.0.0.1:80', 'token': TOKEN},
                {'pid': '1', 'url': 'http://127.0.0.1:80', 'token': TOKEN}):
        path.write_text(json.dumps(bad))
        assert instance.read_record(path) is None
    instance.write_record(path, port=54321, token=TOKEN)
    instance.remove_record_if_owned(path, 'cd' * 32)
    assert path.exists(), 'another launch must not delete a record it does not own'
    instance.remove_record_if_owned(path, TOKEN)
    assert not path.exists()


def test_second_launch_never_sends_its_token_off_loopback(monkeypatch):
    monkeypatch.setattr(instance, 'loopback_opener', lambda: pytest.fail('must not open a connection'))
    assert not instance.notify_existing({'pid': 1, 'url': 'http://10.0.0.5:80', 'token': TOKEN})
    assert not instance.notify_existing(None)


# Loopback server and desktop routes -----------------------------------------------------------

class FakeRunner:
    running = False

    def __init__(self):
        self.started = []

    def status(self):
        return {'state': 'idle'}

    def cancel(self):
        return {'state': 'idle'}

    def start(self, action):
        if action not in ACTIONS:
            raise ValueError('Unknown setup action.')
        self.started.append(action)
        return {'state': 'running', 'action': action}

    def shutdown(self):
        pass


def _controller(tmp_path, env, opened=None):
    config, _, models = env
    app = create_app(config, models)
    controller = DesktopController(_paths(tmp_path), config, app, TOKEN, FakeRunner(),
                                   browser_open=(opened.append if opened is not None else None))
    controller.accelerators = lambda: {'note': 'not checked in unit tests'}
    install_desktop_routes(app, controller)
    return app, controller


def test_desktop_routes_require_the_launch_token(tmp_path, env, monkeypatch):
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    app, _ = _controller(tmp_path, env)
    with TestClient(app) as client:
        assert client.get('/desktop/api/diagnostics').status_code == 403
        assert client.get('/desktop/api/diagnostics', headers={instance.TOKEN_HEADER: 'cd' * 32}).status_code == 403
        assert client.post('/desktop/api/setup/download').status_code == 403
        response = client.get('/desktop/api/diagnostics', headers={instance.TOKEN_HEADER: TOKEN})
        assert response.status_code == 200
        assert response.json()['required_models_present'] is False
        assert response.json()['offline']['HF_HUB_OFFLINE'] == '1'


def test_desktop_routes_keep_the_cross_origin_guard_and_csp(tmp_path, env):
    app, controller = _controller(tmp_path, env)
    headers = {instance.TOKEN_HEADER: TOKEN, 'Origin': 'http://evil.example'}
    with TestClient(app) as client:
        assert client.post('/desktop/api/setup/download', headers=headers).status_code == 403
        assert controller.runner.started == []
        page = client.get('/desktop/setup')
        assert page.status_code == 200
        assert "script-src 'self'" in page.headers['content-security-policy']
        assert client.get('/desktop/static/setup.js').status_code == 200


def test_external_links_are_https_allow_listed(tmp_path, env):
    opened = []
    app, _ = _controller(tmp_path, env, opened)
    headers = {instance.TOKEN_HEADER: TOKEN}
    with TestClient(app) as client:
        for url in ('http://huggingface.co/x', 'https://evil.example/', 'https://huggingface.co.evil.example/',
                    'javascript:alert(1)', 'file:///etc/passwd', 'https://user@github.com/', 'https://github.com:8443/'):
            assert client.post('/desktop/api/open-external', json={'url': url}, headers=headers).status_code == 422
        assert client.post('/desktop/api/open-external', json={'url': 'https://huggingface.co/google/embeddinggemma-2'},
                           headers=headers).status_code == 200
    assert opened == ['https://huggingface.co/google/embeddinggemma-2']


def test_setup_actions_are_validated(tmp_path, env):
    app, controller = _controller(tmp_path, env)
    headers = {instance.TOKEN_HEADER: TOKEN}
    with TestClient(app) as client:
        assert client.post('/desktop/api/setup/rm-rf', headers=headers).status_code == 422
        assert client.post('/desktop/api/setup/verify', headers=headers).status_code == 200
    assert controller.runner.started == ['verify']


def test_navigation_and_start_page_helpers(tmp_path, env):
    _, controller = _controller(tmp_path, env)
    controller.url = 'http://127.0.0.1:50000'
    assert same_origin('http://127.0.0.1:50000/desktop/setup', controller.url)
    assert not same_origin('http://127.0.0.1:50001/', controller.url)
    assert not same_origin('https://127.0.0.1:50000/', controller.url)
    assert not same_origin('about:blank', controller.url)
    assert controller.start_url() == 'http://127.0.0.1:50000/desktop/setup'
    script = controller.inject_script()
    assert script.startswith('(function') and script.endswith(');') and json.dumps(TOKEN) in script
    assert safe_external_url('https://github.com/DDDD-433/SightVault') == 'https://github.com/DDDD-433/SightVault'


def test_loopback_server_starts_on_free_loopback_port_and_stops(tmp_path, env):
    app, controller = _controller(tmp_path, env)
    config = env[0]
    server = LoopbackServer(app)
    assert server.socket.getsockname()[0] == '127.0.0.1' and server.port > 0
    controller.url = server.url
    server.start()
    try:
        assert server.wait_ready(30, probe=lambda url: probe_status(url, config.root))
        assert not probe_status(server.url, tmp_path / 'other-instance')
        record = {'pid': os.getpid(), 'url': server.url, 'token': TOKEN}
        assert instance.notify_existing(record)
        assert not instance.notify_existing({**record, 'token': 'cd' * 32})
    finally:
        assert server.stop(10)
    assert not server.running
    assert not probe_status(server.url, timeout=0.5)


# Setup child process -------------------------------------------------------------------------

def test_worker_command_and_environment_keep_the_app_offline(tmp_path, monkeypatch):
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    source = worker_command('download', tmp_path / 'models', frozen=False, executable='/py')
    assert source == ['/py', '-m', 'sightvault.desktop', '--setup-worker', 'download', '--model-root', str(tmp_path / 'models')]
    assert worker_command('verify', tmp_path, frozen=True, executable='/App')[:2] == ['/App', '--setup-worker']
    with pytest.raises(ValueError):
        worker_command('shell', tmp_path)
    child = worker_environment()
    assert child['HF_HUB_OFFLINE'] == '0' and child['TRANSFORMERS_OFFLINE'] == '0'
    assert os.environ['HF_HUB_OFFLINE'] == '1'


def _runner(tmp_path, script):
    def command(action, model_root):
        return [sys.executable, '-c', script]
    return SetupJobRunner(_paths(tmp_path, resources=tmp_path), command_factory=command)


def _wait(runner, timeout=20):
    deadline = time.monotonic() + timeout
    while runner.running and time.monotonic() < deadline:
        time.sleep(0.05)
    return runner.status()


def test_setup_runner_reports_child_result_and_log(tmp_path):
    runner = _runner(tmp_path, 'import os; print("offline", os.environ["HF_HUB_OFFLINE"])')
    runner.start('verify')
    status = _wait(runner)
    assert status['state'] == 'succeeded' and status['returncode'] == 0
    assert 'offline 0' in status['log_tail']
    failing = _runner(tmp_path / 'b', 'raise SystemExit(3)')
    failing.start('verify')
    assert _wait(failing)['state'] == 'failed'


def test_setup_runner_allows_one_task_and_cancels_only_its_child(tmp_path):
    runner = _runner(tmp_path, 'import time; time.sleep(60)')
    bystander = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        runner.start('verify')
        with pytest.raises(RuntimeError):
            runner.start('verify')
        status = runner.cancel()
        assert status['state'] == 'cancelled'
        assert bystander.poll() is None, 'an unrelated process must keep running'
    finally:
        bystander.kill()
        bystander.wait()


def test_setup_runner_refuses_downloads_into_a_read_only_model_folder(tmp_path):
    runner = SetupJobRunner(_paths(tmp_path, models_writable=False),
                            command_factory=lambda *a: pytest.fail('must not start'))
    with pytest.raises(ValueError, match='not writable'):
        runner.start('download')


def test_model_inventory_checks_presence_and_size_only(tmp_path):
    manifest = {'sources': {'a': {'repo': 'org/a', 'revision': '1' * 40}, 'b': {'repo': 'org/b', 'revision': '2' * 40,
                                                                             'optional': True}},
                'files': [{'path': '.models/a/model.safetensors', 'size': 3, 'sha256': '0' * 64},
                          {'path': '.models/a/config.json', 'size': 2, 'sha256': '0' * 64},
                          {'path': '.models/b/model.gguf', 'size': 5, 'sha256': '0' * 64}]}
    (tmp_path / 'a').mkdir()
    (tmp_path / 'a' / 'model.safetensors').write_bytes(b'abc')
    (tmp_path / 'a' / 'config.json').write_bytes(b'toolong')
    groups = {g['name']: g for g in model_inventory(manifest, tmp_path)}
    assert groups['a']['present'] == 1 and groups['a']['files'] == 2 and not groups['a']['complete']
    assert groups['a']['required'] and not groups['b']['required']


def test_mlx_conversion_check_uses_reviewed_fingerprints(tmp_path):
    folder = tmp_path / 'minicpm-ocr-mlx-4bit'
    folder.mkdir()
    (folder / 'config.json').write_bytes(b'{}')
    digest = hashlib.sha256(b'{}').hexdigest()
    manifest = {'files': [{'path': '.models/minicpm-ocr-mlx-4bit/config.json', 'size': 2, 'sha256': digest}]}
    assert conversion_failures(manifest, folder, 'minicpm-ocr') == []
    (folder / 'config.json').write_bytes(b'[]')
    assert conversion_failures(manifest, folder, 'minicpm-ocr')[0]['error'] == 'SHA-256 differs'
    assert conversion_failures(manifest, folder, 'minicpm-v')[0]['error'] == 'no reviewed fingerprints recorded'


def test_pinned_downloader_requires_an_absolute_model_root():
    from handoff import download_pinned_models
    with pytest.raises(SystemExit):
        download_pinned_models.main(['--model-root', 'relative/models'])


# Demo import ---------------------------------------------------------------------------------

def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _demo_bundle(tmp_path):
    """A two-image stand-in for the reviewed public demo with matching manifests."""
    resources = tmp_path / 'resources'
    (resources / 'samples').mkdir(parents=True)
    (resources / 'handoff').mkdir()
    demo = resources / 'demo'
    (demo / 'images').mkdir(parents=True)
    (demo / 'thumbs').mkdir()
    samples, entries = [], []
    db = Database(demo / 'sightvault.sqlite3')
    for index, color in enumerate(('red', 'blue')):
        name = f'sample_{index}.png'
        Image.new('RGB', (40, 30), color).save(resources / 'samples' / name)
        digest = _sha(resources / 'samples' / name)
        mtime_ns = 1792002840000000000 + index * 86400 * 10 ** 9
        samples.append({'path': f'samples/{name}', 'sha256': digest, 'mtime_ns': mtime_ns,
                        'size': (resources / 'samples' / name).stat().st_size})
        (demo / 'images' / f'{digest}.png').write_bytes((resources / 'samples' / name).read_bytes())
        Image.new('RGB', (20, 15), color).save(demo / 'thumbs' / f'{digest}.webp')
        entries.append({'image': {'path': f'data/images/{digest}.png', 'sha256': digest},
                        'thumbnail': {'path': f'data/thumbs/{digest}.webp', 'sha256': _sha(demo / 'thumbs' / f'{digest}.webp')}})
        row = record(index, text=f'{color} text ERESOLVE{index}')
        row.update(filepath=f'C:/old/samples/{name}', filename=name, folder='C:/old/samples', sha256=digest,
                   image_path=f'{digest}.png', thumbnail_path=f'{digest}.webp')
        db.upsert(row)
    samples.append({'path': 'samples/photo_sources.json', 'sha256': '0' * 64, 'mtime_ns': 0, 'size': 0})
    (resources / 'samples' / 'photo_sources.json').write_text('')
    samples[-1]['sha256'] = _sha(resources / 'samples' / 'photo_sources.json')
    (resources / 'handoff' / 'SAMPLE_MANIFEST.json').write_text(json.dumps(samples))
    (resources / 'handoff' / 'DEMO_EVIDENCE_MANIFEST.json').write_text(json.dumps({'entries': entries}))
    return resources


def test_demo_import_copies_verified_evidence_and_rebases_without_reindexing(tmp_path):
    resources = _demo_bundle(tmp_path)
    data = tmp_path / 'user-data'
    library = Database(data / 'sightvault.sqlite3')
    result = demo_import.import_demo(resources, data, library.path, expected_count=2)
    assert result['imported'] == 2
    rows = sorted(library.all(), key=lambda row: row['filename'])
    folder = data / 'demo-samples'
    assert [row['filepath'] for row in rows] == [str(folder / 'sample_0.png'), str(folder / 'sample_1.png')]
    assert all(row['folder'] == str(folder) and row['embedding'] for row in rows)
    assert rows[0]['timestamp'] == '2026-10-20T15:00:00+00:00', 'indexed timestamps are preserved'
    assert (folder / 'sample_1.png').stat().st_mtime_ns == 1792002840000000000 + 86400 * 10 ** 9
    assert (data / 'images' / rows[0]['image_path']).is_file() and (data / 'thumbs' / rows[0]['thumbnail_path']).is_file()
    assert library.fts(['ERESOLVE1']), 'full-text index is rebuilt by the insert triggers'
    with pytest.raises(demo_import.DemoImportError, match='empty library'):
        demo_import.import_demo(resources, data, library.path, expected_count=2)
    assert library.stats()['total'] == 2


def test_demo_import_never_overwrites_a_differing_file_or_a_library(tmp_path):
    resources = _demo_bundle(tmp_path)
    data = tmp_path / 'user-data'
    library = Database(data / 'sightvault.sqlite3')
    plan = demo_import.plan_import(resources, expected_count=2)
    (subdir, name), _ = sorted(plan['evidence'].items())[0]
    (data / subdir).mkdir(parents=True, exist_ok=True)
    (data / subdir / name).write_bytes(b'user file')
    with pytest.raises(demo_import.DemoImportError, match='different file'):
        demo_import.import_demo(resources, data, library.path, expected_count=2)
    assert (data / subdir / name).read_bytes() == b'user file'
    assert library.stats()['total'] == 0


def test_demo_import_rejects_tampered_samples_and_wrong_counts(tmp_path):
    resources = _demo_bundle(tmp_path)
    with pytest.raises(demo_import.DemoImportError, match='Expected 62'):
        demo_import.plan_import(resources)
    Image.new('RGB', (40, 30), 'green').save(resources / 'samples' / 'sample_0.png')
    with pytest.raises(demo_import.DemoImportError, match='missing or changed'):
        demo_import.plan_import(resources, expected_count=2)


def test_demo_import_rejects_traversal_in_stored_evidence_names(tmp_path):
    resources = _demo_bundle(tmp_path)
    db = sqlite3.connect(resources / 'demo' / 'sightvault.sqlite3')
    db.execute("UPDATE assets SET image_path='../../escape.png' WHERE id=1")
    db.commit()
    db.close()
    with pytest.raises(demo_import.DemoImportError, match='Unsafe'):
        demo_import.plan_import(resources, expected_count=2)


class FakeWindow:
    def __init__(self, urls, confirm=True):
        self.urls = list(urls)
        self.loaded, self.html, self.scripts = [], [], []
        self.confirm = confirm
        self.asked = []

    def get_current_url(self):
        return self.urls.pop(0) if self.urls else None

    def load_url(self, url):
        self.loaded.append(url)

    def load_html(self, content):
        self.html.append(content)

    def run_js(self, script):
        self.scripts.append(script)

    def create_confirmation_dialog(self, title, message):
        self.asked.append(message)
        return self.confirm


def _shell(tmp_path, env, urls, confirm=True):
    from sightvault.desktop.shell import WindowShell
    _, controller = _controller(tmp_path, env)
    controller.url = 'http://127.0.0.1:50000'
    controller.ready.set()
    shell = WindowShell(controller, server=None)
    shell.window = FakeWindow(urls, confirm)
    return shell, controller


def test_shell_injects_the_token_only_into_the_apps_own_origin(tmp_path, env):
    shell, _ = _shell(tmp_path, env, ['http://127.0.0.1:50000/desktop/setup', 'https://evil.example/'])
    shell.on_loaded()
    assert len(shell.window.scripts) == 1 and TOKEN in shell.window.scripts[0]
    shell.on_loaded()
    assert len(shell.window.scripts) == 1, 'no token for a foreign page'
    assert shell.window.loaded == ['http://127.0.0.1:50000/']


def test_shell_navigation_guard_cannot_loop(tmp_path, env):
    shell, _ = _shell(tmp_path, env, [None] + ['https://evil.example/'] * 10)
    shell.on_loaded()
    assert shell.window.loaded == [] and shell.window.scripts == []
    for _ in range(10):
        shell.on_loaded()
    assert len(shell.window.loaded) == 3 and len(shell.window.html) == 1
    assert shell.window.scripts == []


def test_shell_asks_before_quitting_during_a_setup_task(tmp_path, env):
    shell, controller = _shell(tmp_path, env, [], confirm=False)
    assert shell.on_closing() is True and shell.window.asked == []
    controller.runner.running = True
    assert shell.on_closing() is False and len(shell.window.asked) == 1


def test_frontend_is_unchanged_in_a_plain_browser(tmp_path, env):
    app, _ = _controller(tmp_path, env)
    with TestClient(app) as client:
        html = client.get('/').text
    assert 'desktop' not in html.lower(), 'desktop integration is injected only by the native shell'
