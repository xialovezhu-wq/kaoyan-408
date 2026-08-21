"""Verify the frozen, historical CS408 source-closure manifest.

This module is intentionally a standalone verification utility.  The runtime
Capture and review paths do not import it, and neither verification mode reads
the historical root unless ``external`` is explicitly given a source root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "study-intake-historical-source-closure-v1"
SUBJECT = "cs408"
EXPECTED_BUNDLE_SHA256 = (
    "8a0067b3b9d3aa3342fcdeb8186343181467d3daa98f7549924d8359b4848428"
)
EXPECTED_FILE_COUNT = 16
EXPECTED_TOTAL_BYTES = 1_028_860
ALLOWED_CLASSIFICATIONS = (
    "restore_required",
    "evidence_only",
    "replaced_by_current",
    "reject_private_or_runtime",
)
ALGORITHM_SPEC: dict[str, str] = {
    "name": "sha256-file-lines-v1",
    "file_bytes": (
        "Read each file as raw bytes; do not normalize, decode, or re-encode "
        "file contents."
    ),
    "record": "<file_sha256><two spaces><repo-relative-path><LF>",
    "aggregate": (
        "SHA-256 of the UTF-8 bytes of all records concatenated in "
        "ordered_paths order."
    ),
    "ordering": "Use ordered_paths exactly; do not sort or repackage.",
}

# These are the frozen source-bundle facts.  Keeping them here makes the
# verifier fail closed if the checked-in manifest is edited as a convenience
# rather than regenerated from the reviewed evidence.
EXPECTED_ROWS: tuple[tuple[str, str, int], ...] = (
    (
        "scripts/capture_hot_writer_408.py",
        "cbd1fc4ed0e75641bf69e6a4c12c8c85ef25f80b5c37fd75aef149fcd7f57724",
        110871,
    ),
    (
        "scripts/review_outcome_hot_408.py",
        "fe5c0bb2ba491ad4550fdf69bf08a9631417a79233e392a5b71aeb2811bb0ed6",
        40843,
    ),
    (
        "scripts/image_integrity_408.py",
        "f3d9219b2d08a8c493dbc034b040bc6374aabbf3885c191b42eb3ccc67a99cc1",
        9135,
    ),
    (
        "scripts/intake_lib_408.py",
        "43b2c1da7c032ec0771fdba40d99c3186b8529160d95b6a96540d67f0c2101fa",
        16496,
    ),
    (
        "scripts/bounded_jsonl_index_408.py",
        "f6136517b324faf6180ed3c29e97eac0e87281d1f51f22adaf9305d2a8ff10df",
        48721,
    ),
    (
        "scripts/linked_practice_source_408.py",
        "8f2edd46af7c129a2f3c204ac4a09b28fa1823c3a7c4d961b7887dc789aeef8e",
        43671,
    ),
    (
        "scripts/question_source_attestation_408.py",
        "8794e8323fae8f9e5856064103542fd2718f5e094290cc98c02e31a4dc781b5b",
        25071,
    ),
    (
        "scripts/review_feedback_loop.py",
        "ab61d654ce15e8b126cc08ca42c96cf0770835b27e44e8e50e49d361ea31d411",
        197955,
    ),
    (
        "scripts/review_hot_state_408.py",
        "7a6efbaaf496d40a469e3b2964892a9820f2e0b86aace238dcd261af438094db",
        91975,
    ),
    (
        "scripts/review_hot_projection_408.py",
        "8913b4ad02fe11cace9940c8c431b1f3db5ac22d3d56609fba74eeda4089453e",
        47928,
    ),
    (
        "scripts/capture_commit_index_408.py",
        "9ff8e6f7a24e017fe4c75dc97f321cebb46775212a7df6b460a0cfe8d33233e3",
        43489,
    ),
    (
        "scripts/morning_review_session.py",
        "21c8ffeacd3fa32e39f84d31299ade573d98908dab85cb57b1e746bfb7c9b3ee",
        207642,
    ),
    (
        "scripts/morning_session_hot_state_408.py",
        "fdf07cb8831da07678d2e1d52eb1719528af8a49267d8a87241e5c9f85bd702f",
        54763,
    ),
    (
        "scripts/morning_review_prepared_pack_408.py",
        "e194e6beb5233148beac9aac47878d9002df1ecf83ad65b7311bf3419cf1f8fd",
        73063,
    ),
    (
        "schema/morning-review-backflow-policy-v1.json",
        "77345d42dfbc4d2a1560a0b0d7e9a31a81184a20f438759a8b60d7721fd04017",
        543,
    ),
    (
        "tests/test_morning_review_session_hot_integration_408.py",
        "014983fa3fbf8a63e0db99b673b217bf38d4fb4411854470dff1e2db4d95059d",
        16694,
    ),
)
EXPECTED_CLASSIFICATIONS: tuple[str, ...] = (
    "replaced_by_current",
    "replaced_by_current",
    "replaced_by_current",
    "replaced_by_current",
    "replaced_by_current",
    "replaced_by_current",
    "evidence_only",
    "replaced_by_current",
    "replaced_by_current",
    "replaced_by_current",
    "replaced_by_current",
    "replaced_by_current",
    "replaced_by_current",
    "replaced_by_current",
    "replaced_by_current",
    "replaced_by_current",
)
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class VerificationError(RuntimeError):
    """A fail-closed, user-actionable verification error."""


def _is_safe_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and "." not in path.parts
        and ".." not in path.parts
        and path.as_posix() == value
    )


def _checked_path(root: Path, relative: str) -> Path:
    if not _is_safe_relative_path(relative):
        raise VerificationError(f"unsafe relative path: {relative!r}")
    root = root.expanduser().resolve()
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    if candidate.is_symlink():
        raise VerificationError(f"symlink is not an accepted source file: {relative}")
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root)
    except ValueError as exc:
        raise VerificationError(f"path escapes root: {relative}") from exc
    return candidate


def _raw_file(path: Path, relative: str) -> bytes:
    if not path.is_file() or path.is_symlink():
        raise VerificationError(f"source file missing or not regular: {relative}")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise VerificationError(f"cannot read source file: {relative}") from exc


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ordered_bytes_digest(rows: Iterable[tuple[str, str]]) -> str:
    """Return the bundle digest for ordered ``(file_sha256, path)`` rows."""

    payload = b"".join(
        f"{file_sha256}  {relative_path}\n".encode("utf-8")
        for file_sha256, relative_path in rows
    )
    return _sha256(payload)


def _load_manifest(repo_root: Path) -> dict[str, Any]:
    manifest_path = repo_root / "schema" / "study-intake-historical-source-closure-v1.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise VerificationError("historical source-closure manifest is missing") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError("historical source-closure manifest is unreadable") from exc
    if not isinstance(manifest, dict):
        raise VerificationError("historical source-closure manifest must be an object")
    _validate_manifest(manifest)
    return manifest


def _validate_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise VerificationError("manifest schema_version mismatch")
    if manifest.get("subject") != SUBJECT:
        raise VerificationError("manifest subject mismatch")
    if manifest.get("bundle_sha256") != EXPECTED_BUNDLE_SHA256:
        raise VerificationError("manifest bundle_sha256 mismatch")
    if manifest.get("ordered_raw_bytes_algorithm") != ALGORITHM_SPEC:
        raise VerificationError("manifest ordered raw-bytes algorithm mismatch")
    ordered_paths = manifest.get("ordered_paths")
    if ordered_paths != [row[0] for row in EXPECTED_ROWS]:
        raise VerificationError("manifest ordered_paths mismatch")
    if manifest.get("file_count") != EXPECTED_FILE_COUNT:
        raise VerificationError("manifest file_count mismatch")
    if manifest.get("total_bytes") != EXPECTED_TOTAL_BYTES:
        raise VerificationError("manifest total_bytes mismatch")
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != EXPECTED_FILE_COUNT:
        raise VerificationError("manifest files list mismatch")

    metadata_rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            raise VerificationError(f"manifest file entry {index} is not an object")
        path, expected_sha256, expected_size = EXPECTED_ROWS[index]
        if entry.get("path") != path or entry.get("path") in seen:
            raise VerificationError(f"manifest file path mismatch at index {index}")
        seen.add(path)
        if not _is_safe_relative_path(entry.get("path")):
            raise VerificationError(f"manifest file path is not safe: {path}")
        if entry.get("file_sha256") != expected_sha256 or not HEX64.fullmatch(
            str(entry.get("file_sha256"))
        ):
            raise VerificationError(f"manifest file hash mismatch: {path}")
        if entry.get("size") != expected_size or not isinstance(
            entry.get("size"), int
        ) or isinstance(entry.get("size"), bool):
            raise VerificationError(f"manifest file size mismatch: {path}")
        classification = entry.get("classification")
        if classification not in ALLOWED_CLASSIFICATIONS:
            raise VerificationError(f"manifest classification is invalid: {path}")
        if classification != EXPECTED_CLASSIFICATIONS[index]:
            raise VerificationError(f"manifest classification mismatch: {path}")
        caller = entry.get("active_caller_or_reference")
        if not isinstance(caller, str) or not caller.strip() or "\n" in caller:
            raise VerificationError(f"manifest caller/reference is invalid: {path}")
        replacement = entry.get("replacement_reference")
        if classification == "replaced_by_current":
            if not _is_safe_relative_path(replacement):
                raise VerificationError(f"replacement reference is invalid: {path}")
        elif replacement is not None:
            raise VerificationError(f"unexpected replacement reference: {path}")
        metadata_rows.append((expected_sha256, path))

    if sum(row[2] for row in EXPECTED_ROWS) != EXPECTED_TOTAL_BYTES:
        raise VerificationError("verifier frozen total_bytes is inconsistent")
    if ordered_bytes_digest(metadata_rows) != EXPECTED_BUNDLE_SHA256:
        raise VerificationError("manifest file metadata does not reproduce bundle hash")


def _classification_counts(manifest: Mapping[str, Any]) -> dict[str, int]:
    counts = Counter(entry["classification"] for entry in manifest["files"])
    return {classification: counts.get(classification, 0) for classification in ALLOWED_CLASSIFICATIONS}


def _verify_external(repo_root: Path, source_root: Path) -> dict[str, Any]:
    manifest = _load_manifest(repo_root)
    if not source_root.expanduser().is_dir():
        raise VerificationError("external source root is missing or not a directory")
    actual_rows: list[tuple[str, str]] = []
    for entry in manifest["files"]:
        relative = entry["path"]
        data = _raw_file(_checked_path(source_root, relative), relative)
        actual_sha256 = _sha256(data)
        actual_size = len(data)
        if actual_sha256 != entry["file_sha256"] or actual_size != entry["size"]:
            raise VerificationError(f"historical source bytes mismatch: {relative}")
        actual_rows.append((actual_sha256, relative))
    aggregate = ordered_bytes_digest(actual_rows)
    if aggregate != manifest["bundle_sha256"]:
        raise VerificationError("historical source aggregate hash mismatch")
    return _success_payload("external", manifest, restore_required=[])


def _verify_canonical(repo_root: Path) -> dict[str, Any]:
    manifest = _load_manifest(repo_root)
    restore_required: list[str] = []
    replacement_checked = 0
    for entry in manifest["files"]:
        relative = entry["path"]
        classification = entry["classification"]
        replacement = entry["replacement_reference"]
        if classification == "restore_required":
            restore_required.append(relative)
            data = _raw_file(_checked_path(repo_root, relative), relative)
            if _sha256(data) != entry["file_sha256"] or len(data) != entry["size"]:
                raise VerificationError(f"restore-required bytes mismatch: {relative}")
        if replacement is not None:
            _raw_file(_checked_path(repo_root, replacement), replacement)
            replacement_checked += 1
    return _success_payload(
        "canonical",
        manifest,
        restore_required=restore_required,
        replacement_references_checked=replacement_checked,
    )


def _success_payload(
    mode: str,
    manifest: Mapping[str, Any],
    *,
    restore_required: Sequence[str],
    replacement_references_checked: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "mode": mode,
        "status": "PASS",
        "subject": manifest["subject"],
        "bundle_sha256": manifest["bundle_sha256"],
        "file_count": manifest["file_count"],
        "total_bytes": manifest["total_bytes"],
        "classification_counts": _classification_counts(manifest),
        "restore_required": list(restore_required),
    }
    if replacement_references_checked is not None:
        payload["replacement_references_checked"] = replacement_references_checked
    return payload


def _failure_payload(mode: str, message: str) -> dict[str, Any]:
    return {"mode": mode, "status": "FAIL", "errors": [message]}


def _emit(payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode")
    external = subparsers.add_parser("external", help="verify the historical source root")
    external.add_argument("--source-root", help="explicit historical source root")
    canonical = subparsers.add_parser("canonical", help="verify canonical replacement references")
    canonical.add_argument("--repo-root", help="canonical repository root")
    args = parser.parse_args(argv)

    if args.mode not in {"external", "canonical"}:
        _emit(_failure_payload("unknown", "a subcommand is required: external or canonical"))
        return 2
    repo_root = Path(__file__).resolve().parents[1]
    try:
        if args.mode == "external":
            if not args.source_root:
                raise VerificationError("external requires explicit --source-root")
            payload = _verify_external(repo_root, Path(args.source_root))
        else:
            payload = _verify_canonical(Path(args.repo_root) if args.repo_root else repo_root)
    except VerificationError as exc:
        _emit(_failure_payload(args.mode, str(exc)))
        return 1
    _emit(payload)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by targeted tests
    raise SystemExit(main())
