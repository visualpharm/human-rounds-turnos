"""The ledger of data-subject requests (core/subject_requests.py, Ley 25.326
arts. 14-16, P3): the deadlines (10 calendar days / 5 working days), the
identity check before anything is released, the «under review» flag, the
six-month rule, the append-only trail and the CLI.
"""
import io
import os
import sys
import unittest
from datetime import date
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", MAIL_ENABLED="0")

from app.core import access_log, subject_requests as sr  # noqa: E402
from app.core import db_catalog  # noqa: E402
import _dbtest  # noqa: E402
import _subject_fixture  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_subject_requests")
P, Q = "20111222", "30222333"


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _load_cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "subject_requests_script", os.path.join(ROOT, "scripts", "subject_requests.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestDeadlines(unittest.TestCase):
    """Pure: no database."""

    def test_access_is_ten_calendar_days(self):
        self.assertEqual(sr.due_date("access", date(2026, 10, 1)), date(2026, 10, 11))
        # weekends and holidays do not stop an access clock
        self.assertEqual(sr.due_date("access", date(2026, 10, 23), holidays={"2026-10-26"}),
                         date(2026, 11, 2))

    def test_correction_and_erasure_are_five_working_days(self):
        # Monday 2026-10-05 + 5 working days = Monday 2026-10-12
        self.assertEqual(sr.due_date("rectification", date(2026, 10, 5), holidays=()), date(2026, 10, 12))
        self.assertEqual(sr.due_date("erasure", date(2026, 10, 5), holidays=()), date(2026, 10, 12))

    def test_received_on_a_friday_counts_from_the_next_working_day(self):
        # Friday 2026-10-09: Mon 12, Tue 13, Wed 14, Thu 15, Fri 16
        self.assertEqual(sr.due_date("erasure", date(2026, 10, 9), holidays=()), date(2026, 10, 16))

    def test_received_on_a_weekend_counts_from_monday(self):
        # Saturday 2026-10-10: Mon 12 .. Fri 16
        self.assertEqual(sr.due_date("erasure", date(2026, 10, 10), holidays=()), date(2026, 10, 16))

    def test_a_holiday_does_not_count(self):
        # Monday 5 + 5 working days, with Wednesday 7 a holiday -> Tuesday 13
        self.assertEqual(sr.due_date("rectification", date(2026, 10, 5), holidays={"2026-10-07"}),
                         date(2026, 10, 13))
        self.assertEqual(sr.add_working_days(date(2026, 10, 5), 5, holidays=[date(2026, 10, 7)]),
                         date(2026, 10, 13))

    def test_the_installations_holidays_are_read_by_default(self):
        with mock.patch("app.modules.turnos.site_holidays.by_date", return_value={"2026-10-07": "x"}):
            self.assertEqual(sr.due_date("erasure", date(2026, 10, 5)), date(2026, 10, 13))

    def test_a_broken_holiday_list_never_blocks_a_request(self):
        with mock.patch("app.modules.turnos.site_holidays.by_date", side_effect=RuntimeError("down")):
            self.assertEqual(sr.due_date("erasure", date(2026, 10, 5)), date(2026, 10, 12))

    def test_unknown_kind_is_refused(self):
        with self.assertRaises(sr.RequestError):
            sr.due_date("deletion", date(2026, 10, 5))

    def test_add_months_clamps_the_day(self):
        self.assertEqual(sr.add_months(date(2026, 8, 31), 6), date(2027, 2, 28))
        self.assertEqual(sr.add_months(date(2026, 10, 25), 6), date(2027, 4, 25))
        self.assertEqual(sr.add_months(date(2026, 7, 15), 6), date(2027, 1, 15))


class DbCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        db_catalog.catalog_list("centros")
        self.fx = _subject_fixture.Fixture(_db.test_url, self)
        self.fx.reset()
        self.fx.execute("TRUNCATE data_subject_requests, data_subject_request_events RESTART IDENTITY")


class TestLedger(DbCase):
    def _open(self, kind="access", dni=P, on=date(2026, 10, 1)):
        return sr.register(kind, dni, "mesa", received_on=on, channel="presencial", holidays=())

    def test_register_stores_the_hash_never_the_dni(self):
        row = self._open()
        self.assertEqual(row["subject_hash"], access_log.dni_hash(P))
        self.assertEqual((row["kind"], row["status"], row["due_on"]), ("access", "received", date(2026, 10, 11)))
        everything = self.fx.scalar("SELECT string_agg(t::text, ' ') FROM data_subject_requests t")
        self.assertFalse(P in everything)
        events = self.fx.scalar("SELECT string_agg(t::text, ' ') FROM data_subject_request_events t")
        self.assertFalse(P in events)

    def test_a_request_keeps_the_day_it_arrived(self):
        row = self._open(on=date(2026, 10, 9))
        self.assertEqual(row["received_on"], date(2026, 10, 9))

    def test_it_needs_a_dni_and_someone_who_registers_it(self):
        with self.assertRaises(sr.RequestError):
            sr.register("access", "abc", "mesa")
        with self.assertRaises(sr.RequestError):
            sr.register("access", P, " ")

    def test_nothing_is_answered_before_the_identity_is_verified(self):
        row = self._open()
        with self.assertRaises(sr.RequestError) as ctx:
            sr.answer(row["id"], "copy handed over", "mesa")
        self.assertIn("identity", str(ctx.exception))
        self.assertEqual(sr.get(row["id"])["status"], "received")
        sr.set_identity_check(row["id"], "DNI shown in person", "mesa")
        sr.answer(row["id"], "copy handed over", "mesa", on=date(2026, 10, 5))
        done = sr.get(row["id"])
        self.assertEqual((done["status"], done["answered_on"], done["decided_by"]),
                         ("answered", date(2026, 10, 5), "mesa"))
        self.assertEqual([e["event"] for e in done["events"]],
                         ["received", "identity_verified", "answered"])

    def test_a_request_whose_identity_fails_is_answered_by_refusing_it(self):
        row = self._open()
        sr.refuse(row["id"], "identity could not be verified", "mesa")
        self.assertEqual(sr.get(row["id"])["status"], "refused")

    def test_a_refusal_must_state_its_basis(self):
        row = self._open("erasure")
        with self.assertRaises(sr.RequestError):
            sr.refuse(row["id"], "  ", "mesa")

    def test_an_answer_says_what_was_done(self):
        row = self._open()
        sr.set_identity_check(row["id"], "in person", "mesa")
        with self.assertRaises(sr.RequestError):
            sr.answer(row["id"], "", "mesa")

    def test_a_closed_request_does_not_reopen(self):
        row = self._open()
        sr.refuse(row["id"], "no", "mesa")
        for call in (lambda: sr.set_identity_check(row["id"], "x", "m"),
                     lambda: sr.answer(row["id"], "x", "m"),
                     lambda: sr.refuse(row["id"], "x", "m"),
                     lambda: sr.start_review(row["id"], "m")):
            with self.assertRaises(sr.RequestError):
                call()

    def test_the_identity_check_needs_a_how(self):
        row = self._open()
        with self.assertRaises(sr.RequestError):
            sr.set_identity_check(row["id"], "", "mesa")

    def test_a_rectification_under_review_flags_the_data(self):
        self.assertFalse(sr.is_under_review(P))
        row = self._open("rectification")
        self.assertFalse(sr.is_under_review(P))
        sr.start_review(row["id"], "mesa", "birth date claimed wrong")
        self.assertTrue(sr.is_under_review(P))
        self.assertFalse(sr.is_under_review(Q))
        sr.set_identity_check(row["id"], "in person", "mesa")
        sr.answer(row["id"], "corrected", "mesa")
        self.assertFalse(sr.is_under_review(P))

    def test_an_access_request_has_no_review_step(self):
        row = self._open("access")
        with self.assertRaises(sr.RequestError):
            sr.start_review(row["id"], "mesa")

    def test_overdue_lists_the_open_ones_past_their_day(self):
        late = self._open(on=date(2026, 10, 1))                   # due 11
        ontime = self._open(on=date(2026, 10, 20))                # due 30
        closed = self._open(on=date(2026, 9, 1))
        sr.refuse(closed["id"], "no", "mesa")
        ids = [r["id"] for r in sr.overdue(date(2026, 10, 25))]
        self.assertEqual(ids, [late["id"]])
        self.assertEqual(sr.overdue(date(2026, 10, 11)), [])      # due today is not late yet
        self.assertEqual({r["id"] for r in sr.list_open()}, {late["id"], ontime["id"]})

    def test_the_six_month_rule(self):
        self.assertIsNone(sr.next_free_access_on(P))
        row = self._open()
        self.assertIsNone(sr.next_free_access_on(P))              # not answered yet
        sr.set_identity_check(row["id"], "in person", "mesa")
        sr.answer(row["id"], "copy", "mesa", on=date(2026, 10, 5))
        self.assertEqual(sr.next_free_access_on(P), date(2027, 4, 5))
        self.assertIsNone(sr.next_free_access_on(Q))
        # a refused access does not start the six months
        other = self._open(dni=Q)
        sr.refuse(other["id"], "no", "mesa", on=date(2026, 10, 6))
        self.assertIsNone(sr.next_free_access_on(Q))

    def test_for_subject_finds_only_their_requests(self):
        mine = self._open(dni=P)
        self._open(dni=Q)
        self.assertEqual([r["id"] for r in sr.for_subject(P)], [mine["id"]])

    def test_the_trail_only_grows(self):
        import psycopg
        row = self._open()
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.fx.execute("UPDATE data_subject_request_events SET detail = 'x' WHERE request_id = %s",
                            (row["id"],))
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.fx.execute("DELETE FROM data_subject_request_events WHERE request_id = %s", (row["id"],))
        self.assertEqual(self.fx.scalar("SELECT count(*) FROM data_subject_request_events"), 1)

    def test_status_and_kind_are_closed_lists(self):
        import psycopg
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.fx.execute("INSERT INTO data_subject_requests (kind, subject_hash, received_on, due_on, "
                            "registered_by) VALUES ('deletion','h','2026-10-01','2026-10-11','x')")

    def test_record_export_is_for_access_requests(self):
        access = self._open("access")
        sr.record_export(access["id"], "tester", "12 rows")
        self.assertIn("export_generated", [e["event"] for e in sr.get(access["id"])["events"]])
        erasure = self._open("erasure")
        with self.assertRaises(sr.RequestError):
            sr.record_export(erasure["id"], "tester")


class TestCli(DbCase):
    def setUp(self):
        super().setUp()
        self.cli = _load_cli()

    def _run(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            code = self.cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_a_request_goes_from_add_to_answer(self):
        code, out, _ = self._run("add", "access", "--dni", P, "--by", "mesa",
                                 "--received", "2026-10-01", "--channel", "email")
        self.assertEqual(code, 0)
        self.assertIn("due 2026-10-11", out)
        self.assertFalse(P in out)
        rid = sr.list_open()[0]["id"]
        code, _, err = self._run("answer", str(rid), "--outcome", "copy", "--by", "mesa")
        self.assertEqual(code, 2)
        self.assertIn("identity", err)
        self.assertEqual(self._run("identity", str(rid), "--how", "in person", "--by", "mesa")[0], 0)
        self.assertEqual(self._run("answer", str(rid), "--outcome", "copy", "--by", "mesa")[0], 0)
        code, out, _ = self._run("show", str(rid))
        self.assertIn("answered", out)
        self.assertIn("identity_verified", out)
        self.assertFalse(P in out)

    def test_list_overdue_exits_one_when_something_is_late(self):
        self._run("add", "access", "--dni", P, "--by", "mesa", "--received", "2026-01-01")
        code, out, _ = self._run("list", "--overdue")
        self.assertEqual(code, 1)
        self.assertIn("OVERDUE", out)
        self.assertEqual(self._run("list")[0], 0)

    def test_export_script_ties_into_the_ledger(self):
        script = _load_export_cli()
        self.fx.seed_person(P, "Pia")
        mine = sr.register("access", P, "mesa", holidays=())
        theirs = sr.register("access", Q, "mesa", holidays=())
        out = os.path.join(self.fx.tmp, "x.zip")
        with mock.patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(script.main(["--dni", P, "--out", out, "--request", str(theirs["id"])]), 2)
        self.assertFalse(os.path.exists(out))
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(script.main(["--dni", P, "--out", out, "--request", str(mine["id"])]), 0)
        self.assertIn("export_generated", [e["event"] for e in sr.get(mine["id"])["events"]])


def _load_export_cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "subject_export_script2", os.path.join(ROOT, "scripts", "subject_export.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
