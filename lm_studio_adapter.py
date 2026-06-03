"""LM Studio adapter for the fitness dashboard's AI coach layer.

Architecture:
    Flask app
      -> POST compact context JSON
      -> configured LM Studio primary/fallback endpoint
      -> strict JSON patch back
      -> Python validates/applies, never the other way around.

The LLM is a coach/translator layer — it returns intent only (muscle + movement
swap + constraint tags). The deterministic engine owns exercise selection,
weight, sets, reps. Safety rules are enforced by the route handler, not here.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
import urllib.error


LM_STUDIO_PRIMARY_URL = os.environ.get(
    "LM_STUDIO_PRIMARY_URL",
    os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234"),
)
LM_STUDIO_PRIMARY_MODEL = os.environ.get(
    "LM_STUDIO_PRIMARY_MODEL",
    os.environ.get("LM_STUDIO_MODEL", "qwen/qwen3-30b-a3b-2507"),
)
LM_STUDIO_FALLBACK_URL = os.environ.get("LM_STUDIO_FALLBACK_URL", "http://127.0.0.1:1234")
LM_STUDIO_FALLBACK_MODEL = os.environ.get("LM_STUDIO_FALLBACK_MODEL", "qwen/qwen3.6-35b-a3b")
LM_STUDIO_TIMEOUT_SEC = float(os.environ.get("LM_STUDIO_TIMEOUT_SEC", "8"))
LM_STUDIO_ANALYZE_TIMEOUT_SEC = float(os.environ.get("LM_STUDIO_ANALYZE_TIMEOUT_SEC", "25"))
LM_STUDIO_SWAP_RESOLVE_TIMEOUT_SEC = float(os.environ.get("LM_STUDIO_SWAP_RESOLVE_TIMEOUT_SEC", "2.5"))
LM_STUDIO_PREFLIGHT_TIMEOUT_SEC = float(os.environ.get("LM_STUDIO_PREFLIGHT_TIMEOUT_SEC", "1.5"))
MEAL_TEXT_LOCK_ACQUIRE_SEC = float(os.environ.get("MEAL_TEXT_LOCK_ACQUIRE_SEC", "2.0"))
# Backwards-compatible names used by app.py cache keys.
LM_STUDIO_URL = LM_STUDIO_PRIMARY_URL
LM_STUDIO_MODEL = LM_STUDIO_PRIMARY_MODEL
LM_STUDIO_MODEL_VERSION = LM_STUDIO_MODEL.split("/")[-1]

# Concurrency: one adjustment request at a time. A global semaphore keeps
# LM Studio from being stampeded if two users click Adjust simultaneously.
_INFERENCE_LOCK = threading.Semaphore(1)
_MEAL_TEXT_INFERENCE_LOCK = threading.Semaphore(1)


class LmStudioError(Exception):
    """Raised when LM Studio is unreachable, times out, or returns malformed JSON."""


def _model_version(model: str) -> str:
    return (model or "").split("/")[-1]


def model_version_for(candidate: dict | None) -> str:
    if not candidate:
        return LM_STUDIO_MODEL_VERSION
    return _model_version(candidate.get("model") or "")


def _lm_candidates() -> list[dict]:
    candidates = [
        {
            "role": "primary",
            "name": "primary",
            "url": LM_STUDIO_PRIMARY_URL.rstrip("/"),
            "model": LM_STUDIO_PRIMARY_MODEL,
        }
    ]
    fallback = {
        "role": "fallback",
        "name": "fallback",
        "url": LM_STUDIO_FALLBACK_URL.rstrip("/"),
        "model": LM_STUDIO_FALLBACK_MODEL,
    }
    if (fallback["url"], fallback["model"]) != (candidates[0]["url"], candidates[0]["model"]):
        candidates.append(fallback)
    return candidates


def _target_loaded(loaded: list[str], model: str) -> bool:
    version = _model_version(model)
    return any(model == m or model in (m or "") or version in (m or "") for m in loaded)


def _models_for(candidate: dict, timeout: float = 2) -> list[str]:
    req = urllib.request.Request(f"{candidate['url']}/v1/models", method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    return [m.get("id") for m in data.get("data", [])]


def _preflight_candidate(candidate: dict) -> None:
    """Fail fast before generation when an endpoint is offline or has the wrong model.

    A primary endpoint can be offline while fallback is healthy. Without this
    preflight, a request spends the full generation timeout on primary before it
    tries fallback.
    """
    try:
        loaded = _models_for(candidate, timeout=LM_STUDIO_PREFLIGHT_TIMEOUT_SEC)
    except urllib.error.URLError as exc:
        raise LmStudioError(f"unreachable: {exc}") from exc
    except TimeoutError as exc:
        raise LmStudioError("timeout") from exc
    except Exception as exc:
        raise LmStudioError(f"preflight failed: {type(exc).__name__}") from exc
    if not _target_loaded(loaded, candidate["model"]):
        raise LmStudioError(f"model not loaded: {candidate['model']}")


def _payload_for_candidate(payload: dict, candidate: dict) -> dict:
    candidate_payload = dict(payload or {})
    candidate_payload["model"] = candidate["model"]
    return candidate_payload


def _same_candidate(left: dict | None, right: dict | None) -> bool:
    return bool(left and right and left.get("url") == right.get("url") and left.get("model") == right.get("model"))


def _candidate_attempt_order(preferred_candidate: dict | None = None) -> list[dict]:
    candidates = _lm_candidates()
    if not preferred_candidate:
        return candidates
    remaining = [candidate for candidate in candidates if not _same_candidate(candidate, preferred_candidate)]
    return [preferred_candidate, *remaining]


def model_versions_after(candidate: dict | None) -> list[str]:
    if not candidate:
        return []
    return [model_version_for(candidate) for candidate in _candidate_attempt_order(candidate)[1:]]


def fallback_model_versions() -> list[str]:
    return [model_version_for(candidate) for candidate in _lm_candidates()[1:]]


def active_candidate() -> dict | None:
    """Return the first currently usable candidate.

    Routes use this before cache lookup so a fallback-active setup does not
    store or read Adjust Plan responses under the missing primary model.
    """
    for candidate in _lm_candidates():
        try:
            _preflight_candidate(candidate)
            return candidate
        except LmStudioError:
            continue
    return None


def active_model_version() -> str:
    return model_version_for(active_candidate())


def health() -> dict:
    """Quick reachability probe used by /api/health and the Settings panel."""
    checks = []
    active = None
    for candidate in _lm_candidates():
        try:
            loaded = _models_for(candidate)
            model_loaded = _target_loaded(loaded, candidate["model"])
            check = {
                **candidate,
                "reachable": True,
                "model_loaded": model_loaded,
                "loaded_models": loaded,
            }
            if active is None and model_loaded:
                active = check
        except Exception as exc:
            check = {
                **candidate,
                "reachable": False,
                "model_loaded": False,
                "loaded_models": [],
                "error": str(exc),
            }
        checks.append(check)

    if active is None:
        active = next((c for c in checks if c.get("reachable")), checks[0] if checks else {})

    return {
        "reachable": bool(active.get("reachable")),
        "url": active.get("url") or LM_STUDIO_PRIMARY_URL,
        "model": active.get("model") or LM_STUDIO_PRIMARY_MODEL,
        "model_loaded": bool(active.get("model_loaded")),
        "loaded_models": active.get("loaded_models") or [],
        "active_role": active.get("role"),
        "fallback_active": active.get("role") == "fallback",
        "primary": checks[0] if checks else None,
        "fallback": checks[1] if len(checks) > 1 else None,
        "checks": checks,
    }


def _post_json_to(candidate: dict, path: str, payload: dict, timeout: float) -> dict:
    body_payload = dict(payload)
    body_payload["model"] = candidate["model"]
    body = json.dumps(body_payload).encode("utf-8")
    req = urllib.request.Request(
        f"{candidate['url']}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as exc:
        raise LmStudioError(f"http {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise LmStudioError(f"unreachable: {exc}") from exc
    except TimeoutError as exc:
        raise LmStudioError("timeout") from exc
    except Exception as exc:
        raise LmStudioError(f"network error: {type(exc).__name__}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LmStudioError(f"invalid envelope json: {raw[:160]}") from exc


def _post_json(path: str, payload: dict, timeout: float) -> dict:
    """POST JSON and parse the response envelope. All errors (network,
    HTTP, malformed JSON, unexpected shape) surface as LmStudioError so
    the Flask routes can uniformly translate them to a deterministic
    fallback instead of leaking a 500."""
    errors = []
    for candidate in _lm_candidates():
        try:
            _preflight_candidate(candidate)
            resp = _post_json_to(candidate, path, _payload_for_candidate(payload, candidate), timeout)
            resp["_lm_candidate"] = candidate
            return resp
        except LmStudioError as exc:
            errors.append(f"{candidate['name']}: {exc}")
    raise LmStudioError("all endpoints failed: " + "; ".join(errors))


def _completion_json(path: str, payload: dict, timeout: float, validate, clean=None, preflighted_candidate=None) -> dict:
    """Call candidates in priority order and return validated JSON content.

    Network failures, malformed envelopes, empty/invalid model JSON, and schema
    validation errors all advance to the next candidate. This is what makes the
    Mac endpoint a real fallback instead of just a connectivity fallback.
    """
    start = time.time()
    errors = []
    for candidate in _candidate_attempt_order(preflighted_candidate):
        try:
            if not _same_candidate(candidate, preflighted_candidate):
                _preflight_candidate(candidate)
            resp = _post_json_to(candidate, path, _payload_for_candidate(payload, candidate), timeout)
            try:
                message = resp["choices"][0]["message"]
                content = message.get("content") or message.get("reasoning_content") or ""
            except (KeyError, IndexError, TypeError) as exc:
                raise LmStudioError(f"unexpected response shape: {resp}") from exc

            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as exc:
                raise LmStudioError(f"invalid json from LLM: {content[:200]}") from exc

            if clean:
                clean(parsed)
            validate(parsed)
            elapsed_ms = int((time.time() - start) * 1000)
            parsed["_meta"] = {
                "model": candidate["model"],
                "model_version": _model_version(candidate["model"]),
                "url": candidate["url"],
                "role": candidate["role"],
                "fallback_used": candidate["role"] == "fallback",
                "attempt_errors": errors,
                "elapsed_ms": elapsed_ms,
            }
            return parsed
        except LmStudioError as exc:
            errors.append(f"{candidate['name']}: {exc}")
    raise LmStudioError("all endpoints failed: " + "; ".join(errors))


# ──────────────────────────────────────────────────────────────
# Adjust Plan — returns an INTENT patch, nothing prescriptive.
# ──────────────────────────────────────────────────────────────
ADJUST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "intent": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "avoid_muscles": {"type": "array", "items": {"type": "string"}},
                "avoid_joints": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "side": {"type": "string", "enum": ["left", "right", "both"]},
                            "joint": {"type": "string", "enum": ["shoulder", "elbow", "wrist", "hip", "knee", "ankle", "spine"]},
                        },
                        "required": ["side", "joint"],
                    },
                },
                "swap": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "replace_exercise": {"type": "string"},
                            "target_muscle": {"type": "string"},
                            "target_exercise": {"type": ["string", "null"]},
                            "reason": {"type": "string"},
                        },
                        "required": ["replace_exercise", "target_muscle", "target_exercise", "reason"],
                    },
                },
                "rpe_delta": {"type": "number"},
                "sets_delta_pct": {"type": "number"},
                "duration_cap_min": {"type": "number"},
                "drop_cardio": {"type": "boolean"},
            },
            "required": [
                "avoid_muscles",
                "avoid_joints",
                "swap",
                "rpe_delta",
                "sets_delta_pct",
                "duration_cap_min",
                "drop_cardio",
            ],
        },
    },
    "required": ["summary", "intent"],
}


SWAP_RESOLVE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "canonical_name": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["canonical_name", "confidence", "reason"],
}


_SWAP_RESOLVE_SYSTEM = (
    "You map a typed workout swap request to one safe exercise-library option.\n"
    "\n"
    "Rules:\n"
    "- Return ONLY JSON matching the provided schema. No prose, no markdown.\n"
    "- Choose canonical_name only from candidate_names. Never invent exercise names.\n"
    "- If no candidate is a clear semantic match, return canonical_name null.\n"
    "- NEVER prescribe weights, sets, reps, RPE, or load. Python infers load.\n"
    "- Err toward null when the typed phrase is vague or unsafe."
)


def _validate_swap_resolution(parsed):
    if not isinstance(parsed, dict):
        raise LmStudioError(f"response is not an object: {type(parsed).__name__}")
    canonical_name = parsed.get("canonical_name")
    if canonical_name is not None and not isinstance(canonical_name, str):
        raise LmStudioError("canonical_name must be string or null")
    confidence = parsed.get("confidence")
    if not isinstance(confidence, (int, float)):
        raise LmStudioError("confidence must be number")
    if not isinstance(parsed.get("reason"), str):
        raise LmStudioError("reason must be string")


def _normalize_candidate_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def resolve_swap_candidate(
    typed_name: str,
    *,
    current_exercise: str,
    target_muscle: str,
    candidates: list[dict],
    timeout: float | None = None,
    preflighted_candidate: dict | None = None,
) -> dict:
    """Ask the LLM to map typed swap text to a bounded candidate name only."""
    timeout = timeout if timeout is not None else LM_STUDIO_SWAP_RESOLVE_TIMEOUT_SEC
    candidate_names = [
        str(candidate.get("name") or "").strip()
        for candidate in (candidates or [])
        if str(candidate.get("name") or "").strip()
    ]
    if not candidate_names:
        raise LmStudioError("no swap candidates provided")
    candidate_lookup = {_normalize_candidate_name(name): name for name in candidate_names}

    messages = [
        {"role": "system", "content": _SWAP_RESOLVE_SYSTEM},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "typed_name": str(typed_name or "").strip(),
                    "current_exercise": str(current_exercise or "").strip(),
                    "target_muscle": str(target_muscle or "").strip(),
                    "candidate_names": candidate_names,
                    "candidates": [
                        {
                            "name": candidate.get("name"),
                            "equipment": candidate.get("equipment"),
                            "aliases": candidate.get("aliases") or [],
                            "compound": bool(candidate.get("compound")),
                        }
                        for candidate in candidates
                    ],
                }
            ),
        },
    ]

    payload = {
        "model": LM_STUDIO_MODEL,
        "temperature": 0,
        "max_tokens": 220,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "swap_resolution",
                "schema": SWAP_RESOLVE_SCHEMA,
                "strict": True,
            },
        },
    }

    acquired = _INFERENCE_LOCK.acquire(timeout=timeout + 1)
    if not acquired:
        raise LmStudioError("busy: another inference request is still in flight")

    def validate(parsed):
        _validate_swap_resolution(parsed)
        canonical_name = parsed.get("canonical_name")
        if canonical_name is None:
            return
        normalized = _normalize_candidate_name(canonical_name)
        if normalized not in candidate_lookup:
            raise LmStudioError("canonical_name is not in candidate_names")
        parsed["canonical_name"] = candidate_lookup[normalized]

    try:
        parsed = _completion_json(
            "/v1/chat/completions",
            payload,
            timeout=timeout,
            validate=validate,
            preflighted_candidate=preflighted_candidate,
        )
    finally:
        _INFERENCE_LOCK.release()

    return parsed


_ADJUST_SYSTEM = (
    "You are a strength coach's safety valve. The deterministic algorithm has already "
    "produced today's workout. Your job is to translate the athlete's natural-language "
    "constraint into an INTENT patch that the algorithm can re-apply.\n"
    "\n"
    "Rules:\n"
    "- Return ONLY JSON matching the provided schema. No prose, no markdown.\n"
    "- NEVER prescribe weights or sets — only intent.\n"
    "- Use avoid_muscles to express 'don't train X' (translate movement cues "
    "like 'no overhead press' into the relevant muscle — shoulders here).\n"
    "- Use avoid_joints for side-specific pain/soreness like 'left shoulder hurts' "
    "or 'right knee sore'. Do not convert those to a whole-muscle avoid unless "
    "the athlete says the full muscle group is sore.\n"
    "- Use swap to say 'the algorithm picked exercise X for muscle M; replace the "
    "target muscle for reason Z'. If the athlete explicitly names a replacement "
    "machine, put that name in target_exercise; otherwise set target_exercise to null. Python validates it and infers load.\n"
    "- rpe_delta: -1.0 to +1.0 — never more. 0 if unsure.\n"
    "- sets_delta_pct: -20 to +20 — never more. 0 if unsure.\n"
    "- duration_cap_min: cap if user gave a tight time box. 0 if no cap.\n"
    "- drop_cardio: true only if user explicitly said no cardio or very short.\n"
    "- summary: one-sentence English explanation of what you did and why.\n"
    "- Err toward LESS change. If constraint is vague, return an empty intent."
)


def adjust_plan(
    current_plan: dict,
    constraint: str,
    readiness: dict | None = None,
    timeout: float | None = None,
    preflighted_candidate: dict | None = None,
) -> dict:
    """Ask the LLM for an intent patch based on the user's constraint.

    Returns the parsed JSON patch on success. Raises LmStudioError on any failure
    (unreachable, timeout, malformed JSON, schema mismatch). The caller must
    translate that into a "deterministic plan unchanged" fallback.
    """
    timeout = timeout if timeout is not None else LM_STUDIO_TIMEOUT_SEC

    # Compact the plan — we send only the fields the LLM needs to reason about
    # so we stay well under the 32k ctx window and keep prompts cheap.
    plan_compact = {
        "focus": current_plan.get("focus"),
        "goal_name": current_plan.get("goal_name"),
        "estimated_minutes": current_plan.get("estimated_minutes"),
        "exercises": [
            {
                "exercise": ex.get("exercise") or ex.get("name") or ex.get("machine"),
                "muscle": ex.get("muscle") or ex.get("muscle_group"),
                "is_compound": bool(ex.get("is_compound")),
                "target_sets": ex.get("target_sets") or ex.get("sets"),
                "target_reps": ex.get("target_reps") or ex.get("reps"),
                "rpe_target": ex.get("rpe_target") or ex.get("rpe"),
            }
            for ex in (current_plan.get("exercises") or [])
        ],
        "cardio": (current_plan.get("cardio") or {}).get("type"),
    }

    messages = [
        {"role": "system", "content": _ADJUST_SYSTEM},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "athlete_constraint": constraint,
                    "current_plan": plan_compact,
                    "readiness": readiness or {},
                }
            ),
        },
    ]

    payload = {
        "model": LM_STUDIO_MODEL,
        "temperature": 0.1,
        "max_tokens": 600,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "adjust_plan_intent",
                "schema": ADJUST_SCHEMA,
                "strict": True,
            },
        },
    }

    acquired = _INFERENCE_LOCK.acquire(timeout=timeout + 1)
    if not acquired:
        raise LmStudioError("busy: another Adjust Plan request is still in flight")

    start = time.time()
    try:
        parsed = _completion_json(
            "/v1/chat/completions",
            payload,
            timeout=timeout,
            validate=_validate_adjust_intent,
            preflighted_candidate=preflighted_candidate,
        )
    finally:
        _INFERENCE_LOCK.release()
    parsed["_meta"]["elapsed_ms"] = int((time.time() - start) * 1000)
    return parsed


def _validate_adjust_intent(parsed):
    """Fail loud if the LLM drifted from the schema. LM Studio's strict mode
    should prevent this, but belt-and-suspenders against prompt injection /
    future model swaps."""
    if not isinstance(parsed, dict):
        raise LmStudioError(f"response is not an object: {type(parsed).__name__}")
    if not isinstance(parsed.get("summary"), str):
        raise LmStudioError("missing/invalid 'summary' (must be string)")
    intent = parsed.get("intent")
    if not isinstance(intent, dict):
        raise LmStudioError("missing/invalid 'intent' (must be object)")
    # Required fields and types
    if not isinstance(intent.get("avoid_muscles"), list):
        raise LmStudioError("intent.avoid_muscles must be a list")
    if not all(isinstance(m, str) for m in intent["avoid_muscles"]):
        raise LmStudioError("intent.avoid_muscles entries must be strings")
    avoid_joints = intent.get("avoid_joints")
    if not isinstance(avoid_joints, list):
        raise LmStudioError("intent.avoid_joints must be a list")
    for item in avoid_joints:
        if not isinstance(item, dict):
            raise LmStudioError("intent.avoid_joints entries must be objects")
        if item.get("side") not in {"left", "right", "both"}:
            raise LmStudioError("intent.avoid_joints[*].side is invalid")
        if item.get("joint") not in {"shoulder", "elbow", "wrist", "hip", "knee", "ankle", "spine"}:
            raise LmStudioError("intent.avoid_joints[*].joint is invalid")
    swap = intent.get("swap")
    if not isinstance(swap, list):
        raise LmStudioError("intent.swap must be a list")
    for s in swap:
        if not isinstance(s, dict):
            raise LmStudioError("intent.swap entries must be objects")
        for key in ("replace_exercise", "target_muscle", "reason"):
            if not isinstance(s.get(key), str):
                raise LmStudioError(f"intent.swap[*].{key} must be a string")
        if s.get("target_exercise") is not None and not isinstance(s.get("target_exercise"), str):
            raise LmStudioError("intent.swap[*].target_exercise must be a string or null")
    for key in ("rpe_delta", "sets_delta_pct", "duration_cap_min"):
        val = intent.get(key)
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            raise LmStudioError(f"intent.{key} must be a number")
    if not isinstance(intent.get("drop_cardio"), bool):
        raise LmStudioError("intent.drop_cardio must be a boolean")


# ──────────────────────────────────────────────────────────────
# Analyze Workout — read-only narrative, no prescription.
# ──────────────────────────────────────────────────────────────
ANALYZE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "wins": {"type": "array", "items": {"type": "string"}},
        "concerns": {"type": "array", "items": {"type": "string"}},
        "comparison": {"type": "string"},
        "next_session_cue": {"type": "string"},
    },
    "required": ["summary", "wins", "concerns", "comparison", "next_session_cue"],
}

ANALYZE_PROMPT_VERSION = "notes-v2"

_ANALYZE_SYSTEM = (
    "You are an honest, direct strength coach reviewing a logged workout. You are "
    "NOT prescribing the next session — that's another layer's job. Your only job "
    "is to look at what the athlete did and deliver a clean post-mortem.\n"
    "\n"
    "Rules:\n"
    "- Return ONLY JSON matching the provided schema. No prose, no markdown.\n"
    "- summary: 2 short sentences. What happened, overall read.\n"
    "- wins: 1-3 concrete things that went right. Specific — cite exercise/weight/RPE.\n"
    "- Use per-set notes and logged cardio when present; treat them as first-class evidence.\n"
    "- If any note mentions pain, hurting, discomfort, tightness, asymmetry, imbalance, "
    "or one side feeling worse than the other, you MUST surface it in concerns or "
    "next_session_cue using the exact exercise/body-side context from the note. Do "
    "not diagnose it, but do not ignore it.\n"
    "- concerns: 0-3 specific things to watch. Based ONLY on the data given — don't "
    "invent injuries, sleep issues, or history you can't see. If nothing to flag, "
    "return an empty array.\n"
    "- comparison: 1-2 sentences putting this session in context vs the recent history "
    "shown. Use relative words (up/down/steady) — not invented percentages.\n"
    "- next_session_cue: 1 sentence. A simple behavioral nudge for next time — form "
    "focus, progression hint, recovery prompt. Never a prescription with numbers.\n"
    "- No hallucinated metrics. If a field is null or missing, ignore it or say so.\n"
    "- No emojis. No exclamation points.\n"
    "- Plain English. Short words. Your athlete reads this on a phone after the gym."
)


def analyze_workout(workout: dict, context: dict, timeout: float | None = None) -> dict:
    """Ask the LLM for a post-mortem on one workout.

    workout: the session being analyzed (sets, reps, weight, RPE, notes).
    context: {
        "recent_sessions": [same-muscle sessions in the last ~4 weeks, compact shape],
        "progression": {exercise_name: {current_e1rm, peak_e1rm, trend_pct, status}},
        "readiness": optional Oura snapshot for the session date,
        "goal": training goal + RPE target from settings,
    }
    """
    timeout = timeout if timeout is not None else LM_STUDIO_ANALYZE_TIMEOUT_SEC

    notable_terms = (
        "pain", "hurt", "hurting", "ache", "discomfort", "tight", "tightness",
        "left", "right", "side", "asymmetry", "imbalance", "worse",
    )
    notable_notes = []
    for ex in (workout.get("exercises") or []):
        ex_name = ex.get("machine") or ex.get("exercise") or ex.get("name") or "Exercise"
        for s in (ex.get("sets") or []):
            note = (s.get("notes") or "").strip()
            if note and any(term in note.lower() for term in notable_terms):
                notable_notes.append({
                    "exercise": ex_name,
                    "set_number": s.get("set_number"),
                    "note": note,
                })

    compact_workout = {
        "date": workout.get("date"),
        "session_type": workout.get("session_type") or workout.get("focus"),
        "duration_minutes": workout.get("duration_minutes"),
        "total_sets": workout.get("total_sets"),
        "total_volume_lbs": workout.get("total_volume"),
        "exercises": [
            {
                "name": ex.get("machine") or ex.get("exercise") or ex.get("name"),
                "muscle": ex.get("muscle_group") or ex.get("muscle"),
                "sets": [
                    {
                        "reps": s.get("reps"),
                        "weight_lbs": s.get("weight_lbs"),
                        "rpe": s.get("rpe"),
                        "notes": s.get("notes") or "",
                    }
                    for s in (ex.get("sets") or [])
                ],
            }
            for ex in (workout.get("exercises") or [])
        ],
        "cardio": workout.get("cardio"),
        "notes": workout.get("notes") or "",
        "notable_notes": notable_notes,
    }

    messages = [
        {"role": "system", "content": _ANALYZE_SYSTEM},
        {
            "role": "user",
            "content": json.dumps({
                "workout": compact_workout,
                "context": context or {},
            }, default=str),
        },
    ]

    payload = {
        "model": LM_STUDIO_MODEL,
        "temperature": 0.2,
        "max_tokens": 700,
        "messages": messages,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "analyze_workout",
                "schema": ANALYZE_SCHEMA,
                "strict": True,
            },
        },
    }

    acquired = _INFERENCE_LOCK.acquire(timeout=timeout + 1)
    if not acquired:
        raise LmStudioError("busy: inference lock held by another request")

    start = time.time()
    try:
        parsed = _completion_json(
            "/v1/chat/completions",
            payload,
            timeout=timeout,
            validate=_validate_analyze_result,
            clean=_clean_analyze_result,
        )
    finally:
        _INFERENCE_LOCK.release()
    parsed["_meta"]["elapsed_ms"] = int((time.time() - start) * 1000)
    return parsed


def _clean_analyze_result(parsed):
    """Trim common local-model section-tag spillover inside schema strings."""
    if not isinstance(parsed, dict):
        return
    section_re = re.compile(
        r"\n\s*(?:<(wins|concerns|comparison|next_session_cue)>|(wins|concerns|comparison|next_session_cue)\s*:).*$",
        re.IGNORECASE | re.DOTALL,
    )
    for key in ("summary", "comparison", "next_session_cue"):
        val = parsed.get(key)
        if isinstance(val, str):
            parsed[key] = section_re.sub("", val).strip()


def _validate_analyze_result(parsed):
    """Reject malformed analyze output before it caches or renders."""
    if not isinstance(parsed, dict):
        raise LmStudioError(f"analyze response is not an object: {type(parsed).__name__}")
    for key in ("summary", "comparison", "next_session_cue"):
        if not isinstance(parsed.get(key), str):
            raise LmStudioError(f"analyze.{key} must be a string")
    for key in ("wins", "concerns"):
        val = parsed.get(key)
        if not isinstance(val, list):
            raise LmStudioError(f"analyze.{key} must be a list")
        if not all(isinstance(v, str) for v in val):
            raise LmStudioError(f"analyze.{key} entries must be strings")
