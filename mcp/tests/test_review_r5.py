"""Reviewer round 5 (head 9f8f8a6): the CIMD vendor-host allowlist, the counted window with /consent,
and the owner verifier's handling of degraded GitHub answers.

Written from the requirements before trusting the builder's tests:
- frankbox may fetch a CIMD document only from a vendor host, whatever spelling the client_id uses;
- an anonymous request to an uncounted endpoint (/token) must never write to the log, so it must never 500;
- a redirect from a vendor host must not be followed;
- a flood of the counted endpoints must not stop the owner's /token refresh;
- the route set must match what the counted window assumes, so a FastMCP upgrade cannot add an uncounted route silently;
- no GitHub answer except "a numeric id that is not the owner's" may be cached as refused.
"""
import asyncio
import base64
import hashlib
import re
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fastmcp.server.auth import ssrf
from starlette.testclient import TestClient

from server import CIMD_HOSTS, LogRejectedRedirects
from test_review_r4 import CLAUDE_CB, PUBLIC, FakeGitHub, counting_app, verifier_with
from test_server import GH_OWNER, OWNER_ID, make, seen  # noqa: F401


def is_vendor(host):
    return any(host == h or host.endswith("." + h) for h in CIMD_HOSTS)


def token_post(c, client_id, grant="authorization_code"):
    return c.post("/token", data={"grant_type": grant, "client_id": client_id, "code": "c", "refresh_token": "r",
                                  "redirect_uri": CLAUDE_CB, "code_verifier": "v" * 43})


# --- BLOCKER evidence: a malformed vendor-host client_id escapes get_client as a 500 -----------

MALFORMED_VENDOR = [
    "https://claude.ai:99999/x",           # urlparse().port raises ValueError (ssrf.py:307, outside its try)
    "https://claude.ai:abc/x",
    "https://a..claude.ai/x",              # getaddrinfo idna encode raises UnicodeError (only gaierror is caught)
    "https://" + "a" * 64 + ".claude.ai/x",
]


@pytest.mark.parametrize("client_id", MALFORMED_VENDOR)
@pytest.mark.parametrize("grant", ["authorization_code", "refresh_token"])
def test_uncounted_token_endpoint_never_500s_on_a_malformed_vendor_client_id(make, client_id, grant):
    """Live at 9f8f8a6: each such POST /token is a 500 and uvicorn writes an 80-97 line traceback
    ('ERROR: Exception in ASGI application', ~7-8.5 KB). /token is not counted, so the log grows without bound."""
    with TestClient(make(), base_url=PUBLIC, raise_server_exceptions=False) as c:
        r = token_post(c, client_id, grant)
    assert r.status_code in (400, 401), (r.status_code, r.text[:120])


@pytest.mark.parametrize("client_id", MALFORMED_VENDOR[:1] + MALFORMED_VENDOR[2:3])
def test_authorize_never_500s_on_a_malformed_vendor_client_id(make, client_id):
    with TestClient(make(), base_url=PUBLIC, raise_server_exceptions=False, follow_redirects=False) as c:
        r = c.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": CLAUDE_CB,
                                        "code_challenge": "x" * 43, "code_challenge_method": "S256", "state": "s"})
    assert r.status_code in (400, 401), r.status_code


# --- the fetch target is always a vendor host -------------------------------------------------

