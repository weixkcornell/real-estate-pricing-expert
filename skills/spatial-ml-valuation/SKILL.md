---
name: spatial-ml-valuation
description: '空间计量与机器学习估值工艺：处理空间依赖与空间异质性，并构建可解释的大规模自动估值（AVM）。涵盖 SAR/SEM、GWR、随机森林、梯度提升、排列重要性归因与 conformal 区间。**注意：MGWR、邻接型空间权重、XGBoost 原版与 SHAP 本包均未实现**（见知识底座 blocks/04 的能力矩阵）。Triggers on "空间计量", "GWR", "地理加权", "AVM", "自动估值", "机器学习估值", "随机森林", "梯度提升"'
version: 0.1.0
user-invocable: true
argument-hint: "[建模/复核] 【城市·样本量·估值目标】"
license: MIT
metadata:
  short-description: 空间计量 + 机器学习房地产估值
---

# 空间计量与机器学习估值（spatial-ml-valuation）

一句话总纲：**空间依赖与空间异质必须同时处理；ML 提升精度但不能牺牲可解释与不确定性。**本文件规定执行规则，不重复附件内容。

## §0 引用（先读）

- 权威附件：`references/spatial-ml-checklist.md`。
- 可执行代码：`scripts/spatial_model.py`（空间权重 / Moran's I / SAR / SEM / GWR / 回归克里金）；`scripts/ml_valuation.py`（CART / 随机森林 / 梯度提升 / 高斯过程 AVM 带区间 / 排列重要性 / 部分依赖）；`scripts/sample_spatial.csv`。用法见 `<pack>/scripts/README.md`。
- 方法来源：mat-010 Brunsdon, Fotheringham & Charlton (1996)；mat-011 Bitter et al. (2007)；mat-012 Pace & LeSage (2004)；mat-013 租金 SAR-GWR (2019, IJGI)；mat-014 Ho et al. (2021)；mat-015 爱尔兰 AVM (2022, JREFE)；mat-008 Oust et al. (2019)；mat-016 Kok et al. (2017)；mat-009 Calainho et al. (2024)。
- 方法矩阵（已实现 / 近似 / 替代 / 未实现）见 `knowledge/experts/rep-001/blocks/04-machine-learning.md` 末节。

## §1 建模步骤（怎么做）

1. **先诊断空间结构**：用 Moran's I / LISA 检验空间依赖；显著则从全局 OLS 升级为空间模型（SAR/SEM，S-03）。
2. **选空间策略**：
   - 空间**依赖**（邻居影响）→ 空间滞后/误差模型（SAR/SEM）；
   - 空间**异质**（系数随位置变）→ GWR（mat-010, mat-011）；**MGWR 本包未实现**，仅列路线图；
   - 两者并存 → SAR-GWR 或局部空间模型（S-05）。
3. **ML 估值（当样本充足、关系非线性）**：
   - 候选：随机森林、梯度提升；实证中树集成常最优（mat-014）；
     ⚠️ **本包实现的是自研梯度提升，非 XGBoost 原版**（未复刻二阶泰勒展开、正则项与稀疏感知分裂）；
   - 必须做**偏差-方差权衡**与去偏（M-04）；对极端值单独处理（M-01 报极端值偏差高）。
4. **样本稀疏时改路线**：低换手率/小样本市场优先空间统计平滑（高斯过程/SAR），因其可给出**预测区间**（M-02）。
5. **可解释性**：ML 必须配特征归因，向业务与合规解释关键驱动（mat-014）。
   ⚠️ **本包提供排列重要性与部分依赖，未实现 SHAP**；如需 Shapley 值归因须显式声明并外接对应库。
6. **不确定性**：输出预测区间而非单点；ML 默认只给点估计，须补不确定性估计。
7. **稳健性**：GWR 需处理多重共线性与带宽敏感（S-01 局限）；ML 需交叉验证与时间外样本测试。

## §2 数据要求（怎么用数）

- **四级标注**：测算 / 估算 / 研判推断 / 待补。
- **溯源格式**：`（来源，机构，截至 年-月；口径：…）`。
- **地理编码质量**：地址错标会污染空间模型，须校验（M-02）。
- **样本量门槛**：ML 需较大样本；小样本下改统计模型并披露不确定性。
- **多源冲突**：显式说明，不静默择优。

## §3 质量门禁（怎么过关）

- 与 `quality-policies/rep-valuation-quality.json` 对应：数字一致、禁例 token 0 命中、无占位符。
- 交付前过 `references/spatial-ml-checklist.md`；公网发布前确认不涉密。
