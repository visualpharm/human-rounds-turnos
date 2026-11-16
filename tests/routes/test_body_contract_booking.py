"""tests/routes/test_body_contract_booking.py — the patient's booking JSON
routes (`modules/turnos/booking_requests_router.py`, `slot_holds_router.py`,
`searches_router.py`, `connectors/puco/puco_router.py`,
`cuentas/ocr_dni_router.py`, `modules/intake/interview_patient_router.py`):
every answer to the body battery of `_body_contract.py`, pinned against
`tests/golden/body_contract_booking.json` (TASK-24, Pydantic stage 3).

The services and the doctor are the fictional public example-town pack's;
the AI, the insurance registry and the document OCR run on their fakes.
"""

import base64
import io
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TEST_DIR)
for _p in (_ROOT, _HERE, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _env  # noqa: E402
from _routes_fixture import RouteTestCase  # noqa: E402
import _body_contract as bc  # noqa: E402

from app.core import db_accounts  # noqa: E402
from app.connectors.puco import puco_router  # noqa: E402
from app.cuentas import api_models as c  # noqa: E402
from app.cuentas.health_coverage import revision  # noqa: E402
from app.modules.intake import api_models as i  # noqa: E402
from app.modules.turnos import api_models as t  # noqa: E402

_DNI = "30999211"
_SERVICIO = "medicina-general-riverside-clinic"
_CENTRO = "riverside-clinic"
_MEDICO_ID = "gonzalez-juan"
_GOLDEN = bc.Golden("booking")
_ENV = {"TURNOS_FAKE_PUCO": "1"}


def _png_b64():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (240, 240, 240)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


_REQUEST = {"servicio_id": _SERVICIO, "centro_slug": _CENTRO, "contacto": "+542254000211",
            "preferencia": "mañana", "caso": "control anual", "idem_key": "k-1"}


class TestBookingBodyContract(RouteTestCase):

    @classmethod
    def setUpClass(cls):
        _env.set_env(cls, _ENV)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        if bc.WRITE:
            _GOLDEN.save()
        super().tearDownClass()

    def _clean(self):
        from app.ai import abuse_guard, ai_cooldown
        from app.connectors.puco import puco
        from app.modules.intake import interview_patient
        from app.modules.turnos import searches, slot_holds
        self.db.truncate_all()
        for hits in (abuse_guard._HITS, puco._HITS, slot_holds._HITS, searches._HITS,
                     interview_patient._HITS):
            hits.clear()
        ai_cooldown._reset_para_tests()
        slot_holds.invalidate()

    def _anonymous(self):
        self._clean()
        return {}

    def _session(self):
        self._clean()
        db_accounts.create_user(_DNI, "Marta", "Gómez", "1980-05-10", "F", "+542254000211",
                       "throwaway-12345")
        return {"Cookie": "st_ses=" + db_accounts.create_session(_DNI)}

    def _session_with_request(self):
        import json
        cookie = self._session()
        status, _, raw = bc.request(self._base_url, "POST", "/api/requests",
                                    json.dumps(_REQUEST).encode("utf-8"), cookie)
        assert status == 200 and json.loads(raw)["id"] == 1, raw
        return cookie

    def _door(self):
        self._clean()
        from app.modules.intake import intake_admin
        token, doc = intake_admin.get_or_create_door_token(_MEDICO_ID)
        intake_admin._write_override(_MEDICO_ID, doc, True)
        self._door_token = token
        return {}

    def _interview(self):
        import json
        self._door()
        status, _, raw = bc.request(self._base_url, "POST",
                                    f"/api/intake/qr/{self._door_token}/start",
                                    json.dumps({"dni": "30999881"}).encode("utf-8"), {})
        assert status == 200, raw
        self._patient_token = json.loads(raw)["patient_token"]
        return {}

    def _pin(self, path, valid, reset, session=False, docs=None, key=None):
        bc.pin_route(self, _GOLDEN, "POST", path, valid, reset=reset,
                     auth=True if session else None, docs=docs, key=key)

    def test_no_body_crashes_a_route(self):
        """Since stage 3 a body the pages never send is a 400 of the family, not
        a 500: the golden file holds no 500 at all."""
        crashes = [(route, case) for route, cases in _GOLDEN.sections.items()
                   for case, got in cases.items() if got["status"] == 500]
        self.assertEqual(crashes, [])

    def test_create_request(self):
        self._pin("/api/requests", _REQUEST, self._session, session=True,
                  docs=(t.BookingRequestIn, t.BookingRequestOut))

    def test_request_coverage(self):
        self._pin("/api/requests/1/coverage",
                  {"choice": "coverage", "revision": revision(None),
                   "cobertura": {"obras_sociales": [{"nombre": "IOMA", "afiliado": "1"}]}},
                  self._session_with_request, session=True,
                  docs=(t.CoverageChoiceIn, t.CoverageChoiceOut))

    def test_request_reschedule(self):
        self._pin("/api/requests/1/reprogramar",
                  {"dia": "2030-01-07", "hora": "10:00", "preferencia": "tarde"},
                  self._session_with_request, session=True,
                  docs=(t.RescheduleIn, t.RescheduleOut))

    def test_request_waitlist(self):
        self._pin("/api/requests/1/waitlist", {"activo": True},
                  self._session_with_request, session=True,
                  docs=(t.WaitlistIn, t.WaitlistOut))

    def test_analyze_order(self):
        self._pin("/api/analyze-order",
                  {"servicio_id": _SERVICIO, "especialidad": "Clínica médica",
                   "orden": {"filename": "orden.png", "mime": "image/png",
                             "data_b64": _png_b64()},
                   "turnstile_token": ""},
                  self._anonymous,
                  docs=(t.AnalyzeOrderIn, t.AnalyzeOrderOut))

    def test_slot_hold(self):
        self._pin("/api/slot-hold",
                  {"servicio_id": _SERVICIO, "dia": "2030-01-07", "hora": "10:00",
                   "token": "hold-1"},
                  self._anonymous,
                  docs=(t.SlotHoldIn, t.SlotHoldOut))

    def test_search_log(self):
        self._pin("/api/search-log", {"q": "clinica", "resultados": 3}, self._anonymous,
                  docs=(t.SearchLogIn, None))

    def test_puco(self):
        self._pin("/api/puco", {"dni": "30999211"}, self._anonymous,
                  docs=(puco_router.PucoIn, puco_router.PucoOut))

    def test_ocr_dni(self):
        self._pin("/api/ocr-dni",
                  {"lado": "frente", "imageDataUrl": "data:image/png;base64," + _png_b64(),
                   "turnstile_token": ""},
                  self._anonymous,
                  docs=(c.OcrDniIn, c.OcrDniOut))

    def test_intake_qr_start(self):
        self._pin(lambda: f"/api/intake/qr/{self._door_token}/start", {"dni": "30999881"},
                  self._door, key="POST /api/intake/qr/{code}/start",
                  docs=(i.QrStartIn, i.QrStartOut))

    def test_intake_message(self):
        self._pin(lambda: f"/api/intake/t/{self._patient_token}/message",
                  {"texto": "me duele la cabeza"},
                  self._interview, key="POST /api/intake/t/{token}/message",
                  docs=(i.InterviewMessageIn, i.InterviewMessageOut))


if __name__ == "__main__":
    unittest.main()
