"""test_module_hooks.py — Tier 2 module-contract framework (2026-08-05):
app_modules.hooks (the named-hook registry) and the entrypoint lifecycle
(startup_one/shutdown_one/startup_all/ensure_started). config.py's DB
layer is monkeypatched to an in-memory dict (same pattern every other
app_modules test file uses) so this runs without a reachable DATABASE_URL.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_module_hooks.py"
"""
import os
import sys
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
from app.core import app_modules  # noqa: E402


class _FakeConfigStore(unittest.TestCase):
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
        self._orig_started = set(app_modules._STARTED)
        self._orig_broken = dict(app_modules._RUNTIME_BROKEN)

    def tearDown(self):
        self._p2.stop()
        self._p1.stop()
        config._reset_cache_para_tests()
        app_modules.hooks.clear()
        app_modules._STARTED.clear()
        app_modules._STARTED.update(self._orig_started)
        app_modules._RUNTIME_BROKEN.clear()
        app_modules._RUNTIME_BROKEN.update(self._orig_broken)

    def _set_enabled(self, raw):
        self._store["ENABLED_MODULES"] = raw
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0


class TestHooksRegistry(_FakeConfigStore):
    def setUp(self):
        super().setUp()
        app_modules.hooks.clear()

    def test_call_invokes_a_registered_hook_of_an_enabled_module(self):
        self._set_enabled("turnos,intake")
        seen = []
        app_modules.hooks.register("intake", "ping", lambda x: seen.append(x))
        out = app_modules.hooks.call("ping", "hi")
        self.assertEqual(seen, ["hi"])
        self.assertEqual(out, [None])

    def test_call_skips_a_hook_of_a_disabled_module(self):
        self._set_enabled("turnos")  # intake NOT enabled
        seen = []
        app_modules.hooks.register("intake", "ping", lambda: seen.append(1))
        app_modules.hooks.call("ping")
        self.assertEqual(seen, [])

    def test_call_on_an_unregistered_name_returns_empty_list(self):
        self.assertEqual(app_modules.hooks.call("nobody-registered-this"), [])

    def test_registering_the_same_module_twice_replaces_not_duplicates(self):
        # A module's startup(ctx) legitimately runs more than once per
        # process (enable -> disable -> enable re-runs it, see
        # shutdown_one()'s docstring) — a second registration from the SAME
        # module for the SAME hook name must not fire twice per call.
        self._set_enabled("turnos,intake")
        seen = []
        app_modules.hooks.register("intake", "ping", lambda: seen.append("first"))
        app_modules.hooks.register("intake", "ping", lambda: seen.append("second"))
        app_modules.hooks.call("ping")
        self.assertEqual(seen, ["second"])  # only the LATEST registration fired

    def test_call_swallows_an_exception_from_one_callable_and_keeps_going(self):
        self._set_enabled("turnos,intake")

        def boom():
            raise RuntimeError("kaboom")

        calls = []
        app_modules.hooks.register("intake", "multi", boom)
        app_modules.hooks.register("intake", "multi", lambda: calls.append(1))
        out = app_modules.hooks.call("multi")
        self.assertEqual(calls, [1])
        self.assertEqual(out, [None])  # only the surviving callable's result

    def test_toggling_the_owner_module_off_stops_the_hook_live(self):
        self._set_enabled("turnos,intake")
        seen = []
        app_modules.hooks.register("intake", "ping", lambda: seen.append(1))
        app_modules.hooks.call("ping")
        self._set_enabled("turnos")
        app_modules.hooks.call("ping")
        self.assertEqual(seen, [1])  # only fired the first time


class TestIntakeEntrypointRegistersInterviewReminder(_FakeConfigStore):
    """The one migrated scatter site (docs/roadmap/module-contract-design.md
    's hooks section): modules/intake/app.py's startup(ctx) registers the
    "interview_reminder" hook — proven here WITHOUT going through
    reminders.py, so this test only depends on app_modules + intake's own
    entrypoint, never on reminders.py's internals."""

    def setUp(self):
        super().setUp()
        app_modules.hooks.clear()
        app_modules._STARTED.discard("intake")

    def test_ensure_started_registers_intakes_hook_when_enabled(self):
        self._set_enabled("turnos,intake")
        app_modules.ensure_started()
        self.assertIn("interview_reminder", app_modules.hooks._registry)
        owners = [mid for mid, _fn in app_modules.hooks._registry["interview_reminder"]]
        self.assertIn("intake", owners)

    def test_hook_call_reaches_notifications_notify_interview_reminder(self):
        self._set_enabled("turnos,intake")
        from app.notifications import send as notifications
        calls = []
        with patch.object(notifications, "notify_interview_reminder",
                           side_effect=lambda row, handler=None: calls.append(row)):
            app_modules.hooks.call("interview_reminder", {"id": 42})
        self.assertEqual(calls, [{"id": 42}])

    def test_hook_never_fires_when_intake_is_disabled(self):
        self._set_enabled("turnos")  # intake NOT enabled
        from app.notifications import send as notifications
        calls = []
        with patch.object(notifications, "notify_interview_reminder",
                           side_effect=lambda row, handler=None: calls.append(row)):
            app_modules.hooks.call("interview_reminder", {"id": 1})
        self.assertEqual(calls, [])


class TestLifecycleStartupShutdown(_FakeConfigStore):
    def setUp(self):
        super().setUp()
        app_modules.hooks.clear()
        app_modules._STARTED.discard("intake")
        app_modules._RUNTIME_BROKEN.pop("intake", None)

    def test_startup_one_is_idempotent(self):
        self._set_enabled("turnos,intake")
        self.assertTrue(app_modules.startup_one("intake"))
        self.assertTrue(app_modules.startup_one("intake"))  # no-op, no error
        self.assertIn("intake", app_modules._STARTED)

    def test_startup_one_unknown_module_returns_false(self):
        self.assertFalse(app_modules.startup_one("not-a-real-module"))

    def test_shutdown_one_clears_started_flag(self):
        self._set_enabled("turnos,intake")
        app_modules.startup_one("intake")
        self.assertIn("intake", app_modules._STARTED)
        app_modules.shutdown_one("intake")
        self.assertNotIn("intake", app_modules._STARTED)

    def test_a_broken_entrypoint_is_swallowed_and_marks_the_module_broken(self):
        with patch("importlib.import_module", side_effect=RuntimeError("boom")):
            ok = app_modules.startup_one("intake")
        self.assertFalse(ok)
        self.assertIn("intake", app_modules._RUNTIME_BROKEN)


if __name__ == "__main__":
    unittest.main()
