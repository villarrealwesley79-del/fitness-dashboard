from pathlib import Path

import app as fitness_app


def test_body_measurement_api_requires_weight_and_accepts_valid_measurement(monkeypatch, tmp_path):
    stored = []
    monkeypatch.setattr(fitness_app, "BODY_DATA", stored)
    monkeypatch.setattr(fitness_app, "BODY_FILE", tmp_path / "body.json")
    monkeypatch.setitem(fitness_app.app.config, "TESTING", True)
    monkeypatch.setitem(fitness_app.app.config, "LOGIN_DISABLED", True)
    client = fitness_app.app.test_client()

    body_fat_only = client.post(
        "/api/add-body-measurement",
        json={"weight_lbs": None, "body_fat_pct": 18.5},
    )
    assert body_fat_only.status_code == 400
    assert stored == []

    valid = client.post(
        "/api/add-body-measurement",
        json={"weight_lbs": 185.5, "body_fat_pct": 18.5},
    )
    assert valid.status_code == 200
    assert valid.get_json()["body_measurement"]["weight_lbs"] == 185.5
    assert valid.get_json()["body_measurement"]["body_fat_pct"] == 18.5


def test_body_form_marks_weight_required_and_has_specific_inline_copy():
    html = Path("templates/index.html").read_text()

    assert 'id="body-log-weight"' in html
    assert 'id="body-log-weight" step="0.1" placeholder="185.5" required' in html
    assert 'id="body-log-error"' in html
    assert "Enter weight to save a body measurement." in html


def test_body_fat_only_entry_locks_save_without_clearing_entered_value():
    source = Path("static/js/app.js").read_text()

    assert "function syncBodyLogValidation()" in source
    assert "saveButton.disabled = bodyFatPresent && !weightPresent;" in source
    assert "bodyFatInput.addEventListener('input', syncBodyLogValidation);" in source
    assert "if (!syncBodyLogValidation()) return;" in source
    assert "bodyFatInput.value = ''" not in source
