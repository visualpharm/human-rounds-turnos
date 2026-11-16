"""test_foreign_keys.py — the 18 foreign keys of Alembic 0032_foreign_keys.

Until 2026-10-25 no application table had a foreign key: a parent could be
deleted under its children and nothing but code kept links honest. Pinned here:

  1. The live schema and `core.db_accounts.FOREIGN_KEYS` say the same thing — a new link
     needs a revision AND a registry entry, a stray one fails (TestForeignKeyMap).
  2. The database refuses to delete a parent that still has children (the owner's
     answer to orphans-report question 2), and the explicit order children →
     parents works (TestParentsAreProtected).
  3. A DNI correction and an operator rename are ONE update of the parent row;
     the database moves the children (TestCascadeOnUpdate).
  4. The revision on an installation that already has orphan rows does not stop
     the site: the constraints are added NOT VALID, the violating ones stay so
     with a log line, the rest validate, and `validate_foreign_keys.py` finishes
     the job once the data is fixed (TestRevisionOnExistingData).
"""
import contextlib
import importlib.util
import io
import os
import sys
import unittest
from urllib.parse import urlsplit, urlunsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_schema  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_fk")


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


def _validate_script():
    spec = importlib.util.spec_from_file_location(
        "validate_foreign_keys", os.path.join(ROOT, "scripts", "fixes", "validate_foreign_keys.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _live_keys():
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT con.conname AS n, rel.relname AS child, att.attname AS col, "
            "prel.relname AS parent, patt.attname AS pcol, "
            "con.confdeltype AS dele, con.confupdtype AS upd, con.convalidated AS ok "
            "FROM pg_constraint con "
            "JOIN pg_class rel ON rel.oid = con.conrelid "
            "JOIN pg_class prel ON prel.oid = con.confrelid "
            "JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = con.conkey[1] "
            "JOIN pg_attribute patt ON patt.attrelid = con.confrelid AND patt.attnum = con.confkey[1] "
            "WHERE con.contype = 'f' AND rel.relnamespace = 'public'::regnamespace "
            "AND rel.relname NOT LIKE 'procrastinate%%' ORDER BY con.conname")
        return [tuple(r[k] for k in ('n', 'child', 'col', 'parent', 'pcol', 'dele', 'upd', 'ok'))
                for r in cur.fetchall()]


class _DbCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE booking_requests, users, interviews, operators, roles, "
                        "study_sessions, backup_versions, waitlist RESTART IDENTITY CASCADE")
        db_operators.seed_builtin_roles()


class TestForeignKeyMap(_DbCase):
    def test_live_foreign_keys_equal_the_registry(self):
        live = {(n, c, col, p, pcol, ("CASCADE" if upd == "c" else None))
                for n, c, col, p, pcol, dele, upd, _ in _live_keys()}
        self.assertEqual(live, set(db_accounts.FOREIGN_KEYS),
                         "a foreign key needs a revision AND an entry in core.db_accounts.FOREIGN_KEYS")
        self.assertEqual(len(db_accounts.FOREIGN_KEYS), 18)

    def test_every_key_restricts_deletes_and_is_validated_on_a_fresh_install(self):
        for n, c, col, p, pcol, dele, upd, validated in _live_keys():
            self.assertEqual(dele, "r", f"{n}: ON DELETE RESTRICT")
            self.assertTrue(validated, f"{n} must be validated on a fresh install")

    def test_every_referencing_column_has_an_index_to_lead_with(self):
        with db._connect() as conn, conn.cursor() as cur:
            for name, child, column, *_ in db_accounts.FOREIGN_KEYS:
                cur.execute(
                    "SELECT 1 FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid "
                    "JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = i.indkey[0] "
                    "WHERE t.relname = %s AND a.attname = %s AND i.indisvalid", (child, column))
                self.assertIsNotNone(cur.fetchone(), f"{name}: {child}.{column} needs an index")

    def test_the_validate_script_reports_a_clean_schema(self):
        mod = _validate_script()
        with mod.connect() as conn:
            states = {i["state"] for i in mod.status(conn)}
        self.assertEqual(states, {"valid"})


