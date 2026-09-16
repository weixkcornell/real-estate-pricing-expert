# eval/ · 房地产定价专家包评测套件

> 对应外部评审 **P0-1 后半**与 **P3-3**：把 `experts/*.json` 里声明的 `proficiency`
> （hedonic 0.92 / 指数 0.90 / 空间 0.88 / ML 0.85 / 租金 0.90 …）从「先验自评、未经标定」
> 变为**可回归标定的实测值**。本目录提供 30 个带标准答案的定价案例 + 评分 rubric +
> 零依赖运行器，让专家能力可被 CI 回归测试。

## 一、本套件解决什么

`experts/rep-001.json`、`rep-002.json` 的 `proficiency` 在 `proficiencyDisclosure.status`
中已声明为 `"prior"`（先验自评）。本套件给出**标定依据**：

- 每个能力一组「带标准答案 + 陷阱」的定价案例；
- 每个案例一组 rubric 评分维度（部分可在文本上客观自动判定，部分必须人工）；
- `run_eval.py` 自动跑分，输出逐案例得分、按能力平均分、建议的 `proficiency`。

标定完成后，把 `proficiencyStatus` 由 `prior` 改为 `calibrated`，并把实测指标回填
`proficiencyDisclosure`。

## 二、目录结构

```
eval/
├── README.md            # 本文件
├── run_eval.py          # 零依赖评测运行器（纯标准库）
├── answers-example.json # 回答格式示例（非必须，演示用）
└── cases/               # 30 个案例，每个一个 JSON 文件
    ├── eval-hedonic-001.json   ... rep.hedonic ×5
    ├── eval-priceindex-001.json ... rep.priceindex ×4
    ├── eval-spatial-001.json   ... rep.spatial ×3
    ├── eval-ml-001.json        ... rep.ml.valuation ×3
    ├── eval-rent-001.json      ... rep.caprate.calibrate / rep.rent.income ×4
    ├── eval-comparison-001.json ... rep.comparison ×3
    ├── eval-cost-001.json      ... rep.cost.residual ×2
    ├── eval-china-001.json     ... rep.china.adapt ×3
    └── eval-datacheck-001.json ... rep.datacheck（rep-002）×3
```

**案例 schema**（每个 `cases/*.json`）：

```json
{
  "id": "eval-hedonic-001",
  "capability": "rep.hedonic",
  "question": "给专家的提问（中文，贴近真实业务）",
  "inputData": { "说明数据是什么、在哪、口径是什么" },
  "expectedAnswer": {
    "mustMention": ["必须提到的要点（自动判定命中）"],
    "mustNotClaim": ["禁止出现的说法（如把自研梯度提升称为 XGBoost）"],
    "expectedRange": { "min": 数字, "max": 数字, "unit": "元/㎡" },
    "expectedCaliber": "应声明的口径（子串命中）"
  },
  "rubric": [
    {"dimension": "...", "weight": 0.3, "manual": false, "check": "mustMention",
     "levels": ["1分...","2分...","3分...","4分...","5分..."]},
    {"dimension": "...", "weight": 0.2, "manual": true,
     "levels": ["1分...","2分...","3分...","4分...","5分..."]}
  ],
  "dataProvenance": "数据来源；若为合成数据必须写明「合成演示数据」"
}
```

`rubric` 维度约定：

- `check`（自动判定类型，可客观判定）：`mustMention` / `mustNotClaim` / `expectedRange` /
  `expectedCaliber`。运行器据此在文本上客观打分（1–5），**不伪造分**。
- `manual: true`（须人工）：区间与不确定性披露、解释深度与证据链等**不能在文本上客观判定**
  的维度，运行器一律标 `人工评分`，等人工补齐。
- 每个案例 `rubric` 权重和必须 = 1（容差 1e-3）。

## 三、如何运行

```bash
# 1) CI 校验模式（必须能跑通，不依赖任何专家回答）
python3 eval/run_eval.py --no-answers

# 2) 评测模式：提供专家回答（形如 {"eval-hedonic-001": "专家回答文本", ...}）
python3 eval/run_eval.py --answers eval/answers-example.json
python3 eval/run_eval.py --answers some_answers_dir/      # 目录下 *.json 合并
```

`--no-answers` 只做**案例集自身完整性校验**：schema 合法性、能力覆盖下限、rubric 权重和=1、
ID 唯一性、合成数据显著标注。它**不评测专家表现**，可作为提交前的 CI 检查。

## 四、评分与 proficiency 标定规则

### 4.1 单案例自动得分

对提供回答的案例，运行器对每个 rubric 维度：

- 可客观判定维度（`check` ∈ {mustMention, mustNotClaim, expectedRange, expectedCaliber}）：
  - `mustMention`：按命中要点比例给 1–5 分（全中=5，全漏=1）；
  - `mustNotClaim`：出现任一禁止说法 → 1 分，否则 5 分；
  - `expectedRange`：回答中任一数字落在区间 `[min,max]` → 5 分，否则 1 分；
  - `expectedCaliber`：应声明口径子串出现在回答中 → 5 分，否则 1 分。
- `manual: true` 维度：**不自动打分**（None），标记「人工评分」。

单案例自动得分 = 仅对**已判定维度**按权重加权后，再按已判定权重**重新归一化**：

```
case_auto = (Σ_{已判定} w_i · s_i) / (Σ_{已判定} w_i)      # 结果 0–5
```

（重新归一化是因为 manual 维度的权重在该案例未被使用，不应拉低自动分。）

### 4.2 单能力 proficiency（建议值）

```
proficiency_cap = mean(case_auto 属于该能力) / 5           # 结果 0–1
```

注意：`case_auto` 只含可客观判定维度，因此这是**自动代理下界**——
人工补齐 manual 维度后，分数可能上调。

### 4.3 完整标定（人工补齐后）

当人工对 manual 维度打分后，用**全维度、不重新归一**的公式：

```
proficiency = (Σ_{全部维度} w_i · s_i) / 5                # 含 manual 维度
```

标定结果回写：

1. `experts/rep-00X.json` 对应能力的 `proficiency` 改为实测值；
2. `proficiencyDisclosure.status` 由 `"prior"` 改为 `"calibrated"`，并把实测指标
   （MAE/MAPE/ rubric 均分）填入 `statement` 与 `calibrationPlan`。

### 4.4 回归纪律

- 每次方法库或知识底座改动后，重跑本套件；`proficiency` 若下滑超过阈值（建议 0.05），
  触发差异归因，不得静默保留旧值。
- `mustNotClaim` 维度一旦命中（得 1 分），该案例判为「踩中本包明令禁止的反模式」，
  应在报告中单列，优先修复。

## 五、⚠ 合成/演示数据声明（重要）

**本套件 `cases/` 下所有案例的房产数据均为 `合成演示数据`，非真实市场数据。**
这一声明写在每个案例的 `dataProvenance` 字段（含「合成演示数据」字样），运行器
`--no-answers` 会校验其存在。

设计原则（与包内「无数据底座、无口径、无来源，不下结论」一致）：

- 案例优先要求专家演示**方法流程与纪律**（拒绝、折算、并列、标注），而非赌一个真实价格；
- 案例内若给出具体数字（如单价、折扣率、cap rate），均为合成示例，仅用于演示口径与区间，
  **绝不可被引用为真实市场行情**；
- 真实标定应替换为 `adapters/fixtures/` 之外的真实授权数据后再回写 `proficiency`。
