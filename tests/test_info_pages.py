"""test_info_pages.py -- public information pages built from `type: "pagina"`
knowledge articles (patient_web/info_pages.py, route /<slug>).

Pins: the /vacunacion page of the public packs renders (national + provincial
blocks, H1 city from site_identity, JSON-LD, canonical, sitemap); without a
local article there is no local block and no "ask the hospital" filler; every
block carries "Fuente: ... (consultado el dd/mm/yyyy)"; a block without a
complete source is never rendered; an unknown slug is a 404; a private town
pack's article (TURNOS_PRIVATE_PACKS_DIR) joins the same page.

Run: python3 -W error::ResourceWarning -m unittest discover -s tests -p "test_info_pages.py"
"""

import json
import os
import re
import sys
import tempfile
import unittest
import _lifespan
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from fastapi.testclient import TestClient  # noqa: E402

from app import main as app_module  # noqa: E402
from app.core import config  # noqa: E402
from app.core import data_packs  # noqa: E402
from app.core import site_identity  # noqa: E402
from app.patient_web import catalog  # noqa: E402
from app.patient_web import info_pages  # noqa: E402
from app.patient_web import site_page_service

import datetime as _dt  # noqa: E402
YEAR = _dt.date.today().year


def _identity(tipo, base, incluir_borrados=False, strict=False):
    meta = {it.get(catalog.id_key(tipo)): {"_origen": "json", "_borrado": False} for it in base}
    return list(base), meta


def _reset():
    config._reset_cache_para_tests()
    catalog._JSON_CACHE.clear()
    catalog.invalidate()
    data_packs.invalidate()
    site_identity._CACHE.clear()


def setUpModule():
    _lifespan.no_background_services()


class _Chain(unittest.TestCase):
    CHAIN = "global,ar,ar-b,example-town"

    def setUp(self):
        # Cleanups run last-in first-out: the merge patch stops, then the environment is back, then the caches reset.
        self.addCleanup(_reset)
        _env.set_env(self, TURNOS_WEBDATA_DIR=None, PACK_CHAIN=self.CHAIN)
        self._merge = mock.patch.object(catalog, "merge_with_overlay", _identity)
        self._merge.start()
        self.addCleanup(self._merge.stop)
        _reset()


class TestVacunacionFromPublicPacks(_Chain):
    def test_page_renders_national_and_provincial_blocks(self):
        html = info_pages.render("vacunacion").decode()
        self.assertIn("Calendario Nacional 2026 por etapa de la vida", html)
        self.assertIn("Vacunas gratuitas en la Provincia de Buenos Aires", html)
        self.assertIn("Buscá tu vacunatorio", html)
        self.assertLess(html.index("Dónde y cómo vacunarte"),
                        html.index("Vacunas gratuitas en la Provincia"))

    def test_h1_city_comes_from_site_identity_not_from_the_text(self):
        city = site_identity.get("city")
        self.assertTrue(city)
        html = info_pages.render("vacunacion").decode()
        self.assertIn(f"<h1 class=\"h1\">Vacunación en {city}: calendario {YEAR} y dónde vacunarte</h1>", html)
        real = site_identity.get
        with mock.patch.object(site_identity, "get",
                               side_effect=lambda k, d=None: "Villa Ejemplo" if k == "city" else real(k, d)):
            html = info_pages.render("vacunacion").decode()
        self.assertIn("Vacunación en Villa Ejemplo:", html)

    def test_title_description_canonical_and_jsonld(self):
        html = info_pages.render("vacunacion").decode()
        self.assertRegex(html, r"<title>Vacunación en [^<|]+: calendario " + str(YEAR) + r" y vacunatorios \| ")
        self.assertRegex(html, r'<meta name="description" content="[^"]{40,}"')
        self.assertIn('<meta name="robots" content="index,follow">', html)
        self.assertRegex(html, r'<link rel="canonical" href="[^"]+/vacunacion">')
        ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1))
        self.assertEqual(ld["@type"], "MedicalWebPage")
        self.assertTrue(ld["url"].endswith("/vacunacion"))
        self.assertEqual(ld["lastReviewed"], "2026-10-07")
        urls = {c["url"] for c in ld["citation"]}
        self.assertIn("https://www.argentina.gob.ar/salud/vacunas", urls)
        self.assertIn("https://vacunacion.gba.gob.ar/calendario", urls)

    def test_every_block_has_a_source_with_its_check_date(self):
        html = info_pages.render("vacunacion").decode()
        sections = re.findall(r'<section class="campaign-section">(.*?)</section>', html, re.S)
        self.assertGreaterEqual(len(sections), 8)
        for sec in sections:
            self.assertRegex(
                sec, r'Fuente: <a class="stz-link" href="https://[^"]+"[^>]*>[^<]+</a> '
                     r'\(consultado el \d{2}/\d{2}/\d{4}\)')
        self.assertIn("(consultado el 07/10/2026)", html)

    def test_no_local_article_means_no_local_block_and_no_filler(self):
        html = info_pages.render("vacunacion").decode()
        self.assertNotIn("Vacunatorio en los CAPS", html)
        self.assertNotRegex(html.lower(), r"consult[aá](?:\s+\w+){0,3}\s+(?:en|con) (?:el|la) (?:hospital|municipalidad)")

    def test_page_is_in_the_sitemap_and_routed(self):
        from app.patient_web import sitemap
        locs = [u for u, _ in sitemap._urls()]
        self.assertTrue(any(u.endswith("/vacunacion") for u in locs), locs)
        with TestClient(app_module.app) as client:
            res = client.get("/vacunacion")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Fuente:", res.text)

    def test_unknown_slug_is_404(self):
        with TestClient(app_module.app) as client:
            res = client.get("/no-existe-esta-pagina")
        self.assertEqual(res.status_code, 404)
        self.assertIsNone(info_pages.render("no-existe-esta-pagina"))
        self.assertIsNone(info_pages.match("GET", "/vacunacion/extra"))
        self.assertIsNone(info_pages.render("../etc/passwd"))

    def test_service_page_links_to_the_information_page(self):
        service = {"id": "vacunacion-riverside-clinic", "cluster": "vacunas-prevencion"}
        html = site_page_service._service_info_page_link_html(service)
        self.assertIn('href="/vacunacion"', html)
        self.assertEqual(site_page_service._service_info_page_link_html({"cluster": "cardiologia"}), "")


