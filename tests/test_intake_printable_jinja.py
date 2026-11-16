"""Standing native-transport and localization gate for intake print
documents.

NATIVE-MIGRATION FINAL WAVE (2026-08-27): this suite used to run each case
through BOTH the legacy handler-shaped dispatch (`printable.handle`/
`intake_admin.handle`, driven by this file's own `FakeHandler`) and the
native FastAPI router (`self.client`, a `TestClient` on the real `app.app`),
asserting equality between the two. The legacy HTTP topology (`server.py`,
`core/routing.py`) is retired — `app.py`'s native
composition is the only transport left. Every case below now asserts the
native response directly, using the STILL-PRESENT (unreachable-but-kept,
per this repo's migration doctrine) pure builder functions —
`printable._receta_html`/`_orden_html`/`_not_found_html`,
`intake_admin._poster_html` — as an independent oracle: these are a
textually separate implementation (hand-built HTML strings) from the native
Jinja templates, so a match against them is a genuine correctness check, not
a tautology. `test_19_disabled_legacy_dispatch_skips_both_handlers` is
retired outright (see its old slot below) because the function it tested,
`core.routing.dispatch()`, no longer exists in the codebase at all.

All persisted rows live in a run-scoped database created only from an
explicit ``TEST_DATABASE_URL``.  Render-only cases patch the shared business
helper used by the router and never open a database connection.
"""

import html
import json
import os
import re
import sys
import unittest
from unittest import mock

