"""sprint-54 / id 26111703 —— `bypass` 拆分的行为锁。

被测的两个判定函数 (`moni.fetch.filters`):

* ``_bypass_keyword_filters(source)`` —— 跳过**全局/源级关键词**闸门
* ``_bypass_tag_or_gate(source)``      —— 跳过 **tag OR 闸门**

**键名契约** (dad 2026-11-17 拍板):

* **新键 = ``bypass_tag_or_gate``** —— 与旧 deprecated alias
  ``bypass_tag_filter`` **不冲突**。旧名在 ``config_sources.py`` 里是
  ``bypass_keyword_filters`` 的 alias (会被归一并 ``pop``), 复用它会让拆分
  自我推翻。
* **旧 alias ``bypass_tag_filter`` 的语义一律保留**: canonical 键优先、
  alias 兜底; ``config_sources.py`` 的归一 / ``pop`` **一行不动**,
  ``tests/test_fetch_config.py`` 里的既有兼容测试**不修改**。

**核心不变量**: 拆分本身是**零行为变更**的 refactor ——
任何**没有显式** ``bypass_tag_or_gate`` 键的源, 两个判定都必须与拆分前一致
(旧键回落)。真正改变 ``xbot_feed`` 行为需要**显式改配置**, 那是另一个 GO。

本文件只测**判定逻辑**, 不改任何生产配置。
"""

from __future__ import annotations

import json

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate

_SOURCE_NAME = "test_bypass_src"
_SOURCE_ID = "src-test-bypass"


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """建一个**临时**库 (照 tests/test_xbot_author_block.py 的既有样板)。

    ⚠️ 必须 close_db → set_db_path → migrate → close_db:
    thread-local 连接跨用例复用, 不 close 会指向已删的旧库文件。
    """
    close_db()
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MONI_DATA_DIR", str(data_dir))
    set_db_path(data_dir / "news.db")
    migrate()
    yield data_dir
    close_db()


def _set_fetch_config(source_name: str, cfg: dict) -> None:
    """写测试库的 fetch_config (只作用于 tmp db)。"""
    conn = get_db()
    conn.execute(
        "UPDATE sources SET fetch_config = ? WHERE name = ?",
        (json.dumps(cfg, ensure_ascii=False), source_name),
    )
    conn.commit()


@pytest.fixture()
def src(fresh_db):
    """一个最小可用的信源行 (供测试改 fetch_config)。"""
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO sources "
        "(id, name, display_name, source_type, url, fetch_interval_min, "
        " is_active, fetch_config, created_at, updated_at) "
        "VALUES (?, ?, '测试源', 'json', "
        "'https://example.invalid/x', 60, 1, '{}', "
        "'2026-11-17T00:00:00+00:00', '2026-11-17T00:00:00+00:00')",
        (_SOURCE_ID, _SOURCE_NAME),
    )
    conn.commit()
    return _SOURCE_NAME


# ── 旧键单独存在: 两个判定都应为 True (回落 ⇒ 零行为变更) ──────────────────

def test_legacy_key_alone_bypasses_both_gates(src):
    """⚠️ 旧配置只有 ``bypass_keyword_filters=True`` ⇒ **两个**判定都是 True。

    这条是「零行为变更」的核心断言: 拆分前 `_is_source_bypassed` 返回 True,
    拆分后两个窄判定也必须都返回 True ⇒ 存量源行为不变。
    """
    from moni.fetch.filters import (
        _bypass_keyword_filters,
        _bypass_tag_or_gate,
        _is_source_bypassed,
    )

    _set_fetch_config(src, {"bypass_keyword_filters": True})
    assert _bypass_keyword_filters(src) is True
    assert _bypass_tag_or_gate(src) is True
    # 兼容包装函数也仍返回 True
    assert _is_source_bypassed(src) is True


def test_no_key_bypasses_nothing(src):
    """没有 bypass 键 ⇒ 两个判定都是 False (缺键 = 未配置 = 不跳过)。"""
    from moni.fetch.filters import (
        _bypass_keyword_filters,
        _bypass_tag_or_gate,
        _is_source_bypassed,
    )

    _set_fetch_config(src, {})
    assert _bypass_keyword_filters(src) is False
    assert _bypass_tag_or_gate(src) is False
    assert _is_source_bypassed(src) is False


# ── 新键独立: 两侧可分别决定 ────────────────────────────────────────────────

