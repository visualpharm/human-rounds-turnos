"""Los términos y la política de privacidad: qué dicen, y que se puedan editar.

Dos cosas distintas se prueban acá:

  - **El contenido.** Hay promesas que el sistema le hace al paciente y que
    tienen que estar escritas: que la IA corre en servidores externos, que
    hacemos un esfuerzo razonable por anonimizar, que la imagen de la orden
    sale tal cual, y que el chat y la historia clínica salen sin sus datos.
    Si alguien saca una de esas cláusulas, el código deja de coincidir con lo
    publicado — y eso es un problema legal, no de estilo.
  - **La mecánica.** El nombre de la instalación es un placeholder (dice
    "Turnos de salud pública en Pinamar" en Pinamar y "Human Rounds" en la
    demo), el documento es
    UNO solo para las dos, y el markdown editable nunca emite un href
    ejecutable (revisión de seguridad 2026-08-03).
"""

import os
import re
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ops import demo_installation  # noqa: E402
from app.patient_web import legal  # noqa: E402
from app.core import site_identity  # noqa: E402
from app.core import theme  # noqa: E402
import _env  # noqa: E402


class TestLinksSeguros(unittest.TestCase):
    def test_link_https_queda_como_esta(self):
        html = legal._inline("Mirá [la guía](https://example.com/guia)")
        self.assertIn('<a href="https://example.com/guia">la guía</a>', html)

    def test_link_relativo_y_mailto_quedan(self):
        self.assertIn('href="/turnos"', legal._inline("[turnos](/turnos)"))
        self.assertIn('href="mailto:a@b.c"', legal._inline("[escribinos](mailto:a@b.c)"))

    def test_javascript_se_neutraliza(self):
        html = legal._inline("[click](javascript:alert(1))")
        self.assertIn('href="#"', html)
        self.assertNotIn("javascript:", html)

    def test_javascript_ofuscado_con_control_chars_se_neutraliza(self):
        # \t o espacio nunca forman link (_LINK_RE no admite \s en la URL);
        # un control char fuera de \s sí llega al href y tiene que morir ahí.
        html = legal._inline("[click](java\x01script:alert(1))")
        self.assertIn('href="#"', html)
        self.assertNotIn("script:alert", html)