def _article(id_, level, blocks, **extra):
    base = {"id": id_, "type": "pagina", "slug": "tema-x", "title": "Tema X",
            "jurisdiction": level, "summary": "s", "content": "c", "tags": [],
            "status": "publicado", "blocks": blocks}
    base.update(extra)
    return base


def _block(h2, **extra):
    base = {"h2": h2, "paragraphs": ["Texto."],
            "source": {"title": "Fuente", "url": "https://example.gob.ar/x",
                       "agency": "Org", "verified_at": "2026-10-07"}}
    base.update(extra)
    return base


class TestEngineRules(_Chain):
    def _render(self, articles):
        with mock.patch.object(catalog, "knowledge", return_value=articles):
            return info_pages.render("tema-x")

    def test_block_without_complete_source_is_never_rendered(self):
        no_source = _block("Sin fuente")
        del no_source["source"]
        no_date = _block("Sin fecha")
        no_date["source"].pop("verified_at")
        http_only = _block("Sin https")
        http_only["source"]["url"] = "http://example.gob.ar/x"
        html = self._render([_article("a", "nacion", [_block("Con fuente"), no_source, no_date, http_only])]).decode()
        self.assertIn("Con fuente", html)
        for missing in ("Sin fuente", "Sin fecha", "Sin https"):
            self.assertNotIn(missing, html)

    def test_page_with_only_unsourced_blocks_is_not_a_page(self):
        bad = _block("Sin fuente")
        del bad["source"]
        self.assertIsNone(self._render([_article("a", "nacion", [bad])]))

    def test_levels_stack_national_provincial_local_whatever_the_list_order(self):
        html = self._render([
            _article("c", "pinamar", [_block("Local")]),
            _article("a", "nacion", [_block("Nacional")]),
            _article("b", "provincia", [_block("Provincial")]),
        ]).decode()
        self.assertLess(html.index("Nacional"), html.index("Provincial"))
        self.assertLess(html.index("Provincial"), html.index("Local"))

    def test_draft_and_expired_articles_are_ignored(self):
        draft = _article("d", "nacion", [_block("Borrador")], status="borrador")
        old = _article("o", "nacion", [_block("Vencido")], valid_to="2000-01-01")
        self.assertIsNone(self._render([draft, old]))

    def test_chat_does_not_see_pagina_articles(self):
        from app.patient_web import knowledge
        with mock.patch.object(catalog, "knowledge",
                               return_value=[_article("a", "nacion", [_block("X")])]):
            self.assertEqual(knowledge.published_articles(), [])

    def test_admin_validator_keeps_pagina_fields(self):
        doc = _article("a", "nacion", [_block("X")], page={"h1": "H"},
                       source={"title": "T", "url": "https://www.argentina.gob.ar/salud/vacunas",
                               "agency": "M", "verified_at": "2026-10-07", "review_every_days": 30})
        clean, err = catalog._validate_knowledge(doc, True)
        self.assertIsNone(err)
        self.assertEqual(clean["slug"], "tema-x")
        self.assertEqual(len(clean["blocks"]), 1)
        _clean, err = catalog._validate_knowledge(dict(doc, slug="Mal Slug"), True)
        self.assertIn("slug", err)


