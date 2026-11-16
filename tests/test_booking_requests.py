"""test_booking_requests.py — pruebas del backend de solicitudes (Agent C).

Correr:  python3 -m unittest discover test    (desde la raíz del repo)

Usa TURNOS_FAKE_AI=1 (sin red). Las pruebas de base crean/dropean una base
dedicada `sana_turnos_test` (conectándose a la maintenance db `postgres`); si
Postgres no está accesible, esas pruebas se saltean con un mensaje claro.
"""

import os
import re
import sys
import json
import base64
import unittest
from unittest.mock import patch
from datetime import datetime, timezone, timedelta

# raíz del repo en el path para importar los módulos de Agent C
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# entorno determinístico ANTES de importar los módulos
import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
# Estos tests crean varias solicitudes del MISMO paciente y el mismo servicio
# para probar otra cosa (auto-confirmación, detalle, identidad). El límite de
# turnos activos por paciente (booking_limits.py, 1 por defecto) los cortaría
# con un 409 que no tiene nada que ver con lo que miden — se sube acá, y la
# regla en sí se prueba en test_booking_limits.py y en TestLimiteTurnosActivos
# de más abajo.
_env.process_set(MAX_ACTIVE_REQUESTS="60")

import zlib          # noqa: E402
from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_hsi  # noqa: E402
from app.core import db_slots  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.core import db_wire  # noqa: E402
from app.core import tz  # noqa: E402
import _dbtest        # noqa: E402
from app.ai import ai_referral  # noqa: E402
from app.modules.turnos import booking_capacity  # noqa: E402
from app.modules.turnos import booking_create  # noqa: E402
from app.modules.turnos import booking_decisions  # noqa: E402
from app.modules.turnos import booking_queue  # noqa: E402
from app.modules.turnos import booking_referrals  # noqa: E402
from app.modules.turnos import booking_requests   # noqa: E402
from app.ai import abuse_guard  # noqa: E402
from app.ai import ai_cooldown  # noqa: E402
from app.ai import ai_costs  # noqa: E402
from app.core import config  # noqa: E402
from app.auth import turnstile         # noqa: E402
from app.modules.turnos import waitlist           # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app import main as app_module  # noqa: E402

_CLIENT = TestClient(app_module.app)
from app.notifications import mailgun as email_mailgun     # noqa: E402
from app.core import i18n  # noqa: E402
from _packtest import reference_pack_test  # noqa: E402
# el checkout de dev tiene TURNOS_TURNSTILE_* en .env — los tests corren sin gate
abuse_guard.ENV_PATH = ""
turnstile.ENV_PATH = ""
config.ENV_PATH = ""  # idem: no leer el .env real del checkout de dev
# ...y también pueden venir por os.environ (shell que sourceó el .env). Sin esta
# línea el archivo SOLO pasaba cuando otro módulo de test (alfabéticamente
# anterior, p.ej. test_admin_users) vaciaba el secret por accidente — parado
# solo, /api/analyze-order devolvía 403 turnstile (descubierto 2026-07-18).
_env.process_set(TURNOS_TURNSTILE_SECRET="", TURNOS_TURNSTILE_SITEKEY=None)

HAVE_DB = False
DB_SKIP_REASON = ""
_TEST_URL = ""
_MAINT_URL = ""
_ORIG_ORDENES = booking_requests.REFERRALS_DIR
_ORIG_SERVICIOS = booking_requests.SERVICES_PATH

_db = _dbtest.PerModuleDb("sana_turnos_test", seed=lambda: db_booking.get_booking_request(-1))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    import tempfile
    booking_requests.REFERRALS_DIR = tempfile.mkdtemp(prefix="ordenes-test-")

    # catálogo de servicios propio y determinístico (el real lo autora otro agente)
    cat = os.path.join(booking_requests.REFERRALS_DIR, "servicios-test.json")
    with open(cat, "w", encoding="utf-8") as f:
        json.dump({"servicios": [
            {"id": "cardiologia", "nombre": "Cardiología"},
            {"id": "dermatologia", "nombre": "Dermatología"},
            {"id": "pediatria-eco", "nombre": "Ecografía infantil", "edad": {"min": None, "max": 15}},
            {"id": "laboratorio-test", "nombre": "Laboratorio y extracciones"},
            {"id": "control-salud", "nombre": "Control de salud",
             "modo": "solicitud", "auto_confirmar": True},
        ]}, f)
    booking_requests.SERVICES_PATH = cat
    # POST /api/requests refuses a center the catalog does not have (schema
    # review №10): these tests book at two made-up ones.
    global _CENTERS_PATCH
    _CENTERS_PATCH = patch("app.patient_web.catalog.centers", return_value=[
        {"slug": "hospital"}, {"slug": "hospital-pinamar"}])
    _CENTERS_PATCH.start()

    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _CENTERS_PATCH.stop()
    import shutil
    shutil.rmtree(booking_requests.REFERRALS_DIR, ignore_errors=True)
    booking_requests.REFERRALS_DIR = _ORIG_ORDENES
    booking_requests.SERVICES_PATH = _ORIG_SERVICIOS
    _db.tear_down()


# 1x1 PNG transparente
_PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
            "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

# usuario de sesión (la identidad ahora sale de la sesión, no del body)
_USER = {"dni": "27888999", "nombre": "Ana", "apellido": "López",
         "nacimiento": "1979-06-01", "sexo": "F", "contacto": "wa:11-5555"}


# ---- ai_referral (sin red, sin base) ------------------------------------------

# Lo que toda orden real trae y sin lo cual no se aprueba: quién la firmó,
# con qué matrícula y de qué día es (ai_referral._verdict_3pass). Los tests
# de abajo miran DNI/especialidad/jurisdicción, así que arrancan de una orden
# COMPLETA en todo lo demás — si no, lo que estarían midiendo es el gate de
# completitud y no lo que dice su nombre.
_ORDEN_COMPLETA = {"medico_firmante": "Dr. Gómez", "matricula": "MP 1234",
                   "fecha": "01/08/2026"}


