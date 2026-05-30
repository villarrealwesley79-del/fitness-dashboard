"""Shared runtime configuration for local and container deployments."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = os.environ.get("DATA_DIR", "").strip() or str(APP_DIR)
PUBLIC_BASE_URL_ENV = "FITNESS_DASHBOARD_PUBLIC_BASE_URL"


def data_path(filename: str) -> str:
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    return str(Path(DATA_DIR) / filename)


def clean_public_url(value: str) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    if "://" not in value:
        value = f"https://{value}"
    return value


def public_base_url(request_obj: Any | None = None) -> str:
    configured = clean_public_url(os.environ.get(PUBLIC_BASE_URL_ENV, ""))
    if configured:
        return configured
    if request_obj is None:
        return ""

    host = (
        request_obj.headers.get("X-Forwarded-Host")
        or request_obj.headers.get("Host")
        or request_obj.host
        or "127.0.0.1:5050"
    ).split(",", 1)[0].strip()
    scheme = (
        request_obj.headers.get("X-Forwarded-Proto")
        or request_obj.scheme
        or "https"
    ).split(",", 1)[0].strip()
    hostname = host.rsplit(":", 1)[0].lower()
    if hostname.endswith(".ts.net"):
        scheme = "https"
    return f"{scheme}://{host}".rstrip("/")
