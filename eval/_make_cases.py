#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成 eval/cases/practice_institution.json —— 补齐「实务方法与制度」能力族的案例。

背景：eval/ 案例集以**逐案例单文件**形式落盘（`eval-*.json`，共 30 例），已覆盖
hedonic / 价格指数 / 空间计量 / 机器学习 / 租金收益法 / 比较法 / 成本法 /
中国制度适配 / 数据口径核验九个能力族。

**本脚本只补一件事**：`run_eval.py` 的 `MIN_COVERAGE` 要求 `rep.caprate.calibrate ≥ 1`，
而上述 30 例未覆盖该能力（Cap Rate 标定是本包为回应评审 P2-1「中国缺乏透明 cap rate 序列」
专门新增的能力，必须有对应评测案例），故单独补齐 3 例。

rubric 的 `check` 字段必须取自 `run_eval.py` 的自动判定分支
（mustMention / mustNotClaim / expectedRange / expectedCaliber），
否则该维度会被判为「需人工」——这不是缺陷，而是刻意区分"可自动判定"与"须人工评分"。

⚠️ 全部案例的数值均为合成/演示数据，仅用于检验专家的**方法与纪律**，不代表真实市场。

重新生成：python3 eval/_make_cases.py
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "cases", "caprate_calibration.json")

SYNTH = ("合成/演示数据（由 benchmark/make_benchmark.py 与 eval/_make_cases.py 构造），"
         "非真实市场数据，禁止引用为行情")
SYNTH_DEMO = ("合成演示数据（skills/rent-income/scripts/matched_pr_demo.csv，固定种子生成），"
              "非真实市场数据")

_LEVELS = {
    "mustMention": [
        "1分：完全遗漏关键要点",
        "2分：覆盖不超过 1/3 要点",
        "3分：覆盖约半数要点",
        "4分：覆盖至少 2/3 要点",
        "5分：覆盖全部要点并展开说明",
    ],
    "mustNotClaim": [
        "1分：出现被禁止的说法（踩中反模式）",
        "2分：部分禁区未规避",
        "3分：基本规避但表述含糊",
        "4分：明确规避并说明原因",
        "5分：明确规避且主动提示风险与不确定性",
    ],
    "expectedRange": [
        "1分：未给数量结论或结论落在区间外",
        "2分：结论接近边界且未说明依据",
        "3分：结论落在区间内但依据不完整",
        "4分：结论落入区间且给出计算依据",
        "5分：结论落入区间 + 依据完整 + 给出敏感性",
    ],
    "expectedCaliber": [
        "1分：未声明任何口径或来源，数字不可追溯",
        "2分：提及口径但缺来源或截至期",
        "3分：口径与来源齐全、可复现",
        "4分：多源交叉验证且一致",
        "5分：多源 + 口径折算不确定性进入区间合成",
    ],
}

D1, D2, D3, D4 = "关键方法要点覆盖", "禁区与反模式规避", "数据口径与来源声明", "数量结论与区间"


def dim(name, weight, check):
    return {"dimension": name, "weight": weight, "manual": False, "check": check,
            "levels": _LEVELS[check]}


def manual_dim(name, weight, note):
    return {"dimension": name, "weight": weight, "manual": True, "check": None,
            "levels": ["1分：%s未体现" % note, "2分：提及但空洞",
                       "3分：有实质内容", "4分：具体且有依据", "5分：具体、有依据且主动指出局限"]}


def case(cid, cap, question, input_data, must, must_not, rng, caliber, prov=SYNTH):
    return {
        "id": cid,
        "capability": cap,
        "question": question,
        "inputData": input_data,
        "expectedAnswer": {
            "mustMention": must,
            "mustNotClaim": must_not,
            "expectedRange": rng,
            "expectedCaliber": caliber,
        },
        "rubric": [
            dim(D1, 0.30, "mustMention"),
            dim(D2, 0.25, "mustNotClaim"),
            dim(D3, 0.20, "expectedCaliber"),
            dim(D4, 0.15, "expectedRange"),
            manual_dim("表达与结构", 0.10, "结论先行与结构"),
        ],
        "dataProvenance": prov,
    }


