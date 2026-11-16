"""test_chat_tools_i18n.py — chat_tools.py's catalog search in English.

`_tool_search` / `_tool_search_doctors` read catalog.services()/doctors()
straight off the pack (never through pack_l10n.localize, unlike
patient_web/site_*.py's browser-facing loaders) and matched with Spanish-only
suffix strippers and stopwords. An English visitor's query — "is there a
gynecologist on Thursday?" — never matched anything, and even when it did
(by accident, via `palabras`) the tool handed the model Spanish service
names to reason over.

This proves, directly against the tool functions (no HTTP, no model):
  - an ENGLISH query finds the service/doctor via the localized name, with
    English suffix folding (-ologist -> -ology, plural -s/-es/-ies, British
    -> American spelling);
  - the SAME Spanish query still finds exactly what it found before (the
    Spanish path is untouched — pack_l10n.localize() is a no-op when the
    active language matches the pack's own language);
  - the "medicina general" fallback entry path (a code-coupled identity
    check against the RAW Spanish name) still fires under an English active
    language, and its displayed label comes back localized.

Uses a dedicated throwaway database (translate.py's cache lives in
Postgres) — skips with a clear message if Postgres isn't reachable, same
pattern as test_translate.py.

Run: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_chat_tools_i18n.py"
"""

import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
config.ENV_PATH = ""  # no leer el .env real del checkout (idem test_translate.py)

from app.core import db_accounts  # noqa: E402
from app.core import i18n  # noqa: E402
from app.patient_web import catalog     # noqa: E402
from app.modules.chat import chat_tools  # noqa: E402
from app.modules.chat import chat_agent  # noqa: E402
from app.core import pack_l10n  # noqa: E402
from app.core import translate  # noqa: E402

import _dbtest    # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_chat_tools_i18n",
                          seed=lambda: db_accounts.get_user("__forzar_esquema__"))

HAVE_DB = False
DB_SKIP_REASON = ""


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


class _FakeHandler:
    """Igual que test_i18n.py's FakeHandler — sólo lo que i18n.resolve_lang
    necesita (headers con Cookie)."""
    def __init__(self, lang=None):
        self.path = "/"
        self.command = "GET"
        self.headers = {"Cookie": f"lang={lang}"} if lang else {}
        self.wfile = io.BytesIO()

    def send_response(self, code):
        pass

    def send_header(self, k, v):
        pass

    def end_headers(self):
        pass


_SERVICIOS = [
    {"id": "gine-hospital", "nombre": "Ginecología", "centro": "hospital",
     "modo": "solicitud_con_orden", "categoria": "especialidades",
     "cluster": "clinica", "palabras": ["mujer", "papanicolau"]},
    {"id": "lab-hospital", "nombre": "Análisis de sangre", "centro": "hospital",
     "modo": "solicitud", "categoria": "estudios", "cluster": "diagnostico",
     "palabras": ["laboratorio", "extraccion"]},
    {"id": "eco-hospital", "nombre": "Ecografía", "centro": "hospital",
     "modo": "solicitud", "categoria": "estudios", "cluster": "diagnostico",
     "palabras": ["eco", "ultrasonido"]},
    {"id": "clinica-hospital", "nombre": "Medicina general", "centro": "hospital",
     "modo": "walkin", "categoria": "especialidades",
     "cluster": "clinica", "palabras": ["generalista"],
     "horario": "Lunes a viernes de 08:00 a 12:00"},
]

_CENTROS = [{"slug": "hospital", "name": "Hospital de Pinamar"}]

_MEDICOS = [
    {"id": "med-1", "nombre": "Ana Gómez", "nombre_pila": "Ana",
     "apellido": "Gómez", "especialidad": "Ginecología",
     "centro": "hospital", "centros": [{"centro": "hospital"}]},
]


