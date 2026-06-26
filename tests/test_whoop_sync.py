from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import scripts.whoop_sync as whoop_sync


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "whoop_sync.py"


class FakeBackend:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[whoop_sync.SyncRequest] = []

    def run_sync(self, request: whoop_sync.SyncRequest):
        self.calls.append(request)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class RetryableSyncError(RuntimeError):
    retryable = True

    def __init__(self, message: str, *, retry_after_seconds: int | None = None):
        super().__init__(message)
        self.redacted_message = message
        self.retry_after_seconds = retry_after_seconds


class FatalSyncError(RuntimeError):
    retryable = False

    def __init__(self, message: str):
        super().__init__(message)
        self.redacted_message = message


def test_resolve_days_uses_safe_mode_defaults():
    assert whoop_sync.resolve_days("normal", None) == 2
    assert whoop_sync.resolve_days("backfill", None) == 30
    assert whoop_sync.resolve_days("repair", None) == 7


@pytest.mark.parametrize(
    ("mode", "days", "expected"),
    [
        ("normal", 0, "--days for normal mode must be between 1 and 14"),
        ("backfill", 29, "--days for backfill mode must be between 30 and 90"),
        ("backfill", 91, "--days for backfill mode must be between 30 and 90"),
        ("repair", 15, "--days for repair mode must be between 1 and 14"),
    ],
)
def test_resolve_days_rejects_out_of_contract_windows(mode, days, expected):
    with pytest.raises(ValueError, match=expected):
        whoop_sync.resolve_days(mode, days)


def test_run_sync_passes_normal_request_through_backend():
    backend = FakeBackend([
        {"status": "success", "sync_run_id": "sync-123", "records_upserted": 18},
    ])
    request = whoop_sync.SyncRequest(mode="normal", days=2)

    result = whoop_sync.run_sync(request, backend=backend, sleep_fn=lambda _seconds: None)

    assert result.status == "success"
    assert result.sync_run_id == "sync-123"
    assert result.records_upserted == 18
    assert backend.calls == [request]


def test_run_sync_retries_retryable_failure_before_success():
    backend = FakeBackend([
        RetryableSyncError("rate limited", retry_after_seconds=3),
        {"status": "success", "sync_run_id": "sync-456", "records_upserted": 7},
    ])
    sleeps: list[int] = []
    request = whoop_sync.SyncRequest(mode="backfill", days=30)

    result = whoop_sync.run_sync(request, backend=backend, sleep_fn=sleeps.append)

    assert result.status == "success"
    assert result.attempts == 2
    assert sleeps == [3]
    assert backend.calls == [request, request]


def test_run_sync_returns_redacted_retryable_error_after_exhaustion():
    backend = FakeBackend([
        RetryableSyncError("rate limited"),
        RetryableSyncError("rate limited"),
        RetryableSyncError("rate limited"),
    ])
    request = whoop_sync.SyncRequest(mode="repair", days=7)

    result = whoop_sync.run_sync(request, backend=backend, sleep_fn=lambda _seconds: None)

    assert result.status == "retryable_error"
    assert result.retryable is True
    assert result.attempts == 3
    assert result.message == "rate limited"


def test_main_prints_failure_summary_for_non_retryable_error(capsys):
    backend = FakeBackend([FatalSyncError("reauth required")])

    exit_code = whoop_sync.main(["--mode", "repair"], backend=backend)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "status=error mode=repair days=7 attempts=1" in captured.err
    assert "message=reauth required" in captured.err


def test_load_backend_uses_default_app_adapter(monkeypatch):
    monkeypatch.delenv(whoop_sync.BACKEND_ENV_VAR, raising=False)

    backend = whoop_sync.load_backend()

    assert isinstance(backend, whoop_sync.AppWhoopSyncBackend)


def test_app_backend_transient_error_uses_retry_loop(monkeypatch):
    class _AppContext:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _FakeFlaskApp:
        def app_context(self):
            return _AppContext()

    class _FakeAppModule:
        app = _FakeFlaskApp()
        calls = 0

        @classmethod
        def _run_whoop_sync(cls, mode, *, days_back):
            cls.calls += 1
            if cls.calls == 1:
                class _Response:
                    def get_json(self, silent=True):
                        return {"error": {"message": "temporary WHOOP outage"}}

                return None, (_Response(), 503)
            return {"run_id": "sync-ok", "records_upserted": 2}, None

    monkeypatch.setattr(whoop_sync.importlib, "import_module", lambda name: _FakeAppModule)
    request = whoop_sync.SyncRequest(mode="normal", days=2)

    result = whoop_sync.run_sync(
        request,
        backend=whoop_sync.AppWhoopSyncBackend(),
        sleep_fn=lambda _seconds: None,
    )

    assert result.status == "success"
    assert result.attempts == 2
    assert result.sync_run_id == "sync-ok"


def test_help_text_is_safe_and_describes_bounded_modes():
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )

    assert completed.stderr == ""
    assert "--mode {normal,backfill,repair}" in completed.stdout
    assert "--days" in completed.stdout
    assert "does not install or schedule launchd/cron automation" in completed.stdout
    assert "default app-backed sync adapter" in " ".join(completed.stdout.split())
    assert "normal=2, backfill=30, repair=7" in completed.stdout
