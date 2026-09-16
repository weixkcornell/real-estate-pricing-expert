# 回归测试（tests）

> 回应评审 P2-2-b：
> "零依赖是纯 Python 标准库的合理取舍（可移植性），但意味着没有 statsmodels / PySAL
> 这类久经考验的数值库兜底——**自建高斯-约当求逆、集中似然 MLE 必须有回归测试护住**。"

---

## 运行

```bash
cd <包根>
python3 -m unittest discover -s tests -v
```

全部用例约 6–8 分钟（含 GP 超参搜索、GWR/SAR/SEM 集中似然与留一法克里金）。

---

## 这组测试护住什么

### A. 缺陷防复发（最有价值的部分）

本包迭代中发现的缺陷有一个共同特点：**语法正确、静态检查拦不住、单看输出也正常**。
只有"已知真值的合成数据 + 直接核算"才能暴露，因此必须固化为断言。

| 测试类 | 缺陷 | 断言 |
|---|---|---|
| `TestDefect1CaseShillerCollinearity` | Case-Shiller 二阶方差方程 `[1, g, g²]` 在持有间隔仅 2 个取值时**完全共线**（`g² = −2·1 + 3·g`）→ 矩阵奇异 → 静默退化为常数拟合 → σ_v 被错估为 0 | 窄间隔样本上 **σ_v > 0**；σ_u/σ_v 与真值（0.05 / 0.06）同量级；指数还原真值 6% 内 |
| `TestDefect2SEMDimensionMismatch` | SEM 中 `(I−λW)X` 误写成对**行**做矩阵-向量乘（维度错配）→ 设计矩阵退化、求逆奇异 | `sem_mle` 不抛异常、λ 可估计；`sar_mle` 的 ρ 落在平稳域 |
| `TestDefect3KrigingFakePrecision` | 回归克里金报 R²=1.000、MAE=0——样本内同点位插值必然精确复现观测，属**自我实现的假精度** | 必须返回 `metrics_loo`（留一法）；LOO 的 MAE > 0、R² < 0.999；**且样本内 R² > 样本外 R²**（这正是假精度的证据） |
| `TestDefect4GPUnitMismatch` | 高斯过程只标准化 X 未标准化 y → 核幅与价格量纲失配 → 核矩阵退化、区间宽度塌缩、覆盖率 0% | 区间宽度/均值落在 (0.5%, 80%)；留出集覆盖率 > 50% |
| `TestDefect5RentIndexJacobian` | ①Box-Cox 与对数形式的 Jacobian 不一致（相差 `Σln R`）→ 模型选择必然偏向对数；②λ≠0 时仍用 `exp(δ)` 算指数 | 返回结构同时含主口径 `index` 与诊断口径 `index_selected`；半对数指数还原真值 4% 内；租金面积系数还原真值 25% 内 |
| `TestDefect6AdapterStringFields` | 适配器把**全部** `standard_fields` 一律数值化 → 字符串字段（期/平台/出让条件）被转成 `None` → `_valid` 全判假 → **样本被静默清空** | 样本非空、`platform` 字段保留多取值、中文表头别名可解析、缺必需列时报 `AdapterError` |
| `TestGateRunner.test_no_false_conflict_on_trial_report` | 门禁的数字比对因格式/舍入产生**伪冲突**（首次实现 18 处全是误报） | 对试运行报告的冲突数必须为 **0**，且命中底座数字 > 50 |
| `TestGateRunner.test_range_separator_not_parsed_as_minus` | 区间连字符被当成负号（`1.50-1.58` → 解析出 `−1.58`） | `−1.58` 不得出现；1.50 须被解析 |
| `TestGateRunner.test_rounding_tolerance` | 四舍五入位差被误判为冲突（底座 1.68 vs 报告「约 1.7%」） | 1.7 vs 1.68 判为一致；5.0 vs 1.68 判为不一致 |
| `TestGateRunner.test_declarative_not_fake_pass` | 不可自动化的门禁被写成"永远 pass" | 声明性门禁必须 `execution == "declarative"` 且 **`status != "pass"`** |

### B. 数值正确性（自建数值库的底线）

- `inverse()`：对称正定矩阵 `A·A⁻¹ ≈ I`
- `det()`：与解析解一致（含负行列式）
- `ols()`：在已知 DGP 的合成基准上还原 6 个属性系数（按系数量级分级容差，见下）
- `wls_by_sigma()`：等权时应与 `ols()` 逐位一致
- `Unit.chain_mom()`：环比链式定基累计涨幅等于各期环比连乘

> **关于系数容差的分级**：基准 DGP 含空间平滑场 `S(经度, 纬度)`，它与"到市中心距离"部分共线，
> 因此距离类系数的可识别性弱于纯结构属性。测试据此分级——`楼层`（系数仅 0.0020）容差 25%，
> 其余属性 15%。**这不是为了让测试通过而放宽**，而是对识别能力的如实反映。

### C. 新增方法（评审 P1/P2 引入的能力）

- `TestComparisonPricing`：只调差异不调水平（同朝向同楼层 → 调整率为 0）；法拍/亲属/限价/超时效必须剔除；案例 <3 个必须拒绝；单项超 ±20% 必须报警；面积调整不得跨案例累积
- `TestCostResidual`：假设开发法解析解**精确闭合**（各项合计 == 开发完成后价值）；土地增值税超率累进
- `TestConformal`：conformal 覆盖率接近名义值；区间合成必须**加宽**区间
- `TestCapRateCalibrator`：还原合成数据中写死的 4 段已知收益率（12% 容差内）

### D. 治理一致性

- `TestManifestConsistency.test_no_ghost_citations`：调用 `source/check_manifest.py`，不得有幽灵引用
- `test_scenario_dag_acyclic_and_resolvable`：任务 id 唯一、`dependsOn` 可解析、skill 引用存在且目录真实、含 `convergencePolicy`
- `test_knowledge_blocks_cover_all_papers`：每条 `evidenceRefs` 指向的知识块文件必须存在，且文件内确实出现该 mat-id

---

## 数据说明

测试使用 `benchmark/` 下的**合成基准**（n≈5000，DGP 与真值写死在 `benchmark/make_benchmark.py`
并打印）。⚠️ **合成数据不代表任何真实市场**，禁止当作市场数据引用。

基准缺失时测试会 `skipTest` 并提示先执行：

```bash
python3 benchmark/make_benchmark.py
```

---

## 接入 CI

```bash
python3 -m unittest discover -s tests -v       || exit 1   # 数值与方法回归
python3 source/check_manifest.py               || exit 1   # 溯源一致性
python3 adapters/registry.py --selftest        || exit 1   # 数据契约实现
python3 eval/run_eval.py --no-answers          || exit 1   # eval 案例集完整性
python3 quality-policies/gate_runner.py --report "$R" --baseline "$B" || exit 1  # 报告门禁
```
