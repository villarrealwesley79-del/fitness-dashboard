"""Meal auto-log confidence policy (FIT-61).

Given a sanitized food estimate (from ``meal_text_parser.parse_meal_text``
or the FIT-60 image stub), return the theoretical policy decision: whether
the estimate qualifies for auto-log or needs explicit user review. Fresh
text, photo, and barcode routes currently override a ``logged`` result and
persist every new submission as ``pending_review``. Those routes use this
module only for confidence bands and reason codes; explicit acceptance is
the sole path into nutrition totals.

Design rules
~~~~~~~~~~~~

* **Theoretical auto-log** when the estimate is unambiguous,
  high-confidence, and macros are within physically plausible bounds.
* **Pending review** when the estimate is uncertain, ambiguous, or
  contains nonsense macros. The user must explicitly accept these
  before they count toward today's coaching context.
* **Reasons** are surfaced so the UI can explain the decision honestly
  (the user sees why their entry needs review).
* **Confidence bands** are explicit constants so test code and
  downstream telemetry can refer to them without copying magic numbers.
* Pure module — no I/O, no globals, no Flask/SQLAlchemy imports.
  Trivially unit-testable.

The thresholds are conservative on purpose. FIT-59 used a single 0.65
auto-log cutoff that allowed parser-fallback estimates (0.60) into
coaching context. FIT-61 raises the bar so the parser's deterministic
fallback flows through pending review by default.
"""

from __future__ import annotations

from typing import Iterable

# ──────────────────────────────────────────────────────────────────
# Public constants
# ──────────────────────────────────────────────────────────────────

#: Estimate must meet or exceed this confidence to be auto-logged.
HIGH_CONFIDENCE_THRESHOLD = 0.75

#: Estimates between ``MEDIUM`` and ``HIGH`` are merely "uncertain" —
#: they still fall to pending review but the reason copy is gentler.
MEDIUM_CONFIDENCE_THRESHOLD = 0.55

#: Macro-plausibility upper bounds. Above these the estimate is treated
#: as model error and forced to pending review regardless of confidence.
CALORIE_MAX = 5000
MACRO_GRAM_MAX = 500      # any of protein/carbs/fat/fiber grams per meal
SODIUM_MG_MAX = 12000     # 12 g sodium per meal is already a red flag

#: Decision statuses surfaced in the response payload. ``logged`` is the
#: legacy wire string from FIT-60; ``pending_review`` is the only other
#: value any consumer needs to handle.
STATUS_LOGGED = "logged"
STATUS_PENDING_REVIEW = "pending_review"

#: ``correction_state`` value to persist alongside the food_log row for
#: each decision. Mirrors the strings already recognised by
#: ``_nutrition_entry_pending_review`` in app.py.
CORRECTION_STATE_ACCEPTED = "accepted"
CORRECTION_STATE_PENDING_REVIEW = "pending_review"

#: Confidence band labels surfaced in telemetry / response metadata.
BAND_HIGH = "high"
BAND_MEDIUM = "medium"
BAND_LOW = "low"

#: Stable reason codes. The UI can map these to copy.
REASON_LOW_CONFIDENCE = "low_confidence"
REASON_MEDIUM_CONFIDENCE = "medium_confidence"
REASON_AMBIGUOUS_INPUT = "ambiguous_input"
REASON_IMPLAUSIBLE_CALORIES = "implausible_calories"
REASON_IMPLAUSIBLE_MACROS = "implausible_macros"
REASON_IMPLAUSIBLE_SODIUM = "implausible_sodium"
REASON_MISSING_CALORIES = "missing_calories"


# ──────────────────────────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────────────────────────

def _coerce_float(value, default=None):
    """Permissive numeric coercion. None/non-numeric return ``default``."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _classify_band(confidence: float) -> str:
    if confidence >= HIGH_CONFIDENCE_THRESHOLD:
        return BAND_HIGH
    if confidence >= MEDIUM_CONFIDENCE_THRESHOLD:
        return BAND_MEDIUM
    return BAND_LOW


def _macro_violations(estimate: dict) -> Iterable[str]:
    """Yield reason codes for any macros outside plausible bounds."""
    cals = _coerce_float(estimate.get("calories"))
    if cals is None:
        yield REASON_MISSING_CALORIES
    elif cals < 0 or cals > CALORIE_MAX:
        yield REASON_IMPLAUSIBLE_CALORIES
    for key in ("protein_g", "carbs_g", "fat_g", "fiber_g"):
        val = _coerce_float(estimate.get(key))
        if val is None:
            continue  # missing macros are tolerated; the UI shows blanks
        if val < 0 or val > MACRO_GRAM_MAX:
            yield REASON_IMPLAUSIBLE_MACROS
            return  # one report is enough
    sodium = _coerce_float(estimate.get("sodium_mg"))
    if sodium is not None and (sodium < 0 or sodium > SODIUM_MG_MAX):
        yield REASON_IMPLAUSIBLE_SODIUM


# ──────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────

def evaluate_meal_log(estimate: dict) -> dict:
    """Return the theoretical policy decision for ``estimate``.

    Parameters
    ----------
    estimate
        A sanitized food estimate dict (FIT-5 schema): ``calories``,
        ``protein_g``, ``carbs_g``, ``fat_g``, ``sodium_mg``, ``fiber_g``,
        ``confidence``, ``ambiguous``, ``uncertainty_notes``, etc.
        Missing or malformed fields are tolerated — they push the
        decision toward pending review.

    Returns
    -------
    dict
        ``{
            "status": "logged" | "pending_review",
            "correction_state": "accepted" | "pending_review",
            "confidence_band": "high" | "medium" | "low",
            "confidence": float,
            "reasons": [str],   # stable codes, see REASON_* constants
        }``

    The function is pure — it never raises and never inspects request
    state. A ``logged`` result describes policy eligibility, not current
    route behavior. Fresh capture routes force ``pending_review`` before
    persistence and use this result only for response metadata.
    """
    if not isinstance(estimate, dict):
        return {
            "status": STATUS_PENDING_REVIEW,
            "correction_state": CORRECTION_STATE_PENDING_REVIEW,
            "confidence_band": BAND_LOW,
            "confidence": 0.0,
            "reasons": [REASON_LOW_CONFIDENCE],
        }

    confidence = _coerce_float(estimate.get("confidence"), default=0.0) or 0.0
    band = _classify_band(confidence)
    ambiguous = bool(estimate.get("ambiguous"))

    reasons: list[str] = []
    if band == BAND_LOW:
        reasons.append(REASON_LOW_CONFIDENCE)
    elif band == BAND_MEDIUM:
        reasons.append(REASON_MEDIUM_CONFIDENCE)
    if ambiguous:
        reasons.append(REASON_AMBIGUOUS_INPUT)
    for code in _macro_violations(estimate):
        reasons.append(code)

    if reasons:
        return {
            "status": STATUS_PENDING_REVIEW,
            "correction_state": CORRECTION_STATE_PENDING_REVIEW,
            "confidence_band": band,
            "confidence": confidence,
            "reasons": reasons,
        }
    return {
        "status": STATUS_LOGGED,
        "correction_state": CORRECTION_STATE_ACCEPTED,
        "confidence_band": band,
        "confidence": confidence,
        "reasons": [],
    }
