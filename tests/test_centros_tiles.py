"""test_centros_tiles.py — mosaico de centros (tiles) y páginas de detalle.

Correr:  python3 -m unittest test.test_centros_tiles -v   (desde la raíz)

Sin base ni red: opera sobre webdata/centros-salud.json + centros-info.json.
Verifica accent-is-link (una tile = un link, sin texto en color de acento ni
"servicios disponibles"), ui-consistency (mismo payload de tile) y que los datos
investigados no repitan lo que la página ya muestra (dedupe).
"""
import contextlib
import json
import re
import unittest
from unittest.mock import patch
from html.parser import HTMLParser

from app.core import pack_l10n, site_identity
from app.patient_web import (
    site_availability,
    site_base,
    site_catalog,
    site_centers,
    site_page_centers,
    site_schedule,
)
from _packtest import reference_pack_test


@contextlib.contextmanager
def _centro_sintetico_sin_foto():
    """Inyecta un centro sin foto (ya no queda ninguno real: secretaria-salud se
    retiró del directorio porque no toma turnos) para probar la degradación."""
    real_load = site_base._load

    def fake_load(name):
        d = real_load(name)
        if name == "centros-salud.json" and d:
            d = json.loads(json.dumps(d))  # copia: no ensuciar el cache real
            d["centros"].append({
                "slug": "test-sin-foto", "name": "Centro de prueba",
                "address": "Calle Falsa 123",
                "description": "Centro sintético sin foto.",
                "jtbd": "Probar el layout sin foto.",
                "jobs": ["Probar la degradación"],
                "bookable": False,
            })
        return d

    site_base._load = fake_load
    try:
        yield
    finally:
        site_base._load = real_load


class TestApiCentros(unittest.TestCase):
    @reference_pack_test
    def test_foto_adjunta(self):
        by = {c["slug"]: c for c in site_centers._centers_web()}
        self.assertEqual(by["hospital-pinamar"].get("foto"),
                         "/webdata/centro-photos/hospital-pinamar.webp")
        # caps-movil y zoonosis ganaron foto (van municipal / vacunación) 2026-07-10
        # el ?v=N es el cache-buster de Cloudflare (convención del repo) — la
        # aserción compara el path pelado para no romperse en cada re-foto.
        self.assertEqual(by["caps-movil"].get("foto", "").split("?")[0],
                         "/webdata/centro-photos/caps-movil.webp")
        self.assertEqual(by["zoonosis"].get("foto"),
                         "/webdata/centro-photos/zoonosis.webp")
        # la Secretaría de Salud no toma turnos → fuera del directorio (2026-07-10)
        self.assertNotIn("secretaria-salud", by)

    @reference_pack_test
    def test_caps_movil_direccion(self):
        by = {c["slug"]: c for c in site_centers._centers()}
        self.assertEqual(by["caps-movil"].get("address"), "Víctor Hugo y Anspach")

    def test_la_foto_subida_desde_el_panel_se_ve(self):
        # Un centro creado en el panel sólo tiene la foto que se le subió ahí
        # (Pinamar 2026-10-17: Oncología - Hospital de Día salía sin foto en la
        # portada). La subida gana sobre centros-info.json, en la tile y en la ficha.
        import os
        import shutil
        import tempfile
        from app.patient_web import doctor_photos
        c = next(iter(site_centers._centers()))
        slug = c["slug"]
        tmp = tempfile.mkdtemp(prefix="centro-fotos-")
        orig = doctor_photos.CENTER_PHOTOS_DIR
        doctor_photos.CENTER_PHOTOS_DIR = tmp
        try:
            with open(os.path.join(tmp, slug + ".jpg"), "wb") as f:
                f.write(b"\xff\xd8\xff\xe0fake")
            by = {x["slug"]: x for x in site_centers._centers_web()}
            self.assertTrue(by[slug]["foto"].startswith("/center-photo/" + slug + "?v="))
            html = site_page_centers._page_center(slug).decode("utf-8")
            self.assertIn('<figure class="hero-photo">', html)
            self.assertIn("/center-photo/" + slug + "?v=", html)
        finally:
            doctor_photos.CENTER_PHOTOS_DIR = orig
            shutil.rmtree(tmp, ignore_errors=True)

    def test_tile_payload_minimo(self):
        c = next(iter(site_centers._centers()))
        t = site_centers._tile_center(c)
        # Sin maps_url (revisión de la demo 2026-11-15, P35): la tarjeta es UN
        # link a la página del centro; Maps vive en esa página.
        self.assertEqual(set(t), {"slug", "nombre", "direccion", "jtbd", "foto"})


