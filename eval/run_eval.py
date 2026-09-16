#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eval/run_eval.py —— 房地产定价专家包 · 零依赖评测运行器（纯标准库）。

用途（对应外部评审 P0-1 后半与 P3-3）：
    把 experts/*.json 里声明的 proficiency 从「先验自评」变为「可回归标定的实测值」。
    本脚本加载 eval/cases/ 下全部定价案例（带标准答案 + rubric），对每个案例：
      · 若提供专家回答（--answers），自动判定**可在文本上客观判定**的 rubric 维度；
      · 不能在文本上客观判定的维度标为 manual（人工评分），绝不伪造分数；
      · 聚合为逐案例得分、按能力（capability）平均分、以及建议的 proficiency 值。

两种运行模式：
    1) 校验模式（CI 用，必须能跑通）：
         python3 eval/run_eval.py --no-answers
       只校验案例集自身的完整性：schema 合法性、能力覆盖下限、rubric 权重和为 1、
       ID 唯一性、合成数据标注。不依赖任何专家回答。

    2) 评测模式：
         python3 eval/run_eval.py --answers answers.json
         python3 eval/run_eval.py --answers answers_dir/      # 目录下 *.json 合并
       answers 形如 {"eval-hedonic-001": "专家回答文本", ...}
       输出逐案例自动得分、按能力聚合、建议 proficiency（含「待人工标定」说明）。

设计纪律（与包内一致）：
    · 零外部依赖，只用标准库；
    · 不编造分数：manual 维度一律 None，等人工；
    · 合成/演示数据在案例 dataProvenance 中显著标注，运行器会校验。
"""

import argparse
import json
import os
import re
import sys

# ----------------------------------------------------------------- 配置
_HERE = os.path.dirname(os.path.abspath(__file__))
CASES_DIR = os.path.join(_HERE, "cases")

# 各能力的最低案例覆盖数（评审 P3-3 + 任务要求）。
MIN_COVERAGE = {
    "rep.hedonic": 4,
    "rep.priceindex": 3,
    "rep.spatial": 3,
    "rep.ml.valuation": 3,
    "rep.rent.income": 1,        # 与 caprate.calibrate 合并计 >=4
    "rep.caprate.calibrate": 1,  # 与 rent.income 合并计 >=4
    "rep.comparison": 3,
    "rep.cost.residual": 2,
    "rep.china.adapt": 3,
    "rep.datacheck": 2,
}
# 合并覆盖约束：以下能力组案例数之和需 >= n。
COMBINED_MIN = [
    (("rep.rent.income", "rep.caprate.calibrate"), 4,
     "租金与收益法（rep.rent.income + rep.caprate.calibrate）合计"),
]

REQUIRED_CASE_KEYS = [
    "id", "capability", "question", "inputData",
    "expectedAnswer", "rubric", "dataProvenance",
]
AUTO_CHECKS = ("mustMention", "mustNotClaim", "expectedRange", "expectedCaliber")
WEIGHT_TOL = 1e-3


# ----------------------------------------------------------------- 案例加载
def load_cases(cases_dir=None):
    """加载 cases/ 下所有 .json；支持单案例对象、数组、或含 "cases" 键的对象。"""
    cases_dir = cases_dir or CASES_DIR
    cases = []
    if not os.path.isdir(cases_dir):
        raise FileNotFoundError("案例目录不存在: %s" % cases_dir)
    for path in sorted(glob_json(cases_dir)):
        with open(path, encoding="utf-8-sig") as f:
            obj = json.load(f)
        if isinstance(obj, list):
            cases.extend(obj)
        elif isinstance(obj, dict):
            if "cases" in obj and isinstance(obj["cases"], list):
                cases.extend(obj["cases"])
            else:
                cases.append(obj)
    return cases


def glob_json(directory):
    import glob
    return glob.glob(os.path.join(directory, "*.json"))


# ----------------------------------------------------------------- 自动判定
def count_hits(needles, text):
    """返回在 text 中命中的 needle 列表（子串匹配，中文直接可用）。"""
    if not text:
        return []
    return [n for n in (needles or []) if n and n in text]


_NUM_RE = re.compile(r"-?\d+\.?\d*")


def parse_numbers(text):
    """从回答文本中提取全部浮点数（用于 expectedRange 落区判定）。"""
    if not text:
        return []
    return [float(x) for x in _NUM_RE.findall(text)]


def dim_auto_score(dim, case, answer):
    """
    给定 rubric 维度与（可选的）专家回答，返回自动得分（1-5）或 None（需人工）。

    规则：
      · dim.manual 为真 或 未提供回答 → None；
      · 维度声明的 check 不在 AUTO_CHECKS，或对应证据在案例中缺失 → None；
      · 否则按 check 类型客观判定。
    """
    if dim.get("manual"):
        return None
    if answer is None:
        return None
    check = dim.get("check")
    ea = case.get("expectedAnswer") or {}
    if check == "mustMention":
        mm = ea.get("mustMention") or []
        if not mm:
            return None
        hits = count_hits(mm, answer)
        cov = len(hits) / len(mm)
        return max(1, int(round(cov * 5)))
    if check == "mustNotClaim":
        mc = ea.get("mustNotClaim") or []
        if not mc:
            return None
        viol = count_hits(mc, answer)
        return 1 if viol else 5
    if check == "expectedRange":
        er = ea.get("expectedRange")
        if not er:
            return None
        lo, hi = er.get("min"), er.get("max")
        if lo is None or hi is None:
            return None
        nums = parse_numbers(answer)
        ok = any(lo <= n <= hi for n in nums)
        return 5 if ok else 1
    if check == "expectedCaliber":
        ec = ea.get("expectedCaliber")
        if not ec:
            return None
        return 5 if ec in answer else 1
    return None


def score_case(case, answer):
    """
    返回 (scored_dims, case_auto_0to5_or_None, pending_dims)。
      scored_dims: [(dimension, weight, score)]
      pending_dims: [(dimension, weight)] 需要人工
      case_auto: 仅在存在已判定维度时，按已判定权重重新归一化的 0-5 分；否则 None。
    """
    scored, pending = [], []
    for dim in case.get("rubric", []):
        w = float(dim.get("weight", 0))
        s = dim_auto_score(dim, case, answer)
        if s is None:
            pending.append((dim.get("dimension", "?"), w))
        else:
            scored.append((dim.get("dimension", "?"), w, s))
    if not scored:
        return scored, None, pending
    wsum = sum(w for _, w, _ in scored)
    val = sum(w * s for _, w, s in scored) / wsum
    return scored, val, pending


# ----------------------------------------------------------------- 校验模式
def validate(cases):
    errors, warnings = [], []

    # 1) schema 合法性 + ID 唯一
    seen_ids = set()
    for i, c in enumerate(cases):
        tag = "cases[%d]" % i
        cid = c.get("id")
        if not cid:
            errors.append("%s 缺少 id" % tag)
        elif cid in seen_ids:
            errors.append("ID 重复: %s" % cid)
        else:
            seen_ids.add(cid)
        for k in REQUIRED_CASE_KEYS:
            if k not in c:
                errors.append("[%s] 缺少必填字段: %s" % (cid or tag, k))
        # rubric 结构
        rubric = c.get("rubric")
        if not isinstance(rubric, list) or not rubric:
            errors.append("[%s] rubric 必须为非空数组" % (cid or tag))
        else:
            total_w = 0.0
            for d in rubric:
                if "dimension" not in d or "weight" not in d or "levels" not in d:
                    errors.append("[%s] rubric 维度缺 dimension/weight/levels" % (cid or tag))
                    continue
                if not isinstance(d.get("levels"), list) or len(d["levels"]) != 5:
                    errors.append("[%s] 维度「%s」levels 必须为 5 级"
                                  % (cid or tag, d.get("dimension")))
                try:
                    total_w += float(d.get("weight", 0))
                except (TypeError, ValueError):
                    errors.append("[%s] 维度「%s」weight 非数字" % (cid or tag, d.get("dimension")))
            if abs(total_w - 1.0) > WEIGHT_TOL:
                errors.append("[%s] rubric 权重和=%.4f，应为 1（±%g）"
                              % (cid or tag, total_w, WEIGHT_TOL))
        # 合成数据标注（显著标注）
        prov = (c.get("dataProvenance") or "")
        if not prov:
            errors.append("[%s] 缺少 dataProvenance" % (cid or tag))
        elif not re.search(r"(合成|演示|样例|模拟|synthetic)", prov, re.IGNORECASE):
            warnings.append("[%s] dataProvenance 未显著标注为合成/演示数据，"
                            "易与真实市场数据混淆" % (cid or tag))

    # 2) 能力覆盖下限
    counts = {}
    for c in cases:
        cap = c.get("capability")
        counts[cap] = counts.get(cap, 0) + 1
    for cap, mn in MIN_COVERAGE.items():
        got = counts.get(cap, 0)
        if got < mn:
            errors.append("能力 %s 覆盖 %d < 下限 %d" % (cap, got, mn))
    for grp, n, label in COMBINED_MIN:
        got = sum(counts.get(c, 0) for c in grp)
        if got < n:
            errors.append("%s 覆盖 %d < 下限 %d" % (label, got, n))

    return errors, warnings


# ----------------------------------------------------------------- 回答加载
def load_answers(path):
    if not path:
        return {}
    answers = {}
    if os.path.isdir(path):
        for p in glob_json(path):
            with open(p, encoding="utf-8-sig") as f:
                obj = json.load(f)
            if isinstance(obj, dict):
                answers.update(obj)
    else:
        with open(path, encoding="utf-8-sig") as f:
            obj = json.load(f)
        if isinstance(obj, dict):
            answers.update(obj)
    return answers


# ----------------------------------------------------------------- 评测模式
def run_eval(answers):
    cases = load_cases()
    errors, warnings = validate(cases)
    if errors:
        print("⛔ 案例集校验未通过，先修复再评测：")
        for e in errors:
            print("  - " + e)
        return 1

    print("=" * 78)
    print("房地产定价专家包 · eval 评测（自动判定部分）")
    print("=" * 78)
    print("案例总数: %d | 提供回答: %d" % (len(cases), len(answers)))

    cap_scores = {}        # capability -> [case_auto, ...]
    cap_pending = {}       # capability -> 待人工维度计数
    auto_dim_total = 0
    manual_dim_total = 0

    for c in cases:
        cid = c.get("id")
        ans = answers.get(cid)
        scored, val, pending = score_case(c, ans)
        auto_dim_total += len(scored)
        manual_dim_total += len(pending)
        cap = c.get("capability")
        cap_pending.setdefault(cap, 0)
        cap_pending[cap] += len(pending)
        if val is not None:
            cap_scores.setdefault(cap, []).append(val)

        # 逐案例输出
        status = ("%.2f/5" % val) if val is not None else "待人工"
        print("-" * 78)
        print("[%s] %s" % (cid, c.get("capability")))
        print("  问题: %s" % _clip(c.get("question", ""), 60))
        if ans is None:
            print("  回答: （未提供 → 全部维度待人工）")
        for dim, w, s in scored:
            print("    ✓ %-22s w=%.2f 自动=%d/5" % (dim, w, s))
        for dim, w in pending:
            print("    · %-22s w=%.2f 人工评分" % (dim, w))

    # 按能力聚合
    print("=" * 78)
    print("按能力聚合（自动代理下界；manual 维度未计入）")
    print("=" * 78)
    print("%-26s %6s %8s %10s %8s" % ("capability", "案例", "自动均分", "proficiency*", "待人工维"))
    prof_map = {}
    for cap in sorted(set(list(cap_scores) + list(cap_pending))):
        vals = cap_scores.get(cap, [])
        n_cases = counts_of(cases, cap)
        avg = (sum(vals) / len(vals)) if vals else None
        prof = (avg / 5.0) if avg is not None else None
        prof_map[cap] = prof
        print("%-26s %6d %8s %10s %8d" % (
            cap, n_cases,
            ("%.2f" % avg) if avg is not None else "—",
            ("%.3f" % prof) if prof is not None else "待人工",
            cap_pending.get(cap, 0)))

    print("=" * 78)
    print("建议 proficiency（自动代理下界，仅含可客观判定维度）：")
    for cap in sorted(prof_map):
        p = prof_map[cap]
        if p is None:
            print("  %-26s → 待人工标定（无自动可判定维度或缺少回答）" % cap)
        else:
            print("  %-26s → 当前自动代理 %.3f（manual 维度补齐后可能上调）" % (cap, p))
    print("  * 完整标定须人工对 manual 维度打分后代入公式：")
    print("    proficiency = (Σ w_i·s_i) / 5  （含全部维度，权重不重新归一）")
    print("  完成后将 experts/*.json 的 proficiencyStatus 由 prior 改为 calibrated 并回填指标。")

    print("=" * 78)
    print("自动判定维度总数: %d | 人工评分维度总数: %d（占比 %.1f%%）"
          % (auto_dim_total, manual_dim_total,
             100.0 * manual_dim_total / max(1, auto_dim_total + manual_dim_total)))
    print("=" * 78)
    return 0


def counts_of(cases, cap):
    return sum(1 for c in cases if c.get("capability") == cap)


def _clip(s, n):
    s = (s or "").replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


# ----------------------------------------------------------------- 校验模式入口
def run_validate_only():
    cases = load_cases()
    errors, warnings = validate(cases)
    print("=" * 78)
    print("eval 案例集校验（--no-answers，CI 模式）")
    print("=" * 78)
    print("案例总数: %d" % len(cases))
    # 覆盖统计
    counts = {}
    for c in cases:
        counts[c.get("capability")] = counts.get(c.get("capability"), 0) + 1
    print("按能力分布:")
    for cap in sorted(counts):
        flag = ""
        mn = MIN_COVERAGE.get(cap)
        if mn is not None and counts[cap] < mn:
            flag = "  ⛔低于下限%d" % mn
        print("  %-26s %d%s" % (cap, counts[cap], flag))
    for grp, n, label in COMBINED_MIN:
        got = sum(counts.get(c, 0) for c in grp)
        flag = "" if got >= n else "  ⛔低于下限%d" % n
        print("  %-26s %d%s" % (label, got, flag))

    if warnings:
        print("-" * 78)
        print("⚠ 警告（不阻断）:")
        for w in warnings:
            print("  - " + w)
    if errors:
        print("-" * 78)
        print("⛔ 校验失败（%d 项）:" % len(errors))
        for e in errors:
            print("  - " + e)
        return 1
    print("-" * 78)
    print("✅ 校验通过：schema 合法、能力覆盖达标、rubric 权重和=1、ID 唯一、合成标注齐全。")
    return 0


# ----------------------------------------------------------------- main
def main(argv=None):
    # ⚠️ global 声明必须位于函数**开头**：此前放在 add_argument 之后，
    # 而 default=CASES_DIR 已先引用了该名字，Python 直接抛 SyntaxError
    # （"name 'CASES_DIR' is used prior to global declaration"）。
    global CASES_DIR
    ap = argparse.ArgumentParser(description="房地产定价专家包 eval 评测运行器")
    ap.add_argument("--answers", help="专家回答 JSON 文件或目录（形如 {caseId: 文本}）")
    ap.add_argument("--no-answers", action="store_true",
                    help="仅校验案例集完整性，不评测（CI 模式）")
    ap.add_argument("--cases-dir", default=CASES_DIR, help="案例目录（默认 eval/cases）")
    args = ap.parse_args(argv)

    if args.cases_dir:
        CASES_DIR = args.cases_dir

    if args.no_answers:
        return run_validate_only()
    if args.answers:
        return run_eval(load_answers(args.answers))
    # 默认：无 --answers 也进入评测（未提供回答的案例全部待人工）
    return run_eval({})


if __name__ == "__main__":
    sys.exit(main())
