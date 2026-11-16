"""The files an erasure removes (Ley 25.326, core/subject_erase.py) go into the
same journal the referral retention writes (`purged_files`, schema review №11):
their B2 copies are deleted by the next backup sweep and a restore refuses them."""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", MAIL_ENABLED="0")

from app.core import db, subject_erase, subject_export  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.core import db_backup  # noqa: E402
from app.core import subject_requests as sr  # noqa: E402
import _dbtest  # noqa: E402
import _subject_fixture  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_subject_erase_journal")
HAVE_DB = False
DB_SKIP_REASON = ""
P, Q = "20111222", "30222333"


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


class TestErasureJournalsItsFiles(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        db_catalog.catalog_list("centros")
        self.fx = _subject_fixture.Fixture(_db.test_url, self)
        self.fx.reset()
        self.photos = os.path.join(self.fx.tmp, "photos")
        self.fx.seed_person(P, "Pia")
        self.fx.seed_person(Q, "Quimey", phone="2255999888")
        self.photo_p = self.fx.photo(self.photos, P)
        self.photo_q = self.fx.photo(self.photos, Q, b"other photo")
        self.referral_p = os.path.join(self.fx.tmp, "orden-Pia.pdf")
        self.referral_q = os.path.join(self.fx.tmp, "orden-Quimey.pdf")
        patcher = mock.patch("app.cuentas.account_photo.PHOTOS_DIR", self.photos)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.fx.execute("UPDATE booking_requests SET status = 'cancelada' WHERE status = 'pendiente'")
        for path, dni in ((self.referral_p, P), (self.referral_q, Q)):
            with open(path, "wb") as f:
                f.write(b"orden")
            self.fx.execute("UPDATE booking_requests SET referral_path = %s WHERE dni = %s", (path, dni))
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM purged_files")

    def _erase(self):
        subject = subject_export.resolve_subject(P)
        row = sr.register("erasure", P, "mesa", holidays=())
        sr.set_identity_check(row["id"], "DNI shown in person", "mesa")
        return subject_erase.apply(subject, "anonymise", row["id"], "counsel, 2026-10-30", "tester")

    def test_the_removed_referral_file_is_journaled_by_its_data_relative_path(self):
        report = self._erase()
        self.assertEqual(report["files"]["removed"], 2)
        self.assertEqual(db_backup.purged_file_paths(), {"ordenes/orden-Pia.pdf"})
        self.assertEqual(db_backup.purged_files_pending(), ["ordenes/orden-Pia.pdf"])

    def test_a_photo_path_is_never_journaled_because_it_carries_the_dni(self):
        self._erase()
        self.assertFalse(os.path.exists(self.photo_p), "the photo is still removed")
        self.assertEqual([p for p in db_backup.purged_file_paths() if "cuenta-fotos" in p or P in p], [])

    def test_the_other_persons_files_are_not_journaled(self):
        self._erase()
        self.assertNotIn("ordenes/orden-Quimey.pdf", db_backup.purged_file_paths())

    def test_a_journal_failure_never_stops_the_erasure(self):
        with mock.patch.object(db_backup, "purged_file_record", side_effect=RuntimeError("db")):
            report = self._erase()
        self.assertEqual(report["files"], {"removed": 2, "failed": []})
        self.assertFalse(os.path.exists(self.photo_p))


if __name__ == "__main__":
    unittest.main()
