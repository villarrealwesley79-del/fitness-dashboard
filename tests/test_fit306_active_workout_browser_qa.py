import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs" / "qa" / "fit-306-active-workout" / "states.json"
RUNBOOK = ROOT / "docs" / "qa" / "fit-306-active-workout" / "README.md"
APP_JS = (ROOT / "static" / "js" / "app.js").read_text()
TEMPLATE = (ROOT / "templates" / "index.html").read_text()
PLAYWRIGHT_CLI_VERSION = "0.1.17"


REQUIRED_STATES = {
    "recovered-draft", "dirty-close", "swap", "adjust", "delete-remove",
    "set-completion", "cardio-completion", "save-error", "queued",
    "conflicted", "saved", "empty", "blocked", "warning",
}


def _playwright_cli() -> list[str]:
    npx = shutil.which("npx")
    assert npx, "FIT-306 browser QA requires npx"
    return [npx, "--yes", "--package", f"@playwright/cli@{PLAYWRIGHT_CLI_VERSION}", "playwright-cli"]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_browser_qa_matrix_covers_every_required_modal_state():
    matrix = json.loads(MATRIX.read_text())
    assert {case["id"] for case in matrix["states"]} == REQUIRED_STATES
    assert matrix["viewport"] == {"width": 390, "height": 844}
    assert all(case["selector"] and case["expected_text"] for case in matrix["states"])


def test_qa_hook_is_query_gated_and_uses_shipped_renderers():
    assert "get('fit306_qa') === '1'" in APP_JS
    assert "window.__aicoach.fit306Qa" in APP_JS
    assert "renderActiveWorkout();" in APP_JS
    for element_id in ("modal-active", "active-workout-body", "active-workout-status", "modal-workout-saved"):
        assert f'id="{element_id}"' in TEMPLATE


