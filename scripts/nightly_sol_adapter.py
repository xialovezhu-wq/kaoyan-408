#!/usr/bin/env python3
"""Thin Study Intake V2 adapter for canonical CS408 daily curation.

The adapter validates an already frozen outer Capture set and wraps the
native receipt.  It never scans for more captures and never interprets or
writes 408 formal fields.
"""

from __future__ import annotations

import argparse
import datetime as dt
import copy
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "study-intake-subject-sol-adapter-invocation-v1"
RESULT_SCHEMA = "study-intake-nightly-sol-adapter-result-v1"
BATCH_SCHEMA = "study-intake-nightly-sol-batch-v2"
AUTHORIZATION_SCHEMA = "study-intake-nightly-command-authorization-v1"
NATIVE_TERMINAL_SCHEMA = "cs408-daily-curation-native-terminal-v1"
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
ABSOLUTE_COMMAND = re.compile(r"^开始 (\d{4}-\d{2}-\d{2}) 408正式入库$")
FORMAL_ID = re.compile(r"^(?:DS|CO|OS|CN)_(?:\d{4}|UNK)_\d{3}$")
AUTHORIZATION_ID = re.compile(r"^NAUTH-[A-F0-9]{24}$")
BATCH_ID = re.compile(r"^NIGHTLY-[A-F0-9]{28}$")
CONFLICT_ID = re.compile(r"^CONFLICT-[A-F0-9]{24}$")
NATIVE_STATUSES = {"complete", "noop", "partial", "awaiting_user", "failed"}
TERMINAL_OUTCOMES = {"curated", "already_current", "needs_user", "failed"}
EXECUTION_EVIDENCE_FIELDS = {
    "pre_state_sha256", "post_state_sha256", "operations", "adapter_run_id",
    "pid", "transaction_id", "ended_at", "stopped_at", "exit_code",
}


class AdapterError(ValueError):
    pass


