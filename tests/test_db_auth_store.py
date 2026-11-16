"""test_db_auth_store.py — the account-security statements of `core/db.py` against a real Postgres.

Stage 5 of the SQLAlchemy Core move converts the accounts + auth group of `core/db.py`. This file
pins the part of it that guards the admin panel: the emergency owner's security row
(`admin_security_*`: 2FA deadline, password/TOTP/email overrides, the locked verify-and-upgrade),
the WebAuthn store (`passkey_*`, `passkeys_*`: public shape, clone-detection counter, the digest-only
one-time challenges), the panel preferences (`admin_preferences`, `admin_preference_save`) and the
one-time password-link tokens (`admin_reset_token_*`). Security paths come first: wrong password,
expired / used / unknown token, inactive account, replayed or foreign challenge, secrets stored
hashed and never in plaintext. It was written and run green on the hand-written SQL first, and
passes unedited on Core.
"""
import os
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.auth import password_hashing  # noqa: E402
from app.core import db  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_auth  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_db_auth_store")

UTC = timezone.utc

LEGACY_HASH = (
    "scrypt$00112233445566778899aabbccddeeff$"
    "91497bb8245985269c9937d7efa80dcb58306598b5c065159b39d033710a4c32"
)
LEGACY_PASSWORD = "legacy-password"

OWNER = "owner-test"


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _sql(sql, params=None):
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


def _now():
    return _sql("SELECT now() AS n")[0]["n"]


def _security(usuario=OWNER):
    rows = _sql("SELECT * FROM admin_account_security WHERE username=%s", (usuario,))
    return rows[0] if rows else None


class DbCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE admin_account_security, passkeys, passkey_challenges, "
                        "admin_preferences, admin_reset_tokens RESTART IDENTITY")
            cur.execute("TRUNCATE operators RESTART IDENTITY CASCADE")


# ---- admin_account_security -------------------------------------------------------

SECURITY_KEYS = ["usuario", "owner_pass_hash", "owner_totp_secret", "owner_totp_set",
                 "totp_deadline", "updated", "owner_email", "owner_ultimo_acceso"]


class TestSecurityGet(DbCase):
    def test_absent_account_is_none(self):
        self.assertIsNone(db_auth.admin_security_get("nadie"))

    def test_row_has_the_wire_keys_in_column_order(self):
        _sql("INSERT INTO admin_account_security (username, owner_email) VALUES (%s, %s)",
             (OWNER, "dueno@example.test"))
        row = db_auth.admin_security_get(OWNER)
        self.assertEqual(list(row), SECURITY_KEYS)
        self.assertEqual(row["usuario"], OWNER)
        self.assertEqual(row["owner_email"], "dueno@example.test")
        self.assertIsNone(row["owner_pass_hash"])
        self.assertIsNone(row["owner_totp_secret"])
        self.assertIs(row["owner_totp_set"], False)
        self.assertIsNone(row["totp_deadline"])
        self.assertIsNone(row["owner_ultimo_acceso"])
        self.assertIsInstance(row["updated"], datetime)

    def test_lookup_is_exact_and_per_user(self):
        _sql("INSERT INTO admin_account_security (username) VALUES ('Owner'), ('other')")
        self.assertIsNone(db_auth.admin_security_get("owner"))
        self.assertEqual(db_auth.admin_security_get("Owner")["usuario"], "Owner")
        self.assertEqual(db_auth.admin_security_get("other")["usuario"], "other")


class TestSecurityEnsureDeadline(DbCase):
    def test_without_2fa_creates_a_deadline_dias_ahead(self):
        before = _now()
        row = db_auth.admin_security_ensure_deadline(OWNER, False)
        after = _now()
        self.assertEqual(list(row), SECURITY_KEYS)
        self.assertEqual(row["usuario"], OWNER)
        self.assertGreaterEqual(row["totp_deadline"], before + timedelta(days=7))
        self.assertLessEqual(row["totp_deadline"], after + timedelta(days=7))
        self.assertEqual(_security()["totp_deadline"], row["totp_deadline"])

    def test_custom_dias(self):
        before = _now()
        row = db_auth.admin_security_ensure_deadline(OWNER, False, dias=3)
        self.assertGreaterEqual(row["totp_deadline"], before + timedelta(days=3))
        self.assertLess(row["totp_deadline"], before + timedelta(days=4))

    def test_a_second_call_keeps_the_first_deadline_and_ignores_dias(self):
        first = db_auth.admin_security_ensure_deadline(OWNER, False, dias=2)
        _sql("UPDATE admin_account_security SET updated=%s WHERE username=%s",
             (_now() - timedelta(days=1), OWNER))
        second = db_auth.admin_security_ensure_deadline(OWNER, False, dias=30)
        self.assertEqual(second["totp_deadline"], first["totp_deadline"])
        self.assertGreater(second["updated"], _now() - timedelta(minutes=1))
        self.assertEqual(_sql("SELECT count(*) AS n FROM admin_account_security")[0]["n"], 1)

    def test_an_already_expired_deadline_is_not_renewed(self):
        past = _now() - timedelta(days=2)
        _sql("INSERT INTO admin_account_security (username, totp_deadline) VALUES (%s, %s)",
             (OWNER, past))
        row = db_auth.admin_security_ensure_deadline(OWNER, False)
        self.assertEqual(row["totp_deadline"], past)

    def test_existing_row_without_deadline_gets_one(self):
        _sql("INSERT INTO admin_account_security (username, owner_email) VALUES (%s, 'a@b.test')",
             (OWNER,))
        row = db_auth.admin_security_ensure_deadline(OWNER, False, dias=5)
        self.assertGreater(row["totp_deadline"], _now() + timedelta(days=4))
        self.assertEqual(row["owner_email"], "a@b.test")

    def test_with_2fa_creates_the_row_without_deadline(self):
        row = db_auth.admin_security_ensure_deadline(OWNER, True)
        self.assertEqual(row["usuario"], OWNER)
        self.assertIsNone(row["totp_deadline"])
        self.assertIsNotNone(_security())

    def test_with_2fa_clears_a_pending_deadline_and_keeps_the_rest(self):
        db_auth.admin_security_owner_update(OWNER, email="a@b.test")
        db_auth.admin_security_set_deadline(OWNER, _now() + timedelta(days=3))
        _sql("UPDATE admin_account_security SET updated=%s WHERE username=%s",
             (_now() - timedelta(days=1), OWNER))
        row = db_auth.admin_security_ensure_deadline(OWNER, True, dias=99)
        self.assertIsNone(row["totp_deadline"])
        self.assertEqual(row["owner_email"], "a@b.test")
        self.assertGreater(row["updated"], _now() - timedelta(minutes=1))
        self.assertIsNone(_security()["totp_deadline"])

    def test_another_account_is_untouched(self):
        db_auth.admin_security_ensure_deadline("otro", False)
        db_auth.admin_security_ensure_deadline(OWNER, True)
        self.assertIsNotNone(_security("otro")["totp_deadline"])


