"""Canonical review-loop constants and current-answer classification."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo


LOOP_REL = Path("wiki/study_vaults/408-full/state/review-loop")
TIMEZONE = "Asia/Shanghai"


class ReviewLoopError(ValueError):
    pass


def morning_session_review_date(event: dict[str, object]) -> str:
    value = str(event.get("observed_date") or event.get("study_date") or "")
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ReviewLoopError("morning review date invalid") from exc
    return value


def normalize_morning_first_classification(
    result: str,
    *,
    choice_result: str = "not_applicable",
    reasoning_result: str = "not_observed",
    prompt_level: str = "none",
) -> tuple[str, dict[str, object]]:
    """Normalize the prepared-pack result without changing ordinary admission."""

    result = str(result or "").strip()
    choice_result = str(choice_result or "not_applicable").strip()
    reasoning_result = str(reasoning_result or "not_observed").strip()
    prompt_level = str(prompt_level or "none").strip()
    if result == "blank":
        result = "uncertain"
    if result not in {"independent_correct", "fragile_correct", "correct", "wrong", "partial", "uncertain"}:
        raise ReviewLoopError("morning first result invalid")
    if choice_result not in {"correct", "incorrect", "partial", "blank", "uncertain", "not_applicable"}:
        raise ReviewLoopError("morning choice result invalid")
    if reasoning_result not in {"sound", "partial", "diverged", "not_observed"}:
        raise ReviewLoopError("morning reasoning result invalid")
    if result in {"wrong", "partial", "uncertain"}:
        return result, {"fragile_override": False}
    if result == "correct":
        result = "fragile_correct" if choice_result == "correct" and (prompt_level != "none" or reasoning_result != "sound") else "independent_correct"
    if result == "fragile_correct":
        return result, {"fragile_override": True}
    return "independent_correct", {"fragile_override": False}
