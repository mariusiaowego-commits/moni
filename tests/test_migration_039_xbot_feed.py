"""Regression tests for migration 039 (xbot_feed source lands in old databases).

v1.0.66 (PR #172) added the ``xbot_feed`` source but only to
``seed_config.py`` and ``config/sources.yaml`` -- there was no migration,
so an existing database that never ran ``moni db seed-config`` had no
such row in ``sources``.  Two things then break silently (see
``docs/plans/2026-11-05_sprint-30-xbot-feed-backfill-and-migration.md``
section 1.0): ``/config`` does not show the source, and
``_is_source_bypassed("xbot_feed")`` returns False because it only reads
the DB -- the global blacklist then kills curated content without any
error being raised.

039 makes the upgrade independent of ``seed-config``: it only INSERTs,
never UPDATEs a row the user owns.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import _CURRENT_VERSION, _run_sql_file, migrate
from moni.db.seed_config import _DEFAULT_SOURCE_TAGS, _DEFAULT_SOURCES, seed

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_039 = (
    REPO_ROOT / "moni" / "db" / "migrations" / "039_xbot_feed_source.sql"
)

SOURCE_ID = "src-xbot-feed"
SOURCE_NAME = "xbot_feed"
EXPECTED_TAGS = {"wechat", "miniprogram", "general-tech", "ai-fintech"}

#: The commit whose code predates the xbot_feed source.  A database
#: built with that code is what "an existing database" looks like.
OLD_COMMIT = "015a2c5"


def _seed_source_row() -> dict:
    for src in _DEFAULT_SOURCES:
        if src["id"] == SOURCE_ID:
            return src
    raise AssertionError("seed_config no longer defines src-xbot-feed")


def _seed_tag_names() -> list[str]:
    return [tag for sid, tag in _DEFAULT_SOURCE_TAGS if sid == SOURCE_ID]


@pytest.fixture
def seeded_db(tmp_path):
    """A migrated + seeded database (the 'already upgraded by seed' state)."""
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    seed()
    conn = get_db()
    yield conn
    close_db()


@pytest.fixture
def unseeded_db(tmp_path):
    """A migrated but NOT seeded database -- the real upgrade scenario."""
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    conn = get_db()
    yield conn
    close_db()


def _row(conn, source_id: str = SOURCE_ID) -> dict | None:
    row = conn.execute(
        "SELECT id, name, display_name, source_type, url, fetch_interval_min, "
        "is_active, notes, fetch_config FROM sources WHERE id = ?",
        (source_id,),
    ).fetchone()
    return dict(row) if row else None


def _links(conn, source_id: str = SOURCE_ID) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT tag_name FROM source_tags WHERE source_id = ? ORDER BY tag_name",
            (source_id,),
        )
    ]


# ── 1. version constant ────────────────────────────────────────────────


def test_039_version_constant_is_39():
    assert _CURRENT_VERSION == 39


# ── 2. the source lands on an unseeded (v38-era) database ──────────────


def test_039_creates_source_on_unseeded_db(unseeded_db):
    """The core upgrade path: an old database never ran seed-config."""
    _run_sql_file(unseeded_db, MIGRATION_039)
    row = _row(unseeded_db)
    assert row is not None
    src = _seed_source_row()
    assert row["name"] == SOURCE_NAME
    assert row["display_name"] == src["display_name"]
    assert row["source_type"] == src["source_type"]
    assert row["url"] == src["url"]
    assert row["fetch_interval_min"] == src["fetch_interval_min"]
    assert row["is_active"] == 1
    assert row["notes"] == src["notes"]


def test_039_creates_source_on_v38_db(seeded_db):
    """Same source, but on a database that already had seed run."""
    seeded_db.execute("DELETE FROM sources WHERE id = ?", (SOURCE_ID,))
    seeded_db.commit()
    _run_sql_file(seeded_db, MIGRATION_039)
    row = _row(seeded_db)
    assert row is not None
    assert row["name"] == SOURCE_NAME


# ── 3. the four tag links ──────────────────────────────────────────────


def test_039_creates_the_four_tag_links(seeded_db):
    seeded_db.execute("DELETE FROM source_tags WHERE source_id = ?", (SOURCE_ID,))
    seeded_db.commit()
    _run_sql_file(seeded_db, MIGRATION_039)
    assert set(_links(seeded_db)) == EXPECTED_TAGS


def test_039_source_tags_match_seed_config(seeded_db):
    """The four links must be exactly the ones seed_config declares."""
    seeded_db.execute("DELETE FROM source_tags WHERE source_id = ?", (SOURCE_ID,))
    seeded_db.commit()
    _run_sql_file(seeded_db, MIGRATION_039)
    assert set(_links(seeded_db)) == set(_seed_tag_names())


# ── 4. idempotency ─────────────────────────────────────────────────────


def test_039_is_idempotent(seeded_db):
    _run_sql_file(seeded_db, MIGRATION_039)
    first_row = _row(seeded_db)
    first_links = _links(seeded_db)
    _run_sql_file(seeded_db, MIGRATION_039)
    assert _row(seeded_db) == first_row
    assert _links(seeded_db) == first_links
    count = seeded_db.execute(
        "SELECT COUNT(*) FROM sources WHERE id = ?", (SOURCE_ID,)
    ).fetchone()[0]
    assert count == 1
    link_count = seeded_db.execute(
        "SELECT COUNT(*) FROM source_tags WHERE source_id = ?", (SOURCE_ID,)
    ).fetchone()[0]
    assert link_count == len(EXPECTED_TAGS)


def test_039_does_not_change_created_at_on_rerun(seeded_db):
    _run_sql_file(seeded_db, MIGRATION_039)
    created = seeded_db.execute(
        "SELECT created_at FROM sources WHERE id = ?", (SOURCE_ID,)
    ).fetchone()[0]
    _run_sql_file(seeded_db, MIGRATION_039)
    again = seeded_db.execute(
        "SELECT created_at FROM sources WHERE id = ?", (SOURCE_ID,)
    ).fetchone()[0]
    assert again == created


# ── 5. the user's edits survive ────────────────────────────────────────


def test_039_does_not_touch_edited_source(seeded_db):
    """A deactivated, renamed, re-configured source keeps every field."""
    seeded_db.execute(
        "UPDATE sources SET display_name = 'MY-XBOT', is_active = 0, "
        "fetch_interval_min = 15, notes = '我的备注', "
        "url = 'https://example.com/x', fetch_config = '{\"max_days\": 3}' "
        "WHERE id = ?",
        (SOURCE_ID,),
    )
    seeded_db.commit()
    before = _row(seeded_db)
    _run_sql_file(seeded_db, MIGRATION_039)
    assert _row(seeded_db) == before


def test_039_does_not_reenable_disabled_source(seeded_db):
    """Spelled out separately: the gate must not re-enable a stopped source."""
    seeded_db.execute(
        "UPDATE sources SET is_active = 0 WHERE id = ?", (SOURCE_ID,)
    )
    seeded_db.commit()
    _run_sql_file(seeded_db, MIGRATION_039)
    assert _row(seeded_db)["is_active"] == 0


def test_039_never_updates_existing_row(seeded_db):
    """Byte-level guard: nothing but the source row count may change."""
    before = seeded_db.execute(
        "SELECT * FROM sources WHERE id = ?", (SOURCE_ID,)
    ).fetchone()
    _run_sql_file(seeded_db, MIGRATION_039)
    after = seeded_db.execute(
        "SELECT * FROM sources WHERE id = ?", (SOURCE_ID,)
    ).fetchone()
    assert tuple(before) == tuple(after)


def test_039_does_not_touch_tag_keywords(seeded_db):
    """Keywords are user data -- the migration must not touch them."""
    raw = '["我的词", "另一个"]'
    seeded_db.execute(
        "UPDATE tag_definitions SET keywords = ? WHERE name = 'wechat'", (raw,)
    )
    seeded_db.execute("UPDATE tag_definitions SET keywords = NULL WHERE name = 'ai-fintech'")
    seeded_db.commit()
    _run_sql_file(seeded_db, MIGRATION_039)
    got = seeded_db.execute(
        "SELECT keywords FROM tag_definitions WHERE name = 'wechat'"
    ).fetchone()[0]
    assert got == raw
    nulled = seeded_db.execute(
        "SELECT keywords FROM tag_definitions WHERE name = 'ai-fintech'"
    ).fetchone()[0]
    assert nulled is None


def test_039_does_not_touch_other_sources(seeded_db):
    """No other source row may change."""
    before = seeded_db.execute(
        "SELECT id, display_name, url, fetch_interval_min, is_active, notes, "
        "fetch_config FROM sources WHERE id != ? ORDER BY id",
        (SOURCE_ID,),
    ).fetchall()
    _run_sql_file(seeded_db, MIGRATION_039)
    after = seeded_db.execute(
        "SELECT id, display_name, url, fetch_interval_min, is_active, notes, "
        "fetch_config FROM sources WHERE id != ? ORDER BY id",
        (SOURCE_ID,),
    ).fetchall()
    assert [tuple(r) for r in before] == [tuple(r) for r in after]


# ── 6. missing tags ────────────────────────────────────────────────────


def test_039_skips_links_when_tags_missing(unseeded_db):
    """An unseeded database has no tag_definitions rows at all."""
    assert unseeded_db.execute(
        "SELECT COUNT(*) FROM tag_definitions"
    ).fetchone()[0] == 0
    _run_sql_file(unseeded_db, MIGRATION_039)  # must not raise
    assert _row(unseeded_db) is not None
    assert _links(unseeded_db) == []


def test_039_no_dangling_links(seeded_db):
    """Every link must point at a tag that really exists."""
    _run_sql_file(seeded_db, MIGRATION_039)
    dangling = seeded_db.execute(
        "SELECT st.tag_name FROM source_tags st "
        "LEFT JOIN tag_definitions t ON t.name = st.tag_name "
        "WHERE st.source_id = ? AND t.name IS NULL",
        (SOURCE_ID,),
    ).fetchall()
    assert dangling == []


def test_039_guard_requires_effective_bindings(seeded_db):
    """The cleanup log must not claim success without four live bindings.

    This mirrors migration 037: the tag gate joins tag_definitions with
    is_active = 1, so four *inactive* tags leave the gate open while the
    log would claim success.
    """
    seeded_db.execute("UPDATE tag_definitions SET is_active = 0")
    seeded_db.commit()
    _run_sql_file(seeded_db, MIGRATION_039)
    row = seeded_db.execute(
        "SELECT COUNT(*) FROM schema_cleanup_log WHERE migration = ?",
        ("039_xbot_feed_source",),
    ).fetchone()[0]
    assert row == 0


def test_039_logs_the_cleanup(seeded_db):
    _run_sql_file(seeded_db, MIGRATION_039)
    row = seeded_db.execute(
        "SELECT migration FROM schema_cleanup_log WHERE migration = ?",
        ("039_xbot_feed_source",),
    ).fetchone()
    assert row is not None


# ── 7. value drift guards ──────────────────────────────────────────────


def test_039_matches_seed_config_values(seeded_db):
    """The migration row must equal the seed_config row field by field."""
    src = _seed_source_row()
    seeded_db.execute("DELETE FROM sources WHERE id = ?", (SOURCE_ID,))
    seeded_db.commit()
    _run_sql_file(seeded_db, MIGRATION_039)
    row = _row(seeded_db)
    assert row["name"] == src["name"]
    assert row["display_name"] == src["display_name"]
    assert row["source_type"] == src["source_type"]
    assert row["url"] == src["url"]
    assert row["fetch_interval_min"] == src["fetch_interval_min"]
    assert row["notes"] == src["notes"]
    assert row["fetch_config"] == src["fetch_config"]


def test_039_fetch_config_matches_default(seeded_db):
    """The SQL is the fourth copy of DEFAULT_FETCH_CONFIG -- this is the guard."""
    from moni.fetch.sources.xbot_feed import DEFAULT_FETCH_CONFIG

    seeded_db.execute("DELETE FROM sources WHERE id = ?", (SOURCE_ID,))
    seeded_db.commit()
    _run_sql_file(seeded_db, MIGRATION_039)
    assert _row(seeded_db)["fetch_config"] == json.dumps(
        DEFAULT_FETCH_CONFIG, ensure_ascii=False
    )


# ── 8. the SQL literal constraint (B-2 guard) ──────────────────────────


def _string_literals(sql: str) -> list[str]:
    """Extract the real string literals from a .sql file.

    A naive regex cannot do this: an apostrophe inside a ``--`` comment
    (``the user's keywords``) opens a phantom literal that swallows the
    rest of the file.  This scanner understands single-quoted strings
    (with the ``''`` escape) and skips line comments.
    """
    literals: list[str] = []
    buf: list[str] = []
    in_string = False
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if in_string:
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                in_string = False
                literals.append("".join(buf))
                buf = []
                i += 1
                continue
            buf.append(ch)
            i += 1
            continue
        if ch == "'":
            in_string = True
            buf = []
            i += 1
            continue
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            # line comment -- skip to end of line
            j = sql.find("\n", i)
            i = n if j == -1 else j
            continue
        i += 1
    return literals


def test_039_sql_literals_have_no_semicolon():
    """migrate.py's fallback path is a naive split(';') -- see section 5.4.1."""
    sql = MIGRATION_039.read_text(encoding="utf-8")
    for literal in _string_literals(sql):
        assert ";" not in literal, literal
        assert "--" not in literal, literal
        assert "/*" not in literal, literal


def test_039_survives_runner_fallback(seeded_db):
    """Force _run_sql_file down the per-statement fallback path."""
    class _BrokenExecutescript:
        """Proxy that refuses executescript, forcing the fallback."""

        def __init__(self, conn):
            self._conn = conn

        def executescript(self, sql):  # noqa: ANN001
            raise sqlite3.OperationalError("forced fallback for the test")

        def execute(self, sql):  # noqa: ANN001
            return self._conn.execute(sql)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    _run_sql_file(_BrokenExecutescript(seeded_db), MIGRATION_039)
    assert _row(seeded_db) is not None
    assert set(_links(seeded_db)) == EXPECTED_TAGS


# ── 9. the runner path through migrate() ────────────────────────────────


def test_039_fresh_db_via_migrate(tmp_path):
    """A brand new database reaches schema 39 without raising."""
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    conn = get_db()
    version = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_version"
    ).fetchone()[0]
    assert version == 39
    assert _row(conn) is not None
    close_db()


