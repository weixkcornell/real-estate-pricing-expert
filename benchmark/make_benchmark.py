#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中等规模合成基准数据集生成器（用于上真实数据前的系统验证）。

⚠️⚠️⚠️ 重要声明：本脚本生成的全部数据均为【合成数据 / 模拟数据】，
   由下方写死的 DGP（数据生成过程）人为构造，**不代表任何真实市场行情**。
   严禁将其当作市场数据引用、披露或对外发布。任何基于本数据的结论都应
   明确标注"合成数据"。

为何需要它（对应外部评审 P2-2-a）：
   原演示数据为 8 / 60 / 450 条的玩具规模，GWR 在 n=60 上做带宽选择基本不可用，
   用户无法在接入真实数据前系统地验证本包实现的还原能力与数值稳定性。
   本脚本给出 n≈5000、DGP 已知且写死的合成基准，配套真值清单，便于：
     - 用 OLS/hedonic 还原已知线性系数真值；
     - 用 BMN / Case-Shiller 还原已知真实价格指数序列；
     - 用 stratified_rent_index 还原已知真实租金指数；
     - 用 GWR / SAR / SEM 检验空间异质与空间自相关处理是否正确；
     - 用 GP-AVM / 回归克里金检验区间与样本外精度是否真实。

DGP 设计要点（必须显式建模，否则空间计量类检验无意义）：
   1. 线性半对数 hedonic：ln(单价) = β·X + γ_t（期效应）+ S(坐标) + ε
      - γ_t 即"真实价格指数"（对数口径）；
      - S(坐标) 为坐标的平滑函数 → 制造【空间自相关】；
      - ε 的标准差随面积变化 → 制造【异方差】。
   2. 重复成交对：同一物业两次成交，含物业固定效应 π_i 与【随机游走】分量
      w_i(gap) ~ N(0, gap·σ_w²)，使对数价差方差 = 2σ_ε² + gap·σ_v²，
      正好对应 Case-Shiller 方差结构 Var = 2σ_u² + (t'−t)σ_v²；且持有间隔
      为数值（月），可直接检验 σ_v 是否被错估为 0。
   3. 租金面板：ln(月租) = δ·X + η_t（期效应）+ 噪声，η_t 即"真实租金指数"。

零外部依赖：仅用 Python 标准库；固定随机种子保证可复现。
"""

import csv
import math
import os
import random

# ============================================================ 固定随机种子（保证可复现）
SEED = 20260916
random.seed(SEED)

# ============================================================ 时空范围与期数
N_HOUSES = 5000          # 房价样本量（≈5000，满足"中等规模"）
N_PAIRS = 1200           # 重复成交对数量（≥800）
N_PERIODS = 24           # 期数（月，1..24，数值型，使持有间隔可数值化）

# 城市坐标包围盒（北京附近，经纬度，仅用于制造空间自相关，无地理含义）
LON0, LON1 = 116.00, 116.60
LAT0, LAT1 = 39.70, 40.10

# ============================================================ 已知真值：线性 hedonic 系数（半对数 ln 单价）
# 面积 ㎡、房龄 年、楼层 1..30、到市中心距离 km、到最近地铁距离 km、学区 0/1
BETA0 = 10.30            # 截距（基线价 ≈ exp(10.30) ≈ 29733 元/㎡）
BETA = {
    "面积": 0.0060,          # 每 ㎡ +0.6%（约 +60㎡ → ×1.43）
    "房龄": -0.0120,         # 每老 1 年 −1.2%
    "楼层": 0.0020,          # 每高 1 层 +0.2%（轻微）
    "到市中心距离": -0.0150,  # 每远 1km −1.5%
    "到最近地铁距离": -0.0200,  # 每远 1km −2.0%
    "学区": 0.0600,          # 学区房 +6%
}
# 朝向（分类型，朝南为参照组，不设虚拟变量）
ORIENT_EFFECT = {"南": 0.0, "北": -0.050, "东": 0.010, "西": -0.020, "东南": 0.015, "西南": -0.010}

# ============================================================ 已知真值：真实价格指数 γ_t（对数口径）
# 24 个月：年化约 +4.8% 的趋势 + 季节性（振幅 2%）
def true_gamma(t):
    """t 从 1 开始。真实期效应（对数价格口径）。"""
    return 0.0040 * (t - 1) + 0.0200 * math.sin(2.0 * math.pi * (t - 1) / 12.0)

# ============================================================ 已知真值：真实租金指数 η_t（对数口径）
def true_eta(t):
    """t 从 1 开始。真实租金期效应（对数租金口径），路径与房价不同。"""
    return 0.0020 * (t - 1) + 0.0150 * math.sin(2.0 * math.pi * (t - 1) / 12.0 + 1.0)

# ============================================================ 已知真值：空间场 S(坐标)（空间自相关来源）
def spatial_field(lon, lat):
    """坐标的平滑函数 → 相邻房产价格高度相关（空间自相关）。"""
    return (0.150 * math.sin(3.0 * lon)
            + 0.120 * math.cos(2.5 * lat)
            + 0.080 * math.sin(2.0 * lon + 1.7 * lat))

# 租金空间场（与房价同一平滑基底，幅度不同）
def rent_spatial_field(lon, lat):
    return (0.100 * math.sin(3.0 * lon + 0.4)
            + 0.090 * math.cos(2.5 * lat + 0.3)
            + 0.060 * math.sin(2.0 * lon + 1.7 * lat + 0.6))

# ============================================================ 已知真值：重复成交随机游走强度（σ_v² 来源）
SIGMA_W = 0.06           # 随机游走单步标准差（对数价差）
SIGMA_EPS_PAIR = 0.05    # 单次成交特异噪声标准差（→ 2σ_u² = 2·SIGMA_EPS_PAIR²）

# ============================================================ 已知真值：租金 hedonic 系数（半对数 ln 月租）
DELTA0 = 7.50            # 截距（基线月租 ≈ exp(7.50) ≈ 1808 元/月）
DELTA = {
    "面积": 0.0100,           # 每 ㎡ +1.0%
    "到最近地铁距离": -0.0150,
    "学区": 0.0400,
}


# ============================================================ 工具
def randn():
    """标准正态（Box-Muller），依赖固定种子的 random 模块。"""
    u1 = random.random()
    u2 = random.random()
    while u1 <= 1e-12:
        u1 = random.random()
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def sample_orientation():
    """按中国常见分布抽样朝向。"""
    return random.choices(
        ["南", "北", "东", "西", "东南", "西南"],
        weights=[0.40, 0.18, 0.15, 0.12, 0.10, 0.05],
    )[0]


# ============================================================ 生成房价样本（benchmark_house.csv）
def gen_houses():
    rows = []
    for i in range(1, N_HOUSES + 1):
        area = random.uniform(45.0, 160.0)       # 面积 ㎡
        age = random.uniform(0.0, 30.0)          # 房龄 年
        floor = random.randint(1, 30)            # 楼层
        cbd = random.uniform(1.0, 25.0)          # 到市中心距离 km
        subway = random.uniform(0.1, 5.0)        # 到最近地铁距离 km
        school = random.choice([0, 1])           # 学区 0/1
        orient = sample_orientation()
        lon = random.uniform(LON0, LON1)
        lat = random.uniform(LAT0, LAT1)
        t = random.randint(1, N_PERIODS)         # 成交期（数值）

        # 异方差：噪声标准差随面积增大（大房子噪声更大）
        sd = 0.05 + 0.0008 * area
        noise = randn() * sd

        lp = (BETA0 + BETA["面积"] * area + BETA["房龄"] * age + BETA["楼层"] * floor
              + BETA["到市中心距离"] * cbd + BETA["到最近地铁距离"] * subway
              + BETA["学区"] * school + ORIENT_EFFECT[orient]
              + true_gamma(t) + spatial_field(lon, lat) + noise)
        price = math.exp(lp)

        rows.append({
            "物业编号": "H%05d" % i,
            "面积㎡": round(area, 1),
            "房龄": round(age, 1),
            "楼层": floor,
            "朝向": orient,
            "到市中心距离km": round(cbd, 3),
            "到最近地铁距离km": round(subway, 3),
            "学区": school,
            "经度": round(lon, 6),
            "纬度": round(lat, 6),
            "成交期": t,
            "成交单价元每平": round(price, 1),
        })
    return rows


# ============================================================ 生成重复成交对（benchmark_pairs.csv）
def gen_pairs():
    rows = []
    for i in range(1, N_PAIRS + 1):
        area = random.uniform(45.0, 160.0)
        age = random.uniform(0.0, 30.0)
        floor = random.randint(1, 30)
        cbd = random.uniform(1.0, 25.0)
        subway = random.uniform(0.1, 5.0)
        school = random.choice([0, 1])
        orient = sample_orientation()

        t1 = random.randint(1, N_PERIODS - 1)
        gap = random.randint(1, min(12, N_PERIODS - t1))   # 持有间隔 1..12 月（数值）
        t2 = t1 + gap

        pi = randn() * 0.18                       # 物业固定效应（两次成交共有）
        w = randn() * (SIGMA_W * math.sqrt(gap))  # 随机游走增量（持有期越长方差越大）
        e1 = randn() * SIGMA_EPS_PAIR
        e2 = randn() * SIGMA_EPS_PAIR

        # 两次成交共享同一属性结构（同一物业）
        base = (BETA0 + BETA["面积"] * area + BETA["房龄"] * age + BETA["楼层"] * floor
                + BETA["到市中心距离"] * cbd + BETA["到最近地铁距离"] * subway
                + BETA["学区"] * school + ORIENT_EFFECT[orient] + pi)
        lp1 = base + true_gamma(t1) + e1
        lp2 = base + true_gamma(t2) + w + e2
        p1 = math.exp(lp1)
        p2 = math.exp(lp2)

        rows.append({
            "物业编号": "P%05d" % i,
            "首次交易期": t1,
            "再次交易期": t2,
            "首次成交价": round(p1, 1),
            "再次成交价": round(p2, 1),
            "面积㎡": round(area, 1),
            "房龄": round(age, 1),
            "楼层": floor,
            "朝向": orient,
            "到市中心距离km": round(cbd, 3),
            "到最近地铁距离km": round(subway, 3),
            "学区": school,
        })
    return rows


# ============================================================ 生成租金面板（benchmark_rent.csv）
def gen_rent():
    rows = []
    for i in range(1, N_HOUSES + 1):
        area = random.uniform(45.0, 160.0)
        subway = random.uniform(0.1, 5.0)
        school = random.choice([0, 1])
        orient = sample_orientation()
        lon = random.uniform(LON0, LON1)
        lat = random.uniform(LAT0, LAT1)
        t = random.randint(1, N_PERIODS)

        noise = randn() * 0.07
        lr = (DELTA0 + DELTA["面积"] * area + DELTA["到最近地铁距离"] * subway
              + DELTA["学区"] * school + ORIENT_EFFECT[orient]
              + true_eta(t) + rent_spatial_field(lon, lat) + noise)
        rent = math.exp(lr)

        rows.append({
            "物业编号": "H%05d" % i,
            "期": t,
            "月租元": round(rent, 1),
            "面积㎡": round(area, 1),
            "到最近地铁距离km": round(subway, 3),
            "学区": school,
            "朝向": orient,
            "经度": round(lon, 6),
            "纬度": round(lat, 6),
        })
    return rows


# ============================================================ 写出
HOUSE_COLS = ["物业编号", "面积㎡", "房龄", "楼层", "朝向", "到市中心距离km",
              "到最近地铁距离km", "学区", "经度", "纬度", "成交期", "成交单价元每平"]
PAIR_COLS = ["物业编号", "首次交易期", "再次交易期", "首次成交价", "再次成交价",
             "面积㎡", "房龄", "楼层", "朝向", "到市中心距离km", "到最近地铁距离km", "学区"]
RENT_COLS = ["物业编号", "期", "月租元", "面积㎡", "到最近地铁距离km", "学区", "朝向", "经度", "纬度"]


def write_csv(path, cols, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def print_truth():
    """把写死的 DGP 与真值清单打印出来（P2-2-a 要求 DGP 已知且打印）。"""
    print("=" * 70)
    print("合成基准数据集 · DGP 与真值清单（⚠ 合成数据，非市场数据）")
    print("=" * 70)
    print("随机种子 SEED = %d（固定，保证可复现）" % SEED)
    print("样本量：房价 %d 行 / 重复成交对 %d 对 / 租金面板 %d 行；期数 %d（月，数值）"
          % (N_HOUSES, N_PAIRS, N_HOUSES, N_PERIODS))
    print("\n【房价 hedonic 真值（半对数 ln 单价 = Σβ·X + γ_t + S(坐标) + ε）】")
    print("  截距 β0 = %.4f" % BETA0)
    for k, v in BETA.items():
        print("  β[%s] = %+.4f" % (k, v))
    print("  朝向效应（朝南为参照）：" + "  ".join("%s=%+.3f" % (k, v) for k, v in ORIENT_EFFECT.items()))
    print("  空间自相关：S(经度,纬度)=0.15·sin(3lon)+0.12·cos(2.5lat)+0.08·sin(2lon+1.7lat)")
    print("  异方差：ε 标准差 sd = 0.05 + 0.0008·面积（随面积增大）")
    print("\n【真实价格指数 γ_t（对数口径，基期 t=1 归 100）】")
    g0 = true_gamma(1)
    for t in range(1, N_PERIODS + 1, 4):
        print("  t=%2d  γ_t=%+.4f  指数=%.2f" % (t, true_gamma(t), math.exp(true_gamma(t) - g0) * 100.0))
    print("\n【真实租金指数 η_t（对数口径，基期 t=1 归 100）】")
    e0 = true_eta(1)
    for t in range(1, N_PERIODS + 1, 4):
        print("  t=%2d  η_t=%+.4f  指数=%.2f" % (t, true_eta(t), math.exp(true_eta(t) - e0) * 100.0))
    print("\n【重复成交方差结构 Var = 2σ_u² + gap·σ_v²】")
    print("  σ_u = %.4f（单次成交特异噪声）  σ_v = %.4f（随机游走单步）" % (SIGMA_EPS_PAIR, SIGMA_W))
    print("\n【租金 hedonic 真值（半对数 ln 月租 = Σδ·X + η_t + 噪声）】")
    print("  截距 δ0 = %.4f" % DELTA0)
    for k, v in DELTA.items():
        print("  δ[%s] = %+.4f" % (k, v))
    print("=" * 70)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    houses = gen_houses()
    pairs = gen_pairs()
    rents = gen_rent()
    write_csv(os.path.join(here, "benchmark_house.csv"), HOUSE_COLS, houses)
    write_csv(os.path.join(here, "benchmark_pairs.csv"), PAIR_COLS, pairs)
    write_csv(os.path.join(here, "benchmark_rent.csv"), RENT_COLS, rents)
    print("已写出：")
    print("  benchmark/benchmark_house.csv  (%d 行)" % len(houses))
    print("  benchmark/benchmark_pairs.csv  (%d 对)" % len(pairs))
    print("  benchmark/benchmark_rent.csv   (%d 行)" % len(rents))
    print()
    print_truth()


if __name__ == "__main__":
    main()
