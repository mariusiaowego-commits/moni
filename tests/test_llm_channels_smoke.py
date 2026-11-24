"""Offline fake-hermes smoke gate for all 5 LLM invocation channels."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import pytest

from moni.llm.reader import run_reader_stream
from moni.llm.runner import CallRequest, FailoverRunner
from moni.llm.stream import run_hermes_stream
from moni.share import summary_gen, worker_llm

REPO_ROOT = Path(__file__).resolve().parents[1]
FAKE_HERMES_SRC = REPO_ROOT / "tests" / "fake_hermes" / "hermes"
BASELINE_PATH = REPO_ROOT / "tests" / "fixtures" / "llm_channel_argv_baseline.json"
EXPECTED_REPLY = "FAKE-REPLY"


@dataclass(frozen=True)
class FakeHermesEnv:
    """Handle for reading invocations logged by the fake hermes binary."""

    bin_path: Path
    log_path: Path
    tmp_dir: Path

    def read_invocations(self) -> list[dict[str, object]]:
        if not self.log_path.is_file():
            return []
        lines = [
            ln.strip()
            for ln in self.log_path.read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        return [json.loads(ln) for ln in lines]


def _load_baseline() -> dict[str, dict[str, object]]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _install_fake_hermes(root: Path) -> tuple[Path, Path]:
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    dst = bin_dir / "hermes"
    shutil.copy2(FAKE_HERMES_SRC, dst)
    dst.chmod(0o755)
    log_path = root / "hermes_calls.jsonl"
    return dst, log_path


@contextmanager
def _fake_hermes_context(tmp_dir: Path) -> Iterator[FakeHermesEnv]:
    dst, log_path = _install_fake_hermes(tmp_dir)
    old_env = {
        key: os.environ.get(key)
        for key in ("PATH", "FAKE_HERMES_LOG", "FAKE_HERMES_REPLY", "PYTHONPATH")
    }
    try:
        os.environ["PATH"] = f"{dst.parent}:{os.environ.get('PATH', '')}"
        os.environ["FAKE_HERMES_LOG"] = str(log_path)
        os.environ["FAKE_HERMES_REPLY"] = EXPECTED_REPLY
        cur_py = old_env["PYTHONPATH"]
        os.environ["PYTHONPATH"] = f"{REPO_ROOT}:{cur_py}" if cur_py else str(REPO_ROOT)

        resolved = shutil.which("hermes")
        assert resolved is not None and Path(resolved).resolve() == dst.resolve(), (
            f"which('hermes')={resolved!r} did not resolve to fake binary {dst}"
        )
        yield FakeHermesEnv(bin_path=dst, log_path=log_path, tmp_dir=tmp_dir)
    finally:
        for key, prev in old_env.items():
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev


@pytest.fixture
def fake_hermes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeHermesEnv:
    """Install fake hermes first on PATH and verify which('hermes') uses it."""
    dst, log_path = _install_fake_hermes(tmp_path)
    monkeypatch.setenv("PATH", f"{dst.parent}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_HERMES_LOG", str(log_path))
    monkeypatch.setenv("FAKE_HERMES_REPLY", EXPECTED_REPLY)
    cur_py = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", f"{REPO_ROOT}:{cur_py}" if cur_py else str(REPO_ROOT))

    resolved = shutil.which("hermes")
    assert resolved is not None and Path(resolved).resolve() == dst.resolve(), (
        f"which('hermes')={resolved!r} did not resolve to fake binary {dst}"
    )
    return FakeHermesEnv(bin_path=dst, log_path=log_path, tmp_dir=tmp_path)


def _assert_argv_matches_baseline(
    channel: str,
    entry: dict[str, object],
    prompt: str,
) -> None:
    spec = _load_baseline()[channel]
    argv = list(entry["argv"])  # type: ignore[arg-type]
    cmd_name = Path(str(entry.get("cmd", "hermes"))).name
    tokens = [cmd_name, *argv]

    assert prompt in argv, f"{channel}: prompt missing as standalone argv item: {argv!r}"

    for required in spec["must_contain"]:  # type: ignore[union-attr]
        assert required in tokens, f"{channel}: missing {required!r} in {tokens!r}"

    profile_flag = spec.get("profile_flag")
    if profile_flag:
        assert profile_flag in argv, f"{channel}: missing {profile_flag!r} in {argv!r}"

    prompt_pos = spec.get("prompt_position")
    if prompt_pos == "after_-z":
        idx = argv.index("-z")
        assert idx + 1 < len(argv) and argv[idx + 1] == prompt
    elif prompt_pos == "after_-q":
        idx = argv.index("-q")
        assert idx + 1 < len(argv) and argv[idx + 1] == prompt


def _run_channel_check(channel: str, env: FakeHermesEnv) -> None:
    prompt = f"smoke-prompt-{channel}-测试招商银行"
    if channel == "runner":
        runner = FailoverRunner.from_settings(fallback_chain=[("dummy", "dummy")])
        res = runner.call(CallRequest(provider="dummy", model_id="dummy", prompt=prompt))
        assert res.reply_text == EXPECTED_REPLY
    elif channel == "stream":
        res = run_hermes_stream(prompt)
        assert res.text == EXPECTED_REPLY
    elif channel == "reader":
        deltas: list[str] = []
        res = run_reader_stream(prompt, on_delta=deltas.append)
        assert res["text"] == EXPECTED_REPLY
        assert "".join(deltas) == EXPECTED_REPLY
    elif channel == "worker_llm":
        tmp_db = env.tmp_dir / "worker.db"
        out_file = env.tmp_dir / "worker_out.txt"
        proc = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "moni" / "share" / "worker_llm.py"),
                "--db",
                str(tmp_db),
                "--output",
                str(out_file),
                "--",
                prompt,
            ],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, f"worker_llm failed rc={proc.returncode}: {proc.stderr}"
        assert EXPECTED_REPLY in out_file.read_text(encoding="utf-8")

        res = worker_llm.run_hermes(prompt, db_path=tmp_db)
        assert res["exit_code"] == 0
        assert EXPECTED_REPLY in res["stdout"]
    elif channel == "summary_gen":
        stdout, rc = summary_gen._invoke_hermes(prompt)
        assert rc == 0
        assert stdout == EXPECTED_REPLY
    else:
        raise ValueError(f"unknown channel: {channel!r}")

    invocations = env.read_invocations()
    assert invocations, f"{channel}: fake hermes was not invoked"
    _assert_argv_matches_baseline(channel, invocations[-1], prompt)


def assert_channel_ok(name: str) -> None:
    """Reusable assertion helper verifying one LLM channel end-to-end offline."""
    with tempfile.TemporaryDirectory() as tmp:
        with _fake_hermes_context(Path(tmp)) as env:
            _run_channel_check(name, env)


def test_channel_runner_smoke(fake_hermes: FakeHermesEnv) -> None:
    _run_channel_check("runner", fake_hermes)


def test_channel_stream_smoke(fake_hermes: FakeHermesEnv) -> None:
    _run_channel_check("stream", fake_hermes)


def test_channel_reader_smoke(fake_hermes: FakeHermesEnv) -> None:
    _run_channel_check("reader", fake_hermes)


def test_channel_worker_llm_smoke(fake_hermes: FakeHermesEnv) -> None:
    _run_channel_check("worker_llm", fake_hermes)


def test_channel_summary_gen_smoke(fake_hermes: FakeHermesEnv) -> None:
    _run_channel_check("summary_gen", fake_hermes)
