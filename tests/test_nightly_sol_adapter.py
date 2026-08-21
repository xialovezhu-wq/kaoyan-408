from __future__ import annotations

import importlib.util
import hashlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/nightly_sol_adapter.py"
SPEC = importlib.util.spec_from_file_location("cs408_nightly_sol_adapter", MODULE_PATH)
assert SPEC and SPEC.loader
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


def batch() -> dict:
    captures = ["CAP-408-001", "CAP-408-002", "CAP-408-003"]
    skill_sha = adapter.sha256_file(adapter.SKILL_PATH)
    command = "开始 2026-08-21 408正式入库"
    authorization_core = {
        "schema_version": adapter.AUTHORIZATION_SCHEMA,
        "subject": "cs408",
        "capture_intake_date": "2026-08-21",
        "normalized_command": command,
        "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
    }
    return {
        "schema_version": adapter.BATCH_SCHEMA,
        "batch_id": "NIGHTLY-" + "B" * 28,
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
        "authorization": {
            **authorization_core,
            "authorization_id": "NAUTH-"
            + adapter.sha256_value(authorization_core)[:24].upper(),
        },
        "status": "frozen",
        "formal_write_count": 0,
    }


def execution_evidence(exit_code: int = 0) -> dict:
    return {
        "pre_state_sha256": "a" * 64,
        "post_state_sha256": "b" * 64,
        "operations": [],
        "adapter_run_id": "RUN-CS408-001",
        "pid": 1234,
        "transaction_id": "TX-CS408-001",
        "ended_at": "2026-08-21T20:00:01+08:00",
        "stopped_at": "2026-08-21T20:00:01+08:00",
        "exit_code": exit_code,
    }


def terminal(
    results: list[dict],
    *,
    status: str = "complete",
    conflict: dict | None = None,
    exit_code: int = 0,
) -> dict:
    return {
        "schema_version": adapter.NATIVE_TERMINAL_SCHEMA,
        "subject": "cs408",
        "batch_id": batch()["batch_id"],
        "status": status,
        "capture_results": results,
        "changed_files": ["synthetic/receipt.json"],
        "formal_write_count": sum(
            row.get("outcome") == "curated" for row in results
        ),
        "conflict": conflict,
        "global_audit_receipt_sha256": (
            "c" * 64 if status in {"complete", "noop"} else None
        ),
        "close_batch_receipt_sha256": (
            "d" * 64
            if status in {"complete", "noop", "partial", "awaiting_user"}
            else None
        ),
        "execution_evidence": execution_evidence(exit_code),
    }


def curated(capture_id: str, formal_id: str = "CO_2026_001") -> dict:
    return {
        "capture_id": capture_id,
        "outcome": "curated",
        "formal_id": formal_id,
        "normal_receipt_sha256": "1" * 64,
        "terminal_binding_sha256": "2" * 64,
    }


def already_current(capture_id: str, formal_id: str = "CO_2026_002") -> dict:
    return {
        "capture_id": capture_id,
        "outcome": "already_current",
        "formal_id": formal_id,
        "verification_sha256": "3" * 64,
        "terminal_binding_sha256": "4" * 64,
    }


