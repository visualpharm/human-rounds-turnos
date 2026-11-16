"""test_medico_page.py — página propia del médico (site_page_doctors._page_doctor) y el
ruteo de la fila del listado (_doctor_listing_row) hacia /doctor/<id>.

Correr:  python3 -m unittest test.test_medico_page -v   (desde la raíz)

Sin base ni red: opera sobre medicos.json (packs/ar-b-pinamar/, movido
2026-07-30) + centros/servicios.
"""
import json
import os
import re
import unittest

import _env
from app.core import i18n
from app.patient_web import (
    site_availability,
    site_base,
    site_catalog,
    site_centers,
    site_doctors,
    site_listing,
    site_page_centers,
    site_page_doctors,
    site_page_service,
)
from app.core import sitedata
from _packtest import reference_pack_case, reference_pack_test

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)


def _meds():
    with open(sitedata.path("medicos.json"), encoding="utf-8") as f:
        return json.load(f)["medicos"]


class TestDoctorsListingFilters(unittest.TestCase):
    """/doctors starts with every row (demo review 15.11: the account's
    «Adultos» hid every pediatrician, «Sánchez» came up empty)."""

    def test_doctors_never_preloads_the_account_into_its_filters(self):
        html = site_page_doctors._page_doctors().decode("utf-8")
        data = re.search(r'<script type="application/json" class="lst-data">(.*?)</script>', html, re.S).group(1)
        self.assertIs(json.loads(data)["options"]["profile"], False)


class TestGuessSex(unittest.TestCase):
    def test_conocidos(self):
        self.assertEqual(site_doctors._guess_sex("Julian Luna"), "m")
        self.assertEqual(site_doctors._guess_sex("Laura Diana Uriarte"), "f")
        self.assertEqual(site_doctors._guess_sex("Hugo Montes"), "m")
        self.assertEqual(site_doctors._guess_sex("Andrea Figueroa"), "f")
        self.assertEqual(site_doctors._guess_sex("Matias Arias"), "m")
        self.assertEqual(site_doctors._guess_sex("Hernan Gallo"), "m")
        self.assertEqual(site_doctors._guess_sex("Ines Campos"), "f")

    def test_no_inventa(self):
        # nombre no derivable → None (no se pone Dr./Dra. al azar)
        self.assertIsNone(site_doctors._guess_sex(""))
        self.assertIsNone(site_doctors._guess_sex("Xyz Qwe"))


class TestRowHref(unittest.TestCase):
    def test_fila_apunta_a_medico(self):
        for m in _meds():
            row = site_listing._doctor_listing_row(m)
            self.assertEqual(row["href"], "/doctor/" + m["id"])

    def test_fila_lleva_el_id_del_medico(self):
        # The panel's Solicitudes filter keys a doctor by id and reads the
        # name from this same /api/doctors payload: without the id no lookup
        # ever matched, so eight slot-only requests read «id:…» (2026-10-23).
        for m in _meds():
            self.assertEqual(site_listing._doctor_listing_row(m)["id"], m["id"])


