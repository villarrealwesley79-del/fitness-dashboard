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
    assert payload["config"]["base_url"] == "http://localhost:8000"
    assert payload["config"]["username"] == "local-user"
    assert payload["config"]["user_id"] == "local-user-id"
    assert payload["config"]["portal_url"] == ""
    assert payload["config"]["pairing_url"] == "http://localhost:3000"
    assert payload["config"]["password_configured"] is True
    assert payload["config"]["config_file"] == "open_wearables_config.json"
    assert credential not in response.get_data(as_text=True)
    saved = json.loads(config_file.read_text())
    assert saved.get("password") == module.OPEN_WEARABLES_PASSWORD
    assert module.OPEN_WEARABLES_USERNAME == "local-user"
    assert module.OPEN_WEARABLES_PASSWORD == credential
    assert module.OPEN_WEARABLES_USER_ID == "local-user-id"


def test_open_wearables_setup_save_preserves_sidecar_path_and_restart_flag(monkeypatch, tmp_path):
    module = _fitness_app()
    config_file = tmp_path / "open_wearables_config.json"
    sidecar_env = tmp_path / "custom-open-wearables.env"
    saved_credential = "saved-" + "credential"
    monkeypatch.setattr(module, "OPEN_WEARABLES_CONFIG_FILE", str(config_file))
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {
        "password": saved_credential,
        "sidecar_env_path": str(sidecar_env),
    })
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", saved_credential)
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SIDECAR_ENV_PATH", str(sidecar_env))
    monkeypatch.setattr(module, "OPEN_WEARABLES_MANAGED_RESTART_REQUIRED", True)
    monkeypatch.setattr(module, "_fetch_open_wearables_provider_statuses", lambda: ([], "open_wearables_no_providers"))

    response = module.app.test_client().post("/api/open-wearables/setup", json={
        "base_url": "http://localhost:8000",
        "username": "admin@example.test",
        "user_id": "11111111-1111-4111-8111-111111111111",
    })

    assert response.status_code == 200
    saved = json.loads(config_file.read_text())
    assert saved["password"] == saved_credential
    assert saved["sidecar_env_path"] == str(sidecar_env)
    assert saved["managed_connector_restart_required"] is True
    assert module.OPEN_WEARABLES_SIDECAR_ENV_PATH == str(sidecar_env)
    assert module.OPEN_WEARABLES_MANAGED_RESTART_REQUIRED is True


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


def test_open_wearables_bootstrap_uses_sidecar_without_echoing_secret(monkeypatch, tmp_path):
    module = _fitness_app()
    config_file = tmp_path / "open_wearables_config.json"
    sidecar_env = tmp_path / "open-wearables.env"
    bootstrap_secret = "bootstrap-" + "credential"
    sidecar_env.write_text(
        "\n".join([
            "ADMIN_EMAIL=admin@example.test",
            f"ADMIN_PASSWORD={bootstrap_secret}",
            "FRONTEND_URL=http://localhost:3000",
        ])
    )
    monkeypatch.setattr(module, "OPEN_WEARABLES_CONFIG_FILE", str(config_file))
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {})
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PORTAL_URL", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SIDECAR_ENV_PATH", str(sidecar_env))
    monkeypatch.setattr(module, "_open_wearables_login", lambda *_args, **_kwargs: ("safe-token", {}))
    monkeypatch.setattr(module, "_open_wearables_resolve_user", lambda *_args, **_kwargs: ("11111111-1111-4111-8111-111111111111", "created"))
    monkeypatch.setattr(module, "_open_wearables_seed_managed_provider_credentials", lambda *_args, **_kwargs: {
        "available": True,
        "changed": False,
        "providers": [{"provider": "whoop", "status": "already_prepared"}],
        "restart_required": False,
    })
    monkeypatch.setattr(module, "_fetch_open_wearables_provider_statuses", lambda: ([], "open_wearables_no_providers"))

    response = module.app.test_client().post("/api/open-wearables/setup/bootstrap", json={})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ready"
    assert payload["bootstrap"] == {
        "user_mapped": True,
        "user_state": "created",
        "managed_connectors": {
            "available": True,
            "changed": False,
            "providers": [{"provider": "whoop", "status": "already_prepared"}],
            "restart_required": False,
        },
    }
    assert payload["config"]["username"] == "admin@example.test"
    assert payload["config"]["user_id"] == "11111111-1111-4111-8111-111111111111"
    assert payload["config"]["password_configured"] is True
    assert payload["config"]["hub_account_ready"] is True
    assert payload["config"]["user_mapped"] is True
    assert payload["config"]["provider_setup_ready"] is False
    assert bootstrap_secret not in response.get_data(as_text=True)
    saved = json.loads(config_file.read_text())
    assert saved["password"] == bootstrap_secret
    assert module.OPEN_WEARABLES_PASSWORD == bootstrap_secret


