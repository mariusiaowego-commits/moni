-- 017: article_pins table — user-initiated article pin/unpin (v1.0.8)
--
-- Distinction from basket (migration 015):
--   basket  = system-passive: articles dropped by filters, user can rescue
--   pin     = user-active:   user marks articles as "save for later share"
--
-- Schema choice: independent table (vs adding columns to articles)
--   - Clean separation: pin metadata doesn't bloat articles row
--   - FK CASCADE: deleting an article automatically removes its pin
--   - Future-proof: easy to extend with note/user_id/etc without altering articles
--
-- Frontend behaviour:
--   - Toggle via POST /api/articles/{id}/pin?on=true|false
--   - List via GET  /api/articles/pinned (filter by source/tag, sort by pinned_at)
--   - Bulk via POST /api/articles/pins/bulk-unpin
--
-- moni is single-user local project, no user_id column needed.

CREATE TABLE IF NOT EXISTS article_pins (
    article_id  TEXT PRIMARY KEY,
    pinned_at   TEXT NOT NULL,            -- ISO8601, set on first pin / re-set on re-pin
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_article_pins_pinned_at ON article_pins(pinned_at DESC);
