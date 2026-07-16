import json
import shutil
import subprocess
from pathlib import Path

import pytest


APP_JS = Path("static/js/app.js").read_text()


def _source_viewer_helpers() -> str:
    start = APP_JS.index("function openMealV2SourceViewer")
    end = APP_JS.index("function mealV2MockEnabled", start)
    return APP_JS[start:end]


def _run_source_viewer_contract() -> dict:
    if not shutil.which("node"):
        pytest.skip("FIT-278 UI contract requires Node.js")
    script = f"""
const vm = require('node:vm');
const appended = [];
const sandbox = {{
  URL,
  window: {{ location: {{ origin: 'http://127.0.0.1:5000', protocol: 'http:' }} }},
  document: {{
    body: {{ appendChild: (node) => appended.push(node) }},
    createElement: () => ({{
      className: '',
      innerHTML: '',
      setAttribute: () => {{}},
      querySelector: () => null,
      querySelectorAll: () => [],
      remove: () => {{}},
    }}),
  }},
  escapeHtml: (value) => String(value),
  focusOpenModal: () => {{}},
  closeModal: () => {{}},
}};
vm.createContext(sandbox);
vm.runInContext({json.dumps(_source_viewer_helpers())}, sandbox);

const off = 'https://world.openfoodfacts.org/product/500032837010';
const results = {{
  off: vm.runInContext(`sanitizeMealV2SourceLink(${{JSON.stringify(off)}})`, sandbox),
  sameOrigin: vm.runInContext("sanitizeMealV2SourceLink('/api/sources/usda/rice')", sandbox),
  unrelated: vm.runInContext("sanitizeMealV2SourceLink('https://example.com/product/1')", sandbox),
  protocolRelative: vm.runInContext("sanitizeMealV2SourceLink('//world.openfoodfacts.org/product/1')", sandbox),
  insecureOff: vm.runInContext("sanitizeMealV2SourceLink('http://world.openfoodfacts.org/product/1')", sandbox),
  credentialedOff: vm.runInContext("sanitizeMealV2SourceLink('https://user@world.openfoodfacts.org/product/1')", sandbox),
}};
vm.runInContext(`openMealV2SourceViewer(${{JSON.stringify(off)}}, 'Open Food Facts')`, sandbox);
const countAfterOff = appended.length;
vm.runInContext("openMealV2SourceViewer('https://example.com/product/1', 'Untrusted')", sandbox);
results.countAfterOff = countAfterOff;
results.countAfterUntrusted = appended.length;
results.modalHtml = appended[0] ? appended[0].innerHTML : '';
process.stdout.write(JSON.stringify(results));
"""
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_open_food_facts_source_opens_in_sandboxed_viewer_only():
    result = _run_source_viewer_contract()

    off = "https://world.openfoodfacts.org/product/500032837010"
    assert result["off"] == off
    assert result["sameOrigin"] == "/api/sources/usda/rice"
    assert result["unrelated"] == ""
    assert result["protocolRelative"] == ""
    assert result["insecureOff"] == ""
    assert result["credentialedOff"] == ""
    assert result["countAfterOff"] == 1
    assert result["countAfterUntrusted"] == 1
    assert f'src="{off}"' in result["modalHtml"]
    assert 'sandbox="allow-same-origin"' in result["modalHtml"]
