"""Bounded review-loop hot-state projection and exact event lookup."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_STATE_RELATIVE = Path("wiki/study_vaults/408-full/state/review-loop/hot-state")


class HotStateError(RuntimeError):
    pass


def _ledger(repo: Path) -> Path:
    return repo / DEFAULT_STATE_RELATIVE.parent / "events.jsonl"


def bootstrap(repo: str | Path, *, state_dir: str | Path | None = None) -> dict[str, Any]:
    root = Path(repo).resolve()
    target = Path(state_dir) if state_dir is not None else root / DEFAULT_STATE_RELATIVE
    if not target.is_absolute():
        target = root / target
    target.mkdir(parents=True, exist_ok=True)
    ledger = _ledger(root)
    raw = ledger.read_bytes() if ledger.exists() else b""
    manifest = {
        "schema": "review-hot-state-v1",
        "ledger_sha256": hashlib.sha256(raw).hexdigest(),
        "event_count": len([line for line in raw.splitlines() if line.strip()]),
        "formal_write_count": 0,
    }
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "ready", **manifest}


def lookup_event(repo: str | Path, *, event_id: str, state_dir: str | Path | None = None) -> dict[str, Any]:
    root = Path(repo).resolve()
    target = Path(state_dir) if state_dir is not None else root / DEFAULT_STATE_RELATIVE
    if not target.is_absolute():
        target = root / target
    manifest_path = target / "manifest.json"
    ledger = _ledger(root)
    if not manifest_path.is_file() or not ledger.is_file():
        raise HotStateError("review hot state unavailable")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HotStateError("review hot manifest invalid") from exc
    actual = hashlib.sha256(ledger.read_bytes()).hexdigest()
    if manifest.get("ledger_sha256") != actual:
        raise HotStateError("review hot state drifted")
    found = []
    for raw in ledger.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        value = json.loads(raw)
        if isinstance(value, dict) and value.get("event_id") == event_id:
            found.append(value)
    if len(found) > 1:
        raise HotStateError("review event id is not unique")
    return {"status": "found", "event": found[0]} if found else {"status": "missing", "event": None}
