"""Offline booking → failed HSI → durable retry → one success.

Audit row 3.5 still needs this exact chain. Nearby proofs do not cover it:

- ``test_booking_requests`` patches ``hsi_client.push_turno`` and never
  retries a failed approval through a fresh consumer.
- ``test_emr_bridge_conformance`` starts from ``db_booking.approve`` + enqueue, not
  the application approval path, and replays in-process.
- ``test_queue_worker`` crash-window cases seed SQL rows and prove remote
  accept / local interruption, not a failed first sync that later succeeds.

This module drives the real approval, journal, claim/retry, and HsiClient
HTTP path against a disposable Postgres database and a loopback fake HSI.
The journal is not replaced. A second process is the retry consumer.

HSI stays opt-in: only this process sets ``CONNECTOR_EMR_SYNC=hsi``. Missing
Postgres fails closed. This file does not close the rest of row 3.5.
"""

import json
import os
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1")

import _dbtest  # noqa: E402
from app.core import config, db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_hsi  # noqa: E402
from app.modules.turnos import booking_decisions, hsi_journal  # noqa: E402

_CONSUMER_FLAG = "TURNOS_TEST_HSI_RETRY_CONSUMER"
_WHEN = (datetime.now(timezone(timedelta(hours=-3))) + timedelta(days=30)).replace(
    hour=10, minute=30, second=0, microsecond=0,
).isoformat()
_ENV_KEYS = (
    "CONNECTOR_EMR_SYNC", "TURNOS_SKIP_HSI", "TURNOS_CONFIG_LOCK",
    "TURNOS_HSI_SYNC_OFF", "TURNOS_DEMO_MODE", "HSI_URL",
    "TURNOS_ENV_FILE",
)

_db = _dbtest.PerModuleDb(
    "sana_turnos_test_hsi_retry_acc",
    seed=lambda: db_booking.get_booking_request(-1),
)


class _FailThenBookHsi:
    """Loopback HSI surface for HsiClient.login / ensure_patient / create.

    The first appointment POST fails without creating a remote booking; later
    POSTs persist one appointment. A retried client that talks HTTP sees the
    same store, so a duplicate create is observable.
    """

    def __init__(self):
        self.appointments = []
        self.create_posts = 0
        self.fail_creates = 1
        self._next_id = 1
        state = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _json(self, code, body, cookie=None):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                if cookie:
                    self.send_header("Set-Cookie", "token=%s; Path=/" % cookie)
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else {}
                if self.path == "/api/auth":
                    self._json(200, True, cookie="faketoken")
                    return
                if self.path == "/api/institutions/1/medicalConsultations/appointments":
                    state.create_posts += 1
                    if state.fail_creates > 0:
                        state.fail_creates -= 1
                        self.close_connection = True
                        return
                    appt_id = state._next_id
                    state._next_id += 1
                    state.appointments.append({
                        "id": appt_id,
                        "date": body["date"],
                        "hour": body["hour"],
                        "appointmentStateId": 1,
                        "patient": {"id": str(body["patientId"])},
                    })
                    self._json(200, appt_id)
                    return
                self._json(404, {"error": "unmatched POST " + self.path})

            def do_GET(self):
                parsed = urlsplit(self.path)
                qs = parse_qs(parsed.query)
                if parsed.path == "/api/institution/1/healthcareprofessional":
                    self._json(200, [{"id": 1, "fullName": "Dr Fake"}])
                    return
                if parsed.path == "/api/institutions/1/medicalConsultations/diary":
                    self._json(200, [{
                        "id": 1,
                        "clinicalSpecialtyName": "Cardiología",
                        "doctorsOfficeDescription": "Consultorio 1",
                        "startDate": "2020-01-01",
                        "endDate": "2099-01-01",
                        "appointmentDuration": 30,
                        "isExpired": False,
                    }])
                    return
                if parsed.path == "/api/institutions/1/medicalConsultations/diary/1":
                    hours = [{
                        "openingHours": {
                            "dayWeekId": day, "from": "09:00:00",
                            "to": "17:00:00", "id": 100 + day,
                        },
                    } for day in range(1, 8)]
                    self._json(200, {
                        "appointmentDuration": 30,
                        "startDate": "2020-01-01",
                        "endDate": "2099-01-01",
                        "diaryOpeningHours": hours,
                    })
                    return
                if parsed.path == "/api/patient/minimalsearch":
                    self._json(200, [555])
                    return
                if parsed.path == (
                        "/api/institutions/1/medicalConsultations/appointments/diary/1"):
                    on_date = (qs.get("from") or [""])[0]
                    self._json(200, [a for a in state.appointments if a["date"] == on_date])
                    return
                self._json(404, {"error": "unmatched GET " + parsed.path})

        self.server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self.port

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)


def _outbox(sid):
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, operation, state, attempts, last_error "
            "FROM hsi_outbox WHERE entity_id=%s ORDER BY id",
            (str(sid),),
        )
        return cur.fetchall()


