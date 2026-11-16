"""The two guards of the municipal installation (salud.pinamar.gob.ar), held by tests.

1. The test portal (`TURNOS_PORTAL_IDENTITY_MODE=test-portal-v1`) never runs
   there: the process does not start on a `*.gob.ar` origin, nor with
   `TURNOS_STAFF_PORTAL_LOGIN=1`; on a test stand (pinamar.humanrounds.org)
   it starts, wrapped in the test gateway.
2. The application has no right to delete backups there: the remote purge is
   off on a `*.gob.ar` origin whatever the environment or the panel says; if
   `TURNOS_BACKUP_PURGE_REMOTE` asks for it (other than 0) the service still
   starts, with an ERROR in the log at start and in every backup pass and a
   warning in the «Sistema» B2 block; on the test stands and the demo the
   purge is on, as before, unless `=0`.

Both read the same marker, `core.config.government_installation`.

Run:  python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_salud_guards.py
"""

import json
import os
import sys
import types
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import asgi_main  # noqa: E402
from app import worker  # noqa: E402
from app.admin_panel import system_status  # noqa: E402
from app.core import config  # noqa: E402
from app.cuentas import portal_identity, test_portal  # noqa: E402
from app.ops import data_backups, demo_installation, purge_guard  # noqa: E402
import _env  # noqa: E402

_GOV = "https://salud.pinamar.gob.ar"
_STANDS = ("https://pinamar.humanrounds.org", "https://demo.humanrounds.org")
_PEOPLE = [{"subject": "tp-laura", "cuit": "27990000013", "email": "laura@example.org",
            "name": "Laura Prueba", "password": test_portal.hash_password("x", iterations=1000)}]
# The environment of a test stand with the test portal; the tests change one thing at a time.
_PORTAL_ENV = {
    "TURNOS_ASGI": "1",
    "TURNOS_PORTAL_IDENTITY_MODE": "test-portal-v1",
    "TURNOS_PATIENT_PORTAL_LOGIN": "1",
    "TURNOS_STAFF_PORTAL_LOGIN": "",
    "TURNOS_PORTAL_IDENTITY_TRUSTED_PEERS": "[]",
    "TURNOS_PORTAL_IDENTITY_GATEWAY_HOST": "",
    "TURNOS_PORTAL_IDENTITY_BINDINGS": "",
    "TURNOS_PUBLIC_ORIGIN": "https://pinamar.humanrounds.org",
    "TURNOS_TEST_PORTAL_SECRET": "s" * 40,
    "TURNOS_TEST_PORTAL_USERS": json.dumps(_PEOPLE),
    "TURNOS_BACKUP_PURGE_REMOTE": "",
    "TURNOS_DEMO_MODE": "0",
}


class _Env(unittest.TestCase):
    def setUp(self):
        self.enterContext(_env.patched_env(_PORTAL_ENV))
        # Nothing from a checkout's .env reaches a guard under test.
        self.enterContext(patch.object(config, "dotenv_values", return_value={}))

    def origin(self, value):
        self.enterContext(_env.patched_env(TURNOS_PUBLIC_ORIGIN=value))


