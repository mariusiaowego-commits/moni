---
type: sprint
sprint: 26112701
status: 已完成
---

# Sprint 87 · 验收记录

## 1. 命令与输出

PR #370（本次会话最后一次代码改动）：

```text
$ /usr/local/bin/python3 -m pytest tests/test_context_pack_render.py tests/test_article_contexts.py -q -p no:cacheprovider
48 passed in 8.62s
```

```text
$ /usr/local/bin/python3 -m pytest tests/ -q -p no:cacheprovider -k "share or brief or context"
233 passed, 2914 deselected in 216.86s
```

CI：`pytest · py3.12` on `cc91f91`（PR #370 head）→ success；`53d80f4`（PR #371 head）→ success。
（`0808b01` 为 PR #370 首个提交，修完 P2 后以 `cc91f91` 合并。）

更早三个 PR 的测试数（引用 `STATUS.md` 对应条目，未在本轮重跑）：#352 → 183 passed；
#362 → 定向文件通过；#367 → 116 passed。

## 2. 真实数据复算（生产库只读）

```text
$ python3 - <<'PY'  # 连接 file:data/news.db?mode=ro
article_contexts 3 条：6f849f76(440字) / a75e0782(2517字) / 8b3b9726(2659字)
render_detail_html 全部返回、0 异常
6f849f76 → 0 节（回退 markdown）
a75e0782 → 4 节：关键事实 9 条、案例与实验 7 条、无出处整段、未知整段
8b3b9726 → 6 节：结论 4 条、玩法 4 条、改造 3 条、洞察 2 条、两处整段
```

`8b3b9726` 是审计发现的边界样本（`一、结论1. …` 无分隔），修复后
`h4=6 / moni-ctx-cols=4 / moni-ctx-item=13`，agy 独立复算同值。

## 3. UI 实测（Playwright，生产 8900，真实产出物）

```text
1500px 级视口（1440）与 1024：
details=2
column-count 2 / column-width 280px
.moni-ctx-item 宽 313px
#moni-user-contexts 高度 collapsed 413px → expanded 2527px
两张卡标题 = 有赞微信小程序接入Skyline的实践 / 小微日活3000万——腾讯Q3财报电话会的12条观察与思考
h4 = 关键事实（作者的结论性主张）/ 案例与实验（证据链逐条）/ 无出处 / 需标注的数字 / 未知（文中没有）
console error = 0
```

简报内嵌路径（真实 brief CSS + 真实上下文数据）：`column-count 2 / 280px`、条目宽 384px、
`.ctx-details .ctx-body` 的 `white-space` 为 `normal`。

截图：`render/context-1440.png`、`render/context-1024.png`、`render/brief-context-1440.png`、
`render/context-glued-heading-900.png`、`render/queue-columns-1440.png`。

## 4. 部署

```text
生产 checkout e86bc0e → dfbec81 → e2723cf（工作区干净）
8900 pid 37383 → 67554；/ /share /api/share/tasks 均 200
main.css?v=20261127004 / app.js?v=20261127001 —— 本次只改后端 Python，版本键不变（预期）
产出物 ehNn… 重注入：sha ae4740fa → 81544d15，备份在 /private/tmp/*.before-v1.0.150.html
HTTP 复取：moni-ctx-cols=2、moni-ctx-item=16、h4=4、<pre>=0
```

## 5. 审计

- PR #370：agy（`w52:p12`）首轮 `approve_with_nits`（P2：标题紧贴编号被并进 h4），
  修复后复检 **approve，无 P1/P2**
- PR #371（STATUS 回填）：agy 8 组数字逐项复算 **approve**，无不一致
- PR #368（上一轮 STATUS 回填）：agy 8 项数字复算 **approve**

## 6. failed / skipped

- **skipped**：真实「简报 + html_landing」端到端生成一次并检查内嵌上下文卡。原因：库内两份带
  上下文的文章被内容闸门判为「业务判断不足」未纳入简报（`0 篇文章 · 另有 2 篇因信息不足未纳入`），
  无法在不改内容闸门的前提下构造真实样本。替代验证：用真实 brief CSS + 真实 `article_contexts`
  数据单独渲染 `.sec-contexts`，测量双列与 `white-space`（见 §3）。
- **failed**：无。
- **已知边界**：`七、优先级建议  第一步：…` 这类双空格分隔已能拆；`八、一句话本质…` 与上一行
  粘连时仍会留在前一段正文里（内容不丢，只是不作为独立节标题）。
