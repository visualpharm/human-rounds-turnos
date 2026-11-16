"""tests/routes/test_intake_response_models.py — every operation of the
pre-visit interview (`tests/_insight_area.py`, part "intake") answered by the
live app, and the answer checked against the model the OpenAPI schema names
for it (TASK-76, OpenAPI stage D, area c): the success status the schema
documents, a JSON body that matches the model, and no top-level key the model
does not name.

The doctor is the fictional public example-town pack's, with the interview
switched on through the doctor-settings override; interviews, red alerts and
a confirmed consultation are made through the app's own functions on a freshly
truncated database, the interviewer is the fake AI (`TURNOS_FAKE_AI=1`, set by
the routes fixture). `test_every_operation_of_the_part_is_answered_here` keeps
the list complete: a new operation of the part needs its answer here.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TEST_DIR)
for _p in (_ROOT, _HERE, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _routes_fixture import RouteTestCase, admin_cookie  # noqa: E402
import _insight_area as area  # noqa: E402

from app.core import db_clinical, db_operators  # noqa: E402

_MEDICO_ID = "gonzalez-juan"
_DNI = "30999721"

# (method, path template) -> the test that answers it.
_ANSWERED = {}


def answers(*operations):
    def mark(fn):
        for operation in operations:
            _ANSWERED[operation] = fn.__name__
        return fn
    return mark


def _door_on(medico_id=_MEDICO_ID):
    """The doctor's door token, with the interview switched on for them."""
    from app.modules.intake import intake_admin
    token, doc = intake_admin.get_or_create_door_token(medico_id)
    intake_admin._write_override(medico_id, doc, True)
    return token


def _red_interview(doctor_id=_MEDICO_ID, dni=_DNI):
    fila = db_clinical.interview_create(doctor_id=doctor_id, entry_mode="qr", dni=dni)
    db_clinical.interview_update_analysis(fila["id"], "ROJO", "Dolor de pecho agudo",
                                          {}, [], [], [], "fake-model")
    db_clinical.interview_set_status(fila["id"], "done")
    return fila["id"]


def _doctor_cookie(usuario="dr.gonzalez"):
    db_operators.operator_create(usuario, "clave-larga-1234", "medico", medico_id=_MEDICO_ID)
    return admin_cookie(usuario, "medico")


