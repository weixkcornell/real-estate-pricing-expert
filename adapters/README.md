# adapters/ —— 数据适配器层

> 解决评审 **P0-2「数据底座是契约而非实现」**：`capability-contract.csv` 声明了 5 个数据源，但包内此前只有自检桩、没有任何适配器代码。结果是 **包内最精密的方法库（SAR / GWR / Case-Shiller）在真实执行路径上一次都没被调用**。

---

## 一、先说清楚：本层做了什么、没做什么

这是本层最重要的一段话，因为它直接决定报告能不能诚实地写。

| | 状态 | 说明 |
|---|---|---|
| **做好数据后能不能标准化进入方法链** | ✅ **已实现** | 5 个数据源全部有适配器，接受任意来源导出的 CSV/TSV（含中文表头），输出统一携带口径、单位、截至期、标注级 |
| **能不能自动联网取数** | ❌ **未实现，且刻意不做** | 需平台密钥授权。本包**不持有密钥、不索取密钥、不伪造数据** |
| 「partial」是什么意思 | ⚠️ **拿到数据后能进链路** | **不是**「已接入数据源」。任何报告不得把 `partial` 写成「已接入」 |

**为什么不做"看起来能用"的桩函数**：一个返回假数据或空壳的 `fetch()` 会让下游误判数据可得性，进而把一个**降级结论当成完整结论**交付——这正是评审指出的最危险后果。所以需要密钥的源一律抛 `NotImplementedError` 并在消息里给出合法路径。

---

## 二、契约实现对照（可用 `python3 adapters/registry.py` 自动生成）

| 数据源能力 | 本地适配器 | 本地状态 | 自动取数 | 契约口径 |
|---|---|---|---|---|
| `rep.flow.wangqian.read` | `WangqianAdapter` | partial | needs_key | 网签备案成交口径 |
| `rep.listing.price.read` | `ListingAdapter` | partial | needs_key | 中介平台挂牌报价口径 |
| `rep.rent.monthly.read` | `RentAdapter` | partial | needs_key | 住房租赁月度租金口径 |
| `rep.stats.70city.read` | `Index70CityAdapter` | **implemented** | needs_key | 国家统计局 70 城指数口径 |
| `rep.land.parcel.read` | `LandParcelAdapter` | partial | needs_key | 土地出让成交口径 |

> `rep.stats.70city.read` 的契约鉴权为「公开」，其本地解析器也是唯一**完全可运行**的；它的作用不是估值本身，而是给其他口径做**独立第三方校验**。Trial Run 里"小区挂牌同比 −12.4% vs 全市官方 −4.5%~−5.5%"这个关键发现就来自它。

---

## 三、文件结构

```
adapters/
├── README.md            # 本文件
├── __init__.py
├── base.py              # 基类与数据契约对象
│                        #   Status（implemented/partial/needs_key/unimplemented）
│                        #   Mark（引用/测算/估算/研判推断/待补）
│                        #   SourceResult（强制携带口径，下游无法"忘记标注口径"）
│                        #   Unit（总价↔单价、万元↔元、环比链式定基、同比反推）
│                        #   quality_report（CV 体检，样本可比性预警）
├── local_csv.py         # 4 个 CSV 适配器：网签 / 挂牌 / 租金 / 土地
├── index70.py           # 70 城指数：环比链式定基、同比反推、跨源方向校验
├── registry.py          # 契约对照 + 可用性探测 + 两源交叉校验 + 自检 CLI
└── fixtures/            # **合成演示数据**（含 _SYNTHETIC.txt 标记与生成脚本）
```

---

## 四、用法

```bash
# 契约实现对照（+ 可用性探测 + 演示数据自检）
python3 adapters/registry.py --probe --selftest

# 直接用适配器
python3 - <<'EOF'
import sys; sys.path.insert(0, "adapters")
from local_csv import ListingAdapter
r = ListingAdapter("你的挂牌导出.csv").fetch()
print(r.summary())
for w in r.warnings: print("  警告:", w)
deal = ListingAdapter.to_deal_estimate(r.rows, 0.03, 0.07)
print("推算出成交区间:", deal["deal_lower"], "~", deal["deal_upper"])
EOF
```

列名不匹配时用 `field_map`：

```python
ListingAdapter("x.csv", field_map={"unit_price": "我的单价列名"}).fetch()
```

适配器内置**中文别名**（`期/月份/统计期`、`单价/成交单价/挂牌单价`、`面积/建筑面积` 等），实务导出数据通常无需手写映射。

---

## 五、适配器内置的"数据体检"（这才是本层的价值）

适配器不只是搬运数据，它在**入口处就拦下会导致错误结论的问题**。演示数据自检实测输出：

| 检查 | 实测检出 |
|---|---|
| **平台间价差** | 平台丙 52771 / 平台乙 50242 / 平台甲 50500 → 价差 5.0%，提示**并列呈现不静默择优** |
| **面积结构污染** | 样本含 36 种面积 → 警告"直接比较均价会失真，应分层或改用 hedonic" |
| **合租混入** | 检出 2 条面积 <20㎡ 样本 → 警告疑为单间/合租，不可与整租混合建模 |
| **面积效应** | 小面积单位租金 97.9 vs 大面积 58.4 元/㎡/月，**相差 +67.7%** |
| **土地用途混统** | 检出住宅/商业 2 类用途 → 警告不可混合统计 |
| **出让条件** | 检出配建/自持/限价条件 → 警告名义楼面价不可直接比较 |
| **样本可比性** | 变异系数 25.6% → 提示样本可比性差 |
| **小样本降级** | 网签样本 <30 条 → 提示低于 hedonic 最小样本阈值，应降级为比较法 |

---

## 六、`fixtures/` 的重要声明

`fixtures/` 下的全部 CSV 是 **脚本生成的合成演示数据**，仅用于验证适配器与方法链的可运行性：

- 目录内有 `_SYNTHETIC.txt` 显式标记；
- 生成脚本 `_generate.py` 可复现；
- **禁止引用为市场数据**。演示数据中的"演示市/演示区/演示小区A"均为占位名。

---

## 七、已知修复（记录在案）

| 缺陷 | 性质 | 修复 |
|---|---|---|
| 把全部 `standard_fields` 一律数值化 | 逻辑错误 | 字符串字段（期/平台/出让条件）被转成 `None`，导致 `_valid` 全判假、**样本被静默清空**。改为声明式 `numeric_fields`，只转换数值字段 |
| `_read()` 中误用裸名 `delimiter` | 名称错误 | 改为 `self.delimiter` |
| `capability-contract.csv` 内嵌未转义逗号 | 数据格式错误 | `{city:string,period:string,district:string}` 会被任何 CSV 解析器切成多列（Excel 同样如此）→ 内嵌分隔符改为 `;`，并加列数一致性校验 |