class TestContenido(unittest.TestCase):
    """Las cláusulas que el responsable del producto pidió el 2026-08-03 y no pueden desaparecer."""

    def setUp(self):
        self.terminos = legal.original("terminos")["cuerpo"]
        self.privacidad = legal.original("privacidad")["cuerpo"]

    def test_dice_que_la_ia_corre_en_servidores_externos(self):
        for cuerpo in (self.terminos, self.privacidad):
            self.assertIn("externos", cuerpo)
        self.assertIn("fuera de la Argentina", self.privacidad)
        self.assertIn("sale del hospital", self.privacidad)

    def test_dice_que_el_esfuerzo_de_anonimizacion_es_razonable_sin_garantia(self):
        self.assertIn("esfuerzo razonable", self.privacidad)
        self.assertIn("no podemos garantizar", self.privacidad.lower())

    def test_dice_que_la_imagen_de_la_orden_va_sin_modificar(self):
        self.assertIn("sin modificar", self.privacidad)
        self.assertIn("orden de derivación", self.privacidad)

    def test_dice_que_el_chat_y_la_historia_clinica_salen_sin_datos_personales(self):
        for surface in ("chat", "entrevista previa", "resumen de tu historia clínica"):
            self.assertIn(surface, self.privacidad)
        self.assertIn("[nombre]", self.privacidad)
        self.assertIn("[documento]", self.privacidad)

    def test_dice_que_los_reportes_de_errores_salen_filtrados_y_que_va(self):
        # core/error_tracking.py: allowlist, sin IP/cookies/cuerpo/variables.
        # El filtro no atrapa un nombre: la política no promete «sin datos».
        for frase in ("Sentry", "quitados o tapados", "lista cerrada de campos",
                      "tu dirección IP", "los valores que el programa tenía en memoria",
                      "300 caracteres", "pantallas del personal",
                      "puede estar alojado fuera de la Argentina",
                      "no prometemos que un reporte nunca lleve un dato tuyo"):
            self.assertIn(frase, self.privacidad)
        self.assertNotIn("sin datos que te identifiquen", self.privacidad)

    def test_los_logs_tapan_dni_telefono_y_email_y_nunca_el_mensaje_de_error(self):
        # core/log_pii.py + AccessLogFilter + log_pii.install(): una falla se
        # anota por tipo y código, nunca por su mensaje (test_logs_pii lo
        # fija sobre todo el código del servidor).
        for frase in ("después del signo «?»", "el DNI, el teléfono y el email aparecen tapados",
                      "nunca el mensaje de error",
                      "depende de la configuración de ese servidor"):
            self.assertIn(frase, self.privacidad)
        self.assertNotIn("a veces repite un dato que recibió", self.privacidad)

    def test_los_plazos_de_borrado_son_los_del_codigo(self):
        # booking_requests (180), housekeeping (chat 90, sesiones 30): si
        # cambia un default, cambia esta frase.
        from app.modules.turnos import booking_referrals
        self.assertEqual(booking_referrals._REFERRAL_RETENTION_DAYS_DEFAULT, 180)
        for frase in ("a los 180 días", "Los mensajes del chat, a los 90 días",
                      "después de 30 días sin uso", "la instalación puede ajustarlos",
                      "no tienen un plazo de borrado automático",
                      "salvo lo que la ley obliga a conservar"):
            self.assertIn(frase, self.privacidad)

    def test_leyenda_de_la_ley_25326_completa(self):
        # Resolución AAIP 14/2018, art. 3 (derogó la Disposición DNPDP
        # 10/2008): el texto del órgano de control va literal; el derecho de
        # acceso del art. 14 inc. 3 de la ley, con su salvedad.
        for frase in ("intervalos no inferiores a seis meses",
                      "salvo que se acredite un interés legítimo",
                      "artículo 14, inciso 3 de la Ley N° 25.326",
                      "Agencia de Acceso a la Información Pública, en su carácter de Órgano "
                      "de Control de la Ley N° 25.326, tiene la atribución de atender las "
                      "denuncias y reclamos", "Ley 26.529"):
            self.assertIn(frase, self.privacidad)

    def test_los_derechos_nombran_el_canal_y_los_plazos_de_la_ley_25326(self):
        # Arts. 14 y 16 de la Ley 25.326: acceso 10 días corridos; corrección
        # y supresión 5 días hábiles. El canal es lo que la instalación ya
        # publica (/centers); nunca una dirección o un responsable inventados
        # (el responsable de la base es una pregunta abierta al hospital).
        derechos = self.privacidad.split("## 8. Tus derechos")[1].split("## 9.")[0]
        for frase in ("**Cómo pedirlo.**", "**Plazos.**", "El pedido es gratuito",
                      "10 días corridos", "5 días hábiles", "ni los feriados",
                      "verificamos tu identidad", "no se borra a pedido",
                      "pueden tener sus propias copias",
                      "[centros de salud](/centers)", "{{sitio.hospital}}"):
            self.assertIn(frase, derechos)
        self.assertNotIn("@", derechos)
        self.assertNotRegex(derechos, r"\d{3,}-?\d{3,}")

    def test_lo_que_no_se_borra_a_pedido_nombra_el_hsi_y_las_copias_cifradas(self):
        # La copia en el HSI es de la provincia (sólo su administrador la
        # corrige o borra) y los respaldos cifrados conservan lo borrado hasta
        # su rotación (core/backup_settings.py: TURNOS_BACKUP_RETAIN_*); al
        # restaurar se vuelven a aplicar los borrados del libro de pedidos.
        derechos = self.privacidad.split("## 8. Tus derechos")[1].split("## 9.")[0]
        bloque = derechos.split("**Lo que no se borra a pedido.**")[1]
        for frase in ("Historia de Salud Integrada", "quien administra el HSI",
                      "pasándole tu pedido", "copias de seguridad cifradas",
                      "hasta que se renuevan por rotación",
                      "volvemos a aplicar los borrados", "proveedores externos"):
            self.assertIn(frase, bloque)

    def test_dice_donde_quedan_la_foto_de_perfil_las_fotos_de_la_entrevista_y_gravatar(self):
        datos = self.privacidad.split("## 1. Qué datos manejamos")[1].split("## 2.")[0]
        for frase in ("**Foto de perfil:**", "retrato recortado", "asociada a tu DNI",
                      "no se borra sola", "**Gravatar:**", "código calculado a partir de tu email",
                      "no el email escrito", "**Foto en la entrevista previa:**",
                      "sin modificar al proveedor de inteligencia artificial"):
            self.assertIn(frase, datos)
        # La sección 7 no promete un borrado automático que el código no hace.
        conservacion = self.privacidad.split("## 7.")[1].split("## 8.")[0]
        self.assertIn("tu foto de perfil, la foto que hayas adjuntado a la entrevista previa",
                      conservacion)

    def test_lo_dicho_de_gravatar_es_lo_que_hace_el_codigo(self):
        # account_photo.py: MD5 del email hacia gravatar.com, apagable con
        # TURNOS_GRAVATAR_OFF. Si el código cambia, esta frase también.
        import inspect
        from app.cuentas import account_photo
        fuente = inspect.getsource(account_photo)
        self.assertIn("hashlib.md5(email", fuente)
        self.assertIn("https://www.gravatar.com/avatar/", fuente)
        self.assertIn("TURNOS_GRAVATAR_OFF", fuente)
        self.assertTrue(hasattr(account_photo, "_GRAVATAR_OFF"))

    def test_las_medidas_de_seguridad_citan_la_resolucion_47_2018(self):
        # Las medidas de seguridad recomendadas son las de la Resolución AAIP
        # 47/2018; la 14/2018 es la del texto de información de la sección 8,
        # no la de seguridad.
        seguridad = self.privacidad.split("## 7. Conservación y seguridad")[1].split("## 8.")[0]
        self.assertIn("Resolución AAIP 47/2018", seguridad)
        self.assertNotIn("14/2018", self.privacidad)

    def test_un_texto_editado_recibe_la_seccion_de_registros_tecnicos(self):
        viejo = "Bajada.\n\n## 5. IA\n\nTexto IA.\n\n## 6. Conservación\n\nGuardamos poco.\n"
        nuevo = legal._con_registros_tecnicos(viejo)
        self.assertIn("## 5. IA\n\nTexto IA.\n\n## Registros técnicos y reportes de errores\n\n"
                      "**Registros del servidor", nuevo)
        self.assertIn("dato tuyo.\n\n## 6. Conservación\n\nGuardamos poco.", nuevo)
        self.assertEqual(nuevo.count("## Registros técnicos"), 1)
        # Con la sección vieja: se reemplaza el texto, el título del hospital queda.
        editado = nuevo.replace("**Registros del servidor (logs).**", "Texto viejo.")
        editado = editado.replace("## Registros técnicos", "## 5 bis. Registros técnicos")
        otra = legal._con_registros_tecnicos(editado)
        self.assertIn("## 5 bis. Registros técnicos", otra)
        self.assertNotIn("Texto viejo.", otra)
        self.assertEqual(legal._con_registros_tecnicos(legal.original("privacidad")["cuerpo"]),
                         legal.original("privacidad")["cuerpo"])

    def test_dice_quien_abrio_tus_datos_de_salud_y_lo_que_hace_el_registro(self):
        # K10 (2026-10-23): la política dice lo que hace core/access_log — ni
        # más ni menos — y queda atada a los números del código.
        from app.core import access_log
        texto = self.privacidad
        self.assertIn("Quién abrió tus datos de salud", texto)
        for frase in ("quién fue, cuándo, qué abrió", "si el sistema se lo permitió",
                      "tu DNI no se guarda tal cual", "no se puede modificar",
                      "la primera vez de cada día", "diez años"):
            self.assertIn(frase, texto)
        with unittest.mock.patch.object(access_log.config, "get", return_value=None):
            self.assertEqual(access_log.retention_days(), 3650)
        # El permiso «todos los pacientes» (responsable del producto 2026-10-23, q781376) está
        # dicho, y existe con ese alcance en el catálogo de permisos.
        from app.core import permissions
        self.assertIn("permiso expreso de ver los pacientes de todos los médicos", texto)
        self.assertIn("esos accesos también quedan registrados", texto)
        self.assertEqual(access_log.ALL_PATIENTS, "todos_los_pacientes")
        self.assertIn(access_log.ALL_PATIENTS, permissions.PERMISSIONS)
        # La cláusula entró el 2026-10-23; cada edición posterior sólo mueve la fecha hacia adelante.
        self.assertGreaterEqual(legal.original("privacidad")["actualizado"], "2026-10-23")

    def test_aclara_que_no_rige_para_la_demo(self):
        for cuerpo in (self.terminos, self.privacidad):
            self.assertIn("instalaciones de demostración", cuerpo)

    def test_el_nombre_de_la_instalacion_nunca_esta_escrito_literal(self):
        # "Human Rounds" sí aparece: es el nombre del proyecto en la cláusula
        # que nombra a las demos, no el de esta instalación.
        for cuerpo in (self.terminos, self.privacidad):
            self.assertNotIn("Pinamar Turnos", cuerpo)
            self.assertNotIn("Turnos de salud pública en Pinamar", cuerpo)
            self.assertIn("{{sitio.brand}}", cuerpo)


