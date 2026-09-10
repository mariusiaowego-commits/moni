-- 035_llm_runnerer_config.sql — v1.0.42 (sprint-22): LLM 调用层配置
-- Failover chain + 熔断器阈值 + stdin 传输开关。
-- 空 fallback_chain = 单 provider 退化(向后兼容 v1.0.41)。

INSERT OR IGNORE INTO llm_settings (key, value) VALUES ('fallback_chain', '');
INSERT OR IGNORE INTO llm_settings (key, value) VALUES ('breaker_failure_threshold', '2');
INSERT OR IGNORE INTO llm_settings (key, value) VALUES ('breaker_reset_seconds', '600');
INSERT OR IGNORE INTO llm_settings (key, value) VALUES ('use_stdin_transport', '0');