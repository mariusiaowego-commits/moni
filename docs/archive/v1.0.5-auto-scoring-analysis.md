# Moni 自动评分指标分析报告

> 基于 news.db 现有数据 (320 篇文章, 6 个源, 24 个发布者, 6 个标签) 分析

---

## 一、可直接从现有数据计算的指标

### 1. 内容完整度 (Content Completeness) — 0~100 分

基于字段填充率计算，无需任何新字段。

```sql
-- 单篇文章内容完整度评分
SELECT id, title,
  (
    -- raw_content 存在且长度 > 200: +30分
    CASE WHEN LENGTH(COALESCE(raw_content,'')) > 200 THEN 30
         WHEN LENGTH(COALESCE(raw_content,'')) > 50  THEN 15
         ELSE 0 END
    -- summary 存在: +20分
    + CASE WHEN summary IS NOT NULL AND summary != '' THEN 20 ELSE 0 END
    -- fact_check 存在: +20分
    + CASE WHEN fact_check_conclusion IS NOT NULL THEN 20 ELSE 0 END
    -- publisher 存在: +10分
    + CASE WHEN publisher IS NOT NULL AND publisher != '' THEN 10 ELSE 0 END
    -- published_at 存在: +10分
    + CASE WHEN published_at IS NOT NULL THEN 10 ELSE 0 END
    -- 有标签: +10分
    + CASE WHEN id IN (SELECT article_id FROM article_tags) THEN 10 ELSE 0 END
  ) as completeness_score
FROM articles;
```

### 2. 关键词命中密度 (Keyword Hit Density) — 连续值

衡量文章与监控主题的匹配深度。每篇平均 5.78 个命中，最高 41 个。

```sql
-- 关键词命中密度 (命中数 / 内容千字数)
SELECT a.id, a.title,
  COUNT(akh.id) as hit_count,
  ROUND(LENGTH(COALESCE(a.raw_content,'')) / 1000.0, 1) as content_kchars,
  ROUND(COUNT(akh.id) * 1.0 / MAX(LENGTH(COALESCE(a.raw_content,'1')) / 1000.0, 0.1), 2) as hit_density
FROM articles a
LEFT JOIN article_keyword_hits akh ON akh.article_id = a.id
GROUP BY a.id
ORDER BY hit_density DESC;
```

### 3. 主题相关度 (Topic Relevance Score) — 0~100 分

基于命中关键词的多样性 (覆盖了多少个不同的 tag 领域) 和命中数量。

```sql
-- 主题相关度: 按 tag 维度统计命中
SELECT a.id, a.title,
  COUNT(DISTINCT akh.tag_name) as tag_coverage,    -- 命中了几个主题领域
  COUNT(akh.id) as total_hits,
  -- 评分: 多领域命中加权
  MIN(COUNT(DISTINCT akh.tag_name) * 20, 60)       -- 最多覆盖 3 个 tag → 60分
  + MIN(COUNT(akh.id), 40)                          -- 最多 40 个命中 → 40分
  as relevance_score
FROM articles a
LEFT JOIN article_keyword_hits akh ON akh.article_id = a.id
GROUP BY a.id
ORDER BY relevance_score DESC;
```

### 4. 时效性 (Timeliness Score) — 0~100 分

基于 published_at 与 fetched_at 的时间差。36kr 平均 6.8h，bestblogs 平均 214h。

```sql
-- 时效性评分 (抓取延迟越小分越高)
SELECT id, title, source,
  published_at,
  ROUND((julianday(fetched_at) - julianday(published_at)) * 24, 1) as delay_hours,
  CASE
    WHEN (julianday(fetched_at) - julianday(published_at)) * 24 <= 1   THEN 100  -- 1小时内
    WHEN (julianday(fetched_at) - julianday(published_at)) * 24 <= 6   THEN 90   -- 6小时内
    WHEN (julianday(fetched_at) - julianday(published_at)) * 24 <= 24  THEN 70   -- 1天内
    WHEN (julianday(fetched_at) - julianday(published_at)) * 24 <= 72  THEN 50   -- 3天内
    WHEN (julianday(fetched_at) - julianday(published_at)) * 24 <= 168 THEN 30   -- 7天内
    ELSE 10
  END as timeliness_score
FROM articles
WHERE published_at IS NOT NULL;
```

### 5. 内容深度 (Content Depth) — 0~100 分

