"""Tests for ``moni.cron.jobs.predict_tags_pending`` (v1.0.47).

Context
-------
v1.0.47 audit found three defects in this job, all of which had to be fixed
before it could ever tag an article:

1. ``SELECT ... a.content`` — the ``articles`` table has **no** ``content``
   column, so every run died with ``sqlite3.OperationalError: no such
   column: a.content`` before reaching the LLM.
2. ``ChatClient.chat(prompt, model="claude-3-5-sonnet-latest", ...)`` — the
   deprecated shim silently drops every kwarg, so the model was never the
   one requested (and not the configured *tag* model either).
3. ``_parse_tags_from_reply`` split the raw reply on commas, while
   ``predict_tag.md`` asks for strict JSON
   (``{"tags": [...], "reasoning": "..."}``).  A JSON reply shredded into
   fragments, none of which matched a tag name, so zero tags were written.

These tests are hermetic: fresh DB in ``tmp_path``, and the LLM is injected
through the ``llm_call_fn`` seam — no network, no hermes subprocess.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

WT_ROOT = Path(__file__).parent.parent
if str(WT_ROOT) not in sys.path:
    sys.path.insert(0, str(WT_ROOT))

from moni.cron import jobs as cron_jobs
from moni.cron.jobs import _parse_tags_from_reply, predict_tags_pending
from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate


@pytest.fixture(autouse=True)
def _reset_predict_tags_lock():
    """每个用例结束后强制释放模块级锁。

    没有这条时, 任何一个用例在锁里失败就会把锁留给下一个用例 ——
    下一个用例的 ``acquire()`` (阻塞版) 直接死锁, 整套测试卡住而不是
    报红。审计第二轮要求"锁在异常后必须可再获取", 这条让失败可见。
    """
    yield
    lock = cron_jobs._PREDICT_TAGS_LOCK
    if lock.locked():
        try:
            lock.release()
        except RuntimeError:
            # 锁被别的线程持有 —— 那不是本用例的责任
            pass


@pytest.fixture
def isolated_db(tmp_path):
    """Fresh migrated DB in tmp_path — the repo's data/news.db is never touched."""
    db_path = tmp_path / "test_predict_tags.db"
    set_db_path(db_path)
    migrate()
    yield db_path
    close_db()
    import moni.db.connection as conn_mod

    conn_mod._CON = None
    conn_mod._DB_PATH = None


def _seed_tag(conn, name: str) -> None:
    conn.execute(
        "INSERT INTO tag_definitions (name, description, color, created_at, is_active) "
        "VALUES (?, ?, '#888888', '2026-10-23T00:00:00+08:00', 1)",
        (name, f"{name} tag"),
    )


def _seed_article(conn, article_id: str, title: str = "测试文章") -> None:
    title = title or f"文章 {article_id}"
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at, raw_content) "
        "VALUES (?, ?, ?, '36kr_rss', '2026-10-23T00:00:00+08:00', ?)",
        (article_id, title, f"https://example.com/{article_id}", f"正文 {article_id}"),
    )


def _tags_of(article_id: str) -> list[tuple]:
    conn = get_db()
    rows = conn.execute(
        "SELECT tag_name, predicted FROM article_tags WHERE article_id = ? ORDER BY tag_name",
        (article_id,),
    ).fetchall()
    return [(row["tag_name"], row["predicted"]) for row in rows]


# ── parser ────────────────────────────────────────────────────────────────


