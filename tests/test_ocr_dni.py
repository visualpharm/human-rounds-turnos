"""test_ocr_dni.py — lectura del DNI por foto (ocr_dni.py) + domicilio en cuenta.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_ocr_dni.py"

Mismo patrón que test_puco.py: FAKE (TURNOS_FAKE_AI=1) para el OCR sin red; una
base dedicada para lo que persiste (si Postgres no está, se saltea).
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_FAKE_PUCO="1")

from app.core import db_accounts  # noqa: E402
from app.cuentas import ocr_dni     # noqa: E402
from app.cuentas import ocr_dni_router  # noqa: E402
from app.cuentas import accounts     # noqa: E402
from app.auth import turnstile   # noqa: E402
from app.core import config  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

turnstile.ENV_PATH = ""  # sin secret → gate apagado en los tests
config.ENV_PATH = ""  # idem: no leer el .env real del checkout de dev

# Un PNG de 1x1 válido (el modo FAKE no lo decodifica, pero el endpoint valida
# el data URL igual).
PNG_1x1 = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCA"
           "AAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_ocr", seed=lambda: db_accounts.get_user("__forzar_esquema__"))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


def _limpiar(case):
    ocr_dni._HITS.clear()
    _env.set_env(case, TURNOS_FAKE_AI="1", TURNOS_OCR_PER_HOUR=None)


# ---- normalización -----------------------------------------------------------

class TestNormalizacion(unittest.TestCase):
    def test_patient_from_ok(self):
        p = ocr_dni._patient_from({
            "es_dni": True, "dni": "30.123.456", "apellido": "perez",
            "nombres": "juan carlos", "sexo": "m", "fecha_nacimiento": "09/05/1980"})
        self.assertEqual(p["dni"], "30123456")     # sin puntos
        self.assertEqual(p["apellido"], "PEREZ")
        self.assertEqual(p["sexo"], "M")
        self.assertEqual(p["nacimiento"], "1980-05-09")  # dd/mm/yyyy → ISO

    def test_patient_from_es_dni_false(self):
        self.assertIsNone(ocr_dni._patient_from({"es_dni": False, "dni": "30123456"}))

    def test_patient_from_nada_util(self):
        self.assertIsNone(ocr_dni._patient_from({"sexo": "M"}))  # ni dni ni nombre

    def test_iso_ya_iso(self):
        self.assertEqual(ocr_dni._iso("1980-05-09"), "1980-05-09")

    def test_iso_invalida(self):
        self.assertIsNone(ocr_dni._iso("no es fecha"))

    def test_domicilio_from_ok(self):
        d = ocr_dni._domicilio_from({
            "es_dorso": True,
            "domicilio": {"calle": "av del libertador", "numero": "742",
                          "localidad": "pinamar", "provincia": "buenos aires",
                          "cp": "7167"},
            "texto": "Av del Libertador 742, Pinamar (7167)"})
        self.assertEqual(d["calle"], "AV DEL LIBERTADOR")
        self.assertEqual(d["numero"], "742")
        self.assertEqual(d["localidad"], "PINAMAR")
        self.assertEqual(d["cp"], "7167")
        self.assertTrue(d["texto"])

    def test_domicilio_from_vacio(self):
        self.assertIsNone(ocr_dni._domicilio_from({"es_dorso": True, "domicilio": {}}))

    def test_parse_data_url_ok(self):
        uri, mime = ocr_dni._parse_data_url(PNG_1x1)
        self.assertEqual(mime, "image/png")
        self.assertEqual(uri, PNG_1x1)

    def test_parse_data_url_no_imagen(self):
        uri, mime = ocr_dni._parse_data_url("data:text/plain;base64,aGk=")
        self.assertIsNone(uri)

    def test_parse_data_url_basura(self):
        self.assertEqual(ocr_dni._parse_data_url("no soy un data url"), (None, None))

    # ---- bbox del retrato (para la foto de perfil) ----

    def test_bbox_ok(self):
        b = ocr_dni._bbox_from({"es_dni": True, "foto": {"x": 0.05, "y": 0.3, "w": 0.24, "h": 0.52}})
        self.assertEqual(b, {"x": 0.05, "y": 0.3, "w": 0.24, "h": 0.52})

    def test_bbox_recorta_al_borde(self):
        b = ocr_dni._bbox_from({"es_dni": True, "foto": {"x": 0.9, "y": 0.9, "w": 0.5, "h": 0.5}})
        self.assertAlmostEqual(b["w"], 0.1, places=3)
        self.assertAlmostEqual(b["h"], 0.1, places=3)

    def test_bbox_degenerado_es_none(self):
        self.assertIsNone(ocr_dni._bbox_from({"es_dni": True, "foto": {"x": 0.5, "y": 0.5, "w": 0.001, "h": 0.4}}))
        self.assertIsNone(ocr_dni._bbox_from({"es_dni": True, "foto": {"x": 1.2, "y": 0.5, "w": 0.2, "h": 0.4}}))
        self.assertIsNone(ocr_dni._bbox_from({"es_dni": True, "foto": None}))
        self.assertIsNone(ocr_dni._bbox_from({"es_dni": True, "foto": {"x": "no", "y": 0, "w": 0.2, "h": 0.2}}))
        self.assertIsNone(ocr_dni._bbox_from({"es_dni": False, "foto": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}}))


# ---- endpoint (FAKE) ---------------------------------------------------------

class TestEndpoint(unittest.TestCase):
    def setUp(self):
        _limpiar(self)
        app = FastAPI()
        app.include_router(ocr_dni_router.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

    def test_frente_fake(self):
        response = self.client.post("/api/ocr-dni", json={"lado": "frente", "imageDataUrl": PNG_1x1})
        j = response.json()
        self.assertTrue(j["ok"])
        self.assertEqual(j["patient"]["dni"], "30123456")
        self.assertEqual(j["patient"]["sexo"], "M")
        self.assertTrue(j["patient"]["nacimiento"])

    def test_dorso_fake(self):
        response = self.client.post("/api/ocr-dni", json={"lado": "dorso", "imageDataUrl": PNG_1x1})
        j = response.json()
        self.assertTrue(j["ok"])
        self.assertIn("localidad", j["domicilio"])
        self.assertEqual(j["domicilio"]["localidad"], "PINAMAR")

    def test_imagen_invalida_400(self):
        response = self.client.post("/api/ocr-dni", json={"lado": "frente", "imageDataUrl": "chau"})
        self.assertEqual(response.status_code, 400)

    def test_no_es_la_ruta(self):
        self.assertEqual(self.client.post("/api/otra-cosa", json={}).status_code, 404)

    def test_get_no_maneja(self):
        self.assertEqual(self.client.get("/api/ocr-dni").status_code, 405)

    def test_rate_limit(self):
        _env.set_env(self, TURNOS_OCR_PER_HOUR="2")
        for _ in range(2):
            self.client.post("/api/ocr-dni", json={"lado": "frente", "imageDataUrl": PNG_1x1})
        response = self.client.post("/api/ocr-dni", json={"lado": "frente", "imageDataUrl": PNG_1x1})
        self.assertEqual(response.status_code, 429)


# ---- domicilio en la cuenta (persiste) --------------------------------------

class TestDomicilioCuenta(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base")
        self._dni = "40222333"
        try:
            db_accounts.update_user  # noqa: B018
        except AttributeError:  # pragma: no cover
            self.skipTest("db sin actualizar_usuario")

    def _crear(self, domicilio):
        return accounts._register_core({
            "dni": self._dni, "password": "secreta1", "nombre": "Ana",
            "apellido": "Gomez", "email": "ana@ejemplo.com", "domicilio": domicilio}, "127.0.0.1")

    def test_registro_persiste_domicilio(self):
        result = self._crear({"calle": "AV LIBERTADOR", "numero": "742",
                         "localidad": "PINAMAR", "cp": "7167",
                         "provincia": "BUENOS AIRES"})
        self.assertEqual(result.status, 200)
        u = db_accounts.get_user(self._dni)
        self.assertIsNotNone(u)
        dom = u.get("domicilio") or {}
        self.assertEqual(dom.get("localidad"), "PINAMAR")
        self.assertEqual(dom.get("cp"), "7167")

    def test_norm_domicilio_vacio_es_none(self):
        self.assertIsNone(accounts._norm_address({}))
        self.assertIsNone(accounts._norm_address({"calle": "  "}))

    def test_norm_domicilio_limpia(self):
        d = accounts._norm_address({"calle": "  Av X 5  ", "localidad": "Pinamar",
                                     "cp": "7167", "basura": "x"})
        self.assertEqual(d["calle"], "Av X 5")
        self.assertEqual(d["localidad"], "Pinamar")
        self.assertNotIn("basura", d)


if __name__ == "__main__":
    unittest.main()
