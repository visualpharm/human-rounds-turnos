"""test_booking_requests_paths.py — the less travelled paths of modules/turnos/booking_requests.py.

The main suites (test_booking_requests.py, test_create_request_steps.py, the router and store tests) pin the
patient's and the desk's usual journeys. This file pins what they leave out, through each function's boundary:
the EMR push and its answers, the owner's settings (theme, sign-in mode, anti-bot gate), the referral retention
sweep, the catalog and session helpers, and the refusals of the desk and patient operations (400/404/409/503).
The collaborators that reach the database, the catalog, the EMR or the clock are replaced by recorders, so no
database is needed and nothing here depends on how the module is cut into files.

Run:  python3 -m unittest discover -s tests -p test_booking_requests_paths.py
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.modules.turnos import booking_requests as br  # noqa: E402
from app.modules.turnos import booking_capacity  # noqa: E402
from app.modules.turnos import booking_create  # noqa: E402
from app.modules.turnos import booking_decisions  # noqa: E402
from app.modules.turnos import booking_emr  # noqa: E402
from app.modules.turnos import booking_patient  # noqa: E402
from app.modules.turnos import booking_queue  # noqa: E402
from app.modules.turnos import booking_referrals  # noqa: E402
from app.modules.turnos import panel_settings  # noqa: E402
from app.modules.turnos import booking_requests_store as store  # noqa: E402

ART = br._ART


def _dt(y, mo, d, h=9, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=ART)


# ---- the EMR's confirmed instant --------------------------------------------------------------------------

class TestEmrInstant(unittest.TestCase):
    def test_parse(self):
        self.assertIsNone(booking_patient._parse_emr_appointment_dt(None))
        self.assertIsNone(booking_patient._parse_emr_appointment_dt("31/12/2026"))
        self.assertEqual(booking_patient._parse_emr_appointment_dt("2026-11-20T09:00:00"), _dt(2026, 11, 20))
        self.assertEqual(booking_patient._parse_emr_appointment_dt("2026-11-20T12:00:00+00:00"), _dt(2026, 11, 20))


# ---- owner's settings -----------------------------------------------------------------------------------------

class TestOwnerSettings(unittest.TestCase):
    def test_theme(self):
        with mock.patch.object(panel_settings.theme, "current", return_value="humanrounds"):
            status, body = panel_settings._theme_get_core()
        self.assertEqual((status, body["tema"]), (200, "humanrounds"))
        self.assertEqual(len(body["themes"]), 2)
        self.assertEqual(panel_settings._theme_set_core({"tema": "no-such"})[0], 400)
        self.assertEqual(panel_settings._theme_set_core(None)[0], 400)
        with mock.patch.object(panel_settings.theme, "save") as save:
            self.assertEqual(panel_settings._theme_set_core({"tema": " Pinamar "}), (200, {"ok": True, "tema": "pinamar"}))
        save.assert_called_once_with("pinamar")
        with mock.patch.object(panel_settings.theme, "save", side_effect=store.DbUnavailable("down")):
            self.assertEqual(panel_settings._theme_set_core({"tema": "pinamar"})[0], 503)

    def test_auth_mode(self):
        from app.auth import mode as auth_mode
        from app.cuentas import accounts
        with mock.patch.object(auth_mode, "current", return_value="password"), \
                mock.patch.object(accounts, "_renaper_provider", return_value="fake"):
            status, body = panel_settings._auth_mode_get_core()
        self.assertEqual((status, body["auth_mode"], body["renaper_provider"]), (200, "password", "fake"))
        self.assertEqual(len(body["modes"]), 2)
        # Whether RENAPER can run decides card vs one «Sin configurar» line
        # in the panel (2026-11-08 D8): the IDP credentials or the fake.
        from app.cuentas import auth_renaper
        for configured in (True, False):
            with mock.patch.object(auth_renaper, "configurado", return_value=configured):
                self.assertIs(panel_settings._auth_mode_get_core()[1]["renaper_conectado"], configured)
        self.assertEqual(panel_settings._auth_mode_set_core({"auth_mode": "fax"})[0], 400)
        with mock.patch.object(auth_mode, "save") as save:
            self.assertEqual(panel_settings._auth_mode_set_core({"auth_mode": "RENAPER"}),
                             (200, {"ok": True, "auth_mode": "renaper"}))
        save.assert_called_once_with("renaper")
        with mock.patch.object(auth_mode, "save", side_effect=store.DbUnavailable("down")):
            self.assertEqual(panel_settings._auth_mode_set_core({"auth_mode": "password"})[0], 503)

    def test_turnstile(self):
        from app.auth import turnstile
        with mock.patch.object(turnstile, "encendido", return_value=True), \
                mock.patch.object(turnstile, "_secret", return_value=""), \
                mock.patch.object(turnstile, "_apagado", return_value=False):
            status, body = panel_settings._turnstile_get_core({"host": "localhost:8000"})
        self.assertEqual(status, 200)
        self.assertEqual((body["on"], body["configurado"], body["forzado_off_env"]), (True, False, False))
        self.assertEqual(len(body["options"]), 2)
        self.assertEqual(panel_settings._turnstile_set_core({"on": "yes"})[0], 400)
        self.assertEqual(panel_settings._turnstile_set_core({})[0], 400)
        with mock.patch.object(turnstile, "guardar") as guardar:
            self.assertEqual(panel_settings._turnstile_set_core({"on": False}), (200, {"ok": True, "on": False}))
        guardar.assert_called_once_with(False)
        with mock.patch.object(turnstile, "guardar", side_effect=store.DbUnavailable("down")):
            self.assertEqual(panel_settings._turnstile_set_core({"on": True})[0], 503)


# ---- small helpers --------------------------------------------------------------------------------------------

class TestHelpers(unittest.TestCase):
    def test_slot_instant(self):
        self.assertEqual(br._slot_dt_art("2026-11-20", "09:30"), _dt(2026, 11, 20, 9, 30))
        self.assertIsNone(br._slot_dt_art("2026-02-30", "09:30"))

    def test_no_show_limit_is_clamped(self):
        for raw, want in (("2", 2), ("0", 1), ("99", 10), ("tres", 3)):
            with _env.patched_env(TURNOS_NO_SHOW_LIMIT=raw):
                self.assertEqual(br._no_show_limit(), want, raw)

    def test_dict_from_items(self):
        self.assertEqual(br._dict_from_items({"a": {"name": "A"}, "b": {}, "c": "C"}), {"a": "A", "b": "b", "c": "C"})
        self.assertEqual(br._dict_from_items([{"slug": "s"}, {"servicio_id": "x", "nombre": "X"}, {}, "plain", 3]),
                         {"s": "s", "x": "X", "plain": "plain"})
        self.assertEqual(br._dict_from_items(None), {})

    def test_services_from_path(self):
        tmp = tempfile.mkdtemp(prefix="br-paths-")
        self.addCleanup(shutil.rmtree, tmp, True)
        self.assertIsNone(br._services_from_path(os.path.join(tmp, "missing.json")))
        bad = os.path.join(tmp, "bad.json")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("{nope")
        self.assertIsNone(br._services_from_path(bad))
        bare = os.path.join(tmp, "bare.json")
        with open(bare, "w", encoding="utf-8") as f:
            json.dump([{"id": "a", "nombre": "A"}], f)
        self.assertEqual(br._services_from_path(bare), {"a": "A"})
        empty = os.path.join(tmp, "empty.json")
        with open(empty, "w", encoding="utf-8") as f:
            json.dump({"servicios": []}, f)
        self.assertIsNone(br._services_from_path(empty))

    def test_user_dict_and_session_dni(self):
        self.assertEqual(br._user_dict(None), {})
        person = SimpleNamespace(dni="27.888.999", nombre="Ana", apellido=None)
        self.assertEqual(br._user_dict(person), {"dni": "27.888.999", "nombre": "Ana"})
        self.assertIsNone(br.session_dni(None))
        self.assertEqual(br.session_dni(person), "27888999")

    def test_get_user(self):
        mcp_user = {"dni": "1"}
        self.assertIs(br._get_user(SimpleNamespace(_usuario_mcp=mcp_user)), mcp_user)
        from app.cuentas import accounts
        with mock.patch.object(accounts, "current_user", return_value={"dni": "2"}):
            self.assertEqual(br._get_user(SimpleNamespace()), {"dni": "2"})
        with mock.patch.object(accounts, "current_user", side_effect=RuntimeError("no session store")):
            self.assertIsNone(br._get_user(SimpleNamespace()))

    def test_age_helpers(self):
        self.assertFalse(booking_create._out_of_range(None, {"min": 1}))
        self.assertFalse(booking_create._out_of_range(5, None))
        self.assertTrue(booking_create._out_of_range(5, {"min": 6}))
        self.assertTrue(booking_create._out_of_range(16, {"max": 15}))
        self.assertFalse(booking_create._out_of_range(10, {"min": 6, "max": 15}))
        from app.cuentas import accounts
        with mock.patch.object(accounts, "_age_years", side_effect=ValueError("bad date")):
            self.assertIsNone(booking_create._patient_age_years("garbage"))


# ---- referral retention and pending analyses ------------------------------------------------------------------

class _ReferralsDir(unittest.TestCase):
    """A throwaway REFERRALS_DIR for the test; the module's log line is silenced."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="br-paths-ordenes-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        for patcher in (mock.patch.object(br, "REFERRALS_DIR", self.dir), mock.patch.object(booking_referrals, "_log")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def touch(self, rel, age_h=0):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"%PDF-1.4")
        if age_h:
            then = datetime.now().timestamp() - age_h * 3600
            os.utime(path, (then, then))
        return path


