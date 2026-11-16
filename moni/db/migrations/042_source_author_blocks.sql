-- 042_source_author_blocks.sql - v1.0.88 (sprint-38)
--
-- X-bot 精选「屏蔽作者」的持久化名单。
--
-- 背景（dad GO IMPL 2026-11-12 15:04）：
--   详情弹窗里一键屏蔽某个 @handle，被屏蔽的作者后续文章不再进入
--   x-bot 精选。名单必须**扛得住 db init / seed-config**。
--
-- 为什么不用 sources.fetch_config（方案 A）：
--   seed_config.py:507-508 的 upsert 是
--       fetch_config = COALESCE(excluded.fetch_config, sources.fetch_config)
--   而 excluded 恒非 null → **整行覆盖**。已实测：往
--   fetch_config.blacklisted_handles 加一个自定义 handle，再跑一遍
--   seed 的 upsert 语义，名单会被**抹回内置 5 个号**。
--   屏蔽名单属 dad 的个人运营资产，丢失 = 被屏蔽的作者悄悄复活
--   （有害回退）—— 与 sprint-35 的门槛/阈值丢失（回落无害默认）
--   后果不同，不能靠「只用 migrate」的人工纪律兜底。
--
-- 为什么不用 source_keyword_filters（方案 C）：
--   那张表的语义是「按源 + 关键词 + match_field(title/content)」的
--   过滤规则，作者 handle 不是关键词；复用会拉伸语义，且要动
--   moni/fetch/keyword_match.py 这个唯一匹配引擎（AGENTS.md 铁律 1），
--   影响面比新增一张 6 列的轻量表大得多。
--
-- 来源列为什么存 source_name 而不是 sources.id（warden AC-15）：
--   * fetcher 只有类属性 source_name = "xbot_feed"（xbot_feed.py:557），
--     **没有** self.source_id（实测 grep 计数 = 0）。存 source_id 就得在
--     fetcher 里按 name 反查 id，多一次查询、多一个「查不到 → 名单静默失效」
--     的失败模式。
--   * source_name 与 articles.source 的写入语义一致（xbot_feed.py:1015）。
--   * 本表对 sources 无生命周期依赖 → 不建外键。
--
-- 幂等：CREATE TABLE / CREATE INDEX 都是 IF NOT EXISTS，重复执行安全。

CREATE TABLE IF NOT EXISTS source_author_blocks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name   TEXT    NOT NULL,
    handle        TEXT    NOT NULL,
    display_name  TEXT,
    blocked_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notes         TEXT,
    UNIQUE(source_name, handle)
);

CREATE INDEX IF NOT EXISTS idx_source_author_blocks_lookup
    ON source_author_blocks(source_name, handle);
