"""v1.0.106 文章级 LLM 任务跟随 —— 前端静态不变量。

钉住的失败模式（都来自线上实测）：

1. 联网核查只轮询 60 秒（30 次 × 2s），而线上同类任务实测 2~5 分钟，
   超时后直接放弃，页面永久停在「⏳ 联网核查中... (job xxxxxxxx)」。
2. 摘要 / 快速核查设完占位符就再没人回读，任务完成页面也不更新。
3. 超时 / 失败后不回读文章详情，占位符不会被真实结论替换。
4. 重进页面（或换设备打开）接不上仍在跑的任务，只能手动刷新。
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
    end = src.index("    _reloadArticleAfterJob(", start)
    return src[start:end]


def test_watcher_budget_covers_real_job_duration():
    src = _read(APP_JS)
    block = _watch_block(src)
    assert "const maxAttempts = 30" not in src, "60 秒轮询上限又回来了"
    m = re.search(r"budgetMs = (\d+) \* (\d+) \* (\d+)", block)
    assert m, "跟随器没有明确的轮询预算"
    budget_ms = int(m.group(1)) * int(m.group(2)) * int(m.group(3))
    assert budget_ms >= 10 * 60 * 1000, f"轮询预算 {budget_ms}ms 太短，覆盖不到线上 2~5 分钟的任务"


def test_all_three_article_actions_start_the_watcher():
    src = _read(APP_JS)
    for task_type in ("summary", "fact_check", "fact_check_plus"):
        pattern = rf"watchArticleJob\(data\.job_id, [^,]+, '{task_type}'\)"
        assert re.search(pattern, src), f"{task_type} 任务没有启动跟随器，占位符会永久卡住"


def test_failure_and_timeout_always_reload_article_detail():
    src = _read(APP_JS)
    block = _watch_block(src)
    fail_at = block.index("if (job.status === 'failed' || job.status === 'cancelled') {")
    fail_body = block[fail_at : block.index("const elapsed = Date.now() - startedAt;")]
    assert "_reloadArticleAfterJob(articleId)" in fail_body, "失败/取消后没有回读详情，占位符撤不掉"

    timeout_at = block.index("if (elapsed > budgetMs) {")
    timeout_body = block[timeout_at : block.index("setTimeout(tick, elapsed < 60000")]
    assert "_reloadArticleAfterJob(articleId)" in timeout_body, "超时后没有回读详情，页面停在不真实的占位符"


def test_done_path_refreshes_detail_and_evidence():
    block = _watch_block(_read(APP_JS))
    done_at = block.index("if (job.status === 'done') {")
    fail_at = block.index("if (job.status === 'failed' || job.status === 'cancelled') {")
    done_body = block[done_at:fail_at]
    assert "_reloadArticleAfterJob(articleId)" in done_body, "任务完成后没有回读详情"
    assert "loadArticleEvidence(articleId)" in done_body, "联网核查完成后没有重载证据链"


def test_reopen_resumes_active_jobs_by_article_id():
    src = _read(APP_JS)
    start = src.index("resumeArticleJobs(articleId) {")
    block = src[start : src.index("    /**", start)]
    assert "article_id=" in block, "续跑没有按 article_id 过滤"
    for status in ("pending", "running"):
        assert f"'{status}'" in block, f"续跑没有覆盖 {status} 状态"
    assert "watchArticleJob(job.id, articleId, job.task_type)" in block, "查到在跑任务后没有接上跟随器"
    assert "Promise.all(" in block, "两个 status 分开回写会并发覆盖 articleDetail"


def test_missing_job_settles_instead_of_retrying():
    """job 被清掉（404）时要收摊，不能再空转 30 分钟。"""
    src = _read(APP_JS)
    block = _watch_block(src)
    assert "r.status === 404" in block, "没有识别 404"
    assert "err.status === 404" in block and "settle();" in block, "404 后没有停止轮询"


def test_reader_and_modal_call_resume_on_open():
    reader = _read(READER_JS)
    assert "this.resumeArticleJobs(data.id)" in reader, "打开阅读器没有续上在跑任务"
    app = _read(APP_JS)
    assert "self.resumeArticleJobs(data.id)" in app, "详情弹窗没有续上在跑任务"


def test_placeholder_text_has_single_source():
    """占位符文案只能由 _articleJobPlaceholder 生成，避免三处手写各写各的。"""
    src = _read(APP_JS)
    assert src.count("`⏳ ") == 1, "占位符文案出现了多处手写拷贝"
    assert "'⏳ " not in src, "还有入口在用字符串拼接占位符"


def test_rerun_entry_stays_visible_after_a_check_exists():
    """核查完成后按钮不能消失 —— 否则白名单/prompt 更新后用户没有重跑入口。

    两条硬约束：
    1. 联网核查按钮必须常驻（label 在「联网核查 / 重新联网核查」之间切换）；
    2. 快速核查是降级路径，只在没有联网结果时出现 —— 否则它会把联网结论
       覆盖成 llm_inference，而 `has_web_fact_check` 仍是 1，徽标与结论对不上。
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
