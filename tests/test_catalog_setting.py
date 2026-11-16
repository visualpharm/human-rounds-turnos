"""core/catalog_setting.CatalogSetting - the saved-setting routine the theme and the sign-in mode share (no database)."""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1")

from app.core import catalog_setting, db_catalog  # noqa: E402


def _setting(default="a", ttl=3.0):
    return catalog_setting.CatalogSetting("probe", "choice", {"a": 1, "b": 2}, lambda: default, "bad choice: %r", ttl)


class CatalogSettingTest(unittest.TestCase):
    def test_saved_valid_value_wins_and_is_normalised(self):
        s = _setting()
        with mock.patch.object(db_catalog, "catalog_get", return_value={"doc": {"choice": " B "}}) as get:
            self.assertEqual(s.current(), "b")
            get.assert_called_once_with("config", "probe")

    def test_nothing_saved_or_invalid_or_db_down_is_the_default(self):
        for row in (None, {"doc": {}}, {"doc": {"choice": "zzz"}}, {"doc": None}):
            s = _setting(default="a")
            with mock.patch.object(db_catalog, "catalog_get", return_value=row):
                self.assertEqual(s.current(), "a", row)
        s = _setting(default="b")
        with mock.patch.object(db_catalog, "catalog_get", side_effect=RuntimeError("down")):
            self.assertEqual(s.current(), "b")

    def test_read_is_cached_for_the_ttl(self):
        s = _setting()
        with mock.patch.object(db_catalog, "catalog_get", return_value={"doc": {"choice": "b"}}) as get:
            s.current()
            s.current()
            self.assertEqual(get.call_count, 1)
            s.cache["t"] -= 10  # older than the ttl
            s.current()
            self.assertEqual(get.call_count, 2)

    def test_default_is_asked_at_read_time(self):
        box = {"d": "a"}
        s = catalog_setting.CatalogSetting("probe", "choice", {"a": 1, "b": 2}, lambda: box["d"], "bad: %r")
        with mock.patch.object(db_catalog, "catalog_get", return_value=None):
            self.assertEqual(s.current(), "a")
            s.cache["v"] = None
            box["d"] = "b"
            self.assertEqual(s.current(), "b")

    def test_save_writes_the_doc_and_forgets_the_cache(self):
        s = _setting()
        s.cache.update(v="a", t=9e18)
        with mock.patch.object(db_catalog, "catalog_upsert") as up:
            self.assertEqual(s.save(" B"), "b")
        up.assert_called_once_with("config", "probe", {"choice": "b"})
        self.assertEqual(s.cache, {"v": None, "t": 0.0})

    def test_save_rejects_unknown_ids_without_writing(self):
        s = _setting()
        with mock.patch.object(db_catalog, "catalog_upsert") as up:
            for bad in (None, "", "zzz"):
                with self.assertRaises(ValueError) as ctx:
                    s.save(bad)
                self.assertEqual(str(ctx.exception), "bad choice: %r" % (bad,))
            up.assert_not_called()

    def test_theme_and_auth_mode_use_it(self):
        from app.auth import mode
        from app.core import theme
        for owner, doc_id in ((theme, "tema"), (mode, "auth_mode")):
            self.assertIs(owner._cache, owner._setting.cache)
            self.assertEqual(owner._setting.doc_id, doc_id)


if __name__ == "__main__":
    unittest.main()
