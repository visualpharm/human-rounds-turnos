"""An exhausted pool is a 503 «base de datos no disponible», never a bare 500.

`db._connect()` raises `DbUnavailable` when no pooled connection frees up within
`TURNOS_DB_POOL_TIMEOUT`. Most routes catch it and answer 503 with
`{"error": "base de datos no disponible"}`; a handler without its own try/except
(found under load: `GET /api/admin/patients`, which answered the plain text
«Internal Server Error») now gets the same answer from one app-level handler.
A route that does catch it keeps answering its own way.
"""
import os
import sys
import unittest

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")  # boot without Postgres
_env.process_defaults(TURNOS_FAKE_AI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import db  # noqa: E402
from app.core import deps  # noqa: E402


def _app():
    app = FastAPI()
    deps.install_db_unavailable_handler(app)

    @app.get("/uncaught")
    async def uncaught():
        raise db.DbUnavailable("pool de conexiones agotado")

    @app.get("/caught")
    async def caught():
        try:
            raise db.DbUnavailable("pool de conexiones agotado")
        except db.DbUnavailable:
            return JSONResponse({"error": "propio"}, status_code=503)

    return app


class TestDbUnavailableHandler(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(_app(), raise_server_exceptions=False)

    def test_an_uncaught_pool_exhaustion_is_the_standard_503(self):
        r = self.client.get("/uncaught")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.content, b'{"error": "base de datos no disponible"}')
        self.assertEqual(r.headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertEqual(r.headers["retry-after"], "3")
        self.assertEqual(r.headers["content-length"], str(len(r.content)))

    def test_the_reason_is_logged(self):
        with self.assertLogs("uvicorn.error", level="WARNING") as logged:
            self.client.get("/uncaught")
        self.assertIn("pool de conexiones agotado", logged.output[0])

    def test_a_route_that_catches_it_keeps_its_own_answer(self):
        r = self.client.get("/caught")
        self.assertEqual((r.status_code, r.json()), (503, {"error": "propio"}))

    def test_other_errors_are_still_500(self):
        app = _app()

        @app.get("/boom")
        async def boom():
            raise RuntimeError("bug")

        r = TestClient(app, raise_server_exceptions=False).get("/boom")
        self.assertEqual(r.status_code, 500)

    def test_the_real_app_installs_it(self):
        from app import main as composition
        self.assertIn(db.DbUnavailable, composition.app.exception_handlers)


if __name__ == "__main__":
    unittest.main()
