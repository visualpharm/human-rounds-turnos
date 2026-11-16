"""test_lab_link.py — the signed link from «Resultados de laboratorio» to the
provider's laboratory page (ai/lab_link.py + GET /account/lab-results/open,
docs/architecture/laboratorio-paciente.md «Provider link»).

The provider's nginx checks `secure_link_md5 "$secure_link_expires$uri KEY"`;
the reference value below is computed here from scratch (MD5 → base64 → `+`→`-`,
`/`→`_`, no `=`) so the module is checked against the nginx formula, not
against itself.

Correr:  python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_lab_link.py
"""

import base64
import hashlib
import os
import sys
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env as test_env  # noqa: E402

test_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db_operators  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.cuentas import accounts  # noqa: E402
from app.ai import lab_link  # noqa: E402
from app.ai import lab_results_router  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_lablink", seed=lambda: db_operators.operators_list())

KEY = "k3y-with_base64url+/chars"  # nginx takes the key as literal text
NOW = 1_800_000_000


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _nginx_md5(expires, uri, key):
    """What nginx's `secure_link_md5` + the documented base64url transform give."""
    raw = hashlib.md5(("%s%s %s" % (expires, uri, key)).encode("utf-8")).digest()
    return base64.b64encode(raw).decode().replace("+", "-").replace("/", "_").replace("=", "")


def _env(**values):
    base = {"TURNOS_LAB_LINK_SECRET": "", "TURNOS_LAB_LINK_BASE": ""}
    base.update(values)
    return test_env.patched_env(base)


class TestSignature(unittest.TestCase):
    def test_firma_igual_a_la_que_calcula_nginx(self):
        with _env(TURNOS_LAB_LINK_SECRET=KEY):
            url = lab_link.signed_url("30123456", now=NOW)
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        expires = query["expires"][0]
        self.assertEqual(parts.scheme + "://" + parts.netloc, "https://laboratorio.pinamar.gob.ar")
        self.assertEqual(parts.path, "/ingresos/hc-paciente/false/30123456")
        self.assertEqual(query["md5"][0], _nginx_md5(expires, parts.path, KEY))

    def test_vector_fijo(self):
        # Fixed inputs written out literally (not rebuilt from the module's own
        # parts): md5("1800000300/ingresos/hc-paciente/false/30123456 KEY").
        with _env(TURNOS_LAB_LINK_SECRET="KEY"):
            url = lab_link.signed_url("30123456", now=NOW)
        raw = hashlib.md5(b"1800000300/ingresos/hc-paciente/false/30123456 KEY").digest()
        esperado = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        self.assertTrue(url.endswith("?md5=%s&expires=1800000300" % esperado), url)

    def test_alfabeto_base64url_sin_relleno(self):
        # 16 digest bytes → 22 chars unpadded, never `+`, `/` or `=`; try many
        # inputs so both substituted characters actually occur.
        vistos = set()
        for n in range(400):
            sig = lab_link.signature(NOW + n, "/ingresos/hc-paciente/false/%d" % n, KEY)
            self.assertEqual(len(sig), 22)
            self.assertNotIn("=", sig)
            self.assertNotIn("+", sig)
            self.assertNotIn("/", sig)
            self.assertEqual(sig, _nginx_md5(NOW + n, "/ingresos/hc-paciente/false/%d" % n, KEY))
            vistos.update(sig)
        self.assertIn("-", vistos)
        self.assertIn("_", vistos)

    def test_vence_a_los_5_minutos(self):
        with _env(TURNOS_LAB_LINK_SECRET=KEY):
            url = lab_link.signed_url("30123456", now=NOW)
            self.assertEqual(parse_qs(urlsplit(url).query)["expires"], [str(NOW + 300)])
            # Wall clock when `now` is omitted.
            with mock.patch.object(lab_link.time, "time", return_value=NOW + 0.9):
                url = lab_link.signed_url("30123456")
            self.assertEqual(parse_qs(urlsplit(url).query)["expires"], [str(NOW + 300)])

    def test_la_firma_no_sirve_para_otro_dni_ni_otra_clave(self):
        with _env(TURNOS_LAB_LINK_SECRET=KEY):
            propia = lab_link.signed_url("30123456", now=NOW)
            ajena = lab_link.signed_url("30999999", now=NOW)
        md5 = lambda u: parse_qs(urlsplit(u).query)["md5"][0]  # noqa: E731
        self.assertNotEqual(md5(propia), md5(ajena))
        self.assertNotEqual(md5(propia), _nginx_md5(NOW + 300, "/ingresos/hc-paciente/false/30123456", "otra"))

    def test_base_configurable_y_dni_solo_digitos(self):
        with _env(TURNOS_LAB_LINK_SECRET=KEY,
                  TURNOS_LAB_LINK_BASE="https://lab.example.test/x/hc/"):
            url = lab_link.signed_url("30.123.456", now=NOW)
        parts = urlsplit(url)
        self.assertEqual(parts.netloc, "lab.example.test")
        self.assertEqual(parts.path, "/x/hc/30123456")
        self.assertEqual(parse_qs(parts.query)["md5"][0], _nginx_md5(NOW + 300, parts.path, KEY))

    def test_sin_clave_o_sin_dni_no_hay_enlace(self):
        with _env():
            self.assertFalse(lab_link.enabled())
            self.assertIsNone(lab_link.signed_url("30123456", now=NOW))
        with _env(TURNOS_LAB_LINK_SECRET=KEY):
            self.assertTrue(lab_link.enabled())
            self.assertIsNone(lab_link.signed_url("", now=NOW))
            self.assertIsNone(lab_link.signed_url(None, now=NOW))


