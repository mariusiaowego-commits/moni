# Sprint-15 PLAN — demo 优化搬运 + artifacts 整肃 + 演示物料 (2026-08-19)

> 背景: v1.0.35.1 已 ship, prod 改了 UI 视觉/间距/分类列/分享产物,但 demo HTML (`docs/moni-web-showcase.html`) 还停留在 v1.0.35 时期,没体现 prod 改动。同时 prod `data/artifacts/` 是真生产目录,但 moni 根目录还有 `output/` `share_output/` `infographic/` `infographics/` 4 个残留目录,混乱。

## 目标 (3 路并行)

### A. artifacts 整肃 (artifacts-curator pane)
- 生产真相: `data/artifacts/` (144 文件, 17.2MB, 最新 08-19) + `data/briefings/` (3 文件, 08-12) 是生产目录 (代码 `moni/share/artifacts.py:25` + `moni/share/worker.py:36` 写死)
- 残留: 根目录 `output/` `share_output/` `infographic/` `infographics/` (均不是 prod 用)
- 整肃: 把残留目录 **mv 到 `~/Documents/moni-archive-20260819/`** 不删 (防误删真物料), 加 `.archived` 标记; 在 moni 根目录写 `docs/ARTIFACTS-STRUCTURE.md` 说明哪里是真目录
- **不碰**: data/artifacts, data/briefings, moni 业务代码

### B. demo 主文件搬运 (主线 blue-owl, 我做)
- 1-3: v1.0.35.1 视觉 + 间距 + 分类列 搬进 demo
- 4.1: articles/pinned 删除按钮 + 二次确认 (demo 化: 点击弹 "演示提示" 不真删)
- 4.2: CRUD 标题模糊搜索框
- 5.1: 分享产物点开 → 开新页 (`docs/moni-showcase-artifacts/<file>`) 展示 5 种物料 (接 C 的输出)
- **不碰**: build 脚本 / fetch 拦截 / 其他 demo 逻辑

### C. 演示物料生成 (demo-builder pane)
- 从 `data/artifacts/` 挑 5 种格式的真物料: `info_graphic_png` `html_landing` `pdf` `report_png` `plain_text`(或 `markdown`/`rich_text`)
- 每个格式生成 1 个独立静态 HTML 展示页, 路径 `docs/moni-showcase-artifacts/<slug>.<ext>` 或 `.<html>`
- HTML 包含: 标题 + "演示"标签 + 物料正文/图片内嵌
- **不碰**: demo 主文件, moni 业务代码

## 不在范围

- 不重跑 build 脚本 (vendor 下载 SSL EOF, 路径不稳)
- 不改 fetch 拦截 / 演示 modal 流程 / 立即抓取演示
- 不删 demo HTML 的 working tree 已有手工润色 (备份在 `~/Documents/moni-backups/moni-web-showcase-wt-v1035.html`)
- 不动 moni 后端 API / DB / 测试

## 验收

- A: 根目录残留目录全 mv 到 archive; 写了 STRUCTURE.md
- B: demo HTML 含 v1.0.35.1 优化标记 + 演示删除弹窗 + CRUD 搜索 + 5.1 新页引用
- C: 5 个独立 HTML 物料展示页, 在 demo 里点开能正常打开
- pytest 不退化 (738+ passed)

## 并发约束

- A 不碰 demo HTML (独立)
- C 不碰 demo HTML (独立, 只输出到 docs/moni-showcase-artifacts/)
- B (我) 写 demo HTML + 接 5.1 引用
- 3 路 git 都提交在 main (独立 commit)

## 风险

- demo HTML 改动大, 工作树改动可能跟 working tree 现有 dirty 冲突 → 先 git stash working tree demo 文件再动手
- herdr pane 派任务超时 / 跑偏 → 我盯 pane 输出, 跑偏 kill 重派