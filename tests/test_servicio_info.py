"""Página informativa del servicio + wizard directo (responsable del producto, 2026-07-12).

Los listados llevan DERECHO al wizard (/service/<id>/book) en los modos con
solicitud; /service/<id> pasa a ser la página informativa estilo médico
(banda de próximo turno + CTA, dónde, horario, preparación)."""

import datetime
import re
import unittest
from unittest import mock

from app.core import i18n
from app.patient_web import (
    site_availability,
    site_base,
    site_catalog,
    site_centers,
    site_doctors,
    site_listing,
    site_page_service,
    site_schedule,
    site_studies,
)
from _packtest import reference_pack_test
import _dbtest

# The pages read the catalog overlay and the free slots from Postgres: the file
# owns a throwaway database at head instead of whatever DATABASE_URL names
# (TestBaseDelModulo below says why).
_db = _dbtest.PerModuleDb("sana_turnos_test_servicio_info")


def setUpModule():
    _db.set_up()


def tearDownModule():
    _db.tear_down()


class TestServicioInfo(unittest.TestCase):
    def _svc(self, modo):
        return next((s for s in site_catalog._services() if s.get("modo") == modo), None)

    def test_info_page_tiene_banda_y_cta_al_wizard(self):
        s = self._svc("solicitud_con_orden")
        self.assertIsNotNone(s)
        html = site_page_service._page_service(s["id"]).decode()
        self.assertIn("mp-band", html)
        self.assertIn(f'/service/{s["id"]}/book', html)
        # referencia demote: dónde se atiende, nunca un formulario acá
        # (el CSS compartido menciona el selector; lo que no puede haber es el div)
        self.assertNotIn('id="svc-form-host"', html)

    @reference_pack_test
    def test_dos_botones_primer_turno_vs_otros_horarios(self):
        """El estudio de usabilidad: la gente leía el turno de la banda como el
        ÚNICO. Con turno real hay DOS salidas — el primer turno libre ES el botón
        (con ?slot=, preseleccionado en el wizard, y el VERBO de los listados:
        "Sacar el viernes 17 de julio, 13:00") y al lado "Más horarios" (mismo
        nombre que en las celdas). La hora se dice UNA sola vez: en el botón."""
        s = next((x for x in site_catalog._services()
                  if str(x.get("modo", "")).startswith("solicitud")
                  and site_availability._service_next(x).get("kind") == "turno"), None)
        self.assertIsNotNone(s)
        px = site_availability._service_next(s)
        html = site_page_service._page_service(s["id"]).decode()
        self.assertIn(f'/service/{s["id"]}/book?slot={px["dia"]}T{px["hora"]}', html)
        self.assertIn("Más horarios", html)
        # "Más horarios" lleva a ELEGIR el turno: ?slots=1 invierte el
        # wizard (Cuándo primero, la orden después). Sin el flag caía en
        # "Subí tu orden" (responsable del producto, 2026-07-15).
        self.assertIn(f'/service/{s["id"]}/book?slots=1"', html)
        self.assertIn("Primer turno libre", html)
        self.assertIn("no el único", html)
        # la hora, una sola vez — en el botón con verbo; el cuerpo de la banda
        # no repite ni la versión capitalizada original de la fecha
        self.assertEqual(html.count(site_studies._get_label(px["v"])), 1)
        self.assertNotIn(px["v"], html)

    def test_el_turno_que_ofrece_la_banda_se_puede_elegir_en_el_wizard(self):
        """Invariante: lo que la banda promete, el wizard lo sabe ofrecer. Antes
        la banda salía de la agenda y el wizard sólo miraba Mi Salud Digital, así
        que ofrecía 'Hoy, 07:30' y después sólo pedía una franja."""
        vistos = 0
        for s in site_catalog._services():
            if not str(s.get("modo", "")).startswith("solicitud"):
                continue
            px = site_availability._service_next(s)
            if px.get("kind") != "turno":
                continue
            vistos += 1
            slots = {(t["dia"], t["hora"])
                     for m in site_availability._appointments_for_service(s).get("medicos", [])
                     for t in m.get("turnos", [])}
            self.assertIn((px["dia"], px["hora"]), slots, s["id"])
        self.assertTrue(vistos)

    def test_breadcrumb_no_repite_el_titulo_del_servicio(self):
        s = self._svc("solicitud_con_orden")
        html = site_page_service._page_service(s["id"]).decode()
        c = site_centers._centers_by_slug().get(s.get("centro")) or {}
        cl = site_catalog._cluster_by_id(s.get("cluster")) or {}
        self.assertIn("svc-crumb", html)
        self.assertIn(f'/center/{c["slug"]}', html)
        self.assertIn(f'/d/{s["cluster"]}', html)
        self.assertIn(cl["nombre"], html)
        visible = html.split('<main class="wrap">', 1)[1].split(
            '<script id="svc-data"', 1)[0]
        self.assertNotIn("svc-crumb-cur", visible)
        self.assertNotIn(f'>{s["nombre"]}</nav>', visible)

    @reference_pack_test
    def test_wizard_conserva_centro_area_y_servicio_en_ese_orden(self):
        s = site_catalog._service_by_id("ecodoppler-hospital")
        html = site_page_service._page_service_appointment(s["id"]).decode()
        c = site_centers._centers_by_slug()[s["centro"]]
        cl = site_catalog._cluster_by_id(s["cluster"])
        crumb = html.split('<nav class="svc-crumb"', 1)[1].split("</nav>", 1)[0]
        self.assertLess(crumb.index(c["name"]), crumb.index(cl["nombre"]))
        self.assertLess(crumb.index(cl["nombre"]), crumb.index(s["nombre"]))
        self.assertIn(f'href="/service/{s["id"]}"', crumb)

    @reference_pack_test
    def test_entrada_al_asistente_de_preparacion_es_un_boton(self):
        """No es una guía: es el asistente que lee la orden. Era un link gris
        ('Ver guía') que nadie clickeaba (responsable del producto, 2026-07-12), después una
        tarjeta con un párrafo de tres renglones; desde 2026-07-25 es un botón
        con una sola frase al lado — la acción se ve, no se lee."""
        s = next((x for x in site_catalog._services()
                  if x.get("categoria") == "estudios"
                  and str(x.get("modo", "")).startswith("solicitud")), None)
        self.assertIsNotNone(s)
        html = site_page_service._page_service(s["id"]).decode()
        self.assertIn('<a class="ai-btn"', html)
        self.assertIn(f'/upload-order?service={s["id"]}', html)
        self.assertIn("Subí tu orden</a>", html)
        self.assertNotIn("Ver guía", html)
        # El párrafo largo no vuelve: una frase, no un bloque de texto.
        self.assertNotIn("en menos de un minuto", html)
        self.assertIn('id="svc-prep-title">Cómo prepararte</h2>', html)
        # Since 2026-11-15 the button stands alone: no subtitle beside it.
        self.assertNotIn("ai-line-s", html)
        self.assertNotIn("la preparación exacta de cada estudio", html)
        self.assertNotIn("tipo no especificado", html.lower())
        # Desde 2026-07-29 la página TAMBIÉN muestra la preparación del
        # catálogo. La regla vieja ("en una página genérica como Ecografía, una
        # instrucción aproximada es peor que ninguna") dejaba mudas a las
        # atenciones que no dependen de ninguna orden — vacunación, consulta,
        # odontología. La entrada genérica no promete una preparación exacta:
        # su propio resumen dice que depende de qué parte del cuerpo es.
        self.assertIn("depende de qué parte del cuerpo", html)

    @reference_pack_test
    def test_el_reloj_publico_usa_art_aunque_el_servidor_este_en_utc(self):
        """22:41 ART todavía es 28/7 aunque UTC ya sea 29/7.

        El primer turno debe ser mañana y jamás rotularse como "hoy 07:50".
        """
        s = site_catalog._service_by_id("ecografia-hospital")
        ahora_art = datetime.datetime(2026, 7, 28, 22, 41)
        with mock.patch.object(site_base, "_now_art", return_value=ahora_art), \
                mock.patch.object(site_availability, "_busy_default", return_value=set()):
            px = site_availability._service_next(s)
        self.assertEqual(px["dia"], "2026-07-29")
        self.assertTrue(px["v"].startswith("Mañana, "), px)
        self.assertNotIn("hoy", site_studies._get_label(px["v"]).lower())

    def test_wizard_vive_en_turno_y_linkea_la_info(self):
        s = self._svc("solicitud_con_orden")
        html = site_page_service._page_service_appointment(s["id"]).decode()
        self.assertIn("svc-form-host", html)
        c = site_centers._centers_by_slug().get(s.get("centro")) or {}
        # El contexto persiste en todos los pasos y conserva la jerarquía.
        self.assertIn('aria-label="Ubicación"', html)
        self.assertIn(f'href="/service/{s["id"]}"', html)
        self.assertIn(f'href="/center/{c["slug"]}"', html)
        self.assertIn(s["nombre"], html)
        self.assertIn(c["name"], html)
        from app.core import static_fingerprint
        # Both canonical ES modules are published through the client manifest
        # for service.js to import. The deleted classic scripts are absent.
        self.assertIn(static_fingerprint.url("platform/calendar-grid.mjs"), html)
        self.assertIn(static_fingerprint.url("platform/format.mjs"), html)
        self.assertIn(static_fingerprint.module_url("modules/turnos/service.mjs"), html)
        self.assertIn('<script type="importmap">', html)
        self.assertIn("takePendingFile: takePendingFile", html)
        self.assertNotIn("calendar-grid" + ".js", html)
        self.assertNotIn("/format.js?v=", html)

    def test_wizard_con_orden_entrega_el_primer_paso_en_el_html_inicial(self):
        """No se deja al footer subir mientras JS arma la zona de carga."""
        s = self._svc("solicitud_con_orden")
        html = site_page_service._page_service_appointment(s["id"]).decode()
        self.assertIn('aria-label="Paso 1 de 4"', html)
        self.assertIn('id="wz-upzone"', html)
        self.assertIn('id="wz-noorden"', html)
        self.assertIn('El hospital la revisa y te confirma el turno.', html)
        # La estructura inicial aparece antes de los scripts diferidos.
        from app.core import static_fingerprint
        self.assertLess(html.index('id="wz-upzone"'),
                        html.index(static_fingerprint.module_url("modules/turnos/service.mjs")))

    def test_wizard_con_slot_y_sesion_muestra_dos_pasos_desde_el_inicio(self):
        s = self._svc("solicitud_con_orden")
        html = site_page_service._page_service_appointment(
            s["id"],
            query_string="slot=2026-08-04T08:40",
            current_user={"dni": "20456781"},
        ).decode()
        self.assertIn('aria-label="Paso 1 de 2"', html)
        self.assertIn('"pasos_estimados": ["orden", "confirm"]', html)
        self.assertIn('id="wz-upzone"', html)
        self.assertNotIn("{_WZ_CAMERA_ICON}", html)

    @reference_pack_test
    def test_geriatria_con_titular_joven_no_muestra_subida_antes_de_elegir_persona(self):
        s = site_catalog._service_by_id("geriatria-hospital")
        joven = {"dni": "20456781", "nacimiento": "1990-03-12"}
        html = site_page_service._page_service_appointment(
            s["id"], query_string="slot=2026-08-04T08:40",
            current_user=joven).decode()
        self.assertIn("¿Para quién es la atención?", html)
        self.assertIn("Comprobando para quién es la atención", html)
        self.assertNotIn('id="wz-upzone"', html)

    def test_wizard_sin_orden_no_renderiza_la_subida_del_otro_flujo(self):
        s = self._svc("solicitud")
        html = site_page_service._page_service_appointment(s["id"]).decode()
        # The known first task is server-rendered before deferred canonical
        # modules load. It is a real loading state, not fabricated slots.
        self.assertIn('id="svc-form-host" class="svc-form-host--schedule-boot"', html)
        self.assertIn('class="sec svc-first-schedule" aria-live="polite"', html)
        self.assertIn("Buscando turnos disponibles", html)
        self.assertIn('aria-label="Paso 1 de 3"', html)
        from app.core import static_fingerprint
        self.assertLess(html.index("svc-first-schedule"),
                        html.index(static_fingerprint.module_url("modules/turnos/service.mjs")))
        self.assertNotIn('id="wz-upzone"', html)

    def test_wizard_sin_orden_con_turno_preseleccionado_conserva_su_espera_unica(self):
        s = self._svc("solicitud")
        html = site_page_service._page_service_appointment(
            s["id"], query_string="slot=2026-08-04T08:40").decode()
        # The shared stylesheet necessarily names the schedule shell; this
        # selected-slot route must not instantiate it in the actual host.
        self.assertIn('<div id="svc-form-host"></div>', html)
        self.assertNotIn('id="svc-form-host" class="svc-form-host--schedule-boot"', html)
        self.assertNotIn('class="sec svc-first-schedule"', html)

    @reference_pack_test
    def test_nombre_msd_linkea_al_medico_del_catalogo(self):
        s = site_catalog._service_by_id("medicina-general-pinamar")
        m = site_doctors._doctor_catalog_by_name("BUSTOS, DIEGO", s)
        self.assertIsNotNone(m)
        self.assertEqual(m["id"], "bustos-diego")

    @reference_pack_test
    def test_nombre_msd_matchea_un_centro_secundario_del_medico(self):
        s = site_catalog._service_by_id("medicina-general-pinamar")
        m = site_doctors._doctor_catalog_by_name("PINTO, LUCIA", s)
        self.assertIsNotNone(m)
        self.assertEqual(m["id"], "lucia-pinto")
        self.assertEqual(m["nombre"], "Lucia Pinto")
        self.assertEqual(m["especialidad"], "Medicina general")

    def test_medico_elegido_queda_primero_sin_ocultar_los_demas(self):
        generic = {
            "medicos": [
                {"medico": "Hugo Montes", "medico_id": "montes-hugo",
                 "turnos": [{"dia": "2026-08-06", "hora": "17:00"}]}
            ],
            "fuente": "msd",
        }
        preferred = {
            "medicos": [
                {"medico": "Ines Quevedo", "medico_id": "quevedo-ines",
                 "turnos": [{"dia": "2026-07-27", "hora": "08:00"}]}
            ],
            "fuente": "agenda-medico",
        }
        with mock.patch.object(site_availability, "_appointments_for_service", return_value=generic), \
                mock.patch.object(site_availability, "_appointments_for_doctor", return_value=preferred):
            out = site_availability._appointments_for_service_preferred(
                {"id": "medicina-general-ostende"}, "quevedo-ines"
            )
        self.assertEqual(
            [m["medico_id"] for m in out["medicos"]],
            ["quevedo-ines", "montes-hugo"],
        )

    @reference_pack_test
    def test_link_de_turno_del_medico_conserva_el_medico(self):
        s = site_catalog._service_by_id("medicina-general-ostende")
        px = {"kind": "turno", "dia": "2026-07-27", "hora": "08:00"}
        fields = site_listing._slot_fields(s, px, "quevedo-ines")
        self.assertIn("medico=quevedo-ines", fields["slot_href"])
        self.assertIn("slots=1", fields["info_href"])
        self.assertIn("medico=quevedo-ines", fields["info_href"])

    @reference_pack_test
    def test_walkin_no_tiene_wizard(self):
        s = self._svc("walkin_con_orden") or self._svc("walkin")
        self.assertIsNotNone(s)
        self.assertIsNone(site_page_service._page_service_appointment(s["id"]))

    @reference_pack_test
    def test_walkin_linkea_centro_mapa_y_telefono_sin_etiquetas_redundantes(self):
        s = site_catalog._service_by_id("testeo-rapido-ostende")
        c = site_centers._centers_by_slug()["caps-ostende"]
        html = site_page_service._page_service(s["id"]).decode()
        visible = html.split('<main class="wrap">', 1)[1].split('<script id="svc-data"', 1)[0]
        self.assertIn('class="svc-contact"', visible)
        self.assertIn('class="svc-req"', visible)
        self.assertIn('data-semana data-titulo="Horarios"', visible)
        self.assertIn(f'href="/center/{c["slug"]}"', html)
        self.assertIn(f'href="{site_centers._maps_url(c)}"', html)
        self.assertIn(f'href="tel:{site_centers._phone_href(c["phone"])}"', html)
        self.assertNotIn("<b>Dónde:</b>", html)
        self.assertNotIn("<b>Dirección:</b>", html)
        self.assertNotIn("<b>Teléfono:</b>", html)
        self.assertEqual(visible.count("No necesitás turno."), 1)
        self.assertEqual(visible.count("No necesitás orden médica."), 1)
        self.assertIn("no informa una hora de cierre", visible)
        self.assertNotIn("cartel--walkin", visible)

    @reference_pack_test
    def test_walkin_usa_tres_secciones_iguales_y_no_promociona_subir_orden(self):
        s = site_catalog._service_by_id("laboratorio-hospital")
        html = site_page_service._page_service(s["id"]).decode()
        visible = html.split('<main class="wrap">', 1)[1].split(
            '<script id="svc-data"', 1)[0]
        self.assertEqual(visible.count('<section class="svc-major'), 3)
        self.assertIn(">Requisitos</h2>", visible)
        self.assertIn(">Cómo prepararte</h2>", visible)
        self.assertIn(">Dónde se atiende</h2>", visible)
        # La preparación ocupa la fila entera de abajo (responsable del producto, 2026-07-29): en
        # tres columnas iguales quedaba angosta y larguísima al lado de dos
        # tarjetas casi vacías.
        self.assertIn('class="svc-major svc-major--prep"', visible)
        self.assertNotIn("Subí tu orden", visible)
        self.assertEqual(visible.count("Podés tomar agua"), 1)
        self.assertIn("hora de presentación", visible)
        from app.core import static_fingerprint
        self.assertIn(static_fingerprint.url("platform/week.mjs"), html)

    def test_cambio_de_centro_solo_se_ofrece_si_hay_un_equivalente(self):
        unico = {
            "id": "solo", "nombre": "Ecografía", "centro": "hospital",
            "modo": "solicitud_con_orden", "edad": {}, "cluster": "imagenes",
        }
        otro_centro = {
            "id": "otro", "nombre": "Ecografía", "centro": "caps",
            "modo": "solicitud_con_orden", "edad": {}, "cluster": "imagenes",
        }
        distinto = {
            "id": "distinto", "nombre": "Radiografía", "centro": "caps",
            "modo": "solicitud_con_orden", "edad": {}, "cluster": "imagenes",
        }
        with mock.patch.object(site_catalog, "_services", return_value=[unico, distinto]):
            self.assertFalse(site_catalog._can_change_center(unico))
        with mock.patch.object(site_catalog, "_services",
                               return_value=[unico, otro_centro]):
            self.assertTrue(site_catalog._can_change_center(unico))

    @reference_pack_test
    def test_horarios_verificados_de_valeria_tienen_inicio_y_fin(self):
        self.assertEqual(
            site_catalog._service_by_id("testeo-rapido-valeria")["horario"],
            "Miércoles de 16 a 18 h",
        )
        self.assertEqual(
            site_catalog._service_by_id("curso-embarazo-valeria")["horario"],
            "Miércoles de 11 a 13 h",
        )

    @reference_pack_test
    def test_odontologia_tiene_descripcion_editable_y_sin_repeticiones(self):
        s = site_catalog._service_by_id("odontologia-ostende")
        html = site_page_service._page_service(s["id"]).decode()
        visible = html.split('<main class="wrap">', 1)[1].split('<script id="svc-data"', 1)[0]
        self.assertIn(s["descripcion"], visible)
        self.assertLess(visible.index("<h1"), visible.index("svc-lede"))
        # Dónde se atiende primero y Requisitos al lado (responsable del producto, 2026-07-29);
        # la preparación, entera, debajo de las dos.
        self.assertLess(visible.index("svc-lede"), visible.index("Dónde se atiende"))
        self.assertLess(visible.index("Dónde se atiende"), visible.index("Requisitos"))
        self.assertLess(visible.index("Requisitos"), visible.index("Cómo prepararte"))
        self.assertEqual(visible.count("No necesitás turno."), 1)
        # El horario se cuenta como texto visible: el atributo data-horario
        # del host de la semana es el dato para week.mjs, no una repetición.
        texto = re.sub(r"<[^>]+>", " ", visible)
        self.assertEqual(texto.count(s["horario"]), 1)

    def test_nombre_lleva_a_info_y_turno_lleva_al_wizard(self):
        con = self._svc("solicitud_con_orden")
        self.assertEqual(site_listing._service_center_row(con)["href"],
                         f'/service/{con["id"]}')
        self.assertEqual(site_listing._service_center_row(con)["turno_href"],
                         f'/service/{con["id"]}/book')
        self.assertEqual(site_listing._service_area_row(con)["href"],
                         f'/service/{con["id"]}')
        self.assertEqual(site_listing._service_area_row(con)["turno_href"],
                         f'/service/{con["id"]}/book')
        walk = self._svc("walkin_con_orden") or self._svc("walkin")
        self.assertEqual(site_listing._service_center_row(walk)["href"],
                         f'/service/{walk["id"]}')

    @reference_pack_test
    def test_como_prepararte_va_al_final(self):
        """el responsable del producto, 2026-07-29: la preparación importa recién cuando ya tenés el
        turno — va última, después de dónde, cuándo y quién. Arriba de todo
        hacía que la página repitiera la orden cuatro veces antes de decir
        dónde te atienden."""
        html = site_page_service._page_service("ecografia-hospital").decode()
        prep = html.index('id="svc-prep-title"')
        self.assertGreater(prep, html.index("Se atiende en"))
        self.assertGreater(prep, html.index("data-semana"))

    @reference_pack_test
    def test_servicio_muestra_profesionales_con_foto_y_proximo_turno(self):
        html = site_page_service._page_service("cirugia-pinamar").decode()
        self.assertIn('id="service-doctors"', html)
        self.assertIn('"title": "Quién atiende"', html)
        self.assertIn('"avatar": true', html)
        self.assertIn('"key": "proximo"', html)
        # platform/listing.mjs is fingerprinted: the page loads the table
        # engine as type=module at its content-hashed URL, never `?v=`.
        from app.core import static_fingerprint
        self.assertIn('type="module"', html)
        self.assertIn(static_fingerprint.url("platform/listing.mjs"), html)
        self.assertNotIn("/listing.js?v=", html)

    def test_la_banda_no_repite_que_hace_falta_la_orden(self):
        """La etiqueta "Con orden" de la banda murió (responsable del producto, 2026-07-29): la
        tarjeta "Cómo funciona", justo abajo, ya explica la orden completa —
        en la banda era la misma palabra por cuarta vez. La orden se sigue
        diciendo, una vez, en el cartel."""
        s = self._svc("solicitud_con_orden")
        html = site_page_service._page_service(s["id"]).decode()
        # el <span> concreto, no el token: la regla CSS compartida sigue en el
        # <style> del shell (la banda del médico la usa)
        self.assertNotIn('<span class="mp-band-badge">', html)
        cartel = html.split('class="cartel"')[1].split("</div>")[0]
        self.assertIn("orden", cartel.lower())


