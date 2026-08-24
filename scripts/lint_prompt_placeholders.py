#!/usr/bin/env python3
"""sprint-16.5 lint: moni/llm/prompts/*.md 模板占位符风格守卫.

moni 有 3 套 prompt 渲染机制:
  1. _render() regex `{name}` × 4 .md 模板 (summary.md / fact_check.md /
     fact_check_plus.md / predict_tag.md) ← 本脚本扫这 4 个
  2. string.Template `$name` × case_extraction.txt ← 不扫 (不同渲染器)
  3. str.format() `{name}` × event_extraction.txt ← 不扫 (不同渲染器)

sprint-16 P1 教训: fact_check_plus.md 自 v1.0.22 ship 起一直用 `$name`,
但 _render() 只认 `{name}`, 占位符原样发 LLM → 拒答 → job failed. v1.0.35.9
修了. 本 lint 防止未来新模板 / 老模板改动再混 `$xxx` 进去.

规则: 4 个 regex-rendered .md 模板里, 任何 `$[a-zA-Z_][a-zA-Z0-9_]*` (dollar 前缀
的 identifier) 都报错退出 1. 例外: ````json ... ```` 代码块里的内容 (因为 JSON
示例可能含 `$` 价格符号 — 但实际现在 4 模板都 clean, 这规则是"将来用").

用法:
  python scripts/lint_prompt_placeholders.py           # 默认扫 moni/llm/prompts/
  python scripts/lint_prompt_placeholders.py --root /path/to/moni  # 自定义 root
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# 4 个 regex-rendered 模板 (跟 moni/llm/prompts/__init__.py::_render() 一致)
TARGETS = ("summary.md", "fact_check.md", "fact_check_plus.md", "predict_tag.md")

# dollar 前缀的 identifier — 这才是占位符形态, 不是价格 ($1B) 或数学
_DOLLAR_PLACEHOLDER_RE = re.compile(r"\$([a-zA-Z_][a-zA-Z0-9_]*)")

# ```json ... ``` 代码块 (允许含 `$` 价格)
_FENCED_CODE_BLOCK_RE = re.compile(r"```[^\n]*\n.*?\n```", re.DOTALL)


def strip_code_blocks(text: str) -> str:
    """去掉 fenced code block 内容, 只 lint 文本段."""
    return _FENCED_CODE_BLOCK_RE.sub("", text)


def lint_file(path: Path) -> list[tuple[int, str]]:
    """返回 (line_no, hit_str) 列表, empty = clean."""
    text = path.read_text(encoding="utf-8")
    stripped = strip_code_blocks(text)
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(stripped.splitlines(), 1):
        for m in _DOLLAR_PLACEHOLDER_RE.finditer(line):
            hits.append((lineno, m.group(0)))
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(
        description="lint moni/llm/prompts/*.md 模板占位符风格 (只允许 {name}, 不允许 $xxx)"
    )
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent.parent),
        help="moni repo root (default: 脚本父目录的父目录)",
    )
    args = parser.parse_args()

    prompts_dir = Path(args.root) / "moni" / "llm" / "prompts"
    if not prompts_dir.is_dir():
        print(f"ERROR: {prompts_dir} not found", file=sys.stderr)
        return 2

    all_hits: dict[str, list[tuple[int, str]]] = {}
    for name in TARGETS:
        p = prompts_dir / name
        if not p.is_file():
            print(f"WARN: {p} missing, skipping", file=sys.stderr)
            continue
        hits = lint_file(p)
        if hits:
            all_hits[name] = hits

    if all_hits:
        print("FAIL: regex-rendered 模板发现 dollar 占位符 ($xxx 形态)", file=sys.stderr)
        print("    _render() 只认 {name}, $xxx 会原样发 LLM → 拒答 'unfilled placeholders'", file=sys.stderr)
        print("    修法: $name → {name} (跟 _render() regex 对齐)", file=sys.stderr)
        print("", file=sys.stderr)
        for name, hits in all_hits.items():
            for lineno, hit in hits:
                print(f"  {name}:{lineno}: {hit}", file=sys.stderr)
        return 1

    print(f"OK: {len(TARGETS)} 个 regex-rendered 模板全 clean (无 $xxx 占位符)")
    return 0


if __name__ == "__main__":
    sys.exit(main())