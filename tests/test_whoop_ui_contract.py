import importlib
from pathlib import Path

from js_runtime import run_app_js


ROOT = Path(__file__).resolve().parents[1]


def test_index_exposes_whoop_dashboard_and_settings_surfaces():
    html = (ROOT / "templates" / "index.html").read_text()
    for token in [
        '/static/css/app.css?v=20260713-fit270-oura-detail', 'id="reco-fresh-whoop"',
        'id="btn-reco-sources"', 'id="reco-sources-summary"', 'id="reco-source-conflict"',
        'id="whoop-int-dot"', 'id="whoop-last-sync"', 'id="whoop-connect-state"',
        'id="btn-connect-whoop" class="btn btn-ghost btn-sm" type="button" hidden aria-haspopup="dialog" aria-controls="modal-whoop-intake"',
        'id="btn-sync-whoop"', 'id="btn-disconnect-whoop"', 'id="btn-delete-whoop-data"',
        'id="whoop-detail-attention"', 'id="whoop-conflict-row"', 'id="modal-reco-sources"',
        'id="modal-whoop-intake"', 'id="whoop-connect-modal-status"', 'id="whoop-connect-open-link"',
        'id="whoop-import-csv-text"', 'id="whoop-import-file"', 'id="btn-whoop-import-file"',
        'id="btn-whoop-import-submit"', 'id="whoop-import-status"', 'aria-controls="modal-reco-sources"',
        'aria-haspopup="dialog"',
    ]:
        assert token in html


def test_whoop_release_assets_and_import_copy_are_stable():
    html = (ROOT / "templates" / "index.html").read_text()
    loader = (ROOT / "static" / "js" / "app-loader.js").read_text()
    service_worker = (ROOT / "static" / "js" / "sw.js").read_text()
    assert "/static/js/app-loader.js?v=20260713-fit270-oura-detail" in html
    assert "/static/js/app.js?v=20260713-fit270-oura-detail" in loader
    assert "fitness-dashboard-v20260713-fit270-oura-detail" in service_worker
    assert "Dates up to one day ahead may be accepted for timezone differences; farther-ahead dates are rejected." in html


def test_whoop_secret_patterns_are_excluded_from_docker_context():
    dockerignore = (ROOT / ".dockerignore").read_text()
    for token in (".whoop-client-id", ".whoop-oauth-state*", ".whoop-token*", ".whoop-protected-material*", "**/.whoop-client-id", "**/.whoop-token*", "**/.whoop-protected-material*"):
        assert token in dockerignore


def test_whoop_gunicorn_uses_safe_access_logs_and_single_threaded_worker_pool():
    dockerfile = (ROOT / "Dockerfile").read_text()
    procfile = (ROOT / "Procfile").read_text()
    for text in (dockerfile, procfile):
        assert "--access-logformat" in text
        assert "%(U)s" in text
        assert "%(q)s" not in text
        assert "%(r)s" not in text
    assert "--workers 1" in dockerfile
    assert "--workers 2" not in dockerfile
    assert "--threads 1" in dockerfile


def test_whoop_state_resolution_prioritizes_connection_and_freshness_contracts():
    output = run_app_js(
        ["resolveWhoopUiState", "mergeWhoopFreshnessNode"],
        """
const conflict = [{ provider: 'whoop', message: 'wearables disagree' }];
process.stdout.write(JSON.stringify({
  missingConfig: e.resolveWhoopUiState({ status: 'missing_config' }),
  disconnected: e.resolveWhoopUiState({ connected: false, status: 'fresh' }),
  csvOnly: e.resolveWhoopUiState({ source_kind: 'csv_only', connected: false }),
  pending: e.resolveWhoopUiState({ connected: true, score_state: 'pending_score' }),
  conflict: e.resolveWhoopUiState({ connected: true }, conflict),
  merged: e.mergeWhoopFreshnessNode({ status: 'fresh', score_state: 'pending_score' }, { status: 'stale', connected: true }, conflict)
}));
""",
    )
    assert output["missingConfig"] == "missing_config"
    assert output["disconnected"] == "disconnected"
    assert output["csvOnly"] == "csv_only"
    assert output["pending"] == "pending_score"
    assert output["conflict"] == "source_conflict"
    assert output["merged"]["status"] == "fresh"
    assert output["merged"]["score_state"] == "pending_score"
    assert output["merged"]["ui_state"] == "source_conflict"
    assert output["merged"]["conflict_message"] == "wearables disagree"


