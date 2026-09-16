#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
特征价格（Hedonic）方法全家族 —— 严格按论文设定实现。

对应论文
--------
[H-01] Rosen, S. (1974). Hedonic Prices and Implicit Markets: Product Differentiation
       in Pure Competition. Journal of Political Economy, 82(1), 34-55.
       → 价格 = 属性隐含价格之和；边际隐含价格 ∂P/∂x。
[H-02] Lancaster, K. (1966). A New Approach to Consumer Theory. JPE, 74(2), 132-157.
       → 消费者从属性中获取效用，为 hedonic 提供理论基础。
[H-05] Malpezzi, S. Hedonic Pricing Models: A Selective and Applied Review. CULER WP 02-05.
       → 函数形式（线性/半对数/双对数/Box-Cox）与常见偏误清单。
[I-05] Chen, T. & Harding, J.P. (2016). Changing Tastes: Estimating Changing Attribute
       Prices in Hedonic and Repeat Sales Models. JREFE, 52, 141-175.
       → 属性价格随时间漂移；时间×属性交互与低阶多项式。
[S-02] Bitter, C., Mulligan, G.F. & Dall'erba, S. (2007). Journal of Geographical Systems, 9(1).
       → 空间扩展法（spatial expansion）：属性 × 空间多项式交互。

本模块实现
----------
1. HedonicModel         基础特征价格模型（线性 / 半对数 / 双对数，OLS 或 WLS）
2. BoxCoxHedonic        Box-Cox 变换 + λ 网格极大似然/最小 SSE 估计（Malpezzi 综述）
3. TimeDummyHedonic     时间虚拟变量价格指数（质量调整后的纯价格变动）
4. AttributeTimeVarying 属性价格时变（Chen & Harding）：时间×属性交互
5. SpatialExpansion     空间扩展法（Bitter 2007），作 GWR 的对照基准

用法
----
    python3 hedonic_model.py --data sample_data.csv --y "挂牌单价(元/㎡)" \
        --x "面积㎡" "南向类(南北/西南/南=1)" "高楼层(高层=1)" --log-y --cv

