"""tests/test_bank_bot.py — scripts/bank_bot/run_bot.py 的离线测试。

bot 是 moni 之外的独立生产者 (agent 管道的上游), 但它产出的 JSON 契约就是
moni 的 bank_feed 源读的东西, 所以坏契约必须在**这里**挡住。

不联网: HTTP 一律 monkeypatch ``_http_get``; agent 一律 monkeypatch
``subprocess.Popen`` (production 用 Popen + 进程组, 超时能杀掉 hermes 的
浏览器孙进程)。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

BOT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "bank_bot" / "run_bot.py"
)


@pytest.fixture(scope="module")
def bot():
    spec = importlib.util.spec_from_file_location("bank_bot_run", BOT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _FakeProc:
    pid = 9_999_999

    def __init__(self, returncode: int, stdout: str, stderr: str, *,
                 timeout: bool) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._timeout = timeout

    def communicate(self, input=None, timeout=None):
        # 超时后调用方会 kill 进程组再 communicate() 收尾, 第二次必须返回。
        if self._timeout and timeout:
            self._timeout = False
            raise subprocess.TimeoutExpired("hermes", timeout or 1)
        return self._stdout, self._stderr


def _fake_popen(returncode: int = 0, stdout: str = "[]", stderr: str = "", *,
                timeout: bool = False):
    def _factory(cmd, **kwargs):
        return _FakeProc(returncode, stdout, stderr, timeout=timeout)

    return _factory


RSS2 = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <item>
    <title>建设银行举办2026年对公客户经理技能竞赛</title>
    <link>https://example.com/news/1.html</link>
    <description><![CDATA[<p>建设银行近日举办技能竞赛。</p>]]></description>
    <pubDate>Thu, 12 Nov 2026 15:55:29 +0800</pubDate>
  </item>
  <item>
    <title>今日 A 股三大指数集体收跌</title>
    <link>https://example.com/news/2.html</link>
    <description>市场成交 1.9 万亿, 工行等权重股走弱。</description>
    <pubDate>Wed, 11 Nov 2026 15:00:00 +0800</pubDate>
  </item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>邮储银行发布2026年中期业绩</title>
    <link href="https://example.com/atom/1.html"/>
    <summary>邮储银行今日发布中期业绩。</summary>
    <updated>2026-11-10T09:00:00+08:00</updated>
  </entry>
</feed>"""


# ── 解析层 ──────────────────────────────────────────────────────────────

def test_parse_rss_rss2(bot):
    entries = bot.parse_rss(RSS2.encode("utf-8"))
    assert len(entries) == 2
    assert entries[0]["url"] == "https://example.com/news/1.html"
    assert "<p>" not in entries[0]["summary"]
    assert "建设银行近日举办技能竞赛。" in entries[0]["summary"]


def test_parse_rss_atom(bot):
    entries = bot.parse_rss(ATOM.encode("utf-8"))
    assert len(entries) == 1
    assert entries[0]["url"] == "https://example.com/atom/1.html"
    assert entries[0]["title"] == "邮储银行发布2026年中期业绩"


def test_parse_rss_rejects_broken_xml(bot):
    with pytest.raises(bot.TargetError, match="invalid XML"):
        bot.parse_rss(b"<rss><channel>")


def test_parse_json_list_yicai(bot):
    payload = json.dumps([
        {"NewsTitle": "工行、农行定增获审核通过",
         "url": "/news/103437120.html",
         "CreateDate": "2026-11-11T22:55:13",
         "NewsNotes": "尚需证监会同意。"},
    ]).encode("utf-8")
    entries = bot.parse_json_list(
        payload,
        {"title": "NewsTitle", "url": "url", "date": "CreateDate",
         "summary": "NewsNotes"},
        "",
        "https://www.yicai.com",
    )
    assert entries[0]["url"] == "https://www.yicai.com/news/103437120.html"
    assert entries[0]["title"] == "工行、农行定增获审核通过"


def test_parse_json_list_bad_path(bot):
    with pytest.raises(bot.TargetError, match="did not yield a list"):
        bot.parse_json_list(b'{"a": 1}', {}, "items", "")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Thu, 12 Nov 2026 15:55:29 +0800", "2026-11-12T15:55:29+08:00"),
        ("2026-11-11T22:55:13", "2026-11-11T22:55:13+08:00"),
        ("2026-11-11", "2026-11-11T00:00:00+08:00"),
        ("", None),
        (None, None),
        ("看不懂", None),
    ],
)
def test_parse_date(bot, raw, expected):
    got = bot._parse_date(raw)
    if expected is None:
        assert got is None
    else:
        # 允许本地时区表示差异, 只比较到分钟
        assert got[:16] == expected[:16]


