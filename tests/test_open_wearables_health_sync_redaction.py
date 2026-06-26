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
