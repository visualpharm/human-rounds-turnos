"""test_db_operators_store.py — the panel-operator statements of `core/db.py` against a real Postgres.

Stage 5 of the SQLAlchemy Core move converts the accounts and auth groups of `core/db.py`. This
file pins the panel operators: the `operators` table with its `operator_access` overlay (effective
role, granted / denied permissions) and the `operator_username_history` of retired names —
`operator_create` / `_rename` / `_update` / `_deactivate`, the reads by name, email, doctor, portal
account id and calendar-feed token, the password check with its transparent legacy-hash upgrade,
the portal claim / unbind, the one-account-per-doctor guard, the account-state word and
`connection_scope`. It pins returned shapes (the Spanish wire keys of `db_wire._wire`, which keys each
reader exposes), the None / False / raised-exception results, what each call does and does not
write, and the rollbacks. Written and run green on the hand-written SQL first; it must pass
unedited on Core. The last-manager protection lives in `core/permissions.py` and is pinned there.
"""
import hashlib
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from cryptography.hazmat.primitives.kdf.scrypt import Scrypt  # noqa: E402

from app.auth import password_hashing  # noqa: E402
from app.core import db  # noqa: E402
from app.core import db_operators  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_db_operators_store")

UTC = timezone.utc
PW = "Synthetic-pw-1"


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


def _row(usuario):
    """The raw `operators` row of `usuario` (DB column names), or None."""
    rows = _sql("SELECT * FROM operators WHERE username=%s", (usuario,))
    return rows[0] if rows else None


def _access(usuario):
    rows = _sql("SELECT * FROM operator_access WHERE username=%s", (usuario,))
    return rows[0] if rows else None


def _role(slug):
    """A custom role the FK of operator_access.role_slug can point at."""
    _sql("INSERT INTO roles (slug, name) VALUES (%s, %s) ON CONFLICT DO NOTHING", (slug, slug))


def _raw_op(usuario, pass_hash="not-a-hash", role="medico", **cols):
    """Insert an operators row by raw SQL; extra DB columns come as keyword arguments."""
    cols = {"username": usuario, "pass_hash": pass_hash, "role": role, **cols}
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    _sql(f"INSERT INTO operators ({names}) VALUES ({marks})", tuple(cols.values()))


def _scrypt_hash(password):
    """The retired `scrypt$<salt-hex>$<digest-hex>` envelope."""
    salt = os.urandom(16)
    digest = Scrypt(salt=salt, length=32, n=16384, r=8, p=1).derive(password.encode())
    return "scrypt$%s$%s" % (salt.hex(), digest.hex())


def _held_locks():
    return {r["mode"] for r in _sql(
        "SELECT mode FROM pg_locks WHERE pid=pg_backend_pid() AND relation='operators'::regclass")}


# The keys every reader exposes, per shape. The public one never carries the
# calendar-feed token nor its hint: a panel row is not a credential (2026-11-06).
_PUBLIC_KEYS = {"id", "usuario", "rol", "activo", "creado", "medico_id", "onboarding_completed_at",
                "ultimo_acceso", "email", "invitado_en", "centros", "tiene_2fa",
                "cuenta_estado"}
_OVERLAY_KEYS = {"permisos_agregados", "permisos_quitados"}
_FULL_KEYS = {"id", "usuario", "pass_hash", "totp_secret", "rol", "activo", "creado", "medico_id",
              "onboarding_completed_at", "feed_token", "feed_token_hint", "ultimo_acceso", "email", "invitado_en",
              "cuit", "portal_user_id", "centros"}


class DbCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _sql("TRUNCATE operators, operator_access, operator_username_history RESTART IDENTITY CASCADE")


# ---- account state ------------------------------------------------------------------

class TestAccountState(DbCase):
    def test_four_states_and_their_precedence(self):
        state = db_operators.operator_account_state
        self.assertEqual(state({}), "sin_invitar")
        self.assertEqual(state({"activo": False}), "sin_invitar")
        self.assertEqual(state({"activo": True}), "invitado")
        self.assertEqual(state({"activo": False, "invitado_en": "2026-01-01T00:00:00"}), "invitado")
        self.assertEqual(state({"activo": False, "ultimo_acceso": "2026-01-01"}), "ingreso")
        self.assertEqual(state({"activo": True, "invitado_en": "x", "ultimo_acceso": "y"}), "ingreso")
        self.assertEqual(state({"ultimo_acceso": "y", "onboarding_completed_at": "z"}), "perfil_completo")
        self.assertEqual(state({"onboarding_completed_at": "z"}), "perfil_completo")

    def test_empty_values_do_not_count(self):
        self.assertEqual(db_operators.operator_account_state(
            {"onboarding_completed_at": None, "ultimo_acceso": "", "invitado_en": None, "activo": 0}),
            "sin_invitar")

    def test_state_through_the_lifecycle(self):
        db_operators.operator_create("ana", PW, "medico", activo=False)
        self.assertEqual(db_operators.operator_get_public("ana")["cuenta_estado"], "sin_invitar")
        db_operators.operator_update("ana", invited=True)
        self.assertEqual(db_operators.operator_get_public("ana")["cuenta_estado"], "invitado")
        db_operators.operator_mark_access("ana")
        self.assertEqual(db_operators.operator_get_public("ana")["cuenta_estado"], "ingreso")
        db_operators.operator_update("ana", medico_id="D1")
        self.assertTrue(db_operators.operator_complete_onboarding("ana") is False)  # inactive: cannot finish
        db_operators.operator_update("ana", activo=True)
        self.assertTrue(db_operators.operator_complete_onboarding("ana"))
        self.assertEqual(db_operators.operator_get_public("ana")["cuenta_estado"], "perfil_completo")


# ---- the public copy of a record ----------------------------------------------------

class TestOperatorPublic(DbCase):
    def test_none_stays_none(self):
        self.assertIsNone(db_operators._operator_public(None))

    def test_secrets_and_portal_binding_are_dropped(self):
        row = {"usuario": "u", "pass_hash": "h", "totp_secret": "SECRET", "portal_user_id": "p",
               "cuit": "20111111112", "centros": None, "activo": True}
        out = db_operators._operator_public(row)
        for gone in ("pass_hash", "totp_secret", "portal_user_id", "cuit"):
            self.assertNotIn(gone, out)
        self.assertIs(out["tiene_2fa"], True)
        self.assertEqual(out["centros"], [])
        self.assertEqual(out["cuenta_estado"], "invitado")
        self.assertIn("pass_hash", row)  # a copy: the input is untouched

    def test_no_totp_secret_means_no_2fa(self):
        self.assertIs(db_operators._operator_public({"usuario": "u", "totp_secret": ""})["tiene_2fa"], False)
        self.assertIs(db_operators._operator_public({"usuario": "u"})["tiene_2fa"], False)

    def test_datetimes_become_isoformat_other_values_pass(self):
        moment = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
        out = db_operators._operator_public({
            "usuario": "u", "creado": moment, "ultimo_acceso": moment,
            "onboarding_completed_at": moment, "invitado_en": moment})
        for key in ("creado", "ultimo_acceso", "onboarding_completed_at", "invitado_en"):
            self.assertEqual(out[key], moment.isoformat())
        out = db_operators._operator_public({"usuario": "u", "creado": "already", "ultimo_acceso": None})
        self.assertEqual(out["creado"], "already")
        self.assertIsNone(out["ultimo_acceso"])

    def test_centros_always_a_list(self):
        self.assertEqual(db_operators._operator_public({"centros": ("a", "b")})["centros"], ["a", "b"])
        self.assertEqual(db_operators._operator_public({"centros": []})["centros"], [])


# ---- the access overlay -------------------------------------------------------------

