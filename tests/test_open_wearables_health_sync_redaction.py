import importlib
import json
from datetime import datetime, timedelta, timezone


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


def test_open_wearables_check_sync_alias_is_metadata_only(monkeypatch):
    module = _fitness_app()
    raw_payload = {
        "sleep": [],
        "workouts": [],
        "activity_summary": [],
        "fetched_at": "2026-07-12T00:00:00",
        "errors": {},
    }
    monkeypatch.setattr(module, "fetch_open_wearables_data", lambda: raw_payload)
    monkeypatch.setattr(
        module,
        "_store_wearable_facts_from_open_wearables",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("metadata check must not write wearable facts")
        ),
    )

    client = module.app.test_client()
    for path in ("/api/open-wearables/check-sync", "/api/health/sync"):
        response = client.post(path)
        assert response.status_code == 200
        assert response.get_json() == {
            "status": "success",
            "source": "open_wearables",
            "fetched_at": "2026-07-12T00:00:00",
            "counts": {"sleep": 0, "workouts": 0, "activity_summary": 0},
            "errors": {},
        }


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


def test_open_wearables_supersedes_direct_oura_and_apple_sources(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "_open_wearables_status_source", lambda: {
        "source": "open_wearables",
        "status": "fresh",
        "hub_status": "connected",
        "connected": True,
        "facts_ready": True,
        "replacement_sources": ["oura", "apple_health"],
        "providers": [
            {"provider_id": "oura", "state": "connected"},
            {"provider_id": "apple", "state": "active"},
        ],
    })

    payload = module._wearable_sources_payload({
        "whoop": {"status": "fresh"},
        "oura": {"status": "stale"},
        "apple_health": {"status": "stale"},
    }, {"status": {"connected": True}, "signals": {}})

    sources = [source["source"] for source in payload]
    assert sources == ["whoop", "open_wearables"]


def test_open_wearables_stale_or_error_providers_do_not_supersede_direct_sources(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "_open_wearables_status_source", lambda: {
        "source": "open_wearables",
        "status": "fresh",
        "hub_status": "connected",
        "connected": True,
        "facts_ready": True,
        "replacement_sources": ["oura", "apple_health"],
        "providers": [
            {"provider_id": "oura", "state": "connected", "stale": True},
            {"provider_id": "apple", "state": "connected", "error_code": "sync_failed"},
        ],
    })

    payload = module._wearable_sources_payload({
        "whoop": {"status": "fresh"},
        "oura": {"status": "stale"},
        "apple_health": {"status": "stale"},
    }, {"status": {"connected": True}, "signals": {}})

    sources = [source["source"] for source in payload]
    assert sources == ["whoop", "oura", "apple_health", "open_wearables"]


def test_open_wearables_generic_facts_ready_without_source_replacement_does_not_supersede(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "_open_wearables_status_source", lambda: {
        "source": "open_wearables",
        "status": "fresh",
        "hub_status": "connected",
        "connected": True,
        "facts_ready": True,
        "replacement_sources": ["apple_health"],
        "providers": [
            {"provider_id": "oura", "state": "connected"},
            {"provider_id": "apple", "state": "connected"},
        ],
    })

    payload = module._wearable_sources_payload({
        "whoop": {"status": "fresh"},
        "oura": {"status": "stale"},
        "apple_health": {"status": "stale"},
    }, {"status": {"connected": True}, "signals": {}})

    sources = [source["source"] for source in payload]
    assert sources == ["whoop", "oura", "open_wearables"]


def test_open_wearables_replacement_sources_use_recent_sync_provenance(monkeypatch, tmp_path):
    module = _fitness_app()
    facts_db = tmp_path / "wearable_facts.sqlite3"
    monkeypatch.setattr(module, "WEARABLE_FACTS_DB_FILE", str(facts_db))
    module.upsert_wearable_source(str(facts_db), {
        "provider_id": "open_wearables",
        "label": "Open Wearables",
        "status": "fresh",
        "last_data_point": "2026-06-28",
        "last_sync_attempt": "2026-06-29T10:00:00",
        "capabilities": {
            "replacement_sources": ["apple_health", "oura", "not_a_source"],
            "replacement_source_dates": {
                "apple_health": "2026-06-28",
                "oura": "2026-06-28",
                "not_a_source": "2026-06-28",
            },
        },
    }, profile_key=module._open_wearables_profile_key())

    sources = module._open_wearables_replacement_sources(now=module.datetime(2026, 6, 29, 12, 0, 0))

    assert sources == ["apple_health", "oura"]


def test_open_wearables_replacement_sources_expire_when_sync_is_stale(monkeypatch, tmp_path):
    module = _fitness_app()
    facts_db = tmp_path / "wearable_facts.sqlite3"
    monkeypatch.setattr(module, "WEARABLE_FACTS_DB_FILE", str(facts_db))
    module.upsert_wearable_source(str(facts_db), {
        "provider_id": "open_wearables",
        "label": "Open Wearables",
        "status": "fresh",
        "last_data_point": "2026-06-20",
        "last_sync_attempt": "2026-06-20T10:00:00",
        "capabilities": {
            "replacement_sources": ["apple_health", "oura"],
            "replacement_source_dates": {
                "apple_health": "2026-06-20",
                "oura": "2026-06-20",
            },
        },
    }, profile_key=module._open_wearables_profile_key())

    sources = module._open_wearables_replacement_sources(now=module.datetime(2026, 6, 29))

    assert sources == []


def test_open_wearables_store_records_replacement_dates_from_fact_provenance(monkeypatch, tmp_path):
    module = _fitness_app()
    facts_db = tmp_path / "wearable_facts.sqlite3"
    monkeypatch.setattr(module, "WEARABLE_FACTS_DB_FILE", str(facts_db))

    facts_count = module._store_wearable_facts_from_open_wearables({
        "fetched_at": "2026-06-29T10:00:00",
        "activity_summary": {"summaries": [
            {"day": "2026-06-28", "steps": 1200, "source": {"provider": "oura"}},
        ]},
        "sleep": {"events": [
            {"end": "2026-06-28T23:58:00Z", "duration_seconds": 3600, "source": {"provider": "Eight Sleep", "device": "Watch6,18"}},
        ]},
    })

    assert facts_count == 2
    stored = module.list_wearable_sources(str(facts_db), profile_key=module._open_wearables_profile_key())
    open_wearables = next(source for source in stored if source["provider_id"] == "open_wearables")
    assert open_wearables["capabilities"]["replacement_sources"] == ["apple_health", "oura"]
    assert open_wearables["capabilities"]["replacement_source_dates"] == {
        "apple_health": "2026-06-28",
        "oura": "2026-06-28",
    }


