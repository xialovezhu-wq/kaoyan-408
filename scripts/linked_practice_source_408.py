"""Portable linked-practice source validation used only by cold curation paths."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class LinkedPracticeSourceError(ValueError):
    pass


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_source(repo: str | Path, locator: object) -> tuple[Path, str]:
    value = str(locator or "").strip()
    if not value or "\x00" in value or value.startswith("file://"):
        raise LinkedPracticeSourceError("linked-practice source locator invalid")
    path = Path(value)
    if not path.is_absolute():
        path = Path(repo) / path
    path = path.resolve()
    try:
        path.relative_to(Path(repo).resolve())
    except ValueError as exc:
        raise LinkedPracticeSourceError("linked-practice source escapes repo") from exc
    if not path.is_file() or path.is_symlink():
        raise LinkedPracticeSourceError("linked-practice source missing")
    return path, path.relative_to(Path(repo).resolve()).as_posix()


def validate_source_for_practice(
    repo: str | Path,
    source_ref: object,
    source_sha256: object,
    *,
    practice_mode: str,
    formal_node_id: str | None = None,
    variant_of_formal_node_id: str | None = None,
) -> dict[str, Any]:
    path, ref = resolve_source(repo, source_ref)
    digest = str(source_sha256 or "")
    if _sha(path) != digest:
        raise LinkedPracticeSourceError("linked-practice source hash mismatch")
    if practice_mode not in {"original", "variant"}:
        raise LinkedPracticeSourceError("linked-practice mode invalid")
    return {
        "path": path,
        "source_ref": ref,
        "source_sha256": digest,
        "practice_mode": practice_mode,
        "formal_node_id": formal_node_id,
        "variant_of_formal_node_id": variant_of_formal_node_id,
    }


def resolve_selection_receipt(repo: str | Path, locator: object) -> tuple[Path, str, dict[str, Any]]:
    path, ref = resolve_source(repo, locator)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LinkedPracticeSourceError("selection receipt unreadable") from exc
    if not isinstance(value, dict):
        raise LinkedPracticeSourceError("selection receipt invalid")
    return path, ref, value


def validate_selection_receipt(
    repo: str | Path,
    selection_ref: object,
    selection_sha256: object,
    *,
    expected: dict[str, Any],
    source_path: Path,
) -> dict[str, Any]:
    path, ref, value = resolve_selection_receipt(repo, selection_ref)
    if _sha(path) != str(selection_sha256 or ""):
        raise LinkedPracticeSourceError("selection receipt hash mismatch")
    for key, expected_value in expected.items():
        if expected_value is not None and value.get(key) != expected_value:
            raise LinkedPracticeSourceError("selection receipt binding mismatch")
    if value.get("source_ref") not in {None, str(source_path), source_path.name, expected.get("source_ref")}:
        raise LinkedPracticeSourceError("selection receipt source mismatch")
    return {"path": path, "selection_receipt_ref": ref, "value": value}
