"""Reviewer baseline: Oura freshness probe count on main's smart endpoint (for PR #264)."""


def test_main_smart_endpoint_oura_probe_count(isolated_app, monkeypatch):
    real = isolated_app._latest_oura_freshness
    calls = []
    monkeypatch.setattr(isolated_app, "_latest_oura_freshness", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    assert isolated_app.app.test_client().get("/api/recommendation/smart").status_code == 200
    assert len(calls) == 1
