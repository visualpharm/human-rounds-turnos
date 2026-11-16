"""test_admin_login_throttle.py — runtime security test for admin login throttling.

This test proves that admin login throttling (IP-based rate limiting) actually
works through the real authentication service boundary using the fixed
turnstile.client_ip_from() from commit 9085b7d3.

The critical invariant: login attempts MUST be throttled by the VERIFIED peer
IP from proxy_trust, not by forgeable headers. Before the security fix, an
attacker could bypass throttling by sending requests with different CF-Connecting-IP
or X-Forwarded-For values, as client_ip_from() trusted those headers directly.

This test exercises the complete login flow through the pure function boundary
(admin_auth.login_result) to prove:
  1. Multiple failed logins from the same verified IP get throttled (429)
  2. Different verified IPs have independent throttle counters
  3. Forged headers cannot bypass throttling (the security fix)

Run:  TURNOS_FAKE_AI=1 TURNOS_ADMIN_PASS=test-pass TURNOS_TURNSTILE_SECRET=1x1 python3 tests/test_admin_login_throttle.py
"""

import itertools
import json
import os
import sys
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.core import proxy_trust  # noqa: E402


_NAMES = itertools.count()


def _login_request(headers=None, client_host="127.0.0.1", json_body=None):
    """Helper to call admin_auth.login_result directly (pure function)."""
    if json_body is None:
        # A fresh name per call: the account counter (security review 2026-11, finding 14)
        # must not hide what these tests pin, the per-IP one.
        json_body = {"usuario": "admin" + str(next(_NAMES)), "password": "wrong-password"}
    if headers is None:
        headers = {}
    return admin_auth.login_result(
        headers, client_host, json.dumps(json_body).encode("utf-8")
    )


class TestDbDownIsNotInvalidCredentials(unittest.TestCase):
    """Un corte de base NO es "credenciales inválidas" (responsable del producto, 2026-09-15):
    login_result responde 503 base_datos — nunca el 401 credenciales que le
    mentiría a un usuario con contraseña perfecta — y el intento no cuenta
    para el throttle: no fue un intento de quien ingresa."""

    def setUp(self):
        admin_auth._ATTEMPTS.clear()
        self.enterContext(_env.patched_env(
            TURNOS_ASGI="1",
            TURNOS_ADMIN_USER="",
            TURNOS_ADMIN_PASS="",
            TURNOS_TURNSTILE_SECRET="",
            TURNOS_ADMIN_TOTP_SECRET="",
        ))

    def tearDown(self):
        admin_auth._ATTEMPTS.clear()

    def test_db_down_answers_503_base_datos(self):
        from app.core import db as core_db
        from app.core import db_operators
        with mock.patch.object(
            db_operators, "operator_verify_password",
            side_effect=core_db.DbUnavailable("caída simulada"),
        ):
            r = _login_request(json_body={"usuario": "alguien", "password": "la-buena-o-mala"})
        self.assertEqual(r.status, 503)
        self.assertEqual(r.body.get("error"), "base_datos")
        self.assertEqual(admin_auth._ATTEMPTS.get("127.0.0.1", []), [])


