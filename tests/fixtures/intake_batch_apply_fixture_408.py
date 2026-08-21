from __future__ import annotations

import copy
import contextlib
import datetime as dt
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import intake_batch_apply_408 as batch_engine  # noqa: E402
from intake_batch_apply_408 import (  # noqa: E402
    BatchError,
    BatchValidationError,
    ProjectedRepo,
    SimulatedCrash,
    TransactionRolledBack,
    _commit_transaction,
    apply_batch,
    apply_batch_file,
    load_batch_input,
    recover_transactions,
)
from intake_preflight_408 import run_preflight, suggest_id  # noqa: E402
from intake_apply_engine_408 import (  # noqa: E402
    Txn,
    apply_new as apply_new_current,
    ensure_review_unit_v2_for_new,
)
try:  # Optional legacy-only fixtures are not part of the V2 internal writer lane.
    from intake_postcommit_repair_408 import repair_postcommit_batch  # type: ignore
except ImportError:
    repair_postcommit_batch = None
try:
    from intake_queue_408 import QueueStore  # type: ignore
except ImportError:
    QueueStore = None
from intake_lib_408 import (  # noqa: E402
    DATE_INDEX_COLS,
    EDGE_COLS,
    MASTER_COLS,
    NODE_LIB_COLS,
    ORIGINAL_TRACK_COLS,
    ORIGINAL_TRACK_FILE,
    REDO_COLS,
    REDO_FILE,
    REVIEW_UNIT_CARD_DIR,
    REVIEW_UNIT_COLS,
    REVIEW_UNIT_FILE,
    REVIEW_UNIT_MAPPING_COLS,
    REVIEW_UNIT_MAPPING_FILE,
    Repo,
    HIT_INDEX_FILE,
    iter_table_rows,
    read_text,
    scan_leak,
)
try:
    from audit_review_schedule import audit as audit_review_schedule  # type: ignore
except ImportError:
    audit_review_schedule = None


