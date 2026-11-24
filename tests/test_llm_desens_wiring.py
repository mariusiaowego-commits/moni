"""Wiring integration tests for FailoverRunner and run_hermes_stream with desensitization."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.llm import stream as stream_mod
from moni.llm.desensitize import audit as desens_audit
from moni.llm.runner import CallRequest, FailoverRunner, LLMRunnerError, SubprocessResult
from tests.test_llm_channels_smoke import (
    BASELINE_PATH,
    FakeHermesEnv,
    _assert_argv_matches_baseline,
    fake_hermes,  # noqa: F401 — pytest fixture re-export
)

NOW = "2026-11-24 16:00:00"


@pytest.fixture
def desens_db(tmp_path: Path) -> sqlite3.Connection:
    """Isolated temp SQLite DB migrated through 058 with clean settings cache."""
    close_db()
    desens_audit._reset_cache()
    set_db_path(tmp_path / "wiring.db")
    migrate()
    conn = get_db()
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
    rule_type: str = "word",
    kind: str = "ORG",
    pattern: str,
    action: str = "mask",
) -> None:
    conn.execute(
        """
        INSERT INTO desensitize_rules (
            rule_type, kind, pattern, action, is_active, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 1, ?, ?)
        """,
        (rule_type, kind, pattern, action, NOW, NOW),
    )
    conn.commit()
    desens_audit._reset_cache()


def test_wiring_disabled_preserves_baseline_argv_and_raw_phone(
    desens_db: sqlite3.Connection,
    fake_hermes: FakeHermesEnv,
) -> None:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    assert set(baseline.keys()) >= {"runner", "stream"}

    phone = "13812345678"
    prompt = f"联系电话 {phone} 请核实"

    runner = FailoverRunner.from_settings(fallback_chain=[("dummy", "dummy")])
    res_runner = runner.call(CallRequest(provider="dummy", model_id="dummy", prompt=prompt))
    assert res_runner.reply_text == "FAKE-REPLY"

    res_stream = stream_mod.run_hermes_stream(prompt)
    assert res_stream.text == "FAKE-REPLY"

    invs = fake_hermes.read_invocations()
    assert len(invs) == 2
    _assert_argv_matches_baseline("runner", invs[0], prompt)
    _assert_argv_matches_baseline("stream", invs[1], prompt)
    assert phone in " ".join(invs[0]["argv"])  # type: ignore[arg-type]
    assert phone in " ".join(invs[1]["argv"])  # type: ignore[arg-type]

    events = desens_db.execute("SELECT COUNT(*) FROM desensitize_events").fetchone()[0]
    assert events == 0


def test_wiring_enabled_masks_outbound_and_restores_roundtrip(
    desens_db: sqlite3.Connection,
    fake_hermes: FakeHermesEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_setting(desens_db, "desensitize.enabled", "1")
    monkeypatch.setenv("FAKE_HERMES_ECHO_PLACEHOLDERS", "1")

    phone = "13812345678"
    prompt = f"联系人手机 {phone} 需要核查"

    runner = FailoverRunner.from_settings(fallback_chain=[("dummy", "dummy")])
    res_runner = runner.call(CallRequest(provider="dummy", model_id="dummy", prompt=prompt))
    assert phone in res_runner.reply_text

    res_stream = stream_mod.run_hermes_stream(prompt)
    assert phone in res_stream.text

    invs = fake_hermes.read_invocations()
    assert len(invs) == 2
    for inv in invs:
        joined = " ".join(inv["argv"])  # type: ignore[arg-type]
        assert phone not in joined
        assert "{{PHONE_" in joined


def test_wiring_enabled_runner_json_output_roundtrip(
    desens_db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_setting(desens_db, "desensitize.enabled", "1")
    _add_rule(desens_db, kind="ORG", pattern="招商银行")

    seen_prompts: list[str] = []

    def fake_invoke(self, req: CallRequest, model_id: str) -> SubprocessResult:
        seen_prompts.append(req.prompt)
        # Echo placeholder inside JSON key/value pair.
        import re

        m = re.search(r"\{\{ORG_[a-z2-7]{8}\}\}", req.prompt)
        assert m is not None
        ph = m.group(0)
        return SubprocessResult(
            stdout=json.dumps({"org": ph}, ensure_ascii=False),
            stderr="",
            returncode=0,
            duration_ms=5,
        )

    monkeypatch.setattr(FailoverRunner, "_invoke", fake_invoke)
    runner = FailoverRunner.from_settings(fallback_chain=[("dummy", "dummy")])
    res = runner.call(
        CallRequest(
            provider="dummy",
            model_id="dummy",
            prompt="分析招商银行表现",
            task_type="summary",
            json_output=True,
        )
    )
    assert json.loads(res.reply_text) == {"org": "招商银行"}
    assert "招商银行" not in seen_prompts[0]


def test_wiring_mask_error_fail_open_and_strict_fail_closed(
    desens_db: sqlite3.Connection,
    fake_hermes: FakeHermesEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_setting(desens_db, "desensitize.enabled", "1")

    def boom(*args, **kwargs):
        raise RuntimeError("simulated mask failure")

    monkeypatch.setattr("moni.llm.desensitize.engine.mask_prompt", boom)
    prompt = "明文联系人 13812345678"

    runner = FailoverRunner.from_settings(fallback_chain=[("dummy", "dummy")])
    res_runner = runner.call(CallRequest(provider="dummy", model_id="dummy", prompt=prompt))
    assert res_runner.reply_text == "FAKE-REPLY"

    res_stream = stream_mod.run_hermes_stream(prompt)
    assert res_stream.text == "FAKE-REPLY"

    invs = fake_hermes.read_invocations()
    assert len(invs) == 2
    assert prompt in invs[0]["argv"]  # type: ignore[arg-type]
    assert prompt in invs[1]["argv"]  # type: ignore[arg-type]

    rows = desens_db.execute(
        "SELECT call_site, fallback_used FROM desensitize_events ORDER BY id ASC"
    ).fetchall()
    assert [(r["call_site"], r["fallback_used"]) for r in rows] == [
        ("runner", "mask_error"),
        ("stream", "mask_error"),
    ]

    # strict_fail_closed=1 must raise rather than sending raw prompt.
    _set_setting(desens_db, "desensitize.strict_fail_closed", "1")
    with pytest.raises(RuntimeError, match="simulated mask failure"):
        runner.call(CallRequest(provider="dummy", model_id="dummy", prompt=prompt))
    assert len(fake_hermes.read_invocations()) == 2


def test_wiring_blocked_skips_hermes_and_preserves_breaker(
    desens_db: sqlite3.Connection,
    fake_hermes: FakeHermesEnv,
) -> None:
    _set_setting(desens_db, "desensitize.enabled", "1")
    _add_rule(desens_db, kind="SECRET_WORD", pattern="绝密词XYZ", action="block")

    runner = FailoverRunner.from_settings(
        fallback_chain=[("p1", "m1"), ("p2", "m2")],
        failure_threshold=1,
    )
    with pytest.raises(LLMRunnerError, match="desensitize blocked"):
        runner.call(CallRequest(provider="p1", model_id="m1", prompt="请输出 绝密词XYZ"))

    with pytest.raises(RuntimeError, match="desensitize blocked"):
        stream_mod.run_hermes_stream("请输出 绝密词XYZ")

    assert fake_hermes.read_invocations() == []
    assert all(b.failure_count == 0 and b.state == "CLOSED" for b in runner.breakers.values())

    rows = desens_db.execute(
        "SELECT call_site, blocked FROM desensitize_events ORDER BY id ASC"
    ).fetchall()
    assert [(r["call_site"], r["blocked"]) for r in rows] == [
        ("runner", 1),
        ("stream", 1),
    ]


def test_wiring_audit_event_fields_failover_once_and_truncation(
    desens_db: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_setting(desens_db, "desensitize.enabled", "1")
    _add_rule(desens_db, kind="ORG", pattern="招商银行")

    attempts: list[str] = []

    def flaky_invoke(self, req: CallRequest, model_id: str) -> SubprocessResult:
        attempts.append(model_id)
        if model_id == "m1":
            raise LLMRunnerError("429 rate limited")
        import re

        hits = re.findall(r"\{\{(?:ORG|PHONE)_[a-z2-7]{8}\}\}", req.prompt)
        return SubprocessResult(
            stdout=" ".join(hits),
            stderr="",
            returncode=0,
            duration_ms=8,
        )

    monkeypatch.setattr(FailoverRunner, "_invoke", flaky_invoke)
    runner = FailoverRunner.from_settings(fallback_chain=[("p1", "m1"), ("p2", "m2")])
    phone = "13812345678"
    token_val = "tok_demo_secret_987654"
    url = f"https://rss.example.cn/all?token={token_val}"
    res = runner.call(
        CallRequest(
            provider="p1",
            model_id="m1",
            prompt=f"招商银行联系人 {phone} 与地址 {url}",
            job_id="job-1",
            article_id="art-1",
        )
    )
    assert attempts == ["m1", "m2"]
    assert "招商银行" in res.reply_text
    assert phone in res.reply_text

    rows = desens_db.execute("SELECT * FROM desensitize_events").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["masked_total"] == 3
    assert row["restored_total"] == 2
    assert row["leak_scan_hits"] == 0
    assert row["model"] == "m2"
    assert phone not in row["items_json"]
    assert "招商银行" not in row["items_json"]
    items = json.loads(row["items_json"])
    by_kind = {it["kind"]: it for it in items}
    assert by_kind["PHONE"]["head_hint"] == ""
    assert by_kind["ORG"]["head_hint"] == ""
    assert by_kind["URL_TOKEN"]["head_hint"] != ""

    # >200 items truncates items_json cleanly and sets items_truncated=1.
    many_phones = " ".join(f"138{10000000 + i}" for i in range(210))
    ctx_many = desens_audit.prepare_outbound(many_phones, call_site="runner")
    assert ctx_many is not None
    assert len(ctx_many.mask_result.items) >= 201
    desens_audit.record_event(ctx_many)
    trunc_row = desens_db.execute(
        "SELECT items_json, items_truncated FROM desensitize_events WHERE call_id = ?",
        (ctx_many.call_id,),
    ).fetchone()
    assert trunc_row["items_truncated"] == 1
    assert len(json.loads(trunc_row["items_json"])) == 200


def test_wiring_stream_argv_limit_downgrade_and_direct_invoke_fallback(
    desens_db: sqlite3.Connection,
    fake_hermes: FakeHermesEnv,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_setting(desens_db, "desensitize.enabled", "1")
    monkeypatch.setenv("FAKE_HERMES_ECHO_PLACEHOLDERS", "1")

    # Masking phone expands 11 chars -> 20+note chars. Force limit so L1+L2 exceeds limit while raw+L1 fits.
    prompt = "请查手机号 13812345678 的历史"
    raw_len = len(prompt.encode("utf-8"))
    monkeypatch.setattr(stream_mod, "ARGV_PROMPT_LIMIT_BYTES", raw_len + 10)
    res_stream = stream_mod.run_hermes_stream(prompt)
    assert res_stream.text == "FAKE-REPLY"

    # Now make raw_len itself too tight even after L1 fallback -> triggers fallback_used='argv_limit'.
    prompt2 = "密钥 sk-proj-AbCdEf1234567890GhIjKlMnOpQr"
    monkeypatch.setattr(stream_mod, "ARGV_PROMPT_LIMIT_BYTES", len(prompt2.encode("utf-8")) + 5)
    stream_mod.run_hermes_stream(prompt2)
    row_limit = desens_db.execute(
        "SELECT fallback_used FROM desensitize_events ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row_limit["fallback_used"] == "argv_limit"

    # Direct _invoke() call bypassing call() still masks outbound & restores inbound.
    runner = FailoverRunner.from_settings(fallback_chain=[("dummy", "dummy")])
    sub = runner._invoke(CallRequest(provider="dummy", model_id="dummy", prompt=prompt), "dummy")
    assert "13812345678" in sub.stdout
    invs = fake_hermes.read_invocations()
    assert "13812345678" not in " ".join(invs[-1]["argv"])  # type: ignore[arg-type]
