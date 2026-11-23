-- 056: article-level user context (Sprint 69 spike)
--
-- The reader AI panel produces answers that currently die with the
-- conversation.  This table lets the user pin selected answers (and later
-- notes/quotes) onto the article itself, so a brief/share can opt in to
-- carrying them as a separate "用户上下文" section.
--
-- Deliberately no FK to reader_messages: a context is a snapshot.  Deleting
-- a conversation must not silently delete the user's saved context.

CREATE TABLE IF NOT EXISTS article_contexts (
    id                TEXT PRIMARY KEY,
    article_id        TEXT NOT NULL,
    kind              TEXT NOT NULL DEFAULT 'chat'
                      CHECK (kind IN ('chat', 'note', 'quote', 'summary', 'fact_check')),
    title             TEXT,
    body              TEXT NOT NULL,
    source_message_id TEXT,
    source_question   TEXT,
    quoted_text       TEXT,
    sort_order        INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_article_contexts_article
    ON article_contexts(article_id, sort_order, created_at);

INSERT OR IGNORE INTO schema_version (version, applied_at)
VALUES (56, datetime('now'));