class TestMarker(_Env):
    def test_government_origins(self):
        for value in (_GOV, "https://SALUD.Pinamar.GOB.AR", "https://gob.ar", "https://a.b.pinamar.gob.ar:8443/x",
                      "salud.pinamar.gob.ar", "https://salud.pinamar.gob.ar./", "  https://salud.pinamar.gob.ar  ",
                      "http://[::1"):  # unreadable: fail closed
            with self.subTest(origin=value):
                self.origin(value)
                self.assertTrue(config.government_installation())

    def test_everything_else_is_not(self):
        for value in _STANDS + ("", "https://notgob.ar", "https://salud.pinamar.gob.ar.example.com",
                                "https://gob.ar.example.com", "https://pinamar.gob.ar.humanrounds.org"):
            with self.subTest(origin=value):
                self.origin(value)
                self.assertFalse(config.government_installation())

    def test_the_env_file_counts_when_the_process_has_nothing(self):
        self.origin("")
        with patch.object(config, "dotenv_values", return_value={"TURNOS_PUBLIC_ORIGIN": _GOV}):
            self.assertTrue(config.government_installation())

    def test_the_database_cannot_move_it(self):
        self.origin(_STANDS[0])
        with patch.object(config, "_db_overrides", return_value={"TURNOS_PUBLIC_ORIGIN": _GOV}):
            self.assertFalse(config.government_installation())
        self.origin(_GOV)
        with patch.object(config, "_db_overrides", return_value={"TURNOS_PUBLIC_ORIGIN": _STANDS[0]}):
            self.assertTrue(config.government_installation())


