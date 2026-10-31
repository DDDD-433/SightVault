"""Desktop entry point: native window by default, plus headless modes for build QA.

  SightVault                    open the native window
  SightVault --serve [--port N] run the same server without a window until Ctrl-C (QA harnesses)
  SightVault --smoke            start, check the status API, stop; prints a JSON report
  SightVault --print-paths      print the per-user folders as JSON
  SightVault --setup-worker ... internal: explicit model download/verify/convert child process
"""
import argparse
import faulthandler
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading

# Inference must never reach the network. Only the explicit setup child (started with
# --setup-worker and an environment that turns these off) may download pinned models.
if sys.argv[1:2] != ['--setup-worker']:
    for _name in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY'):
        os.environ[_name] = '1'

log = logging.getLogger('sightvault.desktop')


def _ensure_stdio(paths=None):
    """A windowed frozen app may start without stdout/stderr; some libraries write to them."""
    target = None
    for name in ('stdout', 'stderr'):
        if getattr(sys, name) is None:
            if target is None:
                destination = os.environ.get('SIGHTVAULT_SETUP_LOG') or (paths.logs / 'stdio.log' if paths else os.devnull)
                target = open(destination, 'a', encoding='utf-8', buffering=1)
            setattr(sys, name, target)


def configure_logging(paths, verbose=False):
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handler = RotatingFileHandler(paths.log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    root.addHandler(handler)
    if verbose and sys.stderr is not None:
        root.addHandler(logging.StreamHandler(sys.stderr))
    logging.captureWarnings(True)
    crash_log = open(paths.logs / 'crash.log', 'a', encoding='utf-8')
    faulthandler.enable(crash_log)
    return crash_log


def parse_args(argv):
    parser = argparse.ArgumentParser(prog='SightVault', description='SightVault desktop app')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--serve', action='store_true', help='Run the loopback server without a window (QA)')
    mode.add_argument('--smoke', action='store_true', help='Start, check readiness, stop and print a JSON report')
    mode.add_argument('--ui-smoke', action='store_true', help='Exercise the real native webview and close it after its script acknowledges startup')
    mode.add_argument('--print-paths', action='store_true', help='Print the per-user data/model/log folders')
    parser.add_argument('--port', type=int, default=0, help='Loopback port for --serve or --smoke (default: any free port)')
    parser.add_argument('--report', type=Path, help='Write QA JSON here even in a windowed executable')
    parser.add_argument('--stop-file', type=Path, help='With --serve, stop gracefully when this owned profile marker is created')
    # macOS Finder may pass a -psn_ process serial number argument on older systems.
    args, unknown = parser.parse_known_args(argv)
    unexpected = [item for item in unknown if not item.startswith('-psn_')]
    if unexpected:
        parser.error('Unknown arguments: ' + ' '.join(unexpected))
    if not 0 <= args.port < 65536:
        parser.error('--port must be between 0 and 65535')
    if args.stop_file is not None and not args.serve:
        parser.error('--stop-file is only supported with --serve')
    return args


def emit_report(report, path=None):
    text = json.dumps(report, indent=2) + '\n'
    if path is not None:
        Path(path).write_text(text, encoding='utf-8')
    print(text, end='', flush=True)


def _make_cleanup(runtime, instance_file, lock):
    """Return one cleanup action shared by native close and Python finally."""
    cleanup_lock = threading.Lock()
    completed = False

    def cleanup():
        nonlocal completed
        with cleanup_lock:
            if completed:
                return
            completed = True

            def attempt(label, action):
                try:
                    action()
                except Exception:
                    log.exception('Desktop cleanup failed while %s', label)

            controller = runtime.get('controller')
            if controller is not None:
                attempt('stopping the controller', controller.shutdown)
            server = runtime.get('server')
            if server is not None:
                def stop_server():
                    if not server.stop():
                        log.warning('Local server thread did not stop cleanly')
                attempt('stopping the local server', stop_server)
            token = runtime.get('token')
            if token is not None:
                def remove_owned_record():
                    from .instance import remove_record_if_owned
                    remove_record_if_owned(instance_file, token)
                attempt('removing the owned instance record', remove_owned_record)
            attempt('releasing the single-instance lock', lock.release)

    return cleanup


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ['--setup-worker']:
        _ensure_stdio()
        from .worker import run
        return run(argv[1:])
    if os.environ.get('SIGHTVAULT_QA_OFFLINE_GUARD') == '1':
        from .offline_guard import install, probe
        install()
        probe()
    args = parse_args(argv)
    from .paths import UnsafePathError, prepare, resolve_paths
    stop_file = None
    try:
        paths = prepare(resolve_paths())
        if args.stop_file is not None:
            marker = args.stop_file.expanduser()
            if not marker.is_absolute() or not marker.resolve().is_relative_to(paths.base.resolve()):
                raise UnsafePathError('--stop-file must be an absolute path inside this profile.')
            if marker.exists() or marker.is_symlink():
                raise UnsafePathError('--stop-file already exists; use a fresh owned marker.')
            stop_file = marker.resolve()
    except (UnsafePathError, OSError) as exc:
        _ensure_stdio()
        print(f'SightVault cannot use its storage folder: {exc}', file=sys.stderr)
        return 2
    _ensure_stdio(paths)
    if args.print_paths:
        emit_report(paths.as_dict(), args.report)
        return 0
    configure_logging(paths, verbose=args.serve or args.smoke)
    from .instance import InstanceLock, new_token, notify_existing, read_record, write_record
    lock = InstanceLock(paths.lock_file)
    if not lock.acquire():
        headless = args.serve or args.smoke or args.ui_smoke
        if not headless and notify_existing(read_record(paths.instance_file)):
            log.info('SightVault is already running; asked it to show its window')
            return 0
        message = ('SightVault is already running for this user (or is still starting). '
                   'Wait a moment and try again. No running process was stopped.')
        log.warning(message)
        print(message, file=sys.stderr)
        return 1
    server = controller = token = None
    runtime = {'controller': None, 'server': None, 'token': None}
    cleanup = _make_cleanup(runtime, paths.instance_file, lock)
    try:
        os.environ.setdefault('SIGHTVAULT_DEVICE', 'auto')
        os.environ['SIGHTVAULT_DATA'] = str(paths.data)
        os.environ['SIGHTVAULT_MODELS'] = str(paths.models)
        from sightvault.config import Config
        from sightvault.server import create_app
        from .controller import DesktopController
        from .loopback import LoopbackServer, probe_status
        from .routes import install_desktop_routes
        from .setup_jobs import SetupJobRunner
        config = Config(root=paths.resources, data=paths.data, models=paths.models)
        demo_folder = paths.data / 'demo-samples'
        if demo_folder.is_dir():
            config.demo_folder = demo_folder
        app = create_app(config)
        token = runtime['token'] = new_token()
        controller = runtime['controller'] = DesktopController(paths, config, app, token, SetupJobRunner(paths))
        install_desktop_routes(app, controller)
        server = runtime['server'] = LoopbackServer(app, port=args.port if (args.serve or args.smoke) else 0)
        controller.url = server.url
        server.start()
        write_record(paths.instance_file, port=server.port, token=token)
        log.info('SightVault desktop %s serving %s (data %s, models %s)', 'headless' if args.serve or args.smoke
                 else 'window', server.url, paths.data, paths.models)
        if args.smoke:
            ready = server.wait_ready(90, probe=lambda url: probe_status(url, config.root))
            report = {'ready': ready, 'url': server.url, 'paths': paths.as_dict(),
                      'start_url': controller.start_url() if ready else None,
                      'error': str(server.error) if server.error else None}
            emit_report(report, args.report)
            return 0 if ready else 1
        if args.serve:
            if not server.wait_ready(90, probe=lambda url: probe_status(url, config.root)):
                print('SightVault server did not become ready; see ' + str(paths.log_file), file=sys.stderr)
                return 1
            controller.ready.set()
            emit_report({'ready': True, 'url': server.url}, args.report)
            stop = threading.Event()
            import signal
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            try:
                while server.running and not stop.wait(0.5):
                    if stop_file is not None and stop_file.exists():
                        break
            except KeyboardInterrupt:
                pass
            return 0
        from .shell import WindowShell
        shell = WindowShell(controller, server, ui_smoke=args.ui_smoke, before_close=cleanup)
        shell.run()
        if args.ui_smoke:
            emit_report(shell.smoke_result, args.report)
            return 0 if shell.smoke_result['ready'] else 1
        return 0
    except Exception as exc:
        log.exception('SightVault desktop failed')
        if args.ui_smoke:
            emit_report({'ready': False, 'error': str(exc)[:500]}, args.report)
            return 1
        raise
    finally:
        try:
            cleanup()
        finally:
            logging.shutdown()


if __name__ == '__main__':
    sys.exit(main())
