"""Contrato público de la agenda avanzada: el sitio sólo ofrece cupos web."""

import datetime
import unittest

from app.patient_web import catalog
from app.patient_web import site_availability


_LUNES_TEMPRANO = datetime.datetime(2026, 7, 6, 7, 0)
_FRANJA_LUNES = {"dias": [1], "desde": "08:00", "hasta": "11:00", "cada_min": 20}


class TestAgendaAvanzadaSlots(unittest.TestCase):
    def test_bloqueo_parcial_preserva_horas_fuera_del_rango(self):
        agenda = {
            "franjas": [_FRANJA_LUNES],
            "excepciones": [{
                "fecha": "2026-07-06", "tipo": "bloqueado",
                "desde": "09:00", "hasta": "09:40", "motivo": "Reunión de equipo",
            }],
        }
        horas = [t["hora"] for t in site_availability._agenda_slots(agenda, now=_LUNES_TEMPRANO)
                 if t["dia"] == "2026-07-06"]
        self.assertIn("08:40", horas)
        self.assertNotIn("09:00", horas)
        self.assertNotIn("09:20", horas)
        # Las ventanas, igual que las franjas ordinarias, terminan de forma
        # exclusiva: a las 09:40 vuelve a abrirse la oferta online.
        self.assertIn("09:40", horas)

    def test_vacaciones_por_rango_ocultan_todos_los_dias_afectados(self):
        agenda = {
            "franjas": [_FRANJA_LUNES],
            "excepciones": [{
                "desde_fecha": "2026-07-06", "hasta_fecha": "2026-07-12",
                "tipo": "cerrado", "motivo": "Vacaciones",
            }],
        }
        slots = site_availability._agenda_slots(agenda, now=_LUNES_TEMPRANO)
        self.assertTrue(slots)
        self.assertNotIn("2026-07-06", [t["dia"] for t in slots])
        self.assertEqual(slots[0]["dia"], "2026-07-13")

    def test_cupos_del_personal_nunca_aparecen_en_el_sitio(self):
        agenda = {"franjas": [dict(_FRANJA_LUNES, cupos={
            "web": 0, "reservado": 4, "sobreturnos": 3,
        })]}
        self.assertEqual(site_availability._agenda_slots(agenda, now=_LUNES_TEMPRANO), [])

    def test_cupos_reservados_no_crean_turnos_web_adicionales(self):
        agenda = {"franjas": [dict(_FRANJA_LUNES, cupos={
            "web": 1, "reservado": 4, "sobreturnos": 3,
        })]}
        horas = [t["hora"] for t in site_availability._agenda_slots(agenda, now=_LUNES_TEMPRANO)
                 if t["dia"] == "2026-07-06"]
        self.assertEqual(horas, ["08:00", "08:20", "08:40", "09:00", "09:20", "09:40", "10:00", "10:20", "10:40"])


class TestAgendaDeTardeCompleta(unittest.TestCase):
    """Caso de un medico con agenda larga (salud, 13.11): agenda 13:00-20:00 cada 20 min; el sitio
    cortaba el dia en 12 horas y el paciente nunca veia las de 17:00 a 19:40."""

    _FRANJA_TARDE = {"dias": [1], "desde": "13:00", "hasta": "20:00", "cada_min": 20}

    def test_el_dia_llega_hasta_la_ultima_hora_de_la_agenda(self):
        slots = site_availability._agenda_slots(
            {"franjas": [self._FRANJA_TARDE]}, now=_LUNES_TEMPRANO)
        horas = [t["hora"] for t in slots if t["dia"] == "2026-07-06"]
        self.assertEqual(len(horas), 21)
        self.assertEqual(horas[0], "13:00")
        self.assertEqual(horas[-1], "19:40")

    def test_una_hora_ocupada_no_corre_el_corte_del_dia(self):
        # Antes el corte se aplicaba ANTES de quitar lo ocupado: con 13:00 y
        # 13:20 tomadas el paciente veia 10 horas y ninguna despues de 17:00.
        servicio = {"id": "svc-tarde", "agenda": {"franjas": [self._FRANJA_TARDE]}}
        slots = site_availability._agenda_slots(servicio["agenda"], now=_LUNES_TEMPRANO)
        libres = [t for t in slots if t["dia"] == "2026-07-06"
                  and t["hora"] not in ("13:00", "13:20")]
        self.assertEqual(libres[-1]["hora"], "19:40")
        self.assertEqual(len(libres), 19)


