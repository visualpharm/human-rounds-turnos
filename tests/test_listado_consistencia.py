"""test_listado_consistencia.py — canonical column-registry consistency
(site_listing._COL_PRESETS/_COL_ORDER/_cols) across every patient-site
listing surface, clean-title linting for webdata/servicios.json, and the exact
invariant that first-party JS/CSS never uses a manual `?v=` URL.

Run: python3 -m unittest discover -s tests -p "test_listado_consistencia.py"

No database or network: operates on webdata/*.json and renders patient_web/site_*.py
pages (the same pattern as test_estudios_tabla.py: extract the JSON from
<script class="lst-data">).
"""
import json
import os
import re
import unittest

from app.core import i18n
from app.patient_web import (
    site_availability,
    site_base,
    site_centers,
    site_listing,
    site_page_centers,
    site_page_doctors,
    site_studies,
)
from app.core import sitedata
from _packtest import reference_pack_case, reference_pack_test

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)


def _payloads(html):
    """Todos los <script type="application/json" class="lst-data">…</script> de
    una página (puede haber más de uno: médicos + servicios en /center/<slug>)."""
    out = []
    for raw in re.findall(
        r'<script type="application/json" class="lst-data">(.*?)</script>', html, re.S
    ):
        out.append(json.loads(raw.replace("\\u003c", "<")))
    return out


def _col_name(col):
    """Encuentra a qué clave del registro (_COL_PRESETS) corresponde una columna
    ya expandida, matcheando tipo+dato+label (una columna de una sola clave del
    registro nunca cambia esos tres campos, sólo hideSm/right puntuales)."""
    for name, spec in site_listing._COL_PRESETS.items():
        if col.get("t") == spec["t"] and col.get("key") == spec["key"] and col.get("label") == spec["label"]:
            return name
    return None


class TestOrdenCanonico(unittest.TestCase):
    """Cada página que usa el registro (_cols) debe emitir sus columnas en el
    orden canónico entidad-primero (responsable del producto, 2026-07-12 — revoca la columna
    "Turno" separada): entidad · contexto · centro · Orden · Próxima
    disponibilidad — sin importar en qué orden se pidieron las claves."""

    _ENTIDADES = ("servicio", "estudio", "medico")

    def _check(self, html, contexto):
        payloads = _payloads(html)
        self.assertTrue(payloads, f"{contexto}: no se encontró ningún listado")
        for p in payloads:
            cols = p["cols"]
            names = [_col_name(c) for c in cols]
            self.assertNotIn(None, names, f"{contexto}: columna no trazable al registro: {cols}")
            ranks = [site_listing._COL_ORDER.index(n) for n in names]
            self.assertEqual(ranks, sorted(ranks),
                             f"{contexto}: columnas fuera de orden canónico: {names}")
            # entidad-primero: si hay entidad, va en la PRIMERA columna
            ent = [n for n in names if n in self._ENTIDADES]
            if ent:
                self.assertEqual(names[0], ent[0],
                                 f"{contexto}: la entidad no es la primera columna: {names}")
            # orden + próxima disponibilidad siempre juntas y en ese orden
            if "orden" in names and "proximo" in names:
                self.assertEqual(names.index("orden") + 1, names.index("proximo"),
                                 f"{contexto}: orden y próxima no están juntas: {names}")
            # la fusión: no existe columna "go" ni "turno" en ninguna tabla
            self.assertNotIn("go", [c.get("t") for c in cols],
                             f"{contexto}: sobrevive una columna 'go': {cols}")
            self.assertNotIn("turno", names, f"{contexto}: sobrevive la columna 'turno': {names}")

    def test_medicos(self):
        html = site_page_doctors._page_doctors().decode("utf-8")
        self._check(html, "/doctors")

    @reference_pack_test
    def test_centro_con_medicos_y_servicios(self):
        html = site_page_centers._page_center("caps-ostende").decode("utf-8")
        self._check(html, "/center/caps-ostende")

    def test_cluster_nombres_distintos(self):
        # "clinica": Medicina general + Trabajo Social -> variante con columna Servicio
        html = site_page_doctors._page_cluster("clinica").decode("utf-8")
        self._check(html, "/d/clinica")

    def test_cluster_un_solo_nombre(self):
        # "cardiologia": un solo nombre de servicio en varios centros
        html = site_page_doctors._page_cluster("cardiologia").decode("utf-8")
        self._check(html, "/d/cardiologia")

    def test_cluster_diagnostico_estudios(self):
        html = site_page_doctors._page_cluster("diagnostico").decode("utf-8")
        self._check(html, "/d/diagnostico")

    @reference_pack_test
    def test_pagina_estudio(self):
        slug = site_base._slug("Extracciones de laboratorio")
        html = site_studies._page_study("diagnostico", slug).decode("utf-8")
        self._check(html, "/study/diagnostico/" + slug)


