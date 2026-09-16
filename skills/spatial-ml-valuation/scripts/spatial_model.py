#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
空间计量方法全家族 —— 严格按论文设定实现。

对应论文
--------
[S-01] Brunsdon, C., Fotheringham, A.S. & Charlton, M. (1996). Geographically Weighted
       Regression: A Method for Exploring Spatial Nonstationarity. Geographical Analysis,
       28(4), 281-298.
       → GWR：β(u_i,v_i) = (X'W_iX)^{-1}X'W_i y，W_i 为距离核权重；带宽由 CV/AICc 选择。
[S-02] Bitter, C., Mulligan, G.F. & Dall'erba, S. (2007). Journal of Geographical Systems,
       9(1), 7-27. → GWR 在解释力与预测精度上优于空间扩展法。
[S-03] Pace, R.K. & LeSage, J.P. (2004). Spatial Statistics and Real Estate. JREFE, 29(2),
       147-166. → 空间权重矩阵、空间滞后/误差模型；忽略空间依赖会导致系数偏误与推断失效。
[S-04] Helbich, M. et al. (2014). Spatial Heterogeneity in Hedonic House Price Models.
       Urban Studies, 51(2), 390-411. → 属性隐含价格显著空间异质。
[S-05] (2019). Modelling Housing Rents Using SAR-GWR. ISPRS IJGI, 8(6), 346.
       → 同时处理空间自相关与空间异质性。
[I-06] Oust, A., Hansen, S.N. & Pettrem, T.R. (2019). JREFE.
       → 回归克里金（regression kriging）：空间增强 hedonic 之一。

本模块实现
----------
1. 空间权重：distance_weights / knn_weights / row_standardize
2. morans_i                 Moran's I 空间自相关检验（含置换检验）
3. sar_mle                 空间滞后模型  y = ρWy + Xβ + ε（集中似然 MLE）
4. sem_mle                 空间误差模型  y = Xβ + u, u = λWu + ε（集中似然 MLE）
5. gwr                      地理加权回归（距离核局部 WLS + 带宽 CV）
6. regression_kriging       回归克里金（趋势 + 残差普通克里金）
7. band_grid_search         带宽的 CV / AICc 网格搜索

用法
----
    python3 spatial_model.py --data sample_spatial.csv --y 单价 --x 面积 楼龄 --lat y --lon x --method all

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
from core import (  # noqa: E402
    read_csv, to_float, ols, mean, metrics, inverse, matvec, fmt_table, standardize, norm_cdf,
)

LOG2PI = math.log(2.0 * math.pi)


# ================================================================ 线性代数补充

def det(A):
    """LU 消元求行列式（带部分主元）。"""
    n = len(A)
    M = [list(row) for row in A]
    d = 1.0
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-14:
            return 0.0
        if p != c:
            M[c], M[p] = M[p], M[c]
            d = -d
        d *= M[c][c]
        for r in range(c + 1, n):
            f = M[r][c] / M[c][c]
            for cc in range(c, n):
                M[r][cc] -= f * M[c][cc]
    return d


# ================================================================ 距离与空间权重

def euclid(p, q):
    return math.sqrt(sum((p[i] - q[i]) ** 2 for i in range(len(p))))


def haversine(lon1, lat1, lon2, lat2):
    """经纬度球面距离（km）。"""
    R = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))


def distance_matrix(coords, metric="euclid"):
    n = len(coords)
    D = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if metric == "haversine":
                d = haversine(coords[i][0], coords[i][1], coords[j][0], coords[j][1])
            else:
                d = euclid(coords[i], coords[j])
            D[i][j] = D[j][i] = d
    return D


def distance_weights(D, threshold=None, kernel="inverse", power=1.0):
    """距离型空间权重：W_ij = 1/d^power（可选阈值截断）。对角线为 0。"""
    n = len(D)
    W = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            d = D[i][j]
            if threshold is not None and d > threshold:
                continue
            if d <= 1e-12:
                W[i][j] = 0.0
            elif kernel == "inverse":
                W[i][j] = 1.0 / (d ** power)
            elif kernel == "gaussian":
                W[i][j] = math.exp(-0.5 * (d ** 2))
            else:
                W[i][j] = 1.0
    return W


