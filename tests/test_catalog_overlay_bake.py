"""The catalog an installation edited, written back as a data pack (scripts/fixes/bake_catalog_pack.py).

pinamar.humanrounds.org → salud.pinamar.gob.ar (2026-10-26): the team's
edits (agendas, opening dates, hidden services, new doctors) live in the
catalog overlay of the test installation's database; salud receives the
catalog as a pack. Proves: the baked pack shows exactly what the live site
shows (edits applied, disabled entries out, panel-created entries in), no
doctor email reaches the pack, the panel-uploaded photos of live entries come
along, the pack inventory verifies, the source pack is left untouched, what
cannot travel in a pack is named, and a second run gives the same bytes.

Run:  python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_catalog_overlay_bake.py
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
FIXES = os.path.join(ROOT, "scripts", "fixes")

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app.core import data_packs  # noqa: E402
from app.core import db  # noqa: E402
from app.core import db_catalog  # noqa: E402
import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_catalog_bake")
HAVE_DB, DB_SKIP_REASON = False, None


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _tree_hashes(directory):
    out = {}
    for current, _dirs, names in os.walk(directory):
        for name in names:
            path = os.path.join(current, name)
            with open(path, "rb") as f:
                out[os.path.relpath(path, directory)] = hashlib.sha256(f.read()).hexdigest()
    return out


def _doctor(mid, **extra):
    d = {"id": mid, "nombre": "Dra. " + mid, "especialidad": "Clínica médica", "centro": "caps-norte",
         "email": mid + "@correo.example", "agenda": {"dias": ["lunes"], "desde": "08:00", "hasta": "12:00"}}
    d.update(extra)
    return d


class TestBake(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de test")
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.pack = os.path.join(tmp, "ar-b-pueblo")
        self.data = os.path.join(tmp, "data")
        self.out = os.path.join(tmp, "salida")
        _write(os.path.join(self.pack, "pack.json"), {"id": "ar-b-pueblo", "level": "town"})
        _write(os.path.join(self.pack, "medicos.json"), {
            "_nota": "fuente: planilla del hospital",
            "medicos": [_doctor("ana-paz"), _doctor("beto-luz"), _doctor("rol-tecnico", rol="Técnico/a")]})
        _write(os.path.join(self.pack, "servicios.json"), {"servicios": [
            {"id": "clinica-norte", "nombre": "Clínica médica", "centro": "caps-norte", "modo": "solicitud"},
            {"id": "vacunas-norte", "nombre": "Vacunas", "centro": "caps-norte", "modo": "walkin"}]})
        _write(os.path.join(self.pack, "centros-salud.json"), {"centros": [
            {"slug": "caps-norte", "name": "CAPS Norte", "email": "caps@pinamar.example"}]})
        with open(os.path.join(self.pack, "._medicos.json"), "wb") as f:
            f.write(b"\x00\x05\x16\x07")   # a macOS sidecar never travels
        data_packs.write_inventory(self.pack)
        for rel, payload in (("medico-fotos/.originals/ana-paz/portrait-x1/portrait.webp", b"RIFF0000WEBPana"),
                             ("medico-fotos/.originals/ana-paz/portrait-x1/original", b"\xff\xd8\xffcrudo"),
                             ("medico-fotos/beto-luz.jpg", b"\xff\xd8\xffbeto"),
                             ("afuera/secreto.webp", b"RIFF0000WEBPno"),
                             ("medico-fotos/nueva-doc.jpg", b"\xff\xd8\xffnueva"),
                             ("centro-fotos/caps-norte.jpg", b"\xff\xd8\xffcaps")):
            path = os.path.join(self.data, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(payload)
        # A processed portrait is published as a symlink into its private bundle
        # (doctor_photos._save_portrait); one pointing outside the folder never travels.
        fotos = os.path.join(self.data, "medico-fotos")
        os.symlink(os.path.join(".originals", "ana-paz", "portrait-x1", "portrait.webp"),
                   os.path.join(fotos, "ana-paz.webp"))
        os.symlink(os.path.join(self.data, "afuera", "secreto.webp"), os.path.join(fotos, "rol-tecnico.webp"))
        self.enterContext(_env.patched_env(TURNOS_WEBDATA_DIR=self.pack))
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM catalog")
        from app.patient_web import catalog, doctor_photos
        self.catalog = catalog
        self.enterContext(patch.object(doctor_photos, "PHOTOS_DIR", os.path.join(self.data, "medico-fotos")))
        self.enterContext(patch.object(doctor_photos, "CENTER_PHOTOS_DIR", os.path.join(self.data, "centro-fotos")))
        # The team's edits on the test installation.
        db_catalog.catalog_upsert("medicos", "ana-paz", _doctor(
            "ana-paz", agenda={"dias": ["martes"], "desde": "09:00", "hasta": "13:00"},
            apertura_turnos="2026-11-02", email="ana.paz@gmail.example"))
        db_catalog.catalog_upsert("medicos", "beto-luz", _doctor("beto-luz"), borrado=True)
        db_catalog.catalog_upsert("medicos", "nueva-doc", _doctor("nueva-doc"))
        db_catalog.catalog_upsert("medicos", "prueba-qa", _doctor("prueba-qa"))
        db_catalog.catalog_upsert("servicios", "vacunas-norte", {"id": "vacunas-norte"}, borrado=True)
        db_catalog.catalog_upsert("servicios", "clinica-norte", {
            "id": "clinica-norte", "nombre": "Clínica médica", "centro": "caps-norte", "modo": "solicitud",
            "cartel": "Traé la orden"})
        db_catalog.catalog_upsert("config", "feriados", {"items": [{"fecha": "2026-11-23", "nombre": "Soberanía"}]})
        catalog.invalidate()
        self.addCleanup(catalog.invalidate)
        self.source_before = _tree_hashes(self.pack)
        if FIXES not in sys.path:
            sys.path.insert(0, FIXES)
        import bake_catalog_pack
        self.script = bake_catalog_pack

    def bake(self, **kw):
        lines = []
        code = self.script.run(self.out, pack_dir=self.pack, skip_ids=kw.pop("skip_ids", ["prueba-qa"]),
                               emit=lines.append, **kw)
        return code, lines

    def baked(self, fname):
        with open(os.path.join(self.out, "packs", "ar-b-pueblo", fname), encoding="utf-8") as f:
            return json.load(f)

    def test_pack_is_what_the_live_site_shows(self):
        code, lines = self.bake()
        self.assertEqual(code, 0)
        medicos = self.baked("medicos.json")
        self.assertEqual(medicos["_nota"], "fuente: planilla del hospital")
        by_id = {d["id"]: d for d in medicos["medicos"]}
        self.assertEqual(set(by_id), {"ana-paz", "rol-tecnico", "nueva-doc"})
        self.assertEqual(by_id["ana-paz"]["agenda"]["dias"], ["martes"])
        self.assertEqual(by_id["ana-paz"]["apertura_turnos"], "2026-11-02")
        servicios = {s["id"]: s for s in self.baked("servicios.json")["servicios"]}
        self.assertEqual(set(servicios), {"clinica-norte"})
        self.assertEqual(servicios["clinica-norte"]["cartel"], "Traé la orden")
        # Untouched by the overlay: the pack's own bytes.
        self.assertEqual(self.baked("centros-salud.json")["centros"][0]["slug"], "caps-norte")

    def test_no_doctor_email_reaches_the_pack(self):
        self.bake()
        with open(os.path.join(self.out, "packs", "ar-b-pueblo", "medicos.json"), encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn("@", raw)
        self.assertNotIn('"email"', raw)

    def test_photos_of_live_entries_come_along(self):
        _, lines = self.bake()
        out_fotos = os.path.join(self.out, "data", "medico-fotos")
        self.assertEqual(sorted(os.listdir(out_fotos)), ["ana-paz.webp", "nueva-doc.jpg"])
        portrait = os.path.join(out_fotos, "ana-paz.webp")
        self.assertFalse(os.path.islink(portrait))
        with open(portrait, "rb") as f:
            self.assertEqual(f.read(), b"RIFF0000WEBPana")
        self.assertEqual(os.listdir(os.path.join(self.out, "data", "centro-fotos")), ["caps-norte.jpg"])
        self.assertIn('"fotos": 3', lines[-1])

    def test_inventory_verifies_and_source_is_untouched(self):
        self.bake()
        out_pack = os.path.join(self.out, "packs", "ar-b-pueblo")
        data_packs._verify_inventory("ar-b-pueblo", out_pack)
        self.assertNotIn("._medicos.json", os.listdir(out_pack))
        self.assertEqual(_tree_hashes(self.pack), self.source_before)

    def test_names_what_does_not_travel_in_a_pack(self):
        _, lines = self.bake()
        report = json.loads(lines[-1])
        self.assertEqual(report["fuera_del_pack"], {"config/feriados": 1})
        self.assertEqual(report["medicos"], {"en_pack": 3, "editados": 1, "nuevos": 1,
                                             "deshabilitados_fuera": 1, "omitidos": 1})

    def test_second_run_gives_the_same_bytes_and_never_overwrites_by_accident(self):
        self.assertEqual(self.bake()[0], 0)
        first = _tree_hashes(self.out)
        self.assertEqual(self.bake()[0], 2)
        self.assertEqual(self.bake(force=True)[0], 0)
        self.assertEqual(_tree_hashes(self.out), first)

    def test_refuses_an_output_on_top_of_what_the_site_serves(self):
        # --force removes what it replaces: pointed at the live pack, the app or
        # the photo folders it would delete them.
        before = _tree_hashes(self.data)
        for out in (self.pack, os.path.join(self.pack, "salida"), self.data,
                    os.path.join(self.data, "medico-fotos"), self.script.ROOT,
                    os.path.dirname(self.pack)):
            lines = []
            code = self.script.run(out, pack_dir=self.pack, force=True, emit=lines.append)
            self.assertEqual(code, 2, out)
            self.assertIn("no puede estar dentro", lines[-1])
        self.assertEqual(_tree_hashes(self.data), before)
        self.assertEqual(_tree_hashes(self.pack), self.source_before)

    def test_a_failed_rewrite_leaves_the_previous_output(self):
        self.assertEqual(self.bake()[0], 0)
        first = _tree_hashes(self.out)
        with patch.object(self.script.data_packs, "write_inventory", side_effect=OSError("disco lleno")):
            with self.assertRaises(OSError):
                self.bake(force=True)
        self.assertEqual(_tree_hashes(self.out), first)
        self.assertFalse([n for n in os.listdir(self.out) if n.startswith(".bake-")])

    def test_refuses_an_overlay_it_cannot_express(self):
        db_catalog.catalog_upsert("conocimiento", "como-pedir", {"id": "como-pedir", "title": "Cómo pedir"})
        self.catalog.invalidate()
        code, lines = self.bake()
        self.assertEqual(code, 3)
        self.assertIn("conocimiento", "\n".join(lines))
        self.assertFalse(os.path.exists(os.path.join(self.out, "packs")))


if __name__ == "__main__":
    unittest.main()
