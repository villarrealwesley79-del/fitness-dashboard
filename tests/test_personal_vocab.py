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


def test_personal_vocab_exact_match_waits_for_accept_threshold(tmp_path, monkeypatch):
    import data_store

    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    personal_vocab.record_accept(1, "chip ckn bur", _estimate())

    assert personal_vocab.lookup("chip ckn bur", user_id=1) is None


def test_personal_vocab_untrusted_exact_match_blocks_fuzzy_substitution(tmp_path, monkeypatch):
    import data_store

    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    personal_vocab.record_accept(1, "chipotle chicken bowl", _estimate(item_name="Chipotle chicken bowl"))
    personal_vocab.record_accept(1, "chipotle chicken burrito", _estimate(item_name="Chipotle chicken burrito"))

    assert personal_vocab.lookup("chipotle chicken bowl", user_id=1) is None


def test_personal_vocab_fuzzy_match_rejects_different_meal_tokens(tmp_path, monkeypatch):
    import data_store

    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    personal_vocab.record_accept(1, "chipotle chicken bowl", _estimate(item_name="Chipotle chicken bowl"))
    personal_vocab.record_accept(1, "chipotle chicken burrito", _estimate(item_name="Chipotle chicken burrito"))

    assert personal_vocab.lookup("chipotle chicken burrito shared", user_id=1) is None
    assert personal_vocab.lookup("chipotle chicken bowl no rice", user_id=1) is None
    assert personal_vocab.lookup("chipotle chicken burrito", user_id=1) is None


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


def test_personal_vocab_correction_can_recover_after_new_accepts(tmp_path, monkeypatch):
    import data_store

    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    for _ in range(3):
        personal_vocab.record_accept(1, "chip usual", _estimate())
    personal_vocab.record_correct(1, "chip usual", _estimate(item_name="Chipotle bowl"))

    corrected = _estimate(item_name="Chipotle bowl", calories=680)
    for _ in range(3):
        personal_vocab.record_accept(1, "chip usual", corrected)

    result = personal_vocab.lookup("chip usual", user_id=1)
    entry = data_store.get_personal_vocab_entry(1, "chip usual")
    assert entry["correct_count"] == 0
    assert result["item_name"] == "Chipotle bowl"
    assert result["calories"] == 680


def test_personal_vocab_fuzzy_match_after_one_confirmed_mapping(tmp_path, monkeypatch):
    import data_store

    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    personal_vocab.record_accept(1, "chipotle chicken burrito", _estimate())

    result = personal_vocab.lookup("chipotle chicken burito", user_id=1)

    assert result["source"] == "personal_vocab"
    assert result["item_name"] == "Chipotle chicken burrito"


def test_personal_vocab_matches_abbreviated_phrase_after_one_confirmed_mapping(tmp_path, monkeypatch):
    import data_store

    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    personal_vocab.record_accept(1, "chipotle chicken burrito", _estimate())

    result = personal_vocab.lookup("chip ckn bur", user_id=1)

    assert result["source"] == "personal_vocab"
    assert result["item_name"] == "Chipotle chicken burrito"


def test_parse_meal_text_consults_personal_vocab_before_branded_lookup(monkeypatch):
    parser = importlib.import_module("meal_text_parser")
    personal = _estimate(source="personal_vocab", confidence=0.9)
    monkeypatch.setattr(parser.personal_vocab, "lookup", lambda text, **_kw: personal)
    monkeypatch.setattr(
        parser.branded_food_lookup,
        "lookup",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("branded lookup must not run")),
    )

    result = parser.parse_meal_text("chip usual")

    assert result == {"estimate": personal, "fallback_used": False}


def test_parse_meal_text_scopes_personal_vocab_to_user_id(monkeypatch):
    parser = importlib.import_module("meal_text_parser")
    personal = _estimate(source="personal_vocab", confidence=0.9)
    seen = {}

    def fake_lookup(text, **kwargs):
        seen["text"] = text
        seen["user_id"] = kwargs.get("user_id")
        return personal if kwargs.get("user_id") == 2 else None

    monkeypatch.setattr(parser.personal_vocab, "lookup", fake_lookup)
    monkeypatch.setattr(
        parser.branded_food_lookup,
        "lookup",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("branded lookup must not run")),
    )

    result = parser.parse_meal_text("chip usual", user_id=2)

    assert seen == {"text": "chip usual", "user_id": 2}
    assert result == {"estimate": personal, "fallback_used": False}


def test_accept_endpoint_records_personal_vocab(monkeypatch, tmp_path):
    import app
    import data_store

    monkeypatch.setenv("SECRET_KEY", "fit74-secret")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    app.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(app, "_current_data_user_id", lambda: 1)

    res = app.app.test_client().post(
        "/api/meal-intake/meal-vocab-1/accept",
        json={"estimate": _estimate(), "text": "chip ckn bur"},
    )

    assert res.status_code == 200
    entry = data_store.get_personal_vocab_entry(1, "chip ckn bur")
    assert entry["accept_count"] == 1
    assert entry["canonical_resolution"]["item_name"] == "Chipotle chicken burrito"


