# 知识底座 · rep-001（置价 · 房地产定价模型专家）

> 本目录是专家的**知识 grounding 层**——让每一条能力声明（capability / proficiency）都能顺藤摸到方法原文要点，而不是只有 SKILL.md 的二手概括。
>
> **修订说明（v0.3.0）**：本目录此前只有一个"放置说明"占位文件，专家实际上是"无根"的——`experts/rep-001.json` 的 `evidenceRefs` 全部指向一个空目录（评审 P0-1）。现已将 `source/SOURCE-MANIFEST.json` 登记的 **23 篇文献全部整理为结构化知识块**。

---

## 一、目录结构

```
knowledge/experts/rep-001/
├── README.md                       # 本文件：知识底座总览与检索指引
├── index.json                      # 知识块索引（能力 → 知识块 → 论文 的可编程映射）
└── blocks/
    ├── 01-hedonic.md               # 特征价格族         mat-001/002/003/007
    ├── 02-price-index.md           # 价格指数族         mat-004/005/006/009
    ├── 03-spatial.md               # 空间计量族         mat-008/010/011/012/013
    ├── 04-machine-learning.md      # 机器学习估值族     mat-014/015/016
    ├── 05-rent-income.md           # 租金与收益法族     mat-017/018/019
    ├── 06-china-institution.md     # 中国制度与政策断点 mat-020/021/022/023
    └── 07-practice-methods.md      # 实务工艺（比较法/成本法/假设开发法）
```

**覆盖核对**：SOURCE-MANIFEST 的 23 篇文献全部落块，无遗漏（`index.json` 的 `coverage` 字段可编程校验）。mat-020 同时服务于租金指数工艺与市场分割分析，已在两处显式标注，避免重复计算或遗漏。

### 一处已修正的口径不一致
上一版本文件列出的材料清单中，**"Kain & Quigley (1970)"与"Istanbul ML (2025)"并未登记在 `SOURCE-MANIFEST.json`**，且未进入任何知识块。按本包"无授权来源不入库""材料与清单须一致"的纪律，此二者已移除。若后续确需纳入，须先在 SOURCE-MANIFEST 登记出处与授权状态。

---

## 二、每个知识块的标准结构

针对**每一篇文献**给出五要素（与评审 P0-1 的要求一致）：

| 要素 | 说明 |
|---|---|
| **模型设定** | 论文的核心方程与理论结构，含符号含义 |
| **关键方法与易错点** | 识别问题、估计要点、实务陷阱 |
| **数据要求** | 需要什么数据、样本量下限、口径约束 |
| **估计方法** | 具体算法步骤（如 Case-Shiller 三阶段） |
| **中国适配注意点** | 制度差异导致的方法调整 |

另有两类附加内容：
- **已修复的实现缺陷**：把本包迭代中暴露的真实缺陷写进对应方法块（SEM 维度错配、克里金假精度、GP 量纲失配、租金指数 Jacobian 口径），使知识块同时成为**回归测试的知识依据**；
- **能力矩阵**：如知识块 04 明确列出「已实现 / 近似 / 替代 / 未实现」，直接约束报告措辞不得夸大。

---

## 三、能力 → 知识块 映射（`evidenceRefs` 依据）

| 能力 id | 知识块 | 论文 | 代码 |
|---|---|---|---|
| `rep.hedonic` | `kb-hedonic` | mat-001/002/003 | `hedonic_model.py` |
| `rep.attr.decompose` | `kb-hedonic`、`kb-ml` | mat-003/007 | `hedonic_model.py`、`ml_valuation.py` |
| `rep.priceindex` | `kb-price-index` | mat-004/005/006/009 | `price_index.py` |
| `rep.spatial` | `kb-spatial` | mat-008/010/011/012/013 | `spatial_model.py` |
| `rep.ml.valuation` | `kb-ml` | mat-014/015/016 | `ml_valuation.py` |
| `rep.rent.income` | `kb-rent-income` | mat-017/018/019 | `rent_model.py` |
| `rep.caprate.calibrate` | `kb-rent-income` | mat-017/018/019 | `rent_model.py` |
| `rep.china.adapt` | `kb-china-institution` | mat-020/021/022/023 | 工艺，见块内四步操作路径 |
| `rep.comparison` | `kb-practice` | 实务标准 | `comparison_model.py` |
| `rep.cost.residual` | `kb-practice` | 实务标准 | `cost_residual.py` |

---

## 四、专家如何使用本知识底座

1. **回答需要方法细节的问题时**（如"GWR 带宽的 AICc 与 CV 冲突怎么办""Box-Cox 的 λ 该怎么定"）→ 先检索对应知识块，**引用其中的方程与判据**，而不是凭 SKILL.md 概括作答；
2. **输出报告时** → 凡涉及方法选择、假设设定、局限性说明，应能在知识块中找到依据，并体现该依据；
3. **遇到知识块未覆盖的问题** → 明确声明"超出本包知识底座范围"，**不得编造**（受禁例 token 约束）；
4. **措辞必须受能力矩阵约束** → 知识块 04 的矩阵是硬约束：自研梯度提升不得称 XGBoost，排列重要性不得称 SHAP。

---

## 五、与 SKILL.md 的分工

| 层 | 定位 | 面向 |
|---|---|---|
| `knowledge/experts/rep-001/blocks/` | **为什么这么做**——理论依据、方程、论文结论 | 专家的推理与溯源 |
| `skills/*/SKILL.md` | **怎么做**——作业步骤、验收清单、触发条件 | 专家的执行流程 |
| `skills/*/scripts/` | **可执行的实现** | 专家的计算 |
| `data-contracts/capability-contract.csv` | **数据从哪来** | 专家的输入边界 |

四者应保持一致：依据 → 流程 → 实现 → 数据。若冲突，**以知识块所引原文为准**，并须修订其余层。

---

## 六、溯源规则（沿用并强化）

1. 每份材料必须在 `source/SOURCE-MANIFEST.json` 登记（出处 + 授权状态）；
2. **无授权来源不入库**；
3. 专家的观点必须能溯源到本目录或 MANIFEST 材料——**编造专家观点比不回答更严重**；
4. 涉密材料可只入库摘要层，溯源材料分级存放；
5. 知识块的任何数值结论必须来自文献本身，**不得引入无来源数字**；
6. 新增方法必须同步三处：知识块（依据）+ SKILL.md（流程）+ scripts（实现）；
7. 修订文献要点时，须标注修订日期与依据来源。
