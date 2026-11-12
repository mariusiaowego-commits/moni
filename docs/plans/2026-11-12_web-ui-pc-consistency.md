---
id: 26111207
type: plan
version: v1.0.76
start_date: 2026-11-12
status: 进行中
sprint: "[[sprint-42-web-ui-pc-consistency-2026-11-12]]"
---

# Sprint 42 PLAN — PC 一致性与可访问性

## 1. /stats 1280 溢出

实测 `documentElement.scrollWidth = 1331 / 1280`。定位溢出元素（脚本输出了 79 个 offender），
根因是图表容器/双列栅格有固定宽度。修法：容器改 `min-width: 0`，栅格用 `repeat(auto-fit, minmax(320px, 1fr))`，
SVG 图表 `width: 100%`、`viewBox` 自适应，禁止固定 px 宽度。

## 2. 字号阶梯

```css
--font-12: 12px; --font-13: 13px; --font-14: 14px;
--font-16: 16px; --font-18: 18px; --font-24: 24px; --font-28: 28px;
```

把 `0.76rem / 0.8rem / 0.82rem / 0.84rem / 0.86rem / 0.88rem / 0.9rem / 0.95rem / 1.05rem / 1.2rem`
逐一映射到最近档位（12/13/14/16/18/24），不保留 `rem`（除 `html` 根字号）。
小字（≤11px）统一提到 12px。

## 3. 焦点态

```css
:where(a, button, input, select, textarea, summary, [tabindex]):focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: inherit;
}
```

## 4. 标签对比度

`.tag-chip` 的 5 种配色逐一测 `contrast(fg, bg)`：
- 不足 4.5 时，前景改用 `--fg-strong` 或降低背景亮度；
- 改完在亮/暗两套主题各测一次。

## 5. Acceptance（orchestrator 执行）

1. `pytest` → ≥1630 passed / 2 skipped。
2. 1280 / 1440 / 1920 × 10 视图：`scrollWidth === clientWidth`（30/30）。
3. 全站可见文本的 `font-size` 去重后 ≤ 8 种，且全部 ∈ {12,13,14,16,18,24,28}。
4. 焦点：`.topnav-links a`、`.btn-primary`、`input` 聚焦后 `outline-width` ≥ 2px。
5. 标签 chip：亮/暗各取 5 个 chip，`contrast(fg,bg) ≥ 4.5`。
6. 改动文件 ⊆ `index.html`、`css/main.css`、`js/stats.js`。
