#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
比较法（市场比较法）—— 零依赖实现。

对应 Skill：skills/comparison-pricing/SKILL.md
对应知识底座：knowledge/experts/rep-001/blocks/07-practice-methods.md

设计要点（针对评审"降级路径没有方法论"）：
  1. **调整率有锚点**：优先从 hedonic 属性隐含价格取值，而不是估价师经验数字。
     这是特征价格模型在实务中最被忽视、却最实用的用途。
  2. **只调差异，不调水平**（关键正确性约束）：
        调整率 = 溢价(估价对象类别) − 溢价(案例类别)
     案例与估价对象同朝向同楼层 → 调整为 0。
     ⚠️ 曾把目标溢价**无条件套用到所有案例**，导致同质案例被凭空抬高、结果系统性失真。
  3. **幅度纪律硬编码**：单项 ±20%、累计 ±30%、离散度 CV ≤10%，超出即报警。
  4. **强制区间输出**：禁止单点断言（对应质量硬门 interval-required）。
  5. **两类修正分开**：交易情况与市场状况用**连乘系数**，区位/实物/权益差异用**连加百分率**。

用法：
    python3 comparison_model.py --cases sample_cases.csv --as-of 2026-09 \
        --target-area 100 --target-orientation 南北 --target-floor 高 \
        --hedonic-coefs "朝向=南北:+0.068,朝向=南:+0.040,朝向=东南:+0.010,楼层=高:+0.092,楼层=中:+0.020,面积:+0.00059"