class TestPageMedico(unittest.TestCase):
    def test_todos_los_medicos_renderizan(self):
        for m in _meds():
            body = site_page_doctors._page_doctor(m["id"])
            self.assertIsNotNone(body, "sin página para " + m["id"])
            self.assertIsInstance(body, bytes)

    def test_desconocido_es_none(self):
        self.assertIsNone(site_page_doctors._page_doctor("no-existe-xyz"))

    @reference_pack_test
    def test_contenido_clave(self):
        html = site_page_doctors._page_doctor("luna-julian").decode("utf-8")
        # nombre con título derivable
        self.assertIn("Dr. Julian Luna", html)
        # banda de próximo turno + acción primaria única (el CTA lleva la
        # variante de color de los listados: mp-cta--ya/semana/lejos/ink)
        self.assertIn("mp-band", html)
        self.assertIn("mp-cta", html)
        self.assertEqual(
            html.count('class="mp-cta"') + html.count('class="mp-cta '), 1)
        # especialidad y centro (link) en el subtítulo
        self.assertIn("Cardiología", html)
        self.assertIn("/center/", html)
        # JSON-LD Physician
        self.assertIn('"@type": "Physician"', html)
        # sin botón "atrás" / "volver" (ui-rules)
        self.assertNotIn(">Volver<", html)

    def test_cta_lleva_a_servicio_o_centro(self):
        for m in _meds():
            html = site_page_doctors._page_doctor(m["id"]).decode("utf-8")
            self.assertTrue('href="/service/' in html or 'href="/center/' in html
                            or 'href="/centers"' in html)

    @reference_pack_test
    def test_agenda_de_dos_centros_agrupa_los_dos_centros_y_omite_consultorio_comun(self):
        m = next(x for x in _meds() if x["id"] == "roldan-martin")
        html = site_availability._schedule_table_html(m)
        self.assertEqual(html.count("CAPS Pinamar"), 1)
        self.assertEqual(html.count("CAPS Ostende"), 1)
        self.assertLess(html.index("CAPS Pinamar"), html.index("Lunes"))
        self.assertLess(html.index("CAPS Ostende"), html.index("Miércoles"))
        self.assertLess(html.index("CAPS Ostende"), html.index("Viernes"))
        self.assertNotIn("Consultorio", html)

    @reference_pack_test
    def test_breadcrumb_no_repite_cluster_y_servicio_con_el_mismo_nombre(self):
        s = site_catalog._service_by_id("pediatria-ostende")
        c = site_centers._centers_by_slug()["caps-ostende"]
        html = site_page_service._service_breadcrumb(s, c, include_service=True)
        self.assertEqual(html.count("Pediatría"), 1)


class TestMedicoWalkin(unittest.TestCase):
    """Médico de un servicio walk-in (por orden de llegada): sin botón de sacar
    turno y sin turno de agenda — la banda dice la apertura (responsable del producto, 2026-07-10)."""

    def _walkins(self):
        out = []
        for m in _meds():
            s = site_doctors._doctor_service(m)
            if s and s.get("modo") in ("walkin", "walkin_con_orden"):
                out.append((m, s))
        return out

    @reference_pack_test
    def test_hay_medicos_walkin(self):
        # Odontología sin turno en CAPS Ostende (Detalle de turnos 12-07-26)
        # es walk-in, y al menos uno de sus médicos lo muestra.
        self.assertIn("odontologia-ostende", [s["id"] for _m, s in self._walkins()])

    def test_proximo_nunca_es_turno_de_agenda(self):
        for m, s in self._walkins():
            px = site_availability._doctor_next(m)
            self.assertEqual(px.get("kind"), "walkin", m["id"])
            self.assertNotIn("dia", px, m["id"])  # sin slot de agenda
            if s.get("horario"):
                self.assertEqual(px.get("horario"), s["horario"], m["id"])

    def test_pagina_sin_boton_sacar_turno(self):
        for m, s in self._walkins():
            html = site_page_doctors._page_doctor(m["id"]).decode("utf-8")
            self.assertNotIn('class="mp-cta"', html, m["id"])
            self.assertNotIn("Sacar turno", html, m["id"])
            self.assertNotIn("Próximo turno disponible", html, m["id"])
            # banda de apertura con el horario del cartel + estado en vivo
            self.assertIn("data-horario=", html, m["id"])
            from app.core import static_fingerprint
            self.assertIn(static_fingerprint.url("platform/hours.mjs"), html, m["id"])
            # La semana se muestra siempre, de una de las dos formas: la tabla
            # "Cuándo atiende" cuando el médico tiene franjas reales (dice
            # además de qué consultorio es cada horario — pedido del hospital,
            # 2026-07-24), o la grilla estilo "store hours" del cartel cuando
            # lo único que hay es el texto del horario.
            if site_availability._schedule_lines(m):
                self.assertIn("mp-agenda", html, m["id"])
                agenda_html = site_availability._schedule_table_html(m)
                servicios = {f["servicio_nombre"].casefold()
                             for f in site_availability._schedule_lines(m)
                             if f["servicio_nombre"]}
                if len(servicios) > 1:
                    self.assertIn("Consultorio", agenda_html, m["id"])
                else:
                    self.assertNotIn("Consultorio", agenda_html, m["id"])
            else:
                self.assertIn("data-semana", html, m["id"])
            self.assertIn(static_fingerprint.url("platform/week.mjs"), html, m["id"])
            if s.get("horario"):
                self.assertIn(site_base._esc(s["horario"]), html, m["id"])

    @reference_pack_test
    def test_solicitud_mantiene_cta(self):
        # un médico de servicio a solicitud conserva su única acción primaria
        # (con la variante de color de los listados en la clase)
        html = site_page_doctors._page_doctor("luna-julian").decode("utf-8")
        self.assertIn('class="mp-cta', html)
        self.assertNotIn("data-horario=", html)

    @reference_pack_test
    def test_la_banda_del_medico_conserva_la_etiqueta_con_orden(self):
        # En la página del SERVICIO la etiqueta murió (el cartel "Cómo
        # funciona" ya dice la orden), pero acá no hay cartel: la banda es la
        # única mención. Guarda contra una limpieza global de mp-band-badge.
        html = site_page_doctors._page_doctor("luna-julian").decode("utf-8")
        self.assertIn("mp-band-badge", html)

    def test_mas_horarios_lleva_a_elegir_el_turno(self):
        # "Más horarios" de la banda abre el wizard en "Cuándo" (?slots=1);
        # sin el flag caía en "Subí tu orden" (responsable del producto, 2026-07-15)
        vistos = 0
        for m in _meds():
            html = site_page_doctors._page_doctor(m["id"]).decode("utf-8")
            for href in re.findall(r'class="mp-more" href="([^"]+)"', html):
                vistos += 1
                self.assertIn("?slots=1", href, (m["id"], href))
                self.assertIn("medico=", href, (m["id"], href))
        self.assertTrue(vistos)