from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
for path in (ROOT, TEST_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")

from app.auth import admin_auth, turnstile  # noqa: E402
from app.core import config, db, i18n, site_identity  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.modules.intake import intake_admin, intake_admin_router, printable, printable_router  # noqa: E402
from app.patient_web import catalog  # noqa: E402
from app import main as app_module  # noqa: E402
import _dbtest  # noqa: E402

turnstile.ENV_PATH = ""

_db = _dbtest.PerModuleDb(
    "sana_turnos_test_intakeprintable_jinja", seed=lambda: db_operators.operators_list())
HAVE_DB = False
DB_SKIP_REASON = "TEST_DATABASE_URL not configured"


def setUpModule():
    """Expose exactly one database-resolution leg: TEST_DATABASE_URL."""
    global HAVE_DB, DB_SKIP_REASON
    # Module cleanups run last-in first-out: the environment is put back, then ENV_PATH, then the config cache is dropped.
    unittest.addModuleCleanup(config._reset_cache_para_tests)
    unittest.addModuleCleanup(setattr, config, "ENV_PATH", config.ENV_PATH)
    _env.set_env_for_module(DATABASE_URL=None, TURNOS_ENV_FILE=None)
    config.ENV_PATH = ""
    config._reset_cache_para_tests()
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _reset_tables():
    if not HAVE_DB:
        return
    import psycopg
    with psycopg.connect(_db.test_url, autocommit=True, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE operators RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE interviews RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE consultations RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE catalog")
    catalog.invalidate()


RECETA = {
    "medico": {"nombre": "Dra. Fernández", "matricula": "92933", "especialidad": "Cardiología"},
    "paciente": {"nombre": "Gómez, Ana", "dni": "30111222"},
    "fecha": "31/07/2026", "espacio_superior_cm": 2,
    "titulo": "Hospital de Pinamar", "lineas": ["Paracetamol — 500 mg"],
    "notas": "Control en 15 días.", "pie": "Documento generado.",
}
ORDEN = {
    "medico": {"nombre": "Dra. Fernández", "matricula": "92933", "especialidad": "Cardiología"},
    "paciente": {"nombre": "Gómez, Ana", "dni": "30111222"},
    "fecha": "31/07/2026", "espacio_superior_cm": 0,
    "titulo": "Hospital de Pinamar", "estudios": ["Hemograma completo"],
    "diagnosticos": ["Hipertensión"], "notas": "Control.", "pie": "Documento generado.",
}
ROW = {
    "receta_token": "tok-receta-123", "orden_token": "tok-orden-456",
    "receta_json": RECETA, "orden_json": ORDEN,
}
DOCTOR_1 = {"id": "print-med-1", "nombre": "María Fernández",
            "especialidad": "Cardiología", "matricula": "92933"}
DOCTOR_2 = {"id": "print-med-2", "nombre": "Carlos Gómez",
            "especialidad": "Pediatría", "matricula": "81234"}


def _cookie(user, role):
    return "st_admin=" + admin_auth._mint_token(user, role)


def _catalog_values(lang):
    path = os.path.join(ROOT, "app", "modules", "intake", "locales", lang, "intake.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return {k: str(v) for k, v in data.items()
            if k.startswith("intake.print.") or k.startswith("intake.poster.")}


def _emitted_units(values):
    """jinja_env._t HTML-escapes the complete catalog value before output."""
    return {key: html.escape(value, quote=True) for key, value in values.items()}


class _Scanner:
    """Single-pass, longest-first scanner used for counts and substitution."""
    def __init__(self, units, replacement=None):
        seen = {}
        for key, unit in units.items():
            if unit in seen:
                raise AssertionError(f"duplicate emitted unit for {seen[unit]} and {key}: {unit!r}")
            seen[unit] = key
        self.units = dict(units)
        self.by_unit = seen
        self.replacement = dict(replacement or {})
        self.pattern = re.compile("|".join(
            re.escape(unit) for unit in sorted(seen, key=len, reverse=True)))

    def scan(self, text):
        hits = {key: 0 for key in self.units}
        def repl(match):
            unit = match.group(0)
            key = self.by_unit[unit]
            hits[key] += 1
            return self.replacement.get(key, unit)
        return self.pattern.sub(repl, text), hits


ES = _emitted_units(_catalog_values("es"))
EN = _emitted_units(_catalog_values("en"))


class IntakePrintableJinjaTest(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.client = TestClient(app_module.app)
        # Registered before any set_env: the config cache is dropped after the environment is back.
        self.addCleanup(config._reset_cache_para_tests)

    def _assert_html_headers(self, status, body, headers, expected_status=200):
        self.assertEqual(status, expected_status)
        self.assertEqual(headers.get("content-type"), "text/html; charset=utf-8")
        self.assertEqual(headers.get("content-length"), str(len(body)))
        self.assertNotIn("cache-control", headers)
        self.assertNotIn("content-encoding", headers)
        self.assertNotIn("vary", headers)

    def _assert_json_headers(self, status, body, headers, expected_status):
        self.assertEqual(status, expected_status)
        self.assertEqual(headers.get("content-type"), "application/json; charset=utf-8")
        self.assertEqual(headers.get("content-length"), str(len(body)))
        self.assertEqual(headers.get("cache-control"), "no-store")

    def _native_print(self, path, row):
        """Runs the request through the ONLY remaining transport
        (`self.client`, real `app.app`) and returns
        (status, body, header_map) — no legacy dispatch involved (see
        module docstring)."""
        with mock.patch.object(db_clinical, "consultation_by_token", return_value=row):
            twin = self.client.get(path, headers={"Accept-Encoding": "identity"})
        return (twin.status_code, twin.content,
                {k.lower(): v for k, v in twin.headers.items()})

    # 1
    def test_new_drafts_are_marked_in_every_printed_copy(self):
        for kind, payload, token, copies in (("orden", ORDEN, ROW["orden_token"], 2),
                                             ("receta", RECETA, ROW["receta_token"], 4)):
            draft = dict(payload, borrador=True)
            row = dict(ROW, **{kind + "_json": draft})
            status, body, _ = self._native_print(("/o/" if kind == "orden" else "/r/") + token, row)
            self.assertEqual(status, 200)
            text = body.decode()
            self.assertEqual(text.count("BORRADOR — REVISAR"), copies)
            self.assertIn("Gómez, Ana", text)
            oracle = printable._orden_html(draft) if kind == "orden" else printable._receta_html(draft)
            self.assertEqual(oracle.count("BORRADOR — REVISAR"), copies)

    def test_01_receta_success_transport_headers_and_body(self):
        twin = self._native_print("/r/tok-receta-123", ROW)
        self._assert_html_headers(*twin)
        self.assertEqual(twin[1].decode(), printable._receta_html(RECETA))

    # 2
    def test_02_orden_success_transport_headers_and_body(self):
        twin = self._native_print("/o/tok-orden-456", ROW)
        self._assert_html_headers(*twin)
        self.assertEqual(twin[1].decode(), printable._orden_html(ORDEN))

    # 3
    def test_03_render_variants_match_frozen_builders(self):
        variants = []
        for space in (0, 10, 99, -3, "bad"):
            payload = dict(RECETA, espacio_superior_cm=space, lineas=[], notas="")
            variants.append(("receta", payload, printable._receta_html(payload)))
        payload = dict(ORDEN, estudios=[], diagnosticos=[], notas="")
        variants.append(("orden", payload, printable._orden_html(payload)))
        for kind, payload, expected in variants:
            self.assertEqual(printable_router._render_printable(kind, payload), expected)

    # 4
    def test_04_cross_kind_tokens_are_not_authorized(self):
        for path in ("/r/tok-orden-456", "/o/tok-receta-123"):
            twin = self._native_print(path, ROW)
            self._assert_html_headers(*twin, expected_status=404)
            self.assertEqual(twin[1].decode(), printable._not_found_html())

    # 5
    def test_05_unknown_token_not_found(self):
        twin = self._native_print("/r/unknowntoken1", None)
        self._assert_html_headers(*twin, expected_status=404)
        self.assertEqual(twin[1].decode(), printable._not_found_html())

    # 6
    def test_06_malformed_charset_tokens_are_ordinary_parity(self):
        for path in ("/r/abc$def", "/o/a$b"):
            twin = self._native_print(path, ROW)
            self._assert_html_headers(*twin, expected_status=404)
            self.assertEqual(twin[1].decode(), printable._not_found_html())

    # 7
    def test_07_admin_can_render_a_seeded_poster(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        _reset_tables()
        db_catalog.catalog_upsert("medicos", DOCTOR_1["id"], dict(DOCTOR_1), borrado=False)
        catalog.invalidate()
        token, doc = intake_admin.get_or_create_door_token(DOCTOR_1["id"])
        cookie = _cookie("admin", "admin")
        url = site_identity.base_url() + "/e/" + token
        with mock.patch.object(intake_admin, "_qr_svg", return_value=None):
            expected = intake_admin._poster_html(doc, url)
            twin_r = self.client.get(f"/api/admin/intake/door/{DOCTOR_1['id']}",
                                     headers={"Accept-Encoding": "identity"},
                                     cookies={"st_admin": cookie.split("=", 1)[1]})
        twin = (twin_r.status_code, twin_r.content,
                {k.lower(): v for k, v in twin_r.headers.items()})
        self.assertEqual(twin[1].decode(), expected)
        self._assert_html_headers(*twin)

    # 8
    def test_08_own_doctor_can_render_poster(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        _reset_tables()
        db_catalog.catalog_upsert("medicos", DOCTOR_1["id"], dict(DOCTOR_1), borrado=False)
        db_operators.operator_create("doctor-own", "password", "medico", medico_id=DOCTOR_1["id"])
        catalog.invalidate()
        token, doc = intake_admin.get_or_create_door_token(DOCTOR_1["id"])
        cookie = _cookie("doctor-own", "medico")
        url = site_identity.base_url() + "/e/" + token
        with mock.patch.object(intake_admin, "_qr_svg", return_value=None):
            expected = intake_admin._poster_html(doc, url)
            twin_r = self.client.get(f"/api/admin/intake/door/{DOCTOR_1['id']}",
                                     headers={"Accept-Encoding": "identity"},
                                     cookies={"st_admin": cookie.split("=", 1)[1]})
        self.assertEqual(twin_r.status_code, 200)
        self.assertEqual(twin_r.content.decode(), expected)
        self._assert_html_headers(twin_r.status_code, twin_r.content,
                                  {k.lower(): v for k, v in twin_r.headers.items()})

    # 9
    def test_09_poster_without_session_preserves_exact_json_contract(self):
        twin_r = self.client.get("/api/admin/intake/door/any")
        self.assertEqual(twin_r.status_code, 401)
        self.assertEqual(twin_r.content, b'{"error": "no_autenticado"}')
        self._assert_json_headers(twin_r.status_code, twin_r.content,
                                  {k.lower(): v for k, v in twin_r.headers.items()}, 401)

    # 10
    def test_10_foreign_doctor_gets_concealing_404(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        _reset_tables()
        for doctor in (DOCTOR_1, DOCTOR_2):
            db_catalog.catalog_upsert("medicos", doctor["id"], dict(doctor), borrado=False)
        db_operators.operator_create("doctor-own", "password", "medico", medico_id=DOCTOR_1["id"])
        cookie = _cookie("doctor-own", "medico")
        twin_r = self.client.get(f"/api/admin/intake/door/{DOCTOR_2['id']}",
                                 cookies={"st_admin": cookie.split("=", 1)[1]})
        self.assertEqual(twin_r.status_code, 404)
        self.assertEqual(twin_r.content, b'{"error": "no encontrado"}')
        self._assert_json_headers(twin_r.status_code, twin_r.content,
                                  {k.lower(): v for k, v in twin_r.headers.items()}, 404)

    # 11
    def test_11_unknown_doctor_is_exact_404(self):
        cookie = _cookie("admin", "admin")
        with mock.patch.object(intake_admin, "get_or_create_door_token", return_value=(None, None)):
            twin_r = self.client.get("/api/admin/intake/door/missing",
                                     cookies={"st_admin": cookie.split("=", 1)[1]})
        self.assertEqual(twin_r.content, b'{"error": "no encontrado"}')
        self._assert_json_headers(twin_r.status_code, twin_r.content,
                                  {k.lower(): v for k, v in twin_r.headers.items()}, 404)

    # 12
    def test_12_db_unavailable_is_exact_503(self):
        cookie = _cookie("admin", "admin")
        with mock.patch.object(intake_admin, "get_or_create_door_token",
                               side_effect=db.DbUnavailable("down")):
            twin_r = self.client.get("/api/admin/intake/door/one",
                                     cookies={"st_admin": cookie.split("=", 1)[1]})
        self.assertEqual(twin_r.status_code, 503)
        self.assertEqual(twin_r.content, b'{"error": "base de datos no disponible"}')
        self._assert_json_headers(twin_r.status_code, twin_r.content,
                                  {k.lower(): v for k, v in twin_r.headers.items()}, 503)

    # 13
    def test_13_poster_svg_and_fallback_branches_match(self):
        doc = dict(DOCTOR_1, entrevista_qr_token="fixed-token")
        cookie = _cookie("admin", "admin")
        url = site_identity.base_url() + "/e/fixed-token"
        for svg in (None, '<svg xmlns="http://www.w3.org/2000/svg"></svg>'):
            with mock.patch.object(intake_admin, "get_or_create_door_token",
                                   return_value=("fixed-token", doc)), \
                 mock.patch.object(intake_admin, "_qr_svg", return_value=svg):
                expected = intake_admin._poster_html(doc, url)
                twin_r = self.client.get("/api/admin/intake/door/one",
                                         headers={"Accept-Encoding": "identity"},
                                         cookies={"st_admin": cookie.split("=", 1)[1]})
            self.assertEqual(twin_r.content.decode(), expected)
            self.assertEqual(twin_r.status_code, 200)

    # 14
    def test_14_language_switch_redirect_cookie_and_empty_body(self):
        _env.set_env(self, LANGUAGES="es,en", LANG_SWITCHER_MODE="secret")
        config._reset_cache_para_tests()
        for path in ("/r/tok-receta-123", "/o/tok-orden-456",
                     "/api/admin/intake/door/one"):
            twin = self.client.get(path + "?lang=en", follow_redirects=False)
            self.assertEqual(twin.status_code, 302)
            self.assertEqual(twin.content, b"")
            self.assertEqual(twin.headers["location"], path)
            self.assertEqual(twin.headers["set-cookie"],
                             "lang=en; Max-Age=31536000; Path=/; SameSite=Lax")
            self.assertEqual(twin.headers["content-length"], "0")
            self.assertNotIn("content-type", twin.headers)

    # 15
    def test_15_default_install_does_not_intercept_lang_query(self):
        _env.set_env(self, LANGUAGES=None, LANG_SWITCHER_MODE=None)
        config._reset_cache_para_tests()
        with mock.patch.object(db_clinical, "consultation_by_token", return_value=ROW):
            twin = self.client.get("/r/tok-receta-123?lang=en",
                                   headers={"Accept-Encoding": "identity"})
        self.assertEqual(twin.status_code, 200)
        self.assertEqual(twin.text, printable._receta_html(RECETA))

    # 16
    def test_16_complete_payload_escaping_matrix_is_exactly_once(self):
        marker = '<x a="b">& ñ {{sitio.brand}}</x>'
        escaped = html.escape(marker, quote=True)
        receta = dict(RECETA, titulo=marker, fecha=marker, lineas=[marker], notas=marker,
                      pie=marker, medico={"nombre": marker, "matricula": marker,
                                          "especialidad": marker},
                      paciente={"nombre": marker, "dni": marker})
        orden = dict(ORDEN, titulo=marker, fecha=marker, estudios=[marker],
                     diagnosticos=[marker], notas=marker, pie=marker,
                     medico=receta["medico"], paciente=receta["paciente"])
        for kind, payload, builder in (("receta", receta, printable._receta_html),
                                       ("orden", orden, printable._orden_html)):
            body = printable_router._render_printable(kind, payload)
            self.assertEqual(body, builder(payload))
            self.assertIn(escaped, body)
            self.assertNotIn("&amp;lt;x", body)
            self.assertIn("{{sitio.brand}}", body)
        doc = {"nombre": marker, "especialidad": marker, "matricula": marker}
        url = "https://example.test/e/" + marker
        with mock.patch.object(intake_admin, "_qr_svg", return_value=None):
            expected = intake_admin._poster_html(doc, url)
        actual = intake_admin_router._render_poster(doc, url, None, marker)
        self.assertEqual(actual, expected)

    # 17
    def test_17_cookie_round_trip_and_collision_safe_english_delta_oracle(self):
        _env.set_env(self, LANGUAGES="es,en", LANG_SWITCHER_MODE="secret")
        config._reset_cache_para_tests()
        with mock.patch.object(db_clinical, "consultation_by_token", return_value=ROW):
            switched = self.client.get("/r/tok-receta-123?lang=en", follow_redirects=False)
            self.assertEqual(switched.status_code, 302)
            twin_en = self.client.get("/r/tok-receta-123",
                                      headers={"Accept-Encoding": "identity"}).text
        legacy_es = printable._receta_html(RECETA)
        self.assertIn('<html lang="en">', twin_en)
        receta_full = {
            "intake.print.imprimir": 1, "intake.print.receta.title": 1,
            "intake.print.receta.doc_label": 4, "intake.print.fecha_label": 4,
            "intake.print.dni_label": 4, "intake.print.matricula_label": 4,
            "intake.print.firma": 4, "intake.print.indicaciones_label": 4,
            "intake.print.copia.paciente": 1, "intake.print.copia.farmacia": 1,
            "intake.print.copia.obra_social": 1, "intake.print.copia.archivo": 1,
        }
        receta_empty = dict(receta_full)
        receta_empty.pop("intake.print.indicaciones_label")
        receta_empty["intake.print.sin_medicacion"] = 4
        orden_full = {
            "intake.print.imprimir": 1, "intake.print.orden.title": 1,
            "intake.print.orden.doc_label": 2, "intake.print.fecha_label": 2,
            "intake.print.dni_label": 2, "intake.print.matricula_label": 2,
            "intake.print.firma": 2, "intake.print.indicaciones_label": 2,
            "intake.print.solicito": 2, "intake.print.dx_titulo": 2,
            "intake.print.copia.original": 1, "intake.print.copia.duplicado": 1,
        }
        orden_empty = dict(orden_full)
        orden_empty.pop("intake.print.indicaciones_label")
        orden_empty.pop("intake.print.dx_titulo")
        orden_empty["intake.print.sin_estudios"] = 2
        not_found = {"intake.print.not_found.title": 2,
                     "intake.print.not_found.body": 1}
        poster = {
            "intake.print.imprimir": 1, "intake.print.matricula_label": 1,
            "intake.poster.title_label": 1, "intake.poster.headline": 1,
            "intake.poster.copete": 1, "intake.poster.segno_fallback": 1,
        }

        empty_receta = dict(RECETA, lineas=[], notas="")
        empty_orden = dict(ORDEN, estudios=[], diagnosticos=[], notas="")
        poster_doc = dict(DOCTOR_1)
        poster_url = "https://example.test/e/fixed"
        _lang, bind_token = i18n.bind_lang("en")
        try:
            cases = [
                (legacy_es, twin_en, receta_full),
                (printable._receta_html(empty_receta),
                 printable_router._render_printable("receta", empty_receta), receta_empty),
                (printable._orden_html(ORDEN),
                 printable_router._render_printable("orden", ORDEN), orden_full),
                (printable._orden_html(empty_orden),
                 printable_router._render_printable("orden", empty_orden), orden_empty),
                (printable._not_found_html(), printable_router._render_not_found(), not_found),
            ]
            with mock.patch.object(intake_admin, "_qr_svg", return_value=None):
                legacy_poster = intake_admin._poster_html(poster_doc, poster_url)
            cases.append((legacy_poster,
                          intake_admin_router._render_poster(
                              poster_doc, poster_url, None,
                              catalog.license_number(poster_doc)), poster))
        finally:
            i18n.unbind(bind_token)

        es_units = dict(ES, __lang__='<html lang="es">')
        en_units = dict(EN, __lang__='<html lang="en">')
        replacements = {key: es_units[key] for key in en_units}
        for legacy, english, expected in cases:
            expected = dict(expected, __lang__=1)
            _, es_hits = _Scanner(es_units).scan(legacy)
            rebuilt, en_hits = _Scanner(en_units, replacements).scan(english)
            for key in es_units:
                self.assertEqual(es_hits[key], expected.get(key, 0), key)
                self.assertEqual(en_hits[key], expected.get(key, 0), key)
            self.assertEqual(rebuilt, legacy)
        # Legacy builders intentionally ignore the language cookie.
        self.assertEqual(printable._receta_html(RECETA), legacy_es)

    # 18
    def test_18_disabled_twin_404s_before_helpers(self):
        store = {"ENABLED_MODULES": "turnos"}
        with mock.patch.object(config, "_db_overrides", return_value=store), \
             mock.patch.object(printable, "_consulta_para") as print_spy, \
             mock.patch.object(intake_admin, "get_or_create_door_token") as door_spy:
            config._reset_cache_para_tests()
            for path in ("/r/a", "/o/a", "/api/admin/intake/door/a"):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), {"detail": {"error": "no_encontrado"}})
            print_spy.assert_not_called()
            door_spy.assert_not_called()

    # 19 — RETIRED (native-migration final wave, 2026-08-27): this test
    # existed solely to prove `core.routing.dispatch()` skipped
    # `printable.handle`/`intake_admin.handle` when the intake module was
    # disabled. `core/routing.py` — the function under test — no longer
    # exists anywhere in the codebase; there is no dispatcher left to call.
    # The surviving contract (a disabled module 404s on the ONE remaining
    # transport before ever reaching the business helpers) is already
    # covered by test_18 above, on the native router.

    def test_oracle_construction_probes(self):
        # HTML-aware overlay: the scanner consumes bytes as jinja_env._t emits them.
        overlay = _emitted_units({"quote": 'A & "B"', "other": "Other"})
        _, hits = _Scanner(overlay).scan('A &amp; &quot;B&quot;')
        self.assertEqual(hits["quote"], 1)
        # Longest-first attribution never counts the long unit as the short key.
        _, hits = _Scanner({"short": "Order", "long": "Order of studies"}).scan(
            "Order of studies / Order")
        self.assertEqual(hits, {"short": 1, "long": 1})
        # Every identical-literal collision shape is rejected with both keys named.
        for units in ({"a": "same", "b": "same"},
                      {"en-a": "same", "en-b": "same"},
                      {"es-a": "igual", "es-b": "igual"}):
            with self.assertRaisesRegex(AssertionError, "a|b"):
                _Scanner(units)
        # A catalog literal injected into payload bytes inflates the named counter.
        collision_payload = dict(RECETA, titulo="Print")
        lang, token = i18n.bind_lang("en")
        try:
            collided = printable_router._render_printable("receta", collision_payload)
        finally:
            i18n.unbind(token)
        _, collision_hits = _Scanner(EN).scan(collided)
        # ``titulo`` is emitted once per prescription copy, so the payload
        # collision adds four hits to the print button's one legitimate hit.
        self.assertEqual(collision_hits["intake.print.imprimir"], 5)


if __name__ == "__main__":
    unittest.main()
