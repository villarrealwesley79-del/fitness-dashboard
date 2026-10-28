from pathlib import Path

import app as fitness_app
from js_runtime import run_app_js


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
    assert entry["hips_in"] == 39.0
    assert "hip_in" not in entry
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


PRELUDE = """
function node(value = '') {
  return { value, disabled: false, hidden: false, required: false, textContent: '', attrs: {}, handlers: {},
    classList: { add() {}, remove() {}, toggle() {} },
    setAttribute(key, val) { this.attrs[key] = val; },
    addEventListener(name, fn) { this.handlers[name] = fn; } };
}
const ids = ['body-log-weight', 'body-log-bf', 'btn-log-body', 'body-log-error', 'body-navy-sex',
  'body-navy-height', 'body-navy-neck', 'body-navy-waist', 'body-navy-hip', 'body-navy-hip-field',
  'btn-calc-navy', 'body-navy-result', 'body-navy-error'];
const n = {};
ids.forEach((id) => { n[id] = node(); sandbox.elements[id] = n[id]; });
n['body-navy-sex'].value = 'male';
n['body-log-weight'].value = '185';
const tapeInputs = ['body-navy-sex', 'body-navy-height', 'body-navy-neck', 'body-navy-waist', 'body-navy-hip'].map((id) => n[id]);
sandbox.document.querySelectorAll = (sel) => {
  if (sel.startsWith('#body-navy-sex')) return tapeInputs;
  if (sel.startsWith('#body-navy-height')) return tapeInputs.slice(1);
  return [];
};
function fillMale() {
  n['body-navy-height'].value = '70'; n['body-navy-neck'].value = '15'; n['body-navy-waist'].value = '34';
}
sandbox.__fitSet.toast((msg, variant) => { toasts.push([msg, variant]); });
const toasts = [];
const apiCalls = [];
"""
MOCKS = ["api", "toast", "renderBody"]


def _run(exports, body):
    return run_app_js(exports, PRELUDE + body, mocks=MOCKS)


def test_body_form_exposes_inline_navy_estimate_controls_and_accessible_result():
    html = Path("templates/index.html").read_text()

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


def test_navy_estimate_posts_tape_context_fills_body_fat_and_unlocks_save():
    out = _run(["calculateNavyEstimate"], """
fillMale();
sandbox.__fitSet.api(async (path, opts) => { apiCalls.push([path, JSON.parse(opts.body)]); return { body_fat_pct: 17.51 }; });
await e.calculateNavyEstimate();
process.stdout.write(JSON.stringify({
  apiCalls, bf: n['body-log-bf'].value, result: n['body-navy-result'].textContent,
  resultHidden: n['body-navy-result'].hidden, calcDisabled: n['btn-calc-navy'].disabled,
  saveDisabled: n['btn-log-body'].disabled, errorHidden: n['body-navy-error'].hidden,
}));
""")

    assert out["apiCalls"] == [["/api/body/navy-calc", {"sex": "male", "height_in": 70, "neck_in": 15, "waist_in": 34, "hip_in": None}]]
    assert out["bf"] == "17.51"
    assert "17.51" in out["result"] and "review before saving" in out["result"]
    assert out["resultHidden"] is False
    assert out["calcDisabled"] is False and out["saveDisabled"] is False
    assert out["errorHidden"] is True


def test_navy_estimate_locks_save_and_calculate_while_request_is_pending():
    out = _run(["calculateNavyEstimate", "logBody"], """
fillMale();
let release;
sandbox.__fitSet.api((path) => { apiCalls.push(path); return new Promise((resolve) => { release = resolve; }); });
const pending = e.calculateNavyEstimate();
const during = { calc: n['btn-calc-navy'].disabled, save: n['btn-log-body'].disabled };
await e.logBody();
const callsWhilePending = apiCalls.length;
release({ body_fat_pct: 18 });
await pending;
process.stdout.write(JSON.stringify({ during, callsWhilePending, after: { calc: n['btn-calc-navy'].disabled, save: n['btn-log-body'].disabled } }));
""")

    assert out == {
        "during": {"calc": True, "save": True},
        "callsWhilePending": 1,
        "after": {"calc": False, "save": False},
    }


def test_navy_estimate_error_is_surfaced_inline_and_leaves_body_fat_alone():
    out = _run(["calculateNavyEstimate"], """
fillMale();
n['body-log-bf'].value = '20';
sandbox.__fitSet.api(async () => { throw new Error('400 {"error":{"message":"waist_in must be greater than neck_in"}}'); });
await e.calculateNavyEstimate();
process.stdout.write(JSON.stringify({
  message: n['body-navy-error'].textContent, hidden: n['body-navy-error'].hidden,
  invalid: tapeInputs.map((el) => el.attrs['aria-invalid']), bf: n['body-log-bf'].value,
  calcDisabled: n['btn-calc-navy'].disabled,
}));
""")

    assert out["message"] == "waist_in must be greater than neck_in"
    assert out["hidden"] is False
    assert out["invalid"] == ["true"] * 5
    assert out["bf"] == "20"
    assert out["calcDisabled"] is False


def test_navy_estimate_ignores_response_after_inputs_change_mid_request():
    out = _run(["calculateNavyEstimate", "noteBodyNavyInputChange"], """
fillMale();
let release;
sandbox.__fitSet.api(() => new Promise((resolve) => { release = resolve; }));
const pending = e.calculateNavyEstimate();
n['body-navy-waist'].value = '36';
e.noteBodyNavyInputChange();
release({ body_fat_pct: 17.51 });
await pending;
process.stdout.write(JSON.stringify({ bf: n['body-log-bf'].value, result: n['body-navy-result'].textContent, calcDisabled: n['btn-calc-navy'].disabled }));
""")

    assert out == {"bf": "", "result": "", "calcDisabled": False}


