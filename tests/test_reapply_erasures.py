"""Apply the erasures again to a restored database (core/erasure_reapply.py, scripts/reapply_erasures.py,
Ley 25.326 art. 16, P5).

A restore brings back a person who was erased after the backup. The synthetic person is erased through
the real path (ledger request, `subject_erase.apply`), then "restored": the same person is seeded again
with an account older than the erasure. After the script they are unreachable again, a second run changes
nothing, the dry-run writes nothing, and the report holds counts only.
"""
import contextlib
import io
import json
import os
import stat
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", MAIL_ENABLED="0")

from app.core import erasure_reapply, personal_data, subject_erase, subject_export  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.core import subject_requests as sr  # noqa: E402
import _dbtest  # noqa: E402
import _subject_fixture  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_reapply_erasures")
P, Q = "20111222", "30222333"


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _load_cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "reapply_erasures_script", os.path.join(ROOT, "scripts", "reapply_erasures.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RestoreCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        db_catalog.catalog_list("centros")
        self.fx = _subject_fixture.Fixture(_db.test_url, self)
        self.fx.reset()
        self.photos = os.path.join(self.fx.tmp, "photos")
        patcher = mock.patch("app.cuentas.account_photo.PHOTOS_DIR", self.photos)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.p = self.fx.seed_person(P, "Pia")
        self.q = self.fx.seed_person(Q, "Quimey", phone="2255999888")
        self.fx.execute("UPDATE booking_requests SET status = 'cancelada' WHERE status = 'pendiente'")
        self.back_up()

    def erase(self, mode="anonymise"):
        row = sr.register("erasure", P, "mesa", holidays=())
        sr.set_identity_check(row["id"], "DNI shown in person", "mesa")
        subject_erase.apply(subject_export.resolve_subject(P), mode, row["id"], "counsel, 2026-10-30", "tester")
        return row["id"]

    def _tables(self):
        with self.fx.conn() as c, c.cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='public' AND table_type='BASE TABLE' "
                        "AND table_name NOT IN ('alembic_version','clinical_access_log') "
                        "AND table_name NOT LIKE 'procrastinate%%' ORDER BY 1")
            return [r["table_name"] for r in cur.fetchall()]

    def back_up(self):
        """The backup: a copy of every table, taken before the erasure."""
        self.fx.execute("DROP SCHEMA IF EXISTS bk CASCADE")
        self.fx.execute("CREATE SCHEMA bk")
        for name in self._tables():
            self.fx.execute(f"CREATE TABLE bk.{name} AS TABLE public.{name}")

    def restore(self, keep_ledger=True):
        """The restore: every table goes back to the copy (the person's account is 30 days old). `keep_ledger=False` is the real case, a
        backup older than the erasure holds neither the erasure nor its ledger line; True keeps the
        two ledger tables as they are now (a partial restore)."""
        ledger = ("data_subject_requests", "data_subject_request_events")
        names = [n for n in self._tables() if not (keep_ledger and n in ledger)]
        with self.fx.conn() as c, c.cursor() as cur:
            cur.execute("SET session_replication_role = replica")
            cur.execute("TRUNCATE %s" % ", ".join(names))
            for name in names:
                cur.execute(f"INSERT INTO public.{name} SELECT * FROM bk.{name}")
            cur.execute("UPDATE users SET created = now() - interval '30 days' WHERE dni = %s", (P,))

    def dump(self):
        """Every row of every table as text: the whole state of the database."""
        state = {}
        with self.fx.conn() as c, c.cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY 1")
            for name in [r["table_name"] for r in cur.fetchall()]:
                cur.execute(f"SELECT t::text AS s FROM {name} t ORDER BY 1")
                state[name] = [r["s"] for r in cur.fetchall()]
        return state

    def state_of(self, *needles):
        """Every row of every table that mentions one of `needles`: what must not move for someone else."""
        return {name: [row for row in rows if any(n in row for n in needles)]
                for name, rows in self.dump().items()
                if any(n in row for row in rows for n in needles)}

    def reachable(self, dni=P):
        subject = subject_export.resolve_subject(dni)
        found = []
        with self.fx.conn() as c, c.cursor() as cur:
            for e in personal_data.REGISTRY:
                if e.erase == personal_data.KEEP:
                    continue
                cur.execute(f"SELECT count(*) AS n FROM {e.table} WHERE {personal_data.where_sql(e, subject)}",
                            subject.params)
                if cur.fetchone()["n"]:
                    found.append(e.label)
        return found

    def reapply(self, apply=True, *sources):
        entries = erasure_reapply.merge(sr.erasures(), *sources)
        return erasure_reapply.run(entries, apply=apply)


