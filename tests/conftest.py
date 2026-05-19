from __future__ import annotations

import urllib.request

import pytest


@pytest.fixture(autouse=True)
def no_live_network(monkeypatch):
    """Fail tests that try to make uncassetted HTTP calls."""
    def blocked(*_args, **_kwargs):
        raise AssertionError("live network calls are blocked in tests")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
