#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
适配器基类与数据契约对象。

设计原则（针对评审 P0-2「数据底座是契约而非实现」）：
    1. **诚实优先**：适配器的 status 必须如实反映可实现性。需要平台密钥的数据源
       标注 `needs_key`，**绝不假装可用**、绝不用伪造数据填充。
    2. **口径是数据的一部分**：任何返回值都携带 caliber / unit / as_of，
       让下游不可能"忘记标注口径"。
    3. **可离线运行**：本地 CSV/TSV 路径必须零依赖可用，使方法库在无网络、无密钥
       时仍能被完整验证（这也是本包"数据条件决定方法层级"纪律的实现基础）。
    4. **缺口显式**：拿不到的指标进 warnings 或 gaps，而不是静默丢弃。

输出统一为 SourceResult，字段与 `data-contracts/capability-contract.csv` 对齐。
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _c in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _c not in sys.path:
        sys.path.insert(0, _c)

from core import read_csv, to_float, mean  # noqa: E402


# ---------------------------------------------------------------- 标注级
class Mark:
    """数字的标注级（与 pack 输出规范一致）。"""
    QUOTED = "引用"     # 官方/权威来源直接引用
    MEASURED = "测算"   # 由明确数据计算得出
    ESTIMATED = "估算"  # 含假设的估算
    INFERRED = "研判推断"  # 判断性结论
    PENDING = "待补"    # 数据缺失


class Status:
    """适配器可用性状态——**报告与 registry 都以此为准**。"""
    IMPLEMENTED = "implemented"      # 本地可完整运行
    PARTIAL = "partial"              # 有本地回退路径，但主通路需密钥
    NEEDS_KEY = "needs_key"          # 仅契约声明，需平台密钥库授权
    UNIMPLEMENTED = "unimplemented"  # 未实现


class AdapterError(Exception):
    pass


# ---------------------------------------------------------------- 结果对象
class SourceResult:
    """适配器返回值。强制携带口径，使下游无法"忘记标注口径"。"""

    def __init__(self, capability, status, caliber, unit, as_of=None,
                 rows=None, series=None, warnings=None, gaps=None, provenance=None):
        self.capability = capability
        self.status = status
        self.caliber = caliber
        self.unit = unit
        self.as_of = as_of or {}
        self.rows = rows or []
        self.series = series or []
        self.warnings = warnings or []
        self.gaps = gaps or []
        self.provenance = provenance or []

    def ok(self):
        return self.status in (Status.IMPLEMENTED, Status.PARTIAL) and bool(self.rows or self.series)

    def summary(self):
        lines = [
            "[%s] %s" % (self.status, self.capability),
            "  口径: %s" % self.caliber,
            "  单位: %s" % self.unit,
            "  截至期: %s" % (self.as_of.get("period") or "未标注"),
            "  记录: %d 行 / %d 序列点" % (len(self.rows), len(self.series)),
        ]
        for w in self.warnings:
            lines.append("  ⚠ %s" % w)
        for g in self.gaps:
            lines.append("  ○ 缺口: %s" % g)
        return "\n".join(lines)


# ---------------------------------------------------------------- 基类
class SourceAdapter:
    """
    所有数据适配器的基类。

    子类必须提供类属性：
        capability  契约中的能力 id（须与 capability-contract.csv 一致）
        caliber     口径声明
        unit        单位
        frequency   更新频率
        lag         时滞
        auth        鉴权方式（public / 平台密钥库）
        status      可用性状态
    并实现 fetch(**params) -> SourceResult
    """

    capability = None
    caliber = ""
    unit = ""
    frequency = ""
    lag = ""
    auth = "public"
    status = Status.UNIMPLEMENTED
    description = ""

    def probe(self):
        """
        可用性探测：不取数，只判断"在当前环境下能否取数"。
        返回 (可用: bool, 原因: str)。密钥型适配器默认不可用。
        """
        if self.status == Status.IMPLEMENTED:
            return True, "本地实现，可直接运行"
        if self.status == Status.PARTIAL:
            return True, "本地回退路径可用；主通路需 %s" % self.auth
        if self.status == Status.NEEDS_KEY:
            return False, "需 %s 授权后才能取数（当前不伪造数据）" % self.auth
        return False, "未实现"

    def fetch(self, **params):
        raise NotImplementedError

    def guard(self, rows):
        """通用前置校验：样本必须存在且非空。"""
        if not rows:
            raise AdapterError("%s：样本为空，拒绝返回空结果（空结果易被误读为 0）" % self.capability)
        return rows


