"""moni.llm.prompts — Loadable prompt templates."""

from __future__ import annotations

from pathlib import Path


def _load(name: str) -> str:
    root = Path(__file__).parent
    return (root / f"{name}.md").read_text(encoding="utf-8")


def render_summary(article: dict) -> str:
    """Render the summary prompt with an article dict.

    Uses ``content`` if present, else ``raw_content``.
    """
    return _load("summary").format(
        title=article.get("title", ""),
        url=article.get("url", ""),
        content=article.get("content") or article.get("raw_content", ""),
    )


def render_fact_check(article: dict) -> str:
    """Render the fact-check prompt with an article dict."""
    return _load("fact_check").format(
        title=article.get("title", ""),
        url=article.get("url", ""),
        content=article.get("content") or article.get("raw_content", ""),
    )


def render_predict_tag(article: dict, available_tags: list[str]) -> str:
    """Render the tag-prediction prompt with an article dict."""
    tags_str = "\n".join(f"- {t}" for t in available_tags)
    return _load("predict_tag").format(
        available_tags=tags_str,
        title=article.get("title", ""),
        content=article.get("content") or article.get("raw_content", ""),
    )
