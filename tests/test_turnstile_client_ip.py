"""test_turnstile_client_ip.py — runtime security test for turnstile.client_ip_from().

This test proves that the admin login throttling (which uses client_ip_from())
can ONLY be bypassed with a verified peer from proxy_trust, not with forgeable
headers like CF-Connecting-IP or X-Forwarded-For. This is the critical security
fix from commit 9085b7d3 that prevented login throttling bypass.

The invariant: in ASGI mode (TURNOS_ASGI=1), client_ip_from() MUST return:
  - The verified peer IP from proxy_trust.real_peer_from() when a valid signed
    X-Turnos-Proxy-Peer header is present
  - Empty string when the header is absent or forged (fail-closed)
  - NEVER the CF-Connecting-IP or X-Forwarded-For headers, which are directly
    controllable by any client hitting the origin directly

Run:  TURNOS_FAKE_AI=1 python3 -m unittest test.test_turnstile_client_ip
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth import turnstile  # noqa: E402
from app.core import proxy_trust  # noqa: E402
import _env  # noqa: E402


def _client_ip(headers, client_host="127.0.0.1"):
    return turnstile.client_ip_from(headers, client_host)


class TestTurnstileClientIpSecurity(unittest.TestCase):
    """Runtime security: client_ip_from() only trusts verified peer."""

    def setUp(self):
        _env.set_env(self, {"TURNOS_PROXY_TRUST_SECRET": "test-secret-for-turnstile-client-ip"})

    def test_asgi_off_returns_socket_peer(self):
        """Legacy topology (no ASGI): returns the socket peer directly."""
        with _env.patched_env(TURNOS_ASGI=None):
            headers = {}
            self.assertEqual(_client_ip(headers, "203.0.113.5"), "203.0.113.5")

    def test_asgi_off_ignores_all_headers(self):
        """Legacy topology: ignores all headers, returns socket peer."""
        with _env.patched_env(TURNOS_ASGI=None):
            headers = {
                "CF-Connecting-IP": "1.2.3.4",
                "X-Forwarded-For": "5.6.7.8",
                proxy_trust.HEADER: proxy_trust.sign("9.9.9.9"),
            }
            # Should return socket peer, NOT any header
            self.assertEqual(_client_ip(headers, "203.0.113.5"), "203.0.113.5")

    def test_asgi_on_no_header_returns_client_host_fallback(self):
        """ASGI mode without proxy header: returns client_host fallback."""
        with _env.patched_env({"TURNOS_ASGI": "1"}, clear=False):
            headers = {}
            # Fallback to client_host in ASGI mode when no verified peer
            self.assertEqual(_client_ip(headers, "192.168.1.100"), "192.168.1.100")

    def test_asgi_on_valid_signature_returns_verified_peer(self):
        """ASGI mode with valid signed header: returns the verified peer IP."""
        with _env.patched_env({"TURNOS_ASGI": "1"}, clear=False):
            real_ip = "198.51.100.7"
            headers = {proxy_trust.HEADER: proxy_trust.sign(real_ip)}
            # Must return the verified peer from the header signature
            self.assertEqual(_client_ip(headers, "127.0.0.1"), real_ip)

    def test_asgi_on_forged_signature_returns_client_host_fallback(self):
        """ASGI mode with forged signature: returns fallback, NOT the forged IP."""
        with _env.patched_env({"TURNOS_ASGI": "1"}, clear=False):
            forged_ip = "1.2.3.4"
            headers = {proxy_trust.HEADER: forged_ip + "|" + "0" * 64}
            # Must NOT trust the forged IP; should return fallback
            self.assertEqual(_client_ip(headers, "192.168.1.100"), "192.168.1.100")
            # Must NOT be the forged IP
            self.assertNotEqual(_client_ip(headers, "192.168.1.100"), forged_ip)

    def test_asgi_on_does_not_trust_cf_connecting_ip(self):
        """CRITICAL: CF-Connecting-IP header MUST be ignored in ASGI mode.

        This header can be set by any client hitting the origin directly. Before
        the security fix (commit 9085b7d3), this header was trusted first, which
        allowed attackers to bypass login throttling by rotating through forged
        CF-Connecting-IP values.
        """
        with _env.patched_env({"TURNOS_ASGI": "1"}, clear=False):
            headers = {"CF-Connecting-IP": "1.2.3.4"}
            # Must NOT return the CF header value
            self.assertNotEqual(_client_ip(headers, "192.168.1.100"), "1.2.3.4")
            # Should return fallback since no valid proxy_trust header
            self.assertEqual(_client_ip(headers, "192.168.1.100"), "192.168.1.100")

    def test_asgi_on_does_not_trust_x_forwarded_for(self):
        """CRITICAL: X-Forwarded-For header MUST be ignored in ASGI mode.

        Like CF-Connecting-IP, this header is fully client-controllable when the
        client hits the origin directly. Before the fix, this was the second
        fallback after CF-Connecting-IP, also allowing throttling bypass.
        """
        with _env.patched_env({"TURNOS_ASGI": "1"}, clear=False):
            headers = {"X-Forwarded-For": "5.6.7.8"}
            # Must NOT return the X-Forwarded-For value
            self.assertNotEqual(_client_ip(headers, "192.168.1.100"), "5.6.7.8")
            # Should return fallback since no valid proxy_trust header
            self.assertEqual(_client_ip(headers, "192.168.1.100"), "192.168.1.100")

    def test_asgi_on_does_not_trust_combined_forgeable_headers(self):
        """CRITICAL: Multiple forgeable headers MUST all be ignored.

        An attacker could set multiple headers trying different bypasses. All
        must be ignored; only the verified peer from proxy_trust is trusted.
        """
        with _env.patched_env({"TURNOS_ASGI": "1"}, clear=False):
            headers = {
                "CF-Connecting-IP": "1.2.3.4",
                "X-Forwarded-For": "5.6.7.8",
                # Also test with forged proxy_trust header
                proxy_trust.HEADER: "9.9.9.9|" + "0" * 64,
            }
            # Must NOT return any of the forged values
            result = _client_ip(headers, "192.168.1.100")
            self.assertNotEqual(result, "1.2.3.4")
            self.assertNotEqual(result, "5.6.7.8")
            self.assertNotEqual(result, "9.9.9.9")
            # Should return fallback since no valid proxy_trust signature
            self.assertEqual(result, "192.168.1.100")

    def test_verified_peer_overrides_all_forgeable_headers(self):
        """Valid proxy_trust signature overrides all forgeable headers.

        When a valid signed X-Turnos-Proxy-Peer header is present, it MUST be
        used even if CF-Connecting-IP or X-Forwarded-For are also present
        (which they would be in a real proxy setup). This proves the verified
        peer takes precedence.
        """
        with _env.patched_env({"TURNOS_ASGI": "1"}, clear=False):
            real_ip = "198.51.100.7"
            headers = {
                "CF-Connecting-IP": "1.2.3.4",
                "X-Forwarded-For": "5.6.7.8",
                proxy_trust.HEADER: proxy_trust.sign(real_ip),
            }
            # Must return the verified peer, NOT the forgeable headers
            self.assertEqual(_client_ip(headers, "127.0.0.1"), real_ip)
            self.assertNotEqual(_client_ip(headers, "127.0.0.1"), "1.2.3.4")
            self.assertNotEqual(_client_ip(headers, "127.0.0.1"), "5.6.7.8")

    def test_asgi_on_without_real_secret_fails_closed(self):
        """Without a real signing secret, even a correctly-formatted header is rejected.

        The signature must be verified with a REAL secret (TURNOS_PROXY_TRUST_SECRET
        or TURNOS_ADMIN_SECRET/TURNOS_ADMIN_PASS). Without it, the fallback literal
        would be forgeable since it's in the source code.
        """
        # Note: This test relies on proxy_trust.sign() using the fallback literal
        # when no secret is configured. The signature verification should fail.
        # Remove all secret sources
        with _env.patched_env(TURNOS_ASGI="1", TURNOS_PROXY_TRUST_SECRET=None,
                              TURNOS_ADMIN_SECRET=None, TURNOS_ADMIN_PASS=None):
            # Sign with the fallback literal (which is what happens when no secret is set)
            signed = proxy_trust.sign("198.51.100.7")
            headers = {proxy_trust.HEADER: signed}
            # Must NOT trust it - should return fallback
            self.assertEqual(_client_ip(headers, "192.168.1.100"), "192.168.1.100")


if __name__ == "__main__":
    unittest.main()
