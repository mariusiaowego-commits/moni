# moni Cron + Default Worker — Setup & Manual Testing

> v1.0.3-cron — `scripts/default_worker.sh` + `scripts/install_cron.sh`
> **v1.0.9.1 更新**: worker 启动子进程前 `export PATH="$HOME/.local/bin:$PATH"`, 防止 cron / launchd 子进程 PATH 精简 (只有 /usr/bin:/bin) 找不到 hermes CLI 导致 FileNotFoundError。Defense-in-depth: 任何调用方式 (cron / launchd / web UI / 手动 shell) 都能找到 hermes。详见 `vibe-coding-log.md` 2026-06-24 v1.0.9.1 段。

## 1. What it does

`scripts/default_worker.sh` is a single-purpose queue processor:

1. Scans `~/.moni/queue/*.json` for share manifest files
2. For each manifest, looks up the corresponding `share_tasks` row in `~/.moni/data/moni.db`
3. If the row's `status == 'pending'`, updates it to `running`, then invokes the **default** profile Hermes agent with a prompt containing the article content + format + `output_path`
4. On agent success → sets `status='done'` + `output_path` + `finished_at`
5. On agent failure / timeout → sets `status='failed'` + `error` message + `finished_at`

The script is **idempotent** and uses a PID-file lock (`~/.moni/queue/.lock`) so multiple
concurrent invocations (e.g. cron + manual run) won't process the same manifest twice.

## 2. Install the 5-minute crontab

```bash
cd /Users/mt16/dev/moni
./scripts/install_cron.sh install
```

This appends the following line to your user crontab:

```
*/5 * * * * /bin/bash /Users/mt16/dev/moni/scripts/default_worker.sh >> /Users/mt16/.moni/cron.log 2>&1 # moni-default-worker
```

### Other commands

```bash
./scripts/install_cron.sh status      # show whether the entry is installed
./scripts/install_cron.sh uninstall    # remove the entry (keeps other cron jobs)
./scripts/install_cron.sh show         # print the entire current user crontab
```

The crontab line carries the marker `# moni-default-worker` so `uninstall` only
removes our entry and leaves any other cron jobs intact.

## 3. Sample manifest — `~/.moni/queue/test-001.json`

A ready-to-use sample manifest lives at `~/.moni/queue/test-001.json`. It points to
a known test article so you can verify the worker end-to-end:

```json
{
  "id": "test-task-001",
  "format": "markdown",
  "article_ids": ["test-article-001"],
  "output_path": "/Users/mt16/.moni/output/test-001.md",
  "extra": {"title": "Sample Article for Worker Test", "source": "test"}
}
```

A corresponding seed article exists in the local `~/.moni/data/moni.db` (or
will be created on first run by the worker seed script). The expected
output is `~/.moni/output/test-001.md`.

> **Note:** the manifest and seed article are written by the manual test
> command below; they're not auto-installed to keep `moni` git-history clean.

## 4. Manual testing — full procedure

### 4.1 One-shot setup (creates manifest + DB row + output placeholder)

```bash
# 1. Make sure the queue + output dirs exist
mkdir -p ~/.moni/queue ~/.moni/output ~/.moni/data

# 2. Ensure the DB schema is current
cd /Users/mt16/dev/moni
./scripts/init_db.sh

# 3. Create the sample manifest
cat > ~/.moni/queue/test-001.json <<'JSON'
{
  "id": "test-task-001",
  "format": "markdown",
  "article_ids": ["test-article-001"],
  "output_path": "/Users/mt16/.moni/output/test-001.md",
  "extra": {"title": "Sample Article for Worker Test", "source": "test"}
}
JSON

# 4. Insert a 'pending' share_tasks row + a test article
/usr/local/bin/python3 - <<'PY'
import json, sqlite3, datetime, pathlib
db = pathlib.Path.home() / ".moni" / "data" / "moni.db"
db.parent.mkdir(parents=True, exist_ok=True)
conn = sqlite3.connect(db)
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
conn.executescript(open("/Users/mt16/dev/moni/moni/db/schema.sql").read())
conn.execute(
    "INSERT OR REPLACE INTO share_tasks (id, format, article_ids, status, created_at, extra) "
    "VALUES (?, ?, ?, 'pending', ?, ?)",
    ("test-task-001", "markdown", json.dumps(["test-article-001"]), now,
     json.dumps({"title": "Sample Article for Worker Test", "source": "test"}))
)
conn.execute(
    "INSERT OR REPLACE INTO articles (id, title, url, source, fetched_at, raw_content) "
    "VALUES (?, ?, ?, ?, ?, ?)",
    ("test-article-001",
     "Sample Article for Worker Test",
     "https://example.invalid/test-001",
     "test",
     now,
     open(pathlib.Path.home() / ".moni" / "output" / "test-001.md").read()
     if (pathlib.Path.home() / ".moni" / "output" / "test-001.md").exists()
     else "This is a sample article body used to exercise default_worker.sh end-to-end.")
)
conn.commit()
print("Seeded: share_tasks row + test article")
PY
```

