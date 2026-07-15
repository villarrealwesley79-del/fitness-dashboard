"""Single-user personal meal vocabulary learned from accepted estimates."""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

import branded_food_lookup
import data_store
from meal_estimate_schema import sanitize_meal_estimate


MIN_ACCEPTS_FOR_EXACT = 3
MIN_ACCEPTS_FOR_FUZZY = MIN_ACCEPTS_FOR_EXACT
FUZZY_CUTOFF = 0.78
RETIRED_NUTRITION_SOURCES = {"nutritionix", "nutritionix_barcode"}


def record_accept(user_id: int, phrase: str | None, estimate: dict[str, Any]) -> dict | None:
    """Record that the user accepted this phrase as canonical."""
    normalized = _normalize(phrase)
    canonical = _canonical_estimate(estimate)
    if not normalized or not canonical:
        return None
    return data_store.upsert_personal_vocab_entry(
        user_id,
        normalized_input=normalized,
        phrase=(phrase or "").strip(),
        canonical_resolution=canonical,
        accepted=True,
    )


def record_correct(user_id: int, phrase: str | None, estimate: dict[str, Any]) -> dict | None:
    """Record a correction and downgrade the learned mapping."""
    normalized = _normalize(phrase)
    canonical = _canonical_estimate(estimate)
    if not normalized or not canonical:
        return None
    return data_store.upsert_personal_vocab_entry(
        user_id,
        normalized_input=normalized,
        phrase=(phrase or "").strip(),
        canonical_resolution=canonical,
        accepted=False,
    )


def record_negative_feedback(
    user_id: int,
    phrase: str | None,
    estimate: dict[str, Any] | None,
    feedback_type: str,
) -> dict | None:
    """Record skipped/deleted review feedback without creating a trusted mapping."""
    phrase_text = (phrase or "").strip()
    normalized = _normalize(phrase_text)
    if not normalized:
        return None
    canonical = _canonical_estimate(estimate or {}) or {
        "item_name": phrase_text[:500],
        "source": "negative_feedback",
    }
    return data_store.record_personal_vocab_negative_feedback(
        user_id,
        normalized_input=normalized,
        phrase=phrase_text,
        canonical_resolution=canonical,
        feedback_type=feedback_type,
    )


def lookup(phrase: str | None, *, user_id: int = 1) -> dict | None:
    """Return a personal-vocab estimate when the learned mapping is trusted."""
    normalized = _normalize(phrase)
    if not normalized:
        return None
    exact = data_store.get_personal_vocab_entry(user_id, normalized)
    if exact:
        if _trusted(exact, minimum_accepts=MIN_ACCEPTS_FOR_EXACT):
            return _estimate_from_entry(exact, trusted_exact=True)
        return None

    entries = data_store.list_personal_vocab_entries(user_id)
    trusted = [
        entry
        for entry in entries
        if entry.get("normalized_input") != normalized
        and _trusted(entry, minimum_accepts=MIN_ACCEPTS_FOR_FUZZY)
    ]
    candidates = {entry["normalized_input"]: entry for entry in trusted}
    abbreviation = _abbreviation_match(normalized, candidates.keys())
    if abbreviation:
        return _estimate_from_entry(candidates[abbreviation])
    typo = _typo_match(normalized, candidates.keys())
    if typo:
        return _estimate_from_entry(candidates[typo])
    return None


def _normalize(phrase: str | None) -> str:
    return branded_food_lookup.normalize_meal_text(phrase or "")


def _canonical_estimate(estimate: dict[str, Any]) -> dict | None:
    if not isinstance(estimate, dict):
        return None
    try:
        canonical = sanitize_meal_estimate(estimate, plausible_ranges=True)
    except Exception:
        return None
    for key in (
        "external_food_id",
        "verified_source_url",
        "data_fetched_at",
        "portion_basis",
        "brand_id",
        "underlying_source",
    ):
        if estimate.get(key) is not None:
            canonical[key] = estimate[key]
    return canonical


def _trusted(entry: dict, *, minimum_accepts: int) -> bool:
    return (
        int(entry.get("accept_count") or 0) >= minimum_accepts
        and int(entry.get("correct_count") or 0) == 0
        and not _uses_retired_nutrition_source(entry)
    )


def _uses_retired_nutrition_source(entry: dict) -> bool:
    canonical = entry.get("canonical_resolution")
    if not isinstance(canonical, dict):
        return False
    values = [canonical.get("source"), canonical.get("underlying_source")]
    underlying_sources = canonical.get("underlying_sources")
    if isinstance(underlying_sources, list):
        values.extend(underlying_sources)
    provenance = {
        part.strip().lower()
        for value in values
        if isinstance(value, str)
        for part in value.split("+")
        if part.strip()
    }
    return bool(provenance & RETIRED_NUTRITION_SOURCES)


def _abbreviation_match(query: str, candidate_keys) -> str | None:
    query_tokens = query.split()
    if not query_tokens or any(len(token) < 3 for token in query_tokens):
        return None
    for candidate in candidate_keys:
        candidate_tokens = candidate.split()
        if len(query_tokens) != len(candidate_tokens):
            continue
        if all(_token_matches_abbreviation(q, c) for q, c in zip(query_tokens, candidate_tokens)):
            return candidate
    return None


def _typo_match(query: str, candidate_keys) -> str | None:
    query_tokens = query.split()
    if not query_tokens:
        return None
    for candidate in candidate_keys:
        candidate_tokens = candidate.split()
        if len(query_tokens) != len(candidate_tokens):
            continue
        if all(_token_matches_typo(q, c) for q, c in zip(query_tokens, candidate_tokens)):
            return candidate
    return None


def _token_matches_typo(query_token: str, candidate_token: str) -> bool:
    if query_token == candidate_token:
        return True
    if len(query_token) < 4 or len(candidate_token) < 4:
        return False
    if query_token[0] != candidate_token[0] or query_token[-1] != candidate_token[-1]:
        return False
    return SequenceMatcher(None, query_token, candidate_token).ratio() >= FUZZY_CUTOFF


def _token_matches_abbreviation(query_token: str, candidate_token: str) -> bool:
    if query_token == candidate_token or candidate_token.startswith(query_token):
        return True
    pos = 0
    for char in candidate_token:
        if pos < len(query_token) and query_token[pos] == char:
            pos += 1
    return pos == len(query_token)


def _estimate_from_entry(entry: dict, *, trusted_exact: bool = False) -> dict | None:
    canonical = entry.get("canonical_resolution")
    if not isinstance(canonical, dict):
        return None
    estimate = dict(canonical)
    underlying = canonical.get("source")
    estimate["underlying_source"] = canonical.get("underlying_source") or underlying
    estimate["source"] = "personal_vocab"
    if trusted_exact:
        estimate["confidence"] = max(float(estimate.get("confidence") or 0), 0.9)
        estimate["ambiguous"] = False
    estimate.setdefault("uncertainty_notes", [])
    estimate["personal_vocab_phrase"] = entry.get("phrase")
    return estimate
