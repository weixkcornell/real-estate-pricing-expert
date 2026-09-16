#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cap Rate 标定器 —— 从租金与价格数据推导城市 / 物业类型的资本化率。

解决评审 P2-1 第三点：
    "Cap Rate『按城市/物业类型分别标定』是正确原则，但中国缺乏透明的 cap rate
     序列，Skill 未给出从数据推导城市/类型 cap rate 的估计方法。"

中国没有公开、连续、分城市分类型的 cap rate 序列。因此本工具的输出**永远是推导值，
不是引用值**——报告中必须标「测算」或「估算」，不得把结果写成"某城市写字楼
cap rate 为 X%"这类看似有权威来源的数字。

两条推导路径（本工具都实现）：
  ① **匹配价格租金比倒数**（主路径）
        毛资本化率 = 年租金 / 价格
        净资本化率 = 毛资本化率 × (1 − 运营成本率)
     「匹配」的含义：用**同小区 / 同户型配对**的租金与价格，消除结构差异
     （直接比较不同标的的租价比会把结构差当成收益率差）。
  ② **大宗交易反推**
        净资本化率 = NOI / 成交价
     需逐笔成交数据；本工具通过 --deal-noi / --deal-price 支持。

同时提供**用户成本法交叉验证**：由观测到的价格租金比反推隐含的预期增值率 π_e：
    P/R = 1 / [ i(1−τ) + δ + m − π_e ]   ⇒   π_e = i(1−τ) + δ + m − 1/(P/R)
这一步把"收益率低所以投资价值弱"这种模糊判断，落到**可检验的假设**上。

用法：
    python3 caprate_calibrate.py --data matched_demo.csv --rent-column 月租 --price-column 单价 \
        --area-column 面积 --segment 城市 物业类型 --opex-lo 0.20 --opex-hi 0.35
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for _c in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _c not in sys.path:
        sys.path.insert(0, _c)

from core import read_csv, to_float, mean  # noqa: E402

# 运营成本率的**示意区间**（中国住宅出租）。商业物业显著更高。
# ⚠️ 必须在报告中标注实际采用的取值与依据，不得默认引用本表。
OPEX_HINT = {
    "住宅": (0.15, 0.25),
    "公寓": (0.20, 0.30),
    "写字楼": (0.25, 0.40),
    "商业": (0.30, 0.45),
    "产业园": (0.20, 0.35),
}


def _median(v):
    v = sorted([x for x in v if x is not None])
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def _quantile(v, q):
    v = sorted([x for x in v if x is not None])
    if not v:
        return None
    idx = min(int(len(v) * q), len(v) - 1)
    return v[idx]


