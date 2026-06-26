from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_settings_contains_open_wearables_hub_controls():
    html = (ROOT / "templates" / "index.html").read_text()
    assert "Open Wearables" in html
    assert 'id="open-wearables-state"' in html
    assert 'id="btn-check-open-wearables"' in html
    assert 'id="btn-sync-open-wearables"' in html


def test_app_js_wires_open_wearables_sources_and_history_category():
    js = (ROOT / "static" / "js" / "app.js").read_text()
    assert "/api/open-wearables/status" in js
    assert "/api/wearable-sources" in js
    assert "renderOpenWearablesDetail" in js
    assert "canonicalHistoryCategory" in js
    assert "strength_training" in js


def test_ai_fact_query_ui_is_suggestion_safe():
    html = (ROOT / "templates" / "index.html").read_text()
    js = (ROOT / "static" / "js" / "app.js").read_text()
    assert 'id="ai-fact-question"' in html
    assert 'id="ai-fact-suggestion-actions"' in html
    assert 'id="btn-ai-suggestion-approve"' in html
    assert 'id="btn-ai-suggestion-reject"' in html
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
