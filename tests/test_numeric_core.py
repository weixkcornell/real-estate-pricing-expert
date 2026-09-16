#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数值正确性基础回归测试（"自建数值库必须有测试护住"的直接落实）。

覆盖：
  - inverse()：随机正定矩阵 A·A⁻¹ ≈ I
  - ols()：已知 DGP 系数还原在容差内
  - det()：与独立的代数余子式展开（参考实现）一致；奇异矩阵返回 0
  - Unit.chain_mom：环比链式定基的正确性
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

from core import inverse, ols, matmul, matvec   # 高斯-约当求逆 / 最小二乘
from spatial_model import det                   # LU 行列式
from base import Unit                            # 适配器单位换算（含 chain_mom）


def _ref_det(A):
    """独立的参考行列式：递归代数余子式展开（与模块 LU 实现无关，用于交叉验证）。"""
    n = len(A)
    if n == 1:
        return A[0][0]
    if n == 2:
        return A[0][0] * A[1][1] - A[0][1] * A[1][0]
    s = 0.0
    for j in range(n):
        minor = [[A[r][c] for c in range(n) if c != j] for r in range(1, n)]
        s += ((-1) ** j) * A[0][j] * _ref_det(minor)
    return s


def _rand_pd(n, seed):
    """生成随机正定矩阵 A = M·Mᵀ + I。"""
    rnd = random.Random(seed)
    M = [[rnd.gauss(0, 1.0) for _ in range(n)] for _ in range(n)]
    MMt = matmul(M, [[M[r][c] for r in range(n)] for c in range(n)])
    return [[MMt[i][j] + (1.0 if i == j else 0.0) for j in range(n)] for i in range(n)]


class TestInverse(unittest.TestCase):
    def test_inverse_random_pd(self):
        """随机正定矩阵：A·A⁻¹ 应等于单位阵（容差 1e-8）。"""
        for seed in (1, 7, 42, 2026):
            A = _rand_pd(5, seed)
            Ainv = inverse(A)
            I = [[1.0 if i == j else 0.0 for j in range(5)] for i in range(5)]
            prod = matmul(A, Ainv)
            max_err = max(abs(prod[i][j] - I[i][j]) for i in range(5) for j in range(5))
            self.assertLess(max_err, 1e-8,
                            "inverse() 在 seed=%d 下 A·A⁻¹ 偏离单位阵 %.2e" % (seed, max_err))

    def test_inverse_singular_raises(self):
        """奇异矩阵必须显式报错，而非静默返回错值（数值稳定性的硬纪律）。"""
        singular = [[1.0, 2.0], [2.0, 4.0]]  # 列共线
        with self.assertRaises(ValueError):
            inverse(singular)


class TestOls(unittest.TestCase):
    def test_ols_known_dgp(self):
        """已知 DGP y = 1 + 2·x1 − 3·x2：系数应精确还原（无噪声）。"""
        X = [[1.0, 1.0, 0.0], [1.0, 0.0, 1.0], [1.0, 2.0, 1.0], [1.0, 1.0, 3.0], [1.0, 4.0, 2.0]]
        y = [1.0 + 2.0 * r[1] - 3.0 * r[2] for r in X]
        beta = ols(X, y)
        for j, truth in enumerate([1.0, 2.0, -3.0]):
            self.assertAlmostEqual(beta[j], truth, places=9,
                                    msg="ols() 系数 %d 未还原真值" % j)

    def test_ols_noisy_recovery(self):
        """含噪声的已知 DGP：系数应在容差内还原（大样本）。"""
        rnd = random.Random(99)
        truth = [0.5, -1.2, 0.8]
        X, y = [], []
        for _ in range(2000):
            x1, x2 = rnd.uniform(-2, 2), rnd.uniform(-2, 2)
            X.append([1.0, x1, x2])
            y.append(truth[0] + truth[1] * x1 + truth[2] * x2 + rnd.gauss(0, 0.1))
        beta = ols(X, y)
        for j in range(3):
            self.assertLess(abs(beta[j] - truth[j]), 0.02,
                            "ols() 含噪 DGP 系数 %d 偏差过大" % j)


class TestDet(unittest.TestCase):
    def test_det_matches_reference(self):
        """det() 应与独立的代数余子式展开一致（随机矩阵）。"""
        rnd = random.Random(5)
        for n in (2, 3, 4, 5):
            A = [[rnd.uniform(-3, 3) for _ in range(n)] for _ in range(n)]
            self.assertAlmostEqual(det(A), _ref_det(A), places=6,
                                   msg="det() 与参考实现在 %d×%d 不一致" % (n, n))

    def test_det_2x2_analytic(self):
        A = [[2.0, 3.0], [1.0, 4.0]]
        self.assertAlmostEqual(det(A), 2.0 * 4.0 - 3.0 * 1.0, places=12)

    def test_det_singular_zero(self):
        """奇异矩阵行列式应为 0（或近似 0）。"""
        A = [[1.0, 2.0, 3.0], [2.0, 4.0, 6.0], [0.0, 1.0, 0.0]]
        self.assertAlmostEqual(det(A), 0.0, places=6)

    def test_det_transpose_invariant(self):
        rnd = random.Random(11)
        A = [[rnd.uniform(-2, 2) for _ in range(4)] for _ in range(4)]
        AT = [[A[j][i] for j in range(4)] for i in range(4)]
        self.assertAlmostEqual(det(A), det(AT), places=10)


class TestChainMom(unittest.TestCase):
    def test_chain_mom_basic(self):
        """环比 [0, 1.0, 2.0]%（百分点）→ 定基 [100, 101, 103.02]。"""
        idx = Unit.chain_mom([0.0, 1.0, 2.0])
        self.assertEqual(idx[0], 100.0)
        self.assertAlmostEqual(idx[1], 101.0, places=6)
        self.assertAlmostEqual(idx[2], 101.0 * 1.02, places=6)

    def test_chain_mom_negative(self):
        """环比含负增长：链式乘法正确处理下跌。"""
        idx = Unit.chain_mom([0.0, -2.0, 5.0])
        self.assertAlmostEqual(idx[1], 98.0, places=6)
        self.assertAlmostEqual(idx[2], 98.0 * 1.05, places=6)

    def test_chain_mom_none_passthrough(self):
        """缺失（None）环比应保持为 None，不臆造数据。"""
        idx = Unit.chain_mom([0.0, None, 3.0])
        self.assertEqual(idx[0], 100.0)
        self.assertIsNone(idx[1])
        self.assertIsNone(idx[2])


if __name__ == "__main__":
    unittest.main()
