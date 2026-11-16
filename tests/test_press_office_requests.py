"""Pinamar press office's content requests (via the hospital technical counterpart, 2026-10-22), pinned
on synthetic data so they hold for every installation:

- the home headline/lede and a footer priority notice come from sitio.json;
  «Urgencias: <n>» closes every public footer;
- a `"kind": "walkin"` cluster («Demanda espontánea») lists every walk-in
  consultorio center by center, after the links it names, never a booking;
- a center job that IS one of its services («Vacunatorio») links to it;
- a center owned by an informational page (Zoonosis) has one address;
- the «¿Te trabás?» line uses the installation's WhatsApp help line;
- a center's WhatsApp shows as WhatsApp, never as a tel: link.

Run: python3 scripts/turnos_managed_exec.py --profile backend-focused \
     --test tests/test_press_office_requests.py
"""

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import i18n  # noqa: E402
from app.core import jinja_env  # noqa: E402
from app.core import site_identity  # noqa: E402
from app.patient_web import avatar_sprite  # noqa: E402
from app.patient_web import catalog  # noqa: E402
from app.patient_web import (
    site_catalog,
    site_centers,
    site_doctors,
    site_listing,
    site_news,
    site_page_centers,
    site_page_doctors,
    site_shell,
)

try:
    from PIL import Image
    HAY_PIL = True
except ImportError:  # pragma: no cover
    HAY_PIL = False

_REAL_GET = site_identity.get

CENTERS = [
    {"slug": "hosp", "name": "Hospital X", "address": "Calle 1", "phone": "(02254) 49-1670",
     "jobs": ["Atenderte en la guardia"], "bookable": True},
    {"slug": "caps-a", "name": "CAPS A", "address": "Calle 2", "whatsapp": "2254 60-2222",
     "jobs": ["Vacunatorio", "Odontología", "Ir al odontólogo sin turno"], "bookable": True},
    {"slug": "zoo", "name": "Zoonosis", "address": "Boulevard 3", "phone": "(02254) 49-1669",
     "whatsapp": "2254 58-5415", "email": "zoo@example.org", "bookable": False},
]
SERVICES = [
    {"id": "guardia-hosp", "nombre": "Guardia", "centro": "hosp", "modo": "walkin",
     "categoria": "consultorios", "cluster": "clinica", "horario": "Las 24 horas, todos los días"},
    {"id": "cardio-hosp", "nombre": "Cardiología", "centro": "hosp", "modo": "solicitud_con_orden",
     "categoria": "especialidades", "cluster": "cardiologia"},
    {"id": "vacunatorio-a", "nombre": "Vacunatorio", "centro": "caps-a", "modo": "walkin",
     "categoria": "consultorios", "cluster": "vacunas-prevencion", "horario": "Lunes a viernes de 8 a 13 h"},
    {"id": "odonto-a-1", "nombre": "Odontología", "centro": "caps-a", "modo": "walkin",
     "categoria": "consultorios", "cluster": "odontologia", "horario": "Lunes de 8 a 11 h"},
    {"id": "odonto-a-2", "nombre": "Odontología", "centro": "caps-a", "modo": "walkin",
     "categoria": "consultorios", "cluster": "odontologia", "horario": "Martes de 8 a 11 h"},
    {"id": "taller-a", "nombre": "Taller de escritura", "centro": "caps-a", "modo": "walkin",
     "categoria": "grupos", "cluster": "grupos", "horario": "Viernes de 11 a 13 h"},
    {"id": "lab-a", "nombre": "Extracciones", "centro": "caps-a", "modo": "walkin_con_orden",
     "categoria": "estudios", "cluster": "diagnostico", "horario": "Lunes de 7 a 9 h"},
]
LINKS = [
    {"id": "telemedicina", "nombre": "Telemedicina", "descripcion": "Videollamada, las 24 horas.",
     "href": "/telemedicine", "accion_texto": "Consultá por videollamada",
     "accion_href": "https://portal.example.org/"},
    {"id": "zoonosis", "nombre": "Zoonosis", "href": "/zoonosis", "centro": "zoo",
     "pagina": {"lede": "Coordinación de Zoonosis.", "whatsapp": "2254 60-2222",
                "servicios": ["Castración"]}},
]
WALKIN_DESC = "Sin turno: guardia las 24 horas en el hospital y vacunatorio en el CAPS."
CLUSTERS = [
    {"id": "clinica", "nombre": "Clínica", "top": 1},
    {"id": "demanda-espontanea", "nombre": "Demanda espontánea", "kind": "walkin",
     "top": 1.5, "links": ["telemedicina"], "descripcion": WALKIN_DESC},
    {"id": "vacunas-prevencion", "nombre": "Vacunas y prevención", "pinned": 1},
    {"id": "otras", "nombre": "Otras especialidades", "catch_all": True},
]


