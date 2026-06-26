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
    assert 'id="btn-open-wearables-copy-link"' in html
    assert 'id="btn-open-wearables-setup-save"' in html
    assert 'id="btn-open-wearables-setup-check"' in html
    assert 'id="btn-sync-open-wearables"' in html
    assert ">Open GitHub<" not in html
    assert "Open pairing" in html
    assert "Copy link" in html
    assert "Check connection" in html
    assert "Save advanced" in html
    assert "Advanced" in html
    assert "Reference only" in html
    assert "https://github.com/the-momentum/open-wearables" in html
    assert "Base URL" not in html
    assert "open_wearables_config.json" in gitignore


def test_app_js_wires_open_wearables_sources_and_history_category():
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
    assert "checkStatus === 'ok'" in js
    assert "Open the pairing portal, connect a wearable, then check connection." in js
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
