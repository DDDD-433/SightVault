"""Stdlib-only tests for the frozen desktop QA socket guard.

Each scenario runs in a child Python process so monkeypatching socket APIs never
changes the test runner or any neighboring test modules.
"""

import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _worker(name):
    sys.path.insert(0, str(ROOT))
    import errno
    import socket
    import tempfile

    from sightvault.desktop import offline_guard

    offline_guard.install()
    if name == 'loopback':
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(('127.0.0.1', 0))
        listener.listen(4)
        port = listener.getsockname()[1]
        for host in ('127.0.0.1', b'127.0.0.1', 'localhost'):
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.settimeout(2)
            client.connect((host, port))
            server, _ = listener.accept()
            client.close()
            server.close()
        listener.close()
        if hasattr(socket, 'AF_INET6'):
            try:
                v6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
                v6.bind(('::1', 0))
                v6.listen(1)
                client = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
                client.settimeout(2)
                client.connect(('::1', v6.getsockname()[1]))
                server, _ = v6.accept()
                client.close()
                server.close()
                v6.close()
            except OSError as exc:
                if exc.errno not in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL, errno.EPROTONOSUPPORT):
                    raise
        if hasattr(socket, 'AF_UNIX'):
            with tempfile.TemporaryDirectory() as folder:
                path = str(Path(folder) / 'local.sock')
                unix_server = None
                try:
                    unix_server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    unix_server.bind(path)
                    unix_server.listen(1)
                except OSError as exc:
                    if unix_server is not None:
                        unix_server.close()
                    unsupported = {getattr(errno, name, None) for name in
                                   ('EAFNOSUPPORT', 'EPROTONOSUPPORT', 'ENOSYS', 'EOPNOTSUPP', 'EINVAL')}
                    if exc.errno not in unsupported:
                        raise
                else:
                    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    client.settimeout(2)
                    client.connect(path)
                    accepted, _ = unix_server.accept()
                    accepted.close()
                    client.close()
                    unix_server.close()
        return

    if name == 'blocked':
        original_connect = offline_guard._ORIGINALS['connect']
        original_getaddrinfo = offline_guard._ORIGINALS['getaddrinfo']
        original_gethostbyname = offline_guard._ORIGINALS['gethostbyname']
        original_sendto = offline_guard._ORIGINALS['sendto']
        calls = []
        offline_guard._ORIGINALS['connect'] = lambda *args: calls.append('connect')
        offline_guard._ORIGINALS['getaddrinfo'] = lambda *args, **kwargs: calls.append('getaddrinfo')
        offline_guard._ORIGINALS['gethostbyname'] = lambda *args: calls.append('gethostbyname')
        offline_guard._ORIGINALS['sendto'] = lambda *args, **kwargs: calls.append('sendto')
        try:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                client.connect(('203.0.113.7', 80))
            except PermissionError as exc:
                assert exc.errno == errno.EACCES
            else:
                raise AssertionError('external connect was not denied')
            assert client.connect_ex(('198.51.100.9', 80)) == errno.EACCES
            for host, operation in (
                ('example.invalid', socket.getaddrinfo),
                ('external.invalid', socket.gethostbyname),
            ):
                try:
                    operation(host, 443) if operation is socket.getaddrinfo else operation(host)
                except socket.gaierror:
                    pass
                else:
                    raise AssertionError('external DNS lookup was not denied')
            datagram = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                datagram.sendto(b'qa', ('203.0.113.10', 53))
            except PermissionError:
                pass
            else:
                raise AssertionError('external datagram was not denied')
            assert calls == [], calls
            # Even the explicitly allowed localhost name must be checked at the
            # connection boundary in case the system resolver returns an outside IP.
            offline_guard._ORIGINALS['getaddrinfo'] = lambda *args, **kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 0, '', ('203.0.113.99', 443))]
            try:
                socket.socket(socket.AF_INET, socket.SOCK_STREAM).connect(('localhost', 443))
            except PermissionError:
                pass
            else:
                raise AssertionError('localhost resolving outside loopback was not denied')
            assert calls == [], calls
            try:
                socket.getaddrinfo('localhost', 443)
            except socket.gaierror:
                pass
            else:
                raise AssertionError('localhost DNS result outside loopback was not denied')
        finally:
            offline_guard._ORIGINALS.update(connect=original_connect, getaddrinfo=original_getaddrinfo,
                                             gethostbyname=original_gethostbyname, sendto=original_sendto)
        return

    if name == 'probe':
        first = offline_guard.snapshot()
        assert first['installed'] is True
        count = first['blocked_attempts']
        one = offline_guard.probe()
        two = offline_guard.probe()
        assert one['probe_denied'] is True
        assert two['probe_denied'] is True
        assert one['blocked_attempts'] == count + 1
        assert two['blocked_attempts'] == count + 1
        assert offline_guard.install()['blocked_attempts'] == count + 1
        return

    if name == 'ordinary-errors':
        # A connection to an unused loopback port reaches the OS and keeps its
        # usual refusal error; the QA guard only changes external destinations.
        reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
        reservation.close()
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            client.connect(('127.0.0.1', port))
        except ConnectionRefusedError:
            pass
        else:
            raise AssertionError('expected the normal loopback connection-refused error')
        client.close()
        expected_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        expected_code = offline_guard._ORIGINALS['connect_ex'](expected_socket, ('127.0.0.1', port))
        expected_socket.close()
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        refusal_code = client.connect_ex(('127.0.0.1', port))
        refusal_codes = {errno.ECONNREFUSED, getattr(errno, 'WSAECONNREFUSED', errno.ECONNREFUSED), 10061}
        assert refusal_code in refusal_codes
        assert refusal_code == expected_code
        assert all(item[4][0] in ('127.0.0.1', '::1')
                   for item in socket.getaddrinfo('localhost', 80))
        return

    raise AssertionError('unknown test worker')


class DesktopOfflineGuardTests(unittest.TestCase):
    def run_isolated(self, scenario):
        env = dict(os.environ)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker', scenario],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    def test_loopback_and_unix_sockets_still_work(self):
        self.run_isolated('loopback')

    def test_external_connections_dns_and_datagrams_are_blocked_before_os_calls(self):
        self.run_isolated('blocked')

    def test_probe_is_denied_once_and_install_is_idempotent(self):
        self.run_isolated('probe')

    def test_ordinary_loopback_socket_errors_are_preserved(self):
        self.run_isolated('ordinary-errors')


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--worker':
        _worker(sys.argv[2])
    else:
        unittest.main()
