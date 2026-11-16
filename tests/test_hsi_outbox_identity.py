"""Real PostgreSQL proofs for one create identity and one lease owner.

Only a disposable database is used. No EMR adapter, network fixture or real
patient is involved. Missing test infrastructure is a failure, never a skip.
"""
import concurrent.futures
from datetime import datetime
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core import db
from app.core import db_booking
from app.core import db_hsi
from app.modules.turnos import booking_requests, hsi_journal
import _dbtest

_database = _dbtest.PerModuleDb("sana_turnos_test_hsi_identity")


def setUpModule():
    _database.set_up()
    if not _database.have_db:
        raise RuntimeError("Disposable PostgreSQL is required for HSI identity proof")


def tearDownModule():
    _database.tear_down()


class TestCreateIdentity(unittest.TestCase):
    def setUp(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE hsi_outbox RESTART IDENTITY")

    def enqueue(self, entity=901, operation="create", version="old"):
        return db_hsi.hsi_outbox_enqueue("appointment", str(entity), operation,
            {"solicitud_id": entity, "when": version},
            f"synthetic-{entity}-{operation}-{version}")

    def fetch(self, id_):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM hsi_outbox WHERE id=%s", (id_,))
            return cur.fetchone()

    def test_changed_payload_preserves_unsettled_create_and_original_key(self):
        for state in ("pending", "sending", "error", "conflict"):
            with self.subTest(state=state):
                entity = 1000 + ("pending", "sending", "error", "conflict").index(state)
                original = self.enqueue(entity)
                with db._connect() as conn, conn.cursor() as cur:
                    cur.execute("UPDATE hsi_outbox SET state=%s WHERE id=%s", (state, original["id"]))
                later = self.enqueue(entity, version="new")
                self.assertEqual(later["id"], original["id"])
                self.assertEqual(later["idempotency_key"], original["idempotency_key"])
                self.assertEqual(later["payload"], original["payload"])
                self.assertEqual(later["state"], state)

    def test_concurrent_producers_with_different_keys_share_one_create(self):
        barrier = threading.Barrier(8)

        def produce(n):
            barrier.wait(timeout=10)
            return self.enqueue(version=str(n))

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(produce, range(8)))
        self.assertEqual(len({r["id"] for r in rows}), 1)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM hsi_outbox")
            self.assertEqual(cur.fetchone()["n"], 1)

    def test_backfill_after_time_change_reuses_the_active_create(self):
        row = {"id": 903, "dni": "12345678", "estado": "aprobada",
               "turno_dt": datetime.fromisoformat("2099-10-01T09:00:00-03:00")}
        with patch.object(booking_requests, "_service_name", return_value="Synthetic"), \
             patch.object(hsi_journal, "_emr_selected", return_value=True), \
             patch.object(db_booking, "booking_requests_hsi_pending", side_effect=lambda **kw: [row]):
            original = hsi_journal.enqueue_appointment(row)
            claim = db_hsi.hsi_outbox_claim_id(original["id"])
            row["turno_dt"] = datetime.fromisoformat("2099-10-02T09:00:00-03:00")
            self.assertEqual(hsi_journal.backfill_pending(), 1)
        saved = self.fetch(original["id"])
        self.assertEqual(saved["lease_expires_at"], claim["lease_expires_at"])
        self.assertEqual(saved["payload"], original["payload"])
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM hsi_outbox WHERE entity_id='903'")
            self.assertEqual(cur.fetchone()["n"], 1)

    def test_different_requests_and_non_create_operations_are_not_coalesced(self):
        rows = [self.enqueue(904), self.enqueue(905),
                self.enqueue(904, "reassign"), self.enqueue(904, "reassign", "new")]
        self.assertEqual(len({row["id"] for row in rows}), 4)

    def test_live_lease_cannot_be_stolen_by_manual_retry(self):
        intent = self.enqueue()
        claim = db_hsi.hsi_outbox_claim_id(intent["id"])
        self.assertIsNone(db_hsi.hsi_outbox_reset(intent["id"]))
        self.assertIsNone(db_hsi.hsi_outbox_claim_id(intent["id"]))
        after = self.fetch(intent["id"])
        self.assertEqual(after["state"], "sending")
        self.assertEqual(after["attempts"], 1)
        self.assertEqual(after["lease_expires_at"], claim["lease_expires_at"])

    def test_expired_lease_can_be_retried_with_same_identity(self):
        intent = self.enqueue()
        db_hsi.hsi_outbox_claim_id(intent["id"])
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE hsi_outbox SET lease_expires_at=now()-interval '1 second' WHERE id=%s",
                        (intent["id"],))
        reset = db_hsi.hsi_outbox_reset(intent["id"])
        self.assertEqual(reset["state"], "pending")
        replay = db_hsi.hsi_outbox_claim_id(intent["id"])
        self.assertEqual(replay["attempts"], 2)
        self.assertEqual(replay["idempotency_key"], intent["idempotency_key"])

    def test_confirmed_intent_cannot_be_reset(self):
        intent = self.enqueue()
        db_hsi.hsi_outbox_confirm(intent["id"], {"estado": "ok", "appointment_id": "synthetic"})
        self.assertIsNone(db_hsi.hsi_outbox_reset(intent["id"]))

    def test_pending_error_and_conflict_can_be_retried(self):
        for state in ("pending", "error", "conflict"):
            with self.subTest(state=state):
                intent = self.enqueue(entity=2000 + ("pending", "error", "conflict").index(state))
                with db._connect() as conn, conn.cursor() as cur:
                    cur.execute("UPDATE hsi_outbox SET state=%s, next_attempt_at=now()+interval '1 hour' WHERE id=%s",
                                (state, intent["id"]))
                self.assertEqual(db_hsi.hsi_outbox_reset(intent["id"])["state"], "pending")
                self.assertIsNotNone(db_hsi.hsi_outbox_claim_id(intent["id"]))


if __name__ == "__main__":
    unittest.main()
