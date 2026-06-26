from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "templates" / "index.html"
APP_JS = ROOT / "static" / "js" / "app.js"
APP_CSS = ROOT / "static" / "css" / "app.css"


def test_index_exposes_whoop_dashboard_and_settings_surfaces():
    html = INDEX_HTML.read_text()

    for token in [
        '/static/css/app.css?v=20260626-fit239-whoop',
        'id="reco-fresh-whoop"',
        'id="btn-reco-sources"',
        'id="reco-sources-summary"',
        'id="reco-source-conflict"',
        'id="whoop-int-dot"',
        'id="whoop-last-sync"',
        'id="whoop-connect-state"',
        'id="btn-sync-whoop"',
        'id="btn-disconnect-whoop"',
        'id="whoop-detail-attention"',
        'id="whoop-conflict-row"',
        'id="modal-reco-sources"',
        'aria-controls="modal-reco-sources"',
        'aria-haspopup="dialog"',
    ]:
        assert token in html


def test_whoop_frontend_calls_expected_status_sync_and_disconnect_endpoints():
    app_js = APP_JS.read_text()

    assert "async function getWhoopStatus(force = false)" in app_js
    assert "async function syncWhoop()" in app_js
    assert "async function disconnectWhoop()" in app_js
    assert "api('/api/whoop/status'" in app_js
    assert "api('/api/whoop/connect/start', { method: 'POST' })" in app_js
    assert "api('/api/whoop/sync'" in app_js
    assert "api('/api/whoop/disconnect'" in app_js
    assert "async function connectWhoop()" in app_js
    assert "window.location.assign(nextUrl);" in app_js


def test_whoop_frontend_preserves_keyboard_and_modal_focus_path():
    app_js = APP_JS.read_text()

    assert "function openRecoSourcesModal()" in app_js
    assert "focusOpenModal(modal);" in app_js
    assert "trigger.setAttribute('aria-expanded', 'true');" in app_js
    assert "trigger.setAttribute('aria-expanded', 'false');" in app_js
    assert "$('btn-reco-sources') && $('btn-reco-sources').addEventListener('click', openRecoSourcesModal);" in app_js
    assert "$('btn-sync-whoop') && $('btn-sync-whoop').addEventListener('click', syncWhoop);" in app_js
    assert "$('btn-disconnect-whoop') && $('btn-disconnect-whoop').addEventListener('click', disconnectWhoop);" in app_js


def test_whoop_recommendation_source_conflict_accepts_nested_backend_shape():
    app_js = APP_JS.read_text()

    assert "if (entries && !Array.isArray(entries) && typeof entries === 'object')" in app_js
    assert "entries.whoop" in app_js
    assert "entries.load_source" in app_js
    assert "function recommendationSourceConflictNode(payload)" in app_js
    assert "payload.recommendation_sources && payload.recommendation_sources.source_conflict" in app_js
    assert "function collectSourceConflicts(dash, reco)" in app_js
    assert "const conflicts = collectSourceConflicts(dash, reco);" in app_js
    assert "renderWhoopFreshnessDetail(whoop, whoopFreshness, collectSourceConflicts(state.dashboard, state.reco));" in app_js


def test_whoop_freshness_status_wins_over_connection_status_merge():
    app_js = APP_JS.read_text()

    assert "if (freshnessNode && freshnessNode.status) merged.status = freshnessNode.status;" in app_js
    assert "if (freshnessNode && freshnessNode.score_state) merged.score_state = freshnessNode.score_state;" in app_js


def test_whoop_styles_live_in_scoped_override_file():
    css = APP_CSS.read_text()

    for selector in [
        ".reco-source-strip {",
        ".reco-source-conflict {",
        ".reco-source-list {",
        ".settings-inline-actions {",
        "#modal-reco-sources .modal-sheet {",
    ]:
        assert selector in css
