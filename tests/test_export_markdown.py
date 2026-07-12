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
                "exercises": [
                    {"sets": [{"reps": 10}, {"weight_lbs": 20}]},
                    {"machine": "Apple Watch Import", "sets": None},
                    {"machine": "Legacy Import"},
                ],
            },
            {"date": "2026-07-13", "exercises": None},
        ],
    )

    response = app.test_client().get("/api/export-md")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "| 2026-07-12 | N/A | 1 | 10 | N/A | N/A |  |" in body
    assert "| 2026-07-12 | N/A | 2 | N/A | 20 | N/A |  |" in body
    assert "| 2026-07-12 | Apple Watch Import | N/A | N/A | N/A | N/A | Non-strength/watch-only row |" in body
    assert "| 2026-07-12 | Legacy Import | N/A | N/A | N/A | N/A | Non-strength/watch-only row |" in body
    assert "*Total Sessions: 2*" in body


def test_markdown_export_handles_empty_history(monkeypatch):
    app = _fitness_app(monkeypatch, [])

    response = app.test_client().get("/api/export-md")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "*Total Sessions: 0*" in body
    assert "## Workout Log" in body
