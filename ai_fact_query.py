"""Sanitized AI fact context and deterministic fact answers."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
import uuid


@dataclass(frozen=True)
class AiFactAnswer:
    answer: str
    evidence: list[dict[str, str]]
    uncertainty: str | None = None
    suggested_action_id: str | None = None

    def public_dict(self) -> dict:
        return asdict(self)


def build_ai_fact_context(*, freshness: dict | None, wearable_sources: list[dict] | None, facts: list[dict] | None, history: list[dict] | None) -> dict:
    return {
        "generated_at": datetime.now().isoformat(),
        "freshness": freshness or {},
        "wearable_sources": wearable_sources or [],
        "facts": facts or [],
        "history": history or [],
        "mutation_policy": "suggestions_require_approval",
    }


def answer_fact_question(question: str, context: dict) -> AiFactAnswer:
    q = (question or "").strip().lower()
    history = context.get("history") if isinstance(context, dict) else []
    facts = context.get("facts") if isinstance(context, dict) else []
    sources = context.get("wearable_sources") if isinstance(context, dict) else []
    evidence: list[dict[str, str]] = []

    if "strength" in q or "lift" in q or "functional" in q:
        strength = [
            row for row in history or []
            if isinstance(row, dict) and row.get("canonical_category") == "strength_training"
        ]
        if strength:
            dates = sorted({str(row.get("date")) for row in strength if row.get("date")})
            evidence.append({
                "source": "history",
                "date_range": f"{dates[0]} to {dates[-1]}" if dates else "unknown",
                "freshness": "local_history",
            })
            return AiFactAnswer(
                answer=f"You have {len(strength)} strength-training sessions in the available history, combining logged lifts and provider strength workouts.",
                evidence=evidence,
            )
        return AiFactAnswer(
            answer="I do not have strength-training history evidence in the sanitized context yet.",
            evidence=[],
            uncertainty="No matching canonical strength history rows were available.",
        )

    if "wearable" in q or "source" in q or "open wearables" in q:
        for source in sources or []:
            if isinstance(source, dict):
                evidence.append({
                    "source": str(source.get("label") or source.get("source") or "wearable"),
                    "date_range": str(source.get("last_data_point") or "unknown"),
                    "freshness": str(source.get("status") or "unknown"),
                })
        if evidence:
            return AiFactAnswer(
                answer="Wearable source status is available from sanitized provider metadata. I can use it to explain confidence, freshness, and conflicts, not to auto-change records.",
                evidence=evidence,
            )
        return AiFactAnswer(
            answer="No wearable source evidence is available in the sanitized context yet.",
            evidence=[],
            uncertainty="Open Wearables or fallback provider metadata was missing.",
        )

    if facts:
        first = facts[0]
        evidence.append({
            "source": str(first.get("source_label") or first.get("provider_id") or "wearable fact"),
            "date_range": str(first.get("date") or "unknown"),
            "freshness": str(first.get("freshness") or "unknown"),
        })
        return AiFactAnswer(
            answer="I found sanitized wearable facts. Ask about strength history, wearable sources, sleep, recovery, or freshness for a more specific answer.",
            evidence=evidence,
        )

    return AiFactAnswer(
        answer="I do not have enough sanitized evidence to answer that yet.",
        evidence=[],
        uncertainty="No matching history or wearable fact rows were available.",
    )


def create_pending_suggestion(kind: str, summary: str, evidence: list[dict] | None = None) -> dict:
    return {
        "id": f"ai-suggestion-{uuid.uuid4().hex[:12]}",
        "kind": kind,
        "summary": summary,
        "evidence": evidence or [],
        "status": "pending",
        "created_at": datetime.now().isoformat(),
    }
