from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "js" / "app.js").read_text()


def test_ai_coach_headline_runtime_states():
    outputs = _run_headline_fixtures_in_node()

    ready = outputs["ready"]
    assert ready["stateText"] == "Ready"
    assert ready["hostText"] == "ASUS GX10"
    assert ready["chipText"] == "Online"
    assert ready["chipCls"] == "state-chip ok"
    assert ready["detail"] == "Serving from ASUS GX10."

    fallback = outputs["fallback_active"]
    assert fallback["stateText"] == "Fallback active"
    assert fallback["hostText"] == "Mac Studio"
    assert fallback["chipText"] == "Fallback"
    assert fallback["chipCls"] == "state-chip warn"
    assert fallback["detail"] == "ASUS GX10 unreachable — Mac Studio is serving traffic."

    offline = outputs["offline"]
    assert offline["stateText"] == "AI offline"
    assert offline["hostText"] == "ASUS GX10 & Mac Studio unavailable"
    assert offline["chipText"] == "Offline"
    assert offline["chipCls"] == "state-chip stale"

    unloaded_fallback = outputs["unloaded_fallback"]
    assert unloaded_fallback["stateText"] == "AI offline"
    assert unloaded_fallback["hostText"] == "ASUS GX10 & Mac Studio unavailable"
    assert unloaded_fallback["chipText"] == "Offline"
    assert unloaded_fallback["chipCls"] == "state-chip stale"
    assert unloaded_fallback["stateText"] != "Fallback active"


def _run_headline_fixtures_in_node() -> dict:
    if not shutil.which("node"):
        pytest.skip("FIT-178 runtime regression requires node to execute app.js")

    helper_source = _slice_between(
        APP_JS,
        "const AI_PRIMARY_HOST_DEFAULT = 'ASUS GX10';",
        "function _aiCheckLabel",
    )
    node_script = f"""
const vm = require('node:vm');
const helperSource = {json.dumps(helper_source)};
const sandbox = {{ module: {{ exports: {{}} }} }};
vm.runInNewContext(`
const AI_PRIMARY_HOST_DEFAULT = 'ASUS GX10';
${{helperSource}}
module.exports = {{ _aiCoachHeadlineFromHealth }};
`, sandbox);
const headlineFromHealth = sandbox.module.exports._aiCoachHeadlineFromHealth;
const outputs = {{
  ready: headlineFromHealth({{
    active_role: 'primary',
    primary: {{ name: 'primary', reachable: true, model_loaded: true }},
    fallback: {{ name: 'fallback', reachable: true, model_loaded: true }},
  }}),
  fallback_active: headlineFromHealth({{
    active_role: 'fallback',
    primary: {{ name: 'primary', reachable: false, model_loaded: false }},
    fallback: {{ name: 'fallback', reachable: true, model_loaded: true }},
  }}),
  offline: headlineFromHealth({{
    active_role: null,
    primary: {{ name: 'primary', reachable: false, model_loaded: false }},
    fallback: {{ name: 'fallback', reachable: false, model_loaded: false }},
  }}),
  unloaded_fallback: headlineFromHealth({{
    active_role: 'fallback',
    primary: {{ name: 'primary', reachable: false, model_loaded: false }},
    fallback: {{ name: 'fallback', reachable: true, model_loaded: false }},
  }}),
}};
process.stdout.write(JSON.stringify(outputs));
"""
    result = subprocess.run(
        ["node", "-e", node_script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _slice_between(source: str, start_marker: str, end_marker: str) -> str:
    assert start_marker in source, f"{start_marker!r} missing from app.js"
    assert end_marker in source, f"{end_marker!r} missing from app.js"
    return source.split(start_marker, 1)[1].split(end_marker, 1)[0]
