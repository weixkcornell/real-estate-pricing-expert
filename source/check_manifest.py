#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
溯源一致性校验器 —— 防止"幽灵引用"。

背景（本次整改中发现的真实问题）：
    包内此前有 5 处引用了**未登记在 SOURCE-MANIFEST.json** 的文献标签
    （Istanbul 2025 / Kain & Quigley 1970 / Helbich et al. 2014 / France 2020 等）。
    这类引用看起来增加了权威性，实际上无法核验，与本包"无授权来源不入库"
    "编造比不回答更严重"的纪律直接冲突，而且**静态检查拦不住、单看正文也看不出来**。
    因此需要一个可自动执行的校验器，把纪律变成程序。

检查项：
  1. **mat-id 引用有效性**：包内所有 `mat-XXX` 必须存在于清单；
  2. **作者-年份引用可解析**：`姓 (YYYY)` / `姓 et al. (YYYY)` 形式必须能对应到清单条目；
  3. **知识块落位**：每份核心文献（mat-001~mat-023）的 fileRef 指向的文件必须存在，
     且文件内确实出现该 mat-id（即"知识块真的写了这篇"）；
  4. **旧式伪标签**：形如"前缀:字母-序号"、无法解析到清单的标签一律报错；
  5. **反向覆盖**：清单中的文献是否都在 index.json 的 paperToBlock 里登记。

用法：
    python3 source/check_manifest.py            # 全包校验
    python3 source/check_manifest.py --json     # 机读输出（CI 用）