class TestParentsAreProtected(_DbCase):
    def _insert(self, sql, params=()):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone() if cur.description else None
            return row[next(iter(row))] if row else None

    def _refused(self, sql, params=()):
        from psycopg.errors import ForeignKeyViolation
        with self.assertRaises(ForeignKeyViolation):
            self._insert(sql, params)

    def test_a_request_with_history_cannot_be_deleted_children_first_works(self):
        rid = self._insert("INSERT INTO booking_requests (dni, first_name, status) "
                           "VALUES ('30111001', 'Ana', 'pendiente') RETURNING id")
        self._insert("INSERT INTO appointment_events (request_id, kind) VALUES (%s, 'creada')", (rid,))
        self._refused("DELETE FROM booking_requests WHERE id=%s", (rid,))
        self._insert("DELETE FROM appointment_events WHERE request_id=%s", (rid,))
        self._insert("DELETE FROM booking_requests WHERE id=%s", (rid,))

    def test_an_event_for_a_request_that_does_not_exist_is_refused(self):
        self._refused("INSERT INTO appointment_events (request_id, kind) VALUES (987654, 'creada')")

    def test_a_request_with_an_interview_a_hold_or_a_waitlist_entry_is_kept(self):
        rid = self._insert("INSERT INTO booking_requests (dni, first_name, status) "
                           "VALUES ('30111002', 'Ana', 'pendiente') RETURNING id")
        self._insert("INSERT INTO interviews (session_key, request_id) VALUES ('k1', %s)", (rid,))
        self._refused("DELETE FROM booking_requests WHERE id=%s", (rid,))
        self._insert("DELETE FROM interviews WHERE request_id=%s", (rid,))
        self._insert("INSERT INTO slot_holds (service_id, day, time, token, expires, request_id) "
                     "VALUES ('s', '2030-01-01', '09:00', 'tok', now() + interval '1 day', %s)", (rid,))
        self._refused("DELETE FROM booking_requests WHERE id=%s", (rid,))
        self._insert("DELETE FROM slot_holds WHERE request_id=%s", (rid,))
        self._insert("INSERT INTO waitlist (request_id) VALUES (%s)", (rid,))
        self._refused("DELETE FROM booking_requests WHERE id=%s", (rid,))

    def test_an_interview_with_a_consultation_is_kept_and_superseded_rows_delete_together(self):
        iid = self._insert("INSERT INTO interviews (session_key) VALUES ('k2') RETURNING id")
        old = self._insert("INSERT INTO consultations (interview_id, doctor_id) VALUES (%s, 'd') RETURNING id", (iid,))
        self._insert("UPDATE consultations SET superseded_at = now() WHERE id=%s", (old,))
        new = self._insert("INSERT INTO consultations (interview_id, doctor_id) VALUES (%s, 'd') RETURNING id", (iid,))
        self._insert("UPDATE consultations SET superseded_by = %s WHERE id=%s", (new, old))
        self._refused("DELETE FROM interviews WHERE id=%s", (iid,))
        self._refused("DELETE FROM consultations WHERE id=%s", (new,))   # the old one points to it
        self._insert("DELETE FROM consultations WHERE interview_id=%s", (iid,))  # both, one statement
        self._insert("DELETE FROM interviews WHERE id=%s", (iid,))

    def test_an_account_with_sessions_dependents_or_uploads_cannot_be_deleted(self):
        db_accounts.create_user("30111003", "Ana", "Paz", "1990-01-01", "F", "1155550000", "Clave-123456")
        db_accounts.create_session("30111003")
        self._refused("DELETE FROM users WHERE dni='30111003'")
        self._insert("DELETE FROM sessions WHERE dni='30111003'")
        self._insert("INSERT INTO dependents (adult_dni, dni, first_name, last_name, birth_date, sex, relationship) "
                     "VALUES ('30111003', '50111003', 'Ito', 'Paz', '2018-01-01', 'M', 'hijo')")
        self._refused("DELETE FROM users WHERE dni='30111003'")
        self._insert("DELETE FROM dependents WHERE adult_dni='30111003'")
        self._insert("DELETE FROM users WHERE dni='30111003'")

    def test_a_session_for_an_account_that_does_not_exist_is_refused(self):
        self._refused("INSERT INTO sessions (token, dni) VALUES ('t-ghost', '99999999')")

    def test_a_role_held_by_an_operator_cannot_be_deleted(self):
        self._insert("INSERT INTO roles (slug, name) VALUES ('custom-fk', 'Custom')")
        db_operators.operator_create("fk_op", "Clave-123456", rol="custom-fk")
        self._refused("DELETE FROM roles WHERE slug='custom-fk'")

    def test_a_study_session_with_events_is_kept(self):
        sid = self._insert("INSERT INTO study_sessions (token_hash, role, locale, consent_at, user_agent_class) "
                           "VALUES ('h', 'patient', 'es', now(), 'mobile') RETURNING id")
        self._insert("INSERT INTO study_events (session_id, task_id, t, kind, path) "
                     "VALUES (%s, 'task', 1, 'click', '/')", (sid,))
        self._refused("DELETE FROM study_sessions WHERE id=%s", (sid,))


