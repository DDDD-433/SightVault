"""Small Python socket/DNS guard for frozen-app QA runs.

This is an in-process test guard, not an operating-system firewall. Install it
before application imports so normal Python networking can reach loopback while
external socket destinations and DNS names are refused.
"""

import errno
import ipaddress
import socket
import threading

_MESSAGE = 'Outbound networking is blocked by the SightVault QA socket guard.'
_LOCK = threading.RLock()
_INSTALLED = False
_BLOCKED_ATTEMPTS = 0
_PROBE_DENIED = None
_ORIGINALS = {}
_AF_UNIX = getattr(socket, 'AF_UNIX', None)


def _increment_blocked():
    global _BLOCKED_ATTEMPTS
    with _LOCK:
        _BLOCKED_ATTEMPTS += 1


def _decode_host(host):
    if isinstance(host, bytes):
        try:
            return host.decode('ascii')
        except UnicodeDecodeError:
            return None
    return host if isinstance(host, str) else None


def _host_kind(host):
    """Return loopback, localhost, external, or None for non-host inputs."""
    value = _decode_host(host)
    if value is None:
        return None
    candidate = value.strip()
    try:
        address = ipaddress.ip_address(candidate.split('%', 1)[0])
    except ValueError:
        if candidate.rstrip('.').lower() == 'localhost':
            return 'localhost'
        return 'external'
    return 'loopback' if address.is_loopback else 'external'


def _sockaddr_is_loopback(sockaddr):
    if not isinstance(sockaddr, tuple) or not sockaddr:
        return False
    kind = _host_kind(sockaddr[0])
    if kind == 'loopback':
        return True
    if kind != 'localhost':
        return False
    # A resolver result must be a numeric loopback address; never recurse into DNS.
    return False


def _is_unix_socket(sock):
    return _AF_UNIX is not None and getattr(sock, 'family', None) == _AF_UNIX


def _destination(sock, address):
    """Return (allowed, destination) while preserving AF_UNIX and invalid inputs."""
    if _is_unix_socket(sock):
        return True, address
    if not isinstance(address, tuple) or not address:
        return True, address
    if not isinstance(address[0], (str, bytes)):
        return True, address
    kind = _host_kind(address[0])
    if kind == 'loopback':
        return True, address
    if kind != 'localhost':
        # Invalid, non-ASCII, and non-loopback hostnames are denied before resolution.
        return False, address

    # Resolve localhost once, inspect every answer, then connect to the checked numeric
    # sockaddr. This both permits localhost and prevents a changed/odd resolver answer
    # from turning the connection into an external one.
    original = _ORIGINALS.get('getaddrinfo')
    if original is None:
        return False, address
    infos = original(address[0].decode('ascii') if isinstance(address[0], bytes) else address[0],
                     address[1], getattr(sock, 'family', 0), getattr(sock, 'type', 0),
                     getattr(sock, 'proto', 0))
    if not infos:
        return True, address
    if any(not _sockaddr_is_loopback(info[4]) for info in infos):
        return False, address
    return True, infos[0][4]


def _blocked_connect(self, address):
    allowed, checked_address = _destination(self, address)
    if not allowed:
        _increment_blocked()
        raise PermissionError(errno.EACCES, _MESSAGE)
    return _ORIGINALS['connect'](self, checked_address)


def _blocked_connect_ex(self, address):
    allowed, checked_address = _destination(self, address)
    if not allowed:
        _increment_blocked()
        return errno.EACCES
    return _ORIGINALS['connect_ex'](self, checked_address)


def _blocked_sendto(self, data, *args, **kwargs):
    if not _is_unix_socket(self):
        address = kwargs.get('address') if 'address' in kwargs else (args[-1] if args else None)
        allowed, checked_address = _destination(self, address)
        if not allowed:
            _increment_blocked()
            raise PermissionError(errno.EACCES, _MESSAGE)
        if 'address' in kwargs:
            kwargs['address'] = checked_address
        elif args:
            args = (*args[:-1], checked_address)
    return _ORIGINALS['sendto'](self, data, *args, **kwargs)


def _blocked_sendmsg(self, buffers, ancdata=(), flags=0, address=None):
    if (address is not None and
            not _is_unix_socket(self)):
        allowed, checked_address = _destination(self, address)
        if not allowed:
            _increment_blocked()
            raise PermissionError(errno.EACCES, _MESSAGE)
        address = checked_address
    return _ORIGINALS['sendmsg'](self, buffers, ancdata, flags, address)


