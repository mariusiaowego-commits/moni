#!/usr/bin/env bash
# init_db.sh — create data/ dir and run DB migrations
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DATA_DIR="$PROJECT_DIR/data"

mkdir -p "$DATA_DIR"

# Run migrations via the Python CLI (auto-creates data/news.db)
cd "$PROJECT_DIR"
python3 -m moni db stats
echo "DB init done → data/news.db"