class TestPageMedicoIngles(unittest.TestCase):
    """El demo en inglés mostraba la especialidad traducida pero el conector
    ("en") seguía en castellano — el título y la descripción se armaban por
    concatenación en Python, lo que congelaba el orden de las palabras
    (responsable del producto/coordinador, 2026-08-04). Ahora la frase entera sale de UNA clave
    de catálogo con {name}/{specialty}/{city} nombradas."""

    def setUp(self):
        self._token = i18n._ACTIVE.set("en")

    def tearDown(self):
        i18n._ACTIVE.reset(self._token)

    @reference_pack_test
    def test_titulo_no_queda_con_el_conector_en_castellano(self):
        # la especialidad puede o no estar traducida (depende del sweep de
        # traducción de datos de paquete, ajeno a esta unidad) — lo que se
        # prueba acá es el CONECTOR: "in", nunca "en", entre especialidad y
        # ciudad.
        html = site_page_doctors._page_doctor("luna-julian").decode("utf-8")
        self.assertIn("Dr. Julian Luna — ", html)
        self.assertIn(" in Pinamar", html)
        self.assertNotIn(" en Pinamar", html)

    @reference_pack_test
    def test_meta_description_no_queda_con_el_conector_en_castellano(self):
        html = site_page_doctors._page_doctor("luna-julian").decode("utf-8")
        self.assertIn("Dr. Julian Luna, ", html)
        self.assertIn(" in the public health system of Pinamar.", html)
        self.assertNotIn(" en el sistema público de salud de Pinamar.", html)

    def test_ambos_sexos_usan_dr_en_ingles(self):
        # el inglés no genera el título — "Dra." también cae en "Dr."
        medicas = [m for m in _meds()
                   if site_doctors._guess_sex(m.get("nombre") or "") == "f"]
        self.assertTrue(medicas, "no hay ninguna médica en el pack de prueba")
        html = site_page_doctors._page_doctor(medicas[0]["id"]).decode("utf-8")
        self.assertNotIn("Dra. ", html)
        self.assertIn(">Dr. ", html)

    @reference_pack_test
    def test_encabezado_cuando_atiende_traducido(self):
        # ojo: "Cuándo atiende" también aparece en un COMENTARIO CSS del
        # <style> compartido (nunca visible) — se busca el <h2> real, no
        # cualquier aparición de la frase.
        html = site_page_doctors._page_doctor("luna-julian").decode("utf-8")
        self.assertIn('<h2 class="mp-agenda-h">When they see patients</h2>', html)
        self.assertNotIn('<h2 class="mp-agenda-h">Cuándo atiende</h2>', html)

    @reference_pack_test
    def test_atiende_en_traducido(self):
        html = site_page_doctors._page_doctor("luna-julian").decode("utf-8")
        self.assertNotIn(">Atiende en <", html)


