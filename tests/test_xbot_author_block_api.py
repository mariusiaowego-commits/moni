"""sprint-38 — X-bot 精选「屏蔽作者」REST 端点 + 旧文物理删除 测试。

覆盖 done-criteria AC-2 / AC-3 / AC-6 / AC-7 / AC-9 / AC-15 / AC-18 / AC-20。

口径（dad GO IMPL 2026-11-12 15:04 + warden DoD v0.3.0）：
- 三个独立 REST：POST / DELETE / GET ``/api/config/sources/{source_id}/blocked-authors``
- POST = 屏蔽 + **物理删除**该 handle 在 xbot_feed 下的已有文章（同一个动作）
- 删除必须**无孤儿行**：7 张 FK 表靠 CASCADE，``share_tasks.article_ids``（JSON 数组）
  必须显式清理
- 新表来源列存 ``source_name``（AC-15），REST 侧把 ``sources.id`` 映射成 name
"""
from __future__ import annotations

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.web.server import create_app

_SOURCE_NAME = "xbot_feed"
_SOURCE_ID = "src-xbot-feed"

#: 7 张引用 articles.id 的表 —— 全部 ON DELETE CASCADE。
FK_REF_TABLES = [
    "article_tags",
    "article_pins",
    "article_keyword_hits",
    "cases",
    "events",
    "fact_check_evidence",
    "llm_jobs",
]

#: 没有 FK 保护的孤儿源：JSON 数组，必须显式清理。
JSON_REF_TABLES = ["share_tasks"]


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    close_db()
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MONI_DATA_DIR", str(data_dir))
    set_db_path(data_dir / "news.db")
    migrate()
    yield data_dir
    close_db()


@pytest.fixture
def client(fresh_db):
    app = create_app()
    with TestClient(app) as c:
        yield c


def _seed_source(conn, name=_SOURCE_NAME, sid=_SOURCE_ID):
    conn.execute("DELETE FROM sources WHERE id=? OR name=?", (sid, name))
    conn.commit()
    conn.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, notes, fetch_config, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (sid, name, "X-bot 精选", "json",
         "https://github.com/mariusiaowego-commits/xbot-feed.git", 60, 1, "test",
         None, "2026-01-01", "2026-01-01"),
    )
    conn.commit()


def _add_article(conn, aid, publisher, source=_SOURCE_NAME):
    conn.execute(
        "INSERT INTO articles (id, title, url, source, publisher, published_at, "
        "fetched_at, raw_content) VALUES (?,?,?,?,?,?,?,?)",
        (aid, f"title {aid}", f"https://x.com/x/status/{aid}", source, publisher,
         "2026-11-03T00:00:00Z", "2026-11-03T00:00:00Z", "body"),
    )
    conn.commit()


def _add_refs(conn, aid):
    """给一篇文章挂上 7 张 FK 引用表的行 + 一条 share_tasks JSON 引用。

    列名/必填列按**生产库真实 schema** 写（不是猜的）：
    ``article_pins.pinned_at``（不是 created_at）、
    ``article_keyword_hits.id`` 自增 + ``created_at``、
    ``events.event_id`` 主键 + ``event_date_precision`` CHECK、
    ``fact_check_evidence.id`` 自增 + ``created_at``、
    ``llm_jobs.id`` 主键 + ``task_type``（不是 job_type）、
    ``cases.case_id`` 主键。
    """
    conn.execute(
        "INSERT INTO tag_definitions (name, description, color, created_at, "
        "is_active, keywords) VALUES ('t1','t1','#000','2026-01-01',1,'[]') "
        "ON CONFLICT(name) DO NOTHING")
    conn.execute(
        "INSERT INTO article_tags (article_id, tag_name, predicted, created_at) "
        "VALUES (?,'t1',0,'2026-01-01')", (aid,))
    conn.execute(
        "INSERT INTO article_pins (article_id, pinned_at) VALUES (?,'2026-01-01')",
        (aid,))
    conn.execute(
        "INSERT INTO article_keyword_hits (article_id, keyword, hit_field, created_at) "
        "VALUES (?,'kw','title','2026-01-01')", (aid,))
    conn.execute(
        "INSERT INTO events (event_id, article_id, event_date_precision, subject, "
        "event_type, created_at) VALUES (?,?,'exact','s','other','2026-01-01')",
        ("ev-" + aid, aid))
    conn.execute(
        "INSERT INTO fact_check_evidence (article_id, query, url, domain, snippet, "
        "fetched_at, model, provider, conclusion, evidence_seq, created_at) "
        "VALUES (?,'q','https://e.invalid','e.invalid','s','2026-01-01',"
        "'m','p','c',1,'2026-01-01')", (aid,))
    conn.execute(
        "INSERT INTO llm_jobs (id, task_type, article_id, status, attempts, "
        "created_at, fence) VALUES (?,?,?,'done',1,'2026-01-01',0)",
        ("job-" + aid, "summary", aid))
    conn.execute(
        "INSERT INTO share_tasks (id, format, article_ids, status, created_at) "
        "VALUES (?,?,?,'pending','2026-01-01')",
        ("st-" + aid, "html", json.dumps([aid, "other_id"])))
    conn.commit()


