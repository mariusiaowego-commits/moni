#!/usr/bin/env bash
# setup-hermes-fallback.sh — 配置 hermes fallback chain (sprint-12 C)
#
# 目标: 给 hermes 配 fallback chain (按序尝试):
#   1. minimax-cn / MiniMax-M3
#   2. google    / gemini-2.5-flash-lite
# 作用: moni share worker 调 hermes 生图/精读时, 即使默认 provider (nous)
#       限流/欠费, 也会自动 fallback, 不会假 done。
#
# 为什么不用 `hermes fallback add`:
#   `hermes fallback add` 是 TTY 交互 picker, 无 --provider/--model flag,
#   位置参数直接报 unrecognized (实测 2026-08-14)。脚本改为行级编辑
#   ~/.hermes/config.yaml 的 fallback_providers: 为真 YAML list (保留注释,
#   不整体 dump)。
#
# 幂等: fallback_providers 已是 YAML list 且含全部目标条目 → 跳过。
#       旧字符串格式 ('[{...}]') 会被识别为未配置并重写为真 YAML list。
# 用法: bash scripts/setup-hermes-fallback.sh
set -euo pipefail

CONFIG="${HERMES_CONFIG:-$HOME/.hermes/config.yaml}"

# 目标 chain (有序): provider / model
declare -a TARGETS=(
  "minimax-cn|MiniMax-M3"
  "google|gemini-2.5-flash-lite"
)

echo "== hermes fallback setup (sprint-12 C) =="
echo "config: $CONFIG"

if [ ! -f "$CONFIG" ]; then
  echo "ERROR: $CONFIG not found" >&2
  exit 1
fi

# 真实幂等检查: 用 YAML 解析 fallback_providers, 必须是非空 list 且包含全部目标条目
needs_write=$(/usr/local/bin/python3 - "$CONFIG" <<'PYEOF'
import sys
import yaml

path = sys.argv[1]
targets = [
    {"provider": "minimax-cn", "model": "MiniMax-M3"},
    {"provider": "google", "model": "gemini-2.5-flash-lite"},
]
try:
    with open(path) as f:
        cfg = yaml.safe_load(f)
except Exception:
    print("yes")
    sys.exit(0)

chain = cfg.get("fallback_providers")
if isinstance(chain, list) and all(
    any(
        isinstance(e, dict) and e.get("provider") == t["provider"] and e.get("model") == t["model"]
        for e in chain
    )
    for t in targets
):
    print("no")
else:
    print("yes")
PYEOF
)

if [ "$needs_write" == "no" ]; then
  echo "already configured (YAML list, minimax-cn/MiniMax-M3 + google/gemini-2.5-flash-lite) — idempotent skip"
else
  # 行级替换/追加 fallback_providers 为 YAML list (含全部目标条目)。
  # 保留文件其余内容与注释, 不整体 dump (PyYAML 会丢注释)。
  /usr/local/bin/python3 - "$CONFIG" <<'PYEOF'
import re
import sys

path = sys.argv[1]
targets = [
    ("minimax-cn", "MiniMax-M3"),
    ("google", "gemini-2.5-flash-lite"),
]
with open(path, "r") as f:
    lines = f.readlines()

new_block = ["fallback_providers:\n"]
for provider, model in targets:
    new_block.append(f"  - provider: {provider}\n")
    new_block.append(f"    model: {model}\n")

idx = None
for i, line in enumerate(lines):
    if re.match(r"^fallback_providers\s*:", line):
        idx = i
        break

if idx is None:
    # 没有该 key → 插到 model: 块之后 (文件开头附近)
    insert_at = 1 if lines and lines[0].startswith("model:") else 0
    lines[insert_at:insert_at] = new_block
else:
    # 替换旧行; 吃掉旧 list 格式的缩进子行 (属于 fallback_providers 的)
    lines[idx] = "".join(new_block)
    j = idx + 1
    while j < len(lines):
        line = lines[j]
        if line.startswith("  - ") or line.startswith("    ") or line.startswith("  provider") or line.startswith("  model") or line.startswith("  base_url"):
            lines.pop(j)
        else:
            break

with open(path, "w") as f:
    f.writelines(lines)
print(f"wrote fallback_providers: {[t for t in targets]}")
PYEOF
fi

echo "== hermes fallback list =="
hermes fallback list

# 终验: list 输出必须含两条目标模型
missing=0
for t in "${TARGETS[@]}"; do
  model="${t#*|}"
  if hermes fallback list 2>&1 | grep -q "$model"; then
    echo "OK: $model in chain"
  else
    echo "WARN: fallback list 未显示 $model, 请手动检查" >&2
    missing=1
  fi
done
if [ "$missing" -ne 0 ]; then
  exit 1
fi
echo "OK: fallback chain verified (minimax-cn/MiniMax-M3 + google/gemini-2.5-flash-lite)"
