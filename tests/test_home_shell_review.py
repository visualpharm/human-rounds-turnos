"""Public shell and home after the demo review of 2026-11-15 (D13, P5)."""
import json
import re
import unittest
from pathlib import Path

from app.core import legacy_urls

ROOT = Path(__file__).resolve().parents[1]


class TestServicesAddress(unittest.TestCase):
    """/services answered 404; it now leads to the studies section, the page
    the header calls «Estudios»."""

    def test_services_redirects_to_the_studies_section(self):
        self.assertEqual(legacy_urls._new_page_path("/services"), "/d/diagnostico")
        self.assertEqual(legacy_urls._new_page_path("/services/"), "/d/diagnostico")

    def test_a_service_page_is_not_caught_by_it(self):
        self.assertIsNone(legacy_urls._new_page_path("/service/x"))
        self.assertIsNone(legacy_urls._new_page_path("/services-extra"))

    def test_the_route_is_mounted_as_a_301(self):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from app.core import legacy_urls_router
        app = FastAPI()
        app.include_router(legacy_urls_router.router)
        r = TestClient(app).get("/services", follow_redirects=False)
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r.headers["location"], "/d/diagnostico")


class TestDemoLedeNamesTheWholeNetwork(unittest.TestCase):
    """The demo's home lede named two centers («del Sampleton Hospital y la
    Riverside Clinic») right above a map of five. It either links every center
    or the list of centers."""

    def test_lede_links_every_center_or_the_centers_page(self):
        pack = ROOT / "packs" / "example-town"
        sitio = json.loads((pack / "sitio.json").read_text(encoding="utf-8"))
        centros = json.loads((pack / "centros-salud.json").read_text(encoding="utf-8"))["centros"]
        hrefs = re.findall(r'href="([^"]+)"', sitio["network_links_html"])
        named = {h.rsplit("/", 1)[1] for h in hrefs if h.startswith("/center/")}
        if "/centers" not in hrefs:
            self.assertEqual(named, {c["slug"] for c in centros})


