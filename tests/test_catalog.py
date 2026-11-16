"""test_catalog.py — pruebas del overlay editable del catálogo (catalog.py + db.py).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest test.test_catalog -v   (desde la raíz)

Sigue el patrón de test_accounts.py: base dedicada `sana_turnos_test_catalogo`
(así corre en paralelo con las otras suites sin pisarlas). Las pruebas de
validación no tocan la base. Las de merge/overlay se saltean si Postgres no
está accesible.
"""

import os
import sys
import json
import shutil
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.patient_web import catalog    # noqa: E402

import _dbtest  # noqa: E402
from _packtest import reference_pack_case  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_catalogo", seed=lambda: db_catalog.catalog_list("centros"))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


def _reset_catalogo_table():
    """Vacía la tabla `catalogo` entre tests — todos comparten la misma base
    de test durante todo el módulo, así que sin esto los overrides de un test
    contaminan al siguiente."""
    if not HAVE_DB:
        return
    import psycopg
    with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute("TRUNCATE catalog")


# ---- fixtures del catálogo base (webdata propio, aislado del real) ---------

_CENTROS = {"centros": [
    {"slug": "centro-a", "name": "Centro A", "bookable": True},
    {"slug": "centro-b", "name": "Centro B", "bookable": False},
]}
_SERVICIOS = {"servicios": [
    {"id": "serv-1", "nombre": "Servicio Uno", "centro": "centro-a", "modo": "walkin"},
    {"id": "serv-2", "nombre": "Servicio Dos", "centro": "centro-a", "modo": "solicitud_con_orden"},
]}
_MEDICOS = {"medicos": [
    {"id": "med-1", "nombre": "Marcelo Aguirre", "especialidad": "Cardiología", "centro": "centro-a"},
]}
_ESPECIALIDADES = {"specialties": [
    {"nombre": "Clínica", "cluster": "clinica"},
    {"nombre": "Cardiología", "cluster": "cardiologia"},
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


class CatalogoTestCase(unittest.TestCase):
    """Apunta TURNOS_WEBDATA_DIR (datos.py) a un webdata/ propio y descarta
    todos los caches en cada test — nunca comparte estado con el catálogo
    real ni entre tests."""

    def setUp(self):
        _reset_catalogo_table()
        self._tmp = tempfile.mkdtemp(prefix="catalogo-test-")
        _write_fixtures(os.path.join(self._tmp, "webdata"))
        _env.set_env(self, TURNOS_WEBDATA_DIR=os.path.join(self._tmp, "webdata"))
        catalog._JSON_CACHE.clear()
        catalog.invalidate()

    def tearDown(self):
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        shutil.rmtree(self._tmp, ignore_errors=True)
        _reset_catalogo_table()


# ---- merge: sin overlay -----------------------------------------------------

class TestMergeSinDB(CatalogoTestCase):
    def test_solo_json_devuelve_la_base_tal_cual(self):
        cs = catalog.centers()
        self.assertEqual([c["slug"] for c in cs], ["centro-a", "centro-b"])
        self.assertEqual(cs[0]["name"], "Centro A")

    def test_servicios_y_medicos_tambien_sin_overlay(self):
        self.assertEqual({s["id"] for s in catalog.services()}, {"serv-1", "serv-2"})
        self.assertEqual({m["id"] for m in catalog.doctors()}, {"med-1"})


# ---- merge: con overlay (requiere DB) ---------------------------------------

class TestMergeConDB(CatalogoTestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        super().setUp()

    def test_override_pisa_por_id(self):
        db_catalog.catalog_upsert("centros", "centro-a",
                           {"slug": "centro-a", "name": "Centro A Editado", "bookable": True})
        catalog.invalidate()
        cs = catalog.centers()
        self.assertEqual([c["slug"] for c in cs], ["centro-a", "centro-b"])  # orden preservado
        self.assertEqual(cs[0]["name"], "Centro A Editado")

    def test_id_nuevo_agrega_al_final(self):
        db_catalog.catalog_upsert("centros", "centro-c", {"slug": "centro-c", "name": "Centro C"})
        catalog.invalidate()
        cs = catalog.centers()
        self.assertEqual([c["slug"] for c in cs], ["centro-a", "centro-b", "centro-c"])

    def test_borrado_esconde_del_publico_pero_no_del_admin(self):
        db_catalog.catalog_upsert("centros", "centro-a",
                           {"slug": "centro-a", "name": "Centro A"}, borrado=True)
        catalog.invalidate()
        cs = catalog.centers()
        self.assertEqual([c["slug"] for c in cs], ["centro-b"])
        items = catalog.admin_list("centros")
        by_slug = {it["slug"]: it for it in items}
        self.assertTrue(by_slug["centro-a"]["_borrado"])
        self.assertFalse(by_slug["centro-b"]["_borrado"])

    def test_restaurar_hard_delete_vuelve_al_original(self):
        db_catalog.catalog_upsert("centros", "centro-a", {"slug": "centro-a", "name": "Editado"})
        catalog.invalidate()
        self.assertEqual(catalog.centers()[0]["name"], "Editado")
        db_catalog.catalog_delete_row("centros", "centro-a")
        catalog.invalidate()
        self.assertEqual(catalog.centers()[0]["name"], "Centro A")

    def test_orden_estable_ids_nuevos_ordenados_por_id(self):
        db_catalog.catalog_upsert("centros", "centro-z", {"slug": "centro-z", "name": "Z"})
        db_catalog.catalog_upsert("centros", "centro-m", {"slug": "centro-m", "name": "M"})
        catalog.invalidate()
        cs = catalog.centers()
        # los ids base primero, en su orden; los nuevos al final, ordenados
        self.assertEqual([c["slug"] for c in cs],
                         ["centro-a", "centro-b", "centro-m", "centro-z"])

    def test_admin_list_incluye_origen(self):
        db_catalog.catalog_upsert("centros", "centro-c", {"slug": "centro-c", "name": "Centro C"})
        db_catalog.catalog_upsert("centros", "centro-a", {"slug": "centro-a", "name": "Editado"})
        catalog.invalidate()
        by_slug = {it["slug"]: it for it in catalog.admin_list("centros")}
        self.assertEqual(by_slug["centro-a"]["_origen"], "json+db")
        self.assertEqual(by_slug["centro-b"]["_origen"], "json")
        self.assertEqual(by_slug["centro-c"]["_origen"], "db")

    def test_overlay_row_con_clave_vieja_se_alia_a_la_nueva(self):
        # una fila guardada ANTES del rename de centros-salud.json (jsonb con
        # "nombre") sigue resolviendo con la clave nueva — sin esto, un
        # centro editado antes de esta slice perdería su nombre en el merge
        # (ver catalog._LEGACY_KEY_ALIASES, "centros"). Prueba la entrada
        # REAL de la tabla, no una sintética como TestLegacyKeyAliases.
        db_catalog.catalog_upsert("centros", "centro-a", {"slug": "centro-a", "nombre": "Legado"})
        catalog.invalidate()
        cs = catalog.centers()
        self.assertEqual(cs[0]["name"], "Legado")
        self.assertNotIn("nombre", cs[0])


# ---- overlay legacy-key aliases ---------------------------------------------

class TestLegacyKeyAliases(CatalogoTestCase):
    """merge_with_overlay() must translate an overlay row's OLD Spanish key
    onto a base whose keys have already been renamed to English — see
    catalog._LEGACY_KEY_ALIASES / _apply_legacy_aliases. These stub
    db_catalog.catalog_list directly (same escape hatch TestDbCaida already uses)
    so the test doesn't depend on a real Postgres row, and exercises a
    synthetic "centros: nombre->name" mapping regardless of whether
    centros-salud.json has actually been renamed yet."""

    def setUp(self):
        super().setUp()
        self._orig_catalog_list = db_catalog.catalog_list
        self._orig_aliases = dict(catalog._LEGACY_KEY_ALIASES)
        catalog._LEGACY_KEY_ALIASES["centros"] = {"nombre": "name"}

    def tearDown(self):
        db_catalog.catalog_list = self._orig_catalog_list
        catalog._LEGACY_KEY_ALIASES.clear()
        catalog._LEGACY_KEY_ALIASES.update(self._orig_aliases)
        super().tearDown()

    def _stub_rows(self, rows):
        db_catalog.catalog_list = lambda tipo: rows

    def test_old_key_overlay_row_merges_onto_new_key_base(self):
        base = [{"slug": "centro-a", "name": "Centro A (nuevo)", "bookable": True}]
        self._stub_rows([{"id": "centro-a", "doc": {"nombre": "Centro A (overlay viejo)"},
                           "borrado": False, "created": None, "updated": None}])
        out, meta = catalog.merge_with_overlay("centros", base)
        self.assertEqual(out[0]["name"], "Centro A (overlay viejo)")
        self.assertNotIn("nombre", out[0])
        self.assertEqual(meta["centro-a"]["_origen"], "json+db")

    def test_new_key_overlay_row_passes_through_unchanged(self):
        # a write made through an already-renamed admin form already uses
        # the new key — the alias step must be an identity map for it.
        base = [{"slug": "centro-a", "name": "Centro A (nuevo)"}]
        self._stub_rows([{"id": "centro-a", "doc": {"name": "Centro A (overlay nuevo)"},
                           "borrado": False, "created": None, "updated": None}])
        out, _ = catalog.merge_with_overlay("centros", base)
        self.assertEqual(out[0]["name"], "Centro A (overlay nuevo)")

    def test_mixed_old_and_new_key_rows_resolve_correctly(self):
        base = [
            {"slug": "centro-a", "name": "A base"},
            {"slug": "centro-b", "name": "B base"},
        ]
        self._stub_rows([
            {"id": "centro-a", "doc": {"nombre": "A overlay viejo"},
             "borrado": False, "created": None, "updated": None},
            {"id": "centro-b", "doc": {"name": "B overlay nuevo"},
             "borrado": False, "created": None, "updated": None},
        ])
        out, _ = catalog.merge_with_overlay("centros", base)
        by_slug = {it["slug"]: it for it in out}
        self.assertEqual(by_slug["centro-a"]["name"], "A overlay viejo")
        self.assertEqual(by_slug["centro-b"]["name"], "B overlay nuevo")

    def test_no_alias_registered_for_type_is_a_no_op(self):
        # "servicios" has no entry in _LEGACY_KEY_ALIASES (still Spanish
        # base, not migrated yet) — the overlay doc must pass through
        # byte-identical, same behaviour as before this feature existed.
        base = [{"id": "serv-1", "nombre": "Servicio Uno"}]
        self._stub_rows([{"id": "serv-1", "doc": {"nombre": "Servicio Uno Editado"},
                           "borrado": False, "created": None, "updated": None}])
        out, _ = catalog.merge_with_overlay("servicios", base)
        self.assertEqual(out[0]["nombre"], "Servicio Uno Editado")

    def test_new_id_from_db_only_also_gets_aliased(self):
        # an id NOT present in the base (created straight from an
        # old-style admin write) goes through the "nuevos" branch of
        # merge_with_overlay — must be aliased too, not just overrides.
        base = [{"slug": "centro-a", "name": "A"}]
        self._stub_rows([{"id": "centro-c", "doc": {"nombre": "C nuevo desde admin viejo"},
                           "borrado": False, "created": None, "updated": None}])
        out, meta = catalog.merge_with_overlay("centros", base)
        by_slug = {it["slug"]: it for it in out}
        self.assertEqual(by_slug["centro-c"]["name"], "C nuevo desde admin viejo")
        self.assertEqual(meta["centro-c"]["_origen"], "db")


class TestDbCaida(CatalogoTestCase):
    def test_db_caida_sirve_solo_json(self):
        orig = db_catalog.catalog_list

        def _boom(tipo):
            raise db.DbUnavailable("simulado")

        db_catalog.catalog_list = _boom
        try:
            cs = catalog.centers()  # no debe levantar
            self.assertEqual([c["slug"] for c in cs], ["centro-a", "centro-b"])
        finally:
            db_catalog.catalog_list = orig

    def test_admin_list_estricto_deja_pasar_dbunavailable(self):
        orig = db_catalog.catalog_list

        def _boom(tipo):
            raise db.DbUnavailable("simulado")

        db_catalog.catalog_list = _boom
        try:
            with self.assertRaises(db.DbUnavailable):
                catalog.admin_list("centros")
        finally:
            db_catalog.catalog_list = orig


# ---- validación --------------------------------------------------------

class TestValidacion(CatalogoTestCase):
    def test_centro_genera_slug_del_nombre(self):
        doc, err = catalog.validate("centros", {"name": "Centro Nuevo"}, is_new=True)
        self.assertIsNone(err)
        self.assertEqual(doc["slug"], "centro-nuevo")

    def test_centro_institucion_de_hsi_es_un_numero(self):
        doc, err = catalog.validate("centros", {"name": "CAPS", "hsi_institution": " 10 86 "}, is_new=True)
        self.assertIsNone(err)
        self.assertEqual(doc["hsi_institution"], "1086")
        doc, err = catalog.validate("centros", {"name": "CAPS", "hsi_institution": 1077}, is_new=True)
        self.assertEqual(doc["hsi_institution"], "1077")
        # One spelling per institution: leading zeros are not part of the number.
        doc, err = catalog.validate("centros", {"name": "CAPS", "hsi_institution": "01086"}, is_new=True)
        self.assertIsNone(err)
        self.assertEqual(doc["hsi_institution"], "1086")
        doc, err = catalog.validate("centros", {"name": "CAPS", "hsi_institution": ""}, is_new=True)
        self.assertIsNone(err)
        self.assertNotIn("hsi_institution", doc)
        doc, err = catalog.validate("centros", {"name": "CAPS", "hsi_institution": "Hospital"}, is_new=True)
        self.assertIsNone(doc)
        self.assertIn("HSI", err)
        # Unicode digits pass str.isdigit() but are no HSI address; nor is an
        # all-zero number.
        for bad in ("١٠٨٦", "10.5", "000", "1234567890"):
            doc, err = catalog.validate("centros", {"name": "CAPS", "hsi_institution": bad}, is_new=True)
            self.assertIsNone(doc, bad)
            self.assertIn("HSI", err)

    def test_centro_sin_nombre_falla(self):
        doc, err = catalog.validate("centros", {}, is_new=True)
        self.assertIsNone(doc)
        self.assertIn("nombre", err)

    def test_servicio_modo_invalido(self):
        doc, err = catalog.validate(
            "servicios",
            {"nombre": "X", "centro": "centro-a", "modo": "no-existe"}, is_new=True)
        self.assertIsNone(doc)
        self.assertIn("modo", err)

    def test_servicio_centro_inexistente(self):
        doc, err = catalog.validate(
            "servicios",
            {"nombre": "X", "centro": "no-existe", "modo": "walkin"}, is_new=True)
        self.assertIsNone(doc)
        self.assertIn("centro", err)

    def test_servicio_walkin_con_agenda_se_rechaza(self):
        doc, err = catalog.validate(
            "servicios",
            {"nombre": "X", "centro": "centro-a", "modo": "walkin",
             "agenda": {"dias": [1], "desde": "09:00", "hasta": "12:00", "cada_min": 20}},
            is_new=True)
        self.assertIsNone(doc)
        self.assertIn("walk-in", err)

    def test_servicio_solicitud_con_orden_admite_agenda(self):
        doc, err = catalog.validate(
            "servicios",
            {"nombre": "X", "centro": "centro-a", "modo": "solicitud_con_orden",
             "agenda": {"dias": [1, 4], "desde": "09:00", "hasta": "12:00", "cada_min": 20}},
            is_new=True)
        self.assertIsNone(err)
        self.assertEqual(doc["agenda"]["dias"], [1, 4])

    def test_servicio_solicitud_sin_orden_admite_agenda_franjas(self):
        # modo `solicitud` (turno programado sin orden, Detalle de turnos
        # 12-07-26) + forma multi-franja {franjas} — la misma que un médico.
        doc, err = catalog.validate(
            "servicios",
            {"nombre": "X", "centro": "centro-a", "modo": "solicitud",
             "agenda": {"franjas": [
                 {"dias": [2], "desde": "08:00", "hasta": "12:00", "cada_min": 20},
                 {"dias": [4], "desde": "14:00", "hasta": "17:00", "cada_min": 20}]}},
            is_new=True)
        self.assertIsNone(err)
        self.assertEqual(len(doc["agenda"]["franjas"]), 2)
        self.assertEqual(doc["agenda"]["excepciones"], [])

    def test_servicio_solicitud_sin_orden_walkin_sigue_bloqueado(self):
        doc, err = catalog.validate(
            "servicios",
            {"nombre": "X", "centro": "centro-a", "modo": "walkin",
             "agenda": {"franjas": [{"dias": [2], "desde": "08:00", "hasta": "12:00"}]}},
            is_new=True)
        self.assertIsNone(doc)
        self.assertIn("walk-in", err)

    # --- médicos: forma vieja plana {centro, agenda} sigue aceptándose (back-compat,
    # se traduce sola a centros=[{centro, franjas:[agenda], excepciones:[]}]) ---

    def test_agenda_mal_formada_dias_fuera_de_rango(self):
        # dias usa Date.getDay() (0=domingo..6=sábado) — 7 y -1 están fuera de rango.
        doc, err = catalog.validate(
            "medicos",
            {"nombre": "Juan Perez", "especialidad": "Clínica", "centro": "centro-a",
             "agenda": {"dias": [-1, 7], "desde": "09:00", "hasta": "12:00", "cada_min": 20}},
            is_new=True)
        self.assertIsNone(doc)
        self.assertIn("franja", err)

    def test_agenda_domingo_0_es_valido(self):
        doc, err = catalog.validate(
            "medicos",
            {"nombre": "Juan Perez", "especialidad": "Clínica", "centro": "centro-a",
             "agenda": {"dias": [0], "desde": "09:00", "hasta": "12:00", "cada_min": 20}},
            is_new=True)
        self.assertIsNone(err)
        self.assertEqual(doc["agenda"]["franjas"][0]["dias"], [0])
        self.assertEqual(doc["centros"][0]["franjas"][0]["dias"], [0])

    def test_agenda_mal_formada_horario(self):
        doc, err = catalog.validate(
            "medicos",
            {"nombre": "Juan Perez", "especialidad": "Clínica", "centro": "centro-a",
             "agenda": {"dias": [1], "desde": "9:00", "hasta": "12:00", "cada_min": 20}},
            is_new=True)
        self.assertIsNone(doc)
        self.assertIn("franja", err)

    def test_agenda_cada_min_no_positivo(self):
        doc, err = catalog.validate(
            "medicos",
            {"nombre": "Juan Perez", "especialidad": "Clínica", "centro": "centro-a",
             "agenda": {"dias": [1], "desde": "09:00", "hasta": "12:00", "cada_min": 0}},
            is_new=True)
        self.assertIsNone(doc)
        self.assertIn("franja", err)

    def test_medico_requiere_centro_existente(self):
        doc, err = catalog.validate(
            "medicos", {"nombre": "Juan Perez", "especialidad": "Clínica", "centro": "no-existe"},
            is_new=True)
        self.assertIsNone(doc)
        self.assertIn("centro", err)

    def test_agenda_desde_debe_ser_anterior_a_hasta(self):
        # ventana invertida (12:00→09:00): produciría cero slots silenciosos.
        doc, err = catalog.validate(
            "medicos",
            {"nombre": "Juan Perez", "especialidad": "Clínica", "centro": "centro-a",
             "agenda": {"dias": [1], "desde": "12:00", "hasta": "09:00", "cada_min": 20}},
            is_new=True)
        self.assertIsNone(doc)
        self.assertIn("anterior", err)
        # ventana vacía (desde == hasta) también se rechaza
        doc, err = catalog.validate(
            "medicos",
            {"nombre": "Juan Perez", "especialidad": "Clínica", "centro": "centro-a",
             "agenda": {"dias": [1], "desde": "09:00", "hasta": "09:00", "cada_min": 20}},
            is_new=True)
        self.assertIsNone(doc)
        self.assertIn("anterior", err)

    def test_medico_especialidad_desconocida_se_rechaza(self):
        doc, err = catalog.validate(
            "medicos",
            {"nombre": "Juan Perez", "especialidad": "Especialidad inventada", "centro": "centro-a"},
            is_new=True)
        self.assertIsNone(doc)
        self.assertIn("especialidad", err)

    def test_medico_cluster_se_deriva_de_especialidad_nunca_del_cliente(self):
        # el cliente manda un cluster que no corresponde a la especialidad —
        # el server lo ignora y deriva el correcto desde especialidades.json.
        doc, err = catalog.validate(
            "medicos",
            {"nombre": "Juan Perez", "especialidad": "Cardiología", "cluster": "otras",
             "centro": "centro-a"},
            is_new=True)
        self.assertIsNone(err)
        self.assertEqual(doc["cluster"], "cardiologia")

    def test_medico_segunda_especialidad_deriva_su_cluster_del_catalogo(self):
        doc, err = catalog.validate(
            "medicos",
            {"nombre": "Juan Perez", "especialidad": "Clínica", "especialidad_2": "Cardiología",
             "cluster_2": "otras", "centro": "centro-a"},
            is_new=True)
        self.assertIsNone(err)
        self.assertEqual(doc["cluster"], "clinica")
        self.assertEqual(doc["especialidad_2"], "Cardiología")
        self.assertEqual(doc["cluster_2"], "cardiologia")
        self.assertEqual(catalog.doctor_clusters(doc), ["clinica", "cardiologia"])

    def test_medico_segunda_especialidad_vacia_se_quita_con_su_cluster(self):
        doc, err = catalog.validate(
            "medicos",
            {"nombre": "Juan Perez", "especialidad": "Clínica", "especialidad_2": "",
             "cluster_2": "cardiologia", "centro": "centro-a"},
            is_new=True)
        self.assertIsNone(err)
        self.assertNotIn("especialidad_2", doc)
        self.assertNotIn("cluster_2", doc)
        self.assertEqual(catalog.doctor_clusters(doc), ["clinica"])

    def test_medico_segunda_especialidad_se_rechaza_si_no_sirve(self):
        base = {"nombre": "Juan Perez", "centro": "centro-a"}
        for extra, pieza in (
                ({"especialidad": "Clínica", "especialidad_2": "Inventada"}, "desconocida"),
                ({"especialidad": "Clínica", "especialidad_2": "Clínica"}, "distinta"),
                ({"especialidad": "", "especialidad_2": "Cardiología"}, "primera")):
            doc, err = catalog.validate("medicos", dict(base, **extra), is_new=True)
            self.assertIsNone(doc, extra)
            self.assertIn(pieza, err)

    def test_doctor_clusters_sin_repetir_ni_vacios(self):
        self.assertEqual(catalog.doctor_clusters(None), [])
        self.assertEqual(catalog.doctor_clusters({"cluster": "a", "cluster_2": "a"}), ["a"])
        self.assertEqual(catalog.doctor_clusters({"cluster": "", "cluster_2": "b"}), ["b"])

    def test_medico_nombre_pila_y_apellido_componen_nombre(self):
        doc, err = catalog.validate(
            "medicos",
            {"nombre_pila": "Juan", "apellido": "Perez", "especialidad": "Clínica",
             "centro": "centro-a"},
            is_new=True)
        self.assertIsNone(err)
        self.assertEqual(doc["nombre"], "Juan Perez")
        self.assertEqual(doc["id"], "juan-perez")

    def test_medico_centros_multiples_con_franjas_propias(self):
        doc, err = catalog.validate(
            "medicos",
            {"nombre": "Juan Perez", "especialidad": "Clínica",
             "centros": [
                 {"centro": "centro-a",
                  "franjas": [{"dias": [1, 4], "desde": "09:00", "hasta": "12:00", "cada_min": 20},
                              {"dias": [2], "desde": "14:00", "hasta": "17:00", "cada_min": 30}]},
                 {"centro": "centro-b",
                  "franjas": [{"dias": [3], "desde": "08:00", "hasta": "10:00", "cada_min": 20}]},
             ]},
            is_new=True)
        self.assertIsNone(err)
        self.assertEqual(len(doc["centros"]), 2)
        self.assertEqual(len(doc["centros"][0]["franjas"]), 2)
        # centro/agenda planos = el centro PRINCIPAL (centros[0]), para patient_web/site_*.py
        self.assertEqual(doc["centro"], "centro-a")
        self.assertEqual(len(doc["agenda"]["franjas"]), 2)

    def test_medico_centro_invalido_dentro_de_centros(self):
        doc, err = catalog.validate(
            "medicos",
            {"nombre": "Juan Perez", "especialidad": "Clínica",
             "centros": [{"centro": "no-existe", "franjas": []}]},
            is_new=True)
        self.assertIsNone(doc)
        self.assertIn("centro", err)

    def test_medico_excepcion_cerrado_valida(self):
        doc, err = catalog.validate(
            "medicos",
            {"nombre": "Juan Perez", "especialidad": "Clínica",
             "centros": [{"centro": "centro-a",
                          "franjas": [{"dias": [1], "desde": "09:00", "hasta": "12:00", "cada_min": 20}],
                          "excepciones": [{"fecha": "2026-08-01", "tipo": "cerrado", "motivo": "Vacaciones"}]}]},
            is_new=True)
        self.assertIsNone(err)
        self.assertEqual(doc["centros"][0]["excepciones"][0]["tipo"], "cerrado")
        self.assertEqual(doc["agenda"]["excepciones"][0]["fecha"], "2026-08-01")

    def test_medico_excepcion_extra_requiere_horario_valido(self):
        doc, err = catalog.validate(
            "medicos",
            {"nombre": "Juan Perez", "especialidad": "Clínica",
             "centros": [{"centro": "centro-a", "franjas": [],
                          "excepciones": [{"fecha": "2026-08-01", "tipo": "extra",
                                           "desde": "12:00", "hasta": "09:00"}]}]},
            is_new=True)
        self.assertIsNone(doc)
        self.assertIn("anterior", err)

    def test_medico_excepcion_fecha_mal_formada(self):
        doc, err = catalog.validate(
            "medicos",
            {"nombre": "Juan Perez", "especialidad": "Clínica",
             "centros": [{"centro": "centro-a", "franjas": [],
                          "excepciones": [{"fecha": "01/08/2026", "tipo": "cerrado"}]}]},
            is_new=True)
        self.assertIsNone(doc)
        self.assertIn("fecha", err)

    def test_metadatos_de_lectura_se_pelan_del_doc(self):
        # el cliente re-manda el item de admin_list tal cual (Object.assign),
        # con _origen/_borrado adentro — validar() debe pelarlos, no persistirlos.
        doc, err = catalog.validate(
            "centros",
            {"name": "Centro A", "slug": "centro-a",
             "_origen": "json+db", "_borrado": False}, is_new=False)
        self.assertIsNone(err)
        self.assertNotIn("_origen", doc)
        self.assertNotIn("_borrado", doc)
        self.assertEqual(doc["name"], "Centro A")

    def test_passthrough_de_campos_desconocidos(self):
        doc, err = catalog.validate(
            "servicios",
            {"nombre": "X", "centro": "centro-a", "modo": "walkin",
             "fuente_nombre": "pinamar.gob.ar", "demo": True}, is_new=True)
        self.assertIsNone(err)
        self.assertEqual(doc["fuente_nombre"], "pinamar.gob.ar")
        self.assertIs(doc["demo"], True)

    def test_edicion_ignora_slug_del_body_cuando_el_router_lo_fuerza(self):
        # el router (admin_content) hace doc[id_key] = <id del path> ANTES de
        # llamar a validar(); acá sólo probamos que validar() no lo pisa si ya
        # viene seteado.
        doc, err = catalog.validate(
            "centros", {"name": "Centro A", "slug": "centro-a"}, is_new=False)
        self.assertIsNone(err)
        self.assertEqual(doc["slug"], "centro-a")


@reference_pack_case
class TestOfficialSources(unittest.TestCase):
    """official_sources() (Phase 2c, 2026-07-30): was two independently
    hardcoded copies (here and knowledge.py) of the same tuple, now ONE
    pack-fed list unioning packs/ar/, packs/ar-b/ and packs/ar-b-pinamar/'s
    own trusted-domains.json. Against the REAL checkout's packs/ (no
    monkeypatching), same spirit as test_tz.py's TestTzDefaults."""

    def test_unions_all_three_levels_of_the_real_chain(self):
        sources = catalog.official_sources()
        for expected in ("pinamar.gob.ar", "gba.gob.ar", "ms.gba.gov.ar",
                          "argentina.gob.ar", "servicios.infoleg.gob.ar"):
            self.assertIn(expected, sources)

    def test_url_oficial_accepts_pinamar_domain(self):
        self.assertTrue(catalog._url_oficial("https://pinamar.gob.ar/salud"))

    def test_url_oficial_rejects_unknown_domain(self):
        self.assertFalse(catalog._url_oficial("https://example.com/salud"))

    def test_knowledge_reads_the_same_list_catalog_does(self):
        from app.patient_web import knowledge
        self.assertIs(knowledge.catalog.official_sources, catalog.official_sources)
        self.assertTrue(knowledge._official_url("https://pinamar.gob.ar/salud"))
        self.assertFalse(knowledge._official_url("https://example.com/salud"))


class TestNombresLegibles(unittest.TestCase):
    """Un turno guarda el id del servicio y el slug del centro para siempre;
    el catálogo no. Cuando el servicio se borra —o la fila viene de otro
    paquete de datos— la mesa de entrada veía «cardiologia-sampleton-hospital»
    en la columna Servicio (responsable del producto, 2026-08-01). Nunca un id en pantalla."""

    def test_servicio_borrado_del_catalogo_sigue_teniendo_nombre(self):
        nombre = catalog.service_name("cardiologia-sampleton-hospital")
        self.assertNotIn("-", nombre)
        self.assertEqual(nombre, "Cardiología")

    def test_toma_la_especialidad_mas_larga_que_matchea(self):
        # "medicina-general-…" no puede resolverse como "Medicina" a secas.
        self.assertEqual(catalog.service_name("medicina-general-riverside-clinic"),
                         "Medicina general")

    def test_id_desconocido_se_humaniza_en_vez_de_mostrarse_crudo(self):
        self.assertEqual(catalog.service_name("servicio-raro-xyz"),
                         "Servicio raro xyz")

    def test_centro_desconocido_se_humaniza(self):
        self.assertEqual(catalog.center_name("centro-que-no-existe"),
                         "Centro que no existe")

    def test_sin_id_no_inventa_nada(self):
        self.assertEqual(catalog.service_name(""), "")
        self.assertEqual(catalog.center_name(None), "")

    def _medicos(self, tipo, incluir_borrados=False, strict=False, incluir_incompletos=None):
        if tipo != "medicos":
            return [], {}
        vivos = [{"id": "ana-perez", "nombre": "ANA PÉREZ"},
                 {"id": "sin-onboarding", "nombre": "Bruno Díaz"}]
        if not incluir_incompletos:
            vivos = vivos[:1]
        borrados = [{"id": "nancy-pellegrini", "nombre": "Nancy Pellegrini"}]
        return (vivos + borrados if incluir_borrados else vivos), {}

    def test_medico_deshabilitado_o_incompleto_sigue_teniendo_nombre(self):
        # A request keeps the doctor's id after the doctor is disabled (or
        # before onboarding finishes); the desk still reads who it was.
        with mock.patch.object(catalog, "_merged", side_effect=self._medicos):
            self.assertEqual(catalog.doctor_name("ana-perez"), "ANA PÉREZ")
            self.assertEqual(catalog.doctor_name("nancy-pellegrini"), "Nancy Pellegrini")
            self.assertEqual(catalog.doctor_name("sin-onboarding"), "Bruno Díaz")
            self.assertEqual(catalog.doctor_name("medico-de-otro-paquete"),
                             "Medico de otro paquete")
            self.assertEqual(catalog.doctor_name(""), "")

    def test_la_fila_del_panel_nombra_al_medico_aunque_solo_tenga_el_slot(self):
        from app.modules.turnos import booking_requests
        with mock.patch.object(catalog, "_merged", side_effect=self._medicos):
            slot = booking_requests._admin_row({"id": 1, "slot_medico_id": "nancy-pellegrini"})
            asignado = booking_requests._admin_row(
                {"id": 2, "medico_asignado_id": "ana-perez", "slot_medico_id": "nancy-pellegrini"})
            sin_medico = booking_requests._admin_row({"id": 3})
        self.assertEqual(slot["medico_nombre"], "Nancy Pellegrini")
        self.assertEqual(asignado["medico_nombre"], "Ana Pérez")
        self.assertEqual(sin_medico["medico_nombre"], "")

    def test_la_cola_del_dia_nunca_publica_un_id(self):
        from app.modules.turnos import doctor_appointments as da
        self.assertEqual(da._service_name("pediatria-riverside-clinic"), "Pediatría")
        self.assertEqual(da._center_name("centro-que-no-existe"),
                         "Centro que no existe")


if __name__ == "__main__":
    unittest.main()


class TestServiceClosurePersistence(CatalogoTestCase):
    def test_walkin_closures_are_validated_without_an_appointment_agenda(self):
        doc = {"id": "serv-1", "nombre": "Clinic", "centro": "centro-a", "modo": "walkin",
               "excepciones": [{"tipo": "cerrado", "desde_fecha": "2026-11-01", "hasta_fecha": ""}]}
        result, error = catalog.validate("servicios", doc, is_new=False)
        self.assertIsNone(result)
        self.assertTrue(error)

    def test_walkin_closure_is_normalized_and_retained(self):
        doc = {"id": "serv-1", "nombre": "Clinic", "centro": "centro-a", "modo": "walkin",
               "excepciones": [{"tipo": "cerrado", "fecha": "2026-11-01", "motivo": " Licencia "}]}
        result, error = catalog.validate("servicios", doc, is_new=False)
        self.assertIsNone(error)
        self.assertNotIn("agenda", result)
        self.assertEqual(result["excepciones"][0]["motivo"], "Licencia")

    def test_bookable_closure_without_weekdays_is_valid(self):
        closure = {"tipo": "cerrado", "fecha": "2026-11-01"}
        result, error = catalog.validate("servicios", {"id": "serv-2", "nombre": "Clinic", "centro": "centro-a", "modo": "solicitud", "agenda": {"franjas": [], "excepciones": [closure]}}, is_new=False)
        self.assertIsNone(error)
        self.assertEqual(result["agenda"]["excepciones"], [closure])
