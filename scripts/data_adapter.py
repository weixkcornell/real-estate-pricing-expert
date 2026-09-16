#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智见数据层适配器（data adapter）

职责：把**智见数据层**（`data-contracts/capability-contract.csv` 定义的能力）
转换为各方法的**模型就绪输入**，并在转换中做口径与单位的显式处理。

核心原则（来自专家包铁律）
--------------------------
1. **口径不混用**：挂牌 ≠ 成交 ≠ 租金 ≠ 官方指数，四类口径分别标记、不得静默互换。
2. **口径冲突显式说明**：多源不一致时并列呈现，不择优。
3. **单位标准化**：统一到「元/㎡（房价）」「元/㎡/月（租金）」「指数（定基=100，指数口径）」。
4. **缺失标「待补」**：不得以估计值冒充实测值。

能力 → 模型映射
---------------
| 能力（capability-contract） | 数据层口径 | 供给的模型 |
|---|---|---|
| rep.flow.wangqian.read   | 网签成交 | hedonic / 重复销售指数 / 混合指数 / 空间计量 / ML |
| rep.listing.price.read   | 挂牌报价 | 比较法 / hedonic（须折成交口径） |
| rep.rent.monthly.read    | 租金     | hedonic 租金 / 租金指数 / 价格租金比 / 用户成本 |
| rep.stats.70city.read    | 官方指数 | 指数交叉校验（禁当绝对量） |
| rep.land.parcel.read     | 土地出让 | 成本法与供给分析（楼面价） |

用法
----
    from data_adapter import DataAdapter
    da = DataAdapter.from_contract("../../data-contracts/capability-contract.csv")
    ds = da.build_hedonic_dataset(wangqian_rows, y="成交价", x=["面积","楼龄"])
    stats = da.normalize_prices(listing_rows, price_col="挂牌单价")

