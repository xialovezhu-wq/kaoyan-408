#!/usr/bin/env python3
"""Seal Sol's nightly curation decision without granting formal-write authority.

The command deliberately sits between the one-shot Luna consumer and the existing
batch-size-1 formal intake pipeline.  It reads the frozen capture projection,
revalidates an optional Luna package, records Sol's field-level judgment, and emits
a content-addressed canonical package whose private binding points back to that
decision.  It never invokes Luna and never calls a formal writer.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import intake_fact_capture_408 as capture_ledger  # noqa: E402
import current_question_evidence_408 as current_evidence  # noqa: E402
from intake_lib_408 import MASTER_COLS, iter_table_rows  # noqa: E402


DECISION_REQUEST_SCHEMA = "sol_curation_decision_request_v1"
DECISION_SCHEMA = "sol_curation_decision_v1"
PACKAGE_BINDING_SCHEMA = "sol_curation_package_binding_v1"
POINTER_SCHEMA = "sol_curation_decision_pointer_v1"
LUNA_PACKAGE_V1 = "study-intake-preprocess-package-v1"
LUNA_PACKAGE_V2 = "study-intake-preprocess-package-v2"
LUNA_PACKAGE_V2_KEYS = {
    "schema_version",
    "package_id",
    "report_id",
    "subject",
    "capture_id",
    "study_date",
    "created_at",
    "input_fingerprint",
    "input_binding",
    "processing_contract_sha256",
    "processing_fingerprint",
    "evidence_manifest_sha256",
    "evidence_bundle_sha256",
    "report_json_ref",
    "report_json_sha256",
    "report_markdown_ref",
    "report_markdown_sha256",
    "renderer_build_sha256",
    "quality_receipt_sha256",
    "pipeline_status",
    "model",
    "reasoning_effort",
    "stage_receipts",
    "quality_receipt",
    "allowed_evidence_refs",
    "formal_write_count",
}
LUNA_CONSUMER_V2_KEYS = {
    "schema_version",
    "status",
    "subject",
    "capture_id",
    "study_date",
    "package_path",
    "package_id",
    "package_sha256",
    "input_fingerprint",
    "validation",
    "adoption_token",
    "formal_write_count",
    "publication_id",
    "pipeline_status",
    "model",
    "reasoning_effort",
    "report_id",
    "report_json_ref",
    "report_json_sha256",
    "report_markdown_ref",
    "report_markdown_sha256",
    "renderer_build_sha256",
    "processing_contract_sha256",
    "processing_fingerprint",
    "evidence_manifest_sha256",
    "evidence_bundle_sha256",
    "quality_receipt_sha256",
    "batch_id",
    "capture_set_sha256",
    "capture_payload_sha256",
    "authority_verified",
    "authority_unit_sha256",
    "authority_lease_fence",
    "authority_receipt_sha256",
    "authority_completion_sha256",
    "authority_release_id",
}
LUNA_CONSUMER_STATUSES = {
    "ready",
    "single_pass_degraded",
    "absent",
    "stale",
    "failed",
    "disabled",
    "unavailable",
    "timeout",
}
LUNA_USABLE_STATUSES = {"ready", "ready_legacy"}
LUNA_TOKEN_STATUSES = {"ready"}
FIELD_ACTIONS = {"adopt", "modify", "reject"}
INTENDED_TERMINALS = {"apply", "already_current", "needs_user"}
TERMINAL_OUTCOMES = {"curated", "already_current", "needs_user", "failed"}
CURATABLE_FIELDS = {
    "safe_summary",
    "key_parameters",
    "question_type",
    "core_point",
    "module",
    "main_knowledge",
    "sub_knowledge",
    "hit_knowledge",
    "fuzzy_concepts",
    "error_tags",
    "redo_first_action",
    "relation_candidates",
    "topic_chain_candidates",
    "review_unit",
    "ask_type",
}
LUNA_V2_FIELD_MAP = {
    "safe_summary": "safe_summary",
    "key_parameters": "key_parameters",
    "question_type": "question_type",
    "main_knowledge": "main_knowledge",
    "secondary_knowledge": "sub_knowledge",
    "hit_knowledge": "hit_knowledge",
    "fuzzy_concepts": "fuzzy_concepts",
    "error_tags": "error_tags",
    "redo_first_action": "redo_first_action",
    "topic_intents": "topic_chain_candidates",
    "relationship_search_intents": "relation_candidates",
}
CLAIM_KEYS = {
    "claim_type",
    "text",
    "evidence_refs",
    "confidence",
    "counterevidence_or_boundary",
    "sol_verification_action",
}
FIELD_DECISION_KEYS = {
    "proposal_sha256",
    "field",
    "action",
    "luna_value",
    "sol_value",
    "evidence_refs",
    "basis",
    "counterevidence",
    "confidence",
    "unresolved",
}
IMMUTABLE_FIELDS = {
    "formal_id",
    "source_id",
    "detail_entry",
    "details_id",
    "study_date",
    "first_done_date",
    "latest_review_date",
    "latest_error_record",
    "user_error_entry",
    "first_action",
    "hint_result",
    "correctness",
    "attachment_registry",
    "mastery",
    "schedule_state",
    "receipt",
    "wal",
    "mode",
}
FORMAL_ID_RE = re.compile(r"^(?:DS|CO|OS|CN)_(?:\d{4}|UNK)_\d{3}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SAFE_FIELD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,119}$")
DEFAULT_RUNTIME_ROOT = Path(
    "~/.codex/study-intake-preprocessor/private/sol-curation"
).expanduser().resolve()
DEFAULT_FORMAL_RUNTIME_ROOT = Path(
    "~/.codex/kaoyan-408-intake"
).expanduser().resolve()
DEFAULT_PRIVATE_EVIDENCE_ROOT = current_evidence.DEFAULT_PRIVATE_ROOT
CURRENT_PREPROCESSOR_POINTER = Path(
    "~/.codex/study-intake-preprocessor/current"
).expanduser()
PREPROCESSOR_RELEASE_SCHEMA = "study-intake-preprocessor-release-v2"
PREPROCESSOR_RELEASE_MANIFEST_KEYS = {
    "schema_version",
    "release_id",
    "release_profile",
    "source_release_id",
    "source_tree_sha256",
    "runtime_data_root",
    "source_files",
    "source_modes",
    "source_owners",
    "generated_files",
    "generated_modes",
    "generated_owners",
    "directories",
    "manifest_metadata",
    "service_topology",
    "component_inventory",
    "model_contract",
    "test_results",
    "formal_write_count",
}
PREPROCESSOR_MODEL_CONTRACT = {
    "model": "gpt-5.6-luna",
    "reasoning_effort": "max",
}
PREPROCESSOR_SERVICE_TOPOLOGY = [
    {
        "name": "math",
        "label": "com.xiazhibin.study-intake-preprocessor.math",
        "template": "launchagents/com.xiazhibin.study-intake-preprocessor.math.plist",
    },
    {
        "name": "cs408",
        "label": "com.xiazhibin.study-intake-preprocessor.cs408",
        "template": "launchagents/com.xiazhibin.study-intake-preprocessor.cs408.plist",
    },
    {
        "name": "english",
        "label": "com.xiazhibin.study-intake-preprocessor.english",
        "template": "launchagents/com.xiazhibin.study-intake-preprocessor.english.plist",
    },
    {
        "name": "dashboard",
        "label": "com.xiazhibin.study-intake-dashboard",
        "template": "launchagents/com.xiazhibin.study-intake-dashboard.plist",
    },
]
CAPTURE_EVENTS_REL = Path(
    "wiki/study_vaults/408-full/state/intake-curation/events.jsonl"
)
MAX_JSON_BYTES = 8 * 1024 * 1024
REPORT_REF_PREFIX = "study-intake-report://sha256/"
REPORT_MARKDOWN_REF_PREFIX = "study-intake-report-markdown://sha256/"


class SolCurationError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _release_id_for_manifest(manifest: Mapping[str, Any]) -> str:
    payload = {
        "schema_version": PREPROCESSOR_RELEASE_SCHEMA,
        "source_files": manifest.get("source_files"),
        "source_modes": manifest.get("source_modes"),
        "source_owners": manifest.get("source_owners"),
        "runtime_data_root": manifest.get("runtime_data_root"),
        "release_profile": manifest.get("release_profile"),
        "source_release_id": manifest.get("source_release_id"),
        "model_contract": manifest.get("model_contract"),
        "test_results": manifest.get("test_results"),
    }
    raw = (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _release_relative(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise SolCurationError("current preprocessor release identity is invalid")
    path = Path(value)
    if (
        path.is_absolute()
        or path == Path(".")
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise SolCurationError("current preprocessor release identity is invalid")
    return path


def _release_expected_directories(files: set[str]) -> set[str]:
    result: set[str] = set()
    for relative in files:
        parent = Path(relative).parent
        while parent != Path("."):
            result.add(parent.as_posix())
            parent = parent.parent
    return result


def _release_component_inventory(
    source_files: Mapping[str, str],
    generated_files: Mapping[str, str],
    service_topology: list[Mapping[str, str]],
) -> dict[str, dict[str, str]]:
    programs = {
        path: digest
        for path, digest in source_files.items()
        if Path(path).parts[0] in {"bin", "lib", "dashboard", "scripts"}
    }
    schemas = {
        path: digest
        for path, digest in source_files.items()
        if Path(path).parts[0] == "schemas"
    }
    evidence_rules = {
        path: digest
        for path, digest in schemas.items()
        if any(
            token in Path(path).name
            for token in ("evidence", "controlled-contract", "trace-supplement")
        )
    }
    return {
        "programs": programs,
        "configuration": {
            "config.example.json": source_files["config.example.json"],
            **dict(sorted(generated_files.items())),
        },
        "evidence_rules": evidence_rules,
        "data_formats": schemas,
        "service_configurations": {
            str(service["template"]): source_files[str(service["template"])]
            for service in service_topology
        },
    }


def _release_tests_passed(value: Any) -> bool:
    if not isinstance(value, Mapping) or set(value) != {
        "schema_version",
        "status",
        "suites",
        "formal_surface_gate",
        "real_model_call_count",
        "formal_write_count",
    }:
        return False
    suites = value.get("suites")
    formal = value.get("formal_surface_gate")
    if (
        value.get("schema_version") != "study-intake-release-test-results-v1"
        or value.get("status") != "passed"
        or value.get("real_model_call_count") != 0
        or value.get("formal_write_count") != 0
        or not isinstance(suites, list)
        or len(suites) != 2
        or not isinstance(formal, Mapping)
    ):
        return False
    for expected_name, suite in zip(("core", "dashboard"), suites):
        if (
            not isinstance(suite, Mapping)
            or set(suite) != {"name", "status", "test_count", "skipped_count"}
            or suite.get("name") != expected_name
            or suite.get("status") != "passed"
            or isinstance(suite.get("test_count"), bool)
            or not isinstance(suite.get("test_count"), int)
            or suite["test_count"] < 1
            or isinstance(suite.get("skipped_count"), bool)
            or not isinstance(suite.get("skipped_count"), int)
            or not 0 <= suite["skipped_count"] <= suite["test_count"]
        ):
            return False
    expected_formal_keys = {
        "schema_version",
        "status",
        "verification_status",
        "baseline_file_sha256",
        "config_file_sha256",
        "baseline_manifest_sha256",
        "current_manifest_sha256",
        "differences",
    }
    return bool(
        set(formal) == expected_formal_keys
        and formal.get("schema_version")
        == "study-intake-release-formal-surface-gate-v1"
        and formal.get("status") == "passed"
        and formal.get("verification_status") == "unchanged"
        and formal.get("differences") == {}
        and all(
            isinstance(formal.get(field), str)
            and SHA256_RE.fullmatch(str(formal[field])) is not None
            for field in (
                "baseline_file_sha256",
                "config_file_sha256",
                "baseline_manifest_sha256",
                "current_manifest_sha256",
            )
        )
        and formal.get("current_manifest_sha256")
        == formal.get("baseline_manifest_sha256")
    )


def _substitute_release_template(value: Any, replacements: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _substitute_release_template(nested, replacements)
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [_substitute_release_template(nested, replacements) for nested in value]
    if isinstance(value, str):
        result = value
        for marker, replacement in replacements.items():
            result = result.replace(marker, replacement)
        return result
    return value


def _resolve_current_preprocessor_context(
    preprocessor_root: str | Path,
) -> dict[str, Any]:
    """Bind Sol to the exact immutable release selected by ``current``."""

    pointer = CURRENT_PREPROCESSOR_POINTER
    try:
        if not pointer.is_symlink():
            raise SolCurationError("preprocessor current pointer is not a symlink")
        active_root = pointer.resolve(strict=True)
        supplied_root = Path(preprocessor_root).expanduser().resolve(strict=True)
    except OSError as exc:
        raise SolCurationError("preprocessor current release is unavailable") from exc
    if supplied_root != active_root:
        raise SolCurationError("preprocessor root is not the current release")

    manifest_path = active_root / "release.json"
    config_path = active_root / "config.json"
    try:
        manifest_bytes = manifest_path.read_bytes()
        config_bytes = config_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        config = json.loads(config_bytes)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SolCurationError("current preprocessor release metadata is invalid") from exc
    if not isinstance(manifest, dict) or not isinstance(config, dict):
        raise SolCurationError("current preprocessor release metadata is invalid")
    release_id = str(manifest.get("release_id") or "")
    source_files = manifest.get("source_files")
    source_modes = manifest.get("source_modes")
    source_owners = manifest.get("source_owners")
    generated_files = manifest.get("generated_files")
    generated_modes = manifest.get("generated_modes")
    generated_owners = manifest.get("generated_owners")
    directories = manifest.get("directories")
    manifest_metadata = manifest.get("manifest_metadata")
    service_topology = manifest.get("service_topology")
    component_inventory = manifest.get("component_inventory")
    model_contract = manifest.get("model_contract")
    test_results = manifest.get("test_results")
    runtime_value = manifest.get("runtime_data_root")
    if (
        set(manifest) != PREPROCESSOR_RELEASE_MANIFEST_KEYS
        or manifest.get("schema_version") != PREPROCESSOR_RELEASE_SCHEMA
        or not SHA256_RE.fullmatch(release_id)
        or manifest.get("source_tree_sha256") != release_id
        or active_root.name != release_id
        or manifest.get("release_profile") != "concurrent_v2"
        or manifest.get("source_release_id") is not None
        or manifest.get("formal_write_count") != 0
        or not isinstance(source_files, dict)
        or not isinstance(source_modes, dict)
        or not isinstance(source_owners, dict)
        or set(source_files) != set(source_modes)
        or set(source_files) != set(source_owners)
        or not isinstance(generated_files, dict)
        or set(generated_files) != {"config.json"}
        or generated_modes != {"config.json": 0o400}
        or not isinstance(generated_owners, dict)
        or set(generated_owners) != {"config.json"}
        or not isinstance(directories, dict)
        or not isinstance(manifest_metadata, dict)
        or service_topology != PREPROCESSOR_SERVICE_TOPOLOGY
        or model_contract != PREPROCESSOR_MODEL_CONTRACT
        or not _release_tests_passed(test_results)
        or not isinstance(runtime_value, str)
        or not runtime_value
        or _release_id_for_manifest(manifest) != release_id
    ):
        raise SolCurationError("current preprocessor release identity is invalid")
    try:
        expected_components = _release_component_inventory(
            source_files, generated_files, service_topology
        )
        source_relatives = {
            relative: _release_relative(relative) for relative in source_files
        }
        generated_relatives = {
            relative: _release_relative(relative) for relative in generated_files
        }
    except (KeyError, TypeError, SolCurationError) as exc:
        raise SolCurationError(
            "current preprocessor release identity is invalid"
        ) from exc
    if component_inventory != expected_components:
        raise SolCurationError("current preprocessor release identity is invalid")
    dispatch_relative = "lib/concurrent_dispatch.py"
    if dispatch_relative not in source_relatives:
        raise SolCurationError("current preprocessor release identity is invalid")
    dispatch_path = active_root / source_relatives[dispatch_relative]
    try:
        active_info = active_root.lstat()
        manifest_info = manifest_path.lstat()
    except OSError as exc:
        raise SolCurationError("current preprocessor release files are unavailable") from exc
    if (
        not stat.S_ISDIR(active_info.st_mode)
        or stat.S_IMODE(active_info.st_mode) != 0o555
        or not stat.S_ISREG(manifest_info.st_mode)
        or stat.S_IMODE(manifest_info.st_mode) != 0o444
        or manifest_metadata
        != {
            "uid": manifest_info.st_uid,
            "gid": manifest_info.st_gid,
            "mode": 0o444,
            "type": "file",
        }
        or manifest_bytes
        != (
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    ):
        raise SolCurationError("current preprocessor release files drifted")
    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    try:
        for current, names, filenames in os.walk(
            active_root, topdown=True, followlinks=False
        ):
            current_path = Path(current)
            for name in names:
                path = current_path / name
                info = path.lstat()
                if not stat.S_ISDIR(info.st_mode):
                    raise SolCurationError(
                        "current preprocessor release files drifted"
                    )
                actual_directories.add(path.relative_to(active_root).as_posix())
            for name in filenames:
                path = current_path / name
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode):
                    raise SolCurationError(
                        "current preprocessor release files drifted"
                    )
                actual_files.add(path.relative_to(active_root).as_posix())
    except OSError as exc:
        raise SolCurationError(
            "current preprocessor release files are unavailable"
        ) from exc
    expected_files = set(source_files) | set(generated_files) | {"release.json"}
    expected_directories = _release_expected_directories(expected_files)
    if (
        actual_files != expected_files
        or actual_directories != expected_directories
        or set(directories) != expected_directories
    ):
        raise SolCurationError("current preprocessor release files drifted")
    for relative in sorted(expected_directories):
        info = (active_root / relative).lstat()
        expected = directories.get(relative)
        if (
            not isinstance(expected, Mapping)
            or expected
            != {
                "uid": info.st_uid,
                "gid": info.st_gid,
                "mode": 0o555,
                "type": "directory",
            }
            or stat.S_IMODE(info.st_mode) != 0o555
        ):
            raise SolCurationError("current preprocessor release files drifted")
    for values, modes, owners, relatives in (
        (source_files, source_modes, source_owners, source_relatives),
        (generated_files, generated_modes, generated_owners, generated_relatives),
    ):
        for relative, path_relative in relatives.items():
            digest = values.get(relative)
            mode = modes.get(relative)
            owner = owners.get(relative)
            path = active_root / path_relative
            try:
                info = path.lstat()
            except OSError as exc:
                raise SolCurationError(
                    "current preprocessor release files are unavailable"
                ) from exc
            if (
                not isinstance(digest, str)
                or SHA256_RE.fullmatch(digest) is None
                or isinstance(mode, bool)
                or not isinstance(mode, int)
                or mode & 0o222
                or not isinstance(owner, Mapping)
                or owner
                != {"uid": info.st_uid, "gid": info.st_gid, "type": "file"}
                or not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != mode
                or sha256_file(path) != digest
            ):
                raise SolCurationError("current preprocessor release files drifted")
    try:
        template = json.loads(
            (active_root / "config.example.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SolCurationError(
            "current preprocessor release files are unavailable"
        ) from exc
    rendered_config = _substitute_release_template(
        template,
        {
            "${RELEASE_ROOT}": str(active_root),
            "${RUNTIME_DATA_ROOT}": str(runtime_value),
        },
    )
    if config_bytes != canonical_bytes(rendered_config) + b"\n":
        raise SolCurationError("current preprocessor config binding is invalid")
    release_config = config.get("release")
    model_config = config.get("model")
    try:
        runtime_root = Path(runtime_value).expanduser().resolve(strict=True)
    except OSError as exc:
        raise SolCurationError("current preprocessor runtime root is unavailable") from exc
    if (
        config.get("runtime_root") != runtime_value
        or config.get("schema_version") != "study-intake-preprocessor-config-v1"
        or not isinstance(release_config, Mapping)
        or release_config.get("manifest_path") != str(manifest_path)
        or not isinstance(model_config, Mapping)
        or model_config.get("model") != "gpt-5.6-luna"
        or model_config.get("reasoning_effort") != "max"
        or not isinstance(config.get("dispatch"), Mapping)
        or config["dispatch"].get("authority_required") is not True
    ):
        raise SolCurationError("current preprocessor config binding is invalid")
    return {
        "release_root": active_root,
        "runtime_root": runtime_root,
        "release_id": release_id,
        "dispatch_path": dispatch_path,
    }


def _load_current_dispatch_module(context: Mapping[str, Any]) -> Any:
    path = Path(context["dispatch_path"])
    module_name = f"_study_intake_dispatch_{context['release_id']}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise SolCurationError("current dispatch verifier cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise SolCurationError("current dispatch verifier cannot be loaded") from exc
    finally:
        sys.modules.pop(module_name, None)
    if (
        getattr(module, "REQUIRED_MODEL", None) != "gpt-5.6-luna"
        or getattr(module, "REQUIRED_REASONING_EFFORT", None) != "max"
        or not callable(getattr(module, "verify_authoritative_completion", None))
    ):
        raise SolCurationError("current dispatch verifier contract drifted")
    return module


def _verify_current_authoritative_completion(
    context: Mapping[str, Any], capture_id: str
) -> dict[str, Any]:
    expected_release_root = Path(context["release_root"])
    try:
        if CURRENT_PREPROCESSOR_POINTER.resolve(strict=True) != expected_release_root:
            raise SolCurationError("preprocessor current release changed")
    except OSError as exc:
        raise SolCurationError("preprocessor current release changed") from exc
    runtime_root = Path(context["runtime_root"])
    state_root = runtime_root / "dispatch" / "state"
    authority_key = state_root / "authority.key"
    lock_path = state_root / "dispatch.lock"
    try:
        key_info = authority_key.lstat()
        lock_info = lock_path.lstat()
    except OSError as exc:
        raise SolCurationError("authoritative dispatch state is missing") from exc
    if (
        authority_key.is_symlink()
        or not stat.S_ISREG(key_info.st_mode)
        or stat.S_IMODE(key_info.st_mode) != 0o600
        or key_info.st_size != 32
        or lock_path.is_symlink()
        or not stat.S_ISREG(lock_info.st_mode)
    ):
        raise SolCurationError("authoritative dispatch state is unsafe")
    module = _load_current_dispatch_module(context)
    try:
        verified = module.verify_authoritative_completion(
            runtime_root,
            "cs408",
            capture_id,
            expected_release_id=str(context["release_id"]),
        )
    except Exception as exc:
        raise SolCurationError("authoritative dispatch verification failed") from exc
    try:
        if CURRENT_PREPROCESSOR_POINTER.resolve(strict=True) != expected_release_root:
            raise SolCurationError("preprocessor current release changed")
    except OSError as exc:
        raise SolCurationError("preprocessor current release changed") from exc
    if not isinstance(verified, dict):
        raise SolCurationError("authoritative dispatch verification returned invalid data")
    return verified


def repo_key(repo: Path) -> str:
    return hashlib.sha256(str(repo.resolve()).encode("utf-8")).hexdigest()[:20]


def require_sha(value: Any, label: str) -> str:
    text = str(value or "")
    if not SHA256_RE.fullmatch(text):
        raise SolCurationError(f"{label} must be lowercase SHA-256")
    return text


def require_date(value: Any, label: str = "study_date") -> str:
    text = str(value or "")
    if not DATE_RE.fullmatch(text):
        raise SolCurationError(f"{label} must be YYYY-MM-DD")
    return text


def require_nonempty(value: Any, label: str, *, maximum: int = 4000) -> str:
    text = str(value or "").strip()
    if not text or len(text.encode("utf-8")) > maximum:
        raise SolCurationError(f"{label} is missing or too large")
    return text


def require_answer_safe(value: Any, label: str) -> Any:
    """Fail closed on answer-bearing material in the formalization layer."""

    if isinstance(value, Mapping):
        return {
            str(key): require_answer_safe(nested, f"{label}.{key}")
            for key, nested in value.items()
        }
    if isinstance(value, list):
        return [
            require_answer_safe(nested, f"{label}[{index}]")
            for index, nested in enumerate(value)
        ]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value).strip()
    fails, warns = capture_ledger.scan_leak(text)
    if fails or warns:
        raise SolCurationError(
            f"{label} failed answer-safety scan: {';'.join(fails + warns)}"
        )
    return text


def _canonical_value_matches(value: Any, expected: Any) -> bool:
    """A claim may be the whole scalar or one exact member of a list field."""

    if value == expected:
        return True
    return isinstance(value, list) and any(item == expected for item in value)


def _read_capture_events(repo: Path) -> list[dict[str, Any]]:
    ledger = capture_ledger.capture_root(repo) / "events.jsonl"
    try:
        raw = ledger.read_bytes()
    except OSError as exc:
        raise SolCurationError("capture truth ledger is unreadable") from exc
    events: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(raw.splitlines(keepends=True), 1):
        if not raw_line.strip():
            continue
        if not raw_line.endswith(b"\n"):
            raise SolCurationError("capture truth ledger has an incomplete tail")
        try:
            event = json.loads(raw_line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise SolCurationError(
                f"capture truth ledger line {line_number} is invalid"
            ) from exc
        if not isinstance(event, dict) or event.get("schema") != capture_ledger.EVENT_SCHEMA:
            raise SolCurationError(
                f"capture truth ledger line {line_number} has the wrong schema"
            )
        events.append(event)
    return events


def _known_evidence_paths(value: Any, prefix: str) -> set[str]:
    result: set[str] = set()
    if isinstance(value, Mapping):
        for key in sorted(value):
            result.update(_known_evidence_paths(value[key], f"{prefix}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.update(_known_evidence_paths(item, f"{prefix}[{index}]"))
    elif value not in (None, "", [], {}):
        result.add(prefix)
    return result


def _detect_image_integrity(data: bytes) -> tuple[str, str]:
    try:
        return current_evidence.validate_image_bytes(data)
    except current_evidence.ImageIntegrityError as exc:
        if exc.code == "signature_unsupported":
            message = "direct current-question image magic is invalid"
        elif exc.code == "decoder_unavailable":
            message = "direct current-question image decoder is unavailable"
        else:
            message = "direct current-question image integrity is invalid"
        raise SolCurationError(message) from exc


def _verify_v3_attachment_integrity(
    bundle: Mapping[str, Any], *, private_evidence_root: Path
) -> None:
    rows = bundle.get("attachment_objects")
    if not isinstance(rows, list) or len(rows) > current_evidence.MAX_ATTACHMENTS:
        raise SolCurationError("direct current-question attachment list is invalid")
    for expected_ordinal, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise SolCurationError("direct current-question attachment row is invalid")
        digest = require_sha(row.get("sha256"), "direct attachment hash")
        path = private_evidence_root / "attachments" / f"{digest}.bin"
        try:
            info = path.lstat()
            data = path.read_bytes()
        except OSError as exc:
            raise SolCurationError("direct current-question attachment is unreadable") from exc
        if (
            path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or row.get("ordinal") != expected_ordinal
            or row.get("original_bytes_ref")
            != current_evidence.ATTACHMENT_LOCATOR_PREFIX + digest
            or row.get("byte_count") != len(data)
            or hashlib.sha256(data).hexdigest() != digest
        ):
            raise SolCurationError("direct current-question attachment binding drifted")
        detected_format, detected_mime = _detect_image_integrity(data)
        if (
            row.get("detected_format") != detected_format
            or row.get("detected_mime_type") != detected_mime
            or row.get("declared_mime_type") != detected_mime
        ):
            raise SolCurationError("direct current-question image declaration drifted")


def _reopen_direct_evidence(
    frozen: Mapping[str, Any], *, private_evidence_root: Path
) -> dict[str, Any]:
    capture = frozen["capture"]
    refs = capture.get("stable_evidence_refs") or []
    current_refs = [
        ref
        for ref in refs
        if isinstance(ref, dict)
        and ref.get("kind") == capture_ledger.CURRENT_QUESTION_EVIDENCE_REF_KIND
    ]
    capture_paths = _known_evidence_paths(capture, "capture")
    if not current_refs:
        return {
            "status": "legacy_evidence",
            "evidence_locator": None,
            "evidence_manifest_sha256": None,
            "evidence_bundle_sha256": None,
            "source_binding_sha256": None,
            "context_id": None,
            "known_evidence_refs": capture_paths,
            "known_evidence_refs_sha256": sha256_value(sorted(capture_paths)),
        }
    if len(current_refs) != 1 or len(refs) != 1:
        raise SolCurationError("current-question capture has ambiguous direct evidence")
    ref = current_refs[0]
    locator = str(ref.get("locator") or "")
    manifest_sha = require_sha(ref.get("sha256"), "direct evidence manifest hash")
    expected_locator = current_evidence.LOCATOR_PREFIX + manifest_sha
    if locator != expected_locator:
        raise SolCurationError("direct evidence locator/hash mismatch")
    try:
        manifest, bundle = current_evidence.read_bundle(
            locator, private_root=private_evidence_root
        )
    except current_evidence.CurrentQuestionEvidenceError as exc:
        raise SolCurationError(f"direct current-question evidence invalid: {exc}") from exc
    if bundle.get("schema_version") == current_evidence.BUNDLE_SCHEMA_V3:
        _verify_v3_attachment_integrity(
            bundle,
            private_evidence_root=private_evidence_root.expanduser().resolve(strict=True),
        )
    source_facts = capture.get("source_facts") or {}
    if (
        manifest.get("study_date") != frozen["study_date"]
        or bundle.get("study_date") != frozen["study_date"]
        or manifest.get("source_id") != source_facts.get("source_id")
        or bundle.get("source_id") != source_facts.get("source_id")
        or manifest.get("context_id") != source_facts.get("details_id")
        or bundle.get("context_id") != source_facts.get("details_id")
    ):
        raise SolCurationError("direct evidence source/date/context binding mismatch")
    bundle_sha = require_sha(manifest.get("object_sha256"), "direct evidence bundle hash")
    if current_evidence.bundle_evidence_status(bundle) != "ready":
        raise SolCurationError("direct current-question evidence is pending")
    bundle_paths = _known_evidence_paths(bundle, "current_question_evidence")
    known = capture_paths | bundle_paths
    return {
        "status": (
            "verified_v3"
            if bundle.get("schema_version") == current_evidence.BUNDLE_SCHEMA_V3
            else "verified_legacy"
        ),
        "evidence_locator": locator,
        "evidence_manifest_sha256": manifest_sha,
        "evidence_bundle_sha256": bundle_sha,
        "source_binding_sha256": require_sha(
            manifest.get("source_binding_sha256"), "direct source binding hash"
        ),
        "context_id": str(manifest.get("context_id") or ""),
        "known_evidence_refs": known,
        "known_evidence_refs_sha256": sha256_value(sorted(known)),
    }


def bounded_file(path: Path, root: Path, label: str) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
        resolved.relative_to(root.expanduser().resolve(strict=True))
        metadata = resolved.lstat()
    except (OSError, ValueError) as exc:
        raise SolCurationError(f"{label} is missing or outside its root") from exc
    if (
        resolved.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size > MAX_JSON_BYTES
    ):
        raise SolCurationError(f"{label} must be a bounded regular file")
    return resolved


def resolve_unique_hashed_file(
    root: Path,
    digest: str,
    label: str,
    *,
    suffix: str,
    require_private: bool = False,
) -> Path:
    """Resolve a cold-path content hash without trusting a returned local path."""

    resolved_root = root.expanduser().resolve(strict=True)
    digest = require_sha(digest, f"{label} hash")
    preferred = resolved_root / "objects" / f"{digest}{suffix}"
    candidates: list[Path] = []
    if preferred.is_file() and not preferred.is_symlink():
        candidates.append(preferred.resolve(strict=True))
    if not candidates:
        for path in resolved_root.rglob(f"*{suffix}"):
            if path.is_symlink() or not path.is_file():
                continue
            try:
                candidate = bounded_file(path, resolved_root, label)
            except SolCurationError:
                continue
            if sha256_file(candidate) == digest:
                candidates.append(candidate)
                if len(candidates) > 1:
                    break
    if len(candidates) != 1:
        raise SolCurationError(f"{label} hash does not resolve uniquely")
    if sha256_file(candidates[0]) != digest:
        raise SolCurationError(f"{label} content hash mismatch")
    if require_private:
        file_mode = stat.S_IMODE(candidates[0].lstat().st_mode)
        if file_mode & 0o077:
            raise SolCurationError(f"{label} permissions are not private")
        cursor = candidates[0].parent
        while True:
            info = cursor.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISDIR(info.st_mode)
                or stat.S_IMODE(info.st_mode) & 0o077
            ):
                raise SolCurationError(f"{label} directory permissions are unsafe")
            if cursor == resolved_root:
                break
            cursor = cursor.parent
    return candidates[0]


def load_json(path: Path, *, root: Path | None = None, label: str = "JSON") -> dict[str, Any]:
    target = bounded_file(path, root, label) if root is not None else path.expanduser().resolve(strict=True)
    try:
        if target.stat().st_size > MAX_JSON_BYTES:
            raise SolCurationError(f"{label} is too large")
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SolCurationError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise SolCurationError(f"{label} must be an object")
    return value


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink():
        raise SolCurationError("runtime output directory is a symlink")
    if path.exists():
        if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
            raise SolCurationError("content-addressed path collision")
        os.chmod(path, 0o600)
        return
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temp_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _runtime_paths(repo: Path, runtime_root: Path) -> dict[str, Path]:
    base = runtime_root.expanduser().resolve() / repo_key(repo)
    return {
        "base": base,
        "decisions": base / "decisions",
        "packages": base / "canonical-packages",
        "pointers": base / "decision-pointers",
    }


def _prepare_runtime_paths(paths: Mapping[str, Path]) -> None:
    base = paths["base"]
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(base, 0o700)
    for key in ("decisions", "packages", "pointers"):
        path = paths[key]
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)


def _frozen_capture_binding(
    repo: Path,
    *,
    study_date: str,
    batch_id: str,
    capture_set_sha256: str,
    capture_id: str,
    require_pending_result: bool,
) -> dict[str, Any]:
    state = capture_ledger.replay(_read_capture_events(repo))
    if state.get("schema") != capture_ledger.STATE_SCHEMA:
        raise SolCurationError("capture replay schema mismatch")
    batch = (state.get("batches") or {}).get(batch_id)
    if not isinstance(batch, dict):
        raise SolCurationError("frozen curation batch does not exist")
    if (
        batch.get("batch_id") != batch_id
        or batch.get("study_date") != study_date
        or batch.get("capture_set_sha256") != capture_set_sha256
        or batch.get("status") != "active"
    ):
        raise SolCurationError("active batch binding mismatch")
    captures = state.get("captures")
    if not isinstance(captures, dict):
        raise SolCurationError("capture projection is missing")
    ordered_ids = list(batch.get("capture_ids") or [])
    if not ordered_ids or sha256_value(ordered_ids) != capture_set_sha256:
        raise SolCurationError("frozen capture inventory mismatch")
    if len(ordered_ids) != len(set(ordered_ids)):
        raise SolCurationError("frozen capture order contains duplicates")
    if capture_id not in ordered_ids:
        raise SolCurationError("capture is not in the frozen batch")
    if capture_id in (batch.get("carried_capture_ids") or []):
        raise SolCurationError("carried success does not receive a new Sol decision")
    row = captures.get(capture_id)
    if not isinstance(row, dict) or not isinstance(row.get("capture"), dict):
        raise SolCurationError("frozen capture row is missing")
    if row.get("study_date") != study_date or row.get("formalization_authorized") is not True:
        raise SolCurationError("capture date or authorization mismatch")
    if require_pending_result and row.get("quality_status") != "curating":
        raise SolCurationError("capture already has a terminal result")
    current_index = ordered_ids.index(capture_id)
    for prior_id in ordered_ids[:current_index]:
        prior_result = (batch.get("results") or {}).get(prior_id)
        if prior_result is None and prior_id not in (batch.get("carried_capture_ids") or []):
            raise SolCurationError("prior frozen capture has not reached a terminal result")
    payload_sha = require_sha(row.get("payload_sha256"), "capture payload")
    if sha256_value(row["capture"]) != payload_sha:
        raise SolCurationError("frozen capture payload hash mismatch")
    stable_refs = row["capture"].get("stable_evidence_refs")
    if not isinstance(stable_refs, list) or not stable_refs:
        raise SolCurationError("capture has no stable evidence refs")
    normalized_refs: list[dict[str, str]] = []
    for index, ref in enumerate(stable_refs):
        if not isinstance(ref, dict):
            raise SolCurationError(f"stable evidence ref {index} is invalid")
        normalized_refs.append(
            {
                "kind": require_nonempty(ref.get("kind"), f"evidence[{index}].kind", maximum=240),
                "locator": require_nonempty(ref.get("locator"), f"evidence[{index}].locator", maximum=2048),
                "sha256": require_sha(ref.get("sha256"), f"evidence[{index}].sha256"),
            }
        )
    return {
        "study_date": study_date,
        "batch_id": batch_id,
        "batch_attempt": int(batch.get("attempt") or 1),
        "capture_set_sha256": capture_set_sha256,
        "capture_ids": ordered_ids,
        "capture_order_index": current_index,
        "capture_id": capture_id,
        "capture_payload_sha256": payload_sha,
        "capture_snapshot_sha256": sha256_value(row["capture"]),
        "stable_evidence_sha256": sha256_value(normalized_refs),
        "capture": row["capture"],
    }


def _validate_luna_stage_receipts(value: Any) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, Mapping) or set(value) != {
        "analysis",
        "critical_review",
    }:
        raise SolCurationError("Luna v2 stage receipts are incomplete")
    normalized: dict[str, Mapping[str, Any]] = {}
    for stage in ("analysis", "critical_review"):
        receipt = value.get(stage)
        if (
            not isinstance(receipt, Mapping)
            or receipt.get("status") != "ready"
            or receipt.get("requested_model") != "gpt-5.6-luna"
            or receipt.get("requested_reasoning_effort") != "max"
            or receipt.get("runtime_model") != "gpt-5.6-luna"
            or receipt.get("runtime_reasoning_effort") != "max"
            or receipt.get("runtime_identity_status") != "confirmed"
        ):
            raise SolCurationError(f"Luna v2 {stage} receipt is not Luna Max ready")
        normalized[stage] = receipt
    return normalized


def _validate_authoritative_luna_binding(
    verified: Mapping[str, Any],
    *,
    consumer: Mapping[str, Any],
    luna_package: Mapping[str, Any],
    report_artifact: Mapping[str, Any],
    expected_release_id: str,
) -> None:
    latest = verified.get("latest")
    completion = verified.get("completion")
    receipt = verified.get("receipt")
    authority_package = verified.get("package")
    if not all(
        isinstance(value, Mapping)
        for value in (latest, completion, receipt, authority_package)
    ):
        raise SolCurationError("authoritative dispatch result is incomplete")
    assert isinstance(latest, Mapping)
    assert isinstance(completion, Mapping)
    assert isinstance(receipt, Mapping)
    assert isinstance(authority_package, Mapping)
    unit_sha256 = require_sha(latest.get("unit_sha256"), "authority unit hash")
    receipt_sha256 = require_sha(
        latest.get("receipt_sha256"), "authority receipt hash"
    )
    completion_sha256 = require_sha(
        latest.get("completion_sha256"), "authority completion hash"
    )
    lease_fence = latest.get("lease_fence")
    if (
        consumer.get("authority_verified") is not True
        or consumer.get("authority_unit_sha256") != unit_sha256
        or consumer.get("authority_lease_fence") != lease_fence
        or consumer.get("authority_receipt_sha256") != receipt_sha256
        or consumer.get("authority_completion_sha256") != completion_sha256
        or consumer.get("authority_release_id") != expected_release_id
        or not isinstance(lease_fence, int)
        or isinstance(lease_fence, bool)
        or lease_fence < 1
        or completion.get("outcome") != "succeeded"
        or receipt.get("outcome") != "succeeded"
        or authority_package.get("release_id") != expected_release_id
    ):
        raise SolCurationError("consumer authority binding mismatch")
    task = authority_package.get("task")
    frozen_payload = task.get("frozen_payload") if isinstance(task, Mapping) else None
    stage_runtime = authority_package.get("stage_runtime")
    if (
        not isinstance(frozen_payload, Mapping)
        or frozen_payload.get("subject") != "cs408"
        or frozen_payload.get("capture_id") != consumer.get("capture_id")
        or frozen_payload.get("input_fingerprint")
        != luna_package.get("input_fingerprint")
        or frozen_payload.get("input_fingerprint")
        != consumer.get("input_fingerprint")
        or frozen_payload.get("input_binding") != luna_package.get("input_binding")
        or authority_package.get("model_contract")
        != {"model": "gpt-5.6-luna", "reasoning_effort": "max"}
        or authority_package.get("pipeline") != ["analysis", "critical_review"]
        or authority_package.get("formal_write_count") != 0
        or not isinstance(stage_runtime, Mapping)
        or any(
            not isinstance(stage_runtime.get(stage), Mapping)
            or stage_runtime[stage].get("runtime_model") != "gpt-5.6-luna"
            or stage_runtime[stage].get("runtime_reasoning_effort") != "max"
            for stage in ("analysis", "critical_review")
        )
    ):
        raise SolCurationError("authority task or Luna Max pipeline binding mismatch")
    critical_review = authority_package.get("critical_review")
    if (
        not isinstance(critical_review, Mapping)
        or critical_review.get("revised_analysis") != report_artifact
    ):
        raise SolCurationError("authority critical review report binding mismatch")


def _validate_luna_consumer(
    consumer: Mapping[str, Any],
    *,
    frozen: Mapping[str, Any],
    direct_evidence: Mapping[str, Any],
    runtime_data_root: Path,
    authoritative_completion: Mapping[str, Any] | None = None,
    expected_release_id: str | None = None,
) -> dict[str, Any]:
    raw_status = str(consumer.get("status") or "unavailable")
    if raw_status not in LUNA_CONSUMER_STATUSES:
        raise SolCurationError("unknown Luna consumer status")
    if consumer.get("formal_write_count") not in (0, None):
        raise SolCurationError("Luna consumer claimed a formal write")
    normalized: dict[str, Any] = {
        "status": raw_status,
        "consumer_result_sha256": sha256_value(consumer),
        "package_contract": None,
        "package_id": None,
        "package_sha256": None,
        "input_fingerprint": None,
        "processing_contract_sha256": None,
        "processing_fingerprint": None,
        "evidence_manifest_sha256": None,
        "evidence_bundle_sha256": None,
        "report_id": None,
        "report_json_ref": None,
        "report_json_sha256": None,
        "report_markdown_ref": None,
        "report_markdown_sha256": None,
        "renderer_build_sha256": None,
        "quality_receipt_sha256": None,
        "legacy_analysis_sha256": None,
        "review_requirement": None,
        "rejection_reason": None,
        "adoption_token": None,
        "package_path": None,
        "report_json_path": None,
    }
    if raw_status != "ready":
        if consumer.get("adoption_token") not in (None, ""):
            raise SolCurationError("non-consumable Luna status must not carry a token")
        return normalized
    for label, expected in (
        ("subject", "cs408"),
        ("capture_id", frozen["capture_id"]),
        ("study_date", frozen["study_date"]),
        ("batch_id", frozen["batch_id"]),
        ("capture_set_sha256", frozen["capture_set_sha256"]),
        ("capture_payload_sha256", frozen["capture_payload_sha256"]),
    ):
        if consumer.get(label) != expected:
            raise SolCurationError(f"Luna consumer {label} mismatch")
    validation = consumer.get("validation")
    if (
        not isinstance(validation, dict)
        or not validation
        or any(value is not True for value in validation.values())
    ):
        raise SolCurationError("Luna consumer validation did not pass")
    package_root = runtime_data_root.expanduser().resolve(strict=True) / "packages"
    package_sha = require_sha(consumer.get("package_sha256"), "Luna package hash")
    package_path_raw = consumer.get("package_path")
    if isinstance(package_path_raw, str) and package_path_raw:
        package_path = bounded_file(Path(package_path_raw), package_root, "Luna package")
        if sha256_file(package_path) != package_sha:
            raise SolCurationError("Luna package hash mismatch")
    else:
        package_path = resolve_unique_hashed_file(
            package_root, package_sha, "Luna package", suffix=".json"
        )
    package = load_json(package_path, root=package_root, label="Luna package")
    package_schema = str(package.get("schema_version") or "")
    if package_schema != LUNA_PACKAGE_V2:
        raise SolCurationError("unsupported Luna package schema")
    if (
        package.get("subject") != "cs408"
        or package.get("capture_id") != frozen["capture_id"]
        or package.get("study_date") != frozen["study_date"]
        or package.get("package_id") != consumer.get("package_id")
        or package.get("input_fingerprint") != consumer.get("input_fingerprint")
        or package.get("formal_write_count") != 0
    ):
        raise SolCurationError("Luna package identity mismatch")
    input_binding = package.get("input_binding")
    if not isinstance(input_binding, dict) or (
        input_binding.get("payload_sha256") != frozen["capture_payload_sha256"]
        or input_binding.get("capture_id") != frozen["capture_id"]
        or input_binding.get("study_date") != frozen["study_date"]
    ):
        raise SolCurationError("Luna package input binding mismatch")
    token = require_nonempty(consumer.get("adoption_token"), "Luna adoption token", maximum=512)
    allowed = package.get("allowed_evidence_refs") or []
    if (
        not isinstance(allowed, list)
        or not all(isinstance(ref, str) for ref in allowed)
        or not set(allowed) <= set(direct_evidence["known_evidence_refs"])
    ):
        raise SolCurationError("Luna package cites evidence outside direct raw evidence")
    if package_schema == LUNA_PACKAGE_V1:
        normalized_status = "ready_legacy"
        if raw_status != "ready":
            raise SolCurationError("legacy Luna package cannot claim degraded v2 status")
        evidence_bundle_sha = input_binding.get("evidence_bundle_sha256")
        if evidence_bundle_sha is not None:
            evidence_bundle_sha = require_sha(
                evidence_bundle_sha, "legacy Luna evidence bundle hash"
            )
        report_json_sha = None
        report_json_ref = None
        report_json_path: Path | None = None
        report_markdown_ref = None
        report_markdown_sha = None
        report_id = None
        renderer_build_sha = None
        evidence_manifest_sha = input_binding.get("evidence_manifest_sha256")
        quality_receipt_sha = None
        legacy_analysis_sha = sha256_value(package.get("analysis"))
        review_requirement = "legacy_reverify_all"
    else:
        normalized_status = raw_status
        if (
            consumer.get("schema_version")
            != "study-intake-preprocess-consumer-v2"
            or consumer.get("package_path") is not None
            or set(consumer) != LUNA_CONSUMER_V2_KEYS
            or set(package) != LUNA_PACKAGE_V2_KEYS
        ):
            raise SolCurationError("Luna v2 consumer or package fields mismatch")
        if direct_evidence.get("status") != "verified_v3":
            raise SolCurationError("Luna v2 requires direct bundle v3 evidence")
        if authoritative_completion is None or not expected_release_id:
            raise SolCurationError("Luna v2 authoritative completion is missing")
        if package.get("pipeline_status") != "two_pass_ready":
            raise SolCurationError("Luna v2 pipeline status is invalid")
        if (
            raw_status != "ready"
            or consumer.get("pipeline_status") != package.get("pipeline_status")
        ):
            raise SolCurationError("consumer and Luna v2 pipeline status disagree")
        if (
            package.get("model") != "gpt-5.6-luna"
            or consumer.get("model") != "gpt-5.6-luna"
            or package.get("reasoning_effort") != "max"
            or consumer.get("reasoning_effort") != "max"
        ):
            raise SolCurationError("Luna v2 model or reasoning contract drifted")
        if "analysis" in package or "critical_review" in package:
            raise SolCurationError("Luna v2 package duplicates the authoritative report")
        _validate_luna_stage_receipts(package.get("stage_receipts"))
        require_nonempty(
            consumer.get("publication_id"), "consumer publication ID", maximum=256
        )
        quality_receipt = package.get("quality_receipt")
        if not isinstance(quality_receipt, dict):
            raise SolCurationError("Luna v2 package lacks quality receipt")
        evidence_bundle_sha = require_sha(
            consumer.get("evidence_bundle_sha256"),
            "consumer evidence bundle hash",
        )
        evidence_manifest_sha = require_sha(
            consumer.get("evidence_manifest_sha256"),
            "consumer evidence manifest hash",
        )
        report_json_sha = require_sha(
            consumer.get("report_json_sha256"), "consumer report JSON hash"
        )
        quality_receipt_sha = require_sha(
            consumer.get("quality_receipt_sha256"),
            "consumer quality receipt hash",
        )
        report_json_ref = require_nonempty(
            consumer.get("report_json_ref"), "consumer report JSON ref", maximum=512
        )
        if report_json_ref != REPORT_REF_PREFIX + report_json_sha:
            raise SolCurationError("consumer report JSON ref/hash mismatch")
        report_markdown_sha = require_sha(
            consumer.get("report_markdown_sha256"),
            "consumer report Markdown hash",
        )
        report_markdown_ref = require_nonempty(
            consumer.get("report_markdown_ref"),
            "consumer report Markdown ref",
            maximum=512,
        )
        if report_markdown_ref != REPORT_MARKDOWN_REF_PREFIX + report_markdown_sha:
            raise SolCurationError("consumer report Markdown ref/hash mismatch")
        report_id = require_nonempty(
            consumer.get("report_id"), "consumer report ID", maximum=256
        )
        renderer_build_sha = require_sha(
            consumer.get("renderer_build_sha256"), "consumer renderer build hash"
        )
        processing_fingerprint = require_sha(
            consumer.get("processing_fingerprint"),
            "consumer processing fingerprint",
        )
        processing_contract_sha = require_sha(
            consumer.get("processing_contract_sha256"),
            "consumer processing contract hash",
        )
        if (
            package.get("processing_fingerprint")
            != processing_fingerprint
            or package.get("processing_contract_sha256")
            != processing_contract_sha
            or package.get("report_id") != report_id
            or package.get("report_json_ref") != report_json_ref
            or package.get("report_markdown_ref") != report_markdown_ref
            or package.get("report_markdown_sha256") != report_markdown_sha
            or package.get("renderer_build_sha256") != renderer_build_sha
            or package.get("evidence_manifest_sha256") != evidence_manifest_sha
            or package.get("evidence_bundle_sha256") != evidence_bundle_sha
            or input_binding.get("evidence_bundle_sha256") != evidence_bundle_sha
            or input_binding.get("evidence_manifest_sha256")
            != evidence_manifest_sha
            or evidence_manifest_sha
            != direct_evidence.get("evidence_manifest_sha256")
            or evidence_bundle_sha
            != direct_evidence.get("evidence_bundle_sha256")
            or package.get("report_json_sha256") != report_json_sha
            or package.get("quality_receipt_sha256") != quality_receipt_sha
        ):
            raise SolCurationError("consumer hashes do not match the Luna v2 package")
        if sha256_value(quality_receipt) != quality_receipt_sha:
            raise SolCurationError("Luna v2 quality receipt content hash mismatch")
        if (
            quality_receipt.get("pipeline_status") != package.get("pipeline_status")
            or quality_receipt.get("model") != "gpt-5.6-luna"
            or quality_receipt.get("reasoning_effort") != "max"
            or quality_receipt.get("two_stage_status")
            != package.get("pipeline_status")
            or quality_receipt.get("processing_contract_sha256")
            != processing_contract_sha
            or quality_receipt.get("processing_fingerprint")
            != processing_fingerprint
            or quality_receipt.get("report_json_sha256") != report_json_sha
            or quality_receipt.get("report_json_ref") != report_json_ref
            or quality_receipt.get("report_markdown_sha256") != report_markdown_sha
            or quality_receipt.get("report_markdown_ref") != report_markdown_ref
            or quality_receipt.get("renderer_build_sha256")
            != renderer_build_sha
            or quality_receipt.get("evidence_manifest_sha256")
            != evidence_manifest_sha
            or quality_receipt.get("evidence_bundle_sha256") != evidence_bundle_sha
            or quality_receipt.get("formal_write_count") != 0
        ):
            raise SolCurationError("Luna v2 quality receipt binding mismatch")
        reports_root = (
            runtime_data_root.expanduser().resolve(strict=True)
            / "private"
            / "reports"
        )
        report_json_path = resolve_unique_hashed_file(
            reports_root,
            report_json_sha,
            "Luna report JSON",
            suffix=".json",
            require_private=True,
        )
        report_markdown_path = resolve_unique_hashed_file(
            reports_root,
            report_markdown_sha,
            "Luna report Markdown",
            suffix=".md",
            require_private=True,
        )
        if (
            report_json_path.parent.name != "objects"
            or report_json_path.name != f"{report_json_sha}.json"
            or report_markdown_path.parent.name != "markdown"
            or report_markdown_path.name != f"{report_markdown_sha}.md"
        ):
            raise SolCurationError("Luna report path is outside the content-addressed store")
        markdown_text = report_markdown_path.read_text(encoding="utf-8")
        if (
            f"report_json_sha256：{report_json_sha}" not in markdown_text
            or f"renderer_build_sha256：{renderer_build_sha}" not in markdown_text
        ):
            raise SolCurationError("Luna report Markdown does not bind JSON/renderer")
        report_artifact = load_json(
            report_json_path, root=reports_root, label="Luna report JSON"
        )
        # The content-addressed report object is the authoritative analysis itself,
        # not an envelope.  Identity, pipeline state, and stage review are bound by
        # the package and quality receipt around its exact content hash.
        required_report_sections = {
            "schema_version",
            "report_profile",
            "executive_summary",
            "question_structure",
            "correct_reasoning_reconstruction",
            "evidence_assessment",
            "reasoning_diagnosis",
            "concept_method_analysis",
            "formalization_candidates",
            "sol_verification_plan",
            "risk_flags",
            "unresolved",
        }
        if (
            set(report_artifact) != required_report_sections
            or report_artifact.get("schema_version")
            != "study-intake-luna-analysis-v2"
            or report_artifact.get("report_profile") != "cs408_deep"
            or not isinstance(report_artifact.get("formalization_candidates"), dict)
        ):
            raise SolCurationError("Luna report JSON contract mismatch")
        _validate_authoritative_luna_binding(
            authoritative_completion,
            consumer=consumer,
            luna_package=package,
            report_artifact=report_artifact,
            expected_release_id=expected_release_id,
        )
        authoritative_stage = quality_receipt.get("authoritative_stage")
        if raw_status == "ready" and authoritative_stage != "critical_review_revised":
            raise SolCurationError("ready Luna report lacks bound critical review")
        if (
            raw_status == "single_pass_degraded"
            and authoritative_stage != "analysis_draft"
        ):
            raise SolCurationError("degraded Luna report has invalid authoritative stage")
        legacy_analysis_sha = None
        review_requirement = (
            "heightened" if raw_status == "single_pass_degraded" else "normal"
        )
    if package_schema == LUNA_PACKAGE_V1 and not isinstance(package.get("analysis"), dict):
        raise SolCurationError("Luna package lacks analysis")
    normalized.update(
        {
            "status": normalized_status,
            "package_contract": package_schema,
            "package_id": str(package["package_id"]),
            "package_sha256": package_sha,
            "input_fingerprint": str(package["input_fingerprint"]),
            "processing_contract_sha256": (
                processing_contract_sha if package_schema == LUNA_PACKAGE_V2 else None
            ),
            "processing_fingerprint": package.get("processing_fingerprint"),
            "evidence_manifest_sha256": evidence_manifest_sha,
            "evidence_bundle_sha256": evidence_bundle_sha,
            "report_id": report_id,
            "report_json_ref": report_json_ref,
            "report_json_sha256": report_json_sha,
            "report_markdown_ref": report_markdown_ref,
            "report_markdown_sha256": report_markdown_sha,
            "renderer_build_sha256": renderer_build_sha,
            "quality_receipt_sha256": quality_receipt_sha,
            "legacy_analysis_sha256": legacy_analysis_sha,
            "review_requirement": review_requirement,
            "adoption_token": token,
            "package_path": str(package_path),
            "report_json_path": (
                str(report_json_path) if report_json_path is not None else None
            ),
        }
    )
    return normalized


def _rejected_luna(consumer: Mapping[str, Any], reason: str) -> dict[str, Any]:
    """Quarantine a drifted optional report without blocking raw-evidence curation."""

    return {
        "status": "rejected_report",
        "consumer_result_sha256": sha256_value(consumer),
        "package_contract": None,
        "package_id": None,
        "package_sha256": None,
        "input_fingerprint": None,
        "processing_contract_sha256": None,
        "processing_fingerprint": None,
        "evidence_manifest_sha256": None,
        "evidence_bundle_sha256": None,
        "report_id": None,
        "report_json_ref": None,
        "report_json_sha256": None,
        "report_markdown_ref": None,
        "report_markdown_sha256": None,
        "renderer_build_sha256": None,
        "quality_receipt_sha256": None,
        "legacy_analysis_sha256": None,
        "review_requirement": None,
        "rejection_reason": reason[:2000],
        "adoption_token": None,
        "package_path": None,
        "report_json_path": None,
    }


def _luna_proposals(
    package: Mapping[str, Any], report: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    package_schema = str(package.get("schema_version") or "")
    analysis = (
        report
        if package_schema == LUNA_PACKAGE_V2 and isinstance(report, Mapping)
        else package.get("analysis")
    )
    if not isinstance(analysis, dict):
        raise SolCurationError("Luna authoritative analysis is missing")
    proposals: list[dict[str, Any]] = []
    if package_schema == LUNA_PACKAGE_V2:
        columns = analysis.get("formalization_candidates")
        if not isinstance(columns, dict) or set(columns) != set(LUNA_V2_FIELD_MAP):
            raise SolCurationError("Luna v2 formalization candidate columns mismatch")
        allowed_refs = set(package.get("allowed_evidence_refs") or [])
        for source_field, field in LUNA_V2_FIELD_MAP.items():
            claims = columns.get(source_field)
            if not isinstance(claims, list):
                raise SolCurationError("Luna v2 formalization column is not an array")
            for index, raw_claim in enumerate(claims):
                if not isinstance(raw_claim, dict) or set(raw_claim) != CLAIM_KEYS:
                    raise SolCurationError("Luna v2 formalization claim fields mismatch")
                claim = dict(raw_claim)
                text = require_nonempty(
                    claim.get("text"), "Luna formalization claim", maximum=12000
                )
                refs = claim.get("evidence_refs")
                if (
                    not isinstance(refs, list)
                    or not refs
                    or not all(isinstance(ref, str) and ref in allowed_refs for ref in refs)
                ):
                    raise SolCurationError(
                        "Luna v2 formalization claim has an unbound evidence ref"
                    )
                if claim.get("confidence") not in {"low", "medium", "high"}:
                    raise SolCurationError("Luna v2 claim confidence is invalid")
                claim["text"] = text
                claim["evidence_refs"] = list(refs)
                claim["counterevidence_or_boundary"] = str(
                    claim.get("counterevidence_or_boundary") or ""
                ).strip()
                claim["sol_verification_action"] = require_nonempty(
                    claim.get("sol_verification_action"),
                    "Luna Sol verification action",
                    maximum=6000,
                )
                require_answer_safe(claim, f"Luna.{source_field}[{index}]")
                identity = {
                    "source_schema": LUNA_PACKAGE_V2,
                    "source_field": source_field,
                    "claim_index": index,
                    "claim": claim,
                }
                proposals.append(
                    {
                        **identity,
                        "field": field,
                        "luna_value": text,
                        "proposal_sha256": sha256_value(identity),
                    }
                )
        return proposals

    updates = analysis.get("candidate_updates")
    if updates is None:
        return []
    if not isinstance(updates, list):
        raise SolCurationError("legacy Luna candidate_updates must be an array")
    for index, row in enumerate(updates):
        if not isinstance(row, dict):
            raise SolCurationError("legacy Luna candidate update is invalid")
        field = require_nonempty(row.get("field"), "legacy Luna field", maximum=120)
        if not SAFE_FIELD_RE.fullmatch(field):
            raise SolCurationError("legacy Luna proposal field is unsafe")
        text = require_nonempty(row.get("proposal"), "legacy Luna proposal", maximum=12000)
        require_answer_safe(text, f"legacy Luna proposal {index}")
        claim = {
            "claim_type": "legacy_unreviewed_suggestion",
            "text": text,
            "evidence_refs": list(row.get("evidence_refs") or []),
            "confidence": str(row.get("confidence") or "unknown"),
            "counterevidence_or_boundary": "legacy v1 has no mandatory critical review",
            "sol_verification_action": "rederive from raw capture and stable evidence",
        }
        identity = {
            "source_schema": LUNA_PACKAGE_V1,
            "source_field": field,
            "claim_index": index,
            "claim": claim,
        }
        proposals.append(
            {
                **identity,
                "field": field,
                "luna_value": text,
                "proposal_sha256": sha256_value(identity),
            }
        )
    return proposals


def _normalize_decision_request(
    request: Mapping[str, Any],
    *,
    frozen: Mapping[str, Any],
    luna: Mapping[str, Any],
    luna_package: Mapping[str, Any] | None,
    luna_report: Mapping[str, Any] | None,
    known_evidence_refs: set[str],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    required = {
        "schema",
        "intended_terminal",
        "identity",
        "field_decisions",
        "immutable_conflicts",
        "canonical_package_path",
        "decision_reason",
    }
    if set(request) != required or request.get("schema") != DECISION_REQUEST_SCHEMA:
        raise SolCurationError("decision request fields or schema mismatch")
    intended = str(request.get("intended_terminal") or "")
    if intended not in INTENDED_TERMINALS:
        raise SolCurationError("invalid intended terminal")
    identity = request.get("identity")
    if not isinstance(identity, dict) or set(identity) != {"status", "mode", "formal_id", "basis"}:
        raise SolCurationError("identity decision fields mismatch")
    identity_status = str(identity.get("status") or "")
    mode = str(identity.get("mode") or "")
    formal_id = identity.get("formal_id")
    if identity_status not in {"resolved", "needs_user"}:
        raise SolCurationError("invalid identity decision status")
    if identity_status == "resolved" and mode == "new":
        if formal_id not in (None, ""):
            raise SolCurationError("new identity must leave formal_id for commit-lock allocation")
        formal_id = None
    elif identity_status == "resolved" and mode == "redo":
        if not FORMAL_ID_RE.fullmatch(str(formal_id or "")):
            raise SolCurationError("redo identity requires one existing formal ID")
        formal_id = str(formal_id)
    elif identity_status == "needs_user":
        if mode != "unknown" or formal_id not in (None, ""):
            raise SolCurationError("unresolved identity cannot bind a formal ID")
        formal_id = None
    else:
        raise SolCurationError("resolved identity mode must be new or redo")
    normalized_identity = {
        "status": identity_status,
        "mode": mode,
        "formal_id": formal_id,
        "basis": require_answer_safe(
            require_nonempty(identity.get("basis"), "identity basis"),
            "identity.basis",
        ),
    }

    conflicts = request.get("immutable_conflicts")
    if not isinstance(conflicts, list):
        raise SolCurationError("immutable_conflicts must be an array")
    normalized_conflicts: list[dict[str, Any]] = []
    for row in conflicts:
        if not isinstance(row, dict) or set(row) != {"field", "reason", "evidence_refs"}:
            raise SolCurationError("immutable conflict fields mismatch")
        field = str(row.get("field") or "")
        if field not in IMMUTABLE_FIELDS:
            raise SolCurationError("conflict does not name an immutable field")
        refs = row.get("evidence_refs")
        if not isinstance(refs, list) or not refs or not all(isinstance(item, str) and item for item in refs):
            raise SolCurationError("immutable conflict needs evidence refs")
        if not set(refs) <= known_evidence_refs:
            raise SolCurationError("immutable conflict cites evidence outside raw capture/bundle")
        normalized_conflicts.append(
            {
                "field": field,
                "reason": require_answer_safe(
                    require_nonempty(row.get("reason"), "conflict reason"),
                    f"immutable_conflict.{field}.reason",
                ),
                "evidence_refs": require_answer_safe(
                    sorted(set(refs)), f"immutable_conflict.{field}.evidence_refs"
                ),
            }
        )

    proposals = (
        _luna_proposals(luna_package or {}, luna_report)
        if luna["status"] in LUNA_USABLE_STATUSES
        else []
    )
    proposal_by_hash = {row["proposal_sha256"]: row for row in proposals}
    if len(proposal_by_hash) != len(proposals):
        raise SolCurationError("Luna proposal identity collision")
    decisions = request.get("field_decisions")
    if not isinstance(decisions, list):
        raise SolCurationError("field_decisions must be an array")
    normalized_decisions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in decisions:
        if not isinstance(row, dict) or set(row) != FIELD_DECISION_KEYS:
            raise SolCurationError("field decision fields mismatch")
        proposal_sha = require_sha(row.get("proposal_sha256"), "proposal hash")
        if proposal_sha in seen or proposal_sha not in proposal_by_hash:
            raise SolCurationError("field decision does not uniquely bind a Luna proposal")
        seen.add(proposal_sha)
        proposal = proposal_by_hash[proposal_sha]
        field = str(row.get("field") or "")
        action = str(row.get("action") or "")
        if field != proposal["field"] or action not in FIELD_ACTIONS:
            raise SolCurationError("field decision identity or action mismatch")
        if action in {"adopt", "modify"} and field not in CURATABLE_FIELDS:
            raise SolCurationError("Sol cannot adopt or modify a non-curatable field")
        if luna["status"] == "ready_legacy" and action == "adopt":
            raise SolCurationError("legacy Luna proposal requires Sol modification or rejection")
        refs = row.get("evidence_refs")
        if not isinstance(refs, list) or not refs or not all(isinstance(item, str) and item for item in refs):
            raise SolCurationError("field decision needs evidence refs")
        if not set(refs) <= known_evidence_refs:
            raise SolCurationError("field decision cites evidence outside raw capture/bundle")
        if row.get("luna_value") != proposal["luna_value"]:
            raise SolCurationError("field decision altered the Luna suggestion")
        sol_value = require_answer_safe(row.get("sol_value"), f"field_decision.{field}.sol_value")
        if action in {"adopt", "modify"} and sol_value is None:
            raise SolCurationError("adopt or modify requires an explicit Sol value")
        if action == "adopt" and sol_value != proposal["luna_value"] and not (
            isinstance(sol_value, list) and proposal["luna_value"] in sol_value
        ):
            raise SolCurationError("adopt does not retain the exact Luna suggestion")
        confidence = str(row.get("confidence") or "")
        if confidence not in {"low", "medium", "high"}:
            raise SolCurationError("field decision confidence is invalid")
        unresolved = row.get("unresolved")
        if not isinstance(unresolved, list) or not all(
            isinstance(item, str) and item.strip() for item in unresolved
        ):
            raise SolCurationError("field decision unresolved must be a string array")
        normalized_decisions.append(
            {
                "proposal_sha256": proposal_sha,
                "field": field,
                "action": action,
                "luna_value": require_answer_safe(
                    proposal["luna_value"], f"field_decision.{field}.luna_value"
                ),
                "sol_value": sol_value,
                "evidence_refs": require_answer_safe(
                    sorted(set(refs)), f"field_decision.{field}.evidence_refs"
                ),
                "basis": require_answer_safe(
                    require_nonempty(row.get("basis"), "field decision basis", maximum=8000),
                    f"field_decision.{field}.basis",
                ),
                "counterevidence": require_answer_safe(
                    str(row.get("counterevidence") or "").strip(),
                    f"field_decision.{field}.counterevidence",
                ),
                "confidence": confidence,
                "unresolved": require_answer_safe(
                    [item.strip() for item in unresolved],
                    f"field_decision.{field}.unresolved",
                ),
                "source_claim": proposal["claim"],
                "review_requirement": luna.get("review_requirement"),
            }
        )
    if set(proposal_by_hash) != seen:
        raise SolCurationError("every Luna candidate update needs one Sol decision")
    if luna["status"] not in LUNA_USABLE_STATUSES and normalized_decisions:
        raise SolCurationError("non-consumable Luna status cannot drive field decisions")

    blocking_field_gaps = any(
        row["action"] in {"adopt", "modify"} and row["unresolved"]
        for row in normalized_decisions
    )
    needs_user = (
        bool(normalized_conflicts)
        or identity_status == "needs_user"
        or blocking_field_gaps
    )
    if needs_user != (intended == "needs_user"):
        raise SolCurationError("needs_user terminal does not match conflicts or identity gap")
    if intended == "already_current" and (identity_status != "resolved" or mode != "redo"):
        raise SolCurationError("already_current requires one resolved redo identity")

    package: dict[str, Any] | None = None
    package_path = request.get("canonical_package_path")
    if intended == "apply":
        if not isinstance(package_path, str) or not package_path:
            raise SolCurationError("apply decision requires a canonical package")
        package = load_json(Path(package_path), label="canonical package")
        if "curation_evidence_binding" in package:
            raise SolCurationError("canonical input package already has a curation binding")
        if package.get("mode") != mode:
            raise SolCurationError("canonical package mode mismatch")
        if mode == "new":
            if (
                package.get("formal_id") not in (None, "")
                or package.get("auto_id") is not True
                or package.get("contract") != "coordinator_approved_intake_v1"
                or package.get("schema_version") != "coordinator_approved_intake_v1"
            ):
                raise SolCurationError(
                    "new canonical package must use approved commit-lock auto-ID allocation"
                )
        elif (
            package.get("formal_id") != formal_id
            or package.get("auto_id") not in (False, None)
        ):
            raise SolCurationError("redo canonical package identity mismatch")
        for row in normalized_decisions:
            field = row["field"]
            if row["action"] in {"adopt", "modify"} and field not in package:
                raise SolCurationError("adopted or modified field is absent from canonical package")
            if row["action"] == "reject":
                actual_value = package.get(field) if field in package else None
                if actual_value != row["sol_value"]:
                    raise SolCurationError(
                        "rejected Luna claim does not record the actual canonical value"
                    )
                if field in package:
                    row["canonical_value_sha256"] = sha256_value(actual_value)
                continue
            if field not in package or package[field] != row["sol_value"]:
                raise SolCurationError("Sol field value differs from the canonical package")
            if row["action"] == "adopt" and not _canonical_value_matches(
                package[field], row["luna_value"]
            ):
                raise SolCurationError("adopted Luna suggestion is absent from canonical package")
            if row["action"] == "modify" and _canonical_value_matches(
                package[field], row["luna_value"]
            ):
                raise SolCurationError("modified Luna suggestion was not materially changed")
            row["canonical_value_sha256"] = sha256_value(package[field])
    elif package_path not in (None, ""):
        raise SolCurationError("non-apply decision cannot carry a canonical package")

    normalized = {
        "identity": normalized_identity,
        "intended_terminal": intended,
        "field_decisions": sorted(normalized_decisions, key=lambda row: row["proposal_sha256"]),
        "immutable_conflicts": sorted(normalized_conflicts, key=lambda row: row["field"]),
        "decision_reason": require_answer_safe(
            require_nonempty(request.get("decision_reason"), "decision reason", maximum=8000),
            "decision_reason",
        ),
        "formal_apply_authorized": intended == "apply",
        "canonical_base_package_sha256": sha256_value(package) if package is not None else None,
    }
    return normalized, package


def seal_decision(
    *,
    repo_root: str | Path,
    study_date: str,
    batch_id: str,
    capture_set_sha256: str,
    capture_id: str,
    consumer_result_path: str | Path,
    decision_request_path: str | Path,
    preprocessor_root: str | Path,
    private_evidence_root: str | Path = DEFAULT_PRIVATE_EVIDENCE_ROOT,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> dict[str, Any]:
    repo = Path(repo_root).expanduser().resolve(strict=True)
    preprocessor_context = _resolve_current_preprocessor_context(preprocessor_root)
    runtime_data_root = Path(preprocessor_context["runtime_root"])
    date = require_date(study_date)
    set_sha = require_sha(capture_set_sha256, "capture set hash")
    frozen = _frozen_capture_binding(
        repo,
        study_date=date,
        batch_id=require_nonempty(batch_id, "batch ID", maximum=256),
        capture_set_sha256=set_sha,
        capture_id=require_nonempty(capture_id, "capture ID", maximum=256),
        require_pending_result=True,
    )
    direct_evidence = _reopen_direct_evidence(
        frozen,
        private_evidence_root=Path(private_evidence_root).expanduser().resolve(),
    )
    try:
        consumer = load_json(Path(consumer_result_path), label="consumer result")
    except (SolCurationError, OSError):
        consumer = {
            "status": "unavailable",
            "consumer_read_error": "consumer_unreadable",
            "formal_write_count": 0,
        }
    try:
        authoritative_completion = (
            _verify_current_authoritative_completion(
                preprocessor_context, frozen["capture_id"]
            )
            if consumer.get("status") == "ready"
            else None
        )
        luna = _validate_luna_consumer(
            consumer,
            frozen=frozen,
            direct_evidence=direct_evidence,
            runtime_data_root=runtime_data_root,
            authoritative_completion=authoritative_completion,
            expected_release_id=str(preprocessor_context["release_id"]),
        )
    except (SolCurationError, OSError) as exc:
        luna = _rejected_luna(consumer, str(exc))
    luna_package = (
        load_json(
            Path(str(luna["package_path"])),
            root=runtime_data_root / "packages",
            label="Luna package",
        )
        if luna["package_path"] is not None
        else None
    )
    luna_report = (
        load_json(
            Path(str(luna["report_json_path"])),
            root=runtime_data_root / "private" / "reports",
            label="Luna report JSON",
        )
        if luna["report_json_path"] is not None
        else None
    )
    request = load_json(Path(decision_request_path), label="decision request")
    normalized, package = _normalize_decision_request(
        request,
        frozen=frozen,
        luna=luna,
        luna_package=luna_package,
        luna_report=luna_report,
        known_evidence_refs=set(direct_evidence["known_evidence_refs"]),
    )
    material = {
        "schema": DECISION_SCHEMA,
        "repo_root_sha256": hashlib.sha256(str(repo).encode("utf-8")).hexdigest(),
        **{key: frozen[key] for key in (
            "study_date",
            "batch_id",
            "batch_attempt",
            "capture_set_sha256",
            "capture_order_index",
            "capture_id",
            "capture_payload_sha256",
            "capture_snapshot_sha256",
            "stable_evidence_sha256",
        )},
        "luna": {key: luna[key] for key in (
            "status",
            "consumer_result_sha256",
            "package_contract",
            "package_id",
            "package_sha256",
            "input_fingerprint",
            "processing_contract_sha256",
            "processing_fingerprint",
            "evidence_manifest_sha256",
            "evidence_bundle_sha256",
            "report_id",
            "report_json_ref",
            "report_json_sha256",
            "report_markdown_ref",
            "report_markdown_sha256",
            "renderer_build_sha256",
            "quality_receipt_sha256",
            "legacy_analysis_sha256",
            "review_requirement",
            "rejection_reason",
            "adoption_token",
        )},
        "direct_evidence": {
            key: direct_evidence[key]
            for key in (
                "status",
                "evidence_locator",
                "evidence_manifest_sha256",
                "evidence_bundle_sha256",
                "source_binding_sha256",
                "context_id",
                "known_evidence_refs_sha256",
            )
        },
        **normalized,
        "formal_write_count": 0,
    }
    decision_content = canonical_bytes(material)
    decision_sha = hashlib.sha256(decision_content).hexdigest()
    paths = _runtime_paths(repo, Path(runtime_root))
    _prepare_runtime_paths(paths)
    decision_path = paths["decisions"] / f"{decision_sha}.json"
    atomic_write(decision_path, decision_content)

    canonical_path: Path | None = None
    canonical_sha: str | None = None
    if package is not None:
        sealed_package = dict(package)
        sealed_package["curation_evidence_binding"] = {
            "schema": PACKAGE_BINDING_SCHEMA,
            "batch_id": frozen["batch_id"],
            "batch_attempt": frozen["batch_attempt"],
            "capture_set_sha256": frozen["capture_set_sha256"],
            "capture_id": frozen["capture_id"],
            "capture_payload_sha256": frozen["capture_payload_sha256"],
            "stable_evidence_sha256": frozen["stable_evidence_sha256"],
            "direct_evidence_status": direct_evidence["status"],
            "evidence_manifest_sha256": direct_evidence[
                "evidence_manifest_sha256"
            ],
            "evidence_bundle_sha256": direct_evidence["evidence_bundle_sha256"],
            "known_evidence_refs_sha256": direct_evidence[
                "known_evidence_refs_sha256"
            ],
            "luna_package_sha256": luna["package_sha256"],
            "luna_processing_contract_sha256": luna[
                "processing_contract_sha256"
            ],
            "luna_processing_fingerprint": luna["processing_fingerprint"],
            "luna_evidence_manifest_sha256": luna[
                "evidence_manifest_sha256"
            ],
            "luna_report_id": luna["report_id"],
            "luna_report_json_ref": luna["report_json_ref"],
            "luna_report_json_sha256": luna["report_json_sha256"],
            "luna_report_markdown_ref": luna["report_markdown_ref"],
            "luna_report_markdown_sha256": luna["report_markdown_sha256"],
            "luna_renderer_build_sha256": luna["renderer_build_sha256"],
            "luna_quality_receipt_sha256": luna["quality_receipt_sha256"],
            "luna_evidence_bundle_sha256": luna["evidence_bundle_sha256"],
            "sol_decision_sha256": decision_sha,
            "canonical_base_package_sha256": normalized["canonical_base_package_sha256"],
        }
        canonical_content = canonical_bytes(sealed_package)
        canonical_sha = hashlib.sha256(canonical_content).hexdigest()
        canonical_path = paths["packages"] / f"{canonical_sha}.json"
        atomic_write(canonical_path, canonical_content)

    pointer = {
        "schema": POINTER_SCHEMA,
        "decision_sha256": decision_sha,
        "decision_path": str(decision_path),
        "canonical_package_sha256": canonical_sha,
        "canonical_package_path": str(canonical_path) if canonical_path else None,
        "study_date": frozen["study_date"],
        "batch_id": frozen["batch_id"],
        "capture_set_sha256": frozen["capture_set_sha256"],
        "capture_id": frozen["capture_id"],
    }
    pointer_path = paths["pointers"] / f"{decision_sha}.json"
    atomic_write(pointer_path, canonical_bytes(pointer))
    return {
        "status": "SEALED",
        **pointer,
        "pointer_path": str(pointer_path),
        "luna_status": luna["status"],
        "adoption_token": luna["adoption_token"],
        "formal_apply_authorized": normalized["formal_apply_authorized"],
        "formal_write_count": 0,
    }


def _load_pointer(repo: Path, runtime_root: Path, decision_sha256: str) -> tuple[dict[str, Any], dict[str, Any]]:
    digest = require_sha(decision_sha256, "decision hash")
    paths = _runtime_paths(repo, runtime_root)
    pointer = load_json(paths["pointers"] / f"{digest}.json", root=paths["pointers"], label="decision pointer")
    if pointer.get("schema") != POINTER_SCHEMA or pointer.get("decision_sha256") != digest:
        raise SolCurationError("decision pointer mismatch")
    decision_path = bounded_file(Path(str(pointer.get("decision_path") or "")), paths["decisions"], "Sol decision")
    if sha256_file(decision_path) != digest:
        raise SolCurationError("Sol decision content hash mismatch")
    decision = load_json(decision_path, root=paths["decisions"], label="Sol decision")
    if decision.get("schema") != DECISION_SCHEMA:
        raise SolCurationError("Sol decision schema mismatch")
    canonical_sha = pointer.get("canonical_package_sha256")
    canonical_path = pointer.get("canonical_package_path")
    if canonical_sha is None:
        if canonical_path is not None:
            raise SolCurationError("decision pointer has an unbound canonical path")
    else:
        require_sha(canonical_sha, "canonical package hash")
        path = bounded_file(Path(str(canonical_path or "")), paths["packages"], "canonical package")
        if sha256_file(path) != canonical_sha:
            raise SolCurationError("canonical package content hash mismatch")
        package = load_json(path, root=paths["packages"], label="canonical package")
        binding = package.get("curation_evidence_binding")
        if not isinstance(binding, dict) or binding.get("sol_decision_sha256") != digest:
            raise SolCurationError("canonical package does not bind the Sol decision")
    return pointer, decision


def _capture_result_event(
    repo: Path, *, batch_id: str, capture_id: str
) -> tuple[dict[str, Any], str, str]:
    path = repo / CAPTURE_EVENTS_REL
    matches: list[tuple[dict[str, Any], bytes]] = []
    events: list[dict[str, Any]] = []
    try:
        for raw_line in path.read_bytes().splitlines(keepends=True):
            if not raw_line.strip():
                continue
            if not raw_line.endswith(b"\n"):
                raise SolCurationError("capture result ledger has an incomplete tail")
            event = json.loads(raw_line.decode("utf-8"))
            if (
                not isinstance(event, dict)
                or event.get("schema") != capture_ledger.EVENT_SCHEMA
            ):
                raise SolCurationError("capture result ledger line has wrong schema")
            events.append(event)
            if (
                event.get("event_type") == "curation_item_result"
                and event.get("batch_id") == batch_id
                and event.get("capture_id") == capture_id
            ):
                matches.append((event, raw_line))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SolCurationError("capture result ledger is unreadable") from exc
    if len(matches) != 1:
        raise SolCurationError("expected exactly one curation item result")
    event, raw_line = matches[0]
    try:
        replayed = capture_ledger.replay(events)
    except capture_ledger.CaptureError as exc:
        raise SolCurationError("capture result ledger replay failed") from exc
    projected = (
        ((replayed.get("batches") or {}).get(batch_id) or {}).get("results") or {}
    ).get(capture_id)
    if not isinstance(projected, dict) or any(
        projected.get(key) != event.get(key)
        for key in (
            "outcome",
            "formal_id",
            "receipt_sha256",
            "verification_sha256",
            "reason",
        )
    ):
        raise SolCurationError("capture result event does not match canonical replay")
    return event, hashlib.sha256(raw_line).hexdigest(), sha256_value(event)


def _normal_receipt_and_package(
    repo: Path,
    runtime_root: Path,
    *,
    receipt_sha256: str,
    canonical_package_sha256: str,
    formal_id: str,
) -> None:
    receipt_root = runtime_root / "receipts" / repo_key(repo)
    if not receipt_root.is_dir() or receipt_root.is_symlink():
        raise SolCurationError("normal receipt root is unavailable")
    matches = [
        path
        for path in receipt_root.glob("*.json")
        if path.is_file() and not path.is_symlink() and sha256_file(path) == receipt_sha256
    ]
    if len(matches) != 1:
        raise SolCurationError("normal receipt hash does not resolve uniquely")
    receipt = load_json(matches[0], root=receipt_root, label="normal receipt")
    if receipt.get("schema") != "intake_batch_receipt_v1" or receipt.get("status") != "COMMITTED":
        raise SolCurationError("normal receipt is not committed")
    receipt_repo_raw = str(receipt.get("repo_root") or "")
    if (
        not Path(receipt_repo_raw).is_absolute()
        or Path(receipt_repo_raw).resolve() != repo
    ):
        raise SolCurationError("normal receipt repository mismatch")
    receipt_items = receipt.get("items")
    if (
        not isinstance(receipt_items, list)
        or len(receipt_items) != 1
        or not isinstance(receipt_items[0], dict)
        or receipt_items[0].get("formal_id") != formal_id
    ):
        raise SolCurationError("normal receipt formal ID mismatch")
    transaction_root = runtime_root / "transactions" / repo_key(repo)
    journal = load_json(Path(str(receipt.get("journal") or "")), root=transaction_root, label="normal WAL")
    journal_repo_raw = str(journal.get("repo_root") or "")
    if (
        journal.get("schema") != "intake_batch_wal_v1"
        or journal.get("state") != "committed"
        or not Path(journal_repo_raw).is_absolute()
        or Path(journal_repo_raw).resolve() != repo
        or receipt.get("transaction_id") != journal.get("transaction_id")
        or receipt.get("payload_sha256") != journal.get("payload_sha256")
        or receipt_items != journal.get("result_items")
    ):
        raise SolCurationError("normal WAL is not committed")
    packages = ((journal.get("payload_identity") or {}).get("packages") or [])
    if (
        not isinstance(packages, list)
        or len(packages) != 1
        or not isinstance(packages[0], dict)
        or sha256_value(packages[0]) != canonical_package_sha256
    ):
        raise SolCurationError("normal WAL does not bind the canonical Sol package")


def _formal_snapshot(repo: Path, formal_id: str) -> tuple[str, str]:
    master = repo / "节点总表.md"
    if not master.is_file() or master.is_symlink():
        raise SolCurationError("formal master is missing or unsafe")
    matches: list[dict[str, str]] = []
    for line_number, cells in iter_table_rows(
        master.read_text(encoding="utf-8").splitlines(), "ID"
    ):
        if len(cells) != len(MASTER_COLS):
            raise SolCurationError(
                f"formal master column mismatch at line {line_number + 1}"
            )
        row = dict(zip(MASTER_COLS, cells))
        if row.get("ID") == formal_id:
            matches.append(row)
    if len(matches) != 1:
        raise SolCurationError("already-current formal identity is not unique")
    card = repo / f"{formal_id}.md"
    if not card.is_file() or card.is_symlink():
        raise SolCurationError("already-current safe card is missing or unsafe")
    return sha256_value(matches[0]), sha256_file(card)


def seal_already_current(
    *,
    repo_root: str | Path,
    decision_sha256: str,
    formal_id: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
) -> dict[str, Any]:
    """Seal a formal-state snapshot for an already-current Sol decision."""

    repo = Path(repo_root).expanduser().resolve(strict=True)
    runtime = Path(runtime_root).expanduser().resolve()
    _, decision = _load_pointer(repo, runtime, decision_sha256)
    if (
        decision.get("intended_terminal") != "already_current"
        or (decision.get("identity") or {}).get("mode") != "redo"
        or (decision.get("identity") or {}).get("formal_id") != formal_id
        or not FORMAL_ID_RE.fullmatch(formal_id)
    ):
        raise SolCurationError("already-current decision identity mismatch")
    events = _read_capture_events(repo)
    try:
        capture_ledger.audit(repo_root=repo)
        state = capture_ledger.replay(events)
    except capture_ledger.CaptureError as exc:
        raise SolCurationError(str(exc)) from exc
    batch = state.get("batches", {}).get(decision["batch_id"])
    capture_row = state.get("captures", {}).get(decision["capture_id"])
    if (
        not isinstance(batch, dict)
        or batch.get("status") != "active"
        or decision["capture_id"] not in (batch.get("capture_ids") or [])
        or decision["capture_id"] in (batch.get("results") or {})
        or not isinstance(capture_row, dict)
        or capture_row.get("payload_sha256") != decision["capture_payload_sha256"]
    ):
        raise SolCurationError("already-current capture is not pending in the sealed batch")
    formal_row_sha, card_sha = _formal_snapshot(repo, formal_id)
    material = {
        "schema": "already_current_verification_v1",
        "kind": "already_current_verification",
        "status": "VERIFIED_CURRENT",
        "answer_safe": True,
        "formal_write_count": 0,
        "repo_root_sha256": hashlib.sha256(str(repo).encode("utf-8")).hexdigest(),
        "batch_id": decision["batch_id"],
        "capture_set_sha256": decision["capture_set_sha256"],
        "capture_id": decision["capture_id"],
        "study_date": decision["study_date"],
        "formal_id": formal_id,
        "capture_payload_sha256": decision["capture_payload_sha256"],
        "stable_evidence_sha256": decision["stable_evidence_sha256"],
        "evidence_manifest_sha256": (decision.get("direct_evidence") or {}).get(
            "evidence_manifest_sha256"
        ),
        "evidence_bundle_sha256": (decision.get("direct_evidence") or {}).get(
            "evidence_bundle_sha256"
        ),
        "sol_decision_sha256": require_sha(decision_sha256, "decision hash"),
        "formal_row_sha256": formal_row_sha,
        "safe_card_sha256": card_sha,
    }
    paths = _runtime_paths(repo, runtime)
    _prepare_runtime_paths(paths)
    verification_parent = runtime / "verification-receipts"
    verification_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if verification_parent.is_symlink() or not verification_parent.is_dir():
        raise SolCurationError("already-current verification parent is unsafe")
    os.chmod(verification_parent, 0o700)
    root = verification_parent / repo_key(repo)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or not root.is_dir():
        raise SolCurationError("already-current verification root is unsafe")
    os.chmod(root, 0o700)
    content = canonical_bytes(material)
    digest = hashlib.sha256(content).hexdigest()
    path = root / f"{digest}.json"
    atomic_write(path, content)
    return {
        "status": "SEALED",
        "verification_sha256": digest,
        "verification_path": str(path),
        "sol_decision_sha256": decision_sha256,
        "formal_write_count": 0,
    }


def _already_current_receipt(
    repo: Path,
    runtime_root: Path,
    *,
    verification_sha256: str,
    decision: Mapping[str, Any],
    formal_id: str,
) -> None:
    root = runtime_root / "verification-receipts" / repo_key(repo)
    path = bounded_file(root / f"{verification_sha256}.json", root, "already-current verification")
    if sha256_file(path) != verification_sha256:
        raise SolCurationError("already-current verification hash mismatch")
    receipt = load_json(path, root=root, label="already-current verification")
    if (
        receipt.get("schema") != "already_current_verification_v1"
        or receipt.get("status") != "VERIFIED_CURRENT"
        or receipt.get("answer_safe") is not True
        or receipt.get("formal_write_count") != 0
        or receipt.get("repo_root_sha256")
        != hashlib.sha256(str(repo).encode("utf-8")).hexdigest()
        or receipt.get("batch_id") != decision["batch_id"]
        or receipt.get("capture_id") != decision["capture_id"]
        or receipt.get("study_date") != decision["study_date"]
        or receipt.get("formal_id") != formal_id
        or receipt.get("capture_payload_sha256") != decision["capture_payload_sha256"]
        or receipt.get("capture_set_sha256") != decision["capture_set_sha256"]
        or receipt.get("stable_evidence_sha256") != decision["stable_evidence_sha256"]
        or receipt.get("evidence_manifest_sha256")
        != (decision.get("direct_evidence") or {}).get("evidence_manifest_sha256")
        or receipt.get("evidence_bundle_sha256")
        != (decision.get("direct_evidence") or {}).get("evidence_bundle_sha256")
        or receipt.get("sol_decision_sha256") != sha256_value(decision)
    ):
        raise SolCurationError("already-current verification binding mismatch")
    formal_row_sha, card_sha = _formal_snapshot(repo, formal_id)
    if (
        receipt.get("formal_row_sha256") != formal_row_sha
        or receipt.get("safe_card_sha256") != card_sha
    ):
        raise SolCurationError("already-current formal state drifted after verification")


def terminal_binding(
    *,
    repo_root: str | Path,
    decision_sha256: str,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    formal_runtime_root: str | Path = DEFAULT_FORMAL_RUNTIME_ROOT,
) -> dict[str, Any]:
    repo = Path(repo_root).expanduser().resolve(strict=True)
    runtime = Path(runtime_root).expanduser().resolve()
    pointer, decision = _load_pointer(repo, runtime, decision_sha256)
    formal_runtime = Path(formal_runtime_root).expanduser().resolve()
    event, event_line_sha, event_object_sha = _capture_result_event(
        repo,
        batch_id=str(decision["batch_id"]),
        capture_id=str(decision["capture_id"]),
    )
    outcome = str(event.get("outcome") or "")
    if outcome not in TERMINAL_OUTCOMES:
        raise SolCurationError("unknown formal terminal outcome")
    intended = decision.get("intended_terminal")
    if intended == "needs_user" and outcome != "needs_user":
        raise SolCurationError("needs_user decision reached a different terminal")
    if intended == "already_current" and outcome != "already_current":
        raise SolCurationError("already_current decision reached a different terminal")
    canonical_sha = pointer.get("canonical_package_sha256")
    formal_id = event.get("formal_id")
    receipt_sha = event.get("receipt_sha256")
    verification_sha = event.get("verification_sha256")
    if outcome == "curated":
        if intended != "apply" or canonical_sha is None or not FORMAL_ID_RE.fullmatch(str(formal_id or "")):
            raise SolCurationError("curated terminal does not match the apply decision")
        _normal_receipt_and_package(
            repo,
            formal_runtime,
            receipt_sha256=require_sha(receipt_sha, "normal receipt hash"),
            canonical_package_sha256=require_sha(canonical_sha, "canonical package hash"),
            formal_id=str(formal_id),
        )
    elif outcome == "already_current":
        if canonical_sha is not None or not FORMAL_ID_RE.fullmatch(str(formal_id or "")):
            raise SolCurationError("already_current terminal has an unexpected package or identity")
        _already_current_receipt(
            repo,
            runtime,
            verification_sha256=require_sha(verification_sha, "verification hash"),
            decision=decision,
            formal_id=str(formal_id),
        )
    else:
        if formal_id is not None or receipt_sha is not None or verification_sha is not None:
            raise SolCurationError("failed terminal carries formal-success fields")
    decisions = decision.get("field_decisions") or []
    action_counts = {
        action: sum(
            1
            for row in decisions
            if isinstance(row, dict) and row.get("action") == action
        )
        for action in sorted(FIELD_ACTIONS)
    }
    adoption_outcome: str | None = None
    if (decision.get("luna") or {}).get("adoption_token"):
        if outcome not in {"curated", "already_current"}:
            adoption_outcome = "rejected"
        elif action_counts["modify"]:
            adoption_outcome = "modified_adopted"
        elif action_counts["adopt"]:
            adoption_outcome = "direct_adopted"
        else:
            adoption_outcome = "rejected"
    return {
        "schema": "sol_curation_terminal_binding_v1",
        "status": "VERIFIED",
        "sol_review_status": "sealed",
        "formal_curation_status": outcome,
        "field_decision_counts": action_counts,
        "derived_adoption_outcome": adoption_outcome,
        "adoption_token": (decision.get("luna") or {}).get("adoption_token"),
        "sol_decision_sha256": require_sha(decision_sha256, "decision hash"),
        "canonical_package_sha256": canonical_sha if outcome == "curated" else None,
        "sealed_canonical_package_sha256": canonical_sha,
        "item_result_event_id": event.get("event_id"),
        "item_result_event_sha256": event_line_sha,
        "item_result_event_line_sha256": event_line_sha,
        "item_result_event_object_sha256": event_object_sha,
        "terminal_outcome": outcome,
        "formal_id": formal_id,
        "normal_receipt_sha256": receipt_sha,
        "verification_sha256": verification_sha,
        "batch_id": decision["batch_id"],
        "capture_set_sha256": decision["capture_set_sha256"],
        "capture_id": decision["capture_id"],
        "capture_payload_sha256": decision["capture_payload_sha256"],
        "stable_evidence_sha256": decision["stable_evidence_sha256"],
        "evidence_manifest_sha256": (decision.get("direct_evidence") or {}).get(
            "evidence_manifest_sha256"
        ),
        "evidence_bundle_sha256": (decision.get("direct_evidence") or {}).get(
            "evidence_bundle_sha256"
        ),
        "luna_package_sha256": (decision.get("luna") or {}).get("package_sha256"),
        "luna_processing_contract_sha256": (decision.get("luna") or {}).get(
            "processing_contract_sha256"
        ),
        "luna_processing_fingerprint": (decision.get("luna") or {}).get(
            "processing_fingerprint"
        ),
        "luna_evidence_manifest_sha256": (decision.get("luna") or {}).get(
            "evidence_manifest_sha256"
        ),
        "luna_report_id": (decision.get("luna") or {}).get("report_id"),
        "luna_report_json_ref": (decision.get("luna") or {}).get(
            "report_json_ref"
        ),
        "luna_report_json_sha256": (decision.get("luna") or {}).get(
            "report_json_sha256"
        ),
        "luna_report_markdown_ref": (decision.get("luna") or {}).get(
            "report_markdown_ref"
        ),
        "luna_report_markdown_sha256": (decision.get("luna") or {}).get(
            "report_markdown_sha256"
        ),
        "luna_renderer_build_sha256": (decision.get("luna") or {}).get(
            "renderer_build_sha256"
        ),
        "luna_quality_receipt_sha256": (decision.get("luna") or {}).get(
            "quality_receipt_sha256"
        ),
        "luna_evidence_bundle_sha256": (decision.get("luna") or {}).get(
            "evidence_bundle_sha256"
        ),
        "formal_write_count": 0,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Seal and verify Sol nightly curation decisions")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument(
        "--formal-runtime-root", default=str(DEFAULT_FORMAL_RUNTIME_ROOT)
    )
    sub = parser.add_subparsers(dest="command", required=True)

    seal = sub.add_parser("seal")
    seal.add_argument("--study-date", required=True)
    seal.add_argument("--batch-id", required=True)
    seal.add_argument("--capture-set-sha256", required=True)
    seal.add_argument("--capture-id", required=True)
    seal.add_argument("--consumer-result", required=True)
    seal.add_argument("--decision-request", required=True)
    seal.add_argument("--preprocessor-root", required=True)
    seal.add_argument(
        "--private-evidence-root", default=str(DEFAULT_PRIVATE_EVIDENCE_ROOT)
    )

    terminal = sub.add_parser("terminal-binding")
    terminal.add_argument("--decision-sha256", required=True)
    already = sub.add_parser("seal-already-current")
    already.add_argument("--decision-sha256", required=True)
    already.add_argument("--formal-id", required=True)
    return parser


def main() -> int:
    os.umask(0o077)
    args = build_parser().parse_args()
    try:
        if args.command == "seal":
            result = seal_decision(
                repo_root=args.repo,
                study_date=args.study_date,
                batch_id=args.batch_id,
                capture_set_sha256=args.capture_set_sha256,
                capture_id=args.capture_id,
                consumer_result_path=args.consumer_result,
                decision_request_path=args.decision_request,
                preprocessor_root=args.preprocessor_root,
                private_evidence_root=args.private_evidence_root,
                runtime_root=args.runtime_root,
            )
        elif args.command == "terminal-binding":
            result = terminal_binding(
                repo_root=args.repo,
                decision_sha256=args.decision_sha256,
                runtime_root=args.runtime_root,
                formal_runtime_root=args.formal_runtime_root,
            )
        else:
            result = seal_already_current(
                repo_root=args.repo,
                decision_sha256=args.decision_sha256,
                formal_id=args.formal_id,
                runtime_root=args.runtime_root,
            )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (SolCurationError, capture_ledger.CaptureError, OSError) as exc:
        print(
            json.dumps(
                {
                    "schema": "sol_curation_decision_error_v1",
                    "status": "error",
                    "error_code": str(exc),
                    "formal_write_count": 0,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
