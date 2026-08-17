-- Migration 030: 删除 4 行 2026-08-13 测试残留的 ghost sources (v1.0.34.2)
--
-- 现象: /fetch 页面显示 2 个 36氪 + 2 个 支付 (4 行 ghost)
-- 根因: 8-13 06:36/06:42 在 /config 页面批量创建测试行, 用 hex 哈希后缀命名
--       (mp_3be50e90 等), url=example.com/mp_xxx 占位, 从未删除, 残留 DB.
--       /fetch 页面调用 /api/config/sources 直接读 DB 不过滤 YAML,
--       所以跟真实 src-36kr-rss / src-mpaypass 同屏.
--
-- 删除判定 (4 行):
--   - url 形如 https://example.com/mp_* (占位)
--   - name 形如 mp_<hex>[_b] (auto-slugified hash, 非人工命名)
--   - 0 articles / 0 source_tags / 0 source_keyword_filters / 0 fetch_jobs
--   - notes 为空, created_at 在 6 分钟内批量
--
-- DELETE by id 是 idempotent — 重复运行 0 影响.
-- 此 migration 不新增列, schema 形态不变.

DELETE FROM sources
WHERE id IN (
    'src-mp_3be50e90',
    'src-mp_3be50e90-b',
    'src-mp_4ea4c40a',
    'src-mp_4ea4c40a-b'
);

-- schema_version 不变 (仍然是 v29 base + 029 stats + 030 cleanup; 但 030 不改结构,
-- 所以 _CURRENT_VERSION 留在 29, 029 + 030 一组).
-- 显式记录本 cleanup 标记, 便于审计.
CREATE TABLE IF NOT EXISTS schema_cleanup_log (
    migration   TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_cleanup_log (migration, applied_at) VALUES
    ('030_drop_mp_ghost_sources', datetime('now'));