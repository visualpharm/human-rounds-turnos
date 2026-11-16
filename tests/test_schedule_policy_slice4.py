"""Schedule ownership and public horizon regressions (synthetic catalog only)."""
import json
from contextlib import nullcontext
import unittest
from datetime import datetime
from unittest.mock import patch
from app.patient_web import site_availability, site_base, site_catalog
from app.admin_panel import admin_content
from app.modules.turnos import slot_holds
from app.core import db_catalog
from app.core import db_slots
from app.core import db_operators
from app.core import db_booking

SERVICE = {"id": "study", "nombre": "Study", "modo": "solicitud", "cluster": "c", "centro": "hospital",
           "agenda": {"dias": list(range(7)), "desde": "09:00", "hasta": "10:00", "cada_min": 20}}
DOCTOR = {"id": "doctor", "nombre": "Doctor Example", "cluster": "c", "centro": "hospital",
          "especialidad": "Study",  # the doctor IS the service «Study» (strict relation: locks and resets)
          "agenda": {"dias": list(range(7)), "desde": "15:00", "hasta": "16:00", "cada_min": 20}}
NOW = datetime(2026, 10, 13, 8)

class ScheduleRegressions(unittest.TestCase):
    def test_service_schedule_is_exclusive_even_before_catalog_cleanup(self):
        with patch.object(site_catalog, "_doctors", return_value=[DOCTOR]), \
             patch.object(site_availability, "_slot_source_info", return_value={}), \
             patch.object(site_base, "_now_art", return_value=NOW), \
             patch.object(site_availability, "_place_of", return_value={}), \
             patch.object(slot_holds, "taken", return_value=set()):
            result = site_availability._appointments_for_service(SERVICE)
        hours = [t["hora"] for b in result["medicos"] for t in b["turnos"]]
        self.assertTrue(hours)
        self.assertTrue(all(h < "10:00" for h in hours), hours)

    def test_service_horizon_filters_offers(self):
        service = dict(SERVICE, dias_anticipacion=2)
        with patch.object(site_catalog, "_doctors", return_value=[]), \
             patch.object(site_availability, "_slot_source_info", return_value={}), \
             patch.object(site_base, "_now_art", return_value=NOW), \
             patch.object(site_availability, "_place_of", return_value={}), \
             patch.object(slot_holds, "taken", return_value=set()):
            result = site_availability._appointments_for_service(service)
        self.assertEqual({t["dia"] for b in result["medicos"] for t in b["turnos"]}, {"2026-10-13", "2026-10-14"})

    def test_slot_hold_rejects_outside_horizon_before_taking_slot(self):
        service = dict(SERVICE, dias_anticipacion=2)
        with patch.object(slot_holds, "_allowed", return_value=True), \
             patch.object(slot_holds, "_service_by_id", return_value=service), \
             patch.object(slot_holds, "try_take", return_value=True) as take:
            status, payload = slot_holds.create_hold("local", json.dumps({"servicio_id": "study", "dia": "2099-01-01", "hora": "09:00"}).encode(), lambda: 5)
        self.assertEqual(status, 409)
        self.assertIn("detalle", payload)
        take.assert_not_called()

    def test_service_save_reports_doctor_reset_conflict_before_any_write(self):
        # The stored service has a DIFFERENT schedule: this save changes it.
        # (A save that leaves the schedule as stored never asks: see
        # test_agenda_ownership_hotfix.)
        stored = dict(SERVICE, agenda=dict(SERVICE["agenda"], desde="08:00"))
        with patch.object(db_catalog, "catalog_schedule_lock", return_value=nullcontext(None)), \
             patch.object(admin_content.catalog, "validate", side_effect=lambda tipo, body, **kw: (body, None)), \
             patch.object(admin_content.catalog, "admin_list", side_effect=lambda tipo: [DOCTOR] if tipo == "medicos" else [stored]), \
             patch.object(db_catalog, "catalog_get", return_value=None), \
             patch.object(db_catalog, "catalog_upsert") as write:
            with self.assertRaises(admin_content.ContentError) as caught:
                admin_content.edit_item("servicios", "study", SERVICE)
        self.assertEqual(caught.exception.status, 409)
        write.assert_not_called()


