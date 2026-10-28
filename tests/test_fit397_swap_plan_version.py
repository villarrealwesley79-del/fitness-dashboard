from __future__ import annotations

import copy
import importlib
from datetime import datetime as real_datetime
from pathlib import Path

import data_store
from js_runtime import run_app_js


def _plan(module, plan_id: str = "fit397-plan"):
    return {
        "id": plan_id,
        "goal": module.TrainingGoal.HYPERTROPHY.value,
        "goal_name": "Hypertrophy",
        "mesocycle": {"week": 1},
        "exercises": [
            {
                "exercise": "Chest Press",
                "muscle": "chest",
                "target_sets": 3,
                "target_reps": 10,
                "target_weight": 100,
                "rpe_target": 7,
            },
            {
                "exercise": "Lat Pulldown",
                "muscle": "back",
                "target_sets": 3,
                "target_reps": 10,
                "target_weight": 90,
                "rpe_target": 7,
            },
        ],
    }


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRET_KEY", "fit397-secret")
    monkeypatch.setattr(data_store, "DATA_DB", str(tmp_path / "fitness_data.db"))
    data_store.init_data_db()
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True, LOGIN_DISABLED=True, PROPAGATE_EXCEPTIONS=False)
    state = {"user_id": 1, "fingerprint": "fp-fit397"}
    settings = copy.deepcopy(module.DEFAULT_SETTINGS)
    settings.update({"training_goal": module.TrainingGoal.HYPERTROPHY.value, "equipment_preference": "machines_only"})
    monkeypatch.setattr(module, "_current_data_user_id", lambda: state["user_id"])
    monkeypatch.setattr(module, "_workout_recommendation_fingerprint", lambda: state["fingerprint"])
    monkeypatch.setattr(module, "USER_SETTINGS", settings)
    monkeypatch.setattr(module, "WORKOUTS", [])
    monkeypatch.setattr(module, "SORENESS_DATA", [])
    monkeypatch.setattr(module, "WORKOUT_RECOMMENDATIONS", [])
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION", None)
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION_FINGERPRINT", None)
    monkeypatch.setattr(module, "LAST_WORKOUT_RECOMMENDATION_OWNER", None)
    monkeypatch.setattr(module, "_current_workout_training_recommendation", lambda: None)
    monkeypatch.setattr(module, "_open_wearables_recommendation_facts", lambda: {})
    monkeypatch.setattr(module, "_apply_open_wearables_recommendation_guard", lambda rec, _facts: (rec, None))
    monkeypatch.setattr(module, "get_oura_daily", lambda *args, **kwargs: {})
    return module, module.app.test_client(), state


def _install_current(module, plan, fingerprint="fp-fit397"):
    module.LAST_WORKOUT_RECOMMENDATION = plan
    module.LAST_WORKOUT_RECOMMENDATION_FINGERPRINT = fingerprint
    module.LAST_WORKOUT_RECOMMENDATION_OWNER = {
        "user_id": 1,
        "fingerprint": fingerprint,
        "plan_id": id(plan),
    }


def test_swap_requires_nonempty_recommendation_id_before_plan_lookup(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(module, "_current_workout_plan_for_fingerprint", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("plan lookup must be after validation")))

    for payload in (
        {"recommendation_id": None, "workout_index": 0, "exercise_index": 0, "new_exercise_name": "Incline Press"},
        {"recommendation_id": "", "workout_index": 0, "exercise_index": 0, "new_exercise_name": "Incline Press"},
    ):
        response = client.post("/api/workout/swap", json=payload)
        assert response.status_code == 400
        assert response.get_json()["error"]["code"] == "invalid_field"


