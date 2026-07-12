from __future__ import annotations

import json
import socketserver
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import scripts.smoke_open_wearables_sidecar as smoke


class _DashboardHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str, dict]] = []

    def _reply(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        self.requests.append(("GET", self.path, dict(self.headers)))
        if self.path == "/api/open-wearables/providers":
            self._reply(200, {
                "status": "connected",
                "providers": [
                    {"provider_id": "ready_watch", "state": "ready", "raw_health": "must-not-leak"},
                    {"provider_id": "blocked_watch", "state": "blocked", "internal_detail": "must-not-leak"},
                ],
                "error_code": None,
            })
            return
        self._reply(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        self.requests.append(("POST", self.path, dict(self.headers)))
        if self.path == "/api/open-wearables/setup/check":
            assert body == {"base_url": "http://127.0.0.1:8000"}
            self._reply(200, {
                "status": "ok",
                "provider_check": {"checked": True, "provider_count": 2, "error_code": None},
                "config": {
                    "base_url": "http://127.0.0.1:8000",
                    "provider_actions": [
                        {"provider": "blocked_watch", "enabled": False, "reason": "provider_not_ready"},
                        {"provider": "ready_watch", "enabled": True, "reason": ""},
                    ],
                },
            })
            return
        if self.path == "/api/open-wearables/sync":
            self._reply(200, {
                "status": "success",
                "source": "open_wearables",
                "counts": {"sleep": 1, "workouts": 2},
                "raw_health": {"heart_rate": 61},
                "facts_upserted": 4,
            })
            return
        if self.path == "/api/open-wearables/pair/blocked_watch":
            self._reply(400, {
                "status": "blocked",
                "provider": "blocked_watch",
                "error": {"code": "provider_not_ready", "message": "private details"},
            })
            return
        if self.path == "/api/open-wearables/pair/ready_watch":
            self._reply(200, {
                "status": "ready",
                "provider": "ready_watch",
                "authorization_url": "https://provider.invalid/oauth?token=must-not-leak",
            })
            return
        self._reply(404, {"error": "not found"})

    def log_message(self, *_args: object) -> None:
        return


def _serve_dashboard():
    _DashboardHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DashboardHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_live_smoke_verifies_contracts_without_retaining_payloads(tmp_path) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_text("session=test-session-secret\n", encoding="utf-8")
    cookie_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
    server, thread = _serve_dashboard()
    try:
        report = smoke.run_smoke(
            dashboard_url=f"http://127.0.0.1:{server.server_port}",
            sidecar_url="http://127.0.0.1:8000",
            cookie_file=cookie_file,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert report == {
        "status": "pass",
        "checks": {
            "provider_catalog": {"status": "pass", "provider_count": 2, "error_code": None},
            "setup_check": {"status": "pass", "error_code": None},
            "metadata_sync": {"status": "pass", "count_fields": ["sleep", "workouts"], "error_code": None},
            "blocked_provider": {"status": "pass", "provider": "blocked_watch", "error_code": "provider_not_ready"},
            "ready_provider": {"status": "pass", "provider": "ready_watch", "error_code": None},
        },
    }
    serialized = json.dumps(report)
    assert "must-not-leak" not in serialized
    assert "authorization_url" not in serialized
    assert "raw_health" not in serialized
    assert all(headers["Cookie"] == "session=test-session-secret" for _, _, headers in _DashboardHandler.requests)
    assert all(headers.get("X-Requested-With") == "XMLHttpRequest" for method, _, headers in _DashboardHandler.requests if method == "POST")


def test_live_smoke_returns_stable_block_when_dashboard_is_unavailable(tmp_path) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_text("session=test-session-secret\n", encoding="utf-8")
    cookie_file.chmod(stat.S_IRUSR | stat.S_IWUSR)

    report = smoke.run_smoke(
        dashboard_url="http://127.0.0.1:1",
        sidecar_url="http://127.0.0.1:8000",
        cookie_file=cookie_file,
    )

    assert report == {
        "status": "blocked",
        "error_code": "smoke_dashboard_unavailable",
        "checks": {},
    }


def test_cookie_file_must_not_be_group_or_world_readable(tmp_path) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_text("session=test-session-secret\n", encoding="utf-8")
    cookie_file.chmod(0o644)

    report = smoke.run_smoke(
        dashboard_url="http://127.0.0.1:1",
        sidecar_url="http://127.0.0.1:8000",
        cookie_file=cookie_file,
    )

    assert report == {
        "status": "blocked",
        "error_code": "smoke_cookie_file_unsafe",
        "checks": {},
    }


def test_plain_http_dashboard_must_be_loopback(tmp_path) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_text("session=test-session-secret\n", encoding="utf-8")
    cookie_file.chmod(0o600)

    report = smoke.run_smoke(
        dashboard_url="http://dashboard.example.test",
        sidecar_url="http://127.0.0.1:8000",
        cookie_file=cookie_file,
    )

    assert report == {
        "status": "blocked",
        "error_code": "smoke_dashboard_insecure",
        "checks": {},
    }


class _RedirectHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:1/stolen")
        self.end_headers()

    def log_message(self, *_args: object) -> None:
        return


def test_dashboard_redirect_is_refused_before_forwarding_cookie(tmp_path) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_text("session=test-session-secret\n", encoding="utf-8")
    cookie_file.chmod(0o600)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _RedirectHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        report = smoke.run_smoke(
            dashboard_url=f"http://127.0.0.1:{server.server_port}",
            sidecar_url="http://127.0.0.1:8000",
            cookie_file=cookie_file,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert report == {
        "status": "blocked",
        "error_code": "smoke_redirect_refused",
        "checks": {},
    }


def test_setup_check_must_confirm_the_requested_sidecar(tmp_path) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_text("session=test-session-secret\n", encoding="utf-8")
    cookie_file.chmod(0o600)
    server, thread = _serve_dashboard()
    original_reply = _DashboardHandler._reply

    def mismatched_reply(self, status, payload):
        if self.path == "/api/open-wearables/setup/check":
            payload["config"]["base_url"] = "http://127.0.0.1:8001"
        return original_reply(self, status, payload)

    _DashboardHandler._reply = mismatched_reply
    try:
        report = smoke.run_smoke(
            dashboard_url=f"http://127.0.0.1:{server.server_port}",
            sidecar_url="http://127.0.0.1:8000",
            cookie_file=cookie_file,
        )
    finally:
        _DashboardHandler._reply = original_reply
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert report["status"] == "blocked"
    assert report["error_code"] == "smoke_setup_check_failed"
    assert report["checks"]["setup_check"] == {
        "status": "blocked",
        "error_code": "sidecar_mismatch",
    }
    assert [path for _, path, _ in _DashboardHandler.requests] == [
        "/api/open-wearables/providers",
        "/api/open-wearables/setup/check",
    ]


def test_distinct_loopback_host_spellings_require_exact_configuration(tmp_path) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_text("session=test-session-secret\n", encoding="utf-8")
    cookie_file.chmod(0o600)
    server, thread = _serve_dashboard()
    original_reply = _DashboardHandler._reply

    def localhost_reply(self, status, payload):
        if self.path == "/api/open-wearables/setup/check":
            payload["config"]["base_url"] = "http://localhost:8000"
        return original_reply(self, status, payload)

    _DashboardHandler._reply = localhost_reply
    try:
        report = smoke.run_smoke(
            dashboard_url=f"http://127.0.0.1:{server.server_port}",
            sidecar_url="http://127.0.0.1:8000",
            cookie_file=cookie_file,
        )
    finally:
        _DashboardHandler._reply = original_reply
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert report["status"] == "blocked"
    assert report["error_code"] == "smoke_setup_check_failed"
    assert report["checks"]["setup_check"]["error_code"] == "sidecar_mismatch"


def test_metadata_sync_errors_block_without_retaining_error_values(tmp_path) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_text("session=test-session-secret\n", encoding="utf-8")
    cookie_file.chmod(0o600)
    server, thread = _serve_dashboard()
    original_reply = _DashboardHandler._reply

    def sync_error_reply(self, status, payload):
        if self.path == "/api/open-wearables/sync":
            payload["errors"] = {"sleep": "open_wearables_sync_error"}
        return original_reply(self, status, payload)

    _DashboardHandler._reply = sync_error_reply
    try:
        report = smoke.run_smoke(
            dashboard_url=f"http://127.0.0.1:{server.server_port}",
            sidecar_url="http://127.0.0.1:8000",
            cookie_file=cookie_file,
        )
    finally:
        _DashboardHandler._reply = original_reply
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert report["status"] == "blocked"
    assert report["error_code"] == "smoke_metadata_sync_failed"
    assert report["checks"]["metadata_sync"] == {
        "status": "blocked",
        "count_fields": ["sleep", "workouts"],
        "error_code": "open_wearables_sync_error",
        "error_fields": ["sleep"],
    }


def test_ready_provider_requires_a_valid_authorization_url(tmp_path) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_text("session=test-session-secret\n", encoding="utf-8")
    cookie_file.chmod(0o600)
    server, thread = _serve_dashboard()
    original_reply = _DashboardHandler._reply

    def missing_url_reply(self, status, payload):
        if self.path == "/api/open-wearables/pair/ready_watch":
            payload.pop("authorization_url", None)
        return original_reply(self, status, payload)

    _DashboardHandler._reply = missing_url_reply
    try:
        report = smoke.run_smoke(
            dashboard_url=f"http://127.0.0.1:{server.server_port}",
            sidecar_url="http://127.0.0.1:8000",
            cookie_file=cookie_file,
        )
    finally:
        _DashboardHandler._reply = original_reply
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert report["status"] == "blocked"
    assert report["error_code"] == "smoke_ready_provider_failed"
    assert report["checks"]["ready_provider"] == {
        "status": "blocked",
        "provider": "ready_watch",
        "error_code": None,
    }


def test_blocked_provider_must_match_its_setup_reason(tmp_path) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_text("session=test-session-secret\n", encoding="utf-8")
    cookie_file.chmod(0o600)
    server, thread = _serve_dashboard()
    original_reply = _DashboardHandler._reply

    def wrong_reason_reply(self, status, payload):
        if self.path == "/api/open-wearables/pair/blocked_watch":
            payload["error"]["code"] = "provider_catalog_unavailable"
        return original_reply(self, status, payload)

    _DashboardHandler._reply = wrong_reason_reply
    try:
        report = smoke.run_smoke(
            dashboard_url=f"http://127.0.0.1:{server.server_port}",
            sidecar_url="http://127.0.0.1:8000",
            cookie_file=cookie_file,
        )
    finally:
        _DashboardHandler._reply = original_reply
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert report["status"] == "blocked"
    assert report["error_code"] == "smoke_blocked_provider_failed"
    assert report["checks"]["blocked_provider"] == {
        "status": "blocked",
        "provider": "blocked_watch",
        "error_code": "provider_catalog_unavailable",
    }


def test_catalog_failure_is_not_masked_by_missing_provider_paths(tmp_path) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_text("session=test-session-secret\n", encoding="utf-8")
    cookie_file.chmod(0o600)
    server, thread = _serve_dashboard()
    original_reply = _DashboardHandler._reply

    def catalog_failure_reply(self, status, payload):
        if self.path == "/api/open-wearables/providers":
            payload["providers"] = []
            payload["error_code"] = "provider_catalog_unavailable"
        return original_reply(self, status, payload)

    _DashboardHandler._reply = catalog_failure_reply
    try:
        report = smoke.run_smoke(
            dashboard_url=f"http://127.0.0.1:{server.server_port}",
            sidecar_url="http://127.0.0.1:8000",
            cookie_file=cookie_file,
        )
    finally:
        _DashboardHandler._reply = original_reply
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert report == {
        "status": "blocked",
        "error_code": "smoke_provider_catalog_failed",
        "checks": {
            "provider_catalog": {
                "status": "blocked",
                "provider_count": 0,
                "error_code": "provider_catalog_unavailable",
            },
        },
    }
    assert [path for _, path, _ in _DashboardHandler.requests] == ["/api/open-wearables/providers"]


def test_provider_paths_are_required_before_metadata_sync(tmp_path) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_text("session=test-session-secret\n", encoding="utf-8")
    cookie_file.chmod(0o600)
    server, thread = _serve_dashboard()
    original_reply = _DashboardHandler._reply

    def missing_paths_reply(self, status, payload):
        if self.path == "/api/open-wearables/setup/check":
            payload["config"]["provider_actions"] = []
        return original_reply(self, status, payload)

    _DashboardHandler._reply = missing_paths_reply
    try:
        report = smoke.run_smoke(
            dashboard_url=f"http://127.0.0.1:{server.server_port}",
            sidecar_url="http://127.0.0.1:8000",
            cookie_file=cookie_file,
        )
    finally:
        _DashboardHandler._reply = original_reply
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert report["error_code"] == "smoke_provider_paths_missing"
    assert [path for _, path, _ in _DashboardHandler.requests] == [
        "/api/open-wearables/providers",
        "/api/open-wearables/setup/check",
    ]


def test_catalog_requires_connected_status(tmp_path) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_text("session=test-session-secret\n", encoding="utf-8")
    cookie_file.chmod(0o600)
    server, thread = _serve_dashboard()
    original_reply = _DashboardHandler._reply

    def attention_catalog_reply(self, status, payload):
        if self.path == "/api/open-wearables/providers":
            payload["status"] = "error"
        return original_reply(self, status, payload)

    _DashboardHandler._reply = attention_catalog_reply
    try:
        report = smoke.run_smoke(
            dashboard_url=f"http://127.0.0.1:{server.server_port}",
            sidecar_url="http://127.0.0.1:8000",
            cookie_file=cookie_file,
        )
    finally:
        _DashboardHandler._reply = original_reply
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert report["error_code"] == "smoke_provider_catalog_failed"
    assert report["checks"]["provider_catalog"]["status"] == "blocked"


def test_blocked_pair_response_must_identify_requested_provider(tmp_path) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_text("session=test-session-secret\n", encoding="utf-8")
    cookie_file.chmod(0o600)
    server, thread = _serve_dashboard()
    original_reply = _DashboardHandler._reply

    def wrong_provider_reply(self, status, payload):
        if self.path == "/api/open-wearables/pair/blocked_watch":
            payload["provider"] = "other_watch"
        return original_reply(self, status, payload)

    _DashboardHandler._reply = wrong_provider_reply
    try:
        report = smoke.run_smoke(
            dashboard_url=f"http://127.0.0.1:{server.server_port}",
            sidecar_url="http://127.0.0.1:8000",
            cookie_file=cookie_file,
        )
    finally:
        _DashboardHandler._reply = original_reply
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert report["error_code"] == "smoke_blocked_provider_failed"


def test_ready_pair_response_must_identify_requested_provider(tmp_path) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_text("session=test-session-secret\n", encoding="utf-8")
    cookie_file.chmod(0o600)
    server, thread = _serve_dashboard()
    original_reply = _DashboardHandler._reply

    def wrong_provider_reply(self, status, payload):
        if self.path == "/api/open-wearables/pair/ready_watch":
            payload["provider"] = "other_watch"
        return original_reply(self, status, payload)

    _DashboardHandler._reply = wrong_provider_reply
    try:
        report = smoke.run_smoke(
            dashboard_url=f"http://127.0.0.1:{server.server_port}",
            sidecar_url="http://127.0.0.1:8000",
            cookie_file=cookie_file,
        )
    finally:
        _DashboardHandler._reply = original_reply
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert report["error_code"] == "smoke_ready_provider_failed"


def test_non_utf8_cookie_file_returns_stable_invalid_result(tmp_path) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_bytes(b"session=\xff\xfe")
    cookie_file.chmod(0o600)

    report = smoke.run_smoke(
        dashboard_url="http://127.0.0.1:1",
        sidecar_url="http://127.0.0.1:8000",
        cookie_file=cookie_file,
    )

    assert report == {
        "status": "blocked",
        "error_code": "smoke_cookie_file_invalid",
        "checks": {},
    }


class _TruncatedResponseHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "100")
        self.end_headers()
        self.wfile.write(b"{")
        self.wfile.flush()
        self.connection.shutdown(1)

    def log_message(self, *_args: object) -> None:
        return


def test_truncated_dashboard_response_returns_stable_unavailable_result(tmp_path) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_text("session=test-session-secret\n", encoding="utf-8")
    cookie_file.chmod(0o600)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TruncatedResponseHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        report = smoke.run_smoke(
            dashboard_url=f"http://127.0.0.1:{server.server_port}",
            sidecar_url="http://127.0.0.1:8000",
            cookie_file=cookie_file,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert report == {
        "status": "blocked",
        "error_code": "smoke_dashboard_unavailable",
        "checks": {},
    }


def test_unrecognized_error_code_is_redacted_from_report(tmp_path) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_text("session=test-session-secret\n", encoding="utf-8")
    cookie_file.chmod(0o600)
    server, thread = _serve_dashboard()
    original_reply = _DashboardHandler._reply

    def unsafe_error_reply(self, status, payload):
        if self.path == "/api/open-wearables/providers":
            payload["error_code"] = "token=must-not-leak"
        return original_reply(self, status, payload)

    _DashboardHandler._reply = unsafe_error_reply
    try:
        report = smoke.run_smoke(
            dashboard_url=f"http://127.0.0.1:{server.server_port}",
            sidecar_url="http://127.0.0.1:8000",
            cookie_file=cookie_file,
        )
    finally:
        _DashboardHandler._reply = original_reply
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert report["checks"]["provider_catalog"]["error_code"] == "smoke_redacted_error"
    assert "must-not-leak" not in json.dumps(report)


def test_malformed_setup_config_returns_stable_invalid_response(tmp_path) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_text("session=test-session-secret\n", encoding="utf-8")
    cookie_file.chmod(0o600)
    server, thread = _serve_dashboard()
    original_reply = _DashboardHandler._reply

    def malformed_setup_reply(self, status, payload):
        if self.path == "/api/open-wearables/setup/check":
            payload["config"] = ["unexpected"]
        return original_reply(self, status, payload)

    _DashboardHandler._reply = malformed_setup_reply
    try:
        report = smoke.run_smoke(
            dashboard_url=f"http://127.0.0.1:{server.server_port}",
            sidecar_url="http://127.0.0.1:8000",
            cookie_file=cookie_file,
        )
    finally:
        _DashboardHandler._reply = original_reply
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert report["status"] == "blocked"
    assert report["error_code"] == "smoke_invalid_response"


def test_malformed_bracketed_url_returns_stable_invalid_result(tmp_path) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_text("session=test-session-secret\n", encoding="utf-8")
    cookie_file.chmod(0o600)

    report = smoke.run_smoke(
        dashboard_url="http://[::1",
        sidecar_url="http://127.0.0.1:8000",
        cookie_file=cookie_file,
    )

    assert report == {
        "status": "blocked",
        "error_code": "smoke_invalid_url",
        "checks": {},
    }


class _BadHttpHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        self.request.recv(4096)
        self.request.sendall(b"this is not an HTTP status line\r\n\r\n")


def test_malformed_http_response_returns_stable_unavailable_result(tmp_path) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_text("session=test-session-secret\n", encoding="utf-8")
    cookie_file.chmod(0o600)
    server = socketserver.TCPServer(("127.0.0.1", 0), _BadHttpHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        report = smoke.run_smoke(
            dashboard_url=f"http://127.0.0.1:{server.server_address[1]}",
            sidecar_url="http://127.0.0.1:8000",
            cookie_file=cookie_file,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert report == {
        "status": "blocked",
        "error_code": "smoke_dashboard_unavailable",
        "checks": {},
    }


@pytest.mark.parametrize("authorization_url", ["http://[::1", "https://provider.invalid:not-a-port/oauth"])
def test_malformed_authorization_url_returns_stable_ready_failure(tmp_path, authorization_url) -> None:
    cookie_file = tmp_path / "cookie"
    cookie_file.write_text("session=test-session-secret\n", encoding="utf-8")
    cookie_file.chmod(0o600)
    server, thread = _serve_dashboard()
    original_reply = _DashboardHandler._reply

    def malformed_url_reply(self, status, payload):
        if self.path == "/api/open-wearables/pair/ready_watch":
            payload["authorization_url"] = authorization_url
        return original_reply(self, status, payload)

    _DashboardHandler._reply = malformed_url_reply
    try:
        report = smoke.run_smoke(
            dashboard_url=f"http://127.0.0.1:{server.server_port}",
            sidecar_url="http://127.0.0.1:8000",
            cookie_file=cookie_file,
        )
    finally:
        _DashboardHandler._reply = original_reply
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)

    assert report["status"] == "blocked"
    assert report["error_code"] == "smoke_ready_provider_failed"
