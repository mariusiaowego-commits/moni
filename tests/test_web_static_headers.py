"""静态 HTML 必须每次回源校验：改版后旧页面不能靠浏览器缓存继续生效。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from moni.web.server import create_app


def test_html_documents_are_not_heuristically_cached() -> None:
    client = TestClient(create_app())
    for path in ("/", "/desens-lab.html"):
        res = client.get(path)
        assert res.status_code == 200, path
        assert res.headers.get("cache-control") == "no-cache", path


def test_versioned_assets_keep_default_caching() -> None:
    client = TestClient(create_app())
    res = client.get("/css/main.css")
    assert res.status_code == 200
    assert "no-cache" not in (res.headers.get("cache-control") or "")