class TestDireccionConFecha(unittest.TestCase):
    """CAPS Móvil: la dirección vale «hasta» un día y, pasado, se oculta sola
    (responsable del producto, 2026-10-29: «hasta qué fecha», no «desde»)."""

    HOY = __import__("datetime").date(2026, 10, 29)

    def _movil(self, hasta):
        return {"slug": "movil-x", "name": "Móvil", "address": "Escuela N°6",
                "location_note": "varía", "location_until": hasta, "maps_url": ""}

    def test_pasado_el_dia_la_direccion_y_el_mapa_se_van(self):
        from app.patient_web import catalog
        fila = catalog.expire_center_locations([self._movil("2026-10-28")], self.HOY)[0]
        self.assertNotIn("address", fila)
        self.assertNotIn("maps_url", fila)
        self.assertNotIn("location_until", fila)
        self.assertEqual(fila["location_note"], "varía")

    def test_el_ultimo_dia_todavia_vale(self):
        from app.patient_web import catalog
        fila = catalog.expire_center_locations([self._movil("2026-10-29")], self.HOY)[0]
        self.assertEqual(fila["address"], "Escuela N°6")

    def test_fecha_ilegible_o_vacia_no_oculta_nada(self):
        from app.patient_web import catalog
        for hasta in ("", "pronto", None):
            fila = catalog.expire_center_locations([self._movil(hasta)], self.HOY)[0]
            self.assertEqual(fila["address"], "Escuela N°6", hasta)

    def test_el_texto_dice_hasta_el_y_el_mapa_no_lleva_la_fecha(self):
        from app.core import i18n
        with patch.object(site_base, "_today_art", return_value=self.HOY), \
                patch.object(site_base, "_load", return_value={"centros": [self._movil("2026-11-27")]}), \
                patch.object(site_base, "_with_overlay", side_effect=lambda t, b: b), \
                patch.object(pack_l10n, "localize", side_effect=lambda t, r: r), \
                patch.object(i18n, "active", return_value="es"):
            fila = site_centers._centers()[0]
        self.assertEqual(fila["address"], "Escuela N°6, hasta el 27 de noviembre")
        self.assertIn("Escuela%20N%C2%B06", fila["maps_url"])
        self.assertNotIn("noviembre", fila["maps_url"])

    def test_la_fecha_queda_fuera_del_link_al_mapa(self):
        """Demo review 2026-11-15, P23: the tile and the center page link the
        place to the map; «hasta el …» is plain text next to it."""
        from app.core import i18n
        with patch.object(site_base, "_today_art", return_value=self.HOY), \
                patch.object(site_base, "_load", return_value={"centros": [self._movil("2026-11-27")]}), \
                patch.object(site_base, "_with_overlay", side_effect=lambda t, b: b), \
                patch.object(pack_l10n, "localize", side_effect=lambda t, r: r), \
                patch.object(i18n, "active", return_value="es"):
            fila = site_centers._centers()[0]
            tile = site_centers._tile_center(fila)
            contacto = site_page_centers._center_contact_html(fila)
        self.assertEqual(tile["direccion"], "Escuela N°6")
        self.assertEqual(tile["direccion_nota"], "Hasta el 27 de noviembre")
        link = re.search(r'<a class="stz-link" href="https://www.google[^"]*"[^>]*>(.*?)</a>(.*?)(<|$)',
                         contacto)
        self.assertEqual(link.group(1), "Escuela N°6")
        self.assertIn("hasta el 27 de noviembre", link.group(2))

    def test_el_punto_final_no_entra_en_la_busqueda_del_mapa(self):
        url = site_centers._maps_url({"address": "Av. Shaw 255, Hospital de Pinamar."})
        self.assertNotIn("Pinamar.", url.replace("%2C", ","))
        self.assertIn("Hospital%20de%20Pinamar", url)
        self.assertNotIn("Pinamar.%2C", url)

    def test_pasado_el_dia_el_centro_sigue_sin_direccion(self):
        with patch.object(site_base, "_today_art", return_value=self.HOY), \
                patch.object(site_base, "_load", return_value={"centros": [self._movil("2026-10-01")]}), \
                patch.object(site_base, "_with_overlay", side_effect=lambda t, b: b), \
                patch.object(pack_l10n, "localize", side_effect=lambda t, r: r):
            fila = site_centers._centers()[0]
        self.assertFalse(fila.get("address"))
        self.assertEqual(site_centers._tile_center(fila).get("maps_url", ""), "")

    def test_el_panel_valida_la_fecha(self):
        from app.patient_web import catalog
        ok, err = catalog._validate_center({"name": "X", "location_until": " 2026-11-27 "}, True)
        self.assertIsNone(err)
        self.assertEqual(ok["location_until"], "2026-11-27")
        ok, err = catalog._validate_center({"name": "X", "location_until": ""}, True)
        self.assertIsNone(err)
        self.assertNotIn("location_until", ok)
        _, err = catalog._validate_center({"name": "X", "location_until": "27/11"}, True)
        self.assertTrue(err)


