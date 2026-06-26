#!/usr/bin/env python3
"""Run a bounded WHOOP sync worker from the command line.

This entry point defines the operational contract for scheduled and manual
WHOOP sync work without creating launchd/cron automation itself.
"""

from __future__ import annotations

import argparse
import importlib
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BACKEND_ENV_VAR = "WHOOP_SYNC_BACKEND"
DEFAULT_BACKEND_SPEC = "app:_run_whoop_sync"


@dataclass(frozen=True)
class ModePolicy:
    default_days: int
    min_days: int
    max_days: int
    description: str


MODE_POLICIES: dict[str, ModePolicy] = {
    "normal": ModePolicy(
        default_days=2,
        min_days=1,
        max_days=14,
        description="Routine polling window for scheduled or local manual sync checks.",
    ),
    "backfill": ModePolicy(
        default_days=30,
        min_days=30,
        max_days=90,
        description="Initial historical import window, capped to the FIT-239 30-90 day contract.",
    ),
    "repair": ModePolicy(
        default_days=7,
        min_days=1,
        max_days=14,
        description="Short reconciliation window for edited sleeps/workouts and late scoring updates.",
    ),
}

MAX_ATTEMPTS = 3
MAX_RETRY_BACKOFF_SECONDS = 30


@dataclass(frozen=True)
class SyncRequest:
    mode: str
    days: int


@dataclass(frozen=True)
class SyncResult:
    status: str
    mode: str
    days: int
    attempts: int
    message: str = ""
    sync_run_id: str = ""
    records_upserted: int = 0
    retryable: bool = False


class WhoopSyncBackend(Protocol):
    def run_sync(self, request: SyncRequest) -> Mapping[str, Any]:
        """Execute one WHOOP sync request and return a summary mapping."""


class BackendUnavailableError(RuntimeError):
    """Raised when no real WHOOP sync backend has been wired yet."""


class AppSyncTransientError(RuntimeError):
    retryable = True

    def __init__(self, message: str):
        super().__init__(message)
        self.redacted_message = message