class TestReapplyPurged(_ReferralsDir):
    def test_removes_only_files_journaled_under_ordenes(self):
        back = self.touch("12.pdf")
        pending = self.touch("pending/tok.json")
        with mock.patch.object(store, "purged_file_paths",
                               return_value={"ordenes/12.pdf", "ordenes/13.pdf", "ordenes/pending/tok.json",
                                             "backups/x.tar"}):
            self.assertEqual(booking_referrals.reapply_purged_files(), 1)
        self.assertFalse(os.path.exists(back))
        self.assertTrue(os.path.exists(pending))

    def test_unreadable_journal_removes_nothing(self):
        kept = self.touch("12.pdf")
        with mock.patch.object(store, "purged_file_paths", side_effect=store.DbUnavailable("down")):
            self.assertEqual(booking_referrals.reapply_purged_files(), 0)
        self.assertTrue(os.path.exists(kept))

    def test_a_file_that_cannot_be_removed_does_not_raise(self):
        self.touch("12.pdf")
        with mock.patch.object(store, "purged_file_paths", return_value={"ordenes/12.pdf"}), \
                mock.patch.object(br.os, "remove", side_effect=PermissionError("ro")):
            self.assertEqual(booking_referrals.reapply_purged_files(), 0)


class TestSweepReferrals(_ReferralsDir):
    def sweep(self, rows, **patches):
        calls = {"record": [], "clear": []}
        record = patches.pop("record", lambda rel, why: calls["record"].append((rel, why)))
        clear = patches.pop("clear", lambda sid: calls["clear"].append(sid))
        with mock.patch.object(booking_referrals, "reapply_purged_files", return_value=0), \
                mock.patch.object(store, "referrals_to_purge", return_value=rows), \
                mock.patch.object(store, "purged_file_record", side_effect=record), \
                mock.patch.object(store, "clear_referral_path", side_effect=clear):
            return booking_referrals.sweep_referrals(**patches), calls

    def test_disabled_window_sweeps_nothing(self):
        with mock.patch.object(store, "referrals_to_purge") as candidates:
            self.assertEqual(booking_referrals.sweep_referrals(retention_days=0), 0)
        candidates.assert_not_called()

    def test_journals_before_deleting_and_clears_missing_files_too(self):
        on_disk = self.touch("1.pdf")
        outside = "/elsewhere/old-volume/2.jpg"
        n, calls = self.sweep([{"id": 1, "orden_path": on_disk}, {"id": 2, "orden_path": outside},
                               {"id": 3, "orden_path": None}], retention_days=30)
        self.assertEqual(n, 3)
        self.assertFalse(os.path.exists(on_disk))
        self.assertEqual(calls["record"], [("ordenes/1.pdf", "retention"), ("ordenes/2.jpg", "retention")])
        self.assertEqual(calls["clear"], [1, 2, 3])

    def test_a_broken_row_does_not_stop_the_rest(self):
        def record(rel, why):
            if rel.endswith("1.pdf"):
                raise OSError("disk")
            if rel.endswith("2.pdf"):
                raise RuntimeError("db")
        n, _calls = self.sweep([{"id": 1, "orden_path": self.touch("1.pdf")},
                                {"id": 2, "orden_path": self.touch("2.pdf")},
                                {"id": 3, "orden_path": self.touch("3.pdf")}], retention_days=30, record=record)
        self.assertEqual(n, 1)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "1.pdf")))

    def test_unreadable_candidates_is_zero(self):
        with mock.patch.object(booking_referrals, "reapply_purged_files", return_value=0), \
                mock.patch.object(store, "referrals_to_purge", side_effect=store.DbUnavailable("down")):
            self.assertEqual(booking_referrals.sweep_referrals(retention_days=30), 0)

    def test_window_comes_from_the_setting(self):
        for raw, want in (("45", 45), ("", 180), ("x", 180)):
            with _env.patched_env(TURNOS_REFERRAL_RETENTION_DAYS=raw):
                self.assertEqual(booking_referrals._referral_retention_days(), want, raw)
        for raw, want in (("6", 6), ("", 24), ("x", 24)):
            with _env.patched_env(TURNOS_REFERRAL_SWEEP_INTERVAL_H=raw):
                self.assertEqual(booking_referrals._referral_sweep_interval_h(), want, raw)


