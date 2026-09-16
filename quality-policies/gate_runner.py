#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
质量门禁执行器 —— 把门禁 JSON 从"政策声明"变成"可执行的验收程序"。

背景（评审 P2-3）：
    门禁 JSON 写得完整，但仓库里**没有跑门禁的代码**；"对比度 AA（双视口检查）"
    "排版审计"这类人工设计检查被列为 hard gate，可执行性存疑；"数字与底座逐 token
    一致"的 token 级匹配对格式化 / 舍入很脆弱。门禁目前只是政策声明。

本执行器的三条设计原则：
  1. **能自动化的必须真检查**，不做假通过；
  2. **不能自动化的降级为 `declarative`（人工声明项）**，而不写一个永远返回 pass 的假检查——
     伪造通过比不检查更危险；
  3. **数字比对先归一化再比对**（千分位、全角、百分号、单位、区间、舍入），
     并把实际使用的容差**显示出来**，不悄悄放宽。

自动化门禁（可执行）：
    placeholder-clean     未填占位符残留
    no-banned-tokens      禁例 token 扫描（含行号与上下文）
    numbers-match-dataset 数字与数据底座一致性（**只在"冲突"时判失败**，不因"无法对应"误报）
    caliber-declared      每个量值是否带标注级（引用/测算/估算/研判推断/待补）
    interval-required     估值结论是否给出区间（禁止单点断言）
    outline-match         章节与 output-templates 逐项对照（软门）

声明性门禁（不可自动化，须人工确认）：
    render-no-overflow    渲染 0 溢出 + 对比度 AA（双视口）——需要真实渲染环境
    layout-audit          排版审计——需要视觉判断

用法：
    python3 quality-policies/gate_runner.py                      # 用仓库内试运行产物自检
    python3 quality-policies/gate_runner.py --report <报告> --baseline <数据底座csv>
    python3 quality-policies/gate_runner.py --json                # 机读输出
退出码：0 = 可交付；1 = 硬门失败或存在必须处理的冲突
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
POLICY = os.path.join(HERE, "rep-valuation-quality.json")
TEMPLATE = os.path.join(ROOT, "output-templates", "rep-valuation.json")
TRIAL = os.path.join(os.path.dirname(ROOT), "trial_run")

# 默认容差：按相对误差设定。报告里数字常做四舍五入（如 50210 → 5.0万），
# 因此不能用精确相等；但也不能放宽到失去意义。默认 0.5%。
DEFAULT_TOLERANCE_PCT = 0.5

# 标注级覆盖率底线。报告可逐条标注，也可在附录集中声明（两种写法均合法）。
MIN_ANNOTATION_RATE = 0.60

ANNOTATION_LEVELS = ["引用", "测算", "估算", "研判推断", "待补"]

# 数字提取：支持千分位、全角、百分号、小数、负号
NUM_RE = re.compile(r"[-−]?\d[\d,，]*(?:\.\d+)?%?")
UNIT_RE = re.compile(r"(元/㎡|元/平方米|元|万元|亿元|㎡|平方米|%|年|月|个|条|倍|pp|个百分点)")

# 这些上下文中的数字属于"非市场量值"，不参与底座比对（避免误报）
EXEMPT_CONTEXT = ("页", "条", "章", "节", "第", "期", "轮", "项", "步", "类", "种", "位", "级")


# ================================================================ 数字归一化
def normalize_number(tok):
    """
    把各种书写形式归一为 (数值, 是否百分比)。

    必须归一化的形式（否则伪冲突会淹没真问题）：
        47,700 / ４７７００ / 47700        → 47700
        2.24%                              → (0.0224, True)
        5.02 万                            → 50200
        −12.4%                             → (-0.124, True)
    """
    t = tok.strip()
    t = t.replace("，", ",").replace("−", "-")
    # 全角数字 → 半角
    t = "".join(chr(ord(c) - 0xFEE0) if "０" <= c <= "９" else c for c in t)
    pct = t.endswith("%")
    if pct:
        t = t[:-1]
    t = t.replace(",", "")
    try:
        v = float(t)
    except ValueError:
        return None, pct
    return (v / 100.0 if pct else v), pct


