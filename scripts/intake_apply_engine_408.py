#!/usr/bin/env python3
"""408 单题入库一键落表。

读取通过 preflight 的 intake 包，一次性更新全部正式表格与安全卡：

new 模式（新题入库）：
  节点总表 / 科目节点库 / 年份索引 / 知识点命中索引 / 错题日期索引 /
  错题复做记录 / 复习单元总表 / 同名安全节点卡片 / 关系边表。

redo 模式（已有节点再次做错）：
  只更新 节点总表最近复做+最近错误记录 / 节点库镜像 / 错题日期索引 /
  追加错题复做记录 / 安全卡用户错误入口 / 复习单元错误次数与日期 /
  命中索引主知识点小节复做日期。不新建节点。

本模块只由 ``nightly_sol_adapter.py`` 和
``intake_batch_apply_408.py`` 内部调用。公开的
``scripts/intake_apply_408.py`` 继续保持退役并 fail closed。

用法：
  python3 scripts/intake_apply_engine_408.py pkg.json [--repo .] [--dry-run]
      [--no-bridge]

事务性：所有文件先在内存中改好，任何一步失败则不写任何文件。
本脚本不写 专题链/、概念词典.md、复盘输出/、历史错题归档/。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from intake_lib_408 import (  # noqa: E402
    DATE_INDEX_FILE, EDGE_FILE, HIT_INDEX_FILE, MASTER_COLS,
    MASTER_FILE, NODE_LIB_FILE, NO_ETAG, ORIGINAL_TRACK_COLS,
    ORIGINAL_TRACK_FILE, REDO_FILE, REVIEW_UNIT_CARD_DIR, REVIEW_UNIT_COLS,
    REVIEW_UNIT_FILE, REVIEW_UNIT_MAPPING_COLS, REVIEW_UNIT_MAPPING_FILE,
    SUBJECT_FULL, YEAR_INDEX_FILE, Repo, build_row, die, find_table,
    normalize_tag, parse_date, read_text, split_multi, split_row,
)
from intake_preflight_408 import DETAILS_ROOT_DEFAULT  # noqa: E402
from generate_morning_review_queue import (  # noqa: E402
    NODE_ID_RE, VALID_REVIEW_STATES,
)


# ---------------------------------------------------------------- 编辑原语

class Txn:
    """纯内存投影：path -> lines。

    正式发布必须交给 ``intake_batch_apply_408`` 的单写者 WAL 事务；本类不再
    直接覆盖仓库文件，避免任意调用方绕过仓库级锁。
    """

    def __init__(self, repo: Repo):
        self.repo = repo
        self.files: dict[str, list[str]] = {}
        self.created: dict[str, list[str]] = {}
        self.log: list[str] = []

    def lines(self, rel: str) -> list[str]:
        if rel not in self.files:
            self.files[rel] = read_text(self.repo.path(rel)).splitlines()
        return self.files[rel]

    def create(self, rel: str, text: str):
        if self.repo.path(rel).exists():
            die(f"{rel} 已存在，拒绝覆盖（redo 请走更新路径）")
        self.created[rel] = text.splitlines()
        self.log.append(f"新建 {rel}")

    def note(self, msg: str):
        self.log.append(msg)

    def rendered_files(self) -> dict[str, bytes]:
        """返回本次投影的最终 UTF-8 文件内容。"""
        overlap = set(self.files) & set(self.created)
        if overlap:
            die(f"事务投影同时修改并创建同一文件：{sorted(overlap)}")
        out: dict[str, bytes] = {}
        for rel, lines in {**self.files, **self.created}.items():
            out[rel] = ("\n".join(lines) + "\n").encode("utf-8")
        return out

    def commit(self, dry_run: bool):
        """兼容 dry-run 日志；禁止直接正式写盘。"""
        if dry_run:
            print("== dry-run，未写盘。将执行以下变更 ==")
            for msg in self.log:
                print(f"  - {msg}")
            return
        die("禁止直接 Txn.commit；请通过 intake_batch_apply_408.py 的单写事务引擎发布")


def append_table_row(txn: Txn, rel: str, first_col: str, row: str, label: str):
    lines = txn.lines(rel)
    h, last = find_table(lines, first_col)
    if h is None:
        die(f"{rel} 中找不到以 {first_col} 开头的表头")
    lines.insert(last + 1, row)
    txn.note(f"{rel}：追加 1 行（{label}）")


def find_row_idx(lines: list[str], first_col: str, key: str) -> int | None:
    h, last = find_table(lines, first_col)
    if h is None:
        return None
    for i in range(h + 2, last + 1):
        cells = split_row(lines[i])
        if cells and cells[0] == key:
            return i
    return None


def merge_etags(existing: str, extra: list[str]) -> str:
    cur = split_multi(existing)
    if extra:
        cur = [t for t in cur if t != NO_ETAG]
        for t in extra:
            if t not in cur:
                cur.append(t)
    return "；".join(cur) if cur else NO_ETAG


def next_day(date_s: str) -> str:
    d = parse_date(date_s)
    return (d + _dt.timedelta(days=1)).isoformat() if d else date_s


# ---------------------------------------------------------------- 命中索引

HIT_SECTION_RE = re.compile(r"^### ([A-Z]{2}\d{2}-\d{2}) (.+)$")


def hit_section_bounds(lines: list[str], code: str):
    """返回 (start, end)：start 为 ### 行下标，end 为下一小节 ### 行或文件尾。"""
    start = None
    for i, ln in enumerate(lines):
        m = HIT_SECTION_RE.match(ln)
        if m and m.group(1) == code:
            start = i
        elif start is not None and m:
            return start, i
    return (start, len(lines)) if start is not None else (None, None)