class TestAccessOverlay(DbCase):
    def test_base_role_maps_only_the_four_table_roles(self):
        for rol in ("admin", "medico", "admisionista", "reportes"):
            self.assertEqual(db_operators._operator_base_role(rol), rol)
        for rol in ("gestor_agendas", "", None, "ADMIN"):
            self.assertEqual(db_operators._operator_base_role(rol), "admisionista")

    def test_with_access_ignores_empty_and_none_rows_without_a_query(self):
        with db._connect() as conn, conn.cursor() as cur:
            self.assertIsNone(db_operators._with_access(cur, []))
            self.assertIsNone(db_operators._with_access(cur, [None, {}]))

    def test_with_access_overlays_in_place(self):
        _role("gestor_agendas")
        db_operators.operator_create("ana", PW, "gestor_agendas")
        db_operators.operator_create("beto", PW, "admin")
        _sql("UPDATE operator_access SET granted=%s, denied=%s WHERE username='ana'",
             (["a.read", "a.write"], ["b.read"]))
        a, b, none = {"usuario": "ana", "rol": "admisionista"}, {"usuario": "beto", "rol": "admin"}, None
        with db._connect() as conn, conn.cursor() as cur:
            self.assertIsNone(db_operators._with_access(cur, [a, none, b]))
        self.assertEqual(a["rol"], "gestor_agendas")
        self.assertEqual(a["permisos_agregados"], ["a.read", "a.write"])
        self.assertEqual(a["permisos_quitados"], ["b.read"])
        self.assertEqual(b["rol"], "admin")
        self.assertEqual((b["permisos_agregados"], b["permisos_quitados"]), ([], []))

    def test_with_access_unknown_user_gets_empty_lists_and_keeps_its_role(self):
        r = {"usuario": "nadie", "rol": "reportes"}
        with db._connect() as conn, conn.cursor() as cur:
            db_operators._with_access(cur, [r])
        self.assertEqual(r, {"usuario": "nadie", "rol": "reportes",
                             "permisos_agregados": [], "permisos_quitados": []})

    def test_access_row_without_slug_keeps_the_table_role_but_exposes_permissions(self):
        db_operators.operator_create("ana", PW, "reportes")
        _sql("INSERT INTO operator_access (username, granted) VALUES ('ana', %s)", (["x"],))
        got = db_operators.operator_get("ana")
        self.assertEqual(got["rol"], "reportes")
        self.assertEqual(got["permisos_agregados"], ["x"])

    def test_set_access_role_writes_the_slug_only_off_the_table_roles(self):
        _role("gestor_agendas")
        _raw_op("ana", role="admisionista")
        with db._connect() as conn, conn.cursor() as cur:
            db_operators._set_access_role(cur, "ana", "gestor_agendas")
        self.assertEqual(_access("ana")["role_slug"], "gestor_agendas")
        with db._connect() as conn, conn.cursor() as cur:
            db_operators._set_access_role(cur, "ana", "reportes")
        row = _access("ana")
        self.assertIsNone(row["role_slug"])  # the row stays, the slug is cleared

    def test_set_access_role_keeps_granted_and_denied_and_bumps_updated(self):
        _role("gestor_agendas")
        _raw_op("ana")
        _sql("INSERT INTO operator_access (username, granted, denied, updated) "
             "VALUES ('ana', %s, %s, now() - interval '1 day')", (["g"], ["d"]))
        before = _access("ana")["updated"]
        with db._connect() as conn, conn.cursor() as cur:
            db_operators._set_access_role(cur, "ana", "gestor_agendas")
        row = _access("ana")
        self.assertEqual((row["granted"], row["denied"]), (["g"], ["d"]))
        self.assertGreater(row["updated"], before)

    def test_set_access_role_for_a_missing_account_violates_the_fk(self):
        import psycopg
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            with db._connect() as conn, conn.cursor() as cur:
                db_operators._set_access_role(cur, "fantasma", "reportes")
        self.assertIsNone(_access("fantasma"))


# ---- the readers --------------------------------------------------------------------

class TestReaders(DbCase):
    def test_operators_list_orders_by_creation_includes_inactive_and_overlays(self):
        _role("gestor_agendas")
        self.assertEqual(db_operators.operators_list(), [])
        db_operators.operator_create("tercero", PW, "admin")
        db_operators.operator_create("primero", PW, "gestor_agendas", activo=False)
        db_operators.operator_create("segundo", PW, "reportes", totp_secret="JBSWY3DP")
        base = datetime(2026, 1, 1, tzinfo=UTC)
        for i, name in enumerate(("primero", "segundo", "tercero")):
            _sql("UPDATE operators SET created=%s WHERE username=%s", (base + timedelta(days=i), name))
        rows = db_operators.operators_list()
        self.assertEqual([r["usuario"] for r in rows], ["primero", "segundo", "tercero"])
        first, second, _ = rows
        self.assertIs(first["activo"], False)
        self.assertEqual(first["rol"], "gestor_agendas")  # the effective role, not the table base
        self.assertEqual(set(first), _PUBLIC_KEYS | _OVERLAY_KEYS)
        self.assertIs(second["tiene_2fa"], True)
        self.assertNotIn("totp_secret", second)
        self.assertEqual(first["creado"], base.isoformat())

    def test_operator_get_is_the_complete_record(self):
        _role("gestor_agendas")
        db_operators.operator_create("ana", PW, "gestor_agendas", totp_secret="JBSWY3DP", medico_id="D1",
                           email="ana@x.org", centros=["c1", "c2"])
        got = db_operators.operator_get("ana")
        self.assertEqual(set(got), _FULL_KEYS | _OVERLAY_KEYS)
        self.assertTrue(got["pass_hash"].startswith("$argon2id$"))
        self.assertEqual(got["totp_secret"], "JBSWY3DP")
        self.assertEqual(got["rol"], "gestor_agendas")
        self.assertEqual(got["medico_id"], "D1")
        self.assertEqual(got["centros"], ["c1", "c2"])
        self.assertIsInstance(got["creado"], datetime)  # raw, not isoformat
        self.assertIsNone(got["portal_user_id"])
        self.assertIsNone(got["cuit"])
        self.assertIsNone(got["feed_token"])
        self.assertIsNone(got["feed_token_hint"])
        self.assertEqual(got["permisos_agregados"], [])

    def test_operator_get_missing_is_none(self):
        self.assertIsNone(db_operators.operator_get("nadie"))
        self.assertIsNone(db_operators.operator_get(None))
        self.assertIsNone(db_operators.operator_get_public("nadie"))

    def test_operator_get_is_exact_on_the_username(self):
        db_operators.operator_create("Ana", PW, "admin")
        self.assertIsNotNone(db_operators.operator_get("Ana"))
        self.assertIsNone(db_operators.operator_get("ana"))

    def test_operator_get_public_hides_secrets(self):
        db_operators.operator_create("ana", PW, "medico", totp_secret="JBSWY3DP")
        _sql("UPDATE operators SET portal_user_id='p1', cuit='20111111112' WHERE username='ana'")
        got = db_operators.operator_get_public("ana")
        self.assertEqual(set(got), _PUBLIC_KEYS | _OVERLAY_KEYS)
        self.assertIs(got["tiene_2fa"], True)
        self.assertIsInstance(got["creado"], str)

    def test_by_email_is_case_insensitive_and_trims_both_sides(self):
        _raw_op("ana", email="  Ana@Hospital.ORG ")
        got = db_operators.operator_by_email("  ana@hospital.org\n")
        self.assertEqual(got["usuario"], "ana")
        self.assertEqual(set(got), _PUBLIC_KEYS | _OVERLAY_KEYS)
        self.assertIsNone(db_operators.operator_by_email("otra@hospital.org"))
        self.assertIsNone(db_operators.operator_by_email(None))
        self.assertIsNone(db_operators.operator_by_email(""))

    def test_by_email_overlays_the_effective_role(self):
        _role("gestor_agendas")
        db_operators.operator_create("ana", PW, "gestor_agendas", email="ana@x.org")
        self.assertEqual(db_operators.operator_by_email("ana@x.org")["rol"], "gestor_agendas")

    def test_by_email_returns_an_inactive_single_match(self):
        db_operators.operator_create("ana", PW, "medico", email="ana@x.org", activo=False)
        self.assertIs(db_operators.operator_by_email("ana@x.org")["activo"], False)

    def test_by_email_ambiguous_is_none_even_with_an_inactive_duplicate(self):
        db_operators.operator_create("ana", PW, "medico", email="dup@x.org")
        db_operators.operator_create("beto", PW, "medico", email="DUP@x.org", activo=False)
        self.assertIsNone(db_operators.operator_by_email("dup@x.org"))

    def test_active_email_taken(self):
        db_operators.operator_create("ana", PW, "medico", email="ana@x.org")
        db_operators.operator_create("beto", PW, "medico", email="beto@x.org", activo=False)
        taken = db_operators.operator_active_email_taken
        self.assertTrue(taken("ANA@x.org "))
        self.assertFalse(taken("ana@x.org", "ana"))       # excluding itself
        self.assertTrue(taken("ana@x.org", "beto"))       # excluding somebody else
        self.assertFalse(taken("beto@x.org"))             # inactive does not count
        self.assertFalse(taken("nadie@x.org"))
        self.assertFalse(taken(None))
        self.assertTrue(taken("ana@x.org", None))

    def test_operator_doctor_id(self):
        db_operators.operator_create("ana", PW, "medico", medico_id="D1")
        db_operators.operator_create("beto", PW, "admin")
        self.assertEqual(db_operators.operator_doctor_id("ana"), "D1")
        self.assertIsNone(db_operators.operator_doctor_id("beto"))
        self.assertIsNone(db_operators.operator_doctor_id("nadie"))
        self.assertIsNone(db_operators.operator_doctor_id(None))

    def test_operator_doctor_ids_is_the_set_of_doctor_accounts_active_or_not(self):
        self.assertEqual(db_operators.operator_doctor_ids(), set())
        db_operators.operator_create("ana", PW, "medico", medico_id="D1")
        db_operators.operator_create("beto", PW, "medico", medico_id="D2", activo=False)
        db_operators.operator_create("carla", PW, "medico")                 # no doctor
        db_operators.operator_create("dani", PW, "admin", medico_id="D3")   # a doctor promoted to administrator
        self.assertEqual(db_operators.operator_doctor_ids(), {"D1", "D2", "D3"})

    def test_holds_doctor_id_any_role_active_or_not(self):
        self.assertFalse(db_operators.operator_holds_doctor_id("D1"))
        db_operators.operator_create("ana", PW, "admin", medico_id="D1", activo=False)
        self.assertTrue(db_operators.operator_holds_doctor_id("D1"))
        self.assertFalse(db_operators.operator_holds_doctor_id("D2"))
        self.assertFalse(db_operators.operator_holds_doctor_id(None))

    def test_holds_doctor_id_reads_through_the_callers_connection(self):
        with db._connect() as conn:
            db_operators.operator_create("ana", PW, "medico", medico_id="D9", connection=conn)
            self.assertTrue(db_operators.operator_holds_doctor_id("D9", connection=conn))
            self.assertFalse(db_operators.operator_holds_doctor_id("D9"))  # uncommitted: invisible elsewhere
        self.assertTrue(db_operators.operator_holds_doctor_id("D9"))

    def test_by_doctor_id_is_the_medico_account_active_or_not(self):
        db_operators.operator_create("ana", PW, "medico", medico_id="D1", activo=False, totp_secret="S")
        db_operators.operator_create("beto", PW, "admin", medico_id="D2")
        got = db_operators.operator_by_doctor_id("D1")
        self.assertEqual(got["usuario"], "ana")
        self.assertIs(got["activo"], False)
        self.assertIs(got["tiene_2fa"], True)
        self.assertNotIn("pass_hash", got)
        # A doctor promoted to administrator keeps being that doctor's account (2026-11-02).
        self.assertEqual(db_operators.operator_by_doctor_id("D2")["usuario"], "beto")
        self.assertIsNone(db_operators.operator_by_doctor_id("D404"))
        self.assertIsNone(db_operators.operator_by_doctor_id(None))

    def test_lookups_without_the_overlay_keep_the_base_role_and_omit_permission_keys(self):
        # operator_by_doctor_id, operator_by_feed_token and operator_deactivate do NOT apply
        # operator_access: `rol` is the table base and the permisos_* keys are absent.
        _role("gestor_agendas")
        db_operators.operator_create("ana", PW, "gestor_agendas", medico_id="D1")
        db_operators.operator_create("beto", PW, "gestor_agendas")
        token = db_operators.operator_feed_token_issue("beto")["token"]
        _sql("UPDATE operators SET role='medico' WHERE username='ana'")
        for got in (db_operators.operator_by_doctor_id("D1"), db_operators.operator_by_feed_token(token),
                    db_operators.operator_deactivate("beto")):
            self.assertEqual(got["rol"], "admisionista" if got["usuario"] == "beto" else "medico")
            self.assertEqual(set(got), _PUBLIC_KEYS)
        self.assertEqual(db_operators.operator_get_public("beto")["rol"], "gestor_agendas")