class TituloDelWizardTestCase(unittest.TestCase):
    """El h1 sale escrito para el paso donde el wizard va a aterrizar.

    Con el turno ya elegido en la URL (?slot=), "Cuándo" está resuelto: con
    sesión abierta se aterriza en Confirmá y sin sesión en Autorizá. Escribirlo
    ya en el servidor es lo que evita que se vea el título de otro paso durante
    una fracción de segundo (responsable del producto, 2026-07-27)."""

    def test_sin_slot_el_titulo_es_el_del_primer_paso(self):
        self.assertEqual(site_page_service._h1_wizard("solicitud"), "Elegí un turno")
        self.assertEqual(site_page_service._h1_wizard("solicitud_con_orden"), "Subí tu orden")

    def test_con_slot_y_sesion_el_titulo_es_confirma(self):
        self.assertEqual(
            site_page_service._h1_wizard(
                "solicitud", "slot=2026-07-28T09:30",
                {"dni": "99000001"}),
            "Confirmá tu solicitud")

    def test_con_slot_sin_sesion_el_titulo_es_autoriza(self):
        self.assertEqual(
            site_page_service._h1_wizard("solicitud", "slot=2026-07-28T09:30"),
            "Autorizá")

    def test_con_orden_pendiente_el_primer_paso_sigue_siendo_subirla(self):
        """Elegir el turno no saltea la orden: sin ?order= el wizard arranca
        igual en la subida, así que el título no puede prometer otra cosa."""
        user = {"dni": "99000001"}
        self.assertEqual(
            site_page_service._h1_wizard(
                "solicitud_con_orden", "slot=2026-07-28T09:30", user),
            "Subí tu orden")
        self.assertEqual(
            site_page_service._h1_wizard(
                "solicitud_con_orden",
                "slot=2026-07-28T09:30&order=tok", user),
            "Confirmá tu solicitud")

    def test_sin_contexto_o_sin_usuario_no_rompe(self):
        self.assertEqual(site_page_service._h1_wizard("solicitud"), "Elegí un turno")
        self.assertEqual(
            site_page_service._h1_wizard("solicitud", "slot=2026-07-28T09:30"),
            "Autorizá")


