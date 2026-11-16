"""/news — historia de actualizaciones (responsable del producto, 2026-07-15).

Curada del git log: sólo cambios que un paciente o el hospital notan (nada de
espaciados ni retoques menores). Link menor gris en el footer del sitio.
"""

import os
import re
import unittest
from unittest import mock

from app.core import i18n
from app.core import site_identity, theme, translate
from app.patient_web import site_news, site_page_doctors

import _env

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The frozen 2026-07-08 entry (docs: "do not relitigate") — the only one with
# a link, so it doubles as the fixture for the markup-preserving translation
# tests below.
_NACE_KEY = "news.entry.8-de-julio-de-2026.0"
_NACE_TEXT = (
    '<b>Nace <a href="https://humanrounds.org/" target="_blank" '
    'rel="noopener">Human Rounds</a></b>, la plataforma detrás de Pinamar '
    'Turnos: los servicios del Hospital de Pinamar y los CAPS, con '
    'solicitud de turno por orden médica.'
)


def _identity_cached_text(_key, text, _lang, pack_lang=None):
    """Stand-in for translate.cached_text: always a cache miss (returns the
    tokenized text unchanged), so every entry degrades to Spanish."""
    return text


class TestNovedades(unittest.TestCase):
    def test_pagina_renderiza(self):
        html = site_news._page_news().decode()
        self.assertIn("<h1>Novedades</h1>", html)
        # la más vieja y la más nueva están; el orden es nuevo→viejo
        self.assertIn("Nace <a href=\"https://humanrounds.org/\"", html)
        # ojo: "8 de julio" es substring de "18 de julio" — comparar por el
        # <h2> completo para no matchear la sección equivocada.
        self.assertLess(
            html.index("<h2>15 de julio de 2026</h2>"),
            html.index("<h2>8 de julio de 2026</h2>"),
        )

    def test_footer_linkea_novedades(self):
        # mismo estilo que el resto de los links del footer (responsable del producto, 2026-07-16:
        # el link gris menor no se veía bien)
        html = site_page_doctors._page_doctors().decode()
        self.assertIn('<a href="/news">Novedades</a>', html)

    def test_home_and_dynamic_pages_render_the_same_footer(self):
        home = site_identity.render_page("index.html")[0].decode("utf-8")
        doctors = site_page_doctors._page_doctors().decode("utf-8")
        footer = re.compile(r'<footer class="site">.*?</footer>', re.S)
        self.assertEqual(footer.search(home).group(0), footer.search(doctors).group(0))

    def test_generic_installation_does_not_publish_reference_place_names(self):
        identity = {
            "city": "Sampleton",
            "hospital": "Sampleton",
        }

        def get(key, default=None):
            return identity.get(key, default)

        with mock.patch.object(site_identity, "get", side_effect=get), \
             mock.patch.object(theme, "brand_name",
                               return_value="Human Rounds"):
            html = site_news._page_news().decode()
        self.assertNotIn("Pinamar", html)
        self.assertIn("Sampleton", html)
        self.assertIn("Human Rounds", html)


