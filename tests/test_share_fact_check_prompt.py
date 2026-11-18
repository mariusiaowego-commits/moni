"""分享事实核查文案的回归测试 (2026-11-18).

失败模式:
  1. 把 fact_check_note 的 query/snippet/rationale 原样塞进 prompt，
     模型只能把证据枚举成一句话。
  2. 有一句人话 summary 时仍然把逐条证据交给模型，枚举风险照旧。
  3. include_fact_check=false 的报告仍从历史数据继承核查块。
  4. prompt 只定义颜色，没有"不抄数字/区间/参数名"的硬约束。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from moni.db.connection import close_db, set_db_path
from moni.db.migrate import migrate
from moni.share.enrichment import rebuild_article_content

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "news.db"
    set_db_path(db_path)
    migrate()
    yield db_path
    close_db()
    import moni.db.connection as conn_mod
    import moni.db.migrate as mig_mod

    conn_mod._CON = None
    conn_mod._DB_PATH = None
    mig_mod._CON = None


def _insert_article(
    db_path: Path,
    aid: str = "a1",
    *,
    conclusion: str = "【待核实】",
    note: str = "",
    summary: str | None = None,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO articles
                (id, title, url, source, publisher, raw_content, published_at,
                 fetched_at, fact_check_conclusion, fact_check_note, has_fact_check)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                aid,
                f"文章 {aid}",
                f"https://example.com/{aid}",
                "testsrc",
                "测试发布方",
                "正文内容",
                "2026-05-23",
                "2026-05-24T00:00:00+00:00",
                conclusion,
                note,
            ),
        )
        if summary is not None:
            conn.execute(
                "UPDATE articles SET fact_check_summary = ? WHERE id = ?",
                (summary, aid),
            )
        conn.commit()
    finally:
        conn.close()


def _evidence_note() -> str:
    return json.dumps(
        [
            {
                "claim": "温度取值范围是 0 到 2",
                "verdict": "【已核实】",
                "query": "OpenAI temperature range",
                "url": "https://developers.openai.com/api",
                "domain": "developers.openai.com",
                "snippet": "temperature: minimum 0, maximum 2, top_p ...",
                "rationale": "温度[0,2]、top_p 为累计概率、频率与存在惩罚[-2,2]与文中表格逐项一致",
            },
            {
                "claim": "SKILL.md 应控制在 500 行内",
                "verdict": "【已核实】",
                "query": "Claude skills best practices",
                "url": "https://code.claude.com/docs/en/skills",
                "domain": "code.claude.com",
                "snippet": "Keep SKILL.md under 500 lines.",
                "rationale": "官方文档原文即控制在 500 行以下",
            },
        ],
        ensure_ascii=False,
    )


def test_summary_replaces_raw_evidence(fresh_db: Path):
    """有 summary 时只给结论 + 人话要点，不给逐条证据。"""
    _insert_article(
        fresh_db,
        note=_evidence_note(),
        summary="参数表与官方文档一致；这些参数该怎么设仍是作者判断",
    )

    text = rebuild_article_content(str(fresh_db), ["a1"])

    assert "**核查要点**" in text
    assert "参数表与官方文档一致" in text
    assert "逐条记录" not in text
    assert "温度[0,2]" not in text
    assert '"snippet"' not in text
    assert "https://developers.openai.com" not in text


def test_legacy_row_uses_compact_claims(fresh_db: Path):
    """历史行没有 summary：只给 claim/verdict/domain，丢掉 query/snippet/url。"""
    _insert_article(fresh_db, note=_evidence_note())

    text = rebuild_article_content(str(fresh_db), ["a1"])

    assert "**逐条记录**" in text
    assert "[【已核实】] 温度取值范围是 0 到 2" in text
    assert "developers.openai.com" in text
    block = text[text.index("**事实核查**"):]
    assert '"snippet"' not in block
    assert '"query"' not in block
    assert "https://" not in block
    assert len(text) < 1200


def test_include_fact_check_false_omits_block(fresh_db: Path):
    _insert_article(fresh_db, note=_evidence_note(), summary="参数一致，其余为观点")

    text = rebuild_article_content(str(fresh_db), ["a1"], include_fact_check=False)

    assert "事实核查" not in text
    assert "核查要点" not in text


def test_brief_flag_false_skips_evidence_footer(fresh_db: Path):
    """brief 模式也不再无条件追加「事实核查证据」脚注."""
    _insert_article(fresh_db, note=_evidence_note(), summary="参数一致，其余为观点")

    text = rebuild_article_content(
        str(fresh_db), ["a1"], brief=True, include_fact_check=False
    )

    assert "事实核查" not in text
    assert "事实核查证据" not in text


def test_non_json_note_falls_back_to_single_line(fresh_db: Path):
    _insert_article(fresh_db, note="claim1 【已核实】: 监管文件已发布")

    text = rebuild_article_content(str(fresh_db), ["a1"])

    assert "监管文件已发布" in text
    assert "【已核实】" in text


def test_report_png_prompt_has_copy_ban():
    prompt = (REPO_ROOT / "moni/share/prompts/format-report-png.md").read_text()

    assert "one-line verdict with colored marker" not in prompt
    assert "2-3 plain sentences" in prompt
    assert "≤150 Chinese characters" in prompt
    # 数字不是一律禁抄：能翻成含义就必须写，翻不出才省略。
    assert "Numbers are allowed" in prompt
    assert "Never write parameter names" in prompt
    assert "「与文档一致」" in prompt
    assert "Override content-structure" in prompt
    good_example = prompt.split("Good: ")[1].splitlines()[0]
    assert "；" not in good_example


def test_fact_check_prompt_marks_evidence_types_and_discipline():
    prompt = (REPO_ROOT / "moni/llm/prompts/fact_check.md").read_text()

    for kind in ("文内矛盾", "公开常识", "模型记忆", "无依据"):
        assert kind in prompt
    # 无证据不许判「错误」。
    assert "没有证据就说没有证据" in prompt
    assert "1-3 条" in prompt
    assert "600 字以内" in prompt


def test_fact_check_plus_prompt_requires_thesis_and_source_date():
    prompt = (REPO_ROOT / "moni/llm/prompts/fact_check_plus.md").read_text()

    assert '"thesis"' in prompt
    assert "≤40 字" in prompt
    assert "2-3 句，≤150 汉字" in prompt
    assert '"source_date"' in prompt
    assert "翻成读者能懂的含义" in prompt


def test_content_structure_scopes_per_claim_to_long_form():
    prompt = (REPO_ROOT / "moni/share/prompts/content-structure.md").read_text()

    assert "For each checked claim, provide 1-2 sentences" not in prompt
    assert "Long-form formats" in prompt
    assert "One-image formats" in prompt


def test_style_prompt_bans_evidence_enumeration():
    prompt = (REPO_ROOT / "moni/share/prompts/writing-style-compact.md").read_text()

    assert "参数区间" in prompt
    assert "逐项一致" in prompt


def test_worker_loads_style_for_one_image_reports():
    script = (REPO_ROOT / "scripts/default_worker.sh").read_text()

    assert (
        'if [[ "$brief_val" == "true" || "$format" == "report_png" '
        '|| "$format" == "info_graphic_png" ]]; then' in script
    )
    assert "MONI_PROMPT_DUMP" in script