# ---------------------------------------------------------------- 单位与口径换算
class Unit:
    """单位换算与指数定基。所有换算都必须显式调用，禁止隐式变形。"""

    @staticmethod
    def area_price(price_total, area, src_unit="元/套", area_unit="㎡"):
        """总价 → 单价（元/㎡）。"""
        out = []
        for p, a in zip(price_total, area):
            if p is None or a is None or a <= 0:
                out.append(None)
            else:
                out.append(p / a)
        return out

    @staticmethod
    def wan_to_yuan(v):
        """万元 → 元。"""
        return None if v is None else v * 10000.0

    @staticmethod
    def sqm_to_total(unit_price, area):
        """单价（元/㎡） × 面积（㎡） → 总价（元）。"""
        return None if (unit_price is None or area is None) else unit_price * area

    @staticmethod
    def rebase(series, base_index=0):
        """
        指数定基：把序列换算为以 base_index 期为 100 的指数。
        用于跨来源序列对齐（如 70 城环比链式合成 vs 中指同比）。
        """
        if not series:
            return []
        base = series[base_index]
        if not base:
            raise AdapterError("定基失败：基期值为 0 或缺失")
        return [None if v is None else v / base * 100.0 for v in series]

    @staticmethod
    def chain_mom(mom_pct_series):
        """
        环比序列 → 定基指数（第 1 期为 100）。mom_pct 为环比百分点（0.3 表示 +0.3%）。

        ⚠️ **缺口处理**：一旦遇到缺失（None）的环比，其**后所有期一律返回 None**。

        理由：缺失期的真实变动未知。若跳过它继续连乘，等于**默认缺口期变动为 0%**——
        这是臆造数据，会让一个断掉的序列看起来是连续的。宁可留空并在报告中标「待补」。
        （原实现确实会静默跨过缺口继续链式；这是被回归测试抓出的真实缺陷，
          对应本包铁律"编造比不回答更严重"。）
        """
        idx, cur, broken = [], 100.0, False
        for i, m in enumerate(mom_pct_series):
            if m is None:
                broken = True
                idx.append(None)
                continue
            if broken:
                idx.append(None)
                continue
            if i > 0:
                cur *= (1.0 + m / 100.0)
            idx.append(cur)
        return idx

    @staticmethod
    def chain_yoy_to_level(yoy_pct_series, base_level=100.0):
        """
        同比序列 → 水平指数（粗糙反推，仅作交叉校验用）。
        注意：同比链式反推需要上年同期的基期值，此处按"逐年同比连乘"近似，
        因此**不可作为主口径**，必须在调用处标注为「研判推断」。
        """
        lvl, prev_year = [], base_level
        for i, y in enumerate(yoy_pct_series):
            if y is None:
                lvl.append(None)
                continue
            if i < 12:
                lvl.append(prev_year * (1.0 + y / 100.0))
            else:
                lvl.append(lvl[i - 12] * (1.0 + y / 100.0))
        return lvl


# ---------------------------------------------------------------- 质量体检
def quality_report(values, name="样本"):
    """通用样本体检：返回计数与离散度，供上游判断可比性。"""
    v = [x for x in values if x is not None]
    if not v:
        return {"name": name, "n": 0, "warn": "无有效值"}
    m = mean(v)
    sd = (sum((x - m) ** 2 for x in v) / len(v)) ** 0.5
    cv = (sd / m) if m else None
    vs = sorted(v)
    n = len(vs)
    med = vs[n // 2] if n % 2 else (vs[n // 2 - 1] + vs[n // 2]) / 2.0
    out = {
        "name": name, "n": n, "mean": m, "median": med, "sd": sd,
        "min": vs[0], "max": vs[-1], "cv": cv,
    }
    if cv is not None and cv > 0.20:
        out["warn"] = "变异系数 %.1f%% 偏高，样本可比性差（建议重选样本或分层）" % (cv * 100)
    return out
