#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
成本法与假设开发法（剩余法）—— 零依赖实现。

对应 Skill：skills/cost-residual/SKILL.md
对应知识底座：knowledge/experts/rep-001/blocks/07-practice-methods.md

为什么必须有这个模块（评审 P1-1/P1-3）：
    `data-contracts/capability-contract.csv` 的 `rep.land.parcel.read` 明确写着
    "用于成本法与供给分析"，但包内**没有成本法 Skill** —— 契约承诺了包内不存在的能力。
    本模块消除这个空头。

两种方法的分工（不可混用）：
  · **成本法**：回答"重新建一个要花多少"。适用市场交易稀疏（新建区、特殊用途、
    工业厂房）、保险与税务场景。**不适用于住宅正常交易估值**——中国住宅价格中含
    大量区位与制度租值，成本与价格无稳定关系，只能作下限校验。
  · **假设开发法（剩余法）**：回答"土地值多少"。土地与在建工程的**首选方法**。

易错点（已内置处理）：
  1. **利润基数的循环**：静态法中若利润以"土地价值 + 开发成本"为基数，而土地价值
     正是待求量，会形成代数循环。必须用**解析解**，不能迭代估计。
  2. **出让条件未建模**：配建、自持、限价会实质改变剩余价值，不建模会高估地价。
  3. **土地增值税超率累进**：不可用简单比例税费率替代。
  4. **建安成本的时代差异**：同一结构类型下，2000 年代与 2020 年代的交付标准差异巨大，
     重置成本必须按建成年份分档。
  5. **用估值反推的地价再去算房价**：循环论证。

用法：
    # 成本法（积算 + 折旧）
    python3 cost_residual.py --method cost --land 18000 --build 3200 --area 100 \
        --age 15 --life 50 --dev-years 2 --profit-rate 0.15

    # 假设开发法（静态解析解）
    python3 cost_residual.py --method residual --dev-value 42000 --build 3200 \
        --dev-years 2 --rate 0.045 --profit-rate 0.15 --sale-tax 0.0555 --land-tax 0.03
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for _c in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _c not in sys.path:
        sys.path.insert(0, _c)

from core import mean  # noqa: E402


# ================================================================ 假设开发法（剩余法）
def residual_static(dev_value, build_cost, dev_years=2.0, rate=0.045,
                    profit_rate=0.15, sale_tax_rate=0.0555, land_tax_rate=0.03,
                    saleable_ratio=1.0, contrib_ratio=1.0):
    """
    静态假设开发法 —— **解析解**，避免利润基数的代数循环。

    推导（各符号含义见下）：
        开发完成后价值  V_dev
        后续开发成本    C            （建安 + 前期 + 基础设施 + 公共配套）
        土地价值        V            ← 待求
        土地取得税费    t_land · V
        销售税费        t_sale · V_dev
        开发利润        p · (V + C)  ← 以"土地 + 开发成本"为基数（循环来源）
        投资利息        土地部分按全额计息 a = (1+i)^n − 1
                        建造成本按**均匀投入**计息 b = (1+i)^(n/2) − 1

    由  V + C + 利息 + 销售税费 + 土地取得税费 + 利润 = V_dev  得：

        V = [ V_dev·(1 − t_sale) − C·(1 + b + p) ] / (1 + a + p + t_land)

    ⚠️ 若用迭代估计，或在已含利润的基数上再乘利润率，会系统性抬高土地价值。

    saleable_ratio : 可售面积比例（出让条件中的配建/自持会降低此值）
    contrib_ratio  : 无偿移交/配建比例（从可售面积中扣除）
    """
    a = (1.0 + rate) ** dev_years - 1.0                  # 土地全额计息因子
    b = (1.0 + rate) ** (dev_years / 2.0) - 1.0          # 建造成本均匀投入计息因子

    c_eff = build_cost * (saleable_ratio / contrib_ratio) if contrib_ratio else build_cost
    denom = 1.0 + a + profit_rate + land_tax_rate
    numer = dev_value * (1.0 - sale_tax_rate) - c_eff * (1.0 + b + profit_rate)
    V = numer / denom

    interest = V * a + c_eff * b
    profit = profit_rate * (V + c_eff)
    sale_tax = sale_tax_rate * dev_value
    land_tax = land_tax_rate * V

    return {
        "method": "假设开发法（静态解析解）",
        "land_value": V,
        "land_value_per_sqm_floor": V,  # 若 dev_value 与 build_cost 均为元/㎡，则 V 亦为元/㎡
        "components": {
            "开发完成后价值": dev_value,
            "后续开发成本(按可售面积调整后)": c_eff,
            "投资利息": interest,
            "销售税费": sale_tax,
            "土地取得税费": land_tax,
            "开发利润": profit,
        },
        "factors": {"土地计息因子 a": a, "建造计息因子 b": b},
        "equation": "V = [V_dev·(1−t_sale) − C·(1+b+p)] / (1+a+p+t_land)",
        "check": {
            "sum_should_equal_dev_value": (V + c_eff + interest + sale_tax + land_tax + profit),
            "dev_value": dev_value,
        },
    }


