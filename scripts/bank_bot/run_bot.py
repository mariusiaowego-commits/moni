#!/usr/bin/env python3
"""bank_bot — 银行同业情报抓取 bot (sprint-40 / v1.0.76).

把「五大行 + 同业的资讯」抓成一份 moni 能直接导入的 JSON feed:

    data/bank_feed/YYYY-MM-DD.json
    data/bank_feed/latest.json

两段式, 故意把确定性的部分和需要判断的部分分开:

1. **确定性段** (``media_rss`` / ``media_json`` 目标): 纯 Python 拉 RSS /
   JSON 列表 → 按银行主体词筛 → 产出候选条目。不烧模型、不猜。
2. **agent 段** (``official_list`` / ``wechat_article`` 目标): 交给
   ``hermes --profile moni`` 的浏览器 / 搜索能力去渲染官网栏目和公众号
   正文, 产出严格 JSON。moni 侧不持任何模型凭证 —— 这一跳和 moni 现有
   的 LLM 链路是同一条 (hermes → magpie)。

为什么不写死选择器
------------------
五大行官网的结构各不相同且改版频繁。把选择器写进代码等于给自己埋一个
每月都要修的坑; 让 agent 每次自己看页面结构, 改版时改的是提示词而不是
代码。

失败语义
--------
**抓不到 ≠ 今天没新闻。** 单个目标失败记进 ``errors``; 全部失败且 0 条
时以 2 退出, 并且**不写当天的 feed** —— 一份「0 条的当天 feed」会让
moni 以为今天真的没有同业新闻, 然后一整天不再重试。

用法
----
    echo '{"targets": [...]}' | run_bot.py --output-dir data/bank_feed
    run_bot.py --output-dir data/bank_feed --targets config/bank_targets.json
    run_bot.py --output-dir data/bank_feed --no-agent      # 只跑确定性段
    run_bot.py --validate --output-dir data/bank_feed      # 校验已有 feed
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # 复用 moni 的唯一关键词引擎
    sys.path.insert(0, str(REPO_ROOT))

from moni.fetch.keyword_match import keyword_matches  # noqa: E402

GENERATOR = "bank_bot/1.0"
DEFAULT_TARGETS_FILE = REPO_ROOT / "config" / "bank_targets.json"
KNOWN_KINDS = ("official_list", "media_rss", "media_json", "wechat_article")
LOCAL_TZ = datetime.now().astimezone().tzinfo

#: 银行代码 → 中文名 + 关键词。关键词匹配统一走 moni 的唯一引擎
#: (``moni/fetch/keyword_match.py``) 的短 CJK 规则: 两字简称要命中标题,
#: 长全称正文任意位置即可。**不在这里另写一套匹配逻辑。**
BANK_ALIASES: dict[str, dict[str, Any]] = {
    "icbc": {
        "name": "工商银行",
        "keywords": ["工商银行", "中国工商银行", "工行", "ICBC"],
    },
    "abc": {
        "name": "农业银行",
        "keywords": ["农业银行", "中国农业银行", "农行"],
    },
    "boc": {
        "name": "中国银行",
        "keywords": ["中国银行", "中行"],
    },
    "ccb": {
        "name": "建设银行",
        "keywords": ["建设银行", "中国建设银行", "建行", "CCB"],
    },
    "psbc": {
        "name": "邮储银行",
        "keywords": ["邮储银行", "中国邮政储蓄银行", "邮储", "PSBC"],
    },
    "bocom": {
        "name": "交通银行",
        "keywords": ["交通银行", "交行"],
    },
}

#: 单个目标最多取多少条
DEFAULT_MAX_ITEMS = 12

#: 正文低于这个字数的条目仍然入库, 但会被案例库抽取跳过。只做记录。
MIN_USEFUL_TEXT = 100

ITEM_FIELDS = ("id", "url", "title", "bank", "source_type", "publisher")


class BotError(RuntimeError):
    """fatal — 整轮跑不下去。"""


class TargetError(RuntimeError):
    """单个目标失败 — 记进 errors, 不拖垮整轮。"""


# ── 基础工具 ────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _strip_html(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_date(raw: Any) -> str | None:
    """把各种日期写法归一成 ISO 8601 (带时区)。失败返回 None。"""
    if raw in (None, ""):
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        try:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(raw).strip()
    if not text:
        return None
    for cand in (text, text.replace("Z", "+00:00"), text.replace("/", "-")):
        try:
            parsed = datetime.fromisoformat(cand)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=LOCAL_TZ)
        return parsed.isoformat()
    try:  # RSS 2.0 的 RFC 822 格式: Thu, 12 Nov 2026 15:55:29 +0800
        return parsedate_to_datetime(text).isoformat()
    except (TypeError, ValueError):
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d", "%Y年%m月%d日"):
        try:
            parsed = datetime.strptime(text[: len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
        return parsed.replace(tzinfo=timezone.utc).isoformat()
    return None


def detect_banks(title: str, body: str) -> list[str]:
    """标题/正文里点名了哪几家行。"""
    hits: list[str] = []
    for code, meta in BANK_ALIASES.items():
        for kw in meta["keywords"]:
            if keyword_matches(
                kw, title=title or "", body=body or "", field="title+content"
            ):
                hits.append(code)
                break
    return hits


def make_item_id(bank: str, url: str) -> str:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return f"{bank}-{digest}"


# ── 目标清单 ────────────────────────────────────────────────────────────────

def load_targets(path: Path | None, stdin_payload: str | None) -> list[dict[str, Any]]:
    """targets 来源优先级: stdin (fetcher 传的) > --targets 文件 > 仓库默认。"""
    payload: Any = None
    if stdin_payload and stdin_payload.strip():
        try:
            payload = json.loads(stdin_payload)
        except ValueError as exc:
            raise BotError(f"stdin targets is not valid JSON: {exc}") from exc
    else:
        target_file = path or DEFAULT_TARGETS_FILE
        try:
            payload = json.loads(Path(target_file).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BotError(f"cannot read targets file {target_file}: {exc}") from exc

    raw = payload.get("targets") if isinstance(payload, dict) else payload
    if not isinstance(raw, list):
        raise BotError("targets payload must be a list or an object with 'targets'")
    return normalize_targets(raw)


def normalize_targets(raw: list[Any]) -> list[dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        target_id = str(entry.get("id") or "").strip()
        url = str(entry.get("url") or "").strip()
        kind = str(entry.get("kind") or "").strip()
        if not target_id or not url:
            raise BotError(f"target needs id + url: {entry!r}")
        if kind not in KNOWN_KINDS:
            raise BotError(
                f"target {target_id}: unknown kind {kind!r} "
                f"(allowed: {', '.join(KNOWN_KINDS)})"
            )
        if target_id in seen:
            continue
        seen.add(target_id)

        bank = str(entry.get("bank") or "").strip() or None
        if bank and bank not in BANK_ALIASES:
            raise BotError(
                f"target {target_id}: unknown bank {bank!r} "
                f"(allowed: {', '.join(BANK_ALIASES)})"
            )
        targets.append({
            **entry,
            "id": target_id,
            "url": url,
            "kind": kind,
            "bank": bank,
            "publisher": str(entry.get("publisher") or "").strip() or target_id,
            "max_items": int(entry.get("max_items") or DEFAULT_MAX_ITEMS),
        })
    if not targets:
        raise BotError("no usable targets")
    return targets


# ── 确定性段: media_rss / media_json ───────────────────────────────────────

def _http_get(url: str, timeout: float) -> bytes:
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - httpx 是 moni 的硬依赖
        raise TargetError(f"httpx unavailable: {exc}") from exc
    try:
        resp = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "(compatible; moni-bank-bot/1.0)"
                )
            },
        )
    except Exception as exc:  # noqa: BLE001 — httpx 异常层级不稳定
        raise TargetError(f"GET failed: {exc}") from exc
    if resp.status_code != 200:
        raise TargetError(f"GET {url} -> HTTP {resp.status_code}")
    return resp.content


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _first_text(node: ET.Element, names: tuple[str, ...]) -> str:
    for child in node:
        if _localname(child.tag) in names:
            return (child.text or "").strip()
    return ""


def parse_rss(payload: bytes) -> list[dict[str, str]]:
    """RSS 2.0 / Atom → [{title, url, summary, date}]。"""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise TargetError(f"invalid XML: {exc}") from exc

    entries: list[dict[str, str]] = []
    for node in root.iter():
        if _localname(node.tag) not in ("item", "entry"):
            continue
        link = _first_text(node, ("link",))
        if not link:
            for child in node:
                local = _localname(child.tag)
                if local == "link" and (child.get("href") or "").strip():
                    link = (child.get("href") or "").strip()
                    break
                if local == "guid" and (child.text or "").strip():
                    link = (child.text or "").strip()
        entries.append({
            "title": _first_text(node, ("title",)),
            "url": link,
            "summary": _strip_html(
                _first_text(node, ("description", "summary", "content"))
            ),
            "date": _first_text(node, ("pubdate", "published", "updated", "date")),
        })
    return entries


def parse_json_list(payload: bytes, fields: dict[str, str],
                    items_path: str, base_url: str) -> list[dict[str, str]]:
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise TargetError(f"invalid JSON: {exc}") from exc

    node: Any = data
    for part in [p for p in (items_path or "").split(".") if p]:
        node = node.get(part) if isinstance(node, dict) else None
    if not isinstance(node, list):
        raise TargetError(f"items_path {items_path!r} did not yield a list")

    entries: list[dict[str, str]] = []
    for row in node:
        if not isinstance(row, dict):
            continue
        link = str(row.get(fields.get("url", "url")) or "").strip()
        if link.startswith("/"):
            link = base_url.rstrip("/") + link
        entries.append({
            "title": str(row.get(fields.get("title", "title")) or "").strip(),
            "url": link,
            "summary": _strip_html(
                str(row.get(fields.get("summary", "summary")) or "")
            ),
            "date": str(row.get(fields.get("date", "date")) or "").strip(),
        })
    return entries


def collect_media(target: dict[str, Any], *, timeout: float) -> list[dict[str, Any]]:
    payload = _http_get(target["url"], timeout)
    if target["kind"] == "media_rss":
        entries = parse_rss(payload)
    else:
        fields = target.get("fields") if isinstance(target.get("fields"), dict) else {}
        entries = parse_json_list(
            payload,
            fields,
            str(target.get("items_path") or ""),
            str(target.get("base_url") or ""),
        )

    items: list[dict[str, Any]] = []
    for entry in entries:
        title, body = entry["title"], entry["summary"]
        if not title or not entry["url"]:
            continue
        # 只认**标题**里点名的银行。媒体 RSS 的正文经常是「今日 A 股收评」,
        # 正文里一句提到工行不代表这条是同业新闻 —— 实测按正文归属时
        # 东财的三条大盘复盘全被记成工行。银行新闻的标题几乎必然点名,
        # 所以标题归属召回不掉, 精度高得多。
        banks = detect_banks(title, "")
        if not banks:
            continue
        # 一条新闻只产出一条 item: moni 的文章 id 由 source+url+title 决定,
        # 同 url 的多条会在入库时被 IGNORE 掉, 多出来的只是噪音。
        # 多条行名同时出现时取第一家主行, 其余在标题里可读。
        items.append({
            "id": make_item_id(banks[0], entry["url"]),
            "url": entry["url"],
            "title": title,
            "text": body,
            "created_at": _parse_date(entry["date"]),
            "bank": banks[0],
            "source_type": "media",
            "publisher": target["publisher"],
            "target_id": target["id"],
        })
    return items[: target["max_items"]]


# ── agent 段: official_list / wechat_article ───────────────────────────────

_AGENT_PROMPT = """你是银行同业情报抓取器。今天是 {today}。