class TestNovedadesI18n(unittest.TestCase):
    """/news in English (2026-08-04): chrome via i18n.py, entries via
    translate.py's cache. See docs/architecture/i18n.md."""

    def test_pagina_es_no_toca_el_cache_de_traduccion(self):
        # En español ni siquiera se llama a translate.cached_text — ni
        # tokeniza de más ni depende de la base para la página que YA
        # tenemos (el "the Spanish page is unchanged" del batch).
        with mock.patch.object(translate, "cached_text") as m:
            html = site_news._page_news().decode()
        m.assert_not_called()
        self.assertIn("<h1>Novedades</h1>", html)
        self.assertIn("Nace <a href=\"https://humanrounds.org/\"", html)

    def test_chrome_en_ingles(self):
        with mock.patch.object(i18n, "active", return_value="en"), \
             mock.patch.object(translate, "cached_text",
                                side_effect=_identity_cached_text):
            html = site_news._page_news().decode()
        self.assertIn("<h1>What's new</h1>", html)
        self.assertIn("By request", html)
        self.assertNotIn("[en] ", html)

    def test_fechas_en_ingles(self):
        # "4 de agosto de 2026" -> "August 4, 2026"; español no se toca.
        self.assertEqual(
            site_news._news_fecha_localized("4 de agosto de 2026", "en"),
            "August 4, 2026")
        self.assertEqual(
            site_news._news_fecha_localized("4 de agosto de 2026", "es"),
            "4 de agosto de 2026")

    def test_encabezados_de_fecha_traducidos_en_ingles(self):
        with mock.patch.object(i18n, "active", return_value="en"), \
             mock.patch.object(translate, "cached_text",
                                side_effect=_identity_cached_text):
            html = site_news._page_news().decode()
        self.assertIn("<h2>August 4, 2026</h2>", html)
        self.assertNotIn("<h2>4 de agosto de 2026</h2>", html)

    def test_entrada_traducida_usa_el_cache(self):
        tokenized, _tags = site_news._news_tokenize(_NACE_TEXT)
        fake_en = tokenized.replace("Nace", "Here comes").replace(
            "la plataforma detrás de", "the platform behind")

        def _fake(key, text, _lang, pack_lang=None):
            return fake_en if key == _NACE_KEY else text

        with mock.patch.object(i18n, "active", return_value="en"), \
             mock.patch.object(translate, "cached_text",
                                side_effect=_fake):
            html = site_news._page_news().decode()
        self.assertIn("Here comes", html)
        self.assertIn('<a href="https://humanrounds.org/"', html)
        self.assertIn("Human Rounds</a>", html)

    def test_cache_frio_degrada_al_espanol(self):
        # Ninguna key está en el cache -> toda la página sigue en español,
        # nunca en blanco ni con un [[N]] a la vista.
        with mock.patch.object(i18n, "active", return_value="en"), \
             mock.patch.object(translate, "cached_text",
                                side_effect=_identity_cached_text):
            html = site_news._page_news().decode()
        self.assertIn("Nace <a href=\"https://humanrounds.org/\"", html)
        self.assertNotIn("[[", html)

    def test_traduccion_con_token_duplicado_degrada_al_espanol(self):
        # Un token repetido detokenizaría en dos </b> (o dos <a>) — el
        # chequeo de conteo/orden lo tiene que agarrar, no sólo la
        # presencia de cada número (code review, 2026-08-04).
        tokenized, _tags = site_news._news_tokenize(_NACE_TEXT)
        broken = tokenized.replace("[[4]]", "[[4]][[4]]", 1)

        def _fake(key, text, _lang, pack_lang=None):
            return broken if key == _NACE_KEY else text

        with mock.patch.object(i18n, "active", return_value="en"), \
             mock.patch.object(translate, "cached_text",
                                side_effect=_fake):
            html = site_news._page_news().decode()
        self.assertIn("Nace <a href=\"https://humanrounds.org/\"", html)
        self.assertNotIn("</b></b>", html)

    def test_news_translation_entries_claves_unicas(self):
        entries = site_news.news_translation_entries()
        total_items = sum(len(items) for _fecha, items in site_news._NEWS)
        self.assertEqual(len(entries), total_items)
        keys = [k for k, _t in entries]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn(_NACE_KEY, keys)


_DEMO_CHAIN = ("global", "example-town")
_REFERENCE_CHAIN = ("global", "ar-b-pinamar")

# Names and looks of the reference deployment that the demo (and any other
# installation) must never publish (demo review 2026-11-15, D3/D5/P10).
_FORBIDDEN_ON_DEMO = (
    "Pinamar", "pinamar.gob.ar", "DIABET", "DiabetPin", "CAPS Móvil",
    "RENAPER", "SUMAR", "1116/26", "profesionales reales", "Fotos reales",
    "contraparte técnica", "Fira Sans", "barra verde", "logo blanco",
    "Vacunas primero", "sin subrayado", "sin caja", "texto chiquito",
    "seis especialidades", "Ostende", "Mi Salud Digital", "bonaerense",
    "Secretaría de Salud",
)


