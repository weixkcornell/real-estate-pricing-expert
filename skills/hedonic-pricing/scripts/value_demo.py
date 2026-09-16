#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模型价值实测：朴素均价法 vs 特征价格模型

复现定价报告中「第二章 2.3 模型价值实测」的全部数字。

用途：向业务方/评审方量化说明「为什么必须用模型定价」——
      同样的数据，只因用了模型，单套定价误差显著收敛。

运行
----
    python3 value_demo.py                 # 使用同目录 sample_data.csv
    python3 value_demo.py --data 其他数据.csv

输出
----
    1. 朴素均价法 与 特征价格模型 的 MAE / MAPE 对比
    2. 属性隐含价格（朝向、楼层的溢价）
    3. 逐样本误差对照（谁改善、谁退步）

依赖：仅标准库 + 同目录 hedonic_model.py
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hedonic_model import (  # noqa: E402
    read_csv, HedonicModel, naive_metrics, to_float,
)

# 建模特征：面积（数值）、南向类（0/1）、高楼层（0/1）
Y = "挂牌单价(元/㎡)"
X = ["面积㎡", "南向类(南北/西南/南=1)", "高楼层(高层=1)"]


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="模型价值实测：均价法 vs 特征价格模型")
    ap.add_argument("--data", default=os.path.join(here, "sample_data.csv"))
    args = ap.parse_args()

    _, rows = read_csv(args.data)

    # 过滤出同时具备因变量与全部自变量的样本
    def usable(r):
        yv = to_float(r.get(Y))
        if yv is None or yv <= 0:
            return False
        return all(to_float(r.get(n)) is not None for n in X)

    data = [r for r in rows if usable(r)]
    print("数据：%s" % os.path.basename(args.data))
    print("可用样本：%d 条  特征：%s" % (len(data), " + ".join(X)))
    print()

    # ---------- 朴素均价法 ----------
    nm = naive_metrics(data, Y)
    y = [to_float(r.get(Y)) for r in data]
    print("=" * 62)
    print("朴素均价法（以样本均值为唯一预测）")
    print("=" * 62)
    print("  样本均价      : %.0f 元/㎡" % nm["mean"])
    print("  单价极差      : %d 元/㎡ (%.1f%%)"
          % (max(y) - min(y), (max(y) - min(y)) / nm["mean"] * 100))
    print("  MAE           : %.0f 元/㎡" % nm["MAE"])
    print("  MAPE          : %.2f%%" % nm["MAPE"])

    # ---------- 特征价格模型 ----------
    model = HedonicModel(Y, X, log_y=True).fit(data)
    m = model.fit_metrics(data)

    print()
    print("=" * 62)
    print("特征价格模型  ln(单价) ~ 面积 + 南向类 + 高楼层")
    print("=" * 62)
    for name, b, pct in model.implicit_prices():
        if pct is None:
            print("  %-26s %12.4f  （截距）" % (name, b))
        elif name.startswith("面积"):
            print("  %-26s %12.4f  →  %+6.2f%% / ㎡   （每 +10㎡ 约 %+.2f%%）"
                  % (name, b, pct, (pow(1 + pct / 100.0, 10) - 1) * 100))
        else:
            print("  %-26s %12.4f  →  %+6.2f%%" % (name, b, pct))
    print("  %-30s MAE         %.0f 元/㎡" % ("", m["MAE"]))
    print("  %-30s MAPE        %.2f%%" % ("", m["MAPE"]))
    print("  %-30s R²          %.3f" % ("", m["R2"]))

    # ---------- 对比 ----------
    print()
    print("=" * 62)
    print("结论")
    print("=" * 62)
    print("  MAE   %.0f → %.0f 元/㎡   改善 %+.1f%%"
          % (nm["MAE"], m["MAE"], (m["MAE"] - nm["MAE"]) / nm["MAE"] * 100))
    print("  MAPE  %.2f%% → %.2f%%    改善 %.2f 个百分点"
          % (nm["MAPE"], m["MAPE"], nm["MAPE"] - m["MAPE"]))
    if m["MAPE"] > 0:
        print("  含义：单套定价误差从 ±%.1f%% 收敛到 ±%.1f%%" % (nm["MAPE"], m["MAPE"]))

    # ---------- 逐样本 ----------
    print()
    print("=" * 62)
    print("逐样本误差对照")
    print("=" * 62)
    print("  %-22s %10s %12s %12s  %s" % ("样本", "实际", "均价法误差", "模型误差", "改善"))
    pred = model.predict(data)
    wins = 0
    for i, r in enumerate(data):
        label = "%s㎡ %s" % (r["面积㎡"], r["朝向"])
        en = nm["mean"] - y[i]
        em = pred[i] - y[i]
        better = abs(em) < abs(en)
        wins += 1 if better else 0
        print("  %-22s %10.0f %+12.0f %+12.0f  %s"
              % (label, y[i], en, em, "✓" if better else "✗"))
    print()
    print("  %d/%d 套改善" % (wins, len(data)))

    # ---------- 交叉验证 ----------
    cv = model.loo_cv(data)
    if cv:
        print()
        print("  留一交叉验证（样本外）：MAE %.0f 元/㎡   MAPE %.2f%%" % (cv["MAE"], cv["MAPE"]))

    print()
    print("注：自由度 = %d，属小样本演示，系数方向稳健但绝对水平不具统计显著性。"
          % (len(data) - len(X) - 1))


if __name__ == "__main__":
    main()
