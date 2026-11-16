"""Exercise the real native ASGI composition through a socket-bound server.

The pure policy tests never enter the terminal route and production smoke
runs only after deployment. This suite closes that gap for public asset
headers, encoded path traversal, and the same source/secret/data denial
matrix used by smoke. It boots the real composed app (`_livehttp.LiveServer`,
lifespan off) so no background service starts, while retaining the exact
request transport (a real uvicorn socket, not an ASGI test transport).

Native-migration final wave (2026-08-27): the legacy stdlib transport
(`server.py`, `routers/legacy_http_transport.py`) was retired — the
composed FastAPI app is now the only executable topology, and
`_livehttp.LiveServer` boots THAT.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_http_server.py" -v
"""

import os
import json
import http.client
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")  # importar/correr sin exigir Postgres
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")
# el gate pre-lanzamiento (site_auth.py) sólo corre con las DOS seteadas —
# apagado acá para que las garantías de esta suite no dependan del .env local.
_env.process_set(TURNOS_BASIC_AUTH_USER=None, TURNOS_BASIC_AUTH_PASS=None)

from app.core import static_fingerprint  # noqa: E402
from _livehttp import LiveServer, LiveServerMixin as _LiveServerMixin  # noqa: E402


class TestAssetsEstaticos(_LiveServerMixin, unittest.TestCase):
    """(a) fonts/icons/animations/favicon/logo — status + content-type, y
    supervivencia a un cambio de PACK_CHAIN."""

    def test_favicon(self):
        status, ctype, body = self._get("/static/favicon.svg")
        self.assertEqual(status, 200)
        self.assertIn("svg", ctype)
        self.assertTrue(body)

    def test_fuente_woff2(self):
        status, ctype, body = self._get("/static/fonts/ubuntu-400-latin.woff2")
        self.assertEqual(status, 200)
        self.assertIn("font", ctype)
        self.assertTrue(body)

    def test_hoja_de_fuentes_css(self):
        status, ctype, _ = self._get("/static/fonts/fonts.css")
        self.assertEqual(status, 200)
        self.assertIn("css", ctype)

    def test_icono(self):
        status, ctype, body = self._get("/static/icons/cardiologia.png")
        self.assertEqual(status, 200)
        self.assertIn("png", ctype)
        self.assertTrue(body)

    def test_animacion(self):
        status, ctype, body = self._get("/static/animations/rescan-document.json")
        self.assertEqual(status, 200)
        self.assertIn("json", ctype)
        self.assertTrue(body)

    def test_logo(self):
        status, ctype, body = self._get("/logo.png")
        self.assertEqual(status, 200)
        self.assertIn("png", ctype)
        self.assertTrue(body)

    def test_los_assets_sobreviven_un_cambio_de_pack_chain(self):
        # /static/ vive en el árbol de la app (ROOT/static/), servido directo
        # por el fallback estático de SimpleHTTPRequestHandler — nunca pasa
        # por sitedata.path()/data_packs.py, así que PACK_CHAIN no debería
        # tocarlo. No hace falta esperar/verificar data_packs.py u otro
        # agente para esta garantía: sólo env + HTTP.
        _env.set_env(self, PACK_CHAIN="global,ar-b-pinamar")
        for path in ("/static/favicon.svg", "/static/fonts/fonts.css",
                    "/static/icons/cardiologia.png", "/logo.png"):
            with self.subTest(path=path):
                status, _, _ = self._get(path)
                self.assertEqual(status, 200, path)


class TestModuleAssetAliasSafety(_LiveServerMixin, unittest.TestCase):
    """A real GET must not expose raw module source on registry loss."""

    def test_broken_manifest_raw_alias_is_no_store_404(self):
        with patch.object(static_fingerprint, "_MODULE_ASSETS_CACHE", frozenset()):
            conn = http.client.HTTPConnection("127.0.0.1", self._port, timeout=5)
            try:
                conn.request("GET", "/modules/intake/entrevista.js")
                response = conn.getresponse()
                body = response.read()
                self.assertEqual(response.status, 404)
                self.assertEqual(response.getheader("Cache-Control"), "no-store")
                self.assertNotIn(b"function renderShell", body)
            finally:
                conn.close()


class TestFingerprintedAssetHeadBehavior(_LiveServerMixin, unittest.TestCase):
    """A real HEAD gets the same immutable/no-store contract as GET."""

    def _head(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self._port, timeout=5)
        try:
            conn.request("HEAD", path)
            response = conn.getresponse()
            body = response.read()
            return response.status, response.getheader("Cache-Control"), body
        finally:
            conn.close()

    def test_current_module_fingerprint_stale_twin_and_passthrough_head(self):
        current = static_fingerprint.module_url("modules/intake/entrevista.js")
        stale = current.replace(current.split(".")[-2], "deadbeef")
        self.assertEqual(self._head(current), (200, "public, max-age=31536000, immutable", b""))
        self.assertEqual(self._head(stale)[0], 404)
        self.assertEqual(self._head(stale)[1], "no-store")
        self.assertEqual(self._head("/static/format.js")[0], 404)


