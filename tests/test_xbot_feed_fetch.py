"""tests/test_xbot_feed_fetch.py — xbot_feed 源 (v1.0.66) 的离线测试。

覆盖 (照 moni-wechat-source-plan.md 第 4 节的用例清单, 命名改成
``xbot_feed``):

  A. 解析层       — parse() 的字段映射、URL 归一、topic → tag
  B. 抓取层       — 读本地 clone 的 feed/*.json, 坏文件跳过, 空目录报错
  C. 去重层       — tweet id 去重、重复导入幂等、跨源软去重
  D. 闸门与 tag   — 黑名单、bypass 合成 tag、seed 定义、注册、YAML
  E. git 层       — clone vs pull 分支、git 失败报错 (临时 bare repo, 不联网)

不联网: git 操作全在 tmp_path 里的本地 bare repo 上跑; 不硬编码
``/Users/...`` 路径 (照 AGENTS.md 的测试纪律)。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "xbot_feed"
_SOURCE_NAME = "xbot_feed"
_SOURCE_ID = "src-xbot-feed"


# ── fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Isolated tmp sqlite + tmp MONI_DATA_DIR."""
    close_db()
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MONI_DATA_DIR", str(data_dir))
    set_db_path(data_dir / "news.db")
    migrate()
    yield data_dir
    close_db()


def _seed_source(conn, name=_SOURCE_NAME, fetch_config=None, is_active=1,
                 source_id=_SOURCE_ID):
    conn.execute("DELETE FROM source_tags WHERE source_id=?", (source_id,))
    conn.execute("DELETE FROM sources WHERE id=? OR name=?", (source_id, name))
    conn.commit()
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, notes, fetch_config, created_at, updated_at) "
        "VALUES (?, ?, ?, 'json', ?, 60, ?, ?, ?, '2026-01-01', '2026-01-01')",
        (source_id, name, "X-bot 精选",
         "https://github.com/mariusiaowego-commits/xbot-feed.git",
         is_active, "test", fetch_config),
    )
    conn.commit()


def _seed_tags(conn, names=("wechat", "miniprogram", "general-tech", "ai-fintech"),
               keywords=None):
    """Seed tag definitions.

    ``keywords`` maps tag name → keyword list (JSON stored in the row).
    Defaults to a small keyword set per tag so the B1 keyword gate has
    something real to match against.
    """
    if keywords is None:
        keywords = {
            "wechat": ["微信", "小程序"],
            "miniprogram": ["小程序"],
            "general-tech": ["AI", "模型", "RSI"],
            "ai-fintech": ["AI", "A股", "投顾", "量化"],
        }
    for name in names:
        kws = keywords.get(name)
        conn.execute(
            "INSERT INTO tag_definitions (name, description, color, created_at, "
            "is_active, keywords) "
            "VALUES (?, ?, '#000000', '2026-01-01', 1, ?) "
            "ON CONFLICT(name) DO UPDATE SET is_active=1, keywords=excluded.keywords",
            (name, name, json.dumps(kws, ensure_ascii=False) if kws else None),
        )
    conn.commit()


def _set_tag_keywords(conn, tag_name, keywords):
    """Replace one tag's keyword list (the web-端 edit path)."""
    conn.execute(
        "UPDATE tag_definitions SET keywords = ? WHERE name = ?",
        (json.dumps(keywords, ensure_ascii=False), tag_name),
    )
    conn.commit()


def _bind_tag(conn, source_id, tag_name):
    conn.execute(
        "INSERT OR IGNORE INTO source_tags (source_id, tag_name, created_at) "
        "VALUES (?, ?, '2026-01-01')",
        (source_id, tag_name),
    )
    conn.commit()


