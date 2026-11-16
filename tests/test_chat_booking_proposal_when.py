"""test_chat_booking_proposal_when.py — the chat's booking proposal reads well
for every shape of the day.

The proposal used to say «…, Cardiología, el Mañana, sábado 11 a las 09:00?»:
an article and «a las» bolted around the shared «day, hour» phrase of
`site_schedule._friendly_date`. Now {when} is its own sentence and comes out
of that formatter whole: «Hoy, 14:40» / «Mañana, sábado 11, 09:00» /
«Martes 17 de noviembre, 09:00» / «Lunes 4 de enero de 2027, 09:00».

Run: python3 -m unittest discover -s tests -p "test_chat_booking_proposal_when.py"
"""

import datetime
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import i18n  # noqa: E402
from app.modules.chat import chat_tools  # noqa: E402

_HOY = datetime.date(2026, 11, 10)  # a Tuesday

_CASES = {
    # day -> (es, en)
    "2026-11-10": ("Hoy, 09:00", "Today, 09:00"),
    "2026-11-11": ("Mañana, miércoles 11, 09:00", "Tomorrow, Wednesday 11, 09:00"),
    "2026-11-17": ("Martes 17 de noviembre, 09:00", "Tuesday 17 November, 09:00"),
    "2027-01-04": ("Lunes 4 de enero de 2027, 09:00", "Monday 4 January 2027, 09:00"),
}


class PropuestaDeTurno(unittest.TestCase):
    def _proposal(self, day, lang):
        service = {"id": "synthetic", "nombre": "Cardiología", "modo": "solicitud"}
        token = i18n._ACTIVE.set(lang)
        try:
            with patch("app.patient_web.site_catalog._service_by_id", return_value=service), \
                 patch("app.patient_web.site_availability._offers_appointments", return_value=True), \
                 patch("app.patient_web.site_base._today_art", return_value=_HOY), \
                 patch.object(chat_tools, "_booking_order_error", return_value=None), \
                 patch.object(chat_tools, "_base_url", return_value="https://synthetic.example"), \
                 patch.object(chat_tools.chat_store, "get_user",
                              return_value={"nombre": "Sofía", "apellido": "Herrera"}), \
                 patch.object(chat_tools.chat_store, "chat_save_message") as save:
                out = chat_tools._tool_request(
                    {"dni": "99000001"},
                    {"service_id": "synthetic", "date": day, "time": "09:00"},
                    chat_tools.ToolInvocation(channel="web", conversation_key="k"))
        finally:
            i18n._ACTIVE.reset(token)
        save.assert_called_once()
        return out["nota"]

    def test_cada_forma_del_dia_en_castellano(self):
        for day, (es, _en) in _CASES.items():
            with self.subTest(day=day):
                note = self._proposal(day, "es")
                self.assertEqual(
                    note, f"¿Confirmás la solicitud para Sofía Herrera, Cardiología? {es}. Respondé «Confirmar».")
                self.assertNotIn(" el ", note)
                self.assertNotIn("a las", note)

    def test_cada_forma_del_dia_en_ingles(self):
        for day, (_es, en) in _CASES.items():
            with self.subTest(day=day):
                note = self._proposal(day, "en")
                self.assertEqual(
                    note, f"Confirm the request for Sofía Herrera, Cardiología? {en}. Reply “Confirm”.")
                self.assertNotIn(" on ", note)
                self.assertNotIn(" at ", note)


if __name__ == "__main__":
    unittest.main()