class TestListaCompactaDeLaPortada(unittest.TestCase):
    """/api/centers feeds the home's compact list: the route to each center,
    the shared-address key and the area (the product owner, 2026-10-25)."""

    def test_ruta_por_texto_o_por_coordenadas(self):
        with patch.object(site_identity, "get",
                          side_effect=lambda k, *a, **kw: {"city": "Pinamar", "province": "Buenos Aires"}.get(k, "")):
            por_texto = site_centers._directions_url({"address": "Guanca 300."})
            self.assertEqual(
                por_texto,
                "https://www.google.com/maps/dir/?api=1&destination=Guanca%20300%2C%20Pinamar%2C%20Buenos%20Aires")
            por_coords = site_centers._directions_url({"address": "Mons y Gaona", "lat": -37.1262603, "lng": -56.9021814})
            self.assertEqual(por_coords,
                             "https://www.google.com/maps/dir/?api=1&destination=-37.126260,-56.902181")

    def test_sin_lugar_fijo_no_hay_ruta(self):
        self.assertEqual(site_centers._directions_url({"address": ""}), "")
        self.assertEqual(site_centers._directions_url({}), "")
        # the mobile unit: an address that moves is not a destination
        self.assertEqual(site_centers._directions_url(
            {"address": "Escuela N°5 en Apolo y Lenguado hasta el 23/10/2026", "location_note": "Unidad móvil"}), "")
        self.assertEqual(site_centers._place_key({"address": "Escuela N°5", "location_note": "Unidad móvil"}), "")

    def test_misma_calle_misma_clave_de_lugar(self):
        a = site_centers._place_key({"address": "Av. Shaw 255"})
        b = site_centers._place_key({"address": "Av. Shaw 255, Hospital Comunitario de Pinamar."})
        c = site_centers._place_key({"address": "av.  SHAW 255 "})
        self.assertEqual(a, "av. shaw 255")
        self.assertEqual({a, b, c}, {a})
        self.assertNotEqual(a, site_centers._place_key({"address": "Intermédanos y Calamares"}))
        self.assertEqual(site_centers._place_key({"address": "Esparza y Pueyrredón, Ostende"}), "esparza y pueyrredon")
        self.assertEqual(site_centers._place_key({"address": ""}), "")

    def test_zona_explicita_gana_y_si_no_se_lee_del_nombre_o_la_direccion(self):
        with patch.object(site_identity, "get",
                          side_effect=lambda k, *a, **kw: {"city": "Pinamar"}.get(k, "")), \
                patch.object(site_centers, "_EXAMPLE_LOCALITIES", ["Ostende", "Valeria del Mar", "Cariló"]):
            z = site_centers._center_locality
            self.assertEqual(z({"name": "CAPS Ostende", "address": "Misiones y Av. del Parque"}), "Ostende")
            self.assertEqual(z({"name": "Centro de Salud Integral", "address": "Esparza y Pueyrredón, Ostende"}), "Ostende")
            self.assertEqual(z({"name": "Servicio de Oncología", "address": "Av. Shaw 255, Hospital Comunitario de Pinamar."}), "Pinamar")
            self.assertEqual(z({"name": "CAPS Valeria del Mar", "address": "Guanca 300"}), "Valeria del Mar")
            # the panel's value wins over what the name says
            self.assertEqual(z({"name": "CAPS Ostende", "address": "x", "locality": "Pinamar"}), "Pinamar")
            # nothing names an area: never guessed
            self.assertEqual(z({"name": "CAPS DiabetPin", "address": "Av. del Parque entre Misiones y Central"}), "")
            self.assertEqual(z({"name": "CAPS Móvil", "address": "Escuela N°5"}), "")

    def test_la_api_trae_ruta_lugar_y_zona_y_hereda_la_del_vecino(self):
        datos = [
            {"slug": "hospital-x", "name": "Hospital X", "address": "Av. Shaw 255", "bookable": True},
            {"slug": "hemo-x", "name": "Hemoterapia", "address": "Av. Shaw 255, Hospital X.", "bookable": True},
            {"slug": "caps-x", "name": "CAPS Ostende", "address": "Calle 1", "bookable": True},
            {"slug": "movil-x", "name": "Móvil", "address": "Escuela", "location_note": "varía", "bookable": True},
        ]
        with patch.object(site_centers, "_centers", return_value=datos), \
                patch.object(site_centers, "_center_pages", return_value={}), \
                patch.object(site_centers, "_centers_info", return_value={}), \
                patch.object(site_centers, "_center_photo", return_value=""), \
                patch.object(site_centers, "_EXAMPLE_LOCALITIES", ["Ostende"]), \
                patch.object(site_identity, "get",
                             side_effect=lambda k, *a, **kw: {"city": "", "main_center": "hospital-x"}.get(k, "")):
            por = {c["slug"]: c for c in site_centers._centers_web()}
        self.assertEqual(por["hospital-x"]["place"], por["hemo-x"]["place"])
        self.assertTrue(por["hospital-x"].get("place_main"))
        self.assertNotIn("place_main", por["hemo-x"])
        self.assertEqual(por["caps-x"]["locality"], "Ostende")
        self.assertEqual(por["hospital-x"]["locality"], "")  # nothing names an area: untitled, not guessed
        self.assertTrue(por["hospital-x"]["directions_url"].startswith("https://www.google.com/maps/dir/?api=1&destination="))
        self.assertEqual(por["movil-x"]["directions_url"], "")
        self.assertNotIn("place", por["movil-x"])

    @reference_pack_test
    def test_los_centros_de_pinamar_caen_en_su_zona(self):
        por = {c["slug"]: c for c in site_centers._centers_web()}
        self.assertEqual(por["hospital-pinamar"]["locality"], "Pinamar")
        self.assertEqual(por["servicio-de-hemoterapia"]["place"], por["hospital-pinamar"]["place"])
        self.assertEqual(por["servicio-de-hemoterapia"]["locality"], "Pinamar")
        self.assertEqual(por["caps-ostende"]["locality"], "Ostende")
        self.assertEqual(por["caps-valeria"]["locality"], "Valeria del Mar")
        self.assertEqual(por["caps-movil"]["directions_url"], "")

    def test_el_panel_valida_coordenadas(self):
        from app.patient_web import catalog
        ok, err = catalog._validate_center({"name": "X", "locality": " Ostende ", "lat": "-37.1262", "lng": "-56.9021"}, True)
        self.assertIsNone(err)
        self.assertEqual((ok["locality"], ok["lat"], ok["lng"]), ("Ostende", -37.1262, -56.9021))
        ok, err = catalog._validate_center({"name": "X", "lat": "", "lng": ""}, True)
        self.assertIsNone(err)
        self.assertNotIn("lat", ok)
        for malo in ({"lat": "-37.1"}, {"lat": "abc", "lng": "1"}, {"lat": "95", "lng": "1"}):
            _, err = catalog._validate_center({"name": "X", **malo}, True)
            self.assertTrue(err, malo)