class TestSweepThread(unittest.TestCase):
    def test_loop_survives_a_failing_pass_and_stops_on_the_event(self):
        stop = mock.Mock()
        stop.is_set.side_effect = [False, False, True]
        with mock.patch.object(booking_referrals, "_referral_sweep_stop", stop), mock.patch.object(booking_referrals, "_log") as log, \
                mock.patch.object(booking_referrals, "sweep_referrals", side_effect=[RuntimeError("boom"), 0]) as sweep:
            booking_referrals._referral_sweep_loop(2)
        self.assertEqual(sweep.call_count, 2)
        stop.wait.assert_called_with(7200)
        self.assertEqual(log.call_count, 1)

    def test_start_refuses_when_off_disabled_or_delegated(self):
        from app.core import queue_infra
        with mock.patch.object(booking_referrals, "_log"), mock.patch.object(booking_referrals.threading, "Thread") as thread:
            with _env.patched_env(TURNOS_REFERRAL_SWEEP_OFF="1"):
                booking_referrals.start_referral_sweep()
            with _env.patched_env(TURNOS_REFERRAL_SWEEP_OFF=None, TURNOS_REFERRAL_RETENTION_DAYS="0"):
                booking_referrals.start_referral_sweep()
            with _env.patched_env(TURNOS_REFERRAL_SWEEP_OFF=None, TURNOS_REFERRAL_RETENTION_DAYS="30"), \
                    mock.patch.object(queue_infra, "worker_mode", return_value="1"):
                booking_referrals.start_referral_sweep()
            thread.assert_not_called()

    def test_start_launches_one_daemon_and_never_raises(self):
        from app.core import queue_infra
        with _env.patched_env(TURNOS_REFERRAL_SWEEP_OFF=None, TURNOS_REFERRAL_RETENTION_DAYS="30",
                              TURNOS_REFERRAL_SWEEP_INTERVAL_H="12"), \
                mock.patch.object(queue_infra, "worker_mode", return_value=None), \
                mock.patch.object(booking_referrals, "_log"), mock.patch.object(booking_referrals.threading, "Thread") as thread:
            booking_referrals.start_referral_sweep()
            thread.assert_called_once()
            self.assertEqual(thread.call_args.kwargs["args"], (12,))
            self.assertTrue(thread.call_args.kwargs["daemon"])
            thread.return_value.start.side_effect = RuntimeError("no threads")
            booking_referrals.start_referral_sweep()


class TestPendingAnalyses(_ReferralsDir):
    def test_load_refuses_bad_tokens_and_broken_files(self):
        self.assertIsNone(booking_referrals._load_pending(None))
        self.assertIsNone(booking_referrals._load_pending("../etc/passwd"))
        self.assertIsNone(booking_referrals._load_pending("missing"))
        path = self.touch("pending/broken.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{")
        self.assertIsNone(booking_referrals._load_pending("broken"))
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ext": "pdf"}, f)
        self.assertEqual(booking_referrals._load_pending("broken"), {"ext": "pdf"})

    def test_discard_is_silent(self):
        booking_referrals._discard_pending_meta("../x")
        booking_referrals._discard_pending_meta("missing")
        path = self.touch("pending/tok.json")
        booking_referrals._discard_pending_meta("tok")
        self.assertFalse(os.path.exists(path))

    def test_cleanup_removes_only_old_files(self):
        booking_referrals._cleanup_pending()  # no pending dir yet: nothing to do
        old = self.touch("pending/old.json", age_h=72)
        fresh = self.touch("pending/fresh.json")
        os.makedirs(os.path.join(self.dir, "pending", "subdir"))
        booking_referrals._cleanup_pending(max_age_h=48)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(fresh))
        with mock.patch.object(br.os, "listdir", side_effect=OSError("gone")):
            booking_referrals._cleanup_pending()


# ---- desk and patient operations: their refusals and best-effort tails ----------------------------------------

FUTURE = datetime.now(timezone.utc) + timedelta(days=10)
PAST = datetime.now(timezone.utc) - timedelta(days=1)
DESK = {"usuario": "mesa1", "rol": "operador"}


