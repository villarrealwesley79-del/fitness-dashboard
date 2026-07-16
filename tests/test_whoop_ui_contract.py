from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "templates" / "index.html"
APP_JS = ROOT / "static" / "js" / "app.js"
APP_CSS = ROOT / "static" / "css" / "app.css"
APP_LOADER = ROOT / "static" / "js" / "app-loader.js"
APP_SW = ROOT / "static" / "js" / "sw.js"


def test_index_exposes_whoop_dashboard_and_settings_surfaces():
    html = INDEX_HTML.read_text()

    for token in [
        '/static/css/app.css?v=20260713-fit270-oura-detail',
        'id="reco-fresh-whoop"',
        'id="btn-reco-sources"',
        'id="reco-sources-summary"',
        'id="reco-source-conflict"',
        'id="whoop-int-dot"',
        'id="whoop-last-sync"',
        'id="whoop-connect-state"',
        'id="btn-connect-whoop" class="btn btn-ghost btn-sm" type="button" hidden aria-haspopup="dialog" aria-controls="modal-whoop-intake"',
        'id="btn-sync-whoop"',
        'id="btn-disconnect-whoop"',
        'id="btn-delete-whoop-data"',
        'id="whoop-detail-attention"',
        'id="whoop-conflict-row"',
        'id="modal-reco-sources"',
        'id="modal-whoop-intake"',
        'id="whoop-connect-modal-status"',
        'id="whoop-connect-open-link"',
        'id="whoop-import-csv-text"',
        'id="whoop-import-file"',
        'id="btn-whoop-import-file"',
        'id="btn-whoop-import-submit"',
        'id="whoop-import-status"',
        'aria-controls="modal-reco-sources"',
        'aria-haspopup="dialog"',
    ]:
        assert token in html


def test_whoop_release_assets_bust_cached_fit238_runtime():
    html = INDEX_HTML.read_text()
    loader = APP_LOADER.read_text()
    service_worker = APP_SW.read_text()

    assert "/static/js/app-loader.js?v=20260716-fit392-rpe" in html
    assert "/static/js/app.js?v=20260716-fit392-rpe" in loader
    assert "fitness-dashboard-v20260716-fit392-rpe" in service_worker


def test_whoop_import_explains_timezone_date_tolerance():
    html = INDEX_HTML.read_text()

    assert "Dates up to one day ahead may be accepted for timezone differences; farther-ahead dates are rejected." in html


def test_whoop_secret_patterns_are_excluded_from_docker_context():
    dockerignore = (ROOT / ".dockerignore").read_text()

    for token in [
        ".whoop-client-id",
        ".whoop-oauth-state*",
        ".whoop-token*",
        ".whoop-protected-material*",
        "**/.whoop-client-id",
        "**/.whoop-token*",
        "**/.whoop-protected-material*",
    ]:
        assert token in dockerignore


def test_whoop_oauth_callback_query_is_not_logged_by_gunicorn_access_logs():
    dockerfile = (ROOT / "Dockerfile").read_text()
    procfile = (ROOT / "Procfile").read_text()

    assert "--access-logformat" in dockerfile
    assert "--access-logformat" in procfile
    assert "%(U)s" in dockerfile
    assert "%(U)s" in procfile
    assert "%(q)s" not in dockerfile
    assert "%(q)s" not in procfile
    assert "%(r)s" not in dockerfile
    assert "%(r)s" not in procfile


def test_docker_gunicorn_uses_single_worker_with_threads():
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "--workers 1" in dockerfile
    assert "--workers 2" not in dockerfile
    assert "--threads 1" in dockerfile


def test_whoop_frontend_calls_expected_status_sync_and_disconnect_endpoints():
    app_js = APP_JS.read_text()

    assert "async function getWhoopStatus(force = false)" in app_js
    assert "async function syncWhoop()" in app_js
    assert "async function disconnectWhoop()" in app_js
    assert "async function deleteWhoopData()" in app_js
    assert "api('/api/whoop/status'" in app_js
    assert "api('/api/whoop/connect/start', { method: 'POST' })" in app_js
    assert "api('/api/whoop/sync'" in app_js
    assert "api('/api/whoop/disconnect'" in app_js
    assert "api('/api/whoop/delete-data'" in app_js
    assert "async function connectWhoop()" in app_js
    assert "function openWhoopIntakeModal()" in app_js
    assert "async function startWhoopConnectFromModal()" in app_js
    assert "async function importWhoopCsvFromModal()" in app_js
    assert "fetch('/api/whoop/import-csv'" in app_js
    assert "await handleUnauthorizedResponse(response);" in app_js
    assert "window.open('', 'fitnessDashboardWhoopOAuth'" in app_js
    assert "popup.opener = null;" in app_js
    assert "setWhoopConnectFallback(nextUrl" in app_js
    assert "function markWhoopOAuthPending(popup)" in app_js
    assert "async function refreshWhoopAfterOAuthReturn()" in app_js
    assert "refreshWhoopAfterOAuthReturn();" in app_js
    assert "markWhoopOAuthPending(popup);" in app_js
    assert "connectBtn.hidden = false;" in app_js
    assert "liveSyncUnavailable || connectUrl ? 'Connect' : 'Import'" in app_js
    assert "missingConfig && !connectUrl ? 'Import'" in app_js
    assert "clearWhoopImportInput();" in app_js
    assert "if (modal.id === 'modal-whoop-intake') clearWhoopImportInput();" in app_js


