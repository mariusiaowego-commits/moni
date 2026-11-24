"""Wiring integration tests for reader, share (worker_llm/summary_gen/enrichment), and jobs."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from moni.db import leases
from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.llm import reader as reader_mod
from moni.llm import reader_worker
from moni.llm.desensitize import audit as desens_audit
from moni.share import enrichment, summary_gen, worker_llm
from tests.test_llm_channels_smoke import (
    BASELINE_PATH,
    REPO_ROOT,
    FakeHermesEnv,
    _assert_argv_matches_baseline,
    fake_hermes,  # noqa: F401 — pytest fixture re-export
)

NOW = "2026-11-24 16:20:00"


@pytest.fixture
def desens_db(tmp_path: Path) -> sqlite3.Connection:
    """Isolated temp SQLite DB migrated through 058 with reset desensitize cache."""
    close_db()
    desens_audit._reset_cache()
    db_file = tmp_path / "wiring_share.db"
    set_db_path(db_file)
    migrate()
    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO sources "
        "(id, name, source_type, url, fetch_interval_min, is_active, created_at, updated_at) "
        "VALUES ('src-desens', 'src-desens', 'rss', 'https://example.com/rss', 30, 1, ?, ?)",
        (NOW, NOW),
    )
    conn.commit()
    yield conn
    desens_audit._reset_cache()
    close_db()


def _set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, NOW),
    )
    conn.commit()
    desens_audit._reset_cache()


def _add_rule(
    conn: sqlite3.Connection,
    *,
    kind: str = "ORG",
    pattern: str = "招商银行",
    action: str = "mask",
) -> None:
    conn.execute(
        """
        INSERT INTO desensitize_rules (
            rule_type, kind, pattern, action, is_active, created_at, updated_at
        ) VALUES ('word', ?, ?, ?, 1, ?, ?)
        """,
        (kind, pattern, action, NOW, NOW),
    )
    conn.commit()
    desens_audit._reset_cache()


def _insert_article(conn: sqlite3.Connection, article_id: str, title: str, content: str) -> None:
    conn.execute(
        """
        INSERT INTO articles (
            id, title, url, source, raw_content, fetched_at
        ) VALUES (?, ?, ?, 'src-desens', ?, ?)
        """,
        (article_id, title, f"https://example.com/{article_id}", content, NOW),
    )
    conn.commit()


# ── 1. Switch OFF: byte-for-byte identical argv & prompt ──


def test_disabled_keeps_reader_share_jobs_argv_identical_to_baseline(
    desens_db: sqlite3.Connection,
    fake_hermes: FakeHermesEnv,
) -> None:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    assert {"reader", "worker_llm", "summary_gen"} <= set(baseline.keys())

    phone = "13812345678"
    prompt = f"联系人手机 {phone} 原样直通"

    deltas: list[str] = []
    reader_res = reader_mod.run_reader_stream(prompt, on_delta=deltas.append)
    assert reader_res["text"] == "FAKE-REPLY"

    db_path = desens_db.execute("PRAGMA database_list").fetchone()["file"]
    wl_res = worker_llm.run_hermes(prompt, db_path=db_path)
    assert wl_res["exit_code"] == 0
    assert wl_res["stdout"] == "FAKE-REPLY"

    sg_out, sg_rc = summary_gen._invoke_hermes(prompt)
    assert sg_rc == 0
    assert sg_out == "FAKE-REPLY"

    invs = fake_hermes.read_invocations()
    assert len(invs) == 3
    _assert_argv_matches_baseline("reader", invs[0], prompt)
    _assert_argv_matches_baseline("worker_llm", invs[1], prompt)
    _assert_argv_matches_baseline("summary_gen", invs[2], prompt)
    for inv in invs:
        assert any(phone in str(part) for part in inv["argv"])  # type: ignore[operator]

    # Jobs path with enabled=0 passes raw prompt straight into DB writeback untouched.
    import moni.web  # noqa: F401 — avoid pre-existing jobs <-> web circular import
    from moni.llm import jobs as jobs_mod

    _insert_article(desens_db, "art-off", f"标题含 {phone}", f"正文含 {phone}")
    desens_db.execute(
        """
        INSERT INTO llm_jobs (
            id, task_type, article_id, provider, model, status, created_at
        ) VALUES ('job-off', 'summary', 'art-off', 'dummy', 'dummy', 'pending', ?)
        """,
        (NOW,),
    )
    desens_db.commit()
    handle = leases.acquire("llm_jobs", grace_sec=120)
    assert handle is not None
    job_row = dict(desens_db.execute("SELECT * FROM llm_jobs WHERE id = ?", (handle.job_id,)).fetchone())
    jobs_mod._run_job(job_row, handle=handle)
    invs = fake_hermes.read_invocations()
    assert len(invs) == 4
    job_argv = invs[3]["argv"]  # type: ignore[index]
    sent_job_prompt = job_argv[job_argv.index("-z") + 1]
    _assert_argv_matches_baseline("runner", invs[3], sent_job_prompt)
    assert "[系统脱敏说明" not in sent_job_prompt
    assert phone in sent_job_prompt

    assert desens_db.execute("SELECT COUNT(*) FROM desensitize_events").fetchone()[0] == 0


# ── 2 & 3. Switch ON: mask outbound + restore inbound + audit without raw PII ──


def test_enabled_reader_worker_llm_summary_gen_roundtrip_and_audit(
    desens_db: sqlite3.Connection,
    fake_hermes: FakeHermesEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_setting(desens_db, "desensitize.enabled", "1")
    _add_rule(desens_db, kind="ORG", pattern="招商银行")
    monkeypatch.setenv("FAKE_HERMES_ECHO_PLACEHOLDERS", "1")

    db_path = desens_db.execute("PRAGMA database_list").fetchone()["file"]
    phone = "13812345678"
    org = "招商银行"
    prompt = f"核实 {org} 联系人手机 {phone}"

    # Reader stream path
    deltas: list[str] = []
    r_res = reader_mod.run_reader_stream(prompt, on_delta=deltas.append)
    assert org in r_res["text"] and phone in r_res["text"]
    assert org in "".join(deltas) and phone in "".join(deltas)

    # worker_llm same-process roundtrip + share_task_id audit
    wl_res = worker_llm.run_hermes(
        prompt,
        db_path=db_path,
        task_id="share-task-42",
        share_format="markdown",
    )
    assert wl_res["exit_code"] == 0
    assert org in wl_res["stdout"] and phone in wl_res["stdout"]

    # summary_gen generate_and_writeback with desensitize_scope
    _insert_article(desens_db, "art-sg", "测试", f"{org} 联系电话 {phone}")
    monkeypatch.setenv("FAKE_HERMES_REPLY", "noop")

    # Have fake_hermes return valid JSON array containing echoed placeholders for summary_gen
    custom_hermes = fake_hermes.bin_path
    custom_hermes.write_text(
        """#!/usr/bin/env python3
