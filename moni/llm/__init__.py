"""moni LLM package — thin-shell LLM client + token usage logging + prompt templates.

薄壳模式（PRD §4.4 / §4.7）：moni 不持有任何 API key。
所有 key 由用户在 ~/.hermes/config.yaml 或 ~/.hermes/.env 配好。
moni 通过 subprocess 调本机 `hermes chat` CLI 调 LLM。
"""
from moni.llm.client import (
    ChatClient,         # 旧 API 兼容
    call_llm,           # 薄壳主入口
    call_llm_json,      # 薄壳 + JSON 解析
    estimate_tokens,    # 粗算 token
)
from moni.llm.token_log import TokenLogger

__all__ = [
    "ChatClient", "call_llm", "call_llm_json", "estimate_tokens",
    "TokenLogger",
]
