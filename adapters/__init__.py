#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据适配器层（adapters）。

提供 capability-contract 中各数据源的**可运行实现**，并如实标注可实现性边界：
    implemented  → 本地可完整运行
    partial      → 本地 CSV 路径已实现；自动取数需平台密钥
    needs_key    → 仅契约声明，自动取数未实现（本包不持有密钥、不伪造数据）

入口：
    python3 adapters/registry.py --probe --selftest
"""

from .base import AdapterError, Mark, SourceAdapter, SourceResult, Status, Unit, quality_report
from .index70 import Index70CityAdapter
from .local_csv import LandParcelAdapter, ListingAdapter, RentAdapter, WangqianAdapter

__all__ = [
    "AdapterError", "Mark", "SourceAdapter", "SourceResult", "Status", "Unit",
    "quality_report", "Index70CityAdapter", "LandParcelAdapter", "ListingAdapter",
    "RentAdapter", "WangqianAdapter",
]

__version__ = "0.1.0"