def knn_weights(D, k=6):
    """K 近邻权重（等权，或可按距离倒数）。"""
    n = len(D)
    W = [[0.0] * n for _ in range(n)]
    for i in range(n):
        order = sorted([j for j in range(n) if j != i], key=lambda j: D[i][j])[:k]
        for j in order:
            W[i][j] = 1.0
    return W


def row_standardize(W):
    """行标准化；孤立点（行和为 0）保持 0，避免除零。"""
    out = []
    for row in W:
        s = sum(row)
        out.append([v / s for v in row] if s > 1e-12 else list(row))
    return out


def wy(W, y):
    return [sum(W[i][j] * y[j] for j in range(len(y))) for i in range(len(W))]


# ================================================================ Moran's I

def morans_i(W, y, permutations=199, seed=7):
    """
    Moran's I（全局空间自相关）。
        I = (n / S0) · (ΣΣ w_ij z_i z_j) / (Σ z_i²),  z = y − ȳ
    置换检验给出伪 p 值（双侧）。
    """
    n = len(y)
    m = mean(y)
    z = [v - m for v in y]
    S0 = sum(sum(row) for row in W)
    if S0 <= 0:
        raise ValueError("空间权重全为 0（检查阈值或距离定义）。")
    num = sum(W[i][j] * z[i] * z[j] for i in range(n) for j in range(n))
    den = sum(v * v for v in z)
    I = (n / S0) * (num / den) if den > 0 else float("nan")

    E_I = -1.0 / (n - 1)
    # 置换分布
    state = seed
    cnt_ge = cnt_le = 0
    zz = list(z)
    for _ in range(permutations):
        for i in range(n - 1, 0, -1):
            state = (1103515245 * state + 12345) % (2 ** 31)
            j = state % (i + 1)
            zz[i], zz[j] = zz[j], zz[i]
        num_p = sum(W[i][j] * zz[i] * zz[j] for i in range(n) for j in range(n))
        Ip = (n / S0) * (num_p / den)
        if Ip >= I:
            cnt_ge += 1
        if Ip <= I:
            cnt_le += 1
    p = 2.0 * min(cnt_ge, cnt_le) / permutations
    p = min(1.0, max(p, 1.0 / permutations))
    return {"I": I, "E_I": E_I, "p_value": p, "n": n, "S0": S0}


# ================================================================ 空间滞后 / 误差

def _profile_rho(W, X, y, lo=-0.999, hi=0.999, steps=400):
    """对 ρ（或 λ）做一维集中似然网格搜索，返回最优参数与统计量。"""
    n = len(y)
    k = len(X[0])
    I = [[1.0 if a == b else 0.0 for b in range(n)] for a in range(n)]
    best = None
    for s in range(steps + 1):
        rho = lo + (hi - lo) * s / steps
        # A = I − ρW
        A = [[I[a][b] - rho * W[a][b] for b in range(n)] for a in range(n)]
        dt = det(A)
        if dt <= 0:
            continue
        Ay = matvec(A, y)
        # 【关键】SAR 集中似然：y − ρWy = Xβ + ε，故把 (I−ρW)y 对「原始 X」回归
        try:
            b = ols(X, Ay)
        except ValueError:
            continue
        e = [Ay[i] - sum(X[i][j] * b[j] for j in range(k)) for i in range(n)]
        s2 = sum(v * v for v in e) / n
        if s2 <= 1e-14:
            continue
        loglik = -0.5 * n * (LOG2PI + math.log(s2)) + math.log(dt)
        if best is None or loglik > best["loglik"]:
            best = {"rho": rho, "beta": b, "sigma2": s2, "loglik": loglik, "det": dt}
    if best is None:
        raise ValueError("集中似然搜索失败：请检查空间权重矩阵。")
    return best


