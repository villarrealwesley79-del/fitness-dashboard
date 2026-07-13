import importlib


def _fitness_app(monkeypatch, workouts):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "WORKOUTS", workouts)
    return module.app


def test_markdown_export_formats_complete_strength_rows(monkeypatch):
    app = _fitness_app(
        monkeypatch,
        [
            {
                "date": "2026-07-12",
                "exercises": [
                    {
                        "machine": "Chest Press",
                        "sets": [{"set_number": 1, "reps": 8, "weight_lbs": 100, "notes": "steady"}],
                    }
                ],
            }
        ],
    )

    response = app.test_client().get("/api/export-md")

    assert response.status_code == 200
    assert "| 2026-07-12 | Chest Press | 1 | 8 | 100 | 800 | steady |" in response.get_data(as_text=True)


def test_markdown_export_labels_partial_and_watch_only_rows(monkeypatch):
    app = _fitness_app(
        monkeypatch,
        [
            {
                "date": "2026-07-12",
                "source": "Apple Health",
                "exercises": [
                    {"sets": [{"reps": 10}, {"weight_lbs": 20}]},
                    {"machine": "Apple Watch Import", "sets": None},
                    {"machine": "Legacy Import"},
                ],
            },
            {
                "date": "2026-07-13",
                "session_type": "Walking",
                "source": "watch",
                "exercises": None,
            },
            {"date": "2026-07-14", "session_type": "strength", "exercises": []},
            {
                "date": "2026-07-15",
                "session_type": "strength",
                "source": "lifted",
                "exercises": [{"machine": "Chest Press"}],
            },
            {"date": "2026-07-16", "source": "lifted", "exercises": []},
        ],
    )

    response = app.test_client().get("/api/export-md")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "| 2026-07-12 | N/A | 1 | 10 | N/A | N/A |  |" in body
    assert "| 2026-07-12 | N/A | 2 | N/A | 20 | N/A |  |" in body
    assert "| 2026-07-12 | Apple Watch Import | N/A | N/A | N/A | N/A | Non-strength/watch-only row |" in body
    assert "| 2026-07-12 | Legacy Import | N/A | N/A | N/A | N/A | Non-strength/watch-only row |" in body
    assert "| 2026-07-13 | Walking | N/A | N/A | N/A | N/A | Non-strength/watch-only row |" in body
    assert "| 2026-07-14 | strength | N/A | N/A | N/A | N/A | No exercise data |" in body
    assert "| 2026-07-15 | Chest Press | N/A | N/A | N/A | N/A | No set data |" in body
    assert "| 2026-07-16 | Strength - Logged | N/A | N/A | N/A | N/A | No exercise data |" in body
    assert "*Total Sessions: 5*" in body


def test_markdown_export_labels_malformed_nested_rows(monkeypatch):
    app = _fitness_app(
        monkeypatch,
        [
            None,
            7,
            {
                "date": "2026-07-15",
                "exercises": [None, {"machine": "Partial", "sets": [None]}],
            },
            {"date": "2026-07-16", "exercises": 1},
            {"date": "2026-07-17", "exercises": [{"machine": "Partial", "sets": 1}]},
            {
                "date": "2026-07-18",
                "exercises": [
                    {
                        "machine": {"invalid": "label"},
                        "sets": [{"reps": 2, "weight_lbs": 5, "notes": "left\\|right\nnext"}],
                    },
                    {
                        "machine": "Nonfinite",
                        "sets": [
                            {
                                "set_number": {"invalid": 1},
                                "reps": float("nan"),
                                "weight_lbs": float("inf"),
                                "notes": ["invalid"],
                            },
                            {"reps": 1e308, "weight_lbs": 1e308},
                            {"reps": 1, "weight_lbs": 1e308},
                            {"reps": 1, "weight_lbs": 1e308},
                        ],
                    },
                ],
            },
        ],
    )

    response = app.test_client().get("/api/export-md")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    invalid_workout_row = "| N/A | N/A | N/A | N/A | N/A | N/A | Invalid workout data |"
    assert body.count(invalid_workout_row) == 2
    assert "| 2026-07-15 | N/A | N/A | N/A | N/A | N/A | Invalid exercise data |" in body
    assert "| 2026-07-15 | Partial | 1 | N/A | N/A | N/A | Invalid set data |" in body
    assert "| 2026-07-16 | N/A | N/A | N/A | N/A | N/A | Invalid exercise collection |" in body
    assert "| 2026-07-17 | Partial | N/A | N/A | N/A | N/A | Invalid set collection |" in body
    escaped_note = "left" + "\\" * 3 + "|right next"
    assert f"| 2026-07-18 | N/A | 1 | 2 | 5 | 10 | {escaped_note} |" in body
    assert "| 2026-07-18 | Nonfinite | N/A | N/A | N/A | N/A | N/A |" in body
    assert "| 2026-07-18 | Nonfinite | 2 | 1e+308 | 1e+308 | N/A |  |" in body
    assert "| 2026-07-18 | Nonfinite | 3 | 1 | 1e+308 | 1e+308 |  |" in body
    assert "| 2026-07-18 | Nonfinite | 4 | 1 | 1e+308 | 1e+308 |  |" in body
    assert "- **Total Volume:** N/A" in body


def test_markdown_export_handles_empty_history(monkeypatch):
    app = _fitness_app(monkeypatch, [])

    response = app.test_client().get("/api/export-md")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "*Total Sessions: 0*" in body
    assert "## Workout Log" in body
