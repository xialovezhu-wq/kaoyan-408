"""Prepared-pack reader and private grader bridge for the morning hot path."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import current_question_context_408 as context_model
import current_question_evidence_408 as private_evidence
import morning_session_hot_state_408 as session_hot


PACK_REL = Path("wiki/study_vaults/408-full/state/morning-review/prepared-pack")
TIMEZONE = "Asia/Shanghai"


class PreparedPackError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _manifest_path(repo: Path, reference: str | Path) -> Path:
    path = Path(reference)
    if not path.is_absolute():
        path = repo / path
    return path.resolve()


def _load_manifest(repo: Path, session_id: str) -> dict[str, Any]:
    try:
        import morning_review_session as session

        state = session._load_state(session._session_dir(repo, session_id), session_id)
    except Exception as exc:
        if isinstance(exc, PreparedPackError):
            raise
        raise PreparedPackError("managed morning session state failed closed") from exc
    reference = state.get("prepared_manifest_ref")
    if not reference:
        raise PreparedPackError("prepared pack binding missing")
    path = _manifest_path(repo, str(reference))
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreparedPackError("prepared pack manifest unavailable") from exc
    if not isinstance(value, dict) or value.get("schema") != "morning_review_prepared_pack_manifest_v1":
        raise PreparedPackError("prepared pack manifest invalid")
    return value


def _validate_item(item: object) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise PreparedPackError("prepared pack item invalid")
    surface = item.get("surface")
    evaluator = item.get("evaluator")
    if not isinstance(surface, dict) or set(surface) != {"stem", "options", "response_instruction"}:
        raise PreparedPackError("prepared surface invalid")
    options = surface.get("options")
    if not isinstance(options, dict) or list(options) != ["A", "B", "C", "D"] or any(not str(options[k]).strip() for k in options):
        raise PreparedPackError("prepared options invalid")
    if not isinstance(evaluator, dict) or str(evaluator.get("correct_option") or "") not in {"A", "B", "C", "D"}:
        raise PreparedPackError("prepared evaluator invalid")
    return item


def draft_hashes(repo: str | Path, queue_path: str | Path, draft_path: str | Path) -> dict[str, Any]:
    queue = Path(queue_path)
    draft = Path(draft_path)
    try:
        value = json.loads(draft.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreparedPackError("prepared draft unavailable") from exc
    if not isinstance(value, dict) or value.get("schema") != "morning_review_prepared_pack_draft_v1":
        raise PreparedPackError("prepared draft schema invalid")
    items = [_validate_item(row) for row in value.get("items", [])]
    if not items:
        raise PreparedPackError("prepared draft has no items")
    return {
        "draft_sha256": _sha_bytes(draft.read_bytes()),
        "queue_sha256": _sha_bytes(queue.read_bytes()),
        "item_hashes": [{"item_id": row["item_id"], "item_sha256": _sha(row)} for row in items],
        "verification_checks_required": ["surface", "evaluator", "evidence", "answer_safety"],
    }


def publish_pack(repo: str | Path, queue_path: str | Path, draft_path: str | Path, verification_path: str | Path) -> dict[str, Any]:
    root = Path(repo).resolve()
    draft = Path(draft_path)
    verification = Path(verification_path)
    hashes = draft_hashes(root, queue_path, draft)
    try:
        draft_value = json.loads(draft.read_text(encoding="utf-8"))
        verify_value = json.loads(verification.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreparedPackError("prepared verification unavailable") from exc
    if verify_value.get("schema") != "morning_review_prepared_pack_verification_v1" or verify_value.get("draft_sha256") != hashes["draft_sha256"]:
        raise PreparedPackError("prepared verification binding invalid")
    checks = {str(k): bool(v) for k, v in (verify_value.get("items") or [{}])[0].get("checks", {}).items()} if verify_value.get("items") else {}
    if checks and not all(checks.values()):
        raise PreparedPackError("prepared verification failed")
    manifest = {
        "schema": "morning_review_prepared_pack_manifest_v1",
        "queue_sha256": hashes["queue_sha256"],
        "draft_sha256": hashes["draft_sha256"],
        "verification_sha256": _sha_bytes(verification.read_bytes()),
        "items": draft_value["items"],
        "study_date": str(draft_value.get("review_date") or "2026-07-31"),
        "formal_write_count": 0,
    }
    target = root / PACK_REL / "manifest.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "published", "manifest_ref": str(target), "manifest_sha256": _sha_bytes(target.read_bytes()), "item_count": len(manifest["items"]), "formal_write_count": 0}


def _item(repo: Path, session_id: str, item_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _load_manifest(repo, session_id)
    row = next((row for row in manifest.get("items", []) if row.get("item_id") == item_id), None)
    if row is None:
        raise PreparedPackError("prepared item not found")
    return _validate_item(row), manifest


def _surface_sha(surface: dict[str, Any]) -> str:
    return _sha(surface)


def _render_surface(surface: dict[str, Any]) -> str:
    lines = [str(surface["stem"]).strip(), ""]
    lines.extend(f"{key}. {str(surface['options'][key]).strip()}" for key in ("A", "B", "C", "D"))
    lines.extend(["", str(surface["response_instruction"]).strip()])
    return "\n".join(lines).strip() + "\n"


def show_item(repo: str | Path, session_id: str, item_id: str) -> dict[str, Any]:
    root = Path(repo).resolve()
    item, manifest = _item(root, session_id, item_id)
    surface = dict(item["surface"])
    return {
        "schema": "morning_review_prepared_item_v1",
        "session_id": session_id,
        "item_id": item_id,
        "source_id": item.get("source_id"),
        "surface": surface,
        "surface_sha256": _surface_sha(surface),
        "answer_safe": True,
        "scheduling_binding": {"item_id": item_id, "source_id": item.get("source_id"), "surface_sha256": _surface_sha(surface), "origin_date": manifest.get("study_date"), "formal_write_count": 0},
        "formal_write_count": 0,
    }


def scheduling_binding_for_item(repo: str | Path, session_id: str, item_id: str) -> dict[str, Any]:
    shown = show_item(repo, session_id, item_id)
    return dict(shown["scheduling_binding"])


def _grade(item: dict[str, Any], choice: str, *, prompt_level: str = "none") -> dict[str, Any]:
    evaluator = item.get("evaluator") or {}
    option = str(choice or "").strip().upper()
    correct = option == str(evaluator.get("correct_option") or "").upper()
    if correct and prompt_level != "none":
        result = "fragile_correct"
    elif correct:
        result = "independent_correct"
    else:
        result = "wrong"
    feedback = (evaluator.get("feedback_by_result") or {}).get(result) or (evaluator.get("feedback_by_result") or {}).get("wrong") or "请根据题面重新核对边界条件。"
    first_break = (evaluator.get("first_break_by_choice") or {}).get(option, "未观察到" if correct else "未观察到")
    return {"choice_result": "correct" if correct else "incorrect", "reasoning_result": "sound" if correct else "diverged", "first_result": result, "feedback_text": str(feedback), "first_break": "未观察到" if correct else str(first_break), "first_break_provenance": "not_observed" if correct else "visible_evidence", "correct_option": str(evaluator.get("correct_option")), "grader_result": "correct" if correct else "wrong", "standard_explanation": str(evaluator.get("decisive_reason") or "分别核对题目条件。"), "grader_basis": str(evaluator.get("decisive_reason") or "分别核对题目条件。"), "provided_by": "prepared_pack_grader", "missing_fields": []}


def open_grading_context(repo: str | Path, session_id: str, item_id: str, choice: str) -> dict[str, Any]:
    item, _ = _item(Path(repo).resolve(), session_id, item_id)
    graded = _grade(item, choice)
    return {"deterministic_choice_result": graded["choice_result"], "first_result": graded["first_result"], "feedback_text": graded["feedback_text"], "correct_option": graded["correct_option"], "formal_write_count": 0, "evaluation_evidence": graded}


def _event_date(event_time: str) -> str:
    parsed = dt.datetime.fromisoformat(event_time)
    return parsed.astimezone(ZoneInfo(TIMEZONE)).date().isoformat()


def prepare_current_turn(
    repo: str | Path,
    *,
    session_id: str,
    item_id: str,
    choice: str,
    confidence: str,
    request_id: str,
    event_time: str,
    display_surface_sha256: str,
    display_receipt_locator: str | None = None,
    prompt_level: str = "none",
    interaction_trace: object | None = None,
    include_private_feedback: bool = True,
    private_root: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(repo).resolve()
    shown = show_item(root, session_id, item_id)
    if str(display_surface_sha256) != shown["surface_sha256"]:
        raise PreparedPackError("display surface hash drifted")
    item, _ = _item(root, session_id, item_id)
    graded = _grade(item, choice, prompt_level=prompt_level)
    capsule_id = "GC-" + hashlib.sha256(f"{session_id}:{item_id}:{shown['surface_sha256']}".encode()).hexdigest()[:24].upper()
    context = {
        "schema": context_model.SCHEMA,
        "source": "morning_review",
        "request_id": request_id,
        "session_id": session_id,
        "item_id": item_id,
        "source_id": str(item.get("source_id") or item_id),
        "source_stable": True,
        "source_binding_sha256": shown["surface_sha256"],
        "grader_capsule_id": capsule_id,
        "event_time": event_time,
        "study_date": _event_date(event_time),
        "timezone": TIMEZONE,
        "idempotency_key": f"{request_id}:first",
        "display_receipt_sha256": (str(display_receipt_locator).removeprefix(private_evidence.TURN_LOCATOR_PREFIX) if display_receipt_locator else hashlib.sha256(f"display:{session_id}:{item_id}:{shown['surface_sha256']}".encode()).hexdigest()),
        "question_valid": True,
        "question_evidence": {
            "public_text": str(shown["surface"]["stem"]),
            "options": [{"label": key, "text": str(shown["surface"]["options"][key])} for key in ("A", "B", "C", "D")],
            "response_instruction": str(shown["surface"]["response_instruction"]),
            "public_surface_sha256": shown["surface_sha256"],
            "attachment_sha256s": [],
        },
        "learner_evidence": {
            "answer_text": str(choice).upper(),
            "choice": str(choice).upper(),
            "confidence": confidence,
            "first_action": None,
            "reasoning": "",
            "prompt_level": prompt_level,
            "observed_at": event_time,
        },
        "assessment": {
            "choice_result": graded["choice_result"],
            "reasoning_result": "sound" if graded["choice_result"] == "correct" else "diverged",
            "confidence": confidence,
            "prompt_level": prompt_level,
            "first_break": graded["first_break"],
            "first_break_provenance": graded["first_break_provenance"],
            "first_action": None,
            "first_action_provenance": None,
        },
        "formal_write_count": 0,
    }
    normalized = context_model.validate_context(context)
    evidence = {
        "grader_capsule_id": capsule_id,
        "correct_answer": graded["correct_option"],
        "correct_option": graded["correct_option"],
        "standard_explanation": graded["standard_explanation"],
        "grader_result": graded["grader_result"],
        "grader_basis": graded["grader_basis"],
        "provided_by": graded["provided_by"],
        "missing_fields": [],
    }
    capsule = private_evidence.publish_evaluation_capsule({
        "schema_version": private_evidence.EVALUATION_CAPSULE_SCHEMA,
        "capsule_id": capsule_id,
        "session_id": session_id,
        "item_id": item_id,
        "source_id": normalized["source_id"],
        "source_stable": True,
        "source_binding_sha256": shown["surface_sha256"],
        "context_id": normalized["context_id"],
        "evaluation_evidence": evidence,
        "formal_write_count": 0,
    }, private_root=private_root)
    return {
        "context": context,
        "context_id": normalized["context_id"],
        "controlled_assessment": {"choice_result": graded["choice_result"], "first_result": graded["first_result"], "formal_write_count": 0},
        "grader_capsule_locator": capsule["locator"],
        "grader_capsule_sha256": capsule["sha256"],
        "scheduling_binding": shown["scheduling_binding"],
        "scheduling_binding_status": "verified",
        "_private_frozen_feedback_text": graded["feedback_text"],
        "_private_interaction_trace": context_model.normalize_interaction_trace(interaction_trace),
        "formal_write_count": 0,
    }


def prepare_followup_attempt_from_display(
    repo: str | Path,
    *,
    display_receipt_locator: str,
    choice: str,
    confidence: str,
    prompt_level: str,
    private_root: str | Path | None = None,
) -> dict[str, Any]:
    display_sha = display_receipt_locator.removeprefix(private_evidence.TURN_LOCATOR_PREFIX)
    lifecycle = private_evidence.read_answer_display_lifecycle(display_sha, private_root=private_root)
    if lifecycle is None or lifecycle.get("status") not in {"first_recorded", "resolved"}:
        raise PreparedPackError("current display lifecycle unavailable")
    operation_id = lifecycle.get("first_operation_id")
    operation = private_evidence.read_answer_operation(str(operation_id), private_root=private_root)
    if operation is None:
        raise PreparedPackError("current answer operation unavailable")
    context = (operation.get("prepared") or {}).get("context") or {}
    session_id, item_id = str(context.get("session_id")), str(context.get("item_id"))
    item, _ = _item(Path(repo).resolve(), session_id, item_id)
    graded = _grade(item, choice, prompt_level=prompt_level)
    return {"session_id": session_id, "item_id": item_id, "choice_result": graded["choice_result"], "first_result": graded["first_result"], "feedback_text": graded["feedback_text"], "evidence_manifest_sha256": lifecycle.get("first_turn", {}).get("evidence_manifest_sha256"), "formal_write_count": 0}


def prepared_feedback_for_item(repo: str | Path, session_id: str, item_id: str, choice: str = "") -> str:
    """Compatibility seam for retired prepared-feedback callers.

    The managed current-question path uses the frozen grader capsule directly;
    this function exists only so isolation tests can prove it is not called.
    """

    item, _ = _item(Path(repo).resolve(), session_id, item_id)
    return str(_grade(item, choice).get("feedback_text") or "")