def test_open_wearables_bootstrap_reports_missing_sidecar_without_secret(monkeypatch, tmp_path):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_CONFIG_FILE", str(tmp_path / "open_wearables_config.json"))
    monkeypatch.setattr(module, "OPEN_WEARABLES_SIDECAR_ENV_PATH", str(tmp_path / "missing.env"))
    monkeypatch.setattr(module, "_fetch_open_wearables_provider_statuses", lambda: ([], "missing_config"))

    response = module.app.test_client().post("/api/open-wearables/setup/bootstrap", json={})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "blocked"
    assert payload["error"]["code"] == "sidecar_env_missing"
    assert "bootstrap-credential" not in response.get_data(as_text=True)


def test_open_wearables_bootstrap_can_prepare_managed_whoop_without_echoing_secret(monkeypatch, tmp_path):
    module = _fitness_app()
    config_file = tmp_path / "open_wearables_config.json"
    sidecar_env = tmp_path / "open-wearables.env"
    provider_secret = "provider-" + "credential"
    sidecar_env.write_text(
        "\n".join([
            "ADMIN_EMAIL=admin@example.test",
            "ADMIN_PASSWORD=bootstrap-credential",
            "FRONTEND_URL=http://localhost:3000",
            "WHOOP_CLIENT_ID=public-client-id",
            "WHOOP_CLIENT_SECRET=private-secret-id",
        ])
    )

    class Config:
        client_id = "local-whoop-client"
        client_secret = provider_secret

    monkeypatch.setattr(module, "OPEN_WEARABLES_CONFIG_FILE", str(config_file))
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {})
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PORTAL_URL", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SIDECAR_ENV_PATH", str(sidecar_env))
    monkeypatch.setattr(module, "OPEN_WEARABLES_MANAGED_RESTART_REQUIRED", False)
    monkeypatch.setattr(module, "load_whoop_config", lambda *_args, **_kwargs: Config())
    monkeypatch.setattr(module, "_open_wearables_login", lambda *_args, **_kwargs: ("safe-token", {}))
    monkeypatch.setattr(module, "_open_wearables_resolve_user", lambda *_args, **_kwargs: ("11111111-1111-4111-8111-111111111111", "created"))
    monkeypatch.setattr(module, "_fetch_open_wearables_provider_statuses", lambda: ([], "open_wearables_no_providers"))
    monkeypatch.setattr(
        module,
        "_open_wearables_provider_settings_from_hub",
        lambda: ({}, "provider_catalog_unavailable"),
    )

    response = module.app.test_client().post("/api/open-wearables/setup/bootstrap", json={})

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["bootstrap"]["managed_connectors"]["changed"] is True
    assert payload["bootstrap"]["managed_connectors"]["restart_required"] is True
    assert payload["config"]["managed_connector_restart_required"] is True
    assert provider_secret not in body
    assert "bootstrap-credential" not in body
    assert json.loads(config_file.read_text())["managed_connector_restart_required"] is True
    saved_env = sidecar_env.read_text()
    assert "WHOOP_CLIENT_ID=local-whoop-client" in saved_env
    assert f"WHOOP_CLIENT_SECRET={provider_secret}" in saved_env


