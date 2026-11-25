"""案例库前端与后端端点契约（防再次调用不存在的 /tags）。"""

from pathlib import Path


def test_cases_js_uses_keywords_endpoints():
    source = (Path(__file__).parents[1] / "moni/web/static/js/cases.js").read_text(
        encoding="utf-8"
    )
    assert "/keywords" in source
    assert "/tags" not in source


def test_cases_js_deletes_keyword_by_value():
    source = (Path(__file__).parents[1] / "moni/web/static/js/cases.js").read_text(
        encoding="utf-8"
    )
    assert "kw.keyword || kw" in source