def insert_sorted_item(lines: list[str], lo: int, hi: int, marker: str, item: str, fid: str):
    """在小节 [lo,hi) 内 marker 子标题下按 ID 排序插入列表项。"""
    mi = None
    for i in range(lo, hi):
        if lines[i].strip() == marker:
            mi = i
            break
    if mi is None:
        die(f"命中索引小节缺少子标题 {marker}")
    item_positions: list[tuple[int, str]] = []
    j = mi + 1
    while j < hi:
        s = lines[j].strip()
        if s.startswith("- "):
            existing_id = s[2:].split("：", 1)[0].strip()
            if existing_id == fid:
                return False  # 已存在
            item_positions.append((j, existing_id))
        elif s.startswith("#### ") or s == "---":
            break
        j += 1
    if item_positions:
        insert_at = next((idx for idx, eid in item_positions if eid > fid),
                         item_positions[-1][0] + 1)
        lines.insert(insert_at, item)
    else:
        # 空列表：插在 marker 后空行之后，并保证与下一子标题/分隔线之间留空行
        insert_at = mi + 1
        if insert_at < len(lines) and lines[insert_at].strip() == "":
            insert_at += 1
        lines.insert(insert_at, item)
        lines.insert(insert_at + 1, "")
    return True


def bump_node_count(lines: list[str], lo: int, hi: int, delta: int = 1):
    for i in range(lo, hi):
        m = re.match(r"^- 节点数量：(\d+)$", lines[i].strip())
        if m:
            lines[i] = f"- 节点数量：{int(m.group(1)) + delta}"
            return
    # 小节缺数量行则补在标题后空行下
    lines.insert(lo + 2, f"- 节点数量：{delta}")


def new_hit_section(code: str, name: str) -> list[str]:
    return [f"### {code} {name}", "", "- 节点数量：0", "",
            "#### 主知识点命中", "", "#### 命中知识点", "",
            "#### 错因触发记录", "", "---", ""]


def ensure_hit_section(txn: Txn, code: str, name: str):
    lines = txn.lines(HIT_INDEX_FILE)
    lo, hi = hit_section_bounds(lines, code)
    if lo is not None:
        return
    insert_at = None
    for i, ln in enumerate(lines):
        m = HIT_SECTION_RE.match(ln)
        if m and m.group(1) > code:
            insert_at = i
            break
    block = new_hit_section(code, name)
    if insert_at is None:
        lines.extend(block)
    else:
        lines[insert_at:insert_at] = block
    txn.note(f"{HIT_INDEX_FILE}：新建小节 {code} {name}")


def ensure_hit_subsection(lines: list[str], lo: int, hi: int, marker: str):
    """旧分节可能缺 ``#### 命中知识点``；在错因块前补齐。"""
    if any(lines[i].strip() == marker for i in range(lo, hi)):
        return
    insert_at = next(
        (i for i in range(lo, hi) if lines[i].strip() in {"#### 错因触发记录", "---"}),
        hi,
    )
    lines[insert_at:insert_at] = [marker, ""]


def section_has_fid(lines: list[str], lo: int, hi: int, fid: str) -> bool:
    pat = re.compile(rf"^-\s*{re.escape(fid)}：")
    return any(pat.match(lines[i].strip()) for i in range(lo, hi))


def hit_index_add(txn: Txn, fid: str, code: str, name: str, is_main: bool,
                  core_point: str, review_date: str, etag_str: str,
                  main_code: str):
    ensure_hit_section(txn, code, name)
    lines = txn.lines(HIT_INDEX_FILE)
    lo, hi = hit_section_bounds(lines, code)
    existed_before = section_has_fid(lines, lo, hi, fid)
    if is_main:
        item = f"- {fid}：{core_point}；最近复做日期：{review_date}。"
        insert_sorted_item(lines, lo, hi, "#### 主知识点命中", item, fid)
    else:
        ensure_hit_subsection(lines, lo, hi, "#### 命中知识点")
        lo, hi = hit_section_bounds(lines, code)
        item = (
            f"- {fid}：{core_point}（命中，主知识点 {main_code}）；"
            f"最近复做日期：{review_date}。"
        )
        insert_sorted_item(lines, lo, hi, "#### 命中知识点", item, fid)
    lo, hi = hit_section_bounds(lines, code)
    insert_sorted_item(lines, lo, hi, "#### 错因触发记录", f"- {fid}：{etag_str}。", fid)
    if not existed_before:
        lo, hi = hit_section_bounds(lines, code)
        bump_node_count(lines, lo, hi, 1)
    txn.note(f"{HIT_INDEX_FILE}：{code} 小节登记 {fid}（{'主' if is_main else '命中'}）")


def hit_index_touch_redo(txn: Txn, fid: str, code: str, review_date: str, etag_str: str | None):
    lines = txn.lines(HIT_INDEX_FILE)
    lo, hi = hit_section_bounds(lines, code)
    if lo is None:
        txn.note(f"[警告] {HIT_INDEX_FILE} 无 {code} 小节，跳过复做日期同步")
        return
    pat = re.compile(rf"^(- {re.escape(fid)}：.*；最近复做日期：)[^。]*(。)$")
    for i in range(lo, hi):
        m = pat.match(lines[i].strip())
        if m:
            lines[i] = f"{m.group(1)}{review_date}{m.group(2)}"
            txn.note(f"{HIT_INDEX_FILE}：{code} 主知识点命中行复做日期 → {review_date}")
    if etag_str:
        pat2 = re.compile(rf"^- {re.escape(fid)}：.*$")
        for i in range(lo, hi):
            if lines[i].strip().startswith("#### 错因触发记录"):
                for j in range(i + 1, hi):
                    if pat2.match(lines[j].strip()):
                        lines[j] = f"- {fid}：{etag_str}。"
                        txn.note(f"{HIT_INDEX_FILE}：{code} 错因触发记录已同步")
                        break
                break


# ---------------------------------------------------------------- 年份索引

def year_index_add(txn: Txn, pkg: dict, fid: str, details_root: Path | None = None):
    year = str(pkg["year"]).strip()
    section = "## 未标明" if year == "UNK" else f"## {year}"
    lines = txn.lines(YEAR_INDEX_FILE)
    sec_idx = None
    for i, ln in enumerate(lines):
        if ln.strip() == section:
            sec_idx = i
            break
    row = build_row([fid, pkg["source_id"], SUBJECT_FULL[pkg["subject"]], pkg["module"],
                     pkg["main_knowledge"], pkg["first_done_date"],
                     pkg["latest_review_date"], master_detail_entry(pkg, fid, txn.repo, details_root)])
    if sec_idx is None:
        # 新年份小节：按年份升序插入（未标明小节保持在最前）
        insert_at = len(lines)
        for i, ln in enumerate(lines):
            m = re.match(r"^## (\d{4})$", ln.strip())
            if m and (year != "UNK") and m.group(1) > year:
                insert_at = i
                break
        block = [section, "",
                 "| ID | 来源ID | 科目 | 模块 | 主知识点 | 首次做题日期 | 最近复做日期 | 详情入口 |",
                 "|---|---|---|---|---|---|---|---|", row, ""]
        lines[insert_at:insert_at] = block
        txn.note(f"{YEAR_INDEX_FILE}：新建 {section} 小节并登记 {fid}")
        return
    h, last = find_table(lines, "ID", sec_idx)
    if h is None:
        die(f"{YEAR_INDEX_FILE} {section} 小节下没有表格")
    lines.insert(last + 1, row)
    txn.note(f"{YEAR_INDEX_FILE}：{section} 小节追加 {fid}")


