"""tests/routes/test_ai_response_models.py — every operation of the AI part of
area (c) (`tests/_insight_area.py`, part "ai": the `ai` package's lab results,
chart summary, AI costs, AI status and prompts, and the staff panel's AI
register and usability study) answered by the live app, and the answer
checked against the model the OpenAPI schema names for it (TASK-76, OpenAPI
stage D, area c): the success status the schema documents, a JSON body that
matches the model, and no top-level key the model does not name.

The AI is the repo's fake (`TURNOS_FAKE_AI=1`, set by the routes fixture): no
call leaves the process. Lab uploads, cost rows, prompt edits, register
changes and study sessions are made through the app's own functions or
routes on a freshly truncated database.
`test_every_operation_of_the_part_is_answered_here` keeps the list complete:
a new operation of the part needs its answer here.
"""

import io
import os
import sys
import unittest
import uuid

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TEST_DIR)
for _p in (_ROOT, _HERE, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _env  # noqa: E402
import _routes_fixture  # noqa: E402
from _routes_fixture import RouteTestCase, admin_cookie  # noqa: E402
import _insight_area as area  # noqa: E402

from app.core import db_accounts  # noqa: E402

_DNI = "30999711"
_MEDICO_ID = "gonzalez-juan"

# (method, path template) -> the test that answers it.
_ANSWERED = {}


def answers(*operations):
    def mark(fn):
        for operation in operations:
            _ANSWERED[operation] = fn.__name__
        return fn
    return mark


def _png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (30, 120, 200)).save(buf, format="PNG")
    return buf.getvalue()


def _multipart(field, filename, content_type, data):
    boundary = "oss3c-" + uuid.uuid4().hex
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
            f"filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n").encode("ascii")
    return head + data + f"\r\n--{boundary}--\r\n".encode("ascii"), boundary


class _AiCase(area.AnswerChecks, RouteTestCase):
    part = "ai"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.load_documented()

    def _request(self, method, path, headers=None, body=None):
        """A bytes body goes as it is (a multipart upload); anything else as JSON."""
        if not isinstance(body, bytes):
            return super()._request(method, path, headers=headers, body=body)
        status, hdrs, raw = _routes_fixture._http_call(
            self._base_url, method, path, data=body, headers=dict(headers or {}))
        return status, {k.title(): v for k, v in dict(hdrs or {}).items()}, raw

    def _patient(self, dni=_DNI):
        db_accounts.create_user(dni, "Marta", "Gomez", "1980-05-10", "F", "+542254000711",
                                "throwaway-12345")
        return {"Cookie": "st_ses=" + db_accounts.create_session(dni)}


class TestAiAnswers(_AiCase):

    def test_every_operation_of_the_part_is_answered_here(self):
        self.assert_every_operation_answered(_ANSWERED)

    @answers(("get", "/api/ai-status"))
    def test_ai_status(self):
        self._answer("get", "/api/ai-status")

    @answers(("get", "/api/admin/costs"))
    def test_costs(self):
        from app.ai import ai_costs
        ai_costs.record("orden", cost_usd=0.0021, ip="203.0.113.7", model="m-1", recognized=True)
        ai_costs.record("orden", cost_usd=0.0019, ip="203.0.113.8", model="m-1", recognized=False)
        ai_costs.record("chat", cost_usd=0.0004, ip="203.0.113.7", model="m-1")
        out = self._answer("get", "/api/admin/costs", "/api/admin/costs?days=30", headers=admin_cookie())
        self.assertGreater(out["resumen"]["operaciones"], 0)

    @answers(("get", "/api/admin/patient-summary"), ("post", "/api/admin/patient-summary"))
    def test_patient_summary(self):
        self._patient()
        got = self._answer("get", "/api/admin/patient-summary",
                           f"/api/admin/patient-summary?dni={_DNI}&medico={_MEDICO_ID}",
                           headers=admin_cookie())
        self.assertIsNotNone(got["generado"])
        self._answer("post", "/api/admin/patient-summary", headers=admin_cookie(),
                     body={"dni": _DNI, "medico": _MEDICO_ID})

    @answers(("get", "/api/account/lab-results"), ("post", "/api/account/lab-results/upload"),
             ("delete", "/api/account/lab-results/upload/{upload_id}"))
    def test_lab_results(self):
        cookie = self._patient()
        data, boundary = _multipart("archivo", "hemograma.png", "image/png", _png())
        up = self._answer("post", "/api/account/lab-results/upload", body=data, headers=dict(
            cookie, **{"Content-Type": f"multipart/form-data; boundary={boundary}"}))
        listed = self._answer("get", "/api/account/lab-results", headers=cookie)
        self.assertTrue(any(r.get("upload_id") == up["id"] for r in listed["informes"]), listed)
        self._answer("delete", "/api/account/lab-results/upload/{upload_id}",
                     f"/api/account/lab-results/upload/{up['id']}", headers=cookie)

    @answers(("get", "/api/admin/prompts"), ("get", "/api/admin/prompts/{clave}"),
             ("get", "/api/admin/prompts/{clave}/traduccion"),
             ("post", "/api/admin/prompts/{clave}/guardar"),
             ("get", "/api/admin/prompts/{clave}/historial"),
             ("delete", "/api/admin/prompts/{clave}"))
    def test_prompts(self):
        admin = admin_cookie()
        clave = self._answer("get", "/api/admin/prompts", headers=admin)["items"][0]["clave"]
        base = f"/api/admin/prompts/{clave}"
        self._answer("get", "/api/admin/prompts/{clave}", base, headers=admin)
        es = self._answer("get", "/api/admin/prompts/{clave}/traduccion", base + "/traduccion",
                          headers=admin)["texto_es"]
        saved = self._answer("post", "/api/admin/prompts/{clave}/guardar", base + "/guardar",
                             headers=admin, body={"texto_es": es + "\n\nRespondé con frases cortas."})
        self.assertTrue(saved["cambios"], saved)
        # The history keeps the versions an edit replaced: a second edit, of the English.
        self._answer("post", "/api/admin/prompts/{clave}/guardar", base + "/guardar", headers=admin,
                     body={"texto_en": saved["item"]["texto_en"] + "\n\nAnswer briefly."})
        history = self._answer("get", "/api/admin/prompts/{clave}/historial", base + "/historial",
                               headers=admin)
        self.assertTrue(history["items"])
        self._answer("delete", "/api/admin/prompts/{clave}", base, headers=admin)