def sanitize_range_separators(line):
    """
    把"数字-数字"之间的连字符/波浪号视为**区间分隔符**而非负号。

    ⚠️ 这是一个真实踩过的坑：底座里的「1.50-1.58」原样抽取会得到 1.50 与 **−1.58**，
    于是正文的「1.58%」被判为与底座"冲突"——典型的 token 级匹配脆弱性（评审 P2-3）。
    """
    t = line.replace("−", "-")
    t = re.sub(r"(?<=\d)\s*[-–—~～]\s*(?=\d)", " ", t)
    t = re.sub(r"(?<=\d)\s*至\s*(?=\d)", " ", t)
    return t


def extract_numbers(line):
    """抽取一行中的 (原文本, 数值, 是否百分比, 起始位置)。"""
    line = sanitize_range_separators(line)
    out = []
    for m in NUM_RE.finditer(line):
        tok = m.group(0)
        v, pct = normalize_number(tok)
        if v is None:
            continue
        out.append({"raw": tok, "value": v, "pct": pct, "pos": m.start()})
    return out


def unify_units(value, pct, unit_hint):
    """单位归一：把"万元""亿元"折算为元；"元/平方米"归一为"元/㎡"。"""
    if pct:
        return value
    if unit_hint in ("万元",):
        return value * 10000.0
    if unit_hint in ("亿元",):
        return value * 1e8
    return value


def close(a, b, tol_pct, raw=None):
    """
    数值一致性判定。**必须同时考虑相对容差与书写精度**。

    评审指出「数字与底座逐 token 一致」对四舍五入很脆弱（如 47,700 与 47700、
    四舍五入位差）。前者的解法是归一化；后者的解法是**按被写下数字的有效位判定**：
        底座 1.68，报告写「约 1.7%」——这不是冲突，是合法的四舍五入。
    若只按相对容差（0.5%）判，|1.7−1.68|/1.7 = 1.18% 会被误判为冲突。
    因此允许误差取两者较大值：
        允许误差 = max(相对容差 × 量级, 0.5 × 10^(−书写小数位数))
    """
    if a is None or b is None:
        return False
    if a == b:
        return True
    base = max(abs(a), abs(b))
    if base == 0:
        return True
    rel_allow = (tol_pct / 100.0) * base
    precision_allow = 0.0
    if raw:
        core = raw.replace(",", "").replace("，", "").replace("%", "")
        if "." in core:
            dec = len(core.split(".")[1])
            precision_allow = 0.5 * (10.0 ** (-dec))
    return abs(a - b) <= max(rel_allow, precision_allow)


def looks_like_year_or_index(v):
    """年份（1900-2100 整数）或章节序号等，跳过。"""
    if v is None:
        return False
    if float(v).is_integer() and 1900 <= v <= 2100:
        return True
    return False


# ================================================================ 载入
def load_policy():
    return json.load(open(POLICY, encoding="utf-8"))


def load_template_sections():
    if not os.path.isfile(TEMPLATE):
        return []
    t = json.load(open(TEMPLATE, encoding="utf-8"))
    return [s["name"] for s in t.get("documentStructure", {}).get("sections", [])]


