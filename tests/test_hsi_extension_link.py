"""A turno the desk loaded into HSI by hand gets its link back from the inbound sync.

The desk's Chrome extension (HSI Humanizador) fills HSI's own «Nuevo turno» screen: it never
calls HSI's API, so it never learns the HSI appointment id and the request stays without
`hsi_appointment_id`. The inbound reconciliation used to import that HSI appointment as a
foreign one and stop, so attendance, cancellation and moves made in HSI never reached the
request. Now an HSI appointment no request links is matched to exactly one approved, unlinked
request of the same patient (DNI), the same catalog doctor (the diary's professional) and the
same minute; it is linked once, with an appointment event, and reconciled like any linked one.
Against a real Postgres: the candidate search, the link's lock and its event are SQL.
"""

import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _clock  # noqa: E402
import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_hsi  # noqa: E402
from app.modules.turnos import doctor_appointments  # noqa: E402
from app.modules.turnos import hsi_journal  # noqa: E402
import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_hsi_extension_link")
HAVE_DB = False
DB_SKIP_REASON = ""

_DIARY = "diary-perez"
_DOCTORS = {"Pérez Juan": "dr-perez", "Gómez Ana": "dr-gomez"}


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _sql(sql, params=None):
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


class ExtensionLinkCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "test database unavailable")
        # The turnos below are on 2026-11-18..20: the retry of an unchanged appointment covers
        # today on, so the installation's day is pinned before them.
        _clock.freeze(self, "2026-11-19T12:00:00Z")
        resolver = patch.object(doctor_appointments, "resolve_doctor_id",
                                side_effect=lambda name: _DOCTORS.get((name or "").strip()))
        resolver.start()
        self.addCleanup(resolver.stop)
        db_hsi.hsi_mirror_schedule(_DIARY, "staff-7", {"id": _DIARY, "professional_name": "Pérez Juan"})
        db_hsi.hsi_mirror_schedule("diary-gomez", "staff-8", {"id": "diary-gomez", "professional_name": "Gómez Ana"})

    def _approved(self, dni, at, doctor="Pérez Juan", overbooking=False, hsi_appointment_id=None):
        sid = db_booking.create_booking_request(
            dni, "Ana", "Prueba", "1980-01-01", None, "cardiologia", "hospital", solicitante_dni=dni)
        db_booking.approve(sid, datetime.fromisoformat(at),
                           {"estado": "ok" if hsi_appointment_id else "skip",
                            "appointment_id": hsi_appointment_id},
                           medico_asignado=doctor, medico_asignado_id=_DOCTORS[doctor],
                           resuelta_por="test", sobreturno=overbooking)
        return sid

    def _observe(self, appt_id, dni, at, state="scheduled", diary=_DIARY, patient_id=None, again=False):
        """One observation of an HSI appointment; `again` replays the same one, unchanged, as
        every later tick of the journal does (its inbox row already exists)."""
        patient_id = patient_id or "p-" + dni
        appt = {"id": appt_id, "schedule_id": diary, "patient_id": patient_id,
                "scheduled_at": at, "state": state,
                "patient": {"id": patient_id, "document_number": dni}}
        source = "test:%s:%s" % (appt_id, state)
        if not again:
            db_hsi.hsi_inbox_record(source, "appointment", appt_id, appt)
        return hsi_journal._reconcile_appointment(appt, source, source_new=not again), source

    def _events(self, sid, kind):
        return [e for e in db_booking.appointment_events(sid) if e["kind"] == kind]

    def _inbox_state(self, source):
        return _sql("SELECT state FROM hsi_inbox WHERE source_key=%s", (source,))[0]["state"]

    def _conflicts(self, sid):
        return [r["reason"] for r in _sql(
            "SELECT reason FROM hsi_sync_conflicts WHERE entity_type='appointment' AND entity_id=%s",
            (str(sid),))]