@reference_pack_case
class TestPageMedicoCastellanoSinCambios(unittest.TestCase):
    """Paridad: en castellano (default, sin bindear idioma) el render tiene
    que seguir siendo BYTE A BYTE el de antes de esta extracción."""

    def test_titulo_y_descripcion_conservan_las_frases_originales(self):
        html = site_page_doctors._page_doctor("luna-julian").decode("utf-8")
        self.assertIn("Dr. Julian Luna — Cardiología en Pinamar", html)
        self.assertIn(
            "Dr. Julian Luna, Cardiología en el sistema público de salud de Pinamar.",
            html)

    def test_encabezado_cuando_atiende_sigue_en_castellano(self):
        html = site_page_doctors._page_doctor("luna-julian").decode("utf-8")
        self.assertIn('<h2 class="mp-agenda-h">Cuándo atiende</h2>', html)

    def test_centro_no_se_repite_despues_del_horario(self):
        html = site_page_doctors._page_doctor("luna-julian").decode("utf-8")
        self.assertNotIn("Atiende en <a", html)
        agenda = site_availability._schedule_table_html(
            next(x for x in _meds() if x["id"] == "luna-julian"))
        self.assertEqual(agenda.count("Hospital de Pinamar"), 1)


class TestPageDoctorsRowHref(unittest.TestCase):
    """Bug real encontrado al traducir: "rowHref" es el NOMBRE de la clave
    de cada fila que lleva el link (listing.js lo usa como `r[rowHref]`), no
    texto de interfaz — había quedado envuelto en i18n.t_or con una clave
    cuyo valor en inglés era otra frase, así que en el sitio en inglés el
    click en toda la fila de /doctors se rompía entero."""

    def test_rowhref_es_siempre_el_literal_href(self):
        for lang in ("es", "en"):
            token = i18n._ACTIVE.set(lang)
            try:
                html = site_page_doctors._page_doctors().decode("utf-8")
            finally:
                i18n._ACTIVE.reset(token)
            self.assertIn('"rowHref": "href"', html, lang)


class TestWorkshopNote(unittest.TestCase):
    """La nota de un taller ("Adolescentes" pide una entrevista breve la
    primera vez) vivía sin campo propio: se recortaba de `cartel` buscando
    un marcador en castellano. `cartel` es contenido de paquete que
    pack_l10n traduce en el borde de carga, así que en inglés el marcador
    dejaba de aparecer y la nota desaparecía en silencio (2026-08-04). Ahora
    vive en su propio campo `nota`, que se traduce por su cuenta — nada que
    parsear, nada que se rompa cuando el idioma cambia."""

    def test_no_depende_de_parsear_el_cartel(self):
        # cartel ya viene traducido (inglés, sin el marcador en castellano)
        # y la nota sigue apareciendo porque viene de su propio campo.
        s = {"cartel": "How to join? Come to the CAPS during the group's hours.",
             "nota": "If it's your first time, the team will do a short interview."}
        self.assertEqual(
            site_page_centers._workshop_note(s),
            "If it's your first time, the team will do a short interview.")

    def test_vacia_sin_campo_nota(self):
        s = {"cartel": "Acercate al CAPS durante el horario del grupo.\n"
                       "Modalidad de acceso: Ingreso abierto."}
        self.assertEqual(site_page_centers._workshop_note(s), "")

    @reference_pack_test
    def test_datos_reales_adolescentes_y_ansiedad(self):
        svs = {s["id"]: s for s in site_catalog._services(localize=False)
               if s.get("id") in ("adolescentes-ostende", "ansiedad-ostende")}
        self.assertIn("entrevista breve",
                      site_page_centers._workshop_note(svs["adolescentes-ostende"]))
        self.assertIn("otra modalidad de atención",
                      site_page_centers._workshop_note(svs["ansiedad-ostende"]))


if __name__ == "__main__":
    unittest.main()


