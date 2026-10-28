"""Read-only tools for the fitness MCP server.

Every tool GETs an allowlisted dashboard route and returns only the fields
named here; nothing is passed through raw, and no tool writes anywhere.
"""
from datetime import date, timedelta
from typing import Annotated, Any

import httpx2
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}
CALENDAR_NOTE = (
    " This server only reads. To protect training time, use your own calendar "
    "connector to block it; nothing here writes."
)
SOURCES = ("open_wearables", "oura", "whoop", "apple_health")  # food was dropped from scope


def _pick(src: Any, keys: tuple[str, ...]) -> dict:
    return {k: src[k] for k in keys if k in src} if isinstance(src, dict) else {}


def _rows(src: Any, keys: tuple[str, ...]) -> list[dict]:
    return [_pick(r, keys) for r in src] if isinstance(src, list) else []


def _since(rows: list[dict], days: int) -> list[dict]:
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    return [r for r in rows if (r.get("date") or "") >= cutoff]


def _freshness(block: Any) -> dict:
    block = block if isinstance(block, dict) else {}
    return {s: _pick(block.get(s), ("status", "last_data_point", "last_sync_attempt")) for s in SOURCES}


class Dashboard:
    """GET-only client for the Flask app, authenticated with the loopback read token."""

    def __init__(self, base_url: str, token: str, transport: httpx2.AsyncBaseTransport | None = None):
        self._http = httpx2.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
            transport=transport,
        )

    async def get(self, path: str, **params: str) -> Any:
        try:
            response = await self._http.get(path, params=params or None)
        except httpx2.HTTPError as exc:
            raise ToolError("The fitness dashboard is unreachable.") from exc
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ToolError(f"The fitness dashboard answered {response.status_code}.")
        return response.json()


