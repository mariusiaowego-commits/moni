-- 061: X-bot scope 准入的专用 tag (sprint-55 / v1.0.131)
--
-- 背景: `sources.fetch_config.scope.wechat_regex` / `.xcircle_accounts` 从
-- v1.0.131 起是 moni 的**入库准入门**（dad 2026-11-25 12:33 批准），与候选
-- tag 的关键词闸门是 OR 关系。被 scope 单独救回的条目必须挂一个 tag
-- (AGENTS.md 铁律 3: 零 tag 不入库)，但又**不能**冒充领域关键词命中 ——
-- 否则一篇只因作者在名单里进来的推文会被打上 `wechat` / `general-tech`，
-- 与 `xbot_feed.dedupe_insert()` 丢弃 bypass 合成 hit 的既有取舍矛盾。
--
-- 所以新增这个**纯归属** tag:
--   * `keywords = '[]'`  ⇒ `_load_tag_keywords()` 会跳过它，它永远不会
--     变成 tag 闸门的候选（加词进去会改变闸门语义 — 不要加）；
--   * 不写 `source_tags` ⇒ 它不绑定任何源，只出现在 `article_tags` 上；
--   * 前端可按它一键筛「靠 scope 进来的」条目。
--
-- 幂等: `INSERT OR IGNORE` —— 重复跑不炸，也不覆盖人工改过的颜色/描述。

INSERT OR IGNORE INTO tag_definitions
    (name, description, color, created_at, is_active, keywords, display_name)
VALUES (
    'xbot-scope',
    'X-bot 范围准入（wechat 正则 / xcircle 固定账号命中，非领域关键词）',
    '#0EA5E9',
    datetime('now'),
    1,
    '[]',
    'X-bot 范围'
);
