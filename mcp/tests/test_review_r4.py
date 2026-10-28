"""Reviewer round 4 (head 7782e5a): callback pre-validation and rate window, the refused-token
cache, the cardio projection, and a full OAuth login through the real /auth/callback.

Written from the requirements before trusting the builder's tests:
- no anonymous request shape may get an unbounded number of FastMCP ERROR lines into the log;
- no client-supplied text may start a new log line;
- a real GitHub callback must still complete the login;
- the refused cache may never hold the owner, nor any GitHub failure.
"""
import asyncio
import base64
import hashlib
import logging
import re
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from cryptography.fernet import Fernet
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

import test_server as ts
from owner_auth import OwnerGitHubVerifier
from server import Config, LogRejectedRedirects, build_app
from test_server import BOT_TOKEN, GH_OTHER, GH_OWNER, OTHER_ID, OWNER_ID, call, make, rpc, seen  # noqa: F401

BASE = "http://127.0.0.1:8765"
PUBLIC = "https://frankbox.example.ts.net:8443"
CLAUDE_CB = "https://claude.ai/api/mcp/auth_callback"


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append((record.levelno, record.getMessage()))


@pytest.fixture
def fastmcp_log():
    logger = logging.getLogger("fastmcp")
    handler, old = Capture(), logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield handler
    logger.removeHandler(handler)
    logger.setLevel(old)


def errors(log):
    return [m for level, m in log.records if level >= logging.ERROR]


# --- callback: path and method variants cannot reach FastMCP's ERROR log uncounted ------------

@pytest.mark.parametrize("path", [
    "/auth/callback/", "//auth/callback", "/auth//callback", "/AUTH/CALLBACK", "/auth/callback/.",
    "/auth/callback%20", "/mcp/../auth/callback",
])
def test_callback_path_variants_never_reach_the_handler(make, fastmcp_log, path):
    with TestClient(make(), base_url=BASE, follow_redirects=False) as c:
        for _ in range(5):
            r = c.get(path + "?error=" + "A" * 3000 + "%0Aforged&state=x")
            assert r.status_code != 200
    assert not [m for m in errors(fastmcp_log) if "IdP callback" in m], (path, errors(fastmcp_log)[:2])


def test_percent_encoded_callback_path_is_counted_and_validated(make, fastmcp_log):
    # uvicorn and the TestClient decode %63 to "c" in scope["path"]; the router and the guard see the same path
    with TestClient(make(), base_url=BASE) as c:
        r = c.get("/auth/%63allback?error=a%0Ab")
    assert r.status_code == 400 and r.text == "Bad request"
    assert not errors(fastmcp_log)


@pytest.mark.parametrize("method", ["HEAD", "GET"])
def test_head_and_get_are_both_prevalidated(make, fastmcp_log, method):
    with TestClient(make(), base_url=BASE) as c:
        long = c.request(method, "/auth/callback", params={"error": "x", "error_description": "A" * 2000})
        newline = c.request(method, "/auth/callback", params={"error": "a\r\nb"})
    assert (long.status_code, newline.status_code) == (400, 400)
    assert not errors(fastmcp_log)


def test_post_to_callback_is_405_and_logs_nothing(make, fastmcp_log):
    with TestClient(make(), base_url=BASE) as c:
        r = c.post("/auth/callback?error=x", data={"error": "y"})
    assert r.status_code == 405
    assert not errors(fastmcp_log)


def test_mixed_flood_of_shapes_and_methods_is_bounded(make, fastmcp_log):
    shapes = [
        ("GET", "/auth/callback?code=abc&state=bad{i}"),
        ("HEAD", "/auth/callback?code=abc&state=bad{i}"),
        ("GET", "/auth/callback?error=e{i}"),
        ("GET", "/auth/%63allback?state=s{i}&code=c"),
        ("GET", "/auth/callback/?code=abc&state=bad{i}"),
        ("POST", "/auth/callback?code=abc&state=bad{i}"),
    ]
    with TestClient(make(), base_url=BASE, follow_redirects=True) as c:
        for i in range(60):
            for method, url in shapes:
                c.request(method, url.format(i=i))
    kept = errors(fastmcp_log)
    assert len(kept) <= 60, f"{len(kept)} ERROR records from 360 anonymous requests"


@pytest.mark.parametrize("encoded", ["%0A", "%0d", "%00", "%7F", "%09", "%1b", "+%0A+"])
def test_encoded_control_characters_are_refused(make, fastmcp_log, encoded):
    with TestClient(make(), base_url=BASE) as c:
        r = c.get(f"/auth/callback?code=abc&state=x{encoded}y")
    assert r.status_code == 400 and r.text == "Bad request"
    assert not errors(fastmcp_log)