def _broken_body():
    raise ValueError("Expecting value")


class _Ops(unittest.TestCase):
    """Patches the module's store with recorders; the row each test reads is `self.row`."""

    def setUp(self):
        self.row = {"id": 7, "dni": "27888999", "estado": "aprobada", "turno_dt": FUTURE,
                    "servicio_id": "cardiologia", "centro_slug": "hospital"}
        self.patch(store, "get_booking_request", side_effect=lambda sid: self.row)
        self.patch(br, "_admin_row", side_effect=lambda row: {"id": (row or {}).get("id"), "admin": True})
        self.patch(booking_patient, "_public_detail", side_effect=lambda row: {"id": (row or {}).get("id"), "public": True})
        self.patch(br, "_emr_sync_enabled", return_value=False)

    def patch(self, target, name, **kwargs):
        patcher = mock.patch.object(target, name, **kwargs)
        found = patcher.start()
        self.addCleanup(patcher.stop)
        return found


class TestMarkAttendance(_Ops):
    def setUp(self):
        super().setUp()
        self.patch(br.admin_auth, "centros_permitidos", return_value=None)
        self.mark = self.patch(store, "mark_attendance", return_value=dict(self.row, asistencia="presente"))
        self.patch(store, "attendance_summary_dni", return_value={"ausentes": 0})
        from app.modules.turnos import patient_ranking
        self.patch(patient_ranking, "by_dni", return_value={"nivel": 1})

    def test_other_center_is_404_before_the_body(self):
        br.admin_auth.centros_permitidos.return_value = {"caps-1"}
        self.assertEqual(booking_decisions._mark_attendance_core(DESK, 7, _broken_body)[0], 404)
        self.row = None
        self.assertEqual(booking_decisions._mark_attendance_core(DESK, 7, _broken_body)[0], 404)

    def test_body_and_value_refusals(self):
        self.assertEqual(booking_decisions._mark_attendance_core(DESK, 7, _broken_body)[0], 400)
        self.assertEqual(booking_decisions._mark_attendance_core(DESK, 7, lambda: {"asistencia": "tarde"}),
                         (400, {"error": "asistencia_invalida"}))
        self.mark.side_effect = ValueError("asistencia_no_elegible")
        self.assertEqual(booking_decisions._mark_attendance_core(DESK, 7, lambda: {"asistencia": "presente"}),
                         (409, {"error": "asistencia_no_elegible"}))
        self.mark.side_effect = ValueError("asistencia_futura")
        self.assertEqual(booking_decisions._mark_attendance_core(DESK, 7, lambda: {"asistencia": "presente"})[0], 400)
        self.mark.side_effect, self.mark.return_value = None, None
        self.assertEqual(booking_decisions._mark_attendance_core(DESK, 7, lambda: {"asistencia": "ausente"})[0], 404)

    def test_marked_row_carries_summary_and_ranking(self):
        status, body = booking_decisions._mark_attendance_core(DESK, 7, lambda: {"asistencia": " PRESENTE "})
        self.assertEqual(status, 200)
        self.assertEqual(body["hsi_sync"], "sin_turno_hsi_vinculado")
        self.assertEqual(body["solicitud"]["asistencia_resumen"], {"ausentes": 0})
        self.assertEqual(body["solicitud"]["ranking"], {"nivel": 1})
        self.mark.assert_called_once_with(7, "presente", "mesa1")

    def test_linked_appointment_goes_through_the_journal(self):
        from app.modules.turnos import hsi_journal
        br._emr_sync_enabled.return_value = True
        self.mark.return_value = dict(self.row, hsi_appointment_id="a1")
        self.row = dict(self.row, hsi_appointment_id="a1", hsi_estado="ok")
        with mock.patch.object(hsi_journal, "enqueue_attendance") as enqueue, \
                mock.patch.object(hsi_journal, "replay") as replay:
            status, body = booking_decisions._mark_attendance_core(DESK, 7, lambda: {"asistencia": "ausente"})
        self.assertEqual((status, body["hsi_sync"]), (200, "ok"))
        enqueue.assert_called_once()
        replay.assert_called_once_with(limit=1)
        with mock.patch.object(hsi_journal, "enqueue_attendance", side_effect=RuntimeError("db")):
            status, body = booking_decisions._mark_attendance_core(DESK, 7, lambda: {"asistencia": "ausente"})
        self.assertEqual((status, body["hsi_sync"]), (200, "pendiente"))


class TestRetryHsi(_Ops):
    def setUp(self):
        super().setUp()
        br._emr_sync_enabled.return_value = True

    def test_refusals(self):
        br._emr_sync_enabled.return_value = False
        self.assertEqual(booking_emr._retry_hsi_core(7), (409, {"error": "hsi_desactivado"}))
        br._emr_sync_enabled.return_value = True
        self.row = None
        self.assertEqual(booking_emr._retry_hsi_core(7)[0], 404)
        self.row = {"id": 7, "estado": "pendiente", "turno_dt": FUTURE}
        self.assertEqual(booking_emr._retry_hsi_core(7)[0], 400)
        self.row = {"id": 7, "estado": "aprobada", "turno_dt": PAST.replace(tzinfo=None)}
        self.assertEqual(booking_emr._retry_hsi_core(7), (400, {"error": "turno_pasado"}))

    def test_already_confirmed_is_200_without_journal(self):
        from app.modules.turnos import hsi_journal
        self.row = dict(self.row, hsi_estado="ok")
        with mock.patch.object(hsi_journal, "enqueue_appointment") as enqueue:
            self.assertEqual(booking_emr._retry_hsi_core(7)[0], 200)
        enqueue.assert_not_called()

    def test_journal_failure_is_502_with_the_generic_reason(self):
        from app.modules.turnos import hsi_journal
        self.row = dict(self.row, hsi_estado="error")
        with mock.patch.object(hsi_journal, "enqueue_appointment", side_effect=RuntimeError("db")), \
                mock.patch.object(hsi_journal, "motivo_operador", side_effect=RuntimeError("no outcome")):
            status, body = booking_emr._retry_hsi_core(7)
        self.assertEqual((status, body["error"]), (502, "hsi_no_confirmo"))
        self.assertTrue(body["detalle"])