class CS408NightlySolAdapterTests(unittest.TestCase):
    def test_prepare_binds_outer_set_and_real_skill_source(self) -> None:
        invocation = adapter.prepare_invocation(batch())
        self.assertEqual(invocation["selection_policy"], "outer_frozen_set_only")
        self.assertEqual(
            invocation["capture_ids"],
            ["CAP-408-001", "CAP-408-002", "CAP-408-003"],
        )
        self.assertEqual(invocation["formal_write_count"], 0)
        with self.assertRaises(TypeError):
            invocation["capture_ids"].append("CAP-408-003")
        with self.assertRaises(TypeError):
            invocation["skill"]["source_sha256"] = "f" * 64

    def test_skill_hash_or_package_set_drift_fails_closed(self) -> None:
        drifted = batch()
        drifted["skill"]["source_sha256"] = "f" * 64
        with self.assertRaisesRegex(adapter.AdapterError, "skill_binding_invalid"):
            adapter.prepare_invocation(drifted)
        drifted = batch()
        drifted["analysis_packages"].reverse()
        with self.assertRaisesRegex(adapter.AdapterError, "analysis_package_set_invalid"):
            adapter.prepare_invocation(drifted)

    def test_exact_authorization_is_required(self) -> None:
        drifted = batch()
        drifted["authorization"]["normalized_command"] = (
            "开始 2026-08-21 408正式入库，请执行"
        )
        with self.assertRaisesRegex(
            adapter.AdapterError, "nightly_authorization_command_invalid"
        ):
            adapter.prepare_invocation(drifted)
        drifted = batch()
        drifted["authorization"]["command_sha256"] = "f" * 64
        with self.assertRaisesRegex(
            adapter.AdapterError, "nightly_authorization_command_sha256_invalid"
        ):
            adapter.prepare_invocation(drifted)

    def test_legacy_wrap_cannot_bypass_strict_terminal_contract(self) -> None:
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
        with self.assertRaisesRegex(
            adapter.AdapterError, "native_terminal_schema_required"
        ):
            adapter.wrap_native_receipt(batch(), native)

    def test_execute_calls_native_once_with_immutable_full_invocation(self) -> None:
        calls: list[dict] = []

        def executor(invocation):
            calls.append(invocation)
            with self.assertRaises(TypeError):
                invocation["capture_ids"] = ["drift"]
            with self.assertRaises(TypeError):
                invocation["capture_ids"].append("drift")
            return terminal([
                curated("CAP-408-001"),
                already_current("CAP-408-002"),
                curated("CAP-408-003", "CO_2026_003"),
            ])

        result = adapter.CS408NightlySolAdapter().execute(
            batch(), native_executor=executor
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            calls[0]["capture_ids"],
            ["CAP-408-001", "CAP-408-002", "CAP-408-003"],
        )
        self.assertEqual(result["schema_version"], adapter.RESULT_SCHEMA)
        self.assertEqual(result["status"], "complete")

    def test_terminal_receipt_requires_coverage_and_execution_evidence(self) -> None:
        invalid = terminal([curated("CAP-408-001"), already_current("CAP-408-002")])
        with self.assertRaisesRegex(
            adapter.AdapterError, "terminal_capture_result_coverage_invalid"
        ):
            adapter.wrap_native_receipt(batch(), invalid)
        invalid = terminal([
            curated("CAP-408-001"),
            already_current("CAP-408-002"),
            already_current("CAP-408-002"),
        ])
        invalid["execution_evidence"].pop("stopped_at")
        with self.assertRaisesRegex(
            adapter.AdapterError, "execution_evidence_shape_invalid"
        ):
            adapter.wrap_native_receipt(batch(), invalid)
        invalid = terminal([
            curated("CAP-408-001"),
            already_current("CAP-408-002"),
            curated("CAP-408-003", "CO_2026_003"),
        ], exit_code=1)
        with self.assertRaisesRegex(
            adapter.AdapterError, "execution_success_exit_code_invalid"
        ):
            adapter.wrap_native_receipt(batch(), invalid)

    def test_awaiting_user_separates_safe_results_and_conflicted_capture(self) -> None:
        conflict = {
            "kind": "ambiguous_formal_target",
            "capture_id": "CAP-408-002",
        }
        native = terminal(
            [
                curated("CAP-408-001"),
                curated("CAP-408-003", "CO_2026_003"),
            ],
            status="awaiting_user",
            conflict=conflict,
        )
        result = adapter.wrap_native_receipt(batch(), native)
        self.assertEqual(result["status"], "awaiting_user")
        self.assertTrue(result["conflict_id"].startswith("CONFLICT-"))
        native["capture_results"] = [
            curated("CAP-408-001"),
            curated("CAP-408-003", "CO_2026_003"),
        ]
        result = adapter.wrap_native_receipt(batch(), native)
        self.assertEqual(result["status"], "awaiting_user")
        conflict["kind"] = "ordinary_warning"
        with self.assertRaisesRegex(adapter.AdapterError, "hard_conflict_invalid"):
            adapter.wrap_native_receipt(batch(), native)

    def test_resolution_restricts_resume_to_one_conflicted_capture(self) -> None:
        skill_sha = batch()["skill"]["source_sha256"]
        resolution = {
            "conflict_id": "CONFLICT-" + "A" * 24,
            "batch_id": batch()["batch_id"],
            "subject": "cs408",
            "conflicted_capture_id": "CAP-408-002",
            "user_option": "use-existing-formal-target",
            "skill_name": adapter.SKILL_NAME,
            "skill_source_sha256": skill_sha,
        }
        calls: list[dict] = []

        def executor(invocation):
            calls.append(invocation)
            return terminal([already_current("CAP-408-002")])

        result = adapter.CS408NightlySolAdapter().execute(
            batch(), native_executor=executor, resolution=resolution
        )
        self.assertEqual(result["status"], "complete")
        self.assertEqual(calls[0]["capture_ids"], ["CAP-408-002"])
        self.assertEqual(calls[0]["selection_policy"], "conflicted_capture_only")
        with self.assertRaisesRegex(
            adapter.AdapterError, "nightly_resolution_capture_scope_invalid"
        ):
            adapter.CS408NightlySolAdapter().execute(
                batch(),
                native_executor=executor,
                capture_ids=["CAP-408-001"],
                resolution=resolution,
            )

    def test_legacy_hard_conflict_cannot_bypass_strict_terminal_contract(self) -> None:
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
        with self.assertRaisesRegex(
            adapter.AdapterError, "native_terminal_schema_required"
        ):
            adapter.wrap_native_receipt(batch(), native)


if __name__ == "__main__":
    unittest.main()