_RESPONSABLE = "Municipalidad de Pinamar — Secretaría de Salud"


class _ConResponsable:
    """La instalación nombra a su responsable de los datos (salud.pinamar.gob.ar)."""

    def setUp(self):
        _env.set_env(self, SITE_ID_DATA_CONTROLLER=_RESPONSABLE)
        super().setUp()


class TestContenidoInstitucional(_ConResponsable, TestContenido):
    """Cada cláusula fáctica del prototipo vale igual en la versión
    institucional: la heredamos entera y la corremos otra vez."""

    def test_es_la_version_institucional(self):
        self.assertTrue(legal.institucional())
        self.assertIn("{{sitio.data_controller}}", self.terminos)
        self.assertIn("{{sitio.data_controller}}", self.privacidad)


class TestVersionInstitucional(_ConResponsable, unittest.TestCase):
    """salud.pinamar.gob.ar: un organismo público con pacientes reales no
    publica «prototipo» ni deja de responder por una filtración (d1147202)."""

    def setUp(self):
        super().setUp()
        self.terminos = legal.original("terminos")["cuerpo"]
        self.privacidad = legal.original("privacidad")["cuerpo"]

    def test_sin_prototipo_ni_descargo_por_la_perdida_o_la_filtracion(self):
        for cuerpo in (self.terminos, self.privacidad):
            for frase in ("prototipo", "en desarrollo", "filtración", "acceso no autorizado a datos",
                          "tal cual está", "exclusiva responsabilidad",
                          "sin que eso genere responsabilidad", "sin garantías",
                          "bien público", "en criollo"):
                self.assertNotIn(frase, cuerpo.lower() if frase.islower() else cuerpo)

    def test_nombra_al_responsable_y_las_leyes(self):
        for cuerpo in (self.terminos, self.privacidad):
            self.assertIn("Ley 25.326", cuerpo)
            self.assertIn("Ley 26.529", cuerpo)
        marco = self.privacidad.split("## 9. Responsable de los datos y marco legal")[1]
        for frase in ("El responsable de la base de datos es **{{sitio.data_controller}}**",
                      "artículo 8 de la Ley 25.326", "artículo 18", "¿Es obligatorio darnos tus datos?"):
            self.assertIn(frase, marco)
        derechos = self.privacidad.split("## 8. Tus derechos")[1].split("## 9.")[0]
        self.assertIn("ante {{sitio.data_controller}}", derechos)
        seguridad = self.privacidad.split("## 7.")[1].split("## 8.")[0]
        self.assertIn("responde por la seguridad de tus datos", seguridad)

    def test_las_secciones_que_describen_el_codigo_son_las_del_prototipo(self):
        # Una sola redacción por hecho: lo que dice qué datos, el registro de
        # accesos, la IA y los registros técnicos sale del mismo texto.
        for n in range(1, 7):
            self.assertEqual(legal._seccion(self.privacidad, n), legal._seccion(legal._PRIVACIDAD, n))
        for n in (2, 4, 5, 7):
            self.assertEqual(legal._seccion(self.terminos, n), legal._seccion(legal._TERMINOS, n))

    def test_el_texto_no_lleva_marcas_de_asesoria(self):
        # Las preguntas abiertas viven en PENDIENTES_ASESORIA, no en la página.
        for cuerpo in (self.terminos, self.privacidad):
            self.assertEqual(legal.marcas_asesoria(cuerpo), [])
            self.assertNotIn("Asesoría", cuerpo)
        self.assertEqual(len(legal.PENDIENTES_ASESORIA), 7)
        self.assertIn("Los pedidos de acceso, rectificación y supresión", self.privacidad)

    def test_la_seccion_de_ia_y_los_registros_se_refrescan_igual(self):
        # document() refresca la sección 5 por su número; el número no se movió.
        self.assertEqual(legal._con_registros_tecnicos(self.privacidad), self.privacidad)
        self.assertIn("## 5. La inteligencia artificial", self.privacidad)

    def test_la_pagina_nombra_al_responsable(self):
        html = site_identity.render_page("privacy.html")[0].decode("utf-8")
        self.assertIn("Municipalidad de Pinamar", html)
        self.assertNotIn("{{sitio.", html)
        self.assertNotIn("prototipo", html)
        html = site_identity.render_page("terms.html")[0].decode("utf-8")
        self.assertIn("Municipalidad de Pinamar", html)
        self.assertNotIn("prototipo", html)

    def test_sin_responsable_queda_el_texto_del_prototipo(self):
        # pinamar.humanrounds.org y la demo: nada cambia.
        _env.set_env(self, SITE_ID_DATA_CONTROLLER=None)
        self.assertFalse(legal.institucional())
        self.assertEqual(legal.original("terminos")["cuerpo"], legal._TERMINOS)
        self.assertEqual(legal.original("privacidad")["cuerpo"], legal._PRIVACIDAD)
        self.assertIn("prototipo", legal.original("terminos")["cuerpo"])


