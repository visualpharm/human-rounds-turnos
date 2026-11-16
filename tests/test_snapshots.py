"""Generated test snapshots (tests/snapshots.py): they are current, and the
invariants that used to be hand-kept registries still hold.

The per-area tests (route census, data map, module manifest, rendered pages)
each compare their own slice with `tests/snapshots.json` and say what moved;
this file is the one place that proves the WHOLE set is what a fresh run
produces, so a section nobody compares (an orphaned key, a removed page) cannot
go stale unnoticed. The fix for a red test here is always
`python3 tests/snapshots.py --write` — read what it lists as changed first.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
import _probe_rows  # noqa: E402
import snapshots  # noqa: E402


class TestOfflineEnvironment(unittest.TestCase):
    """`_probe_rows.offline_environment()` builds every generated snapshot and the route probe:
    it must hide the developer's own configuration and give all of it back."""

    def test_restores_exact_presence_and_values(self):
        keys = _probe_rows._ENV_KEYS
        _env.set_env(self, {
            "DATABASE_URL": "postgresql://must-not-be-read.invalid/db",
            "TURNOS_DEMO_MODE": "1",
            "TURNOS_ADMIN_SECRET": "deployment-secret-must-not-be-read",
            "TURNOS_AUTH_MODE": None,
        })
        expected = {key: (key in os.environ, os.environ.get(key)) for key in keys}
        with _probe_rows.offline_environment():
            self.assertNotIn("DATABASE_URL", os.environ)
            self.assertEqual(os.environ["TURNOS_DEMO_MODE"], "0")
            self.assertEqual(os.environ["TURNOS_ENV_FILE"], "/dev/null")
            self.assertEqual(os.environ["TURNOS_CONFIG_LOCK"], "1")
            self.assertEqual(os.environ["TURNOS_ADMIN_SECRET"], "fallback-census-offline-only")
        self.assertEqual({key: (key in os.environ, os.environ.get(key)) for key in keys}, expected)

    def test_invalidates_loaded_auth_mode_cache(self):
        from app.auth import mode as auth_mode

        auth_mode._cache["v"] = "renaper"
        auth_mode._cache["t"] = 9999999999.0
        with _probe_rows.offline_environment():
            self.assertIsNone(auth_mode._cache["v"])
            auth_mode._cache["v"] = "renaper"
            auth_mode._cache["t"] = 9999999999.0
        self.assertIsNone(auth_mode._cache["v"])


class TestSnapshotsAreCurrent(unittest.TestCase):
    def test_every_generated_file_matches_a_fresh_run(self):
        for path, text in snapshots._render(snapshots._collect()).items():
            with self.subTest(file=os.path.relpath(path, ROOT)):
                self.assertEqual(
                    snapshots._read(path), text,
                    "stale — run: python3 tests/snapshots.py --write")

    def test_every_snapshot_section_is_declared(self):
        declared = {name for name, (_emit, target) in snapshots.SECTIONS.items()
                    if target is None}
        self.assertEqual(set(snapshots.load()), declared)


class TestAdminAccessMap(unittest.TestCase):
    """`core/permissions._PREFIXES` is the one map from admin route to the
    permission it needs; these hold it to the routes and permissions that exist."""

    @classmethod
    def setUpClass(cls):
        with _probe_rows.offline_environment():
            from app import main as app
            cls.paths = [route.path for route in _probe_rows.flat_routes(app.app.routes, [])]

    def test_every_prefix_guards_a_real_route(self):
        from app.core import permissions

        dead = sorted(
            prefix for prefix in permissions._PREFIXES
            if not any(path == prefix or path.startswith(prefix + "/")
                       for path in self.paths))
        self.assertEqual(
            dead, [],
            "permission prefixes that match no route — a renamed route leaves "
            "its guard behind and the new path silently falls to the legacy rule")

    def test_every_prefix_names_a_real_permission(self):
        from app.core import permissions

        for prefix, needed in permissions._PREFIXES.items():
            for permission in (needed if isinstance(needed, frozenset) else (needed,)):
                with self.subTest(prefix=prefix):
                    self.assertTrue(
                        permission == permissions.ANY or permission in permissions.PERMISSIONS,
                        "%s needs %r, which is not a permission" % (prefix, permission))

    def test_snapshot_labels_are_known_permissions(self):
        from app.core import permissions

        known = {"any", "unmapped"} | set(permissions.PERMISSIONS)
        for route, label in snapshots.load()["admin_access"].items():
            with self.subTest(route=route):
                self.assertTrue(set(label.split("|")) <= known, label)


if __name__ == "__main__":
    unittest.main()
