"""Worker-driven booking approval → failed HSI → due error → automatic claim.

SUPPLEMENTS literal audit row 3.5's criterion without amending it or marking
it MET. The existing manual proof (``test_hsi_booking_retry_acceptance.py``,
untouched) closes the same chain only through a MANUAL consumer that calls
``db_hsi.hsi_outbox_reset()`` before replaying — a deliberate skip of the 30 s
backoff that also resets the row to 'pending'. This module proves the
AUTOMATIC path the runtime already ships (no reset, no manual replay, no
mocked successful tick):

  real approval → real FAILED loopback HSI HTTP (durable 'error' row with the
  REAL production backoff still in the future) → the REGISTERED worker's own
  hsi_sync tick (a real ``python3 worker.py`` subprocess, the real
  procrastinate scheduler, the real boot-rekick + self-rescheduling chain)
  claims the DUE error row by itself → exactly ONE remote appointment over
  real HTTP → a later tick and a FRESH worker process never book a second.

Bounded deterministic due-time advancement (documented allowance): after the
approval failure is pinned with its ``next_attempt_at`` still in the future
(pinning that the production backoff ``_backoff(1) == 30 s`` was applied and
NOT weakened), the test UPDATEs ``hsi_outbox.next_attempt_at`` to now()
INSIDE THE DISPOSABLE TEST DATABASE ONLY. That UPDATE stands in for waiting
out the 30 s wall clock; it touches no production code, no production row
and no retry policy. The worker then claims the row through
``db_hsi.hsi_outbox_claim``'s own due-error path ('pending'/'error' AND
``next_attempt_at <= now()``) — the exact automatic path the manual proof
had to bypass with a reset.

Worker cadence: ``TURNOS_HSI_SYNC_INTERVAL_MIN=0.05`` (3 s), a first-class
fractional value of the production env var (see ``hsi_journal.
_interval_min`` and ``worker._hsi_sync_cadence``'s ``:g`` formatting for
"genuinely fractional test-seam values"). The scheduler itself is real: the
worker subprocess boots, its startup rekick defers the cold-start tick, the
tick's completion durably self-defers the next run — none of that is
shortcut here.

The fake HSI server extends the manual proof's ``_FailThenBookHsi`` surface
(a per-test copy, credited here, because its handler closes over per-test
state) with the two endpoints the worker's full tick also touches:
``/api/patient/<id>/basicdata`` (returns ``{}`` → falsy → the patient is
skipped by ``pull_observations``) and an appointments listing that honours
the from..to window (``list_appointments`` walks it during reconcile).
Neither is needed for the approval path; both keep the worker's reconcile
phase green so the proof exercises the tick's REAL shape, not a
reduced one.

HSI stays opt-in: only the test process and the spawned worker set
``CONNECTOR_EMR_SYNC=hsi`` / ``HSI_URL=loopback``. Missing disposable
Postgres fails closed. Run via:

    python3 scripts/turnos_managed_exec.py --profile backend-focused \\
        --test tests/test_hsi_worker_retry_acceptance.py
"""

import json
import os
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
import _wait  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1")

import _dbtest  # noqa: E402
import _workerproc  # noqa: E402
from app.core import config, db, queue_infra  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_hsi  # noqa: E402
from app.modules.turnos import booking_decisions, hsi_journal_queue  # noqa: E402

_WHEN = (datetime.now(timezone(timedelta(hours=-3))) + timedelta(days=30)).replace(
    hour=10, minute=30, second=0, microsecond=0,
).isoformat()
_ENV_KEYS = (
    "CONNECTOR_EMR_SYNC", "TURNOS_SKIP_HSI", "TURNOS_CONFIG_LOCK",
    "TURNOS_HSI_SYNC_OFF", "TURNOS_DEMO_MODE", "HSI_URL",
    "TURNOS_ENV_FILE",
)

_db = _dbtest.PerModuleDb(
    "sana_turnos_test_hsi_worker_retry",
    seed=lambda: db_booking.get_booking_request(-1),
)

# Everything the registered worker would otherwise also consume is OFF, so
# the subprocess is the hsi_sync worker with only its maintenance minimum —
# same focus-flag set test_queue_worker's HSI suites use.
_WORKER_FOCUS = {
    "CONNECTOR_EMR_SYNC": "hsi",
    "TURNOS_HSI_SYNC_INTERVAL_MIN": "0.05",
    "TURNOS_WAITLIST_OFF": "1",
    "TURNOS_REMINDERS_OFF": "1",
    "TURNOS_HOUSEKEEPING_OFF": "1",
    "TURNOS_REFERRAL_SWEEP_OFF": "1",
    "TURNOS_BACKUP_OFF": "1",
    "TURNOS_SUMMARY_PREGEN": "0",
    "TURNOS_TRANSLATE_WARMUP_OFF": "1",
    "TURNOS_CONFIG_LOCK": "1",
}