基于正文长度。avg=1591 chars, min=4, max=23640。

```sql
-- 内容深度评分
SELECT id, title,
  LENGTH(COALESCE(raw_content,'')) as content_len,
  CASE
    WHEN LENGTH(COALESCE(raw_content,'')) >= 5000 THEN 100  -- 深度长文
    WHEN LENGTH(COALESCE(raw_content,'')) >= 2000 THEN 80   -- 中等文章
    WHEN LENGTH(COALESCE(raw_content,'')) >= 800  THEN 60   -- 短文
    WHEN LENGTH(COALESCE(raw_content,'')) >= 200  THEN 40   -- 摘要
    WHEN LENGTH(COALESCE(raw_content,'')) >= 50   THEN 20   -- 片段
    ELSE 0                                                    -- 几乎无内容
  END as depth_score
FROM articles;
```

### 6. 来源可信度 (Source Credibility) — 静态配置分

基于 sources 表和发布者的稳定性。可硬编码映射。

```sql
-- 来源可信度评分 (静态映射)
SELECT a.id, a.title, a.source, a.publisher,
  CASE a.source
    WHEN '36kr_rss'     THEN 80   -- 36氪，主流科技媒体
    WHEN 'qbitai'       THEN 85   -- 量子位，专业AI媒体
    WHEN 'mpaypass'     THEN 75   -- 移动支付网，垂直行业媒体
    WHEN 'bestblogs'    THEN 70   -- 聚合源，需看 publisher
    WHEN 'wechat_rss_a' THEN 65   -- 微信公众号RSS，质量参差
    WHEN 'wechat_rss_b' THEN 65
    ELSE 50
  END as source_credibility
FROM articles a;
```

### 7. 黑名单过滤得分 (Blacklist Filter Score) — 二值

利用 source_keyword_filters blacklist 判断文章是否触发了排除关键词。

```sql
-- 检查文章是否命中了黑名单关键词 (当前只有 36kr 和 qbitai 有 blacklist)
SELECT a.id, a.title, a.source,
  CASE WHEN EXISTS (
    SELECT 1 FROM article_keyword_hits akh
    JOIN source_keyword_filters skf ON skf.id = akh.filter_id
    WHERE akh.article_id = a.id AND skf.mode = 'blacklist'
  ) THEN 0 ELSE 100 END as blacklist_clean_score
FROM articles a;
```

### 8. 事实核查状态 (Fact-Check Status) — 0/50/100

```sql
-- 事实核查评分
SELECT id, title,
  CASE
    WHEN fact_check_conclusion IS NOT NULL AND fact_check_conclusion LIKE '%可信度：高%' THEN 100
    WHEN fact_check_conclusion IS NOT NULL AND fact_check_conclusion LIKE '%可信度：中%' THEN 60
    WHEN fact_check_conclusion IS NOT NULL THEN 40
    ELSE 0
  END as fact_check_score
FROM articles;
```

### 9. LLM 处理质量 (LLM Processing Quality)

基于 llm_jobs 表，评估文章是否经过了 LLM 处理及处理结果。

```sql
-- LLM 处理覆盖率和质量
SELECT a.id, a.title,
  COALESCE(s.summary_count, 0) as summary_jobs_done,
  COALESCE(fc.fc_count, 0) as factcheck_jobs_done,
  (COALESCE(s.summary_count, 0) * 40 + COALESCE(fc.fc_count, 0) * 60) as llm_score
FROM articles a
LEFT JOIN (
  SELECT article_id, COUNT(*) as summary_count
  FROM llm_jobs WHERE task_type = 'summary' AND status = 'done'
  GROUP BY article_id
) s ON s.article_id = a.id
LEFT JOIN (
  SELECT article_id, COUNT(*) as fc_count
  FROM llm_jobs WHERE task_type = 'fact_check' AND status = 'done'
  GROUP BY article_id
) fc ON fc.article_id = a.id;
```

### 10. 发布者权威度 (Publisher Authority) — 可基于频次推断

当前有 24 个不同发布者。高频出现的发布者 (如"移动支付网" 162 篇) 可赋予更高分。

