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
  > 由 `tools/release.py` 在发布时自动断言，无需人工记得。授权断言为幂等操作：
  > 已生效则核验，临近过期或缺失则重新签发。因 GitHub 邀请 7 天未接受即过期，
  > 长期未发布时应定期执行 `python3 tools/grant_access.py` 续期。
  > 状态核验：`python3 tools/grant_access.py --check`
- [x] 待接受状态下已书面告知对方：需在 GitHub 点 Accept invitation 后权限方进入协作者列表

## 红线自查
- [x] 全部 JSON 可解析（`python3 -m json.tool`）
- [x] 全部模板占位符已清除（包内无未填写项）
- [x] 无凭据 / 密钥 / 内网地址 / 真实个人信息
