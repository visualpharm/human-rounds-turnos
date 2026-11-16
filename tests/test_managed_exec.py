"""Synthetic subprocess checks for fixed managed execution profiles."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.path.insert(0, str(ROOT / "scripts"))
import turnos_managed_exec as managed_exec  # noqa: E402
import _env  # noqa: E402


class ManagedExecTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="turnos-managed-exec-")
        self.addCleanup(self.dir.cleanup)
        self.source = Path(self.dir.name, "source.json")
        self.source.write_text(json.dumps({"profiles": {
            "backend-focused": {
                "DATABASE_URL": "postgresql://fixture:profile-child-sentinel@127.0.0.1/fixture",
            },
            "backend-full": {
                "DATABASE_URL": "postgresql://fixture:check-only@127.0.0.1/fixture",
            },
            "local-ci": {
                "DATABASE_URL": "postgresql://fixture:asgi-profile-child-sentinel@127.0.0.1/fixture",
            },
            "asgi": {
                "SACOTURNO_REMOTE_URL": "https://synthetic.invalid/vault",
            },
        }}), encoding="utf-8")
        self.source.chmod(0o600)

    def test_profile_command_is_fixed_and_environment_is_scrubbed(self):
        command, env = managed_exec.resolve(
            "backend-focused", "tests/managed_exec_child.py", f"file:{self.source}")
        self.assertEqual(command[-1], "managed_exec_child.py")
        self.assertEqual(env["TURNOS_ENV_FILE"], os.devnull)
        self.assertNotIn("HOME", env)
        self.assertNotIn("TURNOS_SECRET_SOURCE", env)
        self.assertNotIn("LEAKED_AMBIENT", env)
        self.assertNotIn("OP_SERVICE_ACCOUNT_TOKEN", env)
        self.assertEqual(set(env) & {"DATABASE_URL", "TEST_DATABASE_URL"}, {"DATABASE_URL"})
        _command, clean_env = managed_exec.resolve("backend-full", None, f"file:{self.source}")
        self.assertNotIn("profile-child-sentinel", clean_env["DATABASE_URL"])

    def test_check_mode_is_silent_and_never_echoes_source_data(self):
        marker = "check-only"
        with patch.object(managed_exec, "resolve", return_value=(
            [sys.executable, "-c", "raise SystemExit(9)"], {"DATABASE_URL": marker},
        )), _env.patched_env({managed_exec.managed_secrets.BROKER_ENV_NAME: "injected"}), \
             patch.object(managed_exec.os, "execve") as execve, \
             patch("sys.stdout", new_callable=io.StringIO) as stdout, \
             patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.assertEqual(managed_exec.main(["--profile", "backend-full", "--check"]), 0)
        execve.assert_not_called()
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn(marker, stdout.getvalue() + stderr.getvalue())

    def test_missing_document_hops_through_the_vault_broker_with_a_fixed_environment(self):
        ambient = {"LEAKED_AMBIENT": "must-not-reach-broker", "OP_SERVICE_ACCOUNT_TOKEN": "parent-only",
                   "DATABASE_URL": "caller-value"}
        ambient.update({managed_exec.managed_secrets.BROKER_ENV_NAME: None, managed_exec._BROKER_HOP: None})
        with _env.patched_env(ambient), \
             patch.object(managed_exec.os, "execve", side_effect=SystemExit(0)) as execve:
            with self.assertRaises(SystemExit):
                managed_exec.main(["--profile", "routes"])
        path, argv, env = execve.call_args.args
        self.assertEqual(path, argv[0])
        self.assertEqual(argv[:4], [str(managed_exec._BROKER_PYTHON), "-P", "-m", "modules.secrets.onepassword"])
        self.assertEqual(argv[6:9], ["run", managed_exec.managed_secrets.BROKER_APPLICATION, "--"])
        self.assertEqual(argv[9:], [str(ROOT / "scripts" / "turnos_managed_exec.py"), "--profile", "routes"])
        self.assertEqual(set(env), {"PATH", "HOME", "PYTHONPATH", "PYTHONDONTWRITEBYTECODE", managed_exec._BROKER_HOP})

    def test_broker_hop_without_document_and_rejected_selectors_never_reach_the_broker(self):
        with patch.object(managed_exec.os, "execve") as execve, \
             patch("sys.stderr", new_callable=io.StringIO):
            with _env.patched_env({managed_exec._BROKER_HOP: "1", managed_exec.managed_secrets.BROKER_ENV_NAME: None}):
                self.assertEqual(managed_exec.main(["--profile", "routes"]), 2)
            self.assertEqual(managed_exec.main(
                ["--profile", "backend-full", "--test", "tests/test_managed_exec.py"]), 2)
            self.assertEqual(managed_exec.main(["--profile", "backend-focused"]), 2)
        execve.assert_not_called()

    def test_e2e_focused_runs_exactly_one_spec_file_and_nothing_else(self):
        command, env = managed_exec.resolve(
            "e2e-focused", "tests/e2e/account-delayed.spec.mjs", f"file:{self.source}")
        self.assertEqual(command[-4:], ["playwright", "test", "--workers=1", "tests/e2e/account-delayed.spec.mjs"])
        self.assertEqual(env["TURNOS_ASGI"], "1")
        self.assertEqual(env["TURNOS_FAKE_AI"], "1")
        self.assertEqual(env["TURNOS_MANAGED_PROFILE"], "e2e-focused")
        for selector in (None, "", "tests/e2e/../../.env", "tests/e2e/lib/fixtures.mjs", "tests/e2e/a.spec.mjs x",
                         "tests/e2e/*.spec.mjs", "tests/test_managed_exec.py", "-x", "../test/e2e/a.spec.mjs"):
            with self.subTest(selector=selector):
                with self.assertRaises(ValueError):
                    managed_exec.PROFILES["e2e-focused"].command(selector)
        with self.assertRaises(ValueError):
            managed_exec.PROFILES["backend-focused"].command("tests/e2e/account-delayed.spec.mjs")

    def test_asgi_profile_admits_the_known_sacoturno_runtime_namespace(self):
        # The "app" profile (python3 server.py) retired with the legacy
        # topology (final wave); "asgi" is the one live-app profile and
        # keeps the same runtime-key admission contract.
        with _env.patched_env({"TURNOS_ASGI": "0"}):
            command, environment = managed_exec.resolve("asgi", None, f"file:{self.source}")
        self.assertEqual(command, [sys.executable, "asgi_main.py"])
        # asgi_main.py refuses to boot without TURNOS_ASGI=1, so the profile
        # sets it itself and the caller cannot switch it off.
        self.assertEqual(environment["TURNOS_ASGI"], "1")
        self.assertEqual(environment["SACOTURNO_REMOTE_URL"], "https://synthetic.invalid/vault")
        self.assertTrue(managed_exec.managed_secrets.is_runtime_key("SACOTURNO_REMOTE_URL"))
        self.assertTrue(managed_exec.managed_secrets.is_runtime_key("INTAKE_PER_HOUR"))
        self.assertFalse(managed_exec.managed_secrets.is_runtime_key("ARBITRARY_REMOTE_URL"))

    def test_real_profile_subprocess_scrubs_ambient_environment_without_outputting_fixture(self):
        marker = "profile-child-sentinel"
        command, environment = managed_exec.resolve(
            "backend-focused", "tests/managed_exec_child.py", f"file:{self.source}")
        result = subprocess.run(command, cwd=ROOT, env=environment, text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(marker, result.stdout + result.stderr)
        self.assertNotIn("LEAKED_AMBIENT", result.stdout + result.stderr)
        self.assertNotIn("provider-parent-only", result.stdout + result.stderr)

    def test_live_cli_refuses_file_selector_without_opening_or_echoing_it(self):
        marker = "file-selector-never-echoed"
        result = subprocess.run(
            [sys.executable, "scripts/turnos_managed_exec.py", "--profile", "backend-full", "--check"],
            cwd=ROOT,
            env={
                "PATH": os.environ.get("PATH", ""),
                "TURNOS_SECRET_SOURCE": f"file:{marker}",
            },
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertNotIn(marker, result.stderr)

    def test_profile_rejects_arbitrary_command_text_and_control_environment_keys(self):
        with self.assertRaises(ValueError):
            managed_exec.resolve("backend-full", "tests/test_managed_exec.py", f"file:{self.source}")
        self.source.write_text(json.dumps({"profiles": {
            "backend-full": {"TURNOS_ENV_FILE": "/not-allowed"},
        }}), encoding="utf-8")
        with self.assertRaises(managed_exec.managed_secrets.SecretSourceError):
            managed_exec.resolve("backend-full", None, f"file:{self.source}")

    def test_local_ci_asgi_is_fixed_db_only_and_does_not_change_legacy_local_ci(self):
        ambient = {
            "TURNOS_ASGI": "0",
            "TURNOS_ASGI_SOURCE": "caller-control",
            "TURNOS_SECRET_SOURCE": "caller-source",
            "HOME": "/caller-home",
            "PYTHONPATH": "/caller-pythonpath",
            "PYTHONHOME": "/caller-pythonhome",
            "VIRTUAL_ENV": "/caller-venv",
            "OP_SERVICE_ACCOUNT_TOKEN": "provider-parent-only",
            "LEAKED_AMBIENT": "must-not-reach-child",
        }
        with _env.patched_env(ambient, clear=False):
            legacy_command, legacy_env = managed_exec.resolve(
                "local-ci", None, f"file:{self.source}")
            asgi_command, asgi_env = managed_exec.resolve(
                "local-ci-asgi", None, f"file:{self.source}")

        self.assertEqual(legacy_command, ["/bin/bash", "scripts/ci_local.sh"])
        self.assertEqual(asgi_command, legacy_command)
        self.assertNotIn("TURNOS_ASGI", legacy_env)
        self.assertEqual(asgi_env["TURNOS_ASGI"], "1")
        self.assertEqual(asgi_env["DATABASE_URL"], legacy_env["DATABASE_URL"])
        self.assertEqual(asgi_env["TURNOS_ENV_FILE"], os.devnull)
        self.assertEqual(
            set(asgi_env) & {"DATABASE_URL", "TEST_DATABASE_URL"}, {"DATABASE_URL"})
        for key in (
            "TURNOS_SECRET_SOURCE", "TURNOS_ASGI_SOURCE", "HOME", "PYTHONPATH",
            "PYTHONHOME", "VIRTUAL_ENV", "OP_SERVICE_ACCOUNT_TOKEN", "LEAKED_AMBIENT",
        ):
            self.assertNotIn(key, asgi_env)

    def test_narrow_browser_profiles_are_fixed_db_only_not_command_selectors(self):
        for profile, filename in (
            ("e2e-account-asgi", "account-lifecycle.spec.mjs"),
            ("e2e-account-delayed-asgi", "account-delayed.spec.mjs"),
            ("e2e-account-reset-asgi", "account-reset-keeps-booking.spec.mjs"),
            ("e2e-home-listing-asgi", "listing-delayed.spec.mjs"),
            ("e2e-home-search-asgi", "search-delayed.spec.mjs"),
            ("e2e-public-chrome-asgi", "public-chrome.spec.mjs"),
            ("e2e-ai-off-asgi", "ai-off.spec.mjs"),
            ("e2e-journey-j10-asgi", "journey-j10-walkin-handoff.spec.mjs"),
            ("e2e-journey-j11-asgi", "journey-j11-booking-to-prescription.spec.mjs"),
            ("e2e-journey-j12-asgi", "journey-j12-reopen-confirmed-visit.spec.mjs"),
            ("e2e-journey-j13-asgi", "journey-j13-schedule-manager-slots.spec.mjs"),
            ("e2e-journey-j14-asgi", "journey-j14-disable-doctor-archive-patient.spec.mjs"),
            ("e2e-journey-j15-asgi", "journey-j15-doctor-sees-own-patients.spec.mjs"),
            ("e2e-journey-j16-asgi", "journey-j16-referral-approval-reminder.spec.mjs"),
            ("e2e-system-asgi", "admin-system-lifecycle.spec.mjs"),
            ("e2e-settings-asgi", "admin-settings-lifecycle.spec.mjs"),
            ("e2e-plan-asgi", "admin-plan-lifecycle.spec.mjs"),
            ("e2e-alerts-asgi", "admin-shell.spec.mjs"),
            ("e2e-reports-asgi", "admin-reports-lifecycle.spec.mjs"),
            ("e2e-appearance-asgi", "admin-appearance-lifecycle.spec.mjs"),
            ("e2e-hsi-asgi", "admin-hsi-lifecycle.spec.mjs"),
            ("e2e-patients-native-asgi", "admin-patients-lifecycle.spec.mjs"),
            ("e2e-doctor-native-asgi", "admin-doctor-lifecycle.spec.mjs"),
            ("e2e-preparations-native-asgi", "admin-preparations-lifecycle.spec.mjs"),
            ("e2e-staff-account-asgi", "admin-staff-account-lifecycle.spec.mjs"),
            ("e2e-patient-portal-asgi", "patient-portal.spec.mjs"),
            ("e2e-consultation-asgi", "admin-consultation-lifecycle.spec.mjs"),
            ("e2e-day-queue-asgi", "admin-day-queue-lifecycle.spec.mjs"),
            ("e2e-catalog-native-asgi", "catalog-screens-lifecycle.spec.mjs"),
            ("e2e-create-appointment-context-asgi", "create-appointment-context.spec.mjs"),
            ("e2e-queue-sse-asgi", "queue-sse.spec.mjs"),
            ("e2e-tiles-asgi", "tiles-canonical.spec.mjs"),
            ("e2e-admin-auth-asgi", "admin.spec.mjs"),
            ("e2e-english-labels-asgi", "english-labels-icons.spec.mjs"),
            ("e2e-footer-small-asgi", "footer-small.spec.mjs"),
        ):
            with self.subTest(profile=profile), _env.patched_env({
                "TURNOS_ASGI": "0", "NODE_OPTIONS": "--require=caller.js",
                "TURNOS_E2E_ALLOW_SKIP": "1", "OP_SERVICE_ACCOUNT_TOKEN": "parent-only",
            }):
                command, env = managed_exec.resolve(profile, None, f"file:{self.source}")
                self.assertEqual(command, ["/opt/homebrew/bin/npx", "playwright", "test",
                    "--workers=1", "tests/e2e/" + filename])
                self.assertEqual(env["TURNOS_ASGI"], "1")
                self.assertEqual(env["TURNOS_FAKE_AI"], "1")
                self.assertEqual(env["TURNOS_ENV_FILE"], os.devnull)
                for key in ("NODE_OPTIONS", "TURNOS_E2E_ALLOW_SKIP", "OP_SERVICE_ACCOUNT_TOKEN", "HOME"):
                    self.assertNotIn(key, env)
                for selector in (filename, "../../caller.mjs", "--eval=caller", "tests/a.spec.mjs"):
                    with self.assertRaises(ValueError):
                        managed_exec.resolve(profile, selector, f"file:{self.source}")
        self.source.write_text(json.dumps({"profiles": {"local-ci": {
            "DATABASE_URL": "synthetic", "NODE_OPTIONS": "--require=caller.js",
        }}}), encoding="utf-8")
        for profile in ("e2e-account-asgi", "e2e-account-delayed-asgi", "e2e-home-listing-asgi", "e2e-system-asgi", "e2e-settings-asgi", "e2e-plan-asgi", "e2e-alerts-asgi", "e2e-reports-asgi", "e2e-appearance-asgi", "e2e-hsi-asgi", "e2e-doctor-native-asgi", "e2e-patients-native-asgi", "e2e-preparations-native-asgi", "e2e-staff-account-asgi", "e2e-admin-auth-asgi", "e2e-consultation-asgi", "e2e-catalog-native-asgi"):
            with self.assertRaises(managed_exec.managed_secrets.SecretSourceError):
                managed_exec.resolve(profile, None, f"file:{self.source}")

    def test_e2e_asgi_is_fixed_db_only_and_rejects_callers_command(self):
        with _env.patched_env({"TURNOS_ASGI": "0", "LEAKED_AMBIENT": "no", "PW_WORKERS": "18"}):
            command, environment = managed_exec.resolve("e2e-asgi", None, f"file:{self.source}")
        self.assertEqual(command, ["/opt/homebrew/bin/npm", "run", "e2e"])
        self.assertEqual(environment["PW_WORKERS"], "4")
        self.assertEqual(environment["TURNOS_ASGI"], "1")
        self.assertEqual(environment["TURNOS_FAKE_AI"], "1")
        self.assertEqual(environment["TURNOS_ENV_FILE"], os.devnull)
        self.assertNotIn("LEAKED_AMBIENT", environment)
        self.assertNotIn("OP_SERVICE_ACCOUNT_TOKEN", environment)
        with self.assertRaises(ValueError):
            managed_exec.resolve("e2e-asgi", "tests/test_managed_exec.py", f"file:{self.source}")
        self.source.write_text(json.dumps({"profiles": {
            "local-ci": {"OPENROUTER_API_KEY": "synthetic-not-admitted"},
        }}), encoding="utf-8")
        with self.assertRaises(managed_exec.managed_secrets.SecretSourceError):
            managed_exec.resolve("e2e-asgi", None, f"file:{self.source}")

    def test_local_ci_asgi_child_sees_only_fixed_asgi_mode_and_scoped_database_value(self):
        _command, environment = managed_exec.resolve(
            "local-ci-asgi", None, f"file:{self.source}")
        child = ROOT / "tests" / "managed_exec_asgi_child.py"
        result = subprocess.run(
            [sys.executable, str(child)], cwd=ROOT, env=environment,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_local_ci_asgi_rejects_selector_and_source_control_override(self):
        with self.assertRaises(ValueError):
            managed_exec.resolve(
                "local-ci-asgi", "tests/test_managed_exec.py", f"file:{self.source}")
        for forbidden_key in ("TURNOS_ASGI", "OPENROUTER_API_KEY"):
            with self.subTest(forbidden_key=forbidden_key):
                self.source.write_text(json.dumps({"profiles": {
                    "local-ci": {forbidden_key: "synthetic-not-admitted"},
                }}), encoding="utf-8")
                with self.assertRaises(managed_exec.managed_secrets.SecretSourceError):
                    managed_exec.resolve("local-ci-asgi", None, f"file:{self.source}")


if __name__ == "__main__":
    unittest.main()
