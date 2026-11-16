"""test_db_accounts_store.py — the patient-account statements of `core/db.py` against a real Postgres.

Stage 5 of the SQLAlchemy Core move converts the accounts group of `core/db.py`: registration
(`create_user`, `create_user_and_session`, `login_renaper`, `user_claim_portal`), reads
(`get_user`, `get_user_by_email`, `user_by_portal_id`, `users_admin_list`), profile writes
(`update_user`, `user_admin_update`, `update_self_booking_coverage`, `archive_user`, `change_dni`
/ `change_dni_in`), passwords and sessions (`verify_password`, `set_password`, `create_session`,
`get_session`, `delete_session`, `purge_idle_sessions`), the one-shot tokens (reset tokens, OTP
codes and their "verified" ticket) and the calendar-feed / MCP tokens. This file pins each of
those functions row for row — returned shapes, the negative paths first (wrong password,
expired / used / exhausted codes, archived accounts), what is stored hashed, what is and is not
written, and the all-or-nothing transactions — so the conversion is proven to change nothing.
It was written and run green on the hand-written SQL first, and passes unedited on Core.
"""
import hashlib
import json
import os
import re
import secrets
import sys
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from cryptography.hazmat.primitives.kdf.scrypt import Scrypt  # noqa: E402
from psycopg import errors as pgerr  # noqa: E402

from app.auth import password_hashing  # noqa: E402
from app.core import db  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_values  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_db_accounts_store")

UTC = timezone.utc

# What every public user dict carries (the Spanish wire keys of `_wire`, minus the secrets
# `_user_public` drops): never `pass_hash`, `portal_user_id`, `feed_token`, `feed_token_hint`,
# `mcp_token` nor `mcp_token_hint`.
USER_KEYS = {"id", "dni", "nombre", "apellido", "nacimiento", "sexo", "contacto", "email",
             "created", "identity_kind", "activo", "cobertura",
             "domicilio", "avisos", "updated", "lang", "renaper"}


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _sql(sql, params=None):
    """Runs one statement and returns all its rows (or [] for a statement without any)."""
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


def _one(sql, params=None):
    return _sql(sql, params)[0]


def _count(table, where="TRUE", params=None):
    return _one(f"SELECT count(*) AS n FROM {table} WHERE {where}", params)["n"]


class DbCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "TRUNCATE users, sessions, reset_tokens, otp_codes, otp_verified, dependents, "
                "booking_requests, waitlist, appointment_offers, attendance_events, interviews, "
                "patient_health_profile, patient_summaries, clinical_history_records, "
                "lab_report_analysis, patient_lab_uploads, ai_costs, chat_messages, chat_summaries, "
                "chat_compactions, chat_handoffs, hsi_patients, operators, admin_account_security "
                "RESTART IDENTITY CASCADE")


def _raw_user(dni, **cols):
    """A users row written straight with SQL (`cols` are real column names)."""
    cols = {"first_name": "Ana", "last_name": "Pérez", **cols}
    names = ["dni", *cols]
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(f"INSERT INTO users ({', '.join(names)}) VALUES ({', '.join(['%s'] * len(names))}) "
                    "RETURNING id", [dni, *cols.values()])
        return cur.fetchone()["id"]


def _make(dni="30111222", password="clave-segura-1", **kw):
    """A user through the public `create_user` (every argument defaulted)."""
    args = dict(nombre="Ana", apellido="Pérez", nacimiento="1990-05-17", sexo="F",
                contacto="2255123456", password=password)
    args.update(kw)
    return db_accounts.create_user(dni, args.pop("nombre"), args.pop("apellido"), args.pop("nacimiento"),
                          args.pop("sexo"), args.pop("contacto"), args.pop("password"), **args)


def _row(dni):
    rows = _sql("SELECT * FROM users WHERE dni=%s", (dni,))
    return rows[0] if rows else None


def _scrypt_hash(password):
    salt = os.urandom(16)
    digest = Scrypt(salt=salt, length=32, n=16384, r=8, p=1).derive(password.encode("utf-8"))
    return f"scrypt${salt.hex()}${digest.hex()}"


def _secs_from_now(table, column, where, params):
    """Seconds between now() and a timestamptz column, computed by the database."""
    return float(_one(f"SELECT EXTRACT(EPOCH FROM ({column} - now())) AS s FROM {table} WHERE {where}",
                      params)["s"])


# ---- value-free legacy inventory ---------------------------------------------------------

class TestLegacyPasswordHashInventory(DbCase):
    def test_empty_database_is_all_zeros(self):
        self.assertEqual(db_accounts.legacy_password_hash_inventory(),
                         {"patients": 0, "staff": 0, "owners": 0})

    def test_counts_only_the_scrypt_prefix_per_table(self):
        _raw_user("1", pass_hash=_scrypt_hash("a"))
        _raw_user("2", pass_hash=_scrypt_hash("b"))
        _raw_user("3", pass_hash="$argon2id$v=19$m=1,t=1,p=1$c2FsdA$aGFzaA")
        _raw_user("4", pass_hash="scrypt-without-dollar")
        _raw_user("5", pass_hash=None)
        _sql("INSERT INTO operators (username, pass_hash) VALUES ('s1', 'scrypt$x'), ('s2', 'x')")
        _sql("INSERT INTO admin_account_security (username, owner_pass_hash) VALUES "
             "('o1', 'scrypt$y'), ('o2', NULL), ('o3', '$argon2id$z')")
        self.assertEqual(db_accounts.legacy_password_hash_inventory(),
                         {"patients": 2, "staff": 1, "owners": 1})

    def test_returns_plain_ints_and_nothing_identifying(self):
        _raw_user("1", pass_hash=_scrypt_hash("a"))
        inventory = db_accounts.legacy_password_hash_inventory()
        self.assertEqual(set(inventory), {"patients", "staff", "owners"})
        self.assertTrue(all(type(v) is int for v in inventory.values()))


# ---- small pure helpers --------------------------------------------------------------------

class TestCoerceJsonb(unittest.TestCase):
    def test_none_stays_none(self):
        self.assertIsNone(db_values._coerce_jsonb(None))

    def test_dict_and_list_become_json_text_keeping_non_ascii(self):
        self.assertEqual(db_values._coerce_jsonb({"calle": "Avenida Libertad ñ", "n": 3}),
                         '{"calle": "Avenida Libertad ñ", "n": 3}')
        self.assertEqual(db_values._coerce_jsonb([1, "á", None]), '[1, "á", null]')

    def test_falsy_values_are_not_none(self):
        self.assertEqual(db_values._coerce_jsonb({}), "{}")
        self.assertEqual(db_values._coerce_jsonb([]), "[]")
        self.assertEqual(db_values._coerce_jsonb(0), "0")
        self.assertEqual(db_values._coerce_jsonb(""), '""')
        self.assertEqual(db_values._coerce_jsonb(False), "false")


class TestOtpHelpers(unittest.TestCase):
    def test_hash_is_sha256_of_contact_purpose_code(self):
        self.assertEqual(db_accounts._hash_otp("2255123456", "registro", "123456"),
                         hashlib.sha256(b"2255123456:registro:123456").hexdigest())

    def test_hash_depends_on_all_three_parts(self):
        base = db_accounts._hash_otp("c", "p", "1")
        self.assertNotEqual(base, db_accounts._hash_otp("d", "p", "1"))
        self.assertNotEqual(base, db_accounts._hash_otp("c", "q", "1"))
        self.assertNotEqual(base, db_accounts._hash_otp("c", "p", "2"))

    def test_norm_code_keeps_digits_only(self):
        self.assertEqual(db_accounts._norm_code(" 12-34 56 "), "123456")
        self.assertEqual(db_accounts._norm_code("abc"), "")
        self.assertEqual(db_accounts._norm_code(None), "")
        self.assertEqual(db_accounts._norm_code(0), "")
        self.assertEqual(db_accounts._norm_code(123456), "123456")


# ---- registration ----------------------------------------------------------------------------

class TestCreateUser(DbCase):
    def test_returns_the_new_id_and_stores_every_field(self):
        uid = _make("30111222", email="ana@ejemplo.com", lang="en",
                    cobertura={"obra_social": {"nombre": "PAMI"}, "prepaga": "ñandú"},
                    domicilio={"calle": "Libertad", "numero": "1"})
        self.assertIsInstance(uid, int)
        row = _row("30111222")
        self.assertEqual(row["id"], uid)
        self.assertEqual((row["first_name"], row["last_name"], row["sex"], row["contact"]),
                         ("Ana", "Pérez", "F", "2255123456"))
        self.assertEqual(row["birth_date"], date(1990, 5, 17))
        self.assertEqual(row["email"], "ana@ejemplo.com")
        self.assertEqual(row["lang"], "en")
        self.assertEqual(row["coverage"], {"obra_social": {"nombre": "PAMI"}, "prepaga": "ñandú"})
        self.assertEqual(row["address"], {"calle": "Libertad", "numero": "1"})

    def test_column_defaults(self):
        _make("30111222")
        row = _row("30111222")
        self.assertIs(row["active"], True)
        self.assertEqual(row["identity_kind"], "dni")
        self.assertIsNotNone(row["created"])
        for column in ("email", "lang", "coverage", "address", "notifications", "feed_token",
                       "feed_token_hint", "mcp_token", "mcp_token_hint", "portal_user_id", "renaper", "updated"):
            self.assertIsNone(row[column], column)

    def test_synthetic_identity_kind_is_stored(self):
        _make("900000001", identity_kind="synthetic")
        self.assertEqual(_row("900000001")["identity_kind"], "synthetic")

    def test_password_is_stored_as_an_argon2_hash_never_plaintext(self):
        _make("30111222", password="clave-secreta")
        stored = _row("30111222")["pass_hash"]
        self.assertTrue(stored.startswith("$argon2id$"))
        self.assertNotIn("clave-secreta", stored)
        self.assertIsNotNone(db_accounts.verify_password("30111222", "clave-secreta"))

    def test_birth_date_accepts_date_iso_string_and_none(self):
        _make("1", nacimiento=date(2000, 2, 29))
        _make("2", nacimiento="2001-03-04T05:06:07")
        _make("3", nacimiento=None)
        self.assertEqual(_row("1")["birth_date"], date(2000, 2, 29))
        self.assertEqual(_row("2")["birth_date"], date(2001, 3, 4))
        self.assertIsNone(_row("3")["birth_date"])

    def test_bad_birth_date_raises_and_writes_nothing(self):
        with self.assertRaises(ValueError):
            _make("1", nacimiento="no-es-fecha")
        self.assertEqual(_count("users"), 0)

    def test_duplicate_dni_raises_and_leaves_the_existing_account_intact(self):
        _make("30111222", nombre="Ana", password="primera-clave")
        before = _row("30111222")
        with self.assertRaises(pgerr.UniqueViolation):
            _make("30111222", nombre="Otra", password="segunda-clave")
        self.assertEqual(_row("30111222"), before)
        self.assertEqual(_count("users"), 1)

    def test_two_accounts_get_two_ids(self):
        a, b = _make("1"), _make("2")
        self.assertNotEqual(a, b)


