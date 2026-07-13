import importlib
import io
import urllib.error


def _fitness_app(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "oura-sync-test-secret")
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "oura-sync-health-token")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "OURA_DB_FILE", str(tmp_path / "oura.sqlite3"))
    return module.app


def test_oura_sync_reports_missing_token_actionably(monkeypatch, tmp_path):
    app = _fitness_app(monkeypatch, tmp_path)
    monkeypatch.delenv("OURA_API_TOKEN", raising=False)

    response = app.test_client().post("/api/oura/sync-sleep", json={"days_back": 30})

    assert response.status_code == 503
    payload = response.get_json()
    assert payload["error"]["code"] == "missing_oura_token"
    assert "Set OURA_API_TOKEN" in payload["error"]["message"]


def test_oura_sync_validates_days_back(monkeypatch, tmp_path):
    app = _fitness_app(monkeypatch, tmp_path)
    monkeypatch.setenv("OURA_API_TOKEN", "test-token")

    response = app.test_client().post("/api/oura/sync-sleep", json={"days_back": "bad"})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload["error"]["code"] == "invalid_field"
    assert payload["error"]["message"] == "days_back must be an integer"


def test_oura_sync_success_returns_summary(monkeypatch, tmp_path):
    app = _fitness_app(monkeypatch, tmp_path)
    monkeypatch.setenv("OURA_API_TOKEN", "test-token")
    oura_sleep_sync = importlib.import_module("oura_sleep_sync")
    calls = {}

    def fake_sync_sleep_data(db_path, api_token, start_date):
        calls["db_path"] = db_path
        calls["api_token"] = api_token
        calls["start_date"] = start_date

    monkeypatch.setattr(oura_sleep_sync, "sync_sleep_data", fake_sync_sleep_data)
    monkeypatch.setattr(
        oura_sleep_sync,
        "get_latest_sleep",
        lambda *_args, **_kwargs: [{"day": "2026-05-18"}, {"day": "2026-05-17"}],
    )

    response = app.test_client().post("/api/oura/sync-sleep", json={"days_back": 7})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "success"
    assert payload["synced_through"] >= payload["synced_from"]
    assert payload["latest_records"] == 2
    assert payload["latest_days"] == ["2026-05-18", "2026-05-17"]
    assert calls["api_token"] == "test-token"


def test_oura_sync_redacts_upstream_response_body(monkeypatch, tmp_path):
    app = _fitness_app(monkeypatch, tmp_path)
    monkeypatch.setenv("OURA_API_TOKEN", "test-token")
    oura_sleep_sync = importlib.import_module("oura_sleep_sync")
    raw_detail = b'{"error":"invalid_token","token":"oura-secret-token-value"}'

    def fail_sync(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "https://api.ouraring.com/v2/usercollection/sleep",
            401,
            "Unauthorized",
            {},
            io.BytesIO(raw_detail),
        )

    monkeypatch.setattr(oura_sleep_sync, "sync_sleep_data", fail_sync)

    response = app.test_client().post("/api/oura/sync-sleep", json={"days_back": 7})

    assert response.status_code == 502
    payload = response.get_json()
    assert payload["error"] == {
        "code": "oura_api_error",
        "message": "Oura API returned HTTP 401.",
    }
    assert "oura-secret-token-value" not in response.get_data(as_text=True)
    assert "invalid_token" not in response.get_data(as_text=True)


def test_oura_sync_redacts_url_error_reason(monkeypatch, tmp_path):
    app = _fitness_app(monkeypatch, tmp_path)
    monkeypatch.setenv("OURA_API_TOKEN", "test-token")
    oura_sleep_sync = importlib.import_module("oura_sleep_sync")

    def fail_sync(*_args, **_kwargs):
        raise urllib.error.URLError("provider rejected oura-secret-token-value")

    monkeypatch.setattr(oura_sleep_sync, "sync_sleep_data", fail_sync)

    response = app.test_client().post("/api/oura/sync-sleep", json={"days_back": 7})

    assert response.status_code == 502
    assert response.get_json()["error"] == {
        "code": "oura_api_error",
        "message": "Oura API request failed.",
    }
    assert "oura-secret-token-value" not in response.get_data(as_text=True)


def test_oura_sync_redacts_unexpected_exception_detail(monkeypatch, tmp_path):
    app = _fitness_app(monkeypatch, tmp_path)
    monkeypatch.setenv("OURA_API_TOKEN", "test-token")
    oura_sleep_sync = importlib.import_module("oura_sleep_sync")

    def fail_sync(*_args, **_kwargs):
        raise RuntimeError("raw provider payload contained oura-secret-token-value")

    monkeypatch.setattr(oura_sleep_sync, "sync_sleep_data", fail_sync)

    response = app.test_client().post("/api/oura/sync-sleep", json={"days_back": 7})

    assert response.status_code == 500
    assert response.get_json()["error"] == {
        "code": "oura_sync_failed",
        "message": "Oura sync failed.",
    }
    assert "oura-secret-token-value" not in response.get_data(as_text=True)