class TestAiOrden(unittest.TestCase):
    def test_fake_path(self):
        sol = {"dni": "13452513", "nombre": "Mónica", "apellido": "Alustiza",
               "servicio_id": "cardiologia", "especialidad": "Cardiología"}
        r = ai_referral.analyze_referral(None, "image/png", sol)
        self.assertEqual(r["verdict"], "parece_correcta")
        self.assertEqual(r["modelo"], "fake")
        self.assertTrue(r["campos"]["dni"]["coincide"])
        self.assertIn("Cardiología", r["resumen"])

    def test_verdict_dni_mismatch_dudosa(self):
        leido = {"dni": "99999999", "especialidad_solicitada": "Cardiología",
                 "paciente": "Juan Perez", "legible": True}
        sol = {"dni": "13452513", "especialidad": "Cardiología"}
        r = ai_referral._verdict_from(leido, sol, "m")
        self.assertEqual(r["verdict"], "dudosa")
        self.assertFalse(r["campos"]["dni"]["coincide"])
        self.assertTrue(r["campos"]["especialidad"]["coincide"])

    def test_verdict_all_match(self):
        leido = {"dni": "13.452.513", "especialidad_solicitada": "Cardiología",
                 "medico_firmante": "Dr. Gómez", "matricula": "MP 1234", "legible": True}
        sol = {"dni": "13452513", "especialidad": "Cardiología"}
        r = ai_referral._verdict_from(leido, sol, "m")
        self.assertEqual(r["verdict"], "parece_correcta")
        self.assertTrue(r["campos"]["dni"]["coincide"])

    def test_verdict_illegible(self):
        leido = {"dni": None, "especialidad_solicitada": None, "legible": False}
        sol = {"dni": "13452513", "especialidad": "Cardiología"}
        r = ai_referral._verdict_from(leido, sol, "m")
        self.assertEqual(r["verdict"], "ilegible")

    def test_esp_vacia_no_es_no_coincide(self):
        # Orden legible, DNI ok, pero sin especialidad leída → NO debe marcarse
        # "no coincide" (coincide=None) y NO debe empujar a dudosa. (responsable del producto, 2026-07-11)
        leidos = [{"dni": "13452513", "especialidad_solicitada": None,
                   "paciente": "Mónica", "es_orden_medica": True, "legible": True,
                   **_ORDEN_COMPLETA}]
        r = ai_referral._verdict_3pass(leidos, {"dni": "13452513", "especialidad": "Cardiología"}, "m", 0.0)
        self.assertIsNone(r["campos"]["especialidad"]["coincide"])
        self.assertEqual(r["verdict"], "parece_correcta")

    # ---- completitud de la orden (responsable del producto, 2026-08-01: «la orden no tiene
    # ninguna de las cosas que suele tener» y aun así decía PARECE CORRECTA) --
    def test_sin_firma_ni_matricula_no_es_parece_correcta(self):
        leidos = [{"dni": "13452513", "especialidad_solicitada": "Cardiología",
                   "es_orden_medica": True, "legible": True, "fecha": "01/08/2026"}]
        sol = {"dni": "13452513", "especialidad": "Cardiología"}
        r = ai_referral._verdict_3pass(leidos, sol, "m", 0.0)
        self.assertEqual(r["verdict"], "dudosa")
        self.assertEqual(r["documento"]["campos_faltantes"], ["firma_matricula"])
        self.assertIn("firma", r["resumen"])

    def test_sin_fecha_no_es_parece_correcta(self):
        leidos = [{"dni": "13452513", "especialidad_solicitada": "Cardiología",
                   "es_orden_medica": True, "legible": True,
                   "medico_firmante": "Dr. Gómez", "matricula": "MP 1234"}]
        sol = {"dni": "13452513", "especialidad": "Cardiología"}
        r = ai_referral._verdict_3pass(leidos, sol, "m", 0.0)
        self.assertEqual(r["verdict"], "dudosa")
        self.assertEqual(r["documento"]["campos_faltantes"], ["fecha"])

    def test_solo_matricula_alcanza_como_firma(self):
        # El sello con el número, sin nombre a mano, es una orden firmada.
        leidos = [{"dni": "13452513", "especialidad_solicitada": "Cardiología",
                   "es_orden_medica": True, "legible": True,
                   "matricula": "MP 1234", "fecha": "01/08/2026"}]
        sol = {"dni": "13452513", "especialidad": "Cardiología"}
        r = ai_referral._verdict_3pass(leidos, sol, "m", 0.0)
        self.assertEqual(r["verdict"], "parece_correcta")
        self.assertEqual(r["documento"]["campos_faltantes"], [])

    def test_documento_equivocado_no_lista_campos_faltantes(self):
        # Una factura de peluquería no "le falta la matrícula": el panel ya
        # dice lo que pasa, listar campos ausentes sería ruido.
        leidos = [{"es_orden_medica": False, "tipo_documento": "un ticket", "legible": True}]
        r = ai_referral._verdict_3pass(leidos, {"dni": "13452513"}, "m", 0.0)
        self.assertEqual(r["verdict"], "documento_equivocado")
        self.assertEqual(r["documento"]["campos_faltantes"], [])

    def test_esp_mismatch_real_sigue_dudosa(self):
        # Un destino leído que difiere del solicitado SÍ es conflicto real.
        leidos = [{"dni": "13452513", "especialidad_solicitada": "Cirugía Plástica",
                   "paciente": "Mónica", "es_orden_medica": True, "legible": True}]
        r = ai_referral._verdict_3pass(leidos, {"dni": "13452513", "especialidad": "Cardiología"}, "m", 0.0)
        self.assertFalse(r["campos"]["especialidad"]["coincide"])
        self.assertEqual(r["verdict"], "dudosa")

    def test_practicas_y_diagnostico_se_extraen(self):
        leidos = [
            {"dni": "13452513", "es_orden_medica": True, "legible": True,
             "diagnostico": "Control", "practicas": ["Hemograma", "Glucemia"]},
            {"dni": "13452513", "es_orden_medica": True, "legible": True,
             "diagnostico": "Control", "practicas": ["Hemograma"]},
        ]
        r = ai_referral._verdict_3pass(leidos, {"dni": "13452513"}, "m", 0.0)
        self.assertIn("Hemograma", r["campos"]["practicas"]["valor"])
        self.assertIn("Glucemia", r["campos"]["practicas"]["valor"])
        self.assertEqual(r["campos"]["diagnostico"]["valor"], "Control")

    def test_extract_json_fenced(self):
        self.assertEqual(ai_referral.extract_json('```json\n{"a":1}\n```'), {"a": 1})
        self.assertEqual(ai_referral.extract_json('texto {"b":2} cola'), {"b": 2})
        self.assertEqual(ai_referral.extract_json("nada"), {})

    # ---- 3 pasadas (fake + reconciliación en código) ----
    def test_3pass_fake_ok(self):
        import tempfile
        ctx = {"dni": "13452513", "nombre": "Mónica", "apellido": "Alustiza",
               "servicio_id": "cardiologia", "especialidad": "Cardiología"}
        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            f.write(b"fake image")
            f.flush()
            r = ai_referral.analyze_referral_3pass(f.name, "image/png", ctx)
        self.assertEqual(r["verdict"], "parece_correcta")
        self.assertEqual(r["pases"], 1)
        self.assertTrue(r["documento"]["es_orden"])
        self.assertIsNone(r["documento"]["tipo"])
        self.assertEqual(r["campos"]["dni"]["acuerdo"], 1.0)
        self.assertEqual(r["modelo"], "fake")

    def test_3pass_fake_incluye_prepaga_y_afiliado(self):
        # handoff 2026-07-17: "there could be also the number of prepaga —
        # 2 fields" — el modo FAKE también los completa para que tests/dev
        # vean las filas nuevas del admin sin pegarle a la red.
        import tempfile
        ctx = {"dni": "13452513", "nombre": "Mónica", "apellido": "Alustiza",
               "servicio_id": "cardiologia", "especialidad": "Cardiología"}
        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            f.write(b"fake image")
            f.flush()
            r = ai_referral.analyze_referral_3pass(f.name, "image/png", ctx)
        self.assertIn("prepaga", r["campos"])
        self.assertIn("afiliado", r["campos"])
        self.assertTrue(r["campos"]["prepaga"]["valor"])
        self.assertTrue(r["campos"]["afiliado"]["valor"])

    def test_3pass_fake_factura_documento_equivocado(self):
        # el FAKE con filename 'factura' gana incluso sobre el guard de archivo
        import tempfile
        ctx = {"filename": "factura-peluqueria.jpg", "especialidad": "Cardiología"}
        with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
            f.write(b"fake image")
            f.flush()
            r = ai_referral.analyze_referral_3pass(f.name, "image/jpeg", ctx)
        self.assertEqual(r["verdict"], "documento_equivocado")
        self.assertFalse(r["documento"]["es_orden"])
        self.assertIn("factura", r["documento"]["tipo"])
        self.assertTrue(r["documento"]["jurisdiccion_valida"])
        self.assertIsNone(r["documento"]["jurisdiccion_detectada"])

    def test_3pass_fake_extranjero_otra_jurisdiccion(self):
        # el FAKE con filename 'extranjero' fuerza el caso de jurisdicción
        # ajena (una orden REAL, pero de otro sistema de salud): es_orden
        # sigue en true -- no es "documento equivocado" -- lo que cambia es
        # jurisdiccion_valida.
        import tempfile
        ctx = {"dni": "13452513", "filename": "orden-extranjero.jpg",
               "especialidad": "Cardiología"}
        with tempfile.NamedTemporaryFile(suffix=".jpg") as f:
            f.write(b"fake image")
            f.flush()
            r = ai_referral.analyze_referral_3pass(f.name, "image/jpeg", ctx)
        self.assertEqual(r["verdict"], "dudosa")
        self.assertTrue(r["documento"]["es_orden"])
        self.assertFalse(r["documento"]["jurisdiccion_valida"])
        self.assertEqual(r["documento"]["jurisdiccion_detectada"], "Chile")
        self.assertIn("jurisdicción", r["resumen"])

    def test_3pass_reconcile_dni_majority(self):
        leidos = [
            {"dni": "13452513", "especialidad_solicitada": "Cardiología", "es_orden_medica": True, "legible": True, **_ORDEN_COMPLETA},
            {"dni": "13.452.513", "especialidad_solicitada": "Cardiologia", "es_orden_medica": True, "legible": True, **_ORDEN_COMPLETA},
            {"dni": "99999999", "especialidad_solicitada": "Cardiología", "es_orden_medica": True, "legible": True, **_ORDEN_COMPLETA},
        ]
        sol = {"dni": "13452513", "especialidad": "Cardiología"}
        r = ai_referral._verdict_3pass(leidos, sol, "m", 0.003)
        self.assertEqual(r["verdict"], "parece_correcta")
        self.assertTrue(r["campos"]["dni"]["coincide"])
        self.assertEqual(r["campos"]["dni"]["acuerdo"], round(2 / 3, 3))
        self.assertEqual(r["pases"], 3)
        self.assertEqual(r["costo_usd"], 0.003)

    def test_3pass_reconcile_documento_equivocado(self):
        leidos = [
            {"es_orden_medica": False, "tipo_documento": "un ticket de supermercado", "legible": True},
            {"es_orden_medica": False, "tipo_documento": "un ticket de supermercado", "legible": True},
            {"es_orden_medica": True, "legible": True},
        ]
        sol = {"dni": "13452513", "especialidad": "Cardiología"}
        r = ai_referral._verdict_3pass(leidos, sol, "m", 0.0)
        self.assertEqual(r["verdict"], "documento_equivocado")
        self.assertFalse(r["documento"]["es_orden"])
        self.assertIn("ticket", r["documento"]["tipo"])

    def test_3pass_reconcile_otra_jurisdiccion(self):
        # Mayoría de las lecturas marca la orden como emitida fuera de la
        # jurisdicción de esta instalación -- orthogonal a si el DNI/nombre
        # coinciden (acá coinciden, y aun así no pasa a "parece_correcta").
        leidos = [
            {"dni": "13452513", "especialidad_solicitada": "Cardiología",
             "es_orden_medica": True, "legible": True,
             "jurisdiccion_valida": False, "jurisdiccion_detectada": "Chile"},
            {"dni": "13452513", "especialidad_solicitada": "Cardiología",
             "es_orden_medica": True, "legible": True,
             "jurisdiccion_valida": False, "jurisdiccion_detectada": "Chile"},
            {"dni": "13452513", "especialidad_solicitada": "Cardiología",
             "es_orden_medica": True, "legible": True},
        ]
        sol = {"dni": "13452513", "especialidad": "Cardiología"}
        r = ai_referral._verdict_3pass(leidos, sol, "m", 0.0)
        self.assertEqual(r["verdict"], "dudosa")
        self.assertTrue(r["documento"]["es_orden"])
        self.assertFalse(r["documento"]["jurisdiccion_valida"])
        self.assertEqual(r["documento"]["jurisdiccion_detectada"], "Chile")
        self.assertIn("jurisdicción", r["resumen"])

    def test_3pass_reconcile_jurisdiccion_ambigua_no_rechaza(self):
        # Ninguna lectura marcó jurisdiccion_valida=false explícitamente:
        # el default (True) gana -- nunca se inventa un rechazo de una
        # instalación que simplemente no leyó el dato.
        leidos = [
            {"dni": "13452513", "especialidad_solicitada": "Cardiología",
             "es_orden_medica": True, "legible": True, **_ORDEN_COMPLETA},
        ]
        sol = {"dni": "13452513", "especialidad": "Cardiología"}
        r = ai_referral._verdict_3pass(leidos, sol, "m", 0.0)
        self.assertEqual(r["verdict"], "parece_correcta")
        self.assertTrue(r["documento"]["jurisdiccion_valida"])
        self.assertIsNone(r["documento"]["jurisdiccion_detectada"])

    def test_3pass_no_reference_dni(self):
        # análisis antes del registro: no hay dni de referencia -> coincide=None
        leidos = [
            {"dni": "13452513", "especialidad_solicitada": "Cardiología", "es_orden_medica": True, "legible": True, **_ORDEN_COMPLETA},
            {"dni": "13452513", "especialidad_solicitada": "Cardiología", "es_orden_medica": True, "legible": True, **_ORDEN_COMPLETA},
            {"dni": "13452513", "especialidad_solicitada": "Cardiología", "es_orden_medica": True, "legible": True, **_ORDEN_COMPLETA},
        ]
        r = ai_referral._verdict_3pass(leidos, {"especialidad": "Cardiología"}, "m", 0.0)
        self.assertEqual(r["verdict"], "parece_correcta")
        self.assertIsNone(r["campos"]["dni"]["coincide"])

    def test_reconciliar_token_prelogin_detecta_dni_y_nombre_ajenos(self):
        pre = {
            "verdict": "parece_correcta", "confianza": 0.9,
            "campos": {
                "dni": {"valor": "20456781", "coincide": None},
                "paciente": {"valor": "Ilya Belov", "coincide": None},
                "especialidad": {"valor": "Cardiología", "coincide": True},
                "practicas": {"valor": ["Ergometría"]},
            },
        }
        r = ai_referral.reconcile_with_request(pre, {
            "dni": "27888999", "nombre": "Ana", "apellido": "López",
            "especialidad": "Ergometría",
        })
        self.assertEqual(r["verdict"], "dudosa")
        self.assertFalse(r["campos"]["dni"]["coincide"])
        self.assertFalse(r["campos"]["paciente"]["coincide"])
        self.assertTrue(r["campos"]["especialidad"]["coincide"])
        self.assertIn("no coincide", r["resumen"])

    def test_reconciliar_no_confunde_numero_afiliado_con_dni(self):
        pre = {"verdict": "parece_correcta", "confianza": 0.9,
               "campos": {"dni": {"valor": "8000063055088010001"},
                           "paciente": {"valor": "Ana López"},
                           "especialidad": {"valor": "Cardiología"}}}
        r = ai_referral.reconcile_with_request(pre, {
            "dni": "27888999", "nombre": "Ana", "apellido": "López",
            "especialidad": "Cardiología",
        })
        self.assertEqual(r["verdict"], "parece_correcta")
        self.assertIsNone(r["campos"]["dni"]["coincide"])
        self.assertTrue(r["campos"]["paciente"]["coincide"])

    # ---- respaldo con modelo gratis cuando el principal se queda sin cuota ----
    def test_fallback_model_env(self):
        _env.set_env(self, TURNOS_AI_MODEL_FALLBACK=None)
        self.assertEqual(ai_referral._fallback_model(), ai_referral.DEFAULT_FALLBACK_MODEL)
        _env.set_env(self, TURNOS_AI_MODEL_FALLBACK="off")
        self.assertEqual(ai_referral._fallback_model(), "")
        _env.set_env(self, TURNOS_AI_MODEL_FALLBACK="prov/modelo:free")
        self.assertEqual(ai_referral._fallback_model(), "prov/modelo:free")

    def _run_3pass_no_fake(self, fake_read):
        """Corre analizar_orden_3pass con el guard FAKE apagado y _read_once
        parcheado, sobre un PNG real en disco. Devuelve el ai_json."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(base64.b64decode(_PNG_B64))
            path = f.name
        orig_read = ai_referral._read_once
        try:
            with _env.patched_env(
                    TURNOS_FAKE_AI=None,                   # apaga el atajo determinístico
                    OPENROUTER_API_KEY="sk-test",          # que pase el guard de key
                    TURNOS_AI_MODEL_FALLBACK="prov/modelo:free",
                    TURNOS_AI_FORCE="up"):                 # hermético: no depende del estado global de ai_health
                ai_referral._read_once = fake_read
                return ai_referral.analyze_referral_3pass(path, "image/png",
                                                     {"especialidad": "Cardiología"})
        finally:
            ai_referral._read_once = orig_read
            os.unlink(path)

    def test_3pass_fallback_gratis_cuando_principal_sin_cuota(self):
        # el principal (paid) devuelve None (403 cuota); el ":free" sí lee.
        def fake_read(content, model, api_key, temperature):
            if model.endswith(":free"):
                return ({"dni": "13452513", "especialidad_solicitada": "Cardiología",
                         "es_orden_medica": True, "legible": True,
                         **_ORDEN_COMPLETA}, 0.0, False)
            return (None, 0.0, False)
        r = self._run_3pass_no_fake(fake_read)
        self.assertEqual(r["verdict"], "parece_correcta")
        self.assertTrue(r["modelo"].endswith(":free"))
        self.assertEqual(r["pases"], 1)

    def test_3pass_revision_manual_cuando_falla_todo(self):
        # ni el principal ni el respaldo devuelven algo -> revision_manual,
        # pero el wizard ya no se lo muestra al paciente.
        def fake_read(content, model, api_key, temperature):
            return (None, 0.0, False)
        r = self._run_3pass_no_fake(fake_read)
        self.assertEqual(r["verdict"], "revision_manual")


# ---- validación / auth (route layer, sin base cuando corta antes) ----------

class TestRouteGuards(unittest.TestCase):
    def test_admin_sin_token_401(self):
        h = _CLIENT.request("GET", "/api/admin/requests")
        self.assertEqual(h.status_code, 401)

    def test_admin_cookie_mala_401(self):
        h = _CLIENT.request("GET", "/api/admin/requests", headers={"Cookie": "st_admin=basura.firmafalsa"})
        self.assertEqual(h.status_code, 401)

    def test_ruta_desconocida_devuelve_false(self):
        h = _CLIENT.request("GET", "/api/otra-cosa")
        self.assertEqual(h.status_code, 404)


class TestPublicRowHoraArt(unittest.TestCase):
    """Repro del bug (2026-07-18): el proceso puede correr con el TZ del
    sistema en otro huso (se vio America/New_York en dev) — Postgres guarda
    turno_dt como timestamptz y psycopg lo devuelve aware en ESE huso, así que
    `_jsonable`/`_public_row` mostraban la hora LITERAL de la columna (09:30)
    en vez de la hora real que ve el paciente en Buenos Aires (10:30 ART).
    No necesita la base: se arma la fila a mano con un turno_dt cuyo tzinfo
    NO es ART, mismo contrato que devuelve `db_booking.get_booking_request`."""

    def _row(self, turno_dt):
        return {
            "id": 69, "servicio_id": "cardiologia", "centro_slug": "hospital",
            "estado": "aprobada", "turno_dt": turno_dt,
            "preferencia_horario": None, "motivo_rechazo": None, "created": None,
            "medico_asignado": "Dra. Marcela Duette",
            "dni": "90000001", "nombre": "Prueba", "apellido": "Uno",
            "ai_json": {},
        }

    def test_turno_dt_se_sirve_en_hora_de_pared_art(self):
        # El instante real ES 10:30 ART; la columna llega con tzinfo -04:00
        # (America/New_York en dev) y por lo tanto el literal es "09:30".
        dt = datetime(2026, 7, 24, 9, 30, tzinfo=timezone(timedelta(hours=-4)))
        pub = booking_requests._public_row(self._row(dt))
        m = re.match(r"^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})-03:00$", pub["turno_dt"])
        self.assertIsNotNone(m, f"turno_dt no vino en ART: {pub['turno_dt']!r}")
        self.assertEqual(m.group(1), "2026-07-24")
        self.assertEqual(m.group(2), "10")  # NO "09" — ese es el bug
        self.assertEqual(m.group(3), "30")

    def test_turno_dt_utc_tambien_se_convierte_a_art(self):
        dt = datetime(2026, 7, 24, 13, 30, tzinfo=timezone.utc)
        pub = booking_requests._public_row(self._row(dt))
        self.assertTrue(pub["turno_dt"].startswith("2026-07-24T10:30:00-03:00"))

    def test_turno_dt_none_no_rompe(self):
        pub = booking_requests._public_row(self._row(None))
        self.assertIsNone(pub["turno_dt"])


# ---- POST /api/analyze-order (sin auth, sin base — disco + FAKE AI) -------

class TestAnalizarOrden(unittest.TestCase):
    def setUp(self):
        abuse_guard._HITS.clear()
        ai_costs._WINDOW.clear()
        ai_cooldown._reset_para_tests()

    def test_token_y_ai(self):
        body = {"servicio_id": "cardiologia", "especialidad": "Cardiología",
                "orden": {"filename": "orden.png", "mime": "image/png", "data_b64": _PNG_B64}}
        h = _CLIENT.request("POST", "/api/analyze-order", json=body)
        self.assertEqual(h.status_code, 200)
        self.assertTrue(h.json()["ok"])
        self.assertTrue(h.json()["token"])
        self.assertEqual(h.json()["ai"]["verdict"], "parece_correcta")
        self.assertEqual(h.json()["ai"]["pases"], 1)
        self.assertTrue(h.json()["ai"]["documento"]["es_orden"])
        # la metadata quedó guardada del lado del server
        meta = booking_referrals._load_pending(h.json()["token"])
        self.assertIsNotNone(meta)
        self.assertTrue(os.path.exists(meta["orden_path"]))
        self.assertEqual(meta["ai"]["verdict"], "parece_correcta")

    def test_factura_documento_equivocado(self):
        body = {"servicio_id": "cardiologia",
                "orden": {"filename": "factura-de-la-peluqueria.png",
                          "mime": "image/png", "data_b64": _PNG_B64}}
        h = _CLIENT.request("POST", "/api/analyze-order", json=body)
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["ai"]["verdict"], "documento_equivocado")
        self.assertFalse(h.json()["ai"]["documento"]["es_orden"])
        self.assertIn("factura", (h.json()["ai"]["documento"]["tipo"] or ""))

    def test_orden_de_otra_jurisdiccion(self):
        body = {"servicio_id": "cardiologia",
                "orden": {"filename": "orden-extranjero.png",
                          "mime": "image/png", "data_b64": _PNG_B64}}
        h = _CLIENT.request("POST", "/api/analyze-order", json=body)
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["ai"]["verdict"], "dudosa")
        self.assertTrue(h.json()["ai"]["documento"]["es_orden"])
        self.assertFalse(h.json()["ai"]["documento"]["jurisdiccion_valida"])
        self.assertEqual(h.json()["ai"]["documento"]["jurisdiccion_detectada"], "Chile")

    def test_sin_orden_400(self):
        h = _CLIENT.request("POST", "/api/analyze-order", json={"servicio_id": "cardiologia"})
        self.assertEqual(h.status_code, 400)


# ---- GET /api/orden-preview/<token> (sin auth: el token ES la capacidad) --
#
# La miniatura que usa Confirmá cuando la orden llegó SÓLO con token (handoff
# de "Subí tu orden", o vuelta de SSO — service.js:512-528, los bytes nunca
# viajan). Ver docstring de booking_requests._orden_preview.

class TestOrdenPreview(unittest.TestCase):
    def setUp(self):
        abuse_guard._HITS.clear()
        ai_costs._WINDOW.clear()
        ai_cooldown._reset_para_tests()

    def _analizar(self, mime, filename, data_b64=_PNG_B64):
        body = {"servicio_id": "cardiologia", "especialidad": "Cardiología",
                "orden": {"filename": filename, "mime": mime, "data_b64": data_b64}}
        h = _CLIENT.request("POST", "/api/analyze-order", json=body)
        return h.json()["token"]

    def test_imagen_se_sirve_por_token(self):
        token = self._analizar("image/png", "orden.png")
        h = _CLIENT.request("GET", "/api/orden-preview/" + token)
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.headers.get("Content-Type"), "image/png")
        self.assertEqual(h.content, base64.b64decode(_PNG_B64))

    def test_pdf_no_se_sirve_como_imagen_404(self):
        # Confirmá renderiza un PDF como chip de documento, nunca un <img> —
        # este endpoint sólo sirve imágenes, un 404 es la señal para degradar.
        token = self._analizar("application/pdf", "orden.pdf",
                                data_b64=base64.b64encode(b"%PDF-1.4 fake").decode())
        h = _CLIENT.request("GET", "/api/orden-preview/" + token)
        self.assertEqual(h.status_code, 404)

    def test_token_desconocido_404(self):
        h = _CLIENT.request("GET", "/api/orden-preview/no-existe-este-token")
        self.assertEqual(h.status_code, 404)

    def test_token_con_slash_no_matchea_la_ruta(self):
        # un token con "/" no puede colarse a través de la regex de ruteo
        # (identidad-desde-el-token, nunca un path adivinado)
        h = _CLIENT.request("GET", "/api/orden-preview/../../etc/passwd")
        self.assertEqual(h.status_code, 404)


# ---- POST /api/requests: auth gate (sin base — corta antes de la DB) ----

class TestCreateAuth(unittest.TestCase):
    def setUp(self):
        self._orig = booking_requests._get_user

    def tearDown(self):
        booking_requests._get_user = self._orig

    def test_sin_sesion_401(self):
        booking_requests._get_user = lambda h: None
        h = _CLIENT.request("POST", "/api/requests", json={"servicio_id": "cardiologia", "centro_slug": "hospital"})
        self.assertEqual(h.status_code, 401)
        self.assertEqual(h.json()["error"], "auth_requerida")

    def test_dni_de_cuenta_invalido_400(self):
        booking_requests._get_user = lambda h: {"dni": "123", "nombre": "X", "apellido": "Y"}
        h = _CLIENT.request("POST", "/api/requests", json={"servicio_id": "cardiologia", "centro_slug": "hospital"})
        self.assertEqual(h.status_code, 400)
        self.assertIn("DNI", h.json()["error"])

    def test_servicio_inexistente_400(self):
        with patch.object(
            booking_requests.store, "attendance_summary_dni",
            return_value={"bloqueado_web": False},
        ):
            status, payload = booking_create.create_request(
                dict(_USER),
                {"servicio_id": "no-existe", "centro_slug": "hospital"},
            )
        self.assertEqual(status, 400)
        self.assertIn("servicio", payload["error"])

    def test_edad_fuera_de_rango_400(self):
        # _USER nació en 1979: un adulto no puede sacar un servicio pediátrico
        # (edad.max=15) para sí mismo. Gate server-side — el cliente ya lo
        # valida, esto cubre el POST directo.
        with patch.object(
            booking_requests.store, "attendance_summary_dni",
            return_value={"bloqueado_web": False},
        ):
            status, payload = booking_create.create_request(
                dict(_USER),
                {"servicio_id": "pediatria-eco", "centro_slug": "hospital"},
            )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "edad_fuera_de_rango")


class TestAutoConfirmar(unittest.TestCase):
    """Reunión hospital 2026-07-24: "con turno, sin orden y sin que valide el
    hospital" — un servicio modo `solicitud` con auto_confirmar queda aprobado
    al instante cuando el paciente eligió un horario concreto. Sin horario
    concreto (franja) sigue pendiente: alguien tiene que fijar la hora."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self._orig_user = booking_requests._get_user
        booking_requests._get_user = lambda h: dict(_USER)
        # el módulo entero ya corre con TURNOS_SKIP_HSI=1 (línea 28): guardar y
        # restaurar, NUNCA pop — borrarlo rompía los tests de más abajo que
        # asumen skip.
        _env.set_env(self, TURNOS_SKIP_HSI="1")

    def tearDown(self):
        booking_requests._get_user = self._orig_user

    def _crear(self, servicio_id, con_slot=True, hora="09:20"):
        body = {"servicio_id": servicio_id, "centro_slug": "hospital-pinamar"}
        if con_slot:
            body["slot_dia"] = (datetime.now(tz.tzinfo()).date() + timedelta(days=7)).isoformat()
            body["slot_hora"] = hora
        else:
            body["preferencia"] = "mañanas"
        h = _CLIENT.request("POST", "/api/requests", json=body)
        self.assertEqual(h.status_code, 200)
        return h

    def test_con_horario_concreto_queda_aprobada(self):
        h = self._crear("control-salud")
        self.assertEqual(h.json()["estado"], "aprobada")
        self.assertTrue(h.json().get("turno_dt"))
        rec = db_booking.get_booking_request(h.json()["id"])
        self.assertEqual(rec["estado"], "aprobada")
        self.assertEqual(rec["resuelta_por"], "auto:web")
        self.assertIsNotNone(rec["turno_dt"])

    def test_la_confirmacion_automatica_guarda_el_medico_del_horario_elegido(self):
        # La contraparte técnica, 2026-10-24: un turno de Cirugía sacado por la web salió en
        # «Sin médico asignado» aunque la paciente había elegido el horario de
        # un médico: la confirmación automática no lo guardaba.
        with patch.object(booking_create, "_auto_confirm_doctor",
                          return_value=("Roberto Prueba", None)) as elegido:
            h = self._crear("control-salud", hora="10:40")
        self.assertEqual(h.json()["estado"], "aprobada")
        elegido.assert_called_once()
        self.assertEqual(elegido.call_args.args[0], "control-salud")
        rec = db_booking.get_booking_request(h.json()["id"])
        self.assertEqual(rec["medico_asignado"], "Roberto Prueba")

    def test_el_medico_de_un_id_forjado_o_de_otro_servicio_no_se_guarda(self):
        servicio = {"id": "control-salud", "nombre": "Control de salud"}
        propio = {"id": "dr-a", "nombre": "Ana Propia"}
        with patch.object(booking_requests, "_service_full", return_value=servicio), \
                patch.object(booking_requests, "_is_lab", return_value=False), \
                patch("app.patient_web.catalog.doctor_by_id", side_effect=lambda i: propio if i == "dr-a" else None), \
                patch.object(booking_requests, "_doctor_pool",
                                  side_effect=lambda s, ms: [m for m in ms if m["id"] == "dr-a"]):
            self.assertEqual(booking_create._auto_confirm_doctor("control-salud", "dr-a"), ("Ana Propia", "dr-a"))
            self.assertEqual(booking_create._auto_confirm_doctor("control-salud", "inventado"), (None, None))
            self.assertEqual(booking_create._auto_confirm_doctor("control-salud", None), (None, None))
        with patch.object(booking_requests, "_service_full", return_value=servicio), \
                patch.object(booking_requests, "_is_lab", return_value=False), \
                patch("app.patient_web.catalog.doctor_by_id", return_value={"id": "dr-b", "nombre": "Otra Especialidad"}), \
                patch.object(booking_requests, "_doctor_pool", return_value=[]):
            self.assertEqual(booking_create._auto_confirm_doctor("control-salud", "dr-b"), (None, None))
        with patch.object(booking_requests, "_service_full", return_value=servicio), \
                patch.object(booking_requests, "_is_lab", return_value=True):
            self.assertEqual(booking_create._auto_confirm_doctor("control-salud", "dr-a"), (None, None))

    def test_sin_horario_concreto_queda_pendiente(self):
        h = self._crear("control-salud", con_slot=False)
        self.assertEqual(h.json()["estado"], "pendiente")

    def test_servicio_sin_flag_no_se_autoaprueba(self):
        h = self._crear("cardiologia")
        self.assertEqual(h.json()["estado"], "pendiente")


# ---- lifecycle con base ----------------------------------------------------