class TestSecurityOwnerUpdate(DbCase):
    def test_creates_the_row_and_returns_it(self):
        row = db_auth.admin_security_owner_update(OWNER, email="dueno@example.test")
        self.assertEqual(list(row), SECURITY_KEYS)
        self.assertEqual(row["usuario"], OWNER)
        self.assertEqual(row["owner_email"], "dueno@example.test")
        self.assertIsNone(row["owner_pass_hash"])
        self.assertIs(row["owner_totp_set"], False)
        self.assertEqual(_sql("SELECT count(*) AS n FROM admin_account_security")[0]["n"], 1)

    def test_no_arguments_only_touches_updated_and_still_creates_the_row(self):
        row = db_auth.admin_security_owner_update(OWNER)
        self.assertEqual(row["usuario"], OWNER)
        self.assertIsNone(row["owner_email"])
        self.assertIsNone(row["owner_pass_hash"])
        self.assertIsNone(row["owner_totp_secret"])
        self.assertIs(row["owner_totp_set"], False)

    def test_password_is_stored_as_a_current_argon2_hash_never_plaintext(self):
        row = db_auth.admin_security_owner_update(OWNER, password="s3cret-Pass!")
        stored = _security()["owner_pass_hash"]
        self.assertEqual(row["owner_pass_hash"], stored)
        self.assertNotIn("s3cret-Pass!", stored)
        self.assertTrue(password_hashing.is_current_hash(stored))
        self.assertEqual(password_hashing.verify_and_update(stored, "s3cret-Pass!"), (True, None))
        self.assertFalse(password_hashing.verify_and_update(stored, "other")[0])

    def test_empty_or_none_password_keeps_the_stored_hash(self):
        db_auth.admin_security_owner_update(OWNER, password="first-pass-1")
        stored = _security()["owner_pass_hash"]
        db_auth.admin_security_owner_update(OWNER, password="")
        db_auth.admin_security_owner_update(OWNER, password=None)
        self.assertEqual(_security()["owner_pass_hash"], stored)

    def test_a_new_password_replaces_the_old_hash(self):
        db_auth.admin_security_owner_update(OWNER, password="first-pass-1")
        old = _security()["owner_pass_hash"]
        db_auth.admin_security_owner_update(OWNER, password="second-pass-2")
        new = _security()["owner_pass_hash"]
        self.assertNotEqual(old, new)
        self.assertTrue(password_hashing.verify_and_update(new, "second-pass-2")[0])

    def test_totp_secret_sets_the_flag_with_it(self):
        row = db_auth.admin_security_owner_update(OWNER, totp_secret="JBSWY3DPEHPK")
        self.assertEqual(row["owner_totp_secret"], "JBSWY3DPEHPK")
        self.assertIs(row["owner_totp_set"], True)

    def test_totp_secret_none_means_cleared_but_still_marked_set(self):
        db_auth.admin_security_owner_update(OWNER, totp_secret="JBSWY3DPEHPK")
        row = db_auth.admin_security_owner_update(OWNER, totp_secret=None)
        self.assertIsNone(row["owner_totp_secret"])
        self.assertIs(row["owner_totp_set"], True)

    def test_not_passing_totp_or_email_leaves_them(self):
        db_auth.admin_security_owner_update(OWNER, totp_secret="AAAA", email="a@b.test")
        row = db_auth.admin_security_owner_update(OWNER, password="pass-word-1")
        self.assertEqual(row["owner_totp_secret"], "AAAA")
        self.assertIs(row["owner_totp_set"], True)
        self.assertEqual(row["owner_email"], "a@b.test")

    def test_email_can_be_changed_and_cleared_with_none(self):
        db_auth.admin_security_owner_update(OWNER, email="a@b.test")
        self.assertEqual(db_auth.admin_security_owner_update(OWNER, email="c@d.test")["owner_email"],
                         "c@d.test")
        self.assertIsNone(db_auth.admin_security_owner_update(OWNER, email=None)["owner_email"])

    def test_the_no_change_sentinel_is_exported(self):
        self.assertIsNotNone(db_auth.ADMIN_NO_CHANGE)
        row = db_auth.admin_security_owner_update(OWNER, totp_secret=db_auth.ADMIN_NO_CHANGE,
                                             email=db_auth.ADMIN_NO_CHANGE)
        self.assertIs(row["owner_totp_set"], False)
        self.assertIsNone(row["owner_email"])

    def test_preserves_a_pending_deadline_and_last_access(self):
        deadline = _now() + timedelta(days=2)
        db_auth.admin_security_set_deadline(OWNER, deadline)
        db_auth.admin_security_owner_access(OWNER)
        row = db_auth.admin_security_owner_update(OWNER, email="a@b.test")
        self.assertEqual(row["totp_deadline"], deadline)
        self.assertIsNotNone(row["owner_ultimo_acceso"])

    def test_bumps_updated(self):
        db_auth.admin_security_owner_update(OWNER)
        _sql("UPDATE admin_account_security SET updated=%s", (_now() - timedelta(days=1),))
        row = db_auth.admin_security_owner_update(OWNER, email="a@b.test")
        self.assertGreater(row["updated"], _now() - timedelta(minutes=1))

    def test_only_the_named_account_changes(self):
        db_auth.admin_security_owner_update("otro", email="otro@example.test")
        db_auth.admin_security_owner_update(OWNER, email="dueno@example.test")
        self.assertEqual(_security("otro")["owner_email"], "otro@example.test")


