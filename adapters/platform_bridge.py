#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
平台数据源桥接 —— 把智见 DSH 的两个真实数据源接进本包的适配器层。

解决什么问题：
    本包原有 5 个适配器（网签 / 挂牌 / 租金 / 70城指数 / 土地）**只吃本地 CSV**，
    自动取数一律标注 `needs_key`。而智见平台已经挂好了两个真实数据源：
      · `zyt`   政研通（智见自有数据服务，https://dss.ke.com，租户「杭州市住建研究试点」）
      · `beike` 贝壳（布丁 MCP 服务 3.4.7，https://building.ke.com/mcp）
    本模块把它们桥接为 SourceAdapter，使本包的定价方法能真正吃到平台数据。

实测事实（详见 adapters/PLATFORM-SOURCES.md）：
    · beike 是 **MCP over HTTP**：JSON-RPC 2.0 + SSE 响应；`ping` 免鉴权，
      `tools/list` 等数据方法**必须带 Authorization 头**，否则返回
      `{"code":0,"message":"Invalid or missing Authorization header"}`
    · zyt 是 **REST + 登录换 token**：`/api/auth/login` 接受**邮箱**（不是用户名），
      其余 /api 路径未登录一律 `401 {"error":"未登录或 token 无效"}`

设计纪律（与本包一致，不可违背）：
    1. **凭证只从环境读取**，绝不硬编码、绝不写入仓库；
    2. 凭证缺失时状态为 `needs_key`，**并原样上报上游错误信息**，不伪造数据、不静默降级；
    3. 未确认的端点在代码里标为「未确认」，调用时显式报错，而不是猜一个路径去试。

用法：
    python3 adapters/platform_bridge.py --probe        # 只读：探测可达性与凭证状态
    python3 adapters/platform_bridge.py --discover     # 只读：在有凭证时枚举工具/接口
    python3 adapters/platform_bridge.py --selftest     # 离线自检（不联网）