class TestRejectAndAssign(_Ops):
    def test_reject_tails_are_best_effort(self):
        self.assertEqual(booking_decisions._reject_core(DESK, 7, {"motivo": "  "})[0], 400)
        self.patch(store, "reject", return_value=dict(self.row, estado="rechazada"))
        self.patch(store, "holds_release_booking_request", side_effect=RuntimeError("db"))
        from app.notifications import send
        self.patch(send, "notify_rejected", side_effect=RuntimeError("smtp"))
        self.assertEqual(booking_decisions._reject_core(DESK, 7, {"motivo": "Sin orden"}), (200, {"ok": True,
                         "solicitud": {"id": 7, "admin": True}}))
        store.reject.side_effect = store.AlreadyProcessed()
        self.assertEqual(booking_decisions._reject_core(DESK, 7, {"motivo": "Sin orden"})[0], 409)

    def test_assign_refusals(self):
        self.assertEqual(booking_decisions._assign_core(DESK, {"estado": "aprobada"}, 7, _broken_body)[0], 409)
        pending = {"estado": "pendiente"}
        self.assertEqual(booking_decisions._assign_core(DESK, pending, 7, _broken_body)[0], 400)
        from app.patient_web import catalog
        with mock.patch.object(catalog, "doctors", side_effect=RuntimeError("catalog")):
            self.assertEqual(booking_decisions._assign_core(DESK, pending, 7, lambda: {"medico_id": "d1"})[0], 400)
        doctors = [{"id": "d0", "nombre": "Dr. Old", "_borrado": True}, {"id": "d1", "nombre": "Dr. One"}]
        assign = self.patch(store, "assign_pending_booking_request", return_value=None)
        from app.core import access_log
        with mock.patch.object(catalog, "doctors", return_value=doctors), \
                mock.patch.object(access_log, "record"):
            self.assertEqual(booking_decisions._assign_core(DESK, pending, 7, lambda: {"medico_id": "d0"})[0], 400)
            self.assertEqual(booking_decisions._assign_core(DESK, pending, 7, lambda: {"medico": "Dr. One"})[0], 409)
            assign.return_value = {"id": 7}
            self.assertEqual(booking_decisions._assign_core(DESK, pending, 7, lambda: {"medico_id": "d1"})[0], 200)
        assign.assert_called_with(7, "Dr. One", "d1")

    def test_assign_leaves_an_access_row_first_and_assigns_nothing_without_it(self):
        # Security review 2026-11 (M-2): the assignment makes the patient the doctor's.
        from app.core import access_log
        from app.patient_web import catalog
        assign = self.patch(store, "assign_pending_booking_request", return_value={"id": 7})
        pending = {"estado": "pendiente", "dni": "30111222"}
        doctors = [{"id": "d1", "nombre": "Dr. One"}]
        with mock.patch.object(catalog, "doctors", return_value=doctors), \
                mock.patch.object(access_log, "record") as rec:
            self.assertEqual(booking_decisions._assign_core(DESK, pending, 7, lambda: {"medico_id": "d1"})[0], 200)
            rec.assert_called_once_with(DESK, "edicion", "30111222", target_id="assign:7")
        assign.reset_mock()
        with mock.patch.object(catalog, "doctors", return_value=doctors), \
                mock.patch.object(access_log, "record", side_effect=access_log.AccessLogUnavailable()):
            self.assertEqual(booking_decisions._assign_core(DESK, pending, 7, lambda: {"medico_id": "d1"})[0], 503)
        assign.assert_not_called()


class TestDeskCancel(_Ops):
    def setUp(self):
        super().setUp()
        self.cancel = self.patch(store, "cancel", return_value=dict(self.row, estado="cancelada"))
        self.patch(booking_patient, "_after_cancel", side_effect=lambda sid, estado, turno_dt, updated, emr: updated)

    def test_refusals(self):
        self.assertEqual(booking_decisions._desk_cancel_core(DESK, {"estado": "rechazada"}, 7)[0], 409)
        self.assertEqual(booking_decisions._desk_cancel_core(DESK, {"estado": "aprobada", "turno_dt": PAST}, 7)[0], 409)
        self.cancel.return_value = None
        self.assertEqual(booking_decisions._desk_cancel_core(DESK, self.row, 7)[0], 409)

    def test_notice_failure_does_not_undo_the_cancel(self):
        from app.notifications import send
        self.patch(send, "notify_cancelled", side_effect=RuntimeError("smtp"))
        self.assertEqual(booking_decisions._desk_cancel_core(DESK, self.row, 7)[0], 200)
        self.assertEqual(self.cancel.call_args.kwargs["actor"], "mesa1")


class TestPatientCancel(_Ops):
    def test_refusals(self):
        self.assertEqual(booking_patient.cancel_request(None, 7)[0], 401)
        self.assertEqual(booking_patient.cancel_request("11111111", 7), (404, {"error": "no_encontrado"}))
        self.row = dict(self.row, estado="cancelada")
        self.assertEqual(booking_patient.cancel_request("27888999", 7), (400, {"error": "no_cancelable"}))
        self.row = dict(self.row, estado="aprobada", turno_dt=PAST)
        self.assertEqual(booking_patient.cancel_request("27888999", 7), (400, {"error": "turno_pasado"}))
        self.row = dict(self.row, turno_dt=FUTURE)
        self.patch(store, "cancel", return_value=None)
        self.assertEqual(booking_patient.cancel_request("27888999", 7), (400, {"error": "no_cancelable"}))

    def test_after_cancel_survives_every_tail(self):
        from app.modules.turnos import hsi_journal, waitlist
        self.patch(store, "holds_release_booking_request", side_effect=RuntimeError("db"))
        br._emr_sync_enabled.return_value = True
        with mock.patch.object(hsi_journal, "enqueue_cancel", side_effect=RuntimeError("db")), \
                mock.patch.object(waitlist, "opt_out", side_effect=RuntimeError("db")):
            out = booking_patient._after_cancel(7, "aprobada", FUTURE, {"id": 7, "hsi_appointment_id": "a1"}, True)
        self.assertEqual(out, {"id": 7, "hsi_appointment_id": "a1"})


