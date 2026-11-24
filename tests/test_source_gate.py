"""The ``sources`` table switches outrank ``config/sources.yaml`` (v1.0.123).

Failure modes pinned here:

1. A source the user switched off (``is_active = 0``) still ran in the
   scheduled batch — the UI switch was documented but never consulted.
2. A source hidden in the management page (``is_hidden = 1``) still ran.
3. A source with no row in ``sources`` is unmanaged and must keep running
   (the gate must not silently drop it).
4. A broken DB must fail open — never stop the whole sweep.
"""
from __future__ import annotations

import json

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.fetch.source_gate import (
    REASON_HIDDEN,
    REASON_INACTIVE,
    load_source_gates,
    split_fetchable,
)


@pytest.fixture
def fresh_db(tmp_path):
    set_db_path(tmp_path / "gate.db")
    migrate()
    yield
    close_db()


def _add_source(name: str, *, is_active: int = 1, is_hidden: int = 0) -> None:
    conn = get_db()
    conn.execute(
        """
        INSERT INTO sources (
            id, name, source_type, url, is_active, is_hidden, created_at, updated_at
        )
        VALUES (?, ?, 'rss', '', ?, ?, '2026-01-01T00:00:00+00:00',
                '2026-01-01T00:00:00+00:00')
        """,
        (f"src-{name}", name, is_active, is_hidden),
    )
    conn.commit()


def _cfgs(*names: str) -> list[dict]:
    return [{"source_name": name} for name in names]


def test_inactive_source_is_skipped(fresh_db):
    _add_source("off_a", is_active=0)
    fetchable, skipped = split_fetchable(_cfgs("off_a"))
    assert fetchable == []
    assert skipped == [("off_a", REASON_INACTIVE)]


def test_hidden_source_is_skipped(fresh_db):
    _add_source("hidden_a", is_hidden=1)
    fetchable, skipped = split_fetchable(_cfgs("hidden_a"))
    assert fetchable == []
    assert skipped == [("hidden_a", REASON_HIDDEN)]


def test_active_visible_source_is_kept(fresh_db):
    _add_source("good_a")
    fetchable, skipped = split_fetchable(_cfgs("good_a"))
    assert [c["source_name"] for c in fetchable] == ["good_a"]
    assert skipped == []


def test_source_without_db_row_stays_fetchable(fresh_db):
    fetchable, skipped = split_fetchable(_cfgs("unmanaged"))
    assert [c["source_name"] for c in fetchable] == ["unmanaged"]
    assert skipped == []


def test_inactive_wins_over_hidden(fresh_db):
    """Both switches off — report the data-plane reason."""
    _add_source("both_off", is_active=0, is_hidden=1)
    gates = load_source_gates(["both_off"])
    assert gates["both_off"].allowed is False
    assert gates["both_off"].reason == REASON_INACTIVE


def test_broken_db_fails_open(fresh_db, monkeypatch):
    import moni.fetch.source_gate as gate_mod

    def _boom():
        raise RuntimeError("db is gone")

    monkeypatch.setattr(gate_mod, "get_db", _boom)
    fetchable, skipped = split_fetchable(_cfgs("whatever"))
    assert [c["source_name"] for c in fetchable] == ["whatever"]
    assert skipped == []


def test_batch_sources_respects_switches(fresh_db, tmp_path, monkeypatch):
    """End-to-end through the YAML scheduler: a hidden source never reaches
    the batch list."""
    from moni.fetch.scheduler import Scheduler

    config = tmp_path / "sources.yaml"
    config.write_text(
        "sources:\n"
        "- source_name: good_a\n"
        "  enabled: true\n"
        "- source_name: hidden_a\n"
        "  enabled: true\n",
        encoding="utf-8",
    )
    _add_source("hidden_a", is_hidden=1)

    fetchable, skipped = split_fetchable(
        list(Scheduler(config_path=config).batch_sources())
    )
    assert [c["source_name"] for c in fetchable] == ["good_a"]
    assert skipped == [("hidden_a", REASON_HIDDEN)]


# ── wiring: 闸必须真的接在三条批量路径上 ──────────────────────────────────


class _FakeFetcher:
    """最小 fetcher：只够让批跑走完一轮。"""

    def __init__(self):
        self.date_from = None

    def run(self, dry_run: bool = False):
        return {"source": "fake", "fetched": 0, "parsed": 0, "inserted": 0,
                "parse_errors": 0, "error": None}


def _write_config(path, names):
    path.write_text(
        "sources:\n"
        + "".join(f"- source_name: {n}\n  enabled: true\n" for n in names),
        encoding="utf-8",
    )
    return path


def test_cron_batch_never_fetches_a_hidden_source(fresh_db, tmp_path, monkeypatch):
    """cron 批跑：隐藏源不产生 fetch_log，且跳过原因写进 cron_runs。"""
    import moni.cron.jobs as cron_jobs
    import moni.fetch.base as fetch_base
    import moni.fetch.scheduler as scheduler_mod

    config = _write_config(tmp_path / "sources.yaml", ["good_a", "hidden_a"])
    monkeypatch.setattr(scheduler_mod, "_CONFIG_PATH", config)
    _add_source("hidden_a", is_hidden=1)
    monkeypatch.setitem(fetch_base._FETCHER_REGISTRY, "good_a", _FakeFetcher)
    monkeypatch.setitem(fetch_base._FETCHER_REGISTRY, "hidden_a", _FakeFetcher)

    cron_jobs.fetch_all_sources()

    conn = get_db()
    logged = [r["source"] for r in conn.execute("SELECT source FROM fetch_log")]
    assert logged == ["good_a"]
    extra = json.loads(
        conn.execute("SELECT extra FROM cron_runs ORDER BY id DESC LIMIT 1").fetchone()["extra"]
    )
    assert extra["skipped_sources"] == {"hidden_a": REASON_HIDDEN}


