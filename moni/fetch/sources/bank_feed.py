"""FetcherBankFeed — 银行同业情报 feed 桥接源 (v1.0.76, sprint-40).

上游: 一个**外部 agent** (``scripts/bank_bot/run_bot.py``) 定期跑, 抓取
银行同业的资讯/新闻 (五大行为重点), 产出一份 JSON feed 写到 moni 的数据
目录下:

    data/bank_feed/YYYY-MM-DD.json
    data/bank_feed/latest.json

契约见 ``docs/plans/2026-11-12_sprint-40-bank-intel-bot.md``:

    {
      "date": "2026-11-12",
      "generated_at": "2026-11-12T08:05:00+08:00",
      "counts_by_bank": {"icbc": 3, "boc": 5},
      "items": [
        {
          "id": "icbc-20261103-1290636159689232384",
          "url": "https://www.icbc.com.cn/page/1290636159689232384.html",
          "title": "工商银行2026年前三季度经营稳中有进",
          "text": "正文或摘要 (>=100 字才能进案例库)",
          "created_at": "2026-11-03T00:00:00+08:00",
          "bank": "icbc",
          "source_type": "official|media|wechat",
          "publisher": "中国工商银行",
          "captured_at": "2026-11-12T08:05:00+08:00"
        }
      ]
    }

与 ``xbot_feed`` 的区别 (sprint-40 的设计取舍)
---------------------------------------------
* **transport**: xbot 从私有 GitHub 仓库 clone; 本源从**本地目录**读 ——
  bot 与 moni 同机, 少一层 git 凭据与并发冲突。
* **触发**: xbot 由上游机器推送; 本源在抓取时若发现今天的 feed 缺失,
  **就地调 agent 补跑** (``scripts/bank_bot/run_bot.py``), 走 hermes 的
  ``moni`` profile —— 与 moni 现有 LLM 链路同一条 (hermes → magpie),
  不新增任何凭证。
* **闸门**: 本源只绑定一个 tag (``peer-banks``), 所以关键词闸门直接用
  ``base.run()`` 内建的 tag-keyword 闸门 (``moni/fetch/tag_filter.py``),
  不像 xbot 那样要自己算「候选 tag → 真命中」。

失败语义
--------
**agent 失败 ≠ 今天没新闻。** 抓不到时抛 ``RuntimeError``, 让 job 记
``failed``; 绝不静默返回 0 篇 (0 篇和「真的没有」在报表里长得一样,
含义完全相反)。
"""

from __future__ import annotations

import copy
import json
import logging
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..base import BaseFetcher, register_fetcher

logger = logging.getLogger(__name__)

#: feed 目录在 moni 数据目录下的子目录名
FEED_SUBDIR = "bank_feed"

#: 导入状态文件名 (放在 moni 数据目录下)
STATE_FILENAME = ".bank_feed_state.json"

#: 相对 feed 目录的候选 tag (默认; 用户可在 fetch_config.candidate_tags 改)
DEFAULT_CANDIDATE_TAGS: list[str] = ["peer-banks"]

#: bot 脚本 (相对 moni 仓库根目录)
DEFAULT_BOT_SCRIPT = "scripts/bank_bot/run_bot.py"

#: 目标清单的默认来源 (相对 moni 仓库根目录)
DEFAULT_TARGETS_FILE = "config/bank_targets.json"