def test_open_wearables_connected_providers_without_facts_do_not_supersede_direct_sources(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "_open_wearables_status_source", lambda: {
        "source": "open_wearables",
        "status": "fresh",
        "hub_status": "connected",
        "connected": True,
        "facts_ready": False,
        "replacement_sources": [],
        "providers": [
            {"provider_id": "oura", "state": "connected"},
            {"provider_id": "apple", "state": "connected"},
        ],
    })

    payload = module._wearable_sources_payload({
        "whoop": {"status": "fresh"},
        "oura": {"status": "stale"},
        "apple_health": {"status": "stale"},
    }, {"status": {"connected": True}, "signals": {}})

    sources = [source["source"] for source in payload]
    assert sources == ["whoop", "oura", "apple_health", "open_wearables"]


def test_push_alert_preview_suppresses_direct_alerts_owned_by_open_wearables(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "_compute_data_freshness", lambda now=None: {
        "open_wearables": {
            "status": "fresh",
            "hub_status": "connected",
            "connected": True,
            "facts_ready": True,
            "replacement_sources": ["oura", "apple_health"],
            "providers": [
                {"provider_id": "oura", "state": "connected"},
                {"provider_id": "healthkit", "state": "connected"},
            ],
        },
        "oura": {"status": "stale", "last_data_point": "2026-06-24"},
        "apple_health": {"status": "stale", "last_data_point": "2026-06-24"},
        "whoop": {"status": "missing", "connected": False},
        "food": {"pending_review": False},
    })

    payload = module._push_alert_preview()

    assert payload["alerts"] == []


def test_open_wearables_adjusted_freshness_marks_superseded_sources_fresh():
    module = _fitness_app()

    payload = module._open_wearables_adjusted_freshness({
        "open_wearables": {
            "status": "fresh",
            "hub_status": "connected",
            "connected": True,
            "replacement_sources": ["oura", "apple_health"],
            "replacement_source_dates": {
                "oura": "2026-06-28",
                "apple_health": "2026-06-28",
            },
            "providers": [
                {"provider_id": "oura", "state": "connected"},
                {"provider_id": "apple", "state": "connected"},
            ],
        },
        "oura": {"status": "stale", "last_data_point": "2026-06-24"},
        "apple_health": {"status": "stale", "last_data_point": "2026-06-24"},
        "whoop": {"status": "fresh"},
    })

    assert payload["oura"]["status"] == "fresh"
    assert payload["oura"]["superseded_by"] == "open_wearables"
    assert payload["oura"]["last_data_point"] == "2026-06-28"
    assert payload["apple_health"]["status"] == "fresh"
    assert payload["apple_health"]["superseded_by"] == "open_wearables"
    assert payload["apple_health"]["last_data_point"] == "2026-06-28"


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
    assert payload["config"]["managed_connector_restart_required"] is False


def test_open_wearables_public_status_does_not_claim_missing_credentials_when_pairing_ready(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    payload = module._open_wearables_public_status(
        providers=[],
        error_code="open_wearables_no_providers",
        provider_actions=[{
            "provider": "whoop",
            "label": "WHOOP",
            "enabled": True,
            "reason": "",
        }],
    )

    assert payload["status"] == "missing_config"
    assert "setup_hint" not in payload


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
    monkeypatch.setattr(module, "_open_wearables_login", lambda *_args, **_kwargs: ("safe-token", {}))
    monkeypatch.setattr(module, "_open_wearables_verified_user_id", lambda _base, _token, user_id, **_kwargs: user_id)
    credential = "local-" + "credential"
    protected_store = {}
    monkeypatch.setattr(
        module,
        "_save_open_wearables_password",
        lambda value: protected_store.update({"password": value}) is None,
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "_load_open_wearables_password",
        lambda: protected_store.get("password", ""),
        raising=False,
    )

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
    assert "password" not in saved
    assert credential not in config_file.read_text()
    assert protected_store["password"] == credential
    assert module.OPEN_WEARABLES_USERNAME == "local-user"
    assert module.OPEN_WEARABLES_PASSWORD == credential
    assert module.OPEN_WEARABLES_USER_ID == "local-user-id"


def test_open_wearables_startup_migrates_existing_local_password(monkeypatch, tmp_path):
    module = _fitness_app()
    config_file = tmp_path / "open_wearables_config.json"
    legacy_password = "legacy-" + "credential"
    config_file.write_text(json.dumps({
        "base_url": "http://localhost:8000",
        "username": "admin@example.test",
        "password": legacy_password,
        "user_id": "local-user-id",
    }))
    protected_store = {}
    monkeypatch.setattr(module, "OPEN_WEARABLES_CONFIG_FILE", str(config_file))
    monkeypatch.setattr(
        module,
        "_save_open_wearables_password",
        lambda value: protected_store.update({"password": value}) is None,
        raising=False,
    )

    migrated = module._migrate_open_wearables_local_password(json.loads(config_file.read_text()))

    saved = json.loads(config_file.read_text())
    assert "password" not in migrated
    assert "password" not in saved
    assert legacy_password not in config_file.read_text()
    assert protected_store["password"] == legacy_password


def test_open_wearables_setup_save_preserves_sidecar_path_and_restart_flag(monkeypatch, tmp_path):
    module = _fitness_app()
    config_file = tmp_path / "open_wearables_config.json"
    sidecar_env = tmp_path / "custom-open-wearables.env"
    saved_credential = "saved-" + "credential"
    monkeypatch.setattr(module, "OPEN_WEARABLES_CONFIG_FILE", str(config_file))
    monkeypatch.setattr(module, "_fetch_open_wearables_provider_statuses", lambda: ([], "open_wearables_no_providers"))
    monkeypatch.setattr(module, "_open_wearables_login", lambda *_args, **_kwargs: ("safe-token", {}))
    monkeypatch.setattr(module, "_open_wearables_verified_user_id", lambda _base, _token, user_id, **_kwargs: user_id)
    protected_store = {"password": saved_credential}
    password_loads = []
    monkeypatch.setattr(
        module,
        "_save_open_wearables_password",
        lambda value: protected_store.update({"password": value}) is None,
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "_load_open_wearables_password",
        lambda: password_loads.append(True) or protected_store.get("password", ""),
        raising=False,
    )
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {})
    monkeypatch.setattr(
        module,
        "OPEN_WEARABLES_MANAGED_RESTART_REQUIRED",
        module.OPEN_WEARABLES_MANAGED_RESTART_REQUIRED,
    )
    module._apply_open_wearables_runtime_config({
        "base_url": "http://localhost:8000",
        "username": "admin@example.test",
        "user_id": "11111111-1111-4111-8111-111111111111",
        "sidecar_env_path": str(sidecar_env),
        "managed_connector_restart_required": True,
    })
    password_loads.clear()

    def login_with_protected_password(*_args, **_kwargs):
        assert password_loads
        return "safe-token", {}

    monkeypatch.setattr(module, "_open_wearables_login", login_with_protected_password)

    response = module.app.test_client().post("/api/open-wearables/setup", json={
        "base_url": "http://localhost:8000",
        "username": "admin@example.test",
        "user_id": "11111111-1111-4111-8111-111111111111",
    })

    assert response.status_code == 200
    saved = json.loads(config_file.read_text())
    assert "password" not in saved
    assert saved_credential not in config_file.read_text()
    assert protected_store["password"] == saved_credential
    assert password_loads
    assert saved["sidecar_env_path"] == str(sidecar_env)
    assert saved["managed_connector_restart_required"] is True
    assert module.OPEN_WEARABLES_SIDECAR_ENV_PATH == str(sidecar_env)
    assert module.OPEN_WEARABLES_MANAGED_RESTART_REQUIRED is True


