from __future__ import annotations

import importlib
from datetime import datetime

import pytest


CANONICAL_MUSCLES = {
    "chest",
    "back",
    "shoulders",
    "quads",
    "hamstrings",
    "glutes",
    "adductors",
    "biceps",
    "triceps",
    "core",
    "calves",
}


@pytest.fixture()
def fitness_app(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit345-readiness-gating-secret")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "CARDIO_DATA", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "_get_oura_readiness_today", lambda **_kwargs: None)
    yield module
    module.app.config.update(LOGIN_DISABLED=False)


def _soreness(muscles):
    now = datetime.now().isoformat()
    return [
        {"muscle": muscle, "soreness_level": 9, "created_at": now}
        for muscle in muscles
    ]


def _recommended_muscles(recommendation):
    return {
        (exercise.get("muscle") or exercise.get("muscle_group") or "").lower()
        for exercise in recommendation["exercises"]
    }


def test_zero_history_with_all_canonical_muscles_sore_recommends_no_sore_targets(fitness_app):
    fitness_app.SORENESS_DATA = _soreness(CANONICAL_MUSCLES)

    recommendation = fitness_app.generate_next_workout(
        fitness_app.WORKOUTS,
        fitness_app.SORENESS_DATA,
        available_time=45,
    )

    assert _recommended_muscles(recommendation).isdisjoint(CANONICAL_MUSCLES)
    assert {item["muscle"].lower() for item in recommendation["muscles_to_avoid"]} == CANONICAL_MUSCLES


def test_zero_history_avoids_sore_muscles_while_allowing_fresh_muscles(fitness_app):
    # Includes "hamstrings", which sits outside the legacy 4-muscle default
    # {chest, back, quads, shoulders}. Pre-FIT-345, muscles outside that
    # legacy set were never scored for readiness, so a sore-but-unscored
    # muscle could leak into the recommendation via the "fill remaining
    # slots" fallback despite being sore.
    sore_muscles = {"chest", "back", "quads", "hamstrings"}
    fitness_app.SORENESS_DATA = _soreness(sore_muscles)

    recommendation = fitness_app.generate_next_workout(
        fitness_app.WORKOUTS,
        fitness_app.SORENESS_DATA,
        available_time=45,
    )

    recommended_muscles = _recommended_muscles(recommendation)
    assert recommended_muscles
    assert recommended_muscles.isdisjoint(sore_muscles)
    assert recommended_muscles <= CANONICAL_MUSCLES - sore_muscles


def test_trained_muscle_soreness_still_excludes_that_muscle(fitness_app):
    now = datetime.now()
    fitness_app.WORKOUTS.append(
        {
            "id": "fit345-trained-chest",
            "date": now.strftime("%Y-%m-%d"),
            "created_at": now.isoformat(),
            "muscles_trained": [{"muscle": "chest", "sets": 3}],
            "exercises": [{"machine": "Chest Press", "muscle_group": "chest", "sets": []}],
        }
    )
    # "hamstrings" is untrained (no volume history) and sits outside the
    # legacy 4-muscle default {chest, back, quads, shoulders}. Pre-FIT-345,
    # only trained/default-4 muscles were scored for readiness, so a sore
    # untrained muscle like this could bypass the soreness gate entirely.
    fitness_app.SORENESS_DATA = _soreness({"chest", "hamstrings"})

    recommendation = fitness_app.generate_next_workout(
        fitness_app.WORKOUTS,
        fitness_app.SORENESS_DATA,
        available_time=45,
    )

    recommended_muscles = _recommended_muscles(recommendation)
    avoid_muscles = {item["muscle"].lower() for item in recommendation["muscles_to_avoid"]}

    assert "chest" not in recommended_muscles
    assert "chest" in avoid_muscles
    assert "hamstrings" not in recommended_muscles
    assert "hamstrings" in avoid_muscles
