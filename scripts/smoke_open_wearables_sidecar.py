#!/usr/bin/env python3
"""Secret-safe live smoke check for a configured local Open Wearables sidecar.

The command talks only to Fitness Dashboard's owner-authenticated API. Raw
responses are reduced immediately to allowlisted status fields so neither
health payloads nor authorization URLs can enter stdout or report artifacts.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


LOCAL_SIDECAR_HOSTS = {"localhost", "127.0.0.1", "::1"}
POST_HEADERS = {"X-Requested-With": "XMLHttpRequest"}
PUBLIC_SYNC_ERROR_FIELDS = {"activity_summary", "auth", "config", "sleep", "sync", "workouts"}
PUBLIC_SYNC_ERROR_CODES = {
    "open_wearables_auth_error",
    "open_wearables_config_error",
    "open_wearables_sync_error",
    "open_wearables_sync_failed",
}
PAIR_BLOCK_CODES = {
    "hub_restart_needed",
    "prepare_profile",
    "provider_app_needed",
    "provider_catalog_unavailable",
    "provider_disabled",
    "provider_not_ready",
    "sdk_provider",
}
PUBLIC_RESPONSE_ERROR_CODES = PUBLIC_SYNC_ERROR_CODES | PAIR_BLOCK_CODES | {
    "base_not_allowed",
    "cloud_provider",
    "config_save_failed",
    "credential_required_for_host_change",
    "credential_required_for_user_mapping",
    "invalid_field",
    "invalid_url",
    "invite_create_failed",
    "missing_config",
    "missing_user_mapping",
    "open_wearables_no_providers",
    "provider_fetch_failed",
    "public_hub_url_required",
    "remote_host_not_allowed",
    "remote_requires_tls",
    "sidecar_env_missing",
    "sidecar_env_unreadable",
    "user_mapping_required_for_host_change",
    "user_mapping_verification_failed",
}


class SmokeBlocked(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise SmokeBlocked("smoke_redirect_refused")


def _blocked(code: str, checks: dict | None = None) -> dict:
    return {"status": "blocked", "error_code": code, "checks": checks or {}}


def _validated_url(raw: str, *, require_loopback: bool) -> str:
    try:
        parsed = urllib.parse.urlsplit(str(raw or "").strip())
        parsed.port
    except ValueError as exc:
        raise SmokeBlocked("smoke_invalid_url") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SmokeBlocked("smoke_invalid_url")
    if require_loopback and parsed.hostname.lower() not in LOCAL_SIDECAR_HOSTS:
        raise SmokeBlocked("smoke_sidecar_not_local")
    if not require_loopback and parsed.scheme == "http" and parsed.hostname.lower() not in LOCAL_SIDECAR_HOSTS:
        raise SmokeBlocked("smoke_dashboard_insecure")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _read_cookie_file(path: Path) -> str:
    try:
        info = path.stat()
    except OSError as exc:
        raise SmokeBlocked("smoke_cookie_file_missing") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SmokeBlocked("smoke_cookie_file_unsafe")
    try:
        cookie = path.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError as exc:
        raise SmokeBlocked("smoke_cookie_file_invalid") from exc
    except OSError as exc:
        raise SmokeBlocked("smoke_cookie_file_unreadable") from exc
    if not cookie or "\n" in cookie or "\r" in cookie:
        raise SmokeBlocked("smoke_cookie_file_invalid")
    return cookie


def _error_code(payload: dict, default: str | None = None) -> str | None:
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        code = error["code"]
    else:
        raw_code = payload.get("error_code")
        code = raw_code if isinstance(raw_code, str) and raw_code else default
    if not code:
        return None
    return code if code in PUBLIC_RESPONSE_ERROR_CODES else "smoke_redacted_error"


def _request_json(
    dashboard_url: str,
    path: str,
    *,
    cookie: str,
    payload: dict | None = None,
    timeout_s: float = 8,
) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json", "Cookie": cookie}
    if data is not None:
        headers.update(POST_HEADERS)
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{dashboard_url}{path}",
        data=data,
        headers=headers,
        method="POST" if data is not None else "GET",
    )
    try:
        response = urllib.request.build_opener(_RejectRedirects).open(request, timeout=timeout_s)
    except urllib.error.HTTPError as exc:
        response = exc
    except (http.client.HTTPException, OSError, urllib.error.URLError, TimeoutError) as exc:
        raise SmokeBlocked("smoke_dashboard_unavailable") from exc
    try:
        status = int(response.status)
        try:
            body = response.read()
        except (http.client.IncompleteRead, OSError, TimeoutError) as exc:
            raise SmokeBlocked("smoke_dashboard_unavailable") from exc
    finally:
        response.close()
    try:
        decoded = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SmokeBlocked("smoke_invalid_response") from exc
    if not isinstance(decoded, dict):
        raise SmokeBlocked("smoke_invalid_response")
    if status == 401:
        raise SmokeBlocked("smoke_auth_required")
    if status == 403:
        raw_error = decoded.get("error")
        raw_code = raw_error.get("code") if isinstance(raw_error, dict) else decoded.get("code")
        raise SmokeBlocked("smoke_csrf_required" if raw_code == "csrf_required" else "smoke_owner_access_required")
    return status, decoded


def _provider_paths(setup: dict) -> tuple[str, str, str]:
    actions = (setup.get("config") or {}).get("provider_actions")
    if isinstance(actions, list):
        ready = next((str(row.get("provider") or "") for row in actions if isinstance(row, dict) and row.get("enabled")), "")
        blocked_action = next(
            (
                row
                for row in actions
                if isinstance(row, dict) and not row.get("enabled") and row.get("reason")
            ),
            {},
        )
        blocked = str(blocked_action.get("provider") or "")
        blocked_reason = str(blocked_action.get("reason") or "")
        if ready and blocked and blocked_reason in PAIR_BLOCK_CODES:
            return blocked, ready, blocked_reason
    return "", "", ""


def _valid_authorization_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        parsed.port
        hostname = parsed.hostname
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(hostname)


def run_smoke(*, dashboard_url: str, sidecar_url: str, cookie_file: Path, timeout_s: float = 8) -> dict:
    checks: dict[str, dict] = {}
    try:
        dashboard_url = _validated_url(dashboard_url, require_loopback=False)
        sidecar_url = _validated_url(sidecar_url, require_loopback=True)
        cookie = _read_cookie_file(Path(cookie_file))

        catalog_status, catalog = _request_json(
            dashboard_url,
            "/api/open-wearables/providers",
            cookie=cookie,
            timeout_s=timeout_s,
        )
        providers = catalog.get("providers")
        provider_count = len(providers) if isinstance(providers, list) else 0
        catalog_error = _error_code(catalog)
        checks["provider_catalog"] = {
            "status": (
                "pass"
                if catalog_status == 200
                and catalog.get("status") == "connected"
                and provider_count
                and not catalog_error
                else "blocked"
            ),
            "provider_count": provider_count,
            "error_code": catalog_error,
        }
        if checks["provider_catalog"]["status"] != "pass":
            return _blocked("smoke_provider_catalog_failed", checks)

        setup_status, setup = _request_json(
            dashboard_url,
            "/api/open-wearables/setup/check",
            cookie=cookie,
            payload={"base_url": sidecar_url},
            timeout_s=timeout_s,
        )
        config = setup.get("config")
        if not isinstance(config, dict):
            raise SmokeBlocked("smoke_invalid_response")
        provider_check = setup.get("provider_check") if isinstance(setup.get("provider_check"), dict) else {}
        setup_error = _error_code(setup) or _error_code({"error_code": provider_check.get("error_code")})
        configured_sidecar = str(config.get("base_url") or "").rstrip("/")
        sidecar_matches = configured_sidecar == sidecar_url
        if not sidecar_matches and not setup_error:
            setup_error = "sidecar_mismatch"
        checks["setup_check"] = {
            "status": (
                "pass"
                if setup_status == 200
                and setup.get("status") == "ok"
                and provider_check.get("checked")
                and sidecar_matches
                else "blocked"
            ),
            "error_code": setup_error,
        }
        if checks["setup_check"]["status"] != "pass":
            return _blocked("smoke_setup_check_failed", checks)

        blocked_provider, ready_provider, blocked_reason = _provider_paths(setup)
        if not blocked_provider or not ready_provider:
            return _blocked("smoke_provider_paths_missing", checks)

        sync_status, sync = _request_json(
            dashboard_url,
            "/api/open-wearables/sync",
            cookie=cookie,
            payload={},
            timeout_s=timeout_s,
        )
        counts = sync.get("counts") if isinstance(sync.get("counts"), dict) else {}
        raw_sync_errors = sync.get("errors") if isinstance(sync.get("errors"), dict) else {}
        sync_errors = {str(key): value for key, value in raw_sync_errors.items() if value}
        sync_error = _error_code(sync)
        if sync_errors and not sync_error:
            sync_error = next(
                (value for value in sync_errors.values() if isinstance(value, str) and value in PUBLIC_SYNC_ERROR_CODES),
                "open_wearables_sync_failed",
            )
        checks["metadata_sync"] = {
            "status": (
                "pass"
                if sync_status == 200 and sync.get("status") == "success" and not sync_errors
                else "blocked"
            ),
            "count_fields": sorted(str(key) for key in counts),
            "error_code": sync_error,
        }
        if sync_errors:
            checks["metadata_sync"]["error_fields"] = sorted(
                key if key in PUBLIC_SYNC_ERROR_FIELDS else "sync" for key in sync_errors
            )
        if checks["metadata_sync"]["status"] != "pass":
            return _blocked("smoke_metadata_sync_failed", checks)

        blocked_status, blocked_payload = _request_json(
            dashboard_url,
            f"/api/open-wearables/pair/{urllib.parse.quote(blocked_provider, safe='')}",
            cookie=cookie,
            payload={},
            timeout_s=timeout_s,
        )
        blocked_error = _error_code(blocked_payload)
        checks["blocked_provider"] = {
            "status": (
                "pass"
                if blocked_status == 400
                and blocked_payload.get("status") == "blocked"
                and blocked_payload.get("provider") == blocked_provider
                and blocked_error == blocked_reason
                else "blocked"
            ),
            "provider": blocked_provider,
            "error_code": blocked_error,
        }
        if checks["blocked_provider"]["status"] != "pass":
            return _blocked("smoke_blocked_provider_failed", checks)

        ready_status, ready_payload = _request_json(
            dashboard_url,
            f"/api/open-wearables/pair/{urllib.parse.quote(ready_provider, safe='')}",
            cookie=cookie,
            payload={},
            timeout_s=timeout_s,
        )
        authorization_url = str(ready_payload.get("authorization_url") or "").strip()
        authorization_ready = _valid_authorization_url(authorization_url)
        checks["ready_provider"] = {
            "status": (
                "pass"
                if ready_status == 200
                and ready_payload.get("status") == "ready"
                and ready_payload.get("provider") == ready_provider
                and authorization_ready
                else "blocked"
            ),
            "provider": ready_provider,
            "error_code": _error_code(ready_payload),
        }
        if checks["ready_provider"]["status"] != "pass":
            return _blocked("smoke_ready_provider_failed", checks)
    except SmokeBlocked as exc:
        return _blocked(exc.code, checks)

    return {"status": "pass", "checks": checks}


def _write_report(path: Path, report: dict) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard-url", default="http://127.0.0.1:5000")
    parser.add_argument("--sidecar-url", default="http://localhost:8000")
    parser.add_argument("--cookie-file", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="Optional 0600 JSON summary (never raw responses)")
    parser.add_argument("--timeout", type=float, default=8)
    args = parser.parse_args(argv)

    report = run_smoke(
        dashboard_url=args.dashboard_url,
        sidecar_url=args.sidecar_url,
        cookie_file=args.cookie_file,
        timeout_s=args.timeout,
    )
    if args.output:
        _write_report(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("status") == "pass" else 2


if __name__ == "__main__":
    sys.exit(main())
