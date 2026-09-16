#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智见房地产定价方法库 · 共享数值核心（core）

零第三方依赖（仅 Python 标准库），为各方法模块提供统一的：
  - 数据读取与类型转换
  - 线性代数（矩阵乘 / 转置 / 求逆 / OLS / WLS）
  - 模型评价指标（MAE / MAPE / RMSE / R² / 调整 R²）
  - 统计工具（分位、标准化、正态分布、t 近似）

设计原则
--------
1. 所有方法模块从本文件导入基础能力，保证口径一致、避免重复实现。
2. 数值稳定优先：求逆用带部分主元的高斯-约当消元，奇异时明确报错而非静默给错值。
3. 不做隐式假设：样本量不足、共线、缺失值一律显式报错或标注。

对应论文
--------
本核心本身不含方法，方法见各 skill 的 scripts/：
  hedonic-pricing / price-index / spatial-ml-valuation / rent-income
"""

import csv
import math

# ------------------------------------------------------------------ 数据读取

def read_csv(path):
    """读取 CSV，返回 (表头列表, 行字典列表)。自动处理 BOM、空行、首尾空白。"""
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV 无表头：%s" % path)
        headers = [h.strip() for h in reader.fieldnames]
        rows = []
        for raw in reader:
            row = {}
            for k, v in raw.items():
                if k is None:
                    continue
                row[k.strip()] = (v or "").strip()
            if any(row.values()):
                rows.append(row)
    return headers, rows


NULLS = ("", "-", "—", "–", "NA", "N/A", "nan", "null", "待补", "无")


def to_float(s):
    """容错转浮点：支持千分位逗号与全角空格；空/占位符返回 None。"""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).replace(",", "").replace("\u3000", "").strip()
    if t in NULLS:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def numeric_column(rows, name):
    """取数值列；缺失值以 None 返回，保持与行对齐。"""
    return [to_float(r.get(name)) for r in rows]


def complete_cases(rows, cols):
    """筛出所有指定列均非空的行。"""
    out = []
    for r in rows:
        if all(to_float(r.get(c)) is not None for c in cols):
            out.append(r)
    return out


# ------------------------------------------------------------------ 线性代数

def transpose(A):
    if not A:
        return []
    return [list(col) for col in zip(*A)]


def matmul(A, B):
    if not A or not B:
        return []
    n, k, m = len(A), len(B), len(B[0])
    if len(B) != k:
        raise ValueError("矩阵维度不匹配：%dx%d × %dx%d" % (n, k, len(B), m))
    BT = transpose(B)
    return [[sum(A[i][t] * BT[j][t] for t in range(k)) for j in range(m)] for i in range(n)]


def matvec(A, v):
    return [sum(A[i][j] * v[j] for j in range(len(v))) for i in range(len(A))]


def inverse(A):
    """带部分主元的高斯-约当求逆；奇异时抛错。"""
    n = len(A)
    M = [list(A[i]) + [1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-12:
            raise ValueError("矩阵奇异（存在完全共线或常数列），无法求逆。")
        M[c], M[p] = M[p], M[c]
        piv = M[c][c]
        for j in range(2 * n):
            M[c][j] /= piv
        for r in range(n):
            if r != c and M[r][c] != 0.0:
                f = M[r][c]
                for j in range(2 * n):
                    M[r][j] -= f * M[c][j]
    return [row[n:] for row in M]


def solve(A, b):
    """解线性方程组 A x = b（A 为方阵）。"""
    return matvec(inverse(A), b)


def ols(X, y, weights=None):
    """
    最小二乘估计。

    X : n×k 设计矩阵（须显式含截距列）
    y : n 维因变量
    weights : 可选 n 维权重（WLS）；权重语义为「观测精度」，即目标函数 Σ w_i (y_i - x_i b)²

    返回 beta（k 维列表）
    """
    n = len(X)
    k = len(X[0])
    if n < k:
        raise ValueError("样本量 %d < 参数个数 %d，无法估计（自由度不足）。" % (n, k))
    if weights is None:
        XtX = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
        Xty = [sum(X[i][a] * y[i] for i in range(n)) for a in range(k)]
    else:
        XtX = [[sum(X[i][a] * weights[i] * X[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
        Xty = [sum(X[i][a] * weights[i] * y[i] for i in range(n)) for a in range(k)]
    return solve(XtX, Xty)


def wls_by_sigma(X, y, sigma):
    """
    以「标准差」为权重尺度的 WLS（Case-Shiller 第三阶段即用此），
    权重 w_i = 1 / σ_i²（σ_i 为第 i 个观测的预测标准差）。
    """
    w = [1.0 / (s * s) if s and s > 1e-12 else 1.0 for s in sigma]
    return ols(X, y, weights=w)


def standard_errors(X, resid, beta=None):
    """OLS 参数标准误：σ² = SSE/(n-k)，Var(b) = σ² (X'WX)^-1。"""
    n = len(X)
    k = len(X[0])
    dof = n - k
    if dof <= 0:
        return None
    sse = sum(r * r for r in resid)
    s2 = sse / dof
    XtX = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    Xi = inverse(XtX)
    return [math.sqrt(max(s2 * Xi[j][j], 0.0)) for j in range(k)]


def vif(X):
    """方差膨胀因子：返回各解释变量（不含截距）的 VIF 列表。"""
    out = []
    p = len(X[0])
    for j in range(1, p):
        yj = [row[j] for row in X]
        Xo = [[row[c] for c in range(p) if c != j] for row in X]
        try:
            b = ols(Xo, yj)
        except ValueError:
            out.append(float("inf"))
            continue
        pred = matvec(Xo, b)
        r2 = r_squared(yj, pred)
        out.append(float("inf") if r2 >= 1.0 - 1e-12 else 1.0 / (1.0 - r2))
    return out


# ------------------------------------------------------------------ 指标

def mean(xs):
    return sum(xs) / len(xs)


def mae(y, p):
    return sum(abs(y[i] - p[i]) for i in range(len(y))) / len(y)


def mape(y, p):
    return sum(abs(y[i] - p[i]) / y[i] for i in range(len(y))) / len(y) * 100.0


def rmse(y, p):
    return math.sqrt(sum((y[i] - p[i]) ** 2 for i in range(len(y))) / len(y))


def r_squared(y, p):
    m = mean(y)
    sst = sum((v - m) ** 2 for v in y)
    if sst <= 0:
        return float("nan")
    sse = sum((y[i] - p[i]) ** 2 for i in range(len(y)))
    return 1.0 - sse / sst


def adj_r_squared(y, p, k):
    """k = 参数个数（含截距）。"""
    n = len(y)
    if n - k <= 0:
        return float("nan")
    r2 = r_squared(y, p)
    return 1.0 - (1.0 - r2) * (n - 1) / (n - k)


def metrics(y, p, k=None):
    m = {"n": len(y), "MAE": mae(y, p), "MAPE": mape(y, p), "RMSE": rmse(y, p), "R2": r_squared(y, p)}
    if k:
        m["AdjR2"] = adj_r_squared(y, p, k)
    return m


def median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def quantile(xs, q):
    s = sorted(xs)
    if not s:
        return None
    i = q * (len(s) - 1)
    lo = int(i)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def standardize(xs):
    """返回 (标准化列表, 均值, 标准差)。"""
    m = mean(xs)
    sd = math.sqrt(sum((v - m) ** 2 for v in xs) / len(xs))
    sd = sd if sd > 1e-12 else 1.0
    return [(v - m) / sd for v in xs], m, sd


# ------------------------------------------------------------------ 分布

def norm_cdf(z):
    """标准正态 CDF（Abramowitz-Stegun 7.1.26 近似，误差 < 7.5e-8）。"""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def normal_interval(pred, se, level=0.95):
    """正态近似预测区间。"""
    z = {0.80: 1.2816, 0.90: 1.6449, 0.95: 1.9600, 0.99: 2.5758}.get(level, 1.9600)
    return pred - z * se, pred + z * se


def z_for(level):
    return {0.80: 1.2816, 0.90: 1.6449, 0.95: 1.9600, 0.99: 2.5758}.get(level, 1.9600)


# ------------------------------------------------------------------ 交叉验证

def kfold_indices(n, k=5, seed=42):
    """确定性 K 折划分（线性同余伪随机，便于复现）。"""
    idx = list(range(n))
    state = seed
    for i in range(n - 1, 0, -1):
        state = (1103515245 * state + 12345) % (2 ** 31)
        j = state % (i + 1)
        idx[i], idx[j] = idx[j], idx[i]
    folds = [[] for _ in range(k)]
    for pos, i in enumerate(idx):
        folds[pos % k].append(i)
    return folds


def loo_indices(n):
    return [[i] for i in range(n)]


# ------------------------------------------------------------------ 输出

def fmt_table(headers, rows, aligns=None):
    """简易等宽表格输出。"""
    if not rows:
        return ""
    cols = len(headers)
    widths = [len(str(headers[j])) for j in range(cols)]
    for r in rows:
        for j in range(cols):
            widths[j] = max(widths[j], len(str(r[j])))
    aligns = aligns or ["<"] * cols
    line = "  ".join(("{:%s%d}" % (aligns[j], widths[j])).format(str(headers[j])) for j in range(cols))
    sep = "  ".join("-" * widths[j] for j in range(cols))
    body = "\n".join("  ".join(("{:%s%d}" % (aligns[j], widths[j])).format(str(r[j])) for j in range(cols)) for r in rows)
    return line + "\n" + sep + "\n" + body
