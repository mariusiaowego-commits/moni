"""脱敏实验室 API（sprint-69）—— 四栏演练台的四条真实路径。

测的是「入口行为」：预览不出网、真跑一次四栏对得上、账本口径分得清。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.web.server import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
FAKE_HERMES_SRC = REPO_ROOT / "tests" / "fake_hermes" / "hermes"

SAMPLE_TEXT = (
    "招商银行客服电话 13812345678，联系人邮箱 analyst@bankcorp.cn。"
    "请复述上述联系方式并判断风险。"
)
PHONE = "13812345678"


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
    import moni.db.connection as conn_mod
    import moni.db.migrate as mig_mod

    conn_mod._CON = None  # type: ignore[attr-defined]
    conn_mod._DB_PATH = None  # type: ignore[attr-defined]
    mig_mod._CON = None  # type: ignore[attr-defined]


@pytest.fixture
def fake_hermes(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    dst = bin_dir / "hermes"
    shutil.copy2(FAKE_HERMES_SRC, dst)
    dst.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_HERMES_LOG", str(tmp_path / "calls.jsonl"))
    monkeypatch.setenv("FAKE_HERMES_REPLY", "FAKE-REPLY")
    monkeypatch.setenv("FAKE_HERMES_ECHO_PLACEHOLDERS", "1")
    monkeypatch.setenv("PYTHONPATH", f"{REPO_ROOT}{os.pathsep}{os.environ.get('PYTHONPATH', '')}")
    return dst


class TestPreview:
    def test_preview_masks_locally_without_calling_the_model(self, client, monkeypatch):
        import moni.llm.runner as runner_mod

        def _boom(self, req):  # pragma: no cover - must never run
            raise AssertionError("preview must not call the model")

        monkeypatch.setattr(runner_mod.FailoverRunner, "call", _boom)

        res = client.post(
            "/api/desensitize/preview",
            json={"text": SAMPLE_TEXT, "task_type": "standard"},
        )
        assert res.status_code == 200
        body = res.json()
        assert PHONE not in body["masked_text"]
        assert body["masked_total"] >= 1
        assert any(item["placeholder"].startswith("{{PHONE_") for item in body["items"])
        # 审计明细里不能出现原值
        assert PHONE not in res.text

    def test_preview_writes_no_ledger_row(self, client):
        client.post("/api/desensitize/preview", json={"text": SAMPLE_TEXT})
        conn = get_db()
        rows = conn.execute("SELECT count(*) AS n FROM desensitize_events").fetchone()
        assert rows["n"] == 0

    def test_extra_terms_mask_org_names_without_touching_the_db(self, client):
        res = client.post(
            "/api/desensitize/preview",
            json={"text": SAMPLE_TEXT, "extra_terms": ["招商银行"]},
        )
        assert res.status_code == 200
        body = res.json()
        assert "招商银行" not in body["masked_text"]
        assert any(item["kind"] == "ORG" for item in body["items"])
        # 临时词只在本次调用生效：规则表没有新增行
        conn = get_db()
        rows = conn.execute("SELECT count(*) AS n FROM desensitize_rules").fetchone()
        assert rows["n"] == 0

    def test_org_name_is_not_masked_without_extra_terms(self, client):
        body = client.post(
            "/api/desensitize/preview", json={"text": SAMPLE_TEXT}
        ).json()
        assert "招商银行" in body["masked_text"]


class TestRun:
    def test_four_columns_line_up(self, client, fake_hermes):
        res = client.post("/api/desensitize/run", json={"text": SAMPLE_TEXT})
        assert res.status_code == 200
        body = res.json()

        assert body["original"] == SAMPLE_TEXT
        assert PHONE not in body["masked"]
        assert "{{PHONE_" in body["reply_raw"]
        assert PHONE not in body["reply_raw"]
        assert PHONE in body["restored"]

        stats = body["stats"]
        assert stats["leak_scan_hits"] == 0
        assert stats["unrestored_count"] == 0
        assert stats["residual_placeholders"] == 0
        assert stats["masked_total"] >= 1
        assert stats["restored_total"] >= 1

    def test_run_records_a_lab_row_that_excluded_from_production_totals(self, client, fake_hermes):
        client.post("/api/desensitize/run", json={"text": SAMPLE_TEXT})

        conn = get_db()
        row = conn.execute(
            "SELECT call_site, leak_scan_hits FROM desensitize_events"
        ).fetchone()
        assert row["call_site"] == "desens_lab"
        assert row["leak_scan_hits"] == 0

        stats = client.get("/api/desensitize/stats").json()
        assert stats["totals"]["calls"] == 0
        assert stats["lab_totals"]["calls"] == 1
        assert stats["lab_totals"]["leak_scan_hits"] == 0
        assert stats["enabled"] is False

    def test_run_with_global_switch_on_does_not_mask_twice(self, client, fake_hermes):
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO app_settings(key, value, updated_at) "
            "VALUES('desensitize.enabled','1', '2026-11-25')"
        )
        conn.commit()

        body = client.post("/api/desensitize/run", json={"text": SAMPLE_TEXT}).json()
        assert body["masked"].count("{{PHONE_") == 1
        assert PHONE in body["restored"]

        conn = get_db()
        sites = [
            r["call_site"]
            for r in conn.execute("SELECT DISTINCT call_site FROM desensitize_events").fetchall()
        ]
        assert sites == ["desens_lab"]


class TestStats:
    def test_stats_shape(self, client):
        body = client.get("/api/desensitize/stats").json()
        assert set(body) == {"enabled", "levels", "totals", "lab_totals", "recent"}
        assert body["totals"]["restore_success_rate"] == 1.0
        assert body["levels"]["level_l1"] == 1