仅依赖 Python 标准库；共享数值核心见 <pack>/scripts/core.py。
"""

import math
import os
import sys


def _import_core():
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (here, os.path.join(here, "..")):
        c = os.path.abspath(cand)
        if os.path.isfile(os.path.join(c, "core.py")):
            if c not in sys.path:
                sys.path.insert(0, c)
            return
    raise ImportError("未找到共享核心 core.py")


_import_core()
from core import read_csv, to_float, mean, median, quantile  # noqa: E402


# 口径枚举
CALIBER_WANGQIAN = "网签"
CALIBER_LISTING = "挂牌"
CALIBER_RENT = "租金"
CALIBER_STATS = "统计"
CALIBER_LAND = "土地"

# 默认挂牌→成交折算系数（研判推断；须以真实成交数据替代）
DEFAULT_LISTING_TO_DEAL = (0.93, 0.97)


class DataAdapter:
    """
    智见数据层适配器。持有能力清单（来自 capability-contract），
    提供口径归一、单位换算、模型数据集构建。
    """

    def __init__(self, contract_rows=None):
        self.contract = {}
        for r in (contract_rows or []):
            cap = (r.get("capability") or "").strip()
            if cap:
                self.contract[cap] = r

    @classmethod
    def from_contract(cls, path):
        """从 capability-contract.csv 载入能力清单。"""
        if not os.path.isfile(path):
            # 允许缺省：仍可使用口径换算功能
            return cls([])
        _h, rows = read_csv(path)
        return cls(rows)

    # ---------------------------------------------------------- 能力查询

    def capabilities(self):
        return sorted(self.contract.keys())

    def caliber_of(self, capability):
        return (self.contract.get(capability, {}).get("caliber") or "").strip()

    def describe(self):
        out = []
        for cap in self.capabilities():
            r = self.contract[cap]
            out.append((cap, r.get("caliber", ""), r.get("unit", ""),
                        r.get("frequency", ""), r.get("lag", ""), r.get("auth", "")))
        return out

    # ---------------------------------------------------------- 口径换算

    def listing_to_deal(self, price, lo=None, hi=None):
        """
        挂牌价 → 成交价折算（**研判推断**，非实测）。
        北京存量房议价空间经验值约 3%–7%；返回区间而非单点，避免制造伪精度。
        """
        lo = lo if lo is not None else DEFAULT_LISTING_TO_DEAL[0]
        hi = hi if hi is not None else DEFAULT_LISTING_TO_DEAL[1]
        return {"point": price * (lo + hi) / 2.0, "low": price * lo, "high": price * hi,
                "discount": (lo, hi), "basis": "研判推断（挂牌价通常高于成交价）"}

    @staticmethod
    def index_rebase(series, base_index=0):
        """把官方价格指数序列重定基（默认首期为 100）。禁当绝对量使用。"""
        if not series:
            return []
        b = series[base_index]
        if not b:
            raise ValueError("基期值为 0，无法定基。")
        return [v / b * 100.0 for v in series]

    @staticmethod
    def normalize_prices(rows, price_col, unit_hint=None):
        """
        单位标准化到「元/㎡」。
        unit_hint: '元/㎡'（原样）| '万元/㎡'（×10000）| '万元'（需 total 与面积）| None（按量级自动判断）
        自动判断规则（仅当 unit_hint 为空时）：中位数量级 < 200 → 视为 万元/㎡。
        """
        vals = [to_float(r.get(price_col)) for r in rows]
        vals = [v for v in vals if v is not None]
        if not vals:
            return {"values": [], "unit": "元/㎡", "note": "无有效值"}
        m = median(vals)
        if unit_hint is None:
            unit_hint = "万元/㎡" if m < 200 else "元/㎡"
        if unit_hint == "万元/㎡":
            f = 10000.0
        elif unit_hint == "元/㎡":
            f = 1.0
        else:
            raise ValueError("无法识别单位：%s（本函数只处理单价口径）" % unit_hint)
        return {"values": [v * f for v in vals], "unit": "元/㎡", "factor": f,
                "note": "已按 %s → 元/㎡ 标准化" % unit_hint}

    @staticmethod
    def price_and_rent_from_total(total_price, area, monthly_rent, period_months=1):
        """由总价与面积得到单价，并计算单位租金与毛收益率。"""
        if not area or area <= 0:
            raise ValueError("面积必须为正。")
        unit_price = total_price / area
        unit_rent = monthly_rent / area
        annual = unit_rent * 12.0
        return {"单价(元/㎡)": unit_price, "单位租金(元/㎡/月)": unit_rent,
                "年租金(元/㎡/年)": annual, "毛租金收益率": annual / unit_price if unit_price else None}

    # ---------------------------------------------------------- 数据集构建

    def build_hedonic_dataset(self, rows, y, x, caliber=CALIBER_WANGQIAN):
        """
        构建 hedonic 模型就绪数据集。返回 dict，含 data（仅完整样本）与缺失诊断。
        caliber 用于在输出中标注口径；若为「挂牌」会附带折算提示。
        """
        ok, dropped = [], []
        for r in rows:
            ys = to_float(r.get(y))
            xs = [to_float(r.get(n)) for n in x]
            if ys is None or ys <= 0 or any(v is None for v in xs):
                miss = [n for n, v in zip(x, xs) if v is None] + ([] if ys else [y])
                dropped.append({"row": r, "missing": miss})
                continue
            ok.append(r)
        out = {"caliber": caliber, "n_total": len(rows), "n_usable": len(ok),
               "n_dropped": len(dropped), "y": y, "x": list(x), "data": ok,
               "dropped_missing_cols": sorted({c for d in dropped for c in d["missing"]})}
        if caliber == CALIBER_LISTING:
            out["warn"] = ("挂牌口径不得直接作成交定价：须做挂牌→成交折算"
                           "（DataAdapter.listing_to_deal，标注研判推断）")
        if caliber == CALIBER_STATS:
            out["warn"] = "官方指数口径是「指数」而非绝对量，禁当价格使用"
        return out

    def build_repeat_sales_pairs(self, rows, id_col, time_col, price_col):
        """
        从成交明细构造重复成交对（供 BMN / Case-Shiller / 混合指数）。
        同一 id_col 出现 ≥2 次时，按时间排序两两相邻（首→次、次→三…）配对。
        """
        by_id = {}
        for r in rows:
            pid = r.get(id_col)
            t = r.get(time_col)
            p = to_float(r.get(price_col))
            if not pid or not t or p is None or p <= 0:
                continue
            by_id.setdefault(pid, []).append((t, p))
        pairs = []
        for pid, seq in by_id.items():
            seq.sort(key=lambda z: str(z[0]))
            for i in range(len(seq) - 1):
                t1, p1 = seq[i]
                t2, p2 = seq[i + 1]
                if t1 == t2:
                    continue
                pairs.append({"物业编号": pid, "首次交易期": t1, "再次交易期": t2,
                              "首次成交价": p1, "再次成交价": p2})
        coverage = len(pairs) / len(rows) * 100.0 if rows else 0.0
        return {"pairs": pairs, "n_pairs": len(pairs), "n_transactions": len(rows),
                "coverage_pct": coverage,
                "warn": ("重复销售样本覆盖率 %.1f%%，存在样本选择偏差；须披露覆盖率"
                         "（Case & Shiller 1989；Pollakowski & Wachter 1997）" % coverage)}

    def build_price_rent_panel(self, sale_rows, rent_rows, area_col, sale_price_col, rent_col):
        """
        为「匹配价格租金比」准备两侧数据（Shanghai 2021 要求属性集一致）。
        """
        common = []
        s_cols = set(sale_rows[0].keys()) if sale_rows else set()
        r_cols = set(rent_rows[0].keys()) if rent_rows else set()
        common = sorted((s_cols & r_cols) - {sale_price_col, rent_col})
        return {"sale": sale_rows, "rent": rent_rows, "common_attributes": common,
                "note": ("价格与租金 hedonic 必须使用**相同属性集**才能构造可比价租比"
                         "（Shanghai 2021）；当前共同属性：%s" % (common or "无，需补充")),
                "area_col": area_col}

    # ---------------------------------------------------------- 交叉校验

    @staticmethod
    def cross_check(series_a, series_b, label_a="源A", label_b="源B"):
        """
        两源同类指标交叉校验：报告水平差与方向是否一致。
        冲突时**并列呈现、不静默择优**（专家包铁律）。
        """
        if not series_a or not series_b:
            return {"comparable": False, "note": "序列缺失"}
        ma, mb = mean(series_a), mean(series_b)
        da = series_a[-1] - series_a[0]
        db = series_b[-1] - series_b[0]
        same_dir = (da >= 0) == (db >= 0)
        return {"comparable": True, "mean_a": ma, "mean_b": mb,
                "level_gap_pct": (ma / mb - 1.0) * 100.0 if mb else None,
                "direction_a": "up" if da >= 0 else "down",
                "direction_b": "up" if db >= 0 else "down",
                "direction_consistent": same_dir,
                "conclusion": ("方向一致，可相互印证" if same_dir else
                               "方向冲突——须显式说明口径差异，不得静默择优")}


# ================================================================ CLI（自检）

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    contract = os.path.join(here, "..", "data-contracts", "capability-contract.csv")
    contract = os.path.abspath(contract)
    da = DataAdapter.from_contract(contract)
    print("=== 智见数据层能力清单 ===")
    cn = {"rep.flow.wangqian.read": "网签成交", "rep.listing.price.read": "挂牌报价",
          "rep.rent.monthly.read": "月度租金", "rep.stats.70city.read": "70 城官方指数",
          "rep.land.parcel.read": "土地出让"}
    for cap, cal, unit, freq, lag, auth in da.describe():
        print("  %-24s %-6s | %-14s | %-8s | 时滞 %-6s | %s"
              % (cap, cn.get(cap, ""), unit, freq, lag, auth))
    print("\n=== 口径换算自检 ===")
    print("  挂牌 50,210 元/㎡ → 成交区间 [%.0f, %.0f]（点估 %.0f）· 研判推断"
          % (da.listing_to_deal(50210)["low"], da.listing_to_deal(50210)["high"],
             da.listing_to_deal(50210)["point"]))
    print("  单位标准化：单价序列中位数 %.0f → %s"
          % (median([1.2, 1.5, 2.0]), da.normalize_prices([{"p": "1.2"}, {"p": "1.5"}, {"p": "2.0"}], "p")["unit"]))
    print("  官方指数定基（首期=100）：%s → %s"
          % ([100.1, 99.8, 100.4], ["%.2f" % v for v in da.index_rebase([100.1, 99.8, 100.4])]))
    print("\n=== 交叉校验自检 ===")
    cc = da.cross_check([100, 97, 94], [100, 99, 98], "源A", "源B")
    print("  水平差 %.1f%% | 方向一致=%s | %s" % (cc["level_gap_pct"], cc["direction_consistent"], cc["conclusion"]))
    print("\n注：本适配器只做口径与单位处理，不改变方法本身；所有折算均为研判推断级别。")


if __name__ == "__main__":
    main()
