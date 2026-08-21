#!/usr/bin/env python3
"""Build a full-coverage, answer-safe 408 morning review queue.

The default mode is read-only and prints Markdown to stdout.  It never writes
formal review dates, mastery scores, nodes, relations, or ``复盘输出/``.
Every formally due unit is rendered exactly once.  Batches organize the work;
they never cap or drop the due set. ``--write-safe`` may only write a derived
queue into the StudyVault dashboard.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import re
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUBJECTS = ["数据结构", "计算机组成原理", "操作系统", "计算机网络"]
ANCHORS = {1, 3, 7, 14, 30, 60, 90}
NODE_ID_RE = re.compile(r"\b(?:DS|CO|OS|CN)_(?:\d{4}|UNK)_\d{3}\b")
PROTECTED_TERMS_RE = re.compile(
    r"(正确答案|标准答案|错误答案|错选答案|我的答案|我的错选|用户此前答案)"
)
PROTECTED_ANSWER_FRAGMENT_RE = re.compile(
    r"(?:正确答案|标准答案|错误答案|错选答案|我的答案|我的错选|用户此前答案|答案是)"
    r"\s*[:：]?\s*[^/；;|\n]{0,120}"
)
VALID_REVIEW_STATES = {
    "active", "active_due", "active_scheduled",
    "uncalibrated_baseline_due", "recalibration_required_after_rebuild",
    "blocked_needs_user",
}
VALID_ORIGINAL_TRACK_STATES = {
    "awaiting_primary_baseline", "blocked_needs_user",
    "active", "active_due", "active_scheduled",
}
VALID_ORIGINAL_ELIGIBILITY = {"eligible", "eligible_unrecorded_date", "protected_until"}


@dataclasses.dataclass(frozen=True)
class ReviewUnit:
    unit_id: str
    subject: str
    module: str
    title: str
    unit_type: str
    schedule_state: str
    source_nodes: str
    first_date: str
    last_date: str
    next_date: str
    interval: int
    mastery: str
    mistakes: int
    importance: int
    difficulty: str
    method: str
    prompt: str
    feedback: str
    note: str

    @property
    def lane(self) -> str:
        if self.schedule_state.startswith("blocked"):
            return "阻塞"
        if self.mastery in {"0", "1", "2"}:
            return "救援"
        if self.mastery == "待评分" or not self.mastery:
            return "校准"
        if self.mastery == "3":
            return "到期"
        return "维护"

    @property
    def module_key(self) -> str:
        return re.split(r"[；;]", self.module)[0].strip() or self.module


@dataclasses.dataclass(frozen=True)
class OriginalQuestionTrack:
    node_id: str
    primary_unit_id: str
    state: str
    eligibility: str
    last_date: str
    earliest_date: str
    next_date: str
    source_id: str
    year: str
    marker: str
    paragraph: str
    full_location: str
    detail: str
    activation: str
    issues: str

    @property
    def subject(self) -> str:
        return {
            "DS": "数据结构",
            "CO": "计算机组成原理",
            "OS": "操作系统",
            "CN": "计算机网络",
        }.get(self.node_id.split("_", 1)[0], "待确认")


@dataclasses.dataclass(frozen=True)
class DerivedReviewItem:
    key: str
    title: str
    prompt: str
    feedback: str
    source: str


def split_md_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)


def read_markdown_table(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    headers: list[str] | None = None
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip().startswith("|"):
            continue
        cells = split_md_row(line)
        if is_separator(cells):
            continue
        if headers is None:
            headers = cells
            continue
        if len(cells) != len(headers):
            raise ValueError(
                f"malformed markdown table row at {path}:{line_number}: "
                f"expected {len(headers)} cells, got {len(cells)}"
            )
        rows.append(dict(zip(headers, cells)))
    return rows


def parse_date(value: str) -> dt.date | None:
    value = (value or "").strip()
    if value in {"", "未记录", "待补充"}:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def parse_date_strict(value: str, field: str) -> dt.date | None:
    raw = (value or "").strip()
    if raw in {"", "未记录", "待补充"}:
        return None
    try:
        return dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"invalid {field} date: {raw}") from exc


def parse_int(value: str, default: int = 0) -> int:
    match = re.search(r"-?\d+", value or "")
    return int(match.group(0)) if match else default


def load_units(repo: Path) -> list[ReviewUnit]:
    units: list[ReviewUnit] = []
    rows = read_markdown_table(repo / "复习单元总表.md")
    required = {"复习单元ID", "科目", "模块", "内容名称", "类型", "下次复习日期", "掌握度"}
    if rows and not required.issubset(rows[0]):
        raise ValueError(f"review table missing required columns: {sorted(required - set(rows[0]))}")
    for row in rows:
        unit_id = row.get("复习单元ID", "")
        if not unit_id:
            continue
        units.append(
            ReviewUnit(
                unit_id=unit_id,
                subject=row.get("科目", ""),
                module=row.get("模块", ""),
                title=row.get("内容名称", ""),
                unit_type=row.get("类型", ""),
                schedule_state=row.get("调度状态", "active") or "active",
                source_nodes=row.get("覆盖节点", ""),
                first_date=row.get("首次学习日期", ""),
                last_date=row.get("上次复习日期", ""),
                next_date=row.get("下次复习日期", ""),
                interval=parse_int(row.get("当前间隔天数", ""), 1),
                mastery=row.get("掌握度", "") or "待评分",
                mistakes=parse_int(row.get("错误次数", ""), 0),
                importance=parse_int(row.get("重要程度", ""), 0),
                difficulty=row.get("难度等级", ""),
                method=row.get("复习方式", ""),
                prompt=row.get("闭卷问题", ""),
                feedback=row.get("派生自查口径", "") or row.get("复习方式", ""),
                note=row.get("备注", ""),
            )
        )
        if units[-1].schedule_state not in VALID_REVIEW_STATES:
            raise ValueError(
                f"unknown review schedule state for {units[-1].unit_id}: {units[-1].schedule_state}"
            )
    duplicate_ids = sorted(item for item, count in Counter(unit.unit_id for unit in units).items() if count > 1)
    if duplicate_ids:
        raise ValueError(f"duplicate review unit IDs: {duplicate_ids[:20]}")
    return units


def load_original_tracks(repo: Path) -> list[OriginalQuestionTrack]:
    path = repo / "原题复做轨总表.md"
    if not path.exists():
        return []
    result: list[OriginalQuestionTrack] = []
    rows = read_markdown_table(path)
    required = {"正式节点ID", "主复习单元ID", "原题轨状态", "7天资格", "最近复做日期", "最早允许日期"}
    if rows and not required.issubset(rows[0]):
        raise ValueError(f"original-track table missing required columns: {sorted(required - set(rows[0]))}")
    for row in rows:
        node_id = row.get("正式节点ID", "")
        if not NODE_ID_RE.fullmatch(node_id):
            continue
        result.append(
            OriginalQuestionTrack(
                node_id=node_id,
                primary_unit_id=row.get("主复习单元ID", ""),
                state=row.get("原题轨状态", ""),
                eligibility=row.get("7天资格", ""),
                last_date=row.get("最近复做日期", ""),
                earliest_date=row.get("最早允许日期", ""),
                next_date=row.get("下次原题复做日期", ""),
                source_id=row.get("来源ID", ""),
                year=row.get("年份", ""),
                marker=row.get("定位标记", ""),
                paragraph=row.get("段落号", ""),
                full_location=row.get("完整来源位置", ""),
                detail=row.get("详情入口", ""),
                activation=row.get("激活条件", ""),
                issues=row.get("待补事项", ""),
            )
        )
        if result[-1].state not in VALID_ORIGINAL_TRACK_STATES:
            raise ValueError(f"unknown original-track state for {node_id}: {result[-1].state}")
        if result[-1].eligibility not in VALID_ORIGINAL_ELIGIBILITY:
            raise ValueError(
                f"unknown original-track eligibility for {node_id}: {result[-1].eligibility}"
            )
    duplicate_ids = sorted(item for item, count in Counter(track.node_id for track in result).items() if count > 1)
    if duplicate_ids:
        raise ValueError(f"duplicate original-question track IDs: {duplicate_ids[:20]}")
    return result


def due_original_tracks(tracks: list[OriginalQuestionTrack], today: dt.date) -> list[OriginalQuestionTrack]:
    result: list[OriginalQuestionTrack] = []
    for track in tracks:
        if track.state not in {"active", "active_due", "active_scheduled"}:
            continue
        earliest = parse_date_strict(track.earliest_date, f"{track.node_id}.最早允许日期")
        if earliest is not None and earliest > today:
            continue
        last = parse_date_strict(track.last_date, f"{track.node_id}.最近复做日期")
        if last is not None and (today - last).days < 7:
            continue
        next_date = parse_date_strict(track.next_date, f"{track.node_id}.下次原题复做日期")
        if next_date is None or next_date <= today:
            result.append(track)
    subject_rank = {name: index for index, name in enumerate(SUBJECTS)}
    return sorted(result, key=lambda item: (subject_rank.get(item.subject, 9), item.node_id))


def parse_derived_sections(path: Path) -> list[DerivedReviewItem]:
    lines = path.read_text(encoding="utf-8").splitlines()
    starts = [
        index
        for index, line in enumerate(lines)
        if re.match(r"^###\s+Q\d+[.．]\s+", line)
    ]
    result: list[DerivedReviewItem] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        for index in range(start + 1, end):
            if lines[index].startswith("## "):
                end = index
                break
        heading = lines[start]
        match = re.match(r"^###\s+Q(\d+)[.．]\s+(.+)$", heading)
        if not match:
            continue
        number, title = match.groups()
        body = lines[start + 1 : end]
        answer_index = next(
            (index for index, line in enumerate(body) if line.startswith("> [!answer]")),
            None,
        )
        prompt_lines = body if answer_index is None else body[:answer_index]
        feedback_lines = [] if answer_index is None else body[answer_index + 1 :]
        prompt = re.sub(r"\s+", " ", " ".join(line.strip() for line in prompt_lines if line.strip())).strip()
        feedback = re.sub(
            r"\s+",
            " ",
            " ".join(line.lstrip("> ").strip() for line in feedback_lines if line.strip()),
        ).strip()
        if not prompt:
            continue
        result.append(
            DerivedReviewItem(
                key=f"{path.name}#Q{number}",
                title=title.strip(),
                prompt=prompt,
                feedback=feedback or "当前安全输入未提供自查口径；作答后回到来源文件核验。",
                source=str(path),
            )
        )
    return result


def load_d0_items(repo: Path, today: dt.date) -> list[DerivedReviewItem]:
    input_dir = repo / "wiki" / "study_vaults" / "408-full" / "input"
    if not input_dir.is_dir():
        return []
    patterns = [
        f"{today.isoformat()}-*晨间回滚安全队列.md",
        f"{today.isoformat()}-*晨间复盘安全队列.md",
    ]
    paths: set[Path] = set()
    for pattern in patterns:
        paths.update(input_dir.glob(pattern))
    items: dict[str, DerivedReviewItem] = {}
    for path in sorted(paths):
        for item in parse_derived_sections(path):
            items[item.key] = item
    return list(items.values())


def load_open_carryover(repo: Path) -> tuple[set[str], set[str], list[DerivedReviewItem]]:
    dashboard = repo / "wiki" / "study_vaults" / "408-full" / "StudyVault" / "00-Dashboard"
    pointer = dashboard / "当前晨间复盘.md"
    if not pointer.exists():
        return set(), set(), []
    pointer_text = pointer.read_text(encoding="utf-8")
    link = re.search(r"\[\[([^]|]+)", pointer_text)
    if not link:
        return set(), set(), []
    queue_path = dashboard / f"{link.group(1)}.md"
    if not queue_path.exists():
        return set(), set(), []
    content = queue_path.read_text(encoding="utf-8")
    status = re.search(r"(?m)^status:\s*([^\s]+)", content)
    if not status or status.group(1) not in {"open", "in_progress"}:
        return set(), set(), []
    formal_ids = set(re.findall(r"来源复习单元：`(RU_[^`]+)`", content))
    original_ids = set(re.findall(r"(?m)^####\s+OQ-\d+\s+((?:DS|CO|OS|CN)_(?:\d{4}|UNK)_\d{3})\s*$", content))
    derived: list[DerivedReviewItem] = []
    for match in re.finditer(
        r"(?ms)^####\s+DQ-(\d+)\s+(.+?)\n\n- 来源派生项：`([^`]+)`.*?"
        r"- 闭卷问题：(.*?)\n- 通过标准[:：]?.*?\n\n> \[!answer\]- 派生自查口径（作答后展开）\n> (.*?)(?=\n####|\n###|\n##|\Z)",
        content,
    ):
        number, title, key, prompt, feedback = match.groups()
        derived.append(
            DerivedReviewItem(
                # Carryover is state, not identity.  Keep the stable source key
                # so a same-day D0 item cannot be duplicated under a new name.
                key=key,
                title=title.strip(),
                prompt=prompt.strip(),
                feedback=feedback.strip(),
                source=str(queue_path),
            )
        )
    return formal_ids, original_ids, derived


def due_units(units: list[ReviewUnit], today: dt.date) -> list[ReviewUnit]:
    result: list[ReviewUnit] = []
    for unit in units:
        if unit.schedule_state.startswith("blocked") or unit.schedule_state in {"retired", "inactive"}:
            continue
        next_date = parse_date_strict(unit.next_date, f"{unit.unit_id}.下次复习日期")
        if next_date is None or next_date <= today:
            result.append(unit)
    return result


def overdue_days(unit: ReviewUnit, today: dt.date) -> int:
    next_date = parse_date_strict(unit.next_date, f"{unit.unit_id}.下次复习日期")
    return max((today - next_date).days, 0) if next_date else 0


def overdue_band(unit: ReviewUnit, today: dt.date) -> int:
    """Return a bounded lateness bucket; old backlog cannot grow without limit."""
    days = overdue_days(unit, today)
    if days == 0:
        return 0
    if days <= 2:
        return 1
    if days <= 7:
        return 2
    if days <= 14:
        return 3
    if days <= 30:
        return 4
    return 5


def unit_priority(unit: ReviewUnit, today: dt.date) -> tuple[int, int, int, int, str]:
    type_priority = 2 if unit.unit_type in {"错题", "弱题"} else 1 if unit.unit_type in {"易混概念", "算法"} else 0
    return (
        unit.importance,
        unit.mistakes,
        overdue_band(unit, today),
        type_priority,
        unit.unit_id,
    )


def order_units(due: list[ReviewUnit], today: dt.date) -> list[ReviewUnit]:
    """Order every due unit without dropping any item."""
    lane_rank = {"救援": 0, "校准": 1, "到期": 2, "维护": 3}
    subject_rank = {name: index for index, name in enumerate(SUBJECTS)}
    return sorted(
        due,
        key=lambda item: (
            lane_rank.get(item.lane, 9),
            -item.importance,
            -item.mistakes,
            -overdue_band(item, today),
            subject_rank.get(item.subject, 9),
            item.module_key,
            item.unit_id,
        ),
    )


def estimated_minutes(units: list[ReviewUnit], batch_size: int) -> int:
    """Lower-bound planning estimate only; never used to remove due units."""
    per_item = {"救援": 3, "校准": 2, "到期": 2, "维护": 1}
    active = sum(per_item.get(item.lane, 2) for item in units)
    breaks = max((len(units) - 1) // batch_size, 0) * 5
    return active + breaks


def question_track(unit: ReviewUnit, today: dt.date) -> str:
    if unit.unit_type not in {"错题", "弱题", "错题机制"}:
        return "知识机制轨"
    last = parse_date_strict(unit.last_date, f"{unit.unit_id}.上次复习日期")
    if last is not None and (today - last).days < 7:
        return "知识机制轨（原题复做保护）"
    return "知识机制轨；原题轨仅在另行输出完整安全定位后可选"


def stage_hint(unit: ReviewUnit) -> str:
    if unit.interval <= 0 or "calibration" in unit.schedule_state or "uncalibrated" in unit.schedule_state:
        return "待基线校准"
    return f"D{unit.interval}（线索）" if unit.interval in ANCHORS else f"{unit.interval} 天（非标准锚点线索）"


def interval_date_consistent(unit: ReviewUnit) -> bool | None:
    last = parse_date_strict(unit.last_date, f"{unit.unit_id}.上次复习日期")
    next_date = parse_date_strict(unit.next_date, f"{unit.unit_id}.下次复习日期")
    if last is None or next_date is None:
        return None
    return (next_date - last).days == unit.interval


def prompt_for(unit: ReviewUnit) -> str:
    if unit.prompt:
        return protect_text(unit.prompt, limit=520)
    title = unit.title or unit.unit_id
    if unit.unit_type == "易混概念":
        return f"不看资料，比较「{title}」涉及的两个口径：判定信号、适用条件、一个反例分别是什么？"
    if unit.unit_type == "算法":
        return f"不看资料，口述「{title}」的输入、关键步骤或不变量、终止条件，并走一个最小例子。"
    if unit.unit_type == "公式":
        return f"不看资料，写出「{title}」的变量关系，说明每个量的语义、单位和一个边界检查。"
    if unit.unit_type in {"错题", "弱题"}:
        return f"不打开原题，围绕「{title}」回答：识别信号是什么、第一步写什么、最容易混淆的边界是什么？"
    return f"不看资料，说明「{title}」的定义或机制、必要条件，以及一个反例或边界情形。"


def protect_text(text: str, limit: int = 360) -> str:
    text = NODE_ID_RE.sub("对应正式节点（已隐藏）", text or "")
    text = PROTECTED_ANSWER_FRAGMENT_RE.sub("答案信息已隐藏（复做保护）", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "本单元暂缺派生自查口径；回答后再从安全概念页核验。"
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def protect_location_text(text: str, limit: int = 1400) -> str:
    """Mask protected answer labels without destroying node IDs or deep links."""
    text = PROTECTED_ANSWER_FRAGMENT_RE.sub("答案信息已隐藏（复做保护）", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "未记录"
    return text if len(text) <= limit else text[: limit - 1] + "…"


def selection_reason(unit: ReviewUnit, today: dt.date) -> str:
    days = overdue_days(unit, today)
    due_text = (
        "日期未记录，进入校准"
        if parse_date_strict(unit.next_date, f"{unit.unit_id}.下次复习日期") is None
        else f"到期/过期 {days} 天"
    )
    return f"{unit.lane}层；{due_text}；错误次数 {unit.mistakes}；重要程度 {unit.importance}"


def render_queue(repo: Path, today: dt.date, batch_size: int = 10) -> str:
    table = repo / "复习单元总表.md"
    units = load_units(repo)
    carry_unit_ids, carry_original_ids, carry_derived = load_open_carryover(repo)
    unit_by_id = {item.unit_id: item for item in units}
    unknown_carry_units = sorted(carry_unit_ids - set(unit_by_id))
    if unknown_carry_units:
        raise ValueError(f"open queue references missing review units: {unknown_carry_units[:20]}")
    due_by_id = {item.unit_id: item for item in due_units(units, today)}
    due_by_id.update({item: unit_by_id[item] for item in carry_unit_ids})
    due = list(due_by_id.values())
    ordered = order_units(due, today)
    original_tracks = load_original_tracks(repo)
    original_by_id = {item.node_id: item for item in original_tracks}
    unknown_carry_original = sorted(carry_original_ids - set(original_by_id))
    if unknown_carry_original:
        raise ValueError(f"open queue references missing original tracks: {unknown_carry_original[:20]}")
    due_original_by_id = {item.node_id: item for item in due_original_tracks(original_tracks, today)}
    due_original_by_id.update({item: original_by_id[item] for item in carry_original_ids})
    due_original = sorted(
        due_original_by_id.values(),
        key=lambda item: (SUBJECTS.index(item.subject) if item.subject in SUBJECTS else 9, item.node_id),
    )
    derived_by_key = {item.key: item for item in load_d0_items(repo, today)}
    derived_by_key.update({item.key: item for item in carry_derived})
    derived_items = list(derived_by_key.values())
    blocked = [item for item in units if item.schedule_state.startswith("blocked")]
    blocked_original = [item for item in original_tracks if item.state.startswith("blocked")]
    total_due = len(due) + len(due_original) + len(derived_items)
    total_included = len(ordered) + len(due_original) + len(derived_items)
    counts = Counter(item.subject for item in due)
    lane_counts = Counter(item.lane for item in due)
    digest = hashlib.sha256(table.read_bytes()).hexdigest()[:16]

    lines = [
        "---",
        "schema: morning_review_queue_v2",
        f"review_date: {today.isoformat()}",
        "status: draft",
        "execution_mode: daily_loop",
        f"formal_due_as_of: {today.isoformat()}",
        f"due_count: {total_due}",
        f"included_count: {total_included}",
        f"mechanism_due_count: {len(due)}",
        f"original_question_due_count: {len(due_original)}",
        f"derived_d0_due_count: {len(derived_items)}",
        f"open_carryover_count: {len(carry_unit_ids) + len(carry_original_ids) + len(carry_derived)}",
        f"blocked_review_unit_count: {len(blocked)}",
        f"blocked_original_track_count: {len(blocked_original)}",
        f"blocked_count: {len(blocked) + len(blocked_original)}",
        "coverage_mode: full",
        f"batch_size: {batch_size}",
        f"formal_source_hash: {digest}",
        "---",
        "",
        f"# {today.isoformat()} 408 晨间复盘队列",
        "",
        "> [!warning] 边界",
        "> 本队列是只读派生层：不修改正式掌握度、复习日期、错题节点或 `复盘输出/`。先闭卷作答，再展开派生自查口径；原题复做另行执行 7 天外硬筛选。",
        "",
        "## 到期概览",
        "",
        f"- 机制到期/过期：{len(due)} 项；原题轨到期：{len(due_original)} 项；前晚 D0 / open 派生项：{len(derived_items)} 项；合计：{total_due} 项。",
        f"- 本轮纳入：{total_included} 项；遗漏：{total_due - total_included} 项。",
        f"- 待补证据阻塞：复习单元 {len(blocked)} 项；独立原题轨 {len(blocked_original)} 项。它们不进入可评分分母，但必须持续显示在质量审计中。",
        f"- 全覆盖承诺：所有到期项都进入队列；每 {batch_size} 项分成一批，分批只组织顺序，不删项。",
        f"- 首轮最低用时估计：约 {estimated_minutes(ordered, batch_size) + 6 * len(due_original) + 2 * len(derived_items)} 分钟（仅作下界；不含失败项的重学轮）；实际时间是结果，不是裁剪条件。",
        "- 机制单元科目分布：" + "；".join(f"{subject}{counts.get(subject, 0)}" for subject in SUBJECTS) + "。",
        "- 原题轨科目分布：" + "；".join(
            f"{subject}{sum(item.subject == subject for item in due_original)}" for subject in SUBJECTS
        ) + "。",
        "- 风险层分布：" + "；".join(f"{lane}{lane_counts.get(lane, 0)}" for lane in ["救援", "校准", "到期", "维护"]) + "。",
        "",
        "## 本轮队列",
        "",
    ]

    if not ordered:
        lines += ["当前没有到期机制单元。", ""]
    for index, unit in enumerate(ordered, start=1):
        if (index - 1) % batch_size == 0:
            batch_no = (index - 1) // batch_size + 1
            batch_end = min(index + batch_size - 1, len(ordered))
            lines += [f"### 第 {batch_no} 批（MQ-{index:02d} 至 MQ-{batch_end:02d}）", ""]
        item_id = f"MQ-{index:02d}"
        consistency = interval_date_consistent(unit)
        quality_text = (
            "日期与间隔一致"
            if consistency is True
            else "日期或间隔缺失，仅作校准"
            if consistency is None
            else "日期—间隔口径不一致；阶段只作线索，不据此自动改日期"
        )
        lines += [
            f"#### {item_id} {unit.title}",
            "",
            f"- 来源复习单元：`{unit.unit_id}`",
            f"- 科目 / 模块：{unit.subject} / {unit.module}",
            f"- 层级 / 阶段线索：{unit.lane} / {stage_hint(unit)}",
            f"- 调度数据：{quality_text}",
            f"- 复盘轨道：{question_track(unit, today)}",
            f"- 入选理由：{selection_reason(unit, today)}。",
            f"- 闭卷问题：{prompt_for(unit)}",
            "- 作答时限：30–90 秒；先说第一动作，再说机制和边界。",
            "- 通过标准：无提示说清核心机制、第一动作和关键边界；若低置信或理由不完整，不算稳定通过。",
            "",
            "> [!answer]- 派生自查口径（作答后展开）",
            f"> {protect_text(unit.feedback or unit.method, limit=720)}",
            "",
        ]
        if index % batch_size == 0 and index < len(ordered):
            lines += [
                "> [!tip] 批次恢复",
                "> 本批全部记录结果后休息约 5 分钟，再进入下一批。休息不改变全覆盖要求。",
                "",
            ]

    lines += ["## 到期独立原题轨", ""]
    if not due_original:
        lines += ["当前没有已通过主机制基线且满足 7 天保护的到期原题轨。", ""]
    for index, track in enumerate(due_original, start=1):
        if (index - 1) % batch_size == 0:
            batch_no = (index - 1) // batch_size + 1
            batch_end = min(index + batch_size - 1, len(due_original))
            lines += [f"### 原题第 {batch_no} 批（OQ-{index:02d} 至 OQ-{batch_end:02d}）", ""]
        lines += [
            f"#### OQ-{index:02d} {track.node_id}",
            "",
            f"- 主机制单元：`{track.primary_unit_id}`",
            f"- 来源 ID / 年份：{track.source_id or '未记录'} / {track.year or '未标明'}",
            f"- 最近复做日期 / 最早允许日期：{track.last_date or '未记录'} / {track.earliest_date or '未记录'}",
            f"- 定位标记 / 段落号：{track.marker or '未提取'} / {track.paragraph or '未记录'}",
            f"- 完整来源位置：{protect_location_text(track.full_location, limit=1400)}",
            f"- 详情入口：{protect_location_text(track.detail, limit=900)}",
            "- 闭卷任务：打开原题但不展开答案或解析，独立完成完整判型、第一动作与求解；机制口述不能替代原题作答。",
            "- 通过标准：无提示完成关键步骤，并在核验后记录首个断点；只记得旧答案不算通过。",
            "",
            "> [!answer]- 核验边界（作答后展开）",
            "> 本正式库不内嵌原题答案或完整解析；请在完成作答后使用详情系统核验，并只回传结果、断点和用户明确评分。",
            "",
        ]
        if index % batch_size == 0 and index < len(due_original):
            lines += [
                "> [!tip] 原题批次恢复",
                "> 本批全部记录结果后恢复，再继续同一份原题轨队列；休息不改变全覆盖分母。",
                "",
            ]

    lines += ["## 前晚 D0 与 open 派生项", ""]
    if not derived_items:
        lines += ["当前没有独立的 D0 / open 派生项。", ""]
    for index, item in enumerate(derived_items, start=1):
        if (index - 1) % batch_size == 0:
            batch_no = (index - 1) // batch_size + 1
            batch_end = min(index + batch_size - 1, len(derived_items))
            lines += [f"### 派生第 {batch_no} 批（DQ-{index:02d} 至 DQ-{batch_end:02d}）", ""]
        lines += [
            f"#### DQ-{index:02d} {item.title}",
            "",
            f"- 来源派生项：`{item.key}`",
            f"- 来源文件：{item.source}",
            f"- 闭卷问题：{protect_text(item.prompt, limit=1200)}",
            "- 通过标准：先无提示生成答案；低置信、部分正确、错误或空白均须反馈后再次提取。",
            "",
            "> [!answer]- 派生自查口径（作答后展开）",
            f"> {protect_text(item.feedback, limit=1600)}",
            "",
        ]

    lines += [
        "## 修复轮：successive relearning",
        "",
        "- 首答错误、部分正确、空白，以及低置信或明显迟缓的脆弱正确项，都进入本批修复队列。",
        "- 先给纠正性或逐级提示反馈；隔开若干其他项目后，换线索再次闭卷提取。",
        "- 每项至少达到一次无提示独立正确，才完成本次到期复盘；只看懂反馈不算完成。",
        "- 同日修复达标不冒充下一次跨日成功；后续到期日仍需再次提取。",
        "",
        "## 派生结果（不等于正式掌握度）",
        "",
        "| Item | 批次 | 首答 | 置信度 | 反应时 | 是否提示 | 反馈类型 | 修复再提取 | 保持状态 | 迁移状态 |",
        "|---|---:|---|---|---|---|---|---|---|---|",
    ]
    for index in range(1, len(ordered) + 1):
        batch_no = (index - 1) // batch_size + 1
        lines.append(
            f"| MQ-{index:02d} | {batch_no} | 待作答 | 待记录 | 待记录 | 待记录 | 待记录 | 待记录 | 待判定 | 待判定 |"
        )
    for index in range(1, len(due_original) + 1):
        batch_no = (index - 1) // batch_size + 1
        lines.append(
            f"| OQ-{index:02d} | 原题{batch_no} | 待作答 | 待记录 | 待记录 | 待记录 | 详情核验 | 待记录 | 待判定 | 待判定 |"
        )
    for index in range(1, len(derived_items) + 1):
        batch_no = (index - 1) // batch_size + 1
        lines.append(
            f"| DQ-{index:02d} | 派生{batch_no} | 待作答 | 待记录 | 待记录 | 待记录 | 待记录 | 待记录 | 待判定 | 待判定 |"
        )
    lines += [
        "",
        "> 首答使用 `independent_correct / fragile_correct / partial / wrong / blank`。置信度、反应时和提示使用不能替代正确性。只有用户明确给出 0–5 分后，正式流程才可更新掌握度和下次日期。",
        "",
        "## 全覆盖门禁",
        "",
        f"- `queue_coverage = 已首答数 / {total_included}`，必须达到 100%；`skipped_count` 必须为 0。",
        f"- `criterion_coverage = 已至少一次无提示独立正确数 / {total_included}`，目标 100%。",
        "- 首答失败或脆弱正确项必须有反馈和稍后的修复再提取记录。",
        "- `carried_over` 不算关闭；它表示队列仍 open，恢复后继续同一份队列。",
        "- 未经用户明确评分，不修改正式表。",
        "",
    ]
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a full-coverage answer-safe 408 morning review queue.")
    parser.add_argument("--repo", default=str(ROOT))
    parser.add_argument("--date", default=dt.date.today().isoformat())
    parser.add_argument("--batch-size", type=int, default=10, help="Items per work batch; does not cap coverage.")
    parser.add_argument(
        "--write-safe",
        action="store_true",
        help="Write only to StudyVault/00-Dashboard; default is stdout/read-only.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    repo = Path(args.repo).resolve()
    try:
        today = dt.date.fromisoformat(args.date)
    except ValueError:
        print("--date must be YYYY-MM-DD", file=sys.stderr)
        return 2
    if args.batch_size < 1 or args.batch_size > 50:
        print("--batch-size must be between 1 and 50", file=sys.stderr)
        return 2
    table = repo / "复习单元总表.md"
    if not table.exists():
        print(f"missing review table: {table}", file=sys.stderr)
        return 2

    try:
        content = render_queue(repo, today, args.batch_size)
    except (OSError, ValueError) as exc:
        print(f"queue generation failed: {exc}", file=sys.stderr)
        return 2
    if not args.write_safe:
        print(content)
        return 0

    content = re.sub(r"(?m)^status: draft$", "status: ready", content, count=1)

    output = (
        repo
        / "wiki"
        / "study_vaults"
        / "408-full"
        / "StudyVault"
        / "00-Dashboard"
        / f"{today.isoformat()}-408晨间复盘队列.md"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    pointer = output.parent / "当前晨间复盘.md"
    if pointer.exists() and "schema: morning_review_pointer_v1" not in pointer.read_text(encoding="utf-8"):
        print(f"refusing to overwrite non-generated morning pointer: {pointer}", file=sys.stderr)
        return 2
    desired = content.rstrip() + "\n"
    if output.exists():
        existing = output.read_text(encoding="utf-8")
        if existing != desired:
            print(
                f"refusing to overwrite existing same-day queue with possible user results: {output}",
                file=sys.stderr,
            )
            return 2
    else:
        output.write_text(desired, encoding="utf-8")
    pointer_content = "\n".join(
        [
            "---",
            "schema: morning_review_pointer_v1",
            f"review_date: {today.isoformat()}",
            "status: ready",
            "---",
            "",
            "# 当前晨间复盘",
            "",
            f"- 当前队列：[[{output.stem}|{today.isoformat()} 408 晨间复盘队列]]",
            "- 稳定入口：[[晨间复盘中心]]",
            "",
            "> 本页由只读生成器的显式 `--write-safe` 模式维护；只指向安全派生队列，不修改正式复习数据。",
            "",
        ]
    )
    pointer.write_text(pointer_content, encoding="utf-8")
    print(output)
    print(pointer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
