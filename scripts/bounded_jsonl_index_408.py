"""Bounded lookup helper for append-only JSONL ledgers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class BoundedJsonlIndexError(RuntimeError):
    pass


def lookup_event(path: str | Path, index_dir: str | Path, event_id: str) -> dict[str, Any] | None:
    """Resolve one event through the optional index, with a bounded fallback."""

    ledger = Path(path)
    index = Path(index_dir)
    manifest = index / "manifest.json"
    if manifest.is_file():
        try:
            value = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BoundedJsonlIndexError("index manifest unreadable") from exc
        if not isinstance(value, dict):
            raise BoundedJsonlIndexError("index manifest invalid")
        # A portable fixture may carry rows directly; otherwise the ledger is
        # still the source of truth and is scanned once for the requested id.
        rows = value.get("events")
        if isinstance(rows, dict) and event_id in rows:
            row = rows[event_id]
            if isinstance(row, dict) and isinstance(row.get("line"), int):
                target = row["line"]
                for number, raw in enumerate(ledger.read_text(encoding="utf-8").splitlines(), 1):
                    if number == target:
                        parsed = json.loads(raw)
                        return {"event": parsed, "line": number}
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise BoundedJsonlIndexError("ledger unreadable") from exc
    found: list[tuple[int, dict[str, Any]]] = []
    for number, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BoundedJsonlIndexError("ledger row invalid") from exc
        if isinstance(value, dict) and value.get("event_id") == event_id:
            found.append((number, value))
    if len(found) > 1:
        raise BoundedJsonlIndexError("event id is not unique")
    return {"event": found[0][1], "line": found[0][0]} if found else None
