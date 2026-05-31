from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_swap_recommend_entry_point_is_live_app_shell():
    html = (ROOT / "templates" / "index.html").read_text()

    assert 'id="swap-recommend-form"' in html
    assert 'id="swap-recommend-input"' in html
    assert 'id="swap-recommend-submit"' in html
    assert 'id="swap-recommend-status"' in html


def test_swap_recommend_client_uses_read_only_endpoint_before_commit():
    source = (ROOT / "static" / "js" / "app.js").read_text()

    assert "SWAP_RECOMMEND_CONFIDENCE_GATE = 0.75" in source
    assert "await getNextWorkout(true);" in source
    assert "api('/api/workout/swap/recommend'" in source
    assert "renderSwapRecommendModal(result)" in source
    assert "Confirm swap" in source
    assert "applySwap(selected.exercise_index, target, selected.name" in source
    assert "planFingerprint: result && result.plan_fingerprint" in source
    assert "body.plan_fingerprint = opts.planFingerprint" in source


def test_swap_recommend_low_confidence_requires_explicit_pick():
    source = (ROOT / "static" / "js" / "app.js").read_text()

    assert "const highConfidence = result" in source
    assert "let selected = (highConfidence || degraded) ? (result && result.slot) : null;" in source
    assert 'id="swap-recommend-confirm" class="btn btn-primary btn-sm"${selected ? \'\' : \' disabled\'}' in source
    assert "confirmBtn.disabled = false;" in source


def test_swap_recommend_degraded_path_shows_banner():
    source = (ROOT / "static" / "js" / "app.js").read_text()

    assert "const degraded = result && result.source === 'deterministic';" in source
    assert "AI unavailable; using the best matching slot." in source
