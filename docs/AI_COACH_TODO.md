# AI Coach — follow-up work

Shipped 2026-04-23. Everything below is future-pass scope, not a blocker.

## Bob's tweak 1 — side/joint granularity

**Problem:** "left shoulder sore" currently maps to `avoid_muscles=["shoulders"]`, which blacklists every shoulder-involving movement, including rows and pulls that don't actually load the left shoulder joint.

**Proposal:**
1. Extend the intent schema with `avoid_joints: ["left_shoulder", "right_shoulder", "left_knee", "right_knee", "left_elbow", "right_elbow", "low_back"]`.
2. Extend the exercise library with `joints_loaded: ["left_shoulder", "right_shoulder"]` per movement (or a generic `joints_loaded: ["shoulder"]` for symmetric work).
3. Prompt the LLM to emit `avoid_joints` when the athlete uses a side-specific phrase ("left shoulder", "right knee").
4. In `_apply_intent_patch`, avoid exercises whose `joints_loaded` intersects `avoid_joints` *in addition to* the muscle blacklist, but don't over-apply: a seated row touches the shoulder joint in both directions, so keep it; an overhead press mostly targets the sore side, so drop it.

**Data work needed:** author a `joints_loaded` mapping for the ~40 exercises in `_filtered_exercise_library()`. Roughly 1 hour.

**Risk:** ML falling back to muscle-only when it can't tell side from the phrase — fine, current behavior is the safe default.

## Tweak 1.5 — "why not changed" note when LLM returned an empty intent

Today, if the LLM returns an empty intent patch ("everything looks fine"), the UI says "No structural changes." That's accurate but not confidence-inspiring. Next pass: pass the LLM's `summary` through to the UI even when no notes fire, so the user sees the coach's reasoning.

## Tweak 2.1 — surface AI metrics in Settings

`/api/ai/metrics?hours=24` already returns `{adjust_requests, ok, cache_hits, fallbacks, fallback_pct, avg_latency_ms, recent}`. Add a small card in Settings → Integrations that pings it every 60s and shows "24h: 12 adjustments, 2.4s avg, 0 fallbacks" — lets Wesley catch ASUS flakiness before it's a surprise.

## Tweak 3.1 — alerting threshold

When `fallback_pct > 20` over a 1h window, auto-switch the "Apply Adjustment" button to a warning state and show "AI coach flaky (GX10 issues) — plan will stay deterministic." Healing is automatic once fallbacks clear.

## Apple Health joint-of-fact side note

The Apple Health workouts sync is keyed on `(source, record_type, record_date, record_key)` where `record_key` is `startDate` for workouts. That kills the AM/PM collapse. If Health Auto Export ever stops emitting `startDate`, we fall back to `activity_type:duration` which is still usually unique per day.

## P2 — HAE timestamp timezone precision (flagged by Codex audit 2026-04-24)

`_normalize_hae_payload` derives `record_date` from `startDate[:10]` — a raw string slice that ignores the timezone offset. For workouts recorded near local midnight in a non-CDT timezone (e.g., the user travels to Tokyo and the Watch syncs Tokyo-local timestamps), the string-slice day may not match the user's intended calendar day. Practical impact today: user is in CDT where HAE already emits CDT-local timestamps, so slice = intended local day. Only breaks for cross-timezone scenarios.

Fix: parse full ISO timestamps with offsets, normalize to the user's preferred display timezone (stored in settings), derive `record_date` from that. Requires a small timezone utility and a settings field. Defer until user actually travels with the Watch enabled.

## Not doing

- Replacing the deterministic engine with an LLM. Bob's call: "determinism is a feature, not a limitation." Don't reopen.
- Sending the full exercise library to the LLM prompt. The LLM only needs to return intent; Python picks from the library.
- Running LLM in the background. Sync + spinner is what users want for an intentional adjustment; background magic hurts trust.
