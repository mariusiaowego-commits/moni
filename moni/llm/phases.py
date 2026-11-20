"""LLM 任务状态机 —— 前后端共用的一套 phase 词表。

为什么单独立一个模块：``llm_jobs.status`` 是**存储**词表（pending / running /
done / failed / stale / cancelled），前端要的是**呈现**词表（多了「没有任务」
的 none，且 pending 在 UI 上叫「排队中」）。两套词表一旦各写各的，就会出现
「后端说 stale、前端当成 unknown」这类对齐事故，所以：

* 后端只用这里的 ``phase_of()`` 派生 phase，不散落 ``if status == ...``；
* 前端 ``job_view.js`` 里有一份同词表，``tests/test_job_view_node.py`` 用
  node 直接比对两边，改动一边不改另一边会立刻红。
"""

from __future__ import annotations

#: 前端可渲染的全部 phase。``none`` = 这篇文章没有在跑的任务。
PHASES = ("none", "queued", "running", "done", "failed", "cancelled", "stale")

#: ``llm_jobs.status`` → phase。未列出的（脏数据）一律退回 ``none``。
STATUS_TO_PHASE = {
    "pending": "queued",
    "running": "running",
    "done": "done",
    "failed": "failed",
    "stale": "stale",
    "cancelled": "cancelled",
}

#: 任务类型 → 阅读器里的展示槽位。快核与联网核查抢同一个槽
#: （同一篇文章的两种核查不可能同时展示，且后跑的覆盖先跑的结论）。
SLOT_OF_TASK = {
    "summary": "summary",
    "fact_check": "fact_check",
    "fact_check_plus": "fact_check",
}

#: 阅读器里存在的槽位（顺序即展示顺序）。
SLOTS = ("summary", "fact_check")


def phase_of(status: object) -> str:
    """把 ``llm_jobs.status`` 映射成前端 phase；未知输入返回 ``none``。"""
    return STATUS_TO_PHASE.get(str(status), "none") if status is not None else "none"


def slot_of(task_type: object) -> str | None:
    """把任务类型映射到槽位；不归阅读器管的类型返回 ``None``。"""
    return SLOT_OF_TASK.get(str(task_type))
