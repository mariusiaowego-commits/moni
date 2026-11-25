#!/usr/bin/env bash
# =============================================================================
# default_worker.sh — moni manifest queue processor
#
# Scans ~/.moni/queue/*.json for pending share_tasks, invokes the default
# profile agent for each via `hermes -z <prompt> --profile default`, and
# updates the DB status to done/failed.
#
# Hermes invocation: `hermes -z "<prompt>" --profile default`
#   -z = inline prompt mode (no interactive chat)
#
# Concurrency: kernel flock on $QUEUE_DIR/.lock (POSIX, macOS + Linux).
#              The flock is acquired by worker_lock_exec.py and survives
#              exec into this script through inherited fd 9.
#
# Dependencies: bash, python3, sqlite3, hermes CLI. No GNU timeout needed.
# =============================================================================

set -euo pipefail

# --- Config ----------------------------------------------------------------
# v1.0.5.18: REPO_ROOT is used by the prompt-lookup code path.  We
# resolve it from the script's own path so the worker finds
# moni/share/prompts/ regardless of how it was launched.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QUEUE_DIR="${MONI_QUEUE_DIR:-${HOME:-~}/.moni/queue}"
# v1.0.49.2 (audit F3): bash 变量展开不会自动做 tilde expansion, 如果用户/cron
# 传入 MONI_QUEUE_DIR=~/custom, 这里必须显式把前缀 ~ 换成真实 $HOME, 否则
# 会在当前工作目录下建一个字面量名为 '~' 的目录, 导致与 Python 侧分裂。
QUEUE_DIR="${QUEUE_DIR/#\~/$HOME}"
LOCK_FILE="${QUEUE_DIR}/.lock"
# v1.0.9.3 fix: handle MONI_DB='' (empty) explicitly.  bash's ${VAR:-default}
# only triggers on unset, not empty.  A caller that explicitly exports
# MONI_DB='' (e.g. a wrapper that wanted to disable the override) would
# silently fall through to the wrong default.  Use ${VAR-default}
# (or an explicit empty-check) so empty means "use project default".
if [[ -n "${MONI_DB:-}" ]]; then
    DB_PATH="${MONI_DB}"
else
    DB_PATH="${REPO_ROOT}/data/news.db"
fi
TIMEOUT_SECONDS="${MONI_WORKER_TIMEOUT:-600}"  # 10 minutes default, override via env

# PNG 任务要先生图再自检（vision_analyze + 像素检查），正常 4-5 分钟，
# 慢的时候会到 11 分钟；统一 600s 会在收尾阶段砍掉已经产出的成品。
# 单独给 PNG 格式更长的窗口，其他格式仍用 TIMEOUT_SECONDS。
task_timeout_for_format() {
    case "$1" in
        info_graphic_png|report_png) printf '%s' "${MONI_WORKER_TIMEOUT_PNG:-1200}" ;;
        *) printf '%s' "$TIMEOUT_SECONDS" ;;
    esac
}

# 产物回收：agent 直接写文件，可能落在 artifacts_dir 之外的 profile cache。
# 找「本次运行开始之后」新出现且内容校验通过的产物，复制成标准输出名。
# reason 只用于日志，区分超时回收和「LLM 正常结束但产物不在 artifacts」。
recover_artifact() {
    local task_id="$1"
    local format="$2"
    local not_before="$3"
    local artifacts_dir="$4"
    local reason="${5:-timeout}"
    local include_ctx="${6:-false}"
    local article_ids="${7:-}"
    local recovered recovered_out
    local output_dir="${MONI_OUTPUT_DIR:-$HOME/.moni/output}"
    output_dir="${output_dir/#\~/$HOME}"

    recovered=$("$PYTHON_BIN" "$REPO_ROOT/moni/share/timeout_recovery.py" \
        --format "$format" \
        --not-before "$not_before" \
        --repo-root "$REPO_ROOT" \
        --artifacts-dir "$artifacts_dir" \
        --profile-dir "$HOME/.hermes/profiles/${MONI_HERMES_PROFILE:-moni}" \
        --extra-dir "$output_dir" \
        2>/dev/null) || true
    [[ -n "$recovered" && -f "$recovered" ]] || return 1

    if [[ "$format" == "html_landing" ]]; then
        recovered_out="$artifacts_dir/${task_id}-${format}.html"
    else
        recovered_out="$artifacts_dir/${task_id}-${format}-0.png"
    fi
    cp -- "$recovered" "$recovered_out" 2>>"$LOG_FILE" || {
        log "FAIL $task_id — copy artifact failed: $recovered"
        return 1
    }
    # 状态置 done 之前完成注入，避免前端轮询到 done 时读到未注入的旧文件。
    if [[ "$format" == "html_landing" && "$include_ctx" == "true" ]]; then
        "$PYTHON_BIN" -m moni.share.context_pack \
            --db "$DB_PATH" --article-ids "$article_ids" \
            --inject-html "$recovered_out" 2>>"$LOG_FILE" || true
    fi
    update_progress "$task_id" "writing_artifact" 95 "回收已产出产物: $recovered"
    task_status_update "$task_id" "done" "$recovered_out"
    sqlite3 "$DB_PATH" \
        "UPDATE share_tasks SET progress_pct=100 WHERE id='$task_id'" \
        >/dev/null 2>&1 || true
    log "RECOVERED $task_id — $reason, $recovered -> $recovered_out"
    return 0
}

# v1.0.5.18: prompt directory.  Relative to the project root so
# the worker finds the per-format prompt files regardless of the
# worker's CWD.  Defaults to ${REPO_ROOT}/moni/share/prompts/.
PROMPT_DIR="${MONI_PROMPT_DIR:-${REPO_ROOT}/moni/share/prompts}"
PYTHON_BIN="${MONI_PYTHON:-/usr/local/bin/python3}"
if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN=python3
fi

# v1.0.14: artifacts + worker log paths.  moni.share.worker.LOG_FILE is
# Python-only; the shell path used to reference $LOG_FILE without defining
# it → under `set -u`, brief html_landing always failed with
# "LOG_FILE: unbound variable" misreported as "brief_html render failed".
ARTIFACTS_DIR="${ARTIFACTS_DIR:-${REPO_ROOT}/data/artifacts}"
LOG_FILE="${MONI_WORKER_LOG:-${ARTIFACTS_DIR}/worker.log}"
mkdir -p "$ARTIFACTS_DIR"
# Touch so 2>>"$LOG_FILE" never fails if parent was just created.
: >>"$LOG_FILE" 2>/dev/null || true

# --- Env validation ---------------------------------------------------------
if [[ ! -d "$QUEUE_DIR" ]]; then
    echo "[worker] queue dir not found: $QUEUE_DIR — nothing to do" >&2
    exit 0
fi

# v1.0.5.26: defense-in-depth PATH prepend.  Cron / launchd subprocs
# inherit a stripped PATH (/usr/bin:/bin) without ~/.local/bin, so
# `hermes` (the AI CLI the worker invokes) is FileNotFoundError →
# share task fails in 1s with empty error_msg.  Adding ~/.local/bin
# here works across cron / launchd / web UI button / manual shell
# without depending on the caller's PATH.  Idempotent.
export PATH="$HOME/.local/bin:$PATH"

# --- Single-instance kernel lock (POSIX, works on macOS + Linux) ----------
# v1.0.49.4 (R3): the directory/claim protocol was removed.  flock() is
# owned by the kernel; it is released automatically when the last process
# referring to the open file description exits, so stale locks, stale
# guards, PID liveness checks and reclaim races no longer exist.
#
# A direct `bash scripts/default_worker.sh` re-enters this same script via
# worker_lock_exec.py, so cron/manual launches cannot bypass the lock.
if [[ "${MONI_WORKER_LOCK_ACQUIRED:-}" != "1" ]]; then
    exec "$PYTHON_BIN" \
        "$REPO_ROOT/scripts/worker_lock_exec.py" \
        "$LOCK_FILE" "${BASH_SOURCE[0]}"
