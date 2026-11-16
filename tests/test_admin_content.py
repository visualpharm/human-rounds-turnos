"""test_admin_content.py — pruebas del CRUD del admin sobre el catálogo
(admin_content.py + catalog.py + db.py).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest test.test_admin_content -v

Usa los routers FastAPI reales con `TestClient` y una base dedicada
(`sana_turnos_test_admin_contenido`) para correr en paralelo sin pisar otras
suites.
"""

import os
import sys
import json
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.auth import admin_auth        # noqa: E402
from app.patient_web import catalog          # noqa: E402
from app.admin_panel import admin_content, admin_content_router  # noqa: E402
from app.patient_web import site_base, site_catalog
from app.modules.turnos import prep_model       # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_app = FastAPI()
_app.include_router(admin_content_router.router)
_CLIENT = TestClient(_app)

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_admin_contenido", seed=lambda: db_catalog.catalog_list("centros"))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


def _reset_catalogo_table():
    """Vacía la tabla `catalogo` entre tests (todos comparten la misma base
    de test durante todo el módulo)."""
    if not HAVE_DB:
        return
    import psycopg
    with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute("TRUNCATE catalog")


# ---- fixtures del catálogo base (aislado del real) -------------------------

_CENTROS = {"centros": [
    {"slug": "centro-a", "name": "Centro A", "bookable": True},
    {"slug": "centro-b", "name": "Centro B", "bookable": False},
]}
_SERVICIOS = {"servicios": [
    {"id": "serv-1", "nombre": "Servicio Uno", "centro": "centro-a", "modo": "walkin"},
]}
_MEDICOS = {"medicos": [
    {"id": "med-1", "nombre": "Marcelo Aguirre", "especialidad": "Cardiología", "centro": "centro-a"},
]}
_ESPECIALIDADES = {"specialties": [
    {"nombre": "Cardiología", "cluster": "cardiologia"},
]}


_PREPARACIONES = {"estudios": [
    {"id": "eco-abdominal", "nombre": "Ecografía abdominal",
     "match": ["ecografia abdominal"],
     "resumen": "Se hace en ayunas.",
     "ayuno": {"required": True, "horas": 8, "agua_permitida": True, "nota": None},
     "traer": ["DNI"], "antes": ["Sólo agua."], "no_hace_falta": ["Vejiga llena."]},
]}


def _write_fixtures(webdir):
    os.makedirs(webdir, exist_ok=True)
    with open(os.path.join(webdir, "centros-salud.json"), "w", encoding="utf-8") as f:
        json.dump(_CENTROS, f)
    with open(os.path.join(webdir, "servicios.json"), "w", encoding="utf-8") as f:
        json.dump(_SERVICIOS, f)
    with open(os.path.join(webdir, "medicos.json"), "w", encoding="utf-8") as f:
        json.dump(_MEDICOS, f)
    with open(os.path.join(webdir, "especialidades.json"), "w", encoding="utf-8") as f:
        json.dump(_ESPECIALIDADES, f)
    with open(os.path.join(webdir, "estudios-preparacion.json"), "w", encoding="utf-8") as f:
        json.dump(_PREPARACIONES, f)


def _admin_headers():
    return {"Cookie": "st_admin=" + admin_auth._mint_token(admin_auth._admin_user())}


def _admision_headers():
    # require_admin sólo verifica firma/rol del token — no hace falta que el
    # usuario exista en la tabla `operadores` para probar el gate.
    return {"Cookie": "st_admin=" + admin_auth._mint_token("mesa-rosa", "admisionista")}


def _medico_headers():
    return {"Cookie": "st_admin=" + admin_auth._mint_token("dra-ana", "medico")}


def _req(method, path, body=None, auth=True, rol="admin"):
    if not auth:
        headers = {}
    elif rol == "admisionista":
        headers = _admision_headers()
    elif rol == "medico":
        headers = _medico_headers()
    else:
        headers = _admin_headers()
    return _CLIENT.request(method, path, json=body, headers=headers)