```sql
-- 发布者权威度 (基于历史文章量和标签覆盖率)
SELECT a.id, a.title, a.publisher,
  pub_stats.article_count,
  pub_stats.avg_tag_count,
  CASE
    WHEN pub_stats.article_count >= 50 THEN 90   -- 高频稳定来源
    WHEN pub_stats.article_count >= 10 THEN 70
    WHEN pub_stats.article_count >= 3  THEN 50
    ELSE 30
  END as publisher_authority
FROM articles a
LEFT JOIN (
  SELECT publisher,
    COUNT(*) as article_count,
    ROUND(AVG(tag_count), 1) as avg_tag_count
  FROM (
    SELECT a2.publisher, a2.id,
      (SELECT COUNT(*) FROM article_tags at2 WHERE at2.article_id = a2.id) as tag_count
    FROM articles a2 WHERE a2.publisher IS NOT NULL
  ) GROUP BY publisher
) pub_stats ON pub_stats.publisher = a.publisher;
```

---

## 二、需要新增字段才能实现的指标

### 1. 文章质量评分 (quality_score) — 建议新增

| 字段 | 类型 | 说明 |
|------|------|------|
| `quality_score` | REAL | 0~100 综合评分 |
| `quality_scored_at` | TEXT | 评分时间 ISO8601 |
| `quality_details` | TEXT | JSON 各维度得分明细 |

**理由**: 虽然上面 10 个维度都可以实时计算，但缓存一个综合分可以避免每次查询都做大量 JOIN。

### 2. 文章去重/相似度 (duplicate_of) — 建议新增

| 字段 | 类型 | 说明 |
|------|------|------|
| `duplicate_of` | TEXT | 指向原始文章的 FK (NULL=原创) |
| `similarity_score` | REAL | 0~1 与最相似文章的余弦相似度 |

**理由**: bestblogs 和 wechat 源经常转载同一内容。当前无法自动检测重复。需要 LLM embedding 或 simhash。

### 3. 信息增量 (information_delta) — 建议新增

| 字段 | 类型 | 说明 |
|------|------|------|
| `info_delta_score` | REAL | 相比同主题近期文章的信息增量 0~100 |

**理由**: 判断一篇文章是否提供了新信息（而非旧闻翻炒）。需要 embedding 比较，当前数据无此能力。

### 4. 读者互动/传播指标 — 需外部数据

| 字段 | 类型 | 说明 |
|------|------|------|
| `view_count` | INTEGER | 阅读数 (微信文章可抓) |
| `share_count` | INTEGER | 分享/转发数 |
| `comment_count` | INTEGER | 评论数 |
| `like_count` | INTEGER | 点赞数 |

**理由**: 当前 articles 表无任何传播/互动数据。微信公众号文章理论上可以通过 API 获取阅读量。

### 5. 人工标注 (human_label) — 建议新增

| 字段 | 类型 | 说明 |
|------|------|------|
| `human_label` | TEXT | 'high_value' / 'low_value' / 'spam' |
| `human_labeled_at` | TEXT | 标注时间 |

**理由**: 用于校准自动评分模型。article_tags 的 predicted=0 是人工标签，但只有 317 条且都是主题标签而非质量标签。

---

## 三、综合评分方案 (综合以上可直接计算的指标)

### 加权综合分 SQL

