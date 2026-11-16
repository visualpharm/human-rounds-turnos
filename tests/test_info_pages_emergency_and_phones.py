"""Informational pages — one emergency number, one phone formatter (demo
review 2026-11-15: D1, D2, D10, D11, D16, D28, D32).

The demo showed 112 in the footer and 107 on /telemedicine; the blood page
printed «+800 400 0001» and dialled 01140000001 while the hospital's own page
said «+54 11 4000-0001»; telemedicine pointed its button at a placeholder
domain and put «No es para emergencias» below the first phone screen. These
tests pin the fixes against the repository's own packs (example-town is the
demo) and against a synthetic installation with a different number.
"""

import json
import os
import re
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import format  # noqa: E402
from app.core import site_identity  # noqa: E402
from app.patient_web import site_catalog, site_centers, site_news, site_shell  # noqa: E402

PACKS = os.path.join(ROOT, "packs")
_LITERAL = re.compile(r"(?<![\d\w.+-])(107|112|911)(?![\d\w-])")


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)
    elif isinstance(value, dict):
        for k, v in value.items():
            if k not in ("palabras", "palabras_excluidas", "match", "id"):
                yield from _strings(v)


def _load(*parts):
    with open(os.path.join(PACKS, *parts), encoding="utf-8") as f:
        return json.load(f)


_REAL_GET = site_identity.get


def _identity(**over):
    def get(key, *a, **k):
        if key in over:
            return over[key]
        if key == "emergency_number_link_html" and "emergency_number" in over:
            n = over["emergency_number"]
            return f'<a class="stz-link" href="tel:{n}">{n}</a>'
        return _REAL_GET(key, *a, **k)
    return get


class TestNoHardCodedEmergencyNumber(unittest.TestCase):
    """Page prose in the engine's packs names the number through
    `{{sitio.emergency_number}}`, never as a literal: an installation's
    number lives in ONE place (sitio.json / Settings)."""

    def test_page_texts_carry_the_placeholder_not_a_literal(self):
        files = [
            ("example-town", "enlaces.json"),
            ("example-town", "servicios.json"),
            ("global", "estudios-preparacion.json"),
        ]
        hits = []
        for parts in files:
            for s in _strings(_load(*parts)):
                if _LITERAL.search(s):
                    hits.append(f"{'/'.join(parts)}: {s[:90]}")
        self.assertEqual(hits, [])

    def test_the_guardia_cartel_says_the_installation_number(self):
        with mock.patch.object(site_identity, "get", side_effect=_identity(emergency_number="999")):
            cartel = site_identity.expand(
                "Ante una emergencia llamá al {{sitio.emergency_number}}.")
        self.assertEqual(cartel, "Ante una emergencia llamá al 999.")

    def test_expand_keeps_unchanged_values_and_drops_html_keys(self):
        row = {"a": "sin número", "b": ["x"]}
        self.assertIs(site_identity.expand(row), row)
        self.assertEqual(site_identity.expand("{{sitio.emergency_number_link_html}}"), "")

    def test_localized_services_resolve_the_placeholder(self):
        row = {"id": "guardia", "cartel": "Llamá al {{sitio.emergency_number}}."}
        with mock.patch.object(site_catalog, "_services_rows", lambda localize=True: [dict(row)]), \
                mock.patch.object(site_identity, "get", side_effect=_identity(emergency_number="999")):
            self.assertEqual(site_catalog._services()[0]["cartel"], "Llamá al 999.")
            self.assertEqual(site_catalog._services(localize=False)[0]["cartel"],
                             "Llamá al {{sitio.emergency_number}}.")


class TestTelemedicine(unittest.TestCase):
    def test_the_page_shows_the_footer_number_before_any_action(self):
        with mock.patch.object(site_identity, "get", side_effect=_identity(emergency_number="999")):
            footer = site_shell._site_footer()
            html = site_news._page_telemedicine().decode()
        self.assertIn('href="tel:999"', footer)
        main = html[html.index('<div class="campaign-page">'):]
        self.assertIn('class="info-alert"', main)
        self.assertIn('<a class="stz-link" href="tel:999">999</a>', main)
        self.assertNotIn("107", main)
        notice = main.index("info-alert")
        self.assertLess(main.index('class="lede"'), notice)
        self.assertLess(notice, main.index("Para qué sirve"))
        if "stz-cta" in main:
            self.assertLess(notice, main.index("stz-cta"))

    def test_the_demo_has_no_dead_button_and_one_name(self):
        html = site_news._page_telemedicine().decode()
        main = html[html.index('<div class="campaign-page">'):]
        self.assertNotIn(".example", main)
        self.assertNotIn("stz-cta", main)
        self.assertNotIn("campaign-action-note", main)
        self.assertNotIn("Cómo funciona", main)
        self.assertNotIn("class=\"gate\"", main)
        self.assertEqual(main.lower().count("videollamada"), 1)
        self.assertIn("No tiene costo.", main)

    def test_a_placeholder_target_draws_no_button(self):
        self.assertEqual(site_news._real_target("https://sampleton.example/tele/"), "")
        self.assertEqual(site_news._real_target("http://portal.org/"), "")
        self.assertEqual(site_news._real_target("https://pinamar.itconsultsa.com/"),
                         "https://pinamar.itconsultsa.com/")

    def test_the_demo_search_row_does_not_lead_to_a_placeholder(self):
        tele = next(e for e in _load("example-town", "enlaces.json")["enlaces"] if e["id"] == "telemedicina")
        self.assertNotIn(".example", json.dumps(tele))
        self.assertEqual(tele["accion_texto"], "Consultá por telemedicina")