# ── 归属层 ──────────────────────────────────────────────────────────────

def test_detect_banks_title_hit(bot):
    assert bot.detect_banks("建设银行发布季度业绩", "") == ["ccb"]


def test_detect_banks_ignores_one_passing_body_mention(bot):
    """两字简称在长正文里出现一次不算 —— 这是 keyword_match 的短 CJK 规则。"""
    body = "今日大盘震荡。" * 40 + "其中工行等权重股走弱。" + "板块轮动。" * 40
    assert bot.detect_banks("今日 A 股三大指数集体收跌", body) == []


def test_detect_banks_full_name_in_body(bot):
    assert bot.detect_banks("某行发布定增方案", "中国工商银行参与认购。") == ["icbc"]


# ── 目标清单 ────────────────────────────────────────────────────────────

def test_normalize_targets_defaults(bot):
    targets = bot.normalize_targets([
        {"id": "t1", "kind": "media_rss", "url": "https://e.com/feed"},
    ])
    assert targets[0]["publisher"] == "t1"
    assert targets[0]["max_items"] == bot.DEFAULT_MAX_ITEMS


@pytest.mark.parametrize(
    "entry, match",
    [
        ({"id": "t", "kind": "nope", "url": "https://e.com"}, "unknown kind"),
        ({"id": "t", "kind": "media_rss", "url": "https://e.com", "bank": "zzz"},
         "unknown bank"),
        ({"id": "t", "kind": "media_rss"}, "needs id \\+ url"),
    ],
)
def test_normalize_targets_rejects(bot, entry, match):
    with pytest.raises(bot.BotError, match=match):
        bot.normalize_targets([entry])


def test_normalize_targets_dedupes_by_id(bot):
    targets = bot.normalize_targets([
        {"id": "t1", "kind": "media_rss", "url": "https://e.com/a"},
        {"id": "t1", "kind": "media_rss", "url": "https://e.com/b"},
    ])
    assert len(targets) == 1


def test_repo_targets_file_loads(bot):
    targets = bot.load_targets(None, None)
    assert len(targets) == 11
    assert {t["bank"] for t in targets if t["kind"] == "official_list"} == {
        "boc", "abc", "icbc", "ccb", "psbc"
    }


# ── 确定性段 ────────────────────────────────────────────────────────────