def _make_clone(data_dir: Path, *, with_feed: bool = True,
                repo_url: str = "") -> Path:
    """Build a fake "clone" of the upstream repo under ``data_dir``.

    ``repo_url`` non-empty → the fetcher will try a real clone against it
    (used by the clone-branch test, backed by a local bare repo).

    The dated feed files are named with **today / yesterday** (not the
    fixture's own 2026-11-0x dates) so the ``max_days`` filter — which
    compares the date *in the filename* against today — behaves the same
    whenever the suite runs.  The file *contents* are the fixtures verbatim,
    so tweet ids stay stable.
    """
    from datetime import date, timedelta

    clone = data_dir / "xbot-feed"
    clone.mkdir(parents=True, exist_ok=True)
    if with_feed:
        feed = clone / "feed"
        feed.mkdir(parents=True, exist_ok=True)
        today = date.today()
        for fixture_name, offset in (("2026-11-03.json", 0),
                                     ("2026-11-02.json", 1)):
            stamp = (today - timedelta(days=offset)).isoformat()
            shutil.copy2(_FIXTURE_DIR / fixture_name, feed / f"{stamp}.json")
        shutil.copy2(_FIXTURE_DIR / "latest.json", feed / "latest.json")
    return clone


def _seeded_fetcher(data_dir, fetch_config=None, clone_dir=None, repo_url=None):
    """Build a FetcherXbotFeed wired to a local fake clone."""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    conn = get_db()
    _seed_tags(conn)
    cfg = {
        "bypass_keyword_filters": True,
        "repo_url": repo_url or "https://example.invalid/xbot-feed.git",
        "max_days": 30,
    }
    if clone_dir is not None:
        cfg["clone_dir"] = str(clone_dir)
    if fetch_config:
        cfg.update(fetch_config)
    _seed_source(conn, fetch_config=json.dumps(cfg, ensure_ascii=False))
    for tag in ("wechat", "miniprogram", "general-tech", "ai-fintech"):
        _bind_tag(conn, _SOURCE_ID, tag)
    conn.commit()
    return FetcherXbotFeed()


def _load_fixture(name="2026-11-03.json"):
    return json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))


# ── A. 解析层 ───────────────────────────────────────────────────────────

def test_parse_maps_fields(fresh_db):
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    conn = get_db()
    _seed_tags(conn)
    payload = _load_fixture()
    item = next(i for i in payload["items"] if i["topic"] == "wechat"
                and i["author_handle"] == "shihuangtimes")
    f = FetcherXbotFeed()
    f._prepare({"blacklisted_handles": [], "skip_existing_urls": False})

    art = f.parse(dict(item))
    assert art is not None
    assert art["source"] == "xbot_feed"
    assert art["url"] == "https://x.com/shihuangtimes/status/2117412872158662796"
    assert art["title"] == "微信小程序代码审核通过啦！速度真够快的，不到四小时就搞定👍 https://t.co/ThZwe0bHxj"
    assert art["publisher"] == "拾荒旧时光 (@shihuangtimes)"
    assert art["published_at"] == "2026-11-03T00:25:43+00:00"
    assert art["xbot_topic"] == "wechat"
    # 只挂**真实命中**的 tag: 正文命中 微信/小程序 → wechat + miniprogram
    assert art["xbot_tags"] == ["wechat", "miniprogram"]
    assert art["xbot_candidate_tags"] == ["wechat", "miniprogram"]
    # hit 里记的是**真实关键词** (不是 (topic:xxx) 合成串)
    hit_kws = {h["keyword"] for h in art["xbot_hits"]}
    assert hit_kws <= {"微信", "小程序"}
    assert hit_kws
    # 互动数字落在正文末尾, 正文本身保留
    assert "❤ 1" in art["content"]
    assert "微信小程序代码审核通过" in art["content"]


def test_parse_url_normalization():
    from moni.fetch.sources.xbot_feed import normalize_tweet_url

    a = normalize_tweet_url("https://twitter.com/foo/status/123?s=20",
                             handle="foo", tweet_id="123")
    b = normalize_tweet_url("https://x.com/foo/status/123",
                             handle="foo", tweet_id="123")
    c = normalize_tweet_url("http://mobile.twitter.com/foo/status/123",
                             handle="foo", tweet_id="123")
    assert a == b == c == "https://x.com/foo/status/123"


