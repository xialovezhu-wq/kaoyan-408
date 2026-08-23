from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image

from question_source_attestation_408 import (
    CONFIRMATION,
    SCHEMA,
    SUBMISSION_SCHEMA,
    finalize_attestation,
    prepare_request,
    question_asset_entries,
    sha256_file,
)


def write_test_png(path: Path, *, color: tuple[int, int, int] = (31, 63, 95)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (2, 2), color=color).save(path, format="PNG")


def add_answer_isolation_attestation(
    *,
    details_root: Path,
    formal_node_id: str,
    question_asset: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    assets = question_asset_entries(
        details_root=details_root,
        formal_node_id=formal_node_id,
        asset_paths=[question_asset],
    )
    verifier = {
        "actor_id": "fixture-isolation-reviewer",
        "execution_id": f"fixture-review-{formal_node_id}",
        "role": "answer_isolation_verifier",
    }
    prepared = prepare_request(
        details_root=details_root,
        formal_node_id=formal_node_id,
        question_assets=assets,
        asset_producer_identity={
            "actor_id": "fixture-surface-producer",
            "execution_id": f"fixture-producer-{formal_node_id}",
            "role": "question_surface_producer",
        },
        verifier_identity=verifier,
        verification_method="independent_visual_review",
    )
    request_path = details_root / str(prepared["request_ref"])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    submission_path = request_path.parent / "review-submission.json"
    submission_path.write_text(
        json.dumps(
            {
                "schema": SUBMISSION_SCHEMA,
                "request_id": request["request_id"],
                "request_sha256": sha256_file(request_path),
                "request_nonce": request["request_nonce"],
                "verifier_identity": verifier,
                "question_asset_set_sha256": request["question_asset_set_sha256"],
                "answer_free_verified": True,
                "solution_free_verified": True,
                "reviewed_asset_bytes_not_filename_only": True,
                "confirmation": CONFIRMATION,
                "formal_write_count": 0,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    attestation_path = (
        details_root
        / "assets"
        / formal_node_id
        / "answer-isolation-attestation.json"
    )
    finalized = finalize_attestation(
        details_root=details_root,
        request_ref=prepared["request_ref"],
        submission_ref=submission_path.relative_to(details_root),
        output=attestation_path,
        replace=attestation_path.exists(),
    )
    return {
        **manifest,
        "answer_isolation_verification": {
            "schema": SCHEMA,
            "attestation_ref": attestation_path.relative_to(details_root).as_posix(),
            "attestation_sha256": finalized["attestation_sha256"],
            "question_asset_set_sha256": finalized["question_asset_set_sha256"],
        },
    }
