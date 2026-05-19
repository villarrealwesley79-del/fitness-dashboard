"""Text-first meal logging parser (FIT-59).

Converts free-form meal text (e.g. "two eggs and toast", "chipotle bowl
chicken white rice", "movie theater popcorn", "ate half the pasta leftovers")
into a sanitized food estimate compatible with the canonical food_logs
persistence path and the FIT-5 estimate schema.

Path:
    text  -> LM Studio (qwen via lm_studio_adapter) -> validated JSON
    or, on LM Studio failure / invalid response / schema mismatch,
    deterministic fallback table of common foods.

Output is sanitized: no raw prompt content, no model trace, no chain of
thought. Errors surface as a low-confidence fallback estimate — never as a
raised exception or 500. The endpoint owner decides auto-log vs pending
review based on the returned ``confidence`` and ``ambiguous`` flag.
"""

from __future__ import annotations

from typing import Iterable, Optional

import branded_food_lookup
from lm_studio_adapter import (
    LM_STUDIO_ANALYZE_TIMEOUT_SEC,
    LmStudioError,
    _completion_json,
    _INFERENCE_LOCK,
)
from meal_estimate_schema import ALLOWED_MEAL_TYPES, CALORIE_MAX, MACRO_GRAM_MAX, SODIUM_MG_MAX


# Estimate keys we accept from the model. Anything else is dropped before
# validation so the model can't smuggle raw prompt, image refs, or trace fields
# into the response we return to the client. ``source`` is parser-controlled
# (we never trust the model to label provenance) — see _PARSER_CONTROLLED_KEYS.
_ALLOWED_ESTIMATE_KEYS = frozenset({
    "item_name",
    "portion_description",
    "meal_type",
    "calories",
    "protein_g",
    "carbs_g",
    "fat_g",
    "sodium_mg",
    "fiber_g",
    "confidence",
    "ambiguous",
    "uncertainty_notes",
    "source",
})

# Fields the parser owns regardless of what the model returns. They are
# stripped before validation and re-applied after the model output is
# accepted, so the model can never lie about provenance.
_PARSER_CONTROLLED_KEYS = frozenset({"source"})

# Plausible-range guards. Anything outside these ranges is treated as model
# error and falls through to the deterministic fallback.
_CALORIE_MAX = CALORIE_MAX
_MACRO_MAX = MACRO_GRAM_MAX
_SODIUM_MAX = SODIUM_MG_MAX


# Tokens that should pull confidence down even when the LLM is bullish.
# Mirrors the FIT-60 stub list so behavior is continuous across the swap.
# Substring match: each token must be either long enough or whitespace-anchored
# to avoid false positives like "fish" / "rice dish" / "rabbit" matching short
# approximation markers.
_AMBIGUOUS_TOKENS = (
    "popcorn", "movie", "shared", "leftover", "leftovers", "snacks", "half",
    "buffet", "potluck", "?", "guessing", "guess", "some food", "a bit",
    "a few",
)