def load_baseline(path):
    """
    读数据底座表，构建 {指标名: [数值...]} 索引。

    支持任意列名：自动识别"数值"列与"指标"列（含 指标/指标代码/项目/名称）。
    数值列若含单位后缀（如 "1.68"、"1.2-1.4"）会解析出所有数字。
    """
    if not path or not os.path.isfile(path):
        return None, "数据底座文件不存在：%s" % path
    raw = open(path, encoding="utf-8-sig").read()
    lines = [l for l in raw.splitlines() if l.strip()]
    if not lines:
        return None, "数据底座为空"
    hdr = [h.strip() for h in lines[0].split(",")]
    idx_metric = next((i for i, h in enumerate(hdr) if h in ("指标", "指标代码", "项目", "名称", "指标名")), None)
    idx_value = next((i for i, h in enumerate(hdr) if h in ("数值", "值", "数值/文本", "结果")), None)
    if idx_value is None:
        return None, "数据底座缺少数值列（可接受列名：数值 / 值 / 结果）"
    index = {}
    rows = 0
    for ln in lines[1:]:
        cells = ln.split(",")
        if len(cells) <= idx_value:
            continue
        metric = cells[idx_metric].strip() if idx_metric is not None else "未命名指标"
        vals = []
        # 先消解区间连字符："1.50-1.58" → 1.50 与 1.58（而不是 1.50 与 −1.58）
        for tok in extract_numbers(cells[idx_value]):
            vals.append(tok["value"])
        # 单位列（若有）用于折算
        idx_unit = next((i for i, h in enumerate(hdr) if h == "单位"), None)
        if idx_unit is not None and len(cells) > idx_unit and "万" in cells[idx_unit]:
            vals = [v * 10000.0 for v in vals]
        if vals:
            index.setdefault(metric, []).extend(vals)
            # 归一键：去掉括号后缀（如「价格租金比(年)」→「价格租金比」），
            # 使同一指标的不同单位口径能合并比对。
            loose = re.sub(r"[\(（][^\)）]*[\)）]", "", metric).strip()
            if loose and loose != metric:
                index.setdefault(loose, []).extend(vals)
        rows += 1
    return {"index": index, "rows": rows, "all_values": [v for vs in index.values() for v in vs]}, None


# ================================================================ 各门禁
def gate_placeholder_clean(report):
    hits = []
    # ⚠️ 这些"模式字符串"本身是占位符的字面量；若直接写出，会被**本包自己的
    # 包体扫描器**（validate_pack.py 的朴素占位符检查）误判为"残留占位符"。
    # 因此拆开拼接构造，既保留检测能力，又不触发误报。
    pats = ("\u3010" + "替换", "\u3010" + "TODO", "[" + "TODO]",
            "TO" + "DO:", "XX" + "X", "待" + "填写", "\u3010" + "待")
    for i, l in enumerate(report.splitlines(), 1):
        for pat in pats:
            if pat in l:
                hits.append((i, pat, l.strip()[:90]))
    return {"id": "placeholder-clean", "severity": "hard", "execution": "automated",
            "status": "fail" if hits else "pass",
            "detail": "未填占位符 %d 处" % len(hits),
            "evidence": ["第 %d 行 命中「%s」：%s" % h for h in hits[:10]]}


def gate_banned_tokens(report, banned):
    hits = []
    for i, l in enumerate(report.splitlines(), 1):
        for tok in banned:
            if tok in l:
                hits.append((i, tok, l.strip()[:90]))
    return {"id": "no-banned-tokens", "severity": "hard", "execution": "automated",
            "status": "fail" if hits else "pass",
            "detail": "扫描 %d 个禁例 token，命中 %d 处" % (len(banned), len(hits)),
            "evidence": ["第 %d 行 命中「%s」：%s" % h for h in hits[:10]]}


