"""test_demo_agenda.py — próximo turno de médicos con una `agenda_demo` inventada
(site_availability._demo_next / _doctor_next). Antes probaba contra los 5 médicos
`demo:true` reales de webdata/medicos.json (cardiología, diagnóstico, endocrino,
salud mental); esos médicos inventados se DIERON DE BAJA el 2026-07-16 (la
planilla oficial ya cubre esos clusters con profesionales reales) — este archivo
sigue probando la MISMA lógica de cómputo de agenda contra fixtures sintéticos,
para no perder cobertura del código de patient_web/site_*.py que sigue vivo (un médico
`demo:true` con `agenda_demo` sigue siendo un caso soportado, aunque hoy no haya
ninguno cargado).

Correr: python3 -m unittest discover -s tests -p "test_*.py"   (desde la raíz)
"""
import datetime
import unittest

from app.patient_web import site_availability


# Formas de agenda_demo representativas de las que tenían los 5 médicos demo
# reales (lun/jue, mar/vie, un solo día, etc.) — ya no viven en webdata, pero
# la forma del dato sigue siendo válida y patient_web/site_*.py la sigue soportando.
_AGENDAS_SINTETICAS = [
    {"dias": [1, 4], "desde": "09:00", "hasta": "12:00", "cada_min": 20},
    {"dias": [2, 5], "desde": "14:00", "hasta": "17:00", "cada_min": 20},
    {"dias": [3], "desde": "08:00", "hasta": "11:00", "cada_min": 15},
    {"dias": [2, 4], "desde": "10:00", "hasta": "13:00", "cada_min": 20},
    {"dias": [5], "desde": "09:00", "hasta": "12:00", "cada_min": 30},
]


class TestDemoProximo(unittest.TestCase):
    def test_hoy_matchea_y_queda_ventana(self):
        # "hoy" = lunes 2026-07-06 10:05, agenda lun/jue 09:00-12:00 cada 20' →
        # el próximo slot es HOY mismo, el primer boundary estrictamente futuro
        # (10:00 ya pasó, el siguiente boundary es 10:20).
        hoy = datetime.datetime(2026, 7, 6, 10, 5)  # lunes
        agenda = {"dias": [1, 4], "desde": "09:00", "hasta": "12:00", "cada_min": 20}
        dia, hora = site_availability._demo_next(agenda, now=hoy)
        self.assertEqual(dia, "2026-07-06")
        self.assertEqual(hora, "10:20")

    def test_hoy_matchea_pero_ventana_ya_paso(self):
        # mismo lunes pero ya son las 13:00 (después de las 12:00) → salta al
        # próximo día de la agenda, jueves.
        hoy = datetime.datetime(2026, 7, 6, 13, 0)  # lunes, ventana cerrada
        agenda = {"dias": [1, 4], "desde": "09:00", "hasta": "12:00", "cada_min": 20}
        dia, hora = site_availability._demo_next(agenda, now=hoy)
        self.assertEqual(dia, "2026-07-09")  # jueves siguiente
        self.assertEqual(hora, "09:00")

    def test_dia_unico_ya_paso_salta_a_la_semana_que_viene(self):
        # agenda de un solo día (miércoles); si ya pasó esta semana, el próximo
        # slot es el miércoles de la semana siguiente (siempre futuro).
        hoy = datetime.datetime(2026, 7, 9, 15, 0)  # jueves (miércoles ya pasó)
        agenda = {"dias": [3], "desde": "08:00", "hasta": "11:00", "cada_min": 15}
        dia, hora = site_availability._demo_next(agenda, now=hoy)
        self.assertEqual(dia, "2026-07-15")  # miércoles siguiente
        self.assertEqual(hora, "08:00")
        # siempre en el futuro respecto de "hoy"
        d = datetime.datetime.combine(datetime.date.fromisoformat(dia),
                                       datetime.time.fromisoformat(hora))
        self.assertGreater(d, hoy)

    def test_sin_dias_devuelve_none(self):
        self.assertIsNone(site_availability._demo_next({"desde": "09:00", "hasta": "12:00"}))

    def test_agendas_sinteticas_dan_turno_futuro(self):
        # cada forma de agenda_demo (sintética, no de webdata) produce un
        # próximo turno estrictamente futuro respecto de "ahora".
        # `now` inyectado (no el reloj real de _demo_next): un "ahora" propio
        # por agenda podría cruzar un borde de día/franja entre iteraciones
        # del loop y volver la comparación de abajo flaky.
        ahora = datetime.datetime.now()
        for agenda in _AGENDAS_SINTETICAS:
            best = site_availability._demo_next(agenda, now=ahora)
            self.assertIsNotNone(best, agenda)
            slot = datetime.datetime.combine(
                datetime.date.fromisoformat(best[0]),
                datetime.time.fromisoformat(best[1]))
            self.assertGreater(slot, ahora, agenda)