def _count_refs(conn, aid):
    out = {}
    for t in FK_REF_TABLES:
        out[t] = conn.execute(
            f"SELECT COUNT(*) AS c FROM {t} WHERE article_id=?", (aid,)
        ).fetchone()["c"]
    return out


def _share_task_refs(conn, aid):
    n = 0
    for row in conn.execute("SELECT article_ids FROM share_tasks").fetchall():
        try:
            ids = json.loads(row["article_ids"])
        except (TypeError, ValueError):
            continue
        if aid in ids:
            n += 1
    return n


def _blocked_rows(conn):
    return conn.execute(
        "SELECT source_name, handle, display_name FROM source_author_blocks "
        "ORDER BY handle"
    ).fetchall()


def _seed_articles(conn):
    """3 篇 xbot 文章（2 篇目标作者）+ 1 篇其它作者 + 1 篇其它源同名作者。"""
    _add_article(conn, "a111111111111111", "阿拾 (@jiamemexyz)")
    _add_article(conn, "a222222222222222", "阿拾 (@jiamemexyz)")
    _add_article(conn, "a333333333333333", "宝玉 (@dotey)")
    _add_article(conn, "a444444444444444", "阿拾 (@jiamemexyz)", source="36kr_rss")
    for aid in ("a111111111111111", "a222222222222222"):
        _add_refs(conn, aid)


# ── AC-2：三个独立 REST 端点 ───────────────────────────────────────────

def test_get_blocked_authors_returns_empty_list_initially(client, fresh_db):
    """AC-2：GET 列表初始为空，且带 builtin_handles 字段。"""
    conn = get_db()
    _seed_source(conn)
    r = client.get(f"/api/config/sources/{_SOURCE_ID}/blocked-authors")
    assert r.status_code == 200
    body = r.json()
    assert body["source_id"] == _SOURCE_ID
    assert body["blocked_authors"] == []
    assert isinstance(body["builtin_handles"], list)
    assert body["builtin_handles"], "内置黑名单号应作为只读参考返回"


