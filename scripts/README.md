# 智见房地产定价方法库 · 代码说明与方法矩阵

本目录是「置价」专家包的**可执行方法论代码**：纯 Python 实现、**零第三方依赖**（仅标准库），
每条方法严格对应一篇论文的模型设定，可直接运行与复现。

---

## 一、目录结构

```
scripts/
├── core.py                 共享数值核心（矩阵/OLS/WLS/指标/分位/正态/交叉验证）
├── data_adapter.py         智见数据层适配器（口径归一、单位换算、数据集构建）
└── README.md               本文件
skills/
├── hedonic-pricing/scripts/
│   ├── hedonic_model.py    Hedonic 全家族（5 个模型）
│   ├── value_demo.py       模型价值实测（复现报告第二章）
│   └── sample_data.csv     8 套带属性样本
├── price-index/scripts/
│   ├── price_index.py      价格指数全家族（4 个模型）
│   └── sample_pairs.csv    60 组重复成交对
├── spatial-ml-valuation/scripts/
│   ├── spatial_model.py    空间计量全家族（7 个方法）
│   ├── ml_valuation.py     ML / AVM 全家族（6 个方法）
│   └── sample_spatial.csv  60 个带坐标样本
└── rent-income/scripts/
    ├── rent_model.py       租金与收益法全家族（6 个方法）
    ├── rent_income.py      收益率 / Cap Rate 计算器
    ├── rent_sample.csv     15 条在租样本
    └── rent_panel.csv      450 条租金面板（3 期 × 150）
```

---

## 二、方法矩阵（论文 → 方法 → 设定 → 数据 → 代码）

### 流派① 特征价格（Hedonic）— `hedonic-pricing/scripts/hedonic_model.py`

| 编号 | 方法 | 模型设定 | 对应论文 | 入口 |
|---|---|---|---|---|
| M1 | 基础 Hedonic | `P = Xβ + ε` / `ln P = Xβ` / `ln P = ln X·β` | Rosen (1974, JPE) | `HedonicModel` |
| M2 | Box-Cox Hedonic | `(P^λ−1)/λ = Xβ + ε`，λ 由 Profile 似然网格搜索 | Malpezzi 综述 | `BoxCoxHedonic` |
| M3 | 时间虚拟指数 | `ln P = Xβ + Σδ_t D_t` | Malpezzi；France 租金 (2020) | `TimeDummyHedonic` |
| M4 | 属性价格时变 | `ln P = Xβ + Σδ_t D_t + Σγ_kt(x_k×D_t)` | Chen & Harding (2016, JREFE) | `AttributeTimeVarying` |
| M5 | 空间扩展法 | `ln P = Xβ + Σ(x_k×f(coord))γ_k` | Bitter et al. (2007) | `SpatialExpansion` |

配套：VIF 共线性诊断、稳健标准误、K 折 / 留一交叉验证、朴素均价法对照。

### 流派② 价格指数（Index）— `price-index/scripts/price_index.py`

| 编号 | 方法 | 模型设定 | 对应论文 | 入口 |
|---|---|---|---|---|
| M6 | BMN 重复销售 | `r = Σ_j X_j b_j + u`，`X_j=−1(t)/+1(t')` | Bailey-Muth-Nourse (1963, JASA) | `bmn_index` |
| M7 | Case-Shiller 三阶段 | ①OLS → ②`û²=C+A·gap(+B·gap²)` → ③WLS（权重=1/σ̂²） | Case & Shiller (1989, AER) | `case_shiller_index` |
| M8 | 混合模型 | 水平方程 + 差分方程**堆叠**估计 | Case & Quigley (1991, REStat) | `HybridIndex` |
| M9 | ML 时外误差指数 | 各期用 t 前数据训练 → 期效应=实际/预测 | Calainho et al. (2024, JREFE) | `ml_price_index` |

**已验证**：合成数据（真实指数 100/94/87.5）下，BMN 得 94.50/88.58，
Case-Shiller 加权后得 **93.88/87.71**——更接近真值，符合论文预期。

### 流派③ 空间计量（Spatial）— `spatial-ml-valuation/scripts/spatial_model.py`

