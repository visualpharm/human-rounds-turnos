"""test_proxy_trust.py — Tier 4 slice 1 (docs/roadmap/tier4-replatform-
design.md, risk added during the slice 0 build: "Every peer-based 'this
request is genuinely local' check ... sees the proxy's own loopback
connection as the TCP peer for ALL traffic once TURNOS_ASGI=1").

Tests `proxy_trust.py` itself (sign/real_peer, forgeable/unforgeable
signature) and the THREE real call sites that stopped reading
`handler.client_address[0]` directly: `ip_exempt.exento()`,
`turnstile.client_ip()` (last-resort branch) and
`accounts._test_auth_enabled()`. The invariant for all three:
  - WITHOUT the trust header (legacy running alone, no proxy in front —
    today's default in all of Coolify): behavior IDENTICAL to always, the
    socket peer rules.
  - WITH the header, correctly signed: the real peer the proxy saw rules,
    NEVER the proxy's own loopback (which is what `client_address` would
    hold in that case).
  - WITH the header but forged/absent and the TURNOS_ASGI flag ON (the
    real scenario behind the proxy): FAILS CLOSED — real_peer returns ""
    (non-local), NEVER the proxy's loopback, so neither ip_exempt nor the
    RENAPER stub activates. This is the independent-review finding of
    2026-08-08: it used to fall back to the socket peer (the proxy's
    loopback), which reopened the bypass for any request without a valid
    marker.
"""

import os
import sys
import time
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.cuentas import accounts  # noqa: E402
from app.auth import mode as auth_mode  # noqa: E402
from app.core import config  # noqa: E402
from app.auth import ip_exempt  # noqa: E402
from app.core import proxy_trust  # noqa: E402

class FakeHandler:
    """Loopback at the socket (what legacy ALWAYS sees behind asgi_main.py's
    internal proxy) — with or without the proxy_trust header on top,
    depending on the test."""

    def __init__(self, headers=None, peer="127.0.0.1"):
        self.headers = headers or {}
        self.client_address = (peer, 54321)


def _real_peer(request):
    return proxy_trust.real_peer_from(request.headers, request.client_address[0])


def _exento(request):
    return ip_exempt.exento_from(request.headers, request.client_address[0])


def _test_auth_enabled(request):
    return accounts._test_auth_enabled(request.headers, request.client_address[0])


