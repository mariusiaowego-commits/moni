"""Tests for xwatch health probe + discovery circuit breaker."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from moni.fetch.health import check_xwatch
from moni.fetch.xwatch_discovery import (
    ddg_circuit_status,
    reset_ddg_circuit,
    search_tweet_urls,
)


class TestCircuitBreaker:
    def setup_method(self):
        reset_ddg_circuit()

    def teardown_method(self):
        reset_ddg_circuit()

    def test_anomaly_opens_circuit(self):
        anomaly_html = '<div class="anomaly-modal">bots use duckduckgo too</div>'

        def fake_get(*a, **kw):
            r = MagicMock()
            r.status_code = 200
            r.text = anomaly_html
            r.raise_for_status = MagicMock()
            return r

        with patch("moni.fetch.xwatch_discovery.httpx.get", fake_get), \
             patch("moni.fetch.xwatch_discovery.time.sleep"):
            urls = search_tweet_urls("q", max_results=5, circuit_minutes=60)
        assert urls == []
        st = ddg_circuit_status()
        assert st["open"] is True
        assert st["last_status"] == "anomaly"

        # Second call short-circuits without network
        with patch("moni.fetch.xwatch_discovery.httpx.get") as mock_get:
            urls2 = search_tweet_urls("q2")
            mock_get.assert_not_called()
        assert urls2 == []
        assert ddg_circuit_status()["last_status"] == "circuit_open"


class TestCheckXwatch:
    def test_synd_ok_marks_ok(self):
        fake_tweet = {
            "id_str": "20",
            "text": "just setting up my twttr",
            "user": {"screen_name": "jack"},
        }
        with patch(
            "moni.fetch.xwatch_syndication.fetch_tweet",
            return_value=fake_tweet,
        ), patch(
            "moni.fetch.xwatch_discovery.search_tweet_urls",
            return_value=["https://x.com/u/status/1"],
        ), patch(
            "moni.fetch.xwatch_discovery.ddg_circuit_status",
            return_value={"open": False, "remaining_sec": 0, "last_status": "ok"},
        ):
            # Avoid DB dependency noise — passive may fail gracefully
            r = check_xwatch(probe_ddg=True, probe_synd=True)
        assert r.ok is True
        assert r.source == "xwatch"
        assert r.detail["syndication"]["ok"] is True

    def test_synd_fail_marks_not_ok(self):
        with patch(
            "moni.fetch.xwatch_syndication.fetch_tweet",
            return_value=None,
        ):
            r = check_xwatch(probe_ddg=False, probe_synd=True)
        assert r.ok is False
        assert r.detail["syndication"]["ok"] is False