class AdminContenidoTestCase(unittest.TestCase):
    """Apunta TURNOS_WEBDATA_DIR (datos.py) a un webdata/ propio, aislado de
    la base real."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_catalogo_table()
        self._tmp = tempfile.mkdtemp(prefix="admin-contenido-test-")
        _write_fixtures(os.path.join(self._tmp, "webdata"))
        _env.set_env(self, TURNOS_WEBDATA_DIR=os.path.join(self._tmp, "webdata"))
        catalog._JSON_CACHE.clear()
        catalog.invalidate()

    def tearDown(self):
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        shutil.rmtree(self._tmp, ignore_errors=True)
        _reset_catalogo_table()


# ---- auth --------------------------------------------------------------

class TestAuth(unittest.TestCase):
    def test_sin_cookie_401(self):
        h = _req("GET", "/api/admin/content/centers", auth=False)
        self.assertEqual(h.status_code, 401)

    def test_ruta_desconocida_devuelve_false(self):
        h = _CLIENT.get("/api/otra-cosa")
        self.assertEqual(h.status_code, 404)


class TestKnowledgeProperties(unittest.TestCase):
    def test_propiedades_se_proponen_desde_el_contenido(self):
        h = _req(
            "POST", "/api/admin/content/knowledge/properties",
            body={
                "content": "Vacunación antigripal en Pinamar. Se aplica según la etapa de la vida.",
                "categories": [{"id": "prevencion", "title": "Prevención"}],
                "current": {"jurisdiction": "pinamar", "parent_id": "prevencion"},
            },
        )
        self.assertEqual(h.status_code, 200, h.json())
        self.assertEqual(h.json()["properties"]["jurisdiction"], "pinamar")
        self.assertEqual(h.json()["properties"]["parent_id"], "prevencion")
        self.assertTrue(h.json()["properties"]["title"])
        self.assertTrue(h.json()["properties"]["summary"])
        self.assertTrue(h.json()["properties"]["tags"])

    def test_propiedades_requieren_contenido_y_admin(self):
        h = _req("POST", "/api/admin/content/knowledge/properties",
                          body={"content": ""})
        self.assertEqual(h.status_code, 400)

        h = _req("POST", "/api/admin/content/knowledge/properties",
                          body={"content": "Texto suficiente."}, auth=False)
        self.assertEqual(h.status_code, 401)


# ---- CRUD ----------------------------------------------------------------

class TestCrud(AdminContenidoTestCase):
    def test_service_closures_survive_save_and_reload_without_weekdays(self):
        closure = {"tipo": "cerrado", "fecha": "2026-11-01", "motivo": "Licencia"}
        for mode in ("solicitud", "walkin"):
            with self.subTest(mode=mode):
                doc = {"nombre": "Servicio Uno", "centro": "centro-a", "modo": mode}
                if mode == "walkin":
                    doc["excepciones"] = [closure]
                else:
                    doc["agenda"] = {"franjas": [], "excepciones": [closure]}
                response = _req("PUT", "/api/admin/content/services/serv-1", body=doc)
                self.assertEqual(response.status_code, 200, response.json())
                saved = response.json()["item"]
                items = _req("GET", "/api/admin/content/services").json()["items"]
                loaded = next(item for item in items if item["id"] == "serv-1")
                for item in (saved, loaded):
                    self.assertEqual((item.get("agenda") or item)["excepciones"], [closure])
                    if mode == "walkin":
                        self.assertNotIn("agenda", item)

    def test_list_incluye_base_y_metadatos(self):
        h = _req("GET", "/api/admin/content/centers")
        self.assertEqual(h.status_code, 200)
        slugs = {it["slug"] for it in h.json()["items"]}
        self.assertEqual(slugs, {"centro-a", "centro-b"})
        by_slug = {it["slug"]: it for it in h.json()["items"]}
        self.assertEqual(by_slug["centro-a"]["_origen"], "json")
        self.assertFalse(by_slug["centro-a"]["_borrado"])

    def test_crear_genera_slug_y_persiste(self):
        h = _req("POST", "/api/admin/content/centers",
                          body={"name": "Centro Nuevo", "bookable": True})
        self.assertEqual(h.status_code, 200)
        self.assertTrue(h.json()["ok"])
        self.assertEqual(h.json()["item"]["slug"], "centro-nuevo")

        h2 = _req("GET", "/api/admin/content/centers")
        slugs = {it["slug"] for it in h2.json()["items"]}
        self.assertIn("centro-nuevo", slugs)

    def test_crear_validacion_400(self):
        h = _req("POST", "/api/admin/content/services",
                          body={"nombre": "X", "centro": "centro-a", "modo": "no-existe"})
        self.assertEqual(h.status_code, 400)
        self.assertIn("error", h.json())

    def test_franja_con_servicio_se_valida_y_persiste(self):
        # 2026-07-24: una franja puede decir QUÉ servicio atiende (Lazcano hace
        # anticoncepción los lunes y medicina general los martes). Id real del
        # catálogo → se guarda; id desconocido → 400.
        franja = {"dias": [1], "desde": "08:00", "hasta": "12:00",
                  "cada_min": 20, "servicio": "serv-1"}
        h = _req("PUT", "/api/admin/content/doctors/med-1",
                          body={"nombre": "Marcelo Aguirre", "especialidad": "Cardiología",
                                "centros": [{"centro": "centro-a", "franjas": [franja],
                                             "excepciones": []}]})
        self.assertEqual(h.status_code, 200, h.json())
        guardada = h.json()["item"]["centros"][0]["franjas"][0]
        self.assertEqual(guardada["servicio"], "serv-1")

        franja["servicio"] = "no-existe"
        h = _req("PUT", "/api/admin/content/doctors/med-1",
                          body={"nombre": "Marcelo Aguirre", "especialidad": "Cardiología",
                                "centros": [{"centro": "centro-a", "franjas": [franja],
                                             "excepciones": []}]})
        self.assertEqual(h.status_code, 400)
        self.assertIn("servicio desconocido", h.json()["error"])

    def test_auto_confirmar_requiere_turnos(self):
        # Reunión hospital 2026-07-24 + ronda 2 (responsable del producto): orden/turno/aprobación
        # son tres perillas independientes — auto_confirmar vale en los dos
        # modos con turno (con o sin orden); walk-in no tiene turno.
        for modo in ("solicitud", "solicitud_con_orden"):
            h = _req("PUT", "/api/admin/content/services/serv-1",
                              body={"nombre": "Servicio Uno", "centro": "centro-a",
                                    "modo": modo, "auto_confirmar": True})
            self.assertEqual(h.status_code, 200, (modo, h.json()))
            self.assertTrue(h.json()["item"]["auto_confirmar"])

        h = _req("PUT", "/api/admin/content/services/serv-1",
                          body={"nombre": "Servicio Uno", "centro": "centro-a",
                                "modo": "walkin", "auto_confirmar": True})
        self.assertEqual(h.status_code, 400)
        self.assertIn("auto_confirmar", h.json()["error"])

    def test_palabras_excluidas_se_normalizan_y_validan(self):
        # Reunión municipal 2026-07-17: "que su consultorio no aparezca a PAP".
        h = _req("PUT", "/api/admin/content/services/serv-1",
                          body={"nombre": "Servicio Uno", "centro": "centro-a",
                                "modo": "walkin",
                                "palabras_excluidas": ["PAP", "", "   "]})
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["item"]["palabras_excluidas"], ["PAP"])

        h = _req("PUT", "/api/admin/content/services/serv-1",
                          body={"nombre": "Servicio Uno", "centro": "centro-a",
                                "modo": "walkin", "palabras_excluidas": "PAP"})
        self.assertEqual(h.status_code, 400)
        self.assertIn("palabras_excluidas", h.json()["error"])

    def test_crear_id_duplicado_409(self):
        h = _req("POST", "/api/admin/content/services",
                          body={"id": "serv-1", "nombre": "Duplicado",
                                "centro": "centro-a", "modo": "walkin"})
        self.assertEqual(h.status_code, 409)

    def test_editar_reemplaza_doc_id_del_path_gana(self):
        h = _req("PUT", "/api/admin/content/centers/centro-a",
                          body={"name": "Centro A Editado", "slug": "otra-cosa"})
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["item"]["slug"], "centro-a")  # id del path gana
        self.assertEqual(h.json()["item"]["name"], "Centro A Editado")

    def test_borrar_id_base_es_soft_delete(self):
        h = _req("DELETE", "/api/admin/content/centers/centro-a")
        self.assertEqual(h.status_code, 200)

        h2 = _req("GET", "/api/admin/content/centers")
        by_slug = {it["slug"]: it for it in h2.json()["items"]}
        self.assertTrue(by_slug["centro-a"]["_borrado"])
        self.assertIn("centro-a", by_slug)  # sigue existiendo, sólo oculto

    def test_borrar_id_solo_db_es_hard_delete(self):
        _req("POST", "/api/admin/content/centers", body={"name": "Efímero"})
        h = _req("DELETE", "/api/admin/content/centers/efimero")
        self.assertEqual(h.status_code, 200)

        h2 = _req("GET", "/api/admin/content/centers")
        slugs = {it["slug"] for it in h2.json()["items"]}
        self.assertNotIn("efimero", slugs)  # ya no queda ni rastro

    def test_lo_que_tiene_turnos_no_se_borra_se_deshabilita(self):
        """Reunión hospital 2026-07-24: "ningún registro con turnos históricos
        puede eliminarse". Un centro creado desde el admin (id sólo-DB, el
        único caso que antes se borraba en duro) con un turno encima queda
        escondido, no borrado — si no, esa solicitud apunta a un id que no
        existe en ningún lado."""
        _req("POST", "/api/admin/content/centers", body={"name": "Con Historia"})
        sid = db_booking.create_booking_request("30111222", "Rosa", "Vázquez", "1970-05-05",
                                 "r@ejemplo.com", "serv-x", "con-historia")
        try:
            h = _req("DELETE", "/api/admin/content/centers/con-historia")
            self.assertEqual(h.status_code, 200)
            self.assertEqual(h.json().get("turnos"), 1)
            self.assertTrue(h.json().get("conservado"))

            h2 = _req("GET", "/api/admin/content/centers")
            by_slug = {it["slug"]: it for it in h2.json()["items"]}
            self.assertIn("con-historia", by_slug)          # la fila sigue
            self.assertTrue(by_slug["con-historia"]["_borrado"])  # pero oculta
        finally:
            with db._connect() as conn, conn.cursor() as cur:
                _dbtest.delete_booking_requests(cur, "id=%s", (sid,))

    def test_restaurar_id_base_vuelve_al_original(self):
        _req("PUT", "/api/admin/content/centers/centro-a", body={"name": "Editado"})
        h = _req("POST", "/api/admin/content/centers/centro-a/restaurar")
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["item"]["name"], "Centro A")  # el original

    def test_restaurar_des_borra(self):
        _req("DELETE", "/api/admin/content/centers/centro-a")
        h_antes = _req("GET", "/api/admin/content/centers")
        by_slug = {it["slug"]: it for it in h_antes.json()["items"]}
        self.assertTrue(by_slug["centro-a"]["_borrado"])

        h = _req("POST", "/api/admin/content/centers/centro-a/restaurar")
        self.assertFalse(h.json()["item"]["_borrado"])

    def test_restaurar_id_inexistente_es_404(self):
        """Nothing to restore — no base item, no overlay row: a 404 in the
        family's domain shape, never a 200 with an empty item."""
        for tipo in ("centers", "services", "doctors", "preparations"):
            h = _req("POST", f"/api/admin/content/{tipo}/no-existe-nunca/restaurar")
            self.assertEqual(h.status_code, 404, tipo)
            self.assertEqual(h.json(), {"error": "no_encontrado"}, tipo)

    def test_restaurar_item_propio_vivo_sigue_200(self):
        _req("POST", "/api/admin/content/centers", body={"name": "Propio Vivo"})
        h = _req("POST", "/api/admin/content/centers/propio-vivo/restaurar")
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["item"]["name"], "Propio Vivo")

    def test_restaurar_item_propio_borrado_en_duro_es_404(self):
        _req("POST", "/api/admin/content/centers", body={"name": "Sin Historia"})
        self.assertEqual(_req("DELETE", "/api/admin/content/centers/sin-historia").status_code, 200)
        h = _req("POST", "/api/admin/content/centers/sin-historia/restaurar")
        self.assertEqual(h.status_code, 404)


