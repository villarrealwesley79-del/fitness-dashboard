#!/usr/bin/env python3
"""Meal model benchmark harness for FIT-58.

This script keeps the text/vision benchmark set and local model probes in
source control so estimator choices are repeatable. It does not require image
fixtures to be committed: photo cases are represented by scenario metadata and
can be paired with local private photos at run time.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import mimetypes
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


# Keep the benchmark tied to the production contract.  This narrow bootstrap
# also makes ``python support/meal_model_benchmark.py`` work from any cwd.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from lm_studio_adapter import (  # noqa: E402
    ADJUST_SCHEMA,
    ANALYZE_SCHEMA,
    LM_STUDIO_ANALYZE_TIMEOUT_SEC,
    LM_STUDIO_SWAP_RESOLVE_TIMEOUT_SEC,
    LM_STUDIO_TIMEOUT_SEC,
    SWAP_RESOLVE_SCHEMA,
    _ADJUST_SYSTEM,
    _ANALYZE_SYSTEM,
    _SWAP_RESOLVE_SYSTEM,
)

# Underscored aliases mirror the production names used in the issue contract;
# both aliases point at the exact imported schema objects.
_ADJUST_SCHEMA = ADJUST_SCHEMA
_SWAP_RESOLVE_SCHEMA = SWAP_RESOLVE_SCHEMA
_ANALYZE_SCHEMA = ANALYZE_SCHEMA


LM_STUDIO_URL = "http://127.0.0.1:1234"
CALORIE_MAX = 5000
MACRO_GRAM_MAX = 500
SODIUM_MG_MAX = 12000
MEAL_TEXT_LATENCY_PASS_MS = 5000
FOOD_PHOTO_LATENCY_PASS_MS = 15000
WORKOUT_ANALYSIS_LATENCY_PASS_MS = 30000
DAILY_BRIEF_LATENCY_PASS_MS = 30000
BRANDED_FOOD_LATENCY_PASS_MS = 5000
TEXT_LATENCY_PASS_MS = MEAL_TEXT_LATENCY_PASS_MS
VISION_LATENCY_PASS_MS = FOOD_PHOTO_LATENCY_PASS_MS
TASK_LATENCY_PASS_MS = {
    "food_photo_nutrition": FOOD_PHOTO_LATENCY_PASS_MS,
    "meal_text_nutrition": MEAL_TEXT_LATENCY_PASS_MS,
    "workout_analysis_adjustment": WORKOUT_ANALYSIS_LATENCY_PASS_MS,
    "daily_coaching_brief": DAILY_BRIEF_LATENCY_PASS_MS,
    "branded_food_resolution": BRANDED_FOOD_LATENCY_PASS_MS,
}
STRUCTURED_TASK_LATENCY_PASS_MS = {
    "adjust_intent": int(LM_STUDIO_TIMEOUT_SEC * 1000),
    "swap_resolution": int(LM_STUDIO_SWAP_RESOLVE_TIMEOUT_SEC * 1000),
    "post_workout_analysis": int(LM_STUDIO_ANALYZE_TIMEOUT_SEC * 1000),
}
ALL_TASK_LATENCY_PASS_MS = {**TASK_LATENCY_PASS_MS, **STRUCTURED_TASK_LATENCY_PASS_MS}
STRUCTURED_TASK_TIMEOUT_SEC = {
    "adjust_intent": LM_STUDIO_TIMEOUT_SEC,
    "swap_resolution": LM_STUDIO_SWAP_RESOLVE_TIMEOUT_SEC,
    "post_workout_analysis": LM_STUDIO_ANALYZE_TIMEOUT_SEC,
}
MEAL_ESTIMATE_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "item_name": {"type": "string"},
        "portion_description": {"type": ["string", "null"]},
        "meal_type": {"type": "string", "enum": ["breakfast", "lunch", "dinner", "snack"]},
        "calories": {"type": "number"},
        "protein_g": {"type": "number"},
        "carbs_g": {"type": "number"},
        "fat_g": {"type": "number"},
        "sodium_mg": {"type": "number"},
        "fiber_g": {"type": "number"},
        "confidence": {"type": "number"},
        "ambiguous": {"type": "boolean"},
        "uncertainty_notes": {"type": "array", "items": {"type": "string"}},
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
}
WORKOUT_ADJUSTMENT_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "readiness": {"type": "string", "enum": ["low", "moderate", "high"]},
        "adjustments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "target": {"type": "string"},
                    "action": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["target", "action", "rationale"],
            },
        },
        "risk_flags": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": ["summary", "readiness", "adjustments", "risk_flags", "confidence"],
}
DAILY_COACHING_BRIEF_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "priorities": {"type": "array", "items": {"type": "string"}},
        "training_focus": {"type": "string"},
        "nutrition_focus": {"type": "string"},
        "recovery_focus": {"type": "string"},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": [
        "summary",
        "priorities",
        "training_focus",
        "nutrition_focus",
        "recovery_focus",
        "warnings",
        "confidence",
    ],
}
BRANDED_FOOD_RESOLUTION_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "query": {"type": "string"},
        "resolved_name": {"type": "string"},
        "brand": {"type": ["string", "null"]},
        "serving_description": {"type": ["string", "null"]},
        "calories": {"type": "number"},
        "protein_g": {"type": "number"},
        "carbs_g": {"type": "number"},
        "fat_g": {"type": "number"},
        "sodium_mg": {"type": "number"},
        "confidence": {"type": "number"},
        "source": {"type": "string"},
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "query",
        "resolved_name",
        "brand",
        "serving_description",
        "calories",
        "protein_g",
        "carbs_g",
        "fat_g",
        "sodium_mg",
        "confidence",
        "source",
        "notes",
    ],
}
TASK_SCHEMAS = {
    "food_photo_nutrition": MEAL_ESTIMATE_RESPONSE_SCHEMA,
    "meal_text_nutrition": MEAL_ESTIMATE_RESPONSE_SCHEMA,
    "workout_analysis_adjustment": WORKOUT_ADJUSTMENT_RESPONSE_SCHEMA,
    "daily_coaching_brief": DAILY_COACHING_BRIEF_RESPONSE_SCHEMA,
    "branded_food_resolution": BRANDED_FOOD_RESOLUTION_RESPONSE_SCHEMA,
    # These objects are imported from lm_studio_adapter.py by identity.  Do
    # not copy or re-state the production prompt/schema here.
    "adjust_intent": ADJUST_SCHEMA,
    "swap_resolution": SWAP_RESOLVE_SCHEMA,
    "post_workout_analysis": ANALYZE_SCHEMA,
}
TASK_SCHEMA_NAMES = {
    "food_photo_nutrition": "meal_estimate",
    "meal_text_nutrition": "meal_estimate",
    "workout_analysis_adjustment": "workout_adjustment",
    "daily_coaching_brief": "daily_coaching_brief",
    "branded_food_resolution": "branded_food_resolution",
    "adjust_intent": "adjust_plan_intent",
    "swap_resolution": "swap_resolution",
    "post_workout_analysis": "analyze_workout",
}
TASK_NUMBER_BOUNDS = {
    "workout_analysis_adjustment": {
        "confidence": (0, 1),
    },
    "daily_coaching_brief": {
        "confidence": (0, 1),
    },
    "branded_food_resolution": {
        "calories": (0, CALORIE_MAX),
        "protein_g": (0, MACRO_GRAM_MAX),
        "carbs_g": (0, MACRO_GRAM_MAX),
        "fat_g": (0, MACRO_GRAM_MAX),
        "sodium_mg": (0, SODIUM_MG_MAX),
        "confidence": (0, 1),
    },
}
EXPECTED_WORKOUT_READINESS = {
    "workout-001": "low",
    "workout-002": "high",
    "workout-003": "moderate",
}
EXPECTED_BRANDED_NUTRITION_BANDS = {
    "brand-001": {"calories": (450, 750), "protein_g": (20, 45), "carbs_g": (30, 60), "fat_g": (20, 45), "sodium_mg": (700, 1600)},
    "brand-002": {"calories": (80, 220), "protein_g": (8, 25), "carbs_g": (8, 35), "fat_g": (0, 8), "sodium_mg": (20, 180)},
    "brand-003": {"calories": (150, 280), "protein_g": (15, 30), "carbs_g": (12, 35), "fat_g": (4, 16), "sodium_mg": (80, 400)},
    "brand-004": {"calories": (500, 1200), "protein_g": (25, 70), "carbs_g": (45, 150), "fat_g": (10, 55), "sodium_mg": (600, 2600)},
}
PUBLIC_ESTIMATE_FIELDS = tuple(MEAL_ESTIMATE_RESPONSE_SCHEMA["properties"]) + ("source",)
EXPECTED_CALORIE_BANDS = {
    "txt-001": (120, 450),
    "txt-002": (250, 650),
    "txt-003": (450, 1100),
    "txt-004": (250, 1200),
    "txt-005": (80, 140),
    "txt-006": (120, 400),
    "txt-007": (350, 850),
    "txt-008": (180, 700),
    "txt-009": (300, 750),
    "txt-010": (250, 650),
    "txt-011": (0, 180),
    "txt-012": (250, 750),
    "txt-013": (600, 1400),
    "txt-014": (450, 700),
    "txt-015": (150, 350),
    "txt-016": (250, 700),
    "txt-017": (350, 850),
    "txt-018": (200, 500),
    "txt-019": (350, 850),
    "txt-020": (150, 900),
    "photo-001": (350, 850),
    "photo-002": (400, 1000),
    "photo-003": (250, 650),
    "photo-004": (350, 900),
    "photo-005": (180, 700),
    "photo-006": (300, 800),
    "photo-007": (450, 1000),
    "photo-008": (250, 800),
    "photo-009": (250, 700),
    "photo-010": (120, 450),
    "photo-011": (200, 650),
    "photo-012": (300, 750),
    "photo-013": (450, 1100),
    "photo-014": (350, 900),
    "photo-015": (250, 800),
    "photo-016": (300, 850),
    "photo-017": (150, 700),
    "photo-018": (250, 800),
    "photo-019": (450, 1200),
    "photo-020": (200, 900),
    "pkg-001": (150, 350),
    "pkg-002": (120, 700),
    "pkg-003": (80, 300),
    "pkg-004": (250, 700),
    "pkg-005": (100, 450),
    "pkg-006": (0, 250),
    "pkg-007": (80, 200),
    "pkg-008": (150, 700),
    "pkg-009": (100, 500),
    "pkg-010": (250, 1000),
    "amb-001": (250, 900),
    "amb-002": (300, 1400),
    "amb-003": (150, 900),
    "amb-004": (80, 450),
    "amb-005": (100, 700),
    "amb-006": (250, 1200),
    "amb-007": (250, 1200),
    "amb-008": (300, 1300),
    "amb-009": (0, 500),
    "amb-010": (150, 900),
}
EXPECTED_NUTRITION_BANDS = {
    "txt-001": {"protein_g": (15, 45), "carbs_g": (0, 45), "fat_g": (0, 18), "sodium_mg": (0, 500)},
    "txt-002": {"protein_g": (10, 35), "carbs_g": (15, 70), "fat_g": (8, 35), "sodium_mg": (100, 900)},
    "txt-003": {"protein_g": (25, 65), "carbs_g": (45, 120), "fat_g": (10, 45), "sodium_mg": (600, 2200)},
    "txt-004": {"protein_g": (0, 12), "carbs_g": (20, 120), "fat_g": (8, 70), "sodium_mg": (100, 1600)},
    "txt-005": {"protein_g": (0, 4), "carbs_g": (20, 35), "fat_g": (0, 2), "sodium_mg": (0, 20)},
    "txt-006": {"protein_g": (8, 35), "carbs_g": (10, 45), "fat_g": (0, 15), "sodium_mg": (30, 250)},
    "txt-007": {"protein_g": (20, 60), "carbs_g": (30, 90), "fat_g": (2, 30), "sodium_mg": (50, 1400)},
    "txt-008": {"protein_g": (5, 30), "carbs_g": (20, 90), "fat_g": (2, 35), "sodium_mg": (100, 1400)},
    "txt-009": {"protein_g": (12, 45), "carbs_g": (25, 80), "fat_g": (5, 35), "sodium_mg": (400, 1600)},
    "txt-010": {"protein_g": (5, 25), "carbs_g": (25, 75), "fat_g": (5, 25), "sodium_mg": (0, 500)},
    "txt-011": {"protein_g": (0, 5), "carbs_g": (0, 15), "fat_g": (0, 12), "sodium_mg": (0, 80)},
    "txt-012": {"protein_g": (18, 55), "carbs_g": (5, 45), "fat_g": (5, 35), "sodium_mg": (150, 1200)},
    "txt-013": {"protein_g": (18, 50), "carbs_g": (45, 120), "fat_g": (20, 70), "sodium_mg": (600, 1800)},
    "txt-014": {"protein_g": (20, 40), "carbs_g": (30, 55), "fat_g": (20, 40), "sodium_mg": (500, 1200)},
    "txt-015": {"protein_g": (8, 35), "carbs_g": (10, 40), "fat_g": (2, 18), "sodium_mg": (50, 400)},
    "txt-016": {"protein_g": (5, 30), "carbs_g": (25, 85), "fat_g": (0, 25), "sodium_mg": (200, 1200)},
    "txt-017": {"protein_g": (15, 45), "carbs_g": (20, 70), "fat_g": (8, 35), "sodium_mg": (300, 1400)},
    "txt-018": {"protein_g": (6, 20), "carbs_g": (20, 55), "fat_g": (6, 20), "sodium_mg": (300, 1000)},
    "txt-019": {"protein_g": (5, 30), "carbs_g": (35, 100), "fat_g": (2, 30), "sodium_mg": (20, 400)},
    "txt-020": {"protein_g": (0, 20), "carbs_g": (5, 80), "fat_g": (2, 45), "sodium_mg": (50, 1200)},
}
GENERIC_NUTRITION_BANDS_BY_INPUT = {
    "photo": {"protein_g": (0, 90), "carbs_g": (0, 180), "fat_g": (0, 100), "sodium_mg": (0, 3000)},
    "packaged_photo": {"protein_g": (0, 80), "carbs_g": (0, 160), "fat_g": (0, 90), "sodium_mg": (0, 2500)},
}
_ITEM_HINT_STOPWORDS = {"and", "with", "meal", "plate", "food", "some"}
RAW_TRACE_BLOCKED_FRAGMENTS = (
    "meal input:",
    "task input:",
    "attached image",
    "image_url",
    "data:image",
    "```",
    "/tmp/",
    ".jpg",
    ".jpeg",
    ".png",
    ".heic",
    "\"calories\"",
)
IMAGE_PAYLOAD_INSTRUCTIONS_BY_TASK = {
    "food_photo_nutrition": "Estimate the meal from the attached image only.",
}


@dataclass(frozen=True)
class MealCase:
    case_id: str
    input_type: str
    prompt: str
    expected_item_hint: str
    ambiguity: str
    notes: str = ""
    task_class: str = "meal_text_nutrition"
    # Structured workout cases use these fields; legacy nutrition cases leave
    # them empty so their scoring and serialized shape remain unchanged.
    request: dict | None = None
    expected: dict | None = None
    coverage: tuple[str, ...] = ()

    @property
    def task(self) -> str:
        return self.task_class

    @property
    def coverage_tags(self) -> tuple[str, ...]:
        return self.coverage


TEXT_CASES = [
    MealCase("txt-001", "text", "protein shake", "protein shake", "low"),
    MealCase("txt-002", "text", "2 eggs and toast", "eggs and toast", "low"),
    MealCase("txt-003", "text", "chipotle bowl chicken white rice", "chipotle bowl", "medium"),
    MealCase("txt-004", "text", "movie theater popcorn", "popcorn", "high"),
    MealCase("txt-005", "text", "banana", "banana", "low"),
    MealCase("txt-006", "text", "greek yogurt with berries", "yogurt", "low"),
    MealCase("txt-007", "text", "chicken and rice", "chicken and rice", "low"),
    MealCase("txt-008", "text", "half pasta leftovers", "pasta", "high"),
    MealCase("txt-009", "text", "turkey sandwich", "sandwich", "medium"),
    MealCase("txt-010", "text", "oatmeal with peanut butter", "oatmeal", "medium"),
    MealCase("txt-011", "text", "coffee with cream", "coffee", "medium"),
    MealCase("txt-012", "text", "salad with grilled chicken", "salad", "medium"),
    MealCase("txt-013", "text", "burger and fries", "burger and fries", "medium"),
    MealCase("txt-014", "text", "mcdonalds quarter pounder", "quarter pounder", "medium"),
    MealCase("txt-015", "text", "protein bar", "protein bar", "low"),
    MealCase("txt-016", "text", "sushi roll", "sushi", "medium"),
    MealCase("txt-017", "text", "steak tacos", "tacos", "medium"),
    MealCase("txt-018", "text", "pizza slice", "pizza", "medium"),
    MealCase("txt-019", "text", "smoothie bowl", "smoothie bowl", "medium"),
    MealCase("txt-020", "text", "some snacks", "snacks", "high"),
]

PHOTO_CASES = [
    MealCase(f"photo-{i:03d}", "photo", prompt, hint, ambiguity, task_class="food_photo_nutrition")
    for i, (prompt, hint, ambiguity) in enumerate(
        [
            ("plate photo: chicken rice vegetables", "chicken rice", "medium"),
            ("plate photo: burger with wrapper", "burger", "medium"),
            ("plate photo: breakfast eggs toast", "eggs toast", "medium"),
            ("plate photo: pasta bowl", "pasta", "medium"),
            ("plate photo: salad bowl", "salad", "medium"),
            ("plate photo: tacos", "tacos", "medium"),
            ("plate photo: steak potatoes", "steak potatoes", "medium"),
            ("plate photo: sushi", "sushi", "medium"),
            ("plate photo: pizza", "pizza", "medium"),
            ("plate photo: protein shake", "shake", "low"),
            ("plate photo: oatmeal", "oatmeal", "medium"),
            ("plate photo: sandwich", "sandwich", "medium"),
            ("plate photo: burrito bowl", "burrito bowl", "medium"),
            ("plate photo: pancakes", "pancakes", "medium"),
            ("plate photo: grilled fish", "fish", "medium"),
            ("plate photo: rice and beans", "rice beans", "medium"),
            ("plate photo: soup", "soup", "high"),
            ("plate photo: fries", "fries", "medium"),
            ("plate photo: chicken wings", "wings", "medium"),
            ("plate photo: mixed leftovers", "leftovers", "high"),
        ],
        start=1,
    )
]

PACKAGED_CASES = [
    MealCase(f"pkg-{i:03d}", "packaged_photo", prompt, hint, ambiguity, task_class="food_photo_nutrition")
    for i, (prompt, hint, ambiguity) in enumerate(
        [
            ("packaged food photo: protein bar front", "protein bar", "low"),
            ("packaged food photo: chips bag", "chips", "medium"),
            ("packaged food photo: yogurt cup", "yogurt", "low"),
            ("packaged food photo: frozen meal box", "frozen meal", "medium"),
            ("packaged food photo: cereal box", "cereal", "medium"),
            ("packaged food photo: sports drink", "sports drink", "low"),
            ("packaged food photo: milk carton", "milk", "low"),
            ("packaged food photo: trail mix bag", "trail mix", "medium"),
            ("packaged food photo: canned soup", "soup", "medium"),
            ("packaged food photo: fast food wrapper", "fast food", "high"),
        ],
        start=1,
    )
]

AMBIGUOUS_CASES = [
    MealCase("amb-001", "photo", "photo: shared half burger and some fries", "burger fries", "high", task_class="food_photo_nutrition"),
    MealCase("amb-002", "photo", "photo: buffet plate", "buffet", "high", task_class="food_photo_nutrition"),
    MealCase("amb-003", "photo", "photo: potluck snacks", "snacks", "high", task_class="food_photo_nutrition"),
    MealCase("amb-004", "photo", "photo: a few bites of pasta", "pasta", "high", task_class="food_photo_nutrition"),
    MealCase("amb-005", "photo", "photo: partially eaten plate", "partial plate", "high", task_class="food_photo_nutrition"),
    MealCase("amb-006", "photo", "photo: family style shared meal", "shared meal", "high", task_class="food_photo_nutrition"),
    MealCase("amb-007", "photo", "photo: dark restaurant plate", "restaurant meal", "high", task_class="food_photo_nutrition"),
    MealCase("amb-008", "photo", "photo: sauce-heavy restaurant meal", "restaurant meal", "high", task_class="food_photo_nutrition"),
    MealCase("amb-009", "photo", "photo: drink with no label", "drink", "high", task_class="food_photo_nutrition"),
    MealCase("amb-010", "photo", "photo: leftovers from last night", "leftovers", "high", task_class="food_photo_nutrition"),
]

DAILY_BRIEF_CASES = [
    MealCase(
        "brief-001",
        "daily_brief",
        "Today: strength day, protein behind by 45g, sleep 6h, soreness moderate, steps low by 3000.",
        "protein strength recovery",
        "medium",
        task_class="daily_coaching_brief",
    ),
    MealCase(
        "brief-002",
        "daily_brief",
        "Today: rest day, calories high yesterday, hydration low, HRV normal, long walk planned.",
        "rest hydration walk",
        "medium",
        task_class="daily_coaching_brief",
    ),
    MealCase(
        "brief-003",
        "daily_brief",
        "Today: interval session, carbs low at breakfast, sleep good, calf tightness mild.",
        "carbs interval calf",
        "medium",
        task_class="daily_coaching_brief",
    ),
]

BRANDED_FOOD_CASES = [
    MealCase(
        "brand-001",
        "branded_food",
        "Resolve branded food: McDonald's Quarter Pounder with Cheese, one sandwich.",
        "quarter pounder mcdonald",
        "low",
        task_class="branded_food_resolution",
    ),
    MealCase(
        "brand-002",
        "branded_food",
        "Resolve branded food: Chobani Greek Yogurt strawberry cup, one single-serve container.",
        "chobani yogurt",
        "low",
        task_class="branded_food_resolution",
    ),
    MealCase(
        "brand-003",
        "branded_food",
        "Resolve branded food: Quest chocolate chip cookie dough protein bar.",
        "quest protein bar",
        "low",
        task_class="branded_food_resolution",
    ),
    MealCase(
        "brand-004",
        "branded_food",
        "Resolve branded food: Chipotle chicken burrito bowl with white rice and black beans.",
        "chipotle chicken bowl",
        "medium",
        task_class="branded_food_resolution",
    ),
]


def _compact_plan(
    *,
    focus: str = "upper body",
    goal_name: str = "strength",
    estimated_minutes: int = 45,
    exercises: list[dict] | None = None,
    cardio: str | None = "easy bike",
) -> dict:
    """Return the already-compact plan shape sent by ``adjust_plan``."""
    if exercises is None:
        exercises = [
            {
                "exercise": "Bench Press",
                "muscle": "chest",
                "is_compound": True,
                "target_sets": 4,
                "target_reps": 6,
                "rpe_target": 8,
            },
            {
                "exercise": "Lat Pulldown",
                "muscle": "back",
                "is_compound": False,
                "target_sets": 3,
                "target_reps": 10,
                "rpe_target": 7,
            },
        ]
    return {
        "focus": focus,
        "goal_name": goal_name,
        "estimated_minutes": estimated_minutes,
        "exercises": exercises,
        "cardio": cardio,
    }


def _adjust_case(
    number: int,
    constraint: str,
    expected: dict,
    *,
    readiness: dict | None = None,
    plan: dict | None = None,
    coverage: tuple[str, ...] = (),
) -> MealCase:
    return MealCase(
        f"adjust-{number:03d}",
        "structured",
        constraint,
        expected.get("summary_hint", "workout"),
        "high" if not any(expected.get(key) for key in ("avoid_muscles", "avoid_joints", "swap", "drop_cardio")) else "medium",
        task_class="adjust_intent",
        request={
            "athlete_constraint": constraint,
            "current_plan": plan or _compact_plan(),
            "readiness": readiness or {},
        },
        expected=expected,
        coverage=coverage,
    )


_ADJUST_DEFAULT_EXPECTED = {
    "avoid_muscles": [],
    "avoid_joints": [],
    "swap": [],
    "rpe_delta": 0,
    "sets_delta_pct": 0,
    "duration_cap_min": 0,
    "drop_cardio": False,
}


ADJUST_INTENT_CASES = [
    _adjust_case(
        1,
        "My legs are very sore after yesterday's intervals; reduce lower-body work and skip cardio.",
        {**_ADJUST_DEFAULT_EXPECTED, "sets_delta_pct": -20, "drop_cardio": True},
        readiness={"sleep_hours": 5, "readiness_score": 32},
        coverage=("readiness", "set_reduction", "drop_cardio"),
    ),
]


def _swap_candidates() -> list[dict]:
    return [
        {"name": "Chest Press Machine", "equipment": "machine", "aliases": ["machine chest press"], "compound": False},
        {"name": "Incline Dumbbell Press", "equipment": "dumbbell", "aliases": ["incline db press"], "compound": True},
        {"name": "Cable Row", "equipment": "cable", "aliases": ["seated cable row"], "compound": True},
        {"name": "Leg Press", "equipment": "machine", "aliases": ["45 degree leg press"], "compound": True},
    ]


def _swap_case(number: int, typed_name: str, expected_name: str | None, *, current: str = "Bench Press", muscle: str = "chest", candidates: list[dict] | None = None, coverage: tuple[str, ...] = ()) -> MealCase:
    candidates = candidates or _swap_candidates()
    names = [candidate["name"] for candidate in candidates]
    expected = {"canonical_name": expected_name}
    return MealCase(
        f"swap-{number:03d}",
        "structured",
        typed_name,
        expected_name or "null",
        "low" if expected_name else "high",
        task_class="swap_resolution",
        request={
            "typed_name": typed_name,
            "current_exercise": current,
            "target_muscle": muscle,
            "candidate_names": names,
            "candidates": [
                {
                    "name": candidate.get("name"),
                    "equipment": candidate.get("equipment"),
                    "aliases": candidate.get("aliases") or [],
                    "compound": bool(candidate.get("compound")),
                }
                for candidate in candidates
            ],
        },
        expected=expected,
        coverage=coverage,
    )


SWAP_RESOLUTION_CASES = [
    _swap_case(
        1,
        "bike",
        "Stationary Bike",
        current="Tempo Run",
        muscle="cardio",
        candidates=[
            {
                "name": "Stationary Bike",
                "equipment": "bike",
                "aliases": ["bike", "exercise bike"],
                "compound": False,
            }
        ],
        coverage=("exact", "single"),
    ),
]


ANALYSIS_NOTABLE_TERMS = (
    "pain", "hurt", "hurting", "ache", "discomfort", "tight", "tightness",
    "left", "right", "side", "asymmetry", "imbalance", "worse",
)


def _compact_workout(*, name: str = "Bench Press", muscle: str = "chest", notes: str = "", rpe: float = 7, cardio: object = None, date: str = "2026-07-15", session_type: str = "upper", duration_minutes: int = 45, total_sets: int = 3, total_volume_lbs: int = 300) -> dict:
    return {
        "date": date,
        "session_type": session_type,
        "duration_minutes": duration_minutes,
        "total_sets": total_sets,
        "total_volume_lbs": total_volume_lbs,
        "exercises": [{
            "name": name,
            "muscle": muscle,
            "sets": [{"reps": 5, "weight_lbs": 100, "rpe": rpe, "notes": notes}],
        }],
        "cardio": cardio,
        "notes": "",
        "notable_notes": (
            [{"exercise": name, "set_number": 1, "note": notes}]
            if notes and any(term in notes.lower() for term in ANALYSIS_NOTABLE_TERMS)
            else []
        ),
    }


def _analysis_case(number: int, workout: dict, context: dict, expected: dict, coverage: tuple[str, ...]) -> MealCase:
    return MealCase(
        f"analysis-{number:03d}",
        "structured",
        json.dumps({"workout": workout, "context": context}, sort_keys=True),
        "post-workout analysis",
        "medium",
        task_class="post_workout_analysis",
        request={"workout": workout, "context": context},
        expected=expected,
        coverage=coverage,
    )


POST_WORKOUT_ANALYSIS_CASES = [
    _analysis_case(1, _compact_workout(name="Bench Press", notes="clean reps", rpe=7, total_volume_lbs=1500), {"recent_sessions": [{"bench": "steady"}]}, {"summary": (("session",), ("bench", "press")), "wins": (("bench", "press"), ("clean",)), "concerns": (), "comparison": (("steady", "same"),), "next_session_cue": (("form", "progress"),), "empty_fields": ("concerns",)}, ("pr", "win")),
]

# Short aliases keep callers from needing to know the storage naming detail.
ADJUST_CASES = ADJUST_INTENT_CASES
SWAP_CASES = SWAP_RESOLUTION_CASES
ANALYSIS_CASES = POST_WORKOUT_ANALYSIS_CASES
POST_WORKOUT_CASES = POST_WORKOUT_ANALYSIS_CASES


def nutrition_cases() -> list[MealCase]:
    return TEXT_CASES + PHOTO_CASES + PACKAGED_CASES + AMBIGUOUS_CASES


def adjust_intent_cases() -> list[MealCase]:
    return ADJUST_INTENT_CASES


def swap_resolution_cases() -> list[MealCase]:
    return SWAP_RESOLUTION_CASES


def post_workout_analysis_cases() -> list[MealCase]:
    return POST_WORKOUT_ANALYSIS_CASES


def structured_cases() -> list[MealCase]:
    return ADJUST_INTENT_CASES + SWAP_RESOLUTION_CASES + POST_WORKOUT_ANALYSIS_CASES


WORKOUT_CASES = structured_cases()


def all_cases() -> list[MealCase]:
    return nutrition_cases() + WORKOUT_CASES + DAILY_BRIEF_CASES + BRANDED_FOOD_CASES


def probe_lm_studio(url: str = LM_STUDIO_URL, timeout: float = 2.0) -> dict:
    started = time.time()
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/v1/models", timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return {
            "reachable": True,
            "latency_ms": int((time.time() - started) * 1000),
            "models": [m.get("id") for m in payload.get("data", []) if isinstance(m, dict)],
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"reachable": False, "latency_ms": int((time.time() - started) * 1000), "error": str(exc), "models": []}


def mac_hardware_summary() -> dict:
    base = {
        "platform": platform.platform(),
        "machine": platform.machine(),
    }
    try:
        proc = subprocess.run(
            ["system_profiler", "SPHardwareDataType"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        base["system_profiler"] = "\n".join(
            line.strip()
            for line in proc.stdout.splitlines()
            if any(key in line for key in ("Model Name", "Chip", "Total Number of Cores", "Memory"))
        )
    except Exception as exc:
        base["system_profiler_error"] = str(exc)
    return base


def routing_recommendation(loaded_models: list[str]) -> dict:
    loaded = {m.lower() for m in loaded_models}
    text_model = "qwen/qwen3.6-35b-a3b" if "qwen/qwen3.6-35b-a3b" in loaded else "current LM Studio text model"
    vision_model = "Qwen2.5-VL 7B on ASUS GX10 first; Qwen2.5-VL 32B only if latency and memory fit"
    if any("qwen2.5-vl" in m or "qwen-vl" in m for m in loaded):
        vision_model = "loaded Qwen-VL-family model"
    return {
        "candidate_text_model": text_model,
        "candidate_vision_model": vision_model,
        "production_text_route": "deterministic parser fallback with pending_review until a benchmark passes",
        "production_vision_route": "manual pending-review fallback until a local VLM benchmark passes",
        "fallback": "deterministic parser fallback with pending_review; cloud disabled by default",
        "auto_log_threshold": "confidence >= 0.75, unambiguous, plausible macros",
        "pending_review_threshold": "confidence < 0.75 or ambiguous/impossible nutrition",
    }


def image_capable_cases() -> list[MealCase]:
    return [case for case in all_cases() if case.input_type in {"photo", "packaged_photo"}]


def task_class_counts(cases: list[MealCase] | None = None) -> dict[str, int]:
    counts = {task_class: 0 for task_class in TASK_LATENCY_PASS_MS}
    selected_cases = all_cases() if cases is None else cases
    for case in selected_cases:
        counts[case.task_class] = counts.get(case.task_class, 0) + 1
    return counts


def requires_image(case: MealCase) -> bool:
    return case.input_type in {"photo", "packaged_photo"}


def _schema_for_case(case: MealCase) -> dict:
    return TASK_SCHEMAS[case.task_class]


def _schema_name_for_case(case: MealCase) -> str:
    return TASK_SCHEMA_NAMES[case.task_class]


def _task_instruction(case: MealCase) -> str:
    if case.task_class == "adjust_intent":
        return _ADJUST_SYSTEM
    if case.task_class == "swap_resolution":
        return _SWAP_RESOLVE_SYSTEM
    if case.task_class == "post_workout_analysis":
        return _ANALYZE_SYSTEM
    if case.task_class in {"food_photo_nutrition", "meal_text_nutrition"}:
        return (
            "Estimate this meal as JSON only with keys item_name, portion_description, "
            "meal_type, calories, protein_g, carbs_g, fat_g, sodium_mg, fiber_g, confidence, "
            "ambiguous, uncertainty_notes. Use low confidence for unclear portions."
        )
    if case.task_class == "workout_analysis_adjustment":
        return (
            "Analyze the planned workout and readiness signals. Return JSON only with keys "
            "summary, readiness, adjustments, risk_flags, confidence. Use adjustments as an "
            "array of target/action/rationale objects."
        )
    if case.task_class == "daily_coaching_brief":
        return (
            "Create a concise daily coaching brief. Return JSON only with keys summary, "
            "priorities, training_focus, nutrition_focus, recovery_focus, warnings, confidence."
        )
    if case.task_class == "branded_food_resolution":
        return (
            "Resolve the branded food query to one likely nutrition entry. Return JSON only with "
            "keys query, resolved_name, brand, serving_description, calories, protein_g, carbs_g, "
            "fat_g, sodium_mg, confidence, source, notes."
        )
    raise ValueError(f"unsupported task class: {case.task_class}")


def _analysis_notable_notes(workout: dict) -> list[dict]:
    notable_notes = []
    for ex in (workout.get("exercises") or []):
        ex_name = ex.get("machine") or ex.get("exercise") or ex.get("name") or "Exercise"
        for current_set in (ex.get("sets") or []):
            note = (current_set.get("notes") or "").strip()
            if note and any(term in note.lower() for term in ANALYSIS_NOTABLE_TERMS):
                notable_notes.append({
                    "exercise": ex_name,
                    "set_number": current_set.get("set_number"),
                    "note": note,
                })
    return notable_notes


def _production_compact_workout(workout: dict) -> dict:
    """Mirror ``lm_studio_adapter.analyze_workout``'s compact projection."""
    return {
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
                        "reps": current_set.get("reps"),
                        "weight_lbs": current_set.get("weight_lbs"),
                        "rpe": current_set.get("rpe"),
                        "notes": current_set.get("notes") or "",
                    }
                    for current_set in (ex.get("sets") or [])
                ],
            }
            for ex in (workout.get("exercises") or [])
        ],
        "cardio": workout.get("cardio"),
        "notes": workout.get("notes") or "",
        "notable_notes": _analysis_notable_notes(workout),
    }