class TestServicioInfoI18n(unittest.TestCase):
    """i18n del 2026-08-04: en inglés la banda, el cartel y las etiquetas de
    la página de servicio se traducen; en castellano el render sigue siendo
    byte a byte el de siempre (i18n_audit.py lo verifica contra origin/main
    en el gate compartido; acá se fija el contrato en un test unitario)."""

    def _svc_con_orden(self):
        return next((s for s in site_catalog._services()
                     if str(s.get("modo", "")).startswith("solicitud")), None)

    def _en(self, fn):
        token = i18n._ACTIVE.set("en")
        try:
            return fn()
        finally:
            i18n._ACTIVE.reset(token)

    def test_banda_y_mas_horarios_en_ingles(self):
        s = next((x for x in site_catalog._services()
                  if str(x.get("modo", "")).startswith("solicitud")
                  and site_availability._service_next(x).get("kind") == "turno"), None)
        self.assertIsNotNone(s)
        html_en = self._en(lambda: site_page_service._page_service(s["id"]).decode())
        self.assertIn("First available appointment", html_en)
        self.assertIn("More times", html_en)
        self.assertNotIn("class=\"mp-band-sub\"", html_en)  # sub-line dropped (review 2026-11-15)
        self.assertNotIn("Primer turno libre", html_en)
        self.assertNotIn("Más horarios", html_en)

    def test_cartel_como_funciona_en_ingles(self):
        s = next((x for x in site_catalog._services()
                  if str(x.get("modo", "")).startswith("solicitud") and x.get("cartel")), None)
        self.assertIsNotNone(s)
        html_en = self._en(lambda: site_page_service._page_service(s["id"]).decode())
        self.assertIn("How it works", html_en)
        self.assertNotIn("Cómo funciona", html_en)

    def test_castellano_sigue_siendo_el_de_siempre(self):
        """Sin idioma activo (instalación de un solo idioma, o request sin
        bind), _page_service/_service_info_request tienen que devolver
        exactamente lo mismo que antes de esta ronda de i18n."""
        s = next((x for x in site_catalog._services()
                  if str(x.get("modo", "")).startswith("solicitud")
                  and site_availability._service_next(x).get("kind") == "turno"), None)
        self.assertIsNotNone(s)
        html_es = site_page_service._page_service(s["id"]).decode()
        self.assertIn("Primer turno libre", html_es)
        self.assertIn("Más horarios", html_es)
        self.assertNotIn("class=\"mp-band-sub\"", html_es)  # sub-line dropped (review 2026-11-15)