# ---- password check -----------------------------------------------------------------

class TestVerifyPassword(DbCase):
    def test_right_password_returns_the_complete_record(self):
        _role("gestor_agendas")
        db_operators.operator_create("ana", PW, "gestor_agendas", totp_secret="JBSWY3DP")
        with patch.object(password_hashing, "dummy_verify") as dummy:
            got = db_operators.operator_verify_password("ana", PW)
        dummy.assert_not_called()
        self.assertEqual(set(got), _FULL_KEYS | _OVERLAY_KEYS)
        self.assertEqual(got["totp_secret"], "JBSWY3DP")
        self.assertEqual(got["rol"], "gestor_agendas")
        self.assertEqual(got["pass_hash"], _row("ana")["pass_hash"])

    def test_wrong_password_is_none_and_changes_nothing(self):
        db_operators.operator_create("ana", PW, "medico")
        before = _row("ana")
        with patch.object(password_hashing, "dummy_verify") as dummy:
            self.assertIsNone(db_operators.operator_verify_password("ana", "wrong"))
        dummy.assert_not_called()
        self.assertEqual(_row("ana"), before)

    def test_empty_none_and_garbage_passwords_are_none(self):
        db_operators.operator_create("ana", PW, "medico")
        self.assertIsNone(db_operators.operator_verify_password("ana", ""))
        self.assertIsNone(db_operators.operator_verify_password("ana", None))
        _raw_op("raro", pass_hash="not-a-hash")
        self.assertIsNone(db_operators.operator_verify_password("raro", PW))

    def test_unknown_user_runs_the_dummy_verify_and_is_none(self):
        with patch.object(password_hashing, "dummy_verify") as dummy:
            self.assertIsNone(db_operators.operator_verify_password("nadie", PW))
        dummy.assert_called_once_with(PW)

    def test_inactive_account_never_verifies_even_with_the_right_password(self):
        db_operators.operator_create("ana", PW, "medico", activo=False)
        with patch.object(password_hashing, "dummy_verify") as dummy:
            self.assertIsNone(db_operators.operator_verify_password("ana", PW))
        dummy.assert_called_once_with(PW)

    def test_username_is_exact(self):
        db_operators.operator_create("Ana", PW, "medico")
        self.assertIsNone(db_operators.operator_verify_password("ana", PW))
        self.assertIsNotNone(db_operators.operator_verify_password("Ana", PW))

    def test_legacy_scrypt_hash_is_upgraded_once_to_argon2(self):
        legacy = _scrypt_hash(PW)
        _raw_op("viejo", pass_hash=legacy)
        got = db_operators.operator_verify_password("viejo", PW)
        self.assertTrue(got["pass_hash"].startswith("$argon2id$"))
        stored = _row("viejo")["pass_hash"]
        self.assertEqual(stored, got["pass_hash"])
        self.assertNotEqual(stored, legacy)
        again = db_operators.operator_verify_password("viejo", PW)  # no further rewrite
        self.assertEqual(again["pass_hash"], stored)
        self.assertEqual(_row("viejo")["pass_hash"], stored)

    def test_legacy_hash_with_a_wrong_password_is_left_alone(self):
        legacy = _scrypt_hash(PW)
        _raw_op("viejo", pass_hash=legacy)
        self.assertIsNone(db_operators.operator_verify_password("viejo", "wrong"))
        self.assertEqual(_row("viejo")["pass_hash"], legacy)

    def test_mark_access_stamps_last_access(self):
        db_operators.operator_create("ana", PW, "medico")
        db_operators.operator_create("beto", PW, "medico")
        self.assertIsNone(_row("ana")["last_access"])
        self.assertIsNone(db_operators.operator_mark_access("ana"))
        self.assertIsNotNone(_row("ana")["last_access"])
        self.assertIsNone(_row("beto")["last_access"])
        before = _row("ana")["last_access"]
        db_operators.operator_mark_access("ana")
        self.assertGreaterEqual(_row("ana")["last_access"], before)
        self.assertIsNone(db_operators.operator_mark_access("nadie"))  # unknown: silent
        self.assertIsInstance(db_operators.operator_get_public("ana")["ultimo_acceso"], str)


# ---- the staff-portal binding -------------------------------------------------------

class TestPortal(DbCase):
    def test_by_portal_id(self):
        _role("gestor_agendas")
        db_operators.operator_create("ana", PW, "gestor_agendas", activo=False)
        _sql("UPDATE operators SET portal_user_id='p1', cuit='20111111112' WHERE username='ana'")
        got = db_operators.operator_by_portal_id("p1")
        self.assertEqual(set(got), _FULL_KEYS | _OVERLAY_KEYS)
        self.assertEqual((got["usuario"], got["portal_user_id"], got["cuit"]), ("ana", "p1", "20111111112"))
        self.assertIs(got["activo"], False)           # active or not: the caller decides
        self.assertEqual(got["rol"], "gestor_agendas")
        self.assertIsNone(db_operators.operator_by_portal_id("p2"))

    def test_by_portal_id_empty_values_are_none(self):
        _raw_op("ana")
        for empty in (None, "", 0):
            self.assertIsNone(db_operators.operator_by_portal_id(empty))

    def test_by_portal_id_coerces_to_text(self):
        _raw_op("ana", portal_user_id="123")
        self.assertEqual(db_operators.operator_by_portal_id(123)["usuario"], "ana")

    def test_claim_binds_the_one_active_account_with_that_email(self):
        _role("gestor_agendas")
        db_operators.operator_create("ana", PW, "gestor_agendas", email="  Ana@Hospital.ORG ")
        db_operators.operator_create("beto", PW, "medico", email="beto@hospital.org")
        got = db_operators.operator_claim_portal(" ana@hospital.org ", 77, "20111111112")
        self.assertEqual(set(got), _FULL_KEYS | _OVERLAY_KEYS)
        self.assertEqual((got["usuario"], got["portal_user_id"], got["cuit"]), ("ana", "77", "20111111112"))
        self.assertEqual(got["rol"], "gestor_agendas")
        row = _row("ana")
        self.assertEqual((row["portal_user_id"], row["cuit"]), ("77", "20111111112"))
        self.assertIsNone(_row("beto")["portal_user_id"])
        self.assertEqual(db_operators.operator_by_portal_id("77")["usuario"], "ana")

    def test_claim_rejects_bad_arguments_and_writes_nothing(self):
        db_operators.operator_create("ana", PW, "medico", email="ana@x.org")
        for args in (("", "p", "20111111112"), (None, "p", "20111111112"),
                     ("ana@x.org", "", "20111111112"), ("ana@x.org", None, "20111111112"),
                     ("ana@x.org", "p", ""), ("ana@x.org", "p", "2011111111"),
                     ("ana@x.org", "p", "201111111123"), ("ana@x.org", "p", "20-11111111-2"),
                     ("ana@x.org", "p", "2011111111a"), ("ana@x.org", "p", None),
                     ("   ", "p", "20111111112")):
            self.assertIsNone(db_operators.operator_claim_portal(*args), args)
        row = _row("ana")
        self.assertEqual((row["portal_user_id"], row["cuit"]), (None, None))

    def test_claim_with_no_matching_active_account_is_none(self):
        db_operators.operator_create("ana", PW, "medico", email="ana@x.org", activo=False)
        self.assertIsNone(db_operators.operator_claim_portal("ana@x.org", "p", "20111111112"))
        self.assertIsNone(db_operators.operator_claim_portal("nadie@x.org", "p", "20111111112"))
        self.assertIsNone(_row("ana")["portal_user_id"])

    def test_claim_skips_an_inactive_namesake_but_by_email_stays_ambiguous(self):
        db_operators.operator_create("ana", PW, "medico", email="ana@x.org")
        db_operators.operator_create("ana2", PW, "medico", email="ana@x.org", activo=False)
        self.assertIsNone(db_operators.operator_by_email("ana@x.org"))
        got = db_operators.operator_claim_portal("ana@x.org", "p", "20111111112")
        self.assertEqual(got["usuario"], "ana")
        self.assertIsNone(_row("ana2")["portal_user_id"])

    def test_claim_with_two_active_accounts_is_none_and_writes_nothing(self):
        db_operators.operator_create("ana", PW, "medico", email="dup@x.org")
        db_operators.operator_create("beto", PW, "medico", email="DUP@x.org")
        self.assertIsNone(db_operators.operator_claim_portal("dup@x.org", "p", "20111111112"))
        self.assertEqual(_sql("SELECT count(*) AS n FROM operators WHERE portal_user_id IS NOT NULL")[0]["n"], 0)

    def test_claim_of_an_already_bound_account_is_none_and_keeps_the_binding(self):
        db_operators.operator_create("ana", PW, "medico", email="ana@x.org")
        self.assertIsNotNone(db_operators.operator_claim_portal("ana@x.org", "p1", "20111111112"))
        self.assertIsNone(db_operators.operator_claim_portal("ana@x.org", "p2", "20222222223"))
        row = _row("ana")
        self.assertEqual((row["portal_user_id"], row["cuit"]), ("p1", "20111111112"))

    def test_claim_with_someone_elses_portal_id_or_cuit_is_none_and_writes_nothing(self):
        db_operators.operator_create("ana", PW, "medico", email="ana@x.org")
        db_operators.operator_create("beto", PW, "medico", email="beto@x.org")
        self.assertIsNotNone(db_operators.operator_claim_portal("ana@x.org", "p1", "20111111112"))
        self.assertIsNone(db_operators.operator_claim_portal("beto@x.org", "p1", "20222222223"))   # portal id taken
        self.assertIsNone(db_operators.operator_claim_portal("beto@x.org", "p2", "20111111112"))   # CUIT taken
        row = _row("beto")
        self.assertEqual((row["portal_user_id"], row["cuit"]), (None, None))
        self.assertIsNotNone(db_operators.operator_claim_portal("beto@x.org", "p2", "20222222223"))

    def test_unbind_clears_both_and_allows_a_fresh_claim(self):
        db_operators.operator_create("ana", PW, "medico", email="ana@x.org")
        db_operators.operator_claim_portal("ana@x.org", "p1", "20111111112")
        self.assertIs(db_operators.operator_unbind_portal("ana"), True)
        row = _row("ana")
        self.assertEqual((row["portal_user_id"], row["cuit"]), (None, None))
        self.assertIsNone(db_operators.operator_by_portal_id("p1"))
        self.assertIs(db_operators.operator_unbind_portal("ana"), True)   # idempotent while the row exists
        self.assertEqual(db_operators.operator_claim_portal("ana@x.org", "p9", "20999999991")["portal_user_id"], "p9")

    def test_unbind_unknown_account_is_false(self):
        self.assertIs(db_operators.operator_unbind_portal("nadie"), False)
        self.assertIs(db_operators.operator_unbind_portal(None), False)