# ---------------------------------------------------------------- 详情入口 / 安全卡

def details_card_exists(fid: str, details_root: Path) -> bool:
    return (details_root / "cards" / f"{fid}.md").exists()


def master_detail_entry(pkg: dict, fid: str, repo: Repo, details_root: Path | None = None) -> str:
    de = pkg["detail_entry"].strip().rstrip("；;")
    droot = details_root or Path(DETAILS_ROOT_DEFAULT)
    tail = f"详情库：obsidian://open?vault=kaoyan-408-details&file=cards%2F{fid}"
    if details_card_exists(fid, droot) and "kaoyan-408-details" not in de:
        return f"{de}；{tail}"
    return de


SAFE_CARD_TEMPLATE = """# {fid}

## 安全节点卡片

- 正式节点 ID：{fid}
- 来源 ID：{source_id}
- 年份：{year_disp}
- 科目：{subject_full}
- 主模块：{module}
- 主知识点：{main_knowledge}
- 副知识点：{subs}
- 命中知识点：{hits}
- 详情入口：{detail_entry}

## 复做安全信息

- 安全题目摘要：{safe_summary}
- 关键题设参数：{key_parameters}
- 问法类型：{ask_type}
- 用户错误入口：{user_error_entry}
- 复做第一动作：{redo_first_action}
- 核心考点：{core_point}
- 模糊概念：{fuzzy_concepts}
- 错因标签：{etags}
- 关系复核：{relation_review_status}

## 附件登记

- 题图：{att_q}
- 解析图或解析正文：{att_s}
- 作答痕迹：{att_h}
- 可视化详情入口：{att_entry}
- 当前附件来源：{att_src}
- OCR/解析处理：只提取安全题目摘要、关键题设参数、问法类型、错误入口和复做第一动作；不复制完整题干、答案或完整解析
- 复做保护：已隐藏答案、选项字母组合、用户错选和标准解析中的答案结论

## 复做保护

- 复做保护说明：本卡只保留题目骨架、必要题设参数、错误入口和复做动作；不保存完整题干、完整解析、标准答案、错选项、选项字母、图片或手写痕迹。"""


def render_safe_card(pkg: dict, fid: str, repo: Repo, details_root: Path) -> str:
    ar = pkg.get("attachment_registry") or {}
    return SAFE_CARD_TEMPLATE.format(
        fid=fid, source_id=pkg["source_id"],
        year_disp="未标明" if str(pkg["year"]) == "UNK" else pkg["year"],
        subject_full=SUBJECT_FULL[pkg["subject"]], module=pkg["module"],
        main_knowledge=pkg["main_knowledge"],
        subs="；".join(pkg.get("sub_knowledge") or []) or "无",
        hits="；".join(pkg.get("hit_knowledge") or []),
        detail_entry=master_detail_entry(pkg, fid, repo, details_root),
        safe_summary=pkg["safe_summary"], key_parameters=pkg["key_parameters"],
        ask_type=pkg["ask_type"], user_error_entry=pkg["user_error_entry"],
        redo_first_action=pkg["redo_first_action"], core_point=pkg["core_point"],
        fuzzy_concepts=pkg["fuzzy_concepts"], etags="；".join(pkg["error_tags"]),
        relation_review_status=relation_review_status(pkg),
        att_q=ar.get("题图", "未提供"), att_s=ar.get("解析图或解析正文", "未提供"),
        att_h=ar.get("作答痕迹", "未提供"),
        att_entry=ar.get("可视化详情入口", "待补充"),
        att_src=ar.get("当前附件来源", "本次对话附件"),
    )


def relation_review_status(pkg: dict) -> str:
    """兼容旧包并规范化关系复核状态。"""
    review = pkg.get("relation_review")
    if isinstance(review, dict):
        status = str(review.get("status", "")).strip()
    else:
        status = str(review or "").strip()
    if status:
        return status
    return "connected" if (pkg.get("relation_candidates") or []) else "reviewed_no_reliable_edge"


# ---------------------------------------------------------------- review-unit-v2

def _one_line(value: object, fallback: str = "未记录") -> str:
    """生成 Markdown 表格/卡片可安全复用的单行文本。"""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return (text or fallback).replace("|", "｜")


def _yaml_string(value: object) -> str:
    """JSON 字符串是 YAML 1.2 的安全标量，避免模块名中的冒号破坏 frontmatter。"""
    return json.dumps(_one_line(value), ensure_ascii=False)


def _require_exact_table(txn: Txn, rel: str, expected: list[str]) -> tuple[list[str], int, int]:
    lines = txn.lines(rel)
    header, last = find_table(lines, expected[0])
    if header is None:
        die(f"{rel} 中找不到 review-unit-v2 表头 {expected[0]}")
    actual = split_row(lines[header])
    if actual != expected:
        die(
            f"{rel} 表头不是 review-unit-v2：期望 {len(expected)} 列，"
            f"实际 {len(actual)} 列"
        )
    for index in range(header + 2, last + 1):
        cells = split_row(lines[index])
        if cells and len(cells) != len(expected):
            die(
                f"{rel} 第 {index + 1} 行列数损坏："
                f"期望 {len(expected)}，实际 {len(cells)}"
            )
    return lines, header, last


