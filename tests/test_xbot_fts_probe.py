"""sprint-54 / id 26111703 —— I9 只读 FTS 探针的护栏。

覆盖 p7 的 **C-3**：

* 中文 token 取法 = **空白切分的整段连续串**（禁正则子串）
* **不可判定 rowid 必须报出**（不许静默跳过）
* 静态扫描**只扫本 sprint 新增脚本**（不扫全仓）
* 只读：禁 FTS integrity 写命令 / rebuild / repair

⚠️ 本文件只读 —— 不写生产库、不改生产配置。
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "verify_fts_index_criteria.py"


def _load():
    spec = importlib.util.spec_from_file_location("_verify_fts", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod():
    return _load()


@pytest.fixture()
def fts_db(tmp_path):
    """建一个带 articles + articles_fts 的**临时**库。"""
    from datetime import datetime, timezone

    db = tmp_path / "fts.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE articles ("
        " id TEXT PRIMARY KEY, title TEXT, publisher TEXT, "
        " raw_content TEXT, summary TEXT, source TEXT)"
    )
    conn.execute(
        "CREATE VIRTUAL TABLE articles_fts USING fts5("
        " title, publisher, raw_content, summary,"
        " content='articles', content_rowid='rowid')"
    )
    rows = [
        ("a1", "微信小程序上线", "@u1", "我们做了个微信小程序", None),
        ("a2", "纯中文标题", "@u2", "这是一段完整的中文正文内容", None),
        ("a3", "---", "@u3", "---", None),                 # 全是纯标点 ⇒ 无 token
        ("a4", "", "@u4", None, None),                     # 全空 ⇒ 无 token
        ("a5", '带"直引号"的标题', "@u5", "正文有引号", None),
    ]
    for aid, title, pub, body, summ in rows:
        conn.execute(
            "INSERT INTO articles (id, title, publisher, raw_content, summary, source) "
            "VALUES (?, ?, ?, ?, ?, 'xbot_feed')",
            (aid, title, pub, body, summ),
        )
        # ⚠️ external-content FTS: **必须带列值**才能写进索引
        #    (只 INSERT (rowid) ⇒ 索引里一个词条都没有 ⇒ vocab 扫不到)
        conn.execute(
            "INSERT INTO articles_fts (rowid, title, publisher, raw_content, summary) "
            "VALUES ((SELECT rowid FROM articles WHERE id = ?), ?, ?, ?, ?)",
            (aid, title, pub, body, summ),
        )
    conn.commit()
    conn.close()
    return db


# ── R11: 中文 token 取法 ────────────────────────────────────────────────────

def test_split_segments_is_whitespace_based(mod):
    """⚠️ 取法 = **空白切分的整段连续串**。"""
    segs = mod.split_segments("微信小程序 上线 很好")
    assert segs == ["微信小程序", "上线", "很好"]


def test_split_segments_keeps_whole_run_not_substring(mod):
    """⚠️ **不**做中文分词 ⇒ 整段连续串是**一个** 段。

    ⚠️ 「我们做了个微信小程序」整体是**一段** ⇒ 段里**没有**「小程序」这一段。
    ⇒ 若探针用正则抠子串, 就会造出 FTS 里**不存在**的 token ⇒ 假绿。
    """
    segs = mod.split_segments("我们做了个微信小程序")
    assert segs == ["我们做了个微信小程序"]
    assert "小程序" not in segs
    assert "用户可扫" not in segs


def test_split_segments_does_not_use_regex_substrings(mod):
    """⚠️ 源码里**不得**出现「按长度抠子串」的正则（如 ``[一-鿿]{4,}``）。"""
    src = _SCRIPT.read_text(encoding="utf-8")
    body = mod.code_only(src)
    assert "{4,}" not in body
    assert "{2,}" not in body


def test_pure_punctuation_segment_is_not_a_token(mod):
    """⚠️ ``unicode61`` 丢掉纯标点段 ⇒ 不算「取不到 token」。"""
    assert mod.is_token_candidate("微信小程序") is True
    assert mod.is_token_candidate("AI") is True
    assert mod.is_token_candidate("---") is False
    assert mod.is_token_candidate("——") is False
    assert mod.is_token_candidate("“") is False


# ── 引号转义 ────────────────────────────────────────────────────────────────

def test_fts_quote_doubles_internal_quotes(mod):
    """⚠️ 内部 ``"`` 必须翻倍 ⇒ 否则 FTS 解析器把引号当语法符。"""
    assert mod.fts_quote('带"引号"') == '"带""引号"""'


def test_article_with_quotes_is_not_false_reported(mod, fts_db):
    """⚠️ **回归**：标题带直引号的文章**不得**被假报成「取不到 token」。"""
    conn = mod.open_readonly(str(fts_db))
    try:
        res = mod.check_article_tokens(conn)
    finally:
        conn.close()
    # ⚠️ a5 标题含中文 + 直引号 ⇒ 有 token ⇒ **不该**出现在 no_token 里
    assert 5 not in res["articles_with_no_token"], "带引号的文章被假报了"
    assert 5 not in res["articles_with_unindexed_token"], "带引号的文章被假报成未索引"
    assert res["query_error_count"] == 0


