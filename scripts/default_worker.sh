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
# Concurrency: PID-file lock on $QUEUE_DIR/.lock (POSIX, macOS + Linux).
#              Workers that can't acquire the lock exit 0 immediately.
#
# Dependencies: bash, python3, sqlite3, hermes CLI. No GNU timeout needed.
# =============================================================================

set -euo pipefail

# --- Config ----------------------------------------------------------------
QUEUE_DIR="${MONI_QUEUE_DIR:-${HOME:-~}/.moni/queue}"
LOCK_FILE="${QUEUE_DIR}/.lock"
DB_PATH="${MONI_DB:-${HOME:-~}/.moni/data/moni.db}"
TIMEOUT_SECONDS=300          # 5 minutes per task

# --- Env validation ---------------------------------------------------------
if [[ ! -d "$QUEUE_DIR" ]]; then
    echo "[worker] queue dir not found: $QUEUE_DIR — nothing to do" >&2
    exit 0
fi

# --- Concurrent execution guard (POSIX, works on macOS + Linux) -----------
# Atomic creation of $QUEUE_DIR/.lock containing our PID.
# - If the lock file doesn't exist, create it with our PID and hold it.
# - If it exists and the PID is alive, another worker is running — exit.
# - If the PID is dead (stale), overwrite the lock with our PID.
_acquired_lock=false
if [[ ! -e "$LOCK_FILE" ]]; then
    echo $$ > "$LOCK_FILE" && _acquired_lock=true
elif pid=$(cat "$LOCK_FILE" 2>/dev/null) && kill -0 "$pid" 2>/dev/null; then
    echo "[worker] another instance (PID $pid) is running — exiting" >&2
    exit 0
else
    # Stale lock — overwrite with our PID
    echo $$ > "$LOCK_FILE" && _acquired_lock=true
fi

if ! "$_acquired_lock"; then
    echo "[worker] failed to acquire lock — exiting" >&2
    exit 1
fi

# Release lock on exit
trap 'rm -f "$LOCK_FILE"' EXIT

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

task_status_update() {
    local task_id="$1"
    local status="$2"
    local output_path="${3:-}"
    local error_msg="${4:-}"

    python3 - "$task_id" "$status" "$output_path" "$error_msg" "$DB_PATH" <<'PYEOF'
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
elif status == "running":
    cur.execute(
        "UPDATE share_tasks SET status=?, started_at=? "
        "WHERE id=? AND status='pending'",
        (status, now, task_id)
    )

conn.commit()
PYEOF
}