class TestRolAdmisionista(AdminContenidoTestCase):
    """Admission reads catalog data to create appointments but never edits it."""

    def test_admisionista_lista_todos_los_tipos(self):
        for tipo in ("centers", "services", "doctors"):
            h = _req("GET", f"/api/admin/content/{tipo}", rol="admisionista")
            self.assertEqual(h.status_code, 200, tipo)

    def test_admisionista_no_edita_servicio_ni_medico(self):
        h = _req("PUT", "/api/admin/content/services/serv-1",
                          body={"nombre": "Servicio Uno", "centro": "centro-a",
                                "modo": "walkin", "palabras": ["nueva palabra"]},
                          rol="admisionista")
        self.assertEqual(h.status_code, 403)

        h = _req("PUT", "/api/admin/content/doctors/med-1",
                          body={"nombre": "Marcelo Aguirre", "especialidad": "Cardiología",
                                "centro": "centro-a",
                                "agenda": {"dias": [1, 3], "desde": "08:00", "hasta": "12:00"}},
                          rol="admisionista")
        self.assertEqual(h.status_code, 403)

    def test_admisionista_no_edita_centros(self):
        h = _req("PUT", "/api/admin/content/centers/centro-a",
                          body={"name": "Centro Hackeado"}, rol="admisionista")
        self.assertEqual(h.status_code, 403)

    def test_admisionista_no_crea_ni_borra_ni_restaura(self):
        h = _req("POST", "/api/admin/content/services",
                          body={"nombre": "Nuevo", "centro": "centro-a", "modo": "walkin"},
                          rol="admisionista")
        self.assertEqual(h.status_code, 403)

        h = _req("DELETE", "/api/admin/content/doctors/med-1", rol="admisionista")
        self.assertEqual(h.status_code, 403)

        h = _req("POST", "/api/admin/content/services/serv-1/restaurar",
                          rol="admisionista")
        self.assertEqual(h.status_code, 403)


