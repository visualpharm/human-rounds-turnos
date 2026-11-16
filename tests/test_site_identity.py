"""test_site_identity.py — white-label fase 2: sitio.json en el paquete de datos,
site_identity.py renderea la identidad (localidad, hospital, textos de marca) en las
páginas estáticas server-rendered (index.html, about.html, privacy.html,
terms.html) vía placeholders {{sitio.*}}, y en patient_web/site_*.py vía llamadas
directas a site_identity.get().

Sigue el patrón de test_datos_dir.py: el seam es TURNOS_WEBDATA_DIR + limpiar
los caches que cachean por nombre de archivo (catalog._JSON_CACHE,
site_base._CACHE, site_identity._CACHE).
"""

import io
import os
import re
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import site_identity  # noqa: E402
from app.patient_web import site_base, site_catalog, site_page_centers, site_page_doctors
from app.patient_web import catalog  # noqa: E402
from app.core import theme  # noqa: E402

# webdata-ejemplo/ retired 2026-07-30 in favor of packs/example-town/ (same 7
# files, byte-identical) -- see docs/architecture/data-packs.md.
EJEMPLO_DIR = os.path.join(ROOT, "packs", "example-town")


class FakeHandler:
    """Response holder shaped like the old legacy-handler fixture (status,
    headers dict, wfile buffer) — `_get()` below fills it from the renderer
    the NATIVE router calls, so the page assertions below keep their shape
    with no legacy transport involved."""

    def __init__(self, path):
        self.path = path
        self.wfile = io.BytesIO()
        self.status = None
        self.sent_headers = {}


def _get(path):
    """Render one public page exactly the way `core/site_identity_router.
    _render`/`_make_text_endpoint` do — `site_identity.render_page(<the
    _PAGES entry for this path>)` — packaging the (body, content-type)
    result as a FakeHandler. (ok, handler), or (False, None) for a path no
    public page owns (the native router never claims it either)."""
    filename = site_identity._PAGES.get(path)
    if filename is None:
        return False, None
    result = site_identity.render_page(filename)
    if result is None:
        return False, None
    h = FakeHandler(path)
    body, ctype = result
    h.status = 200
    h.sent_headers["Content-Type"] = ctype
    h.wfile.write(body)
    return True, h


