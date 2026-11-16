"""Staff accounts from one installation to another (scripts/fixes/staff_transfer.py).

pinamar.humanrounds.org → salud.pinamar.gob.ar (2026-10-26): the export
carries who the staff are and what they may do, never how they used to log in;
the import, run by the municipality on its own server, creates the accounts
the portal then binds by email. Proves: no secret leaves in the file, the
import is a dry run unless told otherwise, it never duplicates an account or
guesses a doctor or a role the target does not have, a second run changes
nothing, and the first portal login of an imported person binds to it.

Run:  python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_staff_transfer.py
"""

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

from app.core import db  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_auth  # noqa: E402
from app.core import permissions  # noqa: E402
import _dbtest  # noqa: E402
import _lifespan  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_staff_transfer")
HAVE_DB, DB_SKIP_REASON = False, None


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    # The portal-login test enters the composed app's lifespan (tests/_lifespan.py).
    _lifespan.no_background_services()
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


_PEER = "fd12::10"
_PORTAL_ENV = {
    "TURNOS_STAFF_PORTAL_LOGIN": "1",
    "TURNOS_STAFF_PORTAL_TOTP": "",
    "TURNOS_PORTAL_IDENTITY_MODE": "private-gateway-v1",
    "TURNOS_PORTAL_IDENTITY_TRUSTED_PEERS": json.dumps([_PEER]),
    "TURNOS_PORTAL_IDENTITY_GATEWAY_HOST": "",
    "TURNOS_PORTAL_IDENTITY_BINDINGS": "",
    "TURNOS_PUBLIC_ORIGIN": "https://salud.example.invalid",
    "TURNOS_ADMIN_SECRET": "staff-transfer-test-signing-key",
}
_PASSWORD = "origen-1"


def _script():
    if FIXES not in sys.path:
        sys.path.insert(0, FIXES)
    import staff_transfer
    return staff_transfer


def _wipe():
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM operator_access")
        cur.execute("DELETE FROM admin_account_security")
        cur.execute("DELETE FROM operator_username_history")
        cur.execute("DELETE FROM operators")


