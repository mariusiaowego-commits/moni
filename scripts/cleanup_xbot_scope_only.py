#!/usr/bin/env python3
"""t119u H1 一次性清理：处理已入库的「纯 ``xbot-scope``」批次。

背景：v1.0.134（PR #322）把 ``scope`` 接成了「命中即准入」的 OR 通道，
16:31 那次手动同步因此入库 3018 条只挂 ``xbot-scope``（工程跟踪 tag）的推文。
t119u（2026-11-26 dad 拍板）把准入改成「必须命中业务 tag」，并要求用 **H1**
处置这批历史数据：**留好删差**，而不是整批删掉重跑。

本工具的规则（与拍板一致）：

* 只处理 ``source='xbot_feed'`` 且 ``article_tags`` **恰好只有** ``xbot-scope``
  （工程 tag）的条目 —— 402 条有真实业务 tag 的历史贴 **一条都不动**；
* 每条用**当前**业务 tag 规则重评（口径与新入库一致）：
  - 先过三道过滤：纯回复（正文以 ``@`` 开头）/ 纯转推（``RT @`` 开头）/
    源级黑名单（``source_keyword_filters``，24 词）—— 命中即**删**；
  - 命中 **source tag 池**（勾选的 ``source_tags``，与 topic 无关）⇒
    **原地改挂**真实业务 tag（写 ``article_tags`` + ``article_keyword_hits``），
    并去掉 ``xbot-scope``；
  - 只命中 **topic tag 池** ⇒ 进 ``review``（**不删不改**）：历史条目在库里
    没有 topic 字段，无法判断它属于哪个 topic，只能交人工；
  - 两个池都不中 ⇒ **删除**该文章（走既有级联 + FTS 清理），并从
    ``.xbot_feed_state.json`` **精确**移除对应 tweet id；
* **绝不复活屏蔽作者**：作者在 ``source_author_blocks`` / 内置黑名单里的条目，
  既不删也不改（它们本来就不该在这批里，出现就跳过并报数）；
* **绝不清空 state**：只做逐条 id 差集；删之前先备份 DB（``sqlite3 .backup``）
  与 state 文件；
* 默认 ``--dry-run``（零写入），``--apply`` 才动手；可重复执行（第二次是 no-op）；
* 有非空引用（分享任务 / 案例 / 摘要 / 评分 / 收藏）的条目**一律跳过**并报告，
  交给人工决定。

用法::

    python3 scripts/cleanup_xbot_scope_only.py                # dry-run
    python3 scripts/cleanup_xbot_scope_only.py --apply        # 真跑（先备份）
    python3 scripts/cleanup_xbot_scope_only.py --apply --limit 50   # 小批验证

⚠️ 生产执行必须由 dad 明确授权；本文件本身不在任何 migration 里被调用。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:  # 允许直接 python3 scripts/xxx.py
    sys.path.insert(0, str(REPO_ROOT))

from moni.fetch.sources.xbot_feed import _switch  # noqa: E402  （唯一开关读取实现）

SOURCE_NAME = "xbot_feed"
ENGINEERING_TAGS = frozenset({"xbot-scope"})


def _now_tag() -> str:
    import os

    return datetime.now().strftime("%Y%m%d-%H%M%S-%f") + f"-{os.getpid()}"


def _data_dir() -> Path:
    import os

    return Path(os.environ.get("MONI_DATA_DIR") or (REPO_ROOT / "data"))


def _normalize_handle(raw: str) -> str:
    return (raw or "").strip().lstrip("@").lower()


def _blocked_handles(conn: sqlite3.Connection) -> set[str]:
    handles: set[str] = set()
    try:
        rows = conn.execute(
            "SELECT handle FROM source_author_blocks WHERE source_name = ?",
            (SOURCE_NAME,),
        ).fetchall()
        handles |= {_normalize_handle(r[0]) for r in rows}
    except sqlite3.Error:
        pass
    try:
        row = conn.execute(
            "SELECT fetch_config FROM sources WHERE name = ?", (SOURCE_NAME,)
        ).fetchone()
        cfg = json.loads(row[0]) if row and row[0] else {}
        for h in cfg.get("blacklisted_handles") or []:
            handles.add(_normalize_handle(str(h)))
    except (sqlite3.Error, ValueError):
        pass
    handles.discard("")
    return handles


def _business_keywords(
    conn: sqlite3.Connection,
) -> tuple[dict[str, list[str]], dict[str, list[str]], bool, bool]:
    """返回 ``(source 池, topic 池, from_source, from_topic)``。

    * source 池 = 勾选的 ``source_tags``（与 topic 无关）→ **判定用**；
    * topic 池 = ``topic_tags`` 映射的并集 → 历史条目查不到 topic，
      只能当「待复核」（pV P1-1）。
    """
    from moni.fetch.sources.xbot_feed import (
        DEFAULT_TOPIC_TAGS,
    )

    row = conn.execute(
        "SELECT fetch_config FROM sources WHERE name = ?", (SOURCE_NAME,)
    ).fetchone()
    cfg = json.loads(row[0]) if row and row[0] else {}
    from_source = _switch(cfg, "business_tags_from_source_tags")
    from_topic = _switch(cfg, "business_tags_from_topic_tags")

    names: list[str] = []
    source_names: list[str] = []
    if from_source:
        for r in conn.execute(
            """
            SELECT t.name FROM source_tags st
              JOIN sources s ON s.id = st.source_id
              JOIN tag_definitions t ON t.name = st.tag_name
             WHERE s.name = ? AND t.is_active = 1
             ORDER BY t.name
            """,
            (SOURCE_NAME,),
        ):
            source_names.append(r[0])
    if from_topic:
        mapping = {k: list(v) for k, v in DEFAULT_TOPIC_TAGS.items()}
        configured = cfg.get("topic_tags")
        if isinstance(configured, dict):
            for key, val in configured.items():
                if isinstance(val, str):
                    val = [val]
                if isinstance(key, str) and isinstance(val, (list, tuple)):
                    mapping[key.strip().lower()] = [str(v) for v in val if str(v)]
        for tags in mapping.values():
            names.extend(tags)

    def _load(names_in: list[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for name in dict.fromkeys(names_in):
            if not name or name in ENGINEERING_TAGS:
                continue
            tag_row = conn.execute(
                "SELECT keywords, is_active FROM tag_definitions WHERE name = ?",
                (name,),
            ).fetchone()
            if not tag_row or not tag_row["is_active"]:
                continue
            try:
                kws = json.loads(tag_row["keywords"] or "[]")
            except ValueError:
                continue
            kws = [k for k in kws if isinstance(k, str) and k.strip()]
            if kws:
                out[name] = kws
        return out

    # pV P1-1: 两个池**分开**返回。
    #   source 池 = source_tags 勾选（与 topic 无关，判定是决定性的）；
    #   topic 池 = topic_tags 映射（历史条目**没有 topic 字段**，
    #              库里查不到，只能按 union 近似 —— 所以它只做「待复核」，
    #              不用于「改挂」或「删除」的判定）。
    return _load(source_names), _load(names), from_source, from_topic


def _source_blacklist_keywords(conn: sqlite3.Connection) -> list[tuple[int, list[str]]]:
    """源级**黑名单**关键词（source_keyword_filters, mode='blacklist'）。

    t119u 起 ``bypass_keyword_filters=false``，这批词在新入库路径上会被拦掉；
    H1 重评必须**同一口径**，否则会把黑名单命中的推文当成「好货」留下。
    """
    try:
        rows = conn.execute(
            """
            SELECT f.id, f.keywords
              FROM source_keyword_filters f
              JOIN sources s ON s.id = f.source_id
             WHERE s.name = ? AND f.mode = 'blacklist' AND f.is_active = 1
             ORDER BY f.id
            """,
            (SOURCE_NAME,),
        ).fetchall()
    except sqlite3.Error:
        return []
    out: list[tuple[int, list[str]]] = []
    for row in rows:
        try:
            kws = json.loads(row["keywords"] or "[]")
        except ValueError:
            continue
        kws = [k for k in kws if isinstance(k, str) and k.strip()]
        if kws:
            out.append((int(row["id"]), kws))
    return out


def _publisher_handle(publisher: str) -> str:
    """从 ``名称 (@handle)`` 里取 handle。

    pV P1-1: 旧写法 ``split("(@")[-1].rstrip(")")`` 在**没有** ``(@`` 时会把
    昵称整段当成 handle。这里用 author_blocks 的正则 + xbot_feed 的
    ``normalize_handle``（全仓唯一归一实现）。
    """
    import re

    from moni.fetch.sources.xbot_feed import normalize_handle

    match = re.search(r"@([^)]+)\)", publisher or "")
    if match:
        return normalize_handle(match.group(1))
    return ""


def _refs_for(conn: sqlite3.Connection, article_id: str) -> list[str]:
    """非空引用检查 —— 有任何一条就跳过（交人工）。"""
    refs: list[str] = []
    checks = (
        ("share_tasks", "SELECT COUNT(*) FROM share_tasks WHERE article_ids LIKE ?",
         f'%"{article_id}"%'),
        ("cases", "SELECT COUNT(*) FROM cases WHERE article_id = ?", article_id),
        ("article_pins", "SELECT COUNT(*) FROM article_pins WHERE article_id = ?",
         article_id),
        ("ai_summary", "SELECT COUNT(*) FROM articles WHERE id = ? AND "
         "COALESCE(ai_summary,'') != ''", article_id),
        ("business_value", "SELECT COUNT(*) FROM articles WHERE id = ? AND "
         "COALESCE(business_value_score,'') != ''", article_id),
        # pV P2-2: 纵深防御 —— 摘要 / 事实核查 / 评分类字段一旦非空也算引用
        ("summary", "SELECT COUNT(*) FROM articles WHERE id = ? AND "
         "COALESCE(summary,'') != ''", article_id),
        ("fact_check", "SELECT COUNT(*) FROM articles WHERE id = ? AND "
         "COALESCE(fact_check_conclusion,'') != ''", article_id),
        ("quality_score", "SELECT COUNT(*) FROM articles WHERE id = ? AND "
         "COALESCE(quality_score,'') != ''", article_id),
        ("brief_summary", "SELECT COUNT(*) FROM articles WHERE id = ? AND "
         "COALESCE(brief_summary,'') != ''", article_id),
    )
    for label, sql, param in checks:
        try:
            n = conn.execute(sql, (param,)).fetchone()[0]
        except sqlite3.Error:
            continue
        if n:
            refs.append(label)
    return refs


def _match_keywords(text: str, tag_keywords: dict[str, list[str]]) -> tuple[list[str], list[dict[str, Any]]]:
    from moni.fetch.keyword_match import keyword_matches

    title_line = (text or "").strip().splitlines()[0] if text else ""
    hits: list[dict[str, Any]] = []
    tags: list[str] = []
    for tag, kws in tag_keywords.items():
        for kw in kws:
            if keyword_matches(kw, title=title_line, body=text or "",
                               field="title+content"):
                tags.append(tag)
                hits.append({"tag_name": tag, "keyword": kw,
                             "hit_field": "title+content"})
                break
    return tags, hits


def _match_blacklist(
    text: str,
    filters: list[tuple[int, list[str]]],
) -> tuple[int, str] | None:
    """命中源级黑名单则返回 ``(filter_id, keyword)``，否则 ``None``。

    匹配走同一个引擎 ``moni.fetch.keyword_match.keyword_matches``（AGENTS.md 铁律 1）。
    """
    from moni.fetch.keyword_match import keyword_matches

    if not filters:
        return None
    title_line = (text or "").strip().splitlines()[0] if text else ""
    for fid, kws in filters:
        for kw in kws:
            if keyword_matches(kw, title=title_line, body=text or "",
                               field="title+content"):
                return fid, kw
    return None


def _select_targets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT a.id, a.url, a.title, a.publisher, a.raw_content
          FROM articles a
         WHERE a.source = ?
           AND EXISTS (SELECT 1 FROM article_tags t
                        WHERE t.article_id = a.id AND t.tag_name = 'xbot-scope')
           AND NOT EXISTS (SELECT 1 FROM article_tags t
                            WHERE t.article_id = a.id AND t.tag_name != 'xbot-scope')
         ORDER BY a.id
        """,
        (SOURCE_NAME,),
    ).fetchall()