class TestRemotePurge(_Env):
    def setUp(self):
        super().setUp()
        self.log = self.enterContext(patch.object(purge_guard, "_log"))

    def errors(self):
        return [c.args[0] for c in self.log.call_args_list if c.args[0].startswith("ERROR")]

    def test_government_without_the_variable_purge_is_off_and_quiet(self):
        self.origin(_GOV)
        self.assertFalse(purge_guard.remote_purge_enabled())
        self.assertIsNone(data_backups._purge_ops_default())
        self.assertFalse(purge_guard.remote_purge_ignored())
        self.assertFalse(purge_guard.warn_purge_ignored("start"))
        self.assertEqual(self.errors(), [])

    def test_government_with_zero_purge_is_off_and_quiet(self):
        self.origin(_GOV)
        with _env.patched_env({"TURNOS_BACKUP_PURGE_REMOTE": "0"}):
            self.assertFalse(purge_guard.remote_purge_enabled())
            self.assertFalse(purge_guard.remote_purge_ignored())
            self.assertFalse(purge_guard.warn_purge_ignored("start"))
        self.assertEqual(self.errors(), [])

    def test_government_asked_to_purge_stays_locked_and_says_so_loudly(self):
        self.origin(_GOV)
        for value in ("1", "true", "yes", " 1 "):
            with self.subTest(value=value), _env.patched_env({"TURNOS_BACKUP_PURGE_REMOTE": value}):
                self.log.reset_mock()
                self.assertFalse(purge_guard.remote_purge_enabled())
                self.assertIsNone(data_backups._purge_ops_default())
                self.assertTrue(purge_guard.remote_purge_ignored())
                self.assertTrue(purge_guard.warn_purge_ignored("start"))
                (line,) = self.errors()
                self.assertIn("TURNOS_BACKUP_PURGE_REMOTE", line)
                self.assertIn("ignored", line)
                self.assertIn("start", line)
                self.assertNotIn("=" + value.strip(), line)  # never a value

    def test_government_asked_through_the_env_file_is_locked_and_reported(self):
        self.origin(_GOV)
        with patch.object(config, "_dotenv", return_value={"TURNOS_BACKUP_PURGE_REMOTE": "1"}):
            self.assertFalse(purge_guard.remote_purge_enabled())
            self.assertTrue(purge_guard.remote_purge_ignored())

    def test_government_the_panel_cannot_turn_it_on_and_is_reported(self):
        self.origin(_GOV)
        with patch.object(purge_guard, "_env", return_value="1"):
            self.assertFalse(purge_guard.remote_purge_enabled())
            self.assertIsNone(data_backups._purge_ops_default())
            self.assertTrue(purge_guard.remote_purge_ignored())

    def test_test_stands_keep_the_purge_on_without_the_variable(self):
        for stand in _STANDS:
            with self.subTest(stand=stand):
                self.origin(stand)
                self.assertTrue(purge_guard.remote_purge_enabled())
                ops = data_backups._purge_ops_default()
                self.assertEqual(len(ops), 2)
                self.assertTrue(all(callable(fn) for fn in ops))
                self.assertFalse(purge_guard.remote_purge_ignored())

    def test_test_stands_still_turn_it_off_with_zero(self):
        self.origin(_STANDS[0])
        with _env.patched_env({"TURNOS_BACKUP_PURGE_REMOTE": "0"}):
            self.assertFalse(purge_guard.remote_purge_enabled())
            self.assertIsNone(data_backups._purge_ops_default())
            self.assertFalse(purge_guard.remote_purge_ignored())

    def test_test_stands_with_one_purge_and_nothing_to_report(self):
        self.origin(_STANDS[0])
        with _env.patched_env({"TURNOS_BACKUP_PURGE_REMOTE": "1"}):
            self.assertTrue(purge_guard.remote_purge_enabled())
            self.assertFalse(purge_guard.remote_purge_ignored())
            self.assertFalse(purge_guard.warn_purge_ignored("start"))
        self.assertEqual(self.errors(), [])

    def _sweep(self):
        with patch.object(data_backups, "configured", return_value=True), \
                patch.object(data_backups, "_sweep_lock") as lock, \
                patch.object(data_backups, "_tick_unlocked", return_value=0) as sweep:
            lock.return_value.__enter__.return_value = object()
            data_backups.tick()
        return sweep.call_args.kwargs["purge_ops"]

    def test_a_real_sweep_on_government_carries_no_remote_deletion(self):
        self.origin(_GOV)
        self.assertIsNone(self._sweep())
        self.assertEqual(self.errors(), [])
        self.origin(_STANDS[0])
        self.assertEqual(len(self._sweep()), 2)

    def test_every_pass_on_government_with_the_purge_asked_logs_the_error(self):
        self.origin(_GOV)
        with _env.patched_env({"TURNOS_BACKUP_PURGE_REMOTE": "1"}):
            for n in (1, 2, 3):
                self.assertIsNone(self._sweep())
                self.assertEqual(len(self.errors()), n)
        self.assertTrue(all("backup pass" in line for line in self.errors()))

    def test_the_backup_module_reads_the_same_lock(self):
        self.assertIs(data_backups.remote_purge_enabled, purge_guard.remote_purge_enabled)
        self.assertIs(data_backups.warn_purge_ignored, purge_guard.warn_purge_ignored)

    def test_the_start_of_the_web_process_does_not_pull_in_the_backup_module(self):
        # The guard is light on purpose: the entrypoint reports before the packs are known-good.
        import subprocess
        code = ("import sys; sys.path.insert(0, sys.argv[1]); from app.ops import purge_guard; "
                "print('app.ops.data_backups' in sys.modules, 'app.core.db' in sys.modules)")
        out = subprocess.run([sys.executable, "-I", "-c", code, ROOT], cwd=ROOT, capture_output=True, text=True,
                             env={"PATH": os.environ.get("PATH", "")}, check=True)
        self.assertEqual(out.stdout.strip(), "False False")

    def test_the_worker_registers_the_sweep_on_government_with_the_purge_asked_and_says_so(self):
        self.origin(_GOV)
        with _env.patched_env({"TURNOS_BACKUP_PURGE_REMOTE": "1"}), \
                patch.object(worker.data_backups_queue, "register") as register:
            worker._register_data_backups("app")
        register.assert_called_once()
        (line,) = self.errors()
        self.assertIn("worker start", line)

    def test_the_worker_registers_the_sweep_on_government_without_the_variable(self):
        self.origin(_GOV)
        with patch.object(worker.data_backups_queue, "register") as register:
            worker._register_data_backups("app")
        register.assert_called_once()
        self.assertEqual(self.errors(), [])