def _append_exact_row(txn: Txn, rel: str, expected: list[str], cells: list[str], label: str):
    if len(cells) != len(expected):
        die(f"{rel} 新行列数错误：期望 {len(expected)}，实际 {len(cells)}")
    lines, _, last = _require_exact_table(txn, rel, expected)
    matches = [
        index for index in range(last + 1)
        if split_row(lines[index]) and split_row(lines[index])[0] == label
    ]
    if matches:
        die(
            f"{rel} 已存在键 {label}，拒绝 new 追加"
            "（可能是孤儿 review-unit surface 冲突）"
        )
    lines.insert(last + 1, build_row([_one_line(cell) for cell in cells], padded=True))
    txn.note(f"{rel}：追加 1 行（{label}）")


def _bounded_int(value: object, default: int, low: int = 1, high: int = 5) -> str:
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        number = default
    return str(number if low <= number <= high else default)


def _optional_bounded_int(value: object, low: int = 1, high: int = 5) -> str:
    """只保留显式合法评分；缺失/非法时保持事实占位。"""
    try:
        number = int(str(value))
    except (TypeError, ValueError):
        return "未记录"
    return str(number) if low <= number <= high else "未记录"


def _normalized_review_date(value: object) -> str:
    raw = _one_line(value)
    return raw if parse_date(raw) else "未记录"


def _original_track_dates(
    value: object,
    as_of: _dt.date | None = None,
) -> tuple[str, str, str]:
    """返回 (最近复做日, 最早允许日, 7天资格)。"""
    raw = _normalized_review_date(value)
    last = parse_date(raw)
    if last is None:
        return "未记录", "未记录", "eligible_unrecorded_date"
    earliest = last + _dt.timedelta(days=7)
    eligibility = "eligible" if earliest <= (as_of or _dt.date.today()) else "protected_until"
    return last.isoformat(), earliest.isoformat(), eligibility


def _track_activation(eligibility: str, earliest: str) -> str:
    if eligibility == "protected_until":
        return f"主机制基线独立成功，且不早于 {earliest}"
    return "主机制基线取得一次无提示独立成功后立即进入原题轨基线"


def _track_qualification(last: str, earliest: str, eligibility: str) -> str:
    if eligibility == "eligible_unrecorded_date":
        return (
            "status=eligible_date_unrecorded_7d_candidate；last_review=未记录；"
            "eligible_now=true；最近复做日期未记录，按本地 7 天规则视为候选，"
            "但低于日期明确者。"
        )
    if eligibility == "eligible":
        return (
            f"status=eligible_7d；last_review={last}；earliest={earliest}；"
            "eligible_now=true；已通过最近复做至少 7 天硬筛选。"
        )
    return (
        f"status=protected_until；last_review={last}；earliest={earliest}；"
        "eligible_now=false；未满 7 天，仅走知识机制轨。"
    )


def _review_unit_texts(pkg: dict) -> tuple[str, str, str]:
    _, knowledge_name = normalize_tag(pkg["main_knowledge"])
    title = f"{knowledge_name or pkg['main_knowledge']} · {_one_line(pkg['ask_type'])}"
    prompt = (
        f"闭卷处理“{_one_line(pkg['ask_type'])}”：先说出第一动作，"
        "再列出必须核验的条件、单位或边界；不得只报结论。"
    )
    feedback = (
        f"首答后核验第一动作：{_one_line(pkg['redo_first_action'])}；"
        f"关键条件核验：{_one_line(pkg['key_parameters'])}；"
        "只核验推理步骤与边界，不展示原题结论、选项或既往错选。"
    )
    return _one_line(title), _one_line(prompt), _one_line(feedback)


def _render_review_unit_card(pkg: dict, fid: str, unit_id: str) -> str:
    title, prompt, feedback = _review_unit_texts(pkg)
    return "\n".join([
        "---",
        "schema: review_unit_card_v2",
        f"review_unit_id: {_yaml_string(unit_id)}",
        f"subject: {_yaml_string(SUBJECT_FULL[pkg['subject']])}",
        f"module: {_yaml_string(pkg['module'])}",
        'schedule_state: "uncalibrated_baseline_due"',
        "---",
        "",
        f"# {unit_id} · {title}",
        "",
        f"- 覆盖正式节点：{fid}",
        "- 当前状态：uncalibrated_baseline_due",
        "",
        "## 闭卷提取",
        "",
        prompt,
        "",
        "先独立作答并记录置信度、反应时与是否使用提示，再展开下方自查口径。",
        "",
        "> [!answer]- 派生自查口径（首答后展开）",
        f"> {feedback}",
        ">",
        "> 达标标准：第一动作、关键中间状态或单位、边界解释均完整，且不是只报结论。",
        "",
        "## 迁移生成规则",
        "",
        "更换表面情境、参数或问法，但保持同一知识机制；先说明识别信号与第一动作，再完成边界判断。",
        "",
        "## 调度与证据边界",
        "",
        "- 本单元先进入闭卷基线；未经用户明确给出 0–5 分，不更新正式掌握度。",
        "- 原题复做轨另行执行 7 天外硬筛选；本卡不保存完整题干、答案、选项或解析。",
        "",
    ])