class ProxyTrustCoreTestCase(unittest.TestCase):
    def setUp(self):
        self._orig_env_path = config.ENV_PATH
        config.ENV_PATH = ""
        _env.set_env(self, {"TURNOS_CONFIG_LOCK": "1", "TURNOS_PROXY_TRUST_SECRET": "secreto-de-test-proxy-trust"})
        config._reset_cache_para_tests()
        # Every test here declares the topology it exercises (sets or clears
        # TURNOS_ASGI itself, through the fixture); whatever mode the
        # surrounding discover pass was started in is restored afterwards so
        # it never leaks between tests -- a bare `os.environ.pop` in a finally
        # used to DELETE the global flag of a TURNOS_ASGI=1 CI pass for every
        # later test module.

    def tearDown(self):
        config.ENV_PATH = self._orig_env_path
        config._reset_cache_para_tests()

    def test_no_header_falls_back_to_socket_peer(self):
        # Subject: LEGACY topology (flag off) — the socket peer rules.
        _env.set_env(self, TURNOS_ASGI=None)
        h = FakeHandler(peer="203.0.113.9")
        self.assertEqual(_real_peer(h), "203.0.113.9")

    def test_correctly_signed_header_wins_over_the_socket(self):
        _env.set_env(self, TURNOS_ASGI="1")
        real = "198.51.100.7"
        h = FakeHandler(peer="127.0.0.1",  # the proxy is ALWAYS this for legacy
                        headers={proxy_trust.HEADER: proxy_trust.sign(real)})
        self.assertEqual(_real_peer(h), real)

    def test_asgi_off_forged_signature_falls_back_to_socket(self):
        # Flag off (default): no proxy, the marker is never even read, the
        # socket peer rules as always — legacy behavior intact.
        _env.set_env(self, TURNOS_ASGI=None)
        h = FakeHandler(peer="203.0.113.9",
                        headers={proxy_trust.HEADER: "198.51.100.7|" + "0" * 64})
        self.assertEqual(_real_peer(h), "203.0.113.9")

    def test_asgi_off_ignores_incoming_marker(self):
        # Flag off: a client that INJECTS the header straight at legacy
        # controls nothing — it is ignored, the socket peer wins.
        _env.set_env(self, TURNOS_ASGI=None)
        h = FakeHandler(peer="203.0.113.9",
                        headers={proxy_trust.HEADER: proxy_trust.sign("8.8.8.8")})
        self.assertEqual(_real_peer(h), "203.0.113.9")

    def test_asgi_on_forged_signature_fails_closed(self):
        # Flag on (behind the proxy): without a valid signature it does NOT
        # fall back to the socket's loopback — it returns "" (non-local).
        # This is the fix for the 2026-08-08 review finding.
        _env.set_env(self, TURNOS_ASGI="1")
        h = FakeHandler(peer="127.0.0.1",
                        headers={proxy_trust.HEADER: "198.51.100.7|" + "0" * 64})
        self.assertEqual(_real_peer(h), "")

    def test_asgi_on_absent_marker_fails_closed(self):
        _env.set_env(self, TURNOS_ASGI="1")
        h = FakeHandler(peer="127.0.0.1", headers={})
        self.assertEqual(_real_peer(h), "")

    def test_asgi_on_valid_signature_wins(self):
        _env.set_env(self, TURNOS_ASGI="1")
        real = "198.51.100.7"
        h = FakeHandler(peer="127.0.0.1",
                        headers={proxy_trust.HEADER: proxy_trust.sign(real)})
        self.assertEqual(_real_peer(h), real)

    def test_asgi_on_without_a_real_secret_marker_is_not_trusted(self):
        # Without a REAL signing secret, a marker signed with the fixed
        # literal (forgeable — it's in the source) is NOT accepted behind
        # the proxy: fails closed even though the signature "matches"
        # against that same literal.
        _env.set_env(self, TURNOS_ASGI="1", TURNOS_PROXY_TRUST_SECRET=None,
                     TURNOS_ADMIN_SECRET=None, TURNOS_ADMIN_PASS=None)
        config._reset_cache_para_tests()
        signed = proxy_trust.sign("198.51.100.7")  # signed with the literal
        h = FakeHandler(peer="127.0.0.1", headers={proxy_trust.HEADER: signed})
        self.assertEqual(_real_peer(h), "")

    def test_header_signed_with_another_secret_is_ignored(self):
        _env.set_env(self, TURNOS_ASGI="1")
        real = "198.51.100.7"
        signed = proxy_trust.sign(real)
        _env.set_env(self, {"TURNOS_PROXY_TRUST_SECRET": "otro-secreto-distinto"})
        config._reset_cache_para_tests()
        h = FakeHandler(peer="127.0.0.1", headers={proxy_trust.HEADER: signed})
        self.assertEqual(_real_peer(h), "")  # another secret's signature -> fail-closed


