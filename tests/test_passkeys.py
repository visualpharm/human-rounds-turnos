"""test_passkeys.py — ingreso al panel con passkey (WebAuthn, 2026-07-27).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_passkeys.py"

Dos capas:
  1. Un autenticador de prueba construido con ``cryptography`` produce las
     respuestas de navegador que verifica py_webauthn.
  2. El trámite completo contra Postgres, en una base dedicada (mismo patrón
     que test_admin_users.py): alta → lista → ingreso → baja → reset.
"""

import os
import sys
import json
import hashlib
import secrets
import unittest

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Session cookies are signed with the admin secret; the suite used to get one
# from whichever module happened to be imported before this one.
import _env  # noqa: E402
_env.process_defaults({"TURNOS_ADMIN_SECRET": "test-only-passkeys-signing-key"})

from app.core import db_operators  # noqa: E402
from app.auth import admin_auth             # noqa: E402
from app.auth import passkeys as pk  # noqa: E402
from app.auth import passkeys_router  # noqa: E402


# ---- autenticador de mentira ----------------------------------------------

class Autenticador:
    """Passkey de mentira: clave P-256 propia, firma como firmaría el aparato."""

    def __init__(self, rp_id="localhost", credential_id=None):
        self.rp_id = rp_id
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = credential_id or secrets.token_bytes(16)
        self.sign_count = 0

    # -- claves
    def cose(self):
        numbers = self.private_key.public_key().public_numbers()
        return {1: 2, 3: -7, -1: 1,
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big")}

    def public_json(self):
        from app.auth import webauthn_adapter
        return webauthn_adapter.public_key_to_storage(cbor2.dumps(self.cose()))

    # -- firma ECDSA (P-256 + SHA-256), DER
    def firmar(self, mensaje):
        return self.private_key.sign(mensaje, ec.ECDSA(hashes.SHA256()))

    # -- ceremonias
    def _auth_data(self, con_credencial, flags=0x01, count=None):
        from hashlib import sha256
        data = sha256(self.rp_id.encode()).digest()
        f = flags | (0x40 if con_credencial else 0)
        data += bytes([f]) + int(self.sign_count if count is None else count).to_bytes(4, "big")
        if con_credencial:
            data += b"\x00" * 16
            data += len(self.credential_id).to_bytes(2, "big") + self.credential_id
            data += cbor2.dumps(self.cose())
        return data

    @staticmethod
    def _client_data(tipo, reto, origen):
        return json.dumps({"type": tipo, "challenge": reto, "origin": origen,
                           "crossOrigin": False}).encode("utf-8")

    def alta(self, reto, origen):
        cd = self._client_data("webauthn.create", reto, origen)
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": self._auth_data(True)})
        return {"challenge": reto, "id": pk.b64u(self.credential_id),
                "response": {"clientDataJSON": pk.b64u(cd),
                             "attestationObject": pk.b64u(att)}}

    def ingreso(self, reto, origen, count=None):
        cd = self._client_data("webauthn.get", reto, origen)
        ad = self._auth_data(False, count=count)
        firma = self.firmar(ad + hashlib.sha256(cd).digest())
        return {"challenge": reto, "id": pk.b64u(self.credential_id),
                "response": {"clientDataJSON": pk.b64u(cd),
                             "authenticatorData": pk.b64u(ad),
                             "signature": pk.b64u(firma)}}


_APP = FastAPI()
_APP.include_router(passkeys_router.router)
_CLIENT = TestClient(
    _APP,
    base_url="http://localhost:7700",
    client=("127.0.0.1", 1234),
)


def _cookie(usuario, rol="admin"):
    return {"Host": "localhost:7700",
            "Cookie": "st_admin=" + admin_auth._mint_token(usuario, rol)}


ORIGEN = "http://localhost:7700"


class TestRetos(unittest.TestCase):
    def test_firma_valida_se_puede_leer_sin_estado_local(self):
        r = pk.nuevo_reto("login")
        self.assertIsNotNone(pk.leer_reto(r, "login"))
        # Replay state belongs in PostgreSQL so a second ASGI process sees it.
        self.assertIsNotNone(pk.leer_reto(r, "login"))

    def test_no_sirve_para_otro_proposito(self):
        r = pk.nuevo_reto("registro", "admin")
        self.assertIsNone(pk.leer_reto(r, "login"))

    def test_firma_adulterada_se_rechaza(self):
        r = pk.nuevo_reto("login")
        p, sig = r.split(".", 1)
        self.assertIsNone(pk.leer_reto(p + "." + ("0" * len(sig)), "login"))

    def test_vencido_se_rechaza(self):
        original = pk._RETO_TTL
        try:
            pk._RETO_TTL = -1
            self.assertIsNone(pk.leer_reto(pk.nuevo_reto("login"), "login"))
        finally:
            pk._RETO_TTL = original


class TestOrigenYRp(unittest.TestCase):
    def test_rp_id_es_el_host_sin_puerto(self):
        request = Request({
            "type": "http", "method": "GET", "path": "/", "query_string": b"",
            "headers": [(b"host", b"turnos.pinamar.gob.ar:443")],
        })
        self.assertEqual(pk.rp_id_from_host(request.headers.get("Host")),
                         "turnos.pinamar.gob.ar")

    def test_origen_usa_el_proto_reenviado(self):
        request = Request({
            "type": "http", "method": "GET", "path": "/", "query_string": b"",
            "headers": [(b"host", b"sana.example.com"),
                        (b"x-forwarded-proto", b"https")],
        })
        self.assertEqual(pk.origen_from_headers(
            request.headers.get("Host"), request.headers.get("X-Forwarded-Proto")),
            "https://sana.example.com")

    def test_dev_sin_proxy_es_http(self):
        request = Request({
            "type": "http", "method": "GET", "path": "/", "query_string": b"",
            "headers": [(b"host", b"localhost:7700")],
        })
        self.assertEqual(pk.origen_from_headers(
            request.headers.get("Host"), request.headers.get("X-Forwarded-Proto")),
            "http://localhost:7700")


# ---- 2. el trámite completo (con base) -------------------------------------

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_passkeys", seed=lambda: db_operators.operators_list())


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


@unittest.skipUnless(os.environ.get("TURNOS_FAKE_AI") is not None or True, "")
class TestFlujo(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        _CLIENT.cookies.clear()
        import psycopg
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
            with c.cursor() as cur:
                cur.execute("TRUNCATE passkey_challenges")
                cur.execute("TRUNCATE passkeys RESTART IDENTITY")
                cur.execute("TRUNCATE operators RESTART IDENTITY CASCADE")
        admin_auth._ATTEMPTS.clear()
        self.usuario = admin_auth._admin_user()

    # -- helpers
    def _reto_alta(self, usuario=None, rol="admin"):
        response = _CLIENT.post(
            pk._BASE + "/register-options", json={},
            headers=_cookie(usuario or self.usuario, rol))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _alta(self, aut, usuario=None, rol="admin"):
        reto = self._reto_alta(usuario, rol)["challenge"]
        cred = aut.alta(reto, ORIGEN)
        cred["nombre"] = "MacBook del responsable del producto"
        response = _CLIENT.post(
            pk._BASE + "/register", json=cred,
            headers=_cookie(usuario or self.usuario, rol))
        return response, response.json()

    def _ingreso(self, aut, count=None):
        options = _CLIENT.post(pk._BASE + "/login-options", json={})
        self.assertEqual(options.status_code, 200, options.text)
        reto = options.json()["challenge"]
        response = _CLIENT.post(
            pk._BASE + "/login", json=aut.ingreso(reto, ORIGEN, count))
        return response, response.json()

    # -- alta
    def test_alta_e_ingreso_dan_una_sesion_valida(self):
        aut = Autenticador()
        _, alta = self._alta(aut)
        self.assertTrue(alta["ok"])
        self.assertEqual(alta["passkey"]["nombre"], "MacBook del responsable del producto")
        # la clave pública NO vuelve al panel
        self.assertNotIn("public_key", alta["passkey"])

        h, res = self._ingreso(aut)
        self.assertTrue(res["ok"])
        self.assertEqual(res["usuario"], self.usuario)
        self.assertEqual(res["rol"], "admin")
        cookie = h.headers.get("Set-Cookie", "")
        self.assertTrue(cookie.startswith("st_admin="))
        token = cookie.split(";")[0].split("=", 1)[1]
        self.assertEqual(admin_auth._verify_token(token),
                         {"usuario": self.usuario, "rol": "admin"})

    def test_el_alta_exige_sesion(self):
        response = _CLIENT.post(
            pk._BASE + "/register-options", json={},
            headers={"Host": "localhost:7700"})
        self.assertEqual(response.status_code, 401)

    def test_no_se_puede_registrar_la_misma_llave_dos_veces(self):
        aut = Autenticador()
        self._alta(aut)
        h, res = self._alta(aut)
        self.assertEqual(h.status_code, 409)

    def test_el_desafio_de_otro_dominio_se_rechaza(self):
        aut = Autenticador(rp_id="otro.example.com")
        reto = self._reto_alta()["challenge"]
        response = _CLIENT.post(
            pk._BASE + "/register", json=aut.alta(reto, ORIGEN),
            headers=_cookie(self.usuario))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "dominio_inesperado")

    def test_el_desafio_de_registro_esta_ligado_a_la_sesion_que_lo_pidio(self):
        db_operators.operator_create("recepcion", "clave-larga-1234", rol="admisionista")
        owner = self._reto_alta()["challenge"]
        auth = Autenticador()
        stolen = _CLIENT.post(
            pk._BASE + "/register", json=auth.alta(owner, ORIGEN),
            headers=_cookie("recepcion", "admisionista"))
        self.assertEqual(stolen.status_code, 400)
        # A rejected cross-account request does not consume the owner's
        # challenge; the owning session can still complete its ceremony.
        real = _CLIENT.post(
            pk._BASE + "/register", json=auth.alta(owner, ORIGEN),
            headers=_cookie(self.usuario))
        self.assertEqual(real.status_code, 200, real.text)

    def test_un_origen_ajeno_se_rechaza(self):
        aut = Autenticador()
        reto = self._reto_alta()["challenge"]
        response = _CLIENT.post(
            pk._BASE + "/register",
            json=aut.alta(reto, "https://phishing.example.com"),
            headers=_cookie(self.usuario))
        self.assertEqual(response.status_code, 400)

    # -- ingreso
    def test_una_llave_desconocida_no_entra(self):
        h, res = self._ingreso(Autenticador())
        self.assertEqual(h.status_code, 401)
        self.assertEqual(res["error"], "llave_desconocida")

    def test_firma_de_otra_llave_no_entra(self):
        aut = Autenticador()
        self._alta(aut)
        impostor = Autenticador(credential_id=aut.credential_id)   # mismo id, otra clave
        h, res = self._ingreso(impostor)
        self.assertEqual(h.status_code, 401)
        self.assertEqual(res["error"], "firma_invalida")

    def test_el_desafio_no_se_puede_reusar(self):
        aut = Autenticador()
        self._alta(aut)
        options = _CLIENT.post(pk._BASE + "/login-options", json={})
        reto = options.json()["challenge"]
        cred = aut.ingreso(reto, ORIGEN)
        first = _CLIENT.post(pk._BASE + "/login", json=cred)
        self.assertTrue(first.json()["ok"])
        replay = _CLIENT.post(pk._BASE + "/login", json=cred)
        self.assertEqual(replay.status_code, 400)

    def test_contador_que_retrocede_es_llave_clonada(self):
        aut = Autenticador()
        self._alta(aut)
        self._ingreso(aut, count=5)
        h, res = self._ingreso(aut, count=3)
        self.assertEqual(h.status_code, 401)
        self.assertEqual(res["error"], "llave_duplicada")

    def test_operador_desactivado_no_entra_con_su_llave(self):
        db_operators.operator_create("recepcion", "clave-larga-1234", rol="admisionista")
        aut = Autenticador()
        self._alta(aut, usuario="recepcion", rol="admisionista")
        _, ok = self._ingreso(aut)
        self.assertTrue(ok["ok"])
        db_operators.operator_update("recepcion", activo=False)
        h, res = self._ingreso(aut)
        self.assertEqual(h.status_code, 403)
        self.assertEqual(res["error"], "cuenta_inactiva")

    def test_el_rol_de_la_sesion_sale_de_la_cuenta_no_de_la_llave(self):
        db_operators.operator_create("dra.whelan", "clave-larga-1234", rol="medico",
                          medico_id=None)
        aut = Autenticador()
        self._alta(aut, usuario="dra.whelan", rol="medico")
        _, res = self._ingreso(aut)
        self.assertEqual(res["rol"], "medico")

    # -- lista, baja y reset
    def test_lista_baja_y_reset(self):
        a1, a2 = Autenticador(), Autenticador()
        self._alta(a1)
        self._alta(a2)
        response = _CLIENT.get(pk._BASE, headers=_cookie(self.usuario))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["passkeys"]), 2)

        response = _CLIENT.post(
            pk._BASE + "/delete",
            json={"credential_id": pk.b64u(a1.credential_id)},
            headers=_cookie(self.usuario))
        self.assertTrue(response.json()["ok"])

        response = _CLIENT.post(
            pk._BASE + "/reset", json={}, headers=_cookie(self.usuario))
        self.assertEqual(response.json()["borradas"], 1)
        response = _CLIENT.get(pk._BASE, headers=_cookie(self.usuario))
        self.assertEqual(response.json()["passkeys"], [])

    def test_borrada_la_llave_ya_no_entra(self):
        aut = Autenticador()
        self._alta(aut)
        _CLIENT.post(pk._BASE + "/reset", json={}, headers=_cookie(self.usuario))
        h, res = self._ingreso(aut)
        self.assertEqual(res["error"], "llave_desconocida")

    def test_un_no_admin_no_toca_las_llaves_de_otro(self):
        db_operators.operator_create("recepcion", "clave-larga-1234", rol="admisionista")
        aut = Autenticador()
        self._alta(aut)                                    # llave del dueño
        response = _CLIENT.post(
            pk._BASE + "/reset", json={"usuario": self.usuario},
            headers=_cookie("recepcion", "admisionista"))
        self.assertEqual(response.status_code, 403)
        response = _CLIENT.get(
            pk._BASE + "?usuario=" + self.usuario,
            headers=_cookie("recepcion", "admisionista"))
        self.assertEqual(response.status_code, 403)

    def test_un_admin_resetea_las_llaves_de_otra_cuenta(self):
        db_operators.operator_create("recepcion", "clave-larga-1234", rol="admisionista")
        aut = Autenticador()
        self._alta(aut, usuario="recepcion", rol="admisionista")
        response = _CLIENT.post(
            pk._BASE + "/reset", json={"usuario": "recepcion"},
            headers=_cookie(self.usuario))
        self.assertEqual(response.json()["borradas"], 1)


if __name__ == "__main__":
    unittest.main()