def test_query_size_boundary_is_exactly_1024_bytes(make, fastmcp_log):
    def query(n):
        head = "code=abc&state="
        return head + "s" * (n - len(head))

    with TestClient(make(), base_url=BASE) as c:
        at_limit = c.get("/auth/callback?" + query(1024))
        over = c.get("/auth/callback?" + query(1025))
    assert at_limit.status_code == 400 and "<html" in at_limit.text.lower()  # reached FastMCP (unknown txn)
    assert over.status_code == 400 and over.text == "Bad request"


def test_double_encoded_newline_reaches_the_log_only_as_text(make, fastmcp_log):
    with TestClient(make(), base_url=BASE) as c:
        c.get("/auth/callback?code=abc&state=a%250Ab")
    assert errors(fastmcp_log) and not [m for m in errors(fastmcp_log) if "\n" in m or "\r" in m]


@pytest.mark.parametrize("encoded", ["%C2%85", "%E2%80%A8", "%E2%80%A9", "%0B", "%0C"])
def test_unicode_line_separators_do_not_reach_fastmcp_log(make, fastmcp_log, encoded):
    """str.splitlines() (and editors such as TextEdit) break lines on NEL, U+2028 and U+2029."""
    with TestClient(make(), base_url=BASE) as c:
        c.get(f"/auth/callback?code=abc&state=a{encoded}2026-10-28 fitness_mcp WARNING /register refused: x")
    assert not [m for m in errors(fastmcp_log) if len(m.splitlines()) > 1], errors(fastmcp_log)


def test_callback_shares_the_window_with_register(caplog):
    async def ok(scope, receive, send):
        await PlainTextResponse("ok")(scope, receive, send)

    guard = TestClient(LogRejectedRedirects(ok, limit=3), base_url=BASE)
    codes = [guard.get("/auth/callback?code=a&state=b").status_code for _ in range(3)]
    codes += [guard.post("/register").status_code, guard.get("/authorize").status_code]
    assert codes == [200, 200, 200, 429, 429]


# --- a real login still works through the guarded callback -----------------------------------

class FakeGitHub:
    def __init__(self, access_token):
        self.access_token = access_token

    async def fetch_token(self, url, **params):
        assert url == "https://github.com/login/oauth/access_token" and params["code"] == "0123456789abcdef0123"
        return {"access_token": self.access_token, "token_type": "bearer", "scope": "read:user"}

    async def aclose(self):
        pass