def test_039_on_v38_db_without_seed(tmp_path):
    """The real upgrade: v38 state, no seed, then migrate()."""
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    conn = get_db()
    # Pretend the database was still on v38 and had no seed at all.
    conn.execute("DELETE FROM schema_version WHERE version >= 39")
    conn.execute("DELETE FROM sources")
    conn.execute("DELETE FROM source_tags")
    conn.execute("DELETE FROM tag_definitions")
    conn.commit()
    migrate()
    conn = get_db()
    version = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_version"
    ).fetchone()[0]
    assert version == 39
    assert _row(conn) is not None
    close_db()


def test_039_restores_bypass_semantics(seeded_db):
    """The silent failure mode: no source row -> bypass is off -> blacklist kills."""
    from moni.fetch.filters import _is_source_bypassed

    seeded_db.execute("DELETE FROM sources WHERE id = ?", (SOURCE_ID,))
    seeded_db.commit()
    assert _is_source_bypassed(SOURCE_NAME) is False

    _run_sql_file(seeded_db, MIGRATION_039)
    assert _is_source_bypassed(SOURCE_NAME) is True


# ── 10. the name UNIQUE clash (S-3) ───────────────────────────────────


def test_039_tolerates_user_owned_same_name_source(seeded_db):
    """The user deleted the row and recreated it under their own id.

    ``sources.name`` is UNIQUE.  Inserting the migration row anyway
    aborts the whole migration, which pins schema_version at 38 and
    breaks every ``moni db *`` subcommand.  The migration must treat
    "the name is already taken" as satisfied instead.
    """
    seeded_db.execute("DELETE FROM sources WHERE id = ?", (SOURCE_ID,))
    seeded_db.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, notes, created_at, updated_at) "
        "VALUES ('src-my-own-xbot', 'xbot_feed', '我的 xbot', 'json', "
        "'https://example.com/x', 30, 1, 'mine', '2026-11-01', '2026-11-01')"
    )
    seeded_db.commit()

    _run_sql_file(seeded_db, MIGRATION_039)  # must not raise

    assert _row(seeded_db) is None
    assert _row(seeded_db, "src-my-own-xbot")["display_name"] == "我的 xbot"
    assert _links(seeded_db) == []
    log = seeded_db.execute(
        "SELECT COUNT(*) FROM schema_cleanup_log WHERE migration = ?",
        ("039_xbot_feed_source",),
    ).fetchone()[0]
    assert log == 0


