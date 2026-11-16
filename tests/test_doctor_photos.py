"""test_doctor_photos.py — pruebas de doctor_photos.py: POST/DELETE/GET
/api/admin/doctors/<id>/photo (admin-gated) + GET pública /doctor-photo/<id>.

Correr: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_doctor_photos.py" -v
(o la suite completa, ver CONTRIBUTING.md — OJO: `python3 -m unittest test.test_x`
no resuelve, el paquete `test` de la stdlib tapa el directorio local).

Corre sobre la app nativa compuesta (TestClient → doctor_photos_router.py;
la transport legacy `doctor_photos.handle()` se retiró con la topología
pre-refactor, 2026-08-30). Sigue el patrón de test_admin_content.py: una base
Postgres de test dedicada (`sana_turnos_test_doctor_photos`, para correr en
paralelo sin pisar las otras suites) — doctor_photos.py valida el id del
médico contra catalog.admin_list("medicos"), que exige DB en modo estricto.
TURNOS_WEBDATA_DIR y doctor_photos.PHOTOS_DIR se apuntan a directorios temporales
propios: ningún test toca webdata/ ni data/medico-fotos/ reales.
"""

import base64
import io
from PIL import Image
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")

from app.core import db_catalog  # noqa: E402
from app.auth import admin_auth        # noqa: E402
from app.patient_web import catalog          # noqa: E402
from app.patient_web import doctor_photos      # noqa: E402
from app.patient_web import doctor_photos_router  # noqa: E402

from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_doctorphotos", seed=lambda: db_catalog.catalog_list("medicos"))

# PNG real de 1x1 (magic bytes válidos) — happy path de la subida.
_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY"
    "42YAAAAASUVORK5CYII="
)


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


_MEDICOS = {"medicos": [
    {"id": "med-1", "nombre": "Marcelo Aguirre", "especialidad": "Cardiología", "centro": "centro-a"},
]}

_CENTROS = {"centros": [
    {"slug": "centro-a", "name": "Centro A", "address": "Calle 1"},
]}


def _write_fixtures(webdir):
    os.makedirs(webdir, exist_ok=True)
    with open(os.path.join(webdir, "medicos.json"), "w", encoding="utf-8") as f:
        json.dump(_MEDICOS, f)
    with open(os.path.join(webdir, "centros-salud.json"), "w", encoding="utf-8") as f:
        json.dump(_CENTROS, f)


def _admin_headers():
    return {"Cookie": "st_admin=" + admin_auth._mint_token(admin_auth._admin_user(), "admin")}


_client = TestClient(app_module.app)


def _req(method, path, body=None, auth=True):
    headers = _admin_headers() if auth else {}
    if method == "GET":
        return _client.get(path, headers=headers)
    if method == "DELETE":
        return _client.delete(path, headers=headers)
    return _client.post(path, json=body, headers=headers)


def _b64(data):
    return base64.b64encode(data).decode("ascii")


def _public_files():
    return sorted(name for name in os.listdir(doctor_photos.PHOTOS_DIR) if not name.startswith('.'))


