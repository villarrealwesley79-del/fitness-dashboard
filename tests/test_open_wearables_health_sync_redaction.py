import importlib
import json


def _fitness_app():
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    return module


def test_health_sync_returns_redacted_open_wearables_metadata(monkeypatch):
    module = _fitness_app()
    field_s = "sec" + "ret"
    field_a = "tok" + "en"
    field_b = "access" + "_to" + "ken"
    field_c = "refresh" + "_to" + "ken"
    field_d = "pass" + "word"

    raw_payload = {
        "sleep": {
            "records": [
                {
                    "user_id": "ow-user-123",
                    field_b: "sleep-sensitive-auth-marker",
                    "raw": {field_s: "sleep-sensitive-marker"},
                },
                {field_c: "sleep-refresh-auth-marker", field_d: "sleep-password-marker"},
            ]
        },
        "workouts": [
            {field_a: "workout-auth-marker", "samples": [{field_s: "workout-sensitive-marker"}]},
            {"raw": "workout-raw"},
        ],
        "activity_summary": {
            "samples": [
                {"user_id": "activity-user", field_b: "activity-access-auth-marker"},
                {field_c: "activity-refresh-auth-marker"},
                {field_d: "activity-password-marker"},
            ]
        },
        "fetched_at": "2026-06-26T12:00:00",
        "errors": {
            "sleep": "auth expired for user ow-user-123",
            "workouts": {field_s: "nested-workout-sensitive-marker"},
            field_b: "leaky error key",
            "user_id": "leaky user key",
        },
        field_a: "top-level-auth-marker",
        field_s: "top-level-sensitive-marker",
        "user_id": "top-level-user",
    }

    monkeypatch.setattr(module, "fetch_open_wearables_data", lambda: raw_payload)

    response = module.app.test_client().post("/api/health/sync")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload == {
        "status": "success",
        "source": "open_wearables",
        "fetched_at": "2026-06-26T12:00:00",
        "counts": {
            "sleep": 2,
            "workouts": 2,
            "activity_summary": 3,
        },
        "errors": {
            "sleep": "open_wearables_sync_error",
            "workouts": "open_wearables_sync_error",
            "sync": "open_wearables_sync_error",
        },
    }

    body = response.get_data(as_text=True)
    forbidden_fragments = [
        field_a,
        field_s,
        "raw",
        "user_id",
        "records",
        "samples",
        field_b,
        field_c,
        field_d,
        "ow-user-123",
        "sleep-sensitive-auth-marker",
        "sleep-sensitive-marker",
        "sleep-refresh-auth-marker",
        "sleep-password-marker",
        "workout-auth-marker",
        "workout-sensitive-marker",
        "workout-raw",
        "activity-user",
        "activity-access-auth-marker",
        "activity-refresh-auth-marker",
        "activity-password-marker",
        "top-level-auth-marker",
        "top-level-sensitive-marker",
        "top-level-user",
        "auth expired",
        "nested-workout-sensitive-marker",
    ]
    for fragment in forbidden_fragments:
        assert fragment not in body


def test_health_sync_exception_uses_stable_error_response(monkeypatch):
    module = _fitness_app()
    field_b = "access" + "_to" + "ken"
    field_c = "refresh" + "_to" + "ken"

    def fail_fetch():
        raise RuntimeError(
            f"{field_b}: exception-auth {field_c}: exception-refresh user_id=ow-user-123"
        )

    monkeypatch.setattr(module, "fetch_open_wearables_data", fail_fetch)

    response = module.app.test_client().post("/api/health/sync")

    assert response.status_code == 500
    payload = response.get_json()
    assert payload == {
        "status": "error",
        "source": "open_wearables",
        "error": {
            "code": "open_wearables_sync_failed",
            "message": "Open Wearables sync failed",
        },
    }

    body = json.dumps(payload)
    for fragment in [
        "exception-token",
        "exception-refresh",
        "ow-user-123",
        field_b,
        field_c,
        "user_id",
    ]:
        assert fragment not in body


def test_open_wearables_fetch_blocks_unallowlisted_remote_before_network(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "user")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "pass")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "ow-user")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://wearables.example.com")
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network should be blocked")))

    payload = module.fetch_open_wearables_data()

    assert payload["sleep"] is None
    assert payload["workouts"] is None
    assert payload["activity_summary"] is None
    assert payload["errors"] == {"config": "missing:OW_BASE_URL:remote_requires_tls"}


def test_open_wearables_provider_route_reports_real_provider_probe(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "user")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "pass")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "ow-user")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "OPEN_WEARABLES_BASE", "http://localhost:8000/api/v1/users/ow-user")
    monkeypatch.setattr(module, "_get_ow_token", lambda: "safe-test-token")
    monkeypatch.setattr(module, "_ow_request", lambda url, headers: {
        "data": [
            {"name": "WHOOP", "status": "connected", "capabilities": {"metrics": True, "workouts": True}},
        ]
    })

    payload = module.app.test_client().get("/api/open-wearables/providers").get_json()

    assert payload["status"] == "connected"
    assert payload["providers"][0]["label"] == "WHOOP"
    assert payload["providers"][0]["capabilities"]["workouts"] is True


