# Sprint 09 — Fetch 进行中 chip + 像素 loading (v1.0.28)

> **状态**: 进行中  
> **作者**: coding agent  
> **日期**: 2026-08-13  
> **分支**: `feature/v1.0.28-frontend-delight-ui`  
> **关联**: v1.0.9.8 fetch modal 粒子 / v1.0.7.6 fetch_jobs 异步轮询

---

## 0. 背景

### 0.1 用户场景 (已确认)

1. 在「抓取管理」点某源「立即抓取」→ 出现 progress modal  
2. 用户关掉 modal（后台 job 仍在跑）  
3. **期望**：右上角 `actions-group` 旁仍有「进行中」状态 + 亲和力 loading 动效 + 可点回 modal  
4. 动效参考：3×3 Drive 像素波 + shimmer 文案 + mono 已耗时

### 0.2 现状缺口

| 点 | 现状 |
|----|------|
| `closeFetchProgress()` | 关 modal **且 clearInterval 轮询** → 状态冻死 |
| `fetchingAll` | 仅「全部抓取」用；单源抓取关窗后右上角无任何提示 |
| `GET /api/fetch/jobs?status=running` | 后端已有，SPA **未接** page-load 恢复 |
| 行内「立即抓取」 | 无 running 禁用 / 指示 |

---

## 1. 范围

### 1.1 IN (v1.0.28 ship)

1. **状态机**：轮询与 modal 解耦；`status===running` 时关窗不停 poll  
2. **`actions-group` 左侧 chip**：Drive 像素格 + shimmer label + elapsed；点击 reopen modal  
3. **行级**：该源 running 时「立即抓取」禁用 / 文案「进行中」  
4. **进入 `/fetch`**：`GET /api/fetch/jobs?status=running` + 必要时 batch 恢复，挂 chip（不强制弹窗）  
5. **终态**：modal 已关时 chip 显示「完成/失败 · Ns」约 6s 后淡出  
6. **a11y**：`prefers-reduced-motion` 停像素动画，保留静态格 + 文案/计时  
7. **tech spec** 本文件 + 轻量测试（若仅前端：手动 checklist；无新后端契约则不强行加 pytest）

### 1.2 OUT (P1+)

- topnav 全局 chip（跨页）  
- 连通性测试行内 Dots loader  
- 替换 modal 内粒子为像素格（modal 保持现有 moni 粒子身份）  
- Orbit 变体

### 1.3 Non-goals

- 不改 fetch_jobs 后端 schema / 超时策略  
- 不改 particle 完成动画语义  

---

## 2. 文件清单

| 文件 | 改法 |
|------|------|
| `moni/web/static/js/app.js` | close/poll/reopen/resume + getters |
| `moni/web/static/index.html` | actions-group chip + 行按钮状态 |
| `moni/web/static/css/main.css` | `.pixel-loader` / keyframes / reduced-motion |
| `docs/specs/2026-08-13-fetch-running-chip-v1.0.28.md` | 本 spec |

---

## 3. 状态契约 (前端)

```
fetchProgressModal   // 仅控制 modal 可见
fetchProgress        // 最新 job/batch JSON（关窗也更新）
fetchBatchMode       // 单源 false / 全部 true（reopen 需要，running 时关窗保留）
fetchingAll          // 全部抓取启动中/进行中
_fetchPollTimer      // running 时关窗不 clear
_fetchActiveJobId    // 当前 single job id（可选，便于 resume）
_fetchActiveBatchId  // 当前 batch id
```

**运行中判定** `isFetchRunning`:

```js
['running'].includes(fetchProgress?.status) || fetchingAll === true
```

**终态** `done | failed | timeout | partial | stale`

---

## 4. UI 契约

### 4.1 Chip (actions-group 左侧)

- 可见：`isFetchRunning || fetchChipLinger`  
- 内容：`[3×3 Drive] [shimmer label] [elapsed mono]`  
- label 单源：`抓取中 · {display_name|name}`；全部：`全部抓取 · {done}/{total}`  
- 终态 linger：`完成 · {Ns}` / `失败 · {Ns}`  
- 点击：`reopenFetchProgress()`（若有 progress）  
- cursor: pointer；`title="点击查看进度"`

### 4.2 行按钮

- `isSourceFetching(s)`：running 且 source 匹配 name / id  
- 文案「进行中」、`:disabled="true"`

### 4.3 像素 Drive

- 9 cell，delay = `(c + Math.abs(r-1)) * 90ms`，dur 650ms  
- 色：`var(--foreground)`；opacity 基线 0.15 → 峰值 1  
- reduced-motion：animation none，opacity 0.25

---

## 5. 验收 checklist

- [ ] 单源立即抓取 → 关 modal → chip 仍动 + 计时涨  
- [ ] 点 chip → modal 重开，进度连续  
- [ ] 全部抓取同上  
- [ ] job 结束后 chip 变完成/失败并约 6s 消失  
- [ ] 刷新 / 再进 fetch 页：若仍有 running job，chip 恢复  
- [ ] 行按钮对 running 源禁用  
- [ ] reduced-motion 下无狂闪  

---

## 6. 落地顺序

1. 状态机解耦  
2. actions-group chip + CSS  
3. 行级  
4. enter-page resume  
5. smoke on 8901 / 8900  

---

## 7. 版本

- **版本号**: v1.0.28  
- **PR 标题建议**: `feat(fetch): v1.0.28 关窗后进行中 chip + Drive 像素 loading`