class TestCreateUserAndSession(DbCase):
    def test_returns_public_user_and_a_token_that_resolves(self):
        user, token = db_accounts.create_user_and_session(
            "30111222", "Ana", "Pérez", "1990-05-17", "F", "2255123456", "clave-segura-1",
            email="ana@ejemplo.com", cobertura={"prepaga": "X"}, domicilio={"calle": "Y"}, lang="es")
        self.assertEqual(set(user), USER_KEYS)
        self.assertEqual((user["dni"], user["nombre"], user["apellido"], user["sexo"],
                          user["contacto"], user["email"], user["lang"]),
                         ("30111222", "Ana", "Pérez", "F", "2255123456", "ana@ejemplo.com", "es"))
        self.assertEqual(user["nacimiento"], date(1990, 5, 17))
        self.assertEqual((user["cobertura"], user["domicilio"]), ({"prepaga": "X"}, {"calle": "Y"}))
        self.assertIs(user["activo"], True)
        self.assertEqual(user["identity_kind"], "dni")
        self.assertNotIn("pass_hash", user)
        self.assertNotIn("portal_user_id", user)
        self.assertEqual(user["id"], _row("30111222")["id"])
        self.assertGreaterEqual(len(token), 40)
        self.assertEqual(_sql("SELECT token, dni FROM sessions"), [{"token": token, "dni": "30111222"}])
        self.assertEqual(db_accounts.get_session(token)["dni"], "30111222")

    def test_password_is_hashed(self):
        db_accounts.create_user_and_session("1", "A", "B", None, None, None, "una-clave-larga")
        stored = _row("1")["pass_hash"]
        self.assertTrue(stored.startswith("$argon2id$"))
        self.assertNotIn("una-clave-larga", stored)

    def test_each_registration_gets_its_own_token(self):
        _, t1 = db_accounts.create_user_and_session("1", "A", "B", None, None, None, "clave-larga-1")
        _, t2 = db_accounts.create_user_and_session("2", "A", "B", None, None, None, "clave-larga-2")
        self.assertNotEqual(t1, t2)

    def test_duplicate_dni_raises_user_key_taken_and_writes_nothing(self):
        _make("30111222", nombre="Ana", password="primera-clave")
        before = _row("30111222")
        with self.assertRaises(db_accounts.UserKeyTaken):
            db_accounts.create_user_and_session("30111222", "Otra", "Persona", None, None, None, "otra-clave")
        self.assertEqual(_row("30111222"), before)
        self.assertEqual(_count("users"), 1)
        self.assertEqual(_count("sessions"), 0)

    def test_synthetic_registration_refuses_a_taken_email_case_insensitively(self):
        _make("30111222", email="Ana@Ejemplo.com")
        with self.assertRaises(db_accounts.UserEmailTaken):
            db_accounts.create_user_and_session("900000001", "A", "B", None, None, None, "clave-larga",
                                       email="ana@ejemplo.COM", identity_kind="synthetic")
        self.assertEqual(_count("users"), 1)
        self.assertEqual(_count("sessions"), 0)

    def test_synthetic_registration_with_a_free_email_or_none_goes_through(self):
        user, _ = db_accounts.create_user_and_session("900000001", "A", "B", None, None, None, "clave-larga",
                                             email="libre@ejemplo.com", identity_kind="synthetic")
        self.assertEqual(user["identity_kind"], "synthetic")
        user2, _ = db_accounts.create_user_and_session("900000002", "A", "B", None, None, None, "clave-larga",
                                              identity_kind="synthetic")
        self.assertIsNone(user2["email"])
        self.assertEqual(_count("users"), 2)

    def test_dni_accounts_may_share_an_email(self):
        _make("1", email="familia@ejemplo.com")
        user, _ = db_accounts.create_user_and_session("2", "A", "B", None, None, None, "clave-larga",
                                             email="familia@ejemplo.com")
        self.assertEqual(user["email"], "familia@ejemplo.com")
        self.assertEqual(_count("users", "email=%s", ("familia@ejemplo.com",)), 2)

    def test_account_and_session_commit_together_or_neither(self):
        # A session token that already exists makes the second INSERT fail: the account row,
        # written first in the same transaction, must be rolled back with it.
        _make("1")
        with patch.object(secrets, "token_urlsafe", return_value="repetido"):
            db_accounts.create_session("1")
            with self.assertRaises(pgerr.UniqueViolation):
                db_accounts.create_user_and_session("2", "A", "B", None, None, None, "clave-larga")
        self.assertIsNone(_row("2"))
        self.assertEqual(_count("sessions"), 1)


# ---- reads --------------------------------------------------------------------------------------

class TestGetUserByEmail(DbCase):
    def test_matches_case_insensitively_and_strips(self):
        _make("30111222", email="Ana@Ejemplo.com")
        user = db_accounts.get_user_by_email("  ana@EJEMPLO.com ")
        self.assertEqual(user["dni"], "30111222")
        self.assertEqual(set(user), USER_KEYS)
        self.assertNotIn("pass_hash", user)

    def test_blank_or_missing_is_none(self):
        _make("30111222", email="ana@ejemplo.com")
        for value in (None, "", "   "):
            self.assertIsNone(db_accounts.get_user_by_email(value))
        self.assertIsNone(db_accounts.get_user_by_email("otra@ejemplo.com"))

    def test_shared_email_returns_the_oldest_account(self):
        _make("2", email="f@ejemplo.com")
        _make("1", email="f@ejemplo.com")
        self.assertEqual(db_accounts.get_user_by_email("f@ejemplo.com")["dni"], "2")

    def test_archived_accounts_are_found_too(self):
        _make("1", email="a@ejemplo.com")
        db_accounts.archive_user("1")
        self.assertIs(db_accounts.get_user_by_email("a@ejemplo.com")["activo"], False)


class TestGetUser(DbCase):
    def test_public_shape_with_wire_keys(self):
        _make("30111222", email="a@b.com", cobertura={"p": 1})
        user = db_accounts.get_user("30111222")
        self.assertEqual(set(user), USER_KEYS)
        self.assertEqual((user["nombre"], user["apellido"], user["sexo"], user["contacto"]),
                         ("Ana", "Pérez", "F", "2255123456"))
        self.assertEqual(user["cobertura"], {"p": 1})
        self.assertEqual(user["nacimiento"], date(1990, 5, 17))

    def test_unknown_dni_is_none(self):
        self.assertIsNone(db_accounts.get_user("999"))

    def test_secrets_never_leave(self):
        _raw_user("1", pass_hash="x", portal_user_id="portal-1")
        user = db_accounts.get_user("1")
        self.assertNotIn("pass_hash", user)
        self.assertNotIn("portal_user_id", user)

    def test_archived_account_is_returned_with_activo_false(self):
        _make("1")
        db_accounts.archive_user("1")
        self.assertIs(db_accounts.get_user("1")["activo"], False)


class TestUserByPortalId(DbCase):
    def test_finds_the_bound_account_without_leaking_the_portal_id(self):
        _raw_user("1", portal_user_id="portal-1", pass_hash="x")
        user = db_accounts.user_by_portal_id("portal-1")
        self.assertEqual(user["dni"], "1")
        self.assertEqual(set(user), USER_KEYS)

    def test_empty_or_unknown_is_none(self):
        _raw_user("1", portal_user_id="portal-1")
        for value in (None, "", 0):
            self.assertIsNone(db_accounts.user_by_portal_id(value))
        self.assertIsNone(db_accounts.user_by_portal_id("portal-2"))

    def test_id_is_coerced_to_text(self):
        _raw_user("1", portal_user_id="42")
        self.assertEqual(db_accounts.user_by_portal_id(42)["dni"], "1")

    def test_disabled_account_is_still_returned(self):
        _raw_user("1", portal_user_id="portal-1", active=False)
        self.assertIs(db_accounts.user_by_portal_id("portal-1")["activo"], False)


class TestMcpTokenRevokeOnlyIfSet(DbCase):
    """The portal-rebinding path revokes on EVERY request of a mismatched binding: once the token is gone
    the call must touch no row (no write per request)."""

    def test_revokes_once_then_touches_nothing(self):
        _raw_user("1", pass_hash="x")
        db_accounts.mcp_token_issue("1")
        self.assertTrue(db_accounts.mcp_token_revoke("1", only_if_set=True))
        self.assertFalse(db_accounts.mcp_token_revoke("1", only_if_set=True))
        self.assertIsNone(db_accounts.user_by_mcp_token("anything"))

    def test_plain_revoke_still_reports_the_user(self):
        _raw_user("1", pass_hash="x")
        self.assertTrue(db_accounts.mcp_token_revoke("1"))
        self.assertFalse(db_accounts.mcp_token_revoke("999"))


class TestUsersAdminList(DbCase):
    def _people(self):
        _raw_user("10", first_name="Zoe", last_name="Alvarez", email="zoe@x.com", contact="111")
        _raw_user("20", first_name="Bruno", last_name="Alvarez", contact="222")
        _raw_user("30", first_name="Carla", last_name="Benitez", contact="333")
        _raw_user("40", first_name="Diego", last_name=None, contact="444")

    def test_item_shape_has_no_secrets_and_no_ids(self):
        _raw_user("1", first_name="Ana", last_name="Pérez", birth_date=date(1990, 5, 17), sex="F",
                  contact="123", email="a@b.com", pass_hash="x", portal_user_id="p1",
                  coverage=json.dumps({"prepaga": "X"}), address=json.dumps({"calle": "Y"}),
                  feed_token="ft", mcp_token="mt")
        items, total = db_accounts.users_admin_list()
        self.assertEqual(total, 1)
        self.assertEqual(set(items[0]), {"dni", "nombre", "apellido", "nacimiento", "sexo", "contacto",
                                         "email", "activo", "created", "cobertura", "domicilio"})
        self.assertEqual((items[0]["dni"], items[0]["nombre"], items[0]["apellido"]),
                         ("1", "Ana", "Pérez"))
        self.assertEqual((items[0]["cobertura"], items[0]["domicilio"]), ({"prepaga": "X"}, {"calle": "Y"}))
        self.assertIs(items[0]["activo"], True)

    def test_empty_table(self):
        self.assertEqual(db_accounts.users_admin_list(), ([], 0))

    def test_alphabetical_by_surname_then_name_then_dni_with_null_surnames_last(self):
        self._people()
        _raw_user("05", first_name="Bruno", last_name="Alvarez")
        items, total = db_accounts.users_admin_list()
        self.assertEqual([i["dni"] for i in items], ["05", "20", "10", "30", "40"])
        self.assertEqual(total, 5)

    def test_archived_patients_are_included(self):
        _raw_user("1", active=False)
        items, total = db_accounts.users_admin_list()
        self.assertEqual((total, items[0]["activo"]), (1, False))

    def test_search_matches_dni_first_last_and_both_full_name_orders_case_insensitively(self):
        self._people()
        for q, expected in (("20", ["20"]), ("brun", ["20"]), ("ALVAREZ", ["20", "10"]),
                            ("bruno alvarez", ["20"]), ("alvarez bruno", ["20"]),
                            ("itez", ["30"]), ("nadie", [])):
            items, total = db_accounts.users_admin_list(q)
            self.assertEqual([i["dni"] for i in items], expected, q)
            self.assertEqual(total, len(expected), q)

    def test_a_dni_written_with_dots_or_spaces_finds_the_stored_digits(self):
        # La cola del día muestra «30.009.042»; la mesa lo copia así en
        # «Crear turno» y no encontraba la ficha — terminaba creando un
        # duplicado (demo, 2026-10-26).
        _raw_user("30009042", first_name="Ana", last_name="Gómez")
        _raw_user("27111222", first_name="Bruno", last_name="Díaz")
        for q in ("30.009.042", "30 009 042", "30-009-042", " 30.009.042 ", "30.009"):
            items, total = db_accounts.users_admin_list(q)
            self.assertEqual([i["dni"] for i in items], ["30009042"], q)
            self.assertEqual(total, 1, q)
        # Letras con puntos siguen siendo texto: no se reducen a dígitos.
        self.assertEqual(db_accounts.users_admin_list("A. Gómez")[1], 0)

    def test_search_term_is_stripped_and_blank_means_everything(self):
        self._people()
        self.assertEqual(db_accounts.users_admin_list("  carla  ")[1], 1)
        self.assertEqual(db_accounts.users_admin_list("   ")[1], 4)
        self.assertEqual(db_accounts.users_admin_list(None)[1], 4)

    def test_percent_and_underscore_are_wildcards_in_the_search(self):
        self._people()
        self.assertEqual(db_accounts.users_admin_list("%")[1], 4)
        self.assertEqual([i["dni"] for i in db_accounts.users_admin_list("Br_no")[0]], ["20"])

    def test_total_counts_the_whole_match_not_the_page(self):
        for i in range(5):
            _raw_user(f"{i}", last_name=f"A{i}")
        items, total = db_accounts.users_admin_list(limit=2, offset=1)
        self.assertEqual([i["dni"] for i in items], ["1", "2"])
        self.assertEqual(total, 5)
        items, total = db_accounts.users_admin_list(limit=2, offset=10)
        self.assertEqual((items, total), ([], 5))

    def test_limit_is_clamped_between_one_and_200_and_zero_means_the_default(self):
        _sql("INSERT INTO users (dni, last_name) SELECT 'd' || g, 'L' || lpad(g::text, 4, '0') "
             "FROM generate_series(1, 205) g")
        self.assertEqual(len(db_accounts.users_admin_list(limit=500)[0]), 200)
        self.assertEqual(len(db_accounts.users_admin_list(limit=0)[0]), 200)
        self.assertEqual(len(db_accounts.users_admin_list(limit=None)[0]), 200)
        self.assertEqual(len(db_accounts.users_admin_list(limit=-5)[0]), 1)
        self.assertEqual(len(db_accounts.users_admin_list(limit=3)[0]), 3)
        items, total = db_accounts.users_admin_list(offset=200)
        self.assertEqual((len(items), total), (5, 205))

    def test_negative_or_missing_offset_is_zero(self):
        self._people()
        first = [i["dni"] for i in db_accounts.users_admin_list()[0]]
        self.assertEqual([i["dni"] for i in db_accounts.users_admin_list(offset=-3)[0]], first)
        self.assertEqual([i["dni"] for i in db_accounts.users_admin_list(offset=None)[0]], first)

    def test_recent_order_is_the_latest_contact_and_adds_ultimo_contacto(self):
        old = datetime(2020, 1, 1, tzinfo=UTC)
        for dni in ("A", "B", "C", "D", "E"):
            _raw_user(dni, created=old)
        _sql("UPDATE users SET updated=%s WHERE dni='C'", (datetime(2026, 3, 1, tzinfo=UTC),))
        _sql("INSERT INTO booking_requests (dni, created) VALUES ('B', %s)",
             (datetime(2026, 1, 1, tzinfo=UTC),))
        _sql("INSERT INTO booking_requests (dni, created, cancelled_at) VALUES ('D', %s, %s)",
             (old, datetime(2026, 5, 1, tzinfo=UTC)))
        _sql("INSERT INTO booking_requests (dni, created, resolved_at, attendance_updated_at, "
             "confirmation_at) VALUES ('B', %s, %s, %s, %s)",
             (old, datetime(2025, 6, 1, tzinfo=UTC), datetime(2025, 7, 1, tzinfo=UTC),
              datetime(2025, 8, 1, tzinfo=UTC)))
        items, total = db_accounts.users_admin_list(recientes=True)
        self.assertEqual(total, 5)
        self.assertEqual([i["dni"] for i in items], ["D", "C", "B", "A", "E"])
        self.assertEqual([i["ultimo_contacto"] for i in items],
                         [datetime(2026, 5, 1, tzinfo=UTC), datetime(2026, 3, 1, tzinfo=UTC),
                          datetime(2026, 1, 1, tzinfo=UTC), old, old])
        self.assertEqual(set(items[0]), {"dni", "nombre", "apellido", "nacimiento", "sexo", "contacto",
                                         "email", "activo", "created", "cobertura", "domicilio",
                                         "ultimo_contacto"})

    def test_recent_order_uses_the_other_booking_date_columns_too(self):
        old = datetime(2020, 1, 1, tzinfo=UTC)
        _raw_user("A", created=old)
        _raw_user("B", created=old)
        _sql("INSERT INTO booking_requests (dni, created, resolved_at) VALUES ('A', %s, %s)",
             (old, datetime(2026, 2, 1, tzinfo=UTC)))
        _sql("INSERT INTO booking_requests (dni, created, attendance_updated_at) VALUES ('B', %s, %s)",
             (old, datetime(2026, 4, 1, tzinfo=UTC)))
        items, _ = db_accounts.users_admin_list(recientes=True)
        self.assertEqual([i["dni"] for i in items], ["B", "A"])

    def test_recent_ties_break_by_dni_and_the_page_still_applies(self):
        for dni in ("3", "1", "2"):
            _raw_user(dni, created=datetime(2020, 1, 1, tzinfo=UTC))
        items, total = db_accounts.users_admin_list(recientes=True, limit=2, offset=1)
        self.assertEqual([i["dni"] for i in items], ["2", "3"])
        self.assertEqual(total, 3)

    def test_a_search_ignores_recientes(self):
        self._people()
        items, total = db_accounts.users_admin_list("alvarez", recientes=True)
        self.assertEqual([i["dni"] for i in items], ["20", "10"])
        self.assertNotIn("ultimo_contacto", items[0])
        self.assertEqual(total, 2)


