#!/usr/bin/env bash
set -euo pipefail

# scripts/ci-preflight.sh — 本地预检脚本 (v1.0.55)
# 在把分支推上去之前本地跑一遍 "干净 HOME + 无 Playwright 缓存" 的模拟，
# 提前发现各类"只有本机绿"的不可移植问题。

CLEAN_HOME=$(mktemp -d)
trap 'rm -rf "$CLEAN_HOME"' EXIT

echo "==> Running local CI preflight with clean HOME=$CLEAN_HOME and empty MONI_CHROME_HEADLESS_BINARY..."
HOME="$CLEAN_HOME" MONI_CHROME_HEADLESS_BINARY= \
  /usr/local/bin/python3 -m pytest tests/ -q -p no:cacheprovider
echo "==> CI preflight passed cleanly!"