def sar_mle(W, X, y, grid=None):
    """
    空间滞后模型（SAR / spatial lag）：
        y = ρWy + Xβ + ε,  ε ~ N(0, σ²I)
    集中对数似然：lnL(ρ) = −n/2·(ln2π + ln σ²(ρ)) + ln|I − ρW|
    """
    n = len(y)
    Ws = row_standardize(W)
    grid = grid or max(60, min(400, int(40000 / max(n, 1))))
    best = _profile_rho(Ws, X, y, steps=grid)
    rho, b, s2 = best["rho"], best["beta"], best["sigma2"]
    resid = [y[i] - rho * sum(Ws[i][j] * y[j] for j in range(n))
             - sum(X[i][j] * b[j] for j in range(len(b))) for i in range(n)]
    pred = [y[i] - resid[i] for i in range(n)]
    return {"model": "SAR", "rho": rho, "beta": b, "sigma2": s2,
            "loglik": best["loglik"], "n": n, "metrics": metrics(y, pred),
            "resid": resid}


def sem_mle(W, X, y, grid=None):
    """
    空间误差模型（SEM / spatial error）：
        y = Xβ + u,  u = λWu + ε,  ε ~ N(0, σ²I)
    等价于对 (I−λW)y 与 (I−λW)X 做 OLS；似然含 ln|I − λW|。
    """
    n = len(y)
    Ws = row_standardize(W)
    grid = grid or max(60, min(400, int(40000 / max(n, 1))))
    I = [[1.0 if a == b else 0.0 for b in range(n)] for a in range(n)]
    k = len(X[0])
    best = None
    for s in range(grid + 1):
        lam = -0.999 + 1.998 * s / grid
        A = [[I[a][b] - lam * Ws[a][b] for b in range(n)] for a in range(n)]
        dt = det(A)
        if dt <= 0:
            continue
        Ay = matvec(A, y)
        # (I−λW)X ：必须对 X 的每一「列」施加变换（对行做运算属于维度错配）
        _cols = [matvec(A, [X[i][j] for i in range(n)]) for j in range(k)]
        AX = [[_cols[j][i] for j in range(k)] for i in range(n)]
        try:
            b = ols(AX, Ay)
        except ValueError:
            continue
        e = [Ay[i] - sum(AX[i][j] * b[j] for j in range(k)) for i in range(n)]
        s2 = sum(v * v for v in e) / n
        if s2 <= 1e-14:
            continue
        loglik = -0.5 * n * (LOG2PI + math.log(s2)) + math.log(dt)
        if best is None or loglik > best["loglik"]:
            best = {"lam": lam, "beta": b, "sigma2": s2, "loglik": loglik, "A": A}
    if best is None:
        raise ValueError("集中似然搜索失败：请检查空间权重矩阵。")
    b = best["beta"]
    pred = [sum(X[i][j] * b[j] for j in range(k)) for i in range(n)]
    return {"model": "SEM", "lambda": best["lam"], "beta": b, "sigma2": best["sigma2"],
            "loglik": best["loglik"], "n": n, "metrics": metrics(y, pred)}


# ================================================================ GWR

def _kernel_w(d, bw, kernel):
    if kernel == "gaussian":
        return math.exp(-0.5 * (d / bw) ** 2)
    if kernel == "bisquare":
        return (1.0 - (d / bw) ** 2) ** 2 if d < bw else 0.0
    if kernel == "exponential":
        return math.exp(-d / bw)
    raise ValueError("未知核函数：%s" % kernel)


