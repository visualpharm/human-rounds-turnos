"""test_golden_pages.py — golden-page parity gate for the layered data-pack
migration (phase 2, 2026-07-30).

Two independent guarantees:

(a) TestGoldenPagesPinamarChain — `/`, one service page and one center
    page render BYTE-IDENTICAL whether resolved through the pack chain
    (default mode now that packs/ exists: global,ar,ar-b,ar-b-pinamar) or
    through the exact single-dir layout webdata/ had BEFORE this slice
    moved especialidades.json/clusters.json out of it into packs/global/.
    That "before" state is reconstructed once (class-scoped) as a temp dir
    symlinking every real webdata/ entry plus the two pilot files pulled
    back in from packs/global/ — a real TURNOS_WEBDATA_DIR=webdata/ run
    would otherwise legitimately differ (those two files are simply gone
    from webdata/ now), which would make the comparison meaningless rather
    than a parity proof. As the mass-migration agent moves more files out
    of webdata/ into packs/ar-b-pinamar/, this is the test that proves each
    move didn't change a single byte of what patients see (extend the
    snapshot construction below the same way each time a file moves).

(b) TestExampleTownChainIsGeneric — PACK_CHAIN=global,example-town boots
    and renders `/` with zero "Pinamar" strings (extends
    test_sitedata_dir.py's TURNOS_WEBDATA_DIR=packs/example-town genericity
    check to the new pack-chain mechanism).

Run: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_golden_pages.py"
"""

import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.patient_web import catalog        # noqa: E402
from app.core import data_packs  # noqa: E402
from app.patient_web import site_base, site_page_centers, site_page_service
from app.core import site_identity  # noqa: E402
from app.core import config  # noqa: E402
from app.core import theme  # noqa: E402

PINAMAR_SERVICE_ID = "radiografia-hospital"
PINAMAR_CENTER_SLUG = "hospital-pinamar"

# Files this slice moved OUT of webdata/ into packs/global/ (see
# docs/architecture/data-packs.md). Kept as a list so the pre-move-snapshot
# fixture below is a one-line edit when the mass-migration agent moves more.
# estudios-preparacion.json: only its "estudios" list feeds the three golden
# pages (site_catalog._preparations()) -- the `_meta` keys it lost to
# packs/ar-b-pinamar/estudios-preparacion-meta.json (Pinamar's
# no_ofrecidos_actualmente_hospital_pinamar override) are read by
# remedies.py, not by page rendering, so they don't affect this parity check.
_MOVED_TO_GLOBAL = ("especialidades.json", "clusters.json", "estudios-preparacion.json")

# Files this slice moved OUT of webdata/ into packs/ar-b-pinamar/ (2026-07-30,
# mass-migration slice) -- same "extend this list" contract as _MOVED_TO_GLOBAL
# above, just symlinked back from a different pack.
_MOVED_TO_PINAMAR_PACK = (
    "sitio.json", "centros-salud.json", "clinicas.json", "conocimiento.json",
    # Round 2 (mass-migration slice, same day): pure-JSON files with no
    # public-URL exposure of their own (unlike the photo/sprite binaries
    # still in webdata/, see data-packs.md "remaining work").
    "servicios.json", "medicos.json", "msd_turnos.json",
    "centros-info.json", "centros-galeria.json", "centros-resenas.json",
    "resenas-analisis.json",
    # Round 3, the binary/public-URL drain (2026-07-30): webdata/ is now
    # EMPTY, everything moved to packs/ar-b-pinamar/. /webdata/<path> stays
    # the stable public URL (server.py resolves it through the pack chain
    # now, see the do_GET() branch added this slice) so none of these
    # changed a single rendered byte -- listed here so the pre-move snapshot
    # can still symlink them back in for the parity comparison.
    "brila-generation-results.json", "clinic-maps-urls.json",
    "doctor-photo-sprite.json", "doctor-photo-sprite.webp",
    "featured-medic-gpt-sheet.png", "featured-medic-sprite.json",
    "featured-medic-sprite.webp", "medico-fotos.json",
    "medico-photo-sprite.json", "medico-photo-sprite.webp", "photos.json",
    "centro-photos", "centros-brila-info", "doctor-photos", "fuentes",
    "medico-photos", "photos-cache", "talleres",
)


