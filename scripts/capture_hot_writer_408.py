"""Portable append-only hot writer for the 408 fact-capture ledger.

The foreground path uses this module for one bounded fact event.  It does not
own curation, formal writes, queues, or deployment authority; the canonical
``intake_fact_capture_408.replay`` function remains the state projection.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


JOINT_MANIFEST_FILENAME = "joint-manifest.json"


class CaptureHotWriterError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _read_events(ledger: Path) -> list[dict[str, Any]]:
    if not ledger.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in ledger.read_text(encoding="utf-8").splitlines():
        if raw.strip():
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise CaptureHotWriterError("capture ledger row is not an object")
            rows.append(value)
    return rows


def _write_state(ledger: Path, state_path: Path, events: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        import intake_fact_capture_408 as capture_model

        state = capture_model.replay(events)
    except Exception as exc:
        raise CaptureHotWriterError(f"capture state replay failed: {exc}") from exc
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_name(f".{state_path.name}.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, state_path)
    return state


def _write_manifest(ledger: Path, state_path: Path, hot_root: Path, *, status: str = "PASS") -> dict[str, Any]:
    hot_root.mkdir(parents=True, exist_ok=True)
    rows = _read_events(ledger)
    manifest = {
        "schema": "capture-hot-joint-manifest-v1",
        "status": status,
        "ledger_event_count": len(rows),
        "ledger_sha256": hashlib.sha256(ledger.read_bytes()).hexdigest() if ledger.exists() else hashlib.sha256(b"").hexdigest(),
        "state_sha256": hashlib.sha256(state_path.read_bytes()).hexdigest() if state_path.exists() else None,
        "formal_write_count": 0,
    }
    target = hot_root / JOINT_MANIFEST_FILENAME
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


@contextmanager
def capture_ledger_lock(ledger: str | Path) -> Iterator[str]:
    path = Path(ledger)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = (path.parent / ".capture-ledger.lock").open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield "capture-ledger-lock-v1"
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def cold_bootstrap(
    ledger: str | Path,
    state_path: str | Path,
    hot_root: str | Path,
    *,
    lock_token: str | None = None,
    reconcile: bool = False,
) -> dict[str, Any]:
    ledger_path, state_file, derived = Path(ledger), Path(state_path), Path(hot_root)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    if not ledger_path.exists():
        ledger_path.write_text("", encoding="utf-8")
    events = _read_events(ledger_path)
    state = _write_state(ledger_path, state_file, events)
    manifest = _write_manifest(ledger_path, state_file, derived)
    return {
        "status": "COLD_BOOTSTRAPPED",
        "ledger_event_count": len(events),
        "state_event_count": state.get("event_count", len(events)),
        "joint_verification": {"status": "PASS", "manifest_sha256": _sha(manifest)},
        "formal_write_count": 0,
        "reconcile": bool(reconcile),
    }


def canonical_fact_event_from_payload(payload: dict[str, Any], *, created_at: str) -> dict[str, Any]:
    payload_copy = json.loads(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    payload_sha = _sha(payload_copy)
    key = str(payload_copy["idempotency_key"])
    capture_id = f"CAP-{str(payload_copy['study_date']).replace('-', '')}-{hashlib.sha256(key.encode()).hexdigest()[:12]}"
    return {
        "schema": "intake_fact_capture_event_v1",
        "event_type": "fact_captured",
        "event_id": f"CE-{hashlib.sha256(('capture:' + key).encode()).hexdigest()[:20]}",
        "idempotency_key": key,
        "created_at": created_at,
        "capture_id": capture_id,
        "payload_sha256": payload_sha,
        "capture": payload_copy,
    }


def _append_event(ledger: Path, event: dict[str, Any]) -> None:
    with ledger.open("ab") as handle:
        handle.write(_canonical(event) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def capture_fact(
    ledger: str | Path,
    state_path: str | Path,
    hot_root: str | Path,
    *,
    lock_token: str | None = None,
    validated_payload: dict[str, Any],
) -> dict[str, Any]:
    ledger_path, state_file, derived = Path(ledger), Path(state_path), Path(hot_root)
    events = _read_events(ledger_path)
    key = str(validated_payload.get("idempotency_key") or "")
    payload_sha = _sha(validated_payload)
    existing = next((row for row in events if row.get("idempotency_key") == key), None)
    if existing is not None:
        if existing.get("event_type") != "fact_captured" or existing.get("payload_sha256") != payload_sha:
            raise CaptureHotWriterError("idempotency key payload drifted")
        state = _write_state(ledger_path, state_file, events)
        manifest = _write_manifest(ledger_path, state_file, derived)
        capture_id = str(existing.get("capture_id") or "")
        return {
            "status": "ALREADY_COMMITTED",
            "capture_id": capture_id,
            "payload_sha256": payload_sha,
            "receipt_sha256": hashlib.sha256(_canonical(existing)).hexdigest(),
            "quality_status": state.get("captures", {}).get(capture_id, {}).get("quality_status"),
            "formal_write_count": 0,
            "joint_verification": {"status": "PASS", "manifest_sha256": _sha(manifest)},
        }
    event = canonical_fact_event_from_payload(validated_payload, created_at=str(validated_payload.get("event_time") or "1970-01-01T00:00:00+00:00"))
    _append_event(ledger_path, event)
    events.append(event)
    state = _write_state(ledger_path, state_file, events)
    manifest = _write_manifest(ledger_path, state_file, derived)
    return {
        "status": "CAPTURED",
        "capture_id": event["capture_id"],
        "payload_sha256": payload_sha,
        "receipt_sha256": hashlib.sha256(_canonical(event)).hexdigest(),
        "quality_status": state["captures"][event["capture_id"]]["quality_status"],
        "formal_write_count": 0,
        "joint_verification": {"status": "PASS", "manifest_sha256": _sha(manifest)},
    }


def save_neutral_event(
    ledger: str | Path,
    state_path: str | Path,
    hot_root: str | Path,
    *,
    lock_token: str | None = None,
    event: dict[str, Any],
) -> dict[str, Any]:
    ledger_path, state_file, derived = Path(ledger), Path(state_path), Path(hot_root)
    events = _read_events(ledger_path)
    event_id = str(event.get("review_event_id") or "")
    existing = next((row for row in events if row.get("event_type") == "neutral_saved" and row.get("review_event_id") == event_id), None)
    if existing is not None:
        material = {key: value for key, value in existing.items() if key not in {"schema", "event_type", "event_id", "idempotency_key", "created_at", "payload_sha256"}}
        return {"status": "ALREADY_SAVED_NEUTRAL", "created": False, "neutral_material": material, "receipt_sha256": hashlib.sha256(_canonical(existing)).hexdigest(), "formal_write_count": 0}
    _append_event(ledger_path, event)
    events.append(event)
    _write_state(ledger_path, state_file, events)
    manifest = _write_manifest(ledger_path, state_file, derived)
    material = {key: value for key, value in event.items() if key not in {"schema", "event_type", "event_id", "idempotency_key", "created_at", "payload_sha256"}}
    return {"status": "SAVED_NEUTRAL", "created": True, "neutral_material": material, "receipt_sha256": hashlib.sha256(_canonical(event)).hexdigest(), "joint_verification": {"status": "PASS", "manifest_sha256": _sha(manifest)}, "formal_write_count": 0}
