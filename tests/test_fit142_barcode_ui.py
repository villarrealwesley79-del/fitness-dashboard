"""FIT-142 barcode scan UI wiring tests."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
APP_CSS = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
INDEX = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")


def _barcode_block() -> str:
    start = APP_JS.find("function normalizeMealBarcode(value)")
    end = APP_JS.find("\n    async function submitMealComposer", start)
    assert start != -1 and end != -1, "FIT-142 barcode block markers not found"
    return APP_JS[start:end]


def test_barcode_controls_are_in_meal_composer_without_nested_form():
    assert 'id="meal-composer-scan"' in INDEX
    assert 'id="meal-composer-barcode"' in INDEX
    assert 'id="meal-composer-barcode-video"' in INDEX
    assert 'id="meal-composer-barcode-input"' in INDEX
    assert 'id="meal-composer-barcode-submit"' in INDEX
    composer = INDEX.split('<form class="meal-composer"', 1)[1].split("</form>", 1)[0]
    assert '<form ' not in composer


def test_barcode_detector_sends_only_decoded_barcode_json():
    block = _barcode_block()
    assert "new window.BarcodeDetector" in block
    assert "navigator.mediaDevices.getUserMedia" in block
    assert "results[0] && results[0].rawValue" in block
    assert "submitMealBarcode(barcode)" in block
    assert "fetch('/api/meal-intake/barcode'" in block
    assert "'Content-Type': 'application/json'" in block
    assert "JSON.stringify({" in block
    assert "barcode," in block
    assert "FormData" not in block
    assert "images" not in block
    assert "srcObject" not in block.split("body: JSON.stringify", 1)[1]


def test_barcode_normalizer_matches_backend_separator_contract():
    block = _barcode_block()
    normalize_section = block.split("function normalizeMealBarcode", 1)[1].split("\n    }", 1)[0]
    assert "replace(/[\\s_.-]+/g, '')" in normalize_section
    assert "/^\\d+$/.test(digits)" in normalize_section
    assert "replace(/\\D+/g" not in normalize_section


def test_barcode_camera_start_cancels_if_panel_closes_mid_permission():
    block = _barcode_block()
    assert "barcodeScanToken" in APP_JS
    assert "function mealBarcodeScanCancelled(scanToken)" in block
    assert "stream.getTracks().forEach((track) => track.stop())" in block
    get_user_media_idx = block.find("navigator.mediaDevices.getUserMedia")
    cancel_idx = block.find("if (mealBarcodeScanCancelled(scanToken))", get_user_media_idx)
    assign_idx = block.find("mealComposerState.barcodeStream = stream", get_user_media_idx)
    assert get_user_media_idx != -1 and cancel_idx != -1 and assign_idx != -1
    assert cancel_idx < assign_idx
    play_idx = block.find("await barcodeVideo.play()", assign_idx)
    post_play_cancel_idx = block.find("if (mealBarcodeScanCancelled(scanToken))", play_idx)
    assert play_idx != -1 and post_play_cancel_idx != -1


def test_barcode_detection_rechecks_cancellation_before_submit():
    block = _barcode_block()
    frame_section = block.split("async function scanMealBarcodeFrame", 1)[1].split("\n    function openMealBarcodePanel", 1)[0]
    assert "const scanToken = mealComposerState.barcodeScanToken;" in frame_section
    detect_idx = frame_section.find("await detector.detect(barcodeVideo)")
    cancel_idx = frame_section.find("if (mealBarcodeScanCancelled(scanToken)) return;", detect_idx)
    submit_idx = frame_section.find("submitMealBarcode(barcode)", detect_idx)
    assert detect_idx != -1 and cancel_idx != -1 and submit_idx != -1
    assert detect_idx < cancel_idx < submit_idx


def test_repeated_scan_click_does_not_start_parallel_camera_streams():
    block = _barcode_block()
    start_section = block.split("async function startMealBarcodeScanner", 1)[1].split("\n    function openMealBarcodePanel", 1)[0]
    assert "mealComposerState.barcodeStream || mealComposerState.barcodeScanRaf" in start_section
    assert "stopMealBarcodeScanner()" in start_section
    open_section = block.split("function openMealBarcodePanel", 1)[1].split("\n    async function postMealBarcodeLookup", 1)[0]
    assert "if (!barcodePanel.hidden)" in open_section
    assert "return;" in open_section.split("if (!barcodePanel.hidden)", 1)[1].split("barcodePanel.hidden = false", 1)[0]


def test_barcode_success_preserves_unsent_composer_draft():
    block = _barcode_block()
    assert "const preserveComposerDraft = !!textValue || mealComposerState.imageFiles.length > 0;" in block
    assert "preserveComposerDraft," in block
    pending_section = APP_JS.split("if (status === 'pending_review')", 1)[1].split("setMealComposerError", 1)[0]
    assert "if (!ctx.fromQueue && !ctx.preserveComposerDraft)" in pending_section
    v2_section = APP_JS.split("function handleMealIntakeV2Response", 1)[1].split("\n    function applyMealV2Refresh", 1)[0]
    assert "if (!ctx.preserveComposerDraft)" in v2_section


def test_missing_barcode_endpoint_does_not_disable_text_photo_composer():
    # FIT-208: only a true 501 (feature disabled) flips into the "unavailable"
    # state that disables the barcode controls. A residual 404 must NOT.
    block = _barcode_block()
    assert "barcodeUnavailable" in APP_JS
    assert "function setMealBarcodeUnavailable(message)" in APP_JS
    unavailable_section = block.split("if (res.status === 501)", 1)[1].split("return;", 1)[0]
    assert "setMealBarcodeUnavailable" in unavailable_section
    assert "setMealBackendUnavailable" not in unavailable_section
    refresh_section = APP_JS.split("function refreshMealSubmitState", 1)[1].split("\n    function saveMealDraft", 1)[0]
    assert "scan.disabled = blocked || mealComposerState.barcodeUnavailable" in refresh_section
    assert "const enabled = (hasText || hasImage) && !blocked;" in refresh_section


def test_residual_404_keeps_barcode_controls_enabled():
    # FIT-208: a residual 404 (feature enabled, barcode just didn't resolve)
    # surfaces a retryable message and leaves scan/input/submit ENABLED. Only a
    # true 501 disables the barcode controls.
    block = _barcode_block()
    gate = "res.status === 404 && payload && payload.error && payload.error.code === 'barcode_not_found'"
    # The not-found gate appears twice: once to trigger the allow_pending retry,
    # and once on the recoverable branch so an unexpected 404 is not silently
    # treated as "barcode not found".
    assert block.count(gate) >= 2
    recoverable = block.split("find that barcode. Double-check the digits", 1)[1].split("return;", 1)[0]
    assert "setMealComposerError" in recoverable
    assert "setMealBarcodeStatus" in recoverable
    # Recoverable path must never disable the controls.
    assert "setMealBarcodeUnavailable" not in recoverable
    assert "barcodeUnavailable = true" not in recoverable


def test_barcode_lookup_preserves_idempotency_key_across_transient_retry():
    block = _barcode_block()
    assert "barcodeDraftClientId" in APP_JS
    assert "barcodeDraftValue" in APP_JS
    assert "mealComposerState.barcodeDraftClientId = newMealClientId()" in block
    assert "const clientId = mealComposerState.barcodeDraftClientId" in block
    assert "if (res.status < 500)" in block
    assert "mealComposerState.barcodeDraftClientId = null" in block


def test_manual_barcode_lookup_stops_camera_before_network_request():
    block = _barcode_block()
    submit_section = block.split("async function submitMealBarcode", 1)[1]
    assert submit_section.find("stopMealBarcodeScanner()") < submit_section.find("await postMealBarcodeLookup")


def test_unknown_barcode_404_creates_manual_pending_review():
    block = _barcode_block()
    assert "payload.error.code === 'barcode_not_found'" in block
    assert "allowPending: false" in block
    assert "allowPending: true" in block
    assert "Creating a manual review card" in block
    assert "handleMealIntakeResponse(payload" in block


def test_barcode_offline_path_does_not_call_lookup_endpoint():
    block = _barcode_block()
    offline_section = block.split("if (!online) {", 1)[1].split("const { text }", 1)[0]
    assert "Barcode lookup needs the server" in offline_section
    assert "fetch(" not in offline_section


def test_barcode_styles_ship_with_composer():
    expected = [
        ".meal-composer-scan",
        ".meal-composer-barcode",
        ".meal-composer-barcode-video",
        ".meal-composer-barcode-manual",
        ".meal-composer-barcode-status",
    ]
    missing = [selector for selector in expected if selector not in APP_CSS]
    assert not missing, f"FIT-142 barcode styles missing from style.css: {missing}"
