"""test_setup_secret_generation.py — bounded real behavior test for TURNOS_ADMIN_SECRET.

Acceptance criteria:
- Call the actual ops.setup.process_apikeys production function
- Prove a 64-hex signing secret is generated only when both TURNOS_ADMIN_SECRET
  and TURNOS_ADMIN_PASS are absent/empty, is not overwritten when either exists,
  and two real calls generate different values
- No secrets in output, no copied production condition, no inspect/source regex,
  no skips, no broad assertions

Run: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_setup_secret_generation.py"
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_SETUP_SKIP_GEOIP="1")

from app.core import config
from app.core import data_packs
from app.core import db_operators
from app.core import db_auth
from app.core import db_catalog
from app.ops import setup


class FakeDB:
    """Minimal in-memory stand-in for db.py functions used by process_apikeys."""
    def __init__(self):
        self.operators = {}
        self.catalog = {}
        self.security = {}

    def operator_get(self, usuario):
        return self.operators.get(usuario)

    def operator_create(self, usuario, rol="admin", totp_secret=None,
                        email=None, pass_hash=None, **kwargs):
        if usuario in self.operators:
            raise RuntimeError("username already exists")
        self.operators[usuario] = {
            "usuario": usuario,
            "rol": rol,
            "totp_secret": totp_secret,
            "email": email,
            "pass_hash": pass_hash,
            "activo": True
        }
        return dict(self.operators[usuario])

    def catalog_upsert(self, tipo, id_, doc):
        self.catalog[(tipo, id_)] = dict(doc)
        return {"type": tipo, "id": id_, "doc": doc}

    def admin_security_set_deadline(self, usuario, deadline):
        self.security[usuario] = deadline
        return {"usuario": usuario, "totp_deadline": deadline}


class TestSecretGeneration(unittest.TestCase):
    """Test that ops.setup.process_apikeys correctly generates TURNOS_ADMIN_SECRET."""

    def setUp(self):
        config._reset_cache_para_tests()
        self._env_path = config.ENV_PATH
        config.ENV_PATH = ""  # Don't read real .env
        setup._reset_cache_para_tests()

        # Clear all secrets and config. patched_env puts the WHOLE environment back (the
        # keys process_apikeys itself writes, LANGUAGES and the like, included); the config
        # cache is reset once more after that.
        self.addCleanup(config._reset_cache_para_tests)
        self.enterContext(_env.patched_env(dict.fromkeys((
            "TURNOS_ADMIN_SECRET", "TURNOS_ADMIN_PASS", "TURNOS_CONFIG_LOCK",
            "TURNOS_PRIVATE_PACKS_DIR", "TURNOS_REQUIRED_PACKS", "PACK_CHAIN",
            "TURNOS_ENV_FILE"))))

        self.fake = FakeDB()
        self.overrides_written = {}

    def tearDown(self):
        config.ENV_PATH = self._env_path
        config._reset_cache_para_tests()
        setup._reset_cache_para_tests()

    def _call_process_apikeys(self, state):
        """Call process_apikeys with mocked dependencies."""
        with patch.object(db_operators, "operator_get", side_effect=self.fake.operator_get), \
             patch.object(db_operators, "operator_create", side_effect=self.fake.operator_create), \
             patch.object(db_catalog, "catalog_upsert", side_effect=self.fake.catalog_upsert), \
             patch.object(db_auth, "admin_security_set_deadline",
                          side_effect=self.fake.admin_security_set_deadline), \
             patch.object(data_packs, "invalidate"), \
             patch.object(config, "guardar", side_effect=self._capture_guardar):
            return setup.process_apikeys({}, state)

    def _capture_guardar(self, overrides):
        """Capture what config.guardar would write."""
        self.overrides_written.update(overrides)

    def test_generate_secret_when_both_absent(self):
        """When neither TURNOS_ADMIN_SECRET nor TURNOS_ADMIN_PASS exist, a 64-hex secret is generated."""
        state = {
            "admin_username": "testadmin",
            "admin_pass_hash": b"hashed_password",
            "admin_email": None,
            "admin_totp_required": False,
            "languages": ["es"],
            "lang_default": "es",
            "lang_switcher_mode": "nav",
            "pack_scaffold": False,
            "pack_chain": [],
        }

        result = self._call_process_apikeys(state)

        self.assertEqual(result, ("ok", "testadmin", False))
        self.assertIn("TURNOS_ADMIN_SECRET", self.overrides_written)
        secret = self.overrides_written["TURNOS_ADMIN_SECRET"]
        self.assertEqual(len(secret), 64)
        self.assertRegex(secret, r"^[0-9a-f]{64}$")

    def test_no_generation_when_secret_exists(self):
        """When TURNOS_ADMIN_SECRET exists, it is not overwritten."""
        existing_secret = "a" * 64
        _env.set_env(self, TURNOS_ADMIN_SECRET=existing_secret)

        state = {
            "admin_username": "testadmin",
            "admin_pass_hash": b"hashed_password",
            "admin_email": None,
            "admin_totp_required": False,
            "languages": ["es"],
            "lang_default": "es",
            "lang_switcher_mode": "nav",
            "pack_scaffold": False,
            "pack_chain": [],
        }

        result = self._call_process_apikeys(state)

        self.assertEqual(result, ("ok", "testadmin", False))
        # TURNOS_ADMIN_SECRET should NOT be in overrides when it already exists
        self.assertNotIn("TURNOS_ADMIN_SECRET", self.overrides_written)

    def test_no_generation_when_pass_exists(self):
        """When TURNOS_ADMIN_PASS exists, no secret is generated (pass is the signing key source)."""
        _env.set_env(self, TURNOS_ADMIN_PASS="some-password")

        state = {
            "admin_username": "testadmin",
            "admin_pass_hash": b"hashed_password",
            "admin_email": None,
            "admin_totp_required": False,
            "languages": ["es"],
            "lang_default": "es",
            "lang_switcher_mode": "nav",
            "pack_scaffold": False,
            "pack_chain": [],
        }

        result = self._call_process_apikeys(state)

        self.assertEqual(result, ("ok", "testadmin", False))
        self.assertNotIn("TURNOS_ADMIN_SECRET", self.overrides_written)

    def test_two_calls_generate_different_secrets(self):
        """Two separate calls with absent secrets should generate different values."""
        state1 = {
            "admin_username": "admin1",
            "admin_pass_hash": b"hash1",
            "admin_email": None,
            "admin_totp_required": False,
            "languages": ["es"],
            "lang_default": "es",
            "lang_switcher_mode": "nav",
            "pack_scaffold": False,
            "pack_chain": [],
        }

        result1 = self._call_process_apikeys(state1)
        self.assertEqual(result1, ("ok", "admin1", False))
        secret1 = self.overrides_written.get("TURNOS_ADMIN_SECRET")

        # Reset for second call
        self.overrides_written.clear()
        self.fake = FakeDB()  # New fake DB

        state2 = {
            "admin_username": "admin2",
            "admin_pass_hash": b"hash2",
            "admin_email": None,
            "admin_totp_required": False,
            "languages": ["es"],
            "lang_default": "es",
            "lang_switcher_mode": "nav",
            "pack_scaffold": False,
            "pack_chain": [],
        }

        result2 = self._call_process_apikeys(state2)
        self.assertEqual(result2, ("ok", "admin2", False))
        secret2 = self.overrides_written.get("TURNOS_ADMIN_SECRET")

        self.assertIsNotNone(secret1)
        self.assertIsNotNone(secret2)
        self.assertNotEqual(secret1, secret2)

    def test_empty_string_treated_as_absent(self):
        """Empty string for TURNOS_ADMIN_SECRET should be treated as absent and generate a new secret."""
        _env.set_env(self, TURNOS_ADMIN_SECRET="", TURNOS_ADMIN_PASS="")

        state = {
            "admin_username": "testadmin",
            "admin_pass_hash": b"hashed_password",
            "admin_email": None,
            "admin_totp_required": False,
            "languages": ["es"],
            "lang_default": "es",
            "lang_switcher_mode": "nav",
            "pack_scaffold": False,
            "pack_chain": [],
        }

        result = self._call_process_apikeys(state)

        self.assertEqual(result, ("ok", "testadmin", False))
        self.assertIn("TURNOS_ADMIN_SECRET", self.overrides_written)
        secret = self.overrides_written["TURNOS_ADMIN_SECRET"]
        self.assertEqual(len(secret), 64)
        self.assertRegex(secret, r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