# Deterministic preset table used when LM Studio is unreachable, returns
# unparseable JSON, or fails schema validation. Shapes intentionally match
# the FIT-60 stub presets so the swap is behavior-compatible for the common
# cases the existing UI tests rely on.
_FALLBACK_PRESETS: tuple[tuple[tuple[str, ...], tuple[str, ...], dict], ...] = (
    (("shake",), (),
     dict(item_name="Protein shake", meal_type="snack",
          calories=210, protein_g=30, carbs_g=14, fat_g=4, sodium_mg=180, fiber_g=2)),
    (("smoothie",), (),
     dict(item_name="Protein shake", meal_type="snack",
          calories=210, protein_g=30, carbs_g=14, fat_g=4, sodium_mg=180, fiber_g=2)),
    (("egg",), (),
     dict(item_name="Eggs and toast", meal_type="breakfast",
          calories=420, protein_g=24, carbs_g=36, fat_g=18, sodium_mg=520, fiber_g=4)),
    (("toast",), (),
     dict(item_name="Eggs and toast", meal_type="breakfast",
          calories=420, protein_g=24, carbs_g=36, fat_g=18, sodium_mg=520, fiber_g=4)),
    (("chipotle", "burrito"), ("bowl",),
     dict(item_name="Chipotle chicken burrito", meal_type="lunch",
          calories=1075, protein_g=51, carbs_g=116, fat_g=41, sodium_mg=2310, fiber_g=13)),
    (("chipotle", "bowl"), (),
     dict(item_name="Chipotle bowl", meal_type="lunch",
          calories=680, protein_g=42, carbs_g=72, fat_g=22, sodium_mg=1450, fiber_g=10)),
    (("salad",), (),
     dict(item_name="Salad", meal_type="lunch",
          calories=320, protein_g=18, carbs_g=22, fat_g=18, sodium_mg=460, fiber_g=6)),
    (("chicken",), (),
     dict(item_name="Chicken and rice", meal_type="dinner",
          calories=560, protein_g=40, carbs_g=58, fat_g=14, sodium_mg=720, fiber_g=4)),
    (("rice",), (),
     dict(item_name="Chicken and rice", meal_type="dinner",
          calories=560, protein_g=40, carbs_g=58, fat_g=14, sodium_mg=720, fiber_g=4)),
    (("yogurt",), (),
     dict(item_name="Yogurt", meal_type="snack",
          calories=180, protein_g=14, carbs_g=22, fat_g=4, sodium_mg=90, fiber_g=1)),
    (("coffee",), (),
     dict(item_name="Coffee", meal_type="snack",
          calories=5, protein_g=0, carbs_g=0, fat_g=0, sodium_mg=5, fiber_g=0)),
    (("banana",), (),
     dict(item_name="Banana", meal_type="snack",
          calories=105, protein_g=1, carbs_g=27, fat_g=0, sodium_mg=1, fiber_g=3)),
    (("oatmeal",), (),
     dict(item_name="Oatmeal", meal_type="breakfast",
          calories=300, protein_g=10, carbs_g=54, fat_g=6, sodium_mg=180, fiber_g=8)),
    (("oats",), (),
     dict(item_name="Oatmeal", meal_type="breakfast",
          calories=300, protein_g=10, carbs_g=54, fat_g=6, sodium_mg=180, fiber_g=8)),
    (("pasta",), (),
     dict(item_name="Pasta", meal_type="dinner",
          calories=520, protein_g=18, carbs_g=78, fat_g=14, sodium_mg=720, fiber_g=4)),
    (("popcorn",), (),
     dict(item_name="Popcorn", meal_type="snack",
          calories=300, protein_g=5, carbs_g=36, fat_g=18, sodium_mg=520, fiber_g=6)),
    (("sandwich",), (),
     dict(item_name="Sandwich", meal_type="lunch",
          calories=480, protein_g=24, carbs_g=48, fat_g=20, sodium_mg=920, fiber_g=4)),
)

_FALLBACK_DEFAULT = dict(
    item_name="Meal", meal_type="snack", calories=400, protein_g=22,
    carbs_g=40, fat_g=15, sodium_mg=560, fiber_g=4,
)


_PARSER_SYSTEM_PROMPT = """You are a precise nutrition estimator for a fitness app.

Given a short free-form meal description, output a SINGLE JSON object that
estimates the meal. Output JSON only — no prose, no preamble, no markdown,
no extra fields.

Schema (every field required, no extras):
{
  "item_name": string,
  "portion_description": string or null,
  "meal_type": "breakfast" | "lunch" | "dinner" | "snack",
  "calories": integer,
  "protein_g": number,
  "carbs_g": number,
  "fat_g": number,
  "sodium_mg": integer,
  "fiber_g": number,
  "confidence": number between 0 and 1,
  "ambiguous": boolean,
  "uncertainty_notes": array of short strings
}

Rules:
- Be conservative about portion. If the user says "half", "shared", or "a bit",
  scale the macros down and reflect that in portion_description.
- If the description is vague ("snacks", "buffet", "some food", "?"), set
  ambiguous=true and confidence below 0.5.
- meal_type defaults to "snack" for between-meal items, "breakfast" for
  morning items, "lunch" or "dinner" for full meals.
- Never invent specifics not implied by the text. Prefer common-sense averages.
- Brand notes: keep Chipotle menu categories distinct. A Chipotle burrito,
  bowl, salad, quesadilla, and tacos are different items; do not convert one
  category into another unless the user text says so.
- Output JSON only."""