def _review_unit_v2_rows(
    pkg: dict,
    fid: str,
    detail_entry: str,
    as_of: _dt.date | None = None,
) -> tuple[str, list[str], list[str], list[str]]:
    unit_id = f"RU_{fid}"
    ru = pkg.get("review_unit") or {}
    title, prompt, feedback = _review_unit_texts(pkg)
    difficulty = _one_line(ru.get("难度等级"), "待确认")
    if difficulty not in {"A基础", "B中等", "C提高", "待确认"}:
        difficulty = "待确认"
    first = _normalized_review_date(pkg.get("first_done_date"))
    note = (
        f"来源：{fid}；{_one_line(pkg['latest_error_record'])}；"
        f"详见 [[{REVIEW_UNIT_CARD_DIR}/{unit_id}]]"
    )
    review_row = [
        unit_id, SUBJECT_FULL[pkg["subject"]], pkg["module"], title, "错题机制",
        "uncalibrated_baseline_due", fid, first, "未记录", "未记录", "0", "待评分",
        _bounded_int(ru.get("错误次数"), 1, 1, 999),
        _optional_bounded_int(ru.get("重要程度")), difficulty, prompt, feedback, note,
    ]

    last, earliest, eligibility = _original_track_dates(pkg.get("latest_review_date"), as_of)
    _, main_name = normalize_tag(pkg["main_knowledge"])
    actual_point = f"{main_name or pkg['main_knowledge']}：{_one_line(pkg['ask_type'])}"
    evidence_grade = _one_line(ru.get("证据等级") or "B")
    if evidence_grade not in {"A", "B", "C", "待确认"}:
        evidence_grade = "B"
    mapping_row = [
        fid, SUBJECT_FULL[pkg["subject"]], evidence_grade, actual_point, unit_id, "无",
        "核心机制＋第一动作＋原题轮换", "reviewed", "required",
        _track_qualification(last, earliest, eligibility), "无",
    ]

    source_id = _one_line(pkg.get("source_id"))
    historical = bool(re.fullmatch(r"H(?:DS|CO|OS|CN)_\d{4}", source_id))
    marker, paragraph = ("未提取", "未记录") if historical else ("无", "无")
    full_location = f"节点总表.md；来源ID：{source_id}；{_one_line(detail_entry, '待补充')}"
    year = "未标明" if str(pkg.get("year")) == "UNK" else _one_line(pkg.get("year"), "未标明")
    track_row = [
        fid, unit_id, "awaiting_primary_baseline", eligibility, last, earliest, "未记录",
        source_id, year, marker, paragraph, full_location, detail_entry,
        _track_activation(eligibility, earliest),
        "历史定位字段待补充" if historical else "无",
    ]
    return unit_id, review_row, mapping_row, track_row


def _validate_existing_review_v2_row(cells: list[str], key: str, fid: str):
    """验证已存在的 v2 行可以安全保留，不将真实调度状态重置为新题默认值。"""
    if cells[REVIEW_UNIT_COLS.index("复习单元ID")] != key or key != f"RU_{fid}":
        die(f"{REVIEW_UNIT_FILE} 复习单元端点不一致：期望 RU_{fid}，实际 {key}")

    covered = set(NODE_ID_RE.findall(cells[REVIEW_UNIT_COLS.index("覆盖节点")]))
    if fid not in covered:
        die(f"{REVIEW_UNIT_FILE} 中 {key} 的覆盖节点未包含 {fid}")

    schedule = cells[REVIEW_UNIT_COLS.index("调度状态")]
    if schedule not in VALID_REVIEW_STATES:
        die(f"{REVIEW_UNIT_FILE} 中 {key} 调度状态非法：{schedule}")

    mastery = cells[REVIEW_UNIT_COLS.index("掌握度")]
    if mastery not in {"待评分", "0", "1", "2", "3", "4", "5"}:
        die(f"{REVIEW_UNIT_FILE} 中 {key} 掌握度非法：{mastery}")

    errors = cells[REVIEW_UNIT_COLS.index("错误次数")]
    if errors != "未记录" and not (errors.isdigit() and int(errors) >= 0):
        die(f"{REVIEW_UNIT_FILE} 中 {key} 错误次数非法：{errors}")

    importance = cells[REVIEW_UNIT_COLS.index("重要程度")]
    if importance not in {"未记录", "1", "2", "3", "4", "5"}:
        die(f"{REVIEW_UNIT_FILE} 中 {key} 重要程度非法：{importance}")


def _repair_review_row(
    txn: Txn,
    expected: list[str],
    cells: list[str],
    key: str,
    fid: str,
):
    """只替换/补入 ``key``；允许同一修复批中尚有别的旧 15 列待修行。"""
    lines = txn.lines(REVIEW_UNIT_FILE)
    header, last = find_table(lines, expected[0])
    if header is None or split_row(lines[header]) != expected:
        die(f"{REVIEW_UNIT_FILE} 表头不是 review-unit-v2 18 列")
    matched = [
        index for index in range(header + 2, last + 1)
        if split_row(lines[index]) and split_row(lines[index])[0] == key
    ]
    if len(matched) > 1:
        die(f"{REVIEW_UNIT_FILE} 存在重复 {key}，拒绝自动修复")
    if matched:
        existing = split_row(lines[matched[0]])
        if len(existing) == 15:
            lines[matched[0]] = build_row(
                [_one_line(cell) for cell in cells], padded=True
            )
            txn.note(f"{REVIEW_UNIT_FILE}：{key} 旧 15 列行替换为 review-unit-v2 18 列")
        elif len(existing) == len(expected):
            _validate_existing_review_v2_row(existing, key, fid)
            txn.note(f"{REVIEW_UNIT_FILE}：{key} 已是合法 v2 行，保留现有调度与评分")
        else:
            die(
                f"{REVIEW_UNIT_FILE} 中 {key} 列数损坏："
                f"仅支持旧 15 列或 v2 {len(expected)} 列，实际 {len(existing)}"
            )
    else:
        lines.insert(
            last + 1,
            build_row([_one_line(cell) for cell in cells], padded=True),
        )
        txn.note(f"{REVIEW_UNIT_FILE}：补入 review-unit-v2 行 {key}")


def _ensure_aux_row(
    txn: Txn,
    rel: str,
    expected: list[str],
    cells: list[str],
    key: str,
    endpoint_index: int,
    endpoint: str,
):
    lines, _, last = _require_exact_table(txn, rel, expected)
    matched = [
        index for index in range(last + 1)
        if split_row(lines[index]) and split_row(lines[index])[0] == key
    ]
    if len(matched) > 1:
        die(f"{rel} 存在重复 {key}")
    if matched:
        existing = split_row(lines[matched[0]])
        if existing[endpoint_index] != endpoint:
            die(f"{rel} 中 {key} 已指向 {existing[endpoint_index]}，拒绝改写为 {endpoint}")
        txn.note(f"{rel}：{key} 已存在，保留现有 v2 记录")
        return
    lines.insert(last + 1, build_row([_one_line(cell) for cell in cells], padded=True))
    txn.note(f"{rel}：补入 {key}")


