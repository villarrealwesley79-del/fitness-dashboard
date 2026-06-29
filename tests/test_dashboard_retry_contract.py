import importlib
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_template_has_three_retry_chips():
    """FIT-128: each of the three primary dashboard cards must carry a
    hidden-by-default retry chip in its header."""
    template = (ROOT / "templates" / "index.html").read_text()

    # Readiness card → /api/oura/status
    assert 'id="readiness-retry"' in template, "readiness-retry chip missing"
    # AI Recommendation card → /api/dashboard + /api/recommendation/smart
    assert 'id="reco-retry"' in template, "reco-retry chip missing"
    # Insight card → /api/oura/sleep-summary
    assert 'id="insight-retry"' in template, "insight-retry chip missing"

    # All three must start hidden so the cold-open path doesn't flash a chip
    # before any fetch has rejected.
    for chip_id in ("readiness-retry", "reco-retry", "insight-retry"):
        # The opening tag should carry both id=... and the hidden attribute.
        # We allow other attributes between them in either order.
        start = template.index(f'id="{chip_id}"')
        end = template.index(">", start)
        opening_tag = template[start:end + 1]
        assert " hidden" in opening_tag, f"{chip_id} must start hidden"

    # Chips must use button semantics (not <span>) so they're keyboard-
    # accessible and screen-readers announce them as clickable.
    for chip_id in ("readiness-retry", "reco-retry", "insight-retry"):
        # Walk back from id=... until we hit the opening '<'
        idx = template.index(f'id="{chip_id}"')
        tag_open = template.rfind("<", 0, idx)
        opening = template[tag_open:idx + len(f'id="{chip_id}"')]
        assert opening.startswith("<button"), (
            f"{chip_id} must be a <button> element for accessibility, "
            f"got: {opening[:60]}"
        )


def test_chip_retry_css_is_defined():
    """FIT-128: .chip-retry must have its own style so the chips don't fall
    back to browser-default button chrome (same regression class as FIT-114)."""
    css = (ROOT / "static" / "css" / "style.css").read_text()
    assert ".chip-retry {" in css, ".chip-retry block missing"
    block = css.split(".chip-retry {", 1)[1].split("}", 1)[0]
    assert "background:" in block, ".chip-retry missing background rule"
    assert "color:" in block, ".chip-retry missing color rule"
    assert "cursor: pointer" in block, ".chip-retry must signal clickability"

    # Hover and focus states (a button without :focus-visible is not
    # keyboard-accessible).
    assert ".chip-retry:hover" in css, ".chip-retry:hover state missing"
    assert ".chip-retry:focus-visible" in css, ".chip-retry:focus-visible missing"


def test_fetchers_no_longer_write_error_sentinels():
    """FIT-129: sentinel ownership moved from the fetchers into
    renderDashboard's settle helper so a stale fetch from an older render or
    retry can no longer flip the sentinel back on. Each fetcher body must
    contain NO `state.<X>Error =` writes (any direction). The catch must
    still null the cache slice (preserves the data-invalidation behavior the
    rest of the painters rely on), and must NOT re-throw (preserves the
    swallow-on-failure contract that non-dashboard callers — renderVitals,
    renderNextWorkout, renderSettings — depend on for graceful-null
    rendering)."""
    app_js = (ROOT / "static" / "js" / "app.js").read_text()

    for fetcher, cache_slice in [
        ("getOuraStatus", "state.oura"),
        ("getOuraSleep", "state.ouraSleep"),
        ("getReco", "state.reco"),
    ]:
        marker = f"async function {fetcher}("
        assert marker in app_js, f"{fetcher} not found"
        body = app_js.split(marker, 1)[1].split("\n    }\n", 1)[0]

        # Negative: no sentinel writes survive in the fetcher body. A stale
        # write here would re-introduce the FIT-129 race regardless of the
        # gen guard.
        for sentinel in ("state.ouraError", "state.recoError", "state.ouraSleepError"):
            assert f"{sentinel} =" not in body, (
                f"{fetcher} body must not write `{sentinel}` — sentinel "
                f"ownership lives in renderDashboard's settle helper (FIT-129)"
            )

        # Positive: cache invalidation on failure is preserved.
        assert f"{cache_slice} = null" in body, (
            f"{fetcher} catch must still set `{cache_slice} = null` so a "
            f"failed refetch doesn't leave stale data in the cache"
        )

        # Positive: catch must not re-throw — non-dashboard callers
        # (renderVitals / renderNextWorkout / renderSettings) rely on the
        # swallow-on-failure contract.
        catch_body = body.split("catch", 1)[1].split("\n", 1)[0]
        assert "throw" not in catch_body, (
            f"{fetcher} catch must not re-throw — non-dashboard callers "
            f"(renderVitals/renderNextWorkout/renderSettings) rely on the "
            f"swallow-on-failure contract"
        )