def _clean_estimate(parsed: dict) -> None:
    """In-place cleanup before validation.

    Drops unknown keys to keep the model from leaking raw prompt fragments,
    trace data, or image references through the response. Also strips
    parser-controlled fields (``source``) so the model cannot lie about
    provenance — the parser re-applies them after validation. Coerces
    simple missing-field cases to safe defaults.
    """
    if not isinstance(parsed, dict):
        raise LmStudioError(f"expected dict, got {type(parsed).__name__}")
    for key in list(parsed.keys()):
        if key in _PARSER_CONTROLLED_KEYS:
            del parsed[key]
        elif key not in _ALLOWED_ESTIMATE_KEYS and key != "_meta":
            del parsed[key]
    parsed.setdefault("ambiguous", False)
    parsed.setdefault("uncertainty_notes", [])
    parsed.setdefault("portion_description", None)


def _validate_estimate(parsed: dict) -> None:
    """Strict schema check — raise LmStudioError on mismatch so the
    candidate-retry loop in lm_studio_adapter advances to the next endpoint.
    """
    required_types: dict[str, tuple] = {
        "item_name": (str,),
        "portion_description": (str, type(None)),
        "meal_type": (str,),
        "calories": (int, float),
        "protein_g": (int, float),
        "carbs_g": (int, float),
        "fat_g": (int, float),
        "sodium_mg": (int, float),
        "fiber_g": (int, float),
        "confidence": (int, float),
        "ambiguous": (bool,),
        "uncertainty_notes": (list,),
    }
    for key, types in required_types.items():
        if key not in parsed:
            raise LmStudioError(f"missing field: {key}")
        val = parsed[key]
        # bool is a subclass of int in Python, so isinstance(True, (int, float))
        # is True. Reject booleans explicitly for any field where bool isn't
        # in the allowed type list (numeric, string, etc.) — otherwise a
        # malformed "calories": true would pass through and auto-log.
        if isinstance(val, bool) and bool not in types:
            raise LmStudioError(f"wrong type for {key}: bool not allowed")
        if not isinstance(val, types):
            raise LmStudioError(
                f"wrong type for {key}: {type(val).__name__}"
            )
    if not parsed["item_name"].strip():
        raise LmStudioError("item_name is empty")
    if parsed["meal_type"] not in ALLOWED_MEAL_TYPES:
        raise LmStudioError(f"invalid meal_type: {parsed['meal_type']}")
    conf = float(parsed["confidence"])
    if not (0.0 <= conf <= 1.0):
        raise LmStudioError(f"confidence out of range: {conf}")
    cals = float(parsed["calories"])
    if not (0 <= cals <= _CALORIE_MAX):
        raise LmStudioError(f"calories out of plausible range: {cals}")
    for macro_key in ("protein_g", "carbs_g", "fat_g", "fiber_g"):
        val = float(parsed[macro_key])
        if not (0 <= val <= _MACRO_MAX):
            raise LmStudioError(f"{macro_key} out of plausible range: {val}")
    sodium = float(parsed["sodium_mg"])
    if not (0 <= sodium <= _SODIUM_MAX):
        raise LmStudioError(f"sodium_mg out of plausible range: {sodium}")
    for note in parsed["uncertainty_notes"]:
        if not isinstance(note, str):
            raise LmStudioError("uncertainty_notes must be array of strings")


def _fallback_estimate(text: str) -> dict:
    """Deterministic estimate produced when the local LLM is unavailable.

    Keyword-matches a small preset table that mirrors the FIT-60 stub so
    behavior is continuous, and lowers confidence (below the LM Studio
    ceiling) so the endpoint downgrades to pending review unless the entry
    is unambiguously simple. Returns the estimate with ``source`` already
    set to ``fallback_text_estimate`` so it round-trips through the
    pending-review accept flow.
    """
    norm = (text or "").lower().strip()
    estimate: dict = dict(_FALLBACK_DEFAULT)
    portion_description: Optional[str] = None
    matched = False
    for must_include, must_exclude, preset in _FALLBACK_PRESETS:
        includes_match = all(k in norm for k in must_include)
        excludes_match = any(k in norm for k in must_exclude)
        if includes_match and not excludes_match:
            estimate = dict(preset)
            matched = True
            break
    if "half" in norm:
        for key in ("calories", "protein_g", "carbs_g", "fat_g", "sodium_mg", "fiber_g"):
            value = estimate.get(key)
            if isinstance(value, float):
                estimate[key] = round(value / 2, 1)
            elif isinstance(value, int):
                estimate[key] = value // 2
        portion_description = "approx half portion"

    ambiguous = any(token in norm for token in _AMBIGUOUS_TOKENS)
    if not norm:
        confidence = 0.0
        ambiguous = True
    elif ambiguous:
        confidence = 0.45
    elif matched:
        confidence = 0.6
    else:
        confidence = 0.5

    notes: list[str] = []
    if ambiguous:
        notes.append("Portion or items are unclear — confirm before it counts toward today.")
    if not matched and norm:
        notes.append("Estimated from a generic meal profile; review macros if accuracy matters.")

    return {
        "item_name": estimate["item_name"],
        "portion_description": portion_description,
        "meal_type": estimate.get("meal_type"),
        "calories": estimate.get("calories"),
        "protein_g": estimate.get("protein_g"),
        "carbs_g": estimate.get("carbs_g"),
        "fat_g": estimate.get("fat_g"),
        "sodium_mg": estimate.get("sodium_mg"),
        "fiber_g": estimate.get("fiber_g"),
        "confidence": confidence,
        "ambiguous": ambiguous,
        "uncertainty_notes": notes,
        "source": "fallback_text_estimate",
    }