class TestMapaDeLaPortada(unittest.TestCase):
    """The home's black-and-white map of the centers (the product owner, 2026-11-05): the
    pack's drawing (centros-mapa.svg + .json) and where each center falls."""

    SPEC = {"bbox": [-57.0, -37.2, -56.8, -37.0], "svg": "centros-mapa.svg", "path": __file__,
            "width": 1000.0, "height": 1000.0, "areas": [], "attribution": "© OpenStreetMap",
            "attribution_url": "https://www.openstreetmap.org/copyright",
            "pins": {"norte": {"place": "av. shaw 255", "lat": -37.1, "lng": -56.9}}}
    RUTA = "https://www.google.com/maps/dir/?api=1&destination=x"

    def _punto(self, c):
        return site_centers._map_point({"slug": "norte", "directions_url": self.RUTA, **c}, self.SPEC)

    def test_el_pin_del_paquete_vale_mientras_el_centro_siga_en_su_puerta(self):
        self.assertEqual(self._punto({"address": "Av. Shaw 255, Pinamar"}), (0.5, 0.5))
        # moved in the panel: off the map (the list under it), not at its old door
        self.assertIsNone(self._punto({"address": "Bunge 1200"}))
        # the mobile unit has no fixed place, so no pin applies to it
        self.assertIsNone(self._punto({"address": "Av. Shaw 255", "location_note": "Unidad móvil"}))

    def test_las_coordenadas_del_panel_ganan_al_pin(self):
        self.assertEqual(self._punto({"address": "Bunge 1200", "lat": -37.05, "lng": -56.85}), (0.75, 0.25))

    def test_sin_ruta_o_fuera_del_dibujo_no_hay_punto(self):
        self.assertIsNone(site_centers._map_point({"slug": "norte", "address": "Av. Shaw 255"}, self.SPEC))
        self.assertIsNone(self._punto({"address": "Bunge 1200", "lat": -36.5, "lng": -56.85}))
        self.assertIsNone(self._punto({"address": "Bunge 1200", "lat": -37.05, "lng": -58.0}))

    def test_un_mapa_roto_deja_la_lista(self):
        buenos = {"bbox": [-57.0, -37.2, -56.8, -37.0], "svg": "centros-mapa.svg", "width": 1000, "height": 1000}
        rotos = [
            None,
            {**buenos, "bbox": [-56.8, -37.2, -57.0, -37.0]},
            {**buenos, "bbox": [-57.0, -37.2, -56.8]},
            {**buenos, "svg": "../secreto.svg"},
            {**buenos, "svg": "centros-mapa.png"},
            {**buenos, "width": 0},
            {**buenos, "height": "alto"},
        ]
        with patch.object(site_centers.sitedata, "path", return_value=__file__):
            for spec in rotos:
                with patch.object(site_base, "_load", return_value=spec):
                    self.assertIsNone(site_centers._map_spec(), spec)
            with patch.object(site_base, "_load", return_value=buenos):
                self.assertEqual(site_centers._map_spec()["svg"], "centros-mapa.svg")
        with patch.object(site_centers.sitedata, "path", return_value=__file__ + ".no-existe"), \
                patch.object(site_base, "_load", return_value=buenos):
            self.assertIsNone(site_centers._map_spec())

    def test_la_api_trae_el_mapa_solo_si_algun_centro_cae_en_el(self):
        spec = {**self.SPEC, "areas": [{"name": "Norte", "lat": -37.1, "lng": -56.9},
                                       {"name": "Lejos", "lat": -36.0, "lng": -56.9}, {"lat": -37.1}]}
        with patch.object(site_centers, "_map_spec", return_value=spec):
            meta = site_centers._centers_map_meta([{"slug": "norte", "map_x": 0.5, "map_y": 0.5}])
            self.assertIsNone(site_centers._centers_map_meta([{"slug": "norte"}]))
        self.assertRegex(meta["svg"], r"^/webdata/centros-mapa\.svg\?v=[0-9a-f]{12}$")
        self.assertEqual(meta["areas"], [{"name": "Norte", "x": 0.5, "y": 0.5}])
        self.assertEqual(meta["attribution_url"], "https://www.openstreetmap.org/copyright")
        with patch.object(site_centers, "_map_spec", return_value=None):
            self.assertNotIn("mapa", site_centers._centers_api())

    def test_el_pueblo_de_ejemplo_tiene_mapa_y_cada_centro_con_ruta_cae_en_el(self):
        api = site_centers._centers_api()
        self.assertIn("mapa", api)
        con_ruta = [c for c in api["centros"] if c.get("directions_url")]
        self.assertTrue(con_ruta)
        for c in con_ruta:
            self.assertTrue(0 <= c.get("map_x", -1) <= 1 and 0 <= c.get("map_y", -1) <= 1, c["slug"])