def _render(chain, city, hospital, solo_pedidos=False, brand="Human Rounds"):
    identity = {"city": city, "hospital": hospital}

    def get(key, default=None):
        return identity.get(key, default)

    with mock.patch.object(site_news.data_packs, "active_chain",
                           return_value=chain), \
         mock.patch.object(site_identity, "get", side_effect=get), \
         mock.patch.object(theme, "brand_name", return_value=brand):
        html = site_news._page_news(solo_pedidos).decode()
    return html[html.index('<section class="sec nv-page">'):]


def _demo(solo_pedidos=False):
    return _render(_DEMO_CHAIN, "Sampleton", "Hospital Municipal de Sampleton",
                   solo_pedidos)


def _reference(solo_pedidos=False):
    return _render(_REFERENCE_CHAIN, "Pinamar", "Hospital de Pinamar",
                   solo_pedidos)


def _plain(fragment):
    return re.sub(r"<[^>]+>", "", fragment)


class TestNovedadesAudiencia(unittest.TestCase):
    """Who sees which entry (demo review 2026-11-15)."""

    def test_demo_no_nombra_al_cliente_real(self):
        for solo in (False, True):
            text = _plain(_demo(solo))
            for word in _FORBIDDEN_ON_DEMO:
                self.assertNotIn(word, text, f"{word!r} on /news (pedidos={solo})")

    def test_referencia_muestra_sus_propias_entradas(self):
        ref = _plain(_reference())
        self.assertIn("Turnos de salud pública en Pinamar", ref)
        self.assertIn("Fotos reales", ref)
        self.assertNotIn("Fotos reales", _plain(_demo()))

    def test_ninguna_entrada_del_personal_ni_retirada_en_la_pagina(self):
        hidden = [it for _f, items in site_news._NEWS for it in items
                  if getattr(it, "staff", False) or getattr(it, "retired", False)]
        self.assertGreater(len(hidden), 50)
        pages = [_plain(p) for p in (_demo(), _demo(True), _reference(), _reference(True))]
        for item in hidden:
            snippet = _plain(item)[:60]
            for page in pages:
                self.assertNotIn(snippet, page)

    def test_los_datos_quedan_en_el_archivo_y_en_la_traduccion(self):
        # Hiding is a filter, never a deletion: keys stay positional.
        total = sum(len(items) for _f, items in site_news._NEWS)
        self.assertEqual(len(site_news.news_translation_entries()), total)

    def test_portal_oculta_lo_que_el_portal_apaga(self):
        # An installation where patients and staff sign in through the
        # portal (as the reference deployment does) has no own sign-up, no
        # panel password and, behind the municipal gateway, no assistant
        # connection: /news must not announce them; elsewhere it does.
        sign_up = "tu pedido queda como reclamo pendiente"
        unverified = "no ve datos clínicos"
        photo = "sacándole una foto al DNI"
        assistant = "Asistente de IA ahora son secciones separadas"
        own = {"TURNOS_PORTAL_IDENTITY_MODE": "off",
               "TURNOS_PATIENT_PORTAL_LOGIN": "0",
               "TURNOS_STAFF_PORTAL_LOGIN": "0"}
        gateway = {"TURNOS_PORTAL_IDENTITY_MODE": "private-gateway-v1",
                   "TURNOS_PATIENT_PORTAL_LOGIN": "1",
                   "TURNOS_STAFF_PORTAL_LOGIN": "1"}
        test_portal = dict(gateway, TURNOS_PORTAL_IDENTITY_MODE="test-portal-v1",
                           TURNOS_STAFF_PORTAL_LOGIN="0")
        with _env.patched_env(own):
            pages = [_plain(_demo()), _plain(_reference())]
            off = site_news._portal_off_features()
        self.assertEqual(off, set())
        for page in pages:
            for phrase in (sign_up, unverified, photo, assistant):
                self.assertIn(phrase, page)
        self.assertIn("RENAPER", pages[1])
        with _env.patched_env(gateway):
            pages = [_plain(_demo()), _plain(_reference())]
            off = site_news._portal_off_features()
        self.assertEqual(off, {"own_signup", "assistant_link"})
        marked = [_plain(it)[:60] for _f, items in site_news._NEWS for it in items
                  if it.__class__ is site_news._Item
                  and (it.own_signup or it.assistant_link)]
        self.assertGreaterEqual(len(marked), 12)
        for page in pages:
            for phrase in [sign_up, unverified, photo, assistant] + marked:
                self.assertNotIn(phrase, page)
            # Nothing about our own sign-in survives behind the portal.
            for phrase in ("RENAPER", "contraseña", "Te podés registrar"):
                self.assertNotIn(phrase, page)
        # The test portal keeps the assistant connection open.
        with _env.patched_env(test_portal):
            page = _plain(_reference())
        self.assertNotIn(sign_up, page)
        self.assertIn(assistant, page)

    def test_marcas_de_portal_se_combinan(self):
        item = site_news.D(site_news.R(site_news.P("x")))
        self.assertTrue(item.own_signup and item.reference and item.requested)
        self.assertFalse(site_news._public(item, True, {"own_signup"}))
        self.assertTrue(site_news._public(item, True, {"assistant_link"}))
        self.assertTrue(site_news.M("z").assistant_link)
        self.assertFalse(site_news.S("y").own_signup)
        for flag in site_news._FLAGS:
            self.assertIs(getattr(site_news._Item, flag), False)

    def test_marcas_se_combinan(self):
        item = site_news.R(site_news.S(site_news.P("x")))
        self.assertTrue(item.requested and item.staff and item.reference)
        self.assertFalse(item.retired)
        self.assertEqual(item, "x")


