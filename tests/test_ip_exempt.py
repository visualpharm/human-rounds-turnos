"""test_ip_exempt.py — ip_exempt.py (excepción por IP al Turnstile del login
de admin, 2026-08-03).

No depende de una base viva: config.py cae a env/.env cuando la DB no
responde (mismo patrón que test_system_status.py / test_settings_admin.py).
ENV_PATH se apaga para no leer el .env real del checkout.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_ip_exempt.py"
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
from app.core import proxy_trust  # noqa: E402
from app.auth import ip_exempt    # noqa: E402


class FakeHandler:
    def __init__(self, headers=None, peer="203.0.113.9"):
        self.headers = headers or {}
        self.client_address = (peer, 12345)


def _exento(request):
    return ip_exempt.exento_from(request.headers, request.client_address[0])


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self._env_path = config.ENV_PATH
        config.ENV_PATH = ""

    def tearDown(self):
        config.ENV_PATH = self._env_path
        config._reset_cache_para_tests()


class NormalizarTestCase(ConfigTestCase):
    def test_ipv4_valida(self):
        ips, err = ip_exempt.parse_lista("203.0.113.36")
        self.assertIsNone(err)
        self.assertEqual(ips, ["203.0.113.36"])

    def test_ipv6_se_comprime(self):
        ips, err = ip_exempt.parse_lista("2001:0db8:8cca:0000:39a2:042e:e848:3761")
        self.assertIsNone(err)
        self.assertEqual(ips, ["2001:db8:8cca:0:39a2:42e:e848:3761"])

    def test_v4_mapped_ipv6_se_normaliza_a_v4(self):
        ips, err = ip_exempt.parse_lista("::ffff:127.0.0.1")
        self.assertIsNone(err)
        self.assertEqual(ips, ["127.0.0.1"])

    def test_cidr_se_rechaza(self):
        ips, err = ip_exempt.parse_lista("203.0.113.0/24")
        self.assertIsNone(ips)
        self.assertIn("203.0.113.0/24", err)

    def test_basura_se_rechaza(self):
        ips, err = ip_exempt.parse_lista("no-es-una-ip")
        self.assertIsNone(ips)
        self.assertIsNotNone(err)

    def test_separa_por_linea_y_por_coma_y_dedup(self):
        ips, err = ip_exempt.parse_lista("127.0.0.1\n::1, 127.0.0.1")
        self.assertIsNone(err)
        self.assertEqual(ips, ["127.0.0.1", "::1"])

    def test_vacio_es_lista_vacia_sin_error(self):
        ips, err = ip_exempt.parse_lista("")
        self.assertIsNone(err)
        self.assertEqual(ips, [])


class ListExemptTestCase(ConfigTestCase):
    def test_sin_configurar_devuelve_el_seed_por_defecto(self):
        self.assertEqual(set(ip_exempt.list_exempt()), set(ip_exempt.DEFAULT_IPS))

    def test_config_guardada_gana_sobre_el_default(self):
        import unittest.mock as mock
        with mock.patch.object(config, "_db_overrides",
                                return_value={ip_exempt.CONFIG_KEY: "10.0.0.9"}):
            self.assertEqual(ip_exempt.list_exempt(), ["10.0.0.9"])


class ExentoTestCase(ConfigTestCase):
    """exento() after the 2026-08-08 retirement of non-loopback exemptions:
    only a VERIFIED loopback peer skips Turnstile, in BOTH serving
    topologies. Forwarded-IP headers (CF-Connecting-IP / X-Forwarded-For)
    never count — with no CDN in front they are client-suppliable end to end.

    Each test declares the topology it exercises: `test_legacy_*` runs with
    TURNOS_ASGI unset (socket peer is authoritative), `test_asgi_*` with
    TURNOS_ASGI=1 (only app.py's HMAC-signed marker is trusted, absent or
    forged marker fails closed). Both halves run regardless of which mode the
    surrounding `unittest discover` pass was started in; the env is saved and
    restored per test."""

    def setUp(self):
        super().setUp()
        # 203.0.113.77 stands in for a remote entry of the retired kind
        # (the product owner's home IP was seeded like this until 2026-08-08): the parser
        # and the panel still accept it, but it must never exempt anyone.
        _env.set_env(self, {"TURNOS_ADMIN_IP_EXEMPT": "127.0.0.1,::1,203.0.113.77", "TURNOS_PROXY_TRUST_SECRET": "test-proxy-trust-secret"})

    def _legacy(self):
        _env.set_env(self, TURNOS_ASGI=None)

    def _asgi(self):
        _env.set_env(self, TURNOS_ASGI="1")

    # ---- legacy topology: the public port is legacy's own socket ----------

    def test_legacy_loopback_socket_peer_is_exempt(self):
        self._legacy()
        self.assertTrue(_exento(FakeHandler(peer="127.0.0.1")))

    def test_legacy_loopback_v6_socket_peer_is_exempt(self):
        self._legacy()
        self.assertTrue(_exento(FakeHandler(peer="::1")))

    def test_legacy_forged_header_claiming_loopback_is_not_exempt(self):
        self._legacy()
        for headers in ({"CF-Connecting-IP": "127.0.0.1"},
                        {"X-Forwarded-For": "127.0.0.1"}):
            h = FakeHandler(headers=headers, peer="8.8.8.8")
            self.assertFalse(_exento(h), headers)

    def test_legacy_forged_header_claiming_listed_ip_is_not_exempt(self):
        # The exact spoof that retired the header branch: any public client
        # could send CF-Connecting-IP with a listed IP and skip Turnstile.
        self._legacy()
        for headers in ({"CF-Connecting-IP": "203.0.113.77"},
                        {"X-Forwarded-For": "203.0.113.77, 6.6.6.6"}):
            h = FakeHandler(headers=headers, peer="8.8.8.8")
            self.assertFalse(_exento(h), headers)

    def test_legacy_listed_remote_ip_at_the_socket_is_not_exempt(self):
        # Even the GENUINE remote peer no longer skips Turnstile: non-loopback
        # entries are inert since the retirement, loopback is the only grant.
        self._legacy()
        self.assertFalse(_exento(FakeHandler(peer="203.0.113.77")))

    # ---- ASGI topology: only app.py's signed marker attests the peer ------

    def test_asgi_signed_loopback_marker_is_exempt(self):
        self._asgi()
        h = FakeHandler(peer="127.0.0.1",
                        headers={proxy_trust.HEADER: proxy_trust.sign("127.0.0.1")})
        self.assertTrue(_exento(h))

    def test_asgi_absent_marker_fails_closed(self):
        # Behind the proxy the socket peer is ALWAYS the proxy's loopback —
        # without a valid marker that must not read as "local".
        self._asgi()
        self.assertFalse(_exento(FakeHandler(peer="127.0.0.1")))

    def test_asgi_forged_marker_fails_closed(self):
        self._asgi()
        h = FakeHandler(peer="127.0.0.1",
                        headers={proxy_trust.HEADER: "127.0.0.1|" + "0" * 64})
        self.assertFalse(_exento(h))

    def test_asgi_forged_cf_header_fails_closed(self):
        self._asgi()
        h = FakeHandler(peer="127.0.0.1",
                        headers={"CF-Connecting-IP": "127.0.0.1",
                                 "X-Forwarded-For": "127.0.0.1"})
        self.assertFalse(_exento(h))

    def test_asgi_signed_marker_for_listed_remote_ip_is_not_exempt(self):
        # A marker legitimately signed for a listed REMOTE peer still grants
        # nothing: the exemption is loopback-only, not list-membership-only.
        self._asgi()
        h = FakeHandler(peer="127.0.0.1",
                        headers={proxy_trust.HEADER: proxy_trust.sign("203.0.113.77")})
        self.assertFalse(_exento(h))

    def test_empty_list_never_exempts_anyone(self):
        import unittest.mock as mock
        self._legacy()
        with mock.patch.object(ip_exempt, "list_exempt", return_value=[]):
            self.assertFalse(_exento(FakeHandler(peer="127.0.0.1")))


if __name__ == "__main__":
    unittest.main()
