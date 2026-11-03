"""Tests for moni.web.security LocalOriginGuard middleware and helpers."""

from __future__ import annotations

import os
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from moni.web.security import (
    DEFAULT_ALLOWED_HOSTS,
    ERROR_CODE,
    REASON_FETCH_SITE,
    REASON_HOST,
    REASON_ORIGIN,
    LocalOriginGuard,
    allowed_hosts,
    check_request,
    hostname_of,
)
from moni.web.server import create_app


def test_hostname_of_extraction() -> None:
    assert hostname_of("127.0.0.1:8900") == "127.0.0.1"
    assert hostname_of("127.0.0.1") == "127.0.0.1"
    assert hostname_of("localhost:8901") == "localhost"
    assert hostname_of("[::1]:8900") == "::1"
    assert hostname_of("evil.com:8900") == "evil.com"
    assert hostname_of("bad user@evil.com") == ""
    assert hostname_of("user:pass@127.0.0.1:8900") == ""
    assert hostname_of("") == ""
    assert hostname_of("   ") == ""



def test_allowed_hosts_environment_override() -> None:
    assert "127.0.0.1" in allowed_hosts()
    assert "testserver" in allowed_hosts()

    with mock.patch.dict(os.environ, {"MONI_ALLOWED_HOSTS": "moni.internal, 192.168.1.50:8900"}):
        hosts = allowed_hosts()
        assert "moni.internal" in hosts
        assert "192.168.1.50:8900" in hosts or "192.168.1.50" in hosts


def test_check_request_pure_matrix() -> None:
    # 1. DNS Rebinding via Host
    assert check_request("GET", {"host": "evil.example.com"}) == REASON_HOST
    assert check_request("POST", {"host": "rebind.attacker.org"}) == REASON_HOST

    # 2. Safe methods pass if host is allowed, regardless of origin/fetch-site
    assert check_request("GET", {"host": "127.0.0.1:8900", "origin": "https://evil.example"}) is None
    assert (
        check_request(
            "HEAD",
            {"host": "localhost:8900", "sec-fetch-site": "cross-site"},
        )
        is None
    )

    # 3. Unsafe methods - Non-browser request (no origin, no sec-fetch-site) passes
    assert check_request("POST", {"host": "127.0.0.1:8900"}) is None
    assert check_request("DELETE", {"host": "localhost:8900"}) is None

    # 4. Unsafe methods - Sec-Fetch-Site checking
    assert (
        check_request(
            "POST",
            {"host": "127.0.0.1:8900", "sec-fetch-site": "cross-site"},
        )
        == REASON_FETCH_SITE
    )
    assert (
        check_request(
            "POST",
            {"host": "127.0.0.1:8900", "sec-fetch-site": "same-site"},
        )
        == REASON_FETCH_SITE
    )
    assert (
        check_request(
            "POST",
            {"host": "127.0.0.1:8900", "sec-fetch-site": "same-origin"},
        )
        is None
    )
    assert (
        check_request(
            "POST",
            {"host": "127.0.0.1:8900", "sec-fetch-site": "none"},
        )
        is None
    )

    # 5. Unsafe methods - Origin matching
    assert (
        check_request(
            "POST",
            {"host": "127.0.0.1:8900", "origin": "null"},
        )
        == REASON_ORIGIN
    )
    assert (
        check_request(
            "POST",
            {"host": "127.0.0.1:8900", "origin": "https://evil.example"},
        )
        == REASON_ORIGIN
    )
    assert (
        check_request(
            "POST",
            {"host": "127.0.0.1:8900", "origin": "http://127.0.0.1:8900"},
        )
        is None
    )
    # Origin port mismatch
    assert (
        check_request(
            "POST",
            {"host": "127.0.0.1:8900", "origin": "http://127.0.0.1:8901"},
        )
        == REASON_ORIGIN
    )


def test_integration_against_app() -> None:
    app = create_app()
    client = TestClient(app)

    # Health (GET) with normal host
    resp = client.get("/health")
    assert resp.status_code == 200

    # DNS rebinding: Bad Host rejected on GET
    resp = client.get("/health", headers={"Host": "evil.example.com"})
    assert resp.status_code == 403
    assert resp.json()["code"] == ERROR_CODE
    assert resp.json()["details"]["reason"] == REASON_HOST

    # CSRF: cross-origin POST rejected
    resp = client.post(
        "/api/service/stop",
        headers={
            "Origin": "https://malicious-site.example",
            "Content-Type": "text/plain",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == ERROR_CODE
    assert resp.json()["details"]["reason"] == REASON_ORIGIN

    # Sec-Fetch-Site cross-site rejected
    resp = client.post(
        "/api/service/stop",
        headers={
            "Sec-Fetch-Site": "cross-site",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["details"]["reason"] == REASON_FETCH_SITE

    # Legitimate same-origin browser request allowed
    # (Note: TestClient default host is testserver)
    resp = client.post(
        "/api/service/status",  # checking a harmless or nonexistent endpoint to see it passes guard
        headers={
            "Host": "testserver",
            "Origin": "http://testserver",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    # Passed through guard (either 200, 404, or 405, NOT 403 forbidden_origin)
    assert resp.status_code != 403

    # Legitimate non-browser CLI/curl request allowed (no Origin or Sec-Fetch-Site)
    resp = client.post(
        "/api/service/status",
        headers={"Host": "testserver"},
    )
    assert resp.status_code != 403
