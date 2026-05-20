from __future__ import annotations

import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import BlockedNetworkError


class _OkHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args: object) -> None:
        return


def test_external_socket_connect_is_blocked_with_endpoint_name() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(BlockedNetworkError, match="example.com:443"):
            sock.connect(("example.com", 443))
    finally:
        sock.close()


def test_external_socket_connect_ex_is_blocked_with_endpoint_name() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(BlockedNetworkError, match="example.com:443"):
            sock.connect_ex(("example.com", 443))
    finally:
        sock.close()


def test_external_dns_resolution_is_blocked_before_connect() -> None:
    with pytest.raises(BlockedNetworkError, match="example.com:443"):
        socket.getaddrinfo("example.com", 443)


def test_external_create_connection_is_blocked_before_dns() -> None:
    with pytest.raises(BlockedNetworkError, match="example.com:443"):
        socket.create_connection(("example.com", 443), timeout=0.1)


def test_urlopen_is_blocked_before_any_external_call() -> None:
    with pytest.raises(BlockedNetworkError, match="https://api.nal.usda.gov/fdc/v1/foods/search"):
        urllib.request.urlopen("https://api.nal.usda.gov/fdc/v1/foods/search")


def test_loopback_socket_connect_is_allowed() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    host, port = server.getsockname()
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.connect((host, port))
        accepted, _addr = server.accept()
        accepted.close()
    finally:
        client.close()
        server.close()


def test_loopback_socket_connect_ex_is_allowed() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    host, port = server.getsockname()
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        assert client.connect_ex((host, port)) == 0
        accepted, _addr = server.accept()
        accepted.close()
    finally:
        client.close()
        server.close()


def test_loopback_dns_resolution_is_allowed() -> None:
    results = socket.getaddrinfo("localhost", 80)
    assert results


def test_loopback_urlopen_is_allowed() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OkHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/health") as resp:
            assert resp.read() == b"ok"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