# ── 11. a deleted source is restored (Q-O-2, Mttt 2026-11-05) ─────────


def test_039_restores_a_deleted_source(seeded_db):
    """Mttt decided: yes, restore it.  Deactivating is the way to stop it."""
    seeded_db.execute("DELETE FROM sources WHERE id = ?", (SOURCE_ID,))
    seeded_db.commit()
    assert _row(seeded_db) is None

    _run_sql_file(seeded_db, MIGRATION_039)

    row = _row(seeded_db)
    assert row is not None
    assert row["name"] == SOURCE_NAME
    assert set(_links(seeded_db)) == EXPECTED_TAGS


def test_039_restores_a_deleted_source_on_unseeded_db(unseeded_db):
    """Same, on a database with no seed at all."""
    _run_sql_file(unseeded_db, MIGRATION_039)
    assert _row(unseeded_db) is not None


# ── 12. the migrate-only upgrade path (B-1 guard) ──────────────────────


def _rewind_to_v38(conn) -> None:
    """Rewind the version marker so migrate() applies 039 again.

    Migration 039 is what turns a v38 database into a v39 one, so a test
    of the *upgrade* has to start from v38 -- otherwise migrate()
    early-exits ("already at version 39") and never applies anything.

    Only the version marker is rewound: the source row itself is the
    thing under test, so each test decides whether it is present.
    """
    conn.execute("DELETE FROM schema_version WHERE version >= 39")
    conn.commit()


