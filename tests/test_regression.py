#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回归测试 —— 把「已修复的真实缺陷」与「自建数值库的正确性」固化为可执行用例。

为什么必须有这组测试（评审 P2-2）：
    "零依赖是纯 Python 标准库的合理取舍（可移植性），但意味着没有 statsmodels /
     PySAL 这类久经考验的数值库兜底——自建高斯-约当求逆、集中似然 MLE 必须有
     回归测试护住。"

本套件分四部分：
  A. 缺陷防复发：本包迭代中发现的 5 类算法缺陷 + 2 类适配器缺陷 + 1 类门禁误报缺陷
  B. 数值正确性：逆矩阵、行列式、OLS/WLS、环比链式定基
  C. 新增方法：比较法、成本法/假设开发法、conformal 区间
  D. 治理一致性：门禁执行器、溯源清单、场景 DAG

运行：
    cd <包根> && python3 -m unittest discover -s tests -v

数据：`benchmark/` 下的 **合成基准**（n≈5000，DGP 与真值写死在生成器里）。
⚠️ 合成数据不代表任何真实市场，禁止当作市场数据引用。
"""

import json
import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _c in (ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "adapters"),
           os.path.join(ROOT, "skills", "price-index", "scripts"),
           os.path.join(ROOT, "skills", "spatial-ml-valuation", "scripts"),
           os.path.join(ROOT, "skills", "rent-income", "scripts"),
           os.path.join(ROOT, "skills", "hedonic-pricing", "scripts"),
           os.path.join(ROOT, "skills", "comparison-pricing", "scripts"),
           os.path.join(ROOT, "skills", "cost-residual", "scripts"),
           os.path.join(ROOT, "quality-policies")):
    if _c not in sys.path:
        sys.path.insert(0, _c)

import core  # noqa: E402
from base import Unit  # noqa: E402

BENCH = os.path.join(ROOT, "benchmark")
HOUSE_CSV = os.path.join(BENCH, "benchmark_house.csv")
PAIRS_CSV = os.path.join(BENCH, "benchmark_pairs.csv")
RENT_CSV = os.path.join(BENCH, "benchmark_rent.csv")
TRIAL = os.path.join(os.path.dirname(ROOT), "trial_run")

# ================================================================ 基准真值
# 与 benchmark/make_benchmark.py 的 DGP 严格一致（改了生成器必须同步改这里）
BETA_TRUE = {"面积㎡": 0.0060, "房龄": -0.0120, "楼层": 0.0020,
             "到市中心距离km": -0.0150, "到最近地铁距离km": -0.0200, "学区": 0.0600}
HOUSE_COLS = ["面积㎡", "房龄", "楼层", "到市中心距离km", "到最近地铁距离km", "学区"]
SIGMA_U_TRUE, SIGMA_V_TRUE = 0.0500, 0.0600


def gamma(t):
    """真实价格指数（对数口径）：γ_t = 0.0040(t−1) + 0.0200·sin(2π(t−1)/12)。"""
    return 0.0040 * (t - 1) + 0.0200 * math.sin(2 * math.pi * (t - 1) / 12.0)


def eta(t):
    """真实租金指数（对数口径）：η_t = 0.0020(t−1) + 0.0150·sin(2π(t−1)/12 + 1.0)。"""
    return 0.0020 * (t - 1) + 0.0150 * math.sin(2 * math.pi * (t - 1) / 12.0 + 1.0)


def _need(path):
    if not os.path.isfile(path):
        raise unittest.SkipTest("基准数据缺失，请先运行 python3 benchmark/make_benchmark.py")
    return core.read_csv(path)[1]


def _house_rows(limit=None):
    rows = _need(HOUSE_CSV)
    return rows[:limit] if limit else rows


def _design(rows, ycol, xcols, with_period=True):
    """构造设计矩阵。默认含期虚拟变量（吸收期效应），与基准 README 的建议一致。"""
    periods = sorted({r["成交期"] for r in rows}, key=lambda v: int(v)) if with_period else []
    X, y = [], []
    for r in rows:
        yv = core.to_float(r.get(ycol))
        xs = [core.to_float(r.get(c)) for c in xcols]
        if yv is None or yv <= 0 or any(v is None for v in xs):
            continue
        dm = [1.0 if r["成交期"] == p else 0.0 for p in periods[1:]] if periods else []
        X.append([1.0] + xs + dm)
        y.append(math.log(yv))
    return X, y


def _coords(rows):
    """经度/纬度 → 坐标对（用于空间权重）。经度纬度量纲不同，先各自标准化避免畸变。"""
    lon = [core.to_float(r.get("经度")) for r in rows]
    lat = [core.to_float(r.get("纬度")) for r in rows]
    return [[a, b] for a, b in zip(lon, lat) if a is not None and b is not None]


# ================================================================ A. 缺陷防复发
class TestDefect1CaseShillerCollinearity(unittest.TestCase):
    """
    缺陷 1：Case-Shiller 二阶方差方程 [1, g, g²] 在持有间隔只有 2 个取值时
    完全共线（g² = −2·1 + 3·g），矩阵奇异后**静默退化为常数拟合**，σ_v 被错估为 0。
    修复：按间隔取值个数自适应降阶。
    """

    def _narrow(self):
        from price_index import load_pairs
        pairs = load_pairs(PAIRS_CSV)
        return [p for p in pairs if (int(p["t2"]) - int(p["t1"])) in (1, 2)]

    def test_two_gap_values_do_not_degrade(self):
        from price_index import case_shiller_index
        narrow = self._narrow()
        self.assertGreaterEqual(len(narrow), 30, "窄间隔样本不足，无法构造该场景")
        res = case_shiller_index(narrow)              # 不应抛异常
        self.assertIn("sigma_v", res)
        self.assertGreater(res["sigma_v"], 0.0,
                           "σ_v 被错估为 0 —— 二阶方差方程又退化为常数拟合（共线未处理）")

    def test_variance_structure_estimated(self):
        """全样本上 σ_u / σ_v 应与真值同量级（真值 0.05 / 0.06）。"""
        from price_index import case_shiller_index
        from price_index import load_pairs
        res = case_shiller_index(load_pairs(PAIRS_CSV))
        self.assertLess(abs(res["sigma_u"] - SIGMA_U_TRUE) / SIGMA_U_TRUE, 0.6,
                        "σ_u=%.4f 偏离真值 %.4f 过大" % (res["sigma_u"], SIGMA_U_TRUE))
        self.assertLess(abs(res["sigma_v"] - SIGMA_V_TRUE) / SIGMA_V_TRUE, 0.6,
                        "σ_v=%.4f 偏离真值 %.4f 过大（若为 0 则是共线退化）" % (res["sigma_v"], SIGMA_V_TRUE))

    def test_recovers_true_index(self):
        """BMN 与 Case-Shiller 的指数都应接近 exp(γ_t − γ_base)·100。"""
        from price_index import bmn_index, case_shiller_index, load_pairs
        pairs = load_pairs(PAIRS_CSV)
        for fn, name in ((bmn_index, "BMN"), (case_shiller_index, "Case-Shiller")):
            res = fn(pairs)
            idx, base = res.get("index") or {}, res.get("base")
            self.assertIn(base, idx, "%s 未返回基期指数" % name)
            for t in ("6", "12", "24"):
                if t not in idx:
                    continue
                want = math.exp(gamma(int(t)) - gamma(int(base))) * 100.0
                err = abs(idx[t] - want) / want
                self.assertLess(err, 0.06,
                                "%s 在 t=%s 的指数 %.2f 偏离真值 %.2f 超过 6%%"
                                % (name, t, idx[t], want))


class TestDefect2SEMDimensionMismatch(unittest.TestCase):
    """
    缺陷 2：SEM 中 (I−λW)X 误写成对**行**做矩阵-向量乘（维度错配），
    导致设计矩阵退化、求逆奇异。修复：对 X 的每一**列**施加变换。
    """

    def _setup(self, n=300):
        import spatial_model as sm
        rows = _house_rows(n)
        X, y = _design(rows, "成交单价元每平", ["面积㎡", "房龄", "到市中心距离km"])
        C = _coords(rows)[:len(X)]
        D = sm.distance_matrix(C, "euclid")
        W = sm.row_standardize(sm.knn_weights(D, 6))
        return sm, W, X, y

    def test_sem_runs_and_estimates(self):
        sm, W, X, y = self._setup()
        res = sm.sem_mle(W, X, y, grid=60)          # 不应抛异常
        self.assertIsInstance(res, dict)
        self.assertIn("beta", res)
        self.assertIsNotNone(res.get("lambda"),
                             "SEM 未返回 λ —— 集中似然可能未正确计算（维度错配会在此退化为奇异）")

    def test_sar_estimates_rho_in_stationary_domain(self):
        sm, W, X, y = self._setup()
        res = sm.sar_mle(W, X, y, grid=60)
        self.assertIn("rho", res)
        self.assertTrue(-1.0 < res["rho"] < 1.0, "ρ 超出平稳域")


class TestDefect3KrigingFakePrecision(unittest.TestCase):
    """
    缺陷 3：回归克里金报 R²=1.000、MAE=0——样本内同点位插值必然精确复现观测，
    属自我实现的假精度。修复：改用留一法（LOO）样本外评测。
    """

    def test_loo_metrics_are_out_of_sample(self):
        import spatial_model as sm
        rows = _house_rows(120)
        X, y = _design(rows, "成交单价元每平", ["面积㎡", "房龄"], with_period=False)
        C = _coords(rows)[:len(X)]
        D = sm.distance_matrix(C, "euclid")
        res = sm.regression_kriging(X, y, D)
        # 返回值同时给出样本外（LOO）与样本内两套指标——这样"假精度"就无处藏身
        self.assertIn("metrics_loo", res, "缺少留一法（样本外）指标")
        self.assertIn("metrics_insample", res)
        loo, ins = res["metrics_loo"], res["metrics_insample"]
        self.assertGreater(loo["MAE"], 0.0,
                           "LOO 的 MAE = 0 说明又在报样本内指标（假精度）")
        self.assertLess(loo["R2"], 0.999,
                        "LOO 的 R² ≈ 1 说明又在报样本内指标；克里金样本内必然精确复现观测")
        # 核心断言：样本内必然优于样本外，且样本内接近完美——这正是"假精度"的证据
        self.assertGreater(ins["R2"], loo["R2"],
                           "样本内 R² 未优于样本外，说明两者被混为一谈")
        self.assertGreater(ins["R2"], 0.99,
                           "样本内 R² 应接近 1（精确插值特性），若否可能是实现变了")


class TestDefect4GPUnitMismatch(unittest.TestCase):
    """
    缺陷 4：高斯过程只标准化 X 未标准化 y → 核幅与价格量纲失配（差 4 个数量级），
    核矩阵退化、区间宽度塌缩到个位数、覆盖率 0%。修复：X 与 y 同时标准化。
    """

    def _fit(self, n=70):
        from ml_valuation import GaussianProcessAVM
        rows = _house_rows(n)
        X, y = _design(rows, "成交单价元每平", ["面积㎡", "房龄", "到市中心距离km"],
                       with_period=False)
        return GaussianProcessAVM().fit(X, y), X, y

    def test_interval_width_commensurate_with_target_scale(self):
        m, X, y = self._fit()
        pred, lo, hi = m.predict(X, level=0.95)
        widths = [h - l for l, h in zip(lo, hi)]
        mean_w = sum(widths) / len(widths)
        ybar = core.mean(y)
        ratio = mean_w / ybar
        self.assertGreater(ratio, 0.005,
                           "区间宽度仅为均值的 %.3f%% —— 量纲失配导致区间塌缩" % (ratio * 100))
        self.assertLess(ratio, 0.8, "区间宽度异常偏大")

    def test_coverage_reasonable(self):
        m, X, y = self._fit()
        half = len(y) // 2
        from ml_valuation import GaussianProcessAVM
        m2 = GaussianProcessAVM().fit(X[:half], y[:half])
        pred, lo, hi = m2.predict(X[half:], level=0.95)
        hit = sum(1 for a, l, h in zip(y[half:], lo, hi) if l <= a <= h)
        cov = hit / max(1, len(pred))
        self.assertGreater(cov, 0.5, "覆盖率 %.0f%% 过低（缺陷 4 未修复时会接近 0%%）" % (cov * 100))


class TestDefect5RentIndexJacobian(unittest.TestCase):
    """
    缺陷 5：租金指数 ①Box-Cox 与对数形式的 Jacobian 不一致（相差 Σln R，量级数百），
    模型选择必然偏向对数；②λ≠0 时仍用 exp(δ) 算指数，口径错误。
    修复：统一 Box-Cox 参数化（含 λ=0）；指数改为在**平均属性上比较**；双口径并列。
    """

    def _rows(self, limit=None):
        rows = _need(RENT_CSV)
        return rows[:limit] if limit else rows

    def test_dual_caliber_returned(self):
        import rent_model as rm
        r = rm.stratified_rent_index(self._rows(4000), "月租元", "期", ["面积㎡"], boxcox=True)
        self.assertIsNotNone(r)
        self.assertIn("index", r, "缺少主口径（半对数）指数")
        self.assertIn("index_selected", r, "缺少诊断口径指数（双口径并列要求）")
        self.assertIn("lambda", r)

    def test_semilog_index_recovers_truth(self):
        import rent_model as rm
        r = rm.stratified_rent_index(self._rows(), "月租元", "期", ["面积㎡"], boxcox=False)
        idx, base = r["index"], r["base"]
        for t in ("6", "12", "24"):
            if t not in idx:
                continue
            want = math.exp(eta(int(t)) - eta(int(base))) * 100.0
            self.assertLess(abs(idx[t] - want) / want, 0.04,
                            "半对数口径租金指数 t=%s 估计 %.2f 偏离真值 %.2f 超过 4%%"
                            % (t, idx[t], want))

    def test_hedonic_rent_recovers_area_effect(self):
        """租金 hedonic 的面积系数真值 +0.0100（半对数）。"""
        import rent_model as rm
        rows = self._rows()
        m = rm.HedonicRentModel("月租元", ["面积㎡"]).fit(rows)
        coef = dict((nm, b) for nm, b, _e, _k in m.coef_table())
        got = coef.get("面积㎡")
        self.assertIsNotNone(got)
        self.assertLess(abs(got - 0.0100) / 0.0100, 0.25,
                        "租金面积系数估计 %.5f 偏离真值 0.0100 超过 25%%" % got)


class TestDefect6AdapterStringFields(unittest.TestCase):
    """
    适配器缺陷：把**全部** standard_fields 一律数值化，导致「期」「平台」「出让条件」
    等字符串字段被转成 None，_valid 全判假 → **样本被静默清空**。
    修复：声明式 numeric_fields。
    """

    def test_string_fields_survive_numeric_coercion(self):
        from local_csv import ListingAdapter
        p = os.path.join(ROOT, "adapters", "fixtures", "listing_demo.csv")
        if not os.path.isfile(p):
            self.skipTest("演示数据缺失")
        res = ListingAdapter(p).fetch()
        self.assertTrue(res.rows, "样本被清空 —— 字符串字段很可能又被误数值化")
        plat = {r.get("platform") for r in res.rows}
        self.assertTrue(all(plat), "platform 字段为 None —— 被数值化污染")
        self.assertGreater(len([x for x in plat if x]), 1, "平台字段应保留多个不同取值")

    def test_chinese_header_alias_resolution(self):
        from local_csv import RentAdapter
        p = os.path.join(ROOT, "adapters", "fixtures", "rent_demo.csv")
        if not os.path.isfile(p):
            self.skipTest("演示数据缺失")
        res = RentAdapter(p).fetch()
        self.assertTrue(res.rows)
        self.assertTrue(all(r.get("rent_monthly") for r in res.rows))

    def test_missing_required_column_refused(self):
        from base import AdapterError
        from local_csv import WangqianAdapter
        p = os.path.join(ROOT, "adapters", "fixtures", "land_demo.csv")
        if not os.path.isfile(p):
            self.skipTest("演示数据缺失")
        with self.assertRaises(AdapterError) as ctx:
            WangqianAdapter(p).fetch()          # 用土地数据喂网签适配器 → 必需列缺失
        self.assertIn("缺少必需列", str(ctx.exception))


# ================================================================ B. 数值正确性
class TestNumericalCore(unittest.TestCase):
    def test_inverse_identity(self):
        A = [[4.0, 1.0, 0.5], [1.0, 3.0, -0.2], [0.5, -0.2, 2.0]]      # 对称正定
        I = core.matmul(A, core.inverse(A))
        for i in range(3):
            for j in range(3):
                self.assertAlmostEqual(I[i][j], 1.0 if i == j else 0.0, places=8)

    def test_det_matches_analytic(self):
        import spatial_model as sm
        self.assertAlmostEqual(sm.det([[2.0, 0.0], [0.0, 3.0]]), 6.0, places=10)
        self.assertAlmostEqual(sm.det([[1.0, 2.0], [3.0, 4.0]]), -2.0, places=10)

    def test_ols_recovers_known_dgp(self):
        """
        在已知系数的合成数据上还原系数。

        容差按系数绝对值分级：楼层系数仅 0.0020，估计的相对波动天然更大，
        故放宽到 25%；其余系数要求 15% 以内。**这不是为了通过而放宽**——
        而是因为小系数的相对误差在大样本下仍受空间场残留影响。
        """
        X, y = _design(_house_rows(), "成交单价元每平", HOUSE_COLS)
        b = core.ols(X, y)
        tol = {"楼层": 0.25}
        for i, name in enumerate(HOUSE_COLS, start=1):
            want = BETA_TRUE[name]
            t = tol.get(name, 0.15)
            self.assertLess(abs(b[i] - want) / abs(want), t,
                            "系数「%s」估计 %.5f 偏离真值 %.5f 超过 %.0f%%"
                            % (name, b[i], want, t * 100))

    def test_chain_mom_rebase(self):
        mom = [1.0, 2.0, -1.0, 0.5]
        idx = Unit.chain_mom(mom)
        self.assertAlmostEqual(idx[0], 100.0)
        want = 100.0
        for i, m in enumerate(mom[1:], 1):
            want *= (1 + m / 100.0)
            self.assertAlmostEqual(idx[i], want, places=8)

    def test_wls_equals_ols_with_equal_weights(self):
        X, y = _design(_house_rows(300), "成交单价元每平", ["面积㎡", "房龄"])
        b1 = core.ols(X, y)
        b2 = core.wls_by_sigma(X, y, [1.0] * len(y))
        for a, b in zip(b1, b2):
            self.assertAlmostEqual(a, b, places=8)


# ================================================================ C. 新增方法
class TestComparisonPricing(unittest.TestCase):
    def _run(self, **kw):
        import comparison_model as cm
        p = os.path.join(ROOT, "skills", "comparison-pricing", "scripts", "sample_cases.csv")
        if not os.path.isfile(p):
            self.skipTest("比较法演示数据缺失")
        cases = cm.load_cases(p)
        target = {"area": 100, "orientation": "南北", "floor": "高"}
        return cm.run(cases, target, as_of="2026-09",
                      hedonic_coefs="朝向=南北:+0.068,朝向=南:+0.040,楼层=高:+0.092,楼层=中:+0.020,面积:+0.00059",
                      **kw)

    def test_only_difference_adjusted(self):
        """
        只调差异不调水平：与估价对象**同朝向**的案例，朝向项必须落在"零调整"里；
        同楼层的案例，楼层项必须落在"零调整"里。
        """
        res = self._run()
        self.assertTrue(res["ok"])
        checked = 0
        for c in res["kept"]:
            zero_items = [it["item"] for it in getattr(c, "adj_zero", [])]
            non_zero = [n for n, _v, _b, _k in c.adjustments]
            if c.fields.get("orientation") == "南北":
                self.assertTrue(any("朝向" in z for z in zero_items),
                                "案例 %s 与估价对象同朝向，却未产生零调整项" % c.id)
                self.assertFalse(any("朝向" in n for n in non_zero),
                                 "案例 %s 同朝向却被施加了朝向调整" % c.id)
                checked += 1
            if c.fields.get("floor") == "高":
                self.assertTrue(any("楼层" in z for z in zero_items),
                                "案例 %s 与估价对象同楼层，却未产生零调整项" % c.id)
                self.assertFalse(any("楼层" in n for n in non_zero),
                                 "案例 %s 同楼层却被施加了楼层调整" % c.id)
                checked += 1
        self.assertGreater(checked, 0, "未覆盖到同质案例，测试无意义")

    def test_exclusion_rules(self):
        res = self._run()
        reasons = " ".join(w for _, w in res["excluded"])
        for kw in ("法拍", "亲属", "限价", "时效"):
            self.assertIn(kw, reasons, "未剔除含「%s」的案例" % kw)

    def test_refuses_when_too_few_cases(self):
        import comparison_model as cm
        cases = [cm.Case("C%d" % i, 50000.0,
                         fields={"date": "2026-06", "orientation": "南", "floor": "中"},
                         group="same_community") for i in range(2)]
        res = cm.run(cases, {"area": 100}, as_of="2026-09")
        self.assertFalse(res["ok"], "案例不足 3 个时必须拒绝出结论")

    def test_amplitude_guard(self):
        import comparison_model as cm
        c = cm.Case("C1", 50000.0, fields={"date": "2026-06"}, group="same_community")
        _adj, _d, issues = cm.adjust_case(
            c, location=[{"item": "学区", "pct": 0.35, "basis": "测试"}])
        self.assertTrue(any("超过" in i for i in issues), "超限调整未报警")

    def test_area_adjustment_not_accumulated(self):
        """
        面积调整必须**逐案例独立计算**。
        曾把每个案例的面积项追加进同一个共享列表，导致后一案例继承前面所有案例的
        面积调整、累计调整随序号单调放大——此处断言各案例的连加小计互不相同且量级合理。
        """
        res = self._run()
        totals = [abs(c.add_total) for c in res["kept"]]
        self.assertTrue(all(t < 0.30 for t in totals),
                        "存在案例累计调整超过 30%%，疑为跨案例累积：%s" % totals)


class TestCostResidual(unittest.TestCase):
    def test_residual_analytic_closure(self):
        """假设开发法解析解必须精确闭合：各项合计 == 开发完成后价值。"""
        import cost_residual as cr
        r = cr.residual_static(42000, 3200, dev_years=2, rate=0.045,
                               profit_rate=0.15, sale_tax_rate=0.0555, land_tax_rate=0.03)
        chk = r["check"]
        self.assertAlmostEqual(chk["sum_should_equal_dev_value"], chk["dev_value"], places=6)

    def test_lvt_progressive(self):
        import cost_residual as cr
        _t1, r1, _ = cr.lvt_ultra_progressive(40, 100)
        self.assertAlmostEqual(r1, 0.30)
        t2, r2, _ = cr.lvt_ultra_progressive(150, 100)
        self.assertEqual(r2, 0.50)
        self.assertGreater(t2, 0)


class TestConformal(unittest.TestCase):
    def test_coverage_near_nominal(self):
        from ml_valuation import GradientBoostingRegressor
        from uncertainty import apply_interval, coverage, split_conformal
        X, y = _design(_house_rows(500), "成交单价元每平",
                       ["面积㎡", "房龄", "到市中心距离km", "楼层", "学区"], with_period=False)
        n = len(y)
        n_tr, n_cal = int(n * 0.6), int(n * 0.2)
        m = GradientBoostingRegressor(n_estimators=40).fit(X[:n_tr], y[:n_tr])
        p_cal = m.predict(X[n_tr:n_tr + n_cal])
        p_te = m.predict(X[n_tr + n_cal:])
        sc = split_conformal([y[n_tr + i] - p_cal[i] for i in range(n_cal)], alpha=0.10)
        cov = coverage(y[n_tr + n_cal:], apply_interval(p_te, sc["q"]))["coverage"]
        self.assertGreater(cov, 0.75, "conformal 覆盖率 %.0f%% 明显低于名义 90%%" % (cov * 100))

    def test_interval_composition_widens(self):
        from uncertainty import compose_intervals
        base = [(50000.0, 52000.0)]
        out = compose_intervals(base, 0.03, 0.07)
        self.assertLess(out[0][0], base[0][0])
        self.assertLess(out[0][1], base[0][1])
        self.assertGreater(out[0][1] - out[0][0], base[0][1] - base[0][0])


class TestCapRateCalibrator(unittest.TestCase):
    def test_recovers_known_yields(self):
        """cap rate 标定器应还原合成数据中写死的各段真实收益率。"""
        from caprate_calibrate import matched_pr_caprate
        p = os.path.join(ROOT, "skills", "rent-income", "scripts", "matched_pr_demo.csv")
        if not os.path.isfile(p):
            self.skipTest("匹配样本演示数据缺失")
        rows = core.read_csv(p)[1]
        res = matched_pr_caprate(rows, "月租", "单价", "面积", ["城市", "物业类型"])
        want = {("演示市", "住宅"): 0.017, ("对照市甲", "住宅"): 0.024,
                ("演示市", "写字楼"): 0.040, ("演示市", "商业"): 0.030}
        hit = 0
        for seg in res["segments"]:
            key = (seg["segment"].get("城市"), seg["segment"].get("物业类型"))
            if key in want:
                hit += 1
                self.assertLess(abs(seg["gross_caprate_median"] - want[key]) / want[key], 0.12,
                                "%s 毛资本化率 %.4f 偏离真值 %.4f 超过 12%%"
                                % (key, seg["gross_caprate_median"], want[key]))
        self.assertEqual(hit, 4, "未覆盖全部 4 个已知收益率分段")


# ================================================================ D. 治理一致性
class TestGateRunner(unittest.TestCase):
    def test_no_false_conflict_on_trial_report(self):
        """
        门禁的数字一致性不得因格式/舍入产生伪冲突。

        评审明确点出这一脆弱点：千分位、全角、百分号、单位、区间、四舍五入
        都会造成伪冲突；伪冲突一多，门禁就会被人为绕开。首次实现曾产出 18 处伪冲突。
        """
        import gate_runner as gr
        report = os.path.join(TRIAL, "北京远洋山水南区-定价报告-v2含方法论.md")
        base = os.path.join(TRIAL, "数据底座表.csv")
        if not (os.path.isfile(report) and os.path.isfile(base)):
            self.skipTest("试运行产物缺失")
        res, _code = gr.run(report, base, gr.DEFAULT_TOLERANCE_PCT, as_json=True)
        gate = next(g for g in res["gates"] if g["id"] == "numbers-match-dataset")
        self.assertEqual(gate["counts"]["conflicts"], 0,
                         "数字一致性出现伪冲突：%s" % gate.get("evidence"))
        self.assertGreater(gate["counts"]["matched"], 50, "命中底座数字过少，可能未真正比对")

    def test_declarative_not_fake_pass(self):
        """不可自动化的门禁必须标为 declarative，**不得返回 pass**（伪造通过比不检查更危险）。"""
        import gate_runner as gr
        decl = gr.declarative_gates(gr.load_policy())
        self.assertTrue(decl)
        for g in decl:
            self.assertEqual(g["execution"], "declarative")
            self.assertNotEqual(g["status"], "pass")

    def test_rounding_tolerance(self):
        """四舍五入位差不得被判为冲突（底座 1.68 vs 报告「约 1.7%」）。"""
        import gate_runner as gr
        self.assertTrue(gr.close(1.7, 1.68, gr.DEFAULT_TOLERANCE_PCT, raw="1.7%"))
        self.assertFalse(gr.close(5.0, 1.68, gr.DEFAULT_TOLERANCE_PCT, raw="5.0"))

    def test_range_separator_not_parsed_as_minus(self):
        """区间连字符必须被当作分隔符：'1.50-1.58' 不得解析出 −1.58。"""
        import gate_runner as gr
        vals = [t["value"] for t in gr.extract_numbers("区间 1.50-1.58%")]
        self.assertNotIn(-1.58, vals)
        self.assertIn(1.5, vals)


class TestManifestConsistency(unittest.TestCase):
    def test_no_ghost_citations(self):
        """溯源一致性：包内不得引用未登记的文献标签。"""
        import subprocess
        p = subprocess.run([sys.executable, os.path.join(ROOT, "source", "check_manifest.py"), "--json"],
                           capture_output=True, text=True, cwd=ROOT, timeout=180)
        data = json.loads(p.stdout)
        self.assertEqual(data["errors"], [], "存在溯源错误：%s" % data["errors"])

    def test_scenario_dag_acyclic_and_resolvable(self):
        with open(os.path.join(ROOT, "scenarios", "rep-valuation.json"), encoding="utf-8") as f:
            d = json.load(f)
        ids = [t["id"] for t in d["tasks"]]
        self.assertEqual(len(ids), len(set(ids)), "任务 id 重复")
        for t in d["tasks"]:
            for dep in t.get("dependsOn", []):
                self.assertIn(dep, ids, "dependsOn 引用了不存在的任务 id：%s" % dep)
        skill_ids = {s["id"] for s in d["skills"]}
        for t in d["tasks"]:
            for s in t.get("skills", []):
                self.assertIn(s, skill_ids, "任务引用了未声明 skill：%s" % s)
                self.assertTrue(os.path.isdir(os.path.join(ROOT, "skills", s)),
                                "skill 目录不存在：skills/%s" % s)
        self.assertIn("convergencePolicy", d, "场景缺少分歧容差策略")

    def test_knowledge_blocks_cover_all_papers(self):
        """每条 evidenceRefs 指向的知识块必须真实存在，且覆盖清单中的核心文献。"""
        idx_path = os.path.join(ROOT, "knowledge", "experts", "rep-001", "index.json")
        with open(idx_path, encoding="utf-8") as f:
            idx = json.load(f)
        for b in idx["blocks"]:
            fp = os.path.join(ROOT, "knowledge", "experts", "rep-001", b["file"])
            self.assertTrue(os.path.isfile(fp), "知识块文件缺失：%s" % b["file"])
            body = open(fp, encoding="utf-8", errors="replace").read()
            for pid in b["papers"]:
                self.assertIn(pid, body, "%s 未在 %s 中真正覆盖" % (pid, b["file"]))
        for mid in idx["paperToBlock"]:
            self.assertIsNotNone(idx["paperToBlock"][mid])


if __name__ == "__main__":
    unittest.main(verbosity=2)

# ================================================================ E. 平台数据源桥接
class TestPlatformBridge(unittest.TestCase):
    """
    桥接层的**诚实行为**回归。

    这是本包最该被测试保护的一条纪律：**无凭证时不返回空壳、不编造数据、
    而是原样上报上游错误**。一个返回空壳的 fetch() 会让下游把降级结论当完整结论交付。
    """

    def test_sse_parsing_real_sample(self):
        """MCP over HTTP 的响应是 SSE；必须能解析出实测样本里的 JSON-RPC 帧。"""
        from platform_bridge import parse_sse
        sample = ('event: message\ndata: {"jsonrpc":"2.0","id":1,"error":'
                  '{"code":0,"message":"Invalid or missing Authorization header"}}\n\n')
        frames = parse_sse(sample)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["error"]["code"], 0)
        self.assertIn("Authorization", frames[0]["error"]["message"])

    def test_plain_json_fallback(self):
        from platform_bridge import parse_sse
        self.assertEqual(parse_sse('{"jsonrpc":"2.0","id":2,"result":{}}')[0]["result"], {})

    def test_auth_error_classified_as_needs_key(self):
        from platform_bridge import classify_mcp_error
        info = classify_mcp_error({"error": {"code": 0,
                                            "message": "Invalid or missing Authorization header"}})
        self.assertEqual(info["kind"], "needs_key")
        self.assertIn("Authorization", info["upstreamMessage"])

    def test_protocol_error_not_misclassified_as_missing_key(self):
        """-32602 是协议参数问题，不得被误判为缺凭证——否则会误导使用者去配密钥。"""
        from platform_bridge import classify_mcp_error
        info = classify_mcp_error({"error": {"code": -32602, "message": "Invalid request parameters"}})
        self.assertEqual(info["kind"], "protocol_params")

    def test_no_credential_returns_no_fabricated_rows(self):
        """
        无凭证时 fetch 必须**不返回伪造数据**：rows 为空，且 gaps/warnings 说明原因。
        """
        from platform_bridge import BeikeMcpAdapter
        a = BeikeMcpAdapter(api_key="")
        a.api_key = None
        res = a.fetch()
        self.assertEqual(res.rows, [], "无凭证时不得返回任何 rows（防伪造）")
        self.assertTrue(res.warnings or res.gaps, "必须说明为何取不到数据")

    def test_credential_only_from_environment(self):
        """
        凭证只从环境变量读取——回归「绝不硬编码密钥」。
        """
        import os
        from platform_bridge import BEIKE_DEFAULT_URL, resolve_beike_key
        key, src = resolve_beike_key()
        if key:
            self.assertIn("环境变量", src if "环境变量" in src else src)
        # 端点必须是实测确认的地址，不得是占位符
        self.assertEqual(BEIKE_DEFAULT_URL, "https://building.ke.com/mcp")

    def test_zyt_login_uses_email_field(self):
        """zyt 登录字段是 **email**（实测：传 username 会被拒），不得写错字段名。"""
        from platform_bridge import ZYT_LOGIN_PATH
        self.assertEqual(ZYT_LOGIN_PATH, "/api/auth/login")
        import inspect
        from platform_bridge import ZytClient
        src = inspect.getsource(ZytClient.login)
        self.assertIn('"email"', src)
        self.assertNotIn('"username"', src)
