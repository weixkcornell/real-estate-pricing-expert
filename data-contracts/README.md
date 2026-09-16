# 数据契约（data-contracts）

`capability-contract.csv` 定义本包所需的数据源能力：能力 id、方法、输入/输出 schema、口径、单位、频率、时滞、鉴权、限流与备注。

---

## 一、这个文件**不是**实现说明

数据契约只声明"需要什么数据"。它**曾经**被误读为"已接入数据源"——评审 P0-2 正是指出这一点：契约里 5 个数据源全部标注"平台密钥库"，包内却没有任何适配器实现，结果包内最精密的方法库在真实执行路径上一次都没被调用。

**实现状态请查 `adapters/`，不要从本表推断：**

```bash
python3 adapters/registry.py            # 契约 vs 实现 的自动对照表
python3 adapters/registry.py --probe    # 附加可用性探测
```

对照表的状态语义：

| 状态 | 含义 |
|---|---|
| `implemented` | 本地可完整运行（零依赖、无需密钥） |
| `partial` | **本地 CSV 路径已实现**；自动取数需平台密钥 |
| `needs_key` | 仅契约声明，自动取数未实现（本包不持有密钥、不伪造数据） |
| `unimplemented` | 未实现 |

> ⚠️ `partial` 的含义是「**拿到数据后能标准化进入方法链**」，
> **不是**「能自动取数」。任何报告不得把 `partial` 写成「已接入数据源」。

---

## 二、各数据源的实际能力

| 能力 | 口径 | 单位 | 本地适配器 | 实现状态 |
|---|---|---|---|---|
| `rep.flow.wangqian.read` | 住建部门网签备案成交口径 | 元/㎡ | `adapters/local_csv.py: WangqianAdapter` | partial |
| `rep.listing.price.read` | 中介平台挂牌报价口径 | 元/㎡ | `adapters/local_csv.py: ListingAdapter` | partial |
| `rep.rent.monthly.read` | 住房租赁月度租金口径 | 元/月 | `adapters/local_csv.py: RentAdapter` | partial |
| `rep.stats.70city.read` | 国家统计局 70 城指数口径 | 指数（定基=100） | `adapters/index70.py: Index70CityAdapter` | **implemented** |
| `rep.land.parcel.read` | 土地出让成交口径 | 元/㎡（楼面价） | `adapters/local_csv.py: LandParcelAdapter` | partial |

`rep.land.parcel.read` 在 notes 中声明"用于成本法与供给分析"——该承诺现已由 `skills/cost-residual/` 兑现（此前是契约空头，见评审 P1-3）。

---

## 三、CSV 格式约定（**重要**）

`capability-contract.csv` 由多个消费方读取：本包的适配器与注册表、Excel、以及资料库的数据表导入。因此：

- **字段内不得出现裸逗号**。schema 字段原先写作 `{city:string,period:string,district:string}`，会被任何 CSV 解析器（含 Excel）切成多列，导致整行列错位。现已改为分号分隔：`{city:string;period:string;district:string}`。
- 校验方式：每行列数必须与表头一致。可用以下一行命令自检：

```bash
python3 - <<'EOF'
lines = open("capability-contract.csv", encoding="utf-8-sig").read().splitlines()
hdr = lines[0].split(",")
bad = [(i, len(l.split(","))) for i, l in enumerate(lines[1:], 2) if len(l.split(",")) != len(hdr)]
print("列数校验：", "全部一致" if not bad else bad)
EOF
```

---

## 四、缺口与获取条件

当前不可自动获取的数据源及其合法路径：

| 数据源 | 为什么不自动取 | 合法路径 |
|---|---|---|
| 网签成交 | 需住建/平台密钥授权 | 导出 CSV → `WangqianAdapter`；或由平台注入 `registry.REMOTE[...].fetch_hook` |
| 挂牌报价 | 需中介平台密钥授权 | 导出 CSV → `ListingAdapter` |
| 租金 | 需租赁平台密钥授权 | 导出 CSV → `RentAdapter` |
| 70 城指数 | 公开，但本包未内置抓取器（避免依赖网络与反爬波动） | 导出 CSV → `Index70CityAdapter` |
| 土地出让 | 需平台密钥授权 | 导出 CSV → `LandParcelAdapter` |

**本包不会伪造数据替代缺失来源。** 需要密钥的适配器在未被注入 `fetch_hook` 时会抛 `NotImplementedError` 并给出上述路径，而不是返回空壳或假数据——后者会让下游把一个降级结论当成完整结论交付。