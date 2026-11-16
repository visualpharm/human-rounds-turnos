"""test_estudios_tabla.py — clasificación y armado de los listados (patient_web/site_*.py:
_study_group / _study_sex / _study_audience / _studies_table_data) y del
componente único listing.js (celda próximo honesta, alias MSD, próximo por médico).

Correr:  python3 -m unittest test.test_estudios_tabla -v   (desde la raíz)

Sin base ni red: opera sobre webdata/servicios.json + data/msd_turnos.json.
Verifica las reglas de table-design (un dato por fila) y patient-flow-design
(mostrar el turno real, nunca inventar; 'Con turno' está prohibido).
"""
import json
import os
import unittest

from app.core import i18n
from app.patient_web import site_availability, site_catalog, site_listing, site_page_doctors, site_studies
from app.connectors.msd import msd_data
from app.core import sitedata
from _packtest import reference_pack_case, reference_pack_test

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)


class TestClasificacion(unittest.TestCase):
    def test_grupo_por_nombre(self):
        self.assertEqual(site_studies._study_group("Electrocardiograma"), "Cardiología")
        self.assertEqual(site_studies._study_group("Ecocardiograma"), "Cardiología")
        self.assertEqual(site_studies._study_group("Radiografía"), "Imagen")
        self.assertEqual(site_studies._study_group("Extracciones de laboratorio"), "Laboratorio")
        self.assertEqual(site_studies._study_group("Audiometría"), "Audiología")
        self.assertEqual(site_studies._study_group("Consulta rara"), "Otros estudios")

    def test_sexo_restringido(self):
        self.assertEqual(site_catalog._study_sex("Ecografía obstétrica"), "F")
        self.assertEqual(site_catalog._study_sex("Mamografía"), "F")
        self.assertEqual(site_catalog._study_sex("Ecografía de próstata"), "M")
        self.assertIsNone(site_catalog._study_sex("Ecografía"))

    def test_publico(self):
        self.assertEqual(site_catalog._study_audience([{"nombre": "Ecografía infantil"}]), "ninos")
        # reproductivo/sexo-restringido -> adultos, nunca pediátrico
        self.assertEqual(site_catalog._study_audience([{"nombre": "Mamografía"}]), "adultos")
        self.assertEqual(site_catalog._study_audience([{"nombre": "Ecografía obstétrica"}]), "adultos")
        self.assertEqual(site_catalog._study_audience([{"nombre": "Radiografía"}]), "todos")


class TestTabla(unittest.TestCase):
    def setUp(self):
        self.rows = site_studies._studies_table_data("diagnostico")

    @reference_pack_test
    def test_una_fila_por_estudio(self):
        # agrupado por nombre: 'Extracciones de laboratorio' (2 centros) = 1 fila
        nombres = [r["nombre"] for r in self.rows]
        self.assertEqual(len(nombres), len(set(nombres)), "hay estudios duplicados")
        extr = [r for r in self.rows if r["nombre"] == "Extracciones de laboratorio"]
        self.assertEqual(len(extr), 1)

    def test_columnas_un_dato(self):
        for r in self.rows:
            px = r["proximo"]
            self.assertIn(px["kind"], ("turno", "walkin", "solicitud"))
            self.assertIn(r["publico"], ("ninos", "adultos", "todos"))
            self.assertIn(r["area"], ("Cardiología", "Laboratorio", "Audiología",
                                      "Imagen", "Otros estudios"))
            self.assertNotIn("centro", r)  # el centro se ve al hacer click, no en la tabla
            self.assertIsInstance(r["orden"], bool)
            self.assertTrue(r["href"].startswith("/service/") or r["href"].startswith("/study/"))

    def test_proximo_nunca_dice_con_turno(self):
        # 'Con turno' no significa nada: es el próximo turno real o una etiqueta honesta
        for r in self.rows:
            self.assertNotEqual(r["proximo"]["v"], "Con turno")
        vals = {r["proximo"]["v"] for r in self.rows if r["proximo"]["kind"] == "solicitud"}
        # las solicitudes sin agenda online dicen 'te confirma el centro'
        self.assertTrue(all(v == "Te confirma el centro" for v in vals), vals)

    @reference_pack_test
    def test_split_por_perfil_existe(self):
        self.assertTrue(any(r["sexo"] for r in self.rows))
        self.assertTrue(any(r["publico"] == "ninos" for r in self.rows))
        self.assertTrue(any(r["publico"] == "adultos" for r in self.rows))


