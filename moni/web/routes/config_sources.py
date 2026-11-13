"""routes/config_sources — CRUD for sources + source_tags (v1.0.2).

Sources are the canonical replacement for config/sources.yaml. Each source
can be associated with one or more tag_definitions via the source_tags
M:N table.
"""
from __future__ import annotations

import copy
import json as _json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter

from moni.db.connection import get_db
# v1.0.66 审计 B5: xbot_feed 的 fetch_config 默认值只有一个来源 ——
# moni/fetch/sources/xbot_feed.py 的 DEFAULT_FETCH_CONFIG。
# 循环 import 风险: xbot_feed.py 只在**函数内** import moni.db.*,
# 模块层不 import moni.db → 这里从 moni.fetch 拉常量不会形成环。
from moni.fetch.sources.xbot_feed import (
    DEFAULT_FETCH_CONFIG as XBOT_FEED_DEFAULT_FETCH_CONFIG,
)
# v1.0.71 (sprint-35): 门槛与 scope 的默认值是**独立常量** (方案 A) ——
# 不进 DEFAULT_FETCH_CONFIG (那是 migration 039 字面量的对照物)。
from moni.fetch.sources.xbot_feed import (
    DEFAULT_SCOPE as XBOT_FEED_DEFAULT_SCOPE,
    DEFAULT_THRESHOLDS as XBOT_FEED_DEFAULT_THRESHOLDS,
)
# v1.0.76: bank_feed 同样只认 bank_feed.py 里的 DEFAULT_FETCH_CONFIG。
from moni.fetch.sources.bank_feed import (
    DEFAULT_FETCH_CONFIG as BANK_FEED_DEFAULT_FETCH_CONFIG,
)
from moni.web.exceptions import (
    BadRequestError,
    ConflictError,
    NotFoundError,
)
from moni.web.models import (
    FetchConfigResponse,
    ScopeResponse,
    Source,
    SourceCreate,
    SourceUpdate,
    SourceWithTags,
)
from moni.web.redact import is_masked_url, sanitize_source_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config/sources", tags=["config"])
# Known fetch_config defaults per source (for UI pre-fill + merge)
# v1.0.12: include xwatch so empty/partial rows still show documented keys.
_FETCH_CONFIG_DEFAULTS: dict[str, dict] = {
    "bestblogs": {
        "max_feeds": 375,
        "max_articles_per_feed": 5,
        "max_articles_total": 500,
        "request_delay": 0.5,
        "timeout_sec": 1800,
    },
    "bestblog-podcast": {
        "max_feeds": 60,
        "max_articles_per_feed": 5,
        "max_articles_total": 100,
        "request_delay": 0.5,
        "timeout_sec": 1800,
    },
    "bestblog-x": {
        "max_feeds": 160,
        "max_articles_per_feed": 5,
        "max_articles_total": 200,
        "request_delay": 0.5,
        "timeout_sec": 1800,
    },
    "bestblog-yt": {
        "max_feeds": 20,
        "max_articles_per_feed": 5,
        "max_articles_total": 50,
        "request_delay": 0.5,
        "timeout_sec": 1800,
    },
    "xwatch": {
        "following_enabled": True,
        "following_max_posts": 100,
        "following_max_scrolls": 40,
        "following_scroll_pause": 1.8,
        "following_skip_syndication": True,
        "following_stop_chrome": True,
        "accounts": [],
        "accounts_per_run": 5,
        "max_results_per_account": 5,
        "account_rss": {},
        "account_ddg_fallback": True,
        "queries": [],
        "queries_per_run": 1,
        "max_results_per_query": 10,
        "request_delay": 8.0,
        "search_timeout": 20,
        "syndication_timeout": 20,
        "ddg_circuit_minutes": 60.0,
        "skip_existing_urls": True,
    },
    "follow_builders": {
        "bypass_keyword_filters": True,
        "timeout_sec": 60,
        "http_timeout": 15.0,
        "skip_existing_urls": True,
    },
    "chinese_independent_blogs": {
        "max_feeds": 80,
        "max_articles_per_feed": 3,
        "max_articles_total": 240,
        "request_delay": 0.35,
        "http_timeout": 12.0,
        "min_feeds_before_failure_rate_check": 10,
        "max_failure_rate": 0.7,
        "timeout_sec": 1200,
    },
    # v1.0.66: xbot_feed — X-bot 精选桥接源。私有仓库的 clone 用机器上
    # 已有的 git 凭据, moni 不存 key。
    # v1.0.66 审计 B5: 默认值统一引用 moni.fetch.sources.xbot_feed 的
    # DEFAULT_FETCH_CONFIG —— 不再在本文件里抄一遍 (三处抄本会漂移)。
    "xbot_feed": copy.deepcopy(XBOT_FEED_DEFAULT_FETCH_CONFIG),
    # v1.0.76 / sprint-40: bank_feed — 目标是用户可直接编辑的 fetch_config
    # 键 (targets)，加信源零代码。
    "bank_feed": copy.deepcopy(BANK_FEED_DEFAULT_FETCH_CONFIG),
}