class _Base(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de test")
        config._reset_cache_para_tests()
        self.addCleanup(config._reset_cache_para_tests)
        _wipe()
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.file = os.path.join(self.tmp, "personal.json")
        self.script = _script()

    def source(self):
        """The test installation: real staff, a doctor, one without email, one QA account."""
        db_operators.operator_create("ana.perez", _PASSWORD, "admin", "JBSWY3DPEHPK3PXP",
                           email="Ana.Perez@Pinamar.example")
        permissions.set_overrides("ana.perez", ["reportes"], [])
        db_operators.operator_create("beto.gomez", _PASSWORD, "admisionista", None,
                           email="beto@pinamar.example", centros=["caps-norte"])
        db_operators.operator_create("carla.diaz", _PASSWORD, "medico", None, medico_id="carla-diaz",
                           email="carla@pinamar.example")
        db_operators.operator_create("dario.sosa", _PASSWORD, "medico", None, medico_id="dario-sosa",
                           email="dario@pinamar.example", activo=False)
        db_operators.operator_create("eva.ruiz", _PASSWORD, "admisionista", None)
        # A role outside operators.role (kept in operator_access) travels as well.
        db_operators.operator_create("fede.agendas", _PASSWORD, "gestor_agendas", None, email="fede@pinamar.example")
        db_operators.operator_create("qa.prueba", _PASSWORD, "admisionista", None, email="qa@example.org")
        db_operators.operator_feed_token_issue("carla.diaz")

    def export(self, **kw):
        lines = []
        code = self.script.export(self.file, skip_usernames=kw.pop("skip", ["qa.prueba"]),
                                  emit=lines.append, **kw)
        return code, lines

    def import_(self, apply=False, doctors=("carla-diaz", "dario-sosa"), centers=("caps-norte",)):
        lines = []
        with patch.object(self.script, "_doctor_ids", lambda: set(doctors)), \
                patch.object(self.script, "_center_ids", lambda: set(centers)):
            code = self.script.import_file(self.file, apply=apply, emit=lines.append)
        return code, lines

    def add_entries(self, *entries):
        with open(self.file, encoding="utf-8") as f:
            data = json.load(f)
        base = {"activo": True, "medico_id": None, "centros": [],
                "permisos_agregados": [], "permisos_quitados": []}
        data["personal"].extend(dict(base, **e) for e in entries)
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump(data, f)


class TestExport(_Base):
    def test_file_carries_who_and_what_never_how_they_logged_in(self):
        self.source()
        code, lines = self.export()
        self.assertEqual(code, 0)
        with open(self.file, encoding="utf-8") as f:
            raw = f.read()
        data = json.loads(raw)
        people = {p["usuario"]: p for p in data["personal"]}
        self.assertEqual(set(people), {"ana.perez", "beto.gomez", "carla.diaz", "dario.sosa", "eva.ruiz",
                                       "fede.agendas"})
        self.assertEqual(people["fede.agendas"]["rol"], "gestor_agendas")
        self.assertEqual(people["ana.perez"]["rol"], "admin")
        self.assertEqual(people["ana.perez"]["permisos_agregados"], ["reportes"])
        self.assertEqual(people["beto.gomez"]["centros"], ["caps-norte"])
        self.assertEqual(people["carla.diaz"]["medico_id"], "carla-diaz")
        self.assertFalse(people["dario.sosa"]["activo"])
        self.assertIsNone(people["eva.ruiz"]["email"])
        for p in data["personal"]:
            self.assertEqual(set(p), set(self.script.FIELDS))
        for secret in ("pass_hash", "totp", "JBSWY3DPEHPK3PXP", "feed", "token", "cuit", "portal",
                       "argon2", _PASSWORD, "ultimo_acceso", "creado"):
            self.assertNotIn(secret, raw)
        # The report says how many, never an email.
        self.assertFalse(any("@" in line for line in lines))

    def test_refuses_to_overwrite_an_existing_file(self):
        self.source()
        self.assertEqual(self.export()[0], 0)
        self.assertEqual(self.export()[0], 2)


class TestImport(_Base):
    def exported_then_target(self):
        """Export from the source, then turn the database into the target (salud):
        empty except for one account that already exists there."""
        self.source()
        self.assertEqual(self.export()[0], 0)
        _wipe()
        db_operators.operator_create("beto", "x" * 20, "admisionista", None, email="BETO@pinamar.example")

    def test_dry_run_writes_nothing(self):
        self.exported_then_target()
        code, lines = self.import_()
        self.assertEqual(code, 0)
        self.assertIn('"modo": "simulacion"', lines[-1])
        self.assertEqual([o["usuario"] for o in db_operators.operators_list()], ["beto"])
        self.assertIn("crear     fede.agendas", "\n".join(lines))

    def test_apply_creates_without_duplicates_and_twice_changes_nothing(self):
        self.exported_then_target()
        code, lines = self.import_(apply=True)
        self.assertEqual(code, 0)
        report = json.loads(lines[-1])
        self.assertEqual(report["creados"], 5)
        self.assertEqual(report["ya_existian"], 1)
        ops = {o["usuario"]: o for o in db_operators.operators_list()}
        self.assertEqual(set(ops), {"beto", "ana.perez", "carla.diaz", "dario.sosa", "eva.ruiz", "fede.agendas"})
        self.assertEqual(ops["fede.agendas"]["rol"], "gestor_agendas")
        self.assertEqual((ops["ana.perez"]["rol"], ops["ana.perez"]["activo"]), ("admin", True))
        self.assertEqual(ops["ana.perez"]["permisos_agregados"], ["reportes"])
        self.assertFalse(ops["ana.perez"]["tiene_2fa"])
        self.assertEqual(ops["carla.diaz"]["medico_id"], "carla-diaz")
        self.assertFalse(ops["dario.sosa"]["activo"])
        self.assertNotIn("beto.gomez", ops)        # same person as «beto»: matched by email
        self.assertEqual(report["sin_email"], 1)
        # The old password opens nothing: the portal is the only way in.
        self.assertIsNone(db_operators.operator_verify_password("ana.perez", _PASSWORD))
        self.assertFalse(any("@" in line for line in lines))
        _, again = self.import_(apply=True)
        report = json.loads(again[-1])
        self.assertEqual((report["creados"], report["ya_existian"]), (0, 6))

    def test_never_guesses_a_doctor_or_a_role_the_target_lacks(self):
        self.source()
        self.assertEqual(self.export()[0], 0)
        with open(self.file, encoding="utf-8") as f:
            data = json.load(f)
        data["personal"].append({"usuario": "jefa.rol", "rol": "jefatura-inexistente", "activo": True,
                                 "email": "jefa@pinamar.example", "medico_id": None, "centros": [],
                                 "permisos_agregados": [], "permisos_quitados": []})
        data["personal"].append({"usuario": "otra.ana", "rol": "admisionista", "activo": True,
                                 "email": "ana.perez@pinamar.example", "medico_id": None, "centros": [],
                                 "permisos_agregados": [], "permisos_quitados": []})
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump(data, f)
        _wipe()
        code, lines = self.import_(apply=True, doctors=("dario-sosa",))
        self.assertEqual(code, 1)
        report = json.loads(lines[-1])
        names = {o["usuario"] for o in db_operators.operators_list()}
        self.assertNotIn("carla.diaz", names)      # her doctor is not in the target catalog
        self.assertNotIn("jefa.rol", names)        # no such role there
        self.assertNotIn("ana.perez", names)       # two people claim one email
        self.assertNotIn("otra.ana", names)
        self.assertIn("dario.sosa", names)
        self.assertEqual(report["conflictos"], 4)
        text = "\n".join(lines)
        self.assertIn("medico_no_esta", text)
        self.assertIn("rol_desconocido", text)
        self.assertIn("email_repetido", text)

    def test_the_dry_run_already_names_overrides_and_centers_the_target_would_refuse(self):
        self.exported_then_target()
        self.add_entries(
            {"usuario": "typo.permiso", "rol": "admisionista", "email": "typo@pinamar.example",
             "permisos_agregados": ["reportes-con-typo"]},
            {"usuario": "doble.permiso", "rol": "admisionista", "email": "doble@pinamar.example",
             "permisos_agregados": ["reportes"], "permisos_quitados": ["reportes"]},
            {"usuario": "otro.centro", "rol": "admisionista", "email": "otro@pinamar.example",
             "centros": ["caps-que-no-esta"]})
        code, lines = self.import_()
        self.assertEqual(code, 1)
        text = "\n".join(lines)
        self.assertIn("CONFLICTO typo.permiso — permisos_invalidos", text)
        self.assertIn("CONFLICTO doble.permiso — permisos_invalidos", text)
        self.assertIn("CONFLICTO otro.centro — centro_no_esta_en_el_catalogo", text)
        code, _ = self.import_(apply=True)
        names = {o["usuario"] for o in db_operators.operators_list()}
        self.assertFalse(names & {"typo.permiso", "doble.permiso", "otro.centro"})
        self.assertIn("ana.perez", names)

    def test_an_account_is_created_whole_or_not_at_all(self):
        # A failure after the account row (overrides, 2FA deadline) rolls the
        # account back: a second run creates it whole instead of calling a
        # half-made account «existe».
        self.exported_then_target()
        with patch.object(db_auth, "admin_security_set_deadline", side_effect=RuntimeError("se cortó")):
            with self.assertRaises(RuntimeError):
                self.import_(apply=True)
        self.assertEqual([o["usuario"] for o in db_operators.operators_list()], ["beto"])
        code, lines = self.import_(apply=True)
        self.assertEqual(code, 0)
        ops = {o["usuario"]: o for o in db_operators.operators_list()}
        self.assertEqual(ops["ana.perez"]["permisos_agregados"], ["reportes"])

    def test_first_portal_login_binds_the_imported_account(self):
        self.exported_then_target()
        self.import_(apply=True)
        _env.set_env(self, _PORTAL_ENV)
        config._reset_cache_para_tests()
        from app import main as app_module
        from app.cuentas import portal_identity
        from fastapi.testclient import TestClient
        client = self.enterContext(TestClient(portal_identity.SocketPeerApp(app_module.app),
                                              client=(_PEER, 50000), follow_redirects=False))
        res = client.get("/api/admin/whoami", headers={
            "X-User-Id": "portal-ana", "X-User-Email": "ana.perez@pinamar.example",
            "X-User-Cuit": "27-33333333-4", "X-User-Roles": "admin"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["usuario"], "ana.perez")


if __name__ == "__main__":
    unittest.main()
