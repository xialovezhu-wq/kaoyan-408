"""Portable read-only projection of the formal review source surfaces."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


SOURCE_FILES = ("复习单元总表.md", "复习单元节点映射.md", "原题复做轨总表.md")


def build_projection(repo: str | Path, *, as_of: str) -> dict[str, Any]:
    root = Path(repo).resolve()
    missing = [name for name in SOURCE_FILES if not (root / name).is_file()]
    if missing:
        return {"status": "blocked", "missing": missing, "formal_write_count": 0}
    target = root / "wiki/study_vaults/408-full/state/review-loop/hot-state"
    target.mkdir(parents=True, exist_ok=True)
    rows = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in SOURCE_FILES}
    (target / "projection.json").write_text(json.dumps({"schema": "review-hot-projection-v1", "as_of": as_of, "sources": rows, "formal_write_count": 0}, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "built", "as_of": as_of, "source_hashes": rows, "formal_write_count": 0}
