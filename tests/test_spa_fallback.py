"""tests/test_spa_fallback.py — SPA fallback path containment (v1.0.42, audit P0-2).

The audit (2026-09-06) proved the old join

    candidate = STATIC_DIR / full_path
    if candidate.is_file(): return FileResponse(candidate)

was an arbitrary-file read: ``Python`` drops the left operand when the right
one is absolute, so ``GET //etc/hosts`` (starlette's ``path`` converter hands
``full_path="/etc/hosts"``) served a real system file.

This module pins the replacement primitive
:func:`moni.web.server.resolve_static_path` with a truth table of attack
vectors plus positive controls (legit static files, symlinks that stay inside
root), and one end-to-end assertion through the real ASGI app.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from moni.web.server import STATIC_DIR, resolve_static_path

# ---------------------------------------------------------------------------
# Attack vectors — every one of these MUST resolve to None
# ---------------------------------------------------------------------------

ATTACK_VECTORS = [
    # (label, rel_path_as_starlette_would_pass_it)
    ("absolute-path-double-slash", "/etc/hosts"),
    ("absolute-path-passwd", "/etc/passwd"),
    ("absolute-path-repo-env", "/Users/mt16/dev/moni/.env"),
    ("dotdot-simple", "../secret.txt"),
    ("dotdot-deep", "../../../../../../etc/hosts"),
    ("dotdot-midpath", "js/../../../etc/hosts"),
    ("dotdot-only", ".."),
    ("dotdot-trailing", "js/.."),
    ("url-decoded-slash", "../../etc/hosts"),
    ("backslash-windows-style", "..\\..\\etc\\hosts"),
    ("backslash-single", "js\\app.js"),
    ("nul-byte-truncation", "js/app.js\x00.png"),
    ("nul-byte-prefix", "\x00/etc/hosts"),
    ("empty-string", ""),
    ("dot-only", "."),
    ("slash-only", "/"),
    ("missing-file", "does-not-exist.js"),
    ("directory-not-file", "js"),
    ("ds-store-not-dir", "js/"),
]


@pytest.mark.parametrize("label,rel", ATTACK_VECTORS, ids=[v[0] for v in ATTACK_VECTORS])
def test_attack_vector_rejected(label: str, rel: str) -> None:
    """Illegal / escaping / missing inputs must all yield None."""
    assert resolve_static_path(STATIC_DIR, rel) is None, f"{label} was not rejected"


def test_absolute_path_would_have_escaped_with_raw_join() -> None:
    """Regression guard for the *root cause*, not just the symptom.

    Documents that the naive join really does discard the base — if a future
    refactor reintroduces it, this test explains why that is unsafe.
    """
    naive = STATIC_DIR / "/etc/hosts"
    assert str(naive) == "/etc/hosts"
    assert not str(naive).startswith(str(STATIC_DIR))
    # …while the primitive refuses it.
    assert resolve_static_path(STATIC_DIR, "/etc/hosts") is None


# ---------------------------------------------------------------------------
# Positive controls — the fix must not break legit static serving
# ---------------------------------------------------------------------------


def test_legit_static_js_file_served() -> None:
    got = resolve_static_path(STATIC_DIR, "js/app.js")
    assert got is not None and got.is_file()
    assert got.name == "app.js"


def test_legit_index_html_served() -> None:
    got = resolve_static_path(STATIC_DIR, "index.html")
    assert got is not None and got.is_file()


def test_legit_nested_css_dir_served() -> None:
    got = resolve_static_path(STATIC_DIR, "css/main.css")
    assert got is not None and got.is_file()


def test_root_itself_is_not_a_file() -> None:
    assert resolve_static_path(STATIC_DIR, "") is None


# ---------------------------------------------------------------------------
# Symlink semantics (tmp_path, self-contained — no dependency on repo layout)
# ---------------------------------------------------------------------------


@pytest.fixture()
def sandbox(tmp_path: Path) -> Path:
    """A disposable ``root`` with one real file, one inside-link, one escape-link."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "real.js").write_text("console.log('hi')\n")

    outside = tmp_path / "root-evil"  # sibling whose NAME shares the root prefix
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP SECRET\n")

    os.symlink(root / "real.js", root / "inside-link.js")  # stays inside root
    os.symlink(outside / "secret.txt", root / "escape-link.txt")  # escapes root
    return root


def test_symlink_inside_root_allowed(sandbox: Path) -> None:
    """Legit symlinks *inside* root must keep working (no over-blocking)."""
    got = resolve_static_path(sandbox, "inside-link.js")
    assert got is not None and got.is_file()


def test_symlink_escaping_root_rejected(sandbox: Path) -> None:
    """A symlink pointing outside root is the same class of bug — must be None."""
    assert resolve_static_path(sandbox, "escape-link.txt") is None


def test_sibling_prefix_dir_rejected(sandbox: Path) -> None:
    """Prefix-string containment would pass ``root-evil``; is_relative_to must not."""
    assert resolve_static_path(sandbox, "../root-evil/secret.txt") is None


def test_real_file_in_sandbox_root_served(sandbox: Path) -> None:
    got = resolve_static_path(sandbox, "real.js")
    assert got is not None and got.read_text().startswith("console.log")


# ---------------------------------------------------------------------------
# End-to-end through the real ASGI app (raw scope path, not normalised away)
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """TestClient with an isolated tmp DB (never data/news.db)."""
    from fastapi.testclient import TestClient

    from moni.db.connection import close_db, set_db_path
    from moni.db.migrate import migrate
    from moni.web.server import create_app

    set_db_path(tmp_path / "test.db")
    migrate()
    try:
        yield TestClient(create_app())
    finally:
        close_db()


def _raw_get(client, target: str):
    """Issue a GET that preserves a leading double slash in the request path.

    ``client.get("//etc/hosts")`` would be parsed as a scheme-relative URL by
    httpx, so we address the transport directly with an absolute URL whose
    path keeps both slashes — reproducing the audit's ``curl --path-as-is``.
    """
    return client.request("GET", target)


def test_spa_fallback_does_not_leak_etc_hosts(client) -> None:
    """The audit PoC: ``GET //etc/hosts`` must NOT return the real file."""
    resp = _raw_get(client, "http://testserver//etc/hosts")
    body = resp.text
    # If /etc/hosts was served, its content would appear verbatim.
    if Path("/etc/hosts").exists():
        assert Path("/etc/hosts").read_text() != body, "arbitrary file read still possible"
    assert "localhost" not in body
    assert len(body) > 1000  # SPA index.html, not a small system file


def test_spa_fallback_serves_index_for_unknown_route(client) -> None:
    resp = client.get("/some/spa/route")
    assert resp.status_code == 200
    assert "<html" in resp.text.lower() or "moni" in resp.text.lower()


def test_static_asset_still_served_over_http(client) -> None:
    resp = client.get("/js/app.js")
    assert resp.status_code == 200
    assert "function" in resp.text or "Alpine" in resp.text
