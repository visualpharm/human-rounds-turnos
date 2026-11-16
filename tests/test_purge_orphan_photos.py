"""scripts/fixes/purge_orphan_photos.py — photos left by records deleted before 2026-11-06.

Until 2026-11-06 a doctor or center deleted for good left its photos on disk
(`data/medico-fotos/<id>.<ext>`, `data/medico-fotos/.originals/<id>/`,
`data/centro-fotos/<slug>.<ext>`). The script removes those whose id is in no
catalog record, active or disabled: dry run by default, `--apply` removes and
prints what it removed; a malformed name is never touched.
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.patient_web import catalog  # noqa: E402
from app.patient_web import doctor_photos  # noqa: E402
import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_orphan_photos", seed=lambda: db_operators.operators_list())


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _script():
    spec = importlib.util.spec_from_file_location(
        "purge_orphan_photos", os.path.join(ROOT, "scripts", "fixes", "purge_orphan_photos.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _portrait(photos, mid):
    """A doctor's portrait as `doctor_photos._save_portrait` leaves it."""
    bundle = os.path.join(photos, ".originals", mid, "portrait-test")
    os.makedirs(bundle)
    for name in ("original", "portrait.webp", "metadata.json"):
        with open(os.path.join(bundle, name), "wb") as f:
            f.write(b"x")
    open(os.path.join(photos, ".originals", mid, "lock"), "a").close()
    os.symlink(os.path.relpath(os.path.join(bundle, "portrait.webp"), photos),
               os.path.join(photos, mid + ".webp"))


def _touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x")


