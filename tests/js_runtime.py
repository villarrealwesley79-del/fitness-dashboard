"""Execute selected internal app.js functions in a minimal Node sandbox."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")


def run_app_js(exports: list[str], scenario: str, *, mocks: list[str] | None = None) -> object:
    """Export internal IIFE bindings, run a scenario, and return its JSON output."""
    if not shutil.which("node"):
        pytest.skip("frontend runtime contract requires Node.js")

    export_map = ", ".join(exports)
    mock_setters = ", ".join(f"{name}: (value) => {{ {name} = value; }}" for name in (mocks or []))
    source = APP_JS.rsplit("})();", 1)[0] + (
        f"globalThis.__fitExports = {{ {export_map} }};\n"
        f"globalThis.__fitSet = {{ {mock_setters} }};\n}})();"
    )
    script = f"""
const vm = require('node:vm');
const source = {json.dumps(source)};
const noop = () => {{}};
const elements = {{}};
const document = {{
  readyState: 'loading',
  visibilityState: 'visible',
  addEventListener: noop,
  getElementById: (id) => elements[id] || null,
  querySelectorAll: () => [],
  querySelector: () => null,
  body: {{ appendChild: noop }},
  documentElement: {{ dataset: {{}} }},
}};
const storage = {{ getItem: () => null, setItem: noop, removeItem: noop }};
const sandbox = {{
  console, document, elements, localStorage: storage, sessionStorage: storage,
  navigator: {{ onLine: true }}, location: {{ hash: '', search: '', pathname: '/' }},
  URL, URLSearchParams, Blob, FormData, Response, Request, Headers,
  fetch: async () => new Response('{{}}', {{ status: 200 }}),
  setTimeout, clearTimeout, setInterval, clearInterval,
  requestAnimationFrame: noop, cancelAnimationFrame: noop,
  confirm: () => true, crypto: globalThis.crypto,
}};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.runInNewContext(source, sandbox, {{ filename: 'app.js' }});
(async () => {{
  const e = sandbox.__fitExports;
  {scenario}
}})().catch((error) => {{ console.error(error); process.exit(1); }});
"""
    result = subprocess.run(
        ["node", "-"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        input=script,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)
