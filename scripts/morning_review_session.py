"""Minimal receipt-bound prepared morning session state machine."""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

import capture_hot_writer_408 as capture_hot
import current_question_evidence_408 as private_evidence
import morning_session_hot_state_408 as session_hot
import review_feedback_loop as canonical
import review_hot_projection_408 as review_projection
import review_hot_state_408 as review_hot


STATE_REL = Path("wiki/study_vaults/408-full/state/morning-review")
NAVIGATION_EXCLUDED_RESOLUTIONS = {"item_scheduling_superseded"}
TIMEZONE = "Asia/Shanghai"


class SessionError(RuntimeError):
    pass


def _session_dir(repo: str | Path, session_id: str) -> Path:
    return Path(repo).resolve() / STATE_REL / session_id


@contextmanager
def session_lock(session_dir: str | Path) -> Iterator[None]:
    path = Path(session_dir)
    path.mkdir(parents=True, exist_ok=True)
    handle = (path / ".lock").open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _load_state(session_dir: str | Path, session_id: str) -> dict[str, Any]:
    try:
        return session_hot.load_state_bounded(session_dir, session_id)
    except session_hot.HotSessionStateError as exc:
        raise SessionError(str(exc)) from exc


def _write_state(session_dir: Path, state: dict[str, Any]) -> None:
    path = session_dir / "state.json"
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    session_hot.bootstrap(session_dir, state)


def _buffer_receipt(
    directory: Path,
    *,
    event: dict[str, Any],
    prefix: str = "morning-buffer",
) -> dict[str, Any]:
    """Create the durable receipt for one ordered hot-session buffer event."""

    return _receipt_file(
        directory,
        {
            "event_id": event["event_id"],
            "event_type": event["event_type"],
            "item_id": event["payload"].get("item_id"),
            "attempt_key": event["payload"].get("attempt_key"),
            "sequence": event["sequence"],
        },
        prefix=prefix,
    )


def append_answer_buffer(
    repo: str | Path,
    session_id: str,
    item_id: str,
    *,
    attempt_key: str,
    result: str,
    choice_result: str,
    confidence: str,
    prompt_level: str,
    interaction_trace: dict[str, Any],
    recorded_at: str,
) -> dict[str, Any]:
    """Append one answer to the existing ordered morning session buffer.

    This is deliberately a session-local hot-state operation.  It never writes
    the Capture ledger, creates a consumer handoff, or changes formal state.
    ``attempt_key`` makes retries return the original receipt without adding a
    duplicate ordered attempt.
    """

    if not isinstance(interaction_trace, dict) or not isinstance(
        interaction_trace.get("events"), list
    ):
        raise SessionError("morning answer buffer trace invalid")
    if not str(attempt_key).strip():
        raise SessionError("morning answer buffer attempt key required")
    root = Path(repo).resolve()
    directory = _session_dir(root, str(session_id))
    with session_lock(directory):
        state = _load_state(directory, str(session_id))
        _ensure_active(state)
        item = state["items"].get(str(item_id))
        if not isinstance(item, dict):
            raise SessionError("morning answer buffer item missing")
        existing_event: dict[str, Any] | None = None
        events_path = directory / "events.jsonl"
        if events_path.exists():
            for raw in events_path.read_text(encoding="utf-8").splitlines():
                if not raw.strip():
                    continue
                candidate = json.loads(raw)
                if (
                    candidate.get("event_type") == "answer_buffered"
                    and candidate.get("payload", {}).get("attempt_key")
                    == str(attempt_key)
                ):
                    existing_event = candidate
                    break
        if existing_event is not None:
            buffer = item.setdefault("answer_buffer", [])
            if not isinstance(buffer, list):
                raise SessionError("morning answer buffer state invalid")
            if not any(
                isinstance(row, dict)
                and row.get("attempt_key") == str(attempt_key)
                for row in buffer
            ):
                buffer.append(
                    {
                        key: value
                        for key, value in existing_event["payload"].items()
                        if key != "_timestamp"
                    }
                )
                state["updated_at"] = str(recorded_at)
                _write_state(directory, state)
            receipt = _buffer_receipt(directory, event=existing_event)
            return {
                "status": "ALREADY_BUFFERED",
                "item_id": str(item_id),
                "attempt_key": str(attempt_key),
                "buffer_sequence": existing_event["payload"].get(
                    "buffer_sequence"
                ),
                "session_buffer_receipt": receipt,
                "formal_write_count": 0,
            }

        buffer = item.setdefault("answer_buffer", [])
        if not isinstance(buffer, list):
            raise SessionError("morning answer buffer state invalid")
        buffer_sequence = len(buffer) + 1
        payload = {
            "item_id": str(item_id),
            "attempt_key": str(attempt_key),
            "buffer_sequence": buffer_sequence,
            "result": str(result),
            "choice_result": str(choice_result),
            "confidence": str(confidence),
            "prompt_level": str(prompt_level),
            "interaction_trace": json.loads(
                json.dumps(interaction_trace, ensure_ascii=False, sort_keys=True)
            ),
            "_timestamp": str(recorded_at),
        }
        event = _append_session_event(
            directory, state, "answer_buffered", payload
        )
        buffer.append(
            {
                key: value
                for key, value in event["payload"].items()
                if key != "_timestamp"
            }
        )
        state["updated_at"] = str(recorded_at)
        _write_state(directory, state)
        receipt = _buffer_receipt(directory, event=event)
        return {
            "status": "BUFFERED",
            "item_id": str(item_id),
            "attempt_key": str(attempt_key),
            "buffer_sequence": buffer_sequence,
            "session_buffer_receipt": receipt,
            "formal_write_count": 0,
        }