def test_topic_to_tag_mapping():
    from moni.fetch.sources.xbot_feed import TOPIC_TAGS, topic_tags

    # wechat 话题 → 微信生态 + 小程序
    assert topic_tags("wechat") == ("wechat", "miniprogram")
    assert TOPIC_TAGS["wechat"] == ("wechat", "miniprogram")
    # xcircle 话题 → 通用科技 + AI×金融
    assert topic_tags("xcircle") == ("general-tech", "ai-fintech")
    # 未知 topic → 空 (不入库)
    assert topic_tags("nope") == ()
    assert topic_tags("") == ()


def test_parse_unknown_topic_is_skipped(fresh_db):
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    conn = get_db()
    _seed_tags(conn)
    payload = _load_fixture()
    item = next(i for i in payload["items"] if i["topic"] == "brand_new_topic")
    f = FetcherXbotFeed()
    f._prepare({"blacklisted_handles": [], "skip_existing_urls": False})
    assert f.parse(dict(item)) is None


def test_parse_missing_id_and_url_is_skipped(fresh_db):
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    f = FetcherXbotFeed()
    f._prepare({"blacklisted_handles": [], "skip_existing_urls": False})
    assert f.parse({"text": "没有 id 也没有 url", "topic": "wechat"}) is None


def test_parse_bad_created_at_gives_none(fresh_db):
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    conn = get_db()
    _seed_tags(conn)
    f = FetcherXbotFeed()
    f._prepare({"blacklisted_handles": [], "skip_existing_urls": False})
    art = f.parse({
        "id": "123", "url": "https://x.com/a/status/123", "handle": "a",
        "text": "微信小程序 hi", "topic": "wechat", "created_at": "not-a-date",
    })
    assert art["published_at"] is None


# ── B. 抓取层 ───────────────────────────────────────────────────────────

def test_fetch_reads_feed_files_and_dedupes_by_id(fresh_db, monkeypatch):
    """两个日期文件 + latest.json (内容相同) → 按 tweet id 去重。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    data_dir = fresh_db
    clone = _make_clone(data_dir)
    f = _seeded_fetcher(data_dir, clone_dir=clone)
    # 不跑 git (clone 已经是"存在"的目录, 但没有 .git → 会走 clone 分支)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)

    raws = f.fetch()
    ids = [r["id"] for r in raws]
    # 2026-11-03 (3 wechat + 3 xcircle) + 2026-11-02 (1) = 7 条唯一 id
    assert len(ids) == len(set(ids))
    assert len(ids) == 7
    assert "2117412872158662796" in ids
    assert "2117000000000000001" in ids


def test_fetch_max_days_limits_dated_files(fresh_db, monkeypatch):
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    data_dir = fresh_db
    clone = _make_clone(data_dir)
    f = _seeded_fetcher(data_dir, clone_dir=clone, fetch_config={"max_days": 1})
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)

    raws = f.fetch()
    ids = [r["id"] for r in raws]
    # 只导最近 1 天的日期文件 + latest.json (内容同 2026-11-03)
    assert "2117000000000000001" not in ids   # 2026-11-02 被 max_days 挡掉
    assert "2117412872158662796" in ids


def test_fetch_skips_broken_feed_file(fresh_db, monkeypatch):
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    data_dir = fresh_db
    clone = _make_clone(data_dir)
    (clone / "feed" / "broken.json").write_text("{not json", encoding="utf-8")
    f = _seeded_fetcher(data_dir, clone_dir=clone)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)

    raws = f.fetch()  # 不抛异常
    assert len(raws) == 7


def test_fetch_empty_feed_dir_raises(fresh_db, monkeypatch):
    """目录里没有 feed JSON → 记 error, 不能静默返回 0 篇。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    data_dir = fresh_db
    clone = _make_clone(data_dir, with_feed=False)
    f = _seeded_fetcher(data_dir, clone_dir=clone)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)

    with pytest.raises(RuntimeError, match="no feed JSON"):
        f.fetch()


def test_run_reports_error_when_no_feed(fresh_db, monkeypatch):
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    data_dir = fresh_db
    clone = _make_clone(data_dir, with_feed=False)
    f = _seeded_fetcher(data_dir, clone_dir=clone)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)

    result = f.run()
    assert result["error"] is not None
    assert result["inserted"] == 0
    assert result["fetched"] == 0