fi

# The helper owns the flock in fd 9 and execs this bash.  Verify fd 9 is
# valid so an accidental call with the marker set cannot claim the lock.
if ! "$PYTHON_BIN" -c '
import os, sys
try:
    os.fstat(9)
except OSError:
    sys.exit(1)
' >/dev/null 2>&1; then
    echo "[worker] lock marker is set but fd 9 is not open — exiting" >&2
    exit 1
fi

# The lock must never be removed: unlink would let a new worker lock a new
# inode while this worker still owns the old one.  A separate result file
# gives Python an explicit "empty queue" signal after the child exits.
_child_pid=""
LOCK_RESULT_FILE="${LOCK_FILE}.result"

_write_worker_result() {
    local result="$1"
    printf '%s\n' "$result" >"$LOCK_RESULT_FILE" 2>/dev/null || true
}

_run_worker_child() {
    local out_file="$1"
    local err_file="$2"
    shift 2

    # Each long child gets its own process group.  This avoids the worker's
    # own group signal reaching an unrelated sibling and makes the child's
    # PGID exactly its PID, so cleanup can target it precisely.
    "$PYTHON_BIN" -c '
import os, sys
os.setsid()
os.execvp(sys.argv[1], sys.argv[1:])
' "$@" >"$out_file" 2>"$err_file" &
    _child_pid=$!
    local rc=0
    wait "$_child_pid" || rc=$?
    _child_pid=""
    return "$rc"
}

_stop_child_group() {
    if [[ -z "${_child_pid:-}" ]]; then
        return 0
    fi
    # On macOS bash 3.2 a trapped TERM interrupts `wait`, but the handler's
    # `exit` stays pending until the wait is re-entered.  Kill the child's
    # own group, with a background watchdog to KILL it if TERM is ignored.
    # Waiting immediately below reaps the child (including zombies), so the
    # normal TERM-responsive path does not pay the full grace period.
    local _child_to_stop="$_child_pid"
    (
        sleep 0.8
        kill -KILL -- "-$_child_to_stop" 2>/dev/null || true
    ) &
    local _kill_watchdog=$!
    kill -TERM -- "-$_child_to_stop" 2>/dev/null || true
    wait "$_child_to_stop" 2>/dev/null || true
    kill "$_kill_watchdog" 2>/dev/null || true
    wait "$_kill_watchdog" 2>/dev/null || true
    _child_pid=""
}

# Append the deterministic user-context appendix to a text artifact.
# Contexts are rendered outside the LLM on purpose: they are the user's
# own notes and must not be rewritten or folded into fact-check copy.
_append_user_context_section() {
    local target="$1"
    local enabled="$2"
    local ids="$3"
    [[ "$enabled" == "true" && -n "$ids" && -f "$target" ]] || return 0
    local section
    section=$("$PYTHON_BIN" -m moni.share.context_pack \
        --db "$DB_PATH" --article-ids "$ids" 2>>"$LOG_FILE") || return 0
    [[ -n "${section//[$'\n\t ']/}" ]] || return 0
    printf '\n\n%s\n' "$section" >>"$target"
}

_worker_cleanup() {
    _stop_child_group
}

trap _worker_cleanup EXIT
trap '_stop_child_group; exit 143' TERM
trap '_stop_child_group; exit 130' INT
trap 'exit 141' PIPE

# --- Helpers ----------------------------------------------------------------

# v1.0.5.17: 5-stage progress checkpoint helper.  Each stage
# transition calls this to write progress_stage / progress_pct /
# progress_message into share_tasks so the UI can show a progress
# bar without parsing the worker log.  The 5 stages (with the
# percentage we typically write):
#   queued            → 0
#   fetching          → 20
#   building_prompt   → 40
#   invoking_llm      → 60  (5-min hermes chat window — biggest delta)
#   writing_artifact  → 95
# The DONE branch in process_manifest bumps to 100 alongside the
# status=done UPDATE.  pct=100 only ever appears with status=done
# so the UI can use pct==100 as a proxy for "row is green".
update_progress() {
    local task_id="$1"
    local stage="$2"
    local pct="$3"
    local message="$4"

    # shellcheck disable=SC2086  # message is intentional
    sqlite3 "$DB_PATH" \
        "UPDATE share_tasks
         SET progress_stage='$stage', progress_pct=$pct, progress_message='$message'
         WHERE id='$task_id'" \
        >/dev/null 2>&1 || true
}

log() { echo "[$(date -u +%T)] $*" ; }

# v1.0.14: cooperative cancel/pause.  Returns 0 if worker should abort
# this task (status is cancelled or paused).  Call at stage boundaries.
task_should_abort() {
    local task_id="$1"
    local st
    st=$(python3 - "$task_id" "$DB_PATH" <<'PYEOF'
import sqlite3, sys
task_id, db_path = sys.argv[1], sys.argv[2]
try:
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status FROM share_tasks WHERE id=?", (task_id,)).fetchone()
    print(row[0] if row else "")
except Exception:
    print("")
PYEOF
)
    if [[ "$st" == "cancelled" || "$st" == "paused" ]]; then
        log "ABORT $task_id — status is '$st' (user control)"
        return 0
    fi
    return 1
}

task_status_update() {
    local task_id="$1"
    local status="$2"
    local output_path="${3:-}"
    local error_msg="${4:-}"

    local result
    result=$(python3 - "$task_id" "$status" "$output_path" "$error_msg" "$DB_PATH" <<'PYEOF'
import json, sys, sqlite3

task_id   = sys.argv[1]
status    = sys.argv[2]
output_path = sys.argv[3]
error_msg = sys.argv[4]
db_path   = sys.argv[5]

conn = sqlite3.connect(db_path)
cur  = conn.cursor()

now = __import__("datetime").datetime.now().__str__()[:19]  # 'YYYY-MM-DD HH:MM:SS'

if status == "done":
    # v1.0.14: never overwrite cancelled/paused (user control wins)
    cur.execute(
        "UPDATE share_tasks SET status=?, output_path=?, finished_at=? "
        "WHERE id=? AND status IN ('pending','running')",
        (status, output_path, now, task_id)
    )
elif status == "failed":
    cur.execute(
        "UPDATE share_tasks SET status=?, error=?, finished_at=? "
        "WHERE id=? AND status IN ('pending','running')",
        (status, error_msg, now, task_id)
    )
    # v1.0.9.12: notify on share task failure
    try:
        from moni.utils.notify import send_notification
        send_notification(
            subject=f"moni share task failed: {task_id[:12]}...",
            body=f"Format: {output_path or '?'}\nError: {error_msg or '?'}\nTask: {task_id}",
            level="error",
        )
    except Exception:
        pass  # notification failure never blocks status update
elif status == "running":
    cur.execute(
        "UPDATE share_tasks SET status=?, started_at=? "
        "WHERE id=? AND status='pending'",
        (status, now, task_id)
    )
    print("claimed" if cur.rowcount == 1 else "lost")

conn.commit()
PYEOF
)
    if [[ "$status" == "running" && "$result" != "claimed" ]]; then
        log "SKIP $task_id — another worker already claimed it"
        return 1
    fi
    return 0
}

# Build the skill flag from format name.
# Maps format strings to Hermes skill names.
# v1.0.5.18: pruned 6 formats.  Only info_graphic and report_png use
# a real skill; the rest rely on the content prompt alone.
skill_for_format() {
    local format="$1"
    case "$format" in
        info_graphic|infographic|info_graphic_png) echo "baoyu-infographic" ;;
        report_png)                echo "baoyu-infographic" ;;  # default; user can override via generation_approach
        *)                         echo "" ;;
    esac
}

# v1.0.5.18: map report_png's generation_approach → skill name.
# Per PRD §4: baoyu-infographic (default) / ljg-card / kami / direct
# (no skill — agent writes a freeform image_generate prompt).
skill_for_report_png() {
    local approach="$1"
    case "$approach" in
        baoyu-infographic) echo "baoyu-infographic" ;;
        ljg-card)          echo "ljg-card" ;;
        kami)              echo "kami" ;;
        direct)            echo "" ;;
        *)                 echo "baoyu-infographic" ;;
    esac
}