class TestListadoEnColumna(unittest.TestCase):
    """Demo check 07.11: «Quién atiende» on a service page began at x≈30 under
    a column that begins at x≈230. Since review 2026-11-15 no listing breaks
    out of the column; the `lst-in-column` marker on a single item's page
    stays as the hook that tells the two kinds of block apart."""

    def test_bloque(self):
        en_columna = site_listing._listing_block("x", [], [], None, in_column=True)
        suelto = site_listing._listing_block("x", [], [], None)
        self.assertIn('<div class="sec lst-in-column" data-listado id="x">', en_columna)
        self.assertIn('<div class="sec" data-listado id="x">', suelto)

    def test_catalogo_sale_de_la_columna(self):
        html = site_page_doctors._page_doctors().decode("utf-8")
        self.assertIn('class="sec" data-listado', html)
        # Look at class attributes only: CSS or comments may name the class.
        self.assertIsNone(re.search(r"""class=["'][^"']*(?<![\w-])lst-in-column(?![\w-])""", html))

    @reference_pack_test
    def test_pagina_estudio_queda_en_la_columna(self):
        slug = site_base._slug("Extracciones de laboratorio")
        html = site_studies._page_study("diagnostico", slug).decode("utf-8")
        self.assertIn('class="sec lst-in-column" data-listado', html)


class TestRankingPopularidad(unittest.TestCase):
    """Ranking por popularidad (horas semanales) en las páginas de listado
    (responsable del producto, 2026-07-14). Los CATÁLOGOS de entidades rankean por popularidad
    (`opts.rank == "popularidad"` + `horas` en cada fila); las superficies de
    "elegí el centro que te atiende antes" (estudio-centros, cluster-centros)
    NO rankean — ahí manda la próxima disponibilidad (patient-flow regla 7)."""

    def _mounts(self, html):
        return {p.get("id") or i: p for i, p in enumerate(_payloads(html))}

    def _mount_por_columnas(self, html, entidad):
        """El primer payload cuya primera columna es la entidad dada."""
        for p in _payloads(html):
            names = [_col_name(c) for c in p["cols"]]
            if names and names[0] == entidad:
                return p
        return None

    def _asserta_rankeado(self, p, contexto):
        self.assertIsNotNone(p, f"{contexto}: no se encontró el listado")
        self.assertEqual(p["options"].get("rank"), "popularidad",
                         f"{contexto}: debería rankear por popularidad")
        for r in p["rows"]:
            self.assertIn("horas", r, f"{contexto}: fila sin 'horas' para rankear: {r}")
            self.assertIsInstance(r["horas"], (int, float),
                                  f"{contexto}: 'horas' no es numérico: {r['horas']}")

    def _asserta_primero_el_mas_pronto(self, p, contexto):
        """Demo review 2026-11-15, P20: the doctors' lists read soonest first,
        then by name — no popularity rank (it read as no order at all)."""
        self.assertIsNotNone(p, f"{contexto}: no se encontró el listado")
        self.assertNotEqual(p["options"].get("rank"), "popularidad", contexto)
        claves = [(str((r.get("proximo") or {}).get("sort") or "9")) for r in p["rows"]]
        self.assertEqual(claves, sorted(claves), f"{contexto}: no va el más pronto primero")
        for a_, b_ in zip(p["rows"], p["rows"][1:]):
            if (a_.get("proximo") or {}).get("sort") == (b_.get("proximo") or {}).get("sort"):
                self.assertLessEqual(a_["nombre"], b_["nombre"], contexto)

    def test_medicos_catalogo_va_por_disponibilidad(self):
        self._asserta_primero_el_mas_pronto(
            self._mount_por_columnas(site_page_doctors._page_doctors().decode(), "medico"),
            "/doctors")

    def test_estudios_catalogo_rankea(self):
        self._asserta_rankeado(
            self._mount_por_columnas(site_page_doctors._page_cluster("diagnostico").decode(), "estudio"),
            "/d/diagnostico (estudios)")

    @reference_pack_test
    def test_centro_servicios_y_medicos_rankean(self):
        html = site_page_centers._page_center("caps-ostende").decode()
        self._asserta_rankeado(self._mount_por_columnas(html, "servicio"),
                               "/center/caps-ostende (servicios)")
        self._asserta_primero_el_mas_pronto(self._mount_por_columnas(html, "medico"),
                                            "/center/caps-ostende (médicos)")

    def test_centro_medicos_va_por_disponibilidad(self):
        for slug in list(site_centers._centers_by_slug())[:3]:
            p = self._mount_por_columnas(site_page_centers._page_center(slug).decode(), "medico")
            if p:
                self._asserta_primero_el_mas_pronto(p, f"/center/{slug} (médicos)")

    def test_cluster_medicos_va_por_disponibilidad(self):
        self._asserta_primero_el_mas_pronto(
            self._mount_por_columnas(site_page_doctors._page_cluster("cardiologia").decode(), "medico"),
            "/d/cardiologia (médicos)")

    @reference_pack_test
    def test_estudio_centros_NO_rankea(self):
        # un solo estudio en varios centros → se ordena por quién atiende antes,
        # nunca por popularidad.
        slug = site_base._slug("Extracciones de laboratorio")
        for p in _payloads(site_studies._page_study("diagnostico", slug).decode()):
            self.assertNotEqual(p["options"].get("rank"), "popularidad",
                                "estudio-centros no debe rankear por popularidad")

    def test_cluster_centros_NO_rankea(self):
        # misma especialidad en varios centros (elegí dónde) → por espera, no popularidad.
        for p in _payloads(site_page_doctors._page_cluster("clinica").decode()):
            names = [_col_name(c) for c in p["cols"]]
            if names and names[0] != "medico":   # el bloque de centros, no el de médicos
                self.assertNotEqual(p["options"].get("rank"), "popularidad",
                                    "cluster-centros no debe rankear por popularidad")


