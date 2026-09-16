# 定价模型代码（scripts）

本目录是「置价」专家包的**可执行方法论代码**——纯 Python 实现，**零第三方依赖**（仅标准库），可直接在任何环境运行与复现。

## 目录

| 文件 | 用途 | 对应 Skill |
|---|---|---|
| `hedonic_model.py` | 特征价格（Hedonic）模型：OLS 估计、属性隐含价格、预测、评价、留一交叉验证 | `hedonic-pricing` |
| `value_demo.py` | 模型价值实测：朴素均价法 vs 特征价格模型，复现报告第二章数字 | `hedonic-pricing` |
| `sample_data.csv` | 8 套带属性挂牌样本（价值实测输入） | `hedonic-pricing` |
| `../../rent-income/scripts/rent_income.py` | 租金收益率 / 价格租金比 / Cap Rate 定价 | `rent-income` |
| `../../rent-income/scripts/rent_sample.csv` | 15 条在租样本 | `rent-income` |

## 一、特征价格模型

```bash
# 拟合 + 属性隐含价格 + 拟合优度
python3 hedonic_model.py --data sample_data.csv \
    --y "挂牌单价(元/㎡)" \
    --x "面积㎡" "南向类(南北/西南/南=1)" "高楼层(高层=1)" \
    --log-y --cv

# 给新样本定价（按 --x 顺序传入属性值）
python3 hedonic_model.py --data sample_data.csv \
    --y "挂牌单价(元/㎡)" \
    --x "面积㎡" "南向类(南北/西南/南=1)" "高楼层(高层=1)" \
    --log-y --predict 100 1 1
```

**参数**

| 参数 | 说明 |
|---|---|
| `--data` | CSV 路径 |
| `--y` | 因变量列名（价格或单价，须为正数） |
| `--x` | 自变量列名（数值列；分类变量请预先编码为 0/1 虚拟变量） |
| `--log-y` | 对因变量取对数（半对数模型，房地产实证最常用） |
| `--cv` | 输出留一交叉验证（样本外）指标 |
| `--predict` | 按 `--x` 顺序传入属性值，输出估值 |

**方法说明**：`价格 = 属性束隐含价格之和`（Rosen 1974）。回归系数经 `exp(β)−1` 转换为百分比效应，即「该属性值多少」。OLS 用正态方程 + 带主元高斯消元实现，不依赖 numpy。

## 二、模型价值实测

```bash
python3 value_demo.py                      # 用同目录 sample_data.csv
python3 value_demo.py --data 你的数据.csv
```

**实测结论**（可复现）：

| 方法 | MAE | MAPE | R² |
|---|---|---|---|
| 朴素均价法 | 3,504 元/㎡ | 7.06% | — |
| 特征价格模型 | 1,100 元/㎡ | 2.24% | 0.818 |
| **改善** | **−68.6%** | **−4.82 pp** | |

属性隐含价格：南向类 **+6.80%**、高楼层 **+9.20%**。留一交叉验证（样本外）MAPE **4.05%**。

## 三、租金与收益法

```bash
python3 ../../rent-income/scripts/rent_income.py \
    --rent-csv rent_sample.csv \
    --price-list 50210 47700 --price-labels 挂牌口径 成交口径 \
    --fin-rate 0.0305

# 商业地产 Cap Rate 定价
python3 ../../rent-income/scripts/rent_income.py --noi 1200000 --cap-rate 0.045
```

输出：单位租金分布与面积效应、毛/净租金收益率、价格租金比、融资成本对照、Cap Rate 定价与敏感性。

## 四、设计原则

1. **零依赖**：只用 Python 标准库，避免任何环境装不上导致代码失效。
2. **可复现**：脚本输出与定价报告逐 token 一致，报告数字均由此生成。
3. **诚实**：样本量不足时主动报错并提示降级（`样本量不足以估计 N 个参数`），不静默给出不可靠结果。
4. **通用**：不写死小区名与数据，任何符合列结构的 CSV 均可运行。

## 五、边界

- 小样本（自由度低）时系数**方向稳健但绝对水平不具统计显著性**，脚本会在输出末尾提示。
- 本代码为量化参考，不构成投资建议或正式估值意见。