class TestSecurityVerifyOwnerPassword(DbCase):
    def test_no_row_is_false_and_creates_nothing(self):
        self.assertIs(db_auth.admin_security_verify_owner_password(OWNER, "anything"), False)
        self.assertIsNone(_security())

    def test_row_without_a_password_override_is_false(self):
        db_auth.admin_security_owner_update(OWNER, email="a@b.test")
        self.assertIs(db_auth.admin_security_verify_owner_password(OWNER, ""), False)
        self.assertIs(db_auth.admin_security_verify_owner_password(OWNER, "x"), False)

    def test_right_password_true_and_the_current_hash_is_left_alone(self):
        db_auth.admin_security_owner_update(OWNER, password="right-pass-1")
        row = _security()
        _sql("UPDATE admin_account_security SET updated=%s", (_now() - timedelta(days=1),))
        before = _security()
        self.assertIs(db_auth.admin_security_verify_owner_password(OWNER, "right-pass-1"), True)
        after = _security()
        self.assertEqual(after["owner_pass_hash"], row["owner_pass_hash"])
        self.assertEqual(after["updated"], before["updated"])

    def test_wrong_password_false_and_nothing_written(self):
        db_auth.admin_security_owner_update(OWNER, password="right-pass-1")
        before = _security()
        for bad in ("wrong-pass-1", "", "RIGHT-PASS-1", "right-pass-1 "):
            self.assertIs(db_auth.admin_security_verify_owner_password(OWNER, bad), False)
        self.assertEqual(_security(), before)

    def test_another_accounts_hash_does_not_verify(self):
        db_auth.admin_security_owner_update("otro", password="otro-pass-1")
        db_auth.admin_security_owner_update(OWNER, password="right-pass-1")
        self.assertIs(db_auth.admin_security_verify_owner_password(OWNER, "otro-pass-1"), False)
        self.assertIs(db_auth.admin_security_verify_owner_password("otro", "otro-pass-1"), True)

    def test_a_non_string_password_is_false(self):
        db_auth.admin_security_owner_update(OWNER, password="right-pass-1")
        self.assertIs(db_auth.admin_security_verify_owner_password(OWNER, None), False)
        self.assertIs(db_auth.admin_security_verify_owner_password(OWNER, 12345), False)

    def test_legacy_scrypt_hash_verifies_and_is_upgraded_to_argon2(self):
        _sql("INSERT INTO admin_account_security (username, owner_pass_hash, updated) "
             "VALUES (%s, %s, %s)", (OWNER, LEGACY_HASH, _now() - timedelta(days=1)))
        self.assertIs(db_auth.admin_security_verify_owner_password(OWNER, LEGACY_PASSWORD), True)
        row = _security()
        self.assertNotEqual(row["owner_pass_hash"], LEGACY_HASH)
        self.assertTrue(password_hashing.is_current_hash(row["owner_pass_hash"]))
        self.assertTrue(password_hashing.verify_and_update(row["owner_pass_hash"],
                                                          LEGACY_PASSWORD)[0])
        self.assertGreater(row["updated"], _now() - timedelta(minutes=1))
        # and the upgraded hash keeps verifying, without a second rewrite
        self.assertIs(db_auth.admin_security_verify_owner_password(OWNER, LEGACY_PASSWORD), True)
        self.assertEqual(_security()["owner_pass_hash"], row["owner_pass_hash"])

    def test_legacy_hash_with_a_wrong_password_is_not_upgraded(self):
        _sql("INSERT INTO admin_account_security (username, owner_pass_hash) VALUES (%s, %s)",
             (OWNER, LEGACY_HASH))
        self.assertIs(db_auth.admin_security_verify_owner_password(OWNER, "nope"), False)
        self.assertEqual(_security()["owner_pass_hash"], LEGACY_HASH)

    def test_a_garbage_stored_hash_is_false(self):
        _sql("INSERT INTO admin_account_security (username, owner_pass_hash) VALUES (%s, 'plain')",
             (OWNER,))
        self.assertIs(db_auth.admin_security_verify_owner_password(OWNER, "plain"), False)

    def test_the_row_is_locked_while_verifying(self):
        """FOR UPDATE: a verify waits for a transaction that holds the row."""
        db_auth.admin_security_owner_update(OWNER, password="right-pass-1")
        outcome = []
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM admin_account_security WHERE username=%s FOR UPDATE",
                        (OWNER,))
            worker = threading.Thread(
                target=lambda: outcome.append(
                    db_auth.admin_security_verify_owner_password(OWNER, "right-pass-1")))
            worker.start()
            worker.join(0.7)
            self.assertTrue(worker.is_alive(), "verify did not wait for the row lock")
            self.assertEqual(outcome, [])
        worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome, [True])


class TestSecurityOwnerAccess(DbCase):
    def test_creates_the_row_with_the_access_time(self):
        before = _now()
        self.assertIsNone(db_auth.admin_security_owner_access(OWNER))
        row = _security()
        self.assertGreaterEqual(row["owner_last_access"], before)
        self.assertLessEqual(row["owner_last_access"], _now())
        self.assertIsNone(row["totp_deadline"])
        self.assertIs(row["owner_totp_set"], False)

    def test_updates_an_existing_row_and_keeps_its_other_fields(self):
        db_auth.admin_security_owner_update(OWNER, password="right-pass-1", email="a@b.test",
                                       totp_secret="AAAA")
        deadline = _now() + timedelta(days=4)
        db_auth.admin_security_set_deadline(OWNER, deadline)
        old = _now() - timedelta(days=3)
        _sql("UPDATE admin_account_security SET owner_last_access=%s, updated=%s", (old, old))
        before = _security()
        db_auth.admin_security_owner_access(OWNER)
        row = _security()
        self.assertGreater(row["owner_last_access"], old + timedelta(days=2))
        self.assertGreater(row["updated"], old + timedelta(days=2))
        for key in ("owner_pass_hash", "owner_email", "owner_totp_secret", "owner_totp_set",
                    "totp_deadline"):
            self.assertEqual(row[key], before[key], key)
        self.assertEqual(row["totp_deadline"], deadline)

    def test_repeated_access_keeps_a_single_row(self):
        db_auth.admin_security_owner_access(OWNER)
        db_auth.admin_security_owner_access(OWNER)
        self.assertEqual(_sql("SELECT count(*) AS n FROM admin_account_security")[0]["n"], 1)


class TestSecuritySetDeadline(DbCase):
    def test_creates_the_row_and_returns_it(self):
        deadline = datetime(2031, 5, 4, 12, 0, tzinfo=UTC)
        row = db_auth.admin_security_set_deadline(OWNER, deadline)
        self.assertEqual(list(row), SECURITY_KEYS)
        self.assertEqual(row["usuario"], OWNER)
        self.assertEqual(row["totp_deadline"], deadline)
        self.assertEqual(_security()["totp_deadline"], deadline)

    def test_overwrites_the_deadline_and_keeps_the_rest(self):
        db_auth.admin_security_owner_update(OWNER, email="a@b.test")
        db_auth.admin_security_set_deadline(OWNER, datetime(2031, 1, 1, tzinfo=UTC))
        _sql("UPDATE admin_account_security SET updated=%s", (_now() - timedelta(days=1),))
        row = db_auth.admin_security_set_deadline(OWNER, datetime(2032, 2, 2, tzinfo=UTC))
        self.assertEqual(row["totp_deadline"], datetime(2032, 2, 2, tzinfo=UTC))
        self.assertEqual(row["owner_email"], "a@b.test")
        self.assertGreater(row["updated"], _now() - timedelta(minutes=1))

    def test_none_clears_the_deadline(self):
        db_auth.admin_security_set_deadline(OWNER, datetime(2031, 1, 1, tzinfo=UTC))
        row = db_auth.admin_security_set_deadline(OWNER, None)
        self.assertIsNone(row["totp_deadline"])
        self.assertIsNone(_security()["totp_deadline"])

    def test_accepts_an_iso_string(self):
        row = db_auth.admin_security_set_deadline(OWNER, "2031-05-04T12:00:00+00:00")
        self.assertEqual(row["totp_deadline"], datetime(2031, 5, 4, 12, 0, tzinfo=UTC))


# ---- passkeys ---------------------------------------------------------------------------

PUBLIC_KEY = {"kty": 2, "alg": -7, "x": "ñandú", "nested": {"a": [1, 2, 3]}}


