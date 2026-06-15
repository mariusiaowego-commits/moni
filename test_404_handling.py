#!/usr/bin/env python3
"""Test 404 handling: mock a fetcher that returns 404, verify no crash."""
import sys, unittest
from unittest.mock import patch, MagicMock
sys.path.insert(0, "/Users/mt16/dev/moni/wt/t1-db")

from moni.fetch.sources.kr_rss import Fetcher36krRSS
import httpx

class Test404Handling(unittest.TestCase):
    def test_404_returns_empty_list(self):
        """A source returning 404 should return empty list, not crash."""
        fetcher = Fetcher36krRSS()

        # Build a realistic HTTP 404 response
        mock_request = MagicMock()
        mock_request.url = "https://36kr.com/feed"
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("httpx.get", side_effect=httpx.HTTPStatusError("404", request=mock_request, response=mock_response)):
            result = fetcher.fetch()
            self.assertEqual(result, [], f"404 should return empty list, got {result}")

        print("  [PASS] 404 returns empty list, no crash")

    def test_timeout_returns_empty_list(self):
        """A source timing out should return empty list, not crash."""
        fetcher = Fetcher36krRSS()

        with patch("httpx.get", side_effect=httpx.TimeoutException("timeout")):
            result = fetcher.fetch()
            self.assertEqual(result, [])
        print("  [PASS] timeout returns empty list, no crash")


if __name__ == "__main__":
    print("Testing 404 / error handling...")
    suite = unittest.TestLoader().loadTestsFromTestCase(Test404Handling)
    runner = unittest.TextTestRunner(verbosity=0)
    runner.run(suite)
