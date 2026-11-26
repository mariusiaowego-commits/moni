"""t119u — H1 清理工具（scripts/cleanup_xbot_scope_only.py）的安全边界。

覆盖：
* ``--dry-run`` 零写入；
* ``--apply`` 只动「恰好只有 xbot-scope」的条目；
* 命中 **source tag 池** 的条目**原地改挂**（去掉 xbot-scope、加业务 tag + 真实 hit）；
* 只命中 **topic tag 池** 的条目 → 进 ``review``（不删不改，历史条目没有 topic 字段）；
* 不命中的条目被删除，且 state 里**精确**退回对应 tweet id；
* 与新入库口径一致的三道过滤：纯回复 / 纯转推 / 源级黑名单 → 删；
* 有真实业务 tag 的历史贴 100% 不动；
* 屏蔽作者不复活（既不删也不改，且 id 仍留在 state 里）；
* DB 先 commit 成功，state 才被原子写（临时文件 + os.replace）；
* 第二次 ``--apply`` 是 no-op（可重复）。
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "cleanup_xbot_scope_only.py"
SOURCE_ID = "src-xbot-feed"
SOURCE_NAME = "xbot_feed"
BLOCKED_HANDLE = "manateelazycat"
BLACKLIST_WORD = "副业"


@pytest.fixture
def db(tmp_path, monkeypatch):
    close_db()
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MONI_DATA_DIR", str(data_dir))
    set_db_path(data_dir / "news.db")
    migrate()
    conn = get_db()
    # 业务 tag（source 池只勾 x-ai-circle；miniprogram 留给 topic 池）
    conn.execute(
        "INSERT OR REPLACE INTO tag_definitions "
        "(name, description, color, created_at, is_active, keywords) "
        "VALUES ('x-ai-circle','x','#6366F1','2026-01-01',1,?)",
        (json.dumps(["Claude Code", "MCP"], ensure_ascii=False),),
    )
    conn.execute(
        "INSERT OR REPLACE INTO tag_definitions "
        "(name, description, color, created_at, is_active, keywords) "
        "VALUES ('miniprogram','mp','#000000','2026-01-01',1,?)",
        (json.dumps(["小程序"], ensure_ascii=False),),
    )
    conn.execute(
        "INSERT OR REPLACE INTO tag_definitions "
        "(name, description, color, created_at, is_active, keywords) "
        "VALUES ('miniprogram-hist','mp-hist','#000000','2026-01-01',1,'[]')"
    )
    # source 池：**只**勾 x-ai-circle
    conn.execute("DELETE FROM source_tags WHERE source_id = ?", (SOURCE_ID,))
    conn.execute(
        "INSERT OR IGNORE INTO source_tags (source_id, tag_name, created_at) "
        "VALUES (?,?,'2026-01-01')",
        (SOURCE_ID, "x-ai-circle"),
    )
    conn.execute(
        "UPDATE sources SET fetch_config = ? WHERE name = ?",
        (json.dumps({
            "business_tags_from_topic_tags": True,
            "business_tags_from_source_tags": True,
            "exclude_replies": True,
            "exclude_retweets": True,
            "topic_tags": {},
        }), SOURCE_NAME),
    )
    # 源级黑名单（与新入库口径一致）
    conn.execute(
        "INSERT OR REPLACE INTO source_keyword_filters "
        "(id, source_id, mode, match_field, keywords, is_active, created_at, updated_at) "
        "VALUES (150, ?, 'blacklist', 'title+content', ?, 1, '2026-01-01', '2026-01-01')",
        (SOURCE_ID, json.dumps([BLACKLIST_WORD], ensure_ascii=False)),
    )
    blocked_id = "2120188271678591093"
    keep_id = "1111111111111111111"
    hit_id = "2222222222222222222"
    rows = [
        # (id, url, title, raw_content, publisher, tags)
        ("a-blocked", f"https://x.com/{BLOCKED_HANDLE}/status/{blocked_id}",
         "被屏蔽作者", "Claude Code 相关", f"Andy (@{BLOCKED_HANDLE})"),
        ("a-keep", f"https://x.com/someone/status/{keep_id}",
         "历史业务贴", "小程序 上线", "x (@someone)"),
        ("a-hit", f"https://x.com/dotey/status/{hit_id}",
         "Claude Code 实践", "用 MCP 串起来", "歸藏 (@dotey)"),
        ("a-drop", "https://x.com/random/status/3333333333333333333",
         "跟行业无关", "今天吃了火锅", "r (@random)"),
        ("a-reply", "https://x.com/replyer/status/4444444444444444444",
         "@someone Claude Code 真香", "@someone 这个 Claude Code 真香", "rp (@replyer)"),
        ("a-rt", "https://x.com/rtper/status/5555555555555555555",
         "RT @dotey: Claude Code 真香", "RT @dotey: Claude Code 真香", "rt (@rtper)"),
        ("a-black", "https://x.com/blackper/status/6666666666666666666",
         "副业笔记 Claude Code", "副业笔记 Claude Code", "b (@blackper)"),
        ("a-topic", "https://x.com/topicper/status/7777777777777777777",
         "小程序新玩法", "小程序新玩法", "t (@topicper)"),
    ]
    for aid, url, title, body, publisher in rows:
        conn.execute(
            "INSERT INTO articles (id, title, url, source, publisher, raw_content, "
            "fetched_at) VALUES (?,?,?,?,?,?,'2026-11-26T08:31:00+00:00')",
            (aid, title, url, SOURCE_NAME, publisher, body),
        )
    # 纯 xbot-scope：除 a-keep 外全部；a-keep 有真实业务 tag
    for aid in ("a-hit", "a-drop", "a-blocked", "a-reply", "a-rt", "a-black",
                "a-topic"):
        conn.execute(
            "INSERT INTO article_tags (article_id, tag_name, created_at) "
            "VALUES (?, 'xbot-scope', '2026-11-26T08:31:00+00:00')",
            (aid,),
        )
    conn.execute(
        "INSERT INTO article_tags (article_id, tag_name, created_at) "
        "VALUES ('a-keep', 'miniprogram-hist', '2026-11-20T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO source_author_blocks (source_name, handle, "
        "display_name, blocked_at) VALUES (?,?,?,'2026-11-26 08:36:58')",
        (SOURCE_NAME, BLOCKED_HANDLE, "Andy Stewart"),
    )
    conn.commit()
    state = {"imported_ids": [
        blocked_id, keep_id, hit_id, "3333333333333333333",
        "4444444444444444444", "5555555555555555555",
        "6666666666666666666", "7777777777777777777",
    ]}
    (data_dir / ".xbot_feed_state.json").write_text(
        json.dumps(state), encoding="utf-8")
    yield data_dir
    close_db()


def _load_tool():
    spec = importlib.util.spec_from_file_location("cleanup_tool", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _tags(article_id: str) -> list[str]:
    return sorted(r[0] for r in get_db().execute(
        "SELECT tag_name FROM article_tags WHERE article_id = ?", (article_id,)))


def _exists(article_id: str) -> bool:
    return get_db().execute(
        "SELECT 1 FROM articles WHERE id = ?", (article_id,)).fetchone() is not None


def _state_ids() -> list[str]:
    path = Path(os.environ["MONI_DATA_DIR"]) / ".xbot_feed_state.json"
    return json.loads(path.read_text(encoding="utf-8"))["imported_ids"]


def _by_url(summary: dict, bucket: str) -> dict[str, dict]:
    return {item["url"].rsplit("/", 1)[-1]: item for item in summary[bucket]}


def test_dry_run_writes_nothing(db, capsys):
    tool = _load_tool()
    before_tags = {a: _tags(a) for a in
                   ("a-hit", "a-drop", "a-blocked", "a-keep", "a-reply", "a-rt",
                    "a-black", "a-topic")}
    before_state = _state_ids()

    rc = tool.main([])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "dry-run"
    assert out["targets"] == 7
    assert set(_by_url(out, "retag")) == {"2222222222222222222"}
    assert set(_by_url(out, "review")) == {"7777777777777777777"}
    deleted = _by_url(out, "delete")
    assert set(deleted) == {"3333333333333333333", "4444444444444444444",
                            "5555555555555555555", "6666666666666666666"}
    assert deleted["4444444444444444444"]["reason"] == "reply"
    assert deleted["5555555555555555555"]["reason"] == "retweet"
    assert deleted["6666666666666666666"]["reason"] == "source_blacklist"
    assert deleted["3333333333333333333"]["reason"] == "no_business_tag"
    assert len(out["skipped_blocked"]) == 1

    assert {a: _tags(a) for a in before_tags} == before_tags
    assert _state_ids() == before_state
    assert _exists("a-drop")


def test_apply_retags_hit_deletes_miss_and_fixes_state(db, capsys):
    tool = _load_tool()
    rc = tool.main(["--apply"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "apply"
    assert "backup" in out and Path(out["backup"]["db"]).exists()

    # 命中的：xbot-scope 去掉、业务 tag 挂上、真实 keyword hit 写进库
    assert _tags("a-hit") == ["x-ai-circle"]
    hits = get_db().execute(
        "SELECT keyword, hit_field FROM article_keyword_hits WHERE article_id = 'a-hit'"
    ).fetchall()
    assert hits and hits[0]["hit_field"] == "title+content"

    # 不命中的 / 形态过滤 / 黑名单：全删
    for aid in ("a-drop", "a-reply", "a-rt", "a-black"):
        assert not _exists(aid), f"{aid} 应被删除"
    # 只命中 topic 池：review（不删不改）
    assert _exists("a-topic")
    assert _tags("a-topic") == ["xbot-scope"]
    # 屏蔽作者：既不删也不改
    assert _exists("a-blocked")
    assert _tags("a-blocked") == ["xbot-scope"]
    # 历史业务贴：完全不动
    assert _tags("a-keep") == ["miniprogram-hist"]

    # state：只退回被删条目的 id；屏蔽作者、保留条目、review 条目都还在
    ids = _state_ids()
    for gone in ("3333333333333333333", "4444444444444444444",
                 "5555555555555555555", "6666666666666666666"):
        assert gone not in ids
    assert "2120188271678591093" in ids, "屏蔽作者的 id 绝不能被清掉"
    assert "1111111111111111111" in ids
    assert "2222222222222222222" in ids
    assert "7777777777777777777" in ids, "review 条目不进 state 退回"


def test_state_written_after_commit_and_atomically(db, capsys):
    """pV P1-1：DB 先 commit，state 再原子写（临时文件 + os.replace）。"""
    tool = _load_tool()
    assert tool.main(["--apply"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["state_written_after_commit"] is True
    assert out["state_removed_ids"] == 4
    # 原子写不留临时文件（.tmp）；备份文件（.pre-cleanup-）是**故意**留的
    data_dir = Path(os.environ["MONI_DATA_DIR"])
    assert not list(data_dir.glob(".xbot_feed_state.json.tmp"))
    assert list(data_dir.glob(".xbot_feed_state.json.pre-cleanup-*"))


def test_apply_is_repeatable(db, capsys):
    tool = _load_tool()
    assert tool.main(["--apply"]) == 0
    capsys.readouterr()
    assert tool.main(["--apply"]) == 0
    out = json.loads(capsys.readouterr().out)
    # 第二次：屏蔽作者被跳过、review 条目仍待复核 —— 没有新的改挂 / 删除
    assert out["retag"] == [] and out["delete"] == []
    assert out["state_removed_ids"] == 0
