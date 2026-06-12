"""tests/test_share_manifest.py — pytest coverage for the v1.0 share manifest pipeline.

Covers the five end-to-end test categories called out in the task body:

1. **Schema validation** — Each of the 10 v1.0 formats accepts valid data
   and rejects invalid data.
2. **write_manifest unit test** — Mock DB and filesystem; verify the
   INSERT is called with the correct params and the JSON file is written
   to the correct path with the correct content.
3. **Failure path** — Simulate a file-write failure *after* the DB
   commit; verify the error is logged and the DB row remains as
   'pending' (no rollback).
4. **API integration test** — POST /api/share with a valid payload;
   assert 201, share_tasks row exists with status='pending', and
   ~/.moni/queue/<id>.json exists and parses as valid JSON.
5. **macOS home-dir path test** — Verify ~/.moni/queue resolves
   correctly and is writable (use tmp_path or monkeypatch HOME).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# Make `moni` importable.  The package is editable-installed at the main
# checkout, so this is belt-and-suspenders for in-worktree runs.
sys.path.insert(0, os.path.expanduser("~/dev/source-monitor"))

from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.share import VALID_FORMATS, validate_manifest, write_manifest
from moni.share.formats import FormatEnum
from moni.share.manifest import _queue_dir
from moni.web.exceptions import BadRequestError
from moni.web.server import create_app


# ---------------------------------------------------------------------------
# Per-test fixtures: fresh DB, fresh queue dir
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up a brand-new SQLite DB at ``tmp_path / 'test.db'`` and run migrations.

    Yields the DB path.  Tears down by closing the connection and clearing
    the module-level singletons so the next test gets a clean slate.
    """
    db_path = tmp_path / "share_test.db"
    set_db_path(db_path)
    migrate()
    yield db_path
    close_db()

    # Reset the connection-module singleton so the next test can set a new path.
    import moni.db.connection as conn_mod

    conn_mod._CON = None
    conn_mod._DB_PATH = None


@pytest.fixture
def client(fresh_db: Path) -> TestClient:
    """A FastAPI test client backed by the fresh DB fixture."""
    return TestClient(create_app())


@pytest.fixture
def seeded_article_id(client: TestClient) -> str:
    """Insert a minimal article row; return its string ID."""
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO articles (id, title, url, source, publisher, published_at, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("art-share-test", "Test Article", "https://example.com/x",
         "testsrc", "TestPub", now, now),
    )
    conn.commit()
    return "art-share-test"


# ---------------------------------------------------------------------------
# Minimal-valid payloads for each of the 10 v1.0 formats
# ---------------------------------------------------------------------------

# Each entry is the ``payload`` portion of a v1.0 manifest.  Every format
# requires ``article_ids``; the rest are format-specific knobs.
# v1.0.5.18: 6 formats (was 10).  Dropped architecture_svg /
# knowledge_comic / ascii_art / ascii_video / pdf_bound / ppt_slides
# (never had a real worker).  Added rich_text + report_png.
_PAYLOADS_VALID: dict[str, dict[str, Any]] = {
    "plain_text": {
        "article_ids": ["a1"],
    },
    "markdown": {
        "article_ids": ["a1"],
    },
    "html_landing": {
        "article_ids": ["a1"],
        "title": "My Landing Page",
    },
    "info_graphic_png": {
        "article_ids": ["a1"],
    },
    "rich_text": {                              # 🆕 v1.0.5.18
        "article_ids": ["a1"],
    },
    "report_png": {                             # 🆕 v1.0.5.18
        "article_ids": ["a1"],
    },
    "pdf": {                                    # 🆕 v1.0.5.21
        "article_ids": ["a1"],
        "title": "测试日报",
    },
}


# ===========================================================================
# 1. Schema validation — each of the 10 formats accepts valid data
# ===========================================================================


