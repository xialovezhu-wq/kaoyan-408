#!/usr/bin/env python3
"""Deterministic node-level coverage campaign and daily due projection for 408.

This module is a cold, read-only planner.  It does not mutate the review ledger,
formal tables, sessions, packages, Captures, or any formal knowledge artifact.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.metadata
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    import fsrs as pyfsrs
except ImportError:  # pragma: no cover - exercised by deployment fail-closed check
    pyfsrs = None  # type: ignore[assignment]


CONFIG_SCHEMA = "review_campaign_config_v1"
MEMORY_SCHEMA = "review_memory_state_v1"
DUE_RUN_SCHEMA = "morning_due_run_v1"
FORECAST_SCHEMA = "morning_coverage_forecast_v1"
ADMISSION_SCHEMA = "review_campaign_admission_event_v1"
REFRESH_HANDOFF_SCHEMA = "review_campaign_refresh_handoff_v1"
POLICY_VERSION = "morning-node-coverage-campaign-v1"
DEFAULT_ADMISSION_REL = Path(
    "wiki/study_vaults/408-full/state/review-campaign/admissions.jsonl"
)
FSRS_DISTRIBUTION = "fsrs"
FSRS_VERSION = "6.3.2"
FSRS_MODEL = f"{FSRS_DISTRIBUTION}=={FSRS_VERSION}"
RATING_MAPPING_VERSION = "review-event-to-fsrs-rating-v1"

FORMAL_NODE_RE = re.compile(r"^(?:DS|CO|OS|CN)_(?:\d{4}|UNK)_\d{3}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
UNKNOWN_DATE_VALUES = {"", "未记录", "未标明", "待确认", "待补充", "unknown", "unrecorded"}
DEFAULT_PARAMETERS = (
    0.212,
    1.2931,
    2.3065,
    8.2956,
    6.4133,
    0.8334,
    3.0194,
    0.001,
    1.8722,
    0.1666,
    0.796,
    1.4835,
    0.0614,
    0.2629,
    1.6483,
    0.6014,
    1.8729,
    0.5425,
    0.0912,
    0.0658,
    0.1542,
)


class CampaignError(RuntimeError):
    """Raised when a campaign input cannot be verified safely."""


def require_pinned_fsrs() -> None:
    if pyfsrs is None:
        raise CampaignError(
            "fsrs==6.3.2 is required; install requirements-morning-coverage.txt"
        )
    try:
        installed = importlib.metadata.version(FSRS_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError as exc:
        raise CampaignError("fsrs==6.3.2 distribution metadata is unavailable") from exc
    if installed != FSRS_VERSION:
        raise CampaignError(
            f"fsrs version drifted: expected {FSRS_VERSION}, found {installed}"
        )


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    raw = value if isinstance(value, bytes) else _canonical_bytes(value)
    return hashlib.sha256(raw).hexdigest()


def file_sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise CampaignError(f"unsafe or missing regular file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_date(value: str, label: str = "date") -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise CampaignError(f"{label} must be YYYY-MM-DD") from exc


def _optional_date(value: object, label: str) -> tuple[dt.date | None, str | None]:
    text = str(value or "").strip()
    if text in UNKNOWN_DATE_VALUES:
        return None, None
    try:
        return dt.date.fromisoformat(text), None
    except ValueError:
        return None, f"{label}_invalid"


def _markdown_rows(
    path: Path, *, id_column: str = "ID"
) -> tuple[list[str], list[dict[str, str]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    header: list[str] | None = None
    rows: list[dict[str, str]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if header is None:
            if id_column not in cells:
                continue
            header = cells
            continue
        if all(cell and set(cell) <= {"-", ":"} for cell in cells):
            continue
        if len(cells) != len(header):
            rows.append(
                {
                    id_column: cells[0] if cells else "",
                    "_line": str(line_number),
                    "_parse_error": "column_count_mismatch",
                }
            )
            continue
        row = dict(zip(header, cells))
        row["_line"] = str(line_number)
        rows.append(row)
    if header is None:
        raise CampaignError(f"markdown table with {id_column} column not found: {path}")
    return header, rows


def default_config() -> dict[str, Any]:
    return {
        "schema": CONFIG_SCHEMA,
        "campaign_id": "RC-408-2026-110D",
        "campaign_start": "2026-08-31",
        "exam_date": "2026-12-19",
        "consolidation_start": "2026-12-12",
        "latest_safe_day": "2026-12-11",
        "desired_retention": 0.90,
        "cohort_expected_count": 454,
        "segment_size": 16,
        "semantic_cap": None,
        "timezone": "Asia/Shanghai",
        "unknown_date_policy": {
            "mode": "balanced_hash_spread",
            "salt": "kaoyan-408-2026-node-coverage-v1",
        },
        "consolidation_policy": "predicted_forgetting_risk_only",
        "excluded_nodes": [],
        "fsrs": {
            "backend": FSRS_MODEL,
            "parameters": list(DEFAULT_PARAMETERS),
            "enable_fuzzing": False,
            "rating_mapping_version": RATING_MAPPING_VERSION,
        },
    }


def validate_config(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != CONFIG_SCHEMA:
        raise CampaignError(f"config schema must be {CONFIG_SCHEMA}")
    config = json.loads(json.dumps(value, ensure_ascii=False))
    start = parse_date(str(config.get("campaign_start") or ""), "campaign_start")
    exam = parse_date(str(config.get("exam_date") or ""), "exam_date")
    consolidation = parse_date(
        str(config.get("consolidation_start") or ""), "consolidation_start"
    )
    latest_safe = parse_date(str(config.get("latest_safe_day") or ""), "latest_safe_day")
    if (exam - start).days != 110:
        raise CampaignError("campaign_start must be exactly 110 days before exam_date")
    if not start <= latest_safe < consolidation < exam:
        raise CampaignError(
            "campaign_start <= latest_safe_day < consolidation_start < exam_date is required"
        )
    if latest_safe != consolidation - dt.timedelta(days=1):
        raise CampaignError("latest_safe_day must be the day before consolidation_start")
    if config.get("timezone") != "Asia/Shanghai":
        raise CampaignError("timezone must be Asia/Shanghai")
    retention = config.get("desired_retention")
    if isinstance(retention, bool) or not isinstance(retention, (int, float)):
        raise CampaignError("desired_retention must be numeric")
    if not math.isclose(float(retention), 0.90, rel_tol=0.0, abs_tol=1e-12):
        raise CampaignError("desired_retention is fixed at 0.90")
    if config.get("semantic_cap") is not None:
        raise CampaignError("semantic_cap must be null; all due nodes are scheduled")
    segment_size = config.get("segment_size")
    if isinstance(segment_size, bool) or not isinstance(segment_size, int) or segment_size != 16:
        raise CampaignError("segment_size is fixed at 16")
    expected = config.get("cohort_expected_count")
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 1:
        raise CampaignError("cohort_expected_count must be a positive integer")
    unknown = config.get("unknown_date_policy")
    if not isinstance(unknown, dict) or unknown.get("mode") != "balanced_hash_spread":
        raise CampaignError("unknown_date_policy.mode must be balanced_hash_spread")
    if not str(unknown.get("salt") or ""):
        raise CampaignError("unknown_date_policy.salt must be non-empty")
    fsrs = config.get("fsrs")
    if not isinstance(fsrs, dict) or fsrs.get("backend") != FSRS_MODEL:
        raise CampaignError(
            f"fsrs.backend must explicitly be {FSRS_MODEL}; no silent external fallback"
        )
    parameters = fsrs.get("parameters")
    if (
        not isinstance(parameters, list)
        or len(parameters) != 21
        or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in parameters)
    ):
        raise CampaignError("FSRS-6 requires exactly 21 numeric parameters")
    if fsrs.get("enable_fuzzing") is not False:
        raise CampaignError("deterministic campaign projection requires enable_fuzzing=false")
    require_pinned_fsrs()
    exclusions = config.get("excluded_nodes")
    if not isinstance(exclusions, list):
        raise CampaignError("excluded_nodes must be a list")
    seen: set[str] = set()
    for row in exclusions:
        if not isinstance(row, dict):
            raise CampaignError("every excluded_nodes row must be an object")
        node = str(row.get("formal_node_id") or "")
        if not FORMAL_NODE_RE.fullmatch(node):
            raise CampaignError(f"invalid excluded formal_node_id: {node}")
        if node in seen:
            raise CampaignError(f"duplicate excluded formal_node_id: {node}")
        if not str(row.get("reason_code") or "") or not str(row.get("evidence_ref") or ""):
            raise CampaignError(f"excluded node {node} requires reason_code and evidence_ref")
        seen.add(node)
    return config


def read_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return validate_config(default_config())
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CampaignError(f"invalid config JSON: {path}") from exc
    return validate_config(value)


def read_admission_ledger(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {
            "path": None if path is None else str(path),
            "byte_size": 0,
            "event_count": 0,
            "sha256": canonical_sha256(b""),
            "events": [],
            "latest_by_node": {},
            "first_kind_by_node": {},
        }
    if path.is_symlink() or not path.is_file():
        raise CampaignError(f"unsafe admission ledger: {path}")
    raw = path.read_bytes()
    events: list[dict[str, Any]] = []
    latest_by_node: dict[str, dict[str, Any]] = {}
    first_kind_by_node: dict[str, str] = {}
    seen_ids: set[str] = set()
    seen_keys: dict[str, str] = {}
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CampaignError(f"invalid admission JSON at line {line_number}") from exc
        if not isinstance(event, dict) or event.get("schema") != ADMISSION_SCHEMA:
            raise CampaignError(f"invalid admission schema at line {line_number}")
        event_id = str(event.get("event_id") or "")
        event_kind = str(event.get("event_kind") or "")
        node = str(event.get("formal_node_id") or "")
        key = str(event.get("idempotency_key") or "")
        if not event_id or event_id in seen_ids:
            raise CampaignError(f"missing or duplicate admission event_id at line {line_number}")
        if event_kind not in {"campaign_node_admitted", "campaign_node_refreshed"}:
            raise CampaignError(f"unsupported admission event_kind at line {line_number}")
        if not FORMAL_NODE_RE.fullmatch(node):
            raise CampaignError(f"invalid admission formal_node_id at line {line_number}")
        if event.get("status") != "succeeded":
            raise CampaignError(f"non-success admission event cannot enter truth at line {line_number}")
        if not str(event.get("source_receipt_ref") or "") or not SHA256_RE.fullmatch(
            str(event.get("source_receipt_sha256") or "")
        ):
            raise CampaignError(f"admission source receipt binding invalid at line {line_number}")
        if not SHA256_RE.fullmatch(str(event.get("formal_node_row_sha256") or "")):
            raise CampaignError(f"admission formal row hash invalid at line {line_number}")
        expected = canonical_sha256({k: v for k, v in event.items() if k != "event_sha256"})
        if event.get("event_sha256") != expected:
            raise CampaignError(f"admission event hash mismatch at line {line_number}")
        if not key:
            raise CampaignError(f"admission idempotency_key missing at line {line_number}")
        prior = seen_keys.get(key)
        if prior is not None and prior != expected:
            raise CampaignError(f"admission idempotency conflict at line {line_number}")
        if event_kind == "campaign_node_admitted" and node in latest_by_node:
            raise CampaignError(f"node {node} admitted more than once")
        if event_kind == "campaign_node_refreshed":
            previous = latest_by_node.get(node)
            if previous is None:
                if event.get("baseline_member") is not True or event.get(
                    "previous_admission_event_id"
                ) not in {None, ""}:
                    raise CampaignError(
                        f"first refresh for baseline node {node} must declare baseline_member=true"
                    )
            elif event.get("previous_admission_event_id") != previous["event_id"]:
                raise CampaignError(f"node {node} refresh does not extend its admission chain")
        seen_ids.add(event_id)
        seen_keys[key] = expected
        events.append(event)
        first_kind_by_node.setdefault(node, event_kind)
        latest_by_node[node] = event
    return {
        "path": str(path),
        "byte_size": len(raw),
        "event_count": len(events),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "events": events,
        "latest_by_node": latest_by_node,
        "first_kind_by_node": first_kind_by_node,
    }


def admission_cutoff(admissions: dict[str, Any]) -> dict[str, Any]:
    events = admissions["events"]
    return {
        "ref": admissions["path"],
        "byte_size": admissions["byte_size"],
        "event_count": admissions["event_count"],
        "sha256": admissions["sha256"],
        "latest_event_id": None if not events else events[-1]["event_id"],
        "latest_event_sha256": None if not events else events[-1]["event_sha256"],
    }


def validate_refresh_handoff(path: Path | None, admissions: dict[str, Any]) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CampaignError(f"invalid refresh handoff JSON: {path}") from exc
    if not isinstance(value, dict) or value.get("schema") != REFRESH_HANDOFF_SCHEMA:
        raise CampaignError(f"refresh handoff schema must be {REFRESH_HANDOFF_SCHEMA}")
    event_id = str(value.get("trigger_admission_event_id") or "")
    event_sha = str(value.get("trigger_admission_event_sha256") or "")
    if not SHA256_RE.fullmatch(event_sha) or not SHA256_RE.fullmatch(
        str(value.get("admission_ledger_sha256") or "")
    ):
        raise CampaignError("refresh handoff SHA-256 fields are invalid")
    matching = [event for event in admissions["events"] if event["event_id"] == event_id]
    if len(matching) != 1 or matching[0]["event_sha256"] != event_sha:
        raise CampaignError("refresh handoff admission event/hash is not in the ledger cutoff")
    if value.get("admission_ledger_sha256") != admissions["sha256"]:
        raise CampaignError("refresh handoff admission ledger hash mismatch")
    return {
        "request_id": value.get("request_id"),
        "trigger_admission_event_id": event_id,
        "trigger_admission_event_sha256": event_sha,
        "handoff_sha256": canonical_sha256(value),
    }


def parse_cohort(
    master_path: Path,
    config: dict[str, Any],
    as_of: dt.date,
    admissions: dict[str, Any] | None = None,
    track_path: Path | None = None,
) -> dict[str, Any]:
    _, raw_rows = _markdown_rows(master_path)
    track_path = track_path or master_path.parent / "原题复做轨总表.md"
    track_header, raw_tracks = _markdown_rows(track_path, id_column="正式节点ID")
    track_by_node: dict[str, list[dict[str, str]]] = {}
    for track in raw_tracks:
        track_by_node.setdefault(str(track.get("正式节点ID") or "").strip(), []).append(track)
    admissions = admissions or read_admission_ledger(None)
    admitted = admissions["latest_by_node"]
    exclusions = {
        str(row["formal_node_id"]): row for row in config.get("excluded_nodes") or []
    }
    id_counts: dict[str, int] = {}
    for row in raw_rows:
        node = str(row.get("ID") or "")
        if node:
            id_counts[node] = id_counts.get(node, 0) + 1
    included: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for row in raw_rows:
        node = str(row.get("ID") or "").strip()
        reason_codes: list[str] = []
        if row.get("_parse_error"):
            reason_codes.append(str(row["_parse_error"]))
        if not FORMAL_NODE_RE.fullmatch(node):
            reason_codes.append("formal_node_id_invalid")
        if node and id_counts.get(node, 0) > 1:
            reason_codes.append("formal_node_id_duplicate")
        tracks = track_by_node.get(node, [])
        track_row: dict[str, str] | None = tracks[0] if len(tracks) == 1 else None
        if len(tracks) == 0:
            reason_codes.append("original_track_missing")
        elif len(tracks) > 1:
            reason_codes.append("original_track_duplicate")
        first_date, first_error = _optional_date(row.get("首次做题日期"), "first_study_date")
        recent_date, recent_error = _optional_date(row.get("最近复做日期"), "last_review_date")
        reason_codes.extend(code for code in (first_error, recent_error) if code)
        if first_date and first_date > as_of:
            reason_codes.append("first_study_date_in_future")
        if recent_date and recent_date > as_of:
            reason_codes.append("last_review_date_in_future")
        base = {
            "formal_node_id": node or f"row-line-{row.get('_line')}",
            "source_id": row.get("来源ID") or None,
            "subject": row.get("科目") or None,
            "module": row.get("主模块") or None,
            "first_study_date": first_date.isoformat() if first_date else None,
            "last_review_date": recent_date.isoformat() if recent_date else None,
            "row_line": int(row.get("_line") or 0),
            "row_sha256": canonical_sha256(
                {key: value for key, value in row.items() if not key.startswith("_")}
            ),
            "original_track_state": (
                None if track_row is None else track_row.get("原题轨状态") or None
            ),
            "original_track_reason": (
                None if track_row is None else track_row.get("待补事项") or None
            ),
            "original_track_row_sha256": (
                None
                if track_row is None
                else canonical_sha256(
                    {key: track_row.get(key, "") for key in track_header}
                )
            ),
        }
        if track_row is not None and track_row.get("原题轨状态") == "blocked_needs_user":
            reason_codes.append("original_track_blocked_needs_user")
        if node in admitted and not reason_codes:
            admission = admitted[node]
            if admission.get("formal_node_row_sha256") != base["row_sha256"]:
                reason_codes.append("admission_formal_row_hash_mismatch")
        if reason_codes:
            blocked.append({**base, "reason_codes": sorted(set(reason_codes))})
        elif node in exclusions:
            exclusion = exclusions[node]
            excluded.append(
                {
                    **base,
                    "reason_code": exclusion["reason_code"],
                    "evidence_ref": exclusion["evidence_ref"],
                    "evidence_sha256": exclusion.get("evidence_sha256"),
                }
            )
        else:
            admission = admitted.get(node)
            included.append(
                {
                    **base,
                    "admission_event_id": None if admission is None else admission["event_id"],
                    "admission_event_sha256": (
                        None if admission is None else admission["event_sha256"]
                    ),
                }
            )
    included.sort(key=lambda row: row["formal_node_id"])
    excluded.sort(key=lambda row: row["formal_node_id"])
    blocked.sort(key=lambda row: (row["formal_node_id"], row["row_line"]))
    cohort_count = len(raw_rows)
    expected = int(config["cohort_expected_count"])
    post_baseline_admitted_nodes = {
        node
        for node, first_kind in admissions["first_kind_by_node"].items()
        if first_kind == "campaign_node_admitted"
    }
    baseline_count = cohort_count - len(post_baseline_admitted_nodes)
    if baseline_count != expected:
        raise CampaignError(
            f"baseline formal cohort count mismatch: expected {expected}, parsed {baseline_count}; "
            "post-baseline nodes require successful admission events"
        )
    missing_admitted = sorted(set(admitted) - {str(row.get("ID") or "") for row in raw_rows})
    if missing_admitted:
        raise CampaignError("admitted nodes missing from formal table: " + ",".join(missing_admitted))
    master_nodes = {str(row.get("ID") or "").strip() for row in raw_rows}
    extra_track_nodes = sorted(set(track_by_node) - master_nodes)
    if extra_track_nodes:
        raise CampaignError(
            "original track rows have no formal node: " + ",".join(extra_track_nodes)
        )
    sets = [
        {row["formal_node_id"] for row in included},
        {row["formal_node_id"] for row in excluded},
        {row["formal_node_id"] for row in blocked},
    ]
    if sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2]:
        raise CampaignError("included/excluded/blocked node sets are not disjoint")
    if len(included) + len(excluded) + len(blocked) != cohort_count:
        raise CampaignError("cohort partition identity failed")
    partition = {
        "cohort_count": cohort_count,
        "baseline_cohort_count": baseline_count,
        "admitted_node_count": len(post_baseline_admitted_nodes),
        "admission_bound_node_count": len(admitted),
        "included_count": len(included),
        "excluded_count": len(excluded),
        "blocked_count": len(blocked),
        "identity": "cohort_count == included_count + excluded_count + blocked_count",
        "identity_holds": True,
        "included": included,
        "excluded": excluded,
        "blocked": blocked,
        "master_sha256": file_sha256(master_path),
        "original_track_sha256": file_sha256(track_path),
        "admission_cutoff": admission_cutoff(admissions),
        "admission_event_bindings": [
            {
                "formal_node_id": node,
                "event_id": event["event_id"],
                "event_sha256": event["event_sha256"],
            }
            for node, event in sorted(admitted.items())
        ],
    }
    partition["cohort_sha256"] = canonical_sha256(partition)
    return partition


def read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CampaignError(f"invalid review event JSON at line {line_number}") from exc
        if not isinstance(value, dict):
            raise CampaignError(f"review event line {line_number} is not an object")
        events.append(value)
    return events


def _corrected_dates(events: Sequence[dict[str, Any]]) -> dict[str, dt.date]:
    result: dict[str, dt.date] = {}
    for event in events:
        if event.get("event_kind") != "date_correction":
            continue
        source = str(event.get("source_event_id") or "")
        value = event.get("actual_observed_date") or event.get("corrected_observed_date")
        if not source or not value:
            continue
        corrected = parse_date(str(value), "date_correction actual date")
        if source in result and result[source] != corrected:
            raise CampaignError(f"conflicting date corrections for event {source}")
        result[source] = corrected
    return result


def _event_binding_ranks(event: dict[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    direct = str(event.get("formal_node_id") or "")
    actual = str(event.get("actual_question_formal_node_id") or "")
    if FORMAL_NODE_RE.fullmatch(direct):
        result[direct] = max(result.get(direct, 0), 400)
    if FORMAL_NODE_RE.fullmatch(actual):
        result[actual] = max(result.get(actual, 0), 390)
    for value in event.get("mapped_formal_node_ids") or []:
        node = str(value)
        if FORMAL_NODE_RE.fullmatch(node):
            result[node] = max(result.get(node, 0), 300)
    source = str(event.get("source_id") or "")
    if FORMAL_NODE_RE.fullmatch(source):
        result[source] = max(result.get(source, 0), 200)
    return result


def _is_exact_original_outcome(event: dict[str, Any]) -> bool:
    if event.get("event_kind") != "outcome" or event.get("question_valid") is not True:
        return False
    if event.get("practice_mode") == "variant":
        return False
    if event.get("surface_kind") == "verified_variant" or event.get("variant_id"):
        return False
    if event.get("presentation_kind") in {"variant", "variant_question", "derived_variant"}:
        return False
    return True


def _rating(event: dict[str, Any]) -> int:
    result = str(event.get("first_result") or "")
    prompt = str(event.get("prompt_level") or "none")
    hint = event.get("hint_used") is True
    reasoning = str(event.get("reasoning_result") or "not_observed")
    confidence = str(event.get("confidence") or "medium")
    if event.get("scheduling_mode") == "campaign_fsrs_successor":
        screened = event.get("screened_correct") is True
        audited = event.get("reason_audited") is True
        choice = str(event.get("choice_result") or "")
        if screened:
            if (
                choice != "correct"
                or audited
                or reasoning != "not_observed"
                or prompt != "none"
            ):
                raise CampaignError(
                    f"campaign screened evidence is inconsistent in {event.get('event_id')}"
                )
            return 2
        if (
            choice != "correct"
            or prompt != "none"
            or hint
            or reasoning in {"partial", "diverged"}
            or result in {"wrong", "uncertain", "partial"}
        ):
            return 1
        if audited and reasoning == "sound" and result == "independent_correct":
            return 2 if confidence == "low" else 3
        raise CampaignError(
            f"campaign outcome cannot be mapped in {event.get('event_id')}"
        )
    if result in {"wrong", "uncertain"}:
        return 1
    if prompt != "none" or hint or reasoning in {"partial", "diverged"}:
        return 1
    if result == "partial":
        if event.get("choice_result") == "correct" and reasoning == "not_observed":
            return 2
        return 1
    if result == "independent_correct":
        if (
            confidence == "low"
            or event.get("fragile_mastery") is True
            or reasoning != "sound"
        ):
            return 2
        return 3
    raise CampaignError(f"unsupported first_result in event {event.get('event_id')}: {result}")


def _is_schedulable_memory_outcome(event: dict[str, Any]) -> bool:
    if event.get("scheduling_mode") == "campaign_fsrs_successor":
        return bool(
            event.get("event_kind") == "outcome"
            and event.get("question_valid") is True
            and FORMAL_NODE_RE.fullmatch(str(event.get("formal_node_id") or ""))
            and event.get("response_mode") in {"choice_only", "choice_plus_reason"}
            and isinstance(event.get("recognized_original"), bool)
            and isinstance(event.get("reason_audited"), bool)
            and isinstance(event.get("screened_correct"), bool)
            and event.get("true_item_id")
        )
    return _is_exact_original_outcome(event)


def prioritized_node_history(
    events: Sequence[dict[str, Any]], node_ids: set[str]
) -> dict[str, list[dict[str, Any]]]:
    """Select one authoritative node-memory outcome per observed date.

    Priority is: corrected date over raw date; exact node binding over mapped
    binding over source-ID fallback; morning cross-day evidence over evening D0
    over ordinary practice; then latest event_time/event_id.  Legacy derived
    variants remain excluded.  A verified successor campaign variant updates
    only its directly bound formal node; exact-original exposure remains a
    separate ledger and is not inferred here.
    """

    corrections = _corrected_dates(events)
    source_rank = {"morning_review": 30, "evening_d0": 20, "daily_practice": 10}
    candidates: dict[tuple[str, dt.date], list[dict[str, Any]]] = {}
    for event in events:
        if not _is_schedulable_memory_outcome(event):
            continue
        event_id = str(event.get("event_id") or "")
        raw_date = event.get("observed_date")
        if not raw_date:
            continue
        observed = corrections.get(event_id) or parse_date(str(raw_date), "event observed_date")
        for node, binding_rank in _event_binding_ranks(event).items():
            if node not in node_ids:
                continue
            priority = (
                binding_rank,
                source_rank.get(str(event.get("source") or ""), 0)
                + (5 if event.get("cross_day") is True else 0),
                str(event.get("event_time") or ""),
                event_id,
            )
            candidates.setdefault((node, observed), []).append(
                {
                    "event_id": event_id,
                    "observed_date": observed.isoformat(),
                    "event_time": event.get("event_time"),
                    "first_result": event.get("first_result"),
                    "confidence": event.get("confidence"),
                    "prompt_level": event.get("prompt_level"),
                    "hint_used": bool(event.get("hint_used")),
                    "reasoning_result": event.get("reasoning_result", "not_observed"),
                    "rating": _rating(event),
                    "binding_rank": binding_rank,
                    "priority": priority,
                    "date_corrected": event_id in corrections,
                    "event_sha256": canonical_sha256(event),
                }
            )
    result: dict[str, list[dict[str, Any]]] = {node: [] for node in sorted(node_ids)}
    for (node, _), rows in candidates.items():
        result[node].append(max(rows, key=lambda row: row["priority"]))
    for node in result:
        result[node].sort(
            key=lambda row: (row["observed_date"], str(row["event_time"] or ""), row["event_id"])
        )
        for row in result[node]:
            row.pop("priority", None)
    return result


@dataclass(frozen=True)
class FsrsState:
    difficulty: float
    stability: float
    last_review_date: dt.date
    reps: int
    lapses: int


class Fsrs6Compat:
    """Deterministic FSRS-6 D/S/R projection using the public 21-weight formula."""

    def __init__(self, parameters: Sequence[float], desired_retention: float) -> None:
        if len(parameters) != 21:
            raise CampaignError("FSRS-6 compatible backend requires 21 parameters")
        self.w = tuple(float(item) for item in parameters)
        self.desired_retention = float(desired_retention)
        self.decay = -self.w[20]
        self.factor = 0.9 ** (1 / self.decay) - 1

    @staticmethod
    def _clamp_difficulty(value: float) -> float:
        return min(max(value, 1.0), 10.0)

    @staticmethod
    def _clamp_stability(value: float) -> float:
        return max(value, 0.001)

    def retrievability(self, stability: float, elapsed_days: int) -> float:
        elapsed = max(0, elapsed_days)
        return (1 + self.factor * elapsed / stability) ** self.decay

    def interval(self, stability: float) -> int:
        value = (stability / self.factor) * (
            self.desired_retention ** (1 / self.decay) - 1
        )
        return max(1, round(value))

    def initial(self, review_date: dt.date, rating: int) -> FsrsState:
        stability = self._clamp_stability(self.w[rating - 1])
        difficulty = self._clamp_difficulty(
            self.w[4] - math.e ** (self.w[5] * (rating - 1)) + 1
        )
        return FsrsState(difficulty, stability, review_date, 1, int(rating == 1))

    def _next_difficulty(self, difficulty: float, rating: int) -> float:
        initial_easy = self.w[4] - math.e ** (self.w[5] * 3) + 1
        delta = -(self.w[6] * (rating - 3))
        damped = difficulty + (10 - difficulty) * delta / 9
        return self._clamp_difficulty(self.w[7] * initial_easy + (1 - self.w[7]) * damped)

    def review(self, state: FsrsState, review_date: dt.date, rating: int) -> FsrsState:
        elapsed = (review_date - state.last_review_date).days
        if elapsed < 0:
            raise CampaignError("FSRS history is not chronological")
        difficulty = state.difficulty
        stability = state.stability
        if elapsed < 1:
            increase = math.e ** (self.w[17] * (rating - 3 + self.w[18]))
            increase *= stability ** -self.w[19]
            if rating >= 2:
                increase = max(increase, 1.0)
            new_stability = stability * increase
        else:
            retrievability = self.retrievability(stability, elapsed)
            if rating == 1:
                long_term = (
                    self.w[11]
                    * difficulty ** -self.w[12]
                    * ((stability + 1) ** self.w[13] - 1)
                    * math.e ** ((1 - retrievability) * self.w[14])
                )
                short_term = stability / math.e ** (self.w[17] * self.w[18])
                new_stability = min(long_term, short_term)
            else:
                hard_penalty = self.w[15] if rating == 2 else 1.0
                easy_bonus = self.w[16] if rating == 4 else 1.0
                new_stability = stability * (
                    1
                    + math.e ** self.w[8]
                    * (11 - difficulty)
                    * stability ** -self.w[9]
                    * (math.e ** ((1 - retrievability) * self.w[10]) - 1)
                    * hard_penalty
                    * easy_bonus
                )
        return FsrsState(
            self._next_difficulty(difficulty, rating),
            self._clamp_stability(new_stability),
            review_date,
            state.reps + 1,
            state.lapses + int(rating == 1),
        )


class Fsrs632Pinned:
    """Strict adapter over the installed ``fsrs==6.3.2`` distribution."""

    def __init__(self, parameters: Sequence[float], desired_retention: float) -> None:
        require_pinned_fsrs()
        assert pyfsrs is not None
        self.scheduler = pyfsrs.Scheduler(
            parameters=tuple(float(item) for item in parameters),
            desired_retention=float(desired_retention),
            learning_steps=(),
            relearning_steps=(),
            enable_fuzzing=False,
        )

    @staticmethod
    def _utc_day(value: dt.date) -> dt.datetime:
        return dt.datetime.combine(value, dt.time(), tzinfo=dt.timezone.utc)

    def initial(self, review_date: dt.date, rating: int) -> FsrsState:
        assert pyfsrs is not None
        reviewed, _ = self.scheduler.review_card(
            pyfsrs.Card(),
            pyfsrs.Rating(rating),
            review_datetime=self._utc_day(review_date),
        )
        if reviewed.difficulty is None or reviewed.stability is None:
            raise CampaignError("fsrs==6.3.2 did not initialize D/S state")
        return FsrsState(
            float(reviewed.difficulty),
            float(reviewed.stability),
            review_date,
            1,
            int(rating == 1),
        )

    def review(
        self, state: FsrsState, review_date: dt.date, rating: int
    ) -> FsrsState:
        assert pyfsrs is not None
        if review_date < state.last_review_date:
            raise CampaignError("FSRS history is not chronological")
        previous = self._utc_day(state.last_review_date)
        card = pyfsrs.Card(
            state=pyfsrs.State.Review,
            step=None,
            stability=float(state.stability),
            difficulty=float(state.difficulty),
            due=previous,
            last_review=previous,
        )
        reviewed, _ = self.scheduler.review_card(
            card,
            pyfsrs.Rating(rating),
            review_datetime=self._utc_day(review_date),
        )
        if reviewed.difficulty is None or reviewed.stability is None:
            raise CampaignError("fsrs==6.3.2 returned incomplete D/S state")
        return FsrsState(
            float(reviewed.difficulty),
            float(reviewed.stability),
            review_date,
            state.reps + 1,
            state.lapses + int(rating == 1),
        )

    def interval(self, stability: float) -> int:
        # Version is fail-closed above; this is the exact 6.3.2 interval helper.
        return int(self.scheduler._next_interval(stability=float(stability)))

    def retrievability(self, stability: float, elapsed_days: int) -> float:
        assert pyfsrs is not None
        anchor = dt.date(2000, 1, 1)
        last_review = self._utc_day(anchor)
        card = pyfsrs.Card(
            state=pyfsrs.State.Review,
            step=None,
            stability=float(stability),
            difficulty=5.0,
            due=last_review,
            last_review=last_review,
        )
        return float(
            self.scheduler.get_card_retrievability(
                card,
                current_datetime=self._utc_day(
                    anchor + dt.timedelta(days=max(0, elapsed_days))
                ),
            )
        )


def _balanced_unknown_dates(
    node_ids: Iterable[str], start: dt.date, latest_safe: dt.date, salt: str
) -> dict[str, dt.date]:
    day_count = (latest_safe - start).days + 1
    if day_count < 1:
        raise CampaignError("unknown-date smoothing window is empty")
    ordered = sorted(
        node_ids,
        key=lambda node: (hashlib.sha256(f"{salt}:{node}".encode()).hexdigest(), node),
    )
    return {
        node: start + dt.timedelta(days=index % day_count)
        for index, node in enumerate(ordered)
    }


def project_memory_states(
    cohort: dict[str, Any],
    events: Sequence[dict[str, Any]],
    config: dict[str, Any],
    as_of: dt.date,
) -> dict[str, Any]:
    included = cohort["included"]
    node_ids = {row["formal_node_id"] for row in included}
    history = prioritized_node_history(events, node_ids)
    fsrs = Fsrs632Pinned(config["fsrs"]["parameters"], float(config["desired_retention"]))
    start = parse_date(config["campaign_start"])
    latest_safe = parse_date(config["latest_safe_day"])
    unknown_nodes = [
        row["formal_node_id"]
        for row in included
        if not history[row["formal_node_id"]]
        and not row.get("last_review_date")
        and not row.get("first_study_date")
    ]
    smoothed = _balanced_unknown_dates(
        unknown_nodes,
        start,
        latest_safe,
        str(config["unknown_date_policy"]["salt"]),
    )
    states: list[dict[str, Any]] = []
    for row in included:
        node = row["formal_node_id"]
        node_history = [
            item
            for item in history[node]
            if parse_date(item["observed_date"]) <= as_of
        ]
        model_state: FsrsState | None = None
        history_source: str
        calibration: str
        evidence_refs: list[dict[str, str]] = []
        if node_history:
            for item in node_history:
                review_date = parse_date(item["observed_date"])
                if model_state is None:
                    model_state = fsrs.initial(review_date, int(item["rating"]))
                else:
                    model_state = fsrs.review(model_state, review_date, int(item["rating"]))
                evidence_refs.append(
                    {
                        "ref": f"review-event://{item['event_id']}",
                        "sha256": item["event_sha256"],
                    }
                )
            history_source = "review_event_ledger"
            calibration = "event_replay"
            due_date = model_state.last_review_date + dt.timedelta(
                days=fsrs.interval(model_state.stability)
            )
        else:
            anchor_text = row.get("last_review_date") or row.get("first_study_date")
            if anchor_text:
                anchor = parse_date(anchor_text)
                model_state = FsrsState(5.0, DEFAULT_PARAMETERS[0], anchor, 0, 0)
                history_source = (
                    "formal_last_review_date" if row.get("last_review_date") else "formal_first_study_date"
                )
                calibration = "date_anchor_only_conservative"
                due_date = anchor + dt.timedelta(days=1)
                evidence_refs.append(
                    {
                        "ref": f"row://节点总表.md/{node}",
                        "sha256": row["row_sha256"],
                    }
                )
            else:
                history_source = "unknown_date_balanced_hash_spread"
                calibration = "unseen_unrated"
                due_date = smoothed[node]
        if due_date < start:
            due_date = start
        if model_state is None:
            retrievability = None
            difficulty = None
            stability = None
            last_review = None
            reps = 0
            lapses = 0
        else:
            retrievability = fsrs.retrievability(
                model_state.stability, (as_of - model_state.last_review_date).days
            )
            difficulty = model_state.difficulty
            stability = model_state.stability
            last_review = model_state.last_review_date.isoformat()
            reps = model_state.reps
            lapses = model_state.lapses
        state = {
            "schema": MEMORY_SCHEMA,
            "formal_node_id": node,
            "model": FSRS_MODEL,
            "rating_mapping_version": RATING_MAPPING_VERSION,
            "desired_retention": float(config["desired_retention"]),
            "history_source": history_source,
            "calibration_status": calibration,
            "last_review_date": last_review,
            "difficulty": None if difficulty is None else round(difficulty, 10),
            "stability_days": None if stability is None else round(stability, 10),
            "retrievability_on_as_of": (
                None if retrievability is None else round(retrievability, 10)
            ),
            "next_due_date": due_date.isoformat(),
            "latest_safe_day": config["latest_safe_day"],
            "reps": reps,
            "lapses": lapses,
            "event_history_count": len(node_history),
            "evidence_refs": evidence_refs,
        }
        state["state_sha256"] = canonical_sha256(state)
        states.append(state)
    states.sort(key=lambda row: row["formal_node_id"])
    projection = {
        "schema": "review_memory_projection_v1",
        "as_of": as_of.isoformat(),
        "model": FSRS_MODEL,
        "fsrs_parameters_sha256": canonical_sha256(config["fsrs"]["parameters"]),
        "desired_retention": float(config["desired_retention"]),
        "state_count": len(states),
        "unknown_date_smoothed_count": len(unknown_nodes),
        "states": states,
    }
    projection["projection_sha256"] = canonical_sha256(projection)
    return projection


def _state_retrievability_on(
    state: dict[str, Any], day: dt.date, fsrs: Fsrs632Pinned
) -> float | None:
    if state.get("stability_days") is None or state.get("last_review_date") is None:
        return None
    elapsed = (day - parse_date(state["last_review_date"])).days
    return fsrs.retrievability(float(state["stability_days"]), elapsed)


def build_due_run(
    cohort: dict[str, Any],
    memory: dict[str, Any],
    config: dict[str, Any],
    run_date: dt.date,
    *,
    source_bindings: list[dict[str, str]] | None = None,
    refresh_handoff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    start = parse_date(config["campaign_start"])
    exam = parse_date(config["exam_date"])
    if not start <= run_date < exam:
        raise CampaignError("run_date must be inside campaign_start..exam_date-1")
    fsrs = Fsrs632Pinned(config["fsrs"]["parameters"], float(config["desired_retention"]))
    due: list[dict[str, Any]] = []
    for state in memory["states"]:
        due_date = parse_date(state["next_due_date"])
        if due_date > run_date:
            continue
        retrievability = _state_retrievability_on(state, run_date, fsrs)
        item = {
            "formal_node_id": state["formal_node_id"],
            "due_date": due_date.isoformat(),
            "overdue_days": (run_date - due_date).days,
            "retrievability": None if retrievability is None else round(retrievability, 10),
            "history_source": state["history_source"],
            "calibration_status": state["calibration_status"],
            "recent_recurrence": int(state.get("lapses") or 0) >= 2,
            "state_sha256": state["state_sha256"],
        }
        item["item_sha256"] = canonical_sha256(item)
        due.append(item)
    due.sort(
        key=lambda row: (
            row["retrievability"] is None,
            1.0 if row["retrievability"] is None else row["retrievability"],
            -row["overdue_days"],
            row["formal_node_id"],
        )
    )
    priority = memory.get("concept_review_priority")
    if priority:
        ranks = {}
        for index, request in enumerate(priority["requests"]):
            for fid in request["candidate_formal_node_ids"]:
                ranks[fid] = min(ranks.get(fid, index), index)
        due.sort(key=lambda row: ranks.get(row["formal_node_id"], len(ranks) + len(priority["requests"])))
    segment_size = int(config["segment_size"])
    segments: list[dict[str, Any]] = []
    for offset in range(0, len(due), segment_size):
        items = due[offset : offset + segment_size]
        segment = {
            "segment_index": len(segments) + 1,
            "segment_size": len(items),
            "formal_node_ids": [item["formal_node_id"] for item in items],
            "item_set_sha256": canonical_sha256(items),
        }
        segment["segment_sha256"] = canonical_sha256(segment)
        segments.append(segment)
    scheduled_ids = [node for segment in segments for node in segment["formal_node_ids"]]
    if scheduled_ids != [item["formal_node_id"] for item in due]:
        raise CampaignError("daily due segmentation changed ordering or coverage")
    run = {
        "schema": DUE_RUN_SCHEMA,
        "policy_version": POLICY_VERSION,
        "campaign_id": config["campaign_id"],
        "run_date": run_date.isoformat(),
        "phase": (
            "consolidation"
            if run_date >= parse_date(config["consolidation_start"])
            else "main_cycle"
        ),
        "desired_retention": float(config["desired_retention"]),
        "semantic_cap": None,
        "segment_size": segment_size,
        "cohort_count": cohort["cohort_count"],
        "included_count": cohort["included_count"],
        "excluded_count": cohort["excluded_count"],
        "blocked_count": cohort["blocked_count"],
        "due_count": len(due),
        "scheduled_count": len(scheduled_ids),
        "skipped_due_count": 0,
        "coverage_identity": "due_count == scheduled_count and skipped_due_count == 0",
        "coverage_identity_holds": len(due) == len(scheduled_ids),
        "due_items": due,
        "segments": segments,
        "excluded": cohort["excluded"],
        "blocked": cohort["blocked"],
        "source_bindings": source_bindings or [],
        "cohort_sha256": cohort["cohort_sha256"],
        "admission_cutoff": cohort["admission_cutoff"],
        "admission_event_bindings": cohort["admission_event_bindings"],
        "refresh_handoff": refresh_handoff,
        "memory_projection_sha256": memory["projection_sha256"],
        "config_sha256": canonical_sha256(config),
        "formal_write_count": 0,
        "learner_evidence_write_count": 0,
    }
    if priority:
        run["concept_review_priority"] = priority
    run["run_sha256"] = canonical_sha256(run)
    return run


def build_forecast(
    cohort: dict[str, Any],
    memory: dict[str, Any],
    config: dict[str, Any],
    *,
    refresh_handoff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Forecast daily load assuming every forecasted review receives rating Good."""

    start = parse_date(config["campaign_start"])
    exam = parse_date(config["exam_date"])
    fsrs = Fsrs632Pinned(config["fsrs"]["parameters"], float(config["desired_retention"]))
    simulated: dict[str, dict[str, Any]] = {
        row["formal_node_id"]: dict(row) for row in memory["states"]
    }
    days: list[dict[str, Any]] = []
    cursor = start
    while cursor < exam:
        due_ids = sorted(
            node
            for node, state in simulated.items()
            if parse_date(state["next_due_date"]) <= cursor
        )
        phase = "consolidation" if cursor >= parse_date(config["consolidation_start"]) else "main_cycle"
        days.append(
            {
                "date": cursor.isoformat(),
                "phase": phase,
                "due_count": len(due_ids),
                "segment_count": math.ceil(len(due_ids) / int(config["segment_size"])),
                "due_set_sha256": canonical_sha256(due_ids),
            }
        )
        for node in due_ids:
            state = simulated[node]
            if state.get("stability_days") is None or state.get("last_review_date") is None:
                next_state = fsrs.initial(cursor, 3)
            else:
                prior = FsrsState(
                    float(state["difficulty"]),
                    float(state["stability_days"]),
                    parse_date(state["last_review_date"]),
                    int(state["reps"]),
                    int(state["lapses"]),
                )
                next_state = fsrs.review(prior, cursor, 3)
            state["difficulty"] = next_state.difficulty
            state["stability_days"] = next_state.stability
            state["last_review_date"] = cursor.isoformat()
            state["reps"] = next_state.reps
            state["lapses"] = next_state.lapses
            state["next_due_date"] = (
                cursor + dt.timedelta(days=fsrs.interval(next_state.stability))
            ).isoformat()
        cursor += dt.timedelta(days=1)
    forecast = {
        "schema": FORECAST_SCHEMA,
        "campaign_id": config["campaign_id"],
        "campaign_start": config["campaign_start"],
        "exam_date": config["exam_date"],
        "consolidation_start": config["consolidation_start"],
        "latest_safe_day": config["latest_safe_day"],
        "desired_retention": float(config["desired_retention"]),
        "assumption": "every forecasted due review receives FSRS rating Good on its due day",
        "semantic_cap": None,
        "segment_size": int(config["segment_size"]),
        "cohort_partition": {
            key: cohort[key]
            for key in ("cohort_count", "included_count", "excluded_count", "blocked_count")
        },
        "day_count": len(days),
        "days": days,
        "cohort_sha256": cohort["cohort_sha256"],
        "admission_cutoff": cohort["admission_cutoff"],
        "admission_event_bindings": cohort["admission_event_bindings"],
        "memory_projection_sha256": memory["projection_sha256"],
        "config_sha256": canonical_sha256(config),
        "formal_write_count": 0,
        "learner_evidence_write_count": 0,
    }
    forecast_content_sha256 = canonical_sha256(forecast)
    receipt = {
        "schema": "review_campaign_forecast_refresh_receipt_v1",
        "campaign_id": config["campaign_id"],
        "admission_cutoff": cohort["admission_cutoff"],
        "admission_event_binding_set_sha256": canonical_sha256(
            cohort["admission_event_bindings"]
        ),
        "trigger_admission_event_id": (
            None
            if refresh_handoff is None
            else refresh_handoff["trigger_admission_event_id"]
        ),
        "trigger_admission_event_sha256": (
            None
            if refresh_handoff is None
            else refresh_handoff["trigger_admission_event_sha256"]
        ),
        "refresh_handoff_sha256": (
            None if refresh_handoff is None else refresh_handoff["handoff_sha256"]
        ),
        "cohort_sha256": cohort["cohort_sha256"],
        "memory_projection_sha256": memory["projection_sha256"],
        "config_sha256": canonical_sha256(config),
        "forecast_content_sha256": forecast_content_sha256,
        "formal_write_count": 0,
        "learner_evidence_write_count": 0,
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    forecast["refresh_receipt"] = receipt
    forecast["forecast_sha256"] = canonical_sha256(forecast)
    return forecast


def _source_bindings(repo: Path, admission_ledger: Path | None) -> list[dict[str, str]]:
    paths = [
        repo / "节点总表.md",
        repo / "原题复做轨总表.md",
        repo / "wiki/study_vaults/408-full/state/review-loop/events.jsonl",
        Path(__file__).resolve(),
    ]
    if admission_ledger is not None and admission_ledger.exists():
        paths.append(admission_ledger)
    result: list[dict[str, str]] = []
    for path in paths:
        resolved = path.resolve(strict=True)
        try:
            ref = resolved.relative_to(repo.resolve(strict=True)).as_posix()
        except ValueError:
            ref = str(resolved)
        result.append({"ref": ref, "sha256": file_sha256(resolved)})
    return result


def load_projection(
    repo: Path,
    config_path: Path | None,
    as_of: dt.date,
    admission_ledger: Path | None = None,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    list[dict[str, str]],
    dict[str, Any],
]:
    repo = repo.resolve(strict=True)
    if admission_ledger is None:
        admission_ledger = repo / DEFAULT_ADMISSION_REL
    elif not admission_ledger.is_absolute():
        admission_ledger = repo / admission_ledger
    config = read_config(config_path)
    master = repo / "节点总表.md"
    track = repo / "原题复做轨总表.md"
    ledger = repo / "wiki/study_vaults/408-full/state/review-loop/events.jsonl"
    admissions = read_admission_ledger(admission_ledger)
    # The same accepted ledger must produce the same projection in a checkout
    # and an isolated published replay. Host-specific absolute paths are not
    # evidence identities and must not enter cohort/run hashes.
    try:
        admissions["path"] = admission_ledger.relative_to(repo).as_posix()
    except ValueError:
        pass  # Explicit external ledgers retain their exact supplied identity.
    cohort = parse_cohort(master, config, as_of, admissions, track)
    events = read_events(ledger)
    memory = project_memory_states(cohort, events, config, as_of)
    from concept_review_priority_408 import load_priority
    _, point_rows = _markdown_rows(master)
    memory["concept_review_priority"] = load_priority(repo, {row["ID"]: row for row in point_rows}, as_of.isoformat())
    return config, cohort, memory, _source_bindings(repo, admission_ledger), admissions


def load_frozen_run(path: Path, expected_date: dt.date) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CampaignError(f"invalid frozen run JSON: {path}") from exc
    if not isinstance(value, dict) or value.get("schema") != DUE_RUN_SCHEMA:
        raise CampaignError(f"frozen run schema must be {DUE_RUN_SCHEMA}")
    if value.get("run_date") != expected_date.isoformat():
        raise CampaignError("frozen run date mismatch")
    expected = canonical_sha256({key: item for key, item in value.items() if key != "run_sha256"})
    if value.get("run_sha256") != expected:
        raise CampaignError("frozen run hash mismatch")
    return {**value, "frozen_reopen": True}


def _print_json(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("forecast", "dry-run"):
        child = sub.add_parser(name)
        child.add_argument("--repo", type=Path, default=Path.cwd())
        child.add_argument("--config", type=Path)
        child.add_argument("--as-of", default="2026-08-31")
        child.add_argument(
            "--admission-ledger",
            type=Path,
            help="append-only review campaign admission events JSONL",
        )
        child.add_argument(
            "--refresh-handoff",
            type=Path,
            help="backflow refresh handoff bound to an admission event/hash",
        )
        if name == "dry-run":
            child.add_argument("--date", required=True)
            child.add_argument(
                "--frozen-run",
                type=Path,
                help="reopen an already frozen run without absorbing later admissions",
            )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        as_of = parse_date(args.as_of, "as_of")
        if args.command == "dry-run" and args.frozen_run is not None:
            if args.refresh_handoff is not None:
                raise CampaignError("a frozen run cannot accept a refresh handoff")
            _print_json(load_frozen_run(args.frozen_run, parse_date(args.date, "run_date")))
            return 0
        config, cohort, memory, bindings, admissions = load_projection(
            args.repo, args.config, as_of, args.admission_ledger
        )
        handoff = validate_refresh_handoff(args.refresh_handoff, admissions)
        if args.command == "forecast":
            forecast = build_forecast(
                cohort, memory, config, refresh_handoff=handoff
            )
            _print_json(forecast)
        else:
            run_date = parse_date(args.date, "run_date")
            _print_json(
                build_due_run(
                    cohort,
                    memory,
                    config,
                    run_date,
                    source_bindings=bindings,
                    refresh_handoff=handoff,
                )
            )
        return 0
    except CampaignError as exc:
        _print_json(
            {
                "status": "unavailable",
                "error": str(exc),
                "formal_write_count": 0,
                "learner_evidence_write_count": 0,
            }
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