class TestWaitlistToggle(_Ops):
    def test_refusals_and_opt_in(self):
        from app.modules.turnos import waitlist
        self.assertEqual(booking_patient._waitlist_toggle_core(None, 7, dict)[0], 401)
        self.assertEqual(booking_patient._waitlist_toggle_core("11111111", 7, dict)[0], 404)
        self.assertEqual(booking_patient._waitlist_toggle_core("27888999", 7, _broken_body)[0], 400)
        with mock.patch.object(waitlist, "opt_out") as opt_out:
            self.assertEqual(booking_patient._waitlist_toggle_core("27888999", 7, lambda: {"activo": False}),
                             (200, {"ok": True, "activo": False}))
        opt_out.assert_called_once_with(7)
        self.row = dict(self.row, turno_dt=PAST)
        self.assertEqual(booking_patient._waitlist_toggle_core("27888999", 7, lambda: {"activo": True}),
                         (400, {"error": "no_aplica"}))
        # a dependent's booking: the notice goes to the guardian's account, who may toggle it too
        self.row = dict(self.row, turno_dt=FUTURE, solicitante_dni="20111222")
        get_user = self.patch(store, "get_user", return_value={})
        self.assertEqual(booking_patient._waitlist_toggle_core("27888999", 7, lambda: {"activo": True}),
                         (400, {"error": "falta_email"}))
        get_user.assert_called_with("20111222")
        get_user.return_value = {"email": "tutor@example.com"}
        with mock.patch.object(waitlist, "opt_in") as opt_in:
            self.assertEqual(booking_patient._waitlist_toggle_core("20111222", 7, lambda: {"activo": 1}),
                             (200, {"ok": True, "activo": True}))
        opt_in.assert_called_once_with(7, "27888999", "tutor@example.com", "cardiologia", "hospital", FUTURE)


class TestReschedule(_Ops):
    """POST /api/requests/<id>/reprogramar: the patient moves their own booking to a new time."""

    BODY = {"dia": "2026-12-01", "hora": "10:00", "preferencia": " por la tarde "}

    def setUp(self):
        super().setUp()
        self.row = dict(self.row, medico_asignado="Dr. One", medico_asignado_id="d1")
        from app.modules.turnos import booking_horizon, slot_holds, hsi_journal, waitlist
        from app.notifications import send
        self.horizon = self.patch(booking_horizon, "check_slot", return_value=None)
        self.take = self.patch(slot_holds, "try_take", return_value=True)
        self.in_schedule = self.patch(booking_capacity, "_doctor_appointment_in_schedule", return_value=(True, None))
        self.busy = self.patch(booking_capacity, "_doctor_appointment_busy", return_value=False)
        self.release_all = self.patch(store, "holds_release_booking_request")
        self.link = self.patch(store, "hold_link_booking_request")
        self.release = self.patch(store, "hold_release")
        self.preference = self.patch(store, "reschedule_preference", return_value={"id": 7, "estado": "pendiente"})
        self.reassign = self.patch(store, "reassign_appointment", return_value={"id": 7, "estado": "aprobada"})
        self.notify = self.patch(send, "notify_approved")
        self.free = self.patch(waitlist, "release_slot")
        self.enqueue = self.patch(hsi_journal, "enqueue_reassign")
        self.replay = self.patch(hsi_journal, "replay")
        self.unlinked = self.patch(hsi_journal, "replay_unlinked_move")

    def go(self, body=None, dni="27888999"):
        return booking_patient._reschedule_core(None, dni, 7, lambda: dict(self.BODY, **(body or {})))

    def test_who_and_which_state(self):
        self.assertEqual(booking_patient._reschedule_core(None, None, 7, dict)[0], 401)
        self.assertEqual(self.go(dni="11111111"), (404, {"error": "no_encontrado"}))
        self.row = dict(self.row, estado="rechazada")
        self.assertEqual(self.go(), (400, {"error": "no_reprogramable"}))
        self.row = dict(self.row, estado="aprobada", turno_dt=PAST)
        self.assertEqual(self.go(), (400, {"error": "turno_pasado"}))

    def test_body_refusals(self):
        self.assertEqual(booking_patient._reschedule_core(None, "27888999", 7, _broken_body)[0], 400)
        self.assertEqual(self.go({"hora": "10"}), (400, {"error": "dia/hora inv\u00e1lidos"}))
        self.assertEqual(self.go({"dia": "2026-02-30"}), (400, {"error": "dia/hora inv\u00e1lidos"}))
        self.horizon.return_value = {"error": "fuera_de_horizonte"}
        self.assertEqual(self.go(), (409, {"error": "fuera_de_horizonte"}))

    def test_doctor_schedule_and_busy(self):
        self.in_schedule.return_value = (False, "fuera_de_horario")
        self.assertEqual(self.go(), (409, {"error": "fuera_de_horario"}))
        self.in_schedule.return_value = (True, None)
        self.busy.return_value = True
        self.assertEqual(self.go(), (409, {"error": "turno_medico_tomado"}))
        self.assertEqual(self.busy.call_args.kwargs, {"medico_id": "d1"})

    def test_hold_fails_closed(self):
        self.take.side_effect = RuntimeError("db hiccup")
        self.assertEqual(self.go(), (409, {"error": "turno_tomado"}))
        self.take.side_effect, self.take.return_value = None, False
        self.assertEqual(self.go(), (409, {"error": "turno_tomado"}))
        self.link.assert_not_called()

    def test_pending_moves_only_the_preference(self):
        self.row = dict(self.row, estado="pendiente")
        status, body = self.go()
        self.assertEqual(status, 200)
        self.preference.assert_called_once_with(7, "por la tarde")
        self.in_schedule.assert_not_called()
        self.reassign.assert_not_called()
        self.assertEqual(self.link.call_args.args[1:], (7, _dt(2026, 12, 1, 10)))

    def test_linked_approved_move_journals_and_survives_its_tails(self):
        self.row = dict(self.row, hsi_appointment_id="a1")
        br._emr_sync_enabled.return_value = True
        self.replay.side_effect = RuntimeError("emr down")
        self.notify.side_effect = RuntimeError("smtp")
        self.free.side_effect = RuntimeError("db")
        self.assertEqual(self.go()[0], 200)
        self.assertEqual(self.reassign.call_args.args[2], {"estado": "pendiente"})
        self.enqueue.assert_called_once()
        self.free.assert_called_once_with("cardiologia", "hospital", FUTURE, handler=None)

    def test_unlinked_approved_move_lands_through_the_journal(self):
        br._emr_sync_enabled.return_value = True
        self.unlinked.side_effect = RuntimeError("emr down")
        self.assertEqual(self.go()[0], 200)
        self.unlinked.assert_called_once()
        self.enqueue.assert_not_called()

    def test_slot_conflict_releases_the_new_hold(self):
        self.reassign.side_effect = store.SlotConflict()
        self.assertEqual(self.go(), (409, {"error": "turno_tomado"}))
        self.row = dict(self.row, hsi_appointment_id="a1")
        self.assertEqual(self.go(), (409, {"error": "turno_tomado"}))
        self.assertEqual(self.release.call_count, 2)
        self.notify.assert_not_called()


