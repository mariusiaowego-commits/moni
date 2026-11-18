#!/usr/bin/env python3
"""sprint-54 / id 26111703 —— I9：FTS 索引**只读**验收探针。

## 它检查什么

对 ``articles_fts``（external-content FTS5，``content='articles'``，
``content_rowid='rowid'``）逐篇比对：**文章按 FTS 口径切出的 token**，
在 FTS 索引里**能不能查到**、且能否**定位回该篇自己的 rowid**。

## ⚠️ 中文 token 口径（PLAN R11 / C-3）

FTS5 的 ``unicode61`` tokenizer 按**空白**切分，**不做**中文分词 ⇒
「微信小程序」是**一个** token，「小程序」**不是**。

⇒ 本探针的切词口径 = **按空白切分的整段连续串**，
**严禁**用正则去正文里抠子串（那会造出 FTS 根本不存在的 token，
并造成**假绿** —— R11 实测 ``MATCH '"用户可扫"'`` → 0 命中，而整段 → 命中 rowid=3）。

⚠️ ``unicode61`` 会**丢掉纯标点段**（``---`` / ``——`` / ``“”``）
⇒ 纯标点段**不是** token ⇒ 不得计入「取不到 token」。

## ⚠️ 三类异常**全部**必须报出 rowid 清单（C-3，不许静默跳过）

1. ``articles_with_no_token`` —— 该篇**取不到任何 token**
   （可搜索文本全空，或全是纯标点）
2. ``articles_with_unindexed_token`` —— token 取到了，但 MATCH 定位不回本篇
   ⇒ **索引与正文不同步**（external-content 表**无 trigger** ⇒ 会发生）
3. ``fts_orphan_rowids`` —— FTS 里有、但 ``articles`` 里映射不回的 rowid

## ⚠️ 查询串必须转义双引号

FTS5 短语语法里内部 ``"`` 要翻倍成 ``""`` ⇒ 否则解析器把引号当语法符，
查询被拆坏 ⇒ **假报**「取不到 token」。

## ⚠️ 只读边界

* 只跑 ``SELECT`` / 只读 ``PRAGMA``；
* **禁止** FTS5 的完整性检查命令（它是 INSERT 语法 ⇒ **写命令**）、
  **禁止** ``rebuild`` / ``repair`` / ``optimize``；
* 生产库只 ``mode=ro`` + ``PRAGMA query_only=ON``；
* **不**自愈 —— 只**报告**异常。

用法::

    python3 scripts/verify_fts_index_criteria.py --db <db>            # 报告
    python3 scripts/verify_fts_index_criteria.py --db <db> --json
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

#: FTS5 ``unicode61`` 按空白切分 ⇒ 本探针用**同样的**口径。
#:
#: ⚠️ **不做**中文分词; **不**用正则抠子串 (PLAN R11 / C-3)。
_TOKEN_SPLIT_RE = re.compile(r"[\s\u3000]+")

#: FTS5 短语查询里必须把内部双引号翻倍
_QUOTE = '"'


def fts_quote(token: str) -> str:
    """把 token 安全地包成 FTS5 短语查询。

    ⚠️ 内部双引号**必须**翻倍 —— 否则 FTS 解析器把 ``"`` 当语法符,
    查询被拆坏 ⇒ 假报「取不到 token」。
    """
    return _QUOTE + token.replace(_QUOTE, _QUOTE * 2) + _QUOTE


def split_segments(text: str) -> list[str]:
    """按**空白**切分文本 ⇒ 得到 FTS5 ``unicode61`` 口径的候选段。

    ⚠️ PLAN R11 规定的口径：**整段连续串**。
    ⚠️ **不得**用正则从正文里抠子串。
    """
    if not text:
        return []
    out = []
    for part in _TOKEN_SPLIT_RE.split(text):
        part = part.strip()
        if part:
            out.append(part)
    return out


def is_token_candidate(segment: str) -> bool:
    """该段是否**可能**是 FTS token。

    ⚠️ ``unicode61`` 丢掉**纯标点**段 ⇒ ``---`` / ``——`` / ``“”``
    根本不是 token ⇒ 不该算「取不到 token」。
    ⇒ 判据: 段里至少有一个**字母或数字**。
    """
    return any(ch.isalnum() for ch in segment)


def open_readonly(db_path: str) -> sqlite3.Connection:
    """**只读**连接（``mode=ro`` + ``PRAGMA query_only=ON``）。"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def collect_orphan_fts_rows(conn: sqlite3.Connection) -> list[dict]:
    """找出**映射不回** articles 的 FTS rowid。

    ⚠️ **external-content FTS 的陷阱**：
    ``articles_fts`` 是 ``content='articles'`` ⇒ 对它做 ``SELECT`` 时
    SQLite **去 articles 表取列值** ⇒ **索引里多出来的 rowid 根本查不到**
    ⇒ 用 ``LEFT JOIN articles_fts`` **永远查不出孤儿**。

    ⇒ 唯一正确办法是读 **FTS 索引自身** ⇒ ``fts5vocab('instance')``。
    ⚠️ 但建 vocab 表是 **DDL（写命令）** ⇒ **生产禁止**。
    ⇒ 本函数在**临时副本**上跑 (见 :func:`vocab_scan_on_copy`)。

    ⚠️ 返回 ``[]`` 且带 ``note`` ⇒ 表示「本次未做 vocab 扫描」。
    """
    return []


