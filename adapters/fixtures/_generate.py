#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 fixtures/ 下的演示数据。

⚠️ 本脚本产出的**全部为合成演示数据**，用于验证适配器与方法的可运行性，
   **不是真实市场数据，也不得被引用为市场数据**。
   列名刻意使用中文，以验证适配器的别名解析能力（实务导出数据基本都是中文列头）。
"""

import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
random.seed(20260916)


def w(path, header, rows):
    with open(os.path.join(HERE, path), "w", encoding="utf-8-sig") as f:
        f.write(",".join(header) + "\n")
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")
    print("wrote %-24s %d 行" % (path, len(rows)))


# ---------------------------------------------------------------- 网签成交
rows = []
for i in range(60):
    area = round(random.uniform(55, 145), 1)
    unit = round(random.uniform(44000, 56000) * (1 + 0.09 * (area - 100) / 100 * 0), -0)
    unit = round(random.gauss(50000, 2600), 0)
    total = round(unit * area / 10000.0, 1)          # 万元
    rows.append(["2026-08", "演示市", "演示区", "演示小区A", area, total, int(unit)])
w("wangqian_demo.csv", ["期", "城市", "区县", "小区", "面积", "总价", "成交单价"], rows)

# ---------------------------------------------------------------- 挂牌（多平台）
rows = []
platforms = ["平台甲", "平台乙", "平台丙"]
# 故意造成平台间系统性差异：平台丙报价整体偏高约 6%，用于触发价差检出
bias = {"平台甲": 1.0, "平台乙": 0.985, "平台丙": 1.06}
for i in range(36):
    p = platforms[i % 3]
    area = round(random.uniform(60, 140), 1)
    unit = round(random.gauss(50200, 2400) * bias[p], 0)
    rows.append([p, "演示小区A", random.choice(["2室1厅", "3室1厅", "3室2厅"]), area,
                 random.choice(["低", "中", "高"]), random.choice(["南", "南北", "东南"]),
                 random.choice([2005, 2008, 2012]), int(unit), "D%03d" % (i + 1)])
w("listing_demo.csv",
  ["平台", "小区", "户型", "面积", "楼层", "朝向", "建成年份", "挂牌单价", "房源编号"], rows)

# ---------------------------------------------------------------- 租金
rows = []
for i in range(15):
    area = round(random.uniform(45, 130), 1)
    # 单位租金随面积递减（中国租赁市场的稳定特征），用于验证面积效应检出
    per_sqm = 88 - 0.28 * area + random.gauss(0, 4)
    rent = int(round(per_sqm * area, -1))
    rows.append(["2026-08", "演示小区A", random.choice(["1室1厅", "2室1厅", "3室1厅"]),
                 area, rent, random.choice(["低", "中", "高"]), "租赁平台甲"])
# 混入 2 条疑似合租样本，用于验证合租检出
rows.append(["2026-08", "演示小区A", "单间", 12.0, 2200, "高", "租赁平台乙"])
rows.append(["2026-08", "演示小区A", "单间", 15.5, 2600, "中", "租赁平台乙"])
w("rent_demo.csv", ["期", "小区", "户型", "面积", "月租", "楼层", "来源"], rows)

# ---------------------------------------------------------------- 土地出让
rows = []
for i in range(12):
    fa = round(random.uniform(2.0, 9.0) * 10000, 0)   # 规划建筑面积 ㎡
    fp = round(random.gauss(26000, 6000), 0)          # 楼面价 元/㎡
    tp = round(fp * fa / 1e8, 2)                      # 亿元
    cond = random.choice(["无", "配建保障房5%", "自持10年", "限价+摇号", "无偿移交幼儿园"])
    rows.append(["2026Q2", "演示市", "演示区", round(fa * 0.6, 0), fa, tp, int(fp),
                 random.choice(["住宅", "商业", "住宅"]), cond])
w("land_demo.csv",
  ["期", "城市", "区县", "土地面积", "规划建筑面积", "成交总价", "楼面价", "用途", "出让条件"], rows)

# ---------------------------------------------------------------- 70 城指数
rows = []
periods = ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]
cities = ["演示市", "对照市甲", "对照市乙"]
for c in cities:
    for i, p in enumerate(periods):
        mom = round(random.gauss(-0.15, 0.35), 1)
        yoy = round(random.gauss(-4.8, 0.9), 1)
        rows.append([p, c, "环比", mom, "二手住宅"])
        rows.append([p, c, "同比", yoy, "二手住宅"])
w("index70_demo.csv", ["期", "城市", "指标", "数值", "房屋类型"], rows)

with open(os.path.join(HERE, "_SYNTHETIC.txt"), "w", encoding="utf-8") as f:
    f.write("本目录下全部 CSV 为脚本生成的**合成演示数据**，非真实市场数据。\n"
            "用途：验证 adapters/ 与方法链的可运行性。禁止引用为市场数据。\n"
            "重新生成：python3 adapters/fixtures/_generate.py\n")
print("\n本目录全部数据为合成演示数据（已写 _SYNTHETIC.txt 标记）")
