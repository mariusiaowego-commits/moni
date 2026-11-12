"""`moni fetch` 的退出码必须反映失败 (v1.0.76)。

CLI 只打印 ERROR 但返回 0 时, shell / cron / CI 看不到失败 —— 这条测试
把三种调用路径里的 CLI 那一环钉住。
"""
from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    "results, expected",
    [
        ([{"source": "a", "fetched": 1, "parsed": 1, "inserted": 1}], 0),
        ([{"source": "a", "fetched": 0, "parsed": 0, "inserted": 0,
           "error": "RuntimeError: boom"}], 1),
        ([
            {"source": "a", "fetched": 1, "parsed": 1, "inserted": 1},
            {"source": "b", "fetched": 0, "parsed": 0, "inserted": 0,
             "error": "RuntimeError: boom"},
        ], 1),
    ],
)
def test_main_exit_code_reflects_source_errors(monkeypatch, results, expected):
    import importlib

    fetch_run = importlib.import_module("moni.fetch.run")

    class _Scheduler:
        def enabled_sources(self):
            return [{"source_name": "stub"}]

        def should_fetch(self, name):
            return True

    monkeypatch.setattr(fetch_run, "Scheduler", _Scheduler)
    monkeypatch.setattr(
        fetch_run, "_collect_results",
        lambda *a, **kw: (results, sum(r["fetched"] for r in results), 0),
    )
    assert fetch_run.main(["--source", "stub"]) == expected
