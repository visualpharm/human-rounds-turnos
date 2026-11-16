"""test_turnstile.py — el gate de Cloudflare Turnstile (turnstile.py).

Lo que se prueba acá es la decisión de SI el gate corre, no la verificación
contra Cloudflare (eso es red). El invariante que cubre estos tests: cliente y
servidor tienen que coincidir — el sitekey que se sirve al widget y la
exigencia del token salen de la MISMA función. Si divergen, o el usuario queda
trabado en "Esperá a que cargue la verificación anti-bot" (dev), o el endpoint
queda abierto (prod).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _env  # noqa: E402
from app.auth import turnstile  # noqa: E402
from app.core import config, proxy_trust  # noqa: E402

turnstile.ENV_PATH = ""  # ignorar el .env del checkout de dev
config.ENV_PATH = ""  # idem: no leer el .env real del checkout de dev


class FakeHandler:
    def __init__(self, host=None):
        self.headers = {"Host": host} if host else {}
        self.client_address = ("203.0.113.5", 12345)


def _activo(request):
    return turnstile.activo_from(request.headers)


def _sitekey(request):
    return turnstile.sitekey_from(request.headers)


def _verificar(request, token):
    return turnstile.verificar_from(
        request.headers, request.client_address[0], token)


def _bypass(request):
    return turnstile.bypass_activo_from(request.headers)


class TestGate(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, TURNOS_TURNSTILE_SECRET="secreto-de-test", TURNOS_TURNSTILE_SITEKEY="0xSITEKEY",
                     TURNOS_TURNSTILE_OFF=None, TURNOS_PROXY_TRUST_SECRET="proxy-de-test")
        # These tests are about host/env precedence, not about the panel
        # setting — pin encendido() to True so a value persisted in the dev
        # DB's `config/turnstile` row (plus its 3 s cache) can't flip the
        # expected outcome depending on test order or environment.
        self._encendido = turnstile.encendido
        turnstile.encendido = lambda: True
        turnstile.invalidar_cache()

    def tearDown(self):
        turnstile.encendido = self._encendido
        turnstile.invalidar_cache()

    def test_host_de_produccion_gatea(self):
        h = FakeHandler("turnos.pinamar.gob.ar")
        self.assertTrue(_activo(h))
        self.assertEqual(_sitekey(h), "0xSITEKEY")
        self.assertFalse(_verificar(h, ""))  # sin token, sin red

    def test_hosts_de_dev_no_gatean(self):
        # el sitekey de Cloudflare está atado al dominio: en estos hosts el
        # widget nunca resuelve, así que el gate se apaga en los DOS lados.
        for host in ("devbox:7700", "localhost:7700", "127.0.0.1:7700", "[::1]:7700",
                     "192.168.1.156:7700", "devbox.local", "mac.lan"):
            h = FakeHandler(host)
            # M-5: solo un par de loopback o red privada puede declararse host de dev. Con TURNOS_ASGI=1 el
            # par sale unicamente del encabezado firmado (proxy_trust), no del socket: sin el, el par es
            # desconocido, cuenta como publico y el gate corre (fail-closed).
            h.client_address = ("127.0.0.1", 12345)
            h.headers[proxy_trust.HEADER] = proxy_trust.sign("127.0.0.1")
            self.assertFalse(_activo(h), host)
            self.assertIsNone(_sitekey(h), host)
            self.assertTrue(_verificar(h, ""), host)  # no exige token

    def test_sin_host_gatea(self):
        # header ausente (no debería pasar en HTTP/1.1) -> fail-closed
        self.assertTrue(_activo(FakeHandler()))

    def test_sin_secret_no_gatea(self):
        _env.set_env(self, TURNOS_TURNSTILE_SECRET=None)
        h = FakeHandler("turnos.pinamar.gob.ar")
        self.assertFalse(_activo(h))
        self.assertTrue(_verificar(h, ""))

    def test_off_por_env_no_gatea(self):
        _env.set_env(self, TURNOS_TURNSTILE_OFF="1")
        h = FakeHandler("turnos.pinamar.gob.ar")
        self.assertFalse(_activo(h))
        self.assertIsNone(_sitekey(h))


class TestBypassDeTesting(unittest.TestCase):
    """TURNOS_TURNSTILE_TEST_BYPASS_SECRET + header X-Turnstile-Test-Bypass:
    la única forma de que una verificación automatizada (smoke_prod.py, un
    browser headless) ejercite el login completo sin poder resolver el
    desafío real de Cloudflare."""

    def setUp(self):
        _env.set_env(self, TURNOS_TURNSTILE_SECRET="secreto-de-test", TURNOS_TURNSTILE_SITEKEY="0xSITEKEY",
                     TURNOS_TURNSTILE_OFF=None, TURNOS_TURNSTILE_TEST_BYPASS_SECRET=None)
        # Same isolation as TestGate: the bypass tests assert "still gates
        # normally" outcomes, which requires the gate to actually be on —
        # never let a persisted panel setting in the dev DB decide that.
        self._encendido = turnstile.encendido
        turnstile.encendido = lambda: True
        turnstile.invalidar_cache()

    def tearDown(self):
        turnstile.encendido = self._encendido
        turnstile.invalidar_cache()

    def test_sin_env_seteado_el_header_no_hace_nada(self):
        h = FakeHandler("turnos.pinamar.gob.ar")
        h.headers["X-Turnstile-Test-Bypass"] = "cualquier-cosa"
        self.assertFalse(_bypass(h))
        self.assertFalse(_verificar(h, ""))  # sigue gateando normal

    def test_header_correcto_pasa_sin_llamar_a_cloudflare(self):
        _env.set_env(self, TURNOS_TURNSTILE_TEST_BYPASS_SECRET="shh-secreto")
        h = FakeHandler("turnos.pinamar.gob.ar")
        h.headers["X-Turnstile-Test-Bypass"] = "shh-secreto"
        self.assertTrue(_bypass(h))
        self.assertTrue(_verificar(h, ""))  # sin token y sin red

    def test_header_incorrecto_no_pasa(self):
        _env.set_env(self, TURNOS_TURNSTILE_TEST_BYPASS_SECRET="shh-secreto")
        h = FakeHandler("turnos.pinamar.gob.ar")
        h.headers["X-Turnstile-Test-Bypass"] = "adivinando"
        self.assertFalse(_bypass(h))
        self.assertFalse(_verificar(h, ""))

    def test_header_ausente_no_pasa_aunque_este_seteado_el_secret(self):
        _env.set_env(self, TURNOS_TURNSTILE_TEST_BYPASS_SECRET="shh-secreto")
        h = FakeHandler("turnos.pinamar.gob.ar")
        self.assertFalse(_bypass(h))


class TestSettingConfiguracion(unittest.TestCase):
    """El interruptor de Configuración (`config/turnstile`), sin tocar la base:
    se falsea `encendido()`, que es el único punto por donde `activo()` la
    consulta. El orden de precedencia es lo que importa acá."""

    def setUp(self):
        _env.set_env(self, TURNOS_TURNSTILE_SECRET="secreto-de-test", TURNOS_TURNSTILE_SITEKEY="0xSITEKEY",
                     TURNOS_TURNSTILE_OFF=None)
        self._encendido = turnstile.encendido
        turnstile.invalidar_cache()

    def tearDown(self):
        turnstile.encendido = self._encendido
        turnstile.invalidar_cache()

    def test_apagado_desde_configuracion_no_gatea(self):
        turnstile.encendido = lambda: False
        h = FakeHandler("turnos.pinamar.gob.ar")
        self.assertFalse(_activo(h))
        self.assertIsNone(_sitekey(h))     # el cliente no monta el widget
        self.assertTrue(_verificar(h, ""))  # y el server no exige token

    def test_encendido_desde_configuracion_gatea(self):
        turnstile.encendido = lambda: True
        h = FakeHandler("turnos.pinamar.gob.ar")
        self.assertTrue(_activo(h))
        self.assertEqual(_sitekey(h), "0xSITEKEY")

    def test_en_local_sigue_apagado_aunque_configuracion_lo_encienda(self):
        # el invariante que pidió el responsable del producto: en el server local NUNCA corre, no
        # importa lo que diga la base.
        turnstile.encendido = lambda: True
        for host in ("localhost", "127.0.0.1:7700", "devbox:7700", "192.168.1.156", "devbox.local"):
            self.assertFalse(_activo(FakeHandler(host)), host)

    def test_el_env_gana_sobre_configuracion(self):
        # TURNOS_TURNSTILE_OFF es el interruptor de emergencia: ningún setting
        # guardado en la base lo revierte.
        _env.set_env(self, TURNOS_TURNSTILE_OFF="1")
        turnstile.encendido = lambda: True
        self.assertFalse(_activo(FakeHandler("turnos.pinamar.gob.ar")))

    def test_sin_secret_no_gatea_aunque_este_encendido(self):
        _env.set_env(self, TURNOS_TURNSTILE_SECRET=None)
        turnstile.encendido = lambda: True
        self.assertFalse(_activo(FakeHandler("turnos.pinamar.gob.ar")))

    def test_base_caida_deja_el_gate_encendido(self):
        # fail-closed: si no se puede leer el setting, se asume encendido.
        from unittest.mock import patch
        with patch("app.core.db_catalog.catalog_get", side_effect=ImportError("db no disponible")):
            turnstile.invalidar_cache()
            self.assertTrue(turnstile.encendido())
        turnstile.invalidar_cache()


if __name__ == "__main__":
    unittest.main()


class SharedClientContract(unittest.TestCase):
    def test_admin_template_does_not_mount_an_implicit_visible_widget(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        template = (root / 'templates/admin/login.html').read_text()
        self.assertNotIn('class="cf-turnstile"', template)
        self.assertNotIn('challenges.cloudflare.com/turnstile', template)
        self.assertIn('id="gate-turnstile"', template)
