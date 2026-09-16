#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临时验证脚本：用正确的 Moran's I（KNN 权重 + 置换检验）检查基准房价是否含空间自相关。"""
import csv, math, os, random

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOUSE = os.path.join(ROOT, "benchmark", "benchmark_house.csv")

def f(x):
    try:
        return float(x)
    except Exception:
        return None

rows = list(csv.DictReader(open(HOUSE, encoding="utf-8-sig")))
rows = [r for r in rows if f(r["经度"]) is not None and f(r["纬度"]) is not None]
n = len(rows)
lon = [f(r["经度"]) for r in rows]
lat = [f(r["纬度"]) for r in rows]
ylog = [math.log(f(r["成交单价元每平"])) for r in rows]
yraw = [f(r["成交单价元每平"]) for r in rows]

# 坐标各自标准化（经纬度量纲不同），避免距离畸变
def z(v):
    m = sum(v) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in v) / n) or 1.0
    return [(x - m) / sd for x in v]
clon, clat = z(lon), z(lat)
C = [[clon[i], clat[i]] for i in range(n)]

K = 6
# KNN 权重（对称化）
D = [[math.hypot(C[i][0]-C[j][0], C[i][1]-C[j][1]) for j in range(n)] for i in range(n)]
W = [[0.0]*n for _ in range(n)]
for i in range(n):
    order = sorted(range(n), key=lambda j: D[i][j])
    for j in order[1:K+1]:
        W[i][j] = 1.0
        W[j][i] = 1.0
# 行标准化
for i in range(n):
    s = sum(W[i])
    if s > 0:
        W[i] = [w / s for w in W[i]]

def morans(y):
    my = sum(y) / n
    num = sum(W[i][j] * (y[i]-my) * (y[j]-my) for i in range(n) for j in range(n))
    den = sum((x-my)**2 for x in y)
    S0 = sum(W[i][j] for i in range(n) for j in range(n))
    return (n / S0) * num / den

def perm_p(y, obs, nperm=2000, seed=1):
    rnd = random.Random(seed)
    yl = y[:]
    ge = 0
    for _ in range(nperm):
        rnd.shuffle(yl)
        if abs(morans(yl)) >= abs(obs):
            ge += 1
    return (ge + 1) / (nperm + 1)

for name, y in (("raw", yraw), ("log", ylog)):
    I = morans(y)
    p = perm_p(y, I)
    print("%s: I=%.4f  E_I=%.4f  perm_p=%.4f" % (name, I, -1.0/(n-1), p))
