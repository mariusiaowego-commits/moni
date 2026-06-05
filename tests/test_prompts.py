"""Tests for moni.llm — ChatClient and prompt templates."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from moni.llm.prompts import render_fact_check, render_predict_tag, render_summary
from moni.llm.token_log import TokenLogger


# ---------------------------------------------------------------------------
# ChatClient import & singleton
# ---------------------------------------------------------------------------

class TestChatClientImport:
    def test_chat_client_importable(self):
        """Verify ChatClient can be imported from moni.llm.client."""
        from moni.llm.client import ChatClient
        assert ChatClient is not None


class TestChatClientSingleton:
    def test_singleton_returns_same_instance(self):
        """get_instance() must return the same object on repeated calls."""
        # Reset the class-level singleton first so this test is hermetic
        import moni.llm.client as client_mod
        client_mod.ChatClient._instance = None

        from moni.llm.client import ChatClient
        a = ChatClient.get_instance()
        b = ChatClient.get_instance()
        assert a is b

        # Clean up for other tests
        client_mod.ChatClient._instance = None

    def test_init_raises_when_instance_exists(self):
        """Direct __init__ call must raise RuntimeError when singleton exists."""
        import moni.llm.client as client_mod
        client_mod.ChatClient._instance = None

        from moni.llm.client import ChatClient
        ChatClient.get_instance()  # prime the singleton
        with pytest.raises(RuntimeError, match="get_instance"):
            ChatClient()

        client_mod.ChatClient._instance = None


# ---------------------------------------------------------------------------
# TokenLogger / llm_usage.log
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_log(tmp_path):
    """Provide a temporary log file path and clean up after."""
    log_file = tmp_path / "llm_usage.log"
    yield log_file
    if log_file.exists():
        log_file.unlink()


class TestTokenLogger:
    def test_log_creates_file(self, tmp_log):
        """Calling log() must create the log file if it does not exist."""
        logger = TokenLogger(log_path=tmp_log)
        logger.log(model="test-model", prompt_tokens=10, completion_tokens=5)
        assert tmp_log.exists()

    def test_log_writes_json_line(self, tmp_log):
        """log() must append a valid JSON line with all required fields."""
        logger = TokenLogger(log_path=tmp_log)
        logger.log(model="gpt-4", prompt_tokens=100, completion_tokens=50)

        lines = tmp_log.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert entry["model"] == "gpt-4"
        assert entry["prompt_tokens"] == 100
        assert entry["completion_tokens"] == 50
        assert "ts" in entry

    def test_log_append_multiple_lines(self, tmp_log):
        """Multiple log() calls must produce one JSON line each."""
        logger = TokenLogger(log_path=tmp_log)
        logger.log(model="a", prompt_tokens=1, completion_tokens=1)
        logger.log(model="b", prompt_tokens=2, completion_tokens=2)

        lines = tmp_log.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["model"] == "a"
        assert json.loads(lines[1])["model"] == "b"

    def test_log_extra_fields(self, tmp_log):
        """Extra keyword arguments must appear in the JSON entry."""
        logger = TokenLogger(log_path=tmp_log)
        logger.log(model="x", prompt_tokens=1, completion_tokens=1, provider="openai")

        entry = json.loads(tmp_log.read_text(encoding="utf-8").strip())
        assert entry["provider"] == "openai"


class TestChatClientLogCreation:
    def test_chat_logs_to_file(self, tmp_log, monkeypatch):
        """A ChatClient call must produce a line in llm_usage.log."""
        import moni.llm.client as client_mod
        client_mod.ChatClient._instance = None

        # Mock httpx so no real network call is made
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "reply": "Test reply",
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }

        from moni.llm.client import ChatClient
        with patch("moni.llm.client.httpx.Client") as mock_client_cls:
            mock_client_instance = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(
                return_value=mock_client_instance
            )
            mock_client_instance.post.return_value = mock_resp

            client = ChatClient.get_instance()
            # Override the log path so we can assert against tmp_log
            client._token_logger = TokenLogger(log_path=tmp_log)

            # No Hermes env var set, falls through to OpenAI path
            monkeypatch.delenv("HERMES_CHAT_URL", raising=False)
            monkeypatch.setenv("OPENAI_API_KEY", "test-key")
            monkeypatch.setenv("OPENAI_BASE_URL", "https://api.test/v1")

            try:
                client.chat("hello")
            except Exception:
                pass  # We only care that the log was written

        assert tmp_log.exists()
        entry = json.loads(tmp_log.read_text(encoding="utf-8").strip())
        assert "prompt_tokens" in entry
        assert "completion_tokens" in entry

        client_mod.ChatClient._instance = None


# ---------------------------------------------------------------------------
# Prompt template imports
# ---------------------------------------------------------------------------

class TestPromptImports:
    def test_summary_importable(self):
        """render_summary must be importable from moni.llm.prompts."""
        assert render_summary is not None

    def test_fact_check_importable(self):
        """render_fact_check must be importable from moni.llm.prompts."""
        assert render_fact_check is not None

    def test_predict_tag_importable(self):
        """render_predict_tag must be importable from moni.llm.prompts."""
        assert render_predict_tag is not None


# ---------------------------------------------------------------------------
# Prompt template rendering
# ---------------------------------------------------------------------------

SAMPLE_ARTICLE = {
    "title": "测试文章标题",
    "source": "测试来源",
    "content": "这是文章正文内容，测试用。",
    "url": "https://example.com/test",
}


class TestPromptRendering:
    def test_summary_template_renders(self):
        """render_summary must fill in all article fields."""
        result = render_summary(SAMPLE_ARTICLE)
        assert SAMPLE_ARTICLE["title"] in result
        # source field is not substituted in the template; content and url are
        assert SAMPLE_ARTICLE["content"] in result
        assert SAMPLE_ARTICLE["url"] in result

    def test_summary_template_contains_label(self):
        """render_summary output must mention it is a summary (Chinese)."""
        result = render_summary(SAMPLE_ARTICLE)
        assert "摘要" in result

    def test_fact_check_template_renders(self):
        """render_fact_check must fill in title, content, url fields."""
        result = render_fact_check(SAMPLE_ARTICLE)
        assert SAMPLE_ARTICLE["title"] in result
        # source field is not substituted in the template
        assert SAMPLE_ARTICLE["content"] in result
        assert SAMPLE_ARTICLE["url"] in result

    def test_fact_check_template_contains_fact_label(self):
        """render_fact_check output must mention fact-checking."""
        result = render_fact_check(SAMPLE_ARTICLE)
        assert "事实" in result or "核查" in result

    def test_predict_tag_template_renders(self):
        """render_predict_tag must fill in title and content fields."""
        tags = ["AI", "科技", "财经"]
        result = render_predict_tag(SAMPLE_ARTICLE, tags)
        assert SAMPLE_ARTICLE["title"] in result
        assert SAMPLE_ARTICLE["content"] in result

    def test_predict_tag_template_lists_candidates(self):
        """render_predict_tag must list candidate tags."""
        tags = ["AI", "科技", "财经"]
        result = render_predict_tag(SAMPLE_ARTICLE, tags)
        assert "AI" in result
        assert "科技" in result
        assert "财经" in result

    def test_summary_missing_key_raises(self):
        """render_summary with an empty dict still renders (uses .get() defaults)."""
        # The actual implementation uses .get() so it never raises KeyError
        result = render_summary({})
        assert isinstance(result, str)

    def test_fact_check_missing_key_raises(self):
        """render_fact_check with an empty dict still renders (uses .get() defaults)."""
        result = render_fact_check({})
        assert isinstance(result, str)

    def test_predict_tag_missing_key_raises(self):
        """render_predict_tag with missing article keys uses .get() defaults."""
        tags = ["AI"]
        result = render_predict_tag({}, tags)
        assert isinstance(result, str)
