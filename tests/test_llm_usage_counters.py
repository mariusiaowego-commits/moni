"""tests/test_llm_usage_counters.py — v1.0.32 sprint-13 Q4 LLM 用量统计.

Covers:
- migration 029 adds input_tokens / output_tokens / cost_usd to llm_jobs
- worker_llm.parse_usage_file() parses hermes --usage-file JSON
- call_llm() passes --usage-file and returns (text, usage)
- jobs._run_job writes usage counters back to llm_jobs
- GET /api/stats/llm-usage aggregates totals + by_model
- 看板前端 stats.js 有 LLM 用量模块标记 (loadLlmUsage / llmUsage state)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import _CURRENT_VERSION, current_version, migrate

# v1.0.22 导入顺序: routes 包必须先于 moni.llm.jobs 加载 —— jobs.py 顶部
# re-export DOMAIN_WHITELIST / is_in_whitelist (来自 routes.fact_check_plus);
# 若先 import jobs, routes/__init__ -> llm_jobs -> jobs 形成循环 import.
import moni.web.routes.fact_check_plus  # noqa: F401,E402

from moni.llm import jobs as llm_jobs
from moni.llm.client import call_llm
from moni.share import worker_llm
from moni.stats.repository import llm_usage_layer_stats
from moni.web.server import create_app


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """每个测试都用干净的临时 DB + 完整 migration."""
    db_path = tmp_path / "test.db"
    set_db_path(db_path)
    migrate()
    yield
    close_db()
    import moni.db.connection as conn_mod
    import moni.db.migrate as mig_mod
    conn_mod._CON = None
    conn_mod._DB_PATH = None
    mig_mod._CON = None


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app)


def _iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).isoformat()


def _seed_llm_jobs() -> None:
    """Seed 2 models × 2 rows with usage counters (one old row w/o counters)."""
    conn = get_db()
    now = datetime.now(timezone.utc)
    for i in range(2):
        conn.execute(
            "INSERT INTO articles (id, title, url, source, fetched_at, raw_content) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"a{i}", f"article {i}", f"https://x/{i}", "s", _iso(now), "content"),
        )
    rows = [
        # (id, model, provider, started_at, input, output, cost)
        ("j1", "MiniMax-M3", "minimax-cn", now - timedelta(days=1), 1000, 200, 0.01),
        ("j2", "MiniMax-M3", "minimax-cn", now - timedelta(days=2), 2000, 400, 0.02),
        ("j3", "gemini-2.5-flash-lite", "google", now - timedelta(days=3), 500, 50, 0.001),
        # 80 天前: 30d 窗外但 90d 窗内 (不用恰好 90 天, 避免与 period_start
        # 重新取 now 的微秒级竞态)
        ("j4", "MiniMax-M3", "minimax-cn", now - timedelta(days=80), 999, 999, 0.99),
    ]
    for job_id, model, provider, started_at, inp, out, cost in rows:
        conn.execute(
            """
            INSERT INTO llm_jobs
                (id, task_type, article_id, provider, model, status, attempts,
                 created_at, started_at, finished_at,
                 input_tokens, output_tokens, cost_usd)
            VALUES (?, 'summary', 'a0', ?, ?, 'done', 1, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id, provider, model,
                _iso(now), _iso(started_at), _iso(started_at),
                inp, out, cost,
            ),
        )
    # 旧行: 无 usage 列值 (NULL) — COALESCE 必须处理
    conn.execute(
        """
        INSERT INTO llm_jobs
            (id, task_type, article_id, provider, model, status, attempts,
             created_at, started_at, finished_at)
        VALUES ('j-old', 'summary', 'a0', 'minimax-cn', 'MiniMax-M3', 'done', 1, ?, ?, ?)
        """,
        (_iso(now), _iso(now - timedelta(days=1)), _iso(now - timedelta(days=1))),
    )
    conn.commit()


# ── migration 029 ─────────────────────────────────────────────────────────


def test_migration_029_adds_usage_columns(fresh_db):
    assert current_version(get_db()) == _CURRENT_VERSION
    cols = {
        r["name"]
        for r in get_db().execute("PRAGMA table_info(llm_jobs)").fetchall()
    }
    assert "input_tokens" in cols
    assert "output_tokens" in cols
    assert "cost_usd" in cols
    assert _CURRENT_VERSION == 44  # 43 from v1.0.88 on; 44 from v1.0.94


