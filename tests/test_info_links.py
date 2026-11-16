"""Guías y trámites: the pack's informational pages, listed on the home page,
and «Guías relacionadas» on the pages that lead to them (sitio.json
"info_links" / "info_related"; SEO 2026-10-16).

Pinamar, 2026-10-15: of 257 sitemap pages Google had indexed only the home
page, and the informational pages (CUD, pensión, vacunación…) had no link
from the home page — Google knew them only from the sitemap ("discovered,
not crawled"). A pack without the keys renders what it rendered before."""

import re
import unittest
from pathlib import Path
from unittest import mock

from app.core import paths, site_identity
from app.patient_web import site_shell

_PACK = {
    "info_links": [
        {"href": "/certificado-unico-de-discapacidad", "label": "Certificado único de discapacidad (CUD)"},
        {"href": "/vacunacion", "label": "Calendario de vacunación"},
        {"href": "/raro", "label": 'A & B <i>"x"</i>'},
        {"href": "https://elsewhere.example/x", "label": "Afuera"},
        {"href": "/con-barra/", "label": "Barra final"},
        {"href": "/sin-nombre", "label": " "},
        "no es un objeto",
    ],
    "info_related": {
        "/d/gineco": ["/vacunacion", "/no-existe"],
        "/vacunacion": ["/vacunacion", "/certificado-unico-de-discapacidad"],
    },
}


def _shell(path):
    return site_shell._shell("t", "", "<p>x</p>", desc="Descripción de prueba", canonical=path).decode("utf-8")


class TestInfoLinks(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(site_identity, "_load", return_value=_PACK)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_only_well_formed_rows_survive(self):
        self.assertEqual([r["href"] for r in site_identity.info_links()],
                         ["/certificado-unico-de-discapacidad", "/vacunacion", "/raro"])

    def test_related_takes_labels_from_info_links_and_skips_unknown_and_self(self):
        self.assertEqual(site_identity.info_related("/d/gineco"),
                         [{"href": "/vacunacion", "label": "Calendario de vacunación"}])
        self.assertEqual([r["href"] for r in site_identity.info_related("/vacunacion")],
                         ["/certificado-unico-de-discapacidad"])
        self.assertEqual(site_identity.info_related("/doctors"), [])

    def test_home_page_lists_them_in_their_own_section(self):
        home = site_identity.render_page("index.html")[0].decode("utf-8")
        block = re.search(r'<section class="info-links".*?</section>', home, re.S).group(0)
        self.assertIn('<h2 id="info-links-title">Guías y trámites</h2>', block)
        # Typography of «Explorar el sitio»: the shared section-heading token, the same size as
        # «Centros de salud»; no underline rule of its own (eval 8).
        self.assertIn(".info-links h2{margin:0 0 var(--home-gap-s);font-size:var(--section-h-size);", home)
        tokens = (Path(paths.root_web_asset("tokens.css"))).read_text(encoding="utf-8")
        self.assertIn("--section-h-size:18px;", tokens)
        self.assertNotIn("text-decoration", home[home.index(".info-links{"):home.index(".campaign-list{")])
        self.assertIn('<a href="/vacunacion">Calendario de vacunación</a>', block)
        self.assertIn("A &amp; B &lt;i&gt;&#34;x&#34;&lt;/i&gt;", block)
        self.assertNotIn("elsewhere.example", home)
        # After «Buen vecino», not inside it.
        self.assertLess(home.index('<section class="citizen"'), home.index('<section class="info-links"'))
        self.assertIn(".info-list{", home)

    def test_guias_relacionadas_only_where_the_pack_names_it(self):
        page = _shell("/d/gineco")
        self.assertIn('<nav class="info-related" aria-label="Guías relacionadas"><h2>Guías relacionadas</h2>'
                      '<ul class="info-list"><li><a href="/vacunacion">Calendario de vacunación</a></li></ul></nav>', page)
        # Its CSS goes in <head>, never inside <body>.
        self.assertLess(page.index(".info-related{"), page.index("</head>"))
        self.assertNotIn('<nav class="info-related"', _shell("/doctors"))
        self.assertNotIn(".info-related{", _shell("/doctors"))


class TestReviewOfPr55(unittest.TestCase):
    """Ilya's review of PR 55, 2026-10-15."""

    def test_malformed_related_entries_are_skipped_and_the_valid_neighbour_kept(self):
        pack = dict(_PACK, info_related={"/d/gineco": [{}, ["/vacunacion"], None, 7, "/vacunacion"]})
        with mock.patch.object(site_identity, "_load", return_value=pack):
            self.assertEqual([r["href"] for r in site_identity.info_related("/d/gineco")], ["/vacunacion"])
            page = _shell("/d/gineco")
        self.assertIn('<a href="/vacunacion">Calendario de vacunación</a>', page)

    def test_labels_come_from_the_translation_cache_with_the_pack_label_as_fallback(self):
        from app.core import translate
        cached = {"sitio.info_links./vacunacion.label": "Vaccination schedule"}
        fake = lambda key, text, lang, pack_lang=None: cached.get(key, text) if lang == "en" else text
        with mock.patch.object(site_identity, "_load", return_value=_PACK), \
             mock.patch.object(translate, "cached_text", side_effect=fake) as spy, \
             mock.patch.object(site_identity.i18n, "active", return_value="en"):
            labels = {r["href"]: r["label"] for r in site_identity.info_links()}
        self.assertEqual(labels["/vacunacion"], "Vaccination schedule")
        self.assertEqual(labels["/certificado-unico-de-discapacidad"], "Certificado único de discapacidad (CUD)")
        self.assertTrue(spy.called)

    def test_the_sweep_sees_every_well_formed_label(self):
        with mock.patch.object(site_identity, "_load", return_value=_PACK):
            keys = dict(site_identity.sitio_entries())
        self.assertEqual(keys["sitio.info_links./vacunacion.label"], "Calendario de vacunación")
        self.assertNotIn("sitio.info_links./con-barra/.label", keys)
        self.assertNotIn("sitio.info_links./sin-nombre.label", keys)

    def test_render_never_calls_the_model(self):
        from app.core import translate
        with mock.patch.object(site_identity, "_load", return_value=_PACK), \
             mock.patch.object(translate, "localized_text", side_effect=AssertionError("model call at render")), \
             mock.patch.object(site_identity.i18n, "active", return_value="en"):
            site_identity.info_links()
            _shell("/d/gineco")

    def test_related_heading_uses_the_navigation_typography(self):
        with mock.patch.object(site_identity, "_load", return_value=_PACK):
            page = _shell("/d/gineco")
        self.assertIn(".info-related h2{margin:0 0 12px;font-size:18px;line-height:1.3;font-weight:650;", page)
        self.assertNotIn("13.5px", page[page.index(".info-related{"):page.index("</style>", page.index(".info-related{"))])


class TestWithoutTheKeys(unittest.TestCase):
    def test_a_pack_without_info_links_renders_no_block_and_no_css(self):
        with mock.patch.object(site_identity, "_load", return_value={}):
            home = site_identity.render_page("index.html")[0].decode("utf-8")
            page = _shell("/d/gineco")
        for html in (home, page):
            self.assertNotIn("info-links", html)
            self.assertNotIn("info-list", html)
            self.assertNotIn("info-related", html)


if __name__ == "__main__":
    unittest.main()
