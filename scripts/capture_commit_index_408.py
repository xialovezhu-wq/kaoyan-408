"""Cold rebuild receipts for the portable capture ledger."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable


class CommitIndexError(RuntimeError):
    pass


def cold_rebuild(
    ledger: str | Path,
    state_path: str | Path,
    *,
    state_replayer: Callable[[list[dict[str, Any]]], dict[str, Any]],
) -> dict[str, Any]:
    path, state_file = Path(ledger), Path(state_path)
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise CommitIndexError("capture ledger cannot be rebuilt") from exc
    if not all(isinstance(row, dict) for row in rows):
        raise CommitIndexError("capture ledger row is not object")
    state = state_replayer(rows)
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipts: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("event_type") != "fact_captured":
            continue
        receipt = {
            "schema": "capture-fact-commit-receipt-v1",
            "capture_id": row.get("capture_id"),
            "payload_sha256": row.get("payload_sha256"),
            "event_id": row.get("event_id"),
            "formal_write_count": 0,
        }
        receipt["receipt_sha256"] = hashlib.sha256(
            (json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":"))).encode("utf-8")
        ).hexdigest()
        receipts[str(row.get("event_id"))] = receipt
    return {"schema": "capture-commit-index-v1", "ledger_event_count": len(rows), "receipts": receipts, "formal_write_count": 0}
