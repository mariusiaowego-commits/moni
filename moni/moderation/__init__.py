"""内容分级标注 (sprint-58).

判定在 ``moni.fetch.moderation.evaluate_labels()``（纯函数），本包只负责
落库、批任务、抽样复核与闸门查询。
"""

from .service import (  # noqa: F401
    GATED_CATEGORIES,
    apply_labels,
    gate_enabled,
    gated_map,
    label_and_store,
    list_runs,
    load_run,
    preview,
    relabel_article,
    report_markdown,
    review_label,
    samples,
    set_gate_enabled,
    summary,
)