def test_browser_executes_production_active_workout_states(tmp_path):
    cli = _playwright_cli()
    port = _free_port()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    env = {
        **os.environ,
        "DATA_DIR": str(data_dir),
        "SECRET_KEY": "fit306-browser-qa-secret",
        "SESSION_COOKIE_SECURE": "false",
    }
    server_code = (
        "import app as module; "
        "module.app.config['LOGIN_DISABLED'] = True; "
        f"module.app.run(host='127.0.0.1', port={port}, debug=False, use_reloader=False)"
    )
    server = subprocess.Popen(
        [sys.executable, "-c", server_code],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/login", timeout=1).read()
            break
        except Exception:
            if server.poll() is not None:
                raise AssertionError(server.stdout.read())
            time.sleep(0.1)
    else:
        server.terminate()
        raise AssertionError("FIT-306 test server did not start")

    session = f"fit306-{os.getpid()}"

    def run(*args: str) -> str:
        result = subprocess.run(
            [*cli, f"-s={session}", *args], cwd=ROOT, text=True,
            capture_output=True, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip()

    def run_global(*args: str) -> str:
        result = subprocess.run(
            [*cli, *args], cwd=ROOT, text=True, capture_output=True, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip()

    fixture = {
        "id": "fit306-workout",
        "focus": "Upper",
        "dirty": False,
        "exercises": [{
            "name": "Chest Press", "muscle_group": "Chest", "target_sets": 3,
            "target_reps": 10, "target_weight": 90,
            "logged_sets": [
                {"weight": 90, "reps": 10, "done": False, "notes": ""},
                {"weight": 90, "reps": 10, "done": False, "notes": ""},
                {"weight": 90, "reps": 10, "done": False, "notes": ""},
            ],
        }],
        "cardio": {
            "recommendation": {"type": "Bike", "duration_minutes": 15},
            "completed": False, "activity_type": "Bike", "duration_minutes": 15, "notes": "",
        },
    }
    completion_responses = {
        "save-error": (200, {"sync_status": "rejected"}),
        "queued": (200, {"sync_status": "pending"}),
        "conflicted": (200, {"sync_status": "conflicted"}),
        "blocked": (200, {"sync_status": "auth_required"}),
        "saved": (200, {"sync_status": "inserted"}),
    }
    try:
        run_global("install-browser", "chromium")
        run("open", "about:blank")
        run("route", "**/api/oura/sleep-summary", "--body", "{}", "--content-type", "application/json")
        run("route", "**/api/oura/status", "--body", '{"connected":false}', "--content-type", "application/json")
        run("resize", "390", "844")
        for case in json.loads(MATRIX.read_text())["states"]:
            run("goto", f"http://127.0.0.1:{port}/?fit306_qa=1")
            assert run("eval", "() => Boolean(window.__aicoach?.fit306Qa)", "--raw") == "true"
            state_fixture = json.loads(json.dumps(fixture))
            if case["id"] == "recovered-draft":
                state_fixture["saveState"] = {"message": case["expected_text"], "variant": "warn"}
            if case["id"] == "dirty-close":
                state_fixture["dirty"] = True
            if case["id"] == "empty":
                state_fixture["exercises"] = []
                state_fixture["cardio"] = None
            if case["id"] == "warning":
                for logged_set in state_fixture["exercises"][0]["logged_sets"]:
                    logged_set["weight"] = ""
                    logged_set["reps"] = ""
            run("eval", f"() => window.__aicoach.fit306Qa.showWorkout({json.dumps(state_fixture)})", "--raw")

            if case["id"] == "dirty-close":
                run("eval", "() => { window.confirm = () => false; document.querySelector('#modal-active .modal-close').click(); }", "--raw")
            elif case["id"] == "swap":
                run("click", ".active-swap-btn")
            elif case["id"] == "adjust":
                run("click", "#btn-adjust-plan-active")
            elif case["id"] == "delete-remove":
                run("click", ".active-remove-btn")
            elif case["id"] == "set-completion":
                run("check", '.set-row[data-set="0"] input[data-field="done"]')
            elif case["id"] == "cardio-completion":
                run("check", '[data-cardio-field="completed"]')
            elif case["id"] in completion_responses:
                status_code, body = completion_responses[case["id"]]
                run("unroute", "**/api/complete-workout")
                run("route", "**/api/complete-workout", "--status", str(status_code), "--body", json.dumps(body), "--content-type", "application/json")
                run("click", "#btn-complete-workout")
            elif case["id"] == "warning":
                run("click", "#btn-complete-workout")

            visible_modal = {
                "swap": "#modal-swap",
                "adjust": "#modal-adjust",
                "queued": "#modal-workout-saved",
                "saved": "#modal-workout-saved",
            }.get(case["id"], "#modal-active")
            run("run-code", f"async (page) => {{ await page.locator('{visible_modal}').waitFor({{state:'visible'}}); }}")
            run("run-code", f"async (page) => {{ await page.locator({json.dumps(case['selector'])}).filter({{hasText:{json.dumps(case['expected_text'])}}}).waitFor({{state:'visible'}}); }}")
            actual = run("eval", "(el) => el.textContent.trim()", case["selector"], "--raw")
            assert case["expected_text"] in actual
            layout_js = f"""() => {{
              const visible = document.querySelector({json.dumps(visible_modal)}); const sheet = visible.querySelector('.modal-sheet'); const controls = visible.querySelector('.modal-foot') || sheet; const nav = document.querySelector('.tab-bar');
              const cr = controls.getBoundingClientRect(); const nr = nav.getBoundingClientRect(); const mr = sheet.getBoundingClientRect(); const modalZ = Number(getComputedStyle(visible).zIndex); const navZ = Number(getComputedStyle(nav).zIndex);
              return {{ visibleModal: visible.id, controlsNotObscured: cr.bottom <= nr.top || modalZ > navZ, modalInViewport: mr.top >= 0 && mr.bottom <= innerHeight, modalZ, navZ }};
            }}"""
            evidence = json.loads(run("eval", layout_js, "--raw"))
            assert evidence["controlsNotObscured"] is True
            assert evidence["modalInViewport"] is True
            run("screenshot", "--filename", str(tmp_path / f"fit-306-{case['id']}.png"))

        console = run("console", "warning")
        assert "Errors: 0, Warnings: 0" in console
        assert len(list(tmp_path.glob("fit-306-*.png"))) == len(REQUIRED_STATES)
    finally:
        subprocess.run([*cli, f"-s={session}", "close"], cwd=ROOT, capture_output=True, check=False)
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


def test_runbook_documents_production_browser_scope_and_limits():
    notes = RUNBOOK.read_text()
    assert "production template" in notes
    assert "production `static/js/app.js`" in notes
    assert "390x844" in notes
    assert "does not use production data" in notes
