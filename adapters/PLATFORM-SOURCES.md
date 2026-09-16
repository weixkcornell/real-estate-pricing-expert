# 平台数据源实测档案（智见 DSH）

> 本文件记录**实测**结果，不是推测。每条都标注了验证方式与时间。
> 探明日期：2026-09-16　探测入口：智见 DSH 专家库插件 `GET /plugins/dsh-expert-library/health`
>
> ⚠️ **纪律**：本文件中的"未知"就是未知，不填猜测值。桥接层遇到未知端点会显式报错，
> 不会静默降级或编造返回。

---

## 一、三个外部数据源的真实状态

来源：`GET https://real.zj.meizu.life/plugins/dsh-expert-library/health`
（checkedAt 2026-09-16T09:03:13.934Z）

| 工具 id | 名称 | 端点 | registered | keyPresent | reachable | 备注 |
|---|---|---|---|---|---|---|
| `zyt` | 政研通 zyt | `https://dss.ke.com` | ✅ true | ✅ true | ✅ true（3748 ms） | 身份：**租户「杭州市住建研究试点」· dataView = internal** |
| `beike` | 贝壳 beike | `https://building.ke.com/mcp` | ✅ true | ✅ true | ✅ true（3885 ms） | serverInfo：**布丁MCP服务 3.4.7** |
| `wind` | Wind | 本地 CLI | ❌ false | ❌ false | — | `cliPath` 指向 `.agents/skills/wind-mcp-skill/scripts/cli.mjs`，**CLI 文件不存在** |

每个源支持三种执行模式（`api` / `cli` / `auto`），可在专家库设置页逐工具配置；
`zytPreferCli` / `beikePreferCli` 为各自的"优先使用 CLI"开关。

---

## 二、beike：MCP over HTTP（已实测）

**端点**：`https://building.ke.com/mcp`
**传输**：HTTP POST + JSON-RPC 2.0，**响应为 SSE**（`Content-Type: text/event-stream`，帧格式 `event: message` + `data: {...}`）
**请求头**：`Accept: application/json, text/event-stream`

实测结果：

| 调用 | 结果 | 解读 |
|---|---|---|
| `ping` | `{"jsonrpc":"2.0","id":2,"result":{}}` | 服务存活，**ping 无需鉴权** |
| `tools/list`（无 Authorization） | `{"jsonrpc":"2.0","id":2,"error":{"code":0,"message":"Invalid or missing Authorization header"}}` | **数据类方法必须带 `Authorization` 头** |
| `initialize`（4 个 protocolVersion 全试） | `{"error":{"code":-32602,"message":"Invalid request parameters"}}` | 服务端对 initialize 参数有自己的要求，**尚未确认为何被拒**（无凭证时可能先校验鉴权） |

**结论**：桥接层可确认「传输与协议正确、凭证缺失」这一状态，并**如实上报上游原始错误信息**。
`Authorization` 的具体形式（`Bearer <key>` 或裸 key）**尚未实测确认**——将在拿到凭证后验证。

**凭证位置（平台侧）**：工作区 `.beike/BEIKE_MCP_API_KEY`（44 字节）。
本地/离线运行时由环境变量 `BEIKE_MCP_API_KEY` 提供。

---

## 三、zyt：智见数据服务（已实测）

**端点**：`https://dss.ke.com`（页面 title 即「智见」——`zyt` 是**智见自有数据服务**，不是第三方）

实测结果：

| 路径 | 方法 | 结果 |
|---|---|---|
| `/` `/health` `/docs` `/mcp` `/openapi.json` | GET | `200`，但返回的是 **SPA 首页 HTML**（不是 API 文档） |
| `/api` `/api/v1` `/api/user` `/api/datasets` `/api/mcp` `/api/openapi.json` | GET | `401 {"error":"未登录或 token 无效"}` |
| `/api/auth/login` | POST `{username, password}` | `401 {"error":"邮箱或密码错误"}` → **登录字段是邮箱，不是用户名** |

**结论**：
- 鉴权方式是**登录换 token**（邮箱 + 密码 → token → `Authorization` 头）
- 数据接口**必须登录后**才能枚举；`/api/openapi.json` 也需鉴权，因此**接口清单目前未知**
- 租户上下文由服务端给出：**「杭州市住建研究试点」**，`dataView = internal`
  → 对本包的直接含义：**数据是可用的内部研究视角，但口径需按该租户的数据字典确认**，
  不能套用公开口径。

**凭证**：平台侧保存（`.config/zyt`）；离线运行由 `ZYT_BASE_URL` + `ZYT_TOKEN`（或 `ZYT_EMAIL`/`ZYT_PASSWORD`）提供。

---

## 四、这对定价模型意味着什么（口径级影响）

1. **数据可得性提升**：原包里 4 个数据源（网签 / 挂牌 / 租金 / 土地）在包内标注为 `needs_key`；
   接入 zyt 后，若其提供网签与租金，则本包的**方法层级判定可以直接从「筛选级」升到「分析级」**——
   这是整包方法库能否真正被调用的前提。
2. **`dataView = internal` 必须进入口径声明**。本包所有输出都要求「来源 + 口径 + 截至期」，
   现在口径需增加 `dataView` 维度，并在报告中标明"数据为内部研究视角"。
3. **租户是「杭州市住建研究试点」** → 当前可用数据的**地域范围应以杭州为主**；
   跨城市结论必须显式标注"超出当前数据租户范围"。这一点直接影响本包 `rep.china.adapt` 能力的输出边界。
4. **beike MCP（布丁 3.4.7）** 提供房产类结构化能力，适合补本包最缺的**成交 microdata 与小区属性字典**。

---

## 五、尚未确认的事项（**不猜**）

| 事项 | 状态 | 确认方式 |
|---|---|---|
| beike `Authorization` 头的确切形式 | 未知 | 拿到 `BEIKE_MCP_API_KEY` 后调用 `tools/list` 验证 |
| beike `initialize` 被拒的确切原因 | 未知 | 同上；先带鉴权重试 |
| beike 的工具清单（有哪些工具、参数 schema） | 未知 | `tools/list` |
| zyt 的数据接口清单与数据字典 | 未知 | 登录后枚举 |
| zyt 的 token 有效期与刷新方式 | 未知 | 登录响应头/体 |
| 两源是否覆盖网签成交 microdata | 未知 | 由工具清单/接口清单判定 |

`adapters/platform_bridge.py` 提供 `--probe` 与 `--discover` 两个只读命令用于逐项确认上面这些项。