class TestPrivatePackJoinsThePage(_Chain):
    """A town pack from TURNOS_PRIVATE_PACKS_DIR adds its own article to the
    same slug; the public blocks stay and the local one comes last."""
    CHAIN = "global,ar,ar-b,ar-b-pinamar"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        pack = os.path.join(self._tmp.name, "ar-b-pinamar")
        os.makedirs(pack)
        local = _article("pagina-vacunacion-local-demo", "pinamar",
                         [_block("Bloque local de prueba")], slug="vacunacion")
        with open(os.path.join(pack, "pack.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "ar-b-pinamar", "level": 4, "parent": "ar-b"}, f)
        with open(os.path.join(pack, "conocimiento.json"), "w", encoding="utf-8") as f:
            json.dump({"articles": [local]}, f)
        with open(os.path.join(pack, "sitio.json"), "w", encoding="utf-8") as f:
            json.dump({"city": "Villa Demo"}, f)
        data_packs.write_inventory(pack)
        super().setUp()
        _env.set_env(self, TURNOS_PRIVATE_PACKS_DIR=self._tmp.name)
        _reset()

    def test_local_block_is_picked_up_and_comes_last(self):
        self.assertEqual(data_packs.active_chain(), ("global", "ar", "ar-b", "ar-b-pinamar"))
        html = info_pages.render("vacunacion").decode()
        self.assertIn("Bloque local de prueba", html)
        self.assertIn("Vacunación en Villa Demo:", html)
        self.assertLess(html.index("Vacunas gratuitas en la Provincia"),
                        html.index("Bloque local de prueba"))


class TestVacunaAntigripalPage(_Chain):
    """/vacuna-antigripal: national + provincial blocks from the public packs;
    the local (Pinamar) blocks live only in the private pack."""
    SLUG = "vacuna-antigripal"

    def _html(self):
        return info_pages.render(self.SLUG).decode()

    def test_page_renders_national_and_provincial_blocks_in_order(self):
        html = self._html()
        for h2 in ("Quiénes se vacunan", "Factores de riesgo", "Orden médica y qué llevar",
                   "Si sos afiliado a PAMI", "Gratuita en toda la Provincia", "Cuándo no corresponde vacunarte"):
            self.assertIn(f"<h2>{h2}</h2>", html)
        self.assertLess(html.index("Si sos afiliado a PAMI"), html.index("Gratuita en toda la Provincia"))

    def test_h1_targets_the_query_and_city_comes_from_site_identity(self):
        city = site_identity.get("city")
        self.assertTrue(city)
        html = self._html()
        self.assertIn(f'<h1 class="h1">Vacuna antigripal {YEAR} en {city}: quiénes se vacunan y dónde</h1>', html)
        self.assertRegex(html, r"<title>Vacuna antigripal " + str(YEAR) + r" en [^<|]+: quiénes y dónde vacunarse \| ")
        real = site_identity.get
        with mock.patch.object(site_identity, "get",
                               side_effect=lambda k, d=None: "Villa Ejemplo" if k == "city" else real(k, d)):
            html = self._html()
        self.assertIn(f"Vacuna antigripal {YEAR} en Villa Ejemplo:", html)

    def test_every_block_has_source_and_check_date_and_jsonld_cites_them(self):
        html = self._html()
        sections = re.findall(r'<section class="campaign-section">(.*?)</section>', html, re.S)
        self.assertGreaterEqual(len(sections), 9)
        # La auditoría de pensiones (nota de febrero de 2025) no está confirmada para 2026: no va.
        self.assertNotIn("auditor", html.lower())
        for sec in sections:
            self.assertRegex(
                sec, r'Fuente: <a class="stz-link" href="https://[^"]+"[^>]*>[^<]+</a> '
                     r'\(consultado el 07/10/2026\)')
        ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1))
        urls = {c["url"] for c in ld["citation"]}
        self.assertTrue({"https://www.argentina.gob.ar/salud/vacunas/antigripal", "https://pami.org.ar/antigripal",
                         "https://vacunacion.gba.gob.ar/gripe"} <= urls)
        self.assertEqual(ld["lastReviewed"], "2026-10-07")
        self.assertRegex(html, r'<link rel="canonical" href="[^"]+/vacuna-antigripal">')

    def test_key_facts_match_the_sources(self):
        html = self._html()
        for fact in ("Personas de 65 años o más.", "Niños de 6 a 24 meses.", "hasta 10 días después del parto",
                     "idealmente antes del comienzo del invierno", "138 (PAMI Escucha)",
                     "nadie puede cobrarte por la vacuna", "Su aplicación no requiere orden médica."):
            self.assertIn(fact, html)

    def test_no_local_article_means_no_local_block_and_no_filler(self):
        html = self._html()
        self.assertNotIn("Campaña antigripal 2026 en Pinamar", html)
        self.assertNotIn("CAPS", html)
        self.assertNotRegex(html.lower(), r"consult[aá](?:\s+\w+){0,3}\s+(?:en|con) (?:el|la) (?:hospital|municipalidad)")

    def test_routed_in_sitemap_and_linked_from_vacunacion(self):
        from app.patient_web import sitemap
        self.assertTrue(any(u.endswith("/vacuna-antigripal") for u, _ in sitemap._urls()))
        with TestClient(app_module.app) as client:
            res = client.get("/vacuna-antigripal")
            vac = client.get("/vacunacion")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Fuente:", res.text)
        self.assertIn('<a class="stz-link" href="/vacuna-antigripal">', vac.text)
        self.assertNotIn('href="/vacuna-antigripal" target', vac.text)


