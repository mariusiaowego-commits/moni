-- Migration 044: 清理历史孤儿行 (sprint-52)
-- allow-missing-tables: 手搓的精简测试库可能没有 article_keyword_hits；
-- 缺表时跳过该句，不影响真实库（010 起就有这张表）。
--
-- 现象: article_tags 305 行、article_keyword_hits 344 行、llm_jobs 3 行
--       指向已经不存在的文章。
-- 根因: 这些表都声明了 ON DELETE CASCADE，但 SQLite 默认 foreign_keys=OFF。
--       残留行的 created_at 全部落在 2026-06-16 ~ 06-18，是那批删除发生时
--       连接上没开外键留下的。
-- 现状: moni/db/connection.py 每次建连都执行 PRAGMA foreign_keys = ON，
--       实测新删文章会级联清掉子表，不再产生新孤儿。这里只清存量。
--
-- 幂等: 重复执行无行可删，影响 0 行。

DELETE FROM article_tags
WHERE article_id NOT IN (SELECT id FROM articles);

DELETE FROM article_keyword_hits
WHERE article_id NOT IN (SELECT id FROM articles);

DELETE FROM llm_jobs
WHERE article_id NOT IN (SELECT id FROM articles);
