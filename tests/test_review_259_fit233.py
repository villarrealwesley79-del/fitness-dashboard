"""Reviewer tests for PR #259 (FIT-233): read-only adaptation polling.

Written from PER-1255 acceptance criteria, independent of the builder's tests.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import importlib
import json
import sqlite3

import pytest

import data_store
import workout_adaptation

_WRITE_PREFIXES = ("INSERT", "UPDATE", "DELETE", "REPLACE", "ALTER", "DROP")


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "review-259-secret")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    monkeypatch.setattr(module, "_current_data_user_id", lambda: 1)
    monkeypatch.setattr(module, "_today_str", lambda: "2026-05-24")
    monkeypatch.setattr(module, "NUTRITION_DATA", [])
    monkeypatch.setattr(module, "save_json", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        module,
        "USER_SETTINGS",
        {"available_time_minutes": 35, "daily_calorie_target": 2200, "daily_protein_target_g": 150},
    )
    monkeypatch.setattr(module, "generate_next_workout", lambda *_a, **_kw: _recommendation())
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: "fp-review")
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", None)
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION_OWNER", None)
    return module, module.app.test_client()


def _recommendation():
    return {
        "id": "rec-review-259",
        "focus": "upper",
        "estimated_minutes": 60,
        "exercises": [
            {"machine": "Chest Press", "muscle_group": "chest", "target_sets": 4,
             "target_reps": 10, "time_per_set_minutes": 5},
            {"machine": "Lat Pulldown", "muscle_group": "back", "target_sets": 3,
             "target_reps": 10, "time_per_set_minutes": 5},
        ],
        "cardio": {"type": "walk", "duration_minutes": 15},
    }


def _add_coverage_breakfast():
    data_store.add_food_log(
        1,
        {
            "client_id": "review-259-breakfast", "date": "2026-05-24",
            "logged_at": "2026-05-24T08:00:00", "meal_id": "meal-review-259-breakfast",
            "item_name": "Breakfast", "calories": 690, "protein_g": 29.5, "carbs_g": 80,
            "fat_g": 25, "sodium_mg": 500, "confidence": 0.9, "correction_state": "accepted",
        },
    )


def _enqueue_due_snack(client_id="review-259-snack"):
    row = data_store.add_food_log(
        1,
        {
            "client_id": client_id,
            "date": "2026-05-24",
            "logged_at": "2026-05-24T12:00:00",
            "meal_id": f"meal-{client_id}",
            "item_name": "Small snack",
            "calories": 250, "protein_g": 5, "carbs_g": 40, "fat_g": 4,
            "sodium_mg": 200, "confidence": 0.9, "correction_state": "accepted",
        },
    )
    workout_adaptation.enqueue_accepted_food_logs(1, [row], clock=datetime(2026, 5, 24, 12, 0, 0))


def _trace_writes(monkeypatch):
    """Record every write statement issued on any data_store connection."""
    writes: list[str] = []
    real_connect = sqlite3.connect

    def connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        conn.set_trace_callback(
            lambda sql: writes.append(sql)
            if sql.lstrip().upper().startswith(_WRITE_PREFIXES)
            and "UPDATE food_logs" not in sql
            else None
        )
        return conn

    monkeypatch.setattr(data_store.sqlite3, "connect", connect)
    return writes


def _db_hash():
    with open(data_store.DATA_DB, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def test_repeated_get_polls_issue_no_dml_with_plan_events_and_pending(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    data_store.save_current_workout_plan(1, "fp-review", _recommendation())
    _add_coverage_breakfast()
    _enqueue_due_snack("review-259-first")
    evaluated = client.post("/api/workout-adaptation-events/evaluate")
    assert evaluated.status_code == 200
    assert evaluated.get_json()["evaluated_count"] == 1
    # A second, still-due window remains pending, and the stored plan exists.
    _enqueue_due_snack("review-259-second")
    assert data_store.get_current_workout_plan(1) is not None
    assert data_store.list_pending_workout_adaptation_windows(1)

    before = _db_hash()
    writes = _trace_writes(monkeypatch)
    for query in ("?unacknowledged=true", "?unacknowledged=false", "?limit=5",
                  "?active_workout_open=false", ""):
        resp = client.get(f"/api/workout-adaptation-events{query}")
        assert resp.status_code == 200

    assert writes == []
    assert _db_hash() == before


def test_get_poll_does_not_publish_unpublished_claim(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    data_store.save_current_workout_plan(1, "fp-review", _recommendation())
    _add_coverage_breakfast()
    _enqueue_due_snack()
    # Simulate a crash between claim and plan publication.
    with monkeypatch.context() as m:
        m.setattr(module, "_persist_current_workout_plan", lambda *a, **k: None)
        m.setattr(module, "_publish_unpublished_workout_adaptations", lambda *a, **k: [])
        client.post("/api/workout-adaptation-events/evaluate")
    assert len(data_store.list_unpublished_applied_workout_adaptation_events(1)) == 1

    before = _db_hash()
    with monkeypatch.context() as m:
        writes = _trace_writes(m)
        feed = client.get("/api/workout-adaptation-events?unacknowledged=true")
    assert feed.status_code == 200
    assert feed.get_json()["count"] == 0
    assert writes == []
    assert _db_hash() == before

    # Explicit POST recovers it and the feed then shows it.
    assert client.post("/api/workout-adaptation-events/evaluate").status_code == 200
    assert data_store.list_unpublished_applied_workout_adaptation_events(1) == []
    assert client.get("/api/workout-adaptation-events").get_json()["count"] == 1


def test_evaluate_post_requires_csrf_header(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    _enqueue_due_snack()
    pending_before = data_store.list_pending_workout_adaptation_windows(1)
    resp = client.post(
        "/api/workout-adaptation-events/evaluate",
        environ_overrides={"fitness_dashboard.omit_auto_csrf_header": True},
    )
    assert resp.status_code in (400, 403)
    assert data_store.list_pending_workout_adaptation_windows(1) == pending_before
    assert data_store.list_workout_adaptation_events(1, unacknowledged=False) == []


def test_evaluate_post_and_feed_require_login(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    module.app.config.update(LOGIN_DISABLED=False)
    try:
        _enqueue_due_snack()
        post = client.post("/api/workout-adaptation-events/evaluate")
        get = client.get("/api/workout-adaptation-events")
        assert post.status_code in (401, 302)
        assert get.status_code in (401, 302)
        assert data_store.list_workout_adaptation_events(1, unacknowledged=False) == []
    finally:
        module.app.config.update(LOGIN_DISABLED=True)


def test_swap_losing_race_returns_409_and_keeps_peer_plan(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    plan = {
        "id": "swap-base", "goal": module.TrainingGoal.HYPERTROPHY.value,
        "estimated_minutes": 45, "mesocycle": {"week": 1},
        "exercises": [{"exercise": "Chest Press", "muscle": "chest", "target_sets": 3,
                       "target_reps": 10, "target_weight": 100, "rpe_target": 7}],
    }
    data_store.save_current_workout_plan(1, "fp-review", plan)
    peer_plan = dict(plan, id="peer-plan")
    real_save = module.save_current_workout_plan

    def racing_save(user_id, fingerprint, plan, **kwargs):
        # A peer request publishes between this request's read and its CAS write.
        real_save(user_id, fingerprint, peer_plan)
        return real_save(user_id, fingerprint, plan, **kwargs)

    monkeypatch.setattr(module, "save_current_workout_plan", racing_save)
    resp = client.post(
        "/api/workout/swap",
        json={"recommendation_id": "swap-base", "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )
    stored = data_store.get_current_workout_plan(1)
    assert resp.status_code == 409, (resp.status_code, resp.get_json())
    assert stored["plan"]["id"] == "peer-plan"
    assert resp.status_code == 409, resp.get_json()
    assert resp.get_json()["error"]["code"] == "workout_plan_conflict"


def _legacy_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE workout_adaptation_events (
            id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, date TEXT NOT NULL,
            status TEXT NOT NULL, silent INTEGER NOT NULL DEFAULT 1,
            change_type TEXT NOT NULL, applies_to TEXT NOT NULL, reason TEXT,
            confidence_json TEXT, trigger_json TEXT, nutrition_context_json TEXT,
            patch_json TEXT, before_plan_json TEXT, after_plan_json TEXT,
            active_workout_json TEXT, reason_metadata_json TEXT,
            created_at TEXT NOT NULL, stale_at TEXT, acknowledged_at TEXT
        );
        CREATE TABLE current_workout_plans (
            user_id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL,
            plan_json TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO current_workout_plans VALUES (1, 'fp-legacy', ?, '2026-05-23T09:00:00')",
        (json.dumps(_recommendation()),),
    )
    conn.execute(
        "INSERT INTO workout_adaptation_events (id, user_id, date, status, silent, change_type, "
        "applies_to, reason, created_at) VALUES "
        "('legacy-applied', 1, '2026-05-23', 'applied', 0, 'changed', 'remaining', "
        "'legacy reason', '2026-05-23T12:00:00')"
    )
    conn.execute(
        "INSERT INTO workout_adaptation_events (id, user_id, date, status, silent, change_type, "
        "applies_to, reason, created_at) VALUES "
        "('legacy-nochange', 1, '2026-05-23', 'no_change', 1, 'none', 'none', "
        "'quiet', '2026-05-23T13:00:00')"
    )
    conn.commit()
    return conn


def test_migration_keeps_legacy_rows_and_is_write_free_afterwards(monkeypatch, tmp_path):
    db = tmp_path / "fitness_data.db"
    _legacy_db(db).close()
    monkeypatch.setattr(data_store, "DATA_DB", str(db))
    data_store.init_data_db()

    plan = data_store.get_current_workout_plan(1)
    assert plan["plan"] == _recommendation()
    assert plan["fingerprint"] == "fp-legacy"
    assert plan["plan_generation"] == 1
    events = {e["id"]: e for e in data_store.list_workout_adaptation_events(1, unacknowledged=False)}
    assert set(events) == {"legacy-applied", "legacy-nochange"}
    assert events["legacy-applied"]["reason"] == "legacy reason"
    assert events["legacy-applied"]["published_at"] == "2026-05-23T12:00:00"

    writes = _trace_writes(monkeypatch)
    data_store.init_data_db()
    data_store.init_data_db()
    assert writes == []


def test_interrupted_migration_does_not_rewrite_legacy_applied_event(monkeypatch, tmp_path):
    """Crash after ALTER ADD published_at but before its backfill UPDATE.

    The backfill is keyed on the column being absent, so it never re-runs; the
    legacy applied event is then treated as an unpublished claim.
    """
    db = tmp_path / "fitness_data.db"
    conn = _legacy_db(db)
    conn.execute("ALTER TABLE workout_adaptation_events ADD COLUMN published_at TEXT")
    conn.commit()
    conn.close()
    module, client = _client(monkeypatch, tmp_path)

    client.post("/api/workout-adaptation-events/evaluate")
    with sqlite3.connect(db) as raw:
        status, reason = raw.execute(
            "SELECT status, reason FROM workout_adaptation_events WHERE id = 'legacy-applied'"
        ).fetchone()
    assert (status, reason) == ("applied", "legacy reason")


def test_persisted_swap_survives_adaptation_evaluation(monkeypatch, tmp_path):
    module, client = _client(monkeypatch, tmp_path)
    plan = {
        "id": "swap-then-adapt", "goal": module.TrainingGoal.HYPERTROPHY.value,
        "estimated_minutes": 60, "mesocycle": {"week": 1},
        "exercises": [
            {"exercise": "Chest Press", "muscle": "chest", "target_sets": 4,
             "target_reps": 10, "target_weight": 100, "rpe_target": 7},
            {"exercise": "Lat Pulldown", "muscle": "back", "target_sets": 3,
             "target_reps": 10, "target_weight": 90, "rpe_target": 7},
        ],
    }
    monkeypatch.setattr(module, "generate_next_workout", lambda *_a, **_kw: json.loads(json.dumps(plan)))
    assert client.get("/api/next-workout").status_code == 200
    swapped = client.post(
        "/api/workout/swap",
        json={"recommendation_id": "swap-then-adapt", "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )
    assert swapped.status_code == 200
    _add_coverage_breakfast()
    _enqueue_due_snack()

    evaluated = client.post("/api/workout-adaptation-events/evaluate")
    assert evaluated.status_code == 200
    assert evaluated.get_json()["evaluated_count"] == 1
    feed = client.get("/api/workout-adaptation-events").get_json()
    assert [e["status"] for e in feed["events"]] == ["applied"]

    after = client.get("/api/next-workout")
    assert after.status_code == 200
    names = [e.get("exercise") for e in after.get_json()["next_workout"]["exercises"]]
    assert "Incline Press" in names
    assert "Chest Press" not in names
    stored = data_store.get_current_workout_plan(1)["plan"]
    assert "Incline Press" in [e.get("exercise") for e in stored["exercises"]]


def test_migration_backfill_leaves_unpublished_claims_unpublished(monkeypatch, tmp_path):
    """The re-runnable backfill must only publish legacy rows, never a new-shape claim."""
    db = tmp_path / "fitness_data.db"
    _legacy_db(db).close()
    monkeypatch.setattr(data_store, "DATA_DB", str(db))
    data_store.init_data_db()
    with sqlite3.connect(db) as raw:
        raw.execute(
            "INSERT INTO workout_adaptation_events (id, user_id, date, status, silent, change_type, "
            "applies_to, created_at, adapted_plan_json) VALUES "
            "('claim', 1, '2026-05-24', 'applied', 0, 'changed', 'remaining', "
            "'2026-05-24T12:00:00', '{}')"
        )
    data_store.init_data_db()
    with sqlite3.connect(db) as raw:
        rows = dict(raw.execute("SELECT id, published_at FROM workout_adaptation_events").fetchall())
    assert rows["claim"] is None
    assert rows["legacy-applied"] == "2026-05-23T12:00:00"
