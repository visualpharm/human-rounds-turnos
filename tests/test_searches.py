"""test_searches.py — logging de búsquedas de pacientes (searches.py + db.py).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_*.py"
         (desde la raíz — SIEMPRE discover, ver CONTRIBUTING.md)

Sigue el patrón de test_accounts.py: base de test DEDICADA
`sana_turnos_test_busquedas` (conectándose a la maintenance db `postgres`),
así las suites corren en paralelo sin pisarse. Si Postgres no está accesible,
las pruebas con base se saltean con un mensaje claro — pero el CONTRATO de
"nunca romper el sitio" (db down -> no-op / lista vacía) se prueba igual, sin
DB, apuntando DATABASE_URL a un puerto que no escucha nadie.
"""

import os
import sys
import json
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db  # noqa: E402
from app.core import db_analytics  # noqa: E402
from app.core import db_schema  # noqa: E402
from app.auth import turnstile   # noqa: E402
from app.core import config  # noqa: E402
turnstile.ENV_PATH = ""  # no leer el .env del dev (módulo compartido — no pisar _secret)
config.ENV_PATH = ""  # idem: no leer el .env real del checkout de dev
from app.modules.turnos import searches   # noqa: E402
from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_busquedas", seed=lambda: db_analytics.log_search("__forzar_esquema__", 0))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


def _truncate():
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE searches")


# ---- normalización (db_analytics._norm_search) ---------------------------------------

class TestNormalizacion(unittest.TestCase):
    def test_lowercase_trim_colapsa_espacios(self):
        self.assertEqual(db_analytics._norm_search("  Medicina    General  "), "medicina general")

    def test_tabs_y_saltos_de_linea_colapsan(self):
        self.assertEqual(db_analytics._norm_search("ecografia\n\tabdominal"), "ecografia abdominal")

    def test_vacio_o_none(self):
        self.assertEqual(db_analytics._norm_search(""), "")
        self.assertEqual(db_analytics._norm_search(None), "")
        self.assertEqual(db_analytics._norm_search("   "), "")


# ---- anti-abuso propio (searches.allowed) ----------------------------------

class TestPermitido(unittest.TestCase):
    def setUp(self):
        searches._HITS.clear()

    def tearDown(self):
        searches._HITS.clear()

    def test_corta_al_limite_diario(self):
        _env.set_env(self, TURNOS_SEARCHES_PER_DAY="3")
        ip = "10.0.0.9"
        for _ in range(3):
            self.assertTrue(searches.allowed(ip))
        self.assertFalse(searches.allowed(ip))

    def test_ip_distinta_tiene_cupo_propio(self):
        _env.set_env(self, TURNOS_SEARCHES_PER_DAY="1")
        self.assertTrue(searches.allowed("1.2.3.4"))
        self.assertFalse(searches.allowed("1.2.3.4"))
        self.assertTrue(searches.allowed("4.3.2.1"))


# ---- POST /api/search-log (endpoint completo) --------------------------------

