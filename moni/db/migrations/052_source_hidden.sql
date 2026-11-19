-- 052: 信源隐藏开关 (sprint-26111901 / v1.0.109)
--
-- is_hidden 与 is_active 是两件事，不要混用：
--   is_active = 0 → 不参与定时/批量同步（数据面停用）
--   is_hidden = 1 → 数据面照跑，只是「信源管理」页不显示（展示面隐藏）
--
-- 纯 additive：加一列 + 默认 0（= 全部可见，与上线时行为一致）。

ALTER TABLE sources ADD COLUMN is_hidden INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_sources_hidden ON sources(is_hidden);
