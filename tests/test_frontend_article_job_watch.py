"""v1.0.110 阅读器任务跟随 —— 前端静态不变量（PR-3 重写）。

钉住的失败模式（都来自线上实测）：

1. 联网核查只轮询 60 秒（30 次 × 2s），线上同类任务实测 2~5 分钟，
   超时后直接放弃，页面永久停在「⏳ 联网核查中...」。
2. 摘要 / 快速核查设完占位符就再没人回读，任务完成页面也不更新。
3. 等待占位写进 ``articleDetail``，与真实结论抢同一字段；失败后撤不掉。
4. 重进页面接不上仍在跑的任务，只能手动刷新。
5. 点完按钮被 ``navigate('llmJobs')`` 拽离正在读的文章。

PR-3 之后等待态从 ``articleDetail`` 里剥离，按槽位（summary / fact_check）
存在 ``readerJobState``，文案由 ``job_view.js`` 的纯函数产出（node 侧另有
表驱动测试）。本文件只钉「接线有没有接上」。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "moni" / "web" / "static" / "js" / "app.js"
READER_JS = ROOT / "moni" / "web" / "static" / "js" / "reader.js"
INDEX_HTML = ROOT / "moni" / "web" / "static" / "index.html"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _watch_block(src: str) -> str:
    """取 watchArticleJob 方法体（到下一个同级方法定义为止）。"""
    start = src.index("watchArticleJob(jobId, articleId, taskType) {")
    end = src.index("    /** 任务收尾后回读服务端真值", start)
    return src[start:end]


def test_watcher_budget_covers_real_job_duration():
    src = _read(APP_JS)
    assert "const maxAttempts = 30" not in src, "60 秒轮询上限又回来了"
    m = re.search(r"budgetMs = (\d+) \* (\d+) \* (\d+)", _watch_block(src))
    assert m, "跟随器没有明确的轮询预算"
    budget_ms = int(m.group(1)) * int(m.group(2)) * int(m.group(3))
    assert budget_ms >= 10 * 60 * 1000, f"轮询预算 {budget_ms}ms 太短，覆盖不到线上 2~5 分钟的任务"


def test_all_three_article_actions_claim_a_slot_and_watch():
    src = _read(APP_JS)
    for task_type in ("summary", "fact_check", "fact_check_plus"):
        assert f"self._setReaderJob('{task_type}'," in src, (
            f"{task_type} 没有占住槽位：状态条不出现，按钮也不会禁用"
        )
        pattern = rf"watchArticleJob\(data\.job_id, [^,]+, '{task_type}'\)"
        assert re.search(pattern, src), f"{task_type} 任务没有启动跟随器，等待态会永久卡住"


def test_failure_and_timeout_always_reload_article_detail():
    block = _watch_block(_read(APP_JS))
    fail_at = block.index("if (view.phase === 'failed' || view.phase === 'stale' || view.phase === 'cancelled') {")
    fail_body = block[fail_at : block.index("const elapsed = Date.now() - startedAt;")]
    assert "_reloadArticleAfterJob(articleId)" in fail_body, "失败/取消后没有回读详情，旧结论与状态条对不上"

    timeout_at = block.index("if (elapsed > budgetMs) {")
    timeout_body = block[timeout_at : block.index("self._readerJobHandles[key] = setTimeout(", timeout_at)]
    assert "_reloadArticleAfterJob(articleId)" in timeout_body, "超时后没有回读详情"
    assert "gaveUp: true" in timeout_body, "超时后没有标记「仍在后台运行」，界面会假装任务没了"


def test_done_path_refreshes_detail_and_clears_the_slot():
    block = _watch_block(_read(APP_JS))
    done_at = block.index("if (view.phase === 'done') {")
    fail_at = block.index("if (view.phase === 'failed' || view.phase === 'stale' || view.phase === 'cancelled') {")
    done_body = block[done_at:fail_at]
    assert "_reloadArticleAfterJob(articleId)" in done_body, "任务完成后没有回读详情"
    assert "loadArticleEvidence(articleId)" in done_body, "联网核查完成后没有重载证据链"
    assert "_clearReaderJob(slot)" in done_body, "完成后槽位没交还，状态条会一直挂着"


def test_reopen_resumes_active_jobs_by_article_id():
    src = _read(APP_JS)
    start = src.index("resumeArticleJobs(articleId) {")
    block = src[start : src.index("    /** 这条异步结果是否还属于阅读器里正在看的那篇。", start)]
    assert "article_id=" in block, "续跑没有按 article_id 过滤"
    for status in ("pending", "running"):
        assert f"'{status}'" in block, f"续跑没有覆盖 {status} 状态"
    assert "watchArticleJob(job.id, articleId, job.task_type)" in block, "查到在跑任务后没有接上跟随器"
    assert "Promise.all(" in block, "两个 status 分开请求会各写各的槽位"


def test_missing_job_settles_instead_of_retrying():
    """job 被清掉（404）时要收摊，不能再空转 30 分钟。"""
    block = _watch_block(_read(APP_JS))
    assert "r.status === 404" in block, "没有识别 404"
    assert "err.status === 404" in block and "stop();" in block, "404 后没有停止轮询"


def test_reader_and_modal_call_resume_on_open():
    reader = _read(READER_JS)
    assert "this.resumeArticleJobs(data.id)" in reader, "打开阅读器没有续上在跑任务"
    app = _read(APP_JS)
    assert "self.resumeArticleJobs(data.id)" in app, "详情弹窗没有续上在跑任务"


def test_closing_the_modal_stops_every_timer():
    """关弹窗必须收掉轮询与秒表，否则用户关了就剩后台空转。

    ⚠️ 实际生效的是 **reader.js** 里的同名方法（app.js 末尾把 window.moniReader
    的属性描述符拷过来，同名覆盖），所以这里扫 reader.js；app.js 那份只是兜底。
    """
    start = _read(READER_JS).index("closeArticleDetail() {")
    reader_src = _read(READER_JS)
    block = reader_src[start : reader_src.index("\n    },", start)]
    assert "stopReaderJobs()" in block, "关弹窗没有停掉任务跟随"

    src = _read(APP_JS)
    stop_at = src.index("stopReaderJobs() {")
    stop_body = src[stop_at : src.index("\n    },", stop_at)]
    assert "clearTimeout(" in stop_body, "定时器没被清掉"
    assert "_stopReaderJobClock()" in stop_body, "秒表没被清掉"


def test_switching_articles_stops_the_previous_watchers_first():
    """换篇（不关弹窗）也要先收摊。

    钉住的 bug：``showArticleDetail`` 里先把 ``articleDetail`` 换成新文章，
    再调 resume；此时「还是不是同一篇」的比较恒为真，旧定时器永远不清理。
    """
    src = _read(READER_JS)
    start = src.index("showArticleDetail(article) {")
    block = src[start : src.index("fetch(`/api/articles/${article.id}`", start)]
    assert "stopReaderJobs()" in block, "换文章前没有停掉上一篇的任务跟随"
    # articleDetail 是在 fetch 回调里才赋值的，所以关键是「收摊必须发生在
    # 发请求之前」—— 排在 guard 之后、fetch 之前。
    assert block.index("stopReaderJobs()") < len(block) - 1
    assert block.index("stopReaderJobs()") > block.index("if (!article || !article.id) return;")


def test_late_reply_cannot_write_into_another_article_slot():
    """fetch 在途期间切走文章：回包属于上一篇，不能写进新文章的槽位。"""
    block = _watch_block(_read(APP_JS))
    then_at = block.index(".then(job => {")
    then_head = block[then_at : block.index("const view = window.moniJobView.viewFor(", then_at)]
    assert "isCurrentReaderArticle(articleId)" in then_head, (
        "回包落地前没有再校验一次文章身份 —— 会写穿新文章的槽位"
    )


def test_stopwatch_only_runs_while_something_is_busy():
    """终态横幅要留着，但它不该让秒表每秒空转。"""
    src = _read(APP_JS)
    start = src.index("_startReaderJobClock() {")
    block = src[start : src.index("\n    },", start)]
    assert "readerJobBusy(" in block, "秒表的存活条件用的是「有槽位」而不是「有任务在跑」，失败后永不停止"


def test_no_placeholder_text_left_in_the_detail_object():
    """等待态不能再往 articleDetail 里写「⏳」，那是失败撤不掉的根源。"""
    src = _read(APP_JS)
    assert "⏳" not in src, "还有入口在往正文里塞占位符"
    assert "_articleJobPlaceholder" not in src, "旧占位符 helper 还在"


def test_status_strip_is_bound_per_slot():
    html = _read(INDEX_HTML)
    for slot in ("summary", "fact_check"):
        assert f"readerJobShow('{slot}')" in html, f"{slot} 槽位没有状态条"
        assert f"readerJobText('{slot}')" in html, f"{slot} 槽位没有接文案"
        assert f"readerJobBusy('{slot}')" in html, f"{slot} 槽位的忙碌态没有接到界面"


def test_rerun_entry_stays_visible_after_a_check_exists():
    """核查完成后按钮不能消失 —— 否则白名单/prompt 更新后用户没有重跑入口。

    两条硬约束：
    1. 联网核查按钮必须常驻（label 在「联网核查 / 重新联网核查」之间切换）；
    2. 快速核查是降级路径，只在没有联网结果时出现 —— 否则它会把联网结论
       覆盖成 llm_inference，而 `has_web_fact_check` 仍是 1，徽标与结论对不上。
    3. 等待态只用 `:disabled` 表达，不许用 `x-show` 把按钮藏起来。
    """
    html = _read(INDEX_HTML)
    start = html.index('class="reader-dock-main"')
    dock = html[start:html.index('class="reader-dock-tools"', start)]

    assert "generateFactCheck()" in dock
    assert "enqueueLlmJobFactCheckPlus(" in dock
    assert 'x-show="!articleDetail?.has_fact_check"' not in dock, "事实核查按钮在核查完成后被隐藏了"
    assert "'重新核查' : '事实核查'" in dock
    assert "'重新联网核查' : '联网核查'" in dock

    fact_check_btn = next(
        line for line in dock.splitlines() if "generateFactCheck()" in line
    )
    web_btn = next(
        line for line in dock.splitlines() if "enqueueLlmJobFactCheckPlus(" in line
    )
    assert 'x-show="!articleDetail?.has_web_fact_check"' in fact_check_btn, \
        "快速核查（降级路径）只在没有联网结果时才能出现"
    assert "x-show=" not in web_btn, "联网核查按钮必须常驻，否则已核查文章没有重跑入口"

    summary_btn = next(line for line in dock.splitlines() if "generateSummary()" in line)
    for name, btn in (("摘要", summary_btn), ("事实核查", fact_check_btn), ("联网核查", web_btn)):
        assert ":disabled=" in btn and "readerJobBusy(" in btn, (
            f"{name}按钮没有接上槽位忙碌态（只能用 :disabled，不许 x-show）"
        )
