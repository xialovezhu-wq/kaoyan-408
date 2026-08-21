#!/usr/bin/env python3
"""408 底层单写事务与历史批次恢复引擎。

2026-07-12 起，日常正式入库必须从 ``intake_apply_408.py`` 进入并保持
batch size=1。本模块继续提供锁、WAL、幂等、恢复和回归测试所需的批量能力；其
CLI 对多 package 默认拒绝，只有显式历史演练开关才能解锁。

输入可为旧版单题 object、package list，或 ``{"packages": [...]}`` manifest。
所有包在同一仓库快照上重新 preflight，先投影到同一个内存 Txn，再由一把
仓库级 ``fcntl.flock`` 通过 staging + WAL 原子发布。bridge 仅在释放锁后刷新。

coordinator approved new 包可省略 formal_id（锁内确定性分配），但必须携带
``relation_review.status``，并把 details inbox 中经过 SHA-256 校验的详情卡投影
到 details/cards/{formal_id}.md；inbox 原件永不移动或删除。
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from intake_apply_engine_408 import (  # noqa: E402
    Txn, apply_new, apply_redo, relation_review_status,
)
from intake_lib_408 import (  # noqa: E402
    FORMAL_ID_RE, REDO_COLS, REDO_FILE, Repo, iter_table_rows, normalize_tag,
    read_text,
)
from intake_lock_408 import RepoLock  # noqa: E402
from intake_preflight_408 import DETAILS_ROOT_DEFAULT, run_preflight  # noqa: E402


DEFAULT_RUNTIME_ROOT = Path("~/.codex/kaoyan-408-intake").expanduser().resolve()
APPROVED_CONTRACT = "coordinator_approved_intake_v1"
MANIFEST_CONTRACT = "coordinator_batch_manifest_v1"
REVIEW_STATUSES = {"connected", "reviewed_no_reliable_edge", "needs_user"}
TERMINAL_WAL_STATES = {"committed", "rolled_back"}
APPROVED_REQUIRED_CHECKS: dict[str, Any] = {
    "evidence_hashes_verified": True,
    "answer_leak_check": "pass",
    "knowledge_tags_verified": True,
    "error_tags_verified": True,
    "duplicate_check": "pass",
    "historical_mapping_check": "pass_or_not_applicable",
}
JOB_ID_RE = re.compile(r"^JOB-[A-Za-z0-9][A-Za-z0-9._-]{2,95}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TEMPLATE_ONLY_RELATION_REASON_RE = re.compile(
    r"^(?:同一主知识点|同属专题链|同一模糊概念)[：:][^，,；;。]{1,100}[。.]?$"
)
SUBMISSION_PAYLOAD_KEYS = {
    "schema_version", "contract", "job_id", "idempotency_key", "mode_hint",
    "subject_hint", "year_hint", "existing_formal_id", "evidence", "safe_context",
    "unreviewed_candidates", "relation_review", "missing_fields", "redaction",
    "worker_self_check",
}


class BatchError(RuntimeError):
    """批量输入、门禁或事务错误。"""


class BatchValidationError(BatchError):
    pass


class LockTimeout(BatchError):
    pass


class TransactionRolledBack(BatchError):
    def __init__(self, message: str, receipt_path: Path):
        super().__init__(message)
        self.receipt_path = receipt_path


class SimulatedCrash(BaseException):
    """仅供故障注入测试：模拟进程在 os.replace 后直接消失。"""


@dataclass
class BatchResult:
    status: str
    idempotency_key: str
    transaction_id: str | None
    items: list[dict[str, str]]
    receipt_path: str | None = None
    changed_files: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def formal_ids(self) -> list[str]:
        return [item["formal_id"] for item in self.items]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "idempotency_key": self.idempotency_key,
            "transaction_id": self.transaction_id,
            "items": self.items,
            "receipt_path": self.receipt_path,
            "changed_files": self.changed_files,
            "logs": self.logs,
            "warnings": self.warnings,
        }


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _repo_key(repo_root: Path) -> str:
    return hashlib.sha256(str(repo_root.resolve()).encode("utf-8")).hexdigest()[:20]


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: Path, data: bytes) -> None:
    """唯一 sibling temp + fsync + os.replace。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with tmp.open("xb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    finally:
        if tmp.exists():
            tmp.unlink()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchError(f"JSON 读取失败 {path}：{exc}") from exc
    if not isinstance(value, dict):
        raise BatchError(f"JSON 必须是 object：{path}")
    return value


def _package_entry_path(entry: Any, base: Path) -> Path | None:
    if isinstance(entry, dict) and set(entry) == {"path"}:
        entry = entry["path"]
    if isinstance(entry, str):
        path = Path(entry).expanduser()
        if not path.is_absolute():
            path = base / path
        return path
    return None


def _load_package_entry(entry: Any, base: Path) -> dict[str, Any]:
    path = _package_entry_path(entry, base)
    if path is not None:
        return _read_json_object(path.resolve())
    if not isinstance(entry, dict):
        raise BatchError("manifest packages 每项必须是 object、路径字符串或 {path: ...}")
    return copy.deepcopy(entry)


def load_batch_input(path: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """读取 object/list/manifest；相对 package path 以 manifest 所在目录解析。"""
    raw_path = Path(path).expanduser()
    if not raw_path.is_absolute():
        raw_path = Path.cwd() / raw_path
    p = raw_path.resolve()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchError(f"批量输入读取失败 {p}：{exc}") from exc
    meta: dict[str, Any] = {"input_path": str(raw_path)}
    if isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict) and "packages" in raw:
        entries = raw["packages"]
        if not isinstance(entries, list):
            raise BatchError("manifest.packages 必须是数组")
        meta.update({k: v for k, v in raw.items() if k != "packages"})
    elif isinstance(raw, dict):
        entries = [raw]
    else:
        raise BatchError("批量输入必须是 package object、package list 或含 packages 的 manifest object")
    package_paths = [_package_entry_path(entry, p.parent) for entry in entries]
    packages = [_load_package_entry(entry, p.parent) for entry in entries]
    if not packages:
        raise BatchError("批次为空")
    meta["_package_paths"] = [str(path) if path is not None else None for path in package_paths]
    return packages, meta


class ProjectedRepo:
    """preflight 只读视图：让当前及同批其他 new ID 都可作为关系端点。"""

    def __init__(self, base: Repo, visible_new: dict[str, dict[str, Any]]):
        self.base = base
        self.visible_new = visible_new

    def master(self) -> dict[str, dict[str, Any]]:
        out = dict(self.base.master())
        for fid, pkg in self.visible_new.items():
            out[fid] = {
                "ID": fid,
                "来源ID": str(pkg.get("source_id", "")),
                "主知识点": str(pkg.get("main_knowledge", "")),
            }
        return out

    def persisted_master(self) -> dict[str, dict[str, Any]]:
        """供 preflight 的 ID 唯一性/顺序检查读取未投影的正式基线。"""
        return self.base.master()

    def __getattr__(self, name: str):
        return getattr(self.base, name)


def _contract(pkg: dict[str, Any]) -> str:
    return str(pkg.get("contract") or pkg.get("schema_version") or pkg.get("schema") or "").strip()


def _validate_contract_identity(pkg: dict[str, Any]) -> str:
    values = {
        str(pkg[key]).strip()
        for key in ("contract", "schema_version", "schema")
        if key in pkg and pkg[key] not in (None, "")
    }
    if len(values) > 1:
        raise BatchValidationError(f"package contract/schema 不一致：{sorted(values)}")
    contract = next(iter(values), "")
    if contract == APPROVED_CONTRACT:
        if pkg.get("contract") != APPROVED_CONTRACT or pkg.get("schema_version") != APPROVED_CONTRACT:
            raise BatchValidationError("approved 包必须同时显式 contract/schema_version=coordinator_approved_intake_v1")
    return contract


def _allocate_ids(packages: list[dict[str, Any]], repo: Repo) -> None:
    """锁内按输入顺序确定性分配；显式 ID 与 base 一起占位。"""
    used = set(repo.master())
    explicit: set[str] = set()
    for pkg in packages:
        fid = str(pkg.get("formal_id") or "").strip()
        if fid:
            if fid in explicit:
                raise BatchValidationError(f"批内正式 ID 重复：{fid}")
            explicit.add(fid)
            used.add(fid)

    for pkg in packages:
        mode = str(pkg.get("mode") or "new").strip()
        fid = str(pkg.get("formal_id") or "").strip()
        if fid or mode != "new":
            continue
        if _contract(pkg) != APPROVED_CONTRACT or pkg.get("auto_id") is not True:
            raise BatchValidationError("只有 coordinator approved new 且 auto_id=true 可锁内分配正式 ID")
        subject = str(pkg.get("subject") or "").strip()
        year = str(pkg.get("year") or "").strip()
        if subject not in {"DS", "CO", "OS", "CN"} or not re.fullmatch(r"\d{4}|UNK", year):
            raise BatchValidationError(f"auto ID 需要合法 subject/year：{subject}/{year}")
        prefix = f"{subject}_{year}_"
        seqs = [int(x.rsplit("_", 1)[1]) for x in used if x.startswith(prefix) and FORMAL_ID_RE.match(x)]
        next_seq = max(seqs, default=0) + 1
        if next_seq > 999:
            raise BatchValidationError(f"{subject}/{year} 序号已超过三位上限")
        fid = f"{prefix}{next_seq:03d}"
        pkg["formal_id"] = fid
        used.add(fid)


def _resolve_job_relations(packages: list[dict[str, Any]]) -> None:
    job_to_id: dict[str, str] = {}
    for pkg in packages:
        job_id = str(pkg.get("job_id") or "").strip()
        if job_id:
            if job_id in job_to_id:
                raise BatchValidationError(f"批内 job_id 重复：{job_id}")
            job_to_id[job_id] = str(pkg.get("formal_id") or "").strip()

    def resolve(value: Any) -> str:
        text = str(value or "").strip()
        if text.startswith("job:"):
            key = text[4:]
            if key not in job_to_id:
                raise BatchValidationError(f"关系引用未知 job：{key}")
            return job_to_id[key]
        return job_to_id.get(text, text)

    for pkg in packages:
        fid = str(pkg.get("formal_id") or "").strip()
        for rel in pkg.get("relation_candidates") or []:
            if not isinstance(rel, dict):
                raise BatchValidationError(f"{fid} relation_candidates 每项必须是 object")
            if rel.get("from_job"):
                rel["from"] = resolve(f"job:{rel['from_job']}")
            else:
                rel["from"] = resolve(rel.get("from") or fid)
            if rel.get("to_job"):
                rel["to"] = resolve(f"job:{rel['to_job']}")
            else:
                rel["to"] = resolve(rel.get("to"))


def _normalize_relation_reviews(packages: list[dict[str, Any]]) -> None:
    for pkg in packages:
        contract = _contract(pkg)
        review = pkg.get("relation_review")
        if contract == APPROVED_CONTRACT and not review:
            raise BatchValidationError(
                f"{pkg.get('job_id') or pkg.get('formal_id')} approved 包缺 relation_review.status"
            )
        status = relation_review_status(pkg)
        if status not in REVIEW_STATUSES:
            raise BatchValidationError(f"关系复核状态非法：{status}")
        rels = pkg.get("relation_candidates") or []
        if status == "needs_user":
            raise BatchValidationError(
                f"{pkg.get('job_id') or pkg.get('formal_id')} relation_review=needs_user，不得进入提交批"
            )
        if rels and status != "connected":
            raise BatchValidationError(f"{pkg.get('formal_id')} 有关系边时 relation_review 必须为 connected")
        if not rels and status != "reviewed_no_reliable_edge":
            raise BatchValidationError(
                f"{pkg.get('formal_id')} 无关系边时必须明确 reviewed_no_reliable_edge，不能伪装 connected"
            )
        if contract == APPROVED_CONTRACT:
            if not isinstance(review, dict):
                raise BatchValidationError("approved relation_review 必须是 object")
            basis = str(review.get("basis") or "").strip()
            reviewed_by = str(review.get("reviewed_by") or "").strip()
            if len(basis) < 10 or not reviewed_by:
                raise BatchValidationError("approved relation_review 必须包含充分 basis 与 reviewed_by")
            pkg["relation_review"] = {
                "status": status, "basis": basis, "reviewed_by": reviewed_by,
            }
        else:
            pkg["relation_review"] = {"status": status}


def _stable_source_id(pkg: dict[str, Any]) -> str | None:
    value = str(pkg.get("source_id") or "").strip()
    if not value or value in {"VISUAL_PENDING", "待补充", "未记录"}:
        return None
    return value


def _validate_batch_invariants(packages: list[dict[str, Any]], repo: Repo) -> None:
    contracts = [_validate_contract_identity(pkg) for pkg in packages]
    approved_count = sum(contract == APPROVED_CONTRACT for contract in contracts)
    if approved_count not in {0, len(packages)}:
        raise BatchValidationError("同一批次不得混用 approved 与 legacy package")
    if approved_count == 0:
        if len(packages) != 1:
            raise BatchValidationError("legacy 入口只允许 batch size=1；批量自动 ID 必须走 coordinator approved")
        legacy = packages[0]
        if "auto_id" in legacy:
            raise BatchValidationError("legacy package 不得包含 auto_id 字段")
        if not str(legacy.get("formal_id") or "").strip():
            raise BatchValidationError("legacy package 必须显式提供 formal_id")

    ids: set[str] = set()
    sources: dict[str, str] = {}
    evidence: dict[str, str] = {}
    for pkg, contract in zip(packages, contracts):
        if contract == "question_submission_v1":
            raise BatchValidationError("question_submission_v1 是未审核队列包，必须先转换为 coordinator_approved_intake_v1")
        if contract and contract not in {APPROVED_CONTRACT, "intake_package_v1"}:
            raise BatchValidationError(f"不支持的 package contract：{contract}")
        fid = str(pkg.get("formal_id") or "").strip()
        mode = str(pkg.get("mode") or "new")
        if mode not in {"new", "redo"}:
            raise BatchValidationError(f"不支持的 mode：{mode}")
        if contract == APPROVED_CONTRACT:
            if pkg.get("decision") != "pass":
                raise BatchValidationError(f"{pkg.get('job_id')} approved decision 必须严格为 pass")
            if pkg.get("topic_chain_candidates"):
                raise BatchValidationError(
                    f"{pkg.get('job_id')} 含 topic_chain_candidates；批量引擎尚未将专题链纳入 WAL，"
                    "必须退出本批并进入 independent formal maintenance；除非缺用户事实，否则不得转 NEEDS_USER"
                )
            if mode == "new":
                if pkg.get("auto_id") is not True or pkg.get("formal_id") not in (None, "", fid):
                    # 此时锁内已分配 formal_id；只校验 auto_id。原始 null 由 approval gate 校验。
                    if pkg.get("auto_id") is not True:
                        raise BatchValidationError("approved new 必须 auto_id=true")
            elif pkg.get("auto_id") is not False:
                raise BatchValidationError("approved redo 必须 auto_id=false")
            if mode == "redo" and (pkg.get("relation_candidates") or []):
                raise BatchValidationError("approved redo 不支持关系写入；关系维护必须走独立复核事务")
        if not FORMAL_ID_RE.match(fid):
            raise BatchValidationError(f"正式 ID 格式非法：{fid}")
        if fid in ids:
            raise BatchValidationError(f"批内正式 ID 重复：{fid}")
        ids.add(fid)
        if mode == "new" and fid in repo.master():
            raise BatchValidationError(f"new 正式 ID 已存在：{fid}")
        if mode == "redo" and fid not in repo.master():
            raise BatchValidationError(f"redo 正式 ID 不存在：{fid}")
        if mode == "new":
            sid = _stable_source_id(pkg)
            if sid:
                if sid in sources:
                    raise BatchValidationError(f"批内来源 ID 重复：{sid}（{sources[sid]} / {fid}）")
                sources[sid] = fid
            package_hashes = {str(pkg.get("evidence_sha256") or "").strip().lower()}
            for pointer in pkg.get("evidence") or []:
                if isinstance(pointer, dict):
                    package_hashes.add(str(pointer.get("sha256") or "").strip().lower())
            detail_pointer = pkg.get("detail_card_source")
            if isinstance(detail_pointer, dict):
                package_hashes.add(str(detail_pointer.get("sha256") or "").strip().lower())
            for promotion in pkg.get("asset_promotions") or []:
                if isinstance(promotion, dict):
                    package_hashes.add(str(promotion.get("sha256") or "").strip().lower())
            for ev in package_hashes - {""}:
                if not re.fullmatch(r"[0-9a-f]{64}", ev):
                    raise BatchValidationError(f"{fid} evidence SHA-256 格式非法：{ev}")
                if ev in evidence:
                    raise BatchValidationError(f"批内证据哈希重复：{ev}（{evidence[ev]} / {fid}）")
                evidence[ev] = fid

    base_sources = {
        row.get("来源ID", ""): node_id
        for node_id, row in repo.master().items()
        if row.get("来源ID", "") not in {"", "VISUAL_PENDING", "待补充", "未记录"}
    }
    for sid, fid in sources.items():
        if sid in base_sources:
            raise BatchValidationError(f"来源 ID {sid} 已映射正式节点 {base_sources[sid]}，拒绝重复 new 为 {fid}")

    existing_pairs = {
        tuple(sorted((edge["起点ID"], edge["终点ID"])))
        for edge in repo.edges()
    }
    batch_pairs: dict[tuple[str, str], str] = {}
    visible_ids = set(repo.master()) | ids
    for pkg in packages:
        fid = str(pkg.get("formal_id") or "").strip()
        for rel in pkg.get("relation_candidates") or []:
            frm, to = str(rel.get("from") or "").strip(), str(rel.get("to") or "").strip()
            if not FORMAL_ID_RE.fullmatch(frm) or not FORMAL_ID_RE.fullmatch(to):
                raise BatchValidationError(f"关系端点必须是正式 ID，禁止历史/悬空端点：{frm} ↔ {to}")
            if frm not in visible_ids or to not in visible_ids:
                raise BatchValidationError(f"关系端点不在 base/projected master：{frm} ↔ {to}")
            if frm == to:
                raise BatchValidationError(f"关系禁止自环：{frm}")
            if _contract(pkg) == APPROVED_CONTRACT and fid not in {frm, to}:
                raise BatchValidationError(
                    f"approved 包 {fid} 的每条边必须包含本题，拒绝代写无关边：{frm} ↔ {to}"
                )
            pair = tuple(sorted((frm, to)))
            if pair in existing_pairs:
                raise BatchValidationError(f"无序节点对已有关系边，拒绝同对多边：{pair}")
            if pair in batch_pairs:
                raise BatchValidationError(f"批内无序节点对重复：{pair}（{batch_pairs[pair]} / {fid}）")
            batch_pairs[pair] = fid
            rcode = normalize_tag(str(rel.get("type") or ""))[0]
            strength = str(rel.get("strength") or "").strip()
            reason = str(rel.get("reason") or "").strip()
            if TEMPLATE_ONLY_RELATION_REASON_RE.fullmatch(reason):
                raise BatchValidationError(
                    f"关系原因只是字段模板，拒绝写入：{frm} ↔ {to}；必须给出题目特异的共享机制或依赖"
                )
            # 本地关系规则：R06/R09 默认中，R07/R08 只作弱关联；禁止脚本层放行过强边。
            if (rcode in {"R06", "R09"} and strength == "强") or (
                rcode in {"R07", "R08"} and strength != "弱"
            ):
                raise BatchValidationError(f"关系强度超过 {rcode} 边界：{frm} ↔ {to} 标为 {strength}")


def _preflight_all(
    packages: list[dict[str, Any]],
    repo: Repo,
    details_root: Path,
    projected_detail_targets: set[Path],
) -> list[str]:
    new_pkgs = {
        str(pkg["formal_id"]): pkg
        for pkg in packages
        if str(pkg.get("mode") or "new") == "new"
    }
    warnings: list[str] = []
    failures: list[str] = []
    projected_repo = ProjectedRepo(repo, new_pkgs)
    for pkg in packages:
        fid = str(pkg.get("formal_id") or "")
        report = run_preflight(pkg, projected_repo, details_root)
        for level, check, message in report.items:
            if level == "FAIL":
                failures.append(f"{fid} [{check}] {message}")
            elif level == "WARN":
                # coordinator auto-ID 在同批 projected master 上会让旧 preflight 的
                # suggest_id 产生假警告；详情卡也尚在 WAL staging，尚未发布到磁盘。
                if _contract(pkg) == APPROVED_CONTRACT and pkg.get("auto_id") is True:
                    if check == "ID顺序":
                        continue
                    detail_target = (details_root / "cards" / f"{fid}.md").resolve()
                    if check == "详情卡路径" and detail_target in projected_detail_targets:
                        continue
                warnings.append(f"{fid} [{check}] {message}")
    if failures:
        raise BatchValidationError("批次 preflight 未通过：\n- " + "\n- ".join(failures))
    return warnings


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _canonical_existing_file(path_value: Any, parent: Path, label: str) -> Path:
    raw = Path(str(path_value or "")).expanduser()
    if not raw.is_absolute():
        raise BatchValidationError(f"{label} 必须是绝对路径：{raw}")
    if any(part in {".", ".."} for part in raw.parts):
        raise BatchValidationError(f"{label} 不得含 . 或 .. 路径片段：{raw}")
    parent = parent.resolve()
    try:
        resolved = raw.resolve(strict=True)
    except FileNotFoundError as exc:
        raise BatchValidationError(f"{label} 文件不存在：{raw}") from exc
    if not _is_within(resolved, parent) or not resolved.is_file():
        raise BatchValidationError(f"{label} 不是 {parent} 内普通文件：{resolved}")
    # macOS 的 /var 本身会规范化成 /private/var，不能把系统级别名误判成
    # job 内逃逸；只检查从 canonical parent 往下的调用方原始路径组件。
    cursor = raw
    below_parent: list[Path] = []
    while cursor != cursor.parent:
        try:
            if cursor.resolve(strict=True) == parent:
                break
        except FileNotFoundError:
            pass
        below_parent.append(cursor)
        cursor = cursor.parent
    else:
        below_parent = [raw]
    for component in below_parent:
        if component.is_symlink():
            raise BatchValidationError(f"{label} 拒绝符号链接路径：{component}")
    return resolved


def _canonical_job_inbox(details_root: Path, job_id: str, label: str) -> Path:
    """Return the canonical, direct ``inbox/{job_id}`` directory.

    Evidence-file checks alone are insufficient: resolving the job directory
    first would otherwise hide a symlink at ``inbox/{job_id}``.  The raw job
    directory must therefore be a real directory whose canonical parent is the
    canonical inbox root and whose single relative component is exactly the
    validated job id.
    """
    if not JOB_ID_RE.fullmatch(job_id):
        raise BatchValidationError(f"{label} job_id 非法：{job_id!r}")
    raw_inbox = details_root / "inbox"
    if raw_inbox.is_symlink():
        raise BatchValidationError(f"{label} canonical inbox 不能是符号链接：{raw_inbox}")
    try:
        inbox = raw_inbox.resolve(strict=True)
    except FileNotFoundError as exc:
        raise BatchValidationError(f"{label} canonical inbox 不存在：{raw_inbox}") from exc
    if not inbox.is_dir():
        raise BatchValidationError(f"{label} canonical inbox 不是目录：{inbox}")

    raw_job_dir = raw_inbox / job_id
    if raw_job_dir.is_symlink():
        raise BatchValidationError(f"{label} raw job 目录不能是符号链接：{raw_job_dir}")
    try:
        job_dir = raw_job_dir.resolve(strict=True)
    except FileNotFoundError as exc:
        raise BatchValidationError(f"{label} job inbox 不存在：{raw_job_dir}") from exc
    if not job_dir.is_dir():
        raise BatchValidationError(f"{label} job inbox 不是目录：{job_dir}")
    try:
        relative = job_dir.relative_to(inbox)
    except ValueError as exc:
        raise BatchValidationError(
            f"{label} job inbox 解析后逃逸 canonical inbox：{job_dir}"
        ) from exc
    if relative.parts != (job_id,):
        raise BatchValidationError(
            f"{label} job inbox 必须是 canonical inbox 的直接子目录：{job_dir}"
        )
    return job_dir


def _submission_sha256(job: dict[str, Any]) -> str:
    payload = {key: job[key] for key in SUBMISSION_PAYLOAD_KEYS if key in job}
    payload.pop("job_id", None)
    return _sha256_bytes(_canonical_bytes(payload))


def _parse_reviewed_at(value: Any, label: str) -> None:
    text = str(value or "").strip()
    if not text:
        raise BatchValidationError(f"{label} 不能为空")
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BatchValidationError(f"{label} 必须是 ISO-8601 时间：{text}") from exc
    if parsed.tzinfo is None:
        raise BatchValidationError(f"{label} 必须包含时区：{text}")


def _canonical_output_target(path: Path, parent: Path, label: str) -> Path:
    """校验可不存在的发布目标；任何 parent 以下已有 symlink 组件都拒绝。"""
    parent = parent.resolve()
    raw = path
    if not raw.is_absolute():
        raise BatchValidationError(f"{label} 目标必须是绝对路径：{raw}")
    try:
        relative = raw.relative_to(parent)
    except ValueError as exc:
        raise BatchValidationError(f"{label} 目标越过 {parent}：{raw}") from exc
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise BatchValidationError(f"{label} 目标路径非法：{raw}")
    cursor = parent
    for part in relative.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise BatchValidationError(f"{label} 目标拒绝符号链接组件：{cursor}")
    resolved = raw.resolve(strict=False)
    if not _is_within(resolved, parent):
        raise BatchValidationError(f"{label} 目标解析后越界：{resolved}")
    return resolved


def _validate_claim_event_chain(
    runtime_root: Path,
    job_id: str,
    claim: dict[str, Any],
    approved_path: Path,
    approved_sha: str,
    batch_id: str,
    writer_actor: str,
    require_committing: bool,
) -> None:
    events_path = _canonical_existing_file(runtime_root / "events.jsonl", runtime_root, "queue events.jsonl")
    events: list[dict[str, Any]] = []
    for line_no, raw_line in enumerate(events_path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise BatchValidationError(f"queue events.jsonl 第 {line_no} 行损坏") from exc
        if isinstance(event, dict) and event.get("job_id") == job_id:
            events.append(event)
    claimed_index = next((
        index for index in range(len(events) - 1, -1, -1)
        if events[index].get("event_type") == "claimed"
        and events[index].get("event_id") == claim["claim_event_id"]
        and events[index].get("from_state") == "READY"
        and events[index].get("to_state") == "REVIEWING"
        and events[index].get("actor") == claim["worker_id"]
    ), None)
    if claimed_index is None:
        raise BatchValidationError(
            f"{job_id} 找不到与 review_fence claim_event_id 一致的 queue claim 事件"
        )
    reviewed_at = next((
        index for index in range(claimed_index + 1, len(events))
        if events[index].get("from_state") == "REVIEWING"
        and events[index].get("to_state") == "REVIEWED"
        and events[index].get("actor") == claim["worker_id"]
        and (events[index].get("details") or {}).get("metadata", {}).get("approved_package_sha256") == approved_sha
    ), None)
    if reviewed_at is None:
        raise BatchValidationError(f"{job_id} 找不到 claim-fenced REVIEWED approved SHA 事件")
    registered_path = (events[reviewed_at].get("details") or {}).get("metadata", {}).get("approved_package_path")
    if _canonical_existing_file(registered_path, runtime_root / "approved", f"{job_id}.event approved path") != approved_path:
        raise BatchValidationError(f"{job_id} REVIEWED 事件 approved path 不一致")
    if require_committing:
        committing = next((
            event for event in events[reviewed_at + 1:]
            if event.get("from_state") == "REVIEWED"
            and event.get("to_state") == "COMMITTING"
            and event.get("actor") == writer_actor
            and (event.get("details") or {}).get("metadata", {}).get("commit_batch_id") == batch_id
        ), None)
        if committing is None:
            raise BatchValidationError(f"{job_id} 找不到当前 writer/batch 的 COMMITTING lease 事件")


def _verify_all_evidence(specs: Any, inbox: Path, label: str) -> None:
    if not isinstance(specs, list) or not specs:
        raise BatchValidationError(f"{label} 必须是非空 evidence 数组")
    for index, spec in enumerate(specs, 1):
        _verified_inbox_source(spec, inbox, f"{label}#{index}")


def _validate_coordinator_batch(
    packages: list[dict[str, Any]],
    manifest_meta: dict[str, Any],
    runtime_root: Path,
    details_root: Path,
    actor: str | None,
    coordinator_generation: int | None,
    idempotency_key: str,
    dry_run: bool,
) -> None:
    """把 approved 包与当前队列/coordinator/commit lease 做一次锁内硬绑定。"""
    if not packages or not all(_contract(pkg) == APPROVED_CONTRACT for pkg in packages):
        return
    if manifest_meta.get("contract") != MANIFEST_CONTRACT:
        raise BatchValidationError(f"approved 自动批必须来自 {MANIFEST_CONTRACT} manifest")
    manifest_schema = manifest_meta.get("schema_version")
    if manifest_schema not in (None, "", MANIFEST_CONTRACT):
        raise BatchValidationError("manifest contract/schema_version 不一致")
    batch_id = str(manifest_meta.get("batch_id") or "").strip()
    if not re.fullmatch(r"BATCH-[A-Za-z0-9][A-Za-z0-9._-]{1,127}", batch_id):
        raise BatchValidationError(f"manifest batch_id 非法：{batch_id!r}")
    if actor is None or actor != str(manifest_meta.get("actor") or "").strip():
        raise BatchValidationError("调用 actor 必须与 manifest.actor 完全一致")
    if coordinator_generation is None or coordinator_generation != manifest_meta.get("coordinator_generation"):
        raise BatchValidationError("调用 coordinator_generation 必须与 manifest 完全一致")
    if str(manifest_meta.get("idempotency_key") or "").strip() != idempotency_key:
        raise BatchValidationError("调用 idempotency_key 必须与 manifest 完全一致")

    manifest_path = _canonical_existing_file(
        manifest_meta.get("input_path"), runtime_root / "manifests", "coordinator manifest"
    )
    if manifest_path.name != f"{batch_id}.json":
        raise BatchValidationError(f"manifest 文件名必须是 {batch_id}.json")

    coordinator_path = _canonical_existing_file(
        runtime_root / "coordinator.json", runtime_root, "coordinator.json"
    )
    coordinator = _read_json_object(coordinator_path)
    if coordinator.get("schema_version") != "coordinator_v1":
        raise BatchValidationError("coordinator.json schema_version 非法")
    if coordinator.get("active") is not True:
        raise BatchValidationError("当前 coordinator 未激活")
    if coordinator.get("generation") != coordinator_generation:
        raise BatchValidationError(
            f"stale coordinator generation：manifest={coordinator_generation} current={coordinator.get('generation')}"
        )

    raw_claims = manifest_meta.get("queue_claims")
    if not isinstance(raw_claims, list) or len(raw_claims) != len(packages):
        raise BatchValidationError("manifest.queue_claims 必须与 packages 一一对应")
    claims: dict[str, dict[str, Any]] = {}
    tokens: set[str] = set()
    for claim in raw_claims:
        if not isinstance(claim, dict):
            raise BatchValidationError("queue_claims 每项必须是 object")
        job_id = str(claim.get("job_id") or "").strip()
        worker_id = str(claim.get("worker_id") or "").strip()
        token = str(claim.get("claim_token") or "").strip()
        claim_event_id = str(claim.get("claim_event_id") or "").strip()
        claimed_at = str(claim.get("claimed_at") or "").strip()
        if (
            not JOB_ID_RE.fullmatch(job_id) or not worker_id or len(token) < 16
            or len(claim_event_id) < 8 or not claimed_at
        ):
            raise BatchValidationError(f"queue claim 字段非法：{claim}")
        _parse_reviewed_at(claimed_at, f"{job_id}.queue_claim.claimed_at")
        if job_id in claims or token in tokens:
            raise BatchValidationError("queue claim 的 job_id/token 必须批内唯一")
        if claim.get("coordinator_generation") != coordinator_generation:
            raise BatchValidationError(f"{job_id} queue claim generation 已过期")
        claims[job_id] = claim
        tokens.add(token)

    package_paths = manifest_meta.get("_package_paths")
    if not isinstance(package_paths, list) or len(package_paths) != len(packages):
        raise BatchValidationError("approved manifest 必须用逐项 package 文件路径，不能内嵌 object")

    expected_jobs: set[str] = set()
    for pkg, package_path_value in zip(packages, package_paths):
        _validate_contract_identity(pkg)
        job_id = str(pkg.get("job_id") or "").strip()
        if not JOB_ID_RE.fullmatch(job_id) or job_id in expected_jobs:
            raise BatchValidationError(f"approved job_id 非法或重复：{job_id!r}")
        expected_jobs.add(job_id)
        claim = claims.get(job_id)
        if claim is None:
            raise BatchValidationError(f"{job_id} 缺唯一 queue claim")

        package_path = _canonical_existing_file(
            package_path_value, runtime_root / "approved", f"{job_id} approved package"
        )
        if package_path.name != f"{job_id}.json":
            raise BatchValidationError(f"approved 文件名必须是 {job_id}.json")
        package_sha = _sha256_file(package_path)
        if _read_json_object(package_path) != pkg:
            raise BatchValidationError(f"{job_id} manifest 内存包与 approved 文件内容不一致")

        job_path = _canonical_existing_file(
            runtime_root / "jobs" / f"{job_id}.json", runtime_root / "jobs", f"{job_id} queue job"
        )
        job = _read_json_object(job_path)
        state = job.get("state")
        allowed_states = {"REVIEWED", "COMMITTING"} if dry_run else {"COMMITTING"}
        if job.get("job_id") != job_id or state not in allowed_states:
            expected = "REVIEWED/COMMITTING" if dry_run else "COMMITTING"
            raise BatchValidationError(f"{job_id} 必须仍处于 {expected}，当前 {state}")
        if job.get("submission_sha256") != _submission_sha256(job):
            raise BatchValidationError(f"{job_id} 持久 submission_sha256 与 job 内容不一致")
        source_sha = str(pkg.get("source_submission_sha256") or "").strip().lower()
        if not SHA256_RE.fullmatch(source_sha) or source_sha != job.get("submission_sha256"):
            raise BatchValidationError(f"{job_id} source_submission_sha256 已过期或非法")
        source_version = pkg.get("source_job_version")
        current_version = job.get("state_version")
        expected_offset = 1 if state == "REVIEWED" else 2
        if (
            isinstance(source_version, bool) or not isinstance(source_version, int)
            or isinstance(current_version, bool) or not isinstance(current_version, int)
            or source_version != current_version - expected_offset
        ):
            raise BatchValidationError(
                f"{job_id} source_job_version stale：approved={source_version} COMMITTING={current_version}"
            )
        if pkg.get("idempotency_key") != job.get("idempotency_key"):
            raise BatchValidationError(f"{job_id} approved idempotency_key 与 source job 不一致")

        registered_approved = _canonical_existing_file(
            job.get("approved_package_path"), runtime_root / "approved",
            f"{job_id}.approved_package_path",
        )
        if registered_approved != package_path:
            raise BatchValidationError(f"{job_id} queue 未登记当前 approved_package_path")
        approved_sha = str(job.get("approved_package_sha256") or "").strip().lower()
        if approved_sha != package_sha:
            raise BatchValidationError(f"{job_id} approved_package_sha256 不匹配")
        if pkg.get("decision") != "pass":
            raise BatchValidationError(f"{job_id} decision 非 pass")
        _parse_reviewed_at(pkg.get("reviewed_at"), f"{job_id}.reviewed_at")
        reviewed_by = str(pkg.get("reviewed_by") or "").strip()
        if reviewed_by != claim["worker_id"]:
            raise BatchValidationError(f"{job_id} reviewed_by 与 queue claim worker 不一致")
        review = pkg.get("relation_review") or {}
        if review.get("reviewed_by") != reviewed_by:
            raise BatchValidationError(f"{job_id} relation_review.reviewed_by 与 reviewer 不一致")
        checks = pkg.get("coordinator_checks")
        if not isinstance(checks, dict):
            raise BatchValidationError(f"{job_id} 缺 coordinator_checks")
        for key, expected in APPROVED_REQUIRED_CHECKS.items():
            if checks.get(key) != expected:
                raise BatchValidationError(f"{job_id} coordinator_checks.{key} 必须为 {expected!r}")

        if state == "COMMITTING":
            lease = job.get("commit_lease")
            if not isinstance(lease, dict):
                raise BatchValidationError(f"{job_id} 缺 commit_lease")
            if job.get("commit_batch_id") != batch_id or lease.get("batch_id") != batch_id:
                raise BatchValidationError(f"{job_id} commit_batch lease 与 manifest batch_id 不一致")
            if lease.get("actor") != actor or lease.get("coordinator_generation") != coordinator_generation:
                raise BatchValidationError(f"{job_id} commit lease actor/generation 不一致")
        _validate_claim_event_chain(
            runtime_root, job_id, claim, package_path, package_sha, batch_id, actor,
            require_committing=state == "COMMITTING",
        )

        fence = job.get("review_fence")
        if not isinstance(fence, dict):
            raise BatchValidationError(f"{job_id} 缺持久 review_fence")
        token_sha = _sha256_bytes(str(claim["claim_token"]).encode("utf-8"))
        expected_fence = {
            "worker_id": claim["worker_id"],
            "claim_token_sha256": token_sha,
            "coordinator_generation": coordinator_generation,
            "claim_event_id": claim.get("claim_event_id"),
            "claimed_at": claim.get("claimed_at"),
        }
        for key, expected in expected_fence.items():
            if expected in (None, "") or fence.get(key) != expected:
                raise BatchValidationError(
                    f"{job_id} manifest claim 与 review_fence.{key} 不一致"
                )

        inbox = _canonical_job_inbox(details_root, job_id, f"{job_id}.inbox")
        _verify_all_evidence(job.get("evidence"), inbox, f"{job_id}.source_evidence")
        if "evidence" in pkg:
            _verify_all_evidence(pkg.get("evidence"), inbox, f"{job_id}.approved_evidence")

        mode = str(pkg.get("mode") or "new")
        if mode == "new":
            if pkg.get("auto_id") is not True or pkg.get("formal_id") not in (None, ""):
                raise BatchValidationError(f"{job_id} approved new 必须 auto_id=true/formal_id=null")
        elif mode == "redo":
            if pkg.get("auto_id") is not False or not FORMAL_ID_RE.fullmatch(str(pkg.get("formal_id") or "")):
                raise BatchValidationError(f"{job_id} approved redo 必须 auto_id=false 且显式 formal_id")
        else:
            raise BatchValidationError(f"{job_id} mode 非法：{mode}")

    if expected_jobs != set(claims):
        raise BatchValidationError("manifest.queue_claims 含多余或缺失 job")


def _verified_inbox_source(spec: Any, inbox: Path, label: str) -> tuple[Path, bytes]:
    if not isinstance(spec, dict):
        raise BatchValidationError(f"{label} 必须是 {{path/source, sha256}} object")
    raw_path = spec.get("path") or spec.get("source")
    expected = str(spec.get("sha256") or "").strip().lower()
    if not raw_path or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise BatchValidationError(f"{label} 缺合法 path/source 或 sha256")
    path = _canonical_existing_file(raw_path, inbox, label)
    data = path.read_bytes()
    actual = _sha256_bytes(data)
    if actual != expected:
        raise BatchValidationError(f"{label} SHA-256 不一致：期望 {expected}，实际 {actual}")
    return path, data


def _details_projection(
    packages: list[dict[str, Any]], details_root: Path
) -> tuple[dict[Path, bytes], list[str]]:
    """只复制 inbox 证据；不删除、不移动原件。"""
    projected: dict[Path, bytes] = {}
    logs: list[str] = []
    for pkg in packages:
        if str(pkg.get("mode") or "new") != "new":
            continue
        fid = str(pkg["formal_id"])
        contract = _contract(pkg)
        target_card = _canonical_output_target(
            details_root / "cards" / f"{fid}.md", details_root / "cards",
            f"{fid}.details_card",
        )
        source_spec = pkg.get("detail_card_source")
        if contract == APPROVED_CONTRACT:
            job_id = str(pkg.get("job_id") or "").strip()
            if not job_id:
                raise BatchValidationError(f"{fid} approved new 包缺 job_id，无法验证 details inbox 边界")
            if job_id in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9._-]+", job_id):
                raise BatchValidationError(f"job_id 含不安全字符：{job_id}")
            inbox = _canonical_job_inbox(details_root, job_id, f"{fid}.inbox")
            if source_spec:
                _, data = _verified_inbox_source(source_spec, inbox, f"{fid}.detail_card_source")
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise BatchValidationError(f"{fid} detail_card_source 不是 UTF-8 Markdown") from exc
                text = text.replace("{{FORMAL_ID}}", fid).replace("__FORMAL_ID__", fid)
                if len(text.strip()) < 20:
                    raise BatchValidationError(f"{fid} 详情卡投影内容为空或过短")
                data = text.encode("utf-8")
                if target_card.exists() and _sha256_file(target_card) != _sha256_bytes(data):
                    raise BatchValidationError(f"details 详情卡已存在且内容不同，拒绝覆盖：{target_card}")
                projected[target_card] = data
                logs.append(f"details：投影 cards/{fid}.md（inbox 原件保留）")
            elif not target_card.is_file():
                raise BatchValidationError(f"{fid} approved new 包无法生成详情卡：缺 detail_card_source 且目标不存在")

            for index, promotion in enumerate(pkg.get("asset_promotions") or [], 1):
                source, data = _verified_inbox_source(
                    promotion, inbox, f"{fid}.asset_promotions#{index}"
                )
                rel_text = str(promotion.get("relative_path") or source.name)
                rel = Path(rel_text)
                if rel.is_absolute() or ".." in rel.parts or not rel.parts:
                    raise BatchValidationError(f"asset relative_path 非法：{rel_text}")
                target = _canonical_output_target(
                    details_root / "assets" / fid / rel,
                    details_root / "assets" / fid,
                    f"{fid}.asset_promotions#{index}",
                )
                if target in projected:
                    raise BatchValidationError(f"批内 asset 目标重复：{target}")
                if target.exists() and _sha256_file(target) != _sha256_bytes(data):
                    raise BatchValidationError(f"details asset 已存在且内容不同，拒绝覆盖：{target}")
                projected[target] = data
                logs.append(f"details：投影 assets/{fid}/{rel.as_posix()}（inbox 原件保留）")

            tail = f"详情库：obsidian://open?vault=kaoyan-408-details&file=cards%2F{fid}"
            detail_entry = str(pkg.get("detail_entry") or "").strip().rstrip("；;")
            if "kaoyan-408-details" not in detail_entry:
                pkg["detail_entry"] = f"{detail_entry}；{tail}" if detail_entry else tail
        elif source_spec:
            raise BatchValidationError("旧版 package 不允许直接提升 inbox 详情证据；请先转换为 coordinator approved 包")
    return projected, logs


def _redo_semantic_duplicates(packages: list[dict[str, Any]], repo: Repo) -> tuple[list[dict[str, Any]], list[str]]:
    """即使调用方丢失 receipt，完全相同的 redo 也不会再次 +1。"""
    existing: set[tuple[str, str, str]] = set()
    lines = read_text(repo.path(REDO_FILE)).splitlines()
    for _, cells in iter_table_rows(lines, "日期"):
        if len(cells) == len(REDO_COLS):
            row = dict(zip(REDO_COLS, cells))
            existing.add((row["日期"], row["ID"], row["错误记录"]))
    kept: list[dict[str, Any]] = []
    logs: list[str] = []
    seen = set(existing)
    for pkg in packages:
        if str(pkg.get("mode") or "new") != "redo":
            kept.append(pkg)
            continue
        key = (
            str(pkg.get("latest_review_date") or ""),
            str(pkg.get("formal_id") or ""),
            str(pkg.get("latest_error_record") or ""),
        )
        if key in seen:
            master_row = repo.master().get(key[1], {})
            current_tags = set(normalize_tag(tag)[0] for tag in re.split(r"[；;,，]", master_row.get("错因标签", "")) if tag.strip())
            requested_tags = set(normalize_tag(tag)[0] for tag in (pkg.get("error_tags") or []))
            if requested_tags - current_tags:
                correction = copy.deepcopy(pkg)
                correction["_semantic_duplicate_tag_correction"] = True
                kept.append(correction)
                logs.append(
                    f"redo 语义记录已存在：{key[1]} {key[0]}，仅合并新增错因标签，不追加记录/错误次数"
                )
                continue
            logs.append(f"redo 幂等跳过：{key[1]} {key[0]} 已有相同错误记录且无标签修正")
            continue
        seen.add(key)
        kept.append(pkg)
    return kept, logs


def _receipt_path(runtime_root: Path, repo_root: Path, idempotency_key: str) -> Path:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return runtime_root / "receipts" / _repo_key(repo_root) / f"{digest}.json"


def _transaction_root(runtime_root: Path, repo_root: Path) -> Path:
    return runtime_root / "transactions" / _repo_key(repo_root)


def _journal_path(txdir: Path) -> Path:
    return txdir / "journal.json"


def _write_journal(txdir: Path, journal: dict[str, Any]) -> None:
    journal["updated_at"] = _utc_now()
    _atomic_json(_journal_path(txdir), journal)


def _receipt_from_journal(journal: dict[str, Any], journal_path: Path, runtime_root: Path) -> Path:
    repo_root = Path(journal["repo_root"])
    path = _receipt_path(runtime_root, repo_root, journal["idempotency_key"])
    receipt = {
        "schema": "intake_batch_receipt_v1",
        "status": "COMMITTED",
        "idempotency_key": journal["idempotency_key"],
        "payload_sha256": journal["payload_sha256"],
        "transaction_id": journal["transaction_id"],
        "repo_root": journal["repo_root"],
        "committed_at": journal.get("committed_at") or journal.get("updated_at"),
        "items": journal.get("result_items", []),
        "changed_files": [item["target"] for item in journal.get("files", [])],
        "journal": str(journal_path),
        "actor": journal.get("actor"),
        "coordinator_generation": journal.get("coordinator_generation"),
        "commit_batch_id": journal.get("commit_batch_id"),
        "manifest": journal.get("manifest"),
        "queue_claims": journal.get("queue_claims") or [],
        "recovery": journal.get("recovery"),
    }
    if path.exists():
        current = _read_json_object(path)
        if current.get("payload_sha256") != receipt["payload_sha256"]:
            raise BatchError(f"幂等键 receipt 内容冲突：{journal['idempotency_key']}")
    else:
        _atomic_json(path, receipt)
    return path


def _rollback_receipt_from_journal(
    journal: dict[str, Any], journal_path: Path, runtime_root: Path
) -> Path:
    """生成不占用主幂等键的 WAL rollback 凭证，供队列精确恢复 COMMITTING。"""
    repo_root = Path(journal["repo_root"])
    path = (
        runtime_root / "receipts" / _repo_key(repo_root)
        / f"recovery-{journal['transaction_id']}-rollback.json"
    )
    recovery = journal.get("recovery") or {
        "action": "roll_back", "recovered_at": journal.get("rolled_back_at") or _utc_now()
    }
    receipt = {
        "schema": "intake_batch_receipt_v1",
        # 队列把 status=COMMITTED 解释为“恢复决定已持久化”；实际结果由 recovery.action 区分。
        "status": "COMMITTED",
        "idempotency_key": journal["idempotency_key"],
        "payload_sha256": journal["payload_sha256"],
        "transaction_id": journal["transaction_id"],
        "repo_root": journal["repo_root"],
        "committed_at": journal.get("rolled_back_at") or journal.get("updated_at"),
        "items": journal.get("result_items", []),
        "changed_files": [],
        "journal": str(journal_path),
        "actor": journal.get("actor"),
        "coordinator_generation": journal.get("coordinator_generation"),
        "commit_batch_id": journal.get("commit_batch_id"),
        "manifest": journal.get("manifest"),
        "queue_claims": journal.get("queue_claims") or [],
        "recovery": recovery,
    }
    if path.exists():
        current = _read_json_object(path)
        if current.get("payload_sha256") != receipt["payload_sha256"]:
            raise BatchError(f"WAL rollback receipt 内容冲突：{path}")
    else:
        _atomic_json(path, receipt)
    return path


def _target_hash(path: Path) -> str | None:
    return _sha256_file(path) if path.exists() else None


def _publish_staged(item: dict[str, Any], txdir: Path) -> None:
    operation = item.get("operation", "replace")
    if operation == "delete":
        if item.get("after_sha256") is not None or item.get("stage") is not None:
            raise BatchError(f"WAL delete 元数据非法：{item.get('target')}")
        target = Path(item["target"])
        target.unlink()
        _fsync_dir(target.parent)
        return
    if operation != "replace":
        raise BatchError(f"WAL operation 非法：{operation!r}")
    stage = txdir / item["stage"]
    if not stage.is_file() or _sha256_file(stage) != item["after_sha256"]:
        raise BatchError(f"WAL stage 缺失或损坏：{stage}")
    _atomic_write(Path(item["target"]), stage.read_bytes())


def _validate_journal_targets(journal: dict[str, Any]) -> None:
    roots = [Path(value).resolve() for value in journal.get("allowed_roots") or [journal["repo_root"]]]
    repo_root = Path(journal["repo_root"]).resolve()
    if repo_root not in roots:
        raise BatchError("WAL allowed_roots 缺正式 repo_root")
    for item in journal.get("files") or []:
        raw_target = Path(item.get("target") or "")
        accepted = False
        for root in roots:
            try:
                raw_target.relative_to(root)
            except ValueError:
                continue
            try:
                _canonical_output_target(raw_target, root, "WAL target")
            except BatchValidationError as exc:
                raise BatchError(str(exc)) from exc
            accepted = True
            break
        if not accepted:
            raise BatchError(f"WAL 目标越过允许根目录：{raw_target}")


def _roll_forward(txdir: Path, journal: dict[str, Any], runtime_root: Path) -> Path:
    _validate_journal_targets(journal)
    journal["state"] = "publishing"
    _write_journal(txdir, journal)
    for item in journal["files"]:
        target = Path(item["target"])
        current = _target_hash(target)
        if current == item["after_sha256"]:
            item["published"] = True
            _write_journal(txdir, journal)
            continue
        if current != item["before_sha256"]:
            raise BatchError(
                f"WAL 恢复冲突：{target} 当前哈希既非 before 也非 after；拒绝覆盖人工变更"
            )
        _publish_staged(item, txdir)
        if _target_hash(target) != item["after_sha256"]:
            raise BatchError(f"WAL 发布后哈希不一致：{target}")
        item["published"] = True
        _write_journal(txdir, journal)
    journal["state"] = "committed"
    journal["committed_at"] = _utc_now()
    _write_journal(txdir, journal)
    return _receipt_from_journal(journal, _journal_path(txdir), runtime_root)


def _restore_before(item: dict[str, Any], txdir: Path) -> None:
    target = Path(item["target"])
    current = _target_hash(target)
    if current == item["before_sha256"]:
        return
    if current != item["after_sha256"]:
        raise BatchError(f"WAL 回滚冲突：{target} 已出现事务外修改")
    if item["before_sha256"] is None:
        target.unlink(missing_ok=True)
        _fsync_dir(target.parent)
        return
    backup = txdir / item["backup"]
    if not backup.is_file() or _sha256_file(backup) != item["before_sha256"]:
        raise BatchError(f"WAL backup 缺失或损坏：{backup}")
    _atomic_write(target, backup.read_bytes())


def _roll_back(txdir: Path, journal: dict[str, Any]) -> None:
    _validate_journal_targets(journal)
    journal["state"] = "rolling_back"
    _write_journal(txdir, journal)
    for item in reversed(journal["files"]):
        _restore_before(item, txdir)
        item["published"] = False
        _write_journal(txdir, journal)
    journal["state"] = "rolled_back"
    journal["rolled_back_at"] = _utc_now()
    _write_journal(txdir, journal)


def recover_transactions(repo_root: Path, runtime_root: Path) -> list[dict[str, str]]:
    """调用方必须已持有 repo lock。默认 roll-forward；已进入回滚则完成回滚。"""
    recovered: list[dict[str, str]] = []
    root = _transaction_root(runtime_root, repo_root)
    if not root.is_dir():
        return recovered
    for jpath in sorted(root.glob("*/journal.json")):
        journal = _read_json_object(jpath)
        if Path(journal.get("repo_root", "")).resolve() != repo_root.resolve():
            continue
        state = journal.get("state")
        txdir = jpath.parent
        if state == "committed":
            expected = _receipt_path(runtime_root, repo_root, journal["idempotency_key"])
            if expected.exists():
                continue
            receipt = _receipt_from_journal(journal, jpath, runtime_root)
            recovered.append({
                "transaction_id": journal["transaction_id"], "action": "receipt",
                "receipt": str(receipt),
            })
        elif state in {"prepared", "publishing"}:
            journal["recovery"] = {"action": "roll_forward", "recovered_at": _utc_now()}
            receipt = _roll_forward(txdir, journal, runtime_root)
            recovered.append({
                "transaction_id": journal["transaction_id"], "action": "roll_forward",
                "receipt": str(receipt),
            })
        elif state in {"rolling_back", "rollback_required"}:
            journal["recovery"] = {"action": "roll_back", "recovered_at": _utc_now()}
            _roll_back(txdir, journal)
            receipt = _rollback_receipt_from_journal(journal, jpath, runtime_root)
            recovered.append({
                "transaction_id": journal["transaction_id"], "action": "roll_back",
                "receipt": str(receipt),
            })
        elif state == "rolled_back" and journal.get("recovery", {}).get("action") == "roll_back":
            expected = (
                runtime_root / "receipts" / _repo_key(repo_root)
                / f"recovery-{journal['transaction_id']}-rollback.json"
            )
            if expected.exists():
                continue
            receipt = _rollback_receipt_from_journal(journal, jpath, runtime_root)
            recovered.append({
                "transaction_id": journal["transaction_id"], "action": "roll_back",
                "receipt": str(receipt),
            })
        elif state not in TERMINAL_WAL_STATES:
            raise BatchError(f"未知 WAL 状态 {state}：{jpath}")
    return recovered


def _assert_dry_run_wal_clean(repo_root: Path, runtime_root: Path) -> None:
    """Read-only WAL gate for shadow runs.

    A dry-run must never become an implicit recovery command.  Any journal that
    needs roll-forward/rollback, or whose terminal recovery receipt is missing,
    is a hard stop until the operator explicitly runs ``--recover-only``.
    """
    root = _transaction_root(runtime_root, repo_root)
    if not root.is_dir():
        return
    issues: list[str] = []
    for jpath in sorted(root.glob("*/journal.json")):
        journal = _read_json_object(jpath)
        if Path(journal.get("repo_root", "")).resolve() != repo_root.resolve():
            continue
        txid = str(journal.get("transaction_id") or jpath.parent.name)
        state = str(journal.get("state") or "")
        if state == "committed":
            receipt = _receipt_path(runtime_root, repo_root, str(journal.get("idempotency_key") or ""))
            if not receipt.is_file():
                issues.append(f"{txid} state=committed 缺 receipt")
        elif state == "rolled_back":
            receipt = (
                runtime_root / "receipts" / _repo_key(repo_root)
                / f"recovery-{txid}-rollback.json"
            )
            if not receipt.is_file():
                issues.append(f"{txid} state=rolled_back 缺 rollback receipt")
        elif state in {"prepared", "publishing", "rolling_back", "rollback_required"}:
            issues.append(f"{txid} state={state} 需要恢复")
        else:
            issues.append(f"{txid} state={state or '<missing>'} 无法安全判定")
    if issues:
        raise BatchError(
            "dry-run 拒绝隐式恢复或补 receipt；请先显式运行 --recover-only：\n- "
            + "\n- ".join(issues)
        )


def _prepare_transaction(
    repo_root: Path,
    runtime_root: Path,
    files: dict[Path, bytes],
    idempotency_key: str,
    payload_sha256: str,
    result_items: list[dict[str, str]],
    allowed_roots: list[Path],
    actor: str | None,
    coordinator_generation: int | None,
    queue_claims: list[dict[str, Any]],
    commit_batch_id: str | None,
    manifest_identity: dict[str, Any] | None,
    payload_identity: dict[str, Any],
    details_root: Path,
    expected_before_hashes: dict[Path, str | None] | None = None,
    delete_targets: set[Path] | None = None,
) -> tuple[Path, dict[str, Any]]:
    # 在创建 WAL 目录前冻结所有 before bytes，并与调用方在投影前记录的
    # base hash 做 CAS。这样影子门禁运行期间出现的外部编辑不会被旧投影覆盖。
    roots = [root.resolve() for root in allowed_roots]

    def canonical_target(raw_target: Path) -> Path:
        for root in roots:
            try:
                raw_target.relative_to(root)
            except ValueError:
                continue
            return _canonical_output_target(raw_target, root, "transaction target")
        raise BatchError(f"事务目标越过允许根目录：{raw_target}")

    canonical_files = {canonical_target(target): data for target, data in files.items()}
    canonical_deletes = {
        canonical_target(target) for target in (delete_targets or set())
    }
    overlap = set(canonical_files) & canonical_deletes
    if overlap:
        raise BatchError(f"同一事务目标不能同时 replace 和 delete：{sorted(map(str, overlap))}")
    transaction_targets = set(canonical_files) | canonical_deletes
    canonical_expected = (
        {canonical_target(target): digest for target, digest in expected_before_hashes.items()}
        if expected_before_hashes is not None
        else None
    )
    if canonical_expected is not None and set(canonical_expected) != transaction_targets:
        raise BatchError("expected_before_hashes 必须精确覆盖全部事务目标")
    frozen_before: dict[Path, bytes | None] = {}
    for target in sorted(transaction_targets, key=str):
        before = target.read_bytes() if target.exists() else None
        before_sha = _sha256_bytes(before) if before is not None else None
        if canonical_expected is not None and before_sha != canonical_expected[target]:
            raise BatchError(f"事务准备前 base hash 已改变：{target}")
        if target in canonical_deletes and before is None:
            raise BatchError(f"delete 目标不存在：{target}")
        frozen_before[target] = before

    txid = f"TXN-{dt.datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:12]}"
    if not runtime_root.exists():
        runtime_root.mkdir(parents=True, exist_ok=True)
        _fsync_dir(runtime_root.parent)
    transactions_dir = runtime_root / "transactions"
    if not transactions_dir.exists():
        transactions_dir.mkdir()
        _fsync_dir(runtime_root)
    transaction_root = _transaction_root(runtime_root, repo_root)
    if not transaction_root.exists():
        transaction_root.mkdir()
        _fsync_dir(transactions_dir)
    txdir = transaction_root / txid
    txdir.mkdir(exist_ok=False)
    _fsync_dir(transaction_root)
    (txdir / "stage").mkdir()
    (txdir / "backup").mkdir()
    _fsync_dir(txdir)
    wal_files: list[dict[str, Any]] = []
    ordered_targets = sorted(transaction_targets, key=str)
    for index, target in enumerate(ordered_targets):
        before = frozen_before[target]
        before_sha = _sha256_bytes(before) if before is not None else None
        operation = "delete" if target in canonical_deletes else "replace"
        if operation == "delete":
            after_sha = None
            stage_rel = None
        else:
            data = canonical_files[target]
            after_sha = _sha256_bytes(data)
            stage_rel = f"stage/{index:04d}.bin"
            _atomic_write(txdir / stage_rel, data)
        backup_rel = None
        if before is not None:
            backup_rel = f"backup/{index:04d}.bin"
            _atomic_write(txdir / backup_rel, before)
        wal_files.append({
            "target": str(target),
            "before_exists": before is not None,
            "before_sha256": before_sha,
            "after_sha256": after_sha,
            "operation": operation,
            "after_exists": operation != "delete",
            "stage": stage_rel,
            "backup": backup_rel,
            "published": False,
        })
    journal: dict[str, Any] = {
        "schema": "intake_batch_wal_v1",
        "transaction_id": txid,
        "repo_root": str(repo_root.resolve()),
        "details_root": str(details_root.resolve()),
        "allowed_roots": [str(root.resolve()) for root in allowed_roots],
        "actor": actor,
        "coordinator_generation": coordinator_generation,
        "commit_batch_id": commit_batch_id,
        "manifest": copy.deepcopy(manifest_identity),
        "queue_claims": queue_claims,
        "state": "prepared",
        "created_at": _utc_now(),
        "idempotency_key": idempotency_key,
        "payload_sha256": payload_sha256,
        "payload_identity": copy.deepcopy(payload_identity),
        "result_items": result_items,
        "files": wal_files,
    }
    _write_journal(txdir, journal)
    return txdir, journal


def _commit_transaction(
    repo_root: Path,
    runtime_root: Path,
    files: dict[Path, bytes],
    idempotency_key: str,
    payload_sha256: str,
    result_items: list[dict[str, str]],
    allowed_roots: list[Path],
    actor: str | None,
    coordinator_generation: int | None,
    queue_claims: list[dict[str, Any]],
    commit_batch_id: str | None,
    manifest_identity: dict[str, Any] | None,
    payload_identity: dict[str, Any],
    details_root: Path,
    crash_after: int | None = None,
    fail_after: int | None = None,
    expected_before_hashes: dict[Path, str | None] | None = None,
    delete_targets: set[Path] | None = None,
) -> tuple[str, Path, list[str]]:
    txdir, journal = _prepare_transaction(
        repo_root, runtime_root, files, idempotency_key, payload_sha256, result_items,
        allowed_roots, actor, coordinator_generation, queue_claims,
        commit_batch_id, manifest_identity, payload_identity, details_root,
        expected_before_hashes, delete_targets,
    )
    _validate_journal_targets(journal)
    journal["state"] = "publishing"
    _write_journal(txdir, journal)
    replacements = 0
    try:
        for item in journal["files"]:
            _validate_journal_targets(journal)
            target = Path(item["target"])
            if _target_hash(target) != item["before_sha256"]:
                raise BatchError(f"发布前 base hash 改变：{target}")
            _publish_staged(item, txdir)
            replacements += 1
            if crash_after is not None and replacements >= crash_after:
                raise SimulatedCrash(f"fault injection after {replacements} os.replace")
            if fail_after is not None and replacements >= fail_after:
                raise RuntimeError(f"recoverable fault injection after {replacements} os.replace")
            item["published"] = True
            _write_journal(txdir, journal)
    except Exception as exc:
        journal["state"] = "rollback_required"
        _write_journal(txdir, journal)
        journal["recovery"] = {"action": "roll_back", "recovered_at": _utc_now()}
        _roll_back(txdir, journal)
        recovery_receipt = _rollback_receipt_from_journal(
            journal, _journal_path(txdir), runtime_root
        )
        raise TransactionRolledBack(
            f"事务发布失败且已完整回滚：{exc}；recovery receipt={recovery_receipt}",
            recovery_receipt,
        ) from exc
    journal["state"] = "committed"
    journal["committed_at"] = _utc_now()
    _write_journal(txdir, journal)
    receipt = _receipt_from_journal(journal, _journal_path(txdir), runtime_root)
    return journal["transaction_id"], receipt, [item["target"] for item in journal["files"]]


def _result_items(packages: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "job_id": str(pkg.get("job_id") or ""),
            "formal_id": str(pkg.get("formal_id") or ""),
            "mode": str(pkg.get("mode") or "new"),
        }
        for pkg in packages
    ]


