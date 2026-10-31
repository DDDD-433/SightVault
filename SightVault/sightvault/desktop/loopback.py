"""Serve the FastAPI app on a pre-bound 127.0.0.1 socket from a background thread.

Binding the socket before uvicorn starts avoids a race between choosing a free
port and listening on it. The server only ever listens on loopback.
"""
import json
import logging
import os
import socket
import threading
import time
import urllib.error

from .instance import loopback_opener, loopback_url

log = logging.getLogger(__name__)


def bind_loopback(port=0):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == 'nt':
            # Refuse to share the port with another process on Windows.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(('127.0.0.1', port))
        sock.listen(128)
    except OSError:
        sock.close()
        raise
    return sock


def probe_status(url, expected_root=None, timeout=2.0):
    """True when the URL serves SightVault's status API (optionally for this resource root)."""
    try:
        with loopback_opener().open(url.rstrip('/') + '/api/status', timeout=timeout) as response:
            if response.status != 200:
                return False
            status = json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return False
    if not isinstance(status, dict) or status.get('local_only') is not True:
        return False
    return expected_root is None or status.get('instance_root') == str(expected_root)


class LoopbackServer:
    def __init__(self, app, port=0, log_level='info'):
        import uvicorn
        self.socket = bind_loopback(port)
        self.port = self.socket.getsockname()[1]
        self.url = loopback_url(self.port)
        # log_config=None: route uvicorn records to the app's own file log. A windowed
        # frozen app may have no usable stderr for uvicorn's default handlers.
        config = uvicorn.Config(app, log_level=log_level, log_config=None, access_log=False,
                                lifespan='on', timeout_graceful_shutdown=5)
        self.server = uvicorn.Server(config)
        self.error = None
        self.thread = threading.Thread(target=self._run, name='sightvault-http', daemon=True)

    def _run(self):
        try:
            # uvicorn skips installing signal handlers when it is not on the main thread.
            self.server.run(sockets=[self.socket])
        except BaseException as exc:  # recorded for the startup report, never swallowed silently
            self.error = exc
            log.exception('Local server stopped with an error')

    def start(self):
        self.thread.start()
        return self

    def wait_ready(self, timeout=60.0, probe=None, interval=0.05):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.error is not None or not self.thread.is_alive():
                return False
            if self.server.started and (probe is None or probe(self.url)):
                return True
            time.sleep(interval)
        return False

    @property
    def running(self):
        return self.thread.is_alive()

    def stop(self, timeout=15.0):
        """Ask uvicorn to finish in-flight requests and run app shutdown. Returns True if stopped."""
        self.server.should_exit = True
        if self.thread.is_alive():
            self.thread.join(timeout)
        if self.thread.is_alive():
            log.warning('Local server did not stop within %.0f s; forcing exit', timeout)
            self.server.force_exit = True
            self.thread.join(5)
        try:
            self.socket.close()
        except OSError:
            pass
        return not self.thread.is_alive()
