from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
import urllib.request

import branded_food_lookup


def test_snapshot_lookup_works_without_network_or_api_keys(monkeypatch):
    monkeypatch.delenv("NUTRITIONIX_APP_ID", raising=False)
    monkeypatch.delenv("NUTRITIONIX_APP_KEY", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("network blocked")))

    result = branded_food_lookup.lookup("bananas", source_priority=("snapshot", "nutritionix", "usda_fdc"))

    assert result["source"] == "offline_snapshot"
    assert result["item_name"] == "Banana, raw"
    assert result["calories"] == 89
    assert result["external_food_id"] == "173944"


def test_snapshot_file_is_small_and_documents_license():
    path = Path("data/nutrition_snapshot.json")
    payload = json.loads(path.read_text())

    assert path.stat().st_size < 5 * 1024 * 1024
    assert "USDA" in payload["license"]
    assert "No Nutritionix-derived data" in payload["license"]
    assert len(payload["items"]) >= 5


def test_refresh_script_dry_run_does_not_write():
    path = Path("data/nutrition_snapshot.json")
    before = path.read_text()
    result = subprocess.run(
        [sys.executable, "scripts/refresh_nutrition_snapshot.py"],
        check=True,
        capture_output=True,
        text=True,
    )
    after = path.read_text()
    payload = json.loads(result.stdout)

    assert payload["status"] == "dry_run"
    assert payload["writes"] is False
    assert after == before
