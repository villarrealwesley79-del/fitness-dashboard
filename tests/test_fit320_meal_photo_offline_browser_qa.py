import base64
import hashlib
import json
import os
import signal
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs" / "qa" / "fit-320-meal-photo-offline" / "states.json"
RUNBOOK = ROOT / "docs" / "qa" / "fit-320-meal-photo-offline" / "README.md"
PLAYWRIGHT_CLI_VERSION = "0.1.17"
PLAYWRIGHT_COMMAND_TIMEOUT_SECONDS = 180
PLAYWRIGHT_INSTALL_TIMEOUT_SECONDS = 300
LIVE_BROWSER_ENV = "FIT320_LIVE_BROWSER_QA"

REQUIRED_STATES = {
    "four-photo-cap",
    "oversize-rejection",
    "photo-removal",
    "offline-persistence",
    "reconnect-replay",
    "discard-blob-deletion",
}


def _playwright_cli() -> list[str]:
    npx = shutil.which("npx")
    assert npx, "FIT-320 browser QA requires npx"
    return [
        npx,
        "--yes",
        "--package",
        f"@playwright/cli@{PLAYWRIGHT_CLI_VERSION}",
        "playwright-cli",
    ]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_browser_qa_matrix_covers_required_mobile_states():
    matrix = json.loads(MATRIX.read_text())
    assert matrix["viewport"] == {"width": 390, "height": 844}
    assert {case["id"] for case in matrix["states"]} == REQUIRED_STATES
    assert all(case["selector"] and case["expected_text"] for case in matrix["states"])