def register(mcp: FastMCP, dash: Dashboard) -> None:
    @mcp.tool(annotations=READ_ONLY, description=(
        "Today's training recommendation: readiness, why, and the planned session "
        "(exercises with target sets, reps, weight and RPE, and estimated minutes)." + CALENDAR_NOTE
    ))
    async def get_today_plan() -> dict:
        raw = await dash.get("/api/recommendation/smart") or {}
        plan = raw.get("next_workout") or {}
        factors = raw.get("readiness_factors") or {}
        return {
            **_pick(raw, (
                "recommendation", "readiness", "effective_readiness", "sleep_score", "hrv",
                "hrv_trend", "avoid_muscles", "suggested_workout", "reasoning",
                "time_of_day", "confidence_level",
            )),
            "readiness_factors": {
                "acwr": _pick(factors.get("acwr"), ("acwr", "risk")),
                "sleep_debt": _pick(factors.get("sleep_debt"), ("debt_minutes", "status")),
            },
            "next_workout": {
                **_pick(plan, (
                    "focus", "goal_name", "estimated_minutes", "available_time",
                    "time_adjusted", "rpe_range", "muscles_to_avoid",
                )),
                "mesocycle": _pick(plan.get("mesocycle"), ("week", "phase", "rpe_base")),
                "cardio": _pick(plan.get("cardio"), ("include_cardio", "reason")),
                "exercises": _rows(plan.get("exercises"), (
                    "exercise", "muscle", "target_sets", "target_reps", "target_weight",
                    "rpe_target", "rationale",
                )),
            },
            "data_freshness": _freshness(raw.get("freshness")),
        }

    @mcp.tool(annotations=READ_ONLY)
    async def get_workout_history(days: Annotated[int, Field(ge=1, le=365)] = 28) -> dict:
        """Lifting, cardio and recovery sessions from the last `days` days, plus all-time
        and 30-day estimated-1RM records per exercise."""
        raw = await dash.get("/api/history-all") or {}
        workouts = _since([_pick(w, (
            "date", "session_type", "duration_minutes", "total_sets", "total_volume", "exercises",
        )) for w in raw.get("workouts") or []], days)
        for w in workouts:
            w["exercises"] = [
                {
                    "name": e.get("machine") or e.get("exercise"),
                    "sets": _rows(e.get("sets"), ("weight_lbs", "reps", "rpe", "rpe_observed")),
                }
                for e in w.get("exercises") or [] if isinstance(e, dict)
            ]
        records = raw.get("personal_records")
        return {
            "workouts": workouts,
            "cardio": _since(_rows(raw.get("cardio"), (
                "date", "activity_type", "duration_minutes", "avg_heart_rate", "intensity",
            )), days),
            "recovery": _since(_rows(raw.get("recovery"), (
                "date", "recovery_type", "duration_minutes", "temperature",
            )), days),
            "personal_records": {
                name: _pick(r, ("all_time", "all_time_date", "recent_30", "recent_30_date"))
                for name, r in (records.items() if isinstance(records, dict) else [])
            },
        }

    @mcp.tool(annotations=READ_ONLY)
    async def get_progressive_overload() -> dict:
        """Top-set weight trend for each major lift: last, previous, change, and recent history."""
        raw = await dash.get("/api/progressive-overload") or {}
        lifts = _rows(raw.get("exercises"), (
            "exercise", "last_weight", "previous_weight", "change_lbs", "change_dir", "trend", "history",
        ))
        for lift in lifts:
            lift["history"] = _rows(lift.get("history"), ("date", "weight"))[-8:]
        return {"exercises": lifts}

    @mcp.tool(annotations=READ_ONLY)
    async def get_body_trend(days: Annotated[int, Field(ge=1, le=730)] = 90) -> dict:
        """Body weight, body fat and tape measurements over the last `days` days, with the
        weight trend, targets and estimated weeks to target."""
        history = await dash.get("/api/body-history") or {}
        recomp = await dash.get("/api/body-recomp") or {}
        summary = recomp.get("summary") or {}
        return {
            "trend": history.get("trend"),
            "history": _since(_rows(history.get("history"), (
                "date", "weight_lbs", "body_fat_pct", "weight_change",
                "neck_in", "waist_in", "chest_in", "hips_in", "arms", "legs",
            )), days),
            "latest": _pick(summary.get("latest"), ("date", "weight_lbs", "body_fat_pct")),
            **_pick(summary, ("target_weight_lbs", "target_body_fat_pct", "eta_weeks")),
        }

    @mcp.tool(annotations=READ_ONLY)
    async def get_sleep_recovery(days: Annotated[int, Field(ge=1, le=90)] = 7) -> dict:
        """Nightly sleep for the last `days` days, sleep consistency, and how fresh each
        wearable source is."""
        sleep = await dash.get("/api/sleep/analytics") or {}
        fresh = await dash.get("/api/freshness") or {}
        return {
            "nights": _since(_rows(sleep.get("history"), (
                "date", "sleep_duration_min", "deep_sleep_min", "rem_sleep_min", "light_sleep_min",
                "awake_min", "sleep_score", "efficiency", "avg_heart_rate", "avg_hrv", "source",
            )), days),
            **_pick(sleep, ("consistency_score", "sleep_perf_correlation")),
            "data_freshness": _freshness(fresh.get("freshness")),
        }

    @mcp.tool(annotations=READ_ONLY, description=(
        "Busy and free training windows for a day, from the owner's calendar feed." + CALENDAR_NOTE
    ))
    async def get_open_windows(day: str = "today") -> dict:
        if day not in ("today", "tomorrow"):
            raise ToolError("day must be 'today' or 'tomorrow'.")
        raw = await dash.get("/api/calendar/windows", day=day)
        if raw is None:  # route not shipped, or no calendar feed configured
            return {"status": "unconfigured", "day": day}
        return {
            **_pick(raw, ("status", "day")),
            "windows": _rows(raw.get("windows"), ("start", "end", "minutes")),
            "busy": _rows(raw.get("busy"), ("start", "end")),
        }