class TestHorarioDisplayI18n(unittest.TestCase):
    """_horario_display() (bug real, demo, 2026-08-05): el `horario` de un
    médico/servicio ("Lunes a viernes de 08:00 a 12:00") se lee tal cual en
    castellano (el parser de hours.mjs necesita el dato crudo,
    pack_l10n.py regla 4) pero tiene que decir "Monday to Friday from 08:00
    to 12:00" en inglés — nunca un mix castellano/inglés."""

    def _en(self, fn):
        token = i18n._ACTIVE.set("en")
        try:
            return fn()
        finally:
            i18n._ACTIVE.reset(token)

    def test_rango_de_dias_y_horas(self):
        self.assertEqual(
            self._en(lambda: site_schedule._horario_display("Lunes a viernes de 08:00 a 12:00")),
            "Monday to Friday from 08:00 to 12:00")

    def test_conector_y_entre_dos_dias_sueltos(self):
        # "y" es MÁS común que "a" en el pack real ("Martes y jueves de…") —
        # antes de esto sólo el rango con "a" se traducía.
        self.assertEqual(
            self._en(lambda: site_schedule._horario_display("Martes y jueves de 10 a 13 h")),
            "Tuesday and Thursday from 10 to 13 h")

    def test_lista_de_tres_dias(self):
        self.assertEqual(
            self._en(lambda: site_schedule._horario_display("Lunes, martes y miércoles de 12 a 14 h")),
            "Monday, Tuesday and Wednesday from 12 to 14 h")

    def test_rango_de_horas_sin_dia(self):
        # walk-in sin día ("14:00 a 18:00") — una guarda vieja lo dejaba
        # intacto porque "todavía no apareció ningún día" en la cadena.
        self.assertEqual(
            self._en(lambda: site_schedule._horario_display("14:00 a 18:00")),
            "14:00 to 18:00")

    def test_apertura_puntual_a_las(self):
        self.assertEqual(
            self._en(lambda: site_schedule._horario_display("Jueves a las 7 h")),
            "Thursday at 7 h")

    def test_prosa_ajena_a_un_rango_no_se_toca(self):
        # revisión de código, 2026-08-05: sacar la guarda vieja del todo
        # traducía también "de 24" en una frase que no es un horario.
        self.assertEqual(
            self._en(lambda: site_schedule._horario_display("Servicio de 24 horas")),
            "Servicio de 24 horas")
        self.assertEqual(
            self._en(lambda: site_schedule._horario_display("El primer día hábil de cada mes")),
            "El primer día hábil de cada mes")

    def test_castellano_no_se_mueve_ni_un_byte(self):
        for h in ("Lunes a viernes de 08:00 a 12:00", "14:00 a 18:00",
                  "Martes y jueves de 10 a 13 h"):
            self.assertEqual(site_schedule._horario_display(h), h)

    def test_todos_los_horarios_reales_del_pack_traducen_sin_dejar_dias_en_castellano(self):
        """Barrido sobre servicios.json + medicos.json: ninguno debe quedar
        con un nombre de día en castellano después de traducir (mezcla
        castellano/inglés, el defecto real de esta ronda)."""
        dias_es = r"\b(Lunes|Martes|Mi[ée]rcoles|Jueves|Viernes|S[áa]bado|Domingo" \
                  r"|lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bado|domingo)\b"
        horarios = {s.get("horario") for s in site_catalog._services() if s.get("horario")}
        horarios |= {m.get("horario") for m in site_catalog._professionals() if m.get("horario")}
        self.assertTrue(horarios)
        for h in horarios:
            out = self._en(lambda h=h: site_schedule._horario_display(h))
            self.assertNotRegex(out, dias_es, f"{h!r} -> {out!r}")