class TestPathTraversal(_LiveServerMixin, unittest.TestCase):
    """(b) recorridos de directorio sobre HTTP real, con formas URL-encoded.
    server.py:3193-3233 realpath-checkea /webdata/<path> y /data/<path>
    contra las raíces permitidas antes de servir — estos casos dan 403
    limpio (el realpath-check está bien; el punto es dejarlo trabado)."""

    _INTENTOS_403 = [
        "/webdata/../server.py",
        "/webdata/../.env",
        "/webdata/%2e%2e/server.py",
        "/webdata/%2e%2e%2fserver.py",
        "/webdata/%2E%2E/server.py",             # mayúsculas
        "/webdata/%252e%252e/server.py",          # doble-encoded (%25 = '%')
        "/webdata/....//server.py",               # dot-dot-slash
        "/webdata/a/../../server.py",
        "/webdata/a/..%2f..%2fserver.py",         # mixto: literal + encoded
        "/data/../server.py",
        "/data/../.env",
        "/data/%2e%2e%2fserver.py",
        "/data/%2E%2E%2Fserver.py",
        "/data/....//server.py",
        "/data/ordenes/",                          # el volumen PHI ni listado
        "/data/ordenes/1.png",                      # ni un nombre real dentro
        "/data/cuenta-fotos/30123456.jpg",
    ]

    def test_recorridos_de_directorio_dan_403(self):
        for path in self._INTENTOS_403:
            with self.subTest(path=path):
                status, _, _ = self._get(path)
                self.assertEqual(status, 403, path)

    def test_archivo_real_dentro_de_webdata_si_sirve(self):
        # sanity check del propio test: si esto empezara a dar 403 también,
        # los 403 de arriba podrían estar escondiendo un guard roto (todo
        # 403 SIEMPRE) en vez de uno que sólo bloquea el escape.
        status, ctype, body = self._get("/webdata/servicios.json")
        self.assertEqual(status, 200)
        self.assertIn("json", ctype)
        self.assertTrue(body)

    def test_byte_nulo_no_corta_la_conexion(self):
        """A percent-encoded NUL is denied with 403, not a dropped socket."""
        for path in ("/webdata/x%00.py", "/data/x%00.py",
                     "/x%00.js", "/static/%00.js"):
            status, _, _ = self._get(path)
            self.assertEqual(status, 403, path)


class TestSyntheticPublicDataBehavior(unittest.TestCase):
    """Tier 6's public-path regression uses no real checkout pack content."""

    def setUp(self):
        from app.core import config
        from app.core import data_packs
        self.data_packs = data_packs
        self.config = config
        self.root = tempfile.mkdtemp(prefix="tier6-public-static-", dir=ROOT)
        self.patches = [
            patch.object(data_packs, "_PACKS_DIR", self.root),
            patch.object(config, "_db_overrides", return_value={}),
        ]
        for item in self.patches:
            item.start()
        _env.set_env(self, PACK_CHAIN="fixture", TURNOS_PRIVATE_PACKS_DIR=None,
                     TURNOS_REQUIRED_PACKS=None,
                     TURNOS_ENV_FILE=os.path.join(self.root, "missing.env"))
        os.makedirs(os.path.join(self.root, "fixture"))
        with open(os.path.join(self.root, "fixture", "pack.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "fixture"}, f)
        with open(os.path.join(self.root, "fixture", "asset.json"), "w", encoding="utf-8") as f:
            json.dump({"synthetic": True}, f)
        config._reset_cache_para_tests()
        data_packs.invalidate()

    def tearDown(self):
        self.config._reset_cache_para_tests()
        self.data_packs.invalidate()
        for item in reversed(self.patches):
            item.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_public_data_route_keeps_stdlib_conditional_behavior(self):
        """A private-root feature must not replace the public default path."""
        import http.client
        with LiveServer() as base_url:
            port = int(base_url.rsplit(":", 1)[1])
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.request("GET", "/webdata/asset.json")
                first = conn.getresponse()
                self.assertEqual(first.status, 200)
                self.assertEqual(first.read(), b'{"synthetic": true}')
                modified = first.getheader("Last-Modified")
                self.assertTrue(modified)
            finally:
                conn.close()
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                conn.request("GET", "/webdata/asset.json",
                             headers={"If-Modified-Since": modified})
                second = conn.getresponse()
                second.read()
                self.assertEqual(second.status, 304)
            finally:
                conn.close()


class TestMatrizDeSeguridad(_LiveServerMixin, unittest.TestCase):
    """(c) la matriz de scripts/smoke_prod.py (SECURITY + un subconjunto de
    PAGES_200), corrida contra un server LOCAL real — que ande en local no
    dice nada de que ande en prod, pero si ni siquiera anda en local no hace
    falta esperar al deploy para saberlo."""

    _DENEGADOS = {
        # Source, tests and documents are not on the static allowlist (2026-11-12,
        # tests/test_static_allowlist.py): 404 whether or not the file exists, so the
        # answer does not map the checkout. Dot segments and the data volume stay 403.
        "/app.py": 404,
        "/core/db.py": 404,
        "/auth/admin_auth.py": 404,
        "/templates/about.html": 404,
        "/test/e2e/lib/fixtures.mjs": 404,
        "/.env": 403,
        "/CLAUDE.md": 404,
        "/AGENTS.md": 404,
        "/requirements.txt": 404,
        "/Dockerfile": 404,
        "/docs/": 404,
        "/data/ordenes/": 403,
        "/api/requests?dni=12345678": 401,   # IDOR: sin sesión jamás lista PHI
    }
    _PAGINAS_200 = ["/", "/about.html", "/static/favicon.svg"]

    def test_matriz_de_seguridad(self):
        for path, esperado in self._DENEGADOS.items():
            with self.subTest(path=path):
                status, _, _ = self._get(path)
                self.assertEqual(status, esperado, path)

    def test_paginas_clave_200(self):
        for path in self._PAGINAS_200:
            with self.subTest(path=path):
                status, _, _ = self._get(path)
                self.assertEqual(status, 200, path)


if __name__ == "__main__":
    unittest.main()
