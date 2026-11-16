"""test_translate.py — translate.py's cache (translations table, from the
Alembic baseline): reuse-until-source-changes, override wins, zero model
calls on a cache hit and the pack-language gate.

Uses a DEDICATED database (`sana_turnos_test_translate`), same pattern as
test_accounts.py — connects to the maintenance db `postgres` to create/drop
it, so this suite can run in parallel with the others. Skips (with a clear
message) if Postgres isn't reachable, same as every other DB-backed suite
in this repo.

Run: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_translate.py"
"""

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
config.ENV_PATH = ""  # no leer el .env real del checkout (idem test_cuentas.py)

from app.core import db_accounts  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.core import translate  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_translate", seed=lambda: db_accounts.get_user("__forzar_esquema__"))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


def _skip_sin_db(test):
    def wrapper(self, *a, **kw):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        return test(self, *a, **kw)
    return wrapper


class TranslateCacheTests(unittest.TestCase):
    def setUp(self):
        if HAVE_DB:
            with __import__("psycopg").connect(_TEST_URL, autocommit=True) as c:
                with c.cursor() as cur:
                    cur.execute("DELETE FROM translations")
        # the process-wide 30 s copy of the rows goes with them (TASK-19)
        translate.invalidate_rows()
        translate.reset_call_count()

    @_skip_sin_db
    def test_cache_miss_then_hit_zero_calls_second_time(self):
        before = translate.call_count()
        first = translate.translate("svc.x.name", "Laboratorio", "en")
        after_first = translate.call_count()
        self.assertEqual(after_first, before + 1)
        self.assertEqual(first, "[en] Laboratorio")

        second = translate.translate("svc.x.name", "Laboratorio", "en")
        after_second = translate.call_count()
        self.assertEqual(after_second, after_first, "cache hit must not call the model")
        self.assertEqual(second, first)

    @_skip_sin_db
    def test_toggle_twice_is_one_row_zero_calls_on_second_toggle(self):
        # The exact scenario the task calls out: es->en->es->en, only the
        # FIRST en translation ever calls the model.
        translate.translate("svc.y.name", "Vacunación", "en")
        calls_after_first_toggle = translate.call_count()
        # "toggling back to es" is a no-op for THIS module (i18n.py owns
        # the UI-language switch; translate.py only ever caches the en
        # row) — what must hold is that asking for "en" again never
        # re-translates.
        translate.translate("svc.y.name", "Vacunación", "en")
        translate.translate("svc.y.name", "Vacunación", "en")
        self.assertEqual(translate.call_count(), calls_after_first_toggle)

        row = db_catalog.translation_get("svc.y.name", "en")
        self.assertIsNotNone(row)
        self.assertEqual(row["content"], "[en] Vacunación")

    @_skip_sin_db
    def test_source_change_invalidates_the_cache(self):
        translate.translate("svc.z.name", "Radiografía", "en")
        calls1 = translate.call_count()
        # Same key, DIFFERENT source text -> the hash no longer matches ->
        # must re-translate.
        out = translate.translate("svc.z.name", "Radiografía de tórax", "en")
        calls2 = translate.call_count()
        self.assertEqual(calls2, calls1 + 1)
        self.assertEqual(out, "[en] Radiografía de tórax")

    @_skip_sin_db
    def test_override_wins_regardless_of_source_hash(self):
        translate.translate("svc.w.name", "Odontología", "en")
        translate.set_override("svc.w.name", "en", "Dentistry (reviewed)")
        # Even if the source text changes, the override keeps winning until
        # explicitly cleared.
        out = translate.translate("svc.w.name", "Odontología general", "en")
        self.assertEqual(out, "Dentistry (reviewed)")
        calls_before_clear = translate.call_count()

        translate.clear_override("svc.w.name", "en")
        out2 = translate.translate("svc.w.name", "Odontología general", "en")
        self.assertEqual(out2, "[en] Odontología general")
        self.assertEqual(translate.call_count(), calls_before_clear + 1)

    @_skip_sin_db
    def test_localized_text_noop_when_lang_matches_pack_language(self):
        before = translate.call_count()
        out = translate.localized_text("svc.same.name", "Cardiología", "es",
                                       pack_lang="es-AR")
        self.assertEqual(out, "Cardiología")
        self.assertEqual(translate.call_count(), before)
        self.assertIsNone(db_catalog.translation_get("svc.same.name", "es"))

    @_skip_sin_db
    def test_localized_text_translates_when_lang_differs(self):
        out = translate.localized_text("svc.diff.name", "Cardiología", "en",
                                       pack_lang="es-AR")
        self.assertEqual(out, "[en] Cardiología")

    @_skip_sin_db
    def test_localized_preparation_summary_proof_surface(self):
        from app.patient_web import catalog
        items = catalog.preparations()
        self.assertTrue(items, "fixture must have at least one preparation")
        item = items[0]
        out = translate.localized_preparation_summary(item, "en", pack_lang="es-AR")
        self.assertEqual(out, f"[en] {item.get('resumen') or ''}")
        row = db_catalog.translation_get(f"modules.turnos.preparation.{item['id']}.resumen", "en")
        self.assertIsNotNone(row)