class MedicoFotosTestCase(unittest.TestCase):
    """Apunta TURNOS_WEBDATA_DIR (datos.py) a un webdata/ propio con un médico
    fixture, y doctor_photos.PHOTOS_DIR a un directorio temporal — nunca toca
    webdata/ ni data/medico-fotos/ reales."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self._reset_table()
        self._tmp = tempfile.mkdtemp(prefix="medico-fotos-test-")
        _write_fixtures(os.path.join(self._tmp, "webdata"))
        _env.set_env(self, TURNOS_WEBDATA_DIR=os.path.join(self._tmp, "webdata"))
        catalog._JSON_CACHE.clear()
        catalog.invalidate()

        self._fotos_tmp = tempfile.mkdtemp(prefix="medico-fotos-store-")
        self._orig_fotos_dir = doctor_photos.PHOTOS_DIR
        doctor_photos.PHOTOS_DIR = self._fotos_tmp
        self._fotos_cen_tmp = tempfile.mkdtemp(prefix="centro-fotos-store-")
        self._orig_fotos_cen_dir = doctor_photos.CENTER_PHOTOS_DIR
        doctor_photos.CENTER_PHOTOS_DIR = self._fotos_cen_tmp

    def tearDown(self):
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        shutil.rmtree(self._tmp, ignore_errors=True)
        doctor_photos.PHOTOS_DIR = self._orig_fotos_dir
        shutil.rmtree(self._fotos_tmp, ignore_errors=True)
        doctor_photos.CENTER_PHOTOS_DIR = self._orig_fotos_cen_dir
        shutil.rmtree(self._fotos_cen_tmp, ignore_errors=True)
        self._reset_table()

    def _reset_table(self):
        if not HAVE_DB:
            return
        import psycopg
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
            with c.cursor() as cur:
                cur.execute("TRUNCATE catalog")


# ---- subir -------------------------------------------------------------

class TestSubir(MedicoFotosTestCase):
    def test_sube_y_queda_servible(self):
        r = _req("POST", "/api/admin/doctors/med-1/photo",
                 body={"filename": "foto.png", "mime": "image/png", "data_b64": _b64(_PNG_1PX)})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        url = r.json()["foto_url"]
        self.assertTrue(url.startswith("/doctor-photo/med-1?v="))
        self.assertEqual(_public_files(), ["med-1.webp"])
        self.assertEqual(doctor_photos.original_bytes('med-1'), _PNG_1PX)

        r2 = _req("GET", url, auth=False)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.headers.get("content-type"), "image/webp")
        with Image.open(io.BytesIO(r2.content)) as image:
            self.assertEqual(image.size, (512,512))
        self.assertIn("immutable", r2.headers.get("cache-control", ""))

    def test_disco_lleno_devuelve_507_no_crashea(self):
        import unittest.mock as mock
        with mock.patch("app.patient_web.doctor_photos.open", side_effect=OSError(28, "No space left on device"), create=True):
            r = _req("POST", "/api/admin/doctors/med-1/photo",
                     body={"filename": "foto.png", "mime": "image/png", "data_b64": _b64(_PNG_1PX)})
        self.assertEqual(r.status_code, 507)
        self.assertFalse(r.json()["ok"])

    def test_reemplazo_borra_la_extension_anterior(self):
        _req("POST", "/api/admin/doctors/med-1/photo",
             body={"filename": "a.png", "mime": "image/png", "data_b64": _b64(_PNG_1PX)})
        buffer = io.BytesIO()
        Image.new('RGB', (30,50), (50,60,70)).save(buffer,format='JPEG')
        jpeg_bytes = buffer.getvalue()
        _req("POST", "/api/admin/doctors/med-1/photo",
             body={"filename": "b.jpg", "mime": "image/jpeg", "data_b64": _b64(jpeg_bytes)})
        self.assertEqual(_public_files(), ["med-1.webp"])
        self.assertEqual(doctor_photos.original_bytes('med-1'), jpeg_bytes)

    def test_magic_bytes_rechaza_mime_mentiroso(self):
        # el cliente dice "image/png" pero los bytes no son una imagen.
        r = _req("POST", "/api/admin/doctors/med-1/photo",
                 body={"filename": "foto.png", "mime": "image/png",
                       "data_b64": _b64(b"esto no es una foto, es texto plano")})
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.json())
        self.assertEqual(_public_files(), [])

    def test_supera_20mb_413(self):
        big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (doctor_photos.MAX_BYTES + 10)
        r = _req("POST", "/api/admin/doctors/med-1/photo",
                 body={"filename": "grande.png", "mime": "image/png", "data_b64": _b64(big)})
        self.assertEqual(r.status_code, 413)

    def test_medico_desconocido_404(self):
        r = _req("POST", "/api/admin/doctors/no-existe/photo",
                 body={"filename": "foto.png", "mime": "image/png", "data_b64": _b64(_PNG_1PX)})
        self.assertEqual(r.status_code, 404)

    def test_sin_sesion_401(self):
        r = _req("POST", "/api/admin/doctors/med-1/photo", auth=False,
                 body={"filename": "foto.png", "mime": "image/png", "data_b64": _b64(_PNG_1PX)})
        self.assertEqual(r.status_code, 401)


# ---- quitar --------------------------------------------------------------

class TestQuitar(MedicoFotosTestCase):
    def test_borra_el_archivo(self):
        _req("POST", "/api/admin/doctors/med-1/photo",
             body={"filename": "a.png", "mime": "image/png", "data_b64": _b64(_PNG_1PX)})
        r = _req("DELETE", "/api/admin/doctors/med-1/photo")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertEqual(_public_files(), [])

    def test_medico_desconocido_404(self):
        r = _req("DELETE", "/api/admin/doctors/no-existe/photo")
        self.assertEqual(r.status_code, 404)

    def test_sin_sesion_401(self):
        r = _req("DELETE", "/api/admin/doctors/med-1/photo", auth=False)
        self.assertEqual(r.status_code, 401)


# ---- GET pública /doctor-photo/<id> ----------------------------------------

class TestGetPublica(MedicoFotosTestCase):
    def test_404_sin_foto(self):
        r = _req("GET", "/doctor-photo/med-1", auth=False)
        self.assertEqual(r.status_code, 404)

    def test_ruta_desconocida_no_es_de_este_router(self):
        # La ruta pública es literal por entidad: cualquier otra cosa es del
        # terminal, no de este módulo.
        paths = {getattr(route, "path", None)
                 for route in doctor_photos_router.router.routes}
        self.assertNotIn("/api/otra-cosa", paths)
        self.assertIn("/{entity}-photo/{eid}", paths)


# ---- vista previa del editor (GET /api/admin/doctors/<id>/photo) -----------

class TestPreview(MedicoFotosTestCase):
    def test_sin_upload_devuelve_iniciales(self):
        r = _req("GET", "/api/admin/doctors/med-1/photo")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertFalse(r.json()["has_upload"])
        self.assertEqual(r.json()["style"], "")
        self.assertTrue(r.json()["ini"])

    def test_con_upload_devuelve_el_corte_del_atlas(self):
        # el círculo de 64 px toma su fragmento del atlas compartido, nunca el
        # retrato subido entero (puede pesar varios MB)
        _req("POST", "/api/admin/doctors/med-1/photo",
             body={"filename": "a.png", "mime": "image/png", "data_b64": _b64(_PNG_1PX)})
        r = _req("GET", "/api/admin/doctors/med-1/photo")
        self.assertTrue(r.json()["has_upload"])
        self.assertIn("/avatar-sprite/", r.json()["style"])
        self.assertNotIn("/doctor-photo/", r.json()["style"])

    def test_sin_atlas_cae_al_retrato_entero(self):
        # degradar, no fallar: sin atlas el círculo usa el retrato subido
        _req("POST", "/api/admin/doctors/med-1/photo",
             body={"filename": "a.png", "mime": "image/png", "data_b64": _b64(_PNG_1PX)})
        with mock.patch("app.patient_web.site_doctors._avatar_sprite", return_value={}):
            r = _req("GET", "/api/admin/doctors/med-1/photo")
        self.assertIn("/doctor-photo/med-1", r.json()["style"])

    def test_medico_desconocido_404(self):
        r = _req("GET", "/api/admin/doctors/no-existe/photo")
        self.assertEqual(r.status_code, 404)

    def test_sin_sesion_401(self):
        r = _req("GET", "/api/admin/doctors/med-1/photo", auth=False)
        self.assertEqual(r.status_code, 401)


# ---- centros (the product owner, 2026-07-24: "for new center, there should be a photo") ---

class TestCentros(MedicoFotosTestCase):
    """El mismo mecanismo sirve a las dos entidades. Lo que cambia: la carpeta,
    la ruta pública y que un centro NO tiene respaldo (ni sprite ni iniciales)."""

    def _subir(self, slug="centro-a"):
        return _req("POST", f"/api/admin/centers/{slug}/photo",
                    {"filename": "c.png", "mime": "image/png",
                     "data_b64": base64.b64encode(_PNG_1PX).decode()})

    def test_sube_en_su_propia_carpeta_y_queda_servible(self):
        r = self._subir()
        self.assertEqual(r.status_code, 200, r.json())
        # carpeta propia: la foto del centro NO cae entre las de los médicos
        self.assertEqual(os.listdir(doctor_photos.CENTER_PHOTOS_DIR), ["centro-a.jpg"])
        self.assertEqual(os.listdir(doctor_photos.PHOTOS_DIR), [])
        self.assertTrue(r.json()["foto_url"].startswith("/center-photo/centro-a?v="))

        url = r.json()["foto_url"].split("?")[0]
        g = _req("GET", url, auth=False)
        self.assertEqual(g.status_code, 200)
        self.assertEqual(g.headers['content-type'], 'image/jpeg')
        with Image.open(io.BytesIO(g.content)) as image:
            self.assertEqual(image.size, (1, 1))
            self.assertEqual(image.format, 'JPEG')
            image.load()

    def test_preview_sin_upload_no_inventa_iniciales(self):
        r = _req("GET", "/api/admin/centers/centro-a/photo")
        self.assertEqual(r.status_code, 200, r.json())
        self.assertFalse(r.json()["has_upload"])
        self.assertEqual(r.json()["style"], "")
        self.assertEqual(r.json()["ini"], "")   # un centro no tiene avatar de iniciales

    def test_preview_con_upload_devuelve_el_corte_del_atlas(self):
        # el círculo de 64 px toma su fragmento del atlas compartido, nunca la
        # foto subida (puede pesar varios MB; product owner, 2026-10-28)
        self._subir()
        r = _req("GET", "/api/admin/centers/centro-a/photo")
        self.assertTrue(r.json()["has_upload"])
        style = r.json()["style"]
        self.assertIn("/avatar-sprite/", style)
        self.assertNotIn("/center-photo/", style)

    def test_preview_sin_atlas_cae_a_la_foto_entera(self):
        # degradar, no fallar: si el atlas no se puede armar, el círculo usa la
        # foto subida, centrada y recortada
        self._subir()
        with mock.patch("app.patient_web.site_doctors.center_circle_style", return_value=""):
            r = _req("GET", "/api/admin/centers/centro-a/photo")
        style = r.json()["style"]
        self.assertIn("/center-photo/centro-a", style)
        self.assertIn("background-size:cover", style)

    def test_quitar(self):
        self._subir()
        r = _req("DELETE", "/api/admin/centers/centro-a/photo")
        self.assertEqual(r.status_code, 200, r.json())
        self.assertEqual(os.listdir(doctor_photos.CENTER_PHOTOS_DIR), [])

    def test_centro_inexistente_404(self):
        r = self._subir("no-existe")
        self.assertEqual(r.status_code, 404)
        self.assertIn("centro", r.json()["error"])

    def test_sin_sesion_no_sube(self):
        r = _req("POST", "/api/admin/centers/centro-a/photo",
                 {"filename": "c.png", "mime": "image/png",
                  "data_b64": base64.b64encode(_PNG_1PX).decode()}, auth=False)
        self.assertIn(r.status_code, (401, 403))
        self.assertEqual(os.listdir(doctor_photos.CENTER_PHOTOS_DIR), [])


if __name__ == "__main__":
    unittest.main()
