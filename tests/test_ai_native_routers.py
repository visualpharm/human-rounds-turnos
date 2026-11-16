"""test_ai_native_routers.py — native-route migration (F8, 2026-08-26):
transport contract for the native FastAPI endpoints in
ai/{prompts,lab_results,chart_summary,ai_health,ai_costs}_router.py.

HANDLER RETIREMENT (2026-08-30): the legacy `handle(handler, method)` path
this file used to replay every request against (the dark-topology half of
each parity assertion) is deleted from the five ai/ modules. The suite now
asserts the NATIVE response absolutely — status and body values the retired
parity assertions proved were shared between the two transports — same
conversion the native-migration final wave (2026-08-27) applied to the other
parity suites.

Each router is mounted alone in a minimal `FastAPI()` app (same pattern as
test_whatsapp_chat_router.py) and driven with `TestClient`.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_ai_native_routers.py" -v

Some cases need a real Postgres (session-role resolution, prompt persistence,
cost aggregation); those skip cleanly without TEST_DATABASE_URL, same
convention as every other _dbtest-backed file in this suite. Cases that
never touch the DB (missing session, missing dni, unknown prompt key) always
run.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")
# Deliberately NOT touching ENABLED_MODULES: unset means "every discovered
# module" (core/app_modules.enabled_ids()'s own contract), which already
# covers `turnos` — pinning it to "turnos" here previously leaked into
# later test modules in the same pytest process (no restore) and made
# THEIR modules (e.g. intake) look disabled, a real regression this file
# caused and fixed 2026-08-26.

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import config  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.cuentas import accounts  # noqa: E402

from app.ai import ai_health_router  # noqa: E402
from app.ai import lab_results_router  # noqa: E402
from app.ai import ai_costs_router  # noqa: E402
from app.ai import chart_summary_router  # noqa: E402
from app.ai import prompts, prompts_router  # noqa: E402

config.ENV_PATH = ""

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_f8native", seed=lambda: db_operators.operators_list())


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _admin_cookie():
    return {"Cookie": "st_admin=" + admin_auth._mint_token(admin_auth._admin_user(), "admin")}


def _medico_cookie(usuario):
    return {"Cookie": "st_admin=" + admin_auth._mint_token(usuario, "medico")}


def _mount(router):
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestAiHealthRouterNative(unittest.TestCase):
    """No DB, no auth — always runs."""

    def setUp(self):
        self.client = _mount(ai_health_router.router)

    def tearDown(self):
        self.client.close()

    def test_disponible_refleja_el_forzado(self):
        for forzado, esperado in (("up", True), ("down", False)):
            with self.subTest(forzado=forzado):
                _env.set_env(self, TURNOS_AI_FORCE=forzado)
                native = self.client.get("/api/ai-status")
                self.assertEqual(native.status_code, 200)
                self.assertEqual(native.json(), {"disponible": esperado})


class TestLabResultsRouterNative(unittest.TestCase):
    def setUp(self):
        self.client = _mount(lab_results_router.router)

    def tearDown(self):
        self.client.close()

    def test_sin_sesion_401(self):
        """No cookie at all: `accounts.current_user` never touches the DB
        for this case (empty token short-circuits), so this always runs."""
        native = self.client.get("/api/account/lab-results")
        self.assertEqual(native.status_code, 401)
        self.assertEqual(native.json(), {"error": "auth_requerida"})

    def test_sesion_valida_200(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        db_accounts.create_user("40777001", "Ana", "Gómez", "1990-05-05", "F",
                       "2254550001", "clave1234")
        token = db_accounts.create_session("40777001")
        headers = {"Cookie": "%s=%s" % (accounts.COOKIE_NAME, token)}
        native = self.client.get("/api/account/lab-results", headers=headers)
        self.assertEqual(native.status_code, 200)


class TestAiCostsRouterNative(unittest.TestCase):
    def setUp(self):
        self.client = _mount(ai_costs_router.router)

    def tearDown(self):
        self.client.close()

    def test_sin_sesion_401(self):
        native = self.client.get("/api/admin/costs")
        self.assertEqual(native.status_code, 401)
        # KNOWN GAP (out of F8 scope — core/deps.py is F1-owned, do-not-edit):
        # `core.deps.require_admin`'s Depends raises a plain `HTTPException`,
        # and FastAPI's default exception handler always wraps the body as
        # `{"detail": ...}` instead of the flat `{"error": "no_autenticado"}`
        # the retired legacy transport used to send. Pinned here as the
        # native contract; flagged for F1 to fix (e.g. an app-level
        # HTTPException handler that unwraps a dict `detail`), not fixable
        # from ai/ without touching core/deps.py (banned by the migration
        # brief).
        self.assertEqual(native.json(), {"detail": {"error": "no_autenticado"}})

    def test_reporte_default_200(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        headers = _admin_cookie()
        for path in ("/api/admin/costs", "/api/admin/costs?days=30", "/api/admin/costs?days=999"):
            with self.subTest(path=path):
                native = self.client.get(path, headers=headers)
                self.assertEqual(native.status_code, 200)


class TestChartSummaryRouterNative(unittest.TestCase):
    def setUp(self):
        self.client = _mount(chart_summary_router.router)

    def tearDown(self):
        self.client.close()

    def test_falta_dni_400(self):
        """No `dni`: the endpoint 400s before ever touching the DB or the
        role gate's DB lookup, so this always runs."""
        headers = _admin_cookie()
        native = self.client.get("/api/admin/patient-summary", headers=headers)
        self.assertEqual(native.status_code, 400)
        self.assertEqual(native.json(), {"error": "falta dni"})

    def test_admin_sin_medico_400(self):
        headers = _admin_cookie()
        native = self.client.get("/api/admin/patient-summary?dni=40777002",
                                 headers=headers)
        self.assertEqual(native.status_code, 400)
        self.assertEqual(native.json(), {"error": "falta medico"})

    def test_medico_usa_su_propio_id(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        db_operators.operator_create("dra-fixture-f8", "clave1234", "medico", medico_id="med-1")
        headers = _medico_cookie("dra-fixture-f8")
        # K10: only her own patient — here, an interview addressed to her.
        db_clinical.interview_create(doctor_id="med-1", dni="40777003", matched=True)
        db_clinical.interview_create(doctor_id="med-2", dni="40777005", matched=True)
        # ?medico=med-2 must be ignored — the médico role always resolves to
        # their OWN médico_id, same criterion as doctor_patients.py.
        native = self.client.get("/api/admin/patient-summary?dni=40777003&medico=med-2",
                                 headers=headers)
        self.assertEqual(native.status_code, 200)
        # ...and naming med-2 does not open med-2's patient either.
        native = self.client.get("/api/admin/patient-summary?dni=40777005&medico=med-2",
                                 headers=headers)
        self.assertEqual(native.status_code, 404)
        self.assertEqual(native.json(), {"error": "paciente no encontrado"})

    def test_post_regenera(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        headers = _admin_cookie()
        body = {"dni": "40777004", "medico": "med-1"}
        native = self.client.post("/api/admin/patient-summary", json=body,
                                  headers=headers)
        self.assertEqual(native.status_code, 200)


class TestPromptsRouterNative(unittest.TestCase):
    def setUp(self):
        self.client = _mount(prompts_router.router)

    def tearDown(self):
        self.client.close()
        prompts.invalidate_cache()

    def test_sin_sesion_401(self):
        native = self.client.get("/api/admin/prompts")
        self.assertEqual(native.status_code, 401)
        # KNOWN GAP — see the identical comment in
        # TestAiCostsRouterNative.test_sin_sesion_401: `core.deps.
        # require_admin`'s HTTPException body always comes back wrapped as
        # `{"detail": ...}` (FastAPI's default handler). Pinned as the
        # native contract; not fixable from ai/ (core/deps.py is F1-owned).
        self.assertEqual(native.json(), {"detail": {"error": "no_autenticado"}})

    def test_clave_desconocida_404(self):
        """`list_prompts()`/detail lookups degrade to the code defaults
        without a DB (see prompts._overrides' fail-closed contract), so the
        unknown-key 404 body ({"error": "no existe ese prompt"}) — the
        domain 404, NOT the generic terminal contract — always runs."""
        headers = _admin_cookie()
        native = self.client.get("/api/admin/prompts/no-existe-esta-clave",
                                 headers=headers)
        self.assertEqual(native.status_code, 404)
        self.assertEqual(native.json(), {"error": "no existe ese prompt"})

    def test_lista_refleja_list_prompts(self):
        headers = _admin_cookie()
        native = self.client.get("/api/admin/prompts", headers=headers)
        self.assertEqual(native.status_code, 200)
        self.assertEqual(native.json(), {"items": prompts.list_prompts()})

    def test_guardar_y_borrar(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        # (Historical note, parity era: two DIFFERENT keys were used, one per
        # transport, because `db_ai.prompt_save()` archives the PREVIOUS edited
        # row on a second save to the SAME key and that archive path has a
        # pre-existing, unrelated column-name bug (core/db.py:4526,
        # `prev["actualizado"]` vs. the actual `updated` alias). The key is
        # still saved only ONCE here, so that path still never fires.)
        clave = list(prompts._BY_KEY)[0]
        headers = _admin_cookie()
        guardar_path = f"/api/admin/prompts/{clave}/guardar"
        # Must keep every `obligatoria` marker the prompt's own contract
        # requires (e.g. {hoy}, JSON key names) or `save_prompt()` 400s on
        # "quedó afuera algo que el sistema necesita" — start from the
        # untouched original text (which by definition satisfies its own
        # contract).
        body = {"texto_en": prompts.original_instructions(clave)}
        resp = self.client.post(guardar_path, json=body, headers=headers)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["item"]["texto_en"], body["texto_en"])
        self.assertEqual(resp.json()["item"]["editado"], True)

        native_del = self.client.delete(f"/api/admin/prompts/{clave}",
                                        headers=headers)
        self.assertEqual(native_del.status_code, 200)
        self.assertFalse(native_del.json()["item"]["editado"])

    def test_historial_conserva_el_nombre_del_api_texto_en(self):
        """problems.md №14: the column is `text_en`, the API still says
        `texto_en` — in the current item and in every history entry."""
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        clave = list(prompts._BY_KEY)[1]
        headers = _admin_cookie()
        guardar_path = f"/api/admin/prompts/{clave}/guardar"
        original = prompts.original_instructions(clave)
        for texto in (original, original + "\n\nSecond synthetic paragraph."):
            resp = self.client.post(guardar_path, json={"texto_en": texto}, headers=headers)
            self.assertEqual(resp.status_code, 200, resp.text)
        items = self.client.get(f"/api/admin/prompts/{clave}/historial", headers=headers).json()["items"]
        self.assertEqual([h["texto_en"] for h in items], [original])
        self.assertNotIn("text_en", items[0])
        self.client.delete(f"/api/admin/prompts/{clave}", headers=headers)


if __name__ == "__main__":
    unittest.main()
