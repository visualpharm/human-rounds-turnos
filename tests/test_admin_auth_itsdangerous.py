"""Session-cookie verification after the legacy HMAC window closed.

The maintained `itsdangerous` format is the only accepted session format.
The former manual HMAC format is reconstructed only as a rejection fixture,
so accidentally restoring the compatibility verifier makes this suite fail.
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.auth import admin_auth  # noqa: E402
from app.core import config  # noqa: E402

config.ENV_PATH = ""  # no leer el .env real del checkout (mismo patrón que
                       # test_ip_exempt.py/test_turnstile.py) -- este dev
                       # checkout trae TURNOS_ADMIN_SECRET seteado, que
                       # taparía cualquier cambio a TURNOS_ADMIN_PASS.


def _b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _legacy_mint(usuario, rol="admin", exp_delta=3600):
    """Reconstruct the retired HMAC format for a rejection assertion."""
    payload = json.dumps(
        {"v": admin_auth.TOKEN_VERSION, "u": usuario, "r": rol,
         "exp": int(time.time()) + exp_delta},
        separators=(",", ":")).encode("utf-8")
    p = _b64u(payload)
    sig = hmac.new(admin_auth._signing_key(), p.encode("ascii"), hashlib.sha256).hexdigest()
    return "%s.%s" % (p, sig)


class ItsdangerousSessionTestCase(unittest.TestCase):
    def setUp(self):
        # Este checkout de dev tiene una Postgres viva con posibles overrides
        # de Settings guardados (config.py: DB > env) — el escape hatch de
        # ops fuerza env/.env a ganar siempre, igual que test_config.py.
        self.addCleanup(config._reset_cache_para_tests)   # after the environment is put back
        _env.set_env(self, TURNOS_ADMIN_PASS="test-pass-session-library",
                     TURNOS_ADMIN_SECRET=None, TURNOS_CONFIG_LOCK="1")
        config._reset_cache_para_tests()

    def test_token_nuevo_es_itsdangerous_y_verifica(self):
        token = admin_auth._mint_token("admin", "admin")
        # Formato itsdangerous URLSafeTimedSerializer: payload.timestamp.firma
        # (tres partes = 2 puntos) — DISTINTO del viejo payload.firma (un
        # punto), tal cual pide el diseño ("byte-different format"). OJO
        # (bomba de tiempo real, cazada 2026-08-08 en la corrida ASGI del
        # gate de la ronda 11-13): itsdangerous comprime el payload con zlib
        # CUANDO el comprimido sale más corto, y le antepone "." — el
        # resultado tiene 3 puntos. Con este payload el veredicto de zlib
        # depende de los BYTES exactos de "exp" (epoch + duración), así que
        # el conteo exacto de puntos cambia según el segundo/día en que
        # corre el test. El contrato real es "no es el formato legacy de un
        # punto y verifica" — se afirma eso, nunca la igualdad exacta.
        self.assertGreaterEqual(token.count("."), 2)
        identidad = admin_auth._verify_token(token)
        self.assertEqual(identidad, {"usuario": "admin", "rol": "admin"})

    def test_cookie_legacy_se_rechaza_al_terminar_la_ventana(self):
        legacy_token = _legacy_mint("admin", "admin")
        self.assertEqual(legacy_token.count("."), 1)
        self.assertIsNone(admin_auth._verify_token(legacy_token))

    def test_cookie_nueva_vencida_no_autentica(self):
        # Mintea directo con itsdangerous pero con "exp" ya pasado -- el
        # payload es NUESTRO campo, itsdangerous no lo vencería solo.
        payload = {"v": admin_auth.TOKEN_VERSION, "u": "admin", "r": "admin",
                   "exp": int(time.time()) - 10}
        token = admin_auth._itsdangerous_serializer().dumps(payload)
        self.assertIsNone(admin_auth._verify_token(token))

    def test_basura_no_autentica_por_ninguna_rama(self):
        self.assertIsNone(admin_auth._verify_token("no-es-un-token-de-nada"))
        self.assertIsNone(admin_auth._verify_token(""))
        self.assertIsNone(admin_auth._verify_token(None))

    def test_firma_nueva_forjada_no_autentica(self):
        token = admin_auth._mint_token("admin", "admin")
        partes = token.split(".")
        partes[-1] = "x" * len(partes[-1])
        forjado = ".".join(partes)
        self.assertIsNone(admin_auth._verify_token(forjado))

    def test_secreto_distinto_invalida_la_sesion(self):
        nuevo_token = admin_auth._mint_token("admin", "admin")
        _env.set_env(self, TURNOS_ADMIN_PASS="otra-contraseña-distinta")
        self.assertIsNone(admin_auth._verify_token(nuevo_token))

    def test_debe_renovar_rechaza_legacy_y_no_renueva_una_nueva(self):
        legacy_token = _legacy_mint("admin", "admin", exp_delta=10)
        nuevo_reciente = admin_auth._mint_token("admin", "admin")
        self.assertFalse(admin_auth._debe_renovar(legacy_token))
        self.assertFalse(admin_auth._debe_renovar(nuevo_reciente))


if __name__ == "__main__":
    unittest.main()
