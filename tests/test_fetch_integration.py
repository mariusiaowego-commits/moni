"""Integration tests for the moni fetch CLI.

Runs against a real temporary database to verify:
1. --source 36kr (prefix match) fetches and inserts articles
2. Re-running same fetch dedupes (row count unchanged)
3. --health flag runs health checks and prints status codes
4. --dry-run skips DB insert

Prerequisites: the moni package must be importable (PYTHONPATH=. or pip install -e .)
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure the moni package in this worktree is importable
WT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(WT_ROOT))


@pytest.fixture
def clean_env(tmp_path):
    """Set up a clean environment: fresh temp DB, isolated config, no last_run."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    config_dir = tmp_path / "config"
    config_dir.mkdir()

    # Copy sources.yaml from the worktree (read-only OK for tests)
    wt_sources = WT_ROOT / "config" / "sources.yaml"
    if wt_sources.exists():
        (config_dir / "sources.yaml").write_bytes(wt_sources.read_bytes())

    env = os.environ.copy()
    env["PYTHONPATH"] = str(WT_ROOT)
    return data_dir, config_dir, env


def _run_fetch(
    data_dir: Path,
    config_dir: Path,
    env: dict,
    *cli_args: str,
) -> subprocess.CompletedProcess:
    """Run `python3 -m moni fetch` with the given args in the worktree.

    MONI_DATA_DIR is set so both the SQLite DB and the scheduler's
    last_run.json are co-located in the same isolated temp directory.
    """
    env = dict(env)  # don't mutate caller's env
    env["MONI_DATA_DIR"] = str(data_dir)
    cmd = [sys.executable, "-m", "moni", "fetch", *cli_args]
    return subprocess.run(
        cmd,
        cwd=str(WT_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


def _row_count(data_dir: Path) -> int:
    db_path = data_dir / "news.db"
    conn = sqlite3.connect(db_path)
    n = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    conn.close()
    return n


class TestFetchCLI:
    """Integration tests for python3 -m moni fetch."""

    def test_fetch_source_prefix(self, clean_env, tmp_path):
        """`--source 36kr` fetches matching sources and inserts articles."""
        data_dir, config_dir, env = clean_env
        # Set MONI_DATA_DIR to the temp data directory (not the file path)
        env["MONI_DATA_DIR"] = str(data_dir)

        # Run init to create schema
        r = subprocess.run(
            [sys.executable, "-m", "moni", "db", "init"],
            cwd=str(WT_ROOT), capture_output=True, text=True, env=env,
        )
        assert r.returncode == 0, f"db init failed: {r.stderr}"

        # Fetch only 36kr sources (prefix match)
        r = _run_fetch(data_dir, config_dir, env, "--source", "36kr")
        assert r.returncode == 0, f"fetch --source 36kr failed:\nstdout: {r.stdout}\nstderr: {r.stderr}"

        n = _row_count(data_dir)
        assert n > 0, f"Expected articles after first fetch, got {n}. stdout: {r.stdout}"
        print(f"  After first fetch: {n} articles")

    def test_dedupe_on_re_fetch(self, clean_env, tmp_path):
        """Re-running fetch does not increase article count (dedupe)."""
        data_dir, config_dir, env = clean_env
        env["MONI_DATA_DIR"] = str(data_dir)

        # Init
        r = subprocess.run(
            [sys.executable, "-m", "moni", "db", "init"],
            cwd=str(WT_ROOT), capture_output=True, text=True, env=env,
        )
        assert r.returncode == 0, f"db init failed: {r.stderr}"

        # First fetch
        r1 = _run_fetch(data_dir, config_dir, env, "--source", "36kr")
        assert r1.returncode == 0, f"First fetch failed:\nstdout: {r1.stdout}\nstderr: {r1.stderr}"
        n1 = _row_count(data_dir)
        assert n1 > 0, f"First fetch should have inserted articles, got {n1}\nstdout: {r1.stdout}"

        # Second fetch — should be deduped
        r2 = _run_fetch(data_dir, config_dir, env, "--source", "36kr")
        assert r2.returncode == 0, f"Second fetch failed: {r2.stderr}"
        n2 = _row_count(data_dir)

        assert n2 == n1, (
            f"Dedupe failed: first fetch={n1} articles, second fetch={n2} articles. "
            f"stdout: {r2.stdout}"
        )
        print(f"  Dedupe verified: {n1} articles, re-fetch added 0")

    def test_health_flag(self, clean_env, tmp_path):
        """`--health` runs health checks and prints status codes."""
        data_dir, config_dir, env = clean_env
        env["MONI_DATA_DIR"] = str(data_dir)

        r = _run_fetch(data_dir, config_dir, env, "--health")
        assert r.returncode == 0, f"--health failed: {r.stderr}"
        output = r.stdout

        # Should contain status codes or error strings
        assert output, "Health check produced no output"
        # Look for status indicators: 'status=' or HTTP codes or ✓/✗
        assert any(kw in output for kw in ["status=", "✓", "✗", "status_code"]), (
            f"Health output doesn't contain status indicators: {output!r}"
        )
        print(f"  Health output:\n{r.stdout}")

    def test_dry_run_skips_insert(self, clean_env, tmp_path):
        """`--dry-run` fetches articles but does not insert into DB."""
        data_dir, config_dir, env = clean_env
        env["MONI_DATA_DIR"] = str(data_dir)

        # Init
        r = subprocess.run(
            [sys.executable, "-m", "moni", "db", "init"],
            cwd=str(WT_ROOT), capture_output=True, text=True, env=env,
        )
        assert r.returncode == 0

        n_before = _row_count(data_dir)
        assert n_before == 0, f"DB should be empty after init, got {n_before}"

        # Dry run fetch
        r = _run_fetch(data_dir, config_dir, env, "--source", "36kr", "--dry-run")
        assert r.returncode == 0, f"--dry-run failed: {r.stderr}"

        n_after = _row_count(data_dir)
        assert n_after == 0, (
            f"--dry-run should not insert articles, but DB has {n_after} rows. "
            f"stdout: {r.stdout}"
        )
        # Verify dry-run message in output
        assert "dry-run" in r.stdout or "dry-run" in r.stderr.lower(), (
            f"Expected 'dry-run' in output: {r.stdout}"
        )
        print(f"  Dry-run verified: DB still empty after fetch, output:\n{r.stdout}")


class TestFetchHelp:
    """Smoke tests for CLI help."""

    def test_fetch_help(self):
        r = subprocess.run(
            [sys.executable, "-m", "moni", "fetch", "--help"],
            cwd=str(WT_ROOT), capture_output=True, text=True,
        )
        assert r.returncode == 0, f"--help failed: {r.stderr}"
        for flag in ["--source", "--dry-run", "--health"]:
            assert flag in r.stdout, f"--help should mention {flag}"
