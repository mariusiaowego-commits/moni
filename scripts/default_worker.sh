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
# v1.0.5.18: REPO_ROOT is used by the prompt-lookup code path.  We
# resolve it from the script's own path so the worker finds
# moni/share/prompts/ regardless of how it was launched.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QUEUE_DIR="${MONI_QUEUE_DIR:-${HOME:-~}/.moni/queue}"
LOCK_FILE="${QUEUE_DIR}/.lock"
DB_PATH="${MONI_DB:-${HOME:-~}/.moni/data/moni.db}"
TIMEOUT_SECONDS=300          # 5 minutes per task

# v1.0.5.18: prompt directory.  Relative to the project root so
# the worker finds the per-format prompt files regardless of the
# worker's CWD.  Defaults to ${REPO_ROOT}/moni/share/prompts/.
PROMPT_DIR="${MONI_PROMPT_DIR:-${REPO_ROOT}/moni/share/prompts}"

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
# v1.0.5.18: pruned 6 formats.  Only info_graphic and report_png use
# a real skill; the rest rely on the content prompt alone.
skill_for_format() {
    local format="$1"
    case "$format" in
        info_graphic|infographic) echo "baoyu-infographic" ;;
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
    case "$format" in
        markdown)         echo "format-markdown.md" ;;
        html_landing)     echo "format-html.md" ;;
        rich_text)        echo "format-rich-text.md" ;;
        report_png)       echo "format-report-png.md" ;;
        plain_text)       echo "" ;;  # no LLM pass
        info_graphic_png) echo "" ;;  # baoyu-infographic skill has its own prompt
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

    # Fetch article content from DB (including existing ai_summary)
    local article_content=""
    if [[ -n "$article_ids_str" ]]; then
        # Python: parse JSON array and query with parameterized IN clause
        article_content=$(python3 - "$article_ids_str" "$DB_PATH" <<'PYEOF'
import json, ast, sqlite3, sys
ids_str = sys.argv[1]
db_path = sys.argv[2]
try:
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
        "SELECT id, title, raw_content, ai_summary FROM articles WHERE id IN ({})".format(placeholders),
        ids
    )
    parts = []
    skipped = []
    for row in cur.fetchall():
        aid, title, content, ai_summary = row
        if content and content.strip():
            block = f"### 文章: {title}\nID: {aid}\n\n{content}"
            if ai_summary and ai_summary.strip():
                block += f"\n\n**AI 摘要**: {ai_summary}"
            parts.append(block)
        else:
            skipped.append(aid)
    if skipped:
        import sys as _sys
        print(f"[warn] {len(skipped)} article(s) with empty content skipped: {skipped}", file=_sys.stderr)
    print("\n\n---\n\n".join(parts))
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

        local gen_result
        gen_result=$(python3 "$REPO_ROOT/moni/share/summary_gen.py" \
            "$DB_PATH" "$article_ids_str" "$manifest_path" 2>/dev/null || echo "{}")

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
def sq(s):
    # single-quote-wrap a string for safe shell eval
    return "'" + str(s).replace("'", "'\"'\"'") + "'"
print(f"include_abstract={sq('true' if ia else 'false')}")
print(f"include_fact_check={sq('true' if fc else 'false')}")
print(f"provider_override={sq(prov)}")
print(f"model_override={sq(mod)}")
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
    pf=$(prompt_file_for_format "$format")
    if [[ -n "$pf" ]]; then
        local format_prompt_file="$PROMPT_DIR/$pf"
        [[ -f "$format_prompt_file" ]] && format_prompt=$(<"$format_prompt_file")
    fi

    # Skill clause — for non-report_png formats, we use the format's
    # own skill_for_format() mapping.  For report_png, the skill is
    # derived from the user's generation_approach choice (defaulting
    # to baoyu-infographic if unspecified).
    local skill_clause=""
    local effective_skill="$skill_name"
    if [[ "$format" == "report_png" ]]; then
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
    prompt+="---\n\n"
    prompt+="## 文章数据\n\n${escaped_content}"

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
    update_progress "$task_id" "invoking_llm" 60 \
        "正在调 hermes chat (timeout=${TIMEOUT_SECONDS}s) ..."

    # v1.0.5.19: Invoke LLM via call_llm() wrapper (unified path with
    # token logging).  Write prompt to temp file to avoid shell escaping
    # issues, then call shell_call.py which routes through call_llm().
    local hermes_exit=0
    local hermes_output

    local prompt_file="/tmp/moni-prompt-${task_id}.txt"
    printf '%s' "$prompt" > "$prompt_file"

    local call_args=("--prompt-file" "$prompt_file" "--module" "share-content")
    [[ -n "$provider_override" ]] && call_args+=("--provider" "$provider_override")
    [[ -n "$model_override" ]] && call_args+=("--model" "$model_override")

    hermes_output=$(python3 "$REPO_ROOT/moni/llm/shell_call.py" "${call_args[@]}" 2>&1) || hermes_exit=$?
    rm -f "$prompt_file"

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
        # v1.0.5.19 fix: the old grep regex matched URLs like
        # //www.mpaypass.com.cn/ and /体育用品/船舶运输 because
        # [[:alnum:]_./-] includes dots and slashes.  Use Python
        # with negative lookbehind to skip :// and // paths, then
        # validate the path exists on the filesystem.
        output_path=$(python3 -c "
import re, os, sys
text = sys.stdin.read()
# (?<![:/]) rejects matches preceded by : (as in ://) or / (as in //)
pattern = re.compile(r'(?<![:/])/[A-Za-z0-9_][A-Za-z0-9_./-]*')
for m in pattern.finditer(text):
    p = m.group(0)
    if len(p) > 1 and os.path.exists(p):
        print(p)
        sys.exit(0)
print('')
" <<< "$hermes_output" 2>/dev/null || echo "")
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
