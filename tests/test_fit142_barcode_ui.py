"""FIT-142 barcode scan UI contracts."""

from __future__ import annotations

from pathlib import Path

from js_runtime import run_app_js

ROOT = Path(__file__).resolve().parents[1]
APP_CSS = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
INDEX = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")


def test_barcode_controls_are_in_meal_composer_without_nested_form():
    for element_id in (
        'id="meal-composer-scan"', 'id="meal-composer-barcode"',
        'id="meal-composer-barcode-video"', 'id="meal-composer-barcode-input"',
        'id="meal-composer-barcode-submit"',
    ):
        assert element_id in INDEX
    composer = INDEX.split('<form class="meal-composer"', 1)[1].split("</form>", 1)[0]
    assert '<form ' not in composer


def test_barcode_normalizer_matches_backend_separator_contract():
    output = run_app_js(
        ["normalizeMealBarcode"],
        """
process.stdout.write(JSON.stringify([
  e.normalizeMealBarcode('0 123-4567'),
  e.normalizeMealBarcode('0123.4567'),
  e.normalizeMealBarcode('123456789012'),
  e.normalizeMealBarcode('12345abc'),
  e.normalizeMealBarcode('123456789'),
]));
""",
    )
    assert output == ["01234567", "01234567", "123456789012", "", ""]


def test_barcode_lookup_posts_json_with_local_time_fields():
    output = run_app_js(
        ["postMealBarcodeLookup"],
        """
let captured;
sandbox.fetch = async (path, options) => {
  captured = { path, options };
  return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'content-type': 'application/json' } });
};
await e.postMealBarcodeLookup({ barcode: '01234567', clientId: 'barcode-client', localTime: {
  local_timestamp: '2026-07-16T12:00:00', local_date: '2026-07-16', local_iso: '2026-07-16T12:00:00-05:00',
}, allowPending: true });
process.stdout.write(JSON.stringify({ path: captured.path, method: captured.options.method, credentials: captured.options.credentials, headers: captured.options.headers, body: JSON.parse(captured.options.body) }));
""",
    )
    assert output == {
        "path": "/api/meal-intake/barcode",
        "method": "POST",
        "credentials": "same-origin",
        "headers": {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        },
        "body": {
            "client_id": "barcode-client", "barcode": "01234567", "allow_pending": True,
            "local_timestamp": "2026-07-16T12:00:00", "local_date": "2026-07-16",
            "local_iso": "2026-07-16T12:00:00-05:00",
        },
    }


def test_camera_permission_cancellation_stops_stream_before_assignment():
    output = run_app_js(
        ["startMealBarcodeScanner", "mealComposerState"],
        """
const tracks = [{ stopped: false, stop() { this.stopped = true; } }];
const stream = { getTracks: () => tracks };
sandbox.elements['meal-composer-barcode'] = { hidden: true };
sandbox.elements['meal-composer-barcode-video'] = { hidden: true, srcObject: null, play: async () => {}, pause: () => {} };
sandbox.BarcodeDetector = class {};
sandbox.navigator.mediaDevices = { getUserMedia: async () => stream };
await e.startMealBarcodeScanner();
process.stdout.write(JSON.stringify({ stopped: tracks[0].stopped, stream: e.mealComposerState.barcodeStream }));
""",
    )
    assert output == {"stopped": True, "stream": None}


def _composer_elements_script():
    return """
const ids = ['meal-composer-text', 'meal-composer-barcode-status', 'meal-composer-error', 'meal-composer-barcode', 'meal-composer-submit', 'meal-composer-scan'];
ids.forEach((id) => { sandbox.elements[id] = { value: '', textContent: '', hidden: false, disabled: false, classList: { add() {}, remove() {} }, dataset: {} }; });
"""


def test_barcode_success_preserves_unsent_composer_draft_and_clears_retry_state():
    output = run_app_js(
        ["submitMealBarcode", "mealComposerState"],
        _composer_elements_script().replace("value: ''", "value: 'leftover bowl'", 1)
        + """
const calls = [];
sandbox.__fitSet.postMealBarcodeLookup(async (payload) => { calls.push(payload); return { res: { status: 200, ok: true }, payload: { status: 'pending_review' } }; });
sandbox.__fitSet.handleMealIntakeResponse((payload, ctx) => calls.push({ payload, ctx }));
sandbox.__fitSet.closeMealBarcodePanel(() => {});
sandbox.__fitSet.toast(() => {});
await e.submitMealBarcode('0 123-4567');
process.stdout.write(JSON.stringify({ ctx: calls[1].ctx, clientId: e.mealComposerState.barcodeDraftClientId, draftValue: e.mealComposerState.barcodeDraftValue }));
""",
        mocks=["postMealBarcodeLookup", "handleMealIntakeResponse", "closeMealBarcodePanel", "toast"],
    )
    assert output["ctx"]["barcode"] == "01234567"
    assert output["ctx"]["preserveComposerDraft"] is True
    assert output["clientId"] is None and output["draftValue"] == ""