# ---- profile writes ---------------------------------------------------------------------------

class TestUserAdminUpdate(DbCase):
    def test_only_the_given_keys_change_and_updated_is_stamped(self):
        _make("1", email="a@b.com", cobertura={"p": 1})
        before = _row("1")
        user = db_accounts.user_admin_update("1", {"nombre": "Anita", "contacto": "999"})
        after = _row("1")
        self.assertEqual((after["first_name"], after["contact"]), ("Anita", "999"))
        self.assertEqual((after["last_name"], after["sex"], after["email"], after["coverage"],
                          after["birth_date"], after["pass_hash"]),
                         (before["last_name"], before["sex"], before["email"], before["coverage"],
                          before["birth_date"], before["pass_hash"]))
        self.assertIsNone(before["updated"])
        self.assertIsNotNone(after["updated"])
        self.assertEqual(set(user), USER_KEYS)
        self.assertEqual((user["nombre"], user["contacto"]), ("Anita", "999"))
        self.assertNotIn("pass_hash", user)

    def test_every_allowed_key(self):
        _make("1")
        user = db_accounts.user_admin_update("1", {"nombre": "N", "apellido": "A", "nacimiento": "2001-02-03",
                                          "sexo": "M", "contacto": "c", "email": "e@x.com",
                                          "activo": False, "cobertura": {"prepaga": "Z", "ñ": 1}})
        row = _row("1")
        self.assertEqual((row["first_name"], row["last_name"], row["birth_date"], row["sex"],
                          row["contact"], row["email"], row["active"], row["coverage"]),
                         ("N", "A", date(2001, 2, 3), "M", "c", "e@x.com", False,
                          {"prepaga": "Z", "ñ": 1}))
        self.assertIs(user["activo"], False)
        self.assertEqual(user["cobertura"], {"prepaga": "Z", "ñ": 1})

    def test_unknown_and_forbidden_keys_are_ignored(self):
        _make("1")
        before = _row("1")
        user = db_accounts.user_admin_update("1", {"dni": "2", "pass_hash": "x", "domicilio": {"a": 1},
                                          "lang": "en", "id": 99})
        self.assertEqual(_row("1"), before)
        self.assertEqual(user["dni"], "1")

    def test_ignored_keys_alongside_a_real_one_do_not_leak(self):
        _make("1")
        db_accounts.user_admin_update("1", {"dni": "2", "nombre": "Nuevo"})
        row = _row("1")
        self.assertEqual((row["dni"], row["first_name"]), ("1", "Nuevo"))
        self.assertIsNone(_row("2"))

    def test_nothing_to_set_returns_the_user_without_stamping_updated(self):
        _make("1")
        self.assertEqual(db_accounts.user_admin_update("1", {}), db_accounts.get_user("1"))
        self.assertEqual(db_accounts.user_admin_update("1", {"dni": "x"}), db_accounts.get_user("1"))
        self.assertIsNone(_row("1")["updated"])

    def test_unknown_account_is_none_in_both_branches(self):
        self.assertIsNone(db_accounts.user_admin_update("404", {"nombre": "X"}))
        self.assertIsNone(db_accounts.user_admin_update("404", {}))

    def test_none_values_are_written_as_null(self):
        _make("1", email="a@b.com", cobertura={"p": 1})
        db_accounts.user_admin_update("1", {"email": None, "cobertura": None, "nacimiento": None})
        row = _row("1")
        self.assertEqual((row["email"], row["coverage"], row["birth_date"]), (None, None, None))

    def test_bad_date_raises_and_writes_nothing(self):
        _make("1")
        with self.assertRaises(ValueError):
            db_accounts.user_admin_update("1", {"nombre": "X", "nacimiento": "basura"})
        self.assertEqual(_row("1")["first_name"], "Ana")

    def test_works_on_an_archived_account(self):
        _make("1")
        db_accounts.archive_user("1")
        user = db_accounts.user_admin_update("1", {"activo": True})
        self.assertIs(user["activo"], True)


class TestUpdateSelfBookingCoverage(DbCase):
    def _booking(self, dni, requester=None):
        return _one("INSERT INTO booking_requests (dni, requester_dni) VALUES (%s, %s) RETURNING id",
                    (dni, requester))["id"]

    def test_writes_when_the_expected_coverage_matches_and_the_booking_is_own(self):
        _make("1", cobertura={"prepaga": "A"})
        rid = self._booking("1")
        self.assertIs(db_accounts.update_self_booking_coverage(rid, "1", {"prepaga": "A"}, {"prepaga": "B"}), True)
        self.assertEqual(_row("1")["coverage"], {"prepaga": "B"})
        self.assertIsNone(_row("1")["updated"])

    def test_stale_expectation_writes_nothing(self):
        _make("1", cobertura={"prepaga": "A"})
        rid = self._booking("1")
        self.assertIs(db_accounts.update_self_booking_coverage(rid, "1", {"prepaga": "Z"}, {"prepaga": "B"}), False)
        self.assertEqual(_row("1")["coverage"], {"prepaga": "A"})

    def test_null_expected_matches_null_coverage(self):
        _make("1")
        rid = self._booking("1")
        self.assertIs(db_accounts.update_self_booking_coverage(rid, "1", None, {"prepaga": "B"}), True)
        self.assertEqual(_row("1")["coverage"], {"prepaga": "B"})
        self.assertIs(db_accounts.update_self_booking_coverage(rid, "1", None, {"prepaga": "C"}), False)

    def test_new_coverage_none_clears_it(self):
        _make("1", cobertura={"prepaga": "A"})
        rid = self._booking("1")
        self.assertIs(db_accounts.update_self_booking_coverage(rid, "1", {"prepaga": "A"}, None), True)
        self.assertIsNone(_row("1")["coverage"])

    def test_requester_may_be_blank_or_the_patient_but_not_somebody_else(self):
        _make("1")
        for requester, ok in ((None, True), ("", True), ("1", True), ("2", False)):
            with self.subTest(requester=requester):
                _sql("DELETE FROM booking_requests")
                _sql("UPDATE users SET coverage=NULL")
                rid = self._booking("1", requester)
                self.assertIs(db_accounts.update_self_booking_coverage(rid, "1", None, {"p": 1}), ok)
                self.assertEqual(_row("1")["coverage"], {"p": 1} if ok else None)

    def test_booking_of_another_patient_or_missing_booking_writes_nothing(self):
        _make("1")
        _make("2")
        other = self._booking("2")
        self.assertIs(db_accounts.update_self_booking_coverage(other, "1", None, {"p": 1}), False)
        self.assertIs(db_accounts.update_self_booking_coverage(99999, "1", None, {"p": 1}), False)
        self.assertIsNone(_row("1")["coverage"])

    def test_unknown_account_is_false(self):
        rid = self._booking("404")
        self.assertIs(db_accounts.update_self_booking_coverage(rid, "404", None, {"p": 1}), False)


class TestUpdateUser(DbCase):
    def _full(self, dni="1", **kw):
        args = dict(nombre="Nuevo", apellido="Nombre", nacimiento="1980-01-02", sexo="M",
                    contacto="555", email="n@x.com", cobertura={"prepaga": "N"}, domicilio={"calle": "C"},
                    avisos={"email": True}, lang="en")
        args.update(kw)
        return db_accounts.update_user(dni, **args)

    def test_rewrites_the_whole_profile_and_returns_true(self):
        _make("1", email="viejo@x.com", cobertura={"prepaga": "V"}, lang="es")
        before = _row("1")
        self.assertIs(self._full(), True)
        row = _row("1")
        self.assertEqual((row["first_name"], row["last_name"], row["birth_date"], row["sex"],
                          row["contact"], row["email"], row["coverage"], row["address"],
                          row["notifications"], row["lang"]),
                         ("Nuevo", "Nombre", date(1980, 1, 2), "M", "555", "n@x.com",
                          {"prepaga": "N"}, {"calle": "C"}, {"email": True}, "en"))
        for untouched in ("id", "dni", "pass_hash", "created", "active", "identity_kind", "updated"):
            self.assertEqual(row[untouched], before[untouched], untouched)

    def test_omitted_arguments_overwrite_with_null(self):
        _make("1", email="a@b.com", cobertura={"p": 1}, domicilio={"c": 1}, lang="es")
        self.assertIs(db_accounts.update_user("1"), True)
        row = _row("1")
        for column in ("first_name", "last_name", "birth_date", "sex", "contact", "email", "coverage",
                       "address", "notifications", "lang"):
            self.assertIsNone(row[column], column)

    def test_coverage_write_false_keeps_the_stored_coverage(self):
        _make("1", cobertura={"prepaga": "V"})
        self.assertIs(self._full(coverage_write=False, cobertura={"prepaga": "IGNORADA"}), True)
        row = _row("1")
        self.assertEqual(row["coverage"], {"prepaga": "V"})
        self.assertEqual(row["first_name"], "Nuevo")

    def test_guard_with_matching_expectation_writes(self):
        _make("1", cobertura={"prepaga": "V"})
        self.assertIs(self._full(coverage_guard=True, coverage_expected={"prepaga": "V"}), True)
        self.assertEqual(_row("1")["coverage"], {"prepaga": "N"})

    def test_guard_with_stale_expectation_writes_nothing(self):
        _make("1", cobertura={"prepaga": "V"})
        before = _row("1")
        self.assertIs(self._full(coverage_guard=True, coverage_expected={"prepaga": "OTRA"}), False)
        self.assertEqual(_row("1"), before)

    def test_guard_with_null_expectation_matches_null_coverage(self):
        _make("1")
        self.assertIs(self._full(coverage_guard=True, coverage_expected=None), True)
        _make("2", cobertura={"p": 1})
        self.assertIs(self._full("2", coverage_guard=True, coverage_expected=None), False)

    def test_guard_off_ignores_the_expectation(self):
        _make("1", cobertura={"prepaga": "V"})
        self.assertIs(self._full(coverage_guard=False, coverage_expected={"x": 1}), True)

    def test_unknown_account_is_false(self):
        self.assertIs(self._full("404"), False)

    def test_bad_date_raises_and_writes_nothing(self):
        _make("1")
        with self.assertRaises(ValueError):
            self._full(nacimiento="mañana")
        self.assertEqual(_row("1")["first_name"], "Ana")


