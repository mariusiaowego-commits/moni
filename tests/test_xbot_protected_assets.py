"""sprint-54 / id 26111703 —— 保护资产只读探针 + 跨文章聚合的护栏。

覆盖 p7 第四轮的两项「开工前必须补」：

* **C-6**：候选清单输出必须含 ``protected_article_ids`` 与 ``is_protected``
* **C-7**：「弱特征命中集 ∩ 作者候选计数集 = ∅」的**交叉集成断言**

本文件**只读** —— 不写生产库、不改生产配置。
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """建一个**临时**库 (照仓库既有样板)。"""
    close_db()
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MONI_DATA_DIR", str(data_dir))
    set_db_path(data_dir / "news.db")
    migrate()
    yield data_dir
    close_db()


def _insert_article(conn, aid, **cols) -> None:
    """插入一篇文章。

    ⚠️ **article_id 是 TEXT** (生产实测: 24 位十六进制串) ⇒ 这里刻意用
    与生产同形状的 id, 防止「用 int 查 ⇒ 一条都命中不了」的静默漏检复发。
    """
    aid = str(aid)
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    base = {
        "id": aid,
        "title": f"文章 {aid}",
        "url": f"https://example.invalid/{aid}",
        "source": "xbot_feed",
        "publisher": "@someone",
        "published_at": now,
        "fetched_at": now,
        "raw_content": "正文",
    }
    base.update(cols)
    keys = ", ".join(base)
    marks = ", ".join("?" * len(base))
    conn.execute(f"INSERT INTO articles ({keys}) VALUES ({marks})", tuple(base.values()))


# ── C-6: 保护资产探针 ───────────────────────────────────────────────────────

def test_guard_detects_each_protected_kind(fresh_db):
    """⚠️ 每类保护资产都要能被检出 (只读查询)。"""
    from moni.fetch.protected_assets import ProtectedAssetGuard

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()

    conn = get_db()
    _insert_article(conn, "aaaaaaaaaaaaaaaaaaaaaaaa")
    _insert_article(conn, "bbbbbbbbbbbbbbbbbbbbbbbb", quality_score=80)
    _insert_article(conn, "cccccccccccccccccccccccc", business_value_score=70)
    _insert_article(conn, "dddddddddddddddddddddddd", has_ai_summary=1)
    _insert_article(conn, "eeeeeeeeeeeeeeeeeeeeeeee", has_fact_check=1)
    _insert_article(conn, "ffffffffffffffffffffffff", brief_summary="简报")
    conn.execute(
        "INSERT INTO article_pins (article_id, pinned_at) VALUES (?, '2026-11-17')",
        ("aaaaaaaaaaaaaaaaaaaaaaaa",),
    )
    conn.execute(
        "INSERT INTO cases (case_id, article_id, subject, case_type, case_action, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("c1", "aaaaaaaaaaaaaaaaaaaaaaaa", "某主体", "AI", "launch", now, now),
    )
    conn.execute(
        "INSERT INTO events (event_id, article_id, subject, event_type, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("e1", "aaaaaaaaaaaaaaaaaaaaaaaa", "某主体", "other", now),
    )
    conn.execute(
        "INSERT INTO reader_conversations (id, article_id, title, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("rc1", "aaaaaaaaaaaaaaaaaaaaaaaa", "对话", now, now),
    )
    conn.execute(
        "INSERT INTO share_tasks (id, format, article_ids, created_at) VALUES (?, ?, ?, ?)",
        ("s1", "md", json.dumps(["aaaaaaaaaaaaaaaaaaaaaaaa"]), now),
    )
    conn.commit()

    guard = ProtectedAssetGuard(conn=conn)

    assert "pin" in guard.kinds_for("aaaaaaaaaaaaaaaaaaaaaaaa")
    assert "case" in guard.kinds_for("aaaaaaaaaaaaaaaaaaaaaaaa")
    assert "event" in guard.kinds_for("aaaaaaaaaaaaaaaaaaaaaaaa")
    assert "reader_conversation" in guard.kinds_for("aaaaaaaaaaaaaaaaaaaaaaaa")
    assert "share_task" in guard.kinds_for("aaaaaaaaaaaaaaaaaaaaaaaa")
    assert "quality_score" in guard.kinds_for("bbbbbbbbbbbbbbbbbbbbbbbb")
    assert "business_value_score" in guard.kinds_for("cccccccccccccccccccccccc")
    assert "ai_summary" in guard.kinds_for("dddddddddddddddddddddddd")
    assert "fact_check" in guard.kinds_for("eeeeeeeeeeeeeeeeeeeeeeee")
    assert "brief" in guard.kinds_for("ffffffffffffffffffffffff")

    assert guard.is_protected("aaaaaaaaaaaaaaaaaaaaaaaa") is True
    assert guard.is_protected("bbbbbbbbbbbbbbbbbbbbbbbb") is True


def test_guard_reports_unprotected_article(fresh_db):
    """普通文章 ⇒ 无保护资产。"""
    from moni.fetch.protected_assets import ProtectedAssetGuard

    conn = get_db()
    _insert_article(conn, "111111111111111111111111")
    conn.commit()

    guard = ProtectedAssetGuard(conn=conn)
    assert guard.is_protected("111111111111111111111111") is False
    assert guard.kinds_for("111111111111111111111111") == ()


def test_split_protected_separates_candidates(fresh_db):
    """⚠️ 候选清单必须能把「普通」与「受保护」分开。"""
    from moni.fetch.protected_assets import ProtectedAssetGuard, split_protected

    conn = get_db()
    _insert_article(conn, "222222222222222222222222")
    _insert_article(conn, "333333333333333333333333", quality_score=90)
    conn.execute(
        "INSERT INTO article_pins (article_id, pinned_at) VALUES (?, '2026-11-17')",
        ("333333333333333333333333",),
    )
    conn.commit()

    guard = ProtectedAssetGuard(conn=conn)
    unprotected, protected, kinds_map = split_protected(
        guard, ["222222222222222222222222", "333333333333333333333333"]
    )

    assert unprotected == ["222222222222222222222222"]
    assert protected == ["333333333333333333333333"]
    assert set(kinds_map["333333333333333333333333"]) == {"pin", "quality_score"}


def test_guard_batch_matches_single(fresh_db):
    """批量查询与单条查询结果**必须一致** (避免两条路径口径漂移)。"""
    from moni.fetch.protected_assets import ProtectedAssetGuard

    conn = get_db()
    _insert_article(conn, "444444444444444444444444")
    _insert_article(conn, "555555555555555555555555", has_ai_summary=1)
    conn.execute(
        "INSERT INTO article_pins (article_id, pinned_at) VALUES (?, '2026-11-17')",
        ("444444444444444444444444",),
    )
    conn.commit()

    guard = ProtectedAssetGuard(conn=conn)
    batch = guard.protected_kinds_map(
        ["444444444444444444444444", "555555555555555555555555"]
    )
    for aid in ("444444444444444444444444", "555555555555555555555555"):
        assert set(batch.get(aid, ())) == set(guard.kinds_for(aid)), (
            f"article {aid} 的批量/单条口径不一致"
        )


def test_guard_does_no_writes(fresh_db):
    """⚠️ 护栏本身**零写入** —— 跑完后所有表行数不变。"""
    from moni.fetch.protected_assets import ProtectedAssetGuard

    conn = get_db()
    _insert_article(conn, "666666666666666666666666", quality_score=50, has_ai_summary=1)
    conn.execute(
        "INSERT INTO article_pins (article_id, pinned_at) VALUES (?, '2026-11-17')",
        ("666666666666666666666666",),
    )
    conn.commit()

    def counts():
        return {
            t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("articles", "article_pins", "cases", "events",
                      "reader_conversations", "share_tasks", "basket",
                      "source_author_blocks")
        }

    before = counts()
    guard = ProtectedAssetGuard(conn=conn)
    guard.protected_kinds_map(["666666666666666666666666"])
    guard.kinds_for("666666666666666666666666")
    conn.rollback()
    assert counts() == before, "保护资产探针产生了写入"


def test_guard_works_on_readonly_connection(fresh_db):
    """⚠️ 在 ``mode=ro`` 连接上也能跑 (阶段一预演器的真实用法)。"""
    import pathlib

    from moni.fetch.protected_assets import ProtectedAssetGuard

    conn = get_db()
    _insert_article(conn, "777777777777777777777777", quality_score=60)
    conn.commit()
    db_path = pathlib.Path(conn.execute("PRAGMA database_list").fetchone()[2])
    conn.close_db() if hasattr(conn, "close_db") else None
    close_db()

    ro = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    ro.row_factory = sqlite3.Row
    ro.execute("PRAGMA query_only=ON")
    try:
        guard = ProtectedAssetGuard(conn=ro)
        assert "quality_score" in guard.kinds_for("777777777777777777777777")
    finally:
        ro.close()


# ── C-7: 弱特征命中集 ∩ 作者候选计数集 = ∅ (交叉集成断言) ────────────────────

def test_weak_signal_hits_never_enter_author_candidate_count():
    """⚠️ **交叉集成断言**（p7 C-7 的核心）。

    弱特征命中的文章**永远不**计入作者候选的强样本数 ⇒
    「弱特征命中集 ∩ 计入作者候选计数的文章集合」**必须为空**。
    """
    from moni.fetch.moderation import aggregate_author, evaluate_article as ea

    # 同一作者: 3 篇弱特征文章 (单条弱证据 ⇒ **无**干净历史 ⇒ quarantine +
    # is_weak_only=True) + 2 篇强信号文章
    #
    # ⚠️ 注意: 若给 ``author_clean_history_count`` ⇒ 走「降权放行」分支 ⇒
    #    ``action=allow`` 且 ``is_weak_only=False`` ⇒ 那**不是**弱特征命中,
    #    不该进 weak 集合。弱特征集合的定义是「判为 quarantine 且 is_weak_only」。
    decisions = []
    for i in range(3):
        d = ea(
            title="家人们",
            content="今天聊聊分包之后主包体积怎么压，讨论了一些实现细节与取舍方案",
            author_name="某人",
            handle="@someone",
            author_strong_sample_count=0,
        )
        decisions.append((f"w{i:023d}", d))
    for i in range(2):
        d = ea(
            title="加群有福利",
            content="博彩平台注册送彩金，私聊入群",
            author_name="某人",
            handle="@someone",
            author_strong_sample_count=0,
        )
        decisions.append((f"s{i:023d}", d))

    agg = aggregate_author(author_key="@someone", decisions=decisions)

    weak_ids = set(agg.weak_article_ids)
    counted_ids = set(agg.strong_article_ids)
    assert weak_ids, "本用例必须真的产出弱特征命中"
    assert weak_ids & counted_ids == set(), (
        f"弱特征文章混进了作者候选计数: {weak_ids & counted_ids}"
    )
    assert agg.weak_samples == 3
    assert agg.strong_samples == 2


def test_author_candidate_needs_strong_samples_only():
    """⚠️ 只有**弱**特征 ⇒ 永远产不出作者候选 (哪怕 99 篇)。"""
    from moni.fetch.moderation import aggregate_author, evaluate_article as ea

    decisions = []
    for i in range(99):
        d = ea(
            title="家人们",
            content="今天聊聊分包之后主包体积怎么压，讨论了一些实现细节与取舍方案",
            author_name="某人",
            handle="@someone",
        )
        decisions.append((f"x{i:023d}", d))

    agg = aggregate_author(author_key="@someone", decisions=decisions)
    assert agg.weak_samples == 99
    assert agg.strong_samples == 0
    assert agg.author_candidate is False, "99 篇弱特征把作者推成候选了"


def test_author_candidate_triggers_on_strong_samples():
    """⚠️ 同一作者多篇**独立**强样本 ⇒ 产出作者候选 (仍需 dad 确认)。"""
    from moni.fetch.moderation import aggregate_author, evaluate_article as ea

    decisions = []
    for i in range(3):
        d = ea(
            title="加群有福利",
            content="博彩平台注册送彩金，私聊入群",
            author_name="某人",
            handle="@someone",
        )
        decisions.append((f"x{i:023d}", d))

    agg = aggregate_author(author_key="@someone", decisions=decisions)
    assert agg.strong_samples == 3
    assert agg.author_candidate is True
    assert agg.weak_article_ids == ()


def test_aggregate_does_not_touch_database():
    """⚠️ 聚合器**零 DB 依赖** —— 不 import 任何 db 模块, 不写任何表。"""
    import pathlib

    import moni.fetch.moderation as mod

    src = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    for forbidden in ("get_db", "sqlite3", "INSERT ", "UPDATE ", "DELETE "):
        assert forbidden not in src, f"moderation.py 不该出现 {forbidden!r}"


def test_downweighted_allow_is_not_a_weak_signal_hit():
    """⚠️ 降权放行 (``action=allow``) 的文章**不是**「弱特征命中」。

    口径写死: 弱特征命中 = ``quarantine`` **且** ``is_weak_only=True``。
    降权放行的文章本来就是「本来就干净」⇒ 不进 weak 集合, 也不进 strong 计数。
    """
    from moni.fetch.moderation import aggregate_author, evaluate_article as ea

    d = ea(
        title="家人们",
        content="今天聊聊分包之后主包体积怎么压，讨论了一些实现细节与取舍方案",
        author_name="某人",
        handle="@someone",
        author_clean_history_count=3,
        author_strong_sample_count=0,
    )
    assert d.action == "allow"
    assert d.is_weak_only is False

    agg = aggregate_author(author_key="@someone", decisions=[("y00000000000000000000001", d)])
    assert agg.weak_samples == 0
    assert agg.strong_samples == 0
    assert agg.author_candidate is False
