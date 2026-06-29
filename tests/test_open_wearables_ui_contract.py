from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_settings_contains_open_wearables_hub_controls():
    html = (ROOT / "templates" / "index.html").read_text()
    gitignore = (ROOT / ".gitignore").read_text()
    assert "Open Wearables" in html
    assert 'id="open-wearables-state"' in html
    assert 'id="btn-open-wearables-setup"' in html
    assert 'id="modal-open-wearables-setup"' in html
    assert 'id="open-wearables-base-url"' in html
    assert 'id="open-wearables-username"' in html
    assert 'id="open-wearables-password"' in html
    assert 'id="open-wearables-user-id"' in html
    assert 'id="open-wearables-allowed-hosts"' not in html
    assert 'id="btn-open-wearables-portal"' in html
    assert 'id="btn-open-wearables-portal-inline"' in html
    assert 'id="btn-open-wearables-copy-link"' in html
    assert 'id="btn-open-wearables-copy-link-inline"' in html
    assert 'id="btn-open-wearables-setup-save"' in html
    assert 'id="btn-open-wearables-setup-check"' in html
    assert 'id="btn-sync-open-wearables"' in html
    assert 'id="settings-row-oura"' in html
    assert 'id="settings-row-apple"' in html
    assert ">Open GitHub<" not in html
    assert "Add a wearable" in html
    assert "This web app prepares the local hub account" in html
    assert "Check connection" in html
    assert "Save advanced" in html
    assert "Advanced" in html
    assert "Reference only" in html
    assert "WHOOP fallback" in html
    assert "Garmin" in html
    assert "Suunto" in html
    assert "Polar" in html
    assert "Ultrahuman" in html
    assert "Samsung Health" in html
    assert "Google Health Connect" in html
    assert "Cloud wearables open provider sign-in" in html
    assert 'id="open-wearables-mobile-invite"' in html
    assert "One-time code" in html
    assert "https://github.com/the-momentum/open-wearables" in html
    assert "Base URL" not in html
    assert "open_wearables_config.json" in gitignore


def test_app_js_wires_open_wearables_sources_and_history_category():
    html = (ROOT / "templates" / "index.html").read_text()
    js = (ROOT / "static" / "js" / "app.js").read_text()
    assert "/api/open-wearables/status" in js
    assert "/api/wearable-sources" in js
    assert "/api/open-wearables/setup" in js
    assert "renderOpenWearablesDetail" in js
    assert "saveOpenWearablesSetup" in js
    assert "openOpenWearablesPortal" in js
    assert "copyOpenWearablesPairingLink" in js
    assert "populateOpenWearablesSetupFields" in js
    assert "safe.pairing_url || safe.portal_url" in js
    assert "function openWearablesIsConnected" in js
    assert "function openWearablesCanSync" in js
    assert "&& Array.isArray(status && status.providers)" in js
    assert "!openWearablesCanSync(" in js
    assert "checkStatus === 'ok'" in js
    assert "Choose your wearable to continue." in js
    assert "Phone setup" in js
    assert "function openWearablesProviderConnected" in js
    assert "function openWearablesDirectSourceReplaced" in js
    assert "function applyOpenWearablesDirectSourceVisibility" in js
    assert "dataset.statusLabel = providerConnected ? 'Connected'" in js
    assert "provider.stale || provider.error_code" in js
    assert "status && status.replacement_sources" in js
    assert "is connected through Open Wearables" in js
    assert "rowId: 'settings-row-oura'" in js
    assert "rowId: 'settings-row-apple'" in js
    assert "elementOrAncestorHidden" in js
    assert "Opening Open Wearables now" in js
    assert "Do not open the server address in a browser" in js
    assert "/api/open-wearables/mobile-invite/" in js
    assert "prepareOpenWearablesThenContinue" in js
    assert "provider_disabled" in js
    assert "openOpenWearablesPortal(message)" in js
    assert "function openOpenWearablesPortal(statusMessage = '')" in js
    assert "$('btn-open-wearables-portal') && $('btn-open-wearables-portal').addEventListener('click', () => openOpenWearablesPortal());" in js
    assert "$('btn-open-wearables-portal-inline') && $('btn-open-wearables-portal-inline').addEventListener('click', () => openOpenWearablesPortal());" in js
    assert "$('btn-open-wearables-copy-link-inline') && $('btn-open-wearables-copy-link-inline').addEventListener('click', copyOpenWearablesPairingLink);" in js
    assert "Enter this server address and one-time code inside the Open Wearables mobile app" in html
    assert "Direct WHOOP fallback" in js
    assert js.index("state.openWearablesStatus = body.open_wearables") < js.index("populateOpenWearablesSetupFields(body && body.config)")
    modal_open_block = js.split("async function openOpenWearablesSetupModal()", 1)[1].split("function readOpenWearablesSetupFields()", 1)[0]
    assert "loadOpenWearablesSetup()" in modal_open_block
    assert "bootstrapOpenWearablesSetup()" not in modal_open_block
    check_block = js.split("async function checkOpenWearables()", 1)[1].split("async function syncOpenWearables()", 1)[0]
    assert "populateOpenWearablesSetupFields(body.config)" in check_block
    assert check_block.index("populateOpenWearablesSetupFields(body.config)") < check_block.index("openWearablesIsConnected")
    assert "hasProviderSetupPath" in js
    assert "&& !hasProviderSetupPath" in js
    assert "canonicalHistoryCategory" in js
    assert "strength_training" in js


def test_ai_fact_query_ui_is_suggestion_safe():
    html = (ROOT / "templates" / "index.html").read_text()
    js = (ROOT / "static" / "js" / "app.js").read_text()
    settings_html = html.split('id="tab-settings"', 1)[1].split('<nav class="tab-bar"', 1)[0]
    dashboard_html = html.split('id="tab-dashboard"', 1)[1].split('id="tab-vitals"', 1)[0]
    assert 'id="ai-fact-question"' not in settings_html
    assert 'id="modal-ai-fact-query"' in html
    assert 'id="btn-open-ai-fact-query"' in dashboard_html
    assert 'id="ai-fact-question"' in html
    assert 'id="ai-fact-suggestion-actions"' in html
    assert 'id="btn-ai-suggestion-approve"' in html
    assert 'id="btn-ai-suggestion-reject"' in html
    assert "function openAiFactQueryModal()" in js
    assert "$('btn-open-ai-fact-query') && $('btn-open-ai-fact-query').addEventListener('click', openAiFactQueryModal);" in js
    assert "/api/ai/facts/query" in js
    assert "suggest: true" in js
    assert "resolveAiSuggestion" in js
    assert "No records were changed" in js


def test_history_ui_merges_same_day_strength_sources_instead_of_double_counting():
    js = (ROOT / "static" / "js" / "app.js").read_text()
    assert "mergeStrengthHistorySources" in js
    assert "Strength - Logged + Watch" in js
    assert "merged Watch" in js
    assert "frequency bars by day - count canonical merged sessions once" in js