_RETIRED_FETCH_CONFIG_KEYS = {"consecutive_failure_limit"}


# ── v1.0.71 (sprint-35): X-bot 精选的门槛与 scope ────────────────────────
#
# 这两个键是**独立常量** (方案 A): 不进 ``DEFAULT_FETCH_CONFIG``, 所以
# ``_FETCH_CONFIG_DEFAULTS["xbot_feed"]`` 仍逐字等于 DEFAULT_FETCH_CONFIG
# (``tests/test_xbot_feed_fetch.py::test_fetch_config_has_single_source``
# 依赖这一点)。它们在**读取时**合并进响应。

#: 门槛认识的 topic —— 未知 topic 一律 400 (写进去也不会生效)。
_XBOT_THRESHOLD_TOPICS = ("wechat", "xcircle")

#: scope 里合法的键。
_XBOT_SCOPE_KEYS = ("xcircle_accounts", "wechat_regex", "wechat_queries")

#: 上游导出 scope 时, feed JSON 顶层用的键名。
_UPSTREAM_SCOPE_KEY = "scope"


def _validate_thresholds(raw: object) -> dict[str, dict[str, int]]:
    """校验 ``thresholds``; 不合法 → BadRequestError (400)。

    规则:
      * 必须是 dict;
      * topic 必须在 {wechat, xcircle} 里 (未知 topic → 400);
      * topic 的值必须是 dict;
      * ``min_followers`` / ``min_likes`` 必须是 **int** (显式排除 bool)
        且 >= 0。
    """
    if not isinstance(raw, dict):
        raise BadRequestError(
            message="thresholds must be an object with per-topic settings")
    out: dict[str, dict[str, int]] = {}
    for topic, cfg in raw.items():
        if topic not in _XBOT_THRESHOLD_TOPICS:
            raise BadRequestError(
                message=(
                    f"unknown threshold topic {topic!r} — "
                    f"allowed: {', '.join(_XBOT_THRESHOLD_TOPICS)}"
                )
            )
        if not isinstance(cfg, dict):
            raise BadRequestError(
                message=f"thresholds.{topic} must be an object")
        topic_out: dict[str, int] = {}
        for field in ("min_followers", "min_likes"):
            if field not in cfg:
                continue
            val = cfg[field]
            if isinstance(val, bool) or not isinstance(val, int):
                raise BadRequestError(
                    message=(
                        f"thresholds.{topic}.{field} must be a whole number "
                        f"(got {val!r})"
                    )
                )
            if val < 0:
                raise BadRequestError(
                    message=(
                        f"thresholds.{topic}.{field} must be >= 0 (got {val})"
                    )
                )
            topic_out[field] = val
        out[topic] = topic_out
    return out


