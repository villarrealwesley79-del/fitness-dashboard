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

import os
import re
from typing import Iterable, Optional

import branded_food_lookup
import personal_vocab
from lm_studio_adapter import (
    LmStudioError,
    MEAL_TEXT_LOCK_ACQUIRE_SEC,
    _completion_json,
    _INFERENCE_LOCK,
    _MEAL_TEXT_INFERENCE_LOCK,
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

_AI_UNAVAILABLE_NOTE = "Rough estimate — AI didn't run; review before logging."
_LOCK_CONTENTION_NOTE = "Skipped AI because meal-text inference is busy; rough estimate, review before logging."
LM_STUDIO_MEAL_TEXT_TIMEOUT_SEC = float(os.environ.get("LM_STUDIO_MEAL_TEXT_TIMEOUT_SEC", "45"))
FALLBACK_REASON_EMPTY_INPUT = "empty_input"
FALLBACK_REASON_NEEDS_QUANTITY = "needs_quantity"
FALLBACK_REASON_TIMEOUT = "timeout"
FALLBACK_REASON_INVALID_JSON = "invalid_json"
FALLBACK_REASON_SCHEMA_MISMATCH = "schema_mismatch"
FALLBACK_REASON_LOCK_TIMEOUT = "lock_timeout"
FALLBACK_REASON_ALL_ENDPOINTS_FAILED = "all_endpoints_failed"
FALLBACK_REASON_VALUES = frozenset({
    FALLBACK_REASON_EMPTY_INPUT,
    FALLBACK_REASON_NEEDS_QUANTITY,
    FALLBACK_REASON_TIMEOUT,
    FALLBACK_REASON_INVALID_JSON,
    FALLBACK_REASON_SCHEMA_MISMATCH,
    FALLBACK_REASON_LOCK_TIMEOUT,
    FALLBACK_REASON_ALL_ENDPOINTS_FAILED,
})


# Deterministic preset table used when LM Studio is unreachable, returns
# unparseable JSON, or fails schema validation. Shapes intentionally match
# the FIT-60 stub presets so the swap is behavior-compatible for the common
# cases the existing UI tests rely on.
_FALLBACK_PRESETS: tuple[tuple[tuple[str, ...], tuple[str, ...], dict], ...] = (
    (("poppers",), (),
     dict(item_name="Bacon wrapped jalapeno poppers", meal_type="snack",
          calories=130, protein_g=4, carbs_g=2, fat_g=12, sodium_mg=180, fiber_g=0)),
    (("popper",), (),
     dict(item_name="Bacon wrapped jalapeno popper", meal_type="snack",
          calories=130, protein_g=4, carbs_g=2, fat_g=12, sodium_mg=180, fiber_g=0)),
    (("jerky",), (),
     dict(item_name="Beef jerky", meal_type="snack",
          calories=80, protein_g=14, carbs_g=4, fat_g=1, sodium_mg=520, fiber_g=0)),
    (("breakfast", "taco"), (),
     dict(item_name="Breakfast taco", meal_type="breakfast",
          calories=320, protein_g=14, carbs_g=28, fat_g=16, sodium_mg=620, fiber_g=3)),
    (("egg", "taco"), (),
     dict(item_name="Breakfast taco", meal_type="breakfast",
          calories=320, protein_g=14, carbs_g=28, fat_g=16, sodium_mg=620, fiber_g=3)),
    (("taco",), (),
     dict(item_name="Taco", meal_type="lunch",
          calories=220, protein_g=10, carbs_g=22, fat_g=10, sodium_mg=420, fiber_g=3)),
    (("potato",), (),
     dict(item_name="Potato", meal_type="snack",
          calories=160, protein_g=4, carbs_g=37, fat_g=0, sodium_mg=20, fiber_g=4)),
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
    (("chipotle", "chicken", "burrito"), ("bowl",),
     dict(item_name="Chipotle chicken burrito", meal_type="lunch",
          calories=1075, protein_g=51, carbs_g=116, fat_g=41, sodium_mg=2310, fiber_g=13)),
    (("chipotle", "burrito"), ("bowl",),
     dict(item_name="Chipotle burrito", meal_type="lunch",
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
_NO_BRANDED_MATCH_NOTE = "Low confidence — no branded match found in Nutritionix, USDA, or Open Food Facts."
_QUANTITY_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "dozen": 12,
    "couple": 2,
}
_QUANTITY_RE = re.compile(r"\b(?:\d+(?:\.\d+)?|" + "|".join(sorted(_QUANTITY_WORDS)) + r")\b", re.I)
_MULTI_ITEM_SPLIT_RE = re.compile(r"\s*(?:\band\b|\+)\s+", re.I)
_QUANTITY_ONLY_TOKENS = {
    "oz", "ounce", "ounces", "serving", "servings", "piece", "pieces",
    "slice", "slices", "item", "items",
}
_MEASUREMENT_UNITS = {
    "oz", "ounce", "ounces", "g", "gram", "grams", "lb", "lbs", "pound", "pounds",
    "cup", "cups", "tbsp", "tablespoon", "tablespoons", "tsp", "teaspoon", "teaspoons",
    "calorie", "calories", "kcal",
}
_COMBO_PRESET_TOKEN_SETS = (
    {"egg", "toast"},
    {"chicken", "rice"},
)
_NON_SCALABLE_FALLBACK_ITEMS = {"Eggs and toast", "Chicken and rice"}
_DISH_MODIFIER_TOKENS = {
    "bacon", "bean", "beans", "beef", "brisket", "cheese", "chicken",
    "egg", "eggs", "fish", "jalapeno", "jalapenos", "potato", "potatoes",
    "rice", "sausage", "steak",
}
_DISH_HEAD_TOKENS = {"taco", "tacos", "burrito", "burritos", "sandwich", "sandwiches"}

_FALLBACK_TOKEN_ALIASES = {
    "chipotole": "chipotle",
    "chipoltle": "chipotle",
    "chiptole": "chipotle",
}


def _fallback_tokens(text: str) -> set[str]:
    raw_tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    tokens = set(raw_tokens)
    for token in raw_tokens:
        alias = _FALLBACK_TOKEN_ALIASES.get(token)
        if alias:
            tokens.add(alias)
    return tokens


def _has_fallback_token(tokens: set[str], term: str) -> bool:
    if term in tokens:
        return True
    if f"{term}s" in tokens:
        return True
    if f"{term}es" in tokens:
        return True
    return False


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

_MEAL_TEXT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "meal_text_estimate",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "item_name": {"type": "string"},
                "portion_description": {"type": ["string", "null"]},
                "meal_type": {"type": "string", "enum": sorted(ALLOWED_MEAL_TYPES)},
                "calories": {"type": "number", "minimum": 0, "maximum": _CALORIE_MAX},
                "protein_g": {"type": "number", "minimum": 0, "maximum": _MACRO_MAX},
                "carbs_g": {"type": "number", "minimum": 0, "maximum": _MACRO_MAX},
                "fat_g": {"type": "number", "minimum": 0, "maximum": _MACRO_MAX},
                "sodium_mg": {"type": "number", "minimum": 0, "maximum": _SODIUM_MAX},
                "fiber_g": {"type": "number", "minimum": 0, "maximum": _MACRO_MAX},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "ambiguous": {"type": "boolean"},
                "uncertainty_notes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 6,
                },
            },
            "required": [
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
            ],
        },
    },
}


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


