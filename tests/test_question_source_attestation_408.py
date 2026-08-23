from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from question_source_attestation_408 import (  # noqa: E402
    CONFIRMATION,
    SUBMISSION_SCHEMA,
    QuestionSourceAttestationError,
    finalize_attestation,
    prepare_request,
    question_asset_entries,
    sha256_file,
    validate_attestation_chain,
)
from tests.question_source_attestation_fixture import write_test_png  # noqa: E402


class QuestionSourceAttestation408Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.details = Path(self.temp.name) / "details"
        self.assets = self.details / "assets/DS_2024_001"
        self.assets.mkdir(parents=True)
        self.question = self.assets / "question-01.png"
        write_test_png(self.question)
        self.producer = {
            "actor_id": "surface-producer",
            "execution_id": "producer-execution-001",
            "role": "question_surface_producer",
        }
        self.verifier = {
            "actor_id": "isolation-reviewer",
            "execution_id": "reviewer-execution-002",
            "role": "answer_isolation_verifier",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _prepare(self) -> tuple[dict[str, object], list[dict[str, object]]]:
        assets = question_asset_entries(
            details_root=self.details,
            formal_node_id="DS_2024_001",
            asset_paths=[self.question],
        )
        prepared = prepare_request(
            details_root=self.details,
            formal_node_id="DS_2024_001",
            question_assets=assets,
            asset_producer_identity=self.producer,
            verifier_identity=self.verifier,
            verification_method="independent_visual_review",
        )
        return prepared, assets

    def _submission(self, prepared: dict[str, object], assets: list[dict[str, object]]) -> Path:
        request = self.details / str(prepared["request_ref"])
        request_payload = json.loads(request.read_text(encoding="utf-8"))
        submission = request.parent / "review-submission.json"
        submission.write_text(
            json.dumps(
                {
                    "schema": SUBMISSION_SCHEMA,
                    "request_id": request_payload["request_id"],
                    "request_sha256": sha256_file(request),
                    "request_nonce": request_payload["request_nonce"],
                    "verifier_identity": self.verifier,
                    "question_asset_set_sha256": request_payload[
                        "question_asset_set_sha256"
                    ],
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
        return submission

    def test_prepare_finalize_and_verify_chain(self) -> None:
        prepared, assets = self._prepare()
        submission = self._submission(prepared, assets)
        output = self.assets / "answer-isolation-attestation.json"
        result = finalize_attestation(
            details_root=self.details,
            request_ref=prepared["request_ref"],
            submission_ref=submission.relative_to(self.details),
            output=output,
        )
        self.assertEqual("finalized", result["status"])
        checked = validate_attestation_chain(
            details_root=self.details,
            attestation_path=output,
            formal_node_id="DS_2024_001",
            expected_assets=assets,
        )
        self.assertTrue(checked["answer_isolated"])

    def test_prepare_rejects_same_execution_identity(self) -> None:
        verifier = dict(self.verifier)
        verifier["execution_id"] = self.producer["execution_id"]
        assets = question_asset_entries(
            details_root=self.details,
            formal_node_id="DS_2024_001",
            asset_paths=[self.question],
        )
        with self.assertRaisesRegex(QuestionSourceAttestationError, "distinct"):
            prepare_request(
                details_root=self.details,
                formal_node_id="DS_2024_001",
                question_assets=assets,
                asset_producer_identity=self.producer,
                verifier_identity=verifier,
                verification_method="independent_visual_review",
            )

    def test_filename_alone_cannot_replace_review_submission(self) -> None:
        prepared, _ = self._prepare()
        fake = self.details / str(prepared["request_ref"])
        output = self.assets / "answer-isolation-attestation.json"
        with self.assertRaisesRegex(QuestionSourceAttestationError, "submission"):
            finalize_attestation(
                details_root=self.details,
                request_ref=prepared["request_ref"],
                submission_ref=fake.relative_to(self.details),
                output=output,
            )

    def test_prepare_rejects_undecodable_question_image(self) -> None:
        self.question.write_bytes(b"not an image")
        with self.assertRaisesRegex(QuestionSourceAttestationError, "not decodable"):
            question_asset_entries(
                details_root=self.details,
                formal_node_id="DS_2024_001",
                asset_paths=[self.question],
            )

    def test_finalize_rejects_asset_that_becomes_undecodable(self) -> None:
        prepared, assets = self._prepare()
        submission = self._submission(prepared, assets)
        self.question.write_bytes(b"no longer an image")
        with self.assertRaisesRegex(QuestionSourceAttestationError, "not decodable"):
            finalize_attestation(
                details_root=self.details,
                request_ref=prepared["request_ref"],
                submission_ref=submission.relative_to(self.details),
                output=self.assets / "answer-isolation-attestation.json",
            )


if __name__ == "__main__":
    unittest.main()
