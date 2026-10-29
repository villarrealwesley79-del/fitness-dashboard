"""Reviewer tests for FIT-271 (PR #264): recommendation source proof contract."""

import pytest

from recommendation_sources import build_recommendation_source_proof

SOURCES = {"open_wearables", "sleep", "recovery", "activity", "weather"}
DOMAINS = {"sleep", "recovery", "activity"}
DOMAIN_KEYS = {"provider", "via", "date", "freshness", "conflict_state"}
NODE_KEYS = {"used_for_recommendation", "fields_used", "modifier_applied", "ignored_reason"}


def _proof(freshness, ow_used=False, **inputs):
    return build_recommendation_source_proof(
        freshness=freshness,
        open_wearables_source={"used_for_recommendation": ow_used, "freshness": "missing"},
        source_inputs=inputs,
    )


def test_unstubbed_smart_endpoint_on_empty_data_exposes_honest_sources(isolated_client):
    resp = isolated_client.get("/api/recommendation/smart")
    assert resp.status_code == 200, resp.get_data(as_text=True)[:500]
    proof = resp.get_json()["recommendation_sources"]["source_proof"]
    assert set(proof) == SOURCES
    for name, node in proof.items():
        assert set(node) == (NODE_KEYS | DOMAIN_KEYS if name in DOMAINS else NODE_KEYS), name
        # Nothing exists in a fresh DATA_DIR, so nothing may claim influence.
        assert node["used_for_recommendation"] is False, (name, node)
        assert node["fields_used"] == [], (name, node)
        assert node["modifier_applied"] is False, (name, node)
        assert node["ignored_reason"], (name, node)


def test_smart_endpoint_probes_wearable_freshness_once_per_request(isolated_app, monkeypatch):
    # PER-1254: "Compute the complete freshness snapshot once per
    # smart-recommendation request and reuse it."
    real = isolated_app._latest_wearable_fact_freshness
    calls = []

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(isolated_app, "_latest_wearable_fact_freshness", counting)
    resp = isolated_app.app.test_client().get("/api/recommendation/smart")
    assert resp.status_code == 200
    assert len(calls) == 1, f"_latest_wearable_fact_freshness ran {len(calls)} times in one request"


@pytest.mark.parametrize("status,reason", [("stale", "stale_source"), ("missing", "missing_data")])
def test_stale_and_missing_weather_are_distinguished(status, reason):
    proof = _proof({"weather": {"status": status}}, weather_fields=["temp_f"], weather_status=status)
    assert proof["weather"]["used_for_recommendation"] is False
    assert proof["weather"]["ignored_reason"] == reason


def test_modifier_flag_never_true_when_source_unused():
    proof = _proof({"weather": {"status": "missing"}}, weather_fields=[], weather_modifier_applied=True)
    assert proof["weather"]["modifier_applied"] is False