# ── worker_llm.parse_usage_file ───────────────────────────────────────────


def test_parse_usage_file_ok(tmp_path):
    uf = tmp_path / "usage.json"
    uf.write_text(
        json.dumps(
            {
                "estimated_cost_usd": 0.0123,
                "input_tokens": 32740,
                "output_tokens": 26,
                "total_tokens": 32894,
                "api_calls": 1,
                "model": "MiniMax-M3",
                "failed": False,
            }
        )
    )
    usage = worker_llm.parse_usage_file(uf)
    assert usage["input_tokens"] == 32740
    assert usage["output_tokens"] == 26
    assert usage["cost_usd"] == 0.0123
    assert usage["raw"]["model"] == "MiniMax-M3"


def test_parse_usage_file_failure_and_missing(tmp_path):
    # 失败报告: 字段为 null → 0
    uf = tmp_path / "usage.json"
    uf.write_text(json.dumps({"failed": True, "input_tokens": None, "estimated_cost_usd": None}))
    usage = worker_llm.parse_usage_file(uf)
    assert usage["input_tokens"] == 0
    assert usage["output_tokens"] == 0
    assert usage["cost_usd"] == 0.0
    # 文件不存在 → 0
    assert worker_llm.parse_usage_file(tmp_path / "nope.json")["input_tokens"] == 0
    assert worker_llm.parse_usage_file(None)["input_tokens"] == 0
    # 坏 JSON → 0
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert worker_llm.parse_usage_file(bad)["input_tokens"] == 0


def test_run_hermes_usage_file_in_argv(monkeypatch, tmp_path):
    """run_hermes 把 --usage-file 拼进 argv (生图任务记账).

    v1.0.42: run_hermes routes through HardenedSubprocess. We patch the
    runner's _invoke to capture argv shape and return a fake success.
    """
    uf = tmp_path / "usage.json"
    captured: list[list[str]] = []

    from moni.llm import runner as runner_mod

    def fake_run(self):
        captured.append(list(self.cmd))
        return runner_mod.SubprocessResult(
            stdout="ok", stderr="", returncode=0, duration_ms=10,
        )

    monkeypatch.setattr(runner_mod.HardenedSubprocess, "run", fake_run)
    result = worker_llm.run_hermes("make png", usage_file=uf)
    assert result["exit_code"] == 0
    assert "--usage-file" in captured[0]
    assert str(uf) in captured[0]
    # usage 文件还没写 → 0
    assert result["input_tokens"] == 0


# ── call_llm usage ────────────────────────────────────────────────────────


def test_call_llm_returns_usage(monkeypatch, tmp_path):
    """call_llm 传 usage_file 时返回 (text, usage); argv 含 --usage-file.

    v1.0.42: call_llm routes through FailoverRunner.call(), which calls
    HardenedSubprocess.run(). We monkeypatch the runner to capture argv
    and return a fake CompletedProcess.
    """
    uf = tmp_path / "usage.json"
    captured: dict = {}

    def fake_invoke(self, req, model_id):
        # Build the same argv shape we used to capture via subprocess.run
        captured["cmd"] = [
            "hermes", "-z", req.prompt,
            "--provider", req.provider, "-m", model_id,
            *(req.extra_args or []),
            *(["--usage-file", req.usage_file] if req.usage_file else []),
        ]
        # Write a fake usage file so the runner picks it up.
        import json as _json
        uf.write_text(_json.dumps(
            {"estimated_cost_usd": 0.005, "input_tokens": 111, "output_tokens": 22}
        ))
        from moni.llm.runner import SubprocessResult
        return SubprocessResult(
            stdout="session_id: s\nhello\n",
            stderr="", returncode=0, duration_ms=10,
            input_tokens=111, output_tokens=22, cost_usd=0.005,
        )

    from moni.llm import runner as runner_mod
    monkeypatch.setattr(runner_mod.FailoverRunner, "_invoke", fake_invoke)
    text, usage = call_llm(
        "minimax-cn", "MiniMax-M3", "hi",
        usage_file=str(uf), usage=True,
    )
    assert text == "hello"
    assert usage["input_tokens"] == 111
    assert usage["output_tokens"] == 22
    assert usage["cost_usd"] == 0.005
    assert "--usage-file" in captured["cmd"]
    assert str(uf) in captured["cmd"]


