"""tests/test_bank_feed.py — bank_feed 源 (v1.0.76 / sprint-40) 离线测试。

覆盖:
  A. 配置层   — tag/source/binding 的 seed 与 migration 一致、fetch_config 单源、
                sources.yaml、_FETCH_CONFIG_DEFAULTS
  B. 解析层   — parse() 字段映射、缺 title/url 的跳过、发布时间归一
  C. 抓取层   — 读本地 feed、id 去重、max_days、坏文件跳过、空目录报错
  D. agent 层 — bot 失败/超时/无产物必须抛错 (绝不能静默 0 篇)
  E. 去重/状态 — 重复导入幂等、被闸门挡掉的条目**不写**已导入状态

不联网、不烧 LLM: subprocess 调用一律 monkeypatch 成假实现。
"""
from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate

SOURCE_NAME = "bank_feed"
SOURCE_ID = "src-bank-feed"
TAG_NAME = "peer-banks"


# ── fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """隔离的 tmp sqlite + tmp MONI_DATA_DIR (migration 已跑 => 源/tag 都在)。"""
    close_db()
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MONI_DATA_DIR", str(data_dir))
    set_db_path(data_dir / "news.db")
    migrate()
    yield data_dir
    close_db()


def _feed_dir(data_dir: Path) -> Path:
    return data_dir / "bank_feed"


def _write_feed(data_dir: Path, items, stamp: str | None = None,
                *, latest: bool = True) -> Path:
    stamp = stamp or date.today().isoformat()
    feed_dir = _feed_dir(data_dir)
    feed_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": date.today().isoformat(),
        "generated_at": "2026-11-12T08:00:00+08:00",
        "counts_by_bank": {},
        "items": items,
    }
    body = json.dumps(payload, ensure_ascii=False)
    path = feed_dir / f"{stamp}.json"
    path.write_text(body, encoding="utf-8")
    if latest:
        (feed_dir / "latest.json").write_text(body, encoding="utf-8")
    return path


def _item(idx: int = 1, *, title: str = "建设银行发布2026年三季度经营业绩",
          bank: str = "ccb", text: str | None = None) -> dict:
    return {
        "id": f"feed-item-{idx}",
        "url": f"https://example.com/bank/{idx}",
        "title": title,
        "text": text if text is not None else (
            "建设银行今日发布季度经营业绩，营业收入与净利润均实现同比增长，"
            "资产质量保持稳定，拨备覆盖率维持较高水平。"
        ),
        "created_at": "2026-11-11T10:00:00+08:00",
        "bank": bank,
        "source_type": "official",
        "publisher": "中国建设银行",
    }


def _fetcher():
    from moni.fetch.sources.bank_feed import FetcherBankFeed

    return FetcherBankFeed()


def _set_fetch_config(conn, cfg: dict) -> None:
    conn.execute(
        "UPDATE sources SET fetch_config = ? WHERE name = ?",
        (json.dumps(cfg, ensure_ascii=False), SOURCE_NAME),
    )
    conn.commit()


# ── A. 配置层 ───────────────────────────────────────────────────────────

def test_source_is_registered():
    from moni.fetch.base import list_fetchers

    assert SOURCE_NAME in list_fetchers()


def test_seed_defines_tag_source_and_binding(fresh_db):
    from moni.db.seed_config import (
        _DEFAULT_SOURCE_TAGS,
        _DEFAULT_SOURCES,
        _DEFAULT_TAGS,
        PEER_BANKS_KEYWORDS,
        seed,
    )

    seed()
    conn = get_db()
    tag = conn.execute(
        "SELECT name, display_name, keywords, is_active FROM tag_definitions "
        "WHERE name = ?",
        (TAG_NAME,),
    ).fetchone()
    assert tag is not None
    assert tag["display_name"] == "同业"
    assert tag["is_active"] == 1
    assert json.loads(tag["keywords"]) == PEER_BANKS_KEYWORDS

    src = conn.execute(
        "SELECT id, name, display_name, is_active FROM sources WHERE id = ?",
        (SOURCE_ID,),
    ).fetchone()
    assert src is not None
    assert src["name"] == SOURCE_NAME
    assert src["is_active"] == 1

    links = [
        r[0]
        for r in conn.execute(
            "SELECT tag_name FROM source_tags WHERE source_id = ?", (SOURCE_ID,)
        )
    ]
    assert links == [TAG_NAME]

    assert ("src-bank-feed", "peer-banks") in _DEFAULT_SOURCE_TAGS
    assert any(s["id"] == SOURCE_ID for s in _DEFAULT_SOURCES)
    assert any(t[0] == TAG_NAME for t in _DEFAULT_TAGS)


