"""sprint-12 v1.0.31 — worker hermes 调用失败检测 (B) 单元测试。

mock ``subprocess.Popen``, 模拟 hermes stdout:

- stdout 含 "API call failed" / "HTTP 404" → ``run_hermes`` 返回非零退出码,
  default_worker.sh 现有分支 → ``share_tasks.status=failed``
- 正常 stdout → 退出码 0 → done 路径
"""
from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.expanduser("~/dev/moni"))

from moni.share import worker_llm


def _fake_proc(stdout: str, rc: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.communicate.return_value = (stdout, None)
    proc.returncode = rc
    return proc


def _run(stdout: str, rc: int = 0) -> tuple[str, int]:
    with patch.object(
        worker_llm.subprocess, "Popen", return_value=_fake_proc(stdout, rc)
    ):
        return worker_llm.run_hermes("test prompt", timeout_sec=30)


class TestWorkerLlmFailure:
    def test_stdout_api_call_failed_goes_fail(self):
        stdout, rc = _run("API call failed: 401 authentication error\n", rc=0)
        assert worker_llm.is_llm_failure(stdout)
        assert rc != 0, "stdout 含 API call failed 必须走 fail 路径"

    def test_stdout_http_404_goes_fail(self):
        stdout, rc = _run("HTTP 404: model not found\n", rc=0)
        assert worker_llm.is_llm_failure(stdout)
        assert rc != 0, "stdout 含 HTTP 404 必须走 fail 路径"

    def test_stdout_normal_goes_done(self):
        stdout, rc = _run("这是一段正常回复。\n", rc=0)
        assert not worker_llm.is_llm_failure(stdout)
        assert rc == 0, "正常 stdout 必须走 done 路径"

    def test_http_5xx_marker_also_fails(self):
        stdout, rc = _run("HTTP 503: service unavailable\n", rc=0)
        assert rc != 0

    def test_nonzero_rc_passthrough(self):
        stdout, rc = _run("something failed", rc=3)
        assert rc == 3
        assert stdout == "something failed"

    def test_timeout_returns_124_and_kills_proc(self):
        proc = MagicMock()
        proc.communicate.side_effect = subprocess.TimeoutExpired("hermes", 30)
        with patch.object(worker_llm.subprocess, "Popen", return_value=proc):
            stdout, rc = worker_llm.run_hermes("p", timeout_sec=30)
        assert rc == 124
        assert stdout == ""
        proc.kill.assert_called_once()
        proc.wait.assert_called_once()

    def test_empty_stdout_not_failure(self):
        assert not worker_llm.is_llm_failure("")
        assert not worker_llm.is_llm_failure(None)

    def test_markers_case_insensitive(self):
        assert worker_llm.is_llm_failure("api call failed")
        assert worker_llm.is_llm_failure("Rate Limit exceeded")

    def test_popen_args_include_profile_and_extra_flags(self):
        proc = _fake_proc("ok", rc=0)
        with patch.object(worker_llm.subprocess, "Popen", return_value=proc) as m:
            worker_llm.run_hermes(
                "hi",
                timeout_sec=60,
                extra_args="--provider google -m gemini",
                skill="web",
            )
        argv = m.call_args.args[0]
        assert argv[:4] == ["hermes", "-z", "hi", "--profile"]
        assert "-s" in argv and argv[argv.index("-s") + 1] == "web"
        assert "--provider" in argv and argv[argv.index("--provider") + 1] == "google"
        assert "-m" in argv and argv[argv.index("-m") + 1] == "gemini"