def test_call_llm_without_usage_file_still_returns_text(monkeypatch):
    """不带 usage_file 的的老调用方式: 返回 (text, usage-all-zero)."""

    def fake_invoke(self, req, model_id):
        from moni.llm.runner import SubprocessResult
        return SubprocessResult(
            stdout="session_id: s\nhi there\n", stderr="",
            returncode=0, duration_ms=10,
        )

    from moni.llm import runner as runner_mod
    monkeypatch.setattr(runner_mod.FailoverRunner, "_invoke", fake_invoke)
    text, usage = call_llm("minimax-cn", "MiniMax-M3", "hi", usage=True)
    assert text == "hi there"
    assert usage["input_tokens"] == 0
    assert usage["output_tokens"] == 0
    assert usage["cost_usd"] == 0.0


def test_call_llm_sets_hermes_profile_moni(monkeypatch):
    """moni 项目固定使用 moni profile。

    v1.0.42: HardenedSubprocess sets HERMES_PROFILE on the env
    passed to Popen. We verify by capturing env on Popen.
    """
    import subprocess as sp
    captured: dict = {}

    real_popen = sp.Popen

    def fake_popen(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        # Need a fast exit. Use echo.
        if "--usage-file" in cmd:
            cmd = [c for c in cmd if c != "--usage-file"]
        # Just run /bin/echo with the first non-flag arg.
        prompt_idx = next(i for i, c in enumerate(cmd) if not c.startswith("-") and c != "hermes" and i > 0)
        return real_popen(["/bin/echo", "ok"], stdout=sp.PIPE, stderr=sp.PIPE, text=True, env=kwargs.get("env"))

    monkeypatch.setattr(sp, "Popen", fake_popen)
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    call_llm("ark", "ark-code-latest", "hi")
    assert captured["env"] is not None
    assert captured["env"].get("HERMES_PROFILE") == "moni"


def test_call_llm_profile_mode_omits_provider_and_model(monkeypatch):
    """llm_model_mode=profile: 不传 --provider/-m，跟随 profile 默认模型."""
    import subprocess as sp
    captured: dict = {}

    real_popen = sp.Popen

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return real_popen(
            ["/bin/echo", "ok"],
            stdout=sp.PIPE,
            stderr=sp.PIPE,
            text=True,
            env=kwargs.get("env"),
        )

    get_db().execute(
        "INSERT OR REPLACE INTO llm_settings (key, value) VALUES ('llm_model_mode', 'profile')"
    )
    get_db().commit()
    monkeypatch.setattr(sp, "Popen", fake_popen)
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    call_llm("ark", "ark-code-latest", "hi")

    assert "--provider" not in captured["cmd"]
    assert "-m" not in captured["cmd"]
    assert captured["cmd"][0] == "hermes"


def test_call_llm_preserves_existing_hermes_profile(monkeypatch):
    """setdefault 语义: 若环境已有 HERMES_PROFILE, 不被覆盖."""
    import subprocess as sp
    captured: dict = {}

    real_popen = sp.Popen

    def fake_popen(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return real_popen(["/bin/echo", "ok"], stdout=sp.PIPE, stderr=sp.PIPE, text=True, env=kwargs.get("env"))

    monkeypatch.setattr(sp, "Popen", fake_popen)
    monkeypatch.setenv("HERMES_PROFILE", "work")
    call_llm("ark", "ark-code-latest", "hi")
    assert captured["env"]["HERMES_PROFILE"] == "work"


# ── jobs._run_job 写回 ────────────────────────────────────────────────────


def test_jobs_writes_usage_to_llm_jobs(monkeypatch):
    """_run_job 成功后 input/output/cost 写回 llm_jobs."""
    conn = get_db()
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at, raw_content) "
        "VALUES ('art-usage', 't', 'u', 's', ?, 'c')",
        (_iso(),),
    )
    conn.execute(
        """
        INSERT INTO llm_jobs
            (id, task_type, article_id, provider, model, status, attempts, created_at)
        VALUES ('job-usage', 'summary', 'art-usage', 'minimax-cn', 'MiniMax-M3', 'pending', 0, ?)
        """,
        (_iso(),),
    )
    conn.commit()

    def fake_call_llm(provider, model, prompt, *, extra_args=None, usage_file=None, usage=False):
        assert usage_file, "jobs._run_job must pass usage_file"
        assert usage is True, "jobs._run_job must request usage tuple"
        # 模拟 hermes 写了 usage 文件
        with open(usage_file, "w", encoding="utf-8") as fh:
            json.dump(
                {"estimated_cost_usd": 0.03, "input_tokens": 3000, "output_tokens": 150},
                fh,
            )
        return "summary text", {
            "input_tokens": 3000,
            "output_tokens": 150,
            "cost_usd": 0.03,
            "raw": {},
        }

    monkeypatch.setattr(llm_jobs, "call_llm", fake_call_llm)
    llm_jobs._run_job(
        {
            "id": "job-usage",
            "task_type": "summary",
            "article_id": "art-usage",
            "provider": "minimax-cn",
            "model": "MiniMax-M3",
            "attempts": 0,
        }
    )
    row = conn.execute(
        "SELECT status, input_tokens, output_tokens, cost_usd FROM llm_jobs WHERE id='job-usage'"
    ).fetchone()
    assert row["status"] == "done"
    assert row["input_tokens"] == 3000
    assert row["output_tokens"] == 150
    assert row["cost_usd"] == 0.03


