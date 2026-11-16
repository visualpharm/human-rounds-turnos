"""test_accounts.py — pruebas de registro / login / cuenta (accounts.py + db.py).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest test.test_cuentas -v   (desde la raíz)

Sigue el patrón de test_booking_requests.py pero usa una base dedicada DISTINTA
`sana_turnos_test_cuentas` (conectándose a la maintenance db `postgres`), así las
suites corren en paralelo sin pisarse. Si Postgres no está accesible, las pruebas
con base se saltean con un mensaje claro.
"""

import os
import sys
import json
import unittest
from unittest import mock

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

# raíz del repo en el path
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# entorno determinístico ANTES de importar los módulos
import _env  # noqa: E402
_env.process_set(TURNOS_DNI_CLAIMS="0", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_AUTH_MODE="password")

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.cuentas import accounts   # noqa: E402
from app.cuentas import accounts_router  # noqa: E402
from app.core import i18n  # noqa: E402
from app.core import proxy_trust  # noqa: E402

# Turnstile no depende de si el .env local del dev tiene o no un secret real
# configurado (el mismo TURNOS_TURNSTILE_SECRET que usa admin_auth.py para
# /admin) — se apaga acá, determinístico, como el resto del entorno de test.
from app.auth import turnstile  # noqa: E402
from app.core import config  # noqa: E402
turnstile.ENV_PATH = ""   # no leer el .env del dev (no parchear _secret: es
config.ENV_PATH = ""  # idem: no leer el .env real del checkout de dev
                          # el módulo COMPARTIDO — pisarlo apaga el gate en los
                          # tests de los otros módulos del mismo proceso)

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_cuentas", seed=lambda: db_accounts.get_user("__forzar_esquema__"))

_APP = FastAPI()
_APP.include_router(accounts_router.router)
_CLIENT = TestClient(
    _APP,
    base_url="http://127.0.0.1:7700",
    follow_redirects=False,
    client=("127.0.0.1", 54321),
)


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


def _token_from_cookie(set_cookie):
    """'st_ses=<token>; Max-Age=...' -> '<token>'."""
    if not set_cookie:
        return None
    first = set_cookie.split(";", 1)[0]
    return first.split("=", 1)[1] if "=" in first else None


def _cookie_header(token):
    return {"Cookie": "%s=%s" % (accounts.COOKIE_NAME, token)}


def _native_headers(lang=None):
    headers = {
        proxy_trust.HEADER: proxy_trust.sign("127.0.0.1"),
    }
    if lang:
        headers["Cookie"] = f"lang={lang}"
    return headers


# ---- unidades sin base -----------------------------------------------------

class TestNormalizersUnit(unittest.TestCase):
    def test_norm_dni(self):
        self.assertEqual(accounts._norm_dni("13.452.513"), "13452513")
        self.assertEqual(accounts._norm_dni(" 30 123 456 "), "30123456")

    def test_norm_tel(self):
        # Se guarda CANÓNICO (responsable del producto, 2026-07-28: "canonical display/storage
        # format"): el mismo número escrito de cualquier forma queda con el
        # mismo valor en la base. El 9 sale sólo si venía marcado — sin marca
        # es otro número, no se inventa.
        self.assertEqual(accounts._norm_phone("(2254) 55-0000"), "+542254550000")
        self.assertEqual(accounts._norm_phone("+54 9 2254 55-0000"), "+5492254550000")
        self.assertEqual(accounts._norm_phone("02254 15 550000"), "+5492254550000")
        self.assertEqual(accounts._norm_phone("2254550000"), "+542254550000")
        # Extranjero: se guarda como vino (dígitos + el + que traía).
        self.assertEqual(accounts._norm_phone("+1 415 555 0000"), "+14155550000")
        self.assertIsNone(accounts._norm_phone("   "))

    def test_parse_fecha(self):
        self.assertEqual(accounts._parse_date("9/11/59"), "1959-11-09")
        self.assertEqual(accounts._parse_date("09/11/1959"), "1959-11-09")
        self.assertEqual(accounts._parse_date("1959-11-09"), "1959-11-09")
        self.assertEqual(accounts._parse_date("09.11.1959"), "1959-11-09")
        self.assertEqual(accounts._parse_date("09111959"), "1959-11-09")
        self.assertIsNone(accounts._parse_date("no-fecha"))
        self.assertIsNone(accounts._parse_date("31/02/1990"))  # día imposible

    def test_ruta_desconocida_no_monta_una_ruta_nativa(self):
        self.assertEqual(_CLIENT.get("/api/otra-cosa").status_code, 404)
        self.assertEqual(_CLIENT.post("/api/otra-cosa").status_code, 404)

    def test_cookie_contract(self):
        c = accounts._set_cookie("abc")
        self.assertIn("st_ses=abc", c)
        self.assertIn("HttpOnly", c)
        self.assertIn("SameSite=Lax", c)
        self.assertIn("Path=/", c)
        self.assertIn("Max-Age=%d" % accounts.COOKIE_MAX_AGE, c)