class TestChipAnticonceptivoPage(_Chain):
    """/chip-anticonceptivo: national + provincial blocks from the public packs;
    no local article exists (nothing official on the implant for the town)."""
    SLUG = "chip-anticonceptivo"

    def _html(self):
        return info_pages.render(self.SLUG).decode()

    def test_page_renders_national_then_provincial_blocks(self):
        html = self._html()
        for h2 in ("Qué es el implante subdérmico", "Cuánto dura", "Quiénes pueden usarlo",
                   "Cómo es la colocación", "Efectos secundarios", "Dónde conseguirlo",
                   "El implante en la Provincia de Buenos Aires",
                   "Gratuito y sin autorización desde los 13 años"):
            self.assertIn(f"<h2>{h2}</h2>", html)
        self.assertLess(html.index("Dónde conseguirlo"), html.index("El implante en la Provincia de Buenos Aires"))

    def test_h1_targets_the_query_and_explains_the_official_name(self):
        city = site_identity.get("city")
        self.assertTrue(city)
        html = self._html()
        self.assertIn(f'<h1 class="h1">Chip anticonceptivo en {city}: qué es, cuánto dura y cómo acceder</h1>', html)
        self.assertIn("se llama oficialmente implante subdérmico", html)
        real = site_identity.get
        with mock.patch.object(site_identity, "get",
                               side_effect=lambda k, d=None: "Villa Ejemplo" if k == "city" else real(k, d)):
            html = self._html()
        self.assertIn("Chip anticonceptivo en Villa Ejemplo:", html)

    def test_every_block_has_source_and_check_date_and_jsonld_cites_them(self):
        html = self._html()
        sections = re.findall(r'<section class="campaign-section">(.*?)</section>', html, re.S)
        self.assertGreaterEqual(len(sections), 12)
        for sec in sections:
            self.assertRegex(
                sec, r'Fuente: <a class="stz-link" href="https://[^"]+"[^>]*>[^<]+</a> '
                     r'\(consultado el 07/10/2026\)')
        ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1))
        urls = {c["url"] for c in ld["citation"]}
        self.assertTrue({"https://www.argentina.gob.ar/salud/sexual/metodos-anticonceptivos/implante-subdermico",
                         "https://www.argentina.gob.ar/sites/default/files/bancos/2023-06/nota_tecnica_5_implantes_08062023.pdf",
                         "https://www.ms.gba.gov.ar/sitios/aborto/comunidad/metodos-anticonceptivos/"} <= urls)
        self.assertEqual(ld["lastReviewed"], "2026-10-07")
        self.assertRegex(html, r'<link rel="canonical" href="[^"]+/chip-anticonceptivo">')

    def test_key_facts_match_the_sources(self):
        html = self._html()
        for fact in ("brinda protección entre 3 y 5 años", "Línea Salud Sexual: 0 800 222 3444",
                     "eficacia anticonceptiva mayor al 99%", "contraindicada únicamente en personas con cáncer de mama actual",
                     "no protegen del VIH ni de otras infecciones de transmisión sexual",
                     "dentro de los 7 días del comienzo del sangrado menstrual",
                     "Por la Ley 25.673, los anticonceptivos están incluidos en la cobertura médica obligatoria",
                     "todos los métodos se entregan gratuitamente"):
            self.assertIn(fact, html)

    def test_no_local_article_means_no_local_block_and_no_filler(self):
        html = self._html()
        self.assertNotIn("CAPS", html)
        self.assertNotRegex(html.lower(), r"consult[aá](?:\s+\w+){0,3}\s+(?:en|con) (?:el|la) (?:hospital|municipalidad)")
        self.assertNotRegex(html.lower(), r"(?:ya |no )?(?:lo |la )?coloca(?:n)? en (?:el )?(?:hospital|caps)")

    def test_routed_and_in_sitemap(self):
        from app.patient_web import sitemap
        self.assertTrue(any(u.endswith("/chip-anticonceptivo") for u, _ in sitemap._urls()))
        with TestClient(app_module.app) as client:
            res = client.get("/chip-anticonceptivo")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Fuente:", res.text)


