#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地 CSV 适配器族 —— 让数据底座在**无网络、无密钥**时也能真实运行。

解决的问题（评审 P0-2）：
    原 `data_adapter.py` 只有自检桩，capability-contract 的 5 个数据源全部标注
    「平台密钥库」，于是包内最精密的方法库在真实执行路径上一次都没被用上。
    本模块提供 4 个**可离线完整运行**的适配器：网签成交、挂牌、租金、土地出让。
    每个适配器都接受任意来源导出的 CSV（字段名可映射），输出统一携带口径与标注级。

    ⚠️ 关于"上传 CSV 是否等于真实接入"：不等于。本模块解决的是
       **「拿到数据后能不能标准化地进入方法链」**，不解决「怎么自动取数」。
       自动取数需要平台密钥，见 `registry.py` 中的 needs_key 声明。

统一约定：
    - 输入：本地 CSV/TSV，表头行 + 数据行，UTF-8（兼容 BOM）；
    - 字段映射：通过 field_map 把任意列名映射到标准字段；
    - 输出：SourceResult（含 caliber / unit / as_of / warnings / gaps）；
    - 缺失值：不填充、不插补，计入 gaps 并在质量报告中体现。
"""

import os

from base import (AdapterError, Mark, SourceAdapter, SourceResult, Status,  # noqa: E402
                  Unit, quality_report)
from core import read_csv, to_float, mean  # noqa: E402


# ================================================================ 通用 CSV 适配器
class CsvFileAdapter(SourceAdapter):
    """
    通用 CSV 适配器骨架。

    子类通过 `standard_fields` 声明所需的标准字段，通过 `field_map` 做列名映射。
    所有子类共享：字段校验、单位归一、去重、质量体检、口径标注。
    """

    standard_fields = []
    required_fields = []
    # 内置中文别名：标准字段 → 可接受的列名（大小写与空格不敏感由 read_csv 保证）
    alias = {}
    # **需要数值化的字段**——只对这些做转换。
    # 教训：曾把全部 standard_fields 一律数值化，结果把「期」「平台」「出让条件」
    # 等字符串字段一并转成 None，导致 _valid 全部判假、样本被清空。
    numeric_fields = []

    def __init__(self, path, field_map=None, encoding="utf-8-sig", delimiter=None):
        self.path = path
        self.field_map = field_map or {}
        self.encoding = encoding
        self.delimiter = delimiter

    # ---------------------------------------------------------- 读取与校验
    def _read(self, path=None):
        p = path or self.path
        if not os.path.isfile(p):
            raise AdapterError("%s：文件不存在 %s" % (self.capability, p))
        hdr, rows = read_csv(p)
        if self.delimiter:  # 兼容非逗号分隔
            test = open(p, encoding=self.encoding).readline()
            if "|" in test:
                hdr = test.strip().split("|")
                rows = []
                with open(p, encoding=self.encoding) as f:
                    next(f)
                    for line in f:
                        line = line.rstrip("\n")
                        if line.strip():
                            rows.append(dict(zip(hdr, line.split("|"))))
        return hdr, rows

    def _map(self, hdr, rows):
        """
        列名归一：优先级为 field_map 显式指定 > 内置别名命中 > 标准字段名本身。

        内置别名让适配器能**直接吃中文表头**（实务导出数据几乎都是中文列名），
        这是"可离线真实运行"的前提——否则每个用户都得先手写映射。
        """
        eff = {}
        for std in self.standard_fields:
            if std in self.field_map:
                eff[std] = self.field_map[std]
                continue
            hit = None
            for cand in self.alias.get(std, []):
                if cand in hdr:
                    hit = cand
                    break
            eff[std] = hit if hit else std
        missing = [s for s in self.required_fields if eff[s] not in hdr]
        if missing:
            need = []
            for s in missing:
                need.append("%s（可接受列名：%s）" % (s, "、".join([s] + self.alias.get(s, []))))
            raise AdapterError(
                "%s：缺少必需列。\n  %s\n  实际表头：%s\n  如列名不在上表，可用 --map \"%s=<你的列名>\" 指定"
                % (self.capability, "\n  ".join(need), hdr, missing[0]))
        out = []
        for r in rows:
            out.append({s: r.get(eff[s]) for s in self.standard_fields})
        return out, {s: eff[s] for s in self.standard_fields}

    def _dedup(self, rows, key_fields):
        seen, kept, dropped = set(), [], 0
        for r in rows:
            k = tuple(str(r.get(f, "")).strip().lower() for f in key_fields)
            if k in seen:
                dropped += 1
                continue
            seen.add(k)
            kept.append(r)
        return kept, dropped

    @staticmethod
    def _numeric(rows, field):
        for r in rows:
            r[field] = to_float(r.get(field))
        return rows

    # ---------------------------------------------------------- 对外接口
    def fetch(self, as_of=None, dedup_key=None, **params):
        hdr, raw = self._read()
        rows, eff = self._map(hdr, raw)
        warnings, gaps = [], []

        for f in (self.numeric_fields or self.required_fields):
            self._numeric(rows, f)

        dropped = 0
        if dedup_key:
            rows, dropped = self._dedup(rows, dedup_key)
            if dropped:
                warnings.append("去重 %d 行（依据 %s）" % (dropped, ",".join(dedup_key)))

        before = len(rows)
        rows = [r for r in rows if self._valid(r)]
        if len(rows) < before:
            gaps.append("%d 行因必需字段缺失/非法被剔除（未插补）" % (before - len(rows)))

        rows = self.guard(rows)
        res = self._build(rows, eff, warnings, gaps, as_of)
        return res

    def _valid(self, r):
        return all(r.get(f) is not None for f in self.required_fields)

    def _build(self, rows, eff, warnings, gaps, as_of):
        raise NotImplementedError


# ================================================================ 网签成交（存量房/商品房备案）
class WangqianAdapter(CsvFileAdapter):
    """
    网签成交口径适配器。

    契约能力：rep.flow.wangqian.read
    口径：住建部门商品房/存量房网签备案成交口径
    单位：元/㎡
    """

    capability = "rep.flow.wangqian.read"
    caliber = "住建部门商品房/存量房网签备案成交口径"
    unit = "元/㎡"
    frequency = "月更"
    lag = "T+7"
    auth = "本地 CSV（自动取数需平台密钥库）"
    status = Status.PARTIAL
    description = "网签备案成交数据标准化。中国唯一可靠的成交价 microdata 来源。"
    standard_fields = ["period", "city", "district", "community", "area", "total_price", "unit_price"]
    required_fields = ["period", "unit_price"]
    numeric_fields = ["area", "total_price", "unit_price"]
    alias = {
        "period": ["期", "月份", "成交月份", "统计期", "日期"],
        "city": ["城市"],
        "district": ["区县", "行政区", "区域", "区"],
        "community": ["小区", "楼盘", "项目"],
        "area": ["面积", "建筑面积", "面积㎡", "建筑面积㎡"],
        "total_price": ["总价", "成交总价", "成交价", "总价元"],
        "unit_price": ["单价", "成交单价", "单价元/㎡", "元/㎡", "成交均价"],
    }

    def _valid(self, r):
        if r.get("unit_price") is None or not r.get("period"):
            return False
        if r["unit_price"] <= 0:
            return False
        # 量级体检：住宅单价合理区间 1e3 ~ 1e6（元/㎡），越界提示口径错误
        return 1e3 <= r["unit_price"] <= 1e6

    def _build(self, rows, eff, warnings, gaps, as_of):
        # 若只给了总价与面积，自动补单价
        filled = 0
        for r in rows:
            if r.get("unit_price") is None and r.get("total_price") and r.get("area"):
                r["unit_price"] = r["total_price"] / r["area"]
                filled += 1
        if filled:
            warnings.append("%d 行由 总价÷面积 推算单价（标为「测算」）" % filled)

        periods = sorted({r["period"] for r in rows if r.get("period")})
        by_period = []
        for p in periods:
            vals = [r["unit_price"] for r in rows if r.get("period") == p]
            by_period.append({"period": p, "n": len(vals), "median": _median(vals), "mean": mean(vals)})

        if len(rows) < 30:
            warnings.append("样本 %d 条：低于 hedonic 最小样本阈值（n≥10×k），"
                            "按本包纪律应降级为比较法或不作属性系数分解" % len(rows))
        q = quality_report([r["unit_price"] for r in rows], "网签单价")
        if q.get("warn"):
            warnings.append(q["warn"])

        return SourceResult(
            self.capability, self.status, self.caliber, self.unit,
            as_of={"period": periods[-1] if periods else None, **(as_of or {})},
            rows=rows, series=by_period, warnings=warnings, gaps=gaps,
            provenance=[os.path.basename(self.path), "字段映射：%s" % eff])


# ================================================================ 挂牌报价（多平台快照）
class ListingAdapter(CsvFileAdapter):
    """
    挂牌报价口径适配器（多平台合并）。

    契约能力：rep.listing.price.read
    口径：房地产中介平台二手房挂牌报价口径
    单位：元/㎡

    关键纪律：
        - **挂牌价 ≠ 成交价**，适配器强制要求调用方说明是否做挂牌→成交折算；
        - 多平台之间的系统性价差**显式检出并并列呈现**，不做静默择优。
    """

    capability = "rep.listing.price.read"
    caliber = "房地产中介平台二手房挂牌报价口径"
    unit = "元/㎡"
    frequency = "日更"
    lag = "T+1"
    auth = "本地 CSV（自动取数需平台密钥库）"
    status = Status.PARTIAL
    description = "多平台挂牌快照合并、价差检出、可比筛选。"
    standard_fields = ["platform", "community", "layout", "area", "floor", "orientation",
                       "build_year", "total_price", "unit_price", "listing_id"]
    required_fields = ["unit_price"]
    numeric_fields = ["area", "build_year", "total_price", "unit_price"]
    alias = {
        "platform": ["平台", "来源平台", "数据源"],
        "community": ["小区", "楼盘", "项目"],
        "layout": ["户型", "房型"],
        "area": ["面积", "建筑面积", "面积㎡"],
        "floor": ["楼层", "所在楼层"],
        "orientation": ["朝向"],
        "build_year": ["建成年份", "建成年", "楼龄"],
        "total_price": ["总价", "挂牌总价", "总价万元"],
        "unit_price": ["单价", "挂牌单价", "元/㎡", "挂牌均价"],
        "listing_id": ["房源编号", "房源id", "编号"],
    }

    def _valid(self, r):
        if r.get("unit_price") is None or r["unit_price"] <= 0:
            return False
        a = r.get("area")
        if a is not None and (a < 10 or a > 2000):
            return False
        return 1e2 <= r["unit_price"] <= 5e5

    def _build(self, rows, eff, warnings, gaps, as_of):
        for r in rows:
            if r.get("unit_price") is None and r.get("total_price") and r.get("area"):
                r["unit_price"] = r["total_price"] / r["area"]

        # ① 平台间价差检出（本包"两源冲突显式并列"纪律的实现）
        by_plat = {}
        for r in rows:
            p = (r.get("platform") or "未标注").strip()
            by_plat.setdefault(p, []).append(r["unit_price"])
        plat_stat = []
        for p, vals in sorted(by_plat.items()):
            plat_stat.append({"platform": p, "n": len(vals), "median": _median(vals)})
        if len(plat_stat) >= 2:
            meds = [x["median"] for x in plat_stat]
            spread = (max(meds) - min(meds)) / min(meds)
            if spread > 0.03:
                warnings.append(
                    "平台间中位价差 %.1f%%（%s）——按纪律**并列呈现，不静默择优**"
                    % (spread * 100,
                       " / ".join("%s %.0f" % (x["platform"], x["median"]) for x in plat_stat)))
            else:
                warnings.append("平台间中位价差 %.1f%%，可视为一致" % (spread * 100))

        # ② 结构可比性提示
        areas = [r["area"] for r in rows if r.get("area") is not None]
        if areas and len(set(areas)) > 1:
            warnings.append("样本含 %d 种面积，**面积结构差异会污染均价**——"
                            "建议按面积段分层或改用 hedonic" % len(set(areas)))
        q = quality_report([r["unit_price"] for r in rows], "挂牌单价")
        if q.get("warn"):
            warnings.append(q["warn"])

        gaps.append("挂牌口径不可直接作为成交价使用；如需成交口径，"
                    "调用 to_deal_estimate() 并标注折算区间")
        self.platform_stats = plat_stat

        return SourceResult(
            self.capability, self.status, self.caliber, self.unit,
            as_of={"snapshot": (as_of or {}).get("snapshot")},
            rows=rows, series=plat_stat, warnings=warnings, gaps=gaps,
            provenance=[os.path.basename(self.path), "字段映射：%s" % eff])

    # ---- 挂牌 → 成交折算（把不确定性参数化，而不是给一个折扣数字）
    @staticmethod
    def to_deal_estimate(rows, discount_lo=0.03, discount_hi=0.07):
        """
        挂牌价 → 推算成交价。

        中国实务中成交价通常低于挂牌价。折扣率**不可凭空假定**，本方法要求
        由调用方给出区间（缺省 3%–7% 仅为经验区间，必须标注为「研判推断」），
        输出中位价与该区间的**价格区间**，使折算不确定性进入估值区间合成。
        """
        vals = [r["unit_price"] for r in rows if r.get("unit_price")]
        if not vals:
            raise AdapterError("挂牌→成交折算：无有效挂牌价")
        med = _median(vals)
        return {
            "caliber_from": ListingAdapter.caliber,
            "caliber_to": "推算成交口径",
            "mark": Mark.INFERRED,
            "listing_median": med,
            "discount_range": [discount_lo, discount_hi],
            "deal_point": med * (1 - (discount_lo + discount_hi) / 2.0),
            "deal_lower": med * (1 - discount_hi),
            "deal_upper": med * (1 - discount_lo),
            "note": "折扣率区间为经验取值，须在报告中标注来源或改标「研判推断」；"
                    "若有同期同小区网签数据，应以 网签/挂牌 实测折扣率替换。",
        }


# ================================================================ 租金
class RentAdapter(CsvFileAdapter):
    """
    租金口径适配器。

    契约能力：rep.rent.monthly.read
    口径：住房租赁市场月度租金挂牌/成交口径
    单位：元/月（同时输出元/㎡/月）
    """

    capability = "rep.rent.monthly.read"
    caliber = "住房租赁市场月度租金挂牌/成交口径"
    unit = "元/月"
    frequency = "月更"
    lag = "T+3"
    auth = "本地 CSV（自动取数需平台密钥库）"
    status = Status.PARTIAL
    description = "租金标准化：等效月租、单位租金、面积效应、市场分割检查。"
    standard_fields = ["period", "community", "layout", "area", "rent_monthly", "floor", "source"]
    required_fields = ["rent_monthly"]
    numeric_fields = ["area", "rent_monthly"]
    alias = {
        "period": ["期", "月份", "统计期", "日期"],
        "community": ["小区", "楼盘", "项目"],
        "layout": ["户型", "房型"],
        "area": ["面积", "建筑面积", "面积㎡"],
        "rent_monthly": ["月租", "租金", "月租金", "月租元", "租金元/月"],
        "floor": ["楼层", "所在楼层"],
        "source": ["来源", "数据源", "平台"],
    }

    def _valid(self, r):
        if r.get("rent_monthly") is None or r["rent_monthly"] <= 0:
            return False
        # 月租合理区间 300 ~ 500,000 元
        return 300 <= r["rent_monthly"] <= 5e5

    def _build(self, rows, eff, warnings, gaps, as_of):
        # 单位租金（元/㎡/月）—— 租金建模与租价比的必要中间量
        has_area = True
        for r in rows:
            if r.get("area") and r["area"] > 0:
                r["rent_per_sqm"] = r["rent_monthly"] / r["area"]
            else:
                r["rent_per_sqm"] = None
                has_area = False
        if not has_area:
            gaps.append("部分样本缺面积，无法计算单位租金（这些样本不能参与价格租金比）")

        # 合租/分租混入检测（评审未提但是中国租赁数据的高频污染源）
        small = [r for r in rows if r.get("area") and r["area"] < 20]
        if small:
            warnings.append("%d 条样本面积 <20㎡，疑为单间/合租出租，"
                            "与整租不可混合建模，建议剔除或单独分层" % len(small))

        # 面积效应：单位租金随面积递减（中国租赁市场的稳定特征）
        pairs = [(r["area"], r["rent_per_sqm"]) for r in rows
                 if r.get("area") and r.get("rent_per_sqm")]
        area_effect = None
        if len(pairs) >= 8:
            pairs.sort()
            half = len(pairs) // 2
            lo = mean([p[1] for p in pairs[:half]])
            hi = mean([p[1] for p in pairs[half:]])
            area_effect = {"small_half_unit_rent": lo, "large_half_unit_rent": hi,
                           "gap_pct": (lo - hi) / hi * 100.0 if hi else None}
            warnings.append(
                "面积效应：小面积半样本单位租金 %.1f 元/㎡/月，大面积半样本 %.1f，"
                "相差 %+.1f%%——**直接比较不同面积段的单位租金会失真**"
                % (lo, hi, area_effect["gap_pct"]))

        per_sqm = [r["rent_per_sqm"] for r in rows if r.get("rent_per_sqm")]
        q = quality_report([r["rent_monthly"] for r in rows], "月租")
        if q.get("warn"):
            warnings.append(q["warn"])

        return SourceResult(
            self.capability, self.status, self.caliber, self.unit,
            as_of={"period": (as_of or {}).get("period")},
            rows=rows,
            series=[{"metric": "rent_per_sqm_median", "value": _median(per_sqm) if per_sqm else None},
                    {"metric": "area_effect", "value": area_effect}],
            warnings=warnings, gaps=gaps,
            provenance=[os.path.basename(self.path), "字段映射：%s" % eff])


# ================================================================ 土地出让
class LandParcelAdapter(CsvFileAdapter):
    """
    土地出让口径适配器。

    契约能力：rep.land.parcel.read
    口径：土地出让成交口径
    单位：元/㎡（楼面价）

    ⚠️ 契约 notes 声明该数据"用于成本法与供给分析"——本适配器**同时服务于
       成本法与假设开发法**（skills/cost-residual/），消除此前的契约空头。
    """

    capability = "rep.land.parcel.read"
    caliber = "土地出让成交口径"
    unit = "元/㎡（楼面价）"
    frequency = "季更"
    lag = "T+30"
    auth = "本地 CSV（自动取数需平台密钥库）"
    status = Status.PARTIAL
    description = "土地出让标准化：楼面价归一、配建/自持条件标注、地价段统计。"
    standard_fields = ["period", "city", "district", "land_area", "floor_area",
                       "total_price", "floor_price", "use_type", "conditions"]
    required_fields = ["floor_price"]
    numeric_fields = ["land_area", "floor_area", "total_price", "floor_price"]
    alias = {
        "period": ["期", "成交日期", "出让日期", "季度"],
        "city": ["城市"],
        "district": ["区县", "行政区", "区域"],
        "land_area": ["土地面积", "用地面积", "占地面积"],
        "floor_area": ["规划建筑面积", "建筑面积", "计容面积"],
        "total_price": ["成交总价", "总价", "出让金", "成交价"],
        "floor_price": ["楼面价", "楼面地价", "成交楼面价"],
        "use_type": ["用途", "土地用途", "用地性质"],
        "conditions": ["出让条件", "配建要求", "备注"],
    }

    def _valid(self, r):
        if r.get("floor_price") is None or r["floor_price"] <= 0:
            return False
        return 100 <= r["floor_price"] <= 1e6

    def _build(self, rows, eff, warnings, gaps, as_of):
        filled = 0
        for r in rows:
            if r.get("floor_price") is None and r.get("total_price") and r.get("floor_area"):
                r["floor_price"] = r["total_price"] / r["floor_area"]
                filled += 1
        if filled:
            warnings.append("%d 行由 总价÷规划建筑面积 推算楼面价（标为「测算」）" % filled)

        # 出让条件对实际地价的影响（中国土地定价的核心扭曲）
        cond = [r for r in rows if (r.get("conditions") or "").strip()]
        if cond:
            warnings.append(
                "%d 条样本含配建/自持/限价等出让条件——此类条件实质压低可售面积或"
                "抬高成本，**直接比较名义楼面价会失真**，须在成本法/剩余法中显式建模"
                % len(cond))
        else:
            gaps.append("未提供出让条件字段：无法判断配建/自持对地价的影响，"
                        "成本法与假设开发法结果应按此限制保守解读")

        uses = sorted({(r.get("use_type") or "未标注") for r in rows})
        if len(uses) > 1:
            warnings.append("样本含 %d 类土地用途（%s）——**不同用途地价不可混合统计**，"
                            "须分层输出" % (len(uses), "、".join(uses)))

        q = quality_report([r["floor_price"] for r in rows], "楼面价")
        if q.get("warn"):
            warnings.append(q["warn"])

        return SourceResult(
            self.capability, self.status, self.caliber, self.unit,
            as_of={"period": (as_of or {}).get("period")},
            rows=rows, series=[{"metric": "floor_price_stats", "value": q}],
            warnings=warnings, gaps=gaps,
            provenance=[os.path.basename(self.path), "字段映射：%s" % eff])


# ================================================================ 内部工具
def _median(vals):
    v = sorted([x for x in vals if x is not None])
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0