# v1.0.5.18: map a format name to its format-specific prompt file.
# Plain text doesn't need a per-format prompt (worker writes directly
# without an LLM call, per PRD §9 decision).  The other 5 formats
# each have a file under moni/share/prompts/.
prompt_file_for_format() {
    local format="$1"
    local brief="${2:-false}"
    case "$format" in
        markdown)
            # v1.0.10: brief mode uses rollup + BV-sorted layout prompt
            if [[ "$brief" == "true" ]]; then
                echo "format-brief-markdown.md"
            else
                echo "format-markdown.md"
            fi
            ;;
        html_landing)
            if [[ "$brief" == "true" ]]; then
                echo "format-brief-html.md"
            else
                echo "format-html.md"
            fi
            ;;
        rich_text)        echo "format-rich-text.md" ;;
        report_png)       echo "format-report-png.md" ;;
        plain_text)       echo "" ;;  # no LLM pass
        info_graphic_png) echo "format-info-graphic-png.md" ;;
        pdf)
            if [[ "$brief" == "true" ]]; then
                echo "format-brief-pdf.md"
            else
                echo "format-pdf.md"
            fi
            ;;
        *)                echo "" ;;
    esac
}

# Extract a JSON string field from a JSON object using Python (available on all
# macOS/Linux — no external dependencies needed).
json_get() {
    local path="$1"
    local field="$2"
    python3 -c "
import json, sys
with open('$path') as f:
    data = json.load(f)
print(data.get('$field', '') if isinstance(data, dict) else '')
" 2>/dev/null || echo ""
}

# Read a top-level string field from manifest JSON — handles both Python dict
# and Node object representations.
json_field() {
    local json_path="$1"
    local field="$2"
    python3 - <<'PYEOF' 2>/dev/null
import json, sys, os

path = os.environ.get('_JSON_PATH', '')
field = os.environ.get('_FIELD', '')

try:
    with open(path) as f:
        data = json.load(f)
    val = data.get(field, '') if isinstance(data, dict) else ''
    if val:
        sys.stdout.write(str(val))
except Exception:
    pass
PYEOF
}

# --- Process one manifest file --------------------------------------------

