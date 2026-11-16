"""Synthetic-only tests for the managed local-secret boundary."""

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

from app.core import managed_secrets  # noqa: E402


class ManagedSecretsTest(unittest.TestCase):
    def test_external_history_flag_is_a_public_runtime_setting(self):
        from app.core import config
        key = "TURNOS_HSI_EXTERNAL_HISTORY"
        self.assertTrue(managed_secrets.is_runtime_key(key))
        self.assertEqual(managed_secrets.classify_key(key), "public")
        for value, enabled in [(None, False), ("0", False), ("true", False), ("1", True)]:
            with patch.object(config, "get", return_value=value) as get:
                self.assertEqual(config.external_history_enabled(), enabled)
                get.assert_called_once_with(key, default="0")

    def test_booking_settings_are_runtime_keys_but_not_db_profile_keys(self):
        from app.modules.turnos import booking_horizon
        keys = {"BOOKING_HORIZON_DAYS": "14", "BOOKING_OPENS_AT": "12:00"}
        for key in keys:
            self.assertTrue(managed_secrets.is_runtime_key(key))
        source = managed_secrets.FileSecretSource(str(self.fixture({"profiles": {"asgi": keys, "backend-focused": keys}})))
        self.assertEqual(source.values_for("asgi", managed_secrets.is_runtime_key), keys)
        with patch.object(booking_horizon.config, "get", side_effect=lambda key, default=None: keys.get(key, default)):
            self.assertEqual(booking_horizon.site_default(), 14)
            self.assertEqual(booking_horizon.opens_for(), "12:00")
        with self.assertRaises(managed_secrets.SecretSourceError):
            source.values_for("backend-focused", lambda key: key in {"DATABASE_URL", "TEST_DATABASE_URL"})

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory(prefix="turnos-secret-fixture-")
        self.addCleanup(self.dir.cleanup)

    def fixture(self, document, name="source.json", mode=0o600):
        path = Path(self.dir.name, name)
        path.write_text(json.dumps(document), encoding="utf-8")
        path.chmod(mode)
        return path

    @staticmethod
    def broker_document(profiles):
        return json.dumps({
            "schema": managed_secrets._PROFILES_SCHEMA,
            "profiles": profiles,
        })

    def test_file_source_is_profile_scoped_and_never_needs_shell_text(self):
        path = self.fixture({"profiles": {
            "backend-full": {"DATABASE_URL": "postgresql://fixture:fixture-only@127.0.0.1/fixture"},
            "app": {"TURNOS_ADMIN_PASS": "fixture-only-admin"},
        }})
        source = managed_secrets.source_from_spec(f"file:{path}")
        values = source.values_for("backend-full", lambda key: key == "DATABASE_URL")
        self.assertEqual(set(values), {"DATABASE_URL"})
        self.assertNotIn("TURNOS_ADMIN_PASS", values)

    def test_source_rejects_group_readable_or_out_of_profile_values(self):
        insecure = self.fixture({"profiles": {"backend-full": {}}}, mode=0o640)
        with self.assertRaises(managed_secrets.SecretSourceError):
            managed_secrets.FileSecretSource(str(insecure)).values_for("backend-full", lambda _key: True)

        outside = self.fixture({"profiles": {"backend-full": {"TURNOS_ADMIN_PASS": "fixture"}}}, "outside.json")
        with self.assertRaises(managed_secrets.SecretSourceError):
            managed_secrets.FileSecretSource(str(outside)).values_for("backend-full", lambda key: key == "DATABASE_URL")

    def test_source_rejects_symlinks_foreign_owners_and_project_worktrees(self):
        target = self.fixture({"profiles": {"backend-full": {}}}, "target.json")
        alias = Path(self.dir.name, "alias.json")
        alias.symlink_to(target)
        with self.assertRaises(managed_secrets.SecretSourceError):
            managed_secrets.FileSecretSource(str(alias)).values_for("backend-full", lambda _key: True)

        with patch("app.core.managed_secrets.os.getuid", return_value=os.getuid() + 1):
            with self.assertRaises(managed_secrets.SecretSourceError):
                managed_secrets.FileSecretSource(str(target)).values_for("backend-full", lambda _key: True)

        with patch.object(managed_secrets, "WORKTREE_CONTAINER", Path(self.dir.name).resolve().parent):
            with self.assertRaises(managed_secrets.SecretSourceError):
                managed_secrets.FileSecretSource(str(target)).values_for("backend-full", lambda _key: True)

    def test_unknown_provider_is_not_a_command_escape_hatch(self):
        with self.assertRaises(managed_secrets.SecretSourceError):
            managed_secrets.source_from_spec("command:echo anything")
        with self.assertRaises(managed_secrets.SecretSourceError):
            managed_secrets.source_from_spec("onepassword:another-item")
        with self.assertRaises(managed_secrets.SecretSourceError):
            managed_secrets.source_from_environment({
                "TURNOS_SECRET_SOURCE": "file:/tmp/synthetic-source.json",
            })

    def test_broker_source_reads_only_the_injected_document_and_profile_scope(self):
        marker = "synthetic-provider-sentinel"
        parent = {
            managed_secrets.BROKER_ENV_NAME: self.broker_document({
                "backend-full": {"DATABASE_URL": marker},
                "app": {"TURNOS_ADMIN_PASS": marker},
            }),
            "OP_SERVICE_ACCOUNT_TOKEN": "synthetic-provider-token",
            "LEAKED_AMBIENT": marker,
        }
        source = managed_secrets.source_from_spec("onepassword", parent)
        self.assertTrue(source.available())
        with patch("subprocess.run") as run, patch("subprocess.Popen") as popen, \
             patch("sys.stdout", new_callable=io.StringIO) as stdout, \
             patch("sys.stderr", new_callable=io.StringIO) as stderr:
            values = source.values_for("backend-full", lambda key: key == "DATABASE_URL")
            with self.assertRaises(managed_secrets.SecretSourceError):
                source.values_for("app", lambda key: key == "DATABASE_URL")
        self.assertEqual(values, {"DATABASE_URL": marker})
        self.assertEqual(stdout.getvalue() + stderr.getvalue(), "")
        run.assert_not_called()
        popen.assert_not_called()
        self.assertNotIn("subprocess", vars(managed_secrets))

    def test_broker_source_errors_are_value_free(self):
        marker = "synthetic-never-echoed"
        self.assertFalse(managed_secrets.source_from_spec("onepassword", {}).available())
        for raw in (None, "", marker, json.dumps({"schema": marker, "profiles": {}}),
                    json.dumps({"schema": managed_secrets._PROFILES_SCHEMA, "profiles": marker})):
            with self.subTest(raw=raw is not None and raw[:12]):
                given_env = {} if raw is None else {managed_secrets.BROKER_ENV_NAME: raw}
                source = managed_secrets.source_from_environment(given_env)
                with self.assertRaises(managed_secrets.SecretSourceError) as error:
                    source.values_for("backend-full", lambda _key: True)
                self.assertNotIn(marker, str(error.exception))

    def test_dry_run_reports_counts_and_mode_only(self):
        env_file = Path(self.dir.name, "synthetic.env")
        marker = "synthetic-never-output"
        env_file.write_text(
            "DATABASE_URL=postgresql://fixture:" + marker + "@127.0.0.1/db\n"
            "PUBLIC_SETTING=sample\n",
            encoding="utf-8",
        )
        env_file.chmod(0o600)
        result = subprocess.run(
            [sys.executable, "scripts/turnos_secret_migration.py", "--dry-run", "--env-file", str(env_file)],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["file_mode"], "0600")
        self.assertEqual(report["classification"], {"malformed": 0, "protected": 1, "public": 1, "total": 2})
        self.assertEqual(report["writes"], 0)
        self.assertNotIn(marker, result.stdout + result.stderr)
        self.assertNotIn(str(env_file), result.stdout + result.stderr)

    def test_self_check_reports_source_counts_without_values(self):
        marker = "synthetic-never-output"
        source = self.fixture({"profiles": {
            "backend-full": {"DATABASE_URL": marker},
            "app": {"TURNOS_ADMIN_PASS": marker, "HOST": "127.0.0.1"},
        }})
        result = subprocess.run(
            [sys.executable, "scripts/turnos_secret_migration.py", "--self-check", "--source-file", str(source)],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["file_mode"], "0600")
        self.assertEqual(report["classification"], {"malformed": 0, "profiles": 2, "protected": 2, "public": 1, "total": 3})
        self.assertNotIn(marker, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
