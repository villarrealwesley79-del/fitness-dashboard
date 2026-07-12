from __future__ import annotations

import importlib
import sqlite3
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from flask import Flask


class _FakeSignatureVerificationError(Exception):
    pass


def _load_stripe_checkout(monkeypatch, tmp_path, event):
    fake_stripe = SimpleNamespace(
        error=SimpleNamespace(SignatureVerificationError=_FakeSignatureVerificationError),
        Webhook=SimpleNamespace(construct_event=lambda *_args: event),
    )
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fit319")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test_fit319")
    monkeypatch.setitem(sys.modules, "stripe", fake_stripe)
    sys.modules.pop("runtime_config", None)
    sys.modules.pop("stripe_checkout", None)
    return importlib.import_module("stripe_checkout")


def _checkout_event():
    return {
        "id": "evt_fit319_checkout",
        "created": 1783846800,
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "metadata": {"user_id": "7"},
                "customer": "cus_fit319",
                "subscription": "sub_fit319",
            }
        },
    }


def test_duplicate_webhook_returns_200_without_reapplying_side_effect(monkeypatch, tmp_path):
    stripe_checkout = _load_stripe_checkout(monkeypatch, tmp_path, _checkout_event())
    upgrades = []
    monkeypatch.setattr(
        stripe_checkout,
        "_apply_stripe_event",
        lambda event, _conn: upgrades.append(event["id"]),
    )
    app = Flask(__name__)
    app.register_blueprint(stripe_checkout.stripe_bp)
    client = app.test_client()

    first = client.post("/webhook", data=b"signed", headers={"Stripe-Signature": "valid"})
    duplicate = client.post("/webhook", data=b"signed", headers={"Stripe-Signature": "valid"})

    assert first.status_code == 200
    assert duplicate.status_code == 200
    assert len(upgrades) == 1

    with sqlite3.connect(tmp_path / "auth.db") as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM stripe_webhook_events").fetchone()
    assert row["event_id"] == "evt_fit319_checkout"
    assert row["event_type"] == "checkout.session.completed"
    assert row["event_created_at"] == 1783846800
    assert row["status"] == "processed"
    assert row["received_at"]
    assert row["processed_at"]
    assert "payload" not in row.keys()


def test_concurrent_duplicate_event_runs_handler_once(monkeypatch, tmp_path):
    stripe_checkout = _load_stripe_checkout(monkeypatch, tmp_path, _checkout_event())
    entered = threading.Event()
    release = threading.Event()
    calls = []
    results = []

    def side_effect(_conn):
        calls.append("called")
        entered.set()
        assert release.wait(timeout=3)

    def process():
        results.append(stripe_checkout._process_event_once(_checkout_event(), side_effect))

    first = threading.Thread(target=process)
    second = threading.Thread(target=process)
    first.start()
    assert entered.wait(timeout=3)
    second.start()
    time.sleep(0.1)
    release.set()
    first.join(timeout=3)
    second.join(timeout=3)

    assert not first.is_alive()
    assert not second.is_alive()
    assert calls == ["called"]
    assert sorted(results) == [False, True]


def test_failed_event_is_audited_and_can_be_retried(monkeypatch, tmp_path):
    stripe_checkout = _load_stripe_checkout(monkeypatch, tmp_path, _checkout_event())

    def fail(_conn):
        raise RuntimeError("synthetic handler failure")

    try:
        stripe_checkout._process_event_once(_checkout_event(), fail)
    except RuntimeError:
        pass
    else:
        raise AssertionError("handler failure must propagate for Stripe retry")

    with sqlite3.connect(tmp_path / "auth.db") as conn:
        failed = conn.execute(
            "SELECT status FROM stripe_webhook_events WHERE event_id=?",
            ("evt_fit319_checkout",),
        ).fetchone()
    assert failed == ("failed",)

    calls = []
    assert stripe_checkout._process_event_once(
        _checkout_event(), lambda _conn: calls.append("retried")
    )
    assert calls == ["retried"]


def test_entitlement_database_failure_propagates_for_webhook_retry(monkeypatch, tmp_path):
    stripe_checkout = _load_stripe_checkout(monkeypatch, tmp_path, _checkout_event())

    def fail_mark_pro(*_args, **_kwargs):
        raise sqlite3.OperationalError("synthetic auth database failure")

    monkeypatch.setitem(
        sys.modules,
        "auth",
        SimpleNamespace(User=SimpleNamespace(mark_pro=fail_mark_pro)),
    )
    app = Flask(__name__)

    with app.app_context(), pytest.raises(sqlite3.OperationalError):
        stripe_checkout._mark_user_pro(
            "7", stripe_customer="cus_fit319", stripe_sub="sub_fit319"
        )


def test_entitlement_write_rolls_back_with_failed_event(monkeypatch, tmp_path):
    stripe_checkout = _load_stripe_checkout(monkeypatch, tmp_path, _checkout_event())
    with sqlite3.connect(tmp_path / "auth.db") as conn:
        conn.execute("CREATE TABLE entitlements (enabled INTEGER NOT NULL)")
        conn.execute("INSERT INTO entitlements VALUES (0)")

    def update_then_fail(conn):
        conn.execute("UPDATE entitlements SET enabled=1")
        raise RuntimeError("synthetic interruption")

    with pytest.raises(RuntimeError):
        stripe_checkout._process_event_once(_checkout_event(), update_then_fail)

    with sqlite3.connect(tmp_path / "auth.db") as conn:
        entitlement = conn.execute("SELECT enabled FROM entitlements").fetchone()
        audit = conn.execute(
            "SELECT status FROM stripe_webhook_events WHERE event_id=?",
            ("evt_fit319_checkout",),
        ).fetchone()

    assert entitlement == (0,)
    assert audit == ("failed",)


def test_entitlement_and_processed_audit_commit_together(monkeypatch, tmp_path):
    stripe_checkout = _load_stripe_checkout(monkeypatch, tmp_path, _checkout_event())
    with sqlite3.connect(tmp_path / "auth.db") as conn:
        conn.execute("CREATE TABLE entitlements (enabled INTEGER NOT NULL)")
        conn.execute("INSERT INTO entitlements VALUES (0)")

    assert stripe_checkout._process_event_once(
        _checkout_event(), lambda conn: conn.execute("UPDATE entitlements SET enabled=1")
    )

    with sqlite3.connect(tmp_path / "auth.db") as conn:
        entitlement = conn.execute("SELECT enabled FROM entitlements").fetchone()
        audit = conn.execute(
            "SELECT status FROM stripe_webhook_events WHERE event_id=?",
            ("evt_fit319_checkout",),
        ).fetchone()

    assert entitlement == (1,)
    assert audit == ("processed",)