def test_transient_barcode_failure_reuses_idempotency_key_until_success():
    output = run_app_js(
        ["submitMealBarcode", "mealComposerState"],
        _composer_elements_script()
        + """
const calls = [];
let attempt = 0;
sandbox.__fitSet.postMealBarcodeLookup(async ({ clientId }) => { calls.push(clientId); attempt += 1; return attempt === 1 ? { res: { status: 503, ok: false }, payload: { error: { message: 'offline' } } } : { res: { status: 200, ok: true }, payload: {} }; });
sandbox.__fitSet.handleMealIntakeResponse(() => {});
sandbox.__fitSet.closeMealBarcodePanel(() => {});
sandbox.__fitSet.toast(() => {});
await e.submitMealBarcode('01234567');
await e.submitMealBarcode('01234567');
process.stdout.write(JSON.stringify({ calls, state: { clientId: e.mealComposerState.barcodeDraftClientId, value: e.mealComposerState.barcodeDraftValue } }));
""",
        mocks=["postMealBarcodeLookup", "handleMealIntakeResponse", "closeMealBarcodePanel", "toast"],
    )
    assert output["calls"][0] == output["calls"][1]
    assert output["state"] == {"clientId": None, "value": ""}


def test_barcode_not_found_falls_back_to_pending_review_without_disabling_composer():
    output = run_app_js(
        ["submitMealBarcode", "mealComposerState"],
        _composer_elements_script().replace("value: ''", "value: 'leftover bowl'", 1)
        + """
const lookups = [];
let handled = null;
sandbox.__fitSet.postMealBarcodeLookup(async (payload) => {
  lookups.push(payload);
  return lookups.length === 1
    ? { res: { status: 404, ok: false }, payload: { error: { code: 'barcode_not_found' } } }
    : { res: { status: 200, ok: true }, payload: { status: 'pending_review' } };
});
sandbox.__fitSet.handleMealIntakeResponse((payload, ctx) => { handled = { payload, ctx }; });
sandbox.__fitSet.closeMealBarcodePanel(() => {});
sandbox.__fitSet.toast(() => {});
await e.submitMealBarcode('0 123-4567');
process.stdout.write(JSON.stringify({
  lookups: lookups.map(({ barcode, clientId, allowPending }) => ({ barcode, clientId, allowPending })),
  handled: !!handled,
  scanDisabled: sandbox.elements['meal-composer-scan'].disabled,
  submitDisabled: sandbox.elements['meal-composer-submit'].disabled,
  textValue: sandbox.elements['meal-composer-text'].value,
  draft: { clientId: e.mealComposerState.barcodeDraftClientId, value: e.mealComposerState.barcodeDraftValue },
}));
""",
        mocks=["postMealBarcodeLookup", "handleMealIntakeResponse", "closeMealBarcodePanel", "toast"],
    )
    assert output["lookups"][0]["allowPending"] is False
    assert output["lookups"][1]["allowPending"] is True
    assert output["lookups"][0]["barcode"] == output["lookups"][1]["barcode"] == "01234567"
    assert output["lookups"][0]["clientId"] == output["lookups"][1]["clientId"]
    assert output["handled"] is True
    assert output["scanDisabled"] is False and output["submitDisabled"] is False
    assert output["textValue"] == "leftover bowl"
    assert output["draft"] == {"clientId": None, "value": ""}


def test_barcode_feature_disabled_disables_barcode_only_and_preserves_text_composer():
    output = run_app_js(
        ["submitMealBarcode", "mealComposerState"],
        _composer_elements_script().replace("value: ''", "value: 'meal draft'", 1)
        + """
sandbox.__fitSet.postMealBarcodeLookup(async () => ({
  res: { status: 501, ok: false },
  payload: { error: { message: 'feature disabled' } },
}));
sandbox.__fitSet.closeMealBarcodePanel(() => {});
sandbox.__fitSet.toast(() => {});
await e.submitMealBarcode('01234567');
process.stdout.write(JSON.stringify({
  barcodeUnavailable: e.mealComposerState.barcodeUnavailable,
  scanDisabled: sandbox.elements['meal-composer-scan'].disabled,
  submitDisabled: sandbox.elements['meal-composer-submit'].disabled,
  textValue: sandbox.elements['meal-composer-text'].value,
  draft: { clientId: e.mealComposerState.barcodeDraftClientId, value: e.mealComposerState.barcodeDraftValue },
}));
""",
        mocks=["postMealBarcodeLookup", "closeMealBarcodePanel", "toast"],
    )
    assert output["barcodeUnavailable"] is True
    assert output["scanDisabled"] is True
    assert output["submitDisabled"] is False
    assert output["textValue"] == "meal draft"
    assert output["draft"] == {"clientId": None, "value": ""}