# ── 不可判定 rowid 必须报出 ────────────────────────────────────────────────

def test_reports_articles_with_no_token(mod, fts_db):
    """⚠️ 取不到 token 的文章**必须报出 rowid**，不许静默跳过。"""
    conn = mod.open_readonly(str(fts_db))
    try:
        res = mod.check_article_tokens(conn)
    finally:
        conn.close()
    # a3 (全是纯标点) 与 a4 (标题空 + 正文 NULL) ⇒ 无 token
    assert set(res["articles_with_no_token"]) == {3, 4}, res["articles_with_no_token"]
    assert res["articles_with_no_token_count"] == 2


def test_reports_orphan_fts_rowids(mod, fts_db):
    """⚠️ FTS 索引里有、但 articles 里映射不回的 rowid ⇒ **必须报出**。

    ⚠️ **external-content 的坑**：
    ``articles_fts`` 是 ``content='articles'`` ⇒ 对它 ``SELECT`` 时
    SQLite **去 articles 取列值** ⇒ 索引里多出来的 rowid **根本查不到**
    ⇒ 用 ``LEFT JOIN articles_fts`` **永远查不出孤儿**。

    ⇒ 正确办法 = ``fts5vocab('instance')`` 读索引自身。
    ⚠️ 建 vocab 表是 **DDL（写命令）** ⇒ 必须在**临时副本**上做
    ⇒ 本探针用 :func:`vocab_scan_on_copy`（只写副本, 源只读）。
    """
    # 先在副本上验证 vocab 能看到孤儿
    conn = sqlite3.connect(str(fts_db))
    # ⚠️ 必须带列值 ⇒ 索引里才有 9999 的词条 ⇒ vocab 才看得到孤儿
    conn.execute(
        "INSERT INTO articles_fts (rowid, title, publisher, raw_content, summary) "
        "VALUES (9999, '孤儿行', '@x', '孤儿正文', NULL)"
    )
    conn.commit()
    conn.close()

    scan = mod.vocab_scan_on_copy(str(fts_db))
    assert scan["scanned_on"] == "temp_copy"
    assert scan["orphan_fts_rowid_count"] >= 1, scan
    assert 9999 in scan["orphan_fts_rowids"], scan


def test_vocab_scan_does_not_write_to_source(fts_db):
    """⚠️ vocab 扫描**只写副本** ⇒ 源库字节不变。"""
    before = fts_db.read_bytes()
    mod = _load()
    mod.vocab_scan_on_copy(str(fts_db))
    assert fts_db.read_bytes() == before, "vocab 扫描写了源库"


def test_vocab_scan_reports_articles_without_index_entry(mod, fts_db):
    """⚠️ articles 里有、索引里没有的 rowid ⇒ 报出 (索引与正文不同步)。"""
    conn = sqlite3.connect(str(fts_db))
    conn.execute(
        "INSERT INTO articles (id, title, publisher, raw_content, summary, source) "
        "VALUES ('a9', '未被索引的标题', '@u9', '正文', NULL, 'xbot_feed')"
    )
    conn.commit()
    conn.close()

    scan = mod.vocab_scan_on_copy(str(fts_db))
    assert scan["articles_without_index_entry_count"] >= 1, scan


def test_reports_unindexed_tokens(mod, fts_db):
    """⚠️ token 取到但 MATCH 定位不回本篇 ⇒ 报出（索引与正文不同步）。

    ⚠️ external-content FTS 的 ``SELECT`` 读 **articles** ⇒ 要造出
    「索引不同步」必须**改 articles 的正文但不同步 FTS 索引** ——
    改完后 MATCH 用**新** token 查不到 ⇒ 命中 0 ⇒ 被检出。
    """
    conn = sqlite3.connect(str(fts_db))
    # ⚠️ 改 articles 正文 (FTS 索引里还是旧的) ⇒ 索引与正文不同步
    conn.execute(
        "UPDATE articles SET raw_content = '全新的正文内容根本不在索引里', title = '全新标题' "
        "WHERE id = 'a2'"
    )
    conn.commit()
    conn.close()

    conn = mod.open_readonly(str(fts_db))
    try:
        res = mod.check_article_tokens(conn)
    finally:
        conn.close()
    assert res["articles_with_unindexed_token_count"] >= 1, res
    assert 2 in res["articles_with_unindexed_token"], res


def test_never_self_heals(mod, fts_db):
    """⚠️ 探针**只报告**，不 rebuild / 不 repair。"""
    conn = mod.open_readonly(str(fts_db))
    try:
        res = mod.check_article_tokens(conn)
    finally:
        conn.close()
    assert res["self_healed"] is False
    assert res["read_only"] is True


# ── 只读边界 ────────────────────────────────────────────────────────────────