class TestSistemaBlock(_Env):
    def setUp(self):
        super().setUp()
        self.enterContext(_env.patched_env(TURNOS_B2_KEY_ID="k", TURNOS_B2_APP_KEY="a", TURNOS_B2_BUCKET_ID="b"))

    def _state(self, **over):
        base = {"last_success_at": None, "pending": 3, "denied": 0, "closed": 0, "failed": 0, "last_pass_at": None}
        base.update(over)
        return patch("app.core.db_backup.backup_purge_state", return_value=base)

    def _items(self):
        with self._state() as state:
            items = system_status._b2_purge_items()
        return items, state

    def test_government_shows_no_purge_line_and_does_not_read_the_state(self):
        self.origin(_GOV)
        items, state = self._items()
        self.assertEqual(items, [])
        state.assert_not_called()

    def test_government_with_zero_the_same(self):
        self.origin(_GOV)
        with _env.patched_env({"TURNOS_BACKUP_PURGE_REMOTE": "0"}):
            self.assertEqual(self._items()[0], [])

    def test_government_asked_to_purge_shows_one_warning_and_does_not_read_the_state(self):
        self.origin(_GOV)
        with _env.patched_env({"TURNOS_BACKUP_PURGE_REMOTE": "1"}):
            items, state = self._items()
            (item,) = items
            self.assertEqual((item["estado"], item["valor"]), ("warn", "Ignorada en esta instalación"))
            self.assertIn("TURNOS_BACKUP_PURGE_REMOTE=1 se ignora en esta instalación", item["detalle"])
            state.assert_not_called()
            with self._state():
                block = system_status._cluster_b2()
            self.assertEqual(block["estado"], "warn")
            self.assertIn("se ignora", block["resumen"])
            self.assertEqual(block["items"][-1]["estado"], "warn")

    def test_the_warning_is_translated_in_every_language(self):
        from app.core import i18n
        self.origin(_GOV)
        seen = set()
        with _env.patched_env({"TURNOS_BACKUP_PURGE_REMOTE": "1"}):
            for lang in ("es", "en", "pt", "fr", "it", "de", "ru", "uk", "ja", "zh"):
                with patch.object(i18n, "active", return_value=lang):
                    block = system_status._cluster_b2()
                item = block["items"][-1]
                for text in (item["label"], item["valor"], item["detalle"], block["resumen"]):
                    self.assertTrue(text, lang)
                self.assertIn("TURNOS_BACKUP_PURGE_REMOTE=1", item["detalle"], lang)
                seen.add(item["valor"])
        self.assertEqual(len(seen), 10)  # ten different texts: none fell back to the Spanish

    def test_test_stand_with_one_shows_the_state_not_the_warning(self):
        self.origin(_STANDS[0])
        with _env.patched_env({"TURNOS_BACKUP_PURGE_REMOTE": "1"}):
            items, state = self._items()
        self.assertEqual(len(items), 2)
        state.assert_called_once()

    def test_test_stand_shows_the_state_of_the_purge(self):
        self.origin(_STANDS[0])
        items, state = self._items()
        self.assertEqual(len(items), 2)
        state.assert_called_once()


def _boot():
    """`asgi_main._asgi_mode()` up to uvicorn.run; returns the mock of run."""
    fake_app = types.SimpleNamespace(app=object())
    fake_uvicorn = types.SimpleNamespace(run=lambda *a, **kw: None)
    return fake_app, fake_uvicorn