class TestServicioEtiquetadoManda(unittest.TestCase):
    """Reporte del hospital (video del 8 de octubre de 2026): una ginecóloga
    con su horario etiquetado «Ginecología» (sin orden) salía «Con orden» y su
    botón sacaba turno en OTRO servicio del mismo grupo que sí pide orden. El
    servicio que el personal etiquetó en el horario del médico manda sobre
    cualquier inferencia por especialidad o grupo."""

    _SERVICIOS = [
        {"id": "gine-alto-riesgo", "nombre": "Ginecología - embarazo de alto riesgo",
         "cluster": "gineco", "centro": "hospital", "modo": "solicitud_con_orden"},
        {"id": "gine", "nombre": "Ginecología",
         "cluster": "gineco", "centro": "hospital", "modo": "solicitud"},
        {"id": "gine-caps", "nombre": "Ginecología",
         "cluster": "gineco", "centro": "caps", "modo": "solicitud_con_orden"},
    ]

    def _medico(self, servicio="gine"):
        fr = {"dias": [2], "desde": "08:00", "hasta": "09:30", "cada_min": 10}
        if servicio:
            fr["servicio"] = servicio
        return {"id": "dra", "nombre": "Sebastian Esquivel", "especialidad": "Ginecología",
                "cluster": "gineco", "centro": "hospital",
                "centros": [{"centro": "hospital", "franjas": [fr]}]}

    def _con_servicios(self):
        from unittest import mock
        return mock.patch.object(site_catalog, "_services", return_value=self._SERVICIOS)

    def test_el_boton_saca_turno_en_el_servicio_etiquetado(self):
        with self._con_servicios():
            self.assertEqual(site_doctors._doctor_service(self._medico())["id"], "gine")

    def test_no_dice_con_orden_si_su_servicio_no_la_pide(self):
        with self._con_servicios():
            self.assertIs(site_doctors._doctor_referral(self._medico()), False)

    def test_dice_con_orden_si_su_servicio_la_pide(self):
        with self._con_servicios():
            self.assertIs(site_doctors._doctor_referral(self._medico("gine-alto-riesgo")), True)

    def test_prefiere_el_servicio_etiquetado_en_su_centro(self):
        m = self._medico()
        m["centros"].insert(0, {"centro": "caps", "franjas": [
            {"dias": [1], "desde": "08:00", "hasta": "09:00", "cada_min": 10, "servicio": "gine-caps"}]})
        with self._con_servicios():
            self.assertEqual(site_doctors._doctor_service(m)["id"], "gine")
            self.assertEqual(site_doctors._doctor_service(dict(m, centro="caps"))["id"], "gine-caps")

    def test_sin_etiqueta_va_a_la_consulta_sin_orden(self):
        """TASK-56 (26.10, persona Norma en /d/cardiologia): sin etiqueta,
        la inferencia prefería el servicio que PIDE orden (ECG frente a la
        consulta de Cardiología) y todos los cardiólogos decían «Subí tu
        orden». Entre los servicios de su especialidad y centro, la consulta
        sin orden va primero."""
        with self._con_servicios():
            self.assertEqual(site_doctors._doctor_service(self._medico(""))["id"], "gine")
            self.assertIs(site_doctors._doctor_referral(self._medico("")), False)

    def test_sin_consulta_sin_orden_la_de_orden_sigue_valiendo(self):
        solo_orden = [s for s in self._SERVICIOS if s["modo"] == "solicitud_con_orden"]
        from unittest import mock
        with mock.patch.object(site_catalog, "_services", return_value=solo_orden):
            self.assertEqual(site_doctors._doctor_service(self._medico(""))["id"], "gine-alto-riesgo")
            self.assertIs(site_doctors._doctor_referral(self._medico("")), True)
