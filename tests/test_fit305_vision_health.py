from pathlib import Path
import json

import vision_estimator


class _JsonResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.body).encode("utf-8")


def test_vision_health_ready_is_sanitized_and_read_only(monkeypatch):
    monkeypatch.setattr(vision_estimator, "configured_provider", lambda: "lm_studio")
    monkeypatch.setattr(
        vision_estimator.local_vision_adapter,
        "_lm_studio_candidates",
        lambda: [{"role": "primary", "url": "http://secret-host:1234", "model": "secret-model"}],
    )
    monkeypatch.setattr(vision_estimator.local_vision_adapter, "_models_for", lambda candidate: [candidate["model"]])
    monkeypatch.setattr(
        vision_estimator.local_vision_adapter,
        "_warm_candidate",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("health must not warm or send an image")),
    )

    payload = vision_estimator.health_status()

    assert payload == {
        "provider": "lm_studio",
        "status": "ready",
        "candidates": [{"role": "primary", "reachable": True, "model_loaded": True}],
    }
    assert "secret-host" not in str(payload)
    assert "secret-model" not in str(payload)


def test_vision_health_reports_warming_fallback_and_unavailable(monkeypatch):
    monkeypatch.setattr(vision_estimator, "configured_provider", lambda: "lm_studio")
    candidates = [
        {"role": "primary", "url": "http://primary", "model": "primary-model"},
        {"role": "fallback", "url": "http://fallback", "model": "fallback-model"},
    ]
    monkeypatch.setattr(vision_estimator.local_vision_adapter, "_lm_studio_candidates", lambda: candidates)

    monkeypatch.setattr(vision_estimator.local_vision_adapter, "_models_for", lambda _candidate: [])
    assert vision_estimator.health_status()["status"] == "warming"

    def fallback_loaded(candidate):
        if candidate["role"] == "primary":
            raise OSError("http://private-host:9999 refused secret-token")
        return [candidate["model"]]

    monkeypatch.setattr(vision_estimator.local_vision_adapter, "_models_for", fallback_loaded)
    payload = vision_estimator.health_status()
    assert payload["status"] == "fallback"
    assert payload["candidates"][0]["error"] == "unreachable"
    assert "private-host" not in str(payload)
    assert "secret-token" not in str(payload)

    monkeypatch.setattr(
        vision_estimator.local_vision_adapter,
        "_models_for",
        lambda _candidate: (_ for _ in ()).throw(TimeoutError("private URL")),
    )
    assert vision_estimator.health_status()["status"] == "unavailable"


def test_ollama_health_uses_running_models_not_installed_tags(monkeypatch):
    monkeypatch.setattr(vision_estimator, "configured_provider", lambda: "ollama")
    requested = []

    def running_models(req, **_kw):
        requested.append(req.full_url)
        return _JsonResponse({"models": [{"name": vision_estimator.local_vision_adapter.OLLAMA_MODEL}]})

    monkeypatch.setattr(
        vision_estimator.request,
        "urlopen",
        running_models,
    )

    payload = vision_estimator.health_status()

    assert payload["status"] == "ready"
    assert requested == [f"{vision_estimator.local_vision_adapter.OLLAMA_URL}/api/ps"]
    assert payload["candidates"][0]["model_loaded"] is True


def test_claude_health_probes_provider_instead_of_trusting_key_presence(monkeypatch):
    monkeypatch.setattr(vision_estimator, "configured_provider", lambda: "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-key")
    requested = []

    def provider_down(req, **_kw):
        requested.append(req.full_url)
        raise OSError("secret response and URL")

    monkeypatch.setattr(vision_estimator.request, "urlopen", provider_down)
    payload = vision_estimator.health_status()

    assert requested == [f"https://api.anthropic.com/v1/models/{vision_estimator.claude_vision_adapter.DEFAULT_MODEL}"]
    assert payload["status"] == "unavailable"
    assert payload["candidates"][0]["reachable"] is False
    assert payload["candidates"][0]["error"] == "unreachable"
    assert "secret" not in str(payload)


def test_vision_health_endpoint_is_authenticated_and_does_not_mutate_meals(monkeypatch):
    import app as module

    monkeypatch.setitem(module.app.config, "TESTING", True)
    monkeypatch.setitem(module.app.config, "LOGIN_DISABLED", True)
    monkeypatch.setattr(module.vision_estimator, "health_status", lambda: {
        "provider": "lm_studio", "status": "ready", "candidates": []
    })
    monkeypatch.setattr(
        module,
        "save_meal_acceptance_event",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("health must not mutate meal state")),
    )
    response = module.app.test_client().get("/api/vision/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ready"

    monkeypatch.setitem(module.app.config, "LOGIN_DISABLED", False)
    response = module.app.test_client().get("/api/vision/health", headers={"Accept": "application/json"})
    assert response.status_code == 401


def test_vision_health_ui_uses_plain_status_text_in_settings_and_log():
    html = Path("templates/index.html").read_text()
    js = Path("static/js/app.js").read_text()

    assert 'id="vision-health-settings-state"' in html
    assert 'id="meal-composer-vision-health"' in html
    assert "'/api/vision/health'" in js
    assert "VISION_HEALTH_STATES" in js
    for state in ("ready", "warming", "fallback", "unavailable"):
        assert state in js
    assert 'class="state-chip" id="vision-health-settings-state"' not in html
    assert 'class="int-dot"' not in html[html.index('id="vision-health-settings-row"'):html.index('id="vision-health-settings-row"') + 500]
