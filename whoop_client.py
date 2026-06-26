from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable


WHOOP_AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
WHOOP_GRANT_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
WHOOP_API_BASE_URL = "https://api.prod.whoop.com/developer/v2"
WHOOP_REVOKE_URL = "https://api.prod.whoop.com/developer/v2/user/access"
WHOOP_DEFAULT_SCOPES = (
    "offline",
    "read:recovery",
    "read:cycles",
    "read:sleep",
    "read:workout",
)
WHOOP_PROTECTED_STORE_SERVICE = "fitness-dashboard-whoop-client-secret"


class WhoopApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True)
class WhoopConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    auth_url: str = WHOOP_AUTH_URL
    token_url: str = WHOOP_GRANT_URL
    api_base_url: str = WHOOP_API_BASE_URL
    revoke_url: str = WHOOP_REVOKE_URL
    scopes: tuple[str, ...] = WHOOP_DEFAULT_SCOPES


def _repo_client_id_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".whoop-client-id")


def _load_client_id(client_id_path: str | None = None) -> str | None:
    candidate = client_id_path or os.environ.get("WHOOP_CLIENT_ID_FILE") or _repo_client_id_path()
    if not candidate or not os.path.exists(candidate):
        return None
    try:
        with open(candidate, "r", encoding="utf-8") as handle:
            value = handle.read().strip()
    except OSError:
        return None
    return value or None


def _load_protected_client_material(store_service: str = WHOOP_PROTECTED_STORE_SERVICE) -> str | None:
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", store_service, "-w"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def load_whoop_config(
    redirect_uri: str,
    *,
    client_id_path: str | None = None,
    keychain_service: str = WHOOP_PROTECTED_STORE_SERVICE,
) -> WhoopConfig:
    client_id = _load_client_id(client_id_path=client_id_path)
    protected_value = _load_protected_client_material(store_service=keychain_service)
    scopes_env = os.environ.get("WHOOP_SCOPES", "").strip()
    scopes = tuple(s.strip() for s in scopes_env.split() if s.strip()) if scopes_env else WHOOP_DEFAULT_SCOPES
    if not client_id or not protected_value:
        raise ValueError("WHOOP OAuth is not fully configured on this server.")
    return WhoopConfig(
        client_id,
        protected_value,
        redirect_uri,
        WHOOP_AUTH_URL,
        WHOOP_GRANT_URL,
        WHOOP_API_BASE_URL,
        WHOOP_REVOKE_URL,
        scopes,
    )


def create_whoop_authorization_url(config: WhoopConfig, *, state: str) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": config.client_id,
            "redirect_uri": config.redirect_uri,
            "response_type": "code",
            "scope": " ".join(config.scopes),
            "state": state,
        }
    )
    return f"{config.auth_url}?{query}"


def _token_form_payload(config: WhoopConfig, **extra: str) -> bytes:
    payload = {
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        **extra,
    }
    return urllib.parse.urlencode(payload).encode("utf-8")


def redact_whoop_error(value: object) -> str:
    text = "" if value is None else str(value)
    redacted = text
    replacements = [
        (r"(?i)(access_token=)[^&\s]+", r"\1[redacted]"),
        (r"(?i)(refresh_token=)[^&\s]+", r"\1[redacted]"),
        (r"(?i)(client_secret=)[^&\s]+", r"\1[redacted]"),
        (r"(?i)(clientSecret=)[^&\s]+", r"\1[redacted]"),
        (r"(?i)(code=)[^&\s]+", r"\1[redacted]"),
        (r"(?i)(state=)[^&\s]+", r"\1[redacted]"),
        (r"(?i)(authorization:\s*bearer\s+)[^\s,;]+", r"\1[redacted]"),
        (r'(?i)("access_token"\s*:\s*")[^"]+(")', r"\1[redacted]\2"),
        (r'(?i)("refresh_token"\s*:\s*")[^"]+(")', r"\1[redacted]\2"),
        (r'(?i)("client_secret"\s*:\s*")[^"]+(")', r"\1[redacted]\2"),
        (r'(?i)("clientSecret"\s*:\s*")[^"]+(")', r"\1[redacted]\2"),
        (r'(?i)("code"\s*:\s*")[^"]+(")', r"\1[redacted]\2"),
        (r'(?i)("state"\s*:\s*")[^"]+(")', r"\1[redacted]\2"),
    ]
    for pattern, replacement in replacements:
        redacted = re.sub(pattern, replacement, redacted)
    return redacted


def _decode_json_response(response) -> dict:
    raw = response.read().decode("utf-8")
    payload = json.loads(raw or "{}")
    return payload if isinstance(payload, dict) else {}