def test_open_wearables_managed_whoop_uses_web_app_callback(monkeypatch, tmp_path):
    module = _fitness_app()
    sidecar_env = tmp_path / "open-wearables.env"
    sidecar_env.write_text(
        "\n".join([
            "ADMIN_EMAIL=admin@example.test",
            "ADMIN_PASSWORD=bootstrap-credential",
            "WHOOP_CLIENT_ID=public-client-id",
            "WHOOP_CLIENT_SECRET=private-secret-id",
            "API_BASE_URL=https://admins-mac-mini.tail6c6490.ts.net:8000",
        ])
    )

    class Config:
        client_id = "local-whoop-client"
        client_secret = "provider-credential"

    monkeypatch.setattr(module, "OPEN_WEARABLES_SIDECAR_ENV_PATH", str(sidecar_env))
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "OPEN_WEARABLES_MANAGED_RESTART_REQUIRED", False)
    monkeypatch.setattr(module, "load_whoop_config", lambda *_args, **_kwargs: Config())

    with module.app.test_request_context(
        "/api/open-wearables/setup",
        base_url="https://admins-mac-mini.tail6c6490.ts.net:5050",
    ):
        result = module._open_wearables_seed_managed_provider_credentials()

    assert result["changed"] is True
    saved_env = sidecar_env.read_text()
    assert "WHOOP_REDIRECT_URI=https://admins-mac-mini.tail6c6490.ts.net:5050/api/whoop/callback" in saved_env
    assert "WHOOP_REDIRECT_URI=https://admins-mac-mini.tail6c6490.ts.net:8000" not in saved_env


def test_open_wearables_managed_seed_preserves_restart_until_hub_restarts(monkeypatch, tmp_path):
    module = _fitness_app()
    sidecar_env = tmp_path / "open-wearables.env"
    sidecar_env.write_text(
        "\n".join([
            "ADMIN_EMAIL=admin@example.test",
            "ADMIN_PASSWORD=bootstrap-credential",
            "WHOOP_CLIENT_ID=local-whoop-client",
            "WHOOP_CLIENT_SECRET=provider-credential",
            "WHOOP_DEFAULT_SCOPE=offline read:cycles read:sleep read:recovery read:workout read:body_measurement",
            "WHOOP_REDIRECT_URI=https://admins-mac-mini.tail6c6490.ts.net:5050/api/whoop/callback",
        ])
    )
    monkeypatch.setattr(module, "OPEN_WEARABLES_SIDECAR_ENV_PATH", str(sidecar_env))
    monkeypatch.setattr(module, "OPEN_WEARABLES_MANAGED_RESTART_REQUIRED", True)

    with module.app.test_request_context(
        "/api/open-wearables/setup",
        base_url="https://admins-mac-mini.tail6c6490.ts.net:5050",
    ):
        result = module._open_wearables_seed_managed_provider_credentials()

    assert result["changed"] is False
    assert result["restart_required"] is True
    assert module.OPEN_WEARABLES_MANAGED_RESTART_REQUIRED is True


def test_open_wearables_provider_actions_do_not_seed_sidecar_on_read(monkeypatch, tmp_path):
    module = _fitness_app()
    sidecar_env = tmp_path / "open-wearables.env"
    original_env = "\n".join([
        "ADMIN_EMAIL=admin@example.test",
        "ADMIN_PASSWORD=bootstrap-credential",
        "WHOOP_CLIENT_ID=public-client-id",
        "WHOOP_CLIENT_SECRET=private-secret-id",
    ])
    sidecar_env.write_text(original_env)

    class Config:
        client_id = "local-whoop-client"
        client_secret = "provider-credential"

    monkeypatch.setattr(module, "OPEN_WEARABLES_SIDECAR_ENV_PATH", str(sidecar_env))
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_MANAGED_RESTART_REQUIRED", False)
    monkeypatch.setattr(module, "load_whoop_config", lambda *_args, **_kwargs: Config())

    actions = module._open_wearables_provider_actions()

    assert actions
    assert sidecar_env.read_text() == original_env
    assert module.OPEN_WEARABLES_MANAGED_RESTART_REQUIRED is False