class TestApiBuscarLog(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        searches._HITS.clear()
        _env.set_env(self, TURNOS_SEARCHES_PER_DAY=None)
        _truncate()

    def tearDown(self):
        searches._HITS.clear()
        _truncate()

    def _post_log(self, body, ip="203.0.113.5"):
        """Native route contract: POST /api/search-log with the client IP
        forced through X-Forwarded-For (what turnstile.client_ip reads)."""
        if not isinstance(body, (bytes, bytearray)):
            body = json.dumps(body).encode("utf-8")
        r = TestClient(app_module.app).post(
            "/api/search-log", content=bytes(body), headers={"X-Forwarded-For": ip})
        return r.status_code

    def test_ok_204_y_persiste_normalizado(self):
        status = self._post_log(
            {"q": "  Medicina   General  ", "resultados": 5, "origen": "/doctors"}, ip="1.1.1.1")
        self.assertEqual(status, 204)
        rows = db_analytics.top_searches(dias=1)
        self.assertEqual(len(rows), 1)
        q, veces, con_resultados = rows[0]
        self.assertEqual(q, "medicina general")
        self.assertEqual(veces, 1)
        self.assertEqual(con_resultados, 1)

    def test_sid_del_cliente_agrupa_la_sesion(self):
        self._post_log({"q": "ped", "resultados": 3, "sid": "abc123"}, ip="1.1.1.2")
        self._post_log({"q": "pediatría", "resultados": 0, "sid": "abc123"}, ip="1.1.1.2")
        self.assertEqual(db_analytics.top_searches(dias=1), [("pediatría", 1, 0)])

    def test_q_corto_400(self):
        self.assertEqual(self._post_log({"q": "a", "resultados": 0}, ip="2.2.2.2"), 400)
        self.assertEqual(db_analytics.top_searches(dias=1), [])

    def test_q_largo_400(self):
        self.assertEqual(self._post_log({"q": "x" * 81, "resultados": 0}, ip="2.2.2.3"), 400)

    def test_falta_q_400(self):
        self.assertEqual(self._post_log({"resultados": 0}, ip="2.2.2.4"), 400)

    def test_resultados_invalido_400(self):
        self.assertEqual(
            self._post_log({"q": "cardiologia", "resultados": "no-numero"}, ip="2.2.2.5"), 400)

    def test_resultados_negativo_400(self):
        self.assertEqual(self._post_log({"q": "cardiologia", "resultados": -1}, ip="2.2.2.6"), 400)

    def test_json_invalido_400(self):
        self.assertEqual(self._post_log(b"{no es json", ip="2.2.2.7"), 400)

    def test_sin_ip_no_se_guarda(self):
        _env.set_env(self, TURNOS_SEARCHES_PER_DAY="1")
        ip = "3.3.3.3"
        self.assertEqual(self._post_log({"q": "kinesiologia", "resultados": 2}, ip=ip), 204)
        self.assertEqual(self._post_log({"q": "otra query distinta", "resultados": 1}, ip=ip),
                         204)  # el tope de analytics no avisa, pero no persiste
        rows = db_analytics.top_searches(dias=1)
        self.assertEqual(len(rows), 1)  # sólo la primera entró

    def test_metodo_o_ruta_no_matchea(self):
        # Native contract: only POST /api/search-log is registered. Anything
        # else — wrong path, or a GET on the POST-only route — falls through
        # to the native terminal, which answers /api/* with the flat JSON 404
        # (never Starlette's 405; test_module_router_fallthrough.py pins the
        # same contract).
        client = TestClient(app_module.app)
        self.assertEqual(client.post("/api/otra-cosa").status_code, 404)
        r = client.get("/api/search-log")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json(), {"error": "not found"})

    def test_no_pii_en_el_registro(self):
        self._post_log({"q": "dermatologia", "resultados": 3}, ip="9.9.9.9")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM searches")
            cols = {desc.name for desc in cur.description}
        self.assertNotIn("ip", cols)
        self.assertNotIn("dni", cols)


# ---- top_busquedas agrega correctamente ---------------------------------------