# ---- the one-account-per-doctor guard -----------------------------------------------

class TestDoctorGuard(DbCase):
    def _guard(self, *args):
        with db._connect() as conn, conn.cursor() as cur:
            db_operators._guard_operator_doctor(cur, *args)
            return {r["locktype"] for r in (cur.execute(
                "SELECT locktype FROM pg_locks WHERE pid=pg_backend_pid() AND locktype='advisory'"
            ).fetchall())}

    def test_only_a_medico_or_admin_with_a_doctor_is_checked_and_locked(self):
        db_operators.operator_create("otro", PW, "medico", medico_id="D1")
        self.assertEqual(self._guard("ana", "admisionista", "D1"), set())
        self.assertEqual(self._guard("ana", "medico", None), set())
        self.assertEqual(self._guard("ana", "medico", ""), set())

    def test_conflict_when_another_medico_account_holds_the_doctor(self):
        db_operators.operator_create("otro", PW, "medico", medico_id="D1", activo=False)
        with db._connect() as conn, conn.cursor() as cur:
            with self.assertRaises(db_operators.OperatorConflict) as cm:
                db_operators._guard_operator_doctor(cur, "ana", "medico", "D1")
        self.assertEqual(str(cm.exception), "medico_con_usuario")
        self.assertIsInstance(cm.exception, ValueError)

    def test_no_conflict_for_itself_or_another_doctor_and_an_admin_holder_counts(self):
        db_operators.operator_create("otro", PW, "medico", medico_id="D1")
        db_operators.operator_create("jefe", PW, "admin", medico_id="D2")
        self.assertEqual(self._guard("otro", "medico", "D1"), {"advisory"})   # itself; the lock is taken
        self.assertEqual(self._guard("jefe", "admin", "D2"), {"advisory"})    # an admin keeping its own doctor
        self.assertEqual(self._guard("ana", "medico", "D3"), {"advisory"})
        # A doctor promoted to administrator still holds the doctor (2026-11-02).
        with db._connect() as conn, conn.cursor() as cur:
            with self.assertRaises(db_operators.OperatorConflict):
                db_operators._guard_operator_doctor(cur, "ana", "medico", "D2")

    def test_link_violation_is_recognised_by_constraint_name(self):
        yes = SimpleNamespace(diag=SimpleNamespace(constraint_name="operators_doctor_id_uniq"))
        other = SimpleNamespace(diag=SimpleNamespace(constraint_name="operators_username_key"))
        self.assertIs(db_operators._doctor_link_violation(yes), True)
        self.assertIs(db_operators._doctor_link_violation(other), False)
        self.assertIs(db_operators._doctor_link_violation(ValueError("x")), False)
        self.assertIs(db_operators._doctor_link_violation(SimpleNamespace(diag=None)), False)


# ---- create -------------------------------------------------------------------------

class TestCreate(DbCase):
    def test_creates_and_returns_the_public_record(self):
        got = db_operators.operator_create("ana", PW, "reportes", totp_secret="JBSWY3DP", medico_id=None,
                                 email="ana@x.org", centros=["c1"])
        self.assertEqual(set(got), _PUBLIC_KEYS | _OVERLAY_KEYS)
        self.assertEqual((got["usuario"], got["rol"], got["email"], got["centros"]),
                         ("ana", "reportes", "ana@x.org", ["c1"]))
        self.assertIs(got["activo"], True)
        self.assertIs(got["tiene_2fa"], True)
        self.assertEqual(got["cuenta_estado"], "invitado")
        self.assertIsNone(got["invitado_en"])
        self.assertEqual((got["permisos_agregados"], got["permisos_quitados"]), ([], []))
        self.assertIsInstance(got["creado"], str)
        row = _row("ana")
        self.assertEqual((row["role"], row["totp_secret"], row["centers"]), ("reportes", "JBSWY3DP", ["c1"]))

    def test_default_role_is_medico(self):
        self.assertEqual(db_operators.operator_create("ana", PW)["rol"], "medico")

    def test_password_is_stored_hashed_never_plain(self):
        db_operators.operator_create("ana", PW, "medico")
        stored = _row("ana")["pass_hash"]
        self.assertTrue(stored.startswith("$argon2id$"))
        self.assertNotIn(PW, stored)
        self.assertIsNotNone(db_operators.operator_verify_password("ana", PW))
        self.assertIsNone(db_operators.operator_verify_password("ana", PW + "x"))

    def test_a_current_pass_hash_is_stored_as_given(self):
        ready = password_hashing.hash_password(PW)
        db_operators.operator_create("ana", None, "medico", pass_hash=ready)
        self.assertEqual(_row("ana")["pass_hash"], ready)
        self.assertIsNotNone(db_operators.operator_verify_password("ana", PW))

    def test_a_non_current_pass_hash_is_refused_before_any_write(self):
        for bad in ("", "plain-text", _scrypt_hash(PW), "$argon2id$v=19$m=8,t=1,p=1$YQ$YQ"):
            with self.assertRaises(ValueError):
                db_operators.operator_create("ana", PW, "medico", pass_hash=bad)
        self.assertIsNone(_row("ana"))

    def test_missing_password_and_hash_raises_before_any_write(self):
        with self.assertRaises(TypeError):
            db_operators.operator_create("ana", None, "medico")
        self.assertIsNone(_row("ana"))

    def test_empty_centros_and_none_are_stored_as_null(self):
        for i, centros in enumerate(([], None, ())):
            name = f"u{i}"
            self.assertEqual(db_operators.operator_create(name, PW, "medico", centros=centros)["centros"], [])
            self.assertIsNone(_row(name)["centers"])
        db_operators.operator_create("tup", PW, "medico", centros=("a", "b"))
        self.assertEqual(_row("tup")["centers"], ["a", "b"])

    def test_inactive_and_truthiness(self):
        got = db_operators.operator_create("ana", PW, "medico", activo=False)
        self.assertIs(got["activo"], False)
        self.assertEqual(got["cuenta_estado"], "sin_invitar")
        db_operators.operator_create("beto", PW, "medico", activo=0)
        self.assertIs(_row("beto")["active"], False)
        db_operators.operator_create("carla", PW, "medico", activo="x")
        self.assertIs(_row("carla")["active"], True)

    def test_custom_role_is_stored_in_access_over_the_base_role(self):
        _role("gestor_agendas")
        got = db_operators.operator_create("ana", PW, "gestor_agendas")
        self.assertEqual(got["rol"], "gestor_agendas")
        self.assertEqual(_row("ana")["role"], "admisionista")
        self.assertEqual(_access("ana")["role_slug"], "gestor_agendas")

    def test_table_role_leaves_no_access_row(self):
        db_operators.operator_create("ana", PW, "admin")
        self.assertIsNone(_access("ana"))

    def test_unknown_custom_role_fails_whole_and_leaves_nothing(self):
        import psycopg
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            db_operators.operator_create("ana", PW, "rol_que_no_existe")
        self.assertIsNone(_row("ana"))
        self.assertIsNone(_access("ana"))

    def test_username_collisions(self):
        db_operators.operator_create("Ana", PW, "medico")
        for dup in ("Ana", "ana", "ANA"):
            with self.assertRaises(db_operators.OperatorConflict) as cm:
                db_operators.operator_create(dup, PW, "medico")
            self.assertEqual(str(cm.exception), "nombre_ya_existe")
        self.assertEqual(_sql("SELECT count(*) AS n FROM operators")[0]["n"], 1)

    def test_a_retired_name_stays_reserved(self):
        db_operators.operator_create("ana", PW, "medico")
        _sql("INSERT INTO operator_username_history (old_username, new_username, operator_id) "
             "SELECT 'Vieja', 'ana', id FROM operators WHERE username='ana'")
        with self.assertRaises(db_operators.OperatorConflict) as cm:
            db_operators.operator_create("vieja", PW, "medico")
        self.assertEqual(str(cm.exception), "nombre_ya_existe")
        self.assertIsNone(_row("vieja"))

    def test_second_medico_for_a_doctor_conflicts(self):
        db_operators.operator_create("ana", PW, "medico", medico_id="D1")
        with self.assertRaises(db_operators.OperatorConflict) as cm:
            db_operators.operator_create("beto", PW, "medico", medico_id="D1")
        self.assertEqual(str(cm.exception), "medico_con_usuario")
        self.assertIsNone(_row("beto"))

    def test_any_role_cannot_share_a_doctor_id_the_index_decides(self):
        db_operators.operator_create("ana", PW, "medico", medico_id="D1")
        with self.assertRaises(db_operators.OperatorConflict) as cm:
            db_operators.operator_create("beto", PW, "admin", medico_id="D1")   # guard skips non-medico; the unique index fires
        self.assertEqual(str(cm.exception), "medico_con_usuario")
        self.assertIsNone(_row("beto"))
        db_operators.operator_create("carla", PW, "admin", medico_id="D2")
        db_operators.operator_create("dani", PW, "admin", medico_id=None)
        db_operators.operator_create("eli", PW, "admin", medico_id=None)

    def test_inside_the_callers_transaction_it_commits_and_rolls_back_with_it(self):
        class Boom(Exception):
            pass
        with self.assertRaises(Boom):
            with db._connect() as conn:
                db_operators.operator_create("ana", PW, "medico", connection=conn)
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 FROM operators WHERE username='ana'")
                    self.assertIsNotNone(cur.fetchone())
                raise Boom()
        self.assertIsNone(_row("ana"))
        with db._connect() as conn:
            db_operators.operator_create("beto", PW, "medico", connection=conn)
        self.assertIsNotNone(_row("beto"))

    def test_account_is_complete_in_one_transaction_when_the_doctor_link_fails(self):
        db_operators.operator_create("ana", PW, "medico", medico_id="D1")
        with self.assertRaises(db_operators.OperatorConflict):
            with db._connect() as conn:
                db_operators.operator_create("beto", PW, "medico", medico_id="D1", connection=conn)
        self.assertIsNone(_row("beto"))