def test_open_wearables_provider_actions_block_when_catalog_unavailable(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_MANAGED_RESTART_REQUIRED", False)
    monkeypatch.setattr(module, "_load_open_wearables_sidecar_env", lambda: ({
        "WHOOP_CLIENT_ID": "local-whoop-client",
        "WHOOP_CLIENT_SECRET": "provider-credential",
    }, None))
    monkeypatch.setattr(module, "_open_wearables_provider_settings_from_hub", lambda: ({}, "provider_catalog_unavailable"))

    whoop = next(action for action in module._open_wearables_provider_actions() if action["provider"] == "whoop")

    assert whoop["enabled"] is False
    assert whoop["url"] == ""
    assert whoop["reason"] == "provider_catalog_unavailable"


def test_open_wearables_provider_actions_clear_restart_after_hub_catalog_ready(monkeypatch, tmp_path):
    module = _fitness_app()
    config_file = tmp_path / "open_wearables_config.json"
    sidecar_env = tmp_path / "open-wearables.env"
    sidecar_env.write_text("\n".join([
        "WHOOP_CLIENT_ID=local-whoop-client",
        "WHOOP_CLIENT_SECRET=provider-credential",
    ]))
    monkeypatch.setattr(module, "OPEN_WEARABLES_CONFIG_FILE", str(config_file))
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {
        "base_url": "http://localhost:8000",
        "username": "admin@example.test",
        "password": "saved-credential",
        "user_id": "11111111-1111-4111-8111-111111111111",
        "sidecar_env_path": str(sidecar_env),
        "managed_connector_restart_required": True,
    })
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SIDECAR_ENV_PATH", str(sidecar_env))
    monkeypatch.setattr(module, "OPEN_WEARABLES_MANAGED_RESTART_REQUIRED", True)
    monkeypatch.setattr(module, "_open_wearables_provider_settings_from_hub", lambda: ({
        "whoop": {"has_cloud_api": True, "is_enabled": True},
    }, None))

    whoop = next(action for action in module._open_wearables_provider_actions() if action["provider"] == "whoop")

    assert module.OPEN_WEARABLES_MANAGED_RESTART_REQUIRED is False
    assert whoop["enabled"] is True
    assert whoop["reason"] == ""
    assert "managed_connector_restart_required" not in json.loads(config_file.read_text())


def test_open_wearables_provider_actions_block_when_catalog_empty(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "OPEN_WEARABLES_MANAGED_RESTART_REQUIRED", False)
    monkeypatch.setattr(module, "_load_open_wearables_sidecar_env", lambda: ({
        "WHOOP_CLIENT_ID": "local-whoop-client",
        "WHOOP_CLIENT_SECRET": "provider-credential",
    }, None))
    monkeypatch.setattr(module, "_ow_json_request", lambda *_args, **_kwargs: [])

    whoop = next(action for action in module._open_wearables_provider_actions() if action["provider"] == "whoop")

    assert whoop["enabled"] is False
    assert whoop["url"] == ""
    assert whoop["reason"] == "provider_catalog_unavailable"


def test_open_wearables_provider_actions_block_when_provider_omitted(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_MANAGED_RESTART_REQUIRED", False)
    monkeypatch.setattr(module, "_load_open_wearables_sidecar_env", lambda: ({
        "WHOOP_CLIENT_ID": "local-whoop-client",
        "WHOOP_CLIENT_SECRET": "provider-credential",
    }, None))
    monkeypatch.setattr(module, "_open_wearables_provider_settings_from_hub", lambda: ({
        "oura": {"has_cloud_api": True, "is_enabled": True},
    }, None))

    whoop = next(action for action in module._open_wearables_provider_actions() if action["provider"] == "whoop")

    assert whoop["enabled"] is False
    assert whoop["url"] == ""
    assert whoop["reason"] == "provider_not_ready"


def test_open_wearables_public_status_does_not_fetch_provider_actions(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")

    def fail_provider_actions():
        raise AssertionError("public status should not fetch provider actions")

    monkeypatch.setattr(module, "_open_wearables_provider_actions", fail_provider_actions)

    status = module._open_wearables_public_status(providers=[], error_code="open_wearables_no_providers")

    assert status["configured"] is True
    assert status["setup_hint"] == "provider_credentials_missing"


def test_open_wearables_bootstrap_preserves_existing_restart_required(monkeypatch, tmp_path):
    module = _fitness_app()
    config_file = tmp_path / "open_wearables_config.json"
    sidecar_env = tmp_path / "open-wearables.env"
    sidecar_env.write_text("\n".join([
        "ADMIN_EMAIL=admin@example.test",
        "ADMIN_PASSWORD=bootstrap-credential",
    ]))

    monkeypatch.setattr(module, "OPEN_WEARABLES_CONFIG_FILE", str(config_file))
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {})
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PORTAL_URL", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SIDECAR_ENV_PATH", str(sidecar_env))
    monkeypatch.setattr(module, "OPEN_WEARABLES_MANAGED_RESTART_REQUIRED", True)
    monkeypatch.setattr(module, "_open_wearables_login", lambda *_args, **_kwargs: ("safe-token", {}))
    monkeypatch.setattr(module, "_ow_json_request", lambda *_args, **_kwargs: {"id": "11111111-1111-4111-8111-111111111111"})
    monkeypatch.setattr(module, "_open_wearables_seed_managed_provider_credentials", lambda *_args, **_kwargs: {
        "available": True,
        "changed": False,
        "providers": [{"provider": "whoop", "status": "already_prepared"}],
        "restart_required": True,
    })

    bootstrap, error = module._open_wearables_bootstrap_local_hub()

    assert error is None
    assert bootstrap["managed_connectors"]["restart_required"] is True
    assert module.OPEN_WEARABLES_MANAGED_RESTART_REQUIRED is True
    assert json.loads(config_file.read_text())["managed_connector_restart_required"] is True