def test_seed_never_overwrites_user_keywords(fresh_db):
    from moni.db.seed_config import seed

    conn = get_db()
    conn.execute(
        "UPDATE tag_definitions SET keywords = ? WHERE name = ?",
        (json.dumps(["我自己的词"], ensure_ascii=False), TAG_NAME),
    )
    conn.commit()
    seed()
    row = get_db().execute(
        "SELECT keywords FROM tag_definitions WHERE name = ?", (TAG_NAME,)
    ).fetchone()
    assert json.loads(row["keywords"]) == ["我自己的词"]


def test_sources_yaml_has_entry():
    import yaml

    cfg = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "config" / "sources.yaml")
        .read_text(encoding="utf-8")
    )
    entry = next(
        (s for s in cfg["sources"] if s["source_name"] == SOURCE_NAME), None
    )
    assert entry is not None
    assert entry["enabled"] is True
    assert entry["crawler_type"] == "json"


def test_fetch_config_defaults_and_single_source():
    from moni.fetch.sources.bank_feed import DEFAULT_FETCH_CONFIG
    from moni.web.routes.config_sources import _FETCH_CONFIG_DEFAULTS

    assert DEFAULT_FETCH_CONFIG["candidate_tags"] == [TAG_NAME]
    assert DEFAULT_FETCH_CONFIG["bot_script"] == "scripts/bank_bot/run_bot.py"
    assert DEFAULT_FETCH_CONFIG["targets_file"] == "config/bank_targets.json"
    # job 级 timeout 必须比 bot 自己的超时大, 否则 bot 还在跑任务就被判 timeout
    assert DEFAULT_FETCH_CONFIG["timeout_sec"] > DEFAULT_FETCH_CONFIG["bot_timeout_sec"]
    # 三处抄本会漂移: web 端的默认值必须逐字等于 fetcher 的默认值
    assert _FETCH_CONFIG_DEFAULTS[SOURCE_NAME] == DEFAULT_FETCH_CONFIG


def test_default_targets_file_is_valid():
    from moni.fetch.sources.bank_feed import DEFAULT_TARGETS_FILE

    repo = Path(__file__).resolve().parents[1]
    payload = json.loads((repo / DEFAULT_TARGETS_FILE).read_text(encoding="utf-8"))
    targets = payload["targets"]
    assert len(targets) == 11
    kinds = {t["kind"] for t in targets}
    assert kinds == {"official_list", "media_rss", "media_json"}
    official_banks = {t["bank"] for t in targets if t["kind"] == "official_list"}
    assert official_banks == {"boc", "abc", "icbc", "ccb", "psbc"}
    for target in targets:
        assert target["id"] and target["url"] and target["kind"]
        assert target["url"].startswith("http")


# ── B. 解析层 ───────────────────────────────────────────────────────────

def test_parse_maps_fields(fresh_db):
    f = _fetcher()
    f._cfg = {}
    art = f.parse(_item(1))
    assert art is not None
    assert art["source"] == SOURCE_NAME
    assert art["url"] == "https://example.com/bank/1"
    assert art["title"] == "建设银行发布2026年三季度经营业绩"
    assert art["publisher"] == "中国建设银行"
    assert art["published_at"] == "2026-11-11T02:00:00+00:00"
    assert art["bank"] == "ccb"
    assert art["source_type"] == "official"
    assert art["bank_item_id"] == "feed-item-1"
    assert "bank:ccb" in art["content"]


def test_parse_skips_when_title_and_url_missing(fresh_db):
    f = _fetcher()
    f._cfg = {}
    assert f.parse({"text": "只有正文"}) is None