def test_next_workout_render_does_not_require_auxiliary_recommendation_calls():
    """FIT-181: the workout execution tab must still render the dashboard
    plan when helper calls that only supply copy/chip context fail."""
    app_js = (ROOT / "static" / "js" / "app.js").read_text()
    marker = "async function renderNextWorkout()"
    body = app_js.split(marker, 1)[1].split("\n    }\n", 1)[0]

    assert "nw = await getNextWorkout(true);" in body, (
        "renderNextWorkout must load the lightweight workout-only contract "
        "instead of the full dashboard payload, and must let the server "
        "fingerprint invalidate stale plans"
    )
    assert "catch (err)" in body and "state.nextWorkout || (state.dashboard && state.dashboard.next_workout)" in body, (
        "renderNextWorkout must fall back to the last hydrated workout plan "
        "when the lightweight endpoint has a transient failure"
    )
    assert "getReco().then(" in body and ".catch(() => {})" in body, (
        "renderNextWorkout must update /api/recommendation/smart reasoning "
        "asynchronously so the Workout tab still renders during a reco "
        "retry-chip state"
    )
    assert "getSettings().then(" in body and "settings unavailable for next workout render" in body, (
        "renderNextWorkout must update /api/settings RPE context asynchronously; "
        "settings only decorate the RPE chip"
    )
    assert "renderRpe(null);" in body, "renderNextWorkout must paint fallback RPE immediately"
    assert "renderWhy(null);" in body, "renderNextWorkout must paint fallback reasoning immediately"
    assert "gen !== nextWorkoutRenderGen" in body, (
        "late optional helper responses must not overwrite a newer Workout tab render"
    )
    assert "Promise.all([getDashboard(), getReco(), getSettings()])" not in body, (
        "renderNextWorkout must not let optional helper failures abort the "
        "dashboard next_workout render"
    )
    assert "await getDashboard()" not in body, (
        "renderNextWorkout must not wait on the heavy /api/dashboard endpoint"
    )


def test_start_workout_falls_back_to_cached_dashboard_plan_on_fetch_failure():
    """FIT-181: Start Workout should use the already-rendered dashboard plan
    if a fresh dashboard fetch fails while the user is trying to enter the
    active workout flow."""
    app_js = (ROOT / "static" / "js" / "app.js").read_text()
    marker = "async function startWorkout()"
    body = app_js.split(marker, 1)[1].split("\n    }\n", 1)[0]

    assert "try {" in body and "nw = await getNextWorkout(true);" in body
    assert "catch (err)" in body, "startWorkout must handle next-workout fetch failures"
    assert "state.nextWorkout || (state.dashboard && state.dashboard.next_workout)" in body, (
        "startWorkout must fall back to cached workout plans before "
        "showing No workout planned"
    )