class IpExemptProxyCorrectTestCase(unittest.TestCase):
    """ip_exempt.exento(): the loopback Turnstile bypass has to keep
    working WITHOUT a proxy, and NOT activate for a real public visitor
    arriving behind the internal proxy (socket peer == loopback, real
    peer != loopback)."""

    def setUp(self):
        # Without a database (or with one shared by other test modules of
        # the same serial `unittest discover` process — see
        # _routes_fixture.py's comment on the deliberate DATABASE_URL leak
        # between modules), `ip_exempt.list_exempt()` falls back on its own
        # to the DEFAULT_IPS seed — no need to write anything to the DB for
        # this test, and writing (config.guardar) would mean depending on a
        # connection this file doesn't need.
        self._orig_env_path = config.ENV_PATH
        config.ENV_PATH = ""
        config._reset_cache_para_tests()
        _env.set_env(self, {"TURNOS_CONFIG_LOCK": "1", "TURNOS_PROXY_TRUST_SECRET": "secreto-de-test-proxy-trust"})
        # Same per-test topology contract as ProxyTrustCoreTestCase: each test
        # sets or clears TURNOS_ASGI for itself; the discover pass's own value
        # is restored afterwards, never popped away globally.

    def tearDown(self):
        config.ENV_PATH = self._orig_env_path
        config._reset_cache_para_tests()

    def test_no_proxy_direct_loopback_stays_exempt(self):
        # Subject: LEGACY topology (flag off) — direct loopback stays exempt.
        _env.set_env(self, TURNOS_ASGI=None)
        h = FakeHandler(peer="127.0.0.1")
        self.assertTrue(_exento(h))

    def test_behind_the_proxy_a_public_visitor_is_not_exempt(self):
        """WITHOUT this slice, this returned True for ANY public visitor
        once TURNOS_ASGI was on — see the design's risk-register entry."""
        _env.set_env(self, TURNOS_ASGI="1")
        h = FakeHandler(peer="127.0.0.1",  # what legacy sees of the proxy
                        headers={proxy_trust.HEADER: proxy_trust.sign("8.8.8.8")})
        self.assertFalse(_exento(h))

    def test_behind_the_proxy_a_remote_exempt_ip_no_longer_exempts(self):
        """2026-08-08 retirement: exento()'s non-loopback branch (which
        honored the product owner's home IP via turnstile.client_ip's headers) is GONE —
        those headers are client-suppliable with no CDN in front. Even a
        marker legitimately signed by app.py for a listed remote peer grants
        nothing now: the exemption is loopback-only. Scripted logins use the
        signed X-Turnstile-Test-Bypass header instead (turnstile.bypass_activo)."""
        _env.set_env(self, TURNOS_ASGI="1")
        remote = "203.0.113.77"
        with unittest.mock.patch.object(ip_exempt, "list_exempt",
                                        return_value=["127.0.0.1", "::1", remote]):
            h = FakeHandler(peer="127.0.0.1",
                            headers={proxy_trust.HEADER: proxy_trust.sign(remote)})
            self.assertFalse(_exento(h))

    def test_asgi_on_without_valid_marker_is_not_exempt(self):
        """END-TO-END fail-closed: flag on, a request WITHOUT a valid marker
        (absent or forged) reaching legacy with the socket peer on loopback
        is NOT exempt — before the 2026-08-08 fix it was, because real_peer
        returned the loopback."""
        _env.set_env(self, TURNOS_ASGI="1")
        absent = FakeHandler(peer="127.0.0.1")  # no marker at all
        self.assertFalse(_exento(absent))
        forged = FakeHandler(peer="127.0.0.1",
                             headers={proxy_trust.HEADER: "127.0.0.1|" + "0" * 64})
        self.assertFalse(_exento(forged))


class AccountsRenaperStubProxyCorrectTestCase(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, {"TURNOS_AUTH_MODE": "renaper", "TURNOS_RENAPER_PROVIDER": "stub", "TURNOS_RENAPER_STUB_DEPLOYED": None, "TURNOS_PROXY_TRUST_SECRET": "secreto-de-test-proxy-trust"})
        self._orig_env_path = config.ENV_PATH
        self._orig_auth_mode_cache = dict(auth_mode._cache)
        # Same per-test topology contract as ProxyTrustCoreTestCase.
        config.ENV_PATH = ""
        config._reset_cache_para_tests()
        # This class isolates proxy peer semantics. Keep the independent
        # DB-backed mode selector from inheriting an earlier test's cache.
        auth_mode._cache["v"] = "renaper"
        auth_mode._cache["t"] = time.time()

    def tearDown(self):
        auth_mode._cache.clear()
        auth_mode._cache.update(self._orig_auth_mode_cache)
        config.ENV_PATH = self._orig_env_path
        config._reset_cache_para_tests()

    def test_no_proxy_direct_loopback_still_enables_the_stub(self):
        # Subject: LEGACY topology (flag off) — direct loopback enables the stub.
        _env.set_env(self, TURNOS_ASGI=None)
        h = FakeHandler(peer="127.0.0.1")
        self.assertTrue(_test_auth_enabled(h))

    def test_behind_the_proxy_a_public_visitor_does_not_enable_the_stub(self):
        _env.set_env(self, TURNOS_ASGI="1")
        h = FakeHandler(peer="127.0.0.1",
                        headers={proxy_trust.HEADER: proxy_trust.sign("8.8.8.8"),
                                 "Host": "pinamar.humanrounds.org"})
        self.assertFalse(_test_auth_enabled(h))

    def test_asgi_on_without_valid_marker_does_not_enable_the_stub(self):
        """END-TO-END fail-closed: flag on + absent/forged marker + dev Host
        -> the RENAPER stub is NOT enabled. Before the fix, real_peer
        returned the socket's loopback and is_loopback enabled it for
        anyone."""
        _env.set_env(self, TURNOS_ASGI="1")
        absent = FakeHandler(peer="127.0.0.1", headers={"Host": "localhost:7700"})
        self.assertFalse(_test_auth_enabled(absent))
        forged = FakeHandler(peer="127.0.0.1",
                             headers={proxy_trust.HEADER: "127.0.0.1|" + "0" * 64,
                                      "Host": "localhost:7700"})
        self.assertFalse(_test_auth_enabled(forged))


