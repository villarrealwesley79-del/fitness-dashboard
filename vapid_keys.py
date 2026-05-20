"""VAPID keypair persistence for Web Push subscription setup."""

from __future__ import annotations

import base64
import json
import os
import secrets
import time
from datetime import datetime
from json import JSONDecodeError
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


VAPID_KEYS_FILENAME = ".vapid_keys.json"


def _keys_file() -> Path:
    configured = os.environ.get("VAPID_KEYS_FILE")
    if configured:
        return Path(configured).expanduser()
    data_dir = os.environ.get("DATA_DIR")
    base_dir = Path(data_dir).expanduser() if data_dir else Path(__file__).parent
    return base_dir / VAPID_KEYS_FILENAME


def _base64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _public_key_for(private_key: ec.EllipticCurvePrivateKey) -> str:
    numbers = private_key.public_key().public_numbers()
    raw = b"\x04" + numbers.x.to_bytes(32, "big") + numbers.y.to_bytes(32, "big")
    return _base64url(raw)


def _generate_payload() -> dict[str, str]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    return {
        "private_key_pem": private_pem,
        "public_key": _public_key_for(private_key),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def _read_payload(path: Path) -> dict[str, str]:
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict) or not isinstance(payload.get("public_key"), str):
        raise RuntimeError(f"invalid VAPID key file: {path}")
    return payload


def _read_public_key_when_ready(path: Path, *, attempts: int = 5) -> str:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return _read_payload(path)["public_key"]
        except (JSONDecodeError, OSError, RuntimeError) as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            time.sleep(0.02)
    raise RuntimeError(f"invalid VAPID key file: {path}") from last_error


def _write_private_payload(path: Path, payload: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    try:
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.link(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def get_vapid_public_key() -> str:
    """Return a stable base64url-encoded uncompressed P-256 public key."""
    path = _keys_file()
    if path.exists():
        return _read_public_key_when_ready(path)
    payload = _generate_payload()
    try:
        _write_private_payload(path, payload)
    except FileExistsError:
        return _read_public_key_when_ready(path)
    return payload["public_key"]