"""

import argparse
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for _c in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _c not in sys.path:
        sys.path.insert(0, _c)

from core import read_csv, to_float, mean  # noqa: E402

# ---------------------------------------------------------------- 铁律阈值
MAX_ITEM_ADJ = 0.20        # 单项调整幅度上限 ±20%
MAX_TOTAL_ADJ = 0.30       # 累计调整幅度上限 ±30%
MAX_CASE_CV = 0.10         # 调整后单价离散度上限 CV 10%
MIN_CASES = 3              # 最少可比案例数
MAX_AGE_MONTHS = 12        # 案例时效上限（月）

WEIGHT_BY_GROUP = {
    "same_community_same_layout": 1.0,
    "same_community": 0.7,
    "same_district": 0.4,
    "same_city": 0.15,
}

EXCLUDE_FLAGS = {
    "法拍": "法拍房属强制处置子市场，价格含折价，不得与普通交易混合",
    "亲属": "亲属/关联方间转让，非市场交易",
    "关联": "关联方交易，价格非市场化",
    "抵债": "抵债房，价格含债务安排因素",
    "限价": "限价/摇号新房备案价为行政定价，非市场价",
    "共有产权": "共有产权房产权受限",
    "保障房": "保障房产权与退出受限",
    "急售": "急售，价格含时间折价",
}


# ---------------------------------------------------------------- 案例
class Case:
    def __init__(self, cid, unit_price, fields=None, flags=None, group="same_community"):
        self.id = cid
        self.unit_price = unit_price
        self.fields = fields or {}
        self.flags = [f for f in (flags or []) if f]
        self.group = group
        self.weight = WEIGHT_BY_GROUP.get(group, 0.4)
        self.adjusted = None
        self.adjustments = []
        self.add_total = 0.0
        self.add_issues = []
        self.adj_zero = []
        self.excluded_reason = None

    def __repr__(self):
        return "<Case %s %.0f %s>" % (self.id, self.unit_price or 0, self.group)


def load_cases(path, field_map=None):
    fm = field_map or {}
    hdr, rows = read_csv(path)
    eff = {
        "id": fm.get("id", "案例编号"), "price": fm.get("price", "成交单价"),
        "date": fm.get("date", "成交日期"), "area": fm.get("area", "面积"),
        "layout": fm.get("layout", "户型"), "floor": fm.get("floor", "楼层"),
        "orientation": fm.get("orientation", "朝向"), "age": fm.get("age", "房龄"),
        "community": fm.get("community", "小区"), "flags": fm.get("flags", "标记"),
        "group": fm.get("group", "可比性分组"),
    }
    cases = []
    for i, r in enumerate(rows):
        cid = (r.get(eff["id"]) or "C%02d" % (i + 1)).strip()
        price = to_float(r.get(eff["price"]))
        if price is None:
            continue
        raw_flags = (r.get(eff["flags"]) or "").strip()
        flags = [f.strip() for f in
                 raw_flags.replace("、", "/").replace("，", "/").replace(",", "/").split("/")
                 if f.strip()]
        grp = (r.get(eff["group"]) or "same_community").strip() or "same_community"
        cases.append(Case(cid, price, fields={
            "date": (r.get(eff["date"]) or "").strip(),
            "area": to_float(r.get(eff["area"])),
            "layout": (r.get(eff["layout"]) or "").strip(),
            "floor": (r.get(eff["floor"]) or "").strip(),
            "orientation": (r.get(eff["orientation"]) or "").strip(),
            "age": to_float(r.get(eff["age"])),
            "community": (r.get(eff["community"]) or "").strip(),
            "flags_raw": raw_flags,
        }, flags=flags, group=grp))
    if not cases:
        raise SystemExit("✗ 未从 %s 读到任何可比案例（检查列名：案例编号 / 成交单价）" % path)
    return cases


def screen(cases, as_of=None, max_age_months=None):
    kept, excluded = [], []
    lim = max_age_months if max_age_months is not None else MAX_AGE_MONTHS
    for c in cases:
        reason = None
        for f in c.flags:
            for key, msg in EXCLUDE_FLAGS.items():
                if key in f:
                    reason = "[%s] %s" % (key, msg)
                    break
            if reason:
                break
        if reason is None and c.unit_price is not None and not (1e2 <= c.unit_price <= 1e6):
            reason = "单价 %.0f 超出合理量级（1e2–1e6 元/㎡），疑口径错误" % c.unit_price
        if reason is None and as_of and c.fields.get("date"):
            gap = _month_gap(c.fields["date"], as_of)
            if gap is not None and gap > lim:
                reason = ("成交日 %s 距基准日 %s 已 %d 个月，超过时效上限 %d 个月"
                          % (c.fields["date"], as_of, gap, lim))
        if reason:
            c.excluded_reason = reason
            excluded.append(c)
        else:
            kept.append(c)
    return kept, excluded


def _parse_ym(s):
    if not s:
        return None
    parts = s.strip().replace("/", "-").replace(".", "-").split("-")
    try:
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 7)
    except Exception:
        return None


def _month_gap(d1, d2):
    a, b = _parse_ym(d1), _parse_ym(d2)
    if a is None or b is None:
        return None
    return abs((b[0] - a[0]) * 12 + (b[1] - a[1]))


# ---------------------------------------------------------------- hedonic 系数解析
def parse_hedonic_coefs(s):
    """
    解析 hedonic 输出，为比较法提供**调整率锚点**。

    格式（多项用逗号 / 分号分隔）：
        分组=类别:+百分比      → 组内某类别的溢价，如 "朝向=南北:+0.068"
        分组:+百分比           → 组的标量系数，如 "面积:+0.00059"

    返回 (groups, scalars, warns)
    """
    groups, scalars, warns = {}, {}, []
    if not s:
        return groups, scalars, warns
    for part in s.replace("；", ";").replace("，", ",").replace(";", ",").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        left, val = part.rsplit(":", 1)
        try:
            v = float(val.strip())
        except ValueError:
            warns.append("无法解析系数值：%s" % part)
            continue
        if "=" in left:
            g, cat = left.split("=", 1)
            groups.setdefault(g.strip(), {})[cat.strip()] = v
        else:
            scalars[left.strip()] = v
    return groups, scalars, warns


def _diff_adjustment(group, table, target_value, case_value, out_warns, case_id):
    """
    调整率 = 溢价(估价对象类别) − 溢价(案例类别)。

    这是"只调差异、不调水平"原则的实现：
      · 两者类别相同 → 调整 0（无论该类别溢价多高）
      · 类别不同 → 取两者溢价之差；未在系数表中的类别按基准类别 0 处理并给出提示
    """
    if not table:
        return None
    tv, cv = (target_value or "").strip(), (case_value or "").strip()
    if not tv:
        return None
    if tv == cv:
        return {"item": "%s（估价对象与案例同为 %s）" % (group, tv), "pct": 0.0,
                "basis": "同类不调整（只调差异不调水平）"}
    if tv not in table:
        out_warns.append("%s：估价对象类别「%s」不在 %s 系数表中，按基准类别 0 处理"
                         "（建议补齐系数以避免漏调）" % (case_id, tv, group))
    if cv and cv not in table:
        out_warns.append("%s：案例类别「%s」不在 %s 系数表中，按基准类别 0 处理"
                         % (case_id, cv, group))
    pt = table.get(tv, 0.0)
    pc = table.get(cv, 0.0) if cv else 0.0
    return {"item": "%s（估价对象 %s ← 案例 %s）" % (group, tv, cv or "未知"),
            "pct": pt - pc,
            "basis": "溢价差：估价对象 %+.2f%% − 案例 %+.2f%%" % (pt * 100, pc * 100)}


# ---------------------------------------------------------------- 调整
def adjust_case(case, market_factor=1.0, transaction_factor=1.0,
                location=None, physical=None, rights=None, allow_oversize=False):
    detail = [
        ("交易情况修正", "系数 %.4f" % transaction_factor,
         "非市场因素剔除" if transaction_factor != 1.0 else "正常交易，不修正", "连乘"),
        ("市场状况调整（期日）", "系数 %.4f" % market_factor,
         "按指数调整至基准日" if market_factor != 1.0 else "未提供指数，未调整", "连乘"),
    ]
    add_total, issues = 0.0, []
    for label, items in (("区位", location), ("实物", physical), ("权益", rights)):
        for it in (items or []):
            pct = float(it.get("pct", 0.0))
            add_total += pct
            detail.append(("%s·%s" % (label, it.get("item", "未命名")),
                           "%+.2f%%" % (pct * 100),
                           it.get("basis") or "**未提供依据**", "连加"))
            if abs(pct) > MAX_ITEM_ADJ and not allow_oversize:
                issues.append("单项调整 %+.1f%% 超过 ±%.0f%% 上限：%s/%s（应重选案例）"
                              % (pct * 100, MAX_ITEM_ADJ * 100, label, it.get("item")))
    if abs(add_total) > MAX_TOTAL_ADJ and not allow_oversize:
        issues.append("累计调整 %+.1f%% 超过 ±%.0f%% 上限（应重选案例）"
                      % (add_total * 100, MAX_TOTAL_ADJ * 100))

    case.adjusted = case.unit_price * transaction_factor * market_factor * (1.0 + add_total)
    case.adjustments = detail
    case.add_total = add_total
    case.add_issues = issues
    return case.adjusted, detail, issues


# ---------------------------------------------------------------- 合成
def weighted_median(pairs):
    pairs = sorted([(v, w) for v, w in pairs if v is not None and w > 0])
    if not pairs:
        return None
    tot, acc = sum(w for _, w in pairs), 0.0
    for v, w in pairs:
        acc += w
        if acc >= tot / 2.0:
            return v
    return pairs[-1][0]


def weighted_quantile(pairs, q):
    pairs = sorted([(v, w) for v, w in pairs if v is not None and w > 0])
    if not pairs:
        return None
    tot, acc = sum(w for _, w in pairs), 0.0
    for v, w in pairs:
        acc += w
        if acc >= tot * q:
            return v
    return pairs[-1][0]


def weighted_mean(pairs):
    num = sum(v * w for v, w in pairs if v is not None)
    den = sum(w for v, w in pairs if v is not None)
    return (num / den) if den else None


def dispersion(values):
    v = [x for x in values if x is not None]
    if len(v) < 2:
        return {"n": len(v), "cv": None, "ok": len(v) >= MIN_CASES}
    m = mean(v)
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / len(v))
    cv = sd / m if m else None
    return {"n": len(v), "mean": m, "sd": sd, "cv": cv, "min": min(v), "max": max(v),
            "ok": (cv is not None and cv <= MAX_CASE_CV)}


# ---------------------------------------------------------------- 主流程
def run(cases, target, as_of=None, market_factor=1.0, hedging=None,
        hedonic_coefs="", allow_oversize=False):
    kept, excluded = screen(cases, as_of=as_of)
    groups, scalars, parse_warns = parse_hedonic_coefs(hedonic_coefs)
    target = target or {}
    metric_warns = []

    area_coef = None
    for k, v in scalars.items():
        if "面积" in k:
            area_coef = v

    pairs, issues_all = [], []
    for c in kept:
        loc, phy, rts = [], [], []
        for gname, tkey, ckey, bucket in (("朝向", "orientation", "orientation", loc),
                                          ("楼层", "floor", "floor", phy)):
            table = groups.get(gname)
            if table:
                it = _diff_adjustment(gname, table, target.get(tkey),
                                      c.fields.get(ckey), metric_warns, c.id)
                if it is None:
                    continue
                if abs(it["pct"]) > 1e-12:
                    bucket.append(it)
                else:
                    c.adj_zero.append(it)
        if area_coef is not None:
            if target.get("area") and c.fields.get("area"):
                da = target["area"] - c.fields["area"]
                phy.append({
                    "item": "面积（估价对象 %.1f㎡ ← 案例 %.1f㎡）"
                            % (target["area"], c.fields["area"]),
                    "pct": area_coef * da,
                    "basis": "hedonic 面积系数 %.5f × 面积差 %.1f㎡（半对数系数的直接换算）"
                             % (area_coef, da)})
            else:
                metric_warns.append("%s：提供了面积系数但缺少 --target-area 或案例面积，面积未调整"
                                    % c.id)

        adj, _detail, issues = adjust_case(
            c, market_factor=market_factor,
            transaction_factor=(hedging or {}).get(c.id, 1.0),
            location=loc, physical=phy, rights=rts, allow_oversize=allow_oversize)
        issues_all.extend(["%s：%s" % (c.id, i) for i in issues])
        pairs.append((adj, c.weight))

    if len(kept) < MIN_CASES:
        return {"ok": False,
                "reason": "可比案例仅 %d 个，低于最低要求 %d 个——**不具备比较法条件，拒绝出结论**"
                          % (len(kept), MIN_CASES),
                "excluded": [(c.id, c.excluded_reason) for c in excluded]}

    disp = dispersion([p[0] for p in pairs])
    point = weighted_median(pairs)
    alt = weighted_mean(pairs)
    lo, hi = weighted_quantile(pairs, 0.25), weighted_quantile(pairs, 0.75)

    notes = list(parse_warns)
    seen = set()
    for w in metric_warns:
        if w not in seen:
            notes.append(w)
            seen.add(w)
    if disp.get("cv") is not None and disp["cv"] > MAX_CASE_CV:
        notes.append("调整后离散度 CV %.1f%% 超过 %.0f%% 上限——**应先回到第 1 步重选案例**，"
                     "本结果仅供参考" % (disp["cv"] * 100, MAX_CASE_CV * 100))
    if point and alt and abs(point - alt) / point > 0.03:
        notes.append("加权中位数 %.0f 与加权均值 %.0f 相差 %.1f%%（>3%），存在影响点，须说明"
                     % (point, alt, abs(point - alt) / point * 100))
    if issues_all:
        notes.append("存在 %d 项幅度超限，须重选案例或显式披露超限理由" % len(issues_all))
    if not groups and not scalars:
        notes.append("**未提供 hedonic 属性隐含价格**：本次调整率没有量化锚点。"
                     "建议先跑 hedonic-pricing 取得系数再回填——否则调整率等同于估价师经验值，"
                     "可审计性显著下降。")

    return {
        "ok": True, "as_of": as_of, "kept": kept,
        "excluded": [(c.id, c.excluded_reason) for c in excluded],
        "pairs": pairs, "point_weighted_median": point, "alt_weighted_mean": alt,
        "interval": [lo, hi], "full_range": [disp.get("min"), disp.get("max")],
        "dispersion": disp, "issues": issues_all, "notes": notes,
        "hedonic_groups": groups, "hedonic_scalars": scalars,
        "caliber": "可比案例成交价口径 → 差异修正调整 → 估价对象推算值",
        "mark": "测算",
    }


# ---------------------------------------------------------------- 输出
def print_result(res):
    if not res.get("ok"):
        print("✗ %s" % res["reason"])
        if res.get("excluded"):
            print("\n被排除的案例：")
            for cid, why in res["excluded"]:
                print("  - %s：%s" % (cid, why))
        return 1

    print("=" * 78)
    print("比较法测算结果")
    print("=" * 78)
    print("基准日：%s" % (res["as_of"] or "未指定"))
    print("口径：%s（标注级：%s）" % (res["caliber"], res["mark"]))
    print()

    print("① 可比案例与权重")
    print("  %-8s %11s %11s %9s %11s %-24s %6s"
          % ("案例", "调整前", "调整后", "连加小计", "净变动", "可比性分组", "权重"))
    print("  " + "-" * 82)
    for c, (val, w) in zip(res["kept"], res["pairs"]):
        # 「净变动」= 调整后 / 调整前 − 1，**含连乘的期日与交易情况系数**。
        # 「连加小计」仅为区位/实物/权益三项之和——两者不可混为"累计调整"，
        # 否则会漏报期日系数的影响（曾误标为「累计调整」，低估净变动）。
        net = (val / c.unit_price - 1.0) if c.unit_price else 0.0
        print("  %-8s %11.0f %11.0f %+8.2f%% %+10.2f%% %-24s %6.2f"
              % (c.id, c.unit_price, val, c.add_total * 100, net * 100, c.group, w))
    print()
    print("  注：净变动 = 调整后 / 调整前 − 1，含连乘的交易情况与市场状况系数；")
    print("      连加小计仅为区位/实物/权益三项差异调整之和。")

    print("② 逐项调整明细（示例：%s）" % res["kept"][0].id)
    for name, val, basis, kind in res["kept"][0].adjustments:
        print("  [%-4s] %-40s %-12s %s" % (kind, name, val, basis))
    for it in res["kept"][0].adj_zero:
        print("  [零调整] %-40s %-12s %s" % (it["item"], "0.00%", it["basis"]))
    print()

    print("③ 合规检查")
    d = res["dispersion"]
    print("  离散度 CV：%s（上限 %.0f%%）→ %s"
          % ("%.2f%%" % (d["cv"] * 100) if d.get("cv") is not None else "样本不足",
             MAX_CASE_CV * 100, "通过" if d.get("ok") else "**不通过**"))
    print("  单项上限 ±%.0f%%　累计上限 ±%.0f%%" % (MAX_ITEM_ADJ * 100, MAX_TOTAL_ADJ * 100))
    if res["issues"]:
        for i in res["issues"]:
            print("    ⚠ %s" % i)
    else:
        print("    全部案例幅度合规")
    if res["excluded"]:
        print("  已排除 %d 个案例：" % len(res["excluded"]))
        for cid, why in res["excluded"]:
            print("    - %s：%s" % (cid, why))
    print()

    print("④ 结果")
    print("  加权中位数（主）：%.0f 元/㎡" % res["point_weighted_median"])
    print("  加权均值（对照）：%.0f 元/㎡" % res["alt_weighted_mean"])
    print("  四分位区间　　：%.0f ~ %.0f 元/㎡" % (res["interval"][0], res["interval"][1]))
    print("  全距　　　　　：%.0f ~ %.0f 元/㎡" % (res["full_range"][0], res["full_range"][1]))
    print()
    if res["notes"]:
        print("⑤ 注意事项（必须写入报告）")
        for n in res["notes"]:
            print("  · %s" % n)
    return 0


def main():
    ap = argparse.ArgumentParser(description="比较法（市场比较法）零依赖实现")
    ap.add_argument("--cases", required=True, help="可比案例 CSV")
    ap.add_argument("--as-of", default=None, help="估价基准日，如 2026-09")
    ap.add_argument("--target-area", type=float, default=None)
    ap.add_argument("--target-layout", default=None)
    ap.add_argument("--target-floor", default=None)
    ap.add_argument("--target-orientation", default=None)
    ap.add_argument("--market-factor", type=float, default=1.0,
                    help="市场状况调整系数 = 基准日指数 / 案例成交日指数")
    ap.add_argument("--hedonic-coefs", default="",
                    help='hedonic 系数，如 "朝向=南北:+0.068,朝向=南:+0.040,楼层=高:+0.092,面积:+0.00059"')
    ap.add_argument("--allow-oversize", action="store_true",
                    help="允许单项/累计幅度超限（须在报告中披露理由）")
    ap.add_argument("--max-age-months", type=int, default=MAX_AGE_MONTHS)
    args = ap.parse_args()

    cases = load_cases(args.cases)
    target = {"area": args.target_area, "layout": args.target_layout,
              "floor": args.target_floor, "orientation": args.target_orientation}
    res = run(cases, target, as_of=args.as_of, market_factor=args.market_factor,
              hedonic_coefs=args.hedonic_coefs, allow_oversize=args.allow_oversize)
    return print_result(res)


if __name__ == "__main__":
    sys.exit(main())
