"""PER-1585: auth, tool surface and output hygiene of the read-only MCP server.

The dashboard and GitHub are mocked at the HTTP transport; everything else is real.
"""
import asyncio
import hashlib
import logging
import re
import time

import httpx2
import pytest
from cryptography.fernet import Fernet
from fastmcp.server.auth.oauth_proxy.models import JTIMapping, UpstreamTokenSet
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

from server import Config, LogRejectedRedirects, build_app

OWNER_ID, OTHER_ID = "424242", "999"
GH_OWNER, GH_OTHER = "gho_owner_token_value", "gho_other_token_value"
BOT_TOKEN = "bot-token-plain-value"
READ_TOKEN = "dashboard-read-token-value"
CLIENT_SECRET = "github-client-secret-value"
SIGNING_KEY = "jwt-signing-key-value-0123456789abcdef"
LEAK = "UNDOCUMENTED-LEAK"
SECRETS = (GH_OWNER, GH_OTHER, BOT_TOKEN, READ_TOKEN, CLIENT_SECRET, SIGNING_KEY)
TOOLS = {
    "get_today_plan", "get_workout_history", "get_progressive_overload",
    "get_body_trend", "get_sleep_recovery", "get_open_windows",
}
ALLOWED_PATHS = {
    "/api/recommendation/smart", "/api/history-all", "/api/progressive-overload", "/api/body-history",
    "/api/body-recomp", "/api/sleep/analytics", "/api/freshness", "/api/calendar/windows",
}
TODAY = time.strftime("%Y-%m-%d")
FRESH = {s: {"status": "ok", "last_data_point": TODAY, "last_sync_attempt": TODAY, "token": LEAK}
         for s in ("open_wearables", "oura", "whoop", "apple_health", "food")}
ROUTES = {
    "/api/recommendation/smart": {
        "recommendation": "moderate", "readiness": 80, "notes": LEAK,
        "readiness_factors": {"acwr": {"acwr": 1.1, "risk": "ok", "x": LEAK}, "sleep_debt": {"debt_minutes": 0, "status": "good", "x": LEAK}},
        "next_workout": {
            "id": LEAK, "auth_scope": LEAK, "focus": "push", "estimated_minutes": 45,
            "mesocycle": {"week": 2, "phase": "build", "rpe_base": 7, "x": LEAK},
            "exercises": [{"exercise": "Chest Press", "target_sets": 3, "target_reps": 8, "id": LEAK}],
        },
        "freshness": FRESH, "nutrition_context": {"x": LEAK},
    },
    "/api/history-all": {
        "workouts": [{"date": TODAY, "session_type": "push", "notes": LEAK, "id": LEAK,
                      "exercises": [{"machine": "Chest Press", "id": LEAK, "sets": [{"weight_lbs": 100, "reps": 8, "rpe": 7, "x": LEAK}]}]},
                     {"date": "2001-01-01", "exercises": []}],
        "cardio": [{"date": TODAY, "activity_type": "walk", "notes": LEAK}],
        "recovery": [], "personal_records": {"Chest Press": {"all_time": 130, "x": LEAK}},
    },
    "/api/progressive-overload": {"exercises": [{"exercise": "Chest Press", "last_weight": 100, "x": LEAK, "history": [{"date": TODAY, "weight": 100, "x": LEAK}]}]},
    "/api/body-history": {"trend": "stable", "history": [{"date": TODAY, "weight_lbs": 180, "notes": LEAK}]},
    "/api/body-recomp": {"summary": {"latest": {"date": TODAY, "weight_lbs": 180, "notes": LEAK}, "target_weight_lbs": 175, "x": LEAK}},
    "/api/sleep/analytics": {"history": [{"date": TODAY, "sleep_duration_min": 450, "deep_min": 80, "notes": LEAK}], "consistency_score": 90},
    "/api/freshness": {"freshness": FRESH},
}


