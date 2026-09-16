#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
机器学习 AVM 回归测试。

缺陷 #4（高斯过程只标准化 X）：
    仅标准化 X 而未标准化 y，核幅 σ_f 与价格量纲失配，核矩阵退化 →
    区间宽度约 1 元/㎡、覆盖率 0%（形同无区间）。
    修复后 X 与 y 都标准化。→ GaussianProcessAVM 的区间宽度应与 y 量级相称
    （不是个位数），且覆盖率在合理区间。
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

from ml_valuation import GaussianProcessAVM
from core import read_csv

BENCHMARK_DIR = os.path.join(ROOT, "benchmark")


class TestGaussianProcessAVM(unittest.TestCase):
    """缺陷 #4 回归：GP 区间宽度应与 y 量级相称、覆盖率合理。"""

    @classmethod
    def setUpClass(cls):
        _, rows = read_csv(BENCHMARK_DIR + "/benchmark_house.csv")
        rows = rows[:150]   # 子集：GP 超参网格搜索 O(n³) 可控
        X, y = [], []
        for r in rows:
            X.append([float(r["面积㎡"]), float(r["房龄"]), float(r["到市中心距离km"]),
                      float(r["到最近地铁距离km"]), float(r["学区"]),
                      float(r["经度"]), float(r["纬度"])])
            y.append(float(r["成交单价元每平"]))
        cls.X, cls.y = X, y
        cls.gp = GaussianProcessAVM().fit(X, y)
        cls.mu, cls.lo, cls.hi = cls.gp.predict(X)

    def test_interval_width_comparable_to_y_scale(self):
        """区间宽度中位数应远大于个位数（与单价 3 万元量级相称，而非 1 元/㎡）。"""
        widths = [self.hi[i] - self.lo[i] for i in range(len(self.y))]
        widths.sort()
        med_w = widths[len(widths) // 2]
        y_scale = max(self.y) - min(self.y)
        self.assertGreater(med_w, 100.0,
                           "区间宽度中位数仅 %.1f 元/㎡：与价格量纲失配（疑未标准化 y）" % med_w)
        # 宽度应与 y 的量级同阶（中位宽度应不低于 y 跨度的 1%）
        self.assertGreater(med_w, y_scale * 0.01,
                           "区间宽度与 y 量级不符：%.1f vs y跨%.1f" % (med_w, y_scale))

    def test_coverage_reasonable(self):
        """95% 预测区间覆盖率应在合理区间（明显 > 0%，通常接近名义水平）。"""
        hit = sum(1 for i in range(len(self.y)) if self.lo[i] <= self.y[i] <= self.hi[i])
        cov = hit / len(self.y)
        self.assertGreater(cov, 0.4,
                           "覆盖率仅 %.1f%%：区间被严重低估（修复前为 0%%）" % (cov * 100))
        self.assertLessEqual(cov, 1.0, "覆盖率异常")


if __name__ == "__main__":
    unittest.main()