class TestPensionPorDiscapacidadPage(_Chain):
    """/pension-por-discapacidad: national blocks only (no provincial or local article).
    One-segment slug: the engine does not route /discapacidad/pension."""
    SLUG = "pension-por-discapacidad"

    def _html(self):
        return info_pages.render(self.SLUG).decode()

    def test_page_renders_national_blocks_only(self):
        html = self._html()
        for h2 in ("Qué es y a quién corresponde", "Requisitos", "Qué documentación necesitás",
                   "Certificado Médico Oficial (CMO)", "Cómo hacer el trámite", "Cuánto se cobra",
                   "Es gratis: no uses gestores"):
            self.assertIn(f"<h2>{h2}</h2>", html)
        self.assertNotIn("CAPS", html)
        self.assertNotIn("/cud", html)

    def test_h1_targets_the_query(self):
        city = site_identity.get("city")
        self.assertTrue(city)
        self.assertIn(f'<h1 class="h1">Pensión por discapacidad en {city}: requisitos y cómo tramitarla</h1>', self._html())

    def test_every_block_has_source_and_check_date(self):
        html = self._html()
        sections = re.findall(r'<section class="campaign-section">(.*?)</section>', html, re.S)
        self.assertGreaterEqual(len(sections), 9)
        for sec in sections:
            self.assertRegex(
                sec, r'Fuente: <a class="stz-link" href="https://[^"]+"[^>]*>[^<]+</a> '
                     r'\(consultado el 07/10/2026\)')
        ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1))
        urls = {c["url"] for c in ld["citation"]}
        self.assertIn("https://www.argentina.gob.ar/andis/solicitar-una-pension-no-contributiva", urls)
        self.assertEqual(ld["lastReviewed"], "2026-10-07")

    def test_key_facts_match_the_sources_and_no_peso_amounts(self):
        html = self._html()
        for fact in ("una disminución del 66% o más", "tenés que tener hasta 65 años",
                     "equivale al 70% de una jubilación mínima", "(54 11) 3910-1010",
                     "10 años de residencia continuada", "No aceptes gestores"):
            self.assertIn(fact, html)
        self.assertNotRegex(html, r"\$\s?\d")

    def test_routed_in_sitemap_and_two_segment_path_is_404(self):
        from app.patient_web import sitemap
        self.assertTrue(any(u.endswith("/pension-por-discapacidad") for u, _ in sitemap._urls()))
        with TestClient(app_module.app) as client:
            self.assertEqual(client.get("/pension-por-discapacidad").status_code, 200)
            self.assertEqual(client.get("/discapacidad/pension").status_code, 404)


class TestInternalLinksInBlocks(_Chain):
    def _render(self, links):
        art = _article("pagina-demo-links", "nacion", [_block("Con enlaces", links=links)], slug="demo-links")
        with mock.patch.object(catalog, "knowledge", return_value=[art]):
            return info_pages.render("demo-links").decode()

    def test_internal_page_link_is_same_tab_and_unsafe_ones_are_dropped(self):
        html = self._render([{"title": "Otra", "url": "/otra-pagina"},
                             {"title": "Externa", "url": "https://example.org/x"},
                             {"title": "Malo", "url": "//evil.example/x"},
                             {"title": "Malo2", "url": "/a/b"},
                             {"title": "Malo3", "url": "javascript:alert(1)"}])
        self.assertIn('<a class="stz-link" href="/otra-pagina">Otra</a>', html)
        self.assertIn('href="https://example.org/x" target="_blank"', html)
        for bad in ("evil.example", "/a/b", "javascript:"):
            self.assertNotIn(bad, html)