# ── C. 去重层 ───────────────────────────────────────────────────────────

def test_cross_source_soft_dedupe(fresh_db, monkeypatch):
    """bestblog-x 已存了同一条 (twitter.com 变体) → 不入库。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    conn = get_db()
    conn.execute(
        "INSERT INTO articles (id, title, url, source, published_at, fetched_at) "
        "VALUES ('best1', 'dup', "
        "'https://twitter.com/shihuangtimes/status/2117412872158662796?s=20', "
        "'bestblog-x', NULL, '2026-11-01')"
    )
    conn.commit()

    f = FetcherXbotFeed()
    f._prepare({"blacklisted_handles": [], "skip_existing_urls": True})
    assert f._is_url_exists_globally(
        "https://x.com/shihuangtimes/status/2117412872158662796",
        "2117412872158662796",
    ) is True

    item = next(i for i in _load_fixture()["items"]
                if i["author_handle"] == "shihuangtimes")
    assert f.parse(dict(item)) is None


def test_repeated_import_is_idempotent(fresh_db, monkeypatch):
    """跑两遍 → 第二遍 inserted == 0。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    data_dir = fresh_db
    clone = _make_clone(data_dir)
    f = _seeded_fetcher(data_dir, clone_dir=clone)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)

    first = f.run()
    assert first["error"] is None
    # 7 条唯一 id − 1 条黑名单 (jkghzone) − 1 条未知 topic − 1 条零关键词命中
    # (Nadja_lernt_Q 那条讲跨境支付, 不含 微信/小程序) = 4
    assert first["inserted"] == 4

    conn = get_db()
    n1 = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE source=?", (_SOURCE_NAME,)
    ).fetchone()[0]
    assert n1 == 4

    # 第二遍: 状态文件里已经有这些 id → 一条都不再导
    f2 = _seeded_fetcher(data_dir, clone_dir=clone)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)
    second = f2.run()
    assert second["error"] is None
    assert second["inserted"] == 0

    n2 = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE source=?", (_SOURCE_NAME,)
    ).fetchone()[0]
    assert n2 == 4


def test_state_not_written_when_fetch_fails(fresh_db, monkeypatch):
    """git 失败 → 状态文件不能被写 (否则这批内容就永久丢了)。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    data_dir = fresh_db
    clone = _make_clone(data_dir, with_feed=False)
    f = _seeded_fetcher(data_dir, clone_dir=clone)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)

    result = f.run()
    assert result["error"] is not None
    state_path = data_dir / ".xbot_feed_state.json"
    assert not state_path.exists()


# ── D. 闸门与 tag ───────────────────────────────────────────────────────

def test_blacklisted_handle_is_dropped(fresh_db):
    from moni.fetch.sources.xbot_feed import (
        DEFAULT_BLACKLISTED_HANDLES,
        FetcherXbotFeed,
    )

    f = FetcherXbotFeed()
    f._prepare({})
    assert "jkghzone" in DEFAULT_BLACKLISTED_HANDLES

    item = next(i for i in _load_fixture()["items"]
                if i["author_handle"] == "jkghzone")
    assert f.parse(dict(item)) is None


def test_blacklist_is_configurable(fresh_db):
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    conn = get_db()
    _seed_tags(conn)
    f = FetcherXbotFeed()
    f._prepare({"blacklisted_handles": ["oran_ge"]})
    item = next(i for i in _load_fixture()["items"]
                if i["author_handle"] == "oran_ge")
    assert f.parse(dict(item)) is None

    # 换成别的 handle, oran_ge 就放行
    f2 = FetcherXbotFeed()
    f2._prepare({"blacklisted_handles": ["someone_else"]})
    assert f2.parse(dict(item)) is not None


def test_tag_gate_tags_follow_real_keyword_hits(fresh_db, monkeypatch):
    """只挂**真实命中**的 tag —— xcircle 不会挂 wechat。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    data_dir = fresh_db
    clone = _make_clone(data_dir)
    f = _seeded_fetcher(data_dir, clone_dir=clone)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)

    result = f.run()
    assert result["error"] is None

    conn = get_db()
    rows = conn.execute(
        "SELECT a.url, t.tag_name FROM articles a "
        "JOIN article_tags t ON t.article_id = a.id "
        "WHERE a.source = ?", (_SOURCE_NAME,)
    ).fetchall()
    by_url: dict[str, set[str]] = {}
    for r in rows:
        by_url.setdefault(r["url"], set()).add(r["tag_name"])

    # 每篇都有 tag (零 tag 的文章前端筛不到)
    n_articles = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE source=?", (_SOURCE_NAME,)
    ).fetchone()[0]
    assert len(by_url) == n_articles

    # wechat 话题命中 微信/小程序 → wechat + miniprogram
    wechat_tags = by_url["https://x.com/shihuangtimes/status/2117412872158662796"]
    assert wechat_tags == {"wechat", "miniprogram"}

    # xcircle 话题 (oran_ge "AI 是放大器") → 只挂 general-tech/ai-fintech,
    # **绝不挂 wechat** (B1 的核心回归)
    xcircle_tags = by_url["https://x.com/oran_ge/status/2117414151186502091"]
    assert xcircle_tags == {"general-tech", "ai-fintech"}
    assert "wechat" not in xcircle_tags
    assert "miniprogram" not in xcircle_tags

    # 零 tag 的文章数必须是 0
    n_untagged = conn.execute(
        "SELECT COUNT(*) FROM articles a WHERE a.source = ? AND NOT EXISTS ("
        "  SELECT 1 FROM article_tags t WHERE t.article_id = a.id)",
        (_SOURCE_NAME,),
    ).fetchone()[0]
    assert n_untagged == 0