def test_swap_plan_mismatch_precedes_invalid_exercise_and_does_not_persist(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    current = _plan(module, "server-plan")
    _install_current(module, current)
    before = copy.deepcopy(current)
    persist_calls = []
    monkeypatch.setattr(module, "_persist_current_workout_plan", lambda *args: persist_calls.append(args))

    response = client.post(
        "/api/workout/swap",
        json={"recommendation_id": "old-plan", "workout_index": 999999, "exercise_index": 999999, "new_exercise_name": "Incline Press"},
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "plan_changed"
    assert "refresh" in response.get_json()["error"]["message"].lower()
    assert persist_calls == []
    assert current == before


def test_swap_mismatch_uses_regenerated_same_index_plan(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    regenerated = _plan(module, "regenerated-plan")
    _install_current(module, regenerated)
    data_store.save_current_workout_plan(1, "fp-fit397", regenerated)

    response = client.post(
        "/api/workout/swap",
        json={"recommendation_id": "old-plan", "workout_index": 0, "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "plan_changed"


def test_generated_plan_ids_are_distinct_within_the_same_second(monkeypatch, tmp_path):
    module, _client_obj, _state = _client(monkeypatch, tmp_path)

    class FrozenDatetime(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 7, 16, 12, 0, 0, tzinfo=tz)

    monkeypatch.setattr(module, "datetime", FrozenDatetime)
    first = module.generate_next_workout(
        [],
        [],
        available_time=30,
        consume_cardio_rotation=False,
        include_open_wearables_readiness=False,
    )
    second = module.generate_next_workout(
        [],
        [],
        available_time=30,
        consume_cardio_rotation=False,
        include_open_wearables_readiness=False,
    )

    assert first["id"] != second["id"]
    assert first["id"].startswith("20260716120000-")
    assert second["id"].startswith("20260716120000-")


def test_swap_matching_plan_id_applies_and_persists(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    current = _plan(module, "matching-plan")
    _install_current(module, current)
    data_store.save_current_workout_plan(1, "fp-fit397", current)

    response = client.post(
        "/api/workout/swap",
        json={"recommendation_id": "matching-plan", "workout_index": 999999, "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )

    assert response.status_code == 200
    assert response.get_json()["recommendation"]["exercises"][0]["exercise"] == "Incline Press"
    assert response.get_json()["recommendation"]["id"] != "matching-plan"
    assert data_store.get_current_workout_plan(1)["plan"]["exercises"][0]["exercise"] == "Incline Press"

    stale_retry = client.post(
        "/api/workout/swap",
        json={"recommendation_id": "matching-plan", "exercise_index": 1, "new_exercise_name": "Chest Press"},
    )
    assert stale_retry.status_code == 409
    assert stale_retry.get_json()["error"]["code"] == "plan_changed"


def test_swap_in_flight_regeneration_hits_the_plan_version_cas(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    original = _plan(module, "original-plan")
    regenerated = _plan(module, "regenerated-plan")
    _install_current(module, original)
    data_store.save_current_workout_plan(1, "fp-fit397", original)
    build_entry = module._build_exercise_entry

    def regenerate_during_swap(**kwargs):
        _install_current(module, regenerated)
        data_store.save_current_workout_plan(1, "fp-fit397", regenerated)
        return build_entry(**kwargs)

    monkeypatch.setattr(module, "_build_exercise_entry", regenerate_during_swap)

    response = client.post(
        "/api/workout/swap",
        json={"recommendation_id": "original-plan", "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )

    # A regeneration that lands while the swap is resolving is the FIT-233 CAS's job
    # (plan_generation/plan_version), so it surfaces as workout_plan_conflict.
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "workout_plan_conflict"
    assert data_store.get_current_workout_plan(1)["plan"] == regenerated


def test_swap_bumps_version_for_unpersisted_current_global(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    current = _plan(module, "global-plan")
    _install_current(module, current)

    response = client.post(
        "/api/workout/swap",
        json={"recommendation_id": "global-plan", "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )

    assert response.status_code == 200
    assert response.get_json()["recommendation"]["id"] != "global-plan"


def test_swap_does_not_consult_legacy_recommendation_list_or_workout_index(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    current = _plan(module, "current-plan")
    _install_current(module, current)
    data_store.save_current_workout_plan(1, "fp-fit397", current)

    class ExplodingList:
        def __iter__(self):
            raise AssertionError("WORKOUT_RECOMMENDATIONS must not be consulted")

    monkeypatch.setattr(module, "WORKOUT_RECOMMENDATIONS", ExplodingList())
    response = client.post(
        "/api/workout/swap",
        json={"recommendation_id": "current-plan", "workout_index": 999999, "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )

    assert response.status_code == 200

    app_source = Path("app.py").read_text()
    handler = app_source[app_source.index("def swap_workout_exercise"):app_source.index("def _apply_intent_patch", app_source.index("def swap_workout_exercise"))]
    assert "workout_index" not in handler
    assert "WORKOUT_RECOMMENDATIONS" not in handler


def test_swap_without_current_plan_preserves_not_found(monkeypatch, tmp_path):
    module, client, _state = _client(monkeypatch, tmp_path)
    response = client.post(
        "/api/workout/swap",
        json={"recommendation_id": "missing-plan", "exercise_index": 0, "new_exercise_name": "Incline Press"},
    )
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


_SWAP_SANDBOX = """
const json = (status, body) => new Response(JSON.stringify(body), {
  status, headers: { 'content-type': 'application/json' },
});
const entry = (name, muscle) => ({ exercise: name, muscle, target_sets: 3, target_reps: 10, target_weight: 100 });
const server = {
  plan: { id: 'plan-A', exercises: [entry('Chest Press', 'chest'), entry('Lat Pulldown', 'back')] },
  swapBodies: [],
  conflictCode: 'plan_changed',
  swaps: 0,
};
sandbox.fetch = async (path, options = {}) => {
  if (path.startsWith('/api/workout/swap')) {
    const body = JSON.parse(options.body);
    server.swapBodies.push(body);
    if (body.recommendation_id !== server.plan.id) {
      return json(409, { error: { code: server.conflictCode, message: 'Workout plan changed.' } });
    }
    server.swaps += 1;
    server.plan = {
      ...server.plan,
      id: `${server.plan.id}-s${server.swaps}`,
      exercises: server.plan.exercises.map((ex, i) => (
        i === body.exercise_index ? { ...ex, exercise: body.new_exercise_name } : ex
      )),
    };
    return json(200, { status: 'success', recommendation: server.plan });
  }
  if (path.startsWith('/api/next-workout')) return json(200, { next_workout: server.plan });
  return json(200, {});
};
sandbox.AbortController = class { constructor() { this.signal = {}; } abort() {} };
const calls = [];
sandbox.__fitSet.renderActiveWorkout(() => calls.push('render-active'));
sandbox.__fitSet.renderNextWorkout(() => calls.push('render-next'));
sandbox.__fitSet.saveActiveWorkoutDraft(() => {});
sandbox.__fitSet.closeModal(() => calls.push('close-modal'));
sandbox.__fitSet.toast((message) => calls.push(`toast:${message}`));
['modal-swap', 'swap-alternatives', 'swap-modal-title', 'swap-modal-sub'].forEach((id) => {
  sandbox.elements[id] = { hidden: true, innerHTML: '', textContent: '' };
});
"""

_SWAP_MOCKS = ["renderActiveWorkout", "renderNextWorkout", "saveActiveWorkoutDraft", "closeModal", "toast"]


def test_swap_client_sends_plan_id_from_plan_card_and_active_workout():
    output = run_app_js(
        ["openSwap", "applySwap", "state"],
        _SWAP_SANDBOX + """
e.state.nextWorkout = { id: 'plan-A', exercises: server.plan.exercises };
await e.openSwap(0, '', 'Chest Press', 'plan');
const fromPlanCard = e.state.swapContext.recommendationId;
await e.applySwap(0, 'Incline Press', 'Chest Press');

e.state.activeWorkout = { id: 'w1', recommendation_id: server.plan.id, exercises: [] };
await e.openSwap(1, '', 'Lat Pulldown', 'active');
const fromActive = e.state.swapContext.recommendationId;
await e.applySwap(1, 'Seated Row', 'Lat Pulldown');
process.stdout.write(JSON.stringify({
  fromPlanCard, fromActive, bodies: server.swapBodies, nextId: e.state.nextWorkout.id,
}));
""",
        mocks=_SWAP_MOCKS,
    )

    assert output["fromPlanCard"] == "plan-A"
    assert output["fromActive"] == "plan-A-s1"
    assert [b["recommendation_id"] for b in output["bodies"]] == ["plan-A", "plan-A-s1"]
    assert output["nextId"] == "plan-A-s1-s2"


def test_custom_swap_sends_plan_id():
    output = run_app_js(
        ["applyCustomSwap", "state"],
        _SWAP_SANDBOX + """
sandbox.elements['swap-custom-input'] = { value: 'Incline Press', focus() {} };
sandbox.elements['swap-custom-error'] = { hidden: true, textContent: '' };
sandbox.elements['swap-custom-submit'] = { disabled: false, textContent: 'Swap' };
e.state.swapContext = {
  exIdx: 0, muscle: 'chest', currentName: 'Chest Press', source: 'plan', recommendationId: 'plan-A',
};
await e.applyCustomSwap();
process.stdout.write(JSON.stringify({ bodies: server.swapBodies, nextId: e.state.nextWorkout.id }));
""",
        mocks=_SWAP_MOCKS,
    )

    assert output["bodies"] == [
        {"recommendation_id": "plan-A", "exercise_index": 0, "new_exercise_name": "Incline Press"}
    ]
    assert output["nextId"] == "plan-A-s1"


def test_swap_plan_changed_adopts_current_plan_keeps_logged_sets_and_next_swap_works():
    """Dead end: the plan regenerated mid-workout, so the client's id is stale."""
    output = run_app_js(
        ["openSwap", "applySwap", "state"],
        _SWAP_SANDBOX + """
const logged = (done) => ({ reps: '10', weight: '100', done, notes: '' });
e.state.activeWorkout = {
  id: 'w1',
  recommendation_id: 'plan-A',
  auth_scope: 'user:1',
  exercises: [
    { ...entry('Chest Press', 'chest'), logged_sets: [logged(true), logged(true), logged(false)] },
    { ...entry('Lat Pulldown', 'back'), logged_sets: [logged(false), logged(false), logged(false)] },
  ],
};
server.plan = { id: 'plan-B', exercises: [entry('Chest Press', 'chest'), entry('Lat Pulldown', 'back')] };

await e.openSwap(1, '', 'Lat Pulldown', 'active');
await e.applySwap(1, 'Seated Row', 'Lat Pulldown');
const afterConflict = {
  recommendationId: e.state.activeWorkout.recommendation_id,
  chestDone: e.state.activeWorkout.exercises[0].logged_sets.map((s) => s.done),
  nextId: e.state.nextWorkout.id,
  serverExercise: server.plan.exercises[1].exercise,
};

await e.openSwap(1, '', 'Lat Pulldown', 'active');
await e.applySwap(1, 'Seated Row', 'Lat Pulldown');
process.stdout.write(JSON.stringify({
  afterConflict,
  bodies: server.swapBodies.map((b) => b.recommendation_id),
  finalServerExercise: server.plan.exercises[1].exercise,
  activeNames: e.state.activeWorkout.exercises.map((ex) => ex.exercise),
  activeRecommendationId: e.state.activeWorkout.recommendation_id,
  calls,
}));
""",
        mocks=_SWAP_MOCKS,
    )

    assert output["afterConflict"] == {
        "recommendationId": "plan-B",
        "chestDone": [True, True, False],
        "nextId": "plan-B",
        "serverExercise": "Lat Pulldown",
    }
    assert output["bodies"] == ["plan-A", "plan-B"]
    assert output["finalServerExercise"] == "Seated Row"
    assert output["activeNames"] == ["Chest Press", "Seated Row"]
    assert output["activeRecommendationId"] == "plan-B-s1"
    assert any(c.startswith("toast:Workout plan changed") and "logged sets were kept" in c for c in output["calls"])
    assert "close-modal" in output["calls"]


def test_swap_workout_plan_conflict_also_refreshes_the_plan_card():
    output = run_app_js(
        ["openSwap", "applySwap", "state"],
        _SWAP_SANDBOX + """
server.conflictCode = 'workout_plan_conflict';
e.state.nextWorkout = { id: 'plan-A', exercises: server.plan.exercises };
server.plan = { id: 'plan-C', exercises: [entry('Chest Press', 'chest'), entry('Lat Pulldown', 'back')] };
await e.openSwap(0, '', 'Chest Press', 'plan');
await e.applySwap(0, 'Incline Press', 'Chest Press');
process.stdout.write(JSON.stringify({ nextId: e.state.nextWorkout.id, calls }));
""",
        mocks=_SWAP_MOCKS,
    )

    assert output["nextId"] == "plan-C"
    assert "render-next" in output["calls"]
