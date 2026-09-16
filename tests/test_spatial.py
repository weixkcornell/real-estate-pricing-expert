#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
空间计量回归测试。

缺陷 #2（SEM 维度错配）：
    (I−λW)X 曾被误写成对「行」做矩阵-向量乘（维度错配），设计矩阵退化、求逆奇异。
    修复后对 X 每一「列」施加 (I−λW) 变换。→ sem_mle 应可估计 λ，且系数与 OLS 同量级。

缺陷 #3（回归克里金假精度）：
    样本内 R²=1.000、MAE=0 是同点位插值必然精确复现观测的「自我实现的假精度」。
    修复后统一返回 LOO 样本外指标，MAE 必须 > 0（MAE==0 即假精度）。

另含 SAR 冒烟测试（不抛异常、ρ 可估计）。
"""

import math
import os
import random
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

from spatial_model import (sem_mle, sar_mle, regression_kriging, distance_matrix,
                            knn_weights, row_standardize, inverse, matvec)
from core import ols, read_csv

BENCHMARK_DIR = os.path.join(ROOT, "benchmark")


def _gen_sem_data(n=250, lam_true=0.5, seed=12345):
    """生成带空间误差（SEM）DGP 的数据：y = Xβ + (I−λW)⁻¹ ζ。"""
    rnd = random.Random(seed)
    X, coords = [], []
    for _ in range(n):
        area = rnd.uniform(45.0, 160.0)
        age = rnd.uniform(0.0, 30.0)
        cbd = rnd.uniform(1.0, 25.0)
        subway = rnd.uniform(0.1, 5.0)
        school = rnd.choice([0, 1])
        X.append([1.0, area, age, cbd, subway, school])
        coords.append([rnd.uniform(116.0, 116.6), rnd.uniform(39.7, 40.1)])
    D = distance_matrix(coords)
    W = row_standardize(knn_weights(D, 6))
    n_ = len(X)
    I = [[1.0 if a == b else 0.0 for b in range(n_)] for a in range(n_)]
    A = [[I[a][b] - lam_true * W[a][b] for b in range(n_)] for a in range(n_)]
    Ainv = inverse(A)
    zeta = [rnd.gauss(0, 0.05) for _ in range(n_)]
    eps = matvec(Ainv, zeta)
    beta_true = [10.3, 0.006, -0.012, -0.015, -0.020, 0.060]
    y = [sum(X[i][j] * beta_true[j] for j in range(len(beta_true))) + eps[i]
         for i in range(n_)]
    return W, X, y


class TestSEM(unittest.TestCase):
    """缺陷 #2 回归：SEM 维度错配修复后，λ 可估计、系数与 OLS 同量级。"""

    def test_sem_runs_and_lambda_estimable(self):
        W, X, y = _gen_sem_data()
        res = sem_mle(W, X, y)              # 修复前此处会因维度错配抛异常/退化解
        self.assertIn("lambda", res)
        self.assertIsNotNone(res["lambda"], "λ 未估计（设计矩阵退化）")
        self.assertGreater(res["lambda"], -0.98)
        self.assertLess(res["lambda"], 0.98)

    def test_sem_coef_same_magnitude_as_ols(self):
        W, X, y = _gen_sem_data()
        res = sem_mle(W, X, y)
        ols_b = ols(X, y)
        sem_b = res["beta"]
        self.assertEqual(len(sem_b), len(ols_b))
        for j in range(len(ols_b)):
            # 同一量级：两者比值落在 [0.5, 2.0]（OLS 与 SEM 对 β 均一致）
            lo, hi = min(ols_b[j], sem_b[j]), max(ols_b[j], sem_b[j])
            denom = abs(lo) if abs(lo) > 1e-9 else 1e-9
            self.assertLess(hi / denom, 2.0,
                            "SEM 系数 %d 与 OLS 量级不符：%.4f vs %.4f" % (j, sem_b[j], ols_b[j]))


class TestRegressionKriging(unittest.TestCase):
    """缺陷 #3 回归：必须返回 LOO 样本外 MAE，且 MAE > 0（拒绝假精度）。"""

    @classmethod
    def setUpClass(cls):
        _, rows = read_csv(BENCHMARK_DIR + "/benchmark_house.csv")
        rows = rows[:150]   # 取子集，保证 O(n²) 克里金可控
        X, y, C = [], [], []
        for r in rows:
            a = float(r["面积㎡"]); cbd = float(r["到市中心距离km"])
            X.append([1.0, a, cbd])
            y.append(float(r["成交单价元每平"]))
            C.append([float(r["经度"]), float(r["纬度"])])
        cls.X, cls.y, cls.D = X, y, distance_matrix(C)

    def test_returns_loo_metrics(self):
        res = regression_kriging(self.X, self.y, self.D)
        self.assertIn("metrics_loo", res, "未返回 LOO 样本外指标")
        self.assertIsNotNone(res["metrics_loo"], "LOO 指标为 None（样本不足）")

    def test_loo_mae_positive_not_fake(self):
        res = regression_kriging(self.X, self.y, self.D)
        mae = res["metrics_loo"]["MAE"]
        self.assertGreater(mae, 0.0,
                           "LOO MAE == 0：同点位插值假精度（应报告样本外指标）")


class TestSARSmoke(unittest.TestCase):
    """SAR 冒烟测试：不抛异常、ρ 可估计（非缺陷固化，纯数值健全性）。"""

    def test_sar_runs(self):
        W, X, y = _gen_sem_data(lam_true=0.3, seed=777)
        res = sar_mle(W, X, y)
        self.assertIn("rho", res)
        self.assertIsNotNone(res["rho"])


if __name__ == "__main__":
    unittest.main()
