---
type: sprint
sprint: 26112601
status: 已完成
---

# Sprint 81 · 案例库 UI V3 实施计划

## 阶段

1. 后端契约：stats 口径 / list 筛选 / facets / detail 聚合（`moni/cases/repository.py`、`moni/web/routes/cases.py`）。
2. 前端数据层：`cases.js` 重写加载、筛选、URL、详情（保留核验/编辑/标签/删除）。
3. 前端视图：`index.html` cases 区块换成概览带 + 筛选条 + `cv-card` + 详情 sheet。
4. 样式：`main.css` 追加 `cv-*`，只用现有 token；暗色无需单独覆盖。
5. 文档 + 测试 + 浏览器验收（8901 隔离端口）。

## 文件

- `moni/cases/repository.py`：`_case_filters()`、`facets()`、`stats()`、`get_case()`。
- `moni/web/routes/cases.py`：查询参数、`/facets`、`_row_to_case()` 字段。
- `moni/web/static/js/cases.js`：新状态与方法。
- `moni/web/static/index.html`：cases 区块（约 2099-2420 行）替换。
- `moni/web/static/css/main.css`：`cv-*` 样式块。
- `tests/test_cases_v3_api.py`：契约与口径。

## 风险

- `index.html` 与 `main.css` 是活跃文件，改动大；用 `cv-` 前缀隔离，旧 `.case-*` 不删以免影响别处。
- 6 维筛选 SQL 需与 stats/ facets 口径一致，否则数字互相打架；用同一 `_case_filters()`。
- 搜索与筛选叠加：q 走 LIKE（subject/summary/counterparty/企业名），不做 FTS 混合，先保证一致。

## 回滚

单 PR、无迁移；回滚 = revert 合并提交。生产未部署前不影响 8900。