class TestAdminQueue(_Ops):
    def setUp(self):
        super().setUp()
        self.rows = [{"id": 1, "dni": "1", "estado": "pendiente", "centro_slug": "hospital"},
                     {"id": 2, "dni": "2", "estado": "aprobada", "centro_slug": "caps-1"}]
        self.list = self.patch(store, "list_admin", side_effect=lambda *a, **k: [dict(r) for r in self.rows])
        self.patch(store, "count_admin", return_value=2)
        self.patch(br, "_admin_row", side_effect=dict)
        self.patch(br.admin_auth, "centros_permitidos", return_value=None)
        self.patch(store, "attendance_summary_by_dnis", side_effect=RuntimeError("db"))
        from app.modules.turnos import patient_ranking
        from app.core import access_log, hsi_extension
        self.patch(patient_ranking, "by_dnis", return_value={})
        self.patch(access_log, "record_many")
        self.patch(hsi_extension, "store_url", return_value=None)
        self.patch(hsi_extension, "install", return_value=None)
        self.patch(booking_queue, "_approved_by_doctor_art", return_value={})
        self.patch(booking_queue, "_doctor_pool_by_service", return_value={})
        self.patch(booking_queue, "_requested_doctor_name", return_value=None)
        self.patch(store, "hold_of_booking_request", side_effect=RuntimeError("db"))
        self.suggest = self.patch(booking_queue, "_suggested_doctor", side_effect=RuntimeError("catalog"))

    def test_auxiliary_failures_never_take_the_queue_down(self):
        status, body = booking_queue._admin_list_core(DESK, {"limit": ["many"], "offset": ["x"]})
        self.assertEqual(status, 200)
        self.assertEqual(self.list.call_args.kwargs, {"limit": None, "offset": 0})
        first = body["solicitudes"][0]
        self.assertEqual(first["asistencia_resumen"]["visitas"], 0)
        self.assertIsNone(first["medico_sugerido"])
        self.assertIsNone(first["turno_pedido"])
        self.assertEqual(body["total"], 2)

    def test_center_scope_filters_rows_and_total(self):
        br.admin_auth.centros_permitidos.return_value = {"caps-1"}
        status, body = booking_queue._admin_list_core(DESK, {"limit": ["10"], "offset": ["20"]})
        self.assertEqual([r["id"] for r in body["solicitudes"]], [2])
        self.assertEqual(body["total"], 1)
        self.assertEqual(self.list.call_args.kwargs, {"limit": 10, "offset": 20})


class TestApprove(_Ops):
    """POST /api/admin/requests/<id>/approve: what the desk is refused, and how a lost race reads."""

    def setUp(self):
        super().setUp()
        self.row = {"id": 7, "estado": "pendiente", "servicio_id": "cardiologia", "centro_slug": "hospital"}
        self.patch(br, "_services", return_value={"cardiologia": "Cardiologia"})
        self.patch(br, "_service_full", return_value={"id": "cardiologia"})
        self.patch(br, "_doctor_serves_service", return_value=True)
        self.patch(booking_capacity, "_doctor_appointment_busy", return_value=False)
        self.patch(booking_capacity, "_doctor_appointment_in_schedule", return_value=(True, None))
        self.persist = self.patch(booking_decisions, "_persist_approval", return_value={"id": 7})
        from app.modules.turnos import doctor_appointments
        self.resolve = self.patch(doctor_appointments, "resolve_doctor_id", return_value="d1")

    def approve(self, **body):
        return booking_decisions._approve_core(DESK, self.row, 7, dict({"turno_dt": "2026-12-01T10:00:00-03:00"}, **body))

    def test_bad_input_is_400(self):
        self.assertEqual(booking_decisions._approve_core(DESK, self.row, 7, {"turno_dt": " "})[0], 400)
        self.assertEqual(self.approve(turno_dt="1/12/2026 10:00")[0], 400)
        self.assertEqual(self.approve(servicio_id="no-such")[0], 400)
        self.assertEqual(self.approve(sobreturno="si"), (400, {"error": "sobreturno_invalido"}))
        self.persist.assert_not_called()

    def test_unresolvable_doctor_name_still_approves(self):
        self.resolve.side_effect = RuntimeError("catalog")
        self.assertEqual(self.approve(medico="Dr. Nadie")[0], 200)
        self.assertIsNone(self.persist.call_args.kwargs["medico_id"])
        self.assertEqual(self.persist.call_args.kwargs["resuelta_por"], "mesa1")

    def test_lost_races_are_409(self):
        for exc, want in ((store.AlreadyProcessed(), None), (store.SlotConflict(), "turno_medico_tomado"),
                          (store.OverbookingCapacityReached(), "sobreturno_cupo_agotado"),
                          (booking_decisions.OverbookingPolicyDenied(), "sobreturno_no_configurado")):
            self.persist.side_effect = exc
            status, body = self.approve(medico="Dr. One")
            self.assertEqual(status, 409, exc)
            if want:
                self.assertEqual(body, {"error": want})


