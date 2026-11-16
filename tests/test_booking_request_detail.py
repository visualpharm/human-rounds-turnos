"""test_booking_request_detail.py — ruteo nativo de /request/<id> en
patient_site_router._request_redirect (la transport legacy patient_web/site_*.py.handle()
se retiró con la topología pre-refactor, 2026-08-30).

La página de detalle propia (patient_web/site_*.py:_page_request_detail) se retiró
el 2026-08-01 (the product owner: "everything should be in the acc list") — todo lo que
mostraba (estado, cancelar, avisame si se libera antes) vive ahora en cada
tarjeta de /my-requests (my-requests.js). /request/<id> queda como un simple
302 a /my-requests para no romper bookmarks/emails viejos, sea cual sea el id.

Correr:  python3 -m unittest discover -s tests -p "test_*.py"   (desde la raíz)
"""
import asyncio
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _env  # noqa: E402
_env.process_set(TURNOS_ENV_FILE="/dev/null")
_env.process_defaults(DATABASE_URL="")
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from starlette.requests import Request  # noqa: E402

from app.patient_web import patient_site_router  # noqa: E402


def _request(path):
    return Request({
        "type": "http", "method": "GET", "scheme": "http", "path": path,
        "raw_path": path.encode("utf-8"), "query_string": b"",
        "headers": [], "client": ("127.0.0.1", 1), "server": ("testserver", 80),
    })


class TestRedirectRuteo(unittest.TestCase):
    def _redirigir(self, resto):
        return asyncio.run(patient_site_router._request_redirect(
            _request(f"/request/{resto}"), rest=resto))

    def test_get_solicitud_numerico_redirige_a_mis_solicitudes(self):
        r = self._redirigir("123")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/my-requests")

    def test_get_solicitud_no_numerico_tambien_redirige(self):
        r = self._redirigir("abc")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/my-requests")

    def test_get_solicitud_sin_id_tambien_redirige(self):
        r = self._redirigir("")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/my-requests")

    def test_post_no_lo_maneja_hospital(self):
        # POST /api/requests lo maneja booking_requests.py; la ruta nativa de
        # /request/<id> se registra GET-only, así que un POST nunca entra.
        rutas = [r for r in patient_site_router.router.routes
                 if getattr(r, "path", None) == "/request/{rest:path}"]
        self.assertEqual(len(rutas), 1)
        self.assertEqual(set(rutas[0].methods), {"GET"})


if __name__ == "__main__":
    unittest.main()
