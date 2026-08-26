-- 031_case_keywords.sql — 案例细粒度业务关键词表 (v1.0.35.19, agy v3 评审方案 B)
-- 解耦于 tag_definitions (信源级大类) / case_tags (FK 强约束), 支持:
--   - LLM 自动打标 (auto_llm)
--   - 人工添加/编辑 (manual_user) — 重新提取时不覆盖
--   - 系统规则 (system_rule, 预留)
-- 反选过滤 (高价值榜单排除标签) 走前端 localStorage + 本表关键词匹配.

CREATE TABLE IF NOT EXISTS case_keywords (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id     TEXT NOT NULL,
    keyword     TEXT NOT NULL,               -- 规范化关键词 (如: 跨境支付 / 智能投顾 / 反欺诈)
    source_type TEXT NOT NULL DEFAULT 'auto_llm'
        CHECK (source_type IN ('auto_llm', 'manual_user', 'system_rule')),
    weight      INTEGER NOT NULL DEFAULT 1,  -- 1-5, 越高越重要 (人工可调)
    created_at  TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE,
    UNIQUE(case_id, keyword)
);

CREATE INDEX IF NOT EXISTS idx_case_kw_case ON case_keywords(case_id);
CREATE INDEX IF NOT EXISTS idx_case_kw_word ON case_keywords(keyword);