def test_web_batch_reports_skipped_sources(fresh_db, tmp_path, monkeypatch):
    """Web 一键抓取：被闸挡下的源不排队，并出现在响应里。"""
    import moni.fetch.base as fetch_base
    import moni.fetch.scheduler as scheduler_mod
    from moni.web.routes import fetch as fetch_routes

    config = _write_config(tmp_path / "sources.yaml", ["good_a", "hidden_a"])
    monkeypatch.setattr(scheduler_mod, "_CONFIG_PATH", config)
    _add_source("hidden_a", is_hidden=1)
    monkeypatch.setitem(fetch_base._FETCHER_REGISTRY, "good_a", _FakeFetcher)
    monkeypatch.setitem(fetch_base._FETCHER_REGISTRY, "hidden_a", _FakeFetcher)

    class _NoThread:
        """不启动后台 worker —— 本用例只关心排队前的闸。"""

        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(fetch_routes.threading, "Thread", _NoThread)

    resp = fetch_routes.trigger_fetch_all()

    assert resp["skipped"] == [{"source": "hidden_a", "reason": REASON_HIDDEN}]
    queued = [r["source"] for r in get_db().execute("SELECT source FROM fetch_jobs")]
    assert "hidden_a" not in queued


def test_cli_batch_never_fetches_a_hidden_source(fresh_db, tmp_path, monkeypatch):
    """CLI 批量抓取：被闸挡下的源不进抓取列表。"""
    import moni.fetch.run as fetch_run
    import moni.fetch.scheduler as scheduler_mod

    config = _write_config(tmp_path / "sources.yaml", ["good_a", "hidden_a"])
    monkeypatch.setattr(scheduler_mod, "_CONFIG_PATH", config)
    _add_source("hidden_a", is_hidden=1)

    seen: list[list[str]] = []

    def _fake_collect(source_names, dry_run=False, force_targeted=False):
        seen.append(list(source_names))
        return [], 0, 0

    monkeypatch.setattr(fetch_run, "_collect_results", _fake_collect)
    fetch_run.main([])

    assert seen == [["good_a"]]


def test_fetch_all_honours_gate_and_batch_enabled(fresh_db, tmp_path, monkeypatch):
    """公共 fetch_all()：隐藏源与 batch_enabled:false 的源都不进这一轮。"""
    import moni.fetch as fetch_pkg
    import moni.fetch.base as fetch_base
    import moni.fetch.scheduler as scheduler_mod

    config = tmp_path / "sources.yaml"
    config.write_text(
        "sources:\n"
        "- source_name: good_a\n"
        "  enabled: true\n"
        "- source_name: hidden_a\n"
        "  enabled: true\n"
        "- source_name: manual_only\n"
        "  enabled: true\n"
        "  batch_enabled: false\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(scheduler_mod, "_CONFIG_PATH", config)
    # 间隔记录隔离到 tmp —— 否则仓库里的 data/.last_run.json 会让
    # "good_a" 看起来「刚跑过」，本用例就假红了。
    monkeypatch.setattr(scheduler_mod, "_LAST_RUN_PATH", tmp_path / "last_run.json")
    monkeypatch.setattr(scheduler_mod, "_INITIAL_LAST_RUN_PATH", tmp_path / "initial.json")
    _add_source("hidden_a", is_hidden=1)

    ran: list[str] = []

    class _RecordingFetcher:
        def __init__(self):
            self.date_from = None

        def run(self, dry_run: bool = False):
            ran.append(type(self).source_name)
            return {"source": type(self).source_name, "fetched": 0, "parsed": 0,
                    "inserted": 0, "parse_errors": 0, "error": None}

    for name in ("good_a", "hidden_a", "manual_only"):
        fetcher = type(f"_F_{name}", (_RecordingFetcher,), {"source_name": name})
        monkeypatch.setitem(fetch_base._FETCHER_REGISTRY, name, fetcher)

    fetch_pkg.fetch_all()

    assert "hidden_a" not in ran
    assert "manual_only" not in ran
    assert "good_a" in ran


def test_cron_batch_records_skips_when_everything_is_gated(fresh_db, tmp_path, monkeypatch):
    """全部源被闸挡下：早退路径也要留下跳过原因。"""
    import moni.cron.jobs as cron_jobs
    import moni.fetch.scheduler as scheduler_mod

    config = _write_config(tmp_path / "sources.yaml", ["hidden_a"])
    monkeypatch.setattr(scheduler_mod, "_CONFIG_PATH", config)
    _add_source("hidden_a", is_hidden=1)

    cron_jobs.fetch_all_sources()

    row = get_db().execute(
        "SELECT success, extra FROM cron_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["success"] == 1
    assert json.loads(row["extra"])["skipped_sources"] == {"hidden_a": REASON_HIDDEN}