def exchange_whoop_code(
    config: WhoopConfig,
    *,
    code: str,
    urlopen: Callable = urllib.request.urlopen,
) -> dict:
    request = urllib.request.Request(
        config.token_url,
        data=_token_form_payload(
            config,
            grant_type="authorization_code",
            code=code,
            redirect_uri=config.redirect_uri,
        ),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            return _decode_json_response(response)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        raise WhoopApiError(
            redact_whoop_error(detail or f"WHOOP token exchange failed with HTTP {exc.code}"),
            status_code=exc.code,
            retryable=exc.code >= 500,
        ) from exc
    except urllib.error.URLError as exc:
        raise WhoopApiError(
            redact_whoop_error(f"WHOOP token exchange failed: {exc.reason}"),
            retryable=True,
        ) from exc


def refresh_whoop_token(
    config: WhoopConfig,
    *,
    renewal_value: str,
    urlopen: Callable = urllib.request.urlopen,
) -> dict:
    grant_fields = {
        "grant_type": "refresh_token",
        "refresh_token": renewal_value,
        "scope": "offline",
    }
    request = urllib.request.Request(
        config.token_url,
        data=_token_form_payload(config, **grant_fields),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            return _decode_json_response(response)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        retryable = exc.code in {429, 500, 502, 503, 504}
        raise WhoopApiError(
            redact_whoop_error(detail or f"WHOOP token refresh failed with HTTP {exc.code}"),
            status_code=exc.code,
            retryable=retryable,
        ) from exc
    except urllib.error.URLError as exc:
        raise WhoopApiError(
            redact_whoop_error(f"WHOOP token refresh failed: {exc.reason}"),
            retryable=True,
        ) from exc


def revoke_whoop_access(
    config: WhoopConfig,
    *,
    session_value: str,
    urlopen: Callable = urllib.request.urlopen,
) -> None:
    request = urllib.request.Request(
        config.revoke_url,
        headers={"Authorization": f"Bearer {session_value}"},
        method="DELETE",
    )
    try:
        with urlopen(request, timeout=15):
            return None
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        raise WhoopApiError(
            redact_whoop_error(detail or f"WHOOP revoke failed with HTTP {exc.code}"),
            status_code=exc.code,
            retryable=exc.code in {429, 500, 502, 503, 504},
        ) from exc
    except urllib.error.URLError as exc:
        raise WhoopApiError(
            redact_whoop_error(f"WHOOP revoke failed: {exc.reason}"),
            retryable=True,
        ) from exc


def _utc_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class WhoopClient:
    def __init__(
        self,
        config: WhoopConfig,
        *,
        session_value: str,
        renewal_value: str | None = None,
        urlopen: Callable = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 2,
    ):
        self.config = config
        self.access_token = session_value
        self.refresh_token = renewal_value
        self.urlopen = urlopen
        self.sleep = sleep
        self.max_retries = max_retries

    def _headers(self, *, limit: int | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.access_token}"}
        if limit is not None:
            headers["limit"] = str(limit)
        return headers

    def _request_json(self, path: str, *, query: dict[str, str] | None = None, limit: int | None = None) -> dict:
        url = f"{self.config.api_base_url}{path}"
        if query:
            url += f"?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url, headers=self._headers(limit=limit), method="GET")
        try:
            with self.urlopen(request, timeout=15) as response:
                return _decode_json_response(response)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                detail = ""
            raise WhoopApiError(
                redact_whoop_error(detail or f"WHOOP API request failed with HTTP {exc.code}"),
                status_code=exc.code,
                retryable=exc.code in {429, 500, 502, 503, 504},
            ) from exc
        except urllib.error.URLError as exc:
            raise WhoopApiError(
                redact_whoop_error(f"WHOOP API request failed: {exc.reason}"),
                retryable=True,
            ) from exc
        except (TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WhoopApiError(
                redact_whoop_error(f"WHOOP API request failed: {type(exc).__name__}"),
                retryable=True,
            ) from exc

    def refresh_access_token(self) -> dict:
        if not self.refresh_token:
            raise WhoopApiError("WHOOP refresh token is unavailable.", retryable=False)
        grant_payload = refresh_whoop_token(
            self.config,
            renewal_value=self.refresh_token,
            urlopen=self.urlopen,
        )
        self.access_token = grant_payload.get("access_token") or self.access_token
        self.refresh_token = grant_payload.get("refresh_token") or self.refresh_token
        return grant_payload

    def fetch_collection(
        self,
        path: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        records: list[dict] = []
        cursor: str | None = None
        while True:
            query: dict[str, str] = {}
            if start is not None:
                query["start"] = _utc_iso(start)
            if end is not None:
                query["end"] = _utc_iso(end)
            if cursor:
                query["nextToken"] = cursor
            retry_attempts = 0
            refreshed = False
            while True:
                try:
                    payload = self._request_json(path, query=query or None, limit=limit)
                    break
                except WhoopApiError as exc:
                    if exc.status_code == 401 and self.refresh_token and not refreshed:
                        self.refresh_access_token()
                        refreshed = True
                        continue
                    if exc.retryable and retry_attempts < self.max_retries:
                        self.sleep(min(2 ** retry_attempts, 4))
                        retry_attempts += 1
                        continue
                    raise
            page_records = payload.get("records") or []
            if isinstance(page_records, list):
                records.extend(item for item in page_records if isinstance(item, dict))
            cursor = payload.get("next_token") or None
            if not cursor:
                break
        return records

    def fetch_recovery(self, *, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
        return self.fetch_collection("/recovery", start=start, end=end)

    def fetch_cycles(self, *, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
        return self.fetch_collection("/cycle", start=start, end=end)

    def fetch_sleep(self, *, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
        return self.fetch_collection("/activity/sleep", start=start, end=end)

    def fetch_workouts(self, *, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
        return self.fetch_collection("/activity/workout", start=start, end=end)
