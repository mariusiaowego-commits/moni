-- 040_llm_model_mode.sql — v1.0.70: moni 跟随 hermes profile 的模型
-- profile: call_llm/worker 不传 --provider/-m，跟随 moni profile 的默认模型
--          （即 magpie 里给该 profile 选的路由组）。
-- explicit: 按 LLM 设置页的 active_*_model_id 显式调用（旧行为）。

INSERT OR IGNORE INTO llm_settings (key, value) VALUES ('llm_model_mode', 'profile');