class HorizonResolution(unittest.TestCase):
    def test_resolution_and_clamp(self):
        from app.modules.turnos import booking_horizon as horizon
        with patch.object(horizon.config, "get", return_value="21"):
            self.assertEqual(horizon.days_for(), 21)
            self.assertEqual(horizon.days_for({"dias_anticipacion": 14}), 14)
            self.assertEqual(horizon.days_for({"dias_anticipacion": 14}, {"dias_anticipacion": 7}), 7)
            self.assertEqual(horizon.days_for({}, {"agenda": {"dias_anticipacion": 3}}), 3)
            for invalid in (0, -1, "", None, "no"):
                self.assertEqual(horizon.days_for({"dias_anticipacion": invalid}), 21)
            self.assertEqual(horizon.days_for({"dias_anticipacion": 9999}), 365)
            self.assertEqual(horizon.days_for({"dias_anticipacion": 1}), 1)

    def test_opening_time_is_inclusive_at_noon_and_uses_installation_timezone(self):
        from datetime import timezone
        from app.modules.turnos import booking_horizon as horizon
        service = {"dias_anticipacion": 7, "apertura_turnos": "12:00"}
        self.assertFalse(horizon.allows("2026-10-19", service, now=NOW.replace(hour=11, minute=59)))
        self.assertTrue(horizon.allows("2026-10-19", service, now=NOW.replace(hour=12)))
        self.assertFalse(horizon.allows("2026-10-20", service, now=NOW.replace(hour=12)))
        self.assertTrue(horizon.allows("2026-10-19", service, now=NOW.replace(hour=15, tzinfo=timezone.utc)))
        with patch.object(horizon.config, "get", return_value="13:00"):
            self.assertEqual(horizon.opens_for(service, {"apertura_turnos": "09:00"}), "09:00")
            self.assertEqual(horizon.opens_for({"apertura_turnos": "bad"}), "13:00")

    def test_catalog_validates_overrides(self):
        from app.patient_web import catalog
        for bad in (0, -1, 366, "abc", True):
            self.assertIsNotNone(catalog._normalizar_booking_window({"dias_anticipacion": bad})[1])
        self.assertEqual(catalog._normalizar_booking_window({"dias_anticipacion": "7", "apertura_turnos": "12:00"})[0],
                         {"dias_anticipacion": 7, "apertura_turnos": "12:00"})
        self.assertIsNotNone(catalog._normalizar_booking_window({"apertura_turnos": "24:00"})[1])
        self.assertEqual(catalog._normalizar_booking_window({"dias_anticipacion": "", "apertura_turnos": ""})[0], {})

    def test_site_settings_accept_horizon_and_opening_time_as_one_validated_save(self):
        from app.admin_panel import settings_admin
        values = {"BOOKING_HORIZON_DAYS": "14", "BOOKING_OPENS_AT": "12:00"}
        with patch.object(settings_admin.config, "guardar") as save:
            result, error = settings_admin._guardar_turnos(values)
            self.assertIsNone(error)
            self.assertEqual(save.call_args.args[0], values)
            save.reset_mock()
            self.assertIsNotNone(settings_admin._guardar_turnos(dict(values, BOOKING_OPENS_AT="99:00"))[1])
            save.assert_not_called()

    def test_real_patient_create_rejects_before_hold_or_insert(self):
        from app.modules.turnos import booking_requests as bookings, booking_limits, booking_horizon
        from app.modules.turnos import booking_create
        with patch.object(bookings.store, "attendance_summary_dni", return_value={}), \
             patch.object(bookings, "_service_valid", return_value=True), \
             patch.object(bookings, "_service_full", return_value=dict(SERVICE, dias_anticipacion=2)), \
             patch.object(booking_limits, "_service_doc", return_value=dict(SERVICE, dias_anticipacion=2)), \
             patch.object(booking_horizon, "_local_now", return_value=NOW), \
             patch.object(bookings.store, "create_booking_request_with_limit") as insert, \
             patch.object(slot_holds, "try_take") as take:
            status, result = booking_create.create_request({"dni": "36555444", "nombre": "Synthetic", "apellido": "Patient"},
                {"servicio_id": "study", "slot_dia": "2026-10-15", "slot_hora": "09:00"})
        self.assertEqual(status, 409)
        self.assertEqual(result["error"], "turno_fuera_de_anticipacion")
        self.assertTrue(result["detalle"])
        insert.assert_not_called(); take.assert_not_called()

    def test_patient_reschedule_rechecks_horizon_before_mutating_existing_request(self):
        from app.modules.turnos import booking_requests as bookings, booking_limits, booking_horizon
        from app.modules.turnos import booking_patient
        row = {"id": 10, "dni": "36555444", "estado": "pendiente", "servicio_id": "study"}
        service = dict(SERVICE, dias_anticipacion=2)
        with patch.object(bookings.store, "get_booking_request", return_value=row), \
             patch.object(bookings, "_service_full", return_value=service), \
             patch.object(booking_limits, "_service_doc", return_value=service), \
             patch.object(booking_horizon, "_local_now", return_value=NOW), \
             patch.object(slot_holds, "try_take") as take:
            status, result = booking_patient._reschedule_core(None, "36555444", 10,
                lambda: {"dia": "2026-10-15", "hora": "09:00"})
        self.assertEqual(status, 409)
        self.assertEqual(result["error"], "turno_fuera_de_anticipacion")
        take.assert_not_called()

    def test_real_slot_hold_uses_service_override(self):
        from app.modules.turnos import booking_limits, booking_horizon
        service = dict(SERVICE, dias_anticipacion=2)
        with patch.object(booking_limits, "_service_doc", return_value=service), \
             patch.object(booking_horizon, "_local_now", return_value=NOW), \
             patch.object(slot_holds, "_allowed", return_value=True), \
             patch.object(slot_holds, "_service_by_id", return_value=service), \
             patch.object(slot_holds, "try_take", return_value=True) as take:
            raw = {"servicio_id": "study", "dia": "2026-10-15", "hora": "09:00"}
            self.assertEqual(slot_holds.create_hold("local", json.dumps(raw).encode(), lambda: 5)[0], 409)
            take.assert_not_called()
            raw["dia"] = "2026-10-14"
            self.assertEqual(slot_holds.create_hold("local", json.dumps(raw).encode(), lambda: 5)[0], 200)

    def test_doctor_horizon_is_resolved_from_calendar_not_forged_id_or_chip_limit(self):
        from app.modules.turnos import booking_requests, booking_limits, booking_horizon
        doctor = dict(DOCTOR, dias_anticipacion=1,
            agenda={"dias": list(range(7)), "desde": "08:00", "hasta": "18:00", "cada_min": 10})
        service = dict(SERVICE); service.pop("agenda")
        with patch.object(booking_requests, "_service_full", return_value=service), \
             patch.object(booking_requests.catalog if hasattr(booking_requests, "catalog") else admin_content.catalog, "doctors", return_value=[doctor]), \
             patch.object(booking_limits, "_service_doc", return_value=service), \
             patch.object(admin_content.catalog, "doctor_doc", side_effect=lambda id: doctor if id == "doctor" else None), \
             patch.object(booking_horizon, "_local_now", return_value=NOW):
            self.assertEqual(booking_requests._resolve_slot_doctor("study", "2026-10-14", "17:00"), "doctor")
            self.assertIsNotNone(booking_horizon.check_slot("study", "2026-10-14", "17:00"))

    def test_staff_creation_is_exempt_and_uses_same_occupancy_lock(self):
        from app.admin_panel import admin_appointments as desk
        from app.modules.turnos import booking_horizon, booking_decisions
        row = {"id": 99, "estado": "aprobada"}
        with patch.object(desk, "_service", return_value=dict(SERVICE, dias_anticipacion=1)), \
             patch.object(desk.admin_auth, "centros_permitidos", return_value=None), \
             patch.object(db_booking, "create_booking_request", return_value=row), \
             patch.object(slot_holds, "try_take", return_value=True), \
             patch.object(db_slots, "hold_release"), \
             patch.object(booking_decisions, "_persist_approval", return_value=row) as approve, \
             patch.object(booking_horizon, "check_slot", side_effect=AssertionError("staff consulted public horizon")):
            desk.create_appointment({"rol": "admin", "usuario": "admin"}, None,
                {"nota": "Synthetic patient", "servicio_id": "study", "dia": "2099-01-01", "hora": "09:00"})


