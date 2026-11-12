"""网关视角的 LLM 配置读取（只读）。

moni 不持任何 API key：真实模型由 hermes 的 ``moni`` profile 决定，而该 profile 指向
本地 magpie 网关的某个路由组。本模块把「当前实际生效的模型」与「网关上可用的
模型 / 路由组」读出来，供 Web UI 显示——设置页因此反映真实来源，而不是让人猜。

只读：不写 hermes 配置、不写 magpie 配置、不持有任何凭证。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx
import yaml

#: moni 项目固定使用的 hermes profile。
DEFAULT_PROFILE = "moni"

#: hermes profile 里 magpie provider 的兜底网关地址。
DEFAULT_GATEWAY_URL = "http://127.0.0.1:3425/v1"

#: 网关探测超时（秒）——设置页要快，不能卡住。
PROBE_TIMEOUT = 3.0


def hermes_home() -> Path:
    """hermes 配置根目录。"""
    env = os.environ.get("HERMES_HOME")
    return Path(env) if env else Path.home() / ".hermes"


def profile_name() -> str:
    """当前 moni 使用的 hermes profile 名。"""
    for var in ("MONI_HERMES_PROFILE", "HERMES_PROFILE"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return DEFAULT_PROFILE


def read_profile_config(profile: str) -> dict[str, Any]:
    """读 hermes profile 的 config.yaml；不存在或坏掉时返回空 dict。"""
    path = hermes_home() / "profiles" / profile / "config.yaml"
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def profile_model(profile: str | None = None) -> dict[str, Any]:
    """当前 profile 的模型信息：provider、默认模型（即 magpie 路由组）、网关地址。"""
    name = profile or profile_name()
    cfg = read_profile_config(name)
    model = cfg.get("model") or {}
    providers = cfg.get("providers") or {}
    if not isinstance(model, dict):
        model = {}
    if not isinstance(providers, dict):
        providers = {}
    provider_key = str(model.get("provider") or "")
    provider_cfg = providers.get(provider_key) or {}
    if not isinstance(provider_cfg, dict):
        provider_cfg = {}
    return {
        "profile": name,
        "provider": provider_key,
        "default_model": str(model.get("default") or ""),
        "base_url": str(provider_cfg.get("base_url") or ""),
        "config_path": str(hermes_home() / "profiles" / name / "config.yaml"),
        "config_found": bool(cfg),
    }


def probe_gateway(base_url: str) -> dict[str, Any]:
    """探测网关的模型目录。失败不抛异常——设置页要能看到「不可达 + 原因」。"""
    url = (base_url or DEFAULT_GATEWAY_URL).rstrip("/") + "/models"
    try:
        resp = httpx.get(url, timeout=PROBE_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001 — 网关挂了也要能显示页面
        return {
            "reachable": False,
            "error": f"{type(exc).__name__}: {exc}",
            "base_url": base_url or DEFAULT_GATEWAY_URL,
            "models": [],
            "groups": [],
            "model_count": 0,
        }

    items = payload.get("data") if isinstance(payload, dict) else None
    models: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "")
        if not model_id:
            continue
        label = str(item.get("magpie_label") or item.get("display_name") or model_id)
        models.append(
            {
                "id": model_id,
                "label": label,
                # 路由组：id 前缀是 group/，或 magpie 自己在 label 里标了 routing group
                # （例如 auto/deepseek-v4.1-flash）。
                "is_group": model_id.startswith("group/") or "routing group" in label,
            }
        )
    return {
        "reachable": True,
        "error": None,
        "base_url": base_url or DEFAULT_GATEWAY_URL,
        "models": models,
        "groups": [m["id"] for m in models if m["is_group"]],
        "model_count": len(models),
    }


def gateway_snapshot() -> dict[str, Any]:
    """Web UI 用的网关快照：模式 + 当前 profile + 网关可用模型。"""
    from .runner import read_model_mode

    profile = profile_model()
    base_url = profile["base_url"] or DEFAULT_GATEWAY_URL
    gateway = probe_gateway(base_url)
    return {
        "mode": read_model_mode(),
        "profile": profile,
        "gateway": gateway,
    }
