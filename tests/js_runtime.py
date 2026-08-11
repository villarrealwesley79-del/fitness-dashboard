"""Execute selected internal app.js functions in a minimal Node sandbox."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def run_app_js(exports: list[str], scenario: str, *, mocks: list[str] | None = None) -> object:
    """Export internal IIFE bindings, run a scenario, and return its JSON output."""
    if not shutil.which("node"):
        pytest.skip("frontend runtime contract requires Node.js")
    invalid_exports = [name for name in exports if not isinstance(name, str) or not name.isidentifier()]
    if invalid_exports:
        raise ValueError(
            "run_app_js exports must be binding identifiers, not expressions: "
            f"{invalid_exports!r}"
        )
    invalid_mocks = [
        name for name in (mocks or [])
        if not isinstance(name, str) or not name.isidentifier()
    ]
    if invalid_mocks:
        raise ValueError(
            "run_app_js mocks must be binding identifiers, not expressions: "
            f"{invalid_mocks!r}"
        )
    reflection_tokens = (".toString", ".toLocaleString", "Function.prototype")
    if any(token in scenario for token in reflection_tokens):
        raise ValueError("run_app_js scenarios may not reflect function source")

    export_map = ", ".join(exports)
    mock_setters = ", ".join(f"{name}: (value) => {{ {name} = value; }}" for name in (mocks or []))
    app_js = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    source = app_js.rsplit("})();", 1)[0] + (
        f"globalThis.__fitExports = {{ {export_map} }};\n"
        f"globalThis.__fitSet = {{ {mock_setters} }};\n}})();"
    )
    scenario_program = f"""
(async () => {{
  const sandbox = globalThis;
  globalThis.__fitScenarioOutput = null;
  globalThis.process = Object.freeze({{
    stdout: Object.freeze({{
      write(value) {{ globalThis.__fitScenarioOutput = String(value); }},
    }}),
  }});
  const rejectFunctionReflection = () => {{
    throw new Error('Source reflection is not allowed for app functions');
  }};
  Object.defineProperty(Function.prototype, 'toString', {{
    value: rejectFunctionReflection, writable: false, configurable: false,
  }});
  Object.defineProperty(Function.prototype, 'toLocaleString', {{
    value: rejectFunctionReflection, writable: false, configurable: false,
  }});
  // Exports stay callable, but their implementation text is not behavioral
  // evidence. The wrappers and scenario are created in this restricted VM,
  // so neither prototypes nor Function constructors can reach Node modules.
  const safeApply = Reflect.apply;
  const e = (() => {{
    const rawExports = globalThis.__fitExports;
    const wrapped = Object.fromEntries(Object.entries(rawExports).map(([name, value]) => [
      name,
      typeof value === 'function'
        ? (() => {{
            const callable = function (...args) {{
              return safeApply(value, this, args);
            }};
            const rejectReflection = () => {{
              throw new Error(`Source reflection is not allowed for exported function ${{name}}`);
            }};
            Object.defineProperty(callable, 'toString', {{ value: rejectReflection }});
            Object.defineProperty(callable, 'toLocaleString', {{ value: rejectReflection }});
            return callable;
          }})()
        : value,
    ]));
    delete globalThis.__fitExports;
    delete globalThis.__aicoach;
    return wrapped;
  }})();
  {scenario}
}})()
"""
    script = f"""
const vm = require('node:vm');
const rejectHostFunctionReflection = () => {{
  throw new Error('Source reflection is not allowed for app functions');
}};
Object.defineProperty(Function.prototype, 'toString', {{
  value: rejectHostFunctionReflection, writable: false, configurable: false,
}});
Object.defineProperty(Function.prototype, 'toLocaleString', {{
  value: rejectHostFunctionReflection, writable: false, configurable: false,
}});
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
const sandbox = (() => {{
  const appSource = {json.dumps(source)};
  const context = {{
    console, document, elements, localStorage: storage, sessionStorage: storage,
    navigator: {{ onLine: true }}, location: {{ hash: '', search: '', pathname: '/' }},
    URL, URLSearchParams, Blob, FormData, Response, Request, Headers,
    fetch: async () => new Response('{{}}', {{ status: 200 }}),
    setTimeout, clearTimeout, setInterval, clearInterval, queueMicrotask, structuredClone,
    requestAnimationFrame: noop, cancelAnimationFrame: noop,
    confirm: () => true, crypto: globalThis.crypto,
  }};
  context.window = context;
  context.globalThis = context;
  vm.runInNewContext(appSource, context, {{
    filename: 'app.js',
    contextCodeGeneration: {{ strings: false, wasm: false }},
  }});
  return context;
}})();
(async () => {{
  await vm.runInNewContext({json.dumps(scenario_program)}, sandbox, {{
    filename: 'scenario.js',
    contextCodeGeneration: {{ strings: false, wasm: false }},
  }});
  if (sandbox.__fitScenarioOutput == null) throw new Error('scenario did not write JSON output');
  process.stdout.write(sandbox.__fitScenarioOutput);
}})().catch((error) => {{ console.error(error); process.exit(1); }});
"""
    result = subprocess.run(
        ["node", "--permission", "-"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        input=script,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)