def _analysis_user_payload(workout: dict, context: dict | None = None) -> dict:
    """Build the exact user object sent by production ``analyze_workout``."""
    # Cases store the compact shape to make the corpus auditable.  Callers may
    # also pass the full application workout shape; detect it by projection.
    compact = workout
    if not (
        set(workout) >= {
            "date", "session_type", "duration_minutes", "total_sets",
            "total_volume_lbs", "exercises", "cardio", "notes", "notable_notes",
        }
    ):
        compact = _production_compact_workout(workout)
    return {"workout": compact, "context": context or {}}


def _structured_chat_payload(case: MealCase, model: str) -> dict:
    task = case.task_class
    if task == "adjust_intent":
        request = case.request or {}
        user_payload = {
            "athlete_constraint": request.get("athlete_constraint", case.prompt),
            "current_plan": request.get("current_plan") or _compact_plan(),
            "readiness": request.get("readiness") or {},
        }
        temperature, max_tokens = 0.1, 600
        system = _ADJUST_SYSTEM
    elif task == "swap_resolution":
        request = case.request or {}
        candidates = request.get("candidates") or []
        user_payload = {
            "typed_name": str(request.get("typed_name", case.prompt) or "").strip(),
            "current_exercise": str(request.get("current_exercise", "") or "").strip(),
            "target_muscle": str(request.get("target_muscle", "") or "").strip(),
            "candidate_names": [
                str(candidate.get("name") or "").strip()
                for candidate in candidates
                if str(candidate.get("name") or "").strip()
            ],
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
        temperature, max_tokens = 0, 220
        system = _SWAP_RESOLVE_SYSTEM
    elif task == "post_workout_analysis":
        request = case.request or {}
        user_payload = _analysis_user_payload(request.get("workout") or {}, request.get("context") or {})
        temperature, max_tokens = 0.2, 700
        system = _ANALYZE_SYSTEM
    else:
        raise ValueError(f"unsupported structured task class: {task}")
    return {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user_payload, default=str)},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": TASK_SCHEMA_NAMES[task],
                "schema": TASK_SCHEMAS[task],
                "strict": True,
            },
        },
    }