class TestDefaultPublicIdentity(unittest.TestCase):
    """An unconfigured public checkout serves fictional example-town only."""

    def setUp(self):
        # Registered before set_env: cleanups run last-in first-out, so the caches are dropped after the environment is back.
        for cache in (site_identity._CACHE, catalog._JSON_CACHE, site_base._CACHE):
            self.addCleanup(cache.clear)
        _env.set_env(self, TURNOS_WEBDATA_DIR=None, TURNOS_PUBLIC_ORIGIN=None)
        site_identity._CACHE.clear()
        catalog._JSON_CACHE.clear()
        site_base._CACHE.clear()
        # Avoid ambient DB configuration while proving the source default.
        self._theme_cache_bak = dict(theme._cache)
        theme._cache["v"] = "humanrounds"
        theme._cache["t"] = time.time()

    def tearDown(self):
        theme._cache.clear()
        theme._cache.update(self._theme_cache_bak)

    def test_get_basico(self):
        self.assertEqual(site_identity.get("city"), "Sampleton")
        self.assertEqual(site_identity.get("hospital"), "Sampleton Hospital")
        self.assertEqual(site_identity.get("main_center"), "sampleton-hospital")
        self.assertEqual(site_identity.get("emergency_number"), "112")

    def test_get_clave_inexistente_cae_al_default_neutro(self):
        self.assertEqual(site_identity.get("clave_que_no_existe"), "")
        self.assertEqual(site_identity.get("clave_que_no_existe", "default explícito"), "default explícito")

    def test_render_reemplaza_placeholders(self):
        out = site_identity.render("Hola {{sitio.city}} y {{sitio.hospital}}")
        self.assertEqual(out, "Hola Sampleton y Sampleton Hospital")

    def test_render_escapa_el_valor_no_el_texto_alrededor(self):
        # el placeholder puede vivir dentro de marcado con "&"/"<" propios del
        # HTML estático (no deben tocarse); sólo el VALOR sustituido se escapa.
        orig_load = site_identity._load
        try:
            site_identity._load = lambda: {"campo": "A & B <script>"}
            out = site_identity.render("<p>Tom &amp; Jerry: {{sitio.campo}}</p>")
            self.assertEqual(out, "<p>Tom &amp; Jerry: A &amp; B &lt;script&gt;</p>")
        finally:
            site_identity._load = orig_load

    def test_render_sin_placeholders_no_toca_nada(self):
        self.assertEqual(site_identity.render("<p>texto plano</p>"), "<p>texto plano</p>")

    def test_home_uses_only_public_fictional_identity(self):
        ok, h = _get("/")
        self.assertTrue(ok)
        self.assertEqual(h.status, 200)
        body = h.wfile.getvalue().decode("utf-8")
        self.assertIn("Sampleton", body)
        self.assertIn("Human Rounds", body)
        self.assertIn('<meta name="description"', body)
        self.assertIn('<link rel="canonical" href="http://127.0.0.1:7700/">', body)
        self.assertIn('<meta property="og:title"', body)
        self.assertNotIn("Pinamar", body)
        self.assertNotIn("{{sitio.", body)
        self.assertEqual(h.sent_headers.get("Content-Type"), "text/html; charset=utf-8")

    def test_index_html_alias_sirve_lo_mismo_que_home(self):
        ok1, h1 = _get("/")
        ok2, h2 = _get("/index.html")
        self.assertTrue(ok1 and ok2)
        self.assertEqual(h1.wfile.getvalue(), h2.wfile.getvalue())

    def test_about_uses_only_public_fictional_identity(self):
        ok, h = _get("/about.html")
        self.assertTrue(ok)
        body = h.wfile.getvalue().decode("utf-8")
        self.assertIn("Sampleton", body)
        self.assertIn("Human Rounds", body)
        self.assertIn('<span class="about-hero__intro">Acerca de</span>', body)
        self.assertIn('<span class="about-hero__name">Sampleton Turnos</span>', body)
        self.assertNotIn("Pinamar", body)
        self.assertNotIn("Se integra con HSI", body)
        self.assertNotIn("Es un prototipo", body)
        self.assertNotIn("Urgencias: llamá", body)
        self.assertNotIn("Ver los servicios", body)
        self.assertNotIn("{{sitio.", body)

    def test_privacidad_y_terminos_sin_placeholders_sueltos(self):
        ok1, h1 = _get("/privacy.html")
        ok2, h2 = _get("/terms.html")
        self.assertTrue(ok1 and ok2)
        b1 = h1.wfile.getvalue().decode("utf-8")
        b2 = h2.wfile.getvalue().decode("utf-8")
        self.assertIn("Sampleton Hospital y las clínicas", b1)
        self.assertIn("Sampleton", b2)
        self.assertNotIn("Pinamar", b1)
        self.assertNotIn("Pinamar", b2)
        self.assertNotIn("{{sitio.", b1)
        self.assertNotIn("{{sitio.", b2)

    def test_llms_txt_sirve_contexto_publico(self):
        ok, h = _get("/llms.txt")
        self.assertTrue(ok)
        self.assertEqual(h.status, 200)
        self.assertEqual(h.sent_headers.get("Content-Type"), "text/plain; charset=utf-8")
        body = h.wfile.getvalue().decode("utf-8")
        self.assertIn("# Human Rounds", body)
        self.assertIn("Sampleton", body)
        self.assertIn("http://127.0.0.1:7700/doctors", body)
        self.assertNotIn("Pinamar", body)
        self.assertNotIn("{{sitio.", body)

    def test_ruta_ajena_no_matchea(self):
        ok, _ = _get("/no-existe.html")
        self.assertFalse(ok)

    def test_home_and_dynamic_pages_render_the_same_footer(self):
        home = site_identity.render_page("index.html")[0].decode("utf-8")
        doctors = site_page_doctors._page_doctors().decode("utf-8")
        footer = re.compile(r'<footer class="site">.*?</footer>', re.S)
        self.assertEqual(footer.search(home).group(0), footer.search(doctors).group(0))

    def test_hospital_py_usa_sitio_get_no_placeholders(self):
        html = site_page_doctors._page_doctors().decode()
        self.assertIn("Sampleton", html)
        self.assertNotIn("Pinamar", html)
        self.assertNotIn("{{sitio.", html)