def test_parse_falls_back_to_first_line(fresh_db):
    f = _fetcher()
    f._cfg = {}
    art = f.parse({
        "id": "x1",
        "url": "https://example.com/x1",
        "text": "邮储银行发布新战略\n正文在这里",
        "bank": "psbc",
    })
    assert art is not None
    assert art["title"] == "邮储银行发布新战略"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-11-11T10:00:00+08:00", "2026-11-11T02:00:00+00:00"),
        ("2026-11-11", "2026-11-10T16:00:00+00:00"),
        ("2026-11-11 10:00:00", "2026-11-11T02:00:00+00:00"),
        ("", None),
        (None, None),
        ("not-a-date", None),
    ],
)
def test_normalize_published_at(raw, expected):
    from moni.fetch.sources.bank_feed import _normalize_published_at

    assert _normalize_published_at(raw) == expected


# ── C. 抓取层 ───────────────────────────────────────────────────────────

def test_fetch_reads_feed_and_dedupes_by_id(fresh_db):
    _write_feed(fresh_db, [_item(1), _item(2)])
    f = _fetcher()
    raws = f.fetch()
    assert [r["id"] for r in raws] == ["feed-item-1", "feed-item-2"]


def test_fetch_empty_feed_dir_raises(fresh_db):
    conn = get_db()
    _set_fetch_config(conn, {"bot_enabled": False})
    f = _fetcher()
    with pytest.raises(RuntimeError, match="no feed for today"):
        f.fetch()


def test_fetch_skips_broken_feed_file(fresh_db, tmp_path):
    _write_feed(fresh_db, [_item(1)])
    ( _feed_dir(fresh_db) / "2026-11-10.json").write_text("{broken", encoding="utf-8")
    f = _fetcher()
    raws = f.fetch()
    assert [r["id"] for r in raws] == ["feed-item-1"]


def test_fetch_max_days_limits_dated_files(fresh_db):
    _write_feed(fresh_db, [_item(1)])
    old = date.today() - timedelta(days=90)
    _write_feed(fresh_db, [_item(2)], stamp=old.isoformat(), latest=False)
    conn = get_db()
    _set_fetch_config(conn, {"max_days": 30})
    raws = _fetcher().fetch()
    assert [r["id"] for r in raws] == ["feed-item-1"]


def test_fetch_ignores_underscore_and_latest(fresh_db):
    _write_feed(fresh_db, [_item(1)])
    (_feed_dir(fresh_db) / "_rss_candidates.json").write_text(
        json.dumps({"items": [_item(9)]}), encoding="utf-8"
    )
    raws = _fetcher().fetch()
    assert [r["id"] for r in raws] == ["feed-item-1"]


# ── D. agent 层: 失败必须报错, 绝不静默 0 篇 ─────────────────────────────

def _no_feed_fetch_config(conn) -> None:
    _set_fetch_config(conn, {"targets": [{"id": "t1", "kind": "media_rss",
                                          "url": "https://example.com/feed"}]})


def test_agent_nonzero_exit_raises(fresh_db, monkeypatch):
    conn = get_db()
    _no_feed_fetch_config(conn)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 2, "", "boom"),
    )
    f = _fetcher()
    with pytest.raises(RuntimeError, match="agent bot failed"):
        f.fetch()


def test_agent_timeout_raises(fresh_db, monkeypatch):
    conn = get_db()
    _no_feed_fetch_config(conn)

    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1)

    monkeypatch.setattr(subprocess, "run", _timeout)
    with pytest.raises(RuntimeError, match="timed out"):
        _fetcher().fetch()


def test_agent_success_but_no_feed_raises(fresh_db, monkeypatch):
    conn = get_db()
    _no_feed_fetch_config(conn)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "ok", ""),
    )
    with pytest.raises(RuntimeError, match="no feed for today"):
        _fetcher().fetch()


def test_agent_missing_script_raises(fresh_db):
    conn = get_db()
    _set_fetch_config(conn, {"bot_script": "scripts/bank_bot/does_not_exist.py"})
    with pytest.raises(RuntimeError, match="bot script not found"):
        _fetcher().fetch()


def test_agent_disabled_raises_when_feed_missing(fresh_db):
    conn = get_db()
    _set_fetch_config(conn, {"bot_enabled": False})
    with pytest.raises(RuntimeError, match="bot_enabled is off"):
        _fetcher().fetch()