#: 抓取配置默认值。``sources.fetch_config`` 里用户配的同名键会覆盖这里。
DEFAULT_FETCH_CONFIG: dict[str, Any] = {
    # 目标清单: [{"id", "kind", "url", "bank", "publisher"}]
    # kind: official_list | media_rss | wechat_article
    "targets": [],
    # 候选 tag (闸门用哪个 tag 的关键词)
    "candidate_tags": list(DEFAULT_CANDIDATE_TAGS),
    # 只导入最近 N 天的 feed (0 = 不限制)
    "max_days": 30,
    # 整轮抓取的任务级超时 (web 端 fetch job 的上限, 读 timeout_sec)。
    # 必须大于 bot_timeout_sec, 给读盘 / 入库留余量。
    "timeout_sec": 1800,
    # agent (bot) 超时秒数 —— 媒体段 ~10s, agent 段预算 1200s, 留余量
    "bot_timeout_sec": 1500,
    # 抓到没有今天的 feed 时, 是否就地调 agent 补跑
    "bot_enabled": True,
    # bot 脚本路径 (相对 moni 仓库根目录)
    "bot_script": DEFAULT_BOT_SCRIPT,
    # 目标清单的默认文件 (fetch_config.targets 为空时用)
    "targets_file": DEFAULT_TARGETS_FILE,
    # 跨源 URL 软去重
    "skip_existing_urls": True,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_published_at(raw: Any) -> str | None:
    """把上游的时间字段归一成 ISO 8601 (UTC)。"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(raw).strip()
    if not text:
        return None
    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d"):
            try:
                parsed = datetime.strptime(text[: len(fmt) + 2].strip(), fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
    return parsed.astimezone(timezone.utc).isoformat()


@register_fetcher
class FetcherBankFeed(BaseFetcher):
    """Import the bank-intel agent feed (agent → local JSON dir → articles)."""

    source_name: str = "bank_feed"

    def __init__(self) -> None:
        super().__init__()
        self._cfg: dict[str, Any] = {}
        self._pending_state: dict[str, Any] | None = None
        self._round_ids: list[str] = []
        self._inserted_ok = False

    # ── config ────────────────────────────────────────────────────────────
    def _load_config(self) -> dict[str, Any]:
        cfg = copy.deepcopy(DEFAULT_FETCH_CONFIG)
        try:
            from ...db.connection import get_db

            row = get_db().execute(
                "SELECT fetch_config FROM sources WHERE name = ?",
                (self.source_name,),
            ).fetchone()
            if row and row["fetch_config"]:
                user = json.loads(row["fetch_config"])
                if isinstance(user, dict):
                    for key, value in user.items():
                        if key in DEFAULT_FETCH_CONFIG:
                            cfg[key] = value
        except Exception as exc:  # noqa: BLE001
            logger.warning("[bank-feed] read fetch_config failed: %s", exc)
        return cfg

    # ── paths ─────────────────────────────────────────────────────────────
    def _data_dir(self) -> Path:
        import os

        env_dir = os.environ.get("MONI_DATA_DIR")
        if env_dir:
            return Path(env_dir)
        return Path("data")

    def _feed_dir(self) -> Path:
        return self._data_dir() / FEED_SUBDIR

    def _state_path(self) -> Path:
        return self._data_dir() / STATE_FILENAME

    def _repo_root(self) -> Path:
        """moni 仓库根目录 (本文件在 ``<root>/moni/fetch/sources/``)。"""
        return Path(__file__).resolve().parents[3]

    # ── targets ───────────────────────────────────────────────────────────
    def _load_targets(self, cfg: dict[str, Any]) -> list[dict[str, Any]]:
        """目标清单: fetch_config.targets 优先, 否则读仓库里的默认文件。"""
        raw_targets = cfg.get("targets")
        if isinstance(raw_targets, list) and raw_targets:
            return [t for t in raw_targets if isinstance(t, dict)]

        path = self._repo_root() / str(
            cfg.get("targets_file") or DEFAULT_TARGETS_FILE
        )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("[bank-feed] cannot read targets file %s: %s", path, exc)
            return []
        targets = payload.get("targets") if isinstance(payload, dict) else payload
        if not isinstance(targets, list):
            return []
        return [t for t in targets if isinstance(t, dict)]

    # ── agent (bot) ───────────────────────────────────────────────────────
    def _feed_is_fresh(self) -> bool:
        """今天的 feed 是否已经在盘上。"""
        latest = self._feed_dir() / "latest.json"
        try:
            payload = json.loads(latest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(payload, dict):
            return False
        return str(payload.get("date") or "") == date.today().isoformat()

    def _run_bot(self, cfg: dict[str, Any], targets: list[dict[str, Any]]) -> None:
        """就地调 agent 补跑今天的 feed。失败 → 抛错 (不静默)。"""
        script = self._repo_root() / str(cfg.get("bot_script") or DEFAULT_BOT_SCRIPT)
        if not script.exists():
            raise RuntimeError(f"bank-feed: bot script not found: {script}")

        timeout = int(cfg.get("bot_timeout_sec") or 900)
        payload = json.dumps({"targets": targets}, ensure_ascii=False)
        cmd = [
            sys.executable or "python3",
            str(script),
            "--output-dir",
            str(self._feed_dir()),
            "--date",
            date.today().isoformat(),
        ]
        logger.info("[bank-feed] running agent bot (timeout %ss)", timeout)
        try:
            proc = subprocess.run(
                cmd,
                input=payload,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self._repo_root()),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"bank-feed: agent bot timed out after {timeout}s"
            ) from exc
        except OSError as exc:
            raise RuntimeError(f"bank-feed: cannot start agent bot: {exc}") from exc

        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
            raise RuntimeError(
                "bank-feed: agent bot failed (exit "
                f"{proc.returncode}): " + " | ".join(tail)
            )
        if not self._feed_is_fresh():
            raise RuntimeError(
                "bank-feed: agent bot exited 0 but produced no feed for today"
            )

    # ── feed files ────────────────────────────────────────────────────────
    def _feed_files(self, max_days: int) -> list[Path]:
        feed_dir = self._feed_dir()
        if not feed_dir.is_dir():
            return []
        files = sorted(
            p for p in feed_dir.glob("*.json")
            if p.name != "latest.json" and not p.name.startswith("_")
        )
        if max_days <= 0:
            return files
        cutoff = date.today() - timedelta(days=max_days)
        kept: list[Path] = []
        for path in files:
            try:
                stamp = datetime.strptime(path.stem, "%Y-%m-%d").date()
            except ValueError:
                continue
            if stamp >= cutoff:
                kept.append(path)
        return kept

    def _read_feed_file(self, path: Path) -> list[dict[str, Any]]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.error("[bank-feed] cannot read %s: %s", path.name, exc)
            return []
        if not isinstance(payload, dict):
            logger.error(
                "[bank-feed] %s: unexpected payload type %s",
                path.name, type(payload).__name__,
            )
            return []
        items = payload.get("items")
        if not isinstance(items, list):
            logger.error("[bank-feed] %s: no items array", path.name)
            return []
        return [it for it in items if isinstance(it, dict)]

    # ── state ─────────────────────────────────────────────────────────────
    def _load_state(self) -> dict[str, Any]:
        path = self._state_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"imported_ids": []}
        if not isinstance(payload, dict):
            return {"imported_ids": []}
        ids = payload.get("imported_ids")
        return {"imported_ids": list(ids) if isinstance(ids, list) else []}

    def _save_state(self, state: dict[str, Any]) -> None:
        path = self._state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            logger.error("[bank-feed] cannot write state file: %s", exc)

    # ── fetch / parse ─────────────────────────────────────────────────────
    def fetch(self) -> list[dict[str, Any]]:
        """确保今天的 feed 在盘上, 然后读出还没导入的 raw item。"""
        cfg = self._load_config()
        self._cfg = cfg
        targets = self._load_targets(cfg)

        if not self._feed_is_fresh():
            if not cfg.get("bot_enabled", True):
                raise RuntimeError(
                    "bank-feed: no feed for today and bot_enabled is off"
                )
            if not targets:
                raise RuntimeError(
                    "bank-feed: no targets configured — cannot run the agent"
                )
            self._run_bot(cfg, targets)

        files = self._feed_files(int(cfg.get("max_days") or 0))
        if not files:
            raise RuntimeError(
                f"bank-feed: no feed JSON under {self._feed_dir()}"
            )

        state = self._load_state()
        imported = {str(i) for i in state.get("imported_ids", [])}

        raws: list[dict[str, Any]] = []
        seen_in_round: set[str] = set()
        skipped_already = 0
        for path in files:
            for item in self._read_feed_file(path):
                key = str(item.get("id") or "").strip() or str(
                    item.get("url") or ""
                ).strip()
                if not key:
                    continue
                if key in imported:
                    skipped_already += 1
                    continue
                if key in seen_in_round:
                    continue
                seen_in_round.add(key)
                raws.append(item)

        self._pending_state = state
        logger.info(
            "[bank-feed] %d feed file(s) → %d new raw item(s) (%d already imported)",
            len(files), len(raws), skipped_already,
        )
        return raws

    def parse(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """把一条 feed item 映射成 moni 的规范 article dict。"""
        url = str(raw.get("url") or "").strip()
        title = str(raw.get("title") or "").strip()
        text = str(raw.get("text") or "").strip()

        if not url and not title:
            logger.debug("[bank-feed] item missing url+title — skipping")
            return None
        if not title:
            title = text.splitlines()[0][:120] if text else url

        if self._cfg.get("skip_existing_urls", True) and self._url_exists(url):
            logger.debug("[bank-feed] url already stored elsewhere — skipping")
            return None

        bank = str(raw.get("bank") or "").strip()
        source_type = str(raw.get("source_type") or "").strip()
        publisher = str(raw.get("publisher") or "").strip() or None

        content_parts = [text] if text else []
        # 归属元数据只用**代码** (bank:ccb), 不写中文行名 —— 正文里出现
        # 「建设银行」会让 tag 闸门恒真, 闸门就退化成摆设。代码是小写
        # ASCII, 不在 peer-banks 的关键词里 (那里是大写 CCB), 也不参与
        # 大小写折叠, 所以它只做溯源信息, 不影响闸门判定。
        meta_bits: list[str] = []
        if bank:
            meta_bits.append(f"bank:{bank}")
        if source_type:
            meta_bits.append(f"type:{source_type}")
        target_id = str(raw.get("target_id") or "").strip()
        if target_id:
            meta_bits.append(f"target:{target_id}")
        if meta_bits:
            content_parts.append("[" + " ".join(meta_bits) + "]")

        return {
            "title": title,
            "url": url,
            "source": self.source_name,
            "published_at": _normalize_published_at(raw.get("created_at")),
            "content": "\n\n".join(p for p in content_parts if p),
            "publisher": publisher,
            "bank": bank,
            "source_type": source_type,
            # feed item id —— 用来在入库成功后记「已导入」(见 dedupe_insert)
            "bank_item_id": str(raw.get("id") or "").strip(),
        }

    # ── helpers ───────────────────────────────────────────────────────────
    @staticmethod
    def _url_exists(url: str) -> bool:
        if not url:
            return False
        try:
            from ...db.connection import get_db

            row = get_db().execute(
                "SELECT 1 FROM articles WHERE url = ? LIMIT 1", (url,)
            ).fetchone()
            return row is not None
        except Exception as exc:  # noqa: BLE001
            logger.warning("[bank-feed] cannot check url existence: %s", exc)
            return False

    # ── dedupe_insert ─────────────────────────────────────────────────────
    def dedupe_insert(
        self,
        articles: list[dict[str, Any]],
        keyword_hits: list[dict[str, Any]] | None = None,
    ) -> int:
        """插入文章, 并记下本轮真正入库的 feed item id。

        走到这一步的条目**已经过了所有闸门** (源级黑白名单 + tag 关键词
        闸门)。被闸门挡掉的条目不会进这里, 所以下一轮还会被重新处理
        (闸门配置改了以后它们还有机会入库)。

        命中记录直接用基类算出来的 ``keyword_hits`` —— tag-keyword 闸门
        (``moni/fetch/tag_filter.py``) 给的每条命中都带真实的
        ``tag_name``, ``dedupe_insert`` 只给**命中的** tag 写
        ``article_tags`` (AGENTS.md 铁律 3)。
        """
        for art in articles:
            item_id = art.get("bank_item_id")
            if item_id:
                self._round_ids.append(str(item_id))
        inserted = super().dedupe_insert(articles, keyword_hits)
        self._inserted_ok = True
        return inserted

    # ── run ───────────────────────────────────────────────────────────────
    def run(self, dry_run: bool = False) -> dict[str, Any]:
        """跑完管线后落导入状态 (入库成功才记已导入)。"""
        self._inserted_ok = False
        self._pending_state = None
        self._round_ids = []
        result = super().run(dry_run=dry_run)
        if not dry_run and not result.get("error") and self._inserted_ok:
            self._flush_state()
        return result

    def _flush_state(self) -> None:
        state = self._pending_state or self._load_state()
        imported = list(state.get("imported_ids", []))
        known = set(imported)
        for item_id in self._round_ids:
            if item_id not in known:
                known.add(item_id)
                imported.append(item_id)
        state["imported_ids"] = imported
        self._save_state(state)