def test_barcode_detection_cancellation_after_detect_resolves_does_not_submit():
    output = run_app_js(
        ["scanMealBarcodeFrame", "closeMealBarcodePanel", "mealComposerState"],
        _composer_elements_script()
        + """
sandbox.elements['meal-composer-barcode'] = { hidden: false };
sandbox.elements['meal-composer-barcode-video'] = { hidden: false, srcObject: null, pause() {} };
sandbox.elements['meal-composer-barcode-input'] = { value: '' };
e.mealComposerState.barcodeVideo = sandbox.elements['meal-composer-barcode-video'];
e.mealComposerState.barcodeInput = sandbox.elements['meal-composer-barcode-input'];
const track = { stopped: false, stop() { this.stopped = true; } };
e.mealComposerState.barcodeStream = { getTracks: () => [track] };
let resolveDetect;
e.mealComposerState.barcodeDetector = { detect: () => new Promise((resolve) => { resolveDetect = resolve; }) };
let submits = 0;
sandbox.__fitSet.submitMealBarcode(() => { submits += 1; });
const frame = e.scanMealBarcodeFrame(500);
await Promise.resolve();
e.closeMealBarcodePanel();
resolveDetect([{ rawValue: '01234567' }]);
await frame;
process.stdout.write(JSON.stringify({ submits, stopped: track.stopped, stream: e.mealComposerState.barcodeStream, panelHidden: sandbox.elements['meal-composer-barcode'].hidden }));
""",
        mocks=["submitMealBarcode"],
    )
    assert output == {"submits": 0, "stopped": True, "stream": None, "panelHidden": True}


def test_repeated_barcode_panel_open_clicks_and_reopen_have_one_scanner_per_open():
    output = run_app_js(
        ["openMealBarcodePanel", "closeMealBarcodePanel", "mealComposerState"],
        _composer_elements_script()
        + """
const panel = { hidden: true };
const input = { value: '', focusCalls: 0, focus() { this.focusCalls += 1; } };
sandbox.elements['meal-composer-barcode'] = panel;
sandbox.elements['meal-composer-barcode-input'] = input;
let starts = 0;
sandbox.__fitSet.startMealBarcodeScanner(async () => { starts += 1; });
e.openMealBarcodePanel();
e.openMealBarcodePanel();
e.closeMealBarcodePanel();
e.openMealBarcodePanel();
process.stdout.write(JSON.stringify({ starts, hidden: panel.hidden, focusCalls: input.focusCalls, scanToken: e.mealComposerState.barcodeScanToken }));
""",
        mocks=["startMealBarcodeScanner"],
    )
    assert output["starts"] == 2
    assert output["hidden"] is False
    assert output["focusCalls"] == 3
    assert output["scanToken"] >= 1


def test_manual_barcode_submission_stops_active_camera_before_lookup_request():
    output = run_app_js(
        ["submitMealBarcode", "mealComposerState"],
        _composer_elements_script()
        + """
sandbox.elements['meal-composer-barcode-video'] = { hidden: false, srcObject: 'camera', pause() {} };
const order = [];
const track = { stopped: false, stop() { this.stopped = true; order.push('stop'); } };
e.mealComposerState.barcodeStream = { getTracks: () => [track] };
sandbox.__fitSet.postMealBarcodeLookup(async () => {
  order.push('request');
  return { res: { status: 200, ok: true }, payload: {} };
});
sandbox.__fitSet.handleMealIntakeResponse(() => {});
sandbox.__fitSet.toast(() => {});
await e.submitMealBarcode('01234567');
process.stdout.write(JSON.stringify({ order, stopped: track.stopped, stream: e.mealComposerState.barcodeStream }));
""",
        mocks=["postMealBarcodeLookup", "handleMealIntakeResponse", "toast"],
    )
    assert output["order"][:2] == ["stop", "request"]
    assert output["stopped"] is True
    assert output["stream"] is None


def test_offline_barcode_path_does_not_call_lookup_endpoint():
    output = run_app_js(
        ["submitMealBarcode"],
        """
sandbox.elements['meal-composer-barcode-status'] = { textContent: '' };
sandbox.navigator.onLine = false;
let calls = 0;
sandbox.__fitSet.postMealBarcodeLookup(async () => { calls += 1; });
await e.submitMealBarcode('01234567');
process.stdout.write(JSON.stringify({ calls, status: sandbox.elements['meal-composer-barcode-status'].textContent }));
""",
        mocks=["postMealBarcodeLookup"],
    )
    assert output["calls"] == 0
    assert "needs the server" in output["status"]


def test_refresh_submit_state_keeps_text_submit_available_when_barcode_unavailable():
    output = run_app_js(
        ["refreshMealSubmitState", "mealComposerState"],
        """
sandbox.elements['meal-composer-text'] = { value: 'meal' };
sandbox.elements['meal-composer-submit'] = { disabled: false, textContent: '' };
sandbox.elements['meal-composer-scan'] = { disabled: false };
e.mealComposerState.barcodeUnavailable = true;
e.refreshMealSubmitState();
process.stdout.write(JSON.stringify({ submit: sandbox.elements['meal-composer-submit'].disabled, scan: sandbox.elements['meal-composer-scan'].disabled }));
""",
    )
    assert output == {"submit": False, "scan": True}


def test_barcode_styles_ship_with_composer():
    expected = [
        ".meal-composer-scan", ".meal-composer-barcode", ".meal-composer-barcode-video",
        ".meal-composer-barcode-manual", ".meal-composer-barcode-status",
    ]
    missing = [selector for selector in expected if selector not in APP_CSS]
    assert not missing, f"FIT-142 barcode styles missing from style.css: {missing}"