def test_open_wearables_setup_save_does_not_persist_default_sidecar_path(monkeypatch, tmp_path):
    module = _fitness_app()
    config_file = tmp_path / "open_wearables_config.json"
    credential = "saved-" + "credential"
    monkeypatch.setattr(module, "OPEN_WEARABLES_CONFIG_FILE", str(config_file))
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {})
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SIDECAR_ENV_PATH", "~/open-wearables/backend/config/.env")
    monkeypatch.delenv("OW_SIDECAR_ENV_PATH", raising=False)
    monkeypatch.setattr(module, "_fetch_open_wearables_provider_statuses", lambda: ([], "missing_config"))

    response = module.app.test_client().post("/api/open-wearables/setup", json={
        "base_url": "http://localhost:8000",
        "username": "admin@example.test",
        "password": credential,
    })

    assert response.status_code == 200
    saved = json.loads(config_file.read_text())
    assert "sidecar_env_path" not in saved


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


def test_open_wearables_setup_rejects_unverified_manual_user_mapping(monkeypatch, tmp_path):
    module = _fitness_app()
    config_file = tmp_path / "open_wearables_config.json"
    monkeypatch.setattr(module, "OPEN_WEARABLES_CONFIG_FILE", str(config_file))
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {})
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "_open_wearables_login", lambda *_args, **_kwargs: ("safe-token", {}))
    monkeypatch.setattr(module, "_open_wearables_verified_user_id", lambda *_args, **_kwargs: "")

    response = module.app.test_client().post("/api/open-wearables/setup", json={
        "base_url": "http://localhost:8000",
        "username": "admin@example.test",
        "password": "saved-credential",
        "user_id": "wrong-open-wearables-user",
    })

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "blocked"
    assert payload["error"]["code"] == "user_mapping_verification_failed"
    assert not config_file.exists()


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


def test_open_wearables_setup_requires_fresh_user_mapping_when_host_changes(monkeypatch, tmp_path):
    module = _fitness_app()
    saved_credential = "saved-" + "credential"
    config_file = tmp_path / "open_wearables_config.json"
    monkeypatch.setattr(module, "OPEN_WEARABLES_CONFIG_FILE", str(config_file))
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {
        "password": saved_credential,
        "user_id": "old-hub-user",
        "profiles": {
            "1": {
                "user_id": "old-hub-user",
                "external_user_id": "fitness-dashboard-user-1",
            }
        },
    })
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", saved_credential)
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "old-hub-user")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "OPEN_WEARABLES_ALLOWED_HOSTS", "wearables.example.com")

    response = module.app.test_client().post("/api/open-wearables/setup", json={
        "base_url": "https://wearables.example.com",
        "username": "remote-user",
        "password": "remote-credential",
        "user_id": "",
    })

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "blocked"
    assert payload["error"]["code"] == "user_mapping_required_for_host_change"
    assert not config_file.exists()


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


def test_open_wearables_config_write_failure_restores_previous_password(monkeypatch):
    module = _fitness_app()
    protected_store = {"password": "previous-credential"}
    monkeypatch.setattr(
        module,
        "_load_open_wearables_password",
        lambda: protected_store.get("password", ""),
    )
    monkeypatch.setattr(
        module,
        "_save_open_wearables_password",
        lambda value: protected_store.update({"password": value}) is None,
    )
    monkeypatch.setattr(
        module,
        "_delete_open_wearables_password",
        lambda: protected_store.pop("password", None),
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "_write_open_wearables_config_file",
        lambda _config: (_ for _ in ()).throw(OSError("config write failed")),
    )

    saved = module._save_open_wearables_local_config({
        "base_url": "http://localhost:8000",
        "password": "replacement-credential",
    })

    assert saved is False
    assert protected_store == {"password": "previous-credential"}