class ScheduleServerRules(unittest.TestCase):
    def patches(self):
        from contextlib import ExitStack
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(db_catalog, "catalog_schedule_lock", return_value=nullcontext("transaction")))
        stack.enter_context(patch.object(admin_content.catalog, "validate", side_effect=lambda tipo, doc, **kw: (dict(doc), None)))
        stack.enter_context(patch.object(admin_content.catalog, "invalidate"))
        stack.enter_context(patch.object(db_catalog, "catalog_get", return_value=None))
        stack.enter_context(patch.object(admin_content.catalog, "services", return_value=[SERVICE]))
        stack.enter_context(patch.object(admin_content.catalog, "admin_list", side_effect=lambda tipo: [DOCTOR] if tipo == "medicos" else [SERVICE]))
        return stack.enter_context(patch.object(db_catalog, "catalog_upsert"))

    def test_explicit_confirmation_resets_all_doctor_centers_and_saves_in_same_transaction(self):
        from copy import deepcopy
        doctor = deepcopy(DOCTOR)
        doctor["centros"] = [{"centro": "hospital", "franjas": [doctor["agenda"]], "excepciones": [{"tipo": "cerrado", "fecha": "2026-11-01"}]}]
        write = self.patches()
        # The service takes the schedule over: it had none before this save.
        stored = dict(SERVICE); stored.pop("agenda")
        with patch.object(admin_content.catalog, "admin_list", side_effect=lambda tipo: [doctor] if tipo == "medicos" else [stored]):
            admin_content.edit_item("servicios", "study", dict(SERVICE, confirmar_reinicio_agendas=True))
        self.assertEqual(write.call_count, 2)
        reset, saved = write.call_args_list
        self.assertNotIn("agenda", reset.args[2])
        self.assertEqual(reset.args[2]["centros"][0]["franjas"], [])
        self.assertEqual(reset.args[2]["centros"][0]["excepciones"], [])
        self.assertNotIn("confirmar_reinicio_agendas", saved.args[2])
        self.assertEqual(reset.kwargs["connection"], saved.kwargs["connection"])
        self.assertTrue(doctor["centros"][0]["franjas"], "input catalog is never mutated")

    def test_doctor_and_admin_cannot_save_individual_schedule_when_service_manages(self):
        write = self.patches()
        for identity in ({"rol": "admin"}, {"rol": "medico", "usuario": "own"}):
            with patch.object(db_operators, "operator_doctor_id", return_value="doctor"):
                with self.assertRaises(admin_content.ContentError) as caught:
                    admin_content.edit_item("medicos", "doctor", dict(DOCTOR,
                        agenda=dict(DOCTOR["agenda"], desde="14:00")), identity)
            self.assertEqual(caught.exception.status, 409)
            self.assertIn("administrador", caught.exception.payload["detalle"])
        write.assert_not_called()

    def test_doctor_can_save_own_schedule_when_service_has_no_agenda(self):
        write = self.patches()
        service = dict(SERVICE); service.pop("agenda")
        with patch.object(admin_content.catalog, "services", return_value=[service]), \
             patch("app.modules.turnos.doctor_appointments.compatible_services_data", return_value={"items": []}), \
             patch.object(admin_content.catalog, "admin_list", side_effect=lambda tipo: [DOCTOR] if tipo == "medicos" else [service]), \
             patch.object(db_operators, "operator_doctor_id", return_value="doctor"):
            result = admin_content.edit_item("medicos", "doctor", dict(DOCTOR, nombre="Forged name"), {"rol": "medico", "usuario": "own"})
        self.assertTrue(result["ok"])
        self.assertEqual(write.call_args.args[2]["nombre"], "Doctor Example")
        self.assertEqual(write.call_args.args[2]["agenda"], DOCTOR["agenda"])

    def test_doctor_cannot_write_another_doctor(self):
        write = self.patches()
        with patch.object(db_operators, "operator_doctor_id", return_value="other"):
            with self.assertRaises(admin_content.ContentError) as caught:
                admin_content.edit_item("medicos", "doctor", DOCTOR, {"rol": "medico", "usuario": "own"})
        self.assertEqual(caught.exception.status, 404)
        write.assert_not_called()

    def test_restore_cannot_reintroduce_a_conflicting_schedule(self):
        write = self.patches()
        with patch.object(admin_content.catalog, "base_items", return_value=[DOCTOR]):
            with self.assertRaises(admin_content.ContentError) as caught:
                admin_content.restore_item("medicos", "doctor")
        self.assertEqual(caught.exception.status, 409)
        write.assert_not_called()

    def test_service_closure_does_not_close_other_service_for_same_doctor(self):
        from app.modules.turnos import doctor_appointments as da
        service = dict(SERVICE, agenda={"franjas": [SERVICE["agenda"]],
            "excepciones": [{"fecha": "2026-10-13", "tipo": "cerrado"}]})
        other = dict(SERVICE, id="other", agenda=DOCTOR["agenda"])
        with patch.object(admin_content.catalog, "services", return_value=[service, other]):
            self.assertEqual(da._free_of_day(DOCTOR, "2026-10-13", set()), ["15:00", "15:20", "15:40"])

    def test_the_doctors_stats_resolve_each_agenda_once_not_once_per_day(self):
        # 05.11.2026: the catalog merge and the managed-service walk ran for
        # every one of the 14 days of every doctor, and the Médicos list
        # waited 20 s and more for its stats. One doctor = one walk.
        from app.modules.turnos import doctor_appointments as da
        from app.modules.turnos import schedule_policy
        doctors = [dict(DOCTOR, id=f"doctor-{i}") for i in range(3)]
        real = schedule_policy.managed_services
        with patch.object(admin_content.catalog, "admin_list", return_value=doctors), \
             patch.object(admin_content.catalog, "services", return_value=[SERVICE]) as services, \
             patch.object(schedule_policy, "managed_services", side_effect=real) as managed, \
             patch.object(db_booking, "list_approved_future", return_value=[]), \
             patch.object(db_operators, "operator_doctor_ids", return_value=set()):
            stats = da.doctors_stats_data()["medicos"]
        self.assertEqual(services.call_count, 1)
        self.assertEqual(managed.call_count, len(doctors))
        # The service's hour every day (three slots), today's past ones aside.
        for st in stats.values():
            self.assertEqual(st["horas_semana"], 7.0)
            self.assertTrue(39 <= st["slots_14d"] <= 42, st["slots_14d"])