def _result_from_receipt(path: Path, receipt: dict[str, Any]) -> BatchResult:
    return BatchResult(
        status="ALREADY_COMMITTED",
        idempotency_key=str(receipt["idempotency_key"]),
        transaction_id=str(receipt.get("transaction_id") or "") or None,
        items=list(receipt.get("items") or []),
        receipt_path=str(path),
        changed_files=list(receipt.get("changed_files") or []),
        logs=["幂等 receipt 命中：正式库与 redo 计数均未重复修改"],
    )


def _refresh_bridge(repo_root: Path) -> str | None:
    script = repo_root / "scripts" / "build_obsidian_bridge_snapshot.py"
    if not script.is_file():
        return f"bridge snapshot 脚本不存在，已跳过：{script}"
    result = subprocess.run(
        [sys.executable, str(script), "--repo", str(repo_root)],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        return "bridge snapshot 刷新失败，请锁外手动重试：" + (result.stderr.strip() or result.stdout.strip())[:300]
    return None


def apply_batch(
    packages: list[dict[str, Any]],
    *,
    repo_root: str | Path = ".",
    details_root: str | Path = DETAILS_ROOT_DEFAULT,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    idempotency_key: str | None = None,
    manifest_meta: dict[str, Any] | None = None,
    actor: str | None = None,
    coordinator_generation: int | None = None,
    queue_claims: list[dict[str, Any]] | None = None,
    dry_run: bool = False,
    lock_timeout: float = 30.0,
    refresh_bridge: bool = False,
    _crash_after: int | None = None,
    _fail_after: int | None = None,
) -> BatchResult:
    """执行一个原子 PASS 批；bad item 会让整批在零写入状态失败。"""
    if not packages:
        raise BatchError("批次为空")
    repo_root = Path(repo_root).expanduser().resolve()
    details_root = Path(details_root).expanduser().resolve()
    runtime_root = Path(runtime_root).expanduser().resolve()
    if (repo_root / ".git").exists() and runtime_root != DEFAULT_RUNTIME_ROOT:
        raise BatchValidationError(
            f"生产 Git 仓库的 runtime_root 固定为 {DEFAULT_RUNTIME_ROOT}，拒绝切换到 {runtime_root}"
        )
    manifest_meta = manifest_meta or {}
    explicit_actor = actor
    explicit_generation = coordinator_generation
    actor = str(actor or manifest_meta.get("actor") or "").strip() or None
    raw_generation = coordinator_generation
    if raw_generation is None:
        raw_generation = manifest_meta.get("coordinator_generation")
    if raw_generation is not None:
        try:
            coordinator_generation = int(raw_generation)
        except (TypeError, ValueError) as exc:
            raise BatchValidationError("coordinator_generation 必须是整数") from exc
        if coordinator_generation < 0:
            raise BatchValidationError("coordinator_generation 不能为负数")
    if queue_claims is None:
        queue_claims = manifest_meta.get("queue_claims") or manifest_meta.get("claims") or []
    if not isinstance(queue_claims, list) or any(not isinstance(item, dict) for item in queue_claims):
        raise BatchValidationError("queue_claims 必须是 object 数组")
    queue_claims = copy.deepcopy(queue_claims)
    original = copy.deepcopy(packages)
    if any(_contract(pkg) == APPROVED_CONTRACT for pkg in original):
        if not str(explicit_actor or "").strip() or explicit_generation is None:
            raise BatchValidationError(
                "approved 自动批必须由调用方显式提供 actor 与 coordinator_generation，不能只信 manifest 自报"
            )
    options_identity = {
        "packages": original,
        "details_root": str(details_root),
        "manifest_contract": manifest_meta.get("contract"),
        "batch_id": manifest_meta.get("batch_id"),
        "actor": actor,
        "coordinator_generation": coordinator_generation,
        "queue_claims": queue_claims,
    }
    payload_sha = _sha256_bytes(_canonical_bytes(options_identity))
    single_key = str(original[0].get("idempotency_key") or "") if len(original) == 1 else ""
    idem = str(
        idempotency_key
        or manifest_meta.get("idempotency_key")
        or single_key
        or f"batch-sha256:{payload_sha}"
    ).strip()
    if not idem:
        raise BatchError("idempotency_key 不能为空")

    result: BatchResult | None = None
    with RepoLock(repo_root, runtime_root, lock_timeout, timeout_error=LockTimeout):
        if dry_run:
            _assert_dry_run_wal_clean(repo_root, runtime_root)
            recovered: list[dict[str, str]] = []
        else:
            recovered = recover_transactions(repo_root, runtime_root)
        _validate_coordinator_batch(
            original, manifest_meta, runtime_root, details_root, actor,
            coordinator_generation, idem, dry_run,
        )
        receipt_path = _receipt_path(runtime_root, repo_root, idem)
        if receipt_path.exists():
            receipt = _read_json_object(receipt_path)
            if receipt.get("payload_sha256") != payload_sha:
                raise BatchError(f"idempotency_key 已用于不同 payload：{idem}")
            result = _result_from_receipt(receipt_path, receipt)
            result.logs.extend(
                f"WAL 自动恢复：{item['transaction_id']} {item['action']}" for item in recovered
            )
        else:
            # 锁内重新加载正式库并分配 ID，杜绝两个任务都取 max+1。
            repo = Repo(repo_root)
            work = copy.deepcopy(original)
            _allocate_ids(work, repo)
            _resolve_job_relations(work)
            _normalize_relation_reviews(work)
            _validate_batch_invariants(work, repo)
            details_files, detail_logs = _details_projection(work, details_root)
            warnings = _preflight_all(work, repo, details_root, set(details_files))
            if any(_contract(pkg) == APPROVED_CONTRACT for pkg in work) and warnings:
                raise BatchValidationError(
                    "approved 自动提交仍有 WARN，必须回主任务复核：\n- " + "\n- ".join(warnings)
                )

            active, redo_logs = _redo_semantic_duplicates(work, repo)
            txn = Txn(repo)
            apply_args = SimpleNamespace(
                details_root=str(details_root),
                no_edges=False,
                no_review_unit=False,
            )
            for pkg in active:
                mode = str(pkg.get("mode") or "new")
                if mode == "new":
                    apply_new(txn, pkg, apply_args)
                elif mode == "redo":
                    apply_redo(txn, pkg, apply_args)
                else:
                    raise BatchValidationError(f"不支持的 mode：{mode}")

            target_files: dict[Path, bytes] = {
                (repo_root / rel).resolve(): data for rel, data in txn.rendered_files().items()
            }
            for target, data in details_files.items():
                if target in target_files and target_files[target] != data:
                    raise BatchError(f"事务投影目标冲突：{target}")
                target_files[target] = data
            for target in target_files:
                if not (_is_within(target, repo_root) or _is_within(target, details_root)):
                    raise BatchError(f"事务目标越过 formal/details 根目录：{target}")

            items = _result_items(work)
            logs = [
                *(f"WAL 自动恢复：{item['transaction_id']} {item['action']}" for item in recovered),
                *txn.log,
                *detail_logs,
                *redo_logs,
            ]
            if dry_run:
                txn.commit(True)
                for line in detail_logs:
                    print(f"  - {line}")
                result = BatchResult(
                    status="DRY_RUN",
                    idempotency_key=idem,
                    transaction_id=None,
                    items=items,
                    changed_files=[str(p) for p in sorted(target_files, key=str)],
                    logs=logs,
                    warnings=warnings,
                )
            else:
                manifest_identity: dict[str, Any] | None = None
                if any(_contract(pkg) == APPROVED_CONTRACT for pkg in original):
                    manifest_path = Path(str(manifest_meta.get("input_path") or "")).expanduser().resolve(strict=True)
                    manifest_identity = {
                        "contract": manifest_meta.get("contract"),
                        "path": str(manifest_path),
                        "sha256": _sha256_file(manifest_path),
                    }
                txid, receipt, changed = _commit_transaction(
                    repo_root,
                    runtime_root,
                    target_files,
                    idem,
                    payload_sha,
                    items,
                    [repo_root, details_root],
                    actor,
                    coordinator_generation,
                    queue_claims,
                    str(manifest_meta.get("batch_id") or "").strip() or None,
                    manifest_identity,
                    options_identity,
                    details_root,
                    crash_after=_crash_after,
                    fail_after=_fail_after,
                )
                result = BatchResult(
                    status="COMMITTED",
                    idempotency_key=idem,
                    transaction_id=txid,
                    items=items,
                    receipt_path=str(receipt),
                    changed_files=changed,
                    logs=logs,
                    warnings=warnings,
                )

    assert result is not None
    # subprocess/Obsidian 派生刷新严格在 flock 释放之后；coverage/HTTP/Tutor 由 coordinator 门禁处理。
    if refresh_bridge and not dry_run:
        bridge_warning = _refresh_bridge(repo_root)
        if bridge_warning:
            result.warnings.append(bridge_warning)
        else:
            result.logs.append("bridge snapshot：已在事务锁外整批刷新 1 次")
    return result


def apply_batch_file(path: str | Path, **kwargs) -> BatchResult:
    packages, meta = load_batch_input(path)
    return apply_batch(packages, manifest_meta=meta, **kwargs)


def recover_only(
    *,
    repo_root: str | Path = ".",
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    lock_timeout: float = 30.0,
) -> list[dict[str, str]]:
    repo_root = Path(repo_root).expanduser().resolve()
    runtime_root = Path(runtime_root).expanduser().resolve()
    if (repo_root / ".git").exists() and runtime_root != DEFAULT_RUNTIME_ROOT:
        raise BatchValidationError(
            f"生产 Git 仓库的 runtime_root 固定为 {DEFAULT_RUNTIME_ROOT}，拒绝切换到 {runtime_root}"
        )
    # Repo() 同时校验传入路径确为 408 正式库。
    Repo(repo_root)
    with RepoLock(repo_root, runtime_root, lock_timeout, timeout_error=LockTimeout):
        return recover_transactions(repo_root, runtime_root)


def _print_result(result: BatchResult, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return
    print(f"batch apply：{result.status}")
    print(f"idempotency_key：{result.idempotency_key}")
    if result.transaction_id:
        print(f"transaction_id：{result.transaction_id}")
    for item in result.items:
        prefix = f"{item.get('job_id')} → " if item.get("job_id") else ""
        print(f"  - {prefix}{item.get('formal_id')}（mode={item.get('mode')}）")
    if result.receipt_path:
        print(f"receipt：{result.receipt_path}")
    for warning in result.warnings:
        print(f"[警告] {warning}")


def main() -> int:
    parser = argparse.ArgumentParser(description="408 批量单写 WAL 入库引擎")
    parser.add_argument("input", nargs="?", help="package object/list/manifest JSON")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--details-root", default=DETAILS_ROOT_DEFAULT)
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--idempotency-key")
    parser.add_argument("--actor", help="写入 WAL/receipt 的 coordinator actor 标识")
    parser.add_argument("--coordinator-generation", type=int, help="coordinator 代际 CAS 元数据")
    parser.add_argument("--lock-timeout", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-bridge", action="store_true", help="兼容显式声明；batch 默认从不刷新 bridge")
    parser.add_argument("--recover-only", action="store_true")
    parser.add_argument(
        "--allow-retired-multi-package",
        action="store_true",
        help="显式解锁已退役的多 package CLI，仅供隔离测试/历史恢复演练",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        if args.recover_only:
            recovered = recover_only(
                repo_root=args.repo,
                runtime_root=args.runtime_root,
                lock_timeout=args.lock_timeout,
            )
            if args.json:
                print(json.dumps({"status": "RECOVERED", "transactions": recovered}, ensure_ascii=False, indent=2))
            else:
                print(f"WAL recover 完成：{len(recovered)} 个事务")
                for item in recovered:
                    print(f"  - {item['transaction_id']}：{item['action']}")
            return 0
        if not args.input:
            parser.error("需要 input JSON 或 --recover-only")
        packages, _ = load_batch_input(args.input)
        if len(packages) != 1 and not args.allow_retired_multi_package:
            raise BatchValidationError(
                "多题 CLI 入库已于 2026-07-12 退役；日常请使用 "
                "intake_apply_408.py 一次同步处理一道题。历史隔离演练必须显式传 "
                "--allow-retired-multi-package。"
            )
        result = apply_batch_file(
            args.input,
            repo_root=args.repo,
            details_root=args.details_root,
            runtime_root=args.runtime_root,
            idempotency_key=args.idempotency_key,
            actor=args.actor,
            coordinator_generation=args.coordinator_generation,
            dry_run=args.dry_run,
            lock_timeout=args.lock_timeout,
            refresh_bridge=False,
        )
        _print_result(result, args.json)
        return 0
    except (BatchError, OSError) as exc:
        if args.json:
            payload = {"status": "FAILED", "error": str(exc)}
            if isinstance(exc, TransactionRolledBack):
                payload["recovery_receipt"] = str(exc.receipt_path)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"[错误] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