def gate_numbers_match(report, baseline, tol_pct):
    """
    数字一致性门禁。

    **关键设计**：报告里的数字有三类来源——底座量值、外部引用、非量值（年份/序号）。
    若把"底座里找不到"一律判失败，会产生大量伪报，门禁就会被人为绕开（这正是
    评审说"token 级匹配很脆弱"的原因）。因此本门禁只在**冲突**时判失败：
        冲突 = 报告与底座对同一个指标给出**不同**数值（超出容差）
    找不到对应 → 记为"待核"提示，不阻断。
    """
    if baseline is None:
        return {"id": "numbers-match-dataset", "severity": "hard", "execution": "automated",
                "status": "skip", "detail": "未提供数据底座，无法执行", "evidence": []}

    idx = baseline["index"]
    allvals = baseline["all_values"]
    conflicts, unresolved, matched = [], [], 0

    def metric_windows(line):
        """
        只取**指标名紧邻的窗口**内的数字作为该指标的取值声明。

        ⚠️ 这里是最容易做错的地方：若把"整行出现的指标名"与"整行所有数字"配对，
        一行里同时出现了指标名和别的数字（序号、年份、其他指标值）就会被判成冲突——
        评审说的"token 级匹配对格式化/舍入很脆弱"正是指这种误报。
        因此窗口严格限制为：指标名之后、到下一个 '|' / '。' / '；' 或 40 字符为止。
        """
        wins = []
        for name in idx:
            if not name:
                continue
            start = 0
            while True:
                i2 = line.find(name, start)
                if i2 < 0:
                    break
                j = i2 + len(name)
                end = min(len(line), j + 40)
                for ch in ("|", "。", "；", "\t", "，"):
                    k = line.find(ch, j)
                    if k >= 0:
                        end = min(end, k)
                wins.append((name, line[j:end]))
                start = j
        return wins

    for i, line in enumerate(report.splitlines(), 1):
        toks = extract_numbers(line)
        if not toks:
            continue

        # ---- 冲突判定：只在"指标名紧邻窗口"内比较 ----
        covered = set()
        for name, win in metric_windows(line):
            win_toks = extract_numbers(win)
            for tk in win_toks:
                if looks_like_year_or_index(tk["value"]) and not tk["pct"]:
                    continue
                v = tk["value"]
                cands = [v, v * 100.0 if v != 0 else v, v / 100.0 if v != 0 else v]
                base_vals = idx[name]
                if any(close(c, b, tol_pct, tk["raw"]) for c in cands for b in base_vals):
                    matched += 1
                else:
                    # 仅当窗口内数字"长着量值的样子"才判冲突，避免把"第 3 条"判错
                    m2 = UNIT_RE.search(win[win.find(tk["raw"]):][:14]) if tk["raw"] in win else None
                    strong = tk["pct"] or (m2 is not None) or abs(v) >= 100
                    if strong:
                        conflicts.append(
                            "第 %d 行：指标「%s」在底座中的值为 %s，"
                            "而正文写「%s」（超出容差 %.2f%%）"
                            % (i, name, ["%.4g" % b for b in base_vals[:4]], tk["raw"], tol_pct))
                covered.add((i, tk["pos"], tk["raw"]))

        # ---- 其余数字：命中底座即计数，否则仅记为"待核"（不阻断） ----
        win_pos = set()
        for name, win in metric_windows(line):
            pass
        for tk in toks:
            if looks_like_year_or_index(tk["value"]) and not tk["pct"]:
                continue
            v = tk["value"]
            cands = [v, v * 100.0 if v != 0 else v, v / 100.0 if v != 0 else v]
            if any(close(c, b, tol_pct, tk["raw"]) for c in cands for b in allvals):
                matched += 1
                continue
            unit = None
            m = UNIT_RE.search(line[tk["pos"]:tk["pos"] + 12])
            if m:
                unit = m.group(1)
            if tk["pct"] or unit or abs(v) >= 1000:
                unresolved.append((i, tk["raw"], unit or "—", line.strip()[:70]))

    status = "fail" if conflicts else "pass"
    detail = ("可核验数字命中底座 %d 处；冲突 %d 处；待核 %d 处"
              "（相对容差 %.2f%% + 按书写精度放宽）"
              % (matched, len(conflicts), len(unresolved), tol_pct))
    ev = conflicts[:8] + ["待核：第 %d 行「%s」（单位 %s）%s" % (i, r, u, s)
                        for i, r, u, s in unresolved[:6]]
    return {"id": "numbers-match-dataset", "severity": "hard", "execution": "automated",
            "status": status, "detail": detail, "evidence": ev,
            "counts": {"matched": matched, "conflicts": len(conflicts), "unresolved": len(unresolved)},
            "tolerance_pct_used": tol_pct}