```sql
-- =====================================================
-- Moni 文章自动综合评分 (Auto Quality Score)
-- 基于现有数据，无需新增字段
-- =====================================================
WITH article_metrics AS (
  SELECT
    a.id,
    a.title,
    a.source,
    a.publisher,

    -- 1. 内容完整度 (权重 15%)
    (
      CASE WHEN LENGTH(COALESCE(a.raw_content,'')) > 200 THEN 30
           WHEN LENGTH(COALESCE(a.raw_content,'')) > 50  THEN 15 ELSE 0 END
      + CASE WHEN a.summary IS NOT NULL AND a.summary != '' THEN 20 ELSE 0 END
      + CASE WHEN a.fact_check_conclusion IS NOT NULL THEN 20 ELSE 0 END
      + CASE WHEN a.publisher IS NOT NULL AND a.publisher != '' THEN 10 ELSE 0 END
      + CASE WHEN a.published_at IS NOT NULL THEN 10 ELSE 0 END
      + CASE WHEN a.id IN (SELECT article_id FROM article_tags) THEN 10 ELSE 0 END
    ) as completeness,

    -- 2. 内容深度 (权重 15%)
    CASE
      WHEN LENGTH(COALESCE(a.raw_content,'')) >= 5000 THEN 100
      WHEN LENGTH(COALESCE(a.raw_content,'')) >= 2000 THEN 80
      WHEN LENGTH(COALESCE(a.raw_content,'')) >= 800  THEN 60
      WHEN LENGTH(COALESCE(a.raw_content,'')) >= 200  THEN 40
      WHEN LENGTH(COALESCE(a.raw_content,'')) >= 50   THEN 20
      ELSE 0
    END as depth,

    -- 3. 关键词命中密度 (权重 20%)
    COALESCE(kw.hit_count, 0) * 5 as keyword_score,  -- 每个命中 5 分，上限 100

    -- 4. 主题覆盖度 (权重 10%)
    COALESCE(kw.tag_diversity, 0) * 25 as topic_score,  -- 每个 tag 25 分

    -- 5. 时效性 (权重 15%)
    CASE
      WHEN (julianday(a.fetched_at) - julianday(a.published_at)) * 24 <= 6   THEN 100
      WHEN (julianday(a.fetched_at) - julianday(a.published_at)) * 24 <= 24  THEN 70
      WHEN (julianday(a.fetched_at) - julianday(a.published_at)) * 24 <= 72  THEN 50
      WHEN (julianday(a.fetched_at) - julianday(a.published_at)) * 24 <= 168 THEN 30
      ELSE 10
    END as timeliness,

    -- 6. 来源可信度 (权重 15%)
    CASE a.source
      WHEN 'qbitai'       THEN 85
      WHEN '36kr_rss'     THEN 80
      WHEN 'mpaypass'     THEN 75
      WHEN 'bestblogs'    THEN 70
      WHEN 'wechat_rss_a' THEN 65
      WHEN 'wechat_rss_b' THEN 65
      ELSE 50
    END as source_cred,

    -- 7. 事实核查 (权重 10%)
    CASE
      WHEN a.fact_check_conclusion IS NOT NULL AND a.fact_check_conclusion LIKE '%可信度：高%' THEN 100
      WHEN a.fact_check_conclusion IS NOT NULL AND a.fact_check_conclusion LIKE '%可信度：中%' THEN 60
      WHEN a.fact_check_conclusion IS NOT NULL THEN 40
      ELSE 0
    END as fact_check_score

  FROM articles a
  LEFT JOIN (
    SELECT article_id,
      COUNT(*) as hit_count,
      COUNT(DISTINCT tag_name) as tag_diversity
    FROM article_keyword_hits
    GROUP BY article_id
  ) kw ON kw.article_id = a.id
)
SELECT
  id,
  title,
  source,
  completeness,
  depth,
  MIN(keyword_score, 100) as keyword_score,
  MIN(topic_score, 100) as topic_score,
  timeliness,
  source_cred,
  fact_check_score,
  -- 加权总分
  ROUND(
    completeness    * 0.15
    + depth         * 0.15
    + MIN(keyword_score, 100) * 0.20
    + MIN(topic_score, 100)   * 0.10
    + timeliness    * 0.15
    + source_cred   * 0.15
    + fact_check_score * 0.10
  , 1) as auto_score
FROM article_metrics
ORDER BY auto_score DESC;
```

---

## 四、数据现状总结

| 维度 | 覆盖率 | 可用性 |
|------|--------|--------|
| raw_content (正文) | 99% (317/320) | ✅ 优秀 |
| published_at (发布时间) | 100% (320/320) | ✅ 完美 |
| keyword_hits (关键词命中) | 100% (320/320) | ✅ 完美 |
| publisher (发布者) | 71% (228/320) | ⚠️ 可用，缺 29% |
| article_tags (主题标签) | 41% (130/320) | ⚠️ 需提升覆盖率 |
| summary (摘要) | 3% (11/320) | ❌ 严重不足 |
| fact_check (事实核查) | 0.6% (2/320) | ❌ 几乎无覆盖 |
| ai_summary (AI摘要) | 9% (30/320) | ⚠️ 需提升 |

### 优先行动建议

1. **立即可做**: 用上述 SQL 直接计算综合分，无需任何 schema 变更
2. **短期提升**: 加大 LLM summary 和 fact_check 的覆盖率（当前 llm_jobs 有 17 个 summary done）
3. **中期新增**: 添加 `quality_score` + `quality_details` (JSON) 缓存字段
4. **长期规划**: 引入 embedding 做去重/相似度检测，接入微信阅读量 API