class TestMarkdown(unittest.TestCase):

    def test_titulos_listas_recuadros_negritas_y_enlaces(self):
        html = legal.markdown_html(
            "Bajada.\n\n## Uno\n\nTexto **fuerte**.\n\n- a\n- b\n\n> Ojo [acá](/x).")
        self.assertIn('<p class="lead">Bajada.</p>', html)
        self.assertIn("<h2>Uno</h2>", html)
        self.assertIn("<b>fuerte</b>", html)
        self.assertIn("<ul><li>a</li><li>b</li></ul>", html)
        self.assertIn('<div class="box"><p>Ojo <a href="/x">acá</a>.</p></div>', html)

    def test_el_html_del_texto_editable_se_escapa(self):
        html = legal.markdown_html("Un <script>alert(1)</script> cualquiera.")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_la_fecha_se_escribe_en_el_idioma_de_la_pagina(self):
        self.assertEqual(legal._fecha_larga("2026-08-03"), "3 de agosto de 2026")
        self.assertEqual(legal._fecha_larga("2026-08-03", "en"), "August 3, 2026")
        self.assertEqual(legal._fecha_larga("cualquier cosa"), "cualquier cosa")


class _TraductorQuePierdeTokens:
    @staticmethod
    def localized_text(source_key, text, lang, pack_lang=None):
        return "Hello the hospital."


