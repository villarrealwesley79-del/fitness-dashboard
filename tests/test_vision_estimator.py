from __future__ import annotations

import json
import urllib.request

import pytest

import claude_vision_adapter
import vision_estimator


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_vision_estimator_cleans_claude_response(monkeypatch):
    monkeypatch.setenv("VISION_ESTIMATOR_PROVIDER", "claude")
    monkeypatch.setattr(vision_estimator.claude_vision_adapter, "describe_food_photo", lambda *_a, **_kw: {
        "item_description": "chicken burrito with white rice",
        "portion_hint": "1 large burrito",
        "confidence": 0.824,
        "ambiguous": False,
        "uncertainty_notes": ["wrapper partially visible"],
    })

    result = vision_estimator.describe(b"fake-image", context_text="Chipotle")

    assert result == {
        "provider": "claude",
        "item_description": "chicken burrito with white rice",
        "portion_hint": "1 large burrito",
        "confidence": 0.82,
        "ambiguous": False,
        "uncertainty_notes": ["wrapper partially visible"],
    }


def test_vision_estimator_rejects_unsupported_provider(monkeypatch):
    monkeypatch.setenv("VISION_ESTIMATOR_PROVIDER", "unknown")
    with pytest.raises(vision_estimator.VisionEstimatorError):
        vision_estimator.describe(b"fake-image")


def test_claude_adapter_requires_env_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(claude_vision_adapter.ClaudeVisionError):
        claude_vision_adapter.describe_food_photo(b"fake-image")


def test_claude_adapter_posts_image_and_parses_json(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    captured = {}

    def fake_urlopen(req, timeout):
        body = json.loads(req.data.decode("utf-8"))
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["key"] = req.headers["X-api-key"]
        captured["version"] = req.headers["Anthropic-version"]
        captured["media_type"] = body["messages"][0]["content"][0]["source"]["media_type"]
        return _Response({
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({
                        "item_description": "chicken burrito",
                        "portion_hint": "1 burrito",
                        "confidence": 0.82,
                        "ambiguous": False,
                        "uncertainty_notes": [],
                    }),
                }
            ]
        })

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = claude_vision_adapter.describe_food_photo(
        b"fake-image",
        context_text="Chipotle",
        media_type="image/png",
    )

    assert result["item_description"] == "chicken burrito"
    assert captured["url"] == claude_vision_adapter.ANTHROPIC_MESSAGES_URL
    assert captured["timeout"] == claude_vision_adapter.TIMEOUT_SECONDS
    assert captured["key"] == "anthropic-key"
    assert captured["version"] == claude_vision_adapter.ANTHROPIC_VERSION
    assert captured["media_type"] == "image/png"