class ServiceLinkFollowsPermission(unittest.TestCase):
    """Hallazgo K8: en la ficha de un médico cuyo horario lo define el
    servicio, «Abrir el servicio» se ofrecía a todo rol que no fuera médico;
    el gestor de agendas (sin `servicios`) caía en «No tenés permiso». El
    link se ofrece sólo a quien tiene el permiso `servicios`."""

    def _list_as(self, identity, permissions):
        from app.auth import admin_auth
        token = admin_auth._ACTIVE_PERMISSIONS.set(tuple(sorted(permissions)))
        try:
            with patch.object(admin_content.catalog, "services", return_value=[SERVICE]), \
                 patch.object(admin_content.catalog, "admin_list", side_effect=lambda tipo: [DOCTOR] if tipo == "medicos" else [SERVICE]), \
                 patch("app.modules.turnos.site_holidays.upcoming_labeled", return_value=[]):
                return admin_content.list_payload("medicos", identity)["items"][0]
        finally:
            admin_auth._ACTIVE_PERMISSIONS.reset(token)

    def test_gestor_without_servicios_gets_no_service_link(self):
        from app.core import permissions
        item = self._list_as({"rol": "gestor_agendas", "usuario": "gestor"},
                             permissions.BUILTIN_ROLES["gestor_agendas"]["permissions"])
        self.assertTrue(item["_agendas_servicio"])
        self.assertFalse(item["_agendas_servicio"][0]["_puede_editar"])

    def test_admin_keeps_the_service_link(self):
        from app.core import permissions
        item = self._list_as({"rol": "admin", "usuario": "admin"}, permissions.ALL)
        self.assertTrue(item["_agendas_servicio"][0]["_puede_editar"])


