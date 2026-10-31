"""Single-instance lock and a loopback-only hand-off to an already open window.

The lock is an OS advisory lock held for the life of the process, so it is
released automatically if the process exits or crashes. A second launch never
terminates the first one; it only asks it to bring its window forward.
"""
import json
import os
from pathlib import Path
import re
import secrets
import urllib.error
import urllib.request
from urllib.parse import urlsplit

TOKEN_HEADER = 'X-SightVault-Desktop-Token'
_TOKEN = re.compile(r'^[0-9a-f]{64}$')


def new_token():
    return secrets.token_hex(32)


def loopback_opener():
    """URL opener that never routes through a configured HTTP proxy."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


class InstanceLock:
    def __init__(self, path):
        self.path = Path(path)
        self._handle = None

    @property
    def held(self):
        return self._handle is not None

    def acquire(self):
        """Take the lock without waiting. Returns False if another process holds it."""
        if self._handle is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, 'a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self):
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()


def loopback_url(port):
    if type(port) is not int or not 0 < port < 65536:
        raise ValueError('Invalid loopback port')
    return f'http://127.0.0.1:{port}'


def is_loopback_url(url):
    try:
        parts = urlsplit(url)
        port = parts.port
    except (TypeError, ValueError):
        return False
    return (parts.scheme == 'http' and parts.hostname == '127.0.0.1' and port is not None
            and not parts.username and not parts.password and parts.path in ('', '/'))


def write_record(path, *, port, token, pid=None):
    """Atomically write the running instance's loopback URL and hand-off token (owner-only)."""
    path = Path(path)
    record = {'pid': pid if pid is not None else os.getpid(), 'url': loopback_url(port), 'token': token}
    temporary = path.with_name(path.name + f'.{os.getpid()}.tmp')
    try:
        os.unlink(temporary)  # a stale file could carry wider permissions
    except FileNotFoundError:
        pass
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(record, stream)
    os.replace(temporary, path)
    return record


def read_record(path):
    try:
        record = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    if (not isinstance(record, dict) or not isinstance(record.get('token'), str)
            or not _TOKEN.match(record['token']) or not is_loopback_url(record.get('url', ''))
            or type(record.get('pid')) is not int):
        return None
    return record


def remove_record_if_owned(path, token):
    record = read_record(path)
    if record and secrets.compare_digest(record['token'], token):
        try:
            Path(path).unlink()
        except OSError:
            pass


def notify_existing(record, timeout=3.0):
    """Ask the running instance to show its window. Returns True when it answered."""
    if not record or not is_loopback_url(record.get('url', '')):
        return False
    request = urllib.request.Request(record['url'].rstrip('/') + '/desktop/api/focus', data=b'{}', method='POST',
                                     headers={TOKEN_HEADER: record['token'], 'Content-Type': 'application/json'})
    try:
        with loopback_opener().open(request, timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False