class TestSchemaValidation:
    """Each of the 6 v1.0.5.18 formats must validate correctly and reject bad input."""

    def test_valid_formats_set_has_6(self) -> None:
        """The registry must contain exactly 7 v1.0.5.21 formats.

        v1.0.5.18: pruned 6 formats that had no real worker (ascii_art,
        ascii_video, architecture_svg, knowledge_comic, pdf_bound,
        ppt_slides) and added rich_text + report_png.  Frontend will
        also drop the 6 chips; 6 is the new canonical count.
        v1.0.5.21: added pdf format.
        """
        assert len(VALID_FORMATS) == 7
        # No concept_video (removed in v1.0.5.15), no architecture_svg /
        # knowledge_comic / ascii_art / ascii_video / pdf_bound /
        # ppt_slides (removed in v1.0.5.18).
        for stale in (
            "concept_video", "architecture_svg", "knowledge_comic",
            "ascii_art", "ascii_video", "pdf_bound", "ppt_slides",
        ):
            assert stale not in VALID_FORMATS, (
                f"stale format {stale!r} should have been removed in v1.0.5.18"
            )
        # rich_text + report_png must be present
        assert "rich_text" in VALID_FORMATS
        assert "report_png" in VALID_FORMATS
        # Every FormatEnum value must be in the set (no orphans).
        assert {f.value for f in FormatEnum} == VALID_FORMATS

    @pytest.mark.parametrize("fmt_name", sorted(VALID_FORMATS))
    def test_valid_payload_passes(self, fmt_name: str) -> None:
        """Each format should accept its minimal-valid payload and return
        ``(format, validated_dict)``."""
        payload = _PAYLOADS_VALID[fmt_name]
        fmt_out, validated = validate_manifest(
            {"format": fmt_name, "version": "1.0", "payload": payload}
        )
        assert fmt_out == fmt_name
        assert isinstance(validated, dict)
        assert validated["article_ids"] == ["a1"]

    @pytest.mark.parametrize("fmt_name", sorted(VALID_FORMATS))
    def test_unknown_format_rejected(self, fmt_name: str) -> None:
        """An unknown format name must raise BadRequestError."""
        payload = _PAYLOADS_VALID[fmt_name]
        with pytest.raises(BadRequestError) as exc_info:
            validate_manifest(
                {"format": "no_such_format", "version": "1.0", "payload": payload}
            )
        assert "Unknown format" in str(exc_info.value)

    @pytest.mark.parametrize("fmt_name", sorted(VALID_FORMATS))
    def test_missing_article_ids_rejected(self, fmt_name: str) -> None:
        """Every format requires ``article_ids`` — omitting it must fail."""
        # Strip article_ids; replace with a benign field if it was the only one.
        bad_payload: dict[str, Any] = {
            k: v for k, v in _PAYLOADS_VALID[fmt_name].items() if k != "article_ids"
        }
        # Some formats have only article_ids; we need at least one key for
        # Pydantic to attempt validation.  Add a junk key to trigger the
        # missing-required-field error.
        if not bad_payload:
            bad_payload = {"include_toc": True}  # arbitrary extra
        with pytest.raises(BadRequestError) as exc_info:
            validate_manifest(
                {"format": fmt_name, "version": "1.0", "payload": bad_payload}
            )
        assert "Validation failed" in str(exc_info.value)

    @pytest.mark.parametrize("fmt_name", sorted(VALID_FORMATS))
    def test_extra_field_rejected(self, fmt_name: str) -> None:
        """All 10 models have ``extra='forbid'`` — extra fields must fail."""
        payload = dict(_PAYLOADS_VALID[fmt_name])
        payload["this_field_does_not_exist"] = "boom"
        with pytest.raises(BadRequestError) as exc_info:
            validate_manifest(
                {"format": fmt_name, "version": "1.0", "payload": payload}
            )
        assert "Validation failed" in str(exc_info.value)

    def test_unsupported_version_rejected(self) -> None:
        """Only ``version == '1.0'`` is accepted."""
        with pytest.raises(BadRequestError) as exc_info:
            validate_manifest(
                {
                    "format": "markdown",
                    "version": "2.0",
                    "payload": _PAYLOADS_VALID["markdown"],
                }
            )
        assert "Unsupported manifest version" in str(exc_info.value)

    def test_top_level_structure_enforced(self) -> None:
        """Missing required top-level fields must raise BadRequestError."""
        # Missing 'format'
        with pytest.raises(BadRequestError):
            validate_manifest({"version": "1.0", "payload": {}})
        # Missing 'payload'
        with pytest.raises(BadRequestError):
            validate_manifest({"format": "markdown", "version": "1.0"})

    def test_range_constraints_enforced(self) -> None:
        """Format-specific numeric ranges (e.g. font_size 8-24) are enforced."""
        # plain_text.font_size: 8 <= n <= 24
        with pytest.raises(BadRequestError) as exc_info:
            validate_manifest(
                {
                    "format": "plain_text",
                    "version": "1.0",
                    "payload": {"article_ids": ["a1"], "font_size": 999},
                }
            )
        assert "Validation failed" in str(exc_info.value)


# ===========================================================================
# 2. write_manifest unit test — INSERT + JSON file written correctly
# ===========================================================================