def test_open_wearables_config_write_failure_removes_new_password(monkeypatch):
    module = _fitness_app()
    protected_store = {}
    monkeypatch.setattr(
        module,
        "_load_open_wearables_password",
        lambda: protected_store.get("password", ""),
    )
    monkeypatch.setattr(
        module,
        "_save_open_wearables_password",
        lambda value: protected_store.update({"password": value}) is None,
    )
    monkeypatch.setattr(
        module,
        "_delete_open_wearables_password",
        lambda: protected_store.pop("password", None),
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "_write_open_wearables_config_file",
        lambda _config: (_ for _ in ()).throw(OSError("config write failed")),
    )

    saved = module._save_open_wearables_local_config({
        "base_url": "http://localhost:8000",
        "password": "new-credential",
    })

    assert saved is False
    assert protected_store == {}


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
            "WHOOP_CLIENT_ID=local-whoop-client",
            "WHOOP_CLIENT_SECRET=provider-credential",
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
    monkeypatch.setattr(module, "_open_wearables_provider_settings_from_hub", lambda: ({
        "whoop": {"has_cloud_api": True, "is_enabled": True},
    }, None))
    monkeypatch.setattr(module, "_fetch_open_wearables_provider_statuses", lambda: ([], "open_wearables_no_providers"))
    protected_store = {}
    monkeypatch.setattr(
        module,
        "_save_open_wearables_password",
        lambda value: protected_store.update({"password": value}) is None,
        raising=False,
    )
    monkeypatch.setattr(
        module,
        "_load_open_wearables_password",
        lambda: protected_store.get("password", ""),
        raising=False,
    )

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
    assert payload["config"]["provider_setup_ready"] is True
    assert bootstrap_secret not in response.get_data(as_text=True)
    saved = json.loads(config_file.read_text())
    assert "password" not in saved
    assert bootstrap_secret not in config_file.read_text()
    assert protected_store["password"] == bootstrap_secret
    assert saved["profiles"]["1"]["user_id"] == "11111111-1111-4111-8111-111111111111"
    assert saved["profiles"]["1"]["external_user_id"] == "fitness-dashboard-user-1"
    assert "sidecar_env_path" not in saved
    assert module.OPEN_WEARABLES_PASSWORD == bootstrap_secret


def test_open_wearables_bootstrap_refuses_remote_hub_sidecar_credentials(monkeypatch, tmp_path):
    module = _fitness_app()
    sidecar_env = tmp_path / "open-wearables.env"
    sidecar_env.write_text(
        "\n".join([
            "ADMIN_EMAIL=admin@example.test",
            "ADMIN_PASSWORD=bootstrap-credential",
        ])
    )
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {})
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "https://wearables.example.com")
    monkeypatch.setattr(module, "OPEN_WEARABLES_ALLOWED_HOSTS", "wearables.example.com")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SIDECAR_ENV_PATH", str(sidecar_env))

    def fail_login(*_args, **_kwargs):
        raise AssertionError("local sidecar admin credentials must not be sent to remote hubs")

    monkeypatch.setattr(module, "_open_wearables_login", fail_login)

    bootstrap, error = module._open_wearables_bootstrap_local_hub()

    assert bootstrap is None
    assert error == "remote_hub_requires_manual_credentials"


def test_open_wearables_profile_mapping_uses_current_data_user(monkeypatch):
    module = _fitness_app()
    seen = {}
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 42)
    monkeypatch.setattr(module, "_open_wearables_find_user", lambda *_args: "")

    def fake_create(_base_url, _token, external_user_id):
        seen["external_user_id"] = external_user_id
        return "open-wearables-user-42"

    monkeypatch.setattr(module, "_open_wearables_create_user", fake_create)

    user_id, state = module._open_wearables_resolve_user("http://localhost:8000", "safe-token")

    assert user_id == "open-wearables-user-42"
    assert state == "created"
    assert seen["external_user_id"] == "fitness-dashboard-user-42"


def test_open_wearables_find_user_requires_exact_external_user_id(monkeypatch):
    module = _fitness_app()

    def fake_request(url, **_kwargs):
        assert "external_user_id=fitness-dashboard-user-42" in url
        return {
            "items": [
                {
                    "id": "wrong-open-wearables-user",
                    "external_user_id": "fitness-dashboard-user-7",
                },
                {
                    "id": "open-wearables-user-42",
                    "external_user_id": "fitness-dashboard-user-42",
                },
            ],
        }

    monkeypatch.setattr(module, "_ow_json_request", fake_request)

    assert module._open_wearables_find_user(
        "http://localhost:8000",
        "safe-token",
        "fitness-dashboard-user-42",
    ) == "open-wearables-user-42"


def test_open_wearables_create_user_refuses_unverified_mapping(monkeypatch):
    module = _fitness_app()
    calls = []

    def fake_request(url, **kwargs):
        calls.append((url, kwargs.get("method")))
        if kwargs.get("method") == "POST":
            return {
                "id": "wrong-open-wearables-user",
                "external_user_id": "fitness-dashboard-user-7",
            }
        return {"items": []}

    monkeypatch.setattr(module, "_ow_json_request", fake_request)

    assert module._open_wearables_create_user(
        "http://localhost:8000",
        "safe-token",
        "fitness-dashboard-user-42",
    ) == ""
    assert calls[-1][0].endswith("/api/v1/users?external_user_id=fitness-dashboard-user-42&limit=1")


def test_open_wearables_bootstrap_discards_existing_user_id_with_wrong_external_user(monkeypatch, tmp_path):
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
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "wrong-open-wearables-user")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PORTAL_URL", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SIDECAR_ENV_PATH", str(sidecar_env))
    monkeypatch.setattr(module, "_open_wearables_login", lambda *_args, **_kwargs: ("safe-token", {}))
    monkeypatch.setattr(module, "_open_wearables_seed_managed_provider_credentials", lambda *_args, **_kwargs: {
        "available": True,
        "changed": False,
        "providers": [],
        "restart_required": False,
    })

    def fake_request(url, **kwargs):
        if "/api/v1/users/wrong-open-wearables-user" in url:
            return {
                "id": "wrong-open-wearables-user",
                "external_user_id": "fitness-dashboard-user-7",
            }
        if kwargs.get("method") == "POST":
            return {
                "id": "open-wearables-user-1",
                "external_user_id": "fitness-dashboard-user-1",
            }
        return {"items": []}

    monkeypatch.setattr(module, "_ow_json_request", fake_request)

    bootstrap, error = module._open_wearables_bootstrap_local_hub()

    assert error is None
    assert bootstrap["user_id"] == "open-wearables-user-1"
    saved = json.loads(config_file.read_text())
    assert saved["user_id"] == "open-wearables-user-1"
    assert saved["profiles"]["1"]["external_user_id"] == "fitness-dashboard-user-1"