def table(headers: list[str]) -> str:
    return "|" + "|".join(headers) + "|\n" + "|" + "|".join("---" for _ in headers) + "|\n"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_sha(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


class IntakeBatchApplyTests(unittest.TestCase):
    generation = 7

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo = root / "repo"
        self.details = root / "details"
        self.runtime = root / "runtime"
        self.repo.mkdir()
        self.details.mkdir()
        for rel in ("jobs", "approved", "manifests", "receipts"):
            (self.runtime / rel).mkdir(parents=True, exist_ok=True)
        self._write_json(self.runtime / "coordinator.json", {
            "schema_version": "coordinator_v1",
            "threadId": "thread-main",
            "hostId": "test-host",
            "generation": self.generation,
            "active": True,
        })
        self._make_repo()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _make_repo(self) -> None:
        files = {
            "节点总表.md": "# 节点总表\n\n" + table(MASTER_COLS),
            "关系边表.md": "# 关系边表\n\n" + table(EDGE_COLS),
            "年份索引.md": "# 年份索引\n\n",
            "知识点命中索引.md": "# 知识点命中索引\n\n",
            "错题日期索引.md": "# 错题日期索引\n\n" + table(DATE_INDEX_COLS),
            "错题复做记录.md": "# 错题复做记录\n\n" + table(REDO_COLS),
            "复习单元总表.md": "# 复习单元总表\n\n" + table(REVIEW_UNIT_COLS),
            REVIEW_UNIT_MAPPING_FILE: "# 复习单元节点映射\n\n" + table(REVIEW_UNIT_MAPPING_COLS),
            ORIGINAL_TRACK_FILE: "# 原题复做轨总表\n\n" + table(ORIGINAL_TRACK_COLS),
            "知识点标签表.md": (
                "# 知识点标签表\n\n## CO03 存储器层次结构\n"
                "- CO03-01 Cache 基本机制\n- CO03-02 DRAM 基本机制\n"
            ),
            "错因标签表.md": (
                "# 错因标签表\n\n## E01 概念边界混淆\n"
                "## E02 条件遗漏\n## E03 计算路径错误\n"
            ),
            "关系规则.md": (
                "# 关系规则\n\n## R01 同一核心考点\n## R06 上下游知识链\n"
                "## R07 跨科目类比\n## R08 反例关系\n## R09 高频考点聚类\n"
            ),
        }
        for rel, content in files.items():
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        (self.repo / REVIEW_UNIT_CARD_DIR).mkdir()
        for subject, name in {
            "DS": "数据结构节点.md", "CO": "计算机组成原理节点.md",
            "OS": "操作系统节点.md", "CN": "计算机网络节点.md",
        }.items():
            path = self.repo / "节点库" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {subject}\n\n" + table(NODE_LIB_COLS), encoding="utf-8")
        (self.repo / "专题链").mkdir()

    def _inbox_file(self, job_id: str, name: str, content: bytes) -> dict[str, object]:
        path = self.details / "inbox" / job_id / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return {"path": str(path), "sha256": hashlib.sha256(content).hexdigest(), "protected": True}

    def package(
        self,
        job_id: str,
        *,
        source_id: str = "VISUAL_PENDING",
        kp: str = "CO03-01 Cache 基本机制",
        mode: str = "new",
        formal_id: str | None = None,
    ) -> dict[str, object]:
        reviewer = f"reviewer-{job_id}"
        card = self._inbox_file(
            job_id, "detail-source.md",
            f"# {{{{FORMAL_ID}}}}\n\nFull visual detail for {job_id} stays in details vault.\n".encode(),
        )
        asset = self._inbox_file(job_id, "question.bin", f"question-image-{job_id}".encode())
        package: dict[str, object] = {
            "contract": "coordinator_approved_intake_v1",
            "schema_version": "coordinator_approved_intake_v1",
            "job_id": job_id,
            "idempotency_key": f"sha256:{digest('submission-' + job_id)}",
            "reviewed_at": "2026-07-10T12:00:00Z",
            "reviewed_by": reviewer,
            "decision": "pass",
            "mode": mode,
            "auto_id": mode == "new",
            "formal_id": None if mode == "new" else formal_id,
            "source_id": source_id,
            "year": "2026",
            "subject": "CO",
            "module": "CO03 存储器层次结构",
            "main_knowledge": kp,
            "sub_knowledge": [],
            "hit_knowledge": [kp],
            "question_type": "单项选择题",
            "core_point": "区分缓存访问过程中的地址字段语义",
            "safe_summary": "给定缓存访问场景，要求判断地址字段的作用范围",
            "key_parameters": "仅保留容量与地址位数等复做所需口径",
            "ask_type": "判断地址字段对应的缓存访问语义",
            "user_error_entry": "2026-07-10 入库：混淆了两个地址字段的适用层级",
            "redo_first_action": "先写出每个地址字段服务的层级，再选择计算口径",
            "fuzzy_concepts": "块内偏移 vs 组索引",
            "error_tags": ["E01 概念边界混淆"],
            "detail_entry": "可视化详情入口待补充",
            "attachment_registry": {
                "题图": "已接收", "解析图或解析正文": "已接收", "作答痕迹": "未提供",
                "可视化详情入口": "待生成", "当前附件来源": "本 job inbox",
            },
            "first_done_date": "2026-07-10",
            "latest_review_date": "2026-07-10",
            "latest_error_record": "2026-07-10 入库：地址字段层级判断错误，需要先定口径",
            "relation_candidates": [],
            "relation_review": {
                "status": "reviewed_no_reliable_edge",
                "basis": "已独立检索同知识点与同错因，未发现足够可靠的正式关系边",
                "reviewed_by": reviewer,
            },
            "topic_chain_candidates": [],
            "evidence": [copy.deepcopy(card), copy.deepcopy(asset)],
            "detail_card_source": copy.deepcopy(card),
            "asset_promotions": [copy.deepcopy(asset) | {"relative_path": "question.bin"}],
            "coordinator_checks": {
                "evidence_hashes_verified": True,
                "answer_leak_check": "pass",
                "knowledge_tags_verified": True,
                "error_tags_verified": True,
                "duplicate_check": "pass",
                "historical_mapping_check": "pass_or_not_applicable",
            },
        }
        if mode == "redo":
            package["auto_id"] = False
            package["latest_review_date"] = "2026-07-11"
            package["latest_error_record"] = "2026-07-11 复做再次错误：仍未先区分地址字段层级"
            package["error_tags"] = ["E02 条件遗漏"]
        return package

    def _source_submission(self, package: dict[str, object]) -> dict[str, object]:
        job_id = str(package["job_id"])
        return {
            "schema_version": "question_submission_v1",
            "contract": "question_submission_v1",
            "job_id": job_id,
            "idempotency_key": package["idempotency_key"],
            "mode_hint": package["mode"],
            "subject_hint": "CO",
            "year_hint": "2026",
            "existing_formal_id": package.get("formal_id") if package["mode"] == "redo" else None,
            "evidence": copy.deepcopy(package["evidence"]),
            "safe_context": {
                "question_skeleton": package["safe_summary"],
                "key_parameters": package["key_parameters"],
                "ask_type": package["ask_type"],
                "user_error_entry": package["user_error_entry"],
                "correct_mechanism": "先确定地址字段服务层级，再按层级解释其语义",
                "redo_first_action": package["redo_first_action"],
            },
            "unreviewed_candidates": {
                "knowledge_points": [], "error_tags": [], "relations": [],
            },
            "relation_review": {"status": "unreviewed", "note": "等待 coordinator 独立复核"},
            "missing_fields": [],
            "redaction": {
                "status": "pass", "protected_content_removed_from_queue": True,
                "full_stem_in_inbox_only": True, "answers_in_inbox_only": True,
            },
            "worker_self_check": {
                "passed": True, "formal_id_assigned": False, "formal_write_attempted": False,
                "apply_invoked": False, "evidence_hashes_verified": True,
            },
        }

    def _stage_manifest(
        self,
        packages: list[dict[str, object]],
        *,
        batch_id: str,
        batch_key: str | None = None,
        actor: str = "coordinator-g7-writer",
    ) -> Path:
        claims: list[dict[str, object]] = []
        package_paths: list[str] = []
        for package in packages:
            job_id = str(package["job_id"])
            claim_token = digest("claim-" + job_id)
            claim_event_id = digest("claim-event-" + job_id)[:32]
            claimed_at = "2026-07-10T12:00:00Z"
            submission = self._source_submission(package)
            digest_payload = dict(submission)
            digest_payload.pop("job_id")
            submission_sha = canonical_sha(digest_payload)
            package["source_job_version"] = 2
            package["source_submission_sha256"] = submission_sha
            approved_path = self.runtime / "approved" / f"{job_id}.json"
            self._write_json(approved_path, package)
            approved_sha = hashlib.sha256(approved_path.read_bytes()).hexdigest()
            job = dict(submission)
            job.update({
                "submitted_at": "2026-07-10T11:55:00Z",
                "created_at": "2026-07-10T11:55:00Z",
                "updated_at": "2026-07-10T12:05:00Z",
                "state_updated_at": "2026-07-10T12:05:00Z",
                "state": "COMMITTING",
                "state_version": 4,
                "submission_sha256": submission_sha,
                "approved_package_path": str(approved_path),
                "approved_package_sha256": approved_sha,
                "commit_batch_id": batch_id,
                "commit_lease": {
                    "batch_id": batch_id, "actor": actor,
                    "coordinator_generation": self.generation,
                    "acquired_at": "2026-07-10T12:05:00Z",
                },
                "review_fence": {
                    "worker_id": package["reviewed_by"],
                    "claim_token_sha256": hashlib.sha256(claim_token.encode("utf-8")).hexdigest(),
                    "coordinator_generation": self.generation,
                    "claim_event_id": claim_event_id,
                    "claimed_at": claimed_at,
                },
            })
            self._write_json(self.runtime / "jobs" / f"{job_id}.json", job)
            claim = {
                "job_id": job_id,
                "worker_id": package["reviewed_by"],
                "claim_token": claim_token,
                "coordinator_generation": self.generation,
                "claim_event_id": claim_event_id,
                "claimed_at": claimed_at,
            }
            claims.append(claim)
            package_paths.append(str(approved_path))
            events = [
                {
                    "event_id": claim_event_id, "timestamp": claimed_at,
                    "event_type": "claimed", "job_id": job_id,
                    "actor": package["reviewed_by"], "from_state": "READY", "to_state": "REVIEWING",
                },
                {
                    "event_id": digest("reviewed-event-" + job_id)[:32],
                    "timestamp": "2026-07-10T12:03:00Z",
                    "event_type": "transitioned", "job_id": job_id,
                    "actor": package["reviewed_by"], "from_state": "REVIEWING", "to_state": "REVIEWED",
                    "details": {"metadata": {
                        "approved_package_path": str(approved_path),
                        "approved_package_sha256": approved_sha,
                    }},
                },
                {
                    "event_id": digest("committing-event-" + job_id)[:32],
                    "timestamp": "2026-07-10T12:05:00Z",
                    "event_type": "transitioned", "job_id": job_id,
                    "actor": actor, "from_state": "REVIEWED", "to_state": "COMMITTING",
                    "details": {"metadata": {"commit_batch_id": batch_id}},
                },
            ]
            with (self.runtime / "events.jsonl").open("a", encoding="utf-8") as handle:
                for event in events:
                    handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        key = batch_key or f"sha256:{digest(batch_id)}"
        manifest = {
            "contract": "coordinator_batch_manifest_v1",
            "schema_version": "coordinator_batch_manifest_v1",
            "batch_id": batch_id,
            "idempotency_key": key,
            "actor": actor,
            "coordinator_generation": self.generation,
            "created_at": "2026-07-10T12:05:00Z",
            "queue_claims": claims,
            "packages": package_paths,
        }
        path = self.runtime / "manifests" / f"{batch_id}.json"
        self._write_json(path, manifest)
        return path

    def apply_manifest(self, path: Path, **kwargs):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        kwargs.setdefault("actor", manifest["actor"])
        kwargs.setdefault("coordinator_generation", manifest["coordinator_generation"])
        return apply_batch_file(
            path,
            repo_root=self.repo,
            details_root=self.details,
            runtime_root=self.runtime,
            refresh_bridge=False,
            **kwargs,
        )

    def apply_approved(self, packages: list[dict[str, object]], batch_id: str, **kwargs):
        return self.apply_manifest(self._stage_manifest(packages, batch_id=batch_id), **kwargs)

    def rows(self, rel: str, columns: list[str]) -> list[list[str]]:
        return [
            cells
            for _, cells in iter_table_rows(
                read_text(self.repo / rel).splitlines(), columns[0]
            )
        ]

    def _duplicate_only_table_row(self, rel: str, columns: list[str]) -> None:
        path = self.repo / rel
        lines = path.read_text(encoding="utf-8").splitlines()
        rows = list(iter_table_rows(lines, columns[0]))
        self.assertEqual(len(rows), 1)
        lines.append(lines[rows[0][0]])
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _remove_only_table_row(self, rel: str, columns: list[str]) -> None:
        path = self.repo / rel
        lines = path.read_text(encoding="utf-8").splitlines()
        rows = list(iter_table_rows(lines, columns[0]))
        self.assertEqual(len(rows), 1)
        del lines[rows[0][0]]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _repo_bytes(self) -> dict[str, bytes]:
        return {
            str(path.relative_to(self.repo)): path.read_bytes()
            for path in self.repo.rglob("*")
            if path.is_file() and path.name != ".codex-intake-408.lock"
        }

    def _wal_bytes(self) -> dict[str, bytes]:
        root = self.runtime / "transactions"
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        } if root.exists() else {}

    def _legacy_apply_new_missing_secondary_and_ru_v2(
        self, txn: Txn, pkg: dict[str, object], args: object
    ) -> None:
        """模拟已由旧引擎正常 receipt 提交的派生面缺陷。"""
        apply_new_current(txn, pkg, args)
        fid = str(pkg["formal_id"])

        # 旧缺陷：节点总表已登记副命中，但命中索引漏掉整个副命中小节。
        hit_lines = txn.files[HIT_INDEX_FILE]
        start = next(
            index for index, line in enumerate(hit_lines)
            if line.startswith("### CO03-02 ")
        )
        end = next(
            (
                index for index in range(start + 1, len(hit_lines))
                if hit_lines[index].startswith("### ")
            ),
            len(hit_lines),
        )
        del hit_lines[start:end]

        # 旧缺陷：RU 总表仍是 15 列，且 mapping/track/card 尚未生成。
        review_lines = txn.files["复习单元总表.md"]
        unit_id = f"RU_{fid}"
        review_matches = [
            index for index, cells in iter_table_rows(review_lines, REVIEW_UNIT_COLS[0])
            if cells and cells[0] == unit_id
        ]
        self.assertEqual(len(review_matches), 1)
        legacy_cells = [
            unit_id,
            "计算机组成原理",
            str(pkg["module"]),
            str(pkg["core_point"]),
            "错题",
            str(pkg["first_done_date"]),
            str(pkg["latest_review_date"]),
            str(pkg["latest_review_date"]),
            "1",
            "待评分",
            "1",
            "4",
            "B中等",
            "旧复习方式",
            "旧备注",
        ]
        review_lines[review_matches[0]] = "| " + " | ".join(legacy_cells) + " |"
        for rel, columns in (
            (REVIEW_UNIT_MAPPING_FILE, REVIEW_UNIT_MAPPING_COLS),
            (ORIGINAL_TRACK_FILE, ORIGINAL_TRACK_COLS),
        ):
            lines = txn.files[rel]
            matches = [
                index for index, cells in iter_table_rows(lines, columns[0])
                if cells and cells[0] == fid
            ]
            for index in reversed(matches):
                del lines[index]
        txn.created.pop(f"{REVIEW_UNIT_CARD_DIR}/{unit_id}.md")

    def _create_legacy_validating_job(
        self, suffix: str
    ) -> tuple[dict[str, object], str, QueueStore, object]:
        job_id = f"JOB-POSTCOMMIT-{suffix}"
        package = self.package(job_id)
        package["sub_knowledge"] = ["CO03-02 DRAM 基本机制"]
        package["hit_knowledge"] = [
            "CO03-01 Cache 基本机制",
            "CO03-02 DRAM 基本机制",
        ]
        batch_id = f"BATCH-POSTCOMMIT-{suffix}"
        with patch.object(
            batch_engine,
            "apply_new",
            side_effect=self._legacy_apply_new_missing_secondary_and_ru_v2,
        ):
            committed_result = self.apply_approved([package], batch_id)
        fid = committed_result.formal_ids[0]
        store = QueueStore(
            self.runtime,
            self.details,
            formal_repo_root=self.repo,
            lock_timeout=2,
        )
        committing = store.status(job_id)
        lease = committing["commit_lease"]
        receipt_path = Path(str(committed_result.receipt_path))
        committed = store.transition(
            job_id,
            "COMMITTED",
            actor=lease["actor"],
            expected_state="COMMITTING",
            expected_generation=self.generation,
            metadata={
                "batch_receipt_path": str(receipt_path),
                "batch_receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                "formal_id": fid,
            },
        )
        validating = store.transition(
            job_id,
            "VALIDATING",
            actor="coordinator-g7-validator",
            expected_state="COMMITTED",
            expected_generation=self.generation,
        )
        self.assertEqual(validating["state"], "VALIDATING")
        return package, fid, store, committed_result

    def legacy_package(self, formal_id: str = "CO_2026_001") -> dict[str, object]:
        package = self.package("JOB-LEGACY")
        for key in (
            "contract", "schema_version", "job_id", "idempotency_key", "reviewed_at", "reviewed_by",
            "decision", "auto_id", "evidence", "detail_card_source", "asset_promotions",
            "coordinator_checks", "relation_review",
        ):
            package.pop(key, None)
        package["formal_id"] = formal_id
        return package

    def test_auto_ids_details_projection_and_false_warnings_are_clean(self) -> None:
        result = self.apply_approved([self.package("JOB-AAA"), self.package("JOB-BBB")], "BATCH-TWO")
        self.assertEqual(result.status, "COMMITTED")
        self.assertEqual(result.formal_ids, ["CO_2026_001", "CO_2026_002"])
        self.assertEqual(result.warnings, [])
        self.assertEqual(set(Repo(self.repo).master()), {"CO_2026_001", "CO_2026_002"})
        self.assertIn("# CO_2026_001", read_text(self.details / "cards" / "CO_2026_001.md"))
        self.assertTrue((self.details / "assets" / "CO_2026_002" / "question.bin").is_file())
        self.assertIn("- 关系复核：reviewed_no_reliable_edge", read_text(self.repo / "CO_2026_001.md"))

    def test_review_unit_v2_new_surfaces_are_complete_safe_and_auditable(self) -> None:
        today = dt.date.today()
        package = self.package("JOB-RU-V2-NEW")
        package["first_done_date"] = today.isoformat()
        package["latest_review_date"] = today.isoformat()
        package["latest_error_record"] = f"{today.isoformat()} 入库：未先区分地址字段层级"
        result = self.apply_approved([package], "BATCH-RU-V2-NEW")
        fid = result.formal_ids[0]
        unit_id = f"RU_{fid}"

        review_rows = self.rows("复习单元总表.md", REVIEW_UNIT_COLS)
        self.assertEqual(len(review_rows), 1)
        self.assertEqual(len(review_rows[0]), 18)
        review = dict(zip(REVIEW_UNIT_COLS, review_rows[0]))
        self.assertEqual(review["复习单元ID"], unit_id)
        self.assertEqual(review["调度状态"], "uncalibrated_baseline_due")
        self.assertEqual(review["覆盖节点"], fid)
        self.assertEqual(review["上次复习日期"], "未记录")
        self.assertEqual(review["下次复习日期"], "未记录")
        self.assertEqual(review["当前间隔天数"], "0")
        self.assertEqual(review["掌握度"], "待评分")
        self.assertEqual(review["重要程度"], "未记录")
        self.assertEqual(review["难度等级"], "待确认")

        mapping_rows = self.rows(REVIEW_UNIT_MAPPING_FILE, REVIEW_UNIT_MAPPING_COLS)
        self.assertEqual(len(mapping_rows), 1)
        self.assertEqual(len(mapping_rows[0]), len(REVIEW_UNIT_MAPPING_COLS))
        mapping = dict(zip(REVIEW_UNIT_MAPPING_COLS, mapping_rows[0]))
        self.assertEqual(mapping["正式节点ID"], fid)
        self.assertEqual(mapping["主复习单元ID"], unit_id)
        self.assertEqual(mapping["复核状态"], "reviewed")

        track_rows = self.rows(ORIGINAL_TRACK_FILE, ORIGINAL_TRACK_COLS)
        self.assertEqual(len(track_rows), 1)
        self.assertEqual(len(track_rows[0]), len(ORIGINAL_TRACK_COLS))
        track = dict(zip(ORIGINAL_TRACK_COLS, track_rows[0]))
        self.assertEqual(track["正式节点ID"], fid)
        self.assertEqual(track["主复习单元ID"], unit_id)
        self.assertEqual(track["原题轨状态"], "awaiting_primary_baseline")
        self.assertEqual(track["7天资格"], "protected_until")
        self.assertEqual(track["最近复做日期"], today.isoformat())
        self.assertEqual(
            track["最早允许日期"], (today + dt.timedelta(days=7)).isoformat()
        )
        self.assertEqual(track["下次原题复做日期"], "未记录")

        card = read_text(self.repo / REVIEW_UNIT_CARD_DIR / f"{unit_id}.md")
        self.assertIn("schema: review_unit_card_v2", card)
        self.assertIn("## 闭卷提取", card)
        self.assertEqual(scan_leak(card), ([], []))
        report = audit_review_schedule(self.repo, today)
        self.assertEqual(report["hard_errors"], 0, report)

    def test_review_unit_v2_keeps_only_explicit_valid_importance_and_difficulty(self) -> None:
        package = self.package("JOB-RU-V2-EXPLICIT")
        package["review_unit"] = {
            "内容名称": "缓存地址字段机制",
            "重要程度": "5",
            "难度等级": "A基础",
        }
        self.apply_approved([package], "BATCH-RU-V2-EXPLICIT")
        review = dict(zip(REVIEW_UNIT_COLS, self.rows("复习单元总表.md", REVIEW_UNIT_COLS)[0]))
        self.assertEqual(review["重要程度"], "5")
        self.assertEqual(review["难度等级"], "A基础")

    def test_review_unit_v2_redo_uses_mapping_not_ru_fid_and_is_idempotent(self) -> None:
        today = dt.date.today()
        package = self.package("JOB-RU-V2-BASE")
        package["first_done_date"] = today.isoformat()
        package["latest_review_date"] = today.isoformat()
        fid = self.apply_approved([package], "BATCH-RU-V2-BASE").formal_ids[0]
        original_unit_id = f"RU_{fid}"
        shared_unit_id = "RU_CO03_SHARED_ADDRESS_SCOPE"

        for rel, columns, key_column in (
            ("复习单元总表.md", REVIEW_UNIT_COLS, "复习单元ID"),
            (REVIEW_UNIT_MAPPING_FILE, REVIEW_UNIT_MAPPING_COLS, "正式节点ID"),
            (ORIGINAL_TRACK_FILE, ORIGINAL_TRACK_COLS, "正式节点ID"),
        ):
            path = self.repo / rel
            lines = path.read_text(encoding="utf-8").splitlines()
            for index, cells in iter_table_rows(lines, columns[0]):
                if cells[columns.index(key_column)] != (original_unit_id if key_column == "复习单元ID" else fid):
                    continue
                if rel == "复习单元总表.md":
                    cells[columns.index("复习单元ID")] = shared_unit_id
                else:
                    cells[columns.index("主复习单元ID")] = shared_unit_id
                lines[index] = "| " + " | ".join(cells) + " |"
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        old_card = self.repo / REVIEW_UNIT_CARD_DIR / f"{original_unit_id}.md"
        new_card = self.repo / REVIEW_UNIT_CARD_DIR / f"{shared_unit_id}.md"
        old_card.rename(new_card)
        new_card.write_text(
            new_card.read_text(encoding="utf-8").replace(original_unit_id, shared_unit_id),
            encoding="utf-8",
        )

        redo = self.package("JOB-RU-V2-REDO", mode="redo", formal_id=fid)
        redo["latest_review_date"] = today.isoformat()
        redo["latest_error_record"] = f"{today.isoformat()} 复做再次错误：仍未先区分地址字段层级"
        self.apply_approved([redo], "BATCH-RU-V2-REDO")

        review = dict(zip(REVIEW_UNIT_COLS, self.rows("复习单元总表.md", REVIEW_UNIT_COLS)[0]))
        self.assertEqual(review["复习单元ID"], shared_unit_id)
        self.assertEqual(review["调度状态"], "active_scheduled")
        self.assertEqual(review["上次复习日期"], today.isoformat())
        self.assertEqual(review["下次复习日期"], (today + dt.timedelta(days=1)).isoformat())
        self.assertEqual(review["当前间隔天数"], "1")
        self.assertEqual(review["错误次数"], "2")

        track = dict(zip(ORIGINAL_TRACK_COLS, self.rows(ORIGINAL_TRACK_FILE, ORIGINAL_TRACK_COLS)[0]))
        self.assertEqual(track["主复习单元ID"], shared_unit_id)
        self.assertEqual(track["原题轨状态"], "awaiting_primary_baseline")
        self.assertEqual(track["7天资格"], "protected_until")
        self.assertEqual(track["最近复做日期"], today.isoformat())
        self.assertEqual(track["最早允许日期"], (today + dt.timedelta(days=7)).isoformat())
        self.assertEqual(track["下次原题复做日期"], "未记录")
        shared_card = read_text(self.repo / REVIEW_UNIT_CARD_DIR / f"{shared_unit_id}.md")
        self.assertIn('schedule_state: "active_scheduled"', shared_card)
        self.assertIn("- 当前状态：active_scheduled", shared_card)

        same = self.package("JOB-RU-V2-REDO-DUP", mode="redo", formal_id=fid)
        same["latest_review_date"] = redo["latest_review_date"]
        same["latest_error_record"] = redo["latest_error_record"]
        same["error_tags"] = redo["error_tags"]
        duplicate = self.apply_approved([same], "BATCH-RU-V2-REDO-DUP")
        self.assertEqual(duplicate.changed_files, [])
        review = dict(zip(REVIEW_UNIT_COLS, self.rows("复习单元总表.md", REVIEW_UNIT_COLS)[0]))
        self.assertEqual(review["错误次数"], "2")
        report = audit_review_schedule(self.repo, today)
        self.assertEqual(report["hard_errors"], 0, report)

    def _assert_duplicate_redo_surface_fails_before_wal(
        self,
        rel: str,
        columns: list[str],
        suffix: str,
    ) -> None:
        fid = self.apply_approved(
            [self.package(f"JOB-RU-CARDINALITY-BASE-{suffix}")],
            f"BATCH-RU-CARDINALITY-BASE-{suffix}",
        ).formal_ids[0]
        self._duplicate_only_table_row(rel, columns)
        before_repo = self._repo_bytes()
        before_wal = self._wal_bytes()
        redo = self.package(
            f"JOB-RU-CARDINALITY-REDO-{suffix}", mode="redo", formal_id=fid
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            self.apply_approved([redo], f"BATCH-RU-CARDINALITY-REDO-{suffix}")
        self.assertIn("必须恰好 1 行，实际 2 行", stderr.getvalue())
        self.assertEqual(self._repo_bytes(), before_repo)
        self.assertEqual(self._wal_bytes(), before_wal)

    def test_redo_rejects_duplicate_review_unit_mapping_before_wal(self) -> None:
        self._assert_duplicate_redo_surface_fails_before_wal(
            REVIEW_UNIT_MAPPING_FILE, REVIEW_UNIT_MAPPING_COLS, "MAPPING"
        )

    def test_redo_rejects_duplicate_review_unit_row_before_wal(self) -> None:
        self._assert_duplicate_redo_surface_fails_before_wal(
            "复习单元总表.md", REVIEW_UNIT_COLS, "UNIT"
        )

    def test_redo_rejects_duplicate_original_track_before_wal(self) -> None:
        self._assert_duplicate_redo_surface_fails_before_wal(
            ORIGINAL_TRACK_FILE, ORIGINAL_TRACK_COLS, "TRACK"
        )

    def _assert_missing_redo_surface_fails_before_wal(
        self,
        rel: str,
        columns: list[str],
        suffix: str,
    ) -> None:
        fid = self.apply_approved(
            [self.package(f"JOB-RU-MISSING-BASE-{suffix}")],
            f"BATCH-RU-MISSING-BASE-{suffix}",
        ).formal_ids[0]
        self._remove_only_table_row(rel, columns)
        before_repo = self._repo_bytes()
        before_wal = self._wal_bytes()
        redo = self.package(f"JOB-RU-MISSING-REDO-{suffix}", mode="redo", formal_id=fid)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            self.apply_approved([redo], f"BATCH-RU-MISSING-REDO-{suffix}")
        self.assertIn("必须恰好 1 行，实际 0 行", stderr.getvalue())
        self.assertEqual(self._repo_bytes(), before_repo)
        self.assertEqual(self._wal_bytes(), before_wal)

    def test_redo_rejects_missing_review_unit_mapping_before_wal(self) -> None:
        self._assert_missing_redo_surface_fails_before_wal(
            REVIEW_UNIT_MAPPING_FILE, REVIEW_UNIT_MAPPING_COLS, "MAPPING"
        )

    def test_redo_rejects_missing_review_unit_row_before_wal(self) -> None:
        self._assert_missing_redo_surface_fails_before_wal(
            "复习单元总表.md", REVIEW_UNIT_COLS, "UNIT"
        )

    def test_redo_rejects_missing_original_track_before_wal(self) -> None:
        self._assert_missing_redo_surface_fails_before_wal(
            ORIGINAL_TRACK_FILE, ORIGINAL_TRACK_COLS, "TRACK"
        )

    def test_redo_rejects_missing_review_unit_card_before_wal(self) -> None:
        fid = self.apply_approved(
            [self.package("JOB-RU-CARD-MISSING-BASE")], "BATCH-RU-CARD-MISSING-BASE"
        ).formal_ids[0]
        unit_id = f"RU_{fid}"
        (self.repo / REVIEW_UNIT_CARD_DIR / f"{unit_id}.md").unlink()
        before_repo = self._repo_bytes()
        before_wal = self._wal_bytes()
        redo = self.package("JOB-RU-CARD-MISSING-REDO", mode="redo", formal_id=fid)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            self.apply_approved([redo], "BATCH-RU-CARD-MISSING-REDO")
        self.assertIn("缺失", stderr.getvalue())
        self.assertEqual(self._repo_bytes(), before_repo)
        self.assertEqual(self._wal_bytes(), before_wal)

    def test_redo_rejects_malformed_review_unit_card_state_before_wal(self) -> None:
        fid = self.apply_approved(
            [self.package("JOB-RU-CARD-MALFORMED-BASE")], "BATCH-RU-CARD-MALFORMED-BASE"
        ).formal_ids[0]
        unit_id = f"RU_{fid}"
        card = self.repo / REVIEW_UNIT_CARD_DIR / f"{unit_id}.md"
        card.write_text(
            card.read_text(encoding="utf-8") + "\n- 当前状态：duplicate\n",
            encoding="utf-8",
        )
        before_repo = self._repo_bytes()
        before_wal = self._wal_bytes()
        redo = self.package("JOB-RU-CARD-MALFORMED-REDO", mode="redo", formal_id=fid)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            self.apply_approved([redo], "BATCH-RU-CARD-MALFORMED-REDO")
        self.assertIn("状态结构异常", stderr.getvalue())
        self.assertEqual(self._repo_bytes(), before_repo)
        self.assertEqual(self._wal_bytes(), before_wal)

    def test_receipt_bound_review_unit_v2_repair_replaces_only_legacy_surfaces(self) -> None:
        today = dt.date.today()
        package = self.package("JOB-RU-V2-REPAIR")
        package["first_done_date"] = today.isoformat()
        package["latest_review_date"] = today.isoformat()
        fid = self.apply_approved([package], "BATCH-RU-V2-REPAIR").formal_ids[0]
        unit_id = f"RU_{fid}"

        review_path = self.repo / "复习单元总表.md"
        review_lines = review_path.read_text(encoding="utf-8").splitlines()
        for index, cells in iter_table_rows(review_lines, REVIEW_UNIT_COLS[0]):
            if cells[0] == unit_id:
                legacy_cells = [
                    unit_id, "计算机组成原理", package["module"], package["core_point"],
                    "错题", today.isoformat(), today.isoformat(),
                    (today + dt.timedelta(days=1)).isoformat(), "1", "待评分", "1",
                    "4", "B中等", "旧复习方式", "旧备注",
                ]
                review_lines[index] = "| " + " | ".join(str(item) for item in legacy_cells) + " |"
        review_path.write_text("\n".join(review_lines) + "\n", encoding="utf-8")
        for rel, columns in (
            (REVIEW_UNIT_MAPPING_FILE, REVIEW_UNIT_MAPPING_COLS),
            (ORIGINAL_TRACK_FILE, ORIGINAL_TRACK_COLS),
        ):
            path = self.repo / rel
            lines = path.read_text(encoding="utf-8").splitlines()
            lines = [
                line for line in lines
                if not (line.startswith(f"| {fid} |") or line.startswith(f"|{fid}|"))
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        (self.repo / REVIEW_UNIT_CARD_DIR / f"{unit_id}.md").unlink()

        txn = Txn(Repo(self.repo))
        ensure_review_unit_v2_for_new(
            txn,
            package,
            fid,
            detail_entry=str(package["detail_entry"]),
            repair_existing=True,
            as_of=today,
        )
        projected = txn.rendered_files()
        self.assertEqual(
            set(projected),
            {
                "复习单元总表.md",
                REVIEW_UNIT_MAPPING_FILE,
                ORIGINAL_TRACK_FILE,
                f"{REVIEW_UNIT_CARD_DIR}/{unit_id}.md",
            },
        )
        for rel, data in projected.items():
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

        review_rows = self.rows("复习单元总表.md", REVIEW_UNIT_COLS)
        self.assertEqual(len(review_rows), 1)
        self.assertEqual(len(review_rows[0]), 18)
        self.assertEqual(len(self.rows(REVIEW_UNIT_MAPPING_FILE, REVIEW_UNIT_MAPPING_COLS)), 1)
        self.assertEqual(len(self.rows(ORIGINAL_TRACK_FILE, ORIGINAL_TRACK_COLS)), 1)
        self.assertTrue((self.repo / REVIEW_UNIT_CARD_DIR / f"{unit_id}.md").is_file())
        report = audit_review_schedule(self.repo, today)
        self.assertEqual(report["hard_errors"], 0, report)

    def test_receipt_bound_review_unit_v2_repair_preserves_valid_v2_schedule(self) -> None:
        today = dt.date.today()
        package = self.package("JOB-RU-V2-REPAIR-PRESERVE")
        fid = self.apply_approved(
            [package], "BATCH-RU-V2-REPAIR-PRESERVE"
        ).formal_ids[0]
        unit_id = f"RU_{fid}"
        review_path = self.repo / REVIEW_UNIT_FILE
        review_lines = review_path.read_text(encoding="utf-8").splitlines()
        for index, cells in iter_table_rows(review_lines, REVIEW_UNIT_COLS[0]):
            if cells[0] != unit_id:
                continue
            cells[REVIEW_UNIT_COLS.index("调度状态")] = "active_scheduled"
            cells[REVIEW_UNIT_COLS.index("覆盖节点")] = fid
            cells[REVIEW_UNIT_COLS.index("上次复习日期")] = today.isoformat()
            cells[REVIEW_UNIT_COLS.index("下次复习日期")] = (
                today + dt.timedelta(days=3)
            ).isoformat()
            cells[REVIEW_UNIT_COLS.index("当前间隔天数")] = "3"
            cells[REVIEW_UNIT_COLS.index("掌握度")] = "4"
            cells[REVIEW_UNIT_COLS.index("错误次数")] = "9"
            cells[REVIEW_UNIT_COLS.index("重要程度")] = "5"
            review_lines[index] = "| " + " | ".join(cells) + " |"
        review_path.write_text("\n".join(review_lines) + "\n", encoding="utf-8")
        before = review_path.read_bytes()

        txn = Txn(Repo(self.repo))
        ensure_review_unit_v2_for_new(
            txn,
            package,
            fid,
            detail_entry=str(package["detail_entry"]),
            repair_existing=True,
            as_of=today,
        )
        self.assertEqual(txn.rendered_files()[REVIEW_UNIT_FILE], before)
        preserved = next(
            cells for _, cells in iter_table_rows(
                before.decode("utf-8").splitlines(), REVIEW_UNIT_COLS[0]
            )
            if cells[0] == unit_id
        )
        values = dict(zip(REVIEW_UNIT_COLS, preserved))
        self.assertEqual(values["调度状态"], "active_scheduled")
        self.assertEqual(values["掌握度"], "4")
        self.assertEqual(values["错误次数"], "9")
        self.assertEqual(values["重要程度"], "5")

    def test_receipt_bound_review_unit_v2_repair_rejects_malformed_width_without_wal(self) -> None:
        package = self.package("JOB-RU-V2-REPAIR-BAD-WIDTH")
        fid = "CO_2026_001"
        unit_id = f"RU_{fid}"
        review_path = self.repo / REVIEW_UNIT_FILE
        review_lines = review_path.read_text(encoding="utf-8").splitlines()
        malformed = [unit_id] + ["损坏"] * 15
        review_lines.append("| " + " | ".join(malformed) + " |")
        review_path.write_text("\n".join(review_lines) + "\n", encoding="utf-8")
        before_repo = self._repo_bytes()
        before_wal = self._wal_bytes()

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            ensure_review_unit_v2_for_new(
                Txn(Repo(self.repo)),
                package,
                fid,
                detail_entry=str(package["detail_entry"]),
                repair_existing=True,
            )
        self.assertIn("仅支持旧 15 列或 v2 18 列，实际 16", stderr.getvalue())
        self.assertEqual(self._repo_bytes(), before_repo)
        self.assertEqual(self._wal_bytes(), before_wal)

    def test_receipt_bound_review_unit_v2_repair_rejects_wrong_coverage_endpoint(self) -> None:
        package = self.package("JOB-RU-V2-REPAIR-BAD-ENDPOINT")
        fid = self.apply_approved(
            [package], "BATCH-RU-V2-REPAIR-BAD-ENDPOINT"
        ).formal_ids[0]
        unit_id = f"RU_{fid}"
        review_path = self.repo / REVIEW_UNIT_FILE
        review_lines = review_path.read_text(encoding="utf-8").splitlines()
        for index, cells in iter_table_rows(review_lines, REVIEW_UNIT_COLS[0]):
            if cells[0] == unit_id:
                cells[REVIEW_UNIT_COLS.index("覆盖节点")] = "CO_2020_999"
                review_lines[index] = "| " + " | ".join(cells) + " |"
        review_path.write_text("\n".join(review_lines) + "\n", encoding="utf-8")
        before_repo = self._repo_bytes()
        before_wal = self._wal_bytes()

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
            ensure_review_unit_v2_for_new(
                Txn(Repo(self.repo)),
                package,
                fid,
                detail_entry=str(package["detail_entry"]),
                repair_existing=True,
            )
        self.assertIn(f"覆盖节点未包含 {fid}", stderr.getvalue())
        self.assertEqual(self._repo_bytes(), before_repo)
        self.assertEqual(self._wal_bytes(), before_wal)

    def test_new_rejects_orphan_review_surface_key_collisions_before_wal(self) -> None:
        collision_cases = (
            (REVIEW_UNIT_FILE, REVIEW_UNIT_COLS, "RU_CO_2026_001", "UNIT"),
            (REVIEW_UNIT_MAPPING_FILE, REVIEW_UNIT_MAPPING_COLS, "CO_2026_001", "MAPPING"),
            (ORIGINAL_TRACK_FILE, ORIGINAL_TRACK_COLS, "CO_2026_001", "TRACK"),
        )
        for rel, columns, key, suffix in collision_cases:
            with self.subTest(surface=rel):
                path = self.repo / rel
                clean = path.read_bytes()
                lines = clean.decode("utf-8").splitlines()
                orphan = [key] + ["孤儿"] * (len(columns) - 1)
                lines.append("| " + " | ".join(orphan) + " |")
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                before_repo = self._repo_bytes()
                before_wal = self._wal_bytes()
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit):
                    self.apply_approved(
                        [self.package(f"JOB-RU-ORPHAN-{suffix}")],
                        f"BATCH-RU-ORPHAN-{suffix}",
                    )
                self.assertIn("孤儿 review-unit surface 冲突", stderr.getvalue())
                self.assertEqual(self._repo_bytes(), before_repo)
                self.assertEqual(self._wal_bytes(), before_wal)
                path.write_bytes(clean)

    def test_secondary_hit_is_indexed_once_and_exact_network_audit_passes(self) -> None:
        package = self.package("JOB-SECONDARY-HIT")
        package["sub_knowledge"] = ["CO03-02 DRAM 基本机制"]
        package["hit_knowledge"] = [
            "CO03-01 Cache 基本机制",
            "CO03-02 DRAM 基本机制",
        ]
        fid = self.apply_approved([package], "BATCH-SECONDARY-HIT").formal_ids[0]
        index_text = read_text(self.repo / "知识点命中索引.md")
        section = index_text.split("### CO03-02 DRAM 基本机制", 1)[1]
        self.assertIn("#### 命中知识点", section)
        self.assertIn(f"- {fid}：", section)
        self.assertIn("主知识点 CO03-01", section)
        self.assertEqual(section.count(f"- {fid}："), 2)  # 命中行 + 错因触发行
        self.assertIn("- 节点数量：1", section)

        audit = subprocess.run(
            [sys.executable, str(SCRIPTS / "audit_network_health.py"), "--repo", str(self.repo)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(audit.returncode, 0, audit.stdout + audit.stderr)
        self.assertIn("[PASS] 命中索引与节点总表完全一致", audit.stdout)

    def test_receipt_bound_postcommit_repair_is_auditable_and_idempotent(self) -> None:
        _, fid, store, original = self._create_legacy_validating_job("SUCCESS")
        unit_id = f"RU_{fid}"
        legacy_review = read_text(self.repo / "复习单元总表.md")
        self.assertIn(f"| {unit_id} |", legacy_review)
        self.assertFalse((self.repo / REVIEW_UNIT_CARD_DIR / f"{unit_id}.md").exists())
        self.assertNotIn("### CO03-02 DRAM 基本机制", read_text(self.repo / HIT_INDEX_FILE))

        protected_rels = {
            "节点总表.md",
            "关系边表.md",
            "错题日期索引.md",
            REDO_FILE,
            "年份索引.md",
            "节点库/计算机组成原理节点.md",
            f"{fid}.md",
        }
        protected_before = {
            rel: (self.repo / rel).read_bytes() for rel in protected_rels
        }
        queue_before = (self.runtime / "jobs" / f"JOB-POSTCOMMIT-SUCCESS.json").read_bytes()
        approved_before = (self.runtime / "approved" / f"JOB-POSTCOMMIT-SUCCESS.json").read_bytes()
        original_receipt_before = Path(str(original.receipt_path)).read_bytes()

        dry = repair_postcommit_batch(
            ["JOB-POSTCOMMIT-SUCCESS"],
            repo_root=self.repo,
            details_root=self.details,
            runtime_root=self.runtime,
            actor="coordinator-g7-postcommit-repair",
            coordinator_generation=self.generation,
            dry_run=True,
        )
        self.assertEqual(dry.status, "DRY_RUN")
        self.assertEqual(
            {Path(path).relative_to(self.repo.resolve()).as_posix() for path in dry.changed_files},
            {
                HIT_INDEX_FILE,
                REVIEW_UNIT_FILE,
                REVIEW_UNIT_MAPPING_FILE,
                ORIGINAL_TRACK_FILE,
                f"{REVIEW_UNIT_CARD_DIR}/{unit_id}.md",
            },
        )
        for rel, before in protected_before.items():
            self.assertEqual((self.repo / rel).read_bytes(), before)

        repaired = repair_postcommit_batch(
            ["JOB-POSTCOMMIT-SUCCESS"],
            repo_root=self.repo,
            details_root=self.details,
            runtime_root=self.runtime,
            actor="coordinator-g7-postcommit-repair",
            coordinator_generation=self.generation,
        )
        self.assertEqual(repaired.status, "REPAIRED")
        self.assertEqual(repaired.idempotency_key, dry.idempotency_key)
        changed_rels = {
            Path(path).relative_to(self.repo.resolve()).as_posix() for path in repaired.changed_files
        }
        self.assertEqual(changed_rels, {
            HIT_INDEX_FILE,
            REVIEW_UNIT_FILE,
            REVIEW_UNIT_MAPPING_FILE,
            ORIGINAL_TRACK_FILE,
            f"{REVIEW_UNIT_CARD_DIR}/{unit_id}.md",
        })
        for rel, before in protected_before.items():
            self.assertEqual((self.repo / rel).read_bytes(), before, rel)
        self.assertEqual(
            (self.runtime / "jobs" / "JOB-POSTCOMMIT-SUCCESS.json").read_bytes(),
            queue_before,
        )
        self.assertEqual(
            (self.runtime / "approved" / "JOB-POSTCOMMIT-SUCCESS.json").read_bytes(),
            approved_before,
        )
        self.assertEqual(Path(str(original.receipt_path)).read_bytes(), original_receipt_before)
        self.assertEqual(store.status("JOB-POSTCOMMIT-SUCCESS")["state"], "VALIDATING")

        section = read_text(self.repo / HIT_INDEX_FILE).split(
            "### CO03-02 DRAM 基本机制", 1
        )[1]
        self.assertEqual(section.count(f"- {fid}："), 2)
        self.assertIn("- 节点数量：1", section)
        review_rows = self.rows(REVIEW_UNIT_FILE, REVIEW_UNIT_COLS)
        self.assertEqual(len(review_rows), 1)
        self.assertEqual(len(review_rows[0]), 18)
        self.assertEqual(len(self.rows(REVIEW_UNIT_MAPPING_FILE, REVIEW_UNIT_MAPPING_COLS)), 1)
        self.assertEqual(len(self.rows(ORIGINAL_TRACK_FILE, ORIGINAL_TRACK_COLS)), 1)
        self.assertTrue((self.repo / REVIEW_UNIT_CARD_DIR / f"{unit_id}.md").is_file())

        network = subprocess.run(
            [sys.executable, str(SCRIPTS / "audit_network_health.py"), "--repo", str(self.repo)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(network.returncode, 0, network.stdout + network.stderr)
        self.assertIn("[PASS] 命中索引与节点总表完全一致", network.stdout)
        schedule = audit_review_schedule(self.repo, dt.date.today())
        self.assertEqual(schedule["hard_errors"], 0, schedule)

        repaired_bytes = self._repo_bytes()
        again = repair_postcommit_batch(
            ["JOB-POSTCOMMIT-SUCCESS"],
            repo_root=self.repo,
            details_root=self.details,
            runtime_root=self.runtime,
            actor="coordinator-g99-different-actor",
            coordinator_generation=99,
        )
        self.assertEqual(again.status, "ALREADY_REPAIRED")
        self.assertEqual(again.idempotency_key, repaired.idempotency_key)
        self.assertEqual(again.transaction_id, repaired.transaction_id)
        self.assertEqual(self._repo_bytes(), repaired_bytes)
        self.assertEqual(section.count(f"- {fid}："), 2)

        receipt_value = json.loads(Path(str(repaired.receipt_path)).read_text(encoding="utf-8"))
        journal_path = Path(str(receipt_value["journal"]))
        journal_value = json.loads(journal_path.read_text(encoding="utf-8"))
        journal_value["files"][0]["published"] = False
        self._write_json(journal_path, journal_value)
        with self.assertRaisesRegex(BatchValidationError, "published=true"):
            repair_postcommit_batch(
                ["JOB-POSTCOMMIT-SUCCESS"],
                repo_root=self.repo,
                details_root=self.details,
                runtime_root=self.runtime,
                actor="coordinator-g100-validator",
                coordinator_generation=100,
            )

    def test_receipt_bound_postcommit_repair_fault_rolls_back_then_retries(self) -> None:
        _, fid, store, _ = self._create_legacy_validating_job("ROLLBACK")
        before = self._repo_bytes()
        with self.assertRaises(TransactionRolledBack):
            repair_postcommit_batch(
                ["JOB-POSTCOMMIT-ROLLBACK"],
                repo_root=self.repo,
                details_root=self.details,
                runtime_root=self.runtime,
                actor="coordinator-g7-postcommit-repair",
                coordinator_generation=self.generation,
                _fail_after=1,
            )
        self.assertEqual(self._repo_bytes(), before)
        self.assertEqual(store.status("JOB-POSTCOMMIT-ROLLBACK")["state"], "VALIDATING")

        retried = repair_postcommit_batch(
            ["JOB-POSTCOMMIT-ROLLBACK"],
            repo_root=self.repo,
            details_root=self.details,
            runtime_root=self.runtime,
            actor="coordinator-g7-postcommit-repair",
            coordinator_generation=self.generation,
        )
        self.assertEqual(retried.status, "REPAIRED")
        self.assertIn(
            f"- {fid}：",
            read_text(self.repo / HIT_INDEX_FILE).split(
                "### CO03-02 DRAM 基本机制", 1
            )[1],
        )
        self.assertEqual(
            audit_review_schedule(self.repo, dt.date.today())["hard_errors"], 0
        )

    def test_postcommit_repair_dirty_wal_dry_run_is_read_only_then_rolls_forward(self) -> None:
        _, fid, store, _ = self._create_legacy_validating_job("ROLLFORWARD")
        with self.assertRaises(SimulatedCrash):
            repair_postcommit_batch(
                ["JOB-POSTCOMMIT-ROLLFORWARD"],
                repo_root=self.repo,
                details_root=self.details,
                runtime_root=self.runtime,
                actor="coordinator-g7-postcommit-repair",
                coordinator_generation=self.generation,
                _crash_after=1,
            )
        partial_repo = self._repo_bytes()
        dirty_wal = self._wal_bytes()
        self.assertTrue(
            any(b'"state": "publishing"' in data for data in dirty_wal.values())
        )

        with self.assertRaisesRegex(BatchError, "dry-run 拒绝隐式恢复"):
            repair_postcommit_batch(
                ["JOB-POSTCOMMIT-ROLLFORWARD"],
                repo_root=self.repo,
                details_root=self.details,
                runtime_root=self.runtime,
                actor="coordinator-g7-postcommit-repair",
                coordinator_generation=self.generation,
                dry_run=True,
            )
        self.assertEqual(self._repo_bytes(), partial_repo)
        self.assertEqual(self._wal_bytes(), dirty_wal)
        self.assertEqual(store.status("JOB-POSTCOMMIT-ROLLFORWARD")["state"], "VALIDATING")

        recovered = repair_postcommit_batch(
            ["JOB-POSTCOMMIT-ROLLFORWARD"],
            repo_root=self.repo,
            details_root=self.details,
            runtime_root=self.runtime,
            actor="coordinator-g7-postcommit-repair",
            coordinator_generation=self.generation,
        )
        self.assertEqual(recovered.status, "ALREADY_REPAIRED")
        self.assertEqual(len(recovered.recovered), 1)
        self.assertEqual(recovered.recovered[0]["action"], "roll_forward")
        section = read_text(self.repo / HIT_INDEX_FILE).split(
            "### CO03-02 DRAM 基本机制", 1
        )[1]
        self.assertEqual(section.count(f"- {fid}："), 2)
        self.assertEqual(
            audit_review_schedule(self.repo, dt.date.today())["hard_errors"], 0
        )

    def test_two_processes_share_lock_and_allocate_unique_ids(self) -> None:
        paths = [
            self._stage_manifest([self.package("JOB-P01")], batch_id="BATCH-P1"),
            self._stage_manifest([self.package("JOB-P02")], batch_id="BATCH-P2"),
        ]
        processes = [
            subprocess.Popen([
                sys.executable, str(SCRIPTS / "intake_batch_apply_408.py"), str(path),
                "--repo", str(self.repo), "--details-root", str(self.details),
                "--runtime-root", str(self.runtime), "--no-bridge", "--json",
                "--actor", "coordinator-g7-writer", "--coordinator-generation", str(self.generation),
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for path in paths
        ]
        outputs = [process.communicate(timeout=15) for process in processes]
        for process, (stdout, stderr) in zip(processes, outputs):
            self.assertEqual(process.returncode, 0, stdout + stderr)
        self.assertEqual(set(Repo(self.repo).master()), {"CO_2026_001", "CO_2026_002"})

    def test_manifest_loader_keeps_contract_and_package_paths(self) -> None:
        path = self._stage_manifest([self.package("JOB-MMM")], batch_id="BATCH-M")
        packages, meta = load_batch_input(path)
        self.assertEqual(len(packages), 1)
        self.assertEqual(meta["contract"], "coordinator_batch_manifest_v1")
        self.assertEqual(meta["_package_paths"], [str(self.runtime / "approved" / "JOB-MMM.json")])
        with self.assertRaisesRegex(BatchValidationError, "显式提供 actor"):
            apply_batch_file(
                path, repo_root=self.repo, details_root=self.details,
                runtime_root=self.runtime, refresh_bridge=False,
            )

    def test_manifest_schema_legacy_compatibility_is_fail_closed(self) -> None:
        legacy = self._stage_manifest(
            [self.package("JOB-MANIFEST-LEGACY")],
            batch_id="BATCH-MANIFEST-LEGACY",
        )
        manifest = json.loads(legacy.read_text(encoding="utf-8"))
        manifest.pop("schema_version")
        self._write_json(legacy, manifest)
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.apply_manifest(legacy, dry_run=True)
        self.assertEqual(result.status, "DRY_RUN")

        conflict = self._stage_manifest(
            [self.package("JOB-MANIFEST-CONFLICT")],
            batch_id="BATCH-MANIFEST-CONFLICT",
        )
        manifest = json.loads(conflict.read_text(encoding="utf-8"))
        manifest["schema_version"] = "coordinator_batch_manifest_v999"
        self._write_json(conflict, manifest)
        with self.assertRaisesRegex(BatchValidationError, "contract/schema_version"):
            self.apply_manifest(conflict, dry_run=True)

    def test_decision_fail_stale_generation_state_version_and_claim_are_rejected(self) -> None:
        bad = self.package("JOB-DECISION")
        bad["decision"] = "fail"
        with self.assertRaises(BatchValidationError):
            self.apply_approved([bad], "BATCH-DECISION")

        path = self._stage_manifest([self.package("JOB-GEN")], batch_id="BATCH-GEN")
        coordinator = json.loads((self.runtime / "coordinator.json").read_text(encoding="utf-8"))
        coordinator["generation"] = self.generation + 1
        self._write_json(self.runtime / "coordinator.json", coordinator)
        with self.assertRaises(BatchValidationError):
            self.apply_manifest(path)
        coordinator["generation"] = self.generation
        self._write_json(self.runtime / "coordinator.json", coordinator)

        path = self._stage_manifest([self.package("JOB-STALE")], batch_id="BATCH-STALE")
        job_path = self.runtime / "jobs" / "JOB-STALE.json"
        job = json.loads(job_path.read_text(encoding="utf-8"))
        job["state"] = "REVIEWED"
        self._write_json(job_path, job)
        with self.assertRaises(BatchValidationError):
            self.apply_manifest(path)

        path = self._stage_manifest([self.package("JOB-CLAIM")], batch_id="BATCH-CLAIM")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["queue_claims"][0]["worker_id"] = "stale-reviewer"
        self._write_json(path, manifest)
        with self.assertRaises(BatchValidationError):
            self.apply_manifest(path)

    def test_commit_lease_and_source_sha_version_are_fenced(self) -> None:
        for suffix, mutate in (
            ("LEASE", lambda job: job["commit_lease"].update({"actor": "other-writer"})),
            ("SHA", lambda job: job.update({"submission_sha256": "0" * 64})),
            ("VERSION", lambda job: job.update({"state_version": 5})),
        ):
            path = self._stage_manifest([self.package(f"JOB-{suffix}")], batch_id=f"BATCH-{suffix}")
            job_path = self.runtime / "jobs" / f"JOB-{suffix}.json"
            job = json.loads(job_path.read_text(encoding="utf-8"))
            mutate(job)
            self._write_json(job_path, job)
            with self.assertRaises(BatchValidationError, msg=suffix):
                self.apply_manifest(path)

    def test_production_git_repo_rejects_runtime_split(self) -> None:
        (self.repo / ".git").mkdir()
        with self.assertRaises(BatchValidationError):
            apply_batch(
                [self.legacy_package()], repo_root=self.repo, details_root=self.details,
                runtime_root=self.runtime, refresh_bridge=False,
            )

    def test_formal_cli_has_no_skip_flags(self) -> None:
        result = subprocess.run([
            sys.executable, str(SCRIPTS / "intake_batch_apply_408.py"), "dummy.json",
            "--skip-preflight", "--no-edges", "--no-review-unit",
        ], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unrecognized arguments", result.stderr)

    def test_shadow_dry_run_accepts_reviewed_without_commit_lease(self) -> None:
        path = self._stage_manifest([self.package("JOB-SHADOW")], batch_id="BATCH-SHADOW")
        job_path = self.runtime / "jobs" / "JOB-SHADOW.json"
        job = json.loads(job_path.read_text(encoding="utf-8"))
        job["state"] = "REVIEWED"
        job["state_version"] = 3
        job.pop("commit_batch_id")
        job.pop("commit_lease")
        self._write_json(job_path, job)
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.apply_manifest(path, dry_run=True)
        self.assertEqual(result.status, "DRY_RUN")
        self.assertEqual(Repo(self.repo).master(), {})

    def test_shadow_dry_run_projects_each_current_new_relation_endpoint(self) -> None:
        base = self.apply_approved([self.package("JOB-REL-BASE")], "BATCH-REL-BASE")
        existing_id = base.formal_ids[0]
        before_master = copy.deepcopy(Repo(self.repo).master())

        packages = [self.package("JOB-REL-A"), self.package("JOB-REL-B")]
        for package in packages:
            job_id = str(package["job_id"])
            reviewer = str(package["reviewed_by"])
            package["relation_review"] = {
                "status": "connected",
                "basis": "已有节点与当前新题共享同一地址字段判定机制，关系方向已独立复核",
                "reviewed_by": reviewer,
            }
            package["relation_candidates"] = [{
                "from": existing_id,
                "to_job": job_id,
                "type": "R01 同一核心考点",
                "strength": "强",
                "reason": "两题都要求先确定地址字段服务层级，再解释缓存访问中的字段语义",
                "priority": "高",
            }]

        packages[1]["relation_candidates"] = [
            {
                "from_job": "JOB-REL-B",
                "to": existing_id,
                "type": "R01 同一核心考点",
                "strength": "强",
                "reason": "两题都要求先确定地址字段服务层级，再解释缓存访问中的字段语义",
                "priority": "高",
            },
            {
                "from_job": "JOB-REL-B",
                "to_job": "JOB-REL-A",
                "type": "R06 上下游知识链",
                "strength": "中",
                "reason": "先识别地址字段服务层级，才能继续判断另一题中的缓存访问字段语义",
                "priority": "中",
            },
        ]

        path = self._stage_manifest(packages, batch_id="BATCH-SHADOW-REL")
        for package in packages:
            job_path = self.runtime / "jobs" / f"{package['job_id']}.json"
            job = json.loads(job_path.read_text(encoding="utf-8"))
            job["state"] = "REVIEWED"
            job["state_version"] = 3
            job.pop("commit_batch_id")
            job.pop("commit_lease")
            self._write_json(job_path, job)

        protected_paths = [
            self.runtime / "manifests" / "BATCH-SHADOW-REL.json",
            *(self.runtime / "jobs" / f"{package['job_id']}.json" for package in packages),
            *(self.runtime / "approved" / f"{package['job_id']}.json" for package in packages),
        ]
        protected_bytes = {path: path.read_bytes() for path in protected_paths}
        receipt_bytes = {
            path: path.read_bytes() for path in (self.runtime / "receipts").rglob("*.json")
        }
        journal_bytes = {
            path: path.read_bytes() for path in (self.runtime / "transactions").rglob("journal.json")
        }

        with contextlib.redirect_stdout(io.StringIO()):
            result = self.apply_manifest(path, dry_run=True)

        self.assertEqual(result.status, "DRY_RUN")
        self.assertEqual(result.formal_ids, ["CO_2026_002", "CO_2026_003"])
        self.assertEqual(result.warnings, [])
        self.assertEqual(Repo(self.repo).master(), before_master)
        self.assertEqual(len(Repo(self.repo).edges()), 0)
        self.assertFalse((self.details / "cards" / "CO_2026_002.md").exists())
        self.assertFalse((self.details / "cards" / "CO_2026_003.md").exists())
        self.assertEqual({path: path.read_bytes() for path in protected_paths}, protected_bytes)
        self.assertEqual(
            {path: path.read_bytes() for path in (self.runtime / "receipts").rglob("*.json")},
            receipt_bytes,
        )
        self.assertEqual(
            {path: path.read_bytes() for path in (self.runtime / "transactions").rglob("journal.json")},
            journal_bytes,
        )

    def test_projected_repo_separates_visible_new_ids_from_persisted_identity(self) -> None:
        base = self.apply_approved([self.package("JOB-PROJECTED-BASE")], "BATCH-PROJECTED-BASE")
        base_id = base.formal_ids[0]
        current = self.package("JOB-PROJECTED-CURRENT")
        other = self.package("JOB-PROJECTED-OTHER")
        current["formal_id"] = "CO_2026_002"
        other["formal_id"] = "CO_2026_003"
        projected = ProjectedRepo(
            Repo(self.repo),
            {str(current["formal_id"]): current, str(other["formal_id"]): other},
        )

        self.assertEqual(set(projected.persisted_master()), {base_id})
        self.assertEqual(set(projected.master()), {base_id, "CO_2026_002", "CO_2026_003"})
        self.assertEqual(suggest_id(projected, "CO", "2026"), "CO_2026_002")

        new_report = run_preflight(current, projected, self.details)
        self.assertFalse(new_report.failed)
        self.assertIn(("PASS", "ID唯一性", "CO_2026_002 未占用"), new_report.items)

        redo_report = run_preflight({
            "mode": "redo",
            "formal_id": "CO_2026_002",
            "latest_review_date": "2026-07-11",
            "latest_error_record": "2026-07-11 复做仍未先区分地址字段的适用层级",
        }, projected, self.details)
        self.assertTrue(redo_report.failed)
        self.assertTrue(any(
            level == "FAIL" and check == "ID存在性"
            for level, check, _ in redo_report.items
        ))

    def test_remaining_preflight_warn_blocks_approved_auto_commit(self) -> None:
        package = self.package("JOB-WARN", source_id="SRC-NONSTANDARD")
        with self.assertRaisesRegex(BatchValidationError, "仍有 WARN"):
            self.apply_approved([package], "BATCH-WARN")
        self.assertEqual(Repo(self.repo).master(), {})

    def test_fake_evidence_hash_and_symlink_are_rejected(self) -> None:
        package = self.package("JOB-FAKE")
        path = self._stage_manifest([package], batch_id="BATCH-FAKE")
        Path(str(package["evidence"][0]["path"])).write_text("tampered", encoding="utf-8")
        with self.assertRaises(BatchValidationError):
            self.apply_manifest(path)

        package = self.package("JOB-LINK")
        original = Path(str(package["detail_card_source"]["path"]))
        link = original.with_name("detail-link.md")
        link.symlink_to(original.name)
        spec = {"path": str(link), "sha256": hashlib.sha256(original.read_bytes()).hexdigest(), "protected": True}
        package["detail_card_source"] = copy.deepcopy(spec)
        package["evidence"][0] = copy.deepcopy(spec)
        path = self._stage_manifest([package], batch_id="BATCH-LINK")
        with self.assertRaisesRegex(BatchValidationError, "符号链接"):
            self.apply_manifest(path)

        package = self.package("JOB-DIR-LINK")
        raw_job_dir = self.details / "inbox" / "JOB-DIR-LINK"
        real_job_dir = self.details / "inbox" / "JOB-DIR-LINK-real"
        raw_job_dir.rename(real_job_dir)
        raw_job_dir.symlink_to(real_job_dir, target_is_directory=True)
        path = self._stage_manifest([package], batch_id="BATCH-DIR-LINK")
        with self.assertRaisesRegex(BatchValidationError, "raw job 目录不能是符号链接"):
            self.apply_manifest(path)

    def test_manifest_claim_token_and_claim_event_are_fenced_to_queue_job(self) -> None:
        path = self._stage_manifest([self.package("JOB-TOKEN-TAMPER")], batch_id="BATCH-TOKEN-TAMPER")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["queue_claims"][0]["claim_token"] = "tampered-claim-token-0000000000000000"
        self._write_json(path, manifest)
        with self.assertRaisesRegex(BatchValidationError, "claim_token_sha256"):
            self.apply_manifest(path)

        path = self._stage_manifest([self.package("JOB-EVENT-TAMPER")], batch_id="BATCH-EVENT-TAMPER")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["queue_claims"][0]["claim_event_id"] = "tampered-claim-event-id"
        self._write_json(path, manifest)
        with self.assertRaisesRegex(BatchValidationError, "claim_event_id"):
            self.apply_manifest(path)

    def test_relation_endpoints_and_ownership_are_strict(self) -> None:
        package = self.package("JOB-DANGLING")
        package["relation_review"] = {
            "status": "connected", "basis": "两个节点共享同一核心机制且解题入口完全一致",
            "reviewed_by": package["reviewed_by"],
        }
        package["relation_candidates"] = [{
            "from": "CO_2020_999", "to_job": "JOB-DANGLING", "type": "R01 同一核心考点",
            "strength": "强", "reason": "两个节点共享同一核心机制且解题入口完全一致", "priority": "高",
        }]
        with self.assertRaisesRegex(BatchValidationError, "关系端点不在 base/projected master"):
            self.apply_approved([package], "BATCH-DANGLING")

        a, b, c = self.package("JOB-OWN-A"), self.package("JOB-OWN-B"), self.package("JOB-OWN-C")
        a["relation_review"] = {
            "status": "connected", "basis": "复核发现另两题共享机制但该边并不包含当前题",
            "reviewed_by": a["reviewed_by"],
        }
        a["relation_candidates"] = [{
            "from_job": "JOB-OWN-B", "to_job": "JOB-OWN-C", "type": "R01 同一核心考点",
            "strength": "强", "reason": "另两题共享同一核心机制但与当前题无直接关系", "priority": "高",
        }]
        with self.assertRaisesRegex(BatchValidationError, "每条边必须包含本题"):
            self.apply_approved([a, b, c], "BATCH-OWN")

    def test_template_only_relation_reason_is_rejected(self) -> None:
        a, b = self.package("JOB-TEMPLATE-A"), self.package("JOB-TEMPLATE-B")
        a["relation_review"] = {
            "status": "connected",
            "basis": "审核器故意提交纯字段模板，事务层必须拒绝，防止同知识点满团回归",
            "reviewed_by": a["reviewed_by"],
        }
        a["relation_candidates"] = [{
            "from_job": "JOB-TEMPLATE-A",
            "to_job": "JOB-TEMPLATE-B",
            "type": "R01 同一核心考点",
            "strength": "强",
            "reason": "同一主知识点：CO03-01 Cache 基本机制。",
            "priority": "高",
        }]
        with self.assertRaisesRegex(BatchValidationError, "字段模板"):
            self.apply_approved([a, b], "BATCH-TEMPLATE")

    def test_approved_redo_relation_and_topic_chain_are_rejected(self) -> None:
        created = self.apply_approved([self.package("JOB-BASE")], "BATCH-BASE")
        fid = created.formal_ids[0]
        redo = self.package("JOB-REDO-REL", mode="redo", formal_id=fid)
        redo["relation_review"] = {
            "status": "connected", "basis": "本次复核试图在 redo 中顺带修改关系，必须拒绝",
            "reviewed_by": redo["reviewed_by"],
        }
        redo["relation_candidates"] = [{
            "from": fid, "to": fid, "type": "R01 同一核心考点", "strength": "强",
            "reason": "故意构造 redo 关系写入以验证硬拒绝路径", "priority": "高",
        }]
        with self.assertRaisesRegex(BatchValidationError, "redo 不支持关系"):
            self.apply_approved([redo], "BATCH-REDO-REL")

        topic = self.package("JOB-TOPIC")
        topic["topic_chain_candidates"] = ["LINK01"]
        with self.assertRaisesRegex(BatchValidationError, "专题链"):
            self.apply_approved([topic], "BATCH-TOPIC")

    def test_legacy_is_single_explicit_id_and_has_no_auto_id(self) -> None:
        one, two = self.legacy_package("CO_2026_001"), self.legacy_package("CO_2026_002")
        with self.assertRaisesRegex(BatchValidationError, "batch size=1"):
            apply_batch(
                [one, two], repo_root=self.repo, details_root=self.details,
                runtime_root=self.runtime, refresh_bridge=False,
            )
        auto = self.legacy_package()
        auto.pop("formal_id")
        auto["auto_id"] = True
        with self.assertRaises(BatchValidationError):
            apply_batch(
                [auto], repo_root=self.repo, details_root=self.details,
                runtime_root=self.runtime, refresh_bridge=False,
            )

    def test_redo_receipt_and_semantic_retry_do_not_increment_twice(self) -> None:
        created = self.apply_approved([self.package("JOB-RRR")], "BATCH-NEW-R")
        fid = created.formal_ids[0]
        redo = self.package("JOB-R01", mode="redo", formal_id=fid)
        first = self.apply_approved([redo], "BATCH-REDO-ONCE")
        path = self.runtime / "manifests" / "BATCH-REDO-ONCE.json"
        second = self.apply_manifest(path)
        self.assertEqual(first.status, "COMMITTED")
        self.assertEqual(second.status, "ALREADY_COMMITTED")

        same = self.package("JOB-R02", mode="redo", formal_id=fid)
        same["latest_review_date"] = redo["latest_review_date"]
        same["latest_error_record"] = redo["latest_error_record"]
        same["error_tags"] = redo["error_tags"]
        third = self.apply_approved([same], "BATCH-REDO-SAME")
        self.assertEqual(third.changed_files, [])
        rows = [cells for _, cells in iter_table_rows(read_text(self.repo / REDO_FILE).splitlines(), "日期")]
        matching = [row for row in rows if row[1] == fid and row[0] == "2026-07-11"]
        self.assertEqual(len(matching), 1)

    def test_semantic_duplicate_still_applies_new_error_tag_without_increment(self) -> None:
        fid = self.apply_approved([self.package("JOB-TAG-BASE")], "BATCH-TAG-BASE").formal_ids[0]
        redo = self.package("JOB-TAG-R1", mode="redo", formal_id=fid)
        self.apply_approved([redo], "BATCH-TAG-R1")
        correction = self.package("JOB-TAG-R2", mode="redo", formal_id=fid)
        correction["latest_review_date"] = redo["latest_review_date"]
        correction["latest_error_record"] = redo["latest_error_record"]
        correction["error_tags"] = ["E03 计算路径错误"]
        self.apply_approved([correction], "BATCH-TAG-R2")
        self.assertIn("E03 计算路径错误", Repo(self.repo).master()[fid]["错因标签"])
        rows = [cells for _, cells in iter_table_rows(read_text(self.repo / REDO_FILE).splitlines(), "日期")]
        self.assertEqual(len([row for row in rows if row[1] == fid and row[0] == "2026-07-11"]), 1)
        review_text = read_text(self.repo / "复习单元总表.md")
        self.assertIn("| 2 |", review_text)
        self.assertNotIn("| 3 |", review_text)

    def test_crash_rolls_forward_and_recoverable_failure_rolls_back(self) -> None:
        package = self.package("JOB-CRASH")
        path = self._stage_manifest([package], batch_id="BATCH-CRASH")
        with self.assertRaises(SimulatedCrash):
            self.apply_manifest(path, _crash_after=1)
        result = self.apply_manifest(path)
        self.assertEqual(result.status, "ALREADY_COMMITTED")
        receipt = json.loads(Path(result.receipt_path).read_text(encoding="utf-8"))
        self.assertEqual(receipt["recovery"]["action"], "roll_forward")

        package = self.package("JOB-ROLLBACK")
        path = self._stage_manifest([package], batch_id="BATCH-ROLLBACK")
        with self.assertRaises(TransactionRolledBack) as captured:
            self.apply_manifest(path, _fail_after=1)
        rollback = json.loads(captured.exception.receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(rollback["recovery"]["action"], "roll_back")
        retried = self.apply_manifest(path)
        self.assertEqual(retried.status, "COMMITTED")

    def test_transaction_cas_rejects_edit_after_projection_without_wal(self) -> None:
        target = (self.repo / "节点总表.md").resolve()
        expected = hashlib.sha256(target.read_bytes()).hexdigest()
        target.write_text("external edit after projection\n", encoding="utf-8")
        with self.assertRaisesRegex(BatchError, "base hash 已改变"):
            _commit_transaction(
                self.repo,
                self.runtime,
                {target: b"stale projected bytes\n"},
                "relation-semantic-cas-test",
                digest("payload"),
                [],
                [self.repo],
                "test-writer",
                None,
                [],
                None,
                None,
                {"mode": "test"},
                self.details,
                expected_before_hashes={target: expected},
            )
        self.assertEqual(target.read_text(encoding="utf-8"), "external edit after projection\n")
        self.assertFalse((self.runtime / "transactions").exists())

    def test_transaction_delete_commits_and_is_receipted(self) -> None:
        target = (self.repo / "delete-me.md").resolve()
        target.write_bytes(b"recoverable deletion\n")
        expected = hashlib.sha256(target.read_bytes()).hexdigest()
        txid, receipt, changed = _commit_transaction(
            self.repo,
            self.runtime,
            {},
            "explicit-delete-test",
            digest("delete-payload"),
            [{"formal_id": "CO_2099_999", "mode": "delete"}],
            [self.repo],
            "test-writer",
            None,
            [],
            None,
            None,
            {"mode": "formal_node_deletion_v1"},
            self.details,
            expected_before_hashes={target: expected},
            delete_targets={target},
        )
        self.assertTrue(txid.startswith("TXN-"))
        self.assertFalse(target.exists())
        self.assertEqual(changed, [str(target)])
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(payload["items"][0]["mode"], "delete")
        journal = json.loads(Path(payload["journal"]).read_text(encoding="utf-8"))
        self.assertEqual(journal["files"][0]["operation"], "delete")
        self.assertIsNone(journal["files"][0]["after_sha256"])

    def test_transaction_delete_failure_rolls_back_original_bytes(self) -> None:
        first = (self.repo / "delete-first.md").resolve()
        second = (self.repo / "replace-second.md").resolve()
        first.write_bytes(b"must return after rollback\n")
        second.write_bytes(b"old bytes\n")
        before_first = hashlib.sha256(first.read_bytes()).hexdigest()
        before_second = hashlib.sha256(second.read_bytes()).hexdigest()
        with self.assertRaises(TransactionRolledBack):
            _commit_transaction(
                self.repo,
                self.runtime,
                {second: b"new bytes\n"},
                "explicit-delete-rollback-test",
                digest("delete-rollback-payload"),
                [],
                [self.repo],
                "test-writer",
                None,
                [],
                None,
                None,
                {"mode": "formal_node_deletion_v1"},
                self.details,
                fail_after=1,
                expected_before_hashes={first: before_first, second: before_second},
                delete_targets={first},
            )
        self.assertEqual(first.read_bytes(), b"must return after rollback\n")
        self.assertEqual(second.read_bytes(), b"old bytes\n")

    def test_transaction_delete_crash_rolls_forward(self) -> None:
        target = (self.repo / "delete-crash.md").resolve()
        target.write_bytes(b"delete after recovery\n")
        expected = hashlib.sha256(target.read_bytes()).hexdigest()
        with self.assertRaises(SimulatedCrash):
            _commit_transaction(
                self.repo,
                self.runtime,
                {},
                "explicit-delete-crash-test",
                digest("delete-crash-payload"),
                [],
                [self.repo],
                "test-writer",
                None,
                [],
                None,
                None,
                {"mode": "formal_node_deletion_v1"},
                self.details,
                crash_after=1,
                expected_before_hashes={target: expected},
                delete_targets={target},
            )
        self.assertFalse(target.exists())
        recovered = recover_transactions(self.repo, self.runtime)
        self.assertEqual(recovered[0]["action"], "roll_forward")
        self.assertFalse(target.exists())

    def test_transaction_delete_missing_target_fails_before_wal(self) -> None:
        target = (self.repo / "missing-delete.md").resolve()
        with self.assertRaisesRegex(BatchError, "delete 目标不存在"):
            _commit_transaction(
                self.repo,
                self.runtime,
                {},
                "explicit-delete-missing-test",
                digest("delete-missing-payload"),
                [],
                [self.repo],
                "test-writer",
                None,
                [],
                None,
                None,
                {"mode": "formal_node_deletion_v1"},
                self.details,
                expected_before_hashes={target: None},
                delete_targets={target},
            )
        self.assertFalse((self.runtime / "transactions").exists())

    def test_transaction_rejects_symlink_escape_even_when_base_hash_matches(self) -> None:
        target = (self.repo / "节点总表.md").resolve()
        baseline = target.read_bytes()
        expected = hashlib.sha256(baseline).hexdigest()
        outside = Path(self.tmp.name) / "outside.md"
        outside.write_bytes(baseline)
        target.unlink()
        target.symlink_to(outside)
        with self.assertRaises(BatchError):
            _commit_transaction(
                self.repo,
                self.runtime,
                {target: b"must not escape\n"},
                "relation-semantic-symlink-test",
                digest("payload"),
                [],
                [self.repo],
                "test-writer",
                None,
                [],
                None,
                None,
                {"mode": "test"},
                self.details,
                expected_before_hashes={target: expected},
            )
        self.assertEqual(outside.read_bytes(), baseline)
        self.assertTrue(target.is_symlink())
        self.assertFalse((self.runtime / "transactions").exists())

    def test_p0_dry_run_never_recovers_incomplete_wal_or_creates_receipt(self) -> None:
        path = self._stage_manifest([self.package("JOB-DRY-CRASH")], batch_id="BATCH-DRY-CRASH")
        with self.assertRaises(SimulatedCrash):
            self.apply_manifest(path, _crash_after=1)
        journals = list((self.runtime / "transactions").rglob("journal.json"))
        self.assertEqual(len(journals), 1)
        before_journal = journals[0].read_bytes()
        before_receipts = set((self.runtime / "receipts").rglob("*.json"))

        with self.assertRaisesRegex(BatchError, "--recover-only"):
            self.apply_manifest(path, dry_run=True)

        self.assertEqual(journals[0].read_bytes(), before_journal)
        self.assertEqual(set((self.runtime / "receipts").rglob("*.json")), before_receipts)

    def test_p0_dry_run_never_backfills_missing_committed_receipt(self) -> None:
        committed = self.apply_approved([self.package("JOB-DRY-RECEIPT")], "BATCH-DRY-RECEIPT")
        receipt = Path(str(committed.receipt_path))
        receipt.unlink()
        self.assertFalse(receipt.exists())
        shadow = self._stage_manifest([self.package("JOB-DRY-SHADOW")], batch_id="BATCH-DRY-SHADOW")

        with self.assertRaisesRegex(BatchError, "--recover-only"):
            self.apply_manifest(shadow, dry_run=True)

        self.assertFalse(receipt.exists())

    def test_bad_item_keeps_entire_batch_at_zero_writes(self) -> None:
        good = self.package("JOB-GOOD")
        bad = self.package("JOB-BAD", kp="CO03-99 不存在知识点")
        with self.assertRaises(BatchValidationError):
            self.apply_approved([good, bad], "BATCH-BAD")
        self.assertEqual(Repo(self.repo).master(), {})

    def test_legacy_cli_delegates_to_batch_size_one_without_bypass_flags(self) -> None:
        package = self.legacy_package()
        path = Path(self.tmp.name) / "legacy.json"
        self._write_json(path, package)
        result = subprocess.run([
            sys.executable, str(SCRIPTS / "intake_apply_408.py"), str(path),
            "--repo", str(self.repo), "--details-root", str(self.details),
            "--runtime-root", str(self.runtime), "--idempotency-key", "legacy-key", "--no-bridge",
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("batch size=1", result.stdout)
        self.assertEqual(set(Repo(self.repo).master()), {"CO_2026_001"})


if __name__ == "__main__":
    unittest.main()
