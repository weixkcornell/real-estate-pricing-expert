# 特征价格（Hedonic）方法代码

> 完整方法矩阵与全库说明见 **`<pack>/scripts/README.md`**（论文 → 方法 → 公式 → 数据 → 代码）。

本目录实现 Hedonic 全家族（严格对应论文设定）：

| 文件 | 实现 | 对应论文 |
|---|---|---|
| `hedonic_model.py` | M1 基础 Hedonic（线性/半对数/双对数） | Rosen (1974, JPE) |
| | M2 Box-Cox Hedonic（λ 似然网格搜索） | Malpezzi 综述 |
| | M3 时间虚拟变量指数 | Malpezzi；France 租金 (2020) |
| | M4 属性价格时变（时间×属性交互） | Chen & Harding (2016, JREFE) |
| | M5 空间扩展法（GWR 的对照基准） | Bitter et al. (2007) |
| `value_demo.py` | 模型价值实测：朴素均价法 vs 特征价格模型 | — |
| `sample_data.csv` | 8 套带属性挂牌样本 | — |

## 快速运行

```bash
# 全家族：拟合 + 共线性诊断 + Box-Cox + 交叉验证
python3 hedonic_model.py --data sample_data.csv \
    --y "挂牌单价(元/㎡)" \
    --x "面积㎡" "南向类(南北/西南/南=1)" "高楼层(高层=1)" \
    --form log-linear --boxcox --cv --k 5

# 给新样本定价（按 --x 顺序传值）
python3 hedonic_model.py --data sample_data.csv \
    --y "挂牌单价(元/㎡)" --x "面积㎡" "南向类(南北/西南/南=1)" "高楼层(高层=1)" \
    --predict 100 1 1

# 模型价值实测（复现定价报告第二章）
python3 value_demo.py
```

## 参数

| 参数 | 说明 |
|---|---|
| `--data` | CSV 路径 |
| `--y` | 因变量列名（价格或单价，正数） |
| `--x` | 自变量列名（分类变量须预编码为 0/1） |
| `--form` | `linear` / `log-linear`（默认，半对数）/ `log-log` |
| `--boxcox` | 附加 Box-Cox 变换并搜索最优 λ |
| `--cv --k N` | K 折交叉验证 |
| `--predict` | 按 `--x` 顺序传入属性值以估值 |

## 输出说明

- **系数表**：含标准误；log 形式自动换算为**百分比效应**（`exp(β)−1`），log-log 形式标注为**弹性**
- **多重共线性**：VIF > 10 显式告警
- **拟合指标**：样本内 MAE / MAPE / RMSE / R² / 调整 R² + 交叉验证（样本外）
- **对照基准**：朴素均价法（说明模型相对均价法的增益）

## 方法边界

- 小样本（自由度低）时系数**方向稳健但绝对水平不具统计显著性**，脚本会提示。
- 数据不足时主动报错并提示降级（`样本量不足以估计 N 个参数`），不静默给出不可靠结果。
