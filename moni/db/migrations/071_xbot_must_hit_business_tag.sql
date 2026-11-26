-- 071: X-bot 精选改为「必须命中业务 tag 才入库」+ 松散业务 tag `x-ai-circle`
-- schema_version 70 → 71（070 已被 case_extract_daily 占用）（t119u, dad 2026-11-26 拍板）
--
-- 拍板语义（与 docs/plans 里的方案不同点按 dad 原话为准）：
--   1. 业务 tag 准入是**唯一放行通道**：必须命中「已启用的业务 tag」之一。
--      两个真相源 —— 信源勾选的 `source_tags`、`fetch_config.topic_tags`
--      映射 —— 分别可启停，默认都开；对集合本身 fail-closed（集合为空 /
--      关键词全空 ⇒ 0 准入，绝不放通）。
--   2. `scope`（wechat_regex / xcircle_accounts）**默认只展示**，不再单独
--      准入；仅当 `scope_fence_enabled=true` 时作为「必须同时命中的 AND
--      围栏」。因此本迁移**故意不**打开这个开关 —— 现网已填的 scope 不会
--      因为升级突然变成 AND。
--   3. 关掉 `bypass_keyword_filters`，让源级 24 词黑名单（id=150, 11-25 配）
--      真正生效；同时显式 `bypass_tag_or_gate=true`：业务 tag 闸门由
--      fetcher 自己执行（`_match_business_tags`），不在 tag_filter 里重复
--      跑一遍，避免两道闸门口径打架。
--   4. 纯回复 / 纯转推默认过滤（`exclude_replies` / `exclude_retweets`=true），
--      随时可关。
--   5. `thresholds.xcircle.min_likes` 提到 20（其余 thresholds 键原样保留）。
--   6. `xbot-scope`（工程跟踪 tag）停用：`is_active=0`，不删行（保护历史
--      外键与筛选追溯），也不再出现在「设置 source 的 tags」里。
--
-- 幂等 / 不覆盖用户数据：
--   * `fetch_config` 用 `json_set` **只改列出的键**，其它键（scope、
--     blacklisted_handles、max_days、topic_tags…）逐字保留；
--   * `tag_definitions` 用 `INSERT OR IGNORE` —— 用户已经改过的 keywords
--     不会被覆盖（AGENTS.md 铁律 4）；
--   * 绑定 `x-ai-circle` 用 `INSERT OR IGNORE`，**不解绑**任何既有 tag
--     （banking / APP / workbuddy 等用户勾选一律保留）。

-- ── 1. xbot_feed 的 fetch_config ────────────────────────────────────────
UPDATE sources
   SET fetch_config = json_set(
           fetch_config,
           '$.bypass_keyword_filters', json('false'),
           '$.bypass_tag_or_gate', json('true'),
           '$.business_tags_from_source_tags', json('true'),
           '$.business_tags_from_topic_tags', json('true'),
           '$.scope_fence_enabled', json('false'),
           '$.exclude_replies', json('true'),
           '$.exclude_retweets', json('true'),
           '$.thresholds.xcircle.min_likes', 20
       ),
       updated_at = datetime('now')
 WHERE name = 'xbot_feed'
   AND fetch_config IS NOT NULL
   AND json_valid(fetch_config);

-- ── 2. `xbot-scope` 停用（工程 tag 不再冒充业务 tag）────────────────────
UPDATE tag_definitions
   SET is_active = 0
 WHERE name = 'xbot-scope';

-- ── 3. 松散业务 tag：X·AI 与工程（37 词）────────────────────────────────
INSERT OR IGNORE INTO tag_definitions
    (name, description, color, created_at, is_active, keywords, display_name)
VALUES (
    'x-ai-circle',
    'X 圈 AI 与工程实词（前沿模型 / Agent / AI 编程 / 工程栈），'
    || '用于 xbot_feed 的业务 tag 准入；词表可在 /config 随时改',
    '#6366F1',
    datetime('now'),
    1,
    json('["Claude","Claude Code","Codex","OpenAI","ChatGPT","GPT-4","GPT-5","Anthropic","Gemini","DeepSeek","Qwen","Kimi","GLM","MiniMax","Grok","Llama","LLM","Agent","Agents","MCP","Skill","Skills","Cursor","Windsurf","Copilot","Vibe Coding","Prompt","RAG","OpenRouter","Ollama","Harness","大模型","智能体","提示词","微调","AI编程","AI Agent"]'),
    'X·AI 与工程'
);

-- ── 4. 建议（不强制）绑到 xbot_feed：只加一条，不动任何既有绑定 ─────────
INSERT OR IGNORE INTO source_tags (source_id, tag_name, created_at)
SELECT s.id, 'x-ai-circle', datetime('now')
  FROM sources s
 WHERE s.name = 'xbot_feed'
   AND EXISTS (SELECT 1 FROM tag_definitions t WHERE t.name = 'x-ai-circle');
