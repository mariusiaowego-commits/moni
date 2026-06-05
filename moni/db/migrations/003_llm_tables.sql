-- Migration 003: LLM providers, models, and settings (PRD §3.5/3.6/3.7)
-- Idempotent: uses CREATE TABLE IF NOT EXISTS so it is safe to re-run.

-- llm_providers: one row per LLM provider (e.g. Google Gemini, OpenAI, etc.)
CREATE TABLE IF NOT EXISTS llm_providers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,   -- display name, e.g. 'Google Gemini'
    api_type    TEXT    NOT NULL DEFAULT 'gemini',  -- 'gemini' | 'openai' | 'anthropic' | ...
    base_url    TEXT,                      -- optional custom endpoint
    api_key     TEXT,                      -- encrypted or plaintext; NULL = use env var
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

-- llm_models: one row per model variant belonging to a provider
CREATE TABLE IF NOT EXISTS llm_models (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id       INTEGER NOT NULL,
    model_id          TEXT    NOT NULL,   -- provider's model ID string, e.g. 'gemini-2.5-flash-lite'
    name              TEXT    NOT NULL,   -- human-readable name
    model_type        TEXT    NOT NULL DEFAULT 'chat',  -- 'chat' | 'embedding' | 'vision' | ...
    context_window    INTEGER,            -- max tokens (informational)
    input_price_per_1m INTEGER,           -- USD price per 1M input tokens (informational)
    output_price_per_1m INTEGER,          -- USD price per 1M output tokens (informational)
    enabled           INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL,
    FOREIGN KEY (provider_id) REFERENCES llm_providers(id) ON DELETE CASCADE,
    UNIQUE(provider_id, model_id)
);

-- llm_settings: key-value config rows for LLM behaviour (timeouts, defaults, feature flags)
CREATE TABLE IF NOT EXISTS llm_settings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT    NOT NULL UNIQUE,
    value       TEXT    NOT NULL,
    description TEXT,
    updated_at  TEXT    NOT NULL
);

-- Indexes for common lookups
CREATE INDEX IF NOT EXISTS idx_llm_models_provider   ON llm_models(provider_id);
CREATE INDEX IF NOT EXISTS idx_llm_models_enabled    ON llm_models(enabled);
CREATE INDEX IF NOT EXISTS idx_llm_settings_key      ON llm_settings(key);
