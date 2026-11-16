"""test_module_runtime.py — HTTP-LEVEL proof that app-module enablement has
REAL runtime effect (Phase 6a runtime slice, 2026-07-30). This is the
acceptance gate the phase's own task asked for: NOT a manifest/code-text
agreement check (that's test_module_manifest.py) — a real socket, a real
`server.Handler`, real `do_GET`/`do_POST`, going through the exact same
`routing.dispatch()` production traffic takes.

Boots a real `http.server.ThreadingHTTPServer` bound to an ephemeral port
on 127.0.0.1, in-process, with `server.Handler` — the SAME handler class
`server.py`'s own `main()` uses — and drives it with real HTTP requests
(urllib). config.py's DB-override layer is monkeypatched to an in-memory
dict (same pattern test_config.py/test_app_modules.py use), so
ENABLED_MODULES can be flipped mid-test with no reachable Postgres and no
server restart — proving the "no restart needed" contract for real.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_module_runtime.py"
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")  # boot without Postgres
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config, data_packs  # noqa: E402
from _livehttp import LiveServer as _LiveServer  # noqa: E402


def setUpModule():
    # LiveServer boots the native app directly (lifespan off), bypassing the
    # asgi_main.py preflight, so reproduce its required-pack admission once
    # here.
    data_packs.verify_required_packs()


def _status(base, path, method="GET"):
    req = urllib.request.Request(base + path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        # HTTPError IS the response on non-2xx, but nothing closes it for us
        # — unclosed it trips the ignored-deallocator ResourceWarning this
        # repo's -W error::ResourceWarning gate exists to surface.
        with exc:
            return exc.code


class TestModuleRuntimeDispatch(unittest.TestCase):
    """(a)/(b)/(c)/(d) from the phase task's acceptance gate, all driven
    through real do_GET/do_POST."""

    def setUp(self):
        config._reset_cache_para_tests()
        self._store = {}

        def fake_overrides():
            return dict(self._store)

        self._patch = patch.object(config, "_db_overrides", side_effect=fake_overrides)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        config._reset_cache_para_tests()

    def _set_enabled(self, raw):
        self._store["ENABLED_MODULES"] = raw
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0

    # -- (a) turnos enabled -> a turnos route serves 200 ------------------

    def test_a_turnos_route_serves_200_when_turnos_enabled(self):
        self._set_enabled("turnos")
        with _LiveServer() as base:
            self.assertEqual(_status(base, "/doctors"), 200)

    # -- (b) turnos disabled -> that same route stops serving --------------

    def test_b_same_route_404s_when_turnos_disabled(self):
        # No second real module exists yet (turnos is still the only one —
        # see docs/modules.md) — naming a placeholder id that isn't turnos
        # is the only way to exercise "turnos disabled" without a live
        # second module, and it's exactly what enabled_ids() does with any
        # config value that doesn't include "turnos".
        #
        # 404, not 403 (the product owner, 2026-08-05 — Tier 2 monitor review, deviation
        # #1): once routing.dispatch() skips patient_web/site_*.py, "/doctors" falls
        # to server.py's static-file fallback, which now answers the
        # friendly 404 page for anything that isn't a KNOWN resource on
        # disk (server.py:_static_publico / _static_denegado_es_conocido) —
        # a disabled module's route is indistinguishable from a path that
        # never existed, by design, so it must not leak "this exists but
        # you can't have it" via 403.
        self._set_enabled("not-turnos")
        with _LiveServer() as base:
            self.assertEqual(_status(base, "/doctors"), 404)

    # -- (c) platform-core routes still serve with turnos disabled ---------

    def test_c_platform_core_routes_survive_turnos_disabled(self):
        self._set_enabled("not-turnos")
        with _LiveServer() as base:
            # i18n: GET /api/i18n/<lang>.json — platform-core, never gated.
            self.assertEqual(_status(base, "/api/i18n/es.json"), 200)
            # admin_password_reset.py — platform-core per module.json's own
            # allowlist (account/session primitives are NOT turnos-owned,
            # unlike /admin itself and its login POST, which still live in
            # booking_requests.py today — see docs/modules.md's "what
            # disabling turnos actually takes down" note).
            self.assertEqual(_status(base, "/admin/reset-password"), 200)
            # static asset — never went through routing.py at all.
            from app.core import static_fingerprint
            self.assertEqual(_status(base, static_fingerprint.url("platform/nav.mjs")), 200)
            self.assertEqual(_status(base, "/nav.js"), 404)

    def test_c_i18n_catalog_is_the_real_thing_not_a_stub(self):
        self._set_enabled("turnos")
        with _LiveServer() as base:
            req = urllib.request.Request(base + "/api/i18n/es.json")
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = resp.read()
        self.assertIn(b"about.hero.intro", body)

    # -- turnos re-enabled mid-process, no restart --------------------------

    def test_toggling_back_on_restores_200_without_restart(self):
        with _LiveServer() as base:
            self._set_enabled("not-turnos")
            self.assertEqual(_status(base, "/doctors"), 404)
            self._set_enabled("turnos")
            self.assertEqual(_status(base, "/doctors"), 200)


class TestIntakeModuleGating(unittest.TestCase):
    """Fase 2 (2026-07-31): las rutas de modules/intake/interview_patient.py
    quedan gateadas por el mismo mecanismo que (a)/(b) arriba — routing.py
    las lista bajo `modules.intake.interview_patient`, cuyo module.json
    reclama esas rutas, así que app_modules.route_enabled las saltea
    enteras cuando "intake" no está en ENABLED_MODULES."""

    def setUp(self):
        config._reset_cache_para_tests()
        self._store = {}

        def fake_overrides():
            return dict(self._store)

        self._patch = patch.object(config, "_db_overrides", side_effect=fake_overrides)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        config._reset_cache_para_tests()

    def _set_enabled(self, raw):
        self._store["ENABLED_MODULES"] = raw
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0

    def test_intake_disabled_404s_the_page_and_never_calls_the_handler(self):
        # /entrevista/1 (no-/api/) cae al fallback estático de server.py, que
        # ahora responde el 404 con la marca del sitio (responsable del producto, 2026-08-05 —
        # mismo criterio que test_b arriba) en vez de negar con 403: un
        # módulo deshabilitado no debe distinguirse de un path que nunca
        # existió. /api/intake/t/xyz SIGUE devolviendo 404 con o sin el
        # módulo habilitado (server.py:3158 responde {"error":"not found"}
        # 404 a CUALQUIER /api/* que ningún handler reclamó — no es una
        # señal de gating por sí sola, a diferencia de /doctors o
        # /entrevista/1); la prueba real de "el módulo deshabilitado ni se
        # llama" es la spy.
        # The native router (modules/intake/interview_patient_router.py)
        # calls interview_patient.resolve_entrevista_page / .load_interview
        # directly — the legacy dispatcher-shaped `handle()` is unreachable
        # source now, so it's these two domain functions that must never
        # run while the module is disabled.
        self._set_enabled("turnos")  # intake NO está listado -> deshabilitado
        from app.modules.intake import interview_patient
        with patch.object(
            interview_patient, "resolve_entrevista_page",
            wraps=interview_patient.resolve_entrevista_page,
        ) as page_spy, patch.object(
            interview_patient, "load_interview",
            wraps=interview_patient.load_interview,
        ) as transcript_spy:
            with _LiveServer() as base:
                self.assertEqual(_status(base, "/entrevista/1"), 404)
                self.assertEqual(_status(base, "/api/intake/t/xyz"), 404)
        page_spy.assert_not_called()
        transcript_spy.assert_not_called()

    def test_intake_enabled_routes_to_the_real_handler(self):
        self._set_enabled("turnos,intake")
        from app.modules.intake import interview_patient
        with patch.object(
            interview_patient, "resolve_entrevista_page",
            wraps=interview_patient.resolve_entrevista_page,
        ) as page_spy, patch.object(
            interview_patient, "load_interview",
            wraps=interview_patient.load_interview,
        ) as transcript_spy:
            with _LiveServer() as base:
                # Sin sesión (no manda cookie) -> 401, nunca el 404 de "ruta
                # no existe": la ruta SÍ se dispatcheó, sólo que no autorizó.
                self.assertEqual(_status(base, "/entrevista/1"), 401)
                # Token inexistente -> 404 del propio handler
                # (interview_patient.load_interview) si la base está
                # accesible, 503 si no (ver _dbtest.py: DATABASE_URL puede
                # quedar apuntando a una base de OTRO módulo de test recién
                # dropeada cuando este archivo corre dentro de la suite
                # completa — el 401 de arriba es lo que realmente prueba el
                # gating; las spies de abajo prueban que el handler SÍ se
                # llamó).
                self.assertIn(_status(base, "/api/intake/t/xyz"), (404, 503))
        self.assertTrue(page_spy.called)
        self.assertTrue(transcript_spy.called)

    def test_toggling_intake_back_on_restores_routing_without_restart(self):
        with _LiveServer() as base:
            self._set_enabled("turnos")
            self.assertEqual(_status(base, "/entrevista/1"), 404)
            self._set_enabled("turnos,intake")
            self.assertEqual(_status(base, "/entrevista/1"), 401)


class TestDisablingLastModuleIsRefused(unittest.TestCase):
    """(d) from the acceptance gate — a CLI/API-level guard, not itself an
    HTTP route, but proven against the real app_modules engine (no mocking
    of the function under test)."""

    def setUp(self):
        config._reset_cache_para_tests()
        self._store = {}

        def fake_overrides():
            return dict(self._store)

        def fake_guardar(overrides):
            for k, v in overrides.items():
                if v in (None, ""):
                    self._store.pop(k, None)
                else:
                    self._store[k] = v
            config._db_cache["v"] = None
            config._db_cache["t"] = 0.0
            return dict(self._store)

        self._p1 = patch.object(config, "_db_overrides", side_effect=fake_overrides)
        self._p2 = patch.object(config, "guardar", side_effect=fake_guardar)
        self._p1.start()
        self._p2.start()

    def tearDown(self):
        self._p2.stop()
        self._p1.stop()
        config._reset_cache_para_tests()

    def test_disable_only_enabled_module_is_refused_and_route_keeps_serving(self):
        from app.core import app_modules

        # Con módulos sin configurar, TODOS están habilitados (turnos,
        # intake desde 2026-07-31, chat desde Tier 2 batch F 2026-08-07).
        # Para probar la guarda del "último módulo" dejamos habilitado SOLO
        # turnos y ahí sí el disable se rehúsa.
        self.assertEqual(app_modules.enabled_ids(), {"turnos", "intake", "chat"})
        config.guardar({"ENABLED_MODULES": "turnos"})
        self.assertEqual(app_modules.enabled_ids(), {"turnos"})
        with self.assertRaises(ValueError):
            app_modules.disable("turnos")
        # Refused for real: the route is STILL live afterwards.
        with _LiveServer() as base:
            self.assertEqual(_status(base, "/doctors"), 200)


class TestDependencyRefusalWithReason(unittest.TestCase):
    """Tier 2 (2026-08-05): `requires` — enable()/disable() refuse on a
    dependency violation, with a human-readable reason, no cascades either
    direction. Exercised against a SYNTHETIC temp-modules-dir fixture
    (`base` / `dependent`, `dependent.requires == ["base"]`) so the
    mechanics stay provable in isolation, independent of turnos/intake's
    own enablement combination — the REAL edge (intake.requires ==
    ["turnos"], Codex monitor review P0, 2026-08-05) is exercised
    separately by test_app_modules.py's TestEnableDisable
    (test_disable_turnos_is_refused_while_intake_depends_on_it and
    siblings) and by test_module_lifecycle.py's real-pair HTTP-level test.
    Same MODULES_DIR-swap pattern as TestBrokenModuleBootSurvival below."""

    def setUp(self):
        from app.core import app_modules
        self.app_modules = app_modules
        self.tmpdir = tempfile.mkdtemp(prefix="turnos_deps_mod_")
        self._orig_modules_dir = app_modules.MODULES_DIR
        app_modules.MODULES_DIR = self.tmpdir
        app_modules._RUNTIME_BROKEN.clear()
        # "other" has no relation to base/dependent — it exists purely so
        # disabling "base" doesn't ALSO trip the separate "last enabled
        # module" guard, which would confound what this class tests.
        for mid, requires in (("base", []), ("dependent", ["base"]), ("other", [])):
            d = os.path.join(self.tmpdir, mid)
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "module.json"), "w", encoding="utf-8") as f:
                json.dump({"id": mid, "name": mid, "requires": requires,
                           "routes": [], "admin_sections": [], "nav_entries": [],
                           "db_tables": [], "config_keys_prefixes": []}, f)

        config._reset_cache_para_tests()
        self._store = {}

        def fake_overrides():
            return dict(self._store)

        def fake_guardar(overrides):
            for k, v in overrides.items():
                if v in (None, ""):
                    self._store.pop(k, None)
                else:
                    self._store[k] = v
            config._db_cache["v"] = None
            config._db_cache["t"] = 0.0
            return dict(self._store)

        self._p1 = patch.object(config, "_db_overrides", side_effect=fake_overrides)
        self._p2 = patch.object(config, "guardar", side_effect=fake_guardar)
        self._p1.start()
        self._p2.start()

    def tearDown(self):
        self._p2.stop()
        self._p1.stop()
        config._reset_cache_para_tests()
        self.app_modules.MODULES_DIR = self._orig_modules_dir
        self.app_modules._RUNTIME_BROKEN.clear()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_disable_base_refused_while_dependent_depends_on_it(self):
        config.guardar({"ENABLED_MODULES": "base,dependent"})
        with self.assertRaises(ValueError) as ctx:
            self.app_modules.disable("base")
        self.assertIn("dependent", str(ctx.exception))
        # Refused for real: base's dependent, still enabled afterwards.
        self.assertEqual(self.app_modules.enabled_ids(), {"base", "dependent"})

    def test_disable_base_allowed_once_dependent_is_already_disabled(self):
        config.guardar({"ENABLED_MODULES": "base,other"})
        self.app_modules.disable("base")  # no OTHER enabled module requires it now
        self.assertEqual(self.app_modules.enabled_ids(), {"other"})

    def test_enable_refused_when_a_requirement_is_not_enabled(self):
        self._store["ENABLED_MODULES"] = "nothing-real"
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0
        with self.assertRaises(ValueError) as ctx:
            self.app_modules.enable("dependent")
        self.assertIn("base", str(ctx.exception))

    def test_enable_succeeds_once_the_requirement_is_enabled(self):
        config.guardar({"ENABLED_MODULES": "base"})
        self.app_modules.enable("dependent")
        self.assertEqual(self.app_modules.enabled_ids(), {"base", "dependent"})


class TestLiveEnableLoadsRoutesWithoutRestart(unittest.TestCase):
    """(already proven for turnos/intake individually above by the
    toggling-back-on tests) — this class is the explicit "live enable()
    call, not just an ENABLED_MODULES string edit" version the test plan
    asks for: app_modules.enable()/disable() themselves flip real HTTP
    behavior with zero server restart."""

    def setUp(self):
        config._reset_cache_para_tests()
        self._store = {}

        def fake_overrides():
            return dict(self._store)

        def fake_guardar(overrides):
            for k, v in overrides.items():
                if v in (None, ""):
                    self._store.pop(k, None)
                else:
                    self._store[k] = v
            config._db_cache["v"] = None
            config._db_cache["t"] = 0.0
            return dict(self._store)

        self._p1 = patch.object(config, "_db_overrides", side_effect=fake_overrides)
        self._p2 = patch.object(config, "guardar", side_effect=fake_guardar)
        self._p1.start()
        self._p2.start()

    def tearDown(self):
        self._p2.stop()
        self._p1.stop()
        config._reset_cache_para_tests()

    def test_enable_call_brings_a_route_up_live(self):
        from app.core import app_modules
        # intake also requires "chat" since Tier 2 batch F (interview_agent.py
        # imports modules.chat.chat_language) — pre-enable it so enable("intake")
        # doesn't refuse on the unmet dependency this test isn't exercising.
        config.guardar({"ENABLED_MODULES": "turnos,chat"})
        with _LiveServer() as base:
            self.assertEqual(_status(base, "/entrevista/1"), 404)
            app_modules.enable("intake")
            self.assertEqual(_status(base, "/entrevista/1"), 401)

    def test_disable_call_takes_a_route_down_live(self):
        from app.core import app_modules
        config.guardar({"ENABLED_MODULES": "turnos,intake"})
        with _LiveServer() as base:
            self.assertEqual(_status(base, "/entrevista/1"), 401)
            app_modules.disable("intake")
            self.assertEqual(_status(base, "/entrevista/1"), 404)


class TestBrokenModuleBootSurvival(unittest.TestCase):
    """Loader contract point 7 (docs/roadmap/module-contract-design.md):
    a manifest whose declared route module can't be imported, or that's
    malformed, never takes the platform down — it's reported `broken` and
    contributes nothing. Exercised directly against app_modules's loader
    functions (list_modules()/route_modules()) with MODULES_DIR pointed at
    a throwaway temp directory — the real proof that "the platform still
    boots" is that route_modules()/list_modules() never raise."""

    def setUp(self):
        from app.core import app_modules
        self.app_modules = app_modules
        self.tmpdir = tempfile.mkdtemp(prefix="turnos_broken_mod_")
        self._orig_modules_dir = app_modules.MODULES_DIR
        app_modules.MODULES_DIR = self.tmpdir
        app_modules._RUNTIME_BROKEN.clear()

    def tearDown(self):
        self.app_modules.MODULES_DIR = self._orig_modules_dir
        self.app_modules._RUNTIME_BROKEN.clear()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_manifest(self, module_id, doc):
        d = os.path.join(self.tmpdir, module_id)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "module.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f)

    def test_unimportable_route_module_is_broken_not_a_crash(self):
        self._write_manifest("ghost", {
            "id": "ghost", "name": "Ghost",
            "routes": ["this_module_definitely_does_not_exist_xyz"],
            "admin_sections": [], "nav_entries": [], "db_tables": [],
            "config_keys_prefixes": [],
        })
        # The loader never raises...
        got = self.app_modules.route_modules("GET")
        self.assertEqual(got, [])  # ...and contributes NOTHING.
        modules = self.app_modules.list_modules()
        ghost = next(m for m in modules if m["id"] == "ghost")
        self.assertEqual(ghost["status"], "broken")
        self.assertIn("this_module_definitely_does_not_exist_xyz", ghost["error"])

    def test_malformed_manifest_json_is_broken_not_a_crash(self):
        d = os.path.join(self.tmpdir, "badjson")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "module.json"), "w", encoding="utf-8") as f:
            f.write("{ not valid json")
        modules = self.app_modules.list_modules()  # never raises
        entry = next(m for m in modules if m["id"] == "badjson")
        self.assertEqual(entry["status"], "broken")
        self.assertIsNotNone(entry["error"])

    def test_a_healthy_sibling_module_is_unaffected_by_a_broken_one(self):
        self._write_manifest("ghost", {
            "id": "ghost", "name": "Ghost",
            "routes": ["this_module_definitely_does_not_exist_xyz"],
            "admin_sections": [], "nav_entries": [], "db_tables": [],
            "config_keys_prefixes": [],
        })
        self._write_manifest("healthy", {
            "id": "healthy", "name": "Healthy",
            "routes": ["app.core.i18n"],  # a real, always-importable module
            "admin_sections": [], "nav_entries": [], "db_tables": [],
            "config_keys_prefixes": [],
        })
        got = self.app_modules.route_modules("GET")
        self.assertEqual([m.__name__ for m in got], ["app.core.i18n"])
        modules = self.app_modules.list_modules()
        self.assertEqual(next(m for m in modules if m["id"] == "healthy")["status"], "enabled")
        self.assertEqual(next(m for m in modules if m["id"] == "ghost")["status"], "broken")


if __name__ == "__main__":
    unittest.main()
