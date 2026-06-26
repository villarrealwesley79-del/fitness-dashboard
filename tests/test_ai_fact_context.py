import importlib

from ai_fact_query import answer_fact_question, build_ai_fact_context


def test_ai_fact_question_answers_strength_history_from_sanitized_context():
    context = build_ai_fact_context(
        freshness={},
        wearable_sources=[],
        facts=[],
        history=[
            {"date": "2026-06-20", "canonical_category": "strength_training", "source_label": "Strength - Logged"},
            {"date": "2026-06-21", "canonical_category": "strength_training", "source_label": "Strength - Watch"},
        ],
    )
    answer = answer_fact_question("how much strength history do I have?", context).public_dict()
    assert "2 strength-training sessions" in answer["answer"]
    assert answer["evidence"][0]["source"] == "history"


def test_ai_fact_routes_exclude_raw_payload_words(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    response = module.app.test_client().get("/api/ai/facts/context")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    for forbidden in ("access_token", "refresh_token", "Authorization", "\"raw\""):
        assert forbidden not in body


def test_ai_suggestion_requires_approval_before_mutation(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(
        module,
        "_build_ai_public_fact_context",
        lambda: build_ai_fact_context(
            freshness={},
            wearable_sources=[{"label": "Open Wearables", "last_data_point": "2026-06-26", "status": "fresh"}],
            facts=[],
            history=[],
        ),
    )
    client = module.app.test_client()

    answer = client.post("/api/ai/facts/query", json={"question": "wearable sources?", "suggest": True}).get_json()
    suggestion_id = answer["suggested_action_id"]
    approved = client.post(f"/api/ai/suggestions/{suggestion_id}/approve", json={}).get_json()

    assert approved["suggestion"]["status"] == "approved"
    assert approved["suggestion"]["mutation_applied"] is False


def test_ai_fact_query_does_not_suggest_without_evidence(monkeypatch):
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(
        module,
        "_build_ai_public_fact_context",
        lambda: build_ai_fact_context(freshness={}, wearable_sources=[], facts=[], history=[]),
    )
    client = module.app.test_client()

    answer = client.post("/api/ai/facts/query", json={"question": "strength history?", "suggest": True}).get_json()

    assert answer["suggested_action_id"] is None
    assert "suggestion" not in answer
    assert answer["evidence"] == []
