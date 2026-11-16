"""tests/routes/test_intake_door_token.py — routes-tier coverage: the
entrevista previa's QR door-entry link, GET /e/<door_token> and POST
/api/intake/qr/<door_token>/start (`modules/intake/interview_patient.py`).
Distinct from the already-covered e2e QR test
(tests/e2e/intake.spec.mjs — see docs/roadmap/tier3-harness-design.md, Area
1's note that "intake QR" is already satisfied there): that one drives a
real browser against one seeded token; this one is the HTTP-level contract
— an unknown token's 404, and the durable effect of a real anonymous
DNI-backed start (`db_clinical.interview_create`, a real `interviews` row readable
back by patient_token, never a hand-built one).

`intake_admin.get_or_create_door_token` is the same function used by the E2E
fixture. This test selects a fictional doctor from the public example-town
pack, then explicitly enables the interview through the same override path
as the doctor-settings toggle. It therefore does not depend on a pack's
current rollout setting.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TEST_DIR)
for _p in (_ROOT, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _routes_fixture import RouteTestCase  # noqa: E402

from app.core import db_clinical  # noqa: E402

_MEDICO_ID = "gonzalez-juan"


def _habilitar_puerta(medico_id):
    """(token, doc) with the pre-consult interview explicitly forced ON for
    this doctor — see the module docstring for why the pack's own default
    can't be relied on."""
    from app.modules.intake import intake_admin
    token, doc = intake_admin.get_or_create_door_token(medico_id)
    doc = intake_admin._write_override(medico_id, doc, True)
    return token, doc


class TestIntakeDoorToken(RouteTestCase):

    def test_unknown_token_is_a_friendly_404_not_a_raw_error(self):
        status, headers, raw = self._request("GET", "/e/no-existe-este-token")
        self.assertEqual(status, 404)
        self.assertIn("text/html", headers.get("Content-Type", ""))

    def test_door_page_loads_for_a_real_token_no_session(self):
        token, doc = _habilitar_puerta(_MEDICO_ID)
        self.assertIsNotNone(token)

        status, _, raw = self._request("GET", f"/e/{token}")
        self.assertEqual(status, 200)
        html = raw.decode("utf-8")
        # No session: the shell has to hand the door token itself to the
        # client-side chat starter (data-qr-start), never data-token (that
        # branch is only for an already-resolved session, see _page_qr).
        self.assertIn(f'data-qr-start="{token}"', html)

    def test_qr_start_creates_a_real_interview_row(self):
        token, _ = _habilitar_puerta(_MEDICO_ID)

        status, body = self._post_json(
            f"/api/intake/qr/{token}/start", {"dni": "30999881"})
        self.assertEqual(status, 200)
        patient_token = body.get("patient_token")
        self.assertTrue(patient_token)

        interview = db_clinical.interview_by_patient_token(patient_token)
        self.assertIsNotNone(interview)
        self.assertEqual(interview.get("dni"), "30999881")

    def test_qr_start_invalid_dni_400(self):
        token, _ = _habilitar_puerta(_MEDICO_ID)
        status, _ = self._post_json(f"/api/intake/qr/{token}/start", {"dni": "12"})
        self.assertEqual(status, 400)

    def test_qr_start_unknown_token_404(self):
        status, body = self._post_json(
            "/api/intake/qr/no-existe-este-token/start", {"dni": "30999882"})
        self.assertEqual(status, 404)
        self.assertEqual(body.get("error"), "no_encontrado")


if __name__ == "__main__":
    unittest.main()