def test_open_wearables_pair_provider_returns_authorization_url(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "_open_wearables_provider_credentials_ready", lambda _provider: True)
    monkeypatch.setattr(module, "_open_wearables_provider_settings_from_hub", lambda: ({
        "whoop": {"has_cloud_api": True, "is_enabled": True},
    }, None))
    monkeypatch.setattr(
        module,
        "_ow_json_request",
        lambda url, **_kwargs: {"authorization_url": "https://provider.example.test/oauth"},
    )

    response = module.app.test_client().post("/api/open-wearables/pair/whoop", json={})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload == {
        "status": "ready",
        "provider": "whoop",
        "authorization_url": "https://provider.example.test/oauth",
    }
    assert "saved-credential" not in response.get_data(as_text=True)


def test_open_wearables_pair_provider_clears_restart_after_hub_catalog_ready(monkeypatch, tmp_path):
    module = _fitness_app()
    config_file = tmp_path / "open_wearables_config.json"
    sidecar_env = tmp_path / "open-wearables.env"
    sidecar_env.write_text("\n".join([
        "WHOOP_CLIENT_ID=local-whoop-client",
        "WHOOP_CLIENT_SECRET=provider-credential",
    ]))
    monkeypatch.setattr(module, "OPEN_WEARABLES_CONFIG_FILE", str(config_file))
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {
        "base_url": "http://localhost:8000",
        "username": "admin@example.test",
        "password": "saved-credential",
        "user_id": "11111111-1111-4111-8111-111111111111",
        "sidecar_env_path": str(sidecar_env),
        "managed_connector_restart_required": True,
    })
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SIDECAR_ENV_PATH", str(sidecar_env))
    monkeypatch.setattr(module, "OPEN_WEARABLES_MANAGED_RESTART_REQUIRED", True)
    monkeypatch.setattr(module, "_open_wearables_provider_settings_from_hub", lambda: ({
        "whoop": {"has_cloud_api": True, "is_enabled": True},
    }, None))
    monkeypatch.setattr(
        module,
        "_ow_json_request",
        lambda url, **_kwargs: {"authorization_url": "https://provider.example.test/oauth"},
    )

    response = module.app.test_client().post("/api/open-wearables/pair/whoop", json={})

    assert response.status_code == 200
    assert module.OPEN_WEARABLES_MANAGED_RESTART_REQUIRED is False
    assert response.get_json()["authorization_url"] == "https://provider.example.test/oauth"
    assert "managed_connector_restart_required" not in json.loads(config_file.read_text())


def test_open_wearables_pair_provider_blocks_disallowed_base_without_request(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "https://evil.example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_ALLOWED_HOSTS", "")
    monkeypatch.setattr(module, "_open_wearables_provider_credentials_ready", lambda _provider: True)

    def fail_request(*_args, **_kwargs):
        raise AssertionError("disallowed Open Wearables host was requested")

    monkeypatch.setattr(module, "_ow_json_request", fail_request)

    response = module.app.test_client().post("/api/open-wearables/pair/oura", json={})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "blocked"
    assert payload["error"]["code"] == "remote_host_not_allowed"
    assert "saved-credential" not in response.get_data(as_text=True)


def test_open_wearables_pair_get_does_not_bootstrap_missing_setup(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "")

    def fail_bootstrap():
        raise AssertionError("GET pairing should not mutate setup")

    monkeypatch.setattr(module, "_open_wearables_bootstrap_local_hub", fail_bootstrap)

    response = module.app.test_client().get("/api/open-wearables/pair/oura")

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "blocked"
    assert payload["error"]["code"] == "missing_user_mapping"