def test_whoop_recommendation_sources_and_conflicts_accept_nested_backend_shape():
    output = run_app_js(
        ["normalizeRecommendationSources", "recommendationSourceConflictNode", "collectSourceConflicts"],
        """
const payload = {
  open_wearables: { role: 'wearable hub' },
  whoop: { explanations: ['recovery'], applied_modifiers: ['reduce strain'] },
  load_source: 'oura'
};
const conflict = { recommendation_sources: { source_conflict: { has_conflict: true, whoop_band: true, oura_band: true, explanation: 'disagreement' } } };
    process.stdout.write(JSON.stringify({ sources: e.normalizeRecommendationSources(payload), node: e.recommendationSourceConflictNode(conflict), conflicts: e.collectSourceConflicts(conflict, null) }));
""",
    )
    assert [entry["key"] for entry in output["sources"]] == ["open_wearables", "whoop", "oura"]
    assert output["sources"][1]["role"] == "modifier"
    assert output["node"] == {"providers": ["whoop", "oura"], "message": "disagreement"}
    assert output["conflicts"] == [{"providers": [], "message": "disagreement"}]


def test_whoop_import_summary_formats_all_row_outcomes():
    output = run_app_js(
        ["formatWhoopImportSummary"],
        """
process.stdout.write(JSON.stringify([
  e.formatWhoopImportSummary({ parsed_rows: 4, imported_rows: 3, skipped_unsupported_rows: 1, ignored_nap_rows: 0, duplicate_or_upserted_rows: 2 }),
  e.formatWhoopImportSummary({ parsed_rows: 4 })
]));
""",
    )
    assert output[0] == "Parsed 4 · Imported 3 · Unsupported 1 · Naps 0 · Duplicates/updates 2"
    assert output[1] is None


def test_whoop_missing_config_disables_live_connect_but_keeps_manual_import():
    output = run_app_js(
        ["applyWhoopIntakeAvailability", "state"],
        """
const live = { disabled: false, textContent: '', attrs: {}, setAttribute(name, value) { this.attrs[name] = value; } };
const modalStatus = { textContent: '', className: '' };
const importStatus = { textContent: '', className: '' };
sandbox.elements['btn-whoop-connect-live'] = live;
sandbox.elements['whoop-connect-modal-status'] = modalStatus;
sandbox.elements['whoop-import-status'] = importStatus;
e.state.whoopStatus = { status: 'missing_config' };
const available = e.applyWhoopIntakeAvailability();
process.stdout.write(JSON.stringify({ available, disabled: live.disabled, aria: live.attrs['aria-disabled'], text: live.textContent, detail: importStatus.textContent }));
""",
    )
    assert output == {
        "available": False,
        "disabled": True,
        "aria": "true",
        "text": "Unavailable",
        "detail": "WHOOP live sync is not configured on this server. Paste or upload a WHOOP export below.",
    }