退出码：0 通过；1 存在错误（warning 不阻断）。
"""

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

MANIFEST = os.path.join(HERE, "SOURCE-MANIFEST.json")
INDEX = os.path.join(ROOT, "knowledge", "experts", "rep-001", "index.json")

SCAN_EXT = (".md", ".json", ".py", ".csv", ".txt")
SKIP_DIRS = {".git", "__pycache__", "fixtures"}
# 以下目录中的"年份/作者"字样属于示例或用户数据，不视为引用
SKIP_PATHS = ("knowledge/experts/rep-001/blocks/06-china-institution.md",)

# 明确已知的非文献性年份表述（避免误报）
# "地点/对象 + 年份"式描述符：指的是已登记材料（如 mat-015 爱尔兰 AVM），
# 不属于幽灵引用，但规范写法应同时给出 mat-id。
YEAR_ALLOW = {
    "AVM 2022", "Cracow 2019", "Shanghai 2021",
}

# 姓 → 清单是否收录（用于把"作者-年份"引用解析到 mat-id）
NAME_HINTS = {
    "Rosen": "mat-001", "Lancaster": "mat-002", "Malpezzi": "mat-003",
    "Bailey": "mat-004", "Muth": "mat-004", "Nourse": "mat-004",
    "Case": "mat-005", "Shiller": "mat-005", "Pollakowski": "mat-006", "Wachter": "mat-006",
    "Chen": "mat-007", "Harding": "mat-007", "Oust": "mat-008", "Hansen": "mat-008",
    "Calainho": "mat-009", "Minne": "mat-009", "Francke": "mat-009",
    "Brunsdon": "mat-010", "Fotheringham": "mat-010", "Charlton": "mat-010",
    "Bitter": "mat-011", "Mulligan": "mat-011", "Pace": "mat-012", "LeSage": "mat-012",
    "Ho": "mat-014", "Kok": "mat-016", "Koponen": "mat-016",
    "Ghysels": "mat-017", "Plazzi": "mat-017", "Valkanov": "mat-017",
    "Fisher": "mat-018", "Miles": "mat-018", "Webb": "mat-018",
    "Song": "mat-020", "Wilhelmsson": "mat-020", "Wu": "mat-023", "Gyourko": "mat-023",
    "Deng": "mat-023", "Quigley": "mat-026",
    "Breiman": "mat-024", "Himmelberg": "mat-025", "Mayer": "mat-025", "Sinai": "mat-025",
}


def iter_files(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(SCAN_EXT):
                yield os.path.join(dirpath, fn)


def rel(p):
    return os.path.relpath(p, ROOT).replace("\\", "/")


def load_manifest():
    d = json.load(open(MANIFEST, encoding="utf-8"))
    return d, {m["id"]: m for m in d["materials"]}


def check():
    manifest, mats = load_manifest()
    errors, warnings, stats = [], [], {"files": 0, "matRefs": 0, "authorYear": 0}

    # ---------- 3. 知识块落位 ----------
    for mid, m in sorted(mats.items()):
        fr = m.get("fileRef", "")
        if not fr:
            errors.append("%s：fileRef 为空" % mid)
            continue
        fp = os.path.join(ROOT, fr)
        if not os.path.isfile(fp):
            errors.append("%s：fileRef 指向的文件不存在 → %s" % (mid, fr))
            continue
        if int(mid.split("-")[1]) <= 23:
            body = open(fp, encoding="utf-8", errors="replace").read()
            if mid not in body:
                errors.append("%s：fileRef 所指知识块内**未出现该 mat-id**（知识块未真正覆盖此文）" % mid)

    # ---------- 5. 反向覆盖 ----------
    if os.path.isfile(INDEX):
        idx = json.load(open(INDEX, encoding="utf-8"))
        p2b = idx.get("paperToBlock", {})
        for mid in mats:
            if int(mid.split("-")[1]) <= 23 and mid not in p2b:
                errors.append("%s：未登记在 knowledge/experts/rep-001/index.json 的 paperToBlock" % mid)
    else:
        errors.append("index.json 缺失，无法做反向覆盖校验")

    # ---------- 1 & 2 & 4. 正文扫描 ----------
    mat_pat = re.compile(r"\bmat-(\d{3})\b")
    paper_label = re.compile("\\bpapers?:" + "[A-Za-z]{1,3}-\\d{1,2}")
    author_year = re.compile(r"\b([A-Z][a-zA-Z\u00C0-\u024F]{2,15})(?:\s+et\s+al\.?)?(?:\s*[,&]\s*[A-Z][a-zA-Z]{2,15})*\s*\(?((?:19|20)\d{2})")
    for path in iter_files(ROOT):
        r = rel(path)
        text = open(path, encoding="utf-8", errors="replace").read()
        stats["files"] += 1

        for ln, line in enumerate(text.splitlines(), 1):
            # 1. mat-id
            for mid in mat_pat.findall(line):
                stats["matRefs"] += 1
                full = "mat-" + mid
                if full not in mats:
                    errors.append("%s:%d 引用了不存在的 %s" % (r, ln, full))
            # 4. 旧式伪标签
            for lab in paper_label.findall(line):
                errors.append("%s:%d 出现无法解析的伪文献标签「%s」——须改用 mat-id" % (r, ln, lab))
            # 2. 作者-年份（排除本校验脚本自身与源清单）
            if r.startswith("source/"):
                continue
            for m2 in author_year.finditer(line):
                name, year = m2.group(1), m2.group(2)
                token = "%s %s" % (name, year)
                if token in YEAR_ALLOW:
                    warnings.append("%s:%d 「%s」为已知非文献表述（应改写为 mat-id）" % (r, ln, token))
                    continue
                if name in NAME_HINTS:
                    stats["authorYear"] += 1
                    continue
                # 只对"看起来像文献引用"的形态报警：句中出现且带括号年份
                if "(" in line and any(k in line for k in ("来源", "方法", "见 ", "参见", "（", "(")):
                    if name[0].isupper() and name not in ("Table", "Figure", "Note", "Python", "JSON",
                                                          "China", "Chinese", "The", "This", "That"):
                        # 需人工判断的，降级为 warning（避免误报）
                        if re.search(r"\b%s\s*(et al\.)?\s*\(%s\)" % (re.escape(name), year), line):
                            warnings.append(
                                "%s:%d 疑似引用了未登记文献「%s %s」——如确为文献，须先在 "
                                "SOURCE-MANIFEST.json 登记；否则请改写为 mat-id"
                                % (r, ln, name, year))
    return errors, warnings, stats, mats


def main():
    ap = argparse.ArgumentParser(description="溯源一致性校验（防幽灵引用）")
    ap.add_argument("--json", action="store_true", help="机读输出")
    args = ap.parse_args()
    errors, warnings, stats, mats = check()

    if args.json:
        print(json.dumps({"errors": errors, "warnings": warnings, "stats": stats,
                          "materials": len(mats)}, ensure_ascii=False, indent=2))
        return 1 if errors else 0

    print("=" * 76)
    print("溯源一致性校验（SOURCE-MANIFEST.json ↔ 包内引用）")
    print("=" * 76)
    print("扫描文件 %d 个 | 清单材料 %d 份 | mat-id 引用 %d 处 | 已识别作者-年份引用 %d 处"
          % (stats["files"], len(mats), stats["matRefs"], stats["authorYear"]))
    print()
    if errors:
        print("✗ 错误 %d 项：" % len(errors))
        for e in errors:
            print("   · %s" % e)
    else:
        print("✓ 无错误：所有 mat-id 有效、知识块落位完整、无伪标签")
    if warnings:
        print()
        print("⚠ 提示 %d 项（不阻断，但建议处理）：" % len(warnings))
        for w in warnings[:40]:
            print("   · %s" % w)
        if len(warnings) > 40:
            print("   … 其余 %d 项省略" % (len(warnings) - 40))
    print()
    print("结论：%s" % ("FAIL" if errors else "PASS"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