class TestParseTagsFromReply:
    """The prompt asks for strict JSON — that is the primary contract."""

    def test_strict_json_reply(self):
        reply = json.dumps(
            {"tags": ["银行", "AI"], "reasoning": "文章讲银行用 AI"},
            ensure_ascii=False,
        )
        assert _parse_tags_from_reply(reply) == ["银行", "AI"]

    def test_fenced_json_reply(self):
        reply = '```json\n{"tags": ["银行"], "reasoning": "x"}\n```'
        assert _parse_tags_from_reply(reply) == ["银行"]

    def test_json_embedded_in_prose(self):
        reply = '好的，结果如下：\n{"tags": ["支付"], "reasoning": "x"}\n希望有帮助。'
        assert _parse_tags_from_reply(reply) == ["支付"]

    def test_empty_tag_array(self):
        assert _parse_tags_from_reply('{"tags": [], "reasoning": "都不相关"}') == []

    def test_broken_json_falls_back_to_regex(self):
        """Object body unparseable, but the "tags": [...] array is still there."""
        reply = '{"tags": ["银行", "AI"], "reasoning": "未闭合的引号 \'oops}'
        assert _parse_tags_from_reply(reply) == ["银行", "AI"]

    def test_plain_comma_separated_fallback(self):
        assert _parse_tags_from_reply("银行, AI") == ["银行", "AI"]

    def test_plain_newline_and_bullet_fallback(self):
        assert _parse_tags_from_reply("- 银行\n- AI") == ["银行", "AI"]

    def test_empty_reply(self):
        assert _parse_tags_from_reply("") == []
        assert _parse_tags_from_reply("   \n  ") == []

    def test_dedupes_and_caps_at_three(self):
        reply = json.dumps({"tags": ["银行", "银行", "AI", "支付", "微信"]}, ensure_ascii=False)
        assert _parse_tags_from_reply(reply) == ["银行", "AI", "支付"]

    def test_reasoning_field_never_leaks_into_tags(self):
        """The old comma-split pulled the reasoning text in as a tag candidate."""
        reply = json.dumps(
            {"tags": ["银行"], "reasoning": "讲的是银行, 跟微信无关, 与 AI 稍有关"},
            ensure_ascii=False,
        )
        assert _parse_tags_from_reply(reply) == ["银行"]


# ── job behaviour ────────────────────────────────────────────────────────