@pytest.fixture
def seen():
    return []


@pytest.fixture
def make(tmp_path, seen):
    def build(*, bot=True):
        def dashboard(request: httpx2.Request) -> httpx2.Response:
            seen.append((request.method, request.url.path, request.headers.get("authorization")))
            body = ROUTES.get(request.url.path)
            return httpx2.Response(200, json=body) if body is not None else httpx2.Response(404)

        def github(request: httpx2.Request) -> httpx2.Response:
            user = {f"Bearer {GH_OWNER}": OWNER_ID, f"Bearer {GH_OTHER}": OTHER_ID}.get(request.headers.get("authorization"))
            return httpx2.Response(200, json={"id": int(user)}) if user else httpx2.Response(401)

        cfg = Config(
            public_url="https://frankbox.example.ts.net:8443/mcp", github_client_id="gh-client-id",
            github_client_secret=CLIENT_SECRET, owner_github_id=OWNER_ID, jwt_signing_key=SIGNING_KEY,
            storage_dir=str(tmp_path / "store"), storage_key=Fernet.generate_key().decode(), read_token=READ_TOKEN,
            bot_token_sha256=hashlib.sha256(BOT_TOKEN.encode()).hexdigest() if bot else "",
        )
        app, _ = build_app(
            cfg, github=httpx2.AsyncClient(transport=httpx2.MockTransport(github)),
            dashboard_transport=httpx2.MockTransport(dashboard),
        )
        return app

    return build


def oauth_token(app, github_token):
    """A proxy-issued access token whose stored GitHub token is `github_token` (the post-login state)."""
    proxy, now, ident = app.state.fastmcp_auth.server, time.time(), github_token

    async def seed():
        await proxy._upstream_token_store.put(key=ident, ttl=3600, value=UpstreamTokenSet(
            upstream_token_id=ident, access_token=github_token, refresh_token=None, refresh_token_expires_at=None,
            expires_at=now + 3600, token_type="Bearer", scope="read:user", client_id="c", created_at=now))
        await proxy._jti_mapping_store.put(key=ident, ttl=3600, value=JTIMapping(jti=ident, upstream_token_id=ident, created_at=now))

    asyncio.run(seed())
    return proxy.jwt_issuer.issue_access_token(client_id="c", scopes=["read:user"], jti=ident, expires_in=3600)


def rpc(client, method, params=None, token=None):
    """POST one JSON-RPC message. With a token, first opens (and reuses) a session for it."""
    headers = {"Accept": "application/json, text/event-stream"}
    sessions = client.__dict__.setdefault("_mcp_sessions", {})  # per client: a reused id() must not leak sessions

    def post(payload, extra=None):
        return client.post("/mcp", headers={**headers, **(extra or {})}, json={"jsonrpc": "2.0", **payload})

    if token:
        headers["Authorization"] = f"Bearer {token}"
        if token not in sessions:
            init = post({"id": 0, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}})
            if init.status_code != 200:
                return init
            sessions[token] = init.headers["mcp-session-id"]
            post({"method": "notifications/initialized"}, {"mcp-session-id": init.headers["mcp-session-id"]})
        headers["mcp-session-id"] = sessions[token]
    return post({"id": 1, "method": method, "params": params or {}})


def call(client, tool, token, **arguments):
    response = rpc(client, "tools/call", {"name": tool, "arguments": arguments}, token)
    assert response.status_code == 200, response.text
    return response.json()["result"]["structuredContent"]


def keys(value):
    """Every dict key in a nested value, for the 'documented fields only' check."""
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in keys(v)}
    return set()


@pytest.fixture
def client(make):
    app = make()
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        c.oauth_owner = oauth_token(app, GH_OWNER)
        yield c


