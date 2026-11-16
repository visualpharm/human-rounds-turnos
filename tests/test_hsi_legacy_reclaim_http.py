"""Synthetic legacy HSI A→B acceptance, lost ack, cancellation and read recovery."""

import json
import os
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from psycopg.types.json import Json

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _dbtest
from app.core import config, db
from app.core import db_booking
from app.core import db_hsi
from app.connectors.hsi.adapter import HsiConnector
from app.modules.turnos import hsi_journal

_database = _dbtest.PerModuleDb("sana_turnos_test_hsi_legacy_reclaim")
_ART = timezone(timedelta(hours=-3))


def setUpModule():
    _database.set_up()
    if not _database.have_db:
        raise RuntimeError("Disposable PostgreSQL is required for legacy recovery proof")


def tearDownModule():
    _database.tear_down()


class _LoopbackHsi:
    def __init__(self, a_time, b_time):
        self.a_time, self.b_time = a_time, b_time
        self.remote = {"id": 12, "diaryId": 9, "patientId": 55,
                       "patient": {"id": 55}, "date": a_time.date().isoformat(),
                       "hour": a_time.strftime("%H:%M:%S"),
                       "appointmentStateId": 1, "updatedOn": "a"}
        self.move_puts = 0
        self.cancel_puts = 0
        state = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def reply(self, code, body, cookie=False):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                if cookie:
                    self.send_header("Set-Cookie", "token=synthetic; Path=/")
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                if self.path == "/api/auth":
                    length = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(length)) if length else {}
                    assert body.get("username") == "synthetic@example.test"
                    self.reply(200, True, cookie=True)
                else:
                    self.reply(404, {"error": "unexpected POST"})

            def do_GET(self):
                parsed = urlsplit(self.path)
                if parsed.path == "/api/institutions/1/medicalConsultations/diary/9":
                    self.reply(200, {"startDate": state.a_time.date().isoformat(),
                                     "endDate": (state.b_time + timedelta(days=7)).date().isoformat(),
                                     "appointmentDuration": 30,
                                     "diaryOpeningHours": [{"openingHours": {
                                         "id": 100 + day, "dayWeekId": day,
                                         "from": "08:00:00", "to": "16:00:00"}}
                                         for day in range(1, 8)]})
                    return
                if parsed.path == "/api/institutions/1/medicalConsultations/appointments/diary/9":
                    query = parse_qs(parsed.query)
                    start = query.get("from", [""])[0]
                    end = query.get("to", [""])[0]
                    self.reply(200, [dict(state.remote)] if start <= state.remote["date"] <= end else [])
                    return
                self.reply(404, {"error": "unexpected GET"})

            def do_PUT(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length)) if length else {}
                parsed = urlsplit(self.path)
                if parsed.path == "/api/institutions/1/medicalConsultations/appointments/12/update-date":
                    state.move_puts += 1
                    assert body["appointmentId"] == 12
                    expected = state.b_time
                    assert body["date"] == {"date": {"year": expected.year, "month": expected.month,
                                                      "day": expected.day},
                                             "time": {"hours": expected.hour, "minutes": expected.minute,
                                                      "seconds": expected.second}}
                    state.remote.update(date=state.b_time.date().isoformat(),
                                        hour=state.b_time.strftime("%H:%M:%S"), updatedOn="b")
                    self.reply(200, {})
                    return
                if parsed.path == "/api/institutions/1/medicalConsultations/appointments/12/change-state":
                    assert parse_qs(parsed.query).get("appointmentStateId") == ["4"]
                    state.cancel_puts += 1
                    state.remote.update(appointmentStateId=4, updatedOn="cancelled")
                    self.reply(200, {})
                    return
                self.reply(404, {"error": "unexpected PUT"})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


