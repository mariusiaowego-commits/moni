# moni — 同业信源自动抓取与本地新闻库

## 首次启动

```bash
# 1. 创建虚拟环境
python3 -m venv .venv && source .venv/bin/activate

# 2. 安装 moni 包
pip install -e .

# 3. 初始化数据库
bash scripts/init_db.sh
```

## 数据库 CLI

```bash
# 查看每张表的行数
python3 -m moni db stats

# 导出数据（JSON）
python3 -m moni db export json

# 导出数据（CSV）
python3 -m moni db export csv
```

## 目录结构

```
moni/
  db/         schema.sql, migrate.py, connection.py
  cli/        main.py — python3 -m moni <subcmd>
config/
  sources.yaml   5个信源初始配置
  tags.yaml      5个 seed tags
data/
  news.db        SQLite 数据库（gitignore）
scripts/
  init_db.sh     初始化 DB
  web.sh         占位（Web task 再实现）
```

## 迁移

v1.0 单 schema 版本，启动时自动 migrate（`moni db` 命令会触发）。

## 注意事项

- 旧 `state.json` 兼容：v1.0 不做迁移，下一个 task 处理
- FTS5 性能：v1.0 数据量小（<10k 行），无需优化