class TestPasskeyPublic(unittest.TestCase):
    def test_none_stays_none(self):
        self.assertIsNone(db_auth._passkey_public(None))

    def test_drops_the_key_and_counter_and_isoformats_datetimes(self):
        when = datetime(2030, 1, 2, 3, 4, 5, tzinfo=UTC)
        row = {"id": 1, "usuario": "u", "credential_id": "c", "public_key": {"k": 1},
               "sign_count": 9, "nombre": "n", "creado": when, "ultimo_uso": when}
        self.assertEqual(db_auth._passkey_public(row),
                         {"id": 1, "usuario": "u", "credential_id": "c", "nombre": "n",
                          "creado": when.isoformat(), "ultimo_uso": when.isoformat()})

    def test_leaves_non_datetimes_and_missing_keys_alone_and_does_not_mutate(self):
        row = {"creado": "ya-texto", "ultimo_uso": None, "public_key": 1}
        out = db_auth._passkey_public(row)
        self.assertEqual(out, {"creado": "ya-texto", "ultimo_uso": None})
        self.assertIn("public_key", row)
        self.assertEqual(db_auth._passkey_public({"id": 3}), {"id": 3})


class TestPasskeyCreate(DbCase):
    def test_returns_the_public_shape_and_stores_the_full_row(self):
        row = db_auth.passkey_create("ana", "cred-1", PUBLIC_KEY, sign_count=4, nombre="Teléfono")
        self.assertEqual(list(row), ["id", "usuario", "credential_id", "nombre", "creado",
                                     "ultimo_uso"])
        self.assertEqual(row["usuario"], "ana")
        self.assertEqual(row["credential_id"], "cred-1")
        self.assertEqual(row["nombre"], "Teléfono")
        self.assertIsInstance(row["id"], int)
        self.assertIsInstance(row["creado"], str)
        datetime.fromisoformat(row["creado"])
        self.assertIsNone(row["ultimo_uso"])
        stored = _sql("SELECT * FROM passkeys")[0]
        self.assertEqual(stored["public_key"], PUBLIC_KEY)
        self.assertEqual(stored["sign_count"], 4)
        self.assertEqual(stored["id"], row["id"])
        self.assertIsNone(stored["last_used"])

    def test_defaults_counter_zero_and_empty_name_becomes_null(self):
        db_auth.passkey_create("ana", "c1", PUBLIC_KEY)
        db_auth.passkey_create("ana", "c2", PUBLIC_KEY, sign_count=None, nombre="")
        db_auth.passkey_create("ana", "c3", PUBLIC_KEY, sign_count="7")
        rows = {r["credential_id"]: r for r in _sql("SELECT * FROM passkeys")}
        self.assertEqual([rows[c]["sign_count"] for c in ("c1", "c2", "c3")], [0, 0, 7])
        self.assertEqual([rows[c]["name"] for c in ("c1", "c2", "c3")], [None, None, None])
        self.assertIsNone(db_auth.passkey_create("ana", "c4", PUBLIC_KEY)["nombre"])

    def test_duplicate_credential_id_raises_and_keeps_the_first(self):
        from psycopg.errors import UniqueViolation
        db_auth.passkey_create("ana", "dup", PUBLIC_KEY, nombre="primera")
        with self.assertRaises(UniqueViolation):
            db_auth.passkey_create("beto", "dup", {"other": True}, nombre="segunda")
        rows = _sql("SELECT username, name, public_key FROM passkeys")
        self.assertEqual(rows, [{"username": "ana", "name": "primera", "public_key": PUBLIC_KEY}])

    def test_ids_increase(self):
        a = db_auth.passkey_create("ana", "c1", PUBLIC_KEY)["id"]
        b = db_auth.passkey_create("ana", "c2", PUBLIC_KEY)["id"]
        self.assertGreater(b, a)


class TestPasskeysOf(DbCase):
    def test_empty_for_a_user_without_keys(self):
        self.assertEqual(db_auth.passkeys_of("nadie"), [])

    def test_oldest_first_public_shape_and_only_that_user(self):
        db_auth.passkey_create("ana", "c-late", PUBLIC_KEY, sign_count=3, nombre="late")
        db_auth.passkey_create("ana", "c-early", PUBLIC_KEY, nombre="early")
        db_auth.passkey_create("beto", "c-beto", PUBLIC_KEY)
        base = _now() - timedelta(days=2)
        _sql("UPDATE passkeys SET created=%s WHERE credential_id='c-early'", (base,))
        _sql("UPDATE passkeys SET created=%s WHERE credential_id='c-late'",
             (base + timedelta(hours=1),))
        _sql("UPDATE passkeys SET last_used=%s WHERE credential_id='c-late'",
             (base + timedelta(hours=2),))
        rows = db_auth.passkeys_of("ana")
        self.assertEqual([r["credential_id"] for r in rows], ["c-early", "c-late"])
        for row in rows:
            self.assertEqual(sorted(row), ["creado", "credential_id", "id", "nombre", "ultimo_uso",
                                           "usuario"])
            self.assertEqual(row["usuario"], "ana")
        self.assertEqual(rows[0]["creado"], base.isoformat())
        self.assertIsNone(rows[0]["ultimo_uso"])
        self.assertEqual(rows[1]["ultimo_uso"], (base + timedelta(hours=2)).isoformat())

    def test_username_match_is_exact(self):
        db_auth.passkey_create("Ana", "c1", PUBLIC_KEY)
        self.assertEqual(db_auth.passkeys_of("ana"), [])


class TestPasskeyByCredential(DbCase):
    def test_unknown_credential_is_none(self):
        self.assertIsNone(db_auth.passkey_by_credential("nope"))

    def test_returns_the_full_row_with_key_counter_and_raw_datetimes(self):
        created = db_auth.passkey_create("ana", "cred-1", PUBLIC_KEY, sign_count=5, nombre="Llave")
        row = db_auth.passkey_by_credential("cred-1")
        self.assertEqual(list(row), ["id", "usuario", "credential_id", "public_key", "sign_count",
                                     "nombre", "creado", "ultimo_uso"])
        self.assertEqual(row["id"], created["id"])
        self.assertEqual(row["usuario"], "ana")
        self.assertEqual(row["public_key"], PUBLIC_KEY)
        self.assertEqual(row["sign_count"], 5)
        self.assertEqual(row["nombre"], "Llave")
        self.assertIsInstance(row["creado"], datetime)
        self.assertIsNone(row["ultimo_uso"])

    def test_lookup_is_exact(self):
        db_auth.passkey_create("ana", "Cred", PUBLIC_KEY)
        self.assertIsNone(db_auth.passkey_by_credential("cred"))
        self.assertIsNotNone(db_auth.passkey_by_credential("Cred"))


