"""Focused contract tests for the HSI appointment handoff."""

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.connectors.hsi import hsi_client  # noqa: E402
import _env  # noqa: E402


class TestHsiConfirmedSlotContract(unittest.TestCase):
    def test_create_diary_sends_explicit_overturn_capacity(self):
        client = hsi_client.HsiClient()
        client._ensure_login = lambda: None
        captured = {}

        def fake_post(path, body=None, params=None):
            captured.update(body)
            return 12

        client._post = fake_post
        diary_id = client.create_diary(
            healthcare_professional_id=3,
            doctors_office_id=4,
            clinical_specialty_id=131,
            start_date="2099-01-01",
            end_date="2099-03-31",
            overturn_count=2,
        )

        self.assertEqual(diary_id, 12)
        self.assertTrue(captured["diaryOpeningHours"])
        self.assertTrue(all(
            row["overturnCount"] == 2
            for row in captured["diaryOpeningHours"]
        ))

    def test_diagnostic_practice_maps_to_owning_hsi_specialty(self):
        client = hsi_client.HsiClient()
        client.list_diaries = lambda: [
            {"diary_id": 9, "specialty": "Cardiología", "professional": "Dra. Prueba"},
            {"diary_id": 10, "specialty": "Kinesiología", "professional": "Lic. Prueba"},
        ]
        self.assertEqual(client._match_diary("Ergometría")["diary_id"], 9)

    def test_assigned_professional_is_not_silently_replaced(self):
        client = hsi_client.HsiClient()
        client.list_diaries = lambda: [
            {"diary_id": 9, "specialty": "Cardiología", "professional": "MEDINA DIEGO"},
            {"diary_id": 10, "specialty": "Cardiología", "professional": "CARDIODEMO MARIA"},
        ]
        self.assertEqual(client._match_diary("Ergometría", "Diego Medina")["diary_id"], 9)
        with self.assertRaises(hsi_client.HsiError):
            client._match_diary("Ergometría", "Jorge Inexistente")

    def test_create_appointment_returns_actual_slot_when_agenda_snaps_forward(self):
        client = hsi_client.HsiClient()
        client._ensure_login = lambda: None
        client._match_diary = lambda hint, professional_hint=None: {
            "diary_id": 9, "specialty": "Cardiología", "professional": "Dra. Prueba",
        }
        client.diary_detail = lambda diary_id: {
            "appointmentDuration": 30,
            "startDate": "2099-07-20", "endDate": "2099-07-20",
            "diaryOpeningHours": [{
                "openingHours": {"id": 3, "dayWeekId": 1, "from": "09:00:00", "to": "12:00:00"},
            }],
        }
        client._booked_hours = lambda diary_id, on_date: set()
        client._book = lambda *args, **kwargs: {"appointment_id": 77, "patient_id": 44}

        # Monday, and deliberately not on the 30-minute boundaries.
        result = client.create_appointment(44, "Cardiología", "2099-07-20T09:05:00")

        self.assertEqual(result["estado"], "ok")
        self.assertEqual(result["appointment_id"], 77)
        self.assertEqual(result["requested_turno_dt"], "2099-07-20T09:05:00-03:00")
        self.assertEqual(result["actual_turno_dt"], "2099-07-20T09:30:00-03:00")
        self.assertTrue(result["slot_changed"])

    def test_authorized_overturn_books_the_exact_taken_boundary(self):
        client = hsi_client.HsiClient()
        client._ensure_login = lambda: None
        client._match_diary = lambda hint, professional_hint=None: {
            "diary_id": 9, "specialty": "Cardiología", "professional": "Dra. Prueba",
        }
        client.diary_detail = lambda diary_id: {
            "appointmentDuration": 30,
            "startDate": "2099-07-20", "endDate": "2099-07-20",
            "diaryOpeningHours": [{
                "openingHours": {"id": 3, "dayWeekId": 1, "from": "09:00:00", "to": "12:00:00"},
            }],
        }
        client._booked_hours = lambda diary_id, on_date: {"09:00:00"}
        called = {}
        def fake_book(*args, **kwargs):
            called.update(kwargs)
            return {"appointment_id": 88, "patient_id": 55}
        client._book = fake_book

        result = client.create_appointment(55, "Cardiología", "2099-07-20T09:00:00",
                                           overturn=True)

        self.assertEqual(result["actual_turno_dt"], "2099-07-20T09:00:00-03:00")
        self.assertTrue(result["sobreturno"])
        self.assertTrue(called["overturn"])

    def test_assigned_professional_never_snaps_to_a_different_boundary(self):
        client = hsi_client.HsiClient()
        client._ensure_login = lambda: None
        client._match_diary = lambda hint, professional_hint=None: {
            "diary_id": 9, "specialty": "Cardiología", "professional": "Dra. Prueba",
        }
        client.diary_detail = lambda diary_id: {
            "appointmentDuration": 30,
            "startDate": "2099-07-20", "endDate": "2099-07-20",
            "diaryOpeningHours": [{
                "openingHours": {"id": 3, "dayWeekId": 1, "from": "09:00:00", "to": "12:00:00"},
            }],
        }
        client._booked_hours = lambda diary_id, on_date: {"09:00:00"}
        client._book = lambda *args, **kwargs: self.fail("an occupied exact slot must not be booked")

        with self.assertRaisesRegex(hsi_client.HsiError, "no exact free slot"):
            client.create_appointment(
                55, "Cardiología", "2099-07-20T09:00:00",
                professional_hint="Dra. Prueba", exact=True,
            )

    def test_replay_recognizes_a_prior_hsi_snap_for_the_same_patient(self):
        client = hsi_client.HsiClient()
        client._ensure_login = lambda: None
        client._match_diary = lambda hint, professional_hint=None: {
            "diary_id": 9, "specialty": "Cardiología", "professional": "Dra. Prueba",
        }
        client.diary_detail = lambda diary_id: {
            "appointmentDuration": 30, "startDate": "2099-07-20", "endDate": "2099-07-20",
            "diaryOpeningHours": [{"openingHours": {"id": 3, "dayWeekId": 1,
                                                     "from": "09:00:00", "to": "12:00:00"}}],
        }
        client._appointments_cache = {(9, "2099-07-20"): [
            {"id": 88, "date": "2099-07-20", "hour": "09:30:00",
             "appointmentStateId": 1, "patient": {"id": 55}},
        ]}
        client._booked_hours = lambda diary_id, on_date: {"09:30:00"}
        client._book = lambda *args, **kwargs: self.fail("replay must not create a duplicate")
        result = client.create_appointment(55, "Cardiología", "2099-07-20T09:00:00")
        self.assertTrue(result["deduplicated"])
        self.assertEqual(result["appointment_id"], 88)

    def test_cancel_sends_required_change_state_body(self):
        client = hsi_client.HsiClient(); client._ensure_login = lambda: None
        checkpoint = {"id": 12, "diaryId": 9, "date": "2099-07-20", "hour": "09:00:00",
                      "appointmentStateId": 1, "patientId": 55}
        remote = {"id": 12, "diaryId": 9, "date": "2099-07-20",
                  "hour": "09:00:00", "appointmentStateId": 1,
                  "patient": {"id": 55}}
        client.appointments_for_diary = lambda *args: [dict(remote)]
        captured = {}
        def put(path, body=None, params=None):
            captured.update({"path": path, "body": body, "params": params})
            remote["appointmentStateId"] = params["appointmentStateId"]
        client._put = put
        result = client.cancel_appointment(12, checkpoint)
        self.assertEqual(captured["params"], {"appointmentStateId": 4})
        self.assertEqual(captured["body"], {"reason": None, "patientIdentificationBarCode": None, "reasonId": None})
        self.assertEqual(result["checkpoint"]["appointmentStateId"], 4)

    def test_cancel_accepts_diary_disappearance_and_replay_is_idempotent(self):
        client = hsi_client.HsiClient(); client._ensure_login = lambda: None
        client.diary_detail = lambda _id: {"startDate": "2099-07-01", "endDate": "2099-08-31"}
        checkpoint = {"id": 12, "diaryId": 9, "date": "2099-07-20", "hour": "09:00:00",
                      "appointmentStateId": 1, "patientId": 55}
        remote = [{"id": 12, "diaryId": 9, "date": "2099-07-20",
                   "hour": "09:00:00", "appointmentStateId": 1,
                   "patient": {"id": 55}}]
        client.appointments_for_diary = lambda *args: list(remote)
        states = []
        def put(_path, body=None, params=None):
            states.append(params["appointmentStateId"])
            remote.clear()
        client._put = put

        first = client.cancel_appointment(12, checkpoint)
        self.assertEqual(first["checkpoint"]["appointmentStateId"], 4)
        self.assertEqual(first["patient_id"], 55)
        self.assertEqual(states, [4])

        replay = client.cancel_appointment(12, first["checkpoint"])
        self.assertEqual(replay["checkpoint"]["appointmentStateId"], 4)
        self.assertTrue(replay["deduplicated"])
        self.assertEqual(states, [4])

    def test_cancel_old_day_absence_finds_same_id_moved_to_new_day(self):
        client = hsi_client.HsiClient(); client._ensure_login = lambda: None
        checkpoint = {"id": 12, "diaryId": 9, "date": "2099-07-20", "hour": "09:00:00",
                      "appointmentStateId": 1, "patientId": 55}
        moved = {**checkpoint, "date": "2099-07-21"}
        client.diary_detail = lambda _id: {"startDate": "2099-07-01", "endDate": "2099-08-31"}
        client.appointments_for_diary = lambda _id, start, end: (
            [moved] if (start, end) == ("2099-07-01", "2099-08-31") else [])
        client._put = lambda *_a, **_k: self.fail("old-day absence must not send change-state")
        with self.assertRaisesRegex(hsi_client.HsiConflict, "hsi_checkpoint_drift") as caught:
            client.cancel_appointment(12, checkpoint)
        self.assertEqual(caught.exception.remote["date"], "2099-07-21")

    def test_cancel_post_put_disappearance_checks_new_day(self):
        client = hsi_client.HsiClient(); client._ensure_login = lambda: None
        checkpoint = {"id": 12, "diaryId": 9, "date": "2099-07-20", "hour": "09:00:00",
                      "appointmentStateId": 1, "patientId": 55}
        moved = {**checkpoint, "date": "2099-07-21"}
        client.diary_detail = lambda _id: {"startDate": "2099-07-01", "endDate": "2099-08-31"}
        wrote = []
        client._put = lambda *_a, **_k: wrote.append(True)
        client.appointments_for_diary = lambda _id, start, end: (
            [moved] if (start, end) == ("2099-07-01", "2099-08-31") else
            ([checkpoint] if not wrote else []))
        with self.assertRaisesRegex(hsi_client.HsiConflict, "hsi_checkpoint_drift"):
            client.cancel_appointment(12, checkpoint)
        self.assertEqual(wrote, [True])

    def test_cancel_absence_requires_complete_diary_range_and_list_payload(self):
        client = hsi_client.HsiClient(); client._ensure_login = lambda: None
        checkpoint = {"id": 12, "diaryId": 9, "date": "2099-07-20", "hour": "09:00:00",
                      "appointmentStateId": 1, "patientId": 55}
        client._put = lambda *_a, **_k: self.fail("uncertain absence must not mutate")
        client.appointments_for_diary = lambda *_a: []
        for detail in ({}, {"startDate": "2099-07-01"},
                       {"startDate": "2099-08-01", "endDate": "2099-07-01"},
                       {"startDate": "2098-01-01", "endDate": "2100-01-01"}):
            with self.subTest(detail=detail):
                client.diary_detail = lambda _id: detail
                with self.assertRaisesRegex(hsi_client.HsiError, "hsi_cancel_absence_unverified"):
                    client.cancel_appointment(12, checkpoint)
        client.diary_detail = lambda _id: {"startDate": "2099-07-01", "endDate": "2099-08-31"}
        client.appointments_for_diary = hsi_client.HsiClient.appointments_for_diary.__get__(client)
        client._get = lambda *_a, **_k: None
        with self.assertRaisesRegex(hsi_client.HsiError, "hsi_diary_appointments_unverified"):
            client.cancel_appointment(12, checkpoint)

    def test_attendance_uses_verified_hsi_states_and_is_idempotent(self):
        client = hsi_client.HsiClient(); client._ensure_login = lambda: None
        checkpoint = {"id": 12, "diaryId": 9, "date": "2099-07-20", "hour": "09:00:00",
                      "appointmentStateId": 1, "patientId": 55}
        states = []
        remote = {
            "id": 12, "diaryId": 9, "date": "2099-07-20", "hour": "09:00:00",
            "appointmentStateId": 1, "patient": {"id": 55},
            "updatedOn": "v1",
        }
        version = [1]
        client.appointments_for_diary = lambda *args: [dict(remote)]
        def put(_path, body=None, params=None):
            states.append(params["appointmentStateId"])
            remote["appointmentStateId"] = params["appointmentStateId"]
            version[0] += 1
            remote["updatedOn"] = "v%d" % version[0]
        client._put = put
        absent = client.mark_attendance(12, checkpoint, "ausente")
        self.assertEqual(absent["checkpoint"]["appointmentStateId"], 3)
        self.assertEqual(absent["checkpoint"]["updatedOn"], "v2")
        self.assertEqual(states, [3])

        states.clear()
        present = client.mark_attendance(12, absent["checkpoint"], "presente")
        self.assertEqual(present["checkpoint"]["appointmentStateId"], 5)
        self.assertEqual(present["checkpoint"]["updatedOn"], "v3")
        self.assertEqual(states, [5])

    def test_reassign_uses_general_update_date_and_confirms_target(self):
        client = hsi_client.HsiClient(); client._ensure_login = lambda: None
        checkpoint = {"id": 12, "diaryId": 9, "date": "2099-07-20", "hour": "09:00:00",
                      "appointmentStateId": 1, "patientId": 55}
        calls = {"reads": 0}
        def appointments(*args):
            calls["reads"] += 1
            hour = "09:30:00" if calls["reads"] >= 3 else "09:00:00"
            return [{"id": 12, "date": "2099-07-20", "hour": hour,
                     "appointmentStateId": 1, "patient": {"id": 55}}]
        client.appointments_for_diary = appointments
        client.diary_detail = lambda id_: {"appointmentDuration": 30, "diaryOpeningHours": [
            {"openingHours": {"dayWeekId": 1, "from": "08:00:00", "to": "12:00:00", "id": 3}}]}
        captured = {}
        client._put = lambda path, body=None, params=None: captured.update({"path": path, "body": body})
        result = client.reassign_appointment(12, checkpoint, "2099-07-20T09:30:00-03:00")
        self.assertTrue(captured["path"].endswith("/12/update-date"))
        self.assertEqual(captured["body"]["date"]["time"]["minutes"], 30)
        self.assertEqual(result["actual_turno_dt"], "2099-07-20T09:30:00-03:00")

    def test_reassign_finds_a_concurrent_move_elsewhere_in_the_diary(self):
        client = hsi_client.HsiClient(); client._ensure_login = lambda: None
        checkpoint = {"id": 12, "diaryId": 9, "date": "2099-07-20", "hour": "09:00:00",
                      "appointmentStateId": 1, "patientId": 55}
        moved = {"id": 12, "date": "2099-08-03", "hour": "10:30:00",
                 "appointmentStateId": 1, "patient": {"id": 55}}
        def appointments(_diary_id, from_date, to_date):
            return [dict(moved)] if from_date == "2099-07-01" and to_date == "2099-09-30" else []
        client.appointments_for_diary = appointments
        client.diary_detail = lambda _id: {"startDate": "2099-07-01", "endDate": "2099-09-30"}

        with self.assertRaisesRegex(hsi_client.HsiConflict, "hsi_checkpoint_drift") as caught:
            client.reassign_appointment(12, checkpoint, "2099-07-27T09:30:00-03:00")

        self.assertEqual(caught.exception.remote["date"], "2099-08-03")
        self.assertEqual(caught.exception.remote["diaryId"], 9)

    def test_push_turno_requires_exact_slot_when_professional_is_assigned(self):
        captured = {}

        class FakeClient:
            def login(self):
                pass

            def ensure_patient(self, *args):
                return 55

            def create_appointment(self, *args, **kwargs):
                captured.update(kwargs)
                return {"estado": "ok"}

        with patch.object(hsi_client, "HsiClient", FakeClient), _env.patched_env({"TURNOS_SKIP_HSI": "0"}, clear=False):
            hsi_client.push_turno(
                "30123456", "Ana", "Prueba", "1980-01-01", "F",
                "Cardiología", "2099-07-20T09:00:00", medico="Dra. Prueba",
            )

        self.assertTrue(captured["exact"])


if __name__ == "__main__":
    unittest.main()
