"""Direction-specific rubrics (design §10).

A direction is a second, independent question set: the model rewrites the ten
general questions for one business direction (e.g. 微信生态), the result is
versioned, validated, and then used to rescore every article with a cache key
of its own.  Nothing here talks to the DB or the model; callers inject both.

The general rubric in :mod:`moni.scoring.rubric` stays the single source of
truth for keys, labels and weights.  Research output may only replace the
question, the four anchors and the reason template.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .bv_pipeline import parse_json_object
from .rubric import (
    BANNED_PATTERN,
    RUBRIC_V1,
    Dimension,
    Rubric,
    banned_hits,
    writing_hits,
)

DIRECTION_PROMPT_ID = "bv_direction_research_v1"
LABEL_MAX_CHARS = 6
QUESTION_MAX_CHARS = 30
ANCHOR_COUNT = 4

STATUS_RUNNING = "running"
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"
RESEARCH_STATUSES = (STATUS_RUNNING, STATUS_PASSED, STATUS_FAILED, STATUS_EXPIRED)

_FIVE_RULES = """\
硬性要求（五条，全部必须满足）：
1. 每个问题保留原 key 的意思，只把评价对象换成本方向；key、label、权重都不许改。
2. 问题一律从银行或支付机构角度问：这件事改变我方什么。
3. 锚点只写正文可见的事实：机构、文号、金额、日期、流程。
4. 不得评价写作，禁止出现这些词：深度、可读、文笔、观点、洞察。
5. 范围外内容一律记 0，不许为本方向牵强关联给分。"""


@dataclass(frozen=True)
class DirectionResearch:
    """One validated research version for a direction."""

    label: str
    brief: str
    scope: str
    out_of_scope: str
    dimensions: tuple[Dimension, ...]
    version: int = 0
    prompt_id: str = DIRECTION_PROMPT_ID

    @property
    def rubric_id(self) -> str:
        return rubric_id_for(self.version)

    def to_payload(self) -> dict[str, Any]:
        """The exact shape stored in ``bv_direction_research.rubric_json``."""
        return {
            "prompt_id": self.prompt_id,
            "scope": self.scope,
            "out_of_scope": self.out_of_scope,
            "dimensions": {
                dim.key: {
                    "question": dim.question,
                    "anchors": dict(zip(("0", "3", "6", "10"), dim.anchors)),
                    "template": dim.template,
                }
                for dim in self.dimensions
            },
        }


def rubric_id_for(version: int) -> str:
    return f"bv_direction_v{int(version)}"


def label_reject_reason(label: str) -> str | None:
    value = (label or "").strip()
    if not value:
        return "方向名不能为空"
    if len(value) > LABEL_MAX_CHARS:
        return f"方向名超过 {LABEL_MAX_CHARS} 字"
    hits = banned_hits(value) + writing_hits(value)
    if hits:
        return f"方向名含禁用词：{'、'.join(hits)}"
    return None


def brief_reject_reason(brief: str) -> str | None:
    if not (brief or "").strip():
        return "方向说明不能为空"
    return None


def build_research_prompt(label: str, brief: str) -> str:
    """Research prompt.  Inputs are the label and brief only — no article body.

    The general rubric is embedded as fixed template text: the model needs the
    original ten questions to preserve their meaning, but it is not per-article
    input and never changes between runs.
    """
    return (
        "你是 moni 业务方向研究员。下面给出一个业务方向，请把固定的十维业务价值问题\n"
        "改写为该方向的专用版本。\n\n"
        f"方向名：{label.strip()}\n"
        f"方向说明：{brief.strip()}\n\n"
        "基准十维（固定模板，key、label、权重不许改）：\n"
        f"{RUBRIC_V1.prompt_block()}\n\n"
        f"{_FIVE_RULES}\n\n"
        "输出要求：\n"
        "- scope：一句话说明这个方向考什么。\n"
        "- out_of_scope：一句话说明哪些内容一律记 0。\n"
        "- 每个问题 ≤30 字；0/3/6/10 四档锚点齐全；理由模板保留 {…} 占位。\n"
        '严格输出 JSON：{"scope":"…","out_of_scope":"…","dimensions":'
        '{"rule_delta":{"question":"…","anchors":{"0":"…","3":"…","6":"…","10":"…"},'
        '"template":"…"}, …共十个 key}}\n'
    )


def parse_research(
    raw: str | Mapping[str, Any] | None,
) -> tuple[DirectionResearch | None, str | None]:
    """Validate one research payload.  Returns ``(research, error)``."""
    if isinstance(raw, Mapping):
        data: Mapping[str, Any] | None = raw
    elif isinstance(raw, str):
        data = parse_json_object(raw)
    else:
        data = None
    if data is None:
        return None, "输出不是 JSON 对象"

    scope = _text(data.get("scope")).strip()
    out_of_scope = _text(data.get("out_of_scope")).strip()
    if not scope:
        return None, "scope 为空"
    if not out_of_scope:
        return None, "out_of_scope 为空"

    dimensions, error = _dimensions_from_payload(data)
    if dimensions is None:
        return None, error
    return (
        DirectionResearch(
            label=_text(data.get("label")),
            brief=_text(data.get("brief")),
            scope=scope,
            out_of_scope=out_of_scope,
            dimensions=dimensions,
        ),
        None,
    )


def research_to_rubric(research: DirectionResearch, version: int | None = None) -> Rubric:
    """Attach the validated question set to a numbered rubric."""
    numbered = research.version if version is None else version
    return Rubric(rubric_id_for(numbered), research.dimensions)


def usable_research(
    rows: Iterable[Mapping[str, Any]],
    *,
    label: str,
    brief: str,
) -> tuple[DirectionResearch | None, str | None]:
    """Gate on the highest ``research_version`` row.  Never falls back.

    ``rows`` may be ``sqlite3.Row`` or dicts with ``research_version``,
    ``status``, ``label_snapshot``, ``brief_snapshot`` and ``rubric_json``.
    """
    latest = _latest_row(rows)
    if latest is None:
        return None, "还没有研究版本"

    version = _int(_get(latest, "research_version"))
    status = _text(_get(latest, "status"))
    if status != STATUS_PASSED:
        return None, f"最新版本 v{version} 状态为 {status or '未知'}"
    if _text(_get(latest, "label_snapshot")).strip() != (label or "").strip():
        return None, "方向名与最新研究快照不一致"
    if _text(_get(latest, "brief_snapshot")).strip() != (brief or "").strip():
        return None, "方向说明与最新研究快照不一致"

    payload = parse_json_object(_text(_get(latest, "rubric_json")))
    if payload is None:
        return None, "研究快照不是合法 JSON"
    dimensions, error = _dimensions_from_payload(payload)
    if dimensions is None:
        return None, error
    return (
        DirectionResearch(
            label=label.strip(),
            brief=brief.strip(),
            scope=_text(_get(latest, "scope")) or _text(payload.get("scope")),
            out_of_scope=_text(_get(latest, "out_of_scope"))
            or _text(payload.get("out_of_scope")),
            dimensions=dimensions,
            version=version,
            prompt_id=_text(_get(latest, "prompt_id")) or DIRECTION_PROMPT_ID,
        ),
        None,
    )


def _dimensions_from_payload(
    data: Mapping[str, Any] | None,
) -> tuple[tuple[Dimension, ...] | None, str | None]:
    if data is None:
        return None, "输出不是 JSON 对象"
    raw_dimensions = data.get("dimensions")
    if not isinstance(raw_dimensions, Mapping):
        return None, "dimensions 缺失"

    expected = RUBRIC_V1.by_key
    missing = [key for key in expected if key not in raw_dimensions]
    if missing:
        return None, f"维度缺失：{'、'.join(missing)}"
    extra = [str(key) for key in raw_dimensions if key not in expected]
    if extra:
        return None, f"维度键多余：{'、'.join(extra)}"

    out: list[Dimension] = []
    for key, base in expected.items():
        item = raw_dimensions[key]
        if not isinstance(item, Mapping):
            return None, f"维度格式不对：{key}"

        question = _text(item.get("question")).strip()
        if not question:
            return None, f"问题为空：{key}"
        if len(question) > QUESTION_MAX_CHARS:
            return None, f"问题超过 {QUESTION_MAX_CHARS} 字：{key}"

        anchors, error = _anchors(item.get("anchors"), key)
        if anchors is None:
            return None, error

        template = _text(item.get("template")).strip()
        if not template:
            return None, f"理由模板为空：{key}"

        hit = _banned_in(key, (question, *anchors, template))
        if hit:
            return None, hit

        out.append(
            Dimension(
                key=key,
                label=base.label,
                weight=base.weight,
                question=question,
                anchors=anchors,
                template=template,
            )
        )
    return tuple(out), None


def _anchors(raw: Any, key: str) -> tuple[tuple[str, str, str, str] | None, str | None]:
    if isinstance(raw, Mapping):
        values = [_text(raw.get(level)).strip() for level in ("0", "3", "6", "10")]
    elif isinstance(raw, (list, tuple)):
        values = [_text(value).strip() for value in raw]
    else:
        return None, f"锚点格式不对：{key}"
    if len(values) != ANCHOR_COUNT:
        return None, f"锚点不足四档：{key}"
    if not all(values):
        return None, f"锚点有空档：{key}"
    return (values[0], values[1], values[2], values[3]), None


def _banned_in(key: str, texts: tuple[str, ...]) -> str | None:
    for text in texts:
        hits = banned_hits(text) + writing_hits(text)
        if hits:
            return f"禁词命中：{key}：{'、'.join(hits)}"
        if BANNED_PATTERN.search(text):
            return f"禁词命中：{key}：不是X而是Y"
    return None


def _latest_row(rows: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    latest: Mapping[str, Any] | None = None
    latest_version = -1
    for row in rows:
        version = _int(_get(row, "research_version"))
        if version > latest_version:
            latest, latest_version = row, version
    return latest


def _get(row: Mapping[str, Any], key: str) -> Any:
    try:
        return row[key]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return None


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "DirectionResearch",
    "LABEL_MAX_CHARS",
    "DIRECTION_PROMPT_ID",
    "QUESTION_MAX_CHARS",
    "RESEARCH_STATUSES",
    "STATUS_EXPIRED",
    "STATUS_FAILED",
    "STATUS_PASSED",
    "STATUS_RUNNING",
    "brief_reject_reason",
    "build_research_prompt",
    "label_reject_reason",
    "parse_research",
    "research_to_rubric",
    "rubric_id_for",
    "usable_research",
]
