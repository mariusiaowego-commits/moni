"""tests/test_obsidian_push.py — Obsidian 待剪藏推送的微信生态判定 (v1.0.66)。

审计 B4/S-3: ``xbot_feed`` 是**混合源** (一半 wechat topic, 一半
xcircle topic)。之前它被整个塞进 ``WECHAT_SOURCES`` → xcircle 的推文
也被当成微信生态推进清单。现在改成按文章**实际挂的 tag** 判断:

* 源在 ``WECHAT_SOURCES`` 里 → 微信生态;
* 或者文章挂了 ``wechat`` / ``miniprogram`` tag → 微信生态
  (xbot_feed 的 wechat 半边正是这样进来的);
* xcircle 半边 (只挂 general-tech / ai-fintech) → 不进清单。

不联网, 不调 Obsidian CLI —— 只测筛选逻辑。
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.obsidian_push import (
    WECHAT_SOURCES,
    WECHAT_TAGS,
    _is_wechat_ecosystem,
    _source_label,
    fetch_high_value_articles,
)


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    close_db()
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MONI_DATA_DIR", str(data_dir))
    set_db_path(data_dir / "news.db")
    migrate()
    yield data_dir
    close_db()


def _insert_article(conn, aid, source, score, tag_names=()):
    conn.execute(
        "INSERT INTO articles (id, title, url, source, business_value_score, "
        "published_at, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (aid, f"t-{aid}", f"https://example.com/{aid}", source, score,
         "2026-11-01T00:00:00+00:00",
         datetime.now(timezone.utc).isoformat()),
    )
    for tag in tag_names:
        conn.execute(
            "INSERT OR IGNORE INTO tag_definitions (name, description, color, "
            "created_at, is_active) VALUES (?, ?, '#000', '2026-01-01', 1)",
            (tag, tag),
        )
        conn.execute(
            "INSERT OR IGNORE INTO article_tags (article_id, tag_name, "
            "predicted, created_at) VALUES (?, ?, 0, '2026-01-01')",
            (aid, tag),
        )
    conn.commit()


def test_xbot_feed_not_in_wechat_sources():
    """B4: 混合源不能整个算微信生态。"""
    assert "xbot_feed" not in WECHAT_SOURCES
    assert "wechat" in WECHAT_TAGS
    assert "miniprogram" in WECHAT_TAGS


def test_is_wechat_ecosystem_by_source():
    assert _is_wechat_ecosystem({"source": "bestblogs", "tag_names": ""}) is True
    assert _is_wechat_ecosystem({"source": "xbot_feed", "tag_names": ""}) is False


def test_is_wechat_ecosystem_by_tag():
    # xbot_feed 的 wechat 半边 (挂了 wechat tag) → 算微信生态
    assert _is_wechat_ecosystem(
        {"source": "xbot_feed", "tag_names": "wechat,miniprogram"}
    ) is True
    # xcircle 半边 (只挂 general-tech / ai-fintech) → 不算
    assert _is_wechat_ecosystem(
        {"source": "xbot_feed", "tag_names": "general-tech,ai-fintech"}
    ) is False


def test_source_label_is_honest():
    """S-3: xbot_feed 的 label 如实反映来源。"""
    assert _source_label("xbot_feed") == "X-bot 精选"
    assert _source_label("bestblogs") == "公众号"


def test_fetch_wechat_filter_uses_tags(fresh_db):
    """--source wechat: wechat 半边进, xcircle 半边不进。"""
    conn = get_db()
    _insert_article(conn, "a1", "xbot_feed", 90, ("wechat", "miniprogram"))
    _insert_article(conn, "a2", "xbot_feed", 85, ("general-tech", "ai-fintech"))
    _insert_article(conn, "a3", "bestblogs", 80)
    _insert_article(conn, "a4", "36kr_rss", 95, ("wechat",))

    rows = fetch_high_value_articles(min_score=70, days=7, source_filter="wechat")
    ids = {r["id"] for r in rows}
    assert "a1" in ids      # xbot_feed 的 wechat 半边 (按 tag)
    assert "a2" not in ids  # xcircle 半边 → 不进
    assert "a3" in ids      # 源在 WECHAT_SOURCES 里
    assert "a4" in ids      # 挂了 wechat tag → 进


def test_fetch_without_filter_returns_all(fresh_db):
    conn = get_db()
    _insert_article(conn, "b1", "xbot_feed", 90, ("general-tech",))
    _insert_article(conn, "b2", "bestblogs", 80)

    rows = fetch_high_value_articles(min_score=70, days=7)
    assert {r["id"] for r in rows} == {"b1", "b2"}


def test_fetch_by_specific_source_unchanged(fresh_db):
    conn = get_db()
    _insert_article(conn, "c1", "xbot_feed", 90, ("wechat",))
    _insert_article(conn, "c2", "bestblogs", 80)

    rows = fetch_high_value_articles(min_score=70, days=7, source_filter="xbot_feed")
    assert {r["id"] for r in rows} == {"c1"}
