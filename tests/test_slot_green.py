"""test_slot_green.py — todos los turnos son verdes; la lejanía sólo vira el verde
hacia un azul (responsable del producto 19/10, llamada con la contraparte técnica). Mismo criterio que
`ORDEN.bucket` de static/platform/listing.mjs (tests/listing-order.test.mjs).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_slot_green.py"
"""
import datetime
import unittest

import _clock
from app.patient_web import site_availability, site_base, site_catalog, site_page_service, site_shell, site_studies


def _iso(days):
    return (site_base._today_art() + datetime.timedelta(days=days)).isoformat()


class TestCtaBucket(unittest.TestCase):
    def test_pasos_de_tres_dias(self):
        self.assertEqual(site_studies._cta_bucket(_iso(0)), "w0")
        self.assertEqual(site_studies._cta_bucket(_iso(1)), "w0")
        self.assertEqual(site_studies._cta_bucket(_iso(7)), "w2")
        self.assertEqual(site_studies._cta_bucket(_iso(8)), "w3")
        self.assertEqual(site_studies._cta_bucket(_iso(15)), "w5")
        self.assertEqual(site_studies._cta_bucket(_iso(30)), "w10")
        self.assertEqual(site_studies._cta_bucket(_iso(120)), "w10")

    def test_sin_fecha_valida_es_el_extremo_cercano_como_antes(self):
        self.assertEqual(site_studies._cta_bucket("nada"), "w0")

    def test_sin_saltos_bruscos(self):
        pasos = [int(site_studies._cta_bucket(_iso(d))[1:]) for d in range(0, 45)]
        for a, b in zip(pasos, pasos[1:]):
            self.assertIn(b - a, (0, 1))


class TestSubiTuOrden(unittest.TestCase):
    """Servicio «con orden»: el botón de la banda es «Subí tu orden», no
    «Sacar el …» (contraparte técnica, 19/10); sin orden sigue igual."""

    def _servicio(self, con_orden):
        modo = "solicitud_con_orden" if con_orden else "solicitud"
        return next((x for x in site_catalog._services()
                     if x.get("modo") == modo
                     and site_availability._service_next(x).get("kind") == "turno"), None)

    def test_con_orden_dice_subi_tu_orden_y_conserva_el_turno(self):
        s = self._servicio(True)
        self.assertIsNotNone(s)
        html = site_page_service._page_service(s["id"]).decode()
        band = html[html.index('class="mp-band'):]
        band = band[:band.index("mp-more")]
        self.assertIn("Subí tu orden</a>", band)
        self.assertNotIn(">Sacar ", band)
        # el primer turno no se pierde: pasa al rótulo de la banda
        self.assertIn("Primer turno libre: ", band)

    def test_sin_orden_sigue_diciendo_sacar(self):
        s = self._servicio(False)
        self.assertIsNotNone(s)
        html = site_page_service._page_service(s["id"]).decode()
        band = html[html.index('class="mp-band'):]
        band = band[:band.index("mp-more")]
        self.assertIn(">Sacar ", band)
        self.assertNotIn("Subí tu orden", band)


class TestTurnosSinHabilitar(unittest.TestCase):
    """Sin turnos abiertos no hay botón «Sacar turno»: el paciente lee «No hay
    turnos disponibles» y, debajo, «Se habilitan a partir de …» (contraparte técnica, 19/10 y 20/10)."""

    FECHA = {"estado": "fecha", "dia": "2026-11-13", "hora": "08:00"}

    def setUp(self):
        # The opening date is fixed (13 November); on the 12th the page says «Mañana, viernes 13».
        _clock.freeze(self, "2026-11-05T15:00:00Z")
        self.s = next(x for x in site_catalog._services()
                      if x.get("modo") == "solicitud")
        site_availability._OPENING_MEMO.clear()
        self._orig = (site_availability._next_appointment_best, site_availability._no_slots_info)
        site_availability._next_appointment_best = lambda s, ocupados=None: None
        site_availability._no_slots_info = lambda s, ref=None, now=None: {"apertura": self.FECHA}

    def tearDown(self):
        site_availability._next_appointment_best, site_availability._no_slots_info = self._orig
        site_availability._OPENING_MEMO.clear()

    def test_proximo_lleva_la_fecha_de_apertura(self):
        px = site_availability._service_next(self.s)
        self.assertEqual(px["kind"], "solicitud")
        self.assertEqual(px["habilita"], self.FECHA)

    def test_sin_agenda_no_inventa_fecha(self):
        site_availability._no_slots_info = lambda s, ref=None, now=None: {}
        self.assertNotIn("habilita", site_availability._service_next(self.s))

    def test_con_turno_no_hay_habilita(self):
        site_availability._next_appointment_best = lambda s, ocupados=None: ("2026-11-02", "09:00")
        self.assertNotIn("habilita", site_availability._service_next(self.s))

    def test_banda_del_servicio_dice_el_mensaje_y_no_el_boton(self):
        html = site_page_service._page_service(self.s["id"]).decode()
        band = html[html.index('class="mp-band'):]
        band = band[:band.index("</a></div></div>")]
        self.assertIn("No hay turnos disponibles", band)
        # the availability column's one short date (review 2026-11-15, P21)
        self.assertIn("Se habilitan desde el vie 13 nov, 08:00", band)
        self.assertNotIn("Sacar turno", band)
        self.assertNotIn('class="mp-cta ', band)  # ningún botón lleno
        self.assertIn("Si es una urgencia, llamá al 107 o acercate a la guardia.", band)
        # el camino a escribir el caso sigue ahí, sin llamarse «Sacar turno»
        self.assertIn("Contanos tu caso", band)

    def test_sin_fechas_dice_que_no_hay_agenda_nueva(self):
        site_availability._no_slots_info = lambda s, ref=None, now=None: {"apertura": {"estado": "sin_fechas"}}
        html = site_page_service._page_service(self.s["id"]).decode()
        self.assertIn("No hay turnos disponibles", html)
        # una sola frase, sin repetirla con otras palabras (responsable del producto, 20/10)
        self.assertNotIn("fechas nuevas", html)
        band = html[html.index('class="mp-band'):]
        band = band[:band.index("Contanos tu caso")]
        self.assertEqual(band.count("No hay turnos disponibles"), 1)


class TestSinDoradoNiRojo(unittest.TestCase):
    def test_css_del_sitio(self):
        with open(site_shell.__file__, encoding="utf-8") as f:
            src = f.read()
        for tono in ("#8a6d1f", "#9a7b1f", "mp-cta--semana", "mp-cta--lejos"):
            self.assertFalse(tono in src, tono)


if __name__ == "__main__":
    unittest.main()