| 编号 | 方法 | 模型设定 | 对应论文 | 入口 |
|---|---|---|---|---|
| M10 | 空间权重 | 距离型 / KNN，行标准化 | Pace & LeSage (2004, JREFE) | `distance_weights` / `knn_weights` |
| M11 | Moran's I | `I=(n/S0)·ΣΣw_ij z_i z_j / Σz_i²` + 置换检验 | Pace & LeSage (2004) | `morans_i` |
| M12 | SAR 空间滞后 | `y = ρWy + Xβ + ε`，集中似然 MLE | Pace & LeSage (2004) | `sar_mle` |
| M13 | SEM 空间误差 | `u = λWu + ε`，集中似然 MLE | Pace & LeSage (2004) | `sem_mle` |
| M14 | GWR | `β(u_i,v_i)=(X'W_iX)⁻¹X'W_iy`，带宽 CV/AICc | Brunsdon et al. (1996) | `GWR` |
| M15 | 回归克里金 | 趋势 OLS + 残差普通克里金（指数变异函数，WLS 拟合） | Oust et al. (2019, JREFE) | `regression_kriging` |

### 流派④ 机器学习 / AVM — `spatial-ml-valuation/scripts/ml_valuation.py`

| 编号 | 方法 | 模型设定 | 对应论文 | 入口 |
|---|---|---|---|---|
| M16 | CART 回归树 | 方差削减准则分裂 | 基础 | `DecisionTreeRegressor` |
| M17 | 随机森林 | Bagging + 特征子抽样 + OOB 估计 | mat-024 Breiman (2001)；mat-014 Ho et al. (2021) | `RandomForest` |
| M18 | 梯度提升 | `F_m=F_{m−1}+η·h_m(残差)` | Calainho et al. (2024) | `GradientBoostingRegressor` |
| M19 | 高斯过程 AVM | 空间核 GP，**输出预测区间** | Irish AVM (2022, JREFE) | `GaussianProcessAVM` |
| M20 | 排列重要性 | 打乱特征后 MAE 增量 | 模型无关归因；本包**未实现 SHAP**，此为可复现替代 | `permutation_importance` |
| M21 | 部分依赖 | 固定其余变量为均值 | 同上 | `partial_dependence` |

### 流派⑤ 租金与收益法 — `rent-income/scripts/rent_model.py`

| 编号 | 方法 | 模型设定 | 对应论文 | 入口 |
|---|---|---|---|---|
| M22 | hedonic 租金 | `ln R = Xβ + ε` / `ln R = ln X·β` | Song et al. (2020)；Shanghai (2021, RSUE) | `HedonicRentModel` |
| M23 | 分层时间虚拟租金指数 | `R^(λ)=Xβ+Σδ_tD_t`，λ 网格 + 统一 Jacobian | France 租金 (2020) | `stratified_rent_index` |
| M24 | 匹配价格租金比 | 同一属性集估价格/租金两式 → 互相插补 | **Shanghai (2021, RSUE) 核心方法** | `matched_price_rent_ratio` |
| M25 | 用户成本法 | `UC=(1−t)r+δ+m+τ−g`，均衡价租比 `1/UC` | Wu-Gyourko-Deng (2012/2016)；Shanghai (2021) | `user_cost_model` |
| M26 | Cap Rate 定价 | `V = NOI / CapRate`（**强制声明城市与物业类型**） | Ghysels et al. (2007, EFM)；Fisher et al. (1994) | `cap_rate_valuation` |
| M27 | 折现现金流 | `V=Σ NOI_t/(1+r)^t + 终值/(1+r)^T` | Ghysels et al. (2007) | `dcf_valuation` |

### 智见数据层适配 — `scripts/data_adapter.py`

| 能力（capability-contract） | 口径 | 供数给 |
|---|---|---|
| `rep.flow.wangqian.read` | 网签成交 | Hedonic / 重复销售 / 混合 / 空间 / ML |
| `rep.listing.price.read` | 挂牌报价 | 比较法 / Hedonic（须折成交口径） |
| `rep.rent.monthly.read` | 月度租金 | hedonic 租金 / 租金指数 / 价租比 / 用户成本 |
| `rep.stats.70city.read` | 官方指数 | 指数交叉校验（**禁当绝对量**） |
| `rep.land.parcel.read` | 土地出让 | 成本法与供给分析 |

