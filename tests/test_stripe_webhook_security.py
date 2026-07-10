from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

from flask import Flask


class _FakeSignatureVerificationError(Exception):
    pass


def _load_stripe_checkout(monkeypatch):
    fake_stripe = SimpleNamespace(
        error=SimpleNamespace(SignatureVerificationError=_FakeSignatureVerificationError),
    )
    monkeypatch.setitem(sys.modules, "stripe", fake_stripe)
    sys.modules.pop("stripe_checkout", None)
    return importlib.import_module("stripe_checkout")


def test_webhook_refuses_unsigned_payload_when_secret_is_unset(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fit255")
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    stripe_checkout = _load_stripe_checkout(monkeypatch)
    upgraded_users = []
    monkeypatch.setattr(
        stripe_checkout,
        "_mark_user_pro",
        lambda user_id, **_kwargs: upgraded_users.append(user_id),
    )
    app = Flask(__name__)
    app.register_blueprint(stripe_checkout.stripe_bp)

    response = app.test_client().post(
        "/webhook",
        json={
            "type": "checkout.session.completed",
            "data": {"object": {"metadata": {"user_id": "1"}}},
        },
    )

    assert response.status_code == 503
    assert b"Stripe webhook secret is not configured" in response.data
    assert upgraded_users == []
