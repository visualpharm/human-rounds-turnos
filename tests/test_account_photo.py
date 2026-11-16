"""test_account_photo.py — foto de perfil del paciente (account_photo.py).

Correr:  python3 -m unittest discover -s tests -p "test_account_photo.py"

Sin red y sin base: la identidad (account_photo._user) se monkeypatchea a un
usuario fijo y el directorio de fotos se apunta a un tmpdir. Las imágenes son
SINTÉTICAS (generadas con Pillow acá mismo, nunca una foto real) y la subida
pasa por el decode real de core/photo_guard: lo que queda en disco es un JPEG
derecho y acotado, no los bytes que llegaron.
"""

import io
import os
import sys
import base64
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PIL import Image  # noqa: E402
from app.cuentas import account_photo   # noqa: E402
from app.cuentas import account_photo_router  # noqa: E402
from app.core import photo_guard  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _img_bytes(fmt, size=(120, 80), color=(12, 128, 240)):
    img = Image.new("RGB", size, color)
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _heic_bytes():
    import pillow_heif
    pillow_heif.register_heif_opener()
    img = Image.new("RGB", (140, 90), (240, 170, 20))
    buf = io.BytesIO()
    img.save(buf, format="HEIF")
    return buf.getvalue()


# Imágenes reales para subir (el guard las normaliza: en disco queda JPG).
PNG = _img_bytes("PNG")
JPG = _img_bytes("JPEG")
WEBP = _img_bytes("WEBP")
# Cabecera JPEG válida con el cuerpo cortado: el magic pasa, el decode no.
JPG_TRUNADO = JPG[:80]
SVG = (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>")
NOT_IMG = b"esto no es una imagen, es texto plano"


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


class _FotoBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="cuenta-fotos-")
        self._orig_dir = account_photo.PHOTOS_DIR
        self._orig_usuario = account_photo._user
        account_photo.PHOTOS_DIR = self._tmp
        account_photo._user = lambda handler: {"dni": "30123456"}
        app = FastAPI()
        app.include_router(account_photo_router.router)
        from app.core import deps as _early_deps
        _early_deps.install_early_response(app)  # the app's answer of a dependency
        self.client = TestClient(app)

    def tearDown(self):
        account_photo.PHOTOS_DIR = self._orig_dir
        account_photo._user = self._orig_usuario
        self.client.close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _post(self, raw_or_b64):
        data = raw_or_b64 if isinstance(raw_or_b64, str) else _b64(raw_or_b64)
        return self.client.post("/api/account/photo", json={"data_b64": data})


class TestRutaYAuth(_FotoBase):
    def test_otra_ruta_no_maneja(self):
        self.assertEqual(self.client.get("/api/otra-cosa").status_code, 404)

    def test_sin_sesion_401(self):
        account_photo._user = lambda handler: None
        response = self.client.get("/api/account/photo")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json().get("error"), "no_autenticado")

    def test_dni_basura_401(self):
        account_photo._user = lambda handler: {"dni": "no-es-dni"}
        self.assertEqual(self.client.get("/api/account/photo").status_code, 401)

    def test_metodo_no_soportado(self):
        self.assertEqual(self.client.put("/api/account/photo").status_code, 405)


class TestSubidaYServir(_FotoBase):
    def test_get_sin_foto_404(self):
        response = self.client.get("/api/account/photo")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json().get("error"), "sin_foto")

    def test_subir_png_ok(self):
        response = self._post(PNG)
        self.assertEqual(response.json(), {"ok": True})
        # el guard re-encoda: en disco queda SIEMPRE un jpg real
        self.assertTrue(os.path.isfile(os.path.join(self._tmp, "30123456.jpg")))

    def test_servir_devuelve_jpeg_decodificable(self):
        self._post(PNG)
        response = self.client.get("/api/account/photo")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")
        with Image.open(io.BytesIO(response.content)) as im:
            self.assertEqual(im.format, "JPEG")
            self.assertEqual(im.size, (120, 80))
        # dato personal: cachea en el navegador del dueño, nunca en cache compartido
        self.assertIn("private", response.headers.get("cache-control", ""))

    def test_subir_persiste_en_disco_entre_clientes(self):
        # persistencia real: otro cliente (otra "sesión") sobre el MISMO
        # directorio sigue viendo la foto — lo guardado es del disco, no de
        # la memoria del request
        self._post(PNG)
        app2 = FastAPI()
        app2.include_router(account_photo_router.router)
        from app.core import deps as _early_deps
        _early_deps.install_early_response(app2)  # the app's answer of a dependency
        with TestClient(app2) as client2:
            response = client2.get("/api/account/photo")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/jpeg")

    def test_disco_lleno_devuelve_507_no_crashea(self):
        import unittest.mock as mock
        with mock.patch("app.cuentas.account_photo.open", side_effect=OSError(28, "No space left on device"), create=True):
            response = self._post(PNG)
        self.assertEqual(response.status_code, 507)
        self.assertEqual(response.json().get("ok"), False)

    def test_un_usuario_un_archivo(self):
        self._post(PNG)
        self._post(WEBP)  # reemplaza: queda sólo el jpg nuevo
        self.assertFalse(os.path.exists(os.path.join(self._tmp, "30123456.png")))
        self.assertTrue(os.path.isfile(os.path.join(self._tmp, "30123456.jpg")))
        self.assertEqual(len(os.listdir(self._tmp)), 1)

    def test_webp_ok(self):
        response = self._post(WEBP)
        self.assertEqual(response.json(), {"ok": True})
        self.assertTrue(os.path.isfile(os.path.join(self._tmp, "30123456.jpg")))

    def test_prefijo_data_url_se_ignora(self):
        response = self._post("data:image/png;base64," + _b64(PNG))
        self.assertEqual(response.json(), {"ok": True})

    def test_jpeg_truncado_400_con_remedio(self):
        response = self._post(JPG_TRUNADO)
        self.assertEqual(response.status_code, 400)
        self.assertIn("dañada", response.json().get("error", ""))

    def test_svg_400(self):
        response = self._post(SVG)
        self.assertEqual(response.status_code, 400)

    def test_no_imagen_400(self):
        self.assertEqual(self._post(NOT_IMG).status_code, 400)

    def test_vacia_400(self):
        self.assertEqual(self.client.post("/api/account/photo", json={"data_b64": ""}).status_code, 400)

    def test_demasiado_grande_413(self):
        # el techo de bytes se controla ANTES de mirar el contenido
        big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (account_photo.MAX_BYTES + 1)
        self.assertEqual(self._post(big).status_code, 413)

    def test_heic_sin_decodificador_remedio_no_500(self):
        if not photo_guard.heif_available():
            self.skipTest("pillow-heif no instalado (hace falta para fabricar el HEIC)")
        original = photo_guard.heif_available
        photo_guard.heif_available = lambda: False
        try:
            response = self._post(_heic_bytes())
        finally:
            photo_guard.heif_available = original
        self.assertEqual(response.status_code, 400)
        self.assertIn("HEIC", response.json().get("error", ""))

    @unittest.skipUnless(photo_guard.heif_available(), "pillow-heif no instalado")
    def test_heic_con_decodificador_queda_jpg(self):
        response = self._post(_heic_bytes())
        self.assertEqual(response.json(), {"ok": True})
        self.assertTrue(os.path.isfile(os.path.join(self._tmp, "30123456.jpg")))

    def test_quitar(self):
        self._post(PNG)
        response = self.client.delete("/api/account/photo")
        self.assertEqual(response.json(), {"ok": True})
        self.assertIsNone(account_photo._existing("30123456"))

    def test_quitar_sin_foto_es_idempotente(self):
        self.assertEqual(self.client.delete("/api/account/photo").json(), {"ok": True})


