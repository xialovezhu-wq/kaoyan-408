#!/usr/bin/env python3
"""408 单题入库前置校验（只读，不写任何仓库文件）。

用法：
  python3 scripts/intake_preflight_408.py <intake_package.json> [--repo .] [--json]
  python3 scripts/intake_preflight_408.py --suggest-id CO --year 2014 [--repo .]

校验 intake 包：ID 格式与唯一性、来源 ID、主知识点唯一且在标签表、
副/命中知识点合法、错因 E01-E10、关系 R01-R09 与端点、日期格式、
安全卡必填字段、答案泄露、临时路径、专题链存在性、详情卡路径分流（A/B）。

exit code：0 = 全部 PASS（允许 WARN）；1 = 存在 FAIL。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from intake_lib_408 import (  # noqa: E402
    ATTACH_KEYS, DATE_PLACEHOLDERS, DATE_RE, FORMAL_ID_RE, HIST_ID_RE, NO_ETAG,
    PKG_REQUIRED_NEW, PKG_REQUIRED_REDO, PRIORITIES, R_DEFAULT_STRENGTH,
    STRENGTHS, SUBJECT_FULL, SUBJECTS, TEXT_FIELDS_FOR_LEAK, Repo,
    check_etag_item, check_kp_item, check_rtag_item, load_package,
    normalize_tag, scan_leak, split_multi,
)

# The details vault lives outside this repo, so its location is host-specific.
# ``KAOYAN_408_DETAILS_ROOT`` lets a sandbox, CI runner, or second machine point at
# its own mount without editing tracked code. Unset means the original path, so
# existing behaviour is unchanged.
DETAILS_ROOT_DEFAULT = os.environ.get(
    "KAOYAN_408_DETAILS_ROOT", "/Users/xiazhibin/Documents/kaoyan-408-details"
)
TEMPLATE_ONLY_RELATION_REASON_RE = re.compile(
    r"^(?:同一主知识点|同属专题链|同一模糊概念)[：:][^，,；;。]{1,100}[。.]?$"
)


class Report:
    def __init__(self):
        self.items = []  # (level, check, message)

    def add(self, level, check, msg):
        self.items.append((level, check, msg))

    def fail(self, check, msg):
        self.add("FAIL", check, msg)

    def warn(self, check, msg):
        self.add("WARN", check, msg)

    def ok(self, check, msg=""):
        self.add("PASS", check, msg)

    @property
    def failed(self):
        return any(l == "FAIL" for l, _, _ in self.items)

    def print_text(self):
        for level, check, msg in self.items:
            line = f"[{level}] {check}"
            if msg:
                line += f"：{msg}"
            print(line)
        n_fail = sum(1 for l, _, _ in self.items if l == "FAIL")
        n_warn = sum(1 for l, _, _ in self.items if l == "WARN")
        print(f"---\npreflight 结果：{'FAIL' if n_fail else 'PASS'}（FAIL {n_fail} / WARN {n_warn}）")

    def print_json(self):
        print(json.dumps(
            {"result": "FAIL" if self.failed else "PASS",
             "items": [{"level": l, "check": c, "message": m} for l, c, m in self.items]},
            ensure_ascii=False, indent=2))


def _persisted_master(repo: Repo) -> dict[str, dict[str, str]]:
    """Return only nodes already persisted before the current batch projection.

    Batch preflight wraps ``Repo`` with a projected view so relation checks can
    resolve every new endpoint in the batch.  ID uniqueness and allocation must
    still be evaluated against the persisted baseline, not that projected view.
    Plain ``Repo`` instances do not expose the optional hook and retain their
    original behaviour.
    """
    getter = getattr(repo, "persisted_master", None)
    return getter() if callable(getter) else repo.master()


def suggest_id(repo: Repo, subject: str, year: str) -> str:
    if subject not in SUBJECTS:
        print(f"[错误] 科目应为 {SUBJECTS}")
        sys.exit(2)
    year = year or "UNK"
    prefix = f"{subject}_{year}_"
    seqs = [int(i.rsplit("_", 1)[1]) for i in _persisted_master(repo) if i.startswith(prefix)]
    return f"{prefix}{(max(seqs) + 1 if seqs else 1):03d}"


def check_date_field(rep: Report, name: str, value: str, required: bool = True):
    v = (value or "").strip()
    if not v:
        if required:
            rep.fail(f"日期字段 {name}", "为空")
        return
    if DATE_RE.match(v) or v in DATE_PLACEHOLDERS:
        rep.ok(f"日期字段 {name}", v)
    else:
        rep.fail(f"日期字段 {name}", f"格式应为 YYYY-MM-DD 或 {sorted(DATE_PLACEHOLDERS)}：{v}")


def run_preflight(pkg: dict, repo: Repo, details_root: Path) -> Report:
    rep = Report()
    mode = pkg.get("mode", "new")
    if mode not in ("new", "redo"):
        rep.fail("mode", f"只支持 new / redo：{mode}")
        return rep

    required = PKG_REQUIRED_NEW if mode == "new" else PKG_REQUIRED_REDO
    missing = [k for k in required if k not in pkg or pkg[k] in (None, "")]
    if missing:
        rep.fail("必填字段", f"缺失：{missing}")
        return rep
    rep.ok("必填字段", f"mode={mode}，字段齐全")

    master = repo.master()
    persisted_master = _persisted_master(repo)
    fid = pkg["formal_id"].strip()

    # ---- ID 格式 / 唯一性
    m = FORMAL_ID_RE.match(fid)
    if not m:
        rep.fail("正式ID格式", f"{fid} 不符合 DS/CO/OS/CN_年份_三位序号")
        return rep
    id_subject, id_year = m.group(1), m.group(2)
    if mode == "new":
        if fid in persisted_master:
            rep.fail("ID唯一性", f"{fid} 已存在于节点总表；新题入库禁止覆盖。若是再错请用 mode=redo")
        else:
            rep.ok("ID唯一性", f"{fid} 未占用")
        expected = suggest_id(repo, id_subject, id_year)
        if fid != expected:
            rep.warn("ID顺序", f"当前包用 {fid}，该科目年份下一个可用序号是 {expected}")
    else:
        if fid not in persisted_master:
            rep.fail("ID存在性", f"redo 模式要求 {fid} 已在节点总表")
            return rep
        rep.ok("ID存在性", f"{fid} 已在节点总表，走再错追加路径")

    # ---- redo 模式只需再校验日期与错误记录 + 泄露
    if mode == "redo":
        check_date_field(rep, "latest_review_date", pkg["latest_review_date"])
        if len(pkg["latest_error_record"].strip()) < 10:
            rep.fail("最近错误记录", "过短，无法支撑复做保护摘要")
        fails, warns = scan_leak(pkg.get("latest_error_record", ""))
        for f in fails:
            rep.fail("答案泄露(latest_error_record)", f)
        for w in warns:
            rep.warn("答案泄露(latest_error_record)", w)
        add_etags = pkg.get("error_tags") or []
        for t in add_etags:
            err = check_etag_item(t, repo.etags())
            if err:
                rep.fail("错因标签", err)
        return rep

    # ================= new 模式完整校验 =================
    subject = pkg["subject"].strip()
    if subject not in SUBJECTS:
        rep.fail("科目", f"应为 {SUBJECTS}：{subject}")
    elif subject != id_subject:
        rep.fail("科目一致性", f"包 subject={subject} 与 ID 前缀 {id_subject} 不一致")
    else:
        rep.ok("科目", f"{subject} {SUBJECT_FULL[subject]}")

    year = str(pkg["year"]).strip()
    if year != id_year:
        rep.fail("年份一致性", f"包 year={year} 与 ID 中 {id_year} 不一致")
    else:
        rep.ok("年份", year)

    # ---- 来源 ID
    sid = pkg["source_id"].strip()
    if sid == "VISUAL_PENDING" or HIST_ID_RE.match(sid):
        rep.ok("来源ID", sid)
        if HIST_ID_RE.match(sid):
            rep.warn("历史来源", f"{sid} 为历史归档 ID：入库后需检查 历史错题归档/正式ID映射.md 与 定位索引.md 是否已回填（历史题路径）")
    elif not sid:
        rep.fail("来源ID", "为空；至少写 VISUAL_PENDING")
    else:
        rep.warn("来源ID", f"非常规值 {sid}，确认是可回溯的来源标识")

    # ---- 主知识点 / 模块 / 副 / 命中
    modules, leaves = repo.kp_tags()
    mk = pkg["main_knowledge"].strip()
    if len(split_multi(mk)) != 1:
        rep.fail("主知识点唯一性", f"必须恰好一个：{mk}")
    err = check_kp_item(mk, leaves)
    if err:
        rep.fail("主知识点", err)
    else:
        rep.ok("主知识点", mk)
    mk_code, _ = normalize_tag(mk)
    mk_chapter = mk_code.split("-")[0] if "-" in mk_code else ""

    mod_code, mod_name = normalize_tag(pkg["module"].strip())
    if mod_code not in modules:
        rep.fail("主模块", f"{pkg['module']} 不在知识点标签表模块列表")
    elif mod_name and mod_name != modules[mod_code]:
        rep.fail("主模块", f"名称与标签表不一致：{pkg['module']}（应为 {mod_code} {modules[mod_code]}）")
    elif mk_chapter and mod_code != mk_chapter:
        rep.fail("主模块一致性", f"主模块 {mod_code} 与主知识点章 {mk_chapter} 不同章")
    else:
        rep.ok("主模块", pkg["module"])
    if mk_chapter and subject and not mk_chapter.startswith(subject):
        rep.fail("主知识点科目一致性", f"主知识点 {mk_code} 与科目 {subject} 不符")

    subs = pkg.get("sub_knowledge") or []
    hits = list(pkg.get("hit_knowledge") or [])
    for item in subs:
        err = check_kp_item(item, leaves)
        if err:
            rep.fail("副知识点", err)
    hit_codes = set()
    for item in hits:
        err = check_kp_item(item, leaves)
        if err:
            rep.fail("命中知识点", err)
        else:
            hit_codes.add(normalize_tag(item)[0])
    if mk_code and mk_code not in hit_codes:
        rep.warn("命中包含主知识点", f"hit_knowledge 未含主知识点 {mk_code}，apply 将自动补入")
    if not rep.failed:
        rep.ok("知识点标签", f"副 {len(subs)} 项 / 命中 {len(hits)} 项均合法")

    # ---- 错因标签
    etags = pkg["error_tags"]
    if not isinstance(etags, list) or not etags:
        rep.fail("错因标签", "error_tags 必须是非空数组")
    else:
        if NO_ETAG in etags and len(etags) > 1:
            rep.fail("错因标签", f"“{NO_ETAG}”不能与 E 标签混用")
        for t in etags:
            err = check_etag_item(t, repo.etags())
            if err:
                rep.fail("错因标签", err)
        if not any(l == "FAIL" and c == "错因标签" for l, c, _ in rep.items):
            rep.ok("错因标签", "；".join(etags))

    # ---- 安全卡必填文本
    for field, label, min_len in [
        ("safe_summary", "安全题目摘要", 10), ("key_parameters", "关键题设参数", 8),
        ("ask_type", "问法类型", 4), ("user_error_entry", "用户错误入口", 10),
        ("redo_first_action", "复做第一动作", 10), ("core_point", "核心考点", 8),
        ("fuzzy_concepts", "模糊概念", 5), ("question_type", "题型", 3),
        ("latest_error_record", "最近错误记录", 10),
    ]:
        v = (pkg.get(field) or "").strip()
        if len(v) < min_len:
            rep.fail(f"安全字段 {label}", "为空或过短；安全卡不能空白")
    if not any(c.startswith("安全字段") and l == "FAIL" for l, c, _ in rep.items):
        rep.ok("安全字段", "九项复做安全字段均非空")

    # ---- 日期
    check_date_field(rep, "first_done_date", pkg["first_done_date"])
    check_date_field(rep, "latest_review_date", pkg["latest_review_date"])

    # ---- 详情入口 / 附件登记
    de = pkg["detail_entry"].strip()
    if "/var/folders/" in de or "/tmp/" in de or "/T/" in de:
        rep.fail("详情入口", "含临时截图路径；临时路径不能作为 durable 详情入口，应写“可视化详情入口待补充”并在附件登记里记录本次来源")
    ar = pkg.get("attachment_registry") or {}
    miss_keys = [k for k in ATTACH_KEYS if k not in ar]
    if miss_keys:
        rep.fail("附件登记", f"缺键：{miss_keys}")
    else:
        rep.ok("附件登记", "键齐全")
        cur_src = str(ar.get("当前附件来源", ""))
        if ("/var/folders/" in str(ar.get("可视化详情入口", ""))):
            rep.fail("附件登记", "可视化详情入口不得是临时截图路径")
        if "/var/folders/" in cur_src:
            rep.warn("附件登记", "当前附件来源为临时路径，仅作本次证据，确认已按“本次对话附件”语义登记")

    # ---- 详情卡 A/B 路径分流
    card = details_root / "cards" / f"{fid}.md"
    if card.exists():
        rep.ok("详情卡路径", f"A 路径：详情库已有 {card.name}")
    else:
        rep.warn("详情卡路径", f"B 路径：{card} 不存在；入库后详情入口按待补处理，安全卡登记附件状态")

    # ---- 关系候选
    rels = pkg.get("relation_candidates") or []
    for i, r in enumerate(rels):
        tag = f"关系候选#{i+1}"
        for key in ("from", "to", "type", "strength", "reason", "priority"):
            if not str(r.get(key, "")).strip():
                rep.fail(tag, f"缺字段 {key}")
        frm, to = str(r.get("from", "")).strip(), str(r.get("to", "")).strip()
        if frm and fid not in {frm, to}:
            rep.warn(tag, f"关系边 {frm} ↔ {to} 不包含本题 {fid}，确认归属")
        for endpoint_name, endpoint in (("起点", frm), ("终点", to)):
            if endpoint.startswith("H") and HIST_ID_RE.match(endpoint):
                rep.fail(tag, f"{endpoint_name} {endpoint} 是历史 ID，关系边端点必须用正式节点 ID")
            elif endpoint and endpoint != fid and endpoint not in master:
                rep.fail(tag, f"{endpoint_name} {endpoint} 不在节点总表或当前批投影视图")
        if frm == to:
            rep.fail(tag, "自环边")
        err = check_rtag_item(str(r.get("type", "")), repo.rtags())
        if err:
            rep.fail(tag, err)
        strength = str(r.get("strength", "")).strip()
        if strength and strength not in STRENGTHS:
            rep.fail(tag, f"联系强度只能是 强/中/弱：{strength}")
        rcode = normalize_tag(str(r.get("type", "")))[0]
        default = R_DEFAULT_STRENGTH.get(rcode)
        if default and strength and strength == "强" and default != "强":
            rep.warn(tag, f"{rcode} 默认强度为{default}，标为“强”需在 reason 给出证据（弱关系不得伪装强关系）")
        if str(r.get("priority", "")).strip() not in PRIORITIES:
            rep.fail(tag, f"复盘优先级只能是 高/中/低：{r.get('priority')}")
        reason = str(r.get("reason", "")).strip()
        if len(reason) < 10:
            rep.fail(tag, "关联原因过短，不能是空话")
        elif TEMPLATE_ONLY_RELATION_REASON_RE.fullmatch(reason):
            rep.fail(tag, "关联原因只是字段模板；必须说明两题共享的具体机制、问法入口、错误模式或上下游依赖")
    if rels and not any(c.startswith("关系候选") and l == "FAIL" for l, c, _ in rep.items):
        rep.ok("关系候选", f"{len(rels)} 条均通过端点/类型/强度校验")
    if not rels:
        rep.ok("关系候选", "0 条（不强行连边，合规）")

    # ---- 专题链候选
    chains = repo.topic_chains()
    for c in pkg.get("topic_chain_candidates") or []:
        hit = [f for f in chains if f.startswith(str(c))]
        if not hit:
            rep.fail("专题链候选", f"{c} 不存在于 专题链/（现有：{[f.split(' ')[0] for f in chains]}）")
        else:
            rep.ok("专题链候选", f"{c} → {hit[0]}（apply 不自动写链，需按复盘顺序人工插入）")

    # ---- 复习单元
    ru = pkg.get("review_unit")
    if ru is not None:
        if not str(ru.get("内容名称", "")).strip():
            rep.warn("复习单元", "未给 内容名称，apply 将默认用核心考点")
        if str(ru.get("难度等级", "")).strip() and not str(ru["难度等级"]).strip()[0] in "ABC":
            rep.warn("复习单元", f"难度等级建议 A基础/B中等/C提高：{ru.get('难度等级')}")

    # ---- 答案泄露扫描（所有文本字段 + 附件登记值）
    leak_texts = [(f, str(pkg.get(f, ""))) for f in TEXT_FIELDS_FOR_LEAK]
    leak_texts += [(f"attachment_registry.{k}", str(v)) for k, v in ar.items()]
    leak_texts += [(f"relation.reason#{i+1}", str(r.get("reason", ""))) for i, r in enumerate(rels)]
    if ru:
        leak_texts += [(f"review_unit.{k}", str(v)) for k, v in ru.items()]
    any_leak = False
    for name, text in leak_texts:
        fails, warns = scan_leak(text)
        for f in fails:
            rep.fail(f"答案泄露({name})", f)
            any_leak = True
        for w in warns:
            rep.warn(f"答案泄露({name})", w)
    if not any_leak:
        rep.ok("答案泄露扫描", "未发现答案结论/正确选项/选项字母组合模式")

    return rep


def main():
    ap = argparse.ArgumentParser(description="408 单题入库前置校验（只读）")
    ap.add_argument("package", nargs="?", help="intake_package.json 路径")
    ap.add_argument("--repo", default=".", help="kaoyan-408 仓库根")
    ap.add_argument("--details-root", default=DETAILS_ROOT_DEFAULT)
    ap.add_argument("--suggest-id", metavar="SUBJECT", help="只输出下一个可用正式 ID")
    ap.add_argument("--year", default="UNK", help="配合 --suggest-id")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    args = ap.parse_args()

    repo = Repo(args.repo)
    if args.suggest_id:
        print(suggest_id(repo, args.suggest_id, args.year))
        return

    if not args.package:
        ap.error("需要 intake 包路径或 --suggest-id")
    pkg = load_package(args.package)
    rep = run_preflight(pkg, repo, Path(args.details_root))
    rep.print_json() if args.json else rep.print_text()
    sys.exit(1 if rep.failed else 0)


if __name__ == "__main__":
    main()
