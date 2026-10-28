from pathlib import Path

import pytest

from js_runtime import run_app_js


APP_LOADER_JS = Path("static/js/app-loader.js").read_text()
APP_SW_JS = Path("static/js/sw.js").read_text()
APP_HTML = Path("templates/index.html").read_text()

ASSET_VERSION = "20260713-fit233-adaptation-polling"


def test_oura_sync_success_renders_status_range_count_and_latest_days():
    elements = run_app_js(
        ["renderOuraSyncResult"],
        """
sandbox.elements['oura-detail-sync-row'] = { hidden: true };
sandbox.elements['oura-detail-sync-result'] = { textContent: 'stale' };
e.renderOuraSyncResult({
  status: 'success',
  synced_from: '2026-07-01',
  synced_through: '2026-07-13',
  latest_records: 2,
  latest_days: ['2026-07-11', '2026-07-10'],
});
process.stdout.write(JSON.stringify(sandbox.elements));
""",
    )

    assert elements["oura-detail-sync-row"]["hidden"] is False
    assert elements["oura-detail-sync-result"]["textContent"] == (
        "Success · 2026-07-01 → 2026-07-13 · 2 latest saved records · "
        "saved days 2026-07-11, 2026-07-10"
    )


def test_fit270_asset_versions_are_coordinated():
    assert f"app-loader.js?v={ASSET_VERSION}" in APP_HTML
    assert f"app.js?v={ASSET_VERSION}" in APP_LOADER_JS
    assert f"fitness-dashboard-v{ASSET_VERSION}" in APP_SW_JS


@pytest.mark.parametrize("code", ["missing_oura_token", "oura_api_error"])
def test_oura_sync_structured_error_is_durable_in_detail_row(code):
    elements = run_app_js(
        ["renderOuraSyncResult"],
        f"""
sandbox.elements['oura-detail-sync-row'] = {{ hidden: true }};
sandbox.elements['oura-detail-sync-result'] = {{ textContent: 'stale' }};
const err = new Error('truncated response');
err.apiErrorCode = {code!r};
err.apiErrorMessage = 'Action required';
e.renderOuraSyncResult(null, err);
process.stdout.write(JSON.stringify(sandbox.elements));
""",
    )

    assert elements["oura-detail-sync-row"]["hidden"] is False
    assert elements["oura-detail-sync-result"]["textContent"] == f"{code} · Action required"