class TestArchiveUser(DbCase):
    def _waitlist(self, dni, active=True):
        return _one("INSERT INTO waitlist (dni, active) VALUES (%s, %s) RETURNING id", (dni, active))["id"]

    def _offer(self, waitlist_id, token, status="ofrecida", expires="now() + interval '1 hour'"):
        return _one("INSERT INTO appointment_offers (waitlist_id, token, status, expires_at, slot_at, "
                    f"service_id, center_slug) VALUES (%s, %s, %s, {expires}, now() + interval '2 day', "
                    "%s, 'c') RETURNING id", (waitlist_id, token, status, token))["id"]

    def test_deactivates_stamps_updated_and_returns_the_public_user(self):
        _make("1", password="clave-larga-1")
        before = _row("1")
        user = db_accounts.archive_user("1")
        row = _row("1")
        self.assertIs(row["active"], False)
        self.assertIsNotNone(row["updated"])
        self.assertEqual(row["pass_hash"], before["pass_hash"])
        self.assertEqual(set(user), USER_KEYS)
        self.assertIs(user["activo"], False)
        self.assertEqual(user["dni"], "1")
        self.assertNotIn("pass_hash", user)

    def test_unknown_account_is_none_and_nothing_else_is_touched(self):
        _make("1")
        db_accounts.create_session("1")
        self.assertIsNone(db_accounts.archive_user("404"))
        self.assertEqual(_count("sessions"), 1)
        self.assertIs(_row("1")["active"], True)

    def test_sessions_and_reset_tokens_of_that_patient_only_are_deleted(self):
        _make("1")
        _make("2")
        db_accounts.create_session("1")
        db_accounts.create_session("1")
        keep = db_accounts.create_session("2")
        db_accounts.create_reset_token("1")
        keep_reset = db_accounts.create_reset_token("2")
        db_accounts.archive_user("1")
        self.assertEqual([r["token"] for r in _sql("SELECT token FROM sessions")], [keep])
        self.assertEqual([r["token"] for r in _sql("SELECT token FROM reset_tokens")], [db_accounts._reset_token_digest(keep_reset)])

    def test_account_stops_resolving_through_sessions_calendar_and_mcp(self):
        _make("1")
        token = db_accounts.create_session("1")
        feed, mcp = db_accounts.feed_token_issue("1")["token"], db_accounts.mcp_token_issue("1")["token"]
        db_accounts.archive_user("1")
        self.assertIsNone(db_accounts.get_session(token))
        self.assertIsNone(db_accounts.user_by_feed_token(feed))
        self.assertIsNone(db_accounts.user_by_mcp_token(mcp))
        self.assertIsNone(db_accounts.verify_password("1", "clave-segura-1"))

    def test_live_offers_expire_now_and_waitlist_entries_deactivate(self):
        _make("1")
        _make("2")
        w1, w1b, w2 = self._waitlist("1"), self._waitlist("1", active=False), self._waitlist("2")
        live = self._offer(w1, "live")
        live_on_inactive = self._offer(w1b, "inactive")
        done = self._offer(w1, "done", status="aceptada")
        lapsed = self._offer(w1, "lapsed", expires="now() - interval '1 hour'")
        others = self._offer(w2, "others")
        before = {r["id"]: r for r in _sql("SELECT * FROM appointment_offers")}
        db_accounts.archive_user("1")
        after = {r["id"]: r for r in _sql("SELECT * FROM appointment_offers")}
        now = _one("SELECT now() AS n")["n"]
        for offer_id in (live, live_on_inactive):
            self.assertLessEqual(after[offer_id]["expires_at"], now)
            self.assertEqual(after[offer_id]["status"], "ofrecida")
        for offer_id in (done, lapsed, others):
            self.assertEqual(after[offer_id], before[offer_id])
        self.assertEqual({r["id"]: r["active"] for r in _sql("SELECT id, active FROM waitlist")},
                         {w1: False, w1b: False, w2: True})

    def test_bookings_and_history_stay(self):
        _make("1")
        _sql("INSERT INTO booking_requests (dni, status) VALUES ('1', 'pendiente')")
        db_accounts.archive_user("1")
        self.assertEqual(_count("booking_requests", "dni='1' AND status='pendiente'"), 1)

    def test_archiving_twice_is_harmless(self):
        _make("1")
        db_accounts.archive_user("1")
        user = db_accounts.archive_user("1")
        self.assertIs(user["activo"], False)


# ---- change of DNI ------------------------------------------------------------------------------------

def _seed_identity(dni, tag):
    """One row under `dni` in every table/column `change_dni` moves; `tag` keeps unique keys apart."""
    _raw_user(dni)
    _sql("INSERT INTO sessions (token, dni) VALUES (%s, %s)", (f"s-{tag}", dni))
    _sql("INSERT INTO reset_tokens (token, dni, expires) VALUES (%s, %s, now() + interval '1 hour')",
         (f"r-{tag}", dni))
    _sql("INSERT INTO dependents (adult_dni, dni, first_name, last_name, birth_date, sex, relationship) "
         "VALUES (%s, %s, 'Hijo', 'X', '2015-01-01', 'M', 'hijo')", (dni, f"dep-{tag}"))
    rid = _one("INSERT INTO booking_requests (dni, requester_dni) VALUES (%s, 'tercero') RETURNING id",
               (dni,))["id"]
    _sql("INSERT INTO booking_requests (dni, requester_dni) VALUES ('paciente-ajeno', %s)", (dni,))
    _sql("INSERT INTO waitlist (dni) VALUES (%s)", (dni,))
    _sql("INSERT INTO attendance_events (request_id, dni, attendance_to, operator) "
         "VALUES (%s, %s, 'presente', 'op')", (rid, dni))
    _sql("INSERT INTO interviews (dni, session_key) VALUES (%s, %s)", (dni, f"k-{tag}"))
    _sql("INSERT INTO patient_health_profile (dni, datos) VALUES (%s, '{}')", (dni,))
    _sql("INSERT INTO patient_summaries (dni, doctor_id, resumen) VALUES (%s, %s, '{}')",
         (dni, f"doc-{tag}"))
    _sql("INSERT INTO clinical_history_records (dni, source_system, record_hash, record) "
         "VALUES (%s, 'hsi', %s, '{}')", (dni, f"h-{tag}"))
    _sql("INSERT INTO lab_report_analysis (dni, report_hash, prompt_hash) VALUES (%s, %s, 'p')",
         (dni, f"lab-{tag}"))
    _sql("INSERT INTO patient_lab_uploads (dni, uploaded_by_dni) VALUES (%s, %s)", (dni, dni))
    _sql("INSERT INTO ai_costs (operation, user_dni) VALUES ('op', %s)", (dni,))
    key = f"dni:{dni}"
    _sql("INSERT INTO chat_messages (channel, key, role, content) VALUES "
         "('web', %s, 'user', '\"a\"'), ('web', %s, 'user', '\"b\"')", (key, key + ":chat:7"))
    _sql("INSERT INTO chat_summaries (patient_key, summary, covers_until) VALUES (%s, 's', now())", (key,))
    _sql("INSERT INTO chat_compactions (patient_key, summary, covers_until) VALUES (%s, 's', now())", (key,))
    _sql("INSERT INTO chat_handoffs (code, web_key, expires) VALUES (%s, %s, now() + interval '1 hour')",
         (f"code-{tag}", key))


MOVED = (("users", "dni"), ("sessions", "dni"), ("reset_tokens", "dni"), ("dependents", "adult_dni"),
         ("booking_requests", "dni"), ("booking_requests", "requester_dni"), ("waitlist", "dni"),
         ("attendance_events", "dni"), ("interviews", "dni"), ("patient_health_profile", "dni"),
         ("patient_summaries", "dni"), ("clinical_history_records", "dni"),
         ("lab_report_analysis", "dni"), ("patient_lab_uploads", "dni"),
         ("patient_lab_uploads", "uploaded_by_dni"), ("ai_costs", "user_dni"))
KEYED = (("chat_messages", "key"), ("chat_summaries", "patient_key"),
         ("chat_compactions", "patient_key"), ("chat_handoffs", "web_key"))


def _where(dni):
    """How many rows each moved column / prefixed key holds for `dni`."""
    found = {(t, c): _count(t, f"{c}=%s", (dni,)) for t, c in MOVED}
    for t, c in KEYED:
        found[(t, c)] = _count(t, f"{c}=%s OR {c} LIKE %s", (f"dni:{dni}", f"dni:{dni}:%"))
    return found


def _snapshot():
    tables = sorted({t for t, _ in MOVED} | {t for t, _ in KEYED} | {"hsi_patients"})
    return {t: sorted(json.dumps(r, default=str, sort_keys=True) for r in _sql(f"SELECT * FROM {t}"))
            for t in tables}


class TestChangeDni(DbCase):
    def test_nothing_to_change_is_true_without_writes(self):
        _seed_identity("111", "a")
        before = _snapshot()
        for viejo, nuevo in (("", "222"), ("111", ""), (None, "222"), ("111", None), ("111", "111"),
                             (" 111 ", "111"), ("111", " 111 ")):
            self.assertIs(db_accounts.change_dni(viejo, nuevo), True, (viejo, nuevo))
        self.assertEqual(_snapshot(), before)

    def test_moves_every_column_and_key_of_the_identity(self):
        _seed_identity("111", "a")
        _seed_identity("999", "bystander")
        old_counts = _where("111")
        self.assertTrue(all(n >= 1 for n in old_counts.values()), old_counts)
        bystander = _where("999")
        self.assertIs(db_accounts.change_dni("111", "222"), True)
        self.assertEqual(_where("111"), {k: 0 for k in old_counts})
        self.assertEqual(_where("222"), old_counts)
        self.assertEqual(_where("999"), bystander)
        # the account itself and its session / reset token follow the new key
        self.assertEqual(_row("222")["first_name"], "Ana")
        self.assertIsNone(_row("111"))
        self.assertEqual(db_accounts.get_session("s-a")["dni"], "222")

    def test_values_are_stripped(self):
        _seed_identity("111", "a")
        self.assertIs(db_accounts.change_dni(" 111 ", " 222 "), True)
        self.assertIsNotNone(_row("222"))
        self.assertIsNone(_row("111"))

    def test_conversation_keys_move_by_exact_prefix_only(self):
        _seed_identity("111", "a")
        keys = ["dni:111", "dni:111:chat:7", "dni:1110", "dni:1112:chat:1", "dni:11", "x:dni:111",
                "dni:111x"]
        for key in keys:
            _sql("INSERT INTO chat_messages (channel, key, role, content) VALUES ('web', %s, 'user', '1')",
                 (key,))
        db_accounts.change_dni("111", "222")
        stored = {r["key"] for r in _sql("SELECT key FROM chat_messages")}
        self.assertEqual(stored, {"dni:222", "dni:222:chat:7", "dni:1110", "dni:1112:chat:1", "dni:11",
                                  "x:dni:111", "dni:111x"})

    def test_what_is_kept_stays_with_the_old_dni(self):
        _seed_identity("111", "a")
        _sql("INSERT INTO users (dni) VALUES ('adulto-ajeno')")
        _sql("INSERT INTO dependents (adult_dni, dni, first_name, last_name, birth_date, sex, relationship) "
             "VALUES ('adulto-ajeno', '111', 'Otro', 'X', '2015-01-01', 'M', 'hijo')")
        _sql("INSERT INTO hsi_patients (hsi_patient_id, dni, payload) VALUES ('p1', '111', '{}')")
        db_accounts.change_dni("111", "222")
        self.assertEqual(_count("dependents", "adult_dni='adulto-ajeno' AND dni='111'"), 1)
        self.assertEqual(_count("hsi_patients", "dni='111'"), 1)
        self.assertEqual(_count("dependents", "adult_dni='222' AND dni='dep-a'"), 1)

    def test_refused_when_the_new_dni_belongs_to_another_account(self):
        _seed_identity("111", "a")
        _raw_user("222")
        before = _snapshot()
        self.assertIs(db_accounts.change_dni("111", "222"), False)
        self.assertEqual(_snapshot(), before)

    def test_refused_when_both_dnis_have_a_health_profile_and_nothing_is_deduplicated(self):
        _seed_identity("111", "a")
        _sql("INSERT INTO patient_health_profile (dni, datos) VALUES ('222', '{}')")
        _sql("INSERT INTO clinical_history_records (dni, source_system, record_hash, record) "
             "VALUES ('222', 'hsi', 'h-a', '{}')")
        before = _snapshot()
        self.assertIs(db_accounts.change_dni("111", "222"), False)
        self.assertEqual(_snapshot(), before)

    def test_health_profile_only_on_one_side_is_not_a_conflict(self):
        _seed_identity("111", "a")
        _sql("DELETE FROM patient_health_profile")
        _sql("INSERT INTO patient_health_profile (dni, datos) VALUES ('222', '{\"x\": 1}')")
        self.assertIs(db_accounts.change_dni("111", "222"), True)
        self.assertEqual(_sql("SELECT dni, datos FROM patient_health_profile"),
                         [{"dni": "222", "datos": {"x": 1}}])
        _sql("DELETE FROM patient_health_profile")
        _sql("INSERT INTO patient_health_profile (dni, datos) VALUES ('333', '{}')")
        _seed_identity("444", "b")
        _sql("DELETE FROM patient_health_profile WHERE dni='444'")
        self.assertIs(db_accounts.change_dni("444", "555"), True)
        self.assertEqual(_count("patient_health_profile", "dni='333'"), 1)

    def test_refused_when_both_adults_declare_the_same_dependent(self):
        # `dependents.adult_dni` has a foreign key to `users`, so an adult row for a DNI without an
        # account exists only as legacy data; load it with the constraint triggers off.
        _seed_identity("111", "a")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SET LOCAL session_replication_role = replica")
            cur.execute("INSERT INTO dependents (adult_dni, dni, first_name, last_name, birth_date, sex, "
                        "relationship) VALUES ('222', 'dep-a', 'Hijo', 'X', '2015-01-01', 'M', 'hijo')")
        before = _snapshot()
        self.assertIs(db_accounts.change_dni("111", "222"), False)
        self.assertEqual(_snapshot(), before)

    def test_a_different_dependent_under_the_new_dni_is_fine(self):
        _seed_identity("111", "a")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SET LOCAL session_replication_role = replica")
            cur.execute("INSERT INTO dependents (adult_dni, dni, first_name, last_name, birth_date, sex, "
                        "relationship) VALUES ('222', 'otro-hijo', 'Hijo', 'X', '2015-01-01', 'M', 'hijo')")
        self.assertIs(db_accounts.change_dni("111", "222"), True)
        self.assertEqual(sorted(r["dni"] for r in _sql("SELECT dni FROM dependents WHERE adult_dni='222'")),
                         ["dep-a", "otro-hijo"])

    def test_identical_clinical_copies_under_the_old_dni_are_dropped_the_rest_move(self):
        _seed_identity("111", "a")
        _sql("INSERT INTO clinical_history_records (dni, source_system, record_hash, record) VALUES "
             "('111', 'hsi', 'same', '1'), ('111', 'otro', 'same', '2'), ('222', 'hsi', 'same', '3')")
        _sql("INSERT INTO lab_report_analysis (dni, report_hash, prompt_hash) VALUES "
             "('111', 'same', 'p1'), ('222', 'same', 'p2'), ('111', 'only-old', 'p1')")
        _sql("INSERT INTO patient_summaries (dni, doctor_id, resumen) VALUES "
             "('111', 'same', '{\"v\": \"old\"}'), ('222', 'same', '{\"v\": \"new\"}')")
        _sql("DELETE FROM patient_health_profile WHERE dni='111'")
        self.assertIs(db_accounts.change_dni("111", "222"), True)
        self.assertEqual(sorted((r["source_system"], r["record_hash"], r["record"])
                                for r in _sql("SELECT * FROM clinical_history_records WHERE dni='222'")),
                         [("hsi", "h-a", {}), ("hsi", "same", 3), ("otro", "same", 2)])
        self.assertEqual(sorted((r["report_hash"], r["prompt_hash"])
                                for r in _sql("SELECT * FROM lab_report_analysis WHERE dni='222'")),
                         [("lab-a", "p"), ("only-old", "p1"), ("same", "p2")])
        self.assertEqual(sorted((r["doctor_id"], r["resumen"])
                                for r in _sql("SELECT * FROM patient_summaries WHERE dni='222'")),
                         [("doc-a", {}), ("same", {"v": "new"})])
        self.assertEqual(_count("clinical_history_records", "dni='111'"), 0)
        self.assertEqual(_count("lab_report_analysis", "dni='111'"), 0)
        self.assertEqual(_count("patient_summaries", "dni='111'"), 0)

    def test_the_new_dnis_compaction_wins_over_the_old_one(self):
        _seed_identity("111", "a")
        _sql("UPDATE chat_compactions SET summary='del viejo' WHERE patient_key='dni:111'")
        _sql("INSERT INTO chat_compactions (patient_key, summary, covers_until) "
             "VALUES ('dni:222', 'del nuevo', now())")
        db_accounts.change_dni("111", "222")
        self.assertEqual(_sql("SELECT patient_key, summary FROM chat_compactions"),
                         [{"patient_key": "dni:222", "summary": "del nuevo"}])

    def test_the_old_compaction_moves_when_the_new_dni_has_none(self):
        _seed_identity("111", "a")
        db_accounts.change_dni("111", "222")
        self.assertEqual(_sql("SELECT patient_key, summary FROM chat_compactions"),
                         [{"patient_key": "dni:222", "summary": "s"}])

    def test_moving_an_identity_without_an_account_row_still_moves_the_rest(self):
        _sql("INSERT INTO interviews (dni, session_key) VALUES ('111', 'k')")
        self.assertIs(db_accounts.change_dni("111", "222"), True)
        self.assertEqual(_count("interviews", "dni='222'"), 1)

    def test_change_dni_in_works_inside_the_callers_transaction(self):
        _seed_identity("111", "a")
        before = _snapshot()
        try:
            with db._connect() as conn, conn.cursor() as cur:
                self.assertIs(db_accounts.change_dni_in(cur, "111", "222"), True)
                cur.execute("SELECT count(*) AS n FROM users WHERE dni='222'")
                self.assertEqual(cur.fetchone()["n"], 1)
                raise RuntimeError("caller aborts")
        except RuntimeError:
            pass
        self.assertEqual(_snapshot(), before)
        with db._connect() as conn, conn.cursor() as cur:
            self.assertIs(db_accounts.change_dni_in(cur, "111", "222"), True)
        self.assertIsNotNone(_row("222"))

    def test_change_dni_in_refusal_writes_nothing_in_the_callers_transaction(self):
        _seed_identity("111", "a")
        _raw_user("222")
        before = _snapshot()
        with db._connect() as conn, conn.cursor() as cur:
            self.assertIs(db_accounts.change_dni_in(cur, "111", "222"), False)
        self.assertEqual(_snapshot(), before)

    def test_change_dni_in_does_not_strip_or_shortcut(self):
        _seed_identity("111", "a")
        with db._connect() as conn, conn.cursor() as cur:
            self.assertIs(db_accounts.change_dni_in(cur, "111", "111"), False)  # "111" is its own new key
        self.assertIsNotNone(_row("111"))


