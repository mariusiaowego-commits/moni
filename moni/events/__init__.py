"""moni.events — 行业事件日历核心模块。

包含：
- normalize：主体别名归一化、中文日期解析、事件类型映射
- pipeline：三层事务管道（normalize → insert → state-transition）
- repository：events 表的 CRUD 操作

设计原则（v1.0.16）：
* 原生 SQL，不用 ORM
* 三层管道在同一个 SQLite 事务内，任何失败全部回滚，只更新 status + error
* event_id = sha256(article_id + (event_date or '') + subject + event_type)[:16]
"""

from __future__ import annotations

from .normalize import normalize_event, BANK_ALIAS_MAP
from .pipeline import extract_events_for_article, extract_events_batch
from .repository import EventRepository

__all__ = [
    "normalize_event",
    "BANK_ALIAS_MAP",
    "extract_events_for_article",
    "extract_events_batch",
    "EventRepository",
]
