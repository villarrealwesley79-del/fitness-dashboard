import importlib

from history_normalization import canonical_training_category, normalize_history_item


def test_strength_labels_share_canonical_category():
    labels = [
        "Lifted",
        "Functional Strength Training",
        "Traditional Strength Training",
        "Weight Training",
        "Resistance Training",
    ]
    assert {canonical_training_category(label) for label in labels} == {"strength_training"}


def test_history_normalization_preserves_original_label_and_source():
    row = normalize_history_item({"date": "2026-06-26", "activity_type": "Functional Strength Training"}, source="apple health")
    assert row["canonical_category"] == "strength_training"
    assert row["original_label"] == "Functional Strength Training"
    assert row["source_label"] == "Strength - Watch"


def test_history_endpoints_emit_canonical_category(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    module.WORKOUTS[:] = [{
        "id": "fit245-history",
        "date": "2026-06-26",
        "session_type": "push",
        "duration_minutes": 45,
        "exercises": [],
    }]

    payload = module.app.test_client().get("/api/history-all").get_json()

    assert payload["workouts"][0]["canonical_category"] == "strength_training"
    assert payload["workouts"][0]["original_label"] == "Lifted"
