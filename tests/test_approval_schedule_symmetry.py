"""test_approval_schedule_symmetry.py — the approval validation must resolve
the doctor's agendas the SAME way the offer surfaces do (schedule_policy, by
the service's center), never by the request row's stored centro_slug.

Bug seen live (the reference-deployment counterpart, 19/10): in Solicitudes the operator picked a time from
the clinician's real calendar (Thursday 09:00) and the approval came back
«La hora elegida no pertenece a la agenda de ese médico». The panel's own
«Otro horario…» select had offered that very slot — `/api/service-slots` had
resolved the doctor's bands by the SERVICE's center, while
`_doctor_appointment_in_schedule` resolved them by the row's centro_slug,
which goes stale the moment a request is derived to another service or the
service moves centers. Both ends now go through schedule_policy.

Pure tests: no database — the store is mocked at its boundary
(`booking_requests_store`), HSI off via TURNOS_SKIP_HSI, notifications patched.

Correr:  python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_approval_schedule_symmetry.py
"""

import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1")

from app.modules.turnos import booking_capacity, booking_requests_store as store_mod  # noqa: E402
from app.modules.turnos import booking_decisions  # noqa: E402
from app.patient_web import catalog, site_availability, site_base, site_catalog

ART = datetime.timezone(datetime.timedelta(hours=-3))
# Jueves 26/11/2026 09:00, hora de pared de Buenos Aires — el caso del called.
JUEVES = datetime.datetime(2026, 11, 26, 9, 0, tzinfo=ART)

_CENTROS = {"centros": [
    {"slug": "hospital", "name": "Hospital", "address": "C1"},
    {"slug": "caps", "name": "CAPS", "address": "C2"},
]}
_SERVICIOS = {"servicios": [
    {"id": "trauma", "nombre": "Traumatología", "centro": "caps",
     "cluster": "trauma", "modo": "solicitud"},
    {"id": "clinica", "nombre": "Clínica médica", "centro": "caps",
     "cluster": "clinica", "modo": "solicitud"},
    # servicio con agenda propia: el dueño del horario es el SERVICIO
    {"id": "eco", "nombre": "Ecografía", "centro": "caps",
     "cluster": "diagnostico", "modo": "solicitud",
     "agenda": {"dias": [5], "desde": "09:00", "hasta": "12:00", "cada_min": 20}},
    # servicio legado sin centro ni agenda propia: el único dato de centro es
    # el centro_slug de la fila
    {"id": "legado", "nombre": "Consultorio legado",
     "cluster": "trauma", "modo": "solicitud"},
    # servicio sin agenda propia, cerrado desde el panel el jueves del caso
    # (Ergometría, 23/10): el cierre vale sobre las bandas de sus médicos
    {"id": "ergo", "nombre": "Ergometría", "centro": "caps",
     "cluster": "trauma", "modo": "solicitud",
     "agenda": {"franjas": [], "excepciones": [
         {"tipo": "cerrado", "desde_fecha": "2026-11-20", "hasta_fecha": "2026-11-30"}]}},
]}
_THU = {"dias": [4], "desde": "09:00", "hasta": "13:00", "cada_min": 20,
        "servicio": "trauma"}
_TUE_CLINICA = {"dias": [2], "desde": "14:00", "hasta": "17:00", "cada_min": 20,
                "servicio": "clinica"}
_MON_HOSPITAL = {"dias": [1], "desde": "09:00", "hasta": "12:00", "cada_min": 20}


def _doctor(did, nombre, centros):
    return {"id": did, "nombre": nombre, "especialidad": "Traumatología",
            "cluster": "trauma", "centro": "caps", "centros": centros}


_MEDICOS = {"medicos": [
    # la doctora del caso: jueves 09-13 para trauma en el CAPS, martes por la
    # tarde para clínica en el mismo CAPS, y lunes SIN tag en el Hospital
    # (otro centro: nunca es agenda de este servicio).
    _doctor("jueves-doc", "Julia Jueves", [
        {"centro": "caps", "franjas": [_THU, _TUE_CLINICA], "excepciones": []},
        {"centro": "hospital", "franjas": [_MON_HOSPITAL], "excepciones": []},
    ]),
    # jueves cerrados por excepción: el día existe, la agenda lo cierra
    _doctor("cerrado-doc", "Carlos Cerrado", [
        {"centro": "caps", "franjas": [dict(_THU)],
         "excepciones": [{"tipo": "cerrado", "fecha": "2026-11-26"}]},
    ]),
    # banda histórica propia bajo un servicio que ya tiene agenda propia
    _doctor("hist-doc", "Elena Historia", [
        {"centro": "caps", "franjas": [dict(_THU, servicio="eco")], "excepciones": []},
    ]),
    # su jueves es de Ergometría, un servicio sin agenda propia
    _doctor("ergo-doc", "Ramiro Ergo", [
        {"centro": "caps", "franjas": [dict(_THU, servicio="ergo")], "excepciones": []},
    ]),
]}


class ApprovalScheduleSymmetryTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="approval-sym-")
        webdir = os.path.join(self._tmp, "webdata")
        os.makedirs(webdir)
        for name, doc in (("centros-salud.json", _CENTROS),
                          ("servicios.json", _SERVICIOS),
                          ("medicos.json", _MEDICOS)):
            with open(os.path.join(webdir, name), "w", encoding="utf-8") as f:
                json.dump(doc, f)
        # Registered BEFORE the environment: cleanups run last-in first-out, so
        # the environment is put back first and the caches are dropped (and the
        # webdata dir deleted) after it. A deleted webdata dir would otherwise
        # leak into every later test of the run.
        self.addCleanup(self._restore)
        _env.set_env(self, TURNOS_WEBDATA_DIR=webdir, TURNOS_SKIP_HSI="1")
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        site_base._CACHE.clear()
        # Site holidays live in the DB overlay; the fixture's Monday 23/11/2026
        # is a national holiday wherever the lane's DB carries the list, which
        # turned «fuera de agenda» into «cerrado». These tests are about bands.
        self._holidays = patch("app.modules.turnos.site_holidays.by_date", return_value={})
        self._holidays.start()
        self.addCleanup(self._holidays.stop)

    def _restore(self):
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        site_base._CACHE.clear()
        shutil.rmtree(self._tmp, ignore_errors=True)

    # fila pendiente con el centro VIEJO (el caso real: derivada o movida)
    def _row(self, **kw):
        return dict({"id": 69, "servicio_id": "trauma", "centro_slug": "hospital",
                     "estado": "pendiente", "dni": "27888999", "nombre": "Ana",
                     "apellido": "López"}, **kw)

    # ---- la regresión del 19/10 --------------------------------------------

    def test_offered_slots_validate_under_a_stale_center_slug(self):
        svc = site_catalog._service_by_id("trauma")
        out = site_availability._appointments_for_doctor(svc, "jueves-doc")
        slots = out.get("medicos", [{}])[0].get("turnos", [])
        self.assertTrue(slots, "fixture: the doctor must offer slots")
        for t in slots:
            dt = datetime.datetime.fromisoformat(t["dia"] + "T" + t["hora"]).replace(tzinfo=ART)
            _, error = booking_capacity._doctor_appointment_in_schedule(
                "Julia Jueves", dt, self._row())
            self.assertIsNone(error, f"offered slot {t} rejected: {error}")
        # el caso literal del called: jueves 26/11 09:00 de su agenda real
        band, error = booking_capacity._doctor_appointment_in_schedule(
            "Julia Jueves", JUEVES, self._row())
        self.assertIsNone(error)
        self.assertEqual(band["desde"], "09:00")

    def test_per_service_band_and_other_center_band_do_not_leak_in(self):
        # martes 14:00 es SU agenda, pero de clínica: no vale para trauma
        martes = datetime.datetime(2026, 11, 24, 14, 0, tzinfo=ART)
        _, error = booking_capacity._doctor_appointment_in_schedule(
            "Julia Jueves", martes, self._row())
        self.assertEqual(error, "turno_medico_fuera_de_agenda")
        # lunes 10:00 es su agenda del Hospital (otro centro, sin tag)
        lunes = datetime.datetime(2026, 11, 23, 10, 0, tzinfo=ART)
        _, error = booking_capacity._doctor_appointment_in_schedule(
            "Julia Jueves", lunes, self._row())
        self.assertEqual(error, "turno_medico_fuera_de_agenda")

    def test_service_owned_schedule_still_owns_the_clock(self):
        # el servicio eco tiene agenda propia (viernes): la banda histórica del
        # jueves de Elena NO autoriza turnos — no se debilita la validación
        _, error = booking_capacity._doctor_appointment_in_schedule(
            "Elena Historia", JUEVES, self._row(servicio_id="eco"))
        self.assertEqual(error, "turno_medico_fuera_de_agenda")
        viernes = datetime.datetime(2026, 11, 27, 9, 0, tzinfo=ART)
        band, error = booking_capacity._doctor_appointment_in_schedule(
            "Elena Historia", viernes, self._row(servicio_id="eco"))
        self.assertIsNone(error)

    def test_legacy_service_without_center_keeps_the_row_center(self):
        # sin centro en el servicio, la agenda del médico sale del centro de la
        # fila, como siempre (el lunes del Hospital vale, el jueves del CAPS no)
        lunes = datetime.datetime(2026, 11, 23, 10, 0, tzinfo=ART)
        _, error = booking_capacity._doctor_appointment_in_schedule(
            "Julia Jueves", lunes, self._row(servicio_id="legado"))
        self.assertIsNone(error)
        _, error = booking_capacity._doctor_appointment_in_schedule(
            "Julia Jueves", JUEVES, self._row(servicio_id="legado"))
        self.assertEqual(error, "turno_medico_fuera_de_agenda")

    def test_service_closure_closes_the_doctor_band_at_approval(self):
        # la grilla no ofrece el día que el servicio cerró: la aprobación tampoco
        row = self._row(servicio_id="ergo", centro_slug="caps")
        _, error = booking_capacity._doctor_appointment_in_schedule("Ramiro Ergo", JUEVES, row)
        self.assertEqual(error, "turno_medico_horario_cerrado")
        # fuera del cierre su jueves sigue valiendo
        despues = datetime.datetime(2026, 12, 3, 9, 0, tzinfo=ART)
        _, error = booking_capacity._doctor_appointment_in_schedule("Ramiro Ergo", despues, row)
        self.assertIsNone(error)

    def test_closed_day_is_rejected_as_closed(self):
        _, error = booking_capacity._doctor_appointment_in_schedule(
            "Carlos Cerrado", JUEVES, self._row())
        self.assertEqual(error, "turno_medico_horario_cerrado")

    # ---- flujo completo de aprobación (store en el borde, mokeado) --------

    def _approve(self, body, approved_in_range=None, approve_impl=None):
        calls = []

        def fake_approve(sid, turno_dt, hsi_result, **kw):
            calls.append((sid, turno_dt, kw))
            if approve_impl:
                return approve_impl(sid, turno_dt, hsi_result, **kw)
            return dict(self._row(), estado="aprobada", turno_dt=turno_dt,
                        medico_asignado=kw.get("medico_asignado"),
                        medico_asignado_id=kw.get("medico_asignado_id"))

        identity = {"usuario": "mesa", "rol": "admin"}
        with patch.object(store_mod, "approved_in_range",
                          return_value=approved_in_range or []), \
             patch.object(store_mod, "get_booking_request",
                          return_value=self._row()), \
             patch.object(store_mod, "approve", side_effect=fake_approve), \
             patch.object(store_mod, "holds_release_booking_request",
                          return_value=None), \
             patch("app.notifications.send.notify_approved"):
            status, payload = booking_decisions._approve_core(
                identity, self._row(), 69, body)
        return status, payload, calls

    def test_approval_persists_the_wire_instant_in_art(self):
        # lo que manda el panel: hora de pared con el offset del navegador
        status, payload, calls = self._approve(
            {"turno_dt": "2026-11-26T09:00:00-03:00", "medico": "Julia Jueves"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(len(calls), 1)
        sid, turno_dt, kw = calls[0]
        self.assertEqual(turno_dt, JUEVES)
        self.assertEqual(kw["medico_asignado"], "Julia Jueves")
        self.assertEqual(kw["medico_asignado_id"], "jueves-doc")

    def test_utc_wire_shape_of_the_same_instant_also_approves(self):
        status, payload, _ = self._approve(
            {"turno_dt": "2026-11-26T12:00:00+00:00", "medico": "Julia Jueves"})
        self.assertEqual(status, 200, payload)

    def test_taken_minute_is_rejected(self):
        busy = [{"id": 70, "medico_asignado": "Julia Jueves", "centro_slug": "caps",
                 "estado": "aprobada", "turno_dt": JUEVES}]
        status, payload, _ = self._approve(
            {"turno_dt": "2026-11-26T09:00:00-03:00", "medico": "Julia Jueves"},
            approved_in_range=busy)
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "turno_medico_tomado")

    def test_wrong_doctor_time_is_rejected(self):
        status, payload, _ = self._approve(
            {"turno_dt": "2026-11-24T14:00:00-03:00", "medico": "Julia Jueves"})
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "turno_medico_fuera_de_agenda")

    def test_double_approval_is_idempotent_on_the_cas(self):
        def raise_already(sid, turno_dt, hsi_result, **kw):
            raise store_mod.AlreadyProcessed()

        status, payload, calls = self._approve(
            {"turno_dt": "2026-11-26T09:00:00-03:00", "medico": "Julia Jueves"},
            approve_impl=raise_already)
        self.assertEqual(status, 409)
        self.assertIn("ya", str(payload["error"]).lower())

        def raise_conflict(sid, turno_dt, hsi_result, **kw):
            raise store_mod.SlotConflict()

        status, payload, _ = self._approve(
            {"turno_dt": "2026-11-26T09:00:00-03:00", "medico": "Julia Jueves"},
            approve_impl=raise_conflict)
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"], "turno_medico_tomado")


if __name__ == "__main__":
    unittest.main()