def test_collect_media_attributes_by_title_only(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_get", lambda url, timeout: RSS2.encode("utf-8"))
    target = bot.normalize_targets([
        {"id": "t1", "kind": "media_rss", "url": "https://e.com/feed",
         "publisher": "测试媒体"},
    ])[0]
    items = bot.collect_media(target, timeout=1.0)
    assert [it["bank"] for it in items] == ["ccb"]
    assert items[0]["source_type"] == "media"
    assert items[0]["created_at"].startswith("2026-11-12T15:55:29")


def test_collect_media_http_error_raises_target_error(bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_get", lambda url, timeout: b"")
    target = bot.normalize_targets([
        {"id": "t1", "kind": "media_rss", "url": "https://e.com/feed"},
    ])[0]
    with pytest.raises(bot.TargetError):
        bot.collect_media(target, timeout=1.0)


# ── agent 段 ────────────────────────────────────────────────────────────

def test_parse_agent_json_strips_fence(bot):
    raw = '```json\n[{"title": "x"}]\n```'
    assert bot.parse_agent_json(raw) == [{"title": "x"}]


@pytest.mark.parametrize("raw, match", [
    ("没有数组", "no JSON array"),
    ("[1, bad]", "JSON is invalid"),
])
def test_parse_agent_json_rejects(bot, raw, match):
    with pytest.raises(bot.TargetError, match=match):
        bot.parse_agent_json(raw)


def test_collect_agent_maps_rows(bot, monkeypatch):
    rows = [{
        "title": "工商银行2026年前三季度经营稳中有进",
        "url": "https://www.icbc.com.cn/page/1.html",
        "created_at": "2026-11-03",
        "text": "工商银行今日发布三季度经营情况。" * 10,
        "bank": "icbc",
    }]
    monkeypatch.setattr(bot, "call_agent", lambda *a, **kw: json.dumps(rows))
    targets = bot.normalize_targets([
        {"id": "icbc-news", "kind": "official_list",
         "url": "https://www.icbc.com.cn/column/1.html", "bank": "icbc",
         "publisher": "中国工商银行"},
    ])
    items, failures = bot.collect_agent(
        targets, today="2026-11-12", timeout=1, profile="moni"
    )
    assert failures == []
    assert items[0]["source_type"] == "official"
    assert items[0]["publisher"] == "中国工商银行"
    assert items[0]["target_id"] == "icbc-news"


def test_collect_agent_failure_is_recorded_not_raised(bot, monkeypatch):
    def _boom(*args, **kwargs):
        raise bot.TargetError("agent exited 1: no key")

    monkeypatch.setattr(bot, "call_agent", _boom)
    targets = bot.normalize_targets([
        {"id": "boc-news", "kind": "official_list",
         "url": "https://www.boc.cn/a/", "bank": "boc"},
    ])
    items, failures = bot.collect_agent(
        targets, today="2026-11-12", timeout=1, profile="moni"
    )
    assert items == []
    assert failures and "no key" in failures[0]["error"]


def test_collect_agent_one_bad_page_does_not_kill_the_rest(bot, monkeypatch):
    """一个官网超时, 其余官网照样产出 —— 这正是拆成「一页一次调用」的理由。"""
    good_row = json.dumps([{
        "title": "农业银行发布乡村振兴金融服务方案",
        "url": "https://www.abchina.com/a/1.htm",
        "created_at": "2026-11-11",
        "text": "农业银行今日发布方案。" * 10,
        "bank": "abc",
    }])

    def _fake(prompt, *, timeout, profile):
        if "www.boc.cn" in prompt:
            raise bot.TargetError("agent timed out after 240s")
        return good_row

    monkeypatch.setattr(bot, "call_agent", _fake)
    targets = bot.normalize_targets([
        {"id": "boc-news", "kind": "official_list",
         "url": "https://www.boc.cn/a/", "bank": "boc"},
        {"id": "abc-news", "kind": "official_list",
         "url": "https://www.abchina.com/a/", "bank": "abc"},
    ])
    items, failures = bot.collect_agent(
        targets, today="2026-11-12", timeout=1, profile="moni"
    )
    assert [it["bank"] for it in items] == ["abc"]
    assert [f["target"] for f in failures] == ["boc-news"]


def test_collect_agent_budget_exhaustion_is_recorded(bot, monkeypatch):
    monkeypatch.setattr(
        bot, "call_agent",
        lambda prompt, *, timeout, profile: json.dumps([]),
    )
    targets = bot.normalize_targets([
        {"id": "t1", "kind": "official_list", "url": "https://e.com/1", "bank": "boc"},
        {"id": "t2", "kind": "official_list", "url": "https://e.com/2", "bank": "abc"},
    ])
    items, failures = bot.collect_agent(
        targets, today="2026-11-12", timeout=1, profile="moni", budget=1
    )
    assert items == []
    assert [f["target"] for f in failures] == ["t1", "t2"]
    assert all("budget" in f["error"] or "timed out" in f["error"] for f in failures)


def test_call_agent_nonzero_exit_raises(bot, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(3, "", "usage error"))
    with pytest.raises(bot.TargetError, match="agent exited 3"):
        bot.call_agent("hi", timeout=1, profile="moni")


def test_call_agent_timeout_raises(bot, monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(bot, "_kill_group", lambda pid: killed.append(pid))
    monkeypatch.setattr(subprocess, "Popen", _fake_popen(timeout=True))
    with pytest.raises(bot.TargetError, match="timed out"):
        bot.call_agent("hi", timeout=1, profile="moni")
    assert killed == [_FakeProc.pid]


# ── 合并 / 落盘 ─────────────────────────────────────────────────────────

def test_merge_items_dedupes_by_url(bot):
    a = [{"id": "i1", "url": "https://e.com/1", "created_at": "2026-11-11"}]
    b = [
        {"id": "i2", "url": "https://e.com/1", "created_at": "2026-11-11"},
        {"id": "i3", "url": "https://e.com/2", "created_at": "2026-11-12"},
    ]
    merged = bot.merge_items(a, b)
    assert [m["id"] for m in merged] == ["i3", "i1"]


def test_main_writes_dated_and_latest(tmp_path, bot, monkeypatch):
    monkeypatch.setattr(bot, "_http_get", lambda url, timeout: RSS2.encode("utf-8"))
    targets = {"targets": [
        {"id": "t1", "kind": "media_rss", "url": "https://e.com/feed",
         "publisher": "测试媒体"},
    ]}
    # 走 stdin 更贴近 fetcher 的真实调用
    import io
    import sys as _sys
    monkeypatch.setattr(_sys, "stdin", io.StringIO(json.dumps(targets)))
    code = bot.main([
        "--output-dir", str(tmp_path / "feed"),
        "--date", "2026-11-12",
        "--no-agent",
    ])
    assert code == 0
    dated = json.loads((tmp_path / "feed" / "2026-11-12.json").read_text("utf-8"))
    latest = json.loads((tmp_path / "feed" / "latest.json").read_text("utf-8"))
    assert dated == latest
    assert dated["date"] == "2026-11-12"
    assert dated["counts_by_bank"] == {"ccb": 1}
    assert dated["errors"] == []


def test_main_refuses_to_write_empty_feed_on_total_failure(tmp_path, bot, monkeypatch):
    def _fail(url, timeout):
        raise bot.TargetError(f"GET {url} -> HTTP 500")

    monkeypatch.setattr(bot, "_http_get", _fail)
    import io
    import sys as _sys
    monkeypatch.setattr(
        _sys, "stdin",
        io.StringIO(json.dumps({"targets": [
            {"id": "t1", "kind": "media_rss", "url": "https://e.com/feed"},
        ]})),
    )
    code = bot.main([
        "--output-dir", str(tmp_path / "feed"), "--date", "2026-11-12", "--no-agent",
    ])
    assert code == 2
    assert not (tmp_path / "feed" / "2026-11-12.json").exists()


def test_validate_ok_and_bad(tmp_path, bot):
    feed = tmp_path / "feed"
    feed.mkdir()
    good = {
        "date": "2026-11-12",
        "items": [{
            "id": "ccb-1", "url": "https://e.com/1", "title": "建设银行发布业绩",
            "bank": "ccb", "source_type": "media", "publisher": "东方财富",
        }],
    }
    (feed / "2026-11-12.json").write_text(json.dumps(good), encoding="utf-8")
    assert bot.main(["--validate", "--output-dir", str(feed)]) == 0

    good["items"][0]["bank"] = "zzz"
    good["items"][0]["url"] = "not-a-url"
    (feed / "2026-11-12.json").write_text(json.dumps(good), encoding="utf-8")
    assert bot.main(["--validate", "--output-dir", str(feed)]) == 2


def test_validate_empty_dir(tmp_path, bot):
    assert bot.main(["--validate", "--output-dir", str(tmp_path / "none")]) == 2


# ── 契约收紧 (v1.0.76 审计修复) ───────────────────────────────────────────

def _run_main(tmp_path, bot, monkeypatch, *, rss: str, targets: list[dict],
              extra: list[str] | None = None) -> int:
    import io
    import sys as _sys

    monkeypatch.setattr(bot, "_http_get", lambda url, timeout: rss.encode("utf-8"))
    monkeypatch.setattr(_sys, "stdin", io.StringIO(json.dumps({"targets": targets})))
    return bot.main([
        "--output-dir", str(tmp_path / "feed"),
        "--date", "2026-11-12",
        "--no-agent",
        *(extra or []),
    ])


def test_main_refuses_empty_feed_even_without_errors(tmp_path, bot, monkeypatch):
    """0 条 ≠ 今天没新闻: 没有任何 error 时也不许写空 feed。"""
    rss = RSS2.replace("建设银行举办2026年对公客户经理技能竞赛", "今日 A 股收盘")
    code = _run_main(
        tmp_path, bot, monkeypatch,
        rss=rss,
        targets=[{"id": "t1", "kind": "media_rss", "url": "https://e.com/feed"}],
    )
    assert code == 2
    assert not (tmp_path / "feed" / "2026-11-12.json").exists()


def test_collect_media_emits_one_item_for_multi_bank_title(bot, monkeypatch):
    rss = RSS2.replace(
        "建设银行举办2026年对公客户经理技能竞赛",
        "工商银行与建设银行签署战略合作协议",
    )
    monkeypatch.setattr(bot, "_http_get", lambda url, timeout: rss.encode("utf-8"))
    items = bot.collect_media(
        {"id": "t1", "kind": "media_rss", "url": "https://e.com/feed",
         "publisher": "测试媒体", "max_items": 12},
        timeout=1,
    )
    # 同 url 的多条会被 moni 的文章 id 合并, 只产出一条才有意义
    multi = [it for it in items if it["url"] == "https://example.com/news/1.html"]
    assert len(multi) == 1
    assert multi[0]["bank"] in ("icbc", "ccb")


def test_rows_to_items_truncates_to_max_items(bot):
    rows = [
        {"title": f"建设银行新闻{i}", "url": f"https://e.com/{i}",
         "text": "建设银行正文", "created_at": "2026-11-12"}
        for i in range(5)
    ]
    target = {"id": "t1", "kind": "official_list", "bank": "ccb",
              "publisher": "中国建设银行", "max_items": 2}
    assert len(bot._rows_to_items(rows, target)) == 2


def test_item_problems_rejects_bad_source_type_and_oversize(bot):
    item = {
        "id": "ccb-1", "url": "https://e.com/1", "title": "建设银行发布业绩",
        "bank": "ccb", "source_type": "blog", "publisher": "测试",
    }
    assert any("source_type" in p for p in bot.item_problems(item, 0))

    item["source_type"] = "media"
    item["title"] = "建" * 301
    assert any("title" in p for p in bot.item_problems(item, 0))
