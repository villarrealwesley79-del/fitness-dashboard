from __future__ import annotations

import data_loader


def test_back_extension_imports_as_erectors_lower_body(tmp_path):
    workout_log = tmp_path / "workout_log.md"
    workout_log.write_text(
        "\n".join(
            [
                "| Date | Machine | Set Role | Set Number | Reps | Weight | Notes | Volume | Is Top Set |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                "| 2026-05-30 | Back Extension | Working | 1 | 10 | 90 | DOMS 2 days | 900 | 1 |",
                "| 2026-05-30 | Low Back | Working | 2 | 10 | 95 |  | 950 | 0 |",
            ]
        )
    )

    workouts, soreness = data_loader.parse_workout_log(str(workout_log))

    assert workouts[0]["session_type"] == "lower"
    assert {ex["muscle_group"] for ex in workouts[0]["exercises"]} == {"erectors"}
    assert soreness == [
        {
            "date": "2026-05-30",
            "muscle": "erectors",
            "soreness_level": 6,
            "notes": "DOMS 2 days",
        }
    ]
