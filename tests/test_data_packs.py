"""test_data_packs.py — data_packs.py: chain resolution, override vs merge.

Uses TEMP directories monkeypatched over data_packs._PACKS_DIR /
_LEGACY_WEBDATA_DIR so this test never touches the real packs/ or webdata/
of this checkout (isolated fixtures, same spirit as test_sitedata_dir.py's
webdata-ejemplo but built ad hoc per test to exercise each rule precisely).

Run: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_data_packs.py"
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
from app.core import data_packs  # noqa: E402
from app.patient_web import site_base, site_catalog, site_preparation


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _write_manifest(packs_dir, pack_id, level, parent=None, compose=None):
    doc = {
        "id": pack_id, "level": level, "parent": parent,
        "language": "es", "name": pack_id, "description": "fixture",
    }
    if compose:
        doc["compose"] = compose
    _write_json(os.path.join(packs_dir, pack_id, "pack.json"), doc)


class DataPacksTestBase(unittest.TestCase):
    def setUp(self):
        # registered first, so they run last: the caches are dropped after the environment is back
        self.addCleanup(data_packs.invalidate)
        self.addCleanup(config._reset_cache_para_tests)
        self._tmp = tempfile.mkdtemp(prefix="sana-data-packs-test-")
        self.packs_dir = os.path.join(self._tmp, "packs")
        self.legacy_dir = os.path.join(self._tmp, "webdata")
        os.makedirs(self.packs_dir)
        os.makedirs(self.legacy_dir)

        self._patches = [
            patch.object(data_packs, "_PACKS_DIR", self.packs_dir),
            patch.object(data_packs, "_LEGACY_WEBDATA_DIR", self.legacy_dir),
        ]
        for p in self._patches:
            p.start()

        _env.set_env(self, TURNOS_WEBDATA_DIR=None, PACK_CHAIN=None, TURNOS_PRIVATE_PACKS_DIR=None,
                     TURNOS_REQUIRED_PACKS=None)
        config._reset_cache_para_tests()
        self._db_patch = patch.object(config, "_db_overrides", return_value={})
        self._db_patch.start()
        data_packs.invalidate()

    def tearDown(self):
        self._db_patch.stop()
        for p in reversed(self._patches):
            p.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestActiveChain(DataPacksTestBase):
    def test_public_default_uses_the_shipped_example_town(self):
        self.assertEqual(data_packs.DEFAULT_CHAIN, ("global", "example-town"))

    def test_none_when_no_packs_dir(self):
        shutil.rmtree(self.packs_dir)
        self.assertFalse(data_packs.packs_root_exists())
        self.assertIsNone(data_packs.active_chain())

    def test_default_chain_when_packs_exist_and_unconfigured(self):
        for pid, lvl, parent in (
            ("global", 1, None), ("example-town", 4, "global"),
        ):
            _write_manifest(self.packs_dir, pid, lvl, parent)
        self.assertEqual(data_packs.active_chain(), data_packs.DEFAULT_CHAIN)

    def test_pack_chain_env_overrides_default(self):
        _write_manifest(self.packs_dir, "a", 1)
        _write_manifest(self.packs_dir, "b", 2, "a")
        _env.set_env(self, PACK_CHAIN="a,b")
        self.assertEqual(data_packs.active_chain(), ("a", "b"))

    def test_unscaffolded_id_in_chain_is_dropped_not_crashed(self):
        _write_manifest(self.packs_dir, "a", 1)
        _env.set_env(self, PACK_CHAIN="a,ghost")
        self.assertEqual(data_packs.active_chain(), ("a",))

    def test_turnos_webdata_dir_forces_legacy_even_with_packs_present(self):
        _write_manifest(self.packs_dir, "a", 1)
        _env.set_env(self, TURNOS_WEBDATA_DIR=self.legacy_dir)
        self.assertIsNone(data_packs.active_chain())


class TestResolvePathOverride(DataPacksTestBase):
    def setUp(self):
        super().setUp()
        _write_manifest(self.packs_dir, "a", 1)
        _write_manifest(self.packs_dir, "b", 2, "a")
        _env.set_env(self, PACK_CHAIN="a,b")

    def test_highest_level_pack_wins_when_both_have_the_file(self):
        _write_json(os.path.join(self.packs_dir, "a", "data.json"), {"x": 1})
        _write_json(os.path.join(self.packs_dir, "b", "data.json"), {"x": 2})
        resolved = data_packs.resolve_path("data.json")
        self.assertEqual(resolved, os.path.join(self.packs_dir, "b", "data.json"))
        with open(resolved) as f:
            self.assertEqual(json.load(f)["x"], 2)

    def test_falls_through_to_lower_level_when_higher_lacks_the_file(self):
        _write_json(os.path.join(self.packs_dir, "a", "only-in-a.json"), {"x": 1})
        resolved = data_packs.resolve_path("only-in-a.json")
        self.assertEqual(resolved, os.path.join(self.packs_dir, "a", "only-in-a.json"))

    def test_missing_everywhere_returns_lowest_level_path_not_a_crash(self):
        resolved = data_packs.resolve_path("nowhere.json")
        self.assertEqual(resolved, os.path.join(self.packs_dir, "a", "nowhere.json"))
        self.assertFalse(os.path.isfile(resolved))

    def test_turnos_webdata_dir_env_wins_over_the_pack_chain(self):
        _write_json(os.path.join(self.packs_dir, "b", "data.json"), {"x": 2})
        _write_json(os.path.join(self.legacy_dir, "data.json"), {"x": "legacy"})
        _env.set_env(self, TURNOS_WEBDATA_DIR=self.legacy_dir)
        resolved = data_packs.resolve_path("data.json")
        self.assertEqual(resolved, os.path.join(self.legacy_dir, "data.json"))


class TestNoLegacyFallbackInPackMode(DataPacksTestBase):
    """The `ar-b-pinamar`-only legacy fallback was deleted 2026-07-30 once
    every real Pinamar file had a home under packs/ar-b-pinamar/ (see
    docs/architecture/data-packs.md). resolve_path() in pack mode must NEVER
    fall through to webdata/ again, for ANY chain — including one that
    contains `ar-b-pinamar` — that's what keeps the example-town genericity
    test honest (no chain can leak Pinamar's webdata/ into it)."""

    def test_ar_b_pinamar_chain_does_not_fall_back_to_legacy_webdata(self):
        _write_manifest(self.packs_dir, "global", 1)
        _write_manifest(self.packs_dir, "ar-b-pinamar", 4, "global")
        _env.set_env(self, PACK_CHAIN="global,ar-b-pinamar")
        _write_json(os.path.join(self.legacy_dir, "sitio.json"), {"localidad": "Pinamar (leak!)"})
        resolved = data_packs.resolve_path("sitio.json")
        # NOT the legacy dir -- the fallback is gone, missing means missing.
        self.assertNotEqual(resolved, os.path.join(self.legacy_dir, "sitio.json"))
        self.assertEqual(resolved, os.path.join(self.packs_dir, "global", "sitio.json"))

    def test_other_chain_does_not_fall_back_either(self):
        _write_manifest(self.packs_dir, "global", 1)
        _write_manifest(self.packs_dir, "example-town", 4, "global")
        _env.set_env(self, PACK_CHAIN="global,example-town")
        _write_json(os.path.join(self.legacy_dir, "sitio.json"), {"localidad": "Pinamar (leak!)"})
        resolved = data_packs.resolve_path("sitio.json")
        self.assertNotEqual(resolved, os.path.join(self.legacy_dir, "sitio.json"))
        self.assertEqual(resolved, os.path.join(self.packs_dir, "global", "sitio.json"))


class TestMerged(DataPacksTestBase):
    def setUp(self):
        super().setUp()
        _write_manifest(self.packs_dir, "global", 1)
        _write_manifest(self.packs_dir, "ar-b-pinamar", 4, "global")
        _env.set_env(self, PACK_CHAIN="global,ar-b-pinamar")

    def test_override_by_id_keeps_base_order_and_appends_new_ids(self):
        _write_json(os.path.join(self.packs_dir, "global", "especialidades.json"), {
            "especialidades": [
                {"nombre": "Cardiología", "cluster": "cardiologia"},
                {"nombre": "Pediatría", "cluster": "pediatria"},
            ],
        })
        _write_json(os.path.join(self.packs_dir, "ar-b-pinamar", "especialidades.json"), {
            "especialidades": [
                {"nombre": "Cardiología", "cluster": "cardio-y-clinica-medica"},
                {"nombre": "Podología", "cluster": "otras"},
            ],
        })
        out = data_packs.merged("especialidades.json", "especialidades", id_key="nombre")
        self.assertEqual([e["nombre"] for e in out], ["Cardiología", "Pediatría", "Podología"])
        self.assertEqual(out[0]["cluster"], "cardio-y-clinica-medica")  # overridden in place
        self.assertEqual(out[1]["cluster"], "pediatria")                # untouched base entry

    def test_does_not_fall_back_to_legacy_webdata_when_nothing_in_the_chain_has_it(self):
        # The ar-b-pinamar legacy fallback was deleted 2026-07-30 (see
        # docs/architecture/data-packs.md) -- a file present ONLY in
        # webdata/, absent from every pack in the chain, is simply not
        # found: merged() returns [], it never reaches into webdata/.
        _write_json(os.path.join(self.legacy_dir, "especialidades.json"), {
            "especialidades": [{"nombre": "Clínica médica", "cluster": "clinica"}],
        })
        out = data_packs.merged("especialidades.json", "especialidades", id_key="nombre")
        self.assertEqual(out, [])

    def test_legacy_mode_reads_the_single_file_directly(self):
        _env.set_env(self, PACK_CHAIN=None)
        shutil.rmtree(self.packs_dir)
        os.makedirs(self.packs_dir)  # packs/ exists but empty -> no manifests
        data_packs.invalidate()
        _write_json(os.path.join(self.legacy_dir, "especialidades.json"), {
            "especialidades": [{"nombre": "Sólo legacy", "cluster": "otras"}],
        })
        out = data_packs.merged("especialidades.json", "especialidades", id_key="nombre")
        self.assertEqual([e["nombre"] for e in out], ["Sólo legacy"])


class TestCompose(DataPacksTestBase):
    """The compose registry (data_packs._COMPOSABLE): default merge, the
    "replace" escape hatch, and tombstones — used by catalog.specialties()
    and site_catalog._clusters() (phase 3, 2026-07-30)."""

    def setUp(self):
        super().setUp()
        _write_manifest(self.packs_dir, "global", 1)
        _write_manifest(self.packs_dir, "ar-b-pinamar", 4, "global")
        _env.set_env(self, PACK_CHAIN="global,ar-b-pinamar")

    def test_rejects_a_file_not_in_the_registry(self):
        with self.assertRaises(KeyError):
            data_packs.compose("centros-salud.json")

    def test_default_merges_across_the_whole_chain_by_id(self):
        _write_json(os.path.join(self.packs_dir, "global", "especialidades.json"), {
            "specialties": [
                {"nombre": "Cardiología", "cluster": "cardiologia"},
                {"nombre": "Pediatría", "cluster": "pediatria"},
            ],
        })
        _write_json(os.path.join(self.packs_dir, "ar-b-pinamar", "especialidades.json"), {
            "specialties": [
                {"nombre": "Cardiología", "cluster": "cardio-y-clinica-medica"},
                {"nombre": "Podología", "cluster": "otras"},
            ],
        })
        out = data_packs.compose("especialidades.json")
        self.assertEqual([e["nombre"] for e in out], ["Cardiología", "Pediatría", "Podología"])
        self.assertEqual(out[0]["cluster"], "cardio-y-clinica-medica")  # higher level wins
        self.assertEqual(out[1]["cluster"], "pediatria")                # untouched base entry

    def test_replace_hides_every_ancestor_for_that_file(self):
        """packs/example-town's real-world case: a town pack whose taxonomy
        subset doesn't line up 1:1 with the parent's full catalog must NOT
        get fused with it."""
        _write_json(os.path.join(self.packs_dir, "global", "especialidades.json"), {
            "specialties": [{"nombre": e, "cluster": "x"} for e in
                             ["Cardiología", "Pediatría", "Traumatología", "Dermatología"]],
        })
        _write_manifest(self.packs_dir, "ar-b-pinamar", 4, "global",
                         compose={"especialidades.json": "replace"})
        _write_json(os.path.join(self.packs_dir, "ar-b-pinamar", "especialidades.json"), {
            "specialties": [{"nombre": "Nutrición", "cluster": "clinica"}],
        })
        out = data_packs.compose("especialidades.json")
        self.assertEqual([e["nombre"] for e in out], ["Nutrición"])  # global's 4 never show up

    def test_replace_only_affects_the_declared_file_not_others(self):
        _write_json(os.path.join(self.packs_dir, "global", "clusters.json"), {
            "clusters": [{"id": "clinica", "nombre": "Clínica"}],
        })
        _write_json(os.path.join(self.packs_dir, "ar-b-pinamar", "clusters.json"), {
            "clusters": [{"id": "otras", "nombre": "Otras"}],
        })
        _write_manifest(self.packs_dir, "ar-b-pinamar", 4, "global",
                         compose={"especialidades.json": "replace"})  # NOT clusters.json
        out = data_packs.compose("clusters.json")
        self.assertEqual({c["id"] for c in out}, {"clinica", "otras"})  # still merged

    def test_tombstone_removes_an_id_from_the_composed_result(self):
        _write_json(os.path.join(self.packs_dir, "global", "clusters.json"), {
            "clusters": [
                {"id": "clinica", "nombre": "Clínica"},
                {"id": "descontinuado", "nombre": "Descontinuado"},
            ],
        })
        _write_json(os.path.join(self.packs_dir, "ar-b-pinamar", "clusters.json"), {
            "clusters": [{"id": "descontinuado", "_removed": True}],
        })
        out = data_packs.compose("clusters.json")
        self.assertEqual([c["id"] for c in out], ["clinica"])

    def test_tombstone_works_together_with_replace(self):
        _write_json(os.path.join(self.packs_dir, "global", "clusters.json"), {
            "clusters": [{"id": "leaks-if-not-replaced", "nombre": "x"}],
        })
        _write_manifest(self.packs_dir, "ar-b-pinamar", 4, "global",
                         compose={"clusters.json": "replace"})
        _write_json(os.path.join(self.packs_dir, "ar-b-pinamar", "clusters.json"), {
            "clusters": [
                {"id": "a", "nombre": "A"},
                {"id": "b", "nombre": "B"},
                {"id": "a", "_removed": True},
            ],
        })
        out = data_packs.compose("clusters.json")
        self.assertEqual([c["id"] for c in out], ["b"])

    def test_legacy_mode_composes_the_single_file_and_drops_tombstones(self):
        _env.set_env(self, PACK_CHAIN=None)
        shutil.rmtree(self.packs_dir)
        os.makedirs(self.packs_dir)
        data_packs.invalidate()
        _write_json(os.path.join(self.legacy_dir, "clusters.json"), {
            "clusters": [
                {"id": "a", "nombre": "A"},
                {"id": "b", "_removed": True},
            ],
        })
        out = data_packs.compose("clusters.json")
        self.assertEqual([c["id"] for c in out], ["a"])


class TestActiveChainCenter(DataPacksTestBase):
    """L5 center packs: active_chain(center=...) appends a real chain
    segment, degrading (not crashing) when the center has no pack."""

    def setUp(self):
        super().setUp()
        _write_manifest(self.packs_dir, "global", 1)
        _write_manifest(self.packs_dir, "ar-b-pinamar", 4, "global")
        _env.set_env(self, PACK_CHAIN="global,ar-b-pinamar")

    def test_no_center_leaves_the_chain_untouched(self):
        self.assertEqual(data_packs.active_chain(), ("global", "ar-b-pinamar"))
        self.assertEqual(data_packs.active_chain(center=None), ("global", "ar-b-pinamar"))

    def test_unscaffolded_center_degrades_to_the_plain_chain(self):
        self.assertEqual(data_packs.active_chain(center="no-existe"),
                          ("global", "ar-b-pinamar"))

    def test_scaffolded_center_is_appended_as_the_highest_segment(self):
        _write_manifest(self.packs_dir, "ar-b-pinamar/centers/hospital-pinamar", 5,
                         "ar-b-pinamar")
        self.assertEqual(
            data_packs.active_chain(center="hospital-pinamar"),
            ("global", "ar-b-pinamar", "ar-b-pinamar/centers/hospital-pinamar"))

    def test_a_different_centers_pack_never_leaks_into_this_ones_lookup(self):
        _write_manifest(self.packs_dir, "ar-b-pinamar/centers/hospital-pinamar", 5,
                         "ar-b-pinamar")
        chain = data_packs.active_chain(center="caps-ostende")  # no pack of its own
        self.assertNotIn("ar-b-pinamar/centers/hospital-pinamar", chain)
        self.assertEqual(chain, ("global", "ar-b-pinamar"))


class TestResolvePathCenter(DataPacksTestBase):
    def setUp(self):
        super().setUp()
        _write_manifest(self.packs_dir, "global", 1)
        _write_manifest(self.packs_dir, "ar-b-pinamar", 4, "global")
        _write_manifest(self.packs_dir, "ar-b-pinamar/centers/hospital-pinamar", 5,
                         "ar-b-pinamar")
        _env.set_env(self, PACK_CHAIN="global,ar-b-pinamar")
        _write_json(os.path.join(self.packs_dir, "ar-b-pinamar", "nota.json"), {"x": "town"})
        _write_json(os.path.join(self.packs_dir, "ar-b-pinamar", "centers",
                                  "hospital-pinamar", "nota.json"), {"x": "hospital"})

    def test_center_pack_file_wins_when_center_given(self):
        resolved = data_packs.resolve_path("nota.json", center="hospital-pinamar")
        with open(resolved) as f:
            self.assertEqual(json.load(f)["x"], "hospital")

    def test_no_center_never_sees_the_center_packs_file(self):
        resolved = data_packs.resolve_path("nota.json")
        with open(resolved) as f:
            self.assertEqual(json.load(f)["x"], "town")

    def test_a_different_center_does_not_leak_the_hospitals_override(self):
        resolved = data_packs.resolve_path("nota.json", center="caps-ostende")
        with open(resolved) as f:
            self.assertEqual(json.load(f)["x"], "town")

    def test_turnos_webdata_dir_ignores_center_entirely(self):
        _env.set_env(self, TURNOS_WEBDATA_DIR=self.legacy_dir)
        _write_json(os.path.join(self.legacy_dir, "nota.json"), {"x": "legacy"})
        resolved = data_packs.resolve_path("nota.json", center="hospital-pinamar")
        self.assertEqual(resolved, os.path.join(self.legacy_dir, "nota.json"))


class TestCenterPackHospitalPinamar(unittest.TestCase):
    """Integration test against the external private deployment's L5 pack.

    ``ar-b-pinamar/centers/hospital-pinamar`` is the one genuine L5 pack
    (data_packs.py, "Center packs (L5)"): its preparacion-centro.json
    overrides laboratorio-sangre's fasting note with the Hospital's own
    (the cartel doesn't publish the detail online, points to consultorio
    5), applied by site_preparation._prep_section_html() for any service whose
    `centro` is hospital-pinamar. Not the tmp-fixture DataPacksTestBase
    below — this exercises the real, committed pack."""

    _OVERRIDE_MARKER = "cartel del Laboratorio del Hospital de Pinamar"

    @classmethod
    def setUpClass(cls):
        try:
            data_packs.private_pack_dir("ar-b-pinamar")
        except data_packs.RequiredPackError as exc:
            raise unittest.SkipTest(
                "external private Pinamar pack is not configured"
            ) from exc

    def setUp(self):
        # The private deployment pack is explicit; the public default remains
        # the fictional example-town chain.
        self.addCleanup(site_base._CACHE.clear)
        self.addCleanup(data_packs.invalidate)
        self.addCleanup(config._reset_cache_para_tests)
        _env.set_env(self, PACK_CHAIN="global,ar,ar-b,ar-b-pinamar", TURNOS_WEBDATA_DIR=None)
        config._reset_cache_para_tests()
        data_packs.invalidate()
        site_base._CACHE.clear()

    def _svc(self, sid):
        s = next((x for x in site_catalog._services() if x["id"] == sid), None)
        self.assertIsNotNone(s, f"servicio '{sid}' no existe en servicios.json")
        return s

    def test_pack_is_scaffolded_and_appended_for_the_hospital(self):
        chain = data_packs.active_chain(center="hospital-pinamar")
        self.assertEqual(chain[-1], "ar-b-pinamar/centers/hospital-pinamar")

    def test_override_wins_for_a_hospital_lab_service(self):
        s = self._svc("laboratorio-hospital")
        self.assertEqual(s.get("centro"), "hospital-pinamar")
        html = site_preparation._prep_section_html(s)
        self.assertIn(self._OVERRIDE_MARKER, html)

    def test_override_wins_for_the_hospitals_other_lab_service_too(self):
        s = self._svc("extracciones-de-laboratorio-pinamar")
        self.assertEqual(s.get("centro"), "hospital-pinamar")
        html = site_preparation._prep_section_html(s)
        self.assertIn(self._OVERRIDE_MARKER, html)

    def test_does_not_leak_to_the_same_study_at_a_different_center(self):
        for sid in ("extracciones-ostende", "extracciones-valeria"):
            s = self._svc(sid)
            self.assertNotEqual(s.get("centro"), "hospital-pinamar")
            html = site_preparation._prep_section_html(s)
            self.assertNotIn(self._OVERRIDE_MARKER, html)
            # still shows SOME fasting note (the generic one, not silence):
            self.assertIn("ayuno", html.lower())


class TestExampleTownCatalogIntegrity(unittest.TestCase):
    """Integration tests against the REAL, committed
    packs/example-town/*.json produced by
    scripts/generate_example_town_catalog.py — referential integrity
    (every doctor's specialty/cluster exists in this pack's own
    especialidades.json/clusters.json, every service's centro exists) and
    the modo distribution the task asked for (~25 solicitud_con_orden / ~22
    walkin / ~20 solicitud / ~3 walkin_con_orden out of ~70, scaled from
    Pinamar's real 43/38/34/5 out of 120)."""

    EJEMPLO_DIR = os.path.join(ROOT, "packs", "example-town")

    @classmethod
    def setUpClass(cls):
        def _load(fname):
            with open(os.path.join(cls.EJEMPLO_DIR, fname), encoding="utf-8") as f:
                return json.load(f)

        cls.centros = _load("centros-salud.json")["centros"]
        cls.especialidades = _load("especialidades.json")["specialties"]
        cls.clusters = _load("clusters.json")["clusters"]
        cls.medicos = _load("medicos.json")["medicos"]
        cls.servicios = _load("servicios.json")["servicios"]

        cls.centro_slugs = {c["slug"] for c in cls.centros}
        cls.especialidad_nombres = {e["nombre"] for e in cls.especialidades}
        cls.cluster_ids = {c["id"] for c in cls.clusters}
        cls.especialidad_cluster = {e["nombre"]: e["cluster"] for e in cls.especialidades}

    def test_five_centers_nineteen_specialties(self):
        self.assertEqual(len(self.centros), 5)
        # 18 -> 19 on 2026-10-26: Urología, for the hospital's urology service
        # that had nobody to see its patients (TASK-58).
        self.assertTrue(15 <= len(self.especialidades) <= 19)

    def test_sixty_eight_doctors_seventy_seven_services(self):
        # 50 -> 68 on 2026-10-26: one professional for each bookable service
        # no doctor covered — «No hay médicos de Ecografía general cargados»
        # on the demo (TASK-58, generator's _MEDICOS_EXTRA).
        self.assertEqual(len(self.medicos), 68)
        # 70 -> 76 on 2026-08-04: the workshops cluster went from 2 services to
        # 8, so /d/grupos looks like a real one (each with its own illustration).
        # 76 -> 77 on 2026-10-18: the hospital's adult Guardia (usability study,
        # two patients could not find the guardia).
        self.assertEqual(len(self.servicios), 77)

    def test_walkin_cluster_card_like_pinamars(self):
        """The demo's «Más buscadas» closes with «Demanda espontánea» as
        Pinamar's does: a `kind: walkin` cluster with its own description,
        backed by walk-in services that really exist in this pack."""
        cl = next((c for c in self.clusters if c["id"] == "demanda-espontanea"), None)
        self.assertIsNotNone(cl, "falta el cluster demanda-espontanea")
        self.assertEqual(cl["kind"], "walkin")
        self.assertTrue(cl.get("top"))
        self.assertTrue(cl.get("descripcion", "").strip())
        walkin = [s for s in self.servicios if s["modo"] == "walkin"]
        self.assertTrue(walkin)
        self.assertTrue(all("agenda" not in s for s in walkin))
        self.assertTrue(any(s["id"] == "guardia-sampleton-hospital" for s in walkin))

    def test_every_doctor_specialty_exists_in_this_packs_own_catalog(self):
        for m in self.medicos:
            self.assertIn(m["especialidad"], self.especialidad_nombres,
                          f"médico {m['id']!r}: especialidad {m['especialidad']!r} "
                          "no está en especialidades.json de este pack")

    def test_every_doctor_cluster_exists_and_matches_its_specialty(self):
        for m in self.medicos:
            self.assertIn(m["cluster"], self.cluster_ids,
                          f"médico {m['id']!r}: cluster {m['cluster']!r} "
                          "no está en clusters.json de este pack")
            self.assertEqual(
                m["cluster"], self.especialidad_cluster.get(m["especialidad"]),
                f"médico {m['id']!r}: su cluster no coincide con el cluster "
                "de su propia especialidad en especialidades.json")

    def test_every_doctor_center_exists(self):
        for m in self.medicos:
            self.assertIn(m["centro"], self.centro_slugs,
                          f"médico {m['id']!r}: centro {m['centro']!r} no existe")
            for c in m.get("centros") or []:
                self.assertIn(c["centro"], self.centro_slugs)

    def test_every_service_center_exists(self):
        for s in self.servicios:
            self.assertIn(s["centro"], self.centro_slugs,
                          f"servicio {s['id']!r}: centro {s['centro']!r} no existe")

    def test_every_service_cluster_exists(self):
        for s in self.servicios:
            self.assertIn(s["cluster"], self.cluster_ids,
                          f"servicio {s['id']!r}: cluster {s['cluster']!r} no existe")

    def test_modo_distribution_is_in_the_intended_ballpark(self):
        from collections import Counter
        modos = Counter(s["modo"] for s in self.servicios)
        # Exact counts the generator targets (25/28/20/3 of 76) — "ballpark"
        # tolerance of +/-3 so small future edits to _SERVICIOS_DEF don't
        # make this test brittle. `walkin` carries the six workshops added on
        # 2026-08-04 (a workshop never takes an appointment).
        objetivo = {"solicitud_con_orden": 25, "walkin": 28,
                    "solicitud": 20, "walkin_con_orden": 3}
        for modo, esperado in objetivo.items():
            self.assertAlmostEqual(modos.get(modo, 0), esperado, delta=3,
                                   msg=f"modo {modo!r}: {modos.get(modo, 0)} "
                                       f"servicios, se esperaban ~{esperado}")

    def test_age_routing_has_a_pediatric_upper_and_geriatric_lower_bound(self):
        maxes = [s["edad"]["max"] for s in self.servicios if s.get("edad", {}).get("max")]
        mins = [s["edad"]["min"] for s in self.servicios if s.get("edad", {}).get("min")]
        self.assertTrue(any(m <= 18 for m in maxes), "falta un tope de edad pediátrico")
        self.assertTrue(any(m >= 60 for m in mins), "falta un piso de edad geriátrico")

    def test_at_least_one_pami_note(self):
        self.assertTrue(any(s.get("pami") for s in self.servicios))

    def test_apertura_mismo_dia_only_on_no_appointment_modes_and_no_agenda(self):
        no_appt = {"walkin", "walkin_con_orden"}
        con_apertura = [s for s in self.servicios if s.get("apertura_mismo_dia")]
        self.assertGreaterEqual(len(con_apertura), 2)
        for s in con_apertura:
            self.assertIn(s["modo"], no_appt)
            self.assertNotIn("agenda", s)

    def test_no_walkin_service_has_an_agenda(self):
        for s in self.servicios:
            if s["modo"] in ("walkin", "walkin_con_orden"):
                self.assertNotIn("agenda", s, f"servicio walkin {s['id']!r} lleva agenda")

    def test_every_appointment_service_has_an_agenda(self):
        for s in self.servicios:
            if s["modo"] in ("solicitud", "solicitud_con_orden"):
                self.assertIn("agenda", s, f"servicio {s['id']!r} no tiene agenda")

    def test_a_subset_of_services_has_turnos_activos_max(self):
        con_max = [s for s in self.servicios if s.get("turnos_activos_max")]
        self.assertTrue(0 < len(con_max) < len(self.servicios))

    def test_at_least_one_service_has_a_sede(self):
        self.assertTrue(any(s.get("sede") for s in self.servicios))

    def test_backward_compat_ids_and_slugs_preserved(self):
        """Ids/slugs other (out-of-scope-for-this-generator) tests hardcode
        — see the generator's module docstring."""
        ids = {s["id"] for s in self.servicios}
        self.assertIn("vacunacion-riverside-clinic", ids)
        self.assertIn("cardiologia-sampleton-hospital", ids)
        self.assertIn("sampleton-hospital", self.centro_slugs)
        self.assertIn("riverside-clinic", self.centro_slugs)
        cardio = next(s for s in self.servicios if s["id"] == "cardiologia-sampleton-hospital")
        self.assertEqual(cardio["centro"], "sampleton-hospital")
        self.assertNotIn(cardio["modo"], ("walkin", "walkin_con_orden"))

    def test_doctors_use_more_than_one_schedule_template(self):
        """Not 50 doctors on an identical schedule (task requirement)."""
        def _hashable_franja(fr):
            return tuple(sorted(
                (k, tuple(v) if isinstance(v, list) else v) for k, v in fr.items()
            ))

        horarios = {tuple(_hashable_franja(fr) for fr in m["agenda"]["franjas"])
                    for m in self.medicos}
        self.assertGreater(len(horarios), 3)

    def test_doctor_matriculas_are_ficticio_never_a_real_registry(self):
        for m in self.medicos:
            mat = m.get("matricula") or {}
            self.assertEqual(mat.get("fuente"), "ficticio",
                             f"médico {m['id']!r}: fuente de matrícula debe ser "
                             "'ficticio', nunca un padrón real")


class TestExampleTownGeneratorDeterminism(unittest.TestCase):
    """scripts/generate_example_town_catalog.py must never use `random` —
    re-running it has to produce byte-identical JSON (same discipline as
    seed_demo_all.py:_pacientes())."""

    def test_regenerating_produces_identical_files(self):
        import importlib
        import sys as _sys

        committed = os.path.join(ROOT, "packs", "example-town")
        names = sorted(n for n in os.listdir(committed) if n.endswith(".json"))
        stamps = {n: os.stat(os.path.join(committed, n)).st_mtime_ns for n in names}

        def snapshot(directory):
            out = {}
            for n in names:
                with open(os.path.join(directory, n), "rb") as f:
                    out[n] = f.read()
            return out

        # generate() writes the pack it is pointed at, truncating each file before it fills it.
        # Run against the real packs/example-town it let any other process that reads the pack
        # at that moment (another backend shard: test_search_examples_i18n) see a cut-off
        # servicios.json — so it runs on a copy.
        with tempfile.TemporaryDirectory() as tmp:
            copy = os.path.join(tmp, "example-town")
            shutil.copytree(committed, copy)
            sys.path.insert(0, os.path.join(ROOT, "scripts"))
            try:
                gen = importlib.import_module("generate_example_town_catalog")
                with patch.object(gen, "PACK_DIR", copy):
                    servicios1, medicos1 = gen.generate()
                    first = snapshot(copy)
                    servicios2, medicos2 = gen.generate()
                    second = snapshot(copy)
            finally:
                _sys.path.remove(os.path.join(ROOT, "scripts"))
            self.assertEqual(servicios1, servicios2)
            self.assertEqual(medicos1, medicos2)
            self.assertEqual(first, second)
            for n in names:
                with open(os.path.join(committed, n), "rb") as f:
                    self.assertEqual(f.read(), first[n], "%s is not what the generator produces" % n)
        self.assertEqual(
            stamps, {n: os.stat(os.path.join(committed, n)).st_mtime_ns for n in names},
            "the determinism test must not write the committed pack",
        )


if __name__ == "__main__":
    unittest.main()