# ---- passwords -----------------------------------------------------------------------------------------

class TestVerifyPassword(DbCase):
    def test_right_password_returns_the_public_user(self):
        _make("1", password="clave-correcta", email="a@b.com")
        user = db_accounts.verify_password("1", "clave-correcta")
        self.assertEqual(set(user) - USER_KEYS, set())
        self.assertEqual(user["dni"], "1")
        self.assertNotIn("pass_hash", user)
        self.assertNotIn("portal_user_id", user)

    def test_wrong_password_unknown_account_and_missing_hash_are_none(self):
        _make("1", password="clave-correcta")
        _raw_user("2", pass_hash=None)
        self.assertIsNone(db_accounts.verify_password("1", "otra"))
        self.assertIsNone(db_accounts.verify_password("1", ""))
        self.assertIsNone(db_accounts.verify_password("404", "clave-correcta"))
        self.assertIsNone(db_accounts.verify_password("2", "cualquiera"))

    def test_a_disabled_account_never_logs_in_even_with_the_right_password(self):
        _make("1", password="clave-correcta")
        _sql("UPDATE users SET active=false WHERE dni='1'")
        self.assertIsNone(db_accounts.verify_password("1", "clave-correcta"))

    def test_a_null_active_flag_still_logs_in(self):
        _make("1", password="clave-correcta")
        _sql("UPDATE users SET active=NULL WHERE dni='1'")
        self.assertIsNotNone(db_accounts.verify_password("1", "clave-correcta"))

    def test_missing_and_disabled_accounts_spend_the_same_work_as_a_wrong_password(self):
        _make("1", password="clave-correcta")
        _sql("UPDATE users SET active=false WHERE dni='1'")
        with patch.object(password_hashing, "dummy_verify") as dummy:
            db_accounts.verify_password("404", "x")
            db_accounts.verify_password("1", "clave-correcta")
        self.assertEqual(dummy.call_count, 2)

    def test_a_good_current_hash_is_left_alone(self):
        _make("1", password="clave-correcta")
        before = _row("1")["pass_hash"]
        db_accounts.verify_password("1", "clave-correcta")
        self.assertEqual(_row("1")["pass_hash"], before)

    def test_a_wrong_password_never_touches_the_hash(self):
        _make("1", password="clave-correcta")
        before = _row("1")["pass_hash"]
        db_accounts.verify_password("1", "mala")
        self.assertEqual(_row("1")["pass_hash"], before)

    def test_legacy_scrypt_hash_is_upgraded_to_argon2_on_the_first_good_login(self):
        legacy = _scrypt_hash("clave-historica")
        _raw_user("1", pass_hash=legacy)
        self.assertIsNone(db_accounts.verify_password("1", "equivocada"))
        self.assertEqual(_row("1")["pass_hash"], legacy)
        user = db_accounts.verify_password("1", "clave-historica")
        self.assertEqual(user["dni"], "1")
        self.assertNotIn("pass_hash", user)
        upgraded = _row("1")["pass_hash"]
        self.assertTrue(upgraded.startswith("$argon2id$"))
        self.assertNotIn("clave-historica", upgraded)
        self.assertIsNotNone(db_accounts.verify_password("1", "clave-historica"))
        self.assertEqual(_row("1")["pass_hash"], upgraded)

    def test_an_outdated_argon2_hash_is_rehashed(self):
        from argon2 import PasswordHasher
        weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash("clave-vieja")
        _raw_user("1", pass_hash=weak)
        self.assertIsNotNone(db_accounts.verify_password("1", "clave-vieja"))
        stored = _row("1")["pass_hash"]
        self.assertNotEqual(stored, weak)
        self.assertTrue(stored.startswith("$argon2id$"))
        self.assertIsNotNone(db_accounts.verify_password("1", "clave-vieja"))

    def test_a_replacement_hash_is_written_in_the_same_call_that_returns_the_user(self):
        legacy = _scrypt_hash("clave-historica")
        _raw_user("1", pass_hash=legacy)
        with patch.object(password_hashing, "verify_and_update",
                          return_value=(True, "$argon2id$sustituto")):
            user = db_accounts.verify_password("1", "lo-que-sea")
        self.assertEqual(user["dni"], "1")
        self.assertEqual(_row("1")["pass_hash"], "$argon2id$sustituto")


class TestSetPassword(DbCase):
    def test_sets_a_new_argon2_hash_and_the_old_password_stops_working(self):
        _make("1", password="clave-anterior")
        db_accounts.set_password("1", "clave-nueva-123")
        stored = _row("1")["pass_hash"]
        self.assertTrue(stored.startswith("$argon2id$"))
        self.assertNotIn("clave-nueva-123", stored)
        self.assertIsNone(db_accounts.verify_password("1", "clave-anterior"))
        self.assertIsNotNone(db_accounts.verify_password("1", "clave-nueva-123"))

    def test_closes_every_open_session_of_that_account_only(self):
        _make("1")
        _make("2")
        db_accounts.create_session("1")
        db_accounts.create_session("1")
        keep = db_accounts.create_session("2")
        db_accounts.set_password("1", "clave-nueva-123")
        self.assertEqual([r["token"] for r in _sql("SELECT token FROM sessions")], [keep])

    def test_other_accounts_keep_their_hash(self):
        _make("1")
        _make("2")
        other = _row("2")["pass_hash"]
        db_accounts.set_password("1", "clave-nueva-123")
        self.assertEqual(_row("2")["pass_hash"], other)

    def test_unknown_account_is_silent(self):
        self.assertIsNone(db_accounts.set_password("404", "clave-nueva-123"))
        self.assertEqual(_count("users"), 0)

    def test_sets_a_password_on_an_account_that_had_none(self):
        _raw_user("1", pass_hash=None)
        db_accounts.set_password("1", "clave-nueva-123")
        self.assertIsNotNone(db_accounts.verify_password("1", "clave-nueva-123"))

    def test_it_does_not_touch_the_active_flag(self):
        _make("1")
        _sql("UPDATE users SET active=false WHERE dni='1'")
        db_accounts.set_password("1", "clave-nueva-123")
        self.assertIs(_row("1")["active"], False)


# ---- reset tokens ---------------------------------------------------------------------------------------

class TestResetTokens(DbCase):
    def test_token_is_opaque_unused_and_expires_in_an_hour_by_default(self):
        _make("1")
        token = db_accounts.create_reset_token("1")
        self.assertGreaterEqual(len(token), 40)
        self.assertEqual(_count("reset_tokens", "token=%s", (token,)), 0)  # only its hash is stored
        row = _one("SELECT * FROM reset_tokens WHERE token=%s", (db_accounts._reset_token_digest(token),))
        self.assertEqual(row["dni"], "1")
        self.assertIs(row["used"], False)
        self.assertAlmostEqual(_secs_from_now("reset_tokens", "expires", "token=%s", (db_accounts._reset_token_digest(token),)), 3600, delta=30)

    def test_custom_lifetime(self):
        _make("1")
        token = db_accounts.create_reset_token("1", ttl_minutos=5)
        self.assertAlmostEqual(_secs_from_now("reset_tokens", "expires", "token=%s", (db_accounts._reset_token_digest(token),)), 300, delta=30)

    def test_each_call_makes_a_new_token_and_keeps_the_previous_ones(self):
        _make("1")
        a, b = db_accounts.create_reset_token("1"), db_accounts.create_reset_token("1")
        self.assertNotEqual(a, b)
        self.assertEqual(_count("reset_tokens", "dni='1'"), 2)

    def test_unknown_account_is_refused_by_the_database(self):
        with self.assertRaises(pgerr.ForeignKeyViolation):
            db_accounts.create_reset_token("404")
        self.assertEqual(_count("reset_tokens"), 0)

    def test_consume_returns_the_dni_once(self):
        _make("1")
        token = db_accounts.create_reset_token("1")
        self.assertEqual(db_accounts.consume_reset_token(token), "1")
        self.assertIs(_one("SELECT used FROM reset_tokens WHERE token=%s", (db_accounts._reset_token_digest(token),))["used"], True)
        self.assertIsNone(db_accounts.consume_reset_token(token))

    def test_expired_token_is_refused_and_stays_unused(self):
        _make("1")
        token = db_accounts.create_reset_token("1")
        _sql("UPDATE reset_tokens SET expires = now() - interval '1 second' WHERE token=%s", (db_accounts._reset_token_digest(token),))
        self.assertIsNone(db_accounts.consume_reset_token(token))
        self.assertIs(_one("SELECT used FROM reset_tokens WHERE token=%s", (db_accounts._reset_token_digest(token),))["used"], False)

    def test_unknown_blank_and_none_tokens_are_refused(self):
        _make("1")
        db_accounts.create_reset_token("1")
        for value in ("nope", "", None):
            self.assertIsNone(db_accounts.consume_reset_token(value))
        self.assertEqual(_count("reset_tokens", "used"), 0)

    def test_consuming_one_token_leaves_the_others_valid(self):
        _make("1")
        a, b = db_accounts.create_reset_token("1"), db_accounts.create_reset_token("1")
        self.assertEqual(db_accounts.consume_reset_token(a), "1")
        self.assertEqual(db_accounts.consume_reset_token(b), "1")

    def test_a_token_follows_its_account_through_a_dni_change(self):
        _make("1")
        token = db_accounts.create_reset_token("1")
        db_accounts.change_dni("1", "2")
        self.assertEqual(db_accounts.consume_reset_token(token), "2")


