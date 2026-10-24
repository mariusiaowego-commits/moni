# sprint-26 架构审计整改与 Chrome 沙箱护栏实施方案

- **日期**: 2026-10-24
- **阶段**: sprint-26 (v1.0.47 ~ v1.0.57)
- **目标仓库**: `mariusiaowego-commits/moni`
- **主要分支**: main (`8ce4f15`), feature 分支链 (#146~#153 已入库, #154~#157 跟踪中)
- **双写同步**: `tqob/05-Coding/project-moni/plans/2026-10-24_sprint-26-audit-remediation-and-chrome-guard.md`

---

## 1. 背景与目标

2026-10-23 完成针对 moni 历史架构的全面审计（产物归档于 `docs/sprint-audit-2026/`），审计暴露了包括定时任务崩溃、XSS 链接注入、并发抢占竞态、缺少 CI 门禁、危险配置扇出重写、测试篡改生产数据库、沙箱抢占前台 Chrome 进程等一系列隐患。

本轮 sprint-26 旨在集中解决上述整改项，完成 8 个核心 PR（#146 ~ #153）的代码与测试闭环，并对近期暴露的 CI 红灯、Chrome 崩溃风暴（25 个 `.ips` 崩溃包）及 Chrome Profile 目录污染开展专项治理与收尾文档补齐。

---

## 2. 实施范围 (v1.0.47 ~ v1.0.57)

### 2.1 已合并发版子项 (v1.0.47 ~ v1.0.53, #146 ~ #153)
1. **v1.0.47 (PR #146, squash `66cc665`)**: 修复 `predict_tags` 定时打标 3 处致命缺陷（SQL 查不存在列直接崩溃，历史从未跑通）。
2. **v1.0.48 (PR #147, squash `013647f`)**: 前端 safeHref URL scheme 白名单（阻断 `javascript:` 等伪协议）+ Alpine.js/GSAP 自托管（断网不白屏）。
3. **v1.0.49 (PR #148, squash `a49b035`)**: 分享队列 worker 锁改原子获取，如实上报启动结果，根除并发双跑。
4. **v1.0.50 (PR #149, squash `0c84875`)**: 搭建 GitHub Actions CI 流水线，修复首次暴露的 fetch job 测试隔离隐患。
5. **v1.0.51 (PR #151, merge `8c884b6`/`b942100`)**: 彻底移除 `PUT /api/fetch/sources` 危险扇出重写接口，封死测试篡改生产配置路径。
6. **v1.0.52 (PR #152, merge `fa0efdc`/`5aa1461`)**: 测试数据强隔离，实施 `MONI_DATA_DIR` 隔离策略与 session 级数据防篡改护栏。
7. **架构审计归档 (PR #150, squash `7d56649`, 打 tag `v1.0.52`)**: 归档 2026-09 架构审计包、10-23 复核结论与 sprint-23 计划。
8. **v1.0.53 (PR #153, squash `8ce4f15`, 打 tag `v1.0.53`)**: 沙箱环境改走 Playwright 自带 headless-shell、headed xwatch 在沙箱内 fail-fast、退避算法增强。

### 2.2 专项治理与收尾子项 (v1.0.54 ~ v1.0.57)
9. **v1.0.54 (`feature/v1.0.54-chrome-guard`)**: Chrome 守护护栏专项，治理沙箱反复拉起系统 Chrome 导致 macOS 25 次 SIGABRT 崩溃的问题。
10. **v1.0.55 (`feature/v1.0.55-ci-green`)**: CI 修绿专项，修复 Python 解释器硬编码、Playwright 依赖与 Pillow 缺失，确保 GitHub Actions 全绿。
11. **v1.0.56 (`feature/v1.0.56-chrome-profile-migration`)**: Chrome Profile 目录搬迁专项，将 moni 占用的 Profile 6/7 迁出至 `~/.moni/chrome/`，附对照表与安全迁移工具。
12. **v1.0.57 (`feature/v1.0.57-docs-sync`)**: 本轮收尾文档同步（STATUS、USER_GUIDE、HANDOFF、vibe-coding-log 与 plan 双写）。

---

## 3. 各子项验收标准 (Acceptance Criteria)

### AC-1: 定时打标任务 (v1.0.47)
- `predict_tags` 成功执行 SQL 查询，不再抛出 `no such column` 崩溃。
- 后台可正常产出标签预测结果，通过自动化回归测试。

### AC-2: 安全外链与离线前端 (v1.0.48)
- 模板中 11 处动态链接统一受 `safeHref` 门禁约束；点击 `javascript:` 链接被直接阻断并提示拦截。
- Alpine 3.14.1 与 GSAP 3.12.5 资源全部本地化自托管，断网环境下首屏完整渲染无空白。

### AC-3: 分享队列原子锁 (v1.0.49)
- worker 争抢锁使用原子原语，多 worker 并发启动时仅有 1 个获得锁并如实返回启动状态，杜绝同一任务被多次消费。

### AC-4: CI 门禁与环境修复 (v1.0.50 / v1.0.55)
- GitHub Actions workflow 正常执行 pytest 矩阵。
- 修掉 `worker.py::launch_worker()` 的 `/usr/local/bin/python3` 绝对路径硬编码（改为 `sys.executable`）。
- 消除 `test_36kr_browser.py` 对本地缓存的强依赖，补齐 `pyproject.toml` 的 Pillow 依赖。
- **最终标准：GitHub Actions CI 必须完全变绿。**

### AC-5: 配置接口安全收敛 (v1.0.51)
- 接口 `PUT /api/fetch/sources` 完全删除，HTTP 请求返回 405 Method Not Allowed。
- 单源修改走独立安全的指定端点，不再覆盖整个 `sources.yaml`。

### AC-6: 测试数据强隔离 (v1.0.52)
- 测试套件运行前后，真实目录 `data/news.db` 与 `config/sources.yaml` 的 sha256 校验和完全一致。
- 测试若尝试写入真实目录，session 级 fixture 立即抛出 AssertionError 阻断。

### AC-7: Chrome 守护护栏 (v1.0.54)
- 新增 `moni/fetch/chrome_guard.py`，真实检测 `CODEX_SANDBOX=seatbelt` 与 `CODEX_CI=1` 环境信号。
- 沙箱内严禁 spawn 任何系统 Chrome 进程；删除向系统 Chrome 的静默回落。
- 引入跨进程单实例锁，限制每小时最多 spawn 3 次，退避状态落盘持久化。
- `stop_chrome()` 终止前校验 cmdline，防止误杀其他 Chrome 进程。

### AC-8: Chrome Profile 目录搬迁与透明化 (v1.0.56)
- 提供 `chrome-profiles.py` 工具，明确展示 UI 名称与磁盘目录对照。
- 建立从 `Chrome/Profile 6/Default` 和 `Chrome/Profile 7` 迁移至 `~/.moni/chrome/{xwatch,daemon}` 的脚本。
- 用户个人日常 Chrome 启动不再遇到 `exit_type = Crashed`「恢复页面」提示。

---

## 4. 风险评估与回滚方案 (Rollback Strategy)

| 风险项 | 触发场景 | 影响面 | 缓解与回滚措施 |
|---|---|---|---|
| **Chrome Profile 误迁移** | 执行迁移脚本时误触用户个人常用 Profile (Profile 1/6) | 用户书签、历史或登录态损坏 | **必须执行四步安全 SOP**：<br>1. 全量冷备份源目录到 `~/.moni/backup/`<br>2. 运行 `--dry-run` 检查目标与源文件计数<br>3. 执行迁移并验证 Cookies 数据库记录数（目标必须 >= 274 条）<br>4. 自动生成一键回滚脚本 `rollback_chrome_profiles.sh`，异常时单键还原。 |
| **CI 解释器跨平台差异** | runner 运行环境 Python 软链不规范 | Actions 继续红灯 | 使用 `sys.executable` 取当前运行环境解释器，并校验有效性。 |
| **沙箱内抓取任务全阻断** | 严格禁止系统 Chrome 后无可用 headless 浏览器 | 抓取任务返回 degraded | 正确引导并预装 Playwright headless-shell，环境降级时如实记录 degraded 状态而非 crash。 |

---

## 5. 明确不在本轮范围 (Out of Scope)

以下条目经评审列为后续演进项，明确**不**在本轮 sprint-26 的交付范围内：
1. **`articles.py:394` 异常捕获类型收窄遗漏**：当前未捕获 `AttributeError` / `TypeError`，留待后续专项修复。
2. **PR #153 degraded 暴露点补充自动化测试**：沙箱降级分支的 mock 单测补强留待下一阶段。
3. **`scripts/web.sh` 适配接管 launchd `com.moni.web`**：暂由 CLI 手动 kickstart，下阶段改造脚本。
4. **`pyproject.toml` 依赖全面精确版本锁定 (pip pin)**：保持现行宽松版本约束惯例，避免产生连锁破坏。
