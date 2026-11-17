# Sprint 51 · xbot_feed 上游 git 临时网络失败的有限次数退避重试

> **目标版本** v1.0.95 · **分支** `feat/xbot-git-retry-v1.0.95` · **base** `origin/main @ 0aef205`
> （2026-11-17 同步：dad 合了 PR #217 / #218 / #219 / #220 / #222，分支已 merge 到 `0aef205`）
> **sprint** 51 · **追踪 id** 26111606 · **状态** 实现完成，待 dad 下一道 GO 才 commit/push/PR

## 用户 GO 原文

> dad 2026-11-16 16:49（Asia/Shanghai）：「那启动修复，既然不稳定就多重试一下。」

## 问题

生产 8900 `/fetch` 上 xbot_feed 抓取失败 `0 篇 0/0 (0s)`：

```
RuntimeError: xbot-feed: git clone/pull failed — cannot read upstream feed
```

底层 stderr（`/private/tmp/moni-8900-v1.0.52.log` 行 3 / 行 612，同一签名）：

```
fatal: unable to access 'https://github.com/mariusiaowego-commits/xbot-feed.git/':
LibreSSL SSL_connect: SSL_ERROR_SYSCALL in connection to github.com:443
```

16:33–16:46 只读诊断定案：**代理隧道间歇性 TLS 重置**。

- 同一条 repo-local 代理路径（`http://127.0.0.1:7897`）上 `git ls-remote` 15 次采样 **OK=12 / FAIL=3**
- `google.com` 与 `api.github.com` 经同一代理也各出现 1 次失败 ⇒ **隧道整体抖动**，不是 GitHub 专项
- 代理配置在位（`.git/config` mtime 11-11 13:39），CONNECT 200 后 TLS 断；**不是缺 proxy env**
- `_sync_clone`（`xbot_feed.py:721-810`）对 `git pull --ff-only` **单次尝试无重试**
- 生产最后成功 pull：**2026-11-12 10:57:54 CST**，此后 xbot_feed 新文章 0 入库

## 改法

只改 `moni/fetch/sources/xbot_feed.py`，加一层**有限次数退避重试**。

### 判据：stderr 文本为主，rc 只判成功

git 的 rc 在网络类失败下**不携带语义** —— TLS 握手失败、仓库不存在、认证失败**都是 128**。
只看 rc 会把「仓库被删了」和「隧道抖了一下」当成同一类，对永久错误做无谓重试。
stderr 才有语义（且这些文本稳定）。

三层求值：

| 层 | 输入 | 动作 |
|---|---|---|
| L1 | `rc == 0` | 成功，不重试（stderr 里可能有无关 warning） |
| L2 | stderr 命中**永久签名** | 不重试，立即失败 |
| L3 | stderr 命中**临时签名** | 重试（预算内） |
| L4 | 都不命中（未知） | 不重试，立即失败（保守：宁可少重试一次） |

**永久签名优先** —— 一条 stderr 同时含临时与永久签名时判不重试。

### 参数

| 参数 | 值 | 依据 |
|---|---|---|
| 总尝试 | **3**（1 原始 + 2 重试） | 对齐 `bestblogs.py:419` 的 `for attempt in (1,2,3)` |
| 退避 | **2s → 4s**（合计 6s） | 同 bestblogs 线性退避 |
| 单次 git 超时 | `min(git_timeout=120, 剩余预算)` | **必须取 min**：否则 3×120+6=366s ≫ 90s，且吃掉 job 级 600s 预算的 61% |
| 总时限 | **90s** | 必须 < `JOB_TIMEOUT_SECONDS`（600） |

### 签名表

**可重试（临时网络/TLS）**：`SSL_ERROR_SYSCALL` / `Connection reset` / `Connection timed out` /
`Could not resolve host` / `couldn't resolve host` / `Temporary failure in name resolution` /
`early EOF` / `GnuTLS recv error` / `GnuTLS error` / `gnutls_handshake` / `curl 28` / `curl 35` / `curl 52` / `curl 56`

**不重试（永久）**：`not fast-forward` / `Authentication failed` / `could not read Username` /
`Repository not found` / `Permission denied` / `host key verification failed` /
`local changes would be overwritten`

匹配前**先脱敏**（`user:token@` 里的字符会干扰子串匹配，同时也避免 token 落日志）。

## 副作用处理

- **clone 失败留半成品目录** → 每次重试**前**清理（否则下一次撞 `destination path already exists`）；最终失败也清理
- **pull 失败不动原 clone** —— `git pull --ff-only` 天然安全，pull 分支不挂任何 cleanup
- **不死锁** —— 新代码内无任何锁构造（AST 断言 + 并发用例）
- **取消语义不变** —— 现有 cancel 只是 DB 标记（无 in-flight 信号），本次**不引入**中断点；退避合计 ≤ 6s，取消后最坏多跑 6s

## 范围外（明确不做）

改代理节点/代理配置（dad 本次 GO 只授权重试）、改 UI、改其它源、改作者屏蔽逻辑、
改 `git pull --ff-only` 语义、改 max_days 去重、动生产 DB、重启 8900、对真实上游 pull/fetch/clone、
commit/push/PR/merge/tag/部署。

## 测试

| 文件 | 覆盖 |
|---|---|
| `tests/test_xbot_feed_git_retry.py` | 短暂失败后成功 / 打满上限 / 永久错误不重试 / clone 副作用 / pull 不动原 clone / 预算 / 日志 |
| `tests/test_xbot_feed_git_retry_signatures.py` | 表 A 每条签名独立用例 / 表 B 每条独立用例 / 未知不重试 / 跨类冲突 / rc 语义证明 |
| `tests/test_xbot_feed_git_retry_guards.py` | 退避序列 [2,4] / 墙钟上下界（绝对常量）/ 无锁 AST / 并发 / argv 逐字相等 / max_days 与作者屏蔽回归护栏 / 改动范围护栏 |
| `tests/test_xbot_feed_git_retry_safety.py` | **P0 数据丢失护栏**（用户文件 / news.db 必须存活）/ 补齐的签名用例 / 元护栏 / R-AB-2 / R-MD-2 / R-FF-3（走 `_sync_clone` 真路径 + 可区分性证明）/ NC-8 载荷 |

**纪律**：全程不联网（git 操作全在 `tmp_path` 本地 bare repo）；不依赖真实 sleep（退避可注入）；
不硬编码本机绝对路径；不用概率型断言。

**负控**：NC-1..NC-25 逐条注入 → 对应测试必须变红，**22/22 OK**（MISS=0 / SKIP=0）。

**最终验证**（round-2 后）：专项 **67 passed**；全量 **1941 passed / 2 skipped / 0 failed**（144.09s，串行）。

## 回归护栏（三项逐字未变）

- `git pull --ff-only` —— argv 列表**完全相等** `["git", "pull", "--ff-only"]`；clone argv 保留 `--quiet` 与 `--`
- `max_days` 去重 —— `_filter_by_max_days` 函数体与基线 commit **逐字相等**（AST 行号切片比对）；默认值仍 30
- 作者屏蔽 —— `author_blocked` 仍是独立标量 int，不并入 `threshold_dropped`