class _CatalogFixture(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        self._orig_services = catalog.services
        self._orig_doctors = catalog.doctors
        self._orig_centers = catalog.centers
        catalog.services = lambda: [dict(s) for s in _SERVICIOS]
        catalog.doctors = lambda: [dict(m) for m in _MEDICOS]
        catalog.centers = lambda: [dict(c) for c in _CENTROS]
        pack_l10n.invalidate_memo()
        # Caches are dropped after the environment is back (cleanups run last-in first-out).
        self.addCleanup(pack_l10n.invalidate_memo)
        self.addCleanup(i18n.invalidate)
        self.addCleanup(config._reset_cache_para_tests)
        _env.set_env(self, LANGUAGES="es,en")
        config._reset_cache_para_tests()
        i18n.invalidate()
        with __import__("psycopg").connect(_db.test_url, autocommit=True) as c:
            with c.cursor() as cur:
                cur.execute("DELETE FROM translations")
        # translate also holds a 30 s process-wide copy of these rows.
        # invalidate_memo() above drops it, but BEFORE the DELETE: a read in
        # between (another thread of the suite's one process) copied the
        # previous test's «Gynecology» back in (TASK-19). Dropping it after
        # the table is empty leaves nothing stale to copy.
        translate.invalidate_rows()
        self._token = None

    def tearDown(self):
        catalog.services = self._orig_services
        catalog.doctors = self._orig_doctors
        catalog.centers = self._orig_centers
        if self._token is not None:
            i18n.unbind(self._token)

    def _bind(self, lang):
        lang_val, token = i18n.bind(_FakeHandler(lang))
        self._token = token
        return lang_val

    def _override(self, kind, ident, field, en_text):
        translate.set_override(pack_l10n.key(kind, ident, field), "en", en_text)


class BusquedaServiciosEnIngles(_CatalogFixture):
    def test_gynecologist_encuentra_ginecologia_por_nombre_localizado(self):
        self._override("servicios", "gine-hospital", "nombre", "Gynecology")
        self._bind("en")
        out = chat_tools._tool_search(None, {"query": "gynecologist"}, None)
        ids = [r["servicio_id"] for r in out["resultados"]]
        self.assertIn("gine-hospital", ids)
        fila = next(r for r in out["resultados"] if r["servicio_id"] == "gine-hospital")
        self.assertEqual(fila["nombre"], "Gynecology")

    def test_blood_test_encuentra_analisis_de_sangre(self):
        self._override("servicios", "lab-hospital", "nombre", "Blood tests")
        self._bind("en")
        out = chat_tools._tool_search(None, {"query": "blood test"}, None)
        ids = [r["servicio_id"] for r in out["resultados"]]
        self.assertIn("lab-hospital", ids)
        fila = next(r for r in out["resultados"] if r["servicio_id"] == "lab-hospital")
        self.assertEqual(fila["nombre"], "Blood tests")

    def test_ultrasound_encuentra_ecografia(self):
        self._override("servicios", "eco-hospital", "nombre", "Ultrasound")
        self._bind("en")
        out = chat_tools._tool_search(None, {"query": "ultrasound"}, None)
        ids = [r["servicio_id"] for r in out["resultados"]]
        self.assertIn("eco-hospital", ids)
        fila = next(r for r in out["resultados"] if r["servicio_id"] == "eco-hospital")
        self.assertEqual(fila["nombre"], "Ultrasound")

    def test_sin_traduccion_en_cache_degrada_al_nombre_del_pack(self):
        # Ninguna traducción cacheada para "eco-hospital" -> sigue en español,
        # y "ecografia" (español) todavía tiene que encontrarlo.
        self._bind("en")
        out = chat_tools._tool_search(None, {"query": "ecografia"}, None)
        ids = [r["servicio_id"] for r in out["resultados"]]
        self.assertIn("eco-hospital", ids)
        fila = next(r for r in out["resultados"] if r["servicio_id"] == "eco-hospital")
        self.assertEqual(fila["nombre"], "Ecografía")

    def test_medicina_general_sigue_siendo_la_puerta_de_entrada_en_ingles(self):
        # "medicina general" es un chequeo de identidad contra el nombre
        # RAW — tiene que seguir andando aunque el nombre mostrado esté en
        # inglés (bug real que este cambio podía introducir si localizaba
        # antes de esa comparación).
        self._override("servicios", "gine-hospital", "nombre", "Gynecology")
        translate.set_override(
            "chat_tools.especialidad_medicina_general", "en", "General Medicine")
        self._bind("en")
        out = chat_tools._tool_search(None, {"query": "gynecologist"}, None)
        self.assertTrue(all(r.get("requiere_orden") for r in out["resultados"]))
        puerta = out.get("puerta_de_entrada")
        self.assertIsNotNone(puerta)
        self.assertEqual(puerta["especialidad"], "General Medicine")

    def test_label_de_medicina_general_es_una_key_unica_no_por_servicio(self):
        # Dos filas "Medicina general" (dos centros) no deben poder cachear
        # traducciones DISTINTAS que hagan el label depender de cuál llegó
        # primera a `bookable` (hallazgo de code review, 2026-08-05).
        segunda = dict(_SERVICIOS[3])
        segunda["id"] = "clinica-hospital-2"
        segunda["centro"] = "hospital"
        catalog.services = lambda: [dict(s) for s in _SERVICIOS] + [segunda]
        self._override("servicios", "gine-hospital", "nombre", "Gynecology")
        self._override("servicios", "clinica-hospital", "nombre", "General Medicine A")
        self._override("servicios", "clinica-hospital-2", "nombre", "General Medicine B")
        translate.set_override(
            "chat_tools.especialidad_medicina_general", "en", "General Medicine")
        self._bind("en")
        out = chat_tools._tool_search(None, {"query": "gynecologist"}, None)
        puerta = out.get("puerta_de_entrada")
        self.assertEqual(puerta["especialidad"], "General Medicine")

    def test_camino_en_espanol_no_se_mueve_un_byte(self):
        # Sin bind (o bind explícito a "es"): mismo comportamiento de
        # siempre, nombre en español, pack_l10n.localize() es un no-op.
        self._override("servicios", "gine-hospital", "nombre", "Gynecology")
        self._bind("es")
        out = chat_tools._tool_search(None, {"query": "ginecologia"}, None)
        ids = [r["servicio_id"] for r in out["resultados"]]
        self.assertIn("gine-hospital", ids)
        fila = next(r for r in out["resultados"] if r["servicio_id"] == "gine-hospital")
        self.assertEqual(fila["nombre"], "Ginecología")


class BusquedaMedicosEnIngles(_CatalogFixture):
    def test_gynecologist_encuentra_a_la_medica_por_especialidad_localizada(self):
        self._override("medicos", "med-1", "especialidad", "Gynecology")
        self._bind("en")
        out = chat_tools._tool_search_doctors(None, {"query": "is there a gynecologist"}, None)
        ids = [r["medico_id"] for r in out["resultados"]]
        self.assertIn("med-1", ids)
        fila = next(r for r in out["resultados"] if r["medico_id"] == "med-1")
        self.assertEqual(fila["especialidad"], "Gynecology")
        # El nombre de la persona NUNCA se traduce.
        self.assertEqual(fila["nombre"], "Ana Gómez")

    def test_busqueda_por_nombre_propio_sigue_andando_en_ingles(self):
        self._bind("en")
        out = chat_tools._tool_search_doctors(None, {"query": "gomez"}, None)
        ids = [r["medico_id"] for r in out["resultados"]]
        self.assertIn("med-1", ids)

    def test_camino_en_espanol_no_se_mueve_un_byte(self):
        self._override("medicos", "med-1", "especialidad", "Gynecology")
        self._bind("es")
        out = chat_tools._tool_search_doctors(None, {"query": "ginecologa"}, None)
        ids = [r["medico_id"] for r in out["resultados"]]
        self.assertIn("med-1", ids)
        fila = next(r for r in out["resultados"] if r["medico_id"] == "med-1")
        self.assertEqual(fila["especialidad"], "Ginecología")


class UnaLecturaConcurrenteNoDejaElGynecologyAnterior(_CatalogFixture):
    """TASK-19: the backend suite shares ONE process, and translate keeps a
    30 s process-wide copy of the rows. setUp emptied that copy BEFORE it
    deleted the rows, so any read in between (another thread of the same
    process) copied the previous test's «Gynecology» back in, and the
    "no cached translation" case then read it — pass or fail by timing.
    This replays that exact interleaving instead of waiting for it."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        # what the previous test of this module leaves in the table
        translate.set_override(pack_l10n.key("especialidades", "Ginecología", "nombre"),
                               "en", "Gynecology")
        original = pack_l10n.invalidate_memo

        def then_a_concurrent_read():
            original()
            translate.warm_rows("en")
        pack_l10n.invalidate_memo = then_a_concurrent_read
        try:
            super().setUp()
        finally:
            pack_l10n.invalidate_memo = original
        self._orig_specialties = catalog.specialties
        catalog.specialties = lambda: [{"nombre": "Ginecología", "cluster": "gineco"}]

    def tearDown(self):
        catalog.specialties = self._orig_specialties
        super().tearDown()

    def test_sin_traduccion_en_esta_base_degrada_al_espanol(self):
        self._bind("en")
        self.assertEqual(chat_agent._specialty_names(), ["Ginecología"])


class ListaDeEspecialidadesEnElSystemPrompt(_CatalogFixture):
    """chat_agent._specialty_names() feeds the system prompt's closed
    specialty list on every turn (chat_agent.py:311-320) — another raw
    catalog.specialties() read that never went through translation."""

    def setUp(self):
        super().setUp()
        self._orig_specialties = catalog.specialties
        catalog.specialties = lambda: [
            {"nombre": "Ginecología", "cluster": "gineco"},
            {"nombre": "Medicina general", "cluster": "clinica"},
        ]

    def tearDown(self):
        catalog.specialties = self._orig_specialties
        super().tearDown()

    def test_degrada_al_espanol_sin_traduccion_cacheada(self):
        self._bind("en")
        self.assertIn("Ginecología", chat_agent._specialty_names())

    def test_usa_la_traduccion_cacheada_si_existe(self):
        translate.set_override(
            pack_l10n.key("especialidades", "Ginecología", "nombre"),
            "en", "Gynecology")
        self._bind("en")
        names = chat_agent._specialty_names()
        self.assertIn("Gynecology", names)
        self.assertNotIn("Ginecología", names)

    def test_camino_en_espanol_no_se_mueve_un_byte(self):
        translate.set_override(
            pack_l10n.key("especialidades", "Ginecología", "nombre"),
            "en", "Gynecology")
        self._bind("es")
        self.assertEqual(chat_agent._specialty_names(),
                          ["Ginecología", "Medicina general"])


class NombreDeCentroEnElIdiomaDeLaCharla(_CatalogFixture):
    """centros.name es contenido traducible desde 2026-09-11 (fuga residual
    2: los nombres en inglés del pack demo caían crudos en respuestas ru/pt).
    El chat arma sus chips y tool output con el nombre LOCALIZADO, igual que
    el resto del contenido del pack."""

    def test_tool_output_lleva_el_centro_localizado(self):
        translate.set_override(pack_l10n.key("centros", "hospital", "name"),
                               "ru", "\u0411\u043e\u043b\u044c\u043d\u0438\u0446\u0430 \u041f\u0438\u043d\u0430\u043c\u0430\u0440\u0430")
        self._bind("ru")
        centros = chat_tools._centros_nombre("ru")
        self.assertEqual(centros.get("hospital"), "\u0411\u043e\u043b\u044c\u043d\u0438\u0446\u0430 \u041f\u0438\u043d\u0430\u043c\u0430\u0440\u0430")

    def test_enlace_coincide_por_el_nombre_localizado(self):
        translate.set_override(pack_l10n.key("centros", "hospital", "name"),
                               "ru", "\u0411\u043e\u043b\u044c\u043d\u0438\u0446\u0430 \u041f\u0438\u043d\u0430\u043c\u0430\u0440\u0430")
        enlaces = chat_tools.enlaces_para(
            "\u0437\u0430\u043f\u0438\u0441\u0430\u0442\u044c\u0441\u044f", "\u041f\u0440\u0438\u0451\u043c \u0432 \u0411\u043e\u043b\u044c\u043d\u0438\u0446\u0430 \u041f\u0438\u043d\u0430\u043c\u0430\u0440\u0430.", [], lang="ru")
        self.assertIn({"label": "\u0411\u043e\u043b\u044c\u043d\u0438\u0446\u0430 \u041f\u0438\u043d\u0430\u043c\u0430\u0440\u0430", "href": "/center/hospital"},
                      enlaces)

    def test_camino_en_espanol_no_se_mueve_un_byte(self):
        translate.set_override(pack_l10n.key("centros", "hospital", "name"),
                               "ru", "\u0411\u043e\u043b\u044c\u043d\u0438\u0446\u0430 \u041f\u0438\u043d\u0430\u043c\u0430\u0440\u0430")
        self._bind("es")
        self.assertEqual(chat_tools._centros_nombre("es").get("hospital"),
                         "Hospital de Pinamar")


class ChipsDeEnlacesEnElIdiomaDeLaCharla(_CatalogFixture):
    """Los chips que el CÓDIGO adjunta (turno pedido, whoami, fallback home)
    salían siempre en castellano dentro de una conversación en ru/pt (fuga
    residual 3 de la simulación 2026-09-11): una reserva en ruso cerraba con
    el chip "Mis turnos". Ahora cada label pasa por el catálogo de secciones
    con el idioma de la charla, igual que `_secciones_localizadas`."""

    def test_chip_de_reserva_queda_en_ruso(self):
        enlaces = chat_tools.enlaces_para(
            "\u0445\u043e\u0447\u0443 \u0437\u0430\u043f\u0438\u0441\u0430\u0442\u044c\u0441\u044f", "\u0413\u043e\u0442\u043e\u0432\u043e, \u0437\u0430\u044f\u0432\u043a\u0430 \u0441\u043e\u0437\u0434\u0430\u043d\u0430.",
            [("request_appointment", {"ok": True})], lang="ru")
        self.assertEqual(enlaces, [{"label": "\u041c\u043e\u0438 \u0437\u0430\u043f\u0438\u0441\u0438",
                                    "href": "/my-requests"}])

    def test_chip_de_reserva_queda_en_portugues(self):
        enlaces = chat_tools.enlaces_para(
            "quero marcar", "Pronto, pedido criado.",
            [("request_appointment", {"ok": True})], lang="pt")
        self.assertEqual(enlaces, [{"label": "Meus agendamentos",
                                    "href": "/my-requests"}])

    def test_fallback_home_queda_en_portugues(self):
        enlaces = chat_tools.enlaces_para(
            "olá", "Uma resposta sem destinos.", [], lang="pt")
        self.assertEqual(enlaces, [{"label": "Serviços e agendamentos",
                                    "href": "/"}])

    def test_camino_en_espanol_no_se_mueve_un_byte(self):
        enlaces = chat_tools.enlaces_para(
            "quiero un turno", "Listo, pediste tu turno.",
            [("request_appointment", {"ok": True})], lang="es")
        self.assertEqual(enlaces, [{"label": "Mis turnos",
                                    "href": "/my-requests"}])


if __name__ == "__main__":
    unittest.main()