任务：打开下面这个信源页面，抓取**它最新的银行资讯条目**（不是公告、
不是招标采购、不是人才招聘、不是利率牌价），为每条产出一条记录。

信源：
{target_block}

严格要求：
1. 只输出一个 JSON 数组，不要任何解释文字，不要 Markdown 代码围栏。
2. 数组元素字段（全部必需）：
   - "title": 条目标题原文
   - "url": 条目详情页的**绝对** URL
   - "created_at": 发布日期，格式 YYYY-MM-DD；页面只有年月就用该月 1 日
   - "text": 正文或导语，**至少 100 个汉字**（不足 100 字就打开详情页取正文）
   - "bank": 该信源的 bank 代码（见上面每个信源给的 bank）
3. 最多 {max_items} 条，只取最新的。
4. 页面打不开或结构看不懂时，改用搜索确认该行最近 7 天的新闻，但 URL 必须是
   真实可访问的页面。仍然拿不到就跳过这个信源，不要编造。
5. 严禁编造标题、日期、链接。拿不到就少给几条。

现在输出 JSON 数组："""


def _hermes_bin() -> str:
    return os.environ.get("MONI_HERMES_BIN", "hermes")


def call_agent(prompt: str, *, timeout: int, profile: str) -> str:
    """调 hermes (argv 传 prompt, 与 moni.llm.client 同一条链路)。"""
    if len(prompt.encode("utf-8")) > 200_000:
        raise BotError("agent prompt exceeds 200KB argv limit")
    cmd = [_hermes_bin(), "-z", prompt, "--profile", profile]
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            # hermes 会拉起浏览器子进程; 自成进程组才能在超时时整组杀掉,
            # 否则孙进程占着管道, communicate() 等到天荒地老。
            start_new_session=True,
        )
    except OSError as exc:
        raise TargetError(f"cannot start agent ({cmd[0]}): {exc}") from exc
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc.pid)
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            print("[bank_bot] stdout pipe still held after SIGKILL", file=sys.stderr)
        raise TargetError(f"agent timed out after {timeout}s") from None
    if proc.returncode != 0:
        tail = (stderr or stdout or "").strip().splitlines()[-3:]
        raise TargetError(f"agent exited {proc.returncode}: " + " | ".join(tail))
    return stdout or ""


def _kill_group(pid: int, grace: float = 5.0) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(pid), sig)
        except ProcessLookupError:
            return
        except OSError:
            print(f"[bank_bot] killpg({pid}, {sig}) failed", file=sys.stderr)
        if sig is signal.SIGTERM:
            time.sleep(grace)


def parse_agent_json(stdout: str) -> list[dict[str, Any]]:
    text = (stdout or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        raise TargetError("agent output contains no JSON array")
    try:
        data = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise TargetError(f"agent JSON is invalid: {exc}") from exc
    if not isinstance(data, list):
        raise TargetError("agent JSON is not an array")
    return [row for row in data if isinstance(row, dict)]


def collect_agent(
    targets: list[dict[str, Any]],
    *,
    today: str,
    timeout: int,
    profile: str,
    budget: int = 0,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """官方栏目 + 公众号: 交给 agent 渲染/抽取。

    **一个目标一次调用**。早先的实现把 5 个官网塞进一次 hermes 调用, 实测
    600s 都没跑完 (浏览器逐页渲染 5 个 SPA), 结果 5 个官网全丢。拆开之后
    每个目标有自己的超时和失败记录, 一个页面卡住不拖垮其余四个。
    """
    deadline = time.monotonic() + budget if budget > 0 else None
    items: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    for target in targets:
        per_call = timeout
        if deadline is not None:
            remaining = int(deadline - time.monotonic())
            if remaining <= 10:
                failures.append({
                    "target": target["id"],
                    "error": "agent budget exhausted before this target",
                })
                continue
            per_call = min(timeout, remaining)

        started = time.monotonic()
        try:
            rows = parse_agent_json(
                call_agent(
                    _target_prompt(target, today),
                    timeout=per_call,
                    profile=profile,
                )
            )
        except TargetError as exc:
            failures.append({"target": target["id"], "error": str(exc)})
            print(
                f"[bank_bot] agent {target['id']}: {exc} "
                f"({time.monotonic() - started:.0f}s)",
                file=sys.stderr,
            )
            continue

        found = _rows_to_items(rows, target)
        items.extend(found)
        print(
            f"[bank_bot] agent {target['id']}: {len(found)} item(s) "
            f"({time.monotonic() - started:.0f}s)"
        )
    return items, failures


def _target_block(target: dict[str, Any]) -> str:
    bank_name = BANK_ALIASES[target["bank"]]["name"] if target["bank"] else "任意"
    block = (
        f"- bank={target['bank'] or ''} ({bank_name}) "
        f"名称={target['publisher']}\n"
        f"  类型={target['kind']}\n"
        f"  URL={target['url']}"
    )
    if target.get("hint"):
        block += f"\n  提示={target['hint']}"
    return block


def _target_prompt(target: dict[str, Any], today: str) -> str:
    return _AGENT_PROMPT.format(
        today=today,
        target_block=_target_block(target),
        max_items=target["max_items"],
    )


def _rows_to_items(rows: list[dict[str, Any]],
                   target: dict[str, Any]) -> list[dict[str, Any]]:
    """agent 的行 → 规范 item。bank 缺失时按标题/正文反推。"""
    items: list[dict[str, Any]] = []
    for row in rows:
        title = str(row.get("title") or "").strip()
        url = str(row.get("url") or "").strip()
        text = _strip_html(str(row.get("text") or ""))
        if not title or not url:
            continue
        bank = str(row.get("bank") or "").strip()
        if bank not in BANK_ALIASES:
            detected = detect_banks(title, text)
            bank = detected[0] if detected else (target["bank"] or "")
        if not bank:
            continue
        items.append({
            "id": make_item_id(bank, url),
            "url": url,
            "title": title,
            "text": text,
            "created_at": _parse_date(row.get("created_at")),
            "bank": bank,
            "source_type": "official" if target["kind"] == "official_list" else "wechat",
            "publisher": str(row.get("publisher") or "").strip() or target["publisher"],
            "target_id": target["id"],
        })
    return items[: int(target.get("max_items") or DEFAULT_MAX_ITEMS)]


# ── 合并 / 落盘 / 校验 ─────────────────────────────────────────────────────

def item_problems(item: Any, idx: int) -> list[str]:
    problems: list[str] = []
    if not isinstance(item, dict):
        return [f"items[{idx}] is not an object"]
    for field in ITEM_FIELDS:
        if not str(item.get(field) or "").strip():
            problems.append(f"items[{idx}].{field} is empty")
    if item.get("bank") not in BANK_ALIASES:
        problems.append(f"items[{idx}].bank is unknown")
    if item.get("source_type") not in ("official", "media", "wechat"):
        problems.append(f"items[{idx}].source_type is not official|media|wechat")
    url = str(item.get("url") or "")
    if url and not url.startswith(("http://", "https://")):
        problems.append(f"items[{idx}].url is not absolute")
    if len(str(item.get("title") or "")) > 300:
        problems.append(f"items[{idx}].title is longer than 300 chars")
    if len(str(item.get("text") or "")) > 20_000:
        problems.append(f"items[{idx}].text is longer than 20000 chars")
    return problems


def validate_items(items: list[Any]) -> list[str]:
    problems: list[str] = []
    for idx, item in enumerate(items):
        problems.extend(item_problems(item, idx))
    return problems


def merge_items(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 url / id 去重 (同一条可能被多个目标命中), 保留首次出现的。"""
    merged: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_ids: set[str] = set()
    for group in groups:
        for item in group:
            url = str(item.get("url") or "")
            item_id = str(item.get("id") or "")
            if url in seen_urls or item_id in seen_ids:
                continue
            seen_urls.add(url)
            seen_ids.add(item_id)
            merged.append(item)
    merged.sort(key=lambda it: str(it.get("created_at") or ""), reverse=True)
    return merged


