---
name: rent-income
description: '租金与收益法定价工艺：对住宅与商业地产分别完成租金定价、资本化率（Cap Rate）、折现现金流（DCF）与价格租金比，打通租金与交易定价。Triggers on "租金定价", "租金指数", "cap rate", "资本化率", "收益法", "DCF", "折现租金", "价格租金比", "租售比"'
version: 0.1.0
user-invocable: true
argument-hint: "[建模/复核] 【城市·物业类型·租金/售价】"
license: MIT
metadata:
  short-description: 租金定价、Cap Rate 与收益法估值
---

# 租金与收益法定价（rent-income）

一句话总纲：**资产价值 = 未来租金现金流的资本化**——租金定价与交易定价必须打通，才能判断价格是否偏离基本面。本文件规定执行规则，不重复附件内容。

## §0 引用（先读）

- 权威附件：`references/rent-income-checklist.md`。
- 可执行代码：`scripts/rent_model.py`（hedonic 租金 / 分层时间虚拟租金指数 / **匹配价格租金比** / 用户成本法 / Cap Rate / DCF）；`scripts/rent_income.py`（收益率与 Cap Rate 计算器）；`scripts/rent_sample.csv`、`scripts/rent_panel.csv`。用法见 `<pack>/scripts/README.md`。
- 方法来源：Song, Wilhelmsson & Yang (2020, 北京租金指数)；Shanghai 价格租金比 (2021, RSUE)；Ghysels, Plazzi & Valkanov (2007, EFM)；Fisher et al. (1994, JRER)；France 租金动态 (2020)；France 大数据租金 (2022, PLOS ONE)；Chen (1996, Urban Studies)；Wu, Gyourko & Deng (2012/2016, RSUE)。

## §1 建模步骤（怎么做）

1. **租金定价（住宅）**：
   - 用 hedonic 租金模型（半对数）估计属性隐含租金价格；结构性属性 + 可达性（R-01）；
   - 构造租金指数用**分层时间虚拟变量**法，偏态强时用 Box-Cox（R-04）；
   - 租金空间依赖/异质用 SAR-GWR 处理（S-05）。
2. **商业地产（有净营运收入 NOI）**：
   - 收益法为主：`价值 = NOI / Cap Rate`，或 DCF 折现租金现金流；
   - Cap Rate 按城市、物业类型分别标定，**禁止跨类型/跨地点套用统一值**（R-05）；
   - 折现租金模型可替代聚合层 hedonic（R-03）。
3. **价格租金比 / 租售比**：
   - 对同一批物业分别估 hedonic 价格模型与 hedonic 租金模型，要求**属性集一致**，互相插补，构造匹配的 price-rent ratio（R-02）；
   - 用**用户成本法**检验价格是否偏离基本面（C-03）。
4. **判断与输出**：给出租金水平/指数、Cap Rate、价值区间，并说明租金与价格是否出现背离。

## §2 数据要求（怎么用数）

- **四级标注**：测算 / 估算 / 研判推断 / 待补。
- **溯源格式**：`（来源，机构，截至 年-月；口径：租金/网签/挂牌）`。
- **口径纪律**：挂牌租金 vs 成交租金须区分并说明折算；商业地产 NOI 须说明计算口径。
- **价格与租金同源属性集**：建模价格租金比时，两套 hedonic 必须使用相同属性集，否则比值不可比（R-02）。
- **多源冲突**：显式说明，不静默择优。

## §3 质量门禁（怎么过关）

- 与 `quality-policies/rep-valuation-quality.json` 对应：数字一致、禁例 token 0 命中、无占位符。
- 交付前过 `references/rent-income-checklist.md`；公网发布前确认不涉密。