def test_open_wearables_public_config_uses_profile_mapping_before_legacy_user(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "_open_wearables_provider_actions", lambda **_kwargs: [])
    monkeypatch.setattr(module, "_open_wearables_sidecar_env_available", lambda: False)
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "legacy-owner-user")
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {
        "user_id": "legacy-owner-user",
        "profiles": {
            "42": {
                "user_id": "open-wearables-user-42",
                "external_user_id": "fitness-dashboard-user-42",
            },
        },
    })
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 42)

    profile_config = module._open_wearables_setup_public_config()

    assert profile_config["profile_key"] == "42"
    assert profile_config["user_id"] == "open-wearables-user-42"
    assert profile_config["user_mapped"] is True

    monkeypatch.setattr(module, "_current_data_user_id", lambda: 43)

    other_profile_config = module._open_wearables_setup_public_config()

    assert other_profile_config["profile_key"] == "43"
    assert other_profile_config["user_id"] == ""
    assert other_profile_config["user_mapped"] is False


def test_open_wearables_setup_save_preserves_other_profile_mappings(monkeypatch, tmp_path):
    module = _fitness_app()
    config_file = tmp_path / "open_wearables_config.json"
    monkeypatch.setattr(module, "OPEN_WEARABLES_CONFIG_FILE", str(config_file))
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {
        "user_id": "legacy-owner-user",
        "profiles": {
            "7": {
                "user_id": "open-wearables-user-7",
                "external_user_id": "fitness-dashboard-user-7",
            },
        },
    })
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "legacy-owner-user")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 42)
    monkeypatch.setattr(module, "_fetch_open_wearables_provider_statuses", lambda: ([], "open_wearables_no_providers"))
    monkeypatch.setattr(module, "_open_wearables_login", lambda *_args, **_kwargs: ("safe-token", {}))
    monkeypatch.setattr(module, "_open_wearables_verified_user_id", lambda _base, _token, user_id, **_kwargs: user_id)

    response = module.app.test_client().post("/api/open-wearables/setup", json={
        "base_url": "http://localhost:8000",
        "username": "admin@example.test",
        "user_id": "open-wearables-user-42",
    })

    assert response.status_code == 200
    saved = json.loads(config_file.read_text())
    assert saved["user_id"] == "legacy-owner-user"
    assert saved["profiles"]["7"]["user_id"] == "open-wearables-user-7"
    assert saved["profiles"]["42"]["user_id"] == "open-wearables-user-42"
    assert saved["profiles"]["42"]["external_user_id"] == "fitness-dashboard-user-42"


def test_open_wearables_setup_remap_clears_profile_cache_and_facts(monkeypatch, tmp_path):
    module = _fitness_app()
    config_file = tmp_path / "open_wearables_config.json"
    facts_db = tmp_path / "wearable_facts.sqlite3"
    monkeypatch.setattr(module, "OPEN_WEARABLES_CONFIG_FILE", str(config_file))
    monkeypatch.setattr(module, "WEARABLE_FACTS_DB_FILE", str(facts_db))
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {
        "profiles": {
            "42": {
                "user_id": "old-open-wearables-user-42",
                "external_user_id": "fitness-dashboard-user-42",
            },
            "7": {
                "user_id": "open-wearables-user-7",
                "external_user_id": "fitness-dashboard-user-7",
            },
        },
    })
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 42)
    monkeypatch.setattr(module, "_fetch_open_wearables_provider_statuses", lambda: ([], "open_wearables_no_providers"))
    monkeypatch.setattr(module, "_open_wearables_login", lambda *_args, **_kwargs: ("safe-token", {}))
    monkeypatch.setattr(module, "_open_wearables_verified_user_id", lambda _base, _token, user_id, **_kwargs: user_id)
    monkeypatch.setattr(module, "OPEN_WEARABLES_WORKOUT_MARKER_CACHE", {"42": {"configured": True}})
    module.upsert_daily_facts(str(facts_db), [
        module.WearableDailyFact("2026-06-26", "open_wearables", "Open Wearables", "sleep_duration", 430, "min"),
    ], profile_key="42")

    response = module.app.test_client().post("/api/open-wearables/setup", json={
        "base_url": "http://localhost:8000",
        "username": "admin@example.test",
        "user_id": "new-open-wearables-user-42",
    })

    assert response.status_code == 200
    saved = json.loads(config_file.read_text())
    assert saved["profiles"]["42"]["user_id"] == "new-open-wearables-user-42"
    assert saved["profiles"]["7"]["user_id"] == "open-wearables-user-7"
    assert module.OPEN_WEARABLES_WORKOUT_MARKER_CACHE == {}
    assert module.list_recommendation_facts(str(facts_db), profile_key="42") == []


def test_open_wearables_sync_uses_current_profile_user_mapping(monkeypatch):
    module = _fitness_app()
    requested = []
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 42)
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {
        "profiles": {
            "1": {"user_id": "open-wearables-user-1"},
            "42": {"user_id": "open-wearables-user-42"},
        },
    })
    monkeypatch.setattr(module, "_get_ow_token", lambda: "safe-token")

    def fake_ow_request(url, **_kwargs):
        requested.append(url)
        return {"items": []}

    monkeypatch.setattr(module, "_ow_request", fake_ow_request)

    payload = module.fetch_open_wearables_data()

    assert payload["errors"] == {}
    assert requested
    assert all("/api/v1/users/open-wearables-user-42/" in url for url in requested)
    assert not any("open-wearables-user-1" in url for url in requested)


def test_open_wearables_recommendation_marker_cache_is_profile_scoped(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "_missing_open_wearables_config", lambda: [])
    monkeypatch.setattr(module, "OPEN_WEARABLES_WORKOUT_MARKER_CACHE", {})

    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    profile_one = module._store_open_wearables_recommendation_marker({
        "sleep": {"events": [{"end_time": "2026-06-26T07:00:00", "duration_min": 480}]}
    })
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 2)
    profile_two = module._store_open_wearables_recommendation_marker({
        "sleep": {"events": [{"end_time": "2026-06-26T07:00:00", "duration_min": 120}]}
    })

    assert profile_one["sleep"]["duration_min"] == 480
    assert profile_two["sleep"]["duration_min"] == 120
    assert module._open_wearables_recommendation_marker()["sleep"]["duration_min"] == 120
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    assert module._open_wearables_recommendation_marker()["sleep"]["duration_min"] == 480


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


