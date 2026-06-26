from __future__ import annotations

import io
import json
import urllib.error

import pytest

import whoop_client


class _FakeResponse:
    def __init__(self, payload, *, status=200):
        self.payload = payload
        self.status = status

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _http_error(code: int, payload: str):
    return urllib.error.HTTPError(
        url="https://api.prod.whoop.com",
        code=code,
        msg="error",
        hdrs={},
        fp=io.BytesIO(payload.encode("utf-8")),
    )


def test_load_whoop_config_uses_keychain_only(monkeypatch, tmp_path):
    client_id_file = tmp_path / ".whoop-client-id"
    client_id_file.write_text("client-id-123", encoding="utf-8")

    class _Proc:
        returncode = 0
        stdout = "keychain-secret\n"

    monkeypatch.setattr(whoop_client.subprocess, "run", lambda *args, **kwargs: _Proc())

    config = whoop_client.load_whoop_config(
        "http://localhost/api/whoop/callback",
        client_id_path=str(client_id_file),
    )

    assert config.client_id == "client-id-123"
    assert config.client_secret == "keychain-secret"


def test_redact_whoop_error_handles_query_and_json_shapes():
    message = (
        'Authorization: Bearer abc123 code=secret-code state=abc123 '
        '{"access_token":"tok","refresh_token":"refresh"}'
    )

    redacted = whoop_client.redact_whoop_error(message)

    assert "abc123" not in redacted
    assert "secret-code" not in redacted
    assert '"access_token":"tok"' not in redacted
    assert '"refresh_token":"refresh"' not in redacted
    assert "[redacted]" in redacted


def test_whoop_client_refreshes_then_paginates(monkeypatch):
    config = whoop_client.WhoopConfig("client-id", "safe-placeholder", "http://localhost/api/whoop/callback")
    calls = {"get": 0, "post": 0, "sleep": []}

    def fake_urlopen(request, timeout=15):
        url = request.full_url
        method = getattr(request, "method", "GET")
        if method == "POST":
            calls["post"] += 1
            return _FakeResponse(
                {
                    "access_token": "fresh-access",
                    "refresh_token": "fresh-refresh",
                    "expires_in": 3600,
                }
            )
        calls["get"] += 1
        if calls["get"] == 1:
            raise _http_error(401, '{"error":"expired","access_token":"stale"}')
        if calls["get"] == 2:
            assert request.headers["Authorization"] == "Bearer fresh-access"
            return _FakeResponse(
                {
                    "records": [{"id": "r1"}, {"id": "r2"}],
                    "next_token": "page-2",
                }
            )
        assert "nextToken=page-2" in url
        return _FakeResponse({"records": [{"id": "r3"}]})

    client = whoop_client.WhoopClient(
        config,
        session_value="stale-access",
        renewal_value="stale-refresh",
        urlopen=fake_urlopen,
        sleep=lambda seconds: calls["sleep"].append(seconds),
    )

    rows = client.fetch_recovery()

    assert [row["id"] for row in rows] == ["r1", "r2", "r3"]
    assert client.access_token == "fresh-access"
    assert client.refresh_token == "fresh-refresh"
    assert calls["post"] == 1


def test_whoop_client_retries_retryable_errors():
    config = whoop_client.WhoopConfig("client-id", "safe-placeholder", "http://localhost/api/whoop/callback")
    attempts = {"count": 0, "sleep": []}

    def fake_urlopen(request, timeout=15):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise _http_error(429, '{"error":"rate_limited","access_token":"tok"}')
        return _FakeResponse({"records": [{"id": "ok"}]})

    client = whoop_client.WhoopClient(
        config,
        session_value="current-session",
        renewal_value="current-renewal",
        urlopen=fake_urlopen,
        sleep=lambda seconds: attempts["sleep"].append(seconds),
        max_retries=2,
    )

    rows = client.fetch_sleep()

    assert rows == [{"id": "ok"}]
    assert attempts["sleep"] == [1]


def test_revoke_whoop_access_uses_delete_with_bearer_token():
    config = whoop_client.WhoopConfig("client-id", "safe-placeholder", "http://localhost/api/whoop/callback")
    seen = {}

    def fake_urlopen(request, timeout=15):
        seen["method"] = getattr(request, "method", "GET")
        seen["url"] = request.full_url
        seen["authorization"] = request.headers.get("Authorization")
        return _FakeResponse({})

    whoop_client.revoke_whoop_access(config, session_value="session-value", urlopen=fake_urlopen)

    assert seen["method"] == "DELETE"
    assert seen["url"] == whoop_client.WHOOP_REVOKE_URL
    assert seen["authorization"] == "Bearer session-value"