class CachedTextTests(unittest.TestCase):
    """`cached_text()` is the ONLY entry point pack_l10n.py's render path is
    allowed to use — it must never call the model, only read a cache that
    scripts/translate_pack.py / translate_warmup.py already filled."""

    def setUp(self):
        if HAVE_DB:
            with __import__("psycopg").connect(_TEST_URL, autocommit=True) as c:
                with c.cursor() as cur:
                    cur.execute("DELETE FROM translations")
        # the process-wide 30 s copy of the rows goes with them (TASK-19)
        translate.invalidate_rows()
        translate.reset_call_count()

    @_skip_sin_db
    def test_cached_text_noop_when_lang_matches_pack_language(self):
        before = translate.call_count()
        out = translate.cached_text("svc.same.name", "Pediatría", "es",
                                    pack_lang="es-AR")
        self.assertEqual(out, "Pediatría")
        self.assertEqual(translate.call_count(), before)
        self.assertIsNone(db_catalog.translation_get("svc.same.name", "es"))

    @_skip_sin_db
    def test_cached_text_hit_after_a_sweep_warmed_it(self):
        translate.translate("svc.warm.name", "Pediatría", "en")
        out = translate.cached_text("svc.warm.name", "Pediatría", "en",
                                    pack_lang="es-AR")
        self.assertEqual(out, "[en] Pediatría")

    @_skip_sin_db
    def test_cached_text_miss_degrades_to_source_zero_calls(self):
        before = translate.call_count()
        out = translate.cached_text("svc.nunca-barrido.name", "Neurología", "en",
                                    pack_lang="es-AR")
        self.assertEqual(out, "Neurología", "a cache miss must show the source, never blank")
        self.assertEqual(translate.call_count(), before, "cached_text never calls the model")

    @_skip_sin_db
    def test_cached_text_stale_hash_degrades_to_current_source(self):
        # The sweep cached the OLD text; the pack content was edited since.
        translate.translate("svc.editado.name", "Rayos X", "en")
        out = translate.cached_text("svc.editado.name", "Radiografía", "en",
                                    pack_lang="es-AR")
        self.assertEqual(out, "Radiografía", "a stale source_hash counts as a miss")


class PackLanguageTests(unittest.TestCase):
    def test_pack_language_defaults_to_es_without_a_chain(self):
        with patch("app.core.data_packs.active_chain", return_value=None):
            self.assertEqual(translate.pack_language(), "es")

    def test_pack_language_reads_the_top_pack_manifest(self):
        with patch("app.core.data_packs.active_chain", return_value=("global", "ar", "ar-b", "ar-b-pinamar")), \
             patch("app.core.data_packs.manifest", return_value={"language": "es-AR"}):
            self.assertEqual(translate.pack_language(), "es")


class BatchProviderDownTests(unittest.TestCase):
    """A batch the provider could not answer is not a malformed batch: the
    one-by-one fallback exists for a misaligned response, and with the
    provider down it only repeated the same failed call for every string
    (40 per batch) — the warmup sweep then outlived a worker's shutdown."""

    def _sweep(self, batch):
        items = [(f"k{n}", f"texto {n}") for n in range(2 * translate.BATCH_SIZE)]
        singles = []

        def one(key, text, lang):
            singles.append(key)
            return "EN " + text

        with patch.object(translate, "_machine_translate_batch",
                          side_effect=batch) as batch_call, \
             patch.object(translate, "translate", side_effect=one), \
             patch.object(translate, "invalidate_rows"):
            out = translate.translate_batch(items, "en")
        return out, singles, batch_call.call_count

    def test_provider_down_skips_each_batch_without_a_single_call(self):
        out, singles, batch_calls = self._sweep(
            translate.TranslationUnavailable("caído"))
        self.assertEqual(out, {})
        self.assertEqual(singles, [])
        self.assertEqual(batch_calls, 2)

    def test_a_misaligned_response_still_goes_one_by_one(self):
        # One line short of the 40 asked for: not aligned, so no line of it
        # may be trusted and every string is asked on its own.
        out, singles, _ = self._sweep(
            lambda texts, lang: ["EN " + t for t in texts][:-1])
        self.assertEqual(len(singles), 2 * translate.BATCH_SIZE)
        self.assertEqual(out["k1"], "EN texto 1")


if __name__ == "__main__":
    unittest.main()