def test_bypass_hits_are_dropped_from_article_tags(fresh_db, monkeypatch):
    """bypass 合成的全量命中不得进 article_tags (否则 tag 串台)。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    data_dir = fresh_db
    clone = _make_clone(data_dir)
    f = _seeded_fetcher(data_dir, clone_dir=clone)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)
    f.run()

    conn = get_db()
    # 没有任何一条 article_keyword_hits 记的是 "(bypass)"
    rows = conn.execute(
        "SELECT keyword FROM article_keyword_hits WHERE keyword = '(bypass)'"
    ).fetchall()
    assert rows == []


def test_item_with_no_keyword_hit_is_not_ingested(fresh_db, monkeypatch):
    """零命中 → 不入库 (不产生零 tag 文章)。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    data_dir = fresh_db
    clone = _make_clone(data_dir)
    f = _seeded_fetcher(data_dir, clone_dir=clone)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)

    # 把 wechat tag 的关键词改成跟正文完全无关的词
    conn = get_db()
    _set_tag_keywords(conn, "wechat", ["完全不相关的关键词"])
    _set_tag_keywords(conn, "miniprogram", ["完全不相关的关键词"])

    result = f.run()
    assert result["error"] is None

    n_articles = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE source=?", (_SOURCE_NAME,)
    ).fetchone()[0]
    # 只有 xcircle 的两条命中 (AI/模型/RSI) —— wechat 半边全被挡掉
    assert n_articles == 2

    n_untagged = conn.execute(
        "SELECT COUNT(*) FROM articles a WHERE a.source = ? AND NOT EXISTS ("
        "  SELECT 1 FROM article_tags t WHERE t.article_id = a.id)",
        (_SOURCE_NAME,),
    ).fetchone()[0]
    assert n_untagged == 0


