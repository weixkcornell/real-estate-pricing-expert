#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
租金与收益法定价计算器 —— 纯 Python 实现，零第三方依赖。

方法来源：
  - Ghysels, Plazzi & Valkanov (2007) "Valuation in US Commercial Real Estate",
    European Financial Management, 13(3), 472-497（折现租金模型 / Cap Rate）
  - Fisher, Miles & Webb (1994) "An Integrated Approach to the Evaluation of
    Commercial Real Estate", Journal of Real Estate Research（收益法 + 特征价格集成）
  - 上海价格租金比 (2021), Regional Science and Urban Economics, 89, 103676

功能
----
  1. 从租金样本计算单位租金（元/㎡/月）分布：中位、均值、分位、面积效应
  2. 毛租金收益率（多价格口径）
  3. 净租金收益率（参数化扣除空置 / 维护 / 税费）
  4. 价格租金比（年）
  5. 收益法 Cap Rate 定价（给定 NOI 反推价值）
  6. 与融资成本对照，给出「租金能否覆盖持有成本」的结论

用法
----
    python3 rent_income.py --rent-csv rent_sample.csv \
        --price-list 50210 47700 --fin-rate 0.0305

    # 商业地产 Cap Rate 定价
    python3 rent_income.py --noi 1200000 --cap-rate 0.045

仅依赖标准库。
"""

import argparse
import csv
import os
import sys


def read_csv(path):
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        headers = [h.strip() for h in reader.fieldnames]
        rows = []
        for raw in reader:
            row = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
            if any(row.values()):
                rows.append(row)
    return headers, rows


def to_float(s):
    if s is None:
        return None
    t = str(s).replace(",", "").strip()
    if t == "" or t in ("-", "—", "待补"):
        return None
    return float(t)


def median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def quantile(xs, q):
    s = sorted(xs)
    if not s:
        return None
    i = q * (len(s) - 1)
    lo, hi = int(i), min(int(i) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def main():
    ap = argparse.ArgumentParser(description="租金与收益法定价计算器")
    ap.add_argument("--rent-csv", help="租金样本 CSV（需含面积与月租两列）")
    ap.add_argument("--area-col", default="面积㎡", help="面积列名（默认：面积㎡）")
    ap.add_argument("--rent-col", default="月租(元)", help="月租列名（默认：月租(元)）")
    ap.add_argument("--price-list", nargs="+", type=float, help="用于计算收益率的售价口径（元/㎡）")
    ap.add_argument("--price-labels", nargs="+", help="与 --price-list 对应的口径名")
    ap.add_argument("--vacancy", type=float, default=0.05, help="空置率（默认 0.05）")
    ap.add_argument("--maintain", type=float, default=0.01, help="维护占租金比（默认 0.01）")
    ap.add_argument("--tax", type=float, default=0.05, help="税费占租金比（默认 0.05）")
    ap.add_argument("--fin-rate", type=float, help="融资成本（年化，如 0.0305 表示 3.05%%）")
    ap.add_argument("--noi", type=float, help="商业地产年净营运收入（元），配合 --cap-rate 使用")
    ap.add_argument("--cap-rate", type=float, help="资本化率（如 0.045）")
    args = ap.parse_args()

    did = False

    # ---------- 租金样本分析 ----------
    if args.rent_csv:
        if not os.path.exists(args.rent_csv):
            sys.exit("文件不存在：%s" % args.rent_csv)
        _, rows = read_csv(args.rent_csv)
        units, detail = [], []
        for r in rows:
            a = to_float(r.get(args.area_col))
            m = to_float(r.get(args.rent_col))
            if a and m and a > 0 and m > 0:
                u = m / a
                units.append(u)
                detail.append((a, m, u))

        print("=" * 64)
        print("租金样本分析（单位租金 = 月租 ÷ 面积）")
        print("=" * 64)
        print("  有效样本      : %d 条" % len(units))
        print("  单位租金 中位 : %.1f 元/㎡/月" % median(units))
        print("  单位租金 均值 : %.1f 元/㎡/月" % (sum(units) / len(units)))
        print("  25%% / 75%% 分位: %.1f / %.1f" % (quantile(units, 0.25), quantile(units, 0.75)))
        print("  最小 / 最大   : %.1f / %.1f" % (min(units), max(units)))

        # 面积效应
        small = [u for a, m, u in detail if a <= 60]
        mid = [u for a, m, u in detail if 90 <= a <= 115]
        large = [u for a, m, u in detail if a >= 130]
        print()
        print("  面积效应（单位租金）：")
        for label, g in (("小户型 ≤60㎡", small), ("中户型 90-115㎡", mid), ("大户型 ≥130㎡", large)):
            if g:
                print("    %-16s 均值 %.1f 元/㎡/月  (n=%d)" % (label, sum(g) / len(g), len(g)))
        if small and large:
            print("    小/大 溢价：%+.1f%%"
                  % ((sum(small) / len(small)) / (sum(large) / len(large)) * 100 - 100))

        rent_med = median(units)

        # ---------- 收益率 ----------
        if args.price_list:
            print()
            print("=" * 64)
            print("收益法测算（年租金 = 单位租金中位 × 12 = %.1f 元/㎡/年）" % (rent_med * 12))
            print("=" * 64)
            labels = args.price_labels or ["口径%d" % (i + 1) for i in range(len(args.price_list))]
            annual = rent_med * 12
            print("  %-14s %12s %12s %12s %12s" % ("价格口径", "售价(元/㎡)", "毛收益率", "净收益率", "价格租金比(年)"))
            for lab, p in zip(labels, args.price_list):
                gross = annual / p
                net = gross * (1 - args.vacancy) * (1 - args.maintain - args.tax)
                pr = p / (rent_med * 12)
                print("  %-14s %12.0f %11.2f%% %11.2f%% %12.1f"
                      % (lab, p, gross * 100, net * 100, pr))

            if args.fin_rate:
                gross = annual / args.price_list[0]
                print()
                print("  融资成本对照：%.2f%%" % (args.fin_rate * 100))
                if gross < args.fin_rate:
                    print("  → 毛收益率低于融资成本：租金现金流无法覆盖持有成本，定价依赖资本增值预期。")
                else:
                    print("  → 毛收益率高于融资成本：租金现金流可覆盖融资成本。")
        did = True

    # ---------- Cap Rate 定价 ----------
    if args.noi is not None and args.cap_rate:
        print()
        print("=" * 64)
        print("收益法（Cap Rate）定价")
        print("=" * 64)
        print("  年净营运收入 NOI : %.0f 元" % args.noi)
        print("  资本化率 Cap Rate: %.2f%%" % (args.cap_rate * 100))
        print("  → 估值 = NOI / CapRate = %.0f 元" % (args.noi / args.cap_rate))
        print()
        print("  敏感性（Cap Rate ±0.5pp）：")
        for d in (-0.005, -0.0025, 0.0, 0.0025, 0.005):
            cr = args.cap_rate + d
            if cr > 0:
                print("    CapRate %.2f%%  →  %.0f 元" % (cr * 100, args.noi / cr))
        print()
        print("  注意：Cap Rate 须按城市、物业类型分别标定，禁止跨类型/跨地点套用统一值")
        print("        （Fisher, Miles & Webb 1994, Journal of Real Estate Research）。")
        did = True

    if not did:
        ap.print_help()


if __name__ == "__main__":
    main()