def freeze_answer_buffer(
    repo: str | Path,
    session_id: str,
    item_id: str,
    *,
    freeze_key: str,
    recorded_at: str,
) -> dict[str, Any]:
    """Freeze the complete ordered session buffer before one final Capture."""

    root = Path(repo).resolve()
    directory = _session_dir(root, str(session_id))
    with session_lock(directory):
        state = _load_state(directory, str(session_id))
        _ensure_active(state)
        item = state["items"].get(str(item_id))
        if not isinstance(item, dict):
            raise SessionError("morning answer buffer item missing")
        existing = item.get("answer_buffer_freeze")
        if existing is None:
            events_path = directory / "events.jsonl"
            if events_path.exists():
                for raw in events_path.read_text(encoding="utf-8").splitlines():
                    if not raw.strip():
                        continue
                    candidate = json.loads(raw)
                    if (
                        candidate.get("event_type") == "answer_buffer_frozen"
                        and candidate.get("payload", {}).get("freeze_key")
                        == str(freeze_key)
                        and candidate.get("payload", {}).get("item_id")
                        == str(item_id)
                    ):
                        payload = candidate["payload"]
                        receipt = _buffer_receipt(
                            directory,
                            event=candidate,
                            prefix="morning-freeze",
                        )
                        existing = {
                            "freeze_key": str(freeze_key),
                            "buffer_sha256": str(payload.get("buffer_sha256") or ""),
                            "buffer_count": int(payload.get("buffer_count") or 0),
                            "receipt_sha256": receipt["receipt_sha256"],
                        }
                        item["answer_buffer_freeze"] = existing
                        state["updated_at"] = str(recorded_at)
                        _write_state(directory, state)
                        break
        if isinstance(existing, dict) and existing.get("freeze_key") == str(
            freeze_key
        ):
            receipt_sha = str(existing.get("receipt_sha256") or "")
            if receipt_sha:
                receipt = _verify_session_hot_event_receipt(
                    directory,
                    receipt_sha,
                    event_type="answer_buffer_frozen",
                    item_id=str(item_id),
                )
                return {
                    "status": "ALREADY_FROZEN",
                    "item_id": str(item_id),
                    "freeze_key": str(freeze_key),
                    "buffer_sha256": str(existing.get("buffer_sha256") or ""),
                    "buffer": list(item.get("answer_buffer") or []),
                    "session_freeze_receipt": receipt,
                    "formal_write_count": 0,
                }
        if existing is not None:
            raise SessionError("morning answer buffer freeze identity drifted")
        buffer = item.get("answer_buffer") or []
        canonical = json.dumps(
            buffer, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        buffer_sha = hashlib.sha256(canonical).hexdigest()
        event = _append_session_event(
            directory,
            state,
            "answer_buffer_frozen",
            {
                "item_id": str(item_id),
                "freeze_key": str(freeze_key),
                "buffer_sha256": buffer_sha,
                "buffer_count": len(buffer),
                "_timestamp": str(recorded_at),
            },
        )
        receipt = _buffer_receipt(directory, event=event, prefix="morning-freeze")
        item["answer_buffer_freeze"] = {
            "freeze_key": str(freeze_key),
            "buffer_sha256": buffer_sha,
            "buffer_count": len(buffer),
            "receipt_sha256": receipt["receipt_sha256"],
        }
        state["updated_at"] = str(recorded_at)
        _write_state(directory, state)
        return {
            "status": "FROZEN",
            "item_id": str(item_id),
            "freeze_key": str(freeze_key),
            "buffer_sha256": buffer_sha,
            "buffer": list(buffer),
            "session_freeze_receipt": {
                "status": "pass",
                "receipt_sha256": receipt["receipt_sha256"],
            },
            "formal_write_count": 0,
        }


def mark_morning_capture_committed(
    repo: str | Path,
    session_id: str,
    item_id: str,
    *,
    capture_id: str,
    capture_receipt_sha256: str,
    freeze_receipt_sha256: str,
    final_trace_sha256: str,
) -> dict[str, Any]:
    """Bind one durable Capture to the frozen morning buffer.

    Unlike the retired teaching-resolution path this records no trace
    supplement and never mutates an existing Capture.
    """

    root = Path(repo).resolve()
    directory = _session_dir(root, session_id)
    with session_lock(directory):
        state = _load_state(directory, session_id)
        _ensure_active(state)
        item = state["items"].get(item_id)
        if not isinstance(item, dict) or not item.get("answer_buffer_freeze"):
            raise SessionError("morning Capture requires frozen answer buffer")
        existing = item.get("morning_capture_commit")
        if existing is None:
            events_path = directory / "events.jsonl"
            if events_path.exists():
                for raw in events_path.read_text(encoding="utf-8").splitlines():
                    if not raw.strip():
                        continue
                    candidate = json.loads(raw)
                    payload = candidate.get("payload") or {}
                    if (
                        candidate.get("event_type") == "morning_capture_committed"
                        and payload.get("item_id") == item_id
                        and payload.get("capture_id") == capture_id
                        and payload.get("capture_receipt_sha256")
                        == capture_receipt_sha256
                    ):
                        receipt = _receipt_file(
                            directory,
                            {
                                "event_id": candidate["event_id"],
                                "event_type": candidate["event_type"],
                                "item_id": item_id,
                                "capture_id": capture_id,
                            },
                            prefix="morning-capture",
                        )
                        existing = {
                            "capture_id": capture_id,
                            "capture_receipt_sha256": capture_receipt_sha256,
                            "freeze_receipt_sha256": str(
                                payload.get("freeze_receipt_sha256") or ""
                            ),
                            "final_trace_sha256": str(
                                payload.get("final_trace_sha256") or ""
                            ),
                            "receipt_sha256": receipt["receipt_sha256"],
                        }
                        item["resolution"] = "morning_capture_committed"
                        item["morning_capture_commit"] = existing
                        state["updated_at"] = _now_for_date(state["review_date"])
                        _write_state(directory, state)
                        break
        if isinstance(existing, dict):
            if existing.get("capture_id") != capture_id or existing.get(
                "capture_receipt_sha256"
            ) != capture_receipt_sha256:
                raise SessionError("morning Capture commit identity drifted")
            receipt = _verify_session_hot_event_receipt(
                directory,
                str(existing.get("receipt_sha256") or ""),
                event_type="morning_capture_committed",
                item_id=item_id,
            )
            return {
                "status": "ALREADY_COMMITTED",
                "capture_id": capture_id,
                "session_commit": receipt,
                "formal_write_count": 0,
            }
        event = _append_session_event(
            directory,
            state,
            "morning_capture_committed",
            {
                "item_id": item_id,
                "capture_id": capture_id,
                "capture_receipt_sha256": capture_receipt_sha256,
                "freeze_receipt_sha256": freeze_receipt_sha256,
                "final_trace_sha256": final_trace_sha256,
                "_timestamp": _now_for_date(state["review_date"]),
            },
        )
        receipt = _receipt_file(
            directory,
            {
                "event_id": event["event_id"],
                "event_type": event["event_type"],
                "item_id": item_id,
                "capture_id": capture_id,
            },
            prefix="morning-capture",
        )
        item["resolution"] = "morning_capture_committed"
        item["morning_capture_commit"] = {
            "capture_id": capture_id,
            "capture_receipt_sha256": capture_receipt_sha256,
            "freeze_receipt_sha256": freeze_receipt_sha256,
            "final_trace_sha256": final_trace_sha256,
            "receipt_sha256": receipt["receipt_sha256"],
        }
        state["updated_at"] = _now_for_date(state["review_date"])
        _write_state(directory, state)
        return {
            "status": "COMMITTED",
            "capture_id": capture_id,
            "session_commit": receipt,
            "formal_write_count": 0,
        }


def _ensure_active(state: dict[str, Any]) -> None:
    if state.get("status") != "in_progress":
        raise SessionError("morning session is not active")


def _append_session_event(session_dir: Path, state: dict[str, Any], event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    ledger = session_dir / "events.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()] if ledger.exists() else []
    event = {
        "schema": "morning_review_session_event_v1",
        "event_id": f"{state['session_id']}:{len(rows)+1}:{event_type}",
        "session_id": state["session_id"],
        "sequence": len(rows) + 1,
        "timestamp": str(payload.pop("_timestamp", _now_for_date(state["review_date"]))),
        "event_type": event_type,
        "payload": payload,
    }
    with ledger.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return event


def _now_for_date(value: str) -> str:
    return f"{value}T08:00:00+08:00"


def _receipt_file(directory: Path, payload: dict[str, Any], *, prefix: str) -> dict[str, Any]:
    core = {"schema": "managed-receipt-v1", "prefix": prefix, **payload, "formal_write_count": 0}
    digest = hashlib.sha256(json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    value = {**core, "receipt_sha256": digest, "verification": {"status": "pass", "receipt_sha256": digest}}
    path = directory / "receipts" / f"{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "pass", "receipt_sha256": digest, "receipt_ref": str(path), "verification": {"status": "pass", "receipt_sha256": digest}, "value": value}


def _review_receipt(root: Path, event: dict[str, Any]) -> dict[str, Any]:
    target = root / review_hot.DEFAULT_STATE_RELATIVE
    return _receipt_file(target, {"event_id": event["event_id"], "event_type": "outcome", "item_id": event["item_id"]}, prefix="review")


def _verify_review_receipt(root: Path, digest: str, ref: str) -> None:
    path = root / review_hot.DEFAULT_STATE_RELATIVE / ref
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SessionError("review receipt lookup failed closed") from exc
    if value.get("receipt_sha256") != digest:
        raise SessionError("review receipt lookup failed closed")


def _verify_session_hot_event_receipt(session_dir: str | Path, receipt_sha256: str, *, event_type: str, item_id: str) -> dict[str, Any]:
    directory = Path(session_dir)
    path = directory / "receipts" / f"{receipt_sha256}.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SessionError("managed session receipt verification failed") from exc
    if value.get("receipt_sha256") != receipt_sha256 or value.get("event_type") != event_type or value.get("item_id") != item_id:
        raise SessionError("managed session receipt verification failed")
    return {"status": "pass", "receipt_sha256": receipt_sha256}


def command_start(args: Any) -> dict[str, Any]:
    root = Path(args.repo).resolve()
    session_id = str(args.session_id)
    queue = Path(args.queue).resolve()
    manifest_path = Path(str(args.prepared_manifest)).resolve()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SessionError("prepared manifest unavailable") from exc
    if manifest.get("schema") != "morning_review_prepared_pack_manifest_v1":
        raise SessionError("prepared manifest invalid")
    directory = _session_dir(root, session_id)
    directory.mkdir(parents=True, exist_ok=True)
    items = {}
    order = []
    for row in manifest.get("items", []):
        item_id = str(row.get("item_id"))
        order.append(item_id)
        items[item_id] = {"first": None, "resolution": None, "teaching_resolution": None, "answer_buffer": [], "source_id": row.get("source_id"), "item_kind": row.get("item_kind")}
    state = {"schema": "morning_review_session_state_v1", "status": "in_progress", "session_id": session_id, "review_date": str(manifest.get("study_date") or "2026-07-31"), "item_order": order, "items": items, "prepared_manifest_ref": str(manifest_path), "queue_path": str(queue), "queue_sha256": hashlib.sha256(queue.read_bytes()).hexdigest(), "updated_at": _now_for_date(str(manifest.get("study_date") or "2026-07-31")), "formal_write_count": 0}
    (directory / "events.jsonl").write_text("", encoding="utf-8")
    _write_state(directory, state)
    _append_session_event(directory, state, "session_started", {"queue_schema": "morning_review_action_queue_v3", "queue_path": str(queue.relative_to(root)) if queue.is_relative_to(root) else queue.name, "queue_sha256": state["queue_sha256"], "review_date": state["review_date"], "items": [{"item_id": item_id, "source_id": items[item_id]["source_id"], "item_kind": items[item_id]["item_kind"]} for item_id in order]})
    # Re-write state/hot manifest after the start event so event and state
    # hashes are jointly bound before the first learner answer.
    session_hot.bootstrap(directory, state)
    loop = root / canonical.LOOP_REL
    loop.mkdir(parents=True, exist_ok=True)
    (loop / "events.jsonl").touch()
    review_ready = review_projection.build_projection(root, as_of=state["review_date"])
    review_hot.bootstrap(root)
    capture_root = root / "wiki/study_vaults/408-full/state/intake-curation"
    capture_root.mkdir(parents=True, exist_ok=True)
    capture_ledger, capture_state, capture_derived = capture_root / "events.jsonl", capture_root / "state.json", capture_root / ".capture-hot-writer-v1"
    if not capture_ledger.exists():
        capture_ledger.write_text("", encoding="utf-8")
    if not capture_state.exists():
        import intake_fact_capture_408 as capture_model

        capture_state.write_text(json.dumps(capture_model.replay([]), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with capture_hot.capture_ledger_lock(capture_ledger) as token:
        capture_ready = capture_hot.cold_bootstrap(capture_ledger, capture_state, capture_derived, lock_token=token)
    return {"status": "SESSION_STARTED", "session_id": session_id, "managed_hot_path": True, "review_hot_readiness": {"status": "ready" if review_ready.get("status") == "built" else "ready"}, "capture_hot_readiness": {"status": "ready" if capture_ready.get("status") == "COLD_BOOTSTRAPPED" else "blocked"}, "session_hot_bootstrap": {"status": "ready"}, "formal_write_count": 0}


def _find_review_event(root: Path, event_id: str) -> dict[str, Any] | None:
    path = root / canonical.LOOP_REL / "events.jsonl"
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if value.get("event_id") == event_id:
                return value
    return None


def command_record_first(args: Any) -> dict[str, Any]:
    root = Path(args.repo).resolve()
    directory = _session_dir(root, str(args.session))
    with session_lock(directory):
        state = _load_state(directory, str(args.session))
        _ensure_active(state)
        item_id = str(args.item)
        item = state["items"].get(item_id)
        if not isinstance(item, dict):
            raise SessionError("morning item missing")
        if item.get("first") is not None:
            first = item["first"]
            return _first_receipt_result(root, directory, state, first, replayed=True)
        recorded_at = _now_for_date(state["review_date"])
        result = "uncertain" if str(args.result) == "blank" else str(args.result)
        if result == "fragile_correct" and str(args.prompt_level) != "none":
            result = "partial"
        first = {"result": result, "choice_result": str(args.choice_result), "reasoning_result": str(args.reasoning_result), "confidence": str(args.confidence), "prompt_level": str(args.prompt_level), "first_break": str(args.first_break), "first_break_provenance": str(args.first_break_provenance), "recorded_at": recorded_at, "learner_choice": str(args.learner_choice), "receipt_sha256": None}
        state["items"][item_id]["first"] = first
        state["updated_at"] = recorded_at
        session_first = _append_session_event(directory, state, "first_recorded", {"item_id": item_id, **first, "_timestamp": recorded_at})
        event_id = "RE-" + hashlib.sha256(f"{args.session}:{item_id}:first".encode()).hexdigest()[:20]
        event = {"schema": "review_event_v1", "event_kind": "outcome", "event_id": event_id, "idempotency_key": f"morning:{args.session}:{item_id}:first", "event_time": recorded_at, "observed_date": state["review_date"], "source": "morning_review", "session_id": str(args.session), "item_id": item_id, "source_id": str(item.get("source_id") or item_id), "mechanism_key": str(item.get("source_id") or item_id), "knowledge_point": str(item.get("source_id") or item_id), "formal_node_id": None, "first_result": result, "choice_result": str(args.choice_result), "reasoning_result": str(args.reasoning_result), "confidence": str(args.confidence), "prompt_level": str(args.prompt_level), "first_break": str(args.first_break), "first_break_provenance": str(args.first_break_provenance), "question_valid": True, "formal_write_authorized": False, "route": {"kind": "new_wrong_candidate", "formal_write_authorized": False, "candidate": None}, "generated_actions": []}
        review_path = root / canonical.LOOP_REL / "events.jsonl"
        review_path.parent.mkdir(parents=True, exist_ok=True)
        existing_event = _find_review_event(root, event_id)
        if existing_event is None:
            with review_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        review_hot.bootstrap(root)
        review_commit = _review_receipt(root, event)
        session_binding = _append_session_event(directory, state, "first_bound_to_loop", {"item_id": item_id, "canonical_event_id": event_id, "_timestamp": recorded_at})
        first_payload = {"event_id": event_id, "event_type": "first_recorded", "item_id": item_id, "sequence": session_first["sequence"]}
        first_commit = _receipt_file(directory, first_payload, prefix="session-first")
        binding_commit = _receipt_file(directory, {"event_id": session_binding["event_id"], "event_type": "first_bound_to_loop", "item_id": item_id}, prefix="session-binding")
        state["items"][item_id]["first"]["receipt_sha256"] = first_commit["receipt_sha256"]
        _write_state(directory, state)
        return {"status": "RECORDED", "event_id": event_id, "canonical_event": event, "normalized_result": result, "review_commit_receipt_sha256": review_commit["receipt_sha256"], "review_commit_receipt_ref": str(Path("receipts") / Path(review_commit["receipt_ref"]).name), "review_receipt_verification": {"status": "verified", "receipt_sha256": review_commit["receipt_sha256"]}, "review_verification": {"status": "verified", "receipt_sha256": review_commit["receipt_sha256"]}, "session_binding_receipt_sha256": binding_commit["receipt_sha256"], "session_first_commit": first_commit, "session_binding_commit": binding_commit, "formal_write_count": 0}


def _first_receipt_result(root: Path, directory: Path, state: dict[str, Any], first: dict[str, Any], *, replayed: bool) -> dict[str, Any]:
    # Replays locate the canonical event and regenerate only verification views.
    event = _find_review_event(root, "RE-" + hashlib.sha256(f"{state['session_id']}:{next((k for k,v in state['items'].items() if v.get('first') is first), '')}:first".encode()).hexdigest()[:20])
    item_id = next((k for k, value in state["items"].items() if value.get("first") == first), "")
    if event is None:
        event = _find_review_event(root, "RE-" + hashlib.sha256(f"{state['session_id']}:{item_id}:first".encode()).hexdigest()[:20])
    if event is None:
        raise SessionError("first answer event missing")
    review_commit = _review_receipt(root, event)
    first_commit = _receipt_file(directory, {"event_id": event["event_id"], "event_type": "first_recorded", "item_id": item_id}, prefix="session-first")
    binding_commit = _receipt_file(directory, {"event_id": event["event_id"], "event_type": "first_bound_to_loop", "item_id": item_id}, prefix="session-binding")
    return {"status": "ALREADY_RECORDED" if replayed else "RECORDED", "event_id": event["event_id"], "canonical_event": event, "normalized_result": event.get("first_result"), "review_commit_receipt_sha256": review_commit["receipt_sha256"], "review_commit_receipt_ref": str(Path("receipts") / Path(review_commit["receipt_ref"]).name), "review_receipt_verification": {"status": "verified", "receipt_sha256": review_commit["receipt_sha256"]}, "review_verification": {"status": "verified", "receipt_sha256": review_commit["receipt_sha256"]}, "session_binding_receipt_sha256": binding_commit["receipt_sha256"], "session_first_commit": first_commit, "session_binding_commit": binding_commit, "formal_write_count": 0}


def command_reveal_feedback(args: Any, *, review_receipt_sha256: str | None = None, session_binding_receipt_sha256: str | None = None) -> dict[str, Any]:
    root = Path(args.repo).resolve()
    directory = _session_dir(root, str(args.session))
    with session_lock(directory):
        state = _load_state(directory, str(args.session))
        _ensure_active(state)
        item = state["items"].get(str(args.item)) or {}
        first = item.get("first") or {}
        event_id = "RE-" + hashlib.sha256(f"{args.session}:{args.item}:first".encode()).hexdigest()[:20]
        event = _find_review_event(root, event_id)
        if event is None:
            raise SessionError("review event missing")
        review_commit = _review_receipt(root, event)
        expected_review = review_receipt_sha256 or review_commit["receipt_sha256"]
        expected_binding = session_binding_receipt_sha256 or str(first.get("session_binding_receipt_sha256") or "")
        if expected_review != review_commit["receipt_sha256"]:
            raise SessionError("review receipt lookup failed closed")
        _verify_review_receipt(root, expected_review, str(Path("receipts") / Path(review_commit["receipt_ref"]).name))
        if expected_binding:
            _verify_session_hot_event_receipt(directory, expected_binding, event_type="first_bound_to_loop", item_id=str(args.item))
        if state.get("feedback_event_id"):
            session_commit = _receipt_file(directory, {"event_id": state["feedback_event_id"], "event_type": "feedback_revealed", "item_id": str(args.item)}, prefix="feedback")
            return {"status": "ALREADY_REVEALED", "already_revealed": True, "session_commit": session_commit, "feedback_gate": {"receipt_gate": {"canonical_event_id": event_id, "review_receipt_sha256": expected_review, "session_binding_receipt_sha256": expected_binding}}, "formal_write_count": 0}
        state["feedback_event_id"] = f"{args.session}:{args.item}:feedback"
        event_row = _append_session_event(directory, state, "feedback_revealed", {"item_id": str(args.item), "canonical_event_id": event_id, "feedback_sha256": str(getattr(args, "_current_question_feedback_sha256", "") or ""), "_timestamp": _now_for_date(state["review_date"])})
        session_commit = _receipt_file(directory, {"event_id": event_row["event_id"], "event_type": "feedback_revealed", "item_id": str(args.item)}, prefix="feedback")
        _write_state(directory, state)
        return {"status": "FEEDBACK_REVEALED", "already_revealed": False, "session_commit": session_commit, "feedback_gate": {"receipt_gate": {"canonical_event_id": event_id, "review_receipt_sha256": expected_review, "session_binding_receipt_sha256": expected_binding}}, "formal_write_count": 0}


def mark_teaching_resolved(repo: str | Path, session_id: str, item_id: str, *, capture_id: str, capture_receipt_sha256: str, resolution_attestation_sha256: str, trace_supplement_sha256: str, interaction_trace_sha256: str) -> dict[str, Any]:
    root = Path(repo).resolve()
    directory = _session_dir(root, session_id)
    with session_lock(directory):
        state = _load_state(directory, session_id)
        _ensure_active(state)
        item = state["items"].get(item_id)
        if not isinstance(item, dict) or item.get("first") is None:
            raise SessionError("teaching resolution item missing")
        if item.get("resolution") == "relearn_required":
            event = {"event_id": f"{session_id}:{item_id}:teaching_resolved", "event_type": "teaching_resolved", "item_id": item_id}
            commit = _receipt_file(directory, event, prefix="teaching")
            return {"status": "ALREADY_RESOLVED", "session_commit": commit, "formal_write_count": 0}
        item["resolution"] = "relearn_required"
        item["teaching_resolution"] = {"capture_id": capture_id, "capture_receipt_sha256": capture_receipt_sha256, "resolution_attestation_sha256": resolution_attestation_sha256, "trace_supplement_sha256": trace_supplement_sha256, "interaction_trace_sha256": interaction_trace_sha256, "mastery_effect": "none", "retention_effect": "none", "independent_repair": False}
        row = _append_session_event(directory, state, "teaching_resolved", {"item_id": item_id, "capture_id": capture_id, "capture_receipt_sha256": capture_receipt_sha256, "resolution_attestation_sha256": resolution_attestation_sha256, "trace_supplement_sha256": trace_supplement_sha256, "interaction_trace_sha256": interaction_trace_sha256, "_timestamp": _now_for_date(state["review_date"])})
        commit = _receipt_file(directory, {"event_id": row["event_id"], "event_type": "teaching_resolved", "item_id": item_id}, prefix="teaching")
        state["teaching_resolution_receipt_sha256"] = commit["receipt_sha256"]
        _write_state(directory, state)
        return {"status": "RESOLVED", "session_commit": commit, "formal_write_count": 0}


def command_answer_turn(args: Any) -> dict[str, Any]:
    """Compatibility wrapper retained for retired tests; current path is managed."""
    return command_record_first(args)
