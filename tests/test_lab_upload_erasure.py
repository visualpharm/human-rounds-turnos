"""A lab result the patient «deletes» is really gone, and one nobody deleted does
not keep its file forever (hospital schema review №11, 2026-10-24).

Before: «Eliminar» only set `deleted=now()` — the file (bytea), its transcription
and the filename stayed in the table and in every database dump; there was no
retention at all. Now: deleting wipes the three columns (the stub row stays as
the record that it happened), the AI reading cached for that report goes with
it, and the file bytes expire like a referral's do (`TURNOS_REFERRAL_RETENTION_DAYS`,
0 = never clean; `TURNOS_LAB_UPLOAD_RETENTION_DAYS` overrides)."""

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1")

import _dbtest  # noqa: E402
from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_clinical  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_lab_erasure", seed=lambda: db_booking.get_booking_request(-1))
HAVE_DB = False
DB_SKIP_REASON = ""


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


PDF = b"%PDF-1.4 resultado de prueba"


def _row(upload_id):
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM patient_lab_uploads WHERE id=%s", (upload_id,))
        return cur.fetchone()


def _age(upload_id, days):
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE patient_lab_uploads SET created = now() - make_interval(days => %s) WHERE id=%s",
                    (days, upload_id))


class _Base(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM patient_lab_uploads")
            cur.execute("DELETE FROM lab_report_analysis")

    def _upload(self, dni="27888999", by=None, texto="Glucemia 90 mg/dl", fecha="2026-10-01"):
        _dbtest.ensure_user(by or dni)   # patient_lab_uploads.uploaded_by_dni is a foreign key
        return db_clinical.patient_lab_upload_insert(
            dni, by or dni, filename="analisis-ana-lopez.pdf", mime="application/pdf",
            archivo=PDF, texto=texto, fecha=fecha)


class TestPatientDelete(_Base):
    def test_delete_wipes_file_text_and_name_and_keeps_the_stub(self):
        uid = self._upload()
        self.assertTrue(db_clinical.patient_lab_upload_delete(uid, "27888999"))
        row = _row(uid)
        self.assertIsNone(row["archivo"])
        self.assertIsNone(row["texto"])
        self.assertIsNone(row["filename"])
        self.assertIsNotNone(row["deleted"])
        self.assertEqual((row["dni"], row["uploaded_by_dni"]), ("27888999", "27888999"))
        self.assertEqual(db_clinical.patient_lab_uploads_by_dni("27888999"), [])

    def test_only_the_uploader_can_delete_and_a_second_delete_finds_nothing(self):
        uid = self._upload(dni="11222333", by="27888999")   # an adult uploading for a dependent
        self.assertFalse(db_clinical.patient_lab_upload_delete(uid, "11222333"))
        self.assertEqual(bytes(_row(uid)["archivo"]), PDF)
        self.assertTrue(db_clinical.patient_lab_upload_delete(uid, "27888999"))
        self.assertFalse(db_clinical.patient_lab_upload_delete(uid, "27888999"))

    def test_the_cached_reading_of_that_report_goes_with_it(self):
        from app.ai import lab_results
        uid = self._upload()
        entrada = lab_results.entrada_de_subida(_row(uid))
        from app.ai import lab_analysis
        db_clinical.lab_analysis_upsert("27888999", lab_analysis.report_hash(entrada), "p", "2026-10-01",
                               "patient_uploads", "Laboratorio", "resumen", "normal", False, [], "fake")
        db_clinical.lab_analysis_upsert("27888999", "otro-informe", "p", "2026-09-01",
                               "alepo", "Laboratorio", "otro", "normal", False, [], "fake")
        status, body = lab_results.borrar_resultado({"dni": "27888999"}, uid)
        self.assertEqual((status, body), (200, {"ok": True}))
        quedan = {f["report_hash"] for f in db_clinical.lab_analysis_for("27888999")}
        self.assertEqual(quedan, {"otro-informe"})
        self.assertIsNone(_row(uid)["archivo"])


    def test_a_failing_cache_delete_never_undoes_the_file_delete(self):
        from app.ai import lab_results
        uid = self._upload()
        with patch.object(db_clinical, "lab_analysis_delete", side_effect=RuntimeError("db")):
            status, body = lab_results.borrar_resultado({"dni": "27888999"}, uid)
        self.assertEqual((status, body), (200, {"ok": True}))
        self.assertIsNone(_row(uid)["archivo"])

    def test_someone_elses_or_missing_upload_is_a_404_and_keeps_the_file(self):
        from app.ai import lab_results
        uid = self._upload(dni="11222333", by="27888999")
        self.assertEqual(lab_results.borrar_resultado({"dni": "11222333"}, uid)[0], 404)
        self.assertEqual(lab_results.borrar_resultado({"dni": "27888999"}, 999999)[0], 404)
        self.assertEqual(bytes(_row(uid)["archivo"]), PDF)


class TestRetention(_Base):
    def test_old_files_lose_their_bytes_but_keep_the_transcription(self):
        viejo, nuevo = self._upload(fecha="2026-01-01"), self._upload(fecha="2026-10-01")
        _age(viejo, 200)
        self.assertEqual(db_clinical.purge_lab_upload_files(180), 1)
        self.assertIsNone(_row(viejo)["archivo"])
        self.assertEqual(_row(viejo)["texto"], "Glucemia 90 mg/dl")
        self.assertIsNone(_row(viejo)["deleted"], "retention is not the patient deleting it")
        self.assertEqual(bytes(_row(nuevo)["archivo"]), PDF)
        # a second pass has nothing left to do
        self.assertEqual(db_clinical.purge_lab_upload_files(180), 0)

    def test_zero_or_negative_means_never(self):
        uid = self._upload()
        _age(uid, 4000)
        self.assertEqual(db_clinical.purge_lab_upload_files(0), 0)
        self.assertEqual(db_clinical.purge_lab_upload_files(-5), 0)
        self.assertEqual(bytes(_row(uid)["archivo"]), PDF)

    def test_the_limit_bounds_one_pass(self):
        ids = [self._upload() for _ in range(3)]
        for i in ids:
            _age(i, 400)
        self.assertEqual(db_clinical.purge_lab_upload_files(180, limit=2), 2)
        self.assertEqual(db_clinical.purge_lab_upload_files(180, limit=2), 1)


class TestHousekeepingWiring(_Base):
    """The retention window follows the referral files' unless told otherwise,
    and keeps their «0 = do not clean» (what the municipal installation sets)."""

    def _purged(self, **env):
        from app.ops import housekeeping
        unset = {"TURNOS_REFERRAL_RETENTION_DAYS": None, "TURNOS_LAB_UPLOAD_RETENTION_DAYS": None}
        with _env.patched_env({**unset, **env}):
            return housekeeping.tick_purges()["lab_upload_files"]

    def test_default_is_the_referral_default_of_180_days(self):
        uid = self._upload()
        _age(uid, 200)
        self.assertEqual(self._purged(), 1)
        self.assertIsNone(_row(uid)["archivo"])

    def test_referral_retention_zero_keeps_every_file(self):
        uid = self._upload()
        _age(uid, 4000)
        self.assertEqual(self._purged(TURNOS_REFERRAL_RETENTION_DAYS="0"), 0)
        self.assertEqual(bytes(_row(uid)["archivo"]), PDF)

    def test_own_setting_wins_over_the_referral_one(self):
        uid = self._upload()
        _age(uid, 40)
        self.assertEqual(self._purged(TURNOS_REFERRAL_RETENTION_DAYS="180",
                                      TURNOS_LAB_UPLOAD_RETENTION_DAYS="30"), 1)
        uid2 = self._upload()
        _age(uid2, 4000)
        self.assertEqual(self._purged(TURNOS_REFERRAL_RETENTION_DAYS="180",
                                      TURNOS_LAB_UPLOAD_RETENTION_DAYS="0"), 0)


if __name__ == "__main__":
    unittest.main()
