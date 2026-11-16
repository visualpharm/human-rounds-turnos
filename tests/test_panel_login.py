"""test_panel_login.py — panel del gate de /admin: Humor de guardia / Consejos
de uso + analytics del experimento (panel_login.py + db.py + reports.py).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_*.py"
         (desde la raíz — SIEMPRE discover, ver CONTRIBUTING.md)

Mismo patrón que test_busquedas.py: base de test dedicada
`sana_turnos_test_panel_login`; sin Postgres accesible, las pruebas con base se
saltean pero el contrato "el gate nunca se rompe por analytics" se prueba
igual apuntando DATABASE_URL a un puerto muerto.

Invariantes clave:
  - Un ítem del manifest con nivel "inappropriate" JAMÁS se sirve al cliente.
  - El beacon valida evento/variante/sid/tab/item y responde 204 aun con la
    DB caída (log no-op) — el login no depende de la analytics.
"""

import os
import sys
import json
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Skipped only in the public source cut: no CLAUDE.md there and no humor images.
_HUMOR_ABSENT_IN_CUT = (not os.path.exists(os.path.join(ROOT, "CLAUDE.md"))
                        and not os.path.exists(os.path.join(ROOT, "static", "humor", "humor.json")))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db  # noqa: E402
from app.core import db_analytics  # noqa: E402
from app.core import db_schema  # noqa: E402
from app.auth import turnstile    # noqa: E402
from app.core import config  # noqa: E402
turnstile.ENV_PATH = ""  # no leer el .env del dev (módulo compartido — no pisar _secret)
config.ENV_PATH = ""  # idem: no leer el .env real del checkout de dev
from app.auth import panel_login  # noqa: E402
from app.auth import panel_login_router  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_panel_login",
                          seed=lambda: db_analytics.log_panel_login("cafecafe", "humor", "exposicion"))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


def _truncate():
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE login_panel_events")


def _client():
    app = FastAPI()
    app.include_router(panel_login_router.router)
    from app.core import deps as _early_deps
    _early_deps.install_early_response(app)  # the app's answer of a dependency
    return TestClient(app)


# ---- GET /api/login-panel ----------------------------------------------------

