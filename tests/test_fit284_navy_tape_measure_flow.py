from pathlib import Path

import app as fitness_app


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(fitness_app, "BODY_DATA", [])
    monkeypatch.setattr(fitness_app, "BODY_FILE", tmp_path / "body.json")
    monkeypatch.setitem(fitness_app.app.config, "TESTING", True)
    monkeypatch.setitem(fitness_app.app.config, "LOGIN_DISABLED", True)
    return fitness_app.app.test_client()


def test_navy_calc_rejects_unknown_sex_and_impossible_geometry(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    valid_measurements = {"height_in": 70, "neck_in": 20, "waist_in": 34}

    invalid_sex = client.post("/api/body/navy-calc", json={**valid_measurements, "sex": "other"})
    assert invalid_sex.status_code == 400
    assert invalid_sex.get_json()["error"]["code"] == "invalid_field"
    assert "sex" in invalid_sex.get_json()["error"]["message"]

    invalid_geometry = client.post(
        "/api/body/navy-calc",
        json={**valid_measurements, "sex": "male", "waist_in": 20},
    )
    assert invalid_geometry.status_code == 400
    assert "waist_in must be greater than neck_in" == invalid_geometry.get_json()["error"]["message"]


def test_navy_calc_preserves_omitted_sex_default_to_male(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/body/navy-calc",
        json={"height_in": 70, "neck_in": 15, "waist_in": 34},
    )

    assert response.status_code == 200
    assert response.get_json()["body_fat_pct"] == 17.51

    blank_sex = client.post(
        "/api/body/navy-calc",
        json={"sex": "", "height_in": 70, "neck_in": 15, "waist_in": 34},
    )
    assert blank_sex.status_code == 200
    assert blank_sex.get_json()["body_fat_pct"] == 17.51


def test_body_measurement_persists_normalized_complete_female_tape_context(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    saved = client.post(
        "/api/add-body-measurement",
        json={
            "weight_lbs": 145,
            "sex": "female",
            "height_in": "65",
            "neck_in": "13.5",
            "waist_in": "29",
            "hip_in": "39",
        },
    )

    assert saved.status_code == 200
    entry = saved.get_json()["body_measurement"]
    assert entry["sex"] == "female"
    assert entry["height_in"] == 65.0
    assert entry["neck_in"] == 13.5
    assert entry["waist_in"] == 29.0
    assert entry["hip_in"] == 39.0
    assert "hips_in" not in entry
    assert entry["body_fat_pct"] is None


def test_body_measurement_rejects_partial_tape_context(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/add-body-measurement",
        json={"weight_lbs": 185, "sex": "male", "height_in": 70, "neck_in": 15},
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "missing_field"
    assert "waist_in" in response.get_json()["error"]["message"]


def test_body_measurement_preserves_validated_legacy_tape_fields(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    response = client.post(
        "/api/add-body-measurement",
        json={"weight_lbs": 185, "neck_in": "15", "waist_in": "34", "hips_in": "38"},
    )

    assert response.status_code == 200
    entry = response.get_json()["body_measurement"]
    assert entry["neck_in"] == 15.0
    assert entry["waist_in"] == 34.0
    assert entry["hips_in"] == 38.0
    assert "sex" not in entry


def test_body_tape_endpoints_reject_non_finite_measurements(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    payload = {"sex": "male", "height_in": "NaN", "neck_in": 15, "waist_in": 34}

    calculated = client.post("/api/body/navy-calc", json=payload)
    saved = client.post("/api/add-body-measurement", json={"weight_lbs": 185, **payload})

    assert calculated.status_code == 400
    assert calculated.get_json()["error"]["code"] == "invalid_field"
    assert "finite number" in calculated.get_json()["error"]["message"]
    assert saved.status_code == 400
    assert saved.get_json()["error"]["code"] == "invalid_field"


def test_body_form_exposes_inline_navy_estimate_controls_and_accessible_result():
    html = Path("templates/index.html").read_text()
    source = Path("static/js/app.js").read_text()

    for control_id in (
        "body-navy-toggle",
        "body-navy-sex",
        "body-navy-height",
        "body-navy-neck",
        "body-navy-waist",
        "body-navy-hip",
        "btn-calc-navy",
        "body-navy-result",
        "body-navy-error",
    ):
        assert f'id="{control_id}"' in html
    assert 'aria-live="polite"' in html
    assert "Navy estimate" in html
    assert "function calculateNavyEstimate()" in source
    assert "'/api/body/navy-calc'" in source
    assert "bodyFatInput.value = String(result.body_fat_pct);" in source


def test_body_navy_client_saves_tape_context_and_surfaces_inline_errors():
    source = Path("static/js/app.js").read_text()

    assert "function bodyNavyContextPayload()" in source
    assert "...navyContext," in source
    assert "function syncBodyNavySex()" in source
    assert "hipField.hidden = !isFemale;" in source
    assert "hipInput.required = isFemale;" in source
    assert "function setBodyNavyError(message)" in source
    assert "setBodyNavyError(apiErrorMessage(e, 'Could not calculate Navy estimate'));" in source
    assert "const navyContext = bodyNavyContextPayload();" in source
    assert "if (Object.keys(navyContext).length) setBodyNavyError(message);" in source
    assert "function invalidateBodyNavyEstimate()" in source
    assert "let bodyNavyDerivedValue = null;" in source
    assert "if (bodyNavyDerivedValue === null) return;" in source
    assert "if (bodyFatInput.value === bodyNavyDerivedValue) bodyFatInput.value = String();" in source
    assert "function detachManualBodyFatFromNavyEstimate()" in source
    assert "function detachManualBodyFatFromNavyEstimate() {\n        bodyNavyInputVersion += 1;" in source
    assert "input.addEventListener('input', noteBodyNavyInputChange);" in source
    assert "let bodyNavyInputVersion = 0;" in source
    assert "const requestVersion = bodyNavyInputVersion;" in source
    assert source.count("if (requestVersion !== bodyNavyInputVersion) return;") == 2
    assert "function noteBodyNavyInputChange() {\n        bodyNavyInputVersion += 1;\n        setBodyNavyCalculationPending(false);\n        setBodyNavyError('');" in source
    assert "let bodyNavyCalculationPending = false;" in source
    assert "function setBodyNavyCalculationPending(pending)" in source
    assert "if (bodyNavyCalculationPending) saveButton.disabled = true;" in source
    assert "if (bodyNavyCalculationPending) return;" in source