class GWR:
    """
    地理加权回归（Brunsdon, Fotheringham & Charlton 1996）。

        β(u_i, v_i) = (X' W_i X)^{-1} X' W_i y
        W_i = diag(k_1i, …, k_ni)，k 为距离核（Gaussian / bisquare / exponential）

    带宽 bw 可用 `select_bandwidth`（CV 或 AICc）自动选择。
    """

    def __init__(self, x_names, y_name, coord_names, kernel="gaussian", bw=None, metric="euclid"):
        self.x_names = list(x_names)
        self.y_name = y_name
        self.coord_names = list(coord_names)
        self.kernel = kernel
        self.bw = bw
        self.metric = metric

    def _data(self, rows):
        X, y, C = [], [], []
        for r in rows:
            ys = to_float(r.get(self.y_name))
            xs = [to_float(r.get(n)) for n in self.x_names]
            c = [to_float(r.get(n)) for n in self.coord_names]
            if ys is None or any(v is None for v in xs) or any(v is None for v in c):
                continue
            X.append([1.0] + xs)
            y.append(ys)
            C.append(c)
        if not X:
            raise ValueError("无有效样本（检查列名与缺失值）。")
        self.names = ["截距"] + list(self.x_names)
        return X, y, C

    def fit(self, rows):
        X, y, C = self._data(rows)
        n, k = len(X), len(X[0])
        D = distance_matrix(C, self.metric)
        if self.bw is None:
            self.bw = self.select_bandwidth(X, y, C)["bw"]
        betas, preds = [], []
        for i in range(n):
            w = [_kernel_w(D[i][j], self.bw, self.kernel) for j in range(n)]
            if sum(w) <= 1e-12:
                w = [1.0 if j == i else 0.0 for j in range(n)]
            Xw = [[X[j][a] * w[j] for a in range(k)] for j in range(n)]
            XtWX = [[sum(X[j][a] * w[j] * X[j][bb] for j in range(n)) for bb in range(k)] for a in range(k)]
            XtWy = [sum(X[j][a] * w[j] * y[j] for j in range(n)) for a in range(k)]
            try:
                bi = matvec(inverse(XtWX), XtWy)
            except ValueError:
                bi = [float("nan")] * k
            betas.append(bi)
            preds.append(sum(X[i][a] * bi[a] for a in range(k)))
        self.X, self.y, self.C, self.D = X, y, C, D
        self.betas, self.preds = betas, preds
        return self

    def select_bandwidth(self, X, y, C, kind="cv", grid=None):
        """带宽网格搜索：CV 最小化预测误差；AICc 最小化信息准则。"""
        D = distance_matrix(C, self.metric)
        n = len(y)
        if grid is None:
            dm = sorted(D[i][j] for i in range(n) for j in range(n) if i != j)
            lo = dm[max(0, int(0.05 * len(dm)))]
            hi = dm[min(len(dm) - 1, int(0.9 * len(dm)))]
            grid = [lo + (hi - lo) * s / 40.0 for s in range(41)]
        best = None
        k = len(X[0])
        for bw in grid:
            if bw <= 1e-9:
                continue
            if kind == "cv":
                sse = 0.0
                for i in range(n):
                    w = [_kernel_w(D[i][j], bw, self.kernel) for j in range(n)]
                    w[i] = 0.0
                    if sum(w) <= 1e-12:
                        continue
                    Xw = [[X[j][a] * w[j] for a in range(k)] for j in range(n)]
                    XtWX = [[sum(X[j][a] * w[j] * X[j][bb] for j in range(n)) for bb in range(k)] for a in range(k)]
                    XtWy = [sum(X[j][a] * w[j] * y[j] for j in range(n)) for a in range(k)]
                    try:
                        bi = matvec(inverse(XtWX), XtWy)
                    except ValueError:
                        continue
                    sse += (y[i] - sum(X[i][a] * bi[a] for a in range(k))) ** 2
                score = sse / n
            else:  # AICc
                g = GWR(self.x_names, self.y_name, self.coord_names, self.kernel, bw, self.metric)
                g.bw = bw
                g.fit_rows(X, y, D)
                rss = sum((y[i] - g.preds[i]) ** 2 for i in range(n))
                aicc = n * math.log(rss / n) + n * math.log(2 * math.pi) + n * (n + g.tr_S) / (n - 2 - g.tr_S) if rss > 0 else float("inf")
                score = aicc
            if best is None or score < best[1]:
                best = (bw, score)
        return {"bw": best[0], "score": best[1], "kind": kind, "grid_n": len(grid)}

    def fit_rows(self, X, y, D):
        """已知 D 的快速拟合（供带宽搜索内部使用）。"""
        n, k = len(X), len(X[0])
        self.names = ["截距"] + list(self.x_names)
        betas, preds, S = [], [], 0.0
        for i in range(n):
            w = [_kernel_w(D[i][j], self.bw, self.kernel) for j in range(n)]
            if sum(w) <= 1e-12:
                w = [1.0 if j == i else 0.0 for j in range(n)]
            Xw = [[X[j][a] * w[j] for a in range(k)] for j in range(n)]
            XtWX = [[sum(X[j][a] * w[j] * X[j][bb] for j in range(n)) for bb in range(k)] for a in range(k)]
            XtWy = [sum(X[j][a] * w[j] * y[j] for j in range(n)) for a in range(k)]
            try:
                Xi = inverse(XtWX)
                bi = matvec(Xi, XtWy)
                S += sum((Xi[a][a]) * (X[i][a] ** 2 * w[i]) for a in range(k))
            except ValueError:
                bi = [float("nan")] * k
            betas.append(bi)
            preds.append(sum(X[i][a] * bi[a] for a in range(k)))
        self.X, self.y, self.D = X, y, D
        self.betas, self.preds, self.tr_S = betas, preds, S
        return self

    def coef_summary(self):
        """各位置系数的最小值 / 中位 / 最大值——衡量空间非平稳（Brunsdon 1996）。"""
        k = len(self.names)
        out = []
        for j in range(k):
            vals = [self.betas[i][j] for i in range(len(self.betas))
                    if not math.isnan(self.betas[i][j])]
            vals.sort()
            if not vals:
                continue
            out.append([self.names[j], "%.4f" % vals[0],
                        "%.4f" % vals[len(vals) // 2], "%.4f" % vals[-1],
                        "%.4f" % (vals[-1] - vals[0])])
        return out

    def local_r2(self):
        """局部 R²（按核加权）。"""
        out = []
        n = len(self.y)
        for i in range(n):
            w = [_kernel_w(self.D[i][j], self.bw, self.kernel) for j in range(n)]
            sw = sum(w)
            if sw <= 1e-12:
                out.append(float("nan"))
                continue
            my = sum(w[j] * self.y[j] for j in range(n)) / sw
            sst = sum(w[j] * (self.y[j] - my) ** 2 for j in range(n))
            sse = sum(w[j] * (self.y[j] - self.preds[j]) ** 2 for j in range(n))
            out.append(1.0 - sse / sst if sst > 0 else float("nan"))
        return out


# ================================================================ 回归克里金

def fit_exponential_variogram(D, resid, nbins=12):
    """
    拟合指数型变异函数 gamma(h) = c0 + c1*(1 - exp(-h/a))。

    采用**按配对数的加权最小二乘**（Cressie 加权思想）：远距离 bin 配对数少、
    估计噪声大，赋予较低权重；否则尾部噪声会主导拟合、把 range 压到极小。
    """
    n = len(resid)
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            pairs.append((D[i][j], (resid[i] - resid[j]) ** 2 / 2.0))
    if not pairs:
        raise ValueError("无有效点对。")
    pairs.sort()
    mx = pairs[-1][0]
    hs, gs, ns = [], [], []
    for b in range(nbins):
        lo, hi = mx * b / nbins, mx * (b + 1) / nbins
        sel = [q[1] for q in pairs if lo <= q[0] < hi]
        if sel:
            hs.append((lo + hi) / 2.0)
            gs.append(mean(sel))
            ns.append(len(sel))
    if len(hs) < 3:
        raise ValueError("距离分箱不足，无法拟合变异函数。")
    best = None
    for k in range(1, 41):
        a = mx * k / 40.0
        Xv = [[1.0, 1.0 - math.exp(-h / a)] for h in hs]
        XtWX = [[sum(Xv[i][p2] * ns[i] * Xv[i][q2] for i in range(len(hs))) for q2 in range(2)] for p2 in range(2)]
        XtWy = [sum(Xv[i][p2] * ns[i] * gs[i] for i in range(len(hs))) for p2 in range(2)]
        try:
            b = matvec(inverse(XtWX), XtWy)
        except ValueError:
            continue
        pred = matvec(Xv, b)
        sse = sum(ns[i] * (gs[i] - pred[i]) ** 2 for i in range(len(hs)))
        if best is None or sse < best[1]:
            best = (a, sse, b)
    if best is None:
        raise ValueError("变异函数拟合失败。")
    a, _sse, b = best
    return {"model": "exponential", "range_a": a, "nugget_c0": max(b[0], 0.0),
            "sill_c1": max(b[1], 0.0), "bins_h": hs, "bins_gamma": gs, "bins_n": ns}


def regression_kriging(X, y, D, vario=None):
    """
    回归克里金（Oust et al. 2019 的空间增强之一）：
      ① 趋势项：OLS 拟合 ln(price) ~ X
      ② 残差项：对残差做普通克里金（指数变异函数）
      ③ 预测 = 趋势 + 克里金插值残差

    【评测口径】克里金在同点位插值必然精确复现观测（样本内 R²=1），
    故这里统一用**留一法（LOO）**残差克里金做样本外评测，避免自我实现的假精度。
    """
    b = ols(X, y)
    resid = [y[i] - sum(X[i][j] * b[j] for j in range(len(b))) for i in range(len(y))]
    n = len(y)
    if vario is None:
        vario = fit_exponential_variogram(D, resid)
    c0, c1, a = vario["nugget_c0"], vario["sill_c1"], vario["range_a"]

    def cov(h):
        return c0 + c1 * math.exp(-h / a) if h > 0 else c0 + c1

    # 留一法：用除 i 外的样本做普通克里金插值，预测 i 处残差
    loo_pred, loo_ok = [], []
    idx_all = list(range(n))
    for i in range(n):
        others = [j for j in idx_all if j != i]
        m = len(others)
        if m < 3:
            loo_pred.append(None)
            continue
        G = [[cov(D[p][q]) for q in others] + [1.0] for p in others]
        G.append([1.0] * m + [0.0])
        try:
            Gi = inverse(G)
        except ValueError:
            loo_pred.append(None)
            continue
        rhs = [cov(D[i][j]) for j in others] + [1.0]
        wk = matvec(Gi, rhs)
        kr = sum(wk[t] * resid[others[t]] for t in range(m))
        loo_pred.append(sum(X[i][j] * b[j] for j in range(len(b))) + kr)
        loo_ok.append(i)

    yv = [y[i] for i in loo_ok]
    pv = [loo_pred[i] for i in loo_ok]
    m_loo = metrics(yv, pv) if yv else None
    # 样本内（仅供对照，勿单独引用）
    in_pred = [sum(X[i][j] * b[j] for j in range(len(b))) + resid[i] for i in range(n)]
    return {"model": "regression-kriging", "beta": b, "variogram": vario,
            "metrics_loo": m_loo, "metrics_insample": metrics(y, in_pred), "n": n}


# ================================================================ CLI

def main():
    ap = argparse.ArgumentParser(description="空间计量方法全家族（Moran/SAR/SEM/GWR/克里金）")
    ap.add_argument("--data", required=True)
    ap.add_argument("--y", required=True)
    ap.add_argument("--x", required=True, nargs="+")
    ap.add_argument("--coord", required=True, nargs=2, help="两列坐标列名（经度 纬度 或 x y）")
    ap.add_argument("--method", default="all", choices=["moran", "sar", "sem", "gwr", "kriging", "all"])
    ap.add_argument("--metric", default="euclid", choices=["euclid", "haversine"])
    ap.add_argument("--knn", type=int, default=6)
    ap.add_argument("--kernel", default="gaussian", choices=["gaussian", "bisquare", "exponential"])
    args = ap.parse_args()

    _, rows = read_csv(args.data)
    X, y, C = [], [], []
    for r in rows:
        ys = to_float(r.get(args.y))
        xs = [to_float(r.get(n)) for n in args.x]
        c = [to_float(r.get(n)) for n in args.coord]
        if ys is None or any(v is None for v in xs) or any(v is None for v in c):
            continue
        X.append([1.0] + xs)
        y.append(ys)
        C.append(c)
    n = len(y)
    print("数据：%s | 有效样本 %d" % (args.data, n))
    if n < 15:
        print("⚠ 样本量偏小（%d），空间模型估计不稳定，结论仅供演示。" % n)

    D = distance_matrix(C, args.metric)
    W = knn_weights(D, args.knn)
    Ws = row_standardize(W)

    if args.method in ("moran", "all"):
        m = morans_i(Ws, y)
        print("\n=== Moran's I 空间自相关检验 ===")
        print("  I = %.4f   E[I] = %.4f   伪 p 值 = %.4f   (n=%d)"
              % (m["I"], m["E_I"], m["p_value"], m["n"]))
        print("  " + ("存在显著空间自相关 → 应使用空间模型（Pace & LeSage 2004）"
                      if m["p_value"] < 0.10 else "未检出显著空间自相关"))
        print("  对照 OLS：", end="")
        b = ols(X, y)
        pr = matvec(X, b)
        mm = metrics(y, pr, k=len(b))
        print("MAE=%.0f  MAPE=%.2f%%  R²=%.3f" % (mm["MAE"], mm["MAPE"], mm["R2"]))

    if args.method in ("sar", "all"):
        r = sar_mle(Ws, X, y)
        print("\n=== 空间滞后模型 SAR  y = ρWy + Xβ + ε ===")
        print("  ρ = %.4f   lnL = %.2f" % (r["rho"], r["loglik"]))
        print("  β = %s" % ", ".join("%.4f" % v for v in r["beta"]))
        print("  MAE=%.0f  MAPE=%.2f%%  R²=%.3f"
              % (r["metrics"]["MAE"], r["metrics"]["MAPE"], r["metrics"]["R2"]))

    if args.method in ("sem", "all"):
        r = sem_mle(Ws, X, y)
        print("\n=== 空间误差模型 SEM  y = Xβ + u, u = λWu + ε ===")
        print("  λ = %.4f   lnL = %.2f" % (r["lambda"], r["loglik"]))
        print("  β = %s" % ", ".join("%.4f" % v for v in r["beta"]))
        print("  MAE=%.0f  MAPE=%.2f%%  R²=%.3f"
              % (r["metrics"]["MAE"], r["metrics"]["MAPE"], r["metrics"]["R2"]))

    if args.method in ("gwr", "all"):
        g = GWR(args.x, args.y, args.coord, kernel=args.kernel, metric=args.metric)
        sel = g.select_bandwidth(*g._data(rows), kind="cv")
        g.bw = sel["bw"]
        g.fit(rows)
        print("\n=== 地理加权回归 GWR（%s 核）===" % args.kernel)
        print("  带宽（CV 选择）= %.4f   CV 得分 = %.4g" % (sel["bw"], sel["score"]))
        print("  系数空间分布（min / 中位 / max / 极差）：")
        print(fmt_table(["变量", "min", "中位", "max", "极差"], g.coef_summary(), ["<", ">", ">", ">", ">"]))
        mm = metrics(y, g.preds, k=len(g.names))
        lr2 = [v for v in g.local_r2() if not math.isnan(v)]
        print("  全局：MAE=%.0f  MAPE=%.2f%%  R²=%.3f" % (mm["MAE"], mm["MAPE"], mm["R2"]))
        if lr2:
            print("  局部 R²：中位 %.3f  最小 %.3f  最大 %.3f"
                  % (sorted(lr2)[len(lr2) // 2], min(lr2), max(lr2)))
        print("  说明：系数极差 > 0 即存在空间非平稳（Brunsdon 1996；Bitter 2007 优于空间扩展法）")

    if args.method in ("kriging", "all"):
        r = regression_kriging(X, y, D)
        print("\n=== 回归克里金（趋势 + 残差克里金，Oust et al. 2019）===")
        v = r["variogram"]
        print("  变异函数：指数型  nugget=%.4g  sill=%.4g  range=%.4g" % (v["nugget_c0"], v["sill_c1"], v["range_a"]))
        ml = r["metrics_loo"]
        if ml:
            print("  留一法（样本外）：MAE=%.0f  MAPE=%.2f%%  R²=%.3f" % (ml["MAE"], ml["MAPE"], ml["R2"]))
        print("  样本内 R²=%.3f（克里金同点位必然精确插值，此值无评测意义，仅供对照）"
              % r["metrics_insample"]["R2"])

    print("\n注：空间模型须先检验空间自相关；GWR 需处理多重共线性与带宽敏感性（Brunsdon 1996 局限）。")


if __name__ == "__main__":
    main()
