#!/usr/bin/env python3
"""Recompute native 408 due for an explicit date from one verified publication."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(root: Path, value: str) -> Path:
    part = Path(value)
    if part.is_absolute() or not part.parts or ".." in part.parts:
        raise ValueError("unsafe relative path")
    path = root / part
    if any(p.is_symlink() for p in [path, *path.parents]) or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("source path leaves the supplied repository")
    return path


def replay(repo: Path, target_date: str, output_dir: Path | None = None) -> dict:
    repo = repo.resolve(strict=True)
    manifest_ref = "support/d408/manifest.json"
    manifest_path = relative(repo, manifest_ref)
    library = json.loads(relative(repo, "PUBLISHED_LIBRARY.json").read_bytes())
    if library.get("subject") != "408" or library.get("entrypoints", {}).get("due_support") != manifest_ref:
        raise ValueError("same-publication root does not bind the native due support entry")
    inventory = {}
    for shard in library.get("file_inventory", []):
        path = relative(repo, shard["path"])
        if sha(path) != shard["sha256"]:
            raise ValueError("publication inventory shard hash mismatch")
        rows = [json.loads(line) for line in path.read_bytes().splitlines() if line.strip()]
        if len(rows) != shard["count"]:
            raise ValueError("publication inventory shard count mismatch")
        for row in rows:
            if row["path"] in inventory:
                raise ValueError("duplicate publication inventory path")
            inventory[row["path"]] = row
    if inventory.get(manifest_ref, {}).get("sha256") != sha(manifest_path):
        raise ValueError("native due support manifest is not hash-bound to the publication")
    manifest = json.loads(manifest_path.read_bytes())
    if manifest.get("schema") != "cs408-readonly-due-support-v2":
        raise ValueError("unsupported native due support manifest")
    before = {}
    for row in manifest["components"] + manifest["inputs"]:
        ref = row["published_path"]
        path = relative(repo, ref)
        if not path.is_file() or path.stat().st_size != row["bytes"] or sha(path) != row["sha256"]:
            raise ValueError("component/input byte binding mismatch: " + ref)
        if inventory.get(ref, {}).get("sha256") != row["sha256"]:
            raise ValueError("component/input is not bound by the same publication: " + ref)
        before[ref] = row["sha256"]
    for row in manifest["absent_inputs"]:
        if any(relative(repo, ref).exists() or ref in inventory for ref in row["published_paths_to_check"]):
            raise ValueError("native empty admission-ledger basis has changed")
    if output_dir is not None:
        output_dir = output_dir.resolve()
        if output_dir == repo or output_dir.is_relative_to(repo):
            raise ValueError("outputs must be outside the input repository")
    with tempfile.TemporaryDirectory(prefix="d408-readonly-") as temporary:
        scratch = Path(temporary).resolve()
        for row in manifest["components"] + manifest["inputs"]:
            target = relative(scratch, row.get("native_path", row["published_path"]))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(relative(repo, row["published_path"]), target)
        vendor = relative(scratch, manifest["dependency_runtime_path"])
        sys.path.insert(0, str(vendor))
        scripts = scratch / "scripts"
        sys.path.insert(0, str(scripts))
        native_path = relative(scratch, manifest["algorithm"]["canonical_source_ref"])
        spec = importlib.util.spec_from_file_location("d408_published_native", native_path)
        native = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = native
        previous = sys.dont_write_bytecode
        try:
            sys.dont_write_bytecode = True
            spec.loader.exec_module(native)
            if not Path(native.pyfsrs.__file__).resolve().is_relative_to(vendor.resolve()):
                raise ValueError("FSRS was not loaded from the hash-bound vendor files; use a fresh process")
            day = native.parse_date(target_date)
            config, cohort, memory, bindings, _ = native.load_projection(
                scratch, relative(scratch, manifest["config_path"]), day
            )
            if bindings != manifest["native_source_bindings"]:
                raise ValueError("native source bindings differ from the current publication")
            if native.canonical_sha256(config) != manifest["config_canonical_sha256"]:
                raise ValueError("native config differs from the bound default snapshot")
            due = native.build_due_run(cohort, memory, config, day, source_bindings=bindings)
            from concept_review_priority_408 import due_context
            _, point_rows = native._markdown_rows(scratch / "节点总表.md")
            context = due_context(due, {row["ID"]: row for row in point_rows})
        finally:
            sys.dont_write_bytecode = previous
            sys.path.remove(str(vendor))
            sys.path.remove(str(scripts))
        if due["formal_write_count"] != 0 or due["learner_evidence_write_count"] != 0:
            raise ValueError("native scheduler violated its read-only contract")
        if output_dir is not None:
            output_dir.mkdir(parents=True, exist_ok=True)
            for filename, body in {"native-due-run.json": due, "due-context.json": context, "current-cohort.json": cohort,
                                   "full-memory-projection.json": memory}.items():
                (output_dir / filename).write_text(
                    json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
                )
    for ref, digest in before.items():
        if sha(relative(repo, ref)) != digest:
            raise ValueError("input changed during native replay: " + ref)
    return {"schema": "cs408-readonly-replay-result-v2", "subject": "408", "target_date": target_date,
            "due_version": due["run_sha256"], "current_master_sha256": cohort["master_sha256"],
            "cohort_count": cohort["cohort_count"], "included_count": cohort["included_count"],
            "blocked_count": cohort["blocked_count"], "excluded_count": cohort["excluded_count"],
            "due_count": due["due_count"], "formal_write_count": 0, "learner_evidence_write_count": 0,
            "inputs_unchanged": True, "semantic_history_scan_proven": False,
            "representative_selection_performed": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--target-date", required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(replay(args.repo, args.target_date, args.output_dir), ensure_ascii=False, sort_keys=True))
    except (ValueError, OSError, RuntimeError) as exc:
        print("DUE_REPLAY_FAILED: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