# ---- connection_scope / name guards -------------------------------------------------

class TestConnectionScopeAndNames(DbCase):
    def test_connection_scope_yields_the_callers_connection_untouched(self):
        sentinel = object()
        with db_operators.connection_scope(sentinel) as got:
            self.assertIs(got, sentinel)
        with db_operators.connection_scope(connection=sentinel) as got:
            self.assertIs(got, sentinel)

    def test_connection_scope_opens_and_commits_its_own(self):
        with db_operators.connection_scope() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO operators (username, pass_hash, role) VALUES ('ana','h','medico')")
        self.assertIsNotNone(_row("ana"))

    def test_connection_scope_rolls_back_its_own_on_error(self):
        with self.assertRaises(RuntimeError):
            with db_operators.connection_scope() as conn:
                with conn.cursor() as cur:
                    cur.execute("INSERT INTO operators (username, pass_hash, role) VALUES ('ana','h','medico')")
                raise RuntimeError("boom")
        self.assertIsNone(_row("ana"))

    def test_name_taken_live_names_any_case_and_retired_names(self):
        _raw_op("Ana")
        _sql("INSERT INTO operator_username_history (old_username, new_username, operator_id) "
             "SELECT 'Vieja', 'Ana', id FROM operators WHERE username='Ana'")
        with db._connect() as conn, conn.cursor() as cur:
            self.assertIs(db_operators.operator_name_taken(cur, "Ana"), True)
            self.assertIs(db_operators.operator_name_taken(cur, "ANA"), True)
            self.assertIs(db_operators.operator_name_taken(cur, "vieja"), True)
            self.assertIs(db_operators.operator_name_taken(cur, "libre"), False)
            self.assertIn("ShareRowExclusiveLock", {r["mode"] for r in (cur.execute(
                "SELECT mode FROM pg_locks WHERE pid=pg_backend_pid() "
                "AND relation='operators'::regclass").fetchall())})

    def test_guard_username_conflicts_and_current_exclusion(self):
        _raw_op("Ana")
        _raw_op("beto")
        _sql("INSERT INTO operator_username_history (old_username, new_username, operator_id) "
             "SELECT 'Vieja', 'beto', id FROM operators WHERE username='beto'")
        with db._connect() as conn, conn.cursor() as cur:
            self.assertIsNone(db_operators._guard_operator_username(cur, "libre"))
            self.assertIsNone(db_operators._guard_operator_username(cur, "ANA", "Ana"))    # a case-only change of itself
            self.assertIsNone(db_operators._guard_operator_username(cur, "Ana", "Ana"))
            for usuario, current in (("ana", None), ("ANA", "beto"), ("beto", "Ana"), ("VIEJA", None),
                                     ("Vieja", "Ana"), ("vieja", "beto")):
                with self.assertRaises(db_operators.OperatorConflict) as cm:
                    db_operators._guard_operator_username(cur, usuario, current)
                self.assertEqual(str(cm.exception), "nombre_ya_existe")
            self.assertIn("ShareRowExclusiveLock", {r["mode"] for r in (cur.execute(
                "SELECT mode FROM pg_locks WHERE pid=pg_backend_pid() "
                "AND relation='operators'::regclass").fetchall())})


# ---- rename -------------------------------------------------------------------------

# (table, column, INSERT with %(u)s = the account name and %(k)s = a unique key)
_RENAME_REFS = [
    ("operator_access", "username", "INSERT INTO operator_access (username) VALUES (%(u)s)"),
    ("admin_preferences", "username",
     "INSERT INTO admin_preferences (username, key, value) VALUES (%(u)s, %(k)s, '1')"),
    ("admin_account_security", "username", "INSERT INTO admin_account_security (username) VALUES (%(u)s)"),
    ("admin_reset_tokens", "username",
     "INSERT INTO admin_reset_tokens (token_hash, username, expires) VALUES (%(k)s, %(u)s, now())"),
    ("passkeys", "username",
     "INSERT INTO passkeys (username, credential_id, public_key) VALUES (%(u)s, %(k)s, '{}')"),
    ("passkey_challenges", "username",
     "INSERT INTO passkey_challenges (challenge_digest, purpose, username, expires_at) "
     "VALUES (convert_to(%(k)s, 'UTF8'), 'login', %(u)s, now())"),
    ("booking_requests", "resolved_by",
     "INSERT INTO booking_requests (dni, first_name, status, resolved_by) "
     "VALUES (%(k)s, 'A', 'pendiente', %(u)s)"),
    ("booking_requests", "attendance_updated_by",
     "INSERT INTO booking_requests (dni, first_name, status, attendance_updated_by) "
     "VALUES (%(k)s || 'x', 'A', 'pendiente', %(u)s)"),
    ("appointment_events", "actor",
     "INSERT INTO appointment_events (request_id, kind, actor) "
     "SELECT id, 'creada', %(u)s FROM booking_requests WHERE dni = 'base'"),
    ("attendance_events", "operator",
     "INSERT INTO attendance_events (request_id, dni, attendance_to, operator) "
     "SELECT id, %(k)s, 'presente', %(u)s FROM booking_requests WHERE dni = 'base'"),
    ("hsi_sync_conflicts", "resolved_by",
     "INSERT INTO hsi_sync_conflicts (entity_type, entity_id, direction, reason, resolved_by) "
     "VALUES ('t', %(k)s, 'inbound', 'r', %(u)s)"),
    ("interviews", "alert_seen_by",
     "INSERT INTO interviews (session_key, alert_seen_by) VALUES (%(k)s, %(u)s)"),
    ("consultations", "created_by",
     "INSERT INTO consultations (interview_id, doctor_id, created_by) "
     "SELECT id, 'd', %(u)s FROM interviews WHERE alert_seen_by = %(u)s"),
    ("ai_prompts", "updated_by", "INSERT INTO ai_prompts (key, text_en, updated_by) VALUES (%(k)s, 'x', %(u)s)"),
    ("ai_prompts_history", "updated_by",
     "INSERT INTO ai_prompts_history (key, text_en, updated_by) VALUES (%(k)s, 'x', %(u)s)"),
    ("translations", "updated_by",
     "INSERT INTO translations (source_key, lang, source_hash, content, updated_by) "
     "VALUES (%(k)s, 'en', 'h', 'c', %(u)s)"),
    ("legal_docs", "updated_by", "INSERT INTO legal_docs (doc, body, updated_by) VALUES (%(k)s, 'b', %(u)s)"),
]


