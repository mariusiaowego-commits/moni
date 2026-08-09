"""moni.cases — F6 同业案例库子包。

sprint 05 起始文件 (v1.0.17):
- normalize: 主体别名归一 (100+ 金融机构/科技公司)
- repository: CRUD + state machine (case_lifecycle)
- pipeline: 三层事务 + LLM 抽取 + 三策略 JSON parse
"""

from .normalize import normalize_case, normalize_subject, normalize_case_type  # noqa
from .repository import CaseRepository, _generate_case_id  # noqa