def login(app, github_token):
    """Register, authorize, approve consent, take GitHub's callback, swap the code. Returns the access token."""
    c = TestClient(app, base_url=PUBLIC, follow_redirects=False)
    client_id = c.post("/register", json={
        "redirect_uris": [CLAUDE_CB], "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]}).json()["client_id"]
    verifier = "reviewer-pkce-verifier-" + "v" * 30
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    consent = c.get("/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": CLAUDE_CB, "code_challenge": challenge,
        "code_challenge_method": "S256", "state": "client-state", "scope": "read:user",
        "resource": PUBLIC + "/mcp"}).headers["location"]
    page = c.get(consent)
    fields = dict(re.findall(r'name="(txn_id|csrf_token|submit)" value="([^"]*)"', page.text))
    to_github = urlsplit(c.post(consent, data={**fields, "action": "approve"}).headers["location"])
    assert to_github.netloc == "github.com"
    state = parse_qs(to_github.query)["state"][0]
    app.state.fastmcp_auth.server._create_upstream_oauth_client = lambda: FakeGitHub(github_token)
    back = c.get(f"/auth/callback?code=0123456789abcdef0123&state={state}")  # GitHub's real callback shape
    assert back.status_code == 302, back.text[:300]
    to_client = urlsplit(back.headers["location"])
    assert f"{to_client.scheme}://{to_client.netloc}{to_client.path}" == CLAUDE_CB
    query = parse_qs(to_client.query)
    assert query["state"] == ["client-state"]
    token = c.post("/token", data={
        "grant_type": "authorization_code", "code": query["code"][0], "redirect_uri": CLAUDE_CB,
        "client_id": client_id, "code_verifier": verifier})
    assert token.status_code == 200, token.text
    return token.json()["access_token"]


def counting_app(tmp_path, calls):
    def github(request):
        calls.append(request.headers.get("authorization"))
        user = {f"Bearer {GH_OWNER}": OWNER_ID, f"Bearer {GH_OTHER}": OTHER_ID}.get(request.headers.get("authorization"))
        return httpx2.Response(200, json={"id": int(user)}) if user else httpx2.Response(401)

    cfg = Config(
        public_url=PUBLIC + "/mcp", github_client_id="gh-client-id", github_client_secret=ts.CLIENT_SECRET,
        owner_github_id=OWNER_ID, jwt_signing_key=ts.SIGNING_KEY, storage_dir=str(tmp_path / "store"),
        storage_key=Fernet.generate_key().decode(), read_token=ts.READ_TOKEN,
    )
    app, _ = build_app(cfg, github=httpx2.AsyncClient(transport=httpx2.MockTransport(github)),
                       dashboard_transport=httpx2.MockTransport(lambda r: httpx2.Response(200, json=ts.ROUTES.get(r.url.path, {}))))
    return app


def test_owner_login_through_the_real_callback_reaches_the_tools(tmp_path):
    calls = []
    app = counting_app(tmp_path, calls)
    access = login(app, GH_OWNER)
    with TestClient(app, base_url=BASE) as c:
        assert rpc(c, "tools/list", token=access).status_code == 200
        assert call(c, "get_today_plan", access)["recommendation"] == "moderate"


def test_non_owner_login_completes_but_every_method_is_refused_with_one_github_check(tmp_path):
    calls = []
    app = counting_app(tmp_path, calls)
    access = login(app, GH_OTHER)
    with TestClient(app, base_url=BASE) as c:
        codes = [rpc(c, m, token=access).status_code for m in ("tools/list", "tools/list", "resources/list", "ping")]
    assert codes == [401, 401, 401, 401]
    assert calls.count(f"Bearer {GH_OTHER}") == 1  # the refused cache holds it


# --- the refused cache --------------------------------------------------------------------------

def verifier_with(handler):
    return OwnerGitHubVerifier(OWNER_ID, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))


@pytest.mark.parametrize("first", [
    lambda r: httpx2.Response(500), lambda r: httpx2.Response(502), lambda r: httpx2.Response(503),
    lambda r: httpx2.Response(401), lambda r: httpx2.Response(403, json={"message": "rate limit"}),
    lambda r: httpx2.Response(429),
])
def test_github_errors_are_never_cached_as_refused(first):
    answers = iter([first, lambda r: httpx2.Response(200, json={"id": int(OWNER_ID)})])
    calls = []

    def handler(request):
        calls.append(1)
        return next(answers)(request)

    v = verifier_with(handler)
    assert asyncio.run(v.verify_token(GH_OWNER)) is None
    assert asyncio.run(v.verify_token(GH_OWNER)).subject == OWNER_ID
    assert len(calls) == 2 and not v._refused._entries


@pytest.mark.parametrize("exc", [httpx2.ConnectError("down"), httpx2.ReadTimeout("slow")])
def test_network_errors_are_never_cached_as_refused(exc):
    state = {"fail": True}

    def handler(request):
        if state.pop("fail", False):
            raise exc
        return httpx2.Response(200, json={"id": int(OWNER_ID)})

    v = verifier_with(handler)
    assert asyncio.run(v.verify_token(GH_OWNER)) is None
    assert asyncio.run(v.verify_token(GH_OWNER)).subject == OWNER_ID
    assert not v._refused._entries


def test_owner_is_never_in_the_refused_cache_and_a_full_refused_cache_spares_the_owner():
    calls = []

    def handler(request):
        calls.append(request.headers["authorization"])
        is_owner = request.headers["authorization"] == f"Bearer {GH_OWNER}"
        return httpx2.Response(200, json={"id": int(OWNER_ID) if is_owner else 7})

    v = verifier_with(handler)

    async def run():
        assert (await v.verify_token(GH_OWNER)).subject == OWNER_ID
        for i in range(100):  # overflow the 64-entry refused cache
            assert await v.verify_token(f"gho_stranger_{i}") is None
        assert (await v.verify_token(GH_OWNER)).subject == OWNER_ID

    asyncio.run(run())
    assert calls.count(f"Bearer {GH_OWNER}") == 1
    assert v._refused._hash_token(GH_OWNER) not in v._refused._entries
    assert len(v._refused._entries) <= 64