class TestBloodDonation(unittest.TestCase):
    def test_the_phone_dials_exactly_what_it_shows_like_the_center_page(self):
        html = site_news._page_blood_donation().decode()
        hospital = site_centers._centers_by_slug()["sampleton-hospital"]
        shown = format.tel_text(hospital["phone"])
        self.assertEqual(shown, "+54 11 4000-0001")
        self.assertIn(f'href="tel:{format.tel_href(hospital["phone"])}"', html)
        self.assertEqual(format.tel_href(hospital["phone"]), "+541140000001")
        self.assertIn(shown, html)
        self.assertNotIn("800 400", html)
        self.assertNotIn("tel:0114", html)

    def test_the_page_says_where_and_when(self):
        html = site_news._page_blood_donation().decode()
        self.assertIn("Dónde y cuándo donar", html)
        self.assertIn("Sampleton Hospital", html)
        self.assertIn("Main Rd. 123", html)
        self.assertIn("De lunes a viernes", html)
        self.assertNotIn('class="note"', html)

    def test_without_hours_the_next_step_is_plain_text(self):
        links = [{"id": "donar-sangre", "pagina": {"lede": "L"}}]
        with mock.patch.object(site_centers, "_links", lambda localize=True: links):
            html = site_news._page_blood_donation().decode()
        self.assertNotIn('class="note"', html)
        self.assertIn("<p>Consultá en el hospital por el horario de donación.</p>", html)
        self.assertIn('class="stz-cta" href="tel:+541140000001"', html)  # main center

    def test_the_packs_own_button_stays_and_its_whatsapp_wins(self):
        # Pinamar: the Hemoterapia line is the pack's own wa.me button
        links = [{"id": "donar-sangre", "pagina": {"lede": "L", "cta_href": "https://wa.me/5492254602222",
                                                   "cta_texto": "WhatsApp · 2254 60-2222"}}]
        with mock.patch.object(site_centers, "_links", lambda localize=True: links):
            html = site_news._page_blood_donation().decode()
        self.assertIn('href="https://wa.me/5492254602222"', html)
        self.assertIn('class="stz-cta" href="tel:', html)
        # a sign-up form for a campaign day stays next to the call button
        links[0]["pagina"] = {"lede": "L", "cta_href": "https://forms.org/x", "cta_texto": "Anotate"}
        with mock.patch.object(site_centers, "_links", lambda localize=True: links):
            html = site_news._page_blood_donation().decode()
        self.assertIn('<a class="stz-link" href="https://forms.org/x">Anotate</a>', html)

    def test_the_call_button_shows_the_one_number_it_dials(self):
        links = [{"id": "donar-sangre", "pagina": {"lede": "L", "telefono": "(02254) 49-1670 / 49-1671"}}]
        with mock.patch.object(site_centers, "_links", lambda localize=True: links):
            html = site_news._page_blood_donation().decode()
        boton = re.search(r'<a class="stz-cta" href="tel:([^"]+)">([^<]+)</a>', html)
        self.assertEqual(boton.group(1), format.tel_href("(02254) 49-1670"))
        self.assertNotIn("49-1671", boton.group(2))


class TestEmergencyLink(unittest.TestCase):
    def test_a_spaced_number_dials_its_digits(self):
        with mock.patch.object(site_identity, "get", side_effect=_identity(emergency_number="0800 333 1234")):
            notice = site_news._emergency_notice_html("es")
        self.assertIn('href="tel:08003331234"', notice)
        self.assertIn(">0800 333 1234</a>", notice)

    def test_a_tel_target_is_a_real_button(self):
        self.assertEqual(site_news._real_target("tel:0800 123 4567"), "tel:08001234567")
        self.assertEqual(site_news._real_target("tel:"), "")


class TestOneLookOnPhones(unittest.TestCase):
    def test_buttons_keep_content_width_and_one_body_size(self):
        css = site_shell._PAGE_CSS
        self.assertNotIn(".campaign-action .stz-cta,.campaign-action .wa-action{width:100%", css)
        self.assertIn(".campaign-action .stz-cta{min-height:46px", css)
        self.assertIn(".campaign-page .lede{font-size:17px", css)
        self.assertIn(".campaign-section p{margin:0 0 10px;font-size:17px", css)

    def test_zoonosis_notice_is_not_brown_on_yellow(self):
        html = site_news._page_zoonosis().decode()
        main = html[html.index('<div class="campaign-page">'):]
        self.assertNotIn('class="gate"', main)
        self.assertNotIn('class="note"', main)
        self.assertIn('href="https://wa.me/5491140000006"', main)


if __name__ == "__main__":
    unittest.main()
