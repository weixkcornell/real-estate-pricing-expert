# 房地产定价专家包（置价）

> 面向中国房地产市场的定价方法论专家包。方法底座提炼自国外房地产核心期刊关于**租金定价**与**交易定价**的研究，形成可执行的交付工艺（Skills），覆盖住宅与商业地产。

## 一、这个包解决什么问题

把「房地产定价」从经验判断，变成**口径清楚、方法可复现、结论带区间**的标准化作业：

1. 房产价值 = 结构 / 区位 / 邻里 / 周期四类属性的隐含价格之和（Hedonic）；
2. 价格指数 = 剥离质量变化与样本偏差后的纯价格变动（Repeat-Sales / Hybrid）；
3. 空间依赖与空间异质必须同时处理（SAR / GWR）；
4. 大规模估值用机器学习，但必须可解释（SHAP）且带不确定性；
5. 资产价值 = 租金现金流资本化（Cap Rate / DCF / 用户成本），租金与交易定价必须打通。

## 二、目录结构

```
real-estate-pricing-expert/
├── README.md                          # 本文件
├── SUBMISSION-CHECKLIST.md            # 提交物验收清单
├── TRIAL-RUN.md                       # 试运行记录（端到端样例）
├── LICENSE                            # 许可证
├── pack.json                          # 包清单（id / version / schemaVersion / 口径声明）
├── experts/
│   ├── rep-001.json                   # 置价 · 房地产定价模型专家（完整 Profile）
│   └── rep-002.json                   # 底座 · 数据口径核验员
├── knowledge/
│   └── experts/
│       └── rep-001/README.md          # 知识底座放置说明
├── scenarios/
│   └── rep-valuation.json             # 定价全流程场景（DAG）
├── output-templates/
│   └── rep-valuation.json             # 定价报告输出模板
├── quality-policies/
│   └── rep-valuation-quality.json     # 质量门禁（硬门 / 软门）
├── skills/
│   ├── hedonic-pricing/SKILL.md + references/ + scripts/   # 含 hedonic_model.py / value_demo.py / sample_data.csv
│   ├── price-index/SKILL.md + references/
│   ├── spatial-ml-valuation/SKILL.md + references/
│   └── rent-income/SKILL.md + references/ + scripts/       # 含 rent_income.py / rent_sample.csv
├── data-contracts/
│   └── capability-contract.csv        # 数据源能力契约表
└── source/
    └── SOURCE-MANIFEST.json           # 溯源清单
```

## 三、专家与 Skill

| 专家 | 定位 | 能力点 |
|---|---|---|
| **置价**（rep-001） | 房地产定价模型专家 | 特征价格、价格指数、空间计量、机器学习估值、租金与收益法 |
| **底座**（rep-002） | 数据口径核验员 | 数据底座表、口径冲突说明、缺口清单 |

| Skill | 用途 | 方法来源 |
|---|---|---|
| `hedonic-pricing` | 特征价格定价与属性分解 | Rosen (1974)、Malpezzi、中国县级 GWR (2011) |
| `price-index` | 重复销售 / 混合价格指数 | Bailey-Muth-Nourse (1963)、Case-Shiller (1989)、Calainho et al. (2024) |
| `spatial-ml-valuation` | 空间计量 + 机器学习 AVM | Brunsdon et al. (1996)、Bitter et al. (2007)、Irish AVM (2022) |
| `rent-income` | 租金定价、Cap Rate、价格租金比 | Ghysels et al. (2007)、Fisher et al. (1994)、Shanghai 租价比 (2021) |

## 四、场景（DAG）

`rep-valuation`：数据底座核验（rep-002）→ 视角 A 交易定价研判（rep-001）/ 视角 B 租金与收益法研判（rep-001）→ 融合成文（docs-coordinator）。

## 五、可执行代码（27 个方法，零依赖）

方法论不只是文档——本包附**纯 Python 标准库实现的方法库**，每条方法严格对应一篇论文：

| 模块 | 方法数 | 覆盖 |
|---|---|---|
| `scripts/core.py` | — | 共享数值核心（矩阵 / OLS / WLS / 指标 / 交叉验证） |
| `scripts/data_adapter.py` | — | 智见数据层适配（口径归一、单位换算、数据集构建、两源交叉校验） |
| `skills/hedonic-pricing/scripts/hedonic_model.py` | 5 | 基础 Hedonic、Box-Cox、时间虚拟指数、属性价格时变、空间扩展法 |
| `skills/price-index/scripts/price_index.py` | 4 | BMN 重复销售、Case-Shiller 三阶段 WLS、混合指数、ML 时外误差指数 |
| `skills/spatial-ml-valuation/scripts/spatial_model.py` | 6 | 空间权重、Moran's I、SAR、SEM、GWR、回归克里金 |
| `skills/spatial-ml-valuation/scripts/ml_valuation.py` | 6 | CART、随机森林、梯度提升、高斯过程 AVM（带区间）、排列重要性、部分依赖 |
| `skills/rent-income/scripts/rent_model.py` | 6 | hedonic 租金、分层时间虚拟租金指数、匹配价格租金比、用户成本法、Cap Rate、DCF |

**完整方法矩阵（论文 → 方法 → 公式 → 数据 → 代码）见 `scripts/README.md`。**

```bash
# 价格指数（BMN + Case-Shiller）
python3 skills/price-index/scripts/price_index.py --pairs skills/price-index/scripts/sample_pairs.csv --method all

# 空间计量（Moran / SAR / SEM / GWR / 克里金）
python3 skills/spatial-ml-valuation/scripts/spatial_model.py \
    --data skills/spatial-ml-valuation/scripts/sample_spatial.csv --y 单价 --x 面积 楼龄 --coord x y --method all

# 机器学习 AVM
python3 skills/spatial-ml-valuation/scripts/ml_valuation.py \
    --data skills/spatial-ml-valuation/scripts/sample_spatial.csv --y 单价 --x 面积 楼龄 x y --method all

# 租金指数 + 用户成本
python3 skills/rent-income/scripts/rent_model.py --data skills/rent-income/scripts/rent_panel.csv \
    --rent "月租(元)" --x "面积㎡" --time 期

# 智见数据层自检
python3 scripts/data_adapter.py
```

## 六、使用方法

1. 将本包放入专家平台的包目录；
2. 触发词：「房地产定价」「房价估值」「租金定价」「价格指数」「价格租金比」；
3. 首次运行先补 `data-contracts/capability-contract.csv` 的真实数据源与 `knowledge/experts/rep-001/` 的溯源材料。

## 七、免责声明

本包输出为方法论与量化建模参考，不构成投资建议、估值报告或法律意见。正式估值须由具备资质的估价机构出具。所有结论必须标注数据来源与口径。