def test_blocked_item_can_enter_after_keyword_change(fresh_db, monkeypatch):
    """被关键词挡掉的条目不写进已导入 state → 改完关键词下一轮能进。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    data_dir = fresh_db
    clone = _make_clone(data_dir)
    conn = get_db()

    # 先建 fetcher (它会 seed 一次 tag), 再改关键词 ——
    # 顺序反了的话 _seed_tags 会把关键词覆盖回默认值。
    f = _seeded_fetcher(data_dir, clone_dir=clone)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)

    # 第一轮: wechat tag 的关键词跟正文无关 → 那三条被挡
    _set_tag_keywords(conn, "wechat", ["完全不相关的关键词"])
    _set_tag_keywords(conn, "miniprogram", ["完全不相关的关键词"])

    first = f.run()
    assert first["error"] is None
    assert first["inserted"] == 2        # 只剩 xcircle 两条

    state_path = data_dir / ".xbot_feed_state.json"
    assert state_path.exists()
    imported_ids = set(json.loads(state_path.read_text())["imported_ids"])
    assert "2117412872158662796" not in imported_ids   # 被挡的没进 state

    # 改关键词 (用户在 web 端 /config 改) → 第二轮被挡的条目能进来
    _set_tag_keywords(conn, "wechat", ["微信", "小程序"])
    _set_tag_keywords(conn, "miniprogram", ["小程序"])

    f2 = _seeded_fetcher(data_dir, clone_dir=clone)
    monkeypatch.setattr(FetcherXbotFeed, "_sync_clone", lambda self, cfg: clone)
    # _seeded_fetcher 又把关键词 seed 回默认值 → 重新改一次
    _set_tag_keywords(conn, "wechat", ["微信", "小程序"])
    _set_tag_keywords(conn, "miniprogram", ["小程序"])

    second = f2.run()
    assert second["error"] is None
    assert second["inserted"] == 2       # 两条 wechat 补进 (第三条本身零命中)

    n_articles = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE source=?", (_SOURCE_NAME,)
    ).fetchone()[0]
    assert n_articles == 4

    imported_ids2 = set(json.loads(state_path.read_text())["imported_ids"])
    assert "2117412872158662796" in imported_ids2


def test_topic_tags_config_is_honoured(fresh_db):
    """fetch_config.topic_tags 可改候选 tag (web 端)。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    conn = get_db()
    _seed_tags(conn)
    f = FetcherXbotFeed()
    # 用户把 xcircle 的候选 tag 改成只有 general-tech
    f._prepare({
        "blacklisted_handles": [], "skip_existing_urls": False,
        "topic_tags": {"xcircle": ["general-tech"]},
    })
    assert f._topic_candidate_tags("xcircle", f._cfg) == ["general-tech"]
    # 没配的 topic 走内置默认
    assert f._topic_candidate_tags("wechat", f._cfg) == ["wechat", "miniprogram"]


def test_unconfigured_topic_falls_back_to_same_name_active_tag(fresh_db):
    """未配置的 topic 若有同名 active tag 就用它, 否则跳过 + warning。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    conn = get_db()
    _seed_tags(conn, names=("brand_new_topic",),
               keywords={"brand_new_topic": ["全新"]})
    f = FetcherXbotFeed()
    f._prepare({"blacklisted_handles": [], "skip_existing_urls": False})
    # 同名 active tag → 用它当候选 tag
    assert f._topic_candidate_tags("brand_new_topic", f._cfg) == ["brand_new_topic"]
    art = f.parse({
        "id": "555", "url": "https://x.com/a/status/555", "author_handle": "a",
        "text": "全新内容", "topic": "brand_new_topic",
    })
    assert art is not None
    assert art["xbot_tags"] == ["brand_new_topic"]

    # 没有同名 tag → 空 → parse 返回 None (不入库)
    f2 = FetcherXbotFeed()
    f2._prepare({"blacklisted_handles": [], "skip_existing_urls": False})
    assert f2._topic_candidate_tags("no_such_topic", f2._cfg) == []
    art2 = f2.parse({
        "id": "556", "url": "https://x.com/a/status/556", "author_handle": "a",
        "text": "全新内容", "topic": "no_such_topic",
    })
    assert art2 is None


def test_cross_source_soft_dedupe_does_not_match_id_prefix(fresh_db):
    """B2 回归: status/123 不得命中 status/1234 (前缀误命中 → 漏采)。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    conn = get_db()
    conn.execute(
        "INSERT INTO articles (id, title, url, source, published_at, fetched_at) "
        "VALUES ('best2', 'other', "
        "'https://x.com/bar/status/1234', "
        "'bestblog-x', NULL, '2026-11-01')"
    )
    conn.commit()

    # id 123 与已入库的 1234 **不是**同一条 → 必须返回 False
    assert FetcherXbotFeed._is_url_exists_globally(
        "https://x.com/foo/status/123", "123",
    ) is False

    # id 1234 与已入库的 1234 是同一条 → True
    assert FetcherXbotFeed._is_url_exists_globally(
        "https://x.com/bar/status/1234", "1234",
    ) is True