class TestAgendaAvanzadaCatalogo(unittest.TestCase):
    def test_normaliza_rango_bloqueo_y_cupos(self):
        doc, err = catalog.validate("servicios", {
            "nombre": "Estudio", "centro": "sampleton-hospital", "modo": "solicitud",
            "agenda": {"franjas": [{
                **_FRANJA_LUNES,
                "cupos": {"web": 1, "reservado": 4, "sobreturnos": 2},
            }], "excepciones": [
                {"desde_fecha": "2026-07-20", "hasta_fecha": "2026-07-31", "tipo": "cerrado"},
                {"fecha": "2026-08-03", "tipo": "bloqueado", "desde": "09:00", "hasta": "09:40"},
            ]},
        }, is_new=True)
        self.assertIsNone(err)
        self.assertEqual(doc["agenda"]["franjas"][0]["cupos"]["reservado"], 4)
        self.assertEqual(doc["agenda"]["excepciones"][0]["hasta_fecha"], "2026-07-31")
        self.assertEqual(doc["agenda"]["excepciones"][1]["tipo"], "bloqueado")

    def test_normaliza_porcentaje_de_sobreturnos_sin_techo_arbitrario(self):
        doc, err = catalog.validate("servicios", {
            "nombre": "Estudio", "centro": "sampleton-hospital", "modo": "solicitud",
            "agenda": {"franjas": [{
                **_FRANJA_LUNES,
                "cupos": {"web": 1, "reservado": 0, "sobreturnos": 2,
                           "overbooking_percent": 200},
            }]},
        }, is_new=True)
        self.assertIsNone(err)
        self.assertEqual(doc["agenda"]["franjas"][0]["cupos"]["overbooking_percent"], 200)

    def test_rechaza_porcentajes_no_enteros_sin_excepcion(self):
        for value in (1.5, True, float("nan"), float("inf")):
            _doc, err = catalog.validate("servicios", {
                "nombre": "Estudio", "centro": "sampleton-hospital", "modo": "solicitud",
                "agenda": {"franjas": [{**_FRANJA_LUNES,
                    "cupos": {"web": 1, "reservado": 0, "sobreturnos": 0,
                               "overbooking_percent": value}}]},
            }, is_new=True)
            self.assertEqual(err, "agenda: franja 1: cupos.overbooking_percent debe ser un entero")

    def test_porcentaje_rechaza_solo_franjas_superpuestas_activas(self):
        base = {"dias": [1], "desde": "09:00", "hasta": "10:00", "cada_min": 20,
                "cupos": {"web": 1, "reservado": 0, "sobreturnos": 0}}
        _out, err = catalog._validate_time_bands([
            dict(base, cupos={"web": 1, "reservado": 0, "sobreturnos": 0, "overbooking_percent": 20}),
            dict(base, desde="09:40", hasta="10:20", cupos={"web": 1, "reservado": 0, "sobreturnos": 0, "overbooking_percent": 20}),
        ])
        self.assertIn("comparten horas", err)
        # Names the rows by their days and hours, never "franjas 1 y 2"
        # (on-site tester, 2026-10-21: the index did not say what to fix).
        self.assertIn("09:00 a 10:00", err)
        self.assertIn("09:40 a 10:20", err)
        self.assertNotIn("franjas 1 y 2", err)
        # One enabled block is enough: a legacy/off block must not shadow it.
        enabled = dict(base, cupos={**base["cupos"], "overbooking_percent": 20})
        for bands in ([base, enabled], [enabled, base]):
            _out, err = catalog._validate_time_bands(bands)
            self.assertIn("comparten horas", err)
        for bands in (
            [dict(base, cupos={"web": 1, "reservado": 0, "sobreturnos": 0, "overbooking_percent": 20}),
             dict(base, dias=[2], cupos={"web": 1, "reservado": 0, "sobreturnos": 0, "overbooking_percent": 20})],
            [dict(base, cupos={"web": 1, "reservado": 0, "sobreturnos": 0, "overbooking_percent": 20}),
             dict(base, desde="10:00", hasta="11:00", cupos={"web": 1, "reservado": 0, "sobreturnos": 0, "overbooking_percent": 20})],
            [dict(base, cupos={"web": 1, "reservado": 0, "sobreturnos": 2}),
             dict(base, desde="09:40", hasta="10:20", cupos={"web": 1, "reservado": 0, "sobreturnos": 0, "overbooking_percent": 0})],
        ):
            _out, err = catalog._validate_time_bands(bands)
            self.assertIsNone(err)


if __name__ == "__main__":
    unittest.main()
