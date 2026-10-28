"""Server-local ISO helpers still used by the Apple Health workout normaliser.

Moved unchanged from the deleted tests/test_meal_intake_api.py (PER-1582).
"""
from __future__ import annotations

from datetime import datetime as _dt


def test_local_iso_from_iso_strips_offset_for_downstream_hour_reads(isolated_app):
    module = isolated_app
    result = module._local_iso_from_iso("2026-05-18T22:00:00")
    assert result is not None
    parsed = _dt.fromisoformat(result)
    assert parsed.tzinfo is None, "stored ISO must be naive (no offset suffix)"
    assert parsed.hour == 22

    result_utc = module._local_iso_from_iso("2026-05-19T03:00:00+00:00")
    assert result_utc is not None
    parsed_utc = _dt.fromisoformat(result_utc)
    assert parsed_utc.tzinfo is None
    assert 0 <= parsed_utc.hour <= 23


def test_local_iso_from_iso_returns_none_for_invalid(isolated_app):
    module = isolated_app
    assert module._local_iso_from_iso(None) is None
    assert module._local_iso_from_iso("") is None
    assert module._local_iso_from_iso("not a date") is None


def test_local_date_from_iso_returns_none_for_empty_or_invalid(isolated_app):
    module = isolated_app
    assert module._local_date_from_iso(None) is None
    assert module._local_date_from_iso("") is None
    assert module._local_date_from_iso("   ") is None
    assert module._local_date_from_iso("not a date") is None
    assert module._local_date_from_iso("2026-99-99T00:00:00Z") is None


def test_local_date_from_iso_handles_naive_input(isolated_app):
    module = isolated_app
    assert module._local_date_from_iso("2026-05-18T23:55:30") == "2026-05-18"
    assert module._local_date_from_iso("2026-05-19T02:30:00") == "2026-05-19"


def test_local_date_from_iso_parses_explicit_offset(isolated_app):
    module = isolated_app
    result = module._local_date_from_iso("2026-05-18T23:00:00+00:00")
    assert result is not None
    assert len(result) == 10


def test_local_date_from_iso_parses_utc_z_suffix(isolated_app):
    result = isolated_app._local_date_from_iso("2026-05-19T03:00:00.000Z")
    assert result is not None
    assert len(result) == 10 and result[4] == "-" and result[7] == "-"
