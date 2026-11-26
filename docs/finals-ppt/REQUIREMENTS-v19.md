# 决赛 PPT v19 · 变更记录（方向口径修正 + 逐页预览文档）

- 上一版：`决赛汇报-v18.pptx`（commit `3839d7d`）
- 本版：`决赛汇报-v19.pptx`（15 页）
- 分支：`docs/finals-ppt-v16`（base `origin/main` @ `10f7966`）
- 事实源：`.ppt-work/build_deck10.py`

## 1. 改了什么（一处内容修正）

| 页 | 项 | v18 | v19 | 依据 |
|---|---|---|---|---|
| P13 | 右栏 8 行第 7 行方向名 | AI 客服 | **智能客服** | 用户当轮注释 |

数字、流程、页数、版式均未动。「智能客服」4 字与该列其余行同字号（12pt），
列宽 1.50in 足够，渲染后目视确认无换行、无挤压。

## 2. 用户当轮裁决（两项）

| # | 事项 | 裁决 | 执行 |
|---|---|---|---|
| 1 | P01 上排三卡是否接受 pixel 的等宽理由 | 好（接受） | v18 已改等宽 3.84，v19 沿用 |
| 2 | P13 方向名口径 | AI 客服 → 智能客服 | v19 已改 |

## 3. 新增：Obsidian 逐页预览文档

`25-逐页预览-v19.md`（与 pptx 同目录），把 15 页渲染图收进一个文档：

```markdown
## P01 · 为什么做

**同业情报靠人工盯盘，被动响应且难沉淀**

![[55-workstuff/.../决赛/PPT/预览-v19/L-01.png]]

- **这一页干什么**：交代为什么必须有同业追踪……
- **口播**：85 字，约 26 秒

> 我们做行内新媒体渠道建设，同业动态和监管口径都得跟……。
```

每节四样：**页号 + 环节名**、**大标题**、**渲染图**、**这页在整场里干什么**（一句人话，不复述标题）、
**口播原文 + 字数时长**。

实现要点：

- 图片用 Obsidian embed 语法 `![[...]]`，路径**相对 vault 根**。
  vault 根是 `…/iCloud~md~obsidian/Documents/tqob`（不是 `Documents`，`.obsidian` 在 tqob 下）。
- 页标题与口播**从 pptx 直接抽**（读 shape 的 top 位置与字号 + notes），不手抄；
  重新出 pptx 后重跑脚本，文档自动跟随，不会与实物脱节。
- 已逐条校验 15 个 wikilink 与尾部两个相关链接均指向真实文件。

生成脚本：`.ppt-work/make_preview_doc.py`

```
python3 .ppt-work/make_preview_doc.py 决赛汇报-v19.pptx \
  "55-workstuff/AI技术应用与创新实践劳动竞赛/决赛/PPT/v19/预览" \
  <输出.md> v19 "24-终稿-v19-15页讲稿.md"
```

脚本参数化后回测 v18，输出与 v18 原文档**字节级一致**。

## 4. 校验（可复算）

```
python3 .ppt-work/build_deck10.py                    → slides: 15 / out of bounds: 0
python3 .ppt-work/fit_check.py 决赛汇报-v19.pptx     → overflow count: 0
python3 .ppt-work/scan_deck8.py 决赛汇报-v19.pptx    → 禁词 0 / 厂商名 0
                                                      speech 978 chars = 4.89 min
```

口播 978 字 + 现场演示 55 秒 = **5:48**，仍在 6 分钟内。

## 5. 归档（Obsidian `…/决赛/PPT/v19/`）

```
v19/决赛汇报-v19.pptx / .pdf
v19/24-终稿-v19-15页讲稿.md     讲稿 + 时长表
v19/25-逐页预览-v19.md           15 页图文预览
v19/预览/L-01…15.png             15 张渲染图
v19/源码/                        build_deck10.py / make_script_v19.py / make_preview_doc.py / assets
```

## 6. Obsidian 目录重构：每版本一个大文件夹（2026-11-26）

原来是扁平结构：顶层 34 个 md + 20 个 pptx/pdf + `预览-v1…v19` + `源码-v10…v15` 散放在一起。
现改为按版本分目录：

```
PPT/
  决赛PPT-索引.md        版本一览 + 定稿入口
  00-筹备/                v1 之前的 7 份过程稿（说明书、四家大纲、四稿对照）
  v01/ … v19/
    决赛汇报-vNN.pptx / .pdf
    预览/                 L-01…NN.png
    源码/                 v10 起：构建脚本、讲稿/预览生成脚本、自检脚本、assets
    <该版本的讲稿、逐页预览、UI 提案、文案终审等 .md>
```

配套处理：

- 文档内 `预览-vNN/`、`源码-vNN/` 的引用统一改为 `vNN/预览/`、`vNN/源码/`
- v16–v19 新增 `源码/`（v16=`build_deck7.py`、v17=`build_deck8.py`、v18=`build_deck9.py`、v19=`build_deck10.py`，各自带 `make_script_vNN.py` 与自检脚本、所用截图素材）
- 校验：全目录 30 处 `![[…]]` embed 全部指向真实文件（broken 0）；移动前后文件无丢失（436 → 502，增量 = 4 份 README + 44 张素材 + 18 个脚本）