class QueueCalendarScope(unittest.TestCase):
    def test_doctor_scope_center_scope_and_adjacent_days_apply_before_projection(self):
        from app.modules.turnos import doctor_appointments as da
        rows = [{"dia": "2026-09-01", "medico_asignado_id": "doctor", "centro_slug": "hospital"},
                {"dia": "2026-10-14", "medico_asignado_id": "doctor", "centro_slug": "hospital"},
                {"dia": "2026-10-15", "medico_asignado_id": "other", "centro_slug": "hospital"},
                {"dia": "2026-10-16", "medico_asignado_id": "doctor", "centro_slug": "other"}]
        with patch.object(db_slots, "approved_calendar_groups", return_value=rows), \
             patch.object(da, "own_doctor", return_value=DOCTOR), \
             patch.object(da.admin_auth, "centros_permitidos", return_value={"hospital"}):
            result = da.queue_calendar_data({"rol": "medico"}, "2026-10-01", "2026-10-31", "2026-10-13", "other")
        self.assertEqual(result["dias"], ["2026-10-14"])
        self.assertEqual(result["anterior"], "2026-09-01")
        self.assertEqual(result["siguiente"], "2026-10-14")
        self.assertEqual(set(result), {"desde", "hasta", "dia", "dias", "conteos", "atencion", "anterior", "siguiente"})

    def test_admin_center_filter_applies_to_dates_and_both_neighbor_directions(self):
        from app.modules.turnos import doctor_appointments as da
        rows = [{"dia": "2026-10-11", "centro_slug": "hospital"},
                {"dia": "2026-10-12", "centro_slug": "other"},
                {"dia": "2026-10-14", "centro_slug": "other"},
                {"dia": "2026-10-19", "centro_slug": "hospital"}]
        with patch.object(db_slots, "approved_calendar_groups", return_value=rows), \
             patch.object(da.admin_auth, "centros_permitidos", return_value=None), \
             patch.object(da, "_center_name", side_effect=lambda slug: "Hospital" if slug == "hospital" else "Other"):
            result = da.queue_calendar_data({"rol": "admin"}, "2026-10-01", "2026-10-31", "2026-10-13", centro="Hospital")
        self.assertEqual(result["dias"], ["2026-10-11", "2026-10-19"])
        self.assertEqual(result["anterior"], "2026-10-11")
        self.assertEqual(result["siguiente"], "2026-10-19")

    def test_ranges_are_bounded_and_unlinked_doctor_fails_closed(self):
        from app.modules.turnos import doctor_appointments as da
        for start, end in (("2026-11-01", "2026-10-01"), ("bad", "2026-10-31"), ("2025-01-01", "2027-01-01")):
            with self.assertRaises(da.DomainError) as error:
                da.queue_calendar_data({"rol": "admin"}, start, end)
            self.assertEqual(error.exception.status, 400)
        with patch.object(db_slots, "approved_calendar_groups", return_value=[]), \
             patch.object(da.admin_auth, "centros_permitidos", return_value=None), \
             patch.object(da, "own_doctor", return_value=None):
            with self.assertRaises(da.DomainError) as error:
                da.queue_calendar_data({"rol": "medico"}, "2026-10-01", "2026-10-31")
            self.assertEqual(error.exception.status, 400)

    def test_calendar_counts_sum_only_permission_scoped_groups(self):
        from app.modules.turnos import doctor_appointments as da
        rows = [{'dia':'2026-10-14','medico_asignado_id':'doctor','centro_slug':'hospital','cantidad':3},
                {'dia':'2026-10-14','medico_asignado_id':'doctor','centro_slug':'hospital','cantidad':2},
                {'dia':'2026-10-14','medico_asignado_id':'other','centro_slug':'hospital','cantidad':99},
                {'dia':'2026-10-14','medico_asignado_id':'doctor','centro_slug':'other','cantidad':88}]
        with patch.object(db_slots,'approved_calendar_groups',return_value=rows), patch.object(da,'own_doctor',return_value=DOCTOR), patch.object(da.admin_auth,'centros_permitidos',return_value={'hospital'}):
            result=da.queue_calendar_data({'rol':'medico'},'2026-10-01','2026-10-31','2026-10-13')
        self.assertEqual(result['conteos'],{'2026-10-14':5})

    def test_a_single_doctor_gets_the_days_they_attend_from_today_on(self):
        # Lira 13.10.2026: the calendar showed only days WITH turnos; a doctor
        # also needs to see the days they attend (agenda) with nobody booked yet.
        from app.modules.turnos import doctor_appointments as da
        mondays = {"2026-10-12": ["09:00"], "2026-10-19": ["09:00"], "2026-10-26": ["09:00"]}
        with patch.object(db_slots, "approved_calendar_groups", return_value=[]), \
             patch.object(da, "own_doctor", return_value=DOCTOR), \
             patch.object(da.admin_auth, "centros_permitidos", return_value=None), \
             patch.object(da, "_today_art_iso", return_value="2026-10-15"), \
             patch.object(da, "_effective_day_hours", side_effect=lambda medico, dia, agendas=None: mondays.get(dia, [])):
            result = da.queue_calendar_data({"rol": "medico"}, "2026-10-01", "2026-10-31", "2026-10-13")
        self.assertEqual(result["atencion"], ["2026-10-19", "2026-10-26"], "no past days")

    def test_no_single_doctor_means_no_attendance_days_and_a_broken_agenda_is_survived(self):
        from app.modules.turnos import doctor_appointments as da
        with patch.object(db_slots, "approved_calendar_groups", return_value=[]), \
             patch.object(da.admin_auth, "centros_permitidos", return_value=None), \
             patch.object(da, "_today_art_iso", return_value="2026-10-15"):
            self.assertEqual(da.queue_calendar_data({"rol": "admin"}, "2026-10-01", "2026-10-31")["atencion"], [])
        with patch.object(da, "_today_art_iso", return_value="2026-10-15"), \
             patch.object(da, "_effective_day_hours", side_effect=RuntimeError("bad agenda")):
            self.assertEqual(da._attendance_days(DOCTOR, "2026-10-01", "2026-10-31"), [])