@unittest.skipUnless(True, "")
class TestLifecycle(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")

    def test_crear_listar_aprobar(self):
        sid = db_booking.create_booking_request("13452513", "Mónica", "Alustiza", "1959-11-09",
                                 "351-555-0000", "cardiologia", "hospital-pinamar",
                                 "image/png")
        self.assertIsInstance(sid, int)

        rec = db_booking.get_booking_request(sid)
        self.assertEqual(rec["estado"], "pendiente")
        self.assertEqual(rec["dni"], "13452513")
        self.assertEqual(rec["nacimiento"].isoformat(), "1959-11-09")

        db_booking.save_ai(sid, {"verdict": "parece_correcta", "resumen": "ok"})
        rec = db_booking.get_booking_request(sid)
        self.assertEqual(rec["ai_json"]["verdict"], "parece_correcta")

        por_dni = db_booking.list_by_dni("13452513")
        self.assertTrue(any(r["id"] == sid for r in por_dni))

        pend = db_booking.list_admin("pendiente")
        self.assertTrue(any(r["id"] == sid for r in pend))

        upd = db_booking.approve(sid, "2026-07-15T09:00", {"estado": "skip"})
        self.assertEqual(upd["estado"], "aprobada")
        self.assertEqual(upd["hsi_estado"], "skip")
        self.assertIsNotNone(upd["turno_dt"])

    def test_rechazar(self):
        sid = db_booking.create_booking_request("30111222", "Juan", "Pérez", "1985-03-20",
                                 None, "dermatologia", "riverside-clinic", None)
        upd = db_booking.reject(sid, "La orden no corresponde a dermatología")
        self.assertEqual(upd["estado"], "rechazada")
        self.assertIn("dermatología", upd["motivo_rechazo"])


# ---- routes end-to-end con base -------------------------------------------

class TestRoutesWithDb(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        abuse_guard._HITS.clear()
        ai_costs._WINDOW.clear()
        ai_cooldown._reset_para_tests()
        self._orig_user = booking_requests._get_user
        booking_requests._get_user = lambda h: dict(_USER)

    def tearDown(self):
        booking_requests._get_user = self._orig_user

    def _tok(self):
        # sesión admin válida: cookie firmada (login real vive en admin_auth)
        from app.auth import admin_auth
        return {"Cookie": "st_admin=" + admin_auth._mint_token(admin_auth._admin_user())}

    def test_create_inline_legacy_usa_identidad_de_sesion(self):
        # el body NO trae identidad: sale de la sesión (_USER)
        body = {"servicio_id": "cardiologia", "centro_slug": "hospital-pinamar",
                "contacto": "wa:11-5555",
                "orden": {"filename": "orden.png", "mime": "image/png",
                          "data_b64": _PNG_B64}}
        h = _CLIENT.request("POST", "/api/requests", json=body)
        self.assertEqual(h.status_code, 200)
        self.assertTrue(h.json()["ok"])
        self.assertEqual(h.json()["estado"], "pendiente")
        self.assertEqual(h.json()["ai"]["verdict"], "parece_correcta")
        self.assertEqual(h.json()["ai"]["pases"], 1)
        sid = h.json()["id"]

        rec = db_booking.get_booking_request(sid)
        self.assertEqual(rec["dni"], _USER["dni"])       # identidad de la sesión
        self.assertEqual(rec["sexo"], "F")
        self.assertEqual(rec["nacimiento"].isoformat(), "1979-06-01")
        self.assertTrue(rec["orden_path"] and os.path.exists(rec["orden_path"]))

        # el paciente ve su solicitud (dni de la sesión, sin ?dni)
        hp = _CLIENT.request("GET", "/api/requests")
        self.assertEqual(hp.status_code, 200)
        self.assertTrue(any(s["id"] == sid for s in hp.json()["solicitudes"]))
        self.assertNotIn("orden_path", hp.json()["solicitudes"][0])
        self.assertNotIn("ai_json", hp.json()["solicitudes"][0])

    def test_create_para_hijo_usa_identidad_del_dependiente(self):
        _dbtest.ensure_user(_USER["dni"])
        db_slots.save_dependent(_USER["dni"], "50123456", "Mateo", "Gómez",
                               "2019-03-12", "M", "hijo")
        h = _CLIENT.request("POST", "/api/requests", json={"servicio_id": "cardiologia", "centro_slug": "hospital",
                              "paciente_dni": "50.123.456"})
        self.assertEqual(h.status_code, 200, h.json())
        rec = db_booking.get_booking_request(h.json()["id"])
        self.assertEqual(rec["dni"], "50123456")
        self.assertEqual(rec["nombre"], "Mateo")
        self.assertEqual(rec["sexo"], "M")
        self.assertEqual(rec["solicitante_dni"], _USER["dni"])
        hp = _CLIENT.request("GET", "/api/requests")
        child = next(x for x in hp.json()["solicitudes"] if x["id"] == h.json()["id"])
        self.assertEqual(child["paciente_nombre"], "Mateo Gómez")

    def test_create_para_dni_no_vinculado_403(self):
        h = _CLIENT.request("POST", "/api/requests", json={"servicio_id": "cardiologia", "centro_slug": "hospital",
                              "paciente_dni": "50999999"})
        self.assertEqual(h.status_code, 403)
        self.assertEqual(h.json()["error"], "paciente_no_vinculado")

    def test_edad_dentro_de_rango_para_hijo_201(self):
        # el mismo servicio pediátrico SÍ vale para un hijo dentro del rango.
        _dbtest.ensure_user(_USER["dni"])
        db_slots.save_dependent(_USER["dni"], "50444555", "Sol", "Ríos", "2015-01-01", "F", "hijo")
        h = _CLIENT.request("POST", "/api/requests", json={"servicio_id": "pediatria-eco", "centro_slug": "hospital",
                              "paciente_dni": "50444555"})
        self.assertEqual(h.status_code, 200, h.json())

    # ---- GET /api/requests/<id> — detalle de UNA solicitud propia --------

    def _crear(self, **over):
        body = {"servicio_id": "cardiologia", "centro_slug": "hospital"}
        body.update(over)
        h = _CLIENT.request("POST", "/api/requests", json=body)
        self.assertEqual(h.status_code, 200, h.json())
        return h.json()["id"]

    def test_detalle_lo_ve_su_dueno(self):
        sid = self._crear()
        h = _CLIENT.request("GET", "/api/requests/%d" % sid)
        self.assertEqual(h.status_code, 200, h.json())
        s = h.json()["solicitud"]
        self.assertEqual(s["id"], sid)
        self.assertEqual(s["estado"], "pendiente")
        self.assertEqual(s["paciente_nombre"], "Ana López")
        self.assertIn("medico_asignado", s)   # el detalle sí lo trae

    def test_detalle_no_filtra_datos_privados(self):
        sid = self._crear()
        h = _CLIENT.request("GET", "/api/requests/%d" % sid)
        s = h.json()["solicitud"]
        for prohibida in ("orden_path", "orden_mime", "ai_json", "contacto",
                          "hsi_patient_id", "hsi_appointment_id", "hsi_estado",
                          "nacimiento", "sexo", "solicitante_dni"):
            self.assertNotIn(prohibida, s)

    def test_detalle_del_turno_del_hijo_lo_ve_el_padre(self):
        _dbtest.ensure_user(_USER["dni"])
        db_slots.save_dependent(_USER["dni"], "50777888", "Tomás", "López",
                               "2016-05-05", "M", "hijo")
        sid = self._crear(paciente_dni="50777888")
        h = _CLIENT.request("GET", "/api/requests/%d" % sid)
        self.assertEqual(h.status_code, 200, h.json())
        self.assertEqual(h.json()["solicitud"]["paciente_nombre"], "Tomás López")

    def test_detalle_ajeno_404(self):
        sid = self._crear()
        otro = dict(_USER, dni="30111222")
        orig = booking_requests._get_user
        booking_requests._get_user = lambda h: dict(otro)
        try:
            h = _CLIENT.request("GET", "/api/requests/%d" % sid)
            self.assertEqual(h.status_code, 404)      # 404, no 403: no confirmamos que exista
        finally:
            booking_requests._get_user = orig

    def test_detalle_sin_sesion_401(self):
        orig = booking_requests._get_user
        booking_requests._get_user = lambda h: None
        try:
            h = _CLIENT.request("GET", "/api/requests/1")
            self.assertEqual(h.status_code, 401)
            self.assertEqual(h.json()["error"], "auth_requerida")
        finally:
            booking_requests._get_user = orig

    def test_public_row_claves_exactas(self):
        sid = db_booking.create_booking_request("41000111", "Rita", "Vega", "1988-01-01",
                                 "wa:11", "cardiologia", "hospital", None)
        row = db_booking.get_booking_request(sid)
        pub = booking_requests._public_row(row)
        self.assertEqual(set(pub.keys()), {
            "id", "servicio_id", "centro_slug", "estado", "turno_dt",
            "preferencia_horario", "motivo_rechazo", "created",
            "medico_asignado", "medico_asignado_id", "paciente_dni",
            "paciente_nombre", "preparacion", "hold", "turno_pasado", "turno_pedido",
        })

    def test_create_con_orden_token(self):
        # 1) analizar la orden (sin sesión) -> token + ai guardado server-side
        ha = _CLIENT.request("POST", "/api/analyze-order", json={"servicio_id": "cardiologia", "especialidad": "Cardiología",
                               "orden": {"filename": "orden.png", "mime": "image/png",
                                         "data_b64": _PNG_B64}})
        token = ha.json()["token"]
        self.assertTrue(token)

        # 2) registrar consumiendo el token (con sesión)
        h = _CLIENT.request("POST", "/api/requests", json={"servicio_id": "cardiologia", "centro_slug": "hospital",
                              "orden_token": token})
        self.assertEqual(h.status_code, 200)
        sid = h.json()["id"]

        rec = db_booking.get_booking_request(sid)
        self.assertTrue(rec["orden_path"] and os.path.exists(rec["orden_path"]))
        self.assertEqual(rec["ai_json"]["verdict"], "parece_correcta")   # ai_json adjunto
        self.assertEqual(rec["ai_json"]["pases"], 1)
        # el pending .json ya no existe (se consumió)
        self.assertIsNone(booking_referrals._load_pending(token))

    def test_get_session_first_ignora_query_dni(self):
        db_booking.create_booking_request(_USER["dni"], "Ana", "López", "1979-06-01", None,
                           "cardiologia", "hospital", None)
        # con sesión, el ?dni ajeno se ignora: devuelve las del usuario logueado
        h = _CLIENT.request("GET", "/api/requests?dni=99999999")
        self.assertEqual(h.status_code, 200)
        self.assertTrue(any(s["servicio_id"] == "cardiologia" for s in h.json()["solicitudes"]))

    def test_get_sin_sesion_no_filtra_por_query_dni(self):
        # IDOR fix: sin sesión, ?dni= NO se honra — antes cualquiera listaba las
        # solicitudes (PHI) de cualquier DNI sin autenticarse. Ahora 401.
        db_booking.create_booking_request(_USER["dni"], "Ana", "López", "1979-06-01", None,
                           "cardiologia", "hospital", None)
        orig = booking_requests._get_user
        booking_requests._get_user = lambda h: None
        try:
            h = _CLIENT.request("GET", "/api/requests?dni=" + _USER["dni"])
            self.assertEqual(h.status_code, 401)
            self.assertNotIn("solicitudes", h.json())
        finally:
            booking_requests._get_user = orig

    def test_admin_list_ok(self):
        ha = _CLIENT.request("GET", "/api/admin/requests?status=pendiente", headers=self._tok())
        self.assertEqual(ha.status_code, 200)
        self.assertIn("solicitudes", ha.json())

    def test_aprobar_route_skip_hsi(self):
        sid = db_booking.create_booking_request("29123456", "Luis", "Díaz", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve", json={"turno_dt": "2026-07-20T10:30"}, headers=self._tok())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["solicitud"]["estado"], "aprobada")
        self.assertEqual(h.json()["solicitud"]["hsi_estado"], "skip")

    def test_aprobar_guarda_hora_confirmada_por_hsi(self):
        """HSI may snap the request to its next real free boundary. The
        patient-facing row must use that confirmed time, never the input."""
        from app.connectors.hsi import hsi_client
        sid = db_booking.create_booking_request("29123457", "Luz", "Díaz", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        old_push = hsi_client.push_turno
        _env.set_env(self, TURNOS_SKIP_HSI="0", CONNECTOR_EMR_SYNC="hsi")
        hsi_client.push_turno = lambda *a, **kw: {
            "estado": "ok", "patient_id": 12, "appointment_id": 34,
            "actual_turno_dt": "2099-07-21T11:00:00-03:00",
        }
        try:
            h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve", json={"turno_dt": "2099-07-20T10:30-03:00"}, headers=self._tok())
            self.assertEqual(h.status_code, 200, h.json())
            row = db_booking.get_booking_request(sid)
            self.assertEqual(row["hsi_estado"], "ok")
            self.assertEqual(str(row["hsi_appointment_id"]), "34")
            self.assertEqual(row["turno_dt"].astimezone(booking_requests._ART).isoformat()[:16],
                             "2099-07-21T11:00")
        finally:
            hsi_client.push_turno = old_push

    def test_aprobar_persiste_y_confirma_la_intencion_antes_del_push_hsi(self):
        """The initial HSI create is journaled and leased before network I/O."""
        from app.connectors.hsi import hsi_client
        sid = db_booking.create_booking_request("29123477", "Outbox", "Confirmada", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        old_push = hsi_client.push_turno
        _env.set_env(self, TURNOS_SKIP_HSI="0", CONNECTOR_EMR_SYNC="hsi")
        hsi_client.push_turno = lambda *a, **kw: {
            "estado": "ok", "patient_id": 88, "appointment_id": 89,
            "actual_turno_dt": "2099-07-21T11:00:00-03:00",
        }
        try:
            h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve", json={"turno_dt": "2099-07-20T10:30-03:00"}, headers=self._tok())
            self.assertEqual(h.status_code, 200, h.json())
            with db._connect() as conn, conn.cursor() as cur:
                cur.execute("""SELECT state,attempts,lease_expires_at,hsi_result,payload
                               FROM hsi_outbox WHERE entity_id=%s AND operation='create'""", (str(sid),))
                intents = cur.fetchall()
            self.assertEqual(len(intents), 1)
            intent = intents[0]
            self.assertEqual(intent["state"], "confirmed")
            self.assertEqual(intent["attempts"], 1)
            self.assertIsNone(intent["lease_expires_at"])
            self.assertEqual(intent["hsi_result"]["appointment_id"], "89")
            self.assertEqual(intent["payload"]["sobreturno"], False)
        finally:
            hsi_client.push_turno = old_push

    def test_aprobar_con_hsi_caido_deja_intencion_durable_en_error(self):
        """An outage leaves the approved local decision and replayable journal row."""
        from app.connectors.hsi import hsi_client
        sid = db_booking.create_booking_request("29123478", "Outbox", "Pendiente", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        old_push = hsi_client.push_turno
        _env.set_env(self, TURNOS_SKIP_HSI="0", CONNECTOR_EMR_SYNC="hsi")
        def offline(*_args, **_kwargs):
            raise ConnectionError("HSI offline")
        hsi_client.push_turno = offline
        try:
            h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve", json={"turno_dt": "2099-07-20T10:30-03:00"}, headers=self._tok())
            self.assertEqual(h.status_code, 200, h.json())
            self.assertEqual(db_booking.get_booking_request(sid)["hsi_estado"], "pendiente")
            with db._connect() as conn, conn.cursor() as cur:
                cur.execute("""SELECT state,attempts,last_error,payload
                               FROM hsi_outbox WHERE entity_id=%s AND operation='create'""", (str(sid),))
                intents = cur.fetchall()
            self.assertEqual(len(intents), 1)
            self.assertEqual(intents[0]["state"], "error")
            self.assertEqual(intents[0]["attempts"], 1)
            self.assertIn("HSI offline", intents[0]["last_error"])
            self.assertEqual(intents[0]["payload"]["sobreturno"], False)
        finally:
            hsi_client.push_turno = old_push

    def test_reintentar_hsi_repara_aprobada_y_guarda_hora_confirmada(self):
        from app.connectors.hsi import hsi_client
        sid = db_booking.create_booking_request("29123467", "Luz", "Retry", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        original = booking_requests._slot_dt_art("2099-07-20", "10:30")
        db_booking.approve(sid, original, {"estado": "error"})
        old_push = hsi_client.push_turno
        _env.set_env(self, TURNOS_SKIP_HSI="0", CONNECTOR_EMR_SYNC="hsi")
        hsi_client.push_turno = lambda *a, **kw: {
            "estado": "ok", "patient_id": 22, "appointment_id": 44,
            "actual_turno_dt": "2099-07-20T11:00:00-03:00",
        }
        try:
            h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/retry-hsi", headers=self._tok())
            self.assertEqual(h.status_code, 200, h.json())
            row = db_booking.get_booking_request(sid)
            self.assertEqual(row["hsi_estado"], "ok")
            self.assertEqual(str(row["hsi_appointment_id"]), "44")
            self.assertEqual(row["turno_dt"].astimezone(booking_requests._ART).isoformat()[:16],
                             "2099-07-20T11:00")
        finally:
            hsi_client.push_turno = old_push

    def test_reintentar_hsi_rearma_un_intento_en_conflicto(self):
        """El botón tiene que funcionar aunque el intento durable quedó 'conflict'.

        Antes: enqueue devolvía la fila conflictuada existente, claim la
        rechazaba (sólo toma pending/error vencidos) y el botón "no hacía
        nada". hsi_outbox_reset la rearma y el push sale ahora."""
        from app.connectors.hsi import hsi_client
        from app.modules.turnos import hsi_journal as hsi_sync
        sid = db_booking.create_booking_request("29123471", "Rea", "Conflicto", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        original = booking_requests._slot_dt_art("2099-07-20", "10:30")
        db_booking.approve(sid, original, {"estado": "error"})
        old_push = hsi_client.push_turno
        _env.set_env(self, TURNOS_SKIP_HSI="0", CONNECTOR_EMR_SYNC="hsi")
        try:
            intent = hsi_sync.enqueue_appointment(db_booking.get_booking_request(sid))
            db_hsi.hsi_outbox_conflict(intent["id"], "no diary matches 'Cardiología'")
            hsi_client.push_turno = lambda *a, **kw: {
                "estado": "ok", "patient_id": 22, "appointment_id": 45,
                "actual_turno_dt": "2099-07-20T10:30:00-03:00",
            }
            h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/retry-hsi", headers=self._tok())
            self.assertEqual(h.status_code, 200, h.json())
            self.assertEqual(db_booking.get_booking_request(sid)["hsi_estado"], "ok")
            with db._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT state FROM hsi_outbox WHERE id=%s", (intent["id"],))
                self.assertEqual(cur.fetchone()["state"], "confirmed")
        finally:
            hsi_client.push_turno = old_push

    def test_reintentar_hsi_informa_la_causa_real(self):
        """502 con `detalle` clasificado, y el conflicto queda visible como
        hsi_estado='error' en la solicitud (no sólo enterrado en la outbox)."""
        from app.connectors.hsi import hsi_client
        sid = db_booking.create_booking_request("29123472", "Cau", "Real", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        original = booking_requests._slot_dt_art("2099-07-20", "11:30")
        db_booking.approve(sid, original, {"estado": "pendiente"})
        old_push = hsi_client.push_turno
        _env.set_env(self, TURNOS_SKIP_HSI="0", CONNECTOR_EMR_SYNC="hsi")
        def sin_agenda(*_a, **_kw):
            raise hsi_client.HsiError("no diary matches 'Cardiología'; available specialties: Pediatría")
        hsi_client.push_turno = sin_agenda
        try:
            h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/retry-hsi", headers=self._tok())
            self.assertEqual(h.status_code, 502, h.json())
            self.assertEqual(h.json()["error"], "hsi_no_confirmo")
            self.assertIn("agenda", h.json()["detalle"])
            self.assertEqual(db_booking.get_booking_request(sid)["hsi_estado"], "error")
        finally:
            hsi_client.push_turno = old_push

    def test_reintentar_hsi_en_modo_skip_responde_hsi_desactivado(self):
        """En cloud (TURNOS_SKIP_HSI=1) el reintento no puede fingir que va a
        funcionar: 409 declarativo en vez de un push imposible."""
        _env.set_env(self, CONNECTOR_EMR_SYNC="hsi")
        sid = db_booking.create_booking_request("29123473", "Sin", "Hsi", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        db_booking.approve(sid, booking_requests._slot_dt_art("2099-07-20", "12:00"), {"estado": "skip"})
        h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/retry-hsi", headers=self._tok())  # TURNOS_SKIP_HSI=1 (default de tests)
        self.assertEqual(h.status_code, 409, h.json())
        self.assertEqual(h.json()["error"], "hsi_desactivado")

    def test_reintentar_hsi_rechaza_turno_pasado(self):
        sid = db_booking.create_booking_request("29123474", "Tur", "Pasado", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        db_booking.approve(sid, booking_requests._slot_dt_art("2020-07-20", "10:30"), {"estado": "error"})
        _env.set_env(self, TURNOS_SKIP_HSI="0", CONNECTOR_EMR_SYNC="hsi")
        h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/retry-hsi", headers=self._tok())
        self.assertEqual(h.status_code, 400, h.json())
        self.assertEqual(h.json()["error"], "turno_pasado")

    def test_reintentar_hsi_envia_una_fila_omitida_cuando_hsi_esta_activo(self):
        """Una aprobación 'skip' (hecha en cloud) se puede impactar después,
        cuando el mismo dato corre contra un HSI real."""
        from app.connectors.hsi import hsi_client
        sid = db_booking.create_booking_request("29123475", "Omi", "Tida", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        db_booking.approve(sid, booking_requests._slot_dt_art("2099-07-20", "13:00"), {"estado": "skip"})
        old_push = hsi_client.push_turno
        _env.set_env(self, TURNOS_SKIP_HSI="0", CONNECTOR_EMR_SYNC="hsi")
        hsi_client.push_turno = lambda *a, **kw: {
            "estado": "ok", "patient_id": 23, "appointment_id": 46,
            "actual_turno_dt": "2099-07-20T13:00:00-03:00",
        }
        try:
            h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/retry-hsi", headers=self._tok())
            self.assertEqual(h.status_code, 200, h.json())
            self.assertEqual(db_booking.get_booking_request(sid)["hsi_estado"], "ok")
        finally:
            hsi_client.push_turno = old_push

    def test_admin_list_declara_si_hsi_esta_activo(self):
        h = _CLIENT.request("GET", "/api/admin/requests", headers=self._tok())
        self.assertEqual(h.status_code, 200)
        self.assertIs(h.json().get("emr_sync_active"), False)  # explicit connector is off in tests

    def test_admin_list_lleva_la_extension_hsi_sin_envio_del_servidor(self):
        # 2026-10-27, d1122568: without server push Solicitudes draws the
        # extension's «Cargar en HSI» — only where the extension runs, so the
        # list says whether it does (its store link) or not (None).
        from app.core import hsi_extension
        h = _CLIENT.request("GET", "/api/admin/requests", headers=self._tok())
        self.assertEqual(h.status_code, 200)
        self.assertIn("hsi_extension", h.json())
        self.assertEqual(h.json()["hsi_extension"], hsi_extension.store_url())
        # The reference-deployment counterpart, 2026-10-27: and where to install it (store or ZIP, the guide).
        self.assertEqual(h.json()["hsi_instalar"], hsi_extension.install())

    def test_aprobar_bloquea_mismo_medico_misma_hora(self):
        primero = db_booking.create_booking_request("29123458", "Paz", "Díaz", "1982-02-02",
                                    None, "cardiologia", "hospital", None)
        segundo = db_booking.create_booking_request("29123459", "Sol", "Díaz", "1982-02-02",
                                     None, "cardiologia", "hospital", None)
        when = "2099-07-20T10:30-03:00"
        agenda_doctor = [{
            "id": "dra-prueba", "nombre": "Dra. Prueba",
            "agenda": {"franjas": [{"dias": [1], "desde": "10:00", "hasta": "11:00", "cada_min": 30,
                                    "servicio": "cardiologia"}],
                       "excepciones": []},
        }]
        with patch("app.patient_web.catalog.doctors", return_value=agenda_doctor):
            h1 = _CLIENT.request("POST", f"/api/admin/requests/{primero}/approve", json={"turno_dt": when, "medico": "Dra. Prueba"}, headers=self._tok())
            self.assertEqual(h1.status_code, 200, h1.json())
            h2 = _CLIENT.request("POST", f"/api/admin/requests/{segundo}/approve", json={"turno_dt": when, "medico": "Dra. Prueba"}, headers=self._tok())
            self.assertEqual(h2.status_code, 409, h2.json())
            self.assertEqual(h2.json()["error"], "turno_medico_tomado")
            self.assertEqual(db_booking.get_booking_request(segundo)["estado"], "pendiente")

    def test_named_doctor_requires_an_art_agenda_boundary(self):
        sid = db_booking.create_booking_request("29123462", "Luz", "Horario", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        agenda_doctor = [{
            "id": "dra-horario", "nombre": "Dra. Horario",
            "agenda": {"franjas": [{"dias": [1], "desde": "10:00", "hasta": "11:00", "cada_min": 30,
                                    "servicio": "cardiologia"}],
                       "excepciones": []},
        }]
        with patch("app.patient_web.catalog.doctors", return_value=agenda_doctor):
            # 10:45 ART is inside work hours but not an actual 30-minute slot.
            h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve", json={"turno_dt": "2099-07-20T10:45:00-03:00", "medico": "Dra. Horario"}, headers=self._tok())
            self.assertEqual(h.status_code, 409, h.json())
            self.assertEqual(h.json()["error"], "turno_medico_fuera_de_agenda")
            self.assertEqual(db_booking.get_booking_request(sid)["estado"], "pendiente")

            # The same schedule accepts an equivalent UTC instant at 10:30 ART.
            franja, motivo = booking_capacity._doctor_appointment_in_schedule(
                "Dra. Horario", datetime.fromisoformat("2099-07-20T13:30:00+00:00"),
                {"centro_slug": "hospital", "servicio_id": "cardiologia"})
            self.assertIsNotNone(franja)
            self.assertIsNone(motivo)

    # Mapa del hospital (desk.json d10, problems.md №2, 2026-10-23): la mesa
    # aprobaba una solicitud con un médico de OTRA especialidad (el panel lo
    # preseleccionaba y el servidor aceptaba cualquier nombre). La regla
    # médico ↔ servicio es una sola, schedule_policy.belongs — la del paciente.
    _MEDICOS_DOS_SERVICIOS = [
        {"id": "dra-corazon", "nombre": "Dra. Corazón", "especialidad": "Cardióloga",
         "agenda": {"franjas": [{"dias": [1], "desde": "10:00", "hasta": "11:00",
                                 "cada_min": 30, "servicio": "cardiologia"}],
                    "excepciones": []}},
        # Agenda sin etiquetar (la de su consultorio, abierta a esa hora): el
        # chequeo de horario solo no alcanzaba para frenarlo.
        {"id": "dr-piel", "nombre": "Dr. Piel", "especialidad": "Dermatólogo",
         "agenda": {"franjas": [{"dias": [1], "desde": "10:00", "hasta": "11:00",
                                 "cada_min": 30}],
                    "excepciones": []}},
    ]

    def test_aprobar_con_medico_de_otro_servicio_se_rechaza(self):
        sid = db_booking.create_booking_request("29123470", "Eco", "Cardio", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        with patch("app.patient_web.catalog.doctors", return_value=self._MEDICOS_DOS_SERVICIOS):
            h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve",
                                json={"turno_dt": "2099-07-20T10:30:00-03:00", "medico": "Dr. Piel"},
                                headers=self._tok())
        self.assertEqual(h.status_code, 409, h.json())
        self.assertEqual(h.json()["error"], "medico_no_atiende_servicio")
        self.assertEqual(db_booking.get_booking_request(sid)["estado"], "pendiente")

    def test_aprobar_con_un_nombre_fuera_del_catalogo_se_rechaza(self):
        sid = db_booking.create_booking_request("29123471", "Eco", "Libre", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        with patch("app.patient_web.catalog.doctors", return_value=self._MEDICOS_DOS_SERVICIOS):
            h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve",
                                json={"turno_dt": "2099-07-20T10:30:00-03:00", "medico": "Dr. Inventado"},
                                headers=self._tok())
        self.assertEqual(h.status_code, 409, h.json())
        self.assertEqual(h.json()["error"], "medico_no_atiende_servicio")

    def test_aprobar_con_medico_del_servicio_ok(self):
        sid = db_booking.create_booking_request("29123472", "Eco", "Bien", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        with patch("app.patient_web.catalog.doctors", return_value=self._MEDICOS_DOS_SERVICIOS):
            h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve",
                                json={"turno_dt": "2099-07-20T10:30:00-03:00", "medico": "Dra. Corazón"},
                                headers=self._tok())
        self.assertEqual(h.status_code, 200, h.json())
        self.assertEqual(db_booking.get_booking_request(sid)["medico_asignado"], "Dra. Corazón")

    def test_derivar_al_aprobar_verifica_el_medico_contra_el_servicio_nuevo(self):
        # El servicio que cuenta es el que se aprueba (derivado), no el pedido.
        sid = db_booking.create_booking_request("29123473", "Eco", "Deriva", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        with patch("app.patient_web.catalog.doctors", return_value=self._MEDICOS_DOS_SERVICIOS):
            h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve",
                                json={"turno_dt": "2099-07-20T10:30:00-03:00", "medico": "Dra. Corazón",
                                      "servicio_id": "dermatologia"},
                                headers=self._tok())
        self.assertEqual(h.status_code, 409, h.json())
        self.assertEqual(h.json()["error"], "medico_no_atiende_servicio")

    # «Derivar a» a service of another center: the appointment is where the service is.
    # Found by the end-to-end journey of the redirection (2026-10-27): the email said
    # «Dónde: Riverside Clinic» for a cardiology appointment at Sampleton Hospital.
    @staticmethod
    def _service_centers(servicio_id):
        return {"dermatologia": {"id": "dermatologia", "centro": "hospital-pinamar"}}.get(servicio_id, {})

    def test_redirecting_to_a_service_of_another_center_moves_the_request_there(self):
        sid = db_booking.create_booking_request("29123478", "Eco", "OtroCentro", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        with patch.object(booking_requests, "_service_full", side_effect=self._service_centers):
            h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve",
                                json={"turno_dt": "2099-07-20T10:30:00-03:00", "servicio_id": "dermatologia"},
                                headers=self._tok())
        self.assertEqual(h.status_code, 200, h.json())
        fila = db_booking.get_booking_request(sid)
        self.assertEqual(fila["servicio_id"], "dermatologia")
        self.assertEqual(fila["centro_slug"], "hospital-pinamar")
        self.assertEqual(h.json()["solicitud"]["centro_slug"], "hospital-pinamar")

    def test_a_redirected_approval_checks_the_doctors_agenda_at_the_new_center(self):
        # The doctor works only at the center of the service the request is redirected to: the agenda
        # is looked up at THAT center, not at the one the patient first asked for.
        sid = db_booking.create_booking_request("29123481", "Eco", "AgendaNueva", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        doctors = [{"id": "dra-piel", "nombre": "Dra. Piel", "especialidad": "Dermatóloga",
                    "centros": [{"centro": "hospital-pinamar", "excepciones": [],
                                 "franjas": [{"dias": [1], "desde": "10:00", "hasta": "11:00",
                                              "cada_min": 30, "servicio": "dermatologia"}]}]}]
        with patch.object(booking_requests, "_service_full", side_effect=self._service_centers), \
                patch("app.patient_web.catalog.doctors", return_value=doctors):
            h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve",
                                json={"turno_dt": "2099-07-20T10:30:00-03:00", "servicio_id": "dermatologia",
                                      "medico": "Dra. Piel"},
                                headers=self._tok())
        self.assertEqual(h.status_code, 200, h.json())
        fila = db_booking.get_booking_request(sid)
        self.assertEqual((fila["servicio_id"], fila["centro_slug"], fila["medico_asignado"]),
                         ("dermatologia", "hospital-pinamar", "Dra. Piel"))

    def test_approving_without_redirecting_keeps_the_center(self):
        sid = db_booking.create_booking_request("29123479", "Eco", "MismoCentro", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        with patch.object(booking_requests, "_service_full", side_effect=self._service_centers):
            h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve",
                                json={"turno_dt": "2099-07-20T10:30:00-03:00", "servicio_id": "cardiologia"},
                                headers=self._tok())
        self.assertEqual(h.status_code, 200, h.json())
        self.assertEqual(db_booking.get_booking_request(sid)["centro_slug"], "hospital")

    def test_redirecting_to_a_service_without_a_center_keeps_the_one_asked(self):
        sid = db_booking.create_booking_request("29123480", "Eco", "SinCentro", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        with patch.object(booking_requests, "_service_full", return_value={}):
            h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve",
                                json={"turno_dt": "2099-07-20T10:30:00-03:00", "servicio_id": "dermatologia"},
                                headers=self._tok())
        self.assertEqual(h.status_code, 200, h.json())
        fila = db_booking.get_booking_request(sid)
        self.assertEqual((fila["servicio_id"], fila["centro_slug"]), ("dermatologia", "hospital"))

    def test_aprobar_sin_medico_sigue_permitido(self):
        # Sin médico del servicio la mesa aprueba «a asignar», como siempre.
        sid = db_booking.create_booking_request("29123474", "Eco", "Asignar", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        with patch("app.patient_web.catalog.doctors", return_value=self._MEDICOS_DOS_SERVICIOS):
            h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve",
                                json={"turno_dt": "2099-07-20T10:30:00-03:00"}, headers=self._tok())
        self.assertEqual(h.status_code, 200, h.json())

    def test_aprobar_sin_medico_no_conserva_a_un_asignado_de_otro_servicio(self):
        # /assign no verifica contra el servicio (corre contra el servicio previo a
        # «Derivar a»): un asignado ajeno no puede sobrevivir a una aprobación
        # «a asignar», que antes lo dejaba intacto (COALESCE).
        sid = db_booking.create_booking_request("29123475", "Eco", "Ajeno", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        with patch("app.patient_web.catalog.doctors", return_value=self._MEDICOS_DOS_SERVICIOS):
            db_booking.assign_pending_booking_request(sid, "Dr. Piel", "dr-piel")
            h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve",
                                json={"turno_dt": "2099-08-03T10:30:00-03:00"}, headers=self._tok())
        self.assertEqual(h.status_code, 200, h.json())
        fila = db_booking.get_booking_request(sid)
        self.assertIsNone(fila["medico_asignado"])
        self.assertIsNone(fila["medico_asignado_id"])

    def test_aprobar_sin_medico_conserva_al_asignado_del_servicio(self):
        sid = db_booking.create_booking_request("29123476", "Eco", "Propio", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        with patch("app.patient_web.catalog.doctors", return_value=self._MEDICOS_DOS_SERVICIOS):
            db_booking.assign_pending_booking_request(sid, "Dra. Corazón", "dra-corazon")
            h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve",
                                json={"turno_dt": "2099-08-03T10:30:00-03:00"}, headers=self._tok())
        self.assertEqual(h.status_code, 200, h.json())
        self.assertEqual(db_booking.get_booking_request(sid)["medico_asignado"], "Dra. Corazón")

    def test_cola_entrega_el_pool_de_medicos_por_servicio(self):
        with patch("app.patient_web.catalog.doctors", return_value=self._MEDICOS_DOS_SERVICIOS):
            h = _CLIENT.request("GET", "/api/admin/requests", headers=self._tok())
        self.assertEqual(h.status_code, 200)
        pool = h.json()["medicos_por_servicio"]
        self.assertEqual(pool["cardiologia"], ["dra-corazon"])
        self.assertEqual(pool["dermatologia"], [])  # Dr. Piel no está etiquetado
        self.assertEqual(pool["pediatria-eco"], [])

    def test_the_queue_get_never_assigns_the_suggested_doctor(self):
        # Security review 2026-11, M-2 bypass: assigning makes the patient the doctor's, and a GET
        # (outside CSRF, before the access-log row) wrote it for every pending row it listed.
        sid = db_booking.create_booking_request("29123477", "Eco", "Sugerida", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        sugerido = {"nombre": "Dra. Corazón", "slot_iso": "2099-08-03T10:30:00-03:00", "pedido": False}
        with patch("app.patient_web.catalog.doctors", return_value=self._MEDICOS_DOS_SERVICIOS), \
                patch("app.modules.turnos.booking_queue._suggested_doctor", return_value=sugerido):
            h = _CLIENT.request("GET", "/api/admin/requests?status=pendiente", headers=self._tok())
        self.assertEqual(h.status_code, 200, h.json())
        row = next(x for x in h.json()["solicitudes"] if x["id"] == sid)
        self.assertEqual(row["medico_sugerido"]["nombre"], "Dra. Corazón")
        fila = db_booking.get_booking_request(sid)
        self.assertIsNone(fila["medico_asignado"])
        self.assertIsNone(fila["medico_asignado_id"])

    def test_named_doctor_closure_and_partial_block_override_recurrence(self):
        base = {"dias": [1], "desde": "10:00", "hasta": "11:00", "cada_min": 30}
        row = {"centro_slug": "hospital"}
        closed = [{"nombre": "Dra. Excepciones", "agenda": {
            "franjas": [base],
            "excepciones": [{"fecha": "2099-07-20", "tipo": "cerrado"}],
        }}]
        with patch("app.patient_web.catalog.doctors", return_value=closed):
            _, motivo = booking_capacity._doctor_appointment_in_schedule(
                "Dra. Excepciones", datetime.fromisoformat("2099-07-20T10:30:00-03:00"), row)
            self.assertEqual(motivo, "turno_medico_horario_cerrado")
            permitido, motivo = booking_capacity._overbooking_allowed(
                "Dra. Excepciones", datetime.fromisoformat("2099-07-20T10:30:00-03:00"), row)
            self.assertFalse(permitido)
            self.assertEqual(motivo, "sobreturno_horario_cerrado")

        blocked = [{"nombre": "Dra. Excepciones", "agenda": {
            "franjas": [base],
            "excepciones": [{"fecha": "2099-07-20", "tipo": "bloqueado",
                             "desde": "10:00", "hasta": "10:30"}],
        }}]
        with patch("app.patient_web.catalog.doctors", return_value=blocked):
            _, motivo = booking_capacity._doctor_appointment_in_schedule(
                "Dra. Excepciones", datetime.fromisoformat("2099-07-20T10:00:00-03:00"), row)
            self.assertEqual(motivo, "turno_medico_horario_bloqueado")
            franja, motivo = booking_capacity._doctor_appointment_in_schedule(
                "Dra. Excepciones", datetime.fromisoformat("2099-07-20T10:30:00-03:00"), row)
            self.assertIsNotNone(franja)
            self.assertIsNone(motivo)

    def test_dos_sobreturnos_explicitos_respetan_el_cupo_y_quedan_auditados(self):
        agenda_doctor = [{
            "id": "dra-sobreturno", "nombre": "Dra. Sobreturno",
            "agenda": {"franjas": [{
                "dias": [1], "desde": "10:00", "hasta": "11:00", "cada_min": 30,
                "cupos": {"web": 1, "reservado": 0, "sobreturnos": 2}, "servicio": "cardiologia",
            }], "excepciones": []},
        }]
        ids = [db_booking.create_booking_request(str(29123500 + i), name, "Prueba", "1982-02-02",
                                  None, "cardiologia", "hospital", None)
               for i, name in enumerate(("Uno", "Dos", "Tres", "Cuatro"))]
        when = "2099-07-20T10:30-03:00"  # Monday, inside the configured frame.
        with patch("app.patient_web.catalog.doctors", return_value=agenda_doctor):
            first = _CLIENT.request("POST", f"/api/admin/requests/{ids[0]}/approve", json={"turno_dt": when, "medico": "Dra. Sobreturno"}, headers=self._tok())
            self.assertEqual(first.status_code, 200, first.json())

            for sid in ids[1:3]:
                overbook = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve", json={"turno_dt": when, "medico": "Dra. Sobreturno", "sobreturno": True}, headers=self._tok())
                self.assertEqual(overbook.status_code, 200, overbook.json())
                self.assertTrue(overbook.json()["solicitud"]["sobreturno"])
                self.assertTrue(db_booking.get_booking_request(sid)["sobreturno"])

            fourth = _CLIENT.request("POST", f"/api/admin/requests/{ids[3]}/approve", json={"turno_dt": when, "medico": "Dra. Sobreturno", "sobreturno": True}, headers=self._tok())
            self.assertEqual(fourth.status_code, 409, fourth.json())
            self.assertEqual(fourth.json()["error"], "sobreturno_cupo_agotado")
            self.assertEqual(db_booking.get_booking_request(ids[3])["estado"], "pendiente")

    def test_dos_sobreturnos_porcentaje_comparten_el_cupo_entre_horarios(self):
        """50% of four regular slots means two extras across the whole band.

        Each extra still needs its own ordinary appointment at that minute;
        the shared budget is deliberately not reset per minute.
        """
        agenda_doctor = [{
            "id": "dra-porcentaje", "nombre": "Dra. Porcentaje",
            "agenda": {"franjas": [{
                "dias": [1], "desde": "10:00", "hasta": "12:00", "cada_min": 30,
                "cupos": {"web": 1, "reservado": 0,
                           "sobreturnos": 99, "overbooking_percent": 50},
                "servicio": "cardiologia",
            }], "excepciones": []},
        }]
        ids = [db_booking.create_booking_request(str(29123600 + i), "Pct", "Prueba", "1982-02-02",
                                         None, "cardiologia", "hospital", None)
               for i in range(6)]
        with patch("app.patient_web.catalog.doctors", return_value=agenda_doctor):
            for sid, when in zip(ids[:3], ("10:00", "10:30", "11:00")):
                approved = _CLIENT.request(
                    "POST", f"/api/admin/requests/{sid}/approve",
                    json={"turno_dt": f"2099-07-20T{when}:00-03:00", "medico": "Dra. Porcentaje"},
                    headers=self._tok())
                self.assertEqual(approved.status_code, 200, approved.json())
            for sid, when in zip(ids[3:5], ("10:00", "10:30")):
                approved = _CLIENT.request(
                    "POST", f"/api/admin/requests/{sid}/approve",
                    json={"turno_dt": f"2099-07-20T{when}:00-03:00", "medico": "Dra. Porcentaje",
                          "sobreturno": True}, headers=self._tok())
                self.assertEqual(approved.status_code, 200, approved.json())
            exhausted = _CLIENT.request(
                "POST", f"/api/admin/requests/{ids[5]}/approve",
                json={"turno_dt": "2099-07-20T11:00:00-03:00", "medico": "Dra. Porcentaje",
                      "sobreturno": True}, headers=self._tok())
            self.assertEqual(exhausted.status_code, 409, exhausted.json())
            self.assertEqual(exhausted.json()["error"], "sobreturno_cupo_agotado")
            self.assertEqual(db_booking.get_booking_request(ids[5])["estado"], "pendiente")

    def test_porcentaje_superpuesto_falla_cerrado_incluso_con_legacy_primero(self):
        legacy = {"dias": [1], "desde": "10:00", "hasta": "12:00", "cada_min": 30,
                  "cupos": {"sobreturnos": 3}}
        percentage = {**legacy, "desde": "11:00", "hasta": "13:00",
                      "cupos": {"overbooking_percent": 20}}
        for bands in ([legacy, percentage], [percentage, legacy]):
            doctor = [{"id": "overlap", "nombre": "Dra. Overlap",
                       "agenda": {"franjas": bands, "excepciones": []}}]
            with patch("app.patient_web.catalog.doctors", return_value=doctor):
                remaining, reason = booking_capacity.overbooking_capacity(
                    "Dra. Overlap", datetime.fromisoformat("2099-07-20T11:00:00-03:00"),
                    {"centro_slug": "hospital"}, 1)
                self.assertEqual(remaining, 0)
                self.assertEqual(reason, "sobreturno_no_configurado")

    def test_porcentaje_cero_desactiva_aun_con_legacy_presente(self):
        doctor = [{"id": "dra-cero", "nombre": "Dra. Cero", "agenda": {"franjas": [{
            "dias": [1], "desde": "10:00", "hasta": "11:00", "cada_min": 30,
            "cupos": {"sobreturnos": 99, "overbooking_percent": 0}, "servicio": "cardiologia",
        }], "excepciones": []}}]
        ordinary = db_booking.create_booking_request("29123620", "Zero", "One", "1982-02-02", None, "cardiologia", "hospital", None)
        extra = db_booking.create_booking_request("29123621", "Zero", "Two", "1982-02-02", None, "cardiologia", "hospital", None)
        with patch("app.patient_web.catalog.doctors", return_value=doctor):
            first = _CLIENT.request("POST", f"/api/admin/requests/{ordinary}/approve",
                                    json={"turno_dt": "2099-07-20T10:00:00-03:00", "medico": "Dra. Cero"}, headers=self._tok())
            self.assertEqual(first.status_code, 200, first.json())
            denied = _CLIENT.request("POST", f"/api/admin/requests/{extra}/approve",
                                     json={"turno_dt": "2099-07-20T10:00:00-03:00", "medico": "Dra. Cero", "sobreturno": True}, headers=self._tok())
            self.assertEqual(denied.status_code, 409, denied.json())
            self.assertEqual(denied.json()["error"], "sobreturno_no_configurado")

    def test_persistir_sobreturno_cero_falla_cerrado_sin_route_precheck(self):
        doctor = [{"id": "dra-cero-core", "nombre": "Dra. Cero Core", "agenda": {"franjas": [{
            "dias": [1], "desde": "10:00", "hasta": "11:00", "cada_min": 30,
            "cupos": {"overbooking_percent": 0},
        }], "excepciones": []}}]
        sid = db_booking.create_booking_request("29123622", "Zero", "Core", "1982-02-02", None, "cardiologia", "hospital", None)
        with patch("app.patient_web.catalog.doctors", return_value=doctor):
            with self.assertRaises(booking_decisions.OverbookingPolicyDenied):
                booking_decisions._persist_approval(
                    sid, booking_requests._slot_dt_art("2099-07-20", "10:00"),
                    medico="Dra. Cero Core", sobreturno=True)
        self.assertEqual(db_booking.get_booking_request(sid)["estado"], "pendiente")

    def test_sobreturno_sin_turno_previo_falla_cerrado(self):
        sid = db_booking.create_booking_request("29123509", "Solo", "Prueba", "1982-02-02",
                                 None, "cardiologia", "hospital", None)
        # Médica del servicio (si no, la aprobación la frena antes por otra
        # especialidad, 2026-10-23) y sin ningún turno previo a esa hora.
        doctor = [{"id": "dra-sin-previo", "nombre": "Dra. Sin Previo", "agenda": {"franjas": [{
            "dias": [1], "desde": "10:00", "hasta": "11:00", "cada_min": 30,
            "servicio": "cardiologia"}], "excepciones": []}}]
        with patch("app.patient_web.catalog.doctors", return_value=doctor):
            h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve", json={"turno_dt": "2099-07-20T10:30-03:00",
                                  "medico": "Dra. Sin Previo", "sobreturno": True}, headers=self._tok())
        self.assertEqual(h.status_code, 409, h.json())
        self.assertEqual(h.json()["error"], "sobreturno_requiere_turno_previo")

    def test_rechazar_route(self):
        sid = db_booking.create_booking_request("31999888", "Sara", "Ruiz", "1990-10-10",
                                 None, "dermatologia", "caps", None)
        h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/reject", json={"motivo": "Falta la firma del médico"}, headers=self._tok())
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["solicitud"]["estado"], "rechazada")

    def test_aprobar_sin_turno_400(self):
        sid = db_booking.create_booking_request("32000111", "Eva", "Sosa", "1995-05-05",
                                 None, "cardiologia", "hospital", None)
        h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve", json={}, headers=self._tok())
        self.assertEqual(h.status_code, 400)

    # ---- POST /api/admin/requests/<id>/campos — edición inline (2026-07-17) --

    def _tok_rol(self, rol, usuario="tester"):
        from app.auth import admin_auth
        return {"Cookie": "st_admin=" + admin_auth._mint_token(usuario, rol)}

    def test_editar_campo_operador_ok_y_recalcula_coincide(self):
        sid = db_booking.create_booking_request("40111222", "Pia", "Cruz", "1990-01-01",
                                 None, "cardiologia", "hospital", None)
        db_booking.save_ai(sid, {"campos": {"dni": {"valor": "40999999", "coincide": False}}})
        h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/campos", json={"campo": "dni", "valor": "40111222"}, headers=self._tok_rol("admisionista"))
        self.assertEqual(h.status_code, 200, h.json())
        self.assertTrue(h.json()["campos"]["dni"]["editado"])
        self.assertTrue(h.json()["campos"]["dni"]["coincide"])   # ahora coincide con la cuenta
        rec = db_booking.get_booking_request(sid)
        self.assertEqual(rec["ai_json"]["campos"]["dni"]["valor"], "40111222")

    def test_editar_campo_paciente_recalcula_coincide(self):
        sid = db_booking.create_booking_request("40123123", "Rocío", "Fabra", "1990-01-01",
                                 None, "cardiologia", "hospital", None)
        db_booking.save_ai(sid, {"campos": {"paciente": {"valor": "Otra Persona", "coincide": False}}})
        h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/campos", json={"campo": "paciente", "valor": "Rocío Fabra"}, headers=self._tok())
        self.assertEqual(h.status_code, 200, h.json())
        self.assertTrue(h.json()["campos"]["paciente"]["coincide"])

    # ---- POST /api/admin/requests/<id>/patient-data — lo que HSI necesita ----

    def test_la_mesa_completa_los_datos_del_paciente_y_la_cola_los_muestra(self):
        sid = db_booking.create_booking_request("40555111", None, None, None,
                                 None, "cardiologia", "hospital", None)
        h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/patient-data",
                            json={"nombre": " Ana ", "apellido": "Ríos", "nacimiento": "1990-02-01", "sexo": "f"},
                            headers=self._tok_rol("admisionista"))
        self.assertEqual(h.status_code, 200, h.json())
        self.assertEqual(h.json(), {"ok": True, "paciente": {
            "nombre": "Ana", "apellido": "Ríos", "nacimiento": "1990-02-01", "sexo": "F"}})
        rec = db_booking.get_booking_request(sid)
        self.assertEqual((rec["nombre"], rec["apellido"], str(rec["nacimiento"]), rec["sexo"]),
                         ("Ana", "Ríos", "1990-02-01", "F"))
        # un solo dato no toca los demás
        h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/patient-data",
                            json={"sexo": "M"}, headers=self._tok())
        self.assertEqual(h.status_code, 200, h.json())
        rec = db_booking.get_booking_request(sid)
        self.assertEqual((rec["nombre"], rec["sexo"]), ("Ana", "M"))

    def test_los_datos_del_paciente_completan_la_cuenta_sin_pisar_lo_que_ya_tiene(self):
        db_accounts.create_user("40555222", "Leo", "Paz", None, None, None, "clave1234")
        sid = db_booking.create_booking_request("40555222", "Leo", "Paz", None,
                                 None, "cardiologia", "hospital", None)
        h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/patient-data",
                            json={"nombre": "Leonardo", "nacimiento": "1985-06-15", "sexo": "M"},
                            headers=self._tok())
        self.assertEqual(h.status_code, 200, h.json())
        cuenta = db_accounts.get_user("40555222")
        self.assertEqual((cuenta["nombre"], str(cuenta["nacimiento"]), cuenta["sexo"]),
                         ("Leo", "1985-06-15", "M"))
        self.assertEqual(db_booking.get_booking_request(sid)["nombre"], "Leonardo")

    def test_lo_que_la_mesa_completa_en_la_cuenta_llega_a_las_otras_solicitudes_vivas(self):
        # docs/decisions/2026-10-w4g.md 2026-10-25: el dato que la mesa agrega a un campo vacío de la cuenta
        # pasa por db_accounts.user_admin_update y por eso alcanza a las demás solicitudes pendientes/futuras
        db_accounts.create_user("40555444", "Leo", "Paz", None, None, None, "clave1234")
        sid = db_booking.create_booking_request("40555444", "Leo", "Paz", None,
                                 None, "cardiologia", "hospital", None)
        otra = db_booking.create_booking_request("40555444", "Leo", "Paz", None,
                                 None, "clinica", "hospital", None)
        h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/patient-data",
                            json={"nacimiento": "1985-06-15"}, headers=self._tok())
        self.assertEqual(h.status_code, 200, h.json())
        self.assertEqual(str(db_accounts.get_user("40555444")["nacimiento"]), "1985-06-15")
        self.assertEqual(str(db_booking.get_booking_request(otra)["nacimiento"]), "1985-06-15")

    def test_datos_del_paciente_invalidos_o_ajenos_no_se_guardan(self):
        sid = db_booking.create_booking_request("40555333", "Uma", "Vidal", "1990-01-01",
                                 None, "cardiologia", "hospital", None)
        for body in ({}, {"sexo": "Z"}, {"nacimiento": "01/02/1990"}, {"nacimiento": "2990-01-01"},
                     {"nombre": ""}, {"otro": "x"}):
            h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/patient-data",
                                json=body, headers=self._tok())
            self.assertEqual(h.status_code, 400, (body, h.json()))
        h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/patient-data",
                            json={"sexo": "F"}, headers=self._tok_rol("medico"))
        self.assertEqual(h.status_code, 404)
        h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/patient-data", json={"sexo": "F"})
        self.assertIn(h.status_code, (401, 403))
        self.assertEqual(str(db_booking.get_booking_request(sid)["nacimiento"]), "1990-01-01")

    def test_editar_campo_medico_ajeno_404(self):
        sid = db_booking.create_booking_request("40222333", "Leo", "Paz", "1990-01-01",
                                 None, "cardiologia", "hospital", None)
        h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/campos", json={"campo": "dni", "valor": "40222333"}, headers=self._tok_rol("medico"))
        self.assertEqual(h.status_code, 404)

    def test_editar_campo_invalido_400(self):
        sid = db_booking.create_booking_request("40333444", "Uma", "Vidal", "1990-01-01",
                                 None, "cardiologia", "hospital", None)
        h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/campos", json={"campo": "practicas", "valor": "x"}, headers=self._tok())
        self.assertEqual(h.status_code, 400)

    def test_editar_campo_solicitud_inexistente_404(self):
        h = _CLIENT.request("POST", "/api/admin/requests/999999/campos", json={"campo": "dni", "valor": "1"}, headers=self._tok())
        self.assertEqual(h.status_code, 404)

    def test_editar_campo_sin_sesion_401(self):
        sid = db_booking.create_booking_request("40555666", "Iara", "Roca", "1990-01-01",
                                 None, "cardiologia", "hospital", None)
        h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/campos", json={"campo": "dni", "valor": "1"})
        self.assertEqual(h.status_code, 401)

    # ---- medico_sugerido en /api/admin/requests (2026-07-17) ----------------

    def test_admin_list_incluye_medico_sugerido_en_pendientes(self):
        sid = db_booking.create_booking_request("40444555", "Nora", "Ibarra", "1990-01-01",
                                 None, "cardiologia", "hospital", None)
        h = _CLIENT.request("GET", "/api/admin/requests?status=pendiente", headers=self._tok())
        self.assertEqual(h.status_code, 200)
        row = next(x for x in h.json()["solicitudes"] if x["id"] == sid)
        self.assertIn("medico_sugerido", row)
        sug = row["medico_sugerido"]
        if sug is not None:   # servicio de prueba sin cluster/centro -> normalmente None
            self.assertIn("nombre", sug)
            self.assertIn("slot_iso", sug)

    def test_medico_sugerido_cluster_cajon_exige_especialidad(self):
        # En `otras` conviven especialidades distintas: un neumólogo con agenda
        # que cubre el slot NO puede sugerirse para Neurología, sí para Neumología.
        from unittest.mock import patch
        agenda = {"dias": [1, 2, 3, 4, 5], "desde": "08:00", "hasta": "12:00", "cada_min": 30}
        medicos = [{"nombre": "Lucia Miranda", "especialidad": "Neumología",
                    "cluster": "otras", "centro": "hospital-pinamar", "agenda": agenda}]
        servicios = {
            "neurologia-hospital": {"id": "neurologia-hospital", "nombre": "Neurología",
                                    "cluster": "otras", "centro": "hospital-pinamar"},
            "neumologia-hospital": {"id": "neumologia-hospital", "nombre": "Neumología",
                                    "cluster": "otras", "centro": "hospital-pinamar"},
        }
        with patch.object(booking_requests, "_service_full", side_effect=lambda sid: servicios.get(sid)):
            from app.patient_web import catalog
            with patch.object(catalog, "doctors", return_value=medicos):
                neuro = booking_queue._suggested_doctor({"servicio_id": "neurologia-hospital"}, {})
                neumo = booking_queue._suggested_doctor({"servicio_id": "neumologia-hospital"}, {})
        self.assertIsNone(neuro)
        self.assertIsNotNone(neumo)
        self.assertEqual(neumo["nombre"], "Lucia Miranda")

    def test_medico_sugerido_respeta_ocupados(self):
        from unittest.mock import patch
        from app.patient_web import site_base
        agenda = {"dias": [1, 2, 3, 4, 5], "desde": "08:00", "hasta": "12:00", "cada_min": 30}
        medicos = [{"nombre": "Ana Prueba", "especialidad": "Cardiología",
                    "cluster": "cardiologia", "centro": "hospital-pinamar", "agenda": agenda}]
        servicio = {"id": "cardiologia-hospital", "nombre": "Cardiología",
                    "cluster": "cardiologia", "centro": "hospital-pinamar"}
        with patch.object(booking_requests, "_service_full", return_value=servicio):
            from app.patient_web import catalog
            with patch.object(catalog, "doctors", return_value=medicos):
                libre = booking_queue._suggested_doctor({"servicio_id": "cardiologia-hospital"}, {})
                dia_iso, hora = libre["slot_iso"][:10], libre["slot_iso"][11:16]
                # Fila vieja sin id → clave por nombre (2026-10-23: con id, "id:<id>").
                ocupados = {"nombre:" + site_base._norm_name("Ana Prueba"): {(dia_iso, hora)}}
                tomado = booking_queue._suggested_doctor({"servicio_id": "cardiologia-hospital"}, ocupados)
        self.assertIsNotNone(libre)
        self.assertIsNotNone(tomado)
        self.assertNotEqual(libre["slot_iso"], tomado["slot_iso"])

    def test_medico_sugerido_prioriza_franja_preferida(self):
        from unittest.mock import patch
        agenda = {"dias": [1, 2, 3, 4, 5], "desde": "08:00", "hasta": "17:00", "cada_min": 60}
        medicos = [{"nombre": "Ana Prueba", "especialidad": "Cardiología",
                    "cluster": "cardiologia", "centro": "hospital-pinamar", "agenda": agenda}]
        servicio = {"id": "cardiologia-hospital", "nombre": "Cardiología",
                    "cluster": "cardiologia", "centro": "hospital-pinamar"}
        with patch.object(booking_requests, "_service_full", return_value=servicio):
            from app.patient_web import catalog
            with patch.object(catalog, "doctors", return_value=medicos):
                sug = booking_queue._suggested_doctor(
                    {"servicio_id": "cardiologia-hospital", "preferencia_horario": "A la tarde"}, {})
        self.assertIsNotNone(sug)
        self.assertGreaterEqual(int(sug["slot_iso"][11:13]), 13)

    def test_admin_list_sugiere_el_turno_que_pidio_el_paciente(self):
        # Journey 2026-09-16: la solicitud pidió jue 09:00 y la cola sugería
        # (y aprobaba) el primer libre. El horario pedido gana si sigue libre;
        # si está tomado, la fila lo dice y la sugerencia no lo finge.
        from unittest.mock import patch
        from app.patient_web import catalog, site_availability, site_base
        agenda = {"dias": [0, 1, 2, 3, 4, 5, 6], "desde": "08:00", "hasta": "17:00", "cada_min": 60}
        medicos = [{"id": "ana-prueba", "nombre": "Ana Prueba", "especialidad": "Cardiología",
                    "cluster": "cardiologia", "centro": "hospital-pinamar", "agenda": agenda}]
        servicio = {"id": "cardiologia", "nombre": "Cardiología",
                    "cluster": "cardiologia", "centro": "hospital-pinamar"}
        slots = site_availability._agenda_slots(agenda)
        pedido = slots[5]
        sid = db_booking.create_booking_request("40777888", "Lía", "Soto", "1990-01-01",
                                        None, "cardiologia", "hospital", None)
        self.assertTrue(db_slots.hold_acquire("cardiologia", pedido["dia"], pedido["hora"], "tok-pedido"))
        db_slots.hold_link_booking_request("tok-pedido", sid,
                                     booking_requests._slot_dt_art(pedido["dia"], pedido["hora"]))

        def fila(ocupados):
            with patch.object(booking_requests, "_service_full", return_value=servicio), \
                    patch.object(catalog, "doctors", return_value=medicos), \
                    patch.object(booking_queue, "_approved_by_doctor_art", return_value=ocupados):
                h = _CLIENT.request("GET", "/api/admin/requests?status=pendiente", headers=self._tok())
            self.assertEqual(h.status_code, 200)
            return next(x for x in h.json()["solicitudes"] if x["id"] == sid)

        libre = fila({})
        self.assertEqual(libre["turno_pedido"], {"dia": pedido["dia"], "hora": pedido["hora"], "libre": True})
        self.assertTrue(libre["medico_sugerido"]["slot_iso"].startswith(f"{pedido['dia']}T{pedido['hora']}"))

        tomado = fila({"nombre:" + site_base._norm_name("Ana Prueba"): {(pedido["dia"], pedido["hora"])}})
        self.assertEqual(tomado["turno_pedido"], {"dia": pedido["dia"], "hora": pedido["hora"], "libre": False})
        self.assertFalse(tomado["medico_sugerido"]["slot_iso"].startswith(f"{pedido['dia']}T{pedido['hora']}"))

    # ---- laboratorio nunca tiene médico (2026-07-19) -----------------------

    def test_medico_sugerido_none_para_laboratorio(self):
        # No hay agenda de laboratorio a sugerir: la regla es explícita
        # (_es_laboratorio), no un accidente de "no hay candidatos".
        sug = booking_queue._suggested_doctor({"servicio_id": "laboratorio-test"}, {})
        self.assertIsNone(sug)

    def test_aprobar_laboratorio_ignora_medico_del_body(self):
        sid = db_booking.create_booking_request("29123470", "Uma", "Prado", "1982-02-02",
                                 None, "laboratorio-test", "hospital", None)
        h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve", json={"turno_dt": "2099-07-20T10:30-03:00",
                              "medico": "Dr. No Debería Asignarse"}, headers=self._tok())
        self.assertEqual(h.status_code, 200, h.json())
        self.assertIsNone(h.json()["solicitud"]["medico_asignado"])
        self.assertIsNone(db_booking.get_booking_request(sid)["medico_asignado"])

    def test_admin_row_sin_medico_true_para_laboratorio_false_para_normal(self):
        lab = db_booking.create_booking_request("29123471", "Iris", "Pardo", "1982-02-02",
                                 None, "laboratorio-test", "hospital", None)
        normal = db_booking.create_booking_request("29123472", "Noa", "Pardo", "1982-02-02",
                                    None, "cardiologia", "hospital", None)
        h = _CLIENT.request("GET", "/api/admin/requests?status=pendiente", headers=self._tok())
        self.assertEqual(h.status_code, 200)
        rows = {r["id"]: r for r in h.json()["solicitudes"]}
        self.assertTrue(rows[lab]["sin_medico"])
        self.assertFalse(rows[normal].get("sin_medico"))

    def test_admin_row_turno_dt_en_art_aunque_la_sesion_de_la_base_este_en_utc(self):
        # Card 2026-09-17: la fila del panel devolvía turno_dt con el offset
        # de la SESIÓN de Postgres (UTC → "13:30+00:00") en vez de la hora
        # de pared de la instalación (10:30-03:00).
        import psycopg
        from psycopg.rows import dict_row
        sid = db_booking.create_booking_request("29123473", "Ema", "Pardo", "1982-02-02",
                                        None, "cardiologia", "hospital", None)
        with psycopg.connect(db._db_url(), options="-c TimeZone=UTC",
                             row_factory=dict_row) as conn:
            conn.execute("UPDATE booking_requests SET appointment_at = %s WHERE id = %s",
                         ("2099-07-20T10:30:00-03:00", sid))
            row = db_wire._wire(conn.execute("SELECT * FROM booking_requests WHERE id = %s",
                                        (sid,)).fetchone(), "booking_requests")
        self.assertEqual(row["turno_dt"].utcoffset(), timedelta(0))
        self.assertEqual(booking_requests._admin_row(row)["turno_dt"], "2099-07-20T10:30:00-03:00")

    def test_admin_row_acepta_una_fila_ya_serializada(self):
        # El camino de aprobación entrega la fila con turno_dt ya en texto:
        # convertirlo de nuevo reventaba con AttributeError (2026-09-17).
        fila = {"id": 1, "turno_dt": "2099-07-20T10:30:00-03:00"}
        self.assertEqual(booking_requests._admin_row(fila)["turno_dt"],
                         "2099-07-20T10:30:00-03:00")


# ---- notifications.py: email al aprobar/rechazar -------------------------

class TestNotificacionesFriendly(unittest.TestCase):
    def test_convierte_utc_a_buenos_aires(self):
        from app.notifications import send as notifications
        dt = datetime(2026, 7, 14, 11, 0, tzinfo=timezone.utc)  # 08:00 ART
        s = notifications._friendly_appointment_time(dt)
        self.assertIn("14 de julio", s)
        self.assertIn("08:00", s)

    def test_none_vacio(self):
        from app.notifications import send as notifications
        self.assertEqual(notifications._friendly_appointment_time(None), "")


class TestNotificaciones(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        from app.notifications import send as notifications
        self.notif = notifications
        self._sent = []
        self._orig_send = notifications.mail.send

        def fake_send(to, subject, text, html=None):
            self._sent.append((to, subject, text))
            return True
        notifications.mail.send = fake_send

    def tearDown(self):
        self.notif.mail.send = self._orig_send

    def _cuenta_y_solicitud(self, dni, email="paciente@example.com"):
        db_accounts.create_user(dni, "Ana", "López", "1979-06-01", "F", "wa:11", "testpass1", email=email)
        sid = db_booking.create_booking_request(dni, "Ana", "López", "1979-06-01", "wa:11",
                                 "cardiologia", "hospital-pinamar", None, solicitante_dni=dni)
        return sid

    def test_aprobar_envia_un_email(self):
        sid = self._cuenta_y_solicitud("40111222")
        row = db_booking.approve(sid, "2026-07-14T08:00", {"estado": "skip"})
        self.notif.notify_approved(row)
        self.assertEqual(len(self._sent), 1)
        to, subject, text = self._sent[0]
        self.assertEqual(to, "paciente@example.com")
        self.assertIn("Cardiología", subject)
        self.assertIn("julio", text)

    def test_sin_email_en_cuenta_no_llama(self):
        sid = self._cuenta_y_solicitud("40111223", email=None)
        row = db_booking.approve(sid, "2026-07-14T08:00", {"estado": "skip"})
        self.notif.notify_approved(row)
        self.assertEqual(self._sent, [])

    def test_rechazar_envia_email_con_motivo(self):
        sid = self._cuenta_y_solicitud("40111224")
        row = db_booking.reject(sid, "La orden no corresponde a cardiología")
        self.notif.notify_rejected(row)
        self.assertEqual(len(self._sent), 1)
        self.assertIn("La orden no corresponde", self._sent[0][2])

    def test_excepcion_del_sender_no_rompe_y_approve_persiste(self):
        sid = self._cuenta_y_solicitud("40111225")
        row = db_booking.approve(sid, "2026-07-14T08:00", {"estado": "skip"})

        def boom(*a, **k):
            raise RuntimeError("mailgun caído")
        self.notif.mail.send = boom
        self.notif.notify_approved(row)  # no debe levantar
        self.assertEqual(db_booking.get_booking_request(sid)["estado"], "aprobada")  # sigue aprobada

    def test_aprobar_route_dispara_notificacion(self):
        sid = self._cuenta_y_solicitud("40111226")
        from app.auth import admin_auth
        h = _CLIENT.request("POST", f"/api/admin/requests/{sid}/approve", json={"turno_dt": "2026-07-20T10:30"}, headers={"Cookie": "st_admin=" + admin_auth._mint_token(admin_auth._admin_user())})
        self.assertEqual(h.status_code, 200)
        self.assertEqual(len(self._sent), 1)


# ---- notifications.py: el idioma sale de la cuenta (users.lang), nunca de
# i18n.active() — no hay request bound cuando aprobar/rechazar corren desde
# una acción de mesa, y el daemon del día antes no tiene request en absoluto
# (2026-08-04). Contra la base real: create_user con `lang=` de por medio,
# para probar la columna, no un mock.

class TestNotificacionesIdioma(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        from app.notifications import send as notifications
        self.notif = notifications
        self._sent = []
        self._orig_send = notifications.mail.send

        def fake_send(to, subject, text, html=None):
            self._sent.append((to, subject, text))
            return True
        notifications.mail.send = fake_send
        # The day-before reminders read why a mail did not leave, so they call `deliver` (send is its bool).
        self._orig_deliver = notifications.mail.deliver
        notifications.mail.deliver = lambda *a, **k: fake_send(*a, **k) and notifications.mail.SENT

        # LANGUAGES lo puede tener seteado el admin real en la base
        # compartida de test (~20 worktrees) — se fija explícito acá para
        # que la cuenta "en" no caiga al canónico por un LANGUAGES=es de
        # otro test corriendo en paralelo.
        self._orig_enabled = i18n.enabled_languages
        i18n.enabled_languages = lambda: ["es", "en"]

    def tearDown(self):
        self.notif.mail.send = self._orig_send
        self.notif.mail.deliver = self._orig_deliver
        i18n.enabled_languages = self._orig_enabled

    def _cuenta_y_solicitud(self, dni, lang=None, email="paciente@example.com"):
        db_accounts.create_user(dni, "Ana", "López", "1979-06-01", "F", "wa:11", "testpass1",
                        email=email, lang=lang)
        sid = db_booking.create_booking_request(dni, "Ana", "López", "1979-06-01", "wa:11",
                                 "cardiologia", "hospital-pinamar", None, solicitante_dni=dni)
        return sid

    def test_recibida_cuenta_en_ingles(self):
        sid = self._cuenta_y_solicitud("40111230", lang="en")
        row = db_booking.get_booking_request(sid)
        self.notif.notify_received(row)
        self.assertEqual(len(self._sent), 1)
        _to, subject, text = self._sent[0]
        self.assertIn("We received your", subject)
        self.assertIn("We received your", text)
        self.assertNotIn("Recibimos", text)

    def test_recibida_cuenta_sin_preferencia_sigue_en_castellano(self):
        # Byte-idéntico a lo que ya mandaba antes de que existiera esta
        # columna: ninguna cuenta vieja tiene `lang`, así que esto es lo que
        # sigue recibiendo cualquier cuenta creada antes de 2026-08-04.
        sid = self._cuenta_y_solicitud("40111231", lang=None)
        row = db_booking.get_booking_request(sid)
        self.notif.notify_received(row)
        _to, subject, text = self._sent[0]
        self.assertIn("Recibimos tu solicitud", subject)
        self.assertIn("Recibimos tu solicitud", text)

    def test_aprobar_cuenta_en_ingles(self):
        sid = self._cuenta_y_solicitud("40111232", lang="en")
        row = db_booking.approve(sid, "2026-07-20T08:00", {"estado": "skip"})
        self.notif.notify_approved(row)
        _to, subject, text = self._sent[0]
        self.assertIn("is confirmed", subject)
        self.assertIn("is confirmed", text)
        self.assertNotIn("confirmado", text)

    def test_aprobar_cuenta_sin_preferencia_sigue_en_castellano(self):
        sid = self._cuenta_y_solicitud("40111239", lang=None)
        row = db_booking.approve(sid, "2026-07-20T08:00", {"estado": "skip"})
        self.notif.notify_approved(row)
        _to, subject, text = self._sent[0]
        self.assertIn("confirmado", subject)
        self.assertIn("confirmado", text)
        self.assertNotIn("is confirmed", text)

    def test_rechazar_cuenta_en_ingles(self):
        sid = self._cuenta_y_solicitud("40111233", lang="en")
        row = db_booking.reject(sid, "La orden no corresponde a cardiología")
        self.notif.notify_rejected(row)
        _to, subject, text = self._sent[0]
        self.assertIn("could not be approved", text)
        # El motivo en sí lo escribe la mesa en castellano (lista curada,
        # admin.html:MOTIVOS_STD) — no es texto de este catálogo, así que
        # sigue viajando tal cual.
        self.assertIn("La orden no corresponde", text)

    def test_recordatorio_cuenta_en_ingles_sin_request_bound(self):
        # Esto simula EXACTAMENTE la situación que motiva la columna: el
        # recordatorio lo dispara reminders.py (un daemon thread), sin
        # ningún request HTTP de por medio — acá no hay ninguno tampoco, es
        # una llamada directa de test. i18n.active() no tendría de dónde
        # leer el idioma; notify_reminder tiene que usar el guardado.
        self.assertIsNone(i18n._ACTIVE.get())
        sid = self._cuenta_y_solicitud("40111234", lang="en")
        row = db_booking.approve(sid, "2026-07-20T08:00", {"estado": "skip"})
        self.notif.notify_reminder(row)
        _to, subject, text = self._sent[0]
        self.assertIn("Tomorrow", subject)
        self.assertIn("you have your", text)
        self.assertNotIn("Mañana", text)

    def test_recordatorio_cuenta_sin_preferencia_sigue_en_castellano(self):
        self.assertIsNone(i18n._ACTIVE.get())
        sid = self._cuenta_y_solicitud("40111235", lang=None)
        row = db_booking.approve(sid, "2026-07-20T08:00", {"estado": "skip"})
        self.notif.notify_reminder(row)
        _to, subject, text = self._sent[0]
        self.assertIn("Mañana", subject)
        self.assertIn("tenés tu turno", text)
        self.assertNotIn("Tomorrow", text)

    def test_rechazar_cuenta_sin_preferencia_sigue_en_castellano(self):
        sid = self._cuenta_y_solicitud("40111236", lang=None)
        row = db_booking.reject(sid, "La orden no corresponde a cardiología")
        self.notif.notify_rejected(row)
        _to, subject, text = self._sent[0]
        self.assertIn("no pudo aprobarse", text)
        self.assertNotIn("could not be approved", text)

    def test_oferta_lista_espera_cuenta_en_ingles(self):
        # notify_offer no toma un `row` de booking_requests: toma la fila de
        # `waitlist` (`cand`), con su propio `email`/`dni` — mismo criterio,
        # _destination_lang la lee igual por `dni`.
        db_accounts.create_user("40111237", "Cande", "Ríos", "1990-01-01", "F",
                        "wa:11", "testpass1", email="cande@example.com", lang="en")
        cand = {"dni": "40111237", "email": "cande@example.com",
                "servicio_id": "cardiologia", "centro_slug": "hospital-pinamar"}
        self.notif.notify_offer(cand, datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc), "tok-en-offer")
        self.assertEqual(len(self._sent), 1)
        _to, subject, text = self._sent[0]
        self.assertIn("opened up", subject)
        self.assertIn("opened up", text)
        self.assertNotIn("liberó", text)

    def test_oferta_lista_espera_cuenta_sin_preferencia_sigue_en_castellano(self):
        db_accounts.create_user("40111240", "Otra", "Ríos", "1990-01-01", "F",
                        "wa:11", "testpass1", email="otra@example.com", lang=None)
        cand = {"dni": "40111240", "email": "otra@example.com",
                "servicio_id": "cardiologia", "centro_slug": "hospital-pinamar"}
        self.notif.notify_offer(cand, datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc), "tok-es-offer")
        _to, subject, text = self._sent[0]
        self.assertIn("liberó", subject)
        self.assertIn("liberó", text)
        self.assertNotIn("opened up", text)

    def test_entrevista_recordatorio_cuenta_en_ingles(self):
        from app.modules.turnos import booking_requests
        orig_campo = booking_requests._entrevista_field
        orig_token = booking_requests._entrevista_patient_token
        booking_requests._entrevista_field = lambda sid, doc=None: {"ofrecer": True, "url": f"/entrevista/{sid}"}
        booking_requests._entrevista_patient_token = lambda sid: None
        try:
            sid = self._cuenta_y_solicitud("40111238", lang="en")
            row = db_booking.approve(sid, "2026-07-20T08:00", {"estado": "skip"})
            self.notif.notify_interview_reminder(row)
            self.assertEqual(len(self._sent), 1)
            _to, subject, text = self._sent[0]
            self.assertIn("Tomorrow", subject)
            self.assertIn("tell the doctor", subject.lower())
            self.assertNotIn("Mañana", text)
        finally:
            booking_requests._entrevista_field = orig_campo
            booking_requests._entrevista_patient_token = orig_token

    def test_entrevista_recordatorio_cuenta_sin_preferencia_sigue_en_castellano(self):
        from app.modules.turnos import booking_requests
        orig_campo = booking_requests._entrevista_field
        orig_token = booking_requests._entrevista_patient_token
        booking_requests._entrevista_field = lambda sid, doc=None: {"ofrecer": True, "url": f"/entrevista/{sid}"}
        booking_requests._entrevista_patient_token = lambda sid: None
        try:
            sid = self._cuenta_y_solicitud("40111241", lang=None)
            row = db_booking.approve(sid, "2026-07-20T08:00", {"estado": "skip"})
            self.notif.notify_interview_reminder(row)
            self.assertEqual(len(self._sent), 1)
            _to, subject, text = self._sent[0]
            self.assertIn("Mañana", subject)
            self.assertIn("contale al médico", subject.lower())
            self.assertNotIn("Tomorrow", text)
        finally:
            booking_requests._entrevista_field = orig_campo
            booking_requests._entrevista_patient_token = orig_token


# ---- POST /api/requests/<id>/cancel -----------------------------------

class TestCancelar(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")

    def test_sin_sesion_401(self):
        orig = booking_requests._get_user
        booking_requests._get_user = lambda h: None
        try:
            h = _CLIENT.request("POST", "/api/requests/1/cancel", json={})
            self.assertEqual(h.status_code, 401)
            self.assertEqual(h.json()["error"], "auth_requerida")
        finally:
            booking_requests._get_user = orig

    def test_ajena_404(self):
        orig = booking_requests._get_user
        sid = db_booking.create_booking_request("42444001", "Rita", "Vega", "1988-01-01", None,
                                 "cardiologia", "hospital", None)
        booking_requests._get_user = lambda h: {"dni": "30999888", "nombre": "Otro", "apellido": "X"}
        try:
            h = _CLIENT.request("POST", f"/api/requests/{sid}/cancel", json={})
            self.assertEqual(h.status_code, 404)
        finally:
            booking_requests._get_user = orig

    def test_cancela_pendiente_200(self):
        orig = booking_requests._get_user
        dni = "42444002"
        sid = db_booking.create_booking_request(dni, "P", "T", "1980-01-01", None, "dermatologia", "caps", None,
                                 solicitante_dni=dni)
        booking_requests._get_user = lambda h: {"dni": dni, "nombre": "P", "apellido": "T"}
        try:
            h = _CLIENT.request("POST", f"/api/requests/{sid}/cancel", json={})
            self.assertEqual(h.status_code, 200, h.json())
            self.assertEqual(h.json()["solicitud"]["estado"], "cancelada")
            self.assertEqual(db_booking.get_booking_request(sid)["estado"], "cancelada")
        finally:
            booking_requests._get_user = orig

    def test_ya_rechazada_no_cancelable_400(self):
        orig = booking_requests._get_user
        dni = "42444003"
        sid = db_booking.create_booking_request(dni, "P", "T", "1980-01-01", None, "dermatologia", "caps", None,
                                 solicitante_dni=dni)
        db_booking.reject(sid, "motivo cualquiera")
        booking_requests._get_user = lambda h: {"dni": dni, "nombre": "P", "apellido": "T"}
        try:
            h = _CLIENT.request("POST", f"/api/requests/{sid}/cancel", json={})
            self.assertEqual(h.status_code, 400)
            self.assertEqual(h.json()["error"], "no_cancelable")
        finally:
            booking_requests._get_user = orig

    def test_aprobada_pasada_400(self):
        orig = booking_requests._get_user
        dni = "42444004"
        sid = db_booking.create_booking_request(dni, "P", "T", "1980-01-01", None, "dermatologia", "caps", None,
                                 solicitante_dni=dni)
        db_booking.approve(sid, "2020-01-01T09:00", {"estado": "skip"})
        booking_requests._get_user = lambda h: {"dni": dni, "nombre": "P", "apellido": "T"}
        try:
            h = _CLIENT.request("POST", f"/api/requests/{sid}/cancel", json={})
            self.assertEqual(h.status_code, 400)
            self.assertEqual(h.json()["error"], "turno_pasado")
        finally:
            booking_requests._get_user = orig

    def test_cancela_aprobada_futura_dispara_liberar_slot(self):
        orig = booking_requests._get_user
        dni = "42444005"
        sid = db_booking.create_booking_request(dni, "P", "T", "1980-01-01", None, "cardiologia", "hospital", None,
                                 solicitante_dni=dni)
        db_booking.approve(sid, "2099-01-01T09:00", {"estado": "skip"})
        booking_requests._get_user = lambda h: {"dni": dni, "nombre": "P", "apellido": "T"}
        calls = []
        orig_liberar = waitlist.release_slot

        def fake_liberar(servicio_id, centro_slug, slot_dt, handler=None):
            calls.append((servicio_id, centro_slug))
            return None
        waitlist.release_slot = fake_liberar
        try:
            h = _CLIENT.request("POST", f"/api/requests/{sid}/cancel", json={})
            self.assertEqual(h.status_code, 200, h.json())
            self.assertEqual(calls, [("cardiologia", "hospital")])
            # el cancel persiste igual aunque liberar_slot explote (best-effort)
        finally:
            booking_requests._get_user = orig
            waitlist.release_slot = orig_liberar

    def test_liberar_slot_que_explota_no_rompe_el_cancel(self):
        orig = booking_requests._get_user
        dni = "42444006"
        sid = db_booking.create_booking_request(dni, "P", "T", "1980-01-01", None, "cardiologia", "hospital", None,
                                 solicitante_dni=dni)
        db_booking.approve(sid, "2099-01-01T09:00", {"estado": "skip"})
        booking_requests._get_user = lambda h: {"dni": dni, "nombre": "P", "apellido": "T"}
        orig_liberar = waitlist.release_slot

        def boom(*a, **k):
            raise RuntimeError("waitlist caída")
        waitlist.release_slot = boom
        try:
            h = _CLIENT.request("POST", f"/api/requests/{sid}/cancel", json={})
            self.assertEqual(h.status_code, 200, h.json())  # no debe levantar ni cambiar el status
            self.assertEqual(db_booking.get_booking_request(sid)["estado"], "cancelada")
        finally:
            booking_requests._get_user = orig
            waitlist.release_slot = orig_liberar


# ---- POST /api/requests/<id>/reprogramar (el paciente elige otro horario) --

class TestReprogramar(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")

    def test_sin_sesion_401(self):
        orig = booking_requests._get_user
        booking_requests._get_user = lambda h: None
        try:
            h = _CLIENT.request("POST", "/api/requests/1/reprogramar", json={"dia": "2099-01-05", "hora": "09:00"})
            self.assertEqual(h.status_code, 401)
            self.assertEqual(h.json()["error"], "auth_requerida")
        finally:
            booking_requests._get_user = orig

    def test_ajena_404(self):
        orig = booking_requests._get_user
        sid = db_booking.create_booking_request("42555001", "Rita", "Vega", "1988-01-01", None,
                                 "cardiologia", "hospital", None)
        booking_requests._get_user = lambda h: {"dni": "30999888", "nombre": "Otro", "apellido": "X"}
        try:
            h = _CLIENT.request("POST", f"/api/requests/{sid}/reprogramar", json={"dia": "2099-01-05", "hora": "09:00"})
            self.assertEqual(h.status_code, 404)
        finally:
            booking_requests._get_user = orig

    @patch("app.modules.turnos.booking_horizon._local_now", new=lambda now: datetime(2099, 1, 4, 8))
    def test_pendiente_cambia_preferencia_no_toca_turno_dt(self):
        orig = booking_requests._get_user
        dni = "42555002"
        sid = db_booking.create_booking_request(dni, "P", "T", "1980-01-01", None, "dermatologia", "caps", None,
                                 solicitante_dni=dni)
        booking_requests._get_user = lambda h: {"dni": dni, "nombre": "P", "apellido": "T"}
        try:
            h = _CLIENT.request("POST", f"/api/requests/{sid}/reprogramar", json={"dia": "2099-01-05", "hora": "09:00", "preferencia": "Martes 9am"})
            self.assertEqual(h.status_code, 200, h.json())
            row = db_booking.get_booking_request(sid)
            self.assertEqual(row["estado"], "pendiente")
            self.assertEqual(row["preferencia_horario"], "Martes 9am")
            self.assertIsNone(row["turno_dt"])
        finally:
            booking_requests._get_user = orig

    def test_estado_no_reprogramable_400(self):
        orig = booking_requests._get_user
        dni = "42555003"
        sid = db_booking.create_booking_request(dni, "P", "T", "1980-01-01", None, "dermatologia", "caps", None,
                                 solicitante_dni=dni)
        db_booking.reject(sid, "motivo cualquiera")
        booking_requests._get_user = lambda h: {"dni": dni, "nombre": "P", "apellido": "T"}
        try:
            h = _CLIENT.request("POST", f"/api/requests/{sid}/reprogramar", json={"dia": "2099-01-05", "hora": "09:00"})
            self.assertEqual(h.status_code, 400)
            self.assertEqual(h.json()["error"], "no_reprogramable")
        finally:
            booking_requests._get_user = orig

    def test_aprobada_pasada_400(self):
        orig = booking_requests._get_user
        dni = "42555004"
        sid = db_booking.create_booking_request(dni, "P", "T", "1980-01-01", None, "dermatologia", "caps", None,
                                 solicitante_dni=dni)
        db_booking.approve(sid, "2020-01-01T09:00", {"estado": "skip"})
        booking_requests._get_user = lambda h: {"dni": dni, "nombre": "P", "apellido": "T"}
        try:
            h = _CLIENT.request("POST", f"/api/requests/{sid}/reprogramar", json={"dia": "2099-01-05", "hora": "09:00"})
            self.assertEqual(h.status_code, 400)
            self.assertEqual(h.json()["error"], "turno_pasado")
        finally:
            booking_requests._get_user = orig

    @patch("app.modules.turnos.booking_horizon._local_now", new=lambda now: datetime(2099, 2, 9, 8))
    def test_aprobada_futura_reasigna_turno_dt_y_libera_el_slot_viejo(self):
        # turno_dt viaja como timestamptz: comparar en ART (mismo gotcha
        # documentado en core/tz.py — el offset crudo depende del TZ de sesión
        # de Postgres, no confiar en él).
        orig = booking_requests._get_user
        dni = "42555005"
        sid = db_booking.create_booking_request(dni, "P", "T", "1980-01-01", None, "cardiologia", "hospital", None,
                                 solicitante_dni=dni)
        viejo_dt = booking_requests._slot_dt_art("2099-01-01", "09:00")
        db_booking.approve(sid, viejo_dt, {"estado": "skip"})
        booking_requests._get_user = lambda h: {"dni": dni, "nombre": "P", "apellido": "T"}
        calls = []
        orig_liberar = waitlist.release_slot

        def fake_liberar(servicio_id, centro_slug, slot_dt, handler=None):
            calls.append((servicio_id, centro_slug, slot_dt))
            return None
        waitlist.release_slot = fake_liberar
        try:
            h = _CLIENT.request("POST", f"/api/requests/{sid}/reprogramar", json={"dia": "2099-02-10", "hora": "14:00", "preferencia": "Dr. X · 10 feb"})
            self.assertEqual(h.status_code, 200, h.json())
            row = db_booking.get_booking_request(sid)
            self.assertEqual(row["estado"], "aprobada")
            self.assertEqual(row["turno_dt"].astimezone(booking_requests._ART).isoformat()[:16], "2099-02-10T14:00")
            # se libera el slot VIEJO (2099-01-01), no el nuevo
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "cardiologia")
            self.assertEqual(calls[0][2].astimezone(booking_requests._ART).isoformat()[:16], "2099-01-01T09:00")
        finally:
            booking_requests._get_user = orig
            waitlist.release_slot = orig_liberar

    @patch("app.modules.turnos.booking_horizon._local_now", new=lambda now: datetime(2099, 7, 19, 8))
    def test_aprobada_con_medico_solo_acepta_su_agenda(self):
        dni = "42555015"
        sid = db_booking.create_booking_request(dni, "P", "Horario", "1980-01-01", None,
                                 "cardiologia", "hospital", None, solicitante_dni=dni)
        db_booking.approve(sid, booking_requests._slot_dt_art("2099-07-20", "10:00"),
                   {"estado": "skip"}, medico_asignado="Dra. Horario",
                   medico_asignado_id="dra-horario")
        old_user = booking_requests._get_user
        booking_requests._get_user = lambda h: {"dni": dni, "nombre": "P", "apellido": "Horario"}
        agenda = [{"id": "dra-horario", "nombre": "Dra. Horario", "centro": "hospital",
                   "agenda": {"franjas": [{"dias": [1], "desde": "10:00", "hasta": "11:00",
                                              "cada_min": 30}], "excepciones": []}}]
        try:
            with patch("app.patient_web.catalog.doctors", return_value=agenda):
                h = _CLIENT.request("POST", f"/api/requests/{sid}/reprogramar", json={"dia": "2099-07-20", "hora": "10:45"})
                self.assertEqual(h.status_code, 409, h.json())
                self.assertEqual(h.json()["error"], "turno_medico_fuera_de_agenda")
                self.assertEqual(db_booking.get_booking_request(sid)["turno_dt"].astimezone(booking_requests._ART)
                                 .isoformat()[:16], "2099-07-20T10:00")
        finally:
            booking_requests._get_user = old_user

    @patch("app.modules.turnos.booking_horizon._local_now", new=lambda now: datetime(2099, 2, 9, 8))
    def test_reprogramar_guarda_hora_confirmada_por_hsi(self):
        from app.connectors.hsi import hsi_client
        dni = "42555012"
        sid = db_booking.create_booking_request(dni, "P", "T", "1980-01-01", None,
                                 "dermatologia", "caps", None, solicitante_dni=dni)
        db_booking.approve(sid, booking_requests._slot_dt_art("2099-01-01", "09:00"), {"estado": "skip"})
        old_user = booking_requests._get_user
        old_push = hsi_client.push_turno
        booking_requests._get_user = lambda h: {"dni": dni, "nombre": "P", "apellido": "T"}
        _env.set_env(self, TURNOS_SKIP_HSI="0", CONNECTOR_EMR_SYNC="hsi")
        hsi_client.push_turno = lambda *a, **kw: {
            "estado": "ok", "patient_id": 12, "appointment_id": 35,
            "actual_turno_dt": "2099-02-11T15:00:00-03:00",
        }
        try:
            h = _CLIENT.request("POST", f"/api/requests/{sid}/reprogramar", json={"dia": "2099-02-10", "hora": "14:00"})
            self.assertEqual(h.status_code, 200, h.json())
            row = db_booking.get_booking_request(sid)
            self.assertEqual(row["hsi_estado"], "ok")
            self.assertEqual(row["turno_dt"].astimezone(booking_requests._ART).isoformat()[:16],
                             "2099-02-11T15:00")
        finally:
            hsi_client.push_turno = old_push
            booking_requests._get_user = old_user

    @patch("app.modules.turnos.booking_horizon._local_now", new=lambda now: datetime(2099, 2, 28, 8))
    def test_slot_ya_tomado_por_otra_solicitud_409(self):
        orig = booking_requests._get_user
        dni = "42555006"
        # otra solicitud ya aprobada y confirmada en ese mismo horario (mismo
        # helper _slot_dt_art que usa _reprogramar, así el instante coincide
        # exacto sin depender del TZ de sesión de Postgres).
        otro_sid = db_booking.create_booking_request("42555007", "Otro", "Paciente", "1980-01-01", None,
                                      "cardiologia", "hospital", None)
        db_booking.approve(otro_sid, booking_requests._slot_dt_art("2099-03-01", "10:00"), {"estado": "skip"})
        sid = db_booking.create_booking_request(dni, "P", "T", "1980-01-01", None, "cardiologia", "hospital", None,
                                 solicitante_dni=dni)
        booking_requests._get_user = lambda h: {"dni": dni, "nombre": "P", "apellido": "T"}
        try:
            h = _CLIENT.request("POST", f"/api/requests/{sid}/reprogramar", json={"dia": "2099-03-01", "hora": "10:00"})
            self.assertEqual(h.status_code, 409, h.json())
            self.assertEqual(h.json()["error"], "turno_tomado")
            # la solicitud propia sigue pendiente, sin tocar
            self.assertEqual(db_booking.get_booking_request(sid)["estado"], "pendiente")
        finally:
            booking_requests._get_user = orig


# ---- POST /api/requests/<id>/waitlist (opt-in/out) ------------------

class TestListaEsperaToggle(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")

    def test_sin_sesion_401(self):
        orig = booking_requests._get_user
        booking_requests._get_user = lambda h: None
        try:
            h = _CLIENT.request("POST", "/api/requests/1/waitlist", json={"activo": True})
            self.assertEqual(h.status_code, 401)
        finally:
            booking_requests._get_user = orig

    def test_ajena_404(self):
        orig = booking_requests._get_user
        dni = "42333001"
        db_accounts.create_user(dni, "P", "T", "1980-01-01", "F", "wa:11", "testpass1", email="p1@example.com")
        sid = db_booking.create_booking_request(dni, "P", "T", "1980-01-01", None, "cardiologia", "hospital", None,
                                 solicitante_dni=dni)
        db_booking.approve(sid, "2099-01-01T09:00", {"estado": "skip"})
        booking_requests._get_user = lambda h: {"dni": "30999888", "nombre": "Otro", "apellido": "X"}
        try:
            h = _CLIENT.request("POST", f"/api/requests/{sid}/waitlist", json={"activo": True})
            self.assertEqual(h.status_code, 404)
        finally:
            booking_requests._get_user = orig

    def test_pendiente_no_aplica_400(self):
        orig = booking_requests._get_user
        dni = "42333002"
        sid = db_booking.create_booking_request(dni, "P", "T", "1980-01-01", None, "cardiologia", "hospital", None,
                                 solicitante_dni=dni)
        booking_requests._get_user = lambda h: {"dni": dni, "nombre": "P", "apellido": "T"}
        try:
            h = _CLIENT.request("POST", f"/api/requests/{sid}/waitlist", json={"activo": True})
            self.assertEqual(h.status_code, 400)
            self.assertEqual(h.json()["error"], "no_aplica")
        finally:
            booking_requests._get_user = orig

    def test_sin_email_400(self):
        orig = booking_requests._get_user
        dni = "42333003"
        db_accounts.create_user(dni, "P", "T", "1980-01-01", "F", "wa:11", "testpass1", email=None)
        sid = db_booking.create_booking_request(dni, "P", "T", "1980-01-01", None, "cardiologia", "hospital", None,
                                 solicitante_dni=dni)
        db_booking.approve(sid, "2099-01-01T09:00", {"estado": "skip"})
        booking_requests._get_user = lambda h: {"dni": dni, "nombre": "P", "apellido": "T"}
        try:
            h = _CLIENT.request("POST", f"/api/requests/{sid}/waitlist", json={"activo": True})
            self.assertEqual(h.status_code, 400)
            self.assertEqual(h.json()["error"], "falta_email")
        finally:
            booking_requests._get_user = orig

    def test_alta_y_baja(self):
        orig = booking_requests._get_user
        dni = "42333004"
        db_accounts.create_user(dni, "P", "T", "1980-01-01", "F", "wa:11", "testpass1", email="p4@example.com")
        sid = db_booking.create_booking_request(dni, "P", "T", "1980-01-01", None, "cardiologia", "hospital", None,
                                 solicitante_dni=dni)
        db_booking.approve(sid, "2099-01-01T09:00", {"estado": "skip"})
        booking_requests._get_user = lambda h: {"dni": dni, "nombre": "P", "apellido": "T"}
        try:
            h = _CLIENT.request("POST", f"/api/requests/{sid}/waitlist", json={"activo": True})
            self.assertEqual(h.status_code, 200, h.json())
            self.assertTrue(h.json()["activo"])
            self.assertTrue(db_slots.waitlist_active(sid))

            det = _CLIENT.request("GET", f"/api/requests/{sid}")
            self.assertTrue(det.json()["solicitud"]["lista_espera_activo"])

            h2 = _CLIENT.request("POST", f"/api/requests/{sid}/waitlist", json={"activo": False})
            self.assertEqual(h2.status_code, 200, h2.json())
            self.assertFalse(h2.json()["activo"])
            self.assertFalse(db_slots.waitlist_active(sid))
        finally:
            booking_requests._get_user = orig


# ---- appointment_offer.py: token de la oferta --------------------------------

class _MailCaptureMixin:
    """Evita pegarle a Mailgun de verdad: waitlist._create_offer y
    notifications.notify_approved mandan mail, y varios de estos tests
    disparan una corrida real de ofertas."""

    def setUp(self):
        super().setUp()
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self._orig_send = email_mailgun.send
        self._sent = []

        def fake_send(to, subject, text, html=None):
            self._sent.append((to, subject, text))
            return True
        email_mailgun.send = fake_send

    def tearDown(self):
        email_mailgun.send = self._orig_send
        super().tearDown()


class TestListaEsperaApi(_MailCaptureMixin, unittest.TestCase):
    def _cuenta(self, dni, email):
        db_accounts.create_user(dni, "Persona", dni[-4:], "1980-01-01", "F", "wa:11", "testpass1", email=email)

    def _en_espera(self, dni, email, turno_actual="2099-06-01T09:00"):
        self._cuenta(dni, email)
        sid = db_booking.create_booking_request(dni, "Persona", dni[-4:], "1980-01-01", None,
                                 "cardiologia", "hospital", None, solicitante_dni=dni)
        db_booking.approve(sid, turno_actual, {"estado": "skip"})
        row = db_booking.get_booking_request(sid)
        waitlist.opt_in(sid, dni, email, "cardiologia", "hospital", row["turno_dt"])
        return sid

    def test_token_invalido_pagina_amable(self):
        h = _CLIENT.request("GET", "/appointment/offer/no-existe-este-token")
        self.assertEqual(h.status_code, 200)
        body = h.content.decode("utf-8")
        self.assertIn("ya no está disponible", body)
        # sin fila (token inválido) no hay servicio que ofrecer: el punto
        # muerto degrada al directorio general, nunca sólo "Mis solicitudes"
        # (the product owner, 2026-07-16: "it should offer the next one").
        self.assertIn('href="/">Ver todos los servicios', body)

    def test_oferta_vigente_muestra_acciones(self):
        self._en_espera("42111001", "ganador1@example.com")
        # Fecha futura estable (anterior al turno_actual 2099-06-01): con una
        # fecha fija tipo "2026-08-01" el test se pudría el día que el
        # calendario la alcanzaba (falló el 2026-08-01 mismo).
        oid = waitlist.release_slot("cardiologia", "hospital", "2099-05-01T09:00")
        self.assertIsNotNone(oid)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT token FROM appointment_offers WHERE id=%s", (oid,))
            token = cur.fetchone()["token"]
        h = _CLIENT.request("GET", f"/appointment/offer/{token}")
        self.assertEqual(h.status_code, 200)
        body = h.content.decode("utf-8")
        self.assertIn("Tomar este turno", body)
        self.assertIn("No lo quiero", body)

    def test_oferta_vencida_pagina_amable(self):
        # "cardiologia"/"hospital" son los ids FAKE del catálogo de este test
        # module (no existen en el webdata real) — la oferta vencida conoce el
        # servicio_id pero patient_web/site_*.py no puede resolverlo: degrada al
        # directorio general, no a un cartel muerto.
        self._en_espera("42111002", "ganador2@example.com")
        # The slot itself must stay in the future; expiry is forced below.
        # A calendar-date fixture made this test fail once that day arrived.
        oid = waitlist.release_slot("cardiologia", "hospital", "2099-05-02T09:00")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE appointment_offers SET expires_at=now() - interval '1 minute' WHERE id=%s RETURNING token",
                (oid,))
            token = cur.fetchone()["token"]
        h = _CLIENT.request("GET", f"/appointment/offer/{token}")
        body = h.content.decode("utf-8")
        self.assertIn("ya no está disponible", body)
        self.assertIn('href="/">Ver todos los servicios', body)

    @reference_pack_test
    def test_oferta_vencida_ofrece_otro_turno_del_mismo_servicio(self):
        """La oferta vencida SABE el servicio: si sigue teniendo agenda, la
        página "ya no disponible" ofrece el próximo turno de ESE servicio
        (mismo CTA con verbo+fecha que la banda de /service/<id>) en vez de
        un punto muerto (responsable del producto, 2026-07-16, tras ver la pantalla en producción:
        "it should offer the next one and allow to choose all the from the
        other ones"). Usa un servicio REAL del webdata (cardiologia-hospital,
        agenda semanal recurrente sin fecha de corte) para que la disponibilidad
        no dependa de datos fixture propios de este módulo."""
        self._cuenta("42111010", "vencida-cardio@example.com")
        sid = db_booking.create_booking_request("42111010", "Persona", "0010", "1980-01-01", None,
                                 "cardiologia-hospital", "hospital-pinamar", None,
                                 solicitante_dni="42111010")
        db_booking.approve(sid, "2099-01-01T09:00", {"estado": "skip"})
        row = db_booking.get_booking_request(sid)
        waitlist.opt_in(sid, "42111010", "vencida-cardio@example.com",
                           "cardiologia-hospital", "hospital-pinamar", row["turno_dt"])
        oid = waitlist.release_slot("cardiologia-hospital", "hospital-pinamar",
                                        "2098-12-20T09:00")
        self.assertIsNotNone(oid)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE appointment_offers SET expires_at=now() - interval '1 minute' WHERE id=%s RETURNING token",
                (oid,))
            token = cur.fetchone()["token"]
        h = _CLIENT.request("GET", f"/appointment/offer/{token}")
        body = h.content.decode("utf-8")
        self.assertIn("ya no está disponible", body)
        self.assertIn("/service/cardiologia-hospital/book?slot=", body)
        self.assertIn(">Más horarios<", body)
        self.assertIn("/service/cardiologia-hospital/book?slots=1", body)

    @reference_pack_test
    def test_oferta_vencida_sin_agenda_ofrece_pagina_del_servicio(self):
        """Sin turno online que ofrecer, la alternativa vuelve a la página del
        servicio (todavía se puede pedir, el hospital confirma la fecha) — no
        se inventa un turno ni se deja al paciente sin ningún camino hacia
        adelante."""
        self._cuenta("42111011", "vencida-sinagenda@example.com")
        sid = db_booking.create_booking_request("42111011", "Persona", "0011", "1980-01-01", None,
                                 "diabetologia-infantil-hospital", "hospital-pinamar", None,
                                 solicitante_dni="42111011")
        db_booking.approve(sid, "2099-01-01T09:00", {"estado": "skip"})
        row = db_booking.get_booking_request(sid)
        waitlist.opt_in(sid, "42111011", "vencida-sinagenda@example.com",
                           "diabetologia-infantil-hospital", "hospital-pinamar", row["turno_dt"])
        # Fecha lejana a propósito (no "hoy"): un test con fecha fija tipo
        # "2026-08-05" colisiona con el calendario real el día que lo alcanza
        # (responsable del producto, hallado durante la integración del batch 2 de i18n).
        # Tiene que ser ANTERIOR al turno aprobado (2099-01-01): la cola sólo
        # ofrece un slot liberado a quien mejora su turno actual (_queue()
        # exige appointment_at > slot_dt), así que una fecha posterior vacía
        # la cola y el test deja de poder probar "sin agenda".
        oid = waitlist.release_slot("diabetologia-infantil-hospital", "hospital-pinamar",
                                        "2098-12-15T09:00")
        self.assertIsNotNone(oid)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE appointment_offers SET expires_at=now() - interval '1 minute' WHERE id=%s RETURNING token",
                (oid,))
            token = cur.fetchone()["token"]
        h = _CLIENT.request("GET", f"/appointment/offer/{token}")
        body = h.content.decode("utf-8")
        self.assertIn("ya no está disponible", body)
        self.assertIn('href="/service/diabetologia-infantil-hospital"', body)
        # sin turno real: sin CTA de "Sacar…" (sólo el link de vuelta a la
        # página del servicio) — sin "?slot=" no se armó el botón de turno.
        self.assertNotIn("?slot=", body)

    def test_aceptar_reasigna_turno_y_marca_hsi(self):
        sid = self._en_espera("42111003", "ganador3@example.com")
        oid = waitlist.release_slot("cardiologia", "hospital", "2099-01-05T09:00")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT token FROM appointment_offers WHERE id=%s", (oid,))
            token = cur.fetchone()["token"]
        h = _CLIENT.request("POST", f"/api/offer/{token}/accept")
        self.assertEqual(h.status_code, 200, h.json())
        self.assertTrue(h.json()["ok"])
        self.assertEqual(h.json()["solicitud_id"], sid)
        updated = db_booking.get_booking_request(sid)
        self.assertEqual(updated["turno_dt"].strftime("%Y-%m-%d"), "2099-01-05")
        self.assertEqual(updated["estado"], "aprobada")
        self.assertEqual(updated["hsi_estado"], "skip")  # TURNOS_SKIP_HSI=1 en el entorno de test
        # y ya no queda activa en la lista de espera (reclamar la sacó)
        self.assertFalse(db_slots.waitlist_active(sid))

    def test_aceptar_token_vencido_410(self):
        self._en_espera("42111004", "ganador4@example.com")
        oid = waitlist.release_slot("cardiologia", "hospital", "2099-01-06T09:00")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE appointment_offers SET expires_at=now() - interval '1 minute' WHERE id=%s RETURNING token",
                (oid,))
            token = cur.fetchone()["token"]
        h = _CLIENT.request("POST", f"/api/offer/{token}/accept")
        self.assertEqual(h.status_code, 410)

    def test_aceptar_token_inexistente_404(self):
        h = _CLIENT.request("POST", "/api/offer/no-existe/accept")
        self.assertEqual(h.status_code, 404)

    def test_rechazar_pasa_al_siguiente(self):
        self._en_espera("42111005", "primero@example.com")
        self._en_espera("42111006", "segundo@example.com")
        # Fecha lejana a propósito, ver comentario en test_oferta_vencida_sin_agenda_*.
        oid = waitlist.release_slot("cardiologia", "hospital", "2099-01-07T09:00")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT token, slot_at FROM appointment_offers WHERE id=%s", (oid,))
            primera = cur.fetchone()

        h = _CLIENT.request("POST", f"/api/offer/{primera['token']}/reject")
        self.assertEqual(h.status_code, 200, h.json())
        self.assertTrue(h.json()["ok"])

        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT id, status, waitlist_id FROM appointment_offers
                   WHERE service_id='cardiologia' AND center_slug='hospital' AND slot_at=%s
                   ORDER BY id""",
                (primera["slot_at"],))
            filas = cur.fetchall()
        self.assertEqual(len(filas), 2)
        self.assertEqual(filas[0]["id"], oid)
        self.assertEqual(filas[0]["status"], "expirada")
        self.assertEqual(filas[1]["status"], "ofrecida")
        self.assertNotEqual(filas[0]["waitlist_id"], filas[1]["waitlist_id"])

    def test_rechazar_token_ya_resuelto_es_idempotente(self):
        self._en_espera("42111007", "unico@example.com")
        oid = waitlist.release_slot("cardiologia", "hospital", "2099-01-08T09:00")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT token FROM appointment_offers WHERE id=%s", (oid,))
            token = cur.fetchone()["token"]
        h1 = _CLIENT.request("POST", f"/api/offer/{token}/reject")
        self.assertEqual(h1.status_code, 200, h1.json())
        h2 = _CLIENT.request("POST", f"/api/offer/{token}/reject")
        self.assertEqual(h2.status_code, 200, h2.json())
        self.assertTrue(h2.json()["ok"])  # segunda vez: confirma, no revienta


# ---- flujo end-to-end de la carrera: A cancela -> oferta a B -> B acepta ---

class TestCarreraListaEspera(_MailCaptureMixin, unittest.TestCase):
    def test_a_cancela_b_recibe_oferta_y_acepta(self):
        # A: turno más cercano (2099-09-01), lo va a cancelar.
        dni_a = "42222001"
        db_accounts.create_user(dni_a, "A", "Uno", "1980-01-01", "F", "wa:11", "testpass1", email="a@example.com")
        sid_a = db_booking.create_booking_request(dni_a, "A", "Uno", "1980-01-01", None,
                                   "cardiologia", "hospital", None, solicitante_dni=dni_a)
        db_booking.approve(sid_a, "2099-09-01T09:00", {"estado": "skip"})

        # B: turno más lejano (2099-10-01), pide que le avisen si se libera algo antes.
        dni_b = "42222002"
        db_accounts.create_user(dni_b, "B", "Dos", "1980-01-01", "F", "wa:11", "testpass1", email="b@example.com")
        sid_b = db_booking.create_booking_request(dni_b, "B", "Dos", "1980-01-01", None,
                                   "cardiologia", "hospital", None, solicitante_dni=dni_b)
        db_booking.approve(sid_b, "2099-10-01T09:00", {"estado": "skip"})
        row_b = db_booking.get_booking_request(sid_b)
        waitlist.opt_in(sid_b, dni_b, "b@example.com", "cardiologia", "hospital", row_b["turno_dt"])

        # A cancela vía el endpoint del paciente (sesión = A) — esto DISPARA
        # liberar_slot, que ofrece el turno a B (único candidato en la cola).
        orig_get = booking_requests._get_user
        booking_requests._get_user = lambda h: {"dni": dni_a, "nombre": "A", "apellido": "Uno"}
        try:
            h = _CLIENT.request("POST", f"/api/requests/{sid_a}/cancel", json={})
            self.assertEqual(h.status_code, 200, h.json())
        finally:
            booking_requests._get_user = orig_get
        self.assertEqual(db_booking.get_booking_request(sid_a)["estado"], "cancelada")

        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT token FROM appointment_offers
                   WHERE service_id='cardiologia' AND center_slug='hospital' AND status='ofrecida'
                   ORDER BY id DESC LIMIT 1""")
            fila = cur.fetchone()
        self.assertIsNotNone(fila, "esperaba una oferta creada por el cancel de A")
        token = fila["token"]

        # B ve la página de la oferta.
        hp = _CLIENT.request("GET", f"/appointment/offer/{token}")
        self.assertIn("Tomar este turno", hp.content.decode("utf-8"))

        # B acepta: su solicitud queda con el turno que A liberó.
        ha = _CLIENT.request("POST", f"/api/offer/{token}/accept")
        self.assertEqual(ha.status_code, 200, ha.json())
        self.assertEqual(ha.json()["solicitud_id"], sid_b)
        updated_b = db_booking.get_booking_request(sid_b)
        self.assertEqual(updated_b["turno_dt"].strftime("%Y-%m-%d"), "2099-09-01")
        self.assertEqual(updated_b["estado"], "aprobada")


