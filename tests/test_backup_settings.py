import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import _env
from app.core import backup_settings as b, backup_crypto as c, managed_secrets, config


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        _env.set_env(self, TURNOS_ENV_FILE=str(Path(self.tmp.name, "installation.env")))
        self.addCleanup(config._reset_cache_para_tests)

    def test_key_lives_in_managed_env_never_db_and_reveals_once(self):
        with patch.object(config, "guardar") as db_write:
            result = b.generate_key()
            key = c.decode_key(result["recovery_key"])
            self.assertEqual(b.keys()[c.key_id(key)], key)
            self.assertNotIn("recovery_key", b.key_status())
            self.assertEqual(b.key_status()["fingerprint"], c.key_id(key))
            with self.assertRaises(ValueError):
                b.generate_key()
            db_write.assert_not_called()

    def test_rotation_retains_old_key_and_config_lock_refuses_write(self):
        old = b.generate_key()
        new = b.generate_key(rotate=True)
        self.assertNotEqual(old["fingerprint"], new["fingerprint"])
        self.assertEqual(len(b.keys()), 2)
        with patch.object(config, "locked", return_value=True):
            with self.assertRaises(ValueError):
                b.generate_key(rotate=True)

    def test_all_settings_are_classified(self):
        for name in b.DEFAULTS:
            self.assertTrue(managed_secrets.is_runtime_key(name), name)
            self.assertEqual(managed_secrets.classify_key(name), "protected" if name in b.PROTECTED else "public", name)

    def test_validation_defaults_and_prefix(self):
        with patch.object(config, "get", side_effect=lambda k, d=None: b.DEFAULTS.get(k, d)):
            cfg = b.snapshot()
        self.assertEqual(cfg["TURNOS_BACKUP_RETAIN_DAILY"], "7")
        for prefix in ("../bad/", "/absolute/", "bad//", ""):
            with self.assertRaises(ValueError):
                b.validate({"TURNOS_BACKUP_S3_PREFIX": prefix})

    def test_canonical_env_redaction_is_fail_closed_for_all_key_assignments(self):
        text = "# KEEP\nTURNOS_BACKUP_RECOVERY_KEY=hidden\nexport TURNOS_BACKUP_OLD_KEYS_SECRET='hidden'\nOTHER=kept\n"
        self.assertEqual(b.sanitize_env(text), "# KEEP\nOTHER=kept\n")

    def test_config_database_layer_refuses_installation_keys(self):
        with patch.object(config, "_db_overrides") as read:
            for name in (b.KEY, b.OLD_KEYS):
                with self.assertRaises(ValueError):
                    config.guardar({name: "fixture"})
            read.assert_not_called()