def test_whoop_no_data_states_keep_manual_import_row_reachable():
    app_js = APP_JS.read_text()
    renderer = app_js.split("function renderWhoopFreshnessDetail", 1)[1].split(
        "function renderOpenWearablesDetail", 1
    )[0]

    assert "!dataThrough" in renderer
    assert "uiState === WHOOP_UI_STATES.missing_config" in renderer
    assert "uiState === WHOOP_UI_STATES.disconnected" in renderer
    assert "if (row) row.hidden = false;" in renderer
    assert "if (detailPanel) detailPanel.hidden = hideDirectFallback;" in renderer
    assert "if (hideDirectFallback) return;" not in renderer
    assert "uiState === WHOOP_UI_STATES.disconnected || uiState === WHOOP_UI_STATES.missing_config" in renderer
    assert renderer.index("if (detailPanel) detailPanel.hidden = hideDirectFallback;") < renderer.index(
        "setWhoopActionButtons(whoop, uiState);"
    )


def test_whoop_connected_or_data_present_states_keep_existing_renderer_path():
    app_js = APP_JS.read_text()
    renderer = app_js.split("function renderWhoopFreshnessDetail", 1)[1].split(
        "function renderOpenWearablesDetail", 1
    )[0]

    assert "const dataThrough =" in renderer
    assert "const hideDirectFallback = Boolean(" in renderer
    assert "if (dot) dot.className = uiState === WHOOP_UI_STATES.disconnected" in renderer
    assert "setWhoopActionButtons(whoop, uiState);" in renderer


def test_whoop_import_success_banner_summarizes_all_row_outcomes():
    app_js = APP_JS.read_text()

    assert "function formatWhoopImportSummary(importResult)" in app_js
    assert "importResult.parsed_rows" in app_js
    assert "importResult.imported_rows" in app_js
    assert "importResult.skipped_unsupported_rows" in app_js
    assert "importResult.ignored_nap_rows" in app_js
    assert "importResult.duplicate_or_upserted_rows" in app_js
    assert "Parsed ${parsed} · Imported ${imported} · Unsupported ${unsupported} · Naps ${naps} · Duplicates/updates ${duplicates}" in app_js


def test_whoop_missing_config_modal_disables_dead_live_connect_path():
    app_js = APP_JS.read_text()

    assert "function applyWhoopIntakeAvailability()" in app_js
    assert "const liveUnavailable = uiState === WHOOP_UI_STATES.missing_config && !url;" in app_js
    assert "liveBtn.disabled = liveUnavailable || state.whoopUi.connectInFlight;" in app_js
    assert "liveBtn.setAttribute('aria-disabled', liveUnavailable ? 'true' : 'false');" in app_js
    assert "WHOOP live sync is not configured on this server. Paste or upload a WHOOP export below." in app_js
    assert "applyWhoopIntakeAvailability();" in app_js


def test_whoop_oauth_success_hash_routes_to_settings_tab():
    app_js = APP_JS.read_text()

    assert "function initialTabFromHash()" in app_js
    assert "function switchTabFromHash(force = false)" in app_js
    assert "if (hash === '#settings') return 'tab-settings';" in app_js
    assert "switchTabFromHash(true);" in app_js
    assert "window.addEventListener('hashchange', switchTabFromHash);" in app_js


def test_whoop_frontend_preserves_keyboard_and_modal_focus_path():
    app_js = APP_JS.read_text()

    assert "function openRecoSourcesModal()" in app_js
    assert "focusOpenModal(modal);" in app_js
    assert "trigger.setAttribute('aria-expanded', 'true');" in app_js
    assert "trigger.setAttribute('aria-expanded', 'false');" in app_js
    assert "$('btn-reco-sources') && $('btn-reco-sources').addEventListener('click', openRecoSourcesModal);" in app_js
    assert "$('btn-whoop-connect-live') && $('btn-whoop-connect-live').addEventListener('click', startWhoopConnectFromModal);" in app_js
    assert "$('btn-whoop-import-submit') && $('btn-whoop-import-submit').addEventListener('click', importWhoopCsvFromModal);" in app_js
    assert "$('btn-whoop-import-file') && $('btn-whoop-import-file').addEventListener('click', () => $('whoop-import-file') && $('whoop-import-file').click());" in app_js
    assert "$('btn-sync-whoop') && $('btn-sync-whoop').addEventListener('click', syncWhoop);" in app_js
    assert "$('btn-disconnect-whoop') && $('btn-disconnect-whoop').addEventListener('click', disconnectWhoop);" in app_js
    assert "$('btn-delete-whoop-data') && $('btn-delete-whoop-data').addEventListener('click', deleteWhoopData);" in app_js


