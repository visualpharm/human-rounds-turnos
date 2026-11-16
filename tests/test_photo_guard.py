"""test_photo_guard.py — the shared upload gate (core/photo_guard.py).

Correr:  python3 -m unittest discover -s tests -p "test_photo_guard.py"

Sin red y sin base: cada imagen se sintetiza acá con Pillow (nunca una foto
real). Lo que se prueba es el DECODE real, no el rótulo: un archivo que dice
JPEG en la cabecera y está roto adentro tiene que caer, una foto con EXIF de
orientación tiene que guardarse derecha, y una cabecera que declara 36 MP
tiene que rechazarse antes de decodificar nada.
"""

import io
import os
import struct
import sys
import unittest
import zlib
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from PIL import Image  # noqa: E402
from app.core import photo_guard  # noqa: E402


# ---- synthetic fixtures (no real photos, ever) ------------------------------

def _jpeg(w=120, h=80, exif_bytes=None):
    img = Image.new("RGB", (w, h), (198, 44, 44))
    for x in range(0, w, 6):
        for y in range(h):
            img.putpixel((x, y), (40, 40, 160))
    buf = io.BytesIO()
    if exif_bytes is None:
        img.save(buf, format="JPEG")
    else:
        img.save(buf, format="JPEG", exif=exif_bytes)
    return buf.getvalue()


def _png(w=60, h=40, alpha=False):
    img = Image.new("RGBA" if alpha else "RGB", (w, h),
                    (12, 128, 240, 60) if alpha else (12, 128, 240))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _webp():
    img = Image.new("RGB", (96, 32), (10, 190, 10))
    buf = io.BytesIO()
    img.save(buf, format="WEBP")
    return buf.getvalue()


def _heic():
    """Real HEIC bytes (pillow-heif), skipped when the decoder is missing."""
    pillow_heif = __import__("pillow_heif")
    pillow_heif.register_heif_opener()
    img = Image.new("RGB", (140, 90), (240, 170, 20))
    buf = io.BytesIO()
    img.save(buf, format="HEIF")
    return buf.getvalue()


def _header_png(w, h):
    """Valid PNG signature + IHDR + a stub IDAT: Pillow's LAZY open parses the
    header and stops, so the pixel gate fires before any decode work."""
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00" * 16))
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat


def _decoded(data):
    with Image.open(io.BytesIO(data)) as im:
        return im.format, im.size


class TestNormalizeAccepts(unittest.TestCase):
    """What comes out is always a real, upright, bounded JPEG."""

    def _ok(self, raw, max_dim=600):
        out, ext, mime = photo_guard.normalize(raw, max_dim=max_dim)
        self.assertEqual((ext, mime), ("jpg", "image/jpeg"))
        self.assertEqual(out[:3], b"\xff\xd8\xff")
        return out

    def test_jpeg_decodifica(self):
        out = self._ok(_jpeg())
        fmt, size = _decoded(out)
        self.assertEqual(fmt, "JPEG")
        self.assertEqual(size, (120, 80))

    def test_png_se_rencodea_a_jpeg(self):
        out = self._ok(_png())
        self.assertEqual(_decoded(out)[0], "JPEG")

    def test_png_con_transparencia_no_negro(self):
        # alpha 60 sobre blanco: el píxel tiene que quedar claro, no negro
        out = self._ok(_png(alpha=True))
        with Image.open(io.BytesIO(out)).convert("RGB") as im:
            r, g, b = im.getpixel((5, 5))
        self.assertGreater(r + g + b, 200, "transparencia aplanada sobre blanco")

    def test_webp_decodifica(self):
        self._ok(_webp())

    def test_exif_orientacion_6_queda_derecha(self):
        # orientación 6 = girar 90° al mostrar: sin exif_transpose la foto
        # guardada queda acostada (el bug clásico de foto de celular)
        exif = Image.Exif()
        exif[274] = 6
        out = self._ok(_jpeg(80, 120, exif_bytes=exif.tobytes()))
        self.assertEqual(_decoded(out)[1], (120, 80), "80x120 + EXIF 6 se guarda 120x80")

    def test_lado_largo_se_acota(self):
        out = self._ok(_jpeg(1900, 100), max_dim=600)
        self.assertEqual(_decoded(out)[1], (600, 32))

    def test_salida_menor_que_la_entrada_gigante(self):
        # 1600px de largo: la salida tiene que quedar lejos del techo de bytes
        out = self._ok(_jpeg(2400, 1600), max_dim=1600)
        self.assertLess(len(out), photo_guard.MAX_BYTES)


