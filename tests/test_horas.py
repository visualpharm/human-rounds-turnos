"""test_horas.py — helpers de horas semanales (site_schedule._window_hours/
_schedule_hours/_week_hours/_week_hours_doctor) y su wiring en
_services_enriched/_doctor_listing_row, más el espejo _COL_PRESETS/
_COL_ORDER/_DOCTORS_COLS contra listing.js.

Correr: python3 -m unittest discover -s tests -p "test_horas.py" -v
"""
import unittest

from app.patient_web import site_availability, site_catalog, site_listing, site_schedule
from _packtest import reference_pack_test


class TestFranjaHoras(unittest.TestCase):
    def test_franja_plana(self):
        self.assertEqual(site_schedule._window_hours(
            {"dias": [1, 2, 3, 4, 5], "desde": "08:00", "hasta": "12:00"}), 20.0)

    def test_franja_sin_dias(self):
        self.assertEqual(site_schedule._window_hours({"desde": "08:00", "hasta": "12:00"}), 0.0)

    def test_franja_invalida(self):
        self.assertEqual(site_schedule._window_hours({}), 0.0)
        self.assertEqual(site_schedule._window_hours({"desde": "x", "hasta": "y", "dias": [1]}), 0.0)


class TestAgendaHoras(unittest.TestCase):
    def test_agenda_plana(self):
        ag = {"dias": [1, 2], "desde": "09:00", "hasta": "11:00"}
        self.assertEqual(site_schedule._schedule_hours(ag), 4.0)

    def test_agenda_con_franjas(self):
        ag = {"franjas": [
            {"dias": [1], "desde": "09:00", "hasta": "11:00"},
            {"dias": [2, 3], "desde": "10:00", "hasta": "12:00"},
        ]}
        self.assertEqual(site_schedule._schedule_hours(ag), 2.0 + 4.0)

    def test_agenda_vacia(self):
        self.assertEqual(site_schedule._schedule_hours({}), 0.0)
        self.assertEqual(site_schedule._schedule_hours(None), 0.0)


class TestHorasSemana(unittest.TestCase):
    def test_agenda_propia_gana(self):
        s = {"cluster": "x", "centro": "y",
             "agenda": {"dias": [1], "desde": "08:00", "hasta": "10:00"}}
        self.assertEqual(site_availability._week_hours(s), 2.0)

    @reference_pack_test
    def test_fallback_medicos_mismo_cluster_centro(self):
        s = {"id": "s1", "cluster": "cardiologia", "centro": "hospital-pinamar"}
        h = site_availability._week_hours(s)
        # depende de webdata/medicos.json real: debe ser > 0 si hay médicos
        # de cardiologia en hospital-pinamar con franjas (Julian Luna).
        self.assertGreater(h, 0.0)

    def test_cero_sin_nada(self):
        s = {"cluster": "no-existe-xyz", "centro": "no-existe-xyz"}
        self.assertEqual(site_availability._week_hours(s), 0.0)


class TestWiring(unittest.TestCase):
    def test_servicios_enriched_trae_horas(self):
        for s in site_listing._services_enriched():
            self.assertIn("horas", s)
            self.assertIsInstance(s["horas"], float)

    def test_medico_listado_row_trae_orden_y_horas(self):
        m = site_catalog._doctors()[0]
        row = site_listing._doctor_listing_row(m)
        self.assertIn("orden", row)
        self.assertIn(row["orden"], (True, False, None))
        self.assertIn("horas", row)
        self.assertIsInstance(row["horas"], float)
        self.assertIn("info_href", row)
        # Desde 418d12e ("Más horarios" en filas de médicos): con turno
        # reservable concreto, _slot_fields pisa info_href con la grilla del
        # wizard (difiere de href para que listing.js dibuje "Más horarios");
        # sin slot, ambos siguen siendo /doctor/<id> y el link se suprime.
        if "slot_href" in row:
            self.assertNotEqual(row["info_href"], row["href"])
            self.assertIn("/book?slots=1&medico=", row["info_href"])
        else:
            self.assertEqual(row["info_href"], row["href"])


class TestEspejoColumnas(unittest.TestCase):
    def test_col_order_sin_turno_orden_antes_de_proximo(self):
        self.assertNotIn("turno", site_listing._COL_ORDER)
        self.assertNotIn("turno", site_listing._COL_PRESETS)
        i_orden = site_listing._COL_ORDER.index("orden")
        i_proximo = site_listing._COL_ORDER.index("proximo")
        self.assertEqual(i_orden + 1, i_proximo)

    def test_medicos_cols_incluye_orden(self):
        # ex-_DOCTORS_COLS: ya no es una constante de módulo (se congelaba en
        # es al importar, sin request bindeado) — _centro_medicos_cols()
        # arma la misma lista de columnas en el momento del render.
        tipos = [c["t"] for c in site_listing._centro_medicos_cols()]
        self.assertIn("orden", tipos)


if __name__ == "__main__":
    unittest.main()
