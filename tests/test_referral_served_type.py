"""La orden de derivación que el paciente sube se le muestra al personal con el
tipo que dicen sus BYTES, nunca con el que declaró el paciente.

Antes la orden se guardaba con el `mime` del cuerpo del pedido y la ruta del
panel (`/api/admin/order/<id>` y `/api/admin/my-appointments/<id>/order`) lo
devolvía tal cual: un archivo con `<script>` declarado `text/html` se ejecutaba
en el origen del panel con la sesión del médico o del administrador
(XSS almacenado, revisión de seguridad 2026-11-04). El nombre del archivo en
disco ya se sanea por magia (`_sniff_referral`); acá se prueba lo que sale por
la red.
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.modules.turnos import booking_referrals  # noqa: E402
from app.modules.turnos import doctor_appointments  # noqa: E402

_HTML = b"<html><script>fetch('/api/admin/me')</script></html>"
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_PDF = b"%PDF-1.4\n" + b"0" * 32


class _Orden(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)

    def _fila(self, data, declarado):
        ruta = os.path.join(self._dir.name, "orden.bin")
        with open(ruta, "wb") as f:
            f.write(data)
        return {"orden_path": ruta, "orden_mime": declarado}


class TestRutaDeAdministracion(_Orden):
    def _servida(self, data, declarado):
        status, is_json, _body, mime, headers = booking_referrals._referral_stream_response(
            self._fila(data, declarado))
        self.assertEqual((status, is_json), (200, False))
        return mime, headers

    def test_html_declarado_no_sale_como_html(self):
        mime, headers = self._servida(_HTML, "text/html")
        self.assertNotIn("html", mime.lower())
        self.assertEqual(mime, "application/octet-stream")
        self.assertTrue(headers["Content-Disposition"].startswith("attachment"))
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_svg_declarado_no_sale_como_svg(self):
        mime, _ = self._servida(b"<svg xmlns='http://www.w3.org/2000/svg' onload='x()'/>", "image/svg+xml")
        self.assertEqual(mime, "application/octet-stream")

    def test_png_con_mime_falso_sale_como_png(self):
        mime, headers = self._servida(_PNG, "text/html")
        self.assertEqual(mime, "image/png")
        self.assertNotIn("attachment", headers.get("Content-Disposition", ""))

    def test_pdf_sale_como_pdf(self):
        mime, _ = self._servida(_PDF, "application/pdf")
        self.assertEqual(mime, "application/pdf")


class TestRutaDelMedico(_Orden):
    def test_html_declarado_no_sale_como_html(self):
        mime, _data = doctor_appointments._referral_file(self._fila(_HTML, "text/html"))
        self.assertEqual(mime, "application/octet-stream")

    def test_jpeg_con_mime_falso_sale_como_jpeg(self):
        mime, _data = doctor_appointments._referral_file(
            self._fila(b"\xff\xd8\xff\xe0" + b"\x00" * 16, "text/html"))
        self.assertEqual(mime, "image/jpeg")


if __name__ == "__main__":
    unittest.main()