def _truncate():
    import psycopg
    with psycopg.connect(_db.test_url, autocommit=True, connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute("TRUNCATE users, sessions, dependents RESTART IDENTITY CASCADE")


class TestOpenRoute(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _truncate()
        app = FastAPI()
        app.include_router(lab_results_router.router)
        self.client = TestClient(app, follow_redirects=False)
        db_accounts.create_user("40111222", "Ana", "Gómez", "1990-05-05", "F", "2254550001", "clave1234")
        db_accounts.create_user("40111333", "Otro", "Paciente", "1985-01-01", "M", "2254550002", "clave5678")
        self.cookie = {"Cookie": "%s=%s" % (accounts.COOKIE_NAME, db_accounts.create_session("40111222"))}

    def tearDown(self):
        self.client.close()

    def test_redirige_al_enlace_firmado_del_dni_de_la_cuenta(self):
        with _env(TURNOS_LAB_LINK_SECRET=KEY):
            response = self.client.get("/account/lab-results/open", headers=self.cookie)
        self.assertEqual(response.status_code, 302)
        parts = urlsplit(response.headers["location"])
        self.assertEqual(parts.path, "/ingresos/hc-paciente/false/40111222")
        query = parse_qs(parts.query)
        self.assertEqual(query["md5"][0], _nginx_md5(query["expires"][0], parts.path, KEY))
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_un_dni_en_la_peticion_se_ignora(self):
        with _env(TURNOS_LAB_LINK_SECRET=KEY):
            for extra in ("?dni=40111333", "?patient_dni=40111333", "/40111333"):
                response = self.client.get("/account/lab-results/open" + extra, headers=self.cookie)
                if response.status_code == 302:
                    self.assertIn("/false/40111222?", response.headers["location"])
                    self.assertNotIn("40111333", response.headers["location"])
                else:
                    self.assertEqual(response.status_code, 404)
            # The other patient's own session gets only their own DNI.
            otro = {"Cookie": "%s=%s" % (accounts.COOKIE_NAME, db_accounts.create_session("40111333"))}
            response = self.client.get("/account/lab-results/open", headers=otro)
        self.assertIn("/false/40111333?", response.headers["location"])
        self.assertNotIn("40111222", response.headers["location"])

    def test_sin_sesion_va_a_entrar_y_nunca_firma(self):
        with _env(TURNOS_LAB_LINK_SECRET=KEY):
            response = self.client.get("/account/lab-results/open")
        self.assertEqual(response.status_code, 302)
        location = response.headers["location"]
        self.assertNotIn("md5=", location)
        self.assertNotIn("laboratorio.pinamar", location)
        self.assertTrue(location.startswith("/account") or location.startswith("/auth/"), location)

    def test_sin_clave_la_ruta_no_existe(self):
        with _env():
            response = self.client.get("/account/lab-results/open", headers=self.cookie)
        self.assertEqual(response.status_code, 404)

    def test_el_json_de_la_cuenta_ofrece_el_boton_solo_con_clave(self):
        with _env():
            sin = self.client.get("/api/account/lab-results", headers=self.cookie).json()
        self.assertIsNone(sin["laboratorio_href"])
        with _env(TURNOS_LAB_LINK_SECRET=KEY):
            con = self.client.get("/api/account/lab-results", headers=self.cookie).json()
        # The page only ever carries OUR route, never the provider's address or a signature.
        self.assertEqual(con["laboratorio_href"], "/account/lab-results/open")
        self.assertNotIn("md5", str(con))
        self.assertNotIn("laboratorio.pinamar", str(con))


if __name__ == "__main__":
    unittest.main()