def test_changing_tape_inputs_clears_derived_estimate_but_not_manual_body_fat():
    out = _run(["calculateNavyEstimate", "noteBodyNavyInputChange", "detachManualBodyFatFromNavyEstimate"], """
fillMale();
sandbox.__fitSet.api(async () => ({ body_fat_pct: 17.51 }));
await e.calculateNavyEstimate();
e.noteBodyNavyInputChange();
const derivedCleared = { bf: n['body-log-bf'].value, result: n['body-navy-result'].textContent };
await e.calculateNavyEstimate();
n['body-log-bf'].value = '20';
e.detachManualBodyFatFromNavyEstimate();
e.noteBodyNavyInputChange();
process.stdout.write(JSON.stringify({ derivedCleared, manualBf: n['body-log-bf'].value, manualResult: n['body-navy-result'].textContent }));
""")

    assert out == {"derivedCleared": {"bf": "", "result": ""}, "manualBf": "20", "manualResult": ""}


def test_navy_sex_toggle_shows_and_requires_hip_only_for_female():
    out = _run(["syncBodyNavySex"], """
n['body-navy-hip'].value = '40';
n['body-navy-sex'].value = 'female';
e.syncBodyNavySex();
const female = { hidden: n['body-navy-hip-field'].hidden, required: n['body-navy-hip'].required, hip: n['body-navy-hip'].value };
n['body-navy-sex'].value = 'male';
e.syncBodyNavySex();
const male = { hidden: n['body-navy-hip-field'].hidden, required: n['body-navy-hip'].required, hip: n['body-navy-hip'].value };
process.stdout.write(JSON.stringify({ female, male }));
""")

    assert out == {
        "female": {"hidden": False, "required": True, "hip": "40"},
        "male": {"hidden": True, "required": False, "hip": ""},
    }


def test_log_body_saves_tape_context_with_measurement_and_omits_it_when_blank():
    out = _run(["logBody"], """
n['body-log-bf'].value = '17.5';
sandbox.__fitSet.api(async (path, opts) => { apiCalls.push([path, JSON.parse(opts.body)]); return {}; });
sandbox.__fitSet.renderBody(async () => {});
n['body-navy-sex'].value = 'female';
n['body-navy-height'].value = '65'; n['body-navy-neck'].value = '13.5';
n['body-navy-waist'].value = '29'; n['body-navy-hip'].value = '39';
await e.logBody();
['body-navy-height', 'body-navy-neck', 'body-navy-waist', 'body-navy-hip'].forEach((id) => { n[id].value = ''; });
await e.logBody();
process.stdout.write(JSON.stringify({ apiCalls, toasts }));
""")

    assert out["apiCalls"][0] == ["/api/add-body-measurement", {
        "weight_lbs": 185, "body_fat_pct": 17.5, "sex": "female",
        "height_in": 65, "neck_in": 13.5, "waist_in": 29, "hip_in": 39,
    }]
    assert out["apiCalls"][1] == ["/api/add-body-measurement", {"weight_lbs": 185, "body_fat_pct": 17.5}]
    assert out["toasts"] == [["Measurement saved", None]] * 2


def test_log_body_still_saves_when_navy_inputs_are_missing_from_the_page():
    out = _run(["logBody"], """
['body-navy-sex', 'body-navy-height', 'body-navy-neck', 'body-navy-waist', 'body-navy-hip'].forEach((id) => { delete sandbox.elements[id]; });
sandbox.__fitSet.api(async (path, opts) => { apiCalls.push(JSON.parse(opts.body)); return {}; });
sandbox.__fitSet.renderBody(async () => {});
await e.logBody();
process.stdout.write(JSON.stringify({ apiCalls }));
""")

    assert out == {"apiCalls": [{"weight_lbs": 185, "body_fat_pct": None}]}


def test_log_body_server_error_shows_inline_only_when_tape_context_was_sent():
    out = _run(["logBody"], """
sandbox.__fitSet.api(async () => { throw new Error('400 {"error":{"message":"hip_in is required"}}'); });
n['body-navy-sex'].value = 'female';
n['body-navy-height'].value = '65'; n['body-navy-neck'].value = '13'; n['body-navy-waist'].value = '30';
await e.logBody();
const withTape = { inline: n['body-navy-error'].textContent, toasts: toasts.splice(0) };
['body-navy-height', 'body-navy-neck', 'body-navy-waist'].forEach((id) => { n[id].value = ''; });
n['body-navy-error'].textContent = '';
await e.logBody();
process.stdout.write(JSON.stringify({ withTape, withoutTape: { inline: n['body-navy-error'].textContent, toasts } }));
""")

    assert out["withTape"] == {"inline": "hip_in is required", "toasts": [["hip_in is required", "err"]]}
    assert out["withoutTape"] == {"inline": "", "toasts": [["hip_in is required", "err"]]}


def test_wire_events_connects_navy_controls_to_estimate_and_invalidation():
    out = _run(["wireEvents"], """
sandbox.addEventListener = () => {};
fillMale();
sandbox.__fitSet.api(async () => ({ body_fat_pct: 17.51 }));
e.wireEvents();
await n['btn-calc-navy'].handlers.click();
const calculated = n['body-log-bf'].value;
n['body-navy-neck'].handlers.input();
const afterTapeEdit = n['body-log-bf'].value;
n['body-navy-sex'].value = 'female';
n['body-navy-sex'].handlers.change();
process.stdout.write(JSON.stringify({ calculated, afterTapeEdit, hipHidden: n['body-navy-hip-field'].hidden, hipRequired: n['body-navy-hip'].required }));
""")

    assert out == {"calculated": "17.51", "afterTapeEdit": "", "hipHidden": False, "hipRequired": True}
