from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/nightly_sol_adapter.py"
SPEC = importlib.util.spec_from_file_location("cs408_nightly_sol_adapter", MODULE_PATH)
assert SPEC and SPEC.loader
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


def batch() -> dict:
    captures = ["CAP-408-001", "CAP-408-002"]
    skill_sha = adapter.sha256_file(adapter.SKILL_PATH)
    return {
        "schema_version": adapter.BATCH_SCHEMA,
        "batch_id": "NIGHTLY-CS408-20260821-001",
        "subject": "cs408",
        "capture_intake_date": "2026-08-21",
        "capture_ids": captures,
        "capture_set_sha256": adapter.sha256_value(captures),
        "analysis_packages": [
            {
                "capture_id": item,
                "package_ref": "study-intake-analysis-package://sha256/" + str(index) * 64,
                "package_sha256": str(index) * 64,
            }
            for index, item in enumerate(captures, start=1)
        ],
        "skill": {
            "name": adapter.SKILL_NAME,
            "source_sha256": skill_sha,
            "declared_version": None,
        },
        "status": "frozen",
        "formal_write_count": 0,
    }


class CS408NightlySolAdapterTests(unittest.TestCase):
    def test_prepare_binds_outer_set_and_real_skill_source(self) -> None:
        invocation = adapter.prepare_invocation(batch())
        self.assertEqual(invocation["selection_policy"], "outer_frozen_set_only")
        self.assertEqual(invocation["capture_ids"], ["CAP-408-001", "CAP-408-002"])
        self.assertEqual(invocation["formal_write_count"], 0)

    def test_skill_hash_or_package_set_drift_fails_closed(self) -> None:
        drifted = batch()
        drifted["skill"]["source_sha256"] = "f" * 64
        with self.assertRaisesRegex(adapter.AdapterError, "skill_binding_invalid"):
            adapter.prepare_invocation(drifted)
        drifted = batch()
        drifted["analysis_packages"].reverse()
        with self.assertRaisesRegex(adapter.AdapterError, "analysis_package_set_invalid"):
            adapter.prepare_invocation(drifted)

    def test_wraps_native_commit_without_interpreting_408_fields(self) -> None:
        native = {
            "schema_version": "cs408-daily-curation-closeout-v1",
            "subject": "cs408",
            "batch_id": batch()["batch_id"],
            "status": "committed",
            "capture_results": [{"capture_id": "CAP-408-001", "opaque": {"x": 1}}],
            "changed_files": ["synthetic/node.md"],
            "formal_write_count": 1,
            "conflict": None,
        }
        result = adapter.wrap_native_receipt(batch(), native)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["native_receipt"], native)

    def test_only_three_hard_conflicts_create_awaiting_user(self) -> None:
        native = {
            "schema_version": "cs408-daily-curation-closeout-v1",
            "subject": "cs408",
            "batch_id": batch()["batch_id"],
            "status": "awaiting_user",
            "capture_results": [],
            "changed_files": [],
            "formal_write_count": 0,
            "conflict": {
                "kind": "ambiguous_formal_target",
                "capture_id": "CAP-408-002",
            },
        }
        result = adapter.wrap_native_receipt(batch(), native)
        self.assertEqual(result["status"], "awaiting_user")
        self.assertTrue(result["conflict_id"].startswith("CONFLICT-"))
        native["conflict"]["kind"] = "ordinary_warning"
        with self.assertRaisesRegex(adapter.AdapterError, "hard_conflict_invalid"):
            adapter.wrap_native_receipt(batch(), native)


if __name__ == "__main__":
    unittest.main()