class TestReapply(RestoreCase):
    def test_a_restored_database_with_an_erased_person_erases_them_again(self):
        self.erase()
        self.restore()
        self.assertTrue(self.reachable(), "the restore brought the person back")
        self.assertEqual(self.fx.scalar("SELECT count(*) FROM users WHERE dni = %s", (P,)), 1)
        report = self.reapply()
        self.assertEqual((report["applied"], report["refused"], report["in_database"]), (1, 0, 1))
        self.assertEqual(self.reachable(), [])
        self.assertEqual(self.fx.scalar("SELECT count(*) FROM users WHERE dni = %s", (P,)), 0)
        self.assertEqual(self.fx.scalar("SELECT count(*) FROM users WHERE first_name IS NOT NULL "
                                        "AND first_name LIKE %s", ("%Pia%",)), 0)
        self.assertEqual(self.fx.scalar("SELECT count(*) FROM sessions WHERE dni = %s", (P,)), 0)

    def test_a_second_run_changes_nothing(self):
        self.erase()
        self.restore()
        self.reapply()
        before = self.dump()
        report = self.reapply()
        self.assertEqual(self.dump(), before)
        self.assertEqual((report["applied"], report["to_apply"], report["in_database"]), (0, 0, 0))

    def test_block_mode_is_applied_and_a_second_run_changes_nothing(self):
        self.erase("block")
        self.restore()
        self.assertEqual(self.reapply()["applied"], 1)
        self.assertEqual(self.fx.scalar("SELECT active FROM users WHERE dni = %s", (P,)), False)
        self.assertIsNone(self.fx.scalar("SELECT feed_token FROM users WHERE dni = %s", (P,)))
        before = self.dump()
        report = self.reapply()
        self.assertEqual(self.dump(), before)
        self.assertEqual((report["in_database"], report["already_applied"], report["applied"]), (1, 1, 0))

    def test_the_dry_run_writes_nothing_and_counts(self):
        self.erase()
        self.restore()
        before = self.dump()
        report = self.reapply(apply=False)
        self.assertEqual(self.dump(), before)
        self.assertEqual((report["to_apply"], report["applied"]), (1, 0))
        self.assertGreater(report["rows"]["users"], 0)
        self.assertTrue(self.reachable(), "still there: nothing was written")

    def test_the_dry_run_is_a_read_only_transaction(self):
        import psycopg
        self.erase()
        self.restore()
        real = subject_erase.pending

        def writing(cur, subject, mode):
            cur.execute("UPDATE users SET first_name = 'x' WHERE dni = %s", (P,))
            return real(cur, subject, mode)

        with mock.patch.object(subject_erase, "pending", writing):
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                self.reapply(apply=False)

    def test_the_other_person_is_not_touched(self):
        photo_q = self.fx.photo(self.photos, Q, b"other photo")
        self.erase()
        self.restore()
        before = self.state_of(Q, "Quimey")
        self.assertTrue(before)
        self.reapply()
        self.assertEqual(self.state_of(Q, "Quimey"), before)
        self.assertTrue(os.path.exists(photo_q))
        self.assertTrue(self.reachable(Q))

    def test_the_tombstones_of_a_file_work_when_the_restore_lost_the_ledger(self):
        self.erase()
        path = os.path.join(self.fx.tmp, "erasures.json")
        self.assertEqual(erasure_reapply.export_file(path), 1)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.restore(keep_ledger=False)  # the backup holds neither the erasure nor its ledger line
        self.assertEqual(sr.erasures(), [])
        report = erasure_reapply.run(erasure_reapply.read_file(path), apply=True)
        self.assertEqual((report["applied"], report["ledger_events_written"]), (1, 0))
        self.assertEqual(self.reachable(), [])
        self.assertTrue(self.reachable(Q))

    def test_the_ledger_of_the_restored_database_gets_a_line(self):
        request_id = self.erase()
        self.restore()
        report = self.reapply()
        self.assertEqual(report["ledger_events_written"], 1)
        events = [e["event"] for e in sr.get(request_id)["events"]]
        self.assertEqual(events[-1], "erasure_reapplied")
        self.assertNotIn(P, json.dumps(sr.get(request_id)["events"], default=str))

    def test_an_account_registered_after_the_erasure_is_a_new_person(self):
        self.erase()
        self.restore()
        self.fx.execute("UPDATE users SET created = now() + interval '1 day' WHERE dni = %s", (P,))  # a new account
        before = self.dump()
        report = self.reapply()
        self.assertEqual((report["skipped_registered_after"], report["applied"]), (1, 0))
        self.assertEqual(self.dump(), before)

    def test_a_person_with_a_live_appointment_is_not_touched_unless_told(self):
        self.erase()
        self.restore()
        self.fx.insert("booking_requests", dni=P, service_id="lab", center_slug="c", status="pendiente")
        before = self.dump()
        for apply in (False, True):
            report = self.reapply(apply=apply)
            self.assertEqual((report["blocked"], report["with_live_blockers"], report["applied"],
                              report["to_apply"]), (1, 1, 0, 0))
            self.assertEqual(self.dump(), before)
        report = erasure_reapply.run(sr.erasures(), apply=True, despite_blockers=True)
        self.assertEqual((report["applied"], report["blocked"], report["with_live_blockers"]), (1, 0, 1))
        self.assertEqual(self.reachable(), [])

    def test_a_pending_hsi_intent_blocks_too(self):
        self.erase()
        self.restore()
        self.fx.insert("hsi_outbox", entity_type="patient", entity_id="1", operation="create",
                       payload={"dni": P}, idempotency_key="ik-p", state="pending")
        self.assertEqual(self.reapply(apply=False)["blocked"], 1)

    def test_a_refusal_rolls_the_person_back_and_the_others_go_on(self):
        self.erase()
        self.restore()
        with mock.patch.object(subject_erase, "_leftovers", return_value=["users"]):
            report = self.reapply()
        self.assertEqual((report["refused"], report["applied"]), (1, 0))
        self.assertEqual(self.fx.scalar("SELECT count(*) FROM users WHERE dni = %s", (P,)), 1)

    def test_files_that_came_back_with_the_restore_are_removed(self):
        self.erase()
        self.restore()
        photo = self.fx.photo(self.photos, P)
        report = self.reapply()
        self.assertFalse(os.path.exists(photo))
        self.assertGreaterEqual(report["files_removed"], 1)
        self.assertEqual(report["files_failed"], 0)

    def test_the_report_holds_counts_and_no_person(self):
        self.erase()
        self.restore()
        text = json.dumps(self.reapply(), sort_keys=True)
        for needle in (P, Q, self.p["name"], self.p["email"], "Pia", sr.erasures()[0]["subject_hash"]):
            self.assertNotIn(needle, text)

    def test_an_entry_without_a_known_mode_is_reported_not_applied(self):
        self.erase()
        self.restore()
        entries = [dict(e, mode="shred") for e in sr.erasures()]
        report = erasure_reapply.run(entries, apply=True)
        self.assertEqual((report["unreadable_entries"], report["applied"]), (1, 0))
        self.assertTrue(self.reachable())

    def test_the_erasure_that_follows_the_account_decides_the_mode(self):
        # anonymised, registered again, blocked later: a backup older than the first erasure holds
        # the first person, whose erasure was the anonymisation
        self.erase()
        self.restore()
        h = sr.erasures()[0]["subject_hash"]
        entries = [{"subject_hash": h, "mode": "anonymise", "erased_at": _ago(10)},
                   {"subject_hash": h, "mode": "block", "erased_at": _ago(1)}]
        report = erasure_reapply.run(entries, apply=True)
        self.assertEqual(report["applied"], 1)
        self.assertEqual(self.fx.scalar("SELECT count(*) FROM users WHERE dni = %s", (P,)), 0)

    def test_merge_keeps_every_erasure_of_a_person_once(self):
        a = {"subject_hash": "h", "mode": "block", "erased_at": "2026-01-01T00:00:00+00:00"}
        b = {"subject_hash": "h", "mode": "anonymise", "erased_at": "2026-06-01T00:00:00+00:00"}
        self.assertEqual(erasure_reapply.merge([b], [a, dict(a)]), [a, b])

    def test_a_person_known_only_by_a_chat_key_is_found(self):
        self.erase()
        self.fx.insert("chat_messages", channel="web", key=f"dni:{P}", role="user", content={"text": "hola"})
        report = self.reapply()
        self.assertEqual((report["in_database"], report["applied"]), (1, 1))
        self.assertEqual(self.fx.scalar("SELECT count(*) FROM chat_messages WHERE key = %s", (f"dni:{P}",)), 0)

    def test_an_unreadable_instant_is_counted_not_guessed(self):
        self.erase()
        self.restore()
        entries = [dict(e, erased_at="yesterday") for e in sr.erasures()]
        report = erasure_reapply.run(entries, apply=True)
        self.assertEqual((report["unreadable_entries"], report["applied"]), (1, 0))
        self.assertTrue(self.reachable())

    def test_a_tombstone_file_of_another_key_is_refused(self):
        path = os.path.join(self.fx.tmp, "other-key.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"format": 1, "key_id": "not-this-key", "erasures": []}, f)
        with self.assertRaises(ValueError):
            erasure_reapply.read_file(path)

    def test_a_tombstone_file_of_another_version_is_refused(self):
        path = os.path.join(self.fx.tmp, "bad.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"format": 99, "key_id": erasure_reapply.key_id(), "erasures": []}, f)
        with self.assertRaises(ValueError):
            erasure_reapply.read_file(path)


class TestScript(RestoreCase):
    def run_cli(self, *argv):
        cli = _load_cli()
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_dry_run_by_default_then_apply_prints_counts_only(self):
        self.erase()
        self.restore()
        before = self.dump()
        code, out, _ = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(self.dump(), before)
        self.assertIn("dry-run: nothing written", out)
        code, out, _ = self.run_cli("--apply", "--operator", "tester")
        self.assertEqual(code, 0)
        self.assertEqual(self.reachable(), [])
        for needle in (P, self.p["name"], self.p["email"], sr.erasures()[0]["subject_hash"][:8]):
            self.assertNotIn(needle, out)
        again = self.dump()
        code, out, _ = self.run_cli("--apply")
        self.assertEqual((code, self.dump()), (0, again))

    def test_export_writes_an_owner_only_file_and_never_overwrites(self):
        self.erase()
        path = os.path.join(self.fx.tmp, "erasures.json")
        code, out, _ = self.run_cli("--export", path)
        self.assertEqual(code, 0)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path, encoding="utf-8") as f:
            self.assertNotIn(P, f.read())
        code, _, err = self.run_cli("--export", path)
        self.assertEqual(code, 2)
        self.assertNotIn(P, err)

    def test_an_unreadable_file_is_a_refusal(self):
        code, _, err = self.run_cli("--from-file", os.path.join(self.fx.tmp, "missing.json"))
        self.assertEqual(code, 2)
        self.assertIn("tombstone file", err)

    def test_a_blocked_person_is_exit_code_2_on_apply(self):
        self.erase()
        self.restore()
        self.fx.insert("booking_requests", dni=P, service_id="lab", center_slug="c", status="pendiente")
        self.assertEqual(self.run_cli()[0], 0)
        code, out, _ = self.run_cli("--apply")
        self.assertEqual(code, 2)
        self.assertIn("blocked", out)
        self.assertEqual(self.run_cli("--apply", "--despite-blockers")[0], 0)
        self.assertEqual(self.reachable(), [])

    def test_a_new_export_keeps_the_lines_of_the_old_file_after_a_recovery(self):
        self.erase()
        old = os.path.join(self.fx.tmp, "old.json")
        self.assertEqual(self.run_cli("--export", old)[0], 0)
        self.restore(keep_ledger=False)
        self.assertEqual(self.run_cli("--apply", "--from-file", old)[0], 0)
        bare = os.path.join(self.fx.tmp, "bare.json")
        self.assertEqual(self.run_cli("--export", bare)[0], 0)
        self.assertEqual(erasure_reapply.read_file(bare), [], "the recovered ledger does not hold the line")
        kept = os.path.join(self.fx.tmp, "kept.json")
        self.assertEqual(self.run_cli("--from-file", old, "--export", kept)[0], 0)
        self.assertEqual(len(erasure_reapply.read_file(kept)), 1)


if __name__ == "__main__":
    unittest.main()
