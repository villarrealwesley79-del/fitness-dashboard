"""Reviewer tests (FIT-265): adjust outcomes must match what changed."""
import copy
import importlib


def _module(monkeypatch):
    module = importlib.import_module("app")
    settings = copy.deepcopy(module.DEFAULT_SETTINGS)
    settings.update({"training_goal": module.TrainingGoal.HYPERTROPHY.value, "equipment_preference": "machines_only"})
    monkeypatch.setattr(module, "USER_SETTINGS", settings)
    for name in ("SORENESS_DATA", "CARDIO_DATA", "RECOVERY_DATA", "COMPLETED_WORKOUTS", "WORKOUT_RECOMMENDATIONS", "WORKOUTS"):
        monkeypatch.setattr(module, name, [])
    return module


def _rec(module):
    return {
        "id": "rev-265", "goal": module.TrainingGoal.HYPERTROPHY.value, "estimated_minutes": 45,
        "mesocycle": {"week": 1}, "cardio": None,
        "exercises": [{"exercise": "Chest Press", "muscle": "chest", "target_sets": 3,
                       "target_reps": 10, "target_weight": 100, "rpe_target": 10}],
    }


def _patch(module, intent):
    return module._apply_intent_patch(
        _rec(module), intent, module.GOAL_PARAMETERS[module.TrainingGoal.HYPERTROPHY.value],
        1, module.MESOCYCLE_PLAN[1], None, "machines_only")


def test_noop_rpe_and_drop_cardio_are_not_reported_as_applied(monkeypatch):
    module = _module(monkeypatch)
    # RPE already at the 10 cap; cardio already absent -> nothing can change.
    patched, applied, skipped = _patch(module, {"rpe_delta": 1.0, "drop_cardio": True})
    assert patched["exercises"][0]["rpe_target"] == 10
    assert applied == []
    assert any("RPE" in note for note in skipped)


def test_unlocatable_swap_is_skipped_not_applied(monkeypatch):
    module = _module(monkeypatch)
    patched, applied, skipped = _patch(module, {"swap": [
        {"replace_exercise": "Leg Press", "target_muscle": "quads"}]})
    assert patched["exercises"][0]["exercise"] == "Chest Press"
    assert applied == []
    assert any("could not locate" in note for note in skipped)


def test_outcome_summary_never_claims_change_when_unchanged(monkeypatch):
    module = _module(monkeypatch)
    assert "could not be applied" in module._adjust_outcome_summary(
        "unchanged", [], ["x"], "I swapped your bench press!")