class TestEnlaceAlServicio(unittest.TestCase):
    """/doctor/<id> enlaza (texto, no botón) a /service/<id> del servicio donde
    atiende —el mismo que usa la banda de turno—, y nada si no hay uno."""

    def setUp(self):
        from app.patient_web import catalog
        from app.core import site_identity
        caches = (site_identity._CACHE, catalog._JSON_CACHE, site_base._CACHE)
        # Registered before set_env: cleanups run last-in first-out, so the caches are dropped after the environment is back.
        for cache in caches:
            self.addCleanup(cache.clear)
        _env.set_env(self, TURNOS_WEBDATA_DIR=os.path.join(_ROOT, "packs", "example-town"))
        for cache in caches:
            cache.clear()

    def _doctor_with_service(self):
        for m in _meds():
            s = site_doctors._doctor_service(m)
            if s and not m.get("rol"):
                return m, s
        self.fail("el pack de ejemplo no trae un médico con servicio")

    def test_los_cardiologos_de_la_demo_van_a_su_consulta_sin_orden(self):
        """TASK-56 (26.10): en /d/cardiologia de la demo todos los
        cardiólogos decían «Subí tu orden» junto a una consulta que no la
        pide. Su horario nombra la consulta de Cardiología."""
        cardios = [m for m in _meds() if m.get("especialidad") == "Cardiología"]
        self.assertTrue(cardios)
        for m in cardios:
            self.assertEqual(site_doctors._doctor_tagged_service(m)["id"],
                             "cardiologia-sampleton-hospital", m["id"])
            self.assertIs(site_doctors._doctor_referral(m), False, m["id"])

    def test_enlaza_a_la_pagina_del_servicio(self):
        m, s = self._doctor_with_service()
        html = site_page_doctors._page_doctor(m["id"]).decode("utf-8")
        link = re.search(r'<p class="mp-service"><a class="stz-link" href="([^"]+)">([^<]+)</a></p>', html)
        self.assertIsNotNone(link, "falta el enlace al servicio")
        self.assertEqual(link.group(1), "/service/" + s["id"])
        self.assertIn(s["nombre"], link.group(2))
        # sigue habiendo UNA sola acción primaria
        self.assertEqual(html.count('class="mp-cta"') + html.count('class="mp-cta '), 1)

    def test_el_destino_existe(self):
        m, s = self._doctor_with_service()
        self.assertIsNotNone(site_page_service._page_service(s["id"]))

    def test_sin_servicio_no_hay_enlace(self):
        m, _s = self._doctor_with_service()
        orig = site_doctors._doctor_service
        site_doctors._doctor_service = lambda _m: None
        self.addCleanup(setattr, site_doctors, "_doctor_service", orig)
        html = site_page_doctors._page_doctor(m["id"]).decode("utf-8")
        self.assertNotIn("mp-service", html.split("</style>")[-1])
        self.assertNotIn('href="/service/', html.split("</style>")[-1])


class TestSecondSpecialty(unittest.TestCase):
    """A doctor's optional second specialty (hospital TASK-35): the row, the
    facet list and the doctor's page read both, linking each to its listing."""

    def _doctor(self, second="Cardiología", cluster_2="cardiologia"):
        base = next(m for m in _meds() if m.get("especialidad") and m.get("cluster")
                    and m["especialidad"] != second)
        return dict(base, especialidad_2=second, cluster_2=cluster_2)

    def test_row_carries_both_labels_and_each_one_as_a_facet_value(self):
        m = self._doctor()
        labels = site_doctors._doctor_specialty_labels(m)
        self.assertEqual(len(labels), 2)
        row = site_listing._doctor_listing_row(m)
        self.assertEqual(row["especialidad_todas"],
                         site_doctors._doctor_specialty_labels(m, gendered=False))
        self.assertEqual(row["especialidad"], " · ".join(labels))
        self.assertEqual(row["esp_href"], "/d/" + m["cluster"])

    def test_a_single_specialty_row_is_unchanged(self):
        m = next(m for m in _meds() if m.get("especialidad"))
        single = {k: v for k, v in m.items() if k not in ("especialidad_2", "cluster_2")}
        row = site_listing._doctor_listing_row(single)
        self.assertEqual(row["especialidad_todas"],
                         site_doctors._doctor_specialty_labels(single, gendered=False))
        self.assertEqual(len(row["especialidad_todas"]), 1)

    def test_doctor_page_links_each_specialty_to_its_listing(self):
        from unittest import mock
        m = self._doctor()
        with mock.patch.object(site_doctors, "_doctor_by_id", return_value=m):
            html = site_page_doctors._page_doctor(m["id"]).decode("utf-8")
        sub = re.search(r'<p class="mp-sub">(.*?)</p>', html).group(1)
        self.assertEqual(sub.count("<a "), 2)
        self.assertEqual(sub.count("/doctors?specialty="), 2)
        self.assertIn(" · ", sub)

    def test_the_second_cluster_counts_for_the_doctor(self):
        from app.patient_web import catalog
        self.assertIn("cardiologia", catalog.doctor_clusters(self._doctor()))