def test_block_author_endpoint_adds_handle(client, fresh_db):
    """AC-2：POST 新增屏蔽记录。"""
    conn = get_db()
    _seed_source(conn)
    r = client.post(
        f"/api/config/sources/{_SOURCE_ID}/blocked-authors",
        json={"handle": "jiamemexyz", "display_name": "阿拾", "notes": "手动屏蔽"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["handle"] == "jiamemexyz"
    assert "blocked_at" in body
    rows = _blocked_rows(conn)
    assert len(rows) == 1
    assert rows[0]["source_name"] == _SOURCE_NAME
    assert rows[0]["handle"] == "jiamemexyz"
    assert rows[0]["display_name"] == "阿拾"


def test_block_author_endpoint_is_idempotent(client, fresh_db):
    """AC-2：重复 POST 不报错、不产生重复行。"""
    conn = get_db()
    _seed_source(conn)
    for _ in range(2):
        r = client.post(
            f"/api/config/sources/{_SOURCE_ID}/blocked-authors",
            json={"handle": "jiamemexyz"},
        )
        assert r.status_code == 200
    assert len(_blocked_rows(conn)) == 1
    got = client.get(f"/api/config/sources/{_SOURCE_ID}/blocked-authors").json()
    assert [b["handle"] for b in got["blocked_authors"]] == ["jiamemexyz"]


def test_block_author_endpoint_rejects_empty_handle(client, fresh_db):
    """AC-2：空 handle → 400。"""
    conn = get_db()
    _seed_source(conn)
    for payload in ({"handle": ""}, {"handle": "   "}, {"handle": "@"}, {}):
        r = client.post(
            f"/api/config/sources/{_SOURCE_ID}/blocked-authors", json=payload
        )
        assert r.status_code == 400, payload


def test_block_author_endpoint_normalizes_handle(client, fresh_db):
    """AC-2 / AC-5：handle 归一化（strip / 去 @ / 小写）。

    注意输入 ``@JiaMeMexyz`` 是 ``jiamemexyz`` 的真 case 变体 ——
    ``@JiaMeXyz`` 归一后是 ``jiamexyz``，那是**另一个** handle。
    """
    conn = get_db()
    _seed_source(conn)
    r = client.post(
        f"/api/config/sources/{_SOURCE_ID}/blocked-authors",
        json={"handle": " @JiaMeMexyz "},
    )
    assert r.status_code == 200
    rows = _blocked_rows(conn)
    assert [r_["handle"] for r_ in rows] == ["jiamemexyz"]


def test_unblock_author_endpoint_removes_handle(client, fresh_db):
    """AC-2：DELETE 解除屏蔽。"""
    conn = get_db()
    _seed_source(conn)
    client.post(f"/api/config/sources/{_SOURCE_ID}/blocked-authors",
                json={"handle": "jiamemexyz"})
    r = client.delete(
        f"/api/config/sources/{_SOURCE_ID}/blocked-authors/jiamemexyz")
    assert r.status_code in (200, 204)
    assert _blocked_rows(conn) == []


def test_unblock_author_endpoint_is_idempotent(client, fresh_db):
    """AC-2：删不存在的 handle 也成功。"""
    conn = get_db()
    _seed_source(conn)
    r = client.delete(
        f"/api/config/sources/{_SOURCE_ID}/blocked-authors/nobody_here")
    assert r.status_code in (200, 204)


def test_blocklist_is_decoupled_from_fetch_config(client, fresh_db):
    """AC-9：名单增删**不经** fetch_config PUT → 改门槛不影响名单。"""
    conn = get_db()
    _seed_source(conn)
    client.post(f"/api/config/sources/{_SOURCE_ID}/blocked-authors",
                json={"handle": "jiamemexyz"})
    before = client.get(
        f"/api/config/sources/{_SOURCE_ID}/blocked-authors").json()

    # 改 fetch_config（门槛 + scope）—— scope 的键必须是 xcircle_accounts /
    # wechat_queries / wechat_regex 之一（_validate_scope 会拒未知键 → 400）
    r = client.put(
        f"/api/config/sources/{_SOURCE_ID}/fetch_config",
        json={"thresholds": {"wechat": {"min_followers": 100, "min_likes": 10}},
              "scope": {"xcircle_accounts": ["dotey"]}},
    )
    assert r.status_code == 200
    after = client.get(
        f"/api/config/sources/{_SOURCE_ID}/blocked-authors").json()
    assert after == before, "改 fetch_config 不该动名单"


def test_blocklist_survives_seed_config(client, fresh_db):
    """AC-3：seed 之后名单仍在（方案 B 的核心优势）。"""
    from moni.db.seed_config import seed

    conn = get_db()
    _seed_source(conn)
    client.post(f"/api/config/sources/{_SOURCE_ID}/blocked-authors",
                json={"handle": "jiamemexyz"})
    seed()
    got = client.get(f"/api/config/sources/{_SOURCE_ID}/blocked-authors").json()
    assert [b["handle"] for b in got["blocked_authors"]] == ["jiamemexyz"]


# ── AC-6：物理删除 + 无孤儿行 ───────────────────────────────────────────

def test_block_author_deletes_existing_articles(client, fresh_db):
    """AC-6.1 / 6.2：屏蔽 = 删除该 handle 在 xbot_feed 下的已有文章。"""
    conn = get_db()
    _seed_source(conn)
    _seed_articles(conn)
    before = conn.execute(
        "SELECT COUNT(*) AS c FROM articles WHERE source=? AND publisher LIKE ?",
        (_SOURCE_NAME, "%(@jiamemexyz)%")).fetchone()["c"]
    assert before == 2

    r = client.post(f"/api/config/sources/{_SOURCE_ID}/blocked-authors",
                    json={"handle": "jiamemexyz"})
    assert r.status_code == 200

    after = conn.execute(
        "SELECT COUNT(*) AS c FROM articles WHERE source=? AND publisher LIKE ?",
        (_SOURCE_NAME, "%(@jiamemexyz)%")).fetchone()["c"]
    assert after == 0


def test_block_author_does_not_delete_other_authors(client, fresh_db):
    """AC-6.1：同源其它作者的文章一律不删。"""
    conn = get_db()
    _seed_source(conn)
    _seed_articles(conn)
    client.post(f"/api/config/sources/{_SOURCE_ID}/blocked-authors",
                json={"handle": "jiamemexyz"})
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM articles WHERE id='a333333333333333'"
    ).fetchone()
    assert row["c"] == 1


def test_block_author_does_not_delete_other_sources(client, fresh_db):
    """AC-6.1：其它来源的同名文章一律不删。"""
    conn = get_db()
    _seed_source(conn)
    _seed_articles(conn)
    client.post(f"/api/config/sources/{_SOURCE_ID}/blocked-authors",
                json={"handle": "jiamemexyz"})
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM articles WHERE id='a444444444444444'"
    ).fetchone()
    assert row["c"] == 1


