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
import os
import re
import signal
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..base import BaseFetcher, register_fetcher

logger = logging.getLogger(__name__)

#: feed 目录在 moni 数据目录下的子目录名
FEED_SUBDIR = "bank_feed"

#: 导入状态文件名 (放在 moni 数据目录下)
STATE_FILENAME = ".bank_feed_state.json"

#: 本源唯一的 tag (迁移 041 建的绑定)。tag 被停用/清空关键词时,
#: 用它兜底合成命中, 避免文章入库后零 tag (AGENTS.md 铁律 3)。
SOURCE_TAG = "peer-banks"

#: bot 脚本 (相对 moni 仓库根目录)
DEFAULT_BOT_SCRIPT = "scripts/bank_bot/run_bot.py"

#: 目标清单的默认来源 (相对 moni 仓库根目录)
DEFAULT_TARGETS_FILE = "config/bank_targets.json"

#: 抓取配置默认值。``sources.fetch_config`` 里用户配的同名键会覆盖这里。
DEFAULT_FETCH_CONFIG: dict[str, Any] = {
    # 目标清单: [{"id", "kind", "url", "bank", "publisher"}]
    # kind: official_list | media_rss | wechat_article
    "targets": [],
    # 只导入最近 N 天的 feed (0 = 不限制)
    "max_days": 30,
    # 整轮抓取的任务级超时 (web 端 fetch job 的上限, 读 timeout_sec)。
    # 必须大于 bot_timeout_sec, 给读盘 / 入库留余量。
    "timeout_sec": 1800,
    # agent (bot) 超时秒数 —— 媒体段 ~10s, agent 段预算 1200s, 留余量
    "bot_timeout_sec": 1500,
    # 当天 feed 有目标失败时, 隔多久才重跑 agent 补齐 (避免连点两次烧两轮)
    # 带 errors 的当天 feed 过了这个窗口才会重新调 agent 补齐。
    # 手动点击不走这个窗口 (base.force), 见 _should_run_bot。
    "retry_after_min": 30,
    # 抓到没有今天的 feed 时, 是否就地调 agent 补跑
    "bot_enabled": True,
    # bot 脚本路径 (相对 moni 仓库根目录)
    "bot_script": DEFAULT_BOT_SCRIPT,
    # 目标清单的默认文件 (fetch_config.targets 为空时用)
    "targets_file": DEFAULT_TARGETS_FILE,
    # 跨源 URL 软去重
    "skip_existing_urls": True,
}

#: bank / target_id 只允许 ASCII 标识符 —— 它们会被写进 content 的归属
#: 元数据, 中文行名会让 tag 闸门恒真。
_IDENT_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


#: 银行代码 → 短名（只用于界面文案，抓取逻辑不看这张表）
_BANK_SHORT = {
    "icbc": "工行",
    "abc": "农行",
    "boc": "中行",
    "ccb": "建行",
    "psbc": "邮储",
    "bocom": "交行",
}

_REASON_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("timed out", "timeout", "超时"), "访问超时"),
    (("connect", "ssl", "dns", "network", "连接"), "网络连接失败"),
    (("403", "404", "http", "status"), "页面无法读取"),
    (("parse", "empty", "no item", "解析"), "未抓到内容"),
)


def _human_reason(raw: str) -> str:
    """把抓取异常翻成一句人话，界面直接显示。"""
    text = (raw or "").lower()
    for keys, label in _REASON_RULES:
        if any(key in text for key in keys):
            return label
    return "未抓到内容"


def _kill_group(pid: int, grace: float = 5.0) -> None:
    """SIGTERM 整个进程组, 宽限期后 SIGKILL (抄 llm/runner.py 的做法)。"""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(pid), sig)
        except ProcessLookupError:
            return
        except OSError:
            # 权限/时序问题也要继续走到 SIGKILL, 不能把孙进程留在原地。
            logger.warning("[bank-feed] killpg(%s, %s) failed", pid, sig)
        if sig is signal.SIGTERM:
            time.sleep(grace)


