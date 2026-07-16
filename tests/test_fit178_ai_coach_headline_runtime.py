from __future__ import annotations

from js_runtime import run_app_js


def test_ai_coach_headline_runtime_states():
    outputs = run_app_js(
        ["_aiCoachHeadlineFromHealth"],
        """
const headlineFromHealth = e._aiCoachHeadlineFromHealth;
const outputs = {
  ready: headlineFromHealth({
    active_role: 'primary',
    primary: { name: 'primary', reachable: true, model_loaded: true },
    fallback: { name: 'fallback', reachable: true, model_loaded: true },
  }),
  fallback_active: headlineFromHealth({
    active_role: 'fallback',
    primary: { name: 'primary', reachable: false, model_loaded: false },
    fallback: { name: 'fallback', reachable: true, model_loaded: true },
  }),
  offline: headlineFromHealth({
    active_role: null,
    primary: { name: 'primary', reachable: false, model_loaded: false },
    fallback: { name: 'fallback', reachable: false, model_loaded: false },
  }),
  unloaded_fallback: headlineFromHealth({
    active_role: 'fallback',
    primary: { name: 'primary', reachable: false, model_loaded: false },
    fallback: { name: 'fallback', reachable: true, model_loaded: false },
  }),
};
process.stdout.write(JSON.stringify(outputs));
""",
    )

    ready = outputs["ready"]
    assert ready["stateText"] == "Ready"
    assert ready["hostText"] == "ASUS GX10"
    assert ready["chipText"] == "Online"
    assert ready["chipCls"] == "state-chip ok"
    assert ready["detail"] == "Serving from ASUS GX10."

    fallback = outputs["fallback_active"]
    assert fallback["stateText"] == "Fallback active"
    assert fallback["hostText"] == "Mac Studio"
    assert fallback["chipText"] == "Fallback"
    assert fallback["chipCls"] == "state-chip warn"
    assert fallback["detail"] == "ASUS GX10 unreachable — Mac Studio is serving traffic."

    offline = outputs["offline"]
    assert offline["stateText"] == "AI offline"
    assert offline["hostText"] == "ASUS GX10 & Mac Studio unavailable"
    assert offline["chipText"] == "Offline"
    assert offline["chipCls"] == "state-chip stale"

    unloaded_fallback = outputs["unloaded_fallback"]
    assert unloaded_fallback["stateText"] == "AI offline"
    assert unloaded_fallback["hostText"] == "ASUS GX10 & Mac Studio unavailable"
    assert unloaded_fallback["chipText"] == "Offline"
    assert unloaded_fallback["chipCls"] == "state-chip stale"
    assert unloaded_fallback["stateText"] != "Fallback active"