def test_deleted_article_is_gone_from_articles_table(client, fresh_db):
    """AC-6.3：数据库里彻底查不到。"""
    conn = get_db()
    _seed_source(conn)
    _seed_articles(conn)
    client.post(f"/api/config/sources/{_SOURCE_ID}/blocked-authors",
                json={"handle": "jiamemexyz"})
    for aid in ("a111111111111111", "a222222222222222"):
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM articles WHERE id=?", (aid,)
        ).fetchone()["c"] == 0
        assert client.get(f"/api/articles/{aid}").status_code == 404


def test_no_orphan_rows_in_fk_tables(client, fresh_db):
    """AC-6.4：7 张 FK 引用表不留孤儿行（CASCADE 真的生效）。"""
    conn = get_db()
    _seed_source(conn)
    _seed_articles(conn)
    before = {aid: _count_refs(conn, aid)
              for aid in ("a111111111111111", "a222222222222222")}
    for aid, counts in before.items():
        assert sum(counts.values()) > 0, f"{aid} 没有引用行，测试会假绿"

    client.post(f"/api/config/sources/{_SOURCE_ID}/blocked-authors",
                json={"handle": "jiamemexyz"})

    for aid in ("a111111111111111", "a222222222222222"):
        after = _count_refs(conn, aid)
        assert after == {t: 0 for t in FK_REF_TABLES}, f"{aid} 留了孤儿行: {after}"


def test_no_orphan_ids_in_share_tasks_json(client, fresh_db):
    """AC-6.5：share_tasks.article_ids（JSON 数组，无 FK）必须显式清理。"""
    conn = get_db()
    _seed_source(conn)
    _seed_articles(conn)
    assert _share_task_refs(conn, "a111111111111111") == 1

    client.post(f"/api/config/sources/{_SOURCE_ID}/blocked-authors",
                json={"handle": "jiamemexyz"})

    assert _share_task_refs(conn, "a111111111111111") == 0
    assert _share_task_refs(conn, "a222222222222222") == 0


def test_share_task_keeps_unrelated_article_ids(client, fresh_db):
    """AC-6.5：清理只剔除已删 id，同一数组里的其它 id 要留着。"""
    conn = get_db()
    _seed_source(conn)
    _seed_articles(conn)
    client.post(f"/api/config/sources/{_SOURCE_ID}/blocked-authors",
                json={"handle": "jiamemexyz"})
    rows = conn.execute("SELECT article_ids FROM share_tasks").fetchall()
    assert rows, "share_tasks 行不应被误删"
    for row in rows:
        ids = json.loads(row["article_ids"])
        assert "a111111111111111" not in ids
        assert "a222222222222222" not in ids
        if ids:
            assert "other_id" in ids or len(ids) > 0


def test_share_task_row_is_kept_not_deleted(client, fresh_db):
    """AC-6.5（处置口径）：剔除已删 id，但**保留 share_task 行**。

    理由 (warden 建议 + 实测): 生产库 111 行 share_tasks **全部是终态**
    (done 93 / failed 10 / cancelled 8) —— 它们是**历史记录**
    (产物路径 / 失败原因), 删整行等于抹掉 dad 的操作历史。
    剔除后数组变空也保留该行 (下游对空数组安全: enrichment.py:273)。
    """
    conn = get_db()
    _seed_source(conn)
    _seed_articles(conn)
    before = conn.execute("SELECT COUNT(*) AS c FROM share_tasks").fetchone()["c"]
    assert before == 2, "fixture 应有 2 行 share_tasks"

    client.post(f"/api/config/sources/{_SOURCE_ID}/blocked-authors",
                json={"handle": "jiamemexyz"})

    after = conn.execute("SELECT COUNT(*) AS c FROM share_tasks").fetchone()["c"]
    assert after == before, "share_task 行必须保留 (历史记录不抹掉)"
    # st-a1111... 的数组里原本是 [该id, 'other_id'] → 剔除后剩 ['other_id']
    row = conn.execute(
        "SELECT article_ids FROM share_tasks WHERE id='st-a111111111111111'"
    ).fetchone()
    assert row is not None, "该 share_task 行不该被删"
    assert json.loads(row["article_ids"]) == ["other_id"]