class AppWhoopSyncBackend:
    def run_sync(self, request: SyncRequest) -> Mapping[str, Any]:
        app_module = importlib.import_module("app")
        with app_module.app.app_context():
            result, error_response = app_module._run_whoop_sync(request.mode, days_back=request.days)
        if error_response is not None:
            response = error_response[0] if isinstance(error_response, tuple) else error_response
            status_code = error_response[1] if isinstance(error_response, tuple) and len(error_response) > 1 else 500
            payload = response.get_json(silent=True) if hasattr(response, "get_json") else None
            message = (
                payload
                and payload.get("error")
                and payload["error"].get("message")
            ) or f"WHOOP sync failed with HTTP {status_code}"
            if int(status_code) >= 500:
                raise AppSyncTransientError(message)
            return {
                "status": "error",
                "message": message,
                "records_upserted": 0,
            }
        return {
            "status": "success",
            "sync_run_id": (result or {}).get("run_id", ""),
            "records_upserted": (result or {}).get("records_upserted", 0),
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog=(
            "Safety: this command does not install or schedule launchd/cron automation. "
            f"Override {BACKEND_ENV_VAR} only when replacing the default app-backed sync adapter."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=tuple(MODE_POLICIES),
        default="normal",
        help="Sync mode: normal polling, initial backfill, or repair reconciliation.",
    )
    parser.add_argument(
        "--days",
        type=int,
        help=(
            "Requested sync window in days. Defaults are bounded per mode: "
            "normal=2, backfill=30, repair=7."
        ),
    )
    return parser


def resolve_days(mode: str, requested_days: int | None) -> int:
    policy = MODE_POLICIES[mode]
    days = policy.default_days if requested_days is None else requested_days
    if days < policy.min_days or days > policy.max_days:
        raise ValueError(
            f"--days for {mode} mode must be between {policy.min_days} and {policy.max_days}"
        )
    return days


def parse_request(argv: list[str] | None = None) -> SyncRequest:
    args = build_parser().parse_args(argv)
    return SyncRequest(mode=args.mode, days=resolve_days(args.mode, args.days))


def _load_backend_spec(spec: str) -> WhoopSyncBackend:
    if ":" not in spec:
        raise BackendUnavailableError(
            f"{BACKEND_ENV_VAR} must be set to module_path:object_name for a WHOOP sync backend"
        )
    module_name, object_name = spec.split(":", 1)
    module = importlib.import_module(module_name)
    target = getattr(module, object_name)
    backend = target() if callable(target) else target
    if not hasattr(backend, "run_sync"):
        raise BackendUnavailableError(
            f"{BACKEND_ENV_VAR} target {spec!r} must expose a run_sync(request) method"
        )
    return backend


def load_backend() -> WhoopSyncBackend:
    spec = os.environ.get(BACKEND_ENV_VAR, DEFAULT_BACKEND_SPEC).strip()
    if not spec:
        return AppWhoopSyncBackend()
    if spec == DEFAULT_BACKEND_SPEC:
        return AppWhoopSyncBackend()
    return _load_backend_spec(spec)


def _error_message(error: Exception) -> str:
    message = getattr(error, "redacted_message", None)
    if message:
        return str(message)
    return str(error) or error.__class__.__name__


def _is_retryable(error: Exception) -> bool:
    return bool(getattr(error, "retryable", False))


def _retry_delay_seconds(error: Exception, attempt: int) -> int:
    retry_after = getattr(error, "retry_after_seconds", None)
    if retry_after is not None:
        try:
            return max(0, min(int(retry_after), MAX_RETRY_BACKOFF_SECONDS))
        except (TypeError, ValueError):
            pass
    return min(2 ** (attempt - 1), MAX_RETRY_BACKOFF_SECONDS)


def _normalize_success(payload: Mapping[str, Any], request: SyncRequest, attempts: int) -> SyncResult:
    return SyncResult(
        status=str(payload.get("status", "success")),
        mode=request.mode,
        days=request.days,
        attempts=attempts,
        message=str(payload.get("message", "")),
        sync_run_id=str(payload.get("sync_run_id", "")),
        records_upserted=int(payload.get("records_upserted", 0) or 0),
        retryable=False,
    )


def run_sync(
    request: SyncRequest,
    *,
    backend: WhoopSyncBackend,
    sleep_fn=time.sleep,
    max_attempts: int = MAX_ATTEMPTS,
) -> SyncResult:
    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        try:
            payload = backend.run_sync(request)
            return _normalize_success(payload, request, attempts=attempt)
        except Exception as error:  # pragma: no cover - exercised through contract tests
            retryable = _is_retryable(error)
            message = _error_message(error)
            if retryable and attempt < max_attempts:
                sleep_fn(_retry_delay_seconds(error, attempt))
                continue
            return SyncResult(
                status="retryable_error" if retryable else "error",
                mode=request.mode,
                days=request.days,
                attempts=attempt,
                message=message,
                retryable=retryable,
            )
    return SyncResult(
        status="error",
        mode=request.mode,
        days=request.days,
        attempts=max_attempts,
        message="WHOOP sync exhausted retries without a terminal result",
    )


def _format_summary(result: SyncResult) -> str:
    parts = [
        f"status={result.status}",
        f"mode={result.mode}",
        f"days={result.days}",
        f"attempts={result.attempts}",
    ]
    if result.sync_run_id:
        parts.append(f"sync_run_id={result.sync_run_id}")
    parts.append(f"records_upserted={result.records_upserted}")
    if result.message:
        parts.append(f"message={result.message}")
    return " ".join(parts)


def main(argv: list[str] | None = None, *, backend: WhoopSyncBackend | None = None) -> int:
    request = parse_request(argv)
    try:
        active_backend = backend or load_backend()
    except BackendUnavailableError as error:
        print(_format_summary(SyncResult(
            status="backend_unavailable",
            mode=request.mode,
            days=request.days,
            attempts=0,
            message=str(error),
        )), file=sys.stderr)
        return 1

    result = run_sync(request, backend=active_backend)
    stream = sys.stdout if result.status == "success" else sys.stderr
    print(_format_summary(result), file=stream)
    return 0 if result.status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
