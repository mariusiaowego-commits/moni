-- 055: BestBlogs 全量抓取（sprint-68 / v1.0.115）
--
-- 旧行为：max_articles_total=1000 按 feed 顺序截断。478 个 feed 每轮只跑到
-- 第 ~106 个，排在后面的信源（财新在第 139 位）永远拿不到预算。
--
-- 新行为：每个 feed 的全量条目先过 (source, url) 去重，再交给 tag 门；
-- 上限只当安全阀，由用户在 /config 里自己调：
--   max_articles_per_feed = 0       → 不限，feed 返回多少取多少
--   max_articles_total    = 200000  → 正常 478 feed × ≤50 条 ≈ 5300，永远碰不到
--   max_feeds             = 500     → 当前 478，留 22 个余量
--   timeout_sec           = 2700    → 只作用于 Web 手动任务看门狗，cron 不受影响
--
-- 只动 bestblogs 一行；bestblog-podcast / -yt / -x 各有自己的 fetch_config。
UPDATE sources
SET fetch_config = json_set(
        COALESCE(NULLIF(fetch_config, ''), '{}'),
        '$.max_feeds', 500,
        '$.max_articles_per_feed', 0,
        '$.max_articles_total', 200000,
        '$.timeout_sec', 2700
    ),
    updated_at = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')
WHERE name = 'bestblogs';