# Public-ish aliases used by parity tests and future benchmark tooling.
adjust_intent_payload = _structured_chat_payload
swap_resolution_payload = _structured_chat_payload
post_workout_analysis_payload = _structured_chat_payload


def build_adjust_intent_payload(case: MealCase, model: str = "benchmark-model") -> dict:
    return _structured_chat_payload(case, model)


def build_swap_resolution_payload(case: MealCase, model: str = "benchmark-model") -> dict:
    return _structured_chat_payload(case, model)


def build_post_workout_analysis_payload(case: MealCase, model: str = "benchmark-model") -> dict:
    return _structured_chat_payload(case, model)


def _chat_payload(case: MealCase, model: str, image_path: str | None = None) -> dict:
    if case.task_class in {"adjust_intent", "swap_resolution", "post_workout_analysis"}:
        if image_path:
            raise ValueError(f"image payloads are not supported for {case.task_class}")
        return _structured_chat_payload(case, model)
    instruction = _task_instruction(case)
    content: str | list[dict] = f"{instruction}\n\nTask input: {case.prompt}"
    if image_path:
        if case.task_class not in IMAGE_PAYLOAD_INSTRUCTIONS_BY_TASK:
            raise ValueError(f"image payloads are only supported for food_photo_nutrition: {case.task_class}")
        mime_type = mimetypes.guess_type(image_path)[0] or "image/jpeg"
        with open(image_path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
        content = [
            {"type": "text", "text": instruction},
            {"type": "text", "text": IMAGE_PAYLOAD_INSTRUCTIONS_BY_TASK[case.task_class]},
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}},
        ]
    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": _schema_name_for_case(case),
                "schema": _schema_for_case(case),
                "strict": True,
            },
        },
    }