class TestVacunaAntigripalWithPrivatePack(_Chain):
    """A town pack from TURNOS_PRIVATE_PACKS_DIR adds its local blocks to the
    antigripal slug (synthetic article: the real Pinamar text stays out of the repo)."""
    CHAIN = "global,ar,ar-b,ar-b-pinamar"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        pack = os.path.join(self._tmp.name, "ar-b-pinamar")
        os.makedirs(pack)
        local = _article("pagina-antigripal-local-demo", "pinamar",
                         [_block("Bloque local antigripal")], slug="vacuna-antigripal")
        with open(os.path.join(pack, "pack.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "ar-b-pinamar", "level": 4, "parent": "ar-b"}, f)
        with open(os.path.join(pack, "conocimiento.json"), "w", encoding="utf-8") as f:
            json.dump({"articles": [local]}, f)
        with open(os.path.join(pack, "sitio.json"), "w", encoding="utf-8") as f:
            json.dump({"city": "Villa Demo"}, f)
        data_packs.write_inventory(pack)
        super().setUp()
        _env.set_env(self, TURNOS_PRIVATE_PACKS_DIR=self._tmp.name)
        _reset()

    def test_local_block_is_picked_up_and_comes_last(self):
        html = info_pages.render("vacuna-antigripal").decode()
        self.assertIn(f"Vacuna antigripal {YEAR} en Villa Demo:", html)
        self.assertIn("Bloque local antigripal", html)
        self.assertLess(html.index("Centros de vacunación"), html.index("Bloque local antigripal"))


if __name__ == "__main__":
    unittest.main()


class TestYearIsAlwaysCurrent(_Chain):
    """{year} in page meta and link titles is the current year (owner,
    2026-10-07): the title of a yearly page never goes stale."""

    def test_year_placeholder_follows_the_calendar(self):
        with mock.patch.object(info_pages, "_this_year", return_value=2031):
            html = info_pages.render("vacuna-antigripal").decode()
        self.assertIn("Vacuna antigripal 2031 en", html)
        self.assertNotIn("{year}", html)


class TestCudPage(_Chain):
    """/certificado-unico-de-discapacidad: national + provincial blocks from the public packs;
    the Pinamar office/hours live only in the private pack (never in the repo)."""
    SLUG = "certificado-unico-de-discapacidad"

    def _html(self):
        return info_pages.render(self.SLUG).decode()

    def test_page_renders_national_then_provincial_blocks(self):
        html = self._html()
        for h2 in ("Qué es el CUD", "Qué derechos te da", "Cómo tramitarlo, paso a paso",
                   "Consultá los requisitos online", "El CUD digital en Mi Argentina", "Si perdiste el CUD",
                   "CUD sin vencimiento", "El CUD en los trámites de salud de la Provincia"):
            self.assertIn(f"<h2>{h2}</h2>", html)
        self.assertLess(html.index("CUD sin vencimiento"), html.index("El CUD en los trámites de salud de la Provincia"))

    def test_h1_targets_the_query_and_city_comes_from_site_identity(self):
        city = site_identity.get("city")
        self.assertTrue(city)
        self.assertIn(f'<h1 class="h1">Certificado único de discapacidad (CUD) en {city}: cómo tramitarlo</h1>', self._html())
        real = site_identity.get
        with mock.patch.object(site_identity, "get",
                               side_effect=lambda k, d=None: "Villa Ejemplo" if k == "city" else real(k, d)):
            html = self._html()
        self.assertIn("(CUD) en Villa Ejemplo:", html)

    def test_every_block_has_source_and_check_date_and_jsonld_cites_them(self):
        html = self._html()
        sections = re.findall(r'<section class="campaign-section">(.*?)</section>', html, re.S)
        self.assertGreaterEqual(len(sections), 9)
        for sec in sections:
            self.assertRegex(
                sec, r'Fuente: <a class="stz-link" href="https://[^"]+"[^>]*>[^<]+</a> '
                     r'\(consultado el 07/10/2026\)')
        ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1))
        urls = {c["url"] for c in ld["citation"]}
        self.assertTrue({"https://www.argentina.gob.ar/servicio/como-obtener-el-certificado-unico-de-discapacidad-cud",
                         "https://www.argentina.gob.ar/cud/consulta-de-requisitos-para-tramitar-el-cud",
                         "https://www.argentina.gob.ar/noticias/la-andis-establece-criterios-para-el-cud-sin-vencimiento",
                         "https://www.ms.gba.gov.ar/sitios/accesoeinclusion/por-discapacidad"} <= urls)
        self.assertEqual(ld["lastReviewed"], "2026-10-07")

    def test_key_facts_match_the_sources(self):
        html = self._html()
        for fact in ("leyes nacionales 22431 y 24901", "voluntario y gratuito",
                     "cobertura del 100% en las prestaciones de rehabilitación",
                     "cualquier persona mayor de 18 años con tu documento original",
                     "Se genera solo para mayores de 13 años",
                     "Resolución 1654/2024", "Ley 27.711", "Tenés más de 60 años y funciones estables por al menos 5 años",
                     "Tenés dos certificados previos y un perfil constante"):
            self.assertIn(fact, html)

    def test_no_local_article_means_no_local_block_and_no_filler(self):
        html = self._html()
        for local in ("Ostende", "CAPS", "49-1624", "535605", "Coordinación de Discapacidad", "13:30"):
            self.assertNotIn(local, html)
        self.assertNotRegex(html.lower(), r"consult[aá](?:\s+\w+){0,3}\s+(?:en|con) (?:el|la) (?:hospital|municipalidad)")

    def test_links_the_pension_page(self):
        self.assertIn('href="/pension-por-discapacidad"', self._html())

    def test_routed_and_in_sitemap(self):
        from app.patient_web import sitemap
        self.assertTrue(any(u.endswith("/certificado-unico-de-discapacidad") for u, _ in sitemap._urls()))
        with TestClient(app_module.app) as client:
            res = client.get("/certificado-unico-de-discapacidad")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Fuente:", res.text)