class TestCascadeOnUpdate(_DbCase):
    def test_a_dni_correction_is_one_update_and_moves_sessions_dependents_and_uploads(self):
        db_accounts.create_user("30111004", "Ana", "Paz", "1990-01-01", "F", "1155550000", "Clave-123456")
        db_accounts.create_session("30111004")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO reset_tokens (token, dni, expires) VALUES ('rt', '30111004', now() + interval '1 day')")
            cur.execute("INSERT INTO dependents (adult_dni, dni, first_name, last_name, birth_date, sex, relationship) "
                        "VALUES ('30111004', '50111004', 'Ito', 'Paz', '2018-01-01', 'M', 'hijo')")
            cur.execute("INSERT INTO patient_lab_uploads (dni, uploaded_by_dni) VALUES ('30111004', '30111004')")
            cur.execute("UPDATE users SET dni='30111005' WHERE dni='30111004'")   # ONE update
            for table, column in (("sessions", "dni"), ("reset_tokens", "dni"),
                                  ("dependents", "adult_dni"), ("patient_lab_uploads", "uploaded_by_dni")):
                cur.execute(f"SELECT count(*) AS n FROM {table} WHERE {column}='30111005'")
                self.assertEqual(cur.fetchone()["n"], 1, f"{table}.{column} follows the account")
                cur.execute(f"SELECT count(*) AS n FROM {table} WHERE {column}='30111004'")
                self.assertEqual(cur.fetchone()["n"], 0)

    def test_change_dni_still_moves_everything_with_the_database_cascading(self):
        db_accounts.create_user("30111006", "Ana", "Paz", "1990-01-01", "F", "1155550000", "Clave-123456")
        db_accounts.create_session("30111006")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO booking_requests (dni, first_name, status) VALUES ('30111006', 'Ana', 'pendiente')")
        self.assertTrue(db_accounts.change_dni("30111006", "30111007"))
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM sessions WHERE dni='30111007'")
            self.assertEqual(cur.fetchone()["n"], 1)
            cur.execute("SELECT count(*) AS n FROM booking_requests WHERE dni='30111007'")
            self.assertEqual(cur.fetchone()["n"], 1)
            cur.execute("SELECT count(*) AS n FROM users WHERE dni='30111006'")
            self.assertEqual(cur.fetchone()["n"], 0)

    def test_an_operator_rename_moves_its_access_row(self):
        self._operator = db_operators.operator_create("fk_old", "Clave-123456", rol="medico")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO operator_access (username) VALUES ('fk_old') ON CONFLICT DO NOTHING")
        db_operators.operator_rename("fk_old", "fk_new", current_password="Clave-123456")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM operator_access WHERE username='fk_new'")
            self.assertEqual(cur.fetchone()["n"], 1)
            cur.execute("SELECT count(*) AS n FROM operator_access WHERE username='fk_old'")
            self.assertEqual(cur.fetchone()["n"], 0)