class TestTopBusquedas(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _truncate()

    def tearDown(self):
        _truncate()

    def test_agrupa_por_q_normalizada_y_ordena_por_veces(self):
        db_analytics.log_search("Cardiología", 4)
        db_analytics.log_search("  cardiología  ", 0)
        db_analytics.log_search("cardiologia", 2)  # sin tilde: normaliza DISTINTO (no se pisa el acento)
        db_analytics.log_search("kinesiologia", 1)
        rows = db_analytics.top_searches(dias=30)
        by_q = {q: (veces, con_resultados) for q, veces, con_resultados in rows}
        self.assertEqual(by_q["cardiología"], (2, 1))  # una con resultados, otra sin
        self.assertEqual(by_q["cardiologia"], (1, 1))
        self.assertEqual(by_q["kinesiologia"], (1, 1))
        # orden: más buscada primero
        self.assertEqual(rows[0][0], "cardiología")

    def test_prefijos_de_una_sesion_se_pegan_al_mas_largo(self):
        for q, n in (("ped", 9), ("pedi", 9), ("pediat", 4), ("pediatría", 0)):
            db_analytics.log_search(q, n, sid="s1")
        rows = db_analytics.top_searches(dias=30)
        self.assertEqual(rows, [("pediatría", 1, 0)])

    def test_fragmentos_de_sesiones_distintas_no_se_pegan(self):
        db_analytics.log_search("ped", 9, sid="s1")
        db_analytics.log_search("pediatría", 3, sid="s2")
        db_analytics.log_search("ped", 9)  # sin sesión (cliente viejo): tal cual
        by_q = {q: v for q, v, _ in db_analytics.top_searches(dias=30)}
        self.assertEqual(by_q, {"ped": 2, "pediatría": 1})

    def test_misma_sesion_consultas_no_prefijo_quedan_separadas(self):
        db_analytics.log_search("pediatría", 3, sid="s1")
        db_analytics.log_search("cardiología", 2, sid="s1")
        by_q = {q: v for q, v, _ in db_analytics.top_searches(dias=30)}
        self.assertEqual(by_q, {"pediatría": 1, "cardiología": 1})

    def test_busqueda_posterior_de_la_misma_pagina_no_se_traga_a_la_anterior(self):
        db_analytics.log_search("dermatologia", 3, sid="s1")
        db_analytics.log_search("dermatologia pinamar", 0, sid="s1")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE searches SET ts = now() - interval '5 minutes' WHERE q='dermatologia'")
        by_q = {q: (v, c) for q, v, c in db_analytics.top_searches(dias=30)}
        self.assertEqual(by_q, {"dermatologia": (1, 1), "dermatologia pinamar": (1, 0)})

    def test_dias_filtra_ventana(self):
        db_analytics.log_search("consulta vieja", 1)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE searches SET ts = now() - interval '90 days' WHERE q='consulta vieja'")
        db_analytics.log_search("consulta nueva", 1)
        rows = db_analytics.top_searches(dias=30)
        qs = [q for q, _, _ in rows]
        self.assertIn("consulta nueva", qs)
        self.assertNotIn("consulta vieja", qs)

    def test_sin_busquedas_lista_vacia(self):
        self.assertEqual(db_analytics.top_searches(dias=30), [])


# ---- contrato "nunca rompe el sitio": DB caída -> no-op / lista vacía --------

class TestDbCaidaNoRompe(unittest.TestCase):
    def setUp(self):
        self._orig_ensured = set(db_schema._ensured)
        # Puerto 1 no escucha nadie a propósito — nunca conecta, así que la
        # password acá es irrelevante (no reutilizar la real igual).
        _env.set_env(self, DATABASE_URL="postgresql://postgres:unreachable@127.0.0.1:1/no_existe")
        db_schema._ensured.clear()

    def tearDown(self):
        db_schema._ensured.clear()
        db_schema._ensured.update(self._orig_ensured)

    def test_log_busqueda_no_lanza(self):
        db_analytics.log_search("lo que sea", 1)  # no debe levantar excepción

    def test_top_busquedas_devuelve_vacio(self):
        self.assertEqual(db_analytics.top_searches(), [])

    def test_endpoint_sigue_devolviendo_204(self):
        searches._HITS.clear()
        r = TestClient(app_module.app).post(
            "/api/search-log",
            content=json.dumps({"q": "algo", "resultados": 0}).encode("utf-8"),
            headers={"X-Forwarded-For": "6.6.6.6"})
        self.assertEqual(r.status_code, 204)  # nunca 500 aunque la DB esté caída


if __name__ == "__main__":
    unittest.main()
