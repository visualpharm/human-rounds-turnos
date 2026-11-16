"""test_agenda_consultorio.py — "Cuándo atiende": cada línea de horario de un
profesional dice de qué CONSULTORIO (servicio) es y en qué centro.

reference-deployment counterpart, reunión 2026-07-24: "Funes los lunes de 8 a 12 está en
anticoncepción, pero los martes de 8 a 12 está en consultorio de medicina
general. No hace lo mismo los lunes que los martes" · "qué tipo de consultorio
es, es más importante que la especialidad".

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_agenda_consultorio.py"
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
from app.core import i18n  # noqa: E402
from app.patient_web import (
    site_availability,
    site_base,
    site_catalog,
    site_doctors,
    site_page_doctors,
    site_schedule,
)


_CENTROS = {"centros": [
    {"slug": "caps-valeria", "name": "CAPS Valeria del Mar", "address": "Calle 1"},
    {"slug": "caps-ostende", "name": "CAPS Ostende", "address": "Calle 2"},
]}
_SERVICIOS = {"servicios": [
    {"id": "anticoncepcion", "nombre": "Anticoncepción", "centro": "caps-valeria",
     "cluster": "generalista", "modo": "solicitud"},
    {"id": "medicina-general", "nombre": "Medicina general", "centro": "caps-valeria",
     "cluster": "generalista", "modo": "solicitud"},
    {"id": "medicina-general-ostende", "nombre": "Medicina general", "centro": "caps-ostende",
     "cluster": "generalista", "modo": "solicitud"},
]}
# Funes: el caso del hospital — mismo horario, distinto consultorio según el día.
_MEDICOS = {"medicos": [
    {"id": "funes", "nombre": "Andrea Funes", "especialidad": "Medicina general",
     "cluster": "generalista", "centro": "caps-valeria",
     "centros": [{"centro": "caps-valeria", "franjas": [
         {"dias": [1], "desde": "08:00", "hasta": "12:00", "cada_min": 20,
          "servicio": "anticoncepcion"},
         {"dias": [2], "desde": "08:00", "hasta": "12:00", "cada_min": 20,
          "servicio": "medicina-general"},
     ]}]},
    # sin etiquetar: cae al servicio al que ya lleva el botón de la página
    {"id": "sin-etiqueta", "nombre": "Ana Pérez", "especialidad": "Medicina general",
     "cluster": "generalista", "centro": "caps-valeria",
     "centros": [{"centro": "caps-valeria", "franjas": [
         {"dias": [3, 4, 5], "desde": "09:00", "hasta": "11:00", "cada_min": 20}]}]},
    # dos sedes: cada fila con el servicio de SU centro
    {"id": "dos-sedes", "nombre": "Luis Rivas", "especialidad": "Medicina general",
     "cluster": "generalista", "centro": "caps-valeria",
     "centros": [
         {"centro": "caps-valeria", "franjas": [{"dias": [1], "desde": "08:00", "hasta": "10:00", "cada_min": 20}]},
         {"centro": "caps-ostende", "franjas": [{"dias": [4], "desde": "08:00", "hasta": "10:00", "cada_min": 20}]},
     ]},
    # sin agenda: no hay tabla que dibujar
    {"id": "sin-agenda", "nombre": "Rosa Díaz", "especialidad": "Medicina general",
     "cluster": "generalista", "centro": "caps-valeria"},
]}


class AgendaConsultorioTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="agenda-consultorio-")
        webdir = os.path.join(self._tmp, "webdata")
        os.makedirs(webdir)
        for name, doc in (("centros-salud.json", _CENTROS), ("servicios.json", _SERVICIOS),
                          ("medicos.json", _MEDICOS)):
            with open(os.path.join(webdir, name), "w", encoding="utf-8") as f:
                json.dump(doc, f)
        self.addCleanup(self._clear_caches)  # registered first: runs once the env is back
        _env.set_env(self, TURNOS_WEBDATA_DIR=webdir)
        self._clear_caches()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    @staticmethod
    def _clear_caches():
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        site_base._CACHE.clear()

    def _lineas(self, mid):
        return site_availability._schedule_lines(site_doctors._doctor_by_id(mid))

    # ---- días en texto ------------------------------------------------

    def test_dias_en_castellano(self):
        casos = {
            (1,): "Lunes",
            (1, 2, 3): "Lunes a miércoles",
            (1, 2, 3, 4, 5): "Lunes a viernes",
            (1, 4): "Lunes y jueves",
            (1, 2, 4, 5): "Lunes, martes, jueves y viernes",
            (1, 2, 3, 5): "Lunes a miércoles y viernes",
            (6, 0): "Sábado y domingo",   # 0 = domingo (convención Date.getDay)
        }
        for dias, esperado in casos.items():
            self.assertEqual(site_schedule._days_text(list(dias)), esperado, dias)
        self.assertEqual(site_schedule._days_text([]), "")

    def test_dias_en_ingles_conector_y_mayuscula_correctos(self):
        """Bug real, demo, 2026-08-05 (encontrado por el chequeo en navegador
        real, no por un test): la tabla "Cuándo atiende" de la página propia
        del médico decía "Monday a friday" en inglés — _days_text() bajaba el
        segundo día SIEMPRE y nunca traducía el conector "a"/"y", a
        diferencia de _horario_display() (arriba) que sí lo hace para el
        texto libre de talleres. El inglés capitaliza los dos días de un
        rango y no baja nada."""
        casos = {
            (1,): "Monday",
            (1, 2, 3): "Monday to Wednesday",
            (1, 2, 3, 4, 5): "Monday to Friday",
            (1, 4): "Monday and Thursday",
            (1, 2, 4, 5): "Monday, Tuesday, Thursday and Friday",
            (1, 2, 3, 5): "Monday to Wednesday and Friday",
        }
        _, tok = i18n.bind_lang("en")
        try:
            for dias, esperado in casos.items():
                self.assertEqual(site_schedule._days_text(list(dias)), esperado, dias)
        finally:
            # unbind(), no bind_lang("es") — dejar el idioma FORZADO a "es" al
            # salir filtra estado a otros tests del mismo proceso (ContextVar
            # compartida): rompió test_booking_requests.py, que asume
            # i18n._ACTIVE.get() es None fuera de un request real (2026-08-05).
            i18n.unbind(tok)
        # castellano no se movió (instalación de un solo idioma, byte a byte).
        self.assertEqual(site_schedule._days_text([1, 2, 3, 4, 5]), "Lunes a viernes")

    # ---- el consultorio de cada franja --------------------------------

    def test_mismo_horario_distinto_consultorio_segun_el_dia(self):
        filas = self._lineas("funes")
        self.assertEqual(len(filas), 2)
        self.assertEqual([(f["dias_txt"], f["servicio_nombre"]) for f in filas],
                         [("Lunes", "Anticoncepción"), ("Martes", "Medicina general")])
        self.assertTrue(all(f["etiquetado"] for f in filas))

    def test_sin_etiquetar_cae_al_servicio_de_la_propia_pagina(self):
        filas = self._lineas("sin-etiqueta")
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["servicio_id"], "medicina-general")
        self.assertFalse(filas[0]["etiquetado"])

    def test_dos_sedes_cada_una_con_el_servicio_de_su_centro(self):
        filas = self._lineas("dos-sedes")
        por_centro = {f["centro"]: f["servicio_id"] for f in filas}
        self.assertEqual(por_centro["caps-valeria"], "medicina-general")
        self.assertEqual(por_centro["caps-ostende"], "medicina-general-ostende")

    def test_sin_agenda_no_hay_tabla(self):
        self.assertEqual(self._lineas("sin-agenda"), [])
        self.assertEqual(site_availability._schedule_table_html(site_doctors._doctor_by_id("sin-agenda")), "")

    def test_las_filas_salen_ordenadas_por_dia_y_hora(self):
        filas = self._lineas("funes")
        self.assertEqual([f["desde"] for f in filas], ["08:00", "08:00"])
        self.assertEqual([min(f["dias"]) for f in filas], [1, 2])

    # ---- la tabla ------------------------------------------------------

    def test_distintos_consultorios_se_agrupan_y_linkean_al_servicio(self):
        html = site_availability._schedule_table_html(site_doctors._doctor_by_id("funes"))
        self.assertIn("Cuándo atiende", html)
        # The band names the service, never «Consultorio <service>» (review 2026-11-15).
        self.assertNotIn("Consultorio", html)
        self.assertIn('href="/service/anticoncepcion"', html)
        self.assertIn('href="/service/medicina-general"', html)

    def test_un_consultorio_comun_no_se_repite(self):
        html = site_availability._schedule_table_html(site_doctors._doctor_by_id("sin-etiqueta"))
        self.assertNotIn("Consultorio", html)
        self.assertNotIn("Medicina general", html)
        self.assertIn("CAPS Valeria del Mar", html)
        self.assertIn("Miércoles a viernes", html)

    def test_centro_comun_va_antes_de_sus_dias(self):
        html = site_availability._schedule_table_html(site_doctors._doctor_by_id("dos-sedes"))
        self.assertLess(html.index("CAPS Valeria del Mar"), html.index("Lunes"))
        self.assertLess(html.index("CAPS Ostende"), html.index("Jueves"))

    def test_horario_no_dice_a_en_ningun_idioma(self):
        """Mismo bug real del 2026-08-05 que _days_text() de arriba: el
        horario se armaba "08:00 a 12:00" a mano y en inglés seguía diciendo
        "a". Desde el rediseño A (2026-10-06) el tramo usa una raya, que no
        se traduce: el mismo texto en los dos idiomas."""
        _, tok = i18n.bind_lang("en")
        try:
            html = site_availability._schedule_table_html(site_doctors._doctor_by_id("funes"))
            self.assertIn("8:00 – 12:00", html)
            self.assertNotIn("08:00 a 12:00", html)
        finally:
            i18n.unbind(tok)
        html = site_availability._schedule_table_html(site_doctors._doctor_by_id("funes"))
        self.assertIn("8:00 – 12:00", html)
        self.assertNotIn("08:00 a 12:00", html)

    # ---- semana en bloques (diseño A, responsable del producto 2026-10-06) --------------

    _MARTES = datetime.datetime(2026, 10, 6, 10, 0)

    def test_dias_seguidos_con_el_mismo_horario_son_un_bloque(self):
        filas = [{"dias": [3], "desde": "08:00", "hasta": "14:00"},
                 {"dias": [4], "desde": "08:00", "hasta": "14:00"}]
        bloques = site_schedule._week_blocks(filas, 1)
        self.assertEqual([b["label"] for b in bloques],
                         ["Lunes y martes", "Miércoles y jueves", "Viernes a domingo"])
        self.assertEqual([len(b["pos"]) for b in bloques], [2, 2, 3])
        self.assertEqual(bloques[1]["horas"], "8:00 – 14:00")
        self.assertEqual(bloques[0]["horas"], "")

    def test_franjas_superpuestas_se_unen(self):
        """Caso real (Dra. Lorenzini, 2026-10-06): miércoles 08-14 y 10-12,
        jueves 08-12 y 08-14 — se leían como cuatro renglones repetidos."""
        filas = [{"dias": [3], "desde": "08:00", "hasta": "14:00"},
                 {"dias": [3], "desde": "10:00", "hasta": "12:00"},
                 {"dias": [4], "desde": "08:00", "hasta": "12:00"},
                 {"dias": [4], "desde": "08:00", "hasta": "14:00"}]
        bloques = site_schedule._week_blocks(filas, 1)
        self.assertEqual(bloques[1]["label"], "Miércoles y jueves")
        self.assertEqual(bloques[1]["horas"], "8:00 – 14:00")

    def test_dos_tramos_separados_van_con_coma(self):
        self.assertEqual(site_schedule._day_hours_text([("08:00", "12:00"), ("16:00", "19:00")]),
                         "8:00 – 12:00, 16:00 – 19:00")

    def test_hoy_completo_en_bloque_y_solo_hoy_en_dia_suelto(self):
        filas = [{"dias": [2], "desde": "08:00", "hasta": "12:00"}]
        solo = site_schedule._week_blocks(filas, 1)
        self.assertEqual([b["hoy"] for b in solo if b["hoy"]], ["Hoy"])
        junto = site_schedule._week_blocks(filas, 3)
        self.assertEqual([b["hoy"] for b in junto if b["hoy"]], ["Hoy, jueves"])

    def test_la_tabla_marca_hoy_y_dice_no_atiende(self):
        html = site_availability._schedule_table_html(site_doctors._doctor_by_id("funes"),
                                                 now=self._MARTES)
        self.assertIn('<span class="mp-ag-hoy">Hoy</span>', html)
        self.assertIn("No atiende", html)
        self.assertIn("mp-ag-b--on", html)

    def test_con_un_solo_centro_se_nombra_una_vez(self):
        html = site_availability._schedule_table_html(site_doctors._doctor_by_id("funes"))
        self.assertEqual(html.count("CAPS Valeria del Mar"), 1)

    def test_con_dos_centros_cada_uno_se_nombra_una_vez(self):
        html = site_availability._schedule_table_html(site_doctors._doctor_by_id("dos-sedes"))
        self.assertEqual(html.count("CAPS Ostende"), 1)
        self.assertEqual(html.count("CAPS Valeria del Mar"), 1)

    def test_la_pagina_del_medico_la_incluye(self):
        html = site_page_doctors._page_doctor("funes").decode("utf-8")
        self.assertIn("mp-agenda", html)
        self.assertIn("Anticoncepción", html)
        self.assertIn("Medicina general", html)


if __name__ == "__main__":
    unittest.main()


class LugarEnElWizardTestCase(AgendaConsultorioTestCase):
    """El paso "Elegí un turno" dice DÓNDE se atiende (responsable del producto, 2026-07-27: "you
    should show the medical centers here too"). Elegir una hora sin saber a
    qué centro ir es elegir a medias — y acá los centros están a diez minutos
    entre sí, así que el dato cambia la decisión."""

    def _con_agenda(self):
        """Agenda ancha en el servicio, para que siempre haya horarios."""
        servicios = json.loads(json.dumps(_SERVICIOS))
        for s in servicios["servicios"]:
            s["agenda"] = {"dias": [0, 1, 2, 3, 4, 5, 6], "desde": "00:00",
                           "hasta": "23:59", "cada_min": 60}
        with open(os.path.join(os.environ["TURNOS_WEBDATA_DIR"], "servicios.json"),
                  "w", encoding="utf-8") as f:
            json.dump(servicios, f)
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        site_base._CACHE.clear()

    def test_cada_bloque_de_horarios_dice_donde_es(self):
        self._con_agenda()
        s = site_catalog._service_by_id("medicina-general")
        data = site_availability._appointments_for_service(s)
        self.assertTrue(data.get("medicos"))
        for m in data["medicos"]:
            self.assertEqual(m["centro"], "caps-valeria")
            self.assertEqual(m["lugar"], "CAPS Valeria del Mar")

    def test_los_horarios_de_un_medico_tambien(self):
        self._con_agenda()
        s = site_catalog._service_by_id("anticoncepcion")
        data = site_availability._appointments_for_doctor(s, "funes")
        self.assertTrue(data.get("medicos"))
        self.assertEqual(data["medicos"][0]["lugar"], "CAPS Valeria del Mar")

    def test_el_bloque_dice_de_que_es_el_medico_y_linkea(self):
        # El nombre solo no alcanza para elegir: la especialidad es lo que dice
        # si esa persona es quien corresponde (responsable del producto, 2026-07-28). Va con su
        # link, igual que el centro.
        self._con_agenda()
        s = site_catalog._service_by_id("anticoncepcion")
        bloque = site_availability._appointments_for_doctor(s, "funes")["medicos"][0]
        self.assertTrue(bloque["especialidad"])
        self.assertTrue(bloque["esp_href"].startswith("/d/"))
        self.assertEqual(bloque["centro_href"], "/center/caps-valeria")

    def test_sin_ficha_en_el_catalogo_no_se_inventa_especialidad(self):
        self.assertEqual(site_availability._doctor_meta(None),
                         {"especialidad": "", "esp_href": ""})

    def test_con_sede_se_dicen_las_dos(self):
        # la sede sola no dice a qué centro ir; el centro solo deja a la
        # persona parada en la puerta equivocada
        servicios = json.loads(json.dumps(_SERVICIOS))
        servicios["servicios"].append(
            {"id": "fono", "nombre": "Fonoaudiología", "centro": "caps-ostende",
             "sede": "Desarrollo Infantil", "cluster": "generalista", "modo": "solicitud",
             "agenda": {"dias": [0, 1, 2, 3, 4, 5, 6], "desde": "00:00",
                        "hasta": "23:59", "cada_min": 60}})
        with open(os.path.join(os.environ["TURNOS_WEBDATA_DIR"], "servicios.json"),
                  "w", encoding="utf-8") as f:
            json.dump(servicios, f)
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        site_base._CACHE.clear()
        lugar = site_availability._place_of(site_catalog._service_by_id("fono"))
        self.assertEqual(lugar["lugar"], "Desarrollo Infantil · CAPS Ostende")
        self.assertEqual(lugar["sede"], "Desarrollo Infantil")
        self.assertEqual(lugar["centro_nombre"], "CAPS Ostende")

    def test_el_medico_preferido_no_se_duplica_abajo_sin_nombre(self):
        # La agenda del servicio se arma con las franjas de estos mismos
        # médicos: repetir abajo, como "Horarios", los minutos que arriba ya
        # figuran con nombre y apellido los hacía parecer turnos distintos
        # (the product owner, captura 2026-07-27).
        self._con_agenda()
        s = site_catalog._service_by_id("anticoncepcion")
        data = site_availability._appointments_for_service_preferred(s, "funes")
        bloques = data.get("medicos") or []
        self.assertTrue(bloques)
        self.assertEqual(bloques[0]["medico"], "Andrea Funes")
        nombrados = {(t["dia"], t["hora"]) for b in bloques if b["medico"]
                     for t in b["turnos"]}
        for b in bloques:
            if b["medico"]:
                continue
            repetidos = [t for t in b["turnos"] if (t["dia"], t["hora"]) in nombrados]
            self.assertEqual(repetidos, [], "un turno no puede salir dos veces")


class TurnosPorConsultorioTestCase(AgendaConsultorioTestCase):
    """Lo que se OFRECE, no sólo lo que se muestra.

    La etiqueta `franjas[].servicio` sólo se usaba para dibujar "Cuándo
    atiende": la grilla del wizard seguía juntando la agenda ENTERA de cada
    médico del cluster, así que los lunes de anticoncepción de Funes se
    ofrecían como turnos de medicina general — justo lo que la reunión pidió
    separar ("no hace lo mismo los lunes que los martes").
    """

    def setUp(self):
        super().setUp()
        # Los médicos reales traen las MISMAS franjas en `agenda` y en
        # `centros[]` (93 de 93 en webdata): la grilla del servicio lee
        # `agenda`, la del médico lee `centros[]`. El fixture de arriba sólo
        # tiene `centros[]`, así que acá se espeja para probar las dos puntas.
        medicos = json.loads(json.dumps(_MEDICOS))
        for m in medicos["medicos"]:
            propios = [c for c in (m.get("centros") or []) if c.get("centro") == m.get("centro")]
            if propios:
                m["agenda"] = {"franjas": propios[0]["franjas"]}
        with open(os.path.join(os.environ["TURNOS_WEBDATA_DIR"], "medicos.json"),
                  "w", encoding="utf-8") as f:
            json.dump(medicos, f)
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        site_base._CACHE.clear()

    @staticmethod
    def _dias_de(data):
        import datetime
        out = set()
        for m in data.get("medicos") or []:
            for t in m.get("turnos") or []:
                out.add(datetime.date.fromisoformat(t["dia"]).isoweekday() % 7)
        return out

    def test_la_grilla_de_un_medico_es_la_de_ese_consultorio(self):
        s = site_catalog._service_by_id("medicina-general")
        dias = self._dias_de(site_availability._appointments_for_doctor(s, "funes"))
        self.assertEqual(dias, {2}, "los martes son de medicina general")

        s = site_catalog._service_by_id("anticoncepcion")
        dias = self._dias_de(site_availability._appointments_for_doctor(s, "funes"))
        self.assertEqual(dias, {1}, "los lunes son de anticoncepción")

    def test_el_servicio_no_junta_las_franjas_de_otro_consultorio(self):
        s = site_catalog._service_by_id("anticoncepcion")
        dias = self._dias_de(site_availability._appointments_for_service(s))
        self.assertNotIn(2, dias, "el martes es de medicina general, no de acá")

    def test_una_franja_sin_etiquetar_sigue_valiendo(self):
        # La mayor parte del catálogo todavía no está etiquetada: filtrarla
        # dejaría servicios enteros sin un solo turno.
        for sid in ("anticoncepcion", "medicina-general"):
            s = site_catalog._service_by_id(sid)
            dias = self._dias_de(site_availability._appointments_for_doctor(s, "sin-etiqueta"))
            self.assertEqual(dias, {3, 4, 5}, sid)

    def test_lo_que_promete_la_banda_es_del_mismo_consultorio(self):
        # Invariante de siempre: lo que promete la banda tiene que estar en la
        # grilla. Si se filtra en una punta y no en la otra, se rompe.
        import datetime
        s = site_catalog._service_by_id("anticoncepcion")
        best = site_availability._next_appointment_best(s)
        self.assertIsNotNone(best)
        self.assertNotEqual(datetime.date.fromisoformat(best[0]).isoweekday() % 7, 2,
                            "un martes es de medicina general")
        ofrecidos = {(t["dia"], t["hora"])
                     for m in (site_availability._appointments_for_service(s).get("medicos") or [])
                     for t in m.get("turnos") or []}
        self.assertIn(best, ofrecidos)
