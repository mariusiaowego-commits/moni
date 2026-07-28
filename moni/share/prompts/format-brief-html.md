## Output format: 同业简报 — 单文件 HTML（Windows 可双击打开）

自包含 HTML（内联 CSS，无外链 JS）。**短**：与 markdown brief 同一套字数硬顶。

### 页眉 / 页脚
- 标题：`同业简报 — YYYY-MM-DD`
- 副标题：`from moni by TQ · N 篇文章 · 按业务价值排序`
- 页脚：`Curated by moni — a TQ project.`

### 原文 vs AI
- 标题：**原样**
- **简报摘要** 字段：原样贴进摘要（系统已对超长原摘要 AI 压短；够短则仍是原文）
- 优先关注 / 事实核查措辞：遵守 `writing-style-compact.md`，且极短

### 字数硬顶
- 优先关注最多 **3** 条，每条 1 句 ≤40 字
- 摘要：用简报摘要原样（已 ≤2 句 / 100 字）
- 业务价值说明 **1** 句 ≤50 字
- 事实核查最多 **2** 条

### 视觉（Windows 友好）
- 字体：`'Segoe UI','Microsoft YaHei','PingFang SC',system-ui,sans-serif`
- 背景 `#f8fafc`，卡片白底 + 1px 边框，max-width 720px
- 事实核查 pill + 内联 SVG：【已核实】绿勾 / 【待核实】琥珀 / 【存疑】红
- **禁止**英文 supported/unverified

### 元数据
- 来源 = 发布方 +（采集通道）；日期 YYYY-MM-DD；质量分必有数字

### 输出
完整 HTML 从 `<!DOCTYPE html>` 起，stdout 全文，不要 markdown 围栏，不要元叙述。