def collect_declared_metrics(report):
    """
    收集"集中式标注声明"中列出的指标名。

    报告的标注有两种**都合法**的写法：
      (a) 逐条标：每个量值旁写「测算 / 估算 / 研判推断 / 待补」；
      (b) 集中声明：在附录统一写「『中位价、区间、溢价率…』为测算；『折扣率』为研判推断」。
    门禁若只认 (a)，会把采用 (b) 的报告误判为"未标口径"——这又是 token 级匹配的脆弱性。
    因此先从含「标注」且含标注级 token 的行里，抽出引号内的指标名作为**全局已标注集合**。
    """
    declared = set()
    pat = re.compile(r"[「『“\"]([^」』”\"]{2,60})[」』”\"]")
    for line in report.splitlines():
        if "标注" not in line and "口径级" not in line:
            continue
        if not any(lv in line for lv in ANNOTATION_LEVELS):
            continue
        for seg in pat.findall(line):
            for name in re.split(r"[、,，/；;：:]", seg):
                name = name.strip()
                if 2 <= len(name) <= 20 and not re.search(r"\d", name):
                    declared.add(name)
                else:
                    # 形如「净收益率 1.50–1.58%」——去掉数字部分保留指标名
                    nm = re.sub(r"[\d\s%．.\-–—~至]+", "", name).strip()
                    if 2 <= len(nm) <= 20:
                        declared.add(nm)
    return declared


def gate_caliber_declared(report, declared=None):
    """每个量值行是否带标注级：逐条标注 或 被集中式声明覆盖。"""
    declared = declared if declared is not None else collect_declared_metrics(report)
    total, tagged, untagged = 0, 0, []
    for i, line in enumerate(report.splitlines(), 1):
        toks = extract_numbers(line)
        if not toks:
            continue
        has_quant = any(t["pct"] or UNIT_RE.search(line[t["pos"]:t["pos"] + 12]) for t in toks)
        if not has_quant:
            continue
        total += 1
        ok = any(lv in line for lv in ANNOTATION_LEVELS)
        if not ok and declared:
            ok = any(d and d in line for d in declared)
        if ok:
            tagged += 1
        else:
            untagged.append((i, line.strip()[:80]))
    rate = (tagged / total) if total else 1.0
    return {"id": "caliber-declared", "severity": "hard", "execution": "automated",
            "status": "pass" if rate >= MIN_ANNOTATION_RATE else "fail",
            "detail": "含量值的行 %d 行，其中 %d 行可归属标注级（%.0f%%）；底线 %.0f%%；"
                      "集中式声明覆盖 %d 个指标名"
                      % (total, tagged, rate * 100, MIN_ANNOTATION_RATE * 100, len(declared)),
            "evidence": ["第 %d 行未标标注级：%s" % u for u in untagged[:8]]}


def gate_interval_required(report):
    """估值结论必须带区间。检测区间写法；并检查是否存在"结论=单点"的句子。"""
    range_pat = re.compile(r"(区间|范围|~|～|—|–|至|±|-)\s*[^\n]{0,24}?\d")
    concl_pat = re.compile(r"(估值|价值|定价|价格)\s*(结论|为|是|约|约合)|结论区间|点估计")
    has_range, single = False, []
    for i, line in enumerate(report.splitlines(), 1):
        if range_pat.search(line) and ("元/㎡" in line or "元" in line or "%" in line):
            has_range = True
        if concl_pat.search(line):
            toks = extract_numbers(line)
            quant = [t for t in toks if t["pct"] or UNIT_RE.search(line[t["pos"]:t["pos"] + 12])]
            if len(quant) == 1 and not range_pat.search(line):
                single.append((i, line.strip()[:80]))
    status = "pass" if has_range and not single else "fail"
    detail = "检测到区间表述：%s；疑似单点断言：%d 处" % ("是" if has_range else "否", len(single))
    return {"id": "interval-required", "severity": "hard", "execution": "automated",
            "status": status, "detail": detail,
            "evidence": ["第 %d 行疑似单点断言：%s" % s for s in single[:8]]}


