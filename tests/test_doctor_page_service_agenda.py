"""test_doctor_page_service_agenda.py — the doctor page's «Cuándo atiende» reads
the same windows the booking does.

Hub brief of 2026-11-08: the public doctor page showed only the doctor's own
windows (`centros[].franjas`), while the slot generator, the desk's day queue
and the printed day sheet read the agenda of the services that manage the
doctor (`schedule_policy.managed_services`), then the center windows, then the
flat agenda — with the `cerrado` / `extra` exceptions of the day. A service
agenda set in the panel left the page empty or telling other hours.

Now one source, `modules/turnos/doctor_windows.py`, feeds the page, the queue
and the print, and the page walks the next seven days through it.
"""

import datetime
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _env  # noqa: E402

from app.modules.turnos import agenda_print  # noqa: E402
from app.modules.turnos import doctor_appointments  # noqa: E402
from app.patient_web import catalog  # noqa: E402
from app.patient_web import site_availability, site_base, site_doctors  # noqa: E402

# Monday 2026-11-09, 07:00 at the installation: the page's week runs Monday
# 2026-11-09 to Sunday 2026-11-15.
_NOW = datetime.datetime(2026, 11, 9, 7, 0, tzinfo=site_base._ART)
_MIERCOLES = "2026-11-11"
_SABADO = "2026-11-14"

_CENTROS = {"centros": [{"slug": "caps-norte", "name": "CAPS Norte", "address": "Calle 1"}]}
_LV_8_12 = {"dias": [1, 2, 3, 4, 5], "desde": "08:00", "hasta": "12:00", "cada_min": 20}


def _servicios(excepciones=None):
    agenda = {"franjas": [dict(_LV_8_12)]}
    if excepciones:
        agenda["excepciones"] = excepciones
    return {"servicios": [
        {"id": "pediatria-norte", "nombre": "Pediatría", "centro": "caps-norte",
         "cluster": "pediatria", "modo": "turno", "agenda": agenda},
        # no agenda of its own: its hours are the windows of the doctors tagged with it
        {"id": "vacunacion-norte", "nombre": "Vacunación", "centro": "caps-norte",
         "cluster": "vacunacion", "modo": "turno"},
        # walk-in, no agenda: it books nothing, so a window tagged with it is no booked hour
        {"id": "control-norte", "nombre": "Control de peso", "centro": "caps-norte",
         "cluster": "pediatria", "modo": "walkin"},
    ]}


_MEDICOS = {"medicos": [
    # no hours of their own: the service's agenda is the only schedule
    {"id": "sin-horas", "nombre": "Ana Sosa", "especialidad": "Pediatría",
     "cluster": "pediatria", "centro": "caps-norte",
     "centros": [{"centro": "caps-norte", "franjas": []}]},
    # own hours the service agenda overrides (the engine books the service's)
    {"id": "con-horas", "nombre": "Luis Paz", "especialidad": "Pediatría",
     "cluster": "pediatria", "centro": "caps-norte",
     "centros": [{"centro": "caps-norte", "franjas": [
         {"dias": [2, 4], "desde": "14:00", "hasta": "18:00", "cada_min": 20}]}]},
    # managed by Pediatría's agenda AND keeps a window of their own, Tuesday
    # 14 to 16, for Vacunación, a service without an agenda: the window the
    # Pediatría save leaves them (`schedule_policy.reset_doctor` keeps a band
    # tagged with another service), and the one Vacunación books
    {"id": "mixto", "nombre": "Eva Ruiz", "especialidad": "Pediatría",
     "cluster": "pediatria", "centro": "caps-norte",
     "centros": [{"centro": "caps-norte", "franjas": [
         {"dias": [2], "desde": "14:00", "hasta": "16:00", "cada_min": 20,
          "servicio": "vacunacion-norte"},
         {"dias": [4], "desde": "14:00", "hasta": "16:00", "cada_min": 20,
          "servicio": "control-norte"}]}]},
    # the same, with Vacunación in the very hours of Pediatría's agenda
    {"id": "mismas-horas", "nombre": "Rita Gil", "especialidad": "Pediatría",
     "cluster": "pediatria", "centro": "caps-norte",
     "centros": [{"centro": "caps-norte", "franjas": [
         dict(_LV_8_12, servicio="vacunacion-norte")]}]},
]}
_MARTES = "2026-11-10"


