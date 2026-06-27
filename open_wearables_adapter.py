"""Safe Open Wearables adapter primitives.

Fitness Dashboard treats Open Wearables as an external hub. This module keeps
the app-facing shape sanitized and provider-generic.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import os
from urllib.parse import urlparse


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True)
class OpenWearablesProviderStatus:
    provider_id: str
    label: str
    state: str
    capabilities: dict[str, bool]
    last_sync_at: str | None = None
    stale: bool = False
    error_code: str | None = None

    def public_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class OpenWearablesStatus:
    status: str
    configured: bool
    base_url: str | None
    user_mapped: bool
    auth_configured: bool
    last_checked_at: str
    providers: list[OpenWearablesProviderStatus]
    error_code: str | None = None

    def public_dict(self) -> dict:
        payload = asdict(self)
        payload["providers"] = [p.public_dict() for p in self.providers]
        return payload


def redacted_base_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    host = parsed.hostname or parsed.netloc
    try:
        port_value = parsed.port
    except ValueError:
        port_value = None
    port = f":{port_value}" if port_value else ""
    return f"{parsed.scheme}://{host}{port}"


def allowed_remote_hosts() -> set[str]:
    raw = os.environ.get("OW_ALLOWED_HOSTS", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def validate_open_wearables_base_url(url: str | None, *, allowed_hosts: set[str] | None = None) -> tuple[bool, str | None]:
    if not url:
        return True, None
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False, "invalid_url"
    try:
        parsed.port
    except ValueError:
        return False, "invalid_url"
    host = parsed.hostname.lower()
    if host in LOCAL_HOSTS:
        return True, None
    if parsed.scheme != "https":
        return False, "remote_requires_tls"
    allowed = allowed_hosts if allowed_hosts is not None else allowed_remote_hosts()
    if host not in allowed:
        return False, "remote_host_not_allowed"
    return True, None


def redact_open_wearables_error(_error: object, *, default_code: str = "open_wearables_error") -> dict:
    return {
        "code": default_code,
        "message": "Open Wearables is unavailable or returned an unsafe response.",
    }


def provider_status_from_payload(entry: dict) -> OpenWearablesProviderStatus | None:
    if not isinstance(entry, dict):
        return None
    raw_provider = str(
        entry.get("provider_id")
        or entry.get("id")
        or entry.get("provider")
        or entry.get("name")
        or ""
    ).strip()
    provider_id = raw_provider.lower().replace(" ", "_")
    if not provider_id:
        return None
    label = str(entry.get("label") or entry.get("display_name") or entry.get("name") or raw_provider or provider_id).strip()
    state = str(entry.get("state") or entry.get("status") or "unknown").strip().lower().replace(" ", "_")
    capabilities = entry.get("capabilities") if isinstance(entry.get("capabilities"), dict) else {}
    normalized_caps = {
        "metrics": bool(capabilities.get("metrics") or capabilities.get("vitals")),
        "workouts": bool(capabilities.get("workouts") or capabilities.get("events")),
        "history": bool(capabilities.get("history") or capabilities.get("backfill")),
        "webhooks": bool(capabilities.get("webhooks")),
        "sync": bool(capabilities.get("sync") or capabilities.get("manual_sync")),
    }
    return OpenWearablesProviderStatus(
        provider_id=provider_id,
        label=label,
        state=state or "unknown",
        capabilities=normalized_caps,
        last_sync_at=entry.get("last_sync_at") if isinstance(entry.get("last_sync_at"), str) else None,
        stale=bool(entry.get("stale")),
        error_code=entry.get("error_code") if isinstance(entry.get("error_code"), str) else None,
    )


def providers_from_payload(payload: object) -> list[OpenWearablesProviderStatus]:
    if isinstance(payload, dict):
        items = payload.get("providers") or payload.get("data") or payload.get("items") or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []
    providers = []
    for item in items if isinstance(items, list) else []:
        provider = provider_status_from_payload(item)
        if provider:
            providers.append(provider)
    return providers


def build_open_wearables_status(
    *,
    username: str | None,
    credential: str | None,
    user_id: str | None,
    base_url: str | None,
    allowed_hosts: set[str] | None = None,
    providers: list[OpenWearablesProviderStatus] | None = None,
    error_code: str | None = None,
) -> OpenWearablesStatus:
    auth_configured = bool(username and credential)
    user_mapped = bool(user_id)
    base_valid, base_error = validate_open_wearables_base_url(base_url, allowed_hosts=allowed_hosts)
    provider_list = providers or []
    waiting_for_provider = error_code == "open_wearables_no_providers"
    if waiting_for_provider:
        error_code = None
    configured = bool(auth_configured and user_mapped and base_valid)
    status = "connected" if configured and provider_list and not error_code else "missing_config"
    if base_error:
        status = "blocked"
        error_code = base_error
    elif error_code:
        status = "error"
    return OpenWearablesStatus(
        status=status,
        configured=configured,
        base_url=redacted_base_url(base_url),
        user_mapped=user_mapped,
        auth_configured=auth_configured,
        last_checked_at=datetime.now().isoformat(),
        providers=provider_list,
        error_code=error_code,
    )