def test_new_key_can_bypass_tag_gate_only(src):
    """``bypass_tag_or_gate=True`` 且旧键 False ⇒ 只跳过 tag 闸门。

    这是拆分的**目标状态**: 源级黑名单闸门照常执行, tag OR 闸门被跳过。
    """
    from moni.fetch.filters import (
        _bypass_keyword_filters,
        _bypass_tag_or_gate,
    )

    _set_fetch_config(src, {"bypass_keyword_filters": False, "bypass_tag_or_gate": True})
    assert _bypass_tag_or_gate(src) is True
    assert _bypass_keyword_filters(src) is False


def test_new_key_can_bypass_keyword_gate_only(src):
    """``bypass_tag_or_gate=False`` 且旧键 True ⇒ 只跳过关键词闸门。"""
    from moni.fetch.filters import (
        _bypass_keyword_filters,
        _bypass_tag_or_gate,
    )

    _set_fetch_config(src, {"bypass_keyword_filters": True, "bypass_tag_or_gate": False})
    assert _bypass_keyword_filters(src) is True
    assert _bypass_tag_or_gate(src) is False


def test_both_false_bypasses_nothing(src):
    """两侧都显式 False ⇒ 都不跳过。"""
    from moni.fetch.filters import (
        _bypass_keyword_filters,
        _bypass_tag_or_gate,
        _is_source_bypassed,
    )

    _set_fetch_config(src, {"bypass_keyword_filters": False, "bypass_tag_or_gate": False})
    assert _bypass_keyword_filters(src) is False
    assert _bypass_tag_or_gate(src) is False
    assert _is_source_bypassed(src) is False


# ── 旧 deprecated alias 的语义**必须保留** (dad 2026-11-17 拍板) ────────────

def test_deprecated_alias_still_falls_back_for_keyword_gate(src):
    """⚠️ 旧 alias ``bypass_tag_filter`` 是 ``bypass_keyword_filters`` 的别名。

    拆分**不得**改变它的语义: 只有 alias 时, 关键词闸门仍被跳过
    (canonical 缺失 ⇒ alias 兜底), 与拆分前一致。
    """
    from moni.fetch.filters import (
        _bypass_keyword_filters,
        _bypass_tag_or_gate,
    )

    _set_fetch_config(src, {"bypass_tag_filter": True})
    assert _bypass_keyword_filters(src) is True
    assert _bypass_tag_or_gate(src) is True


def test_canonical_key_wins_over_stale_alias(src):
    """⚠️ canonical 键优先于 stale alias (旧语义, 不得反转)。

    ``bypass_keyword_filters=False`` + 残留 alias ``True`` ⇒ 两个闸门都**不**跳过。
    这正是旧 ``_is_source_bypassed`` 的行为 (canonical 优先)。
    """
    from moni.fetch.filters import (
        _bypass_keyword_filters,
        _bypass_tag_or_gate,
        _is_source_bypassed,
    )

    _set_fetch_config(src, {"bypass_keyword_filters": False, "bypass_tag_filter": True})
    assert _bypass_keyword_filters(src) is False
    assert _bypass_tag_or_gate(src) is False
    assert _is_source_bypassed(src) is False


def test_new_key_does_not_affect_the_deprecated_alias_semantics(src):
    """新键**只**管 tag 闸门, 不得让旧 alias 变成「同时管两个闸门」。

    新键 True + canonical False + 残留 alias True ⇒ 关键词闸门仍由
    canonical 决定 (False) ⇒ 源级黑名单照常执行 (这是拆分的**目标状态**)。
    """
    from moni.fetch.filters import (
        _bypass_keyword_filters,
        _bypass_tag_or_gate,
    )

    _set_fetch_config(
        src,
        {
            "bypass_keyword_filters": False,
            "bypass_tag_filter": True,
            "bypass_tag_or_gate": True,
        },
    )
    assert _bypass_keyword_filters(src) is False, "canonical 必须仍然优先"
    assert _bypass_tag_or_gate(src) is True


# ── 存量真实源的零行为变更 (用生产 seed 的默认值做形状断言) ──────────────────