class TestRename(DbCase):
    def setUp(self):
        super().setUp()
        _sql("TRUNCATE admin_preferences, admin_account_security, admin_reset_tokens, passkeys, "
             "passkey_challenges, ai_prompts, ai_prompts_history, translations, legal_docs, "
             "hsi_sync_conflicts")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM consultations")
            cur.execute("DELETE FROM interviews")
            _dbtest.delete_booking_requests(cur)
        owner = patch("app.auth.admin_auth.owner_usuario", return_value="el.dueno")
        owner.start()
        self.addCleanup(owner.stop)
        db_operators.operator_create("old.staff", PW, "medico")

    def _seed_refs(self):
        db_operators.operator_create("other.staff", PW, "medico")
        _sql("INSERT INTO booking_requests (dni, first_name, status) VALUES ('base', 'B', 'pendiente')")
        for u, k in (("old.staff", "k1"), ("other.staff", "k2")):
            for table, column, insert in _RENAME_REFS:
                _sql(insert, {"u": u, "k": k + table + column})

    def _conflict(self, usuario, nuevo, **kw):
        with self.assertRaises(db_operators.OperatorConflict) as cm:
            db_operators.operator_rename(usuario, nuevo, current_password=kw.pop("current_password", PW), **kw)
        return str(cm.exception)

    def test_moves_the_account_the_references_and_records_the_history(self):
        self._seed_refs()
        old_id = _row("old.staff")["id"]
        self.assertIsNone(db_operators.operator_rename("old.staff", "new.staff", current_password=PW))
        self.assertIsNone(_row("old.staff"))
        self.assertEqual(_row("new.staff")["id"], old_id)
        for table, column, _ in _RENAME_REFS:
            counts = {r["who"]: r["n"] for r in _sql(
                f"SELECT {column} AS who, count(*) AS n FROM {table} GROUP BY {column}")}
            self.assertNotIn("old.staff", counts, (table, column))
            self.assertEqual(counts.get("new.staff"), 1, (table, column))
            if table != "operator_access":
                self.assertEqual(counts.get("other.staff"), 1, (table, column))
        self.assertEqual(
            [dict(r) for r in _sql("SELECT old_username, new_username, operator_id FROM operator_username_history")],
            [{"old_username": "old.staff", "new_username": "new.staff", "operator_id": old_id}])
        self.assertIsNotNone(_sql("SELECT changed_at FROM operator_username_history")[0]["changed_at"])

    def test_password_is_kept_unless_a_new_one_is_given(self):
        before = _row("old.staff")["pass_hash"]
        db_operators.operator_rename("old.staff", "mid.staff", current_password=PW, password="")
        self.assertEqual(_row("mid.staff")["pass_hash"], before)
        self.assertIsNotNone(db_operators.operator_verify_password("mid.staff", PW))
        db_operators.operator_rename("mid.staff", "new.staff", current_password=PW, password="Another-pw-2")
        self.assertNotEqual(_row("new.staff")["pass_hash"], before)
        self.assertIsNone(db_operators.operator_verify_password("new.staff", PW))
        self.assertIsNotNone(db_operators.operator_verify_password("new.staff", "Another-pw-2"))

    def test_a_legacy_hash_is_upgraded_by_the_rename(self):
        _raw_op("viejo", pass_hash=_scrypt_hash(PW))
        db_operators.operator_rename("viejo", "nuevo", current_password=PW)
        self.assertTrue(_row("nuevo")["pass_hash"].startswith("$argon2id$"))
        self.assertIsNotNone(db_operators.operator_verify_password("nuevo", PW))

    def test_rename_keeps_the_rest_of_the_account(self):
        _role("gestor_agendas")
        db_operators.operator_create("rica", PW, "gestor_agendas", totp_secret="S", medico_id="D1",
                           email="r@x.org", centros=["c1"])
        db_operators.operator_rename("rica", "rica2", current_password=PW)
        got = db_operators.operator_get("rica2")
        self.assertEqual((got["rol"], got["totp_secret"], got["medico_id"], got["email"], got["centros"]),
                         ("gestor_agendas", "S", "D1", "r@x.org", ["c1"]))
        self.assertEqual(_access("rica2")["role_slug"], "gestor_agendas")

    def test_a_case_only_change_is_allowed(self):
        db_operators.operator_rename("old.staff", "OLD.staff", current_password=PW)
        self.assertIsNotNone(_row("OLD.staff"))
        self.assertEqual(_sql("SELECT old_username FROM operator_username_history")[0]["old_username"], "old.staff")

    def test_invalid_new_names_and_the_owner_are_refused(self):
        for nuevo in ("a", "", "con espacio", "x" * 121, "tab\tchar", "EL.DUENO", "el.dueno"):
            self.assertEqual(self._conflict("old.staff", nuevo), "nombre_ya_existe", nuevo)
        self.assertEqual(self._conflict("el.dueno", "otro.nombre"), "nombre_ya_existe")
        self.assertEqual(self._conflict("EL.DUENO", "otro.nombre"), "nombre_ya_existe")
        self.assertIsNotNone(_row("old.staff"))
        self.assertEqual(_sql("SELECT count(*) AS n FROM operator_username_history")[0]["n"], 0)

    def test_taken_and_retired_names_are_refused(self):
        db_operators.operator_create("busy", PW, "medico")
        self.assertEqual(self._conflict("old.staff", "BUSY"), "nombre_ya_existe")
        db_operators.operator_rename("busy", "busy2", current_password=PW)
        self.assertEqual(self._conflict("old.staff", "busy"), "nombre_ya_existe")   # retired name reserved
        self.assertEqual(self._conflict("busy2", "busy"), "nombre_ya_existe")       # even for its former owner

    def test_unknown_inactive_or_wrong_password_is_credenciales_and_changes_nothing(self):
        db_operators.operator_create("off", PW, "medico", activo=False)
        self.assertEqual(self._conflict("nadie", "libre"), "credenciales")
        self.assertEqual(self._conflict("off", "libre"), "credenciales")
        self.assertEqual(self._conflict("old.staff", "libre", current_password="wrong"), "credenciales")
        self.assertEqual(self._conflict("old.staff", "libre", current_password=""), "credenciales")
        self.assertIsNotNone(_row("old.staff"))
        self.assertIsNone(_row("libre"))
        self.assertEqual(_sql("SELECT count(*) AS n FROM operator_username_history")[0]["n"], 0)

    def test_the_name_check_comes_before_the_account_check(self):
        db_operators.operator_create("busy", PW, "medico")
        self.assertEqual(self._conflict("nadie", "busy"), "nombre_ya_existe")

    def test_any_failure_rolls_back_the_account_and_every_reference(self):
        _sql("INSERT INTO ai_prompts (key, text_en, updated_by) VALUES ('p', 'x', 'old.staff')")
        _sql("INSERT INTO admin_preferences (username, key, value) VALUES ('old.staff', 'landing', '1')")
        _sql("CREATE FUNCTION s2_reject_update() RETURNS trigger LANGUAGE plpgsql AS "
             "$$ BEGIN RAISE EXCEPTION 'synthetic rollback'; END $$")
        _sql("CREATE TRIGGER s2_reject_update BEFORE UPDATE ON ai_prompts "
             "FOR EACH ROW EXECUTE FUNCTION s2_reject_update()")
        try:
            before = _row("old.staff")
            with self.assertRaises(Exception) as cm:
                db_operators.operator_rename("old.staff", "new.staff", password="Another-pw-2", current_password=PW)
            self.assertNotIsInstance(cm.exception, db_operators.OperatorConflict)
            self.assertEqual(_row("old.staff"), before)
            self.assertIsNone(_row("new.staff"))
            self.assertEqual(_sql("SELECT username FROM admin_preferences")[0]["username"], "old.staff")
            self.assertEqual(_sql("SELECT count(*) AS n FROM operator_username_history")[0]["n"], 0)
        finally:
            _sql("DROP TRIGGER s2_reject_update ON ai_prompts")
            _sql("DROP FUNCTION s2_reject_update()")


# ---- update -------------------------------------------------------------------------