def _validate_scope(raw: object) -> dict[str, object]:
    """校验 ``scope``; 不合法 → BadRequestError (400)。

    scope 是 moni 侧的**副本** (记录与展示用), 字段要求:
      * 必须是 dict;
      * ``xcircle_accounts`` / ``wechat_queries`` 必须是 list[str];
      * ``wechat_regex`` 必须是 str。
    """
    if not isinstance(raw, dict):
        raise BadRequestError(message="scope must be an object")
    out: dict[str, object] = {}
    for key, val in raw.items():
        if key not in _XBOT_SCOPE_KEYS:
            raise BadRequestError(
                message=(
                    f"unknown scope key {key!r} — "
                    f"allowed: {', '.join(_XBOT_SCOPE_KEYS)}"
                )
            )
        if key == "wechat_regex":
            if not isinstance(val, str):
                raise BadRequestError(
                    message="scope.wechat_regex must be a string")
            out[key] = val
            continue
        if not isinstance(val, list) or any(
            not isinstance(v, str) for v in val
        ):
            raise BadRequestError(
                message=f"scope.{key} must be a list of strings")
        # 去空白、去空串、去重 (保持顺序) —— 免得 UI 存进一堆空行。
        seen: set[str] = set()
        cleaned: list[str] = []
        for v in val:
            item = v.strip()
            if not item or item in seen:
                continue
            seen.add(item)
            cleaned.append(item)
        out[key] = cleaned
    return out


def _read_upstream_scope(cfg: dict) -> dict | None:
    """从**本地 clone** 的 feed JSON 里读上游导出的 scope (只读, 不 git pull)。

    上游目前没导出这个键 → 返回 None → 走 moni 侧兜底。

    注意: 这里**只读本地文件**, 绝不在 web 请求路径上做网络同步
    (git pull 是 fetcher 的活)。
    """
    import os
    from pathlib import Path

    env_dir = os.environ.get("MONI_DATA_DIR")
    data_dir = Path(env_dir) if env_dir else Path("data")
    configured = (cfg.get("clone_dir") or "").strip()
    if configured:
        clone_root = Path(configured)
        if not clone_root.is_absolute():
            clone_root = data_dir / clone_root
    else:
        clone_root = data_dir / "xbot-feed"
    feed_dir = clone_root / "feed"
    if not feed_dir.is_dir():
        return None
    try:
        files = sorted(feed_dir.glob("*.json"), reverse=True)
    except OSError:
        return None
    for path in files:
        try:
            payload = _json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        scope = payload.get(_UPSTREAM_SCOPE_KEY)
        if isinstance(scope, dict):
            return scope
    return None


def _effective_thresholds(user_cfg: dict) -> dict[str, dict[str, int]]:
    """把用户配的 thresholds 与默认值合并 (用户值优先)。"""
    merged = {
        topic: dict(cfg)
        for topic, cfg in XBOT_FEED_DEFAULT_THRESHOLDS.items()
    }
    raw = user_cfg.get("thresholds")
    if isinstance(raw, dict):
        for topic, cfg in raw.items():
            if topic not in merged or not isinstance(cfg, dict):
                continue
            for field, val in cfg.items():
                if field in ("min_followers", "min_likes") and isinstance(
                    val, int
                ) and not isinstance(val, bool) and val >= 0:
                    merged[topic][field] = val
    return merged


def _effective_scope(user_cfg: dict) -> tuple[dict, str, bool, bool]:
    """算出 (scope, scope_source, upstream_scope_available, scope_edited)。

    * ``scope`` —— **展示用**的范围:
      上游导出的 scope (权威) > moni 侧已存的副本 > DEFAULT_SCOPE 兜底;
    * ``scope_source`` —— **三态** (P1-1, 方案 b):
      ``upstream`` (上游导出了) > ``moni_stored`` (moni 侧有用户副本)
      > ``moni_fallback`` (兜底快照);
    * ``upstream_scope_available`` —— 上游 feed JSON 顶层有没有 scope 键;
    * ``scope_edited`` —— moni 侧存着用户编辑过的副本 (UI 徽标用)。
    """
    user_scope = user_cfg.get("scope")
    has_stored = isinstance(user_scope, dict) and bool(user_scope)

    upstream = _read_upstream_scope(user_cfg)
    upstream_available = upstream is not None

    if upstream_available:
        scope = dict(upstream)
        source = "upstream"
    elif has_stored:
        scope = dict(user_scope)  # type: ignore[arg-type]
        source = "moni_stored"
    else:
        scope = {
            "xcircle_accounts": list(
                XBOT_FEED_DEFAULT_SCOPE["xcircle_accounts"]),
            "wechat_regex": XBOT_FEED_DEFAULT_SCOPE["wechat_regex"],
            "wechat_queries": list(
                XBOT_FEED_DEFAULT_SCOPE["wechat_queries"]),
        }
        source = "moni_fallback"
    return scope, source, upstream_available, has_stored