# ================================================================ 路径 ①：匹配价格租金比
def matched_pr_caprate(rows, rent_col, price_col, area_col=None,
                       segment_cols=None, opex_lo=None, opex_hi=None,
                       rent_is_monthly_total=True, price_is_unit=True):
    """
    从匹配样本推导 cap rate。

    返回逐段（segment）的：样本数、毛资本化率中位、净资本化率区间、诊断与警告。
    """
    segment_cols = segment_cols or []
    groups = {}
    parse_warn = []

    for r in rows:
        rent = to_float(r.get(rent_col))
        price = to_float(r.get(price_col))
        area = to_float(r.get(area_col)) if area_col else None
        if rent is None or rent <= 0 or price is None or price <= 0:
            continue
        # 归一为「年租金 / 标的价」与「元/㎡/月 对 元/㎡」
        if not price_is_unit:
            if not area:
                parse_warn.append("价格非单价口径但缺少面积列，该行被跳过")
                continue
            price = price / area
        annual_per_sqm = (rent * 12.0 / area) if (rent_is_monthly_total and area) else (rent * 12.0)
        if annual_per_sqm <= 0 or price <= 0:
            continue
        gross = annual_per_sqm / price          # 毛资本化率
        key = tuple((r.get(c) or "未标注").strip() for c in segment_cols) if segment_cols else ("全部",)
        groups.setdefault(key, []).append({
            "gross": gross, "annual_rent_per_sqm": annual_per_sqm, "price": price,
            "price_rent_ratio_years": price / annual_per_sqm if annual_per_sqm else None,
        })

    out = []
    for key, items in sorted(groups.items(), key=lambda x: str(x[0])):
        g = [i["gross"] for i in items]
        pr = [i["price_rent_ratio_years"] for i in items]
        rec = {
            "segment": dict(zip(segment_cols or ["范围"], key)),
            "n": len(items),
            "gross_caprate_median": _median(g),
            "gross_caprate_q1": _quantile(g, 0.25),
            "gross_caprate_q3": _quantile(g, 0.75),
            "price_rent_ratio_years_median": _median(pr),
            "warnings": [],
            "mark": "测算",
        }
        # 样本量
        if len(items) < 10:
            rec["warnings"].append(
                "样本仅 %d 条：cap rate 估计不稳定（该段结果仅供参考，须与官方指数或"
                "大宗交易反推交叉验证）" % len(items))
        # 离散度
        if len(g) >= 3:
            m = mean(g)
            sd = (sum((x - m) ** 2 for x in g) / len(g)) ** 0.5
            rec["gross_caprate_cv"] = sd / m if m else None
            if rec["gross_caprate_cv"] and rec["gross_caprate_cv"] > 0.25:
                rec["warnings"].append(
                    "毛资本化率变异系数 %.0f%% 偏高：说明段内异质性大（可能混了不同物业类型"
                    "或不同房龄），建议进一步细分" % (rec["gross_caprate_cv"] * 100))

        # 净资本化率 = 毛 × (1 − op_ex)
        hint = None
        ptype = rec["segment"].get("物业类型") or rec["segment"].get("类型")
        if ptype and ptype in OPEX_HINT:
            hint = OPEX_HINT[ptype]
        lo = opex_lo if opex_lo is not None else (hint[0] if hint else None)
        hi = opex_hi if opex_hi is not None else (hint[1] if hint else None)
        if lo is not None and hi is not None:
            gm = rec["gross_caprate_median"]
            rec["opex_rate_range"] = [lo, hi]
            rec["net_caprate_range"] = [gm * (1 - hi), gm * (1 - lo)]   # 成本高 → 净收益低
            rec["opex_source"] = ("命令行指定" if (opex_lo is not None or opex_hi is not None)
                                  else ("内置示意区间（须替换为实测或权威来源）" if hint
                                        else "未提供，无法给出净资本化率"))
            if hint and opex_lo is None and opex_hi is None:
                rec["warnings"].append(
                    "运营成本率取自内置示意区间 %s，**属估算**——报告中必须标注取值依据，"
                    "或替换为该城市/类型的实测数据" % (list(hint),))
        else:
            rec["warnings"].append("未提供运营成本率，只能给出**毛**资本化率；"
                                   "净资本化率须扣运营成本后计算")
        out.append(rec)
    return {"method": "匹配价格租金比", "segments": out, "parse_warnings": parse_warn,
            "note": "**全部结果为推导值（测算/估算），非引用值**。中国无公开连续的分城市"
                    "分类型 cap rate 序列，报告中不得写成看似有权威来源的引用数字。"}


# ================================================================ 路径 ②：大宗交易反推
def deal_reverse_caprate(noi, price):
    if price <= 0:
        raise ValueError("成交价必须为正")
    return {"method": "大宗交易反推", "noi": noi, "price": price,
            "cap_rate": noi / price, "mark": "测算",
            "note": "逐笔反推，精度取决于 NOI 的可靠性；单笔结果不应代表整个市场"}


# ================================================================ 用户成本交叉验证
def implied_appreciation(price, annual_rent_per_sqm, funding_rate,
                         depreciation=0.015, holding_tax=0.0, tax_shield=0.0):
    """
    由观测价格租金比**反推隐含的预期增值率 π_e**（用户成本法的逆向使用）。

        P/R = 1 / [ i(1−τ) + δ + m − π_e ]
     ⇒  π_e = i(1−τ) + δ + m − (年租金 / 价格)

    这一步把"收益率低 ⇒ 投资价值弱"这种模糊判断，落到可检验的假设上：
    要让当前价格成立，市场隐含的年度增值预期必须达到 π_e。
    """
    if price <= 0:
        raise ValueError("价格必须为正")
    holding_yield = annual_rent_per_sqm / price
    uc_without_appre = funding_rate * (1 - tax_shield) + depreciation + holding_tax
    pi = uc_without_appre - holding_yield
    return {
        "holding_yield_annual": holding_yield,
        "user_cost_without_appreciation": uc_without_appre,
        "implied_appreciation_pi_e": pi,
        "components": {"i(1−τ)": funding_rate * (1 - tax_shield), "δ 折旧维护": depreciation,
                       "m 持有税": holding_tax},
        "mark": "研判推断",
        "note": "π_e 不可观测，本值为**反推值**，用于回答『当前价格隐含多高的增值预期』。"
                "须在报告中标注为「研判推断」，并给出敏感性（i 与 δ 取值变化的影响）。",
    }