class TestUpdate(DbCase):
    def setUp(self):
        super().setUp()
        db_operators.operator_create("ana", PW, "medico", email="ana@x.org", centros=["c1"], medico_id="D1")

    def test_nothing_to_change_returns_the_current_public_record(self):
        got = db_operators.operator_update("ana")
        self.assertEqual(got, db_operators.operator_get_public("ana"))
        self.assertEqual(set(got), _PUBLIC_KEYS | _OVERLAY_KEYS)
        self.assertIsNone(db_operators.operator_update("nadie"))
        for ignored in (dict(password=""), dict(password=None), dict(rol=None), dict(activo=None),
                        dict(link_only=True)):
            self.assertEqual(db_operators.operator_update("ana", **ignored), got, ignored)
        self.assertIsNone(db_operators.operator_update("nadie", password=""))

    def test_each_field_on_its_own(self):
        got = db_operators.operator_update("ana", activo=False)
        self.assertIs(got["activo"], False)
        self.assertEqual(got["cuenta_estado"], "sin_invitar")
        self.assertIs(db_operators.operator_update("ana", activo=True)["activo"], True)
        self.assertEqual(db_operators.operator_update("ana", email="nuevo@x.org")["email"], "nuevo@x.org")
        self.assertIsNone(db_operators.operator_update("ana", email=None)["email"])
        self.assertEqual(db_operators.operator_update("ana", centros=["c2", "c3"])["centros"], ["c2", "c3"])
        self.assertEqual(db_operators.operator_update("ana", centros=[])["centros"], [])
        self.assertIsNone(_row("ana")["centers"])
        self.assertEqual(db_operators.operator_update("ana", centros=("c9",))["centros"], ["c9"])
        self.assertIsNone(_row("ana")["totp_secret"])
        self.assertIs(db_operators.operator_update("ana", totp_secret="JBSWY3DP")["tiene_2fa"], True)
        self.assertEqual(_row("ana")["totp_secret"], "JBSWY3DP")
        self.assertIs(db_operators.operator_update("ana", email="otro@x.org")["tiene_2fa"], True)   # 2FA untouched
        self.assertIs(db_operators.operator_update("ana", totp_secret=None)["tiene_2fa"], False)
        self.assertIsNone(_row("ana")["totp_secret"])
        self.assertEqual(db_operators.operator_update("ana", medico_id="D2")["medico_id"], "D2")
        self.assertIsNone(db_operators.operator_update("ana", medico_id=None)["medico_id"])
        self.assertIsNone(_row("ana")["doctor_id"])

    def test_untouched_fields_stay(self):
        db_operators.operator_update("ana", activo=False)
        row = _row("ana")
        self.assertEqual((row["role"], row["email"], row["centers"], row["doctor_id"]),
                         ("medico", "ana@x.org", ["c1"], "D1"))

    def test_password_is_rehashed_and_the_old_one_stops_working(self):
        before = _row("ana")["pass_hash"]
        db_operators.operator_update("ana", password="Another-pw-2")
        after = _row("ana")["pass_hash"]
        self.assertNotEqual(after, before)
        self.assertTrue(after.startswith("$argon2id$"))
        self.assertNotIn("Another-pw-2", after)
        self.assertIsNone(db_operators.operator_verify_password("ana", PW))
        self.assertIsNotNone(db_operators.operator_verify_password("ana", "Another-pw-2"))

    def test_invitation_stamps_invited_at(self):
        self.assertIsNone(_row("ana")["invited_at"])
        got = db_operators.operator_update("ana", invited=True)
        self.assertIsInstance(got["invitado_en"], str)
        self.assertIsNotNone(_row("ana")["invited_at"])

    def test_several_fields_at_once(self):
        got = db_operators.operator_update("ana", rol="admin", activo=False, password="Another-pw-2",
                                 email="z@x.org", centros=["q"], totp_secret="S", medico_id=None, invited=True)
        self.assertEqual((got["rol"], got["activo"], got["email"], got["centros"], got["medico_id"]),
                         ("admin", False, "z@x.org", ["q"], None))
        self.assertIs(got["tiene_2fa"], True)
        self.assertIsNotNone(got["invitado_en"])

    def test_role_changes_map_to_the_table_role_and_the_access_slug(self):
        _role("gestor_agendas")
        got = db_operators.operator_update("ana", rol="gestor_agendas")
        self.assertEqual(got["rol"], "gestor_agendas")
        self.assertEqual(_row("ana")["role"], "admisionista")
        self.assertEqual(_access("ana")["role_slug"], "gestor_agendas")
        self.assertEqual(db_operators.operator_get_public("ana")["rol"], "gestor_agendas")
        got = db_operators.operator_update("ana", rol="reportes")
        self.assertEqual(got["rol"], "reportes")
        self.assertEqual(_row("ana")["role"], "reportes")
        self.assertIsNone(_access("ana")["role_slug"])   # cleared; the row stays

    def test_updating_other_fields_leaves_the_access_row_alone(self):
        _role("gestor_agendas")
        db_operators.operator_update("ana", rol="gestor_agendas")
        _sql("UPDATE operator_access SET granted=%s WHERE username='ana'", (["g"],))
        got = db_operators.operator_update("ana", email="otro@x.org")
        self.assertEqual(got["rol"], "gestor_agendas")
        self.assertEqual(got["permisos_agregados"], ["g"])
        self.assertEqual(_access("ana")["role_slug"], "gestor_agendas")

    def test_role_update_keeps_granted_and_denied(self):
        _role("gestor_agendas")
        _sql("INSERT INTO operator_access (username, granted, denied) VALUES ('ana', %s, %s)", (["g"], ["d"]))
        got = db_operators.operator_update("ana", rol="gestor_agendas")
        self.assertEqual((got["permisos_agregados"], got["permisos_quitados"]), (["g"], ["d"]))

    def test_unknown_role_fails_whole(self):
        import psycopg
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            db_operators.operator_update("ana", rol="rol_que_no_existe", email="cambiado@x.org")
        row = _row("ana")
        self.assertEqual((row["role"], row["email"]), ("medico", "ana@x.org"))
        self.assertIsNone(_access("ana"))

    def test_unknown_account_is_none_and_writes_nothing(self):
        self.assertIsNone(db_operators.operator_update("nadie", rol="admin"))
        self.assertIsNone(db_operators.operator_update("nadie", medico_id="D5"))
        self.assertIsNone(db_operators.operator_update("nadie", activo=False))
        self.assertIsNone(db_operators.operator_update("nadie", email="x@x.org", invited=True))
        self.assertEqual(_sql("SELECT count(*) AS n FROM operators")[0]["n"], 1)
        self.assertIsNone(_access("nadie"))

    def test_moving_a_medico_to_a_taken_doctor_conflicts_and_writes_nothing(self):
        db_operators.operator_create("carla", PW, "medico", medico_id="D3")
        with self.assertRaises(db_operators.OperatorConflict) as cm:
            db_operators.operator_update("carla", medico_id="D1", email="cambiado@x.org")      # ana holds D1
        self.assertEqual(str(cm.exception), "medico_con_usuario")
        row = _row("carla")
        self.assertEqual((row["doctor_id"], row["email"]), ("D3", None))

    def test_a_role_change_keeps_the_doctor_link_and_is_checked_against_it(self):
        db_operators.operator_create("carla", PW, "admin", medico_id="D8")
        self.assertEqual(db_operators.operator_update("carla", rol="medico")["medico_id"], "D8")
        self.assertEqual(_row("carla")["role"], "medico")
        self.assertEqual(db_operators.operator_update("carla", rol="reportes")["medico_id"], "D8")
        self.assertEqual(_row("carla")["doctor_id"], "D8")

    def test_the_unique_index_decides_for_non_medico_roles(self):
        db_operators.operator_create("beto", PW, "admin")
        with self.assertRaises(db_operators.OperatorConflict) as cm:
            db_operators.operator_update("beto", medico_id="D1")   # held by ana; the guard skips an admin, the index fires
        self.assertEqual(str(cm.exception), "medico_con_usuario")
        self.assertIsNone(_row("beto")["doctor_id"])

    def test_link_only_refuses_to_move_an_already_linked_account(self):
        with self.assertRaises(db_operators.OperatorConflict) as cm:
            db_operators.operator_update("ana", medico_id="D2", link_only=True)
        self.assertEqual(str(cm.exception), "usuario_ya_vinculado")
        self.assertEqual(_row("ana")["doctor_id"], "D1")
        self.assertEqual(db_operators.operator_update("ana", medico_id="D1", link_only=True, invited=True)["medico_id"], "D1")
        db_operators.operator_create("beto", PW, "medico")
        self.assertEqual(db_operators.operator_update("beto", medico_id="D2", link_only=True)["medico_id"], "D2")
        self.assertIsNone(db_operators.operator_update("nadie", medico_id="D2", link_only=True))

    def test_a_unique_violation_that_is_not_the_doctor_link_propagates(self):
        import psycopg
        db_operators.operator_create("beto", PW, "medico", email="beto@x.org")
        _sql("CREATE UNIQUE INDEX s2_email_uniq ON operators (email)")
        try:
            with self.assertRaises(psycopg.errors.UniqueViolation):
                db_operators.operator_update("beto", email="ana@x.org")
            self.assertEqual(_row("beto")["email"], "beto@x.org")
        finally:
            _sql("DROP INDEX s2_email_uniq")


# ---- onboarding, feed token, deactivate ---------------------------------------------