CASES = [
    case("eval-caprate-calib-001", "rep.caprate.calibrate",
         "请给出北京写字楼的 cap rate。",
         {"诉求": "北京写字楼 cap rate", "数据条件": "无公开连续序列"},
         ["中国无公开连续的分城市分类型 cap rate 序列，不能给引用值",
          "只能给推导值：匹配价格租金比倒数 × (1−运营成本率)，并标「测算/估算」",
          "须给出运营成本率取值与依据（不得默认引用内置示意区间而不标注）",
          "须说明样本量与离散度，并建议与用户成本法交叉验证"],
         ["给出貌似有权威来源的 cap rate 引用数字",
          "跨城市或跨物业类型套用同一个 cap rate",
          "把推导值写成引用值"],
         {"min": 0.02, "max": 0.06, "unit": "净资本化率"},
         "推导值口径（标注级：测算或估算）", SYNTH_DEMO),

    case("eval-caprate-calib-002", "rep.caprate.calibrate",
         "用提供的匹配样本标定不同城市 / 物业类型的 cap rate，并说明推导方法。",
         {"数据": "skills/rent-income/scripts/matched_pr_demo.csv（合成）",
          "字段": "城市 / 物业类型 / 面积 / 月租 / 单价",
          "已知真值": "演示市住宅 1.7%、对照市甲住宅 2.4%、演示市写字楼 4.0%、演示市商业 3.0%"},
         ["用匹配价格租金比倒数（先按城市与物业类型分段），而非混合样本直接平均",
          "净资本化率 = 毛资本化率 × (1−运营成本率)，且须标注 opex 取值来源",
          "样本量 <10 或变异系数过高须报警",
          "与已知真值对比，验证标定器的还原能力"],
         ["不分段而给全市单一 cap rate",
          "把推导值写成引用值",
          "用未扣运营成本的毛资本化率直接当净资本化率"],
         {"min": 0.015, "max": 0.045, "unit": "毛资本化率"},
         "合成演示数据 → 推导值（标注级：测算）", SYNTH_DEMO),

    case("eval-caprate-calib-003", "rep.caprate.calibrate",
         "观测到某小区价格租金比约 58 年、商贷利率 3.05%、折旧维护率 1.5%、持有税费率 0。"
         "请反推市场隐含的预期增值率，并说明这个数意味着什么。",
         {"P/R": "≈58 年", "i": "3.05%", "δ": "1.5%", "m": "0"},
         ["用户成本法反推：π_e = i(1−τ) + δ + m − 1/(P/R)",
          "结果约 2.2%/年（并给 i 与 δ 的敏感性）",
          "π_e 不可观测，须标「研判推断」",
          "把「收益率低所以投资价值弱」这种模糊判断，落到可检验的假设上，而非直接判泡沫"],
         ["把 π_e 当作可观测事实直接引用",
          "未说明 i 与 δ 的取值来源",
          "直接得出「存在泡沫」的确定结论"],
         {"min": 0.0, "max": 0.06, "unit": "π_e（年化）"},
         "反推值口径（标注级：研判推断）"),
]


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({
            "note": "Cap Rate 标定能力的评测案例（补齐 run_eval.MIN_COVERAGE）。全部数值为合成演示数据。",
            "cases": CASES,
        }, f, ensure_ascii=False, indent=2)
    print("已写出 %s（%d 例）" % (os.path.relpath(OUT, HERE).replace("\\", "/"), len(CASES)))
    caps = {}
    for c in CASES:
        caps[c["capability"]] = caps.get(c["capability"], 0) + 1
    for k, v in sorted(caps.items()):
        print("   %-26s %d" % (k, v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