def _extract_json_object(content: str) -> dict | None:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _hint_tokens(text: str) -> set[str]:
    return {
        token
        for token in "".join(ch if ch.isalnum() else " " for ch in text.lower()).split()
        if len(token) >= 3 and token not in _ITEM_HINT_STOPWORDS
    }


def _calories_within_band(case: MealCase, estimate: dict) -> bool:
    band = EXPECTED_CALORIE_BANDS.get(case.case_id)
    if not band:
        return False
    value = estimate.get("calories")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return band[0] <= float(value) <= band[1]


def _nutrition_fields_within_bands(case: MealCase, estimate: dict) -> bool:
    bands = EXPECTED_NUTRITION_BANDS.get(case.case_id) or GENERIC_NUTRITION_BANDS_BY_INPUT.get(case.input_type)
    if not bands:
        return False
    for key, band in bands.items():
        value = estimate.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if not (band[0] <= float(value) <= band[1]):
            return False
    return True


def _portion_reasonable(estimate: dict) -> bool:
    portion = estimate.get("portion_description")
    if portion is None:
        return True
    return isinstance(portion, str) and 3 <= len(portion.strip()) <= 180


def _item_hint_matches(case: MealCase, estimate: dict) -> bool:
    expected = _hint_tokens(case.expected_item_hint)
    if not expected:
        return False
    observed = _hint_tokens(
        " ".join(
            str(estimate.get(key) or "")
            for key in ("item_name", "portion_description")
        )
    )
    return bool(expected & observed)


