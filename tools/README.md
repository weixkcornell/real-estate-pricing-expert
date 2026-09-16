# 发布工具（tools/）

本目录放**发布与授权**的运维工具。它们不属于定价方法论本身，但保证方法论能被可靠交付。

---

## 一、为什么需要 `grant_access.py`

GitHub 的协作邀请有两个**易失特性**，会让"已授权"这件事静默失效：

| 情况 | 后果 | 会不会通知你 |
|---|---|---|
| 邀请**7 天**未被接受 | 邀请自动过期，权限不再可用 | ❌ 不会 |
| 已接受的协作者被改名 / 移除 | 权限消失 | ❌ 不会 |
| 换了新仓库 | 旧授权不跟随 | ❌ 不会 |

所以「开通一次」≠「一直开着」。`grant_access.py` 把授权做成**幂等操作**：可重复执行，按需签发或续期，并在结束时给出可核验的状态。

**本包的固定授权对象**（改动需同步 `README.md` 第八节）：

| 项 | 值 |
|---|---|
| 仓库 | `weixkcornell/real-estate-pricing-expert` |
| 协作者 | `scubiry-glitch`（scubiry@gmail.com） |
| 权限 | `write`（`push`） |

---

## 二、`release.py`：推送 + 授权断言，二合一

「推送成功」会给人一种"发布完成"的错觉，但授权可能已经掉了。因此 `release.py` 把两件事**绑成一次发布**——推送和授权断言都成功，才算发布完成（退出码 0）。

它还解决了本环境的一个实际问题：**git 的 HTTPS 通道会被代理拦截（`CONNECT tunnel failed, response 502`）**。`release.py` 直接走 GitHub REST API（建 blob → 建 tree → 建 commit → 更新 ref），不依赖 git 传输层。

```bash
# 标准发布
python3 tools/release.py -m "feat: xxx v0.2.1"

# 干跑：只列出将要变更的文件
python3 tools/release.py -m "..." --dry-run

# 只做授权断言 / 只推送
python3 tools/release.py --no-push
python3 tools/release.py -m "..." --no-access
```

推送阶段会用 **git blob sha**（`sha1("blob <len>\0" + content)`）与远端逐文件比对，只上传真正变更的文件，并自动处理远端多余文件（删除）。因此**可以反复执行，不会产生空提交**。

---

## 三、授权断言：幂等规则

```bash
python3 tools/grant_access.py            # 检查 + 按需续期（默认）
python3 tools/grant_access.py --check    # 仅检查，不做变更
python3 tools/grant_access.py --force    # 撤销旧邀请并重新签发（确保邮件送达）
```

判定逻辑：

| 当前状态 | 动作 |
|---|---|
| 已是协作者且具备 write | 只核验，**不动作**（已接受的授权不会过期） |
| 邀请在途，剩余有效期 ≥ 3 天 | 只核验，不动作 |
| 邀请在途，剩余有效期 < 3 天 | 撤销旧邀请 → 重新签发（续期） |
| 邀请已过期 / 不存在 | 直接重新签发 |

> 阈值 3 天可在 `grant_access.py` 的 `REFRESH_THRESHOLD_DAYS` 调整。

**⚠️ 一个无法自动化的环节**：GitHub 协作邀请是**双向确认**——邀请发出后，必须由对方登录 GitHub 点 **Accept invitation** 才真正进入协作者列表。在对方接受前，`/collaborators` 接口不会返回该账号，这是正常的，不代表邀请失败。接受入口：

- 邀请邮件里的 **Accept invitation**
- 或直接访问 `https://github.com/weixkcornell/real-estate-pricing-expert/invitations`

---

## 四、令牌

按优先级取用：

1. 环境变量 `GITHUB_TOKEN` / `GH_TOKEN`
2. git credential helper（`git credential fill`，即 Windows 凭据管理器中已存的 GitHub 凭据）

需要 **经典令牌的 `repo` 权限**，或**细粒度令牌对该仓库的 Administration: write**。

### 为什么不把令牌写成仓库 Secret 让 Actions 自动跑

技术上可行，但本包**刻意不做**：仓库已给外部协作者 write 权限，而仓库内的 workflow 在 `pull_request`（同仓库分支）场景下能读到 Secrets——等于把一枚有 `repo` 权限的令牌交给了协作者。为一个"续期提醒"付这个代价不划算。

需要全自动时，推荐更小披露面的做法：新建一枚**仅限本仓库、仅 Administration 权限**的细粒度令牌，再配 Actions。

---

## 五、发布检查（每次更新版本必做）

```
1. python3 validate_pack.py                 # 包体自检（JSON / 占位符 / 结构）
2. 更新 pack.json 的 version 与 README/清单
3. python3 tools/release.py -m "<版本说明>"   # 推送 + 授权断言（一步到位）
4. 若 3 中授权断言提示"邀请在途"，提醒对方接受
```

第 3 步是**强制项**：任何版本更新都必须以「推送 + 授权在册」双成功收尾。
