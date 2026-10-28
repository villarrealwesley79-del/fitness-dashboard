"""Reviewer r6 (PER-1585): CatchUnhandled against fake inner ASGI apps, and the vendor CIMD guard."""
import asyncio
import logging

import httpx2
import pytest

import server
from server import CatchUnhandled, VendorCIMDProxy
from test_server import make, seen  # noqa: F401  fixtures


def run(app, scope, inbound=None):
    sent = []
    inbound = list(inbound or [{"type": "http.request", "body": b"", "more_body": False}])

    async def receive():
        return inbound.pop(0) if inbound else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


def http(path="/x"):
    return {"type": "http", "path": path, "method": "GET", "headers": [], "query_string": b""}


def raises_after(*messages, exc=RuntimeError("boom")):
    async def app(scope, receive, send):
        for m in messages:
            await send(m)
        raise exc
    return app


START = {"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/event-stream")]}


def ours(caplog):
    return [r for r in caplog.records if r.name == "fitness_mcp"]


def test_raise_before_start_is_a_plain_500(caplog):
    sent = run(CatchUnhandled(raises_after()), http())
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 500
    assert b"".join(m.get("body", b"") for m in sent[1:]) == b"Internal Server Error"
    assert len(ours(caplog)) == 1


def test_raise_mid_stream_ends_the_body_once_and_never_sends_a_second_start(caplog):
    part = {"type": "http.response.body", "body": b"data: 1\n\n", "more_body": True}
    sent = run(CatchUnhandled(raises_after(START, part)), http())
    assert [m["type"] for m in sent].count("http.response.start") == 1
    assert sent[-1] == {"type": "http.response.body", "body": b"", "more_body": False}
    assert len(sent) == 3


def test_raise_after_complete_response_sends_nothing_more(caplog):
    body = {"type": "http.response.body", "body": b"ok"}
    sent = run(CatchUnhandled(raises_after({**START, "status": 500}, body)), http())
    assert sent == [{**START, "status": 500}, body]
    assert len(ours(caplog)) == 1


@pytest.mark.parametrize("exc", [asyncio.CancelledError(), KeyboardInterrupt(), SystemExit(3)])
def test_base_exceptions_propagate(exc, caplog):
    with pytest.raises(type(exc)):
        run(CatchUnhandled(raises_after(exc=exc)), http())
    assert not ours(caplog)


