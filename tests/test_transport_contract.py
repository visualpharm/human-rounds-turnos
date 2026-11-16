"""test_transport_contract.py — el contrato de transporte de WhatsApp
(Tier 6, cierre en código del runbook docs/meta-whatsapp-adoption-runbook.md
§2, tracking card 2026-08-14-turnos-tier-6-code-slice-meta-only-production-
transport-waha). Seis invariantes, cada uno con su clase:

1. WAHA remoto = CERO transporte (ni un byte de red, no-op logueado).
2. WAHA loopback = opt-in de desarrollo explícito (TURNOS_WA_PROVIDER=waha
   Y URL loopback; ninguna de las dos sola alcanza).
3. Selección de Meta: explícita o auto por token; el pin a meta sin token
   falla cerrado (no-op), nunca cae a WAHA.
4. El OTP viaja por Meta sólo como la plantilla de Authentication aprobada
   (OTP_PROVIDER=meta sin META_WA_OTP_TEMPLATE, o desconocido = no-op; el
   envío con plantilla: test_meta_wa_limits.py) y el OTP por WAHA hereda la
   restricción loopback.
5. Modo demo bloquea toda salida aunque el transporte esté configurado.
6. El cassette de Meta (tests/cassettes/meta/enviar_texto_ok.json) sigue
   existiendo y su shape sigue siendo el que _enviar_meta construye/lee —
   guarda contra una "limpieza" accidental en refactors de transporte.

Correr:  python3 -m unittest test.test_transport_contract
"""

import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
config.ENV_PATH = ""  # nunca leer el .env real del checkout (tests deterministas)

from app.notifications import otp_send  # noqa: E402
from app.ops import demo_installation  # noqa: E402

_DESTINO = "5492255111222"  # mismo fixture que test_whatsapp_chat.py
_ENV_KEYS = ("TURNOS_WA_PROVIDER", "META_WA_TOKEN", "META_WA_PHONE_NUMBER_ID",
             "OTP_WAHA_URL", "OTP_WAHA_API_KEY", "OTP_WAHA_SESSION",
             "OTP_PROVIDER", "META_WA_OTP_TEMPLATE")

CASSETTE_PATH = os.path.join(ROOT, "tests", "cassettes", "meta", "enviar_texto_ok.json")


class _Base(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, {k: None for k in _ENV_KEYS})

    @staticmethod
    def _urlopen_prohibido():
        """Patch de urllib.request.urlopen que hace FALLAR el test si algo
        intenta salir a la red — "cero transporte" se afirma literal."""
        return mock.patch(
            "urllib.request.urlopen",
            side_effect=AssertionError("transporte prohibido: urlopen fue llamado"),
        )


class _FakeResponse:
    status = 201

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b"{}"


class TestWahaRemotoCeroTransporte(_Base):
    """Invariante 1: una OTP_WAHA_URL remota no manda nada, nunca."""

    def setUp(self):
        super().setUp()
        _env.set_env(self, OTP_WAHA_URL="http://192.168.1.156:3005", OTP_WAHA_API_KEY="k")

    def test_enviar_whatsapp_directo_no_toca_la_red(self):
        with self._urlopen_prohibido():
            self.assertFalse(otp_send._enviar_whatsapp(_DESTINO, "hola"))

    def test_enviar_texto_con_optin_explicito_igual_no_manda(self):
        _env.set_env(self, TURNOS_WA_PROVIDER="waha")
        self.assertEqual(otp_send.wa_provider(), "")
        with self._urlopen_prohibido():
            self.assertFalse(otp_send.enviar_texto(_DESTINO, "hola"))

    def test_otp_por_waha_remoto_tampoco_manda(self):
        _env.set_env(self, OTP_PROVIDER="whatsapp")
        with self._urlopen_prohibido():
            self.assertFalse(otp_send.enviar_otp(_DESTINO, "123456"))

    def test_hostnames_no_loopback_rechazados(self):
        for url in ("https://waha.pinamar.gob.ar", "http://hsi-lab:3010",
                    "http://10.0.0.5:3005", "http://127x0.evil.com", ""):
            self.assertFalse(otp_send._waha_es_loopback(url), url)


class TestWahaLoopbackOptIn(_Base):
    """Invariante 2: loopback + opt-in explícito = transporte de dev."""

    def setUp(self):
        super().setUp()
        _env.set_env(self, OTP_WAHA_URL="http://127.0.0.1:3005", OTP_WAHA_API_KEY="k")

    def test_loopback_sin_optin_no_selecciona_waha(self):
        self.assertEqual(otp_send.wa_provider(), "")
        with self._urlopen_prohibido():
            self.assertFalse(otp_send.enviar_texto(_DESTINO, "hola"))

    def test_loopback_con_optin_envia(self):
        _env.set_env(self, TURNOS_WA_PROVIDER="waha")
        self.assertEqual(otp_send.wa_provider(), "waha")
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse()) as m:
            self.assertTrue(otp_send.enviar_texto(_DESTINO, "hola"))
        self.assertEqual(m.call_count, 1)
        req = m.call_args[0][0]
        self.assertTrue(req.full_url.startswith("http://127.0.0.1:3005/"))

    def test_localhost_tambien_es_loopback(self):
        for url in ("http://localhost:3005", "http://127.0.0.1:3005",
                    "http://[::1]:3005"):
            self.assertTrue(otp_send._waha_es_loopback(url), url)


