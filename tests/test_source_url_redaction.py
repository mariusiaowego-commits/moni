"""tests/test_source_url_redaction.py — out-bound credential redaction (v1.0.42, audit P1-10).

``sources.url`` for the two WeChat aggregators carries a long-lived JWT
(``…/api/rss/all?token=eyJhbGci…``) and ``GET /api/config/sources`` used to
return it verbatim.  These tests pin the primitive
(:func:`moni.web.redact.sanitize_source_url`), the "masked means unchanged"
update semantics, and the end-to-end guarantee that no response body contains
the credential.

Deliberately tests the *stored* value too: redaction must be out-bound only —
masking before storage would break RSS fetching (401).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from moni.web.redact import MASK, is_masked_url, sanitize_source_url

# A structurally real JWT (not a live credential) — same shape as the DB row.
JWT = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJtb25pIn0.abcdefghijklmnop"
WECHAT_URL = f"https://wechatrss.waytomaster.com/api/rss/all?token={JWT}"
WECHAT_MASKED = f"https://wechatrss.waytomaster.com/api/rss/all?token={MASK}"


# ---------------------------------------------------------------------------
# Truth table — masking
# ---------------------------------------------------------------------------

MASK_CASES = [
    ("plain-token", WECHAT_URL, WECHAT_MASKED),
    (
        "token-among-other-params",
        f"https://h/a?x=1&token={JWT}&y=2",
        f"https://h/a?x=1&token={MASK}&y=2",
    ),
    ("uppercase-param", f"https://h/a?TOKEN={JWT}", f"https://h/a?TOKEN={MASK}"),
    ("access-token-dash", f"https://h/a?access-token={JWT}", f"https://h/a?access-token={MASK}"),
    ("poc-token", f"https://h/a?poc_token=abc123def456", f"https://h/a?poc_token={MASK}"),
    ("key-param", "https://h/a?key=supersecretvalue", f"https://h/a?key={MASK}"),
    ("secret-param", "https://h/a?secret=abcdefghij", f"https://h/a?secret={MASK}"),
    ("api-key-param", "https://h/a?api_key=abcdefghij", f"https://h/a?api_key={MASK}"),
    ("client-secret", "https://h/a?client_secret=abcdefghij", f"https://h/a?client_secret={MASK}"),
    # JWT-shaped value under an unexpected name is still a credential.
    ("jwt-under-unknown-name", f"https://h/a?q={JWT}", f"https://h/a?q={MASK}"),
    # Fragment preserved.
    ("with-fragment", f"https://h/a?token={JWT}#frag", f"https://h/a?token={MASK}#frag"),
]


@pytest.mark.parametrize("label,raw,expected", MASK_CASES, ids=[c[0] for c in MASK_CASES])
def test_sanitize_masks_credentials(label: str, raw: str, expected: str) -> None:
    assert sanitize_source_url(raw) == expected


# ---------------------------------------------------------------------------
# Truth table — identity (must NOT change; 13 of 15 sources look like this)
# ---------------------------------------------------------------------------

IDENTITY_CASES = [
    ("no-query", "https://36kr.com/feed"),
    ("plain-query", "https://example.com/feed?page=2&lang=zh"),
    ("empty", ""),
    ("none", None),
    ("already-masked", WECHAT_MASKED),
    ("local-path", "/Users/mt16/dev/moni/config/bestblogs_x.opml"),
    ("token-as-value-not-name", "https://h/a?q=token"),
    ("short-non-credential-token-param-name-only", "https://h/a?tokenize=1"),
]


@pytest.mark.parametrize("label,raw", IDENTITY_CASES, ids=[c[0] for c in IDENTITY_CASES])
def test_sanitize_identity(label: str, raw) -> None:
    assert sanitize_source_url(raw) == raw


def test_sanitize_is_idempotent() -> None:
    once = sanitize_source_url(WECHAT_URL)
    assert sanitize_source_url(once) == once


def test_sanitize_never_raises_on_junk() -> None:
    for junk in [1234, b"bytes", object(), "http://[bad", "https://h/a?="]:
        sanitize_source_url(junk)  # must not raise


def test_param_name_kept_so_ui_can_hint_credential() -> None:
    """We mask the *value* only — the name tells the user a credential exists."""
    out = sanitize_source_url(WECHAT_URL)
    assert "token=" in out
    assert JWT not in out
    assert MASK in out


# ---------------------------------------------------------------------------
# is_masked_url
# ---------------------------------------------------------------------------


def test_is_masked_url_true_for_masked_form() -> None:
    assert is_masked_url(WECHAT_MASKED) is True


def test_is_masked_url_false_for_real_credential() -> None:
    assert is_masked_url(WECHAT_URL) is False


@pytest.mark.parametrize("raw", [None, "", "https://36kr.com/feed", "no-query-string"])
def test_is_masked_url_false_for_plain(raw) -> None:
    assert is_masked_url(raw) is False


# ---------------------------------------------------------------------------
# HTTP integration — out-bound never leaks; stored value never corrupted
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path):
    from fastapi.testclient import TestClient

    from moni.db.connection import close_db, get_db, set_db_path
    from moni.db.migrate import migrate
    from moni.web.server import create_app

    set_db_path(tmp_path / "test.db")
    migrate()
    conn = get_db()
    now = "2026-09-10T00:00:00+00:00"
    rows = [
        ("src-wechat", "wechat_rss_a", WECHAT_URL, 0),
        ("src-36kr", "36kr_rss", "https://36kr.com/feed", 1),
    ]
    for sid, name, url, active in rows:
        conn.execute(
            """
            INSERT INTO sources
                (id, name, display_name, source_type, url, fetch_interval_min,
                 is_active, notes, fetch_config, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
            """,
            (sid, name, name, "rss", url, 30, active, now, now),
        )
    conn.commit()  # discipline: DML must be committed before any HTTP call
    try:
        yield TestClient(create_app())
    finally:
        close_db()


def _stored_url(source_id: str) -> str:
    from moni.db.connection import get_db

    row = get_db().execute("SELECT url FROM sources WHERE id = ?", (source_id,)).fetchone()
    return row["url"]


def test_list_endpoint_never_returns_jwt(client) -> None:
    resp = client.get("/api/config/sources")
    assert resp.status_code == 200
    body = resp.text
    assert JWT not in body, "credential leaked via list endpoint"
    assert f"token={MASK}" in body
    # non-credential source untouched (byte-identical)
    urls = {s["name"]: s["url"] for s in resp.json()}
    assert urls["36kr_rss"] == "https://36kr.com/feed"


def test_get_one_endpoint_never_returns_jwt(client) -> None:
    resp = client.get("/api/config/sources/src-wechat")
    assert resp.status_code == 200
    assert JWT not in resp.text
    assert MASK in resp.json()["url"]


def test_post_echo_is_sanitized(client) -> None:
    resp = client.post(
        "/api/config/sources",
        json={
            "id": "src-new",
            "name": "new_src",
            "display_name": "new",
            "source_type": "rss",
            "url": f"https://h/feed?token={JWT}",
            "fetch_interval_min": 30,
            "is_active": True,
        },
    )
    assert resp.status_code == 201, resp.text
    assert JWT not in resp.text
    assert MASK in resp.json()["url"]


def test_put_masked_value_keeps_stored_credential(client) -> None:
    """A client echoing the masked URL back must NOT overwrite the real token."""
    resp = client.put(
        "/api/config/sources/src-wechat",
        json={"url": WECHAT_MASKED, "display_name": "wechat A"},
    )
    assert resp.status_code == 200, resp.text
    assert JWT not in resp.text
    # The DB still holds the working credential — redaction is out-bound only.
    assert _stored_url("src-wechat") == WECHAT_URL


def test_put_new_url_overwrites_stored_value(client) -> None:
    new_url = "https://wechatrss.waytomaster.com/api/rss/all?token=brandnewtoken123"
    resp = client.put("/api/config/sources/src-wechat", json={"url": new_url})
    assert resp.status_code == 200, resp.text
    assert _stored_url("src-wechat") == new_url
    assert JWT not in resp.text
    assert MASK in resp.json()["url"]


def test_put_returns_masked_url_after_overwrite(client) -> None:
    resp = client.put("/api/config/sources/src-36kr", json={"url": "https://36kr.com/feed?v=2"})
    assert resp.status_code == 200
    assert resp.json()["url"] == "https://36kr.com/feed?v=2"