# ---- creation: the referral analysed before the request exists ------------------------------------------------

PDF_BYTES = b"%PDF-1.4 order"


class TestAnalyzeReferralBytes(_ReferralsDir):
    def setUp(self):
        super().setUp()
        from app.ai import ai_costs, ai_health, orden_analysis_cache, prompts
        # the cache contract reads the prompt text: the code's own prompts, never the overrides of a shared database
        mock.patch.object(prompts, "_overrides", return_value={}).start()
        self.health = mock.patch.object(ai_health, "available", return_value=True).start()
        self.record = mock.patch.object(ai_costs, "record").start()
        self.cache_key = mock.patch.object(orden_analysis_cache, "key_for", side_effect=RuntimeError("cache")).start()
        self.cache_put = mock.patch.object(orden_analysis_cache, "put", side_effect=RuntimeError("cache")).start()
        self.read = mock.patch.object(booking_referrals.ai_referral, "analyze_referral_3pass",
                                      return_value={"verdict": "dudosa", "campos": {}}).start()
        self.addCleanup(mock.patch.stopall)

    def test_cache_and_preparation_failures_are_invisible(self):
        from app.modules.turnos import preparation
        with mock.patch.object(preparation, "match_studies", side_effect=RuntimeError("catalog")):
            token, ai_json, available = booking_referrals.analyze_referral_bytes(PDF_BYTES, "application/pdf", "o.pdf",
                                                                  especialidad="Cardiologia", ip="10.0.0.1")
        self.assertTrue(available)
        self.assertEqual((ai_json["preparacion"], ai_json["preparacion_no_reconocidos"]), ([], []))
        self.assertTrue(self.record.call_args.kwargs["recognized"])
        self.assertEqual(self.record.call_args.kwargs["window_key"], None)
        self.assertEqual(booking_referrals._load_pending(token)["especialidad"], "Cardiologia")
        self.assertTrue(os.path.exists(os.path.join(self.dir, "pending", f"{token}.pdf")))

    def test_metadata_that_cannot_be_written_still_returns_the_reading(self):
        with mock.patch.object(br.json, "dump", side_effect=OSError("disk full")):
            token, ai_json, available = booking_referrals.analyze_referral_bytes(PDF_BYTES, "application/pdf", "o.pdf")
        self.assertTrue(available)
        self.assertIsNone(booking_referrals._load_pending(token))

    def test_ai_down_saves_the_file_as_pending_even_without_metadata(self):
        self.health.return_value = False
        with mock.patch.object(br.json, "dump", side_effect=OSError("disk full")):
            token, ai_json, available = booking_referrals.analyze_referral_bytes(PDF_BYTES, "application/pdf", "o.pdf")
        self.assertFalse(available)
        self.read.assert_not_called()
        self.record.assert_not_called()
        self.assertTrue(os.path.exists(os.path.join(self.dir, "pending", f"{token}.pdf")))


class TestAnalyzeReferralRoute(unittest.TestCase):
    """POST /api/analyze-order, after the rate gates: what a malformed upload gets."""

    def setUp(self):
        from app.ai import ai_costs, ai_health, ai_cooldown
        mock.patch.object(ai_health, "available", return_value=True).start()
        mock.patch.object(ai_costs, "blocked", return_value=False).start()
        mock.patch.object(ai_cooldown, "attempt", return_value=(True, 0)).start()
        mock.patch.object(booking_referrals.abuse_guard, "identity", return_value="ip|id").start()
        mock.patch.object(booking_referrals.abuse_guard, "allowed", return_value=True).start()
        mock.patch.object(booking_referrals.abuse_guard, "ceiling_ok", return_value=True).start()
        self.turnstile = mock.patch.object(booking_referrals.abuse_guard, "turnstile_ok", return_value=True).start()
        mock.patch.object(br, "_service_name", return_value="Cardiologia").start()
        self.analyze = mock.patch.object(booking_referrals, "analyze_referral_bytes",
                                         return_value=("tok", {"verdict": "dudosa"}, False)).start()
        self.addCleanup(mock.patch.stopall)

    def post(self, body):
        return booking_referrals._analyze_referral_core({}, "127.0.0.1", "127.0.0.1", lambda: body)

    def test_refusals(self):
        self.assertEqual(booking_referrals._analyze_referral_core({}, "h", "ip", _broken_body)[0], 400)
        self.turnstile.return_value = False
        self.assertEqual(self.post({"orden": {"data_b64": "JVBERg=="}}), (403, {"error": "turnstile"}))
        self.turnstile.return_value = True
        self.assertEqual(self.post({"orden": {}})[0], 400)
        self.assertEqual(self.post({"orden": {"data_b64": "%%%not base64"}})[0], 400)
        with mock.patch.object(br, "MAX_REFERRAL_BYTES", 3):
            self.assertEqual(self.post({"orden": {"data_b64": "JVBERi0xLjQ="}})[0], 413)
        self.analyze.side_effect = OSError("disk")
        self.assertEqual(self.post({"orden": {"data_b64": "JVBERi0xLjQ="}})[0], 500)

    def test_ai_down_reading_says_so(self):
        status, body = self.post({"servicio_id": "cardiologia", "orden": {"data_b64": "JVBERi0xLjQ="}})
        self.assertEqual((status, body["token"], body["ia_disponible"]), (200, "tok", False))
        self.assertEqual(self.analyze.call_args.args[3:5], ("cardiologia", "Cardiologia"))


if __name__ == "__main__":
    unittest.main()