def _fallback_quantity_factor(text: str) -> float:
    match = _QUANTITY_RE.search(text or "")
    if not match:
        return 1.0
    tail = (text or "")[match.end():]
    if tail.lstrip().startswith("%"):
        return 1.0
    unit_match = re.match(r"\s*([a-zA-Z]+)\b", tail)
    if unit_match and unit_match.group(1).lower() in _MEASUREMENT_UNITS:
        return 1.0
    raw = match.group(0).lower()
    if raw in _QUANTITY_WORDS:
        return float(_QUANTITY_WORDS[raw])
    try:
        value = float(raw)
    except ValueError:
        return 1.0
    return max(0.25, min(value, 12.0))


def _fallback_has_count_quantity(text: str) -> bool:
    match = _QUANTITY_RE.search(text or "")
    if not match:
        return False
    tail = (text or "")[match.end():]
    if tail.lstrip().startswith("%"):
        return False
    unit_match = re.match(r"\s*([a-zA-Z]+)\b", tail)
    return not (unit_match and unit_match.group(1).lower() in _MEASUREMENT_UNITS)


def _fallback_parts_are_combo_preset(parts: list[str]) -> bool:
    if len(parts) != 2:
        return False
    raw_tokens = set(re.findall(r"[a-z0-9]+", " ".join(parts).lower()))
    tokens = {
        token for token in raw_tokens
        if token not in _QUANTITY_ONLY_TOKENS and token not in _MEASUREMENT_UNITS
    }
    quantity_tokens = set(_QUANTITY_WORDS) | {token for token in tokens if re.fullmatch(r"\d+(?:\.\d+)?", token)}
    egg_toast_tokens = {"egg", "eggs", "toast"} | quantity_tokens
    chicken_rice_tokens = {"chicken", "rice"} | quantity_tokens
    return tokens <= egg_toast_tokens or tokens <= chicken_rice_tokens


