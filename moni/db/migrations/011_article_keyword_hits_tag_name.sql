-- v1.0.5.20 (part 2): article_keyword_hits 加 tag_name 列
-- 记录每个关键词属于哪个 tag

ALTER TABLE article_keyword_hits ADD COLUMN tag_name TEXT;
