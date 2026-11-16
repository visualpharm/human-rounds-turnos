"""Executable password-library and transparent legacy-upgrade proofs."""

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
if TEST_DIR not in sys.path:
    sys.path.insert(0, TEST_DIR)

from app.auth import password_hashing  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_auth  # noqa: E402
import _dbtest  # noqa: E402


LEGACY_HASH = (
    "scrypt$00112233445566778899aabbccddeeff$"
    "91497bb8245985269c9937d7efa80dcb58306598b5c065159b39d033710a4c32"
)
LEGACY_PASSWORD = "legacy-password"

_db = _dbtest.PerModuleDb(
    "sana_turnos_test_password_hashing", seed=lambda: db_operators.operators_list(),
)


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


class PasswordLibraryUnitTest(unittest.TestCase):
    def test_new_hash_is_argon2id_and_verifies(self):
        stored = password_hashing.hash_password("correct horse")
        self.assertTrue(password_hashing.is_current_hash(stored))
        self.assertTrue(stored.startswith("$argon2id$"))
        self.assertEqual(
            password_hashing.verify_and_update(stored, "correct horse"),
            (True, None),
        )
        self.assertEqual(
            password_hashing.verify_and_update(stored, "wrong"),
            (False, None),
        )
        for invalid_password in (None, 123, ["password"]):
            with self.subTest(invalid_password=invalid_password):
                with self.assertRaises(TypeError):
                    password_hashing.hash_password(invalid_password)
                self.assertEqual(
                    password_hashing.verify_and_update(stored, invalid_password),
                    (False, None),
                )

    def test_exact_legacy_hash_verifies_and_returns_argon2_replacement(self):
        valid, replacement = password_hashing.verify_and_update(
            LEGACY_HASH, LEGACY_PASSWORD
        )
        self.assertTrue(valid)
        self.assertTrue(password_hashing.is_current_hash(replacement))
        self.assertEqual(
            password_hashing.verify_and_update(LEGACY_HASH, "wrong"),
            (False, None),
        )

    def test_malformed_unknown_and_oversized_hashes_fail_closed(self):
        malformed = (
            None,
            "",
            "unknown$value",
            "scrypt$not-hex$00",
            "scrypt$00$" + "00" * 32,
            "scrypt$" + "00" * 16 + "$" + "00" * 31,
            "scrypt$" + "00" * 10_000 + "$" + "00" * 32,
            "$argon2id$broken",
            "$argon2id$v=19$m=1,t=1,p=1$$",
            "$argon2id$v=19$m=1,t=1,p=1$bad$bad",
            "$argon2id$v=19$m=65536,t=3,p=4$bad$bad",
        )
        for stored in malformed:
            with self.subTest(stored=str(stored)[:40]):
                self.assertEqual(
                    password_hashing.verify_and_update(stored, "anything"),
                    (False, None),
                )
                self.assertFalse(password_hashing.is_current_hash(stored))


