from __future__ import annotations

import ipaddress
import socket
import urllib.request
from collections.abc import Iterator
from urllib.parse import urlparse

import pytest
from flask.testing import FlaskClient
from werkzeug.datastructures import Headers


class BlockedNetworkError(RuntimeError):
    """Raised when a test attempts uncassetted external network access."""


_CSRF_TEST_HEADER = "X-Requested-With"
_CSRF_TEST_VALUE = "XMLHttpRequest"
_CSRF_TEST_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_CSRF_TEST_OMIT_ENVIRON = "fitness_dashboard.omit_auto_csrf_header"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "allow_net: allow live external network access for an explicitly live test",
    )


def _loopback_host(host: object) -> bool:
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii")
        except UnicodeDecodeError:
            return False
    if not isinstance(host, str):
        return False
    cleaned = host.strip("[]").lower()
    if cleaned in {"localhost", "0.0.0.0"} or cleaned.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(cleaned).is_loopback
    except ValueError:
        return False


def _endpoint(address: object) -> tuple[object, object | None]:
    if isinstance(address, tuple) and address:
        host = address[0]
        port = address[1] if len(address) > 1 else None
        return host, port
    return address, None


def _request_host(req: object) -> object | None:
    target = getattr(req, "full_url", req)
    if not isinstance(target, str):
        return None
    parsed = urlparse(target)
    return parsed.hostname


@pytest.fixture(autouse=True)
def csrf_browser_header_for_test_clients(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make Flask test-client mutations look like same-origin browser fetches."""
    original_open = FlaskClient.open

    def open_with_csrf_header(self: FlaskClient, *args: object, **kwargs: object) -> object:
        method = str(kwargs.get("method") or "").upper()
        environ_overrides = kwargs.get("environ_overrides") or {}
        if method in _CSRF_TEST_MUTATING_METHODS and not environ_overrides.get(_CSRF_TEST_OMIT_ENVIRON):
            headers = Headers(kwargs.get("headers") or {})
            if _CSRF_TEST_HEADER not in headers:
                headers.add(_CSRF_TEST_HEADER, _CSRF_TEST_VALUE)
            kwargs["headers"] = headers
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(FlaskClient, "open", open_with_csrf_header)
    yield


@pytest.fixture(autouse=True)
def isolated_whoop_material_dir(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[None]:
    monkeypatch.setenv("WHOOP_PROTECTED_MATERIAL_DIR", str(tmp_path / "whoop-material"))
    yield


@pytest.fixture(autouse=True)
def isolated_open_wearables_password_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[None]:
    monkeypatch.setenv("OPEN_WEARABLES_PASSWORD_FILE", str(tmp_path / "open-wearables-password"))
    yield


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Iterator[None]:
    """Fail tests that try to make uncassetted external network calls."""
    if request.node.get_closest_marker("allow_net"):
        yield
        return

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo
    original_urlopen = urllib.request.urlopen

    def _check_external(address: object, *, service: object | None = None) -> None:
        host, port = _endpoint(address)
        if service is not None:
            port = service
        if not _loopback_host(host):
            target = f"{host}:{port}" if port is not None else str(host)
            raise BlockedNetworkError(
                "live external network call blocked by pytest no-live-network guard: "
                f"{target}. Mock the call, use a cassette, or mark an intentionally live "
                "local-only test with @pytest.mark.allow_net."
            )

    def guarded_connect(sock: socket.socket, address: object) -> object:
        _check_external(address)
        return original_connect(sock, address)

    def guarded_connect_ex(sock: socket.socket, address: object) -> int:
        _check_external(address)
        return original_connect_ex(sock, address)

    def guarded_getaddrinfo(host: object, port: object, *args: object, **kwargs: object) -> object:
        _check_external(host, service=port)
        return original_getaddrinfo(host, port, *args, **kwargs)

    def guarded_urlopen(req: object, *_args: object, **_kwargs: object) -> object:
        host = _request_host(req)
        if _loopback_host(host):
            return original_urlopen(req, *_args, **_kwargs)
        target = getattr(req, "full_url", req)
        raise BlockedNetworkError(
            "live external urllib.request.urlopen call blocked by pytest no-live-network guard: "
            f"{target}. Mock the call or use a cassette."
        )

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)
    monkeypatch.setattr(urllib.request, "urlopen", guarded_urlopen)
    yield