# ---- password hashing (db, con base) ---------------------------------------

class TestPasswordHashing(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")

    def test_hash_format_y_verificacion(self):
        uid = db_accounts.create_user("40111222", "Ana", "Gómez", "1990-05-05", "F",
                               "2254550001", "secreta")
        self.assertIsInstance(uid, int)

        u = db_accounts.get_user("40111222")
        self.assertNotIn("pass_hash", u)  # nunca sale de db.py

        ok = db_accounts.verify_password("40111222", "secreta")
        self.assertIsNotNone(ok)
        self.assertEqual(ok["dni"], "40111222")
        self.assertNotIn("pass_hash", ok)
        self.assertIsNone(db_accounts.verify_password("40111222", "mala"))

        # New credentials use the maintained Argon2id implementation.
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT pass_hash FROM users WHERE dni='40111222'")
            ph = cur.fetchone()["pass_hash"]
        self.assertTrue(ph.startswith("$argon2id$"))

    def test_sesiones(self):
        db_accounts.create_user("40999888", "Leo", "Ruiz", None, "M", None, "clave1234")
        tok = db_accounts.create_session("40999888")
        self.assertTrue(tok)
        ses = db_accounts.get_session(tok)
        self.assertEqual(ses["dni"], "40999888")
        self.assertEqual(ses["usuario"]["dni"], "40999888")
        self.assertNotIn("pass_hash", ses["usuario"])
        db_accounts.delete_session(tok)
        self.assertIsNone(db_accounts.get_session(tok))
        self.assertIsNone(db_accounts.get_session(None))


# ---- rutas end-to-end (con base) -------------------------------------------

class TestRoutes(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _CLIENT.cookies.clear()

    def _registro(self, **over):
        body = {"dni": "13.452.513", "nombre": "Mónica", "apellido": "Alustiza",
                "nacimiento": "9/11/59", "sexo": "F", "contacto": "(2254) 55-0000",
                "password": "clave1234"}
        body.update(over)
        return _CLIENT.post("/api/account/register", json=body)

    def test_registro_normaliza_y_setea_cookie(self):
        h = self._registro()
        self.assertEqual(h.status_code, 200, h.text)
        j = h.json()
        self.assertTrue(j["ok"])
        self.assertEqual(j["usuario"]["dni"], "13452513")     # DNI sin puntos
        self.assertEqual(j["usuario"]["nacimiento"], "1959-11-09")  # 9/11/59 -> 1959
        self.assertNotIn("pass_hash", j["usuario"])
        cookie = h.headers.get("set-cookie")
        self.assertIsNotNone(cookie)
        self.assertIn("st_ses=", cookie)
        self.assertIn("HttpOnly", cookie)

    def test_registro_rechaza_un_telefono_que_no_se_puede_llamar(self):
        """TASK-51 (26.10, persona Norma): el «2254» que el formulario traía
        escrito más el número completo daba 22542254000000, y se guardaba.
        Un teléfono incompleto o duplicado no se acepta; el rechazo dice qué
        escribir, con un ejemplo."""
        for malo in ("22542254000000", "2254", "550000"):
            h = self._registro(dni="27111222", contacto=malo)
            self.assertEqual(h.status_code, 400, malo)
            self.assertIn("2254 550000", h.json()["error"], malo)
        # Sin teléfono y con email sigue valiendo: el teléfono no es obligatorio.
        h = self._registro(dni="27111223", contacto="", email="norma@example.invalid")
        self.assertEqual(h.status_code, 200, h.text)

    def test_registro_yo_roundtrip(self):
        h = self._registro(dni="27888999")
        tok = _token_from_cookie(h.headers.get("set-cookie"))
        # con cookie -> 200
        hy = _CLIENT.get("/api/account/me", headers=_cookie_header(tok))
        self.assertEqual(hy.status_code, 200)
        self.assertEqual(hy.json()["usuario"]["dni"], "27888999")
        # sin cookie -> 401
        _CLIENT.cookies.clear()
        hn = _CLIENT.get("/api/account/me")
        self.assertEqual(hn.status_code, 401)

    def test_yo_no_trae_el_turno_por_delante(self):
        """/api/account/me devuelve solo el usuario: la barra siempre dice
        «Mi cuenta» (responsable del producto, 2026-10-29, revierte q708829), así que el campo
        `turno_proximo` y su consulta ya no existen, ni con un turno
        pendiente ni con uno aprobado por delante."""
        h = self._registro(dni="27555444")
        tok = _token_from_cookie(h.headers.get("set-cookie"))

        def yo():
            r = _CLIENT.get("/api/account/me", headers=_cookie_header(tok))
            self.assertEqual(r.status_code, 200, r.text)
            return r.json()

        self.assertEqual(set(yo()), {"usuario"})
        db_booking.create_booking_request(
            "27555444", "Mónica", "Alustiza", "1959-11-09", "", "clinica", "hospital")
        self.assertEqual(set(yo()), {"usuario"})

    def test_dni_duplicado_409(self):
        self._registro(dni="30111333")
        h2 = self._registro(dni="30111333")
        self.assertEqual(h2.status_code, 409)
        self.assertEqual(h2.json()["error"], "dni_registrado")

    def test_login_ok_y_malo(self):
        self._registro(dni="28777666", password="hola1234")
        # login ok
        ho = _CLIENT.post(
            "/api/account/login",
            json={"dni": "28.777.666", "password": "hola1234"})
        self.assertEqual(ho.status_code, 200)
        self.assertEqual(ho.json()["usuario"]["dni"], "28777666")
        self.assertIsNotNone(ho.headers.get("set-cookie"))
        # login con contraseña mala -> 401
        hb = _CLIENT.post(
            "/api/account/login",
            json={"dni": "28777666", "password": "incorrecta"})
        self.assertEqual(hb.status_code, 401)
        self.assertEqual(hb.json()["error"], "credenciales")
        # dni inexistente -> 401
        hx = _CLIENT.post(
            "/api/account/login", json={"dni": "99999999", "password": "x"})
        self.assertEqual(hx.status_code, 401)

    def test_salir_invalida_la_sesion(self):
        h = self._registro(dni="29123456")
        tok = _token_from_cookie(h.headers.get("set-cookie"))
        # salir
        hs = _CLIENT.post("/api/account/logout", headers=_cookie_header(tok))
        self.assertEqual(hs.status_code, 200)
        clear = hs.headers.get("set-cookie")
        self.assertIn("Max-Age=0", clear)
        # ahora yo con ese token -> 401 (sesión borrada)
        hy = _CLIENT.get("/api/account/me", headers=_cookie_header(tok))
        self.assertEqual(hy.status_code, 401)

    def test_registro_password_corta_400(self):
        h = _CLIENT.post(
            "/api/account/register", json={"dni": "31222111", "password": "ab"})
        self.assertEqual(h.status_code, 400)

    def test_registro_password_7_chars_400(self):
        # mínimo subido a 8 (revisión de seguridad 2026-07-17, ítem F)
        h = _CLIENT.post(
            "/api/account/register",
            json={"dni": "31222112", "password": "abcdefg"})
        self.assertEqual(h.status_code, 400)

    def test_login_throttle_por_ip(self):
        # ítem F: fuerza bruta al login de paciente -> 429 tras los intentos
        # fallidos de la ventana. Desde el acceso de médicos (2026-10-13) el
        # login de paciente comparte el contador del panel: el límite efectivo
        # es el MÍNIMO de ambos (cada fallo también alimenta el throttle del
        # admin). Estado en memoria: limpiar antes y después.
        from app.auth import admin_auth
        self._registro(dni="28777667", password="hola1234")
        accounts._LOGIN_ATTEMPTS.clear()
        admin_auth._ATTEMPTS.clear()
        efectivo = min(accounts._LOGIN_MAX, admin_auth._MAX_ATTEMPTS)
        try:
            for _ in range(efectivo):
                hb = _CLIENT.post(
                    "/api/account/login",
                    json={"dni": "28777667", "password": "mala"})
                self.assertEqual(hb.status_code, 401)
            ht = _CLIENT.post(
                "/api/account/login",
                json={"dni": "28777667", "password": "hola1234"})
            self.assertEqual(ht.status_code, 429)
            self.assertEqual(ht.json()["error"], "demasiados_intentos")
        finally:
            accounts._LOGIN_ATTEMPTS.clear()
            admin_auth._ATTEMPTS.clear()
        # con el throttle limpio, el login bueno vuelve a andar y limpia su IP
        ho = _CLIENT.post(
            "/api/account/login",
            json={"dni": "28777667", "password": "hola1234"})
        self.assertEqual(ho.status_code, 200)

    def test_alta_de_cuentas_throttleada_por_ip(self):
        # Revisión de seguridad de la demo pública (2026-08-01): el alta no
        # tenía freno ninguno, así que un script podía llenar la lista de
        # pacientes con DNIs inventados. Cuenta las cuentas CREADAS, no los
        # intentos fallidos. Estado en memoria: limpiar antes y después.
        accounts._REGISTERED.clear()
        try:
            for i in range(accounts._REGISTER_MAX):
                self._registro(dni=str(29500100 + i), password="hola1234")
            h = _CLIENT.post(
                "/api/account/register",
                json={"dni": "29500999", "password": "hola1234",
                      "email": "otro@example.com"})
            self.assertEqual(h.status_code, 429)
            self.assertEqual(h.json()["error"], "demasiadas_cuentas")
            self.assertIsNone(db_accounts.get_user("29500999"), "no debió crearse")
        finally:
            accounts._REGISTERED.clear()

    def test_un_alta_fallida_no_gasta_cupo(self):
        accounts._REGISTERED.clear()
        try:
            for _ in range(accounts._REGISTER_MAX + 2):
                h = _CLIENT.post(
                    "/api/account/register",
                    json={"dni": "29600111", "password": "ab"})
                self.assertEqual(h.status_code, 400)
            self._registro(dni="29600222", password="hola1234")
        finally:
            accounts._REGISTERED.clear()

    def test_registro_dni_invalido_400(self):
        h = _CLIENT.post(
            "/api/account/register",
            json={"dni": "123", "password": "clave1234"})
        self.assertEqual(h.status_code, 400)

    def test_cuenta_page_html(self):
        h = _CLIENT.get("/account")
        self.assertEqual(h.status_code, 200)
        body = h.text
        self.assertIn("Mi cuenta", body)
        from app.core import static_fingerprint
        self.assertIn(static_fingerprint.url("platform/account-form.mjs"), body)
        self.assertIn(static_fingerprint.url("platform/dependents.mjs"), body)
        self.assertNotIn("static('dependents.js')", body)

    def test_cuenta_autenticada_embebe_usuario_y_turnos_en_la_primera_respuesta(self):
        user = {"dni": "30123456", "nombre": "Sofía", "apellido": "Herrera"}
        rows = [{"id": 42, "servicio_id": "cardiologia-sampleton-hospital"}]
        with mock.patch("app.cuentas.accounts.current_user", return_value=user), \
                mock.patch("app.modules.turnos.booking_patient.patient_rows", return_value=rows):
            h = _CLIENT.get("/account")
        body = h.text
        self.assertIn('window.__ACCOUNT_BOOTSTRAP__={"usuario":', body)
        self.assertIn('"solicitudes":[{"id":42', body)
        from app.core import static_fingerprint
        self.assertIn(static_fingerprint.url('platform/account.mjs'), body)
        # Bootstrap consumption is exercised by the real module lifecycle
        # tests; the HTTP contract is data plus the canonical executable owner.

    def test_cuenta_demo_cambia_la_url_sin_redireccionar_ni_recargar(self):
        user = {"dni": "30123456", "nombre": "Sofía"}
        app = FastAPI()

        @app.get("/account/demo-access")
        def demo_access(request: Request):
            return accounts_router.render_account(
                request,
                bootstrap_user=user,
                set_cookie="st_ses=demo",
                canonical_path="/account",
            )

        with mock.patch("app.modules.turnos.booking_patient.patient_rows", return_value=[]):
            response = TestClient(app).get("/account/demo-access")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("set-cookie"), "st_ses=demo")
        body = response.text
        self.assertIn('window.__ACCOUNT_CANONICAL_PATH__="/account"', body)
        from app.core import static_fingerprint
        self.assertIn(static_fingerprint.url('platform/account.mjs'), body)
        self.assertNotIn('location', response.headers)

    def test_cuenta_page_muestra_banner_demo(self):
        # patient_web/site_*.py ya lo tenía (site_identity.py:213); /account y
        # /account/reset eran las dos superficies públicas que faltaban
        # (responsable del producto, plan de demo 2026-07-30).
        with _env.patched_env({"TURNOS_DEMO_MODE": "1"}, clear=False):
            h = _CLIENT.get("/account")
        self.assertEqual(h.status_code, 200)
        body = h.text
        self.assertIn("demo-installation", body)
        self.assertIn("Todos los datos son ficticios", body)

    def test_reset_page_muestra_banner_demo(self):
        with _env.patched_env({"TURNOS_DEMO_MODE": "1"}, clear=False):
            h = _CLIENT.get("/account/reset")
        self.assertEqual(h.status_code, 200)
        body = h.text
        self.assertIn("demo-installation", body)
        self.assertIn("Todos los datos son ficticios", body)

    def test_cuenta_page_sin_demo_mode_no_muestra_banner(self):
        h = _CLIENT.get("/account")
        body = h.text
        self.assertNotIn("demo-installation", body)

    def test_dependiente_agregar_listar_y_quitar(self):
        h = self._registro(dni="33222444")
        tok = _token_from_cookie(h.headers.get("set-cookie"))
        headers = _cookie_header(tok)
        body = {"dni": "50.123.456", "nombre": "Mateo", "apellido": "Gómez",
                "nacimiento": "12/03/2019", "sexo": "M", "vinculo": "hijo",
                "declaracion_jurada": True}
        ha = _CLIENT.post("/api/account/dependents", json=body, headers=headers)
        self.assertEqual(ha.status_code, 200, ha.text)
        self.assertEqual(ha.json()["dependiente"]["dni"], "50123456")

        hl = _CLIENT.get("/api/account/dependents", headers=headers)
        self.assertEqual(hl.status_code, 200)
        self.assertEqual(hl.json()["dependientes"][0]["nombre"], "Mateo")

        hb = _CLIENT.post(
            "/api/account/dependents/delete",
            json={"dni": "50123456"}, headers=headers)
        self.assertEqual(hb.status_code, 200)
        self.assertTrue(hb.json()["ok"])

    def test_dependiente_exige_declaracion_y_acepta_adulto_a_cargo(self):
        h = self._registro(dni="34222444")
        headers = _cookie_header(_token_from_cookie(h.headers.get("set-cookie")))
        base = {"dni": "50123457", "nombre": "Luz", "apellido": "Gómez",
                "nacimiento": "12/03/2019", "sexo": "F", "vinculo": "hijo"}
        hs = _CLIENT.post("/api/account/dependents", json=base, headers=headers)
        self.assertEqual(hs.status_code, 400)
        adult = dict(base, nacimiento="12/03/1990", declaracion_jurada=True)
        ha = _CLIENT.post("/api/account/dependents", json=adult, headers=headers)
        self.assertEqual(ha.status_code, 200)


# ---- OTP (WhatsApp gating de registro + recuperación) — con base ----------
#
# El transporte real (otp_send.enviar_otp -> WAHA) se stubea: estos tests NO
# deben pegarle a ninguna red ni mandar un mensaje real (responsable del producto, domingo, no
# outbound). `_sent` captura (contacto, code) de cada llamada para que el test
# pueda "leer" el código que un usuario real vería en su WhatsApp.

class TestOtp(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _CLIENT.cookies.clear()
        self._sent = []
        self._orig_enviar = accounts._send_otp_whatsapp

        def _fake_enviar(contacto, code):
            self._sent.append((contacto, code))
            return True

        accounts._send_otp_whatsapp = _fake_enviar
        self._orig_gate = accounts._otp_registration_required

    def tearDown(self):
        accounts._send_otp_whatsapp = self._orig_enviar
        accounts._otp_registration_required = self._orig_gate

    def _enviar(self, contacto, proposito):
        return _CLIENT.post(
            "/api/account/otp/send",
            json={"contacto": contacto, "proposito": proposito})

    def _verificar(self, contacto, proposito, code):
        return _CLIENT.post(
            "/api/account/otp/verify",
            json={"contacto": contacto, "proposito": proposito, "code": code})

    def test_enviar_manda_codigo_por_whatsapp_stub(self):
        h = self._enviar("2254551111", "registro")
        self.assertEqual(h.status_code, 200, h.text)
        self.assertTrue(h.json()["ok"])
        self.assertEqual(len(self._sent), 1)
        contacto, code = self._sent[0]
        # el OTP sale al contacto YA canónico (lo guarda _norm_tel)
        self.assertEqual(contacto, "+542254551111")
        self.assertRegex(code, r"^\d{6}$")

    def test_enviar_rate_limited_45s(self):
        self._enviar("2254551112", "registro")
        h2 = self._enviar("2254551112", "registro")
        self.assertEqual(h2.status_code, 429)
        self.assertEqual(len(self._sent), 1)  # el segundo no mandó nada nuevo

    def test_verificar_codigo_correcto_e_incorrecto(self):
        self._enviar("2254551113", "registro")
        _, code = self._sent[-1]
        hb = self._verificar("2254551113", "registro", "000000")
        self.assertEqual(hb.status_code, 400)
        self.assertFalse(hb.json()["ok"])
        hg = self._verificar("2254551113", "registro", code)
        self.assertEqual(hg.status_code, 200)
        self.assertTrue(hg.json()["ok"])

    def test_verificar_agota_intentos(self):
        self._enviar("2254551114", "registro")
        for _ in range(5):
            h = self._verificar("2254551114", "registro", "000000")
            self.assertEqual(h.status_code, 400)
        _, code = self._sent[-1]
        # el código correcto ya no sirve: se gastaron los 5 intentos
        h = self._verificar("2254551114", "registro", code)
        self.assertEqual(h.status_code, 400)

    def test_registro_bloqueado_sin_telefono_verificado_con_gating_prendido(self):
        accounts._otp_registration_required = lambda: True
        h = _CLIENT.post("/api/account/register", json={
            "dni": "36111222", "contacto": "2254551115", "password": "clave1234",
        })
        self.assertEqual(h.status_code, 400)
        self.assertEqual(h.json()["error"], "telefono_no_verificado")

    def test_registro_ok_con_telefono_verificado_y_gating_prendido(self):
        accounts._otp_registration_required = lambda: True
        contacto = "2254551116"
        self._enviar(contacto, "registro")
        _, code = self._sent[-1]
        self._verificar(contacto, "registro", code)
        h = _CLIENT.post("/api/account/register", json={
            "dni": "36111223", "contacto": contacto, "password": "clave1234",
        })
        self.assertEqual(h.status_code, 200, h.text)
        self.assertIsNotNone(h.headers.get("set-cookie"))
        # el ticket "verificado" es de un solo uso: un segundo registro con el
        # mismo contacto (otro DNI) ya no puede reusarlo sin un código nuevo.
        h2 = _CLIENT.post("/api/account/register", json={
            "dni": "36111224", "contacto": contacto, "password": "clave1234",
        })
        self.assertEqual(h2.status_code, 400)
        self.assertEqual(h2.json()["error"], "telefono_no_verificado")

    def test_registro_email_only_no_gatea_aunque_este_prendido(self):
        accounts._otp_registration_required = lambda: True
        h = _CLIENT.post("/api/account/register", json={
            "dni": "36111225", "email": "paciente@example.com", "password": "clave1234",
        })
        self.assertEqual(h.status_code, 200, h.text)

    def test_recuperar_manda_otp_por_whatsapp_cuando_hay_contacto(self):
        db_accounts.create_user("37111222", "Rita", "Paz", None, "F", "2254551117", "clave1234")
        h = _CLIENT.post("/api/account/recover", json={"dni": "37111222"})
        accounts.drain_detached()
        self.assertEqual(h.status_code, 200)
        self.assertTrue(h.json()["ok"])
        self.assertEqual(len(self._sent), 1)
        self.assertEqual(self._sent[0][0], "2254551117")

    def test_reset_otp_fija_contrasena_nueva(self):
        db_accounts.create_user("37111223", "Bruno", "Diaz", None, "M", "2254551118", "vieja1234")
        _CLIENT.post("/api/account/recover", json={"dni": "37111223"})
        accounts.drain_detached()
        _, code = self._sent[-1]

        # código incorrecto -> 400, no toca la contraseña
        hb = _CLIENT.post(
            "/api/account/reset_otp",
            json={"dni": "37111223", "code": "000000", "password": "nueva1234"})
        self.assertEqual(hb.status_code, 400)
        self.assertIsNotNone(db_accounts.verify_password("37111223", "vieja1234"))

        hg = _CLIENT.post(
            "/api/account/reset_otp",
            json={"dni": "37111223", "code": code, "password": "nueva1234"})
        self.assertEqual(hg.status_code, 200, hg.text)
        self.assertIsNotNone(hg.headers.get("set-cookie"))
        self.assertIsNone(db_accounts.verify_password("37111223", "vieja1234"))
        self.assertIsNotNone(db_accounts.verify_password("37111223", "nueva1234"))


class TestRecoveryReturnPath(unittest.TestCase):
    """The recovery link carries the page it was asked from (a half-done
    booking, product owner 2026-10-25), but only a path of this site."""

    def test_un_camino_propio_vuelve_tal_cual(self):
        destino = "/service/cardiologia/book?slots=1&slot=2026-10-27T09:00"
        self.assertEqual(accounts._return_path(destino), destino)

    def test_otro_sitio_o_basura_no_vuelve(self):
        for valor in (None, "", "service/x", "https://evil.example/x", "//evil.example/x",
                      "/\\evil.example", "/a b", "/a\nb", "/" + "x" * 1000):
            self.assertEqual(accounts._return_path(valor), "", repr(valor))


# ---- i18n: las cuatro páginas de accounts.py -------------------------------
# unit 16 del batch de traducción (2026-08-04): /account, /auth/test,
# /auth/sso y /account/reset ganaron {{i18n_bootstrap}} + {{t.*}}. Cubrimos:
# el castellano por defecto no se movió un byte en los puntos que ya
# probaban las suites hermanas (test_renaper_auth.py), y en inglés (cookie
# lang=en) el contenido que este módulo controla sale en inglés real, con
# <html lang="en"> y el bootstrap de window.SANA_I18N presente.

class TestI18nWiring(unittest.TestCase):
    # /auth/test y /auth/sso sólo sirven contenido cuando el simulador RENAPER
    # está habilitado (accounts._test_auth_enabled) — el módulo entero corre
    # en TURNOS_AUTH_MODE=password (línea 26 de este archivo) para el resto de
    # las pruebas, así que estas dos páginas necesitan su propio override,
    # igual que test_renaper_auth.py.
    def setUp(self):
        _CLIENT.cookies.clear()
        # Registered before the environment, so they run after it is put back.
        self.addCleanup(i18n.invalidate)
        self.addCleanup(config._reset_cache_para_tests)
        # Real signing secret so the native request's peer marker verifies under
        # a TURNOS_ASGI=1 CI pass (proxy_trust only trusts a marker when a
        # real secret is configured — fail-closed otherwise).
        _env.set_env(self, {"LANGUAGES": "es,en", "TURNOS_AUTH_MODE": "renaper", "TURNOS_RENAPER_PROVIDER": "stub", "TURNOS_ASGI": "1", "TURNOS_PROXY_TRUST_SECRET": "test-proxy-trust-i18n"})
        config._reset_cache_para_tests()
        i18n.invalidate()

    def test_cuenta_en_ingles_trae_html_lang_en_y_bootstrap(self):
        html = _CLIENT.get("/account", headers=_native_headers("en")).text
        self.assertIn('<html lang="en"', html)
        self.assertIn("window.SANA_I18N=", html)
        self.assertIn("Sign in", html)  # account.login.title / tx() de reemplazo
        self.assertIn("My appointments", html)  # account.dash.nav_turnos

    def test_cuenta_en_castellano_no_cambia(self):
        html = _CLIENT.get("/account", headers=_native_headers()).text
        self.assertIn('<html lang="es"', html)
        self.assertIn("Crear cuenta", html)
        # Login is rendered by the native controller, not inline source.
        # account-lifecycle.e2e exercises the visible Spanish login action.
        self.assertIn("Mis turnos", html)

    def test_test_auth_en_ingles_trae_html_lang_en_y_bootstrap(self):
        html = _CLIENT.get("/auth/test", headers=_native_headers("en")).text
        self.assertIn('<html lang="en"', html)
        self.assertIn("window.SANA_I18N=", html)
        self.assertIn("RENAPER authentication simulator", html)
        self.assertIn("Create random person", html)

    def test_test_auth_en_castellano_no_cambia(self):
        html = _CLIENT.get("/auth/test", headers=_native_headers()).text
        self.assertIn('<html lang="es"', html)
        self.assertIn("Simulador de autenticación RENAPER", html)
        self.assertIn("Crear persona aleatoria", html)

    def test_test_auth_y_sso_resuelven_sus_assets_fingerprinted(self):
        # Regresión 2026-08-08: _serve_test_auth/_serve_sso no pasaban por
        # static_fingerprint.render() y servían el literal {{static.…}} —
        # el JS de la página nunca cargaba. Acá se fija que el placeholder
        # se resuelve de verdad en ambas.
        from app.core import static_fingerprint
        for path, asset in (("/auth/test", "platform/auth-test.mjs"),
                            ("/auth/sso", "platform/i18n.mjs")):
            html = _CLIENT.get(path, headers=_native_headers()).text
            self.assertNotIn("{{static.", html, path)
            self.assertIn(static_fingerprint.url(asset), html, path)

    def test_sso_en_ingles_trae_html_lang_en_y_bootstrap(self):
        html = _CLIENT.get("/auth/sso", headers=_native_headers("en")).text
        self.assertIn('<html lang="en"', html)
        self.assertIn("window.SANA_I18N=", html)
        self.assertIn("ID Number", html)
        self.assertIn("Sign In", html)

    def test_sso_en_castellano_no_cambia(self):
        html = _CLIENT.get("/auth/sso", headers=_native_headers()).text
        self.assertIn('<html lang="es"', html)
        self.assertIn("mero de Documento", html)  # "Número de Documento"
        self.assertIn("Iniciá Sesión", html)

    def test_reset_en_ingles_trae_html_lang_en_y_bootstrap(self):
        html = _CLIENT.get("/account/reset", headers=_native_headers("en")).text
        self.assertIn('<html lang="en"', html)
        self.assertIn("window.SANA_I18N=", html)
        self.assertIn("Choose your new password", html)
        self.assertIn("Loading…", html)

    def test_reset_en_castellano_no_cambia(self):
        html = _CLIENT.get("/account/reset", headers=_native_headers()).text
        self.assertIn('<html lang="es"', html)
        self.assertIn("Elegí tu contraseña nueva", html)
        self.assertIn("Cargando…", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
