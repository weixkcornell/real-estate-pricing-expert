#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
发布工具 —— 推送版本 + 断言协作授权，二合一。

为什么把这两件事绑在一起：
    「推送了新版本，但协作授权掉了」是一种静默失败——推送成功会给人「发布完成」的错觉，
    而授权失效不会报错、不会通知。把授权断言**内建进发布流程**，才能保证
    「无论怎么更新版本，授权始终在」。

    因此本脚本的正常路径 = 推送 + 授权断言，两步都必须成功才算发布完成（退出码 0）。

    GitHub 的协作邀请 7 天未接受即过期，故授权断言是**幂等**的：已生效则只核验；
    快过期或缺失则自动重新签发。

用法：
    # 标准发布（自动取 header 版本号 + 变更摘要生成提交信息）
    python3 tools/release.py --message "feat: xxx v0.2.1"

    # 只推送、只断言授权
    python3 tools/release.py --no-access
    python3 tools/release.py --no-push

    # 干跑：列出将要变更的文件，不实际提交
    python3 tools/release.py --message "..." --dry-run

令牌来源：GITHUB_TOKEN 环境变量 → git credential helper。
本脚本用 GitHub REST API 完成推送，不依赖 git 的 HTTPS 通道——
本环境下 git HTTPS 曾被代理拦截（502），API 通道可用，故以此为主路径。

