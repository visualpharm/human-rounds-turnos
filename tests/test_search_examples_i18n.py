"""test_search_examples_i18n.py — the per-language extension to
scripts/search_examples.py (2026-08-04): a visitor reading the site in
English has to search, and get results, in English. `pick_all_languages()`
adds a second dimension to the six chips under the search box — one set per
enabled language, each generated from and verified against THAT language's
own corpus (not a translation of the Spanish chips) — and this file is the
guard that keeps that promise honest:

  - every term `pick_all_languages()` chooses for a language really does
    return a result when searched in THAT language's own corpus (the same
    guarantee tests/search-examples.test.mjs already gives the canonical
    Spanish list, just extended across the language dimension);
  - a language whose translation cache is still cold gets SKIPPED, never
    silently filled with Spanish words under that language's key.

Runs against a dedicated throwaway database (its own `translations` table,
same pattern as test_translate.py) with `TURNOS_FAKE_AI=1` (CI's own
setting) — this test seeds the one field it needs itself (via
`translate.translate_batch`, the exact call `scripts/translate_pack.py`
makes), so it never depends on a real OpenRouter-translated cache being warm
already, and is exactly as reproducible in CI as it is on a laptop.

Run: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_search_examples_i18n.py"
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")
# NOT `PACK_CHAIN` here: `unittest discover` imports every test MODULE up
# front, before any test runs — a module-level `os.environ.setdefault(...)`
# would poison every OTHER file's tests for the rest of the process (nothing
# ever undoes it, because the per-test setUp/tearDown below only restores
# whatever PACK_CHAIN happened to be already sitting in the environment at
# import time, which by then is this value). `setUp()` sets/restores it
# per-test instead — the same reason test_golden_pages.py never sets it at
# module level either.

from app.core import config  # noqa: E402
config.ENV_PATH = ""  # no leer el .env real del checkout (idem test_translate.py)

from app.core import db_accounts  # noqa: E402
from app.core import translate  # noqa: E402
from app.core import pack_l10n  # noqa: E402
from app.core import i18n  # noqa: E402
from app.patient_web import site_base

import _dbtest  # noqa: E402
import search_examples as se  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_search_examples_i18n",
                          seed=lambda: db_accounts.get_user("__forzar_esquema__"))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


class SearchExamplesPerLanguageTests(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        # se._corpora() (called by every method below, directly and via
        # pick_all_languages()) monkeypatches site_base._with_overlay to
        # skip the DB catalog overlay — by design, since what this generator
        # writes has to be true of the PACK alone (see _corpora()'s own
        # docstring). That monkeypatch has no restore of its own (the CLI
        # script runs once and exits), which is harmless there but would
        # leak into every test module that runs AFTER this one in the same
        # `unittest discover` process otherwise — restore it ourselves.
        # Cleanups run last-in first-out: the overlay and the environment (PACK_CHAIN here, LANGUAGES in the
        # tests that set it) are put back first, then the caches are dropped.
        self.addCleanup(pack_l10n.invalidate_memo)
        self.addCleanup(config._reset_cache_para_tests)
        self.addCleanup(setattr, site_base, "_with_overlay", site_base._with_overlay)
        _env.set_env(self, PACK_CHAIN="global,example-town")
        config._reset_cache_para_tests()
        pack_l10n.invalidate_memo()

    def _warm_service_names(self, lang):
        """Fake-translates every service `nombre` into `lang` — the one
        field `pick()`'s specialty/study flavors need to see something other
        than the pack's own Spanish. Same write path (`translate_batch` ->
        `db_catalog.translation_upsert`) `scripts/translate_pack.py` uses on a real
        sweep; scoped to one field here so the test stays fast."""
        services, _doctors, _centers = se._corpora()
        entries = [
            (pack_l10n.key("servicios", s.get("id") or s.get("slug") or s.get("nombre"), "nombre"),
             s.get("nombre"))
            for s in services if s.get("nombre")
        ]
        translate.translate_batch(entries, lang)
        pack_l10n.invalidate_memo()

    def test_generated_language_chips_all_verify_against_their_own_corpus(self):
        self._warm_service_names("en")
        _env.set_env(self, LANGUAGES="es,en")
        results, pack_lang = se.pick_all_languages(verbose=False)

        self.assertIn(pack_lang, results)
        self.assertIn("en", results,
            "the cache was just warmed for 'en' — pick_all_languages() "
            "should not treat it as cold")
        self.assertTrue(results["en"], "no 'en' chip verified at all")

        for lang, chosen in results.items():
            with self.subTest(lang=lang):
                token = None
                if lang != pack_lang:
                    _, token = i18n.bind_lang(lang)
                try:
                    services, doctors, centers = se._corpora()
                finally:
                    if token is not None:
                        i18n.unbind(token)
                counts = se._probe(chosen, services, doctors, centers)
                for term in chosen:
                    self.assertGreater(
                        counts.get(term, 0), 0,
                        f'"{term}" ({lang}) returns nothing against its own '
                        f'corpus — re-run scripts/search_examples.py')

    def test_examples_are_distinct_per_language(self):
        self._warm_service_names("en")
        _env.set_env(self, LANGUAGES="es,en")
        results, _pack_lang = se.pick_all_languages(verbose=False)
        for lang, chosen in results.items():
            with self.subTest(lang=lang):
                seen = {t.lower() for t in chosen}
                self.assertEqual(len(seen), len(chosen),
                                  f"{lang}: duplicate chip in {chosen}")

    def test_cold_cache_is_skipped_not_silently_written_as_spanish(self):
        # No warm-up this time: the 'en' translations table is empty (a
        # fresh per-test-module DB — see _dbtest.py), so the corpus bound to
        # 'en' is byte-identical to the pack's own Spanish. That must be
        # SKIPPED, never written under the 'en' key as if it had verified.
        _env.set_env(self, LANGUAGES="es,en")
        results, _pack_lang = se.pick_all_languages(verbose=False)
        self.assertNotIn("en", results)


if __name__ == "__main__":
    unittest.main()