# Build the skill flag from format name.
# Maps format strings to Hermes skill names.
skill_for_format() {
    local format="$1"
    case "$format" in
        markdown|md)         echo "dizical-report" ;;
        info_graphic|infographic) echo "baoyu-infographic" ;;
        comic|baoyu-comic)   echo "baoyu-comic" ;;
        article-illustrator) echo "baoyu-article-illustrator" ;;
        translation)         echo "translation" ;;
        *)                  echo "" ;;
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

    local task_id format article_ids_str output_path skill_name error_msg
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
    task_status_update "$task_id" "running"
    update_progress "$task_id" "fetching" 20 \
        "正在从 articles 表读 raw_content ..."

    log "PROCESS $task_id format=$format output=$output_path"

    # Fetch article content from DB
    local article_content=""
    if [[ -n "$article_ids_str" ]]; then
        # Python: parse JSON array and query with parameterized IN clause
        article_content=$(python3 - "$article_ids_str" "$DB_PATH" <<'PYEOF'
import json, ast, sqlite3, sys
ids_str = sys.argv[1]
db_path = sys.argv[2]
try:
    # Handle both JSON ["a","b"] and Python repr ['a','b'] formats
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
    cur = conn.execute(
        "SELECT raw_content FROM articles WHERE id IN ({}) LIMIT 1".format(placeholders),
        ids
    )
    row = cur.fetchone()
    print(row[0] if row else "")
except Exception:
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

    # Build skill flag
    skill_name=$(skill_for_format "$format")

    # Escape article content for shell: replace " with \" and wrap in double
    # quotes.  Use a here-doc with a Python interpolator to avoid all shell
    # escaping issues.
    local escaped_content
    escaped_content=$(python3 -c "
import json, sys
content = '''$article_content'''
print(json.dumps(content))
" 2>/dev/null || echo "\"\"")

    # v1.0.5.17: stage 2 → 3 transition.  Article content is now in
    # hand; we mark building_prompt so the UI can show "拼 prompt
    # 中" while the Python interpolator runs (typically <1s but
    # visible for big content).
    update_progress "$task_id" "building_prompt" 40 \
        "正在拼 prompt (skill=$skill_name, content=$(printf '%d' "${#escaped_content}") 字节) ..."

    # Build the hermes prompt
    local skill_clause=""
    if [[ -n "$skill_name" ]]; then
        skill_clause="Use the skill: $skill_name. "
    fi

    local prompt="You are a content processing worker. Process the following article and produce output at the specified path.

Article content:
$escaped_content

Required format: $format
Output path: $output_path

${skill_clause}Write the final output to: $output_path
If the output path is a directory, generate an appropriate file inside it.
Do NOT rely on any external web services for content — use only the article provided above."

    # v1.0.5.17: stage 3 → 4 transition.  The prompt is built; next
    # up is the 5-minute hermes chat subprocess.  This is the
    # longest stage (typically 30s-3min for an LLM call), so we
    # write a clear message so the UI can show "正在调 LLM, 估
    # X 秒" while it runs.
    update_progress "$task_id" "invoking_llm" 60 \
        "正在调 hermes chat (timeout=${TIMEOUT_SECONDS}s) ..."

    # Invoke hermes agent with 5-minute timeout (Python, works on macOS + Linux)
    local hermes_exit=0
    local hermes_output

    hermes_output=$(python3 - "$TIMEOUT_SECONDS" "$prompt" <<'PYEOF'
import subprocess, sys, signal, shlex

timeout_sec = int(sys.argv[1])
prompt_text = sys.argv[2]

proc = subprocess.Popen(
    ["hermes", "-z", prompt_text, "--profile", "default"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
)

try:
    stdout, _ = proc.communicate(timeout=timeout_sec)
    sys.stdout.write(stdout)
    sys.exit(proc.returncode)
except subprocess.TimeoutExpired:
    proc.kill()
    proc.wait()
    sys.stderr.write(f"timeout after {timeout_sec}s\n")
    sys.exit(124)
PYEOF
) || hermes_exit=$?

    if [[ $hermes_exit -eq 124 ]]; then
        error_msg="timeout after ${TIMEOUT_SECONDS}s"
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

    # Success — extract output path from agent response if not already set
    if [[ -z "$output_path" ]]; then
        # v1.0.5.14: BSD grep has a long-standing bug where the
        # regex `[^ \n]+` truncates strings at letter 'n' in
        # certain versions (it treats a literal \n inside [^ ]
        # as the actual newline char).  We use POSIX char classes
        # instead, which are unambiguous on both BSD and GNU grep.
        output_path=$(echo "$hermes_output" | grep -oE '/[[:alnum:]_./-]+|~\.[[:alnum:]_./-]+|\$HOME/[[:alnum:]_./-]+' | head -1 || echo "")
        if [[ -z "$output_path" ]]; then
            # Fall back to a default location under MONI_OUTPUT_DIR
            local output_dir="${MONI_OUTPUT_DIR:-$HOME/.moni/output}"
            mkdir -p "$output_dir"
            output_path="$output_dir/${task_id}.out"
            echo "$hermes_output" > "$output_path"
        fi
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
    task_status_update "$task_id" "done" "$output_path"
    # Bump pct to 100 alongside the done status so the UI bar fills
    # exactly when the row turns green.
    sqlite3 "$DB_PATH" \
        "UPDATE share_tasks SET progress_pct=100 WHERE id='$task_id'" \
        >/dev/null 2>&1 || true
    return 0
}

# --- Main -------------------------------------------------------------------
log "worker started — queue=$QUEUE_DIR timeout=${TIMEOUT_SECONDS}s"

# Ensure DB exists and has the share_tasks table
if [[ ! -f "$DB_PATH" ]]; then
    log "WARN: DB not found at $DB_PATH — creating directory"
    mkdir -p "$(dirname "$DB_PATH")"
fi

# Process each JSON file in the queue directory
for manifest in "$QUEUE_DIR"/*.json; do
    [[ -e "$manifest" ]] || break  # no glob match
    process_manifest "$manifest"
done

log "worker finished"