def ensure_review_unit_v2_for_new(
    txn: Txn,
    pkg: dict,
    fid: str,
    *,
    detail_entry: str | None = None,
    repair_existing: bool = False,
    as_of: _dt.date | None = None,
):
    """原子维护 new 节点的四个 review-unit-v2 surface。

    ``repair_existing`` 仅供 receipt-bound postcommit repair：它会把同 ID 的
    旧 15 列 RU 行替换为 18 列，并只补缺 mapping/track/card；不会增加 redo
    次数，也不会触碰节点、索引或关系表。
    """
    rendered_detail = detail_entry or _one_line(pkg.get("detail_entry"), "待补充")
    unit_id, review_row, mapping_row, track_row = _review_unit_v2_rows(
        pkg, fid, _one_line(rendered_detail, "待补充"), as_of
    )
    if repair_existing:
        _repair_review_row(txn, REVIEW_UNIT_COLS, review_row, unit_id, fid)
        _ensure_aux_row(
            txn, REVIEW_UNIT_MAPPING_FILE, REVIEW_UNIT_MAPPING_COLS, mapping_row, fid,
            REVIEW_UNIT_MAPPING_COLS.index("主复习单元ID"), unit_id,
        )
        _ensure_aux_row(
            txn, ORIGINAL_TRACK_FILE, ORIGINAL_TRACK_COLS, track_row, fid,
            ORIGINAL_TRACK_COLS.index("主复习单元ID"), unit_id,
        )
        card_rel = f"{REVIEW_UNIT_CARD_DIR}/{unit_id}.md"
        if not txn.repo.path(card_rel).exists() and card_rel not in txn.created:
            txn.create(card_rel, _render_review_unit_card(pkg, fid, unit_id))
        else:
            txn.note(f"{card_rel}：已存在，repair 保留")
        return

    _append_exact_row(txn, REVIEW_UNIT_FILE, REVIEW_UNIT_COLS, review_row, unit_id)
    _append_exact_row(txn, REVIEW_UNIT_MAPPING_FILE, REVIEW_UNIT_MAPPING_COLS, mapping_row, fid)
    _append_exact_row(txn, ORIGINAL_TRACK_FILE, ORIGINAL_TRACK_COLS, track_row, fid)
    txn.create(f"{REVIEW_UNIT_CARD_DIR}/{unit_id}.md", _render_review_unit_card(pkg, fid, unit_id))


def _find_primary_review_unit(txn: Txn, fid: str) -> str:
    lines, header, last = _require_exact_table(
        txn, REVIEW_UNIT_MAPPING_FILE, REVIEW_UNIT_MAPPING_COLS
    )
    index = _require_unique_row(
        lines, header, last, REVIEW_UNIT_MAPPING_FILE, fid
    )
    cells = split_row(lines[index])
    unit_id = cells[REVIEW_UNIT_MAPPING_COLS.index("主复习单元ID")]
    if not unit_id.startswith("RU_"):
        die(f"{fid} 主复习单元ID 非法：{unit_id}")
    return unit_id


def _require_unique_row(
    lines: list[str],
    header: int,
    last: int,
    rel: str,
    key: str,
) -> int:
    matches = [
        index for index in range(header + 2, last + 1)
        if split_row(lines[index]) and split_row(lines[index])[0] == key
    ]
    if len(matches) != 1:
        die(f"{rel} 中 {key} 必须恰好 1 行，实际 {len(matches)} 行")
    return matches[0]


def _sync_review_unit_card_state(txn: Txn, unit_id: str, state: str):
    rel = f"{REVIEW_UNIT_CARD_DIR}/{unit_id}.md"
    if not txn.repo.path(rel).is_file():
        die(f"{rel} 缺失，拒绝只更新表而留下卡片状态漂移")
    lines = txn.lines(rel)
    if not lines or lines[0].strip() != "---":
        die(f"{rel} 缺合法 YAML frontmatter 起始分隔线")
    try:
        frontmatter_end = next(
            index for index in range(1, len(lines)) if lines[index].strip() == "---"
        )
    except StopIteration:
        die(f"{rel} 缺合法 YAML frontmatter 结束分隔线")
    schedule_lines = [
        index for index in range(1, frontmatter_end)
        if re.match(r"^schedule_state\s*:", lines[index].strip())
    ]
    status_lines = [
        index for index, line in enumerate(lines)
        if line.strip().startswith("- 当前状态：")
    ]
    if len(schedule_lines) != 1 or len(status_lines) != 1:
        die(
            f"{rel} 状态结构异常：schedule_state={len(schedule_lines)}，"
            f"当前状态行={len(status_lines)}"
        )
    lines[schedule_lines[0]] = f"schedule_state: {_yaml_string(state)}"
    lines[status_lines[0]] = f"- 当前状态：{state}"
    txn.note(f"{rel}：调度状态同步为 {state}")


def _touch_review_unit_v2_redo(
    txn: Txn,
    fid: str,
    date: str,
    record: str,
    *,
    as_of: _dt.date | None = None,
):
    unit_id = _find_primary_review_unit(txn, fid)
    lines, header, last_row = _require_exact_table(txn, REVIEW_UNIT_FILE, REVIEW_UNIT_COLS)
    index = _require_unique_row(lines, header, last_row, REVIEW_UNIT_FILE, unit_id)
    cells = split_row(lines[index])
    parsed = parse_date(date)
    if parsed:
        schedule_state = "active_scheduled"
        cells[REVIEW_UNIT_COLS.index("调度状态")] = schedule_state
        cells[REVIEW_UNIT_COLS.index("上次复习日期")] = parsed.isoformat()
        cells[REVIEW_UNIT_COLS.index("下次复习日期")] = (parsed + _dt.timedelta(days=1)).isoformat()
        cells[REVIEW_UNIT_COLS.index("当前间隔天数")] = "1"
    else:
        schedule_state = "uncalibrated_baseline_due"
        cells[REVIEW_UNIT_COLS.index("调度状态")] = schedule_state
        cells[REVIEW_UNIT_COLS.index("上次复习日期")] = "未记录"
        cells[REVIEW_UNIT_COLS.index("下次复习日期")] = "未记录"
        cells[REVIEW_UNIT_COLS.index("当前间隔天数")] = "0"
    cells[REVIEW_UNIT_COLS.index("掌握度")] = "待评分"
    error_index = REVIEW_UNIT_COLS.index("错误次数")
    try:
        cells[error_index] = str(int(cells[error_index]) + 1)
    except ValueError:
        cells[error_index] = "1"
    note_index = REVIEW_UNIT_COLS.index("备注")
    safe_record = _one_line(record)
    if safe_record not in cells[note_index]:
        cells[note_index] = f"{cells[note_index]}；{safe_record}".strip("；")
    lines[index] = build_row(cells, padded=True)
    txn.note(f"{REVIEW_UNIT_FILE}：{unit_id} 错误次数+1，机制轨重置为 D1")
    _sync_review_unit_card_state(txn, unit_id, schedule_state)

    track_lines, track_header, track_last = _require_exact_table(
        txn, ORIGINAL_TRACK_FILE, ORIGINAL_TRACK_COLS
    )
    track_index = _require_unique_row(
        track_lines, track_header, track_last, ORIGINAL_TRACK_FILE, fid
    )
    track = split_row(track_lines[track_index])
    track_unit = track[ORIGINAL_TRACK_COLS.index("主复习单元ID")]
    if track_unit != unit_id:
        die(f"{fid} 映射主单元 {unit_id} 与原题轨端点 {track_unit} 不一致")
    last, earliest, eligibility = _original_track_dates(date, as_of)
    track[ORIGINAL_TRACK_COLS.index("原题轨状态")] = "awaiting_primary_baseline"
    track[ORIGINAL_TRACK_COLS.index("7天资格")] = eligibility
    track[ORIGINAL_TRACK_COLS.index("最近复做日期")] = last
    track[ORIGINAL_TRACK_COLS.index("最早允许日期")] = earliest
    track[ORIGINAL_TRACK_COLS.index("下次原题复做日期")] = "未记录"
    track[ORIGINAL_TRACK_COLS.index("激活条件")] = _track_activation(eligibility, earliest)
    track_lines[track_index] = build_row(track, padded=True)
    txn.note(f"{ORIGINAL_TRACK_FILE}：{fid} 原题轨日期与 7 天资格已重算")


