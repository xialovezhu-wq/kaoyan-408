#!/usr/bin/env python3
"""Managed cold-path answer-isolation attestation for original question surfaces.

The pipeline is prepare -> independent review submission -> finalize.  A local
HMAC key proves that the review request was issued by this managed pipeline and
binds its nonce and exact asset set.  Producer/reviewer actor and execution IDs
are managed execution labels, not cryptographic proof of a human identity; the
pipeline only proves that the two bound labels are distinct.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, UnidentifiedImageError


SCHEMA = "question_answer_isolation_attestation_v1"
REQUEST_SCHEMA = "question_answer_isolation_review_request_v1"
SUBMISSION_SCHEMA = "question_answer_isolation_review_submission_v1"
ATTESTATION_KIND = "independent_answer_isolation_verification"
IDENTITY_SCOPE = "managed_execution_labels_not_cryptographic_person_identities"
CONFIRMATION = "reviewed_question_surface_contains_no_answer_or_solution"
FORMAL_NODE_RE = re.compile(r"^(?:DS|CO|OS|CN)_(?:\d{4}|UNK)_\d{3}$")
QUESTION_ASSET_RE = re.compile(
    r"^question-\d{2}\.(?:png|jpe?g|webp)$", re.IGNORECASE
)
ACTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,80}$")
EXECUTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,120}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
NONCE_RE = re.compile(r"^[0-9a-f]{64}$")
METHODS = {"independent_visual_review", "independent_asset_review"}
SUPPORTED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}
WORK_DIR = ".answer-isolation-work"
KEY_FILE = ".issuance.key"


class QuestionSourceAttestationError(RuntimeError):
    """A stable cold-path attestation error."""


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def asset_set_sha256(entries: list[dict[str, Any]]) -> str:
    return hashlib.sha256(canonical_json_bytes(entries)).hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def normalize_identity(value: object, *, role: str, label: str) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {
        "actor_id",
        "execution_id",
        "role",
    }:
        raise QuestionSourceAttestationError(f"{label} identity is incomplete")
    result = {
        "actor_id": str(value.get("actor_id") or ""),
        "execution_id": str(value.get("execution_id") or ""),
        "role": str(value.get("role") or ""),
    }
    if (
        result["role"] != role
        or not ACTOR_RE.fullmatch(result["actor_id"])
        or not EXECUTION_RE.fullmatch(result["execution_id"])
    ):
        raise QuestionSourceAttestationError(f"{label} identity is invalid")
    return result


def question_asset_entries(
    *, details_root: str | Path, formal_node_id: str, asset_paths: Iterable[str | Path]
) -> list[dict[str, Any]]:
    root = Path(details_root).expanduser().resolve()
    if not root.is_dir() or not FORMAL_NODE_RE.fullmatch(formal_node_id):
        raise QuestionSourceAttestationError("details root or formal node is invalid")
    values = list(asset_paths)
    if not values:
        raise QuestionSourceAttestationError("at least one explicit question asset is required")
    entries: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for raw in values:
        candidate = Path(raw).expanduser()
        unresolved = candidate if candidate.is_absolute() else root / candidate
        if unresolved.is_symlink():
            raise QuestionSourceAttestationError("question asset must not be a symlink")
        path = unresolved.resolve()
        if (
            path in seen
            or not _inside(path, root)
            or not path.is_file()
            or path.stat().st_size <= 0
            or path.parent.name != formal_node_id
            or not QUESTION_ASSET_RE.fullmatch(path.name)
        ):
            raise QuestionSourceAttestationError("question asset identity is invalid")
        try:
            with Image.open(path) as image:
                image.load()
                if image.format not in SUPPORTED_IMAGE_FORMATS:
                    raise QuestionSourceAttestationError(
                        "question asset image format is unsupported"
                    )
        except QuestionSourceAttestationError:
            raise
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            raise QuestionSourceAttestationError(
                "question asset image is not decodable"
            ) from exc
        seen.add(path)
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return sorted(entries, key=lambda item: item["path"])


def _key(root: Path, *, create: bool) -> bytes:
    work = root / WORK_DIR
    path = work / KEY_FILE
    if not path.exists() and create:
        work.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            descriptor = None
        if descriptor is not None:
            try:
                os.write(descriptor, secrets.token_bytes(32))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    try:
        info = path.lstat()
        value = path.read_bytes()
    except OSError as exc:
        raise QuestionSourceAttestationError("attestation issuance key is unavailable") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_mode & 0o077
        or len(value) != 32
    ):
        raise QuestionSourceAttestationError("attestation issuance key is unsafe")
    return value


def _request_signature(payload: dict[str, Any], key: bytes) -> str:
    core = dict(payload)
    core.pop("request_signature", None)
    return hmac.new(key, canonical_json_bytes(core), hashlib.sha256).hexdigest()


def _atomic_write(path: Path, payload: dict[str, Any], *, replace: bool = False) -> str:
    data = pretty_json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists() and not replace:
        if path.is_file() and not path.is_symlink() and path.read_bytes() == data:
            return hashlib.sha256(data).hexdigest()
        raise QuestionSourceAttestationError("managed artifact already exists with different bytes")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return hashlib.sha256(data).hexdigest()


def _relative_file(root: Path, raw: object, label: str) -> tuple[Path, str]:
    locator = Path(str(raw or ""))
    if not str(raw or "") or locator.is_absolute() or ".." in locator.parts:
        raise QuestionSourceAttestationError(f"{label} reference is invalid")
    unresolved = root / locator
    if unresolved.is_symlink():
        raise QuestionSourceAttestationError(f"{label} must not be a symlink")
    path = unresolved.resolve()
    if not _inside(path, root) or not path.is_file() or path.stat().st_size <= 0:
        raise QuestionSourceAttestationError(f"{label} is unavailable")
    return path, path.relative_to(root).as_posix()


def prepare_request(
    *,
    details_root: str | Path,
    formal_node_id: str,
    question_assets: list[dict[str, Any]],
    asset_producer_identity: dict[str, Any],
    verifier_identity: dict[str, Any],
    verification_method: str,
) -> dict[str, Any]:
    root = Path(details_root).expanduser().resolve()
    producer = normalize_identity(
        asset_producer_identity, role="question_surface_producer", label="asset producer"
    )
    verifier = normalize_identity(
        verifier_identity, role="answer_isolation_verifier", label="answer-isolation verifier"
    )
    if (
        producer["actor_id"] == verifier["actor_id"]
        or producer["execution_id"] == verifier["execution_id"]
    ):
        raise QuestionSourceAttestationError("producer and verifier must be distinct executions")
    if verification_method not in METHODS:
        raise QuestionSourceAttestationError("verification method is invalid")
    nonce = secrets.token_hex(32)
    core = {
        "schema": REQUEST_SCHEMA,
        "formal_node_id": formal_node_id,
        "request_nonce": nonce,
        "identity_scope": IDENTITY_SCOPE,
        "verification_method": verification_method,
        "asset_producer_identity": producer,
        "assigned_verifier_identity": verifier,
        "question_assets": question_assets,
        "question_asset_set_sha256": asset_set_sha256(question_assets),
        "required_confirmation": CONFIRMATION,
        "formal_write_count": 0,
    }
    request_id = "QAIR-" + hashlib.sha256(canonical_json_bytes(core)).hexdigest()[:24].upper()
    unsigned = {**core, "request_id": request_id}
    request = {**unsigned, "request_signature": _request_signature(unsigned, _key(root, create=True))}
    request_path = root / WORK_DIR / request_id / "request.json"
    request_sha = _atomic_write(request_path, request)
    return {
        "status": "prepared",
        "request_id": request_id,
        "request_ref": request_path.relative_to(root).as_posix(),
        "request_sha256": request_sha,
        "submission_schema": SUBMISSION_SCHEMA,
        "required_confirmation": CONFIRMATION,
        "identity_scope": IDENTITY_SCOPE,
        "formal_write_count": 0,
    }


def _validate_request(root: Path, path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QuestionSourceAttestationError("review request is unreadable") from exc
    if not isinstance(payload, dict):
        raise QuestionSourceAttestationError("review request must be an object")
    producer = normalize_identity(
        payload.get("asset_producer_identity"),
        role="question_surface_producer",
        label="asset producer",
    )
    verifier = normalize_identity(
        payload.get("assigned_verifier_identity"),
        role="answer_isolation_verifier",
        label="assigned verifier",
    )
    signature = str(payload.get("request_signature") or "")
    expected_keys = {
        "schema", "request_id", "formal_node_id", "request_nonce", "identity_scope",
        "verification_method", "asset_producer_identity", "assigned_verifier_identity",
        "question_assets", "question_asset_set_sha256", "required_confirmation",
        "formal_write_count", "request_signature",
    }
    if (
        set(payload) != expected_keys
        or payload.get("schema") != REQUEST_SCHEMA
        or not re.fullmatch(r"QAIR-[0-9A-F]{24}", str(payload.get("request_id") or ""))
        or not FORMAL_NODE_RE.fullmatch(str(payload.get("formal_node_id") or ""))
        or not NONCE_RE.fullmatch(str(payload.get("request_nonce") or ""))
        or payload.get("identity_scope") != IDENTITY_SCOPE
        or payload.get("verification_method") not in METHODS
        or producer["actor_id"] == verifier["actor_id"]
        or producer["execution_id"] == verifier["execution_id"]
        or not isinstance(payload.get("question_assets"), list)
        or payload.get("question_asset_set_sha256")
        != asset_set_sha256(payload.get("question_assets") or [])
        or payload.get("required_confirmation") != CONFIRMATION
        or int(payload.get("formal_write_count", -1)) != 0
        or not SHA_RE.fullmatch(signature)
        or not hmac.compare_digest(signature, _request_signature(payload, _key(root, create=False)))
    ):
        raise QuestionSourceAttestationError("review request is invalid or unauthentic")
    return payload


def _validate_submission(
    path: Path, *, request: dict[str, Any], request_sha256: str
) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QuestionSourceAttestationError("review submission is unreadable") from exc
    if not isinstance(payload, dict):
        raise QuestionSourceAttestationError("review submission must be an object")
    verifier = normalize_identity(
        payload.get("verifier_identity"),
        role="answer_isolation_verifier",
        label="review submission verifier",
    )
    if (
        set(payload)
        != {
            "schema", "request_id", "request_sha256", "request_nonce",
            "verifier_identity", "question_asset_set_sha256", "answer_free_verified",
            "solution_free_verified", "reviewed_asset_bytes_not_filename_only",
            "confirmation", "formal_write_count",
        }
        or payload.get("schema") != SUBMISSION_SCHEMA
        or payload.get("request_id") != request.get("request_id")
        or payload.get("request_sha256") != request_sha256
        or payload.get("request_nonce") != request.get("request_nonce")
        or verifier != request.get("assigned_verifier_identity")
        or payload.get("question_asset_set_sha256")
        != request.get("question_asset_set_sha256")
        or payload.get("answer_free_verified") is not True
        or payload.get("solution_free_verified") is not True
        or payload.get("reviewed_asset_bytes_not_filename_only") is not True
        or payload.get("confirmation") != CONFIRMATION
        or int(payload.get("formal_write_count", -1)) != 0
    ):
        raise QuestionSourceAttestationError("review submission is invalid or incomplete")
    return payload


def finalize_attestation(
    *,
    details_root: str | Path,
    request_ref: str | Path,
    submission_ref: str | Path,
    output: str | Path,
    replace: bool = False,
) -> dict[str, Any]:
    root = Path(details_root).expanduser().resolve()
    request_path, normalized_request = _relative_file(root, request_ref, "review request")
    submission_path, normalized_submission = _relative_file(root, submission_ref, "review submission")
    request = _validate_request(root, request_path)
    request_sha = sha256_file(request_path)
    submission = _validate_submission(
        submission_path, request=request, request_sha256=request_sha
    )
    formal_node_id = str(request["formal_node_id"])
    current_assets = question_asset_entries(
        details_root=root,
        formal_node_id=formal_node_id,
        asset_paths=[item["path"] for item in request["question_assets"]],
    )
    if current_assets != request["question_assets"]:
        raise QuestionSourceAttestationError("reviewed question assets drifted before finalize")
    attestation = {
        "schema": SCHEMA,
        "formal_node_id": formal_node_id,
        "attestation_kind": ATTESTATION_KIND,
        "identity_scope": IDENTITY_SCOPE,
        "verification_method": request["verification_method"],
        "asset_producer_identity": request["asset_producer_identity"],
        "verifier_identity": submission["verifier_identity"],
        "question_assets": current_assets,
        "question_asset_set_sha256": request["question_asset_set_sha256"],
        "request_ref": normalized_request,
        "request_sha256": request_sha,
        "request_nonce_sha256": hashlib.sha256(
            str(request["request_nonce"]).encode("utf-8")
        ).hexdigest(),
        "review_submission_ref": normalized_submission,
        "review_submission_sha256": sha256_file(submission_path),
        "answer_isolated": True,
        "solution_or_answer_content_absent": True,
        "formal_write_count": 0,
    }
    output_path = Path(output).expanduser().resolve()
    expected = root / "assets" / formal_node_id / "answer-isolation-attestation.json"
    if output_path != expected.resolve():
        raise QuestionSourceAttestationError(
            "attestation output must be the managed formal-node attestation file"
        )
    digest = _atomic_write(output_path, attestation, replace=replace)
    return {
        "status": "finalized",
        "attestation_path": str(output_path),
        "attestation_sha256": digest,
        "question_asset_set_sha256": attestation["question_asset_set_sha256"],
        "identity_scope": IDENTITY_SCOPE,
        "formal_write_count": 0,
    }


def validate_attestation_chain(
    *,
    details_root: str | Path,
    attestation_path: str | Path,
    formal_node_id: str,
    expected_assets: list[dict[str, Any]],
) -> dict[str, Any]:
    root = Path(details_root).expanduser().resolve()
    path = Path(attestation_path).expanduser().resolve()
    if not _inside(path, root) or not path.is_file() or path.is_symlink():
        raise QuestionSourceAttestationError("attestation is outside the trusted details root")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QuestionSourceAttestationError("attestation is unreadable") from exc
    if not isinstance(payload, dict):
        raise QuestionSourceAttestationError("attestation must be an object")
    expected_keys = {
        "schema", "formal_node_id", "attestation_kind", "identity_scope",
        "verification_method", "asset_producer_identity", "verifier_identity",
        "question_assets", "question_asset_set_sha256", "request_ref",
        "request_sha256", "request_nonce_sha256", "review_submission_ref",
        "review_submission_sha256", "answer_isolated",
        "solution_or_answer_content_absent", "formal_write_count",
    }
    producer = normalize_identity(
        payload.get("asset_producer_identity"),
        role="question_surface_producer",
        label="asset producer",
    )
    verifier = normalize_identity(
        payload.get("verifier_identity"),
        role="answer_isolation_verifier",
        label="answer-isolation verifier",
    )
    if (
        set(payload) != expected_keys
        or payload.get("schema") != SCHEMA
        or payload.get("formal_node_id") != formal_node_id
        or payload.get("attestation_kind") != ATTESTATION_KIND
        or payload.get("identity_scope") != IDENTITY_SCOPE
        or payload.get("verification_method") not in METHODS
        or producer["actor_id"] == verifier["actor_id"]
        or producer["execution_id"] == verifier["execution_id"]
        or payload.get("question_assets") != expected_assets
        or payload.get("question_asset_set_sha256") != asset_set_sha256(expected_assets)
        or not SHA_RE.fullmatch(str(payload.get("request_sha256") or ""))
        or not SHA_RE.fullmatch(str(payload.get("request_nonce_sha256") or ""))
        or not SHA_RE.fullmatch(str(payload.get("review_submission_sha256") or ""))
        or payload.get("answer_isolated") is not True
        or payload.get("solution_or_answer_content_absent") is not True
        or int(payload.get("formal_write_count", -1)) != 0
    ):
        raise QuestionSourceAttestationError("answer-isolation attestation is invalid")
    request_path, _ = _relative_file(root, payload.get("request_ref"), "review request")
    submission_path, _ = _relative_file(
        root, payload.get("review_submission_ref"), "review submission"
    )
    if (
        sha256_file(request_path) != payload["request_sha256"]
        or sha256_file(submission_path) != payload["review_submission_sha256"]
    ):
        raise QuestionSourceAttestationError("attestation provenance drifted")
    request = _validate_request(root, request_path)
    if hashlib.sha256(str(request["request_nonce"]).encode("utf-8")).hexdigest() != payload["request_nonce_sha256"]:
        raise QuestionSourceAttestationError("attestation request nonce drifted")
    submission = _validate_submission(
        submission_path,
        request=request,
        request_sha256=payload["request_sha256"],
    )
    if (
        request["formal_node_id"] != formal_node_id
        or request["question_assets"] != expected_assets
        or request["asset_producer_identity"] != producer
        or submission["verifier_identity"] != verifier
    ):
        raise QuestionSourceAttestationError("attestation chain identity drifted")
    return payload


def _json_object(raw: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise QuestionSourceAttestationError(f"{label} JSON is invalid") from exc
    if not isinstance(value, dict):
        raise QuestionSourceAttestationError(f"{label} must be an object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--details-root", required=True)
    prepare.add_argument("--formal-node-id", required=True)
    prepare.add_argument("--question-asset", action="append", required=True)
    prepare.add_argument("--asset-producer-identity-json", required=True)
    prepare.add_argument("--verifier-identity-json", required=True)
    prepare.add_argument("--verification-method", choices=sorted(METHODS), required=True)
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--details-root", required=True)
    finalize.add_argument("--request", required=True)
    finalize.add_argument("--submission", required=True)
    finalize.add_argument("--output", required=True)
    finalize.add_argument("--replace", action="store_true")
    verify = sub.add_parser("verify")
    verify.add_argument("--details-root", required=True)
    verify.add_argument("--formal-node-id", required=True)
    verify.add_argument("--question-asset", action="append", required=True)
    verify.add_argument("--attestation", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            assets = question_asset_entries(
                details_root=args.details_root,
                formal_node_id=args.formal_node_id,
                asset_paths=args.question_asset,
            )
            result = prepare_request(
                details_root=args.details_root,
                formal_node_id=args.formal_node_id,
                question_assets=assets,
                asset_producer_identity=_json_object(
                    args.asset_producer_identity_json, "asset producer identity"
                ),
                verifier_identity=_json_object(
                    args.verifier_identity_json, "verifier identity"
                ),
                verification_method=args.verification_method,
            )
        elif args.command == "finalize":
            result = finalize_attestation(
                details_root=args.details_root,
                request_ref=args.request,
                submission_ref=args.submission,
                output=args.output,
                replace=bool(args.replace),
            )
        else:
            assets = question_asset_entries(
                details_root=args.details_root,
                formal_node_id=args.formal_node_id,
                asset_paths=args.question_asset,
            )
            checked = validate_attestation_chain(
                details_root=args.details_root,
                attestation_path=args.attestation,
                formal_node_id=args.formal_node_id,
                expected_assets=assets,
            )
            result = {
                "status": "verified",
                "attestation_sha256": sha256_file(Path(args.attestation).resolve()),
                "question_asset_set_sha256": checked["question_asset_set_sha256"],
                "identity_scope": IDENTITY_SCOPE,
                "formal_write_count": 0,
            }
    except QuestionSourceAttestationError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
