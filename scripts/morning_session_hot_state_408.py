"""Hash-bound session hot state for prepared morning review."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_HOT_DIRNAME = ".morning-session-hot-v1"


class HotSessionStateError(RuntimeError):
    pass


def bootstrap(session_dir: str | Path, state: dict[str, Any]) -> dict[str, Any]:
    directory = Path(session_dir)
    hot = directory / DEFAULT_HOT_DIRNAME
    hot.mkdir(parents=True, exist_ok=True)
    state_path = directory / "state.json"
    event_path = directory / "events.jsonl"
    manifest = {
        "schema": "morning-session-hot-state-v1",
        "state_sha256": hashlib.sha256(state_path.read_bytes()).hexdigest(),
        "events_sha256": hashlib.sha256(event_path.read_bytes()).hexdigest() if event_path.exists() else hashlib.sha256(b"").hexdigest(),
        "formal_write_count": 0,
    }
    (hot / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "ready", **manifest}


def load_state_bounded(session_dir: str | Path, session_id: str | None = None) -> dict[str, Any]:
    directory = Path(session_dir)
    state_path = directory / "state.json"
    hot = directory / DEFAULT_HOT_DIRNAME
    manifest_path = hot / "manifest.json"
    if not state_path.is_file() or not manifest_path.is_file() or state_path.is_symlink() or manifest_path.is_symlink():
        raise HotSessionStateError("managed morning session state failed closed")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HotSessionStateError("managed morning session state failed closed") from exc
    if hashlib.sha256(state_path.read_bytes()).hexdigest() != manifest.get("state_sha256"):
        raise HotSessionStateError("managed morning session state failed closed")
    event_path = directory / "events.jsonl"
    actual_events = hashlib.sha256(event_path.read_bytes()).hexdigest() if event_path.exists() else hashlib.sha256(b"").hexdigest()
    if actual_events != manifest.get("events_sha256"):
        raise HotSessionStateError("managed morning session state failed closed")
    if not isinstance(state, dict) or state.get("formal_write_count", 0) != 0:
        raise HotSessionStateError("managed morning session state failed closed")
    return state