class TestHrefsDeEntidad(unittest.TestCase):
    """Toda fila con columna 'centro' (link) trae centro_href; toda fila con
    columna 'especialidad' (link) trae esp_href — nunca una celda linkeable sin
    su destino (accent-is-link: un link, un href real)."""

    def _rows_with(self, html, col_name):
        out = []
        for p in _payloads(html):
            names = [_col_name(c) for c in p["cols"]]
            if col_name in names:
                out.extend(p["rows"])
        return out

    def test_centro_href_en_medicos(self):
        html = site_page_doctors._page_doctors().decode("utf-8")
        # /doctors no lista columna centro hoy, pero /d/cardiologia y
        # /center/<slug> médicos sí — la fila SIEMPRE trae el dato aunque la
        # tabla no lo muestre (una sola fuente de filas, _doctor_listing_row).
        rows = json.loads(
            re.search(r'<script type="application/json" class="lst-data">(.*?)</script>',
                      html, re.S).group(1).replace("\\u003c", "<")
        )["rows"]
        self.assertTrue(rows)
        for r in rows:
            self.assertIn("centro_href", r)

    def test_centro_href_en_cluster_medicos(self):
        html = site_page_doctors._page_cluster("cardiologia").decode("utf-8")
        rows = self._rows_with(html, "centro")
        self.assertTrue(rows, "no se encontró la tabla de médicos con columna centro")
        for r in rows:
            if r.get("centro"):
                self.assertTrue(r.get("centro_href", "").startswith("/center/"), r)

    def test_esp_href_en_medico_listado_row(self):
        rows = site_listing._doctors_enriched()
        self.assertTrue(rows)
        for r in rows:
            if r.get("especialidad"):
                self.assertTrue(r.get("esp_href", "").startswith("/d/"), r)


class TestLabelsCanonicos(unittest.TestCase):
    def test_labels(self):
        esperado = {
            "estado": "Estado",
            "proximo": "Próxima disponibilidad",
            "servicio": "Servicio",
            "estudio": "Estudio",
            "medico": "Médico",
            "especialidad": "Especialidad",
            "area": "Área",
            "centro": "Centro",
            "lugar": "Centro",
            "orden": "Orden",
        }
        self.assertEqual(set(esperado), set(site_listing._COL_PRESETS))  # "go"/"turno" murieron
        for name, label in esperado.items():
            self.assertEqual(site_listing._COL_PRESETS[name]["label"], label, name)

    def test_proximo_lleva_href_y_more(self):
        # la pastilla de "Próxima disponibilidad" ES el botón (responsable del producto, 2026-07-12)
        self.assertEqual(site_listing._COL_PRESETS["proximo"]["href"], "turno_href")
        self.assertEqual(site_listing._COL_PRESETS["proximo"]["more"], "info_href")