def _tweet_id_of(url: str) -> str:
    import re

    m = re.search(r"/status/(\d+)", url or "")
    return m.group(1) if m else ""


def _backup(conn: sqlite3.Connection, db_path: Path, state_path: Path) -> dict[str, str]:
    stamp = _now_tag()
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    db_backup = backup_dir / f"{db_path.name}.pre-xbot-scope-cleanup-{stamp}"
    conn.execute(f"VACUUM INTO '{db_backup}'")
    result = {"db": str(db_backup)}
    if state_path.exists():
        state_backup = state_path.with_suffix(state_path.suffix + f".pre-cleanup-{stamp}")
        shutil.copy2(state_path, state_backup)
        result["state"] = str(state_backup)
    return result


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="真写库（默认 dry-run）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 条（0 = 不限）")
    args = ap.parse_args(list(argv) if argv is not None else None)

    data_dir = _data_dir()
    db_path = data_dir / "news.db"
    state_path = data_dir / ".xbot_feed_state.json"
    if not db_path.exists():
        print(f"找不到库: {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    targets = _select_targets(conn)
    if args.limit:
        targets = targets[: args.limit]
    source_pool, topic_pool, from_source, from_topic = _business_keywords(conn)
    blacklist_filters = _source_blacklist_keywords(conn)
    blocked = _blocked_handles(conn)
    cfg_row = conn.execute(
        "SELECT fetch_config FROM sources WHERE name = ?", (SOURCE_NAME,)
    ).fetchone()
    cfg = json.loads(cfg_row[0]) if cfg_row and cfg_row[0] else {}
    exclude_replies = _switch(cfg, "exclude_replies")
    exclude_retweets = _switch(cfg, "exclude_retweets")

    summary: dict[str, Any] = {
        "mode": "apply" if args.apply else "dry-run",
        "db": str(db_path),
        "state": str(state_path),
        "business_tag_gate": {
            "from_source_tags": from_source,
            "from_topic_tags": from_topic,
            "source_pool_tags": sorted(source_pool),
            "source_pool_keywords": sum(len(v) for v in source_pool.values()),
            "topic_pool_tags": sorted(topic_pool),
            "topic_pool_keywords": sum(len(v) for v in topic_pool.values()),
            "exclude_replies": exclude_replies,
            "exclude_retweets": exclude_retweets,
            "source_blacklist_filters": [
                {"id": fid, "keywords": kws} for fid, kws in blacklist_filters
            ],
        },
        "targets": len(targets),
        "retag": [],
        "review": [],
        "delete": [],
        "skipped_blocked": [],
        "skipped_refs": [],
        # 只有 source 池能**决定性**判定。topic 池因历史条目缺 topic 字段
        # 只能近似 → 命中它的一律进 review（不删不改）。
        "fail_closed": not source_pool,
    }

    if summary["fail_closed"]:
        print(json.dumps({**summary, "note": "source 池为空 → 不会删除任何条目"},
                         ensure_ascii=False, indent=2))
        print("拒绝执行：当前 source 业务 tag 词表为空（fail-closed）。", file=sys.stderr)
        return 3

    from moni.fetch.sources.xbot_feed import _is_reply_text, _is_retweet_text

    for row in targets:
        handle = _publisher_handle(str(row["publisher"] or ""))
        if handle and handle in blocked:
            summary["skipped_blocked"].append(row["url"])
            continue
        refs = _refs_for(conn, row["id"])
        if refs:
            summary["skipped_refs"].append({"url": row["url"], "refs": refs})
            continue
        text = (row["title"] or "") + "\n\n" + (row["raw_content"] or "")
        body = row["raw_content"] or ""

        # ── 与新入库口径一致的三道过滤 ──────────────────────────────
        if exclude_retweets and _is_retweet_text(body):
            summary["delete"].append({"url": row["url"],
                                      "tweet_id": _tweet_id_of(row["url"]),
                                      "reason": "retweet"})
            continue
        if exclude_replies and _is_reply_text(body, handle):
            summary["delete"].append({"url": row["url"],
                                      "tweet_id": _tweet_id_of(row["url"]),
                                      "reason": "reply"})
            continue
        hit_filter = _match_blacklist(text, blacklist_filters)
        if hit_filter is not None:
            fid, kw = hit_filter
            summary["delete"].append({"url": row["url"],
                                      "tweet_id": _tweet_id_of(row["url"]),
                                      "reason": "source_blacklist",
                                      "filter_id": fid, "keyword": kw})
            continue

        tags, hits = _match_keywords(text, source_pool)
        if tags:
            summary["retag"].append({"url": row["url"], "tags": tags,
                                     "hits": len(hits)})
            continue
        topic_tags, topic_hits = _match_keywords(text, topic_pool)
        if topic_tags:
            # 历史条目查不到 topic → topic 池命中只能当「待复核」。
            summary["review"].append({"url": row["url"], "tags": topic_tags,
                                      "hits": len(topic_hits)})
            continue
        summary["delete"].append({"url": row["url"],
                                  "tweet_id": _tweet_id_of(row["url"]),
                                  "reason": "no_business_tag"})

    if args.apply:
        summary["backup"] = _backup(conn, db_path, state_path)
        removed_ids: list[str] = []
        from moni.web.routes.author_blocks import (  # 复用既有 FTS 清理
            _fts_rows_for,
            _purge_fts_index,
        )

        for item in summary["retag"]:
            row = conn.execute(
                "SELECT id FROM articles WHERE url = ? AND source = ?",
                (item["url"], SOURCE_NAME),
            ).fetchone()
            if not row:
                continue
            aid = row["id"]
            conn.execute(
                "DELETE FROM article_tags WHERE article_id = ? AND tag_name = 'xbot-scope'",
                (aid,),
            )
            conn.execute(
                "DELETE FROM article_keyword_hits WHERE article_id = ?",
                (aid,),
            )
            now = datetime.now(timezone.utc).isoformat()
            for tag in item["tags"]:
                conn.execute(
                    "INSERT OR IGNORE INTO article_tags (article_id, tag_name, created_at) "
                    "VALUES (?,?,?)",
                    (aid, tag, now),
                )
            # 重新算一遍真实命中，写 article_keyword_hits
            text_row = conn.execute(
                "SELECT title, raw_content FROM articles WHERE id = ?", (aid,)
            ).fetchone()
            _, hits = _match_keywords(
                (text_row["title"] or "") + "\n\n" + (text_row["raw_content"] or ""),
                source_pool,
            )
            for hit in hits:
                conn.execute(
                    "INSERT INTO article_keyword_hits "
                    "(article_id, keyword, filter_id, hit_field, created_at, tag_name) "
                    "VALUES (?,?,?,?,?,?)",
                    (aid, hit["keyword"], None, hit["hit_field"], now, hit["tag_name"]),
                )

        for item in summary["delete"]:
            row = conn.execute(
                "SELECT id FROM articles WHERE url = ? AND source = ?",
                (item["url"], SOURCE_NAME),
            ).fetchone()
            if not row:
                continue
            aid = row["id"]
            fts_rows = _fts_rows_for(conn, [aid])
            conn.execute("DELETE FROM articles WHERE id = ?", (aid,))
            try:
                _purge_fts_index(conn, [aid], fts_rows=fts_rows)
            except Exception as exc:  # noqa: BLE001
                summary.setdefault("fts_errors", []).append(str(exc))
            if item["tweet_id"]:
                removed_ids.append(item["tweet_id"])

        # ── pV P1-1: 顺序必须是「先提交 DB，成功后才动 state」──────
        # 旧写法先写 state 再 commit：commit 失败 → DB 回滚，state 却已丢
        # 掉 1576 个 id（不可逆的静默数据丢失）。
        conn.commit()

        if removed_ids and state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            keep = [i for i in state.get("imported_ids", []) if str(i) not in set(removed_ids)]
            state["imported_ids"] = keep
            # 原子写：临时文件 + os.replace()，避免半截文件
            import os

            tmp_path = state_path.with_name(state_path.name + ".tmp")
            tmp_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp_path, state_path)
        summary["state_removed_ids"] = len(removed_ids)
        summary["state_written_after_commit"] = True

    conn.close()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
