#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
价格指数方法全家族 —— 严格按论文设定实现。

对应论文
--------
[I-01] Bailey, M.J., Muth, R.F. & Nourse, H.O. (1963). A Regression Method for Real
       Estate Price Index Construction. JASA, 58(304), 933-942.
       → 重复销售法（BMN）：r = Σ_j X_j b_j + u，X_j = -1 (t) / +1 (t') / 0。
[I-02] Case, K.E. & Shiller, R.J. (1989). The Efficiency of the Market for Single-Family
       Homes. American Economic Review, 79(1), 125-137.
       → 三阶段加权最小二乘（WLS）：OLS → 残差平方对持有间隔回归 → 加权回归。
       方差结构 Var = 2σ_u² + (t'−t)σ_v²（Wharton 教材式 (4)）。
[I-03] Case, B. & Quigley, J.M. (1991). The Dynamics of Real Estate Prices.
       Review of Economics and Statistics, 73(1), 50-58；Quigley (1995) 混合模型。
       → 混合法：水平方程（hedonic）+ 差分方程（重复销售）堆叠估计。
[I-04] Case, B., Pollakowski, H.O. & Wachter, S.M. (1991). On Choosing Among House
       Price Index Methodologies. Real Estate Economics, 19(3), 286-307.
       → 无单一最优方法；须按数据条件选择并说明理由。
[I-07] Calainho, F.D., van de Minne, A.M. & Francke, M.K. (2024). A Machine Learning
       Approach to Price Indices: Applications in Commercial Real Estate. JREFE, 68, 624-653.
       → 模型无关指数：以个体「时外预测误差」度量价格变化。

本模块实现
----------
1. bmn_index            Bailey-Muth-Nourse 重复销售指数
2. case_shiller_index   Case-Shiller 三阶段加权重复销售指数
3. HybridIndex          Case-Quigley 混合指数（水平 + 差分堆叠）
4. ml_price_index       Calainho 机器学习（时外预测误差）指数

用法
----
    python3 price_index.py --pairs sample_pairs.csv --method cs
    python3 price_index.py --pairs sample_pairs.csv --method all

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
from core import read_csv, to_float, ols, mean, metrics, fmt_table  # noqa: E402


# ================================================================ 通用：期间与设计矩阵

def _periods(pairs):
    ps = set()
    for r in pairs:
        ps.add(r["t1"])
        ps.add(r["t2"])
    return sorted(ps)


def _diff_design(pairs, periods, base):
    """差分方程设计矩阵：X_j = +1 (t') / -1 (t)，剔除基期列。"""
    cols = [p for p in periods if p != base]
    X, y = [], []
    for r in pairs:
        row = [0.0] * len(cols)
        if r["t2"] in cols:
            row[cols.index(r["t2"])] += 1.0
        if r["t1"] in cols:
            row[cols.index(r["t1"])] -= 1.0
        X.append(row)
        y.append(math.log(r["p2"] / r["p1"]))
    return X, y, cols


def load_pairs(path, t1="首次交易期", t2="再次交易期", p1="首次成交价", p2="再次成交价"):
    _, rows = read_csv(path)
    pairs = []
    for r in rows:
        a, b = r.get(t1), r.get(t2)
        x, y = to_float(r.get(p1)), to_float(r.get(p2))
        if not a or not b or x is None or y is None or x <= 0 or y <= 0:
            continue
        pairs.append({"t1": a, "t2": b, "p1": x, "p2": y, "id": r.get("物业编号", "")})
    if not pairs:
        raise ValueError("未解析到有效的重复成交对（检查列名）。")
    return pairs


# ================================================================ 1. BMN 重复销售指数

def bmn_index(pairs, base=None):
    """
    Bailey-Muth-Nourse (1963) 重复销售指数。

        r_i = ln(P_it') − ln(P_it) = Σ_j X_j^i b_j + u_i
        b̂ = (X'X)^{-1} X'r ，基期系数约束为 0

    返回 dict：periods / beta / index（基期=100）/ n
    """
    periods = _periods(pairs)
    if len(periods) < 2:
        raise ValueError("期数 < 2，无法构造指数。")
    base = base or periods[0]
    X, y, cols = _diff_design(pairs, periods, base)
    if len(X) <= len(cols):
        raise ValueError("重复成交对 %d ≤ 期数 %d，自由度不足。" % (len(X), len(cols)))
    beta = ols(X, y)
    idx = {base: 100.0}
    for j, p in enumerate(cols):
        idx[p] = math.exp(beta[j]) * 100.0
    resid = [y[i] - sum(X[i][j] * beta[j] for j in range(len(beta))) for i in range(len(X))]
    return {"periods": periods, "base": base, "beta": dict(zip(cols, beta)),
            "index": {p: idx[p] for p in periods}, "n": len(X),
            "resid": resid, "X": X, "y": y, "cols": cols}


# ================================================================ 2. Case-Shiller 三阶段

def case_shiller_index(pairs, base=None, use_sq=True, max_iter=1):
    """
    Case & Shiller (1989) 三阶段加权最小二乘重复销售指数。

      阶段 1：对 r = Xb + u 做 OLS，取残差 û
      阶段 2：û² = A(t'−t) + B(t'−t)² + C + η（use_sq=False 时去掉平方项）
              → 拟合方差 v̂；C 项即 2σ_u²，A（或含 B）刻画 σ_v²
      阶段 3：以 1/√v̂ 加权做 WLS（权重尺度为「标准差」，与 Wharton 教材一致）

    返回 dict，含 index / sigma 结构 / 各阶段诊断。
    """
    periods = _periods(pairs)
    base = base or periods[0]
    X, y, cols = _diff_design(pairs, periods, base)
    n = len(X)
    if n <= len(cols):
        raise ValueError("重复成交对 %d ≤ 期数 %d，自由度不足。" % (n, len(cols)))

    b1 = ols(X, y)
    r1 = [y[i] - sum(X[i][j] * b1[j] for j in range(len(b1))) for i in range(n)]

    # 阶段 2：方差方程  û² = C + A·gap (+ B·gap²)
    gaps = []
    for r in pairs:
        gaps.append(abs(float(r["t2"]) - float(r["t1"])) if _isnum(r["t2"]) and _isnum(r["t1"]) else 1.0)
    # 【关键】按间隔取值个数自适应选阶：k 个不同取值最多容纳 k 个参数，
    # k<3 时加入 gap² 会造成完全共线（g² = −2·1 + 3·g 对 g∈{1,2} 恒成立），
    # 必须自动降阶，否则 X'X 奇异、方差结构退化。
    distinct = sorted(set(gaps))
    nk = len(distinct)
    if nk >= 3 and use_sq:
        spec = "quadratic"
    elif nk >= 2:
        spec = "linear"
    else:
        spec = "constant"
    if spec == "quadratic":
        Z = [[1.0, g, g * g] for g in gaps]
    elif spec == "linear":
        Z = [[1.0, g] for g in gaps]
    else:
        Z = [[1.0] for g in gaps]
    self_spec = spec
    yv = [e * e for e in r1]
    try:
        bv = ols(Z, yv)
    except ValueError:
        bv = [mean(yv)] + [0.0] * (len(Z[0]) - 1)
    var_fit = [max(sum(Z[i][j] * bv[j] for j in range(len(bv))), 1e-9) for i in range(n)]
    sd_fit = [math.sqrt(v) for v in var_fit]

    # 阶段 3：以标准差为尺度的 WLS
    w = [1.0 / (s * s) for s in sd_fit]
    Xw = [[X[i][j] / sd_fit[i] for j in range(len(cols))] for i in range(n)]
    yw = [y[i] / sd_fit[i] for i in range(n)]
    b3 = ols(Xw, yw)

    # 可选迭代（Case-Shiller 实践中常用一次；iter>1 时重复阶段 2-3）
    for _ in range(max(0, max_iter - 1)):
        r3 = [y[i] - sum(X[i][j] * b3[j] for j in range(len(b3))) for i in range(n)]
        yv = [e * e for e in r3]
        try:
            bv = ols(Z, yv)
        except ValueError:
            break
        var_fit = [max(sum(Z[i][j] * bv[j] for j in range(len(bv))), 1e-9) for i in range(n)]
        sd_fit = [math.sqrt(v) for v in var_fit]
        Xw = [[X[i][j] / sd_fit[i] for j in range(len(cols))] for i in range(n)]
        yw = [y[i] / sd_fit[i] for i in range(n)]
        b3 = ols(Xw, yw)

    idx = {base: 100.0}
    for j, p in enumerate(cols):
        idx[p] = math.exp(b3[j]) * 100.0

    # 方差结构解读：Var = C + A·gap (+ B·gap²)   ←→   2σ_u² + (t'−t)σ_v²
    C = bv[0]
    A = bv[1] if spec in ("linear", "quadratic") else 0.0
    B = bv[2] if spec == "quadratic" else None
    sigma_u2, sigma_v2 = C / 2.0, A
    return {"periods": periods, "base": base, "index": {p: idx[p] for p in periods},
            "beta": dict(zip(cols, b3)), "n": n, "method": "Case-Shiller 3-stage WLS",
            "stage1_beta": dict(zip(cols, b1)), "var_spec": self_spec,
            "var_coef": {"C(2σu²)": C, "A(σv²)": A, "B": B},
            "sigma_u": math.sqrt(max(sigma_u2, 0.0)), "sigma_v": math.sqrt(max(sigma_v2, 0.0)),
            "mean_sd": mean(sd_fit)}


def _isnum(s):
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


# ================================================================ 3. 混合指数

class HybridIndex:
    """
    Case & Quigley (1991) / Quigley (1995) 混合指数。

    将两类方程堆叠估计：
      水平方程（全部成交）：  ln(P_it) = X_it β + Σ_t γ_t D_it + ε
      差分方程（重复成交对）：Δln(P_i)  = Σ_t γ_t ΔD_it + η
    属性价格 β 由水平方程识别；时间效应 γ 由两类方程共同识别（差分方程天然控制属性）。

    返回 index（基期=100）与 β。
    """

    def __init__(self, price_col, time_col, x_names, base=None):
        self.price_col = price_col
        self.time_col = time_col
        self.x_names = list(x_names)
        self.base = base

    def fit(self, level_rows, pairs):
        levels = sorted({r.get(self.time_col) for r in level_rows if r.get(self.time_col)})
        for p in pairs:
            levels.append(p["t1"])
            levels.append(p["t2"])
        periods = sorted(set(levels))
        base = self.base or periods[0]
        cols = [p for p in periods if p != base]
        self.periods, self.base, self.cols = periods, base, cols

        nx = len(self.x_names)
        self.names = ["截距"] + list(self.x_names) + ["D_" + str(c) for c in cols]

        X, y = [], []
        # 水平方程
        for r in level_rows:
            p = to_float(r.get(self.price_col))
            if p is None or p <= 0:
                continue
            xs = [to_float(r.get(n)) for n in self.x_names]
            if any(v is None for v in xs):
                continue
            d = [1.0 if r.get(self.time_col) == c else 0.0 for c in cols]
            X.append([1.0] + xs + d)
            y.append(math.log(p))
        n_level = len(X)
        # 差分方程（属性项置 0，时间项为差值）
        for pr in pairs:
            d = [0.0] * len(cols)
            if pr["t2"] in cols:
                d[cols.index(pr["t2"])] += 1.0
            if pr["t1"] in cols:
                d[cols.index(pr["t1"])] -= 1.0
            X.append([0.0] * (1 + nx) + d)
            y.append(math.log(pr["p2"] / pr["p1"]))

        if len(X) <= len(self.names):
            raise ValueError("混合模型样本不足：%d ≤ 参数 %d。" % (len(X), len(self.names)))
        self.beta = ols(X, y)
        self.n_level, self.n_pair = n_level, len(pairs)
        return self

    def index(self):
        idx = {self.base: 100.0}
        off = 1 + len(self.x_names)
        for j, c in enumerate(self.cols):
            idx[c] = math.exp(self.beta[off + j]) * 100.0
        return {p: idx[p] for p in self.periods}

    def attribute_prices(self):
        return {n: (math.exp(self.beta[1 + i]) - 1.0) * 100.0 for i, n in enumerate(self.x_names)}


# ================================================================ 4. ML 时外误差指数

def ml_price_index(rows, time_col, price_col, x_cols, learner=None, base=None):
    """
    Calainho, van de Minne & Francke (2024) 模型无关价格指数。

    步骤：对每个期 t，用 t 之前的全部成交训练模型 → 预测第 t 期的成交 →
          期效应 = 实际/预测 的几何均值 → 链式得到指数。

    learner: 需实现 fit(X, y) / predict(X)，且 X 为数值列表、y 为 ln(价格)。
             缺省用 OLS（即 hedonic 时外预测）。
    """
    data = []
    for r in rows:
        t = r.get(time_col)
        p = to_float(r.get(price_col))
        xs = [to_float(r.get(c)) for c in x_cols]
        if not t or p is None or p <= 0 or any(v is None for v in xs):
            continue
        data.append({"t": t, "lnp": math.log(p), "x": xs})
    if not data:
        raise ValueError("无有效样本。")
    periods = sorted({d["t"] for d in data})
    if len(periods) < 2:
        raise ValueError("期数 < 2。")
    base = base or periods[0]

    if learner is None:
        def learner(Xtr, ytr):
            b = ols(Xtr, ytr)
            return lambda Xte: [sum(row[j] * b[j] for j in range(len(b))) for row in Xte]

    effects, detail = {}, []
    first = [d for d in data if d["t"] == base]
    effects[base] = 1.0
    detail.append((base, len(first), None, None))
    for i in range(1, len(periods)):
        t = periods[i]
        tr = [d for d in data if d["t"] < t]
        te = [d for d in data if d["t"] == t]
        if len(tr) <= len(x_cols) + 1 or not te:
            effects[t] = effects[periods[i - 1]]
            detail.append((t, len(te), None, "样本不足，沿用上期"))
            continue
        Xtr = [[1.0] + d["x"] for d in tr]
        ytr = [d["lnp"] for d in tr]
        predict = learner(Xtr, ytr)
        Xte = [[1.0] + d["x"] for d in te]
        pred = predict(Xte)
        ratios = [te[k]["lnp"] - pred[k] for k in range(len(te))]
        effects[t] = math.exp(mean(ratios))
        detail.append((t, len(te), effects[t] - 1.0, ""))

    # 链式累乘
    idx, acc = {}, 1.0
    for p in periods:
        acc *= effects[p]
        idx[p] = acc * 100.0
    return {"periods": periods, "base": base, "index": idx, "n": len(data), "detail": detail}


# ================================================================ CLI

def main():
    ap = argparse.ArgumentParser(description="价格指数方法全家族（BMN / Case-Shiller / 混合 / ML）")
    ap.add_argument("--pairs", required=True, help="重复成交对 CSV")
    ap.add_argument("--method", default="all", choices=["bmn", "cs", "all"])
    ap.add_argument("--base", help="基期（默认首期）")
    ap.add_argument("--t1", default="首次交易期")
    ap.add_argument("--t2", default="再次交易期")
    ap.add_argument("--p1", default="首次成交价")
    ap.add_argument("--p2", default="再次成交价")
    args = ap.parse_args()

    pairs = load_pairs(args.pairs, args.t1, args.t2, args.p1, args.p2)
    print("重复成交对：%d 对" % len(pairs))

    if args.method in ("bmn", "all"):
        r = bmn_index(pairs, base=args.base)
        print("\n=== BMN 重复销售指数（Bailey-Muth-Nourse 1963）===")
        print(fmt_table(["期", "指数(基期=100)"], [[p, "%.2f" % r["index"][p]] for p in r["periods"]], ["<", ">"]))
        ch = {r["periods"][i]: (r["index"][r["periods"][i]] / r["index"][r["periods"][i - 1]] - 1) * 100
              for i in range(1, len(r["periods"]))}
        print("  环比(%)：" + "  ".join("%s:%+.2f" % (k, v) for k, v in ch.items()))

    if args.method in ("cs", "all"):
        r = case_shiller_index(pairs, base=args.base)
        print("\n=== Case-Shiller 三阶段加权指数（1989）===")
        print(fmt_table(["期", "指数(基期=100)"], [[p, "%.2f" % r["index"][p]] for p in r["periods"]], ["<", ">"]))
        print("  方差结构：C(2σu²)=%.4f  A(σv²)=%.4f  B=%s"
              % (r["var_coef"]["C(2σu²)"], r["var_coef"]["A(σv²)"],
                 ("%.4f" % r["var_coef"]["B"]) if r["var_coef"]["B"] is not None else "未纳入"))
        print("  方差方程设定：%s（按持有间隔取值个数自适应降阶，避免完全共线）" % r["var_spec"])
        print("  σ_u=%.4f  σ_v=%.4f  （σ_u：white noise；σ_v：随机游走单步波动）"
              % (r["sigma_u"], r["sigma_v"]))
        if r["var_coef"]["C(2σu²)"] < 0:
            print("  ⚠ 诊断：二阶拟合截距为负（无约束 OLS 的常见结果），σ_u 已置 0。")
            print("    说明：残差平方对间隔的回归本身噪声大（残差平方服从 χ²(1) 量级），")
            print("    小样本下 σ_v 的估计不精确；如需精确方差结构须扩大样本或改用约束/拟似然估计。")
        print("  说明：加权后长持有间隔样本对指数影响被削弱（Case & Shiller 1989）")

    print("\n注：重复销售法依赖重复成交样本，存在样本选择偏差（Case & Shiller 1989；"
          "Pollakowski & Wachter 1997）；须同时披露样本覆盖率。")


if __name__ == "__main__":
    main()