### 4.2 Run the worker once (foreground)

```bash
bash /Users/mt16/dev/moni/scripts/default_worker.sh
```

You should see log output similar to:

```
[10:00:00] worker started — queue=/Users/mt16/.moni/queue timeout=300s
[10:00:00] PROCESS test-task-001 format=markdown output=/Users/mt16/.moni/output/test-001.md
[10:00:30] DONE test-task-001 -> /Users/mt16/.moni/output/test-001.md
[10:00:30] worker finished
```

### 4.3 Verify status transitions

```bash
# Check share_tasks row
sqlite3 ~/.moni/data/moni.db \
  "SELECT id, status, output_path, finished_at FROM share_tasks WHERE id='test-task-001'"

# Expected: status=done, output_path=/Users/mt16/.moni/output/test-001.md, finished_at populated
```

### 4.4 Verify the output file exists and is non-empty

```bash
ls -l ~/.moni/output/test-001.md
head -20 ~/.moni/output/test-001.md
```

### 4.5 Verify the failure path (optional)

Force a failure by writing a manifest that references a missing article:

```bash
cat > ~/.moni/queue/test-fail.json <<'JSON'
{
  "id": "test-task-fail",
  "format": "markdown",
  "article_ids": ["nonexistent-article-id"],
  "output_path": "/Users/mt16/.moni/output/test-fail.md"
}
JSON

/usr/local/bin/python3 - <<'PY'
import sqlite3, datetime, json, pathlib
db = pathlib.Path.home() / ".moni" / "data" / "moni.db"
conn = sqlite3.connect(db)
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
conn.execute(
    "INSERT OR REPLACE INTO share_tasks (id, format, article_ids, status, created_at) "
    "VALUES (?, ?, ?, 'pending', ?)",
    ("test-task-fail", "markdown", json.dumps(["nonexistent-article-id"]), now)
)
conn.commit()
PY

bash /Users/mt16/dev/moni/scripts/default_worker.sh

# Verify
sqlite3 ~/.moni/data/moni.db \
  "SELECT id, status, error FROM share_tasks WHERE id='test-task-fail'"
# Expected: status=failed, error contains "no article content found"
```

## 5. Logs

- **Cron logs**: `~/.moni/cron.log` (set by `install_cron.sh`)
- **Worker run logs**: in `~/.moni/cron.log` when run via cron, or stdout when run manually
- **DB run history**: `cron_runs` table for in-process APScheduler jobs;
  `share_tasks.started_at` / `finished_at` / `error` for manifest jobs

`fetch_all_sources` writes its `cron_runs` row up front with
`extra = {"in_progress": true}` and clears it in the final UPDATE; the API
reports such rows as `status="running"` (never as `success`). On scheduler
startup, rows still carrying the flag are closed as
`success=0, error="进程中断…"`, and any `cron_run_requests` row left
`claimed` by a dead process is marked `failed` so the job can be triggered
again. The 15s tick also closes rows still `in_progress` after 3 hours, so a
failed final UPDATE heals without waiting for a restart.

## 6. Tuning

`scripts/default_worker.sh` exposes three env vars:

| Var | Default | Meaning |
|---|---|---|
| `MONI_QUEUE_DIR` | `~/.moni/queue` | Manifest directory to scan |
| `MONI_DB` | `~/.moni/data/moni.db` | SQLite path |
| `MONI_OUTPUT_DIR` | `~/.moni/output` | Fallback output dir if manifest has no `output_path` |

The per-task timeout is hard-coded to 300s (5 minutes) — matches the crontab frequency.

## 7. Format → skill mapping

The worker picks a Hermes skill based on the manifest's `format` field:

| Format | Skill loaded |
|---|---|
| `markdown`, `md` | `dizical-report` |
| `info_graphic`, `infographic` | `baoyu-infographic` |
| `comic`, `baoyu-comic` | `baoyu-comic` |
| `article-illustrator` | `baoyu-article-illustrator` |
| `translation` | `translation` |
| anything else | (no skill) |

To add a new format → skill mapping, edit the `skill_for_format()` function in `scripts/default_worker.sh`.