# ================================================================ 输出
def print_report(res, implied=None):
    print("=" * 78)
    print("Cap Rate 标定报告（推导值，非引用值）")
    print("=" * 78)
    print("方法：%s" % res.get("method"))
    print("%s" % res.get("note", ""))
    print()
    print("  %-24s %5s %12s %18s %16s" %
          ("分段", "n", "毛 cap 中位", "净 cap 区间", "价格租金比(年)"))
    print("  " + "-" * 80)
    for s in res.get("segments", []):
        seg = " / ".join(str(v) for v in s["segment"].values())
        net = ("%.2f%% ~ %.2f%%" % (s["net_caprate_range"][0] * 100,
                                    s["net_caprate_range"][1] * 100)
               if s.get("net_caprate_range") else "—")
        print("  %-24s %5d %11.2f%% %18s %16s"
              % (seg[:24], s["n"], s["gross_caprate_median"] * 100, net,
                 ("%.1f" % s["price_rent_ratio_years_median"])
                 if s["price_rent_ratio_years_median"] else "—"))
    print()
    for s in res.get("segments", []):
        seg = " / ".join(str(v) for v in s["segment"].values())
        for w in s["warnings"]:
            print("  ⚠ [%s] %s" % (seg, w))
        if s.get("opex_source"):
            print("  · [%s] 运营成本率来源：%s（取 %s）"
                  % (seg, s["opex_source"], s.get("opex_rate_range")))
    if res.get("parse_warnings"):
        for w in res["parse_warnings"][:5]:
            print("  ⚠ %s" % w)
    print()
    if implied:
        print("=" * 78)
        print("用户成本法交叉验证：反推隐含预期增值率 π_e")
        print("=" * 78)
        print("  持有收益率（年租金/价格）：%.2f%%" % (implied["holding_yield_annual"] * 100))
        print("  不含增值的用户成本　　：%.2f%%"
              % (implied["user_cost_without_appreciation"] * 100))
        print("  ⇒ 隐含预期增值率 π_e　：**%.2f%%/年**"
              % (implied["implied_appreciation_pi_e"] * 100))
        print("  构成：%s" % ", ".join("%s=%.3f%%" % (k, v * 100)
                                      for k, v in implied["components"].items()))
        print("  %s" % implied["note"])
    print()
    print("⚠ 免责：本报告全部数值为**从租金与价格数据推导**的结果，非权威来源引用值。")
    print("   中国无公开连续的分城市分类型 cap rate 序列，报告中不得写成引用数字。")


def main():
    ap = argparse.ArgumentParser(description="Cap Rate 标定器（从租金与价格推导）")
    ap.add_argument("--data", help="匹配样本 CSV（须含租金与价格列）")
    ap.add_argument("--rent-column", default="月租")
    ap.add_argument("--price-column", default="单价")
    ap.add_argument("--area-column", default="面积")
    ap.add_argument("--segment", nargs="*", default=[], help="分段列，如 城市 物业类型")
    ap.add_argument("--opex-lo", type=float, default=None, help="运营成本率下限")
    ap.add_argument("--opex-hi", type=float, default=None, help="运营成本率上限")
    ap.add_argument("--price-is-unit", action="store_true", default=True)
    ap.add_argument("--price-is-total", action="store_true", help="价格为总价口径")
    # 交叉验证
    ap.add_argument("--funding-rate", type=float, default=None,
                    help="资金成本 i（如商贷利率 0.0305），提供则做用户成本交叉验证")
    ap.add_argument("--depreciation", type=float, default=0.015, help="折旧维护率 δ")
    ap.add_argument("--holding-tax", type=float, default=0.0, help="持有税费率 m")
    args = ap.parse_args()

    if not args.data:
        ap.error("需要 --data 指定匹配样本 CSV")
    hdr, rows = read_csv(args.data)
    res = matched_pr_caprate(
        rows, args.rent_column, args.price_column, args.area_column,
        args.segment, args.opex_lo, args.opex_hi,
        rent_is_monthly_total=True, price_is_unit=not args.price_is_total)

    implied = None
    if args.funding_rate is not None:
        s0 = res["segments"][0]
        # 由中位价格租金比反推：年租金/价格 = 1/PR
        pr = s0["price_rent_ratio_years_median"]
        if pr:
            implied = implied_appreciation(1.0, 1.0 / pr, args.funding_rate,
                                           args.depreciation, args.holding_tax)
    print_report(res, implied)
    return 0


if __name__ == "__main__":
    sys.exit(main())