class TestSeleccionMeta(_Base):
    """Invariante 3: Meta por pin explícito o por token; pin sin token
    falla cerrado (no-op) — jamás cae a WAHA."""

    def test_pin_explicito_usa_meta(self):
        _env.set_env(self, TURNOS_WA_PROVIDER="meta", META_WA_TOKEN="tok",
                     META_WA_PHONE_NUMBER_ID="123")
        with mock.patch.object(otp_send, "_enviar_meta", return_value=True) as m:
            self.assertTrue(otp_send.enviar_texto(_DESTINO, "hola"))
        m.assert_called_once()

    def test_auto_meta_por_token(self):
        _env.set_env(self, META_WA_TOKEN="tok")
        self.assertEqual(otp_send.wa_provider(), "meta")

    def test_pin_meta_sin_token_falla_cerrado_no_cae_a_waha(self):
        # El escenario del runbook §2: producción pineada a meta, token
        # ausente, y una instancia WAHA loopback configurada al lado. El
        # resultado correcto es no-op — no un fallback a WAHA.
        _env.set_env(self, TURNOS_WA_PROVIDER="meta", OTP_WAHA_URL="http://127.0.0.1:3005",
                     OTP_WAHA_API_KEY="k")
        with mock.patch.object(otp_send, "_enviar_whatsapp") as waha, \
                self._urlopen_prohibido():
            self.assertFalse(otp_send.enviar_texto(_DESTINO, "hola"))
        waha.assert_not_called()


class TestOtpNuncaPorMeta(_Base):
    """Invariante 4: sin plantilla de Authentication aprobada
    (META_WA_OTP_TEMPLATE) no existe OTP por Meta — y un OTP_PROVIDER
    desconocido tampoco cae a WAHA."""

    def test_otp_provider_meta_es_noop(self):
        _env.set_env(self, OTP_PROVIDER="meta", META_WA_TOKEN="tok", META_WA_PHONE_NUMBER_ID="123")
        with mock.patch.object(otp_send, "_enviar_meta") as meta, \
                mock.patch.object(otp_send, "_enviar_whatsapp") as waha, \
                self._urlopen_prohibido():
            self.assertFalse(otp_send.enviar_otp(_DESTINO, "123456"))
        meta.assert_not_called()
        waha.assert_not_called()

    def test_otp_provider_desconocido_es_noop(self):
        _env.set_env(self, OTP_PROVIDER="palomas")
        with mock.patch.object(otp_send, "_enviar_whatsapp") as waha:
            self.assertFalse(otp_send.enviar_otp(_DESTINO, "123456"))
        waha.assert_not_called()

    def test_otp_por_waha_loopback_sigue_andando(self):
        # El flujo de dev de siempre: OTP_PROVIDER=whatsapp contra la
        # instancia loopback propia.
        _env.set_env(self, OTP_PROVIDER="whatsapp", OTP_WAHA_URL="http://127.0.0.1:3005",
                     OTP_WAHA_API_KEY="k")
        with mock.patch("urllib.request.urlopen", return_value=_FakeResponse()):
            self.assertTrue(otp_send.enviar_otp(_DESTINO, "123456"))


class TestDemoBloqueaTodo(_Base):
    """Invariante 5: modo demo = ninguna salida de WhatsApp, con cualquier
    transporte configurado."""

    def setUp(self):
        super().setUp()
        _env.set_env(self, TURNOS_WA_PROVIDER="meta", META_WA_TOKEN="tok",
                     META_WA_PHONE_NUMBER_ID="123")

    def test_demo_bloquea_texto_y_otp(self):
        with mock.patch.object(demo_installation, "enabled", return_value=True), \
                mock.patch.object(otp_send, "_enviar_meta") as meta, \
                self._urlopen_prohibido():
            self.assertFalse(otp_send.enviar_texto(_DESTINO, "hola"))
            self.assertFalse(otp_send.enviar_otp(_DESTINO, "123456"))
        meta.assert_not_called()


class TestCassettePreservado(unittest.TestCase):
    """Invariante 6: el cassette de Meta sobrevive a este slice y su shape
    sigue siendo el contrato HTTP real de _enviar_meta (request
    messaging_product/to/type/text.body; response messages[0].id — el campo
    que _enviar_meta lee para dar el envío por bueno)."""

    def test_cassette_existe_y_conserva_el_shape(self):
        self.assertTrue(os.path.exists(CASSETTE_PATH),
                        "se borró tests/cassettes/meta/enviar_texto_ok.json")
        with open(CASSETTE_PATH, encoding="utf-8") as fh:
            cassette = json.load(fh)
        inter = cassette["interactions"][0]
        body = inter["request_body"]
        self.assertEqual(body["messaging_product"], "whatsapp")
        self.assertEqual(body["type"], "text")
        self.assertIn("to", body)
        self.assertIn("body", body["text"])
        respuesta = inter["response"]["body"]
        self.assertTrue(respuesta["messages"][0]["id"])


if __name__ == "__main__":
    unittest.main()
