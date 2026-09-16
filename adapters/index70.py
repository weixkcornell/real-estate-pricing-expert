#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
70 个大中城市住宅销售价格指数适配器。

契约能力：rep.stats.70city.read
口径：国家统计局 70 个大中城市住宅销售价格指数口径
单位：指数（定基=100）
频率：月更　时滞：T+15　鉴权：公开（5 QPS）

为什么单独实现这一个：
    它是契约中**唯一鉴权为「公开」**的数据源，因此也是唯一可以真正端到端打通的
    官方口径。它的作用不是估值本身，而是给其他口径（挂牌、成交、租金）做
    **独立第三方校验**——本包 Trial Run 正是靠它发现"小区挂牌同比 −12.4% vs
    全市官方 −4.5%~−5.5%"这个关键分化。没有它，所有的同比都是自说自话。

国家统计局公开发布的口径有两个易被混淆的形式：
    - **环比**（本月与上月比，%）  —— 反映短期动能
    - **同比**（本月与上年同月比，%）—— 反映年度变化
两种序列不能相加、不能互换。要得到跨期水平序列必须用**链式合成**（见下）。
"""

import os

from base import AdapterError, Mark, SourceAdapter, SourceResult, Status, Unit  # noqa: E402
from core import read_csv, to_float, mean  # noqa: E402


class Index70CityAdapter(SourceAdapter):
    capability = "rep.stats.70city.read"
    caliber = "国家统计局70个大中城市住宅销售价格指数口径"
    unit = "指数（定基=100）"
    frequency = "月更"
    lag = "T+15"
    auth = "public"
    status = Status.IMPLEMENTED
    description = "70 城指数解析：环比链式定基、同比序列、跨源交叉校验。"

    # 支持的指标别名 → 标准指标
    INDICATOR_ALIAS = {
        "环比": "mom", "mom": "mom", "月环比": "mom",
        "同比": "yoy", "yoy": "yoy", "年同比": "yoy",
        "定基": "level", "level": "level", "指数": "level",
    }

    def __init__(self, path, field_map=None):
        self.path = path
        # 标准字段：period(期) / city(城市) / indicator(指标) / value(数值) / category(房屋类型)
        self.fm = field_map or {}

    def probe(self):
        ok = os.path.isfile(self.path)
        return ok, ("指数文件可用" if ok else "指数文件不存在：%s" % self.path)

    # ---------------------------------------------------------- 解析
    def _load(self):
        if not os.path.isfile(self.path):
            raise AdapterError("70城指数：文件不存在 %s" % self.path)
        hdr, rows = read_csv(self.path)
        eff = {
            "period": self.fm.get("period", "期"),
            "city": self.fm.get("city", "城市"),
            "indicator": self.fm.get("indicator", "指标"),
            "value": self.fm.get("value", "数值"),
            "category": self.fm.get("category", "房屋类型"),
        }
        miss = [k for k, v in eff.items() if v not in hdr and k in ("period", "indicator", "value")]
        if miss:
            raise AdapterError("70城指数：缺少列 %s（实际表头 %s）。可用 field_map 指定映射。"
                               % (miss, hdr))
        out = []
        for r in rows:
            ind_raw = (r.get(eff["indicator"]) or "").strip()
            ind = self.INDICATOR_ALIAS.get(ind_raw)
            if ind is None:
                continue
            out.append({
                "period": (r.get(eff["period"]) or "").strip(),
                "city": (r.get(eff["city"]) or "").strip(),
                "category": (r.get(eff["category"]) or "").strip() or "未标注",
                "indicator": ind,
                "indicatorRaw": ind_raw,
                "value": to_float(r.get(eff["value"])),
            })
        if not out:
            raise AdapterError("70城指数：解析后无有效记录（指标列须含 环比/同比/定基 之一）")
        return self.guard(out), eff

    # ---------------------------------------------------------- 主流程
    def fetch(self, city=None, category=None, **params):
        rows, eff = self._load()
        warnings, gaps = [], []

        if city:
            rows = [r for r in rows if r["city"] == city]
        if category:
            rows = [r for r in rows if r["category"] == category]
        rows = self.guard(rows)

        by_ind = {}
        for r in rows:
            by_ind.setdefault(r["indicator"], []).append(r)

        series, derived = {}, {}

        # ① 环比 → 定基（链式合成）。这是把"月度动能"变成"跨期水平"的唯一正确方式。
        if "mom" in by_ind:
            recs = sorted(by_ind["mom"], key=lambda x: x["period"])
            base_period = recs[0]["period"]
            level = Unit.chain_mom([r["value"] for r in recs])
            derived["mom_chained_level"] = [
                {"period": r["period"], "level": lv} for r, lv in zip(recs, level)]
            series["mom_chained_base"] = base_period
            warnings.append(
                "定基序列由**环比链式合成**，基期 %s = 100（标为「测算」）；"
                "环比链式会累积月度舍入误差，跨 3 年以上区间解读需谨慎" % base_period)

        # ② 同比 → 水平（粗糙反推，仅交叉校验用）
        if "yoy" in by_ind:
            recs = sorted(by_ind["yoy"], key=lambda x: x["period"])
            lvl = Unit.chain_yoy_to_level([r["value"] for r in recs])
            derived["yoy_implied_level"] = [
                {"period": r["period"], "level": lv} for r, lv in zip(recs, lvl)]
            warnings.append(
                "由同比反推的水平序列为**近似值**（需上年同期基期值才能精确还原），"
                "仅可用于方向性交叉校验，**不得作为主口径引用**——标注为「研判推断」")

        # ③ 官方定基序列（若有）
        if "level" in by_ind:
            series["official_level"] = [
                {"period": r["period"], "level": r["value"]}
                for r in sorted(by_ind["level"], key=lambda x: x["period"])]

        # ④ 交叉校验：环比链式 vs 同比反推，方向是否一致
        check = None
        if "mom_chained_level" in derived and "yoy_implied_level" in derived:
            a = {d["period"]: d["level"] for d in derived["mom_chained_level"]}
            b = {d["period"]: d["level"] for d in derived["yoy_implied_level"]}
            common = sorted(set(a) & set(b))
            if len(common) >= 2:
                da = a[common[-1]] - a[common[-2]]
                db = b[common[-1]] - b[common[-2]]
                direction_match = (da >= 0) == (db >= 0)
                check = {
                    "period": common[-1],
                    "mom_chained_delta": da,
                    "yoy_implied_delta": db,
                    "direction_match": direction_match,
                }
                if not direction_match:
                    warnings.append(
                        "**两口径方向相反**（环比链式 %+.2f vs 同比反推 %+.2f，期 %s）——"
                        "按本包纪律须**并列呈现、不静默择优**，并在报告中说明冲突原因"
                        % (da, db, common[-1]))
        if check:
            series["cross_check"] = check

        if not by_ind:
            gaps.append("未解析到 环比/同比/定基 任何一类指标")

        latest = max((r["period"] for r in rows), default=None)
        return SourceResult(
            self.capability, self.status, self.caliber, self.unit,
            as_of={"period": latest, "city": city, "category": category},
            rows=rows, series=series, warnings=warnings, gaps=gaps,
            provenance=[os.path.basename(self.path), "字段映射：%s" % eff])

    # ---------------------------------------------------------- 与本地序列的对照
    def compare_with_local(self, local_series, local_label="本地序列", period=None):
        """
        把本地（挂牌/成交）同比与 70 城官方同比并列，检出分化。

        用途：本包 Trial Run 的关键发现（小区挂牌同比 −12.4% vs 全市官方 −4.5%~−5.5%）
        正是这一步产出的。分化本身不是错误，而是**需要解释的信号**。
        """
        rows, _ = self._load()
        yoy = [r for r in rows if r["indicator"] == "yoy"]
        if period:
            yoy = [r for r in yoy if r["period"] == period]
        if not yoy:
            return {"ok": False, "reason": "70城指数中无同比记录"}
        vals = [r["value"] for r in yoy]
        lo, hi = min(vals), max(vals)
        out = {
            "ok": True, "official_yoy_range": [lo, hi],
            "official_yoy_mean": mean(vals), "n_city": len(vals),
            "official_caliber": self.caliber, "official_mark": Mark.QUOTED,
            "local_label": local_label, "local_mark": Mark.MEASURED,
        }
        if local_series is not None:
            out["local_yoy"] = local_series
            out["divergence_pp"] = local_series - mean(vals)
            if abs(out["divergence_pp"]) > 5:
                out["warn"] = ("本地序列同比 %+.1f%% 与官方均值 %+.1f%% 相差 %.1f 个百分点，"
                               "**需在报告中显式归因**（常见原因：口径不同、样本结构差异、"
                               "小区与全市分化、样本量不足）"
                               % (local_series, mean(vals), out["divergence_pp"]))
        return out