def _confidence_calibrated(case: MealCase, estimate: dict) -> bool:
    confidence = estimate.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return False
    confidence = float(confidence)
    ambiguous = bool(estimate.get("ambiguous"))
    if case.ambiguity == "high":
        return ambiguous and confidence <= 0.6
    if case.ambiguity == "low":
        return not ambiguous and confidence >= 0.55
    return 0.35 <= confidence <= 0.9


def _macro_energy_consistent(estimate: dict) -> bool:
    values = {
        key: estimate.get(key)
        for key in ("calories", "protein_g", "carbs_g", "fat_g")
    }
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values.values()):
        return False
    calories = float(values["calories"])
    if calories <= 0:
        return True
    macro_calories = (
        float(values["protein_g"]) * 4
        + float(values["carbs_g"]) * 4
        + float(values["fat_g"]) * 9
    )
    return calories * 0.4 <= macro_calories <= calories * 1.6


def _raw_trace_free(estimate: dict) -> bool:
    snippets: list[str] = []
    for key in ("item_name", "portion_description"):
        value = estimate.get(key)
        if isinstance(value, str):
            snippets.append(value)
    notes = estimate.get("uncertainty_notes")
    if isinstance(notes, list):
        snippets.extend(note for note in notes if isinstance(note, str))
    joined = "\n".join(snippets).lower()
    return not any(fragment in joined for fragment in RAW_TRACE_BLOCKED_FRAGMENTS)


def _string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            values.extend(_string_values(item))
        return values
    if isinstance(value, dict):
        values = []
        for item in value.values():
            values.extend(_string_values(item))
        return values
    return []


def _structured_response_schema_errors(response: dict | None, schema: dict) -> list[str]:
    if not isinstance(response, dict):
        return ["response_not_json_object"]
    required = set(schema.get("required", []))
    properties = schema.get("properties", {})
    errors = [f"missing_{key}" for key in sorted(required - set(response))]
    if schema.get("additionalProperties") is False:
        errors.extend(f"unexpected_{key}" for key in sorted(set(response) - set(properties)))
    for key, rules in properties.items():
        if key not in response:
            continue
        if not _schema_value_valid(response[key], rules):
            errors.append(f"invalid_{key}")
    return errors


def _schema_value_valid(value: object, rules: dict) -> bool:
    if "enum" in rules:
        try:
            if value not in rules["enum"]:
                return False
        except TypeError:
            return False
    expected = rules.get("type")
    if isinstance(expected, list):
        return any(_schema_value_valid(value, {**rules, "type": item}) for item in expected)
    if expected == "string":
        if not isinstance(value, str):
            return False
        return bool(value.strip())
    if expected == "null":
        return value is None
    if expected == "number":
        return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        if not isinstance(value, list):
            return False
        item_rules = rules.get("items")
        return not item_rules or all(_schema_value_valid(item, item_rules) for item in value)
    if expected == "object":
        if not isinstance(value, dict):
            return False
        return not _structured_response_schema_errors(value, rules)
    return True


def _task_response_schema_errors(case: MealCase, response: dict | None) -> list[str]:
    if case.task_class in {"food_photo_nutrition", "meal_text_nutrition"}:
        return _estimate_schema_errors(response)
    errors = _structured_response_schema_errors(response, _schema_for_case(case))
    if isinstance(response, dict):
        errors.extend(_number_bounds_errors(response, TASK_NUMBER_BOUNDS.get(case.task_class, {})))
    return list(dict.fromkeys(errors))


def _number_bounds_errors(response: dict, bounds: dict[str, tuple[float, float]]) -> list[str]:
    errors = []
    for key, (minimum, maximum) in bounds.items():
        value = response.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            continue
        if not (minimum <= float(value) <= maximum):
            errors.append(f"invalid_{key}")
    return errors


def _non_empty_string_list(value: object) -> bool:
    return isinstance(value, list) and any(isinstance(item, str) and item.strip() for item in value)


def _workout_adjustment_text(response: dict) -> str:
    adjustments = response.get("adjustments")
    if not isinstance(adjustments, list):
        return ""
    return " ".join(_string_values({"adjustments": adjustments}))