class TestRolMedico(AdminContenidoTestCase):
    """El médico lee el catálogo para dar un hueco de su propia agenda
    (2026-08-05) — servicios, centros y colegas son datos públicos del sitio.
    «Conocimiento» no: es material interno y sigue siendo solo_admin."""

    def test_medico_lista_el_catalogo_publico(self):
        for tipo in ("centers", "services", "doctors"):
            h = _req("GET", f"/api/admin/content/{tipo}", rol="medico")
            self.assertEqual(h.status_code, 200, tipo)

    def test_medico_no_ve_conocimiento(self):
        h = _req("GET", "/api/admin/content/knowledge", rol="medico")
        self.assertEqual(h.status_code, 403)

    def test_medico_no_edita_nada(self):
        h = _req("PUT", "/api/admin/content/services/serv-1",
                          body={"nombre": "Servicio Uno", "centro": "centro-a",
                                "modo": "walkin"}, rol="medico")
        self.assertEqual(h.status_code, 403)

        h = _req("DELETE", "/api/admin/content/doctors/med-1", rol="medico")
        self.assertEqual(h.status_code, 403)


class TestDbCaida503(AdminContenidoTestCase):
    def test_list_503_si_la_base_no_esta(self):
        orig = db_catalog.catalog_list

        def _boom(tipo):
            raise db.DbUnavailable("simulado")

        db_catalog.catalog_list = _boom
        try:
            h = _req("GET", "/api/admin/content/centers")
            self.assertEqual(h.status_code, 503)
        finally:
            db_catalog.catalog_list = orig

    def test_crear_503_si_la_base_no_esta(self):
        orig = db_catalog.catalog_list

        def _boom(tipo):
            raise db.DbUnavailable("simulado")

        db_catalog.catalog_list = _boom
        try:
            h = _req("POST", "/api/admin/content/centers", body={"name": "X"})
            self.assertEqual(h.status_code, 503)
        finally:
            db_catalog.catalog_list = orig


