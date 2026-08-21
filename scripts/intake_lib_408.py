#!/usr/bin/env python3
"""408 单题入库脚本族共享库。

供 intake_preflight_408.py / intake_apply_408.py / intake_validate_408.py /
related_candidates_408.py 使用：仓库表解析、标签表解析、表格行读写、
答案泄露扫描、日期工具。只依赖标准库。
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------- 常量

SUBJECTS = ("DS", "CO", "OS", "CN")

SUBJECT_FULL = {
    "DS": "数据结构",
    "CO": "计算机组成原理",
    "OS": "操作系统",
    "CN": "计算机网络",
}

NODE_LIB_FILE = {
    "DS": "节点库/数据结构节点.md",
    "CO": "节点库/计算机组成原理节点.md",
    "OS": "节点库/操作系统节点.md",
    "CN": "节点库/计算机网络节点.md",
}

MASTER_FILE = "节点总表.md"
EDGE_FILE = "关系边表.md"
YEAR_INDEX_FILE = "年份索引.md"
HIT_INDEX_FILE = "知识点命中索引.md"
DATE_INDEX_FILE = "错题日期索引.md"
REDO_FILE = "错题复做记录.md"
REVIEW_UNIT_FILE = "复习单元总表.md"
REVIEW_UNIT_MAPPING_FILE = "复习单元节点映射.md"
ORIGINAL_TRACK_FILE = "原题复做轨总表.md"
REVIEW_UNIT_CARD_DIR = "复习单元卡"
KP_TAG_FILE = "知识点标签表.md"
ETAG_FILE = "错因标签表.md"
RTAG_FILE = "关系规则.md"
TOPIC_CHAIN_DIR = "专题链"

MASTER_COLS = [
    "ID", "来源ID", "年份", "科目", "主模块", "主知识点", "副知识点",
    "命中知识点", "题型", "核心考点", "模糊概念", "错因标签",
    "首次做题日期", "最近复做日期", "最近错误记录", "详情入口",
]

DATE_INDEX_COLS = [
    "ID", "来源ID", "科目", "主知识点", "首次做题日期", "最近复做日期",
    "7天外候选", "日期来源", "最近错误记录",
]

NODE_LIB_COLS = ["ID", "来源ID", "年份", "主知识点", "核心考点", "错因标签", "最近复做日期"]

YEAR_INDEX_COLS = ["ID", "来源ID", "科目", "模块", "主知识点", "首次做题日期", "最近复做日期", "详情入口"]

REDO_COLS = ["日期", "ID", "来源ID", "动作", "错误记录"]

EDGE_COLS = ["起点ID", "终点ID", "关系类型", "联系强度", "关联原因", "复盘优先级"]

REVIEW_UNIT_COLS = [
    "复习单元ID", "科目", "模块", "内容名称", "类型", "调度状态", "覆盖节点",
    "首次学习日期", "上次复习日期", "下次复习日期", "当前间隔天数", "掌握度",
    "错误次数", "重要程度", "难度等级", "闭卷问题", "派生自查口径", "备注",
]

REVIEW_UNIT_MAPPING_COLS = [
    "正式节点ID", "科目", "证据等级", "实际考点", "主复习单元ID", "次复习单元ID",
    "覆盖方式", "复核状态", "原题轨", "原题轨资格", "待补事项",
]

ORIGINAL_TRACK_COLS = [
    "正式节点ID", "主复习单元ID", "原题轨状态", "7天资格", "最近复做日期",
    "最早允许日期", "下次原题复做日期", "来源ID", "年份", "定位标记", "段落号",
    "完整来源位置", "详情入口", "激活条件", "待补事项",
]

FORMAL_ID_RE = re.compile(r"^(DS|CO|OS|CN)_(\d{4}|UNK)_(\d{3})$")
HIST_ID_RE = re.compile(r"^H(DS|CO|OS|CN)_\d{4}$")
KP_CODE_RE = re.compile(r"^([A-Z]{2}\d{2})-(\d{2})\s+(.+)$")
MODULE_CODE_RE = re.compile(r"^([A-Z]{2}\d{2})\s+(.+)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# 允许的日期占位
DATE_PLACEHOLDERS = {"未记录", "待确认", "待补充"}

NO_ETAG = "暂无明确错因"

STRENGTHS = {"强", "中", "弱"}
PRIORITIES = {"高", "中", "低"}

# 关系类型 → 默认联系强度建议（关系规则.md 复盘排序规则）
R_DEFAULT_STRENGTH = {
    "R01": "强", "R02": "强", "R03": "强", "R04": "强", "R05": "强",
    "R06": "中", "R09": "中",
    "R07": "弱", "R08": "弱",
}

# ---------------------------------------------------------------- 答案泄露扫描

# FAIL 级：明确的答案结论表述
LEAK_FAIL_PATTERNS = [
    (re.compile(r"正确答案"), "含“正确答案”表述"),
    (re.compile(r"标准答案"), "含“标准答案”表述"),
    (re.compile(r"答案[是为选:：]\s*[A-DＡ-Ｄ]\b"), "含“答案是/为/选X”选项字母结论"),
    (re.compile(r"应选\s*[A-DＡ-Ｄ]\b"), "含“应选X”选项字母结论"),
    (re.compile(r"正确选项"), "含“正确选项”表述"),
    (re.compile(r"故选\s*[A-DＡ-Ｄ]\b"), "含“故选X”选项字母结论"),
    (re.compile(r"选\s*[A-DＡ-Ｄ]\s*(正确|对)"), "含“选X正确”选项字母结论"),
    (
        re.compile(
            r"(?:本题|我|用户)?\s*(?:选|选择|作答|回答)\s*"
            r"(?:了|为|是|[:：])?\s*[A-HＡ-Ｈ](?![A-Za-z])",
            re.IGNORECASE,
        ),
        "含作答语境中的选项字母",
    ),
]

# WARN 级：疑似选项字母组合 / 用户错选痕迹，需人工确认
LEAK_WARN_PATTERNS = [
    (re.compile(r"错选\s*了?\s*[A-DＡ-Ｄ]\b"), "疑似记录用户错选选项字母"),
    (re.compile(r"\b[A-D]\s*[、,，]\s*[A-D]\b(?![)）级])"), "疑似选项字母组合"),
    (re.compile(r"选项\s*[A-HＡ-Ｈ](?![A-Za-z])", re.IGNORECASE), "疑似指名具体选项字母"),
]

# 泄露扫描白名单：难度等级 A基础/B中等 等惯用语
LEAK_WHITELIST_RE = re.compile(r"[ABC](基础|中等|提高)")

# 只移除这些固定、无载荷的安全占位符；前缀和后缀仍继续扫描。
# 第二条是复做保护说明里那句“不保存……”的否定式清单：它声明的是这些内容的
# 缺席，不是泄露。只剥离这一个字面模板短语，词表本身仍然全库生效——写了
# “不保存”再泄露仍会被抓到，因为只有完整模板短语才会被移除。
LEAK_SAFE_PLACEHOLDER_RE = re.compile(
    r"答案信息已隐藏（复做保护）"
    r"|不保存完整题干、完整解析、标准答案、"
    r"(?:正确选项、用户错选项、选项字母组合"
    r"|错选项、选项字母"
    r"|错选答案)"
    r"、图片或手写痕迹"
)


def scan_leak(text: str) -> tuple[list[str], list[str]]:
    """返回 (fail 描述列表, warn 描述列表)。

    按行扫描。固定答案安全占位符本身不参与匹配，但其前后内容仍扫描；
    “不得”、“已隐藏”、“复做保护”等词不会屏蔽同行后续内容。
    """
    fails, warns = [], []
    for raw_line in (text or "").splitlines() or [text or ""]:
        cleaned = LEAK_SAFE_PLACEHOLDER_RE.sub("", raw_line)
        cleaned = LEAK_WHITELIST_RE.sub("", cleaned)
        for pat, desc in LEAK_FAIL_PATTERNS:
            if pat.search(cleaned) and desc not in fails:
                fails.append(desc)
        for pat, desc in LEAK_WARN_PATTERNS:
            if pat.search(cleaned) and desc not in warns:
                warns.append(desc)
    return fails, warns


# ---------------------------------------------------------------- 基础工具

def die(msg: str, code: int = 2):
    print(f"[错误] {msg}", file=sys.stderr)
    sys.exit(code)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, text: str):
    path.write_text(text, encoding="utf-8")


def today_str(override: str | None = None) -> str:
    if override:
        if not DATE_RE.match(override):
            die(f"--today 格式应为 YYYY-MM-DD：{override}")
        return override
    return _dt.date.today().isoformat()


def parse_date(s: str) -> _dt.date | None:
    s = (s or "").strip()
    if DATE_RE.match(s):
        try:
            return _dt.date.fromisoformat(s)
        except ValueError:
            return None
    return None


def days_since(date_s: str, today: str) -> int | None:
    """最近复做日期距 today 的天数；日期缺失/占位返回 None。"""
    d = parse_date(date_s)
    if d is None:
        return None
    return (_dt.date.fromisoformat(today) - d).days


def is_outside_7d(date_s: str, today: str) -> bool:
    """7 天外硬筛选：日期未记录按 7 天外候选处理。"""
    n = days_since(date_s, today)
    return True if n is None else n >= 7


# ---------------------------------------------------------------- Markdown 表格

def split_row(line: str) -> list[str]:
    line = line.strip()
    if not (line.startswith("|") and line.endswith("|")):
        return []
    return [c.strip() for c in line[1:-1].split("|")]


def build_row(cells: list[str], padded: bool = False) -> str:
    for c in cells:
        if "|" in c:
            die(f"表格单元格含竖线，会破坏表结构：{c[:50]}")
        if "\n" in c:
            die(f"表格单元格含换行：{c[:50]}")
    if padded:
        return "| " + " | ".join(cells) + " |"
    return "|" + "|".join(cells) + "|"


def is_sep_row(line: str) -> bool:
    cells = split_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c) for c in cells)


def find_table(lines: list[str], first_col: str, start: int = 0):
    """定位第一列列名为 first_col 的表。

    返回 (header_idx, last_row_idx)。last_row_idx 为表格块最后一行的下标
    （含表头与分隔行；表可以为空，此时 last_row_idx 是分隔行）。
    找不到返回 (None, None)。
    """
    for i in range(start, len(lines)):
        cells = split_row(lines[i])
        if cells and cells[0] == first_col and i + 1 < len(lines) and is_sep_row(lines[i + 1]):
            j = i + 1
            while j + 1 < len(lines) and split_row(lines[j + 1]):
                j += 1
            return i, j
    return None, None


def iter_table_rows(lines: list[str], first_col: str, start: int = 0):
    """生成 (line_idx, cells)，跳过表头与分隔行。"""
    h, last = find_table(lines, first_col, start)
    if h is None:
        return
    for i in range(h + 2, last + 1):
        cells = split_row(lines[i])
        if cells:
            yield i, cells


# ---------------------------------------------------------------- 仓库数据解析

class Repo:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        if not (self.root / MASTER_FILE).exists():
            die(f"{self.root} 下没有 {MASTER_FILE}，--repo 指向 kaoyan-408 仓库根")
        self._master = None
        self._kp = None
        self._etags = None
        self._rtags = None

    def path(self, rel: str) -> Path:
        return self.root / rel

    # ---- 节点总表
    def master(self) -> dict[str, dict]:
        if self._master is None:
            lines = read_text(self.path(MASTER_FILE)).splitlines()
            out = {}
            for _, cells in iter_table_rows(lines, "ID"):
                if len(cells) != len(MASTER_COLS):
                    # 结构损坏行也登记，供 validate 报告
                    row = dict(zip(MASTER_COLS, cells + [""] * len(MASTER_COLS)))
                    row["_bad_colcount"] = len(cells)
                else:
                    row = dict(zip(MASTER_COLS, cells))
                out[row["ID"]] = row
            self._master = out
        return self._master

    # ---- 知识点标签表：返回 (modules {CO03: name}, leaves {CO03-04: name})
    def kp_tags(self):
        if self._kp is None:
            modules, leaves = {}, {}
            for line in read_text(self.path(KP_TAG_FILE)).splitlines():
                s = line.strip()
                if s.startswith("## "):
                    m = MODULE_CODE_RE.match(s[3:].strip())
                    if m:
                        modules[m.group(1)] = m.group(2).strip()
                elif s.startswith("- "):
                    m = KP_CODE_RE.match(s[2:].strip())
                    if m:
                        leaves[f"{m.group(1)}-{m.group(2)}"] = m.group(3).strip()
            self._kp = (modules, leaves)
        return self._kp

    # ---- 错因标签表 {E01: 概念边界混淆}
    def etags(self) -> dict[str, str]:
        if self._etags is None:
            out = {}
            for line in read_text(self.path(ETAG_FILE)).splitlines():
                m = re.match(r"^##\s+(E\d{2})\s+(.+)$", line.strip())
                if m:
                    out[m.group(1)] = m.group(2).strip()
            self._etags = out
        return self._etags

    # ---- 关系规则 {R01: 同一核心考点}
    def rtags(self) -> dict[str, str]:
        if self._rtags is None:
            out = {}
            for line in read_text(self.path(RTAG_FILE)).splitlines():
                m = re.match(r"^##\s+(R\d{2})\s+(.+)$", line.strip())
                if m:
                    out[m.group(1)] = m.group(2).strip()
            self._rtags = out
        return self._rtags

    # ---- 关系边表：list[dict]
    def edges(self) -> list[dict]:
        lines = read_text(self.path(EDGE_FILE)).splitlines()
        out = []
        for _, cells in iter_table_rows(lines, "起点ID"):
            if len(cells) == len(EDGE_COLS):
                out.append(dict(zip(EDGE_COLS, cells)))
        return out

    # ---- 专题链文件名列表
    def topic_chains(self) -> list[str]:
        d = self.path(TOPIC_CHAIN_DIR)
        if not d.is_dir():
            return []
        return sorted(p.name for p in d.glob("LINK*.md"))


# ---------------------------------------------------------------- 标签串工具

def split_multi(s: str) -> list[str]:
    """按全角/半角分号切多值字段。"""
    return [p.strip() for p in re.split(r"[；;]", s or "") if p.strip()]


def normalize_tag(code_name: str) -> tuple[str, str]:
    """'CO03-04 DRAM' -> ('CO03-04', 'DRAM')；无空格时名称为空。"""
    s = (code_name or "").strip()
    m = re.match(r"^(\S+)\s+(.*)$", s)
    if m:
        return m.group(1), m.group(2).strip()
    return s, ""


def check_kp_item(item: str, leaves: dict[str, str]) -> str | None:
    """校验 '编码 名称' 是否在标签表；返回错误描述或 None。"""
    code, name = normalize_tag(item)
    if code not in leaves:
        return f"知识点编码不在标签表：{item}"
    if name and name != leaves[code]:
        return f"知识点名称与标签表不一致：{item}（标签表为 {code} {leaves[code]}）"
    return None


def check_etag_item(item: str, etags: dict[str, str]) -> str | None:
    if item == NO_ETAG:
        return None
    code, name = normalize_tag(item)
    if not re.fullmatch(r"E(0[1-9]|10)", code):
        return f"错因标签编码非法（只允许 E01-E10 或 {NO_ETAG}）：{item}"
    if code not in etags:
        return f"错因标签不在错因标签表：{item}"
    if name and name != etags[code]:
        return f"错因标签名称与标签表不一致：{item}（标签表为 {code} {etags[code]}）"
    return None


def check_rtag_item(item: str, rtags: dict[str, str]) -> str | None:
    code, name = normalize_tag(item)
    if not re.fullmatch(r"R0[1-9]", code):
        return f"关系类型非法（只允许 R01-R09）：{item}"
    if code not in rtags:
        return f"关系类型不在关系规则：{item}"
    if name and name != rtags[code]:
        return f"关系类型名称与关系规则不一致：{item}（规则为 {code} {rtags[code]}）"
    return None


# ---------------------------------------------------------------- intake 包

PKG_REQUIRED_NEW = [
    "mode", "formal_id", "source_id", "year", "subject", "module",
    "main_knowledge", "sub_knowledge", "hit_knowledge", "question_type",
    "core_point", "safe_summary", "key_parameters", "ask_type",
    "user_error_entry", "redo_first_action", "fuzzy_concepts", "error_tags",
    "detail_entry", "attachment_registry", "first_done_date",
    "latest_review_date", "latest_error_record",
]

PKG_REQUIRED_REDO = [
    "mode", "formal_id", "latest_review_date", "latest_error_record",
]

ATTACH_KEYS = ["题图", "解析图或解析正文", "作答痕迹", "可视化详情入口", "当前附件来源"]

TEXT_FIELDS_FOR_LEAK = [
    "core_point", "safe_summary", "key_parameters", "ask_type",
    "user_error_entry", "redo_first_action", "fuzzy_concepts",
    "detail_entry", "latest_error_record",
]


def load_package(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        die(f"intake 包不存在：{p}")
    try:
        pkg = json.loads(read_text(p))
    except json.JSONDecodeError as e:
        die(f"intake 包 JSON 解析失败：{e}")
    if not isinstance(pkg, dict):
        die("intake 包必须是 JSON object")
    return pkg