退出码：0 成功；非 0 见输出。
"""

import argparse
import base64
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from grant_access import (DEFAULT_PERMISSION, DEFAULT_REPO, DEFAULT_USER,  # noqa: E402
                          api, get_state, has_write, issue_invite, resolve_token,
                          revoke_invite, REFRESH_THRESHOLD_DAYS)

PACK_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "__pycache__"}
SKIP_FILES = {".DS_Store", "Thumbs.db"}


# ---------------------------------------------------------------- 工具函数
def git_blob_sha(data: bytes) -> str:
    """复算 git blob sha：sha1('blob <len>\\0' + content)。用于与远端比对，跳过未变更文件。"""
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def collect_files(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn in SKIP_FILES or fn.endswith(".pyc"):
                continue
            full = os.path.join(dirpath, fn)
            out.append((os.path.relpath(full, root).replace("\\", "/"), full))
    out.sort()
    return out


# ---------------------------------------------------------------- 推送
def push(owner, repo, branch, root, message, token, dry_run=False):
    st, ref = api("GET", f"/repos/{owner}/{repo}/git/ref/heads/{branch}", token)
    if st != 200:
        print("✗ 读取分支 %s 失败（HTTP %s）" % (branch, st))
        return False
    head = ref["object"]["sha"]
    st, head_commit = api("GET", f"/repos/{owner}/{repo}/git/commits/{head}", token)
    base_tree = head_commit["tree"]["sha"]

    st, remote_tree = api("GET", f"/repos/{owner}/{repo}/git/trees/{base_tree}?recursive=1", token)
    remote = {x["path"]: x["sha"] for x in remote_tree.get("tree", []) if x["type"] == "blob"}

    files = collect_files(root)
    entries, changed, unchanged = [], [], 0
    for rel, full in files:
        with open(full, "rb") as f:
            raw = f.read()
        sha = git_blob_sha(raw)
        entries.append((rel, raw, sha))
        if remote.get(rel) == sha:
            unchanged += 1
        else:
            changed.append(rel)

    deleted = sorted(set(remote) - {rel for rel, _, _ in entries})
    print("本地文件 %d 个 | 未变更 %d | 变更/新增 %d | 远端多余 %d"
          % (len(files), unchanged, len(changed), len(deleted)))
    for p in changed:
        print("   M %s" % p)
    for p in deleted:
        print("   D %s" % p)

    if dry_run:
        print("\n· dry-run：未提交")
        return True

    if not changed and not deleted:
        print("\n· 无内容变更，跳过提交（远端已是最新）")
        return True

    tree = []
    for rel, raw, sha in entries:
        if rel not in changed:
            tree.append({"path": rel, "mode": "100644", "type": "blob", "sha": sha})
            continue
        try:
            content, enc = raw.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            content, enc = base64.b64encode(raw).decode(), "base64"
        st, blob = api("POST", f"/repos/{owner}/{repo}/git/blobs", token,
                       {"content": content, "encoding": enc})
        if st not in (200, 201):
            print("✗ blob 创建失败 %s（HTTP %s）" % (rel, st))
            return False
        tree.append({"path": rel, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    for p in deleted:
        tree.append({"path": p, "mode": "100644", "type": "blob", "sha": None})

    st, newtree = api("POST", f"/repos/{owner}/{repo}/git/trees", token,
                      {"base_tree": base_tree, "tree": tree})
    if st not in (200, 201):
        print("✗ tree 创建失败（HTTP %s）：%s" % (st, newtree.get("message")))
        return False

    st, commit = api("POST", f"/repos/{owner}/{repo}/git/commits", token,
                     {"message": message, "tree": newtree["sha"], "parents": [head]})
    if st not in (200, 201):
        print("✗ commit 创建失败（HTTP %s）：%s" % (st, commit.get("message")))
        return False

    st, _ = api("PATCH", f"/repos/{owner}/{repo}/git/refs/heads/{branch}", token,
                {"sha": commit["sha"], "force": False})
    if st not in (200, 201):
        print("✗ 更新分支引用失败（HTTP %s）" % st)
        return False

    st, c = api("GET", f"/repos/{owner}/{repo}/commits/{branch}", token)
    st2, tr = api("GET", f"/repos/{owner}/{repo}/git/trees/{branch}?recursive=1", token)
    nblob = len([x for x in tr.get("tree", []) if x["type"] == "blob"])
    print("✓ 推送完成 | HEAD %s | %s | 远端文件 %d"
          % (c["sha"][:8], c["commit"]["message"].splitlines()[0], nblob))
    return True


# ---------------------------------------------------------------- 授权断言
def assert_access(owner, repo, user, email, permission, token):
    """幂等授权断言：已生效→核验；将过期/缺失→重新签发。返回 True/False。"""
    st = get_state(owner, repo, user, token)
    if not st["ok"]:
        print("✗ 授权断言失败：%s" % st["error"])
        return False
    if has_write(st):
        m = st["member"]
        print("✓ 授权已生效：%s 已是协作者（%s）" % (m["login"], m.get("role_name")))
        return True

    iv = st.get("invite")
    if iv and not iv.get("expired") and (iv.get("days_left") or 0) >= REFRESH_THRESHOLD_DAYS:
        print("✓ 授权有效：%s 邀请在途（write，剩余 %.1f 天，编号 %s）"
              % (user, iv["days_left"], iv["id"]))
        print("  待接受：https://github.com/%s/%s/invitations（%s）" % (owner, repo, email))
        return True

    if iv and iv.get("id"):
        revoke_invite(owner, repo, iv["id"], token)
    st2, body = issue_invite(owner, repo, user, permission, token)
    if st2 not in (200, 201):
        print("✗ 重新签发失败（HTTP %s）：%s" % (st2, body.get("message")))
        return False
    after = get_state(owner, repo, user, token)
    iv2 = after.get("invite")
    if has_write(after) or iv2:
        print("✓ 授权已重新签发：%s（%s）" % (user, permission))
        return True
    print("✗ 签发后复核未通过")
    return False


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(description="推送版本 + 断言协作授权")
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--user", default=DEFAULT_USER, help="协作者登录名")
    ap.add_argument("--email", default="scubiry@gmail.com")
    ap.add_argument("--permission", default=DEFAULT_PERMISSION,
                    choices=["pull", "triage", "push", "maintain", "admin"])
    ap.add_argument("--branch", default="main")
    ap.add_argument("--root", default=PACK_ROOT, help="包根目录，默认脚本上级目录")
    ap.add_argument("--message", "-m", default="", help="提交信息")
    ap.add_argument("--message-file", default="", help="从文件读取提交信息")
    ap.add_argument("--no-push", action="store_true", help="跳过推送，只做授权断言")
    ap.add_argument("--no-access", action="store_true", help="跳过授权断言，只推送")
    ap.add_argument("--dry-run", action="store_true", help="只列出变更，不提交")
    args = ap.parse_args()

    owner, _, repo = args.repo.partition("/")
    token, src = resolve_token()
    if not token:
        print("✗ 未获取到 GitHub 令牌（设置 GITHUB_TOKEN 或登录 git credential helper）")
        return 1

    print("=" * 66)
    print("发布 · %s/%s（分支 %s）" % (owner, repo, args.branch))
    print("令牌来源：%s | 包目录：%s" % (src, args.root))
    print("=" * 66)

    ok = True

    if not args.no_push:
        msg = args.message
        if args.message_file:
            with open(args.message_file, encoding="utf-8") as f:
                msg = f.read().strip()
        if not msg:
            print("✗ 缺少提交信息（--message 或 --message-file）")
            return 1
        print("\n【1/2】推送")
        if not push(owner, repo, args.branch, args.root, msg, token, args.dry_run):
            ok = False

    if not args.no_access:
        print("\n【%s】协作授权断言 —— %s（%s）"
              % ("2/2" if not args.no_push else "1/1", args.user, args.email))
        if not assert_access(owner, repo, args.user, args.email, args.permission, token):
            ok = False

    print("\n" + "=" * 66)
    if ok:
        print("✓ 发布完成：版本已推送，协作授权在册（write）")
        print("  已接受的协作者不会过期；若对方尚未接受，邀请 7 天内有效，")
        print("  下一次发布（或定期执行 tools/grant_access.py）会自动续期。")
    else:
        print("✗ 发布未完成：见上方错误。发布以「推送 + 授权」双双成功为准。")
    print("=" * 66)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
