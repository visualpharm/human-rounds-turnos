# test_fecha_amigable.py — formato amigable de fechas del lado servidor.
# Gemelo de tests/dates.test.mjs (dates.mjs): mismo contrato en ambos lados.
# Correr: python3 -m unittest discover -s tests
import datetime
import unittest

from app.patient_web import site_schedule


HOY = datetime.date(2026, 7, 10)  # viernes (verificado)


class TestFechaAmigable(unittest.TestCase):
    def test_hoy(self):
        self.assertEqual(site_schedule._friendly_date("2026-07-10", "14:40", HOY), "Hoy, 14:40")

    def test_manana(self):
        self.assertEqual(site_schedule._friendly_date("2026-07-11", "09:00", HOY), "Mañana, sábado 11, 09:00")

    def test_manana_cruza_mes(self):
        hoy = datetime.date(2026, 7, 31)
        self.assertEqual(site_schedule._friendly_date("2026-08-01", "08:15", hoy), "Mañana, sábado 1, 08:15")

    def test_resto(self):
        self.assertEqual(site_schedule._friendly_date("2026-07-14", "14:40", HOY),
                         "Martes 14 de julio, 14:40")

    def test_sin_hora(self):
        self.assertEqual(site_schedule._friendly_date("2026-07-14", None, HOY), "Martes 14 de julio")
        self.assertEqual(site_schedule._friendly_date("2026-07-14", "", HOY), "Martes 14 de julio")

    def test_otro_anio(self):
        self.assertEqual(site_schedule._friendly_date("2027-01-05", "10:00", HOY),
                         "Martes 5 de enero de 2027, 10:00")

    def test_timestamp_largo(self):
        self.assertEqual(site_schedule._friendly_date("2026-07-11T00:00:00", None, HOY), "Mañana, sábado 11")

    def test_invalido(self):
        self.assertEqual(site_schedule._friendly_date("14/07/2026", "10:00", HOY), "")
        self.assertEqual(site_schedule._friendly_date("", None, HOY), "")
        self.assertEqual(site_schedule._friendly_date(None, None, HOY), "")


if __name__ == "__main__":
    unittest.main()