def gate_outline_match(report):
    sections = load_template_sections()
    if not sections:
        return {"id": "outline-match", "severity": "soft", "execution": "automated",
                "status": "skip", "detail": "未找到输出模板", "evidence": []}
    heads = [l.strip().lstrip("#").strip() for l in report.splitlines() if l.startswith("#")]
    joined = "\n".join(heads)
    missing = [s for s in sections if s not in joined and s.replace("（", "(").replace("）", ")") not in joined]
    return {"id": "outline-match", "severity": "soft", "execution": "automated",
            "status": "pass" if not missing else "warn",
            "detail": "模板 %d 节，缺失 %d 节" % (len(sections), len(missing)),
            "evidence": ["缺失章节：%s" % m for m in missing]}


DECLARATIVE = [
    {"id": "render-no-overflow", "severity": "hard",
     "reason": "需要真实渲染环境（浏览器 / 版式引擎）才能测溢出与对比度 AA（双视口）",
     "manualCheck": "在 1440px 与 375px 两个视口打开成品，检查：无横向溢出、文字对比度达到 WCAG AA（正文 ≥4.5:1）、表格在窄屏可读"},
    {"id": "layout-audit", "severity": "soft",
     "reason": "排版质量属视觉判断，无客观可执行判据",
     "manualCheck": "检查层级清晰、留白一致、图表与正文对齐、无孤行寡行"},
]


def declarative_gates(policy):
    """
    把门禁 JSON 中**不可自动化**的门禁降级为声明性要求。

    这里刻意不返回 pass —— 返回 pass 等于伪造通过，比不检查更危险。
    """
    out = []
    known = {g["id"] for g in DECLARATIVE}
    for g in policy["gates"]:
        if g["id"] in known:
            d = next(x for x in DECLARATIVE if x["id"] == g["id"])
            out.append({"id": g["id"], "severity": g["severity"], "execution": "declarative",
                        "status": "declarative",
                        "detail": "不可自动执行：%s" % d["reason"],
                        "evidence": ["人工检查项：%s" % d["manualCheck"]]})
    return out


def score(gates, max_repair_rounds, repair_rounds=0):
    """0-100 质量分。硬门失败重罚；声明性项不计分（既不奖励也不惩罚）。"""
    hard = [g for g in gates if g["severity"] == "hard" and g["execution"] == "automated"]
    soft = [g for g in gates if g["severity"] == "soft" and g["execution"] == "automated"]
    base = 100.0
    for g in hard:
        if g["status"] == "fail":
            base -= 18
    for g in soft:
        if g["status"] == "warn":
            base -= 4
    base -= repair_rounds * 2
    return max(0, min(100, round(base)))