class _Catalog(unittest.TestCase):
    """Synthetic catalog: centers, services, links and clusters patched in."""

    def setUp(self):
        self._token = i18n.bind_lang("es")[1]
        patches = [
            mock.patch.object(site_centers, "_centers", lambda localize=True: [dict(c) for c in CENTERS]),
            mock.patch.object(site_catalog, "_services", lambda localize=True: [dict(s) for s in SERVICES]),
            mock.patch.object(site_centers, "_links", lambda localize=True: json.loads(json.dumps(LINKS))),
            mock.patch.object(site_catalog, "_clusters", lambda: [dict(c) for c in CLUSTERS]),
            mock.patch.object(site_catalog, "_professionals", lambda: []),
            mock.patch.object(site_catalog, "_doctors", lambda localize=True: []),
            mock.patch.object(site_centers, "_centers_info", lambda: {}),
            mock.patch.object(site_centers, "_centers_gallery", lambda: {}),
            mock.patch.object(site_centers, "_centers_reviews", lambda: {}),
            mock.patch.object(site_centers, "_center_photo",
                              lambda slug, info=None: f"/webdata/centro-photos/{slug}.webp"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        i18n.unbind(self._token)


def _identity(**over):
    def get(key, default=None):
        if key in over:
            return over[key]
        return _REAL_GET(key, default)
    return get


class TestHomeCopyAndFooter(unittest.TestCase):
    def setUp(self):
        self._token = i18n.bind_lang("es")[1]

    def tearDown(self):
        i18n.unbind(self._token)

    def test_pack_headline_and_lede_replace_the_engine_copy(self):
        with mock.patch.object(site_identity, "get", side_effect=_identity(
                hero_title="Encontrá la atención que necesitás",
                hero_lede="Accedé a turnos médicos desde un solo lugar.")):
            page = jinja_env.render("index.html")
        self.assertIn("<h1>Encontrá la atención que necesitás</h1>", page)
        self.assertIn("<p>Accedé a turnos médicos desde un solo lugar.</p>", page)
        self.assertNotIn("Turnos de la medicina pública", page)

    def test_without_pack_copy_the_engine_headline_stays_and_the_lede_no_longer_repeats_the_emergency(self):
        with mock.patch.object(site_identity, "get", side_effect=_identity(
                hero_title="", hero_lede="", city="Villa Test")):
            page = jinja_env.render("index.html")
        hero = page[page.index('<section class="hero">'):page.index("</section>", page.index('<section class="hero">'))]
        self.assertIn("Turnos de la medicina pública de Villa Test", hero)
        self.assertNotIn("Urgencias", hero)

    def test_every_footer_carries_the_notice_and_the_emergency_number(self):
        notice = "De acuerdo con el Decreto 1116/26, tienen prioridad los residentes."
        with mock.patch.object(site_identity, "get", side_effect=_identity(
                priority_notice=notice, emergency_number="107")):
            footers = {"shell": site_shell._site_footer()}
            for tpl in ("index.html", "about.html", "privacy.html", "terms.html",
                        "account.html", "account-reset.html"):
                footers[tpl] = jinja_env.render(tpl)
        for name, html in footers.items():
            with self.subTest(page=name):
                self.assertIn(f'<p class="fnotice">{notice}</p>', html)
                self.assertIn('<p class="fnotice furg">Urgencias: <a class="stz-link" href="tel:107">107</a></p>', html)

    def test_no_notice_without_the_pack_key(self):
        with mock.patch.object(site_identity, "get", side_effect=_identity(priority_notice="")):
            html = site_shell._site_footer()
        self.assertEqual(html.count('class="fnotice"'), 0)  # no priority notice
        self.assertEqual(html.count('class="fnotice furg"'), 1)  # only «Urgencias»

    def test_urgencias_comes_first_and_the_notice_second(self):
        """Footer design (the product owner, 2026-10-25): the number people look for in a
        hurry is the first row, the legal priority notice the second."""
        notice = "De acuerdo con el Decreto 1116/26, tienen prioridad los residentes."
        with mock.patch.object(site_identity, "get", side_effect=_identity(
                priority_notice=notice, emergency_number="107")):
            html = site_shell._site_footer()
        self.assertLess(html.index('class="fnotice furg"'), html.index(f'<p class="fnotice">{notice}'))
        self.assertLess(html.index(notice), html.index('<nav class="flinks">'))

    def test_citizen_cards_have_no_heading_and_name_telemedicine(self):
        page = jinja_env.render("index.html")
        block = page[page.index('<section class="citizen"'):]
        block = block[:block.index("</section>")]
        self.assertNotIn("Buen vecino", block)
        self.assertNotIn("<h2", block)
        self.assertIn("Una acción que salva vidas.", block)
        self.assertIn("<h3>Telemedicina</h3>", block)
        self.assertIn("Consultá por telemedicina", block)


class TestWalkinCluster(_Catalog):
    def test_directory_card_carries_one_description_from_the_pack(self):
        data = site_listing._directory_data()
        card = next(c for c in data["clusters"] if c["id"] == "demanda-espontanea")
        self.assertEqual(card["desc"], WALKIN_DESC)
        self.assertNotIn("sub", card)  # no grey subtitle under the title
        self.assertNotIn("donde", card)  # no list of centers either
        self.assertTrue(card["walkin"])  # the client closes the row with it, whatever its top
        self.assertEqual(card["n_servicios"], 4)  # guardia, vacunatorio, 2× odontología
        self.assertEqual(card["avatares"], [])
        by = {c["id"]: c for c in data["clusters"]}
        self.assertEqual(by["vacunas-prevencion"]["pinned"], 1)

    def test_a_pack_without_a_description_gets_the_generic_line(self):
        card = site_listing._walkin_card({"id": "x", "nombre": "X"})
        self.assertEqual(card["desc"], "Sin turno, por llegada")

    def test_a_blank_description_falls_back_and_markup_is_escaped(self):
        self.assertEqual(site_listing._walkin_card({"id": "x", "descripcion": "   "})["desc"],
                         "Sin turno, por llegada")
        cl = dict(CLUSTERS[1], descripcion="Guardia <b>24</b> & CAPS")
        with mock.patch.object(site_catalog, "_clusters", lambda: [cl]):
            html = site_page_doctors._page_cluster("demanda-espontanea").decode()
        self.assertIn('<p class="lede walkin-lede">Guardia &lt;b&gt;24&lt;/b&gt; &amp; CAPS</p>', html)

    def test_a_center_without_slug_does_not_break_the_card_or_the_page(self):
        broken = [{"name": "Sin slug"}] + list(site_centers._centers())
        with mock.patch.object(site_centers, "_centers", lambda *a, **k: broken):
            card = site_listing._walkin_card(CLUSTERS[1])
            html = site_page_doctors._page_cluster("demanda-espontanea").decode()
        self.assertEqual(card["n_servicios"], 4)
        self.assertIn('class="wk-list"', html)

    def test_the_page_stays_readable_and_uses_one_row_layout(self):
        html = site_page_doctors._page_cluster("demanda-espontanea").decode()
        self.assertIn("flex-direction:column", html)  # «when» always under the name
        self.assertIn(".walkin-lede{color:var(--text)", html.replace(" ", ""))

    def test_walkin_always_line_is_translated_in_english(self):
        with open(os.path.join(ROOT, "locales", "en", "page.json"), encoding="utf-8") as f:
            en = json.load(f)
        self.assertIn("page.walkin.always", en)

    def test_the_page_opens_with_the_same_text_as_the_card(self):
        html = site_page_doctors._page_cluster("demanda-espontanea").decode()
        self.assertIn(f'<p class="lede walkin-lede">{WALKIN_DESC}</p>', html)

    def test_a_focus_applies_only_to_the_photo_it_was_chosen_on(self):
        """A new upload from the panel must not inherit a crop picked on the
        old photo — it would cut a random patch of the new one."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "f.jpg")
        with open(path, "wb") as f:
            f.write(b"foto")
        sha = hashlib.sha256(b"foto").hexdigest()[:12]
        info = {"avatar": {"x": 0.62, "y": 0.7, "zoom": 2, "for": sha}}
        self.assertEqual(site_centers._center_focus(info, path), (0.62, 0.7, 2.0))
        info["avatar"]["for"] = "000000000000"
        self.assertEqual(site_centers._center_focus(info, path), site_centers._CENTER_FOCUS_DEFAULT)
        self.assertEqual(site_centers._center_focus({}, path), site_centers._CENTER_FOCUS_DEFAULT)

    def test_a_pack_photo_path_never_leaves_the_pack(self):
        for malo in ("/webdata/../secret.webp", "/webdata/a/../../b.webp",
                     "/webdata/a\\b.webp", "https://x.org/a.webp", ""):
            with self.subTest(photo=malo):
                self.assertEqual(site_centers._center_photo_file("nadie-xyz", {"photo": malo}), "")

    def test_page_lists_walkins_by_center_after_the_links_and_never_books(self):
        html = site_page_doctors._page_cluster("demanda-espontanea").decode()
        self.assertIn('<a href="/telemedicine">Telemedicina</a>', html)
        self.assertIn('href="https://portal.example.org/"', html)
        self.assertLess(html.index("Telemedicina"), html.index('href="/center/hosp"'))
        self.assertLess(html.index('href="/center/hosp"'), html.index('href="/center/caps-a"'))
        ids = re.findall(r'class="wk-name" href="([^"]+)"', html)
        self.assertEqual(ids, ["/service/guardia-hosp", "/service/odonto-a-1",
                               "/service/odonto-a-2", "/service/vacunatorio-a"])
        # no table, no empty column: the Guardia says it is open 24 h
        self.assertNotIn("lst-data", html)
        self.assertIn('<span class="wk-when">Las 24 horas, todos los días</span>', html)
        # groups/workshops and walk-in-with-order stay out
        self.assertNotIn("taller-a", html)
        self.assertNotIn("lab-a", html)
        rows = html.split('class="wk-list"', 1)[1].split("</ul>", 1)[0]
        self.assertNotIn("/book", rows)

    def test_catch_all_closes_the_home_sections_even_after_pack_clusters(self):
        # A town pack appends its clusters after the global «Otras».
        clusters = [CLUSTERS[0], CLUSTERS[3], CLUSTERS[1]]
        svs = SERVICES + [{"id": "x", "nombre": "X", "centro": "hosp", "modo": "solicitud_con_orden",
                           "categoria": "especialidades", "cluster": "otras"}]
        with mock.patch.object(site_catalog, "_clusters", lambda: [dict(c) for c in clusters]), \
                mock.patch.object(site_catalog, "_services", lambda localize=True: svs):
            ids = [i for i, _ in site_catalog.home_sections()]
        self.assertEqual(ids[-1], "otras")
        self.assertIn("demanda-espontanea", ids)

    def test_home_sections_and_unknown_page(self):
        self.assertIn(("demanda-espontanea", "Demanda espontánea"), site_catalog.home_sections())
        with mock.patch.object(site_catalog, "_services", lambda localize=True: []):
            with mock.patch.object(site_centers, "_links", lambda localize=True: []):
                self.assertIsNone(site_page_doctors._page_cluster("demanda-espontanea"))


@unittest.skipUnless(HAY_PIL, "Pillow no instalado")
class TestCenterCirclesNeverLoadTheFullPhoto(_Catalog):
    """The hospital circles used to pull huge pictures (the product owner,
    2026-10-28). A small round picture of a center is a ~1 KB tile of the
    shared atlas; the full photo (a panel upload can be several MB) is for
    the places that show it large. Runs the REAL atlas over throwaway photos,
    nothing mocked between the circle and the image."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.fotos = {}
        for slug in ("hosp", "caps-a", "zoo"):
            ruta = os.path.join(self.tmp, slug + ".png")
            Image.new("RGB", (1200, 900), (200, 40, 40)).save(ruta)
            self.fotos[slug] = ruta
        sprite_dir = avatar_sprite.SPRITE_DIR
        avatar_sprite.SPRITE_DIR = os.path.join(self.tmp, "avatar-sprite")
        self.addCleanup(setattr, avatar_sprite, "SPRITE_DIR", sprite_dir)
        patch = mock.patch.object(site_centers, "_center_photo_file",
                                  lambda slug, info=None: self.fotos.get(slug, ""))
        patch.start()
        self.addCleanup(patch.stop)
        self._reset()
        self.addCleanup(self._reset)

    @staticmethod
    def _reset():
        avatar_sprite._cache = None
        avatar_sprite._deadline = 0.0
        avatar_sprite._man_cache = None
        avatar_sprite.invalidate()
        site_doctors._sprite_memo = None  # `global` state lives beside its writer, not on the facade
        site_doctors._sprite_deadline = 0.0

    def test_every_center_with_a_photo_gets_a_tile_not_only_the_walk_in_ones(self):
        self.assertEqual([k for k, _p, _f in site_centers._center_faces()],
                         ["center:hosp", "center:caps-a", "center:zoo"])
        for slug in ("hosp", "caps-a", "zoo"):
            with self.subTest(center=slug):
                style = site_doctors.center_circle_style(slug)
                self.assertIn("/avatar-sprite/", style)
                self.assertNotIn("/webdata/", style)
                self.assertNotIn("/center-photo/", style)

    def test_a_center_without_a_photo_has_no_circle_style(self):
        self.assertEqual(site_doctors.center_circle_style("nadie-xyz"), "")

    def test_the_home_card_circles_are_tiles_never_the_photo_file(self):
        data = site_listing._directory_data()
        card = next(c for c in data["clusters"] if c["id"] == "demanda-espontanea")
        self.assertEqual(card["avatares"], [])  # named in text since 2026-11-13
        blob = json.dumps(data)
        self.assertNotIn("/webdata/centro-photos/", blob)
        self.assertNotIn("/center-photo/", blob)

    def test_the_atlas_tile_is_a_few_kilobytes_next_to_the_photo(self):
        style = site_doctors.center_circle_style("hosp")
        clave = re.search(r"/avatar-sprite/([0-9a-f]{16})\.webp", style).group(1)
        self.assertLess(len(avatar_sprite.atlas_bytes(clave)), 20_000)


class TestCenterPages(_Catalog):
    def test_a_job_named_like_one_service_links_to_it(self):
        html = site_page_centers._page_center("caps-a").decode()
        self.assertIn('<li><a href="/service/vacunatorio-a">Vacunatorio</a></li>', html)
        # two «Odontología» services: ambiguous, stays text
        self.assertIn("<li>Odontología</li>", html)
        self.assertIn("<li>Ir al odontólogo sin turno</li>", html)

    def test_center_whatsapp_is_shown_as_whatsapp(self):
        html = site_page_centers._page_center("caps-a").decode()
        self.assertIn('href="https://wa.me/5492254602222"', html)
        self.assertNotIn('href="tel:+5492254602222"', html)
        links = site_centers._center_call_links(CENTERS[1], "stz-link")
        self.assertEqual(len(links), 1)
        self.assertIn("WhatsApp · ", links[0])
        self.assertIn("whatsapp-sf-regular.svg", links[0])

    def test_a_center_owned_by_an_informational_page_has_one_address(self):
        self.assertEqual(site_centers._center_pages(), {"zoo": "/zoonosis"})
        self.assertEqual(site_centers.center_href("zoo"), "/zoonosis")
        self.assertEqual(site_centers.center_href("caps-a"), "/center/caps-a")
        tiles = {c["slug"]: c for c in site_centers._centers_web()}
        self.assertEqual(tiles["zoo"]["href"], "/zoonosis")
        self.assertNotIn("href", tiles["caps-a"])
        self.assertEqual(site_centers._tile_center(tiles["zoo"])["href"], "/zoonosis")

    def test_zoonosis_page_takes_the_center_data_and_labels_the_landline(self):
        html = site_news._page_zoonosis().decode()
        self.assertIn("Coordinación de Zoonosis.", html)
        self.assertIn('href="https://wa.me/5492254602222"', html)  # the page's own WhatsApp wins
        self.assertNotIn("5492254585415", html)
        self.assertIn('Teléfono fijo: <a class="stz-link" href="tel:', html)
        self.assertIn("mailto:zoo@example.org", html)
        self.assertIn("Boulevard 3", html)
        self.assertIn("/webdata/centro-photos/zoo.webp", html)


class TestHelpLine(_Catalog):
    def test_the_installation_help_line_wins(self):
        with mock.patch.object(site_identity, "get", side_effect=_identity(help_whatsapp="2254 60-2222")):
            html = site_centers.help_contact_html(CENTERS[0], "el hospital")
        self.assertIn("Escribinos por WhatsApp", html)
        self.assertIn('href="https://wa.me/5492254602222"', html)
        self.assertNotIn("49-1670", html)

    def test_without_it_the_center_contact_is_used(self):
        with mock.patch.object(site_identity, "get", side_effect=_identity(help_whatsapp="")):
            phone = site_centers.help_contact_html(CENTERS[0], "el hospital")
            wa = site_centers.help_contact_html(CENTERS[1], "el centro")
            nothing = site_centers.help_contact_html({}, "el centro")
        self.assertIn("Llamá a Hospital X", phone)
        self.assertIn('href="tel:', phone)
        self.assertIn("Escribile a CAPS A por WhatsApp", wa)
        self.assertEqual(nothing, "")

    def test_upload_order_page_uses_the_same_line(self):
        from app.modules.turnos import preparation
        with mock.patch.object(site_identity, "get", side_effect=_identity(help_whatsapp="2254 60-2222")):
            self.assertIn("Escribinos por WhatsApp", preparation._contact_html(CENTERS[0]))


class TestCenterWhatsappField(unittest.TestCase):
    def test_catalog_keeps_a_center_whatsapp(self):
        doc, err = catalog._validate_center({"name": "CAPS B", "whatsapp": "  2254 60-2222 "}, True)
        self.assertIsNone(err)
        self.assertEqual(doc["whatsapp"], "2254 60-2222")


if __name__ == "__main__":
    unittest.main()