def lvt_ultra_progressive(value_added, value_added_ratio_base):
    """
    土地增值税（超率累进，简化实现）。

    中国土地增值税按"增值率"（增值额 / 扣除项目金额）分档累进。本函数按
    增值率分档计算税额与速算扣除系数。**分档比例与速算扣除系数须核对当期
    税法原文**——本表仅为结构示意，报告中引用前必须复核。

    返回 (税额, 适用税率, 速算扣除系数)
    """
    if value_added_ratio_base <= 0:
        raise ValueError("扣除项目金额必须为正")
    r = value_added / value_added_ratio_base
    if r <= 0.5:
        return value_added * 0.30, 0.30, 0.0
    if r <= 1.0:
        return value_added * 0.40 - value_added_ratio_base * 0.05, 0.40, 0.05
    if r <= 2.0:
        return value_added * 0.50 - value_added_ratio_base * 0.15, 0.50, 0.15
    return value_added * 0.60 - value_added_ratio_base * 0.35, 0.60, 0.35


# ================================================================ 成本法
# 建安重置成本按建成年份分档（元/㎡）。**这是示意值，报告中必须替换为
# 当期当地造价信息或造价咨询机构的实测单价，不得直接引用本表。**
BUILD_COST_BAND = {
    "2000年以前": 2200,
    "2000-2009": 2800,
    "2010-2014": 3300,
    "2015-2019": 3800,
    "2020以后": 4300,
}


def build_cost_by_year(year, structure="钢混", adjust=1.0):
    """按建成年份档给出建安重置成本（示意值，须以当地造价信息替换）。"""
    band = None
    for k in BUILD_COST_BAND:
        if k == "2000年以前" and year < 2000:
            band = k
        elif k.endswith("以后") and year >= int(k[:4]):
            band = k
        elif "-" in k and not k.endswith("以后"):
            lo, hi = k.split("-")
            if int(lo) <= year <= int(hi):
                band = k
    if band is None:
        band = "2015-2019"
    base = BUILD_COST_BAND[band]
    return {"band": band, "unit_cost": base * adjust, "structure": structure}


def depreciation(age, life=50, physical=None, functional=0.0, economic=0.0,
                 method="age-life"):
    """
    建筑物折旧。

    method:
      · age-life  年限法：成新率 = 剩余年限 / 总使用年限
      · observed  观察法：由物理/功能/经济三种折旧分别给出
    ⚠️ 年限法过于粗糙（无法反映功能折旧与经济折旧），重要标的应使用观察法
      或两者交叉验证。
    """
    if method == "age-life":
        remain = max(life - age, 0)
        rate = remain / life
        return {"method": "年限法", "成新率": rate, "已使用年限": age, "总使用年限": life,
                "note": "年限法不能反映功能折旧与经济折旧，重要标的应用观察法交叉验证"}
    total = physical if physical is not None else age / life
    rate = max(1.0 - total - functional - economic, 0.0)
    return {"method": "观察法", "成新率": rate,
            "物理折旧率": total, "功能折旧率": functional, "经济折旧率": economic,
            "note": "经济折旧须独立判断（区位衰落、需求转移），不可由房龄机械推导"}