# ── stats API ─────────────────────────────────────────────────────────────


def test_llm_usage_api_aggregates(client):
    _seed_llm_jobs()
    r = client.get("/api/stats/llm-usage?period=30d")
    assert r.status_code == 200
    body = r.json()
    assert body["module"] == "llm_usage"
    assert body["period"] == "30d"

    totals = body["totals"]
    # j1+j2+j3+j-old (j4 在 30d 外; j-old 无 usage 列 → COALESCE 0)
    assert totals["calls"] == 4
    assert totals["input_tokens"] == 1000 + 2000 + 500 + 0
    assert totals["output_tokens"] == 200 + 400 + 50 + 0
    assert totals["cost_usd"] == pytest.approx(0.01 + 0.02 + 0.001 + 0.0)

    by_model = {m["model"]: m for m in body["by_model"]}
    assert by_model["MiniMax-M3"]["calls"] == 3
    assert by_model["MiniMax-M3"]["input_tokens"] == 3000
    assert by_model["MiniMax-M3"]["cost_usd"] == pytest.approx(0.03)
    assert by_model["gemini-2.5-flash-lite"]["calls"] == 1


def test_llm_usage_api_invalid_period_falls_back(client):
    _seed_llm_jobs()
    r = client.get("/api/stats/llm-usage?period=whatever")
    assert r.status_code == 200
    assert r.json()["period"] == "30d"


def test_llm_usage_api_empty(client):
    r = client.get("/api/stats/llm-usage?period=30d")
    body = r.json()
    assert body["totals"]["calls"] == 0
    assert body["by_model"] == []


def test_llm_usage_layer_stats_90d(client):
    _seed_llm_jobs()
    body = llm_usage_layer_stats("90d")
    # j4 在 90d 窗内 → 全部 5 行 (j1+j2+j3+j4+j-old)
    assert body["totals"]["calls"] == 5
    assert body["totals"]["input_tokens"] == 1000 + 2000 + 500 + 999


# ── 看板前端 ──────────────────────────────────────────────────────────────


def test_stats_js_has_llm_usage_module():
    """stats.js 必须有 loadLlmUsage / llmUsage state (渲染入口)."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "moni" / "web" / "static" / "js" / "stats.js"
    text = src.read_text(encoding="utf-8")
    assert "loadLlmUsage" in text
    assert "/api/stats/llm-usage" in text
    assert "llmUsage" in text
    assert "costFmt" in text


def test_index_html_has_llm_usage_module():
    """index.html 渲染 LLM 用量卡片 + by_model 表格."""
    from pathlib import Path

    html = Path(__file__).resolve().parents[1] / "moni" / "web" / "static" / "index.html"
    text = html.read_text(encoding="utf-8")
    assert 'id="stats-llm-usage"' in text
    assert "LLM 用量" in text
    assert "stats-usage-table" in text