def vocab_scan_on_copy(db_path: str) -> dict:
    """在**临时副本**上用 ``fts5vocab('instance')`` 扫描 FTS 索引自身。

    ⚠️ **为什么必须在副本上**：

    * ``fts5vocab`` 是**虚拟表** ⇒ 建它就是 **DDL（写命令）**
      ⇒ 生产库上**禁止**；
    * ``articles_fts`` 是 external-content ⇒ 直接 ``SELECT`` 看不到索引里的
      孤儿 rowid ⇒ 必须从 vocab 表读索引。

    ⇒ 用 SQLite **在线 backup** 把生产库复制到临时文件（**只写目标**），
    在副本上建 vocab 表 ⇒ **生产库零写入**。

    返回::

        {
          "indexed_rowid_count": N,
          "orphan_fts_rowids": [...],       # 索引里有、articles 里没有
          "articles_without_index_entry": [...],  # articles 有、索引里没有
          "scanned_on": "temp_copy",
        }
    """
    import shutil
    import tempfile

    tmpdir = tempfile.mkdtemp(prefix="moni-fts-probe-")
    copy_path = os.path.join(tmpdir, "probe.db")
    try:
        # ⚠️ 在线 backup: 只写目标, 源只读
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        src.execute("PRAGMA query_only=ON")
        dst = sqlite3.connect(copy_path)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
            src.close()

        conn = sqlite3.connect(copy_path)
        conn.execute(
            "CREATE VIRTUAL TABLE v_fts_vocab USING fts5vocab(articles_fts, 'instance')"
        )
        indexed = {r[0] for r in conn.execute("SELECT DISTINCT doc FROM v_fts_vocab")}
        articles = {r[0] for r in conn.execute("SELECT rowid FROM articles")}
        orphans = sorted(indexed - articles)
        missing = sorted(articles - indexed)
        conn.close()
        return {
            "scanned_on": "temp_copy",
            # ⚠️ 与 skip 分支**同形** (两条路径的键集必须一致 ⇒ 便于测试钉住)
            "skipped": False,
            "indexed_rowid_count": len(indexed),
            "articles_rowid_count": len(articles),
            "orphan_fts_rowids": orphans,
            "articles_without_index_entry": missing,
            "orphan_fts_rowid_count": len(orphans),
            "articles_without_index_entry_count": len(missing),
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def check_article_tokens(
    conn: sqlite3.Connection, *, source: str | None = None, limit: int | None = None
) -> dict:
    """逐篇比对：文章 token 在 FTS 里能不能定位回本篇。"""
    sql = (
        "SELECT a.rowid AS a_rowid, a.id AS a_id, a.title, a.raw_content "
        "FROM articles a"
    )
    params: list = []
    if source:
        sql += " WHERE a.source = ?"
        params.append(source)
    sql += " ORDER BY a.rowid"
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))

    articles = conn.execute(sql, params).fetchall()

    no_token: list[int] = []
    unindexed: list[int] = []
    missing_detail: list[dict] = []
    query_errors: list[dict] = []
    checked = 0

    for a in articles:
        a_rowid = a["a_rowid"]
        title = a["title"] or ""
        body = a["raw_content"] or ""

        segments = [
            s for s in split_segments(f"{title}\n{body}") if is_token_candidate(s)
        ]
        seen: list[str] = []
        for s in segments:
            if s not in seen:
                seen.append(s)
        segments = seen[:12]

        if not segments:
            # ⚠️ 该篇取不到任何 token ⇒ 必须报出
            no_token.append(a_rowid)
            checked += 1
            continue

        hit = False
        for seg in segments:
            try:
                n = conn.execute(
                    "SELECT count(*) FROM articles_fts "
                    "WHERE articles_fts MATCH ? AND rowid = ?",
                    (fts_quote(seg), a_rowid),
                ).fetchone()[0]
            except sqlite3.OperationalError as exc:
                # FTS 语法不接受 ⇒ 记为**不可判定**, 不静默吞
                query_errors.append(
                    {"rowid": a_rowid, "token": seg[:80], "error": str(exc)[:200]}
                )
                continue
            if n:
                hit = True
                break

        if not hit:
            unindexed.append(a_rowid)
            if len(missing_detail) < 500:
                missing_detail.append(
                    {
                        "rowid": a_rowid,
                        "article_id": a["a_id"],
                        "tokens_tried": segments[:5],
                    }
                )
        checked += 1

    orphans = collect_orphan_fts_rows(conn)

    return {
        "checked_articles": checked,
        "articles_with_no_token_count": len(no_token),
        "articles_with_no_token": no_token,
        "articles_with_unindexed_token_count": len(unindexed),
        "articles_with_unindexed_token": unindexed,
        "fts_orphan_rowid_count": len(orphans),
        "fts_orphan_rowids": orphans,
        "query_error_count": len(query_errors),
        "query_errors": query_errors[:50],
        "unindexed_detail": missing_detail,
        "tokenization": "whitespace_split_contiguous_runs (PLAN R11 / C-3)",
        "quote_escaping": "internal double quotes doubled (FTS5 phrase syntax)",
        "read_only": True,
        "self_healed": False,
        "note": "只报告，不 rebuild / 不 repair / 不自愈",
    }


