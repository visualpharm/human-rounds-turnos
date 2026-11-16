"""The personal-data registry (core/personal_data.py, Ley 25.326 art. 14-16, P1).

The completeness test is the point: it reads the LIVE schema and goes red when
someone adds a table, or a column that holds a DNI / phone / email / name /
birth date / address / IP, without deciding what the data-subject tools do
with it — the same trap `TestChangeDniClosure` closes for the DNI move, for
every kind of identifier.
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", MAIL_ENABLED="0")

from app.core import personal_data  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_catalog  # noqa: E402
import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_personal_data")


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _live_schema():
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(_db.test_url, autocommit=True, connect_timeout=5,
                         row_factory=dict_row) as c, c.cursor() as cur:
        cur.execute("""SELECT c.table_name, c.column_name, c.data_type
                         FROM information_schema.columns c
                         JOIN information_schema.tables t
                           ON t.table_name = c.table_name AND t.table_schema = c.table_schema
                        WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'""")
        schema = {}
        for r in cur.fetchall():
            schema.setdefault(r["table_name"], {})[r["column_name"]] = r["data_type"]
        return schema


class DbCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        db_catalog.catalog_list("centros")  # makes sure the schema exists


class TestRegistryCompleteness(DbCase):
    def test_the_registry_covers_the_live_schema(self):
        self.assertEqual(personal_data.audit(_live_schema()), [])

    def test_a_new_table_nobody_classified_is_red(self):
        schema = _live_schema()
        schema["patient_notes_v2"] = {"id": "bigint", "note": "text"}
        problems = personal_data.audit(schema)
        self.assertTrue(any("patient_notes_v2" in p and "not in REGISTRY" in p for p in problems), problems)

    def test_a_new_dni_column_without_an_entry_is_red(self):
        schema = _live_schema()
        schema["booking_requests"]["guardian_dni"] = "text"
        problems = personal_data.audit(schema)
        self.assertTrue(any("booking_requests.guardian_dni" in p for p in problems), problems)

    def test_a_new_phone_or_email_column_is_red(self):
        for table, column in (("users", "work_phone"), ("waitlist", "backup_email"),
                              ("sessions", "device_contact"), ("catalog", "owner_email")):
            schema = _live_schema()
            schema[table][column] = "text"
            problems = personal_data.audit(schema)
            self.assertTrue(any(f"{table}.{column}" in p for p in problems), (table, problems))

    def test_a_personal_column_in_a_no_personal_table_is_red(self):
        schema = _live_schema()
        schema["day_queue_events"]["patient_dni"] = "text"
        self.assertTrue(any("day_queue_events.patient_dni" in p for p in personal_data.audit(schema)))

    def test_a_new_token_column_would_be_exported_so_it_is_red(self):
        schema = _live_schema()
        schema["interviews"]["share_token"] = "text"
        problems = personal_data.audit(schema)
        self.assertTrue(any("interviews.share_token" in p and "secret" in p for p in problems), problems)

    def test_a_column_the_schema_lost_is_red(self):
        schema = _live_schema()
        del schema["users"]["renaper"]
        self.assertTrue(any("users" in p and "renaper" in p for p in personal_data.audit(schema)))

    def test_a_table_the_schema_lost_is_red(self):
        schema = _live_schema()
        del schema["waitlist"]
        self.assertTrue(any("waitlist" in p and "not in the schema" in p for p in personal_data.audit(schema)))

    def test_a_parent_listed_before_its_child_is_red(self):
        reordered = tuple(sorted(personal_data.REGISTRY,
                                 key=lambda e: 0 if e.table == "booking_requests" else 1))
        with mock.patch.object(personal_data, "REGISTRY", reordered):
            problems = personal_data.audit(_live_schema())
        self.assertTrue(any("AFTER its parent" in p for p in problems), problems)

    def test_a_table_in_two_lists_is_red(self):
        with mock.patch.object(personal_data, "NO_PERSONAL_DATA",
                               {**personal_data.NO_PERSONAL_DATA, "users": "oops"}):
            problems = personal_data.audit(_live_schema())
        self.assertTrue(any("users" in p and "more than one" in p for p in problems), problems)

    def test_the_job_queue_tables_are_not_personal(self):
        schema = _live_schema()
        schema["procrastinate_jobs"] = {"id": "bigint", "args": "jsonb"}
        self.assertEqual(personal_data.audit(schema), [])


class TestRegistryAgreesWithChangeDni(DbCase):
    def test_every_column_change_dni_moves_is_in_the_registry(self):
        registered = {(e.table, c) for e in personal_data.REGISTRY for c in e.columns}
        missing = [tc for tc in db_accounts.DNI_MOVED_COLUMNS if tc not in registered]
        self.assertEqual(missing, [])

    def test_every_column_change_dni_keeps_has_an_entry_for_its_table(self):
        tables = {e.table for e in personal_data.REGISTRY}
        self.assertEqual([t for t, _ in db_accounts.DNI_KEPT_COLUMNS if t not in tables], [])

    def test_every_chat_key_column_change_dni_moves_is_searched_by_key(self):
        keyed = {(e.table, s[1]) for e in personal_data.REGISTRY for s in e.find if s[0] == "key"}
        self.assertEqual([tc for tc in db_accounts.DNI_PREFIXED_KEY_COLUMNS if tc not in keyed], [])

    def test_an_anonymise_entry_blanks_something_or_moves_a_dni_column(self):
        moved_tables = {t for t, _ in db_accounts.DNI_MOVED_COLUMNS}
        for e in personal_data.REGISTRY:
            if e.erase == personal_data.ANONYMISE:
                self.assertTrue(e.blank or e.table in moved_tables, e.label)

    def test_a_disputed_entry_is_never_a_hard_delete_of_clinical_data(self):
        # What counsel may call the clinical record is kept (anonymised) by default.
        clinical = {"interviews", "consultations", "patient_health_profile",
                    "clinical_history_records", "booking_requests"}
        for e in personal_data.REGISTRY:
            if e.table in clinical:
                self.assertNotEqual(e.erase, personal_data.DELETE, e.label)


class TestSelectors(DbCase):
    FULL = personal_data.Subject("20111222", phone10="2255123456", emails=("a@b.org",),
                                 dni_hash="deadbeef")
    BARE = personal_data.Subject("20111222")

    def _run(self, subject):
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(_db.test_url, autocommit=True, row_factory=dict_row) as c, c.cursor() as cur:
            for e in personal_data.REGISTRY:
                sql = f"SELECT count(*) AS n FROM {e.table} WHERE {personal_data.where_sql(e, subject)}"
                cur.execute(sql, subject.params)  # must be valid SQL on the live schema
                self.assertEqual(cur.fetchone()["n"], 0, e.label)

    def test_every_selector_is_valid_sql_on_the_live_schema(self):
        self._run(self.FULL)

    def test_without_a_phone_or_email_those_selectors_match_nothing(self):
        self._run(self.BARE)
        for e in personal_data.REGISTRY:
            if e.part == "whatsapp":
                self.assertEqual(personal_data.where_sql(e, self.BARE), "(FALSE)")

    def test_a_selector_with_a_quote_in_the_value_cannot_break_out(self):
        evil = personal_data.Subject("1' OR '1'='1", phone10="1' OR '1'='1", emails=("x'--",))
        self._run(evil)


if __name__ == "__main__":
    unittest.main()
