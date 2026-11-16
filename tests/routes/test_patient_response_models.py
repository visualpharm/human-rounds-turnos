"""tests/routes/test_patient_response_models.py — every operation of the
public site and the patient's account (`tests/_response_models.py`) answered
by the live app, and the answer checked against the model the OpenAPI schema
names for it (TASK-76, OpenAPI stage D, area a): the success status the
schema documents, a JSON body that matches the model, and no top-level key
the model does not name.

The catalog is the fictional public example-town pack's; requests,
appointments, waitlist offers, the calendar feed, a doctor's own portrait and
the usability study are made through the app's own functions on a freshly
truncated database. `test_every_operation_of_the_area_is_answered_here` keeps
the list complete: a new operation of the area needs its answer here.
"""

import base64
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TEST_DIR)
for _p in (_ROOT, _HERE, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _env  # noqa: E402
from _routes_fixture import RouteTestCase, admin_cookie  # noqa: E402
import _response_models as rm  # noqa: E402

from app.core import api_body, db_accounts, db_booking, db_operators  # noqa: E402

_DNI = "30999611"
_SERVICIO = "medicina-general-riverside-clinic"
_CENTRO = "riverside-clinic"
_MEDICO_ID = "gonzalez-juan"

# (method, path template) -> the test that answers it.
_ANSWERED = {}


def answers(*operations):
    def mark(fn):
        for operation in operations:
            _ANSWERED[operation] = fn.__name__
        return fn
    return mark


def _schema():
    from app import main as app_module
    from app.routers import api_docs
    return api_docs.build_schema(app_module.app)


def _png_b64():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 30, 30)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _in_days(days):
    return (datetime.now(timezone.utc) + timedelta(days=days)).replace(microsecond=0)


def _first_slot(case):
    """{dia, hora} of a free slot of the service, from its own time grid."""
    status, _hdrs, raw = case._request("GET", f"/api/service-slots/{_SERVICIO}")
    assert status == 200, raw
    slot = json.loads(raw)["medicos"][0]["turnos"][-1]
    return {"dia": slot["dia"], "hora": slot["hora"]}


