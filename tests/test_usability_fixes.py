"""Regresiones de los hallazgos de usabilidad sintética del 2026-07-12."""

import copy
import os
import unittest

import _patient_site_source
from app.patient_web import catalog
from app.patient_web import site_availability, site_listing, site_page_service
from app.modules.turnos import preparation

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*path):
    with open(os.path.join(ROOT, *path), encoding="utf-8") as f:
        return f.read()


def _admin_controller():
    # The legacy admin.html shell is deleted (2026-09-01): the direct
    # /admin/<screen> pages render the markup and load the SAME controllers,
    # so admin-app.js alone is now the logical source for the markup the
    # controller renders itself.
    return _read("static/root/admin-app.js")


class TestUsabilityFixes(unittest.TestCase):
    def test_preparation_laboratorio_enlaza_donde_atenderse(self):
        matched = [{"nombre": "Glucemia"}, {"nombre": "Perfil lipídico"}]
        self.assertEqual(preparation._care_href(matched), "/?q=laboratorio")
        self.assertEqual(preparation._care_href(matched, "laboratorio-hospital"),
                         "/service/laboratorio-hospital")

    def test_medico_admite_palabras_y_las_expone_en_busqueda(self):
        base = copy.deepcopy(catalog.doctors()[0])
        base["palabras"] = ["palpitaciones", "dolor de pecho"]
        doc, err = catalog.validate("medicos", base, is_new=False)
        self.assertIsNone(err)
        self.assertEqual(doc["palabras"], ["palpitaciones", "dolor de pecho"])
        row = site_listing._doctor_listing_row(doc)
        self.assertEqual(row["palabras"], doc["palabras"])

    def test_agenda_real_del_medico_produce_disponibilidad(self):
        m = {"id": "test-agenda", "nombre": "Dra. Test", "especialidad": "Cardiología",
             "cluster": "cardiologia", "centro": "hospital-pinamar",
             "agenda": {"franjas": [{"dias": [1, 4], "desde": "08:00",
                                       "hasta": "12:00", "cada_min": 20}],
                        "excepciones": []}}
        px = site_availability._doctor_next(m)
        self.assertEqual(px["kind"], "turno")
        self.assertIn("dia", px)

    def test_sin_turnos_ofrece_fecha_y_caso_sin_franja_ni_telefono(self):
        with open(os.path.join(ROOT, "app", "modules", "turnos", "service.mjs"), encoding="utf-8") as f:
            src = f.read()
        # La contraparte técnica 2026-10-17: «Ahora no hay turnos» — cuándo vuelve a haber
        # turnos y el caso en texto libre. La franja y «A partir de» se leían
        # como una promesa; el teléfono lo sacó la contraparte técnica el 2026-10-18.
        self.assertIn('T("service.noslots.lead", null, "Ahora no hay turnos.")', src)
        # said once, in the lead (TASK-15): the card carries no second heading
        self.assertNotIn('"No hay turnos disponibles"', src)
        self.assertNotIn('"¿No podés esperar?"', src)
        self.assertIn('"Contanos tu caso"', src)
        self.assertIn('payload.caso = wiz.caso', src)
        self.assertNotIn('id="wz-franja"', src)
        self.assertNotIn('id="wz-desde"', src)
        self.assertNotIn("¿Qué parte del día te resulta más cómoda?", src)
        self.assertNotIn("Más flexibilidad puede significar menos espera.", src)

    def test_wizard_pages_light_no_menu_item_and_paint_the_order_notice(self):
        """A booking wizard page is a task, not the «Centros» section (TASK-29
        batch): no menu item is lit. The order step's server paint carries the
        AI notice the client repaint draws, so the link below does not move."""
        html = site_page_service._page_service_appointment("electrocardiograma-hospital").decode("utf-8")
        self.assertIn('<header id="stznav" data-section="">', html)
        self.assertNotIn('data-section="centros"', html)
        self.assertIn('id="wz-noorden"', html)
        self.assertIn('data-ai-notice="order"', html)
        self.assertLess(html.index('data-ai-notice="order"'), html.index('id="wz-upzone"'))

    def test_confirmacion_lee_como_resumen_humano_y_formatea_identidad(self):
        with open(os.path.join(ROOT, "app", "modules", "turnos", "service.mjs"), encoding="utf-8") as f:
            src = f.read()
        # El resumen dejó de ser un párrafo corrido (2026-07-28, mockup
        # "Confirmar Turno"): servicio/centro viven en la tarjeta del turno y
        # acá queda PARA QUIÉN es. Lo que se sigue guardando es que la
        # identidad se lee como una frase y que DNI y teléfono van formateados.
        self.assertIn('class="wz-pac"', src)
        # "Para {nombre}" pasó por T() en la ronda de i18n del 2026-08-04; el
        # nombre en <b> se arma aparte y viaja como variable del catálogo.
        self.assertIn('var pacienteNombreHtml = "<b>" + esc(personName(paciente)) + "</b>";', src)
        self.assertIn('T("service.patient.for_label", { nombre: pacienteNombreHtml }, "Para {nombre}")', src)
        self.assertIn('DNI <span class="wz-mono">', src)
        self.assertIn('function formatDni', src)
        self.assertIn('function formatPhone', src)
        self.assertIn('function contactField', src)
        self.assertIn('$("#wz-contact-tel", host)', src)
        self.assertIn('$("#wz-contact-email", host)', src)
        self.assertNotIn('class="wz-confirm-for">Para', src)
        self.assertNotIn('contact-phone-ios7.png', src)
        self.assertNotIn('(más pronto)', src)
        self.assertNotIn('<span class="wz-k">Servicio</span>', src)

    def test_confirma_arranca_por_el_turno_y_no_promete_lo_que_no_tiene(self):
        """El turno va arriba de todo y cada variante dice sólo lo que sabe."""
        with open(os.path.join(ROOT, "app", "modules", "turnos", "service.mjs"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("function turnoCardHtml()", src)
        self.assertIn('class="wz-turno"', src)
        # fecha + hora en la cabecera, y la hora aparte (se lee de un vistazo)
        self.assertIn('<span class="wz-hora">', src)
        self.assertIn("Cambiar fecha y horario", src)
        # el reloj del hold sale del ttl_min QUE MANDA EL SERVIDOR, nunca de un
        # 15 escrito acá, y sólo existe si hay un slot concreto reservado
        self.assertIn("res.j.ttl_min", src)
        self.assertIn("if (!wiz.holdHasta || !wiz.slotDia || !wiz.slotHora) return null;", src)
        self.assertIn("Se venció la reserva del horario", src)
        # sin turno: ni fecha ni reloj — el hospital contacta y da la fecha
        self.assertIn("El hospital te contacta para darte la fecha.", src)
        # página blanca, un solo nivel de recuadros, sin sello de cuenta
        # verificada (responsable del producto, 2026-10-27): el paso no va en tarjeta y "¿Cómo te
        # avisamos?" no lleva recuadro — el único es el del turno
        self.assertIn('paginaBlanca(key === "confirm");', src)
        self.assertIn("body.wz-pagina-blanca{background:var(--panel);}", src)
        self.assertIn("background:none;border:0;", src)
        self.assertIn('".wz-avisos{margin-top:24px;}"', src)
        self.assertNotIn("Cuenta verificada", src)
        self.assertNotIn("wz-chip", src)
        self.assertIn("¿Cómo te avisamos?", src)
        # mezclar con blanco en oklch pierde el tono y pinta rosa: va en srgb
        self.assertIn("background:color-mix(in srgb,var(--accent-soft)", src)
        self.assertNotIn("color-mix(in oklch,var(--accent-soft)", src)

    def test_confirma_deja_editar_el_contacto_sin_salir_del_tramite(self):
        """Los datos se leen, pero el que falta se pide y el resto se cambia."""
        with open(os.path.join(ROOT, "app", "modules", "turnos", "service.mjs"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("function contactoSection(u)", src)
        self.assertIn("function contactValue(kind, value)", src)
        self.assertIn('var editTel = editando("tel") || !tel;', src)
        self.assertIn('var editEmail = editando("email") || !email;', src)
        # un "Cambiar" POR dato, al lado del dato que cambia (hook de
        # proximidad, responsable del producto 2026-07-28) — no uno solo que abre los dos
        self.assertIn('data-contact-edit="' + "' + kind + '", src)
        self.assertIn('host.querySelectorAll("[data-contact-edit]")', src)
        # el teléfono es WhatsApp: es por ahí que el hospital escribe. Leído,
        # el dato lleva el ícono (con ese nombre de alt) en vez del rótulo;
        # en edición, el campo conserva su rótulo (responsable del producto, 2026-10-27)
        self.assertIn('{ tel: "WhatsApp", email: "Email" }', src)
        self.assertIn('tel: "/static/icons/whatsapp-sf-regular.svg"', src)
        self.assertIn('email: "/static/icons/email-sf-regular.svg"', src)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "static", "icons", "email-sf-regular.svg")))
        # y el dato es un link: teléfono a WhatsApp, mail a mailto, con el
        # ÚNICO formateador del sitio (format.mjs) — nunca una quinta copia
        self.assertIn("Format.telHtml(value)", src)
        self.assertIn("Format.emailHtml(value)", src)
        self.assertIn('var formatLista = importStatic("platform/format.mjs")', src)
        self.assertNotIn('d.slice(0, 4) + " " + d.slice(4, 6)', src)

    def test_confirma_no_muestra_reloj_y_avisa_en_el_boton_al_final(self):
        """El reloj distrae; el aviso aparece en el botón, y en letras."""
        with open(os.path.join(ROOT, "app", "modules", "turnos", "service.mjs"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn('class="wz-timer"', src)
        self.assertIn("var AVISO_SEG = 60;", src)
        self.assertIn("if (left == null || left > AVISO_SEG) return \"\";", src)
        self.assertIn("function segundosTexto(n)", src)
        self.assertIn('"sesenta"', src)
        self.assertIn('id="wz-send-aviso"', src)
        # y el contacto de escape se mudó adentro de la tarjeta
        self.assertIn("function ayudaHtml()", src)
        self.assertIn('ayudaEnShell(key !== "confirm")', src)

    def test_la_reserva_dura_menos_con_sesion_abierta(self):
        """3 min logueado, 10 sin sesión (responsable del producto, 2026-07-28)."""
        with open(os.path.join(ROOT, "app", "modules", "turnos", "slot_holds.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("_TTL_LOGGED_IN_MIN = 3", src)
        self.assertIn("_TTL_ANON_MIN = 10", src)
        self.assertIn("def _ttl_for(user):", src)
        self.assertIn("ttl_min=ttl", src)
        self.assertIn('"ttl_min": ttl', src)
        # sin poder saberlo, el largo: nunca sacarle el turno a alguien por un
        # problema nuestro
        self.assertIn("return _TTL_ANON_MIN", src)

    def test_el_modo_de_ingreso_viaja_en_el_html_del_wizard(self):
        """Sin esto, Confirmá le ofrecería a una sesión del portal un cambio de
        cuenta que nuestro logout no puede hacer."""
        src = _patient_site_source.source()
        self.assertIn('"auth_mode": auth_mode', src)
        self.assertIn('auth_mode="password"', src)
        self.assertNotIn("def _auth_mode(", src)
        with open(os.path.join(ROOT, "app", "cuentas", "accounts.py"), encoding="utf-8") as f:
            accounts_src = f.read()
        self.assertIn("def auth_mode(headers, client_host):", accounts_src)

    def test_confirmacion_permite_cambiar_solo_a_centro_equivalente(self):
        with open(os.path.join(ROOT, "app", "modules", "turnos", "service.mjs"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("data.puede_cambiar_centro", src)
        self.assertIn('function equivalentCentros()', src)
        self.assertIn('s.modo !== servicio.modo', src)
        self.assertIn('ageSignature(s) !== ageSignature(servicio)', src)
        self.assertIn('normTxt(s.nombre) !== normTxt(servicio.nombre)', src)
        self.assertIn('seen[s.centro]', src)
        self.assertIn('setServicioCentro(match.servicio, match.centro)', src)
        self.assertIn('wiz.slotMedico = null; wiz.slotMedicoId = null; wiz.slotCuando = null;', src)

    def test_confirmacion_reserva_espacio_para_contactos_y_alinea_paciente(self):
        with open(os.path.join(ROOT, "app", "modules", "turnos", "service.mjs"), encoding="utf-8") as f:
            src = f.read()
        # El responsable del producto, 2026-10-27: WhatsApp y email van en UNA línea (fila flexible
        # que en el teléfono baja un dato entero, nunca lo parte), y el
        # paciente queda en el mismo borde izquierdo que la tarjeta del turno.
        self.assertIn(".wz-cuenta{display:flex;flex-wrap:wrap;", src)
        self.assertIn(".wz-editin--tel{width:22ch", src)
        self.assertNotIn(".wz-pac{padding-inline", src)

    def test_exito_tiene_estado_resumen_y_siguiente_accion(self):
        with open(os.path.join(ROOT, "app", "modules", "turnos", "service.mjs"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("function successHtml(confirmado)", src)
        self.assertIn('class="wz-success-state"', src)
        self.assertIn('class="wz-success-summary"', src)
        # "Fecha y hora" pasó por T() (i18n 2026-08-04): la etiqueta de la
        # fila ya no es un literal crudo en el array de rows.
        self.assertIn('[T("service.success.row_datetime", null, "Fecha y hora"), cuando]', src)
        # «Ver mis solicitudes» salió de esta pantalla (responsable del producto 26.10): el recibo es
        # el resumen y la única acción es «Guardar cobertura».
        self.assertNotIn('verRequests', src)

    def test_wizard_mobile_header_is_compact_and_uses_new_asset_version(self):
        src = _patient_site_source.source()
        self.assertIn('.wrap--focus{padding-top:10px;}', src)
        self.assertIn('.h1--focus{font-size:28px;', src)
        self.assertIn('.wrap{width:100%;max-width:1120px;box-sizing:border-box;', src)
        self.assertIn('.svc-head{display:block;margin-top:6px;}', src)
        self.assertIn('#svc-steps{margin-top:var(--space-1);}', src)
        # Tier 4 slice 6, batch 2 (2026-08-08): service.js salió del régimen
        # manual de ?v= — ahora se sirve por URL fingerprinted
        # (static_fingerprint.url), que cambia sola con el contenido, así que
        # ya no hay número de versión que journal a mano ni que pueda quedar
        # desincronizado entre referencias.
        self.assertIn('_turnos_controller_scripts("service.mjs"', src)
        self.assertNotIn("/service.js?v=", src, "service.mjs ya no debería usar el ?v= manual")
        self.assertNotIn('static_fingerprint.url("service.js")', src)

    def test_booking_band_keeps_icon_copy_aligned_and_cta_on_one_line(self):
        src = _patient_site_source.source()
        self.assertIn(".mp-band-txt{min-width:0;flex:1 1 0;}", src)
        self.assertIn(".mp-band-actions .mp-cta{padding-inline:0;}", src)

    def test_admin_preselects_the_recommended_rejection_reason(self):
        # Handoff de diseño 2026-07-17: la línea de prosa "Motivo recomendado: ..."
        # se eliminó (ruido/repetición) — el <select> de motivo YA VIENE
        # pre-elegido con lo que suggestMotivo() encontró, eso ES la recomendación.
        # suggestMotivo/defaultMotivo live in the controller (admin-app.js after
        # the extraction): read via the admin logical source.
        src = _admin_controller()
        self.assertNotIn("function motivoRecomendado", src)
        self.assertNotIn("Motivo recomendado:", src)
        self.assertIn("function suggestMotivo", src)
        self.assertIn("function defaultMotivo", src)
        self.assertIn("m===motivo0?' selected'", src)

    def test_admin_catalog_navigation_tolerates_removed_subtitle(self):
        # The catalog editor lives in static/platform/admin-content.mjs;
        # its classic source was retired by the native migration.
        src = _read("static", "platform", "admin-content.mjs")
        self.assertIn('if (sub && (sec !== "solicitudes" || isDetail))', src)

    def test_admin_queue_uses_compact_responsive_chrome(self):
        # admin.html is deleted: the shell is templates/admin/base.html and
        # the queue screen is templates/admin/solicitudes.html (renderer:
        # static/platform/admin-solicitudes.mjs, controller: admin-app.js).
        shell = _read("templates", "admin", "base.html")
        screen = _read("templates", "admin", "solicitudes.html")
        cola = _read("static", "platform", "admin-solicitudes.mjs")
        # The direct shell owns the presentational table contract; the shared
        # SanaTable module now handles sizing and preferences only.
        direct_css = _read("static/root/admin-direct.css")
        self.assertIn("vertical-align:top;white-space:nowrap", direct_css)
        # La marca del cascarón directo es un anchor textual (adm5-brand); el
        # lockup adm-brand/adm-brand-sub era markup del admin.html borrado.
        self.assertIn('<a class="adm5-brand" href="/admin/solicitudes">{{ sitio(\'brand\') }}</a>', shell)
        # handoff 2026-07-19: el h1 de la cola es "Solicitudes de turno".
        # 2026-07-20: el estado es un <select> del filtro (murieron las
        # pastillas con 4 conteos) y el encabezado "Pendientes de revisión (N)"
        # murió — el conteo vive sólo en el badge de la nav (2026-11-08).
        # 2026-08-05: pasa por i18n (namespace admin.queue) — la plantilla
        # cruda lleva la clave, no el literal.
        self.assertIn('id="adm-h1"', shell)
        self.assertIn("{{ t('admin.queue.titulo_solicitudes', 'Solicitudes') }}", screen)
        # El menú compacto del cascarón directo es el <details> nativo
        # (adm5-menu) — no hay #adm-menu-toggle; los filtros siguen siendo
        # un toggle + campos.
        self.assertIn('<details class="adm5-menu" open>', shell)
        self.assertIn('id="adm-filter-toggle"', screen)
        self.assertIn('id="adm-filter-fields"', screen)
        self.assertIn('id="f-estado"', screen)
        self.assertIn('id="f-sug"', screen)
        self.assertNotIn('class="adm-filter on"', screen + cola)
        self.assertNotIn('class="adm-band-div"', shell + screen)
        self.assertNotIn('Pendientes de revisión (${pend.length})', cola)

    def test_admin_hsi_es_su_propia_seccion_no_un_bloque_en_la_cola(self):
        # 2026-07-22: el <details> de conflictos incrustado en la cola murió;
        # los conflictos que el resolutor no cierra viven en la sección «HSI»
        # (nav propio) y cada uno linkea a su solicitud.
        # The direct /admin/hsi screen renders the section
        # (templates/admin/hsi.html) and the native controller
        # (static/platform/admin-hsi.mjs) owns its handler.
        pantalla = _read("templates", "admin", "hsi.html")
        controlador = _read("static", "platform", "admin-hsi.mjs")
        self.assertNotIn('data-open-hsi-conflicts', controlador)
        self.assertNotIn('id="hsi-conflicts"', controlador + pantalla)
        self.assertIn('id="sec-hsi"', pantalla)
        self.assertIn('export function mount(host, options)', controlador)
        self.assertIn('/api/admin/hsi-sync/resolver', controlador)
        self.assertIn('href="/admin/solicitudes"', controlador)

    def test_admin_shared_route_titles_the_queue_solicitudes_de_turno(self):
        src = _read("static", "platform", "admin-content.mjs")
        # El label puede venir directo o envuelto en T(key, vars, "Solicitudes
        # de turno") (i18n) — lo que importa es el texto castellano exacto.
        # Desde 2026-10-21 es un getter (get solicitudes() { return T(…) }),
        # para que el panel en inglés no quede con el fallback castellano.
        self.assertRegex(src, r'(?:solicitudes: |get solicitudes\(\) \{ return )(?:T\([^)]*?, )?"Solicitudes de turno"')

    def test_admin_attendance_is_one_compact_visit_signal(self):
        # The dialog lives in the direct screen's template
        # (templates/admin/solicitudes.html) and the handler/dots in the
        # controller (admin-app.js).
        src = _admin_controller()
        with open(os.path.join(ROOT, "static", "platform", "admin-solicitudes.mjs"), encoding="utf-8") as f:
            cola = f.read()
        self.assertIn('id="attendance-dialog"', _read("templates", "admin", "solicitudes.html"))
        # 2026-08-06: Aprobadas/Rechazadas/Todas dejaron la tabla vieja
        # (RES_COLS con un `label:` por columna) y pasan por la misma cola
        # densa que Pendientes — asistencia y visitas viven ahora bajo UN
        # encabezado «Visita» en el panel de detalle (detalleResHtml, en
        # static/platform/admin-solicitudes.mjs), no en dos columnas separadas de una tabla.
        self.assertRegex(cola, r"T\(\"admin\.queue\.solicitudes\.col_visita\", null, \"Visita\"\)")
        self.assertIn('data-attendance-detail', src)
        self.assertIn('attendance-dot--${level.cls}', src)
        # attendanceLevel() produces the same three visible levels, with the
        # compact JS object spelling used by the direct controller.
        for level in ("good", "warn", "bad"):
            self.assertIn("cls:'" + level + "'", src)
        self.assertNotIn("label:'Faltas'", src)
        self.assertNotIn("label:'Web'", src)
        self.assertNotIn("label:'Historial de asistencia'", src)
        self.assertNotIn("label:'Tipo de turno'", src)
        self.assertNotIn('Disponible después del turno', src)

    def test_pending_page_has_selection_and_one_bulk_bar(self):
        # runBulk/COLA_SEL live in the controller (admin-app.js after the
        # extraction): the controller is the logical source now that the
        # legacy admin.html shell is deleted.
        src = _admin_controller()
        with open(os.path.join(ROOT, "static", "platform", "admin-solicitudes.mjs"), encoding="utf-8") as f:
            cola = f.read()
        # La casilla por fila y las acciones de lote siguen existiendo; desde
        # el rediseño 2026-08-05 el markup vive en static/platform/admin-solicitudes.mjs y la
        # barra aparece SÓLO con filas tildadas (una barra permanente con tres
        # botones apagados era chrome sin contenido).
        self.assertIn('data-pending-select', cola)
        self.assertIn('function selbarHtml(n, conSugerencia)', cola)
        # UNA sola barra de lote (responsable del producto, 2026-07-19: dos barras idénticas eran
        # un duplicado) y el checkbox por fila va pelado, sin texto al lado
        self.assertEqual(cola.count('selbarHtml('), 2)  # definición + llamada
        self.assertNotIn('bulk-card-select', cola)
        self.assertIn('data-bulk-action="aprobar"', cola)
        self.assertIn('data-bulk-action="rechazar"', cola)
        self.assertIn('function runBulk(action, rejectReason)', src)
        # la selección vive en el estado, no en el DOM: al abrir otra
        # solicitud se redibuja la cola entera
        self.assertIn('const COLA_SEL = new Set()', src)

    def test_home_no_filtra_por_edad_del_titular(self):
        with open(os.path.join(ROOT, "templates", "index.html"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn("puede estar buscando para un hijo", src)
        self.assertNotIn('fetch("/api/account/me", { credentials: "include" })', src)


if __name__ == "__main__":
    unittest.main()