class TestIntakeAnswers(area.AnswerChecks, RouteTestCase):

    part = "intake"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.load_documented()

    def setUp(self):
        super().setUp()
        from app.core import config
        # Settings are cached for a few seconds: the class before this one read its own.
        config._reset_cache_para_tests()
        self.addCleanup(config._reset_cache_para_tests)

    def test_every_operation_of_the_part_is_answered_here(self):
        self.assert_every_operation_answered(_ANSWERED)

    @answers(("post", "/api/intake/qr/{code}/start"), ("get", "/api/intake/t/{token}"),
             ("post", "/api/intake/t/{token}/message"), ("post", "/api/intake/t/{token}/reopen"))
    def test_patient_interview(self):
        door = _door_on()
        started = self._answer("post", "/api/intake/qr/{code}/start", f"/api/intake/qr/{door}/start",
                               body={"dni": _DNI})
        token = started["patient_token"]
        transcript = self._answer("get", "/api/intake/t/{token}", f"/api/intake/t/{token}")
        self.assertEqual(transcript["status"], "active")
        self.assertTrue(transcript["mensajes"])
        turn = self._answer("post", "/api/intake/t/{token}/message", f"/api/intake/t/{token}/message",
                            body={"texto": "Me duele la cabeza desde ayer"})
        self.assertTrue(turn["texto"])
        db_clinical.interview_set_status(db_clinical.interview_by_patient_token(token)["id"], "done")
        reopened = self._answer("post", "/api/intake/t/{token}/reopen", f"/api/intake/t/{token}/reopen")
        self.assertEqual(reopened["status"], "active")
        self.assertTrue(any(line["role"] == "user" for line in reopened["mensajes"]))

    @answers(("post", "/api/intake/case/start"), ("post", "/api/intake/case/{token}/message"),
             ("post", "/api/intake/case/{token}/finish"))
    def test_case_chat(self):
        started = self._answer("post", "/api/intake/case/start")
        self.assertTrue(started["disponible"])
        token = started["token"]
        self._answer("post", "/api/intake/case/{token}/message", f"/api/intake/case/{token}/message",
                     body={"texto": "Necesito un control de presion"})
        done = self._answer("post", "/api/intake/case/{token}/finish", f"/api/intake/case/{token}/finish")
        self.assertEqual(done, {"ok": True})

    @answers(("get", "/api/admin/intake/settings"), ("post", "/api/admin/intake/settings"))
    def test_settings(self):
        _door_on()
        _doctor_cookie()
        admin = admin_cookie()
        settings = self._answer("get", "/api/admin/intake/settings", headers=admin)
        self.assertTrue(settings["medicos"])
        row = next(m for m in settings["medicos"] if m["id"] == _MEDICO_ID)
        self.assertEqual(row["cuenta"], "dr.gonzalez")
        path = "/api/admin/intake/settings"
        for body in ({"sistema": True},
                     {"medico_ids": [_MEDICO_ID, "no-such-doctor"], "enabled": True},
                     {"medico_id": _MEDICO_ID, "columnas": ["confirmo", "confirmo", "cobertura"]},
                     {"medico_id": _MEDICO_ID, "override": None}):
            with self.subTest(body=body):
                saved = self._answer("post", path, headers=admin, body=body)
                self.assertTrue(saved["ok"])
        saved = self._answer("post", path, headers=admin,
                             body={"medico_ids": [_MEDICO_ID, "no-such-doctor"], "enabled": False})
        self.assertEqual(saved["errores"], 1)

    @answers(("get", "/api/admin/intake/my-settings"), ("post", "/api/admin/intake/my-settings"),
             ("post", "/api/admin/intake/door/{code}/regenerate"))
    def test_my_settings(self):
        _door_on()
        doctor = _doctor_cookie()
        mine = self._answer("get", "/api/admin/intake/my-settings", headers=doctor)
        self.assertTrue(mine["efectivo"])
        self._answer("post", "/api/admin/intake/my-settings", headers=doctor, body={"override": False})
        mine = self._answer("get", "/api/admin/intake/my-settings", headers=doctor)
        self.assertIsNone(mine["pdf_url"])
        link = self._answer("post", "/api/admin/intake/door/{code}/regenerate",
                            f"/api/admin/intake/door/{_MEDICO_ID}/regenerate", headers=doctor)
        self.assertTrue(link["url"].endswith(_MEDICO_ID))

    @answers(("get", "/api/admin/intake/alerts/count"), ("get", "/api/admin/intake/alerts"),
             ("post", "/api/admin/intake/alerts/{aid}/ack"))
    def test_red_alerts(self):
        aid = _red_interview()
        _red_interview(doctor_id=None, dni="30999722")
        admin = admin_cookie()
        count = self._answer("get", "/api/admin/intake/alerts/count", headers=admin)
        self.assertEqual(count["count"], 2)
        listed = self._answer("get", "/api/admin/intake/alerts", headers=admin)
        self.assertEqual(len(listed["items"]), 2)
        self._answer("post", "/api/admin/intake/alerts/{aid}/ack", f"/api/admin/intake/alerts/{aid}/ack",
                     headers=admin)

    @answers(("get", "/api/admin/intake/consult/{cid}"), ("post", "/api/admin/intake/consult/{cid}/confirm"))
    def test_consult(self):
        iid = _red_interview()
        admin = admin_cookie()
        path = f"/api/admin/intake/consult/{iid}"
        consult = self._answer("get", "/api/admin/intake/consult/{cid}", path, headers=admin)
        self.assertIsNone(consult["confirmada"])
        body = {"notas": "Control en una semana",
                "patient_reviewed": {"nombre": consult["nombre"], "dni": consult["dni"] or ""},
                "choices_reviewed": {"diagnosticos": [], "estudios": [], "medicacion": [],
                                     "notas": "Control en una semana"}}
        confirmed = self._answer("post", "/api/admin/intake/consult/{cid}/confirm", path + "/confirm",
                                 headers=admin, body=body)
        self.assertTrue(confirmed["receta_url"].startswith("/r/"))
        consult = self._answer("get", "/api/admin/intake/consult/{cid}", path, headers=admin)
        self.assertEqual(consult["confirmada"]["receta_url"], confirmed["receta_url"])


if __name__ == "__main__":
    unittest.main()