class TestNormalizeRejects(unittest.TestCase):
    def _reject(self, raw, status=400):
        with self.assertRaises(photo_guard.PhotoRejected) as cm:
            photo_guard.normalize(raw)
        self.assertEqual(cm.exception.status, status)
        return cm.exception.error

    def test_vacia(self):
        self._reject(b"")

    def test_texto_no_imagen(self):
        err = self._reject(b"esto no es una imagen, es texto plano")
        self.assertIn("JPG", err["error"])

    def test_svg_se_rechaza(self):
        # SVG = XML con texto y posiblemente scripts: nunca un "formato de foto"
        svg = (b"<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'>"
               b"<script>alert(1)</script></svg>")
        err = self._reject(svg)
        self.assertIn("JPG", err["error"])

    def test_jpeg_truncado_magic_valido(self):
        # cabecera JPEG real + cuerpo cortado: el magic pasa, el decode no
        err = self._reject(_jpeg()[:80])
        self.assertIn("dañada", err["error"])

    def test_png_roto(self):
        err = self._reject(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        self.assertIn("dañada", err["error"])

    def test_exe_con_cabecera_mentirosa(self):
        err = self._reject(b"\xff\xd8\xff" + os.urandom(4096))
        self.assertIn("dañada", err["error"])

    def test_sobre_5mb(self):
        big = _jpeg() + b"\x00" * (photo_guard.MAX_BYTES + 1)
        self._reject(big, status=413)

    def test_megapixeles_sobre_el_techo(self):
        # 6000x6000 = 36 MP: por encima del techo de 24, por debajo del de
        # Pillow — este camino ES nuestro, no el de Pillow
        err = self._reject(_header_png(6000, 6000))
        self.assertIn("megapíxeles", err["error"])

    def test_bomba_de_descompresion_de_pillow(self):
        # 30000x30000 = 900 MP: dispara el propio tripwire de Pillow; tiene
        # que salir nuestro remedio, no un 500
        err = self._reject(_header_png(30000, 30000))
        self.assertIn("megapíxeles", err["error"])


class TestHeic(unittest.TestCase):
    """HEIC entra sólo con decodificador real; sin él, el remedio exacto."""

    def test_sniff_heic(self):
        brand = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 8
        self.assertEqual(photo_guard.sniff(brand), "heic")
        otro = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8
        self.assertIsNone(photo_guard.sniff(otro))

    def test_sin_decodificador_remedio_concreto(self):
        # aunque HAYA decodificador instalado: el contrato sin él queda fijado
        original = photo_guard.heif_available
        photo_guard.heif_available = lambda: False
        try:
            with self.assertRaises(photo_guard.PhotoRejected) as cm:
                photo_guard.normalize(_heic())
        finally:
            photo_guard.heif_available = original
        self.assertEqual(cm.exception.status, 400)
        self.assertIn("HEIC", cm.exception.error["error"])
        self.assertIn("JPG", cm.exception.error["error"])

    def test_registration_failure_is_actionable_and_next_upload_can_retry(self):
        import pillow_heif
        raw = _heic()
        with patch.object(photo_guard, '_HEIF_OPENED', False):
            with patch.object(pillow_heif, 'register_heif_opener', side_effect=RuntimeError('codec unavailable')):
                with self.assertRaises(photo_guard.PhotoRejected) as cm:
                    photo_guard.decode(raw)
            self.assertEqual(cm.exception.status, 400)
            self.assertIn('JPG', cm.exception.error['error'])
            self.assertFalse(photo_guard._HEIF_OPENED)
            self.assertEqual(photo_guard.decode(raw).size, (140, 90))
            self.assertTrue(photo_guard._HEIF_OPENED)

    @unittest.skipUnless(photo_guard.heif_available(), "pillow-heif no instalado")
    def test_heic_con_decodificador_sale_jpeg(self):
        out, ext, mime = photo_guard.normalize(_heic(), max_dim=600)
        self.assertEqual((ext, mime), ("jpg", "image/jpeg"))
        fmt, size = _decoded(out)
        self.assertEqual((fmt, size), ("JPEG", (140, 90)))


class TestSniffUnchanged(unittest.TestCase):
    """The claim-only sniff stays stable for its network-edge callers."""

    def test_familias(self):
        self.assertEqual(photo_guard.sniff(b"\x89PNG\r\n\x1a\n" + b"x"), "png")
        self.assertEqual(photo_guard.sniff(b"\xff\xd8\xff" + b"x"), "jpg")
        riff = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"x"
        self.assertEqual(photo_guard.sniff(riff), "webp")
        self.assertIsNone(photo_guard.sniff(b""))


if __name__ == "__main__":
    unittest.main()
