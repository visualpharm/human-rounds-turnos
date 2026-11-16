"""test_apertura_mismo_dia.py — el servicio por orden de llegada que abre su
listado el mismo día (the reference-deployment counterpart, reunión hospital 2026-07-24: "el sin turno
por orden de llegada… que el mismo día se abra, para que la gente lo saque a
distancia, pero ese día").

Es la ÚNICA grieta del invariante "walk-in nunca ofrece turnos". Estas pruebas
fijan que sea angosta: sólo HOY, sólo con el flag, y que las dos puntas — la
que promete (la banda/celda) y la que ofrece (la grilla del wizard) — digan lo
mismo.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_apertura_mismo_dia.py"
"""

import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1")

from app.patient_web import catalog    # noqa: E402
from app.patient_web import site_availability, site_base, site_catalog, site_listing, site_page_service
from app.core import tz  # noqa: E402

_CENTROS = {"centros": [{"slug": "centro-a", "name": "Centro A", "address": "Calle 1"}]}
# ventana enorme todos los días: siempre queda algún horario futuro hoy
_AGENDA = {"dias": [0, 1, 2, 3, 4, 5, 6], "desde": "00:00", "hasta": "23:59", "cada_min": 60}
_SERVICIOS = {"servicios": [
    {"id": "enfermeria", "nombre": "Enfermería", "centro": "centro-a", "cluster": "enfermeria",
     "modo": "walkin", "horario": "Lunes a viernes de 8 a 12 h",
     "apertura_mismo_dia": True, "agenda": dict(_AGENDA)},
    {"id": "guardia", "nombre": "Guardia", "centro": "centro-a", "cluster": "guardia",
     "modo": "walkin", "horario": "Todos los días"},
    {"id": "cardio", "nombre": "Cardiología", "centro": "centro-a", "cluster": "cardiologia",
     "modo": "solicitud", "agenda": dict(_AGENDA)},
]}
_MEDICOS = {"medicos": []}


# El reloj de estas pruebas se fija a propósito (2026-07-27). La agenda del
# fixture abre 00:00–23:59, así que "siempre queda algún horario futuro hoy"
# es verdad todo el día MENOS la última hora: corriendo 23:35 el horizonte de
# un solo día ya no tenía nada y tres pruebas fallaban por la hora a la que se
# corrió la suite, no por el código. `_agenda_slots` ya recibe `now` — acá se
# le da uno fijo, y el caso del final del día pasa a ser una prueba propia en
# vez de una bomba de tiempo.
def _congelar_reloj(test, hora=datetime.time(9, 0)):
    real = site_availability._agenda_slots

    def fijo(agenda, now=None, solo_hoy=False, **policy):
        # fecha en ART, no la del sistema (el runner de CI corre en UTC —
        # naive date.today() puede quedar un día adelantado/atrasado del
        # calendario real de la instalación cerca de la medianoche ART).
        ahora = now or datetime.datetime.combine(
            datetime.datetime.now(tz.tzinfo()).date(), hora)
        return real(agenda, ahora, solo_hoy, **policy)

    site_availability._agenda_slots = fijo
    site_base._CACHE.clear()
    test.addCleanup(site_base._CACHE.clear)
    test.addCleanup(setattr, site_availability, "_agenda_slots", real)


class AperturaMismoDiaTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="apertura-mismo-dia-")
        webdir = os.path.join(self._tmp, "webdata")
        os.makedirs(webdir)
        for name, doc in (("centros-salud.json", _CENTROS), ("servicios.json", _SERVICIOS),
                          ("medicos.json", _MEDICOS)):
            with open(os.path.join(webdir, name), "w", encoding="utf-8") as f:
                json.dump(doc, f)
        # Registered before set_env: cleanups run last-in first-out, so the caches are dropped after the environment is back.
        self.addCleanup(catalog._JSON_CACHE.clear)
        self.addCleanup(catalog.invalidate)
        _env.set_env(self, TURNOS_WEBDATA_DIR=webdir)
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        site_base._CACHE.clear()
        _congelar_reloj(self)

    def tearDown(self):
        site_base._CACHE.clear()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _s(self, sid):
        return site_catalog._service_by_id(sid)

    def _hoy(self):
        return datetime.datetime.now(tz.tzinfo()).date().isoformat()

    # ---- el portero ----------------------------------------------------

    def test_solo_el_marcado_ofrece_turnos(self):
        self.assertTrue(site_availability._offers_appointments(self._s("enfermeria")))
        self.assertFalse(site_availability._offers_appointments(self._s("guardia")))
        self.assertTrue(site_availability._offers_appointments(self._s("cardio")))
        self.assertTrue(site_availability._opens_same_day(self._s("enfermeria")))
        self.assertFalse(site_availability._opens_same_day(self._s("cardio")))

    def test_un_walkin_comun_sigue_sin_ofrecer_nada(self):
        self.assertEqual(site_availability._appointments_for_service(self._s("guardia")), {})
        self.assertIsNone(site_availability._next_appointment_best(self._s("guardia")))
        px = site_availability._service_next(self._s("guardia"))
        self.assertEqual(px["kind"], "walkin")

    # ---- sólo hoy -------------------------------------------------------

    def test_la_grilla_del_wizard_sólo_trae_hoy(self):
        data = site_availability._appointments_for_service(self._s("enfermeria"))
        dias = {t["dia"] for m in data["medicos"] for t in m["turnos"]}
        self.assertTrue(dias, "tendría que ofrecer los horarios de hoy")
        self.assertEqual(dias, {self._hoy()})

    def test_el_servicio_con_turno_sigue_ofreciendo_todo_el_mes(self):
        data = site_availability._appointments_for_service(self._s("cardio"))
        dias = {t["dia"] for m in data["medicos"] for t in m["turnos"]}
        self.assertGreater(len(dias), 1)

    def test_lo_que_promete_la_celda_es_de_hoy_y_está_en_la_grilla(self):
        # invariante del proyecto: lo que promete la banda, el wizard lo ofrece
        best = site_availability._next_appointment_best(self._s("enfermeria"))
        self.assertIsNotNone(best)
        self.assertEqual(best[0], self._hoy())
        data = site_availability._appointments_for_service(self._s("enfermeria"))
        ofrecidos = {(t["dia"], t["hora"]) for m in data["medicos"] for t in m["turnos"]}
        self.assertIn(best, ofrecidos)

    def test_la_celda_habla_como_un_turno_cuando_hay_lugar_hoy(self):
        px = site_availability._service_next(self._s("enfermeria"))
        self.assertEqual(px["kind"], "turno")
        self.assertEqual(px["dia"], self._hoy())

    def test_cuando_el_dia_se_acabo_vuelve_a_hablar_como_lo_que_es(self):
        # El listado se abre hoy y se cierra hoy: pasada la última hora no hay
        # nada que prometer, y la celda tiene que volver a decir lo de siempre
        # (por orden de llegada), no quedarse ofreciendo un turno que no existe.
        _congelar_reloj(self, datetime.time(23, 58))
        self.assertIsNone(site_availability._next_appointment_best(self._s("enfermeria")))
        self.assertEqual(site_availability._appointments_for_service(self._s("enfermeria")), {})
        self.assertEqual(site_availability._service_next(self._s("enfermeria"))["kind"], "walkin")

    # ---- destino de la acción -------------------------------------------

    def test_el_link_lleva_al_wizard_y_no_a_la_pagina_informativa(self):
        self.assertEqual(site_listing._appointment_href(self._s("enfermeria")), "/service/enfermeria/book")
        self.assertEqual(site_listing._appointment_href(self._s("guardia")), "/service/guardia")

    def test_el_wizard_abre_y_se_presenta_como_turno_sin_orden(self):
        html = site_page_service._page_service_appointment("enfermeria")
        self.assertIsNotNone(html)
        txt = html.decode("utf-8")
        self.assertIn("Elegí un turno", txt)
        self.assertIn('"modo": "solicitud"', txt)

    def test_el_wizard_de_un_walkin_comun_sigue_sin_existir(self):
        self.assertIsNone(site_page_service._page_service_appointment("guardia"))

    # ---- validación del catálogo ----------------------------------------

    def test_el_flag_solo_vale_en_walkin(self):
        doc, err = catalog._validate_service(
            {"id": "x", "nombre": "X", "centro": "centro-a", "modo": "solicitud",
             "apertura_mismo_dia": True}, True)
        self.assertIsNone(doc)
        self.assertIn("orden de llegada", err)

    def test_con_el_flag_el_walkin_puede_tener_agenda(self):
        doc, err = catalog._validate_service(
            {"id": "x", "nombre": "X", "centro": "centro-a", "modo": "walkin",
             "apertura_mismo_dia": True, "agenda": dict(_AGENDA)}, True)
        self.assertIsNone(err, err)
        self.assertTrue(doc["apertura_mismo_dia"])

    def test_apagar_el_flag_lo_saca_del_documento(self):
        doc, err = catalog._validate_service(
            {"id": "x", "nombre": "X", "centro": "centro-a", "modo": "walkin",
             "apertura_mismo_dia": False}, True)
        self.assertIsNone(err, err)
        self.assertNotIn("apertura_mismo_dia", doc)


if __name__ == "__main__":
    unittest.main()