# ---- OTP ---------------------------------------------------------------------------------------------------

class TestOtpRecent(DbCase):
    def test_a_fresh_code_blocks_a_resend(self):
        db_accounts.create_otp("c1", "registro")
        self.assertIs(db_accounts.otp_recent("c1", "registro"), True)

    def test_other_contact_or_purpose_is_not_recent(self):
        db_accounts.create_otp("c1", "registro")
        self.assertIs(db_accounts.otp_recent("c2", "registro"), False)
        self.assertIs(db_accounts.otp_recent("c1", "recuperar"), False)

    def test_window_is_the_given_number_of_seconds(self):
        db_accounts.create_otp("c1", "registro")
        _sql("UPDATE otp_codes SET created = now() - interval '60 seconds'")
        self.assertIs(db_accounts.otp_recent("c1", "registro"), False)
        self.assertIs(db_accounts.otp_recent("c1", "registro", segundos=90), True)
        self.assertIs(db_accounts.otp_recent("c1", "registro", segundos=30), False)

    def test_no_codes_at_all(self):
        self.assertIs(db_accounts.otp_recent("c1", "registro"), False)

    def test_used_codes_still_count_as_recent(self):
        db_accounts.create_otp("c1", "registro")
        _sql("UPDATE otp_codes SET used=true")
        self.assertIs(db_accounts.otp_recent("c1", "registro"), True)


class TestCreateOtp(DbCase):
    def test_returns_a_six_digit_code_stored_only_as_a_hash(self):
        code = db_accounts.create_otp("2255123456", "registro")
        self.assertRegex(code, r"^\d{6}$")
        row = _one("SELECT * FROM otp_codes")
        self.assertEqual(row["code_hash"], db_accounts._hash_otp("2255123456", "registro", code))
        self.assertEqual((row["contact"], row["purpose"], row["attempts"], row["used"]),
                         ("2255123456", "registro", 0, False))
        self.assertNotIn(code, json.dumps(row, default=str).replace(row["code_hash"], ""))

    def test_the_code_is_zero_padded(self):
        with patch("secrets.randbelow", return_value=42):
            self.assertEqual(db_accounts.create_otp("c", "p"), "000042")

    def test_lifetime_defaults_to_ten_minutes(self):
        db_accounts.create_otp("c", "p")
        self.assertAlmostEqual(_secs_from_now("otp_codes", "expires", "TRUE", ()), 600, delta=30)
        db_accounts.create_otp("c2", "p", ttl_minutos=3)
        self.assertAlmostEqual(_secs_from_now("otp_codes", "expires", "contact='c2'", ()), 180, delta=30)

    def test_a_new_code_retires_the_unused_ones_of_the_same_contact_and_purpose_only(self):
        db_accounts.create_otp("c1", "registro")
        db_accounts.create_otp("c1", "recuperar")
        db_accounts.create_otp("c2", "registro")
        db_accounts.create_otp("c1", "registro")
        rows = _sql("SELECT id, contact, purpose, used FROM otp_codes ORDER BY id")
        self.assertEqual([(r["contact"], r["purpose"], r["used"]) for r in rows],
                         [("c1", "registro", True), ("c1", "recuperar", False),
                          ("c2", "registro", False), ("c1", "registro", False)])

    def test_the_retired_code_no_longer_verifies(self):
        with patch("secrets.randbelow", return_value=111111):
            db_accounts.create_otp("c", "p")
        with patch("secrets.randbelow", return_value=222222):
            db_accounts.create_otp("c", "p")
        self.assertIs(db_accounts.verify_otp("c", "p", "111111"), False)
        self.assertIs(db_accounts.verify_otp("c", "p", "222222"), True)