def test_next_workout_endpoint_and_asset_bust_are_wired():
    """FIT-181 urgent follow-up: gym execution must not wait on the heavy
    dashboard endpoint, and phones must receive the new client bundle."""
    app_py = (ROOT / "app.py").read_text()
    auth_py = (ROOT / "auth.py").read_text()
    app_js = (ROOT / "static" / "js" / "app.js").read_text()
    template = (ROOT / "templates" / "index.html").read_text()
    sw = (ROOT / "static" / "js" / "sw.js").read_text()

    assert "@app.route('/api/next-workout')" in app_py
    assert "def _current_workout_training_recommendation():" in app_py
    assert "def _workout_recommendation_fingerprint():" in app_py
    assert "LAST_WORKOUT_RECOMMENDATION_FINGERPRINT != fingerprint" in app_py
    assert "\"day\": today_s" in app_py
    assert "\"oura\": {" in app_py
    assert "get_oura_daily_range(OURA_DB_FILE" in app_py
    assert "\"weather\": {" in app_py
    assert "\"open_wearables\": {" in app_py
    assert "_open_wearables_workout_inputs_live()" in app_py
    assert '"configured": _open_wearables_workout_inputs_live()' in app_py
    assert "def _open_wearables_recommendation_marker(refresh=False):" in app_py
    assert '"marker": _open_wearables_recommendation_marker()' in app_py
    assert "include_open_wearables_readiness=False" in app_py
    assert "_open_wearables_workout_inputs_live()\n        or not LAST_WORKOUT_RECOMMENDATION" not in app_py
    assert "\"apple_health\": {" in app_py
    assert "file_marker(_apple_health_sync_db_file())" in app_py
    assert "healthkit_samples_workout_*.json" in app_py
    assert (
        "training_recommendation=_current_workout_training_recommendation()" in app_py
        or (
            "training_recommendation = _current_workout_training_recommendation()" in app_py
            and "training_recommendation=training_recommendation" in app_py
        )
    )
    assert "api('/api/next-workout', { timeoutMs: DASHBOARD_FETCH_TIMEOUT_MS })" in app_js
    assert "app-loader.js?v=20260629-fit253-open-wearables-link" in template
    assert "app.js?v=20260629-fit253-open-wearables-link" in (ROOT / "static" / "js" / "app-loader.js").read_text()
    assert "fitness-dashboard-v20260629-fit253-open-wearables-link" in sw
    assert "const STATIC_ASSETS" not in sw
    assert "cache.addAll" not in sw
    assert "caches.keys()" in sw
    assert "keys.map(key => caches.delete(key))" in sw
    assert "client.navigate(client.url)" not in sw
    assert "navigator.serviceWorker.addEventListener('controllerchange'" in app_js
    assert "if (activeWorkoutHasProgress())" in app_js
    assert "Update ready after workout. Refresh when finished." in app_js
    assert "window.location.reload()" in app_js
    assert "body.error === 'reload_required'" in app_js
    assert "throw new Error('reload required after workout')" in app_js
    assert "reg.waiting.postMessage({ type: 'SKIP_WAITING' })" in app_js
    assert 'APP_SHELL_RELOAD_COOKIE = "fd_shell_reload"' in app_py
    assert 'APP_SHELL_RELOAD_VERSION = "20260525-fit181-controller-reload-r2"' in app_py
    assert "APP_SHELL_RELOAD_COOKIE_MAX_AGE_S = 365 * 24 * 60 * 60" in app_py
    assert '"reload_required"' in app_py
    assert "response.status_code = 401" in app_py
    assert "response.set_cookie(" in app_py
    assert 'request.cookies.get("session")' in app_py
    assert "current_user.is_authenticated" in auth_py
    assert 'request.args.get("next")' in auth_py
    assert 'next_page.startswith("//")' in auth_py
    assert 'fd_shell_reload=20260525-fit181-controller-reload-r2' in auth_py
    assert "@app.route('/gym-now')" in app_py
    assert "Cache-Control\": \"no-store\"" in app_py
    assert "\"Cache-Control\": \"no-store, max-age=0\"" in app_py
    assert "event.request.mode === 'navigate'" in sw
    assert "url.pathname.endsWith('.js')" in sw


def test_dashboard_shell_defers_heavy_app_bundle_until_after_load():
    """FIT-237: the dashboard document load must not wait on the 500 KB app
    bundle. A tiny loader may boot the full app after the browser load event,
    but index.html must not directly include app.js as a parser-blocking
    script."""
    template = (ROOT / "templates" / "index.html").read_text()
    loader = (ROOT / "static" / "js" / "app-loader.js").read_text()

    assert "app-loader.js?v=20260629-fit253-open-wearables-link" in template
    assert "<script src=\"/static/js/app.js" not in template
    assert "window.addEventListener('load', loadAppBundle, { once: true });" in loader
    assert "script.src = '/static/js/app.js?v=20260629-fit253-open-wearables-link';" in loader
    assert "script.async = true;" in loader


