import json
import shutil
import subprocess
from pathlib import Path

import pytest


APP_JS = Path("static/js/app.js").read_text()


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
        "latest_records: 2, latest_days: ['2026-07-11', '2026-07-10']});"
    )

    assert elements["oura-detail-sync-row"]["hidden"] is False
    assert elements["oura-detail-sync-result"]["textContent"] == (
        "Success · 2026-07-01 → 2026-07-11 · 2 recent records · "
        "latest 2026-07-11, 2026-07-10"
    )


@pytest.mark.parametrize("code", ["missing_oura_token", "oura_api_error"])
def test_oura_sync_structured_error_is_durable_in_detail_row(code):
    elements = _render_in_node(
        f"const err = new Error('truncated response'); err.apiErrorCode = {json.dumps(code)}; "
        "err.apiErrorMessage = 'Action required'; renderOuraSyncResult(null, err);"
    )

    assert elements["oura-detail-sync-row"]["hidden"] is False
    assert elements["oura-detail-sync-result"]["textContent"] == f"{code} · Action required"
