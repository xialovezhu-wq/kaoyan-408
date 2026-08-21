from __future__ import annotations

import hashlib
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests/fixtures/intake_batch_apply_fixture_408.py"
SPEC = importlib.util.spec_from_file_location("intake_batch_apply_fixture_408", FIXTURE_PATH)
assert SPEC and SPEC.loader
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)


class InternalNightlyApply408Tests(unittest.TestCase):
    def test_synthetic_commit_and_identical_replay_are_receipt_bound(self) -> None:
        case = fixture.IntakeBatchApplyTests(methodName="runTest")
        case.setUp()
        try:
            package = case.package("JOB-V2-NIGHTLY-SYNTHETIC")
            manifest = case._stage_manifest(
                [package], batch_id="BATCH-V2-NIGHTLY-SYNTHETIC"
            )
            first = case.apply_manifest(manifest)
            self.assertEqual(first.status, "COMMITTED")
            self.assertEqual(first.formal_ids, ["CO_2026_001"])
            self.assertTrue(first.receipt_path)
            receipt = Path(first.receipt_path)
            self.assertTrue(receipt.is_file())
            receipt_sha = hashlib.sha256(receipt.read_bytes()).hexdigest()
            before = case._repo_bytes()
            second = case.apply_manifest(manifest)
            after = case._repo_bytes()
            self.assertEqual(second.status, "ALREADY_COMMITTED")
            self.assertEqual(before, after)
            self.assertEqual(second.receipt_path, first.receipt_path)
            self.assertEqual(
                hashlib.sha256(Path(second.receipt_path).read_bytes()).hexdigest(),
                receipt_sha,
            )
            self.assertTrue(any("幂等 receipt" in row for row in second.logs))
        finally:
            case.tearDown()

    def test_public_apply_entrypoint_remains_retired(self) -> None:
        text = (ROOT / "scripts/intake_apply_408.py").read_text(encoding="utf-8")
        self.assertIn("已退役", text)
        self.assertNotIn("from intake_apply_engine_408 import", text)


if __name__ == "__main__":
    unittest.main()
