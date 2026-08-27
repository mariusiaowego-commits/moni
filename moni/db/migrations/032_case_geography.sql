-- 032_case_geography.sql — 案例地理维度 (国家 + 全球大区)
-- v1.0.37 sprint-19: 正交独立于 case_keywords / case_tags
-- 存储 ISO 3166-1 alpha-2 + 规范中文名 + 5 大区 (APAC/NAM/EMEA/LATAM/GLOBAL)

CREATE TABLE IF NOT EXISTS case_geography (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id       TEXT NOT NULL,
    country_code  TEXT NOT NULL,
    country_name  TEXT NOT NULL,
    region        TEXT NOT NULL
        CHECK (region IN ('APAC', 'NAM', 'EMEA', 'LATAM', 'GLOBAL')),
    sub_region    TEXT,
    is_primary    INTEGER NOT NULL DEFAULT 1,
    source_type   TEXT NOT NULL DEFAULT 'auto_rule'
        CHECK (source_type IN ('auto_rule', 'auto_llm', 'manual_user')),
    created_at    TEXT NOT NULL,
    FOREIGN KEY (case_id) REFERENCES cases(case_id) ON DELETE CASCADE,
    UNIQUE(case_id, country_code)
);

CREATE INDEX IF NOT EXISTS idx_case_geo_country ON case_geography(country_code);
CREATE INDEX IF NOT EXISTS idx_case_geo_region  ON case_geography(region);
CREATE INDEX IF NOT EXISTS idx_case_geo_case    ON case_geography(case_id);
