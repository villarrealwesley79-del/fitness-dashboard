from __future__ import annotations

import importlib

import personal_vocab


def _estimate(**overrides):
    base = {
        "item_name": "Chipotle chicken burrito",
        "portion_description": "1 burrito",
        "meal_type": "lunch",
        "calories": 1075,
        "protein_g": 51,
        "carbs_g": 116,
        "fat_g": 41,
        "sodium_mg": 2310,
        "fiber_g": 13,
        "confidence": 0.85,
        "ambiguous": False,
        "uncertainty_notes": [],
        "source": "nutritionix",
        "external_food_id": "chipotle-burrito",
    }
    base.update(overrides)
    return base


def test_personal_vocab_learns_after_three_accepts(tmp_path, monkeypatch):
    import data_store

    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()

    assert personal_vocab.lookup("chip ckn bur", user_id=1) is None
    for _ in range(3):
        personal_vocab.record_accept(1, "chip ckn bur", _estimate())

    result = personal_vocab.lookup("chip ckn bur", user_id=1)

    assert result["source"] == "personal_vocab"
    assert result["underlying_source"] == "nutritionix"
    assert result["confidence"] == 0.9
    assert result["item_name"] == "Chipotle chicken burrito"
    assert result["external_food_id"] == "chipotle-burrito"


def test_personal_vocab_correction_resets_mapping(tmp_path, monkeypatch):
    import data_store

    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    for _ in range(3):
        personal_vocab.record_accept(1, "chip usual", _estimate())
    assert personal_vocab.lookup("chip usual", user_id=1)

    personal_vocab.record_correct(1, "chip usual", _estimate(item_name="Chipotle bowl"))

    assert personal_vocab.lookup("chip usual", user_id=1) is None
    entry = data_store.get_personal_vocab_entry(1, "chip usual")
    assert entry["accept_count"] == 0
    assert entry["correct_count"] == 1


def test_personal_vocab_fuzzy_match_after_one_confirmed_mapping(tmp_path, monkeypatch):
    import data_store

    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    personal_vocab.record_accept(1, "chipotle chicken burrito", _estimate())

    result = personal_vocab.lookup("chipotle chicken burito", user_id=1)

    assert result["source"] == "personal_vocab"
    assert result["item_name"] == "Chipotle chicken burrito"


def test_parse_meal_text_consults_personal_vocab_before_branded_lookup(monkeypatch):
    parser = importlib.import_module("meal_text_parser")
    personal = _estimate(source="personal_vocab", confidence=0.9)
    monkeypatch.setattr(parser.personal_vocab, "lookup", lambda text: personal)
    monkeypatch.setattr(
        parser.branded_food_lookup,
        "lookup",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("branded lookup must not run")),
    )

    result = parser.parse_meal_text("chip usual")

    assert result == {"estimate": personal, "fallback_used": False}


def test_accept_endpoint_records_personal_vocab(monkeypatch, tmp_path):
    import app
    import data_store

    monkeypatch.setenv("SECRET_KEY", "fit74-secret")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    app.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(app, "_current_data_user_id", lambda: 1)
    monkeypatch.setattr(app, "add_food_log", lambda _uid, record: {"client_id": record["client_id"], **record})

    res = app.app.test_client().post(
        "/api/meal-intake/meal-vocab-1/accept",
        json={"estimate": _estimate(), "text": "chip ckn bur"},
    )

    assert res.status_code == 200
    entry = data_store.get_personal_vocab_entry(1, "chip ckn bur")
    assert entry["accept_count"] == 1
    assert entry["canonical_resolution"]["item_name"] == "Chipotle chicken burrito"
