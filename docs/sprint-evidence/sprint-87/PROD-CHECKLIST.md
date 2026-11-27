---
type: sprint
sprint: 26112701
status: 已完成
---

# Sprint 87 · 生产验证清单

逐条可点；括号里是期望值。任一不符按「回滚」一节处理。

## A. 分享页 v2（v1.0.144）

1. `/share` 队列有独立「特性」列：每行只显示前 2 项，其余显示 `+N`，悬浮出完整列表
2. PNG 行的「特性」里能看到生图方式（生图·排版 / 生图·卡片 / 生图·信息图）
3. 新建分享选 `html_landing`，标题预填 `同业简报-261127-网页-01`；同格式当天再来一次变 `-02`
4. 自定义分享 `share-card`：带 AI 对话上下文的文章只显示图标（无文字），与右侧勾选框对齐
5. `share-picker-toolbar` 右侧按钮同高同圆角同字号（实测 26.9px 高 / 13px / 4px 圆角 / 5px 14px 内距）

## B. 队列列宽（v1.0.146）

6. 列头是「简报链接」而不是「产出产物」，宽度约 280px
7. 失败行的操作列同时能看到「继续」和「丢弃」，末尾不再出现被裁出来的「…」
8. 简报链接过长时 hover 有 tooltip 看全

## C. 上下文呈现（v1.0.148 + v1.0.150）

9. 打开 `http://127.0.0.1:8900/artifacts/ehNnDB1HJPkr5lvPdKfa9A-html_landing.html`
   → 两张卡：徽标「AI 对话」右侧有文章标题（有赞…Skyline 的实践 / 小微日活 3000 万…）
10. 每张卡显示一段加粗 TLDR（117 字 / 90 字），下面「展开完整上下文」
11. 展开后：`关键事实`、`案例与实验` 是双列卡片（每列约 313px，高度不等、上下错落），
    每张卡内是「主张 + 灰色证明」
12. `无出处 / 需标注的数字`、`未知（文中没有）` 是整段满宽，不是两列
13. 区块高度：折叠 413px、展开 2527px（1440 与 1024 同值）
14. 页面 console 0 error

## D. 新产物自动生效

15. 新建一次 `html_landing`（不勾简报），选一篇带上下文的文章 → 按 9–12 检查
16. 选「简报」+`html_landing` 生成一次 → 文章卡内上下文同样双列（每列约 384px）

## E. 服务

17. `/`、`/share`、`/cases`、`/api/share/tasks`、`/api/share/next-title` 均 200
18. 本次只改后端 Python：`main.css?v=20261127004`、`app.js?v=20261127001` 不变是正确表现

## 回滚

- 产出物：`/private/tmp/ehNnDB1HJPkr5lvPdKfa9A-html_landing.before-v1.0.150.html`（更早还有
  `.before-v1.0.148.html`）直接覆盖回去
- 代码：`git revert` PR #370 的 squash commit `dfbec81`，重新 pull + `launchctl kickstart -k
  gui/$(id -u)/com.moni.web`
