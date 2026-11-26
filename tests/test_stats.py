"""tests/test_stats.py — W1 量化看板测试。

v1.0.19: moni-B §四.7 W1 申报包装。
覆盖：
- repository SQL 聚合 (5 模块 + timeseries + overview)
- savings 公式 (T₀ yaml + T₁ 实测)
- 防夸大口径 (N<阈值 → display_status="数据积累中")
- API 端点 (7 个 GET /api/stats/*)
- COALESCE(extracted_at, created_at) 兼容老数据
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.stats.repository import (
    N_DEFAULT_THRESHOLD,
    N_EXTRACT_THRESHOLD,
    extract_layer_stats,
    fetch_layer_stats,
    llm_layer_stats,
    overview,
    timeseries,
    usage_layer_stats,
)
from moni.stats.savings import compute_savings
from moni.web.server import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """每个测试都用干净的临时 DB。"""
    db_path = tmp_path / "test.db"
    set_db_path(db_path)
    migrate()
    yield
    close_db()
    import moni.db.connection as conn_mod
    import moni.db.migrate as mig_mod
    conn_mod._CON = None
    conn_mod._DB_PATH = None
    mig_mod._CON = None


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def seed_minimal():
    """种入各表 ≥ N_DEFAULT_THRESHOLD (10) 条样本, 测正常 display_status=ok 路径."""
    conn = get_db()
    now = _iso_now()

    # articles: 先种 12 条, 给 FK 引用 (article_pins / llm_jobs / events / cases)
    for i in range(12):
        conn.execute(
            "INSERT INTO articles (id, title, url, source, fetched_at, has_ai_summary, raw_content) "
            "VALUES (?, ?, ?, ?, ?, 0, ?)",
            (
                f"art_{i}", f"测试文章{i}", f"https://example.com/{i}",
                "test_source", now, f"raw content {i}",
            ),
        )

    # cron_runs: 12 条 (>= N_DEFAULT_THRESHOLD=10) — 无 FK
    for i in range(12):
        conn.execute(
            "INSERT INTO cron_runs (job_id, triggered_at, finished_at, success, new_articles, sources_run) "
            "VALUES (?, ?, ?, 1, ?, ?)",
            (f"job_{i}", now, now, 50 + i, "test_source"),
        )

    # llm_jobs: 12 条 done (>= N_DEFAULT_THRESHOLD) — FK articles.id
    for i in range(8):
        conn.execute(
            "INSERT INTO llm_jobs (id, task_type, article_id, status, duration_ms, created_at, started_at, finished_at) "
            "VALUES (?, 'summary', ?, 'done', ?, ?, ?, ?)",
            (f"sum_{i}", f"art_{i}", 30000 + i * 1000, now, now, now),
        )
    for i in range(4):
        conn.execute(
            "INSERT INTO llm_jobs (id, task_type, article_id, status, duration_ms, created_at, started_at, finished_at) "
            "VALUES (?, 'fact_check', ?, 'done', ?, ?, ?, ?)",
            (f"fc_{i}", f"art_{i}", 40000 + i * 1000, now, now, now),
        )

    # share_tasks: 20 条 done — 无 FK, 但 article_ids NOT NULL (JSON 数组字符串)
    for i in range(20):
        conn.execute(
            "INSERT INTO share_tasks (id, format, article_ids, status, created_at, started_at, finished_at) "
            "VALUES (?, 'pdf', '[\"art_0\"]', 'done', ?, ?, ?)",
            (f"share_{i}", now, now, now),
        )

    # article_pins: 12 条 — FK articles.id
    for i in range(12):
        conn.execute(
            "INSERT INTO article_pins (article_id, pinned_at) VALUES (?, ?)",
            (f"art_{i}", now),
        )

    # events: 8 条 (>= N_EXTRACT_THRESHOLD=5) — FK articles.id
    for i in range(8):
        conn.execute(
            "INSERT INTO events (event_id, article_id, event_date, event_date_precision, subject, event_type, "
            "extraction_status, review_status, extraction_attempts, normalize_version, "
            "extracted_at, created_at) "
            "VALUES (?, ?, ?, 'exact', ?, 'regulation', 'completed', 'pending', 1, 1, ?, ?)",
            (f"evt_{i}", f"art_{i}", "2026-08-01", f"事件{i}", now, now),
        )

    # cases: 6 条 (>= N_EXTRACT_THRESHOLD=5) — FK articles.id
    for i in range(6):
        conn.execute(
            "INSERT INTO cases (case_id, article_id, case_date_precision, subject, case_type, case_action, "
            "reference_value, extraction_status, review_status, case_lifecycle, "
            "extraction_attempts, normalize_version, extracted_at, created_at, updated_at) "
            "VALUES (?, ?, 'month', ?, 'AI', 'launch', 'medium', 'completed', 'pending', 'draft', "
            "1, 1, ?, ?, ?)",
            (f"MN-AI-2608-{i:04d}", f"art_{i}", f"案例{i}", now, now, now),
        )

    conn.commit()


@pytest.fixture
def seed_sparse():
    """种入稀疏数据: 各表 < N_DEFAULT_THRESHOLD, 测 display_status='数据积累中' 路径."""
    conn = get_db()
    now = _iso_now()

    # articles: 给 FK 引用
    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at, has_ai_summary, raw_content) "
        "VALUES ('a1', '测试', 'https://example.com/1', 'src', ?, 0, 'raw')",
        (now,),
    )

    # cron_runs: 3 条 (<< N_DEFAULT_THRESHOLD=10)
    for i in range(3):
        conn.execute(
            "INSERT INTO cron_runs (job_id, triggered_at, finished_at, success, new_articles, sources_run) "
            "VALUES (?, ?, ?, 1, 10, 'src')",
            (f"j_{i}", now, now),
        )

    # events: 1 条, cases: 1 条 (远小于 N_EXTRACT_THRESHOLD=5)
    conn.execute(
        "INSERT INTO events (event_id, article_id, event_date, event_date_precision, subject, event_type, "
        "extraction_status, review_status, extraction_attempts, normalize_version, "
        "extracted_at, created_at) "
        "VALUES ('e1', 'a1', '2026-08-01', 'exact', '事件', 'regulation', 'completed', 'pending', 1, 1, ?, ?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO cases (case_id, article_id, case_date_precision, subject, case_type, case_action, "
        "reference_value, extraction_status, review_status, case_lifecycle, "
        "extraction_attempts, normalize_version, extracted_at, created_at, updated_at) "
        "VALUES ('MN-AI-2608-0001', 'a1', 'month', '案例', 'AI', 'launch', 'medium', 'completed', 'pending', "
        "'draft', 1, 1, ?, ?, ?)",
        (now, now, now),
    )

    conn.commit()


@pytest.fixture
def seed_no_extracted_at():
    """events/cases 老数据: extracted_at 为 NULL, 验证 COALESCE fallback 到 created_at."""
    conn = get_db()
    now = _iso_now()

    conn.execute(
        "INSERT INTO articles (id, title, url, source, fetched_at, has_ai_summary, raw_content) "
        "VALUES ('a1', '测试', 'https://example.com/1', 'src', ?, 0, 'raw')",
        (now,),
    )
    conn.execute(
        "INSERT INTO events (event_id, article_id, event_date, event_date_precision, subject, event_type, "
        "extraction_status, review_status, extraction_attempts, normalize_version, created_at) "
        "VALUES ('e_null', 'a1', '2026-08-01', 'exact', '事件', 'regulation', 'completed', 'pending', 1, 1, ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO cases (case_id, article_id, case_date_precision, subject, case_type, case_action, "
        "reference_value, extraction_status, review_status, case_lifecycle, "
        "extraction_attempts, normalize_version, created_at, updated_at) "
        "VALUES ('MN-AI-2608-0099', 'a1', 'month', '案例', 'AI', 'launch', 'medium', 'completed', 'pending', "
        "'draft', 1, 1, ?, ?)",
        (now, now),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# TestRepositoryFetch — 抓取层
# ---------------------------------------------------------------------------

class TestRepositoryFetch:

    def test_empty_db(self):
        result = fetch_layer_stats("30d")
        assert result["count"] == 0
        assert result["display_status"] == "数据积累中"
        assert result["threshold"] == N_DEFAULT_THRESHOLD

    def test_sparse_db(self, seed_sparse):
        result = fetch_layer_stats("30d")
        assert result["count"] == 3
        assert result["display_status"] == "数据积累中"

    def test_normal_db(self, seed_minimal):
        result = fetch_layer_stats("30d")
        assert result["count"] >= N_DEFAULT_THRESHOLD
        assert result["display_status"] == "ok"
        assert result["raw"]["cron_total"] >= N_DEFAULT_THRESHOLD
        assert result["raw"]["new_articles_total"] > 0

    def test_success_rate(self, seed_minimal):
        result = fetch_layer_stats("30d")
        assert 0 <= result["raw"]["success_rate"] <= 1


# ---------------------------------------------------------------------------
# TestRepositoryLLM — LLM 层
# ---------------------------------------------------------------------------

class TestRepositoryLLM:

    def test_status_done_not_success(self, seed_minimal):
        """验证 status 枚举是 'done' 而非 'success' (sprint 06 踩坑修复)."""
        result = llm_layer_stats("30d")
        # seed_minimal 12 条全是 done, success_rate 应 = 1.0
        assert result["raw"]["success_rate"] == 1.0
        assert result["raw"]["task_success"] == 12

    def test_by_task_type_breakdown(self, seed_minimal):
        result = llm_layer_stats("30d")
        by_type = {x["task_type"]: x for x in result["raw"]["by_task_type"]}
        assert by_type["summary"]["count"] == 8
        assert by_type["fact_check"]["count"] == 4
        # avg_duration_ms 来自 seed: 30000-37000 / 40000-43000
        assert by_type["summary"]["avg_duration_ms"] > 0
        assert by_type["fact_check"]["avg_duration_ms"] > 0


# ---------------------------------------------------------------------------
# TestRepositoryExtract — 抽取层 (Q6 补丁: events/cases 走 N_EXTRACT_THRESHOLD)
# ---------------------------------------------------------------------------

class TestRepositoryExtract:

    def test_sparse_dual_threshold(self, seed_sparse):
        """cases=1 / events=1 都 < N_EXTRACT_THRESHOLD=5 → display_status='数据积累中'."""
        result = extract_layer_stats("30d")
        assert result["count"] == 1  # min(events=1, cases=1)
        assert result["display_status"] == "数据积累中"
        assert result["threshold"] == N_EXTRACT_THRESHOLD
        assert result["raw"]["events_display"] == "数据积累中"
        assert result["raw"]["cases_display"] == "数据积累中"

    def test_normal_both_pass(self, seed_minimal):
        """events=8 cases=6 都 >= N_EXTRACT_THRESHOLD=5 → display_status='ok'."""
        result = extract_layer_stats("30d")
        assert result["count"] == 6
        assert result["display_status"] == "ok"
        assert result["raw"]["events_display"] == "ok"
        assert result["raw"]["cases_display"] == "ok"

    def test_coalesce_fallback(self, seed_no_extracted_at):
        """events/cases 老数据 extracted_at NULL 时, 走 COALESCE(extracted_at, created_at)."""
        result = extract_layer_stats("30d")
        assert result["raw"]["events_total"] == 1
        assert result["raw"]["cases_total"] == 1


# ---------------------------------------------------------------------------
# TestRepositoryUsage — 使用层
# ---------------------------------------------------------------------------

class TestRepositoryUsage:

    def test_pin_share_count(self, seed_minimal):
        result = usage_layer_stats("30d")
        assert result["raw"]["pin_count"] == 12
        assert result["raw"]["share_count"] == 20
        assert result["raw"]["brief_count"] == 20  # 20 条全 done


# ---------------------------------------------------------------------------
# TestRepositoryTimeseries — 趋势
# ---------------------------------------------------------------------------

class TestRepositoryTimeseries:

    def test_valid_metric(self, seed_minimal):
        result = timeseries("fetch", "30d", "daily")
        assert result["metric"] == "fetch"
        assert result["granularity"] == "daily"
        assert result["display_status"] == "ok"
        assert len(result["points"]) >= 1

    def test_invalid_metric(self):
        result = timeseries("invalid", "30d", "daily")
        assert result["display_status"] == "invalid_metric"
        assert result["points"] == []


# ---------------------------------------------------------------------------
# TestRepositoryOverview — 顶部三卡
# ---------------------------------------------------------------------------

class TestRepositoryOverview:

    def test_cards_structure(self, seed_minimal):
        result = overview("30d")
        assert "cards" in result
        assert all(k in result["cards"] for k in ("baseline", "current", "target"))
        assert all(
            k in result["cards"]["current"]
            for k in ("fetch_total", "llm_total", "new_articles")
        )

    def test_target_is_120_percent(self, seed_minimal):
        """target = current × 1.2 + 1 (保守)."""
        result = overview("30d")
        cur = result["cards"]["current"]
        tgt = result["cards"]["target"]
        for k in cur:
            assert tgt[k] >= int(cur[k] * 1.2)

    def test_modules_present(self, seed_minimal):
        result = overview("30d")
        assert set(result["modules"].keys()) == {
            "fetch", "llm", "extract", "usage", "efficiency",
        }


# ---------------------------------------------------------------------------
# TestSavings — 节省时间公式 (Q2+A 公式估算 + Q3+A 保守 T₀)
# ---------------------------------------------------------------------------

class TestSavings:

    def test_label_is_formula_estimate(self, seed_minimal):
        """display_label 必须 = '公式估算' (防评委误读为实测)."""
        result = compute_savings("30d")
        assert result["display_label"] == "公式估算"

    def test_t0_source_transparency(self, seed_minimal):
        """t0_source 必须指向 yaml, 口径透明可审计."""
        result = compute_savings("30d")
        assert result["t0_source"] == "config/savings_baseline.yaml"
        assert result["t0_config_version"] == "1.0"

    def test_summary_t0_is_30_min(self, seed_minimal):
        """summary 任务 T₀ = 30min/篇 (MOA 拍板 Q3+A 保守)."""
        result = compute_savings("30d")
        by_type = {x["task_type"]: x for x in result["raw"]["by_task_type"]}
        assert by_type["summary"]["t0_minutes_per_task"] == 30.0
        assert by_type["fact_check"]["t0_minutes_per_task"] == 30.0

    def test_savings_calculation(self, seed_minimal):
        """节省 = (T₀ - T₁) × N. seed summary 8 条, T₁ ~30-37s/条."""
        result = compute_savings("30d")
        by_type = {x["task_type"]: x for x in result["raw"]["by_task_type"]}
        # summary: 8 条, T₀=30, T₁≈0.5min → savings_per ≈ 29.5, total ≈ 236min
        assert by_type["summary"]["savings_minutes_per_task"] > 29
        assert by_type["summary"]["savings_minutes_total"] > 200

    def test_empty_db_sparse(self):
        """空 DB → display_status='数据积累中', 不报节省."""
        result = compute_savings("30d")
        assert result["count"] == 0
        assert result["display_status"] == "数据积累中"


# ---------------------------------------------------------------------------
# TestAPIRoutes — 7 个端点
# ---------------------------------------------------------------------------

class TestAPIRoutes:

    def test_overview(self, client, seed_minimal):
        r = client.get("/api/stats/overview")
        assert r.status_code == 200
        body = r.json()
        assert "cards" in body
        assert "modules" in body

    def test_overview_period_validation(self, client):
        r = client.get("/api/stats/overview?period=invalid")
        assert r.status_code == 200
        assert r.json()["period"] == "30d"  # fallback

    def test_timeseries(self, client, seed_minimal):
        r = client.get("/api/stats/timeseries?metric=fetch&granularity=daily")
        assert r.status_code == 200
        body = r.json()
        assert body["metric"] == "fetch"

    def test_llm(self, client, seed_minimal):
        r = client.get("/api/stats/llm")
        assert r.status_code == 200
        assert r.json()["module"] == "llm"

    def test_savings(self, client, seed_minimal):
        r = client.get("/api/stats/savings")
        assert r.status_code == 200
        assert r.json()["display_label"] == "公式估算"

    def test_fetch(self, client, seed_minimal):
        r = client.get("/api/stats/fetch")
        assert r.status_code == 200
        assert r.json()["module"] == "fetch"

    def test_extract(self, client, seed_minimal):
        r = client.get("/api/stats/extract")
        assert r.status_code == 200
        assert r.json()["module"] == "extract"

    def test_usage(self, client, seed_minimal):
        r = client.get("/api/stats/usage")
        assert r.status_code == 200
        assert r.json()["module"] == "usage"

    def test_timeseries_invalid_metric(self, client):
        r = client.get("/api/stats/timeseries?metric=invalid")
        assert r.status_code == 200
        assert r.json()["display_status"] == "invalid_metric"


# ---------------------------------------------------------------------------
# TestCasesDashboard — 竞赛成果指标 (v1.0.35.19)
# ---------------------------------------------------------------------------

class TestCasesDashboard:

    def test_baseline_seed(self, seed_minimal):
        """seed_minimal: 6 条 cases (全 medium/pending/draft) → 计数正确."""
        from moni.stats.repository import cases_dashboard_stats
        d = cases_dashboard_stats("all")
        assert d["total_cases"] == 6
        assert d["high_value_cases"] == 0
        assert d["verified_cases"] == 0
        assert d["pending_review"] == 6
        assert d["total_institutions"] == 6  # subject 案例0..5 各 1 家
        assert d["period"] == "all"

    def test_high_value_and_verified(self, seed_minimal):
        """加 1 条 high + 1 条 confirmed → 计数反映."""
        from moni.stats.repository import cases_dashboard_stats
        conn = get_db()
        now = _iso_now()
        conn.execute(
            "UPDATE cases SET reference_value = 'high' WHERE case_id = 'MN-AI-2608-0000'"
        )
        conn.execute(
            "UPDATE cases SET review_status = 'confirmed', case_lifecycle = 'verified' "
            "WHERE case_id = 'MN-AI-2608-0001'"
        )
        conn.commit()
        d = cases_dashboard_stats("all")
        assert d["high_value_cases"] == 1
        assert d["verified_cases"] == 1
        assert d["pending_review"] == 5
        assert d["display_label"] == "待核验"

    def test_institution_split_counts(self, seed_minimal):
        """subject ∪ counterparty 拆分: "A、B" → 2 家."""
        from moni.stats.repository import cases_dashboard_stats
        conn = get_db()
        conn.execute(
            "UPDATE cases SET counterparty = '美国银行、摩根大通' "
            "WHERE case_id = 'MN-AI-2608-0000'"
        )
        conn.execute(
            "UPDATE cases SET subject = '招商银行' "
            "WHERE case_id = 'MN-AI-2608-0001'"
        )
        conn.commit()
        d = cases_dashboard_stats("all")
        # 案例0: subject 案例0 + counterparty 拆 2 = 3; 案例1: 招商银行 → 1
        # 合计 = 3 + 1 + 案例2..5 (案例2,3,4,5) 4 家 = 8
        assert d["total_institutions"] == 8

    def test_period_all_ignores_toolbar(self, seed_minimal):
        """默认 all = 累计, 7d/30d 过滤后计数不同 (extracted_at 是 now)."""
        from moni.stats.repository import cases_dashboard_stats
        d_all = cases_dashboard_stats("all")
        assert d_all["total_cases"] == 6
        assert d_all["archived_total"] == 0
        # 30d 窗口 (extracted_at=now 在内) → 也是 6
        d_30 = cases_dashboard_stats("30d")
        assert d_30["total_cases"] == 6

    def test_archived_excluded_and_counted(self, seed_minimal):
        """archived 软删: 计入 archived_total, 但不进 total / 榜单 / 机构数."""
        from moni.cases.repository import CaseRepository
        from moni.stats.repository import cases_dashboard_stats
        conn = get_db()
        conn.execute(
            "UPDATE cases SET case_lifecycle = 'archived', reference_value = 'high' "
            "WHERE case_id = 'MN-AI-2608-0000'"
        )
        conn.commit()
        d = cases_dashboard_stats("all")
        assert d["archived_total"] == 1
        assert d["total_cases"] == 5
        assert d["high_value_cases"] == 0
        assert "MN-AI-2608-0000" not in [r["case_id"] for r in d["high_value_ranking"]]
        assert d["total_cases"] == CaseRepository.stats()["total"]

    def test_archived_excluded_from_extract_and_funnel(self, seed_minimal):
        """抽取层与数据漏斗与案例库同口径, 归档不重复计数."""
        from moni.stats.repository import data_funnel_stats, extract_layer_stats
        conn = get_db()
        conn.execute(
            "UPDATE cases SET case_lifecycle = 'archived' WHERE case_id = 'MN-AI-2608-0000'"
        )
        conn.commit()
        assert extract_layer_stats("30d")["raw"]["cases_total"] == 5
        assert data_funnel_stats()["cases_total"] == 5


class TestSplitInstitutions:

    def test_split_comma_and(self):
        from moni.stats.repository import _split_institutions
        assert _split_institutions("美国银行、摩根大通") == ["美国银行", "摩根大通"]
        assert _split_institutions("Visa、万事达卡、渣打银行、贝莱德等11家机构") == [
            "Visa", "万事达卡", "渣打银行", "贝莱德",
        ]

    def test_alias_normalize(self):
        from moni.stats.repository import _split_institutions
        assert _split_institutions("招行") == ["招商银行"]
        assert _split_institutions("工行与建行") == ["工商银行", "建设银行"]

    def test_empty_and_none(self):
        from moni.stats.repository import _split_institutions
        assert _split_institutions(None) == []
        assert _split_institutions("") == []
        assert _split_institutions("   ") == []
