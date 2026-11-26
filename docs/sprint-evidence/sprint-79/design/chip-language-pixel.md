# 案例库标签语言走查（unslop / polish / critique）

对象：案例卡底部米色药丸墙、筛选条白色药丸、卡片右上紫色类型药丸。
基线（以 worktree 为准）：`/Users/mt16/.codex/worktrees/1323/moni/docs/sprint-evidence/sprint-79/design/prototype-v2.html`（`.tag:105`、`.cc-type:88`、`.chip-facet:75`）、`filter-mock.html`（`.fchip:53`、`.ftoken:74`、`.fopt:88`）。
说明：brief 引用的 `render/filter-zones-1440.png`、`render/prototype-zones-1440.png` 在 worktree 中不存在（`render/` 目录缺失），故按 skill 第 1 步「只有代码/截图缺失 → 重建为静态 HTML 再评审」处理。
流程：unslop 九步（look → audit → 定程度 → direction → 九道 pass → check → report），范围只限标签层；polish 用于细节收口，critique 用于层级与可发现性复核。

---

## 1. Look（unslop step 1）

读了两个基线文件的 chip 相关规则与 DOM；截图缺失，重建 `chip-language-pixel.html`（旧/新并排）后渲染 1440 判读。结论：不是整页 slop，问题集中在「标签层」——**同一种药丸形状被用在 4 个不同语义位**（筛选维度、已选条件、选项、内容元信息），所以「标签+标签」看起来是一个东西。

## 2. Audit（unslop step 2，signs.md 术语，每条一行 + 位置）

| # | 症状（signs 原文术语） | 位置 |
|---|---|---|
| 1 | Every element a full pill, from search to buttons to cards → radius by size | `.fchip:53` 999px、`.chip-facet:75` 999px、`.ftoken:74` 6px、`.fopt:88` 6px、`.tag:105` 6px：五种圆角混用，药丸占满 |
| 2 | Glowing pills for counts, plans, shortcuts or status → quiet mono text or a keycap | `.chip-facet .c:76`、`.fchip__n:60`、`.fopt__n:95`：计数塞在药丸里（mono 12 + opacity .6） |
| 3 | Many near identical tints of the same hue for text, like five lavenders → three text roles | `.tag:105` `--muted` 底 + `--muted-fg` 字，与 `.fchip` `--card` 底 + `--border` 只差一档明度，米色药丸连成一片 |
| 4 | Every element highlighted, so nothing stands out → one accent used once or twice per screen | `.cc-type:88` `--accent` 紫底 + 600；`.fchip[aria-expanded]:59`、`.fopt[aria-selected]:94` 黑底：三种强调并存 |
| 5 | Icons in many sizes and colors → one size, one stroke | `.ftoken__x:79` 12px 文本 ×（tokens：图标 16/18px、stroke 1.5） |
| 6 | Uppercase labels with wide tracking → sentence case at normal tracking | `.fpanel__head`、``.facet h4` `letter-spacing:.06em` |
| 7 | Arbitrary values, like `mt-[13px]` → steps on the 4px scale | `.tag:105` `padding:2px 8px` → 高 20px，非 4 的倍数 |
| 8 | Dashed borders（占位感） | `.cc-foot:104` `border-top:1px dashed var(--border)` |
| 9 | Trailing/wordy labels、标签与值重复 | `.tag` 内容「参考价值 高」「AI 应用」：类型与值是内部 taxonomy |
| 10 | Template look：chips and avatars stacked | `.cc-foot` 一排 5 个同形药丸，卡片底部成「药丸墙」 |

定程度（step 3）：**Light slop（polish 模式）**——版式、token、三层表面都成立，只有 chip 层需要收口。不重做整页，不改三层表面语言。

## 3. Direction（unslop step 4，五行）

- **Product.** 给同业战略与产品团队用的案例库，30 秒内判断「这家做了什么、值不值得抄」。
- **Character.** 克制、精确、带一点纸感（不是 clean / modern 这类模板词）。
- **World.** 档案库：索引卡、剪报、著录行；不是仪表盘霓虹。
- **Moments.** 案例卡的「关键数字」（28px 数字 + 单位）与详情页原文引文；标签层保持安静。
- **Signature.** 左上直角三角（项目已有 `.brand-mark`）+ 元信息用「·」连成的著录行。

## 4. 三套标签语言（确切 CSS）

### 方案 A 著录行（推荐）

```css
/* 一行元信息：行业 · 场景 · 动作 · 时间 */
.c-meta{display:flex; flex-wrap:wrap; align-items:center; gap:0 var(--space-2);
  font-size:var(--font-12); line-height:16px; color:var(--muted-fg)}
.c-meta .sep{color:var(--border)}                 /* 分隔点用边框色，弱于文字 */
.c-meta .k{color:var(--card-fg)}                  /* 强调项：仅 1 个，如「高价值」 */
/* 强调项：弱底 + 4px 圆角，不做药丸 */
.c-flag{font-size:var(--font-12); font-weight:500; line-height:16px;
  padding:0 6px; border-radius:4px; background:var(--muted); color:var(--card-fg)}
