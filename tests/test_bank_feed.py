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


class _FakeProc:
    """走 ``subprocess.Popen`` 接口的假进程 (production 用 Popen 才能杀进程组)。"""

    pid = 9_999_999  # 不存在的 pid: 真被 killpg 也只会 ProcessLookupError

    def __init__(self, returncode: int, stdout: str, stderr: str, *,
                 timeout: bool) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._timeout = timeout
        self.timeout_kwarg: float | None = None

    def communicate(self, input=None, timeout=None):
        self.timeout_kwarg = timeout
        # 超时后调用方会 kill 进程组再 communicate() 收尾, 第二次必须返回。
        if self._timeout and timeout:
            self._timeout = False
            raise subprocess.TimeoutExpired("hermes", timeout or 1)
        return self._stdout, self._stderr


def _fake_popen(returncode: int = 0, stdout: str = "ok", stderr: str = "", *,
                timeout: bool = False, on_start=None):
    def _factory(cmd, **kwargs):
        if on_start is not None:
            on_start(cmd, kwargs)
        return _FakeProc(returncode, stdout, stderr, timeout=timeout)

    return _factory


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
    # 抓取段会拉起 agent (十分钟级), 只允许单源「立即抓取」触发,
    # 不参加 9:10 定时任务和 web「一键抓取」。
    assert entry["batch_enabled"] is False


def test_scheduler_batch_includes_and_single_source_still_works():
    from moni.fetch.scheduler import Scheduler

    scheduler = Scheduler()
    enabled = [c["source_name"] for c in scheduler.enabled_sources()]
    batch = [c["source_name"] for c in scheduler.batch_sources()]
    assert SOURCE_NAME in enabled
    assert SOURCE_NAME not in batch


def test_seed_keeps_user_edited_fetch_config(fresh_db):
    """`moni db init` 每次都会跑 seed; 用户改过的 targets 不能被默认值擦掉。"""
    from moni.db.seed_config import seed

    conn = get_db()
    mine = {"targets": [{"id": "my-target", "kind": "media_rss",
                         "url": "https://example.com/mine"}]}
    _set_fetch_config(conn, mine)

    seed()

    row = conn.execute(
        "SELECT fetch_config FROM sources WHERE name = ?", (SOURCE_NAME,)
    ).fetchone()
    assert json.loads(row["fetch_config"])["targets"] == mine["targets"]


def test_fetch_config_defaults_and_single_source():
    from moni.fetch.sources.bank_feed import DEFAULT_FETCH_CONFIG
    from moni.web.routes.config_sources import _FETCH_CONFIG_DEFAULTS

    # candidate_tags 没有任何读取方 (闸门只看绑定的 tag), 不再留在配置里
    assert "candidate_tags" not in DEFAULT_FETCH_CONFIG
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


def test_fetch_skips_broken_historical_feed_file(fresh_db, tmp_path):
    _write_feed(fresh_db, [_item(1)])
    old = (date.today() - timedelta(days=2)).isoformat()
    (_feed_dir(fresh_db) / f"{old}.json").write_text("{broken", encoding="utf-8")
    f = _fetcher()
    raws = f.fetch()
    assert [r["id"] for r in raws] == ["feed-item-1"]


def test_fetch_raises_when_today_feed_is_corrupt(fresh_db):
    """今天的 feed 坏了 = 今天的数据不可信, 不能静默当 0 篇。"""
    today = date.today().isoformat()
    _write_feed(fresh_db, [_item(1)])
    (_feed_dir(fresh_db) / f"{today}.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(RuntimeError, match="today's feed"):
        _fetcher().fetch()


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
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(2, "", "boom"))
    f = _fetcher()
    with pytest.raises(RuntimeError, match="agent bot failed"):
        f.fetch()


def test_agent_timeout_raises(fresh_db, monkeypatch):
    conn = get_db()
    _no_feed_fetch_config(conn)
    killed: list[int] = []
    import moni.fetch.sources.bank_feed as bank_feed

    monkeypatch.setattr(bank_feed, "_kill_group", lambda pid: killed.append(pid))
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(timeout=True))
    with pytest.raises(RuntimeError, match="timed out"):
        _fetcher().fetch()
    # 只杀直接子进程会留下 hermes 的浏览器孙进程, 必须整组杀
    assert killed == [_FakeProc.pid]


