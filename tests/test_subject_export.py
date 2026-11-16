"""The copy of one person's data (core/subject_export.py, Ley 25.326 art. 14, P2).

A synthetic person with a row in every registry entry, and a second person
whose data must not appear. The export is read only, has no secrets, leaves
nobody else's data in, and lists every place it looked.
"""
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", MAIL_ENABLED="0")

from app.core import access_log, personal_data, subject_export  # noqa: E402
from app.core import db  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.core import db_catalog  # noqa: E402
import _dbtest  # noqa: E402
import _subject_fixture  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_subject_export")
P, Q = "20111222", "30222333"


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _load_script():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "subject_export_script", os.path.join(ROOT, "scripts", "subject_export.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _zip_json(blob):
    z = zipfile.ZipFile(io.BytesIO(blob))
    return z, {n: z.read(n) for n in z.namelist()}


class DbCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        db_catalog.catalog_list("centros")
        self.fx = _subject_fixture.Fixture(_db.test_url, self)
        self.fx.reset()
        self.photos = os.path.join(self.fx.tmp, "photos")


class TestExport(DbCase):
    def setUp(self):
        super().setUp()
        self.p = self.fx.seed_person(P, "Pia")
        self.q = self.fx.seed_person(Q, "Quimey", phone="2255999888")
        self.fx.photo(self.photos, P)
        self.fx.photo(self.photos, Q, b"other photo")
        patcher = mock.patch("app.cuentas.account_photo.PHOTOS_DIR", self.photos)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _export(self, **kw):
        subject = subject_export.resolve_subject(P, **kw)
        return subject, *subject_export.export_to_bytes(subject)

    def test_every_exportable_registry_entry_has_the_persons_rows(self):
        _, manifest, _ = self._export()
        found = {(s["table"], s["part"]): s.get("found", 0) for s in manifest["searched"] if s["exported"]}
        for e in personal_data.REGISTRY:
            if e.export:
                self.assertGreaterEqual(found[(e.table, e.part)], 1,
                                        f"{e.label}: the fixture or the selector misses it")

    def test_the_manifest_lists_every_place_searched_and_what_is_not_reachable(self):
        _, manifest, blob = self._export()
        labels = [(s["table"], s["part"]) for s in manifest["searched"]]
        self.assertEqual(labels, [(e.table, e.part) for e in personal_data.REGISTRY])
        withheld = [s for s in manifest["searched"] if not s["exported"]]
        self.assertTrue(any(s["table"] == "clinical_access_log" for s in withheld))
        self.assertTrue(all(s.get("withheld") for s in withheld))
        self.assertTrue(any("HSI" in n["what"] for n in manifest["not_reachable"]))
        self.assertTrue(any("anonymous" in n["what"] for n in manifest["not_reachable"]))
        _, files = _zip_json(blob)
        self.assertIn("manifest.json", files)

    def test_a_place_with_nothing_is_still_listed_with_zero(self):
        self.fx.reset()
        self.fx.insert("users", dni=P, first_name="Solo")
        _, manifest, _ = self._export()
        sessions = [s for s in manifest["searched"] if s["table"] == "sessions"][0]
        self.assertEqual(sessions["found"], 0)

    def test_no_secret_leaves(self):
        _, _, blob = self._export()
        z, files = _zip_json(blob)
        everything = b"".join(files.values()).decode("utf-8", "replace")
        for name, value in self.p["secrets"].items():
            self.assertFalse(value in everything, f"secret {name} leaked")
        for e in personal_data.REGISTRY:
            for col in e.secret:
                self.assertFalse(f'"{col}"' in everything, f"{e.label}.{col} leaked")

    def test_nobody_elses_data_is_in_the_copy(self):
        _, _, blob = self._export()
        _, files = _zip_json(blob)
        everything = b"".join(files.values()).decode("utf-8", "replace")
        for marker in (self.q["name"], self.q["surname"], Q, self.q["email"], "2255999888",
                       "other photo"):
            self.assertFalse(marker in everything, f"{marker!r} of the other person leaked")
        self.assertTrue(self.p["name"] in everything)

    def test_the_files_come_with_it(self):
        _, manifest, blob = self._export()
        z, files = _zip_json(blob)
        names = set(files)
        self.assertIn(f"files/referrals/{self.p['ids']['request']}.pdf", names)
        self.assertIn("files/profile-photo.jpg", names)
        lab = [n for n in names if n.startswith("files/lab/")]
        self.assertEqual(len(lab), 1)
        self.assertEqual(files[lab[0]], self.p["lab_bytes"])
        kinds = {f["kind"] for f in manifest["files"] if f["included"]}
        self.assertEqual(kinds, {"referral", "profile_photo", "lab_upload"})

    def test_a_lab_file_a_guardian_uploaded_for_a_dependent_is_the_dependents(self):
        self.fx.insert("patient_lab_uploads", dni="9" + P[1:], uploaded_by_dni=P,
                       filename="hijo.pdf", mime="application/pdf", archivo=b"CHILD-LAB")
        _, manifest, blob = self._export()
        _, files = _zip_json(blob)
        self.assertEqual(len(json.loads(files["data/patient_lab_uploads.json"])), 1)
        self.assertFalse(any(b"CHILD-LAB" == v for v in files.values()))
        self.assertEqual(len([f for f in manifest["files"] if f["kind"] == "lab_upload"]), 1)

    def test_a_waiting_list_row_with_a_dni_of_its_own_is_not_found_by_email(self):
        self.fx.insert("waitlist", dni="9" + P[1:], email=self.p["email"], service_id="lab",
                       center_slug="c")                      # a child's entry with the parent's address
        self.fx.insert("waitlist", dni=None, email=self.p["email"].upper(), service_id="kine",
                       center_slug="c")                      # an entry made with the email alone
        _, _, blob = self._export()
        _, files = _zip_json(blob)
        rows = json.loads(files["data/waitlist.json"])
        self.assertEqual(sorted((r["dni"] or "", r["service_id"]) for r in rows),
                         [("", "kine"), (P, "lab")])

    def test_the_manifest_is_dated_in_the_installations_clock(self):
        _, manifest, _ = self._export()
        self.assertRegex(manifest["generated_at"], r"-03:00$")

    def test_the_lab_bytes_are_not_inlined_in_json(self):
        _, _, blob = self._export()
        _, files = _zip_json(blob)
        rows = json.loads(files["data/patient_lab_uploads.json"])
        self.assertEqual(len(rows), 1)
        self.assertNotIn("archivo", rows[0])
        self.assertEqual(rows[0]["archivo_bytes"], len(self.p["lab_bytes"]))

    def test_a_soft_deleted_lab_file_that_still_holds_its_bytes_is_exported(self):
        self.fx.execute("UPDATE patient_lab_uploads SET deleted = now() WHERE dni = %s", (P,))
        _, manifest, blob = self._export()
        _, files = _zip_json(blob)
        self.assertTrue(any(n.startswith("files/lab/") for n in files))
        self.assertTrue([f for f in manifest["files"] if f["kind"] == "lab_upload"][0]["deleted"])

    def test_a_file_the_retention_sweep_removed_is_said_not_failed(self):
        os.remove(os.path.join(self.fx.tmp, "orden-Pia.pdf"))
        _, manifest, _ = self._export()
        ref = [f for f in manifest["files"] if f["kind"] == "referral"][0]
        self.assertFalse(ref["included"])
        self.assertIn("retention", ref["why"])

    def test_requests_made_for_a_dependent_are_counted_not_handed_over(self):
        _, manifest, blob = self._export()
        _, files = _zip_json(blob)
        rows = json.loads(files["data/booking_requests.json"])
        self.assertEqual([r["id"] for r in rows], [self.p["ids"]["request"]])
        self.assertEqual(manifest["requests_made_for_others"]["count"], 1)
        self.assertFalse("HijoPia" in files["data/booking_requests.json"].decode())

    def test_the_lines_about_a_minor_are_not_handed_to_the_minor(self):
        self.fx.reset()
        guardian = "27000111"
        self.fx.seed_person(guardian, "Gia", phone="2255000111")
        self.fx.seed_person(P, "Pia", birth="2015-02-02", guardian_dni=guardian)
        self.fx.execute("UPDATE users SET birth_date = '2015-02-02' WHERE dni = %s", (P,))
        _, manifest, blob = self._export()
        _, files = _zip_json(blob)
        rows = json.loads(files["data/dependents.json"])
        self.assertTrue(all(r["adult_dni"] == P for r in rows))
        dep = [s for s in manifest["searched"] if s["table"] == "dependents"][0]
        self.assertIn("minor", dep["note"])

    def test_the_lines_about_an_adult_dependent_are_handed_over(self):
        self.fx.reset()
        guardian = "27000111"
        self.fx.seed_person(guardian, "Gia", phone="2255000111")
        self.fx.seed_person(P, "Pia", guardian_dni=guardian)
        _, _, blob = self._export()
        _, files = _zip_json(blob)
        rows = json.loads(files["data/dependents.json"])
        self.assertIn(P, {r["dni"] for r in rows})

    def test_the_whatsapp_thread_is_found_by_phone_and_left_out_when_the_phone_is_shared(self):
        subject, manifest, blob = self._export()
        _, files = _zip_json(blob)
        self.assertEqual(len(json.loads(files["data/chat_messages.whatsapp.json"])), 1)
        # another account with the same phone
        self.fx.insert("users", dni="40555666", contact="+54 9 2255 123456", first_name="Otro")
        subject2, manifest2, blob2 = self._export()
        _, files2 = _zip_json(blob2)
        self.assertEqual(subject2.phone10, "")
        self.assertEqual(json.loads(files2["data/chat_messages.whatsapp.json"]), [])
        self.assertTrue(any("phone" in n for n in manifest2["notes"]))
        # the operator says it is this person's
        subject3, _, blob3 = self._export(confirm_shared=True)
        _, files3 = _zip_json(blob3)
        self.assertEqual(len(json.loads(files3["data/chat_messages.whatsapp.json"])), 1)

    def test_a_shared_email_leaves_out_the_waiting_list_row_found_by_email_only(self):
        self.fx.insert("users", dni="40555667", email=self.p["email"].upper(), first_name="Otro")
        subject, manifest, _ = self._export()
        self.assertEqual(subject.emails, ())
        self.assertTrue(any("email" in n for n in manifest["notes"]))

    def test_the_manifest_does_not_carry_the_dni_phone_or_email_whole(self):
        _, manifest, _ = self._export()
        text = json.dumps(manifest)
        self.assertFalse(P in text)
        self.assertFalse("2255123456" in text)
        self.assertFalse(self.p["email"] in text)
        self.assertEqual(manifest["subject"]["dni_hash"], access_log.dni_hash(P))

    def test_the_export_writes_nothing(self):
        before = self.fx.table_counts()
        self._export()
        self.assertEqual(self.fx.table_counts(), before)

    def test_the_transaction_is_read_only(self):
        subject = subject_export.resolve_subject(P)
        real = subject_export._select_rows

        def write_while_exporting(cur, entry, subj, extra=""):
            cur.execute("UPDATE users SET first_name = 'tampered' WHERE dni = %s", (P,))
            return real(cur, entry, subj, extra)

        import psycopg
        with mock.patch.object(subject_export, "_select_rows", write_while_exporting):
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
                subject_export.export_to_bytes(subject)
        self.assertEqual(self.fx.scalar("SELECT first_name FROM users WHERE dni = %s", (P,)),
                         self.p["name"])

    def test_a_dni_that_is_not_one_is_refused(self):
        for bad in ("", "abc", "123", "1" * 12):
            with self.assertRaises(ValueError):
                subject_export.resolve_subject(bad)

    def test_a_person_with_no_account_still_gets_what_is_keyed_by_their_dni(self):
        self.fx.drop_account(P)
        subject, manifest, _ = self._export()
        self.assertEqual(subject.phone10, "")
        found = {s["table"]: s.get("found") for s in manifest["searched"] if s["exported"]}
        self.assertEqual(found["users"], 0)
        self.assertGreaterEqual(found["booking_requests"], 1)


class TestScript(DbCase):
    def test_the_script_writes_the_zip_and_leaves_an_access_row(self):
        script = _load_script()
        self.fx.seed_person(P, "Pia")
        out = os.path.join(self.fx.tmp, "out.zip")
        before = self.fx.scalar("SELECT count(*) FROM clinical_access_log WHERE action = 'exportacion'")
        with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            code = script.main(["--dni", P, "--out", out, "--operator", "tester"])
        self.assertEqual(code, 0)
        self.assertTrue(zipfile.is_zipfile(out))
        self.assertEqual(oct(os.stat(out).st_mode & 0o777), "0o600")
        after = self.fx.scalar("SELECT count(*) FROM clinical_access_log WHERE action = 'exportacion'")
        self.assertEqual(after, before + 1)
        printed = stdout.getvalue()
        self.assertFalse(P in printed)
        self.assertFalse("NombrePia" in printed)

    def test_the_script_refuses_when_the_access_row_cannot_be_written(self):
        script = _load_script()
        self.fx.seed_person(P, "Pia")
        out = os.path.join(self.fx.tmp, "never.zip")
        with mock.patch.object(db_clinical, "insert_clinical_access", side_effect=db.DbUnavailable("down")):
            code = script.main(["--dni", P, "--out", out])
        self.assertEqual(code, 3)
        self.assertFalse(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
