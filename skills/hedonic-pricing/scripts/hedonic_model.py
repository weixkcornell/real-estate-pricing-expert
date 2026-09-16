#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
特征价格（Hedonic）定价模型 —— 纯 Python 实现，零第三方依赖。

方法来源：Rosen, S. (1974) "Hedonic Prices and Implicit Markets", Journal of Political Economy, 82(1), 34-55.

核心思想：房产价格 = 属性束的隐含价格之和。用回归把价格拆到每一个可观测属性上，
从而（1）给出可解释的估值，（2）分解出「朝向值多少、楼层值多少」。

用法示例
--------
    # 拟合模型并输出属性隐含价格与拟合优度
    python3 hedonic_model.py --data sample_data.csv --y 挂牌单价 \
        --x "面积㎡" "南向类(南北/西南/南=1)" "高楼层(高层=1)" --log-y

    # 附加留一交叉验证
    python3 hedonic_model.py --data sample_data.csv --y 挂牌单价 \
        --x "面积㎡" "南向类(南北/西南/南=1)" "高楼层(高层=1)" --log-y --cv

    # 用拟合结果给新样本定价
    python3 hedonic_model.py --data sample_data.csv --y 挂牌单价 \
        --x "面积㎡" "南向类(南北/西南/南=1)" "高楼层(高层=1)" --log-y \
        --predict "105" "1" "1"

输出
----
    - 估计系数 beta
    - 属性隐含价格（log 因变量时自动换算为百分比效应）
    - 拟合指标：MAE / MAPE / R²
    - 可选：留一交叉验证的 MAE / MAPE