class TestExampleChipsAreNotSynonyms(unittest.TestCase):
    """«Probá:» showed «Odontología» and «Dentista» side by side: the same
    search twice. A chip whose results the earlier chips already found is
    skipped."""

    def _mod(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "search_examples_under_test", ROOT / "scripts" / "search_examples.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_rows_already_found_make_a_synonym(self):
        mod = self._mod()
        odonto = frozenset({"s:odo-1", "s:odo-2", "d:a"})
        pedia = frozenset({"s:odontopediatria", "s:ped"})
        dentista = frozenset({"s:odo-1", "s:odo-2", "s:odontopediatria"})
        self.assertTrue(mod._is_synonym(dentista, [odonto, pedia]))
        self.assertFalse(mod._is_synonym(frozenset({"s:geri-1", "s:geri-2"}), [odonto, pedia]))
        self.assertFalse(mod._is_synonym(dentista, []))

    def test_the_demo_chips_carry_no_synonym_of_dentistry(self):
        sitio = json.loads((ROOT / "packs" / "example-town" / "sitio.json").read_text(encoding="utf-8"))
        chips = sitio["search_examples"]
        self.assertIn("Odontología", chips)
        self.assertNotIn("Dentista", chips)
        self.assertEqual(len(chips), len(set(chips)))


class TestDemoStripHasNoSecondLanguageSwitch(unittest.TestCase):
    """The demo strip carried its own language select right above the
    header's: two switches for one setting. Where the header shows its switch
    (mode «nav»), the public strip shows none; the admin panel, which has no
    header switch, keeps its own. On a phone the strip is one line: the lead
    and the three links, the detail sentence dropped."""

    def _banner(self, surface, mode="nav"):
        import _env
        from unittest import mock
        from app.core import config
        from app.ops import demo_installation
        with _env.patched_env({"LANGUAGES": "es,en", "LANG_SWITCHER_MODE": mode}, clear=False), \
                mock.patch.object(config, "_db_overrides", return_value={}):
            return demo_installation._banner(surface, "es", switchable=True)

    def test_public_strip_leaves_the_switch_to_the_header(self):
        out = self._banner("public")
        markup = out.split('<script id="demo-installation-layout">')[0]
        self.assertNotIn("<select data-demo-lang-select", markup)
        self.assertNotIn('<nav class="demo-installation__lang"', out)

    def test_admin_strip_keeps_its_own_switch(self):
        self.assertIn("<select data-demo-lang-select", self._banner("admin"))

    def test_public_strip_keeps_a_switch_when_the_header_has_none(self):
        self.assertIn("<select data-demo-lang-select", self._banner("public", mode="secret"))

    def test_phone_strip_is_one_row(self):
        out = self._banner("public")
        self.assertNotIn("grid-template-columns:repeat(3,minmax(0,1fr))", out)
        self.assertIn(".demo-installation__detail{display:none}", out)


class TestCentersPage(unittest.TestCase):
    """P35: /centers opened with a «Centros | Talleres» tab bar above its
    title, its cards were stretched to the tallest one in the row (empty
    bottoms), and each card carried a second link — the address to Maps —
    inside the card's own link. Now: no tabs (the workshops are a heading
    link after the centers), cards at their own height, one link per card
    (the address is dark text; Maps lives on the center's page)."""

    def test_no_tab_bar_on_the_centers_or_workshops_page(self):
        from app.patient_web import site_catalog, site_page_centers
        self.assertNotIn('class="subtabs"', site_page_centers._page_centers().decode("utf-8"))
        cl = site_catalog._cluster_by_id("grupos") or {"id": "grupos", "nombre": "Grupos y talleres"}
        self.assertNotIn('class="subtabs"', site_page_centers._page_workshops(cl).decode("utf-8"))

    def test_workshops_stay_reachable_from_the_centers_page(self):
        from unittest import mock
        from app.patient_web import site_catalog, site_page_centers
        taller = {"id": "t1", "nombre": "Taller", "categoria": "grupos", "centro": "x"}
        with mock.patch.object(site_catalog, "_services", return_value=[taller]):
            html = site_page_centers._page_centers().decode("utf-8")
        self.assertIn('<h2 class="lst-h centers-workshops"><a href="/d/grupos">', html)
        with mock.patch.object(site_catalog, "_services", return_value=[]):
            html = site_page_centers._page_centers().decode("utf-8")
        self.assertNotIn('href="/d/grupos"', html)

    def test_a_center_card_carries_no_maps_link(self):
        from app.patient_web import site_centers
        c = {"slug": "x", "name": "X", "address": "Av. Shaw 255"}
        tile = site_centers._tile_center(c)
        self.assertNotIn("maps_url", tile)
        self.assertEqual(tile["direccion"], "Av. Shaw 255")

    def test_cards_keep_their_own_height_and_dark_text(self):
        tiles = (ROOT / "static" / "platform" / "tiles.mjs").read_text(encoding="utf-8")
        self.assertIn("minmax(300px,1fr));gap:16px;margin-top:14px;align-items:start;}", tiles)
        self.assertIn(".tile-lede{font-size:15px;color:var(--text);", tiles)
        self.assertNotIn("tile--stretch", tiles)
        self.assertNotIn("tile-addr--map", tiles)


class TestFooterIsReadable(unittest.TestCase):
    """The footer read as small grey print: 13px links, and «Funciona con
    Human Rounds» in a 72% grey. Every footer line is now ≥15px in the
    page's ink, on both themes (one block in human-rounds.css)."""

    css = (ROOT / "static" / "root" / "human-rounds.css").read_text(encoding="utf-8")

    def _rule(self, selector_start):
        at = self.css.index(selector_start)
        return self.css[at:self.css.index("}", at)]

    def test_links_and_notices_are_15px_dark(self):
        links = self._rule("body footer.site a,body footer.about-footer a,")
        self.assertIn("font-size:15px", links)
        self.assertIn("color:var(--text)", links)
        notice = self._rule("body footer .fnotice{")
        self.assertIn("font-size:15px", notice)
        self.assertIn("color:var(--text)", notice)
        self.assertNotIn("font-size:13px", self._rule("body footer.site .fmision{"))

    def test_emergency_line_is_not_smaller_than_the_rest(self):
        urg = self._rule("body footer .fnotice.furg{")
        size = int(re.search(r"font-size:(\d+)px", urg).group(1))
        self.assertGreaterEqual(size, 15)

    def test_no_empty_band_under_the_footer_on_a_phone(self):
        # 76px were kept under the last line for the floating chat button; it
        # now steps aside from any content under it, so the band only read
        # as an empty strip (D28, P34).
        at = self.css.index("@media(max-width:600px){body footer.site,")
        phone = self.css[at:self.css.index("}", at)]
        self.assertNotIn("76px", phone)

    def test_guides_are_one_list_at_content_width(self):
        # «Guías y trámites»: six short links stretched over three wide
        # columns (P32); they flow at their own width instead.
        home = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        at = home.index(".info-list{")
        rule = home[at:home.index("}", at)]
        self.assertNotIn("1fr", rule)
        self.assertIn("flex-wrap:wrap", rule)

    def test_platform_credit_is_not_grey(self):
        credit = self._rule("body footer .fcredit,")
        self.assertIn("color:var(--text)", credit)
        self.assertNotIn("foot-ink-2", self.css)


class TestNoSubheadingRepeatsTheTitle(unittest.TestCase):
    """D27, P36: «Cardiología» over «Turnos y atención de cardiología en el
    sistema público de…», «Médicos y profesionales» over «Quiénes atienden…
    Tocá un profesional…», «Hospital y centros de salud» over «El Hospital y
    los CAPS… Tocá un centro…». The title says it; the line under it goes."""

    def test_the_generic_ledes_are_gone(self):
        pages = (ROOT / "app" / "patient_web" / "site_page_doctors.py").read_text(encoding="utf-8") \
            + (ROOT / "app" / "patient_web" / "site_page_centers.py").read_text(encoding="utf-8")
        for key in ("page.cluster.lede", "page.doctors.lede", "page.centers.lede"):
            self.assertNotIn(key, pages)
        for lang in ("es", "en"):
            catalog = json.loads((ROOT / "locales" / lang / "page.json").read_text(encoding="utf-8"))
            for key in ("page.cluster.lede", "page.doctors.lede", "page.centers.lede"):
                self.assertNotIn(key, catalog)


class TestHomeIconsAreFiles(unittest.TestCase):
    """D33: the home's search glass was a hand-drawn inline SVG; it is the
    Icons8 file of the pack, drawn as a mask in the text color."""

    def test_home_has_no_inline_svg_icon(self):
        home = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<svg", home)
        self.assertIn("/static/icons/search-sf-regular.svg", home)
        self.assertTrue((ROOT / "static" / "icons" / "search-sf-regular.svg").is_file())


class TestRoundTheClockGuardiaHasOneSource(unittest.TestCase):
    """P12: the home's «Demanda espontánea» card put the 24 h guardia at the
    hospital while /centers said a CAPS had one too. The one source is the
    walk-in service's own `horario`; any prose (a center's card text or jobs,
    the walk-in cluster's description) that claims a 24 h guardia must name a
    center holding such a service."""

    def _pack(self, name):
        pack = ROOT / "packs" / "example-town"
        return json.loads((pack / name).read_text(encoding="utf-8"))

    def test_the_source_is_the_walkin_service_hours(self):
        from app.patient_web import site_centers
        svs = [{"id": "g", "centro": "hosp", "modo": "walkin", "horario": "Las 24 horas, todos los días"},
               {"id": "v", "centro": "caps", "modo": "walkin", "horario": "Lunes a viernes de 8 a 20"},
               {"id": "t", "centro": "caps", "modo": "turno", "horario": "24 h"}]
        self.assertEqual(site_centers.round_the_clock_centers(svs), {"hosp"})

    def test_a_caps_without_it_is_flagged(self):
        from app.patient_web import site_centers
        svs = [{"id": "g", "centro": "hosp", "modo": "walkin", "horario": "Las 24 horas, todos los días"}]
        centros = [{"slug": "hosp", "jtbd": "Guardia las 24 horas, internación."},
                   {"slug": "caps", "jtbd": "Tu salita. Guardia las 24h, vacunas.",
                    "jobs": ["Atenderte en la Guardia las 24h", "Vacunatorio"]}]
        clusters = [{"id": "sin-turno", "kind": "walkin",
                     "descripcion": "Sin turno: guardia las 24 horas en el hospital."}]
        found = site_centers.round_the_clock_contradictions(centros, svs, clusters)
        self.assertEqual(sorted(found), [("caps", "jobs"), ("caps", "jtbd")])

    def test_the_cluster_text_is_flagged_when_no_center_has_it(self):
        from app.patient_web import site_centers
        clusters = [{"id": "sin-turno", "kind": "walkin", "descripcion": "Guardia 24 hs."}]
        self.assertEqual(site_centers.round_the_clock_contradictions([], [], clusters),
                         [("sin-turno", "descripcion")])

    def test_the_demo_pack_agrees_with_itself(self):
        from app.patient_web import site_centers
        found = site_centers.round_the_clock_contradictions(
            self._pack("centros-salud.json")["centros"],
            self._pack("servicios.json")["servicios"],
            self._pack("clusters.json")["clusters"])
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