def test_agent_success_but_no_feed_raises(fresh_db, monkeypatch):
    conn = get_db()
    _no_feed_fetch_config(conn)
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(0))
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

    def _write(cmd, kwargs):
        out_dir = Path(cmd[cmd.index("--output-dir") + 1])
        stamp = cmd[cmd.index("--date") + 1]
        payload = json.dumps({"date": stamp, "items": [_item(1)]}, ensure_ascii=False)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{stamp}.json").write_text(payload, encoding="utf-8")
        (out_dir / "latest.json").write_text(payload, encoding="utf-8")

    monkeypatch.setattr(subprocess, "Popen", _fake_popen(0, on_start=_write))
    raws = _fetcher().fetch()
    assert [r["id"] for r in raws] == ["feed-item-1"]


def test_run_reports_error_instead_of_zero(fresh_db, monkeypatch):
    conn = get_db()
    _no_feed_fetch_config(conn)
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(1, "", "agent exploded"))
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
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(1, "", "fail"))
    _fetcher().run()
    assert not (fresh_db / ".bank_feed_state.json").exists()


def test_dry_run_writes_no_state(fresh_db):
    _write_feed(fresh_db, [_item(1)])
    _fetcher().run(dry_run=True)
    assert not (fresh_db / ".bank_feed_state.json").exists()


# ── F. 部分失败 / 完整性 ─────────────────────────────────────────────────

def _write_partial_feed(data_dir: Path, items, errors,
                        generated_at: str = "2026-11-12T08:00:00+08:00") -> None:
    today = date.today().isoformat()
    payload = {
        "date": today,
        "generated_at": generated_at,
        "counts_by_bank": {},
        "errors": errors,
        "items": items,
    }
    feed_dir = _feed_dir(data_dir)
    feed_dir.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False)
    (feed_dir / f"{today}.json").write_text(body, encoding="utf-8")
    (feed_dir / "latest.json").write_text(body, encoding="utf-8")


def test_partial_feed_imports_items_and_reports_error(fresh_db):
    """媒体成功、官网全挂: 已抓到的要入库, 但任务必须报「不完整」。"""
    conn = get_db()
    _set_fetch_config(conn, {"bot_enabled": False})
    now = datetime.now(timezone.utc).isoformat()
    _write_partial_feed(
        fresh_db,
        [_item(1)],
        [{"target": "icbc", "error": "timeout"}, {"target": "boc", "error": "403"}],
        generated_at=now,
    )
    result = _fetcher().run()
    assert result["inserted"] == 1
    assert "feed 不完整" in result["error"]
    assert "icbc" in result["error"]


def test_partial_feed_is_not_fresh_and_triggers_retry(fresh_db, monkeypatch):
    """带 errors 的当天 feed 不算完成; 过了冷却窗口再抓要重新调 agent 补齐。"""
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    _write_partial_feed(
        fresh_db, [_item(1)], [{"target": "boc", "error": "403"}],
        generated_at=stale,
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        subprocess, "Popen",
        _fake_popen(0, on_start=lambda cmd, kw: calls.append(cmd)),
    )
    _fetcher().fetch()
    assert calls, "partial feed must trigger a bot retry"


def test_state_only_records_items_that_exist(fresh_db, monkeypatch):
    """插入失败的条目不能被记成「已导入」, 否则第二天也不再重试。"""
    _write_feed(fresh_db, [_item(1), _item(2)])
    f = _fetcher()
    monkeypatch.setattr(f, "_article_exists", lambda art: art["url"].endswith("/1"))
    f.run()
    state = json.loads((fresh_db / ".bank_feed_state.json").read_text(encoding="utf-8"))
    assert state["imported_ids"] == ["feed-item-1"]