class TestGenderedTitle(unittest.TestCase):
    """The title follows the pack's `genero` field, never a guess; the filter
    value stays the generic title; a two-part specialty names two professions
    and a department is not a profession (demo review 2026-11-15, D14/P16)."""

    def test_feminine_only_from_the_pack_field(self):
        f = site_doctors._specialty_as_doctor
        self.assertEqual(f("Cardiología"), "Cardiólogo")
        self.assertEqual(f("Cardiología", "f"), "Cardióloga")
        self.assertEqual(f("Medicina general", "f"), "Médica general")
        self.assertEqual(f("Pediatría", "f"), f("Pediatría"))

    def test_two_professions_and_no_department_as_a_person(self):
        f = site_doctors._specialty_as_doctor
        self.assertEqual(f("Ginecología y obstetricia"), "Ginecólogo y obstetra")
        self.assertEqual(f("Ginecología y Obstetricia", "f"), "Ginecóloga y obstetra")
        self.assertEqual(f("Diagnóstico por imágenes"), "Especialista en imágenes")

    def test_english_stays_neutral(self):
        f = site_doctors._specialty_as_doctor
        self.assertEqual(f("Cardiology", "f"), "Cardiology")
        self.assertEqual(f("Gynecology and obstetrics", "f"), "Gynecology and obstetrics")

    def test_genero_is_read_never_guessed(self):
        self.assertIsNone(site_doctors._doctor_genero({"nombre": "Laura Pérez"}))
        self.assertEqual(site_doctors._doctor_genero({"genero": "F"}), "f")
        self.assertIsNone(site_doctors._doctor_genero({"genero": "x"}))

    def test_row_shows_her_title_and_filters_by_the_generic_one(self):
        base = next(m for m in _meds() if m.get("cluster"))
        m = dict(base, especialidad="Cardiología", genero="f")
        m.pop("especialidad_2", None)
        row = site_listing._doctor_listing_row(m)
        self.assertEqual(row["especialidad"], "Cardióloga")
        self.assertEqual(row["especialidad_todas"], ["Cardiólogo"])

    def test_doctor_page_title_and_subtitle_follow_the_field(self):
        from unittest import mock
        base = next(m for m in _meds() if m.get("cluster"))
        m = dict(base, especialidad="Cardiología", genero="f", nombre="Andrea Tomás")
        m.pop("especialidad_2", None)
        with mock.patch.object(site_doctors, "_doctor_by_id", return_value=m):
            html = site_page_doctors._page_doctor(m["id"]).decode("utf-8")
        sub = re.search(r'<p class="mp-sub">(.*?)</p>', html).group(1)
        self.assertIn(">Cardióloga</a>", sub)
        self.assertIn("specialty=Cardi%C3%B3logo", sub)
        self.assertIn("Dra.", html)

    def test_every_demo_doctor_who_is_a_person_has_genero(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", "packs", "example-town", "medicos.json"),
                  encoding="utf-8") as fh:
            meds = json.load(fh)["medicos"]
        self.assertEqual([m["id"] for m in meds if m.get("genero") not in ("f", "m")], [])


class TestRowNamesWhatItBooks(unittest.TestCase):
    """A doctor's row whose booking is another area's service names it next to
    the title: Pinamar's cardiologist who only does Ergometría read
    «Cardiólogo» while the row booked an ergometry (demo review 2026-11-15, P4)."""

    def _m(self):
        return {"id": "x", "nombre": "Ana Ruiz", "especialidad": "Cardiología",
                "cluster": "cardiologia", "genero": "m"}

    def test_other_area_service_is_named(self):
        sv = {"id": "ergo", "nombre": "Ergometría", "cluster": "diagnostico"}
        self.assertEqual(site_doctors._doctor_row_specialty(self._m(), sv), "Cardiólogo · Ergometría")

    def test_own_area_service_is_not_repeated(self):
        sv = {"id": "cardio", "nombre": "Cardiología", "cluster": "cardiologia"}
        self.assertEqual(site_doctors._doctor_row_specialty(self._m(), sv), "Cardiólogo")
        self.assertEqual(site_doctors._doctor_row_specialty(self._m()), "Cardiólogo")