def test_no_targets_raises(fresh_db):
    conn = get_db()
    _set_fetch_config(conn, {"targets": [], "targets_file": "config/does-not-exist.json"})
    with pytest.raises(RuntimeError, match="no targets configured"):
        _fetcher().fetch()


def test_agent_writes_feed_and_fetch_picks_it_up(fresh_db, monkeypatch):
    """跑通「今天没 feed → 调 bot → bot 落盘 → 读回来」这条链。"""
    conn = get_db()
    _no_feed_fetch_config(conn)

    def _fake_run(cmd, **kwargs):
        out_dir = Path(cmd[cmd.index("--output-dir") + 1])
        stamp = cmd[cmd.index("--date") + 1]
        payload = json.dumps({"date": stamp, "items": [_item(1)]}, ensure_ascii=False)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{stamp}.json").write_text(payload, encoding="utf-8")
        (out_dir / "latest.json").write_text(payload, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    raws = _fetcher().fetch()
    assert [r["id"] for r in raws] == ["feed-item-1"]


def test_run_reports_error_instead_of_zero(fresh_db, monkeypatch):
    conn = get_db()
    _no_feed_fetch_config(conn)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 1, "", "agent exploded"),
    )
    result = _fetcher().run()
    assert result["inserted"] == 0
    assert result["error"] and "agent exploded" in result["error"]


# ── E. 去重 / 状态 / 闸门 ───────────────────────────────────────────────

def test_run_inserts_and_tags(fresh_db):
    _write_feed(fresh_db, [_item(1)])
    result = _fetcher().run()
    assert result["inserted"] == 1
    conn = get_db()
    row = conn.execute(
        "SELECT id, title, source FROM articles WHERE source = ?", (SOURCE_NAME,)
    ).fetchone()
    assert row is not None
    tags = [
        r[0]
        for r in conn.execute(
            "SELECT tag_name FROM article_tags WHERE article_id = ?", (row["id"],)
        )
    ]
    assert tags == [TAG_NAME]


def test_repeated_run_is_idempotent(fresh_db):
    _write_feed(fresh_db, [_item(1)])
    first = _fetcher().run()
    second = _fetcher().run()
    assert first["inserted"] == 1
    assert second["fetched"] == 0
    assert second["inserted"] == 0


def test_item_without_bank_name_is_not_ingested(fresh_db):
    _write_feed(fresh_db, [_item(1, title="今日 A 股三大指数集体收跌",
                                 text="市场全天成交 1.9 万亿。")])
    result = _fetcher().run()
    assert result["inserted"] == 0
    assert result["dropped_tag_filter"] == 1


def test_blocked_item_can_enter_after_keyword_change(fresh_db):
    _write_feed(fresh_db, [_item(1, title="某行发布新战略", text="正文若干。")])
    conn = get_db()
    first = _fetcher().run()
    assert first["inserted"] == 0

    # 被闸门挡掉的条目不能记成「已导入」—— 文件压根不该出现
    assert not (fresh_db / ".bank_feed_state.json").exists()

    conn.execute(
        "UPDATE tag_definitions SET keywords = ? WHERE name = ?",
        (json.dumps(["某行"], ensure_ascii=False), TAG_NAME),
    )
    conn.commit()
    second = _fetcher().run()
    assert second["inserted"] == 1


def test_cross_source_soft_dedupe(fresh_db):
    conn = get_db()
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at, raw_content) "
        "VALUES ('existing', '已存在', 'https://example.com/bank/1', 'mpaypass', "
        "'2026-11-01', '正文')"
    )
    conn.commit()
    _write_feed(fresh_db, [_item(1)])
    result = _fetcher().run()
    assert result["inserted"] == 0


def test_state_not_written_when_fetch_fails(fresh_db, monkeypatch):
    conn = get_db()
    _no_feed_fetch_config(conn)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 1, "", "fail"),
    )
    _fetcher().run()
    assert not (fresh_db / ".bank_feed_state.json").exists()


def test_dry_run_writes_no_state(fresh_db):
    _write_feed(fresh_db, [_item(1)])
    _fetcher().run(dry_run=True)
    assert not (fresh_db / ".bank_feed_state.json").exists()