```
取舍：胶囊 5→0（强调项 1 个弱底），信息不丢（6 项全在），高度 20→16px，卡片更轻；代价是「可点感」弱——但标签本来就是只读元信息，不需要可点。

### 方案 B 两个药丸 + 文本

```css
.c-meta{...同 A...}
.c-pill{height:22px; padding:0 8px; font-size:var(--font-12); line-height:22px;
  border-radius:6px; background:var(--card); border:1px solid var(--border); color:var(--card-fg)}
```
取舍：保留「类型」「价值」两个药丸做视觉锚点，其余文本；22px 高、6px 圆角（tokens：tag 4–6）。代价：仍有 2 个药丸，药丸墙变「药丸点」。

### 方案 C 全文本 + 三角

```css
.c-meta{...同 A...}
.c-meta .tri{width:0; height:0; border-left:3px solid var(--muted-fg);
  border-top:3px solid transparent; border-bottom:3px solid transparent} /* 指向左侧 */
```
取舍：最安静、最像索引卡；代价：强调完全靠字重，弱信息（时间）与强信息（价值）只差 500/400，扫读层级偏平。

## 5. 推荐

**方案 A**，并把方案 B 的 22px/6px 只留给「价值」一个锚点（A+B 混合）：
1. 药丸从 5 个降到 1 个，形状语言不再通胀（signs #1/#10）。
2. 计数不再进药丸，改 mono muted 文本（#2）。
3. 去掉 accent 紫底（#4），accent 只留给主按钮/选中。
4. 12px/16px 行高、4px 圆角、实线分隔（#7/#8），全部落 4px 栅格。
5. 灰度与 AA：元信息 `--muted-fg` on `--card` = 6.31:1（AA），强调项 `--card-fg` on `--muted` = 14.06:1。

## 6. 直接删掉的标签

| 删 | 理由 |
|---|---|
| 卡片右上「AI 应用」类型药丸 | 8 类内部 taxonomy，列表不需要；类型进详情「基本信息」 |
| 「参考价值 高」前缀 | 标签与值重复，改「高价值」 |
| 卡片底部「场景」与标题/摘要重复的那一项 | 结论句已含场景，元信息只留行业 + 动作 + 时间 |
| 卡片左上 `MN-AI-2611-0004` 编号 chip | 列表 30 秒扫读不需要主键；详情页角标保留 |
| 「为什么值得看」小标签 | 与卡片结论句重复 |
| 筛选条 `.ftoken` 的边框药丸 | 已选条件改「行业 2」计数 + 文本 token，边框 1px 保留但去底色 |

## 7. 九道 pass 落点（只动标签层）

| pass | 改动 |
|---|---|
| 1 Spacing | `.tag` 高 20→16px（12/16 + 0 padding），gap 8，`.cc-foot` padding-top 8 |
| 2 Color | 标签去 `--muted` 底（除 1 个强调项），文字走 `--muted-fg`；分隔点用 `--border` |
| 3 Icons | `.ftoken__x` 12px 文本 → 16px 线图标（stroke 1.5，`--muted-fg`） |
| 4 Copy | 「参考价值 高」→「高价值」；删类型药丸；标签只留 行业 · 动作 · 时间（+价值锚点） |
| 5 Type | 标签 12px/400、字距 0；面板头去掉 `.06em` tracking |
| 6 Layout | `.cc-foot` 由 flex-wrap 药丸墙 → 单行 `.c-meta`，超出换行不换行成第二行 |
| 7 Details | 圆角 4px（tag）/6px（唯一锚点）；计数 mono muted；dashed → solid hairline |
| 8 Visuals | 无（标签层无图） |
| 9 Motion | hover 只变文字色，120ms `cubic-bezier(0.22,1,0.36,1)` |

## 8. Check（unslop step 6 + polish 清单）

- 4px 栅格：16/8/4 全部命中；无 2px padding。
- 三层表面未动：A 无框 + hairline + 0；B `--muted` + 6px；C `--card` + 12px + hover `--shadow`。
- 对比度：6.31 / 14.06 / 17.21，全过 AA；无 emoji、无渐变、无发光。
- 交互态：hover 文字色 `--foreground`、focus-visible 2px `--muted-fg`、selected 黑底（仅筛选层）。
- critique 复核：层级（数字 > 标题 > 元信息）清晰；可发现性（筛选胶囊仍可点）；模板感下降（药丸墙消失）。

## 9. 验收（实测）

- 渲染：`/private/tmp/chip-language-1440.png`（130610 B）、`/private/tmp/chip-language-1024.png`（82050 B）。
- DOM 计数：旧块 `.tag` 11 个、新块 `.tag` 0 个 / `.c-meta` 2 行 / `.c-flag` 2 个；`border-radius:999px` 仅剩 `.old` 作用域 1 处。
- 仓库未动（git status 空）。

---

## 10. 三行摘要

1. Audit 10 条症状集中在「一种药丸形状用在 4 个语义位」：满屏 999px 胶囊、计数进药丸、accent 紫底与黑底并存、2px padding、dashed 分隔。
2. 推荐方案 A 著录行：12px/16px `--muted-fg` 单行 + 「·」分隔 + 仅 1 个 `--muted` 底 4px 圆角锚点，药丸 5→1，信息 6 项不丢。
3. 直接删：类型药丸、编号 chip、「参考价值」前缀、「为什么值得看」标签、重复场景项；对比度 6.31–17.21 全过 AA，三层表面语言不变。