class TestColumnasIdioma(unittest.TestCase):
    """Bug fijado 2026-08-04: en la página de un centro, la tabla de médicos
    quedaba en español SIEMPRE (_DOCTORS_COLS se congelaba en el idioma
    canónico al importar el módulo, antes de que hubiera un request
    bindeado), mientras la de servicios (armada con _cols() adentro del
    handler) sí respetaba el idioma del visitante. _cols()/_centro_medicos_cols()
    ahora resuelven contra i18n.active() en el momento del render, así que
    alcanza con bindear un idioma distinto y ver que TODAS las tablas lo
    reflejan por igual."""

    def _en(self, fn):
        token = i18n._ACTIVE.set("en")
        try:
            return fn()
        finally:
            i18n._ACTIVE.reset(token)

    def test_medicos_de_centro_en_ingles(self):
        # antes del fix: quedaba "Médico"/"Orden" pase lo que pase.
        cols = self._en(site_listing._centro_medicos_cols)
        labels = {c["t"]: c["label"] for c in cols}
        self.assertEqual(labels["orden"], "Referral")
        self.assertIn("Doctor", [c["label"] for c in cols])

    def test_medicos_de_centro_en_espanol_por_defecto(self):
        # sin idioma bindeado (el caso normal en estos tests) sigue en
        # español, byte a byte igual que siempre.
        cols = site_listing._centro_medicos_cols()
        labels = {c["t"]: c["label"] for c in cols}
        self.assertEqual(labels["orden"], "Orden")
        self.assertIn("Médico", [c["label"] for c in cols])

    def test_servicios_de_centro_en_ingles(self):
        cols = self._en(lambda: site_listing._cols("servicio", "area", "proximo", "orden"))
        labels = {c["t"]: c["label"] for c in cols}
        self.assertEqual(labels["orden"], "Referral")
        self.assertIn("Service", [c["label"] for c in cols])

    def test_estudios_de_cluster_en_ingles(self):
        # mismo defecto, distinta víctima (encontrado 2026-08-05): _STUDIES_COLS
        # era un dict a nivel de módulo, congelado en castellano al importar
        # patient_web/site_*.py — la tabla de /d/diagnostico quedaba en español para
        # TODO visitante sin importar su idioma. _studies_cols() ahora se arma
        # adentro de _studies_listing(), como _centro_medicos_cols().
        cols = self._en(site_studies._studies_cols)
        labels = {c["t"]: c["label"] for c in cols}
        self.assertEqual(labels["orden"], "Referral")
        self.assertIn("Study", [c["label"] for c in cols])

    def test_estudios_de_cluster_en_espanol_por_defecto(self):
        cols = site_studies._studies_cols()
        labels = {c["t"]: c["label"] for c in cols}
        self.assertEqual(labels["orden"], "Orden")
        self.assertIn("Estudio", [c["label"] for c in cols])

    def test_pagina_cluster_diagnostico_en_ingles(self):
        # prueba end-to-end del render real de /d/diagnostico, no sólo la
        # función de columnas — así queda cubierto el camino completo que
        # un visitante en inglés efectivamente recorre.
        html = self._en(lambda: site_page_doctors._page_cluster("diagnostico").decode("utf-8"))
        cols_rendered = [c.get("label") for p in _payloads(html) for c in (p.get("cols") or [])]
        self.assertIn("Study", cols_rendered)
        self.assertNotIn("Estudio", cols_rendered)

    def test_pagina_cluster_diagnostico_en_espanol_por_defecto(self):
        html = site_page_doctors._page_cluster("diagnostico").decode("utf-8")
        cols_rendered = [c.get("label") for p in _payloads(html) for c in (p.get("cols") or [])]
        self.assertIn("Estudio", cols_rendered)

    def _facet_labels(self, html, key):
        out = []
        for p in _payloads(html):
            for f in (p.get("options") or {}).get("filters") or []:
                if f.get("key") == key:
                    out.append(f.get("label"))
        return out

    @reference_pack_test
    def test_facet_area_en_ingles(self):
        html = self._en(lambda: site_page_centers._page_center("caps-ostende").decode("utf-8"))
        self.assertIn("Area", self._facet_labels(html, "area"))

    @reference_pack_test
    def test_facet_area_en_espanol_por_defecto(self):
        html = site_page_centers._page_center("caps-ostende").decode("utf-8")
        self.assertIn("Área", self._facet_labels(html, "area"))

    def test_facet_especialidad_doctores_en_ingles(self):
        html = self._en(lambda: site_page_doctors._page_doctors().decode("utf-8"))
        self.assertIn("Specialty", self._facet_labels(html, "especialidad"))

    def test_facet_especialidad_doctores_en_espanol_por_defecto(self):
        html = site_page_doctors._page_doctors().decode("utf-8")
        self.assertIn("Especialidad", self._facet_labels(html, "especialidad"))

    @reference_pack_test
    def test_titulo_servicios_en_ingles(self):
        # caps-ostende tiene médicos Y servicios: el título "Servicios" del
        # segundo bloque sólo aparece cuando hay ambos (titulo_svs).
        html = self._en(lambda: site_page_centers._page_center("caps-ostende").decode("utf-8"))
        titulos = [(p.get("options") or {}).get("title") for p in _payloads(html)]
        self.assertIn("Services", titulos)

    @reference_pack_test
    def test_titulo_servicios_en_espanol_por_defecto(self):
        html = site_page_centers._page_center("caps-ostende").decode("utf-8")
        titulos = [(p.get("options") or {}).get("title") for p in _payloads(html)]
        self.assertIn("Servicios", titulos)


