-- Migration 003: LLM provider bindings, model registry, and active settings
-- PRD §3.5 / §3.6 / §3.7 — 薄壳模式
--
-- 设计原则：moni 不持有任何 API key。所有 key 由用户在
--   ~/.hermes/config.yaml 或 ~/.hermes/.env 配好
-- moni DB 只存"我用哪个 provider / 哪个 model"的引用关系
-- 实际调 LLM 走 subprocess 调 `hermes chat` CLI（moni/llm/client.py）

-- llm_providers: 一行 = 一个 provider 绑定（不存 key）
CREATE TABLE IF NOT EXISTS llm_providers (
    id              TEXT PRIMARY KEY,         -- uuid4
    name            TEXT NOT NULL,            -- 展示用 e.g. "Google Gemini"
    hermes_provider TEXT NOT NULL,            -- 对应 `hermes chat --provider` 参数值
    is_active       INTEGER NOT NULL DEFAULT 1,
    notes           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_providers_active ON llm_providers(is_active);

-- llm_models: 一行 = 一个 model 绑定（model_id 透传给 hermes -m）
CREATE TABLE IF NOT EXISTS llm_models (
    id           TEXT PRIMARY KEY,             -- uuid4
    provider_id  TEXT NOT NULL,
    model_id     TEXT NOT NULL,               -- 对应 `hermes chat -m` 参数值
    display_name TEXT,
    use_for      TEXT,                        -- JSON array: ["summary","fact_check","tag_predict"]
    enabled      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    FOREIGN KEY (provider_id) REFERENCES llm_providers(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_llm_models_provider ON llm_models(provider_id);

-- llm_settings: key-value 存活跃配置
CREATE TABLE IF NOT EXISTS llm_settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