def test_next_workout_endpoint_does_not_fetch_open_wearables(monkeypatch):
    """FIT-181 review: the gym execution endpoint must stay fast even when
    Open Wearables is configured but unavailable."""
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "_missing_open_wearables_config", lambda: [])
    monkeypatch.setattr(
        module,
        "fetch_open_wearables_data",
        lambda: (_ for _ in ()).throw(AssertionError("Open Wearables fetch should not run")),
    )
    monkeypatch.setattr(module, "OPEN_WEARABLES_USER_ID", "test-user")
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", None)
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION_FINGERPRINT", None)
    monkeypatch.setattr(module, "OPEN_WEARABLES_WORKOUT_MARKER_CACHE", None)

    response = module.app.test_client().get("/api/next-workout")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["next_workout"]["exercises"]
    assert payload["next_workout"]["auth_scope"].startswith("user:")


def test_open_wearables_marker_tolerates_timezone_sleep_events():
    """FIT-181 review: marker caching must not break successful OW fetches
    when the bridge returns timezone-aware sleep timestamps."""
    module = importlib.import_module("app")
    payload = {
        "sleep": {
            "events": [
                {
                    "end_time": datetime.now(timezone.utc).isoformat(),
                    "duration_min": 480,
                    "avg_hr": 60,
                }
            ]
        },
        "workouts": {"events": []},
        "activity_summary": {"summaries": []},
    }

    marker = module._store_open_wearables_recommendation_marker(payload)

    assert marker["sleep"]["duration_min"] == 480
    assert marker["sleep"]["recent"] is True


def test_next_workout_caches_clear_after_plan_inputs_change():
    """FIT-181: the fast workout path must not keep serving stale plans
    after settings, equipment, swap, or adjust flows mutate the canonical
    recommendation."""
    app_py = (ROOT / "app.py").read_text()
    app_js = (ROOT / "static" / "js" / "app.js").read_text()

    settings_body = app_py.split("def settings():", 1)[1].split("@app.route('/api/settings/equipment'", 1)[0]
    equipment_body = app_py.split("def settings_equipment():", 1)[1].split("@app.route('/api/personal-vocab'", 1)[0]
    assert "global LAST_WORKOUT_RECOMMENDATION" in settings_body
    assert "LAST_WORKOUT_RECOMMENDATION = None" in settings_body
    assert "global LAST_WORKOUT_RECOMMENDATION" in equipment_body
    assert "LAST_WORKOUT_RECOMMENDATION = None" in equipment_body

    assert "state.settings = null; state.dashboard = null; state.nextWorkout = null;" in app_js
    assert (
        "state.dashboard = null;\n"
        "            state.nextWorkout = null;\n"
        "            state.reco = null;"
    ) in app_js
    assert (
        "state.dashboard.next_workout = resp.recommendation;\n"
        "            state.nextWorkout = resp.recommendation;"
    ) in app_js
    assert (
        "state.dashboard.next_workout = payload.recommendation;\n"
        "            state.nextWorkout = payload.recommendation;"
    ) in app_js
    for marker in [
        "def add_workout():",
        "def add_soreness():",
        "def add_cardio():",
        "def add_recovery():",
        "def sync_oura_sleep():",
    ]:
        body = app_py.split(marker, 1)[1].split("\n\n@app.route", 1)[0]
        assert "global LAST_WORKOUT_RECOMMENDATION" in body
        assert "LAST_WORKOUT_RECOMMENDATION = None" in body


