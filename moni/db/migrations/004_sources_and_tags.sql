-- Migration 004: 抓取源管理 + 业务 tag + 关键词过滤 (PRD v1.0.2)
--
-- 设计：
--   sources             抓取源（替代 config/sources.yaml 主用，YAML 保留 fallback）
--   source_tags         源 ↔ tag_definitions 多对多（每个源关联 1+ 业务 tag）
--   source_keyword_filters 源的关键词白/黑名单（控制抓回文章是否入库）
--
-- 与已有表关系：
--   sources.name 与 articles.source 用字符串关联（弱耦合——源可删/重命名不影响历史文章）
--   source_tags 关联到已有 tag_definitions（不要重复建表）
--   不动 article_tags（那是 article ↔ tag，与 source_tags 不同的关联方向）

-- ── sources 表 ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sources (
    id                  TEXT PRIMARY KEY,        -- uuid4 (slug from name)
    name                TEXT NOT NULL UNIQUE,   -- '36kr_rss' | 'mpaypass' | 'qbitai' | 'wechat_rss' | '36kr_newsflash'
    display_name        TEXT,                   -- '36氪 RSS' (给 UI 看)
    source_type         TEXT NOT NULL,           -- 'rss' | 'html' | 'api' | 'wechat_aggregator'
    url                 TEXT NOT NULL,           -- 列表/首页 URL
    fetch_interval_min  INTEGER NOT NULL DEFAULT 30,
    is_active           INTEGER NOT NULL DEFAULT 1,
    notes               TEXT,                    -- 给维护者看
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sources_active ON sources(is_active);

-- ── source_tags 表（多对多：源 ↔ 业务 tag） ───────────────────────────────
CREATE TABLE IF NOT EXISTS source_tags (
    source_id   TEXT NOT NULL,
    tag_name    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (source_id, tag_name),
    FOREIGN KEY (source_id) REFERENCES sources(id)      ON DELETE CASCADE,
    FOREIGN KEY (tag_name)  REFERENCES tag_definitions(name) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_source_tags_tag ON source_tags(tag_name);

-- ── source_keyword_filters 表（白/黑名单） ──────────────────────────────
CREATE TABLE IF NOT EXISTS source_keyword_filters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       TEXT NOT NULL,
    mode            TEXT NOT NULL,           -- 'whitelist' | 'blacklist'
    match_field     TEXT NOT NULL DEFAULT 'title+content',  -- 'title' | 'content' | 'title+content'
    keywords        TEXT NOT NULL,           -- JSON array of strings, e.g. ["央行", "支付", "银行"]
    is_active       INTEGER NOT NULL DEFAULT 1,
    notes           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_kw_filters_source ON source_keyword_filters(source_id);
CREATE INDEX IF NOT EXISTS idx_kw_filters_active ON source_keyword_filters(is_active);