class TestEnlacesDeCentroCanonicos(unittest.TestCase):
    """La home imprime /center/<slug>: un pack que aún trae /centro/<slug> no
    se toca, se normaliza al renderizar (el viejo sólo hace 301)."""

    def setUp(self):
        site_identity._CACHE.clear()

    def tearDown(self):
        site_identity._CACHE.clear()

    def _with_links(self, html):
        orig = site_identity._load
        site_identity._load = lambda: {"network_links_html": html}
        self.addCleanup(setattr, site_identity, "_load", orig)

    def test_centro_viejo_se_imprime_como_center(self):
        self._with_links('del <a class="stz-link" href="/centro/uno">Uno</a> y '
                         '<a class="stz-link" href=\'/centro/dos\'>Dos</a>')
        out = site_identity.get("network_links_html")
        self.assertIn('href="/center/uno"', out)
        self.assertIn("href='/center/dos'", out)
        self.assertNotIn("/centro/", out)

    def test_center_y_otros_enlaces_quedan_igual(self):
        html = ('<a href="/center/uno">Uno</a> <a href="/centros-x">X</a> '
                '<a href="/doctor/centro/y">Y</a> centro/ en texto')
        self._with_links(html)
        self.assertEqual(site_identity.get("network_links_html"), html)

    def test_home_renderizada_no_enlaza_centro_viejo(self):
        self._with_links('del <a class="stz-link" href="/centro/uno">Uno</a>')
        body, _ = site_identity.render_page("index.html")
        body = body.decode("utf-8")
        self.assertIn('href="/center/uno"', body)
        self.assertNotIn('href="/centro/', body)

    def test_ruta_vieja_sigue_redirigiendo(self):
        from app.core import legacy_urls
        self.assertEqual(legacy_urls._new_page_path("/centro/uno"), "/center/uno")