class _FrozenList(list):
    """JSON-compatible recursive immutable list for the native handoff."""

    def _readonly(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("native invocation is immutable")

    __setitem__ = __delitem__ = append = extend = insert = pop = remove = clear = sort = reverse = _readonly
    __iadd__ = __imul__ = _readonly

    def __deepcopy__(self, _memo: dict[int, Any]) -> "_FrozenList":
        return self


class _FrozenDict(dict):
    """JSON-compatible recursive immutable mapping for the native handoff."""

    def _readonly(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("native invocation is immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _readonly
    __ior__ = _readonly

    def __deepcopy__(self, _memo: dict[int, Any]) -> "_FrozenDict":
        return self


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _FrozenDict({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return _FrozenList(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return _FrozenList(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


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
    if not isinstance(value, str):
        raise AdapterError("capture_intake_date_invalid")
    text = value
    try:
        if dt.date.fromisoformat(text).isoformat() != text:
            raise ValueError
    except ValueError as exc:
        raise AdapterError("capture_intake_date_invalid") from exc
    return text


def _sha(value: Any, code: str) -> str:
    text = value
    if not isinstance(text, str) or len(text) != 64 or any(
        char not in "0123456789abcdef" for char in text
    ):
        raise AdapterError(code)
    return text


def _nonempty_text(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise AdapterError(code)
    return value


def _command_sha256(command: str) -> str:
    return hashlib.sha256(command.encode("utf-8")).hexdigest()


def _validate_authorization(value: Any, capture_intake_date: str) -> dict[str, str]:
    required = {
        "schema_version", "subject", "capture_intake_date", "normalized_command",
        "command_sha256", "authorization_id",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise AdapterError("nightly_authorization_shape_invalid")
    if value.get("schema_version") != AUTHORIZATION_SCHEMA:
        raise AdapterError("nightly_authorization_schema_invalid")
    if value.get("subject") != SUBJECT:
        raise AdapterError("nightly_authorization_subject_invalid")
    if value.get("capture_intake_date") != capture_intake_date:
        raise AdapterError("nightly_authorization_date_invalid")
    normalized_command = value.get("normalized_command")
    if (
        not isinstance(normalized_command, str)
        or normalized_command != normalized_command.strip()
    ):
        raise AdapterError("nightly_authorization_command_invalid")
    match = ABSOLUTE_COMMAND.fullmatch(normalized_command)
    if match is None or match.group(1) != capture_intake_date:
        raise AdapterError("nightly_authorization_command_invalid")
    _date(match.group(1))
    command_sha = _sha(
        value.get("command_sha256"),
        "nightly_authorization_command_sha256_invalid",
    )
    if command_sha != _command_sha256(normalized_command):
        raise AdapterError("nightly_authorization_command_sha256_invalid")
    authorization_id = _nonempty_text(
        value.get("authorization_id"), "nightly_authorization_id_invalid"
    )
    if AUTHORIZATION_ID.fullmatch(authorization_id) is None:
        raise AdapterError("nightly_authorization_id_invalid")
    core = {
        "schema_version": AUTHORIZATION_SCHEMA,
        "subject": SUBJECT,
        "capture_intake_date": capture_intake_date,
        "normalized_command": normalized_command,
        "command_sha256": command_sha,
    }
    if authorization_id != "NAUTH-" + sha256_value(core)[:24].upper():
        raise AdapterError("nightly_authorization_id_invalid")
    return {**core, "authorization_id": authorization_id}


def validate_batch(value: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "schema_version", "batch_id", "subject", "capture_intake_date",
        "capture_ids", "capture_set_sha256", "analysis_packages", "skill",
        "authorization", "status", "formal_write_count",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise AdapterError("nightly_batch_shape_invalid")
    if value.get("schema_version") != BATCH_SCHEMA or value.get("subject") != SUBJECT:
        raise AdapterError("nightly_batch_subject_invalid")
    capture_intake_date = _date(value.get("capture_intake_date"))
    if (
        value.get("status") != "frozen"
        or isinstance(value.get("formal_write_count"), bool)
        or value.get("formal_write_count") != 0
    ):
        raise AdapterError("nightly_batch_state_invalid")
    batch_id = _nonempty_text(value.get("batch_id"), "nightly_batch_id_invalid")
    if BATCH_ID.fullmatch(batch_id) is None:
        raise AdapterError("nightly_batch_id_invalid")
    capture_ids = value.get("capture_ids")
    if (
        not isinstance(capture_ids, list)
        or not capture_ids
        or any(not isinstance(item, str) or not item for item in capture_ids)
        or len(capture_ids) != len(set(capture_ids))
        or capture_ids != sorted(capture_ids)
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
        package_id = row.get("capture_id")
        package_sha = _sha(row.get("package_sha256"), "analysis_package_sha256_invalid")
        package_ref = row.get("package_ref")
        if (
            not isinstance(package_id, str)
            or not package_id
            or not isinstance(package_ref, str)
            or package_ref
            != "study-intake-analysis-package://sha256/" + package_sha
        ):
            raise AdapterError("analysis_package_ref_invalid")
        package_ids.append(package_id)
    if package_ids != capture_ids:
        raise AdapterError("analysis_package_set_invalid")
    skill = value.get("skill")
    if not isinstance(skill, Mapping) or set(skill) != {
        "name", "source_sha256", "declared_version"
    }:
        raise AdapterError("skill_binding_invalid")
    expected_skill_sha = sha256_file(SKILL_PATH)
    skill_sha = _sha(skill.get("source_sha256"), "skill_source_sha256_invalid")
    if skill.get("name") != SKILL_NAME or skill_sha != expected_skill_sha:
        raise AdapterError("skill_binding_invalid")
    result = copy.deepcopy(dict(value))
    result["capture_intake_date"] = capture_intake_date
    result["authorization"] = _validate_authorization(
        value.get("authorization"), capture_intake_date
    )
    return result


def _validate_resolution(
    value: Any, checked: Mapping[str, Any]
) -> dict[str, str]:
    required = {
        "conflict_id", "batch_id", "subject", "conflicted_capture_id",
        "user_option", "skill_name", "skill_source_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise AdapterError("nightly_resolution_shape_invalid")
    conflict_id = value.get("conflict_id")
    if not isinstance(conflict_id, str) or CONFLICT_ID.fullmatch(conflict_id) is None:
        raise AdapterError("nightly_resolution_conflict_id_invalid")
    if value.get("batch_id") != checked.get("batch_id"):
        raise AdapterError("nightly_resolution_batch_binding_invalid")
    if value.get("subject") != SUBJECT:
        raise AdapterError("nightly_resolution_subject_binding_invalid")
    capture_id = value.get("conflicted_capture_id")
    if capture_id not in checked.get("capture_ids", []):
        raise AdapterError("nightly_resolution_capture_binding_invalid")
    if not isinstance(value.get("user_option"), str) or not value.get("user_option"):
        raise AdapterError("nightly_resolution_user_option_invalid")
    if value.get("skill_name") != SKILL_NAME:
        raise AdapterError("nightly_resolution_skill_binding_invalid")
    skill_sha = _sha(
        value.get("skill_source_sha256"),
        "nightly_resolution_skill_sha256_invalid",
    )
    if skill_sha != checked["skill"]["source_sha256"]:
        raise AdapterError("nightly_resolution_skill_binding_invalid")
    return {
        "conflict_id": conflict_id,
        "batch_id": checked["batch_id"],
        "subject": SUBJECT,
        "conflicted_capture_id": capture_id,
        "user_option": value["user_option"],
        "skill_name": SKILL_NAME,
        "skill_source_sha256": skill_sha,
    }


def _validate_selection(
    checked: Mapping[str, Any],
    capture_ids: Any,
    resolution: Mapping[str, str] | None,
) -> list[str]:
    full = list(checked["capture_ids"])
    if resolution is not None:
        expected = [resolution["conflicted_capture_id"]]
        if capture_ids is None:
            return expected
        if capture_ids != expected:
            raise AdapterError("nightly_resolution_capture_scope_invalid")
        return list(capture_ids)
    if capture_ids is None:
        return full
    if capture_ids != full:
        raise AdapterError("nightly_batch_capture_scope_invalid")
    return list(capture_ids)


def prepare_invocation(
    batch: Mapping[str, Any],
    *,
    capture_ids: list[str] | None = None,
    resolution: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    checked = validate_batch(batch)
    checked_resolution = (
        _validate_resolution(resolution, checked) if resolution is not None else None
    )
    selected_ids = _validate_selection(checked, capture_ids, checked_resolution)
    for relative in NATIVE_ENTRYPOINTS:
        if not (REPO_ROOT / relative).is_file():
            raise AdapterError("native_entrypoint_missing")
    selected_packages = [
        row for row in checked["analysis_packages"]
        if row["capture_id"] in selected_ids
    ]
    core = {
        "schema_version": SCHEMA,
        "adapter_name": ADAPTER_NAME,
        "subject": SUBJECT,
        "batch_id": checked["batch_id"],
        "capture_intake_date": checked["capture_intake_date"],
        "capture_ids": selected_ids,
        "capture_set_sha256": checked["capture_set_sha256"],
        "selected_capture_set_sha256": sha256_value(selected_ids),
        "analysis_packages": selected_packages,
        "skill": checked["skill"],
        "authorization": checked["authorization"],
        "native_entrypoints": list(NATIVE_ENTRYPOINTS),
        "selection_policy": (
            "conflicted_capture_only"
            if checked_resolution is not None
            else "outer_frozen_set_only"
        ),
        "execution_mode": (
            "conflict_recovery" if checked_resolution is not None else "full_batch"
        ),
        "resolution": checked_resolution,
        "formal_write_count": 0,
    }
    return _freeze({**core, "invocation_sha256": sha256_value(core)})


def _parsed_timestamp(value: Any, code: str) -> dt.datetime:
    if not isinstance(value, str) or not value:
        raise AdapterError(code)
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise AdapterError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AdapterError(code)
    return parsed


def _validate_execution_evidence(value: Any, status: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != EXECUTION_EVIDENCE_FIELDS:
        raise AdapterError("execution_evidence_shape_invalid")
    _sha(value.get("pre_state_sha256"), "execution_pre_state_sha256_invalid")
    _sha(value.get("post_state_sha256"), "execution_post_state_sha256_invalid")
    if not isinstance(value.get("operations"), list):
        raise AdapterError("execution_operations_invalid")
    try:
        canonical_bytes(value["operations"])
    except (TypeError, ValueError) as exc:
        raise AdapterError("execution_operations_invalid") from exc
    _nonempty_text(value.get("adapter_run_id"), "execution_adapter_run_id_invalid")
    pid = value.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise AdapterError("execution_pid_invalid")
    _nonempty_text(value.get("transaction_id"), "execution_transaction_id_invalid")
    ended_at = _parsed_timestamp(value.get("ended_at"), "execution_ended_at_invalid")
    stopped_at = _parsed_timestamp(
        value.get("stopped_at"), "execution_stopped_at_invalid"
    )
    if ended_at > stopped_at:
        raise AdapterError("execution_timestamp_order_invalid")
    exit_code = value.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise AdapterError("execution_exit_code_invalid")
    if status in {"complete", "noop", "partial", "awaiting_user"} and exit_code != 0:
        raise AdapterError("execution_success_exit_code_invalid")
    if status == "failed" and exit_code == 0:
        raise AdapterError("execution_failure_exit_code_invalid")
    return copy.deepcopy(dict(value))


def _conflict_id(
    checked: Mapping[str, Any], *, capture_id: str, kind: str
) -> str:
    return "CONFLICT-" + sha256_value(
        {
            "schema_version": NATIVE_TERMINAL_SCHEMA,
            "subject": SUBJECT,
            "batch_id": checked["batch_id"],
            "capture_id": capture_id,
            "kind": kind,
            "skill_name": checked["skill"]["name"],
            "skill_source_sha256": checked["skill"]["source_sha256"],
        }
    )[:24].upper()


def _row_outcome(row: Mapping[str, Any]) -> str:
    outcome = row.get("outcome")
    status = row.get("status")
    if outcome is not None and status is not None and outcome != status:
        raise AdapterError("terminal_result_outcome_conflict")
    candidate = outcome if outcome is not None else status
    if candidate not in TERMINAL_OUTCOMES:
        raise AdapterError("terminal_result_outcome_invalid")
    return str(candidate)


def _validate_terminal_result_row(
    row: Any, *, selected_ids: list[str]
) -> tuple[str, str]:
    if not isinstance(row, Mapping):
        raise AdapterError("terminal_result_shape_invalid")
    capture_id = row.get("capture_id")
    if not isinstance(capture_id, str) or capture_id not in selected_ids:
        raise AdapterError("terminal_result_capture_binding_invalid")
    outcome = _row_outcome(row)
    expected_keys = {
        "curated": {
            "capture_id", "outcome", "formal_id", "normal_receipt_sha256",
            "terminal_binding_sha256",
        },
        "already_current": {
            "capture_id", "outcome", "formal_id", "verification_sha256",
            "terminal_binding_sha256",
        },
        "needs_user": {"capture_id", "outcome", "reason"},
        "failed": {"capture_id", "outcome", "reason"},
    }[outcome]
    if set(row) != expected_keys:
        raise AdapterError("terminal_result_shape_invalid")
    formal_id = row.get("formal_id")
    normal_receipt = row.get("normal_receipt_sha256")
    verification = row.get("verification_sha256")
    terminal_binding = row.get("terminal_binding_sha256")
    if terminal_binding is not None:
        _sha(terminal_binding, "terminal_binding_sha256_invalid")
    if outcome == "curated":
        formal = _nonempty_text(formal_id, "curated_formal_id_invalid")
        if FORMAL_ID.fullmatch(formal) is None:
            raise AdapterError("curated_formal_id_invalid")
        _sha(normal_receipt, "normal_receipt_sha256_invalid")
        if verification is not None:
            raise AdapterError("curated_verification_forbidden")
        if terminal_binding is None:
            raise AdapterError("curated_terminal_binding_missing")
    elif outcome == "already_current":
        formal = _nonempty_text(formal_id, "already_current_formal_id_invalid")
        if FORMAL_ID.fullmatch(formal) is None:
            raise AdapterError("already_current_formal_id_invalid")
        if normal_receipt is not None:
            raise AdapterError("already_current_normal_receipt_forbidden")
        _sha(verification, "verification_sha256_invalid")
        if terminal_binding is None:
            raise AdapterError("already_current_terminal_binding_missing")
    else:
        _nonempty_text(row.get("reason"), "terminal_failure_reason_missing")
    return capture_id, outcome


def _validate_native_terminal(
    batch: Mapping[str, Any],
    native_receipt: Mapping[str, Any],
    *,
    selected_ids: list[str],
) -> tuple[dict[str, Any], str | None]:
    required = {
        "schema_version", "subject", "batch_id", "status", "capture_results",
        "changed_files", "formal_write_count", "conflict",
        "global_audit_receipt_sha256", "close_batch_receipt_sha256",
        "execution_evidence",
    }
    if not isinstance(native_receipt, Mapping) or set(native_receipt) != required:
        raise AdapterError("native_terminal_shape_invalid")
    if native_receipt.get("schema_version") != NATIVE_TERMINAL_SCHEMA:
        raise AdapterError("native_terminal_schema_invalid")
    status = native_receipt.get("status")
    if (
        native_receipt.get("subject") != SUBJECT
        or native_receipt.get("batch_id") != batch["batch_id"]
        or status not in NATIVE_STATUSES
    ):
        raise AdapterError("native_terminal_binding_invalid")
    formal_write_count = native_receipt.get("formal_write_count")
    if (
        isinstance(formal_write_count, bool)
        or not isinstance(formal_write_count, int)
        or formal_write_count < 0
    ):
        raise AdapterError("native_terminal_formal_write_count_invalid")
    changed_files = native_receipt.get("changed_files")
    if (
        not isinstance(changed_files, list)
        or any(not isinstance(path, str) or not path for path in changed_files)
        or len(changed_files) != len(set(changed_files))
    ):
        raise AdapterError("native_terminal_changed_files_invalid")
    _validate_execution_evidence(native_receipt.get("execution_evidence"), status)
    for field in ("global_audit_receipt_sha256", "close_batch_receipt_sha256"):
        value = native_receipt.get(field)
        if value is not None:
            _sha(value, f"{field}_invalid")
        if status in {"complete", "noop"} and value is None:
            raise AdapterError(f"{field}_missing")
    if status in {"partial", "awaiting_user"} and native_receipt.get(
        "close_batch_receipt_sha256"
    ) is None:
        raise AdapterError("close_batch_receipt_sha256_missing")
    capture_results = native_receipt.get("capture_results")
    if not isinstance(capture_results, list):
        raise AdapterError("terminal_capture_results_invalid")
    if len(capture_results) != (
        len(selected_ids) - 1 if status == "awaiting_user" else len(selected_ids)
    ):
        raise AdapterError("terminal_capture_result_coverage_invalid")
    rows: list[tuple[str, str]] = []
    for row in capture_results:
        rows.append(_validate_terminal_result_row(row, selected_ids=selected_ids))
    row_ids = [capture_id for capture_id, _ in rows]
    if (
        len(set(row_ids)) != len(row_ids)
        or any(capture_id not in selected_ids for capture_id in row_ids)
        or row_ids != [item for item in selected_ids if item in row_ids]
    ):
        raise AdapterError("terminal_capture_result_coverage_invalid")
    outcomes = {capture_id: outcome for capture_id, outcome in rows}
    conflict = native_receipt.get("conflict")
    conflict_id: str | None = None
    if status == "awaiting_user":
        if not isinstance(conflict, Mapping):
            raise AdapterError("hard_conflict_invalid")
        kind = conflict.get("kind")
        capture_id = conflict.get("capture_id")
        alternate_capture_id = conflict.get("conflicted_capture_id")
        if capture_id is None:
            capture_id = alternate_capture_id
        elif alternate_capture_id is not None and alternate_capture_id != capture_id:
            raise AdapterError("hard_conflict_invalid")
        if kind not in HARD_CONFLICT_KINDS or capture_id not in selected_ids:
            raise AdapterError("hard_conflict_invalid")
        expected_safe_ids = [item_id for item_id in selected_ids if item_id != capture_id]
        if row_ids != expected_safe_ids:
            raise AdapterError("hard_conflict_result_separation_invalid")
        if any(outcome == "needs_user" for _, outcome in rows):
            raise AdapterError("hard_conflict_result_separation_invalid")
        conflict_id = _conflict_id(
            batch, capture_id=capture_id, kind=str(kind)
        )
        supplied_conflict_id = conflict.get("conflict_id")
        if supplied_conflict_id is not None and supplied_conflict_id != conflict_id:
            raise AdapterError("hard_conflict_id_invalid")
    elif conflict is not None:
        raise AdapterError("hard_conflict_invalid")
    elif row_ids != selected_ids:
        raise AdapterError("terminal_capture_result_coverage_invalid")
    if status in {"complete", "noop"} and any(
        outcome not in {"curated", "already_current"} for outcome in outcomes.values()
    ):
        raise AdapterError("complete_terminal_outcomes_invalid")
    curated_count = sum(outcome == "curated" for outcome in outcomes.values())
    if formal_write_count != curated_count:
        raise AdapterError("native_terminal_formal_write_count_mismatch")
    if status == "failed" and formal_write_count != 0:
        raise AdapterError("native_failed_formal_write_count_nonzero")
    return _thaw(native_receipt), conflict_id


def wrap_native_receipt(
    batch: Mapping[str, Any],
    native_receipt: Mapping[str, Any],
    *,
    selected_capture_ids: list[str] | None = None,
    capture_ids: list[str] | None = None,
    resolution: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    checked = validate_batch(batch)
    if selected_capture_ids is not None and capture_ids is not None:
        raise AdapterError("native_terminal_capture_scope_invalid")
    requested_ids = (
        selected_capture_ids if selected_capture_ids is not None else capture_ids
    )
    checked_resolution = (
        _validate_resolution(resolution, checked) if resolution is not None else None
    )
    if checked_resolution is not None:
        selected = _validate_selection(checked, requested_ids, checked_resolution)
    elif requested_ids is None:
        selected = list(checked["capture_ids"])
    else:
        if (
            not isinstance(requested_ids, list)
            or not requested_ids
            or any(not isinstance(item, str) or not item for item in requested_ids)
            or requested_ids != checked["capture_ids"]
            or len(requested_ids) != len(set(requested_ids))
            or not set(requested_ids).issubset(set(checked["capture_ids"]))
        ):
            raise AdapterError("native_terminal_capture_scope_invalid")
        selected = list(requested_ids)
    if isinstance(native_receipt, Mapping) and native_receipt.get(
        "schema_version"
    ) == NATIVE_TERMINAL_SCHEMA:
        receipt, conflict_id = _validate_native_terminal(
            checked, native_receipt, selected_ids=selected
        )
        status = str(receipt["status"])
        return {
            "schema_version": RESULT_SCHEMA,
            "adapter_name": ADAPTER_NAME,
            "subject": SUBJECT,
            "batch_id": checked["batch_id"],
            "status": status,
            "native_receipt": receipt,
            "native_receipt_sha256": sha256_value(receipt),
            "conflict_id": conflict_id,
            "formal_write_count": int(receipt["formal_write_count"]),
        }
    raise AdapterError("native_terminal_schema_required")


class CS408NightlySolAdapter:
    """Callable CS408 boundary for one frozen nightly batch or one recovery item."""

    def execute(
        self,
        batch: Mapping[str, Any],
        *,
        native_executor: Any,
        capture_ids: list[str] | None = None,
        resolution: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not callable(native_executor):
            raise AdapterError("native_executor_invalid")
        checked = validate_batch(batch)
        checked_resolution = (
            _validate_resolution(resolution, checked) if resolution is not None else None
        )
        selected = _validate_selection(checked, capture_ids, checked_resolution)
        if checked_resolution is None and capture_ids is None:
            invocation = prepare_invocation(checked)
        else:
            invocation = prepare_invocation(
                checked,
                capture_ids=selected,
                resolution=checked_resolution,
            )
        native_receipt = native_executor(invocation)
        return wrap_native_receipt(
            checked,
            native_receipt,
            selected_capture_ids=selected,
            resolution=checked_resolution,
        )


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
