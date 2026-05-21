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


def test_fetchers_set_per_endpoint_error_sentinels():
    """FIT-128: getOuraStatus / getOuraSleep / getReco must each set their
    corresponding `state.<x>Error` sentinel on rejection so the chips know
    when to surface. Without this the chips can never appear because the
    existing try/catch swallows the rejection silently (a regression of
    FIT-125 AC 3)."""
    app_js = (ROOT / "static" / "js" / "app.js").read_text()

    for fetcher, sentinel in [
        ("getOuraStatus", "state.ouraError"),
        ("getOuraSleep", "state.ouraSleepError"),
        ("getReco", "state.recoError"),
    ]:
        marker = f"async function {fetcher}("
        assert marker in app_js, f"{fetcher} not found"
        body = app_js.split(marker, 1)[1].split("\n    }\n", 1)[0]
        # The catch branch must set the sentinel to true.
        assert f"{sentinel} = true" in body, (
            f"{fetcher} catch must set `{sentinel} = true` so the retry chip "
            f"can surface"
        )
        # The success branch must clear the sentinel so a recovered fetch
        # doesn't leave a stale chip behind on the next render.
        assert f"{sentinel} = false" in body, (
            f"{fetcher} success branch must set `{sentinel} = false` so a "
            f"recovered fetch clears the chip"
        )


def test_render_dashboard_resets_error_sentinels_and_maps_dashboard_to_reco():
    """FIT-128 persistence + mapping: renderDashboard must reset all three
    error sentinels at the top (so a recovered card doesn't stay stuck), and
    it must map a dashboard-endpoint rejection onto state.recoError because
    the AI Recommendation card chip covers BOTH dashboard and reco."""
    app_js = (ROOT / "static" / "js" / "app.js").read_text()
    marker = "async function renderDashboard()"
    body = app_js.split(marker, 1)[1].split("\n    }\n", 1)[0]

    # Sentinels reset at the top of each render.
    assert "state.ouraError = false;" in body, "renderDashboard must reset state.ouraError"
    assert "state.recoError = false;" in body, "renderDashboard must reset state.recoError"
    assert "state.ouraSleepError = false;" in body, "renderDashboard must reset state.ouraSleepError"

    # Dashboard rejection maps to state.recoError so the chip fires for either
    # dashboard or reco failure (per the FIT-128 endpoint-to-chip mapping).
    assert "getDashboard().then(repaint, () => { state.recoError = true; repaint(); })" in body, (
        "renderDashboard's dashboard .then-rejection must set state.recoError "
        "= true — this is the only fetcher that doesn't swallow its own "
        "rejection, so the chip mapping has to be wired up here"
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
    """FIT-128: paintRetryChip helper must exist, and paintDashboardFromState
    must call it once per chip with the matching sentinel + retry fn."""
    app_js = (ROOT / "static" / "js" / "app.js").read_text()

    assert "function paintRetryChip(" in app_js, "paintRetryChip helper missing"

    # The helper must hide the chip when no error and show it on error,
    # plus wire an onclick handler.
    helper = app_js.split("function paintRetryChip(", 1)[1].split("\n    }\n", 1)[0]
    assert "chip.hidden = !isErrored" in helper, (
        "paintRetryChip must set chip.hidden based on the error sentinel"
    )
    assert "chip.onclick = " in helper, (
        "paintRetryChip must wire chip.onclick to trigger the retry"
    )

    # paintDashboardFromState must invoke the helper for all three chips.
    paint_body = app_js.split("function paintDashboardFromState()", 1)[1].split("\n    }\n", 1)[0]
    assert "paintRetryChip('readiness-retry', state.ouraError," in paint_body, (
        "readiness-retry chip must be wired to state.ouraError"
    )
    assert "paintRetryChip('reco-retry', state.recoError," in paint_body, (
        "reco-retry chip must be wired to state.recoError"
    )
    assert "paintRetryChip('insight-retry', state.ouraSleepError," in paint_body, (
        "insight-retry chip must be wired to state.ouraSleepError"
    )