def _blocked_getaddrinfo(host, port, *args, **kwargs):
    if host is None or host == '' or host == b'':
        return _ORIGINALS['getaddrinfo'](host, port, *args, **kwargs)
    if not isinstance(host, (str, bytes)):
        return _ORIGINALS['getaddrinfo'](host, port, *args, **kwargs)
    kind = _host_kind(host)
    if kind == 'external' or kind is None:
        _increment_blocked()
        raise socket.gaierror(getattr(socket, 'EAI_FAIL', -4), _MESSAGE)
    value = _decode_host(host) if kind == 'localhost' else host
    results = _ORIGINALS['getaddrinfo'](value, port, *args, **kwargs)
    if kind == 'localhost' and any(not _sockaddr_is_loopback(item[4]) for item in results):
        _increment_blocked()
        raise socket.gaierror(getattr(socket, 'EAI_FAIL', -4), _MESSAGE)
    return results


def _blocked_gethostbyname(host):
    if not isinstance(host, (str, bytes)) or host == '' or host == b'':
        return _ORIGINALS['gethostbyname'](host)
    kind = _host_kind(host)
    if kind not in ('loopback', 'localhost'):
        _increment_blocked()
        raise socket.gaierror(getattr(socket, 'EAI_FAIL', -4), _MESSAGE)
    value = _decode_host(host) if kind == 'localhost' else host
    result = _ORIGINALS['gethostbyname'](value)
    if not _sockaddr_is_loopback((result,)):
        _increment_blocked()
        raise socket.gaierror(getattr(socket, 'EAI_FAIL', -4), _MESSAGE)
    return result


def _blocked_gethostbyname_ex(host):
    if not isinstance(host, (str, bytes)) or host == '' or host == b'':
        return _ORIGINALS['gethostbyname_ex'](host)
    kind = _host_kind(host)
    if kind not in ('loopback', 'localhost'):
        _increment_blocked()
        raise socket.gaierror(getattr(socket, 'EAI_FAIL', -4), _MESSAGE)
    value = _decode_host(host) if kind == 'localhost' else host
    result = _ORIGINALS['gethostbyname_ex'](value)
    if not result[2] or any(not _sockaddr_is_loopback((address,)) for address in result[2]):
        _increment_blocked()
        raise socket.gaierror(getattr(socket, 'EAI_FAIL', -4), _MESSAGE)
    return result


def _blocked_gethostbyaddr(address):
    if not isinstance(address, (str, bytes)):
        return _ORIGINALS['gethostbyaddr'](address)
    if _host_kind(address) != 'loopback':
        _increment_blocked()
        raise socket.herror(getattr(socket, 'EAI_FAIL', -4), _MESSAGE)
    return _ORIGINALS['gethostbyaddr'](address)


def install():
    """Install the guard once. Returns a copy of its non-sensitive counters."""
    global _INSTALLED
    with _LOCK:
        if not _INSTALLED:
            _ORIGINALS.update({
                'connect': socket.socket.connect,
                'connect_ex': socket.socket.connect_ex,
                'sendto': socket.socket.sendto,
                'getaddrinfo': socket.getaddrinfo,
                'gethostbyname': socket.gethostbyname,
                'gethostbyname_ex': socket.gethostbyname_ex,
                'gethostbyaddr': socket.gethostbyaddr,
            })
            if hasattr(socket.socket, 'sendmsg'):
                _ORIGINALS['sendmsg'] = socket.socket.sendmsg
                socket.socket.sendmsg = _blocked_sendmsg
            socket.socket.connect = _blocked_connect
            socket.socket.connect_ex = _blocked_connect_ex
            socket.socket.sendto = _blocked_sendto
            socket.getaddrinfo = _blocked_getaddrinfo
            socket.gethostbyname = _blocked_gethostbyname
            socket.gethostbyname_ex = _blocked_gethostbyname_ex
            socket.gethostbyaddr = _blocked_gethostbyaddr
            _INSTALLED = True
    return snapshot()


def snapshot():
    """Return the install state and simple blocked-attempt counters."""
    with _LOCK:
        return {'installed': _INSTALLED, 'blocked_attempts': _BLOCKED_ATTEMPTS,
                'probe_denied': _PROBE_DENIED}


def probe():
    """Run one cached, network-free external-connect denial self-test."""
    global _PROBE_DENIED
    if not _INSTALLED:
        install()
    with _LOCK:
        if _PROBE_DENIED is None:
            if socket.socket.connect is not _blocked_connect:
                _PROBE_DENIED = False
            else:
                class _ProbeSocket:
                    family = socket.AF_INET

                try:
                    # Exercise the installed wrapper with a non-socket test double. Its
                    # deny path returns before the captured OS socket method can run.
                    _blocked_connect(_ProbeSocket(), ('203.0.113.1', 9))
                except PermissionError:
                    _PROBE_DENIED = True
                except Exception:
                    _PROBE_DENIED = False
                else:
                    _PROBE_DENIED = False
    return snapshot()