def test_render_dashboard_resets_error_sentinels_and_maps_dashboard_to_reco():
    """FIT-128 persistence + mapping (FIT-129 refactor): renderDashboard
    must reset all three error sentinels at the top (so a recovered card
    doesn't stay stuck), and it must route a dashboard-endpoint rejection
    onto state.recoError via the settle helper because the AI Recommendation
    card chip covers BOTH dashboard and reco."""
    app_js = (ROOT / "static" / "js" / "app.js").read_text()
    marker = "async function renderDashboard()"
    body = app_js.split(marker, 1)[1].split("\n    }\n", 1)[0]

    # Sentinels reset at the top of each render.
    assert "state.ouraError = false;" in body, "renderDashboard must reset state.ouraError"
    assert "state.recoError = false;" in body, "renderDashboard must reset state.recoError"
    assert "state.ouraSleepError = false;" in body, "renderDashboard must reset state.ouraSleepError"

    # Dashboard's rejection branch routes onto recoError via settle (FIT-129
    # shape). getDashboard is the only fetcher that still throws on failure,
    # so it gets the rejection-handler form of .then; the others use
    # null-as-failure detection (covered by the gen-counter test below).
    # Whitespace-tolerant: the source uses vertical alignment in the four
    # .then chains, so match on the .then structure rather than literal text.
    import re
    dash_then = re.search(
        r"getDashboard\(\)\.then\(\s*\(\)\s*=>\s*settle\(true,\s*'recoError'\)\s*,"
        r"\s*\(\)\s*=>\s*settle\(false,\s*'recoError'\)\s*\)",
        body,
    )
    assert dash_then is not None, (
        "renderDashboard's getDashboard .then chain must route both branches "
        "onto the recoError sentinel via settle — success → settle(true, "
        "'recoError'), rejection → settle(false, 'recoError'). Both dashboard "
        "and reco endpoints feed the AI Recommendation card chip."
    )


def test_dashboard_fetchers_pass_30s_timeout_to_api():
    """FIT-128 AC explicitly requires the chip to surface when an endpoint
    'times out (>30s)'. The shared api() helper takes an opts.timeoutMs that
    wires through an AbortController; the four dashboard fetchers must pass
    that constant so a hung endpoint actually aborts instead of leaving the
    chip silent forever (the bug Codex flagged in round 1)."""
    app_js = (ROOT / "static" / "js" / "app.js").read_text()

    # The constant must exist and be 30 seconds.
    assert "const DASHBOARD_FETCH_TIMEOUT_MS = 30000" in app_js, (
        "DASHBOARD_FETCH_TIMEOUT_MS constant must be defined at 30000ms"
    )

    # api() must honor timeoutMs by wiring an AbortController.
    api_body = app_js.split("async function api(", 1)[1].split("\n    }\n", 1)[0]
    assert "AbortController" in api_body, (
        "api() must use AbortController to enforce timeoutMs — without it a "
        "hung endpoint sits forever and the retry chip never surfaces"
    )
    assert "controller.abort()" in api_body, (
        "api() must call controller.abort() on timeout fire"
    )

    # Each of the four dashboard fetchers must pass timeoutMs through.
    for fetcher in ("getDashboard", "getOuraStatus", "getOuraSleep", "getReco"):
        body = app_js.split(f"async function {fetcher}(", 1)[1].split("\n    }\n", 1)[0]
        assert "timeoutMs: DASHBOARD_FETCH_TIMEOUT_MS" in body, (
            f"{fetcher} must pass `timeoutMs: DASHBOARD_FETCH_TIMEOUT_MS` to "
            f"api() — otherwise a hung endpoint can never surface the chip"
        )


def test_retry_chips_announce_via_aria_live():
    """FIT-128 a11y: the chips appear asynchronously when a fetch fails, so
    screen-reader users need aria-live to hear them. Without the attribute
    the failure state is silent to assistive tech."""
    template = (ROOT / "templates" / "index.html").read_text()
    for chip_id in ("readiness-retry", "reco-retry", "insight-retry"):
        start = template.index(f'id="{chip_id}"')
        end = template.index(">", start)
        opening_tag = template[start:end + 1]
        assert 'aria-live="polite"' in opening_tag, (
            f"{chip_id} must carry aria-live=\"polite\" so screen readers "
            f"announce when the chip appears"
        )


