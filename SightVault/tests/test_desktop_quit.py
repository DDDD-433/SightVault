"""Native-window shutdown must clean up before the OS terminates the process."""
import os
from types import SimpleNamespace

from sightvault.desktop import instance
from sightvault.desktop.shell import WindowShell


class FakeController:
    def __init__(self, busy=None):
        self._busy = busy

    def busy_message(self):
        return self._busy


class FakeWindow:
    def __init__(self, confirm=True, events=None):
        self.confirm = confirm
        self.events = events if events is not None else []
        self.asked = []

    def create_confirmation_dialog(self, title, message):
        self.asked.append((title, message))
        return self.confirm

    def load_url(self, url):
        self.events.append(('load_url', url))

    def destroy(self):
        self.events.append(('destroy', None))


def _cleanup_factory():
    # Importing main applies its offline defaults for a launched process. Keep this
    # unit-test import from changing the test runner's environment.
    names = ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY')
    before = {name: os.environ.get(name) for name in names}
    try:
        from sightvault.desktop.main import _make_cleanup
    finally:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return _make_cleanup


def test_busy_quit_cancel_does_not_run_cleanup():
    calls = []
    shell = WindowShell(FakeController('A setup task is still running.'), server=None,
                        before_close=lambda: calls.append('cleanup'))
    shell.window = FakeWindow(confirm=False)

    assert shell.on_closing() is False
    assert shell.window.asked == [('Quit SightVault?', 'A setup task is still running.')]
    assert calls == []


def test_idle_accepted_native_close_runs_cleanup_before_returning_true():
    calls = []
    shell = WindowShell(FakeController(), server=None,
                        before_close=lambda: calls.append('cleanup'))
    shell.window = FakeWindow()

    assert shell.on_closing() is True
    assert calls == ['cleanup']
    assert shell.window.asked == []


def test_ui_smoke_acknowledgement_cleans_before_destroying_window(tmp_path):
    events = []

    class ReadyEvent:
        def __init__(self):
            self.was_set = False

        def set(self):
            self.was_set = True

        def wait(self, timeout):
            return True

    class ReadyServer:
        def wait_ready(self, timeout, probe=None):
            return True

    controller = FakeController()
    controller.config = SimpleNamespace(root=tmp_path)
    controller.ready = ReadyEvent()
    controller.window_ready = ReadyEvent()
    controller.url = 'http://127.0.0.1:54321'
    controller.window_renderer = None
    controller.start_url = lambda: controller.url + '/desktop/setup'

    def before_close():
        events.append(('cleanup', None))

    shell = WindowShell(controller, ReadyServer(), ui_smoke=True, before_close=before_close)
    shell.window = FakeWindow(events=events)
    shell._after_start()

    assert shell.smoke_result['ready'] is True
    assert controller.ready.was_set
    assert events == [('load_url', controller.url + '/desktop/setup'),
                      ('cleanup', None), ('destroy', None)]


def test_shared_cleanup_is_idempotent_and_preserves_a_newer_record(tmp_path, caplog):
    owned_token = '12' * 32
    current_token = '34' * 32
    instance_file = tmp_path / 'instance.json'
    instance.write_record(instance_file, port=54321, token=owned_token)

    class Controller:
        calls = 0

        def shutdown(self):
            self.calls += 1
            raise RuntimeError('cleanup step failure')

    class Server:
        calls = 0

        def stop(self):
            self.calls += 1
            return True

    class Lock:
        calls = 0

        def release(self):
            self.calls += 1

    controller, server, lock = Controller(), Server(), Lock()
    runtime = {'controller': controller, 'server': server, 'token': current_token}
    cleanup = _cleanup_factory()(runtime, instance_file, lock)

    cleanup()  # native Cocoa close callback
    cleanup()  # main() finally after webview returns, if the OS allows it

    assert controller.calls == server.calls == lock.calls == 1
    assert instance.read_record(instance_file)['token'] == owned_token
    assert 'Desktop cleanup failed while stopping the controller' in caplog.text


def test_shared_cleanup_removes_its_own_instance_record(tmp_path):
    token = '56' * 32
    instance_file = tmp_path / 'instance.json'
    instance.write_record(instance_file, port=54322, token=token)

    class Lock:
        def release(self):
            pass

    cleanup = _cleanup_factory()({'controller': None, 'server': None, 'token': token},
                                 instance_file, Lock())
    cleanup()

    assert not instance_file.exists()
