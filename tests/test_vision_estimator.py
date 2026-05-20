from __future__ import annotations

import json
import urllib.request

import pytest

import claude_vision_adapter
import local_vision_adapter
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


def test_vision_estimator_can_be_disabled_explicitly(monkeypatch):
    monkeypatch.setenv("VISION_ESTIMATOR_PROVIDER", "disabled")
    monkeypatch.setattr(
        vision_estimator.local_vision_adapter,
        "describe_food_photo",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("local provider must not run")),
    )

    with pytest.raises(vision_estimator.VisionEstimatorError, match="provider disabled"):
        vision_estimator.describe(b"fake-image")


def test_vision_estimator_defaults_to_local_lm_studio(monkeypatch):
    monkeypatch.delenv("VISION_ESTIMATOR_PROVIDER", raising=False)
    captured = {}

    def fake_describe(*_args, **kwargs):
        captured.update(kwargs)
        return {
            "item_description": "breakfast taco",
            "portion_hint": "1 taco",
            "confidence": 0.9,
            "ambiguous": False,
            "uncertainty_notes": [],
        }

    monkeypatch.setattr(vision_estimator.local_vision_adapter, "describe_food_photo", fake_describe)

    result = vision_estimator.describe(b"fake-image")

    assert result["provider"] == "lm_studio"
    assert captured["provider"] == "lm_studio"


def test_vision_estimator_cleans_claude_response(monkeypatch):
    monkeypatch.setenv("VISION_ESTIMATOR_PROVIDER", "claude")
    monkeypatch.setattr(vision_estimator.claude_vision_adapter, "describe_food_photo", lambda *_a, **_kw: {
        "item_description": "chicken burrito with white rice",
        "portion_hint": "1 large burrito",
        "confidence": 0.824,
        "ambiguous": False,
        "uncertainty_notes": ["wrapper partially visible"],
        "items": [
            {
                "brand": "Bill Miller BBQ",
                "item_name": "Breakfast Sandwich",
                "quantity": "2",
                "modifiers": ["On a Biscuit", "Sausage Patty"],
            }
        ],
    })

    result = vision_estimator.describe(b"fake-image", context_text="Chipotle")

    assert result == {
        "provider": "claude",
        "item_description": "chicken burrito with white rice",
        "portion_hint": "1 large burrito",
        "confidence": 0.82,
        "ambiguous": False,
        "uncertainty_notes": ["wrapper partially visible"],
        "items": [
            {
                "brand": "Bill Miller BBQ",
                "item_name": "Breakfast Sandwich",
                "quantity": 2,
                "modifiers": ["On a Biscuit", "Sausage Patty"],
            }
        ],
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
        captured["prompt"] = body["messages"][0]["content"][1]["text"]
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
    assert "items" in captured["prompt"]
    assert "restaurant cart" in captured["prompt"]


def test_local_lm_studio_adapter_posts_image_and_parses_json(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        body = json.loads(req.data.decode("utf-8"))
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["model"] = body["model"]
        captured["content"] = body["messages"][0]["content"]
        return _Response({
            "choices": [
                {
                    "message": {
                        "content": json.dumps({
                            "item_description": "Bill Miller BBQ order with breakfast items",
                            "portion_hint": "1 taco and 2 sandwiches",
                            "confidence": 0.91,
                            "ambiguous": False,
                            "uncertainty_notes": [],
                            "items": [
                                {"brand": "Bill Miller BBQ", "item_name": "Bacon & Egg Taco", "quantity": 1},
                                {"brand": "Bill Miller BBQ", "item_name": "Breakfast Sandwich", "quantity": 2},
                            ],
                        })
                    }
                }
            ]
        })

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    result = local_vision_adapter.describe_food_photo(
        b"fake-image",
        context_text="Bill Miller",
        media_type="image/png",
        provider="lm_studio",
    )

    assert result["items"][1]["quantity"] == 2
    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["timeout"] == local_vision_adapter.TIMEOUT_SECONDS
    assert captured["model"] == local_vision_adapter.LM_STUDIO_MODEL
    assert captured["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "Do not collapse a multi-item cart" in captured["content"][0]["text"]


def test_local_adapter_blank_env_values_fall_back(monkeypatch):
    monkeypatch.setenv("VISION_LM_STUDIO_URL", "")
    monkeypatch.setenv("LM_STUDIO_URL", " http://127.0.0.1:9999/ ")

    assert local_vision_adapter._env_first(
        "VISION_LM_STUDIO_URL",
        "LM_STUDIO_URL",
        default="http://127.0.0.1:1234",
    ) == "http://127.0.0.1:9999/"


def test_local_adapter_vision_model_does_not_fall_back_to_text_model(monkeypatch):
    monkeypatch.setenv("VISION_LM_STUDIO_MODEL", "")
    monkeypatch.setenv("LM_STUDIO_VISION_MODEL", "")
    monkeypatch.setenv("LM_STUDIO_MODEL", "text-only-coach-model")

    assert local_vision_adapter._env_first(
        "VISION_LM_STUDIO_MODEL",
        "LM_STUDIO_VISION_MODEL",
        default="qwen2.5-vl-7b-instruct",
    ) == "qwen2.5-vl-7b-instruct"


def test_local_adapter_malformed_url_is_handled():
    with pytest.raises(local_vision_adapter.LocalVisionError):
        local_vision_adapter._post_json("/v1/chat/completions", {}, timeout=1)
