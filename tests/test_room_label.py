"""A service's room reads once (TASK-57, 26.10, persona Tomás).

The service page put the label «Consultorio» before whatever the catalog
holds, and the packs already write «Consultorio 1» — «Consultorio Consultorio
1», «Consultorio Sala de curaciones». A bare number gets the label; a value
that names its own place is shown as it is.

Run:  python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_room_label.py
"""
import os
import sys
import unittest

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.patient_web import site_centers


class TestRoomLabel(unittest.TestCase):
    def test_a_number_gets_the_label(self):
        self.assertEqual(site_centers._room_text("4"), "Consultorio 4")
        self.assertEqual(site_centers._room_text(" 12B "), "Consultorio 12B")

    def test_a_named_place_is_not_prefixed(self):
        for valor in ("Consultorio 1", "consultorio 3", "Sala de curaciones", "Vacunatorio",
                      "Consultorio odontológico", "SUM", "Laboratorio central"):
            self.assertEqual(site_centers._room_text(valor), valor.strip(), valor)

    def test_every_pack_value_reads_without_a_doubled_word(self):
        import json
        for pack in sorted(os.listdir(os.path.join(ROOT, "packs"))):
            path = os.path.join(ROOT, "packs", pack, "servicios.json")
            if not os.path.exists(path):
                continue
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            items = data if isinstance(data, list) else data.get("servicios", data)
            if isinstance(items, dict):
                items = list(items.values())
            for s in items:
                valor = (s or {}).get("consultorio") if isinstance(s, dict) else None
                if not valor:
                    continue
                texto = site_centers._room_text(valor).lower().split()
                self.assertNotEqual(texto[:1], texto[1:2], (pack, valor))

    def test_service_page_line_uses_it(self):
        html = site_centers._room_line_html("Consultorio 1")
        self.assertNotIn("Consultorio</span> Consultorio", html)
        self.assertIn("Consultorio 1", html)
        self.assertIn('class="ci-k">Consultorio</span> 4', site_centers._room_line_html("4"))


if __name__ == "__main__":
    unittest.main()
