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

---

## 八、平台数据源桥接（智见 DSH）—— 2026-09-16 新增

### 为什么加这一层

本层原有 5 个适配器**只吃本地 CSV**，自动取数一律标 `needs_key`。而智见 DSH 平台已经挂好了
**三个真实数据源**，因此新增桥接层 `platform_bridge.py`，让定价方法能真正吃到平台数据。

### 三个平台数据源（实测）

| 工具 id | 名称 | 端点 | 协议 | 状态 |
|---|---|---|---|---|
| **`zyt`** | 政研通（**智见自有数据服务**） | `https://dss.ke.com` | REST + 登录换 token（**邮箱** + 密码） | 平台侧已注册、可达（3.7s）。租户 **「杭州市住建研究试点」**，`dataView=internal` |
| **`beike`** | 贝壳（布丁 MCP） | `https://building.ke.com/mcp` | **MCP over HTTP**（JSON-RPC 2.0 + SSE） | 平台侧已注册、可达（3.9s）。serverInfo：布丁MCP服务 3.4.7 |
| `wind` | Wind | 本地 CLI | 受控本地命令 | **未注册**：`cliPath` 指向的 `cli.mjs` 文件不存在 |

### 能力映射（诚实标注，不猜）

| 本包能力 | 首选平台源 | 状态 |
|---|---|---|
| `rep.flow.wangqian.read`（网签成交） | `zyt` | 待确认接口 |
| `rep.listing.price.read`（挂牌与小区属性） | `beike` | 待确认工具 |
| `rep.rent.monthly.read`（月度租金） | `zyt` | 待确认接口 |
| `rep.land.parcel.read`（土地出让） | `zyt` | 待确认接口 |
| `rep.stats.70city.read`（70 城指数） | —（公开源） | **已实现**（本地解析器） |

> ⚠️ **纪律**：标「待确认」的项**不得**在代码里猜路径去试。
> 拿到凭证后用 `--discover` 逐项确认，确认一项改一项。

### 用法

```bash
python3 adapters/platform_bridge.py --selftest    # 离线自检（不联网）
python3 adapters/platform_bridge.py --probe       # 只读探测可达性与凭证状态
python3 adapters/platform_bridge.py --discover    # 有凭证时枚举工具/接口
python3 adapters/registry.py --probe              # 在契约对照表里一并看到平台源
```

### 凭证（**只从环境读取，绝不硬编码、绝不入库**）

| 源 | 环境变量 | 平台侧位置 |
|---|---|---|
| beike | `BEIKE_MCP_API_KEY`（可选 `BEIKE_BASE_URL`） | `.beike/BEIKE_MCP_API_KEY` |
| zyt | `ZYT_TOKEN` 或 `ZYT_EMAIL` + `ZYT_PASSWORD`（可选 `ZYT_BASE_URL`） | `.config/zyt` |

内网自签场景可设 `PLATFORM_INSECURE_TLS=1` 关闭校验——**默认开启校验，降级必须显式且留痕**。

### 无凭证时的行为（本层最关键的设计）

不返回空壳、不返回假数据、不静默降级，而是**原样上报上游错误**。实测输出：

```
【beike】凭证来源: 未找到（需设置 BEIKE_MCP_API_KEY，或由平台注入）
  详情: 传输可达（ping OK），但**凭证缺失**：Invalid or missing Authorization header。
        上游要求 Authorization 头；本包不持有密钥（平台侧凭证为 .beike/BEIKE_MCP_API_KEY）

【zyt】凭证来源: 未找到（需设置 ZYT_TOKEN，或 ZYT_EMAIL + ZYT_PASSWORD）
  详情: 服务可达，但**凭证缺失**：…。上游原文：{"error":"未登录或 token 无效"}
```

**为什么这样做**：一个返回空壳的 `fetch()` 会让下游误判数据可得性，
进而把一个**降级结论当成完整结论**交付——这正是本包从评审中吸收的最重要一条纪律。

### 对定价模型的口径级影响

1. **方法层级可升级**：若 zyt 提供网签与租金，本包的方法层级判定可从**筛选级**升到**分析级**
   （hedonic + 空间计量才真正可用）——这是整包方法库能否被调用的前提。
2. **`dataView=internal` 必须进入口径声明**：输出规范中的「来源 + 口径 + 截至期」现在要加 `dataView` 维度。
3. **租户为「杭州市住建研究试点」** → 当前数据的**地域范围应以杭州为主**；
   跨城市结论必须标注"超出当前数据租户范围"（直接影响 `rep.china.adapt` 的输出边界）。
4. **beike 布丁 MCP** 适合补本包最缺的**成交 microdata 与小区属性字典**。

实测档案见 `adapters/PLATFORM-SOURCES.md`（含每条的验证方式与"尚未确认"清单）。
