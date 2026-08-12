"""moni.llm.prompts — Loadable prompt templates.

模板用 {name} 占位符，由 render_*() 函数填值。
实现用 regex 而非 str.format()，避免模板里 ```json { ... }``` 块的冲突。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _load(name: str) -> str:
    root = Path(__file__).parent
    return (root / f"{name}.md").read_text(encoding="utf-8")


def _render(template: str, **kwargs: Any) -> str:
    """Replace {name} placeholders with values, leaving unknown names unchanged.

    比 str.format() 优越：
    - 模板里 ```json { "key": "value" } ``` 这种 JSON 示例不会冲突
    - 缺 key 不抛 KeyError，保留原 {name} 占位符
    """
    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        if key in kwargs:
            return str(kwargs[key])
        return m.group(0)  # 保留原占位符
    return _PLACEHOLDER_RE.sub(repl, template)


def render_summary(article: dict, tags: list[str] | None = None) -> str:
    """Render the summary prompt with an article dict and optional tags.

    v1.0.5.24: uses perspective system — selects the best-matching
    perspective based on article tags, then fills the template.

    Uses ``content`` if present, else ``raw_content``.
    """
    from .perspectives import select_perspective

    perspective = select_perspective(tags or [])
    questions_str = "\n".join(
        f"{i+1}. {q}" for i, q in enumerate(perspective["questions"])
    )

    return _render(
        _load("summary"),
        role_name=perspective["name"],
        reader=perspective["reader"],
        focus=perspective["focus"],
        questions=questions_str,
        title=article.get("title", ""),
        url=article.get("url", ""),
        content=article.get("content") or article.get("raw_content", ""),
    )


def render_fact_check(article: dict) -> str:
    """Render the fact-check prompt with an article dict.

    Template includes {published_at} placeholder; missing values are kept as-is.
    """
    return _render(
        _load("fact_check"),
        title=article.get("title", ""),
        url=article.get("url", ""),
        content=article.get("content") or article.get("raw_content", ""),
        published_at=article.get("published_at", ""),
    )


def render_fact_check_plus(article: dict) -> str:
    """Render the fact-check-plus (web-search) prompt with an article dict.

    v1.0.22: networked fact-check. Template is fact_check_plus.md (green-frog).
    Uses regex _render so JSON example braces in the template do not conflict
    (same pattern as sprint 05 / render_fact_check).
    """
    return _render(
        _load("fact_check_plus"),
        title=article.get("title", ""),
        url=article.get("url", ""),
        content=article.get("content") or article.get("raw_content", ""),
        published_at=article.get("published_at", ""),
    )


def render_predict_tag(article: dict, available_tags: list[str]) -> str:
    """Render the tag-prediction prompt with an article dict."""
    tags_str = "\n".join(f"- {t}" for t in available_tags)
    return _render(
        _load("predict_tag"),
        available_tags=tags_str,
        title=article.get("title", ""),
        content=article.get("content") or article.get("raw_content", ""),
    )
