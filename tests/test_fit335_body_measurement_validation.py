from pathlib import Path

import app as fitness_app
from js_runtime import run_app_js


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
    result = run_app_js(
        ["syncBodyLogValidation"],
        """
sandbox.elements['body-log-weight'] = {
  value: '', attrs: {},
  setAttribute(key, value) { this.attrs[key] = value; },
};
sandbox.elements['body-log-bf'] = { value: '18.5' };
sandbox.elements['btn-log-body'] = { disabled: false };
sandbox.elements['body-log-error'] = { hidden: true };
const valid = e.syncBodyLogValidation();
process.stdout.write(JSON.stringify({
  valid,
  disabled: sandbox.elements['btn-log-body'].disabled,
  ariaInvalid: sandbox.elements['body-log-weight'].attrs['aria-invalid'],
  errorHidden: sandbox.elements['body-log-error'].hidden,
  bodyFatValue: sandbox.elements['body-log-bf'].value,
}));
""",
    )

    assert result == {
        "valid": False,
        "disabled": True,
        "ariaInvalid": "true",
        "errorHidden": False,
        "bodyFatValue": "18.5",
    }


def test_body_inputs_wire_validation_and_only_submit_when_weight_is_present():
    output = run_app_js(
        ["wireEvents"],
        """
sandbox.addEventListener = () => {};
function node(value = '') {
  return { value, disabled: false, hidden: true, textContent: '', attrs: {}, handlers: {}, classList: { add() {}, remove() {}, toggle() {} },
    setAttribute(key, value) { this.attrs[key] = value; }, addEventListener(name, fn) { this.handlers[name] = fn; } };
}
const weight = node('');
const bodyFat = node('18.5');
const save = node();
const error = node();
sandbox.elements['body-log-weight'] = weight;
sandbox.elements['body-log-bf'] = bodyFat;
sandbox.elements['btn-log-body'] = save;
sandbox.elements['body-log-error'] = error;
let apiCalls = 0;
sandbox.__fitSet.api(async () => { apiCalls += 1; return {}; });
sandbox.__fitSet.toast(() => {});
sandbox.__fitSet.renderBody(async () => {});
e.wireEvents();
weight.handlers.input();
await save.handlers.click();
const blocked = { apiCalls, disabled: save.disabled, invalid: weight.attrs['aria-invalid'], errorHidden: error.hidden };
weight.value = '185.5';
weight.handlers.input();
await save.handlers.click();
process.stdout.write(JSON.stringify({ blocked, apiCalls, disabledAfterValid: save.disabled, invalidAfterValid: weight.attrs['aria-invalid'] }));
""",
        mocks=["api", "toast", "renderBody"],
    )
    assert output == {
        "blocked": {"apiCalls": 0, "disabled": True, "invalid": "true", "errorHidden": False},
        "apiCalls": 1, "disabledAfterValid": False, "invalidAfterValid": "false",
    }