def _post_process(estimate: dict, *, source_text: str) -> dict:
    """Apply local heuristics on top of the model output.

    The LLM is often over-confident on inputs we know are messy. Force-
    flag ambiguous tokens and lower confidence so the endpoint downgrades
    to pending review for the same inputs as the FIT-60 stub used to.
    """
    norm = (source_text or "").lower()
    if any(token in norm for token in _AMBIGUOUS_TOKENS):
        estimate["ambiguous"] = True
        estimate["confidence"] = min(float(estimate["confidence"]), 0.55)
        note = "Portion or items are unclear — confirm before it counts toward today."
        if note not in estimate["uncertainty_notes"]:
            estimate["uncertainty_notes"] = [note, *estimate["uncertainty_notes"]]
    return estimate


def parse_meal_text(
    text: str,
    *,
    timestamp: Optional[str] = None,  # noqa: ARG001 — accepted for forward-compat
) -> dict:
    """Convert free-form meal text into a sanitized food estimate.

    Tries LM Studio first via the shared completion path (primary + fallback
    candidates, schema-validated JSON output). On any failure — unreachable
    endpoint, invalid JSON, schema mismatch, lock timeout — returns a
    deterministic fallback estimate. Never raises.

    Returns a dict shaped:
        {
            "estimate": {
                "item_name", "portion_description", "meal_type",
                "calories", "protein_g", "carbs_g", "fat_g",
                "sodium_mg", "fiber_g",
                "confidence", "ambiguous", "uncertainty_notes",
                "source",          # parser-controlled, never trusted from model
            },
            "fallback_used": bool,
        }

    ``source`` lives inside the estimate so it round-trips through the
    pending-review accept handler in app.py (which reads
    ``estimate.get("source")`` to label the persisted food_log).

    Never includes raw prompt content, raw model output, chain of thought,
    image references, or any other model trace.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return {
            "estimate": _fallback_estimate(""),
            "fallback_used": True,
        }

    try:
        branded_estimate = branded_food_lookup.lookup(cleaned)
    except Exception:
        branded_estimate = None
    if branded_estimate:
        branded_estimate = _post_process(branded_estimate, source_text=cleaned)
        return {
            "estimate": branded_estimate,
            "fallback_used": False,
        }

    payload = {
        "messages": [
            {"role": "system", "content": _PARSER_SYSTEM_PROMPT},
            {"role": "user", "content": cleaned[:500]},
        ],
        "temperature": 0.0,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
    }

    # Respect the shared LM Studio inference lock so concurrent meal
    # submissions don't stampede the local model — matches the pattern the
    # Adjust Plan and other adapter entry points use.
    timeout = LM_STUDIO_ANALYZE_TIMEOUT_SEC
    acquired = _INFERENCE_LOCK.acquire(timeout=timeout + 1)
    if not acquired:
        return {
            "estimate": _fallback_estimate(cleaned),
            "fallback_used": True,
        }
    try:
        try:
            parsed = _completion_json(
                "/v1/chat/completions",
                payload,
                timeout,
                validate=_validate_estimate,
                clean=_clean_estimate,
            )
        except LmStudioError:
            return {
                "estimate": _fallback_estimate(cleaned),
                "fallback_used": True,
            }
    finally:
        _INFERENCE_LOCK.release()

    parsed.pop("_meta", None)
    estimate = _post_process(parsed, source_text=cleaned)
    # Parser-controlled provenance: re-applied after the model output is
    # accepted so the model can't lie about which path produced the estimate.
    estimate["source"] = "ai_text_estimate"
    return {
        "estimate": estimate,
        "fallback_used": False,
    }
