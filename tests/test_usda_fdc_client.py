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
    assert params["dataType"] == ["Branded,Foundation,SR Legacy"]


def test_usda_barcode_search_limits_to_branded_foods(monkeypatch):
    monkeypatch.setenv("USDA_FDC_API_KEY", "fdc-key")
    captured = {}

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        return _Response({"foods": []})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    usda_fdc_client.search_foods_by_barcode("0123-4567-8905")

    params = urllib.parse.parse_qs(urllib.parse.urlparse(captured["url"]).query)
    assert params["api_key"] == ["fdc-key"]
    assert params["query"] == ["012345678905"]
    assert params["dataType"] == ["Branded"]


def test_usda_handles_network_errors(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_kw: (_ for _ in ()).throw(OSError("down")))
    assert usda_fdc_client.search_foods("banana") is None


def test_usda_text_lookup_uses_first_complete_candidate_not_first_row(monkeypatch):
    import branded_food_lookup

    monkeypatch.setattr(branded_food_lookup.data_store, "get_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.data_store, "save_branded_lookup_cache", lambda *_a, **_kw: None)
    monkeypatch.setattr(branded_food_lookup.nutritionix_client, "natural_nutrients", lambda *_a, **_kw: None)
    incomplete = {
        "fdcId": 101,
        "description": "BROKEN BANANA",
        "foodNutrients": [{"nutrientName": "Energy", "unitName": "KCAL", "value": 89}],
    }
    complete = {
        "fdcId": 202,
        "description": "BANANAS,RAW",
        "foodNutrients": [
            {"nutrientName": "Energy", "unitName": "KCAL", "value": 89},
            {"nutrientName": "Protein", "value": 1.1},
            {"nutrientName": "Carbohydrate, by difference", "value": 22.8},
            {"nutrientName": "Total lipid (fat)", "value": 0.3},
        ],
    }
    monkeypatch.setattr(
        branded_food_lookup.usda_fdc_client,
        "search_foods",
        lambda *_a, **_kw: {"foods": [incomplete, complete]},
    )

    estimate = branded_food_lookup.lookup("banana", source_priority=("usda_fdc",))

    assert estimate["external_food_id"] == "202"
    assert estimate["item_name"] == "BANANAS,RAW"


def test_usda_barcode_lookup_skips_schema_invalid_complete_candidate(monkeypatch):
    import branded_food_lookup

    def candidate(fdc_id, calories):
        return {
            "fdcId": fdc_id,
            "gtinUpc": "012345678905",
            "description": f"CANDIDATE {fdc_id}",
            "foodNutrients": [
                {"nutrientName": "Energy", "unitName": "KCAL", "value": calories},
                {"nutrientName": "Protein", "value": 4},
                {"nutrientName": "Carbohydrate, by difference", "value": 30},
                {"nutrientName": "Total lipid (fat)", "value": 8},
            ],
        }

    monkeypatch.setattr(
        branded_food_lookup.usda_fdc_client,
        "search_foods_by_barcode",
        lambda *_a, **_kw: {
            "foods": [
                {"fdcId": 100, "gtinUpc": "012345678905", "foodNutrients": None},
                candidate(101, "invalid"),
                candidate(202, 200),
            ]
        },
    )

    estimate = branded_food_lookup._usda_barcode_lookup("012345678905")

    assert estimate["external_food_id"] == "202"