class TestOnboardingFeedDeactivate(DbCase):
    def test_complete_onboarding_conditions(self):
        db_operators.operator_create("ana", PW, "medico", medico_id="D1")
        db_operators.operator_create("sin.doc", PW, "medico")
        db_operators.operator_create("admin", PW, "admin", medico_id="D2")
        db_operators.operator_create("off", PW, "medico", medico_id="D3", activo=False)
        for name in ("sin.doc", "admin", "off", "nadie"):
            self.assertIs(db_operators.operator_complete_onboarding(name), False, name)
            self.assertIsNone(_row(name) and _row(name)["onboarding_completed_at"])
        self.assertIs(db_operators.operator_complete_onboarding("ana"), True)
        self.assertIsNotNone(_row("ana")["onboarding_completed_at"])

    def test_complete_onboarding_is_idempotent_and_keeps_the_first_moment(self):
        db_operators.operator_create("ana", PW, "medico", medico_id="D1")
        db_operators.operator_complete_onboarding("ana")
        _sql("UPDATE operators SET onboarding_completed_at='2026-01-02T03:04:05+00' WHERE username='ana'")
        self.assertIs(db_operators.operator_complete_onboarding("ana"), True)
        self.assertEqual(_row("ana")["onboarding_completed_at"], datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
        got = db_operators.operator_get_public("ana")
        self.assertEqual(got["onboarding_completed_at"], "2026-01-02T03:04:05+00:00")
        self.assertEqual(got["cuenta_estado"], "perfil_completo")

    @staticmethod
    def _digest(token):
        # The stored form, written independently of db_operators: the SHA-256 hex
        # of the UTF-8 token, the same expression alembic 0039 applies in SQL.
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def test_feed_token_is_shown_once_then_only_its_hint(self):
        db_operators.operator_create("ana", PW, "medico")
        db_operators.operator_create("beto", PW, "medico")
        self.assertIsNone(db_operators.operator_feed_token_issue("nadie"))
        first = db_operators.operator_feed_token_issue("ana")
        token = first["token"]
        self.assertRegex(token, r"^[A-Za-z0-9_-]{22}$")
        self.assertEqual(first["hint"], token[-4:])
        self.assertEqual(db_operators.operator_feed_token_issue("ana"), {"token": None, "hint": token[-4:]})
        self.assertNotEqual(db_operators.operator_feed_token_issue("beto")["token"], token)
        self.assertEqual(db_operators.operator_by_feed_token(token)["usuario"], "ana")

    def test_the_database_keeps_only_the_hash_and_the_last_four_characters(self):
        db_operators.operator_create("ana", PW, "medico")
        token = db_operators.operator_feed_token_issue("ana")["token"]
        row = _row("ana")
        self.assertEqual(row["feed_token"], self._digest(token))
        self.assertEqual(row["feed_token_hint"], token[-4:])
        self.assertNotIn(token, json.dumps(row, default=str))
        rotated = db_operators.operator_rotate_feed_token("ana")
        self.assertNotIn(rotated, json.dumps(_row("ana"), default=str))
        self.assertEqual(_row("ana")["feed_token"], self._digest(rotated))

    def test_the_stored_hash_is_not_a_working_token(self):
        db_operators.operator_create("ana", PW, "medico")
        db_operators.operator_feed_token_issue("ana")
        self.assertIsNone(db_operators.operator_by_feed_token(_row("ana")["feed_token"]))

    def test_feed_token_hashed_before_keeps_working(self):
        # A doctor's calendar subscribed before alembic 0039 hashed its token keeps updating.
        db_operators.operator_create("ana", PW, "medico")
        _sql("UPDATE operators SET feed_token=%s, feed_token_hint='0039' WHERE username='ana'",
             (self._digest("suscripto-antes-de-0039"),))
        self.assertEqual(db_operators.operator_feed_token_issue("ana"), {"token": None, "hint": "0039"})
        self.assertEqual(db_operators.operator_by_feed_token("suscripto-antes-de-0039")["usuario"], "ana")

    def test_feed_token_empty_string_counts_as_missing(self):
        db_operators.operator_create("ana", PW, "medico")
        _sql("UPDATE operators SET feed_token='' WHERE username='ana'")
        token = db_operators.operator_feed_token_issue("ana")["token"]
        self.assertEqual(len(token), 22)
        self.assertEqual(_row("ana")["feed_token"], self._digest(token))

    def test_feed_token_still_in_the_clear_is_hashed_in_place(self):
        # Written by the previous build while both served a deploy, after 0039 ran
        # (its rotation leaves the old hint behind): the address it handed out keeps
        # working, on the next open of «Mis turnos» or the next calendar poll.
        raw = "escritoPorElBuildAnterior"
        db_operators.operator_create("ana", PW, "medico")
        db_operators.operator_create("beto", PW, "medico")
        _sql("UPDATE operators SET feed_token=%s, feed_token_hint='0039' WHERE username='ana'", (raw,))
        _sql("UPDATE operators SET feed_token=%s, feed_token_hint=NULL WHERE username='beto'", (raw + "B",))
        self.assertEqual(db_operators.operator_feed_token_issue("ana"), {"token": None, "hint": raw[-4:]})
        self.assertEqual(_row("ana")["feed_token"], self._digest(raw))
        self.assertEqual(db_operators.operator_by_feed_token(raw)["usuario"], "ana")
        self.assertEqual(db_operators.operator_by_feed_token(raw + "B")["usuario"], "beto")
        self.assertEqual((_row("beto")["feed_token"], _row("beto")["feed_token_hint"]),
                         (self._digest(raw + "B"), (raw + "B")[-4:]))

    def test_feed_token_rotate_replaces_the_token_and_kills_the_old_one(self):
        db_operators.operator_create("ana", PW, "medico")
        db_operators.operator_create("beto", PW, "medico")
        self.assertIsNone(db_operators.operator_rotate_feed_token("nadie"))
        old = db_operators.operator_feed_token_issue("ana")["token"]
        beto = db_operators.operator_feed_token_issue("beto")["token"]
        new = db_operators.operator_rotate_feed_token("ana")
        self.assertRegex(new, r"^[A-Za-z0-9_-]{22}$")
        self.assertNotEqual(new, old)
        self.assertEqual(db_operators.operator_feed_token_issue("ana"), {"token": None, "hint": new[-4:]})
        self.assertIsNone(db_operators.operator_by_feed_token(old))
        self.assertEqual(db_operators.operator_by_feed_token(new)["usuario"], "ana")
        self.assertEqual(db_operators.operator_by_feed_token(beto)["usuario"], "beto")

    def test_feed_token_rotate_mints_one_when_the_account_had_none(self):
        db_operators.operator_create("ana", PW, "medico")
        new = db_operators.operator_rotate_feed_token("ana")
        self.assertEqual(len(new), 22)
        self.assertEqual(_row("ana")["feed_token"], self._digest(new))

    def test_feed_token_revoke_kills_the_address_and_the_next_visit_shows_a_new_one(self):
        # The admin's «Cambiar la dirección del calendario»: a token rotated
        # there could never be shown (only its hash is stored), so it is
        # cleared and the doctor's next visit issues one in the clear.
        db_operators.operator_create("ana", PW, "medico")
        db_operators.operator_create("beto", PW, "medico")
        self.assertFalse(db_operators.operator_revoke_feed_token("nadie"))
        old = db_operators.operator_feed_token_issue("ana")["token"]
        beto = db_operators.operator_feed_token_issue("beto")["token"]
        self.assertTrue(db_operators.operator_revoke_feed_token("ana"))
        self.assertIsNone(_row("ana")["feed_token"])
        self.assertIsNone(_row("ana")["feed_token_hint"])
        self.assertIsNone(db_operators.operator_by_feed_token(old))
        issued = db_operators.operator_feed_token_issue("ana")
        self.assertNotEqual(issued["token"], old)
        self.assertEqual(db_operators.operator_by_feed_token(issued["token"])["usuario"], "ana")
        self.assertEqual(db_operators.operator_by_feed_token(beto)["usuario"], "beto")

    def test_by_feed_token_resolves_the_public_record_only(self):
        db_operators.operator_create("ana", PW, "medico", totp_secret="S")
        token = db_operators.operator_feed_token_issue("ana")["token"]
        got = db_operators.operator_by_feed_token(token)
        self.assertEqual(got["usuario"], "ana")
        self.assertEqual(set(got), _PUBLIC_KEYS)
        self.assertIs(got["tiene_2fa"], True)
        for secret in ("pass_hash", "feed_token", "feed_token_hint"):
            self.assertNotIn(secret, got)
        self.assertIsNone(db_operators.operator_by_feed_token("otro-token"))

    def test_no_public_operator_row_carries_the_feed_token(self):
        db_operators.operator_create("ana", PW, "medico")
        db_operators.operator_feed_token_issue("ana")
        for got in (db_operators.operator_get_public("ana"), db_operators.operators_list()[0]):
            self.assertNotIn("feed_token", got)
            self.assertNotIn("feed_token_hint", got)

    def test_by_feed_token_never_matches_an_account_without_one(self):
        db_operators.operator_create("ana", PW, "medico")
        for empty in (None, ""):
            self.assertIsNone(db_operators.operator_by_feed_token(empty))

    def test_by_feed_token_serves_inactive_accounts_too(self):
        db_operators.operator_create("ana", PW, "medico")
        token = db_operators.operator_feed_token_issue("ana")["token"]
        db_operators.operator_deactivate("ana")
        self.assertIs(db_operators.operator_by_feed_token(token)["activo"], False)

    def test_deactivate_is_a_soft_delete(self):
        db_operators.operator_create("ana", PW, "medico", email="ana@x.org", medico_id="D1")
        db_operators.operator_create("beto", PW, "medico")
        got = db_operators.operator_deactivate("ana")
        self.assertIs(got["activo"], False)
        self.assertEqual(set(got), _PUBLIC_KEYS)
        self.assertEqual((got["usuario"], got["email"], got["medico_id"]), ("ana", "ana@x.org", "D1"))
        self.assertIs(_row("ana")["active"], False)
        self.assertIs(_row("beto")["active"], True)
        self.assertIsNone(db_operators.operator_verify_password("ana", PW))
        self.assertEqual(db_operators.operator_deactivate("ana")["usuario"], "ana")   # idempotent
        self.assertIsNone(db_operators.operator_deactivate("nadie"))
        self.assertIsNone(db_operators.operator_deactivate(None))
        self.assertEqual(_sql("SELECT count(*) AS n FROM operators")[0]["n"], 2)


if __name__ == "__main__":
    unittest.main()
