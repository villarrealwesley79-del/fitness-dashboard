#!/usr/bin/env python3
"""Refresh the offline nutrition snapshot from official APIs only.

This script intentionally defaults to dry-run. USDA FoodData Central content is
public domain; Nutritionix-derived data must not be redistributed unless a
human ToS review explicitly clears it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "data" / "nutrition_snapshot.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="write the refreshed snapshot")
    args = parser.parse_args()

    snapshot = json.loads(SNAPSHOT_PATH.read_text())
    if not args.write:
        print(
            json.dumps(
                {
                    "status": "dry_run",
                    "items": len(snapshot.get("items", [])),
                    "writes": False,
                    "note": "USDA-only starter snapshot loaded; live refresh requires a separate curation pass.",
                },
                sort_keys=True,
            )
        )
        return 0

    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "written", "items": len(snapshot.get("items", [])), "writes": True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
