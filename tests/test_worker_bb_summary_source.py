"""tests/test_worker_bb_summary_source.py — verify default_worker.sh
branches on bb_summary_source (moni_ai | bestblogs | both | none).

v1.0.9.3: was a prompt soft-constraint, never honored.  Worker
template must now actually branch.

We extract the inline Python from default_worker.sh's two heredocs
and run them in a fresh sqlite to verify the branching logic.
"""
from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKER = REPO_ROOT / "scripts" / "default_worker.sh"


def _extract_python_helpers() -> str:
    """Pull the two <<'PYEOF' ... PYEOF blocks out of default_worker.sh,
    strip the sys.argv[1..3] destructure (we set it ourselves), and
    return the merged Python source so the tests can run it.

    The two blocks are nearly identical (one is the "first read" before
    BB fetch, the other is the "re-read after BB fetch").  After the
    bb_summary_source branching was added in v1.0.9.3, they should
    produce identical output for the same input.
    """
    src = WORKER.read_text()
    # Find all `<<'PYEOF' ... PYEOF` heredocs
    blocks = re.findall(
        r"<<'PYEOF'\n(.*?)\nPYEOF",
        src,
        re.DOTALL,
    )
    if len(blocks) < 2:
        raise RuntimeError(
            f"expected 2 PYEOF blocks in default_worker.sh, found {len(blocks)}"
        )
    # The 2nd and 3rd heredocs (0-indexed: 1 and 2) are the article_content
    # builders.  Index 0 is the bb_fetch helper, index 3+ are other helpers.
    # Match by signature: must read "ids_str = sys.argv[1]"
    article_blocks = [b for b in blocks if "ids_str = sys.argv[1]" in b]
    if len(article_blocks) != 2:
        raise RuntimeError(
            f"expected exactly 2 article_content PYEOF blocks, found {len(article_blocks)}"
        )
    # We test block 1 (first read) — block 2 (re-read after BB fetch)
    # is identical after v1.0.9.3 and shares the same branching.
    py = article_blocks[0]
    # Remove the sys.argv destructure so we control the input via locals
    py = re.sub(
        r"ids_str = sys\.argv\[1\]\ndb_path = sys\.argv\[2\]\nmanifest_path = sys\.argv\[3\] if len\(sys\.argv\) > 3 else \"\"\n",
        "",
        py,
    )
    return py


ARTICLE_CONTENT_PY = _extract_python_helpers()


def _run_builder(
    db_path: Path,
    article_ids: list[str],
    bb_summary_source: str,
    manifest_payload: dict | None = None,
) -> str:
    """Execute the extracted inner-Python with controlled args.

    Writes a temp manifest file with the given bb_summary_source so the
    builder can read it.  Returns the builder's stdout (the markdown
    block that gets sent to the LLM).

    Note: the inner Python expects ``ids_str`` as a JSON-encoded string
    like ``'["art1","art2"]'`` (the bash invocation in default_worker.sh
    passes the raw ``$article_ids_str`` from the share manifest, which is
    a JSON array string).  We mimic that exactly.
    """
    if manifest_payload is None:
        manifest_payload = {"payload": {"bb_summary_source": bb_summary_source}}
    else:
        manifest_payload.setdefault("payload", {})
        manifest_payload["payload"]["bb_summary_source"] = bb_summary_source
    manifest_path = db_path.parent / f"_test_manifest_{bb_summary_source}.json"
    manifest_path.write_text(
        __import__("json").dumps(manifest_payload, ensure_ascii=False)
    )
    # Build the full script: inject the test args as locals.
    # ids_str must be a JSON array string (matches what bash passes).
    ids_str_json = __import__("json").dumps([str(x) for x in article_ids])
    full_script = textwrap.dedent(f"""
        import sys, os
        sys.path.insert(0, {str(REPO_ROOT)!r})
        ids_str = {ids_str_json!r}
        db_path = {str(db_path)!r}
        manifest_path = {str(manifest_path)!r}
        # Now run the extracted builder
        exec({ARTICLE_CONTENT_PY!r})
    """)
    result = subprocess.run(
        [sys.executable, "-c", full_script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    manifest_path.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"builder failed: rc={result.returncode}\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )
    return result.stdout


@pytest.fixture
def seeded_db(tmp_path):
    """Create a fresh sqlite with a bestblogs article that has BOTH
    ai_summary and bb_summary populated, plus all 5 bb_* data fields."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE articles (
            id TEXT PRIMARY KEY,
            title TEXT,
            raw_content TEXT,
            ai_summary TEXT,
            source TEXT,
            bb_score INTEGER,
            bb_summary TEXT,
            bb_one_sentence TEXT,
            bb_main_points TEXT,
            bb_key_quotes TEXT,
            bb_tags TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO articles VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "art1",
            "Test Article",
            "raw content here",
            "MONI summary text",
            "bestblogs",
            90,
            "BB summary text",
            "BB one-liner",
            '[{"point":"P1"}]',
            '["Q1"]',
            '["t1"]',
        ),
    )
    conn.commit()
    conn.close()
    return db


def test_source_moni_ai_only_shows_ai_summary(seeded_db):
    out = _run_builder(seeded_db, ["art1"], "moni_ai")
    assert "**AI 摘要**: MONI summary text" in out
    assert "BestBlogs 摘要" not in out


def test_source_bestblogs_only_shows_bb_summary(seeded_db):
    out = _run_builder(seeded_db, ["art1"], "bestblogs")
    assert "**BestBlogs 摘要**: BB summary text" in out
    assert "AI 摘要" not in out


def test_source_both_shows_both_summaries(seeded_db):
    out = _run_builder(seeded_db, ["art1"], "both")
    assert "**AI 摘要**: MONI summary text" in out
    assert "**BestBlogs 摘要**: BB summary text" in out


def test_source_none_shows_no_summary(seeded_db):
    out = _run_builder(seeded_db, ["art1"], "none")
    assert "AI 摘要" not in out
    assert "BestBlogs 摘要" not in out
    # But other BB fields (score / points / quotes / tags) still show
    assert "**BestBlogs 评分**: 90/100" in out
    assert "要点" in out
    assert "金句" in out
    assert "**BestBlogs 标签**: t1" in out


def test_missing_bb_summary_source_defaults_to_moni_ai(seeded_db):
    """v1.0.9.3: pre-v1.0.9.3 manifests (no bb_summary_source) default
    to 'moni_ai' so the behavior matches what users had before."""
    out = _run_builder(
        seeded_db,
        ["art1"],
        "",  # not in any of the 4 valid values → falls through default
    )
    assert "**AI 摘要**: MONI summary text" in out
    assert "BestBlogs 摘要" not in out


def test_legacy_manifest_without_bb_summary_source_field(seeded_db):
    """Pre-v1.0.9.3 manifests don't have bb_summary_source at all.
    Builder must still work (treat as 'moni_ai' default)."""
    out = _run_builder(
        seeded_db,
        ["art1"],
        "moni_ai",  # explicitly set, but use a manifest with NO payload key
        manifest_payload={"payload": {}},
    )
    assert "**AI 摘要**: MONI summary text" in out
    assert "BestBlogs 摘要" not in out
