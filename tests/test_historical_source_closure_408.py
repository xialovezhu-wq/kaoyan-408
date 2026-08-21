"""Targeted tests for the standalone historical CS408 source verifier."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFIER_PATH = ROOT / "scripts" / "verify_historical_source_closure_408.py"
MANIFEST_PATH = ROOT / "schema" / "study-intake-historical-source-closure-v1.json"
# Keep the test portable across the canonical checkout layout.  The explicit
# acceptance command remains the authoritative external-root invocation.
HISTORICAL_ROOT = ROOT.parents[3] / "kaoyan-408"

_SPEC = importlib.util.spec_from_file_location(
    "verify_historical_source_closure_408", VERIFIER_PATH
)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - test setup failure
    raise RuntimeError(f"cannot load {_SPEC} from {VERIFIER_PATH}")
_VERIFIER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_VERIFIER)


class HistoricalSourceClosure408Tests(unittest.TestCase):
    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, str(VERIFIER_PATH), *arguments],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_manifest_is_frozen_and_has_no_machine_paths(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["schema_version"],
            "study-intake-historical-source-closure-v1",
        )
        self.assertEqual(manifest["subject"], "cs408")
        self.assertEqual(manifest["file_count"], 16)
        self.assertEqual(manifest["total_bytes"], 1_028_860)
        self.assertEqual(len(manifest["ordered_paths"]), 16)
        self.assertEqual(len(manifest["files"]), 16)
        self.assertEqual(
            {entry["classification"] for entry in manifest["files"]},
            {"replaced_by_current", "evidence_only"},
        )
        self.assertEqual(
            sum(entry["classification"] == "replaced_by_current" for entry in manifest["files"]),
            15,
        )
        self.assertEqual(
            sum(entry["classification"] == "evidence_only" for entry in manifest["files"]),
            1,
        )
        self.assertNotIn("/Users/", MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("/home/", MANIFEST_PATH.read_text(encoding="utf-8"))

    def test_ordered_raw_bytes_algorithm_reproduces_frozen_bundle(self) -> None:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        rows = [
            (entry["file_sha256"], entry["path"]) for entry in manifest["files"]
        ]
        self.assertEqual(
            _VERIFIER.ordered_bytes_digest(rows),
            manifest["bundle_sha256"],
        )
        self.assertEqual(
            manifest["ordered_raw_bytes_algorithm"]["record"],
            "<file_sha256><two spaces><repo-relative-path><LF>",
        )

    def test_canonical_default_and_explicit_root_pass(self) -> None:
        for arguments in (("canonical",), ("canonical", "--repo-root", str(ROOT))):
            with self.subTest(arguments=arguments):
                result = self._run(*arguments)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "PASS")
                self.assertEqual(payload["mode"], "canonical")
                self.assertEqual(payload["restore_required"], [])
                self.assertEqual(payload["classification_counts"]["replaced_by_current"], 15)
                self.assertEqual(payload["classification_counts"]["evidence_only"], 1)
                self.assertEqual(payload["replacement_references_checked"], 15)

    def test_external_requires_explicit_source_root(self) -> None:
        result = self._run("external")
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "FAIL")
        self.assertIn("--source-root", payload["errors"][0])

    def test_external_recomputes_all_sixteen_fixed_files(self) -> None:
        self.assertTrue(HISTORICAL_ROOT.is_dir())
        result = self._run("external", "--source-root", str(HISTORICAL_ROOT))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "PASS")
        self.assertEqual(payload["mode"], "external")
        self.assertEqual(payload["file_count"], 16)
        self.assertEqual(payload["total_bytes"], 1_028_860)
        self.assertEqual(payload["restore_required"], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