@pytest.mark.parametrize("send", [lambda c: rpc(c, "tools/list"), lambda c: c.get("/mcp")])
def test_unauthenticated_is_401_with_challenge(client, send):
    response = send(client)
    assert response.status_code == 401
    assert 'resource_metadata="https://frankbox.example.ts.net:8443/.well-known/oauth-protected-resource/mcp"' in response.headers["www-authenticate"]


def test_oauth_discovery_metadata_is_served(client):
    resource = client.get("/.well-known/oauth-protected-resource/mcp")
    assert resource.status_code == 200
    assert resource.json()["resource"] == "https://frankbox.example.ts.net:8443/mcp"
    server = client.get("/.well-known/oauth-authorization-server")
    assert server.status_code == 200
    assert server.json()["registration_endpoint"] == "https://frankbox.example.ts.net:8443/register"


def test_only_read_only_tools_are_listed_on_both_auth_paths(client):
    for token in (BOT_TOKEN, client.oauth_owner):
        tools = rpc(client, "tools/list", token=token).json()["result"]["tools"]
        assert {t["name"] for t in tools} == TOOLS
        assert all(t["annotations"]["readOnlyHint"] is True and t["annotations"]["openWorldHint"] is False for t in tools)


def test_static_bearer_right_wrong_missing(client):
    assert rpc(client, "tools/list", token=BOT_TOKEN).status_code == 200
    assert rpc(client, "tools/list", token="not-the-bot-token").status_code == 401
    assert rpc(client, "tools/list").status_code == 401


def test_no_bot_hash_configured_turns_the_static_path_off(make):
    with TestClient(make(bot=False), base_url="http://127.0.0.1:8765") as c:
        assert rpc(c, "tools/list", token=BOT_TOKEN).status_code == 401


@pytest.mark.parametrize("method", ["tools/list", "resources/list", "prompts/list"])
def test_non_owner_github_user_is_refused_on_every_method(make, method):
    app = make()
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        assert rpc(c, method, token=oauth_token(app, GH_OTHER)).status_code == 401
        assert rpc(c, method, token=oauth_token(app, GH_OWNER)).status_code == 200


def test_non_owner_cannot_call_a_tool(make, seen):
    app = make()
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        response = rpc(c, "tools/call", {"name": "get_today_plan", "arguments": {}}, oauth_token(app, GH_OTHER))
    assert response.status_code == 401
    assert seen == []


def test_tools_return_only_documented_fields(client):
    outputs = {
        "get_today_plan": call(client, "get_today_plan", BOT_TOKEN),
        "get_workout_history": call(client, "get_workout_history", BOT_TOKEN),
        "get_progressive_overload": call(client, "get_progressive_overload", client.oauth_owner),
        "get_body_trend": call(client, "get_body_trend", BOT_TOKEN),
        "get_sleep_recovery": call(client, "get_sleep_recovery", BOT_TOKEN),
    }
    flat = str(outputs)
    assert LEAK not in flat
    assert not keys(outputs) & {"id", "auth_scope", "notes", "food", "nutrition_context", "token", "x"}
    plan = outputs["get_today_plan"]
    assert plan["next_workout"]["exercises"] == [{"exercise": "Chest Press", "target_sets": 3, "target_reps": 8}]
    assert set(plan["data_freshness"]) == {"open_wearables", "oura", "whoop", "apple_health"}
    assert outputs["get_sleep_recovery"]["nights"][0]["deep_min"] == 80  # imported-night key name survives
    history = outputs["get_workout_history"]
    assert [w["date"] for w in history["workouts"]] == [TODAY]  # the 2001 session is outside `days`
    assert history["workouts"][0]["exercises"] == [{"name": "Chest Press", "sets": [{"weight_lbs": 100, "reps": 8, "rpe": 7}]}]


def test_tools_only_issue_allowlisted_gets_with_the_read_token(client, seen):
    for tool in sorted(TOOLS - {"get_open_windows"}):
        call(client, tool, BOT_TOKEN)
    assert {path for _, path, _ in seen} <= ALLOWED_PATHS
    assert {method for method, _, _ in seen} == {"GET"}
    assert {auth for _, _, auth in seen} == {f"Bearer {READ_TOKEN}"}