class TestPredictTagsPending:
    def test_writes_predicted_tags(self, isolated_db):
        conn = get_db()
        _seed_tag(conn, "银行")
        _seed_tag(conn, "AI")
        _seed_article(conn, "a1")
        conn.commit()

        calls: list[str] = []

        def fake_llm(prompt: str) -> str:
            calls.append(prompt)
            return json.dumps({"tags": ["银行"], "reasoning": "x"}, ensure_ascii=False)

        predict_tags_pending(llm_call_fn=fake_llm)

        assert len(calls) == 1
        assert "银行" in calls[0]  # the prompt lists the candidate tags
        assert _tags_of("a1") == [("银行", 1)]

    def test_ignores_tags_outside_tag_definitions(self, isolated_db):
        conn = get_db()
        _seed_tag(conn, "银行")
        _seed_article(conn, "a1")
        conn.commit()

        predict_tags_pending(
            llm_call_fn=lambda p: json.dumps(
                {"tags": ["银行", "不存在标签"], "reasoning": "x"}, ensure_ascii=False
            )
        )

        assert _tags_of("a1") == [("银行", 1)]

    def test_skips_already_tagged_articles(self, isolated_db):
        conn = get_db()
        _seed_tag(conn, "银行")
        _seed_article(conn, "a1")
        conn.execute(
            "INSERT INTO article_tags (article_id, tag_name, predicted, created_at) "
            "VALUES ('a1', '银行', 0, '2026-10-23T00:00:00+08:00')"
        )
        conn.commit()

        calls: list[str] = []
        predict_tags_pending(llm_call_fn=lambda p: calls.append(p) or "{}")

        assert calls == []

    def test_resolves_the_configured_tag_model(self, isolated_db, monkeypatch):
        conn = get_db()
        _seed_tag(conn, "银行")
        _seed_article(conn, "a1")
        conn.commit()

        seen: list[str] = []
        called: list[tuple] = []

        def fake_resolve(key: str = "active_summary_model_id"):
            seen.append(key)
            return "deepseek", "deepseek-chat"

        def fake_call_llm(provider, model, prompt, **kwargs):
            called.append((provider, model, prompt))
            return '{"tags": ["银行"], "reasoning": "x"}'

        monkeypatch.setattr(cron_jobs, "resolve_active_model", fake_resolve)
        monkeypatch.setattr(cron_jobs, "call_llm", fake_call_llm)

        predict_tags_pending()

        assert seen == ["active_tag_model_id"]
        assert called and called[0][0] == "deepseek" and called[0][1] == "deepseek-chat"

    def test_reentrancy_guard_skips_overlapping_run(self, isolated_db):
        conn = get_db()
        _seed_tag(conn, "银行")
        _seed_article(conn, "a1")
        conn.commit()

        calls: list[str] = []
        cron_jobs._PREDICT_TAGS_LOCK.acquire()
        try:
            predict_tags_pending(llm_call_fn=lambda p: calls.append(p) or "{}")
        finally:
            cron_jobs._PREDICT_TAGS_LOCK.release()

        assert calls == []
        assert _tags_of("a1") == []
        rows = get_db().execute(
            "SELECT success, error FROM cron_runs "
            "WHERE job_id = 'predict_tags_pending' ORDER BY id DESC LIMIT 1"
        ).fetchall()
        assert rows and rows[0]["success"] == 0
        assert "already in progress" in (rows[0]["error"] or "")

    def test_lock_is_released_when_the_body_raises(self, isolated_db, monkeypatch):
        """异常退出后锁必须释放 —— 审计第二轮 F7 一直缺这条自动化。

        锁是模块级 ``threading.Lock``。如果 locked 体抛异常时 ``finally``
        没兜住, 这个定时任务会**一次崩溃就永久卡死** (之后每个 tick 都
        只能走 "skipped: another run already in progress")。
        """
        def boom(**_kwargs):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(cron_jobs, "_predict_tags_pending_locked", boom)

        with pytest.raises(RuntimeError):
            predict_tags_pending(llm_call_fn=lambda p: "{}")

        try:
            # 锁必须已经释放 —— 能再拿到就说明 finally 生效了
            assert cron_jobs._PREDICT_TAGS_LOCK.acquire(blocking=False)
        finally:
            # 万一这条断言失败, 别把锁留给后面的用例 (否则整套测试死锁)
            if cron_jobs._PREDICT_TAGS_LOCK.locked():
                cron_jobs._PREDICT_TAGS_LOCK.release()

    def test_partial_provider_override_is_not_discarded(self, isolated_db, monkeypatch):
        """v1.0.47.1 (audit F2): explicit provider must survive model resolution."""
        conn = get_db()
        _seed_tag(conn, "银行")
        _seed_article(conn, "a1")
        conn.commit()

        called: list[tuple] = []
        monkeypatch.setattr(
            cron_jobs,
            "resolve_active_model",
            lambda key="active_summary_model_id": ("deepseek", "deepseek-chat"),
        )
        monkeypatch.setattr(
            cron_jobs,
            "call_llm",
            lambda provider, model, prompt, **kwargs: (
                called.append((provider, model)) or '{"tags": ["银行"]}'
            ),
        )

        predict_tags_pending(provider="ark")

        assert called == [("ark", "deepseek-chat")]

    def test_partial_model_override_is_not_discarded(self, isolated_db, monkeypatch):
        conn = get_db()
        _seed_tag(conn, "银行")
        _seed_article(conn, "a1")
        conn.commit()

        called: list[tuple] = []
        monkeypatch.setattr(
            cron_jobs,
            "resolve_active_model",
            lambda key="active_summary_model_id": ("deepseek", "deepseek-chat"),
        )
        monkeypatch.setattr(
            cron_jobs,
            "call_llm",
            lambda provider, model, prompt, **kwargs: (
                called.append((provider, model)) or '{"tags": ["银行"]}'
            ),
        )

        predict_tags_pending(model="custom-model")

        assert called == [("deepseek", "custom-model")]

    def test_no_active_model_aborts_cleanly(self, isolated_db, monkeypatch):
        conn = get_db()
        _seed_tag(conn, "银行")
        _seed_article(conn, "a1")
        conn.commit()

        def boom(key: str = "active_summary_model_id"):
            raise RuntimeError("No active LLM model configured in llm_settings")

        monkeypatch.setattr(cron_jobs, "resolve_active_model", boom)

        called: list[str] = []
        monkeypatch.setattr(cron_jobs, "call_llm", lambda *a, **k: called.append(a) or "{}")

        predict_tags_pending()

        assert called == []
        rows = get_db().execute(
            "SELECT success FROM cron_runs WHERE job_id = 'predict_tags_pending'"
        ).fetchall()
        assert rows and rows[0]["success"] == 0

    def test_llm_failure_isolated_per_article(self, isolated_db):
        conn = get_db()
        _seed_tag(conn, "银行")
        _seed_article(conn, "a1")
        _seed_article(conn, "a2")
        conn.commit()

        def flaky(prompt: str) -> str:
            if "a1" in prompt:
                raise RuntimeError("hermes failed: boom")
            return '{"tags": ["银行"], "reasoning": "x"}'

        predict_tags_pending(llm_call_fn=flaky)

        # a1 failed → no tags; a2 succeeded → tagged
        assert _tags_of("a1") == []
        assert _tags_of("a2") == [("银行", 1)]


