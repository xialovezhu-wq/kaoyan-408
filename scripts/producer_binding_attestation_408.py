"""Release-neutral Producer/foreground-Skill attestation sidecar."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping


FORBIDDEN_KEYS = {
    "release_id", "activation_id", "dispatcher_authority",
    "dispatcher_authority_fingerprint", "mcp_authority",
    "mcp_authority_fingerprint", "mcp_release_id",
    "producer_authority_fingerprint",
}
CAPTURE_LEDGER_RELATIVE_PATH = Path(
    "wiki/study_vaults/408-full/state/intake-curation/events.jsonl"
)
CAPTURE_EVENT_SCHEMA = "intake_fact_capture_event_v1"
MAX_CAPTURE_LEDGER_BYTES = 64 * 1024 * 1024
CAPTURE_ID_RE = re.compile(r"^CAP-\d{8}-[0-9a-f]{12}$")


class ProducerBindingError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def capture_receipt_sha256(value: Any) -> str:
    """Hash one canonical ledger event using the Producer writer contract."""

    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _assert_release_neutral(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key) in FORBIDDEN_KEYS:
                raise ProducerBindingError("producer attestation contains deployment identity")
            _assert_release_neutral(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_release_neutral(nested)


def _timestamp(value: str) -> dt.datetime:
    if not isinstance(value, str) or not value:
        raise ProducerBindingError("producer binding timestamp is invalid")
    try:
        parsed = dt.datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
    except (TypeError, ValueError) as exc:
        raise ProducerBindingError(
            "producer binding timestamp is invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProducerBindingError("producer binding timestamp must include timezone")
    return parsed.astimezone(dt.timezone.utc)


def load_descriptor(path: Path, *, subject: str) -> dict[str, Any]:
    descriptor = json.loads(path.read_text(encoding="utf-8"))
    expected = {
        "schema_version", "subject", "attestation_required_after",
        "foreground_skill", "producer", "capture_contract",
        "attestation_relative_root", "formal_write_count",
        "descriptor_content_sha256",
    }
    if (
        not isinstance(descriptor, dict)
        or set(descriptor) != expected
        or descriptor.get("schema_version") != "producer_binding_descriptor_v1"
        or descriptor.get("subject") != subject
        or descriptor.get("formal_write_count") != 0
    ):
        raise ProducerBindingError("producer binding descriptor invalid")
    core = {key: value for key, value in descriptor.items() if key != "descriptor_content_sha256"}
    if descriptor.get("descriptor_content_sha256") != sha256_value(core):
        raise ProducerBindingError("producer binding descriptor hash invalid")
    _timestamp(str(descriptor["attestation_required_after"]))
    relative_root = Path(str(descriptor["attestation_relative_root"]))
    if relative_root.is_absolute() or ".." in relative_root.parts:
        raise ProducerBindingError("producer binding sidecar root invalid")
    skill = descriptor.get("foreground_skill")
    if not isinstance(skill, dict):
        raise ProducerBindingError("foreground Skill binding missing")
    for prefix in ("authoritative", "installed"):
        raw_path = skill.get(f"{prefix}_path")
        digest = skill.get(f"{prefix}_sha256")
        candidate = Path(str(raw_path or ""))
        if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_file() or sha256_file(candidate) != digest:
            raise ProducerBindingError(f"foreground Skill {prefix} binding mismatch")
    authoritative_path = Path(str(skill.get("authoritative_path") or ""))
    installed_path = Path(str(skill.get("installed_path") or ""))
    if (
        skill.get("authoritative_sha256") != skill.get("installed_sha256")
        or authoritative_path.read_bytes() != installed_path.read_bytes()
    ):
        raise ProducerBindingError("foreground Skill authoritative/installed parity mismatch")
    producer = descriptor.get("producer")
    files = producer.get("source_files") if isinstance(producer, dict) else None
    if not isinstance(files, list) or not files:
        raise ProducerBindingError("producer source closure missing")
    normalized: list[dict[str, Any]] = []
    for row in files:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise ProducerBindingError("producer source closure invalid")
        candidate = Path(str(row["path"]))
        if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_file() or sha256_file(candidate) != row["sha256"]:
            raise ProducerBindingError("producer source closure mismatch")
        normalized.append(dict(row))
    if producer.get("source_closure_sha256") != sha256_value(normalized):
        raise ProducerBindingError("producer source closure hash invalid")
    contract = descriptor.get("capture_contract")
    files = contract.get("files") if isinstance(contract, dict) else None
    if not isinstance(files, list) or not files:
        raise ProducerBindingError("Capture contract closure missing")
    for row in files:
        candidate = Path(str((row or {}).get("path") or "")) if isinstance(row, dict) else Path("")
        if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_file() or sha256_file(candidate) != row.get("sha256"):
            raise ProducerBindingError("Capture contract closure mismatch")
    _assert_release_neutral(descriptor)
    return descriptor


def _bound_file(path: Path) -> dict[str, str]:
    candidate = path.expanduser().resolve(strict=True)
    if candidate.is_symlink() or not candidate.is_file():
        raise ProducerBindingError("producer binding source file invalid")
    return {"path": str(candidate), "sha256": sha256_file(candidate)}


def build_descriptor(
    *,
    subject: str,
    attestation_required_after: str,
    authoritative_skill: Path,
    installed_skill: Path,
    producer_files: list[Path],
    capture_contract_files: list[Path],
    attestation_relative_root: str,
) -> dict[str, Any]:
    """Build one resolved descriptor without embedding machine paths in source.

    Deployment and tests call this function with explicit paths and persist the
    result outside the version-controlled source tree.  The checked-in example
    documents placeholders only.
    """

    if subject != "cs408" or not producer_files or not capture_contract_files:
        raise ProducerBindingError("producer binding descriptor inputs invalid")
    _timestamp(attestation_required_after)
    authoritative = _bound_file(authoritative_skill)
    installed = _bound_file(installed_skill)
    if (
        authoritative["sha256"] != installed["sha256"]
        or Path(authoritative["path"]).read_bytes()
        != Path(installed["path"]).read_bytes()
    ):
        raise ProducerBindingError("foreground Skill authoritative/installed parity mismatch")
    relative_root = Path(attestation_relative_root)
    if relative_root.is_absolute() or ".." in relative_root.parts:
        raise ProducerBindingError("producer binding sidecar root invalid")
    producer_rows = [_bound_file(path) for path in producer_files]
    contract_rows = [_bound_file(path) for path in capture_contract_files]
    core = {
        "schema_version": "producer_binding_descriptor_v1",
        "subject": subject,
        "attestation_required_after": attestation_required_after,
        "foreground_skill": {
            "authoritative_path": authoritative["path"],
            "authoritative_sha256": authoritative["sha256"],
            "installed_path": installed["path"],
            "installed_sha256": installed["sha256"],
        },
        "producer": {
            "source_files": producer_rows,
            "source_closure_sha256": sha256_value(producer_rows),
        },
        "capture_contract": {"files": contract_rows},
        "attestation_relative_root": relative_root.as_posix(),
        "formal_write_count": 0,
    }
    _assert_release_neutral(core)
    return {**core, "descriptor_content_sha256": sha256_value(core)}


def write_descriptor(path: Path, descriptor: Mapping[str, Any]) -> None:
    """Persist a resolved descriptor atomically and reject conflicting bytes."""

    load_subject = str(descriptor.get("subject") or "")
    load_descriptor_payload = dict(descriptor)
    if load_subject != "cs408":
        raise ProducerBindingError("producer binding descriptor subject invalid")
    _assert_release_neutral(load_descriptor_payload)
    _atomic_no_clobber(path.expanduser().resolve(), load_descriptor_payload)


def _atomic_no_clobber(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_bytes(value)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != payload:
                raise ProducerBindingError("producer attestation no-clobber conflict")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def publish_attestation(
    *, descriptor_path: Path, repo_root: Path, subject: str,
    capture_id: str, capture_content_sha256: str, recorded_at: str,
) -> dict[str, Any]:
    descriptor = load_descriptor(descriptor_path, subject=subject)
    if _timestamp(recorded_at) < _timestamp(descriptor["attestation_required_after"]):
        return {
            "status": "historical_pre_attestation",
            "attestation_path": None,
            "attestation_sha256": None,
            "attestation_file_sha256": None,
            "formal_write_count": 0,
        }
    core = {
        "schema_version": "producer_binding_attestation_v1",
        "subject": subject,
        "capture_id": capture_id,
        "capture_content_sha256": capture_content_sha256,
        "foreground_skill": descriptor["foreground_skill"],
        "producer": descriptor["producer"],
        "capture_contract": descriptor["capture_contract"],
        "binding_descriptor_sha256": sha256_file(descriptor_path),
        "attestation_required_after": descriptor["attestation_required_after"],
        "formal_write_count": 0,
    }
    _assert_release_neutral(core)
    attestation = {**core, "attestation_sha256": sha256_value(core)}
    root = repo_root.resolve() / descriptor["attestation_relative_root"]
    path = root / f"{capture_id}.json"
    _atomic_no_clobber(path, attestation)
    return {
        "status": "attested",
        "attestation_path": str(path),
        "attestation_sha256": attestation["attestation_sha256"],
        "attestation_file_sha256": sha256_file(path),
        "formal_write_count": 0,
    }


def _read_capture_ledger(repo_root: Path) -> list[dict[str, Any]]:
    ledger = repo_root.resolve() / CAPTURE_LEDGER_RELATIVE_PATH
    try:
        before = ledger.lstat()
    except OSError as exc:
        raise ProducerBindingError("capture ledger unavailable") from exc
    if (
        ledger.is_symlink()
        or not ledger.is_file()
        or before.st_size <= 0
        or before.st_size > MAX_CAPTURE_LEDGER_BYTES
    ):
        raise ProducerBindingError("capture ledger unsafe")
    try:
        with ledger.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if (
                opened.st_dev != before.st_dev
                or opened.st_ino != before.st_ino
                or opened.st_size != before.st_size
            ):
                raise ProducerBindingError("capture ledger drifted before reopen")
            raw = handle.read(MAX_CAPTURE_LEDGER_BYTES + 1)
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise ProducerBindingError("capture ledger unavailable") from exc
    if (
        len(raw) != before.st_size
        or len(raw) > MAX_CAPTURE_LEDGER_BYTES
        or after.st_dev != before.st_dev
        or after.st_ino != before.st_ino
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
    ):
        raise ProducerBindingError("capture ledger drifted during reopen")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ProducerBindingError("capture ledger is not UTF-8") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProducerBindingError(
                f"capture ledger row {line_number} is invalid"
            ) from exc
        if not isinstance(value, dict):
            raise ProducerBindingError(
                f"capture ledger row {line_number} is invalid"
            )
        rows.append(value)
    return rows


def _reopen_capture_identity(
    *,
    repo_root: Path,
    capture_id: str,
    capture_content_sha256: str,
    capture_receipt_sha256_value: str,
    recorded_at: str,
) -> dict[str, Any]:
    if (
        not isinstance(capture_id, str)
        or not CAPTURE_ID_RE.fullmatch(capture_id)
    ):
        raise ProducerBindingError("capture identity invalid")
    for value, label in (
        (capture_content_sha256, "capture payload hash"),
        (capture_receipt_sha256_value, "capture receipt hash"),
    ):
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ProducerBindingError(f"{label} invalid")
    _timestamp(recorded_at)
    matches = [
        row
        for row in _read_capture_ledger(repo_root)
        if row.get("event_type") == "fact_captured"
        and row.get("capture_id") == capture_id
    ]
    if len(matches) != 1:
        raise ProducerBindingError("capture ledger identity is not unique")
    event = matches[0]
    capture = event.get("capture")
    idempotency_key = (
        str(capture.get("idempotency_key") or "")
        if isinstance(capture, dict)
        else ""
    )
    study_date = (
        str(capture.get("study_date") or "")
        if isinstance(capture, dict)
        else ""
    )
    expected_capture_id = (
        f"CAP-{study_date.replace('-', '')}-"
        f"{hashlib.sha256(idempotency_key.encode()).hexdigest()[:12]}"
    )
    if (
        event.get("schema") != CAPTURE_EVENT_SCHEMA
        or event.get("capture_id") != capture_id
        or event.get("payload_sha256") != capture_content_sha256
        or event.get("created_at") != recorded_at
        or capture_receipt_sha256(event) != capture_receipt_sha256_value
        or not isinstance(capture, dict)
        or expected_capture_id != capture_id
        or capture_receipt_sha256(capture) != capture_content_sha256
    ):
        raise ProducerBindingError("capture ledger binding mismatch")
    return event


def commit_capture_with_producer_attestation(
    *,
    descriptor_path: Path,
    repo_root: Path,
    subject: str,
    capture_id: str,
    capture_content_sha256: str,
    capture_receipt_sha256: str,
    recorded_at: str,
    recovered: bool,
) -> dict[str, Any]:
    """Reopen one committed Capture before publishing its immutable sidecar.

    This is the shared finalizer for both the ordinary and managed-hot 408
    Producer paths.  The ledger is authoritative; caller-returned values never
    authorize an attestation on their own.
    """

    if subject != "cs408" or not isinstance(recovered, bool):
        raise ProducerBindingError("capture finalizer inputs invalid")
    repo = repo_root.expanduser().resolve()
    if not repo.is_dir():
        raise ProducerBindingError("capture repository unavailable")
    event = _reopen_capture_identity(
        repo_root=repo,
        capture_id=capture_id,
        capture_content_sha256=capture_content_sha256,
        capture_receipt_sha256_value=capture_receipt_sha256,
        recorded_at=recorded_at,
    )
    attestation_recorded_at = recorded_at
    if recorded_at == "1970-01-01T00:00:00+00:00":
        observed_at = str(
            ((event.get("capture") or {}).get("user_facts") or {}).get(
                "observed_at"
            )
            or ""
        )
        _timestamp(observed_at)
        attestation_recorded_at = observed_at
    descriptor = load_descriptor(descriptor_path, subject=subject)
    sidecar_path = (
        repo / str(descriptor["attestation_relative_root"]) / f"{capture_id}.json"
    )
    existed_before = sidecar_path.exists() or sidecar_path.is_symlink()
    published = publish_attestation(
        descriptor_path=descriptor_path,
        repo_root=repo,
        subject=subject,
        capture_id=capture_id,
        capture_content_sha256=capture_content_sha256,
        recorded_at=attestation_recorded_at,
    )
    if published["status"] == "attested":
        try:
            value = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProducerBindingError("producer attestation reopen failed") from exc
        if (
            not isinstance(value, dict)
            or value.get("capture_id") != capture_id
            or value.get("capture_content_sha256") != capture_content_sha256
            or value.get("attestation_sha256")
            != published["attestation_sha256"]
            or sha256_file(sidecar_path) != published["attestation_file_sha256"]
        ):
            raise ProducerBindingError("producer attestation reopen mismatch")
    return {
        "status": "finalized",
        "producer_binding_status": published["status"],
        "producer_attestation_path": published["attestation_path"],
        "producer_attestation_sha256": published["attestation_sha256"],
        "producer_attestation_file_sha256": published[
            "attestation_file_sha256"
        ],
        "capture_identity": {
            "capture_id": capture_id,
            "capture_content_sha256": capture_content_sha256,
            "capture_receipt_sha256": capture_receipt_sha256,
        },
        "recorded_at": recorded_at,
        "attestation_recorded_at": attestation_recorded_at,
        "idempotent": bool(existed_before),
        "recovered": recovered,
        "handoff_ready_allowed": published["status"] == "attested",
        "formal_write_count": 0,
    }