def _clear_caches():
    catalog._JSON_CACHE.clear()
    site_base._CACHE.clear()
    site_identity._CACHE.clear()
    data_packs.invalidate()


def _reset_caches():
    config._reset_cache_para_tests()
    _clear_caches()


def _identidad(tipo, base, incluir_borrados=False, strict=False):
    """Same determinism patch as test_sitedata_dir.py: a real Postgres with
    admin-edited Pinamar overlay rows would append DB-only ids at the end
    regardless of which base it's handed, making a naive byte-diff
    non-deterministic depending on whether Postgres happens to be up."""
    meta = {it.get(catalog.id_key(tipo)): {"_origen": "json", "_borrado": False} for it in base}
    return list(base), meta


class TestGoldenPagesPinamarChain(unittest.TestCase):
    """Compares the live pack-chain render against a reconstructed
    pre-migration webdata/ (real webdata/ + the pilot files symlinked back
    in from packs/global/) — the honest "what did this page look like
    before the move" baseline. See the module docstring."""

    @classmethod
    def setUpClass(cls):
        try:
            private_pack = data_packs.private_pack_dir("ar-b-pinamar")
        except data_packs.RequiredPackError as exc:
            raise unittest.SkipTest(
                "external private Pinamar pack is not configured"
            ) from exc
        cls._snapshot_dir = tempfile.mkdtemp(prefix="sana-webdata-pre-move-")
        webdata = os.path.join(ROOT, "webdata")
        for name in os.listdir(webdata):
            os.symlink(os.path.join(webdata, name), os.path.join(cls._snapshot_dir, name))
        for name in _MOVED_TO_GLOBAL:
            os.symlink(os.path.join(ROOT, "packs", "global", name),
                       os.path.join(cls._snapshot_dir, name))
        for name in _MOVED_TO_PINAMAR_PACK:
            os.symlink(os.path.join(private_pack, name),
                       os.path.join(cls._snapshot_dir, name))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._snapshot_dir, ignore_errors=True)

    def setUp(self):
        self.addCleanup(_reset_caches)   # registered first, so it runs after the environment is put back
        _env.set_env(self, TURNOS_WEBDATA_DIR=None, PACK_CHAIN="global,ar,ar-b,ar-b-pinamar")
        config._reset_cache_para_tests()
        self._merge_patch = patch.object(catalog, "merge_with_overlay", _identidad)
        self._merge_patch.start()
        _clear_caches()

    def tearDown(self):
        self._merge_patch.stop()

    def _render_pack_mode(self, fn):
        _env.set_env(self, TURNOS_WEBDATA_DIR=None)
        _clear_caches()
        self.assertEqual(data_packs.active_chain(),
                         ("global", "ar", "ar-b", "ar-b-pinamar"))
        return fn()

    def _render_pre_move_snapshot(self, fn):
        _env.set_env(self, TURNOS_WEBDATA_DIR=self._snapshot_dir)
        _clear_caches()
        self.assertIsNone(data_packs.active_chain())
        return fn()

    def test_home_page_is_byte_identical(self):
        pack = self._render_pack_mode(lambda: site_identity.render_page("index.html"))
        pre_move = self._render_pre_move_snapshot(lambda: site_identity.render_page("index.html"))
        self.assertEqual(pack, pre_move)

    def test_service_page_is_byte_identical(self):
        pack = self._render_pack_mode(lambda: site_page_service._page_service(PINAMAR_SERVICE_ID))
        pre_move = self._render_pre_move_snapshot(lambda: site_page_service._page_service(PINAMAR_SERVICE_ID))
        self.assertIsNotNone(pack)
        self.assertEqual(pack, pre_move)

    def test_center_page_is_byte_identical(self):
        pack = self._render_pack_mode(lambda: site_page_centers._page_center(PINAMAR_CENTER_SLUG))
        pre_move = self._render_pre_move_snapshot(lambda: site_page_centers._page_center(PINAMAR_CENTER_SLUG))
        self.assertIsNotNone(pack)
        self.assertEqual(pack, pre_move)