def test_whoop_freshness_renderer_keeps_manual_import_row_reachable():
    output = run_app_js(
        ["renderWhoopFreshnessDetail"],
        """
const ids = ['whoop-settings-row', 'whoop-detail', 'whoop-int-dot', 'whoop-last-sync', 'whoop-detail-connection', 'whoop-detail-data-through', 'whoop-detail-recovery', 'whoop-detail-source', 'whoop-detail-attention', 'whoop-conflict-row', 'whoop-conflict-text'];
ids.forEach((id) => { sandbox.elements[id] = { hidden: true, textContent: '', className: '', setAttribute() {} }; });
sandbox.__fitSet.setWhoopActionButtons(() => {});
e.renderWhoopFreshnessDetail({ status: 'missing_config', connected: false }, null, []);
const missing = { row: sandbox.elements['whoop-settings-row'].hidden, detail: sandbox.elements['whoop-detail'].hidden, connection: sandbox.elements['whoop-detail-connection'].textContent };
e.renderWhoopFreshnessDetail({ status: 'fresh', connected: true, last_data_point: '2026-07-16', recovery_score: 88 }, { status: 'fresh', last_data_point: '2026-07-16' }, []);
process.stdout.write(JSON.stringify({ missing, fresh: { row: sandbox.elements['whoop-settings-row'].hidden, detail: sandbox.elements['whoop-detail'].hidden, connection: sandbox.elements['whoop-detail-connection'].textContent, recovery: sandbox.elements['whoop-detail-recovery'].textContent } }));
""",
        mocks=["setWhoopActionButtons"],
    )
    assert output["missing"] == {"row": False, "detail": True, "connection": "Config missing"}
    assert output["fresh"] == {"row": False, "detail": False, "connection": "Connected · fresh", "recovery": "Recovery 88"}


def test_whoop_sync_disconnect_and_delete_use_expected_api_paths():
    output = run_app_js(
        ["getWhoopStatus", "syncWhoop", "disconnectWhoop", "deleteWhoopData"],
        """
const calls = [];
sandbox.__fitSet.api(async (path) => { calls.push(path); return { status: 'connected', connection: { status: 'connected' } }; });
sandbox.__fitSet.renderWhoopFreshnessDetail(() => {});
sandbox.__fitSet.renderSettings(async () => {});
sandbox.__fitSet.toast(() => {});
await e.getWhoopStatus(true);
await e.syncWhoop();
await e.disconnectWhoop();
await e.deleteWhoopData();
process.stdout.write(JSON.stringify(calls));
""",
        mocks=["api", "renderWhoopFreshnessDetail", "renderSettings", "toast"],
    )
    assert output == ["/api/whoop/status", "/api/whoop/sync", "/api/whoop/disconnect", "/api/whoop/delete-data"]


def test_whoop_controls_are_wired_to_their_actions():
    output = run_app_js(
        ["wireEvents"],
        """
function control() { return { handlers: {}, clicks: 0, addEventListener(name, fn) { this.handlers[name] = fn; }, click() { this.clicks += 1; } }; }
const ids = [
  'btn-connect-whoop', 'btn-whoop-connect-live', 'btn-whoop-import-submit',
  'btn-whoop-import-file', 'whoop-import-file', 'btn-sync-whoop',
  'btn-disconnect-whoop', 'btn-delete-whoop-data',
];
ids.forEach((id) => { sandbox.elements[id] = control(); });
sandbox.document.querySelectorAll = () => [];
sandbox.addEventListener = () => {};
const calls = [];
sandbox.__fitSet.connectWhoop(() => calls.push('connect'));
sandbox.__fitSet.startWhoopConnectFromModal(() => calls.push('connect-live'));
sandbox.__fitSet.importWhoopCsvFromModal(() => calls.push('import'));
sandbox.__fitSet.syncWhoop(() => calls.push('sync'));
sandbox.__fitSet.disconnectWhoop(() => calls.push('disconnect'));
sandbox.__fitSet.deleteWhoopData(() => calls.push('delete'));
e.wireEvents();
['btn-connect-whoop', 'btn-whoop-connect-live', 'btn-whoop-import-submit', 'btn-whoop-import-file', 'btn-sync-whoop', 'btn-disconnect-whoop', 'btn-delete-whoop-data'].forEach((id) => sandbox.elements[id].handlers.click());
process.stdout.write(JSON.stringify({
  bindings: ids.map((id) => [id, typeof sandbox.elements[id].handlers[id === 'whoop-import-file' ? 'change' : 'click']]),
  calls,
  fileClicks: sandbox.elements['whoop-import-file'].clicks,
}));
""",
        mocks=["connectWhoop", "startWhoopConnectFromModal", "importWhoopCsvFromModal", "syncWhoop", "disconnectWhoop", "deleteWhoopData"],
    )
    assert output["calls"] == ["connect", "connect-live", "import", "sync", "disconnect", "delete"]
    assert output["fileClicks"] == 1
    assert all(binding == "function" for _element_id, binding in output["bindings"])


