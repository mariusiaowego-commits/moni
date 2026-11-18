"""sprint-54 / id 26111703 —— I3 只读候选预演器的护栏。

覆盖 p7 的硬约束：

* **G-C**（p8 护栏 C）：预演器必须能在**只读连接**上跑通 ⇒ 任何隐式写
  立即抛 ``sqlite3.OperationalError: attempt to write a readonly database``。
* **C-3 / p8 G-B**：判定「仅命中裸词『微信』」必须**重放完整词表**，
  **严禁**单看 ``article_keyword_hits``（它每个 tag 只记首词 ⇒ 是下界）。
* **C-6**：输出必须含 ``protected_article_ids`` 与 ``is_protected``。
* **AC-PRV-01**：输出**不含正文**。

⚠️ 本文件只读，不写生产库、不改生产配置。
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "scripts" / "xbot_quality_candidates.py"


@pytest.fixture()
def snapshot_db(tmp_path):
    """建一个**临时**库, 带 xbot_feed 的文章 + tag 关键词 + 保护资产。"""
    from datetime import datetime, timezone

    from moni.db.connection import close_db, get_db, set_db_path
    from moni.db.migrate import migrate

    close_db()
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    set_db_path(data_dir / "news.db")
    migrate()

    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()

    conn.execute(
        "INSERT OR REPLACE INTO sources "
        "(id, name, display_name, source_type, url, fetch_interval_min, "
        " is_active, fetch_config, created_at, updated_at) "
        "VALUES ('src-xbot-feed', 'xbot_feed', 'X-bot', 'json', 'https://x', 60, 1, ?, ?, ?)",
        (json.dumps({"scope": {}}), now, now),
    )
    conn.execute(
        "INSERT OR REPLACE INTO tag_definitions (name, keywords, is_active, created_at) "
        "VALUES ('wechat', ?, 1, ?)",
        (json.dumps(["微信", "微信小程序", "公众号", "视频号", "企业微信"]), now),
    )
    conn.execute(
        "INSERT OR REPLACE INTO source_tags (source_id, tag_name, created_at) "
        "VALUES ('src-xbot-feed', 'wechat', ?)",
        (now,),
    )

    # 一篇「只命中裸词微信」的文章
    conn.execute(
        "INSERT INTO articles (id, title, url, source, publisher, published_at, "
        "fetched_at, raw_content) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("bare01", "聊聊微信", "https://x/1", "xbot_feed", "@a", now, now, "就说一句微信"),
    )
    # 一篇「命中微信小程序」的文章 (article_keyword_hits 只会记首词「微信」!)
    conn.execute(
        "INSERT INTO articles (id, title, url, source, publisher, published_at, "
        "fetched_at, raw_content) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("mini02", "微信小程序上线", "https://x/2", "xbot_feed", "@b", now, now,
         "我们做了个微信小程序"),
    )
    # 一篇有简报的 ⇒ 受保护
    conn.execute(
        "INSERT INTO articles (id, title, url, source, publisher, published_at, "
        "fetched_at, raw_content, brief_summary) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("prot03", "微信生态观察", "https://x/3", "xbot_feed", "@c", now, now,
         "深度分析微信生态", "这是一份简报"),
    )
    conn.commit()
    conn.close()
    close_db()
    return data_dir / "news.db"


def _code_only(src: str) -> str:
    """抽出源码里的**纯代码**部分 (剔除注释与 **docstring**)。

    ⚠️ **只剔 docstring, 不剔普通字符串** —— 普通字符串里可能就有
    待查的关键词 (如 ``"mode=ro"``), 全剔会把真违规也漏掉。

    ⚠️ 用 AST 而不是正则: 正则无法可靠区分「代码里的字符串」与
    「docstring 里提到某个词」—— 本文件第一版就因此把脚本自己的
    说明文字判成了违规。
    """
    import ast
    import io
    import tokenize

    # ① 找出所有 docstring 的行号 (模块级 + 函数/类级)
    tree = ast.parse(src)
    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            ds = body[0]
            for ln in range(ds.lineno, (ds.end_lineno or ds.lineno) + 1):
                doc_lines.add(ln)

    # ② 逐行剔除: 注释行 + docstring 行
    out: list[str] = []
    for i, ln in enumerate(src.splitlines(), 1):
        if i in doc_lines:
            continue
        if ln.lstrip().startswith("#"):
            continue
        out.append(ln)
    return "\n".join(out)


def _run_script(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), "--db", str(db_path), *args],
        capture_output=True,
        text=True,
        timeout=180,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/Users/mt16"},
    )


# ── G-C: 只读连接 ───────────────────────────────────────────────────────────

def test_script_runs_on_readonly_connection(snapshot_db):
    """⚠️ **G-C**：预演器必须能在只读连接上跑通。

    连接用 ``mode=ro`` + ``PRAGMA query_only=ON`` ⇒ 任何隐式写
    (含 ``INSERT INTO articles_fts``) 立即抛
    ``sqlite3.OperationalError: attempt to write a readonly database``。
    """
    r = _run_script(snapshot_db, "--json")
    assert r.returncode == 0, r.stderr[-1500:]
    data = json.loads(r.stdout)
    assert data["scanned_articles"] == 3


def test_script_source_contains_no_write_sql():
    """⚠️ **静态断言**：脚本的**可执行代码**里**不得**出现写库语句。

    ⚠️ 扫描范围**限定在本脚本** —— 不扫全仓 (否则会误报既有的
    ``moni/web/routes/author_blocks.py:185``, 那里的 FTS5 ``'delete'``
    是合法写法)。p7 P1-5 / PLAN R7。

    ⚠️ 扫描时**剔除注释与 docstring** —— 否则脚本自己的「不 INSERT INTO …」
    说明文字会把自己判红 (本测试第一版就踩了这个坑)。
    """
    src = _SCRIPT.read_text(encoding="utf-8")
    code_only = _code_only(src)

    for forbidden in (
        "INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ", "ALTER ",
        "rebuild", "integrity-check", "checkpoint",
    ):
        assert forbidden not in code_only, f"预演器代码不该出现 {forbidden!r}"
    # 必须用只读打开
    assert "mode=ro" in code_only
    assert "query_only" in code_only


def test_script_does_not_write_to_db(snapshot_db):
    """⚠️ 跑完预演器, **所有表行数不变** (含 FTS 表)。"""
    conn = sqlite3.connect(f"file:{snapshot_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")

    def counts():
        return {
            t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("articles", "articles_fts", "basket", "source_author_blocks",
                      "article_keyword_hits", "article_tags", "article_pins")
        }

    before = counts()
    conn.close()

    r = _run_script(snapshot_db, "--json")
    assert r.returncode == 0, r.stderr[-800:]

    conn = sqlite3.connect(f"file:{snapshot_db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    after = counts()
    conn.close()
    assert after == before, "预演器产生了写入"


# ── C-3 / G-B: 完整词表重放 ─────────────────────────────────────────────────

def test_bare_wechat_detected_via_full_wordlist_replay(snapshot_db):
    """⚠️ 「只命中裸词微信」的文章**必须**被识别 (靠完整词表重放)。"""
    r = _run_script(snapshot_db, "--json")
    data = json.loads(r.stdout)
    ids = {c["article_id"] for c in data["candidates"]}
    assert "bare01" in ids, f"裸词微信没被识别: {data['candidates']}"


def test_article_hitting_specific_business_term_is_not_bare_wechat(snapshot_db):
    """⚠️ **G-B 核心反例**：正文含「微信小程序」的文章**不得**被判「仅裸词微信」。

    ⚠️ ``article_keyword_hits`` 那个表每个 tag 只记**首**个命中词
    ⇒ 查它会把本文误判成「仅裸词微信」(它记的是「微信」)。
    ⇒ 必须靠**完整词表重放**才判得对。
    """
    r = _run_script(snapshot_db, "--json")
    data = json.loads(r.stdout)
    cands = {c["article_id"]: c for c in data["candidates"]}
    if "mini02" in cands:
        ev = " ".join(cands["mini02"]["evidence"])
        assert "only_bare_wechat" not in ev, (
            f"含「微信小程序」的文章被误判成仅裸词微信: {ev}"
        )


def test_replay_does_not_use_article_keyword_hits(snapshot_db):
    """⚠️ 脚本**不得**查 ``article_keyword_hits`` 来做裸词判定。

    ⚠️ 扫描剔除注释与 docstring —— 只看**可执行代码**。
    """
    src = _SCRIPT.read_text(encoding="utf-8")
    code_only = _code_only(src)
    assert "article_keyword_hits" not in code_only, (
        "裸词判定不得读 article_keyword_hits (它每个 tag 只记首词 ⇒ 是下界)"
    )


# ── C-6: 保护标记 ───────────────────────────────────────────────────────────

def test_output_contains_protected_article_ids_and_is_protected(snapshot_db):
    """⚠️ **C-6**：输出必须含 ``protected_article_ids`` 与 ``is_protected``。"""
    r = _run_script(snapshot_db, "--json")
    data = json.loads(r.stdout)
    assert "protected_article_ids" in data
    for c in data["candidates"]:
        assert "is_protected" in c, "候选缺 is_protected"
        assert "protected_article_ids" in c, "候选缺 protected_article_ids"
        assert "protected_kinds" in c, "候选缺 protected_kinds"
    # prot03 有 brief ⇒ 受保护
    cands = {c["article_id"]: c for c in data["candidates"]}
    if "prot03" in cands:
        assert cands["prot03"]["is_protected"] is True
        assert "prot03" in data["protected_article_ids"]


# ── AC-PRV-01: 输出不含正文 ────────────────────────────────────────────────

def test_output_contains_no_article_body(snapshot_db):
    """⚠️ **AC-PRV-01**：候选清单**不得**含正文。

    只允许元数据 + 标签 ⇒ 正文片段 (如「深度分析微信生态」) 不应出现。
    """
    r = _run_script(snapshot_db, "--json")
    out = r.stdout
    for body_marker in ("深度分析微信生态", "就说一句微信", "我们做了个微信小程序"):
        assert body_marker not in out, f"输出里出现了正文片段: {body_marker!r}"


def test_output_reports_no_accuracy_metrics(snapshot_db):
    """⚠️ **没有标注集就不报精确率/召回率/误杀率**。"""
    r = _run_script(snapshot_db, "--json")
    data = json.loads(r.stdout)
    assert data["metrics_reported"] == []
    for forbidden in ("precision", "recall", "accuracy", "f1"):
        assert forbidden not in json.dumps(data, ensure_ascii=False).lower(), (
            f"输出了不该有的准确率指标: {forbidden}"
        )


# ── I4: 标注集模板 ──────────────────────────────────────────────────────────

def test_annotation_template_is_agent_suggestion_only(snapshot_db):
    """⚠️ 标注集模板必须标明「**agent 建议，不是 dad 人工标注**」。"""
    r = _run_script(snapshot_db, "--emit-annotation-template", "--json")
    assert r.returncode == 0, r.stderr[-800:]
    tpl = json.loads(r.stdout)
    assert tpl, "模板为空"
    for row in tpl:
        assert row["dad_verdict"] == "", "dad_verdict 必须留空待 dad 填"
        assert row["dad_note"] == "", "dad_note 必须留空待 dad 填"
        assert row["agent_suggestion"], "必须标明 agent 建议"
        # 不含正文
        assert "raw_content" not in row
        assert "content" not in row


def test_annotation_template_contains_no_body(snapshot_db):
    """⚠️ 模板**只含元数据 + 标签, 不含正文**。"""
    r = _run_script(snapshot_db, "--emit-annotation-template", "--json")
    for body_marker in ("深度分析微信生态", "就说一句微信", "我们做了个微信小程序"):
        assert body_marker not in r.stdout, f"模板里出现了正文: {body_marker!r}"
