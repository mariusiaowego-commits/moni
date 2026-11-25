"""脱敏配置读写接口（sprint-79）—— 总开关/级别/严格模式，运行时生效。

测的是「入口行为」：读得到当前配置、写完立刻生效、写坏的值被挡。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.llm.desensitize.audit import SETTING_KEYS, load_settings
from moni.web.server import create_app


@pytest.fixture
def client(tmp_path):
    set_db_path(tmp_path / "test.db")
    migrate()
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO app_settings(key, value, updated_at) "
        "VALUES('desensitize.enabled','0', '2026-11-25')"
    )
    conn.commit()
    yield TestClient(create_app())
    close_db()


def _setting(key: str) -> str | None:
    row = get_db().execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def test_get_config_returns_switch_levels_and_rules(client):
    res = client.get("/api/desensitize/config")
    assert res.status_code == 200
    body = res.json()
    assert body["enabled"] is False
    assert body["levels"]["level_l1"] is True
    assert body["levels"]["level_l2"] is True
    assert body["levels"]["level_l3"] is False
    assert body["levels"]["strict_fail_closed"] is False
    assert body["rules_count"] == 0
    assert body["rules"] == []


def test_put_config_writes_switch_and_levels(client):
    res = client.put(
        "/api/desensitize/config",
        json={"enabled": True, "level_l3": True},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["enabled"] is True
    assert body["levels"]["level_l3"] is True
    # 没传的字段保持原值。
    assert body["levels"]["level_l1"] is True
    assert _setting(SETTING_KEYS["enabled"]) == "1"
    assert _setting(SETTING_KEYS["level_l3"]) == "1"
    assert _setting(SETTING_KEYS["level_l1"]) == "1"


def test_put_config_takes_effect_without_restart(client):
    client.put("/api/desensitize/config", json={"enabled": True})
    res = client.get("/api/desensitize/config")
    assert res.json()["enabled"] is True
    client.put("/api/desensitize/config", json={"enabled": False})
    assert client.get("/api/desensitize/config").json()["enabled"] is False


@pytest.mark.parametrize("value", ["开", "false", "true", "yes", 1, 0])
def test_put_config_rejects_non_boolean(client, value):
    """严格布尔：字符串与数字都不收，免得「保存成功但配置没变」。"""
    res = client.put("/api/desensitize/config", json={"enabled": value})
    assert res.status_code == 422
    assert client.get("/api/desensitize/config").json()["enabled"] is False


def test_put_config_rejects_unknown_field(client):
    """字段名打错要报错，不能静默忽略。"""
    res = client.put("/api/desensitize/config", json={"enable": True})
    assert res.status_code == 422
    assert client.get("/api/desensitize/config").json()["enabled"] is False


def test_put_config_writes_each_field_to_its_own_key(client):
    """每个字段写自己的键：L1/L2 互换这种错位要被抓出来。"""
    res = client.put(
        "/api/desensitize/config",
        json={
            "enabled": True,
            "level_l1": False,
            "level_l2": True,
            "level_l3": True,
            "strict_fail_closed": False,
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["enabled"] is True
    assert body["levels"] == {
        "level_l1": False,
        "level_l2": True,
        "level_l3": True,
        "strict_fail_closed": False,
    }
    values = dict(get_db().execute("SELECT key, value FROM app_settings").fetchall())
    assert values[SETTING_KEYS["enabled"]] == "1"
    assert values[SETTING_KEYS["level_l1"]] == "0"
    assert values[SETTING_KEYS["level_l2"]] == "1"
    assert values[SETTING_KEYS["level_l3"]] == "1"
    assert values[SETTING_KEYS["strict_fail_closed"]] == "0"


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("enabled", "1"),
        ("level_l1", "0"),
        ("level_l2", "0"),
        ("level_l3", "1"),
        ("strict_fail_closed", "1"),
    ],
)
def test_each_field_writes_only_its_own_key(client, field, expected):
    """一次只写一个字段：键互换、顺序错位都要被抓出来。"""
    before = dict(get_db().execute("SELECT key, value FROM app_settings").fetchall())
    res = client.put("/api/desensitize/config", json={field: expected == "1"})
    assert res.status_code == 200
    after = dict(get_db().execute("SELECT key, value FROM app_settings").fetchall())
    assert [key for key in after if before.get(key) != after[key]] == [SETTING_KEYS[field]]
    assert after[SETTING_KEYS[field]] == expected


def test_get_config_without_rules_table_returns_400(client):
    """规则表缺失（未迁移 / 恢复旧库）时给 400，不是 500。"""
    get_db().execute("DROP TABLE desensitize_rules")
    get_db().commit()
    res = client.get("/api/desensitize/config")
    assert res.status_code == 400
    res = client.put("/api/desensitize/config", json={"enabled": True})
    assert res.status_code == 400


def test_config_with_missing_column_returns_400(client):
    """列缺失时给 400，不 500。"""
    conn = get_db()
    conn.execute("ALTER TABLE app_settings RENAME TO app_settings_old")
    conn.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()
    assert client.get("/api/desensitize/config").status_code == 400
    assert client.put("/api/desensitize/config", json={"enabled": True}).status_code == 400


def test_rules_table_broken_does_not_disable_masking(client):
    """规则表读不出来时，只丢用户规则；脱敏开关不能被顺手关掉。"""
    client.put("/api/desensitize/config", json={"enabled": True})
    get_db().execute("DROP TABLE desensitize_rules")
    get_db().commit()
    settings = load_settings()
    assert settings.enabled is True


def test_stats_and_config_agree_on_switch(client):
    client.put("/api/desensitize/config", json={"enabled": True})
    assert client.get("/api/desensitize/stats").json()["enabled"] is True