def test_probe_source_has_no_write_commands():
    """⚠️ **静态断言**：探针源码里不得出现 FTS 写命令。"""
    src = _SCRIPT.read_text(encoding="utf-8")
    mod = _load()
    body = mod.code_only(src, skip_assignments=("forbidden",))
    for forbidden in ("integrity-check", "'rebuild'", "'repair'", "VACUUM"):
        assert forbidden not in body, f"探针不该出现 {forbidden!r}"


def test_static_scan_scope_is_only_this_sprint_scripts(mod):
    """⚠️ **静态扫描范围**只限本 sprint 新增脚本 —— 不扫全仓。"""
    res = mod.check_static_write_safety(_REPO_ROOT / "scripts")
    assert res["clean"] is True, res["violations"]
    for name in res["checked_scripts"]:
        assert name.startswith("verify_fts") or name.startswith("xbot_quality")
    assert "author_blocks.py" not in res["checked_scripts"]


def test_probe_does_not_write_to_db(mod, fts_db):
    """⚠️ 跑完探针, DB 字节**不变**。"""
    before = fts_db.read_bytes()
    conn = mod.open_readonly(str(fts_db))
    try:
        mod.check_article_tokens(conn)
    finally:
        conn.close()
    assert fts_db.read_bytes() == before, "探针动了 DB"


def test_open_readonly_sets_query_only(mod, fts_db):
    """⚠️ 连接必须 ``mode=ro`` + ``PRAGMA query_only=ON``。"""
    conn = mod.open_readonly(str(fts_db))
    try:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO articles (id) VALUES ('x')")
    finally:
        conn.close()


# ── P2-1（p7 最终 diff 复核）: `--skip-vocab-scan` 的「未检查」语义必须被钉住 ──

def test_skip_vocab_scan_reports_not_checked_not_zero(mod, fts_db):
    """⚠️ **P2-1**：跳过 vocab 扫描时必须显示「**未检查**」，**不能**显示 0。

    ⚠️ 理由：`articles_fts` 是 external-content ⇒ 对它 `SELECT` 读的是
    `articles` ⇒ **索引里的孤儿 rowid 根本查不到** ⇒ 用 `LEFT JOIN` 查孤儿
    是**假绿**。所以「跳过 vocab 扫描」得到的 0 **不是「查过且为 0」**，
    而是「**根本没查**」⇒ 若报成 0 就是拿「没查」冒充「查过且为 0」。

    ⚠️ p7 实测：本用例写之前，M1（skip 分支 ``None → 0``）与
    M2（渲染层「未检查」→ ``0``）两条变异**全部逃逸**（无测试变红）⇒
    下一个实施方可能把它改回 0 而无人察觉。
    ⇒ 本用例把语义**钉死**：谁改回去，谁立刻红。
    """
    import contextlib
    import io
    import json as _json

    # ⚠️ argparse 的 argv **不含**程序名
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main(["--db", str(fts_db), "--skip-vocab-scan", "--json"])
    assert rc == 0
    fts = _json.loads(buf.getvalue())["fts"]

    # ⚠️ 必须是「未检查」语义 —— **不是 0**
    assert fts["fts_orphan_checked"] is False
    assert fts["fts_orphan_rowid_count"] is None, "跳过后孤儿数必须是 None（未检查），不能是 0"
    assert fts["articles_without_index_entry_count"] is None
    assert fts["fts_orphan_rowids"] == []
    assert fts["vocab"]["skipped"] is True
    assert fts["vocab"]["scanned_on"] == "skipped"
    assert "未检查" in fts["vocab"]["note"]


def test_skip_vocab_scan_text_output_says_not_checked(mod, fts_db):
    """⚠️ **P2-1（渲染层）**：文本报告也必须显示「未检查」，不能显示 0。

    ⚠️ p7 实测 M2（渲染层「未检查」→ 0）**逃逸** ⇒ 本用例钉住渲染层。
    """
    import contextlib
    import io
    import json as _json

    # ⚠️ argparse 的 argv **不含**程序名
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main(["--db", str(fts_db), "--skip-vocab-scan", "--json"])
    assert rc == 0
    fts = _json.loads(buf.getvalue())["fts"]

    static = mod.check_static_write_safety(Path(_SCRIPT).parent)
    text = mod.render_text(fts, static)
    assert "未检查" in text
    # ⚠️ 不能出现「孤儿 rowid : 0」这种把「没查」说成「查过且为 0」的措辞
    assert "孤儿 rowid               : 0" not in text


def test_default_mode_still_checks_vocab(mod, fts_db):
    """⚠️ **P2-1**：默认**必须仍跑** vocab 扫描（跳过只是可选开关，不是默认）。

    ⚠️ p7 实测 M3（开关整个失效）与 M4（跳过时仍扫描）**逃逸** ⇒ 本用例钉住默认值。
    """
    import contextlib
    import io
    import json as _json

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.main(["--db", str(fts_db), "--json"])
    assert rc == 0
    fts = _json.loads(buf.getvalue())["fts"]
    assert fts["vocab"]["skipped"] is False
    assert fts["vocab"]["scanned_on"] == "temp_copy"
    assert fts["fts_orphan_checked"] is True
    assert fts["fts_orphan_rowid_count"] == 0