def test_open_windows_unconfigured_when_route_is_missing(client):
    assert call(client, "get_open_windows", BOT_TOKEN, day="today") == {"status": "unconfigured", "day": "today"}
    bad = rpc(client, "tools/call", {"name": "get_open_windows", "arguments": {"day": "yesterday"}}, BOT_TOKEN).json()
    assert bad["result"]["isError"] is True


def test_unlisted_redirect_uri_is_refused_and_logged(client, caplog):
    body = {"redirect_uris": ["https://grok.example/cb"], "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code"], "response_types": ["code"]}
    with caplog.at_level(logging.WARNING, logger="fitness_mcp"):
        refused = client.post("/register", json=body)
        body["redirect_uris"] = ["https://claude.ai/api/mcp/auth_callback"]
        allowed = client.post("/register", json=body)
    assert (refused.status_code, allowed.status_code) == (400, 201)
    assert "https://grok.example/cb" in caplog.text


def test_refused_uri_cannot_forge_log_lines(client, caplog):
    forged = "https://x.invalid/a\n2026-10-28 10:30:00,000 fitness_mcp WARNING /register refused: grok.evil.example"
    with caplog.at_level(logging.WARNING, logger="fitness_mcp"):
        client.get("/authorize", params={"redirect_uri": forged})
    assert caplog.text.count("\n") <= 1 and "\\n" in caplog.text


def test_anonymous_endpoints_are_rate_limited(caplog):
    async def ok(scope, receive, send):
        await PlainTextResponse("ok")(scope, receive, send)

    limited = TestClient(LogRejectedRedirects(ok, limit=3), base_url="http://127.0.0.1:8765")
    with caplog.at_level(logging.WARNING, logger="fitness_mcp"):
        codes = [limited.post("/register").status_code for _ in range(4)] + [limited.get("/authorize").status_code]
    assert codes == [200, 200, 200, 429, 429]
    assert caplog.text.count("rate limited") == 1  # a flood must not grow the log per request
    assert limited.get("/mcp").status_code == 200  # other paths are not counted


def test_unknown_host_header_is_refused(client):
    assert rpc(client, "tools/list", token=BOT_TOKEN).status_code == 200
    response = client.post("/mcp", headers={"Host": "evil.example", "Authorization": f"Bearer {BOT_TOKEN}"}, json={})
    assert response.status_code == 421


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append((record.levelno, record.getMessage()))


@pytest.fixture
def fastmcp_log():
    """Logger 'fastmcp' has propagate=False, so caplog (a root handler) never sees its records."""
    logger = logging.getLogger("fastmcp")
    handler, old = Capture(), logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield handler
    logger.removeHandler(handler)
    logger.setLevel(old)


def test_no_secret_in_outputs_or_logs(make, caplog, fastmcp_log):
    app = make()
    with caplog.at_level(logging.DEBUG), TestClient(app, base_url="http://127.0.0.1:8765") as c:
        texts = [rpc(c, "tools/list", token=t).text for t in (BOT_TOKEN, "wrong-token", oauth_token(app, GH_OTHER))]
        texts += [str(call(c, "get_today_plan", BOT_TOKEN)), c.get("/.well-known/oauth-authorization-server").text]
    assert fastmcp_log.records  # the capture works: FastMCP did log
    everything = "\n".join(texts) + caplog.text + "\n".join(m for _, m in fastmcp_log.records)
    assert not [s for s in SECRETS if s in everything]
    assert not re.search(r"Bearer\s+\S{8,}", caplog.text)