def _fresh_consumer():
    """Claim persisted outbox work in a new process. No in-memory journal."""
    # A dedicated child process: these settings are for its whole life, never undone.
    _env.process_set(TURNOS_SKIP_HSI=None, TURNOS_CONFIG_LOCK="1", CONNECTOR_EMR_SYNC="hsi",
                     TURNOS_ENV_FILE=os.devnull)
    config._reset_cache_para_tests()
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, state FROM hsi_outbox WHERE operation='create' ORDER BY id")
        rows = cur.fetchall()
    if not rows:
        sys.stderr.write("consumer: no create outbox rows\n")
        return 2
    due = [row["id"] for row in rows if row["state"] == "error"]
    confirmed = [row for row in rows if row["state"] == "confirmed"]
    if due:
        for outbox_id in due:
            if db_hsi.hsi_outbox_reset(outbox_id) is None:
                sys.stderr.write("consumer: reset refused for %s\n" % outbox_id)
                return 3
        replayed = hsi_journal.replay(limit=20)
        sys.stderr.write("consumer: replayed=%s\n" % replayed)
        return 0 if replayed == 1 else 1
    replayed = hsi_journal.replay(limit=20)
    sys.stderr.write(
        "consumer: idle replayed=%s confirmed_rows=%s\n" % (replayed, len(confirmed)))
    return 0 if replayed == 0 and len(confirmed) == 1 else 1


def setUpModule():
    _db.set_up()
    if not _db.have_db:
        raise RuntimeError(
            "booking→HSI retry acceptance REQUIRES the disposable-Postgres "
            "harness and it is unavailable: %s"
            % (_db.skip_reason or "unknown reason"))


def tearDownModule():
    _db.tear_down()


class TestBookingFailedHsiDurableRetry(unittest.TestCase):
    def setUp(self):
        # Cleanups run last-in first-out: the config cache is dropped after the environment is back.
        self.addCleanup(config._reset_cache_para_tests)
        self.addCleanup(setattr, config, "ENV_PATH", config.ENV_PATH)
        config.ENV_PATH = ""
        _env.set_env(self, {key: None for key in _ENV_KEYS})
        _env.set_env(self, TURNOS_ENV_FILE=os.devnull, TURNOS_CONFIG_LOCK="1",
                     CONNECTOR_EMR_SYNC="hsi")
        config._reset_cache_para_tests()
        self.fake = _FailThenBookHsi()
        self.addCleanup(self.fake.stop)
        _env.set_env(self, HSI_URL=self.fake.url)

    def _spawn_consumer(self):
        env = os.environ.copy()
        env[_CONSUMER_FLAG] = "1"
        env["DATABASE_URL"] = _db.test_url
        env["CONNECTOR_EMR_SYNC"] = "hsi"
        env["HSI_URL"] = self.fake.url
        env["TURNOS_CONFIG_LOCK"] = "1"
        env["TURNOS_ENV_FILE"] = os.devnull
        env["TURNOS_FAKE_AI"] = "1"
        env.pop("TURNOS_SKIP_HSI", None)
        env["PYTHONPATH"] = ROOT
        return subprocess.run(
            [sys.executable, os.path.abspath(__file__)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_failed_sync_stays_retryable_across_a_fresh_consumer_once(self):
        sid = db_booking.create_booking_request(
            "30111901", "Retry", "Acceptance", "1984-03-03", None,
            "cardiologia", "hospital", sexo="M", solicitante_dni="30111901",
        )
        status, payload = booking_decisions._approve_core(
            {"usuario": "test-admin", "rol": "admin"},
            db_booking.get_booking_request(sid),
            sid,
            {"turno_dt": _WHEN},
        )
        self.assertEqual(status, 200, payload)
        row = db_booking.get_booking_request(sid)
        self.assertEqual(row["estado"], "aprobada")
        self.assertNotEqual(row["hsi_estado"], "ok")
        self.assertNotEqual(row["hsi_estado"], "skip")
        intents = _outbox(sid)
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0]["operation"], "create")
        self.assertEqual(intents[0]["state"], "error")
        self.assertGreaterEqual(intents[0]["attempts"], 1)
        self.assertTrue(intents[0]["last_error"])
        self.assertEqual(self.fake.appointments, [])
        self.assertEqual(self.fake.create_posts, 1)

        first = self._spawn_consumer()
        self.assertEqual(
            first.returncode, 0,
            "fresh consumer stderr=%r stdout=%r" % (first.stderr, first.stdout),
        )
        after = db_booking.get_booking_request(sid)
        self.assertEqual(after["hsi_estado"], "ok")
        self.assertEqual(str(after["hsi_appointment_id"]), "1")
        intents = _outbox(sid)
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0]["state"], "confirmed")
        self.assertEqual(len(self.fake.appointments), 1)
        self.assertEqual(self.fake.create_posts, 2)

        second = self._spawn_consumer()
        self.assertEqual(
            second.returncode, 0,
            "idle consumer stderr=%r stdout=%r" % (second.stderr, second.stdout),
        )
        self.assertEqual(len(self.fake.appointments), 1)
        self.assertEqual(self.fake.create_posts, 2)
        self.assertEqual(_outbox(sid)[0]["state"], "confirmed")
        self.assertEqual(hsi_journal.replay(limit=20), 0)


if __name__ == "__main__":
    if os.environ.get(_CONSUMER_FLAG) == "1":
        raise SystemExit(_fresh_consumer())
    unittest.main()