仅依赖 Python 标准库；共享数值核心见 <pack>/scripts/core.py。
"""

import argparse
import math
import os
import sys


# ---------- 定位共享核心（向上查找 scripts/core.py）----------
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
from core import (  # noqa: E402
    read_csv, to_float, mean, median, ols, standard_errors, vif,
    metrics, standardize, kfold_indices, fmt_table,
)

EPS = 1e-12


# ================================================================ 1. 基础 Hedonic

class HedonicModel:
    """
    特征价格模型（Rosen 1974）。

    form
    ----
      "linear"      : P = Xβ + ε
      "log-linear"  : ln(P) = Xβ + ε         （半对数，房地产实证最常用）
      "log-log"     : ln(P) = ln(X)β + ε     （系数即弹性）

    y_name / x_names 为列名；x_names 中的分类变量须预先编码为 0/1 虚拟变量。
    """

    FORMS = ("linear", "log-linear", "log-log")

    def __init__(self, y_name, x_names, form="log-linear"):
        if form not in self.FORMS:
            raise ValueError("form 必须是 %s 之一" % (self.FORMS,))
        self.y_name = y_name
        self.x_names = list(x_names)
        self.form = form
        self.beta = None
        self.names = ["截距"] + list(x_names)
        self.nobs = 0
        self.resid = None

    # ---- 内部：设计与变换 ----
    def _row_xy(self, r):
        ys = to_float(r.get(self.y_name))
        xs = [to_float(r.get(n)) for n in self.x_names]
        if ys is None or any(v is None for v in xs):
            return None
        if self.form in ("log-linear", "log-log") and ys <= 0:
            return None
        y = ys if self.form == "linear" else math.log(ys)
        if self.form == "log-log":
            if any(v <= 0 for v in xs):
                return None
            xs = [math.log(v) for v in xs]
        return [1.0] + xs, y

    def prepare(self, rows):
        X, y = [], []
        for r in rows:
            got = self._row_xy(r)
            if got is None:
                continue
            X.append(got[0])
            y.append(got[1])
        return X, y

    def fit(self, rows):
        X, y = self.prepare(rows)
        if len(X) <= len(self.names):
            raise ValueError(
                "样本量 %d 不足以估计 %d 个参数（自由度 ≤ 0）。按 Skill 规则应降级为比较法或扩大样本。"
                % (len(X), len(self.names)))
        self.beta = ols(X, y)
        self.nobs = len(X)
        self._X, self._y = X, y
        self._rows = rows
        self.resid = [y[i] - sum(X[i][j] * self.beta[j] for j in range(len(self.beta))) for i in range(len(X))]
        return self

    def predict(self, rows):
        if self.beta is None:
            raise RuntimeError("请先调用 fit()")
        out = []
        for r in rows:
            got = self._row_xy(r)
            if got is None:
                out.append(None)
                continue
            z = sum(got[0][j] * self.beta[j] for j in range(len(self.beta)))
            out.append(z if self.form == "linear" else math.exp(z))
        return out

    def coef_table(self, se=None):
        """系数表：log 形式换算为百分比效应，log-log 形式标注为弹性。"""
        if self.beta is None:
            raise RuntimeError("请先调用 fit()")
        out = []
        for i, name in enumerate(self.names):
            b = self.beta[i]
            s = None if not se else se[i]
            if i == 0:
                out.append((name, b, None, "截距", s))
            elif self.form == "log-linear":
                out.append((name, b, (math.exp(b) - 1.0) * 100.0, "百分比效应", s))
            elif self.form == "log-log":
                out.append((name, b, b, "弹性", s))
            else:
                out.append((name, b, b, "单位变化量", s))
        return out

    def coef_se(self):
        """参数标准误（OLS 正态近似）。"""
        X, _ = self.prepare(self._rows) if getattr(self, "_rows", None) else (None, None)
        if X is None:
            return None
        try:
            return standard_errors(X, self.resid)
        except ValueError:
            return None

    def evaluate(self, rows):
        """返回拟合评价（原始尺度）。"""
        pairs = []
        for r in rows:
            got = self._row_xy(r)
            if got is None:
                continue
            ys = to_float(r.get(self.y_name))
            z = sum(got[0][j] * self.beta[j] for j in range(len(self.beta)))
            pred = z if self.form == "linear" else math.exp(z)
            pairs.append((ys, pred))
        y = [p[0] for p in pairs]
        p = [p[1] for p in pairs]
        return metrics(y, p, k=len(self.names))

    def vif(self, rows):
        X, _ = self.prepare(rows)
        return dict(zip(self.x_names, vif(X)))

    def kfold_cv(self, rows, k=5):
        """K 折交叉验证（样本外 MAE / MAPE / RMSE），确定性划分便于复现。"""
        X, y = self.prepare(rows)
        n = len(X)
        if n <= k:
            return None
        folds = kfold_indices(n, k)
        yt, yp = [], []
        for f in folds:
            te = set(f)
            trX = [X[i] for i in range(n) if i not in te]
            trY = [y[i] for i in range(n) if i not in te]
            if len(trX) <= len(self.names):
                continue
            try:
                b = ols(trX, trY)
            except ValueError:
                continue
            for i in sorted(te):
                z = sum(X[i][j] * b[j] for j in range(len(b)))
                pred = z if self.form == "linear" else math.exp(z)
                yt.append(y[i] if self.form == "linear" else math.exp(y[i]))
                yp.append(pred)
        if not yt:
            return None
        return metrics(yt, yp)

    def loo_cv(self, rows):
        """留一交叉验证（样本外）。"""
        X, y = self.prepare(rows)
        n = len(X)
        yt, yp = [], []
        for i in range(n):
            trX = [X[j] for j in range(n) if j != i]
            trY = [y[j] for j in range(n) if j != i]
            if len(trX) <= len(self.names):
                continue
            try:
                b = ols(trX, trY)
            except ValueError:
                continue
            z = sum(X[i][j] * b[j] for j in range(len(b)))
            pred = z if self.form == "linear" else math.exp(z)
            yt.append(y[i] if self.form == "linear" else math.exp(y[i]))
            yp.append(pred)
        if not yt:
            return None
        return metrics(yt, yp)

    def implicit_prices(self):
        """兼容旧接口：返回 (名称, 系数, 百分比效应)。"""
        return [(n, b, e) for n, b, e, _k, _s in self.coef_table()]


# ================================================================ 2. Box-Cox Hedonic

class BoxCoxHedonic:
    """
    Box-Cox 变换特征价格模型（Malpezzi 综述；France 租金动态 2020 亦用此法）。

        P^(λ) = (P^λ − 1)/λ ,  λ≠0 ；  P^(0) = ln P

    λ 通过网格搜索最小化残差平方和（等价于正态似然下的 Profile MLE）。
    λ=1 → 线性；λ=0 → 半对数。返回最优 λ 与对应系数。
    """

    def __init__(self, y_name, x_names, lambdas=None):
        self.y_name = y_name
        self.x_names = list(x_names)
        self.lambdas = lambdas or [round(-1.0 + 0.05 * i, 2) for i in range(0, 61)]  # -1.0 ~ 2.0
        self.best_lambda = None
        self.beta = None
        self.sse = None
        self.trace = []

    @staticmethod
    def transform(p, lam):
        if abs(lam) < 1e-8:
            return math.log(p)
        return (p ** lam - 1.0) / lam

    def _data(self, rows):
        X, y, raw = [], [], []
        for r in rows:
            ys = to_float(r.get(self.y_name))
            xs = [to_float(r.get(n)) for n in self.x_names]
            if ys is None or ys <= 0 or any(v is None for v in xs):
                continue
            X.append([1.0] + xs)
            y.append(ys)
        return X, y

    def fit(self, rows):
        X, y = self._data(rows)
        if len(X) <= len(self.x_names) + 1:
            raise ValueError("样本量不足（%d ≤ 参数个数 %d）。" % (len(X), len(self.x_names) + 1))
        best = None
        for lam in self.lambdas:
            ty = [self.transform(v, lam) for v in y]
            try:
                b = ols(X, ty)
            except ValueError:
                continue
            resid = [ty[i] - sum(X[i][j] * b[j] for j in range(len(b))) for i in range(len(ty))]
            sse = sum(e * e for e in resid)
            # Box-Cox 似然含 Jacobian: 减去 (λ-1)Σln(y)
            loglik = -0.5 * len(y) * math.log(sse / len(y)) + (lam - 1.0) * sum(math.log(v) for v in y)
            self.trace.append((lam, sse, loglik))
            if best is None or loglik > best[2]:
                best = (lam, b, loglik, sse)
        if best is None:
            raise ValueError("Box-Cox 搜索失败：所有 λ 均不可估计。")
        self.best_lambda, self.beta, self._loglik, self.sse = best[0], best[1], best[2], best[3]
        self._X, self._y = X, y
        return self

    def evaluate(self, rows=None):
        X, y = (self._X, self._y) if rows is None else self._data(rows)
        lam = self.best_lambda
        ty = [self.transform(v, lam) for v in y]
        pred_t = [sum(X[i][j] * self.beta[j] for j in range(len(self.beta))) for i in range(len(X))]
        if abs(lam) < 1e-8:
            pred = [math.exp(v) for v in pred_t]
        else:
            pred = [max((v * lam + 1.0), EPS) ** (1.0 / lam) for v in pred_t]
        return metrics(y, pred, k=len(self.beta))


# ================================================================ 3. 时间虚拟变量指数

class TimeDummyHedonic:
    """
    时间虚拟变量法（stratified time dummy）价格指数（Malpezzi；France 租金 2020）。

        ln(P) = Xβ + Σ_t δ_t D_t + ε

    δ_t 即质量调整后的价格指数（相对基期）。支持分层（按子市场分别估计再合并）。
    """

    def __init__(self, price_col, time_col, x_names, base=None):
        self.price_col = price_col
        self.time_col = time_col
        self.x_names = list(x_names)
        self.base = base
        self.beta = None
        self.x_names_full = None

    def fit(self, rows):
        periods = sorted({r.get(self.time_col) for r in rows if r.get(self.time_col)})
        if len(periods) < 2:
            raise ValueError("时间期数 < 2，无法构造指数。")
        base = self.base or periods[0]
        if base not in periods:
            raise ValueError("基期 %s 不在样本期内。" % base)
        dummies = [p for p in periods if p != base]
        self.periods, self.base, self.dummies = periods, base, dummies
        names = list(self.x_names) + ["D_" + str(d) for d in dummies]
        self.x_names_full = names
        X, y = [], []
        for r in rows:
            p = to_float(r.get(self.price_col))
            if p is None or p <= 0:
                continue
            xs = [to_float(r.get(n)) for n in self.x_names]
            if any(v is None for v in xs):
                continue
            row = [1.0] + xs + [1.0 if r.get(self.time_col) == d else 0.0 for d in dummies]
            X.append(row)
            y.append(math.log(p))
        if len(X) <= len(names) + 1:
            raise ValueError("样本量不足：%d ≤ 参数 %d。" % (len(X), len(names) + 1))
        self.beta = ols(X, y)
        self.names = ["截距"] + names
        return self

    def index(self):
        """返回 {期: 指数}（基期=100）。"""
        idx = {self.base: 100.0}
        for j, d in enumerate(self.dummies):
            b = self.beta[1 + len(self.x_names) + j]
            idx[d] = math.exp(b) * 100.0
        return {p: idx[p] for p in self.periods}

    def chain(self):
        """环比变动（%）。"""
        idx = self.index()
        out = {}
        for i, p in enumerate(self.periods):
            out[p] = None if i == 0 else (idx[p] / idx[self.periods[i - 1]] - 1.0) * 100.0
        return out


# ================================================================ 4. 属性价格时变

class AttributeTimeVarying:
    """
    属性价格时变模型（Chen & Harding 2016）。

        ln(P) = Xβ + Σ_t δ_t D_t + Σ_k Σ_t γ_{kt} (x_k × D_t) + ε

    放松「属性价格恒定」假设，估计各属性隐含价格随时间的漂移。
    为避免维数爆炸，支持只对指定属性（vary_names）加入时间交互。
    """

    def __init__(self, price_col, time_col, x_names, vary_names=None, base=None):
        self.price_col = price_col
        self.time_col = time_col
        self.x_names = list(x_names)
        self.vary_names = list(vary_names) if vary_names else list(x_names)
        self.base = base
        self.beta = None

    def fit(self, rows):
        periods = sorted({r.get(self.time_col) for r in rows if r.get(self.time_col)})
        base = self.base or periods[0]
        dummies = [p for p in periods if p != base]
        self.periods, self.base, self.dummies = periods, base, dummies
        vary_idx = [self.x_names.index(n) for n in self.vary_names]

        names = list(self.x_names) + ["D_" + str(d) for d in dummies]
        for n in self.vary_names:
            for d in dummies:
                names.append("%s×%s" % (n, d))
        self.names = ["截距"] + names

        X, y = [], []
        for r in rows:
            p = to_float(r.get(self.price_col))
            if p is None or p <= 0:
                continue
            xs = [to_float(r.get(n)) for n in self.x_names]
            if any(v is None for v in xs):
                continue
            ds = [1.0 if r.get(self.time_col) == d else 0.0 for d in dummies]
            inter = []
            for vi in vary_idx:
                for dv in ds:
                    inter.append(xs[vi] * dv)
            X.append([1.0] + xs + ds + inter)
            y.append(math.log(p))
        if len(X) <= len(self.names):
            raise ValueError("样本量不足：%d ≤ 参数 %d。" % (len(X), len(self.names)))
        self.beta = ols(X, y)
        return self

    def attribute_path(self, name):
        """返回某属性隐含价格的时变路径 {期: 百分比效应}。"""
        if name not in self.vary_names:
            return None
        base_i = 1 + self.x_names.index(name)
        b0 = self.beta[base_i]
        out = {self.base: (math.exp(b0) - 1.0) * 100.0}
        off = 1 + len(self.x_names) + len(self.dummies)
        order = [(n, d) for n in self.vary_names for d in self.dummies]
        for j, (n, d) in enumerate(order):
            if n == name:
                b = b0 + self.beta[off + j]
                out[d] = (math.exp(b) - 1.0) * 100.0
        return {p: out[p] for p in self.periods}


# ================================================================ 5. 空间扩展法

class SpatialExpansion:
    """
    空间扩展法（Bitter, Mulligan & Dall'erba 2007 的对照方法）。

        ln(P) = Xβ + Σ_k (x_k × f(coord)) γ_k + ε

    以坐标多项式作为属性价格的平滑空间趋势，与 GWR 并列比较（Bitter 2007 发现 GWR 优于本法）。
    coord 列名由 coord_names 指定（如 ["x", "y"]），degree=1 时加入 x, y, x², y², x·y。
    """

    def __init__(self, price_col, x_names, coord_names, degree=1):
        self.price_col = price_col
        self.x_names = list(x_names)
        self.coord_names = list(coord_names)
        self.degree = degree
        self.beta = None

    def _poly(self, x, y):
        terms = [x, y]
        if self.degree >= 2:
            terms += [x * x, y * y, x * y]
        if self.degree >= 3:
            terms += [x ** 3, y ** 3, x * x * y, x * y * y]
        return terms

    def fit(self, rows):
        # 坐标标准化，改善条件数
        cs = [to_float(r.get(c)) for r in rows for c in [self.coord_names[0]]]
        X, y, names = [], [], None
        n = len(rows)
        c1 = [to_float(r.get(self.coord_names[0])) for r in rows]
        c2 = [to_float(r.get(self.coord_names[1])) for r in rows]
        z1, m1, s1 = standardize([v for v in c1 if v is not None] or [0.0])
        z2, m2, s2 = standardize([v for v in c2 if v is not None] or [0.0])
        z1i, z2i = 0, 0
        for i, r in enumerate(rows):
            p = to_float(r.get(self.price_col))
            xs = [to_float(r.get(nm)) for nm in self.x_names]
            if p is None or p <= 0 or any(v is None for v in xs) or c1[i] is None or c2[i] is None:
                continue
            zx = (c1[i] - m1) / s1
            zy = (c2[i] - m2) / s2
            poly = self._poly(zx, zy)
            inter = [xs[k] * t for k in range(len(xs)) for t in poly]
            if names is None:
                names = (["截距"] + list(self.x_names)
                         + ["%s·f%d" % (nm, j + 1) for nm in self.x_names for j in range(len(poly))])
                self.names = names
            X.append([1.0] + xs + inter)
            y.append(math.log(p))
        if not X or len(X) <= len(self.names):
            raise ValueError("样本不足或坐标缺失，无法估计空间扩展模型。")
        self.beta = ols(X, y)
        self._X, self._y = X, y
        return self


# ================================================================ 评价与对照

def naive_metrics(rows, y_name):
    """朴素均价法基准（供价值实测对照）。"""
    y = [to_float(r.get(y_name)) for r in rows]
    y = [v for v in y if v is not None and v > 0]
    if not y:
        return None
    m = mean(y)
    p = [m] * len(y)
    return {"n": len(y), "mean": m, "MAE": metrics(y, p)["MAE"], "MAPE": metrics(y, p)["MAPE"]}


def fit_metrics(model, rows):
    """兼容旧接口：返回全样本评价。"""
    if hasattr(model, "evaluate"):
        return model.evaluate(rows)
    raise TypeError("模型不支持 evaluate()")


# ================================================================ CLI

def main():
    ap = argparse.ArgumentParser(description="特征价格（Hedonic）方法全家族 —— 零依赖实现")
    ap.add_argument("--data", required=True, help="CSV 路径")
    ap.add_argument("--y", required=True, help="因变量列名（价格或单价，正数）")
    ap.add_argument("--x", required=True, nargs="+", help="自变量列名（分类请预编码 0/1）")
    ap.add_argument("--form", default="log-linear", choices=HedonicModel.FORMS,
                    help="函数形式（默认 log-linear 半对数）")
    ap.add_argument("--boxcox", action="store_true", help="额外做 Box-Cox 变换并搜索最优 λ")
    ap.add_argument("--cv", action="store_true", help="附加 K 折交叉验证")
    ap.add_argument("--k", type=int, default=5, help="K 折数（默认 5）")
    ap.add_argument("--predict", nargs="+", help="按 --x 顺序传入属性值以估值")
    args = ap.parse_args()

    if not os.path.exists(args.data):
        sys.exit("文件不存在：%s" % args.data)
    _, rows = read_csv(args.data)
    print("数据：%s" % args.data)
    print("样本：%d 条" % len(rows))

    model = HedonicModel(args.y, args.x, form=args.form).fit(rows)

    print("\n=== 估计系数（%s）===" % args.form)
    se = model.coef_se()
    tbl = []
    for i, (name, b, eff, kind, _s) in enumerate(model.coef_table(se=se)):
        tbl.append([name, "%.4f" % b,
                    ("%.4f" % se[i]) if se else "—",
                    ("—" if eff is None else "%+.2f%%" % eff), kind])
    print(fmt_table(["变量", "系数", "标准误", "效应", "类型"], tbl, ["<", ">", ">", ">", "<"]))

    m = model.evaluate(rows)
    print("\n=== 拟合效果（全样本）===")
    print("  n=%d  MAE=%.0f  MAPE=%.2f%%  RMSE=%.0f  R²=%.3f  调整R²=%.3f"
          % (m["n"], m["MAE"], m["MAPE"], m["RMSE"], m["R2"], m.get("AdjR2", float("nan"))))

    nm = naive_metrics(rows, args.y)
    if nm:
        print("\n=== 对照：朴素均价法 ===")
        print("  样本均值=%.0f  MAE=%.0f  MAPE=%.2f%%" % (nm["mean"], nm["MAE"], nm["MAPE"]))
        if m["MAE"] > 0:
            print("  模型相对均价法 MAE 变化：%+.1f%%" % ((m["MAE"] - nm["MAE"]) / nm["MAE"] * 100))

    v = model.vif(rows)
    hi = {k: val for k, val in v.items() if val > 10}
    print("\n=== 多重共线性 ===")
    print("  " + ("VIF 全部 < 10" if not hi else "高共线变量（VIF>10）：%s" % hi))

    if args.boxcox:
        bc = BoxCoxHedonic(args.y, args.x).fit(rows)
        bm = bc.evaluate()
        print("\n=== Box-Cox 变换 ===")
        print("  最优 λ = %.2f  （λ=0 ↔ 半对数，λ=1 ↔ 线性）" % bc.best_lambda)
        print("  n=%d  MAE=%.0f  MAPE=%.2f%%  R²=%.3f" % (bm["n"], bm["MAE"], bm["MAPE"], bm["R2"]))

    if args.cv:
        n = len(model.prepare(rows)[0])
        folds = kfold_indices(n, args.k)
        X, y = model.prepare(rows)
        errs = []
        for f in folds:
            te = set(f)
            trX = [X[i] for i in range(n) if i not in te]
            trY = [y[i] for i in range(n) if i not in te]
            teX = [X[i] for i in sorted(te)]
            teY = [y[i] for i in sorted(te)]
            if len(trX) <= len(model.names):
                continue
            try:
                b = ols(trX, trY)
            except ValueError:
                continue
            for i, row in enumerate(teX):
                z = sum(row[j] * b[j] for j in range(len(b)))
                pred = z if model.form == "linear" else math.exp(z)
                errs.append((teY[i] if model.form == "linear" else math.exp(teY[i]), pred))
        if errs:
            yy = [e[0] for e in errs]
            pp = [e[1] for e in errs]
            cm = metrics(yy, pp)
            print("\n=== %d 折交叉验证（样本外）===" % args.k)
            print("  n=%d  MAE=%.0f  MAPE=%.2f%%  RMSE=%.0f" % (cm["n"], cm["MAE"], cm["MAPE"], cm["RMSE"]))

    if args.predict:
        if len(args.predict) != len(args.x):
            sys.exit("--predict 需按 --x 顺序提供 %d 个值" % len(args.x))
        row = {nm: v for nm, v in zip(args.x, args.predict)}
        val = model.predict([row])[0]
        print("\n=== 预测 ===")
        for nm in args.x:
            print("  %-28s %s" % (nm, row[nm]))
        print("  → 估计值：%.0f" % val)

    print("\n注：小样本（自由度低）时系数方向稳健但绝对水平不具统计显著性；缺失属性一律标「待补」。")


if __name__ == "__main__":
    main()