class TestPasskeyMarkUse(DbCase):
    def _make(self, count=0):
        db_auth.passkey_create("ana", "cred", PUBLIC_KEY, sign_count=count)

    def _row(self):
        return _sql("SELECT sign_count, last_used FROM passkeys WHERE credential_id='cred'")[0]

    def test_unknown_credential_is_false(self):
        self.assertIs(db_auth.passkey_mark_use("nope"), False)
        self.assertIs(db_auth.passkey_mark_use("nope", 5), False)

    def test_without_a_counter_only_the_use_time_moves(self):
        self._make(count=9)
        for sign in (None, 0, -3, "0"):
            _sql("UPDATE passkeys SET last_used=NULL")
            self.assertIs(db_auth.passkey_mark_use("cred", sign), True, sign)
            row = self._row()
            self.assertEqual(row["sign_count"], 9)
            self.assertIsNotNone(row["last_used"])

    def test_a_higher_counter_advances_it_and_stamps_the_use(self):
        self._make(count=3)
        self.assertIs(db_auth.passkey_mark_use("cred", 4), True)
        row = self._row()
        self.assertEqual(row["sign_count"], 4)
        self.assertGreater(row["last_used"], _now() - timedelta(minutes=1))

    def test_a_counter_from_zero_is_accepted_and_a_string_counter_too(self):
        self._make(count=0)
        self.assertIs(db_auth.passkey_mark_use("cred", "12"), True)
        self.assertEqual(self._row()["sign_count"], 12)

    def test_a_replayed_or_lower_counter_is_rejected_and_nothing_changes(self):
        self._make(count=10)
        before = self._row()
        self.assertIs(db_auth.passkey_mark_use("cred", 10), False)
        self.assertIs(db_auth.passkey_mark_use("cred", 9), False)
        self.assertEqual(self._row(), before)
        self.assertIsNone(before["last_used"])

    def test_the_counter_never_moves_back_after_advancing(self):
        self._make(count=1)
        self.assertIs(db_auth.passkey_mark_use("cred", 8), True)
        self.assertIs(db_auth.passkey_mark_use("cred", 5), False)
        self.assertEqual(self._row()["sign_count"], 8)

    def test_a_non_numeric_counter_raises_before_touching_the_row(self):
        self._make(count=2)
        with self.assertRaises(ValueError):
            db_auth.passkey_mark_use("cred", "abc")
        self.assertEqual(self._row(), {"sign_count": 2, "last_used": None})

    def test_only_the_named_credential_is_updated(self):
        self._make(count=1)
        db_auth.passkey_create("ana", "other", PUBLIC_KEY, sign_count=1)
        db_auth.passkey_mark_use("cred", 5)
        other = _sql("SELECT sign_count, last_used FROM passkeys WHERE credential_id='other'")[0]
        self.assertEqual(other, {"sign_count": 1, "last_used": None})


class TestPasskeyChallengeIssue(DbCase):
    def _rows(self):
        return _sql("SELECT * FROM passkey_challenges ORDER BY created_at, challenge_digest")

    def test_stores_the_digest_purpose_username_and_expiry(self):
        before = _now()
        self.assertIsNone(db_auth.passkey_challenge_issue(b"d" * 32, "registration", "ana", 120))
        after = _now()
        row = self._rows()[0]
        self.assertEqual(bytes(row["challenge_digest"]), b"d" * 32)
        self.assertEqual((row["purpose"], row["username"]), ("registration", "ana"))
        self.assertIsNone(row["consumed_at"])
        self.assertGreaterEqual(row["expires_at"], before + timedelta(seconds=120))
        self.assertLessEqual(row["expires_at"], after + timedelta(seconds=120))

    def test_defaults_are_a_login_challenge_without_user_valid_five_minutes(self):
        before = _now()
        db_auth.passkey_challenge_issue(b"x" * 32, "login")
        row = self._rows()[0]
        self.assertIsNone(row["username"])
        self.assertGreaterEqual(row["expires_at"], before + timedelta(seconds=300))
        self.assertLess(row["expires_at"], before + timedelta(seconds=310))

    def test_accepts_bytes_like_digests_and_a_string_ttl(self):
        db_auth.passkey_challenge_issue(bytearray(b"a" * 32), "login", None, "60")
        db_auth.passkey_challenge_issue(memoryview(b"b" * 32), "login")
        self.assertEqual({bytes(r["challenge_digest"]) for r in self._rows()},
                         {b"a" * 32, b"b" * 32})

    def test_rejects_an_unknown_purpose_or_a_bad_ttl_and_writes_nothing(self):
        for args in (("x" * 3, "other"), (b"y", "LOGIN"), (b"y", "")):
            with self.assertRaises(ValueError):
                db_auth.passkey_challenge_issue(*args)
        for ttl in (0, -1, "0"):
            with self.assertRaises(ValueError):
                db_auth.passkey_challenge_issue(b"y", "login", None, ttl)
        with self.assertRaises(ValueError):
            db_auth.passkey_challenge_issue(b"y", "login", None, "soon")
        self.assertEqual(self._rows(), [])

    def test_a_text_digest_is_refused(self):
        with self.assertRaises(TypeError):
            db_auth.passkey_challenge_issue("not-bytes", "login")
        self.assertEqual(self._rows(), [])

    def test_issuing_collects_expired_and_long_consumed_challenges_only(self):
        for digest, purpose in ((b"expired", "login"), (b"old-consumed", "login"),
                                (b"new-consumed", "login"), (b"live", "login"),
                                (b"consumed-expired", "registration")):
            db_auth.passkey_challenge_issue(digest, purpose, None, 600)
        now = _now()
        _sql("UPDATE passkey_challenges SET expires_at=%s WHERE challenge_digest=%s",
             (now - timedelta(seconds=1), b"expired"))
        _sql("UPDATE passkey_challenges SET consumed_at=%s WHERE challenge_digest=%s",
             (now - timedelta(days=2), b"old-consumed"))
        _sql("UPDATE passkey_challenges SET consumed_at=%s WHERE challenge_digest=%s",
             (now - timedelta(hours=2), b"new-consumed"))
        _sql("UPDATE passkey_challenges SET consumed_at=%s, expires_at=%s "
             "WHERE challenge_digest=%s", (now - timedelta(minutes=5), now - timedelta(minutes=1),
                                           b"consumed-expired"))
        db_auth.passkey_challenge_issue(b"fresh", "login")
        self.assertEqual({bytes(r["challenge_digest"]) for r in self._rows()},
                         {b"new-consumed", b"live", b"fresh"})

    def test_a_duplicate_digest_raises_and_the_cleanup_rolls_back_with_it(self):
        from psycopg.errors import UniqueViolation
        db_auth.passkey_challenge_issue(b"dup", "login", None, 600)
        db_auth.passkey_challenge_issue(b"stale", "login", None, 600)
        _sql("UPDATE passkey_challenges SET expires_at=%s WHERE challenge_digest=%s",
             (_now() - timedelta(minutes=1), b"stale"))
        with self.assertRaises(UniqueViolation):
            db_auth.passkey_challenge_issue(b"dup", "login", None, 600)
        self.assertEqual({bytes(r["challenge_digest"]) for r in self._rows()}, {b"dup", b"stale"})