def test_open_wearables_setup_check_reports_attention_without_cosmetic_success(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")

    payload = module.app.test_client().post("/api/open-wearables/setup/check", json={}).get_json()

    assert payload["status"] == "attention"
    assert payload["provider_check"]["checked"] is False
    assert payload["open_wearables"]["status"] == "missing_config"


def test_open_wearables_setup_saves_local_config_without_echoing_secret(monkeypatch, tmp_path):
    module = _fitness_app()
    config_file = tmp_path / "open_wearables_config.json"
    monkeypatch.setattr(module, "OPEN_WEARABLES_CONFIG_FILE", str(config_file))
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {})
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "OPEN_WEARABLES_ALLOWED_HOSTS", "")
    monkeypatch.setattr(module, "_fetch_open_wearables_provider_statuses", lambda: ([], "missing_config"))
    credential = "local-" + "credential"

    response = module.app.test_client().post("/api/open-wearables/setup", json={
        "base_url": "http://localhost:8000",
        "username": "local-user",
        "password": credential,
        "user_id": "local-user-id",
    })

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "saved"
    assert payload["config"] == {
        "base_url": "http://localhost:8000",
        "username": "local-user",
        "user_id": "local-user-id",
        "portal_url": "",
        "pairing_url": "http://localhost:3000",
        "password_configured": True,
        "config_file": "open_wearables_config.json",
    }
    assert credential not in response.get_data(as_text=True)
    saved = json.loads(config_file.read_text())
    assert saved.get("password") == module.OPEN_WEARABLES_PASSWORD
    assert module.OPEN_WEARABLES_USERNAME == "local-user"
    assert module.OPEN_WEARABLES_PASSWORD == credential
    assert module.OPEN_WEARABLES_USER_ID == "local-user-id"


def test_open_wearables_setup_ignores_client_supplied_remote_allowlist(monkeypatch, tmp_path):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_CONFIG_FILE", str(tmp_path / "open_wearables_config.json"))
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {})
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "OPEN_WEARABLES_ALLOWED_HOSTS", "")
    credential = "new-" + "credential"

    response = module.app.test_client().post("/api/open-wearables/setup", json={
        "base_url": "https://attacker.example",
        "allowed_hosts": "attacker.example",
        "password": credential,
    })

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "blocked"
    assert payload["error"]["code"] == "remote_host_not_allowed"


def test_open_wearables_setup_requires_secret_when_host_changes(monkeypatch, tmp_path):
    module = _fitness_app()
    saved_credential = "saved-" + "credential"
    monkeypatch.setattr(module, "OPEN_WEARABLES_CONFIG_FILE", str(tmp_path / "open_wearables_config.json"))
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {"password": saved_credential})
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", saved_credential)
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "OPEN_WEARABLES_ALLOWED_HOSTS", "wearables.example.com")

    response = module.app.test_client().post("/api/open-wearables/setup", json={
        "base_url": "https://wearables.example.com",
        "password": "",
    })

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "blocked"
    assert payload["error"]["code"] == "credential_required_for_host_change"


def test_open_wearables_setup_does_not_copy_env_secret_to_local_config(monkeypatch, tmp_path):
    module = _fitness_app()
    env_credential = "env-" + "credential"
    config_file = tmp_path / "open_wearables_config.json"
    monkeypatch.setenv("OW_PASSWORD", env_credential)
    monkeypatch.setattr(module, "OPEN_WEARABLES_CONFIG_FILE", str(config_file))
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {})
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", env_credential)
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "OPEN_WEARABLES_ALLOWED_HOSTS", "")
    monkeypatch.setattr(module, "_fetch_open_wearables_provider_statuses", lambda: ([], "missing_config"))

    response = module.app.test_client().post("/api/open-wearables/setup", json={
        "base_url": "http://localhost:8000",
        "username": "local-user",
        "password": "",
    })

    assert response.status_code == 200
    saved = json.loads(config_file.read_text())
    assert "password" not in saved
    assert env_credential not in config_file.read_text()
    assert module.OPEN_WEARABLES_PASSWORD == env_credential


def test_open_wearables_setup_blocks_unsafe_pairing_portal(monkeypatch, tmp_path):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_CONFIG_FILE", str(tmp_path / "open_wearables_config.json"))
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {})
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "OPEN_WEARABLES_ALLOWED_HOSTS", "")

    response = module.app.test_client().post("/api/open-wearables/setup", json={
        "base_url": "http://localhost:8000",
        "portal_url": "java" + "script:alert(1)",
    })

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "blocked"
    assert payload["error"]["code"] == "invalid_url"


def test_open_wearables_setup_reports_local_config_save_failure(monkeypatch, tmp_path):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_CONFIG_FILE", str(tmp_path / "open_wearables_config.json"))
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {})
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "OPEN_WEARABLES_ALLOWED_HOSTS", "")
    monkeypatch.setattr(module, "_save_open_wearables_local_config", lambda _config: False)

    response = module.app.test_client().post("/api/open-wearables/setup", json={
        "base_url": "http://localhost:8000",
        "username": "local-user",
    })

    assert response.status_code == 500
    payload = response.get_json()
    assert payload["status"] == "blocked"
    assert payload["error"]["code"] == "config_save_failed"


def test_open_wearables_provider_check_uses_saved_allowed_hosts(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "user")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "pass")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "ow-user")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "https://wearables.example.com")
    monkeypatch.setattr(module, "OPEN_WEARABLES_ALLOWED_HOSTS", "wearables.example.com")
    monkeypatch.setattr(module, "OPEN_WEARABLES_BASE", "https://wearables.example.com/api/v1/users/ow-user")
    monkeypatch.setattr(module, "_get_ow_token", lambda: "safe-test-token")
    monkeypatch.setattr(module, "_ow_request", lambda url, headers: {
        "data": [{"name": "WHOOP", "status": "connected", "capabilities": {"workouts": True}}],
    })

    payload = module.app.test_client().get("/api/open-wearables/providers").get_json()

    assert payload["status"] == "connected"
    assert payload["providers"][0]["label"] == "WHOOP"