def _daily_focus_text(response: dict) -> str:
    return " ".join(
        _string_values({
            key: response.get(key)
            for key in ("priorities", "training_focus", "nutrition_focus", "recovery_focus")
        })
    )


def _branded_nutrition_within_bands(case: MealCase, response: dict) -> bool:
    bands = EXPECTED_BRANDED_NUTRITION_BANDS.get(case.case_id, {})
    for key, (low, high) in bands.items():
        value = response.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            return False
        if not (low <= float(value) <= high):
            return False
    return bool(bands)


def _non_nutrition_task_checks(case: MealCase, response: dict, expected: set[str], observed: set[str]) -> dict:
    if case.task_class == "workout_analysis_adjustment":
        adjustment_tokens = _hint_tokens(_workout_adjustment_text(response))
        return {
            "response_hint_match": bool(expected & observed),
            "actionable_adjustments": bool(response.get("adjustments")) and bool(expected & adjustment_tokens),
            "readiness_matches": response.get("readiness") == EXPECTED_WORKOUT_READINESS.get(case.case_id),
        }
    if case.task_class == "daily_coaching_brief":
        focus_tokens = _hint_tokens(_daily_focus_text(response))
        required_matches = min(2, len(expected))
        return {
            "response_hint_match": bool(expected & observed),
            "actionable_priorities": _non_empty_string_list(response.get("priorities")),
            "focus_matches": len(expected & focus_tokens) >= required_matches,
        }
    if case.task_class == "branded_food_resolution":
        return {
            "response_hint_match": bool(expected & observed),
            "branded_nutrition_plausible": _branded_nutrition_within_bands(case, response),
        }
    return {
        "response_hint_match": bool(expected & observed),
    }


def _task_confidence_calibrated(case: MealCase, response: dict) -> bool:
    confidence = response.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return False
    confidence = float(confidence)
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        return False
    if case.ambiguity == "high":
        return confidence <= 0.65
    if case.ambiguity == "low":
        return confidence >= 0.55
    return 0.35 <= confidence <= 0.9


def _task_quality_score(case: MealCase, response: dict | None, schema_errors: list[str]) -> dict:
    if case.task_class in {"adjust_intent", "swap_resolution", "post_workout_analysis"}:
        return _structured_quality_score(case, response, schema_errors)
    if case.task_class in {"food_photo_nutrition", "meal_text_nutrition"}:
        return _quality_score(case, response, schema_errors)
    if not response or schema_errors:
        return {
            "passed": False,
            "response_hint_match": False,
            "confidence_calibrated": False,
            "raw_trace_free": False,
        }
    if case.task_class == "branded_food_resolution":
        observed_text = " ".join(
            _string_values({
                key: response.get(key)
                for key in ("resolved_name", "brand", "serving_description")
            })
        )
    else:
        observed_text = " ".join(_string_values(response))
    observed = _hint_tokens(observed_text)
    expected = _hint_tokens(case.expected_item_hint)
    joined = "\n".join(_string_values(response)).lower()
    raw_trace_free = not any(fragment in joined for fragment in RAW_TRACE_BLOCKED_FRAGMENTS)
    checks = {
        **_non_nutrition_task_checks(case, response, expected, observed),
        "confidence_calibrated": _task_confidence_calibrated(case, response),
        "raw_trace_free": raw_trace_free,
    }
    checks["passed"] = all(checks.values())
    return checks


def _normalized_tokens(value: object) -> set[str]:
    return {
        token
        for token in "".join(ch if ch.isalnum() else " " for ch in str(value or "").lower()).split()
        if token
    }


def _normalized_text(value: object) -> str:
    return " ".join(sorted(_normalized_tokens(value)))


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _joint_key(value: object) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    side, joint = value.get("side"), value.get("joint")
    if not isinstance(side, str) or not isinstance(joint, str):
        return None
    return (_normalized_text(side), _normalized_text(joint))


def _swap_key(value: object) -> tuple[str, str, str] | None:
    if not isinstance(value, dict):
        return None
    values = [value.get(key) for key in ("replace_exercise", "target_muscle", "target_exercise")]
    if not isinstance(values[0], str) or not isinstance(values[1], str):
        return None
    target = "" if values[2] is None else _normalized_text(values[2])
    return (_normalized_text(values[0]), _normalized_text(values[1]), target)


def _number_equal(observed: object, expected: object, tolerance: float = 0.05) -> bool:
    if isinstance(observed, bool) or not isinstance(observed, (int, float)):
        return False
    if isinstance(expected, bool) or not isinstance(expected, (int, float)):
        return False
    return math.isfinite(float(observed)) and abs(float(observed) - float(expected)) <= tolerance


def _forbidden_action_free(response: dict) -> bool:
    text = " ".join(_string_values(response)).lower()
    if any(token in text for token in ("prescribe", "prescription")):
        return False
    return not re.search(
        r"\b\d+(?:\.\d+)?\s*(?:sets?|reps?|lbs?|pounds?|kg)\b",
        text,
    )


def _structured_quality_score(case: MealCase, response: dict | None, schema_errors: list[str]) -> dict:
    expected = case.expected or {}
    if not response or schema_errors:
        return {"score": 0.0, "verdict": "FAIL", "failure_reasons": ["schema_invalid"], "passed": False}
    if case.task_class == "adjust_intent":
        intent = response.get("intent")
        if not isinstance(intent, dict):
            return {"score": 0.0, "verdict": "FAIL", "failure_reasons": ["schema_invalid"], "passed": False}
        checks: dict[str, bool] = {}
        checks["avoid_muscles"] = {
            _normalized_text(item) for item in _string_list(intent.get("avoid_muscles"))
        } == {_normalized_text(item) for item in expected.get("avoid_muscles", [])}
        checks["avoid_joints"] = {
            key for key in (_joint_key(item) for item in intent.get("avoid_joints", [])) if key
        } == {
            key for key in (_joint_key(item) for item in expected.get("avoid_joints", [])) if key
        }
        checks["swap"] = {
            key for key in (_swap_key(item) for item in intent.get("swap", [])) if key
        } == {
            key for key in (_swap_key(item) for item in expected.get("swap", [])) if key
        }
        checks["swap_reasons"] = all(
            isinstance(item, dict) and isinstance(item.get("reason"), str) and bool(item["reason"].strip())
            for item in intent.get("swap", [])
        )
        for key in ("rpe_delta", "sets_delta_pct", "duration_cap_min"):
            checks[key] = _number_equal(intent.get(key), expected.get(key, 0), tolerance=0.05)
        checks["rpe_delta_bounds"] = (
            isinstance(intent.get("rpe_delta"), (int, float))
            and not isinstance(intent.get("rpe_delta"), bool)
            and -1 <= float(intent.get("rpe_delta")) <= 1
        )
        checks["sets_delta_pct_bounds"] = (
            isinstance(intent.get("sets_delta_pct"), (int, float))
            and not isinstance(intent.get("sets_delta_pct"), bool)
            and -20 <= float(intent.get("sets_delta_pct")) <= 20
        )
        checks["duration_cap_bounds"] = (
            isinstance(intent.get("duration_cap_min"), (int, float))
            and not isinstance(intent.get("duration_cap_min"), bool)
            and float(intent.get("duration_cap_min")) >= 0
        )
        checks["drop_cardio"] = intent.get("drop_cardio") is expected.get("drop_cardio", False)
        checks["summary"] = isinstance(response.get("summary"), str) and bool(response["summary"].strip())
        checks["forbidden_actions"] = _forbidden_action_free(response)
        failures = [key for key, passed in checks.items() if not passed]
    elif case.task_class == "swap_resolution":
        expected_name = case.expected.get("canonical_name")
        observed_name = response.get("canonical_name")
        checks = {
            "canonical_name": (
                observed_name is None
                if expected_name is None
                else isinstance(observed_name, str)
                and _normalized_text(observed_name) == _normalized_text(expected_name)
            ),
            "confidence": (
                not isinstance(response.get("confidence"), bool)
                and isinstance(response.get("confidence"), (int, float))
                and math.isfinite(float(response.get("confidence")))
                and 0 <= float(response.get("confidence")) <= 1
            ),
            "reason": isinstance(response.get("reason"), str) and bool(response["reason"].strip()),
            "forbidden_actions": _forbidden_action_free(response),
        }
        failures = [key for key, passed in checks.items() if not passed]
    else:
        checks = {}
        empty_fields = set(expected.get("empty_fields", ()))
        for field_name in ("summary", "wins", "concerns", "comparison", "next_session_cue"):
            observed_text = " ".join(_string_values(response.get(field_name)))
            groups = expected.get(field_name, ())
            if field_name in empty_fields:
                checks[field_name] = response.get(field_name) == []
            else:
                observed_tokens = _normalized_tokens(observed_text)
                checks[field_name] = all(
                    bool(set(group) & observed_tokens)
                    for group in groups
                ) if groups else bool(observed_text.strip())
        response_text = " ".join(_string_values(response)).lower()
        checks["forbidden_actions"] = not any(
            token in response_text for token in ("prescribe", "prescription", "diagnose")
        ) and not re.search(
            r"\b(?:do|perform|complete|add|use|target|aim for)\s+"
            r"(?:rpe\s*)?\d+(?:\.\d+)?"
            r"(?:\s*(?:sets?|reps?|lbs?|pounds?|kg|kilograms?|minutes?|mins?|seconds?|secs?))?\b",
            response_text,
        )
        failures = [key for key, passed in checks.items() if not passed]
    score = round(sum(checks.values()) / len(checks), 3) if checks else 0.0
    return {
        "score": score,
        "verdict": "PASS" if not failures else "FAIL",
        "failure_reasons": failures,
        "passed": not failures,
        **checks,
    }