适配器提供：`listing_to_deal`（挂牌→成交折算，研判推断）、`index_rebase`（指数定基）、
`normalize_prices`（单位标准化到元/㎡）、`build_hedonic_dataset` / `build_repeat_sales_pairs` /
`build_price_rent_panel`、`cross_check`（两源交叉校验，冲突**并列不择优**）。

---

### 流派⑥ 实务方法（比较法 / 成本法 / 假设开发法）—— 评审 P1 整改新增

| # | 方法 | 设定 | 来源 | 代码 |
|---|---|---|---|---|
| P1 | 比较法（市场比较法） | 可比案例 → 交易情况/期日（连乘）+ 区位/实物/权益（连加）→ 加权合成 | 实务规范 GB/T 50291 | `skills/comparison-pricing/scripts/comparison_model.py` |
| P2 | 案例筛选与硬性排除 | 法拍/亲属/抵债/限价/共有产权/超时效 自动剔除并给理由 | 同上 | 同上（`screen()`） |
| P3 | 幅度与离散度纪律 | 单项 ≤±20%、累计 ≤±30%、调整后 CV ≤10% | 同上 | 同上（阈值常量） |
| P4 | 成本法（积算 + 折旧） | 土地 + 重置成本 + 管理/销售费 + 利息 + 利润 + 税费；折旧分年限法/观察法 | GB/T 50291 | `skills/cost-residual/scripts/cost_residual.py` |
| P5 | 假设开发法（剩余法） | `V = [V_dev(1−t_sale) − C(1+b+p)] / (1+a+p+t_land)` **解析解** | GB/T 18508 | 同上（`residual_static()`） |
| P6 | 土地增值税超率累进 | 按增值率分档 + 速算扣除系数 | 税法（须核对原文） | 同上（`lvt_ultra_progressive()`） |

> **P1 的关键设计**：调整率**优先锚定 hedonic 属性隐含价格**，而非估价师经验数字——
> 这是特征价格模型在中国实务中最被忽视、却最实用的用途。且严格遵守"**只调差异、不调水平**"：
> 案例与估价对象同朝向同楼层 → 调整为 0。

### 不确定性出口（全方法族统一）—— 评审 P1-2 整改新增

`scripts/uncertainty.py`

| # | 方法 | 用途 |
|---|---|---|
| U1 | split conformal | 为**任意黑箱模型**（RF / GBM / …）提供有限样本覆盖率保证的预测区间，无分布假设 |
| U2 | normalized conformal | 用"难度估计"（如 RF 树间标准差）使区间宽度**随样本变化**（贵房子给宽区间） |
| U3 | CV+ | 小样本下更高效（(k+1)/k 膨胀残差） |
| U4 | 区间合成 | 把方法不确定性与**口径折算不确定性**（挂牌→成交折扣区间）合成，取最不利组合 |

实测覆盖率（合成数据，名义 95%）：GBM+分裂共形 100%、RF+归一化共形 91.7%、RF+分裂共形 100%。

### 数据适配器层 —— 评审 P0-2 整改新增

`adapters/`（详见 `adapters/README.md`）

| # | 适配器 | 对应契约能力 | 状态 |
|---|---|---|---|
| A1 | `WangqianAdapter` | `rep.flow.wangqian.read` | partial（本地 CSV 已实现） |
| A2 | `ListingAdapter` | `rep.listing.price.read` | partial（含多平台价差检出、挂牌→成交折算） |
| A3 | `RentAdapter` | `rep.rent.monthly.read` | partial（含合租检出、面积效应） |
| A4 | `Index70CityAdapter` | `rep.stats.70city.read` | **implemented**（含环比链式定基、跨源方向校验） |
| A5 | `LandParcelAdapter` | `rep.land.parcel.read` | partial（含出让条件与用途混统预警） |

> ⚠️ `partial` 的含义是「**拿到数据后能标准化进入方法链**」，**不是**「能自动取数」。
> 自动取数需平台密钥，本包不持有密钥、不伪造数据（`registry.py` 中的 `needsRemoteFetch` 声明）。

