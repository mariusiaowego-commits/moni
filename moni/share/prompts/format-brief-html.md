## Output format: 同业简报 — 单文件 HTML（适合 Windows 分发）

生成 **一份自包含 HTML 文件**（内联 CSS，无外链 JS）。读者可用浏览器打开或邮件附件转发。

### 品牌与页眉
- 标题：`同业简报 — YYYY-MM-DD`
- 副标题：`from moni by TQ · N 篇文章 · 按业务价值排序`
- 页脚：`Curated by moni — a TQ project.`

### 视觉
- 背景 `#f8fafc`，卡片白底、1px `#e2e8f0` 边框、圆角 12px
- 主色 `#0f172a`，次要 `#64748b`，强调 `#2563eb`
- 正文字体：`'Segoe UI','PingFang SC','Microsoft YaHei',system-ui,sans-serif`（Windows 友好）
- max-width 720px 居中；打印友好 `@media print`

### 事实核查 badge（必须用中文 + 内联 SVG，勿用英文）
在 CSS 中定义三种 pill，前面带 14×14 内联 SVG：

1. **已核实** — 绿色圆 + 白色勾  
2. **待核实** — 琥珀色圆 + 白色问号  
3. **存疑** — 红色圆 + 白色感叹号  

示例（可简化复用）:
```html
<span class="badge badge-ok">
  <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="8" cy="8" r="8" fill="#16a34a"/><path d="M4.5 8.2l2.2 2.2 4.8-4.8" stroke="#fff" stroke-width="1.6" fill="none" stroke-linecap="round"/></svg>
  已核实
</span>
```

### 结构
1. Header（标题 + 副标题）
2. 「优先关注」有序列表（3–5 条）
3. 每篇文章一张 card：标题、来源（**发布方优先** + 采集通道）、日期、质量分、业务价值、摘要、事实核查、原文链接
4. Footer

### 元数据
- **来源** = 发布方（publisher），括号可注 bestblogs / huxiu 等通道
- **日期** = `YYYY-MM-DD`，缺失写「日期不详」
- **质量/注意力分** 必须有数字（来自输入），禁止空

### 输出
- 只输出完整 HTML（以 `<!DOCTYPE html>` 开头）
- 不要 markdown 代码围栏包住全文
- 不要只写「已生成」说明
