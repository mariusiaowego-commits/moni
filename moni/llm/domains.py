"""事实核查交叉验证的域名分档与判定（v1.0.100）。

配置在 ``config/fact_check_domains.toml``：加域名改配置，不改代码。
判定只认注册域（eTLD+1），同一集团的多个页面不会被当成独立来源。
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "fact_check_domains.toml"

#: 需要按三段取注册域的后缀（eTLD+1 的粗略实现，覆盖本项目会遇到的域名）。
_MULTI_LABEL_SUFFIXES = frozenset(
    {
        "com.cn", "gov.cn", "org.cn", "net.cn", "edu.cn",
        "com.hk", "com.tw", "com.au", "com.sg", "com.my", "com.br", "com.mx",
        "co.uk", "co.jp", "co.kr", "or.kr", "co.in",
    }
)

_TIER_RANK = {"A": 0, "B": 1, "C": 2}


@dataclass(frozen=True)
class DomainConfig:
    tier_a: frozenset[str]
    tier_a_brand: frozenset[str]
    tier_a_regex: tuple[str, ...]
    tier_b: frozenset[str]
    tier_c: frozenset[str]


@lru_cache(maxsize=1)
def load_config(path: str | None = None) -> DomainConfig:
    with open(path or CONFIG_PATH, "rb") as fh:
        raw = tomllib.load(fh)
    return DomainConfig(
        tier_a=frozenset(d.lower() for d in raw.get("tier_a", [])),
        tier_a_brand=frozenset(d.lower() for d in raw.get("tier_a_brand", [])),
        tier_a_regex=tuple(raw.get("tier_a_regex", [])),
        tier_b=frozenset(d.lower() for d in raw.get("tier_b", [])),
        tier_c=frozenset(d.lower() for d in raw.get("tier_c", [])),
    )


def normalize(domain: str | None) -> str:
    return (domain or "").strip().lower().lstrip(".")


def registrable_domain(domain: str | None) -> str:
    """粗略 eTLD+1：``news.qq.com`` → ``qq.com``，``x.com.cn`` → ``x.com.cn``。"""
    parts = [p for p in normalize(domain).split(".") if p]
    if len(parts) >= 3 and ".".join(parts[-2:]) in _MULTI_LABEL_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _matches(domain: str, entry: str) -> bool:
    return domain == entry or domain.endswith("." + entry)


def _in_regex(domain: str, patterns: tuple[str, ...]) -> bool:
    return any(re.fullmatch(p, domain) for p in patterns)


def classify(domain: str | None, cfg: DomainConfig | None = None) -> str | None:
    """返回 ``"A"`` / ``"B"`` / ``"C"``；未收录返回 None。"""
    cfg = cfg or load_config()
    d = normalize(domain)
    if not d:
        return None
    if any(_matches(d, e) for e in cfg.tier_a) or _in_regex(d, cfg.tier_a_regex):
        return "A"
    if any(_matches(d, e) for e in cfg.tier_a_brand):
        return "A"
    if any(_matches(d, e) for e in cfg.tier_b):
        return "B"
    if any(_matches(d, e) for e in cfg.tier_c):
        return "C"
    return None


def is_in_whitelist(domain: str | None) -> bool:
    """单域是否属于一手来源（tier A）。仅用于展示，不等同于判定通过。"""
    return classify(domain) == "A"


@dataclass(frozen=True)
class AuthorityResult:
    passed: bool
    distinct_domains: int
    tier_a_domains: tuple[str, ...]
    tier_b_domains: tuple[str, ...]
    reason: str


def evaluate_authority(
    domains: list[str] | tuple[str, ...],
    cfg: DomainConfig | None = None,
) -> AuthorityResult:
    """两条判定路径：

    1. ≥2 个不同注册域且 ≥1 个一手来源；
    2. ≥3 个不同注册域且全部是编辑制媒体（tier B）。

    未收录域名与 tier C 都不计入。
    """
    cfg = cfg or load_config()
    strongest: dict[str, str] = {}
    for raw in domains:
        tier = classify(raw, cfg)
        if tier is None:
            continue
        key = registrable_domain(raw)
        if not key:
            continue
        current = strongest.get(key)
        if current is None or _TIER_RANK[tier] < _TIER_RANK[current]:
            strongest[key] = tier

    usable = {k: t for k, t in strongest.items() if t in ("A", "B")}
    a = tuple(sorted(k for k, t in usable.items() if t == "A"))
    b = tuple(sorted(k for k, t in usable.items() if t == "B"))

    if usable and len(usable) >= 2 and a:
        return AuthorityResult(
            True, len(usable), a, b, f"{len(usable)} 个注册域，含 {len(a)} 个一手来源"
        )
    if len(b) >= 3:
        return AuthorityResult(True, len(usable), a, b, f"{len(b)} 个编辑制媒体交叉印证")
    if not usable:
        return AuthorityResult(False, 0, a, b, "没有可计入的来源")
    if len(usable) < 2:
        return AuthorityResult(False, len(usable), a, b, f"只有 {len(usable)} 个可计入的注册域")
    return AuthorityResult(False, len(usable), a, b, "没有一手来源，且编辑制媒体不足 3 个")


#: 供旧代码 / 测试导入的兼容符号。
DOMAIN_WHITELIST: set[str] = set(load_config().tier_a) | set(load_config().tier_a_brand)
DOMAIN_WHITELIST_REGEX: list[str] = list(load_config().tier_a_regex)