# ── static contract ──────────────────────────────────────────────────────


def test_no_hardcoded_model_or_deprecated_client_in_jobs_source():
    """Regression guard for defects 2 and 3 of v1.0.47."""
    src = (WT_ROOT / "moni" / "cron" / "jobs.py").read_text(encoding="utf-8")
    # ignore comments — they document what the old code did
    code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    assert "claude-3-5-sonnet-latest" not in code
    assert "ChatClient" not in code
    assert "a.content" not in code  # no such column on articles


def test_predict_tag_prompt_still_asks_for_strict_json():
    """The parser's primary tier assumes the prompt asks for JSON.

    审计第三轮实测: 全文子串匹配会被"反例"块满足 —— ``{"tags"`` 在模板里
    **只出现在 34 行之后的反例区**, 正向示例 (17-21 行) 是 ``{`` 单独成行。
    于是把指令改成"不要用严格 JSON"这类语义取反的文本, 断言照样 PASS。

    改成: 只在"输出格式"**正向块**里断言 + 显式拒绝"否定式指令"。
    """
    template = (WT_ROOT / "moni" / "llm" / "prompts" / "predict_tag.md").read_text(
        encoding="utf-8"
    )
    head = re.search(r"^#\s*输出格式.*$", template, re.M)
    assert head, "prompt lost its '# 输出格式' section"
    nxt = re.search(r"^#\s", template[head.end() :], re.M)
    block = (
        template[head.start() : head.end() + nxt.start()]
        if nxt
        else template[head.start() :]
    )

    # 正向块必须真的给出 JSON 输出示例 (带 "tags" 字段)
    assert re.search(r'"tags"\s*:', block), "输出格式 block lost the \"tags\" field"
    assert re.search(r"```\s*json", block, re.I), (
        "输出格式 block lost the fenced JSON example"
    )

    # 指令必须是"要求 JSON", 不能是"不要 JSON"这类语义取反
    assert re.search(r"json", head.group(0), re.I), head.group(0)
    negation = re.search(
        r"(不要|不用|禁止|无需|不需要)[^。\n]{0,12}json", head.group(0), re.I
    )
    assert not negation, (
        "输出格式 heading turned into a negation: %r" % head.group(0)
    )

    # 反例块另在别处 —— 正向块不应包含反例标记
    assert "❌" not in block
    assert "# 反例" in template