class TestPreparaciones(AdminContenidoTestCase):
    """Las indicaciones de "Cómo prepararte" se editan desde el panel, con el
    mismo overlay que servicios/centros/médicos (responsable del producto, 2026-07-29): son del
    hospital, no del código — si el laboratorio pasa el ayuno de 8 a 6 horas,
    eso no puede necesitar un deploy."""

    def test_list_trae_las_del_json_base(self):
        h = _req("GET", "/api/admin/content/preparations")
        self.assertEqual(h.status_code, 200)
        ids = {it["id"] for it in h.json()["items"]}
        self.assertEqual(ids, {"eco-abdominal"})
        self.assertEqual(h.json()["items"][0]["_origen"], "json")

    def test_editar_pisa_el_original_y_lo_ve_el_sitio(self):
        h = _req("PUT", "/api/admin/content/preparations/eco-abdominal",
                          body={"nombre": "Ecografía abdominal",
                                "match": ["ecografia abdominal"],
                                "resumen": "Ahora son 6 horas.",
                                "ayuno": {"required": True, "horas": 6,
                                          "agua_permitida": True},
                                "antes": ["Sólo agua."]})
        self.assertEqual(h.status_code, 200)
        # lo que lee el sitio público sale de la misma capa
        site_base._CACHE.clear()
        entrada = {e["id"]: e for e in site_catalog._preparations()}["eco-abdominal"]
        self.assertEqual(entrada["ayuno"]["horas"], 6)
        self.assertEqual(prep_model.ayuno(entrada)["texto"], "Ayuno de 6 horas.")

    def test_restaurar_vuelve_al_texto_autorado(self):
        _req("PUT", "/api/admin/content/preparations/eco-abdominal",
             body={"nombre": "X", "match": ["ecografia abdominal"], "resumen": "otra"})
        h = _req("POST", "/api/admin/content/preparations/eco-abdominal/restaurar")
        self.assertEqual(h.status_code, 200)
        site_base._CACHE.clear()
        entrada = {e["id"]: e for e in site_catalog._preparations()}["eco-abdominal"]
        self.assertEqual(entrada["resumen"], "Se hace en ayunas.")

    def test_las_palabras_se_guardan_sin_tildes_y_en_minuscula(self):
        # una orden escaneada pierde las tildes; si la keyword las tiene, no
        # matchea nunca (misma regla que _meta.diseno_match del JSON base)
        h = _req("POST", "/api/admin/content/preparations",
                          body={"nombre": "Vacunación", "match": "Vacunación\nANTIGRIPAL"})
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.json()["item"]["match"], ["vacunacion", "antigripal"])
        self.assertEqual(h.json()["item"]["id"], "vacunacion")

    def test_sin_palabras_no_se_guarda(self):
        # una preparación que no matchea nada no se la muestra nunca a nadie
        h = _req("POST", "/api/admin/content/preparations",
                          body={"nombre": "Huérfana", "match": []})
        self.assertEqual(h.status_code, 400)

    def test_una_indicacion_clinica_no_la_edita_cualquiera(self):
        # Servicios y médicos SÍ los edita un operador (son la agenda del día a
        # día); un ayuno no. El admisionista es el rol de menor privilegio que
        # este endpoint deja listar, así que es el que prueba el gate.
        h = _req("PUT", "/api/admin/content/preparations/eco-abdominal",
                          body={"nombre": "X", "match": ["x"]}, rol="admisionista")
        self.assertEqual(h.status_code, 403)


if __name__ == "__main__":
    unittest.main()