def _run_module(args: list[str], db_path: Path, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "moni.db.migrate", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env={
            **dict(__import__("os").environ),
            "MONI_DB_PATH": str(db_path),
            "PYTHONPATH": str(cwd),
        },
    )


def test_039_migrate_only_path_preserves_user_edits(tmp_path):
    """`python3 -m moni.db.migrate` must not touch the user's data.

    This is the upgrade command the README tells users to run, so it is
    the one that must not re-seed.
    """
    db_path = tmp_path / "news.db"
    close_db()
    set_db_path(db_path)
    migrate()
    seed()
    conn = get_db()
    conn.execute(
        "UPDATE sources SET display_name = 'MY-XBOT', is_active = 0, "
        "fetch_interval_min = 15, notes = '我的备注', "
        "fetch_config = '{\"max_days\": 3}' WHERE id = ?",
        (SOURCE_ID,),
    )
    conn.execute("DELETE FROM source_tags WHERE source_id = ? AND tag_name = ?",
                 (SOURCE_ID, "ai-fintech"))
    conn.commit()
    before = _row(conn)
    before_links = _links(conn)
    _rewind_to_v38(conn)
    close_db()

    proc = _run_module([], db_path, REPO_ROOT)
    assert proc.returncode == 0, proc.stderr

    close_db()
    set_db_path(db_path)
    conn = get_db()
    assert _row(conn) == before
    # A migration only adds links, it never removes them.
    assert set(before_links).issubset(set(_links(conn)))
    close_db()