# ---------------------------------------------------------------- new 模式

def apply_new(txn: Txn, pkg: dict, args):
    repo, fid = txn.repo, pkg["formal_id"].strip()
    droot = Path(args.details_root)
    mk_code, mk_name = normalize_tag(pkg["main_knowledge"])
    etag_str = "；".join(pkg["error_tags"])
    hits = list(pkg.get("hit_knowledge") or [])
    if mk_code not in {normalize_tag(h)[0] for h in hits}:
        hits.insert(0, pkg["main_knowledge"])
    pkg["hit_knowledge"] = hits

    detail_entry = master_detail_entry(pkg, fid, repo, droot)

    # 1. 节点总表
    append_table_row(txn, MASTER_FILE, "ID", build_row([
        fid, pkg["source_id"], "未标明" if str(pkg["year"]) == "UNK" else str(pkg["year"]),
        SUBJECT_FULL[pkg["subject"]], pkg["module"], pkg["main_knowledge"],
        "；".join(pkg.get("sub_knowledge") or []), "；".join(hits),
        pkg["question_type"], pkg["core_point"], pkg["fuzzy_concepts"], etag_str,
        pkg["first_done_date"], pkg["latest_review_date"],
        pkg["latest_error_record"], detail_entry,
    ]), fid)

    # 2. 科目节点库镜像
    append_table_row(txn, NODE_LIB_FILE[pkg["subject"]], "ID", build_row([
        fid, pkg["source_id"], "未标明" if str(pkg["year"]) == "UNK" else str(pkg["year"]),
        pkg["main_knowledge"], pkg["core_point"], etag_str, pkg["latest_review_date"],
    ]), fid)

    # 3. 年份索引
    year_index_add(txn, pkg, fid, droot)

    # 4. 知识点命中索引
    _, leaves = repo.kp_tags()
    for item in hits:
        code, _ = normalize_tag(item)
        hit_index_add(txn, fid, code, leaves[code], code == mk_code,
                      pkg["core_point"], pkg["latest_review_date"], etag_str,
                      mk_code)

    # 5. 错题日期索引
    date_source = pkg.get("date_source") or f"用户 {pkg['latest_review_date']} 入库确认"
    append_table_row(txn, DATE_INDEX_FILE, "ID", build_row([
        fid, pkg["source_id"], SUBJECT_FULL[pkg["subject"]], pkg["main_knowledge"],
        pkg["first_done_date"], pkg["latest_review_date"], "否", date_source,
        pkg["latest_error_record"],
    ]), fid)

    # 6. 错题复做记录
    append_table_row(txn, REDO_FILE, "日期", build_row([
        pkg["latest_review_date"], fid, pkg["source_id"],
        pkg.get("redo_action") or "今日入库/复做", pkg["latest_error_record"],
    ]), fid)

    # 7. 复习单元总表
    if not args.no_review_unit:
        ensure_review_unit_v2_for_new(txn, pkg, fid, detail_entry=detail_entry)

    # 8. 安全节点卡片
    txn.create(f"{fid}.md", render_safe_card(pkg, fid, repo, droot))

    # 9. 关系边表
    if not args.no_edges:
        existing = {(e["起点ID"], e["终点ID"], normalize_tag(e["关系类型"])[0]) for e in repo.edges()}
        for r in pkg.get("relation_candidates") or []:
            key = (r["from"], r["to"], normalize_tag(r["type"])[0])
            if key in existing or (key[1], key[0], key[2]) in existing:
                txn.note(f"{EDGE_FILE}：跳过已存在边 {key}")
                continue
            append_table_row(txn, EDGE_FILE, "起点ID", build_row([
                r["from"], r["to"], r["type"], r["strength"], r["reason"], r["priority"],
            ]), f"{r['from']}→{r['to']}")

    for c in pkg.get("topic_chain_candidates") or []:
        txn.note(f"[待人工] 专题链候选 {c}：本脚本不自动写链，请读对应 LINK 文件后按复盘顺序插入 {fid}")


# ---------------------------------------------------------------- redo 模式