def test_whoop_callback_falls_back_to_open_wearables_state(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "consume_oauth_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_open_wearables_complete_oauth_callback", lambda provider, code, state: (
        provider == "whoop" and code == "provider-code" and state == "ow-state"
    ))

    response = module.app.test_client().get(
        "/api/whoop/callback?code=provider-code&state=ow-state",
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload == {"status": "success", "provider": "whoop", "source": "open_wearables"}


def test_whoop_callback_does_not_call_open_wearables_when_base_disallowed(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "consume_oauth_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "https://evil.example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_ALLOWED_HOSTS", "")

    def fail_request(*_args, **_kwargs):
        raise AssertionError("disallowed Open Wearables callback host was requested")

    monkeypatch.setattr(module, "_ow_json_request", fail_request)

    response = module.app.test_client().get(
        "/api/whoop/callback?code=provider-code&state=ow-state",
        headers={"Accept": "application/json"},
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["error"]["code"] == "invalid_state"


def test_open_wearables_pair_provider_uses_open_wearables_for_non_whoop_cloud(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "_open_wearables_provider_credentials_ready", lambda _provider: True)
    monkeypatch.setattr(module, "_open_wearables_provider_settings_from_hub", lambda: ({
        "oura": {"has_cloud_api": True, "is_enabled": True},
    }, None))
    monkeypatch.setattr(
        module,
        "_ow_json_request",
        lambda url, **_kwargs: {"authorization_url": "https://oura.example.test/oauth"},
    )

    response = module.app.test_client().post("/api/open-wearables/pair/oura", json={})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload == {
        "status": "ready",
        "provider": "oura",
        "authorization_url": "https://oura.example.test/oauth",
    }
    assert "saved-credential" not in response.get_data(as_text=True)


def test_open_wearables_pair_provider_blocks_sdk_sources(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")

    response = module.app.test_client().post("/api/open-wearables/pair/apple", json={})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "blocked"
    assert payload["error"]["code"] == "sdk_provider"
    assert "saved-credential" not in response.get_data(as_text=True)


def test_open_wearables_pair_provider_blocks_placeholder_cloud_credentials(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "_open_wearables_provider_credentials_ready", lambda _provider: False)
    monkeypatch.setattr(module, "_open_wearables_provider_settings_from_hub", lambda: ({
        "oura": {"has_cloud_api": True, "is_enabled": True},
    }, None))

    response = module.app.test_client().post("/api/open-wearables/pair/oura", json={})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "blocked"
    assert payload["error"]["code"] == "provider_app_needed"
    assert "saved-credential" not in response.get_data(as_text=True)


def test_open_wearables_pair_provider_blocks_missing_hub_catalog(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "_open_wearables_provider_credentials_ready", lambda _provider: True)
    monkeypatch.setattr(module, "_open_wearables_provider_settings_from_hub", lambda: ({}, "provider_catalog_unavailable"))

    def fail_request(*_args, **_kwargs):
        raise AssertionError("provider website should not open without hub provider readiness")

    monkeypatch.setattr(module, "_ow_json_request", fail_request)

    response = module.app.test_client().post("/api/open-wearables/pair/oura", json={})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "blocked"
    assert payload["error"]["code"] == "provider_catalog_unavailable"
    assert "saved-credential" not in response.get_data(as_text=True)


def test_open_wearables_mobile_invite_returns_sdk_code_without_secret(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "_get_ow_token", lambda: "safe-test-token")

    def fake_json_request(url, **kwargs):
        assert kwargs.get("method") == "POST"
        assert kwargs.get("token") == "safe-test-token"
        assert url.endswith("/api/v1/users/11111111-1111-4111-8111-111111111111/invitation-code")
        return {"code": "ABCD2345", "expires_at": "2026-06-28T09:00:00Z"}

    monkeypatch.setattr(module, "_ow_json_request", fake_json_request)

    response = module.app.test_client().post(
        "/api/open-wearables/mobile-invite/apple",
        json={},
        base_url="http://admins-mac-mini.tail6c6490.ts.net:5050",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ready"
    assert payload["invite"]["provider"] == "apple"
    assert payload["invite"]["label"] == "Apple Health"
    assert payload["invite"]["server_url"] == "http://admins-mac-mini.tail6c6490.ts.net:8000"
    assert payload["invite"]["code"] == "ABCD2345"
    assert "saved-credential" not in response.get_data(as_text=True)


def test_open_wearables_pair_provider_blocks_unknown_provider(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")

    response = module.app.test_client().post("/api/open-wearables/pair/not-a-provider", json={})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "blocked"
    assert payload["error"]["code"] == "provider_not_supported"


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