def _quality_score(case: MealCase, estimate: dict | None, schema_errors: list[str]) -> dict:
    if not estimate or schema_errors:
        return {
            "passed": False,
            "item_hint_match": False,
            "calories_within_band": False,
            "macro_energy_consistent": False,
            "confidence_calibrated": False,
            "raw_trace_free": False,
        }
    checks = {
        "item_hint_match": _item_hint_matches(case, estimate),
        "calories_within_band": _calories_within_band(case, estimate),
        "nutrition_fields_within_bands": _nutrition_fields_within_bands(case, estimate),
        "macro_energy_consistent": _macro_energy_consistent(estimate),
        "portion_reasonable": _portion_reasonable(estimate),
        "confidence_calibrated": _confidence_calibrated(case, estimate),
        "raw_trace_free": _raw_trace_free(estimate),
    }
    checks["passed"] = all(checks.values())
    return checks


def _estimate_schema_errors(estimate: dict | None) -> list[str]:
    if not estimate:
        return ["response_not_json_object"]
    required = {
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
    }
    allowed = set(MEAL_ESTIMATE_RESPONSE_SCHEMA["properties"])
    errors = [f"missing_{key}" for key in sorted(required - set(estimate))]
    errors.extend(f"unexpected_{key}" for key in sorted(set(estimate) - allowed))
    for key in ("item_name",):
        if not isinstance(estimate.get(key), str) or not estimate.get(key).strip():
            errors.append(f"invalid_{key}")
    portion_description = estimate.get("portion_description")
    if portion_description is not None and (
        not isinstance(portion_description, str) or not portion_description.strip()
    ):
        errors.append("invalid_portion_description")
    if estimate.get("meal_type") not in {"breakfast", "lunch", "dinner", "snack"}:
        errors.append("invalid_meal_type")
    maxima = {
        "calories": CALORIE_MAX,
        "protein_g": MACRO_GRAM_MAX,
        "carbs_g": MACRO_GRAM_MAX,
        "fat_g": MACRO_GRAM_MAX,
        "sodium_mg": SODIUM_MG_MAX,
        "fiber_g": MACRO_GRAM_MAX,
    }
    for key, maximum in maxima.items():
        value = estimate.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or value < 0
            or value > maximum
        ):
            errors.append(f"invalid_{key}")
    confidence = estimate.get("confidence")
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
        or confidence < 0
        or confidence > 1
    ):
        errors.append("invalid_confidence")
    if not isinstance(estimate.get("ambiguous"), bool):
        errors.append("invalid_ambiguous")
    uncertainty_notes = estimate.get("uncertainty_notes")
    if not isinstance(uncertainty_notes, list) or not all(isinstance(note, str) for note in uncertainty_notes):
        errors.append("invalid_uncertainty_notes")
    return errors