def _moni_scope_copy(user_cfg: dict) -> dict:
    """moni 侧**可编辑**的 scope 副本 (用户存的, 没有就用兜底常量)。"""
    user_scope = user_cfg.get("scope")
    if isinstance(user_scope, dict) and user_scope:
        return dict(user_scope)
    return {
        "xcircle_accounts": list(
            XBOT_FEED_DEFAULT_SCOPE["xcircle_accounts"]),
        "wechat_regex": XBOT_FEED_DEFAULT_SCOPE["wechat_regex"],
        "wechat_queries": list(XBOT_FEED_DEFAULT_SCOPE["wechat_queries"]),
    }



def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_source(r) -> Source:
    fc = None
    if r["fetch_config"]:
        try:
            fc = _json.loads(r["fetch_config"])
        except (_json.JSONDecodeError, TypeError):
            fc = None
    return Source(
        id=r["id"],
        name=r["name"],
        display_name=r["display_name"],
        source_type=r["source_type"],
        # v1.0.42 (audit P1-10): never echo the credential embedded in the
        # WeChat aggregator URLs.  Out-bound only — the stored value is the
        # fetch input and must stay intact.  (`or ""` only for typing: a NULL
        # url used to raise a ValidationError here anyway.)
        url=sanitize_source_url(r["url"]) or "",
        fetch_interval_min=r["fetch_interval_min"],
        is_active=bool(r["is_active"]),
        notes=r["notes"],
        fetch_config=fc,
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


@router.get("", response_model=list[SourceWithTags])
def list_sources() -> list[SourceWithTags]:
    """List all sources with their associated tag names."""
    conn = get_db()
    sources = conn.execute(
        """
        SELECT id, name, display_name, source_type, url,
               fetch_interval_min, is_active, notes, fetch_config,
               created_at, updated_at
        FROM sources
        ORDER BY name
        """
    ).fetchall()
    if not sources:
        return []

    # Fetch all source_tags in one query (N+1 avoidance)
    tag_rows = conn.execute(
        "SELECT source_id, tag_name FROM source_tags"
    ).fetchall()
    tags_by_source: dict[str, list[str]] = {}
    for t in tag_rows:
        tags_by_source.setdefault(t["source_id"], []).append(t["tag_name"])

    result = []
    for r in sources:
        s = _row_to_source(r).model_dump()
        s["tags"] = sorted(tags_by_source.get(r["id"], []))
        result.append(SourceWithTags(**s))
    return result


@router.post("", response_model=SourceWithTags, status_code=201)
def create_source(body: SourceCreate) -> SourceWithTags:
    """Create a new source. Returns 409 if id or name already exists."""
    conn = get_db()
    existing = conn.execute(
        "SELECT 1 FROM sources WHERE id = ? OR name = ?", (body.id, body.name)
    ).fetchone()
    if existing:
        raise ConflictError(
            message=f"Source id={body.id!r} or name={body.name!r} already exists",
            details={"id": body.id, "name": body.name},
        )
    now = _now()
    conn.execute(
        """
        INSERT INTO sources
            (id, name, display_name, source_type, url,
             fetch_interval_min, is_active, notes, fetch_config,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (body.id, body.name, body.display_name, body.source_type, body.url,
         body.fetch_interval_min, 1 if body.is_active else 0, body.notes,
         _json.dumps(body.fetch_config, ensure_ascii=False) if body.fetch_config else None,
         now, now),
    )
    conn.commit()
    return SourceWithTags(
        id=body.id, name=body.name, display_name=body.display_name,
        source_type=body.source_type,
        # v1.0.42 (P1-10): sanitize the echo as well (idempotent).
        url=sanitize_source_url(body.url) or "",
        fetch_interval_min=body.fetch_interval_min,
        is_active=body.is_active, notes=body.notes,
        fetch_config=body.fetch_config,
        created_at=now, updated_at=now, tags=[],
    )


@router.get("/{source_id}", response_model=SourceWithTags)
def get_source(source_id: str) -> SourceWithTags:
    """Get a single source with its tag names."""
    conn = get_db()
    row = conn.execute(
        """
        SELECT id, name, display_name, source_type, url,
               fetch_interval_min, is_active, notes, fetch_config,
               created_at, updated_at
        FROM sources WHERE id = ?
        """,
        (source_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Source {source_id!r} not found")
    tag_rows = conn.execute(
        "SELECT tag_name FROM source_tags WHERE source_id = ? ORDER BY tag_name",
        (source_id,),
    ).fetchall()
    s = _row_to_source(row).model_dump()
    s["tags"] = [t["tag_name"] for t in tag_rows]
    return SourceWithTags(**s)


@router.put("/{source_id}", response_model=SourceWithTags)
def update_source(source_id: str, body: SourceUpdate) -> SourceWithTags:
    """Update mutable fields of a source. Returns 404 if not found."""
    conn = get_db()
    row = conn.execute(
        """
        SELECT id, name, display_name, source_type, url,
               fetch_interval_min, is_active, notes, fetch_config,
               created_at, updated_at
        FROM sources WHERE id = ?
        """,
        (source_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Source {source_id!r} not found")

    fields = {**dict(row)}
    if body.display_name is not None:
        fields["display_name"] = body.display_name
    if body.url is not None:
        # v1.0.42 (P1-10): a masked value means "the client echoed what we
        # gave it" → keep the stored credential, never write "…" into the DB
        # (it is the fetch input; a masked value would break RSS pulling).
        if not is_masked_url(body.url):
            fields["url"] = body.url
    if body.fetch_interval_min is not None:
        fields["fetch_interval_min"] = body.fetch_interval_min
    if body.is_active is not None:
        fields["is_active"] = 1 if body.is_active else 0
    if body.notes is not None:
        fields["notes"] = body.notes
    if body.fetch_config is not None:
        fields["fetch_config"] = _json.dumps(body.fetch_config, ensure_ascii=False)
    fields["updated_at"] = _now()

    conn.execute(
        """
        UPDATE sources SET
            display_name = ?, url = ?, fetch_interval_min = ?,
            is_active = ?, notes = ?, fetch_config = ?, updated_at = ?
        WHERE id = ?
        """,
        (fields["display_name"], fields["url"], fields["fetch_interval_min"],
         fields["is_active"], fields["notes"], fields.get("fetch_config"),
         fields["updated_at"], source_id),
    )
    conn.commit()
    tag_rows = conn.execute(
        "SELECT tag_name FROM source_tags WHERE source_id = ? ORDER BY tag_name",
        (source_id,),
    ).fetchall()
    s = {**fields, "is_active": bool(fields["is_active"])}
    # v1.0.42 (P1-10): the response must not carry the stored credential.
    s["url"] = sanitize_source_url(s.get("url")) or ""
    # Parse fetch_config JSON string back to dict for the response model
    fc_raw = s.get("fetch_config")
    if isinstance(fc_raw, str) and fc_raw:
        try:
            s["fetch_config"] = _json.loads(fc_raw)
        except (_json.JSONDecodeError, TypeError):
            s["fetch_config"] = None
    s["tags"] = [t["tag_name"] for t in tag_rows]
    return SourceWithTags(**s)


@router.delete("/{source_id}", status_code=204)
def delete_source(source_id: str) -> None:
    """Delete a source. CASCADE removes source_tags and source_keyword_filters."""
    conn = get_db()
    row = conn.execute("SELECT 1 FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        raise NotFoundError(message=f"Source {source_id!r} not found")
    conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    conn.commit()


@router.put("/{source_id}/tags", response_model=SourceWithTags)
def set_source_tags(source_id: str, tag_names: list[str]) -> SourceWithTags:
    """Replace the tag list for a source (PUT semantics: full replacement).

    Tags not in the request body are removed; new tags are added.
    Tags must already exist in tag_definitions (FK).
    """
    conn = get_db()
    row = conn.execute("SELECT 1 FROM sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        raise NotFoundError(message=f"Source {source_id!r} not found")

    # Validate all tag names exist
    if tag_names:
        placeholders = ",".join("?" for _ in tag_names)
        valid = conn.execute(
            f"SELECT name FROM tag_definitions WHERE name IN ({placeholders})",
            tag_names,
        ).fetchall()
        valid_names = {v["name"] for v in valid}
        missing = set(tag_names) - valid_names
        if missing:
            from moni.web.exceptions import BadRequestError
            raise BadRequestError(
                message=f"Unknown tag names: {sorted(missing)}",
                details={"missing": sorted(missing)},
            )

    now = _now()
    conn.execute("DELETE FROM source_tags WHERE source_id = ?", (source_id,))
    for tag_name in tag_names:
        conn.execute(
            "INSERT INTO source_tags (source_id, tag_name, created_at) VALUES (?, ?, ?)",
            (source_id, tag_name, now),
        )
    conn.execute("UPDATE sources SET updated_at = ? WHERE id = ?", (now, source_id))
    conn.commit()

    # Return updated source
    return get_source(source_id)


@router.get("/{source_id}/fetch_config", response_model=FetchConfigResponse)
def get_fetch_config(source_id: str) -> FetchConfigResponse:
    """Get the fetch_config for a source, merged with type defaults."""
    conn = get_db()
    row = conn.execute(
        "SELECT name, fetch_config FROM sources WHERE id = ?",
        (source_id,),
   ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Source {source_id!r} not found")

    user_cfg: dict = {}
    if row["fetch_config"]:
        try:
            user_cfg = _json.loads(row["fetch_config"])
        except (_json.JSONDecodeError, TypeError):
            pass

    source_name = row["name"]
    defaults = _FETCH_CONFIG_DEFAULTS.get(source_name, {})
    # Only remove keys that were explicitly retired. A schema whitelist is
    # unsafe here: fetch_config also carries runtime keys and documented
    # aliases (timeout_sec, bypass_tag_filter) that older source schemas do
    # not list.
    user_cfg = {
        k: v for k, v in user_cfg.items() if k not in _RETIRED_FETCH_CONFIG_KEYS
    }
    if "bypass_tag_filter" in user_cfg:
        if "bypass_keyword_filters" not in user_cfg:
            user_cfg["bypass_keyword_filters"] = user_cfg["bypass_tag_filter"]
        user_cfg.pop("bypass_tag_filter")
    merged = {**defaults, **user_cfg}

    # v1.0.71 (sprint-35): X-bot 精选的门槛与 scope 是**独立常量**
    # (方案 A) —— 在**读取时**合并进响应, 而不是在 ``defaults`` 里
    # (那样会破坏 ``test_fetch_config_has_single_source`` 的逐字比对)。
    if source_name == "xbot_feed":
        merged["thresholds"] = _effective_thresholds(user_cfg)
        merged["scope"] = _moni_scope_copy(user_cfg)

    return FetchConfigResponse(
        source_id=source_id,
        source_name=source_name,
        fetch_config=merged,
        has_custom=bool(user_cfg),
        defaults=defaults,
    )


@router.get("/{source_id}/scope", response_model=ScopeResponse)
def get_source_scope(source_id: str) -> ScopeResponse:
    """X-bot 精选的信源范围 (scope) + 门槛 (thresholds)。

    * ``scope`` —— 展示用的范围: 上游导出的 > moni 侧存的副本 > 兜底常量;
    * ``scope_source`` —— ``upstream`` | ``moni_fallback``;
    * ``upstream_scope_available`` —— 上游 feed JSON 顶层有没有 scope 键;
    * ``scope_edited`` —— moni 侧有没有用户编辑过的副本;
    * ``thresholds`` —— per-topic 门槛 (默认全 0 = 不设限);
    * ``scope_gates_ingest`` —— **false**: scope 只是记录与展示, 不参与
      入库闸门 (编辑它不会改变 X-bot 的搜索范围)。

    只读本地 clone 的 feed JSON —— **绝不在请求路径上做 git 操作**。
    """
    conn = get_db()
    row = conn.execute(
        "SELECT name, fetch_config FROM sources WHERE id = ?",
        (source_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Source {source_id!r} not found")

    source_name = row["name"]
    if source_name != "xbot_feed":
        raise BadRequestError(
            message=(
                f"scope is only supported for the xbot_feed source "
                f"(got {source_name!r})"
            )
        )

    user_cfg: dict = {}
    if row["fetch_config"]:
        try:
            parsed = _json.loads(row["fetch_config"])
            if isinstance(parsed, dict):
                user_cfg = parsed
        except (_json.JSONDecodeError, TypeError):
            user_cfg = {}

    scope, scope_source, upstream_available, scope_edited = _effective_scope(
        user_cfg
    )
    return ScopeResponse(
        source_id=source_id,
        source_name=source_name,
        scope=scope,
        scope_source=scope_source,
        upstream_scope_available=upstream_available,
        scope_edited=scope_edited,
        thresholds=_effective_thresholds(user_cfg),
        scope_gates_ingest=False,
        stacking_note=(
            "上游自 2026-11-12 起自带 wechat 门槛（5000 粉 / 5 赞）；"
            "moni 门槛是第二道串联闸门，只能更严，捞不回上游已丢的条目。"
        ),
    )


@router.put("/{source_id}/fetch_config", response_model=FetchConfigResponse)
def update_fetch_config(source_id: str, body: dict) -> FetchConfigResponse:
    """Update fetch_config for a source. Pass the full config dict.

    v1.0.71 (sprint-35):

    * **校验**: ``thresholds`` / ``scope`` 非法 → 400, 且**不写库**;
    * **合并语义** (只对 xbot_feed): ``stored = {**现有, **body}``
      → 只传 ``thresholds`` 时其余 8 个键仍在。
      其它信源维持"整体替换 body"的既有语义 (G-2: 不改既有行为契约)。
    """
    conn = get_db()
    row = conn.execute(
        "SELECT name, fetch_config FROM sources WHERE id = ?",
        (source_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Source {source_id!r} not found")

    source_name = row["name"]

    if source_name == "xbot_feed":
        # ── 校验 (写库之前!) ─────────────────────────────────────────
        # P1-4: 校验**只在 xbot_feed 分支内**。其它信源的
        # ``thresholds`` / ``scope`` 键按普通键透传 → 维持"整体替换 body"
        # 的既有语义 (G-2), 公共端点的 body 契约不被单信源需求改写。
        payload = dict(body)
        if "thresholds" in payload:
            payload["thresholds"] = _validate_thresholds(payload["thresholds"])
        if "scope" in payload:
            payload["scope"] = _validate_scope(payload["scope"])

        # 合并语义: 只传 thresholds 时, 库里原有的 8 个键不能被抹掉。
        stored: dict = {}
        if row["fetch_config"]:
            try:
                parsed = _json.loads(row["fetch_config"])
                if isinstance(parsed, dict):
                    stored = parsed
            except (_json.JSONDecodeError, TypeError):
                stored = {}
        merged_payload = {**stored, **payload}
    else:
        merged_payload = dict(body)

    now = _now()
    conn.execute(
        "UPDATE sources SET fetch_config = ?, updated_at = ? WHERE id = ?",
        (_json.dumps(merged_payload, ensure_ascii=False), now, source_id),
    )
    conn.commit()

    # Return merged config
    return get_fetch_config(source_id)