class TestProximoHonesto(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(site_availability._next_from("walkin", None)["v"], "Vení en el día")
        self.assertEqual(site_availability._next_from("walkin_con_orden", None)["v"], "Sin turno, por llegada")
        self.assertEqual(site_availability._next_from("solicitud_con_orden", None)["v"], "Te confirma el centro")
        real = site_availability._next_from(None, ("2026-07-27", "14:00"))
        self.assertEqual(real["kind"], "turno")
        self.assertTrue(real["sort"].startswith("0"))
        # real ordena antes que walk-in, walk-in antes que a-confirmar
        self.assertLess(real["sort"], site_availability._next_from("walkin", None)["sort"])
        self.assertLess(site_availability._next_from("walkin", None)["sort"],
                        site_availability._next_from("solicitud_con_orden", None)["sort"])


class TestListadoBuilder(unittest.TestCase):
    def _payload(self, block_html):
        # extrae el JSON incrustado del <script class="lst-data"> y lo revierte
        raw = block_html.split('class="lst-data">', 1)[1].split("</script>", 1)[0]
        return json.loads(raw.replace("\\u003c", "<"))

    def test_estudios_listado_shape(self):
        html = site_studies._studies_listing("diagnostico")
        self.assertIn("data-listado", html)
        p = self._payload(html)
        self.assertTrue(p["cols"] and p["rows"])
        # orden canónico entidad-primero (responsable del producto, 2026-07-12): Estudio | Centro |
        # Orden | Próxima — sin "Área" (columna, sigue siendo dato de filtro) ni
        # "Turno". "Centro" (preset `lugar`) entró el 2026-07-12 (sin el DÓNDE
        # la tabla no contesta "¿en qué centro me lo hago?") y el 2026-07-18
        # pasó de texto plano a link (t:"lugar") — mismo lenguaje que
        # Especialidad/Centro en Médicos.
        self.assertEqual([c["t"] for c in p["cols"]],
                         ["link", "lugar", "orden", "proximo"])
        self.assertEqual(p["cols"][0]["label"], "Estudio")
        self.assertEqual(p["cols"][1]["label"], "Centro")
        # filtros perfilados presentes (área/público/sexo)
        tipos = {f["t"] for f in p["options"]["filters"]}
        self.assertTrue({"publico", "sexo", "facet"} <= tipos)

    def test_estudios_titulo_y_facet_area_se_traducen(self):
        """El título ("Estudios") y el label del facet ("Área") eran
        literales en castellano — en /d/diagnostico con el sitio en inglés
        quedaban sin traducir arriba del buscador (bug real, demo,
        2026-08-05). Ahora reusan nav.studies / listing.col.area, las mismas
        claves que ya traducen esas palabras en otros lados del sitio."""
        i18n.bind_lang("en")
        try:
            p = self._payload(site_studies._studies_listing("diagnostico"))
            self.assertEqual(p["options"]["title"], "Studies")
            area = next(f for f in p["options"]["filters"] if f["t"] == "facet")
            self.assertEqual(area["label"], "Area")
        finally:
            i18n.bind_lang("es")
        # castellano (instalación de un solo idioma, el default): byte a
        # byte igual que siempre.
        p = self._payload(site_studies._studies_listing("diagnostico"))
        self.assertEqual(p["options"]["title"], "Estudios")
        area = next(f for f in p["options"]["filters"] if f["t"] == "facet")
        self.assertEqual(area["label"], "Área")

    def test_estudios_buscador_siempre_visible(self):
        """El buscador de estudios NO puede depender de cuántas filas haya.

        listing.js sólo dibuja la búsqueda sola a partir de >15 filas y los
        estudios son 15 justos: el buscador quedaba configurado pero NUNCA se
        renderizaba. El responsable del producto lo pidió explícito ("people can search for the study
        they want", 2026-07-12) → `search: True`."""
        p = self._payload(site_studies._studies_listing("diagnostico"))
        self.assertIs(p["options"]["search"], True)
        self.assertTrue(p["options"]["searchPlaceholder"])

    @reference_pack_test
    def test_estudios_lugar_dice_donde(self):
        """La celda Centro dice DÓNDE: linkea al centro con más horas semanales
        (el que más probablemente atiende antes) y trae `lugar_mas` — los demás
        centros, también linkeados, para el "+N" que abre "También se hace en"
        (2026-07-18: revoca el "3 centros" en texto plano, sin link)."""
        rows = {r["nombre"]: r for r in site_studies._studies_table_data("diagnostico")}
        # "Extracciones de laboratorio" se hace en 3 centros (hospital + 2 CAPS)
        multi = rows["Extracciones de laboratorio"]
        self.assertTrue(multi["lugar"])              # nombre del centro principal
        self.assertTrue(multi["lugar_href"].startswith("/center/"))
        self.assertEqual(len(multi["lugar_mas"]), 2)  # los otros 2 centros
        for c in multi["lugar_mas"]:
            self.assertTrue(c["nombre"])
            self.assertTrue(c["href"].startswith("/center/"))
        self.assertEqual(multi["n_centros"], 3)
        # el link de un estudio multi-centro va a la comparación, no a un wizard
        self.assertTrue(multi["href"].startswith("/study/"))
        # un estudio de un solo centro nombra el centro, linkeado, sin "+N"
        self.assertEqual(rows["Tomografía"]["lugar"], "Hospital de Pinamar")
        self.assertEqual(rows["Tomografía"]["lugar_href"], "/center/hospital-pinamar")
        self.assertEqual(rows["Tomografía"]["lugar_mas"], [])

    def test_cluster_diagnostico_muestra_especialidad_de_medicos(self):
        html = site_page_doctors._page_cluster("diagnostico").decode()
        bloque = html.split('id="cluster-medicos"', 1)[1]
        p = self._payload('class="lst-data">' + bloque.split('class="lst-data">', 1)[1])
        self.assertIn("Especialidad", [c.get("label") for c in p["cols"]])

    @reference_pack_test
    def test_estudio_multicentro_ordena_por_espera_y_lo_explica(self):
        """Página de un estudio: sección "estudios" en la nav (no "centros"), y
        el consejo espera-vs-viaje SÓLO cuando hay más de un centro donde elegir
        (the product owner: "better to drive a bit than to wait a couple of weeks")."""
        # _shell devuelve bytes (es lo que se escribe al socket)
        html = site_studies._page_study("diagnostico", "extracciones-de-laboratorio").decode()
        self.assertIn('<p class="elegir-espera">', html)
        self.assertIn("10 minutos", html)
        # la nav marca Estudios, no Centros de salud
        self.assertIn('data-section="estudios"', html)
        # con UN solo centro no hay nada que comparar → sin consejo
        solo = site_studies._page_study("diagnostico", "tomografia").decode()
        self.assertNotIn('<p class="elegir-espera">', solo)

    def test_medicos_enriched_rows(self):
        rows = site_listing._doctors_enriched()
        self.assertTrue(rows)
        for r in rows:
            self.assertIn(r["proximo"]["kind"], ("turno", "walkin", "solicitud"))
            # el link de la fila lleva a la página propia del médico
            self.assertTrue(r["href"].startswith("/doctor/"))
            self.assertIn("nombre", r)


@reference_pack_case
class TestMsdAliasExpansion(unittest.TestCase):
    """Toda especialidad MSD con turnos aflora en >=1 servicio Sana con turno real.

    Se fija al snapshot de referencia de webdata/: el dump vivo de data/ (que
    _load() prefiere) cambia con cada scrape — un turno que se reserva
    desaparece y los conteos flotan (visto 2026-07-11: Diabetología@924 pasó
    de 2 turnos a 0 y el test quedó rojo sin cambio de código)."""

    def setUp(self):
        from app.connectors.msd import msd_data
        self._dumps_orig, self._cache_orig = msd_data._DUMPS, msd_data._CACHE
        # msd_turnos.json moved to packs/ar-b-pinamar/ (2026-07-30).
        ref = sitedata.path("msd_turnos.json")
        msd_data._DUMPS, msd_data._CACHE = [ref], None
        with open(ref, encoding="utf-8") as f:
            self.dump = json.load(f)
        self.svs = site_catalog._services()

    def tearDown(self):
        from app.connectors.msd import msd_data
        msd_data._DUMPS, msd_data._CACHE = self._dumps_orig, self._cache_orig

    def test_cada_especialidad_con_turnos_tiene_servicio(self):
        checked = 0
        for c in self.dump.get("centros", []):
            slugs = [k for k, v in msd_data.SLUG_TO_MSD.items() if v == c.get("id")]
            for e in c.get("especialidades", []):
                tot = sum(len(m.get("turnos", [])) for m in e.get("medicos", []))
                if tot <= 0:
                    continue
                self.assertTrue(slugs, f"centro MSD {c.get('id')} sin slug Sana")
                matched = [s for s in self.svs
                           if s.get("centro") in slugs
                           and msd_data.turnos_for_servicio(s).get("especialidad") == e.get("nombre")]
                self.assertTrue(matched, f"MSD '{e.get('nombre')}' @ {c.get('id')} no aflora en ningún servicio")
                # walk-in muestra apertura del cartel, NUNCA el turno de agenda
                # (responsable del producto, 2026-07-10); el resto sí muestra el turno MSD.
                px = site_availability._service_next(matched[0])
                if matched[0].get("modo") in ("walkin", "walkin_con_orden"):
                    self.assertEqual(px["kind"], "walkin")
                    self.assertNotIn("dia", px)
                else:
                    self.assertEqual(px["kind"], "turno")
                checked += 1
        self.assertGreaterEqual(checked, 6)  # medicina general, trauma, diabeto, endo, pedia, neo

    def test_al_menos_10_servicios_con_turno_real(self):
        n = sum(1 for s in self.svs if msd_data.turnos_for_servicio(s).get("medicos"))
        self.assertGreaterEqual(n, 10)  # antes: 3


class TestProximoPorMedico(unittest.TestCase):
    def test_match_por_apellido(self):
        # 'ARIAS, MATIAS' (Ortopedia/traumatología @ Ostende) ↔ 'Matias Arias'
        self.assertTrue(msd_data._name_matches("ARIAS, MATIAS", "Matias Arias"))
        self.assertTrue(msd_data._name_matches("MONTES, Hugo", "Dr. Hugo Montes"))
        self.assertFalse(msd_data._name_matches("ARIAS, MATIAS", "Martin Correa"))

    @reference_pack_test
    def test_medico_proximo_real(self):
        arias = next((m for m in site_catalog._doctors() if m["nombre"] == "Matias Arias"), None)
        self.assertIsNotNone(arias)
        self.assertEqual(site_availability._doctor_next(arias)["kind"], "turno")


if __name__ == "__main__":
    unittest.main()
