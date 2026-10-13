#!/usr/bin/env bash
# sprint-25 test/impl launcher.
#
# Two environment problems this solves:
#
# 1. hermes injects PYTHONPATH=/…/venv/lib/python3.14/site-packages into every
#    shell, which shadows python3.12's own pydantic_core (2.41.5) with an
#    incompatible 3.14 build. Result: `import pydantic_core` raises
#    ModuleNotFoundError even though 3.12 has a working copy.
#    => unset PYTHONPATH first. No pip install needed (dad confirmed 3.12 works).
#
# 2. moni is pip-installed editable against the MAIN checkout, so a worktree
#    would import main's code instead of the branch's.
#    => prepend the worktree root to PYTHONPATH so `import moni` resolves here.
#
# Usage: scripts/s25-env.sh <command...>   e.g. scripts/s25-env.sh pytest tests/ -q
set -euo pipefail

WORKTREE="/Users/mt16/dev/moni-worktrees/26101201-motion"

unset PYTHONPATH
export PYTHONPATH="$WORKTREE"
cd "$WORKTREE"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

export PYTHONDONTWRITEBYTECODE=1
exec "$@"