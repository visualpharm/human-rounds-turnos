"""Erase / anonymise one person (core/subject_erase.py, Ley 25.326 art. 16, P4).

A synthetic person with a row in every registry entry and a second person whose
rows must not move. After an erasure the first is unreachable by DNI, phone and
email, what may be the care record stays WITHOUT them under a random key, the
access log is untouched, and the whole thing is one transaction.
"""
import io
import json
import os
import sys
import unittest
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", MAIL_ENABLED="0")

from app.core import access_log, personal_data, subject_erase, subject_export  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.core import subject_requests as sr  # noqa: E402
import _dbtest  # noqa: E402
import _subject_fixture  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_subject_erase")
P, Q = "20111222", "30222333"
KEY_COLUMNS = (("users", "dni"), ("booking_requests", "dni"), ("booking_requests", "requester_dni"),
               ("interviews", "dni"), ("patient_health_profile", "dni"),
               ("attendance_events", "dni"), ("clinical_history_records", "dni"),
               ("ai_costs", "user_dni"), ("patient_lab_uploads", "dni"),
               ("patient_lab_uploads", "uploaded_by_dni"))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _load_cli():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "subject_erase_script", os.path.join(ROOT, "scripts", "subject_erase.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ErasureCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        db_catalog.catalog_list("centros")
        self.fx = _subject_fixture.Fixture(_db.test_url, self)
        self.fx.reset()
        self.photos = os.path.join(self.fx.tmp, "photos")
        self.p = self.fx.seed_person(P, "Pia")
        self.q = self.fx.seed_person(Q, "Quimey", phone="2255999888")
        self.photo_p = self.fx.photo(self.photos, P)
        self.photo_q = self.fx.photo(self.photos, Q, b"other photo")
        self.referral_p = os.path.join(self.fx.tmp, "orden-Pia.pdf")
        self.referral_q = os.path.join(self.fx.tmp, "orden-Quimey.pdf")
        patcher = mock.patch("app.cuentas.account_photo.PHOTOS_DIR", self.photos)
        patcher.start()
        self.addCleanup(patcher.stop)
        # the live appointments of the fixture would block an erasure on purpose
        self.fx.execute("UPDATE booking_requests SET status = 'cancelada' WHERE status = 'pendiente'")

    def subject(self, dni=P, **kw):
        return subject_export.resolve_subject(dni, **kw)

    def request(self, dni=P, verified=True, kind="erasure"):
        row = sr.register(kind, dni, "mesa", holidays=())
        if verified:
            sr.set_identity_check(row["id"], "DNI shown in person", "mesa")
        return row["id"]

    def erase(self, mode="anonymise", subject=None, request_id=None, authorised_by="counsel, 2026-10-30"):
        return subject_erase.apply(subject or self.subject(), mode, request_id or self.request(),
                                   authorised_by, "tester")

    def dump(self, dni):
        _, blob = subject_export.export_to_bytes(self.subject(dni))
        z = zipfile.ZipFile(io.BytesIO(blob))
        return {n: z.read(n) for n in z.namelist() if n != "manifest.json"}

    def count(self, sql, params=None):
        return self.fx.scalar(sql, params)

    def row(self, sql, params=None):
        with self.fx.conn() as c, c.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()


class TestPlan(ErasureCase):
    def test_a_dry_run_writes_nothing_and_counts_every_place(self):
        before = self.fx.table_counts()
        plan = subject_erase.plan(self.subject())
        self.assertEqual(self.fx.table_counts(), before)
        self.assertEqual([s["entry"] for s in plan["steps"]], [e.label for e in personal_data.REGISTRY])
        rows = {s["entry"]: s["rows"] for s in plan["steps"]}
        for e in personal_data.REGISTRY:
            if e.erase != personal_data.KEEP:
                self.assertGreaterEqual(rows[e.label], 1, e.label)
        self.assertTrue(plan["not_erased"])
        self.assertEqual(plan["files"], {"referrals": 1, "profile_photo": 1})

    def test_the_plan_names_the_live_appointment_that_blocks(self):
        self.assertEqual(subject_erase.plan(self.subject())["blockers"], [])
        self.fx.insert("booking_requests", dni=P, service_id="lab", center_slug="c", status="pendiente")
        blockers = subject_erase.plan(self.subject())["blockers"]
        self.assertEqual(len(blockers), 1)
        self.assertIn("live appointments", blockers[0])

    def test_the_plan_is_a_read_only_transaction(self):
        import psycopg
        real = subject_erase._count

        def writing(cur, entry, subject, extra=""):
            cur.execute("UPDATE users SET first_name = 'x' WHERE dni = %s", (P,))
            return real(cur, entry, subject, extra)

        with mock.patch.object(subject_erase, "_count", writing):
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                subject_erase.plan(self.subject())

    def test_block_mode_plans_less(self):
        anon = {s["entry"]: s["action"] for s in subject_erase.plan(self.subject())["steps"]}
        block = {s["entry"]: s["action"] for s in subject_erase.plan(self.subject(), "block")["steps"]}
        self.assertEqual(block["interviews"], "revoke")
        self.assertEqual(block["users"], "revoke")
        self.assertEqual(block["booking_requests"], "revoke")
        self.assertEqual(block["consultations"], "keep")
        self.assertEqual(block["hsi_patients"], "keep")
        self.assertEqual(block["sessions"], "delete")
        self.assertEqual(anon["hsi_patients"], "delete")
        self.assertEqual(block["patient_lab_uploads"], "keep")


class TestAnonymise(ErasureCase):
    def test_the_person_is_unreachable_afterwards(self):
        self.erase()
        subject = self.subject()
        with self.fx.conn() as c, c.cursor() as cur:
            for e in personal_data.REGISTRY:
                if e.erase == personal_data.KEEP:
                    continue
                cur.execute(f"SELECT count(*) AS n FROM {e.table} WHERE {personal_data.where_sql(e, subject)}",
                            subject.params)
                self.assertEqual(cur.fetchone()["n"], 0, e.label)
        # (the surname is not searched: the fixture's dependent shares it, and a dependent is
        # another person whose request this erasure leaves alone)
        for needle in (self.p["name"], self.p["email"], "2255 123456", P):
            hits = self.count(
                "SELECT count(*) FROM (SELECT t::text AS s FROM users t UNION ALL "
                "SELECT t::text FROM booking_requests t UNION ALL SELECT t::text FROM interviews t "
                "UNION ALL SELECT t::text FROM dependents t UNION ALL SELECT t::text FROM waitlist t "
                "UNION ALL SELECT t::text FROM attendance_events t) x WHERE s LIKE %s",
                (f"%{needle}%",))
            # P's DNI is also the *dependent's* adult_dni nowhere now; Q's rows never carry it
            self.assertEqual(hits, 0, needle)

    def test_what_may_be_the_care_record_stays_without_the_person(self):
        self.erase()
        self.assertEqual(self.count("SELECT count(*) FROM interviews WHERE summary = 'resumen Pia'"), 1)
        self.assertEqual(self.count("SELECT count(*) FROM consultations WHERE notes = 'notes Pia'"), 1)
        self.assertEqual(self.count("SELECT count(*) FROM patient_health_profile "
                                    "WHERE datos->>'alergias' = 'alergia Pia'"), 1)
        self.assertEqual(self.count("SELECT count(*) FROM clinical_history_records "
                                    "WHERE record->>'x' = 'hist Pia'"), 1)
        self.assertEqual(self.count("SELECT count(*) FROM chat_messages WHERE key = 'interview:Pia'"), 1)
        self.assertEqual(self.count("SELECT count(*) FROM attendance_events WHERE dni LIKE 'ANON-%'"), 1)

    def test_the_kept_rows_hang_from_one_random_key(self):
        self.erase()
        keys = set()
        for table, column in KEY_COLUMNS:
            for dni in (self.fx.scalar(f"SELECT array_agg(DISTINCT {column}) FROM {table} "
                                       f"WHERE {column} LIKE 'ANON-%'") or []):
                keys.add(dni)
        self.assertEqual(len(keys), 1, keys)
        (key,) = keys
        self.assertRegex(key, r"^ANON-[0-9a-f]{16}$")
        # and the Q rows still hang from Q
        self.assertEqual(self.count("SELECT count(*) FROM interviews WHERE dni = %s", (Q,)), 1)

    def test_two_erasures_get_two_different_keys(self):
        self.erase()
        self.fx.seed_person("27000111", "Gia", phone="2255000111")
        self.erase(subject=self.subject("27000111"), request_id=self.request("27000111"))
        self.assertEqual(self.count("SELECT count(DISTINCT dni) FROM users WHERE dni LIKE 'ANON-%'"), 2)

    def test_the_account_is_emptied_and_cannot_log_in(self):
        self.erase()
        u = self.row("SELECT * FROM users WHERE dni LIKE 'ANON-%'")
        self.assertFalse(u["active"])
        for col in ("first_name", "last_name", "birth_date", "sex", "contact", "email", "address",
                    "coverage", "notifications", "renaper", "portal_user_id", "pass_hash",
                    "feed_token", "feed_token_hint", "mcp_token", "mcp_token_hint"):
            self.assertIsNone(u[col], col)
        self.assertEqual(self.count("SELECT count(*) FROM sessions WHERE dni = %s", (P,)), 0)

    def test_the_booking_copy_of_name_contact_and_note_is_gone_but_the_ids_stay(self):
        self.erase()
        b = self.row("SELECT * FROM booking_requests WHERE dni LIKE 'ANON-%'")
        for col in ("first_name", "last_name", "birth_date", "sex", "contact", "patient_note",
                    "rejection_reason", "ai_json", "confirm_token", "idem_key", "referral_path",
                    "referral_mime"):
            self.assertIsNone(b[col], col)
        self.assertEqual((b["service_id"], b["status"], b["hsi_patient_id"]), ("lab", "aprobada", "hsi-Pia"))
        self.assertEqual(b["requester_dni"], b["dni"])
        i = self.row("SELECT entered_name, entered_surname, patient_token FROM interviews "
                     "WHERE dni LIKE 'ANON-%'")
        self.assertEqual(tuple(i.values()), (None, None, None))

    def test_a_request_made_for_a_dependent_keeps_the_dependent_and_loses_the_requester(self):
        self.erase()
        row = self.row("SELECT dni, requester_dni FROM booking_requests WHERE first_name = 'HijoPia'")
        self.assertEqual(row["dni"], "9" + P[1:])
        self.assertTrue(row["requester_dni"].startswith("ANON-"))

    def test_the_lab_bytes_and_text_go_the_stub_stays(self):
        self.erase()
        lab = self.row("SELECT * FROM patient_lab_uploads WHERE dni LIKE 'ANON-%'")
        self.assertIsNone(lab["archivo"])
        self.assertIsNone(lab["texto"])
        self.assertIsNone(lab["filename"])
        self.assertIsNotNone(lab["deleted"])

    def test_the_hsi_mirrors_go_and_a_confirmed_payload_is_emptied(self):
        self.erase()
        for table in ("hsi_patients", "hsi_appointments", "hsi_checkpoints", "hsi_inbox",
                      "hsi_sync_conflicts"):
            self.assertEqual(self.count(f"SELECT count(*) FROM {table} t WHERE t::text LIKE '%Pia%'"), 0, table)
        out = self.row("SELECT payload, hsi_result, last_error FROM hsi_outbox "
                       "WHERE idempotency_key = 'idem-ob-Pia'")
        self.assertEqual((out["payload"], out["hsi_result"], out["last_error"]), ({}, None, None))

    def test_conversations_caches_tokens_and_links_go_and_only_the_other_persons_remain(self):
        self.erase()
        for table in ("sessions", "reset_tokens", "otp_codes", "otp_verified", "waitlist",
                      "appointment_offers", "slot_holds", "dependents", "chat_summaries",
                      "chat_compactions", "chat_handoffs", "patient_summaries",
                      "lab_report_analysis", "searches"):
            self.assertEqual(self.count(f"SELECT count(*) FROM {table}"), 1, table)  # Q's
        self.assertEqual(self.count("SELECT count(*) FROM chat_messages WHERE channel IN ('web', 'whatsapp')"), 3)  # Q's

    def test_the_files_go_and_the_other_persons_stay(self):
        report = self.erase()
        self.assertEqual(report["files"], {"removed": 2, "failed": []})
        self.assertFalse(os.path.exists(self.photo_p))
        self.assertFalse(os.path.exists(self.referral_p))
        self.assertTrue(os.path.exists(self.photo_q))
        self.assertTrue(os.path.exists(self.referral_q))

    def test_a_file_that_cannot_be_removed_is_reported_not_hidden(self):
        real = os.remove

        def refusing(path):
            if path.endswith("orden-Pia.pdf"):
                raise PermissionError("read-only volume")
            return real(path)

        with mock.patch("os.remove", refusing):
            report = self.erase()
        self.assertEqual(report["files"]["failed"], ["orden-Pia.pdf"])
        events = [e for r in sr.list_open() for e in sr.get(r["id"])["events"] if e["event"] == "note"]
        self.assertTrue(any("orden-Pia.pdf" in e["detail"] for e in events))

    def test_the_other_person_is_untouched(self):
        before = self.dump(Q)
        self.erase()
        self.assertEqual(self.dump(Q), before)

    def test_the_access_log_is_untouched(self):
        access_log.record({"usuario": "mesa", "rol": "admisionista"}, "ficha", P)
        mine = self.count("SELECT count(*) FROM clinical_access_log WHERE dni_hash = %s",
                          (access_log.dni_hash(P),))
        total = self.count("SELECT count(*) FROM clinical_access_log")
        self.erase()
        self.assertEqual(self.count("SELECT count(*) FROM clinical_access_log WHERE dni_hash = %s",
                                    (access_log.dni_hash(P),)), mine)
        self.assertEqual(self.count("SELECT count(*) FROM clinical_access_log"), total)

    def test_the_ledger_remembers_it_without_the_dni(self):
        rid = self.request()
        self.erase(request_id=rid)
        events = sr.get(rid)["events"]
        self.assertIn("erased", [e["event"] for e in events])
        text = json.dumps([[e["actor"], e["event"], e["detail"]] for e in events])
        self.assertFalse(P in text)
        self.assertIn("counsel", text)
        self.assertEqual(sr.erased_subject_hashes(), [access_log.dni_hash(P)])
        self.assertEqual(sr.get(rid)["status"], "received")  # answering the person is a human step

    def test_a_new_person_who_registers_with_that_dni_inherits_nothing(self):
        self.erase()
        self.fx.insert("users", dni=P, first_name="Nuevo")
        for table, column in db_accounts.DNI_MOVED_COLUMNS:
            if table != "users":
                self.assertEqual(self.count(f"SELECT count(*) FROM {table} WHERE {column} = %s", (P,)), 0,
                                 f"{table}.{column}")
        self.assertEqual(self.count("SELECT count(*) FROM chat_messages WHERE key LIKE %s", (f"dni:{P}%",)), 0)

    def test_the_steps_run_children_first(self):
        order = [s["entry"] for s in self.erase()["steps"]]
        self.assertEqual(order, [e.label for e in personal_data.REGISTRY if e.label in order])
        self.assertLess(order.index("appointment_offers"), order.index("waitlist"))
        self.assertLess(order.index("interviews"), order.index("booking_requests"))
        self.assertLess(order.index("hsi_appointments"), order.index("hsi_patients"))

    def test_it_is_one_transaction_a_failure_halfway_leaves_everything(self):
        rid = self.request()
        before = self.fx.table_counts()
        real = subject_erase._run_step
        reached = []

        def failing(cur, entry, action, subject):
            reached.append(entry.label)
            if entry.table == "booking_requests":
                raise RuntimeError("disk on fire")
            return real(cur, entry, action, subject)

        with mock.patch.object(subject_erase, "_run_step", failing):
            with self.assertRaises(RuntimeError):
                self.erase(request_id=rid)
        self.assertGreater(len(reached), 5)  # it did get going
        self.assertEqual(self.fx.table_counts(), before)
        self.assertEqual(self.count("SELECT count(*) FROM users WHERE first_name = %s", (self.p["name"],)), 1)
        self.assertTrue(os.path.exists(self.photo_p) and os.path.exists(self.referral_p))

    def test_a_leftover_the_steps_missed_rolls_everything_back(self):
        rid = self.request()
        before = self.fx.table_counts()
        real = subject_erase._run_step

        def forgetting(cur, entry, action, subject):
            if entry.table == "hsi_inbox":
                return 0  # a registry bug: this one is never erased
            return real(cur, entry, action, subject)

        with mock.patch.object(subject_erase, "_run_step", forgetting):
            with self.assertRaises(subject_erase.ErasureRefused) as ctx:
                self.erase(request_id=rid)
        self.assertIn("hsi_inbox", str(ctx.exception))
        self.assertEqual(self.fx.table_counts(), before)
        self.assertTrue(os.path.exists(self.photo_p))

    def test_the_whatsapp_thread_of_a_shared_phone_is_not_deleted(self):
        self.fx.insert("users", dni="40555666", contact="+54 9 2255 123456", first_name="Otro")
        subject = self.subject()
        self.assertEqual(subject.phone10, "")
        self.erase(subject=subject)
        self.assertEqual(self.count("SELECT count(*) FROM chat_messages WHERE channel = 'whatsapp' "
                                    "AND content->>'text' = 'wa Pia'"), 1)

    def test_a_dependents_lab_file_survives_its_guardians_erasure(self):
        child = "9" + P[1:]
        self.fx.insert("patient_lab_uploads", dni=child, uploaded_by_dni=P, filename="hijo.pdf",
                       mime="application/pdf", archivo=b"CHILD-LAB", texto="texto del hijo")
        self.erase()
        row = self.row("SELECT * FROM patient_lab_uploads WHERE dni = %s", (child,))
        self.assertEqual((bytes(row["archivo"]), row["texto"], row["filename"]),
                         (b"CHILD-LAB", "texto del hijo", "hijo.pdf"))
        self.assertTrue(row["uploaded_by_dni"].startswith("ANON-"))

    def test_a_childs_waiting_list_entry_with_the_parents_email_survives(self):
        self.fx.insert("waitlist", dni="9" + P[1:], email=self.p["email"], service_id="lab",
                       center_slug="c")
        self.erase()
        self.assertEqual(self.count("SELECT count(*) FROM waitlist WHERE dni = %s", ("9" + P[1:],)), 1)

    def test_one_request_is_one_erasure(self):
        rid = self.request()
        self.erase(request_id=rid)
        self.fx.insert("users", dni=P, first_name="Nuevo")  # someone registers with that DNI later
        with self.assertRaises(subject_erase.ErasureRefused) as ctx:
            self.erase(request_id=rid)
        self.assertIn("already carried out", str(ctx.exception))
        self.assertEqual(self.count("SELECT first_name FROM users WHERE dni = %s", (P,)), "Nuevo")

    def test_a_blank_that_silently_missed_rolls_everything_back(self):
        rid = self.request()
        before = self.fx.table_counts()
        real = subject_erase._run_step

        def forgetting(cur, entry, action, subject):
            if entry.table == "booking_requests":
                return 0  # the copy of the name is never emptied
            return real(cur, entry, action, subject)

        with mock.patch.object(subject_erase, "_run_step", forgetting):
            with self.assertRaises(subject_erase.ErasureRefused) as ctx:
                self.erase(request_id=rid)
        self.assertIn("personal fields left", str(ctx.exception))
        self.assertIn("booking_requests", str(ctx.exception))
        self.assertEqual(self.fx.table_counts(), before)
        self.assertEqual(self.count("SELECT count(*) FROM booking_requests WHERE first_name = %s",
                                    (self.p["name"],)), 1)

    def test_a_ledger_note_that_fails_after_the_commit_does_not_lose_the_report(self):
        with mock.patch.object(sr, "note", side_effect=RuntimeError("ledger down")):
            report = self.erase()
        self.assertTrue(report["files"]["ledger_note_failed"])
        self.assertEqual(self.count("SELECT count(*) FROM users WHERE dni = %s", (P,)), 0)

    def test_erasing_the_same_person_twice_changes_nothing_more(self):
        self.erase()
        before = self.fx.table_counts()
        self.erase()
        after = self.fx.table_counts()
        for table in ("data_subject_requests", "data_subject_request_events"):
            before.pop(table), after.pop(table)
        self.assertEqual(after, before)

    def test_an_account_less_person_with_leftovers_is_erased_too(self):
        self.fx.drop_account(P)
        self.erase()
        self.assertEqual(self.count("SELECT count(*) FROM interviews WHERE dni = %s", (P,)), 0)


class TestBlock(ErasureCase):
    def test_the_derived_data_goes_the_account_is_revoked_the_records_stay_under_the_dni(self):
        report = self.erase("block")
        self.assertEqual(report["mode"], "block")
        u = self.row("SELECT * FROM users WHERE dni = %s", (P,))
        self.assertFalse(u["active"])
        self.assertEqual(u["first_name"], self.p["name"])  # restricted, not anonymised
        for col in ("pass_hash", "feed_token", "feed_token_hint", "mcp_token", "mcp_token_hint"):
            self.assertIsNone(u[col], col)
        self.assertEqual(self.count("SELECT count(*) FROM sessions WHERE dni = %s", (P,)), 0)
        self.assertEqual(self.count("SELECT count(*) FROM interviews WHERE dni = %s", (P,)), 1)
        self.assertIsNone(self.row("SELECT patient_token FROM interviews WHERE dni = %s", (P,))["patient_token"])
        self.assertIsNone(self.row("SELECT confirm_token FROM booking_requests WHERE dni = %s", (P,))["confirm_token"])
        self.assertEqual(self.count("SELECT count(*) FROM booking_requests WHERE dni = %s "
                                    "AND first_name = %s", (P, self.p["name"])), 1)
        self.assertEqual(self.count("SELECT count(*) FROM hsi_patients WHERE dni = %s", (P,)), 1)
        self.assertEqual(self.count("SELECT count(*) FROM patient_lab_uploads WHERE dni = %s "
                                    "AND archivo IS NOT NULL", (P,)), 1)

    def test_the_photo_goes_the_referral_file_stays(self):
        self.assertEqual(self.erase("block")["files"]["removed"], 1)
        self.assertFalse(os.path.exists(self.photo_p))
        self.assertTrue(os.path.exists(self.referral_p))

    def test_the_other_person_is_untouched(self):
        before = self.dump(Q)
        self.erase("block")
        self.assertEqual(self.dump(Q), before)


class TestRefusals(ErasureCase):
    def assertRefusedAndNothingWritten(self, reason, **kw):
        if "request_id" not in kw:
            kw["request_id"] = self.request()
        before = self.fx.table_counts()
        with self.assertRaises(subject_erase.ErasureRefused) as ctx:
            self.erase(**kw)
        self.assertIn(reason, str(ctx.exception))
        self.assertEqual(self.fx.table_counts(), before)
        self.assertTrue(os.path.exists(self.photo_p) and os.path.exists(self.referral_p))

    def test_no_authorisation_no_erasure(self):
        self.assertRefusedAndNothingWritten("no authorisation", authorised_by="  ")

    def test_a_live_appointment_blocks_it(self):
        self.fx.insert("booking_requests", dni=P, service_id="lab", center_slug="c", status="pendiente")
        self.assertRefusedAndNothingWritten("live appointments")

    def test_an_appointment_still_to_be_sent_to_hsi_blocks_it(self):
        from datetime import datetime, timedelta, timezone
        self.fx.insert("booking_requests", dni=P, service_id="lab", center_slug="c",
                       status="cancelada", hsi_status="pendiente",
                       appointment_at=datetime.now(timezone.utc) + timedelta(days=3))
        self.assertRefusedAndNothingWritten("HSI")

    def test_a_pending_hsi_intent_blocks_it(self):
        self.fx.insert("hsi_outbox", entity_type="appointment",
                       entity_id=str(self.p["ids"]["request"]), operation="create",
                       payload={"x": 1}, idempotency_key="pending-1", state="pending")
        self.assertRefusedAndNothingWritten("HSI intent")

    def test_an_unresolved_hsi_conflict_blocks_it(self):
        self.fx.insert("hsi_sync_conflicts", entity_type="patient", entity_id="9", direction="inbound",
                       reason="x", local_payload={"documento": P}, remote_payload={})
        self.assertRefusedAndNothingWritten("conflict")

    def test_the_ledger_must_hold_an_erasure_request_of_this_person_with_a_verified_identity(self):
        self.assertRefusedAndNothingWritten("identity", request_id=self.request(verified=False))
        self.assertRefusedAndNothingWritten("not an erasure", request_id=self.request(kind="access"))
        self.assertRefusedAndNothingWritten("another person's", request_id=self.request(dni=Q))
        closed = self.request()
        sr.refuse(closed, "no", "mesa")
        self.assertRefusedAndNothingWritten("already refused", request_id=closed)
        self.assertRefusedAndNothingWritten("no request", request_id=999999)


class TestCli(ErasureCase):
    def setUp(self):
        super().setUp()
        self.cli = _load_cli()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            code = self.cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_without_apply_it_is_a_dry_run_that_prints_counts_only(self):
        before = self.fx.table_counts()
        code, out, _ = self.run_cli("--dni", P)
        self.assertEqual(code, 0)
        self.assertEqual(self.fx.table_counts(), before)
        self.assertIn("dry-run", out)
        self.assertIn("users", out)
        for secret in (P, self.p["name"], self.p["email"]):
            self.assertFalse(secret in out)

    def test_apply_needs_the_request_and_the_authorisation(self):
        self.assertEqual(self.run_cli("--dni", P, "--apply")[0], 2)
        rid = self.request()
        code, _, err = self.run_cli("--dni", P, "--apply", "--request", str(rid))
        self.assertEqual(code, 2)
        self.assertIn("authorisation", err)
        self.assertEqual(self.count("SELECT count(*) FROM users WHERE dni = %s", (P,)), 1)

    def test_apply_erases_and_prints_counts_only(self):
        rid = self.request()
        code, out, _ = self.run_cli("--dni", P, "--apply", "--request", str(rid),
                                    "--authorised-by", "counsel 30/10", "--operator", "tester")
        self.assertEqual(code, 0)
        self.assertEqual(self.count("SELECT count(*) FROM users WHERE dni = %s", (P,)), 0)
        self.assertIn("files: 2 removed", out)
        self.assertFalse(P in out)

    def test_a_blocked_erasure_exits_two(self):
        self.fx.insert("booking_requests", dni=P, service_id="lab", center_slug="c", status="pendiente")
        code, _, err = self.run_cli("--dni", P, "--apply", "--request", str(self.request()),
                                    "--authorised-by", "x")
        self.assertEqual(code, 2)
        self.assertIn("live appointments", err)

    def test_a_bad_dni_is_refused(self):
        self.assertEqual(self.run_cli("--dni", "abc")[0], 2)


if __name__ == "__main__":
    unittest.main()