def test_pass_through_still_tags_articles(fresh_db):
    """peer-banks 被停用/清空关键词时, 闸门 pass-through 也必须落 tag。"""
    conn = get_db()
    conn.execute("UPDATE tag_definitions SET is_active = 0 WHERE name = ?", (TAG_NAME,))
    conn.commit()
    _write_feed(fresh_db, [_item(1, title="今日 A 股三大指数集体收跌",
                                 text="市场全天成交 1.9 万亿。")])
    result = _fetcher().run()
    assert result["inserted"] == 1
    tags = [
        r[0]
        for r in conn.execute("SELECT tag_name FROM article_tags")
    ]
    assert tags == [TAG_NAME]


def test_deleted_tag_refuses_to_ingest(fresh_db):
    """tag 被删 → 绑定被 CASCADE，合成命中会指向不存在的 tag。

    正确行为是拒绝入库并报错，而不是制造一批零 tag 文章。
    """
    conn = get_db()
    conn.execute("DELETE FROM tag_definitions WHERE name = ?", (TAG_NAME,))
    conn.commit()
    _write_feed(fresh_db, [_item(1)])
    result = _fetcher().run()
    assert result["inserted"] == 0
    assert "no tag bound" in result["error"]
    assert conn.execute("SELECT COUNT(*) FROM articles WHERE source = ?",
                        (SOURCE_NAME,)).fetchone()[0] == 0


def test_pass_through_with_source_filter_still_tags(fresh_db):
    """关键词清空 + 源白名单同时存在时，filter 命中不带 tag_name。

    只看「整轮 keyword_hits 是否为空」会漏掉这条路径 → 零 tag。
    """
    conn = get_db()
    conn.execute("UPDATE tag_definitions SET keywords = NULL WHERE name = ?",
                 (TAG_NAME,))
    conn.execute(
        """
        INSERT INTO source_keyword_filters
            (source_id, mode, match_field, keywords, is_active, created_at, updated_at)
        VALUES (?, 'whitelist', 'title+content', ?, 1, '2026-11-01', '2026-11-01')
        """,
        (SOURCE_ID, json.dumps(["建行"], ensure_ascii=False)),
    )
    conn.commit()
    _write_feed(fresh_db, [_item(1, title="建行发布2026年三季度经营业绩")])
    result = _fetcher().run()
    assert result["inserted"] == 1
    tags = [r[0] for r in conn.execute("SELECT tag_name FROM article_tags")]
    assert tags == [TAG_NAME]


def test_bot_retry_failure_still_imports_existing_partial_feed(fresh_db, monkeypatch):
    """补跑失败不该把盘上已有的部分 feed 一起丢掉。"""
    conn = get_db()
    _no_feed_fetch_config(conn)
    stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    _write_partial_feed(
        fresh_db, [_item(1)], [{"target": "icbc", "error": "timeout"}],
        generated_at=stale,
    )
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(1, "", "agent exploded"))
    result = _fetcher().run()
    assert result["inserted"] == 1
    assert "agent 补跑失败" in result["error"]
    assert "feed 不完整" in result["error"]


def test_insert_failure_is_not_a_green_success(fresh_db):
    """dedupe_insert 抛错时 result["error"] 必须有值 (四条入口都靠它)。"""
    import sqlite3

    from moni.fetch.base import BaseFetcher

    class _BoomFetcher(BaseFetcher):
        source_name = "boom"

        def fetch(self):
            return [{"url": "https://example.com/boom", "title": "建设银行新闻"}]

        def parse(self, raw):
            return {
                "title": raw["title"],
                "url": raw["url"],
                "content": "建设银行正文",
            }

        def dedupe_insert(self, articles, keyword_hits=None):
            raise sqlite3.OperationalError("disk I/O error")

    result = _BoomFetcher().run()
    assert result["inserted"] == 0
    assert result["error"] and "disk I/O error" in result["error"]


def test_binding_deleted_between_fetch_and_insert_is_refused(fresh_db):
    """fetch() 之后绑定被删的窗口: 入库必须拒绝, 不能产出零 tag 文章。"""
    _write_feed(fresh_db, [_item(1)])
    f = _fetcher()
    f._cfg = {}
    art = f.parse(_item(1))
    assert art is not None

    conn = get_db()
    conn.execute("DELETE FROM tag_definitions WHERE name = ?", (TAG_NAME,))
    conn.commit()
    with pytest.raises(RuntimeError, match="tag binding disappeared"):
        f.dedupe_insert([art], [])
    assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 0
