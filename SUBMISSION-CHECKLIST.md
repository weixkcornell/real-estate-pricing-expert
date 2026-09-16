# 提交物验收清单（对照开发文档 §9.2）

> 平台评审按此逐项打勾；先自检再提交。

## 包清单
- [x] `pack.json`（id / version / schemaVersion / caliberDeclarations）

## 专家与溯源
- [x] `experts/*.json`（schemaVersion 2 全字段；`source/` + `SOURCE-MANIFEST.json` 溯源齐备）
- [x] 溯源材料均有授权状态（本包全为公开可引用文献）

## 场景与交付
- [x] `scenarios/*.json`（DAG 依赖无环、专家 id 全部可解析、deliverable 明确）
- [x] `deliverable` 明确：定价报告 + 数据底座表 + GATE 文件

## 输出与门禁
- [x] `output-templates/` + `quality-policies/`（起步门禁覆盖数字一致与禁例 token）

## 工艺 Skill
- [x] `skills/<id>/SKILL.md`（触发词齐全，references 完整）
  - hedonic-pricing / price-index / spatial-ml-valuation / rent-income

## 数据契约
- [x] 数据契约表 `data-contracts/capability-contract.csv`（含口径、单位、频率、时滞、鉴权）

## 试运行
- [x] 试运行记录：一单端到端交付 + GATE 文件 + 匿名化对外样本
  > 已完成（2026-09-16）：北京远洋山水南区定价试运行，端到端交付定价报告 + 数据底座表 + GATE 文件，GATE 质 92/100。详见 `TRIAL-RUN.md`。

## 发布与协作授权（每次更新版本必过）
- [x] 版本已推送至 `weixkcornell/real-estate-pricing-expert`（main 分支）
- [x] 协作授权在册：`scubiry-glitch`（scubiry@gmail.com）具备 **write** 权限
  > 由 `../ops/release.py` 在发布时自动断言，无需人工记得。授权断言为幂等操作：
  > 已生效则核验，临近过期或缺失则重新签发。因 GitHub 邀请 7 天未接受即过期，
  > 长期未发布时应定期执行 `python3 ../ops/grant_access.py` 续期。
  > 状态核验：`python3 ../ops/grant_access.py --check`
- [x] 待接受状态下已书面告知对方：需在 GitHub 点 Accept invitation 后权限方进入协作者列表

## 知识 grounding（评审 P0-1 整改）
- [x] `knowledge/experts/rep-001/blocks/` 已落 7 个结构化知识块，23 篇核心文献逐篇含
      「模型设定 / 关键方程 / 数据要求 / 估计方法 / 中国适配注意点」
- [x] `knowledge/experts/rep-001/index.json` 建立「能力 → 知识块 → 论文」映射，`evidenceRefs` 名副其实
- [x] `proficiency` 已显式标注为**先验假设、未经评测**，并给出标定方式（`eval/`）
- [x] 无幽灵引用：`python3 source/check_manifest.py` 通过

## 数据接入（评审 P0-2 整改）
- [x] `adapters/` 提供 5 个数据源的**可运行实现**（网签 / 挂牌 / 租金 / 70城指数 / 土地）
- [x] 自动取数未实现的部分**如实标注 `needs_key`**，不伪造数据、不假装可用
- [x] `python3 adapters/registry.py --probe --selftest` 通过

## 方法覆盖（评审 P1 整改）
- [x] 新增 `comparison-pricing`（比较法，中国实务最高频路径）
- [x] 新增 `cost-residual`（成本法 + 假设开发法），消除 `rep.land.parcel.read` 的契约空头
- [x] `scripts/uncertainty.py` 提供 conformal 区间，使 `interval-required` 对全方法族可执行

## 中国化与统计加固（评审 P2 整改）
- [x] `skills/hedonic-pricing/references/policy-shock.md`：政策断点四步工艺（学区/限价/法拍/限售）
- [x] `skills/rent-income/scripts/caprate_calibrate.py`：从租金与价格推导城市/类型 cap rate
- [x] `benchmark/` 合成基准（n≈5000，已知 DGP）+ `tests/` 回归测试
- [x] `quality-policies/gate_runner.py`：可执行门禁；不可自动化的降级为声明性要求
- [x] 场景 DAG：任务 id 引用、分歧容差阈值 15%、skill 字段补全、无环

## 措辞校准（评审 P3-2 整改）
- [x] XGBoost → 明确为「自研梯度提升，非原版」；SHAP → 明确为「排列重要性，未实现 SHAP」
- [x] MGWR、邻接型空间权重 → 明确标注「未实现，列路线图」
- [x] 幽灵引用（Istanbul 2025 / Kain & Quigley 1970 / Helbich 2014 / France 2020 / Quigley 1995）
      已全部改写为 mat-id；其中 Breiman (2001)、Himmelberg et al. (2005)、Quigley (1995)
      已在 SOURCE-MANIFEST 补登（mat-024 / mat-025 / mat-026）

## 红线自查
- [x] 全部 JSON 可解析（`python3 -m json.tool`）
- [x] 全部模板占位符已清除（包内无未填写项）
- [x] 无凭据 / 密钥 / 内网地址 / 真实个人信息
