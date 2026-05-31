from __future__ import annotations

import copy
import importlib

import pytest


@pytest.fixture()
def fitness_app(monkeypatch):
    module = importlib.import_module("app")
    settings = copy.deepcopy(module.DEFAULT_SETTINGS)
    settings.update(
        {
            "equipment_preference": "machines_only",
            "preferred_equipment_brands": ["Hoist", "Nautilus"],
            "excluded_exercises": ["Preacher Curl"],
        }
    )
    monkeypatch.setattr(module, "USER_SETTINGS", settings)
    return module


def test_disambiguation_taxonomy_documents_expected_schema(fitness_app):
    expected_fields = {
        "movement_pattern",
        "body_position",
        "shoulder_position",
        "primary_emphasis",
        "match_tokens",
        "confusable_with",
        "disambiguation",
    }

    assert set(fitness_app.EXERCISE_DISAMBIGUATION_METADATA_FIELDS) == expected_fields
    assert fitness_app.EXERCISE_DISAMBIGUATION_CLUSTERS


def test_confusable_exercise_clusters_have_disambiguation_metadata(fitness_app):
    assert fitness_app._exercise_disambiguation_annotation_gaps() == []


def test_confusable_cluster_guard_catches_unannotated_member(fitness_app):
    modified_library = copy.deepcopy(fitness_app.EXERCISE_LIBRARY)
    for exercise in modified_library:
        if exercise["name"] == "Calf Raise (Seated)":
            exercise.pop("match_tokens", None)
            exercise.pop("movement_pattern", None)
            exercise.pop("body_position", None)
            exercise.pop("shoulder_position", None)
            exercise.pop("primary_emphasis", None)
            exercise.pop("disambiguation", None)
            exercise.pop("confusable_with", None)
            break

    gaps = fitness_app._exercise_disambiguation_annotation_gaps(modified_library)

    assert ("calf_raise", "Calf Raise (Seated)", "match_tokens") in gaps
    assert ("calf_raise", "Calf Raise (Seated)", "distinguishing_field") in gaps


def test_ai_cache_key_changes_when_disambiguation_taxonomy_changes(fitness_app, monkeypatch):
    recommendation = {
        "id": "fit-194-cache",
        "goal": fitness_app.TrainingGoal.HYPERTROPHY.value,
        "estimated_minutes": 60,
        "exercises": [
            {
                "exercise": "Shoulder Press",
                "muscle": "shoulders",
                "target_sets": 3,
                "target_reps": 10,
                "target_weight": 60,
                "rpe_target": 8,
            }
        ],
    }

    original_key = fitness_app._ai_cache_key(
        recommendation,
        "swap shoulders to quads",
        "2026-05-30",
        "test-model",
        "machines_only",
    )
    modified_library = copy.deepcopy(fitness_app.EXERCISE_LIBRARY)
    for exercise in modified_library:
        if exercise["name"] == "Shoulder Press":
            exercise["disambiguation"] = "Changed taxonomy metadata for cache test."
            break
    monkeypatch.setattr(fitness_app, "EXERCISE_LIBRARY", modified_library)

    changed_key = fitness_app._ai_cache_key(
        recommendation,
        "swap shoulders to quads",
        "2026-05-30",
        "test-model",
        "machines_only",
    )

    assert changed_key != original_key