def test_refused_entry_expires(monkeypatch):
    import fastmcp.utilities.token_cache as tc

    calls = []
    v = verifier_with(lambda r: calls.append(1) or httpx2.Response(200, json={"id": 7}))
    now = [1_000_000.0]
    monkeypatch.setattr(tc.time, "time", lambda: now[0])
    assert asyncio.run(v.verify_token(GH_OTHER)) is None
    assert asyncio.run(v.verify_token(GH_OTHER)) is None
    now[0] += 301
    assert asyncio.run(v.verify_token(GH_OTHER)) is None
    assert len(calls) == 2


# --- cardio projection against the dashboard's real shapes (app.py:4031-4066) -----------------

@pytest.mark.parametrize("cardio,expected", [
    ({"type": "Stairmaster", "duration_minutes": 15, "zone": "Zone 2", "zone_description": "Fat Burning Zone",
      "heart_rate_range": "114-133 BPM", "intensity": "Steady", "technique": "Light grip", "reason": "Burn"},
     {"type": "Stairmaster", "duration_minutes": 15, "zone": "Zone 2", "heart_rate_range": "114-133 BPM",
      "intensity": "Steady", "reason": "Burn"}),
    ({"include_cardio": False, "reason": "No time for cardio today"},
     {"include_cardio": False, "reason": "No time for cardio today"}),
    (None, {}),
])
def test_cardio_projection_matches_the_dashboard_shapes(make, monkeypatch, cardio, expected):
    smart = ts.ROUTES["/api/recommendation/smart"]
    monkeypatch.setitem(ts.ROUTES, "/api/recommendation/smart",
                        {**smart, "next_workout": {**smart["next_workout"], "cardio": cardio}})
    with TestClient(make(), base_url=BASE) as c:
        assert call(c, "get_today_plan", BOT_TOKEN)["next_workout"]["cardio"] == expected


# --- anonymous POST /token with a CIMD client_id: FastMCP fetches the URL, httpx2 logs it -------

MAIN_PROBE = r"""
import json, logging, sys
sys.path.insert(0, sys.argv[1])
import uvicorn, server
seen = {}
uvicorn.run = lambda app, **kw: seen.update(kw)
server.Config.from_env = classmethod(lambda cls, env=None: __import__("types").SimpleNamespace(host="127.0.0.1", port=0))
server.build_app = lambda cfg: (None, "/mcp")
server.main()
print(json.dumps({"httpx2_info": logging.getLogger("httpx2").isEnabledFor(logging.INFO),
                  "fitness_warning": logging.getLogger("fitness_mcp").isEnabledFor(logging.WARNING),
                  "uvicorn_log_level": seen.get("log_level")}))
"""


def _main_logging():
    import json
    import subprocess
    import sys
    from pathlib import Path

    mcp_dir = str(Path(__file__).resolve().parents[1])
    out = subprocess.run([sys.executable, "-c", MAIN_PROBE, mcp_dir], capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_production_logging_drops_httpx2_request_lines():
    """Live run: each anonymous POST /token with client_id=https://<attacker>/<30 KB path> made FastMCP fetch
    that URL and wrote a 30 KB 'httpx2 INFO HTTP Request: GET ...' line. /token is not rate limited."""
    facts = _main_logging()
    assert facts["fitness_warning"] is True  # the owner still needs the 'refused' lines
    assert facts["httpx2_info"] is False, facts


def test_production_logging_drops_uvicorn_per_request_warnings():
    """Live run: every malformed request adds 'WARNING: Invalid HTTP request received.' (uvicorn.error)."""
    assert _main_logging()["uvicorn_log_level"] in ("error", "critical")


@pytest.mark.parametrize("degraded", [{}, {"login": "owner"}, {"id": None}, {"id": ""}])
def test_a_degraded_github_200_does_not_lock_the_owner_out(degraded):
    """A 200 with no usable id says nothing about who owns the token; caching it as refused would
    refuse the owner for the cache TTL (300 s) after GitHub recovers."""
    answers = iter([httpx2.Response(200, json=degraded), httpx2.Response(200, json={"id": int(OWNER_ID)})])
    v = verifier_with(lambda request: next(answers))
    assert asyncio.run(v.verify_token(GH_OWNER)) is None
    assert asyncio.run(v.verify_token(GH_OWNER)) is not None


@pytest.mark.parametrize("host", ["evil.example", "frankbox.example.ts.net.evil.example"])
def test_slash_redirect_never_points_at_a_foreign_host(make, host):
    with TestClient(make(), base_url=BASE, follow_redirects=False) as c:
        r = c.get("/auth/callback/?code=a&state=b", headers={"Host": host})
    assert host not in r.headers.get("location", ""), (r.status_code, r.headers.get("location"))