@reference_pack_case
class TestSedeEnLugar(unittest.TestCase):
    """CAPS Ostende tiene dos filas 'Medicina general' — se distinguen por la
    columna condicional 'lugar' (sede si existe, si no el centro)."""

    def test_columna_lugar_aparece_por_sede(self):
        html = site_page_centers._page_center("caps-ostende").decode("utf-8")
        payloads = _payloads(html)
        con_lugar = [p for p in payloads if "lugar" in [_col_name(c) for c in p["cols"]]]
        self.assertTrue(con_lugar, "CAPS Ostende debería mostrar la columna 'lugar' (tiene sede)")
        lugares = {r["lugar"] for r in con_lugar[0]["rows"]}
        self.assertIn("Centro Cultural", lugares)

    def test_centro_sin_sede_no_muestra_columna(self):
        html = site_page_centers._page_center("hospital-pinamar").decode("utf-8")
        payloads = _payloads(html)
        servicios_p = [p for p in payloads if "servicio" in [_col_name(c) for c in p["cols"]]]
        self.assertTrue(servicios_p)
        self.assertNotIn("lugar", [_col_name(c) for c in servicios_p[0]["cols"]])

    def test_cluster_filas_distinguibles_por_sede(self):
        # /d/clinica lista dos "Medicina general" de CAPS Ostende: donde el centro
        # YA es columna, la sede se anexa a su display ("CAPS Ostende · Centro
        # Cultural") — sin eso las dos filas visibles son idénticas (verificador
        # adversarial, 2026-07-11).
        html = site_page_doctors._page_cluster("clinica").decode("utf-8")
        centros_p = [p for p in _payloads(html)
                     if "centro" in [_col_name(c) for c in p["cols"]]]
        self.assertTrue(centros_p, "no se encontró la tabla cluster-centros")
        rows = centros_p[0]["rows"]
        vistos = [(r["nombre"], r["centro"]) for r in rows]
        self.assertEqual(len(vistos), len(set(vistos)),
                         f"filas visibles indistinguibles: {vistos}")
        self.assertTrue(any("Centro Cultural" in r["centro"] for r in rows), vistos)
        # el link del centro sigue siendo el del centro real, no cambia por la sede
        cc = next(r for r in rows if "Centro Cultural" in r["centro"])
        self.assertEqual(cc["centro_href"], "/center/caps-ostende")

    def test_api_servicios_expone_sede(self):
        # The home search template appends the site to the center label:
        # necesita que /api/services (=_services_enriched) la traiga.
        svs = {s["id"]: s for s in site_listing._services_enriched()}
        self.assertEqual(
            svs["medicina-general-centro-cultural-ostende"].get("sede"),
            "Centro Cultural")
        # y la home la usa de verdad (guardia de regresión del inline script)
        with open(os.path.join(_ROOT, "templates", "index.html"), encoding="utf-8") as f:
            self.assertIn("s.sede", f.read())


class TestTitulosLimpios(unittest.TestCase):
    """webdata/servicios.json: el nombre no lleva datos de otros campos (modo,
    edad, sede, centro) — esos ya se ven en su propia columna/estado."""

    _BANNED = re.compile(
        r"\((sin turno|demanda espontánea|con orden|por orden de llegada|3ra edad)\)", re.I
    )

    def setUp(self):
        # servicios.json/centros-salud.json moved to packs/ar-b-pinamar/
        # (2026-07-30) — sitedata.path() resolves through the active pack
        # chain instead of a hand-built webdata/ path (data-packs.md's
        # swappable-package rule).
        with open(sitedata.path("servicios.json"), encoding="utf-8") as f:
            self.servicios = json.load(f)["servicios"]
        with open(sitedata.path("centros-salud.json"), encoding="utf-8") as f:
            self.centros = {c["slug"]: c for c in json.load(f)["centros"]}

    def test_sin_sufijos_de_modo_o_edad(self):
        for s in self.servicios:
            self.assertNotRegex(s["nombre"], self._BANNED, s["id"])

    def test_sin_nombre_de_centro_o_sede_en_el_titulo(self):
        for s in self.servicios:
            c = self.centros.get(s.get("centro")) or {}
            nombre_centro = c.get("name") or ""
            self.assertNotIn(" en el ", s["nombre"], s["id"])
            self.assertNotIn(" en la ", s["nombre"], s["id"])
            if nombre_centro:
                self.assertNotIn(nombre_centro, s["nombre"], s["id"])
            if s.get("sede"):
                self.assertNotIn(s["sede"], s["nombre"], s["id"])

    @reference_pack_test
    def test_texto_borrado_sigue_siendo_buscable(self):
        # los 8 casos reescritos conservan la palabra clave en `palabras`
        casos = {
            "medicina-general-ostende": "demanda espontánea",
            "pediatria-ostende": "demanda espontánea",
            "odontologia-ostende": "sin turno",
            "odontologia-valeria": "sin turno",
            "medicina-general-pinamar": "sin turno",
            "prevencion-de-caidas-ostende": "tercera edad",
            "estimulacion-cognitiva-ostende": "tercera edad",
            "medicina-general-centro-cultural-ostende": "centro cultural",
        }
        by_id = {s["id"]: s for s in self.servicios}
        for sid, palabra in casos.items():
            self.assertIn(palabra, by_id[sid].get("palabras", []), sid)

    @reference_pack_test
    def test_sede_solo_donde_corresponde(self):
        s = next(x for x in self.servicios if x["id"] == "medicina-general-centro-cultural-ostende")
        self.assertEqual(s.get("sede"), "Centro Cultural")