def test_paint_retry_chip_helper_exists_and_is_called_per_chip():
    """FIT-128 (FIT-129 signature update): paintRetryChip helper must exist
    with the (elementId, isErrored, sentinelKey, retryFn) signature, and
    paintDashboardFromState must call it once per chip with the matching
    sentinel value, sentinel key string, and retry fn."""
    app_js = (ROOT / "static" / "js" / "app.js").read_text()

    # FIT-129: signature now includes sentinelKey so the click handler can
    # write `state[sentinelKey] = failed` authoritatively under the gen guard
    # (instead of relying on the fetcher's catch to flip the sentinel).
    assert "function paintRetryChip(elementId, isErrored, sentinelKey, retryFn)" in app_js, (
        "paintRetryChip signature must be (elementId, isErrored, sentinelKey, "
        "retryFn) — the sentinelKey lets the click handler write the sentinel "
        "authoritatively under the gen guard"
    )

    # The helper must hide the chip when no error and show it on error,
    # plus wire an onclick handler.
    helper = app_js.split("function paintRetryChip(", 1)[1].split("\n    }\n", 1)[0]
    assert "chip.hidden = !isErrored" in helper, (
        "paintRetryChip must set chip.hidden based on the error sentinel"
    )
    assert "chip.onclick = " in helper, (
        "paintRetryChip must wire chip.onclick to trigger the retry"
    )

    # paintDashboardFromState must invoke the helper for all three chips,
    # passing the matching sentinel-key string as the third argument.
    paint_body = app_js.split("function paintDashboardFromState()", 1)[1].split("\n    }\n", 1)[0]
    assert "paintRetryChip('readiness-retry', state.ouraError, 'ouraError'," in paint_body, (
        "readiness-retry chip must be wired to state.ouraError + 'ouraError' key"
    )
    assert "paintRetryChip('reco-retry', state.recoError, 'recoError'," in paint_body, (
        "reco-retry chip must be wired to state.recoError + 'recoError' key"
    )
    assert "paintRetryChip('insight-retry', state.ouraSleepError, 'ouraSleepError'," in paint_body, (
        "insight-retry chip must be wired to state.ouraSleepError + 'ouraSleepError' key"
    )


def test_dashboard_render_uses_generation_counters():
    """FIT-129: renderDashboard must capture BOTH a render-gen and a
    per-sentinel gen at the top, and the settle helper must bail on either
    gen mismatch BEFORE touching the sentinel — otherwise stale fetches
    (from an older render OR from before a retry click superseded them) can
    still flip the sentinel back on."""
    app_js = (ROOT / "static" / "js" / "app.js").read_text()

    # Module-scope counters.
    assert "let dashboardRenderGen = 0;" in app_js, (
        "dashboardRenderGen module-scope counter is missing"
    )
    assert "const dashboardSentinelGen = { ouraError: 0, recoError: 0, ouraSleepError: 0 };" in app_js, (
        "dashboardSentinelGen module-scope per-sentinel counter is missing — "
        "render-gen alone doesn't close the intra-render retry race"
    )

    body = app_js.split("async function renderDashboard()", 1)[1].split("\n    }\n", 1)[0]

    # Render-gen capture at top.
    assert "const gen = ++dashboardRenderGen;" in body, (
        "renderDashboard must capture `const gen = ++dashboardRenderGen;`"
    )

    # Per-sentinel gen snapshot at top — all three.
    for sentinel in ("ouraError", "recoError", "ouraSleepError"):
        assert f"++dashboardSentinelGen.{sentinel}" in body, (
            f"renderDashboard must capture sentinel gen for {sentinel} via "
            f"`++dashboardSentinelGen.{sentinel}` so retry on this chip "
            f"invalidates older same-render fetches for it"
        )

    # settle helper has BOTH guards.
    assert "if (gen !== dashboardRenderGen) return;" in body, (
        "settle helper must bail on render-gen mismatch"
    )
    assert "if (sentinelGens[sentinel] !== dashboardSentinelGen[sentinel]) return;" in body, (
        "settle helper must bail on per-sentinel-gen mismatch — closes the "
        "intra-render retry race where the older fetcher from the current "
        "render rejects after a retry on the same chip has succeeded"
    )

    # Ordering: both guards must precede any sentinel mutation inside settle.
    render_gen_idx = body.index("if (gen !== dashboardRenderGen) return;")
    sentinel_gen_idx = body.index("if (sentinelGens[sentinel] !== dashboardSentinelGen[sentinel]) return;")
    # The only sentinel write inside settle is `state[sentinel] = true;`.
    settle_write_idx = body.index("state[sentinel] = true;")
    assert render_gen_idx < settle_write_idx, (
        "render-gen guard must appear BEFORE `state[sentinel] = true;` so a "
        "stale settle never mutates"
    )
    assert sentinel_gen_idx < settle_write_idx, (
        "sentinel-gen guard must appear BEFORE `state[sentinel] = true;` so a "
        "stale settle never mutates"
    )