class PurgeOrphanPhotos(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self._tmp = tempfile.mkdtemp(prefix="orphan-photos-")
        self.addCleanup(shutil.rmtree, self._tmp, True)
        webdir = os.path.join(self._tmp, "webdata")
        os.makedirs(webdir)
        with open(os.path.join(webdir, "medicos.json"), "w", encoding="utf-8") as f:
            json.dump({"medicos": [{"id": "med-pack", "nombre": "Ana Pack", "especialidad": "Clínica médica"}]}, f)
        with open(os.path.join(webdir, "centros-salud.json"), "w", encoding="utf-8") as f:
            json.dump({"centros": [{"slug": "centro-pack", "nombre": "Centro Pack"}]}, f)
        _env.set_env(self, TURNOS_WEBDATA_DIR=webdir)
        self.photos = os.path.join(self._tmp, "medico-fotos")
        self.centers = os.path.join(self._tmp, "centro-fotos")
        self.enterContext(unittest.mock.patch.object(doctor_photos, "PHOTOS_DIR", self.photos))
        self.enterContext(unittest.mock.patch.object(doctor_photos, "CENTER_PHOTOS_DIR", self.centers))
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE catalog RESTART IDENTITY CASCADE")
        db_catalog.catalog_upsert("medicos", "med-activo", {"id": "med-activo", "nombre": "Activo"})
        db_catalog.catalog_upsert("medicos", "med-oculto", {"id": "med-oculto", "nombre": "Oculto"}, borrado=True)
        db_catalog.catalog_upsert("centros", "centro-oculto", {"slug": "centro-oculto", "nombre": "Oculto"}, borrado=True)
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        self.addCleanup(catalog.invalidate)
        self.addCleanup(catalog._JSON_CACHE.clear)
        for mid in ("med-pack", "med-activo", "med-oculto", "med-borrado"):
            _portrait(self.photos, mid)
        # an original whose public pointer was already gone
        _touch(os.path.join(self.photos, ".originals", "med-solo-original", "portrait-x", "original"))
        _touch(os.path.join(self.photos, "med-viejo.jpg"))           # a legacy upload of a deleted doctor
        _touch(os.path.join(self.photos, "Raro Nombre.jpg"))         # not an id: never touched
        _touch(os.path.join(self.photos, "notas.txt"))               # not a photo: never touched
        _touch(os.path.join(self.photos, ".originals", "..x", "original"))
        for slug in ("centro-pack", "centro-oculto", "centro-borrado"):
            _touch(os.path.join(self.centers, slug + ".jpg"))
        self.lines = []

    def _run(self, apply):
        return _script().run(apply=apply, emit=self.lines.append)

    def _left(self):
        out = set()
        for base in (self.photos, self.centers):
            for dirpath, dirnames, filenames in os.walk(base):
                for name in dirnames + filenames:
                    path = os.path.join(dirpath, name)
                    if os.path.islink(path) or os.path.isfile(path):
                        out.add(os.path.relpath(path, self._tmp))
        return out

    ORPHANS = {"medicos": ["med-borrado", "med-solo-original", "med-viejo"], "centros": ["centro-borrado"]}

    def test_dry_run_touches_nothing_and_lists_the_orphans(self):
        before = self._left()
        report = self._run(apply=False)
        self.assertEqual(self._left(), before)
        self.assertEqual(report["modo"], "simulacion")
        self.assertEqual(report["huerfanas"], self.ORPHANS)
        self.assertEqual(report["quitadas"], {"medicos": [], "centros": []})
        self.assertTrue(any("med-borrado" in line for line in self.lines))

    def test_apply_removes_only_the_orphans_and_prints_them(self):
        report = self._run(apply=True)
        self.assertEqual(report["quitadas"], self.ORPHANS)
        left = self._left()
        for gone in ("medico-fotos/med-borrado.webp", "medico-fotos/med-viejo.jpg",
                     "centro-fotos/centro-borrado.jpg"):
            self.assertNotIn(gone, left)
        for folder in ("med-borrado", "med-solo-original"):
            self.assertFalse(os.path.lexists(os.path.join(self.photos, ".originals", folder)))
        for kept in ("med-pack", "med-activo", "med-oculto"):
            self.assertIn("medico-fotos/%s.webp" % kept, left, "a catalog doctor keeps its portrait")
            self.assertTrue(os.path.isdir(os.path.join(self.photos, ".originals", kept)))
        for kept in ("centro-pack", "centro-oculto"):
            self.assertIn("centro-fotos/%s.jpg" % kept, left, "a catalog center keeps its photo")
        for odd in ("medico-fotos/Raro Nombre.jpg", "medico-fotos/notas.txt",
                    "medico-fotos/.originals/..x/original"):
            self.assertIn(odd, left, "a name that is not an id is never touched")
        self.assertIn("ignoradas", report)
        self.assertTrue(any("quitada" in line and "med-borrado" in line for line in self.lines))

    def test_apply_twice_finds_nothing_more(self):
        self._run(apply=True)
        report = self._run(apply=True)
        self.assertEqual(report["huerfanas"], {"medicos": [], "centros": []})

    def test_a_record_created_meanwhile_keeps_its_photo(self):
        """The catalog is read again for each orphan right before its removal
        (for a doctor, under the portrait lock): a record that appeared after
        the listing keeps its photo."""
        script = _script()
        real = script._known_ids

        def appears_after_listing(tipo, _calls=[]):
            _calls.append(tipo)
            if len(_calls) > 2:
                db_catalog.catalog_upsert("medicos", "med-borrado", {"id": "med-borrado", "nombre": "Vuelve"})
                catalog.invalidate()
            return real(tipo)

        with unittest.mock.patch.object(script, "_known_ids", side_effect=appears_after_listing):
            report = script.run(apply=True, emit=self.lines.append)
        self.assertNotIn("med-borrado", report["quitadas"]["medicos"])
        self.assertTrue(os.path.lexists(os.path.join(self.photos, "med-borrado.webp")))

    def test_an_empty_catalog_refuses_to_remove_anything(self):
        """A catalog that lists no doctor (a misconfigured pack, a wrong
        database) would make every portrait an orphan: refused, nothing goes."""
        script = _script()
        with unittest.mock.patch.object(script, "_known_ids", return_value=set()):
            with self.assertRaises(SystemExit):
                script.run(apply=True, emit=self.lines.append)
        self.assertIn("medico-fotos/med-borrado.webp", self._left())

    def test_main_prints_the_report(self):
        out = io.StringIO()
        with unittest.mock.patch("sys.stdout", out):
            self.assertEqual(_script().main([]), 0)
        self.assertIn('"modo": "simulacion"', out.getvalue())


if __name__ == "__main__":
    unittest.main()
