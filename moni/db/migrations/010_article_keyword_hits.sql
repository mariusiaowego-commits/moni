-- v1.0.5.20: article_keyword_hits — 记录文章实际命中了哪些关键词
-- 用于前端 tooltip 高亮展示，让用户知道文章为什么被收录

CREATE TABLE IF NOT EXISTS article_keyword_hits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id      TEXT NOT NULL,
    keyword         TEXT NOT NULL,
    filter_id       INTEGER,                -- source_keyword_filters.id
    hit_field       TEXT NOT NULL DEFAULT 'title+content',  -- 'title' | 'content' | 'title+content'
    created_at      TEXT NOT NULL,
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE,
    FOREIGN KEY (filter_id) REFERENCES source_keyword_filters(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_article_keyword_hits_article ON article_keyword_hits(article_id);
CREATE INDEX IF NOT EXISTS idx_article_keyword_hits_keyword ON article_keyword_hits(keyword);
