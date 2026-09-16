#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
租金定价与收益法方法全家族 —— 严格按论文设定实现。

对应论文
--------
[R-01] Song, Z., Wilhelmsson, M. & Yang, Z. (2020). Constructing a rental housing index
       and identifying market segmentation in the case of Beijing, China. KTH WP 20/10.
       → hedonic 租金模型 + 聚类识别租赁子市场；数据驱动子市场优于行政边界。
[R-02] (2021). The user cost of housing and the price-rent ratio in Shanghai.
       Regional Science and Urban Economics, 89, 103676.
       → 对**出售**与**出租**分别估 hedonic（半对数），互相插补，构造匹配的价格租金比；
         并用用户成本（user cost）框架检验基本面。
[R-03] Ghysels, E., Plazzi, A. & Valkanov, R. (2007). Valuation in US Commercial Real
       Estate. European Financial Management, 13(3), 472-497.
       → 对数线性化折现租金模型；Cap Rate 预测回报；Cap Rate 分解为本地状态变量、
         租金增长与正交项。
[R-04] (2020). Rent dynamics in France between 1970 and 2013. J. European Real Estate
       Research. → 分层时间虚拟变量 hedonic + Box-Cox 变换构造长期租金指数。
[R-05] Fisher, J.D., Miles, M.E. & Webb, R.B. (1994). An Integrated Approach to the
       Evaluation of Commercial Real Estate. JRER. → hedonic + 收益率集成；
         **Cap Rate 随时间与项目类型显著变化，禁止跨类型套用统一值**。
[R-06] (2022). Can big data increase our knowledge of local rental markets? PLOS ONE.
       → hedonic 租金/价格指数 → 租金价格比；空间异质显著。
[C-03] Wu, J., Gyourko, J. & Deng, Y. (2012/2016). Evaluating conditions in major Chinese
       housing markets. RSUE. → 用户成本法评估中国房价是否偏离基本面。

本模块实现
----------
1. hedonic_rent_model           hedonic 租金模型（半对数 / 双对数）
2. stratified_rent_index        分层时间虚拟变量租金指数（含 Box-Cox）
3. matched_price_rent_ratio     匹配价格与租金的 hedonic 价格租金比（R-02 核心方法）
4. user_cost_model              用户成本法（均衡价格租金比 / 反向检验）
5. cap_rate_valuation           收益法 Cap Rate 定价与敏感性（R-03 / R-05）
6. dcf_valuation                折现现金流 / 折现租金模型（R-03 / R-05）

用法
----
    python3 rent_model.py --data rent_sample.csv --rent 月租(元) --area 面积㎡ --x 户型
    python3 rent_model.py --price 50210 --rent-per-sqm 70.4 --fin-rate 0.0305

