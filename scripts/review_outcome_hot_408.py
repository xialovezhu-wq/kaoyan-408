"""Current-question review outcome writer with no formal projection reads."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import review_feedback_loop as canonical
import review_hot_state_408


class HotOutcomeError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _receipt(root: Path, event: dict[str, Any]) -> dict[str, Any]:
    receipt_core = {
        "schema": "review-hot-commit-receipt-v1",
        "event_id": event["event_id"],
        "event_sha256": hashlib.sha256(_canonical(event)).hexdigest(),
        "formal_write_count": 0,
    }
    digest = hashlib.sha256(_canonical(receipt_core)).hexdigest()
    receipt = {**receipt_core, "receipt_sha256": digest}
    relative = Path("receipts") / f"{digest}.json"
    target = root / review_hot_state_408.DEFAULT_STATE_RELATIVE / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return {"sha256": digest, "ref": relative.as_posix(), "value": receipt}


def _formal_context(*args: Any, **kwargs: Any) -> None:
    raise HotOutcomeError("formal context is not used by current-question hot path")


def record_outcome_hot(
    repo: str | Path,
    *,
    source: str,
    session_id: str,
    item_id: str,
    source_id: str,
    mechanism_key: str,
    knowledge_point: str,
    first_result: str,
    choice_result: str,
    reasoning_result: str,
    confidence: str,
    prompt_level: str,
    first_break: str,
    first_break_provenance: str,
    event_time: str,
    observed_date: str,
    idempotency_key: str,
    formal_node_id: str | None = None,
    review_unit_id: str | None = None,
    fragile_override: bool = False,
    managed_user_reply_binding_sha256: str | None = None,
    presentation_kind: str = "current_question",
    current_question_minimal: bool = True,
) -> dict[str, Any]:
    if source not in {"daily_practice", "evening_d0"}:
        raise HotOutcomeError("ordinary source required")
    root = Path(repo).resolve()
    loop = root / canonical.LOOP_REL
    loop.mkdir(parents=True, exist_ok=True)
    ledger = loop / "events.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()] if ledger.exists() else []
    existing = next((row for row in rows if row.get("idempotency_key") == idempotency_key), None)
    if existing is not None:
        receipt = _receipt(root, existing)
        return {
            "status": "ALREADY_COMMITTED",
            "event_id": existing["event_id"],
            "canonical_event": existing,
            "commit_receipt_sha256": receipt["sha256"],
            "commit_receipt_ref": receipt["ref"],
            "verification": {"status": "verified", "receipt_sha256": receipt["sha256"]},
            "formal_write_count": 0,
        }
    event_id = "RE-" + hashlib.sha256(("review:" + idempotency_key).encode()).hexdigest()[:20]
    event = {
        "schema": "review_event_v1",
        "event_kind": "outcome",
        "event_id": event_id,
        "idempotency_key": idempotency_key,
        "event_time": event_time,
        "observed_date": observed_date,
        "source": source,
        "session_id": session_id,
        "item_id": item_id,
        "source_id": source_id,
        "mechanism_key": mechanism_key,
        "knowledge_point": knowledge_point,
        "formal_node_id": formal_node_id,
        "mapped_formal_node_ids": [formal_node_id] if formal_node_id else [],
        "review_unit_id": review_unit_id,
        "first_result": first_result,
        "choice_result": choice_result,
        "reasoning_result": reasoning_result,
        "confidence": confidence,
        "hint_used": prompt_level != "none",
        "prompt_level": prompt_level,
        "first_break": first_break,
        "first_break_provenance": first_break_provenance,
        "question_valid": True,
        "formal_write_authorized": False,
        "route": {"kind": "new_wrong_candidate", "formal_write_authorized": False, "candidate": None},
        "generated_actions": [],
        "presentation_kind": presentation_kind,
        "current_question_minimal": current_question_minimal,
        "fragile_override": fragile_override,
        "managed_user_reply_binding_sha256": managed_user_reply_binding_sha256,
    }
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    receipt = _receipt(root, event)
    review_hot_state_408.bootstrap(root)
    return {
        "status": "COMMITTED",
        "event_id": event_id,
        "canonical_event": event,
        "commit_receipt_sha256": receipt["sha256"],
        "commit_receipt_ref": receipt["ref"],
        "verification": {"status": "verified", "receipt_sha256": receipt["sha256"]},
        "formal_write_count": 0,
    }