process_manifest() {
    local manifest_path="$1"
    local basename
    basename=$(basename "$manifest_path" .json)

    # Skip lock file and hidden files
    [[ "$basename" == .* ]] && return 0

    local task_id format article_ids_str output_path skill_name error_msg artifacts_dir
    artifacts_dir="${ARTIFACTS_DIR:-$REPO_ROOT/data/artifacts}"
    mkdir -p "$artifacts_dir"
    # Support both 'id' and 'share_id' as the primary key field
    task_id=$(python3 -c "import json; d=json.load(open('$manifest_path')); print(d.get('id','') or d.get('share_id',''))" 2>/dev/null || echo "")
    format=$(python3 -c "import json; d=json.load(open('$manifest_path')); print(d.get('format',''))" 2>/dev/null || echo "")
    # v1.0.5.14: article_ids is nested under 'payload' (the
    # write_manifest() schema).  Older queue files may still
    # have it at top level — accept both.  The v1.0.5.x path
    # is d['payload']['article_ids'].
    article_ids_str=$(python3 -c "
import json
d=json.load(open('$manifest_path'))
top = d.get('article_ids','')
nested = d.get('payload', {}).get('article_ids', '') if isinstance(d.get('payload'), dict) else ''
out = top or nested
print(out if out is not None else '')
" 2>/dev/null || echo "")
    output_path=$(python3 -c "import json; d=json.load(open('$manifest_path')); print(d.get('output_path',''))" 2>/dev/null || echo "")

    if [[ -z "$task_id" || -z "$format" ]]; then
        log "SKIP $basename — missing id or format field"
        return 0
    fi

    # Check status in DB — only process pending (Python to avoid shell-injection)
    local current_status
    current_status=$(python3 - "$task_id" "$DB_PATH" <<'PYEOF'
import sqlite3, sys
task_id = sys.argv[1]
db_path = sys.argv[2]
try:
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "SELECT status FROM share_tasks WHERE id=?",
        (task_id,)
    )
    row = cur.fetchone()
    print(row[0] if row else "")
except Exception:
    print("")
PYEOF
)

    if [[ "$current_status" != "pending" ]]; then
        log "SKIP $task_id — status is '$current_status', not pending"
        return 0
    fi

    # v1.0.5.17: stage 1 → 2 transition.  The task_status_update
    # below flips status pending → running; the progress_stage
    # separately moves queued → fetching because "running" is a
    # status state (queue position) while "fetching" is a pipeline
    # stage.  Two columns, two concepts.
    if ! task_status_update "$task_id" "running"; then
        return 0
    fi
    # Claim may lose race with user cancel between SELECT and UPDATE
    if task_should_abort "$task_id"; then
        return 0
    fi
    # ── P2-1: case_* formats short-circuit to pure Python renderer ────────
    if [[ "$format" == case_* ]]; then
        output_path="$artifacts_dir/${task_id}-case_brief_html.html"
        update_progress "$task_id" "preparing" 20 "正在汇总案例上下文"
        local case_ids_json case_title case_group_by case_flags=()
        case_ids_json=$(python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
p = d.get('payload', {}) if isinstance(d.get('payload'), dict) else {}
print(json.dumps(p.get('case_ids') or [], ensure_ascii=False))
" "$manifest_path" 2>/dev/null || echo "[]")
        case_title=$(python3 -c "
import json, sys
from datetime import date
d = json.load(open(sys.argv[1]))
p = d.get('payload', {}) if isinstance(d.get('payload'), dict) else {}
print(p.get('title') or ('同业案例简报 — ' + date.today().isoformat()))
" "$manifest_path" 2>/dev/null || echo "同业案例简报")
        case_group_by=$(python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
p = d.get('payload', {}) if isinstance(d.get('payload'), dict) else {}
print(p.get('group_by') or '')
" "$manifest_path" 2>/dev/null || echo "")
        local inc_ins inc_evi
        inc_ins=$(python3 -c "import json,sys; p=json.load(open(sys.argv[1])).get('payload',{}); print('1' if p.get('include_insight', True) else '0')" "$manifest_path" 2>/dev/null || echo "1")
        inc_evi=$(python3 -c "import json,sys; p=json.load(open(sys.argv[1])).get('payload',{}); print('1' if p.get('include_evidence', True) else '0')" "$manifest_path" 2>/dev/null || echo "1")
        [[ -n "$case_group_by" ]] && case_flags+=(--group-by "$case_group_by")
        [[ "$inc_ins" == "0" ]] && case_flags+=(--no-insight)
        [[ "$inc_evi" == "0" ]] && case_flags+=(--no-evidence)

        local case_err case_out
        case_err=$(mktemp "${TMPDIR:-/tmp}/moni_case_brief_XXXXXX.err")
        case_out=$(mktemp "${TMPDIR:-/tmp}/moni_case_brief_XXXXXX.out")
        if _run_worker_child "$case_out" "$case_err" \
            "$PYTHON_BIN" -m moni.share.case_brief \
            --db "$DB_PATH" \
            --case-ids "$case_ids_json" \
            --output "$output_path" \
            --title "$case_title" \
            ${case_flags[@]+"${case_flags[@]}"} \
            ; then
            cat "$case_err" >>"$LOG_FILE" 2>/dev/null || true
            rm -f "$case_err" "$case_out"
            log "CASE_BRIEF $task_id -> $output_path"
            update_progress "$task_id" "writing_artifact" 95 "HTML 已写入 $output_path"
            task_status_update "$task_id" "done" "$output_path"
            sqlite3 "$DB_PATH" \
                "UPDATE share_tasks SET progress_pct=100 WHERE id='$task_id'" \
                >/dev/null 2>&1 || true
            log "DONE $task_id -> $output_path"
            return 0
        else
            cat "$case_err" >>"$LOG_FILE" 2>/dev/null || true
            local case_tail
            case_tail=$(tail -c 400 "$case_err" 2>/dev/null | tr '\n' ' ' | head -c 300 || true)
            rm -f "$case_err" "$case_out"
            error_msg="case_brief render failed"
            [[ -n "${case_tail// /}" ]] && error_msg="case_brief render failed: $case_tail"
            log "FAIL $task_id — $error_msg"
            task_status_update "$task_id" "failed" "" "$error_msg"
            return 0
        fi
    fi

    update_progress "$task_id" "fetching" 20 \
        "正在从 articles 表读 raw_content ..."

    log "PROCESS $task_id format=$format output=$output_path"

    # Fetch article content from DB (including existing ai_summary)
    local article_content=""
    if [[ -n "$article_ids_str" ]]; then
        # Python: parse JSON array and query with parameterized IN clause
        # v1.0.9.3: also pass manifest_path so the inner Python can read
        # bb_summary_source and branch on it (was prompt soft-constraint,
        # worker template always rendered both ai_summary + bb_summary).
        article_content=$(python3 - "$article_ids_str" "$DB_PATH" "$manifest_path" <<'PYEOF'
import json, ast, sqlite3, sys
ids_str = sys.argv[1]
db_path = sys.argv[2]
manifest_path = sys.argv[3] if len(sys.argv) > 3 else ""
try:
    # v1.0.9.3: read bb_summary_source from manifest payload.  Default
    # 'moni_ai' preserves pre-v1.0.9.3 behavior (always show AI summary).
    bb_summary_source = "moni_ai"
    if manifest_path:
        try:
            with open(manifest_path) as _mf:
                _m = json.loads(_mf.read())
            _p = _m.get("payload", {}) if isinstance(_m.get("payload"), dict) else {}
            _v = _p.get("bb_summary_source")
            if _v in ("moni_ai", "bestblogs", "both", "none"):
                bb_summary_source = _v
        except Exception:
            pass
    ids_str = ids_str.strip()
    if ids_str.startswith("["):
        try:
            ids = json.loads(ids_str)
        except Exception:
            ids = ast.literal_eval(ids_str)
    else:
        ids = ast.literal_eval(ids_str)
    conn = sqlite3.connect(db_path)
    placeholders = ",".join("?" * len(ids))
    try:
        cur = conn.execute(
            "SELECT id, title, raw_content, ai_summary, source, bb_score, bb_summary, bb_one_sentence, bb_main_points, bb_key_quotes, bb_tags FROM articles WHERE id IN ({})".format(placeholders),
            ids
        )
    except sqlite3.OperationalError:
        cur = conn.execute(
            "SELECT id, title, raw_content, ai_summary, source, NULL, NULL, NULL, NULL, NULL, NULL FROM articles WHERE id IN ({})".format(placeholders),
            ids
        )
    parts = []
    skipped = []
    for row in cur.fetchall():
        aid, title, content, ai_summary, source, bb_score, bb_summary, bb_one_sentence, bb_main_points, bb_key_quotes, bb_tags = row
        if content and content.strip():
            block = f"### 文章: {title}\nID: {aid}\n\n{content}"
            # v1.0.9.3: branch on bb_summary_source.  Was a prompt
            # soft-constraint, worker always rendered both.  Now:
            #   moni_ai   → AI summary only
            #   bestblogs → BB summary only
            #   both      → AI + BB, AI first
            #   none      → neither
            show_ai = bb_summary_source in ("moni_ai", "both") and ai_summary and ai_summary.strip()
            show_bb = bb_summary_source in ("bestblogs", "both") and bb_summary and bb_summary.strip()
            if show_ai:
                block += f"\n\n**AI 摘要**: {ai_summary}"
            if show_bb:
                block += f"\n\n**BestBlogs 摘要**: {bb_summary}"
            # BestBlogs 增强数据（如果已拉取）
            if bb_score is not None:
                block += f"\n\n**BestBlogs 评分**: {bb_score}/100"
            if bb_one_sentence and bb_one_sentence.strip():
                block += f"\n\n**一句话洞察**: {bb_one_sentence}"
            if bb_main_points:
                try:
                    import json as _json
                    points = _json.loads(bb_main_points)
                    if points:
                        lines = [f"- {p.get('point','')}" for p in points if p.get('point')]
                        block += f"\n\n**要点**:\n" + "\n".join(lines)
                except Exception:
                    pass
            if bb_key_quotes:
                try:
                    import json as _json
                    quotes = _json.loads(bb_key_quotes)
                    if quotes:
                        lines = [f"> {q}" for q in quotes]
                        block += f"\n\n**金句**:\n" + "\n".join(lines)
                except Exception:
                    pass
            if bb_tags:
                try:
                    import json as _json
                    tags = _json.loads(bb_tags)
                    if tags:
                        block += f"\n\n**BestBlogs 标签**: {', '.join(tags)}"
                except Exception:
                    pass
            parts.append(block)
        else:
            skipped.append(aid)
    if skipped:
        import sys as _sys
        print(f"[warn] {len(skipped)} article(s) with empty content skipped: {skipped}", file=_sys.stderr)
    print("\n\n---\n\n".join(parts))
except Exception as e:
    import traceback
    print(f"[article_content] error: {e}\n{traceback.format_exc()}", file=sys.stderr)
    print("")
PYEOF
)
    fi

    if [[ -z "$article_content" ]]; then
        error_msg="no article content found for article_ids=$article_ids_str"
        log "FAIL $task_id — $error_msg"
        task_status_update "$task_id" "failed" "" "$error_msg"
        return 0
    fi

    # v1.0.5.25: BestBlogs API 按需拉取增强元数据.
    # For bestblogs articles without bb_fetched_at, call the API to
    # fetch score/summary/mainPoints/keyQuotes/tags and write to DB.
    # The article_content block above will pick up the data on next read.
    local bb_enhance_val
    bb_enhance_val=$(python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
p = d.get('payload', {}) if isinstance(d.get('payload'), dict) else {}
v = p.get('bb_enhance', False)
print('true' if v else 'false')
" "$manifest_path" 2>/dev/null || echo "false")

    if [[ "$bb_enhance_val" == "true" && -n "$article_ids_str" ]]; then
        update_progress "$task_id" "fetching_bb_meta" 30 \
            "正在拉取 BestBlogs 增强数据 ..."
        log "BB_FETCH $task_id — checking bestblogs articles for missing bb_fetched_at"

        python3 - "$article_ids_str" "$DB_PATH" <<'BBEOF'
import json, sys, os
sys.path.insert(0, os.environ.get("REPO_ROOT", "."))
from moni.fetch.bestblogs_api import fetch_and_store

ids_str = sys.argv[1]
try:
    if ids_str.startswith("["):
        ids = json.loads(ids_str)
    else:
        ids = eval(ids_str)
    fetched = 0
    for aid in ids:
        result = fetch_and_store(aid)
        if result:
            fetched += 1
    if fetched > 0:
        print(f"[bb_fetch] fetched meta for {fetched} article(s)", file=sys.stderr)
except Exception as e:
    import traceback
    print(f"[bb_fetch] error: {e}\n{traceback.format_exc()}", file=sys.stderr)
BBEOF

        # Re-read article content to pick up newly stored BB data
        # v1.0.9.3: also pass manifest_path for bb_summary_source branching.
        if [[ -n "$article_ids_str" ]]; then
            article_content=$(python3 - "$article_ids_str" "$DB_PATH" "$manifest_path" <<'PYEOF'
import json, ast, sqlite3, sys
ids_str = sys.argv[1]
db_path = sys.argv[2]
manifest_path = sys.argv[3] if len(sys.argv) > 3 else ""
try:
    # v1.0.9.3: read bb_summary_source from manifest payload
    bb_summary_source = "moni_ai"
    if manifest_path:
        try:
            with open(manifest_path) as _mf:
                _m = json.loads(_mf.read())
            _p = _m.get("payload", {}) if isinstance(_m.get("payload"), dict) else {}
            _v = _p.get("bb_summary_source")
            if _v in ("moni_ai", "bestblogs", "both", "none"):
                bb_summary_source = _v
        except Exception:
            pass
    ids_str = ids_str.strip()
    if ids_str.startswith("["):
        try:
            ids = json.loads(ids_str)
        except Exception:
            ids = ast.literal_eval(ids_str)
    else:
        ids = ast.literal_eval(ids_str)
    conn = sqlite3.connect(db_path)
    placeholders = ",".join("?" * len(ids))
    try:
        cur = conn.execute(
            "SELECT id, title, raw_content, ai_summary, source, bb_score, bb_summary, bb_one_sentence, bb_main_points, bb_key_quotes, bb_tags FROM articles WHERE id IN ({})".format(placeholders),
            ids
        )
    except sqlite3.OperationalError:
        cur = conn.execute(
            "SELECT id, title, raw_content, ai_summary, source, NULL, NULL, NULL, NULL, NULL, NULL FROM articles WHERE id IN ({})".format(placeholders),
            ids
        )
    parts = []
    skipped = []
    for row in cur.fetchall():
        aid, title, content, ai_summary, source, bb_score, bb_summary, bb_one_sentence, bb_main_points, bb_key_quotes, bb_tags = row
        if content and content.strip():
            block = f"### 文章: {title}\nID: {aid}\n\n{content}"
            # v1.0.9.3: branch on bb_summary_source
            show_ai = bb_summary_source in ("moni_ai", "both") and ai_summary and ai_summary.strip()
            show_bb = bb_summary_source in ("bestblogs", "both") and bb_summary and bb_summary.strip()
            if show_ai:
                block += f"\n\n**AI 摘要**: {ai_summary}"
            if show_bb:
                block += f"\n\n**BestBlogs 摘要**: {bb_summary}"
            if bb_score is not None:
                block += f"\n\n**BestBlogs 评分**: {bb_score}/100"
            if bb_one_sentence and bb_one_sentence.strip():
                block += f"\n\n**一句话洞察**: {bb_one_sentence}"
            if bb_main_points:
                try:
                    import json as _json
                    points = _json.loads(bb_main_points)
                    if points:
                        lines = [f"- {p.get('point','')}" for p in points if p.get('point')]
                        block += f"\n\n**要点**:\n" + "\n".join(lines)
                except Exception:
                    pass
            if bb_key_quotes:
                try:
                    import json as _json
                    quotes = _json.loads(bb_key_quotes)
                    if quotes:
                        lines = [f"> {q}" for q in quotes]
                        block += f"\n\n**金句**:\n" + "\n".join(lines)
                except Exception:
                    pass
            if bb_tags:
                try:
                    import json as _json
                    tags = _json.loads(bb_tags)
                    if tags:
                        block += f"\n\n**BestBlogs 标签**: {', '.join(tags)}"
                except Exception:
                    pass
            parts.append(block)
        else:
            skipped.append(aid)
    if skipped:
        import sys as _sys
        print(f"[warn] {len(skipped)} article(s) with empty content skipped: {skipped}", file=_sys.stderr)
    print("\n\n---\n\n".join(parts))
except Exception as e:
    import traceback
    print(f"[article_content] error: {e}\n{traceback.format_exc()}", file=sys.stderr)
    print("")
PYEOF
)
        fi
        log "BB_FETCH $task_id — done, article_content refreshed"
    fi

    # v1.0.5.19: AI summary generation + DB writeback.
    # Read include_abstract from manifest BEFORE building the main prompt.
    # If true, generate summaries for articles that don't have them yet,
    # write back to DB, and append to article_content for the main prompt.
    local include_abstract_val
    include_abstract_val=$(python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
p = d.get('payload', {}) if isinstance(d.get('payload'), dict) else {}
v = p.get('include_abstract', True)
print('true' if v else 'false')
" "$manifest_path" 2>/dev/null || echo "true")

    if [[ "$include_abstract_val" == "true" && -n "$article_ids_str" ]]; then
        update_progress "$task_id" "generating_summaries" 35 \
            "正在生成 AI 摘要 (首次生成会调 LLM, 已有则跳过) ..."
        log "SUMMARY_GEN $task_id — checking articles for missing ai_summary"

        local gen_result_file gen_result_err gen_result
        gen_result_file=$(mktemp "${TMPDIR:-/tmp}/moni_summary_XXXXXX.json")
        gen_result_err=$(mktemp "${TMPDIR:-/tmp}/moni_summary_XXXXXX.err")
        if _run_worker_child "$gen_result_file" "$gen_result_err" \
            "$PYTHON_BIN" "$REPO_ROOT/moni/share/summary_gen.py" \
            "$DB_PATH" "$article_ids_str" "$manifest_path"; then
            gen_result=$(cat "$gen_result_file" 2>/dev/null) || gen_result="{}"
        else
            gen_result="{}"
        fi
        rm -f "$gen_result_file" "$gen_result_err"

        # Append newly generated summaries to article_content
        local new_summaries
        new_summaries=$(python3 -c "
import json, sys
d = json.loads(sys.argv[1])
if d:
    parts = []
    for aid, summary in d.items():
        parts.append(f'### 文章 ID {aid} 的 AI 摘要\n{summary}')
    print('\n\n'.join(parts))
else:
    print('')
" "$gen_result" 2>/dev/null || echo "")

        if [[ -n "$new_summaries" ]]; then
            article_content="${article_content}

---

## 新生成的 AI 摘要 (请在报告中使用)

${new_summaries}"
            log "SUMMARY_GEN $task_id — appended new summaries to article_content"
        fi
    fi

    # v1.0.10: enrichment (fact_check / business_value / brief re-read).
    # Runs when brief mode or either enrichment flag is on. Rebuilds
    # article_content from DB so format prompt sees writebacks.
    local brief_val include_fc_val include_bv_val include_ctx_val
    brief_val=$(python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
p = d.get('payload', {}) if isinstance(d.get('payload'), dict) else {}
print('true' if p.get('brief') else 'false')
" "$manifest_path" 2>/dev/null || echo "false")
    include_fc_val=$(python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
p = d.get('payload', {}) if isinstance(d.get('payload'), dict) else {}
print('true' if p.get('include_fact_check') else 'false')
" "$manifest_path" 2>/dev/null || echo "false")
    include_bv_val=$(python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
p = d.get('payload', {}) if isinstance(d.get('payload'), dict) else {}
print('true' if p.get('include_business_value') else 'false')
" "$manifest_path" 2>/dev/null || echo "false")
    include_ctx_val=$(python3 -c "
import json, sys
d = json.load(open(sys.argv[1]))
p = d.get('payload', {}) if isinstance(d.get('payload'), dict) else {}
print('true' if p.get('include_user_context') else 'false')
" "$manifest_path" 2>/dev/null || echo "false")

    if task_should_abort "$task_id"; then
        return 0
    fi

    if [[ -n "$article_ids_str" ]] && \
       { [[ "$brief_val" == "true" ]] || [[ "$include_fc_val" == "true" ]] || [[ "$include_bv_val" == "true" ]]; }; then
        update_progress "$task_id" "enrich_business_value" 50 \
            "正在富化（事实核查/业务价值）..."
        log "ENRICH $task_id — running moni.share.enrichment (brief=$brief_val fc=$include_fc_val bv=$include_bv_val)"

        # Prefer python -m so editable install resolves moni package.
        # Command substitution around a background+wait child would delay
        # the trap, so capture stdout in a file and wait in _run_worker_child.
        local enrich_out enrich_err enrich_out_file
        enrich_err="/tmp/moni_enrich_${task_id}.err"
        enrich_out_file=$(mktemp "${TMPDIR:-/tmp}/moni_enrich_XXXXXX.out")
        _run_worker_child "$enrich_out_file" "$enrich_err" \
            "$PYTHON_BIN" -m moni.share.enrichment \
            --db "$DB_PATH" \
            --task-id "$task_id" \
            --manifest "$manifest_path" \
            --article-ids "$article_ids_str" \
            --print-content || true
        enrich_out=$(cat "$enrich_out_file" 2>/dev/null || true)
        rm -f "$enrich_out_file"

        if [[ -n "$enrich_out" ]]; then
            article_content="$enrich_out"
            log "ENRICH $task_id — article_content rebuilt ($(printf '%d' "${#article_content}") bytes)"
        else
            log "ENRICH $task_id — rebuild empty; keeping prior article_content (see /tmp/moni_enrich_${task_id}.err)"
        fi
    fi

    # v1.0.10: brief + html_landing → deterministic HTML (full body, collapsible
    # 原文, GSAP). Skip hermes — layout/UX must be stable for Windows offline.
    brief_val="${brief_val:-false}"
    if task_should_abort "$task_id"; then
        return 0
    fi
    if [[ "$brief_val" == "true" && "$format" == "html_landing" ]]; then
        update_progress "$task_id" "writing_artifact" 90 \
            "正在生成离线 HTML 简报（含全文折叠）..."
        output_path="$artifacts_dir/${task_id}-html_landing.html"
        local brief_title
        brief_title=$(python3 -c "
import json, sys
from datetime import date
d=json.load(open(sys.argv[1]))
p=d.get('payload',{}) if isinstance(d.get('payload'),dict) else {}
print(p.get('title') or ('同业简报 — ' + date.today().isoformat()))
" "$manifest_path" 2>/dev/null || echo "同业简报")
        # stderr → LOG_FILE (now defined above under set -u).  Also keep a
        # temp copy so failure messages surface in share_tasks.error.
        local brief_err brief_out
        brief_err=$(mktemp "${TMPDIR:-/tmp}/moni_brief_XXXXXX.err")
        brief_out=$(mktemp "${TMPDIR:-/tmp}/moni_brief_XXXXXX.out")
        local brief_ctx_args=()
        [[ "$include_ctx_val" == "true" ]] && brief_ctx_args=(--include-user-context)
        if _run_worker_child "$brief_out" "$brief_err" \
            "$PYTHON_BIN" -m moni.share.brief_html \
            --db "$DB_PATH" \
            --article-ids "$article_ids_str" \
            --output "$output_path" \
            --title "$brief_title" \
            ${brief_ctx_args[@]+"${brief_ctx_args[@]}"} \
            ; then
            cat "$brief_err" >>"$LOG_FILE" 2>/dev/null || true
            rm -f "$brief_err" "$brief_out"
            log "BRIEF_HTML $task_id -> $output_path"
            update_progress "$task_id" "writing_artifact" 95 "HTML 已写入 $output_path"
            task_status_update "$task_id" "done" "$output_path"
            sqlite3 "$DB_PATH" \
                "UPDATE share_tasks SET progress_pct=100 WHERE id='$task_id'" \
                >/dev/null 2>&1 || true
            log "DONE $task_id -> $output_path"
            return 0
        else
            cat "$brief_err" >>"$LOG_FILE" 2>/dev/null || true
            local brief_tail
            brief_tail=$(tail -c 400 "$brief_err" 2>/dev/null | tr '\n' ' ' | head -c 300 || true)
            rm -f "$brief_err" "$brief_out"
            error_msg="brief_html render failed"
            if [[ -n "${brief_tail// /}" ]]; then
                error_msg="brief_html render failed: $brief_tail"
            fi
            log "FAIL $task_id — $error_msg"
            task_status_update "$task_id" "failed" "" "$error_msg"
            return 0
        fi
    fi

    # Build skill flag
    skill_name=$(skill_for_format "$format")

    # Escape article content for shell: replace " with \" and wrap in double
    # quotes.  Use a here-doc with a Python interpolator to avoid all shell
    # escaping issues.
    local escaped_content
    escaped_content=$(python3 -c "
import json, sys
content = '''$article_content'''
print(json.dumps(content, ensure_ascii=False))
" 2>/dev/null || echo "\"\"")

    # v1.0.5.17: stage 2 → 3 transition.  Article content is now in
    # hand; we mark building_prompt so the UI can show "拼 prompt
    # 中" while the Python interpolator runs (typically <1s but
    # visible for big content).
    if task_should_abort "$task_id"; then
        return 0
    fi
    update_progress "$task_id" "building_prompt" 40 \
        "正在拼 prompt (skill=$skill_name, content=$(printf '%d' "${#escaped_content}") 字节) ..."

    # v1.0.5.18: read user options from the manifest payload.
    # These three are universal across all 6 formats (per PRD §2).
    # include_abstract defaults to true (most users want the AI
    # summary); include_fact_check defaults to false (slower; an
    # explicit opt-in).  provider/model let the user override the
    # default hermes profile's choice when one provider's quota
    # is exhausted.
    #
    # Implementation: we do a single Python pass to read all four
    # fields and emit `key=value` lines that we then `eval` into the
    # shell.  We use a simple Python-emit format (no f-string shell
    # interpolation, no nested quotes) to avoid bash 3.2 quoting
    # edge cases.  Each value is wrapped in single quotes so empty
    # strings survive intact.
    eval "$(python3 - "$manifest_path" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1]))
p = d.get("payload", {}) if isinstance(d.get("payload"), dict) else {}
ia = p.get("include_abstract", True)
fc = p.get("include_fact_check", False)
prov = p.get("provider") or ""
mod = p.get("model") or ""
title = p.get("title") or ""
def sq(s):
    # single-quote-wrap a string for safe shell eval
    return "'" + str(s).replace("'", "'\"'\"'") + "'"
print(f"include_abstract={sq('true' if ia else 'false')}")
print(f"include_fact_check={sq('true' if fc else 'false')}")
print(f"provider_override={sq(prov)}")
print(f"model_override={sq(mod)}")
print(f"info_graphic_title={sq(title)}")
bb = p.get("bb_enhance", False)
print(f"bb_enhance={sq('true' if bb else 'false')}")
PYEOF
)"

    # v1.0.5.18: assemble the prompt by joining the content-structure
    # prompt (always first) + the per-format prompt (if any) + the
    # user-options block + the article data.  The skill clause is
    # appended to the format-specific section (not the content section)
    # so that the LLM knows "this skill is available, use it" after
    # reading the format requirements.
    local content_prompt=""
    local content_prompt_file="$PROMPT_DIR/content-structure.md"
    [[ -f "$content_prompt_file" ]] && content_prompt=$(<"$content_prompt_file")

    local format_prompt=""
    local pf
    # brief_val may already be set above (v1.0.10); default false for old path
    brief_val="${brief_val:-false}"
    pf=$(prompt_file_for_format "$format" "$brief_val")
    if [[ -n "$pf" ]]; then
        local format_prompt_file="$PROMPT_DIR/$pf"
        [[ -f "$format_prompt_file" ]] && format_prompt=$(<"$format_prompt_file")
    fi
    # writing style: brief mode + one-image reports.  Non-brief report_png
    # used to skip this file, so "no AI tone / no data enumeration" never
    # reached the model.
    local prepend=""
    if [[ "$brief_val" == "true" || "$format" == "report_png" || "$format" == "info_graphic_png" ]]; then
        local style_file="$PROMPT_DIR/writing-style-compact.md"
        [[ -f "$style_file" ]] && prepend+="$(<"$style_file")

"
    fi
    # brief mode also gets the business-value persona
    if [[ "$brief_val" == "true" ]]; then
        local bv_prompt_file="$PROMPT_DIR/business-value.md"
        [[ -f "$bv_prompt_file" ]] && prepend+="$(<"$bv_prompt_file")

"
    fi
    [[ -n "$prepend" ]] && content_prompt="${prepend}${content_prompt}"

    # Skill clause — for non-png formats, we use the format's
    # own skill_for_format() mapping.  For report_png and info_graphic_png,
    # the skill is derived from the user's generation_approach choice
    # (defaulting to baoyu-infographic if unspecified).
    local skill_clause=""
    local effective_skill="$skill_name"
    if [[ "$format" == "report_png" || "$format" == "info_graphic_png" ]]; then
        local approach
        approach=$(python3 -c "
import json
d=json.load(open('$manifest_path'))
p=d.get('payload',{}) if isinstance(d.get('payload'),dict) else {}
print(p.get('generation_approach',''))
" 2>/dev/null || echo "")
        effective_skill=$(skill_for_report_png "$approach")
    fi
    if [[ -n "$effective_skill" ]]; then
        skill_clause="Use the skill: $effective_skill."
    fi

    # User-options directive.  Abstract default true (most users want
    # an AI summary); fact-check default false (slower, opt-in).
    local opts=""
    [[ "$include_abstract" == "true" ]] && opts+="包含 AI 摘要: 是。对没有摘要的文章请生成。\n"
    [[ "$include_fact_check" == "true" ]] && opts+="包含事实核查: 是。请核查事实性声明。\n"

    # Assemble the final prompt.  Each section is separated by
    # a horizontal rule so the LLM can see where each phase begins.
    local prompt=""
    [[ -n "$content_prompt" ]] && prompt+="${content_prompt}\n\n"
    prompt+="---\n\n"
    [[ -n "$format_prompt" ]] && prompt+="${format_prompt}\n\n"
    [[ -n "$skill_clause" ]] && prompt+="${skill_clause}\n\n"
    prompt+="---\n\n"
    prompt+="## 用户选项\n"
    [[ -n "$opts" ]] && prompt+="${opts}\n"
    # v1.0.5.20: info_graphic_png / pdf 标题
    if [[ "$format" == "info_graphic_png" || "$format" == "pdf" ]] && [[ -n "$info_graphic_title" ]]; then
        prompt+="信息图标题: ${info_graphic_title}\n"
    fi
    prompt+="---\n\n"
    prompt+="## 文章数据\n\n${escaped_content}"

    # MONI_PROMPT_DUMP=<path>: write the assembled prompt for audit/tests.
    if [[ -n "${MONI_PROMPT_DUMP:-}" ]]; then
        printf '%s' "$prompt" > "$MONI_PROMPT_DUMP" 2>/dev/null \
            || log "WARN prompt dump failed: $MONI_PROMPT_DUMP"
    fi

    # Plain-text is a no-LLM pass per PRD §9 decision: worker
    # writes the article content directly to the output file,
    # skipping the LLM call.  We mark the artifact as
    # 'plain_text' and return early.
    if [[ "$format" == "plain_text" ]]; then
        update_progress "$task_id" "writing_artifact" 95 \
            "正在写 plain_text artifact 到 $output_path ..."
        local output_dir
        output_dir=$(dirname "$output_path")
        mkdir -p "$output_dir"
        printf '%s\n' "$article_content" > "$output_path"
        _append_user_context_section "$output_path" "$include_ctx_val" "$article_ids_str"
        task_status_update "$task_id" "done" "$output_path"
        sqlite3 "$DB_PATH" \
            "UPDATE share_tasks SET progress_pct=100 WHERE id='$task_id'" \
            >/dev/null 2>&1 || true
        log "DONE $task_id (plain_text, no LLM) -> $output_path"
        return 0
    fi

    # v1.0.5.17: stage 3 → 4 transition.  The prompt is built; next
    # up is the 5-minute hermes chat subprocess.  This is the
    # longest stage (typically 30s-3min for an LLM call), so we
    # write a clear message so the UI can show "正在调 LLM, 估
    # X 秒" while it runs.
    if task_should_abort "$task_id"; then
        return 0
    fi
    local task_timeout
    task_timeout=$(task_timeout_for_format "$format")
    update_progress "$task_id" "invoking_llm" 60 \
        "正在调 hermes chat (timeout=${task_timeout}s) ..."

    # v1.0.31 (sprint-12): hermes invocation moved to moni/share/worker_llm.py
    # — a dedicated Python module.  It:
    #   A1. reads provider/model from DB (llm_settings.active_summary_model_id
    #       → llm_models → llm_providers) and passes explicit
    #       `--provider X -m Y`, so the user's default hermes provider no
    #       longer decides what moni uses.  Manifest payload overrides
    #       ($provider_override / $model_override) still win.
    #   A2. scans stdout for LLM failure keywords (API call failed / HTTP 4xx
    #       / HTTP 5xx / rate limit / insufficient credits) and returns 1 so
    #       the task goes to failed instead of "假 done".
    #   A3. stdout is written to a file ($hermes_out_file) so the worker can
    #       both inspect it and hand remote fal.media URLs to curl below.
    local hermes_exit=0
    local hermes_out_file="$artifacts_dir/${task_id}-hermes-output.txt"
    local hermes_usage_file="$artifacts_dir/${task_id}-hermes-usage.json"
    local hermes_output=""
    local llm_started_epoch
    llm_started_epoch=$(date +%s)
    local llm_args=("--db" "$DB_PATH" "--timeout" "$task_timeout")
    [[ -n "$effective_skill" ]] && llm_args+=("--skill" "$effective_skill")
    [[ -n "$provider_override" ]] && llm_args+=("--provider" "$provider_override")
    [[ -n "$model_override" ]] && llm_args+=("--model" "$model_override")
    llm_args+=("--output" "$hermes_out_file" "--usage-file" "$hermes_usage_file" "--" "$prompt")

    _run_worker_child "$artifacts_dir/${task_id}-hermes.stdout" \
        "$artifacts_dir/${task_id}-hermes.err" \
        "$PYTHON_BIN" "$REPO_ROOT/moni/share/worker_llm.py" "${llm_args[@]}" \
        || hermes_exit=$?
    # hermes 输出已经由 worker_llm.py 写到 hermes_out_file; 超时/失败时
    # 文件可能不存在, 所以 read 前先兜底。
    hermes_output=$(cat "$hermes_out_file" 2>/dev/null) || true

    if [[ $hermes_exit -eq 124 ]]; then
        if recover_artifact "$task_id" "$format" \
            "$llm_started_epoch" "$artifacts_dir" "timeout" \
            "$include_ctx_val" "$article_ids_str"; then
            return 0
        fi
        error_msg="timeout after ${task_timeout}s"
        log "FAIL $task_id — timeout"
        task_status_update "$task_id" "failed" "" "$error_msg"
        return 0
    elif [[ $hermes_exit -ne 0 ]]; then
        # Extract first line of error for the log
        error_msg=$(echo "$hermes_output" | head -3 | tr '\n' ' ' | cut -c1-500)
        log "FAIL $task_id — exit $hermes_exit: $error_msg"
        task_status_update "$task_id" "failed" "" "$error_msg"
        return 0
    fi

    # v1.0.5.25: For PNG/HTML formats, the LLM may generate actual files
    # (e.g. infographic.png, report.png) and return a text description
    # with the file path.  Detect the real file and copy it to the
    # artifact location instead of saving the text output.
    if [[ "$format" == "info_graphic_png" || "$format" == "report_png" ]]; then
        # v1.0.31 (sprint-12) A3: 远程 PNG (fal.media 等) 下载 + magic
        # bytes 校验, 不再把文本描述冒充成 PNG。 校验失败 → 报 FAIL,
        # 不是 done。 保留 v1.0.30 的本地文件兜底 (hermes 直接写本地
        # PNG 的场景)。
        local png_urls=""
        png_urls=$("$PYTHON_BIN" - "$hermes_out_file" <<'PYEOF'
import re, sys
urls = re.findall(r"https?://[^\s<>\"']+?\.png(?:\?[^\s<>\"']*)?", open(sys.argv[1], encoding="utf-8", errors="replace").read())
seen = []
for u in urls:
    u = u.rstrip(".,;:!?)]}>\"'")
    if u not in seen:
        seen.append(u)
print("\n".join(seen))
PYEOF
) || true
        local png_count=0
        local png_url
        local png_fail_reason=""
        for png_url in $png_urls; do
            local out_png="$artifacts_dir/${task_id}-${format}-${png_count}.png"
            if curl -fsSL --max-time 120 -o "$out_png" "$png_url" 2>/dev/null \
                && [[ "$(head -c 8 "$out_png" | od -An -tx1 | tr -d ' \n')" == "89504e470d0a1a0a" ]]; then
                log "PNG artifact downloaded $png_url -> $out_png"
                output_path="$out_png"
                png_count=$((png_count + 1))
            else
                rm -f "$out_png"
                png_fail_reason="remote PNG download/validation failed: $png_url"
                log "FAIL $task_id — remote PNG download/validation failed: $png_url"
            fi
        done
        if [[ $png_count -eq 0 ]]; then
            # v1.0.97: agent 回复里通常写明成品绝对路径，优先按它取。
            # 只靠 mtime 会挑到 cache/scratch 里的中间截图（手机预览）。
            local explicit_pngs=""
            explicit_pngs=$(PYTHONPATH="$REPO_ROOT" "$PYTHON_BIN" \
                "$REPO_ROOT/moni/share/timeout_recovery.py" \
                --from-output "$hermes_out_file" \
                --not-before "$llm_started_epoch" 2>/dev/null) || true
            local cand
            while IFS= read -r cand; do
                [[ -n "$cand" ]] || continue
                local out_png="$artifacts_dir/${task_id}-${format}-${png_count}.png"
                cp -- "$cand" "$out_png" 2>>"$LOG_FILE" || {
                    log "FAIL $task_id — copy artifact failed: $cand"
                    continue
                }
                log "PNG artifact copied from $cand -> $out_png"
                png_count=$((png_count + 1))
            done <<< "$explicit_pngs"
        fi
        if [[ $png_count -eq 0 ]]; then
            # v1.0.30 本地文件兜底: hermes 直接写本地 PNG 的场景。
            # v1.0.97: 复用 timeout_recovery 的搜索根（含 profile cache），
            # 并做真 PNG 校验；旧的 find + file 会在缺目录时被 set -e 杀掉。
            if recover_artifact "$task_id" "$format" \
                "$llm_started_epoch" "$artifacts_dir" "local PNG outside artifacts dir"; then
                return 0
            fi
        fi
        if [[ $png_count -gt 0 ]]; then
            # keep last copied as primary output_path (UI shows one link)
            output_path="$artifacts_dir/${task_id}-${format}-$((png_count - 1)).png"
        else
            # v1.0.31: 产物不是真 PNG 时标记 FAIL 而不是 done —
            # 防静默降级 (文本冒充 PNG / 远程 URL 下载失败)。
            if [[ -z "$png_fail_reason" ]]; then
                png_fail_reason="no real PNG artifact produced (no valid remote/local PNG)"
            fi
            log "FAIL $task_id — $png_fail_reason"
            task_status_update "$task_id" "failed" "" "$png_fail_reason"
            return 0
        fi
    elif [[ "$format" == "html_landing" ]]; then
        # Extract HTML file path from hermes output (look for /path/to/*.html)
        local real_html=""
        real_html=$(echo "$hermes_output" | grep -oE '/[^ ]+\.html' | head -1)
        if [[ -n "$real_html" && -f "$real_html" ]]; then
            output_path="$artifacts_dir/${task_id}-${format}.html"
            cp "$real_html" "$output_path"
            log "HTML artifact copied from $real_html -> $output_path"
            if [[ "$include_ctx_val" == "true" ]]; then
                "$PYTHON_BIN" -m moni.share.context_pack \
                    --db "$DB_PATH" --article-ids "$article_ids_str" \
                    --inject-html "$output_path" 2>>"$LOG_FILE" || true
            fi
        fi
    fi

    # Success — extract output path from agent response if not already set
    if [[ -z "$output_path" ]]; then
        # v1.0.5.21: pdf 格式后处理 — 将 Markdown 内容转换为 PDF
        if [[ "$format" == "pdf" ]]; then
            local pdf_dir="${ARTIFACTS_DIR:-$REPO_ROOT/data/artifacts}"
            mkdir -p "$pdf_dir"
            local pdf_output="$pdf_dir/${task_id}.pdf"
            local md_temp="/tmp/${task_id}.md"
            
            # 将 LLM 输出写到临时 Markdown 文件
            echo "$hermes_output" > "$md_temp"
            _append_user_context_section "$md_temp" "$include_ctx_val" "$article_ids_str"
            
            # 调用 generate_pdf.py 生成 PDF
            if python3 "$REPO_ROOT/scripts/generate_pdf.py" "$md_temp" "$pdf_output" "$info_graphic_title" 2>&1; then
                output_path="$pdf_output"
                rm -f "$md_temp"
                log "PDF generated: $pdf_output"
            else
                log "PDF generation failed, falling back to .out"
                rm -f "$md_temp"
            fi
        fi
    fi

    if [[ -z "$output_path" ]]; then
        # Fall back to a default location under MONI_OUTPUT_DIR
        local output_dir="${MONI_OUTPUT_DIR:-$HOME/.moni/output}"
        mkdir -p "$output_dir"
        output_path="$output_dir/${task_id}.out"
        echo "$hermes_output" > "$output_path"
        _append_user_context_section "$output_path" "$include_ctx_val" "$article_ids_str"
    fi

    # v1.0.5.17: 5-stage progress tracking.  Each stage transition
    # writes progress_stage / progress_pct / progress_message so the
    # UI can show a progress bar + 5-stage stepper without polling
    # the worker log.  See migration 008 for the columns.
    #
    # Stage 5 (writing_artifact) is what we're entering now — the
    # output_path above is the artifact we're about to write.  We
    # mark 95% (close to done but not 100; the DB UPDATE for status=done
    # below will bump to 100 alongside it).
    update_progress "$task_id" "writing_artifact" 95 \
        "正在写 artifact 到 $output_path ..."

    log "DONE $task_id -> $output_path"
    log "DONE $task_id -> $output_path"
    task_status_update "$task_id" "done" "$output_path"
    # Bump pct to 100 alongside the done status so the UI bar fills
    sqlite3 "$DB_PATH" \
        "UPDATE share_tasks SET progress_pct=100 WHERE id='$task_id'" \
        >/dev/null 2>&1 || true
    log "DONE $task_id -> $output_path"
    return 0
}

# --- Main -------------------------------------------------------------------
log "worker started — queue=$QUEUE_DIR timeout=${TIMEOUT_SECONDS}s"

# Ensure DB exists and has the share_tasks table
if [[ ! -f "$DB_PATH" ]]; then
    log "WARN: DB not found at $DB_PATH — creating directory"
    mkdir -p "$(dirname "$DB_PATH")"
fi

# v1.0.10: process pending tasks from DB first (queue dir has 1000s of
# historical json; scanning all of them makes new briefs wait minutes).
# Fall back to full dir scan only if DB query fails.
pending_ids=$(python3 - "$DB_PATH" <<'PYEOF'
import sqlite3, sys
try:
    conn = sqlite3.connect(sys.argv[1])
    rows = conn.execute(
        "SELECT id FROM share_tasks WHERE status='pending' ORDER BY created_at ASC"
    ).fetchall()
    for r in rows:
        print(r[0])
except Exception as e:
    print(f"[pending query failed] {e}", file=sys.stderr)
PYEOF
)

_processed_any=false
if [[ -n "$pending_ids" ]]; then
    _processed_any=true
    # Tell the launcher that there is real work before entering the first
    # manifest.  A long task must report started without waiting to exit.
    _write_worker_result "started"
    while IFS= read -r tid; do
        [[ -z "$tid" ]] && continue
        manifest="$QUEUE_DIR/${tid}.json"
        if [[ -f "$manifest" ]]; then
            process_manifest "$manifest"
        else
            log "SKIP $tid — pending in DB but no queue file $manifest"
        fi
    done <<< "$pending_ids"
else
    log "no pending share_tasks in DB — nothing to process"
    _write_worker_result "nothing_to_do"
fi

log "worker finished"
