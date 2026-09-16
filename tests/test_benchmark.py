#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基准数据集集成测试（P2-2-a：上真实数据前系统验证实现）。

验证：
  - 三个基准文件规模达标（房价 ≈5000、配对 ≥800、租金面板 ≈5000）；
  - 列齐全、含空间坐标；
  - OLS 在已知 DGP 上能还原属性系数真值（含期虚拟变量吸收 γ_t 与空间场）；
  - 基准数据确实存在空间自相关（Moran's I 显著），使 GWR/SAR/SEM 有可检验对象；
  - 基准数据确实存在异方差（大面积组残差方差 > 小组）。

真值来自 make_benchmark.py 写死的 BETA（本文件重复声明以避免导入生成器的副作用）。
"""

import math
import os
import sys
import unittest

# 与仓库内各模块风格一致：用 sys.path 手动插入脚本目录（零外部依赖）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _c in (ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "adapters"),
          os.path.join(ROOT, "skills", "price-index", "scripts"),
          os.path.join(ROOT, "skills", "spatial-ml-valuation", "scripts"),
          os.path.join(ROOT, "skills", "rent-income", "scripts")):
    if _c not in sys.path:
        sys.path.insert(0, _c)

from core import read_csv, ols, mean
from spatial_model import distance_matrix, knn_weights, row_standardize, morans_i

BENCHMARK_DIR = os.path.join(ROOT, "benchmark")

# 与 make_benchmark.py 中写死的房价 hedonic 真值一致
# 键名必须与 benchmark_house.csv 的**实际列名**一致（面积㎡ / 到市中心距离km / …）
BETA_TRUTH = {
    "面积㎡": 0.0060, "房龄": -0.0120, "楼层": 0.0020,
    "到市中心距离km": -0.0150, "到最近地铁距离km": -0.0200, "学区": 0.0600,
}


class TestBenchmarkIntegrity(unittest.TestCase):
    def test_sizes(self):
        _, houses = read_csv(BENCHMARK_DIR + "/benchmark_house.csv")
        _, pairs = read_csv(BENCHMARK_DIR + "/benchmark_pairs.csv")
        _, rents = read_csv(BENCHMARK_DIR + "/benchmark_rent.csv")
        self.assertGreaterEqual(len(houses), 4000, "房价样本不足 4000（非中等规模）")
        self.assertGreaterEqual(len(pairs), 800, "重复成交对不足 800")
        self.assertGreaterEqual(len(rents), 4000, "租金面板不足 4000")
        # 含空间坐标列
        need = {"经度", "纬度", "成交期", "成交单价元每平", "面积㎡", "房龄", "学区"}
        self.assertTrue(need.issubset(set(houses[0].keys())), "房价表缺列")


class TestBenchmarkOlsRecovery(unittest.TestCase):
    def test_attribute_coef_recovery(self):
        _, houses = read_csv(BENCHMARK_DIR + "/benchmark_house.csv")
        periods = sorted({int(r["成交期"]) for r in houses})
        base = periods[0]
        cols = ["面积㎡", "房龄", "楼层", "到市中心距离km", "到最近地铁距离km", "学区"]
        X, y = [], []
        for r in houses:
            row = [1.0]
            for c in cols:
                row.append(float(r[c]))
            # 期虚拟变量（吸收 γ_t 与空间场）
            for p in periods[1:]:
                row.append(1.0 if int(r["成交期"]) == p else 0.0)
            X.append(row)
            y.append(math.log(float(r["成交单价元每平"])))
        beta = ols(X, y)
        # ⚠️ 设计矩阵第一列是截距，属性系数从 beta[1] 开始。
        # 原测试用 beta[j] 会拿截距（≈10.25）去比"面积系数真值 0.006"，
        # 这是测试自身的下标错误，不是实现问题。
        for j, c in enumerate(cols):
            got = beta[j + 1]
            self.assertLess(abs(got - BETA_TRUTH[c]), 0.02,
                            "属性 %s 系数未还原真值（估计 %.4f vs 真值 %.4f）"
                            % (c, got, BETA_TRUTH[c]))


class TestBenchmarkSpatialAndHetero(unittest.TestCase):
    def test_morans_i_significant(self):
        """
        基准应存在空间自相关（Moran's I 显著），否则空间模型无检验对象。

        ⚠️ **必须在 hedonic 残差上检验，而不是原始价格上**。
        原始价格的变异被属性（面积/房龄/楼层…）主导，空间成分占比很小，
        直接对价格做 Moran's I 会得到接近 0 的值（实测 I=0.002，p=0.77），
        从而**误判"基准没有空间结构"**。剔除属性效应后残差的空间自相关才可检出
        （实测 I=0.158，p=0.005）。这是空间计量检验的常见误用。
        """
        _, houses = read_csv(BENCHMARK_DIR + "/benchmark_house.csv")
        houses = houses[:600]
        cols = ["面积㎡", "房龄", "楼层", "到市中心距离km", "到最近地铁距离km", "学区"]
        X, y, C = [], [], []
        for r in houses:
            X.append([1.0] + [float(r[c]) for c in cols])
            y.append(math.log(float(r["成交单价元每平"])))
            C.append([float(r["经度"]), float(r["纬度"])])
        b = ols(X, y)
        resid = [y[i] - sum(X[i][j] * b[j] for j in range(len(b))) for i in range(len(y))]
        D = distance_matrix(C)
        W = row_standardize(knn_weights(D, 6))
        m = morans_i(W, resid, permutations=199, seed=7)
        self.assertLess(m["p_value"], 0.05,
                        "残差 Moran's I 未检出显著空间自相关（基准缺少空间结构），I=%.4f p=%.4f"
                        % (m["I"], m["p_value"]))
        self.assertGreater(m["I"], 0.05,
                           "残差空间自相关强度过低（I=%.4f），GWR/SAR/SEM 缺乏可检验对象" % m["I"])

    def test_heteroscedasticity_present(self):
        """基准应存在异方差：大面积组残差方差应明显大于小组。"""
        _, houses = read_csv(BENCHMARK_DIR + "/benchmark_house.csv")
        # 用仅含截距+面积的粗模型取残差
        X = [[1.0, float(r["面积㎡"])] for r in houses]
        y = [math.log(float(r["成交单价元每平"])) for r in houses]
        b = ols(X, y)
        resid = [y[i] - (b[0] + b[1] * X[i][1]) for i in range(len(y))]
        small = [resid[i] for i in range(len(resid)) if float(houses[i]["面积㎡"]) < 80]
        large = [resid[i] for i in range(len(resid)) if float(houses[i]["面积㎡"]) > 130]
        var_small = sum(v * v for v in small) / len(small)
        var_large = sum(v * v for v in large) / len(large)
        self.assertGreater(var_large, var_small,
                           "未检出异方差（大面积组残差方差未大于小组）")


if __name__ == "__main__":
    unittest.main()
