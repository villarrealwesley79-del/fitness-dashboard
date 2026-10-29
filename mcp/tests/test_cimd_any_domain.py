"""PER-1585 (owner choice): MCP_EXTRA_CIMD_HOSTS=* lets a CIMD client_id come from any public host.

Everything else stays: https, port 443, plain ASCII, FastMCP's SSRF layer, the redirect allowlist,
the owner-only login and the bot token.
"""
import dataclasses

import pytest
from fastmcp.server.auth.cimd import CIMDDocument, CIMDFetcher
from starlette.testclient import TestClient

import server
from server import VendorCIMDProxy
from test_review_r4 import CLAUDE_CB, PUBLIC
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


@pytest.mark.parametrize("client_id", [
    "https://127.0.0.1/c.json", "https://10.1.2.3/c.json", "https://[::1]/c.json", "https://localhost/c.json",
    "https://169.254.169.254/c.json",
])
def test_star_still_refuses_private_addresses_through_fastmcp_ssrf_layer(any_domain, monkeypatch, client_id):
    calls = []
    real = CIMDFetcher.fetch

    async def spy(self, url):
        calls.append(url)
        return await real(self, url)  # the real fetcher: its SSRF check must be what refuses

    monkeypatch.setattr(CIMDFetcher, "fetch", spy)
    with TestClient(any_domain(), base_url=PUBLIC, follow_redirects=False, raise_server_exceptions=False) as c:
        codes = {authorize(c, client_id).status_code,
                 c.post("/token", data={"grant_type": "authorization_code", "client_id": client_id, "code": "c",
                                        "redirect_uri": CLAUDE_CB, "code_verifier": "v" * 43}).status_code}
    assert calls and codes <= {400, 401}, (calls, codes)  # reached FastMCP, refused there, never a 500


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
