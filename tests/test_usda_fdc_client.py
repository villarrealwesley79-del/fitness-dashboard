from __future__ import annotations

import json
import urllib.parse
import urllib.request

import usda_fdc_client


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_usda_search_skips_network_without_key(monkeypatch):
    monkeypatch.delenv("USDA_FDC_API_KEY", raising=False)

    def fail_urlopen(*_args, **_kwargs):
        raise AssertionError("USDA lookup must not call the network without a key")

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)
    assert usda_fdc_client.search_foods("banana") is None


def test_usda_search_adds_optional_api_key(monkeypatch):
    monkeypatch.setenv("USDA_FDC_API_KEY", "fdc-key")
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: _Response({"foods": []}))

    usda_fdc_client.search_foods("oatmeal")
    # Re-run with capture for clarity after autouse network guard has been replaced.
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        return _Response({"foods": []})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    usda_fdc_client.search_foods("oatmeal")
    params = urllib.parse.parse_qs(urllib.parse.urlparse(captured["url"]).query)
    assert params["api_key"] == ["fdc-key"]
    assert params["query"] == ["oatmeal"]
    assert params["dataType"] == ["Foundation,SR Legacy"]


def test_usda_handles_network_errors(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("down")))
    assert usda_fdc_client.search_foods("banana") is None