def test_anonymous_callback_flood_is_bounded_at_the_plist_log_level(make, fastmcp_log):
    """FASTMCP_LOG_LEVEL=ERROR (the plist) keeps FastMCP's failed-callback lines: they must be capped."""
    with TestClient(make(), base_url="http://127.0.0.1:8765") as c:
        codes = {
            c.get("/auth/callback", params={"error": f"x\nforged {i}", "error_description": "A" * 2000}).status_code
            for i in range(200)
        }
        bad_state = c.get("/auth/callback", params={"code": "abc", "state": "bad"})
    kept = [m for level, m in fastmcp_log.records if level >= logging.ERROR]
    assert 429 in codes
    assert len(kept) <= 61, f"{len(kept)} ERROR records, {sum(map(len, kept))} bytes"
    assert not [m for m in kept if "\n" in m]
    assert bad_state.status_code == 429  # the window is already spent


def test_callback_validation_and_normal_callbacks(make, fastmcp_log):
    with TestClient(make(), base_url="http://127.0.0.1:8765") as c:
        long = c.get("/auth/callback", params={"error": "access_denied", "error_description": "A" * 2000})
        newline = c.get("/auth/callback", params={"error": "a\nb"})
        bad_state = c.get("/auth/callback", params={"code": "abc", "state": "bad"})
        denied = c.get("/auth/callback", params={"error": "access_denied", "state": "bad"})
    assert (long.status_code, newline.status_code) == (400, 400)
    assert "A" * 50 not in "".join(m for _, m in fastmcp_log.records)
    # a real-size callback still reaches FastMCP, which answers its usual 400
    assert bad_state.status_code == denied.status_code == 400 and "<html" in bad_state.text.lower()
    assert any(level >= logging.ERROR for level, _ in fastmcp_log.records)


def test_non_owner_github_token_costs_one_upstream_call_and_one_warning(caplog):
    from owner_auth import OwnerGitHubVerifier

    calls = []

    def github(request):
        calls.append(request.url.path)
        return httpx2.Response(200, json={"id": int(OTHER_ID)})

    verifier = OwnerGitHubVerifier(OWNER_ID, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(github)))
    with caplog.at_level(logging.WARNING, logger="fitness_mcp.auth"):
        results = [asyncio.run(verifier.verify_token(GH_OTHER)) for _ in range(5)]
    assert results == [None] * 5
    assert len(calls) == 1 and caplog.text.count("not the owner") == 1


def test_github_failures_are_never_cached():
    from owner_auth import OwnerGitHubVerifier

    answers = iter([httpx2.Response(503), httpx2.Response(200, json={"id": int(OWNER_ID)})])
    verifier = OwnerGitHubVerifier(OWNER_ID, http_client=httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: next(answers))))
    assert asyncio.run(verifier.verify_token(GH_OWNER)) is None
    assert asyncio.run(verifier.verify_token(GH_OWNER)) is not None


def test_cardio_prescription_keeps_documented_fields(make, monkeypatch):
    plan = ROUTES["/api/recommendation/smart"]["next_workout"]
    cardio = {"type": "walk", "duration_minutes": 20, "zone": 2, "zone_description": "easy", "heart_rate_range": "120-140",
              "intensity": "low", "technique": LEAK, "reason": "recovery", "secret": LEAK}
    monkeypatch.setitem(ROUTES, "/api/recommendation/smart", {
        **ROUTES["/api/recommendation/smart"], "next_workout": {**plan, "cardio": cardio}})
    with TestClient(make(), base_url="http://127.0.0.1:8765") as c:
        out = call(c, "get_today_plan", BOT_TOKEN)["next_workout"]["cardio"]
    assert out == {"type": "walk", "duration_minutes": 20, "zone": 2, "heart_rate_range": "120-140",
                   "intensity": "low", "reason": "recovery"}


def _fake_cimd(monkeypatch, url_doc):
    """Count the CIMD fetches FastMCP would make; `url_doc` maps client_id URL -> document."""
    from fastmcp.server.auth.cimd import CIMDDocument, CIMDFetcher

    fetched = []

    async def fetch(self, client_id_url):
        fetched.append(client_id_url)
        return CIMDDocument(client_id=client_id_url, client_name="t", redirect_uris=[url_doc])

    monkeypatch.setattr(CIMDFetcher, "fetch", fetch)
    return fetched