class NativeEndpointRules(unittest.TestCase):
    def test_calendar_endpoint_requires_login(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.modules.turnos import doctor_appointments_router
        app = FastAPI(); app.include_router(doctor_appointments_router.router)
        with TestClient(app) as client:
            response = client.get("/api/admin/day-queue/calendar?desde=2026-10-01&hasta=2026-10-31")
        self.assertEqual(response.status_code, 401)

    def test_calendar_native_endpoint_returns_only_own_dates(self):
        from unittest.mock import AsyncMock
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.modules.turnos import doctor_appointments_router as router, doctor_appointments as da
        app = FastAPI(); app.include_router(router.router)
        rows = [{"dia": "2026-10-14", "medico_asignado_id": "doctor", "centro_slug": "hospital"},
                {"dia": "2026-10-15", "medico_asignado_id": "other", "centro_slug": "hospital"}]
        with patch.object(router, "_require_admin", new=AsyncMock(return_value={"rol": "medico"})), \
             patch.object(db_slots, "approved_calendar_groups", return_value=rows), \
             patch.object(da, "own_doctor", return_value=DOCTOR), \
             patch.object(da.admin_auth, "centros_permitidos", return_value=None), TestClient(app) as client:
            response = client.get("/api/admin/day-queue/calendar?desde=2026-10-01&hasta=2026-10-31&dia=2026-10-13&medico=other")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["dias"], ["2026-10-14"])
        self.assertNotIn("medico", response.json())

    def test_doctor_save_native_endpoint_preserves_structured_conflict(self):
        from unittest.mock import AsyncMock
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.admin_panel import admin_content_router as router
        app = FastAPI(); app.include_router(router.router)
        with patch.object(router, "gate", new=AsyncMock(return_value={"rol": "medico", "usuario": "own"})) as gate, \
             patch.object(db_catalog, "catalog_schedule_lock", return_value=nullcontext(None)), \
             patch.object(db_operators, "operator_doctor_id", return_value="doctor"), \
             patch.object(admin_content.catalog, "invalidate"), \
             patch.object(admin_content.catalog, "services", return_value=[SERVICE]), \
             patch.object(admin_content.catalog, "admin_list", return_value=[DOCTOR]), TestClient(app) as client:
            response = client.put("/api/admin/content/doctors/doctor", json=DOCTOR)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "agenda_servicio_aplica")
        self.assertIn("administrador", response.json()["detalle"])
        self.assertTrue(gate.call_args.kwargs["permitir_medico"])