class TestPasskeyChallengeConsume(DbCase):
    def _consumed_at(self, digest):
        return _sql("SELECT consumed_at FROM passkey_challenges WHERE challenge_digest=%s",
                    (digest,))[0]["consumed_at"]

    def test_a_challenge_is_consumed_exactly_once(self):
        db_auth.passkey_challenge_issue(b"one-shot", "login", None)
        self.assertIs(db_auth.passkey_challenge_consume(b"one-shot", "login"), True)
        self.assertGreater(self._consumed_at(b"one-shot"), _now() - timedelta(minutes=1))
        self.assertIs(db_auth.passkey_challenge_consume(b"one-shot", "login"), False)

    def test_unknown_digest_is_false(self):
        self.assertIs(db_auth.passkey_challenge_consume(b"nope", "login"), False)

    def test_an_invalid_purpose_is_false_and_leaves_the_challenge_usable(self):
        db_auth.passkey_challenge_issue(b"c", "login", None)
        for purpose in ("other", "", None, "LOGIN"):
            self.assertIs(db_auth.passkey_challenge_consume(b"c", purpose), False)
        self.assertIsNone(self._consumed_at(b"c"))
        self.assertIs(db_auth.passkey_challenge_consume(b"c", "login"), True)

    def test_the_purpose_must_match_the_one_issued(self):
        db_auth.passkey_challenge_issue(b"reg", "registration", "ana")
        self.assertIs(db_auth.passkey_challenge_consume(b"reg", "login", "ana"), False)
        self.assertIsNone(self._consumed_at(b"reg"))
        self.assertIs(db_auth.passkey_challenge_consume(b"reg", "registration", "ana"), True)

    def test_the_username_must_match_including_none(self):
        db_auth.passkey_challenge_issue(b"for-ana", "registration", "ana")
        db_auth.passkey_challenge_issue(b"anon", "login", None)
        self.assertIs(db_auth.passkey_challenge_consume(b"for-ana", "registration", "beto"), False)
        self.assertIs(db_auth.passkey_challenge_consume(b"for-ana", "registration", "Ana"), False)
        self.assertIs(db_auth.passkey_challenge_consume(b"for-ana", "registration", None), False)
        self.assertIs(db_auth.passkey_challenge_consume(b"anon", "login", "ana"), False)
        self.assertIsNone(self._consumed_at(b"for-ana"))
        self.assertIsNone(self._consumed_at(b"anon"))
        self.assertIs(db_auth.passkey_challenge_consume(b"anon", "login", None), True)
        self.assertIs(db_auth.passkey_challenge_consume(b"for-ana", "registration", "ana"), True)

    def test_an_expired_challenge_is_false_and_stays_unconsumed(self):
        db_auth.passkey_challenge_issue(b"late", "login", None)
        _sql("UPDATE passkey_challenges SET expires_at=%s", (_now() - timedelta(seconds=1),))
        self.assertIs(db_auth.passkey_challenge_consume(b"late", "login"), False)
        self.assertIsNone(self._consumed_at(b"late"))

    def test_accepts_bytes_like_digests(self):
        db_auth.passkey_challenge_issue(b"z" * 8, "login")
        self.assertIs(db_auth.passkey_challenge_consume(bytearray(b"z" * 8), "login"), True)

    def test_consuming_one_leaves_the_others(self):
        db_auth.passkey_challenge_issue(b"a", "login")
        db_auth.passkey_challenge_issue(b"b", "login")
        db_auth.passkey_challenge_consume(b"a", "login")
        self.assertIsNone(self._consumed_at(b"b"))


class TestPasskeyDelete(DbCase):
    def _credentials(self):
        return [r["credential_id"] for r in _sql("SELECT credential_id FROM passkeys "
                                                  "ORDER BY id")]

    def test_unknown_credential_is_false(self):
        self.assertIs(db_auth.passkey_delete("nope"), False)
        self.assertIs(db_auth.passkey_delete("nope", "ana"), False)

    def test_with_the_owner_it_deletes_that_key_only(self):
        db_auth.passkey_create("ana", "c1", PUBLIC_KEY)
        db_auth.passkey_create("ana", "c2", PUBLIC_KEY)
        self.assertIs(db_auth.passkey_delete("c1", "ana"), True)
        self.assertEqual(self._credentials(), ["c2"])
        self.assertIs(db_auth.passkey_delete("c1", "ana"), False)

    def test_somebody_elses_key_is_not_deleted(self):
        db_auth.passkey_create("ana", "c1", PUBLIC_KEY)
        self.assertIs(db_auth.passkey_delete("c1", "beto"), False)
        self.assertIs(db_auth.passkey_delete("c1", "Ana"), False)
        self.assertEqual(self._credentials(), ["c1"])

    def test_without_an_owner_any_key_is_deleted(self):
        db_auth.passkey_create("ana", "c1", PUBLIC_KEY)
        db_auth.passkey_create("beto", "c2", PUBLIC_KEY)
        self.assertIs(db_auth.passkey_delete("c1"), True)
        self.assertIs(db_auth.passkey_delete("c2", None), True)
        self.assertEqual(self._credentials(), [])

    def test_passkey_delete_with_an_empty_owner_deletes_nothing(self):
        # Defence in depth: only `None` means "any owner"; an empty owner matches no key.
        db_auth.passkey_create("ana", "c1", PUBLIC_KEY)
        db_auth.passkey_create("beto", "c2", PUBLIC_KEY)
        self.assertIs(db_auth.passkey_delete("c1", ""), False)
        self.assertIs(db_auth.passkey_delete("c2", ""), False)
        self.assertEqual(self._credentials(), ["c1", "c2"])


class TestPasskeysDeleteOfAndCount(DbCase):
    def test_delete_of_returns_how_many_and_only_that_users(self):
        db_auth.passkey_create("ana", "c1", PUBLIC_KEY)
        db_auth.passkey_create("ana", "c2", PUBLIC_KEY)
        db_auth.passkey_create("beto", "c3", PUBLIC_KEY)
        self.assertEqual(db_auth.passkeys_delete_of("ana"), 2)
        self.assertEqual(db_auth.passkeys_delete_of("ana"), 0)
        self.assertEqual(db_auth.passkeys_delete_of("nadie"), 0)
        self.assertEqual([r["credential_id"] for r in _sql("SELECT credential_id FROM passkeys")],
                         ["c3"])

    def test_delete_of_is_exact_on_the_username(self):
        db_auth.passkey_create("Ana", "c1", PUBLIC_KEY)
        self.assertEqual(db_auth.passkeys_delete_of("ana"), 0)

    def test_count_is_a_plain_dict_of_ints_per_user(self):
        self.assertEqual(db_auth.passkeys_count(), {})
        db_auth.passkey_create("ana", "c1", PUBLIC_KEY)
        db_auth.passkey_create("ana", "c2", PUBLIC_KEY)
        db_auth.passkey_create("beto", "c3", PUBLIC_KEY)
        counts = db_auth.passkeys_count()
        self.assertEqual(counts, {"ana": 2, "beto": 1})
        self.assertTrue(all(type(n) is int for n in counts.values()))
        db_auth.passkeys_delete_of("ana")
        self.assertEqual(db_auth.passkeys_count(), {"beto": 1})