class _TraductorFiel:
    @staticmethod
    def localized_text(source_key, text, lang, pack_lang=None):
        return f"[{lang}] {text}"


class TestTraduccion(unittest.TestCase):
    """Un documento legal traducido a medias es peor que uno sin traducir."""

    def _con_traductor(self, traductor, cuerpo, lang="en"):
        anterior = legal.translate
        try:
            legal.translate = traductor
            return legal._traducido("terminos", cuerpo, lang)
        finally:
            legal.translate = anterior

    def test_una_traduccion_que_pierde_el_nombre_de_la_institucion_se_descarta(self):
        cuerpo = "Hola {{sitio.brand}}."
        self.assertEqual(self._con_traductor(_TraductorQuePierdeTokens(), cuerpo),
                         cuerpo)

    def test_una_traduccion_que_conserva_los_tokens_se_usa(self):
        self.assertEqual(
            self._con_traductor(_TraductorFiel(), "Hola {{sitio.brand}}."),
            "[en] Hola {{sitio.brand}}.")


class TestPaginas(unittest.TestCase):
    """Las páginas públicas: sin placeholders sueltos y con la marca resuelta."""

    def _render(self, filename):
        resultado = site_identity.render_page(filename)
        self.assertIsNotNone(resultado)
        return resultado[0].decode("utf-8")

    def test_no_queda_ningun_placeholder_sin_resolver(self):
        for filename in ("terms.html", "privacy.html"):
            html = self._render(filename)
            with self.subTest(filename=filename):
                for token in ("{{legal.", "{{t.", "{{sitio."):
                    self.assertNotIn(token, html)

    def test_el_cuerpo_editable_llega_a_la_pagina(self):
        html = self._render("privacy.html")
        self.assertIn("servidores de proveedores externos", html)
        self.assertIn('<p class="updated">Última actualización:', html)

    def test_el_titulo_de_la_portada_en_pinamar_es_la_marca_sin_repetirla(self):
        anterior = theme.current
        marca = "Turnos de salud pública en Pinamar"
        try:
            theme.current = lambda: "pinamar"
            html = self._render("index.html")
            self.assertIn(f"<title>{marca}</title>", html)
            self.assertIn(f'<meta property="og:title" content="{marca}">', html)
            self.assertIn(f'<meta name="twitter:title" content="{marca}">', html)
            theme.current = lambda: "humanrounds"
            html = self._render("index.html")
            self.assertIn(" | Human Rounds</title>", html)
        finally:
            theme.current = anterior

    def test_la_marca_de_la_instalacion_reemplaza_al_placeholder(self):
        anterior = theme.current
        try:
            theme.current = lambda: "humanrounds"
            html = self._render("terms.html")
            self.assertIn("Human Rounds", html)
            self.assertNotIn("Turnos de salud pública en Pinamar", html)
            theme.current = lambda: "pinamar"
            for pagina in ("terms.html", "privacy.html"):
                self.assertIn("Turnos de salud pública en Pinamar", self._render(pagina))
        finally:
            theme.current = anterior


