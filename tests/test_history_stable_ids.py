from __future__ import annotations

import copy

import pytest

import app as fitness_app


@pytest.fixture(autouse=True)
def authenticated_test_app():
    previous = dict(fitness_app.app.config)
    fitness_app.app.config.update(TESTING=True, LOGIN_DISABLED=True)
    yield
    fitness_app.app.config.clear()
    fitness_app.app.config.update(previous)


@pytest.fixture
def isolated_history(monkeypatch, tmp_path):
    workouts = [
        {"id": "workout-old", "date": "2026-01-01", "exercises": []},
        {"id": "workout-new", "date": "2026-01-03", "exercises": []},
    ]
    cardio = [
        {"id": "cardio-old", "date": "2026-01-01", "activity_type": "Walk"},
        {"id": "cardio-new", "date": "2026-01-03", "activity_type": "Bike"},
    ]
    recovery = [
        {"id": "recovery-old", "date": "2026-01-01", "recovery_type": "Sauna"},
        {"id": "recovery-new", "date": "2026-01-03", "recovery_type": "Stretch"},
    ]
    monkeypatch.setattr(fitness_app, "WORKOUTS", workouts)
    monkeypatch.setattr(fitness_app, "CARDIO_DATA", cardio)
    monkeypatch.setattr(fitness_app, "RECOVERY_DATA", recovery)
    monkeypatch.setattr(fitness_app, "WORKOUTS_FILE", str(tmp_path / "workouts.json"))
    monkeypatch.setattr(fitness_app, "CARDIO_FILE", str(tmp_path / "cardio.json"))
    monkeypatch.setattr(fitness_app, "RECOVERY_FILE", str(tmp_path / "recovery.json"))
    return {"workout": workouts, "cardio": cardio, "recovery": recovery}


def test_history_rows_expose_stable_ids_for_every_entry_type(isolated_history):
    payload = fitness_app.app.test_client().get("/api/history-all").get_json()

    assert [row["id"] for row in payload["workouts"]] == ["workout-new", "workout-old"]
    assert [row["id"] for row in payload["cardio"]] == ["cardio-new", "cardio-old"]
    assert [row["id"] for row in payload["recovery"]] == ["recovery-new", "recovery-old"]


def test_legacy_history_rows_are_backfilled_once_and_keep_the_same_ids(isolated_history):
    for rows in isolated_history.values():
        rows[0].pop("id")
    client = fitness_app.app.test_client()

    first = client.get("/api/history-all").get_json()
    second = client.get("/api/history-all").get_json()
    workout_only = client.get("/api/history").get_json()

    for key in ("workouts", "cardio", "recovery"):
        assert all(isinstance(row["id"], str) and row["id"] for row in first[key])
        assert [row["id"] for row in second[key]] == [row["id"] for row in first[key]]
    assert [row["id"] for row in workout_only["workouts"]] == [
        row["id"] for row in first["workouts"]
    ]


@pytest.mark.parametrize("entry_type", ["workout", "cardio", "recovery"])
def test_delete_targets_stable_id_after_history_reorders(isolated_history, entry_type):
    rows = isolated_history[entry_type]
    expected = copy.deepcopy(rows[0])
    rows.insert(0, {"id": f"{entry_type}-raced", "date": "2026-01-04"})

    response = fitness_app.app.test_client().post(
        "/api/delete-history",
        json={"type": entry_type, "id": expected["id"]},
    )

    assert response.status_code == 200
    assert response.get_json()["deleted"] == expected
    assert all(row["id"] != expected["id"] for row in rows)
    assert any(row["id"] == f"{entry_type}-raced" for row in rows)


@pytest.mark.parametrize("entry_type", ["workout", "cardio", "recovery"])
def test_undo_restores_exact_deleted_payload(isolated_history, entry_type):
    client = fitness_app.app.test_client()
    entry_id = f"{entry_type}-new"
    deleted = client.post(
        "/api/delete-history",
        json={"type": entry_type, "id": entry_id},
    ).get_json()["deleted"]

    response = client.post(
        "/api/restore-history",
        json={"type": entry_type, "entry": deleted},
    )

    assert response.status_code == 200
    assert response.get_json()["restored"] == deleted
    assert isolated_history[entry_type][-1] == deleted


@pytest.mark.parametrize("entry_type", ["workout", "cardio", "recovery"])
def test_delete_rejects_unknown_stable_id_without_mutating_history(isolated_history, entry_type):
    before = copy.deepcopy(isolated_history[entry_type])

    response = fitness_app.app.test_client().post(
        "/api/delete-history",
        json={"type": entry_type, "id": "missing-id"},
    )

    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"
    assert isolated_history[entry_type] == before


def test_delete_requires_stable_id_instead_of_sorted_index(isolated_history):
    response = fitness_app.app.test_client().post(
        "/api/delete-history",
        json={"type": "workout", "index": 0},
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "missing_field"
    assert [row["id"] for row in isolated_history["workout"]] == ["workout-old", "workout-new"]


@pytest.mark.parametrize("entry_type", ["workout", "cardio", "recovery"])
def test_restore_rejects_payload_without_stable_id(isolated_history, entry_type):
    response = fitness_app.app.test_client().post(
        "/api/restore-history",
        json={"type": entry_type, "entry": {"date": "2026-01-05"}},
    )

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "missing_field"


def test_new_cardio_and_recovery_rows_receive_stable_ids(monkeypatch, tmp_path):
    monkeypatch.setattr(fitness_app, "CARDIO_DATA", [])
    monkeypatch.setattr(fitness_app, "RECOVERY_DATA", [])
    monkeypatch.setattr(fitness_app, "CARDIO_FILE", str(tmp_path / "cardio.json"))
    monkeypatch.setattr(fitness_app, "RECOVERY_FILE", str(tmp_path / "recovery.json"))
    client = fitness_app.app.test_client()

    cardio = client.post(
        "/api/add-cardio",
        json={"activity_type": "Walk", "duration_minutes": 20},
    ).get_json()["cardio"]
    recovery = client.post(
        "/api/add-recovery",
        json={"recovery_type": "Stretch", "duration_minutes": 15},
    ).get_json()["recovery"]

    assert isinstance(cardio["id"], str) and cardio["id"]
    assert isinstance(recovery["id"], str) and recovery["id"]


def test_history_ui_deletes_by_id_and_undoes_with_returned_payload():
    source = (fitness_app.os.path.dirname(fitness_app.__file__) + "/static/js/app.js")
    with open(source, encoding="utf-8") as handle:
        javascript = handle.read()

    assert "JSON.stringify({ type: 'workout', id: workout.id })" in javascript
    assert "restoreDeletedWorkout(deletedEntry)" in javascript
    assert "index: workout._origIndex" not in javascript