class _AreaCase(RouteTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.documented = {(method, path): rm.success_answer(operation)
                          for method, path, operation in rm.area_operations(_schema())}

    def setUp(self):
        super().setUp()
        from app.auth import mode as auth_mode
        from app.core import config
        # Settings are cached for a few seconds: the class before this one read its own.
        for reset in (lambda: auth_mode._cache.update(v=None, t=0.0), config._reset_cache_para_tests):
            reset()
            self.addCleanup(reset)

    def _answer(self, method, template, path=None, headers=None, body=None):
        """Ask `method path` (`template` filled in) and check the answer against the
        documented model; returns the decoded body."""
        status, hdrs, raw = self._request(method.upper(), path or template, headers=headers, body=body)
        code, name = self.documented[(method, template)]
        self.assertEqual(status, int(code), f"{method.upper()} {path or template}: {raw[:300]!r}")
        if name is None:
            self.assertEqual(raw, b"")
            return None
        self.assertTrue(hdrs.get("Content-Type", "").startswith("application/json"))
        value = json.loads(raw.decode("utf-8"))
        with self.subTest(operation=(method, path or template)):
            rm.check_answer(self, api_body.models()[name], value)
        return value

    def _patient(self, dni=_DNI):
        db_accounts.create_user(dni, "Marta", "Gomez", "1980-05-10", "F", "+542254000611",
                                "throwaway-12345")
        return {"Cookie": "st_ses=" + db_accounts.create_session(dni)}

    def _approved(self, dni=_DNI, days=5):
        sid = db_booking.create_booking_request(dni, "Marta", "Gomez", "1980-05-10", None,
                                                _SERVICIO, _CENTRO)
        status, _hdrs, raw = self._request(
            "POST", f"/api/admin/requests/{sid}/approve", headers=admin_cookie(),
            body={"turno_dt": _in_days(days).isoformat()})
        assert status == 200, raw
        return sid


class TestPatientSiteAnswers(_AreaCase):

    def test_every_operation_of_the_area_is_answered_here(self):
        """...or its answers are pinned in a golden file (checked by test_patient_response_docs.py)."""
        described = {key for key in self.documented if key not in rm.EXCLUDED}
        expected = described - rm.golden_operations()
        self.assertEqual(sorted(expected - set(_ANSWERED)), [])
        self.assertEqual(sorted(set(_ANSWERED) - described), [])

    @answers(("get", "/api/services"), ("get", "/api/doctors"), ("get", "/api/centers"),
             ("get", "/api/directory"), ("get", "/api/links"), ("get", "/api/specialties"),
             ("get", "/api/preparation-studies"))
    def test_catalog(self):
        self.assertTrue(self._answer("get", "/api/services")["servicios"])
        self.assertTrue(self._answer("get", "/api/doctors")["medicos"])
        self.assertTrue(self._answer("get", "/api/centers")["centros"])
        self.assertTrue(self._answer("get", "/api/directory")["clusters"])
        self.assertTrue(self._answer("get", "/api/links")["enlaces"])
        self._answer("get", "/api/specialties")
        self.assertTrue(self._answer("get", "/api/preparation-studies")["estudios"])

    @answers(("get", "/api/service-slots/{sid}"))
    def test_service_slots(self):
        template = "/api/service-slots/{sid}"
        grid = self._answer("get", template, f"/api/service-slots/{_SERVICIO}")
        self.assertTrue(grid["medicos"][0]["turnos"])
        for query in (f"medico={_MEDICO_ID}", f"prefer_medico={_MEDICO_ID}"):
            self._answer("get", template, f"/api/service-slots/{_SERVICIO}?{query}")
        self.assertEqual(self._answer("get", template, "/api/service-slots/no-such-service"), {})

    @answers(("post", "/api/search-log"))
    def test_search_log(self):
        self._answer("post", "/api/search-log", body={"q": "clinica", "resultados": 3})

    @answers(("post", "/api/ev"))
    def test_event_batch(self):
        self._answer("post", "/api/ev", body={"events": []})

    @answers(("post", "/api/slot-hold"), ("delete", "/api/slot-hold/{token}"))
    def test_slot_hold(self):
        slot = _first_slot(self)
        held = self._answer("post", "/api/slot-hold", body=dict(slot, servicio_id=_SERVICIO, token="hold-611"))
        self.assertEqual(held["token"], "hold-611")
        self._answer("delete", "/api/slot-hold/{token}", "/api/slot-hold/hold-611")

    @answers(("get", "/api/mi/foto"), ("post", "/api/mi/foto"), ("delete", "/api/mi/foto"))
    def test_a_doctors_own_portrait(self):
        from app.patient_web import doctor_photos
        photos = tempfile.mkdtemp(prefix="response-models-portraits-")
        self.addCleanup(shutil.rmtree, photos, True)
        stored = patch.object(doctor_photos, "PHOTOS_DIR", photos)
        stored.start()
        self.addCleanup(stored.stop)
        db_operators.operator_create("dr.gonzalez", "throwaway-12345", rol="medico", medico_id=_MEDICO_ID)
        cookie = admin_cookie("dr.gonzalez", "medico")
        self._answer("get", "/api/mi/foto", headers=cookie)
        saved = self._answer("post", "/api/mi/foto", headers=cookie, body={"data_b64": _png_b64()})
        self.assertTrue(saved["foto_url"])
        self._answer("get", "/api/mi/foto", headers=cookie)
        self._answer("delete", "/api/mi/foto", headers=cookie)


class TestPatientAccountAnswers(_AreaCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from app.cuentas import account_photo
        cls._photos_before = account_photo.PHOTOS_DIR
        cls._photos_tmp = tempfile.mkdtemp(prefix="response-models-photos-")
        account_photo.PHOTOS_DIR = cls._photos_tmp

    @classmethod
    def tearDownClass(cls):
        from app.cuentas import account_photo
        account_photo.PHOTOS_DIR = cls._photos_before
        shutil.rmtree(cls._photos_tmp, ignore_errors=True)
        super().tearDownClass()

    @answers(("get", "/api/account/config"))
    def test_config(self):
        self.assertEqual(self._answer("get", "/api/account/config")["auth_mode"], "password")

    @answers(("get", "/api/account/me"), ("get", "/api/account/dependents"),
             ("delete", "/api/account/photo"), ("post", "/api/account/logout"))
    def test_the_signed_in_patient(self):
        cookie = self._patient()
        status, _hdrs, raw = self._request("POST", "/api/account/dependents", headers=cookie, body={
            "dni": "40999611", "nombre": "Leo", "apellido": "Gomez", "nacimiento": "2015-05-05",
            "sexo": "M", "vinculo": "hijo", "declaracion_jurada": True})
        self.assertEqual(status, 200, raw)
        status, _hdrs, raw = self._request("POST", "/api/account/photo", headers=cookie,
                                           body={"data_b64": _png_b64()})
        self.assertEqual(status, 200, raw)
        self.assertEqual(self._answer("get", "/api/account/me", headers=cookie)["usuario"]["dni"], _DNI)
        self.assertEqual(len(self._answer("get", "/api/account/dependents", headers=cookie)["dependientes"]), 1)
        self._answer("delete", "/api/account/photo", headers=cookie)
        self._answer("post", "/api/account/logout", headers=cookie)

    @answers(("get", "/api/requests"), ("get", "/api/requests/{req_id}"),
             ("get", "/api/requests/{req_id}/coverage"), ("post", "/api/requests/{req_id}/cancel"))
    def test_my_requests(self):
        cookie = self._patient()
        status, _hdrs, raw = self._request("POST", "/api/requests", headers=cookie, body={
            "servicio_id": _SERVICIO, "centro_slug": _CENTRO, "contacto": "+542254000611",
            "preferencia": "manana", "caso": "control anual", "idem_key": "k-611"})
        self.assertEqual(status, 200, raw)
        pending = json.loads(raw)["id"]
        approved = self._approved()
        rows = self._answer("get", "/api/requests", headers=cookie)["solicitudes"]
        self.assertEqual({row["estado"] for row in rows}, {"pendiente", "aprobada"})
        for sid in (pending, approved):
            self._answer("get", "/api/requests/{req_id}", f"/api/requests/{sid}", headers=cookie)
            self._answer("get", "/api/requests/{req_id}/coverage", f"/api/requests/{sid}/coverage",
                         headers=cookie)
        cancelled = self._answer("post", "/api/requests/{req_id}/cancel",
                                 f"/api/requests/{pending}/cancel", headers=cookie)
        self.assertEqual(cancelled["solicitud"]["estado"], "cancelada")
        self._answer("get", "/api/requests", headers=cookie)

    @answers(("post", "/api/requests/{req_id}/coverage"), ("post", "/api/requests/{req_id}/reprogramar"),
             ("post", "/api/requests/{req_id}/waitlist"))
    def test_changing_a_request(self):
        from app.cuentas.health_coverage import revision
        cookie = self._patient()
        status, _hdrs, raw = self._request("POST", "/api/account/update", headers=cookie,
                                           body={"email": "marta@example.com"})
        self.assertEqual(status, 200, raw)  # the waitlist writes to the account's email
        approved = self._approved()
        changed = self._answer("post", "/api/requests/{req_id}/coverage", f"/api/requests/{approved}/coverage",
                               headers=cookie, body={"choice": "none", "revision": revision(None)})
        self.assertTrue(changed["sin_cobertura"])
        waiting = self._answer("post", "/api/requests/{req_id}/waitlist", f"/api/requests/{approved}/waitlist",
                               headers=cookie, body={"activo": True})
        self.assertTrue(waiting["activo"])
        # One open request per service is the patient's own limit; the desk's path has none.
        pending = db_booking.create_booking_request(_DNI, "Marta", "Gomez", "1980-05-10", None,
                                                    _SERVICIO, _CENTRO)
        moved = self._answer("post", "/api/requests/{req_id}/reprogramar", f"/api/requests/{pending}/reprogramar",
                             headers=cookie, body=dict(_first_slot(self), preferencia="tarde"))
        self.assertEqual(moved["solicitud"]["id"], pending)

    @answers(("post", "/api/account/otp/verify"), ("post", "/api/account/reset"),
             ("post", "/api/account/reset_otp"))
    def test_recovering_the_password(self):
        from app.cuentas import accounts
        self._patient()
        contacto = accounts._norm_phone("+542254000611")
        code = db_accounts.create_otp(contacto, "registro")
        self.assertTrue(self._answer("post", "/api/account/otp/verify", body={
            "contacto": contacto, "proposito": "registro", "code": code})["ok"])
        token = db_accounts.create_reset_token(_DNI)
        self._answer("post", "/api/account/reset", body={"token": token, "password": "clave-nueva-611"})
        code = db_accounts.create_otp(db_accounts.get_user(_DNI)["contacto"], "recuperar")
        self._answer("post", "/api/account/reset_otp", body={
            "dni": _DNI, "code": code, "password": "clave-nueva-612"})

    @answers(("get", "/api/calendar/feed"), ("post", "/api/calendar/feed/regenerate"))
    def test_calendar_feed(self):
        cookie = self._patient()
        self.assertTrue(self._answer("get", "/api/calendar/feed", headers=cookie)["url"])
        self.assertIsNone(self._answer("get", "/api/calendar/feed", headers=cookie)["url"])
        self.assertTrue(self._answer("post", "/api/calendar/feed/regenerate", headers=cookie)["url"])

    @answers(("post", "/api/confirm/{token}/{answer}"))
    def test_will_you_come(self):
        self._patient()
        token = db_booking.booking_request_confirm_token(self._approved())
        answer = self._answer("post", "/api/confirm/{token}/{answer}", f"/api/confirm/{token}/si")
        self.assertEqual(answer["confirmacion"], "si")

    @answers(("post", "/api/offer/{token}/accept"), ("post", "/api/offer/{token}/reject"))
    def test_a_waitlist_offer(self):
        from app.modules.turnos import waitlist_store
        self._patient()
        turno = _in_days(9)
        for token, days in (("offer-611-a", 3), ("offer-611-b", 4)):
            sid = self._approved(days=9)
            entry = waitlist_store.opt_in(sid, _DNI, None, _SERVICIO, _CENTRO, turno)
            now = datetime.now(timezone.utc)
            waitlist_store.create_offer(_in_days(days), _SERVICIO, _CENTRO, entry, token,
                                        now, now + timedelta(hours=2))
        taken = self._answer("post", "/api/offer/{token}/accept", "/api/offer/offer-611-a/accept")
        self.assertIsNotNone(taken["solicitud_id"])
        self._answer("post", "/api/offer/{token}/reject", "/api/offer/offer-611-b/reject")


class TestTestIdentityAnswers(_AreaCase):
    """The synthetic sign-in of a development host (loopback peer, stub provider)."""

    @classmethod
    def setUpClass(cls):
        cls._identities = tempfile.mkdtemp(prefix="response-models-identities-")
        _env.set_env(cls, TURNOS_AUTH_MODE="renaper", TURNOS_RENAPER_PROVIDER="stub",
                     TURNOS_TEST_IDENTITIES_PATH=os.path.join(cls._identities, "identities.json"))
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls._identities, ignore_errors=True)


    @answers(("get", "/api/account/test-identities"), ("post", "/api/account/test-identities/random"),
             ("post", "/api/account/test-identities"), ("post", "/api/account/test-login"))
    def test_test_identities(self):
        from app.core import proxy_trust
        # Behind the app's own proxy the peer is the signed one: a loopback client.
        local = {proxy_trust.HEADER: proxy_trust.sign("127.0.0.1")}
        identity = self._answer("post", "/api/account/test-identities/random", headers=local)["identidad"]
        listed = self._answer("get", "/api/account/test-identities", headers=local)["identidades"]
        self.assertIn(identity["dni"], [item.get("dni") for item in listed])
        self._answer("post", "/api/account/test-identities", headers=local, body={
            "identidad": dict(identity, nombre="Otra"), "original_dni": identity["dni"]})
        self._answer("post", "/api/account/test-login", headers=local, body={
            "provider": "renaper", "dni": identity["dni"], "sexo": identity.get("sexo")})


class TestStudyAnswers(_AreaCase):
    """The usability study runs on a demo installation only."""

    @classmethod
    def setUpClass(cls):
        _env.set_env(cls, TURNOS_STUDY="1", TURNOS_DEMO_MODE="1")
        super().setUpClass()

    @answers(("post", "/api/study/session"), ("post", "/api/study/s/{token}/event"),
             ("post", "/api/study/s/{token}/result"), ("post", "/api/study/s/{token}/finish"))
    def test_a_study_session(self):
        from app.modules.turnos import study
        role = sorted(study.ROLES)[0]
        url = self._answer("post", "/api/study/session", body={"consent": True, "role": role})["url"]
        token = url.rsplit("/", 1)[-1]
        task = sorted(study.task_ids(role))[0]
        self._answer("post", "/api/study/s/{token}/event", f"/api/study/s/{token}/event", body={
            "events": [{"task_id": task, "t": 10, "kind": sorted(study.KINDS)[0],
                        "target": {"tag": "a"}, "path": "/"}]})
        self._answer("post", "/api/study/s/{token}/result", f"/api/study/s/{token}/result", body={
            "task_id": task, "outcome": sorted(study.OUTCOMES)[0], "ease": 5, "comment": "",
            "duration_ms": 1000})
        self._answer("post", "/api/study/s/{token}/finish", f"/api/study/s/{token}/finish",
                     body={"answers": {}})


if __name__ == "__main__":
    unittest.main()