def apply_redo(txn: Txn, pkg: dict, args):
    repo, fid = txn.repo, pkg["formal_id"].strip()
    master = repo.master()
    row = master[fid]
    date = pkg["latest_review_date"]
    record = pkg["latest_error_record"]
    add_etags = pkg.get("error_tags") or []
    new_etag_str = merge_etags(row["错因标签"], add_etags)
    etag_changed = new_etag_str != row["错因标签"]
    tag_only_correction = bool(pkg.get("_semantic_duplicate_tag_correction"))

    # 1. 节点总表
    lines = txn.lines(MASTER_FILE)
    idx = find_row_idx(lines, "ID", fid)
    if idx is None:
        die(f"{MASTER_FILE} 中未找到 {fid}")
    cells = split_row(lines[idx])
    cells[MASTER_COLS.index("最近复做日期")] = date
    cells[MASTER_COLS.index("最近错误记录")] = record
    cells[MASTER_COLS.index("错因标签")] = new_etag_str
    lines[idx] = build_row(cells)
    txn.note(f"{MASTER_FILE}：{fid} 最近复做日期/最近错误记录已更新" + ("，错因标签已合并" if etag_changed else ""))

    # 2. 科目节点库镜像
    subject = fid.split("_")[0]
    lines = txn.lines(NODE_LIB_FILE[subject])
    idx = find_row_idx(lines, "ID", fid)
    if idx is not None:
        cells = split_row(lines[idx])
        cells[NODE_LIB_COLS_LAST] = date
        if etag_changed:
            cells[5] = new_etag_str
        lines[idx] = build_row(cells)
        txn.note(f"{NODE_LIB_FILE[subject]}：{fid} 镜像行已同步")
    else:
        txn.note(f"[警告] {NODE_LIB_FILE[subject]} 缺 {fid} 镜像行，未同步（跑 --full 审计确认）")

    # 3. 错题日期索引
    lines = txn.lines(DATE_INDEX_FILE)
    idx = find_row_idx(lines, "ID", fid)
    if idx is None:
        die(f"{DATE_INDEX_FILE} 中未找到 {fid}")
    cells = split_row(lines[idx])
    cells[5] = date
    cells[6] = "否"
    if date not in cells[7]:
        cells[7] = f"{cells[7]}；{date} 复做再次错误"
    cells[8] = record
    lines[idx] = build_row(cells)
    txn.note(f"{DATE_INDEX_FILE}：{fid} 行已更新")

    # 4. 错题复做记录。相同日期/记录的 lost-receipt 重试若只补了新标签，
    # 只同步标签，不得再次追加复做行或增加错误次数。
    if not tag_only_correction:
        append_table_row(txn, REDO_FILE, "日期", build_row([
            date, fid, row["来源ID"], pkg.get("redo_action") or "复做再次错误", record,
        ]), fid)
    else:
        txn.note(f"{REDO_FILE}：{fid} 相同复做记录已存在，仅执行错因标签修正")

    # 5. 安全卡：用户错误入口追加 + 错因标签同步
    card_rel = f"{fid}.md"
    if repo.path(card_rel).exists():
        lines = txn.lines(card_rel)
        for i, ln in enumerate(lines):
            if ln.strip().startswith("- 用户错误入口："):
                if record not in ln:
                    lines[i] = ln.rstrip() + f"{record}" if ln.rstrip().endswith("：") else ln.rstrip() + f" {record}"
                txn.note(f"{card_rel}：用户错误入口已追加本次错误")
            elif ln.strip().startswith("- 错因标签：") and etag_changed:
                lines[i] = f"- 错因标签：{new_etag_str}"
                txn.note(f"{card_rel}：错因标签已同步")
    else:
        txn.note(f"[警告] 安全卡 {card_rel} 缺失，本次未创建；列为修复目标")

    # 6. 复习单元
    if not args.no_review_unit and not tag_only_correction:
        _touch_review_unit_v2_redo(txn, fid, date, record)
    elif tag_only_correction:
        txn.note(f"{REVIEW_UNIT_FILE}：{fid} 标签修正不重复增加错误次数")

    # 7. 命中索引主知识点小节复做日期
    mk_code, _ = normalize_tag(row["主知识点"])
    hit_index_touch_redo(txn, fid, mk_code, date, new_etag_str if etag_changed else None)


NODE_LIB_COLS_LAST = 6  # 节点库镜像最近复做日期列下标


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="408 单题入库一键落表")
    ap.add_argument("package")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--details-root", default=DETAILS_ROOT_DEFAULT)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-bridge", action="store_true", help="兼容显式声明；batch 默认不刷新 bridge")
    ap.add_argument("--runtime-root", default=None, help="批量引擎 WAL/receipt 根（默认 ~/.codex/kaoyan-408-intake）")
    ap.add_argument("--idempotency-key", default=None)
    ap.add_argument("--lock-timeout", type=float, default=30.0)
    ap.add_argument("--json", action="store_true", help="输出机器可读的单题事务结果")
    args = ap.parse_args()

    # 本地导入避免 batch 模块复用本文件的投影函数时形成顶层循环依赖。
    from intake_batch_apply_408 import BatchError, apply_batch_file, load_batch_input

    batch_kwargs = dict(
        repo_root=args.repo,
        details_root=args.details_root,
        dry_run=args.dry_run,
        refresh_bridge=False,
        idempotency_key=args.idempotency_key,
        lock_timeout=args.lock_timeout,
    )
    if args.runtime_root:
        batch_kwargs["runtime_root"] = args.runtime_root
    try:
        packages, _ = load_batch_input(args.package)
        if len(packages) != 1:
            raise BatchError(
                "intake_apply_408.py 是 2026-07-12 起的公开单题同步入口；"
                f"每次必须恰好 1 个 package，当前为 {len(packages)}。"
                "历史多题 manifest 只能用于显式恢复，不能作为日常入库入口。"
            )
        result = apply_batch_file(args.package, **batch_kwargs)
    except (BatchError, OSError) as exc:
        die(str(exc), 1)
    if args.json:
        import json
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return
    if result.status == "ALREADY_COMMITTED":
        print(f"apply 幂等命中：{', '.join(result.formal_ids)}（正式库未重复修改）")
    elif result.status == "DRY_RUN":
        print(f"apply dry-run 完成：{', '.join(result.formal_ids)}（正式库未修改）")
    else:
        for msg in result.logs:
            print(f"  - {msg}")
        print(f"apply 完成：{', '.join(result.formal_ids)}（batch size=1）")
    if result.receipt_path:
        print(f"receipt：{result.receipt_path}")
    for warning in result.warnings:
        print(f"[警告] {warning}")


if __name__ == "__main__":
    main()