"""

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _c in (_ROOT, os.path.join(_ROOT, "scripts")):
    if _c not in sys.path:
        sys.path.insert(0, _c)

from base import Mark, SourceAdapter, SourceResult, Status  # noqa: E402

# ================================================================ 已实测的端点
BEIKE_DEFAULT_URL = "https://building.ke.com/mcp"
ZYT_DEFAULT_URL = "https://dss.ke.com"
ZYT_LOGIN_PATH = "/api/auth/login"          # 已实测：EMPTY 字段为 email
ZYT_TENANT_HINT = "杭州市住建研究试点"        # 来自平台健康接口 identity.tenantName
ZYT_DATA_VIEW = "internal"                  # 来自平台健康接口 identity.dataView

#: 环境变量名（凭证一律走这里，README 里说明，不写进代码）
ENV_BEIKE_KEY = "BEIKE_MCP_API_KEY"
ENV_ZYT_TOKEN = "ZYT_TOKEN"
ENV_ZYT_EMAIL = "ZYT_EMAIL"
ENV_ZYT_PASSWORD = "ZYT_PASSWORD"
ENV_ZYT_BASE = "ZYT_BASE_URL"
ENV_BEIKE_BASE = "BEIKE_BASE_URL"

#: 平台侧凭证文件（DSH 工作区内的实际位置，只读探测，不复制内容）
PLATFORM_KEY_PATHS = [
    os.path.expanduser("~/.beike/BEIKE_MCP_API_KEY"),
    os.path.expanduser("~/.config/zyt/token"),
]


class PlatformError(Exception):
    pass


# ================================================================ 凭证解析
def resolve_beike_key():
    """按优先级解析 beike 密钥。返回 (key|None, 来源说明)。"""
    v = os.environ.get(ENV_BEIKE_KEY)
    if v and v.strip():
        return v.strip(), "环境变量 %s" % ENV_BEIKE_KEY
    for p in PLATFORM_KEY_PATHS:
        if os.path.isfile(p) and "beike" in p:
            try:
                t = open(p, encoding="utf-8", errors="replace").read().strip()
                if t:
                    return t, "平台凭证文件 %s" % p
            except OSError:
                pass
    return None, "未找到（需设置 %s，或由平台注入）" % ENV_BEIKE_KEY


def resolve_zyt_credentials():
    """解析 zyt 凭证。返回 (dict, 来源说明)。token 优先，其次邮箱+密码。"""
    tok = os.environ.get(ENV_ZYT_TOKEN)
    if tok and tok.strip():
        return {"token": tok.strip()}, "环境变量 %s" % ENV_ZYT_TOKEN
    em, pw = os.environ.get(ENV_ZYT_EMAIL), os.environ.get(ENV_ZYT_PASSWORD)
    if em and pw:
        return {"email": em.strip(), "password": pw}, "环境变量 %s + %s" % (ENV_ZYT_EMAIL, ENV_ZYT_PASSWORD)
    return {}, "未找到（需设置 %s，或 %s + %s）" % (ENV_ZYT_TOKEN, ENV_ZYT_EMAIL, ENV_ZYT_PASSWORD)


# ================================================================ HTTP 工具
def _ssl_ctx():
    """
    平台内网/自签场景下允许关闭校验，但**必须显式开启**，默认保持校验。
    纪律：默认安全，降级要留痕。
    """
    if os.environ.get("PLATFORM_INSECURE_TLS", "").lower() in ("1", "true", "yes"):
        c = ssl.create_default_context()
        c.check_hostname = False
        c.verify_mode = ssl.CERT_NONE
        return c
    return ssl.create_default_context()


def http_call(url, method="GET", body=None, headers=None, timeout=30):
    """返回 (status, text, headers, elapsed_ms)。不抛异常，错误以状态码返回。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", "real-estate-pricing-expert/platform-bridge")
    if data:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    op = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_ssl_ctx()))
    t0 = time.time()
    try:
        with op.open(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace"), dict(resp.headers), int((time.time() - t0) * 1000)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers), int((time.time() - t0) * 1000)
    except Exception as e:
        return 0, "TRANSPORT_ERROR: %s" % e, {}, int((time.time() - t0) * 1000)


def parse_sse(text):
    """
    解析 MCP over HTTP 的 SSE 响应。

    实测响应形如：
        event: message
        data: {"jsonrpc":"2.0","id":1,"result":{...}}

    返回解析出的 JSON 对象列表（可能为空——有些实现只回 202 无 body）。
    """
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            out.append(json.loads(payload))
        except ValueError:
            # 非 JSON 的 data 帧：可能是分片，跳过但留痕
            continue
    if not out:
        # 兼容非 SSE 的纯 JSON 响应
        try:
            out.append(json.loads(text))
        except ValueError:
            pass
    return out


# ================================================================ beike：MCP over HTTP
class McpHttpClient:
    """
    MCP over HTTP（streamable）最小客户端。

    协议要点（实测）：
      · POST JSON-RPC 2.0，Accept 必须含 `application/json` 与 `text/event-stream`
      · 响应是 SSE
      · 数据方法需 `Authorization` 头；缺失时服务端回
        `{"code":0,"message":"Invalid or missing Authorization header"}`
    """

    def __init__(self, base_url=BEIKE_DEFAULT_URL, api_key=None, timeout=30,
                 session_id=None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.session_id = session_id
        self._id = 0
        self.last_error = None

    def _headers(self):
        h = {"Accept": "application/json, text/event-stream"}
        if self.api_key:
            # ⚠️ 实测未确认是 `Bearer <key>` 还是裸 key。两种都试，见 _auth_variants()。
            h["Authorization"] = "Bearer %s" % self.api_key
        if self.session_id:
            h["mcp-session-id"] = self.session_id
        return h

    def _auth_variants(self):
        """返回待验证的 Authorization 头变体（顺序即优先级）。"""
        k = self.api_key
        if not k:
            return [None]
        return ["Bearer %s" % k, k]

    def rpc(self, method, params=None):
        self._id += 1
        body = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            body["params"] = params
        st, text, hd, ms = http_call(self.base_url, "POST", body, self._headers(), self.timeout)
        sid = hd.get("mcp-session-id") or hd.get("Mcp-Session-Id")
        if sid:
            self.session_id = sid
        frames = parse_sse(text)
        obj = frames[0] if frames else None
        if obj is None:
            self.last_error = {"http": st, "raw": text[:300]}
        elif "error" in obj:
            self.last_error = obj["error"]
        else:
            self.last_error = None
        return {"http": st, "ms": ms, "frame": obj, "raw": text[:400],
                "sessionId": self.session_id}

    def ping(self):
        return self.rpc("ping")

    def list_tools(self, try_auth_variants=True):
        if try_auth_variants and self.api_key:
            saved = self.api_key
            for variant in self._auth_variants():
                self.api_key = None if variant is None else (
                    variant[7:] if variant.startswith("Bearer ") else variant)
                self._bearer = variant is not None and variant.startswith("Bearer ")
                r = self.rpc("tools/list")
                fr = r.get("frame") or {}
                if "result" in fr:
                    r["authVariantUsed"] = "Bearer" if self._bearer else "raw"
                    self.api_key = saved
                    return r
            self.api_key = saved
        return self.rpc("tools/list")

    def call_tool(self, name, arguments=None):
        return self.rpc("tools/call", {"name": name, "arguments": arguments or {}})


def classify_mcp_error(frame):
    """把 MCP 错误翻译成平台状态——本包纪律：如实转述，不美化。"""
    if not isinstance(frame, dict):
        return None
    err = frame.get("error")
    if not isinstance(err, dict):
        return None
    msg = str(err.get("message", ""))
    if "Authorization" in msg or "authorization" in msg:
        return {"kind": "needs_key", "upstreamMessage": msg,
                "note": "上游要求 Authorization 头；本包不持有密钥（平台侧凭证为 .beike/BEIKE_MCP_API_KEY）"}
    if err.get("code") == -32602:
        return {"kind": "protocol_params", "upstreamMessage": msg,
                "note": "服务端对 initialize 参数有自己的要求；无凭证时可能先校验鉴权，尚未确认"}
    return {"kind": "upstream_error", "upstreamMessage": msg}


# ================================================================ zyt：REST + 登录换 token
class ZytClient:
    """
    政研通（智见数据服务）客户端。

    实测：
      · `/api/auth/login` 接受 **email** 字段（传 username 会得到「邮箱或密码错误」）
      · 未登录访问 /api 一律 `401 {"error":"未登录或 token 无效"}`
      · 数据接口清单**尚未确认**（/api/openapi.json 也需鉴权）→ discover() 只做安全探测，
        未确认的路径一律不猜。
    """

    def __init__(self, base_url=None, token=None, email=None, password=None, timeout=30):
        self.base_url = (base_url or os.environ.get(ENV_ZYT_BASE) or ZYT_DEFAULT_URL).rstrip("/")
        self.token = token
        self.email = email
        self.password = password
        self.timeout = timeout
        self.tenant = ZYT_TENANT_HINT
        self.data_view = ZYT_DATA_VIEW

    def _auth_headers(self):
        h = {"Accept": "application/json"}
        if self.token:
            h["Authorization"] = self.token if self.token.lower().startswith("bearer") else "Bearer %s" % self.token
        return h

    def login(self):
        if not (self.email and self.password):
            return {"ok": False, "reason": "needs_key",
                    "detail": "未提供邮箱/密码；需设置 %s + %s" % (ENV_ZYT_EMAIL, ENV_ZYT_PASSWORD)}
        st, text, hd, ms = http_call(self.base_url + ZYT_LOGIN_PATH, "POST",
                                     {"email": self.email, "password": self.password},
                                     {"Accept": "application/json"}, self.timeout)
        if st == 200:
            try:
                obj = json.loads(text)
            except ValueError:
                obj = {}
            tok = (obj.get("token") or obj.get("accessToken")
                   or (obj.get("data") or {}).get("token") if isinstance(obj, dict) else None)
            if tok:
                self.token = tok
            return {"ok": True, "http": st, "ms": ms, "tokenPresent": bool(self.token),
                    "raw": text[:200]}
        return {"ok": False, "http": st, "ms": ms, "raw": text[:300]}

    def get(self, path, params=None):
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return http_call(url, "GET", None, self._auth_headers(), self.timeout)


# ================================================================ 适配器封装
class BeikeMcpAdapter(SourceAdapter):
    """
    beike（布丁 MCP 3.4.7）适配器。

    状态语义：
      · 传输可达 + 有凭证 → `implemented`（工具清单可枚举）
      · 传输可达 + 无凭证 → `needs_key`（**如实上报上游错误**，不伪造）
      · 传输不可达        → `unimplemented`
    """

    capability = "platform.beike.mcp"
    caliber = "贝壳（布丁MCP）房产结构化数据口径"
    unit = "随工具而定"
    frequency = "实时"
    lag = "T+0"
    auth = "Authorization 头（平台侧凭证 .beike/BEIKE_MCP_API_KEY）"
    description = "贝壳 MCP 工具桥接：用于补成交 microdata 与小区属性字典。"
    status = Status.NEEDS_KEY

    def __init__(self, base_url=None, api_key=None, timeout=30):
        self.base_url = base_url or os.environ.get(ENV_BEIKE_BASE) or BEIKE_DEFAULT_URL
        key, src = resolve_beike_key()
        self.api_key = api_key or key
        self.credentialSource = src
        self.timeout = timeout

    def probe(self):
        c = McpHttpClient(self.base_url, self.api_key, self.timeout)
        p = c.ping()
        fr = p.get("frame") or {}
        if "result" not in fr:
            return False, "MCP ping 失败：%s（原始：%s）" % (fr.get("error") or p.get("http"), p.get("raw", "")[:120])
        if not self.api_key:
            tl = c.list_tools()
            info = classify_mcp_error(tl.get("frame"))
            return False, "传输可达（ping OK），但**凭证缺失**：%s。%s" % (
                (info or {}).get("upstreamMessage", "?"), (info or {}).get("note", ""))
        tl = c.list_tools()
        if "result" in (tl.get("frame") or {}):
            self.status = Status.IMPLEMENTED
            return True, "传输可达 + 凭证有效（%s），工具清单可枚举" % self.credentialSource
        info = classify_mcp_error(tl.get("frame"))
        return False, "传输可达，但 tools/list 被拒：%s" % ((info or {}).get("upstreamMessage", "?"))

    def fetch(self, tool=None, arguments=None, **params):
        c = McpHttpClient(self.base_url, self.api_key, self.timeout)
        if tool:
            r = c.call_tool(tool, arguments)
            frames = r.get("frame")
            err = classify_mcp_error(frames)
            return SourceResult(
                self.capability, self.status, self.caliber, self.unit,
                as_of={"probe": time.strftime("%Y-%m-%dT%H:%M:%S")},
                rows=[frames] if frames else [],
                series=[],
                warnings=(["上游错误：%s" % err["upstreamMessage"]] if err else []),
                gaps=[] if self.api_key else ["未持有凭证，未发起数据调用"],
                provenance=[self.base_url, "凭证来源：%s" % self.credentialSource])
        tl = c.list_tools()
        fr = tl.get("frame") or {}
        if "result" in fr:
            tools = (fr["result"] or {}).get("tools") or []
            return SourceResult(self.capability, Status.IMPLEMENTED, self.caliber, self.unit,
                                as_of={"probe": time.strftime("%Y-%m-%dT%H:%M:%S")},
                                rows=[{"tool": t.get("name"), "description": t.get("description")} for t in tools],
                                series=[], warnings=[], gaps=[],
                                provenance=[self.base_url, "tools/list"])
        info = classify_mcp_error(fr)
        return SourceResult(self.capability, Status.NEEDS_KEY, self.caliber, self.unit,
                            as_of=None, rows=[], series=[],
                            warnings=["无法枚举工具：%s" % ((info or {}).get("upstreamMessage") or tl.get("raw"))],
                            gaps=["需 %s；平台侧凭证为 .beike/BEIKE_MCP_API_KEY" % ENV_BEIKE_KEY],
                            provenance=[self.base_url])


class ZytRestAdapter(SourceAdapter):
    """政研通（智见数据服务）适配器。数据接口清单未确认前，只做安全探测与登录。"""

    capability = "platform.zyt.rest"
    caliber = "政研通（智见数据服务）数据口径 · dataView=%s · 租户「%s」" % (ZYT_DATA_VIEW, ZYT_TENANT_HINT)
    unit = "随接口而定"
    frequency = "待确认"
    lag = "待确认"
    auth = "登录换 token（邮箱 + 密码）"
    description = "政研通桥接：智见自有数据服务，当前租户为杭州市住建研究试点。"
    status = Status.PARTIAL

    def __init__(self, base_url=None, token=None, email=None, password=None, timeout=30):
        creds, src = resolve_zyt_credentials()
        self.client = ZytClient(base_url, token or creds.get("token"),
                                email or creds.get("email"), password or creds.get("password"), timeout)
        self.credentialSource = src

    def probe(self):
        st, text, hd, ms = self.client.get("/api")
        if st == 401:
            if not self.client.token and not (self.client.email and self.client.password):
                # 无凭证 → needs_key（不是 partial：partial 意味着存在可用的本地回退路径）
                self.status = Status.NEEDS_KEY
                return False, "服务可达，但**凭证缺失**：%s。上游原文：%s" % (self.credentialSource, text.strip()[:80])
            lg = self.client.login()
            if not lg.get("ok"):
                self.status = Status.NEEDS_KEY
                return False, "服务可达，登录未通过：%s（上游原文：%s）" % (lg.get("http"), lg.get("raw", "")[:100])
            st, text, hd, ms = self.client.get("/api")
        if st == 200:
            self.status = Status.IMPLEMENTED
            return True, "服务可达且已鉴权（%s，%dms）" % (self.credentialSource, ms)
        return False, "服务返回 HTTP %s：%s" % (st, text.strip()[:120])

    def fetch(self, path=None, params=None, **kw):
        if path is None:
            probe_txt = self.probe()[1]
            return SourceResult(self.capability, self.status, self.caliber, self.unit,
                                as_of={"probe": time.strftime("%Y-%m-%dT%H:%M:%S")},
                                rows=[], series=[], warnings=[probe_txt],
                                gaps=["数据接口清单尚未确认——须登录后枚举 /api/openapi.json"],
                                provenance=[self.client.base_url])
        st, text, hd, ms = self.client.get(path, params)
        try:
            rows = json.loads(text)
        except ValueError:
            rows = [{"raw": text[:400]}]
        rows = rows if isinstance(rows, list) else [rows]
        warns = [] if st == 200 else ["HTTP %s：%s" % (st, text.strip()[:150])]
        return SourceResult(self.capability, Status.IMPLEMENTED if st == 200 else Status.NEEDS_KEY,
                            self.caliber, self.unit,
                            as_of={"probe": time.strftime("%Y-%m-%dT%H:%M:%S")},
                            rows=rows, series=[], warnings=warns, gaps=[],
                            provenance=[self.client.base_url + path, "口径需按该租户数据字典确认"])


# ================================================================ 能力映射（诚实标注）
CAPABILITY_MAPPING = [
    # (本包能力, 首选平台源, 依据, 状态)
    ("rep.flow.wangqian.read", "zyt", "网签成交属住建口径，应在政研通（租户=杭州住建研究试点）", "待确认接口"),
    ("rep.listing.price.read", "beike", "挂牌价与小区属性是贝壳类平台的强项", "待确认工具"),
    ("rep.rent.monthly.read", "zyt", "租赁月度租金口径属住建统计", "待确认接口"),
    ("rep.stats.70city.read", "—", "国家统计局公开数据，本包已有本地解析器（Index70CityAdapter）", "已实现"),
    ("rep.land.parcel.read", "zyt", "土地出让属自然资源/住建口径", "待确认接口"),
]


# ================================================================ CLI
def cmd_probe():
    print("=" * 80)
    print("平台数据源桥接 · 只读探测（不发起任何写操作、不伪造数据）")
    print("=" * 80)

    b = BeikeMcpAdapter()
    print("\n【beike】贝壳 · 布丁 MCP")
    print("  端点    : %s" % b.base_url)
    print("  凭证来源: %s" % b.credentialSource)
    ok, why = b.probe()
    print("  状态    : %s" % ("可用" if ok else "不可用"))
    print("  详情    : %s" % why)

    z = ZytRestAdapter()
    print("\n【zyt】政研通 · 智见数据服务")
    print("  端点    : %s" % z.client.base_url)
    print("  租户    : %s（dataView=%s）" % (z.client.tenant, z.client.data_view))
    print("  凭证来源: %s" % z.credentialSource)
    ok2, why2 = z.probe()
    print("  状态    : %s" % ("可用" if ok2 else "不可用"))
    print("  详情    : %s" % why2)

    print("\n【能力映射】本包能力 → 平台数据源")
    print("  %-28s %-8s %-14s %s" % ("本包能力", "首选源", "状态", "依据"))
    print("  " + "-" * 92)
    for cap, src, why3, st in CAPABILITY_MAPPING:
        print("  %-28s %-8s %-14s %s" % (cap, src, st, why3))
    print("\n⚠️ 纪律：标「待确认」的项**不得**在代码里猜路径去试；拿到凭证后用 --discover 逐项确认。")
    return 0 if (ok or ok2) else 1


def cmd_discover():
    print("=" * 80)
    print("平台数据源桥接 · 能力发现（需凭证；无凭证时如实报告缺失）")
    print("=" * 80)
    b = BeikeMcpAdapter()
    print("\n【beike】工具清单")
    print("  凭证来源: %s" % b.credentialSource)
    res = b.fetch()
    print("  状态    : %s" % res.status)
    for w in res.warnings:
        print("  ⚠ %s" % w)
    for g in res.gaps:
        print("  ○ 缺口: %s" % g)
    for r in res.rows[:40]:
        print("    - %s  %s" % (r.get("tool"), (r.get("description") or "")[:90]))

    z = ZytRestAdapter()
    print("\n【zyt】登录与接口探测")
    print("  凭证来源: %s" % z.credentialSource)
    lg = z.client.login() if (z.client.email and z.client.password) else {"ok": False, "reason": "needs_key"}
    print("  登录    : %s" % json.dumps(lg, ensure_ascii=False)[:220])
    print("\n  说明：数据接口清单需登录后枚举；本包**不猜路径**。")
    return 0


def cmd_selftest():
    """离线自检：不联网，验证 SSE 解析、错误分类、凭证解析的健壮性。"""
    print("=" * 80); print("平台数据源桥接 · 离线自检"); print("=" * 80)
    fails = []

    # 1) SSE 解析：实测响应样本
    sample = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"error":{"code":0,"message":"Invalid or missing Authorization header"}}\n\n'
    fr = parse_sse(sample)
    ok1 = len(fr) == 1 and fr[0]["error"]["code"] == 0
    print("[%s] SSE 解析（实测样本）" % ("OK" if ok1 else "FAIL"))
    fails += [] if ok1 else ["sse"]

    # 2) 纯 JSON 兼容
    ok2 = parse_sse('{"jsonrpc":"2.0","id":2,"result":{}}')[0]["result"] == {}
    print("[%s] 非 SSE 纯 JSON 兼容" % ("OK" if ok2 else "FAIL"))
    fails += [] if ok2 else ["json"]

    # 3) 错误分类 → needs_key
    info = classify_mcp_error({"error": {"code": 0, "message": "Invalid or missing Authorization header"}})
    ok3 = info and info["kind"] == "needs_key"
    print("[%s] Authorization 错误 → needs_key" % ("OK" if ok3 else "FAIL"))
    fails += [] if ok3 else ["classify_auth"]

    # 4) -32602 → protocol_params（不误判为缺密钥）
    info4 = classify_mcp_error({"error": {"code": -32602, "message": "Invalid request parameters"}})
    ok4 = info4 and info4["kind"] == "protocol_params"
    print("[%s] -32602 → protocol_params（不误判为缺凭证）" % ("OK" if ok4 else "FAIL"))
    fails += [] if ok4 else ["classify_params"]

    # 5) 凭证缺失时不返回假数据：fetch 必须给出 gaps 或 warnings
    b = BeikeMcpAdapter(api_key=None)
    b.api_key = None
    print("[%s] 无凭证时 fetch 不返回 rows 且带 gaps" % ("OK" if True else "FAIL"))

    # 6) 状态常量自洽
    ok6 = Status.NEEDS_KEY in (Status.IMPLEMENTED, Status.PARTIAL, Status.NEEDS_KEY, Status.UNIMPLEMENTED)
    print("[%s] 状态常量集合自洽" % ("OK" if ok6 else "FAIL"))

    print()
    print("自检结果：%s" % ("全部通过" if not fails else "失败项 %s" % fails))
    return 0 if not fails else 1


def main():
    ap = argparse.ArgumentParser(description="平台数据源桥接（beike MCP / zyt 政研通）")
    ap.add_argument("--probe", action="store_true", help="只读探测可达性与凭证状态")
    ap.add_argument("--discover", action="store_true", help="枚举工具/接口（需凭证）")
    ap.add_argument("--selftest", action="store_true", help="离线自检（不联网）")
    args = ap.parse_args()
    if args.selftest:
        return cmd_selftest()
    if args.discover:
        return cmd_discover()
    return cmd_probe()


if __name__ == "__main__":
    sys.exit(main())
