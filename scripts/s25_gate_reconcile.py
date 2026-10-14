#!/usr/bin/env python3
"""scripts/s25_gate_reconcile.py — D-13 失败集集合级对账 (sprint-25)

为什么需要这个 (warden P2-4):

基线文件里存的是**裸 test id**::

    tests/test_api.py::TestShare::test_get_task_by_id_200

而 pytest 的输出是**带前缀和断言后缀**的::

    FAILED tests/test_api.py::TestShare::test_get_task_by_id_200 - assert 404 == 200

直接做字符串比较会让**同一批失败全部被判成「新增」**。warden 在他自己的
复审里就踩过一次, 第一次算出「NEW failures: 8 项」, 归一化后才是 NONE。

本脚本把两侧统一归一化到**裸 test id**, 再做集合比较 + sha256 对拍。

用法::

    # 1. 采基线 (改动前)
    ./scripts/s25-env.sh /usr/local/bin/python3 -m pytest tests/ -q -p no:cacheprovider \\
        | grep '^FAILED' | sort > /tmp/baseline_failed.txt

    # 2. 改动后再采一次, 然后对账
    ./scripts/s25-env.sh /usr/local/bin/python3 -m pytest tests/ -q -p no:cacheprovider \\
        | grep '^FAILED' | sort > /tmp/head_failed.txt
    ./scripts/s25-env.sh /usr/local/bin/python3 scripts/s25_gate_reconcile.py \\
        /tmp/baseline_failed.txt /tmp/head_failed.txt

退出码: 0 = 失败集完全相同; 1 = 有新增或消失的失败 (门禁不过)。
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

FAILED_PREFIX = "FAILED "
ASSERTION_SEP = " - "


def normalize(lines: list[str]) -> list[str]:
    """Reduce a pytest FAILED line (or a bare test id) to the bare test id.

    Handles all three shapes seen in practice:
      * bare id            -> tests/x.py::Test::test_y
      * FAILED <id>        -> tests/x.py::Test::test_y
      * FAILED <id> - msg  -> tests/x.py::Test::test_y
    """
    out = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith(FAILED_PREFIX):
            line = line[len(FAILED_PREFIX):].strip()
        # The assertion text is everything after the first " - "; a test id
        # never contains " - ", so this split is safe.
        if ASSERTION_SEP in line:
            line = line.split(ASSERTION_SEP, 1)[0].strip()
        if line:
            out.append(line)
    return sorted(out)


def digest(ids: list[str]) -> str:
    return hashlib.sha256("\n".join(ids).encode()).hexdigest()[:32]


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    base_p, head_p = Path(sys.argv[1]), Path(sys.argv[2])
    for p in (base_p, head_p):
        if not p.is_file():
            print(f"MISSING: {p}")
            return 2

    base = normalize(base_p.read_text(encoding="utf-8").splitlines())
    head = normalize(head_p.read_text(encoding="utf-8").splitlines())

    new = [x for x in head if x not in set(base)]
    gone = [x for x in base if x not in set(head)]

    print("D-13 failure-set reconciliation (normalized to bare test ids)")
    print(f"  baseline : {len(base):>3d}  sha256[:32] = {digest(base)}")
    print(f"  head     : {len(head):>3d}  sha256[:32] = {digest(head)}")
    print(f"  NEW failures      : {new if new else 'NONE'}")
    print(f"  DISAPPEARED       : {gone if gone else 'NONE'}")
    identical = base == head
    print(f"  IDENTICAL SET     : {identical}")
    print()
    print("VERDICT:", "PASS (no new failures)" if identical else "FAIL")
    return 0 if identical else 1


if __name__ == "__main__":
    sys.exit(main())