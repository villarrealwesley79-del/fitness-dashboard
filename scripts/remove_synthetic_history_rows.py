#!/usr/bin/env python3
"""Inspect or remove known synthetic Apr 12-14 history rows from runtime JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


HISTORY_FILES = {
    "workout": "data_workouts.json",
    "cardio": "data_cardio.json",
    "recovery": "data_recovery.json",
}
SUSPECT_DATES = {"2026-04-12", "2026-04-13", "2026-04-14"}


def _load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON list")
    if not all(isinstance(row, dict) for row in data):
        raise ValueError(f"{path} must contain only JSON objects")
    return data


def _row_key(kind: str, row: dict, index: int) -> str:
    return str(row.get("id") or row.get("client_id") or f"{kind}:{index}")


def _summarize(kind: str, row: dict, index: int) -> str:
    label = row.get("session_type") or row.get("activity_type") or row.get("recovery_type") or kind
    volume = row.get("total_volume")
    bits = [f"{kind}:{index}", f"id={_row_key(kind, row, index)}", f"date={row.get('date')}", f"type={label}"]
    if volume is not None:
        bits.append(f"volume={volume}")
    return " ".join(bits)


def _parse_remove(values: list[str]) -> set[tuple[str, str]]:
    pairs = set()
    for value in values:
        if ":" not in value:
            raise ValueError(f"remove key must be KIND:ID, got {value!r}")
        kind, row_id = value.split(":", 1)
        if kind not in HISTORY_FILES:
            raise ValueError(f"unknown history kind {kind!r}")
        if not row_id:
            raise ValueError(f"remove key must include an id, got {value!r}")
        pairs.add((kind, row_id))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=".", help="Directory containing data_workouts/cardio/recovery JSON files")
    parser.add_argument("--remove", action="append", default=[], help="Explicit row key to remove, formatted KIND:ID")
    parser.add_argument("--apply", action="store_true", help="Write changes. Without this flag the script is dry-run only.")
    parser.add_argument("--expect-count", type=int, help="Fail unless this many rows would be removed")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    remove_keys = _parse_remove(args.remove)
    candidates: list[tuple[str, str, str]] = []
    rewrites: dict[Path, list[dict]] = {}

    for kind, filename in HISTORY_FILES.items():
        path = data_dir / filename
        rows = _load_rows(path)
        kept = []
        changed = False
        for index, row in enumerate(rows):
            key = _row_key(kind, row, index)
            row_date = str(row.get("date") or "")[:10]
            if row_date in SUSPECT_DATES:
                candidates.append((kind, key, _summarize(kind, row, index)))
            if (kind, key) in remove_keys:
                changed = True
                continue
            kept.append(row)
        if changed:
            rewrites[path] = kept

    if candidates:
        print("Apr 12-14 candidate rows:")
        for _kind, _key, summary in candidates:
            print(f"- {summary}")
    else:
        print("No Apr 12-14 candidate rows found in runtime history JSON files.")

    remove_count = sum(len(_load_rows(path)) - len(rows) for path, rows in rewrites.items())
    if args.expect_count is not None and remove_count != args.expect_count:
        raise SystemExit(f"expected to remove {args.expect_count} rows, would remove {remove_count}")

    if not remove_keys:
        print("No explicit --remove keys supplied; nothing will be deleted.")
        return 0
    if not args.apply:
        print(f"Dry run: would remove {remove_count} explicit row(s). Re-run with --apply to write changes.")
        return 0

    for path, rows in rewrites.items():
        path.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")
    print(f"Removed {remove_count} explicit row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