class TestPageCentros(unittest.TestCase):
    def setUp(self):
        self.html = site_page_centers._page_centers().decode("utf-8")
        # Cuerpo = lo que va después del ÚLTIMO <style> del head (los bloques
        # de estilo sí traen reglas con --accent). rsplit y no split: el head
        # tiene más de un <style> — las @font-face self-hosted van inline
        # antes del CSS de la página (theme.fonts).
        self.body = self.html.rsplit("</style>", 1)[1]

    def test_usa_tiles(self):
        self.assertIn("data-tiles", self.html)
        from app.core import static_fingerprint
        self.assertIn(static_fingerprint.url("platform/tiles.mjs"), self.html)
        self.assertIn('type="module"', self.html)
        self.assertNotIn("/tiles.js", self.html)
        self.assertNotIn("ccard", self.html)

    def test_sin_texto_muerto_en_acento(self):
        # el viejo contador "N servicios disponibles" en color de acento está prohibido
        self.assertNotIn("servicios disponibles", self.html)
        # el cuerpo no pinta texto con el color de acento (las tiles no llevan acento)
        self.assertNotIn("color:var(--accent)", self.body)

    @reference_pack_test
    def test_sin_anchors_anidados_en_la_tile(self):
        # las tiles se dibujan client-side desde este JSON; el payload no lleva <a>
        self.assertNotIn("<a", self.html.split("data-tiles", 1)[1].split("</script>", 1)[0])
        # y el payload incluye el slug para construir el href /center/<slug>
        self.assertIn("hospital-pinamar", self.html)