class TestExtensionOrden(unittest.TestCase):
    """_sniff_orden/_ext_for — el fix del XSS almacenado (2026-07-17/18): la
    extensión con la que se guarda una orden sale de los MAGIC BYTES, nunca del
    mime/filename que declara el cliente; lo no reconocido cae al allowlist y
    lo que no está en el allowlist cae a .bin (jamás .html/.svg/.js)."""

    def test_magic_bytes_mandan(self):
        casos = [
            (b"\x89PNG\r\n\x1a\n" + b"x" * 8, "png"),
            (b"\xff\xd8\xff\xe0" + b"x" * 8, "jpg"),
            (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "webp"),
            (b"%PDF-1.4 hola", "pdf"),
            (b"GIF89a" + b"x" * 8, "gif"),
            (b"\x00\x00\x00\x18ftypheic" + b"x" * 8, "heic"),
        ]
        for data, esperado in casos:
            self.assertEqual(booking_referrals._sniff_referral(data), esperado, data[:8])

    def test_html_declarado_como_imagen_cae_a_bin(self):
        # el ataque real: <script> subido con mime image/png y filename .png —
        # los bytes no son PNG, el mime/filename NO se honran fuera del sniff
        # (text/html ni .html están en el allowlist) -> .bin, nunca ejecutable.
        html = b"<html><script>alert(1)</script></html>"
        self.assertIsNone(booking_referrals._sniff_referral(html))
        self.assertEqual(booking_referrals._ext_for(html, "text/html", "nota.html"), "bin")
        self.assertEqual(booking_referrals._ext_for(html, "image/png", "foto.png"), "png")
        # ...pero .png por mime con bytes html: png está en el allowlist — el
        # archivo se guarda .png y el navegador lo trata como imagen rota, no
        # como documento html (content-sniffing del lado nuestro, no del cliente)

    def test_sin_bytes_cae_al_allowlist_declarado(self):
        # mover un pending (bytes no en memoria): mime/extensión declarados
        # valen SOLO dentro del allowlist
        self.assertEqual(booking_referrals._ext_for(None, "application/pdf", None), "pdf")
        self.assertEqual(booking_referrals._ext_for(None, None, "orden.jpeg"), "jpeg")
        self.assertEqual(booking_referrals._ext_for(None, "text/html", "x.html"), "bin")
        self.assertEqual(booking_referrals._ext_for(None, "image/svg+xml", "x.svg"), "bin")
        self.assertEqual(booking_referrals._ext_for(None, None, None), "bin")

    def test_bytes_reales_ganan_al_mime_mentiroso(self):
        pdf = b"%PDF-1.7 contenido"
        self.assertEqual(booking_referrals._ext_for(pdf, "image/png", "foto.png"), "pdf")