def test_existing_bypass_users_keep_their_behaviour():
    """⚠️ 存量两个 bypass 用户的 fetch_config 里**没有** ``bypass_tag_or_gate``。

    ⇒ 拆分后它们两个闸门**都**仍然跳过 (行为不变)。
    这条锁死「拆分不改变任何存量源的行为」这个前提 ——
    若将来有人给 seed 加了 ``bypass_tag_or_gate``, 这条会红, 提醒复核。
    """
    from moni.fetch.sources.follow_builders import _DEFAULTS as FB_CFG
    from moni.fetch.sources.xbot_feed import DEFAULT_FETCH_CONFIG as XB_CFG

    for name, cfg in (("follow_builders", FB_CFG), ("xbot_feed", XB_CFG)):
        assert cfg.get("bypass_keyword_filters") is True, f"{name} 旧键应仍为 True"
        assert "bypass_tag_or_gate" not in cfg, (
            f"{name} 的 DEFAULT_FETCH_CONFIG 里出现了新键 ⇒ "
            "存量源行为可能改变, 需重新评估"
        )


# ── tag_filter 用的是窄判定 (不是合并判定) ─────────────────────────────────

def test_tag_filter_module_uses_the_narrow_helper():
    """静态断言: ``tag_filter`` 导入的是 ``_bypass_tag_or_gate``。

    ⚠️ 判据要落在**导入名**上 (唯一出处), 而不是「某个字符串出现过」。
    """
    import pathlib

    import moni.fetch.tag_filter as tf

    src = pathlib.Path(tf.__file__).read_text(encoding="utf-8")
    assert "from .filters import _bypass_tag_or_gate" in src
    assert "from .filters import _is_source_bypassed" not in src


def test_filters_module_uses_the_narrow_helper():
    """静态断言: ``filters.apply_keyword_filters`` 用窄判定。"""
    import pathlib

    import moni.fetch.filters as fl

    src = pathlib.Path(fl.__file__).read_text(encoding="utf-8")
    assert "if _bypass_keyword_filters(source_name):" in src


# ── 新键能被 fetcher 的 _load_config 读到 (第 4 处落点) ─────────────────────

def test_new_key_is_in_extra_config_keys():
    """``bypass_tag_or_gate`` 必须在 ``EXTRA_CONFIG_KEYS`` 白名单里。

    否则 `_load_config()` (只放行 `_DEFAULTS` ∪ `EXTRA_CONFIG_KEYS`) 会
    **静默吃掉**它 —— 库里存着、UI 显示正常、抓取时读到的却是默认值。
    """
    from moni.fetch.sources.xbot_feed import EXTRA_CONFIG_KEYS

    assert "bypass_tag_or_gate" in EXTRA_CONFIG_KEYS
    # ⚠️ 新键名**不得**与旧 deprecated alias 撞名
    assert "bypass_tag_filter" not in EXTRA_CONFIG_KEYS


def test_new_key_survives_config_save(src, fresh_db):
    """⚠️ 新键 ``bypass_tag_or_gate`` 在保存路径上**不能**被丢。

    ⚠️ **必须打真实端点**, 不能在测试里复刻归一逻辑 ——
    复刻版是「拿被测代码的副本当被测对象」, 真实代码改回 pop 它照样绿 (假绿)。

    走真实链路: ``PUT /api/config/sources/{id}/fetch_config`` → 再读库
    ⇒ 断言新键**仍在**库里。

    ⚠️ 这条**不依赖** ``config_sources.py`` 的旧 alias 归一被改 ——
    新键名与旧 alias 不冲突, 所以旧归一/pop 逻辑可以**一行不动**。
    """
    from fastapi.testclient import TestClient

    from moni.web.server import create_app

    client = TestClient(create_app())
    r = client.put(
        f"/api/config/sources/{_SOURCE_ID}/fetch_config",
        json={"bypass_tag_or_gate": True, "bypass_keyword_filters": False},
    )
    assert r.status_code == 200, r.text

    conn = get_db()
    row = conn.execute(
        "SELECT fetch_config FROM sources WHERE id = ?", (_SOURCE_ID,)
    ).fetchone()
    stored = json.loads(row["fetch_config"])
    assert "bypass_tag_or_gate" in stored, "新键在保存路径上被丢掉了 (拆分自我推翻)"
    assert stored["bypass_tag_or_gate"] is True
    assert stored["bypass_keyword_filters"] is False, "旧键的显式值被覆盖了"


