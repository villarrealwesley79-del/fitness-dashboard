"""Protected-secret storage: macOS Keychain with a 0600 file fallback."""

from __future__ import annotations

import base64
import ctypes
import os
import sys
import tempfile
from subprocess import SubprocessError


PROTECTED_SECRET_FILE_PREFIX = "fd-protected-secret-v1:"


def _protected_secret_uses_keychain(fallback_override_env: str = "") -> bool:
    if sys.platform != "darwin":
        return False
    if fallback_override_env and os.environ.get(fallback_override_env, "").strip():
        return False
    return True


def _write_protected_secret_file(path: str, secret_value: str) -> None:
    target = os.path.abspath(os.path.expanduser(path))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f".{os.path.basename(target)}.", dir=os.path.dirname(target))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_encode_protected_secret_file_value(secret_value))
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, target)
        os.chmod(target, 0o600)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _encode_protected_secret_file_value(secret_value: str) -> str:
    encoded = base64.urlsafe_b64encode(secret_value.encode("utf-8")).decode("ascii")
    return f"{PROTECTED_SECRET_FILE_PREFIX}{encoded}"


def _decode_protected_secret_file_value(raw_value: str) -> str:
    if not raw_value.startswith(PROTECTED_SECRET_FILE_PREFIX):
        return raw_value
    encoded = raw_value[len(PROTECTED_SECRET_FILE_PREFIX):]
    try:
        return base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def _read_raw_protected_secret_file(path: str) -> str:
    try:
        with open(os.path.abspath(os.path.expanduser(path)), "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def _read_protected_secret_file(path: str) -> str:
    return _decode_protected_secret_file_value(_read_raw_protected_secret_file(path))


def _delete_protected_secret_file(path: str) -> None:
    try:
        os.unlink(os.path.abspath(os.path.expanduser(path)))
    except FileNotFoundError:
        return


def _macos_security_framework():
    framework = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
    framework.SecKeychainAddGenericPassword.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    framework.SecKeychainAddGenericPassword.restype = ctypes.c_int32
    framework.SecKeychainFindGenericPassword.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    framework.SecKeychainFindGenericPassword.restype = ctypes.c_int32
    framework.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    framework.SecKeychainItemFreeContent.restype = ctypes.c_int32
    framework.SecKeychainItemDelete.argtypes = [ctypes.c_void_p]
    framework.SecKeychainItemDelete.restype = ctypes.c_int32
    return framework


def _macos_keychain_add(service: str, account: str, secret_value: str) -> None:
    framework = _macos_security_framework()
    service_bytes = service.encode("utf-8")
    account_bytes = account.encode("utf-8")
    secret_bytes = secret_value.encode("utf-8")
    status = framework.SecKeychainAddGenericPassword(
        None,
        len(service_bytes),
        service_bytes,
        len(account_bytes),
        account_bytes,
        len(secret_bytes),
        secret_bytes,
        None,
    )
    if status != 0:
        raise RuntimeError(f"Could not save protected secret (Keychain status {status}).")


def _keychain_find(service: str, account: str) -> str:
    framework = _macos_security_framework()
    service_bytes = service.encode("utf-8")
    account_bytes = account.encode("utf-8")
    length = ctypes.c_uint32()
    data = ctypes.c_void_p()
    item = ctypes.c_void_p()
    status = framework.SecKeychainFindGenericPassword(
        None,
        len(service_bytes),
        service_bytes,
        len(account_bytes),
        account_bytes,
        ctypes.byref(length),
        ctypes.byref(data),
        ctypes.byref(item),
    )
    if status == -25300:
        return ""
    if status != 0:
        raise RuntimeError(f"Could not load protected secret (Keychain status {status}).")
    try:
        return ctypes.string_at(data.value, length.value).decode("utf-8")
    finally:
        framework.SecKeychainItemFreeContent(None, data)


def _keychain_save(service: str, account: str, secret_value: str) -> None:
    _keychain_delete(service, account)
    _macos_keychain_add(service, account, secret_value)


def _keychain_delete(service: str, account: str) -> None:
    framework = _macos_security_framework()
    service_bytes = service.encode("utf-8")
    account_bytes = account.encode("utf-8")
    length = ctypes.c_uint32()
    data = ctypes.c_void_p()
    item = ctypes.c_void_p()
    status = framework.SecKeychainFindGenericPassword(
        None,
        len(service_bytes),
        service_bytes,
        len(account_bytes),
        account_bytes,
        ctypes.byref(length),
        ctypes.byref(data),
        ctypes.byref(item),
    )
    if status == -25300:
        return
    if status != 0:
        raise RuntimeError(f"Could not find protected secret for deletion (Keychain status {status}).")
    try:
        delete_status = framework.SecKeychainItemDelete(item)
    finally:
        framework.SecKeychainItemFreeContent(None, data)
    if delete_status != 0:
        raise RuntimeError(f"Could not delete protected secret (Keychain status {delete_status}).")


def save_protected_secret(
    service: str,
    account: str,
    secret_value: str,
    *,
    fallback_path: str,
    fallback_override_env: str = "",
) -> str:
    if _protected_secret_uses_keychain(fallback_override_env):
        try:
            _keychain_save(service, account, secret_value)
            _delete_protected_secret_file(fallback_path)
            return "keychain"
        except (OSError, RuntimeError, SubprocessError):
            pass
    _write_protected_secret_file(fallback_path, secret_value)
    return "file"


def load_protected_secret(
    service: str,
    account: str,
    *,
    fallback_path: str,
    fallback_override_env: str = "",
) -> str:
    fallback_raw = _read_raw_protected_secret_file(fallback_path)
    if fallback_raw.startswith(PROTECTED_SECRET_FILE_PREFIX):
        return _decode_protected_secret_file_value(fallback_raw)
    if _protected_secret_uses_keychain(fallback_override_env):
        try:
            secret_value = _keychain_find(service, account)
        except (OSError, RuntimeError, SubprocessError):
            secret_value = ""
        if secret_value:
            return secret_value
    return _decode_protected_secret_file_value(fallback_raw)


def delete_protected_secret(
    service: str,
    account: str,
    *,
    fallback_path: str,
    fallback_override_env: str = "",
) -> None:
    keychain_error = None
    if _protected_secret_uses_keychain(fallback_override_env):
        try:
            _keychain_delete(service, account)
        except (OSError, RuntimeError, SubprocessError) as exc:
            keychain_error = exc
    if keychain_error is not None:
        raise keychain_error
    _delete_protected_secret_file(fallback_path)
