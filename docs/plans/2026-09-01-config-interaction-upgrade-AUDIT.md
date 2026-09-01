# moni Config 交互升级方案 — 独立 Audit 报告

- **日期**: 2026-09-01
- **审计者**: moni-audit (herdr pane w4:p22, step-3.7-flash:free) — 独立审计, 由 orchestrator(moni-hermes) 派活
- **被审方案**: `docs/plans/2026-09-01-config-interaction-upgrade.md` (agy 出)
- **结论**: ⚠️ **有条件通过** — 必须修改 1 个严重问题后评估, 建议项纳入实现

---

## 发现的严重问题 (必须改)

**S1. 4.1 nameConfirmed 状态机缺「改 name 后自动回退 false」的 watcher**
- 位置: 方案 §4.1 + `index.html` tag modal
- 细节: 方案里"新建 tag"保存按钮 disabled = `!original && !nameConfirmed`。但用户 `nameConfirmed=true`(锁定) 后点"✏️修改"解锁改 name, 改完若不重新确认, `nameConfirmed` 仍是 true → 保存按钮 enabled → 会 PUT 新 name 到后端。而 name 是主键, 后端返回 404/不更新, 前端却没拦住。
- 修复: name 一旦被修改(set), 立即把 `nameConfirmed` 回退 false, 保存按钮重新 disabled, 必须再次点确认。

## 建议问题 (应纳入实现)

1. **3.4 fetch_log / cron_runs 的 dropped_* 统计透出**: 方案写了"统计透出", 但要明确是加列(需 migration)还是存 JSON。建议明确 + select schema。
2. **3.4 后端硬兜底**: 全局白名单创建/启用时, 若 keywords 为空应后端直接 400 拒绝, 不能只靠前端 Warning。
3. **3.4 match_field 混用边界**: 全局规则与源规则各自带 match_field, 代码应分别 `_match_field(article, flt["match_field"])`, 全局与源级独立按自身 match_field 判断、不继承。方案文档应补明确语义。
4. **3.3 keywordChipEditor 闭包 stale ref**: `getKeywordsArray/setKeywordsArray` 闭包捕获 configModalTarget, 若组件跨配置对象复用可能闭包指向失效对象。建议改读 `this.$root` 的 configModalTarget 或 Alpine.store, 避免闭包 stale。
5. **3.4 keyword_hits 记录**: 方案写 article_keyword_hits 记全局命中 filter_id=NULL。已核实 migration 010 `article_keyword_hits` **已有 `filter_id INTEGER NULL` 列**(FK source_keyword_filters ON DELETE SET NULL), 存 NULL 即可, 无需迁移。但需确认 repository.py 的 insert 语句是否允许传 NULL (filter_id 可选)。
6. **4.1 非法 name 实时反馈**: "✏️修改"解锁后, 输入非法字符(非 `^[a-z0-9_-]+$`)时建议实时校验提示, 不必等到点"确定"才报错。

## 疑问 (实现时确认)

- **3.4 global-filters API 路径**: 是扩展 `/api/config/filters?scope=global` 还是独立 `/api/config/global-filters`? 现状 filters API 有 source_id 参数, 扩展可能更简洁; 需确认前端 filter 列表筛选逻辑是否兼容。

---

## orchestrator 补充核实 (2026-09-01)

> 我(moni-hermes)在派活前已抽查关键断言, 追加以下事实, 供实现参考:

1. **schema_version 当前 = 32** (`moni/db/migrate.py` `_CURRENT_VERSION = 32`), 最新 migration 文件是 `032_case_geography.sql`。方案要用 migration 033, **编号连续不冲突** ✓
2. **article_keyword_hits 已有 filter_id 列** (migration 010, `INTEGER NULL`, FK→source_keyword_filters.id ON DELETE SET NULL)。audit 建议#5 的 filter_id=NULL 方案**无需新迁移**, 直接存 NULL 即可。
3. **现状管线 filter 加载**: `moni/fetch/filters.py` `_load_active_filters(source_id)` 仅 `WHERE source_id=? AND is_active=1`; `apply_keyword_filters` 先黑名单 drop、后白名单 keep。3.4 方案要在此处扩展全局层, 需保证不影响现有单源行为(回归风险)。