class TestNoManualFirstPartyVersionedAssets(unittest.TestCase):
    """Recursive zero-tolerance gate for first-party JS/CSS cache URLs."""

    # Numeric versions only: example documentation comments (listing.js,
    # platform/week.mjs) intentionally use the "?v=N" placeholder so they do not become
    # stale after each bump; they are not real references to invalidate.
    _REF = re.compile(r"/([\w-]+\.(?:js|css))\?v=(\d+)")

    # Production source = everything except docs, tests, stories, tooling,
    # vendored/third-party trees and the harness itself.
    _SKIP_DIRS = frozenset({
        ".git", "node_modules", "docs", "tests", "stories", ".storybook",
        "storybook-static", "scripts", "vendor", ".claude", "__pycache__",
        ".pytest_cache",
    })
    _SKIP_NESTED = ("static/vendor",)
    _EXTS = (".html", ".py", ".js", ".mjs", ".css", ".json")

    @classmethod
    def _production_files(cls):
        for dirpath, dirnames, filenames in os.walk(_ROOT):
            rel = os.path.relpath(dirpath, _ROOT)
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in cls._SKIP_DIRS
                and os.path.normpath(os.path.join(rel, d)) not in cls._SKIP_NESTED)
            for fn in sorted(filenames):
                if not fn.endswith(cls._EXTS):
                    continue
                yield os.path.join(dirpath, fn)

    def test_retired_webturno_shells_are_not_production_source(self):
        for name in ("app.html", "panel.html", "directorio.html",
                     "ui-core.js", "specialty-browser.js"):
            with self.subTest(name=name):
                self.assertFalse(os.path.exists(os.path.join(_ROOT, name)), name)

    def _refs(self):
        """{asset: {version: [file, ...]}} discovered across production code,
        without a hand-maintained list (drift-proof: a new ?v= asset is covered
        automatically; admin-charts.js/admin-system.js used to be excluded,
        and before batch 3 all of `patient_web/`, `modules/`, and `templates/`
        was outside the scan)."""
        refs = {}  # asset -> {version -> [file, ...]}
        for path in self._production_files():
            with open(path, encoding="utf-8", errors="ignore") as f:
                txt = f.read()
            for asset, v in set(self._REF.findall(txt)):
                refs.setdefault(asset, {}).setdefault(v, []).append(
                    os.path.relpath(path, _ROOT))
        return refs

    def test_una_sola_version_por_asset_en_todo_el_repo(self):
        refs = self._refs()
        self.assertFalse(refs, "first-party JS/CSS must not use manual ?v= URLs")

    def test_no_first_party_manual_versioned_asset_is_discovered(self):
        refs = self._refs()
        self.assertFalse(
            refs,
            "first-party JS/CSS manual-version URLs are forbidden: " + repr(refs),
        )

    def test_every_client_loaded_asset_is_referenced_only_fingerprinted(self):
        # The batch-3 contract stated from the other side: none of the
        # CLIENT_LOADED names appear on `?v=` nowhere in
        # production source — neither in a renderer nor in a loader.
        from app.core import static_fingerprint
        refs = self._refs()
        for name in sorted(static_fingerprint.CLIENT_LOADED):
            with self.subTest(name=name):
                self.assertNotIn(name, refs)


class TestListadosConLinksEnHtml(unittest.TestCase):
    """Every listing and tile mosaic ships its links as real <a href> in the
    HTML, not only inside the JSON listing.mjs/tiles.mjs read (SEO audit
    2026-10-01, Pinamar item 1: /doctors and /centers had zero links to
    /doctor, /service or /center for a crawler that runs no script)."""

    @staticmethod
    def _hrefs(html):
        visible = re.sub(r"<(script|style)\b[\s\S]*?</\1>", " ", html)
        return set(re.findall(r'<a\b[^>]*\bhref="([^"]+)"', visible))

    def test_doctors_hub_links_every_doctor_and_specialty(self):
        html = site_page_doctors._page_doctors().decode()
        hrefs = self._hrefs(html)
        rows = [r for p in _payloads(html) for r in p["rows"]]
        self.assertTrue(rows)
        for r in rows:
            self.assertIn(r["href"], hrefs)
            if r.get("esp_href"):
                self.assertIn(r["esp_href"], hrefs)
            if r.get("centro_href"):
                self.assertIn(r["centro_href"], hrefs)

    def test_centers_hub_links_every_center(self):
        hrefs = self._hrefs(site_page_centers._page_centers().decode())
        for c in site_centers._centers_web():
            self.assertIn(f'/center/{c["slug"]}', hrefs)

    def test_center_page_links_its_services(self):
        c = site_centers._centers_web()[0]
        html = site_page_centers._page_center(c["slug"]).decode()
        hrefs = self._hrefs(html)
        servicios = [r["href"] for p in _payloads(html) for r in p["rows"]
                     if r.get("href", "").startswith("/service/")]
        self.assertTrue(servicios)
        for h in servicios:
            self.assertIn(h, hrefs)

    def test_listing_links_never_point_at_robots_blocked_booking_steps(self):
        hrefs = self._hrefs(site_page_doctors._page_doctors().decode())
        self.assertFalse([h for h in hrefs if "/book" in h])