class TestContenido(unittest.TestCase):
    def setUp(self):
        panel_login._HITS.clear()
        self._orig_json = panel_login.HUMOR_JSON
        panel_login._CACHE.update(mtime=None, items=[])
        self.client = _client()

    def tearDown(self):
        panel_login.HUMOR_JSON = self._orig_json
        panel_login._CACHE.update(mtime=None, items=[])
        self.client.close()

    def _manifest(self, items):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"items": items}, f)
        f.close()
        self.addCleanup(os.unlink, f.name)
        panel_login.HUMOR_JSON = f.name

    def test_inappropriate_jamas_se_sirve(self):
        self._manifest([
            {"id": "ok-1", "file": "appropriate/a.jpg", "nivel": "appropriate", "texto": "t1", "alt": ""},
            {"id": "medio", "file": "borderline/b.jpg", "nivel": "borderline", "texto": "t2", "alt": ""},
            {"id": "feo", "file": "inappropriate/c.jpg", "nivel": "inappropriate", "texto": "t3", "alt": ""},
        ])
        data = self.client.get("/api/login-panel").json()
        ids = [it["id"] for it in data["humor"]]
        self.assertEqual(ids, ["ok-1", "medio"])
        self.assertNotIn("feo", json.dumps(data))

    def test_src_apunta_a_static(self):
        self._manifest([{"id": "x", "file": "appropriate/x.jpg", "nivel": "appropriate", "texto": "t", "alt": "a"}])
        self.assertEqual(self.client.get("/api/login-panel").json()["humor"][0]["src"], "/static/humor/appropriate/x.jpg")

    def test_manifest_ausente_degrada_a_solo_consejos(self):
        panel_login.HUMOR_JSON = "/no/existe/humor.json"
        data = self.client.get("/api/login-panel").json()
        self.assertEqual(data["humor"], [])
        self.assertTrue(len(data["consejos"]) >= 3)

    # The humor images stay out of the public source cut (.gitattributes): there
    # the panel degrades to tips only, and these two checks have nothing to read.
    # CLAUDE.md stays out of the cut too, so the private tree never skips them.
    @unittest.skipIf(_HUMOR_ABSENT_IN_CUT, "the public source cut ships without the humor images")
    def test_manifest_real_del_repo_es_valido(self):
        # el manifest commiteado: todos publicables, archivos presentes en disco
        data = self.client.get("/api/login-panel").json()
        self.assertTrue(len(data["humor"]) >= 10)
        for it in data["humor"]:
            f = os.path.join(ROOT, "static", "humor", it["src"].split("/static/humor/")[1])
            self.assertTrue(os.path.exists(f), f)
            # texto = transcripción del texto EN la imagen; un meme puramente
            # visual no tiene, pero siempre debe traer alt para el <img>
            self.assertTrue(it["texto"] or it["alt"], it["src"])

    @unittest.skipIf(_HUMOR_ABSENT_IN_CUT, "the public source cut ships without the humor images")
    def test_manifest_real_ingles_los_originales_existen(self):
        # El responsable del producto, 2026-09-18: el meme en español en la interfaz inglesa es
        # inaceptable — en EN se sirve file_original (meme fuente en inglés).
        # Cada original anotado tiene que existir en disco.
        en = panel_login._humor_items("en")
        self.assertTrue(len(en) >= 10)
        for it in en:
            f = os.path.join(ROOT, "static", "humor", it["src"].split("/static/humor/")[1])
            self.assertTrue(os.path.exists(f), f)

    def test_ingles_sirve_el_original_y_alt_en(self):
        self._manifest([
            {"id": "m", "file": "appropriate/m-es.jpg", "file_original": "appropriate/m.jpg",
             "nivel": "appropriate", "texto": "t", "alt": "a es", "alt_en": "a en"},
        ])
        es = panel_login.contenido_payload("es")["humor"][0]
        en = panel_login.contenido_payload("en")["humor"][0]
        self.assertEqual(es["src"], "/static/humor/appropriate/m-es.jpg")
        self.assertEqual(es["alt"], "a es")
        self.assertEqual(en["src"], "/static/humor/appropriate/m.jpg")
        self.assertEqual(en["alt"], "a en")

    def test_ingles_sin_original_degrada_al_mismo_archivo(self):
        self._manifest([
            {"id": "m2", "file": "appropriate/m2.jpg", "nivel": "appropriate",
             "texto": "t", "alt": "a"},
        ])
        en = panel_login.contenido_payload("en")["humor"][0]
        self.assertEqual(en["src"], "/static/humor/appropriate/m2.jpg")
        self.assertEqual(en["alt"], "a")

    def test_ingles_tampoco_sirve_inappropriate(self):
        self._manifest([
            {"id": "ok", "file": "appropriate/ok.jpg", "file_original": "appropriate/ok-en.jpg",
             "nivel": "appropriate", "texto": "t", "alt": ""},
            {"id": "feo", "file": "inappropriate/feo.jpg", "nivel": "inappropriate",
             "texto": "t", "alt": ""},
        ])
        ids = [it["id"] for it in panel_login.contenido_payload("en")["humor"]]
        self.assertEqual(ids, ["ok"])

    def test_consejos_ingles_mismos_ids_copy_en_ingles(self):
        es = panel_login.contenido_payload("es")["consejos"]
        en = panel_login.contenido_payload("en")["consejos"]
        self.assertEqual([c["id"] for c in es], [c["id"] for c in en])
        joined = json.dumps(en)
        self.assertNotIn("veredicto", joined)
        self.assertNotIn("Deriv", joined)
        # el copy EN mantiene los <b> ancla que el cliente inyecta confiando
        # en la única fuente server-side
        self.assertIn("<b>", en[0]["texto"])

    def test_router_pasa_el_idioma_del_visitante(self):
        self._manifest([
            {"id": "m", "file": "appropriate/m-es.jpg", "file_original": "appropriate/m.jpg",
             "nivel": "appropriate", "texto": "t", "alt": "a"},
        ])
        with mock.patch.object(panel_login_router.i18n, "resolve_lang", return_value="en"):
            en = self.client.get("/api/login-panel").json()["humor"][0]
        self.assertEqual(en["src"], "/static/humor/appropriate/m.jpg")


# ---- POST /api/login-panel/event --------------------------------------------