class TestAdminLoginThrottleRuntime(unittest.TestCase):
    """Runtime security through the same login service called by the ASGI route."""

    def setUp(self):
        # Clear throttle state FIRST before doing anything else
        admin_auth._ATTEMPTS.clear()

        # The fixture puts the whole environment back after the test
        self.enterContext(_env.patched_env({"TURNOS_PROXY_TRUST_SECRET": "test-secret-for-login-throttle", "TURNOS_ASGI": "1", "TURNOS_ADMIN_USER": "admin", "TURNOS_ADMIN_PASS": "test-login-throttle-pass", "TURNOS_TURNSTILE_SECRET": "", "TURNOS_ADMIN_TOTP_SECRET": ""}))
        # Clear again after setting environment (defensive)
        admin_auth._ATTEMPTS.clear()

    def tearDown(self):
        admin_auth._ATTEMPTS.clear()

    def test_throttle_counters_are_per_ip(self):
        """Different verified IPs have independent throttle counters."""
        ip1 = "203.0.113.10"
        ip2 = "203.0.113.20"

        # Make 8 failed attempts from IP1 (below the threshold that triggers on 9)
        for _ in range(8):
            headers = {proxy_trust.HEADER: proxy_trust.sign(ip1)}
            result = _login_request(headers=headers)
            self.assertEqual(result.status, 401, result.body)

        # IP1 gets throttled on 9th attempt
        headers = {proxy_trust.HEADER: proxy_trust.sign(ip1)}
        result = _login_request(headers=headers)
        self.assertEqual(result.status, 429, result.body)

        # Make 8 failed attempts from IP2 (below threshold)
        for _ in range(8):
            headers = {proxy_trust.HEADER: proxy_trust.sign(ip2)}
            result = _login_request(headers=headers)
            self.assertEqual(result.status, 401, result.body)

        # IP2 gets throttled on 9th attempt (independent counter)
        headers = {proxy_trust.HEADER: proxy_trust.sign(ip2)}
        result = _login_request(headers=headers)
        self.assertEqual(result.status, 429, result.body)

    def test_throttle_persists_across_requests_from_same_ip(self):
        """Throttle state persists across multiple requests from the same IP."""
        ip = "203.0.113.30"

        # Make 9 failed attempts (exceeds threshold of 8)
        for i in range(9):
            headers = {proxy_trust.HEADER: proxy_trust.sign(ip)}
            result = _login_request(headers=headers)
            if i < 8:
                self.assertEqual(result.status, 401, result.body)
            else:
                # 9th attempt is throttled (8 attempts already recorded)
                self.assertEqual(result.status, 429, result.body)

        # Verify throttling persists on subsequent requests
        for _ in range(3):
            headers = {proxy_trust.HEADER: proxy_trust.sign(ip)}
            result = _login_request(headers=headers)
            self.assertEqual(result.status, 429, result.body)

    def test_successful_login_clears_throttle_for_that_ip(self):
        """A successful login clears the throttle counter for that IP."""
        ip = "203.0.113.40"

        # Make 7 failed attempts (below threshold)
        for _ in range(7):
            headers = {proxy_trust.HEADER: proxy_trust.sign(ip)}
            result = _login_request(headers=headers)
            self.assertEqual(result.status, 401, result.body)

        # Successful login should clear the counter
        headers = {proxy_trust.HEADER: proxy_trust.sign(ip)}
        result = _login_request(
            headers=headers,
            json_body={
                "usuario": "admin",
                "password": "test-login-throttle-pass"
            }
        )
        self.assertEqual(result.status, 200, result.body)

        # Now failed attempts should NOT be throttled (counter was cleared)
        headers = {proxy_trust.HEADER: proxy_trust.sign(ip)}
        result = _login_request(headers=headers)
        self.assertEqual(result.status, 401, result.body)

    def test_forged_headers_cannot_bypass_throttle(self):
        """CRITICAL: Forgeable headers cannot bypass throttling.

        Before the security fix (commit 9085b7d3), an attacker could bypass
        throttling by rotating through different CF-Connecting-IP or
        X-Forwarded-For values. After the fix, only the verified peer from
        proxy_trust is used for throttling.
        """
        ip = "203.0.113.50"

        # Make 8 failed attempts from the verified IP
        for _ in range(8):
            headers = {proxy_trust.HEADER: proxy_trust.sign(ip)}
            result = _login_request(headers=headers)
            self.assertEqual(result.status, 401, result.body)

        # Try to bypass with forged CF-Connecting-IP header
        headers = {
            proxy_trust.HEADER: proxy_trust.sign(ip),
            "CF-Connecting-IP": "1.2.3.4",
        }
        result = _login_request(headers=headers)
        # Should still count against the same verified IP (9th attempt = throttled)
        self.assertEqual(result.status, 429, result.body)

    def test_multiple_different_forged_ips_same_throttle(self):
        """All requests with the same verified IP share one throttle counter.

        Even if the request includes different CF-Connecting-IP or X-Forwarded-For
        values, they must all be throttled together under the verified peer IP.
        This proves the security fix is working.
        """
        verified_ip = "203.0.113.60"

        # Make 8 failed attempts with different forged headers (below threshold)
        forged_ips = ["1.2.3.4", "5.6.7.8", "9.9.9.9", "10.10.10.10",
                      "11.11.11.11", "12.12.12.12", "13.13.13.13", "14.14.14.14"]

        for forged_ip in forged_ips:
            headers = {
                proxy_trust.HEADER: proxy_trust.sign(verified_ip),
                "CF-Connecting-IP": forged_ip,
            }
            result = _login_request(headers=headers)
            self.assertEqual(result.status, 401, result.body)

        # 9th attempt is throttled (regardless of forged header value)
        headers = {
            proxy_trust.HEADER: proxy_trust.sign(verified_ip),
            "CF-Connecting-IP": "15.15.15.15",
        }
        result = _login_request(headers=headers)
        self.assertEqual(result.status, 429, result.body)

    def test_throttle_without_verified_peer_uses_client_host(self):
        """When no verified peer is available, throttling uses client_host fallback.

        This happens in local/dev mode without a proxy. The test verifies that
        throttling still works in this scenario.
        """
        # Make requests without the proxy_trust header (uses client_host)
        client_host = "192.168.1.100"

        # Make 9 failed attempts (exceeds threshold of 8)
        for i in range(9):
            result = _login_request(headers={}, client_host=client_host)
            if i < 8:
                self.assertEqual(result.status, 401, result.body)
            else:
                # 9th attempt is throttled (8 attempts already recorded)
                self.assertEqual(result.status, 429, result.body)

    def test_throttle_with_valid_signature_but_different_ip(self):
        """Throttling is keyed by the verified IP value, not just signature validity."""
        ip1 = "203.0.113.70"
        ip2 = "203.0.113.71"

        # Make 8 failed attempts from IP1
        for _ in range(8):
            headers = {proxy_trust.HEADER: proxy_trust.sign(ip1)}
            result = _login_request(headers=headers)
            self.assertEqual(result.status, 401, result.body)

        # IP1 should be throttled on 9th attempt
        headers = {proxy_trust.HEADER: proxy_trust.sign(ip1)}
        result = _login_request(headers=headers)
        self.assertEqual(result.status, 429, result.body)

        # IP2 should NOT be throttled (different IP value)
        headers = {proxy_trust.HEADER: proxy_trust.sign(ip2)}
        result = _login_request(headers=headers)
        self.assertEqual(result.status, 401, result.body)


    def test_throttle_map_prunes_expired_but_keeps_live_counters(self):
        """Past admin_auth._MAX_TRACKED_IPS entries, recording prunes only
        EXPIRED entries; a live counter (inside the window) keeps throttling.

        `_throttled` prunes one IP's list when that IP comes back, but an IP
        that never returns used to leave its entry forever — the map grew
        without a ceiling in a long-lived process. The cap prunes
        fully-expired entries on record; live counters survive.
        """
        live_ip = "203.0.113.81"
        for _ in range(8):
            headers = {proxy_trust.HEADER: proxy_trust.sign(live_ip)}
            self.assertEqual(_login_request(headers=headers).status, 401)

        # Seed the map up to the cap with fully-expired one-shot entries.
        stale = {f"10.{i // 65536 % 256}.{i // 256 % 256}.{i % 256}":
                 [time.time() - admin_auth._WINDOW - 1]
                 for i in range(admin_auth._MAX_TRACKED_IPS - 1)}
        admin_auth._ATTEMPTS.update(stale)
        self.assertGreaterEqual(len(admin_auth._ATTEMPTS), admin_auth._MAX_TRACKED_IPS)

        # One more failed login from a fresh IP triggers the prune-then-insert.
        fresh_ip = "203.0.113.82"
        headers = {proxy_trust.HEADER: proxy_trust.sign(fresh_ip)}
        result = _login_request(headers=headers)
        self.assertEqual(result.status, 401, result.body)
        self.assertEqual({k for k in admin_auth._ATTEMPTS if not k.startswith("u|")},
                         {live_ip, fresh_ip})

        # The live counter kept its 8 attempts: the 9th from it is 429.
        headers = {proxy_trust.HEADER: proxy_trust.sign(live_ip)}
        result = _login_request(headers=headers)
        self.assertEqual(result.status, 429, result.body)


if __name__ == "__main__":
    unittest.main()