def test_cimd_is_fetched_only_from_vendor_hosts(make, monkeypatch):
    fetched = _fake_cimd(monkeypatch, "https://claude.ai/api/mcp/auth_callback")
    good, sub = "https://claude.ai/oauth/client.json", "https://mcp.anthropic.com/c.json"
    bad = ["https://example.com/x", "https://claude.ai.evil.example/x", "https://claude.ai@evil.example/x",
           "https://evilclaude.ai/x", "https://x.ai.example/x"]
    with TestClient(make(), base_url="https://frankbox.example.ts.net:8443", follow_redirects=False) as c:
        def authorize(client_id):
            return c.get("/authorize", params={
                "response_type": "code", "client_id": client_id, "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "code_challenge": "x" * 43, "code_challenge_method": "S256", "state": "s"}).status_code

        refused = [authorize(u) for u in bad]
        token = [c.post("/token", data={"grant_type": "authorization_code", "code": "c", "client_id": u,
                                        "redirect_uri": "x", "code_verifier": "v" * 43}).status_code for u in bad]
        assert fetched == [] and set(refused) <= {400, 401} and set(token) <= {400, 401}
        assert authorize(good) == 302 and authorize(sub) == 302  # vendor hosts and subdomains still work
        dcr = c.post("/register", json={"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                                        "token_endpoint_auth_method": "none"})
    assert fetched == [good, sub] and dcr.status_code == 201


def test_extra_cimd_hosts_come_from_the_environment(make, monkeypatch, tmp_path):
    for name, value in (("MCP_GITHUB_CLIENT_ID", "x"), ("MCP_GITHUB_CLIENT_SECRET", "y"), ("MCP_OWNER_GITHUB_ID", "1"),
                        ("MCP_STORAGE_DIR", "z"), ("MCP_EXTRA_CIMD_HOSTS", " grok.example , ,other.example")):
        monkeypatch.setenv(name, value)
    for name in ("MCP_JWT_SIGNING_KEY_FILE", "MCP_STORAGE_ENCRYPTION_KEY_FILE", "FITNESS_READ_TOKEN_FILE"):
        (tmp_path / name).write_text("k")
        monkeypatch.setenv(name, str(tmp_path / name))
    assert Config.from_env().extra_cimd_hosts == ("grok.example", "other.example")


def test_consent_page_is_rate_limited_and_the_store_stops_growing(make, tmp_path):
    base = "https://frankbox.example.ts.net:8443"
    cb = "https://claude.ai/api/mcp/auth_callback"
    with TestClient(make(), base_url=base, follow_redirects=False) as c:
        client_id = c.post("/register", json={"redirect_uris": [cb], "token_endpoint_auth_method": "none",
                                              "grant_types": ["authorization_code"], "response_types": ["code"]}).json()["client_id"]
        consent = c.get("/authorize", params={
            "response_type": "code", "client_id": client_id, "redirect_uri": cb, "code_challenge": "x" * 43,
            "code_challenge_method": "S256", "state": "s", "scope": "read:user"}).headers["location"]
        files = lambda: sum(f.is_file() for f in (tmp_path / "store").rglob("*"))  # noqa: E731
        before = files()
        codes = [c.get(consent).status_code for _ in range(70)]
    assert codes.count(429) >= 10 and codes[:55] == [200] * 55  # register + authorize + 55 renders fit the window
    assert files() - before <= 60


def test_unhandled_error_is_a_bare_500_with_one_log_line_and_no_traceback(make, monkeypatch, caplog):
    from server import VendorCIMDProxy

    async def boom(self, client_id):
        raise RuntimeError("secret detail\nforged line")

    monkeypatch.setattr(VendorCIMDProxy, "get_client", boom)
    with caplog.at_level(logging.DEBUG), TestClient(make(), base_url="https://frankbox.example.ts.net:8443") as c:
        codes = {c.post("/token", data={"grant_type": "authorization_code", "client_id": "x", "code": "c",
                                        "redirect_uri": "x", "code_verifier": "v" * 43}).status_code for _ in range(50)}
        body = c.post("/token", data={"grant_type": "authorization_code", "client_id": "x"}).text
    ours = [r for r in caplog.records if r.name == "fitness_mcp"]
    assert codes == {500} and body == "Internal Server Error"
    assert len(ours) == 1 and "RuntimeError" in ours[0].getMessage()
    assert "Traceback" not in caplog.text and "secret detail" not in caplog.text and "forged" not in caplog.text


@pytest.mark.parametrize("client_id", [
    "https://claude.ai:99999/x", "https://claude.ai:abc/x", "https://a..claude.ai/x", "https://" + "a" * 64 + ".claude.ai/x",
    "https://claude.ai/x\x00y", "https://claude.ai/x\x7fy", "https://claude.ai:8443/x",
])
def test_malformed_vendor_client_ids_are_refused_before_fastmcp_can_crash(make, monkeypatch, caplog, client_id):
    fetched = _fake_cimd(monkeypatch, "https://claude.ai/api/mcp/auth_callback")
    with caplog.at_level(logging.DEBUG), TestClient(make(), base_url="https://frankbox.example.ts.net:8443") as c:
        codes = {c.post("/token", data={"grant_type": "authorization_code", "client_id": client_id, "code": "c",
                                        "redirect_uri": "x", "code_verifier": "v" * 43}).status_code for _ in range(5)}
    assert codes == {401} and fetched == [] and not [r for r in caplog.records if r.name == "fitness_mcp"]


def test_a_fastmcp_error_while_fetching_a_vendor_document_is_a_refusal(make, monkeypatch, caplog):
    from fastmcp.server.auth.cimd import CIMDFetcher

    async def fetch(self, url):
        raise ValueError("crash inside FastMCP")

    monkeypatch.setattr(CIMDFetcher, "fetch", fetch)
    with caplog.at_level(logging.DEBUG), TestClient(make(), base_url="https://frankbox.example.ts.net:8443") as c:
        r = c.post("/token", data={"grant_type": "authorization_code", "client_id": "https://claude.ai/c.json",
                                   "code": "c", "redirect_uri": "x", "code_verifier": "v" * 43})
    assert r.status_code == 401 and not [x for x in caplog.records if x.name == "fitness_mcp"]


def test_days_window_is_inclusive_of_today_only_once(make, monkeypatch):
    from datetime import date, timedelta

    dates = [(date.today() - timedelta(days=i)).isoformat() for i in range(10)]
    monkeypatch.setitem(ROUTES, "/api/history-all", {"workouts": [], "recovery": [], "personal_records": {},
                                                     "cardio": [{"date": d, "activity_type": "walk"} for d in dates]})
    with TestClient(make(), base_url="http://127.0.0.1:8765") as c:
        one = call(c, "get_workout_history", BOT_TOKEN, days=1)["cardio"]
        seven = call(c, "get_workout_history", BOT_TOKEN, days=7)["cardio"]
    assert [r["date"] for r in one] == dates[:1] and [r["date"] for r in seven] == dates[:7]


def test_owner_id_must_be_ascii_digits(make):
    cfg = Config(
        public_url="https://frankbox.example.ts.net:8443/mcp", github_client_id="i", github_client_secret="s",
        owner_github_id="٤٢", jwt_signing_key=SIGNING_KEY, storage_dir="unused",
        storage_key=Fernet.generate_key().decode(), read_token=READ_TOKEN,
    )
    with pytest.raises(SystemExit):
        build_app(cfg)