class TestMedicoProximoUsaDemo(unittest.TestCase):
    def test_medico_demo_usa_agenda_no_msd(self):
        # cluster inexistente: sin servicio propio, para que ninguna agenda
        # real importada del hospital aporte un turno por otra vía (mismo
        # truco que test_medico_no_demo_no_usa_agenda_demo, abajo).
        m = {
            "id": "synthetic-demo-medico", "nombre": "Demo Sintético",
            "cluster": "cluster-inexistente", "centro": "hospital-pinamar",
            "demo": True,
            "agenda_demo": {"dias": [1, 4], "desde": "09:00", "hasta": "12:00", "cada_min": 20},
        }
        px = site_availability._doctor_next(m)
        self.assertEqual(px["kind"], "turno")
        # sort empieza con "0" (turno real), no con "1"/"2" (walkin/solicitud)
        self.assertTrue(px["sort"].startswith("0"))

    def test_medico_no_demo_no_usa_agenda_demo(self):
        # un médico real sin `demo` nunca pasa por _demo_next aunque tuviera
        # (por error) agenda_demo — _doctor_next sólo la mira si demo:true.
        # cluster inexistente: sin servicio propio, para que ninguna agenda
        # real importada del hospital aporte un turno por otra vía.
        m = {"nombre": "Alguien Real", "cluster": "cluster-inexistente", "centro": "hospital-pinamar",
             "agenda_demo": {"dias": [1], "desde": "09:00", "hasta": "10:00"}}
        px = site_availability._doctor_next(m)
        # sin dump MSD que matchee y sin demo:true → cae a la etiqueta honesta
        self.assertNotEqual(px.get("kind"), "turno")


class TestProximoMultiFranjas(unittest.TestCase):
    """agenda = {franjas:[...], excepciones:[...]} (2026-07-11) — un médico
    puede tener horarios distintos en distintos días, y excepciones puntuales
    (vacaciones/licencia = cerrado, guardia extra = extra)."""

    def test_dos_franjas_distintos_dias_y_horarios(self):
        # lunes/jueves 9-12 Y martes 14-17 en el mismo centro.
        hoy = datetime.datetime(2026, 7, 6, 7, 0)  # lunes temprano
        agenda = {"franjas": [
            {"dias": [1, 4], "desde": "09:00", "hasta": "12:00", "cada_min": 20},
            {"dias": [2], "desde": "14:00", "hasta": "17:00", "cada_min": 30},
        ]}
        dia, hora = site_availability._demo_next(agenda, now=hoy)
        self.assertEqual(dia, "2026-07-06")  # lunes: su franja abre a las 9
        self.assertEqual(hora, "09:00")

    def test_franja_del_martes_se_usa_cuando_lunes_ya_paso(self):
        hoy = datetime.datetime(2026, 7, 6, 13, 0)  # lunes, ventana 9-12 cerrada
        agenda = {"franjas": [
            {"dias": [1, 4], "desde": "09:00", "hasta": "12:00", "cada_min": 20},
            {"dias": [2], "desde": "14:00", "hasta": "17:00", "cada_min": 30},
        ]}
        dia, hora = site_availability._demo_next(agenda, now=hoy)
        self.assertEqual(dia, "2026-07-07")  # martes
        self.assertEqual(hora, "14:00")

    def test_excepcion_cerrado_salta_ese_dia_aunque_matchee_franja(self):
        # vacaciones el lunes 6/7: aunque su franja semanal es lunes/jueves,
        # el lunes 6/7 puntual queda cerrado -> el próximo turno es el jueves.
        hoy = datetime.datetime(2026, 7, 6, 7, 0)
        agenda = {
            "franjas": [{"dias": [1, 4], "desde": "09:00", "hasta": "12:00", "cada_min": 20}],
            "excepciones": [{"fecha": "2026-07-06", "tipo": "cerrado", "motivo": "Vacaciones"}],
        }
        dia, hora = site_availability._demo_next(agenda, now=hoy)
        self.assertEqual(dia, "2026-07-09")  # jueves
        self.assertEqual(hora, "09:00")

    def test_excepcion_extra_abre_un_dia_fuera_de_franja(self):
        # sólo atiende jueves, pero el miércoles 8/7 hay una guardia extra.
        hoy = datetime.datetime(2026, 7, 6, 7, 0)  # lunes
        agenda = {
            "franjas": [{"dias": [4], "desde": "09:00", "hasta": "12:00", "cada_min": 20}],
            "excepciones": [{"fecha": "2026-07-08", "tipo": "extra",
                              "desde": "16:00", "hasta": "18:00", "cada_min": 20,
                              "motivo": "Guardia adicional"}],
        }
        dia, hora = site_availability._demo_next(agenda, now=hoy)
        self.assertEqual(dia, "2026-07-08")  # miércoles, antes que el jueves
        self.assertEqual(hora, "16:00")

    def test_forma_vieja_plana_sigue_funcionando(self):
        # back-compat explícito: {dias,desde,hasta,cada_min} sin "franjas".
        hoy = datetime.datetime(2026, 7, 6, 10, 5)
        agenda = {"dias": [1, 4], "desde": "09:00", "hasta": "12:00", "cada_min": 20}
        dia, hora = site_availability._demo_next(agenda, now=hoy)
        self.assertEqual(dia, "2026-07-06")
        self.assertEqual(hora, "10:20")


if __name__ == "__main__":
    unittest.main()
