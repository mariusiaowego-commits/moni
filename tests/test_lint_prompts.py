"""tests/test_lint_prompts.py — sprint-16.5 lint 守卫 pytest 集成.

moni 有 3 套 prompt 渲染机制:
  1. _render() regex {name} × 4 .md 模板
  2. string.Template $name × case_extraction.txt
  3. str.format() {name} × event_extraction.txt

sprint-16 P1 教训: fact_check_plus.md 自 v1.0.22 ship 起一直用 $name,
_render() 不识别, LLM 拒答 "unfilled placeholders" → job failed.
v1.0.35.9 修了. 本测试 + scripts/lint_prompt_placeholders.py 防止再混 $xxx.

跟 tests/test_prompts.py:222/228/235 现有 render_* happy/empty/fallback
测试同款 (但 scope 是 lint, 不是 render).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LINT_SCRIPT = REPO_ROOT / "scripts" / "lint_prompt_placeholders.py"


def _run_lint() -> subprocess.CompletedProcess:
    """调 lint 脚本, 返 CompletedProcess. cwd=REPO_ROOT 让脚本 default root 走对."""
    return subprocess.run(
        [sys.executable, str(LINT_SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


class TestLintPromptsHappy:
    """happy path: 当前 4 个 regex-rendered 模板全 clean, lint exit 0."""

    def test_lint_exits_zero_on_clean_templates(self):
        """v1.0.35.9 ship 后所有模板都用 {name}, lint 应通过."""
        result = _run_lint()
        assert result.returncode == 0, (
            f"lint 应 exit 0, 实得 {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "OK" in result.stdout
        assert "4 个" in result.stdout

    def test_lint_scans_only_four_md_templates(self):
        """lint 应明确扫 4 个 .md, 不扫 .txt (避免误报 case_extraction / event_extraction 的合法 $ 占位符)."""
        result = _run_lint()
        # stdout 提到 4 个 = 确认只扫 4 模板
        assert "4" in result.stdout
        # stderr 不应提到 .txt (case_extraction.txt / event_extraction.txt 故意用 $xxx)
        assert "case_extraction" not in result.stderr
        assert "event_extraction" not in result.stderr


class TestLintPromptsNegative:
    """negative path: 故意 inject $xxx 到模板, lint 应报错.

    用 tmp_path 复制 4 模板 + 注入污染, 调 lint --root 指向 tmp 目录.
    """

    @pytest.fixture
    def contaminated_repo(self, tmp_path: Path) -> Path:
        """构造一个含污染模板的 moni-style repo."""
        prompts = tmp_path / "moni" / "llm" / "prompts"
        prompts.mkdir(parents=True)

        # 复制 4 个真模板
        real_prompts = REPO_ROOT / "moni" / "llm" / "prompts"
        for name in ("summary.md", "fact_check.md", "fact_check_plus.md", "predict_tag.md"):
            (prompts / name).write_text(
                (real_prompts / name).read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        # 故意 inject $title 到 fact_check_plus.md (sprint-16 P1 bug 形态)
        target = prompts / "fact_check_plus.md"
        text = target.read_text(encoding="utf-8")
        text = text.replace("{title}", "$title", 1)  # 只替第一处避免误伤
        target.write_text(text, encoding="utf-8")
        return tmp_path

    def test_lint_exits_one_on_dollar_placeholder(self, contaminated_repo: Path):
        """模板含 $xxx 时, lint 应 exit 1 + stderr 报具体位置."""
        result = subprocess.run(
            [sys.executable, str(LINT_SCRIPT), "--root", str(contaminated_repo)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, (
            f"lint 应 exit 1, 实得 {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "FAIL" in result.stderr
        assert "fact_check_plus.md" in result.stderr
        assert "$title" in result.stderr