class TestLegacyReadRecoveryHttp(unittest.TestCase):
    def setUp(self):
        self.a_time = (datetime.now(_ART) + timedelta(days=20)).replace(hour=9, minute=0, second=0, microsecond=0)
        self.b_time = self.a_time + timedelta(days=1)
        self.fake = _LoopbackHsi(self.a_time, self.b_time)
        self.adapter = HsiConnector()
        self.a = {"appointment_id": "12", "schedule_id": "9", "patient_id": "55",
                  "scheduled_at": self.a_time.isoformat(), "state": "scheduled", "version": "a"}
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE booking_requests CASCADE")
            cur.execute("TRUNCATE hsi_outbox,hsi_sync_conflicts,hsi_checkpoints,hsi_appointments RESTART IDENTITY")
            cur.execute("""INSERT INTO booking_requests
                        (dni,first_name,last_name,status,appointment_at,hsi_status,hsi_appointment_id)
                        VALUES ('99900019','Synthetic','Legacy','aprobada',%s,'pendiente','12') RETURNING id""",
                        (self.b_time,))
            self.sid = cur.fetchone()["id"]
            cur.execute("""INSERT INTO hsi_checkpoints(entity_type,entity_id,remote)
                        VALUES ('appointment','12',%s)""", (Json(self.a),))
        self.r = db_hsi.hsi_outbox_enqueue("appointment", str(self.sid), "reassign",
            {"solicitud_id": self.sid, "appointment_id": "12", "checkpoint": self.a,
             "when": self.b_time.isoformat()}, "R-legacy-%s" % self.sid)
        self.r = db_hsi.hsi_outbox_claim_id(self.r["id"])

    def tearDown(self):
        self.fake.close()

    def replay(self, item):
        real_get = config.get
        def test_get(key, default=None, legacy=None):
            fixed = {"HSI_URL": self.fake.url, "HSI_EMAIL": "synthetic@example.test",
                     "HSI_PASS": "synthetic-only", "HSI_INSTITUTION_ID": "1"}
            return fixed[key] if key in fixed else real_get(key, default=default, legacy=legacy)
        with patch.object(config, "get", side_effect=test_get), \
             patch.object(hsi_journal.connectors, "resolve", return_value=("hsi", "explicit")), \
             patch.object(hsi_journal.connectors, "adapter_for", return_value=self.adapter):
            return hsi_journal._replay_one(item)

    def test_remote_move_crash_reclaim_and_corrective_cancel(self):
        with patch.object(hsi_journal, "_test_hold_after_remote_accept", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                result = self.replay(self.r)
                self.fail("first replay did not reach remote acceptance: %r" % result)
        self.assertEqual(self.fake.move_puts, 1)
        self.assertEqual(db_hsi.hsi_outbox_get(self.r["id"])["state"], "sending")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE booking_requests SET status='cancelada' WHERE id=%s", (self.sid,))
        c1 = db_hsi.hsi_outbox_enqueue("appointment", str(self.sid), "cancel",
            {"solicitud_id": self.sid, "appointment_id": "12", "checkpoint": self.a,
             "operation": "cancel"}, "C1-legacy-%s" % self.sid)
        c1 = db_hsi.hsi_outbox_claim_id(c1["id"])
        self.assertEqual(self.replay(c1)["state"], "conflict")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("UPDATE hsi_outbox SET lease_expires_at=now()-interval '1 second' WHERE id=%s", (self.r["id"],))
        reclaimed = db_hsi.hsi_outbox_claim_id(self.r["id"])
        self.assertEqual(reclaimed["attempts"], 2)
        self.assertEqual(self.replay(reclaimed)["state"], "confirmed")
        self.assertEqual(self.fake.move_puts, 1)
        rows = db_hsi.hsi_outbox_by_entity(str(self.sid), states=("pending", "confirmed", "conflict"))
        c2 = next(row for row in rows if row["operation"] == "cancel" and row["id"] != c1["id"])
        self.assertEqual(c2["payload"]["checkpoint"]["scheduled_at"], self.b_time.isoformat())
        self.assertEqual(self.replay(db_hsi.hsi_outbox_claim_id(c2["id"]))["state"], "confirmed")
        self.assertEqual((self.fake.move_puts, self.fake.cancel_puts), (1, 1))
        self.assertEqual((self.fake.remote["id"], self.fake.remote["appointmentStateId"]), (12, 4))
        self.assertEqual(db_booking.get_booking_request(self.sid)["hsi_estado"], "ok")
        checkpoint = db_hsi.hsi_checkpoint_get("appointment", "12")
        self.assertEqual((checkpoint["scheduled_at"], checkpoint["state"]),
                         (self.b_time.isoformat(), "cancelled"))
