#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
价格指数回归测试。

缺陷 #1（Case-Shiller 二阶方差共线）：
    当持有间隔只有 2 种取值时，设计矩阵 [1, g, g²] 完全共线
    （g² = −2·1 + 3·g 对 g∈{1,2} 恒成立），矩阵奇异后静默退化为常数拟合，
    σ_v 被错估为 0。修复后按间隔取值个数自适应降阶。
    → 本用例用只有 2 种间隔的数据跑 case_shiller_index，断言不抛异常且 σ_v > 0。

另含：benchmark 真实价格指数还原（BMN / Case-Shiller 对照已知真值 γ_t）。
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

from price_index import case_shiller_index, bmn_index, load_pairs
from core import read_csv

BENCHMARK_DIR = os.path.join(ROOT, "benchmark")

# 与 make_benchmark.py 中写死的真实价格指数口径一致（避免导入生成器副作用）
def true_gamma(t):
    return 0.0040 * (t - 1) + 0.0200 * math.sin(2.0 * math.pi * (t - 1) / 12.0)


def _build_two_gap_pairs(seed=2024):
    """构造只有 2 种持有间隔（gap∈{1,2}）的重复成交对。

    每个物业两期成交共享固定效应，并叠加随机游走分量 w~N(0, gap·σ_w²)，
    使对数价差方差随 gap 增大 → 方差方程斜率（σ_v²）必为正。
    """
    rnd = random.Random(seed)
    SIG_W = 0.06
    SIG_U = 0.05
    GAMMA = {1: 0.0, 2: 0.03, 3: 0.05}   # 期效应真值（任意设定，仅用于制造价差）
    pairs = []
    for i in range(500):
        t1 = rnd.choice([1, 2])           # 首次期 ∈ {1,2}
        gap = rnd.choice([1, 2])         # 持有间隔只有 1 或 2
        t2 = t1 + gap
        if t2 > 3 or t2 not in GAMMA:
            continue
        pi = rnd.gauss(0, 0.18)           # 物业固定效应
        w = rnd.gauss(0, SIG_W * math.sqrt(gap))
        e1 = rnd.gauss(0, SIG_U)
        e2 = rnd.gauss(0, SIG_U)
        base = 10.0 + pi
        lp1 = base + GAMMA[t1] + e1
        lp2 = base + GAMMA[t2] + w + e2
        # case_shiller_index 期望的是 load_pairs() 的**解析后**结构（键名 t1/t2/p1/p2），
        # 而不是原始 CSV 表头。这里同时给出两套键，避免调用处再转换一次。
        pairs.append({"物业编号": "X%04d" % i, "首次交易期": t1, "再次交易期": t2,
                      "首次成交价": math.exp(lp1), "再次成交价": math.exp(lp2),
                      "id": "X%04d" % i, "t1": str(t1), "t2": str(t2),
                      "p1": math.exp(lp1), "p2": math.exp(lp2)})
    return pairs


class TestCaseShillerTwoGap(unittest.TestCase):
    """缺陷 #1 回归：2 种持有间隔不得退化、σ_v 不得错估为 0。"""

    def test_no_exception_and_sigma_v_positive(self):
        pairs = _build_two_gap_pairs()
        self.assertGreaterEqual(len(pairs), 100, "构造的配对过少")
        # 不应抛异常（修复前会静默退化；此处验证修复后正常返回）
        res = case_shiller_index(pairs)
        self.assertIn("sigma_v", res, "返回结构缺少 sigma_v")
        # 因随机游走使方差随 gap 增大，σ_v 必须为正数（修复前恒为 0）
        self.assertGreater(res["sigma_v"], 1e-4,
                            "σ_v 应 > 0（方差随持有间隔增大），实际=%.2e" % res["sigma_v"])
        # 仅 2 种间隔 → 方差方程应为 linear 设定（自适应降阶生效）
        self.assertEqual(res["var_spec"], "linear",
                         "2 种间隔应自动降阶为 linear，实际=%s" % res["var_spec"])


class TestBenchmarkIndexRecovery(unittest.TestCase):
    """benchmark 真实价格指数还原：BMN / Case-Shiller 应逼近已知真值 exp(γ_t−γ_1)·100。"""

    @classmethod
    def setUpClass(cls):
        _, rows = read_csv(BENCHMARK_DIR + "/benchmark_pairs.csv")
        cls.pairs = load_pairs(BENCHMARK_DIR + "/benchmark_pairs.csv")
        # 真值同时以 int 与 str 两种键登记：指数结果的期标签可能是 '1'/'2'（字符串），
        # 而这里天然是 int。键类型不匹配会抛 KeyError —— 属测试自身缺陷，非实现问题。
        _base = sorted({int(r["首次交易期"]) for r in rows}
                       | {int(r["再次交易期"]) for r in rows})
        cls.truth = {}
        for t in _base:
            v = math.exp(true_gamma(t) - true_gamma(1)) * 100.0
            cls.truth[t] = v
            cls.truth[str(t)] = v

    def test_bmn_recovers_true_index(self):
        res = bmn_index(self.pairs)
        for t in res["periods"]:
            true = self.truth[t]
            self.assertLess(abs(res["index"][t] - true) / true, 0.10,
                            "BMN 在期 %s 偏离真值 %.1f%%（真值=%.2f，估计=%.2f）"
                            % (t, abs(res["index"][t] - true) / true * 100,
                               true, res["index"][t]))

    def test_cs_recovers_true_index(self):
        res = case_shiller_index(self.pairs)
        for t in res["periods"]:
            true = self.truth[t]
            self.assertLess(abs(res["index"][t] - true) / true, 0.10,
                            "Case-Shiller 在期 %s 偏离真值 %.1f%%（真值=%.2f，估计=%.2f）"
                            % (t, abs(res["index"][t] - true) / true * 100,
                               true, res["index"][t]))


if __name__ == "__main__":
    unittest.main()
