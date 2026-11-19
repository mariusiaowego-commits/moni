"""Preview / apply / review behaviour of ``moni.moderation.service``."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.moderation import service


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.db"
    set_db_path(path)
    migrate()
    yield path
    close_db()
    import moni.db.connection as conn_mod
    import moni.db.migrate as mig_mod

    conn_mod._CON = None  # type: ignore[attr-defined]
    conn_mod._DB_PATH = None  # type: ignore[attr-defined]
    mig_mod._CON = None  # type: ignore[attr-defined]


def _add(article_id: str, title: str, body: str) -> None:
    conn = get_db()
    conn.execute(
        """
        INSERT INTO articles (id, title, url, source, fetched_at, raw_content)
        VALUES (?, ?, ?, 'qbitai', ?, ?)
        """,
        (
            article_id,
            title,
            f"https://e.com/{article_id}",
            datetime.now(timezone.utc).isoformat(),
            body,
        ),
    )
    conn.commit()


def _rows() -> int:
    return get_db().execute("SELECT COUNT(*) FROM content_labels").fetchone()[0]


def test_preview_never_writes_labels(db):
    _add("a1", "标题", "加微信稳赚，内部渠道名额有限。")
    run = service.preview()
    assert run["mode"] == "preview"
    assert run["labeled"] == 1
    assert run["new_rows"] == 0
    assert _rows() == 0


def test_apply_writes_and_is_idempotent(db):
    _add("a1", "标题", "加微信稳赚，内部渠道名额有限。")
    first = service.apply_labels()
    assert first["new_rows"] == 1
    second = service.apply_labels()
    assert second["new_rows"] == 0
    assert _rows() == 1


def test_apply_never_touches_article_body(db):
    body = "加微信稳赚，内部渠道名额有限。\n<ENV_ID>"
    _add("a1", "标题", body)
    service.apply_labels()
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT raw_content FROM articles WHERE id='a1'"
        ).fetchone()[0] == body


def test_human_decision_is_kept_on_rerun(db):
    _add("a1", "标题", "加微信稳赚，内部渠道名额有限。")
    service.apply_labels()
    service.review_label("a1", "pure_ad", "dismissed", "正常营销号")
    run = service.apply_labels()
    assert run["kept_manual"] == 1
    state = get_db().execute(
        "SELECT source, review_state FROM content_labels WHERE article_id='a1'"
    ).fetchone()
    assert tuple(state) == ("manual", "dismissed")


def test_samples_mask_s_title_by_default(db):
    _add("a1", "露骨标题示例", "加微信，博彩平台内部渠道，日入三百。")
    service.apply_labels()
    masked = service.samples("nsfw_gamble")
    assert masked and "***" in masked[0]["title"]
    plain = service.samples("nsfw_gamble", reveal=True)
    assert plain[0]["title"] == "露骨标题示例"


def test_gate_respects_dismissed(db, monkeypatch):
    monkeypatch.setattr(service, "GATE_ENABLED", True)
    _add("a1", "标题", "加微信稳赚，内部渠道名额有限。")
    service.apply_labels()
    assert service.gated_map(get_db(), ["a1"]) == {"a1": "pure_ad"}
    service.review_label("a1", "pure_ad", "dismissed")
    assert service.gated_map(get_db(), ["a1"]) == {}


def test_gate_is_off_by_default(db, monkeypatch):
    _add("a1", "标题", "加微信稳赚，内部渠道名额有限。")
    service.apply_labels()
    assert service.GATE_ENABLED is False
    assert service.gated_map(get_db(), ["a1"]) == {}
    monkeypatch.setattr(service, "GATE_ENABLED", True)
    assert service.gated_map(get_db(), ["a1"]) == {"a1": "pure_ad"}


def test_relabel_after_body_change_drops_stale_label(db):
    _add("a1", "标题", "加微信稳赚，内部渠道名额有限。")
    service.apply_labels()
    get_db().execute("UPDATE articles SET raw_content='今天天气不错。' WHERE id='a1'")
    get_db().commit()
    service.relabel_article("a1")
    assert _rows() == 0


def test_summary_reports_rule_counts_not_terms(db):
    _add("a1", "标题", "加微信稳赚。")
    service.apply_labels()
    data = service.summary()
    assert data["rule_version"] == "labels-v1"
    assert set(data["categories"]) == set(service.CATEGORIES)
    assert "稳赚" not in str(data)


def test_report_contains_no_titles(db):
    _add("a1", "标题甲", "加微信稳赚。")
    run = service.apply_labels()
    text = service.report_markdown(run)
    assert "标题甲" not in text
    assert "S 类" in text
