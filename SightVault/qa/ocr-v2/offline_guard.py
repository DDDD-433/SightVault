"""In-process outbound socket guard for local OCR inference, not a firewall."""
from __future__ import annotations

import ipaddress
import socket


def install_socket_guard():
    attempts = []
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo

    def loopback(host):
        if host in ("localhost", "localhost."):
            return True
        try:
            return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
        except (ValueError, AttributeError):
            return False

    def check(address, method):
        if not isinstance(address, tuple) or not address:
            return  # AF_UNIX and other local socket families have no remote host.
        host = address[0]
        if loopback(host):
            return
        event = {"method": method, "address": repr(address)}
        attempts.append(event)
        raise OSError("SightVault OCR offline guard blocked a non-loopback network connection")

    def guarded_connect(sock, address):
        check(address, "connect")
        return original_connect(sock, address)

    def guarded_connect_ex(sock, address):
        check(address, "connect_ex")
        return original_connect_ex(sock, address)

    def guarded_getaddrinfo(host, *args, **kwargs):
        if not loopback(host):
            attempts.append({"method": "getaddrinfo", "address": repr(host)})
            raise OSError("SightVault OCR offline guard blocked external DNS resolution")
        return original_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket.getaddrinfo = guarded_getaddrinfo

    def uninstall():
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex
        socket.getaddrinfo = original_getaddrinfo

    return attempts, uninstall