def test_paint_retry_chip_guards_on_both_generations():
    """FIT-129: paintRetryChip's click handler must capture the render-gen
    AND bump+capture the per-sentinel gen BEFORE retryFn() runs. The bump
    invalidates any older same-render in-flight fetch for the same sentinel
    (the intra-render retry race Codex flagged in round 2). Both gens are
    re-checked at the top of `finally` so a stale retry cannot re-enable the
    chip, write the sentinel, or repaint."""
    app_js = (ROOT / "static" / "js" / "app.js").read_text()

    helper = app_js.split("function paintRetryChip(", 1)[1].split("\n    }\n", 1)[0]

    # Capture render-gen at click time.
    assert "const clickGen = dashboardRenderGen;" in helper, (
        "paintRetryChip click handler must capture the render-gen at click time"
    )

    # Bump + capture per-sentinel gen — must use `++` (the bump) so older
    # same-render fetches for the same sentinel are invalidated. Capturing
    # without bumping wouldn't drop the older fetch's settle.
    assert "const clickSentinelGen = ++dashboardSentinelGen[sentinelKey];" in helper, (
        "click handler must `++dashboardSentinelGen[sentinelKey]` and capture "
        "it as clickSentinelGen BEFORE retryFn runs — the bump invalidates "
        "older same-render in-flight fetches for this sentinel"
    )

    # Ordering: the sentinel-gen bump must happen BEFORE retryFn is awaited,
    # otherwise an older in-flight fetch could land between retryFn start and
    # the bump and still touch the sentinel.
    bump_idx = helper.index("const clickSentinelGen = ++dashboardSentinelGen[sentinelKey];")
    try_idx = helper.index("try { await retryFn();")
    assert bump_idx < try_idx, (
        "sentinel-gen bump must occur BEFORE retryFn is awaited"
    )

    # Both bail checks in finally.
    assert "if (clickGen !== dashboardRenderGen) return;" in helper, (
        "click handler must bail in finally on render-gen mismatch"
    )
    assert "if (clickSentinelGen !== dashboardSentinelGen[sentinelKey]) return;" in helper, (
        "click handler must bail in finally on sentinel-gen mismatch"
    )

    # Ordering: both bails must precede the three mutations
    # (re-enable, sentinel write, repaint) in the finally block.
    render_bail_idx = helper.index("if (clickGen !== dashboardRenderGen) return;")
    sentinel_bail_idx = helper.index("if (clickSentinelGen !== dashboardSentinelGen[sentinelKey]) return;")
    sentinel_write_idx = helper.index("state[sentinelKey] = failed;")
    disable_idx = helper.index("chip.disabled = false;")
    repaint_idx = helper.index("paintDashboardFromState();")

    for label, idx in (
        ("state[sentinelKey] = failed;", sentinel_write_idx),
        ("chip.disabled = false;", disable_idx),
        ("paintDashboardFromState();", repaint_idx),
    ):
        assert render_bail_idx < idx, (
            f"render-gen bail must precede `{label}` so a stale retry cannot "
            f"mutate after a newer render has taken over"
        )
        assert sentinel_bail_idx < idx, (
            f"sentinel-gen bail must precede `{label}` so a stale retry cannot "
            f"mutate after another retry has superseded it"
        )
