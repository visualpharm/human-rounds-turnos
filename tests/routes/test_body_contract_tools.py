"""tests/routes/test_body_contract_tools.py — the JSON routes that got their
body model last (TASK-76, item C): the panel's tools (browser error reports,
the table export, the «Recorrido» steps, the AI register, the usability
findings, a doctor's photo, the chart summary), the booking chat and its
voice dictation, and the usability study. Every answer to the body battery
of `_body_contract.py`, pinned against `tests/golden/body_contract_tools.json`.

The AI runs on its fake; the study is switched on for its class only. The
doctor's photo is pinned with an `action` on a portrait that does not exist,
in an empty photo folder of the test's own, so no case writes an image.
"""

import base64
import os
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TEST_DIR)
for _p in (_ROOT, _HERE, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _routes_fixture import RouteTestCase, admin_cookie  # noqa: E402
import _body_contract as bc  # noqa: E402

from app.admin_panel import api_models as p  # noqa: E402
from app.ai import api_models as ai_models  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.modules.chat import api_models as chat_models  # noqa: E402
from app.modules.turnos import api_models as t  # noqa: E402
from app.patient_web import api_models as w  # noqa: E402

_USUARIO = "tools.pin"
_DNI = "30999311"
_MEDICO_ID = "gonzalez-juan"
_GOLDEN = bc.Golden("tools")


class _Pinned(RouteTestCase):

    @classmethod
    def tearDownClass(cls):
        if bc.WRITE:
            _GOLDEN.save()
        super().tearDownClass()

    def _clean(self):
        from app.admin_panel import client_errors_router
        from app.ai import abuse_guard
        from app.modules.chat import chat_api, voice
        self.db.truncate_all()
        client_errors_router._recent.clear()
        for hits in (abuse_guard._HITS, chat_api._HITS, voice._HITS):
            hits.clear()

    def _anonymous(self):
        self._clean()
        return {}

    def _staff(self):
        self._clean()
        db_operators.operator_create(_USUARIO, password="clave-tools-12", rol="admin")
        return admin_cookie(usuario=_USUARIO, rol="admin")

    def _pin(self, path, valid, reset, session=False, docs=None, key=None, refuses=False):
        # Only the chart summary reads its body with a family contract; every other route here answers each
        # body itself and its model only observes (`api_body.watch`/`watched`, `raw(None, …)`).
        bc.pin_route(self, _GOLDEN, "POST", path, valid, reset=reset,
                     auth=True if session else None, docs=docs, key=key, refuses=refuses)


class TestPanelToolsBodyContract(_Pinned):

    def test_no_body_crashes_a_route(self):
        """The chart summary answered a body that is not a JSON object with a bare 500; since its body goes
        through its model it is the family's 400. The golden file holds no 500 at all."""
        crashes = [(route, case) for route, cases in _GOLDEN.sections.items()
                   for case, got in cases.items() if got["status"] == 500]
        self.assertEqual(crashes, [])

    def test_client_errors(self):
        self._pin("/api/admin/client-errors",
                  {"kind": "error", "message": "x is undefined", "source": "/static/platform/x.mjs",
                   "line": 12, "col": 3, "stack": "", "screen": "/admin/cola"},
                  self._staff, session=True, docs=(p.ClientErrorIn, None))

    def test_table_export(self):
        self._pin("/api/admin/table-export",
                  {"screen": "/admin/doctors", "title": "Médicos", "day": "2026-11-02",
                   "columns": ["Nombre", "Especialidad"], "rows": [["Juan", "Clínica"]], "patients": []},
                  self._staff, session=True, docs=(p.TableExportIn, None))

    def test_funnel_steps(self):
        self._pin("/api/admin/reports/funnel/steps",
                  {"steps": [{"key": "pedido", "on": True}, {"key": "aprobada", "on": True}]},
                  self._staff, session=True, docs=(p.FunnelStepsIn, None))

    def test_ai_register_notify(self):
        self._pin("/api/admin/ai-register/notify",
                  {"id": 1, "reference": "NOTA-1", "date": "2026-11-02"},
                  self._staff, session=True, docs=(p.AiRegisterNotifyIn, None))

    def test_doctor_photo(self):
        # An empty photo folder of its own: the doctor has no portrait, so `restore` finds nothing to
        # restore, whatever a checkout's `data/` holds, and no case writes there.
        import tempfile
        from app.patient_web import doctor_photos
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(doctor_photos, "PHOTOS_DIR", folder):
            self._pin(f"/api/admin/doctors/{_MEDICO_ID}/photo", {"action": "restore"},
                      self._staff, session=True, docs=(w.PhotoUploadIn, None))

    def test_patient_summary(self):
        self._pin("/api/admin/patient-summary", {"dni": _DNI, "medico": _MEDICO_ID},
                  self._staff, session=True, docs=(ai_models.PatientSummaryIn, None), refuses=True)


class TestChatBodyContract(_Pinned):

    def test_chat(self):
        self._pin("/api/chat", {"mensaje": "hola", "session_id": "pin-1"},
                  self._anonymous, docs=(chat_models.ChatMessageIn, None))

    def test_chat_stream(self):
        self._pin("/api/chat/stream", {"mensaje": "hola", "session_id": "pin-2"},
                  self._anonymous, docs=(chat_models.ChatMessageIn, None))

    def test_transcribe(self):
        audio = base64.b64encode(b"\x1aE\xdf\xa3 fake webm").decode("ascii")
        self._pin("/api/chat/transcribe", {"audio": {"mime": "audio/webm", "data_b64": audio}},
                  self._anonymous, docs=(chat_models.VoiceIn, None))


class TestStudyBodyContract(_Pinned):

    @classmethod
    def setUpClass(cls):
        cls._study = mock.patch("app.modules.turnos.study.enabled", return_value=True)
        cls._study.start()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._study.stop()

    def _participant(self):
        from app.modules.turnos import study_store
        self._clean()
        self._token = study_store.create_session("patient", "es", True, "desktop")
        return {}

    def _owner(self):
        from app.auth import admin_auth
        self._clean()
        return admin_cookie(usuario=admin_auth.owner_usuario(), rol="admin")

    def _study_path(self, tail):
        return lambda: f"/api/study/s/{self._token}/{tail}"

    def test_session(self):
        self._pin("/api/study/session", {"consent": True, "role": "patient"},
                  self._anonymous, docs=(t.StudySessionIn, None))

    def test_event(self):
        self._pin(self._study_path("event"),
                  {"events": [{"task_id": "P1", "kind": "click", "t": 10, "path": "/",
                               "target": {"tag": "a", "interactive": True, "index": 0}}]},
                  self._participant, docs=(t.StudyEventsIn, None), key="POST /api/study/s/{token}/event")

    def test_result(self):
        self._pin(self._study_path("result"),
                  {"task_id": "P1", "outcome": "ok", "ease": 4, "comment": "", "duration_ms": 1000},
                  self._participant, docs=(t.StudyResultIn, None), key="POST /api/study/s/{token}/result")

    def test_finish(self):
        self._pin(self._study_path("finish"), {"answers": {"F1": "", "F2": "", "F3": "yes"}},
                  self._participant, docs=(t.StudyFinishIn, None), key="POST /api/study/s/{token}/finish")

    def test_usability_analyze(self):
        self._pin("/api/admin/usability/analyze", {}, self._owner, session=True,
                  docs=(p.UsabilityAnalyzeIn, None))

    def test_usability_finding(self):
        self._pin("/api/admin/usability/findings",
                  {"role": "patient", "task_id": "P1", "severity": "medium", "title": "Botón escondido",
                   "detail": "No se ve el botón de pedir turno."},
                  self._owner, session=True, docs=(p.UsabilityFindingIn, None))

    def test_usability_finding_update(self):
        self._pin("/api/admin/usability/findings/1", {"status": "fixed", "fix_note": "Botón visible."},
                  self._owner, session=True, docs=(p.UsabilityFindingUpdateIn, None))


if __name__ == "__main__":
    unittest.main()