class TestVerifyOtp(DbCase):
    def _code(self, contact="c", purpose="p", number=123456):
        with patch("secrets.randbelow", return_value=number):
            return db_accounts.create_otp(contact, purpose)

    def _otp(self):
        return _one("SELECT * FROM otp_codes ORDER BY id DESC LIMIT 1")

    def test_right_code_marks_it_used_counts_the_attempt_and_issues_a_ticket(self):
        code = self._code()
        self.assertIs(db_accounts.verify_otp("c", "p", code), True)
        row = self._otp()
        self.assertEqual((row["used"], row["attempts"]), (True, 1))
        ticket = _one("SELECT * FROM otp_verified")
        self.assertEqual((ticket["contact"], ticket["purpose"]), ("c", "p"))
        self.assertAlmostEqual(_secs_from_now("otp_verified", "verified_until", "TRUE", ()), 900, delta=30)

    def test_the_code_works_once(self):
        code = self._code()
        self.assertIs(db_accounts.verify_otp("c", "p", code), True)
        self.assertIs(db_accounts.verify_otp("c", "p", code), False)
        self.assertEqual(self._otp()["attempts"], 1)

    def test_wrong_code_counts_an_attempt_and_issues_nothing(self):
        self._code(number=123456)
        self.assertIs(db_accounts.verify_otp("c", "p", "654321"), False)
        row = self._otp()
        self.assertEqual((row["used"], row["attempts"]), (False, 1))
        self.assertEqual(_count("otp_verified"), 0)

    def test_after_five_wrong_attempts_even_the_right_code_is_refused(self):
        self._code(number=123456)
        for _ in range(5):
            self.assertIs(db_accounts.verify_otp("c", "p", "000000"), False)
        self.assertEqual(self._otp()["attempts"], 5)
        self.assertIs(db_accounts.verify_otp("c", "p", "123456"), False)
        row = self._otp()
        self.assertEqual((row["used"], row["attempts"]), (False, 5))
        self.assertEqual(_count("otp_verified"), 0)

    def test_concurrent_wrong_guesses_never_exceed_the_attempt_cap(self):
        # Security review 2026-11 (M-5): the cap was read, then the counter written, so parallel
        # requests all saw «< 5» and each got a guess.
        import threading
        self._code(number=123456)
        barrier = threading.Barrier(16)

        def guess():
            barrier.wait()
            db_accounts.verify_otp("c", "p", "000000")

        threads = [threading.Thread(target=guess) for _ in range(16)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(self._otp()["attempts"], 5)
        self.assertIs(db_accounts.verify_otp("c", "p", "123456"), False)

    def test_the_fifth_attempt_can_still_succeed(self):
        self._code(number=123456)
        for _ in range(4):
            db_accounts.verify_otp("c", "p", "000000")
        self.assertIs(db_accounts.verify_otp("c", "p", "123456"), True)
        self.assertEqual(self._otp()["attempts"], 5)

    def test_expired_code_is_refused_without_counting(self):
        code = self._code()
        _sql("UPDATE otp_codes SET expires = now() - interval '1 second'")
        self.assertIs(db_accounts.verify_otp("c", "p", code), False)
        self.assertEqual(self._otp()["attempts"], 0)
        self.assertEqual(_count("otp_verified"), 0)

    def test_code_for_another_contact_or_purpose_is_refused(self):
        code = self._code("c", "p")
        self.assertIs(db_accounts.verify_otp("x", "p", code), False)
        self.assertIs(db_accounts.verify_otp("c", "q", code), False)
        self.assertEqual(self._otp()["attempts"], 0)

    def test_blank_codes_are_refused_before_touching_the_database(self):
        self._code()
        for value in ("", None, "abc", "  "):
            self.assertIs(db_accounts.verify_otp("c", "p", value), False)
        self.assertEqual(self._otp()["attempts"], 0)

    def test_separators_and_spaces_in_the_typed_code_are_ignored(self):
        self._code(number=123456)
        self.assertIs(db_accounts.verify_otp("c", "p", " 123-456 "), True)

    def test_an_integer_code_is_accepted(self):
        self._code(number=123456)
        self.assertIs(db_accounts.verify_otp("c", "p", 123456), True)

    def test_only_the_most_recent_unused_code_is_considered(self):
        old = db_accounts._hash_otp("c", "p", "111111")
        new = db_accounts._hash_otp("c", "p", "222222")
        _sql("INSERT INTO otp_codes (contact, purpose, code_hash, expires, created) VALUES "
             "('c', 'p', %s, now() + interval '5 minutes', now() - interval '2 minutes'), "
             "('c', 'p', %s, now() + interval '5 minutes', now() - interval '1 minute')", (old, new))
        self.assertIs(db_accounts.verify_otp("c", "p", "111111"), False)
        self.assertIs(db_accounts.verify_otp("c", "p", "222222"), True)

    def test_a_used_code_is_skipped_for_an_older_valid_one(self):
        old = db_accounts._hash_otp("c", "p", "111111")
        _sql("INSERT INTO otp_codes (contact, purpose, code_hash, expires, created, used) VALUES "
             "('c', 'p', %s, now() + interval '5 minutes', now() - interval '2 minutes', false), "
             "('c', 'p', 'x', now() + interval '5 minutes', now() - interval '1 minute', true)", (old,))
        self.assertIs(db_accounts.verify_otp("c", "p", "111111"), True)

    def test_a_second_verification_refreshes_the_single_ticket(self):
        self.assertIs(db_accounts.verify_otp("c", "p", self._code()), True)
        _sql("UPDATE otp_verified SET verified_until = now() + interval '1 minute'")
        self.assertIs(db_accounts.verify_otp("c", "p", self._code(number=654321)), True)
        self.assertEqual(_count("otp_verified"), 1)
        self.assertAlmostEqual(_secs_from_now("otp_verified", "verified_until", "TRUE", ()), 900, delta=30)


class TestConsumeVerifiedOtp(DbCase):
    def _ticket(self, contact="c", purpose="p", interval="15 minutes"):
        _sql(f"INSERT INTO otp_verified (contact, purpose, verified_until) "
             f"VALUES (%s, %s, now() + interval '{interval}')", (contact, purpose))

    def test_valid_ticket_is_consumed_exactly_once(self):
        self._ticket()
        self.assertIs(db_accounts.consume_verified_otp("c", "p"), True)
        self.assertEqual(_count("otp_verified"), 0)
        self.assertIs(db_accounts.consume_verified_otp("c", "p"), False)

    def test_expired_ticket_is_refused_and_not_deleted(self):
        self._ticket(interval="-1 second")
        self.assertIs(db_accounts.consume_verified_otp("c", "p"), False)
        self.assertEqual(_count("otp_verified"), 1)

    def test_other_contact_or_purpose_does_not_match(self):
        self._ticket()
        self.assertIs(db_accounts.consume_verified_otp("x", "p"), False)
        self.assertIs(db_accounts.consume_verified_otp("c", "q"), False)
        self.assertEqual(_count("otp_verified"), 1)

    def test_blank_contact_is_refused_without_touching_anything(self):
        self._ticket("", "p")
        self.assertIs(db_accounts.consume_verified_otp("", "p"), False)
        self.assertIs(db_accounts.consume_verified_otp(None, "p"), False)
        self.assertEqual(_count("otp_verified"), 1)

    def test_only_the_matching_ticket_is_deleted(self):
        self._ticket("c", "p")
        self._ticket("c", "q")
        db_accounts.consume_verified_otp("c", "p")
        self.assertEqual([r["purpose"] for r in _sql("SELECT purpose FROM otp_verified")], ["q"])


# ---- portal and national identity -----------------------------------------------------------------------------

class TestUserClaimPortal(DbCase):
    def test_invalid_input_returns_none_and_writes_nothing(self):
        for portal, dni in (("", "123"), (None, "123"), ("p1", ""), ("p1", None), ("p1", "12a"),
                            ("p1", " 123"), ("p1", "12.3")):
            self.assertIsNone(db_accounts.user_claim_portal(portal, dni), (portal, dni))
        self.assertEqual(_count("users"), 0)

    def test_creates_an_active_account_without_a_password(self):
        user = db_accounts.user_claim_portal("p1", "30111222", "  ana@ejemplo.com ")
        self.assertEqual(set(user), USER_KEYS)
        self.assertEqual((user["dni"], user["email"], user["activo"]), ("30111222", "ana@ejemplo.com", True))
        self.assertNotIn("portal_user_id", user)
        row = _row("30111222")
        self.assertEqual((row["portal_user_id"], row["pass_hash"], row["identity_kind"]), ("p1", None, "dni"))
        self.assertEqual(db_accounts.user_by_portal_id("p1")["dni"], "30111222")

    def test_blank_email_is_stored_as_null_and_ids_are_coerced_to_text(self):
        user = db_accounts.user_claim_portal(77, 30111222, "   ")
        self.assertIsNone(user["email"])
        self.assertEqual(_row("30111222")["portal_user_id"], "77")

    def test_binds_an_existing_unbound_account_keeping_its_data(self):
        uid = _make("30111222", email="guardado@ejemplo.com", cobertura={"p": 1})
        user = db_accounts.user_claim_portal("p1", "30111222", "portal@ejemplo.com")
        self.assertEqual(user["id"], uid)
        self.assertEqual(user["email"], "guardado@ejemplo.com")
        self.assertEqual(user["cobertura"], {"p": 1})
        self.assertEqual(_row("30111222")["portal_user_id"], "p1")
        self.assertTrue(_row("30111222")["pass_hash"].startswith("$argon2id$"))

    def test_the_portal_email_fills_an_empty_one_only(self):
        _raw_user("1", email=None)
        _raw_user("2", email="   ")
        _raw_user("3", email="")
        for n in ("1", "2", "3"):
            self.assertEqual(db_accounts.user_claim_portal(f"p{n}", n, "portal@ejemplo.com")["email"],
                             "portal@ejemplo.com", n)

    def test_no_portal_email_leaves_an_empty_one_empty(self):
        _raw_user("1", email="   ")
        user = db_accounts.user_claim_portal("p1", "1")
        self.assertIsNone(user["email"])
        _raw_user("2", email=None)
        self.assertIsNone(db_accounts.user_claim_portal("p2", "2", None)["email"])

    def test_disabled_account_is_refused_and_untouched(self):
        _raw_user("1", active=False)
        before = _row("1")
        self.assertIsNone(db_accounts.user_claim_portal("p1", "1", "x@y.com"))
        self.assertEqual(_row("1"), before)

    def test_account_with_a_null_active_flag_can_be_claimed(self):
        _raw_user("1", active=None)
        self.assertEqual(db_accounts.user_claim_portal("p1", "1")["dni"], "1")

    def test_account_already_bound_is_refused_even_to_the_same_portal_id(self):
        _raw_user("1", portal_user_id="other")
        before = _row("1")
        self.assertIsNone(db_accounts.user_claim_portal("p1", "1", "x@y.com"))
        self.assertIsNone(db_accounts.user_claim_portal("other", "1"))
        self.assertEqual(_row("1"), before)

    def test_a_portal_id_already_bound_elsewhere_loses_cleanly_on_a_new_dni(self):
        _raw_user("1", portal_user_id="p1")
        self.assertIsNone(db_accounts.user_claim_portal("p1", "2"))
        self.assertIsNone(_row("2"))
        self.assertEqual(_count("users"), 1)

    def test_a_portal_id_already_bound_elsewhere_loses_cleanly_on_an_existing_dni(self):
        _raw_user("1", portal_user_id="p1")
        _raw_user("2", email=None)
        before = _row("2")
        self.assertIsNone(db_accounts.user_claim_portal("p1", "2", "x@y.com"))
        self.assertEqual(_row("2"), before)

    def test_the_name_the_portal_asserts_is_stored_on_a_new_account(self):
        user = db_accounts.user_claim_portal("p1", "30111222", "ana@ejemplo.com", "Ana", "  Pérez ")
        self.assertEqual((user["nombre"], user["apellido"]), ("Ana", "Pérez"))
        self.assertEqual((_row("30111222")["first_name"], _row("30111222")["last_name"]), ("Ana", "Pérez"))

    def test_no_name_asserted_stores_null_not_an_empty_string(self):
        db_accounts.user_claim_portal("p1", "30111222", "ana@ejemplo.com", "  ", "")
        row = _row("30111222")
        self.assertEqual((row["first_name"], row["last_name"]), (None, None))

    def test_the_portal_name_fills_empty_name_fields_when_binding_but_never_overwrites(self):
        _raw_user("1", first_name=None, last_name="   ")
        _raw_user("2", first_name="Guardado", last_name="Propio")
        user = db_accounts.user_claim_portal("p1", "1", None, "Portal", "Nuevo")
        self.assertEqual((user["nombre"], user["apellido"]), ("Portal", "Nuevo"))
        user = db_accounts.user_claim_portal("p2", "2", None, "Portal", "Nuevo")
        self.assertEqual((user["nombre"], user["apellido"]), ("Guardado", "Propio"))


class TestUserFillName(DbCase):
    def test_fills_only_the_empty_name_fields(self):
        _raw_user("1", first_name=None, last_name="Propio")
        user = db_accounts.user_fill_name("1", "Portal", "Otro")
        self.assertEqual((user["nombre"], user["apellido"]), ("Portal", "Propio"))
        self.assertEqual((_row("1")["first_name"], _row("1")["last_name"]), ("Portal", "Propio"))

    def test_an_account_with_both_names_is_left_untouched_and_returns_none(self):
        _raw_user("1")
        before = _row("1")
        self.assertIsNone(db_accounts.user_fill_name("1", "Portal", "Otro"))
        self.assertEqual(_row("1"), before)

    def test_blank_values_unknown_dni_and_a_value_for_a_set_field_do_nothing(self):
        _raw_user("1", first_name=" ", last_name="Propio")
        before = _row("1")
        for dni, nombre, apellido in (("1", None, None), ("1", "  ", ""), ("", "Portal", None),
                                      ("999", "Portal", None), ("1", None, "Otro")):
            self.assertIsNone(db_accounts.user_fill_name(dni, nombre, apellido), (dni, nombre, apellido))
        self.assertEqual(_row("1"), before)


class TestLoginRenaper(DbCase):
    DATOS = {"nombre": "Ana", "apellido": "Pérez", "nacimiento": "1990-05-17", "sexo": "F",
             "email": "ana@ejemplo.com"}

    def test_creates_the_account_without_a_password_and_keeps_the_claims(self):
        uid = db_accounts.login_renaper("30111222", self.DATOS, {"sub": "abc", "ñ": 1})
        self.assertIsInstance(uid, int)
        row = _row("30111222")
        self.assertEqual(row["id"], uid)
        self.assertEqual((row["first_name"], row["last_name"], row["birth_date"], row["sex"],
                          row["email"], row["pass_hash"]),
                         ("Ana", "Pérez", date(1990, 5, 17), "F", "ana@ejemplo.com", None))
        self.assertEqual(row["renaper"]["claims"], {"sub": "abc", "ñ": 1})
        stamp = datetime.fromisoformat(row["renaper"]["last_login"])
        self.assertLess(abs((datetime.now(UTC) - stamp).total_seconds()), 60)
        self.assertEqual(set(row["renaper"]), {"claims", "last_login"})
        self.assertIs(row["active"], True)

    def test_missing_identity_keys_are_null(self):
        db_accounts.login_renaper("1", {}, {})
        row = _row("1")
        self.assertEqual((row["first_name"], row["last_name"], row["birth_date"], row["sex"], row["email"]),
                         (None, None, None, None, None))
        self.assertEqual(row["renaper"]["claims"], {})

    def test_an_existing_account_keeps_what_the_person_curated_and_fills_only_gaps(self):
        uid = _raw_user("1", first_name="Curada", last_name=None, birth_date=None, sex="X", email=None,
                        pass_hash="hash-intacto")
        again = db_accounts.login_renaper("1", self.DATOS, {"sub": "nuevo"})
        self.assertEqual(again, uid)
        row = _row("1")
        self.assertEqual((row["first_name"], row["last_name"], row["birth_date"], row["sex"], row["email"]),
                         ("Curada", "Pérez", date(1990, 5, 17), "X", "ana@ejemplo.com"))
        self.assertEqual(row["pass_hash"], "hash-intacto")
        self.assertEqual(row["renaper"]["claims"], {"sub": "nuevo"})

    def test_empty_strings_count_as_curated_values_and_are_not_overwritten(self):
        _raw_user("1", first_name="", email="")
        db_accounts.login_renaper("1", self.DATOS, {})
        row = _row("1")
        self.assertEqual((row["first_name"], row["email"]), ("", ""))

    def test_claims_and_last_login_are_replaced_on_every_login(self):
        db_accounts.login_renaper("1", self.DATOS, {"sub": "uno"})
        first = _row("1")["renaper"]
        db_accounts.login_renaper("1", self.DATOS, {"sub": "dos"})
        second = _row("1")["renaper"]
        self.assertEqual(second["claims"], {"sub": "dos"})
        self.assertGreaterEqual(second["last_login"], first["last_login"])
        self.assertEqual(_count("users"), 1)

    def test_a_disabled_account_is_not_reactivated(self):
        _raw_user("1", active=False)
        db_accounts.login_renaper("1", self.DATOS, {})
        self.assertIs(_row("1")["active"], False)

    def test_a_bad_birth_date_raises_and_writes_nothing(self):
        with self.assertRaises(ValueError):
            db_accounts.login_renaper("1", {"nacimiento": "x"}, {})
        self.assertEqual(_count("users"), 0)


# ---- sessions ---------------------------------------------------------------------------------------------------

class TestSessions(DbCase):
    def test_create_session_stores_an_opaque_token_for_the_dni(self):
        _make("1")
        token = db_accounts.create_session("1")
        self.assertGreaterEqual(len(token), 40)
        row = _one("SELECT * FROM sessions")
        self.assertEqual((row["token"], row["dni"]), (token, "1"))
        self.assertIsNotNone(row["created"])
        self.assertIsNotNone(row["last_seen"])
        self.assertNotEqual(db_accounts.create_session("1"), token)
        self.assertEqual(_count("sessions"), 2)

    def test_create_session_for_an_unknown_account_is_refused_by_the_database(self):
        with self.assertRaises(pgerr.ForeignKeyViolation):
            db_accounts.create_session("404")
        self.assertEqual(_count("sessions"), 0)

    def test_get_session_resolves_the_public_user(self):
        _make("1", email="a@b.com")
        token = db_accounts.create_session("1")
        session = db_accounts.get_session(token)
        self.assertEqual(set(session), {"dni", "usuario"})
        self.assertEqual(session["dni"], "1")
        self.assertEqual(session["usuario"], db_accounts.get_user("1"))
        self.assertNotIn("pass_hash", session["usuario"])

    def test_get_session_refreshes_last_seen(self):
        _make("1")
        token = db_accounts.create_session("1")
        _sql("UPDATE sessions SET last_seen = now() - interval '5 days' WHERE token=%s", (token,))
        db_accounts.get_session(token)
        age = -_secs_from_now("sessions", "last_seen", "token=%s", (token,))
        self.assertLess(age, 60)

    def test_blank_unknown_tokens_resolve_to_nothing(self):
        _make("1")
        db_accounts.create_session("1")
        for value in (None, "", "nope"):
            self.assertIsNone(db_accounts.get_session(value))

    def test_an_archived_account_session_does_not_resolve(self):
        _make("1")
        token = db_accounts.create_session("1")
        _sql("UPDATE users SET active=false WHERE dni='1'")
        self.assertIsNone(db_accounts.get_session(token))
        self.assertEqual(_count("sessions"), 1)

    def test_a_null_active_flag_still_resolves(self):
        _make("1")
        token = db_accounts.create_session("1")
        _sql("UPDATE users SET active=NULL WHERE dni='1'")
        self.assertEqual(db_accounts.get_session(token)["dni"], "1")

    def test_a_session_without_an_account_row_does_not_resolve(self):
        # Foreign keys forbid this state today; legacy rows predate them.
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SET LOCAL session_replication_role = replica")
            cur.execute("INSERT INTO sessions (token, dni) VALUES ('huerfana', '404')")
        self.assertIsNone(db_accounts.get_session("huerfana"))

    def test_delete_session_removes_only_that_token(self):
        _make("1")
        a, b = db_accounts.create_session("1"), db_accounts.create_session("1")
        self.assertIsNone(db_accounts.delete_session(a))
        self.assertEqual([r["token"] for r in _sql("SELECT token FROM sessions")], [b])
        self.assertIsNone(db_accounts.get_session(a))

    def test_delete_session_is_silent_for_unknown_or_blank_tokens(self):
        _make("1")
        db_accounts.create_session("1")
        for value in (None, "", "nope"):
            self.assertIsNone(db_accounts.delete_session(value))
        self.assertEqual(_count("sessions"), 1)


class TestPurgeIdleSessions(DbCase):
    def _sessions(self, days_idle_list):
        _make("1")
        for n, days in enumerate(days_idle_list):
            _sql("INSERT INTO sessions (token, dni, last_seen) VALUES (%s, '1', now() - %s * interval '1 day')",
                 (f"t{n}", days))

    def test_deletes_only_sessions_idle_longer_than_the_window(self):
        self._sessions([1, 29, 31, 90])
        self.assertEqual(db_accounts.purge_idle_sessions(), 2)
        self.assertEqual(sorted(r["token"] for r in _sql("SELECT token FROM sessions")), ["t0", "t1"])

    def test_window_is_configurable_and_accepts_text(self):
        self._sessions([1, 5, 10])
        self.assertEqual(db_accounts.purge_idle_sessions(idle_days="7"), 1)
        self.assertEqual(db_accounts.purge_idle_sessions(idle_days=0), 2)
        self.assertEqual(_count("sessions"), 0)

    def test_limit_caps_one_pass(self):
        self._sessions([40, 41, 42, 43, 44])
        self.assertEqual(db_accounts.purge_idle_sessions(limit=2), 2)
        self.assertEqual(_count("sessions"), 3)
        self.assertEqual(db_accounts.purge_idle_sessions(limit="10"), 3)
        self.assertEqual(_count("sessions"), 0)

    def test_nothing_idle_deletes_nothing(self):
        self._sessions([0, 2])
        self.assertEqual(db_accounts.purge_idle_sessions(), 0)
        self.assertEqual(_count("sessions"), 2)

    def test_empty_table(self):
        self.assertEqual(db_accounts.purge_idle_sessions(), 0)

    def test_a_session_in_use_is_kept_alive_by_get_session(self):
        self._sessions([100])
        db_accounts.get_session("t0")
        self.assertEqual(db_accounts.purge_idle_sessions(), 0)


# ---- calendar-feed and MCP tokens -----------------------------------------------------------------------------------

class TestFeedToken(DbCase):
    @staticmethod
    def _digest(token):
        # The stored form, written independently of db_accounts: the SHA-256 hex
        # of the UTF-8 token, the same expression alembic 0039 applies in SQL.
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def test_the_database_never_holds_the_raw_token(self):
        # A copy of the database (a dump, a backup) must not hand out working calendar addresses.
        _make("1")
        issued = db_accounts.feed_token_issue("1")["token"]
        self.assertTrue(issued)
        self.assertNotIn(issued, json.dumps(_row("1"), default=str))
        _make("2")
        token = db_accounts.feed_token_regenerate("2")
        self.assertTrue(token)
        self.assertNotIn(token, json.dumps(_row("2"), default=str))

    def test_the_row_keeps_the_hash_and_the_last_four_characters(self):
        _make("1")
        token = db_accounts.feed_token_regenerate("1")
        self.assertRegex(token, r"^[A-Za-z0-9_-]{22,}$")
        row = _row("1")
        self.assertEqual(row["feed_token"], self._digest(token))
        self.assertEqual(row["feed_token_hint"], token[-4:])

    def test_the_stored_hash_is_not_a_working_token(self):
        _make("1")
        token = db_accounts.feed_token_issue("1")["token"]
        self.assertIsNone(db_accounts.user_by_feed_token(_row("1")["feed_token"]))
        self.assertEqual(db_accounts.user_by_feed_token(token)["dni"], "1")

    def test_issue_shows_the_token_once_then_only_its_hint(self):
        _make("1")
        first = db_accounts.feed_token_issue("1")
        self.assertTrue(first["token"])
        self.assertEqual(first["hint"], first["token"][-4:])
        again = db_accounts.feed_token_issue("1")
        self.assertEqual(again, {"token": None, "hint": first["hint"]})
        self.assertEqual(_row("1")["feed_token"], self._digest(first["token"]))
        self.assertEqual(db_accounts.user_by_feed_token(first["token"])["dni"], "1")

    def test_unknown_account_is_none_and_creates_nothing(self):
        self.assertIsNone(db_accounts.feed_token_issue("404"))
        self.assertIsNone(db_accounts.feed_token_regenerate("404"))
        self.assertEqual(_count("users"), 0)

    def test_a_hashed_token_keeps_working_and_an_empty_one_is_replaced(self):
        # A calendar subscribed before alembic 0039 hashed its token keeps updating.
        _raw_user("1", feed_token=self._digest("suscripto-antes-de-0039"), feed_token_hint="0039")
        _raw_user("2", feed_token="")
        self.assertEqual(db_accounts.feed_token_issue("1"), {"token": None, "hint": "0039"})
        self.assertEqual(db_accounts.user_by_feed_token("suscripto-antes-de-0039")["dni"], "1")
        self.assertTrue(db_accounts.feed_token_issue("2")["token"])

    def test_a_token_still_in_the_clear_is_hashed_in_place_on_open(self):
        # Written by the previous build while both served a deploy, after 0039 ran
        # (its rotation leaves the old hint behind): the address it handed out keeps
        # working, and the database stops holding it in the clear.
        raw = "escritoPorElBuildAnterior"
        _raw_user("1", feed_token=raw, feed_token_hint="0039")
        self.assertEqual(db_accounts.feed_token_issue("1"), {"token": None, "hint": raw[-4:]})
        self.assertEqual(_row("1")["feed_token"], self._digest(raw))
        self.assertEqual(db_accounts.user_by_feed_token(raw)["dni"], "1")

    def test_a_token_still_in_the_clear_is_hashed_on_the_first_poll(self):
        raw = "escritoPorElBuildAnterior"
        _raw_user("1", feed_token=raw)
        self.assertEqual(db_accounts.user_by_feed_token(raw)["dni"], "1")
        self.assertEqual((_row("1")["feed_token"], _row("1")["feed_token_hint"]), (self._digest(raw), raw[-4:]))
        self.assertEqual(db_accounts.feed_token_issue("1"), {"token": None, "hint": raw[-4:]})

    def test_a_leaked_hash_is_never_an_address(self):
        _make("1")
        db_accounts.feed_token_issue("1")
        stored = _row("1")["feed_token"]
        self.assertIsNone(db_accounts.user_by_feed_token(stored))
        self.assertEqual(_row("1")["feed_token"], stored)

    def test_accounts_get_different_tokens(self):
        _make("1")
        _make("2")
        self.assertNotEqual(db_accounts.feed_token_issue("1")["token"], db_accounts.feed_token_issue("2")["token"])

    def test_regenerate_rotates_and_revokes_the_old_address(self):
        _make("1")
        old = db_accounts.feed_token_issue("1")["token"]
        new = db_accounts.feed_token_regenerate("1")
        self.assertNotEqual(new, old)
        self.assertEqual(_row("1")["feed_token"], self._digest(new))
        self.assertEqual(db_accounts.feed_token_issue("1"), {"token": None, "hint": new[-4:]})
        self.assertIsNone(db_accounts.user_by_feed_token(old))
        self.assertEqual(db_accounts.user_by_feed_token(new)["dni"], "1")

    def test_regenerate_touches_only_that_account(self):
        _make("1")
        _make("2")
        other = db_accounts.feed_token_issue("2")["token"]
        db_accounts.feed_token_regenerate("1")
        self.assertEqual(db_accounts.user_by_feed_token(other)["dni"], "2")

    def test_user_by_feed_token_public_shape(self):
        _make("1")
        user = db_accounts.user_by_feed_token(db_accounts.feed_token_issue("1")["token"])
        self.assertEqual(set(user), USER_KEYS)
        self.assertEqual(user["dni"], "1")
        for secret in ("pass_hash", "feed_token", "feed_token_hint"):
            self.assertNotIn(secret, user)

    def test_no_public_user_dict_carries_the_feed_token(self):
        _make("1")
        db_accounts.feed_token_issue("1")
        user = db_accounts.get_user("1")
        self.assertNotIn("feed_token", user)
        self.assertNotIn("feed_token_hint", user)

    def test_unknown_blank_or_missing_tokens_resolve_to_nothing(self):
        _make("1")
        db_accounts.feed_token_issue("1")
        for value in ("nope", "", None):
            self.assertIsNone(db_accounts.user_by_feed_token(value))

    def test_archived_accounts_do_not_resolve_but_null_active_does(self):
        _make("1")
        token = db_accounts.feed_token_issue("1")["token"]
        _sql("UPDATE users SET active=false WHERE dni='1'")
        self.assertIsNone(db_accounts.user_by_feed_token(token))
        _sql("UPDATE users SET active=NULL WHERE dni='1'")
        self.assertEqual(db_accounts.user_by_feed_token(token)["dni"], "1")

    def test_an_archived_account_can_still_be_asked_for_its_token(self):
        _make("1")
        _sql("UPDATE users SET active=false WHERE dni='1'")
        self.assertTrue(db_accounts.feed_token_issue("1")["token"])


class TestMcpToken(DbCase):
    @staticmethod
    def _digest(token):
        # The stored form, written independently of db_accounts: the SHA-256 hex
        # of the UTF-8 token, the same expression alembic 0038 applies in SQL.
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def test_the_database_never_holds_the_raw_token(self):
        # A copy of the database (a dump, a backup) must not hand out working MCP tokens.
        _make("1")
        token = db_accounts.mcp_token_regenerate("1")
        self.assertTrue(token)
        self.assertNotIn(token, json.dumps(_row("1"), default=str))
        _make("2")
        issued = db_accounts.mcp_token_issue("2")["token"]
        self.assertTrue(issued)
        self.assertNotIn(issued, json.dumps(_row("2"), default=str))

    def test_the_row_keeps_the_hash_and_the_last_four_characters(self):
        _make("1")
        token = db_accounts.mcp_token_regenerate("1")
        self.assertGreaterEqual(len(token), 32)
        row = _row("1")
        self.assertEqual(row["mcp_token"], self._digest(token))
        self.assertEqual(row["mcp_token_hint"], token[-4:])

    def test_the_stored_hash_is_not_a_working_token(self):
        _make("1")
        token = db_accounts.mcp_token_regenerate("1")
        self.assertIsNone(db_accounts.user_by_mcp_token(_row("1")["mcp_token"]))
        self.assertEqual(db_accounts.user_by_mcp_token(token)["dni"], "1")

    def test_issue_shows_the_token_once_then_only_its_hint(self):
        _make("1")
        first = db_accounts.mcp_token_issue("1")
        self.assertTrue(first["token"])
        self.assertEqual(first["hint"], first["token"][-4:])
        again = db_accounts.mcp_token_issue("1")
        self.assertEqual(again, {"token": None, "hint": first["hint"], "last_used": None})
        self.assertEqual(_row("1")["mcp_token"], self._digest(first["token"]))
        self.assertEqual(db_accounts.user_by_mcp_token(first["token"])["dni"], "1")

    def test_unknown_account_is_none(self):
        self.assertIsNone(db_accounts.mcp_token_issue("404"))

    def test_an_existing_token_is_kept_and_an_empty_one_replaced(self):
        # A token hashed by alembic 0038 keeps working and is not reissued.
        _raw_user("1", mcp_token=self._digest("a-mano-del-asistente"), mcp_token_hint="ente")
        _raw_user("2", mcp_token="")
        self.assertEqual(db_accounts.mcp_token_issue("1"), {"token": None, "hint": "ente", "last_used": None})
        self.assertEqual(db_accounts.user_by_mcp_token("a-mano-del-asistente")["dni"], "1")
        self.assertTrue(db_accounts.mcp_token_issue("2")["token"])

    def test_feed_and_mcp_tokens_are_independent(self):
        _make("1")
        feed, mcp = db_accounts.feed_token_issue("1")["token"], db_accounts.mcp_token_issue("1")["token"]
        self.assertNotEqual(feed, mcp)
        self.assertEqual(db_accounts.user_by_feed_token(feed)["dni"], "1")
        self.assertIsNone(db_accounts.user_by_feed_token(mcp))
        self.assertIsNone(db_accounts.user_by_mcp_token(feed))

    def test_regenerate_rotates_and_revokes_the_old_token(self):
        _make("1")
        old = db_accounts.mcp_token_issue("1")["token"]
        new = db_accounts.mcp_token_regenerate("1")
        self.assertNotEqual(new, old)
        self.assertEqual(_row("1")["mcp_token"], self._digest(new))
        self.assertEqual(db_accounts.mcp_token_issue("1"), {"token": None, "hint": new[-4:], "last_used": None})
        self.assertIsNone(db_accounts.user_by_mcp_token(old))
        self.assertEqual(db_accounts.user_by_mcp_token(new)["dni"], "1")

    def test_regenerate_creates_one_when_there_was_none_and_is_none_for_unknown(self):
        _make("1")
        self.assertTrue(db_accounts.mcp_token_regenerate("1"))
        self.assertIsNone(db_accounts.mcp_token_regenerate("404"))

    def test_regenerate_touches_only_that_account(self):
        _make("1")
        _make("2")
        other = db_accounts.mcp_token_issue("2")["token"]
        db_accounts.mcp_token_regenerate("1")
        self.assertEqual(db_accounts.user_by_mcp_token(other)["dni"], "2")

    def test_user_by_mcp_token_public_shape(self):
        _make("1")
        user = db_accounts.user_by_mcp_token(db_accounts.mcp_token_issue("1")["token"])
        self.assertEqual(set(user), USER_KEYS)
        for secret in ("pass_hash", "mcp_token", "mcp_token_hint"):
            self.assertNotIn(secret, user)

    def test_blank_unknown_and_archived_resolve_to_nothing(self):
        _make("1")
        token = db_accounts.mcp_token_issue("1")["token"]
        for value in (None, "", "nope"):
            self.assertIsNone(db_accounts.user_by_mcp_token(value))
        _sql("UPDATE users SET active=false WHERE dni='1'")
        self.assertIsNone(db_accounts.user_by_mcp_token(token))
        _sql("UPDATE users SET active=NULL WHERE dni='1'")
        self.assertEqual(db_accounts.user_by_mcp_token(token)["dni"], "1")


if __name__ == "__main__":
    unittest.main()
