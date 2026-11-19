"""tests/test_worker_llm_failure.py — v1.0.31 sprint-12 PNG 生图流程.

Covers the three worker-side fixes in ``moni/share/worker_llm.py``:

- A1: ``llm_model_mode=explicit`` resolves provider/model from
  ``llm_settings.active_summary_model_id`` → ``llm_models`` → ``llm_providers``
  and passes them as ``--provider X -m Y``; ``profile`` mode omits both and
  follows the hermes profile default. Manifest overrides win in both modes.
- A2: stdout with LLM failure keywords (API call failed / HTTP 4xx / HTTP
  5xx / rate limit / insufficient credits / requires available credits)
  → exit code 1 so the worker marks the task failed, not "假 done".
- A3: remote PNG URL extraction + PNG magic bytes validation.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from moni.share import worker_llm
from moni.share.worker_llm import (
    PNG_MAGIC,
    detect_llm_failure,
    download_png,
    extract_remote_png_urls,
    is_png,
    resolve_provider_model,
    run_hermes,
)


class TestNoBodyGate:
    """clean_v1 ``no_body`` gate must only block summary/brief jobs."""

    @pytest.fixture
    def db(self, tmp_path):
        from moni.db.connection import close_db, set_db_path
        from moni.db.migrate import migrate

        path = tmp_path / "jobs.db"
        set_db_path(path)
        migrate()
        yield path
        close_db()
        import moni.db.connection as conn_mod
        import moni.db.migrate as mig_mod

        conn_mod._CON = None  # type: ignore[attr-defined]
        conn_mod._DB_PATH = None  # type: ignore[attr-defined]
        mig_mod._CON = None  # type: ignore[attr-defined]

    def _seed(self, db, task_type: str) -> tuple[str, str]:
        from datetime import datetime, timezone

        from moni.db.connection import get_db

        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO sources (id, name, display_name, source_type, url,
                                 fetch_interval_min, is_active, created_at, updated_at)
            VALUES ('src-q', 'qbitai', 'qbitai', 'rss', 'https://qbitai.com',
                    30, 1, ?, ?)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO articles (id, title, url, source, fetched_at, raw_content,
                                  content_state)
            VALUES ('a1', '标题', 'https://e.com/a1', 'qbitai', ?, NULL, 'empty')
            """,
            (now,),
        )
        job_id = "job-" + task_type
        conn.execute(
            """
            INSERT INTO llm_jobs (id, task_type, article_id, provider, model,
                                  status, created_at)
            VALUES (?, ?, 'a1', 'minimax-cn', 'MiniMax-M3', 'pending', ?)
            """,
            (job_id, task_type, now),
        )
        conn.commit()
        return job_id, "a1"

    def test_summary_empty_body_fails_before_llm(self, db, monkeypatch):
        from moni.llm import jobs as llm_jobs

        job_id, _ = self._seed(db, "summary")
        called = {"n": 0}

        def _fake_call(*args, **kwargs):
            called["n"] += 1
            return "fake"

        monkeypatch.setattr(llm_jobs, "call_llm", _fake_call)
        llm_jobs._run_job({"id": job_id, "task_type": "summary", "article_id": "a1",
                           "provider": "minimax-cn", "model": "MiniMax-M3",
                           "attempts": 0}, None)
        assert called["n"] == 0
        from moni.db.connection import get_db
        row = get_db().execute(
            "SELECT status, error FROM llm_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        assert row["status"] == "failed"
        assert "no_body" in row["error"]

    def test_fact_check_empty_body_still_calls_llm(self, db, monkeypatch):
        from moni.llm import jobs as llm_jobs

        job_id, _ = self._seed(db, "fact_check")
        called = {"n": 0}

        def _fake_call(*args, **kwargs):
            called["n"] += 1
            return "结论"

        monkeypatch.setattr(llm_jobs, "call_llm", _fake_call)
        llm_jobs._run_job({"id": job_id, "task_type": "fact_check", "article_id": "a1",
                           "provider": "minimax-cn", "model": "MiniMax-M3",
                           "attempts": 0}, None)
        assert called["n"] == 1
        from moni.db.connection import get_db
        row = get_db().execute(
            "SELECT status, error FROM llm_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        assert row["status"] == "done"
        assert row["error"] is None


# ── A1: provider/model resolution ────────────────────────────────────────


def _seed_llm_db(db_path: Path) -> None:
    """Create llm_providers / llm_models / llm_settings rows for tests."""
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE llm_providers (
            id TEXT PRIMARY KEY, name TEXT, hermes_provider TEXT,
            is_active INTEGER DEFAULT 1, notes TEXT,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE llm_models (
            id TEXT PRIMARY KEY, provider_id TEXT, model_id TEXT,
            display_name TEXT, use_for TEXT, enabled INTEGER DEFAULT 1,
            created_at TEXT,
            FOREIGN KEY (provider_id) REFERENCES llm_providers(id)
        );
        CREATE TABLE llm_settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        INSERT INTO llm_providers
            (id, name, hermes_provider, is_active, notes, created_at, updated_at)
        VALUES
            ('p-minimax', 'MiniMax CN', 'minimax-cn', 1, '', '', ''),
            ('p-google', 'Google', 'google', 1, '', '', '');
        INSERT INTO llm_models
            (id, provider_id, model_id, display_name, use_for, enabled, created_at)
        VALUES
            ('m-m3', 'p-minimax', 'MiniMax-M3', 'MiniMax M3', '["summary"]', 1, ''),
            ('m-gemini', 'p-google', 'gemini-2.5-flash-lite', 'Gemini Flash', '["summary"]', 1, '');
        INSERT INTO llm_settings (key, value)
        VALUES ('active_summary_model_id', 'm-gemini');
        """
    )
    conn.commit()
    conn.close()


def test_resolve_provider_model_reads_db_default(tmp_path):
    db = tmp_path / "test.db"
    _seed_llm_db(db)
    provider, model = resolve_provider_model(db)
    assert provider == "google"
    assert model == "gemini-2.5-flash-lite"


def test_resolve_provider_model_override_wins(tmp_path):
    db = tmp_path / "test.db"
    _seed_llm_db(db)
    provider, model = resolve_provider_model(
        db, provider_override="minimax-cn", model_override="MiniMax-M3"
    )
    assert provider == "minimax-cn"
    assert model == "MiniMax-M3"


def test_resolve_provider_model_partial_override(tmp_path):
    """One-side override fills the other side from DB."""
    db = tmp_path / "test.db"
    _seed_llm_db(db)
    provider, model = resolve_provider_model(db, provider_override="minimax-cn")
    assert provider == "minimax-cn"
    assert model == "gemini-2.5-flash-lite"


def test_resolve_provider_model_falls_back_on_missing_db(tmp_path):
    provider, model = resolve_provider_model(tmp_path / "nope.db")
    assert provider == "minimax-cn"
    assert model == "MiniMax-M3"


def test_resolve_provider_model_missing_tables_falls_back(tmp_path):
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    provider, model = resolve_provider_model(db)
    assert provider == "minimax-cn"
    assert model == "MiniMax-M3"


# ── A2: LLM failure detection ────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "API call failed: 401 unauthorized",
        "HTTP 404 Not Found",
        "HTTP 4xx — provider error",
        "HTTP 500 Internal Server Error",
        "HTTP 5xx upstream",
        "rate limit exceeded, retry later",
        "insufficient credits on this account",
        "your request requires available credits",
        "Error: API call failed after retries",
    ],
)
def test_detect_llm_failure_hits_keywords(text):
    assert detect_llm_failure(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "好的, 信息图已生成: https://v3b.fal.media/files/b/abc.png",
        "Here is your summary.",
        "",
        "HTTP is a protocol; 400 块钱手续费 (no API error)",
    ],
)
def test_detect_llm_failure_ok_output(text):
    assert detect_llm_failure(text) is None


class _FakePopen:
    """Minimal subprocess.Popen stand-in for run_hermes tests."""

    def __init__(self, stdout: str, returncode: int, stderr: str = ""):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self.called = False

    def communicate(self, timeout: float | None = None):
        self.called = True
        return self._stdout, self._stderr

    def kill(self):
        pass

    def wait(self):
        pass


def _patch_popen(
    monkeypatch, stdout: str, returncode: int, stderr: str = ""
) -> list[list[str]]:
    captured: list[list[str]] = []

    def fake_popen(argv, **kwargs):
        captured.append(argv)
        return _FakePopen(stdout, returncode, stderr)

    monkeypatch.setattr(worker_llm.subprocess, "Popen", fake_popen)
    return captured


def test_run_hermes_failure_keyword_returns_1(monkeypatch, tmp_path):
    db = tmp_path / "test.db"
    _seed_llm_db(db)
    argv = _patch_popen(
        monkeypatch,
        stdout="API call failed: 401 Unauthorized",
        returncode=0,
    )
    result = run_hermes("make a png", db_path=db, skill="baoyu-infographic")
    assert result["exit_code"] == 1
    assert "API call failed" in result["stdout"]
    # 显式 provider/model 必须出现在 argv (A1)
    assert "--provider" in argv[0]
    assert "google" in argv[0]
    assert "-m" in argv[0]
    assert "gemini-2.5-flash-lite" in argv[0]
    # v1.0.32: usage 字段默认 0
    assert result["input_tokens"] == 0
    assert result["output_tokens"] == 0
    assert result["cost_usd"] == 0.0


def test_run_hermes_normal_output_returns_0(monkeypatch, tmp_path):
    db = tmp_path / "test.db"
    _seed_llm_db(db)
    _patch_popen(
        monkeypatch,
        stdout="PNG done: https://v3b.fal.media/files/b/ok.png",
        returncode=0,
    )
    result = run_hermes("make a png", db_path=db)
    assert result["exit_code"] == 0
    assert "fal.media" in result["stdout"]


def test_run_hermes_nonzero_returns_rc(monkeypatch, tmp_path):
    db = tmp_path / "test.db"
    _seed_llm_db(db)
    _patch_popen(monkeypatch, stdout="boom", returncode=3)
    result = run_hermes("make a png", db_path=db)
    assert result["exit_code"] == 3
    assert "boom" in result["stdout"]


def test_run_hermes_prompt_stays_with_oneshot_flag(monkeypatch, tmp_path):
    """回归: v1.0.42 曾把 prompt 挪到 argv 末尾, -z 吃到 --usage-file,
    hermes argparse exit 2, 分享任务全部静默失败。"""
    db = tmp_path / "test.db"
    _seed_llm_db(db)
    usage = tmp_path / "usage.json"
    argv = _patch_popen(monkeypatch, stdout="ok", returncode=0)

    run_hermes("build landing", db_path=db, usage_file=usage)

    assert argv[0][:3] == ["hermes", "-z", "build landing"]
    assert argv[0][argv[0].index("--usage-file") + 1] == str(usage)


def test_run_hermes_surfaces_stderr_on_argparse_error(monkeypatch, tmp_path):
    """退出码非 0 且 stdout 为空时, 把 stderr 带出来, 不再落空 error。"""
    db = tmp_path / "test.db"
    _seed_llm_db(db)
    _patch_popen(
        monkeypatch,
        stdout="",
        returncode=2,
        stderr="hermes: error: argument -z/--oneshot: expected one argument\n",
    )

    result = run_hermes("build landing", db_path=db)

    assert result["exit_code"] == 2
    assert "expected one argument" in result["stdout"]


def test_run_hermes_override_in_argv(monkeypatch, tmp_path):
    db = tmp_path / "test.db"
    _seed_llm_db(db)
    argv = _patch_popen(
        monkeypatch,
        stdout="ok",
        returncode=0,
    )
    result = run_hermes(
        "make a png",
        db_path=db,
        provider_override="minimax-cn",
        model_override="MiniMax-M3",
    )
    assert result["exit_code"] == 0
    assert argv[0][argv[0].index("--provider") + 1] == "minimax-cn"
    assert argv[0][argv[0].index("-m") + 1] == "MiniMax-M3"


def test_run_hermes_profile_mode_omits_provider_and_model(monkeypatch, tmp_path):
    """llm_model_mode=profile: worker 跟随 moni profile 的默认模型."""
    db = tmp_path / "test.db"
    _seed_llm_db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT OR REPLACE INTO llm_settings (key, value) VALUES ('llm_model_mode', 'profile')"
    )
    conn.commit()
    conn.close()

    argv = _patch_popen(monkeypatch, stdout="ok", returncode=0)
    result = run_hermes("make a png", db_path=db)

    assert result["exit_code"] == 0
    assert "--provider" not in argv[0]
    assert "-m" not in argv[0]
    assert argv[0][argv[0].index("--profile") + 1] == "moni"


def test_run_hermes_profile_mode_keeps_explicit_override(monkeypatch, tmp_path):
    """profile 模式仍尊重 manifest/CLI 的显式覆盖."""
    db = tmp_path / "test.db"
    _seed_llm_db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT OR REPLACE INTO llm_settings (key, value) VALUES ('llm_model_mode', 'profile')"
    )
    conn.commit()
    conn.close()

    argv = _patch_popen(monkeypatch, stdout="ok", returncode=0)
    run_hermes(
        "make a png",
        db_path=db,
        provider_override="minimax-cn",
        model_override="MiniMax-M3",
    )

    assert argv[0][argv[0].index("--provider") + 1] == "minimax-cn"
    assert argv[0][argv[0].index("-m") + 1] == "MiniMax-M3"


# ── A3: remote PNG URL extraction + magic validation ─────────────────────


def test_extract_remote_png_urls_fal_media():
    out = (
        "已生成信息图 PNG: https://v3b.fal.media/files/b/abc-123.png "
        "(下载地址), 备选 http://cdn.example.com/x/y.png。"
    )
    urls = extract_remote_png_urls(out)
    assert urls == [
        "https://v3b.fal.media/files/b/abc-123.png",
        "http://cdn.example.com/x/y.png",
    ]


def test_extract_remote_png_urls_empty():
    assert extract_remote_png_urls("no png here") == []
    assert extract_remote_png_urls("/local/path/file.png") == []


def test_is_png_magic_bytes(tmp_path):
    good = tmp_path / "good.png"
    good.write_bytes(PNG_MAGIC + b"\x00\x00\x00\x0dIHDR")
    bad = tmp_path / "bad.png"
    bad.write_text("已生成信息图 PNG: 其实这是文本")
    assert is_png(good)
    assert not is_png(bad)
    assert not is_png(tmp_path / "missing.png")


def test_download_png_validates_magic(monkeypatch, tmp_path):
    import subprocess as sp

    dest = tmp_path / "dl.png"

    def fake_run_fail(*args, **kwargs):
        return sp.CompletedProcess(args[0], 22, stdout=b"", stderr=b"")

    monkeypatch.setattr(worker_llm.subprocess, "run", fake_run_fail)
    assert not download_png("https://v3b.fal.media/files/b/x.png", dest)
    assert not dest.exists()

    def fake_run_good(*args, **kwargs):
        # 写一个真实 PNG 到 curl 的 -o 目标, 模拟下载成功
        out_path = Path(args[0][args[0].index("-o") + 1])
        out_path.write_bytes(PNG_MAGIC + b"rest")
        return sp.CompletedProcess(args[0], 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(worker_llm.subprocess, "run", fake_run_good)
    assert download_png("https://v3b.fal.media/files/b/x.png", dest)
    assert is_png(dest)
