"""PER-1585 (owner choice): MCP_EXTRA_CIMD_HOSTS=* lets a CIMD client_id come from any public host.

Everything else stays: https, port 443, plain ASCII, FastMCP's SSRF layer, the redirect allowlist,
the owner-only login and the bot token.
"""
import dataclasses
import socket

import httpx2
import pytest
from fastmcp.server.auth import ssrf
from fastmcp.server.auth.cimd import CIMDDocument, CIMDFetcher
from starlette.testclient import TestClient

import server
from server import VendorCIMDProxy
from test_review_r4 import CLAUDE_CB, PUBLIC
from test_review_r5 import RecordingHttpx
from test_server import BOT_TOKEN, GH_OTHER, GH_OWNER, make, oauth_token, rpc, seen  # noqa: F401

STRANGER = "https://agent.example.org/client.json"


def proxy(hosts):
    p = VendorCIMDProxy.__new__(VendorCIMDProxy)
    p._cimd_hosts = hosts
    return p


@pytest.fixture
def any_domain(make, monkeypatch):  # noqa: F811
    """`make` with MCP_EXTRA_CIMD_HOSTS=* (the config path, not a code default)."""
    import test_server

    real = test_server.build_app
    monkeypatch.setattr(test_server, "build_app",
                        lambda cfg, **kw: real(dataclasses.replace(cfg, extra_cimd_hosts=("*",)), **kw))
    return make


def fake_doc(monkeypatch, redirect_uri):
    """Replace only the HTTP fetch; keep a list of what FastMCP was asked to fetch."""
    fetched = []

    async def fetch(self, url):
        fetched.append(url)
        return CIMDDocument(client_id=url, client_name="Totally Claude", redirect_uris=[redirect_uri])

    monkeypatch.setattr(CIMDFetcher, "fetch", fetch)
    return fetched


def authorize(c, client_id, redirect_uri=CLAUDE_CB):
    return c.get("/authorize", params={"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
                                       "code_challenge": "x" * 43, "code_challenge_method": "S256", "state": "s"})


def test_star_allows_an_unlisted_public_host_and_its_absence_refuses_it():
    assert proxy((*server.CIMD_HOSTS, "*"))._fetchable(STRANGER)
    assert not proxy(server.CIMD_HOSTS)._fetchable(STRANGER)


@pytest.mark.parametrize("client_id", [
    "https://agent.example.org:8443/c.json", "https://agent.example.org:99999/c.json", "https://agent.example.org/c\x00.json",
    "https://agent.example.org/c\x7f.json", "https://ägent.example.org/c.json", "https://a..example.org/c.json",
    "https://" + "a" * 64 + ".example.org/c.json", "https://:443/c.json", "https://agent.example.org/c.json ",
])
def test_star_keeps_every_other_check(client_id):
    assert not proxy(("*",))._fetchable(client_id)


HOST = "agent.example.org"
PUBLIC_IP = "93.184.216.34"
NON_GLOBAL = [
    ["127.0.0.1"], ["10.0.0.5"], ["192.168.1.10"], ["169.254.169.254"], ["100.101.102.103"],  # Tailscale CGNAT
    ["fd7a:115c:a1e0::1"], ["::1"], ["::ffff:127.0.0.1"], ["64:ff9b::a00:5"], ["224.0.0.1"], ["0.0.0.0"],
    [PUBLIC_IP, "10.0.0.5"],  # one bad answer is enough
]


def fake_dns_and_doc(monkeypatch, ips):
    """Mock DNS only (the real SSRF check runs on these answers) and serve a valid CIMD document over a recording
    transport: if the SSRF layer let the request through, /authorize would answer 302."""
    def getaddrinfo(host, port, *args, **kwargs):
        if host != HOST:
            raise socket.gaierror(socket.EAI_NONAME, "no such name")
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    def handler(request):
        return httpx2.Response(200, json={"client_id": STRANGER, "client_name": "Claude", "redirect_uris": [CLAUDE_CB],
                                          "token_endpoint_auth_method": "none"})

    fake, errors, real = RecordingHttpx(handler), [], ssrf.validate_url

    async def spy(url, require_path=False):
        try:
            return await real(url, require_path=require_path)
        except ssrf.SSRFError as e:
            errors.append(str(e))
            raise

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(ssrf, "httpx2", fake)
    monkeypatch.setattr(ssrf, "validate_url", spy)
    return fake, errors


@pytest.mark.parametrize("ips", NON_GLOBAL, ids=[",".join(i) for i in NON_GLOBAL])
def test_star_name_resolving_to_a_non_global_ip_is_refused_by_the_ssrf_layer_before_any_connection(
        any_domain, monkeypatch, ips):
    fake, errors = fake_dns_and_doc(monkeypatch, ips)
    with TestClient(any_domain(), base_url=PUBLIC, follow_redirects=False, raise_server_exceptions=False) as c:
        a = authorize(c, STRANGER)
        t = c.post("/token", data={"grant_type": "authorization_code", "client_id": STRANGER, "code": "c",
                                   "redirect_uri": CLAUDE_CB, "code_verifier": "v" * 43})
    assert fake.requests == []  # no connection attempted
    assert errors and all("blocked IP" in e for e in errors), errors  # the SSRF layer is what refused
    assert a.status_code == 400 and t.status_code in (400, 401), (a.status_code, t.status_code)  # never a 500


def test_star_public_answer_is_fetched_pinned_to_the_ip_and_accepted(any_domain, monkeypatch):
    """Positive control for the test above: same harness, a global address."""
    fake, errors = fake_dns_and_doc(monkeypatch, [PUBLIC_IP])
    with TestClient(any_domain(), base_url=PUBLIC, follow_redirects=False) as c:
        r = authorize(c, STRANGER)
    assert errors == [] and fake.requests == [(f"https://{PUBLIC_IP}/client.json", HOST)]
    assert r.status_code == 302 and "/consent" in r.headers["location"]


def test_unlisted_host_cannot_name_an_unlisted_redirect_uri(any_domain, monkeypatch):
    """Answer for the PR: the redirect allowlist still refuses it at /authorize."""
    evil = "https://agent.example.org/cb"
    fetched = fake_doc(monkeypatch, evil)
    with TestClient(any_domain(), base_url=PUBLIC, follow_redirects=False) as c:
        r = authorize(c, STRANGER, evil)
    assert fetched == [STRANGER] and r.status_code == 400 and "consent" not in r.headers.get("location", "")


def test_consent_page_shows_the_client_host_even_when_the_document_claims_to_be_claude(any_domain, monkeypatch):
    fake_doc(monkeypatch, CLAUDE_CB)  # an allowlisted callback is the only way past the redirect check
    with TestClient(any_domain(), base_url=PUBLIC, follow_redirects=False) as c:
        r = authorize(c, STRANGER)
        assert r.status_code == 302
        page = c.get(r.headers["location"]).text
    assert "Verified domain: <strong>agent.example.org</strong>" in page.replace("\n", " ").replace("  ", "")


def test_star_keeps_the_owner_lock_and_the_bot_token(any_domain, seen):  # noqa: F811
    app = any_domain()
    with TestClient(app, base_url="http://127.0.0.1:8765") as c:
        owner, other = oauth_token(app, GH_OWNER), oauth_token(app, GH_OTHER)
        assert rpc(c, "tools/list", token=owner).status_code == 200
        assert rpc(c, "tools/list", token=BOT_TOKEN).status_code == 200
        assert rpc(c, "tools/list", token=other).status_code == 401