EITHER = "either"  # fetching this vendor host or refusing the id are both safe
SPELLINGS = [  # (client_id, hostname FastMCP would resolve, or None for no fetch at all)
    ("https://claude.ai/c.json", "claude.ai"),
    ("https://CLAUDE.AI/c.json", "claude.ai"),
    ("https://mcp.Anthropic.com/c.json", "mcp.anthropic.com"),
    ("https://claude.ai:443/c.json", "claude.ai"),
    ("https://claude.ai:8443/c.json", EITHER),       # a vendor host; refusing non-443 ports is also fine
    ("https://evil.example\\@claude.ai/c.json", "claude.ai"),   # both parsers agree the host is claude.ai
    ("https://evil.example\t.claude.ai/c.json", EITHER),  # urllib drops the tab: still a claude.ai name
    ("https://evil.example/c.json", None),
    ("https://claude.ai.evil.example/c.json", None),
    ("https://evilclaude.ai/c.json", None),
    ("https://claude.ai@evil.example/c.json", None),
    ("https://user:pw@evil.example/c.json", None),
    ("https://claude.ai\\@evil.example/c.json", None),
    ("https://claude.ai%40evil.example/c.json", None),
    ("https://evil.example%2eclaude.ai/c.json", None),
    ("https://evil.example#@claude.ai/c.json", None),
    ("https://evil.example?@claude.ai/c.json", None),
    ("https://claude.ai./c.json", None),
    ("https://127.0.0.1/c.json", None),
    ("https://[::1]/c.json", None),
    ("https://2130706433/c.json", None),
    ("https://xn--claude-ai.example/c.json", None),
    ("https://claude.ａｉ/c.json", None),          # fullwidth "ai"
    ("https://evil。claude.ai/c.json", None),          # ideographic full stop
    ("https://evil.example．claude.ai/c.json", None),   # fullwidth full stop
    ("https://x.ai.evil/c.json", None),
    ("https://evilx.ai/c.json", None),
    ("https://grok.com.evil/c.json", None),
    ("http://claude.ai/c.json", None),                      # not https: not a CIMD id
    ("https://claude.ai", None),
    ("https://claude.ai/", None),
]


def test_every_cimd_spelling_resolves_only_a_vendor_host(make, monkeypatch):
    resolved = []

    async def record(hostname, port):
        resolved.append(hostname)
        raise ssrf.SSRFError("reviewer: no network")

    monkeypatch.setattr(ssrf, "resolve_hostname", record)
    got = {}
    with TestClient(make(), base_url=PUBLIC, raise_server_exceptions=False) as c:
        for client_id, _ in SPELLINGS:
            resolved.clear()
            r = token_post(c, client_id)
            got[client_id] = (r.status_code, list(resolved))
    wrong = {cid: got[cid] for cid, host in SPELLINGS
             if host != EITHER and got[cid] != (401, [host] if host else [])}
    assert not wrong, wrong
    assert all(is_vendor(h) for _, hosts in got.values() for h in hosts)


# --- a vendor host that redirects is not followed ---------------------------------------------

class RecordingHttpx:
    """Stands in for the httpx2 module inside fastmcp's ssrf helper; every client gets a mock transport."""

    def __init__(self, handler):
        self.requests, self._handler = [], handler

    def __getattr__(self, name):
        return getattr(httpx2, name)

    def AsyncClient(self, **kwargs):  # noqa: N802
        def handle(request):
            self.requests.append((str(request.url), request.headers.get("host")))
            return self._handler(request)

        return httpx2.AsyncClient(**{**kwargs, "transport": httpx2.MockTransport(handle)})


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_vendor_redirect_is_not_followed_and_the_client_is_refused(make, monkeypatch, status):
    url = "https://claude.ai/oauth/client.json"

    async def resolve(hostname, port):
        return ["104.18.32.47"]  # a global address, so the SSRF check lets the fetch go ahead

    def handler(request):
        if status == 200:
            return httpx2.Response(200, json={"client_id": url, "client_name": "t", "redirect_uris": [CLAUDE_CB],
                                              "token_endpoint_auth_method": "none"})
        return httpx2.Response(status, headers={"Location": "https://evil.example/c.json"})

    fake = RecordingHttpx(handler)
    monkeypatch.setattr(ssrf, "resolve_hostname", resolve)
    monkeypatch.setattr(ssrf, "httpx2", fake)
    with TestClient(make(), base_url=PUBLIC, follow_redirects=False) as c:
        r = c.get("/authorize", params={"response_type": "code", "client_id": url, "redirect_uri": CLAUDE_CB,
                                        "code_challenge": "x" * 43, "code_challenge_method": "S256", "state": "s"})
    assert [host for _, host in fake.requests] == ["claude.ai"]  # one request, to the vendor; no second hop
    assert r.status_code == 400