### Cap Rate 标定 —— 评审 P2-1 整改新增

`skills/rent-income/scripts/caprate_calibrate.py`

| # | 方法 | 说明 |
|---|---|---|
| C1 | 匹配价格租金比倒数 | 毛资本化率 = 年租金 / 价格；净 = 毛 ×(1−运营成本率) |
| C2 | 大宗交易反推 | 净资本化率 = NOI / 成交价 |
| C3 | 用户成本反推隐含 π_e | `π_e = i(1−τ) + δ + m − 1/(P/R)`，把"收益率低"变成可检验假设 |

实测：合成数据上精确还原各段已知收益率（1.71% / 2.39% / 3.98% / 2.90% vs 真值 1.7% / 2.4% / 4.0% / 3.0%）。

---

## 三、快速运行

```bash
# Hedonic 全家族（含 Box-Cox、共线性、交叉验证）
python3 skills/hedonic-pricing/scripts/hedonic_model.py \
    --data skills/hedonic-pricing/scripts/sample_data.csv \
    --y "挂牌单价(元/㎡)" --x "面积㎡" "南向类(南北/西南/南=1)" "高楼层(高层=1)" --boxcox --cv

# 价格指数（BMN + Case-Shiller）
python3 skills/price-index/scripts/price_index.py --pairs skills/price-index/scripts/sample_pairs.csv --method all

# 空间计量（Moran / SAR / SEM / GWR / 克里金）
python3 skills/spatial-ml-valuation/scripts/spatial_model.py \
    --data skills/spatial-ml-valuation/scripts/sample_spatial.csv --y 单价 --x 面积 楼龄 --coord x y --method all

# 机器学习 AVM（RF / GBM / 高斯过程 + 归因）
python3 skills/spatial-ml-valuation/scripts/ml_valuation.py \
    --data skills/spatial-ml-valuation/scripts/sample_spatial.csv --y 单价 --x 面积 楼龄 x y --method all

# 租金指数 + 用户成本
python3 skills/rent-income/scripts/rent_model.py --data skills/rent-income/scripts/rent_panel.csv \
    --rent "月租(元)" --x "面积㎡" --time 期
python3 skills/rent-income/scripts/rent_model.py --price 50210 --rent-per-sqm 70.4 --fin-rate 0.0305

# 比较法
python3 skills/comparison-pricing/scripts/comparison_model.py --cases \
    skills/comparison-pricing/scripts/sample_cases.csv --as-of 2026-09 --target-area 100

# 成本法 / 假设开发法
python3 skills/cost-residual/scripts/cost_residual.py --method residual \
    --dev-value 42000 --build 3200 --dev-years 2 --profit-rate 0.15

# Conformal 区间（覆盖率检验）
python3 scripts/uncertainty.py --demo

# Cap Rate 标定
python3 skills/rent-income/scripts/caprate_calibrate.py \
    --data skills/rent-income/scripts/matched_pr_demo.csv --segment 城市 物业类型

# 数据适配器：契约对照 + 探测 + 自检
python3 adapters/registry.py --probe --selftest

# 智见数据层自检
python3 scripts/data_adapter.py
```

---

## 四、迭代记录（5 轮，含真实缺陷修复）

开发过程中通过「构造已知真值的合成数据 + 直接核算校验」发现并修复了 4 处实现缺陷：

