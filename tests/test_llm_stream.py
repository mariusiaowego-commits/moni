from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from moni.llm.progress import get_job, record_event, start_job, finish_job
from moni.llm.stream import (
    StreamMidwayError,
    parse_event,
    read_stream,
    run_hermes_stream,
)

FIXTURE = Path(__file__).parent / "fixtures" / "hermes_stream_sample.jsonl"


def _proc(lines: list[str]) -> subprocess.Popen[str]:
    script = (
        "import json, sys, time\n"
        "raw = sys.stdin.readline()\n"
        "lines = json.loads(raw) if raw.strip() else []\n"
        "for line in lines:\n"
        "    print(line, flush=True)\n"
        "    time.sleep(0.01)\n"
        "sys.stderr.write('hermes stream failed before result')\n"
        "raise SystemExit(1)\n"
    )
    return subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_stream_events_and_result_are_parsed(tmp_path):
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps({"type": "tool_use", "name": "web_search", "input": {"query": "央行 辽宁"}}),
        json.dumps({"type": "tool_result", "name": "web_search", "duration_ms": 1200, "is_error": False}),
        json.dumps({"type": "text", "text": "部分回答"}),
        json.dumps({"type": "result", "exit_code": 0, "text": "最终回答", "tokens": {"input": 11, "output": 7}}),
    ]
    events = []
    proc = _proc(lines)
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(lines) + "\n")
    proc.stdin.close()
    result = read_stream(proc, on_event=events.append)
    assert result.text == "最终回答"
    assert result.input_tokens == 11
    assert result.output_tokens == 7
    assert [event["type"] for event in events] == ["system", "tool_use", "tool_result", "text", "result"]


def test_failure_before_events_is_runtime_error():
    proc = _proc([])
    assert proc.stdin is not None
    proc.stdin.close()
    with pytest.raises(RuntimeError) as exc_info:
        read_stream(proc)
    assert not isinstance(exc_info.value, StreamMidwayError)


def test_failure_after_events_is_midway_error():
    lines = [json.dumps({"type": "text", "text": "已开始"})]
    proc = _proc(lines)
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(lines) + "\n")
    proc.stdin.close()
    with pytest.raises(StreamMidwayError):
        read_stream(proc)


def test_malformed_lines_do_not_kill_progress():
    assert parse_event("not-json") is None
    assert parse_event(json.dumps([1, 2])) is None


def test_run_hermes_stream_uses_stream_json(monkeypatch):
    calls = []

    class FakeProc:
        stdout = iter([])
        stderr = None
        pid = -1

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(
        "moni.llm.stream.subprocess.Popen",
        lambda argv, **kwargs: calls.append(argv) or FakeProc(),
    )
    with pytest.raises(RuntimeError):
        run_hermes_stream("prompt", provider="p", model="m")
    assert calls
    argv = calls[0]
    assert argv[:6] == ["hermes", "chat", "-q", "prompt", "--format", "stream-json"]
    assert ["--provider", "p"] == argv[-6:-4]
    assert ["-m", "m"] == argv[-4:-2]
    assert ["-t", "web"] == argv[-2:]


def test_progress_registry_reduces_events_and_cleans_up():
    start_job("job")
    record_event("job", {"type": "tool_use", "name": "web_search", "input": {"query": "央行"}})
    first = get_job("job")
    assert first is not None
    assert first["stage"] == "searching"
    assert first["searches"] == 1
    assert first["last_query"] == "央行"

    record_event("job", {"type": "text", "text": "写结论"})
    second = get_job("job")
    assert second is not None
    assert second["stage"] == "writing"
    assert second["searches"] == 1

    finish_job("job")
    assert get_job("job") is None


def test_real_hermes_sample_fixture_reduces_to_progress():
    start_job("fixture-job")
    try:
        for line in FIXTURE.read_text(encoding="utf-8").splitlines():
            record_event("fixture-job", parse_event(line) or {})
        snapshot = get_job("fixture-job")
        assert snapshot is not None
        assert snapshot["stage"] == "writing"
        assert snapshot["searches"] == 2
        assert snapshot["last_query"] == "邮储银行 中信 盛京"
    finally:
        finish_job("fixture-job")