def _with_bound_tag_hits(
    articles: list[dict[str, Any]],
    keyword_hits: list[dict[str, Any]],
    bound_tags: list[str],
) -> list[dict[str, Any]]:
    """保证每篇文章都带一个本源的绑定 tag。

    不能只看「整轮 keyword_hits 是否为空」—— 源/全局关键词过滤器命中时
    ``keyword_hits`` 非空, 但那些 hit 的 ``tag_name`` 是 ``None`` 或
    ``[global]``, 落不进 ``article_tags``。所以按**每篇文章**检查它是否
    已经有绑定 tag 的命中, 没有才补一条 (AGENTS.md 铁律 3)。
    """
    bound = set(bound_tags)
    tagged_urls = {
        str(hit.get("article_url") or "")
        for hit in keyword_hits
        if hit.get("tag_name") in bound
    }
    extra = [
        {
            "article_url": art.get("url", ""),
            "keyword": "(自动归属)",
            "tag_name": bound_tags[0],
            "filter_id": None,
            "hit_field": "tag_binding",
        }
        for art in articles
        if str(art.get("url") or "") not in tagged_urls
    ]
    return list(keyword_hits) + extra


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
        self._today_errors: list[dict[str, Any]] = []
        self._bot_error = ""
        self._progress_total = 0
        self._progress_inserted = 0
        self._progress_done = 0
        self._today_items = 0
        self._skipped_existing = 0
        self._target_labels: dict[str, str] = {}

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

    def _bound_tags(self) -> list[str]:
        """本源绑定的 tag 名 (不看 is_active / keywords, 只看行是否存在)。

        tag 被删时 source_tags 会 CASCADE 掉 —— 那时返回空, 调用方必须
        拒绝入库, 否则就是「有文章、零 tag」。

        数据库读失败不在这里吞掉: 那是基础设施问题, 不是「没有绑定」。
        """
        from ...db.connection import get_db

        rows = get_db().execute(
            """
            SELECT st.tag_name
            FROM source_tags st
            JOIN sources s ON s.id = st.source_id
            WHERE s.name = ?
            """,
            (self.source_name,),
        ).fetchall()
        names = [str(r[0]) for r in rows]
        # 主 tag 优先, 其余按名字稳定排序
        return sorted(names, key=lambda n: (n != SOURCE_TAG, n))

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
    @staticmethod
    def _describe_target(target: dict[str, Any]) -> str:
        """目标在界面上的短名（工行官网 / 未央网 / …）。"""
        bank = str(target.get("bank") or "").strip()
        short = _BANK_SHORT.get(bank)
        if short:
            return f"{short}官网" if target.get("kind") == "official_list" else short
        return str(
            target.get("publisher") or target.get("name") or target.get("id") or "?"
        )

    def _target_label(self, target_id: str) -> str:
        label = self._target_labels.get(target_id)
        if label:
            return label
        bank = target_id.split("-")[0]
        short = _BANK_SHORT.get(bank)
        if short:
            return f"{short}官网" if target_id.endswith("-news") else short
        return target_id

    def _latest_payload(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(
                (self._feed_dir() / "latest.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return None
        return payload if isinstance(payload, dict) else None

    def _is_today(self, payload: dict[str, Any]) -> bool:
        return str(payload.get("date") or "") == date.today().isoformat()

    def _feed_is_fresh(self) -> bool:
        """今天的 feed 是否已经在盘上, 且这一轮没有目标失败。

        只写了一半的 feed (``errors`` 非空) 不算 fresh: 下一次抓取会重新
        调 agent 补齐。重跑不会产生重复文章 —— 已导入的 id 记在 state 里。
        """
        payload = self._latest_payload()
        return payload is not None and self._is_today(payload) and not payload.get(
            "errors"
        )

    def _today_feed_exists(self) -> bool:
        """今天的 feed 是否落盘 (允许 ``errors`` 非空 = 部分结果)。"""
        payload = self._latest_payload()
        return payload is not None and self._is_today(payload)

    def _feed_age_minutes(self) -> float | None:
        """latest.json 的 ``generated_at`` 距今多少分钟; 读不出来返回 None。"""
        payload = self._latest_payload()
        if payload is None:
            return None
        generated = _normalize_published_at(payload.get("generated_at"))
        if not generated:
            return None
        try:
            stamp = datetime.fromisoformat(generated)
        except ValueError:
            return None
        return (datetime.now(timezone.utc) - stamp).total_seconds() / 60

    def _should_run_bot(self, cfg: dict[str, Any]) -> bool:
        """今天的 feed 缺失 → 跑; 有失败 → 手动点击或过冷却窗口再跑。"""
        if not self._today_feed_exists():
            return True
        if self._feed_is_fresh():
            return False
        if getattr(self, "force", False):
            # 用户点了「立即抓取」—— 明确要求补跑失败目标, 不受冷却限制。
            return True
        age = self._feed_age_minutes()
        if age is None:
            return True
        return age >= float(cfg.get("retry_after_min") or 0)

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
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(self._repo_root()),
                start_new_session=True,
            )
        except OSError as exc:
            raise RuntimeError(f"bank-feed: cannot start agent bot: {exc}") from exc
        try:
            stdout, stderr = proc.communicate(payload, timeout=timeout)
        except subprocess.TimeoutExpired:
            # hermes 会拉起浏览器子进程; 只 kill 直接子进程会把孙进程和
            # 管道留在原地, communicate() 永远等不到 EOF。
            _kill_group(proc.pid)
            try:
                proc.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                logger.error("[bank-feed] bot stdout pipe still held after SIGKILL")
            raise RuntimeError(
                f"bank-feed: agent bot timed out after {timeout}s"
            ) from None

        if proc.returncode != 0:
            tail = (stderr or stdout or "").strip().splitlines()[-5:]
            raise RuntimeError(
                "bank-feed: agent bot failed (exit "
                f"{proc.returncode}): " + " | ".join(tail)
            )
        if not self._today_feed_exists():
            raise RuntimeError(
                "bank-feed: agent bot exited 0 but produced no feed for today"
            )

    # ── feed files ────────────────────────────────────────────────────────
    def _feed_files(self, max_days: int) -> list[Path]:
        feed_dir = self._feed_dir()
        if not feed_dir.is_dir():
            return []
        # 倒序: 同一条 item 出现在多天 feed 里时, 新的一份先被读到, 旧版本
        # 在 seen_in_round 里被丢掉 (先到先得 = 先读到新数据)。
        files = sorted(
            (
                p for p in feed_dir.glob("*.json")
                if p.name != "latest.json" and not p.name.startswith("_")
            ),
            reverse=True,
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

    def _read_feed_file(
        self, path: Path
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
        """读一份 feed → (items, errors); 文件坏了返回 ``None``。"""
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.error("[bank-feed] cannot read %s: %s", path.name, exc)
            return None
        if not isinstance(payload, dict):
            logger.error(
                "[bank-feed] %s: unexpected payload type %s",
                path.name, type(payload).__name__,
            )
            return None
        items = payload.get("items")
        if not isinstance(items, list):
            logger.error("[bank-feed] %s: no items array", path.name)
            return None
        errors = payload.get("errors")
        return (
            [it for it in items if isinstance(it, dict)],
            [e for e in errors if isinstance(e, dict)] if isinstance(errors, list) else [],
        )

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
        self._today_errors = []
        self._bot_error = ""
        targets = self._load_targets(cfg)
        self._target_labels = {
            str(t.get("id")): self._describe_target(t)
            for t in targets
            if t.get("id")
        }

        if not self._bound_tags():
            # 没有绑定 tag 就必然零 tag; 宁可不入库也不要制造无 tag 文章。
            raise RuntimeError(
                "bank-feed: no tag bound to this source — refusing to ingest "
                "untagged articles"
            )

        if self._should_run_bot(cfg):
            if not cfg.get("bot_enabled", True):
                if not self._today_feed_exists():
                    raise RuntimeError(
                        "bank-feed: no feed for today and bot_enabled is off"
                    )
                # 有当天 feed (哪怕不完整) 就先导入, 不因为关掉 bot 丢数据。
            else:
                if not targets:
                    raise RuntimeError(
                        "bank-feed: no targets configured — cannot run the agent"
                    )
                try:
                    self._run_bot(cfg, targets)
                except RuntimeError as exc:
                    if not self._today_feed_exists():
                        raise
                    # 补跑失败但盘上有（不完整的）feed: 先把已到手的数据入库,
                    # 再把失败报上去。整轮中止只会让这些数据一直进不来。
                    logger.error("[bank-feed] bot retry failed, importing "
                                 "existing partial feed: %s", exc)
                    self._bot_error = str(exc)

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
        today = date.today().isoformat()
        for path in files:
            loaded = self._read_feed_file(path)
            if loaded is None:
                if path.stem == today:
                    # 今天的 feed 坏了 = 今天的数据不可信。历史文件坏了只
                    # 影响补录, 不能让整轮抓取失败。
                    raise RuntimeError(
                        f"bank-feed: today's feed {path.name} is unreadable"
                    )
                continue
            items, feed_errors = loaded
            if path.stem == today:
                self._today_errors = feed_errors
                self._today_items = len(items)
            for item in items:
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
        self._skipped_existing = skipped_already
        logger.info(
            "[bank-feed] %d feed file(s) → %d new raw item(s) (%d already imported)",
            len(files), len(raws), skipped_already,
        )
        self._progress_total = len(raws)
        self._progress_inserted = 0
        self._progress_done = 0
        self._report_progress(
            total_feeds=len(raws),
            done_feeds=0,
            articles_so_far=0,
            current_feed=f"bank_feed {self._today_feed_name()}",
        )
        return raws

    def _today_feed_name(self) -> str:
        return f"{date.today().isoformat()}.json"

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
        if bank and not _IDENT_RE.fullmatch(bank):
            logger.warning("[bank-feed] invalid bank code %r — dropped from metadata", bank)
            bank = ""
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
        if target_id and not _IDENT_RE.fullmatch(target_id):
            logger.warning(
                "[bank-feed] invalid target_id %r — dropped from metadata", target_id
            )
            target_id = ""
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
    def _report_progress(self, **fields: Any) -> None:
        """把进度报给 fetch_jobs; 回调异常不能影响抓取本身。"""
        if self.progress_callback is None:
            return
        try:
            self.progress_callback(**fields)
        except TimeoutError:
            # web 端用 TimeoutError 中止超时抓取, 吞掉它会让已经超时的
            # 任务继续跑完才被标 timeout。
            raise
        except Exception as exc:  # noqa: BLE001
            logger.debug("[bank-feed] progress callback failed: %s", exc)

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
        bound_tags = self._bound_tags()
        if articles:
            if not bound_tags:
                # fetch() 已经拦过一次; 走到这里说明 tag 在抓取过程中被删了。
                # 抛错让 base._ingest 把它写进 result["error"], 不要悄悄入库。
                raise RuntimeError(
                    "bank-feed: tag binding disappeared mid-run — "
                    "refusing to insert untagged articles"
                )
            keyword_hits = _with_bound_tag_hits(
                articles, list(keyword_hits or []), bound_tags
            )
        inserted = super().dedupe_insert(articles, keyword_hits)
        self._inserted_ok = True
        self._progress_done += len(articles)
        self._progress_inserted += inserted
        self._report_progress(
            done_feeds=self._progress_done,
            articles_so_far=self._progress_inserted,
        )
        # 只记**确实在库里**的 item: 基类对单条 INSERT 异常只 warning,
        # 先记账会把没入库的条目永久标成已导入。
        for art in articles:
            item_id = str(art.get("bank_item_id") or "").strip()
            if item_id and self._article_exists(art):
                self._round_ids.append(item_id)
        return inserted

    def _article_exists(self, art: dict[str, Any]) -> bool:
        try:
            from ...db.connection import get_db

            row = get_db().execute(
                "SELECT 1 FROM articles WHERE source = ? AND url = ? LIMIT 1",
                (self.source_name, str(art.get("url") or "")),
            ).fetchone()
            return row is not None
        except Exception as exc:  # noqa: BLE001
            logger.warning("[bank-feed] cannot verify insert: %s", exc)
            # 查不到就把「已导入」记上 —— 宁可少记一次重试, 也不要在
            # 数据库不可用时把整轮 item 反复重放。
            return True

    # ── run ───────────────────────────────────────────────────────────────
    def run(self, dry_run: bool = False) -> dict[str, Any]:
        """跑完管线后落导入状态 (入库成功才记已导入)。"""
        self._inserted_ok = False
        self._pending_state = None
        self._round_ids = []
        result = super().run(dry_run=dry_run)
        result["raw_items"] = self._today_items
        result["skipped_existing"] = self._skipped_existing
        if not dry_run and self._inserted_ok:
            # 部分失败也要落 state: 已入库的条目不该在重跑时再处理一遍。
            self._flush_state()
        if not dry_run and not result.get("error"):
            problems: list[str] = []
            if self._bot_error:
                problems.append(f"补抓失败：{_human_reason(self._bot_error)}")
            if self._today_errors:
                failed = "、".join(
                    f"{self._target_label(str(e.get('target') or '?'))}"
                    f"（{_human_reason(str(e.get('error') or ''))}）"
                    for e in self._today_errors[:3]
                )
                problems.append(
                    f"{len(self._today_errors)} 个来源未抓到：{failed}"
                )
                result["failed_targets_detail"] = [
                    {
                        "target": str(e.get("target") or "?"),
                        "label": self._target_label(str(e.get("target") or "")),
                        "reason": _human_reason(str(e.get("error") or "")),
                        "error": str(e.get("error") or ""),
                    }
                    for e in self._today_errors
                ]
            if problems:
                text = "; ".join(problems)
                # 「今天有数据」比「本轮新插了几条」更能说明这次抓取有效:
                # 同一天点第二次时 inserted 自然是 0, 但库里的文章是真的。
                if int(result.get("inserted") or 0) > 0 or self._today_items > 0:
                    result["warning"] = text
                    result["failed_targets"] = sorted({
                        str(e.get("target") or "?") for e in self._today_errors
                    })
                else:
                    result["error"] = text
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