def test_lifespan_and_websocket_scopes_pass_through_untouched():
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["type"])
        if scope["type"] == "lifespan":
            assert (await receive())["type"] == "lifespan.startup"
            await send({"type": "lifespan.startup.complete"})
            assert (await receive())["type"] == "lifespan.shutdown"
            await send({"type": "lifespan.shutdown.complete"})
        else:
            raise RuntimeError("must not be caught for non-http scopes")

    sent = run(CatchUnhandled(app), {"type": "lifespan"}, [{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    assert [m["type"] for m in sent] == ["lifespan.startup.complete", "lifespan.shutdown.complete"]
    with pytest.raises(RuntimeError):
        run(CatchUnhandled(app), {"type": "websocket", "path": "/ws"})


def test_one_log_line_per_window_across_many_paths(monkeypatch, caplog):
    clock = [1000.0]
    monkeypatch.setattr(server.time, "monotonic", lambda: clock[0])
    app = CatchUnhandled(raises_after(), window=3600)
    for i in range(1000):
        run(app, http(f"/p{i}"))
    assert len(ours(caplog)) == 1
    clock[0] += 3599
    run(app, http("/late"))
    assert len(ours(caplog)) == 1
    clock[0] += 1
    run(app, http("/next"))
    assert len(ours(caplog)) == 2


def test_logged_path_is_one_short_line(caplog):
    run(CatchUnhandled(raises_after(exc=ValueError("secret\nforged"))), http("/a\nb c\x00" + "z" * 10_000))
    (record,) = ours(caplog)
    line = record.getMessage()
    assert "\n" not in line and " " not in line and "\x00" not in line
    assert len(line) <= 140 and "secret" not in line and "forged" not in line


def test_send_failing_inside_the_500_path_is_swallowed():
    async def send(message):
        raise OSError("client gone")

    async def receive():
        return {"type": "http.disconnect"}

    asyncio.run(CatchUnhandled(raises_after())(http(), receive, send))  # must not raise


def test_wrapper_is_an_asgi3_coroutine_for_uvicorn_and_forwards_starlette_attributes(make):
    import inspect

    from uvicorn.config import Config as UConfig

    app = make()
    assert inspect.iscoroutinefunction(type(app).__call__)
    cfg = UConfig(app, lifespan="on")
    cfg.load()
    assert cfg.loaded_app is not None
    assert app.state.path == "/mcp" and app.routes and app.router is app.app.router


@pytest.mark.parametrize("client_id,ok", [
    ("https://claude.ai/c.json", True),
    ("https://claude.ai:443/c.json", True),
    ("https://sub.chatgpt.com/c.json", True),
    ("https://evil.example\\@claude.ai/c.json", None),   # parser differential: decided below
    ("https://claude.ai\\@evil.example/c.json", False),
    ("https://claude.ai@evil.example/c.json", False),
    ("https://evil.example/c.json?x=@claude.ai", False),
    ("https://evil.example/#@claude.ai", False),
    ("https://claude.ai.evil.example/c.json", False),
    ("https://evilclaude.ai/c.json", False),
    ("https://claude%2eai/c.json", False),
    ("https://[::1]/c.json", False),
    ("https://claude.ai./c.json", False),
    ("https://CLAUDE.AI/c.json", True),
])
def test_vendor_guard_agrees_with_the_host_the_fetcher_would_connect_to(client_id, ok):
    proxy = VendorCIMDProxy.__new__(VendorCIMDProxy)
    proxy._cimd_hosts = server.CIMD_HOSTS
    allowed = proxy._fetchable(client_id)
    if ok is not None:
        assert allowed is ok
    if allowed:  # whatever the guard allows, the HTTP client must connect to a vendor host too
        try:
            fetch_host = httpx2.URL(client_id).host.lower()
        except Exception:
            return  # the fetcher cannot even parse it, so nothing is fetched
        assert any(fetch_host == h or fetch_host.endswith("." + h) for h in server.CIMD_HOSTS), fetch_host


def test_anonymous_register_cannot_store_an_oversized_registration(make, tmp_path):
    """r6 BLOCKER: /register reads any JSON body and stores it; 60 a day of these would fill the disk."""
    from starlette.testclient import TestClient

    cb = "https://claude.ai/api/mcp/auth_callback"
    with TestClient(make(), base_url="https://frankbox.example.ts.net:8443") as c:
        r = c.post("/register", json={"redirect_uris": [cb], "token_endpoint_auth_method": "none", "client_name": "n" * (2 << 20)})
        normal = c.post("/register", json={"redirect_uris": [cb], "token_endpoint_auth_method": "none", "client_name": "Claude"})
    biggest = max((f.stat().st_size for f in (tmp_path / "store").rglob("*") if f.is_file()), default=0)
    assert normal.status_code == 201
    assert r.status_code in (400, 413), r.status_code
    assert biggest < 64 * 1024, biggest


def test_real_main_writes_no_line_per_anonymous_request(tmp_path):
    """r6 SHOULD-FIX: through the real main() at the plist log levels, malformed multipart on the uncounted /token
    (python_multipart WARNING) and a bad chunk size on any path (uvicorn 'ASGI callable returned without completing
    response.') each wrote one line per request, so the log had no bound."""
    import hashlib
    import os
    import socket
    import subprocess
    import sys
    import time
    from pathlib import Path

    from cryptography.fernet import Fernet

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    for name, value in (("jwt", "j" * 48), ("skey", Fernet.generate_key().decode()), ("read", "r" * 32)):
        (tmp_path / name).write_text(value)
    env = {**os.environ, "MCP_PUBLIC_BASE_URL": "https://frankbox.example.ts.net:8443/mcp", "MCP_LISTEN_PORT": str(port),
           "MCP_GITHUB_CLIENT_ID": "i", "MCP_GITHUB_CLIENT_SECRET": "s", "MCP_OWNER_GITHUB_ID": "424242",
           "MCP_JWT_SIGNING_KEY_FILE": str(tmp_path / "jwt"), "MCP_STORAGE_ENCRYPTION_KEY_FILE": str(tmp_path / "skey"),
           "MCP_STORAGE_DIR": str(tmp_path / "store"), "FITNESS_READ_TOKEN_FILE": str(tmp_path / "read"),
           "MCP_BOT_TOKEN_SHA256": hashlib.sha256(b"b").hexdigest(), "FASTMCP_LOG_LEVEL": "ERROR"}
    log = tmp_path / "mcp.log"
    server_py = Path(__file__).resolve().parents[1] / "server.py"
    with open(log, "wb") as out:
        proc = subprocess.Popen([sys.executable, str(server_py)], env=env, stdout=out, stderr=out, cwd=server_py.parent)
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        time.sleep(0.3)
        before = log.read_bytes().count(b"\n")
        host = b"Host: frankbox.example.ts.net:8443\r\n"
        shapes = [b"POST /token HTTP/1.1\r\n" + host + b"Content-Type: multipart/form-data; boundary=b\r\nContent-Length: 3\r\n\r\na=b",
                  b"POST /nope HTTP/1.1\r\n" + host + b"Transfer-Encoding: chunked\r\n\r\nzz\r\n"]
        for payload in shapes * 20:
            with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
                s.sendall(payload)
                s.shutdown(socket.SHUT_WR)
                try:
                    while s.recv(65536):
                        pass
                except OSError:
                    pass
        time.sleep(0.5)
        new = log.read_bytes().splitlines()[before:]
    finally:
        proc.terminate()
        proc.wait(10)
    assert len(new) <= 2, b"\n".join(new[:6]).decode("utf-8", "replace")