def test_open_wearables_redirect_only_update_does_not_require_restart(monkeypatch, tmp_path):
    module = _fitness_app()
    sidecar_env = tmp_path / "open-wearables.env"
    sidecar_env.write_text(
        "\n".join([
            "ADMIN_EMAIL=admin@example.test",
            "ADMIN_PASSWORD=bootstrap-credential",
            "WHOOP_CLIENT_ID=public-client-id",
            "WHOOP_CLIENT_SECRET=private-secret-id",
            "WHOOP_REDIRECT_URI=http://old.example/callback",
        ])
    )

    monkeypatch.setattr(module, "OPEN_WEARABLES_SIDECAR_ENV_PATH", str(sidecar_env))
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "OPEN_WEARABLES_MANAGED_RESTART_REQUIRED", False)

    def fail_whoop_config(*_args, **_kwargs):
        raise RuntimeError("owner setup needed")

    monkeypatch.setattr(module, "load_whoop_config", fail_whoop_config)

    with module.app.test_request_context(
        "/api/open-wearables/setup",
        base_url="https://admins-mac-mini.tail6c6490.ts.net:5050",
    ):
        result = module._open_wearables_seed_managed_provider_credentials()

    assert result["changed"] is True
    assert result["providers"] == [{"provider": "whoop", "status": "owner_setup_needed"}]
    assert result["restart_required"] is False
    assert module.OPEN_WEARABLES_MANAGED_RESTART_REQUIRED is False
    saved_env = sidecar_env.read_text()
    assert "WHOOP_REDIRECT_URI=https://admins-mac-mini.tail6c6490.ts.net:5050/api/whoop/callback" in saved_env


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


def test_open_wearables_managed_env_backup_is_restricted(monkeypatch, tmp_path):
    module = _fitness_app()
    sidecar_env = tmp_path / "open-wearables.env"
    sidecar_env.write_text("\n".join([
        "ADMIN_EMAIL=admin@example.test",
        "ADMIN_PASSWORD=bootstrap-credential",
        "WHOOP_CLIENT_ID=local-whoop-client",
        "WHOOP_CLIENT_SECRET=provider-credential",
        "WHOOP_DEFAULT_SCOPE=old-scope",
    ]))
    sidecar_env.chmod(0o644)
    monkeypatch.setattr(module, "OPEN_WEARABLES_SIDECAR_ENV_PATH", str(sidecar_env))
    monkeypatch.setattr(module, "OPEN_WEARABLES_MANAGED_RESTART_REQUIRED", False)

    result = module._open_wearables_seed_managed_provider_credentials()

    backups = list(tmp_path.glob("open-wearables.env.before-managed-connectors-*"))
    assert result["changed"] is True
    assert backups
    assert (backups[0].stat().st_mode & 0o777) == 0o600
    assert (sidecar_env.stat().st_mode & 0o777) == 0o600


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


def test_open_wearables_provider_actions_check_default_sidecar_credentials(monkeypatch, tmp_path):
    module = _fitness_app()
    sidecar_env = tmp_path / "open-wearables.env"
    sidecar_env.write_text("\n".join([
        "ADMIN_EMAIL=admin@example.test",
        "ADMIN_PASSWORD=bootstrap-credential",
        "WHOOP_CLIENT_ID=public-client-id",
        "WHOOP_CLIENT_SECRET=private-secret-id",
    ]))
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {
        "base_url": "http://localhost:8000",
        "username": "admin@example.test",
        "password": "saved-credential",
        "user_id": "11111111-1111-4111-8111-111111111111",
    })
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SIDECAR_ENV_PATH", str(sidecar_env))
    monkeypatch.setattr(module, "OPEN_WEARABLES_MANAGED_RESTART_REQUIRED", False)
    monkeypatch.setattr(module, "_open_wearables_provider_settings_from_hub", lambda: ({
        "whoop": {"has_cloud_api": True, "is_enabled": True},
    }, None))

    whoop = next(action for action in module._open_wearables_provider_actions() if action["provider"] == "whoop")

    assert whoop["enabled"] is False
    assert whoop["reason"] == "provider_app_needed"
    assert "sidecar_env_path" not in module.OPEN_WEARABLES_LOCAL_CONFIG


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