class TestProbeYGravatar(_FotoBase):
    """Fuente del avatar: subida > gravatar del email > ninguna. El gravatar se
    monkeypatchea (sin red): _gravatar(email) devuelve un PNG fijo para el email
    con cuenta y None para el resto."""

    GRAV_PNG = b"\x89PNG\r\n\x1a\n" + b"\x11" * 40

    def setUp(self):
        super().setUp()
        self._orig_grav = account_photo._gravatar
        account_photo._gravatar = lambda email: (
            (self.GRAV_PNG, "png", "image/png") if (email or "").strip().lower() == "con@grav.com" else None
        )
        # usuario con email que TIENE gravatar
        account_photo._user = lambda handler: {"dni": "30123456", "email": "con@grav.com"}

    def tearDown(self):
        account_photo._gravatar = self._orig_grav
        super().tearDown()

    def _probe(self):
        return self.client.get("/api/account/photo?probe=1").json()

    def test_probe_gravatar_cuando_no_hay_subida(self):
        self.assertEqual(self._probe(), {"ok": True, "source": "gravatar"})

    def test_probe_upload_gana_a_gravatar(self):
        self._post(JPG)
        self.assertEqual(self._probe(), {"ok": True, "source": "upload"})

    def test_probe_none_sin_gravatar(self):
        account_photo._user = lambda handler: {"dni": "30123456", "email": "sin@grav.com"}
        self.assertEqual(self._probe(), {"ok": True, "source": "none"})

    def test_servir_cae_al_gravatar(self):
        response = self.client.get("/api/account/photo")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(response.content, self.GRAV_PNG)
        self.assertIn("private", response.headers.get("cache-control", ""))

    def test_subida_pisa_al_gravatar_al_servir(self):
        self._post(WEBP)
        response = self.client.get("/api/account/photo")
        # la subida se normaliza a JPEG; el gravatar crudo se sirve tal cual
        self.assertEqual(response.headers["content-type"], "image/jpeg")

    def test_servir_404_sin_subida_ni_gravatar(self):
        account_photo._user = lambda handler: {"dni": "30123456", "email": "sin@grav.com"}
        self.assertEqual(self.client.get("/api/account/photo").status_code, 404)


class TestSniff(unittest.TestCase):
    def test_png(self):
        self.assertEqual(account_photo._sniff(PNG), ("png", "image/png"))

    def test_jpg(self):
        self.assertEqual(account_photo._sniff(JPG), ("jpg", "image/jpeg"))

    def test_webp(self):
        self.assertEqual(account_photo._sniff(WEBP), ("webp", "image/webp"))

    def test_desconocido(self):
        self.assertIsNone(account_photo._sniff(NOT_IMG))


class TestNavAvatarFallback(unittest.TestCase):
    def test_nav_probea_antes_de_pedir_la_foto(self):
        with open(os.path.join(ROOT, "static/platform/nav.mjs"), encoding="utf-8") as f:
            source = f.read()
        self.assertIn('fetch("/api/account/photo?probe=1"', source)
        self.assertIn(
            '<span class="stznav-cuenta-av noimg"><img alt="" width="40" height="40">',
            source)
        self.assertNotIn('<img src="/api/account/photo', source)


if __name__ == "__main__":
    unittest.main()