class TestEvento(unittest.TestCase):
    def setUp(self):
        panel_login._HITS.clear()
        self.client = _client()

    def tearDown(self):
        self.client.close()

    def _evento(self, body, ip="203.0.113.5"):
        return self.client.post("/api/login-panel/event", json=body,
                                headers={"X-Forwarded-For": ip})

    def test_evento_valido_204(self):
        self.assertEqual(self._evento({"evento": "exposicion", "variante": "humor", "sid": "cafe1234"}).status_code, 204)

    def test_evento_desconocido_400(self):
        self.assertEqual(self._evento({"evento": "hackeo", "variante": "humor", "sid": "cafe1234"}).status_code, 400)

    def test_variante_desconocida_400(self):
        self.assertEqual(self._evento({"evento": "tab", "variante": "otros", "sid": "cafe1234"}).status_code, 400)

    def test_sid_invalido_400(self):
        for sid in ("", "MAYUS123", "zz", "x" * 40):
            self.assertEqual(self._evento({"evento": "tab", "variante": "humor", "sid": sid}).status_code, 400, sid)

    def test_item_con_forma_rara_400(self):
        self.assertEqual(self._evento({"evento": "tab", "variante": "humor", "sid": "cafe1234", "tab": "humor", "item": "../etc/passwd"}).status_code, 400)

    def test_evento_item_murio_con_el_scroller(self):
        # "item" era el click en "Otro"; sin scroller ya no es un evento válido
        # (item quedó como CAMPO de exposicion/tab).
        self.assertEqual(self._evento({"evento": "item", "variante": "humor", "sid": "cafe1234", "tab": "humor", "item": "tres-pastillas"}).status_code, 400)

    def test_db_caida_no_rompe_el_beacon(self):
        _env.set_env(self, DATABASE_URL="postgresql://x:x@127.0.0.1:59999/nada")
        db_schema._ensured.clear()
        self.addCleanup(db_schema._ensured.clear)   # runs after the environment is back (LIFO)
        self.assertEqual(self._evento({"evento": "login_ok", "variante": "consejos", "sid": "cafe1234", "ms": 4200}).status_code, 204)

    def test_rate_limit_responde_204_sin_persistir(self):
        _env.set_env(self, TURNOS_LOGIN_PANEL_PER_DAY="2")
        ip = "10.9.9.9"
        for _ in range(2):
            self.assertEqual(self._evento({"evento": "tab", "variante": "humor", "sid": "cafe1234"}, ip=ip).status_code, 204)
        self.assertEqual(self._evento({"evento": "tab", "variante": "humor", "sid": "cafe1234"}, ip=ip).status_code, 204)  # al tope: 204 igual, sin guardar


# ---- agregación (db_analytics.stats_panel_login) ---------------------------------------

class TestStats(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        panel_login._HITS.clear()
        _truncate()

    def test_la_parte_de_sesiones_que_choca_con_un_error(self):
        # aaaa0001 entra directo; aaaa0002 falla dos veces y entra;
        # aaaa0003 solo falla. La exposicion del gate no es un intento.
        db_analytics.log_panel_login("aaaa0001", "humor", "exposicion", tab="humor", item="tres-pastillas")
        db_analytics.log_panel_login("aaaa0001", "humor", "login_ok", tab="humor", ms=8000)
        db_analytics.log_panel_login("aaaa0002", "humor", "exposicion", tab="humor", item="tres-pastillas")
        db_analytics.log_panel_login("aaaa0002", "humor", "login_error")
        db_analytics.log_panel_login("aaaa0002", "humor", "login_error")
        db_analytics.log_panel_login("aaaa0002", "humor", "login_ok", tab="consejos", ms=4000)
        db_analytics.log_panel_login("aaaa0003", "consejos", "login_error")

        s = db_analytics.stats_panel_login(30)
        self.assertEqual(s, {"sesiones": 3, "logins": 2, "errores": 3,
                             "sesiones_con_error": 2, "ms_mediana_login": 6000.0})

    def test_vacio_no_rompe(self):
        s = db_analytics.stats_panel_login(30)
        self.assertEqual(s, {"sesiones": 0, "logins": 0, "errores": 0,
                             "sesiones_con_error": 0, "ms_mediana_login": None})


if __name__ == "__main__":
    unittest.main()