# ---- admin_preferences --------------------------------------------------------------------

class TestAdminPreferences(DbCase):
    def test_no_preferences_is_an_empty_dict(self):
        self.assertEqual(db_auth.admin_preferences("ana"), {})

    def test_save_returns_the_stored_json_value(self):
        value = {"cols": ["a", "b"], "n": 3, "ok": True, "t": "ñandú", "nil": None}
        self.assertEqual(db_auth.admin_preference_save("ana", "tabla", value), value)
        self.assertEqual(_sql("SELECT value FROM admin_preferences")[0]["value"], value)

    def test_any_json_type_round_trips(self):
        for index, value in enumerate(([1, "dos", [3]], "texto", 42, 1.5, True, False, None, {})):
            key = f"k{index}"
            self.assertEqual(db_auth.admin_preference_save("ana", key, value), value)
            self.assertEqual(db_auth.admin_preferences("ana")[key], value)
            self.assertEqual(type(db_auth.admin_preferences("ana")[key]), type(value))

    def test_returns_a_key_to_value_map_for_that_user_only(self):
        db_auth.admin_preference_save("ana", "tema", "oscuro")
        db_auth.admin_preference_save("ana", "orden", {"by": "hora"})
        db_auth.admin_preference_save("beto", "tema", "claro")
        self.assertEqual(db_auth.admin_preferences("ana"), {"tema": "oscuro", "orden": {"by": "hora"}})
        self.assertEqual(db_auth.admin_preferences("beto"), {"tema": "claro"})
        self.assertEqual(db_auth.admin_preferences("Ana"), {})

    def test_saving_again_replaces_the_value_and_bumps_updated(self):
        db_auth.admin_preference_save("ana", "tema", "oscuro")
        db_auth.admin_preference_save("ana", "otra", 1)
        _sql("UPDATE admin_preferences SET updated=%s", (_now() - timedelta(days=1),))
        self.assertEqual(db_auth.admin_preference_save("ana", "tema", ["nuevo"]), ["nuevo"])
        rows = {r["key"]: r for r in _sql("SELECT * FROM admin_preferences")}
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows["tema"]["value"], ["nuevo"])
        self.assertGreater(rows["tema"]["updated"], _now() - timedelta(minutes=1))
        self.assertLess(rows["otra"]["updated"], _now() - timedelta(hours=1))
        self.assertEqual(db_auth.admin_preferences("ana"), {"tema": ["nuevo"], "otra": 1})

    def test_keys_are_case_sensitive(self):
        db_auth.admin_preference_save("ana", "Tema", 1)
        db_auth.admin_preference_save("ana", "tema", 2)
        self.assertEqual(db_auth.admin_preferences("ana"), {"Tema": 1, "tema": 2})


# ---- admin_reset_tokens -----------------------------------------------------------------------

def _operator(usuario, password="old-pass-123", active=True):
    db_operators.operator_create(usuario, password=password, rol="admin", activo=active)


def _tokens(usuario=None):
    if usuario is None:
        return _sql("SELECT * FROM admin_reset_tokens ORDER BY created, token_hash")
    return _sql("SELECT * FROM admin_reset_tokens WHERE username=%s ORDER BY created, token_hash",
                (usuario,))


def _pass_hash(usuario):
    return _sql("SELECT pass_hash FROM operators WHERE username=%s", (usuario,))[0]["pass_hash"]


class TestResetTokenCreate(DbCase):
    def test_returns_the_raw_token_and_stores_only_its_sha256(self):
        import hashlib
        before = _now()
        raw = db_auth.admin_reset_token_create("ana", horas=2)
        after = _now()
        self.assertIsInstance(raw, str)
        self.assertGreaterEqual(len(raw), 40)
        rows = _tokens()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["token_hash"], hashlib.sha256(raw.encode()).hexdigest())
        self.assertNotIn(raw, str(row))
        self.assertEqual(row["username"], "ana")
        self.assertIs(row["used"], False)
        self.assertGreaterEqual(row["expires"], before + timedelta(hours=2))
        self.assertLessEqual(row["expires"], after + timedelta(hours=2))

    def test_default_lifetime_is_a_day_and_hours_may_be_text(self):
        before = _now()
        db_auth.admin_reset_token_create("ana")
        db_auth.admin_reset_token_create("beto", horas="3")
        ana, beto = _tokens("ana")[0], _tokens("beto")[0]
        self.assertGreaterEqual(ana["expires"], before + timedelta(hours=24))
        self.assertLess(ana["expires"], before + timedelta(hours=24, minutes=1))
        self.assertGreaterEqual(beto["expires"], before + timedelta(hours=3))
        self.assertLess(beto["expires"], before + timedelta(hours=3, minutes=1))

    def test_tokens_are_unique(self):
        self.assertEqual(len({db_auth.admin_reset_token_create("ana") for _ in range(5)}), 5)

    def test_a_new_token_voids_the_unused_ones_of_that_user_only(self):
        old = db_auth.admin_reset_token_create("ana")
        other = db_auth.admin_reset_token_create("beto")
        new = db_auth.admin_reset_token_create("ana")
        self.assertIsNone(db_auth.admin_reset_token_username(old))
        self.assertEqual(db_auth.admin_reset_token_username(new), "ana")
        self.assertEqual(db_auth.admin_reset_token_username(other), "beto")
        ana = _tokens("ana")
        self.assertEqual(sorted(r["used"] for r in ana), [False, True])
        self.assertEqual(len(ana), 2)

    def test_creating_does_not_require_the_account_to_exist(self):
        raw = db_auth.admin_reset_token_create("fantasma")
        self.assertEqual(db_auth.admin_reset_token_username(raw), "fantasma")

    def test_a_non_numeric_lifetime_raises_and_writes_nothing(self):
        with self.assertRaises(ValueError):
            db_auth.admin_reset_token_create("ana", horas="mucho")
        self.assertEqual(_tokens(), [])

    def test_zero_hours_makes_a_token_that_is_already_expired(self):
        raw = db_auth.admin_reset_token_create("ana", horas=0)
        self.assertIsNone(db_auth.admin_reset_token_username(raw))


