"""moni.db.seed_llm — 薄壳模式 LLM seed.

Seed 默认 3 个 provider（minimax-cn / google / openrouter，coder profile
已配）+ 4 个 model + 7 个活跃 settings 行。

可重复跑（INSERT OR IGNORE / UPSERT）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .connection import get_db


# 默认 seed provider：coder profile 自带的 minimax-cn
# key 已在 ~/.hermes/config.yaml 里，moni 不存。
#
# v1.0.35.13: minimax-cn (MiniMax-M3) Token Plan 已达用量上限 (HTTP 429),
# 默认主力换成 ark + ark-code-latest (dad 2026-08-24 指示)。minimax 行保留作回退。
_DEFAULT_PROVIDERS = [
    {
        "id": "seed-provider-ark",
        "name": "Ark",
        "hermes_provider": "ark",
        "notes": "Volcengine Ark 直连。Key 在 ~/.hermes/.env。仅 explicit 模式使用。",
    },
    {
        "id": "seed-provider-minimax-cn",
        "name": "minimax-cn",
        "hermes_provider": "minimax-cn",
        "notes": "MiniMax 直连。Key 在 ~/.hermes/config.yaml。⚠️ Token Plan 2026-08-24 已耗尽 (HTTP 429)。仅 explicit 模式使用。",
    },
    {
        "id": "seed-provider-google",
        "name": "Google Gemini",
        "hermes_provider": "google",
        "notes": "通过 `hermes --provider google` 调 Gemini 直连。Key 在 ~/.hermes/.env (GOOGLE_API_KEY)。仅 explicit 模式使用。",
    },
    {
        "id": "seed-provider-openrouter",
        "name": "OpenRouter",
        "hermes_provider": "openrouter",
        "notes": "OpenRouter 网关可访问多模型。Key 在 ~/.hermes/.env (OPENROUTER_API_KEY)。仅 explicit 模式使用。",
    },
    {
        "id": "seed-provider-magpie",
        "name": "magpie",
        "hermes_provider": "magpie",
        "notes": "magpie 本地网关 (127.0.0.1:3425)。explicit 模式可绑定 magpie 的 model_id"
                 "（形如 provider/model 或 group/xxx）；默认 profile 模式不需要绑定。",
    },
]

# 默认 model seed
_DEFAULT_MODELS = [
    ("seed-provider-ark", "ark-code-latest", "Ark Code Latest", ["summary", "tag_predict", "fact_check"]),
    ("seed-provider-minimax-cn", "MiniMax-M3", "MiniMax M3", ["summary", "tag_predict", "fact_check"]),
    ("seed-provider-google", "gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite", ["summary", "tag_predict"]),
    ("seed-provider-google", "gemini-2.5-pro", "Gemini 2.5 Pro", ["fact_check"]),
    ("seed-provider-openrouter", "google/gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite (via OpenRouter)", ["summary"]),
]

# 默认活跃 settings
_DEFAULT_SETTINGS = {
    "active_summary_model_id": None,        # patch: 第一轮 seed 之后用 model id 填
    "active_fact_check_model_id": None,
    "active_tag_model_id": None,
    "temperature_summary": "0.3",
    "temperature_fact_check": "0.2",
    "max_tokens_summary": "500",
    "max_tokens_fact_check": "1500",
    # profile: 不传 --provider/-m，跟随 hermes profile 的默认模型（magpie 路由组）
    "llm_model_mode": "profile",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def seed() -> None:
    """Insert or update LLM seed data. Idempotent — safe to call multiple times."""
    conn = get_db()
    now = _now()

    # ── Providers ────────────────────────────────────────────────────────
    for prov in _DEFAULT_PROVIDERS:
        conn.execute(
            """
            INSERT INTO llm_providers
                (id, name, hermes_provider, is_active, notes, created_at, updated_at)
            VALUES (?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name            = excluded.name,
                hermes_provider = excluded.hermes_provider,
                notes           = excluded.notes,
                updated_at      = excluded.updated_at
            """,
            (prov["id"], prov["name"], prov["hermes_provider"], prov["notes"], now, now),
        )

    # ── Models ───────────────────────────────────────────────────────────
    for provider_id, model_id, display_name, use_for in _DEFAULT_MODELS:
        # model_id may contain "/" (e.g. openrouter/google/gemini-2.5-flash-lite).
        # Replace slashes so the row id stays URL/CLI safe.
        model_row_id = f"seed-model-{provider_id}-{model_id}".replace("/", "-")
        conn.execute(
            """
            INSERT INTO llm_models
                (id, provider_id, model_id, display_name, use_for, enabled, created_at)
            VALUES (?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(id) DO UPDATE SET
                model_id     = excluded.model_id,
                display_name = excluded.display_name,
                use_for      = excluded.use_for,
                enabled      = excluded.enabled
            """,
            (model_row_id, provider_id, model_id, display_name, json.dumps(use_for, ensure_ascii=False), now),
        )

    # ── Active model resolution (look up the model row ids) ──────────────
    # Default: ark / ark-code-latest for all three purposes
    # (v1.0.35.13: minimax-cn 已无额度, 切 ark; 见 _DEFAULT_PROVIDERS 注释)
    active_summary = _find_model_id(conn, "ark", "ark-code-latest")
    active_fact = _find_model_id(conn, "ark", "ark-code-latest")
    active_tag = _find_model_id(conn, "ark", "ark-code-latest")

    # fallback: 若 seed 里找不到 (crop 早期 DB), 退回 minimax-cn 以免空
    if active_fact is None:
        active_fact = _find_model_id(conn, "minimax-cn", "MiniMax-M3")
    if active_summary is None:
        active_summary = _find_model_id(conn, "minimax-cn", "MiniMax-M3")
    if active_tag is None:
        active_tag = _find_model_id(conn, "minimax-cn", "MiniMax-M3")

    # ── Settings ─────────────────────────────────────────────────────────
    for key, value in _DEFAULT_SETTINGS.items():
        if value is None:
            # Fill the active_*_model_id with the resolved ids
            if key == "active_summary_model_id":
                value = active_summary
            elif key == "active_fact_check_model_id":
                value = active_fact
            elif key == "active_tag_model_id":
                value = active_tag
            else:
                continue
        conn.execute(
            """
            INSERT INTO llm_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    conn.commit()

    n_providers = conn.execute("SELECT COUNT(*) FROM llm_providers").fetchone()[0]
    n_models = conn.execute("SELECT COUNT(*) FROM llm_models").fetchone()[0]
    n_settings = conn.execute("SELECT COUNT(*) FROM llm_settings").fetchone()[0]
    print(
        f"LLM seed complete: {n_providers} provider(s), "
        f"{n_models} model(s), {n_settings} setting(s)"
    )
    print("⚠️  moni does NOT store API keys. Configure keys in ~/.hermes/config.yaml.")


def _find_model_id(conn: sqlite3.Connection, provider: str, model_id: str) -> str | None:
    """Look up the internal model row id by (hermes_provider, model_id)."""
    row = conn.execute(
        """
        SELECT m.id FROM llm_models m
        JOIN llm_providers p ON p.id = m.provider_id
        WHERE p.hermes_provider = ? AND m.model_id = ?
        """,
        (provider, model_id),
    ).fetchone()
    return row["id"] if row else None


if __name__ == "__main__":
    seed()
