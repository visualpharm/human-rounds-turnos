"""Slice 4 regressions reproduced without requiring a database."""
from contextlib import nullcontext
from datetime import datetime
import unittest
from unittest.mock import patch

from app.admin_panel import admin_content
from app.modules.turnos import booking_horizon, booking_requests, schedule_policy
from app.modules.turnos import booking_capacity
from app.modules.turnos import booking_patient
from app.modules.turnos import booking_queue
from app.patient_web import site_availability, site_base, site_doctors
from app.core import db_catalog
from app.core import db_operators

SERVICE = {"id": "study", "nombre": "Study", "centro": "hospital", "cluster": "c", "modo": "solicitud",
           "agenda": {"dias": list(range(7)), "desde": "09:00", "hasta": "12:00", "cada_min": 20}}
DOCTOR = {"id": "doctor", "nombre": "Doctor Example", "especialidad": "Study", "centro": "hospital", "cluster": "c",
          "centros": [{"centro": "hospital", "franjas": [dict(SERVICE["agenda"], cupos={"sobreturnos": 2})]}]}


class Slice4FixRegressions(unittest.TestCase):
    def test_flat_service_schedule_accepts_its_boundary(self):
        with patch("app.patient_web.catalog.doctors", return_value=[DOCTOR]), \
             patch.object(booking_requests, "_service_full", return_value=SERVICE):
            band, error = booking_capacity._doctor_appointment_in_schedule(
                DOCTOR["nombre"], datetime(2026, 10, 14, 9, 20),
                {"servicio_id": "study", "centro_slug": "hospital"})
        self.assertIsNone(error)
        self.assertEqual(band["desde"], "09:00")

    def test_staff_overbooking_preserves_doctor_capacity_on_service_boundary(self):
        with patch("app.patient_web.catalog.doctors", return_value=[DOCTOR]), \
             patch.object(booking_requests, "_service_full", return_value=SERVICE):
            remaining, error = booking_capacity.overbooking_capacity(
                DOCTOR["nombre"], datetime(2026, 10, 14, 9, 20),
                {"servicio_id": "study", "centro_slug": "hospital"}, usados=1)
        self.assertEqual((remaining, error), (2, None))

    def test_staff_capacity_cannot_offer_a_time_outside_service_schedule(self):
        doctor = dict(DOCTOR, centros=[{"centro": "hospital", "franjas": [
            dict(SERVICE["agenda"], desde="08:00", cupos={"sobreturnos": 2})]}])
        with patch("app.patient_web.catalog.doctors", return_value=[doctor]), \
             patch.object(booking_requests, "_service_full", return_value=SERVICE):
            self.assertEqual(booking_capacity.overbooking_capacity(
                DOCTOR["nombre"], datetime(2026, 10, 14, 8, 20),
                {"servicio_id": "study", "centro_slug": "hospital"}, usados=1),
                (0, "sobreturno_fuera_de_agenda"))

    def test_service_staff_capacity_overrides_legacy_doctor_capacity(self):
        service = dict(SERVICE, agenda=dict(SERVICE["agenda"], cupos={"sobreturnos": 0}))
        with patch("app.patient_web.catalog.doctors", return_value=[DOCTOR]), \
             patch.object(booking_requests, "_service_full", return_value=service):
            self.assertEqual(booking_capacity.overbooking_capacity(
                DOCTOR["nombre"], datetime(2026, 10, 14, 9, 20),
                {"servicio_id": "study", "centro_slug": "hospital"}, usados=1),
                (0, "sobreturno_no_configurado"))

    def test_staff_percentage_budget_keeps_the_original_doctor_band(self):
        doctor = dict(DOCTOR, centros=[{"centro": "hospital", "franjas": [
            dict(SERVICE["agenda"], hasta="10:00", cupos={"overbooking_percent": 50})]}])
        with patch("app.patient_web.catalog.doctors", return_value=[doctor]), \
             patch.object(booking_requests, "_service_full", return_value=SERVICE), \
             patch.object(booking_requests.store, "overbooking_count", return_value=0) as count:
            remaining, error = booking_capacity.overbooking_capacity(
                DOCTOR["nombre"], datetime(2026, 10, 14, 9, 20),
                {"servicio_id": "study", "centro_slug": "hospital"}, usados=1)
        self.assertEqual((remaining, error), (1, None))
        self.assertEqual(count.call_args.args[1].strftime("%H:%M"), "10:00")

    def test_reschedule_uses_existing_doctor_horizon_even_with_overlapping_slots(self):
        row = {"id": 3, "dni": "36555444", "estado": "aprobada", "servicio_id": "study",
               "medico_asignado": "Doctor Example", "medico_asignado_id": "doctor",
               "turno_dt": datetime(2099, 1, 1, tzinfo=booking_requests._ART)}
        service = dict(SERVICE); service.pop("agenda")
        doctor = dict(DOCTOR, dias_anticipacion=1)
        with patch.object(booking_requests.store, "get_booking_request", return_value=row), \
             patch("app.modules.turnos.booking_limits._service_doc", return_value=service), \
             patch("app.patient_web.catalog.doctor_doc", side_effect=lambda id_: doctor if id_ == "doctor" else None), \
             patch.object(booking_requests, "_resolve_slot_doctor", return_value="other"), \
             patch.object(booking_horizon, "_local_now", return_value=datetime(2026, 10, 13, 8)), \
             patch("app.modules.turnos.slot_holds.try_take") as take:
            status, result = booking_patient._reschedule_core(None, row["dni"], 3,
                lambda: {"dia": "2026-10-14", "hora": "09:20"})
        self.assertEqual(status, 409)
        self.assertEqual(result["error"], "turno_fuera_de_anticipacion")
        take.assert_not_called()

    def test_creation_returns_item_with_server_generated_id(self):
        doc = dict(SERVICE)
        with patch.object(db_catalog, "catalog_schedule_lock", return_value=nullcontext("transaction")), \
             patch.object(admin_content.catalog, "invalidate"), \
             patch.object(admin_content.catalog, "validate", return_value=(doc, None)), \
             patch.object(admin_content.catalog, "admin_list", return_value=[]), \
             patch.object(db_catalog, "catalog_upsert"), \
             patch.object(admin_content, "_item_out", side_effect=lambda tipo, id_: doc if id_ == "study" else None):
            result = admin_content.create_item("servicios", {"nombre": "Study"})
        # The saved item also carries the editor's display projection (slice 6:
        # the service form needs `_schedule_doctor_count` without a re-fetch).
        item = dict(result["item"])
        self.assertEqual(item.pop("_schedule_doctor_count"), 0)
        self.assertEqual(item.pop("_medicos"), [])
        self.assertEqual(item, doc)

    def test_admin_metadata_save_preserves_unchanged_legacy_doctor_schedule(self):
        with patch.object(admin_content.catalog, "admin_list", side_effect=lambda tipo: [DOCTOR] if tipo == "medicos" else [SERVICE]), \
             patch.object(admin_content.catalog, "validate", side_effect=lambda tipo, doc, **kw: (dict(doc), None)), \
             patch.object(db_catalog, "catalog_schedule_lock", return_value=nullcontext("transaction")), \
             patch.object(db_catalog, "catalog_get", return_value=None), \
             patch.object(db_catalog, "catalog_upsert") as write, \
             patch.object(admin_content.catalog, "invalidate"), \
             patch.object(admin_content.catalog, "services", return_value=[SERVICE]):
            result = admin_content.edit_item("medicos", "doctor", dict(DOCTOR, nota="Updated note"), {"rol": "admin"})
        self.assertTrue(result["ok"])
        self.assertEqual(write.call_args.args[2]["centros"], DOCTOR["centros"])

    def test_form_without_schedule_edit_never_trips_the_service_ownership_gate(self):
        # Staff feedback, 2026-10-14: «No podemos editar ningún profesional».
        # The editor rebuilds `centros` from its own state (drops empty
        # franjas and unknown keys), so a legacy schedule compared as
        # "changed" and every save of a service-managed doctor answered 409.
        mangled = [{"centro": "hospital", "franjas": [{"dias": [1], "desde": "09:00", "hasta": "12:00"}],
                    "excepciones": []}]
        body = dict(DOCTOR, nota="Updated note", centros=mangled, _agenda_sin_cambios=True)
        with patch.object(admin_content.catalog, "admin_list", side_effect=lambda tipo: [DOCTOR] if tipo == "medicos" else [SERVICE]), \
             patch.object(admin_content.catalog, "validate", side_effect=lambda tipo, doc, **kw: (dict(doc), None)), \
             patch.object(db_catalog, "catalog_schedule_lock", return_value=nullcontext("transaction")), \
             patch.object(db_catalog, "catalog_get", return_value=None), \
             patch.object(db_catalog, "catalog_upsert") as write, \
             patch.object(admin_content.catalog, "invalidate"), \
             patch.object(admin_content.catalog, "services", return_value=[SERVICE]):
            result = admin_content.edit_item("medicos", "doctor", body, {"rol": "admin"})
        self.assertTrue(result["ok"])
        saved = write.call_args.args[2]
        self.assertEqual(saved["centros"], DOCTOR["centros"], "stored schedule untouched")
        self.assertNotIn("_agenda_sin_cambios", saved)

    def test_doctor_self_service_save_under_service_schedule_keeps_schedule_and_saves_data(self):
        mangled = [{"centro": "hospital", "franjas": [{"dias": [1], "desde": "09:00", "hasta": "12:00"}]}]
        body = dict(nombre_pila="Ana", apellido="Example", email="ana@example.org",
                    centros=mangled, _agenda_sin_cambios=True)
        with patch.object(admin_content.catalog, "admin_list", side_effect=lambda tipo: [DOCTOR] if tipo == "medicos" else [SERVICE]), \
             patch.object(admin_content.catalog, "validate", side_effect=lambda tipo, doc, **kw: (dict(doc), None)), \
             patch.object(db_catalog, "catalog_schedule_lock", return_value=nullcontext("transaction")), \
             patch.object(db_catalog, "catalog_get", return_value=None), \
             patch.object(db_catalog, "catalog_upsert") as write, \
             patch.object(db_operators, "operator_doctor_id", return_value="doctor"), \
             patch.object(admin_content.catalog, "invalidate"), \
             patch.object(admin_content.catalog, "services", return_value=[SERVICE]), \
             patch("app.modules.turnos.doctor_appointments.compatible_services_data", return_value={"items": []}), \
             patch("app.admin_panel.admin_users.doctor_email_changed") as account_sync:
            result = admin_content.edit_item("medicos", "doctor", body, {"rol": "medico", "usuario": "doc"})
        self.assertTrue(result["ok"])
        saved = write.call_args.args[2]
        self.assertEqual(saved["email"], "ana@example.org")
        account_sync.assert_called_once_with("doctor", "ana@example.org")
        self.assertEqual(saved["centros"], DOCTOR["centros"])

    def test_schedule_edit_under_service_ownership_is_still_refused(self):
        changed = [{"centro": "hospital", "franjas": [{"dias": [2], "desde": "10:00", "hasta": "11:00"}]}]
        with patch.object(admin_content.catalog, "admin_list", side_effect=lambda tipo: [DOCTOR] if tipo == "medicos" else [SERVICE]), \
             patch.object(admin_content.catalog, "validate", side_effect=lambda tipo, doc, **kw: (dict(doc), None)), \
             patch.object(db_catalog, "catalog_schedule_lock", return_value=nullcontext("transaction")), \
             patch.object(db_catalog, "catalog_get", return_value=None), \
             patch.object(db_catalog, "catalog_upsert") as write, \
             patch.object(admin_content.catalog, "invalidate"), \
             patch.object(admin_content.catalog, "services", return_value=[SERVICE]):
            with self.assertRaises(admin_content.ContentError) as caught:
                admin_content.edit_item("medicos", "doctor", dict(DOCTOR, centros=changed), {"rol": "admin"})
        self.assertEqual(caught.exception.status, 409)
        write.assert_not_called()

    def test_unrelated_service_does_not_hide_doctors_own_schedule(self):
        doctor = dict(DOCTOR, centro="other", centros=[], agenda=SERVICE["agenda"])
        with patch.object(site_doctors, "_doctor_service", return_value=SERVICE), \
             patch.object(site_base, "_now_art", return_value=datetime(2026, 10, 13, 8)):
            self.assertEqual(site_availability._doctor_next(doctor)["kind"], "turno")

    def test_assigned_doctor_offers_only_the_service_owned_schedule(self):
        doctor = dict(DOCTOR, centros=[], agenda=dict(SERVICE["agenda"], desde="15:00", hasta="16:00"))
        with patch.object(site_doctors, "_doctor_by_id", return_value=doctor), \
             patch.object(site_base, "_now_art", return_value=datetime(2026, 10, 13, 8)), \
             patch.object(site_availability, "_place_of", return_value={}), \
             patch("app.modules.turnos.slot_holds.taken", return_value=set()):
            result = site_availability._appointments_for_doctor(SERVICE, "doctor")
        hours = {t["hora"] for t in result["medicos"][0]["turnos"]}
        self.assertIn("09:20", hours)
        self.assertTrue(all("09:00" <= hour < "12:00" for hour in hours))

    def test_suggestion_does_not_pass_off_a_requested_time_that_has_gone_by(self):
        # The desk preselects the time the patient asked for only while it is still open: the app
        # offers no past time. The J16 journey went red when the first offered time (09:15) passed
        # during the run and the approval field came up empty; the journey now asks for a time half
        # an hour ahead, and this pins the product rule with explicit clocks.
        service = dict(SERVICE, agenda=dict(SERVICE["agenda"], desde="08:00", hasta="17:00", cada_min=15))
        asked = {"dia": "2026-10-28", "hora": "09:15"}
        request = {"servicio_id": "study"}
        with patch("app.patient_web.catalog.doctors", return_value=[DOCTOR]), \
             patch.object(booking_requests, "_service_full", return_value=service):
            with patch.object(site_base, "_now_art", return_value=datetime(2026, 10, 28, 9, 14)):
                open_ = booking_queue._suggested_doctor(request, {}, asked)
            with patch.object(site_base, "_now_art", return_value=datetime(2026, 10, 28, 9, 16)):
                gone = booking_queue._suggested_doctor(request, {}, asked)
        self.assertTrue(open_.get("pedido"))
        self.assertEqual(open_["slot_iso"][:16], "2026-10-28T09:15")
        self.assertFalse(gone.get("pedido"), "a time that has gone by is not presented as the requested one")
        self.assertEqual(gone["slot_iso"][:16], "2026-10-28T09:30")

    def test_default_horizon_and_remedy_survive_missing_service_doc(self):
        with patch.object(booking_horizon.config, "get", side_effect=lambda key, default=None: default), \
             patch("app.modules.turnos.booking_limits._service_doc", return_value=None), \
             patch.object(booking_requests, "_resolve_slot_doctor", return_value=None):
            now = datetime(2026, 10, 13, 8)
            self.assertIsNone(booking_horizon.check_slot("old-service", "2026-11-12", "09:00", now=now))
            error = booking_horizon.check_slot("old-service", "2026-11-13", "09:00", now=now)
        self.assertEqual(error["accion"]["url"], "/service/old-service/book?slots=1")


if __name__ == "__main__":
    unittest.main()