class TestCudWithPrivatePack(_Chain):
    """Synthetic local article (the real Pinamar text stays out of the repo)."""
    CHAIN = "global,ar,ar-b,ar-b-pinamar"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        pack = os.path.join(self._tmp.name, "ar-b-pinamar")
        os.makedirs(pack)
        local = _article("pagina-cud-local-demo", "pinamar", [_block("Bloque local CUD")],
                         slug="certificado-unico-de-discapacidad")
        with open(os.path.join(pack, "pack.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "ar-b-pinamar", "level": 4, "parent": "ar-b"}, f)
        with open(os.path.join(pack, "conocimiento.json"), "w", encoding="utf-8") as f:
            json.dump({"articles": [local]}, f)
        with open(os.path.join(pack, "sitio.json"), "w", encoding="utf-8") as f:
            json.dump({"city": "Villa Demo"}, f)
        data_packs.write_inventory(pack)
        super().setUp()
        _env.set_env(self, TURNOS_PRIVATE_PACKS_DIR=self._tmp.name)
        _reset()

    def test_local_block_is_picked_up_and_comes_last(self):
        html = info_pages.render("certificado-unico-de-discapacidad").decode()
        self.assertIn("(CUD) en Villa Demo:", html)
        self.assertIn("Bloque local CUD", html)
        self.assertLess(html.index("El CUD en los trámites de salud de la Provincia"), html.index("Bloque local CUD"))


class TestSaludReproductivaPage(_Chain):
    """/salud-reproductiva: national + provincial blocks from the public packs; the local
    CAPS hours live only in the private pack (never in the repo)."""
    SLUG = "salud-reproductiva"

    def _html(self):
        return info_pages.render(self.SLUG).decode()

    def test_page_renders_national_then_provincial_blocks(self):
        html = self._html()
        for h2 in ("Anticonceptivos en Argentina: qué métodos incluye la ley", "Anticonceptivos con obra social o prepaga",
                   "Tus derechos: acceso desde los 13 años", "Anticoncepción de emergencia",
                   "Línea Salud Sexual: 0 800 222 3444", "Anticonceptivos gratis en la Provincia de Buenos Aires",
                   "Cerca de tu casa: preguntá por consejerías"):
            self.assertIn(f"<h2>{h2}</h2>", html)
        self.assertLess(html.index("Línea Salud Sexual: 0 800"), html.index("Anticonceptivos gratis en la Provincia"))

    def test_h1_is_reproductive_and_city_comes_from_site_identity(self):
        city = site_identity.get("city")
        self.assertTrue(city)
        self.assertIn(f'<h1 class="h1">Salud reproductiva y anticonceptivos gratis en {city}</h1>', self._html())
        real = site_identity.get
        with mock.patch.object(site_identity, "get",
                               side_effect=lambda k, d=None: "Villa Ejemplo" if k == "city" else real(k, d)):
            html = self._html()
        self.assertIn("anticonceptivos gratis en Villa Ejemplo</h1>", html)

    def test_program_name_appears_once_in_the_text(self):
        html = self._html()
        self.assertEqual(html.count("Programa Nacional de Salud Sexual y Reproductiva"), 1)
        body = re.sub(r'<script.*?</script>|<head>.*?</head>', "", html, flags=re.S)
        self.assertEqual(len(re.findall(r"(?i)salud sexual y (?:salud )?reproductiva", body)), 1)

    def test_every_block_has_source_and_check_date_and_jsonld_cites_them(self):
        html = self._html()
        sections = re.findall(r'<section class="campaign-section">(.*?)</section>', html, re.S)
        self.assertGreaterEqual(len(sections), 11)
        for sec in sections:
            self.assertRegex(
                sec, r'Fuente: <a class="stz-link" href="https://[^"]+"[^>]*>[^<]+</a> '
                     r'\(consultado el 07/10/2026\)')
        ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S).group(1))
        urls = {c["url"] for c in ld["citation"]}
        self.assertTrue({"https://www.argentina.gob.ar/salud/sexual/metodos-anticonceptivos",
                         "https://www.argentina.gob.ar/salud/sexual/ahe",
                         "https://www.argentina.gob.ar/salud/saludsexual/lineasaludsexual",
                         "https://www.ms.gba.gov.ar/sitios/aborto/comunidad/metodos-anticonceptivos/"} <= urls)
        self.assertRegex(html, r'<link rel="canonical" href="[^"]+/salud-reproductiva">')

    def test_key_facts_match_the_sources(self):
        html = self._html()
        for fact in ("La Ley 25.673 garantiza el acceso", "Desde los 13 años", "especialmente dentro de las primeras 12 horas",
                     "hasta cinco días después", "farmacias sin necesidad de receta médica",
                     "0800-222-3444", "de lunes a viernes de 9 a 20 h", "todos se entregan gratuitamente",
                     "No es una pastilla abortiva"):
            self.assertIn(fact, html)

    def test_internal_link_to_chip_page_and_unresolved_facts_stay_out(self):
        html = self._html()
        self.assertIn('<a class="stz-link" href="/chip-anticonceptivo">', html)
        self.assertNotIn('href="/chip-anticonceptivo" target', html)
        self.assertNotIn("16 años", html)  # surgical-contraception age differs between sources: not written
        self.assertNotIn("CAPS", html)
        self.assertNotRegex(html.lower(), r"consult[aá](?:\s+\w+){0,3}\s+(?:en|con) (?:el|la) (?:hospital|municipalidad)")

    def test_routed_and_in_sitemap(self):
        from app.patient_web import sitemap
        self.assertTrue(any(u.endswith("/salud-reproductiva") for u, _ in sitemap._urls()))
        with TestClient(app_module.app) as client:
            res = client.get("/salud-reproductiva")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Fuente:", res.text)