def test_open_wearables_provider_actions_keep_sdk_provider_when_catalog_unavailable(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_MANAGED_RESTART_REQUIRED", False)
    monkeypatch.setattr(module, "_open_wearables_provider_settings_from_hub", lambda: ({}, "provider_catalog_unavailable"))

    apple = next(action for action in module._open_wearables_provider_actions() if action["provider"] == "apple")

    assert apple["enabled"] is False
    assert apple["kind"] == "sdk"
    assert apple["reason"] == "sdk_provider"


def test_open_wearables_provider_actions_keep_cloud_provider_when_api_not_ready(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_MANAGED_RESTART_REQUIRED", False)
    monkeypatch.setattr(module, "_open_wearables_provider_settings_from_hub", lambda: ({
        "oura": {"has_cloud_api": False, "is_enabled": True},
    }, None))

    oura = next(action for action in module._open_wearables_provider_actions() if action["provider"] == "oura")

    assert oura["enabled"] is False
    assert oura["kind"] == "cloud"
    assert oura["reason"] == "provider_not_ready"
    assert oura["url"] == ""


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
    monkeypatch.setattr(module, "_open_wearables_provider_authorization_ready", lambda _provider, *_args, **_kwargs: True)

    read_whoop = next(action for action in module._open_wearables_provider_actions() if action["provider"] == "whoop")

    assert module.OPEN_WEARABLES_MANAGED_RESTART_REQUIRED is True
    assert read_whoop["enabled"] is False
    assert read_whoop["reason"] == "hub_restart_needed"
    assert not config_file.exists()

    whoop = next(action for action in module._open_wearables_provider_actions(
        allow_managed_restart_clear=True
    ) if action["provider"] == "whoop")

    assert module.OPEN_WEARABLES_MANAGED_RESTART_REQUIRED is False
    assert whoop["enabled"] is True
    assert whoop["reason"] == ""
    assert "managed_connector_restart_required" not in json.loads(config_file.read_text())


def test_open_wearables_provider_actions_keep_restart_until_hub_authorizes(monkeypatch, tmp_path):
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
    monkeypatch.setattr(module, "_open_wearables_provider_authorization_ready", lambda _provider, *_args, **_kwargs: False)

    whoop = next(action for action in module._open_wearables_provider_actions(
        allow_managed_restart_clear=True
    ) if action["provider"] == "whoop")

    assert module.OPEN_WEARABLES_MANAGED_RESTART_REQUIRED is True
    assert whoop["enabled"] is False
    assert whoop["reason"] == "hub_restart_needed"
    assert not config_file.exists()


def test_open_wearables_default_sidecar_remains_managed_until_restart_clears(monkeypatch, tmp_path):
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
    monkeypatch.setattr(module, "_open_wearables_provider_authorization_ready", lambda _provider: True)

    whoop = next(action for action in module._open_wearables_provider_actions(
        allow_managed_restart_clear=True
    ) if action["provider"] == "whoop")

    assert module.OPEN_WEARABLES_MANAGED_RESTART_REQUIRED is False
    assert whoop["enabled"] is True
    assert "managed_connector_restart_required" not in json.loads(config_file.read_text())


def test_open_wearables_status_source_fetches_provider_aware_status(monkeypatch):
    from open_wearables_adapter import OpenWearablesProviderStatus

    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "_fetch_open_wearables_provider_statuses", lambda: ([
        OpenWearablesProviderStatus(
            provider_id="whoop",
            label="WHOOP",
            state="connected",
            capabilities={"metrics": True, "workouts": True},
        )
    ], None))

    payload = module._open_wearables_status_source()

    assert payload["status"] == "connected"
    assert payload["connected"] is True
    assert payload["used_for_recommendation"] is True


def test_open_wearables_freshness_uses_provider_aware_status(monkeypatch):
    from open_wearables_adapter import OpenWearablesProviderStatus

    module = _fitness_app()
    monkeypatch.setattr(module, "_latest_oura_freshness", lambda _now: ("missing", None, None))
    monkeypatch.setattr(module, "latest_whoop_freshness", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(module, "_latest_apple_health_freshness", lambda _now: ("missing", None, None))
    monkeypatch.setattr(module, "_latest_food_freshness", lambda _now: ("missing", None, None))
    monkeypatch.setattr(module, "_food_target_state", lambda _now: {})
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "_fetch_open_wearables_provider_statuses", lambda: ([
        OpenWearablesProviderStatus(
            provider_id="whoop",
            label="WHOOP",
            state="connected",
            capabilities={"metrics": True, "workouts": True},
        )
    ], None))

    freshness = module._compute_data_freshness()

    assert freshness["open_wearables"]["status"] == "fresh"
    assert freshness["open_wearables"]["hub_status"] == "connected"


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


def test_open_wearables_provider_actions_allow_manual_hub_without_sidecar(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {})
    monkeypatch.delenv("OW_SIDECAR_ENV_PATH", raising=False)
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_MANAGED_RESTART_REQUIRED", False)
    monkeypatch.setattr(module, "_open_wearables_sidecar_env_available", lambda: False)
    monkeypatch.setattr(module, "_load_open_wearables_sidecar_env", lambda: ({}, "sidecar_env_missing"))
    monkeypatch.setattr(module, "_open_wearables_provider_settings_from_hub", lambda: ({
        "whoop": {"has_cloud_api": True, "is_enabled": True},
    }, None))

    whoop = next(action for action in module._open_wearables_provider_actions() if action["provider"] == "whoop")

    assert whoop["enabled"] is True
    assert whoop["url"] == "/api/open-wearables/pair/whoop"
    assert whoop["reason"] == ""


def test_open_wearables_authorization_allows_manual_hub_without_sidecar(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {})
    monkeypatch.delenv("OW_SIDECAR_ENV_PATH", raising=False)
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "OPEN_WEARABLES_MANAGED_RESTART_REQUIRED", False)
    monkeypatch.setattr(module, "_open_wearables_sidecar_env_available", lambda: False)
    monkeypatch.setattr(module, "_load_open_wearables_sidecar_env", lambda: ({}, "sidecar_env_missing"))
    monkeypatch.setattr(module, "_open_wearables_provider_settings_from_hub", lambda: ({
        "whoop": {"has_cloud_api": True, "is_enabled": True},
    }, None))
    monkeypatch.setattr(module, "_ow_json_request", lambda *_args, **_kwargs: {
        "authorization_url": "https://api.prod.whoop.com/oauth/oauth2/auth?client_id=abc",
    })

    url, error = module._open_wearables_authorization_url("whoop")

    assert error is None
    assert url.startswith("https://api.prod.whoop.com/oauth/oauth2/auth")


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
    monkeypatch.setattr(module, "_ow_json_request", lambda *_args, **_kwargs: {
        "id": "11111111-1111-4111-8111-111111111111",
        "external_user_id": "fitness-dashboard-user-1",
    })
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
    monkeypatch.setattr(module, "_open_wearables_provider_credentials_ready", lambda _provider, *_args, **_kwargs: True)
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
    monkeypatch.setattr(module, "_open_wearables_provider_credentials_ready", lambda _provider, *_args, **_kwargs: True)

    def fail_request(*_args, **_kwargs):
        raise AssertionError("disallowed Open Wearables host was requested")

    monkeypatch.setattr(module, "_ow_json_request", fail_request)

    response = module.app.test_client().post("/api/open-wearables/pair/oura", json={})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "blocked"
    assert payload["error"]["code"] == "remote_host_not_allowed"
    assert "saved-credential" not in response.get_data(as_text=True)


def test_open_wearables_pair_provider_treats_cloud_api_missing_as_not_ready(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "_open_wearables_provider_settings_from_hub", lambda: ({
        "oura": {"has_cloud_api": False, "is_enabled": True},
    }, None))

    def fail_request(*_args, **_kwargs):
        raise AssertionError("provider authorize endpoint should not be requested before cloud API is ready")

    monkeypatch.setattr(module, "_ow_json_request", fail_request)

    response = module.app.test_client().post("/api/open-wearables/pair/oura", json={})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "blocked"
    assert payload["error"]["code"] == "provider_not_ready"
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
    monkeypatch.setattr(module, "_open_wearables_provider_credentials_ready", lambda _provider, *_args, **_kwargs: True)
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
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {"sidecar_env_path": "/tmp/open-wearables.env"})
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "_open_wearables_provider_credentials_ready", lambda _provider, *_args, **_kwargs: False)
    monkeypatch.setattr(module, "_open_wearables_provider_settings_from_hub", lambda: ({
        "oura": {"has_cloud_api": True, "is_enabled": True},
    }, None))

    response = module.app.test_client().post("/api/open-wearables/pair/oura", json={})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "blocked"
    assert payload["error"]["code"] == "provider_app_needed"
    assert "saved-credential" not in response.get_data(as_text=True)