class TestBoot(_Env):
    def boot(self):
        fake_app, fake_uvicorn = _boot()
        with patch.dict(sys.modules, {"app.main": fake_app, "uvicorn": fake_uvicorn}), \
                patch.object(asgi_main, "_verify_required_packs"), \
                patch.object(demo_installation, "apply_safety_defaults"), \
                patch.object(fake_uvicorn, "run") as run:
            try:
                asgi_main._asgi_mode()
            finally:
                self.run = run
                self.fake_app = fake_app

    # the purge guard

    def test_government_boots_without_the_purge_variable(self):
        self.origin(_GOV)
        with _env.patched_env({"TURNOS_PORTAL_IDENTITY_MODE": "off", "TURNOS_PATIENT_PORTAL_LOGIN": ""}):
            self.boot()
        self.run.assert_called_once()

    def test_government_boots_with_the_purge_asked_and_says_so(self):
        self.origin(_GOV)
        with _env.patched_env({"TURNOS_PORTAL_IDENTITY_MODE": "off", "TURNOS_PATIENT_PORTAL_LOGIN": "",
                                     "TURNOS_BACKUP_PURGE_REMOTE": "1"}), \
                patch.object(purge_guard, "_log") as log:
            self.boot()
        self.run.assert_called_once()
        lines = [c.args[0] for c in log.call_args_list]
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("ERROR"))
        self.assertIn("(start)", lines[0])

    def test_government_boots_with_the_purge_off_and_says_nothing(self):
        self.origin(_GOV)
        with _env.patched_env({"TURNOS_PORTAL_IDENTITY_MODE": "off", "TURNOS_PATIENT_PORTAL_LOGIN": "", "TURNOS_BACKUP_PURGE_REMOTE": "0"}), \
                patch.object(purge_guard, "_log") as log:
            self.boot()
        self.run.assert_called_once()
        log.assert_not_called()

    def test_a_test_stand_boots_with_the_purge_asked_or_not(self):
        for value in ("", "0", "1"):
            with self.subTest(value=value), _env.patched_env({"TURNOS_BACKUP_PURGE_REMOTE": value}):
                self.boot()
                self.run.assert_called_once()

    # the test-portal-v1 guard

    def test_the_test_portal_boots_on_a_test_stand(self):
        for stand in _STANDS:
            with self.subTest(stand=stand):
                self.origin(stand)
                self.boot()
                self.run.assert_called_once()
                self.assertIsInstance(self.run.call_args.args[0], test_portal.TestPortalGateway)

    def test_the_test_portal_does_not_boot_on_government(self):
        for value in (_GOV, "https://gob.ar", "https://x.salud.pinamar.gob.ar"):
            with self.subTest(origin=value):
                self.origin(value)
                with self.assertRaises(test_portal.ConfigurationError):
                    self.boot()
                self.run.assert_not_called()

    def test_the_test_portal_does_not_boot_with_the_staff_portal_login(self):
        # With the real gateway in front the flag would be legitimate; with the test portal
        # a test person could be mapped to an operator, so the process stops. The gateway
        # boundary of the flag itself is checked first, hence both errors are acceptable.
        with _env.patched_env({"TURNOS_STAFF_PORTAL_LOGIN": "1"}):
            with self.assertRaises((test_portal.ConfigurationError, portal_identity.ConfigurationError)):
                self.boot()
        self.run.assert_not_called()

    def test_the_test_portal_on_government_with_the_staff_portal_login_does_not_boot_either(self):
        self.origin(_GOV)
        with _env.patched_env({"TURNOS_STAFF_PORTAL_LOGIN": "1"}):
            with self.assertRaises((test_portal.ConfigurationError, portal_identity.ConfigurationError)):
                self.boot()
        self.run.assert_not_called()


class TestTestPortalValidation(_Env):
    def test_stand_validates(self):
        for stand in _STANDS:
            with self.subTest(stand=stand):
                self.origin(stand)
                test_portal.validate_configuration()

    def test_government_is_refused_through_the_shared_marker(self):
        self.origin(_GOV)
        with self.assertRaises(test_portal.ConfigurationError):
            test_portal.validate_configuration()
        with patch.object(config, "government_installation", return_value=False):
            test_portal.validate_configuration()  # the refusal is the marker's, not a copy of it

    def test_the_staff_portal_login_is_refused(self):
        with _env.patched_env({"TURNOS_STAFF_PORTAL_LOGIN": "1"}):
            with self.assertRaises(test_portal.ConfigurationError):
                test_portal.validate_configuration()


if __name__ == "__main__":
    unittest.main()
