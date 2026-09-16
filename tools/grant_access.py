#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
协作授权保活工具 —— 确保指定协作者始终持有 write 权限。

为什么需要这个脚本：
    GitHub 的协作邀请有两个易失特性，会让「已授权」这件事悄悄失效——
      ① **邀请 7 天未接受即过期**（过期后权限不再可用，且不会主动通知仓库管理员）；
      ② 邀请被接受后若对方账号改名/被移除，权限同样消失。
    因此「开通一次」不等于「一直开着」。本脚本把授权做成**幂等操作**：
    可重复执行，按需签发/刷新，并在结束后给出可核验的状态。

用法：
    # 默认：检查 + 按需刷新（邀请剩余有效期不足 3 天或已过期时重新签发）
    python3 tools/grant_access.py

    # 强制重新签发（撤销旧邀请后重发，用于确保邮件送达）
    python3 tools/grant_access.py --force

    # 仅检查，不做任何变更
    python3 tools/grant_access.py --check

    # 覆盖默认目标
    python3 tools/grant_access.py --repo other-owner/other-repo --user someone --permission push

令牌来源（按优先级）：
    1. 环境变量 GITHUB_TOKEN
    2. git credential helper（git credential fill protocol=https host=github.com）
   令牌需具备 repo 权限（经典令牌）或对该仓库的 Administration 写权限（细粒度令牌）。

退出码：
    0 = 授权已就绪（已接受 或 有效邀请在途）
    1 = 授权未就绪（令牌缺失/权限不足/接口失败）
