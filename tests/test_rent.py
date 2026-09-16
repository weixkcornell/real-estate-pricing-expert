#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
租金指数回归测试。

缺陷 #5（租金指数 Box-Cox Jacobian 不一致）：
    原实现对「对数形式」单独走一条不带 Jacobian 的分支，与 λ=0 的 Box-Cox 相比
    凭空多出 Σln R，模型选择必然偏向对数；且 λ≠0 时仍用 exp(δ) 算指数（错误）。
    修复后统一用 λ=0 的 Box-Cox 参数化（Jacobian 统一），指数构造随 λ 变化。
    → 本用例断言返回结构同时含主口径（半对数 index）与诊断口径（index_selected），
      且半对数口径能还原已知真值租金指数（容差内）。
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

from rent_model import stratified_rent_index
from core import read_csv

BENCHMARK_DIR = os.path.join(ROOT, "benchmark")

# 与 make_benchmark.py 中写死的真实租金指数口径一致（避免导入生成器副作用）
def true_eta(t):
    return 0.0020 * (t - 1) + 0.0150 * math.sin(2.0 * math.pi * (t - 1) / 12.0 + 1.0)


class TestStratifiedRentIndex(unittest.TestCase):
    """缺陷 #5 回归：两口径齐全 + 半对数口径还原真值。"""

    @classmethod
    def setUpClass(cls):
        _, rows = read_csv(BENCHMARK_DIR + "/benchmark_rent.csv")
        cls.rows = rows
        cls.res = stratified_rent_index(rows, "月租元", "期", x_names=["面积㎡"])
        cls.periods = sorted({int(r["期"]) for r in rows})
        # 期标签在 index 结果里是字符串；真值同时以 int/str 登记，避免键类型不匹配
        cls.truth = {}
        for t in cls.periods:
            v = math.exp(true_eta(t) - true_eta(cls.periods[0])) * 100.0
            cls.truth[t] = v
            cls.truth[str(t)] = v

    def test_both_calibers_present(self):
        """返回结构必须同时含主口径（半对数）与诊断口径（选中 λ）。"""
        self.assertIn("index", self.res, "缺少主口径 index（半对数）")
        self.assertIn("index_selected", self.res, "缺少诊断口径 index_selected")
        self.assertIn("lambda", self.res, "缺少选中的 λ")

    def test_semilog_caliber_recovers_truth(self):
        """半对数主口径应还原已知真值租金指数（容差内）。"""
        idx = self.res["index"]
        for t in self.periods:
            key = t if t in idx else str(t)      # index 的期标签是字符串
            true = self.truth[t]
            self.assertLess(abs(idx[key] - true) / true, 0.10,
                            "租金指数在期 %s 偏离真值 %.1f%%（真值=%.2f，估计=%.2f）"
                            % (t, abs(idx[key] - true) / true * 100, true, idx[key]))


if __name__ == "__main__":
    unittest.main()
