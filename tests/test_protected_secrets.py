from __future__ import annotations

import os
import secrets
import stat
import sys

import pytest

import protected_secrets as ps


def _fake_keychain(monkeypatch, *, fail_save=False, fail_delete=False):
    stored = {}

    def save(service, account, value):
        if fail_save:
            raise RuntimeError("keychain unavailable")
        stored[(service, account)] = value

    def find(service, account):
        return stored.get((service, account), "")

    def delete(service, account):
        if fail_delete:
            raise RuntimeError("delete denied")
        stored.pop((service, account), None)

    monkeypatch.setattr(ps, "_keychain_save", save)
    monkeypatch.setattr(ps, "_keychain_find", find)
    monkeypatch.setattr(ps, "_keychain_delete", delete)
    return stored


def test_file_fallback_round_trip_is_private_and_prefixed(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_SECRET_FILE", "1")
    path = tmp_path / "secret"

    where = ps.save_protected_secret(
        "svc", "acct", "s3cret", fallback_path=str(path), fallback_override_env="TEST_SECRET_FILE"
    )

    assert where == "file"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert path.read_text().startswith(ps.PROTECTED_SECRET_FILE_PREFIX)
    assert "s3cret" not in path.read_text()
    assert ps.load_protected_secret(
        "svc", "acct", fallback_path=str(path), fallback_override_env="TEST_SECRET_FILE"
    ) == "s3cret"

    ps.delete_protected_secret(
        "svc", "acct", fallback_path=str(path), fallback_override_env="TEST_SECRET_FILE"
    )
    assert not path.exists()


def test_unprefixed_legacy_file_value_loads_as_is(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_SECRET_FILE", "1")
    path = tmp_path / "legacy"
    path.write_text("plain-value")

    assert ps.load_protected_secret(
        "svc", "acct", fallback_path=str(path), fallback_override_env="TEST_SECRET_FILE"
    ) == "plain-value"


def test_keychain_path_saves_there_and_removes_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    stored = _fake_keychain(monkeypatch)
    path = tmp_path / "secret"
    path.write_text("old")

    assert ps.save_protected_secret("svc", "acct", "v", fallback_path=str(path)) == "keychain"

    assert stored == {("svc", "acct"): "v"}
    assert not path.exists()
    assert ps.load_protected_secret("svc", "acct", fallback_path=str(path)) == "v"


def test_keychain_failure_falls_back_to_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    _fake_keychain(monkeypatch, fail_save=True)
    path = tmp_path / "secret"

    assert ps.save_protected_secret("svc", "acct", "v", fallback_path=str(path)) == "file"
    assert ps.load_protected_secret("svc", "acct", fallback_path=str(path)) == "v"


def test_keychain_delete_failure_is_raised_and_file_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    _fake_keychain(monkeypatch, fail_delete=True)
    path = tmp_path / "secret"
    path.write_text("x")

    with pytest.raises(RuntimeError):
        ps.delete_protected_secret("svc", "acct", fallback_path=str(path))
    assert path.exists()


def test_keychain_save_uses_native_backend_without_subprocess(monkeypatch):
    saved = []
    monkeypatch.setattr(ps, "_keychain_delete", lambda *_args: None)
    monkeypatch.setattr(ps, "_macos_keychain_add", lambda *args: saved.append(args))

    ps._keychain_save("fitness-dashboard-test", "test-account", "value")

    assert saved == [("fitness-dashboard-test", "test-account", "value")]


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS Keychain integration")
def test_keychain_round_trip_preserves_long_secret():
    service = f"fitness-dashboard-test-{secrets.token_hex(8)}"
    account = f"temporary-{secrets.token_hex(8)}"
    secret = secrets.token_urlsafe(192)
    try:
        ps._keychain_save(service, account, secret)
        assert ps._keychain_find(service, account) == secret
    finally:
        ps._keychain_delete(service, account)