class TestSaludReproductivaWithPrivatePack(_Chain):
    """The CAPS blocks come from TURNOS_PRIVATE_PACKS_DIR (synthetic article here)."""
    CHAIN = "global,ar,ar-b,ar-b-pinamar"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        pack = os.path.join(self._tmp.name, "ar-b-pinamar")
        os.makedirs(pack)
        local = _article("pagina-salud-reproductiva-local-demo", "pinamar",
                         [_block("Bloque local reproductiva")], slug="salud-reproductiva")
        with open(os.path.join(pack, "pack.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "ar-b-pinamar", "level": 4, "parent": "ar-b"}, f)
        with open(os.path.join(pack, "conocimiento.json"), "w", encoding="utf-8") as f:
            json.dump({"articles": [local]}, f)
        with open(os.path.join(pack, "sitio.json"), "w", encoding="utf-8") as f:
            json.dump({"city": "Villa Demo"}, f)
        data_packs.write_inventory(pack)
        super().setUp()
        _env.set_env(self, TURNOS_PRIVATE_PACKS_DIR=self._tmp.name)
        _reset()

    def test_local_block_is_picked_up_and_comes_last(self):
        html = info_pages.render("salud-reproductiva").decode()
        self.assertIn("anticonceptivos gratis en Villa Demo</h1>", html)
        self.assertLess(html.index("Cerca de tu casa"), html.index("Bloque local reproductiva"))