def test_cross_source_soft_dedupe_matches_same_id_with_query(fresh_db):
    """B2: 带 query 的同一条 (twitter.com?s=20) 仍应命中。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    conn = get_db()
    conn.execute(
        "INSERT INTO articles (id, title, url, source, published_at, fetched_at) "
        "VALUES ('best3', 'dup2', "
        "'https://twitter.com/foo/status/123?s=20', "
        "'bestblog-x', NULL, '2026-11-01')"
    )
    conn.commit()

    assert FetcherXbotFeed._is_url_exists_globally(
        "https://x.com/foo/status/123", "123",
    ) is True


def test_dedupe_insert_has_no_mutable_default_arg():
    """B3: keyword_hits 不得用可变默认参数 (列表) —— 用 None 默认。"""
    import inspect

    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    sig = inspect.signature(FetcherXbotFeed.dedupe_insert)
    default = sig.parameters["keyword_hits"].default
    assert default is None


def test_round_ids_is_per_instance():
    """B3: _round_ids 是实例属性, 不跨实例共享。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    a = FetcherXbotFeed()
    b = FetcherXbotFeed()
    a._round_ids.append("111")
    assert b._round_ids == []
    assert a._round_ids == ["111"]


def test_source_is_registered():
    from moni.fetch.base import list_fetchers
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    assert list_fetchers()["xbot_feed"] is FetcherXbotFeed


def test_seed_defines_source_and_tags():
    from moni.db.seed_config import _DEFAULT_SOURCES, _DEFAULT_SOURCE_TAGS, _DEFAULT_TAGS

    ids = {s["id"] for s in _DEFAULT_SOURCES}
    names = {s["name"] for s in _DEFAULT_SOURCES}
    assert "src-xbot-feed" in ids
    assert "xbot_feed" in names

    src = next(s for s in _DEFAULT_SOURCES if s["name"] == "xbot_feed")
    assert src["display_name"] == "X-bot 精选"
    assert src["fetch_interval_min"] == 60
    cfg = json.loads(src["fetch_config"])
    assert cfg["bypass_keyword_filters"] is True
    assert "repo_url" in cfg
    assert "blacklisted_handles" in cfg

    bound = {tag for sid, tag in _DEFAULT_SOURCE_TAGS if sid == "src-xbot-feed"}
    assert bound == {"wechat", "miniprogram", "general-tech", "ai-fintech"}

    # 引用的 tag 必须先在 _DEFAULT_TAGS 里定义 (防空库 FK 崩)
    defined = {t[0] for t in _DEFAULT_TAGS}
    assert bound <= defined


def test_fetch_config_defaults_present():
    from moni.web.routes.config_sources import _FETCH_CONFIG_DEFAULTS

    defaults = _FETCH_CONFIG_DEFAULTS["xbot_feed"]
    assert defaults["bypass_keyword_filters"] is True
    assert defaults["repo_url"].endswith("xbot-feed.git")
    assert "blacklisted_handles" in defaults


def test_sources_yaml_has_entry():
    import yaml

    cfg_path = Path(__file__).resolve().parents[1] / "config" / "sources.yaml"
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    names = {s["source_name"] for s in data["sources"]}
    assert "xbot_feed" in names

    entry = next(s for s in data["sources"] if s["source_name"] == "xbot_feed")
    assert entry["fetch_interval_min"] == 60
    assert entry["enabled"] is True