class _FailThenBookHsi:
    """Loopback HSI surface for the full worker tick, not only the approval.

    Same contract as test_hsi_booking_retry_acceptance's server of the same
    idea: the first appointment-create POST fails by closing the connection
    (no remote booking); later POSTs persist one appointment each, minting
    ids from 1 — so a duplicate create is observable in ``appointments`` /
    ``create_posts``. Adds ``basicdata`` (falsy patient) and window-aware
    appointment listing for the tick's reconcile phase.
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
                # reconcile's pull_observations: a falsy body skips the patient
                if (parsed.path.startswith("/api/patient/")
                        and parsed.path.endswith("/basicdata")):
                    self._json(200, {})
                    return
                if parsed.path == (
                        "/api/institutions/1/medicalConsultations/appointments/diary/1"):
                    on_date = (qs.get("from") or [""])[0]
                    to_date = (qs.get("to") or [on_date])[0]
                    self._json(200, [a for a in state.appointments
                                     if on_date <= a["date"] <= to_date])
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
            "SELECT id, operation, state, attempts, last_error, next_attempt_at "
            "FROM hsi_outbox WHERE entity_id=%s ORDER BY id",
            (str(sid),),
        )
        return cur.fetchall()


def _confirmation_complete(sid):
    """The booking write precedes journal acknowledgement in the real worker."""
    row = db_booking.get_booking_request(sid)
    intents = _outbox(sid)
    return bool(row and row["hsi_estado"] == "ok"
                and len(intents) == 1 and intents[0]["state"] == "confirmed")


def _succeeded_ticks():
    """Real procrastinate ledger: succeeded hsi_sync tick jobs in this DB."""
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM procrastinate_jobs "
            "WHERE task_name=%s AND status='succeeded'",
            (hsi_journal_queue._TASK_NAME,),
        )
        return cur.fetchone()["count"]


def _wait_until(probe, timeout, label):
    _wait.until(probe, f"{label} within {timeout:g}s", timeout=timeout, interval=0.25)


def setUpModule():
    _db.set_up()
    if not _db.have_db:
        raise RuntimeError(
            "worker→HSI retry acceptance REQUIRES the disposable-Postgres "
            "harness and it is unavailable: %s"
            % (_db.skip_reason or "unknown reason"))
    # The worker boot would create these anyway; doing it here lets the test
    # observe the ledger from the first tick without an ordering race.
    queue_infra.ensure_schema()


def tearDownModule():
    _db.tear_down()


class TestConfirmationObservation(unittest.TestCase):
    def test_booking_success_alone_is_not_journal_completion(self):
        # Reader regression over real disposable-DB transactions. This is
        # not the worker acceptance proof below and does not replay any HTTP.
        sid = db_booking.create_booking_request(
            "30111904", "Journal", "Observation", "1984-03-03", None,
            "cardiologia", "hospital", sexo="M", solicitante_dni="30111904")
        intent = db_hsi.hsi_outbox_enqueue(
            "appointment", sid, "create", {"solicitud_id": sid},
            f"test-observe-{sid}")
        self.assertEqual(db_hsi.hsi_outbox_claim_id(intent["id"])["state"], "sending")
        result = {"estado": "ok", "patient_id": "1", "appointment_id": "1"}
        db_booking.update_hsi(sid, result)
        # The previous wait condition is now true, but finishing would race
        # the separate hsi_outbox_confirm transaction in _replay_one.
        self.assertEqual(db_booking.get_booking_request(sid)["hsi_estado"], "ok")
        self.assertEqual(_outbox(sid)[0]["state"], "sending")
        self.assertFalse(_confirmation_complete(sid))
        db_hsi.hsi_outbox_confirm(intent["id"], result)
        self.assertTrue(_confirmation_complete(sid))


class TestWorkerClaimsDueHsiError(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, {key: None for key in _ENV_KEYS})
        self.env_path = config.ENV_PATH
        config.ENV_PATH = ""
        _env.set_env(self, TURNOS_ENV_FILE=os.devnull, TURNOS_CONFIG_LOCK="1",
                     CONNECTOR_EMR_SYNC="hsi")
        config._reset_cache_para_tests()
        self.fake = _FailThenBookHsi()
        self.addCleanup(self.fake.stop)
        _env.set_env(self, HSI_URL=self.fake.url)
        self._workers = []
        self.addCleanup(self._stop_workers)

    def tearDown(self):
        config.ENV_PATH = self.env_path
        config._reset_cache_para_tests()

    def _stop_workers(self):
        for worker in self._workers:
            try:
                worker.cleanup()
            except Exception:  # noqa: BLE001 — best-effort safety net
                pass

    def _spawn_worker(self):
        env = _workerproc.worker_env(_db.test_url, dict(_WORKER_FOCUS))
        # The shared harness disables HSI by default; this proof is ABOUT
        # the HSI worker, so the child runs with real (loopback) HSI.
        env.pop("TURNOS_SKIP_HSI", None)
        env["HSI_URL"] = self.fake.url
        worker = _workerproc.WorkerProc(env)
        self._workers.append(worker)
        return worker

    def test_due_error_row_claimed_by_registered_worker_exactly_once(self):
        # Stage A — the registered worker is up and its self-rescheduling
        # chain (boot rekick → tick → completion-anchored next defer) is
        # really running BEFORE anything fails. An idle tick must touch
        # nothing: real scheduler proof, not a mocked tick.
        w1 = self._spawn_worker()
        w1.wait_line("corriendo")
        _wait_until(lambda: _succeeded_ticks() >= 1, 60,
                    "worker #1 never completed its first hsi_sync tick")
        self.assertEqual(self.fake.appointments, [])
        self.assertEqual(self.fake.create_posts, 0)

        # Stage B — real approval; the inline first sync fails over real
        # HTTP and leaves ONE durable error row with the real backoff.
        sid = db_booking.create_booking_request(
            "30111903", "Worker", "RetryAcceptance", "1984-03-03", None,
            "cardiologia", "hospital", sexo="M", solicitante_dni="30111903",
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
        self.assertNotIn(row["hsi_estado"], ("ok", "skip"))
        intents = _outbox(sid)
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0]["operation"], "create")
        self.assertEqual(intents[0]["state"], "error")
        self.assertEqual(intents[0]["attempts"], 1)
        self.assertTrue(intents[0]["last_error"])
        self.assertEqual(self.fake.appointments, [])
        self.assertEqual(self.fake.create_posts, 1)
        # The production backoff (_backoff(1) == 30 s) was applied and is
        # still pending — pinning it BEFORE substituting the wait (below).
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT extract(epoch FROM (next_attempt_at - now())) AS espera "
                "FROM hsi_outbox WHERE id=%s", (intents[0]["id"],))
            backoff_s = float(cur.fetchone()["espera"])
        self.assertGreaterEqual(backoff_s, 25.0,
                                "retry backoff shorter than production 30 s")
        self.assertLessEqual(backoff_s, 40.0)

        # Stage C — deterministic due-time advancement, DISPOSABLE DB ONLY:
        # stands in for the 30 s backoff wait (documented allowance). The
        # row is still 'error' here — nothing reset it to 'pending', and the
        # worker must claim it through the due-error path on its own.
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE hsi_outbox SET next_attempt_at = now() - interval '1 second' "
                "WHERE id=%s AND state='error'", (intents[0]["id"],))
            self.assertEqual(cur.rowcount, 1,
                             "error row was not claimable-due when advanced")

        # Stage D — the worker's OWN next scheduled tick claims the due
        # error and confirms exactly one remote appointment. attempts==2 is
        # the "the WORKER claimed it" proof: the approval's inline replay
        # was attempt 1, the test process never replays, and no reset ever
        # touched the row.
        _wait_until(
            lambda: _confirmation_complete(sid),
            90, "worker never confirmed the due error row")
        after = db_booking.get_booking_request(sid)
        self.assertEqual(after["hsi_estado"], "ok")
        self.assertEqual(str(after["hsi_appointment_id"]), "1")
        intents = _outbox(sid)
        self.assertEqual(intents[0]["state"], "confirmed")
        self.assertEqual(intents[0]["attempts"], 2)
        self.assertEqual(len(self.fake.appointments), 1)
        self.assertEqual(self.fake.create_posts, 2)

        # Stage E — a later tick runs and books nothing more: confirmed
        # rows are not claimable, so there is no second remote booking.
        ticks_at_confirm = _succeeded_ticks()
        _wait_until(lambda: _succeeded_ticks() >= ticks_at_confirm + 1, 60,
                    "no later hsi_sync tick ran after the confirmation")
        self.assertEqual(len(self.fake.appointments), 1)
        self.assertEqual(self.fake.create_posts, 2)
        self.assertEqual(_outbox(sid)[0]["state"], "confirmed")

        # Stage F — fresh worker process over the same disposable DB: its
        # boot (rekick or the pending self-reschedule) ticks again without
        # re-claiming the confirmed row — exactly one remote booking still.
        self.assertEqual(w1.stop(), 0, "worker #1 did not shut down gracefully")
        w1.cleanup()
        ticks_at_stop = _succeeded_ticks()
        w2 = self._spawn_worker()
        w2.wait_line("corriendo")
        _wait_until(lambda: _succeeded_ticks() >= ticks_at_stop + 1, 90,
                    "fresh worker #2 never ticked")
        self.assertEqual(len(self.fake.appointments), 1)
        self.assertEqual(self.fake.create_posts, 2)
        intents = _outbox(sid)
        self.assertEqual(intents[0]["state"], "confirmed")
        self.assertEqual(intents[0]["attempts"], 2,
                         "fresh worker process re-claimed a confirmed row")
        self.assertEqual(w2.stop(), 0)
        w2.cleanup()


if __name__ == "__main__":
    unittest.main()