def test_open_wearables_pair_provider_ignores_unmanaged_placeholder_sidecar(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {})
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "_open_wearables_sidecar_env_available", lambda: False)
    monkeypatch.setattr(module, "_open_wearables_provider_credentials_ready", lambda _provider, *_args, **_kwargs: False)
    monkeypatch.setattr(module, "_open_wearables_provider_settings_from_hub", lambda: ({
        "oura": {"has_cloud_api": True, "is_enabled": True},
    }, None))
    monkeypatch.setattr(module, "_ow_json_request", lambda *_args, **_kwargs: {
        "authorization_url": "https://oura.example.test/oauth",
    })

    response = module.app.test_client().post("/api/open-wearables/pair/oura", json={})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ready"
    assert payload["authorization_url"] == "https://oura.example.test/oauth"


def test_open_wearables_pair_provider_blocks_missing_managed_sidecar(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_LOCAL_CONFIG", {"sidecar_env_path": "/tmp/missing-open-wearables.env"})
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "_load_open_wearables_sidecar_env", lambda: ({}, "sidecar_env_missing"))
    monkeypatch.setattr(module, "_open_wearables_provider_settings_from_hub", lambda: ({
        "oura": {"has_cloud_api": True, "is_enabled": True},
    }, None))

    response = module.app.test_client().post("/api/open-wearables/pair/oura", json={})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "blocked"
    assert payload["error"]["code"] == "provider_app_needed"

    oura = next(action for action in module._open_wearables_provider_actions() if action["provider"] == "oura")
    assert oura["enabled"] is False
    assert oura["reason"] == "provider_app_needed"


def test_open_wearables_pair_provider_blocks_missing_hub_catalog(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "_open_wearables_provider_credentials_ready", lambda _provider, *_args, **_kwargs: True)
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
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    def fake_json_request(url, **kwargs):
        assert kwargs.get("method") == "POST"
        assert kwargs.get("token") == "safe-test-token"
        assert url.endswith("/api/v1/users/11111111-1111-4111-8111-111111111111/invitation-code")
        return {"code": "ABCD2345", "expires_at": expires_at}

    monkeypatch.setattr(module, "_ow_json_request", fake_json_request)

    response = module.app.test_client().post(
        "/api/open-wearables/mobile-invite/apple",
        json={},
        base_url="http://admins-mac-mini.tail6c6490.ts.net:5050",
        headers={"X-Requested-With": "XMLHttpRequest"},
        environ_overrides={"fitness_dashboard.omit_auto_csrf_header": True},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ready"
    assert payload["invite"]["provider"] == "apple"
    assert payload["invite"]["label"] == "Apple Health"
    assert payload["invite"]["server_url"] == "http://admins-mac-mini.tail6c6490.ts.net:8000"
    assert payload["invite"]["code"] == "ABCD2345"
    assert datetime.fromisoformat(payload["invite"]["expires_at"].replace("Z", "+00:00")) > datetime.now(timezone.utc)
    assert "saved-credential" not in response.get_data(as_text=True)


def test_open_wearables_mobile_invite_brackets_ipv6_dashboard_host(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")
    monkeypatch.setattr(module, "_get_ow_token", lambda: "safe-test-token")
    monkeypatch.setattr(module, "_ow_json_request", lambda *_args, **_kwargs: {
        "code": "ABCD2345",
        "expires_at": "2026-06-28T09:00:00Z",
    })

    response = module.app.test_client().post(
        "/api/open-wearables/mobile-invite/apple",
        json={},
        base_url="http://[2001:db8::10]:5050",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["invite"]["server_url"] == "http://[2001:db8::10]:8000"
    assert "saved-credential" not in response.get_data(as_text=True)


def test_open_wearables_mobile_invite_blocks_loopback_server_url(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://localhost:8000")

    def fail_request(*_args, **_kwargs):
        raise AssertionError("phone app code should not be requested for loopback server URLs")

    monkeypatch.setattr(module, "_get_ow_token", lambda: "safe-test-token")
    monkeypatch.setattr(module, "_ow_json_request", fail_request)

    response = module.app.test_client().post(
        "/api/open-wearables/mobile-invite/apple",
        json={},
        base_url="http://localhost:5050",
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "blocked"
    assert payload["error"]["code"] == "public_hub_url_required"
    assert "saved-credential" not in response.get_data(as_text=True)


def test_open_wearables_mobile_invite_blocks_ipv6_loopback_server_url(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "http://[::1]:8000")

    def fail_request(*_args, **_kwargs):
        raise AssertionError("phone app code should not be requested for IPv6 loopback server URLs")

    monkeypatch.setattr(module, "_get_ow_token", lambda: "safe-test-token")
    monkeypatch.setattr(module, "_ow_json_request", fail_request)

    response = module.app.test_client().post(
        "/api/open-wearables/mobile-invite/apple",
        json={},
        base_url="http://localhost:5050",
    )

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["status"] == "blocked"
    assert payload["error"]["code"] == "public_hub_url_required"
    assert "saved-credential" not in response.get_data(as_text=True)


def test_open_wearables_mobile_invite_preserves_public_hub_url(monkeypatch):
    module = _fitness_app()
    monkeypatch.setattr(module, "OPEN_WEARABLES_USERNAME", "admin@example.test")
    monkeypatch.setattr(module, "OPEN_WEARABLES_PASSWORD", "saved-credential")
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setattr(module, "OPEN_WEARABLES_SERVICE_BASE", "https://wearables.example.com")
    monkeypatch.setattr(module, "OPEN_WEARABLES_ALLOWED_HOSTS", "wearables.example.com")
    monkeypatch.setattr(module, "_get_ow_token", lambda: "safe-test-token")
    monkeypatch.setattr(module, "_ow_json_request", lambda *_args, **_kwargs: {
        "code": "ABCD2345",
        "expires_at": "2026-06-28T09:00:00Z",
    })

    response = module.app.test_client().post(
        "/api/open-wearables/mobile-invite/apple",
        json={},
        base_url="https://dashboard.example.com",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["invite"]["server_url"] == "https://wearables.example.com"
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