class LegacyShimFailClosedTestCase(unittest.TestCase):
    """The FastAPI router transport NEVER
    injects a proxy-trust marker. So a request reaching a native route under
    TURNOS_ASGI=1 must FAIL CLOSED on every peer check: real_peer -> "",
    the loopback Turnstile exemption and the RENAPER stub stay shut, and a
    client-forged marker is rejected by the signature just the same. See
    core/proxy_trust.py's module docstring. This proves the in-process
    transport is safe: the client's own headers verbatim."""

    def setUp(self):
        # Every key and ENV_PATH this test touches is put back afterwards, so it
        # can never erase a caller's pre-existing environment nor leak
        # ENV_PATH="" across the rest of the discover run.
        self._orig_env_path = config.ENV_PATH
        config._reset_cache_para_tests()
        _env.set_env(self, {"TURNOS_PROXY_TRUST_SECRET": "secreto-de-test-proxy-trust", "TURNOS_ADMIN_IP_EXEMPT": "127.0.0.1,::1", "TURNOS_AUTH_MODE": "renaper", "TURNOS_RENAPER_PROVIDER": "stub", "TURNOS_RENAPER_STUB_DEPLOYED": None, "TURNOS_ASGI": "1"})
        config.ENV_PATH = ""
        config._reset_cache_para_tests()

    def tearDown(self):
        config.ENV_PATH = self._orig_env_path
        config._reset_cache_para_tests()

    def _shim(self, headers, peer="127.0.0.1"):
        # A minimal request duck with the exact surface `proxy_trust` (and
        # its consumers) read: `.headers.get(...)` and `.client_address`.
        # The contract under test: the
        # client's own headers verbatim and NO injected marker. The socket
        # peer is deliberately irrelevant here — an in-process
        # request.client is not necessarily loopback, but under
        # TURNOS_ASGI=1 a markerless request fails closed regardless of
        # what peer the request carries.
        base = {"Host": "localhost:7700"}
        base.update(headers or {})

        class _Req:
            def __init__(self, hdrs, client):
                self.headers = hdrs
                self.client_address = client

        return _Req(base, (peer, 54321))

    def test_markerless_shim_real_peer_is_empty(self):
        self.assertEqual(_real_peer(self._shim({})), "")

    def test_markerless_shim_is_not_turnstile_exempt(self):
        self.assertFalse(_exento(self._shim({})))

    def test_markerless_shim_does_not_enable_renaper_stub(self):
        self.assertFalse(_test_auth_enabled(self._shim({})))

    def test_client_forged_marker_on_the_shim_fails_closed(self):
        # A client CAN put the header on a request to a migrated route (the
        # shim doesn't strip it), but without the server secret the signature
        # can't verify -> "" -> no exemption, no stub.
        forged = {proxy_trust.HEADER: "127.0.0.1|" + "0" * 64}
        self.assertEqual(_real_peer(self._shim(forged)), "")
        self.assertFalse(_exento(self._shim(forged)))
        self.assertFalse(_test_auth_enabled(self._shim(forged)))


class ProxyTrustFixtureIsolationTestCase(unittest.TestCase):
    def test_ip_exempt_fixture_restores_existing_lock_secret_and_env_path(self):
        original_path = config.ENV_PATH
        with _env.patched_env(
                {"TURNOS_CONFIG_LOCK": "sentinel-lock",
                 "TURNOS_PROXY_TRUST_SECRET": "sentinel-secret"},
                clear=False):
            case = IpExemptProxyCorrectTestCase("test_no_proxy_direct_loopback_stays_exempt")
            case.setUp()
            case.tearDown()
            case.doCleanups()   # the fixture's environment restore is a cleanup, not part of tearDown
            self.assertEqual(os.environ.get("TURNOS_CONFIG_LOCK"), "sentinel-lock")
            self.assertEqual(os.environ.get("TURNOS_PROXY_TRUST_SECRET"), "sentinel-secret")
            self.assertEqual(config.ENV_PATH, original_path)


if __name__ == "__main__":
    unittest.main()