class DoctorPageServiceAgendaTest(unittest.TestCase):
    servicios = _servicios()

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="doctor-page-agenda-")
        self.addCleanup(shutil.rmtree, self._tmp, True)
        webdir = os.path.join(self._tmp, "webdata")
        os.makedirs(webdir)
        for name, doc in (("centros-salud.json", _CENTROS), ("servicios.json", self.servicios),
                          ("medicos.json", _MEDICOS)):
            with open(os.path.join(webdir, name), "w", encoding="utf-8") as f:
                json.dump(doc, f)
        self.addCleanup(self._clear_caches)
        _env.set_env(self, TURNOS_WEBDATA_DIR=webdir, TURNOS_FAKE_AI="1")
        self._clear_caches()
        for target, attr, value in ((site_base, "_now_art", lambda: _NOW),
                                    (site_base, "_with_overlay", lambda tipo, base: base)):
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)

    @staticmethod
    def _clear_caches():
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        site_base._CACHE.clear()

    def _medico(self, mid):
        return site_doctors._doctor_by_id(mid)

    def _semana(self, mid):
        """{weekday (Sunday 0): [(desde, hasta)]} as the page states it."""
        out = {}
        for fila in site_availability._schedule_lines(self._medico(mid)):
            for d in fila["dias"]:
                out.setdefault(d, []).append((fila["desde"], fila["hasta"]))
        return {d: sorted(v) for d, v in out.items()}

    def _segun_la_agenda(self, mid):
        """The same week through the print's `day_windows` (the slot
        generator's day resolution)."""
        out = {}
        for offset in range(7):
            dia = _NOW.date() + datetime.timedelta(days=offset)
            ventanas = agenda_print.day_windows(self._medico(mid), dia.isoformat())
            if ventanas:
                out[dia.isoweekday() % 7] = sorted((v["desde"], v["hasta"]) for v in ventanas)
        return out


class SinHorasPropias(DoctorPageServiceAgendaTest):
    def test_la_pagina_muestra_las_horas_de_la_agenda_del_servicio(self):
        self.assertEqual(self._semana("sin-horas"), {d: [("08:00", "12:00")] for d in (1, 2, 3, 4, 5)})
        html = site_availability._schedule_table_html(self._medico("sin-horas"), now=_NOW)
        self.assertIn("Lunes a viernes", html)
        self.assertIn("8:00 – 12:00", html)
        self.assertIn("No atiende", html)  # Saturday and Sunday

    def test_la_pagina_dice_lo_mismo_que_la_agenda(self):
        for mid in ("sin-horas", "con-horas"):
            with self.subTest(mid=mid):
                self.assertEqual(self._semana(mid), self._segun_la_agenda(mid))


class ConHorasQueElServicioReemplaza(DoctorPageServiceAgendaTest):
    def test_la_agenda_del_servicio_manda_sobre_las_horas_propias(self):
        self.assertEqual(self._semana("con-horas"), {d: [("08:00", "12:00")] for d in (1, 2, 3, 4, 5)})
        html = site_availability._schedule_table_html(self._medico("con-horas"), now=_NOW)
        self.assertNotIn("14:00", html)


class CerradoYExtraEnLaSemana(DoctorPageServiceAgendaTest):
    servicios = _servicios(excepciones=[
        {"fecha": _MIERCOLES, "tipo": "cerrado"},
        {"fecha": _SABADO, "tipo": "extra", "desde": "10:00", "hasta": "12:00", "cada_min": 20},
    ])

    def test_un_dia_cerrado_no_atiende_y_un_extra_suma_su_ventana(self):
        semana = self._semana("sin-horas")
        self.assertNotIn(3, semana)  # Wednesday closed
        self.assertEqual(semana[6], [("10:00", "12:00")])  # the Saturday extra
        self.assertEqual(semana, self._segun_la_agenda("sin-horas"))
        html = site_availability._schedule_table_html(self._medico("sin-horas"), now=_NOW)
        self.assertIn("Miércoles", html)
        self.assertIn("10:00 – 12:00", html)

    def test_la_cola_del_dia_lee_la_misma_fuente(self):
        m = self._medico("sin-horas")
        self.assertEqual(doctor_appointments._effective_day_hours(m, _MIERCOLES), [])
        self.assertEqual(doctor_appointments._effective_day_hours(m, _SABADO),
                         ["10:00", "10:20", "10:40", "11:00", "11:20", "11:40"])