class TestWriteManifestUnit:
    """Mock DB and filesystem; verify INSERT params and JSON file content."""

    def test_insert_called_with_correct_params(
        self, fresh_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """write_manifest should INSERT with (id, format, article_ids_json, 'pending', timestamp)."""
        captured: dict[str, Any] = {}

        # Replace the queue dir with a tmp dir so we don't pollute ~/.moni
        fake_queue = tmp_path / "queue"
        fake_queue.mkdir()
        monkeypatch.setattr("moni.share.manifest._queue_dir", lambda: fake_queue)

        # sqlite3.Connection is a C-implemented type whose instance attributes
        # are read-only, so we cannot monkeypatch ``conn.execute`` in place.
        # Instead, wrap _db_connect to return a thin proxy that records
        # share_tasks INSERT calls and forwards everything else.
        import moni.share.manifest as manifest_mod

        real_db_connect = manifest_mod._db_connect

        class _SpyConn:
            """Proxy around sqlite3.Connection that captures INSERT params."""

            def __init__(self, real: sqlite3.Connection) -> None:
                self._real = real

            def execute(self, sql: str, params: Any = (), *a: Any, **kw: Any) -> Any:
                if "INSERT INTO share_tasks" in sql:
                    captured["sql"] = sql
                    captured["params"] = params
                return self._real.execute(sql, params, *a, **kw)

            def commit(self) -> Any:
                return self._real.commit()

            def rollback(self) -> Any:
                return self._real.rollback()

        def spy_db_connect(*args: Any, **kwargs: Any) -> _SpyConn:
            return _SpyConn(real_db_connect(*args, **kwargs))

        monkeypatch.setattr(manifest_mod, "_db_connect", spy_db_connect)

        validated = {
            "format": "markdown",
            "article_ids": ["a1", "a2"],
        }
        share_id = write_manifest(
            share_id="test-id-001",
            validated_payload=validated,
            format_name="markdown",
        )

        assert share_id == "test-id-001"
        assert "INSERT INTO share_tasks" in captured["sql"]
        # Note: status='pending' is a SQL literal, not a bound param, so
        # params has 4 elements: (id, format, article_ids_json, created_at).
        params = captured["params"]
        assert params[0] == "test-id-001"
        assert params[1] == "markdown"
        assert json.loads(params[2]) == ["a1", "a2"]  # article_ids JSON
        assert isinstance(params[3], str)  # ISO8601 timestamp
        # And the SQL literal 'pending' should be present in the query.
        assert "'pending'" in captured["sql"]

    def test_queue_file_written_to_correct_path_with_correct_content(
        self, fresh_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """JSON queue file must be at ``<queue_dir>/<share_id>.json`` and
        contain the manifest document."""
        fake_queue = tmp_path / "queue"
        fake_queue.mkdir()
        monkeypatch.setattr("moni.share.manifest._queue_dir", lambda: fake_queue)

        # v1.0.5.18: use one of the 6 surviving formats (was pdf_bound).
        validated = {
            "format": "markdown",
            "article_ids": ["x9"],
        }
        share_id = write_manifest(
            share_id="file-test-002",
            validated_payload=validated,
            format_name="markdown",
        )

        queue_path = fake_queue / "file-test-002.json"
        assert queue_path.exists(), f"Queue file not written: {queue_path}"
        doc = json.loads(queue_path.read_text(encoding="utf-8"))
        assert doc["share_id"] == "file-test-002"
        assert doc["status"] == "pending"
        assert doc["format"] == "markdown"
        assert doc["payload"] == validated
        assert isinstance(doc["created_at"], str)

    def test_share_id_generated_when_none(
        self, fresh_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When share_id is None, write_manifest must generate a non-empty id."""
        fake_queue = tmp_path / "queue"
        fake_queue.mkdir()
        monkeypatch.setattr("moni.share.manifest._queue_dir", lambda: fake_queue)

        share_id = write_manifest(
            share_id=None,
            validated_payload={"format": "markdown", "article_ids": ["a1"]},
            format_name="markdown",
        )
        assert isinstance(share_id, str)
        assert len(share_id) > 0
        # The file should exist with the generated id.
        assert (fake_queue / f"{share_id}.json").exists()

    def test_format_name_extracted_from_payload_when_not_passed(
        self, fresh_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When format_name is None, write_manifest must pull it from the payload."""
        fake_queue = tmp_path / "queue"
        fake_queue.mkdir()
        monkeypatch.setattr("moni.share.manifest._queue_dir", lambda: fake_queue)

        # v1.0.5.18: was ascii_art.  Use one of the 6 surviving formats.
        share_id = write_manifest(
            share_id="fmt-extract-001",
            validated_payload={"format": "rich_text", "article_ids": ["a1"]},
        )
        # Verify the file's format field is "rich_text"
        doc = json.loads((fake_queue / "fmt-extract-001.json").read_text())
        assert doc["format"] == "rich_text"

    def test_created_at_is_timezone_aware_utc_iso8601(self) -> None:
        """created_at default_factory must produce a timezone-aware UTC ISO8601
        string ending in 'Z' (regression for v1.0.5.13: datetime.utcnow()
        DeprecationWarning under Python 3.12+).
        """
        from moni.share.formats import ManifestPayload

        # No warning should be emitted by the new code path.
        with warnings.catch_warnings(record=True) as wlist:
            warnings.simplefilter("always")
            p = ManifestPayload(format="markdown", payload={"article_ids": ["x"]})
            deprecation_warnings = [
                w for w in wlist
                if issubclass(w.category, DeprecationWarning)
                and "utcnow" in str(w.message)
            ]
            assert deprecation_warnings == [], (
                f"ManifestPayload default_factory should not call "
                f"datetime.utcnow(); got: {[str(w.message) for w in deprecation_warnings]}"
            )

        ts = p.created_at
        # Must end with 'Z' (RFC 3339 UTC marker)
        assert ts.endswith("Z"), f"created_at should end with 'Z', got: {ts!r}"
        # Must parse as ISO 8601 with explicit UTC offset
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timezone.utc.utcoffset(parsed)


# ===========================================================================
# 3. Failure path — file write fails after DB commit
# ===========================================================================


class TestFailurePath:
    """A post-commit file-write failure must log the error and leave the
    DB row in 'pending' state (no rollback)."""

    def test_file_write_failure_does_not_rollback_db(
        self,
        fresh_db: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Patch Path.write_text to raise OSError; verify the row stays pending."""
        # Use a queue dir we control.
        fake_queue = tmp_path / "queue"
        fake_queue.mkdir()
        monkeypatch.setattr("moni.share.manifest._queue_dir", lambda: fake_queue)

        # Patch write_text to raise.
        real_write_text = Path.write_text

        def boom(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise OSError("simulated disk full")

        monkeypatch.setattr(Path, "write_text", boom)

        with caplog.at_level(logging.ERROR, logger="moni.share.manifest"):
            with pytest.raises(OSError, match="simulated disk full"):
                write_manifest(
                    share_id="fail-001",
                    validated_payload={"format": "markdown", "article_ids": ["a1"]},
                    format_name="markdown",
                )

        # The DB row MUST still exist with status='pending' (no rollback).
        # Use a fresh connection to verify it's actually persisted, not just
        # sitting in the singleton's uncommitted state.
        fresh = sqlite3.connect(str(fresh_db))
        fresh.row_factory = sqlite3.Row
        row = fresh.execute(
            "SELECT id, status FROM share_tasks WHERE id = ?", ("fail-001",)
        ).fetchone()
        fresh.close()
        assert row is not None, "DB row should NOT be rolled back"
        assert row["status"] == "pending"

        # The error must be logged.
        assert any(
            "queue file write FAILED" in record.message
            for record in caplog.records
        ), f"Expected log entry missing; got: {[r.message for r in caplog.records]}"

        # The queue file must not exist.
        assert not (fake_queue / "fail-001.json").exists()

    def test_db_error_rolls_back_and_does_not_write_file(
        self,
        fresh_db: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A DB error during INSERT must roll back the transaction AND
        skip the file write."""
        fake_queue = tmp_path / "queue"
        fake_queue.mkdir()
        monkeypatch.setattr("moni.share.manifest._queue_dir", lambda: fake_queue)

        # Force the DB connection's execute() to fail on the share_tasks INSERT
        # by wrapping _db_connect to return a proxy.
        import moni.share.manifest as manifest_mod

        real_db_connect = manifest_mod._db_connect

        class _FaultyConn:
            """Proxy that raises OperationalError on share_tasks INSERT."""

            def __init__(self, real: sqlite3.Connection) -> None:
                self._real = real

            def execute(self, sql: str, params: Any = (), *a: Any, **kw: Any) -> Any:
                if "INSERT INTO share_tasks" in sql:
                    raise sqlite3.OperationalError("simulated db failure")
                return self._real.execute(sql, params, *a, **kw)

            def commit(self) -> Any:
                return self._real.commit()

            def rollback(self) -> Any:
                return self._real.rollback()

        def faulty_db_connect(*args: Any, **kwargs: Any) -> _FaultyConn:
            return _FaultyConn(real_db_connect(*args, **kwargs))

        monkeypatch.setattr(manifest_mod, "_db_connect", faulty_db_connect)

        with caplog.at_level(logging.ERROR, logger="moni.share.manifest"):
            with pytest.raises(sqlite3.OperationalError, match="simulated db failure"):
                write_manifest(
                    share_id="db-fail-001",
                    validated_payload={"format": "markdown", "article_ids": ["a1"]},
                    format_name="markdown",
                )

        # No file should have been written
        assert not (fake_queue / "db-fail-001.json").exists()
        # And a rollback log entry should exist
        assert any(
            "DB rollback" in record.message
            for record in caplog.records
        )


# ===========================================================================
# 4. API integration test — POST /api/share end-to-end
# ===========================================================================


class TestShareAPIIntegration:
    """End-to-end: POST /api/share → 201 → DB row → queue file."""

    def test_post_share_creates_row_and_queue_file(
        self,
        client: TestClient,
        seeded_article_id: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A valid POST /api/share must return 201, insert a 'pending' row,
        and write a valid JSON file at the queue path."""
        # Redirect the queue dir to tmp_path so we don't touch ~/.moni
        fake_queue = tmp_path / "queue"
        fake_queue.mkdir()
        monkeypatch.setattr("moni.share.manifest._queue_dir", lambda: fake_queue)

        r = client.post(
            "/api/share",
            json={
                "format": "markdown",
                "version": "1.0",
                "payload": {
                    "article_ids": [seeded_article_id],
                    "include_toc": True,
                },
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        share_id = body["share_id"]
        assert isinstance(share_id, str) and len(share_id) > 0
        assert body["status"] == "pending"

        # --- DB row ---
        conn = get_db()
        row = conn.execute(
            "SELECT id, format, article_ids, status FROM share_tasks WHERE id = ?",
            (share_id,),
        ).fetchone()
        conn.close()  # release the file lock
        assert row is not None
        assert row["id"] == share_id
        assert row["format"] == "markdown"
        assert row["status"] == "pending"
        assert json.loads(row["article_ids"]) == [seeded_article_id]

        # --- Queue file ---
        queue_path = fake_queue / f"{share_id}.json"
        assert queue_path.exists(), f"Queue file missing: {queue_path}"
        doc = json.loads(queue_path.read_text(encoding="utf-8"))
        assert doc["share_id"] == share_id
        assert doc["format"] == "markdown"
        assert doc["status"] == "pending"
        assert doc["payload"]["article_ids"] == [seeded_article_id]

    def test_post_share_400_on_invalid_payload(
        self, client: TestClient
    ) -> None:
        """Invalid payload (missing required field) returns 400 — no DB row, no file."""
        r = client.post(
            "/api/share",
            json={
                "format": "markdown",
                "version": "1.0",
                "payload": {"include_toc": True},  # missing article_ids
            },
        )
        assert r.status_code == 400

    def test_post_share_400_on_unknown_format(self, client: TestClient) -> None:
        r = client.post(
            "/api/share",
            json={
                "format": "no_such_format",
                "version": "1.0",
                "payload": {"article_ids": ["a1"]},
            },
        )
        assert r.status_code == 400


# ===========================================================================
# 5. macOS home-dir path test — ~/.moni/queue resolves correctly
# ===========================================================================


class TestHomeDirPath:
    """Verify the queue directory is resolved from $HOME and is writable."""

    def test_queue_dir_resolves_under_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With HOME pointed at a tmp dir, _queue_dir() must return <HOME>/.moni/queue."""
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))

        qdir = _queue_dir()
        expected = fake_home / ".moni" / "queue"
        assert qdir == expected
        assert qdir.is_dir(), "Queue dir should be created on demand"

    def test_queue_dir_is_writable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A file written to the queue dir must succeed (no permission errors)."""
        fake_home = tmp_path / "fakehome2"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))

        qdir = _queue_dir()
        probe = qdir / "probe.txt"
        probe.write_text("hello", encoding="utf-8")
        assert probe.read_text(encoding="utf-8") == "hello"

    def test_queue_dir_uses_expanduser_tilde(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the function uses ``Path('~').expanduser()`` semantics by
        confirming the returned path is a subpath of the (monkeypatched) HOME."""
        fake_home = tmp_path / "tilde_home"
        fake_home.mkdir()
        monkeypatch.setenv("HOME", str(fake_home))

        qdir = _queue_dir()
        # expanduser('~') on POSIX resolves to $HOME
        assert qdir.is_relative_to(fake_home)
        assert ".moni" in qdir.parts
        assert "queue" in qdir.parts