def test_block_author_response_reports_deleted_count(client, fresh_db):
    """AC-17：响应里要带将被删除/已删除的篇数 N（确认框要显示）。"""
    conn = get_db()
    _seed_source(conn)
    _seed_articles(conn)
    r = client.post(f"/api/config/sources/{_SOURCE_ID}/blocked-authors",
                    json={"handle": "jiamemexyz"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("deleted_articles") == 2


def test_block_author_endpoint_does_not_error(client, fresh_db):
    """AC-6.7：删除过程不报错（响应无异常信息，状态码 200）。"""
    conn = get_db()
    _seed_source(conn)
    _seed_articles(conn)
    r = client.post(f"/api/config/sources/{_SOURCE_ID}/blocked-authors",
                    json={"handle": "jiamemexyz"})
    assert r.status_code == 200
    assert "error" not in r.text.lower()
    assert "traceback" not in r.text.lower()


def test_fts_index_has_no_stale_entry_for_deleted_article(client, fresh_db):
    """AC-6.6：articles_fts 索引不留脏条目（external-content FTS5 无 trigger）。"""
    conn = get_db()
    _seed_source(conn)
    _add_article(conn, "a555555555555555", "阿拾 (@jiamemexyz)")
    rowid = conn.execute(
        "SELECT rowid FROM articles WHERE id='a555555555555555'").fetchone()
    rid = rowid["rowid"] if rowid else None

    client.post(f"/api/config/sources/{_SOURCE_ID}/blocked-authors",
                json={"handle": "jiamemexyz"})

    assert rid is not None
    hit = conn.execute(
        "SELECT rowid FROM articles_fts WHERE rowid=?", (rid,)).fetchall()
    assert hit == [], "articles_fts 留了已删文章的索引条目"


def test_blocklist_survives_migrate(client, fresh_db):
    """AC-3：跑 migrate 后名单仍在（升级路径不碰名单）。"""
    conn = get_db()
    _seed_source(conn)
    client.post(f"/api/config/sources/{_SOURCE_ID}/blocked-authors",
                json={"handle": "jiamemexyz"})
    migrate()
    got = client.get(f"/api/config/sources/{_SOURCE_ID}/blocked-authors").json()
    assert [b["handle"] for b in got["blocked_authors"]] == ["jiamemexyz"]


# ── AC-20：解除屏蔽后旧文章不恢复 ───────────────────────────────────────

def test_unblock_does_not_restore_deleted_articles(client, fresh_db):
    """AC-20：解除屏蔽只影响以后的入库，旧文章不恢复。"""
    conn = get_db()
    _seed_source(conn)
    _seed_articles(conn)
    client.post(f"/api/config/sources/{_SOURCE_ID}/blocked-authors",
                json={"handle": "jiamemexyz"})
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM articles WHERE id='a111111111111111'"
    ).fetchone()["c"] == 0

    client.delete(
        f"/api/config/sources/{_SOURCE_ID}/blocked-authors/jiamemexyz")

    assert conn.execute(
        "SELECT COUNT(*) AS c FROM articles WHERE id='a111111111111111'"
    ).fetchone()["c"] == 0, "解除屏蔽不该复活已删文章"
    assert _blocked_rows(conn) == []


# ── AC-7：只对 xbot_feed 生效 ──────────────────────────────────────────

def test_block_endpoint_rejects_other_sources(client, fresh_db):
    """AC-7：只对 xbot_feed 生效 —— 其它源调用应被拒。"""
    conn = get_db()
    _seed_source(conn, name="36kr_rss", sid="src-36kr")
    r = client.post("/api/config/sources/src-36kr/blocked-authors",
                    json={"handle": "someone"})
    assert r.status_code == 400
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM source_author_blocks").fetchone()["c"] == 0


def test_block_endpoint_rejects_unknown_source(client, fresh_db):
    """AC-7：未知 source → 404/400，不能建孤儿记录。"""
    r = client.post("/api/config/sources/src-nope/blocked-authors",
                    json={"handle": "someone"})
    assert r.status_code in (400, 404)