def test_whoop_intake_styles_are_scoped_to_modal():
    app_css = APP_CSS.read_text()

    for token in [
        "#modal-whoop-intake .modal-sheet",
        ".whoop-intake-section",
        ".whoop-import-textarea",
        ".whoop-import-file-row",
        ".whoop-intake-status.err",
    ]:
        assert token in app_css


def test_whoop_recommendation_source_conflict_accepts_nested_backend_shape():
    app_js = APP_JS.read_text()

    assert "if (entries && !Array.isArray(entries) && typeof entries === 'object')" in app_js
    assert "entries.whoop" in app_js
    assert "entries.load_source" in app_js
    assert "if (entries.whoop && !entries.whoop.summary_hidden)" in app_js
    assert "if (entries.load_source && !entries.load_source_summary_hidden)" in app_js
    assert "function recommendationSourceConflictNode(payload)" in app_js
    assert "payload.recommendation_sources && payload.recommendation_sources.source_conflict" in app_js
    assert "function collectSourceConflicts(dash, reco)" in app_js
    assert "const conflicts = collectSourceConflicts(dash, reco);" in app_js
    assert "renderWhoopFreshnessDetail(whoop, whoopFreshness, collectSourceConflicts(state.dashboard, state.reco));" in app_js


def test_whoop_freshness_status_wins_over_connection_status_merge():
    app_js = APP_JS.read_text()

    assert "if (freshnessNode && freshnessNode.status) merged.status = freshnessNode.status;" in app_js
    assert "if (freshnessNode && freshnessNode.score_state) merged.score_state = freshnessNode.score_state;" in app_js
    assert "const scoreState = normalizeWhoopStateToken(whoop && whoop.score_state);" in app_js
    assert "scoreState === WHOOP_UI_STATES.pending_score" in app_js
    assert "scoreState === WHOOP_UI_STATES.calibrating" in app_js
    assert "missing_config: 'missing_config'" in app_js
    assert "if (status === WHOOP_UI_STATES.missing_config) return WHOOP_UI_STATES.missing_config;" in app_js
    assert "connectionState === WHOOP_UI_STATES.missing_config" in app_js
    assert "const csvOnlyDisconnected = uiState === WHOOP_UI_STATES.csv_only && (!whoop || whoop.connected === false);" in app_js
    assert "syncBtn.disabled = busy || liveSyncUnavailable;" in app_js
    assert "disconnectBtn.hidden = cleanupUnavailable;" in app_js
    assert "state.dashboard = null;\n            state.reco = null;\n            await renderSettings();" in app_js
    resolver_start = app_js.index("function resolveWhoopUiState")
    csv_only_check = app_js.index("normalizeWhoopStateToken(whoop.source_kind) === WHOOP_UI_STATES.csv_only", resolver_start)
    missing_config_check = app_js.index("if (status === WHOOP_UI_STATES.missing_config)", resolver_start)
    disconnected_check = app_js.index("if (status === WHOOP_UI_STATES.disconnected", resolver_start)
    conflict_check = app_js.index("if (firstWhoopConflict(conflicts)", resolver_start)
    assert csv_only_check < missing_config_check
    assert disconnected_check < conflict_check
    assert app_js.index("normalizeWhoopStateToken(whoop.source_kind) === WHOOP_UI_STATES.csv_only") < app_js.index("whoop.connected === false")
    assert app_js.index("whoop.connected === false") < app_js.index("if (status === WHOOP_UI_STATES.fresh")


def test_dashboard_recomputes_nutrition_after_whoop_adjustment():
    app_py = (ROOT / "app.py").read_text()

    # api_dashboard applies the wearable modifier as a display-time transform via
    # the fail-open helper (FIT-256 finding 2); the ordering contract is unchanged:
    # adjust -> recompute nutrition for the adjusted plan -> build the public payload.
    route_start = app_py.index("def api_dashboard():")
    whoop_adjust = app_py.index("whoop_adjusted = _wearable_adjusted_for_display(", route_start)
    recompute = app_py.index("nutrition_context = _nutrition_context_for_date(", whoop_adjust)
    public_payload = app_py.index("nutrition_today_payload = _nutrition_today_public_payload", recompute)

    assert whoop_adjust < recompute < public_payload


def test_smart_recommendation_recomputes_nutrition_after_whoop_adjustment():
    app_py = (ROOT / "app.py").read_text()

    route_start = app_py.index("def smart_recommendation_api():")
    whoop_adjust = app_py.index("whoop_adjusted = apply_wearable_modifiers(", route_start)
    recompute = app_py.index("nutrition_context = _nutrition_context_for_date(", whoop_adjust)
    confidence = app_py.index("confidence_level = _confidence_level_from", recompute)

    assert whoop_adjust < recompute < confidence


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
