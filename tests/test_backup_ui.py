import os
import tempfile
import unittest
import asyncio
import json
from pathlib import Path
from unittest.mock import patch
from app.core import backup_setup as staged, backup_settings as settings, config


class BackupLocaleBoundaryTest(unittest.TestCase):
    def test_operator_backup_copy_stays_out_of_patient_bootstrap(self):
        from app.core import i18n
        i18n.invalidate()
        self.addCleanup(i18n.invalidate)
        self.assertNotIn("backup", i18n.client_namespaces())
        self.assertFalse(any(key.startswith("backup.") for key in i18n._client_catalog("en")))

    def test_settings_and_system_explicitly_inline_backup_copy(self):
        from app.core import i18n
        from app.admin_panel import admin_settings_screen, admin_system_screen
        with patch.object(i18n, "active", return_value="en"):
            for render in (admin_settings_screen.settings_screen, admin_system_screen.system_screen):
                with self.subTest(screen=render.__name__):
                    response = asyncio.run(render(identity={"rol": "admin"}))
                    html = response.body.decode()
                    payload = json.loads(html.split("window.SANA_I18N=", 1)[1].split(";</script>", 1)[0])
                    self.assertEqual(payload["catalog"]["backup.copy"], "Copy")
from app.admin_panel import settings_admin, system_status
from app.ops import setup


class BackupUITest(unittest.TestCase):
    def test_wizard_state_contains_only_token_no_keys_or_db_writes(self):
        with patch.object(config, "guardar") as save, patch.object(config, "save_env_keys") as env:
            token, result = staged.update(None, {"action": "generate"})
            self.assertIn("recovery_key", result)
            self.assertNotIn("recovery_key", staged.public(token))
            self.assertNotIn("recovery_key", token)
            self.assertEqual(staged.public(token)["fingerprint"], result["fingerprint"])
            self.assertNotIn(result["recovery_key"], setup.page_backups({"backup_stage": token}))
            save.assert_not_called()
            env.assert_not_called()

    def test_skip_explicit_and_expired_stage_cannot_silently_enable(self):
        token, result = staged.update(None, {"action": "skip"})
        self.assertTrue(result["continue"])
        self.assertEqual(staged.commit(token)[0]["TURNOS_BACKUP_ENABLED"], "0")
        with self.assertRaises(ValueError):
            staged.commit("missing")

    def test_wizard_connection_probe_stages_credentials_without_persisting(self):
        token, _ = staged.update(None, {"action": "generate"})
        values = {"action": "probe", "TURNOS_BACKUP_S3_ENDPOINT": "https://fixture.example",
                  "TURNOS_BACKUP_S3_BUCKET": "fixture", "TURNOS_BACKUP_S3_KEY_ID": "synthetic",
                  "TURNOS_BACKUP_S3_SECRET": "synthetic"}
        with patch.object(settings, "client") as client, patch.object(config, "guardar") as save:
            staged.update(token, values)
            client.return_value.probe.assert_called_once()
            save.assert_not_called()
        self.assertNotIn("synthetic", str(staged.public(token)))

    def test_settings_masks_secrets_and_key_fingerprint_only(self):
        values = {"TURNOS_BACKUP_S3_SECRET": "synthetic-secret", "TURNOS_BACKUP_S3_KEY_ID": "synthetic-id"}
        with patch.object(config, "get", side_effect=lambda k, default=None: values.get(k, default)), patch.object(settings, "key_status", return_value={"configured": True, "fingerprint": "12345678"}):
            group = next(g for g in settings_admin._api_keys_payload() if g["id"] == "backup")
        self.assertNotIn("synthetic-secret", str(group))
        self.assertNotIn("synthetic-id", str(group))
        self.assertEqual(group["key_status"]["fingerprint"], "12345678")

    def test_locked_backup_settings_are_refused_before_mutation(self):
        with patch.object(config, "locked", return_value=True), patch.object(config, "guardar") as save:
            with self.assertRaises(settings_admin.SettingsError):
                settings_admin.resolve_save("api_keys", {"TURNOS_BACKUP_S3_BUCKET": "new-bucket"}, [])
            save.assert_not_called()

    def test_one_system_row_exposes_separate_evidence_states(self):
        with patch.object(settings, "configured", return_value=False), patch("app.core.backup_journal.Store.list", return_value=[]):
            cluster = system_status._cluster_backup()
        self.assertEqual(len(cluster["items"]), 1)
        self.assertEqual(cluster["items"][0]["backup"]["state"], "unconfigured")