class TestPaginasSinRelleno(unittest.TestCase):
    """Una página legal pública nunca muestra una marca «[Asesoría Letrada: …]»
    ni un corchete de relleno, en ninguna configuración (c1cd1a13, 29.10)."""

    _RELLENO = re.compile(r"\[Asesor|\[[^\]\n]*(?:…|\.\.\.)[^\]\n]*\]")
    _MARCADO = ("Antes.\n\n**[Asesoría Letrada: jurisdicción.]**\n\n"
                "- **[Asesoría Letrada: domicilio.]**\n- Un ítem real.\n\n"
                "Texto **[Asesoría Letrada: en línea.]** sigue.")

    def _sin_relleno(self, html, donde):
        self.assertIsNone(self._RELLENO.search(html), donde)
        self.assertNotIn("Asesoría Letrada", html, donde)

    def test_ninguna_configuracion_publica_marcas(self):
        for responsable in (None, _RESPONSABLE):
            for demo in (None, "1"):
                _env.set_env(self, SITE_ID_DATA_CONTROLLER=responsable, TURNOS_DEMO_MODE=demo)
                for filename in ("terms.html", "privacy.html"):
                    for lang in (None, "en"):
                        with self.subTest(responsable=bool(responsable), demo=demo, page=filename, lang=lang):
                            doc = "terminos" if filename == "terms.html" else "privacidad"
                            self._sin_relleno(legal.body_html(doc, lang), "body_html")
                    html = site_identity.render_page(filename)[0].decode("utf-8")
                    with self.subTest(responsable=bool(responsable), demo=demo, page=filename, render=True):
                        self._sin_relleno(html, "render_page")

    def test_una_fila_guardada_con_el_texto_de_antes_tampoco_las_publica(self):
        _env.set_env(self, SITE_ID_DATA_CONTROLLER=_RESPONSABLE)
        fila = {"body": self._MARCADO, "updated": None, "updated_by": "x"}
        with unittest.mock.patch("app.core.db_catalog.legal_get", return_value=fila):
            for doc in ("terminos", "privacidad"):
                html = legal.body_html(doc)
                self._sin_relleno(html, doc)
                self.assertIn("Un ítem real.", html)
                self.assertNotIn("<li></li>", html)

    def test_la_marca_no_llega_al_traductor(self):
        visto = []

        class _Espia:
            @staticmethod
            def localized_text(source_key, text, lang, pack_lang=None):
                visto.append(text)
                return text

        anterior = legal.translate
        legal.translate = _Espia
        try:
            _env.set_env(self, SITE_ID_DATA_CONTROLLER=_RESPONSABLE)
            fila = {"body": self._MARCADO, "updated": None, "updated_by": "x"}
            with unittest.mock.patch("app.core.db_catalog.legal_get", return_value=fila):
                legal.body_html("terminos", "en")
        finally:
            legal.translate = anterior
        self.assertTrue(visto)
        for texto in visto:
            self.assertNotIn("Asesoría", texto)

    def test_sin_marcas_deja_el_resto_intacto(self):
        self.assertEqual(legal.sin_marcas("Hola.\n\nChau."), "Hola.\n\nChau.")
        self.assertEqual(legal.sin_marcas("a **[Asesoría Letrada: x.]** b"), "a  b")

    def test_las_preguntas_para_la_asesoria_siguen_en_un_solo_lugar(self):
        todas = " ".join(legal.PENDIENTES_ASESORIA)
        for tema in ("responsabilidad del Municipio", "jurisdicción", "incidente",
                     "domicilio", "Registro Nacional de Bases de Datos", "artículo 12",
                     "historia clínica"):
            self.assertIn(tema, todas)


class TestAvisoDeDemo(unittest.TestCase):

    def test_la_instalacion_real_no_muestra_aviso_de_demo(self):
        _env.set_env(self, TURNOS_DEMO_MODE=None)
        self.assertFalse(demo_installation.enabled())
        self.assertEqual(legal._aviso_demo("es"), "")

    def test_la_demo_avisa_que_el_documento_no_rige(self):
        _env.set_env(self, TURNOS_DEMO_MODE="1")
        aviso = legal._aviso_demo("es")
        self.assertIn("demostración", aviso)
        self.assertIn("no rige", aviso)

    def test_es_el_MISMO_documento_en_las_dos_instalaciones(self):
        # No hay un texto legal aparte para la demo: el cuerpo es idéntico y
        # sólo cambian el nombre de la instalación y el aviso de arriba.
        sin_demo = legal.document("terminos")["cuerpo"]
        _env.set_env(self, TURNOS_DEMO_MODE="1")
        self.assertEqual(legal.document("terminos")["cuerpo"], sin_demo)


if __name__ == "__main__":
    unittest.main()
