#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
适配器回归测试（adapters/local_csv.py）。

已修复缺陷回归：曾把全部 standard_fields 一律数值化，导致「平台」「小区」「来源」
「出让条件」等字符串字段被 to_float 转成 None，进而 _valid 全判假、样本被清空。
修复后仅 numeric_fields 中的字段被数值化，字符串字段保持原值。
→ 本用例用中文表头 CSV 喂给 ListingAdapter / RentAdapter / WangqianAdapter，
  断言：① 能正确解析中文表头；② 字符串字段不被数值化污染（保持原字符串、非 None）；
        ③ 数值字段正确解析为 float。
"""

import os
import sys
import tempfile
import unittest

# 与仓库内各模块风格一致：用 sys.path 手动插入脚本目录（零外部依赖）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _c in (ROOT, os.path.join(ROOT, "scripts"), os.path.join(ROOT, "adapters"),
          os.path.join(ROOT, "skills", "price-index", "scripts"),
          os.path.join(ROOT, "skills", "spatial-ml-valuation", "scripts"),
          os.path.join(ROOT, "skills", "rent-income", "scripts")):
    if _c not in sys.path:
        sys.path.insert(0, _c)

import local_csv
from local_csv import ListingAdapter, RentAdapter, WangqianAdapter


def _write_csv(header, data_rows):
    fd, path = tempfile.mkstemp(suffix=".csv", prefix="adapter_test_")
    os.close(fd)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(",".join(header) + "\n")
        for r in data_rows:
            f.write(",".join(str(v) for v in r) + "\n")
    return path


class TestListingAdapter(unittest.TestCase):
    def test_chinese_headers_and_string_fields_preserved(self):
        path = _write_csv(
            ["平台", "小区", "户型", "面积㎡", "楼层", "朝向", "建成年份", "总价万元", "元/㎡", "房源编号"],
            [["贝壳", "幸福里", "三室一厅", 90, 12, "南", 2010, 270, 30000, "L0001"],
             ["链家", "翠湖天地", "两室", 75, 8, "北", 2005, 210, 28000, "L0002"]],
        )
        try:
            res = ListingAdapter(path).fetch()
            self.assertTrue(res.ok(), "ListingAdapter 未解析到有效样本（疑似字符串字段被污染清空）")
            r0 = res.rows[0]
            # 字符串字段保持原值（未被数值化污染为 None）
            self.assertEqual(r0["platform"], "贝壳", "platform 字符串被数值化污染")
            self.assertEqual(r0["community"], "幸福里", "community 字符串被数值化污染")
            self.assertEqual(r0["orientation"], "南", "orientation 字符串被数值化污染")
            self.assertEqual(r0["listing_id"], "L0001", "listing_id 字符串被数值化污染")
            # 数值字段正确解析
            self.assertAlmostEqual(r0["unit_price"], 30000.0, places=3)
            self.assertAlmostEqual(r0["area"], 90.0, places=3)
        finally:
            os.remove(path)


class TestRentAdapter(unittest.TestCase):
    def test_chinese_headers_and_string_fields_preserved(self):
        path = _write_csv(
            ["期", "小区", "户型", "面积㎡", "月租元", "楼层", "来源"],
            [["2023H1", "阳光花园", "两室", 80, 5000, 6, "链家"],
             ["2023H2", "橡树湾", "三室", 110, 7200, 15, "我爱我家"]],
        )
        try:
            res = RentAdapter(path).fetch()
            self.assertTrue(res.ok(), "RentAdapter 未解析到有效样本（疑似字符串字段被污染清空）")
            r0 = res.rows[0]
            self.assertEqual(r0["source"], "链家", "source 字符串被数值化污染")
            self.assertEqual(r0["community"], "阳光花园", "community 字符串被数值化污染")
            self.assertEqual(r0["layout"], "两室", "layout 字符串被数值化污染")
            self.assertAlmostEqual(r0["rent_monthly"], 5000.0, places=3)
        finally:
            os.remove(path)


class TestWangqianAdapter(unittest.TestCase):
    def test_chinese_headers_and_string_fields_preserved(self):
        path = _write_csv(
            ["期", "城市", "区县", "小区", "面积㎡", "总价万元", "元/㎡"],
            [["2024-03", "北京", "朝阳区", "锦绣园", 95, 285, 30000],
             ["2024-03", "北京", "海淀区", "百合园", 88, 264, 30000]],
        )
        try:
            res = WangqianAdapter(path).fetch()
            self.assertTrue(res.ok(), "WangqianAdapter 未解析到有效样本（疑似字符串字段被污染清空）")
            r0 = res.rows[0]
            self.assertEqual(r0["city"], "北京", "city 字符串被数值化污染")
            self.assertEqual(r0["district"], "朝阳区", "district 字符串被数值化污染")
            self.assertEqual(r0["community"], "锦绣园", "community 字符串被数值化污染")
            self.assertAlmostEqual(r0["unit_price"], 30000.0, places=3)
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