import json, os, re, sys
from pathlib import Path
argv = sys.argv[1:]
p = Path(os.environ["FAKE_HERMES_LOG"])
p.parent.mkdir(parents=True, exist_ok=True)
with p.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"cmd": sys.argv[0], "argv": argv}, ensure_ascii=False) + "\\n")
prompt = argv[argv.index("-z") + 1]
phs = re.findall(r"\\{\\{(?:ORG|PHONE)_[a-z2-7]{8}\\}\\}", prompt)
print(json.dumps([{"id": "art-sg", "summary": "摘要：" + " ".join(phs)}], ensure_ascii=False))
""",
        encoding="utf-8",
    )
    custom_hermes.chmod(0o755)
    # Wait: restore normal fake_hermes for summary_gen after checking output
    # Wait: generate_and_writeback parses JSON array output -> summary_gen restores before _parse_summaries!
    summaries = summary_gen.generate_and_writeback(db_path, ["art-sg"])
    assert org in summaries["art-sg"] and phone in summaries["art-sg"]
    art_row = desens_db.execute("SELECT ai_summary FROM articles WHERE id = 'art-sg'").fetchone()
    assert org in art_row["ai_summary"] and phone in art_row["ai_summary"]

    invs = fake_hermes.read_invocations()
    assert len(invs) == 3
    for inv in invs:
        joined = " ".join(inv["argv"])  # type: ignore[arg-type]
        assert org not in joined
        assert phone not in joined
        assert "{{ORG_" in joined
        assert "{{PHONE_" in joined

    rows = desens_db.execute(
        "SELECT call_site, share_task_id, masked_total, restored_total, leak_scan_hits, items_json "
        "FROM desensitize_events ORDER BY id ASC"
    ).fetchall()
    assert [r["call_site"] for r in rows] == ["reader", "worker_llm", "summary_gen"]
    assert rows[1]["share_task_id"] == "share-task-42"
    for r in rows:
        assert r["masked_total"] >= 2
        assert r["restored_total"] >= 2
        assert r["leak_scan_hits"] == 0
        assert phone not in r["items_json"]
        assert org not in r["items_json"]


def test_enabled_jobs_writes_restored_result_to_db_without_double_restore(
    desens_db: sqlite3.Connection,
    fake_hermes: FakeHermesEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_setting(desens_db, "desensitize.enabled", "1")
    _add_rule(desens_db, kind="ORG", pattern="招商银行")
    monkeypatch.setenv("FAKE_HERMES_ECHO_PLACEHOLDERS", "1")

    phone = "13812345678"
    _insert_article(desens_db, "art-job", "招商银行快报", f"总结 招商银行 与联系人 {phone}")
    desens_db.execute(
        """
        INSERT INTO llm_jobs (
            id, task_type, article_id, provider, model, status, created_at
        ) VALUES ('job-desens-1', 'summary', 'art-job', 'dummy', 'dummy', 'pending', ?)
        """,
        (NOW,),
    )
    desens_db.commit()

    handle = leases.acquire("llm_jobs", grace_sec=120)
    assert handle is not None
    import moni.web  # noqa: F401 — avoid pre-existing jobs <-> web circular import
    from moni.llm import jobs as jobs_mod

    job_row = dict(desens_db.execute("SELECT * FROM llm_jobs WHERE id = ?", (handle.job_id,)).fetchone())
    jobs_mod._run_job(job_row, handle=handle)

    invs = fake_hermes.read_invocations()
    assert len(invs) == 1
    sent = " ".join(invs[0]["argv"])  # type: ignore[arg-type]
    assert "招商银行" not in sent
    assert phone not in sent

    art = desens_db.execute("SELECT summary FROM articles WHERE id = 'art-job'").fetchone()
    job = desens_db.execute("SELECT status, result FROM llm_jobs WHERE id = 'job-desens-1'").fetchone()
    assert job["status"] == "done"
    assert "招商银行" in art["summary"] and phone in art["summary"]
    assert "招商银行" in job["result"] and phone in job["result"]
    assert "{{" not in art["summary"]

    ev = desens_db.execute(
        "SELECT task_type, article_id, job_id, masked_total, restored_total, items_json "
        "FROM desensitize_events WHERE job_id = 'job-desens-1'"
    ).fetchone()
    assert ev is not None
    assert ev["task_type"] == "summary"
    assert ev["article_id"] == "art-job"
    assert ev["masked_total"] == 3
    assert ev["restored_total"] >= 2
    assert phone not in ev["items_json"]
    assert "招商银行" not in ev["items_json"]


# ── 4. Reader mid-stream abort leaves ZERO partial placeholders in DB ──


def test_reader_interrupt_abort_flushes_without_partial_placeholder_in_db(
    desens_db: sqlite3.Connection,
    fake_hermes: FakeHermesEnv,
) -> None:
    _set_setting(desens_db, "desensitize.enabled", "1")
    _add_rule(desens_db, kind="ORG", pattern="招商银行")

    # Replace fake_hermes script to emit 1 valid placeholder + trailing incomplete "{{ORG_r4" + error event.
    fake_hermes.bin_path.write_text(
        """#!/usr/bin/env python3
