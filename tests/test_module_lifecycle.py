"""test_module_lifecycle.py — Codex monitor review (P1, 2026-08-05):
lifecycle/hooks transactionality. Proves, against synthetic MODULES_DIR
fixtures (same swap pattern as test_module_runtime.py's
TestBrokenModuleBootSurvival/TestDependencyRefusalWithReason) plus one
real-pair HTTP case:

  1. A module already reported broken (unknown dependency / cycle / a
     PRIOR runtime failure) never gets a startup attempt at all.
  2. A startup() that registers a hook and then raises rolls the
     registration back — the hook is gone, not just skipped.
  3. hooks.call() only ever invokes a HEALTHY, enabled module's callback
     (not merely "enabled").
  4. enable() requires a dependency to be healthy AND started, not just
     named in the enabled-config string.
  5. startup_all()/shutdown_all() run in topological/reverse-topological
     order by `requires`, not alphabetical.
  6. The real intake -> turnos edge, at the HTTP level.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_module_lifecycle.py"
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
from app.core import app_modules  # noqa: E402
from _livehttp import LiveServer  # noqa: E402


def setUpModule():
    # LiveServer uses the production Handler without the production listener
    # wrapper, so admit required packs before serving any request.
    data_packs.verify_required_packs()


class _SyntheticModulesDir(unittest.TestCase):
    """Shared fixture: a throwaway modules/ directory app_modules.py reads
    from, PLUS a throwaway sys.path entry for hand-written entrypoint .py
    files (importlib needs a real importable module, not just a JSON
    manifest). Mirrors test_module_runtime.py's TestBrokenModuleBootSurvival/
    TestDependencyRefusalWithReason setup exactly."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="turnos_lifecycle_mod_")
        self.entrypoints_dir = tempfile.mkdtemp(prefix="turnos_lifecycle_ep_")
        sys.path.insert(0, self.entrypoints_dir)
        self._orig_modules_dir = app_modules.MODULES_DIR
        app_modules.MODULES_DIR = self.tmpdir
        app_modules._RUNTIME_BROKEN.clear()
        app_modules._STARTED.clear()
        app_modules.hooks.clear()

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
        app_modules.MODULES_DIR = self._orig_modules_dir
        app_modules._RUNTIME_BROKEN.clear()
        app_modules._STARTED.clear()
        app_modules.hooks.clear()
        sys.path.remove(self.entrypoints_dir)
        # Every synthetic entrypoint file lives under a per-test tempdir,
        # but Python caches imports by MODULE NAME in sys.modules --
        # reusing a name (e.g. "lc_ep_topo_x") across test methods would
        # silently reload the STALE cached module (and its module-level
        # state, like an ORDER list) instead of the fresh file this test
        # just wrote. Evict anything this fixture might have imported.
        for name in list(sys.modules):
            if name.startswith("lc_ep_"):
                del sys.modules[name]
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        shutil.rmtree(self.entrypoints_dir, ignore_errors=True)

    def _set_enabled(self, raw):
        self._store["ENABLED_MODULES"] = raw
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0

    def _write_manifest(self, module_id, entrypoint=None, requires=None):
        d = os.path.join(self.tmpdir, module_id)
        os.makedirs(d, exist_ok=True)
        doc = {"id": module_id, "name": module_id, "requires": requires or [],
               "routes": [], "admin_sections": [], "nav_entries": [],
               "db_tables": [], "config_keys_prefixes": []}
        if entrypoint:
            doc["entrypoint"] = entrypoint
        with open(os.path.join(d, "module.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f)

    def _write_entrypoint(self, name, body):
        with open(os.path.join(self.entrypoints_dir, f"{name}.py"), "w", encoding="utf-8") as f:
            f.write(body)


class TestBrokenModuleNeverStarts(_SyntheticModulesDir):
    def test_module_with_unknown_dependency_gets_no_startup_attempt(self):
        self._write_entrypoint("lc_ep_ghost_dep", (
            "STARTED = []\n"
            "def startup(ctx):\n"
            "    STARTED.append(True)\n"
        ))
        self._write_manifest("ghostdep", entrypoint="lc_ep_ghost_dep",
                              requires=["this-does-not-exist"])
        self._set_enabled("ghostdep")
        app_modules.startup_all()
        import lc_ep_ghost_dep
        self.assertEqual(lc_ep_ghost_dep.STARTED, [])
        self.assertFalse(app_modules.startup_one("ghostdep"))

    def test_module_on_a_dependency_cycle_gets_no_startup_attempt(self):
        self._write_entrypoint("lc_ep_cycle_a", (
            "STARTED = []\n"
            "def startup(ctx):\n"
            "    STARTED.append(True)\n"
        ))
        self._write_manifest("cyc_a", entrypoint="lc_ep_cycle_a", requires=["cyc_b"])
        self._write_manifest("cyc_b", requires=["cyc_a"])
        self._set_enabled("cyc_a,cyc_b")
        app_modules.startup_all()
        import lc_ep_cycle_a
        self.assertEqual(lc_ep_cycle_a.STARTED, [])


class TestHookRollbackOnRaise(_SyntheticModulesDir):
    def test_register_hook_then_raise_rolls_back_the_registration(self):
        self._write_entrypoint("lc_ep_hook_then_raise", (
            "def startup(ctx):\n"
            "    ctx.register_hook('lifecycle_test_hook', lambda: 'should-never-fire')\n"
            "    raise RuntimeError('boom')\n"
        ))
        self._write_manifest("flaky", entrypoint="lc_ep_hook_then_raise")
        self._set_enabled("flaky")
        ok = app_modules.startup_one("flaky")
        self.assertFalse(ok)
        status = next(m for m in app_modules.list_modules() if m["id"] == "flaky")
        self.assertEqual(status["status"], "broken")
        self.assertIn("boom", status["error"])
        # The hook is GONE, not merely skipped -- prove it two ways.
        self.assertNotIn("flaky", [mid for mid, _fn in
                                    app_modules.hooks._registry.get("lifecycle_test_hook", ())])
        self.assertEqual(app_modules.hooks.call("lifecycle_test_hook"), [])

    def test_hook_registered_by_a_healthy_module_still_fires(self):
        # Sanity check for the test above: a startup() that DOESN'T raise
        # keeps its hook live and callable.
        self._write_entrypoint("lc_ep_hook_ok", (
            "def startup(ctx):\n"
            "    ctx.register_hook('lifecycle_test_hook_ok', lambda: 'fired')\n"
        ))
        self._write_manifest("healthyhook", entrypoint="lc_ep_hook_ok")
        self._set_enabled("healthyhook")
        self.assertTrue(app_modules.startup_one("healthyhook"))
        self.assertEqual(app_modules.hooks.call("lifecycle_test_hook_ok"), ["fired"])


class TestHooksRequireHealthyNotJustEnabled(_SyntheticModulesDir):
    def test_hook_of_a_module_marked_broken_after_starting_does_not_fire(self):
        self._write_entrypoint("lc_ep_hook_then_break_later", (
            "def startup(ctx):\n"
            "    ctx.register_hook('lifecycle_break_later', lambda: 'fired')\n"
        ))
        self._write_manifest("breaklater", entrypoint="lc_ep_hook_then_break_later")
        self._set_enabled("breaklater")
        self.assertTrue(app_modules.startup_one("breaklater"))
        self.assertEqual(app_modules.hooks.call("lifecycle_break_later"), ["fired"])
        # Something ELSE marks it broken later (a resync daemon, a health
        # probe -- doesn't matter what) -- is_enabled() alone would still
        # say True, but the hook must stop firing.
        app_modules._RUNTIME_BROKEN["breaklater"] = "simulated later failure"
        self.assertEqual(app_modules.hooks.call("lifecycle_break_later"), [])


class TestEnableRequiresHealthyAndStarted(_SyntheticModulesDir):
    def test_enable_refused_when_dependency_is_enabled_but_broken(self):
        self._write_entrypoint("lc_ep_broken_dep", (
            "def startup(ctx):\n"
            "    raise RuntimeError('dependency is broken')\n"
        ))
        self._write_manifest("brokendep", entrypoint="lc_ep_broken_dep")
        self._write_manifest("needsit", requires=["brokendep"])
        self._set_enabled("brokendep")
        with self.assertRaises(ValueError) as ctx:
            app_modules.enable("needsit")
        self.assertIn("brokendep", str(ctx.exception))
        self.assertFalse(app_modules.is_enabled("needsit"))

    def test_enable_succeeds_once_dependency_is_healthy(self):
        self._write_entrypoint("lc_ep_healthy_dep", (
            "def startup(ctx):\n"
            "    pass\n"
        ))
        self._write_manifest("healthydep", entrypoint="lc_ep_healthy_dep")
        self._write_manifest("needsit2", requires=["healthydep"])
        self._set_enabled("healthydep")
        app_modules.enable("needsit2")
        self.assertTrue(app_modules.is_enabled("needsit2"))


class TestTopologicalLifecycleOrder(_SyntheticModulesDir):
    """c requires b requires a -- alphabetical order would ALSO happen to
    start a,b,c in the right order, so the fixture deliberately uses
    reverse-alphabetical ids (z requires y requires x) to prove the order
    comes from `requires`, not from sorting ids."""

    def setUp(self):
        super().setUp()
        self._write_entrypoint("lc_ep_topo_x", (
            "ORDER = []\n"
            "def startup(ctx):\n"
            "    ORDER.append('x')\n"
            "def shutdown(ctx):\n"
            "    ORDER.append('x-down')\n"
        ))
        self._write_entrypoint("lc_ep_topo_y", (
            "import lc_ep_topo_x as base\n"
            "def startup(ctx):\n"
            "    base.ORDER.append('y')\n"
            "def shutdown(ctx):\n"
            "    base.ORDER.append('y-down')\n"
        ))
        self._write_entrypoint("lc_ep_topo_z", (
            "import lc_ep_topo_x as base\n"
            "def startup(ctx):\n"
            "    base.ORDER.append('z')\n"
            "def shutdown(ctx):\n"
            "    base.ORDER.append('z-down')\n"
        ))
        self._write_manifest("z", entrypoint="lc_ep_topo_z", requires=["y"])
        self._write_manifest("y", entrypoint="lc_ep_topo_y", requires=["x"])
        self._write_manifest("x", entrypoint="lc_ep_topo_x")
        self._set_enabled("x,y,z")

    def test_startup_all_runs_dependencies_before_dependents(self):
        app_modules.startup_all()
        import lc_ep_topo_x as base
        self.assertEqual(base.ORDER, ["x", "y", "z"])

    def test_shutdown_all_runs_dependents_before_dependencies(self):
        app_modules.startup_all()
        import lc_ep_topo_x as base
        base.ORDER.clear()
        app_modules.shutdown_all()
        self.assertEqual(base.ORDER, ["z-down", "y-down", "x-down"])


class TestRealIntakeTurnosEdgeHttp(unittest.TestCase):
    """The real pair (Codex monitor review, P0, 2026-08-05:
    intake.requires == ["turnos"] is no longer declarative-only) at the
    HTTP level -- same _LiveServer pattern test_module_runtime.py uses."""

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

    # The first request this process serves that reaches the database also runs
    # the one-time schema bootstrap (core.db_schema._ensure_schema: Alembic on the
    # empty database of the lane), so it is slow by design -- 0.4 s on an idle
    # box, several seconds on a loaded CI runner under coverage. The test asks
    # "does the route still answer", not "how fast", so the bound is only
    # there to stop a hang. It was 5 s and the first request overran it
    # (pipeline 183392, shard2); a 6 s delay injected into the bootstrap
    # reproduces that failure exactly.
    _REQUEST_DEADLINE = 60

    def _status(self, url):
        try:
            with urllib.request.urlopen(url, timeout=self._REQUEST_DEADLINE) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            with e:  # unclosed, it trips the ResourceWarning gate at GC
                return e.code

    def test_disable_turnos_refused_while_intake_enabled_route_keeps_serving(self):
        config.guardar({"ENABLED_MODULES": "turnos,intake"})
        with LiveServer() as base:
            with self.assertRaises(ValueError) as ctx:
                app_modules.disable("turnos")
            self.assertIn("intake", str(ctx.exception))
            self.assertEqual(self._status(base + "/doctors"), 200)  # never went down -- disable was refused

    def test_disable_chat_refused_while_intake_enabled_route_keeps_serving(self):
        # Symmetric to the turnos case above: Tier 2 batch F gave intake a
        # SECOND real dependency, `requires: ["turnos", "chat"]`
        # (interview_agent.py imports modules.chat.chat_language) — added
        # as a fix, not part of the original batch F commit (independent
        # review, 2026-08-07: zero tests exercised this edge before).
        config.guardar({"ENABLED_MODULES": "turnos,intake,chat"})
        with LiveServer() as base:
            with self.assertRaises(ValueError) as ctx:
                app_modules.disable("chat")
            self.assertIn("intake", str(ctx.exception))
            # chat's OWN route (404 `no_encontrado` once chat is disabled), not
            # turnos' /doctors: a refusal that still switched chat off would
            # leave /doctors serving and pass unnoticed.
            self.assertEqual(self._status(base + "/api/chat/voice"), 200)  # never went down -- disable was refused


if __name__ == "__main__":
    unittest.main()
