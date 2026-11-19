-- 051: 运行时开关表 (sprint-58 follow-up)
--
-- 让"闸门开/关"这类运行时开关改由数据库配置决定，而不是改代码常量 + 走 PR。
-- 纯 additive：一张 KV 表 + 一个默认值（默认关闭，与上线时行为一致）。

CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT
);

INSERT OR IGNORE INTO app_settings (key, value, updated_at)
VALUES ('moderation.gate_enabled', '0', NULL);