def _fallback_parts_are_single_dish_modifier(parts: list[str]) -> bool:
    if len(parts) != 2:
        return False
    left, right = parts
    if _fallback_has_count_quantity(left) or _fallback_has_count_quantity(right):
        return False
    left_tokens = {
        token for token in _fallback_tokens(left)
        if token not in _QUANTITY_ONLY_TOKENS and token not in _MEASUREMENT_UNITS
    }
    right_tokens = _fallback_tokens(right)
    if not left_tokens or not (right_tokens & _DISH_HEAD_TOKENS):
        return False
    return left_tokens <= _DISH_MODIFIER_TOKENS


def _fallback_merge_combo_parts(parts: list[str]) -> list[str]:
    merged: list[str] = []
    index = 0
    while index < len(parts):
        if (
            index + 1 < len(parts)
            and (
                _fallback_parts_are_combo_preset([parts[index], parts[index + 1]])
                or _fallback_parts_are_single_dish_modifier([parts[index], parts[index + 1]])
            )
        ):
            merged.append(f"{parts[index]} and {parts[index + 1]}")
            index += 2
        else:
            merged.append(parts[index])
            index += 1
    return merged


def _fallback_item_breakdown(
    text: str,
    *,
    include_ai_note: bool = True,
    reason: str | None = None,
) -> list[dict]:
    parts = []
    for raw_part in _MULTI_ITEM_SPLIT_RE.split(text or ""):
        part = raw_part.strip(" .;")
        if not part:
            continue
        tokens = set(re.findall(r"[a-z0-9]+", part.lower()))
        non_quantity_tokens = {
            token for token in tokens
            if token not in _QUANTITY_ONLY_TOKENS and token not in _QUANTITY_WORDS and not re.fullmatch(r"\d+(?:\.\d+)?", token)
        }
        if not non_quantity_tokens and parts:
            parts[-1] = f"{parts[-1]} {part}".strip()
            continue
        parts.append(part)
    parts = _fallback_merge_combo_parts(parts)
    if len(parts) < 2:
        return []
    if _fallback_parts_are_combo_preset(parts):
        return []
    items = []
    skipped_unknown = False
    recognized_count = 0
    for part in parts[:6]:
        estimate = _single_fallback_estimate(part, include_ai_note=include_ai_note, reason=reason)
        if estimate.get("item_name") == _FALLBACK_DEFAULT["item_name"]:
            skipped_unknown = True
            estimate = _unknown_split_fallback_estimate(part, include_ai_note=include_ai_note, reason=reason)
        else:
            recognized_count += 1
        notes = estimate.setdefault("uncertainty_notes", [])
        if "Multi-item fallback split from text; confirm each quantity." not in notes:
            notes.append("Multi-item fallback split from text; confirm each quantity.")
        quantity = _fallback_quantity_factor(part)
        index = len(items) + 1
        items.append({
            "item_id": f"fallback-item-{index}",
            "item_name": estimate["item_name"],
            "quantity": int(quantity) if quantity.is_integer() else round(quantity, 2),
            "portion_hint": estimate.get("portion_description"),
            "estimate": estimate,
        })
    if recognized_count == 0:
        return []
    if skipped_unknown:
        note = "Some unrecognized text was left out of the fallback item split; review before logging."
        for item in items:
            notes = item["estimate"].setdefault("uncertainty_notes", [])
            if note not in notes:
                notes.append(note)
    return items