def test_redirect_harness_positive_control(make, monkeypatch):
    """Same harness with a 200 document: the client is accepted, so the refusal above is not a harness artefact."""
    url = "https://claude.ai/oauth/client.json"

    async def resolve(hostname, port):
        return ["104.18.32.47"]

    fake = RecordingHttpx(lambda request: httpx2.Response(200, json={
        "client_id": url, "client_name": "t", "redirect_uris": [CLAUDE_CB], "token_endpoint_auth_method": "none"}))
    monkeypatch.setattr(ssrf, "resolve_hostname", resolve)
    monkeypatch.setattr(ssrf, "httpx2", fake)
    with TestClient(make(), base_url=PUBLIC, follow_redirects=False) as c:
        r = c.get("/authorize", params={"response_type": "code", "client_id": url, "redirect_uri": CLAUDE_CB,
                                        "code_challenge": "x" * 43, "code_challenge_method": "S256", "state": "s"})
    assert fake.requests and r.status_code == 302, (r.status_code, r.text[:200])


# --- the owner's refresh survives a flood of every counted endpoint ---------------------------

def login_tokens(app, github_token, github=None):
    """The r4 login flow, returning the whole /token response (access and refresh) and the client id."""
    c = TestClient(app, base_url=PUBLIC, follow_redirects=False)
    client_id = c.post("/register", json={
        "redirect_uris": [CLAUDE_CB], "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]}).json()["client_id"]
    verifier = "reviewer-pkce-verifier-" + "v" * 30
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    consent = c.get("/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": CLAUDE_CB, "code_challenge": challenge,
        "code_challenge_method": "S256", "state": "st", "scope": "read:user offline_access",
        "resource": PUBLIC + "/mcp"}).headers["location"]
    fields = dict(re.findall(r'name="(txn_id|csrf_token|submit)" value="([^"]*)"', c.get(consent).text))
    state = parse_qs(urlsplit(c.post(consent, data={**fields, "action": "approve"}).headers["location"]).query)["state"][0]
    app.state.fastmcp_auth.server._create_upstream_oauth_client = lambda: (github or FakeGitHub)(github_token)
    code = parse_qs(urlsplit(c.get(f"/auth/callback?code=0123456789abcdef0123&state={state}").headers["location"]).query)["code"][0]
    tok = c.post("/token", data={"grant_type": "authorization_code", "code": code, "redirect_uri": CLAUDE_CB,
                                 "client_id": client_id, "code_verifier": verifier})
    assert tok.status_code == 200, tok.text
    return c, client_id, tok.json()


class ExpiringGitHub(FakeGitHub):
    """A GitHub App with expiring user tokens: the case where the owner's client really refreshes via /token."""

    async def fetch_token(self, url, **params):
        return {**await super().fetch_token(url, **params), "refresh_token": "ghr_reviewer", "expires_in": 28800}

    async def refresh_token(self, url, **params):
        return {"access_token": GH_OWNER, "token_type": "bearer", "scope": "read:user",
                "refresh_token": "ghr_reviewer_2", "expires_in": 28800}


def refresh(c, client_id, refresh_token):
    return c.post("/token", data={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id})


def test_owner_refresh_still_works_after_every_counted_endpoint_is_flooded(tmp_path):
    app = counting_app(tmp_path, [])
    c, client_id, tok = login_tokens(app, GH_OWNER, ExpiringGitHub)
    assert tok.get("refresh_token"), tok
    flood = [c.get("/consent", params={"txn_id": "x"}).status_code for _ in range(60)]
    flood += [c.get("/authorize").status_code for _ in range(10)]
    flood += [c.post("/register", json={}).status_code for _ in range(10)]
    flood += [c.get("/auth/callback?code=a&state=b").status_code for _ in range(10)]
    assert flood[-30:] == [429] * 30  # the window is exhausted for all four paths
    r = refresh(c, client_id, tok["refresh_token"])
    assert r.status_code == 200, r.text  # /token is outside the window
    assert r.json()["access_token"] != tok["access_token"]


# --- the route set the counted window was designed for ----------------------------------------

def test_route_inventory_matches_the_counted_window(make):
    app = make()
    paths = {getattr(r, "path", None) for r in app.routes}
    anonymous = {"/.well-known/oauth-authorization-server", "/.well-known/oauth-protected-resource/mcp",
                 "/authorize", "/token", "/register", "/auth/callback", "/consent"}
    assert paths == anonymous | {"/mcp"}, paths  # a FastMCP upgrade that adds a route must be reviewed
    # Every anonymous route that can write the store or FastMCP's ERROR log is counted; /token and
    # the metadata documents are deliberately not.
    assert anonymous - set(LogRejectedRedirects.COUNTED) == {
        "/token", "/.well-known/oauth-authorization-server", "/.well-known/oauth-protected-resource/mcp"}


# --- degraded GitHub answers are never cached as refused --------------------------------------

@pytest.mark.parametrize("body", [
    b"[]", b'"424242"', b"not json", b'{"id": true}', b'{"id": 424242.0}',
    '{"id": "٤٢٤٢٤٢"}'.encode(),  # Arabic-Indic digits: isdigit() but not ASCII
    b'{"id": "-1"}', b'{"id": " 424242"}',
])
def test_odd_github_200_bodies_do_not_raise_or_lock_the_owner_out(body):
    answers = iter([httpx2.Response(200, content=body, headers={"content-type": "application/json"}),
                    httpx2.Response(200, json={"id": int(OWNER_ID)})])
    v = verifier_with(lambda request: next(answers))
    assert asyncio.run(v.verify_token(GH_OWNER)) is None  # no exception, no owner
    assert asyncio.run(v.verify_token(GH_OWNER)) is not None  # and the next good answer lets the owner in


# --- other ways a vendor-host fetch can fail must also be a clean refusal on /token -------------

VENDOR = "https://claude.ai/oauth/client.json"
ODD = [  # (client_id, vendor response)
    ("https://claude.ai/x\x00y", httpx2.Response(404)),
    ("https://claude.ai/x\x7fy", httpx2.Response(404)),
    ("https://claude.ai/x y", httpx2.Response(404)),
    ("https://claude.ai/x y", httpx2.Response(404)),
    (VENDOR, httpx2.Response(200, json={"hello": "world"})),
    (VENDOR, httpx2.Response(200, content=b"<html>not json</html>")),
    (VENDOR, httpx2.Response(200, json=["a"])),
    (VENDOR, httpx2.Response(200, json={"client_id": "https://claude.ai/other.json", "redirect_uris": [CLAUDE_CB]})),
    (VENDOR, httpx2.Response(200, json={"client_id": VENDOR, "redirect_uris": [CLAUDE_CB], "token_endpoint_auth_method": "client_secret_post"})),
    (VENDOR, httpx2.Response(200, json={"client_id": VENDOR, "redirect_uris": [CLAUDE_CB], "token_endpoint_auth_method": "private_key_jwt", "jwks_uri": "https://evil.example/jwks"})),
    (VENDOR, httpx2.Response(200, content=b"{" * 5000)),
    (VENDOR, httpx2.Response(200, content=b"x" * 6000)),
]


@pytest.mark.parametrize("client_id,response", ODD, ids=[f"odd{i}" for i in range(len(ODD))])
def test_uncounted_token_never_500s_on_odd_vendor_paths_or_documents(make, monkeypatch, client_id, response):
    async def resolve(hostname, port):
        return ["104.18.32.47"]

    fake = RecordingHttpx(lambda request: response)
    monkeypatch.setattr(ssrf, "resolve_hostname", resolve)
    monkeypatch.setattr(ssrf, "httpx2", fake)
    with TestClient(make(), base_url=PUBLIC, raise_server_exceptions=False) as c:
        r = token_post(c, client_id)
    assert r.status_code in (400, 401), (r.status_code, r.text[:120])
    assert all(host == "claude.ai" for _, host in fake.requests), fake.requests
