"""test_pack_l10n.py — pack_l10n.py's field map and the localize()/entries()
walk: which fields translate, that a LIST field (palabras) round-trips as a
list translated term by term (never joined into prose and re-split), that a
cache miss degrades to the pack's own language, and that a request in the
pack's own language is a true no-op (identity return, zero DB rows, zero
model calls) — the invariant the golden-page byte-identity tests lean on.

Uses a DEDICATED database (`sana_turnos_test_pack_l10n`), same pattern as
test_translate.py.

Run: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_pack_l10n.py"
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
config.ENV_PATH = ""  # no leer el .env real del checkout (idem test_translate.py)

from app.core import db_accounts  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.core import pack_l10n  # noqa: E402
from app.core import translate  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_pack_l10n", seed=lambda: db_accounts.get_user("__forzar_esquema__"))


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


class FieldMapTests(unittest.TestCase):
    def test_palabras_is_translatable_for_servicios(self):
        self.assertIn("palabras", pack_l10n.FIELDS["servicios"])

    def test_medicos_does_not_carry_palabras(self):
        # No pack's medicos.json has a `palabras` field (checked every pack
        # on disk) — a doctor's translatable surface stays just the
        # specialty label, never search vocabulary.
        self.assertNotIn("palabras", pack_l10n.FIELDS["medicos"])


class LocalizeListFieldTests(unittest.TestCase):
    def setUp(self):
        if HAVE_DB:
            with __import__("psycopg").connect(_TEST_URL, autocommit=True) as c:
                with c.cursor() as cur:
                    cur.execute("DELETE FROM translations")
        translate.reset_call_count()
        pack_l10n.invalidate_memo()

    @_skip_sin_db
    def test_list_field_translates_term_by_term_and_stays_a_list(self):
        rows = [{"id": "svc1", "nombre": "Laboratorio",
                 "palabras": ["análisis", "sangre"]}]
        # Warm the cache exactly like scripts/translate_pack.py would — the
        # render path (localize -> cached_text) never calls the model itself.
        for key, text in pack_l10n.entries("servicios", rows):
            translate.translate(key, text, "en")

        with patch("app.core.translate.pack_language", return_value="es"):
            out = pack_l10n.localize("servicios", rows, lang="en")

        self.assertIsInstance(out[0]["palabras"], list)
        self.assertEqual(out[0]["palabras"], ["[en] análisis", "[en] sangre"])
        # Never joined into one string and re-split.
        self.assertEqual(len(out[0]["palabras"]), 2)

    @_skip_sin_db
    def test_a_miss_on_one_list_item_degrades_to_source_for_that_item_only(self):
        rows = [{"id": "svc2", "nombre": "Vacunación",
                 "palabras": ["vacuna", "inmunización"]}]
        # Only warm the cache for the FIRST term.
        translate.translate(pack_l10n.key("servicios", "svc2", "palabras", 0),
                             "vacuna", "en")

        with patch("app.core.translate.pack_language", return_value="es"):
            out = pack_l10n.localize("servicios", rows, lang="en")

        self.assertEqual(out[0]["palabras"][0], "[en] vacuna")
        # Second term never got a cache entry -> degrades to the original
        # Spanish word, never blank, never raised.
        self.assertEqual(out[0]["palabras"][1], "inmunización")

    @_skip_sin_db
    def test_spanish_request_is_a_complete_noop(self):
        rows = [{"id": "svc3", "nombre": "Cardiología",
                 "palabras": ["corazón", "cardiólogo"]}]
        before = translate.call_count()

        with patch("app.core.translate.pack_language", return_value="es"):
            out = pack_l10n.localize("servicios", rows, lang="es")

        # Identity: the very same list object, never a copy.
        self.assertIs(out, rows)
        self.assertEqual(translate.call_count(), before, "no model call")
        self.assertIsNone(db_catalog.translation_get(
            pack_l10n.key("servicios", "svc3", "palabras", 0), "es"),
            "no DB row written for a same-language request")

    @_skip_sin_db
    def test_scalar_field_still_works_alongside_the_new_list_field(self):
        rows = [{"id": "svc4", "nombre": "Odontología",
                 "palabras": ["diente", "muela"]}]
        for key, text in pack_l10n.entries("servicios", rows):
            translate.translate(key, text, "en")

        with patch("app.core.translate.pack_language", return_value="es"):
            out = pack_l10n.localize("servicios", rows, lang="en")

        self.assertEqual(out[0]["nombre"], "[en] Odontología")
        self.assertEqual(out[0]["palabras"], ["[en] diente", "[en] muela"])


if __name__ == "__main__":
    unittest.main()