def test_config_sources_keeps_legacy_alias_pop(src, fresh_db):
    """⚠️ ``config_sources.py`` 的旧 alias 归一 / ``pop`` **必须保留**。

    dad 2026-11-17 拍板: 保住旧 deprecated alias 行为, 不改既有兼容测试
    (``tests/test_fetch_config.py``)。本条把该约束钉在新测试文件里 ——
    将来若有人为了「新键不被 pop」而去动旧归一, 这条会红。

    ⚠️ 用**真实端点**断言, 不复刻归一逻辑。
    """
    from fastapi.testclient import TestClient

    from moni.web.server import create_app

    client = TestClient(create_app())
    r = client.put(
        f"/api/config/sources/{_SOURCE_ID}/fetch_config",
        json={"bypass_tag_filter": True},
    )
    assert r.status_code == 200, r.text

    # GET 响应: 旧 alias 被归一进 canonical 并 pop 掉 (旧行为)
    g = client.get(f"/api/config/sources/{_SOURCE_ID}/fetch_config")
    assert g.status_code == 200, g.text
    body = g.json()["fetch_config"]
    assert body.get("bypass_keyword_filters") is True, "旧 alias 未归一到 canonical"
    assert "bypass_tag_filter" not in body, "旧 alias 未被 pop (旧行为被改)"

    # 判定侧: 行为与拆分前一致 (两个闸门都跳过)
    from moni.fetch.filters import _bypass_keyword_filters, _bypass_tag_or_gate

    assert _bypass_keyword_filters(_SOURCE_NAME) is True
    assert _bypass_tag_or_gate(_SOURCE_NAME) is True


def test_legacy_alias_only_config_keeps_old_behaviour(src, fresh_db):
    """⚠️ 旧配置**只有** ``bypass_tag_filter`` (无旧键) ⇒ 保存后旧键补上、行为不变。

    这是「拆分对存量配置零行为变更」的端点级断言。
    """
    from fastapi.testclient import TestClient

    from moni.web.server import create_app

    client = TestClient(create_app())
    r = client.put(
        f"/api/config/sources/{_SOURCE_ID}/fetch_config",
        json={"bypass_tag_filter": True},
    )
    assert r.status_code == 200, r.text

    conn = get_db()
    row = conn.execute(
        "SELECT fetch_config FROM sources WHERE id = ?", (_SOURCE_ID,)
    ).fetchone()
    stored = json.loads(row["fetch_config"])
    # ⚠️ 真相: **PUT** 对非 xbot_feed 源是「整体替换 body」, **不做**归一 ——
    #    归一 + pop 发生在 **GET** 路径 (``config_sources.get_fetch_config``)。
    # ⇒ alias-only 的 PUT 存的就是 {"bypass_tag_filter": True} 本身,
    #    canonical **不会**被补上。这是拆分前的既有行为, 未被改动。
    assert stored == {"bypass_tag_filter": True}

    # 判定侧确认**行为**不变 (alias 兜底 ⇒ 两个闸门都跳过)
    from moni.fetch.filters import _bypass_keyword_filters, _bypass_tag_or_gate

    assert _bypass_keyword_filters(_SOURCE_NAME) is True
    assert _bypass_tag_or_gate(_SOURCE_NAME) is True


# ── keyword_audit 的排除口径 (p8 修正 B) ───────────────────────────────────

def test_keyword_audit_excludes_by_tag_gate_only():
    """⚠️ ``keyword_audit`` 审计的是**通用 tag 闸门**, 排除口径必须按 tag 闸门。

    p8 修正 B: ``xbot_feed`` 走自己的 ``_match_topic_tags``, 永远跳过通用
    ``tag_filter`` ⇒ 拆分后仍必须被 ``keyword-audit`` **排除**。
    若改成按「两个闸门都跳过」判定, 一旦 dad 在 G2 把
    ``bypass_keyword_filters`` 置 false, ``xbot_feed`` 就会被拿
    ``source_tags`` 去审计 —— 审计一道它从未跑过的闸门。
    """
    import pathlib

    import moni.cli.keyword_audit as ka

    src = pathlib.Path(ka.__file__).read_text(encoding="utf-8")
    # ⚠️ 判据落在 **import 行 + 调用点** 上 —— 不能全文扫名字,
    #    docstring 里提到 ``_bypass_keyword_filters`` 是**说明**不是调用。
    assert "from moni.fetch.filters import _bypass_tag_or_gate" in src
    assert "from moni.fetch.filters import _bypass_keyword_filters" not in src
    assert "if _bypass_tag_or_gate(row[\"name\"]):" in src