def code_only(src: str, skip_assignments: tuple[str, ...] = ()) -> str:
    """抽出源码里的**纯代码**部分（剔除注释、**docstring** 与指定赋值块）。

    ⚠️ 只剔 docstring 与注释 —— 普通字符串里可能有待查的关键词
    (如 ``"mode=ro"``), 全剔会把真违规也漏掉。
    """
    tree = ast.parse(src)
    skip_lines: set[int] = set()

    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            body = getattr(node, "body", [])
            if (
                body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ds = body[0]
                for ln in range(ds.lineno, (ds.end_lineno or ds.lineno) + 1):
                    skip_lines.add(ln)
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if getattr(tgt, "id", None) in skip_assignments:
                    for ln in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                        skip_lines.add(ln)

    return "\n".join(
        ln for i, ln in enumerate(src.splitlines(), 1)
        if i not in skip_lines and not ln.lstrip().startswith("#")
    )


def check_static_write_safety(scripts_dir: Path) -> dict:
    """静态扫描**本 sprint 新增脚本** ⇒ 确认没有写命令。

    ⚠️ 扫描范围**只限本 sprint 新增的脚本** —— 不扫全仓
    （否则会误报既有的 ``moni/web/routes/author_blocks.py``,
    那里的 FTS5 ``'delete'`` 是合法写法）。
    """
    targets = [
        "verify_fts_index_criteria.py",
        "xbot_quality_candidates.py",
        "xbot_quality_artifacts.py",
        "xbot_quality_validate.py",
    ]
    # ⚠️ 模式表本身**必然**含这些字符串 ⇒ 扫描时跳过该赋值块
    forbidden = (
        "integrity-check", "integrity_check", "'rebuild'", '"rebuild"',
        "'repair'", '"repair"', "INSERT INTO articles_fts",
        "INSERT INTO articles", "UPDATE articles", "DELETE FROM articles",
        "PRAGMA writable_schema", "VACUUM",
    )
    hits = []
    checked = []
    for name in targets:
        p = scripts_dir / name
        if not p.exists():
            continue
        checked.append(name)
        src = p.read_text(encoding="utf-8")
        body = code_only(src, skip_assignments=("forbidden",))
        for f in forbidden:
            if f in body:
                hits.append({"script": name, "pattern": f})
    return {
        "checked_scripts": checked,
        "violations": hits,
        "clean": not hits,
        "scope_note": "只扫本 sprint 新增脚本，不扫全仓",
    }


def render_text(result: dict, static: dict) -> str:
    lines = [
        "=" * 72,
        "FTS 索引只读验收探针（I9）",
        "=" * 72,
        f"检查文章数                    : {result['checked_articles']}",
        "",
        "── 必须报出的异常（C-3）──",
        f"  取不到任何 token 的 rowid    : {result['articles_with_no_token_count']}",
        f"  token 未被索引的 rowid       : {result['articles_with_unindexed_token_count']}",
        f"  FTS 孤儿 rowid               : "
        f"{result['fts_orphan_rowid_count'] if result.get('fts_orphan_checked', True) else '**未检查**（跳过了 vocab 扫描）'}",
        f"  articles 里没有索引条目的      : "
        f"{result.get('articles_without_index_entry_count') if result.get('fts_orphan_checked', True) else '**未检查**'}",
        f"  MATCH 查询语法错误           : {result['query_error_count']}",
        "",
        f"  分词口径: {result['tokenization']}",
        f"  引号转义: {result['quote_escaping']}",
    ]
    for label, key in (
        ("取不到 token", "articles_with_no_token"),
        ("token 未被索引", "articles_with_unindexed_token"),
        ("FTS 孤儿 rowid", "fts_orphan_rowids"),
    ):
        items = result[key]
        lines.append(f"  ── {label} (rowid 清单) ──")
        if not items:
            lines.append("     (无)")
        else:
            for x in items[:20]:
                v = x["fts_rowid"] if isinstance(x, dict) else x
                lines.append(f"     rowid={v}")
            if len(items) > 20:
                lines.append(f"     … 另有 {len(items) - 20} 条")

    lines += [
        "",
        "── 静态扫描（本 sprint 新增脚本）──",
        f"   已扫: {', '.join(static['checked_scripts']) or '(无)'}",
        f"   违规: {static['violations'] or '(无)'}",
        "",
        "⚠️ 本探针**只报告**，不 rebuild / 不 repair / 不自愈。",
        "⚠️ FTS5 完整性检查命令是 INSERT 语法（**写命令**）⇒ 生产**禁止**。",
        "⚠️ 中文 token 按**空白切分的整段连续串**（PLAN R11）—— 不做中文分词。",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="数据库路径（**只读** 打开）")
    ap.add_argument("--source", default=None, help="只查某个信源")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    ap.add_argument(
        "--skip-vocab-scan",
        action="store_true",
        help=(
            "跳过 fts5vocab 扫描 (它要复制全库到临时副本 ⇒ 124MB, 偏重)。"
            "**验收场景不要跳过** —— 孤儿 rowid 只有 vocab 扫得到。"
        ),
    )
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        print(f"DB 不存在: {args.db}", file=sys.stderr)
        return 2

    conn = open_readonly(args.db)
    try:
        result = check_article_tokens(conn, source=args.source, limit=args.limit)
    finally:
        conn.close()

    # ⚠️ 孤儿 rowid 必须报出 ⇒ 在**临时副本**上用 fts5vocab 扫 FTS 索引自身
    #    (生产库零写入; 建 vocab 表是 DDL ⇒ 不能在生产上做)
    if args.skip_vocab_scan:
        # ⚠️ 跳过 ⇒ 孤儿 rowid **查不到** (external-content 表的固有陷阱)
        #    ⇒ 报告里必须显式说「未做」, 不能显示 0 冒充「查过且为 0」
        vocab = {
            "scanned_on": "skipped",
            "skipped": True,
            "note": (
                "已跳过 vocab 扫描 ⇒ **孤儿 rowid 未检查**。"
                "external-content 表用 SELECT 查不到索引里的孤儿 ⇒ "
                "不要把本项的 0 当成「查过且为 0」。"
            ),
            "indexed_rowid_count": None,
            "articles_rowid_count": None,
            "orphan_fts_rowids": [],
            "orphan_fts_rowid_count": None,
            "articles_without_index_entry": [],
            "articles_without_index_entry_count": None,
        }
    else:
        vocab = vocab_scan_on_copy(args.db)
    result["vocab"] = vocab
    result["fts_orphan_rowid_count"] = vocab["orphan_fts_rowid_count"]
    result["fts_orphan_rowids"] = [
        {"fts_rowid": x} for x in vocab["orphan_fts_rowids"]
    ]
    result["fts_orphan_checked"] = not args.skip_vocab_scan
    result["articles_without_index_entry"] = vocab["articles_without_index_entry"]
    result["articles_without_index_entry_count"] = vocab[
        "articles_without_index_entry_count"
    ]

    static = check_static_write_safety(Path(__file__).resolve().parent)

    if args.json:
        print(json.dumps({"fts": result, "static": static}, ensure_ascii=False, indent=2))
    else:
        print(render_text(result, static))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