class TestRevisionOnExistingData(unittest.TestCase):
    """The revision runs at app start: an installation that already holds orphan
    rows must still boot."""

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        import psycopg
        self.name = _dbtest.run_scoped("sana_turnos_test_fk_up")
        with psycopg.connect(_MAINT_URL, autocommit=True, connect_timeout=5) as c:
            c.execute("DROP DATABASE IF EXISTS %s WITH (FORCE)" % self.name)
            c.execute("CREATE DATABASE %s" % self.name)
        p = urlsplit(_TEST_URL)
        self.url = urlunsplit((p.scheme, p.netloc, "/" + self.name, p.query, p.fragment))
        self.addCleanup(self._drop)

    def _drop(self):
        import psycopg
        with psycopg.connect(_MAINT_URL, autocommit=True, connect_timeout=5) as c:
            c.execute("DROP DATABASE IF EXISTS %s WITH (FORCE)" % self.name)

    def _at_previous_head(self):
        from alembic import command
        command.upgrade(db_schema._alembic_config(self.url), "0031_ai_prompts_text_en")

    def _boot(self):
        db_schema._ensured.clear()
        out = io.StringIO()
        with _env.patched_env(DATABASE_URL=self.url):
            try:
                with contextlib.redirect_stdout(out):
                    db._connect().close()
            finally:
                db.close_pools()
        db_schema._ensured.clear()
        return out.getvalue()

    def _states(self):
        mod = _validate_script()
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(self.url, autocommit=True, row_factory=dict_row) as conn:
            return {i["name"]: i for i in mod.status(conn)}, mod, conn

    def _sql(self, sql):
        import psycopg
        with psycopg.connect(self.url, autocommit=True) as c:
            c.execute(sql)

    def test_clean_data_validates_every_key_in_the_boot(self):
        self._at_previous_head()
        self._sql("INSERT INTO booking_requests (dni, first_name, status) VALUES ('30111008', 'Ana', 'pendiente')")
        self._boot()
        import psycopg
        from psycopg.rows import dict_row
        mod = _validate_script()
        with psycopg.connect(self.url, autocommit=True, row_factory=dict_row) as conn:
            self.assertEqual({i["state"] for i in mod.status(conn)}, {"valid"})

    def test_orphan_rows_do_not_stop_the_boot_the_key_stays_not_valid_and_is_logged(self):
        self._at_previous_head()
        self._sql("INSERT INTO waitlist (request_id) VALUES (424242)")        # no such request
        self._sql("INSERT INTO sessions (token, dni) VALUES ('t-ghost', '99999999')")  # no such account
        log = self._boot()                                                  # must not raise
        import psycopg
        from psycopg.rows import dict_row
        mod = _validate_script()
        with psycopg.connect(self.url, autocommit=True, row_factory=dict_row) as conn:
            states = {i["name"]: i for i in mod.status(conn)}
            self.assertEqual(states["fk_waitlist_request"]["state"], "not_valid")
            self.assertEqual(states["fk_waitlist_request"]["orphans"], 1)
            self.assertEqual(states["fk_sessions_dni"]["state"], "not_valid")
            others = [i["state"] for n, i in states.items() if n not in ("fk_waitlist_request", "fk_sessions_dni")]
            self.assertEqual(set(others), {"valid"})
            self.assertIn("fk_waitlist_request stays NOT VALID", log)
            self.assertNotIn("99999999", log, "the log carries counts, never a DNI")
            # new rows are checked even while the old ones are not:
            from psycopg.errors import ForeignKeyViolation
            with self.assertRaises(ForeignKeyViolation):
                conn.execute("INSERT INTO waitlist (request_id) VALUES (515151)")
            # still not valid after a retry while the orphan exists ...
            self.assertEqual({i["name"] for i in mod.validate(conn) if i["state"] == "not_valid"},
                             {"fk_waitlist_request", "fk_sessions_dni"})
            # ... and valid once the data is fixed (the owner decides how; here: delete the test rows)
            conn.execute("DELETE FROM waitlist WHERE request_id=424242")
            conn.execute("DELETE FROM sessions WHERE token='t-ghost'")
            self.assertEqual({i["state"] for i in mod.validate(conn)}, {"valid"})

    def test_the_revision_is_idempotent(self):
        self._at_previous_head()
        self._boot()
        from alembic import command
        cfg = db_schema._alembic_config(self.url)
        self._sql("DELETE FROM alembic_version")
        self._sql("INSERT INTO alembic_version VALUES ('0031_ai_prompts_text_en')")
        command.upgrade(cfg, "head")   # runs 0028 again over the existing constraints
        import psycopg
        from psycopg.rows import dict_row
        mod = _validate_script()
        with psycopg.connect(self.url, autocommit=True, row_factory=dict_row) as conn:
            self.assertEqual({i["state"] for i in mod.status(conn)}, {"valid"})


if __name__ == "__main__":
    unittest.main()