class TestHomeFoot(unittest.TestCase):
    """The foot of the home. The folded «Todas las especialidades» block that
    sat above the footer is gone (demo review 2026-11-15: it repeated the
    specialties grid above it); the /d/<id> pages stay reachable from that
    grid and from /sitemap.xml. The footer keeps its 4 links."""

    def setUp(self):
        # Registered before set_env: cleanups run last-in first-out, so the caches are dropped after the environment is back.
        for cache in (site_identity._CACHE, catalog._JSON_CACHE, site_base._CACHE):
            self.addCleanup(cache.clear)
        _env.set_env(self, TURNOS_WEBDATA_DIR=EJEMPLO_DIR)
        site_identity._CACHE.clear()
        catalog._JSON_CACHE.clear()
        site_base._CACHE.clear()
        self._theme_cache_bak = dict(theme._cache)
        theme._cache["v"] = "humanrounds"
        theme._cache["t"] = time.time()

    def tearDown(self):
        theme._cache.clear()
        theme._cache.update(self._theme_cache_bak)

    def _home(self):
        body, _ = site_identity.render_page("index.html")
        return body.decode("utf-8")

    def test_sin_bloque_de_especialidades_plegadas(self):
        html = self._home()
        self.assertNotIn('class="browse"', html)
        self.assertNotIn(".browse", html)
        self.assertNotIn("Todas las especialidades", html)

    def test_las_secciones_siguen_en_el_sitemap(self):
        from app.patient_web import sitemap
        with open(sitemap.__file__, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('"/d/diagnostico"', src)
        self.assertIn("con_contenido", src)

    def test_el_pie_sigue_con_cuatro_enlaces(self):
        html = self._home()
        footer = re.search(r'<footer class="site">.*?</footer>', html, re.S).group(0)
        # The notice lines above the links (Urgencias: <tel:107>, 2026-10-22)
        # are prose, not footer links.
        links = re.sub(r'<p class="fnotice[^"]*">.*?</p>', "", footer, flags=re.S)
        self.assertEqual(len(re.findall(r"<a ", links)), 4)
        # El pie es UN componente compartido: la home no le agrega sangrado ni padding propio.
        self.assertNotIn("footer.site{border-top", html)
        self.assertNotIn("-16px 0;padding:var(--home-gap-m) 16px", html)

    def test_credito_de_la_plataforma_solo_fuera_de_human_rounds(self):
        """«Funciona con Human Rounds» (responsable del producto, 2026-10-17): en una instalación
        con otra marca (Pinamar) el pie lo enlaza; en la demo, con la marca
        Human Rounds, sería redundante y no sale."""
        footer = lambda h: re.search(r'<footer class="site">.*?</footer>', h, re.S).group(0)
        self.assertNotIn("fcredit", footer(self._home()))
        theme._cache["v"] = "pinamar"
        theme._cache["t"] = time.time()
        site_base._CACHE.clear()
        site_identity._CACHE.clear()
        f = footer(self._home())
        self.assertIn('<a class="fcredit" href="https://humanrounds.org/?lang=es">Funciona con Human Rounds</a>', f)


class TestSitioOjoManso(unittest.TestCase):
    """Con TURNOS_WEBDATA_DIR=packs/example-town: sirve Sampleton, sin
    absolutamente nada de Pinamar — ni en las páginas estáticas ni en las
    páginas server-rendered de patient_web/site_*.py."""

    def setUp(self):
        # Registered before set_env: cleanups run last-in first-out, so the caches are dropped after the environment is back.
        for cache in (site_identity._CACHE, catalog._JSON_CACHE, site_base._CACHE):
            self.addCleanup(cache.clear)
        _env.set_env(self, TURNOS_WEBDATA_DIR=EJEMPLO_DIR)
        site_identity._CACHE.clear()
        catalog._JSON_CACHE.clear()
        site_base._CACHE.clear()
        self._merge_bak = catalog.merge_with_overlay
        catalog.merge_with_overlay = lambda tipo, base: (list(base), {})
        # Sampleton (packs/example-town) only ships paired with the
        # generic/demo theme in every real deployment ("pinamar" is the
        # MUNICIPALITY's own theme id + catalog, an impossible pairing with
        # this fictional pack). theme.brand_name() reads the active theme, so
        # pin it here — otherwise theme.current()'s default ("pinamar")
        # legitimately renders the word "Pinamar" and these "sin Pinamar"
        # assertions would be testing a combination that can't happen.
        self._theme_cache_bak = dict(theme._cache)
        theme._cache["v"] = "humanrounds"
        theme._cache["t"] = time.time()

    def tearDown(self):
        catalog.merge_with_overlay = self._merge_bak
        theme._cache.clear()
        theme._cache.update(self._theme_cache_bak)

    def test_get_basico(self):
        self.assertEqual(site_identity.get("city"), "Sampleton")
        self.assertEqual(site_identity.get("hospital"), "Sampleton Hospital")
        self.assertEqual(site_identity.get("main_center"), "sampleton-hospital")

    def test_home_sirve_ojo_manso_sin_pinamar(self):
        ok, h = _get("/")
        self.assertTrue(ok)
        body = h.wfile.getvalue().decode("utf-8")
        self.assertIn("Sampleton", body)
        self.assertNotIn("Pinamar", body)
        self.assertNotIn("{{sitio.", body)

    def test_acerca_sirve_ojo_manso_sin_pinamar(self):
        ok, h = _get("/about.html")
        self.assertTrue(ok)
        body = h.wfile.getvalue().decode("utf-8")
        self.assertIn("Sampleton", body)
        self.assertNotIn("Pinamar", body)
        self.assertNotIn("{{sitio.", body)

    def test_hospital_py_paginas_sin_pinamar(self):
        html_medicos = site_page_doctors._page_doctors().decode()
        self.assertIn("Sampleton", html_medicos)
        self.assertNotIn("Pinamar", html_medicos)

        html_centros = site_page_centers._page_centers().decode()
        self.assertIn("Sampleton", html_centros)
        self.assertNotIn("Pinamar", html_centros)

    def test_footer_de_hospital_py_dice_ojo_manso(self):
        # 2026-09-14: The mission/tagline line ("Hecha y sostenida por vecinos
        # de…") has been removed from the footer per the product owner's zero-small-print rule.
        # This test now verifies that the site identity (city name) is correctly
        # substituted in the remaining footer links ("Acerca de..." → "Acerca de
        # Sampleton") and that the removed tagline is absent.
        html = site_page_doctors._page_doctors().decode()
        self.assertIn("Sampleton", html)
        self.assertNotIn("Hecha y sostenida por vecinos de Sampleton.", html)
        self.assertNotIn("Pinamar", html)


class TestSitioSinArchivo(unittest.TestCase):
    """sitio.json falta por completo (paquete de datos que no lo trae, o un
    directorio vacío): el motor arranca igual, con defaults neutros, sin
    crashear y sin dejar placeholders sin resolver."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix="sana-sin-sitio-")
        # Copiamos lo mínimo de packs/example-town salvo sitio.json, para que
        # site_catalog._services()/_centers() sigan andando sin romperse.
        import shutil
        for name in os.listdir(EJEMPLO_DIR):
            if name == "sitio.json":
                continue
            src = os.path.join(EJEMPLO_DIR, name)
            if not os.path.isfile(src):
                continue  # asset subdirs (fotos-medicos/, ordenes-demo/) — not flat pack files
            shutil.copy(src, os.path.join(self._tmp, name))
        # Registered before set_env: cleanups run last-in first-out, so the caches are dropped after the environment is back.
        for cache in (site_identity._CACHE, catalog._JSON_CACHE, site_base._CACHE):
            self.addCleanup(cache.clear)
        _env.set_env(self, TURNOS_WEBDATA_DIR=self._tmp)
        site_identity._CACHE.clear()
        catalog._JSON_CACHE.clear()
        site_base._CACHE.clear()
        self._merge_bak = catalog.merge_with_overlay
        catalog.merge_with_overlay = lambda tipo, base: (list(base), {})

    def tearDown(self):
        import shutil
        catalog.merge_with_overlay = self._merge_bak
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_get_devuelve_defaults_neutros(self):
        self.assertEqual(site_identity.get("city"), "la localidad")
        self.assertEqual(site_identity.get("hospital"), "el hospital")
        self.assertEqual(site_identity.get("emergency_number"), "112")

    def test_home_bootea_sin_crash_ni_placeholders_sueltos(self):
        ok, h = _get("/")
        self.assertTrue(ok)
        self.assertEqual(h.status, 200)
        body = h.wfile.getvalue().decode("utf-8")
        self.assertIn("la localidad", body)
        self.assertNotIn("{{sitio.", body)

    def test_hospital_py_no_crashea(self):
        html = site_page_doctors._page_doctors().decode()
        self.assertNotIn("{{sitio.", html)

    def test_base_url_pineado_gana_al_host(self):
        # Client Host input never selects absolute-link destinations.
        class H:
            headers = {"Host": "evil.example.com"}
            client_address = ("1.2.3.4", 0)
        with _env.patched_env(TURNOS_PUBLIC_ORIGIN="https://sana-turnos.brieflysites.com/"):
            self.assertEqual(site_identity.base_url(H()), "https://sana-turnos.brieflysites.com")
        # A fresh checkout shares SEO's safe loopback development origin.
        self.assertEqual(site_identity.base_url(H()), "http://127.0.0.1:7700")
        self.assertEqual(site_identity.base_url(None), "http://127.0.0.1:7700")


class TestOverridesDeSettings(unittest.TestCase):
    """Fase 4 (2026-07-30): un subconjunto curado de claves top-level de
    sitio.json es editable desde Settings sin tocar el paquete de datos —
    config.get("SITE_ID_<CLAVE>") gana si está seteado."""

    def setUp(self):
        site_identity._CACHE.clear()

    def tearDown(self):
        site_identity._CACHE.clear()

    def test_override_gana_al_paquete(self):
        from unittest.mock import patch
        with patch("app.core.config.get", return_value="Hospital de Otra Ciudad"):
            self.assertEqual(site_identity.get("hospital"), "Hospital de Otra Ciudad")

    def test_sin_override_usa_el_paquete(self):
        from unittest.mock import patch
        with patch("app.core.config.get", return_value=None):
            valor = site_identity.get("hospital")
        self.assertNotEqual(valor, "")  # sigue resolviendo del sitio.json/defaults

    def test_clave_no_overridable_ignora_config(self):
        from app.core import config
        from unittest.mock import patch
        real_get = config.get
        vistas = []

        def spy(key, *a, **k):
            vistas.append(key)
            return real_get(key, *a, **k)

        with patch("app.core.config.get", side_effect=spy):
            site_identity.get("hero.titulo")
        self.assertFalse(any(k.startswith("SITE_ID_") for k in vistas),
                         "no debería consultar SITE_ID_* para una clave no overridable")

    def test_responsable_de_los_datos_por_instalacion(self):
        # Dos sitios pueden compartir el paquete: el responsable de los datos
        # (que elige la versión institucional de los textos legales) lo pone
        # el entorno de cada instalación, no el sitio.json.
        from unittest.mock import patch
        with patch("app.core.config.get", side_effect=lambda k, *a, **kw:
                   "Municipalidad de Pinamar" if k == "SITE_ID_DATA_CONTROLLER" else None):
            self.assertEqual(site_identity.get("data_controller"), "Municipalidad de Pinamar")
        with patch("app.core.config.get", return_value=None):
            self.assertFalse(site_identity.get("data_controller"))


if __name__ == "__main__":
    unittest.main()