def cost_approach(land_cost, build_unit_cost, area, dep_rate,
                  mgmt_rate=0.03, sale_rate=0.03, interest_rate=0.045,
                  dev_years=2.0, profit_rate=0.15, land_tax_rate=0.03):
    """
    成本法（积算式）：各项成本加总后计入投资利息、税费与开发利润。

        V = (土地取得成本 + 建筑物重置成本 + 管理费用 + 销售费用) × (1 + 利润) 型结构
    """
    building = build_unit_cost * area
    mgmt = (land_cost + building) * mgmt_rate
    sale = (land_cost + building) * sale_rate
    base = land_cost + building + mgmt + sale
    a = (1.0 + interest_rate) ** dev_years - 1.0
    interest = base * a
    profit = (base + interest) * profit_rate
    land_tax = land_cost * land_tax_rate
    total = base + interest + profit + land_tax
    return {
        "method": "成本法（积算）",
        "value": total,
        "value_after_depreciation": total * dep_rate,
        "components": {
            "土地取得成本": land_cost, "建筑物重置成本": building,
            "管理费用": mgmt, "销售费用": sale, "投资利息": interest,
            "开发利润": profit, "土地取得税费": land_tax,
        },
        "depreciation_rate": dep_rate,
    }


# ================================================================ 敏感性
def sensitivity(fn, grid_a, grid_b, label_a, label_b):
    """二维敏感性表。fn(a, b) -> 数值。"""
    lines = []
    for b in grid_b:
        row = []
        for a in grid_a:
            row.append(fn(a, b))
        lines.append((b, row))
    return {"label_a": label_a, "label_b": label_b, "grid_a": grid_a,
            "grid_b": grid_b, "rows": lines}


def print_sensitivity(s, fmt="%.0f", unit=""):
    wa = max(len(s["label_a"]) + 2, 10)
    out = ["  %s\\%s" % (s["label_b"].ljust(12), s["label_a"].ljust(wa))
           + "  ".join(("%12s" % ("%.4g" % v)) for v in s["grid_a"])]
    out.append("  " + "-" * (12 + wa + 14 * len(s["grid_a"])))
    for b, row in s["rows"]:
        out.append("  %s%s" % (("%.4g" % b).ljust(12), " " * wa)
                   + "  ".join(("%12s" % (fmt % v)) for v in row))
    if unit:
        out.append("  单位：%s" % unit)
    return "\n".join(out)