class TestNovedadesForma(unittest.TestCase):
    def test_sin_bajada_que_repite_el_titulo(self):
        html = _demo()
        self.assertNotIn("nv-lede", html)
        self.assertNotIn("Lo que fuimos agregando", html)

    def test_a_pedido_es_una_pestana_con_link_no_una_casilla(self):
        todas = _demo()
        self.assertNotIn("<input", todas)
        self.assertNotIn("location.href", todas)
        self.assertIn('<nav class="subtabs nv-tabs"', todas)
        self.assertIn('<a href="/news" aria-current="page">Todas</a>', todas)
        self.assertIn('<a href="/news/pedidos">A pedido</a>', todas)
        pedidos = _demo(True)
        self.assertIn('<a href="/news/pedidos" aria-current="page">A pedido</a>', pedidos)
        self.assertIn('<a href="/news">Todas</a>', pedidos)

    def test_fecha_con_una_sola_entrada_no_es_lista(self):
        html = _demo()
        one = re.search(r"<h2>11 de noviembre de 2026</h2>(.*?)</section>", html).group(1)
        self.assertTrue(one.startswith("<p>"), one[:40])
        self.assertNotIn("<ul>", one)
        two = re.search(r"<h2>14 de noviembre de 2026</h2>(.*?)</section>", html).group(1)
        self.assertTrue(two.startswith("<ul>"))

    def test_nace_no_dice_detras_de_human_rounds(self):
        text = _plain(_demo())
        self.assertNotIn("detrás de Human Rounds", text)
        self.assertIn("la plataforma detrás de los turnos de salud de Sampleton", text)
        # Another brand keeps its own name.
        other = _plain(_render(_DEMO_CHAIN, "Sampleton", "Hospital X",
                               brand="Turnos Sampleton"))
        self.assertIn("la plataforma detrás de Turnos Sampleton", other)

    def test_meses_viejos_plegados_pagina_corta(self):
        html = _demo()
        months = re.findall(r'<details class="nv-mes"><summary>([^<]+)</summary>', html)
        self.assertIn("Julio de 2026", months)
        self.assertNotIn("<details class=\"nv-mes\" open", html)
        # The newest date is never folded away.
        self.assertLess(html.index("<h2>14 de noviembre de 2026</h2>"),
                        html.index('<details class="nv-mes">'))
        # «A pedido» is short: everything open.
        self.assertNotIn('class="nv-mes"', _demo(True))

    def test_copia_visible_sin_solo_con_tilde_ni_comillas_rectas(self):
        text = _plain(_demo()) + _plain(_reference())
        self.assertNotIn("sólo", text.lower())
        self.assertNotIn('"', text)
        self.assertNotIn(" click", text)
        for item in re.findall(r"<(?:li|p)>(.*?)</(?:li|p)>", _demo()):
            first = _plain(item).lstrip("«")[:1]
            self.assertFalse(first.islower(), item[:60])


if __name__ == "__main__":
    unittest.main()
