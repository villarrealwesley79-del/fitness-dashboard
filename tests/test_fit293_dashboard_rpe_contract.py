import importlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_workout_contract_exposes_exercise_rpe_range():
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)

    workout = {
        "goal": "hypertrophy",
        "exercises": [
            {"exercise": "Chest Press", "rpe_target": 7},
            {"exercise": "Lateral Raise", "rpe_target": 8},
            {"exercise": "Cable Fly", "rpe_target": None},
        ],
    }

    scoped = module._workout_with_auth_scope(workout)

    assert scoped["rpe_range"] == {"min": 7, "max": 8, "label": "7–8"}
    assert "rpe_range" not in workout


def test_workout_contract_omits_rpe_range_without_exercise_targets():
    module = importlib.import_module("app")

    scoped = module._workout_with_auth_scope(
        {"goal": "hypertrophy", "exercises": [{"exercise": "Chest Press"}]}
    )

    assert "rpe_range" not in scoped


def test_dashboard_uses_rpe_range_in_compact_intensity_metadata():
    app_js = (ROOT / "static" / "js" / "app.js").read_text()
    template = (ROOT / "templates" / "index.html").read_text()
    marker = "// Intensity metadata / time chip."
    body = app_js.split(marker, 1)[1].split("// FIT-88:", 1)[0]

    assert "nw.rpe_range.label" in body
    assert "Target intensity" in body
    assert "nw.goal && nw.goal.rpe_target" not in body
    assert "$('reco-rpe').hidden = true" in body
    assert 'class="reco-intensity-meta" id="reco-intensity"' in template