class TestExampleTownChainIsGeneric(unittest.TestCase):
    def setUp(self):
        self.addCleanup(_reset_caches)   # registered first, so it runs after the environment is put back
        # Pin these even though they match neutral public behavior: this test
        # proves data-package genericity and must not become sensitive to a
        # future runtime-default change.
        _env.set_env(self, TURNOS_WEBDATA_DIR=None, PACK_CHAIN="global,example-town",
                     TURNOS_PUBLIC_ORIGIN="https://example-town.example",
                     TURNOS_THEME_DEFAULT="humanrounds")
        config._reset_cache_para_tests()
        self._db_patch = patch.object(config, "_db_overrides", return_value={})
        self._db_patch.start()
        self._merge_patch = patch.object(catalog, "merge_with_overlay", _identidad)
        self._merge_patch.start()
        # theme.current() has its OWN 3s-TTL cache, independent of the env
        # var above and possibly already warmed by an earlier test in the
        # suite (module-global dict) -- force it directly instead of racing
        # the TTL, same reasoning as the TURNOS_PUBLIC_ORIGIN override just
        # above ("pinamar" here is a CSS theme id, not a locality leak, but
        # it still needs to not be the string "pinamar" for this assertion).
        self._theme_patch = patch.object(theme, "_cache", {"v": "humanrounds", "t": time.time()})
        self._theme_patch.start()
        _clear_caches()

    def tearDown(self):
        self._theme_patch.stop()
        self._merge_patch.stop()
        self._db_patch.stop()

    def test_chain_resolves_to_global_plus_example_town(self):
        self.assertEqual(data_packs.active_chain(), ("global", "example-town"))

    def test_home_page_has_zero_pinamar_strings(self):
        body, _ctype = site_identity.render_page("index.html")
        html = body.decode("utf-8")
        self.assertNotIn("pinamar", html.lower())
        self.assertIn("Sampleton", html)

    def test_home_page_still_boots_with_real_locality_data(self):
        body, _ctype = site_identity.render_page("index.html")
        html = body.decode("utf-8")
        self.assertIn("Sampleton", html)

    # Phase 2c acceptance (2026-07-30, see docs/architecture/data-packs.md
    # and ~/.claude/plans/large-rewrok-before-open-robust-chipmunk.md Phase 2):
    # sweep MORE rendered pages than just home — a service page, a center
    # page, and the booking wizard's first step (_page_service_appointment,
    # the server-rendered /service/<id>/book — service.js's upload-or-choose-
    # time step). "pinamar" only (not "argentina"): Sampleton IS a real
    # Argentine town in this fixture (packs/example-town/sitio.json's own
    # province is "Buenos Aires"), so "Argentina" is expected, correct
    # content from packs/ar/country.json — not a hardcode leak. This mirrors
    # test_home_page_has_zero_pinamar_strings's own established scope.
    SAMPLETON_SERVICE_ID = "cardiologia-sampleton-hospital"
    SAMPLETON_CENTER_SLUG = "sampleton-hospital"

    def test_service_page_has_zero_pinamar_strings(self):
        body = site_page_service._page_service(self.SAMPLETON_SERVICE_ID)
        self.assertIsNotNone(body)
        html = body.decode("utf-8")
        self.assertNotIn("pinamar", html.lower())
        self.assertIn("Sampleton", html)

    def test_center_page_has_zero_pinamar_strings(self):
        body = site_page_centers._page_center(self.SAMPLETON_CENTER_SLUG)
        self.assertIsNotNone(body)
        html = body.decode("utf-8")
        self.assertNotIn("pinamar", html.lower())
        self.assertIn("Sampleton", html)

    def test_booking_wizard_step1_has_zero_pinamar_strings(self):
        body = site_page_service._page_service_appointment(self.SAMPLETON_SERVICE_ID)
        self.assertIsNotNone(body)
        html = body.decode("utf-8")
        self.assertNotIn("pinamar", html.lower())


if __name__ == "__main__":
    unittest.main()
