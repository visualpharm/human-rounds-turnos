"""tests/routes/test_staff_response_models.py — every staff operation of booking
requests, patients and the doctor's appointments (`tests/_staff_response_models.py`)
answered by the live app, and the answer checked against the model the
OpenAPI schema names for it (TASK-76, OpenAPI stage D, area b): the success
status the schema documents, a JSON body that matches the model, and no
top-level key the model does not name.

The catalog is the fictional public example-town pack's; requests,
appointments, patients, dependents and the doctor's account are made through
the app's own functions on a freshly truncated database. The EMR connector is
selected where an operation needs one (`CONNECTOR_EMR_SYNC=hsi`, no network:
nothing is pending). `test_every_operation_of_the_area_is_answered_here`
keeps the list complete: a new operation of the area needs its answer here.
"""

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TEST_DIR)
for _p in (_ROOT, _HERE, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _clock  # noqa: E402
import _env  # noqa: E402
from _routes_fixture import RouteTestCase, admin_cookie  # noqa: E402
import _response_models as shared  # noqa: E402
import _staff_response_models as rm  # noqa: E402

from app.core import api_body, db_accounts, db_booking, db_operators  # noqa: E402

_DNI = "30999711"
_DEPENDENT = "40999711"
_SERVICIO = "medicina-general-riverside-clinic"
_CENTRO = "riverside-clinic"
_MEDICO_ID = "gonzalez-juan"
_DOCTOR = "dr.gonzalez"

# (method, path template) -> the test that answers it.
_ANSWERED = {}


def answers(*operations):
    def mark(fn):
        for operation in operations:
            _ANSWERED[operation] = fn.__name__
        return fn
    return mark


def _app():
    from app import main as app_module
    return app_module.app


def _schema():
    from app.routers import api_docs
    return api_docs.build_schema(_app())


_ART = timezone(timedelta(hours=-3))


def _in_days(days, hour=10):
    local = datetime.now(_ART) + timedelta(days=days)
    return local.replace(hour=hour, minute=0, second=0, microsecond=0)


def _free_slot(case):
    """A free slot of the example town's doctor, from the service's own time grid."""
    status, _hdrs, raw = case._request("GET", f"/api/service-slots/{_SERVICIO}?medico={_MEDICO_ID}")
    assert status == 200, raw
    slot = json.loads(raw)["medicos"][0]["turnos"][-1]
    return datetime.fromisoformat(f"{slot['dia']}T{slot['hora']}").replace(tzinfo=_ART)


class _AreaCase(RouteTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.documented = {(method, path): shared.success_answer(operation)
                          for method, path, operation in rm.area_operations(_schema(), _app())}

    def setUp(self):
        super().setUp()
        from app.auth import mode as auth_mode
        from app.core import config
        # Settings are cached for a few seconds: the class before this one read its own.
        for reset in (lambda: auth_mode._cache.update(v=None, t=0.0), config._reset_cache_para_tests):
            reset()
            self.addCleanup(reset)

    def _answer(self, method, template, path=None, headers=None, body=None):
        """Ask `method path` (`template` filled in) as the admin unless `headers`
        say otherwise, and check the answer against the documented model;
        returns the decoded body."""
        status, hdrs, raw = self._request(method.upper(), path or template,
                                          headers=headers or admin_cookie(), body=body)
        code, name = self.documented[(method, template)]
        self.assertEqual(status, int(code), f"{method.upper()} {path or template}: {raw[:300]!r}")
        if name is None:
            self.assertEqual(raw, b"")
            return None
        self.assertTrue(hdrs.get("Content-Type", "").startswith("application/json"))
        value = json.loads(raw.decode("utf-8"))
        with self.subTest(operation=(method, path or template)):
            shared.check_answer(self, api_body.models()[name], value)
        return value

    def _patient(self, dni=_DNI):
        db_accounts.create_user(dni, "Marta", "Gomez", "1980-05-10", "F", "+542254000711",
                                "throwaway-12345")

    def _pending(self, dni=_DNI):
        return db_booking.create_booking_request(dni, "Marta", "Gomez", "1980-05-10", "+542254000711",
                                                 _SERVICIO, _CENTRO)

    def _approved(self, dni=_DNI, days=5, hour=10, when=None):
        """A request approved with the example town's doctor, `days` from today
        or `when` (written as the desk's approval writes it; the doctor's agenda
        is not what these answers are about)."""
        from app.patient_web import catalog
        sid = self._pending(dni)
        db_booking.approve(sid, when or _in_days(days, hour), medico_asignado=catalog.doctor_name(_MEDICO_ID),
                           medico_asignado_id=_MEDICO_ID, resuelta_por="routes.tier.test")
        return sid

    def _doctor(self):
        """The example town's doctor with its account; the cookie of that account."""
        db_operators.operator_create(_DOCTOR, "throwaway-12345", rol="medico", medico_id=_MEDICO_ID,
                                     email="dr.gonzalez@example.com")
        return admin_cookie(_DOCTOR, "medico")


class TestRequestsAnswers(_AreaCase):

    def test_every_operation_of_the_area_is_answered_here(self):
        """...or its answers are pinned in a golden file (checked by test_staff_response_docs.py)."""
        described = {key for key in self.documented if key not in rm.EXCLUDED}
        expected = described - shared.pinned_by_template(_app())
        self.assertEqual(sorted(expected - set(_ANSWERED)), [])
        self.assertEqual(sorted(set(_ANSWERED) - described), [])

    @answers(("get", "/api/admin/requests"), ("get", "/api/admin/requests/{req_id}/case-chat"))
    def test_the_queue(self):
        from app.core import db_chat
        self._patient()
        pending = self._pending()
        self._approved(days=6)
        db_chat.case_chat_set(pending, {
            "resumen": {"quiere": "un control", "urgencia": "esta_semana", "por_que": "", "hechos": [],
                        "fuente": "texto"},
            "mensajes": [{"role": "paciente", "texto": "un control"},
                         {"role": "asistente", "texto": "¿para cuándo?"}]})
        queue = self._answer("get", "/api/admin/requests")
        self.assertEqual({row["estado"] for row in queue["solicitudes"]}, {"pendiente", "aprobada"})
        for query in ("status=pendiente", "status=aprobada&limit=1&offset=0"):
            self._answer("get", "/api/admin/requests", f"/api/admin/requests?{query}")
        self._answer("get", "/api/admin/requests", headers=self._doctor())
        chat = self._answer("get", "/api/admin/requests/{req_id}/case-chat",
                            f"/api/admin/requests/{pending}/case-chat")
        self.assertEqual(len(chat["mensajes"]), 2)

    @answers(("post", "/api/admin/requests/{req_id}/cancel"), ("post", "/api/admin/requests/{req_id}/restore"),
             ("post", "/api/admin/requests/{req_id}/attendance"))
    def test_the_desk_changes_an_appointment(self):
        self._patient()
        sid = self._approved(when=_free_slot(self))  # restoring checks the doctor's agenda
        cancelled =self._answer("post", "/api/admin/requests/{req_id}/cancel",
                                 f"/api/admin/requests/{sid}/cancel")
        self.assertEqual(cancelled["solicitud"]["estado"], "cancelada")
        restored = self._answer("post", "/api/admin/requests/{req_id}/restore",
                                f"/api/admin/requests/{sid}/restore")
        self.assertEqual(restored["solicitud"]["estado"], "aprobada")
        past = self._approved(days=-1)
        for mark in ("presente", "ausente"):
            self._answer("post", "/api/admin/requests/{req_id}/attendance",
                         f"/api/admin/requests/{past}/attendance", body={"asistencia": mark})

    @answers(("get", "/api/admin/refeps"))
    def test_a_license_lookup(self):
        from app.modules.turnos import booking_requests_store as store
        store.refeps_set("104512", [{
            "apellido": "GONZALEZ", "nombre": "JUAN", "nroDoc": "20111222", "sexo": "M", "activo": True,
            "profesiones": [{"profesionReferencia": "Médico", "refepsEspecialidad": "Clínica",
                             "matriculas": [{"matricula": "104512", "origenEmite": "Nacional",
                                             "provinciaMatricula": "", "situacionMatricula": "Habilitada",
                                             "tipoSancion": None}]}]}], ok=True)
        found = self._answer("get", "/api/admin/refeps", "/api/admin/refeps?matricula=M.N.%20104512&medico=Juan%20Gonzalez")
        self.assertTrue(found["found"])
        self._answer("get", "/api/admin/refeps", "/api/admin/refeps?matricula=104512&medico=Otro")

    @answers(("get", "/api/admin/analytics/keys"), ("post", "/api/admin/analytics/keys"),
             ("delete", "/api/admin/analytics/keys"))
    def test_the_analytics_export_keys(self):
        made = self._answer("post", "/api/admin/analytics/keys", body={"label": "routes.tier"})
        self.assertTrue(made["key"].startswith("hra_"))
        listed = self._answer("get", "/api/admin/analytics/keys")
        self.assertEqual([k["label"] for k in listed["keys"]], ["routes.tier"])
        self._answer("delete", "/api/admin/analytics/keys", f"/api/admin/analytics/keys?id={made['id']}")

    @answers(("get", "/api/admin/patient-external-history"))
    def test_the_external_history(self):
        self._patient()
        off = self._answer("get", "/api/admin/patient-external-history",
                           f"/api/admin/patient-external-history?dni={_DNI}")
        self.assertEqual(off["reason"], "flag_off")


class TestEmrAnswers(_AreaCase):

    def setUp(self):
        _env.set_env(self, CONNECTOR_EMR_SYNC="hsi")
        super().setUp()

    @answers(("get", "/api/admin/hsi-sync"), ("post", "/api/admin/hsi-sync/resolver"),
             ("post", "/api/admin/requests/{req_id}/retry-hsi"))
    def test_the_emr_sync(self):
        self._answer("get", "/api/admin/hsi-sync")
        self._answer("post", "/api/admin/hsi-sync/resolver")
        self._patient()
        sid = self._approved(days=3)
        # Already in HSI: the retry answers the request without pushing anything.
        db_booking.update_hsi(sid, {"estado": "ok", "patient_id": "p-711", "appointment_id": "a-711"})
        _env.set_env(self, TURNOS_SKIP_HSI="0")
        self._answer("post", "/api/admin/requests/{req_id}/retry-hsi", f"/api/admin/requests/{sid}/retry-hsi")

    @answers(("post", "/api/admin/hsi-sync/conflicts"))
    def test_the_emr_conflicts(self):
        from app.core import db_hsi
        self._patient()
        # The request is no longer approved, so «rechazar» touches nothing and says why.
        conflict = db_hsi.hsi_conflict("booking_request", self._pending(), "outbound", "slot taken in HSI")
        answer = self._answer("post", "/api/admin/hsi-sync/conflicts",
                              body={"accion": "rechazar", "conflictos": [conflict["id"], 999999]})
        self.assertEqual([r["ok"] for r in answer["resultados"]], [False, True])


class TestDoctorAnswers(_AreaCase):

    @answers(("get", "/api/admin/doctors/{doctor}/compatible-services"),
             ("get", "/api/admin/doctors/{doctor}/appointments"), ("get", "/api/admin/doctors/stats"))
    def test_a_doctor_seen_by_the_desk(self):
        self._patient()
        self._approved(days=2)
        self.assertTrue(self._answer("get", "/api/admin/doctors/{doctor}/compatible-services",
                                     f"/api/admin/doctors/{_MEDICO_ID}/compatible-services"))
        self._answer("get", "/api/admin/doctors/{doctor}/appointments",
                     f"/api/admin/doctors/{_MEDICO_ID}/appointments")
        self._answer("get", "/api/admin/doctors/stats")

    @answers(("get", "/api/admin/my-appointments"), ("get", "/api/admin/my-appointments/feed"),
             ("post", "/api/admin/my-appointments/feed/rotate"))
    def test_the_doctors_own_appointments(self):
        cookie = self._doctor()
        self._patient()
        self._approved(days=2)
        mine = self._answer("get", "/api/admin/my-appointments", headers=cookie)
        self.assertEqual(len(mine["turnos"]), 1)
        self.assertTrue(self._answer("get", "/api/admin/my-appointments/feed", headers=cookie)["url"])
        self.assertIsNone(self._answer("get", "/api/admin/my-appointments/feed", headers=cookie)["url"])
        self.assertTrue(self._answer("post", "/api/admin/my-appointments/feed/rotate", headers=cookie)["url"])

    @answers(("get", "/api/admin/day-queue"), ("get", "/api/admin/day-queue/calendar"),
             ("get", "/api/admin/day-queue/changes"), ("post", "/api/admin/day-queue/changes/{req_id}/hide"))
    def test_the_day_queue(self):
        # The two turnos below sit at 22:00 and 23:00 today: after 22:00 the desk refuses to cancel the
        # first («Ese turno ya pasó»), so the test was red every evening. The cancel check asks
        # `core.clock` (booking_decisions): freeze the application's «now» at 08:00 today.
        _clock.freeze(self, _in_days(0, hour=8))
        self._patient()
        today = self._approved(days=0, hour=23)
        moved = self._approved(days=0, hour=22)
        self._answer("post", "/api/admin/requests/{req_id}/cancel", f"/api/admin/requests/{moved}/cancel")
        queue = self._answer("get", "/api/admin/day-queue")
        self.assertEqual(queue["total"], 1)
        self._answer("get", "/api/admin/day-queue", headers=self._doctor())
        dia = _in_days(0).date()
        self._answer("get", "/api/admin/day-queue/calendar",
                     f"/api/admin/day-queue/calendar?desde={dia}&hasta={dia + timedelta(days=13)}&dia={dia}")
        changes = self._answer("get", "/api/admin/day-queue/changes",
                               f"/api/admin/day-queue/changes?ids={today},{moved}")
        self.assertEqual(len(changes["cambios"]), 2)
        self._answer("post", "/api/admin/day-queue/changes/{req_id}/hide",
                     f"/api/admin/day-queue/changes/{moved}/hide")

    @answers(("get", "/api/admin/doctor-patients"))
    def test_the_doctors_patients(self):
        cookie = self._doctor()
        self._patient()
        self._approved(days=2)
        self._answer("get", "/api/admin/doctor-patients", headers=cookie)
        self._answer("get", "/api/admin/doctor-patients")

    @answers(("get", "/api/admin/doctor-profile"), ("post", "/api/admin/doctor-profile"),
             ("post", "/api/admin/onboarding/complete"))
    def test_the_doctors_own_profile(self):
        cookie = self._doctor()
        profile = self._answer("get", "/api/admin/doctor-profile", headers=cookie)
        self.assertTrue(profile["item"]["nombre"])
        item = profile["item"]
        saved = self._answer("post", "/api/admin/doctor-profile", headers=cookie, body={
            "nombre_pila": item["nombre_pila"], "apellido": item["apellido"],
            "especialidad": item["especialidad"], "telefono": "2254 40-0711"})
        self.assertEqual(saved["item"]["telefono"], "2254 40-0711")
        from app.admin_panel import doctor_workspace
        # The example town's doctor has no email in the pack; what the onboarding asks for is not this answer.
        with patch.object(doctor_workspace, "onboarding_missing", return_value=[]):
            done = self._answer("post", "/api/admin/onboarding/complete", headers=cookie)
        self.assertEqual(done["next"], "/admin/account")


class TestPatientsAnswers(_AreaCase):

    @answers(("get", "/api/admin/patients"), ("get", "/api/admin/patients/{dni}"),
             ("get", "/api/admin/patients/{dni}/access-log"))
    def test_the_patients(self):
        self._patient()
        self._approved(days=3)
        listed = self._answer("get", "/api/admin/patients")
        self.assertTrue(listed)
        for query in ("q=Gomez", "q=Gonzalez", "limit=1&offset=0"):
            self._answer("get", "/api/admin/patients", f"/api/admin/patients?{query}")
        self._answer("get", "/api/admin/patients/{dni}", f"/api/admin/patients/{_DNI}")
        self._answer("get", "/api/admin/patients/{dni}/access-log", f"/api/admin/patients/{_DNI}/access-log")

    @answers(("get", "/api/admin/dni-claims"), ("post", "/api/admin/dni-claims/{claim_id}/resolve"))
    def test_the_desk_resolves_a_dni_claim(self):
        from app.core import db_accounts
        self._patient()
        db_accounts.create_user("30999799", "Ana", "Reclamo", "1990-01-01", "F", "1155550000", "Clave-123456")
        claim = db_accounts.add_claim(_DNI, "30999799", "pending")
        listed = self._answer("get", "/api/admin/dni-claims")
        self.assertTrue(any(i["id"] == claim for i in listed["items"]))
        self._answer("post", "/api/admin/dni-claims/{claim_id}/resolve",
                     f"/api/admin/dni-claims/{claim}/resolve", body={"aprobar": False, "nota": "no es el titular"})

    @answers(("post", "/api/admin/patients/{dni}/temp-password"),
             ("delete", "/api/admin/patients/{dni}/dependents/{dni_dep}"),
             ("delete", "/api/admin/patients/{dni}"))
    def test_the_desk_changes_a_patient(self):
        self._patient()
        status, _hdrs, raw = self._request("POST", f"/api/admin/patients/{_DNI}/dependents", headers=admin_cookie(),
                                           body={"dni": _DEPENDENT, "nombre": "Leo", "apellido": "Gomez",
                                                 "nacimiento": "2015-05-05", "sexo": "M", "vinculo": "hijo"})
        self.assertEqual(status, 200, raw)
        self._answer("post", "/api/admin/patients/{dni}/temp-password",
                     f"/api/admin/patients/{_DNI}/temp-password")
        self._answer("delete", "/api/admin/patients/{dni}/dependents/{dni_dep}",
                     f"/api/admin/patients/{_DNI}/dependents/{_DEPENDENT}")
        self._answer("delete", "/api/admin/patients/{dni}", f"/api/admin/patients/{_DNI}")

    @answers(("get", "/api/admin/appointment-slots"))
    def test_the_desks_free_slots(self):
        for query in (f"servicio_id={_SERVICIO}", f"servicio_id={_SERVICIO}&medico={_MEDICO_ID}"):
            self._answer("get", "/api/admin/appointment-slots", f"/api/admin/appointment-slots?{query}")


if __name__ == "__main__":
    unittest.main()