def test_scheduler_knows_the_source():
    from moni.fetch.scheduler import Scheduler

    cfg_path = Path(__file__).resolve().parents[1] / "config" / "sources.yaml"
    sched = Scheduler(config_path=cfg_path,
                      last_run_path=Path("/tmp/moni-test-lastrun-xbot.json"))
    # enabled: true + 从没跑过 → 立刻该抓
    assert sched.should_fetch("xbot_feed") is True


# ── E. git 层 (本地 bare repo, 不联网) ─────────────────────────────────

def _make_bare_repo(tmp_path: Path) -> Path:
    """建一个本地 bare repo 当上游, 并写一份 feed JSON。"""
    work = tmp_path / "upstream-work"
    work.mkdir()
    (work / "feed").mkdir()
    for name in ("2026-11-03.json", "latest.json"):
        shutil.copy2(_FIXTURE_DIR / name, work / "feed" / name)

    subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "t@example.com"],
                   check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-qm", "feed"], check=True)

    bare = tmp_path / "upstream.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    return bare


def test_clone_branch_when_no_local_clone(fresh_db, monkeypatch):
    """没有本地 clone → 走 git clone 分支。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    data_dir = fresh_db
    bare = _make_bare_repo(fresh_db.parent)
    f = _seeded_fetcher(data_dir, repo_url=str(bare))

    clone_root = f._sync_clone(f._load_config())
    assert clone_root is not None
    assert (clone_root / ".git").is_dir()
    assert (clone_root / "feed" / "latest.json").is_file()


def test_pull_branch_when_clone_exists(fresh_db, monkeypatch):
    """已经有本地 clone → 走 git pull --ff-only 分支。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    data_dir = fresh_db
    bare = _make_bare_repo(fresh_db.parent)
    f = _seeded_fetcher(data_dir, repo_url=str(bare))

    clone_root = f._sync_clone(f._load_config())
    assert clone_root is not None

    # 上游加一个新 commit
    work = fresh_db.parent / "upstream-work"
    (work / "feed" / "2026-11-04.json").write_text(
        (work / "feed" / "2026-11-03.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(work), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-qm", "more"], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", str(bare), "main"],
                   check=True)

    clone_root = f._sync_clone(f._load_config())
    assert clone_root is not None
    assert (clone_root / "feed" / "2026-11-04.json").is_file()


def test_git_failure_is_reported_not_silent(fresh_db):
    """clone 失败 → 返回 None (并记 error), 不能静默 0 篇。"""
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    data_dir = fresh_db
    f = _seeded_fetcher(data_dir, repo_url=str(data_dir / "does-not-exist.git"))

    assert f._sync_clone(f._load_config()) is None


def test_git_failure_makes_run_report_error(fresh_db):
    from moni.fetch.sources.xbot_feed import FetcherXbotFeed

    data_dir = fresh_db
    f = _seeded_fetcher(data_dir, repo_url=str(data_dir / "does-not-exist.git"))

    result = f.run()
    assert result["error"] is not None
    assert "git clone/pull failed" in result["error"]
    assert result["inserted"] == 0
    assert result["fetched"] == 0


# ── 回归: 现有源不受影响 ───────────────────────────────────────────────

def test_other_sources_still_filtered(fresh_db):
    """bypass 只对 xbot_feed 生效, 别的源照常过滤。"""
    from moni.fetch.filters import apply_keyword_filters

    conn = get_db()
    _seed_source(conn, name="36kr_rss", source_id="src-36kr-rss")
    conn.execute(
        "INSERT INTO global_keyword_filters "
        "(mode, match_field, keywords, is_active, notes, created_at, updated_at) "
        "VALUES ('blacklist', 'title+content', '[\"博彩\"]', 1, 'test', 'now', 'now')"
    )
    conn.commit()

    articles = [
        {"source": "36kr_rss", "url": "http://e/1", "title": "含博彩的文章", "content": "x"},
        {"source": "36kr_rss", "url": "http://e/2", "title": "干净文章", "content": "x"},
    ]
    kept, stats, _ = apply_keyword_filters("36kr_rss", articles)
    assert len(kept) == 1
    assert kept[0]["url"] == "http://e/2"
    assert stats["dropped_global_blacklist"] == 1