@pytest.mark.allow_net
@pytest.mark.skipif(
    os.environ.get(LIVE_BROWSER_ENV) != "1",
    reason=f"set {LIVE_BROWSER_ENV}=1 to run the FIT-320 live browser gate",
)
def test_browser_executes_multi_photo_offline_replay_and_cleanup(tmp_path):
    cli = _playwright_cli()
    states = {
        case["id"]: case
        for case in json.loads(MATRIX.read_text())["states"]
    }
    port = _free_port()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    home_dir = tmp_path / "home"
    (home_dir / "Documents" / "Health").mkdir(parents=True)
    valid_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    small_paths = []
    for index in range(5):
        path = tmp_path / f"meal-{index + 1}.png"
        path.write_bytes(valid_png + bytes([index]) * index)
        small_paths.append(str(path))
    oversize_path = tmp_path / "oversize.jpg"
    oversize_path.write_bytes(b"x" * (6 * 1024 * 1024 + 1))
    expected_replay_records = []
    for index, path_string in enumerate(small_paths[2:4], start=1):
        path = Path(path_string)
        payload = path.read_bytes()
        expected_replay_records.append({
            "filename": f"meal-{index}.png",
            "mimetype": "image/png",
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    tool_home = tmp_path / "playwright-home"
    tool_home.mkdir()
    tool_env = {
        key: os.environ[key]
        for key in ("LANG", "LC_ALL", "PATH", "TMPDIR")
        if key in os.environ
    }
    tool_env.update({
        "HOME": str(tool_home),
        "npm_config_cache": str(tmp_path / "npm-cache"),
        "PLAYWRIGHT_BROWSERS_PATH": str(tmp_path / "playwright-browsers"),
    })
    offline_photo_bytes = sum(Path(path).stat().st_size for path in small_paths[2:4])
    discard_photo_bytes = Path(small_paths[4]).stat().st_size
    env = {
        key: os.environ[key]
        for key in ("LANG", "LC_ALL", "PATH", "TMPDIR")
        if key in os.environ
    }
    env.update({
        "HOME": str(home_dir),
        "DATA_DIR": str(data_dir),
        "SECRET_KEY": "fit320-browser-qa-secret",
        "SESSION_COOKIE_SECURE": "false",
        "HEALTH_SYNC_TOKEN": "fit320-browser-qa-health-token",
        "APPLE_HEALTH_SYNC_DB": str(data_dir / "apple_health_sync.sqlite3"),
        "OURA_API_TOKEN": "",
        "OPENAI_API_KEY": "",
        "OW_USERNAME": "",
        "OW_PASSWORD": "",
        "OW_USER_ID": "",
    })
    # app.py loads absent keys from the checkout's ignored .env during import.
    # Shadow every declared key without copying any value into the child.
    dotenv_path = ROOT / ".env"
    if dotenv_path.exists():
        for raw_line in dotenv_path.read_text().splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#") and "=" in line:
                key = line.split("=", 1)[0].strip()
                if key:
                    env.setdefault(key, "")
    server_code = f"""
import hashlib
import json
from flask import jsonify, request
import app as module

expected_records = {expected_replay_records!r}

def fit320_meal_intake():
    images = request.files.getlist('images')
    records = []
    for image in images:
        payload = image.read()
        records.append({{
            'filename': image.filename,
            'mimetype': image.mimetype,
            'size': len(payload),
            'sha256': hashlib.sha256(payload).hexdigest(),
        }})
    if records != expected_records:
        return jsonify({{'error': {{'message': 'FIT-320 replay fixture received incorrect image parts', 'records': records}}}}), 422
    return jsonify({{'status': 'logged', 'estimate': {{'calories': 320, 'protein_g': 24, 'carbs_g': 31, 'fat_g': 11}}}})

module.app.view_functions['meal_intake'] = fit320_meal_intake
module.app.config['LOGIN_DISABLED'] = True
module.app.run(host='127.0.0.1', port={port}, debug=False, use_reloader=False)
"""
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
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
        raise AssertionError("FIT-320 test server did not start")

    session = f"fit320-{os.getpid()}"

    def run_cli(args: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            [*cli, *args],
            cwd=tmp_path,
            env=tool_env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
            exc.stdout = stdout
            exc.stderr = stderr
            raise
        return subprocess.CompletedProcess(
            [*cli, *args], process.returncode, stdout, stderr
        )

    def run(*args: str) -> str:
        result = run_cli(
            [f"-s={session}", *args],
            timeout=PLAYWRIGHT_COMMAND_TIMEOUT_SECONDS,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip()

    def run_global(*args: str) -> str:
        result = run_cli(list(args), timeout=PLAYWRIGHT_INSTALL_TIMEOUT_SECONDS)
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout.strip()

    def session_daemon_pids() -> list[int]:
        result = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
        )
        suffix = f"cliDaemon.js {session}"
        pids = []
        for line in result.stdout.splitlines():
            pid_text, _, command = line.strip().partition(" ")
            if pid_text.isdigit() and command.strip().endswith(suffix):
                pids.append(int(pid_text))
        return pids

    def terminate_session_daemon() -> list[int]:
        pids = session_daemon_pids()
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            alive = []
            for pid in pids:
                try:
                    os.kill(pid, 0)
                    alive.append(pid)
                except ProcessLookupError:
                    pass
            if not alive:
                return pids
            time.sleep(0.1)
        for pid in alive:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return pids

    def assert_state(state_id: str) -> None:
        case = states[state_id]
        run(
            "run-code",
            "async (page) => { await page.locator(%s).filter({hasText:%s}).waitFor({state:'visible',timeout:5000}); }"
            % (json.dumps(case["selector"]), json.dumps(case["expected_text"])),
        )

    def snapshot() -> dict:
        script = """async () => {
          const db = await new Promise((resolve, reject) => {
            const req = indexedDB.open('fitMealIntakeQueueDB', 1);
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
          });
          const tx = db.transaction(['queued_meals', 'meal_photos'], 'readonly');
          const getAll = (store) => new Promise((resolve, reject) => {
            const req = tx.objectStore(store).getAll();
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
          });
          const meals = await getAll('queued_meals');
          const photos = await getAll('meal_photos');
          return {
            mealCount: meals.length,
            photoCount: photos.length,
            blobCount: photos.filter((photo) => photo.blob instanceof Blob).length,
            photoBytes: photos.reduce((total, photo) => total + (photo.blob?.size || 0), 0),
          };
        }"""
        return json.loads(run("eval", script, "--raw"))

    try:
        run_global("install-browser", "chromium")
        run("open", "about:blank")
        run("route", "**/api/auth/scope", "--body", '{"auth_scope":"fit320-owner"}', "--content-type", "application/json")
        run("route", "**/api/oura/**", "--body", '{"connected":false}', "--content-type", "application/json")
        run("route", "**/api/whoop/status", "--body", '{"connected":false}', "--content-type", "application/json")
        run("route", "**/api/open-wearables/status", "--body", '{"connected":false}', "--content-type", "application/json")
        run("route", "**/api/wearable-sources", "--body", '{"sources":[]}', "--content-type", "application/json")
        run("route", "**/api/ai/**", "--body", '{"enabled":false}', "--content-type", "application/json")
        run("route", "**/api/apple-health/**", "--body", '{}', "--content-type", "application/json")
        run("route", "**/favicon.ico", "--status", "204", "--body", "")
        run("run-code", r"""async (page) => {
          const cdp = await page.context().newCDPSession(page);
          await cdp.send('Emulation.setDeviceMetricsOverride', {
            width: 390, height: 844, deviceScaleFactor: 3, mobile: true,
          });
          await cdp.send('Emulation.setTouchEmulationEnabled', {
            enabled: true, maxTouchPoints: 5,
          });
          await cdp.send('Emulation.setUserAgentOverride', {
            userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1',
            platform: 'iPhone',
          });
          await page.addInitScript(() => {
            const loads = Number(sessionStorage.getItem('fit320-load-count') || '0');
            sessionStorage.setItem('fit320-load-count', String(loads + 1));
          });
        }""")
        run("goto", f"http://127.0.0.1:{port}/")
        run("run-code", """async (page) => {
          await page.waitForFunction(() => Number(sessionStorage.getItem('fit320-load-count')) >= 2 && navigator.serviceWorker.controller !== null);
          await page.locator('#meal-composer').waitFor({state:'visible'});
          await page.waitForFunction(() => {
            const authFinishedThisDocument = performance.getEntriesByType('resource').some((entry) => {
              const url = new URL(entry.name);
              return url.pathname === '/api/auth/scope' && entry.responseEnd > 0;
            });
            return authFinishedThisDocument
              && localStorage.getItem('fit145:meal-queue-auth-scope:v1') === 'fit320-owner';
          });
        }""")
        mobile_profile = json.loads(run("eval", "() => ({width:innerWidth,height:innerHeight,dpr:devicePixelRatio,touch:navigator.maxTouchPoints,iphone:navigator.userAgent.includes('iPhone'),multiple:document.querySelector('#meal-composer-image').multiple,accept:document.querySelector('#meal-composer-image').accept})", "--raw"))
        assert mobile_profile == {
            "width": 390,
            "height": 844,
            "dpr": 3,
            "touch": 5,
            "iphone": True,
            "multiple": True,
            "accept": "image/*",
        }

        # Five inputs exercise the production four-photo cap without replacing
        # the accepted thumbnails.
        run("run-code", f"async (page) => {{ await page.locator('#meal-composer-image').setInputFiles({json.dumps(small_paths)}); }}")
        assert run("eval", "() => document.querySelectorAll('.meal-composer-thumb').length", "--raw") == "4"
        run("run-code", "async (page) => { await page.waitForFunction(() => { const images = [...document.querySelectorAll('.meal-composer-thumb img')]; return images.length === 4 && images.every((image) => image.complete && image.naturalWidth > 0 && image.naturalHeight > 0); }); }")
        assert_state("four-photo-cap")
        run("screenshot", "--filename", str(tmp_path / "fit-320-four-photo-cap.png"))
        run("run-code", "async (page) => { while (await page.locator('.meal-composer-thumb-remove').count()) await page.locator('.meal-composer-thumb-remove').first().click(); }")

        # A per-photo oversize rejection leaves the composer empty.
        run("run-code", f"async (page) => {{ await page.locator('#meal-composer-image').setInputFiles({json.dumps(str(oversize_path))}); }}")
        assert run("eval", "() => document.querySelectorAll('.meal-composer-thumb').length", "--raw") == "0"
        assert_state("oversize-rejection")
        run("screenshot", "--filename", str(tmp_path / "fit-320-oversize-rejection.png"))

        # Removing one of two previews revokes its object URL and leaves the
        # other attachment intact.
        run("eval", "() => { window.__fit320Revoked = []; const original = URL.revokeObjectURL.bind(URL); URL.revokeObjectURL = (url) => { window.__fit320Revoked.push(url); original(url); }; }", "--raw")
        run("run-code", f"async (page) => {{ await page.locator('#meal-composer-image').setInputFiles({json.dumps(small_paths[:2])}); }}")
        preview_sources = json.loads(run("eval", "() => [...document.querySelectorAll('.meal-composer-thumb img')].map((image) => image.src)", "--raw"))
        assert len(preview_sources) == 2
        run("run-code", "async (page) => { await page.locator('.meal-composer-thumb-remove').first().click(); }")
        assert run("eval", "() => document.querySelectorAll('.meal-composer-thumb').length", "--raw") == "1"
        removal_proof = json.loads(run("eval", "() => ({revoked:window.__fit320Revoked.slice(),retained:document.querySelector('.meal-composer-thumb img').src})", "--raw"))
        assert removal_proof == {"revoked": [preview_sources[0]], "retained": preview_sources[1]}
        run("run-code", "async (page) => { await page.waitForFunction(() => { const image = document.querySelector('.meal-composer-thumb img'); return image && image.complete && image.naturalWidth > 0 && image.naturalHeight > 0; }); }")
        assert_state("photo-removal")
        run("screenshot", "--filename", str(tmp_path / "fit-320-photo-removal.png"))
        run("run-code", "async (page) => { await page.locator('.meal-composer-thumb-remove').first().click(); }")

        # Save two real Blob-backed files while the browser is offline.
        run("run-code", "async (page) => { await page.context().setOffline(true); }")
        run("run-code", "async (page) => { await page.waitForFunction(() => document.querySelector('#meal-composer-submit').textContent === 'Save offline'); }")
        run("run-code", f"async (page) => {{ await page.locator('#meal-composer-image').setInputFiles({json.dumps(small_paths[2:4])}); await page.locator('#meal-composer-text').fill('Offline meal'); await page.locator('#meal-composer-submit').click(); }}")
        run("run-code", "async (page) => { await page.waitForFunction(async () => { const req=indexedDB.open('fitMealIntakeQueueDB',1); const db=await new Promise((r,j)=>{req.onsuccess=()=>r(req.result);req.onerror=()=>j(req.error)}); const tx=db.transaction('queued_meals','readonly'); const all=await new Promise((r,j)=>{const q=tx.objectStore('queued_meals').getAll();q.onsuccess=()=>r(q.result);q.onerror=()=>j(q.error)}); return all.length===1; }); }")
        assert snapshot() == {"mealCount": 1, "photoCount": 2, "blobCount": 2, "photoBytes": offline_photo_bytes}
        assert_state("offline-persistence")
        run("screenshot", "--filename", str(tmp_path / "fit-320-offline-persistence.png"))

        # Reload the shell from the isolated server while an init script keeps
        # production code in its offline branch. This proves IndexedDB boot
        # persistence, not disconnected app-shell availability. A temporary
        # 503 route prevents the old page's online event from winning the
        # transition.
        run("route", "**/api/meal-intake", "--status", "503", "--body", '{"error":{"message":"offline reload transition"}}', "--content-type", "application/json")
        run("run-code", """async (page) => {
          await page.addInitScript(() => {
            Object.defineProperty(navigator, 'onLine', {configurable:true, get:() => false});
          });
          await page.context().setOffline(false);
          await page.reload();
          await page.locator('#meal-composer').waitFor({state:'visible'});
          await page.waitForFunction(() => navigator.onLine === false && !document.querySelector('#sync-banner').hidden);
        }""")
        assert run("eval", "() => navigator.onLine", "--raw") == "false"
        assert snapshot() == {"mealCount": 1, "photoCount": 2, "blobCount": 2, "photoBytes": offline_photo_bytes}
        assert_state("offline-persistence")

        # Reconnection runs the shipped online listener and evicts both meal
        # metadata and photo blobs only after a successful replay response.
        run("unroute", "**/api/meal-intake")
        reconnect_case = states["reconnect-replay"]
        reconnect_wait = (
            "await page.locator(%s).filter({hasText:%s}).waitFor({state:'visible',timeout:5000});"
            % (
                json.dumps(reconnect_case["selector"]),
                json.dumps(reconnect_case["expected_text"]),
            )
        )
        reconnect_code = """async (page) => {
          await page.evaluate(() => {
            const originalFetch = window.fetch.bind(window);
            window.__fit320Replay = null;
            window.fetch = async (input, init = {}) => {
              const url = new URL(typeof input === 'string' ? input : input.url, location.href);
              if (url.pathname === '/api/meal-intake' && init.body instanceof FormData) {
                const records = [];
                for (const file of init.body.getAll('images')) {
                  const payload = await file.arrayBuffer();
                  const digest = await crypto.subtle.digest('SHA-256', payload);
                  records.push({
                    filename: file.name,
                    mimetype: file.type,
                    size: file.size,
                    sha256: Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join(''),
                  });
                }
                window.__fit320Replay = {count: records.length, records};
              }
              return originalFetch(input, init);
            };
            Object.defineProperty(navigator, 'onLine', {configurable:true, get:() => true});
            window.dispatchEvent(new Event('online'));
          });
          __RECONNECT_WAIT__
        }""".replace("__RECONNECT_WAIT__", reconnect_wait)
        run("run-code", reconnect_code)
        run("run-code", "async (page) => { await page.waitForFunction(async () => { const req=indexedDB.open('fitMealIntakeQueueDB',1); const db=await new Promise((r,j)=>{req.onsuccess=()=>r(req.result);req.onerror=()=>j(req.error)}); const tx=db.transaction(['queued_meals','meal_photos'],'readonly'); const count=(store)=>new Promise((r,j)=>{const q=tx.objectStore(store).count();q.onsuccess=()=>r(q.result);q.onerror=()=>j(q.error)}); return (await count('queued_meals'))===0 && (await count('meal_photos'))===0; }, null, {timeout:10000}); }")
        replay = json.loads(run("eval", "() => window.__fit320Replay", "--raw"))
        assert replay == {"count": 2, "records": expected_replay_records}
        assert snapshot() == {"mealCount": 0, "photoCount": 0, "blobCount": 0, "photoBytes": 0}
        run("screenshot", "--filename", str(tmp_path / "fit-320-reconnect-replay.png"))

        # A second offline save is discarded through the production queue UI.
        run("run-code", f"""async (page) => {{
          await page.context().setOffline(true);
          await page.evaluate(() => {{
            Object.defineProperty(navigator, 'onLine', {{configurable:true, get:() => false}});
            window.dispatchEvent(new Event('offline'));
          }});
          await page.locator('#meal-composer-image').setInputFiles({json.dumps(small_paths[4])});
          await page.locator('#meal-composer-text').fill('Discard me');
          await page.locator('#meal-composer-submit').click();
          await page.waitForFunction(() => !document.querySelector('#sync-banner').hidden);
        }}""")
        assert snapshot() == {"mealCount": 1, "photoCount": 1, "blobCount": 1, "photoBytes": discard_photo_bytes}
        run("click", "#sync-banner")
        run("run-code", "async (page) => { await page.locator('[data-meal-sync-discard]').waitFor({state:'visible'}); }")
        assert_state("discard-blob-deletion")
        layout = json.loads(run("eval", "() => { const modal=document.querySelector('#modal-sync-queue'); const sheet=modal.querySelector('.modal-sheet'); const foot=modal.querySelector('.modal-foot'); const nav=document.querySelector('.tab-bar'); const sr=sheet.getBoundingClientRect(); const fr=foot.getBoundingClientRect(); const nr=nav.getBoundingClientRect(); const modalZ=Number(getComputedStyle(modal).zIndex); const navZ=Number(getComputedStyle(nav).zIndex); return {inViewport:sr.top>=0 && sr.bottom<=innerHeight, controlsNotObscured:fr.bottom<=nr.top || modalZ>navZ}; }", "--raw"))
        assert layout == {"inViewport": True, "controlsNotObscured": True}
        run("screenshot", "--filename", str(tmp_path / "fit-320-discard-blob-deletion.png"))
        run("eval", "() => { window.confirm = () => true; }", "--raw")
        run("click", "[data-meal-sync-discard]")
        run("run-code", "async (page) => { await page.waitForFunction(async () => { const req=indexedDB.open('fitMealIntakeQueueDB',1); const db=await new Promise((r,j)=>{req.onsuccess=()=>r(req.result);req.onerror=()=>j(req.error)}); const tx=db.transaction(['queued_meals','meal_photos'],'readonly'); const count=(store)=>new Promise((r,j)=>{const q=tx.objectStore(store).count();q.onsuccess=()=>r(q.result);q.onerror=()=>j(q.error)}); return (await count('queued_meals'))===0 && (await count('meal_photos'))===0; }); }")
        assert snapshot() == {"mealCount": 0, "photoCount": 0, "blobCount": 0, "photoBytes": 0}

        console = run("console", "warning")
        assert "Errors: 0, Warnings: 0" in console
        assert len(list(tmp_path.glob("fit-320-*.png"))) == len(REQUIRED_STATES)
    finally:
        active_error = sys.exc_info()[1]
        close_error = None
        try:
            for attempt in range(2):
                try:
                    close_result = run_cli(
                        [f"-s={session}", "close"],
                        timeout=30,
                    )
                    if close_result.returncode == 0:
                        deadline = time.monotonic() + 5
                        daemon_gone = False
                        while time.monotonic() < deadline:
                            if not session_daemon_pids():
                                daemon_gone = True
                                break
                            time.sleep(0.1)
                        if daemon_gone:
                            close_error = None
                            break
                        close_error = AssertionError(
                            f"FIT-320 Playwright close attempt {attempt + 1} "
                            "returned success but the exact session daemon remained"
                        )
                    else:
                        close_error = AssertionError(
                            f"FIT-320 Playwright close attempt {attempt + 1} failed: "
                            f"{close_result.stdout}{close_result.stderr}"
                        )
                except subprocess.TimeoutExpired as exc:
                    close_error = AssertionError(
                        f"FIT-320 Playwright close attempt {attempt + 1} timed out"
                    )
                    close_error.__cause__ = exc
                except (OSError, subprocess.SubprocessError) as exc:
                    close_error = AssertionError(
                        f"FIT-320 Playwright close attempt {attempt + 1} could not run"
                    )
                    close_error.__cause__ = exc
            if close_error is not None:
                try:
                    terminated = terminate_session_daemon()
                    close_error.add_note(
                        f"Force-terminated exact Playwright session daemon PIDs: {terminated or 'none found'}"
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    close_error.add_note(
                        f"Exact-session daemon inspection/termination also failed: {exc}"
                    )
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        if close_error is not None:
            if active_error is not None:
                active_error.add_note(str(close_error))
                for note in getattr(close_error, "__notes__", []):
                    active_error.add_note(note)
            else:
                raise close_error


def test_runbook_documents_production_browser_scope_and_limits():
    notes = RUNBOOK.read_text()
    assert "production template" in notes
    assert "production `static/js/app.js`" in notes
    assert "390x844" in notes
    assert "production data or services" in notes
    assert "IndexedDB" in notes
    assert "blob deletion" in notes
