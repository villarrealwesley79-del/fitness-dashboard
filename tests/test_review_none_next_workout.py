"""Reviewer test (PR #264): smart endpoint must survive generate_next_workout returning None."""


def test_smart_endpoint_survives_none_next_workout(isolated_app, monkeypatch):
    monkeypatch.setattr(isolated_app, "generate_next_workout", lambda *a, **k: None)
    resp = isolated_app.app.test_client().get("/api/recommendation/smart")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