class TestResetTokenStatusAndUsername(DbCase):
    def test_a_live_token_is_ok_and_names_its_user(self):
        raw = db_auth.admin_reset_token_create("ana")
        self.assertIs(db_auth.admin_reset_token_status(raw), True)
        self.assertEqual(db_auth.admin_reset_token_username(raw), "ana")

    def test_reading_does_not_consume_the_token(self):
        raw = db_auth.admin_reset_token_create("ana")
        for _ in range(3):
            db_auth.admin_reset_token_status(raw)
            db_auth.admin_reset_token_username(raw)
        self.assertEqual([r["used"] for r in _tokens("ana")], [False])

    def test_unknown_empty_and_none_tokens(self):
        db_auth.admin_reset_token_create("ana")
        for raw in ("nope", "", None, 0, "   "):
            self.assertIs(db_auth.admin_reset_token_status(raw), False, raw)
            self.assertIsNone(db_auth.admin_reset_token_username(raw), raw)

    def test_the_stored_digest_itself_is_not_a_valid_token(self):
        db_auth.admin_reset_token_create("ana")
        digest = _tokens()[0]["token_hash"]
        self.assertIsNone(db_auth.admin_reset_token_username(digest))

    def test_a_used_token_is_not_ok(self):
        raw = db_auth.admin_reset_token_create("ana")
        _sql("UPDATE admin_reset_tokens SET used=true")
        self.assertIs(db_auth.admin_reset_token_status(raw), False)
        self.assertIsNone(db_auth.admin_reset_token_username(raw))

    def test_an_expired_token_is_not_ok(self):
        raw = db_auth.admin_reset_token_create("ana")
        _sql("UPDATE admin_reset_tokens SET expires=%s", (_now() - timedelta(seconds=1),))
        self.assertIs(db_auth.admin_reset_token_status(raw), False)
        self.assertIsNone(db_auth.admin_reset_token_username(raw))

    def test_a_token_is_ok_until_the_last_moment(self):
        raw = db_auth.admin_reset_token_create("ana")
        _sql("UPDATE admin_reset_tokens SET expires=%s", (_now() + timedelta(minutes=5),))
        self.assertEqual(db_auth.admin_reset_token_username(raw), "ana")

    def test_a_non_string_raw_is_stringified(self):
        raw = db_auth.admin_reset_token_create("ana")
        class Wrapper:
            def __str__(self):
                return raw
        self.assertEqual(db_auth.admin_reset_token_username(Wrapper()), "ana")


class TestResetTokenConsume(DbCase):
    def test_changes_the_password_burns_the_token_and_returns_the_user(self):
        _operator("ana", "old-pass-123")
        raw = db_auth.admin_reset_token_create("ana")
        self.assertEqual(db_auth.admin_reset_token_consume(raw, "brand-new-pass-1"), "ana")
        stored = _pass_hash("ana")
        self.assertNotIn("brand-new-pass-1", stored)
        self.assertTrue(password_hashing.is_current_hash(stored))
        self.assertEqual(password_hashing.verify_and_update(stored, "brand-new-pass-1"),
                         (True, None))
        self.assertFalse(password_hashing.verify_and_update(stored, "old-pass-123")[0])
        self.assertEqual([r["used"] for r in _tokens("ana")], [True])
        self.assertIsNone(db_auth.admin_reset_token_username(raw))

    def test_a_token_works_only_once(self):
        _operator("ana")
        raw = db_auth.admin_reset_token_create("ana")
        self.assertEqual(db_auth.admin_reset_token_consume(raw, "first-new-pass-1"), "ana")
        first = _pass_hash("ana")
        self.assertIsNone(db_auth.admin_reset_token_consume(raw, "second-new-pass-2"))
        self.assertEqual(_pass_hash("ana"), first)

    def test_unknown_empty_and_none_tokens_change_nothing(self):
        _operator("ana")
        db_auth.admin_reset_token_create("ana")
        before = _pass_hash("ana")
        for raw in ("nope", "", None):
            self.assertIsNone(db_auth.admin_reset_token_consume(raw, "new-pass-123"), raw)
        self.assertEqual(_pass_hash("ana"), before)
        self.assertEqual([r["used"] for r in _tokens("ana")], [False])

    def test_an_expired_token_changes_nothing(self):
        _operator("ana")
        raw = db_auth.admin_reset_token_create("ana")
        _sql("UPDATE admin_reset_tokens SET expires=%s", (_now() - timedelta(seconds=1),))
        before = _pass_hash("ana")
        self.assertIsNone(db_auth.admin_reset_token_consume(raw, "new-pass-123"))
        self.assertEqual(_pass_hash("ana"), before)
        self.assertEqual([r["used"] for r in _tokens("ana")], [False])

    def test_a_superseded_token_changes_nothing(self):
        _operator("ana")
        old = db_auth.admin_reset_token_create("ana")
        new = db_auth.admin_reset_token_create("ana")
        before = _pass_hash("ana")
        self.assertIsNone(db_auth.admin_reset_token_consume(old, "new-pass-123"))
        self.assertEqual(_pass_hash("ana"), before)
        self.assertEqual(db_auth.admin_reset_token_consume(new, "new-pass-123"), "ana")

    def test_an_inactive_account_is_refused_and_the_token_stays_unspent(self):
        _operator("ana", active=False)
        raw = db_auth.admin_reset_token_create("ana")
        before = _pass_hash("ana")
        self.assertIsNone(db_auth.admin_reset_token_consume(raw, "new-pass-123"))
        self.assertEqual(_pass_hash("ana"), before)
        self.assertEqual([r["used"] for r in _tokens("ana")], [False])

    def test_a_token_for_a_missing_account_is_refused_and_stays_unspent(self):
        raw = db_auth.admin_reset_token_create("fantasma")
        self.assertIsNone(db_auth.admin_reset_token_consume(raw, "new-pass-123"))
        self.assertEqual([r["used"] for r in _tokens("fantasma")], [False])

    def test_only_the_tokens_owner_changes(self):
        _operator("ana")
        _operator("beto")
        beto = _pass_hash("beto")
        raw = db_auth.admin_reset_token_create("ana")
        self.assertEqual(db_auth.admin_reset_token_consume(raw, "new-pass-123"), "ana")
        self.assertEqual(_pass_hash("beto"), beto)

    def test_other_operator_fields_are_untouched(self):
        db_operators.operator_create("ana", password="old-pass-123", rol="admin", email="ana@example.test",
                           totp_secret="AAAA")
        raw = db_auth.admin_reset_token_create("ana")
        db_auth.admin_reset_token_consume(raw, "new-pass-123")
        row = _sql("SELECT username, role, active, email, totp_secret FROM operators")[0]
        self.assertEqual(row, {"username": "ana", "role": "admin", "active": True,
                               "email": "ana@example.test", "totp_secret": "AAAA"})

    def test_a_non_string_password_raises_and_the_token_survives(self):
        _operator("ana")
        raw = db_auth.admin_reset_token_create("ana")
        before = _pass_hash("ana")
        with self.assertRaises(TypeError):
            db_auth.admin_reset_token_consume(raw, None)
        self.assertEqual(_pass_hash("ana"), before)
        self.assertEqual(db_auth.admin_reset_token_username(raw), "ana")

    def test_the_token_row_is_locked_so_two_consumers_cannot_both_win(self):
        _operator("ana")
        raw = db_auth.admin_reset_token_create("ana")
        results = []

        def attempt(password):
            results.append(db_auth.admin_reset_token_consume(raw, password))

        threads = [threading.Thread(target=attempt, args=(f"race-pass-{i}-xyz",)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual(sorted(results, key=lambda r: r is None), ["ana", None, None, None])


if __name__ == "__main__":
    unittest.main()