def test_dashboard_and_smart_nutrition_use_wearable_adjusted_workout(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    today = module._today_str()
    base_plan = {"estimated_minutes": 20, "mesocycle": {"rpe_base": 3}, "exercises": []}
    adjusted_plan = {"estimated_minutes": 50, "mesocycle": {"rpe_base": 8}, "exercises": []}
    food_logs = [{
        "date": today, "logged_at": f"{today}T08:00:00", "calories": 500,
        "protein_g": 20, "carbs_g": 55, "fat_g": 12, "sodium_mg": 400,
        "accepted": True,
    }]

    monkeypatch.setattr(module, "generate_next_workout", lambda *_args, **_kwargs: dict(base_plan))
    monkeypatch.setattr(module, "get_food_logs", lambda *_args, **_kwargs: food_logs)
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(module, "USER_SETTINGS", {"daily_calorie_target": 2200, "daily_protein_target_g": 148})
    monkeypatch.setattr(module, "get_oura_daily", lambda *_args, **_kwargs: {"readiness_score": 82})
    monkeypatch.setattr(module, "get_oura_daily_range", lambda *_args, **_kwargs: [{"hrv": 41}])
    monkeypatch.setattr(module, "compute_hrv_trend", lambda *_args, **_kwargs: "stable")
    monkeypatch.setattr(module, "calculate_acwr", lambda *_args, **_kwargs: {"acwr": 1.0})
    monkeypatch.setattr(module, "calculate_sleep_debt", lambda *_args, **_kwargs: {"debt_minutes": 0, "status": "ok"})
    monkeypatch.setattr(module, "calculate_recovery_bonus", lambda *_args, **_kwargs: {"bonus_points": 0})
    monkeypatch.setattr(module, "_fetch_wttr", lambda *_args, **_kwargs: {"available": False})
    monkeypatch.setattr(module, "_current_workout_plan_for_fingerprint", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "get_current_workout_plan", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_persist_current_workout_plan", lambda plan, *_args, **_kwargs: plan)
    monkeypatch.setattr(module, "_apply_due_workout_adaptations_for_plan", lambda plan, **_kwargs: (plan, []))
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", None)
    monkeypatch.setattr(
        module,
        "_wearable_adjusted_for_display",
        lambda *_args, **_kwargs: {"next_workout": dict(adjusted_plan), "load_source": "whoop", "applied_modifiers": ["harder"]},
    )

    def apply_modifiers(recommendation, next_workout, **_kwargs):
        return {
            "recommendation": recommendation,
            "next_workout": dict(adjusted_plan) if next_workout is not None else None,
            "load_source": "whoop",
            "applied_modifiers": ["harder"],
        }

    monkeypatch.setattr(module, "apply_wearable_modifiers", apply_modifiers)
    original_nutrition = module._nutrition_context_for_date
    hard_flags = []

    def capture_nutrition(*args, **kwargs):
        hard_flags.append(kwargs.get("hard_training_planned"))
        return original_nutrition(*args, **kwargs)

    monkeypatch.setattr(module, "_nutrition_context_for_date", capture_nutrition)
    client = module.app.test_client()

    dashboard = client.get("/api/dashboard")
    dashboard_flags = hard_flags[:]
    hard_flags.clear()
    smart = client.get("/api/recommendation/smart")
    smart_flags = hard_flags[:]

    assert dashboard.status_code == 200
    assert smart.status_code == 200
    assert dashboard_flags[-1] is True and False in dashboard_flags
    assert smart_flags[-1] is True and False in smart_flags
    assert "under_fueled_hard_workout" in {
        warning["code"]
        for warning in dashboard.get_json()["nutrition_today"]["coaching_context"]["warnings"]
    }
    assert "under_fueled_hard_workout" in {
        warning["code"] for warning in smart.get_json()["nutrition_context"]["warnings"]
    }


def test_whoop_hash_routes_settings_and_recommendation_modal_tracks_aria_state():
    output = run_app_js(
        ["initialTabFromHash", "openRecoSourcesModal"],
        """
sandbox.location.hash = '#settings';
const tab = e.initialTabFromHash();
const modal = { hidden: true };
const trigger = { attrs: {}, setAttribute(name, value) { this.attrs[name] = value; } };
sandbox.elements['modal-reco-sources'] = modal;
sandbox.elements['btn-reco-sources'] = trigger;
sandbox.__fitSet.renderRecommendationSourceSummary(() => {});
sandbox.__fitSet.focusOpenModal(() => {});
e.openRecoSourcesModal();
modal.__fit192Close();
process.stdout.write(JSON.stringify({ tab, expanded: trigger.attrs['aria-expanded'], hidden: modal.hidden }));
""",
        mocks=["renderRecommendationSourceSummary", "focusOpenModal"],
    )
    assert output == {"tab": "tab-settings", "expanded": "false", "hidden": True}


def test_whoop_intake_and_modal_styles_remain_scoped_and_accessible():
    app_css = (ROOT / "static" / "css" / "app.css").read_text()
    for token in ["#modal-whoop-intake .modal-sheet", ".whoop-intake-section", ".whoop-import-textarea", ".whoop-import-file-row", ".whoop-intake-status.err"]:
        assert token in app_css
    html = (ROOT / "templates" / "index.html").read_text()
    assert 'id="btn-reco-sources"' in html and 'aria-controls="modal-reco-sources"' in html
    assert 'id="btn-connect-whoop"' in html and 'aria-controls="modal-whoop-intake"' in html


def test_whoop_connect_detaches_popup_opener_before_navigation():
    output = run_app_js(
        ["startWhoopConnectFromModal", "state"],
        """
const popup = { opener: 'unsafe', closed: false, location: {} };
sandbox.window.open = () => popup;
e.state.whoopStatus = { status: 'connected', connected: true };
sandbox.__fitSet.applyWhoopIntakeAvailability(() => true);
sandbox.__fitSet.setWhoopIntakeStatus(() => {});
sandbox.__fitSet.setWhoopConnectFallback(() => {});
sandbox.__fitSet.markWhoopOAuthPending(() => {});
let path = '';
sandbox.__fitSet.api(async (requestPath) => { path = requestPath; return { authorization_url: 'https://whoop.example/authorize' }; });
await e.startWhoopConnectFromModal();
process.stdout.write(JSON.stringify({ openerDetached: popup.opener === null, href: popup.location.href, path, inFlight: e.state.whoopUi.connectInFlight }));
""",
        mocks=["applyWhoopIntakeAvailability", "setWhoopIntakeStatus", "setWhoopConnectFallback", "markWhoopOAuthPending", "api"],
    )
    assert output == {
        "openerDetached": True,
        "href": "https://whoop.example/authorize",
        "path": "/api/whoop/connect/start",
        "inFlight": False,
    }


def test_whoop_csv_import_routes_unauthorized_response_through_shared_handler():
    output = run_app_js(
        ["importWhoopCsvFromModal", "state"],
        """
const textarea = { value: 'date,recovery_score\\n2026-07-16,80' };
const submit = { disabled: false };
const status = { textContent: '', className: '' };
sandbox.elements['whoop-import-csv-text'] = textarea;
sandbox.elements['btn-whoop-import-submit'] = submit;
sandbox.elements['whoop-import-status'] = status;
let requestedPath = '';
let handledStatus = null;
sandbox.__fitSet.fetch(async (path) => { requestedPath = path; return new Response('{}', { status: 401 }); });
sandbox.__fitSet.handleUnauthorizedResponse(async (response) => { handledStatus = response.status; throw new Error('unauthorized'); });
await e.importWhoopCsvFromModal();
process.stdout.write(JSON.stringify({ requestedPath, handledStatus, status: status.textContent, disabled: submit.disabled, inFlight: e.state.whoopUi.importInFlight }));
""",
        mocks=["fetch", "handleUnauthorizedResponse"],
    )
    assert output == {
        "requestedPath": "/api/whoop/import-csv",
        "handledStatus": 401,
        "status": "unauthorized",
        "disabled": False,
        "inFlight": False,
    }
