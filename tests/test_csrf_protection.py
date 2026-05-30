from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_OMIT_AUTO_CSRF = {"fitness_dashboard.omit_auto_csrf_header": True}


def _app_module(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "fit191-csrf-secret")
    monkeypatch.setenv("HEALTH_SYNC_TOKEN", "fit191-health-token")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "save_json", lambda *_args, **_kwargs: None)
    return module


def _csrf_from_form(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match
    return match.group(1)


def test_state_changing_post_without_csrf_header_is_rejected(monkeypatch):
    module = _app_module(monkeypatch)

    response = module.app.test_client().post(
        "/api/settings",
        json={},
        environ_overrides=_OMIT_AUTO_CSRF,
    )

    assert response.status_code == 403
    assert response.get_json()["code"] == "csrf_required"


def test_same_origin_metadata_allows_cached_app_shell_posts(monkeypatch):
    module = _app_module(monkeypatch)

    response = module.app.test_client().post(
        "/api/settings",
        json={},
        headers={"Origin": "http://localhost"},
        environ_overrides=_OMIT_AUTO_CSRF,
    )

    assert response.status_code == 200
    assert response.get_json()["status"] == "success"


def test_cross_origin_header_cannot_satisfy_csrf(monkeypatch):
    module = _app_module(monkeypatch)

    response = module.app.test_client().post(
        "/api/settings",
        json={},
        headers={"Origin": "https://evil.example", "X-Requested-With": "XMLHttpRequest"},
        environ_overrides=_OMIT_AUTO_CSRF,
    )

    assert response.status_code == 403
    assert response.get_json()["code"] == "csrf_required"


def test_forwarded_https_origin_keeps_real_site_mutations_working(monkeypatch):
    module = _app_module(monkeypatch)

    response = module.app.test_client().post(
        "/api/settings",
        json={},
        headers={
            "Host": "fitness.example.test",
            "Origin": "https://fitness.example.test",
            "X-Forwarded-Host": "fitness.example.test",
            "X-Forwarded-Proto": "https",
            "X-Requested-With": "XMLHttpRequest",
        },
        environ_overrides=_OMIT_AUTO_CSRF,
    )

    assert response.status_code == 200
    assert response.get_json()["status"] == "success"


def test_configured_public_base_origin_keeps_proxied_mutations_working(monkeypatch):
    monkeypatch.setenv("FITNESS_DASHBOARD_PUBLIC_BASE_URL", "https://fitness.example.test")
    module = _app_module(monkeypatch)

    response = module.app.test_client().post(
        "/api/settings",
        json={},
        headers={
            "Host": "127.0.0.1:5050",
            "Origin": "https://fitness.example.test",
            "X-Requested-With": "XMLHttpRequest",
        },
        environ_overrides=_OMIT_AUTO_CSRF,
    )

    assert response.status_code == 200
    assert response.get_json()["status"] == "success"


def test_cors_preflight_does_not_allow_csrf_header(monkeypatch):
    module = _app_module(monkeypatch)

    response = module.app.test_client().open(
        "/register",
        method="OPTIONS",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-Requested-With, Content-Type",
        },
    )

    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert "X-Requested-With" not in response.headers["Access-Control-Allow-Headers"]


def test_public_auth_posts_without_form_token_are_rejected(monkeypatch):
    module = _app_module(monkeypatch)
    client = module.app.test_client()

    for path in ("/login", "/register"):
        response = client.post(path, data={}, environ_overrides=_OMIT_AUTO_CSRF)

        assert response.status_code == 403


def test_public_auth_form_token_allows_login_post(monkeypatch):
    module = _app_module(monkeypatch)
    client = module.app.test_client()
    token = _csrf_from_form(client.get("/login").get_data(as_text=True))

    response = client.post(
        "/login",
        data={"username": "missing", "password": "invalid", "csrf_token": token},
        environ_overrides=_OMIT_AUTO_CSRF,
    )

    assert response.status_code == 200
    assert "Invalid username or password." in response.get_data(as_text=True)


def test_server_rendered_checkout_form_includes_csrf_token():
    source = Path("templates/pricing.html").read_text()
    form = source.split('action="/create-checkout-session" method="POST"', 1)[1].split("</form>", 1)[0]

    assert 'name="csrf_token"' in form
    assert 'value="{{ csrf_token }}"' in form


def test_all_server_rendered_post_forms_include_csrf_token():
    post_forms = []

    for template_path in sorted(Path("templates").glob("*.html")):
        source = template_path.read_text()
        for match in re.finditer(r"<form\b[^>]*>", source, flags=re.IGNORECASE):
            tag = match.group(0)
            if not re.search(r"\bmethod\s*=\s*['\"]?post['\"]?", tag, flags=re.IGNORECASE):
                continue
            end = source.find("</form>", match.end())
            assert end != -1, f"{template_path}:{match.start()} POST form is not closed"
            form_html = source[match.start():end]
            post_forms.append(str(template_path))

            assert 'name="csrf_token"' in form_html
            assert 'value="{{ csrf_token }}"' in form_html

    assert post_forms == ["templates/login.html", "templates/pricing.html"]


def test_state_changing_post_with_csrf_header_continues_to_work(monkeypatch):
    module = _app_module(monkeypatch)

    response = module.app.test_client().post(
        "/api/settings",
        json={},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )

    assert response.status_code == 200
    assert response.get_json()["status"] == "success"


def test_token_authed_apple_health_webhook_is_csrf_exempt(monkeypatch):
    module = _app_module(monkeypatch)

    response = module.app.test_client().post(
        "/api/apple-health/sync",
        json={"steps": []},
        environ_overrides=_OMIT_AUTO_CSRF,
    )

    assert response.status_code == 401
    assert response.get_json()["error"] == "invalid or missing sync token"


def test_live_js_client_sends_csrf_header():
    source = Path("static/js/app.js").read_text()

    assert "X-Requested-With" in source
    assert "XMLHttpRequest" in source
    assert "[CSRF_HEADER_NAME]: CSRF_HEADER_VALUE" in source


def test_api_helper_preserves_csrf_header_when_callers_add_headers():
    source = Path("static/js/app.js").read_text()
    api_body = source.split("async function api(path, opts = {}) {", 1)[1].split("if (res.status === 401)", 1)[0]

    assert "const headers = { 'Accept': 'application/json', ...(fetchOpts.headers || {}), [CSRF_HEADER_NAME]: CSRF_HEADER_VALUE };" in api_body
    assert "...fetchOpts,\n                credentials: 'same-origin',\n                headers," in api_body


def test_csrf_rollout_bumps_cached_asset_versions():
    index = Path("templates/index.html").read_text()
    service_worker = Path("static/js/sw.js").read_text()

    assert "app.js?v=20260530-fit192-a11y" in index
    assert "fitness-dashboard-v20260530-fit192-a11y" in service_worker
    assert "self.skipWaiting()" in service_worker
    assert "self.clients.claim()" in service_worker