def run_model_case(
    case: MealCase,
    *,
    model: str,
    lm_studio_url: str = LM_STUDIO_URL,
    image_path: str | None = None,
    timeout: float = 60.0,
) -> dict:
    started = time.time()
    try:
        payload = json.dumps(_chat_payload(case, model, image_path=image_path)).encode("utf-8")
        request = urllib.request.Request(
            f"{lm_studio_url.rstrip('/')}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        latency_ms = int((time.time() - started) * 1000)
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ValueError("completion choices missing")
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise ValueError("completion choice must be an object")
        message = first_choice.get("message") or {}
        content = message.get("content") or message.get("reasoning_content") or ""
        estimate = _extract_json_object(content)
        errors = _task_response_schema_errors(case, estimate)
        public_estimate = None
        if estimate:
            if case.task_class in {"food_photo_nutrition", "meal_text_nutrition"}:
                public_estimate = {
                    key: estimate.get(key)
                    for key in PUBLIC_ESTIMATE_FIELDS
                    if key != "source"
                }
                public_estimate["source"] = "local_model_benchmark"
            else:
                public_estimate = estimate
        quality = _task_quality_score(case, estimate, errors)
        return {
            "case_id": case.case_id,
            "input_type": case.input_type,
            "task_class": case.task_class,
            "model": model,
            "has_image": bool(image_path),
            "ran_model": True,
            "latency_ms": latency_ms,
            "schema_valid": not errors,
            "schema_errors": errors,
            "quality": quality,
            "estimate": public_estimate,
            "confidence": estimate.get("confidence") if estimate else None,
            "ambiguous": estimate.get("ambiguous") if estimate else None,
            "expected_item_hint": case.expected_item_hint,
        }
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as exc:
        return {
            "case_id": case.case_id,
            "input_type": case.input_type,
            "task_class": case.task_class,
            "model": model,
            "has_image": bool(image_path),
            "ran_model": False,
            "latency_ms": int((time.time() - started) * 1000),
            "schema_valid": False,
            "schema_errors": [f"request_failed:{exc}"],
            "quality": _task_quality_score(case, None, [f"request_failed:{exc}"]),
            "estimate": None,
            "expected_item_hint": case.expected_item_hint,
        }


def run_model_benchmark(
    cases: list[MealCase],
    *,
    model: str | None = None,
    text_model: str | None = None,
    vision_model: str | None = None,
    lm_studio_url: str = LM_STUDIO_URL,
    image_map: dict[str, str] | None = None,
) -> dict:
    results = []
    image_map = image_map or {}
    for case in cases:
        image_path = image_map.get(case.case_id)
        if requires_image(case) and not image_path:
            results.append({
                "case_id": case.case_id,
                "input_type": case.input_type,
                "task_class": case.task_class,
                "model": vision_model or model,
                "has_image": False,
                "ran_model": False,
                "latency_ms": None,
                "schema_valid": False,
                "schema_errors": ["missing_image_mapping"],
                "quality": _task_quality_score(case, None, ["missing_image_mapping"]),
                "estimate": None,
                "confidence": None,
                "ambiguous": None,
                "expected_item_hint": case.expected_item_hint,
            })
            continue
        case_model = _model_for_case(case, model=model, text_model=text_model, vision_model=vision_model)
        result = run_model_case(
            case,
            model=case_model,
            lm_studio_url=lm_studio_url,
            image_path=image_path,
            timeout=STRUCTURED_TASK_TIMEOUT_SEC.get(case.task_class, 60.0),
        )
        result.setdefault("task_class", case.task_class)
        results.append(result)
    valid_count = sum(1 for result in results if result["schema_valid"])
    quality_pass_count = sum(1 for result in results if result.get("quality", {}).get("passed"))
    latencies = [
        result["latency_ms"]
        for result in results
        if result.get("ran_model", True) and isinstance(result.get("latency_ms"), (int, float))
    ]
    latency_ms_avg = round(sum(latencies) / len(latencies), 1) if latencies else None
    latency_gates: dict[str, dict] = {}
    for route, limit in (("text", TEXT_LATENCY_PASS_MS), ("vision", VISION_LATENCY_PASS_MS)):
        route_results = [
            result
            for result in results
            if (
                "vision"
                if result.get("task_class") == "food_photo_nutrition"
                else "text"
                if result.get("task_class") == "meal_text_nutrition"
                else "other"
            ) == route
        ]
        route_latencies = [
            result["latency_ms"]
            for result in route_results
            if result.get("ran_model", True) and isinstance(result.get("latency_ms"), (int, float))
        ]
        if not route_results:
            continue
        route_avg = round(sum(route_latencies) / len(route_latencies), 1) if route_latencies else None
        latency_gates[route] = {
            "latency_ms_avg": route_avg,
            "latency_limit_ms": limit,
            "latency_passed": route_avg is not None and route_avg <= limit,
        }
    task_latency_gates: dict[str, dict] = {}
    for task_class, limit in ALL_TASK_LATENCY_PASS_MS.items():
        task_results = [result for result in results if result.get("task_class") == task_class]
        if not task_results:
            continue
        task_latencies = [
            result["latency_ms"]
            for result in task_results
            if result.get("ran_model", True) and isinstance(result.get("latency_ms"), (int, float))
        ]
        task_avg = round(sum(task_latencies) / len(task_latencies), 1) if task_latencies else None
        if task_class in STRUCTURED_TASK_LATENCY_PASS_MS:
            latency_passed = (
                len(task_latencies) == len(task_results)
                and all(latency <= limit for latency in task_latencies)
            )
        else:
            latency_passed = task_avg is not None and task_avg <= limit
        task_latency_gates[task_class] = {
            "latency_ms_avg": task_avg,
            "latency_ms_max": max(task_latencies) if task_latencies else None,
            "latency_limit_ms": limit,
            "latency_passed": latency_passed,
        }
    task_summary = {}
    for task_class in sorted({case.task_class for case in cases} | set(TASK_LATENCY_PASS_MS)):
        task_results = [result for result in results if result.get("task_class") == task_class]
        if not task_results:
            continue
        task_summary[task_class] = {
            "case_count": len(task_results),
            "schema_valid_count": sum(1 for result in task_results if result["schema_valid"]),
            "quality_pass_count": sum(1 for result in task_results if result.get("quality", {}).get("passed")),
            "latency": task_latency_gates.get(task_class),
        }
    latency_passed = not latency_gates or all(gate["latency_passed"] for gate in latency_gates.values())
    task_latency_passed = bool(task_latency_gates) and all(
        gate["latency_passed"] for gate in task_latency_gates.values()
    )
    latency_limit = (
        next(iter(latency_gates.values()))["latency_limit_ms"]
        if len(latency_gates) == 1
        else None
    )
    report_results = [_public_case_result(case, result) for case, result in zip(cases, results)]
    return {
        "model": model or "per-task",
        "text_model": text_model,
        "vision_model": vision_model,
        "case_count": len(results),
        "task_class_counts": task_class_counts(cases),
        "model_run_count": len(latencies),
        "missing_image_count": sum(1 for result in results if "missing_image_mapping" in result.get("schema_errors", [])),
        "schema_valid_count": valid_count,
        "schema_valid_rate": round(valid_count / len(results), 3) if results else 0,
        "quality_pass_count": quality_pass_count,
        "quality_pass_rate": round(quality_pass_count / len(results), 3) if results else 0,
        "latency_ms_avg": latency_ms_avg,
        "latency_passed": latency_passed,
        "latency_limit_ms": latency_limit,
        "latency_gates": latency_gates,
        "task_latency_passed": task_latency_passed,
        "task_latency_gates": task_latency_gates,
        "task_summary": task_summary,
        "candidate_passed": (
            bool(results)
            and valid_count == len(results)
            and quality_pass_count == len(results)
            and latency_passed
            and task_latency_passed
        ),
        # Keep the public result rows strictly raw-free.  The local ``results``
        # variable above never leaves this function.
        "report": report_results,
        "results": report_results,
    }


def _public_case_result(case: MealCase, result: dict) -> dict:
    quality = result.get("quality") or {}
    failure_reasons = []
    schema_errors = result.get("schema_errors") or []
    if schema_errors:
        if any(str(error).startswith("request_failed:") for error in schema_errors):
            failure_reasons.append("request_failed")
        elif any(str(error).startswith("missing_image_mapping") for error in schema_errors):
            failure_reasons.append("missing_image_mapping")
        else:
            failure_reasons.append("schema_invalid")
    if not quality.get("passed", False) and not schema_errors:
        failure_reasons.extend(
            key for key, value in quality.items()
            if key not in {"passed", "score", "verdict", "failure_reasons"} and value is False
        )
    row = {
        "case_id": case.case_id,
        "task": case.task_class,
        "score": quality.get("score", 1.0 if quality.get("passed") else 0.0),
        "verdict": quality.get("verdict", "PASS" if quality.get("passed") else "FAIL"),
        "failure_reasons": list(dict.fromkeys(failure_reasons)),
    }
    if "missing_image_mapping" in schema_errors:
        row["skipped_reason"] = "missing_image_mapping"
    return row


def _model_for_case(
    case: MealCase,
    *,
    model: str | None,
    text_model: str | None,
    vision_model: str | None,
) -> str:
    if requires_image(case):
        selected = vision_model or model
    else:
        selected = text_model or model
    if not selected:
        raise ValueError("model is required; provide --run-model or task-specific model flags")
    return selected


def _load_image_map(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("image map must be a JSON object keyed by case_id")
    return {str(key): str(value) for key, value in payload.items()}


def _public_benchmark_summary(summary: dict) -> dict:
    public = {key: value for key, value in summary.items() if key not in {"results", "report"}}
    public["results"] = summary.get("report") or summary.get("results", [])
    return public


def _safe_probe(probe: dict) -> dict:
    return {
        "reachable": bool(probe.get("reachable")),
        "latency_ms": probe.get("latency_ms"),
        "models": list(probe.get("models") or []),
    }


def _safe_case_listing(cases: list[MealCase]) -> list[dict]:
    return [
        {
            "case_id": case.case_id,
            "task": case.task_class,
            "input_type": case.input_type,
            "coverage": list(case.coverage),
        }
        for case in cases
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lm-studio-url", default=LM_STUDIO_URL)
    parser.add_argument("--probe-only", action="store_true")
    parser.add_argument("--list-cases", action="store_true")
    parser.add_argument("--run-model", help="Run the benchmark against an OpenAI-compatible local chat model")
    parser.add_argument("--text-model", help="Model used for non-image benchmark tasks")
    parser.add_argument("--vision-model", help="Model used for image benchmark tasks")
    parser.add_argument(
        "--case-set",
        choices=(
            "all",
            "text",
            "vision",
            "meal_text",
            "food_photo",
            "workout",
            "daily_brief",
            "branded_food",
            "adjust_intent",
            "swap_resolution",
            "post_workout_analysis",
        ),
        default="all",
        help="Case subset used with --run-model",
    )
    parser.add_argument("--image-map", help="Optional JSON mapping case_id to private local image path")
    parser.add_argument("--output-file", help="Write the raw-free benchmark report JSON to this file")
    args = parser.parse_args()

    probe = probe_lm_studio(args.lm_studio_url)
    cases = all_cases()
    if args.case_set == "text":
        cases = TEXT_CASES
    elif args.case_set == "vision":
        cases = image_capable_cases()
    elif args.case_set == "meal_text":
        cases = TEXT_CASES
    elif args.case_set == "food_photo":
        cases = PHOTO_CASES + PACKAGED_CASES + AMBIGUOUS_CASES
    elif args.case_set == "workout":
        cases = WORKOUT_CASES
    elif args.case_set == "daily_brief":
        cases = DAILY_BRIEF_CASES
    elif args.case_set == "branded_food":
        cases = BRANDED_FOOD_CASES
    elif args.case_set == "adjust_intent":
        cases = ADJUST_INTENT_CASES
    elif args.case_set == "swap_resolution":
        cases = SWAP_RESOLUTION_CASES
    elif args.case_set == "post_workout_analysis":
        cases = POST_WORKOUT_ANALYSIS_CASES
    output = {
        "hardware": mac_hardware_summary(),
        "lm_studio": _safe_probe(probe),
        "case_counts": {
            "text": len(TEXT_CASES),
            "photo": len(PHOTO_CASES),
            "packaged_photo": len(PACKAGED_CASES),
            "ambiguous": len(AMBIGUOUS_CASES),
            "workout": len(WORKOUT_CASES),
            "daily_brief": len(DAILY_BRIEF_CASES),
            "branded_food": len(BRANDED_FOOD_CASES),
            "adjust_intent": len(ADJUST_INTENT_CASES),
            "swap_resolution": len(SWAP_RESOLUTION_CASES),
            "post_workout_analysis": len(POST_WORKOUT_ANALYSIS_CASES),
            "image_capable": len(image_capable_cases()),
            "nutrition_total": len(nutrition_cases()),
            "total": len(all_cases()),
        },
        "task_class_counts": task_class_counts(),
        "latency_targets_ms": ALL_TASK_LATENCY_PASS_MS,
        "routing_recommendation": routing_recommendation(probe.get("models") or []),
    }
    if args.list_cases:
        output["cases"] = _safe_case_listing(all_cases())
    if args.probe_only:
        serialized = json.dumps(output, indent=2, sort_keys=True)
        if args.output_file:
            with open(args.output_file, "w", encoding="utf-8") as handle:
                handle.write(serialized + "\n")
        print(serialized)
        return 0
    if args.run_model or args.text_model or args.vision_model:
        image_map = _load_image_map(args.image_map)
        if not args.run_model:
            if args.vision_model and any(not requires_image(case) for case in cases) and not args.text_model:
                parser.error("--text-model is required for selected non-image cases when --run-model is not provided")
            if args.text_model and not args.vision_model and any(case.case_id in image_map for case in cases if requires_image(case)):
                parser.error("--vision-model is required for selected image cases with image-map entries when --run-model is not provided")
        benchmark = run_model_benchmark(
            cases,
            model=args.run_model,
            text_model=args.text_model,
            vision_model=args.vision_model,
            lm_studio_url=args.lm_studio_url,
            image_map=image_map,
        )
        output["benchmark"] = _public_benchmark_summary(benchmark)
    serialized = json.dumps(output, indent=2, sort_keys=True)
    if args.output_file:
        with open(args.output_file, "w", encoding="utf-8") as handle:
            handle.write(serialized + "\n")
    print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
