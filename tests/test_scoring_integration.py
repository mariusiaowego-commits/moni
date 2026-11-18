"""Integration tests for the scoring surface (design §5.2–§7, §9)."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.share.brief_html import render_brief_html


@pytest.fixture(autouse=True)
def fresh_db(tmp_path: Path):
    db_path = tmp_path / "scoring.db"
    set_db_path(db_path)
    migrate()
    yield db_path
    close_db()
    import moni.db.connection as conn_mod

    conn_mod._CON = None
    conn_mod._DB_PATH = None


@pytest.fixture
def client() -> TestClient:
    from moni.web.server import create_app

    return TestClient(create_app())


def _long_body() -> str:
    sentence = "监管要求支付机构在 2025 年 12 月 31 日前完成备案，系统改造预算 3000 万元。"
    paragraph = sentence + "补充说明。" * 210
    return "\n\n".join([paragraph] * 5)


def _insert(
    aid: str,
    *,
    title: str = "测试文章",
    body: str = "",
    publisher: str = "某报",
    published_at: str | None = None,
    **extra,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    cols = {
        "id": aid,
        "title": title,
        "url": f"https://example.com/{aid}",
        "source": "testsrc",
        "publisher": publisher,
        "published_at": published_at,
        "fetched_at": now,
        "raw_content": body,
    }
    cols.update(extra)
    names = ", ".join(cols)
    marks = ", ".join("?" * len(cols))
    get_db().execute(
        f"INSERT INTO articles ({names}) VALUES ({marks})", tuple(cols.values())
    )
    get_db().commit()


# ── Pin column ────────────────────────────────────────────────────────────


def test_pin_scores_quality_and_list_shape(client):
    _insert("q1", body=_long_body())
    r = client.post("/api/articles/q1/pin?on=true")
    assert r.status_code == 200
    body = r.json()
    assert body["quality_score"] == 4.8
    assert body["quality_formula"] == "pin_heuristic_v3"
    assert body["quality_status"] == "ready"
    assert body["quality_breakdown"]["total"] == 4.8

    pinned = client.get("/api/articles/pinned").json()["items"]
    assert pinned[0]["quality"]["score"] == 4.8
    assert pinned[0]["quality"]["formula"] == "pin_heuristic_v3"

    # The shared article listing must not grow quality columns.
    listed = client.get("/api/articles").json()
    assert "quality" not in listed[0]
    assert "quality_score" not in listed[0]


def test_refresh_endpoint_is_idempotent_and_body_sensitive(client):
    _insert(
        "legacy",
        body="短稿",
        quality_score=96,
        quality_status=None,
        quality_formula=None,
    )
    get_db().execute(
        "INSERT INTO article_pins (article_id, pinned_at) VALUES (?, ?)",
        ("legacy", datetime.now(timezone.utc).isoformat()),
    )
    get_db().commit()

    first = client.post(
        "/api/articles/pins/quality-refresh", json={"article_ids": ["legacy"]}
    ).json()
    assert first["updated"] == ["legacy"]
    row = get_db().execute(
        "SELECT quality_score, quality_formula, quality_input_hash FROM articles "
        "WHERE id='legacy'"
    ).fetchone()
    assert row["quality_formula"] == "pin_heuristic_v3"
    assert row["quality_score"] == 1.7  # 2.5 base − 1.1 thin body + 0.3 publisher
    first_hash = row["quality_input_hash"]

    second = client.post(
        "/api/articles/pins/quality-refresh", json={"article_ids": ["legacy"]}
    ).json()
    assert second["unchanged"] == ["legacy"]

    get_db().execute("UPDATE articles SET raw_content=? WHERE id='legacy'", (_long_body(),))
    get_db().commit()
    third = client.post(
        "/api/articles/pins/quality-refresh", json={"article_ids": ["legacy"]}
    ).json()
    assert third["updated"] == ["legacy"]
    row = get_db().execute(
        "SELECT quality_score, quality_input_hash FROM articles WHERE id='legacy'"
    ).fetchone()
    assert row["quality_score"] == 4.8
    assert row["quality_input_hash"] != first_hash

    skipped = client.post(
        "/api/articles/pins/quality-refresh", json={"article_ids": ["missing"]}
    ).json()
    assert skipped["skipped"] == ["missing"]


def test_pinned_get_is_read_only_for_legacy_rows(client):
    _insert("legacy2", body="短稿", quality_score=96)
    get_db().execute(
        "INSERT INTO article_pins (article_id, pinned_at) VALUES (?, ?)",
        ("legacy2", datetime.now(timezone.utc).isoformat()),
    )
    get_db().commit()

    body = client.get("/api/articles/pinned").json()
    item = body["items"][0]
    assert item["quality"]["score"] == 96
    assert item["quality"]["formula"] is None
    row = get_db().execute(
        "SELECT quality_score, quality_formula FROM articles WHERE id='legacy2'"
    ).fetchone()
    assert row["quality_score"] == 96
    assert row["quality_formula"] is None


def test_pinned_list_pages_cover_the_whole_pin_set(client):
    now = datetime.now(timezone.utc).isoformat()
    for i in range(25):
        _insert(f"page-{i:02d}", body="正文" * 200)
        get_db().execute(
            "INSERT INTO article_pins (article_id, pinned_at) VALUES (?, ?)",
            (f"page-{i:02d}", now),
        )
    get_db().commit()

    first = client.get("/api/articles/pinned?page=1&page_size=20").json()
    second = client.get("/api/articles/pinned?page=2&page_size=20").json()
    assert first["total"] == 25
    assert len(first["items"]) == 20
    assert len(second["items"]) == 5
    assert {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]} == set()
    assert {i["id"] for i in first["items"]} | {i["id"] for i in second["items"]} == {
        f"page-{i:02d}" for i in range(25)
    }


def test_budget_skip_persists_null_score_and_retries(fresh_db):
    from moni.share.enrichment import _ensure_business_value

    _insert("budget", body=_long_body(), business_value_score=55, business_value_note="旧说明")
    conn = get_db()
    row = conn.execute("SELECT * FROM articles WHERE id='budget'").fetchone()
    ok = _ensure_business_value(conn, row, [], [0.0], article_budget=10.0)
    assert ok is False
    row = conn.execute(
        "SELECT business_value_score, business_value_status, business_value_error "
        "FROM articles WHERE id='budget'"
    ).fetchone()
    assert row["business_value_score"] is None
    assert row["business_value_status"] == "budget_skipped"
    assert row["business_value_error"] == "预算不足"


def test_crud_put_recomputes_pinned_quality(client):
    _insert("edit", body="短稿")
    client.post("/api/articles/edit/pin?on=true")
    before = get_db().execute(
        "SELECT quality_score FROM articles WHERE id='edit'"
    ).fetchone()["quality_score"]

    r = client.put("/api/articles/edit", json={"content": _long_body()})
    assert r.status_code == 200, r.text
    after = get_db().execute(
        "SELECT quality_score FROM articles WHERE id='edit'"
    ).fetchone()["quality_score"]
    assert before != after
    assert after == 4.8


def test_crud_title_only_does_not_recompute(client):
    _insert("t1", body="短稿")
    client.post("/api/articles/t1/pin?on=true")
    before = get_db().execute(
        "SELECT quality_score_at FROM articles WHERE id='t1'"
    ).fetchone()["quality_score_at"]

    r = client.put("/api/articles/t1", json={"title": "只改标题"})
    assert r.status_code == 200, r.text
    after = get_db().execute(
        "SELECT quality_score_at FROM articles WHERE id='t1'"
    ).fetchone()["quality_score_at"]
    assert after == before


def test_fast_hermes_does_not_burn_the_run_budget(fresh_db, monkeypatch):
    import moni.share.enrichment as enrichment

    def fake_hermes(prompt: str, timeout_sec: int = 120, extra_args=None):
        if "取证助手" in prompt:
            return json.dumps(
                {
                    "evidence": [
                        {"id": "e1", "quote": "监管要求支付机构在 2025 年 12 月 31 日前完成备案"}
                    ]
                },
                ensure_ascii=False,
            ), 0
        if "评分员" in prompt:
            dims = {
                key: {"score": 0, "evidence_ids": [], "reason": f"{key} 无"}
                for key in ("rule_delta",)
            }
            return json.dumps({"dimensions": dims}), 0
        if "检查员" in prompt:
            return json.dumps({"pass": True, "reasons": []}), 0
        return "{}", 0

    monkeypatch.setattr(enrichment, "_invoke_hermes", fake_hermes)
    _insert("fast", body=_long_body())
    conn = get_db()
    row = conn.execute("SELECT * FROM articles WHERE id='fast'").fetchone()
    budget = [900.0]
    enrichment._ensure_business_value(conn, row, [], budget, article_budget=120.0)
    assert budget[0] > 890.0


# ── Brief rendering ───────────────────────────────────────────────────────


def _breakdown(
    *,
    status: str = "ready",
    total: int = 72,
    dims: list[dict] | None = None,
    scored_on: str | None = None,
    window_dates: list[str] | None = None,
) -> str:
    if dims is None:
        dims = [
            {"key": "rule_delta", "label": "规则约束", "weight": 14, "score": 8},
            {"key": "named_peer_move", "label": "同业动作", "weight": 12, "score": 8},
            {"key": "decision_window", "label": "决策窗口", "weight": 8, "score": 8},
            {"key": "money_path", "label": "资金路径", "weight": 10, "score": 3},
        ]
    return json.dumps(
        {
            "rubric": "bv_rubric_v1",
            "status": status,
            "total": total,
            "scored_on": scored_on or date.today().isoformat(),
            "dimensions": dims,
            "decision_window_dates": window_dates or [],
        },
        ensure_ascii=False,
    )


def _brief_row(aid: str, title: str, *, score: int, status: str, breakdown: str | None = None):
    _insert(
        aid,
        title=title,
        body=_long_body(),
        ai_summary="摘要：支付新规与系统改造。",
        fact_check_conclusion="无需核查",
        business_value_score=score,
        business_value_note="同业动作与规则约束清楚",
        business_value_status=status,
        business_value_breakdown_json=breakdown,
    )


def test_insufficient_row_is_excluded_with_short_body_reason(fresh_db):
    _insert(
        "thin",
        title="短文",
        body="短" * 199,
        ai_summary="摘要内容足够长用于通过摘要闸门。",
        fact_check_conclusion="无需核查",
        business_value_status="insufficient",
        business_value_note="正文过短，未打业务价值分",
    )
    html_doc = render_brief_html(str(fresh_db), ["thin"])
    assert "未纳入本次简报" in html_doc
    assert "正文过短（不足 200 字）" in html_doc


def test_ready_sorts_before_needs_review(fresh_db):
    _brief_row("rev", "待复核文章", score=90, status="needs_review", breakdown=_breakdown(status="needs_review", total=90))
    _brief_row("ok", "通过文章", score=11, status="ready", breakdown=_breakdown(total=11))
    html_doc = render_brief_html(str(fresh_db), ["rev", "ok"])
    assert html_doc.index("通过文章") < html_doc.index("待复核文章")
    assert "待复核" in html_doc


def test_card_tags_are_capped_and_carry_no_numbers(fresh_db):
    _brief_row("tags", "标签文章", score=72, status="ready", breakdown=_breakdown())
    html_doc = render_brief_html(str(fresh_db), ["tags"])
    assert "通用 72" in html_doc
    assert "规则约束、同业动作、决策窗口" in html_doc
    assert "资金路径" not in html_doc
    assert "规则约束 8" not in html_doc


def test_expired_window_is_annotated_without_changing_score(fresh_db):
    scored_on = (date.today() - timedelta(days=120)).isoformat()
    deadline = (date.today() - timedelta(days=68)).isoformat()  # 52 days at scoring
    _brief_row(
        "win",
        "窗口文章",
        score=72,
        status="ready",
        breakdown=_breakdown(scored_on=scored_on, window_dates=[deadline]),
    )
    html_doc = render_brief_html(str(fresh_db), ["win"])
    assert "窗口已过（评分时 52 天）" in html_doc
    assert "通用 72" in html_doc
    row = get_db().execute(
        "SELECT business_value_score, business_value_status FROM articles WHERE id='win'"
    ).fetchone()
    assert row["business_value_score"] == 72
    assert row["business_value_status"] == "ready"


def test_future_window_is_not_annotated(fresh_db):
    scored_on = date.today().isoformat()
    deadline = (date.today() + timedelta(days=30)).isoformat()
    _brief_row(
        "win2",
        "窗口未到",
        score=72,
        status="ready",
        breakdown=_breakdown(scored_on=scored_on, window_dates=[deadline]),
    )
    html_doc = render_brief_html(str(fresh_db), ["win2"])
    assert "窗口已过" not in html_doc


def test_stale_hash_is_excluded(fresh_db):
    _brief_row("stale", "旧分文章", score=88, status="ready", breakdown=_breakdown(total=88))
    get_db().execute(
        "UPDATE articles SET business_value_hash='deadbeef' WHERE id='stale'"
    )
    get_db().commit()
    html_doc = render_brief_html(str(fresh_db), ["stale"])
    assert "未纳入本次简报" in html_doc
    assert "业务价值未完成" in html_doc


def test_legacy_scored_rows_sort_with_ready(fresh_db):
    _brief_row("legacy-ok", "旧行八十分", score=80, status=None, breakdown=None)
    _brief_row("ready-low", "新行十一分", score=11, status="ready", breakdown=_breakdown(total=11))
    html_doc = render_brief_html(str(fresh_db), ["ready-low", "legacy-ok"])
    assert html_doc.index("旧行八十分") < html_doc.index("新行十一分")
