import json
import shutil
import subprocess
from pathlib import Path

import pytest


APP_JS = Path("static/js/app.js").read_text()
APP_LOADER_JS = Path("static/js/app-loader.js").read_text()
APP_SW_JS = Path("static/js/sw.js").read_text()
APP_HTML = Path("templates/index.html").read_text()

FIT270_ASSET_VERSION = "20260713-fit270-oura-detail"


def _sync_detail_helpers() -> str:
    start = APP_JS.index("function _ouraSyncErrorDetails")
    end = APP_JS.index("async function syncOura", start)
    return APP_JS[start:end]


def _render_in_node(expression: str) -> dict:
    if not shutil.which("node"):
        pytest.skip("FIT-270 UI contract requires Node.js")
    script = f"""
const vm = require('node:vm');
const elements = {{
  'oura-detail-sync-row': {{ hidden: true }},
  'oura-detail-sync-result': {{ textContent: 'stale' }},
}};
const sandbox = {{
  elements,
  $: (id) => elements[id] || null,
}};
vm.createContext(sandbox);
vm.runInContext({json.dumps(_sync_detail_helpers())}, sandbox);
vm.runInContext({json.dumps(expression)}, sandbox);
process.stdout.write(JSON.stringify(elements));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_oura_sync_success_renders_status_range_count_and_latest_days():
    elements = _render_in_node(
        "renderOuraSyncResult({status: 'success', synced_from: '2026-07-01', "
        "synced_through: '2026-07-13', latest_records: 2, "
        "latest_days: ['2026-07-11', '2026-07-10']});"
    )

    assert elements["oura-detail-sync-row"]["hidden"] is False
    assert elements["oura-detail-sync-result"]["textContent"] == (
        "Success · 2026-07-01 → 2026-07-13 · 2 latest saved records · "
        "saved days 2026-07-11, 2026-07-10"
    )


def test_fit270_asset_versions_are_coordinated():
    assert f"app-loader.js?v={FIT270_ASSET_VERSION}" in APP_HTML
    assert f"app.js?v={FIT270_ASSET_VERSION}" in APP_LOADER_JS
    assert f"fitness-dashboard-v{FIT270_ASSET_VERSION}" in APP_SW_JS


@pytest.mark.parametrize("code", ["missing_oura_token", "oura_api_error"])
def test_oura_sync_structured_error_is_durable_in_detail_row(code):
    elements = _render_in_node(
        f"const err = new Error('truncated response'); err.apiErrorCode = {json.dumps(code)}; "
        "err.apiErrorMessage = 'Action required'; renderOuraSyncResult(null, err);"
    )

    assert elements["oura-detail-sync-row"]["hidden"] is False
    assert elements["oura-detail-sync-result"]["textContent"] == f"{code} · Action required"