# ================================================================ CLI
def main():
    ap = argparse.ArgumentParser(description="成本法与假设开发法（剩余法）")
    ap.add_argument("--method", choices=["cost", "residual"], default="residual")
    # 通用
    ap.add_argument("--rate", type=float, default=0.045, help="投资利息率/折现率")
    ap.add_argument("--dev-years", type=float, default=2.0, help="开发期（年）")
    ap.add_argument("--profit-rate", type=float, default=0.15)
    ap.add_argument("--land-tax", type=float, default=0.03, help="土地取得税费率")
    ap.add_argument("--no-sensitivity", action="store_true")
    # 假设开发法
    ap.add_argument("--dev-value", type=float, default=None, help="开发完成后价值（元/㎡）")
    ap.add_argument("--build", type=float, default=None, help="后续开发成本（元/㎡）")
    ap.add_argument("--sale-tax", type=float, default=0.0555, help="销售税费率")
    ap.add_argument("--saleable-ratio", type=float, default=1.0, help="可售面积比例（出让条件折减）")
    # 成本法
    ap.add_argument("--land", type=float, default=None, help="土地取得成本（元/㎡ 或总额）")
    ap.add_argument("--area", type=float, default=None)
    ap.add_argument("--age", type=float, default=15)
    ap.add_argument("--life", type=float, default=50)
    args = ap.parse_args()

    if args.method == "residual":
        if args.dev_value is None or args.build is None:
            ap.error("假设开发法需要 --dev-value 与 --build")
        r = residual_static(args.dev_value, args.build, dev_years=args.dev_years,
                            rate=args.rate, profit_rate=args.profit_rate,
                            sale_tax_rate=args.sale_tax, land_tax_rate=args.land_tax,
                            saleable_ratio=args.saleable_ratio)
        print("=" * 74)
        print("假设开发法（剩余法）测算结果")
        print("=" * 74)
        print("公式：%s" % r["equation"])
        print()
        print("  土地价值（楼面地价）：%.0f 元/㎡" % r["land_value"])
        print()
        print("  构成：")
        for k, v in r["components"].items():
            print("    %-32s %12.0f" % (k, v))
        print()
        print("  计息因子：土地 %.6f　建造（均匀投入）%.6f"
              % (r["factors"]["土地计息因子 a"], r["factors"]["建造计息因子 b"]))
        chk = r["check"]
        print("  验算：各项合计 %.2f 应等于开发完成后价值 %.2f（差 %.4f）"
              % (chk["sum_should_equal_dev_value"], chk["dev_value"],
                 chk["sum_should_equal_dev_value"] - chk["dev_value"]))
        print()
        print("  ⚠ 利润基数取「土地价值 + 开发成本」，故必须用解析解；")
        print("     若改用「开发完成后价值」为基数，结果会显著不同，须在报告中说明取了哪种。")
        if args.saleable_ratio != 1.0:
            print("  ⚠ 已按出让条件折减可售面积比例 %.3f（配建/自持会实质压低地价）"
                  % args.saleable_ratio)

        if not args.no_sensitivity:
            print()
            print("=" * 74)
            print("二维敏感性：利润率（行） × 利息率（列）→ 土地价值（元/㎡）")
            print("=" * 74)
            s = sensitivity(
                lambda rr, pp: residual_static(
                    args.dev_value, args.build, dev_years=args.dev_years, rate=rr,
                    profit_rate=pp, sale_tax_rate=args.sale_tax,
                    land_tax_rate=args.land_tax, saleable_ratio=args.saleable_ratio)["land_value"],
                [0.03, 0.045, 0.06, 0.075], [0.10, 0.15, 0.20, 0.25],
                "利息率", "利润率")
            print(print_sensitivity(s, unit="元/㎡"))
        return 0

    # ---- 成本法
    if args.land is None or args.build is None:
        ap.error("成本法需要 --land 与 --build")
    area = args.area or 100.0
    dep = depreciation(args.age, args.life, method="age-life")
    r = cost_approach(args.land * area, args.build, area, dep["成新率"],
                      interest_rate=args.rate, dev_years=args.dev_years,
                      profit_rate=args.profit_rate, land_tax_rate=args.land_tax)
    print("=" * 74)
    print("成本法测算结果")
    print("=" * 74)
    print("  成新率：%.1f%%（%s）" % (dep["成新率"] * 100, dep["method"]))
    print("  %s" % dep["note"])
    print()
    print("  重置成本合计：%.0f 元（%.0f 元/㎡）" % (r["value"], r["value"] / area))
    print("  考虑折旧后　：%.0f 元（%.0f 元/㎡）"
          % (r["value_after_depreciation"], r["value_after_depreciation"] / area))
    print()
    print("  构成：")
    for k, v in r["components"].items():
        print("    %-24s %12.0f" % (k, v))
    print()
    print("  ⚠ 成本法**不适用于住宅正常交易估值**——中国住宅价格中含大量区位与制度租值，")
    print("     成本与价格无稳定关系。本结果只能作为交易法估值的**下限校验**。")
    print("  ⚠ 建安重置成本若取自内置分档表，属**示意值**，报告中必须替换为当地造价信息。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