class TestPageCentroDetalle(unittest.TestCase):
    def test_center_photo_links_have_names_without_javascript(self):
        class PhotoNames(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_photo = False
                self.names = []

            def handle_starttag(self, tag, attrs):
                values = dict(attrs)
                if tag == "a":
                    self.in_photo = "data-photozoom" in values
                if tag == "img" and self.in_photo:
                    self.names.append(values.get("alt", ""))

            def handle_endtag(self, tag):
                if tag == "a":
                    self.in_photo = False

        center = next(iter(site_centers._centers()))
        slug, name = center["slug"], center["name"]
        with patch.object(site_centers, "_centers_info", return_value={
                slug: {"photo": "/webdata/centro-photos/hero.webp"}}), \
                patch.object(site_centers, "_centers_gallery", return_value={
                    slug: [{"src": "/webdata/centro-photos/gallery.webp"}]}):
            parser = PhotoNames()
            parser.feed(site_page_centers._page_center(slug).decode("utf-8"))
        self.assertTrue(name)
        self.assertEqual(parser.names, [name, name])

    def _facts(self, html):
        m = re.search(r'<ul class="facts">(.*?)</ul>', html, re.S)
        return [re.sub(r"<[^>]+>", "", x) for x in re.findall(r"<li>(.*?)</li>", m.group(1))] if m else []

    @reference_pack_test
    def test_hero_sin_credito(self):
        # la foto se muestra sin línea de crédito/fuente en pantalla (2026-07-10);
        # la fuente queda documentada en centros-info.json
        html = site_page_centers._page_center("hospital-pinamar").decode("utf-8")
        self.assertIn('<figure class="hero-photo">', html)
        self.assertIn("/webdata/centro-photos/hospital-pinamar.webp", html)
        self.assertNotIn("Foto:", html)
        self.assertNotIn("Fotos:", html)

    def test_sin_hero_si_no_hay_foto(self):
        # el <style> compartido trae la regla .hero-photo; lo que no debe existir
        # es el elemento <figure> del hero cuando el centro no tiene foto
        with _centro_sintetico_sin_foto():
            html = site_page_centers._page_center("test-sin-foto").decode("utf-8")
        self.assertNotIn('<figure class="hero-photo">', html)

    @reference_pack_test
    def test_datos_deduplicados(self):
        html = site_page_centers._page_center("hospital-pinamar").decode("utf-8")
        facts = self._facts(html)
        # "Guardia ... 24 horas" ya está en el lede → NO se repite en los datos
        self.assertFalse(any("Guardia" in f and "24" in f for f in facts), facts)
        # los hechos únicos sí aparecen
        self.assertTrue(any("maternidad" in f for f in facts), facts)

    @reference_pack_test
    def test_datos_unicos_no_se_pierden(self):
        # el horario específico de pediatría no está en el lede/jobs → se conserva
        facts = self._facts(site_page_centers._page_center("caps-ostende").decode("utf-8"))
        self.assertTrue(any("Pediatría" in f for f in facts), facts)

    @reference_pack_test
    def test_mas_en_la_lista_y_sin_fuentes(self):
        # el ex-"más información" es un renglón más de la lista visible (un
        # pliegue para un renglón ocupa más que el renglón) y las fuentes no se
        # muestran: somos parte del mismo portal que citábamos (responsable del producto, 2026-07-16)
        html = site_page_centers._page_center("hospital-pinamar").decode("utf-8")
        body = html.split("</style>", 1)[1]
        facts = self._facts(html)
        self.assertTrue(any("Hospital Comunitario de Pinamar" in f for f in facts), facts)
        self.assertNotIn("ci-src", body)
        self.assertNotIn(">Fuente", body)

    @reference_pack_test
    def test_sin_rating_de_google(self):
        # el rating de Google se retiró (2026-07-10: reseñas flojas, no se
        # muestran); en su lugar, dirección + teléfono directo bajo la lede.
        html = site_page_centers._page_center("hospital-pinamar").decode("utf-8")
        body = html.split("</style>", 1)[1]
        self.assertNotIn("rating-line", body)
        self.assertNotIn("reseñas en Google", body)

    @reference_pack_test
    def test_contacto_bajo_la_lede(self):
        # dirección (link a Maps) y teléfono (link tel:) juntos, arriba — en la
        # columna de texto del header, antes del <details> y de la tabla.
        html = site_page_centers._page_center("hospital-pinamar").decode("utf-8")
        body = html.split("</style>", 1)[1]
        m = re.search(r'<p class="c-contact">(.*?)</p>', body, re.S)
        self.assertIsNotNone(m)
        self.assertIn("Av. Shaw 255", m.group(1))
        self.assertIn('href="tel:', m.group(1))
        self.assertLess(body.index("c-contact"), body.index("<details"))
        # y no se repiten más abajo: el <details> ya no lleva teléfono
        det = re.search(r'more-body">(.*?)</details>', body, re.S).group(1)
        self.assertNotIn("tel:", det)

    @reference_pack_test
    def test_tres_pliegues_con_nombre_propio(self):
        # TRES <details> separados arriba, cada uno con su nombre en el summary
        # (responsable del producto, 2026-07-16 — nada de un solo "Fotos, consejos… y más información"):
        # Fotos / Lo que dicen los vecinos / Consejos de vecinos.
        html = site_page_centers._page_center("hospital-pinamar").decode("utf-8")
        body = html.split("</style>", 1)[1]
        summaries = re.findall(r"<summary>(.*?)</summary>", body, re.S)
        self.assertEqual(summaries,
                         ["Fotos", "Lo que dicen los vecinos", "Consejos de vecinos"])
        dets = re.findall(r'more-body">(.*?)</details>', body, re.S)
        self.assertIn('class="gal-grid"', dets[0])
        self.assertIn("Fuimos a la guardia de pediatría", dets[1])
        self.assertIn("triage", dets[2])
        self.assertNotIn("q-meta", body)  # sin "★ n" por cita
        # ni card ni barra de acento en las citas (ui-rules 2026-06-28)
        self.assertNotIn("border-left", html)
        self.assertNotIn('<blockquote', html)
        # orden: los pliegues van ARRIBA, antes de las tablas
        self.assertLess(body.index("Lo que dicen los vecinos"), body.index("data-listado"))

    @reference_pack_test
    def test_email_en_el_contacto_y_vacunatorio_una_vez(self):
        # el email vive en la línea de contacto bajo la lede (no bajo un pliegue)
        html = site_page_centers._page_center("caps-pinamar").decode("utf-8")
        body = html.split("</style>", 1)[1]
        m = re.search(r'<p class="c-contact">(.*?)</p>', body, re.S)
        self.assertIn('href="mailto:', m.group(1))
        # y "vacunarte/vacunatorio" aparece UNA vez en la lista (responsable del producto, 2026-07-16)
        facts = self._facts(html)
        self.assertEqual(sum("vacun" in f.lower() for f in facts), 1, facts)
        # el edificio nuevo vive en la lede, no en la lista
        self.assertIn("edificio nuevo", re.search(r'<p class="lede">(.*?)</p>', body, re.S).group(1))
        self.assertFalse(any("Edificio nuevo" in f for f in facts), facts)

    @reference_pack_test
    def test_hero_abre_el_visor_con_la_galeria(self):
        # click en la foto hero → photo-zoom.mjs con TODAS las fotos del centro:
        # hero y miniaturas comparten data-photozoom-group (responsable del producto, 2026-07-16)
        html = site_page_centers._page_center("hospital-pinamar").decode("utf-8")
        body = html.split("</style>", 1)[1]
        hero = re.search(r'<figure class="hero-photo">(.*?)</figure>', body, re.S).group(1)
        self.assertIn("data-photozoom", hero)
        self.assertIn('data-photozoom-group="centro-hospital-pinamar"', hero)
        from app.core import static_fingerprint
        self.assertIn(static_fingerprint.url("platform/photo-zoom.mjs"), body)

    @reference_pack_test
    def test_jobs_y_datos_una_sola_lista(self):
        # jobs (qué hacés ahí) + datos verificados = UNA lista, una columna
        # (responsable del producto, 2026-07-16): no queda <ul class="jobs"> aparte
        html = site_page_centers._page_center("hospital-pinamar").decode("utf-8")
        body = html.split("</style>", 1)[1]
        self.assertNotIn('class="jobs"', body)
        facts = self._facts(html)
        # un job y un dato conviven en la misma lista
        self.assertTrue(any("maternidad" in f for f in facts), facts)
        self.assertTrue(any(f in (site_centers._centers_by_slug()["hospital-pinamar"].get("jobs") or [])
                            for f in facts), facts)

    @reference_pack_test
    def test_sin_resena_sin_rating_sin_crash(self):
        # caps-movil no está en centros-resenas.json: degrada en silencio, sin
        # línea de rating y sin sección de reseñas, sin romper la página.
        html = site_page_centers._page_center("caps-movil").decode("utf-8")
        body = html.split("</style>", 1)[1]
        self.assertNotIn("rating-line", body)
        self.assertNotIn("Lo que dicen los vecinos", html)
        self.assertNotIn("Consejos de vecinos", html)

    @reference_pack_test
    def test_galeria_ausente_no_rompe(self):
        # webdata/centros-galeria.json puede no existir todavía (otro workstream):
        # _load ya degrada a None -> {}, la sección de fotos simplemente no aparece.
        real_load = site_base._load

        def fake_load(name):
            if name == "centros-galeria.json":
                return None
            return real_load(name)

        site_base._load = fake_load
        try:
            html = site_page_centers._page_center("hospital-pinamar").decode("utf-8")
        finally:
            site_base._load = real_load
        body = html.split("</style>", 1)[1]  # .gal-grid es una regla CSS siempre presente
        self.assertNotIn('class="gal-grid"', body)
        # el resto de la página (reseñas, contacto) sigue intacto
        self.assertIn("Lo que dicen los vecinos", html)

    @reference_pack_test
    def test_centro_head_dos_columnas_con_foto(self):
        html = site_page_centers._page_center("hospital-pinamar").decode("utf-8")
        body = html.split("</style>", 1)[1]
        self.assertIn('<div class="centro-head">', body)
        self.assertIn('<div class="ch-top">', body)
        self.assertIn('<div class="ch-photo">', body)
        self.assertIn('<div class="ch-bottom">', body)

    def test_centro_head_sin_grid_sin_foto(self):
        # centro sin foto -> sin wrapper de grid (layout plano)
        with _centro_sintetico_sin_foto():
            html = site_page_centers._page_center("test-sin-foto").decode("utf-8")
        body = html.split("</style>", 1)[1]
        self.assertNotIn('<div class="centro-head">', body)


class TestProximoTurno(unittest.TestCase):
    """Celda 'Próximo turno' con fecha real: `v` amigable + dia/hora crudos —
    listing.js color-codea la espera con ellos (verde/ámbar/gris)."""

    def test_proximo_from_lleva_dia_hora_y_v_amigable(self):
        px = site_availability._next_from(None, ("2026-07-14", "14:40"))
        self.assertEqual(px["kind"], "turno")
        self.assertEqual((px["dia"], px["hora"]), ("2026-07-14", "14:40"))
        self.assertEqual(px["v"], site_schedule._friendly_date("2026-07-14", "14:40"))
        self.assertTrue(px["sort"].startswith("02026-07-14"))
        # los estados sin fecha no llevan dia (el cliente muestra texto)
        self.assertNotIn("dia", site_availability._next_from("walkin", None))

    @reference_pack_test
    def test_walkin_mensual_cae_al_cartel_no_a_veni_en_el_dia(self):
        # 'El primer día hábil de cada mes' NO es "Vení en el día": sin JS el
        # fallback honesto es el cartel; con JS hours.mjs pone la fecha
        # concreta de la próxima ocurrencia (responsable del producto, 2026-07-10).
        s = next(x for x in site_catalog._services() if x["id"] == "psicologia-admision-valeria")
        px = site_availability._service_next(s)
        self.assertEqual(px["kind"], "walkin")
        self.assertEqual(px["v"], s["horario"])
        self.assertEqual(px["horario"], s["horario"])
        self.assertNotIn("dia", px)

    def test_fecha_amigable_absoluta_y_relativa(self):
        import datetime
        hoy = datetime.date(2026, 7, 10)
        self.assertEqual(site_schedule._friendly_date("2026-07-10", "18:20", hoy), "Hoy, 18:20")
        self.assertEqual(site_schedule._friendly_date("2026-07-11", "09:00", hoy), "Mañana, sábado 11, 09:00")
        self.assertEqual(site_schedule._friendly_date("2026-07-14", "14:40", hoy),
                         "Martes 14 de julio, 14:40")


class TestDedupeFacts(unittest.TestCase):
    def test_descarta_solo_lo_repetido(self):
        corpus = "Guardia las 24 horas, internación y especialistas"
        datos = ["Guardia de emergencias las 24 horas",
                 "Sala de maternidad propia desde diciembre de 2024"]
        out = site_catalog._dedupe_facts(datos, corpus)
        self.assertEqual(out, ["Sala de maternidad propia desde diciembre de 2024"])


if __name__ == "__main__":
    unittest.main()
