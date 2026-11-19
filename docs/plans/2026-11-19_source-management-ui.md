# 信源管理改版（v1.0.109）· sprint 26111901

> 完整 sprint 文档（prd / plan / tech-spec / test-plan / verify）在 Obsidian：
> `tqob/05-Coding/project-moni/sprints/sprint-26111901-source-management/`
> 本文件是仓库内的索引 + 变更清单。

## 背景

dad 实测生产 `/fetch` 页提了 7 条：弹窗多重滚动条 + 滚轮穿透 + 完成态占位过大、
「抓取」措辞、同步中关弹窗找不回进度、信源隐藏未做、执行状态区 slop、
刷新按钮底色描边、三个信源 logo 与品牌无关。

## 变更清单

| 模块 | 改动 |
|---|---|
| 文案 | `index.html` 45 处 + `app.js` 43 处：动词「抓取」→「同步」，标题「抓取管理」→「信源管理」 |
| 数据 | `052_source_hidden.sql`：`sources.is_hidden`（默认 0）；`GET/POST/PUT /api/config/sources` 透传 |
| 展示 | 信源管理页信源列表 + 同步链按 `is_hidden` 过滤；`/config` 行内眼睛按钮切换，隐藏行标「已隐藏」 |
| 弹窗 | `body.modal-locked` 页面滚动锁 + `overscroll-behavior: contain`；完成时 `#particleStage.is-complete` → 72px（`source-modal.css` 覆盖 main.css 的 128px 基线） |
| 执行状态区 | 终态任务不再 6 秒自动消失（改「清除」）；上限 12 条；行内「进度 / 停止 / 清除」 |
| 样式 | 独立 `css/source-ops.css`、`css/source-modal.css`；全局 `.icon-btn` 基线（去浏览器默认灰底描边） |
| logo | 新增 `bank_feed` / `chinese_independent_blogs` / `follow_builders` 三张品牌图标；15 张底板由纯黑提亮为 `#2E2A24` |

## 测试

- `tests/test_source_hidden.py`（新增 5 条）
- `tests/test_fetch_config.py` / `test_source_url_redaction.py` / `test_frontend_render_hygiene.py` 回归
- CDP 实机验收（1440×900）：见 Obsidian `test-plan` / `verify`

## 分支

`feat/source-management-ui`（基线 `26a3502` / v1.0.105）