def test_accept_endpoint_records_edited_pending_estimate_as_correction(monkeypatch, tmp_path):
    import app
    import data_store

    monkeypatch.setenv("SECRET_KEY", "fit74-secret")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    app.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(app, "_current_data_user_id", lambda: 1)
    captured = {}
    monkeypatch.setattr(app, "add_food_log", lambda _uid, record: (captured.update(record), {"client_id": record["client_id"], **record})[1])
    monkeypatch.setattr(app, "claim_food_log_vocab_learning", lambda *_a, **_kw: True)
    calls = {"accept": 0, "correct": 0}
    monkeypatch.setattr(app.personal_vocab, "record_accept", lambda *_a, **_kw: calls.__setitem__("accept", calls["accept"] + 1))
    monkeypatch.setattr(app.personal_vocab, "record_correct", lambda *_a, **_kw: calls.__setitem__("correct", calls["correct"] + 1))
    original = _estimate(calories=1075)
    edited = _estimate(calories=900)

    res = app.app.test_client().post(
        "/api/meal-intake/meal-vocab-corrected/accept",
        json={"estimate": edited, "original_estimate": original, "text": "chip ckn bur"},
    )

    assert res.status_code == 200
    assert calls == {"accept": 0, "correct": 1}
    assert captured["correction_state"] == "corrected"
    assert captured["original_estimate"]["calories"] == 1075
    assert captured["calories"] == 900


def test_accept_endpoint_vocab_learning_is_idempotent_by_client_id(monkeypatch, tmp_path):
    import app
    import data_store

    monkeypatch.setenv("SECRET_KEY", "fit74-secret")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    app.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(app, "_current_data_user_id", lambda: 1)
    payload = {"estimate": _estimate(), "text": "chip ckn bur"}

    first = app.app.test_client().post("/api/meal-intake/meal-vocab-retry/accept", json=payload)
    second = app.app.test_client().post("/api/meal-intake/meal-vocab-retry/accept", json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    entry = data_store.get_personal_vocab_entry(1, "chip ckn bur")
    assert entry["accept_count"] == 1


def test_repeated_edited_accepts_keep_personal_vocab_untrusted(monkeypatch, tmp_path):
    import app
    import data_store

    monkeypatch.setenv("SECRET_KEY", "fit74-secret")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    app.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(app, "_current_data_user_id", lambda: 1)
    original = _estimate(item_name="Chipotle chicken bowl", calories=700)
    edited = _estimate(item_name="Chipotle chicken burrito", calories=1075)
    client = app.app.test_client()

    for idx in range(3):
        res = client.post(
            f"/api/meal-intake/meal-vocab-corrected-{idx}/accept",
            json={"estimate": edited, "original_estimate": original, "text": "chip ckn bur"},
        )
        assert res.status_code == 200

    assert personal_vocab.lookup("chip ckn bur", user_id=1) is None
    entry = data_store.get_personal_vocab_entry(1, "chip ckn bur")
    assert entry["accept_count"] == 0
    assert entry["correct_count"] == 3


def test_meal_intake_preserves_personal_vocab_provenance(monkeypatch, tmp_path):
    import app
    import data_store

    monkeypatch.setenv("SECRET_KEY", "fit74-secret")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    app.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(app, "_current_data_user_id", lambda: 1)
    captured = {}
    monkeypatch.setattr(app, "add_food_log", lambda _uid, record: (captured.update(record), {"client_id": record["client_id"], **record})[1])
    estimate = _estimate(
        source="personal_vocab",
        confidence=0.95,
        underlying_source="nutritionix",
        personal_vocab_phrase="chip usual",
    )
    monkeypatch.setattr(app, "parse_meal_text", lambda *_a, **_kw: {"estimate": estimate, "fallback_used": False})

    res = app.app.test_client().post(
        "/api/meal-intake",
        data={"text": "chip usual", "client_id": "meal-personal-provenance"},
        content_type="multipart/form-data",
    )

    body = res.get_json()
    assert res.status_code == 200
    assert body["estimate"]["source"] == "personal_vocab"
    assert body["estimate"]["underlying_source"] == "nutritionix"
    assert body["estimate"]["personal_vocab_phrase"] == "chip usual"
    assert captured["original_estimate"]["underlying_source"] == "nutritionix"
    assert captured["original_estimate"]["personal_vocab_phrase"] == "chip usual"


def test_auto_logged_meals_do_not_train_personal_vocab_before_explicit_accept(monkeypatch, tmp_path):
    import app
    import data_store

    monkeypatch.setenv("SECRET_KEY", "fit74-secret")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    app.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(app, "_current_data_user_id", lambda: 1)
    monkeypatch.setattr(app, "add_food_log", lambda _uid, record: {"client_id": record["client_id"], **record})
    calls = {"accept": 0}
    monkeypatch.setattr(app.personal_vocab, "record_accept", lambda *_a, **_kw: calls.__setitem__("accept", calls["accept"] + 1))
    monkeypatch.setattr(app, "parse_meal_text", lambda *_a, **_kw: {"estimate": _estimate(confidence=0.95), "fallback_used": False})

    res = app.app.test_client().post(
        "/api/meal-intake",
        data={"text": "chip ckn bur", "client_id": "meal-auto-vocab"},
        content_type="multipart/form-data",
    )

    assert res.status_code == 200
    assert res.get_json()["status"] == "logged"
    assert calls == {"accept": 0}