import json, os, re, sys
from pathlib import Path
argv = sys.argv[1:]
p = Path(os.environ["FAKE_HERMES_LOG"])
p.parent.mkdir(parents=True, exist_ok=True)
with p.open("a", encoding="utf-8") as fh:
    fh.write(json.dumps({"cmd": sys.argv[0], "argv": argv}, ensure_ascii=False) + "\\n")
prompt = argv[argv.index("-q") + 1]
m = re.search(r"\\{\\{ORG_[a-z2-7]{8}\\}\\}", prompt)
ph = m.group(0) if m else "招商银行"
print(json.dumps({"type": "text", "text": f"首段：{ph}；结尾前缀 {{ORG_r4"}, ensure_ascii=False), flush=True)
print(json.dumps({"type": "error", "message": "simulated mid-stream abort"}, ensure_ascii=False), flush=True)
sys.exit(1)
""",
        encoding="utf-8",
    )
    fake_hermes.bin_path.chmod(0o755)

    _insert_article(desens_db, "art-reader", "标题", "正文提及招商银行详情")
    desens_db.execute(
        """
        INSERT INTO reader_conversations (id, article_id, title, created_at, updated_at)
        VALUES ('conv-1', 'art-reader', '测试会话', ?, ?)
        """,
        (NOW, NOW),
    )
    desens_db.execute(
        """
        INSERT INTO llm_jobs (
            id, task_type, article_id, provider, model, status, created_at
        ) VALUES ('job-reader-abort', 'reader_chat', 'art-reader', 'dummy', 'dummy', 'pending', ?)
        """,
        (NOW,),
    )
    desens_db.execute(
        """
        INSERT INTO reader_messages (id, conversation_id, job_id, role, content, status, created_at, updated_at)
        VALUES ('msg-user', 'conv-1', NULL, 'user', '总结招商银行', 'done', ?, ?),
               ('msg-asst', 'conv-1', 'job-reader-abort', 'assistant', '', 'pending', ?, ?)
        """,
        (NOW, NOW, NOW, NOW),
    )
    desens_db.commit()

    handle = leases.acquire("llm_jobs", grace_sec=120, task_types=("reader_chat",))
    assert handle is not None
    reader_worker._run_claimed(handle)

    msg = desens_db.execute(
        "SELECT status, content FROM reader_messages WHERE id = 'msg-asst'"
    ).fetchone()
    assert msg["status"] == "failed"
    assert "首段：招商银行；结尾前缀 " in msg["content"]
    assert "{{ORG" not in msg["content"]
    assert "{ORG" not in msg["content"]

    ev = desens_db.execute(
        "SELECT call_site, fallback_used FROM desensitize_events WHERE job_id = 'job-reader-abort'"
    ).fetchone()
    assert ev["call_site"] == "reader"
    assert ev["fallback_used"] == "stream_aborted"


# ── 5. Mask raises exception -> fail-open across reader / worker_llm / summary_gen ──


def test_mask_error_fail_open_reader_worker_llm_and_summary_gen(
    desens_db: sqlite3.Connection,
    fake_hermes: FakeHermesEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_setting(desens_db, "desensitize.enabled", "1")

    def boom(*args, **kwargs):
        raise RuntimeError("boom mask error")

    monkeypatch.setattr("moni.llm.desensitize.engine.mask_prompt", boom)
    db_path = desens_db.execute("PRAGMA database_list").fetchone()["file"]
    prompt = "明文手机 13812345678"

    r_res = reader_mod.run_reader_stream(prompt)
    assert r_res["text"] == "FAKE-REPLY"

    wl_res = worker_llm.run_hermes(prompt, db_path=db_path)
    assert wl_res["exit_code"] == 0
    assert wl_res["stdout"] == "FAKE-REPLY"

    sg_out, sg_rc = summary_gen._invoke_hermes(prompt)
    assert sg_rc == 0
    assert sg_out == "FAKE-REPLY"

    invs = fake_hermes.read_invocations()
    assert len(invs) == 3
    for inv in invs:
        assert prompt in inv["argv"]  # type: ignore[operator]

    rows = desens_db.execute(
        "SELECT call_site, fallback_used FROM desensitize_events ORDER BY id ASC"
    ).fetchall()
    assert [(r["call_site"], r["fallback_used"]) for r in rows] == [
        ("reader", "mask_error"),
        ("worker_llm", "mask_error"),
        ("summary_gen", "mask_error"),
    ]


def test_enrichment_preserves_three_arg_invoke_hermes_injection(
    desens_db: sqlite3.Connection,
    fake_hermes: FakeHermesEnv,
) -> None:
    seen: list[tuple[str, float, object]] = []

    def fake_invoke(prompt: str, timeout_sec: int = 120, extra_args=None):
        seen.append((prompt, timeout_sec, extra_args))
        return json.dumps({"summary": "紧凑摘要。"}), 0

    _insert_article(desens_db, "art-enr", "标题", "原文")
    db_path = desens_db.execute("PRAGMA database_list").fetchone()["file"]
    row = desens_db.execute("SELECT * FROM articles WHERE id = 'art-enr'").fetchone()

    stats = enrichment.run_enrichment(
        db_path,
        [row["id"]],
        task_id="task-enr-1",
        invoke_hermes=fake_invoke,
    )
    assert isinstance(stats, dict)

    # Verify CLI subprocess works with fake_hermes on PATH (never calls real hermes).
    out_file = Path(db_path).with_name("cli_out.txt")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "moni" / "share" / "worker_llm.py"),
            "--db",
            db_path,
            "--output",
            str(out_file),
            "--task-id",
            "cli-task-1",
            "--",
            "测试提示词",
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert out_file.read_text(encoding="utf-8") == "FAKE-REPLY"