仅依赖 Python 标准库；共享数值核心见 <pack>/scripts/core.py。
"""

import argparse
import math
import os
import sys


def _import_core():
    here = os.path.dirname(os.path.abspath(__file__))
    cur = here
    for _ in range(6):
        cand = os.path.join(cur, "scripts")
        if os.path.isfile(os.path.join(cand, "core.py")):
            if cand not in sys.path:
                sys.path.insert(0, cand)
            return
        nxt = os.path.dirname(cur)
        if nxt == cur:
            break
        cur = nxt
    raise ImportError("未找到共享核心 scripts/core.py")


_import_core()
from core import read_csv, to_float, ols, mean, median, quantile, metrics, fmt_table, standard_errors  # noqa: E402


# ================================================================ 1. hedonic 租金模型

class HedonicRentModel:
    """
    hedonic 租金模型（mat-020 Song et al. 2020；mat-019 上海价格租金比 (2021)）。

        ln(R) = Xβ + ε        （半对数，β 经 exp(β)−1 转百分比效应）
        ln(R) = ln(X)β + ε    （双对数，β 为弹性）

    与房价 hedonic 使用**相同属性集**，才能构造可比的价租比（mat-019 的关键要求）。
    """

    def __init__(self, rent_col, x_names, form="log-linear"):
        self.rent_col = rent_col
        self.x_names = list(x_names)
        self.form = form
        self.beta = None
        self.names = ["截距"] + list(x_names)

    def _row(self, r):
        rv = to_float(r.get(self.rent_col))
        xs = [to_float(r.get(n)) for n in self.x_names]
        if rv is None or rv <= 0 or any(v is None for v in xs):
            return None
        y = math.log(rv) if self.form == "log-linear" else rv
        if self.form == "log-log":
            if any(v <= 0 for v in xs):
                return None
            xs = [math.log(v) for v in xs]
        return [1.0] + xs, y

    def fit(self, rows):
        X, y = [], []
        for r in rows:
            got = self._row(r)
            if got:
                X.append(got[0])
                y.append(got[1])
        if len(X) <= len(self.names):
            raise ValueError("样本量 %d 不足以估计 %d 个参数。" % (len(X), len(self.names)))
        self.beta = ols(X, y)
        try:
            self.se = standard_errors(X, [y[i] - sum(X[i][j] * self.beta[j] for j in range(len(self.beta))) for i in range(len(X))])
        except ValueError:
            self.se = None
        self._X, self._y = X, y
        return self

    def coef_table(self):
        out = []
        for i, nm in enumerate(self.names):
            b = self.beta[i]
            if i == 0:
                out.append((nm, b, None, "截距"))
            elif self.form == "log-linear":
                out.append((nm, b, (math.exp(b) - 1) * 100.0, "百分比效应"))
            elif self.form == "log-log":
                out.append((nm, b, b, "弹性"))
            else:
                out.append((nm, b, b, "单位变化量"))
        return out

    def predict(self, rows):
        out = []
        for r in rows:
            got = self._row(r)
            if not got:
                out.append(None)
                continue
            z = sum(got[0][j] * self.beta[j] for j in range(len(self.beta)))
            out.append(math.exp(z) if self.form != "linear" else z)
        return out

    def evaluate(self, rows):
        ys = [to_float(r.get(self.rent_col)) for r in rows]
        keep = [r for r in rows if self._row(r)]
        yv = [v for v in ys if v is not None and v > 0]
        pv = [v for v in self.predict(keep) if v is not None]
        m = min(len(yv), len(pv))
        return metrics(yv[:m], pv[:m], k=len(self.names))


# ================================================================ 2. 分层时间虚拟租金指数

def stratified_rent_index(rows, rent_col, time_col, x_names=None, strata_col=None, boxcox=True):
    """
    分层时间虚拟变量租金指数（France 租金动态 2020）。

        R^(λ) = Xβ + Σ_t δ_t D_t + ε      （λ=0 即半对数 ln R）

    【两处关键修正】
    ① **Jacobian 必须统一**：Box-Cox 极大似然的 Jacobian 为 (λ−1)·Σln(R)。
       若「对数形式」单独走一条不带 Jacobian 的分支，与 λ=0 的 Box-Cox 相比会
       凭空多出 Σln(R)（量级数百），模型选择必然偏向对数形式。故统一用
       λ=0 的 Box-Cox 参数化，不再单列对数分支。
    ② **指数口径随 λ 变化**：λ≠0 时 exp(δ_t) 不再是租金比。改为在**平均属性**
       上比较各期拟合租金（R̂_t / R̂_base），对任意 λ 都成立。

    strata_col 非空时按该列分层分别估计，得到分市场租金指数。
    """
    x_names = x_names or []

    def _fit(sub_rows):
        recs = []
        for r in sub_rows:
            rv = to_float(r.get(rent_col))
            xs = [to_float(r.get(n)) for n in x_names]
            t = r.get(time_col)
            if not t or rv is None or rv <= 0 or any(v is None for v in xs):
                continue
            recs.append((rv, xs, t))
        periods = sorted({rec[2] for rec in recs})
        if len(periods) < 2:
            return None
        base, dummies = periods[0], periods[1:]
        k = 1 + len(x_names) + len(dummies)
        if len(recs) <= k:
            return None
        logsum = sum(math.log(rec[0]) for rec in recs)
        n = len(recs)

        lams = [0.0] if not boxcox else [round(-1.0 + 0.1 * i, 2) for i in range(0, 31)]
        best = None
        for lam in lams:
            X, y = [], []
            for rv, xs, t in recs:
                yv = math.log(rv) if abs(lam) < 1e-8 else (rv ** lam - 1.0) / lam
                X.append([1.0] + xs + [1.0 if t == d else 0.0 for d in dummies])
                y.append(yv)
            try:
                b = ols(X, y)
            except ValueError:
                continue
            resid = [y[i] - sum(X[i][j] * b[j] for j in range(len(b))) for i in range(n)]
            sse = sum(e * e for e in resid)
            if sse <= 0:
                continue
            ll = -0.5 * n * math.log(sse / n) + (lam - 1.0) * logsum
            if best is None or ll > best[0]:
                best = (ll, lam, b)
        if best is None:
            return None
        ll, lam, b = best

        # λ=0（半对数）再单独拟合一次：
        # 指数构造惯例使用半对数——此时 δ_t 有干净的乘性解释（index=exp(δ)）；
        # Box-Cox 的 λ 仅用于**检验函数形式**。若直接用选中 λ 造指数，
        # 一旦 λ≠0（函数形式被数据推向非对数），指数会系统性偏离真值。
        X0, y0 = [], []
        for rv, xs, t in recs:
            X0.append([1.0] + xs + [1.0 if t == d else 0.0 for d in dummies])
            y0.append(math.log(rv))
        b_log = ols(X0, y0)

        def _index_from(beta, lam_use):
            mx = [mean([rec[1][j] for rec in recs]) for j in range(len(x_names))]
            raw = {}
            for per in periods:
                row = [1.0] + mx + [1.0 if per == d else 0.0 for d in dummies]
                z = sum(row[j] * beta[j] for j in range(len(beta)))
                raw[per] = math.exp(z) if abs(lam_use) < 1e-8 else max(z * lam_use + 1.0, 1e-12) ** (1.0 / lam_use)
            b0 = raw[base]
            return {p: raw[p] / b0 * 100.0 for p in periods}

        idx = _index_from(b_log, 0.0)          # 主口径：半对数
        idx_sel = _index_from(b, lam)          # 诊断口径：选中 λ
        return {"periods": periods, "base": base, "index": idx, "index_selected": idx_sel,
                "lambda": lam, "loglik": ll, "n": n, "beta": b_log, "beta_selected": b,
                "x_names": x_names}

    if strata_col:
        groups = sorted({r.get(strata_col) for r in rows if r.get(strata_col)})
        return {g: _fit([r for r in rows if r.get(strata_col) == g]) for g in groups}
    return _fit(rows)


# ================================================================ 3. 匹配价格租金比

def matched_price_rent_ratio(sale_rows, rent_rows, x_names,
                            sale_price_col="成交价", rent_col="月租(元)",
                            sale_time_col=None, rent_time_col=None):
    """
    匹配 hedonic 价格租金比（mat-019 的核心方法）。

    论文要点：价租比必须在**同一属性集**上估计两套 hedonic（价格方程与租金方程），
    再互相插补得到每套物业的匹配价格与租金，才能计算可比的价格租金比。
    直接「均价 ÷ 均租」会因样本结构差异产生系统性偏差。

    返回：per-unit 匹配价租比 + 汇总统计。
    """
    sale = [r for r in sale_rows if to_float(r.get(sale_price_col)) and
            all(to_float(r.get(n)) is not None for n in x_names)]
    rent = [r for r in rent_rows if to_float(r.get(rent_col)) and
            all(to_float(r.get(n)) is not None for n in x_names)]
    if len(sale) <= len(x_names) + 1 or len(rent) <= len(x_names) + 1:
        raise ValueError("价格或租金样本不足以估计 hedonic（需 > 属性数+1）。")

    pm = HedonicRentModel(sale_price_col, x_names, "log-linear").fit(sale)
    rm = HedonicRentModel(rent_col, x_names, "log-linear").fit(rent)

    # 对每套出租物业：由价格方程插补价格 → 与其租金构成匹配价租比
    ratios = []
    for r in rent:
        rv = to_float(r.get(rent_col))
        p_hat = pm.predict([r])[0]
        if rv and p_hat:
            ratios.append({"物业": r.get("物业编号", ""), "面积": r.get(x_names[0], ""),
                           "实际月租": rv, "插补价格": p_hat,
                           "价格租金比(年)": p_hat / (rv * 12.0)})
    vals = [x["价格租金比(年)"] for x in ratios]
    naive = (mean([to_float(r.get(sale_price_col)) for r in sale]) /
             (mean([to_float(r.get(rent_col)) for r in rent]) * 12.0))

    return {"ratios": ratios, "matched_median": median(vals), "matched_mean": mean(vals),
            "n_matched": len(ratios), "naive_ratio": naive,
            "bias_pct": (naive / median(vals) - 1.0) * 100.0 if vals else None,
            "price_model": pm, "rent_model": rm}


# ================================================================ 4. 用户成本法

def user_cost_model(price, annual_rent, mortgage_rate, maintenance=0.01, property_tax=0.004,
                    tx_cost_amort=0.005, expected_growth=0.0, tax_deduct=0.0):
    """
    用户成本法（mat-025 Himmelberg et al. 2005；mat-023 Wu, Gyourko & Deng；mat-019 上海 (2021)）。

        单期用户成本（占房价比）：
            UC = (1 − t)·r + δ + m + τ − g
            r = 房贷利率, t = 利息税前扣除率, δ = 维护折旧率,
            m = 房产税率, τ = 年化交易成本, g = 预期资本增值率

        无套利均衡： 年租金 / 房价 = UC   ⟺   价格租金比* = 1 / UC
        若实际价租比 > 1/UC → 价格相对基本面偏贵（隐含更高增值预期）。

    返回：UC、均衡价租比、实际价租比、偏离度。
    """
    uc = (1 - tax_deduct) * mortgage_rate + maintenance + property_tax + tx_cost_amort - expected_growth
    eq_ratio = (1.0 / uc) if uc > 0 else float("inf")
    actual = price / annual_rent if annual_rent > 0 else float("inf")
    return {"user_cost_UC": uc, "equilibrium_ratio": eq_ratio, "actual_ratio": actual,
            "deviation_pct": (actual / eq_ratio - 1.0) * 100.0 if eq_ratio not in (0, float("inf")) else None,
            "implied_required_growth": (1 - tax_deduct) * mortgage_rate + maintenance
                                       + property_tax + tx_cost_amort - annual_rent / price,
            "rent_yield_gross": annual_rent / price if price > 0 else None,
            "params": {"r": mortgage_rate, "delta": maintenance, "m": property_tax,
                       "tau": tx_cost_amort, "g": expected_growth, "t": tax_deduct}}


# ================================================================ 5. Cap Rate 定价

def cap_rate_valuation(noi, cap_rate, cap_grid=None, city=None, property_type=None):
    """
    收益法 Cap Rate 定价（Ghysels et al. 2007；Fisher et al. 1994）。

        价值 = NOI / CapRate

    Fisher et al. (1994) 的核心告诫：Cap Rate 随**时间**与**物业类型**显著变化，
    禁止跨类型/跨地点套用统一值——故本函数强制要求声明 city 与 property_type。
    """
    if not city or not property_type:
        raise ValueError("必须声明 city 与 property_type：Cap Rate 不可跨类型/跨地点套用"
                         "（Fisher, Miles & Webb 1994）。")
    if cap_rate <= 0:
        raise ValueError("Cap Rate 必须为正。")
    value = noi / cap_rate
    grid = cap_grid or [cap_rate + d for d in (-0.01, -0.005, -0.0025, 0.0, 0.0025, 0.005, 0.01)]
    sens = [(cr, noi / cr) for cr in grid if cr > 0]
    return {"noi": noi, "cap_rate": cap_rate, "value": value, "sensitivity": sens,
            "city": city, "property_type": property_type}


def dcf_valuation(noi0, years=10, rent_growth=0.02, discount_rate=0.08,
                  vacancy=0.05, opex_ratio=0.25, exit_cap_rate=0.05):
    """
    折现现金流 / 折现租金模型（Ghysels et al. 2007；Fisher et al. 1994）。

        V = Σ_{t=1..T} NOI_t / (1+r)^t + 期末价值 / (1+r)^T
        NOI_t = NOI_0·(1+g)^t·(1−vacancy)·(1−opex_ratio)
        期末价值 = NOI_{T+1} / exit_cap_rate
    """
    if discount_rate <= exit_cap_rate:
        raise ValueError("折现率须高于退出 Cap Rate，否则终值发散（Gordon 增长模型不收敛）。")
    pv, flows = 0.0, []
    for t in range(1, years + 1):
        noi_t = noi0 * ((1 + rent_growth) ** t) * (1 - vacancy) * (1 - opex_ratio)
        df = 1.0 / ((1 + discount_rate) ** t)
        pv += noi_t * df
        flows.append((t, noi_t, df, noi_t * df))
    noi_next = noi0 * ((1 + rent_growth) ** (years + 1)) * (1 - vacancy) * (1 - opex_ratio)
    tv = noi_next / exit_cap_rate
    tv_pv = tv / ((1 + discount_rate) ** years)
    return {"value": pv + tv_pv, "pv_explicit": pv, "terminal_value": tv,
            "pv_terminal": tv_pv, "terminal_share": tv_pv / (pv + tv_pv) * 100.0,
            "flows": flows, "params": {"g": rent_growth, "r": discount_rate,
            "vacancy": vacancy, "opex_ratio": opex_ratio, "exit_cap": exit_cap_rate}}


# ================================================================ CLI

def main():
    ap = argparse.ArgumentParser(description="租金定价与收益法方法全家族")
    ap.add_argument("--data", help="租金样本 CSV")
    ap.add_argument("--rent", default="月租(元)", help="租金列名")
    ap.add_argument("--x", nargs="*", default=[], help="租金 hedonic 属性列（如 面积㎡）")
    ap.add_argument("--time", help="时间列（构造租金指数）")
    ap.add_argument("--strata", help="分层列（分市场租金指数）")
    ap.add_argument("--price", type=float, help="售价（元/㎡）")
    ap.add_argument("--rent-per-sqm", type=float, help="单位租金（元/㎡/月）")
    ap.add_argument("--fin-rate", type=float, help="融资成本（年化，如 0.0305）")
    ap.add_argument("--noi", type=float, help="年净营运收入（元）")
    ap.add_argument("--cap-rate", type=float, help="资本化率（如 0.045）")
    ap.add_argument("--city", help="城市（Cap Rate 单独标定要求）")
    ap.add_argument("--ptype", help="物业类型（Cap Rate 单独标定要求）")
    args = ap.parse_args()

    if args.data:
        _, rows = read_csv(args.data)
        print("数据：%s | 样本 %d" % (args.data, len(rows)))
        if args.x:
            m = HedonicRentModel(args.rent, args.x).fit(rows)
            print("\n=== hedonic 租金模型（Song et al. 2020；Shanghai 2021）===")
            tbl = []
            for i, (nm, b, eff, kind) in enumerate(m.coef_table()):
                tbl.append([nm, "%.4f" % b, ("—" if eff is None else "%+.2f%%" % eff), kind])
            print(fmt_table(["变量", "系数", "效应", "类型"], tbl, ["<", ">", ">", "<"]))
            ev = m.evaluate(rows)
            print("  n=%d  MAE=%.0f  MAPE=%.2f%%  R²=%.3f" % (ev["n"], ev["MAE"], ev["MAPE"], ev["R2"]))
        if args.time:
            r = stratified_rent_index(rows, args.rent, args.time, args.x, args.strata)
            print("\n=== 分层时间虚拟变量租金指数（mat-020 Song et al. 2020；分市场租金指数）===")
            if isinstance(r, dict) and r and "index" in next(iter(r.values()), {}):
                for g, sub in r.items():
                    if not sub:
                        continue
                    print("  [%s] λ=%s  n=%d" % (g, sub["lambda"], sub["n"]))
                    print("    " + "  ".join("%s:%.2f" % (p, sub["index"][p]) for p in sub["periods"]))
            elif r:
                print("  n=%d  Box-Cox 选中 λ=%s（函数形式诊断）" % (r["n"], r["lambda"]))
                print("  指数（半对数口径，主）：" + "  ".join("%s:%.2f" % (p, r["index"][p]) for p in r["periods"]))
                if "index_selected" in r:
                    print("  指数（选中 λ 口径，仅对照）：" + "  ".join(
                        "%s:%.2f" % (p, r["index_selected"][p]) for p in r["periods"]))
                if r["lambda"] is not None and abs(r["lambda"]) > 0.05:
                    print("  ⚠ λ 明显偏离 0：说明函数形式可能非对数；两个口径指数若分化，"
                          "须说明以半对数为主口径的理由（mat-020 亦以分层时间虚拟为主）")
            else:
                print("  （时间期数不足，跳过）")

    if args.price and args.rent_per_sqm:
        annual = args.rent_per_sqm * 12.0
        print("\n=== 收益法基线 ===")
        print("  售价 %.0f 元/㎡ | 单位租金 %.1f 元/㎡/月 | 年租金 %.1f 元/㎡" % (args.price, args.rent_per_sqm, annual))
        print("  毛租金收益率 = %.2f%%   价格租金比 = %.1f 年" % (annual / args.price * 100, args.price / annual))
        uc = user_cost_model(args.price, annual, args.fin_rate or 0.0305)
        print("\n=== 用户成本法（mat-025 Himmelberg et al. 2005；mat-023；mat-019 上海 2021）===")
        p = uc["params"]
        print("  UC = (1−%.2f)×%.2f%% + %.1f%% + %.1f%% + %.1f%% − %.1f%% = %.2f%%"
              % (p["t"], p["r"] * 100, p["delta"] * 100, p["m"] * 100, p["tau"] * 100, p["g"] * 100, uc["user_cost_UC"] * 100))
        print("  均衡价格租金比* = 1/UC = %.1f 年" % uc["equilibrium_ratio"])
        print("  实际价格租金比   = %.1f 年" % uc["actual_ratio"])
        print("  偏离 = %+.1f%%  →  %s" % (uc["deviation_pct"],
              "价格高于基本面（隐含更高增值预期）" if uc["deviation_pct"] > 0 else "价格低于基本面"))
        print("  使实际价租比成立所需的最小年增值率 g* = %.2f%%" % (uc["implied_required_growth"] * 100))

    if args.noi and args.cap_rate:
        try:
            v = cap_rate_valuation(args.noi, args.cap_rate, city=args.city, property_type=args.ptype)
            print("\n=== 收益法 Cap Rate 定价（Ghysels 2007；Fisher 1994）===")
            print("  [%s · %s] NOI=%.0f  CapRate=%.2f%%  →  价值=%.0f 元"
                  % (v["city"], v["property_type"], v["noi"], v["cap_rate"] * 100, v["value"]))
            print("  敏感性：")
            print(fmt_table(["CapRate", "价值(元)"], [["%.2f%%" % (c * 100), "%.0f" % val] for c, val in v["sensitivity"]], ["<", ">"]))
        except ValueError as e:
            print("\n⚠ %s" % e)
        d = dcf_valuation(args.noi)
        print("\n=== 折现现金流 / 折现租金模型（Ghysels 2007）===")
        print("  参数：g=%.1f%%  r=%.1f%%  空置=%.0f%%  运营成本比=%.0f%%  退出CapRate=%.1f%%"
              % (d["params"]["g"] * 100, d["params"]["r"] * 100, d["params"]["vacancy"] * 100,
                 d["params"]["opex_ratio"] * 100, d["params"]["exit_cap"] * 100))
        print("  显式期现值=%.0f  终值现值=%.0f  合计估值=%.0f 元" % (d["pv_explicit"], d["pv_terminal"], d["value"]))
        print("  终值占比 = %.1f%%（占比过高说明估值高度依赖退出假设）" % d["terminal_share"])


if __name__ == "__main__":
    main()
