from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

from flask import Flask
from flask_login import LoginManager, UserMixin


class _User(UserMixin):
    def __init__(self, user_id: str, *, is_pro: bool = False):
        self.id = user_id
        self.is_pro = is_pro


def _build_app(monkeypatch, *, user: _User, sessions=None, log_in=True):
    sessions = sessions or {}

    class _Session:
        @staticmethod
        def retrieve(session_id):
            value = sessions[session_id]
            if isinstance(value, Exception):
                raise value
            return value

    fake_stripe = SimpleNamespace(
        api_key="",
        checkout=SimpleNamespace(Session=_Session),
        error=SimpleNamespace(SignatureVerificationError=ValueError),
    )
    monkeypatch.setitem(sys.modules, "stripe", fake_stripe)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fit272")
    sys.modules.pop("stripe_checkout", None)
    stripe_checkout = importlib.import_module("stripe_checkout")

    app = Flask(__name__, template_folder="../templates")
    app.config.update(SECRET_KEY="fit272-test", TESTING=True)
    login_manager = LoginManager(app)
    login_manager.user_loader(lambda user_id: user if user_id == user.id else None)
    app.register_blueprint(stripe_checkout.stripe_bp)
    client = app.test_client()
    if log_in:
        with client.session_transaction() as flask_session:
            flask_session["_user_id"] = user.id
            flask_session["_fresh"] = True
    return client


def test_success_direct_access_requires_login(monkeypatch):
    client = _build_app(monkeypatch, user=_User("7"), log_in=False)

    response = client.get("/success")

    assert response.status_code == 401


def test_success_missing_session_id_is_pending(monkeypatch):
    client = _build_app(monkeypatch, user=_User("7"))

    response = client.get("/success?session_id=")

    assert response.status_code == 200
    assert b"Activation pending" in response.data


def test_success_for_webhook_updated_user_is_active(monkeypatch):
    client = _build_app(monkeypatch, user=_User("7", is_pro=True))

    response = client.get("/success")

    assert response.status_code == 200
    assert b"Your Pro account is now active" in response.data
    assert b"Activation pending" not in response.data


def test_success_for_webhook_pending_user_uses_verified_checkout(monkeypatch):
    client = _build_app(
        monkeypatch,
        user=_User("7"),
        sessions={
            "cs_valid": {
                "status": "complete",
                "payment_status": "paid",
                "metadata": {"user_id": "7"},
            }
        },
    )

    response = client.get("/success?session_id=cs_valid")

    assert response.status_code == 200
    assert b"Your Pro account is now active" in response.data


def test_success_does_not_trust_incomplete_or_other_users_session(monkeypatch):
    client = _build_app(
        monkeypatch,
        user=_User("7"),
        sessions={
            "cs_open": {
                "status": "open",
                "payment_status": "unpaid",
                "metadata": {"user_id": "7"},
            },
            "cs_other": {
                "status": "complete",
                "payment_status": "paid",
                "metadata": {"user_id": "9"},
            },
        },
    )

    assert b"Activation pending" in client.get("/success?session_id=cs_open").data
    assert b"Activation pending" in client.get("/success?session_id=cs_other").data


def test_success_stays_pending_when_stripe_verification_fails(monkeypatch):
    client = _build_app(
        monkeypatch,
        user=_User("7"),
        sessions={"cs_error": RuntimeError("provider unavailable")},
    )

    response = client.get("/success?session_id=cs_error")

    assert response.status_code == 200
    assert b"Activation pending" in response.data