| 轮次 | 发现的缺陷 | 性质 | 修复 |
|---|---|---|---|
| R2 | Case-Shiller 二阶方差方程的 `[1,g,g²]` 在间隔只有 2 个取值时**完全共线**（g²=−2·1+3·g），矩阵奇异后退化为常数拟合，σ_v 被错估为 0 | 数值退化 | 按间隔取值个数**自适应降阶**（quadratic/linear/constant） |
| R3 | SEM 中 `(I−λW)X` 误写成对**行**做矩阵-向量乘（维度错配），设计矩阵退化、求逆奇异 | 维度错误 | 改为对 X 的**每一列**施加变换 |
| R3 | 回归克里金报 **R²=1.000、MAE=0**——样本内同点位插值必然精确复现观测，属自我实现的假精度 | 评测口径错误 | 改为**留一法（LOO）**样本外评测 |
| R4 | 高斯过程**只标准化 X 未标准化 y**，核幅 σ_f 与价格量纲失配 → 核矩阵退化、区间宽度 1 元/㎡、覆盖率 0% | 量纲错误 | X 与 y **同时标准化**；扩展超参数网格 |
| R4 | 租金指数 ①Box-Cox 与对数形式的 **Jacobian 不一致**，模型选择必然偏向对数；②λ≠0 时仍用 exp(δ) 算指数，口径错误 | 似然/口径错误 | 统一 Box-Cox 参数化（含 λ=0）；指数改为**平均属性上比较**，并**双口径并列** |

### 本轮整改中新增发现并修复的缺陷（评审后）

| 轮次 | 缺陷 | 性质 | 修复 |
|---|---|---|---|
| R5 | 数据适配器把**全部** `standard_fields` 一律数值化，导致「期」「平台」「出让条件」等字符串字段被转成 `None`，`_valid` 全判假 → **样本被静默清空** | 逻辑错误 | 改为声明式 `numeric_fields`，只转换数值字段 |
| R5 | `capability-contract.csv` 的 schema 字段内嵌**未转义逗号**，任何 CSV 解析器（含 Excel）都会切成多列、整行列错位 | 数据格式错误 | 内嵌分隔符改为 `;`，并加列数一致性校验 |
| R5 | 比较法把目标溢价**无条件套用到所有案例**（同朝向同楼层的案例也被抬高），结果系统性失真 | 方法论错误 | 改为「只调差异不调水平」：`调整率 = 溢价(估价对象类别) − 溢价(案例类别)` |
| R5 | 比较法面积调整项被**跨案例累积**（后一案例继承前面所有案例的面积调整） | 逻辑错误 | 改为逐案例独立计算 |
| R5 | 比较法报表把「连加小计」误标为「累计调整」，**漏报期日连乘系数的影响** | 报表口径错误 | 分列「连加小计」与「净变动（含连乘）」 |
| R5 | 包内存在 5 处**幽灵引用**（引用了未登记在溯源清单的文献） | 溯源纪律 | 全部改写为 mat-id；其中 3 份真实文献补登；新增 `source/check_manifest.py` 自动校验 |

**验证方法**：所有方法均在「已知真值的合成数据」上检验还原能力。
例如租金指数，用 20 次重复实验确认估计量**无偏**（均值 97.79，真值 98.0，标准差 1.42）；
并直接对原始数据按期分组核算，与模型输出**逐位吻合**（99.80 vs 99.81），确认偏差来自抽样噪声而非实现。

---

## 五、设计原则

1. **零依赖**：只用 Python 标准库；任何环境可直接运行。
2. **忠于论文**：每条方法在模块 docstring 中标注对应论文与模型设定。
3. **可复现**：伪随机全部带固定种子，交叉验证划分确定性。
4. **诚实**：样本量不足、共线、奇异、λ 撞边界等一律显式报错或提示，不静默给错值。
5. **评测口径正确**：插值类方法（克里金）必须用留一法；ML 必须给样本外指标。
6. **口径不混用**：挂牌 / 成交 / 租金 / 官方指数四类口径在数据层即分离标记。
7. **不假装能用**：需要密钥的数据源如实标注 `needs_key`，不写"看起来能用"的桩函数——
   后者会让下游把降级结论当成完整结论交付。
8. **措辞与实现一致**：自研梯度提升不得称 XGBoost，排列重要性不得称 SHAP，
   未实现的能力（MGWR、邻接型空间权重）必须显式标注，由 `source/check_manifest.py` 与能力矩阵共同约束。

---

## 六、边界与免责

- 各合成样本（`sample_*.csv`、`rent_*.csv`）均为**演示数据**，用于验证实现，不代表真实市场。
- 小样本（自由度低）时系数方向稳健但绝对水平不具统计显著性，脚本会在输出末尾提示。
- 本代码库为方法论与量化建模参考，不构成投资建议、估值报告或法律意见。