class TestHorarioEnHtml(unittest.TestCase):
    """The hours are text in the HTML, not only a data-horario attribute that
    week.mjs paints (SEO audit 2026-10-01, Pinamar item 9: a crawler that
    runs no script read «No pudimos cargar los horarios» instead of them)."""

    @staticmethod
    def _visible(html):
        import re
        html = re.sub(r"<(script|style)\b[\s\S]*?</\1>", " ", html)
        html = re.sub(r"<(\w+)\b[^>]*\shidden\b[^>]*>[\s\S]*?</\1>", " ", html)
        return " ".join(re.sub(r"<[^>]+>", " ", html).split())

    def test_week_markup_prints_title_and_hours(self):
        html = site_listing._week_markup("Lunes a viernes de 8 a 12 h", "Horarios")
        visible = self._visible(html)
        self.assertIn("Horarios", visible)
        self.assertIn("Lunes a viernes de 8 a 12 h", visible)
        # week.mjs repaints the host, so the server text and the grid's own
        # <h2 class="sem-t"> never show together; the recovery stays hidden.
        self.assertIn('<h2 class="sem-t">Horarios</h2>', html)
        self.assertIn("data-semana-error hidden", html)

    def test_service_page_has_its_hours_without_scripts(self):
        s = next(x for x in site_catalog._services()
                 if (x.get("horario") or "").startswith("Lunes a viernes de"))
        visible = self._visible(site_page_service._page_service(s["id"]).decode())
        self.assertIn(s["horario"], visible)


class TestBaseDelModulo(unittest.TestCase):
    """The pages read the catalog overlay and the free slots from the database.
    Run alone (`backend-focused`, `unittest discover -p test_servicio_info.py`)
    this file used to fall through to the shared development database: once a
    branch stamped it with an Alembic revision this checkout does not know,
    every connection retried the schema bootstrap and failed, and the file took
    over 55 minutes instead of seconds (2026-11-06). The module owns a
    throwaway database at head instead, whatever state the shared one is in."""

    def test_el_modulo_corre_en_su_propia_base(self):
        from urllib.parse import urlsplit

        from app.core import db

        if not _db.have_db:
            self.skipTest(_db.skip_reason or "sin Postgres de test")
        name =urlsplit(db._db_url()).path.lstrip("/")
        self.assertTrue(name.startswith("sana_turnos_test_servicio_info"), name)


if __name__ == "__main__":
    unittest.main()
