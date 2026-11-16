# test_short_date.py — the availability column's ONE date shape (review 2026-11-15, P21).
# Twin of dates.mjs corta() and listing.mjs fechaEnFrase(): «Mié 18 nov, 08:00»
# in the cell, «el mié 18 nov, 08:00» inside a sentence.
import datetime
import unittest

from app.core import i18n
from app.patient_web import site_availability, site_schedule, site_studies

HOY = datetime.date(2026, 11, 15)  # domingo


class TestShortDate(unittest.TestCase):
    def setUp(self):
        _, self._tok = i18n.bind_lang("es")

    def tearDown(self):
        i18n.unbind(self._tok)

    def test_hoy_y_manana(self):
        self.assertEqual(site_schedule._short_date("2026-11-15", "08:00", HOY), "Hoy, 08:00")
        self.assertEqual(site_schedule._short_date("2026-11-16", "08:00", HOY), "Mañana, 08:00")

    def test_resto(self):
        self.assertEqual(site_schedule._short_date("2026-11-18", "08:00", HOY), "Mié 18 nov, 08:00")
        self.assertEqual(site_schedule._short_date("2026-12-02", None, HOY), "Mié 2 dic")

    def test_otro_anio(self):
        self.assertEqual(site_schedule._short_date("2027-01-05", "10:00", HOY), "Mar 5 ene 2027, 10:00")

    def test_invalido(self):
        self.assertEqual(site_schedule._short_date("18/11/2026", "08:00", HOY), "")
        self.assertEqual(site_schedule._short_date_in_phrase("x", "08:00", HOY), "")

    def test_en_frase(self):
        self.assertEqual(site_schedule._short_date_in_phrase("2026-11-18", "08:00", HOY), "el mié 18 nov, 08:00")
        self.assertEqual(site_schedule._short_date_in_phrase("2026-11-16", "08:00", HOY), "mañana, 08:00")
        self.assertEqual(site_schedule._short_date_in_phrase("2026-11-15", "08:00", HOY), "hoy, 08:00")

    def test_en_frase_ingles(self):
        i18n.unbind(self._tok)
        _, self._tok = i18n.bind_lang("en")
        self.assertEqual(site_schedule._short_date_in_phrase("2026-11-18", "08:00", HOY), "Wed 18 Nov, 08:00")

    def test_se_habilitan_sin_dos_puntos(self):
        # the old shape was «Se habilitan a partir de: Mié 2 dic, 08:00»
        txt = site_studies._habilita_text({"estado": "fecha", "dia": "2099-12-02", "hora": "08:00"})
        self.assertTrue(txt.startswith("Se habilitan desde el "), txt)
        self.assertNotIn(":", txt.replace("08:00", ""))
        self.assertNotIn("diciembre", txt)

    def test_hay_turno_con_sin_dos_puntos(self):
        dia = "2099-12-02"
        rows = [
            {"nombre": "Ana Paz", "especialidad_todas": ["cardio"],
             "proximo": {"habilita": {"estado": "sin_fecha"}, "sort": "9"}},
            {"nombre": "Luis Gil", "especialidad_todas": ["cardio"], "slot_href": "/d/x?slot=1",
             "proximo": {"v": "Miércoles 2 de diciembre de 2099, 08:00", "kind": "turno",
                         "dia": dia, "hora": "08:00", "sort": "0" + dia + " 08:00"}},
        ]
        out = site_availability._with_remedies(rows, by="medico")
        texto = out[0]["proximo"]["remedio"]["texto"]
        self.assertTrue(texto.startswith("Hay turno con Luis Gil el "), texto)
        self.assertNotIn("Luis Gil:", texto)
        self.assertNotIn("diciembre", texto)
        self.assertEqual(out[0]["proximo"]["remedio"]["href"], "/d/x?slot=1")


if __name__ == "__main__":
    unittest.main()