"""

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"

# ---- 默认目标：本专家包的既有授权对象，改动需同步 README「发布规程」----
DEFAULT_REPO = "weixkcornell/real-estate-pricing-expert"
DEFAULT_USER = "scubiry-glitch"
DEFAULT_PERMISSION = "push"          # push == write（可推送/建分支），GitHub API 中二者等价
DEFAULT_EMAIL = "scubiry@gmail.com"  # 仅用于提示，不作为接口参数
REFRESH_THRESHOLD_DAYS = 3           # 邀请剩余有效期低于此值即刷新


# ---------------------------------------------------------------- 令牌与请求
def resolve_token():
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok.strip(), "环境变量"
    for exe in (r"C:\Program Files\Git\cmd\git.exe", "git"):
        try:
            env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
            p = subprocess.run([exe, "credential", "fill"],
                               input="protocol=https\nhost=github.com\n\n",
                               capture_output=True, text=True, timeout=60, env=env)
        except Exception:
            continue
        for line in p.stdout.splitlines():
            if line.startswith("password="):
                return line[9:].strip(), "git credential helper"
    return None, None


def api(method, path, token, body=None):
    req = urllib.request.Request(API + path, method=method)
    req.add_header("Authorization", "token " + token)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "real-estate-pricing-expert/access-guard")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
        req.data = data
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode() or "{}"
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode() or "{}"
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:300]}
    except Exception as e:
        return 0, {"error": str(e)}


# ---------------------------------------------------------------- 状态查询
def get_state(owner, repo, user, token):
    st, repo_info = api("GET", f"/repos/{owner}/{repo}", token)
    if st != 200:
        return {"ok": False, "error": f"仓库不可访问（HTTP {st}）：{repo_info.get('message', repo_info)}"}

    state = {
        "ok": True,
        "repo": repo_info.get("full_name"),
        "visibility": "private" if repo_info.get("private") else "public",
        "pushed_at": repo_info.get("pushed_at"),
        "member": None,
        "invite": None,
    }

    st, cs = api("GET", f"/repos/{owner}/{repo}/collaborators?affiliation=all", token)
    if st == 200 and isinstance(cs, list):
        for c in cs:
            if (c.get("login") or "").lower() == user.lower():
                state["member"] = {"login": c.get("login"), "permissions": c.get("permissions"),
                                   "role_name": c.get("role_name")}

    st, inv = api("GET", f"/repos/{owner}/{repo}/invitations", token)
    if st == 200 and isinstance(inv, list):
        for i in inv:
            if (i.get("invitee", {}).get("login") or "").lower() == user.lower():
                created = i.get("created_at")
                days = None
                if created:
                    try:
                        c = _dt.datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc)
                        days = (_dt.datetime.now(_dt.timezone.utc) - c).total_seconds() / 86400.0
                    except Exception:
                        pass
                state["invite"] = {"id": i.get("id"), "permissions": i.get("permissions"),
                                   "created_at": created, "expired": i.get("expired"),
                                   "age_days": days,
                                   "days_left": (7.0 - days) if days is not None else None}
    return state


def has_write(state):
    """已接受为成员且具备 write/push。"""
    m = state.get("member")
    if not m:
        return False
    p = m.get("permissions") or {}
    return bool(p.get("push") or p.get("maintain") or p.get("admin"))


# ---------------------------------------------------------------- 授权动作
def revoke_invite(owner, repo, invite_id, token):
    st, _ = api("DELETE", f"/repos/{owner}/{repo}/invitations/{invite_id}", token)
    return st in (204, 200)


def issue_invite(owner, repo, user, permission, token):
    st, body = api("PUT", f"/repos/{owner}/{repo}/collaborators/{user}", token,
                   {"permission": permission})
    return st, body


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(description="确保协作者持有 write 权限（幂等，可重复执行）")
    ap.add_argument("--repo", default=DEFAULT_REPO, help="owner/repo，默认 %s" % DEFAULT_REPO)
    ap.add_argument("--user", default=DEFAULT_USER, help="GitHub 登录名，默认 %s" % DEFAULT_USER)
    ap.add_argument("--email", default=DEFAULT_EMAIL, help="对方邮箱（仅提示用）")
    ap.add_argument("--permission", default=DEFAULT_PERMISSION, choices=["pull", "triage", "push", "maintain", "admin"])
    ap.add_argument("--force", action="store_true", help="撤销旧邀请并重新签发")
    ap.add_argument("--check", action="store_true", help="仅检查，不做变更")
    args = ap.parse_args()

    owner, _, repo = args.repo.partition("/")
    if not owner or not repo:
        print("✗ --repo 需为 owner/repo 形式")
        return 1

    token, src = resolve_token()
    if not token:
        print("✗ 未获取到 GitHub 令牌。请设置 GITHUB_TOKEN，或先登录 git credential helper。")
        return 1

    print("=" * 62)
    print("协作授权保活 · %s/%s → %s（%s）" % (owner, repo, args.user, args.email))
    print("令牌来源：%s" % src)
    print("=" * 62)

    state = get_state(owner, repo, args.user, token)
    if not state["ok"]:
        print("✗ %s" % state["error"])
        return 1

    print("仓库      : %s（%s）" % (state["repo"], state["visibility"]))
    print("最后推送  : %s" % state["pushed_at"])

    if state["member"]:
        print("成员状态  : 已是协作者 | 角色 %s | 权限 %s"
              % (state["member"]["role_name"], state["member"]["permissions"]))
    else:
        print("成员状态  : 尚未成为协作者")

    iv = state["invite"]
    if iv:
        left = iv.get("days_left")
        print("邀请状态  : 在途 | 权限 %s | 编号 %s | 发出 %s | 剩余 %.1f 天"
              % (iv["permissions"], iv["id"], iv["created_at"], left if left is not None else -1))
    else:
        print("邀请状态  : 无在途邀请")

    # ---- 判定是否需要动作 ----
    if has_write(state):
        print("\n✓ 授权已生效（对方已接受邀请，write 权限在册）")
        print("  说明：已接受的协作者不会过期，无需再次签发。")
        return 0

    if args.check:
        print("\n· 仅检查模式：未做任何变更")
        return 0 if iv and not iv.get("expired") else 1

    need_refresh = args.force or (iv is None) or bool(iv.get("expired")) \
        or (iv.get("days_left") is not None and iv["days_left"] < REFRESH_THRESHOLD_DAYS)

    if not need_refresh:
        print("\n✓ 有效邀请在途，且剩余有效期充足（阈值 %d 天），无需刷新" % REFRESH_THRESHOLD_DAYS)
        print("  提示：对方接受后即永久生效；如未接受，GitHub 会在 7 天时使其失效。")
        return 0

    reason = "强制刷新" if args.force else ("邀请已过期" if (iv and iv.get("expired"))
                                       else ("无在途邀请" if iv is None else "剩余有效期不足"))
    print("\n→ 需要动作：%s" % reason)

    if iv and iv.get("id"):
        if revoke_invite(owner, repo, iv["id"], token):
            print("  已撤销旧邀请 %s（避免新旧邀请并存导致邮件重复/歧义）" % iv["id"])
        else:
            print("  · 旧邀请撤销失败（可能已被接受或已失效），继续签发")

    st, body = issue_invite(owner, repo, args.user, args.permission, token)
    if st not in (200, 201):
        print("✗ 签发失败 HTTP %s：%s" % (st, body.get("message", body)))
        if st == 403:
            print("  令牌缺少仓库管理权限（需经典令牌 repo 权限，或细粒度令牌 Administration:write）")
        return 1
    print("  ✓ 已签发邀请：%s → %s" % (args.user, args.permission))

    # ---- 复核 ----
    after = get_state(owner, repo, args.user, token)
    iv2 = after.get("invite")
    print("\n--- 复核 ---")
    if has_write(after):
        print("✓ 已具备 write 权限（对方已接受）")
    elif iv2:
        print("✓ 有效邀请在途 | 权限 %s | 编号 %s | 发出 %s"
              % (iv2["permissions"], iv2["id"], iv2["created_at"]))
        print("  待接受链接：https://github.com/%s/%s/invitations" % (owner, repo))
        print("  ⚠ 邀请为双向确认：需对方接受后才出现在协作者列表（7 天内有效）")
    else:
        print("✗ 复核未发现成员或邀请，请检查令牌权限")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