class TestColisionMedicoAcotada(unittest.TestCase):
    """db_booking.approved_in_range + _turnos_medico_en_hora (optimización 2026-07-22).

    El chequeo de colisión de médico dejó de traer TODAS las aprobadas del
    histórico para acotar al minuto vía el índice estado+turno_dt. La semántica
    tiene que ser idéntica: encuentra la colisión del mismo médico en el mismo
    minuto y NADA fuera de ese minuto."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")

    def _aprobar(self, dni, medico, when):
        sid = db_booking.create_booking_request(dni, "Col", "Ision", "1980-01-01",
                                 "351-000-0000", "cardiologia", "hospital-pinamar")
        db_booking.approve(sid, when, medico_asignado=medico)
        return sid

    def test_rango_trae_solo_el_minuto(self):
        when = datetime(2030, 5, 6, 9, 30, tzinfo=booking_requests._ART)
        sid = self._aprobar("40100001", "Dra. Marcela Duette", when)
        en_minuto = db_booking.approved_in_range(when, when + timedelta(minutes=1))
        self.assertIn(sid, [r["id"] for r in en_minuto])
        # un minuto antes no lo incluye (rango [desde, hasta) semiabierto)
        otro = db_booking.approved_in_range(when - timedelta(minutes=1), when)
        self.assertNotIn(sid, [r["id"] for r in otro])

    def test_colision_mismo_medico_mismo_minuto(self):
        when = datetime(2030, 5, 6, 11, 0, tzinfo=booking_requests._ART)
        self._aprobar("40100002", "Dra. Marcela Duette", when)
        self.assertTrue(booking_capacity._doctor_appointment_busy("Dra. Marcela Duette", when))
        # otro médico a la misma hora NO es colisión
        self.assertFalse(booking_capacity._doctor_appointment_busy("Dr. Otro Nombre", when))
        # el mismo médico un minuto después tampoco
        self.assertFalse(
            booking_capacity._doctor_appointment_busy("Dra. Marcela Duette",
                                              when + timedelta(minutes=1)))

    def test_excluir_sid_no_choca_consigo_misma(self):
        when = datetime(2030, 5, 6, 12, 15, tzinfo=booking_requests._ART)
        sid = self._aprobar("40100003", "Dra. Marcela Duette", when)
        # excluyéndose a sí misma, no hay colisión (reprogramar el propio turno)
        self.assertFalse(
            booking_capacity._doctor_appointment_busy("Dra. Marcela Duette", when, exclude_sid=sid))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestTieneOrden(unittest.TestCase):
    """`tiene_orden` sale del DISCO, no de la columna (2026-07-19).

    Una solicitud vieja puede conservar `orden_path` apuntando a un archivo
    que ya no está (volumen recreado, fila sembrada sin adjunto). Cuando el
    payload decía tiene_orden=True igual, el panel pedía la imagen y pintaba
    el JSON del 404 DENTRO de la tarjeta — que es como se vio en producción.
    """

    def test_path_sin_archivo_no_promete_orden(self):
        r = booking_requests._admin_row(
            {"id": 999, "estado": "pendiente",
             "orden_path": "/tmp/no-existe-jamas-sana-turnos.jpg"})
        self.assertFalse(r["tiene_orden"])

    def test_path_con_archivo_si(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
            f.flush()
            r = booking_requests._admin_row(
                {"id": 998, "estado": "pendiente", "orden_path": f.name})
            self.assertTrue(r["tiene_orden"])

    def test_sin_path_no_tiene_orden(self):
        r = booking_requests._admin_row({"id": 997, "estado": "pendiente",
                                    "orden_path": None})
        self.assertFalse(r["tiene_orden"])

    def test_nunca_expone_la_ruta_en_disco(self):
        r = booking_requests._admin_row(
            {"id": 996, "estado": "pendiente", "orden_path": "/tmp/x.png"})
        self.assertNotIn("orden_path", r)

    def test_nunca_expone_la_llave_del_link_de_confirmacion(self):
        """`confirm_token` IS the patient's authentication on the reminder's
        confirm and calendar links (a secret in `personal_data.REGISTRY`); no
        staff screen reads it, so the queue row and every action's answer
        never carry it."""
        r = booking_requests._admin_row(
            {"id": 995, "estado": "aprobada", "confirm_token": "tok-secreto-del-paciente"})
        self.assertNotIn("confirm_token", r)
        self.assertNotIn("tok-secreto-del-paciente", json.dumps(r))

    def test_nunca_expone_la_llave_de_idempotencia_del_paciente(self):
        """`idem_key` is the patient wizard's hold token sent once with the
        booking POST (a secret in `personal_data.REGISTRY`); the server reads
        it from the database, no staff screen reads it from the row."""
        r = booking_requests._admin_row(
            {"id": 994, "estado": "pendiente", "idem_key": "hold-secreto-del-paciente"})
        self.assertNotIn("idem_key", r)
        self.assertNotIn("hold-secreto-del-paciente", json.dumps(r))


# ---- POST /api/requests: máximo de turnos activos por paciente ---------------

class TestLimiteTurnosActivos(unittest.TestCase):
    """D9/D10 booking limits end-to-end tests via the patient POST endpoint.

    D9: service-level turnos_activos_max
    D10: doctor-level override (resolved from slot_dia/slot_hora, not client-sent slot_medico_id)
    """

    # Un día futuro dentro del horizonte que `_agenda_slots` realmente
    # genera (tope: `_SLOTS_DAYS` días CON turnos desde hoy), y horas
    # dentro de la ventana que las agendas de abajo publican.
    # Cada prueba que elige turnos concretos reserva su PROPIO día: el
    # soft-lock de `slot_holds` es por (servicio, día, hora) y sobrevive a
    # la prueba que lo tomó, así que dos pruebas compartiendo día se
    # rechazan entre sí con `turno_tomado`.
    _DIA_OFFSET = 2
    _HORAS = ("09:00", "09:20", "09:40", "10:00", "10:20")

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self._orig_user = booking_requests._get_user
        self.addCleanup(config._reset_cache_para_tests)    # after the environment is put back
        _env.set_env(self, MAX_ACTIVE_REQUESTS="1")
        config._reset_cache_para_tests()
        self._dni = "3011" + str(zlib.crc32(self._testMethodName.encode()) % 10000).zfill(4)
        usuario = dict(_USER)
        usuario["dni"] = self._dni
        booking_requests._get_user = lambda h: dict(usuario)
        # Toda fila de catálogo que una prueba cree o pise se anota acá y se
        # revierte en tearDown: el catálogo es estado COMPARTIDO por todo el
        # módulo (db_catalog.catalog_upsert escribe en la base del run), y una
        # prueba que lo deja tocado rompe a las otras clases del archivo —
        # que es exactamente lo que pasaba con la primera versión de D9/D10.
        self._catalogo_backup = []

    def tearDown(self):
        for tipo, id_, doc in reversed(self._catalogo_backup):
            if doc is None:
                db_catalog.catalog_delete_row(tipo, id_)
            else:
                db_catalog.catalog_upsert(tipo, id_, doc)
        if self._catalogo_backup:
            from app.patient_web import catalog
            catalog.invalidate()
        booking_requests._get_user = self._orig_user

    def _catalogo(self, tipo, id_, doc):
        """Upsert de catálogo reversible (ver `_catalogo_backup`)."""
        previo = db_catalog.catalog_get(tipo, id_)
        self._catalogo_backup.append((tipo, id_, previo))
        db_catalog.catalog_upsert(tipo, id_, doc)
        config._reset_cache_para_tests()
        from app.patient_web import catalog
        catalog.invalidate()

    def _dia(self):
        return (datetime.now(tz.tzinfo()).date()
                + timedelta(days=getattr(self, "_offset", self._DIA_OFFSET))).isoformat()

    def _agenda(self, servicio_id=None):
        """Agenda que publica `self._HORAS` todos los días de la semana."""
        franja = {"dias": [0, 1, 2, 3, 4, 5, 6], "desde": "09:00",
                  "hasta": "10:40", "cada_min": 20}
        if servicio_id:
            franja["servicio"] = servicio_id
        return {"franjas": [franja]}

    def _pedir(self, servicio_id="cardiologia", hora=None, slot_medico_id=None):
        """POST a booking. Con `hora`, manda el turno concreto (slot_dia/
        slot_hora) — que es lo único que dispara la resolución del médico
        dueño del turno en el servidor."""
        body = {"servicio_id": servicio_id, "centro_slug": "hospital-pinamar"}
        if hora:
            body["slot_dia"] = self._dia()
            body["slot_hora"] = hora
        else:
            body["preferencia"] = "mañanas"
        if slot_medico_id:
            body["slot_medico_id"] = slot_medico_id
        return _CLIENT.request("POST", "/api/requests", json=body)

    def test_el_segundo_turno_del_mismo_servicio_se_rechaza_con_una_salida(self):
        """Band mode: site default limit (1) rejects 2nd."""
        self.assertEqual(self._pedir().status_code, 200)
        h = self._pedir()
        self.assertEqual(h.status_code, 409)
        self.assertEqual(h.json()["error"], "limite_turnos_activos")
        self.assertEqual(h.json()["limite"], 1)
        # soluciones.py: el rechazo lleva el próximo paso concreto, no un error.
        self.assertIn("/account#turnos", h.json()["accion"]["url"])
        self.assertIn("Mis turnos", h.json()["detalle"])
        # TASK-49 (26.10): «Mis turnos» no tiene «Cambiar horario» desde el
        # 30.08 — la salida es la que existe: cancelar el que tiene y pedir
        # uno nuevo. Ningún texto promete un cambio de horario.
        self.assertIn("cancel", h.json()["detalle"].lower())
        self.assertNotIn("cambiar el horario", h.json()["detalle"].lower())
        self.assertEqual(h.json()["accion"]["texto"], "Mis turnos")

    def test_otro_servicio_no_cuenta_contra_el_mismo_limite(self):
        """Each service has its own counter."""
        self.assertEqual(self._pedir("cardiologia").status_code, 200)
        self.assertEqual(self._pedir("control-salud").status_code, 200)

    def test_d9_service_limit_3_rechaza_cuarta(self):
        """D9: el máximo del SERVICIO (3) rechaza el cuarto turno."""
        self._offset = 2
        self._catalogo("servicios", "cardiologia",
                       {"nombre": "Cardiología", "cluster": "cardiologia",
                        "centro": "hospital-pinamar", "turnos_activos_max": 3})

        for i, hora in enumerate(self._HORAS[:3]):
            h = self._pedir(hora=hora)
            self.assertEqual(h.status_code, 200, f"turno {i + 1}: {h.json()}")

        h = self._pedir(hora=self._HORAS[3])
        self.assertEqual(h.status_code, 409, h.json())
        self.assertEqual(h.json()["error"], "limite_turnos_activos")
        self.assertEqual(h.json()["limite"], 3)

    def _servicio_y_medico_para_d10(self, max_medico=2, max_servicio=10):
        """Un servicio SIN agenda propia y un médico de la misma
        cluster/centro CON agenda: así el turno concreto que manda el
        paciente pertenece, en el catálogo, a ese médico — que es lo que el
        servidor resuelve para elegir el límite (`_resolve_slot_doctor`)."""
        # `_service_full` de este módulo lee el catálogo de archivo que
        # arma setUpModule (no la base), así que la cluster/centro con la
        # que el servidor empareja al médico se declara acá.
        servicio = {"id": "cardiologia", "nombre": "Cardiología",
                    "cluster": "cardiologia", "centro": "hospital-pinamar",
                    "turnos_activos_max": max_servicio}
        parche = patch.object(booking_requests, "_service_full",
                              side_effect=lambda sid: dict(servicio)
                              if sid == "cardiologia" else None)
        parche.start()
        self.addCleanup(parche.stop)
        self._catalogo("servicios", "cardiologia",
                       {"nombre": "Cardiología", "cluster": "cardiologia",
                        "centro": "hospital-pinamar",
                        "turnos_activos_max": max_servicio})
        self._catalogo("medicos", "dra-lim2",
                       {"nombre": "Ana", "apellido": "Limitada",
                        "cluster": "cardiologia", "centro": "hospital-pinamar",
                        "agenda": self._agenda("cardiologia"),
                        "turnos_activos_max": max_medico})

    def test_d10_doctor_limit_2_rechaza_tercera(self):
        """D10: el máximo del MÉDICO dueño del turno (2) gana sobre el del
        servicio (10), y el servidor lo resuelve del turno elegido — el
        cuerpo del pedido no dice qué médico es."""
        self._offset = 3
        self._servicio_y_medico_para_d10()

        for i, hora in enumerate(self._HORAS[:2]):
            h = self._pedir(hora=hora)
            self.assertEqual(h.status_code, 200, f"turno {i + 1}: {h.json()}")

        h = self._pedir(hora=self._HORAS[2])
        self.assertEqual(h.status_code, 409, h.json())
        self.assertEqual(h.json()["limite"], 2,
                         "tiene que frenar en el límite del médico (2), "
                         "no en el del servicio (10) ni en el del sitio (1)")

    def test_d10_slot_medico_id_ajeno_no_afloja_el_limite_del_dueno(self):
        """Un `slot_medico_id` inventado por el cliente, apuntando a un
        médico MÁS PERMISIVO, no puede aflojar el límite del médico que
        realmente es dueño del turno: el servidor ignora ese campo cuando
        el pedido trae un turno concreto."""
        self._offset = 4
        self._servicio_y_medico_para_d10()
        self._catalogo("medicos", "dr-suelto",
                       {"nombre": "Beto", "apellido": "Suelto",
                        "cluster": "cardiologia", "centro": "hospital-pinamar",
                        "turnos_activos_max": 30})

        for i, hora in enumerate(self._HORAS[:2]):
            h = self._pedir(hora=hora, slot_medico_id="dr-suelto")
            self.assertEqual(h.status_code, 200, f"turno {i + 1}: {h.json()}")

        h = self._pedir(hora=self._HORAS[2], slot_medico_id="dr-suelto")
        self.assertEqual(h.status_code, 409, h.json())
        self.assertEqual(h.json()["limite"], 2,
                         "el límite es el del médico dueño del turno (2), "
                         "no el del médico que mandó el cliente (30)")

    def test_admin_panel_create_bypasses_limit(self):
        """Admin path bypasses patient web limits."""
        h1 = self._pedir()
        self.assertEqual(h1.status_code, 200)

        h2 = self._pedir()
        self.assertEqual(h2.status_code, 409)

        sid = db_booking.create_booking_request(self._dni, "Test", "User", "1990-01-01",
                                        "123456789", "cardiologia", "hospital-pinamar", None)
        self.assertIsNotNone(sid)
        rec = db_booking.get_booking_request(sid)
        self.assertEqual(rec["dni"], self._dni)