class TestLinkExtensionLoadedTurno(ExtensionLinkCase):
    def test_links_once_and_takes_the_attendance_marked_in_hsi(self):
        at = "2026-11-20T09:30:00-03:00"
        sid = self._approved("30200001", at)
        status, source = self._observe("7001", "30200001", at, state="attended")
        self.assertEqual(status, "confirmed")
        self.assertEqual(self._inbox_state(source), "confirmed")
        row = db_booking.get_booking_request(sid)
        self.assertEqual(row["hsi_appointment_id"], "7001")
        self.assertEqual(row["hsi_patient_id"], "p-30200001")
        self.assertEqual(row["hsi_estado"], "ok")
        self.assertEqual(row["asistencia"], "presente")
        self.assertEqual(len(self._events(sid, db_hsi.HSI_LINKED)), 1)
        attendance = self._events(sid, "asistencia")
        self.assertEqual([(e["actor"], e["attendance_to"]) for e in attendance], [("hsi:HSI", "presente")])
        source_of_mark = _sql("SELECT source FROM attendance_events WHERE request_id=%s", (sid,))
        self.assertEqual([r["source"] for r in source_of_mark], ["hsi"])
        # The next observation of the same appointment finds the link: no second link event.
        status, _source = self._observe("7001", "30200001", at, state="no_show")
        self.assertEqual(status, "confirmed")
        self.assertEqual(db_booking.get_booking_request(sid)["asistencia"], "ausente")
        self.assertEqual(len(self._events(sid, db_hsi.HSI_LINKED)), 1)

    def test_the_link_lets_a_cancellation_in_hsi_reach_the_request(self):
        at = "2026-11-20T10:00:00-03:00"
        sid = self._approved("30200002", at)
        self.assertEqual(self._observe("7002", "30200002", at)[0], "confirmed")
        status, _source = self._observe("7002", "30200002", at, state="cancelled")
        self.assertEqual(status, "conflict")
        self.assertEqual(self._conflicts(sid), ["remote_appointment_cancelled_or_blocked"])

    def test_two_candidates_are_a_conflict_and_neither_is_linked(self):
        at = "2026-11-20T10:30:00-03:00"
        first = self._approved("30200003", at)
        second = self._approved("30200003", at, overbooking=True)
        status, source = self._observe("7003", "30200003", at, state="attended")
        self.assertEqual(status, "conflict")
        self.assertEqual(self._inbox_state(source), "conflict")
        for sid in (first, second):
            row = db_booking.get_booking_request(sid)
            self.assertIsNone(row["hsi_appointment_id"])
            self.assertIsNone(row["asistencia"])
            self.assertEqual(self._events(sid, db_hsi.HSI_LINKED), [])
            self.assertEqual(self._conflicts(sid), ["ambiguous_local_requests_for_hsi_appointment"])

    def test_no_candidate_stays_an_imported_hsi_appointment(self):
        sid = self._approved("30200004", "2026-11-20T11:00:00-03:00")
        cases = (("7004", "30200004", "2026-11-20T11:15:00-03:00", _DIARY),   # another minute
                 ("7005", "30200099", "2026-11-20T11:00:00-03:00", _DIARY),   # another patient
                 ("7006", "30200004", "2026-11-20T11:00:00-03:00", "diary-gomez"))  # another doctor
        for appt_id, dni, at, diary in cases:
            with self.subTest(appt_id=appt_id):
                status, source = self._observe(appt_id, dni, at, state="attended", diary=diary)
                self.assertEqual(status, "imported")
                self.assertEqual(self._inbox_state(source), "confirmed")
        row = db_booking.get_booking_request(sid)
        self.assertIsNone(row["hsi_appointment_id"])
        self.assertIsNone(row["asistencia"])

    def test_a_request_already_linked_is_never_relinked(self):
        at = "2026-11-20T11:30:00-03:00"
        sid = self._approved("30200005", at, hsi_appointment_id="6000")
        status, _source = self._observe("7007", "30200005", at, state="attended")
        self.assertEqual(status, "imported")
        row = db_booking.get_booking_request(sid)
        self.assertEqual(row["hsi_appointment_id"], "6000")
        self.assertIsNone(row["asistencia"])
        self.assertEqual(self._events(sid, db_hsi.HSI_LINKED), [])

    def test_a_cancelled_hsi_appointment_never_claims_the_request(self):
        at = "2026-11-20T12:00:00-03:00"
        sid = self._approved("30200006", at)
        self.assertEqual(self._observe("7008", "30200006", at, state="cancelled")[0], "imported")
        self.assertIsNone(db_booking.get_booking_request(sid)["hsi_appointment_id"])
        # The live appointment the desk loaded again at the same place is the one that links.
        self.assertEqual(self._observe("7009", "30200006", at)[0], "confirmed")
        self.assertEqual(db_booking.get_booking_request(sid)["hsi_appointment_id"], "7009")

    def test_a_request_the_journal_is_still_sending_is_a_conflict_not_a_link(self):
        at = "2026-11-20T12:30:00-03:00"
        sid = self._approved("30200007", at)
        db_hsi.hsi_outbox_enqueue("appointment", sid, "create", {"solicitud_id": sid, "when": at},
                                  "test-create-%s" % sid)
        status, _source = self._observe("7010", "30200007", at)
        self.assertEqual(status, "conflict")
        self.assertIsNone(db_booking.get_booking_request(sid)["hsi_appointment_id"])
        self.assertEqual(self._conflicts(sid), ["hsi_link_while_local_intent_unsettled"])

    def test_the_dni_comes_from_the_patient_mirror_when_the_observation_lacks_it(self):
        at = "2026-11-20T13:00:00-03:00"
        sid = self._approved("30200008", at)
        db_hsi.hsi_mirror_patient("p-mirror", "30200008", {"id": "p-mirror"})
        appt = {"id": "7011", "schedule_id": _DIARY, "patient_id": "p-mirror",
                "scheduled_at": at, "state": "attended", "patient": {"id": "p-mirror"}}
        db_hsi.hsi_inbox_record("test:7011", "appointment", "7011", appt)
        self.assertEqual(hsi_journal._reconcile_appointment(appt, "test:7011"), "confirmed")
        row = db_booking.get_booking_request(sid)
        self.assertEqual(row["hsi_appointment_id"], "7011")
        self.assertEqual(row["hsi_patient_id"], "p-mirror")
        self.assertEqual(row["asistencia"], "presente")

    def test_an_observation_without_any_dni_never_links(self):
        at = "2026-11-20T13:10:00-03:00"
        sid = self._approved("30200010", at)
        appt = {"id": "7013", "schedule_id": _DIARY, "patient_id": "p-anon",
                "scheduled_at": at, "state": "attended", "patient": {"id": "p-anon"}}
        db_hsi.hsi_inbox_record("test:7013", "appointment", "7013", appt)
        self.assertEqual(hsi_journal._reconcile_appointment(appt, "test:7013"), "imported")
        self.assertIsNone(db_booking.get_booking_request(sid)["hsi_appointment_id"])

    def test_a_request_moved_between_the_search_and_the_lock_is_refused(self):
        at = "2026-11-20T13:20:00-03:00"
        sid = self._approved("30200011", at)
        row, refused = db_hsi.hsi_link_request(sid, "7014", "p-30200011", dni="30200011",
                                               medico_id="dr-perez",
                                               minute=datetime.fromisoformat("2026-11-20T13:00:00-03:00"))
        self.assertIsNone(row)
        self.assertEqual(refused, "not_linkable")
        self.assertIsNone(db_booking.get_booking_request(sid)["hsi_appointment_id"])

    def test_an_unchanged_appointment_links_once_its_request_becomes_linkable(self):
        # HSI showed the turno before the desk approved the request (or before the patient poll
        # learned the DNI): every later tick replays it unchanged, and one of them links it.
        at = "2026-11-20T14:30:00-03:00"
        self.assertEqual(self._observe("7016", "30200013", at, state="attended")[0], "imported")
        self.assertEqual(self._observe("7016", "30200013", at, state="attended", again=True)[0], "unchanged")
        sid = self._approved("30200013", at)
        self.assertEqual(self._observe("7016", "30200013", at, state="attended", again=True)[0], "confirmed")
        row = db_booking.get_booking_request(sid)
        self.assertEqual(row["hsi_appointment_id"], "7016")
        self.assertEqual(row["asistencia"], "presente")
        self.assertEqual(self._observe("7016", "30200013", at, state="attended", again=True)[0], "unchanged")
        self.assertEqual(len(self._events(sid, db_hsi.HSI_LINKED)), 1)

    def test_an_unchanged_appointment_of_a_past_day_is_not_retried(self):
        at = "2026-11-18T09:00:00-03:00"
        self.assertEqual(self._observe("7017", "30200014", at)[0], "imported")
        sid = self._approved("30200014", at)
        self.assertEqual(self._observe("7017", "30200014", at, again=True)[0], "unchanged")
        self.assertIsNone(db_booking.get_booking_request(sid)["hsi_appointment_id"])

    def test_rejecting_the_extra_candidate_links_the_other_and_settles_nothing_twice(self):
        at = "2026-11-20T15:00:00-03:00"
        first = self._approved("30200015", at)
        second = self._approved("30200015", at, overbooking=True)
        self.assertEqual(self._observe("7018", "30200015", at)[0], "conflict")
        # Replayed unchanged while both are still there: the conflict is not opened again.
        self.assertEqual(self._observe("7018", "30200015", at, again=True)[0], "unchanged")
        self.assertEqual(self._conflicts(second), ["ambiguous_local_requests_for_hsi_appointment"])
        db_booking.cancel(second, actor="test")
        self.assertEqual(self._observe("7018", "30200015", at, again=True)[0], "confirmed")
        self.assertEqual(db_booking.get_booking_request(first)["hsi_appointment_id"], "7018")
        self.assertIsNone(db_booking.get_booking_request(second)["hsi_appointment_id"])

    def test_an_intent_hsi_refused_for_good_does_not_block_the_link(self):
        # The server's create was refused for good ('conflict'): that is when the desk loads the
        # turno by hand, and the hand-loaded one must link.
        at = "2026-11-20T14:00:00-03:00"
        sid = self._approved("30200012", at)
        intent = db_hsi.hsi_outbox_enqueue("appointment", sid, "create", {"solicitud_id": sid, "when": at},
                                           "test-create-%s" % sid)
        _sql("UPDATE hsi_outbox SET state='conflict' WHERE id=%s", (intent["id"],))
        self.assertEqual(self._observe("7015", "30200012", at)[0], "confirmed")
        self.assertEqual(db_booking.get_booking_request(sid)["hsi_appointment_id"], "7015")

    def test_a_time_without_an_offset_never_links(self):
        sid = self._approved("30200009", "2026-11-20T13:30:00-03:00")
        self.assertEqual(self._observe("7012", "30200009", "2026-11-20T13:30:00")[0], "imported")
        row = db_booking.get_booking_request(sid)
        self.assertIsNone(row["hsi_appointment_id"])
        self.assertEqual(row["turno_dt"], datetime.fromisoformat("2026-11-20T13:30:00-03:00"))

    def test_unchanged_hospital_appointments_cost_one_read_per_tick_not_three_each(self):
        # Most unchanged appointments replayed every tick are the hospital's own: with no unlinked
        # request at their minute they must cost no query, whatever their number.
        at_request = "2026-11-20T16:00:00-03:00"
        self._approved("30200020", at_request)
        pool = hsi_journal._RelinkPool()
        names = ("hsi_booking_requests_by_appointment", "hsi_unlinked_approved_at",
                 "hsi_mirror_patient_dni", "hsi_diary_professional", "hsi_unlinked_approved_minutes")
        calls = {n: 0 for n in names}

        def counting(name):
            real = getattr(db_hsi, name)

            def wrapper(*args, **kwargs):
                calls[name] += 1
                return real(*args, **kwargs)
            return wrapper

        n = 12
        appts = [{"id": "8%03d" % i, "schedule_id": _DIARY, "patient_id": "p-x%d" % i,
                  "scheduled_at": "2026-11-21T%02d:%02d:00-03:00" % (8 + i // 4, (i % 4) * 15),
                  "state": "scheduled", "patient": {"id": "p-x%d" % i, "document_number": "3999%04d" % i}}
                 for i in range(n)]
        for appt in appts:
            db_hsi.hsi_inbox_record("test:%s" % appt["id"], "appointment", appt["id"], appt)
            hsi_journal._reconcile_appointment(appt, "test:%s" % appt["id"], relink_pool=pool)
        patchers = [patch.object(db_hsi, name, counting(name)) for name in names]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        for appt in appts:
            self.assertEqual(hsi_journal._reconcile_appointment(
                appt, "test:%s" % appt["id"], source_new=False, relink_pool=pool), "unchanged")
        self.assertEqual(calls["hsi_unlinked_approved_minutes"], 1)
        for name in names[:-1]:
            self.assertEqual(calls[name], 0, name)


class TestMirrorPatientStub(ExtensionLinkCase):
    def test_a_stub_without_dni_never_replaces_the_stored_record(self):
        full = {"id": "p-stub", "first_name": "Ana", "last_name": "Prueba"}
        db_hsi.hsi_mirror_patient("p-stub", None, full)
        self.assertIsNone(db_hsi.hsi_mirror_patient("p-stub", None, {"id": "p-stub"}))
        self.assertEqual(_sql("SELECT payload FROM hsi_patients WHERE hsi_patient_id='p-stub'")[0]["payload"], full)
        # A richer record without DNI still replaces a stub, and the DNI still arrives later.
        db_hsi.hsi_mirror_patient("p-stub2", None, {"id": "p-stub2"})
        self.assertIsNotNone(db_hsi.hsi_mirror_patient("p-stub2", None, {"id": "p-stub2", "first_name": "Luz"}))
        self.assertIsNotNone(db_hsi.hsi_mirror_patient("p-stub2", "30200021", {"id": "p-stub2"}))
        self.assertEqual(db_hsi.hsi_mirror_patient_dni("p-stub2"), "30200021")
        self.assertIsNone(db_hsi.hsi_mirror_patient("p-stub2", None, {"id": "p-stub2"}))

    def test_an_empty_dni_is_no_data_and_never_erases_the_stored_one(self):
        db_hsi.hsi_mirror_patient("p-empty", "30200022", {"id": "p-empty", "first_name": "Eva"})
        db_hsi.hsi_mirror_patient("p-empty", "", {"id": "p-empty"})
        db_hsi.hsi_mirror_patient("p-empty", "  ", {"id": "p-empty", "first_name": "Eva"})
        self.assertEqual(db_hsi.hsi_mirror_patient_dni("p-empty"), "30200022")
        db_hsi.hsi_mirror_patient("p-empty2", "", {"id": "p-empty2"})
        self.assertIsNone(db_hsi.hsi_mirror_patient_dni("p-empty2"))


if __name__ == "__main__":
    unittest.main()