def test_039_migrate_only_does_not_run_seed(tmp_path):
    """migrate-only must not seed: an unseeded database stays unseeded."""
    db_path = tmp_path / "news.db"
    close_db()
    set_db_path(db_path)
    migrate()
    seed()
    conn = get_db()
    conn.execute("DELETE FROM sources WHERE id = ?", (SOURCE_ID,))
    conn.execute(
        "UPDATE sources SET display_name = 'OTHER-SOURCE-EDIT' WHERE id = ?",
        ("src-36kr-rss",),
    )
    conn.commit()
    _rewind_to_v38(conn)
    close_db()

    proc = _run_module([], db_path, REPO_ROOT)
    assert proc.returncode == 0, proc.stderr

    close_db()
    set_db_path(db_path)
    conn = get_db()
    # The other source keeps the user's edit -> seed did not run.
    other = conn.execute(
        "SELECT display_name FROM sources WHERE id = ?", ("src-36kr-rss",)
    ).fetchone()
    assert other is not None
    assert other[0] == "OTHER-SOURCE-EDIT"
    # ... and the migration still landed the source.
    assert _row(conn) is not None
    close_db()


def test_039_db_init_does_clobber_user_edits(tmp_path):
    """`moni db init` = migrate + seed, so it DOES reset the user's edits.

    This is not a wish -- it is the documented reason the upgrade command
    is `python3 -m moni.db.migrate` and not `moni db init`.  Pinning it
    keeps the README honest.
    """
    db_path = tmp_path / "news.db"
    close_db()
    set_db_path(db_path)
    migrate()
    seed()
    conn = get_db()
    conn.execute(
        "UPDATE sources SET display_name = 'MY-XBOT', is_active = 0, "
        "fetch_interval_min = 15, notes = '我的备注' WHERE id = ?",
        (SOURCE_ID,),
    )
    conn.commit()
    close_db()

    proc = subprocess.run(
        [sys.executable, "-m", "moni", "db", "init"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={
            **dict(__import__("os").environ),
            "MONI_DB_PATH": str(db_path),
            "PYTHONPATH": str(REPO_ROOT),
        },
    )
    assert proc.returncode == 0, proc.stderr

    close_db()
    set_db_path(db_path)
    conn = get_db()
    row = _row(conn)
    assert row["display_name"] == "X-bot 精选"
    assert row["fetch_interval_min"] == 60
    close_db()


# ── 13. upgrading a v38 database that predates the xbot_feed source ────
#
# v1.0.67 (IMPL-AUDIT S-5): this section used to build the "old" database
# with `git worktree add 015a2c5` and silently `pytest.skip()` when that
# failed.  CI checks out with the default `fetch-depth: 1`, so the commit
# is not in the clone and the whole test **skipped** on CI -- the single
# most important guard of this migration had zero coverage there.
#
# The fix: build the v38 database **by hand** (a hand-written, minimal
# v38 schema), so the upgrade path is validated in every environment --
# shallow clone, tarball export, no git at all.  The git-based variant is
# kept *below* as an extra layer; CI now uses `fetch-depth: 0` so it runs
# there too.

#: The schema version an "existing database" sits at before 039.
V38_VERSION = 38

#: The minimal v38 schema an existing database has.
#:
#: Hand-written on purpose: this is the *shape* a real v38 database has
#: (verified against a database actually built by the pre-xbot_feed
#: code), not a copy of today's schema.sql.  Only the tables migration
#: 039 touches are needed:
#:
#:   * ``sources``         -- without ``src-xbot-feed``
#:   * ``source_tags``     -- the link table
#:   * ``tag_definitions`` -- the four business tags (optional; a
#:     database that never ran seed-config has none)
#:   * ``schema_version``  -- pinned at 38
#:
#: The column lists are the v38 ones (migration 012 added
#: ``sources.fetch_config``, migration 006 added
#: ``tag_definitions.keywords``, migration 007 added
#: ``tag_definitions.display_name``).
_V38_SCHEMA = """
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE sources (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,
    display_name        TEXT,
    source_type         TEXT NOT NULL,
    url                 TEXT NOT NULL,
    fetch_interval_min  INTEGER NOT NULL DEFAULT 30,
    is_active           INTEGER NOT NULL DEFAULT 1,
    notes               TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    fetch_config        TEXT
);

CREATE TABLE source_tags (
    source_id   TEXT NOT NULL,
    tag_name    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (source_id, tag_name),
    FOREIGN KEY (source_id) REFERENCES sources(id)      ON DELETE CASCADE,
    FOREIGN KEY (tag_name)  REFERENCES tag_definitions(name) ON DELETE CASCADE
);

CREATE TABLE tag_definitions (
    name        TEXT PRIMARY KEY,
    description TEXT,
    color       TEXT,
    created_at  TEXT NOT NULL,
    is_active   INTEGER NOT NULL DEFAULT 1,
    keywords    TEXT,
    display_name TEXT
);

CREATE TABLE schema_cleanup_log (
    migration   TEXT PRIMARY KEY,
    applied_at  TEXT NOT NULL
);
"""

#: The four business tags migration 039 binds the source to.
_V38_TAGS = ("wechat", "miniprogram", "general-tech", "ai-fintech")

#: A source that existed before the xbot_feed source was added.  It is
#: here so the test can prove the migration leaves other sources alone.
_OLD_SOURCE_ID = "src-36kr-rss"


def _build_v38_db(
    db_path: Path,
    *,
    with_tags: bool,
    with_other_source: bool = True,
) -> None:
    """Write a minimal, hand-built v38 database to ``db_path``.

    ``with_tags=False`` models the real "existing database" case: one
    that never ran ``seed-config`` and therefore has no tag_definitions
    rows at all.  ``with_tags=True`` models a database that has the
    business tags but not the xbot_feed source.

    The schema_version marker is pinned at 38, so ``migrate()`` sees a
    database that is genuinely behind and applies the pending
    migrations -- exactly like a real one.  No git, no network, no
    external state.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(_V38_SCHEMA)
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (V38_VERSION, "2026-11-05 00:00:00"),
        )
        if with_other_source:
            conn.execute(
                "INSERT INTO sources (id, name, display_name, source_type, url, "
                "fetch_interval_min, is_active, notes, created_at, updated_at) "
                "VALUES (?, '36kr_rss', '36氪 RSS', 'rss', "
                "'https://36kr.com/feed', 5, 1, 'old source', "
                "'2026-06-05 00:00:00', '2026-06-05 00:00:00')",
                (_OLD_SOURCE_ID,),
            )
        if with_tags:
            for tag in _V38_TAGS:
                conn.execute(
                    "INSERT INTO tag_definitions (name, description, color, "
                    "created_at, is_active) VALUES (?, ?, '#000000', "
                    "'2026-06-05 00:00:00', 1)",
                    (tag, f"tag {tag}"),
                )
        conn.commit()
    finally:
        conn.close()


def test_039_upgrades_a_v38_db_without_seed(tmp_path):
    """The genuine upgrade path, with **no git history required**.

    A hand-built v38 database (schema_version = 38, sources without
    ``src-xbot-feed``, no tag_definitions) is upgraded with the current
    code: the source must land, nothing may crash, and the version must
    become 39.  Running it again must be a no-op.

    This test never touches git, so it runs identically in a shallow CI
    clone, in a tarball export, and on a developer machine.
    """
    db_path = tmp_path / "v38-news.db"
    _build_v38_db(db_path, with_tags=False)

    # Preconditions -- the hand-built database really is a v38 one.
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    assert conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_version"
    ).fetchone()[0] == V38_VERSION
    assert conn.execute(
        "SELECT COUNT(*) FROM sources WHERE id = ?", (SOURCE_ID,)
    ).fetchone()[0] == 0
    conn.close()

    # Upgrade with the current code (migrate-only -- no seed).
    upgrade = _run_module([], db_path, REPO_ROOT)
    assert upgrade.returncode == 0, upgrade.stderr

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    new_version = conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_version"
    ).fetchone()[0]
    assert new_version == 39
    row = conn.execute(
        "SELECT id, name, display_name, is_active FROM sources WHERE id = ?",
        (SOURCE_ID,),
    ).fetchone()
    assert row is not None
    assert row["name"] == SOURCE_NAME
    assert row["is_active"] == 1
    # The old database had no seed, so there are no tag definitions
    # and therefore no tag links -- but no crash either.
    link_count = conn.execute(
        "SELECT COUNT(*) FROM source_tags WHERE source_id = ?", (SOURCE_ID,)
    ).fetchone()[0]
    assert link_count == 0
    conn.close()

    # Running the upgrade again is a no-op.
    again = _run_module([], db_path, REPO_ROOT)
    assert again.returncode == 0, again.stderr
    assert "nothing to migrate" in again.stdout
    conn = sqlite3.connect(str(db_path))
    final = conn.execute(
        "SELECT COUNT(*) FROM sources WHERE id = ?", (SOURCE_ID,)
    ).fetchone()[0]
    conn.close()
    assert final == 1


def test_039_upgrades_a_v38_db_that_has_the_business_tags(tmp_path):
    """Same upgrade, but the v38 database already has the four tags.

    This is the shape of a real user database that ran seed-config at
    some point but was created before the xbot_feed source existed: the
    business tags are there, the source is not.  The migration must
    land the source **and** its four bindings.
    """
    db_path = tmp_path / "v38-tags-news.db"
    _build_v38_db(db_path, with_tags=True)

    upgrade = _run_module([], db_path, REPO_ROOT)
    assert upgrade.returncode == 0, upgrade.stderr

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    assert conn.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_version"
    ).fetchone()[0] == 39
    row = conn.execute(
        "SELECT id, name, is_active FROM sources WHERE id = ?", (SOURCE_ID,)
    ).fetchone()
    assert row is not None
    assert row["name"] == SOURCE_NAME
    assert row["is_active"] == 1
    links = [
        r[0]
        for r in conn.execute(
            "SELECT tag_name FROM source_tags WHERE source_id = ? ORDER BY tag_name",
            (SOURCE_ID,),
        )
    ]
    assert set(links) == set(_V38_TAGS)
    # The other source is untouched.
    assert conn.execute(
        "SELECT COUNT(*) FROM sources WHERE id = ?", (_OLD_SOURCE_ID,)
    ).fetchone()[0] == 1
    conn.close()


def test_039_upgrade_path_needs_no_git(tmp_path):
    """The upgrade path must not depend on git being available.

    Regression guard for IMPL-AUDIT S-5: the old version of this file
    built the "old" database with ``git worktree add 015a2c5`` and
    silently skipped when that failed -- which is *always* the case in
    a shallow CI clone (``fetch-depth: 1``).  The migration's most
    important guard therefore had zero coverage on CI.

    This test proves the upgrade works with **no git binary on PATH**
    and from a working directory that is not a git repository.
    """
    db_path = tmp_path / "v38-news.db"
    _build_v38_db(db_path, with_tags=True)

    # An empty PATH -- there is no `git` (or anything else) to be found.
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()

    proc = subprocess.run(
        [sys.executable, "-m", "moni.db.migrate"],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),          # not a git repository
        env={
            **dict(__import__("os").environ),
            "PATH": str(empty_bin),
            "MONI_DB_PATH": str(db_path),
            "PYTHONPATH": str(REPO_ROOT),
        },
    )
    assert proc.returncode == 0, proc.stderr
    conn = sqlite3.connect(str(db_path))
    assert conn.execute(
        "SELECT COUNT(*) FROM sources WHERE id = ?", (SOURCE_ID,)
    ).fetchone()[0] == 1
    conn.close()


def test_039_upgrades_a_db_built_by_old_code(tmp_path):
    """Extra layer: build the database with the *actual* pre-xbot_feed code.

    This is the strongest form of the guard -- the database is created
    by the real code from before the xbot_feed source existed, then
    upgraded with the current code.

    It needs the old commit in the local clone.  CI now uses
    ``fetch-depth: 0``, so it runs there; a shallow local clone skips
    it with an explicit reason.  Either way the upgrade path itself is
    covered by the hand-built variants above, which never skip.
    """
    proc = subprocess.run(
        ["git", "cat-file", "-e", f"{OLD_COMMIT}^{{commit}}"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        pytest.skip(
            f"{OLD_COMMIT} is not in this clone (shallow checkout?) -- "
            "the hand-built v38 upgrade path above covers this case"
        )

    worktree = tmp_path / "old"
    proc = subprocess.run(
        ["git", "worktree", "add", "--detach", str(worktree), OLD_COMMIT],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    if proc.returncode != 0:
        pytest.fail(
            f"cannot create worktree at {OLD_COMMIT}: {proc.stderr.strip()}"
        )

    try:
        old_db = tmp_path / "old-news.db"
        env = {
            **dict(__import__("os").environ),
            "MONI_DB_PATH": str(old_db),
            "PYTHONPATH": str(worktree),
        }
        # Build the old database with the old code.
        build = subprocess.run(
            [sys.executable, "-m", "moni.db.migrate"],
            capture_output=True,
            text=True,
            cwd=str(worktree),
            env=env,
        )
        assert build.returncode == 0, build.stderr

        # The old code has no xbot_feed source at all.
        conn = sqlite3.connect(str(old_db))
        conn.row_factory = sqlite3.Row
        old_sources = [
            r[0] for r in conn.execute("SELECT id FROM sources ORDER BY id")
        ]
        assert SOURCE_ID not in old_sources
        old_version = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_version"
        ).fetchone()[0]
        conn.close()
        assert old_version == 38

        # Upgrade with the current code (migrate-only -- no seed).
        upgrade = subprocess.run(
            [sys.executable, "-m", "moni.db.migrate"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env={
                **dict(__import__("os").environ),
                "MONI_DB_PATH": str(old_db),
                "PYTHONPATH": str(REPO_ROOT),
            },
        )
        assert upgrade.returncode == 0, upgrade.stderr

        conn = sqlite3.connect(str(old_db))
        conn.row_factory = sqlite3.Row
        new_version = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_version"
        ).fetchone()[0]
        assert new_version == 39
        row = conn.execute(
            "SELECT id, name, display_name, is_active FROM sources WHERE id = ?",
            (SOURCE_ID,),
        ).fetchone()
        assert row is not None
        assert row["name"] == SOURCE_NAME
        assert row["is_active"] == 1
        # The old database had no seed, so there are no tag definitions
        # and therefore no tag links -- but no crash either.
        link_count = conn.execute(
            "SELECT COUNT(*) FROM source_tags WHERE source_id = ?", (SOURCE_ID,)
        ).fetchone()[0]
        assert link_count == 0
        conn.close()

        # Running the upgrade again is a no-op.
        again = subprocess.run(
            [sys.executable, "-m", "moni.db.migrate"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env={
                **dict(__import__("os").environ),
                "MONI_DB_PATH": str(old_db),
                "PYTHONPATH": str(REPO_ROOT),
            },
        )
        assert again.returncode == 0, again.stderr
        assert "nothing to migrate" in again.stdout
        conn = sqlite3.connect(str(old_db))
        final = conn.execute(
            "SELECT COUNT(*) FROM sources WHERE id = ?", (SOURCE_ID,)
        ).fetchone()[0]
        conn.close()
        assert final == 1
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        shutil.rmtree(str(worktree), ignore_errors=True)
