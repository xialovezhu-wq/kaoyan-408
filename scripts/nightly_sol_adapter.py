#!/usr/bin/env python3
"""Thin Study Intake V2 adapter for canonical CS408 daily curation.

The adapter validates an already frozen outer Capture set and wraps the
native receipt.  It never scans for more captures and never interprets or
writes 408 formal fields.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "study-intake-subject-sol-adapter-invocation-v1"
RESULT_SCHEMA = "study-intake-nightly-sol-adapter-result-v1"
BATCH_SCHEMA = "study-intake-nightly-sol-batch-v1"
SUBJECT = "cs408"
ADAPTER_NAME = "CS408NightlySolAdapter"
SKILL_NAME = "kaoyan-408-daily-intake-curation"
REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = REPO_ROOT / "codex-skill-sources" / SKILL_NAME / "SKILL.md"
NATIVE_ENTRYPOINTS = (
    "scripts/intake_fact_capture_408.py",
    "scripts/sol_curation_decision_408.py",
    "scripts/intake_batch_apply_408.py",
    "scripts/intake_apply_engine_408.py",
)
HARD_CONFLICT_KINDS = {
    "ambiguous_formal_target",
    "immutable_source_conflict",
    "material_formal_choice",
}


class AdapterError(ValueError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _date(value: Any) -> str:
    text = str(value or "")
    try:
        if dt.date.fromisoformat(text).isoformat() != text:
            raise ValueError
    except ValueError as exc:
        raise AdapterError("capture_intake_date_invalid") from exc
    return text


def _sha(value: Any, code: str) -> str:
    text = str(value or "")
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise AdapterError(code)
    return text


def validate_batch(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version", "batch_id", "subject", "capture_intake_date",
        "capture_ids", "capture_set_sha256", "analysis_packages", "skill",
        "status", "formal_write_count",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise AdapterError("nightly_batch_shape_invalid")
    if value.get("schema_version") != BATCH_SCHEMA or value.get("subject") != SUBJECT:
        raise AdapterError("nightly_batch_subject_invalid")
    if value.get("status") != "frozen" or value.get("formal_write_count") != 0:
        raise AdapterError("nightly_batch_state_invalid")
    capture_ids = value.get("capture_ids")
    if (
        not isinstance(capture_ids, list)
        or not capture_ids
        or capture_ids != sorted(set(capture_ids))
        or any(not isinstance(item, str) or not item for item in capture_ids)
    ):
        raise AdapterError("nightly_batch_capture_ids_invalid")
    expected_set_sha = sha256_value(capture_ids)
    if _sha(value.get("capture_set_sha256"), "capture_set_sha256_invalid") != expected_set_sha:
        raise AdapterError("capture_set_sha256_invalid")
    packages = value.get("analysis_packages")
    if not isinstance(packages, list) or len(packages) != len(capture_ids):
        raise AdapterError("analysis_package_set_invalid")
    package_ids: list[str] = []
    for row in packages:
        if not isinstance(row, Mapping) or set(row) != {
            "capture_id", "package_ref", "package_sha256"
        }:
            raise AdapterError("analysis_package_set_invalid")
        package_ids.append(str(row.get("capture_id") or ""))
        _sha(row.get("package_sha256"), "analysis_package_sha256_invalid")
        if not str(row.get("package_ref") or "").startswith(
            "study-intake-analysis-package://sha256/"
        ):
            raise AdapterError("analysis_package_ref_invalid")
    if package_ids != capture_ids:
        raise AdapterError("analysis_package_set_invalid")
    skill = value.get("skill")
    if not isinstance(skill, Mapping) or set(skill) != {
        "name", "source_sha256", "declared_version"
    }:
        raise AdapterError("skill_binding_invalid")
    expected_skill_sha = sha256_file(SKILL_PATH)
    if skill.get("name") != SKILL_NAME or skill.get("source_sha256") != expected_skill_sha:
        raise AdapterError("skill_binding_invalid")
    result = dict(value)
    result["capture_intake_date"] = _date(value.get("capture_intake_date"))
    return result


def prepare_invocation(batch: Mapping[str, Any]) -> dict[str, Any]:
    checked = validate_batch(batch)
    for relative in NATIVE_ENTRYPOINTS:
        if not (REPO_ROOT / relative).is_file():
            raise AdapterError("native_entrypoint_missing")
    core = {
        "schema_version": SCHEMA,
        "adapter_name": ADAPTER_NAME,
        "subject": SUBJECT,
        "batch_id": checked["batch_id"],
        "capture_intake_date": checked["capture_intake_date"],
        "capture_ids": checked["capture_ids"],
        "capture_set_sha256": checked["capture_set_sha256"],
        "analysis_packages": checked["analysis_packages"],
        "skill": checked["skill"],
        "native_entrypoints": list(NATIVE_ENTRYPOINTS),
        "selection_policy": "outer_frozen_set_only",
        "formal_write_count": 0,
    }
    return {**core, "invocation_sha256": sha256_value(core)}


def wrap_native_receipt(
    batch: Mapping[str, Any], native_receipt: Mapping[str, Any]
) -> dict[str, Any]:
    checked = validate_batch(batch)
    required = {
        "schema_version", "subject", "batch_id", "status", "capture_results",
        "changed_files", "formal_write_count", "conflict",
    }
    if not isinstance(native_receipt, Mapping) or set(native_receipt) != required:
        raise AdapterError("native_receipt_shape_invalid")
    status = str(native_receipt.get("status") or "")
    if (
        native_receipt.get("subject") != SUBJECT
        or native_receipt.get("batch_id") != checked["batch_id"]
        or status not in {"committed", "noop", "partial", "awaiting_user", "failed"}
        or isinstance(native_receipt.get("formal_write_count"), bool)
        or not isinstance(native_receipt.get("formal_write_count"), int)
        or int(native_receipt["formal_write_count"]) < 0
        or not isinstance(native_receipt.get("changed_files"), list)
        or not isinstance(native_receipt.get("capture_results"), list)
    ):
        raise AdapterError("native_receipt_invalid")
    conflict = native_receipt.get("conflict")
    conflict_id = None
    if status == "awaiting_user":
        if (
            not isinstance(conflict, Mapping)
            or conflict.get("kind") not in HARD_CONFLICT_KINDS
            or conflict.get("capture_id") not in checked["capture_ids"]
        ):
            raise AdapterError("hard_conflict_invalid")
        conflict_id = "CONFLICT-" + sha256_value(
            {
                "subject": SUBJECT,
                "batch_id": checked["batch_id"],
                "capture_id": conflict["capture_id"],
                "kind": conflict["kind"],
            }
        )[:24].upper()
    elif conflict is not None:
        raise AdapterError("hard_conflict_invalid")
    status_map = {
        "committed": "complete",
        "noop": "noop",
        "partial": "partial",
        "awaiting_user": "awaiting_user",
        "failed": "failed",
    }
    receipt = dict(native_receipt)
    return {
        "schema_version": RESULT_SCHEMA,
        "adapter_name": ADAPTER_NAME,
        "subject": SUBJECT,
        "batch_id": checked["batch_id"],
        "status": status_map[status],
        "native_receipt": receipt,
        "native_receipt_sha256": sha256_value(receipt),
        "conflict_id": conflict_id,
        "formal_write_count": int(receipt["formal_write_count"]),
    }


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AdapterError("json_object_required")
    return value


def _write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(canonical_bytes(value)); handle.flush(); os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        try:
            Path(name).unlink()
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "wrap"):
        command = sub.add_parser(name)
        command.add_argument("--batch", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        if name == "wrap":
            command.add_argument("--native-receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    batch = _load(args.batch)
    result = (
        prepare_invocation(batch)
        if args.command == "prepare"
        else wrap_native_receipt(batch, _load(args.native_receipt))
    )
    _write(args.output, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