class AgendaDelServicioYHorasPropiasParaOtroServicio(DoctorPageServiceAgendaTest):
    """Hub brief of 2026-11-08 (the case the first change left open): a
    service agenda replaces the doctor's own hours only for ITS service; the
    doctor's own window for a service without an agenda stays — the booking
    offers it, so the page, the day queue and the printed sheet show it too."""

    def setUp(self):
        super().setUp()
        # the slot generator keeps the naive wall-clock contract of `_now_art`
        p = mock.patch.object(site_base, "_now_art", lambda: _NOW.replace(tzinfo=None))
        p.start()
        self.addCleanup(p.stop)

    def _servicio(self, sid):
        from app.patient_web import site_catalog
        return site_catalog._service_by_id(sid)

    def _turnos_de_la_semana(self, sid, **kw):
        """{iso day: [hora]} the booking offers for `sid` in the page's week."""
        s = self._servicio(sid)
        if kw.get("medico"):
            ofrecido = site_availability._appointments_for_doctor(s, kw["medico"])
        else:
            ofrecido = site_availability._appointments_for_service(s)
        fin = (_NOW.date() + datetime.timedelta(days=7)).isoformat()
        out = {}
        for bloque in ofrecido.get("medicos") or []:
            for t in bloque.get("turnos") or []:
                if t["dia"] < fin:
                    out.setdefault(t["dia"], []).append(t["hora"])
        return out

    @staticmethod
    def _dentro(ventanas, hora):
        return any(desde <= hora < hasta for desde, hasta in ventanas)

    def test_la_reserva_de_vacunacion_ofrece_el_martes_a_la_tarde(self):
        turnos = self._turnos_de_la_semana("vacunacion-norte", medico="mixto")
        self.assertEqual(turnos.get(_MARTES), ["14:00", "14:20", "14:40", "15:00", "15:20", "15:40"])

    def test_la_pagina_muestra_la_ventana_de_vacunacion(self):
        semana = self._semana("mixto")
        self.assertEqual(semana[2], [("08:00", "12:00"), ("14:00", "16:00")])
        filas = site_availability._schedule_lines(self._medico("mixto"))
        martes = [f for f in filas if f["desde"] == "14:00"]
        self.assertEqual([(f["dias"], f["servicio_id"]) for f in martes], [([2], "vacunacion-norte")])
        html = site_availability._schedule_table_html(self._medico("mixto"), now=_NOW)
        self.assertIn("14:00 – 16:00", html)
        self.assertIn("Vacunación", html)

    def test_una_ventana_de_un_servicio_sin_turnos_no_suma_horas(self):
        """The demo's general practitioners also staff their clinic's
        walk-in desk: that window books nothing, so the page keeps only the
        agenda's hours that Thursday (and the queue and the sheet agree)."""
        self.assertEqual(self._semana("mixto")[4], [("08:00", "12:00")])
        self.assertEqual(self._segun_la_agenda("mixto")[4], [("08:00", "12:00")])

    def test_dos_consultorios_con_la_misma_semana_son_una_franja(self):
        """Two offices booked in the same hours read as one week naming
        both, not as the same strip twice."""
        html = site_availability._schedule_table_html(self._medico("mismas-horas"), now=_NOW)
        self.assertEqual(html.count('class="mp-ag-week"'), 1)
        self.assertEqual(html.count('class="mp-ag-office"'), 1)
        self.assertIn('href="/service/pediatria-norte"', html)
        self.assertIn('href="/service/vacunacion-norte"', html)
        # a wrapped phone line never opens with the separator
        self.assertIn("</a>&nbsp;· <a ", html)
        # Eva Ruiz's two weeks differ: two strips, each with its office
        html = site_availability._schedule_table_html(self._medico("mixto"), now=_NOW)
        self.assertEqual(html.count('class="mp-ag-week"'), 2)
        self.assertEqual(html.count('class="mp-ag-office"'), 2)

    def test_lo_que_se_reserva_esta_en_la_pagina_la_cola_y_la_hoja(self):
        m = self._medico("mixto")
        semana = self._semana("mixto")
        self.assertEqual(semana, self._segun_la_agenda("mixto"))
        for sid, kw in (("vacunacion-norte", {"medico": "mixto"}), ("vacunacion-norte", {}),
                        ("pediatria-norte", {})):
            turnos = self._turnos_de_la_semana(sid, **kw)
            self.assertTrue(turnos, sid)
            for dia, horas in turnos.items():
                weekday = datetime.date.fromisoformat(dia).isoweekday() % 7
                cola = doctor_appointments._effective_day_hours(m, dia)
                hoja = [(v["desde"], v["hasta"]) for v in agenda_print.day_windows(m, dia)]
                for hora in horas:
                    with self.subTest(servicio=sid, dia=dia, hora=hora, **kw):
                        self.assertTrue(self._dentro(semana.get(weekday, []), hora), "página")
                        self.assertIn(hora, cola, "cola del día")
                        self.assertTrue(self._dentro(hoja, hora), "hoja impresa")


if __name__ == "__main__":
    unittest.main()
