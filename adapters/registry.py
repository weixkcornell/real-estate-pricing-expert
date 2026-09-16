#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
适配器注册表 —— capability-contract 的实现对照与可用性探测。

解决的核心问题（评审 P0-2）：
    契约声明了 5 个数据源、全部标注「平台密钥库」，但包内**没有任何适配器实现**，
    也没有任何机制说明"哪些真能用、哪些只是声明"。结果是：
    包内最精密的方法库在最真实的执行路径上一次都没被调用。

    本注册表提供三件事：
      1. **自动对照**：读 capability-contract.csv，逐行给出「声明 vs 实现」状态；
      2. **可用性探测**：不取数，只回答"当前环境下这个源能不能用、为什么不能"；
      3. **诚实边界**：需要平台密钥的源标注 NEEDS_KEY，**不伪造数据、不假装可用**。

用法：
    python3 adapters/registry.py                    # 打印契约实现对照表
    python3 adapters/registry.py --probe            # 附带可用性探测说明
    python3 adapters/registry.py --selftest         # 用演示数据跑通全部本地适配器
"""

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _c in (_ROOT, os.path.join(_ROOT, "scripts"), _HERE):
    if _c not in sys.path:
        sys.path.insert(0, _c)

from base import Status  # noqa: E402
from core import read_csv  # noqa: E402
from index70 import Index70CityAdapter  # noqa: E402
from local_csv import (LandParcelAdapter, ListingAdapter, RentAdapter,  # noqa: E402
                       WangqianAdapter)

CONTRACT = os.path.join(_ROOT, "data-contracts", "capability-contract.csv")
FIXTURES = os.path.join(_HERE, "fixtures")


# ================================================================ 自动取数声明
class RemoteFetch:
    """
    契约中标注「平台密钥库」的数据源——**自动取数未实现**。

    这里不是"TODO 占位"，而是一份**明确的边界声明**：本包不持有也不索取密钥，
    因此不会尝试联网取数。用户有两条合法路径：
      (a) 自行导出数据为 CSV → 交给 adapters/local_csv.py 的对应适配器（已实现）；
      (b) 由平台在运行时注入授权 → 在 `fetch_hook` 中接入，本类提供接口约定。

    之所以不做"看起来能用"的桩函数：那会让下游误判数据可得性，
    进而把一个降级结论当成完整结论交付——这正是评审指出的最危险后果。
    """

    def __init__(self, capability, reason, alt_adapter):
        self.capability = capability
        self.status = Status.NEEDS_KEY
        self.reason = reason
        self.alt_adapter = alt_adapter
        self.fetch_hook = None  # 平台注入点：(params) -> SourceResult

    def probe(self):
        if self.fetch_hook is not None:
            return True, "平台已注入 fetch_hook"
        return False, self.reason

    def fetch(self, **params):
        if self.fetch_hook is None:
            raise NotImplementedError(
                "%s 自动取数未实现（%s）。\n"
                "  合法路径：导出 CSV 后使用 %s；\n"
                "  或由平台注入 registry.remote['%s'].fetch_hook。\n"
                "  本包不会伪造数据替代缺失来源。"
                % (self.capability, self.reason, self.alt_adapter.__name__, self.capability))
        return self.fetch_hook(**params)


# ================================================================ 注册表
LOCAL = {
    "rep.flow.wangqian.read": WangqianAdapter,
    "rep.listing.price.read": ListingAdapter,
    "rep.rent.monthly.read": RentAdapter,
    "rep.land.parcel.read": LandParcelAdapter,
    "rep.stats.70city.read": Index70CityAdapter,
}

REMOTE = {
    "rep.flow.wangqian.read": RemoteFetch(
        "rep.flow.wangqian.read",
        "网签备案数据需住建/平台密钥授权，本包不持有密钥",
        WangqianAdapter),
    "rep.listing.price.read": RemoteFetch(
        "rep.listing.price.read",
        "中介平台数据需平台密钥授权，本包不持有密钥",
        ListingAdapter),
    "rep.rent.monthly.read": RemoteFetch(
        "rep.rent.monthly.read",
        "租赁平台数据需平台密钥授权，本包不持有密钥",
        RentAdapter),
    "rep.land.parcel.read": RemoteFetch(
        "rep.land.parcel.read",
        "土地出让数据需平台密钥授权，本包不持有密钥",
        LandParcelAdapter),
    "rep.stats.70city.read": RemoteFetch(
        "rep.stats.70city.read",
        "国家统计局公开数据：本包未内置抓取器（避免依赖网络与反爬波动），"
        "请导出为 CSV 后使用 Index70CityAdapter",
        Index70CityAdapter),
}


def load_contract(path=None):
    path = path or CONTRACT
    if not os.path.isfile(path):
        return []
    hdr, rows = read_csv(path)
    return rows


def coverage_table():
    """逐行对照：契约声明 vs 本包实现。"""
    rows = load_contract()
    out = []
    for r in rows:
        cap = (r.get("capability") or "").strip()
        local = LOCAL.get(cap)
        remote = REMOTE.get(cap)
        out.append({
            "capability": cap,
            "declared_caliber": (r.get("caliber") or "").strip(),
            "declared_unit": (r.get("unit") or "").strip(),
            "declared_auth": (r.get("auth") or "").strip(),
            "local_adapter": local.__name__ if local else None,
            "local_status": local.status if local else Status.UNIMPLEMENTED,
            "remote_status": remote.status if remote else Status.UNIMPLEMENTED,
            "notes": (r.get("notes") or "").strip(),
        })
    return out


def probe_all():
    """探测全部声明数据源在当前环境下的可用性。"""
    out = []
    for r in load_contract():
        cap = (r.get("capability") or "").strip()
        local = LOCAL.get(cap)
        remote = REMOTE.get(cap)
        if local:
            gen = local(os.path.join(FIXTURES, _fixture_name(local)))
            ok, why = gen.probe()
            out.append((cap, Status.PARTIAL, ok, "本地 CSV 路径：" + why))
        if remote:
            ok2, why2 = remote.probe()
            out.append((cap, Status.NEEDS_KEY, ok2, "自动取数：" + why2))
    return out


def _fixture_name(adapter_cls):
    return {
        WangqianAdapter: "wangqian_demo.csv",
        ListingAdapter: "listing_demo.csv",
        RentAdapter: "rent_demo.csv",
        LandParcelAdapter: "land_demo.csv",
        Index70CityAdapter: "index70_demo.csv",
    }.get(adapter_cls, "unknown.csv")


# ================================================================ 交叉校验
def crosscheck(listing_result, index_result=None, local_yoy=None):
    """
    两源交叉校验（对应 rep-002 的 `rep.crosscheck` 能力）。

    纪律：**冲突时并列呈现，不静默择优**。返回结构里没有任何"选一个"的字段，
    只有两边的值、差值与归因提示——这是刻意的。
    """
    out = {"sources": [], "conflicts": []}
    if listing_result is not None and listing_result.ok():
        med = None
        for s in listing_result.rows:
            pass
        vals = [r.get("unit_price") for r in listing_result.rows if r.get("unit_price")]
        vals = sorted(vals)
        if vals:
            med = vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2.0
        out["sources"].append({
            "caliber": listing_result.caliber, "metric": "unit_price_median", "value": med,
            "unit": listing_result.unit, "as_of": listing_result.as_of})
        plat = {}
        for r in listing_result.rows:
            plat.setdefault((r.get("platform") or "未标注"), []).append(r.get("unit_price"))
        if len(plat) >= 2:
            meds = {}
            for p, vv in plat.items():
                vv = sorted([x for x in vv if x])
                meds[p] = vv[len(vv) // 2] if vv else None
            out["sources"].append({"caliber": "平台内中位", "metric": "by_platform", "value": meds})
            valid = [v for v in meds.values() if v]
            if valid and min(valid) > 0:
                spread = (max(valid) - min(valid)) / min(valid)
                if spread > 0.03:
                    out["conflicts"].append({
                        "type": "platform_spread", "magnitude_pct": spread * 100,
                        "detail": meds,
                        "action": "并列呈现两平台口径，不静默择优（本包纪律）"})
    if index_result is not None and index_result.ok():
        chk = index_result.series.get("cross_check")
        if chk and not chk.get("direction_match"):
            out["conflicts"].append({
                "type": "official_direction_conflict",
                "detail": chk,
                "action": "官方环比链式与同比反推方向相反，须并列呈现并说明原因"})
        out["sources"].append({
            "caliber": index_result.caliber, "metric": "official_index",
            "value": index_result.series.get("official_level") or index_result.series.get("mom_chained_level"),
            "as_of": index_result.as_of})
    if local_yoy is not None:
        out["local_yoy"] = local_yoy
    return out


# ================================================================ CLI
def _fmt_table(rows, cols, aligns):
    widths = [max(len(str(c)), *(len(str(r.get(c, ""))) for r in rows)) if rows else len(str(c)) for c in cols]
    line = "  ".join(str(c).ljust(w) for c, w in zip(cols, widths))
    sep = "  ".join("-" * w for w in widths)
    out = [line, sep]
    for r in rows:
        cells = []
        for c, w in zip(cols, widths):
            v = str(r.get(c, ""))
            cells.append(v.rjust(w) if aligns.get(c) == ">" else v.ljust(w))
        out.append("  ".join(cells))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="数据适配器注册表")
    ap.add_argument("--probe", action="store_true", help="附带可用性探测")
    ap.add_argument("--selftest", action="store_true", help="用演示数据跑通全部本地适配器")
    ap.add_argument("--contract", default=CONTRACT)
    args = ap.parse_args()

    print("=" * 78)
    print("数据契约实现对照（capability-contract.csv vs 本包实现）")
    print("=" * 78)
    tbl = coverage_table()
    rows = []
    for t in tbl:
        rows.append({
            "能力": t["capability"],
            "本地适配器": t["local_adapter"] or "—",
            "本地状态": t["local_status"],
            "自动取数": t["remote_status"],
            "契约鉴权": t["declared_auth"],
        })
    print(_fmt_table(rows, ["能力", "本地适配器", "本地状态", "自动取数", "契约鉴权"], {}))
    print()
    print("说明：")
    print("  · implemented  = 本地可完整运行（零依赖、无需密钥）")
    print("  · partial      = 本地 CSV 路径已实现；自动取数需平台密钥")
    print("  · needs_key    = 仅契约声明，自动取数未实现（本包不持有密钥、不伪造数据）")
    print("  ⚠ 「partial」的含义是：**拿到数据后能标准化进入方法链**，")
    print("     不是「能自动取数」。任何报告不得把 partial 写成「已接入数据源」。")

    if args.probe:
        print()
        print("=" * 78)
        print("可用性探测（不取数，只判断当前环境是否可用）")
        print("=" * 78)
        for cap, st, ok, why in probe_all():
            print("  [%s] %s" % ("可用" if ok else "不可用", cap))
            print("        %s（%s）" % (why, st))

    if args.selftest:
        print()
        print("=" * 78)
        print("演示数据自检（fixtures/ 全部为**合成演示数据**，非真实市场数据）")
        print("=" * 78)
        cases = [
            (WangqianAdapter, "wangqian_demo.csv", {}),
            (ListingAdapter, "listing_demo.csv", {}),
            (RentAdapter, "rent_demo.csv", {}),
            (LandParcelAdapter, "land_demo.csv", {}),
            (Index70CityAdapter, "index70_demo.csv", {}),
        ]
        fail = 0
        for cls, fn, params in cases:
            p = os.path.join(FIXTURES, fn)
            try:
                res = cls(p).fetch(**params)
                print()
                print(res.summary())
            except Exception as e:
                fail += 1
                print("\n✗ %s 失败：%s" % (cls.__name__, e))
        print()
        print("自检结果：%d/%d 通过" % (len(cases) - fail, len(cases)))
        return 0 if fail == 0 else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