def build_payload(today: str, items: list[dict[str, Any]],
                  errors: list[dict[str, str]], targets_total: int) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item["bank"]] = counts.get(item["bank"], 0) + 1
    return {
        "date": today,
        "generated_at": _now_iso(),
        "generator": GENERATOR,
        "targets_total": targets_total,
        "counts_by_bank": counts,
        "errors": errors,
        "items": items,
    }


def write_feed(output_dir: Path, payload: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    paths = [
        output_dir / f"{payload['date']}.json",
        output_dir / "latest.json",
    ]
    for path in paths:
        fd, tmp_name = tempfile.mkstemp(dir=str(output_dir), prefix=".tmp-feed-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(body)
            os.replace(tmp_name, path)
        except OSError as exc:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise BotError(f"cannot write {path}: {exc}") from exc
    return paths[0]


# ── CLI ────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bank_bot", description="银行同业情报抓取 bot")
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "data" / "bank_feed"))
    parser.add_argument("--targets", default=None,
                        help="目标清单 JSON (默认 config/bank_targets.json)")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--http-timeout", type=float, default=25.0)
    # 单个目标 (一个官网栏目 / 一篇公众号) 的 agent 预算。实测浏览器渲染
    # 一个官网栏目 30~120s, 240s 留足余量。
    parser.add_argument("--agent-timeout", type=int, default=240)
    # agent 段总预算 —— 必须小于 fetcher 的 bot_timeout_sec, 否则 bot 会被
    # 上游直接 kill 掉, 连已经抓到的条目都来不及落盘。
    parser.add_argument("--agent-budget", type=int, default=1200)
    parser.add_argument("--profile", default=os.environ.get("MONI_HERMES_PROFILE", "moni"))
    parser.add_argument("--no-agent", action="store_true",
                        help="只跑确定性段 (media_rss/media_json)")
    parser.add_argument("--validate", action="store_true",
                        help="校验 --output-dir 下已有的 feed")
    return parser