class TestResumenPorCentroSaleDeLosMedicos(unittest.TestCase):
    """/d/<especialidad>: the per-center rows above come from the SAME doctors
    listed below (demo review 2026-11-15, P2/P3): no row for a center where
    no listed doctor books; the row's turno and «orden» are its doctors'."""

    def _sv(self, sid, centro, modo="solicitud"):
        return {"id": sid, "centro": centro, "modo": modo, "nombre": "Cardiología"}

    def _fila_servicio(self, s):
        return {"nombre": s["nombre"], "centro": s["centro"], "href": f'/service/{s["id"]}',
                "turno_href": f'/service/{s["id"]}/turno',
                "proximo": {"kind": "turno", "sort": "02026-11-26 08:00", "v": "26 nov"},
                "slot_href": "/propio", "orden": False}

    def _fila_medico(self, nombre, sort, orden, slot):
        return {"nombre": nombre, "orden": orden, "turno_href": "/w?medico=" + nombre,
                "proximo": {"kind": "turno", "sort": sort, "v": sort},
                "slot_href": slot, "info_href": "/grid?medico=" + nombre}

    def test_un_centro_sin_medico_no_tiene_fila_y_la_fila_es_de_sus_medicos(self):
        from unittest import mock
        svs = [self._sv("cardio-hosp", "hospital"), self._sv("cardio-valeria", "valeria"),
               self._sv("cardio-guardia", "hospital", modo="walkin")]
        filas = [self._fila_medico("Ana", "02026-11-20 09:00", True, "/a"),
                 self._fila_medico("Beto", "02026-11-19 08:00", False, "/b"),
                 self._fila_medico("Berisso", "02026-11-18 08:00", True, "/c")]
        servicios = [svs[0], svs[0], {"id": "ergometria"}]
        with mock.patch.object(site_listing, "_service_center_row", side_effect=self._fila_servicio):
            rows = site_page_doctors._center_rows_from_doctors(svs, filas, servicios)
        self.assertEqual([r["href"] for r in rows], ["/service/cardio-hosp", "/service/cardio-guardia"])
        hosp = rows[0]
        self.assertEqual(hosp["proximo"]["sort"], "02026-11-19 08:00")  # Beto, not the ergometry
        self.assertEqual(hosp["slot_href"], "/b")
        self.assertIsNone(hosp["orden"])  # one with, one without: never a single word for both

    def test_sin_medicos_las_filas_son_las_de_siempre(self):
        from unittest import mock
        svs = [self._sv("cardio-valeria", "valeria")]
        with mock.patch.object(site_listing, "_service_center_row", side_effect=self._fila_servicio):
            rows = site_page_doctors._center_rows_from_doctors(svs, [], [])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["slot_href"], "/propio")

    def test_la_pagina_de_cardiologia_no_ofrece_un_centro_sin_cardiologo(self):
        html = site_page_doctors._page_cluster("cardiologia").decode()
        pays = _payloads(html)
        medicos = next((p for p in pays if p["cols"] and _col_name(p["cols"][0]) == "medico"), None)
        centros = next((p for p in pays if p.get("options", {}).get("searchGroup") == "cluster"
                        and p is not medicos), None)
        if not medicos or not centros:
            self.skipTest("this pack's cardiology page has no two tables")
        con_medico = {r.get("centro") for r in medicos["rows"]}
        for r in centros["rows"]:
            if (r.get("proximo") or {}).get("kind") == "walkin":
                continue
            self.assertTrue(any(c and c in r.get("centro", "") for c in con_medico), r.get("centro"))