仅依赖 Python 标准库（csv / math / argparse），可直接在任何环境运行。
"""

import argparse
import csv
import math
import os
import sys


# ---------------------------------------------------------------- 数据读取

def read_csv(path):
    """读取 CSV，返回 (表头, 行字典列表)。自动跳过空行并去除 BOM。"""
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


def to_float(s):
    """容错转数字：允许千分位逗号、全角空格；空值返回 None。"""
    if s is None:
        return None
    t = str(s).replace(",", "").replace("\u3000", "").strip()
    if t == "" or t in ("-", "—", "NA", "N/A", "待补"):
        return None
    return float(t)


# ---------------------------------------------------------------- 线性代数

def ols(X, y):
    """
    普通最小二乘：解正态方程 (X'X) b = X'y。
    用带部分主元的高斯-约当消元，避免依赖 numpy。
    """
    n = len(X)
    k = len(X[0])
    A = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    rhs = [sum(X[i][a] * y[i] for i in range(n)) for a in range(k)]

    for c in range(k):
        p = max(range(c, k), key=lambda r: abs(A[r][c]))
        if abs(A[p][c]) < 1e-12:
            raise ValueError("设计矩阵奇异（存在完全共线或常数列），无法估计。")
        A[c], A[p] = A[p], A[c]
        rhs[c], rhs[p] = rhs[p], rhs[c]
        piv = A[c][c]
        for cc in range(c, k):
            A[c][cc] /= piv
        rhs[c] /= piv
        for r in range(k):
            if r != c and A[r][c] != 0.0:
                f = A[r][c]
                for cc in range(c, k):
                    A[r][cc] -= f * A[c][cc]
                rhs[r] -= f * rhs[c]
    return rhs


# ---------------------------------------------------------------- 指标

def mae(y, p):
    return sum(abs(y[i] - p[i]) for i in range(len(y))) / len(y)


def mape(y, p):
    return sum(abs(y[i] - p[i]) / y[i] for i in range(len(y))) / len(y) * 100.0


def r_squared(y, p):
    m = sum(y) / len(y)
    sst = sum((v - m) ** 2 for v in y)
    sse = sum((y[i] - p[i]) ** 2 for i in range(len(y)))
    return 1.0 - sse / sst if sst > 0 else float("nan")


# ---------------------------------------------------------------- 模型

class HedonicModel:
    """
    特征价格模型。

    参数
    ----
    y_name    : 因变量列名（价格或单价）
    x_names   : 自变量列名列表（数值列；分类变量请预先编码为 0/1 虚拟变量）
    log_y     : 是否对因变量取对数（半对数模型，房地产实证最常用，见 Malpezzi 综述）
    """

    def __init__(self, y_name, x_names, log_y=False):
        self.y_name = y_name
        self.x_names = list(x_names)
        self.log_y = log_y
        self.beta = None
        self.names = ["截距"] + list(x_names)

    def _design(self, rows, need_y=True):
        X, y = [], []
        for r in rows:
            xs = [to_float(r.get(n)) for n in self.x_names]
            if any(v is None for v in xs):
                continue
            if need_y:
                yv = to_float(r.get(self.y_name))
                if yv is None or yv <= 0:
                    continue
                y.append(math.log(yv) if self.log_y else yv)
            X.append([1.0] + xs)
        return X, y

    def fit(self, rows):
        X, y = self._design(rows)
        if len(X) <= len(self.names):
            raise ValueError(
                "样本量 %d 不足以估计 %d 个参数（自由度 ≤ 0）。"
                "按 Skill 规则应降级为比较法或扩大样本。" % (len(X), len(self.names)))
        self.beta = ols(X, y)
        return self

    def predict(self, rows):
        if self.beta is None:
            raise RuntimeError("请先调用 fit()")
        out = []
        for r in rows:
            xs = [to_float(r.get(n)) for n in self.x_names]
            if any(v is None for v in xs):
                out.append(None)
                continue
            z = self.beta[0] + sum(self.beta[j + 1] * xs[j] for j in range(len(xs)))
            out.append(math.exp(z) if self.log_y else z)
        return out

    def implicit_prices(self):
        """属性隐含价格：log 因变量 → 百分比效应 exp(b)-1；水平因变量 → 单位变化量。"""
        if self.beta is None:
            raise RuntimeError("请先调用 fit()")
        res = []
        for i, name in enumerate(self.names):
            b = self.beta[i]
            if i == 0:
                res.append((name, b, None))
            elif self.log_y:
                res.append((name, b, (math.exp(b) - 1.0) * 100.0))
            else:
                res.append((name, b, b))
        return res

    def fit_metrics(self, rows):
        y = []
        keep = []
        for r in rows:
            yv = to_float(r.get(self.y_name))
            xs = [to_float(r.get(n)) for n in self.x_names]
            if yv is None or yv <= 0 or any(v is None for v in xs):
                continue
            y.append(yv)
            keep.append(r)
        p = [v for v in self.predict(keep) if v is not None]
        if len(p) != len(y):
            raise RuntimeError("预测缺失，检查数据完整性")
        return {"n": len(y), "MAE": mae(y, p), "MAPE": mape(y, p), "R2": r_squared(y, p)}

    def loo_cv(self, rows):
        """留一交叉验证：逐条剔除后训练，预测被剔除样本。"""
        y_all, p_all = [], []
        for i in range(len(rows)):
            train = rows[:i] + rows[i + 1:]
            yv = to_float(rows[i].get(self.y_name))
            xs = [to_float(rows[i].get(n)) for n in self.x_names]
            if yv is None or yv <= 0 or any(v is None for v in xs):
                continue
            try:
                m = HedonicModel(self.y_name, self.x_names, self.log_y).fit(train)
            except ValueError:
                continue
            pred = m.predict([rows[i]])[0]
            if pred is None:
                continue
            y_all.append(yv)
            p_all.append(pred)
        if not y_all:
            return None
        return {"n": len(y_all), "MAE": mae(y_all, p_all), "MAPE": mape(y_all, p_all)}


def naive_metrics(rows, y_name):
    """朴素均价法基准：用样本均值预测每一条。"""
    y = [to_float(r.get(y_name)) for r in rows]
    y = [v for v in y if v is not None and v > 0]
    if not y:
        return None
    m = sum(y) / len(y)
    p = [m] * len(y)
    return {"n": len(y), "mean": m, "MAE": mae(y, p), "MAPE": mape(y, p)}


# ---------------------------------------------------------------- CLI

def main():
    ap = argparse.ArgumentParser(description="特征价格（Hedonic）定价模型 — 零依赖实现")
    ap.add_argument("--data", required=True, help="CSV 文件路径")
    ap.add_argument("--y", required=True, help="因变量列名（价格或单价，须为正数）")
    ap.add_argument("--x", required=True, nargs="+", help="自变量列名（数值列；分类请预编码 0/1）")
    ap.add_argument("--log-y", action="store_true", help="对因变量取对数（半对数模型，推荐）")
    ap.add_argument("--cv", action="store_true", help="额外输出留一交叉验证结果")
    ap.add_argument("--predict", nargs="+", help="给出预测：按 --x 顺序传入各属性值")
    args = ap.parse_args()

    if not os.path.exists(args.data):
        sys.exit("文件不存在：%s" % args.data)

    _, rows = read_csv(args.data)
    print("数据：%s" % args.data)
    print("样本：%d 条" % len(rows))

    model = HedonicModel(args.y, args.x, log_y=args.log_y).fit(rows)

    print("\n=== 估计系数 ===")
    for name, b, pct in model.implicit_prices():
        if pct is None:
            print("  %-28s %12.4f" % (name, b))
        elif args.log_y:
            print("  %-28s %12.4f   → %+7.2f%%" % (name, b, pct))
        else:
            print("  %-28s %12.4f   （单位变化量）" % (name, b))

    m = model.fit_metrics(rows)
    print("\n=== 拟合效果（全样本）===")
    print("  n=%d   MAE=%.0f   MAPE=%.2f%%   R²=%.3f" % (m["n"], m["MAE"], m["MAPE"], m["R2"]))

    nm = naive_metrics(rows, args.y)
    if nm:
        print("\n=== 对照：朴素均价法 ===")
        print("  样本均值=%.0f   MAE=%.0f   MAPE=%.2f%%" % (nm["mean"], nm["MAE"], nm["MAPE"]))
        if m["MAE"] > 0:
            print("  模型相对均价法 MAE 变化：%+.1f%%" % ((m["MAE"] - nm["MAE"]) / nm["MAE"] * 100))

    if args.cv:
        cv = model.loo_cv(rows)
        if cv:
            print("\n=== 留一交叉验证（LOO-CV，样本外）===")
            print("  n=%d   MAE=%.0f   MAPE=%.2f%%" % (cv["n"], cv["MAE"], cv["MAPE"]))

    if args.predict:
        if len(args.predict) != len(args.x):
            sys.exit("--predict 需按 --x 顺序提供 %d 个值" % len(args.x))
        row = {n: v for n, v in zip(args.x, args.predict)}
        val = model.predict([row])[0]
        print("\n=== 预测 ===")
        for n in args.x:
            print("  %-28s %s" % (n, row[n]))
        print("  → 估计值：%.0f" % val)

    print("\n注：本模型输出为量化参考；小样本（自由度低）时系数方向稳健但绝对水平不具统计显著性。")


if __name__ == "__main__":
    main()
