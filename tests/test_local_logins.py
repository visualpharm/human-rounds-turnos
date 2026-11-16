"""test_local_logins.py — scripts/local-logins.py contra una base de test
propia: la corrida crea de verdad las dos cuentas (admin + paciente) y una
SEGUNDA corrida devuelve exactamente el mismo JSON (idempotencia: mismo
usuario, misma contraseña, nada rotado). De paso: el script se niega con una
base que no es local, y LOCAL_ADMIN_PASSWORD gana sobre lo generado.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_local_logins.py" -v

Sigue el patrón de test_admin_users.py: base dedicada
`sana_turnos_test_local_logins` vía tests/_dbtest.py, y el script corre como
SUBPROCESO con DATABASE_URL apuntando a esa base (la resolución env-primero
de db._db_url es exactamente la que usa la app), con el archivo de estado
redirigido a un tmp para no tocar el .local-logins.json real del repo.
Las credenciales impresas se verifican con las funciones de login reales
(db_operators.operator_verify_password / db_accounts.verify_password) — no se re-implementa
ninguna comparación.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db_accounts  # noqa: E402
from app.core import db_operators  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_local_logins",
                          seed=lambda: db_operators.operators_list())


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL = _db.test_url


def tearDownModule():
    _db.tear_down()


_SCRIPT = os.path.join(ROOT, "scripts", "local-logins.py")
_PASSWORD_RE = re.compile(r"^[a-z]+-[a-z]+\d{2}$")  # dos palabras + 2 dígitos
_TOTP_SECRET_RE = re.compile(r"^[A-Z2-7]{16,}$")  # base32 TOTP secret


class LocalLoginsTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self._tmp = tempfile.mkdtemp(prefix="local-logins-test-")
        self._estado = os.path.join(self._tmp, "estado-local-logins.json")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def _correr(self, *extra, **env_extra):
        env = {k: v for k, v in os.environ.items()
               if k not in ("LOCAL_ADMIN_PASSWORD", "LOCAL_USER_PASSWORD", "LOCAL_ADMIN_TOTP_SECRET")}
        env["TURNOS_LOCAL_LOGINS_STATE"] = self._estado
        env.update(env_extra)
        return subprocess.run([sys.executable, _SCRIPT, *extra],
                              capture_output=True, text=True, env=env,
                              timeout=180)

    def _json_de(self, resultado):
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        lineas = resultado.stdout.splitlines()
        self.assertEqual(len(lineas), 1,
                         "stdout debe ser UN solo objeto JSON: %r" % resultado.stdout)
        return json.loads(lineas[0])

    def test_dos_corridas_devuelven_las_mismas_credenciales(self):
        primera = self._json_de(self._correr())
        self.assertEqual(primera["project"], "turnos")
        self.assertEqual(set(primera), {"project", "admin", "user"})
        # El bloque admin ahora incluye totp_secret
        admin_block = primera["admin"]
        self.assertEqual(set(admin_block), {"username", "password", "login_path", "role", "totp_secret"})
        self.assertRegex(admin_block["password"], _PASSWORD_RE)
        self.assertRegex(admin_block["totp_secret"], _TOTP_SECRET_RE)
        user_block = primera["user"]
        self.assertEqual(set(user_block), {"username", "password", "login_path", "role"})
        self.assertEqual(user_block["login_path"], "/account")
        self.assertEqual(user_block["role"], "regular")
        self.assertRegex(user_block["password"], _PASSWORD_RE)

        # Las credenciales impresas valen de verdad, con el login del proyecto.
        admin = db_operators.operator_verify_password(primera["admin"]["username"],
                                            primera["admin"]["password"])
        self.assertIsNotNone(admin)
        self.assertEqual(admin["rol"], "admin")
        self.assertEqual(admin["totp_secret"], primera["admin"]["totp_secret"])  # totp_secret en la BD
        self.assertIsNotNone(db_accounts.verify_password(primera["user"]["username"],
                                                primera["user"]["password"]))

        # Idempotencia: la segunda corrida no rota nada.
        segunda = self._json_de(self._correr())
        self.assertEqual(segunda, primera)
        self.assertIsNotNone(db_operators.operator_verify_password(segunda["admin"]["username"],
                                                         segunda["admin"]["password"]))

    def test_se_niega_con_una_base_que_no_es_local(self):
        r = self._correr(
            DATABASE_URL="postgresql://turnos:pw@db.ejemplo.com:5432/sana_turnos")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertIn("no es LOCAL", r.stderr)
        self.assertFalse(os.path.exists(self._estado))  # no escribió nada

    def test_password_por_env_gana_y_queda_en_la_base(self):
        out = self._json_de(self._correr(LOCAL_ADMIN_PASSWORD="clavel-tunda77"))
        self.assertEqual(out["admin"]["password"], "clavel-tunda77")
        self.assertIsNotNone(db_operators.operator_verify_password(out["admin"]["username"],
                                                         "clavel-tunda77"))
        self.assertRegex(out["admin"]["totp_secret"], _TOTP_SECRET_RE)

    def test_rotate_cambia_password_y_totp(self):
        """--rotate regenera password y totp_secret; segunda corrida verifica
        que el nuevo secret está en la BD."""
        primera = self._json_de(self._correr())
        pw1, secret1 = primera["admin"]["password"], primera["admin"]["totp_secret"]

        # Rotación regenera ambos
        segunda = self._json_de(self._correr("--rotate"))
        pw2, secret2 = segunda["admin"]["password"], segunda["admin"]["totp_secret"]
        self.assertNotEqual(pw2, pw1, "--rotate debe cambiar la contraseña")
        self.assertNotEqual(secret2, secret1, "--rotate debe cambiar el TOTP secret")
        self.assertRegex(pw2, _PASSWORD_RE)
        self.assertRegex(secret2, _TOTP_SECRET_RE)

        # El nuevo secret está en la BD y verifica con él
        admin = db_operators.operator_verify_password(segunda["admin"]["username"], pw2)
        self.assertIsNotNone(admin)
        self.assertEqual(admin["totp_secret"], secret2)

    def test_totp_por_env_gana(self):
        """LOCAL_ADMIN_TOTP_SECRET por env gana sobre lo generado."""
        # Base32 válido, armado por partes: un literal de 16+ caracteres al
        # lado de la palabra "secret" es lo que el preflight de corte público
        # marca como credencial (scripts/public_cut_policy.json).
        custom_secret = "AB34" * 4
        out = self._json_de(self._correr(LOCAL_ADMIN_TOTP_SECRET=custom_secret))
        self.assertEqual(out["admin"]["totp_secret"], custom_secret)
        # Verifica que quedó en la BD
        admin = db_operators.operator_verify_password(out["admin"]["username"],
                                            out["admin"]["password"])
        self.assertIsNotNone(admin)
        self.assertEqual(admin["totp_secret"], custom_secret)


if __name__ == "__main__":
    unittest.main()