class TestAiRegisterAnswers(_AiCase):

    @answers(("get", "/api/admin/ai-register"), ("get", "/api/admin/ai-register/export"),
             ("post", "/api/admin/ai-register/notify"))
    def test_ai_register(self):
        from app.ai import prompts
        from app.core import ai_register
        admin = admin_cookie()
        ai_register.observe_inventory(ai_register.inventory())
        # A prompt edit is a substantial change waiting for its notification.
        key = prompts.REGISTRY[0]["clave"]
        status, _h, raw = self._request("POST", f"/api/admin/prompts/{key}/guardar", headers=admin,
                                        body={"texto_en": prompts._BY_KEY[key]["instrucciones_en"]
                                              + "\n\nAnswer briefly."})
        self.assertEqual(status, 200, raw)
        got = self._answer("get", "/api/admin/ai-register", headers=admin)
        pending = [c for c in got["modificaciones"] if c.get("status") == "pending" and c.get("substantial")]
        self.assertTrue(pending, got["modificaciones"])
        self._answer("get", "/api/admin/ai-register/export", headers=admin)
        self._answer("post", "/api/admin/ai-register/notify", headers=admin, body={
            "id": pending[0]["id"], "reference": "EX-2026-0001", "date": "2026-01-15"})


class TestUsabilityAnswers(_AiCase):
    """The usability study runs on a demo installation only."""

    @classmethod
    def setUpClass(cls):
        _env.set_env(cls, TURNOS_STUDY="1", TURNOS_DEMO_MODE="1")
        super().setUpClass()

    @answers(("get", "/api/admin/usability"), ("post", "/api/admin/usability/analyze"),
             ("post", "/api/admin/usability/findings"),
             ("post", "/api/admin/usability/findings/{finding_id}"))
    def test_usability(self):
        from app.auth import admin_auth
        from app.modules.turnos import study
        owner = admin_cookie(admin_auth.owner_usuario())
        role = sorted(study.ROLES)[0]
        task = sorted(study.task_ids(role))[0]
        status, _h, raw = self._request("POST", "/api/study/session", body={"consent": True, "role": role})
        self.assertEqual(status, 201, raw)
        self._answer("post", "/api/admin/usability/analyze", headers=owner, body={})
        created = self._answer("post", "/api/admin/usability/findings", headers=owner, body={
            "role": role, "task_id": task, "severity": "high", "title": "The button is hard to find"})
        self._answer("post", "/api/admin/usability/findings/{finding_id}",
                     f"/api/admin/usability/findings/{created['id']}", headers=owner,
                     body={"status": sorted(study.STATUSES)[0]})
        report = self._answer("get", "/api/admin/usability", headers=owner)
        self.assertTrue(report["sessions"] and report["findings"], report)


if __name__ == "__main__":
    unittest.main()
