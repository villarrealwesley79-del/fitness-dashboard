from __future__ import annotations

import json
import urllib.request

import nutritionix_client


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_nutritionix_skips_without_env(monkeypatch):
    monkeypatch.delenv("NUTRITIONIX_APP_ID", raising=False)
    monkeypatch.delenv("NUTRITIONIX_APP_KEY", raising=False)

    def fail(*_args, **_kwargs):
        raise AssertionError("must not call Nutritionix without credentials")

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    assert nutritionix_client.natural_nutrients("Chipotle burrito") is None


def test_nutritionix_posts_expected_request(monkeypatch):
    monkeypatch.setenv("NUTRITIONIX_APP_ID", "app-id")
    monkeypatch.setenv("NUTRITIONIX_APP_KEY", "app-key")
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(req.data.decode("utf-8"))
        captured["app_id"] = req.headers["X-app-id"]
        captured["app_key"] = req.headers["X-app-key"]
        return _Response({"foods": [{"food_name": "chicken burrito"}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = nutritionix_client.natural_nutrients("Chipotle burrito")

    assert result["foods"][0]["food_name"] == "chicken burrito"
    assert captured == {
        "url": nutritionix_client.NUTRITIONIX_URL,
        "timeout": nutritionix_client.TIMEOUT_SECONDS,
        "body": {"query": "Chipotle burrito"},
        "app_id": "app-id",
        "app_key": "app-key",
    }


def test_nutritionix_handles_bad_json(monkeypatch):
    monkeypatch.setenv("NUTRITIONIX_APP_ID", "app-id")
    monkeypatch.setenv("NUTRITIONIX_APP_KEY", "app-key")

    class BadResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"{not-json"

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_kw: BadResponse())
    assert nutritionix_client.natural_nutrients("banana") is None


def test_nutritionix_network_warning_is_provider_attributed_and_safe(monkeypatch, caplog):
    import logging

    monkeypatch.setenv("NUTRITIONIX_APP_ID", "safe-app-id")
    monkeypatch.setenv("NUTRITIONIX_APP_KEY", "secret-app-key")
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("https://secret-app-key@example.invalid?q=banana")),
    )

    with caplog.at_level(logging.WARNING):
        assert nutritionix_client.natural_nutrients("banana") is None

    warnings = [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "nutritionix" in warnings[0].lower()
    assert "secret-app-key" not in warnings[0]
    assert "banana" not in warnings[0]
