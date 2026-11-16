"""Regression guards for the 2026-07-23 public mobile annotation batch."""

from pathlib import Path
import unittest
from unittest import mock

import _env
from app.ai import ai_client
from app.patient_web import site_listing, site_page_doctors, site_shell


ROOT = Path(__file__).resolve().parents[1]


def source(name):
    return (ROOT / name).read_text(encoding="utf-8")


class TestMobilePublicUi(unittest.TestCase):
    def test_home_search_examples_and_directory_counts_are_compact(self):
        home = source("templates/index.html")
        directory = source("static/platform/directory.mjs")
        # i18n (fase 3b): el literal se movió a locales/es.json (home.search.placeholder)
        self.assertIn("placeholder=\"{{ t('home.search.placeholder'", home)
        # Demo review 2026-11-15: the examples are dark, 16px and WRAP — the
        # same ones on a phone and a desktop, none hidden, none cut.
        self.assertIn(".ejemplos{margin:0 2px var(--home-gap-l);font-size:16px;line-height:1.4;color:var(--text);", home)
        self.assertIn("flex-wrap:wrap", home)
        self.assertNotIn(".ejemplos a:nth-of-type", home)
        self.assertNotIn("overflow:hidden;}\n  .ejemplos a", home)
        self.assertNotIn("cl.n_servicios +", directory)
        self.assertIn('class="dr-label"', directory)
        # Demo review 2026-11-15: no bare number on a row, no rotating colours.
        self.assertNotIn(".dr-n{", directory)
        self.assertNotIn("CHIP_BG", directory)
        # directory.mjs is fingerprinted and imported into a local binding.
        self.assertIn("import(\"{{ static('platform/directory.mjs') }}\")", home)
        self.assertNotIn("SanaDirectory", home)
        self.assertNotIn("/directory.mjs?v=", home)

    def test_icons8_credit_is_part_of_the_footer_links(self):
        # 2026-09-14: Footer now contains ONLY the four real links (Privacy,
        # Terms, About, What's new). The mission/tagline sentence, the
        # "Funciona con el software Human Rounds" line, and the Icons8 credit
        # are removed per the product owner's zero-small-print rule.
        home = source("templates/index.html")
        self.assertIn('{% include "_site_footer.html" -%}', home)
        self.assertNotIn("fcredit", home)
        footer = source("templates/_site_footer.html")
        # Assert the four real footer links are present
        self.assertIn("footer.privacy", footer)
        self.assertIn("footer.terms", footer)
        self.assertIn("footer.about", footer)
        self.assertIn("footer.news", footer)
        # Assert the credit line is NOT in the footer source
        self.assertNotIn("footer.credit", footer)
        self.assertNotIn("Íconos por Icons8", footer)
        rendered = site_page_doctors._page_doctors().decode()
        # Assert the four real links are rendered
        self.assertIn('href="/privacy.html"', rendered)
        self.assertIn('href="/terms.html"', rendered)
        self.assertIn('href="/about.html"', rendered)
        self.assertIn('href="/news"', rendered)
        # Assert the Icons8 credit is NOT rendered
        self.assertNotIn(">Íconos por Icons8</a>", rendered)

    def test_directory_uses_air_between_fields_and_commas_within_location(self):
        listing = source("static/platform/listing.mjs")
        # Preserve space between fields without forcing a long center into
        # an indivisible inline-block. Real ES/EN viewport and chat-container
        # bounds are enforced by listing-long-name-fit.spec.mjs.
        self.assertIn("td.lst-td-sub{margin-top:0;margin-left:10px;white-space:normal;}", listing)
        self.assertIn(".lst-linkcell{display:inline;}", listing)
        self.assertNotIn("td.lst-td-sub::before", listing)
        row = site_listing._service_center_row({
            "id": "medicina-general-centro-cultural-norte",
            "nombre": "Medicina general",
            "centro": "riverside-clinic",
            "sede": "Centro Cultural",
            "modo": "walkin",
            "horario": "",
        })
        self.assertEqual(row["centro"], "Riverside Clinic, Centro Cultural")

    def test_mobile_nav_features_order_upload_and_account_menu(self):
        nav = source("static/platform/nav.mjs")
        # En mobile NADA queda fuera del burger. "Subí tu orden" vive en el
        # menú y el chat conserva su lanzador flotante propio.
        self.assertNotIn("stznav-featured", nav)
        self.assertIn("ICO_ORDEN", nav)
        self.assertIn("ICO_LOGOUT", nav)
        # Las etiquetas salen del catálogo compartido (2026-08-04, reemplaza
        # la tabla es/en que vivía dentro de nav.js): T(clave, vars, castellano).
        self.assertIn('label: T("nav.upload_order", null, "Subí tu orden"), href: "/upload-order"', nav)
        self.assertIn('label: T("nav.account.details", null, "Datos personales")', nav)
        self.assertIn('label: T("nav.account.mcp", null, "Asistente de IA (MCP)")', nav)
        self.assertIn("stznav-account-logout", nav)
        self.assertIn("stznav-account-section", nav)
        self.assertIn('function fetchAuthMode(signal)', nav)
        self.assertIn('"/auth/sso?provider=renaper&next=%2Faccount"', nav)
        self.assertIn('color:var(--nav-ink,var(--text));text-decoration:none;font-size:var(--nav-size,15px)', nav)
        self.assertNotIn('color:var(--accent-hover);text-decoration:none;font-size:15px', nav)

    def test_mobile_account_hides_profile_rail_and_quiets_signout(self):
        account = source("templates/account.html")
        # On a phone the rail becomes one strip of every section (hr-patient
        # review #1, 2026-11-15): the profile block hides, the sections stay.
        self.assertNotIn(".rail{display:none;}", account)
        self.assertIn(".rail-id{display:none;}", account)
        self.assertIn(".rail-nav{margin-top:0;flex-direction:row;", account)
        self.assertIn(".rail-out{width:auto;border:0;background:none", account)
        self.assertIn("static('platform/account.mjs')", account)
        self.assertNotIn("window.__sanaAccountHashHandler", account)

    @staticmethod
    def _focus_page():
        return site_shell._shell(
            "Elegí un horario",
            "medicos",
            "<main>Turnos</main>",
            desc="Horarios de atención.",
            chrome="focus",
            index=False,
        ).decode("utf-8")

    def test_focus_wizards_keep_the_persistent_chat_sidebar(self):
        # The launcher exists only when there is an AI to talk to (decision
        # 2026-10-28 «no AI key means no chat entrance», docs/decisions/
        # 2026-10-w5e.md): this case is an installation WITH the AI, pinned
        # here instead of inherited from whatever the runner's environment has.
        _env.set_env(self, TURNOS_FAKE_AI="1", TURNOS_AI_DISABLED=None)
        page = self._focus_page()
        from app.core import static_fingerprint
        self.assertIn(static_fingerprint.url("platform/dates.mjs"), page)
        self.assertIn(static_fingerprint.url("platform/chat-widget.mjs"), page)
        self.assertIn('id="stz-chat-launcher"', page)
        self.assertNotIn("chat-widget.js", page)
        self.assertIn('class="wrap wrap--focus"', page)

    def test_focus_wizards_without_an_ai_key_paint_no_chat_launcher(self):
        # Same decision, the other side: salud has no key, so its wizard shows
        # no chat button that would lead to «El asistente no está disponible».
        _env.set_env(self, TURNOS_FAKE_AI=None, TURNOS_AI_DISABLED=None)
        with mock.patch.object(ai_client, "available", return_value=False):
            page = self._focus_page()
        self.assertNotIn("stz-chat-launcher", page)
        self.assertNotIn("chat-boot-error", page)
        self.assertIn('class="wrap wrap--focus"', page)


if __name__ == "__main__":
    unittest.main()