def _unknown_split_fallback_estimate(
    text: str,
    *,
    include_ai_note: bool = True,
    reason: str | None = None,
) -> dict:
    item_name = re.sub(r"\s+", " ", str(text or "").strip(" .;"))[:80] or "Unrecognized item"
    notes = []
    if include_ai_note:
        notes.append(_AI_UNAVAILABLE_NOTE)
    notes.append("Text fallback could not estimate this item; enter nutrition manually before logging.")
    estimate = {
        "item_name": item_name,
        "portion_description": None,
        "meal_type": "snack",
        "calories": 0,
        "protein_g": 0,
        "carbs_g": 0,
        "fat_g": 0,
        "sodium_mg": 0,
        "fiber_g": 0,
        "confidence": 0.0,
        "ambiguous": True,
        "uncertainty_notes": notes,
        "source": "fallback_text_estimate",
    }
    if reason is not None:
        estimate["fallback_reason"] = reason
    return estimate


def _single_fallback_estimate(
    text: str,
    *,
    include_ai_note: bool = True,
    reason: str | None = None,
) -> dict:
    """Deterministic estimate produced when the local LLM is unavailable.

    Keyword-matches a small preset table that mirrors the FIT-60 stub so
    behavior is continuous, and lowers confidence (below the LM Studio
    ceiling) so the endpoint downgrades to pending review unless the entry
    is unambiguously simple. Returns the estimate with ``source`` already
    set to ``fallback_text_estimate`` so it round-trips through the
    pending-review accept flow.
    """
    norm = (text or "").lower().strip()
    tokens = _fallback_tokens(norm)
    estimate: dict = dict(_FALLBACK_DEFAULT)
    portion_description: Optional[str] = None
    matched = False
    for must_include, must_exclude, preset in _FALLBACK_PRESETS:
        includes_match = all(_has_fallback_token(tokens, k) for k in must_include)
        excludes_match = any(_has_fallback_token(tokens, k) for k in must_exclude)
        if includes_match and not excludes_match:
            estimate = dict(preset)
            matched = True
            break
    preserve_combo_quantity = estimate.get("item_name") in _NON_SCALABLE_FALLBACK_ITEMS or any(
        all(_has_fallback_token(tokens, token) for token in combo)
        for combo in _COMBO_PRESET_TOKEN_SETS
    )
    quantity_factor = 1.0 if preserve_combo_quantity else _fallback_quantity_factor(norm)
    has_half = "half" in norm
    if has_half:
        quantity_factor *= 0.5
    if quantity_factor != 1.0:
        _scale_estimate_macros(estimate, quantity_factor)
        if has_half:
            portion_description = "approx half portion"
        else:
            portion_description = f"approx {int(quantity_factor) if quantity_factor.is_integer() else round(quantity_factor, 2)} servings"
    elif has_half and not _fallback_has_count_quantity(norm):
        _scale_estimate_macros(estimate, 0.5)
        portion_description = "approx half portion"
    elif has_half:
        portion_description = "approx 1 serving"

    ambiguous = any(token in norm for token in _AMBIGUOUS_TOKENS)
    if not norm:
        confidence = 0.0
        ambiguous = True
    elif ambiguous:
        confidence = 0.3
    elif matched:
        confidence = 0.55
    else:
        confidence = 0.25

    notes: list[str] = []
    if include_ai_note and norm:
        notes.append(_AI_UNAVAILABLE_NOTE)
    if ambiguous:
        notes.append("Portion or items are unclear — confirm before it counts toward today.")
    if not matched and norm:
        notes.append("Estimated from a generic meal profile; review macros if accuracy matters.")
    if matched and "chipotle" in tokens:
        notes.append("Local Chipotle fallback uses a typical menu profile; verify modifiers if exact macros matter.")

    result = {
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
    if reason is not None:
        result["fallback_reason"] = reason
    return result


def _fallback_estimate(
    text: str,
    *,
    include_ai_note: bool = True,
    reason: str | None = None,
) -> dict:
    items = _fallback_item_breakdown(text, include_ai_note=include_ai_note, reason=reason)
    if not items:
        return _single_fallback_estimate(text, include_ai_note=include_ai_note, reason=reason)
    aggregate = _single_fallback_estimate(text, include_ai_note=include_ai_note, reason=reason)
    aggregate["item_name"] = "; ".join(item["item_name"] for item in items)[:160]
    aggregate["portion_description"] = "; ".join(
        str(item.get("portion_hint") or item["item_name"]) for item in items
    )[:300]
    for key in ("calories", "protein_g", "carbs_g", "fat_g", "sodium_mg", "fiber_g"):
        total = sum(float(item["estimate"].get(key) or 0) for item in items)
        aggregate[key] = int(round(total)) if key in {"calories", "sodium_mg"} else round(total, 1)
    aggregate["confidence"] = min(float(aggregate.get("confidence") or 0), 0.55)
    aggregate["ambiguous"] = True
    notes = aggregate.setdefault("uncertainty_notes", [])
    if "Multi-item fallback split from text; confirm each quantity." not in notes:
        notes.append("Multi-item fallback split from text; confirm each quantity.")
    aggregate["items"] = items
    return aggregate


def _scale_estimate_macros(estimate: dict, factor: float) -> None:
    for key in ("calories", "protein_g", "carbs_g", "fat_g", "sodium_mg", "fiber_g"):
        value = estimate.get(key)
        if isinstance(value, float):
            estimate[key] = round(value * factor, 1)
        elif isinstance(value, int):
            estimate[key] = int(value * factor)


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


def _should_parse_private_label_miss(text: str) -> bool:
    """Let the parser handle quantity-bearing branded misses so it can scale portions."""
    return bool(_QUANTITY_RE.search(text or ""))


def _add_no_branded_match_note(estimate: dict) -> dict:
    estimate["ambiguous"] = True
    try:
        confidence = float(estimate.get("confidence", 0.45))
    except (TypeError, ValueError):
        confidence = 0.45
    estimate["confidence"] = min(confidence, 0.45)
    notes = estimate.setdefault("uncertainty_notes", [])
    if _NO_BRANDED_MATCH_NOTE not in notes:
        estimate["uncertainty_notes"] = [_NO_BRANDED_MATCH_NOTE, *notes]
    return estimate


def _fallback_reason_from_lm_error(exc: LmStudioError) -> str:
    message = str(exc).lower()
    if "invalid json" in message or "invalid envelope json" in message:
        return FALLBACK_REASON_INVALID_JSON
    if "timeout" in message:
        return FALLBACK_REASON_TIMEOUT
    if (
        "unexpected response shape" in message
        or "expected dict" in message
        or "missing field" in message
        or "wrong type" in message
        or "out of range" in message
        or "must be" in message
        or "invalid meal_type" in message
    ):
        return FALLBACK_REASON_SCHEMA_MISMATCH
    if message.startswith("all endpoints failed"):
        return FALLBACK_REASON_ALL_ENDPOINTS_FAILED
    if (
        "unreachable" in message
        or "network error" in message
        or message.startswith("http ")
        or "model not loaded" in message
    ):
        return FALLBACK_REASON_ALL_ENDPOINTS_FAILED
    return FALLBACK_REASON_SCHEMA_MISMATCH


def parse_meal_text(
    text: str,
    *,
    timestamp: Optional[str] = None,  # noqa: ARG001 — accepted for forward-compat
    user_id: int = 1,
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
            "estimate": _fallback_estimate("", reason=FALLBACK_REASON_EMPTY_INPUT),
            "fallback_used": True,
            "fallback_reason": FALLBACK_REASON_EMPTY_INPUT,
        }

    try:
        personal_estimate = personal_vocab.lookup(cleaned, user_id=user_id)
    except Exception:
        personal_estimate = None
    if personal_estimate:
        return {
            "estimate": personal_estimate,
            "fallback_used": False,
        }

    try:
        branded_estimate = None
        branded_lookup_attempted = branded_food_lookup.should_attempt_direct_lookup(cleaned)
        private_label_lookup = branded_food_lookup.is_private_label_query(cleaned)
        if branded_lookup_attempted:
            branded_estimate = branded_food_lookup.lookup(cleaned, user_id=user_id)
    except Exception:
        branded_estimate = None
        branded_lookup_attempted = False
        private_label_lookup = False
    if branded_estimate:
        branded_estimate = _post_process(branded_estimate, source_text=cleaned)
        return {
            "estimate": branded_estimate,
            "fallback_used": False,
        }
    private_label_miss = branded_lookup_attempted and private_label_lookup
    if private_label_miss and not _should_parse_private_label_miss(cleaned):
        estimate = _add_no_branded_match_note(_fallback_estimate(
            cleaned,
            reason=FALLBACK_REASON_NEEDS_QUANTITY,
        ))
        return {
            "estimate": estimate,
            "fallback_used": True,
            "fallback_reason": FALLBACK_REASON_NEEDS_QUANTITY,
        }

    payload = {
        "messages": [
            {"role": "system", "content": _PARSER_SYSTEM_PROMPT},
            {"role": "user", "content": cleaned[:500]},
        ],
        "temperature": 0.0,
        "max_tokens": 400,
        "response_format": _MEAL_TEXT_RESPONSE_FORMAT,
    }

    timeout = LM_STUDIO_MEAL_TEXT_TIMEOUT_SEC
    acquired = _MEAL_TEXT_INFERENCE_LOCK.acquire(timeout=MEAL_TEXT_LOCK_ACQUIRE_SEC)
    if not acquired:
        estimate = _fallback_estimate(
            cleaned,
            include_ai_note=False,
            reason=FALLBACK_REASON_LOCK_TIMEOUT,
        )
        notes = estimate.setdefault("uncertainty_notes", [])
        if _LOCK_CONTENTION_NOTE not in notes:
            notes.append(_LOCK_CONTENTION_NOTE)
        if private_label_miss:
            estimate = _add_no_branded_match_note(estimate)
        return {
            "estimate": estimate,
            "fallback_used": True,
            "fallback_reason": FALLBACK_REASON_LOCK_TIMEOUT,
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
        except LmStudioError as exc:
            fallback_reason = _fallback_reason_from_lm_error(exc)
            estimate = _fallback_estimate(cleaned, reason=fallback_reason)
            if private_label_miss:
                estimate = _add_no_branded_match_note(estimate)
            return {
                "estimate": estimate,
                "fallback_used": True,
                "fallback_reason": fallback_reason,
            }
    finally:
        _MEAL_TEXT_INFERENCE_LOCK.release()

    parsed.pop("_meta", None)
    estimate = _post_process(parsed, source_text=cleaned)
    if private_label_miss:
        estimate = _add_no_branded_match_note(estimate)
    # Parser-controlled provenance: re-applied after the model output is
    # accepted so the model can't lie about which path produced the estimate.
    estimate["source"] = "ai_text_estimate"
    return {
        "estimate": estimate,
        "fallback_used": False,
    }