class TestConteoDeLaPortadaEsLaLista(unittest.TestCase):
    """The home's «N médicos» of a specialty is the number of doctors its
    page lists (demo review 2026-11-15, D12/P18: the card counted only the
    specialty's own doctors while the page also listed those whose schedule
    books one of its services)."""

    def test_cada_tarjeta_cuenta_lo_que_lista_su_pagina(self):
        for cl in site_listing._directory_data()["clusters"]:
            if cl.get("walkin") or cl["id"] in ("grupos", "diagnostico"):
                continue
            html = site_page_doctors._page_cluster(cl["id"])
            if html is None:
                continue
            p = next((x for x in _payloads(html.decode()) if x["cols"] and _col_name(x["cols"][0]) == "medico"), None)
            self.assertEqual(cl["n_medicos"], len(p["rows"]) if p else 0, cl["id"])

    def test_cuenta_al_medico_de_otra_especialidad_que_la_pagina_lista(self):
        from unittest import mock
        from app.patient_web import site_catalog, site_doctors
        cardio = next((s for s in site_catalog._services() if s.get("cluster") == "cardiologia"), None)
        otro = next((m for m in site_catalog._professionals()
                     if "cardiologia" not in site_catalog._doctor_clusters(m) and not m.get("demo")), None)
        if not cardio or not otro:
            self.skipTest("this pack has no cardiology service or no other doctor")
        real = site_doctors._doctor_tagged_services

        def tagged(m, by_id=None):
            return [cardio] if m.get("id") == otro["id"] else real(m, by_id)

        with mock.patch.object(site_doctors, "_doctor_tagged_services", side_effect=tagged):
            card = next(c for c in site_listing._directory_data()["clusters"] if c["id"] == "cardiologia")
            html = site_page_doctors._page_cluster("cardiologia").decode()
        p = next(x for x in _payloads(html) if x["cols"] and _col_name(x["cols"][0]) == "medico")
        self.assertIn(otro["nombre"], [r["nombre"] for r in p["rows"]])
        self.assertEqual(card["n_medicos"], len(p["rows"]))


class TestEjemploNoEsLaPropiaPagina(unittest.TestCase):
    """«Probá: Cardiología» on the cardiology page itself searched for the
    page it was on (demo review 2026-11-15, P24)."""

    def test_sin_otro_nombre_no_hay_ejemplo_de_servicio(self):
        svs = [{"id": "a", "nombre": "Cardiología"}, {"id": "b", "nombre": "Cardiologia"}]
        self.assertIsNone(site_listing._example_service(svs, "Cardiología"))
        self.assertEqual(site_listing._example_service(svs), "Cardiología")

    def test_ninguna_pagina_de_especialidad_propone_su_propio_nombre(self):
        for cl in site_listing._directory_data()["clusters"]:
            html = site_page_doctors._page_cluster(cl["id"])
            if html is None:
                continue
            for p in _payloads(html.decode()):
                ej = [site_base._norm_name(e) for e in (p["options"].get("searchExamples") or [])]
                self.assertNotIn(site_base._norm_name(cl["nombre"]), ej, cl["id"])


class TestSinTurnosDaElPasoSiguiente(unittest.TestCase):
    """«No hay turnos disponibles» never stands alone (demo review 2026-11-15,
    P8): the cell carries `proximo.remedio = {texto, href}` — the soonest
    turno with another doctor of the same specialty (or at another center),
    else «Contanos tu caso» in the row's own wizard."""

    def _fila(self, nombre, sort=None, esp=("Cardiólogo",), habilita=False, **kw):
        px = {"kind": "turno" if sort else "solicitud", "sort": sort or "2", "v": "jueves 19 de noviembre, 08:00"}
        if habilita:
            px["habilita"] = {"estado": "sin_fechas"}
        return dict({"nombre": nombre, "proximo": px, "especialidad_todas": list(esp),
                     "turno_href": "/turno/" + nombre, "slot_href": "/slot/" + nombre}, **kw)

    def test_otro_medico_de_la_misma_especialidad(self):
        a = self._fila("Ana", habilita=True)
        b = self._fila("Beto", sort="02026-11-19 08:00")
        c = self._fila("Ceci", sort="02026-11-18 08:00", esp=("Pediatra",))
        site_availability._with_remedies([a, b, c])
        rem = a["proximo"]["remedio"]
        self.assertEqual(rem["href"], "/slot/Beto")
        self.assertIn("Beto", rem["texto"])
        self.assertIn("19 de noviembre", rem["texto"])
        self.assertNotIn("remedio", b["proximo"])

    def test_sin_alternativa_contanos_tu_caso(self):
        a = self._fila("Ana", habilita=True)
        site_availability._with_remedies([a, self._fila("Ceci", sort="02026-11-18 08:00", esp=("Pediatra",))])
        self.assertEqual(a["proximo"]["remedio"]["href"], "/turno/Ana")
        self.assertEqual(a["proximo"]["remedio"]["texto"],
                         i18n.t_or("service.noslots.case_label", "Contanos tu caso", i18n.active()))

    def test_fila_de_centro_y_celda_compartida(self):
        compartido = {"kind": "solicitud", "sort": "2", "habilita": {"estado": "sin_fechas"}}
        medico = {"nombre": "Ana", "proximo": compartido, "turno_href": "/t"}
        centro = {"nombre": "Cardiología", "centro": "CAPS Norte", "proximo": compartido, "turno_href": "/t"}
        otro = {"nombre": "Cardiología", "centro": "Hospital", "slot_href": "/h",
                "proximo": {"kind": "turno", "sort": "02026-11-19 08:00", "v": "19 nov"}}
        site_availability._with_remedies([centro, otro], by="centro")
        self.assertIn("Hospital", centro["proximo"]["remedio"]["texto"])
        self.assertNotIn("remedio", medico["proximo"])  # the doctor's cell is not touched


if __name__ == "__main__":
    unittest.main()
