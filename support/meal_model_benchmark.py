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
import platform
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass


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
}
TASK_SCHEMA_NAMES = {
    "food_photo_nutrition": "meal_estimate",
    "meal_text_nutrition": "meal_estimate",
    "workout_analysis_adjustment": "workout_adjustment",
    "daily_coaching_brief": "daily_coaching_brief",
    "branded_food_resolution": "branded_food_resolution",
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


@dataclass(frozen=True)
class MealCase:
    case_id: str
    input_type: str
    prompt: str
    expected_item_hint: str
    ambiguity: str
    notes: str = ""
    task_class: str = "meal_text_nutrition"


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

WORKOUT_CASES = [
    MealCase(
        "workout-001",
        "workout",
        "Planned: heavy lower day with squats 5x5 and RDL 4x8. Signals: sleep 5h, legs sore 8/10, HRV down, yesterday intervals.",
        "reduce lower volume",
        "high",
        task_class="workout_analysis_adjustment",
    ),
    MealCase(
        "workout-002",
        "workout",
        "Planned: upper hypertrophy. Signals: sleep 8h, soreness low, last bench RPE 7, calories on target.",
        "progress upper workout",
        "low",
        task_class="workout_analysis_adjustment",
    ),
    MealCase(
        "workout-003",
        "workout",
        "Planned: tempo run. Signals: resting HR elevated, knee pain 4/10, missed carbs at lunch.",
        "swap run",
        "medium",
        task_class="workout_analysis_adjustment",
    ),
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


def nutrition_cases() -> list[MealCase]:
    return TEXT_CASES + PHOTO_CASES + PACKAGED_CASES + AMBIGUOUS_CASES


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


def _chat_payload(case: MealCase, model: str, image_path: str | None = None) -> dict:
    instruction = _task_instruction(case)
    content: str | list[dict] = f"{instruction}\n\nTask input: {case.prompt}"
    if image_path:
        mime_type = mimetypes.guess_type(image_path)[0] or "image/jpeg"
        with open(image_path, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
        content = [
            {"type": "text", "text": instruction},
            {"type": "text", "text": "Estimate the meal from the attached image only."},
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
    blocked_fragments = (
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
    return not any(fragment in joined for fragment in blocked_fragments)


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


def _task_quality_score(case: MealCase, response: dict | None, schema_errors: list[str]) -> dict:
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
    confidence = response.get("confidence")
    confidence_calibrated = (
        not isinstance(confidence, bool)
        and isinstance(confidence, (int, float))
        and math.isfinite(float(confidence))
        and 0 <= float(confidence) <= 1
    )
    joined = "\n".join(_string_values(response)).lower()
    raw_trace_free = not any(
        fragment in joined
        for fragment in (
            "task input:",
            "meal input:",
            "image_url",
            "data:image",
            "```",
            "/tmp/",
            ".jpg",
            ".jpeg",
            ".png",
            ".heic",
        )
    )
    checks = {
        "response_hint_match": bool(expected & observed),
        "confidence_calibrated": confidence_calibrated,
        "raw_trace_free": raw_trace_free,
    }
    checks["passed"] = all(checks.values())
    return checks


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
    for task_class, limit in TASK_LATENCY_PASS_MS.items():
        task_results = [result for result in results if result.get("task_class") == task_class]
        if not task_results:
            continue
        task_latencies = [
            result["latency_ms"]
            for result in task_results
            if result.get("ran_model", True) and isinstance(result.get("latency_ms"), (int, float))
        ]
        task_avg = round(sum(task_latencies) / len(task_latencies), 1) if task_latencies else None
        task_latency_gates[task_class] = {
            "latency_ms_avg": task_avg,
            "latency_limit_ms": limit,
            "latency_passed": task_avg is not None and task_avg <= limit,
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
        "results": results,
    }


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
        ),
        default="all",
        help="Case subset used with --run-model",
    )
    parser.add_argument("--image-map", help="Optional JSON mapping case_id to private local image path")
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
    output = {
        "hardware": mac_hardware_summary(),
        "lm_studio": probe,
        "case_counts": {
            "text": len(TEXT_CASES),
            "photo": len(PHOTO_CASES),
            "packaged_photo": len(PACKAGED_CASES),
            "ambiguous": len(AMBIGUOUS_CASES),
            "workout": len(WORKOUT_CASES),
            "daily_brief": len(DAILY_BRIEF_CASES),
            "branded_food": len(BRANDED_FOOD_CASES),
            "image_capable": len(image_capable_cases()),
            "nutrition_total": len(nutrition_cases()),
            "total": len(all_cases()),
        },
        "task_class_counts": task_class_counts(),
        "latency_targets_ms": TASK_LATENCY_PASS_MS,
        "routing_recommendation": routing_recommendation(probe.get("models") or []),
    }
    if args.list_cases:
        output["cases"] = [asdict(case) for case in all_cases()]
    if args.probe_only:
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0
    if args.run_model or args.text_model or args.vision_model:
        image_map = _load_image_map(args.image_map)
        if not args.run_model:
            if args.vision_model and any(not requires_image(case) for case in cases) and not args.text_model:
                parser.error("--text-model is required for selected non-image cases when --run-model is not provided")
            if args.text_model and not args.vision_model and any(case.case_id in image_map for case in cases if requires_image(case)):
                parser.error("--vision-model is required for selected image cases with image-map entries when --run-model is not provided")
        output["benchmark"] = run_model_benchmark(
            cases,
            model=args.run_model,
            text_model=args.text_model,
            vision_model=args.vision_model,
            lm_studio_url=args.lm_studio_url,
            image_map=image_map,
        )
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