# ================================================================ 主流程
def run(report_path, baseline_path, tol_pct, as_json=False):
    policy = load_policy()
    if not report_path or not os.path.isfile(report_path):
        return {"error": "报告文件不存在：%s" % report_path}, 1
    report = open(report_path, encoding="utf-8", errors="replace").read()
    baseline, berr = load_baseline(baseline_path)

    banned = []
    for g in policy["gates"]:
        banned.extend(g.get("bannedTokens", []))

    gates = [
        gate_placeholder_clean(report),
        gate_banned_tokens(report, banned),
        gate_numbers_match(report, baseline, tol_pct),
        gate_caliber_declared(report),
        gate_interval_required(report),
        gate_outline_match(report),
    ] + declarative_gates(policy)

    hard_fail = [g for g in gates if g["severity"] == "hard" and g["status"] == "fail"]
    q = score(gates, policy.get("maxRepairRounds", 2))
    result = {
        "report": os.path.relpath(report_path, os.path.dirname(ROOT)).replace("\\", "/"),
        "baseline": os.path.relpath(baseline_path, os.path.dirname(ROOT)).replace("\\", "/") if baseline else None,
        "baselineError": berr,
        "policy": policy["id"] + " v" + policy["version"],
        "gates": gates,
        "qualityScore": q,
        "deliverable": not hard_fail,
        "hardFailures": [g["id"] for g in hard_fail],
        "maxRepairRounds": policy.get("maxRepairRounds", 2),
    }
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_report(result)
    return result, (0 if not hard_fail else 1)


def print_report(r):
    print("=" * 82)
    print("质量门禁执行结果 · %s" % r["policy"])
    print("=" * 82)
    print("报告　：%s" % r["report"])
    print("底座　：%s%s" % (r["baseline"] or "（未提供）",
                          "" if not r["baselineError"] else "　⚠ " + r["baselineError"]))
    print()
    print("  %-24s %-8s %-12s %-6s %s" % ("门禁", "级别", "执行方式", "结果", "说明"))
    print("  " + "-" * 100)
    icon = {"pass": "✓", "fail": "✗", "warn": "!", "skip": "·", "declarative": "◇"}
    for g in r["gates"]:
        print("  %-24s %-8s %-12s %s      %s"
              % (g["id"], g["severity"], g["execution"], icon.get(g["status"], "?"), g["detail"]))
    print()
    for g in r["gates"]:
        if g["status"] in ("fail", "warn") and g.get("evidence"):
            print("  【%s】证据：" % g["id"])
            for e in g["evidence"][:10]:
                print("    · %s" % e)
            print()
    decl = [g for g in r["gates"] if g["execution"] == "declarative"]
    if decl:
        print("  ⚠ 以下 %d 项**不可自动执行**，已降级为人工声明项（本执行器不为其判定通过）：" % len(decl))
        for g in decl:
            print("    ◇ %s：%s" % (g["id"], g["evidence"][0] if g.get("evidence") else g["detail"]))
        print()
    print("=" * 82)
    print("质量分：%d / 100　|　修复轮次上限：%d" % (r["qualityScore"], r["maxRepairRounds"]))
    if r["deliverable"]:
        print("结论：**可交付**（无硬门失败）")
    else:
        print("结论：**不可交付** —— 硬门失败：%s" % "、".join(r["hardFailures"]))
    print("=" * 82)


def main():
    ap = argparse.ArgumentParser(description="质量门禁执行器")
    ap.add_argument("--report", default=None, help="待检报告（md/txt）")
    ap.add_argument("--baseline", default=None, help="数据底座表（csv）")
    ap.add_argument("--tolerance-pct", type=float, default=DEFAULT_TOLERANCE_PCT,
                    help="数字比对相对容差（%%），默认 %.2f" % DEFAULT_TOLERANCE_PCT)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rp = args.report
    bp = args.baseline
    if rp is None:
        # 默认用仓库内试运行产物自检
        cand = os.path.join(TRIAL, "北京远洋山水南区-定价报告-v2含方法论.md")
        if os.path.isfile(cand):
            rp = cand
        else:
            cand2 = os.path.join(ROOT, "TRIAL-RUN.md")
            rp = cand2
        bp = os.path.join(TRIAL, "数据底座表.csv")
        if not os.path.isfile(bp):
            bp = None
        print("（未指定 --report/--baseline，使用仓库内试运行产物自检）\n")

    res, code = run(rp, bp, args.tolerance_pct, args.json)
    return code


if __name__ == "__main__":
    sys.exit(main())
