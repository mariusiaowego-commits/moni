-- Sprint 47: article reader AI panel.
--
-- reader_prompts        user-editable prompt library (pill row + manager)
-- reader_conversations  one or more conversations per article; P0 uses latest
-- reader_messages       user/assistant turns; assistant rows carry stream state
-- reader_chunks         long-article chunk index (full-text, no truncation loss)
-- llm_jobs              + conversation_id / prompt_name / parent_job_id
--
-- llm_jobs.task_type gains 'reader_chat'.  Existing workers must exclude it;
-- the reader worker claims only it (see moni/db/leases.py task filters).

CREATE TABLE IF NOT EXISTS reader_prompts (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    body        TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'article',
    is_builtin  INTEGER NOT NULL DEFAULT 0,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reader_conversations (
    id          TEXT PRIMARY KEY,
    article_id  TEXT NOT NULL,
    title       TEXT,
    unread      INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reader_conversations_article
    ON reader_conversations(article_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_reader_conversations_unread
    ON reader_conversations(unread) WHERE unread = 1;

CREATE TABLE IF NOT EXISTS reader_messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT NOT NULL DEFAULT '',
    quote_text      TEXT,
    job_id          TEXT,
    reasoning       TEXT,
    status          TEXT NOT NULL DEFAULT 'done'
                    CHECK (status IN ('pending', 'streaming', 'done', 'failed', 'cancelled')),
    error           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES reader_conversations(id) ON DELETE CASCADE,
    FOREIGN KEY (job_id) REFERENCES llm_jobs(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_reader_messages_conversation
    ON reader_messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_reader_messages_job
    ON reader_messages(job_id);

CREATE TABLE IF NOT EXISTS reader_chunks (
    id          TEXT PRIMARY KEY,
    article_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    heading     TEXT,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_reader_chunks_article
    ON reader_chunks(article_id, seq);

ALTER TABLE llm_jobs ADD COLUMN conversation_id TEXT;
ALTER TABLE llm_jobs ADD COLUMN prompt_name TEXT;
ALTER TABLE llm_jobs ADD COLUMN parent_job_id TEXT;

INSERT INTO reader_prompts (id, name, body, scope, is_builtin, sort_order, created_at, updated_at)
VALUES
  ('rp-summary',   '总结要点', '用 5 条 bullet 总结这篇文章最值得关注的观点。每条先给结论，再给一句原文依据。', 'article', 1, 10, datetime('now'), datetime('now')),
  ('rp-structure', '结构拆解', '把文章的论证结构拆成：问题、证据、结论、隐含假设。指出哪一环最薄弱。', 'article', 1, 20, datetime('now'), datetime('now')),
  ('rp-facts',     '事实清单', '列出文章里的关键事实、数据、案例，并逐条说明它们分别证明了什么。没有出处的数字单独标出来。', 'article', 1, 30, datetime('now'), datetime('now')),
  ('rp-verify',    '待验证点', '这篇文章有哪些值得怀疑或需要验证的地方？按重要性排序，每条说明验证方法。', 'article', 1, 40, datetime('now'), datetime('now')),
  ('rp-oppose',    '反方观点', '站在反方立场，指出这篇文章最可能被挑战的 5 个点。只基于文中内容，不引入外部事实。', 'article', 1, 50, datetime('now'), datetime('now')),
  ('rp-oneline',   '一句话解释', '用一句话解释这篇文章的核心观点，再用三句话展开。不要复述标题。', 'article', 1, 60, datetime('now'), datetime('now'))
ON CONFLICT(name) DO NOTHING;

INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (43, datetime('now'));
