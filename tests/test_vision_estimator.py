from __future__ import annotations

import json
import urllib.error
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


def _meal_estimate_payload(**overrides):
    payload = {
        "item_name": "Bill Miller BBQ breakfast tacos",
        "portion_description": "1 taco and 2 sandwiches",
        "meal_type": "breakfast",
        "calories": 720,
        "protein_g": 34,
        "carbs_g": 68,
        "fat_g": 32,
        "sodium_mg": 1280,
        "fiber_g": 4,
        "confidence": 0.81,
        "ambiguous": False,
        "uncertainty_notes": [],
    }
    payload.update(overrides)
    return payload


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
        captured["temperature"] = body["temperature"]
        captured["response_format"] = body["response_format"]
        captured["content"] = body["messages"][0]["content"]
        return _Response({
            "choices": [
                {
                    "message": {
                        "content": json.dumps(_meal_estimate_payload())
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

    assert result["item_description"] == "Bill Miller BBQ breakfast tacos"
    assert result["portion_hint"] == "1 taco and 2 sandwiches"
    assert result["macro_estimate"]["calories"] == 720
    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["timeout"] == local_vision_adapter.TIMEOUT_SECONDS
    assert captured["model"] == local_vision_adapter.LM_STUDIO_MODEL
    assert captured["temperature"] == 0.1
    assert captured["response_format"]["type"] == "json_schema"
    assert captured["response_format"]["json_schema"]["strict"] is True
    schema = captured["response_format"]["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "item_name",
        "portion_description",
        "meal_type",
        "calories",
        "protein_g",
        "carbs_g",
        "fat_g",
        "sodium_mg",
        "fiber_g",
        "confidence",
        "ambiguous",
        "uncertainty_notes",
    }
    assert captured["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "Return JSON only with item_name" in captured["content"][0]["text"]


def test_local_lm_studio_adapter_uses_temperature_env(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout):
        body = json.loads(req.data.decode("utf-8"))
        captured["temperature"] = body["temperature"]
        return _Response({"choices": [{"message": {"content": json.dumps(_meal_estimate_payload())}}]})

    monkeypatch.setenv("VISION_TEMPERATURE", "0.23")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    local_vision_adapter.describe_food_photo(b"fake-image", provider="lm_studio")

    assert captured["temperature"] == 0.23


def test_local_lm_studio_adapter_retries_invalid_schema(monkeypatch):
    attempts = []

    def fake_urlopen(req, timeout):
        attempts.append(json.loads(req.data.decode("utf-8")))
        if len(attempts) < 3:
            return _Response({"choices": [{"message": {"content": json.dumps({"item_name": "missing fields"})}}]})
        return _Response({"choices": [{"message": {"content": json.dumps(_meal_estimate_payload())}}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = local_vision_adapter.describe_food_photo(b"fake-image", provider="lm_studio")

    assert result["item_description"] == "Bill Miller BBQ breakfast tacos"
    assert result["_meta"]["schema_retries"] == 2
    assert len(attempts) == 3


def test_local_lm_studio_adapter_falls_back_after_primary_failure(monkeypatch):
    attempts = []
    monkeypatch.setattr(local_vision_adapter, "LM_STUDIO_URL", "http://primary.test")
    monkeypatch.setattr(local_vision_adapter, "LM_STUDIO_MODEL", "primary-vision")
    monkeypatch.setattr(local_vision_adapter, "LM_STUDIO_FALLBACK_URL", "http://fallback.test")
    monkeypatch.setattr(local_vision_adapter, "LM_STUDIO_FALLBACK_MODEL", "fallback-vision")

    def fake_urlopen(req, timeout):
        attempts.append(req.full_url)
        if "primary.test" in req.full_url:
            raise urllib.error.URLError("primary down")
        return _Response({"choices": [{"message": {"content": json.dumps(_meal_estimate_payload())}}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = local_vision_adapter.describe_food_photo(b"fake-image", provider="lm_studio")

    assert attempts == [
        "http://primary.test/v1/chat/completions",
        "http://fallback.test/v1/chat/completions",
    ]
    assert result["_meta"]["role"] == "fallback"
    assert result["_meta"]["model"] == "fallback-vision"
    assert result["_meta"]["fallback_used"] is True


def test_local_lm_studio_adapter_reports_missing_fallback_config(monkeypatch):
    monkeypatch.setattr(local_vision_adapter, "LM_STUDIO_URL", "http://primary.test")
    monkeypatch.setattr(local_vision_adapter, "LM_STUDIO_MODEL", "primary-vision")
    monkeypatch.setattr(local_vision_adapter, "LM_STUDIO_FALLBACK_URL", "")
    monkeypatch.setattr(local_vision_adapter, "LM_STUDIO_FALLBACK_MODEL", "")

    def fake_urlopen(req, timeout):
        raise urllib.error.URLError("primary down")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(local_vision_adapter.LocalVisionError, match="all LM Studio vision endpoints failed"):
        local_vision_adapter.describe_food_photo(b"fake-image", provider="lm_studio")


def test_local_lm_studio_result_does_not_leak_raw_inputs(monkeypatch):
    def fake_urlopen(req, timeout):
        return _Response({"choices": [{"message": {"content": json.dumps(_meal_estimate_payload())}}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = local_vision_adapter.describe_food_photo(
        b"fake-image",
        context_text="/tmp/private-food.jpg data:image/png;base64,abc",
        provider="lm_studio",
    )

    serialized = json.dumps(result)
    assert "data:image" not in serialized
    assert "base64" not in serialized
    assert "/tmp/private-food" not in serialized


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


# ──────────────────────────────────────────────────────────────────
# FIT-138: multi-image support in all three provider adapters.
# Each adapter must emit N image content blocks in one combined call,
# alongside one text prompt that names the count so the model treats
# the photos as one meal context.
# ──────────────────────────────────────────────────────────────────


def test_vision_estimator_threads_multi_image_list_to_adapter(monkeypatch):
    """FIT-138: vision_estimator.describe(images=[(b, mt), ...]) forwards
    the full list to the configured adapter without dropping any images."""
    monkeypatch.setenv("VISION_ESTIMATOR_PROVIDER", "claude")
    captured = {}

    def fake_describe(*_args, **kwargs):
        captured.update(kwargs)
        return {
            "item_description": "two-photo meal",
            "portion_hint": "1 plate",
            "confidence": 0.8,
            "ambiguous": False,
            "uncertainty_notes": [],
        }

    monkeypatch.setattr(vision_estimator.claude_vision_adapter, "describe_food_photo", fake_describe)
    result = vision_estimator.describe(
        images=[(b"first", "image/png"), (b"second", "image/jpeg")],
        context_text="combo meal",
    )
    assert result["provider"] == "claude"
    assert captured.get("images") == [(b"first", "image/png"), (b"second", "image/jpeg")]
    assert captured.get("context_text") == "combo meal"


def test_vision_estimator_preserves_legacy_single_image_call(monkeypatch):
    """FIT-138 back-compat: callers that still pass image_bytes positionally
    keep working — vision_estimator normalizes to a one-element images list."""
    monkeypatch.setenv("VISION_ESTIMATOR_PROVIDER", "lm_studio")
    captured = {}

    def fake_describe(*_args, **kwargs):
        captured.update(kwargs)
        return {
            "item_description": "single photo meal",
            "portion_hint": "1 plate",
            "confidence": 0.8,
            "ambiguous": False,
            "uncertainty_notes": [],
        }

    monkeypatch.setattr(vision_estimator.local_vision_adapter, "describe_food_photo", fake_describe)
    vision_estimator.describe(b"fake-image", media_type="image/webp")
    images = captured.get("images")
    assert images == [(b"fake-image", "image/webp")]


def test_claude_adapter_emits_n_image_blocks_for_multi_image(monkeypatch):
    """FIT-138: claude adapter emits one Anthropic-style image content block
    per image, followed by the text prompt that names the photo count."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    captured = {}

    def fake_urlopen(req, timeout):
        body = json.loads(req.data.decode("utf-8"))
        captured["content"] = body["messages"][0]["content"]
        return _Response({
            "content": [
                {"type": "text", "text": json.dumps({
                    "item_description": "two-photo meal",
                    "portion_hint": "1 plate",
                    "confidence": 0.8,
                    "ambiguous": False,
                    "uncertainty_notes": [],
                })}
            ]
        })

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    claude_vision_adapter.describe_food_photo(
        images=[(b"front", "image/png"), (b"side", "image/jpeg")],
        context_text="dinner",
    )
    content = captured["content"]
    # Two image content blocks + one text prompt block, in that order.
    assert [part["type"] for part in content] == ["image", "image", "text"]
    assert content[0]["source"]["media_type"] == "image/png"
    assert content[1]["source"]["media_type"] == "image/jpeg"
    # Text prompt names the photo count so the model treats them as one meal.
    assert "2 photos" in content[2]["text"]


def test_lm_studio_adapter_emits_n_image_urls_for_multi_image(monkeypatch):
    """FIT-138: lm_studio adapter emits one OpenAI-style image_url block per
    image, with the text prompt first."""
    captured = {}

    def fake_urlopen(req, timeout):
        body = json.loads(req.data.decode("utf-8"))
        captured["content"] = body["messages"][0]["content"]
        return _Response({
            "choices": [
                {"message": {"content": json.dumps(_meal_estimate_payload(
                    item_name="two-photo meal",
                    portion_description="1 plate",
                ))}}
            ]
        })

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    local_vision_adapter.describe_food_photo(
        images=[(b"front", "image/png"), (b"side", "image/jpeg")],
        context_text="dinner",
        provider="lm_studio",
    )
    content = captured["content"]
    # text first, then N image_url entries.
    assert content[0]["type"] == "text"
    assert "2 photos" in content[0]["text"]
    assert [part["type"] for part in content[1:]] == ["image_url", "image_url"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[2]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_ollama_adapter_emits_n_base64_images_for_multi_image(monkeypatch):
    """FIT-138: ollama adapter uses the existing ``images: [...]`` array on
    its chat message — multi-image just means N entries in that array."""
    captured = {}

    def fake_urlopen(req, timeout):
        body = json.loads(req.data.decode("utf-8"))
        captured["message"] = body["messages"][0]
        return _Response({
            "message": {"content": json.dumps({
                "item_description": "two-photo meal",
                "portion_hint": "1 plate",
                "confidence": 0.8,
                "ambiguous": False,
                "uncertainty_notes": [],
            })}
        })

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    local_vision_adapter.describe_food_photo(
        images=[(b"front", "image/png"), (b"side", "image/jpeg")],
        context_text="dinner",
        provider="ollama",
    )
    msg = captured["message"]
    assert len(msg["images"]) == 2
    assert "2 photos" in msg["content"]