def _validate_existing(output_dir: Path) -> int:
    files = sorted(p for p in output_dir.glob("*.json") if not p.name.startswith("."))
    if not files:
        print(f"[validate] no feed files under {output_dir}", file=sys.stderr)
        return 2
    bad = 0
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"[validate] {path.name}: unreadable ({exc})", file=sys.stderr)
            bad += 1
            continue
        items = payload.get("items") if isinstance(payload, dict) else None
        problems = validate_items(items) if isinstance(items, list) else [
            "payload has no items array"
        ]
        if problems:
            bad += 1
            print(f"[validate] {path.name}: {len(problems)} problem(s)", file=sys.stderr)
            for problem in problems[:10]:
                print(f"  - {problem}", file=sys.stderr)
        else:
            print(f"[validate] {path.name}: OK ({len(items)} items)")
    return 0 if bad == 0 else 2


def _read_stdin_payload() -> str | None:
    if not sys.stdin or sys.stdin.isatty():
        return None
    try:
        return sys.stdin.read()
    except OSError:
        return None


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    output_dir = Path(args.output_dir)

    if args.validate:
        return _validate_existing(output_dir)

    try:
        targets = load_targets(
            Path(args.targets) if args.targets else None, _read_stdin_payload()
        )
    except BotError as exc:
        print(f"[bank_bot] {exc}", file=sys.stderr)
        return 3

    media_targets = [t for t in targets if t["kind"] in ("media_rss", "media_json")]
    agent_targets = [
        t for t in targets
        if t["kind"] in ("official_list", "wechat_article") and not args.no_agent
    ]

    errors: list[dict[str, str]] = []
    media_items: list[dict[str, Any]] = []
    for target in media_targets:
        try:
            found = collect_media(target, timeout=args.http_timeout)
        except TargetError as exc:
            errors.append({"target": target["id"], "error": str(exc)})
            print(f"[bank_bot] {target['id']}: {exc}", file=sys.stderr)
            continue
        media_items.extend(found)
        print(f"[bank_bot] {target['id']}: {len(found)} item(s)")

    agent_items: list[dict[str, Any]] = []
    if agent_targets:
        try:
            agent_items, agent_failures = collect_agent(
                agent_targets,
                today=args.date,
                timeout=args.agent_timeout,
                profile=args.profile,
                budget=args.agent_budget,
            )
            errors.extend(agent_failures)
            print(f"[bank_bot] agent: {len(agent_items)} item(s)")
        except BotError as exc:
            errors.append({"target": "agent", "error": str(exc)})
            print(f"[bank_bot] agent: {exc}", file=sys.stderr)

    merged = merge_items(agent_items, media_items)
    items: list[dict[str, Any]] = []
    problems: list[str] = []
    for idx, item in enumerate(merged):
        item_issues = item_problems(item, idx)
        if item_issues:
            problems.extend(item_issues)
            continue
        items.append(item)
    if problems:
        print(
            f"[bank_bot] dropped {len(merged) - len(items)} item(s) failing schema",
            file=sys.stderr,
        )
        for problem in problems[:10]:
            print(f"  - {problem}", file=sys.stderr)

    if not items:
        print(
            f"[bank_bot] no item captured ({len(errors)} error(s)) — "
            "refusing to write an empty feed (0 条 ≠ 今天没新闻)",
            file=sys.stderr,
        )
        return 2

    payload = build_payload(args.date, items, errors, len(targets))
    path = write_feed(output_dir, payload)
    short_text = sum(1 for it in items if len(it.get("text") or "") < MIN_USEFUL_TEXT)
    print(
        f"[bank_bot] wrote {path} — {len(items)} item(s) "
        f"({short_text} below {MIN_USEFUL_TEXT} chars), {len(errors)} error(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