class PasswordDatabaseMigrationTest(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        import psycopg
        with psycopg.connect(_db.test_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE users, operators, admin_account_security CASCADE")

    def _set_hash(self, table, key_column, key, hash_column="pass_hash"):
        import psycopg
        from psycopg import sql
        with psycopg.connect(_db.test_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("UPDATE {} SET {}=%s WHERE {}=%s").format(
                        sql.Identifier(table), sql.Identifier(hash_column),
                        sql.Identifier(key_column),
                    ),
                    (LEGACY_HASH, key),
                )

    def _get_hash(self, table, key_column, key, hash_column="pass_hash"):
        import psycopg
        from psycopg import sql
        with psycopg.connect(_db.test_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("SELECT {} FROM {} WHERE {}=%s").format(
                        sql.Identifier(hash_column), sql.Identifier(table),
                        sql.Identifier(key_column),
                    ),
                    (key,),
                )
                return cur.fetchone()[0]

    def test_patient_legacy_login_upgrades_atomically(self):
        dni = "99000001"
        db_accounts.create_user(
            dni, "Patient", "Example", "1990-01-01", "X",
            "+54 9 2254 000001", "temporary-password",
        )
        self._set_hash("users", "dni", dni)
        self.assertIsNone(db_accounts.verify_password(dni, "wrong"))
        self.assertEqual(self._get_hash("users", "dni", dni), LEGACY_HASH)
        self.assertEqual(db_accounts.verify_password(dni, LEGACY_PASSWORD)["dni"], dni)
        self.assertTrue(password_hashing.is_current_hash(
            self._get_hash("users", "dni", dni)
        ))

    def test_every_password_writer_emits_current_argon2id(self):
        dni = "99000002"
        db_accounts.create_user(
            dni, "Writer", "Proof", "1990-01-01", "X",
            "+54 9 2254 000002", "initial-password",
        )
        self.assertTrue(password_hashing.is_current_hash(
            self._get_hash("users", "dni", dni)
        ))
        db_accounts.set_password(dni, "changed-password")
        self.assertTrue(password_hashing.is_current_hash(
            self._get_hash("users", "dni", dni)
        ))

        username = "hash-writer-proof"
        db_operators.operator_create(username, password="initial-" + "password", rol="admin")
        self.assertTrue(password_hashing.is_current_hash(
            self._get_hash("operators", "username", username)
        ))
        db_operators.operator_update(username, password="changed-" + "password")
        self.assertTrue(password_hashing.is_current_hash(
            self._get_hash("operators", "username", username)
        ))
        reset_token = db_auth.admin_reset_token_create(username)
        self.assertEqual(
            db_auth.admin_reset_token_consume(reset_token, "reset-password"),
            username,
        )
        self.assertTrue(password_hashing.is_current_hash(
            self._get_hash("operators", "username", username)
        ))

        owner = "owner-writer-proof"
        db_auth.admin_security_owner_update(owner, password="owner-password")
        self.assertTrue(password_hashing.is_current_hash(self._get_hash(
            "admin_account_security", "username", owner, "owner_pass_hash"
        )))

    def test_every_staff_role_logs_in_and_legacy_operator_upgrades(self):
        for role in ("admin", "medico", "admisionista", "reportes"):
            username = "hash-" + role
            db_operators.operator_create(username, password="current-" + "password", rol=role)
            self.assertEqual(
                db_operators.operator_verify_password(username, "current-password")["rol"],
                role,
            )
        username = "hash-legacy"
        db_operators.operator_create(username, password="temporary-" + "password", rol="admin")
        self._set_hash("operators", "username", username)
        self.assertIsNone(db_operators.operator_verify_password(username, "wrong"))
        self.assertEqual(self._get_hash("operators", "username", username), LEGACY_HASH)
        self.assertIsNotNone(db_operators.operator_verify_password(username, LEGACY_PASSWORD))
        self.assertTrue(password_hashing.is_current_hash(
            self._get_hash("operators", "username", username)
        ))

    def test_owner_override_upgrades_and_setup_bypass_rejects_legacy(self):
        username = "owner-hash-proof"
        db_auth.admin_security_owner_update(username, password="temporary-" + "password")
        self._set_hash(
            "admin_account_security", "username", username, "owner_pass_hash"
        )
        self.assertFalse(db_auth.admin_security_verify_owner_password(
            username, "wrong"
        ))
        self.assertEqual(
            self._get_hash(
                "admin_account_security", "username", username, "owner_pass_hash"
            ),
            LEGACY_HASH,
        )
        with patch("app.auth.admin_auth._admin_user", return_value=username), \
             patch("app.auth.admin_auth._admin_pass", return_value="env-fallback"):
            identity = admin_auth._authenticate(username, LEGACY_PASSWORD)
        self.assertEqual(identity["rol"], "admin")
        self.assertTrue(password_hashing.is_current_hash(
            self._get_hash(
                "admin_account_security", "username", username, "owner_pass_hash"
            )
        ))
        with self.assertRaises(ValueError):
            db_operators.operator_create("legacy-bypass", pass_hash=LEGACY_HASH)
        for unsafe_hash in (
            "$argon2id$v=19$m=1,t=1,p=1$$",
            "$argon2id$v=19$m=1,t=1,p=1$bad$bad",
            "$argon2id$v=19$m=65536,t=3,p=4$bad$bad",
        ):
            with self.subTest(unsafe_hash=unsafe_hash):
                with self.assertRaises(ValueError):
                    db_operators.operator_create("unsafe-bypass", pass_hash=unsafe_hash)

    def test_legacy_inventory_counts_only_aggregate_credential_stores(self):
        patient = "99000003"
        staff = "inventory-staff"
        owner = "inventory-owner"
        db_accounts.create_user(
            patient, "Inventory", "Patient", "1990-01-01", "X",
            "+54 9 2254 000003", "test-pass",
        )
        db_operators.operator_create(staff, password="test-pass", rol="admin")
        db_auth.admin_security_owner_update(owner, password="test-pass")
        self.assertEqual(
            db_accounts.legacy_password_hash_inventory(),
            {"patients": 0, "staff": 0, "owners": 0},
        )
        self._set_hash("users", "dni", patient)
        self._set_hash("operators", "username", staff)
        self._set_hash(
            "admin_account_security", "username", owner, "owner_pass_hash"
        )
        self.assertEqual(
            db_accounts.legacy_password_hash_inventory(),
            {"patients": 1, "staff": 1, "owners": 1},
        )


if __name__ == "__main__":
    unittest.main()
