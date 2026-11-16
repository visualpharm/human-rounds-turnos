"""Los links de recuperación de contraseña no quedan legibles en la base (2026-10-25).

Hallazgos (sqla-5, 2026-10-25): `reset_tokens.token` guardaba el token del link
en claro — quien leyera la base (copia, volcado, réplica) podía armar el link y
tomar la cuenta durante la hora de vida del token, mientras los códigos OTP ya
se guardaban con hash. Además `set_password` cerraba las sesiones pero dejaba
vivos los otros links sin usar de la misma cuenta: el link viejo de un correo
anterior seguía cambiando la contraseña después de recuperarla.

Ahora:
  - la columna guarda SHA-256(token) (como `admin_reset_tokens.token_hash`);
    el link del correo es el mismo y se busca por el hash;
  - `set_password` (link, código OTP o cualquier otro camino) vence todos los
    links sin usar de ese DNI.
"""

import hashlib
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_AUTH_MODE="password")

from app.core import db  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.cuentas import accounts  # noqa: E402
from app.cuentas import accounts_router  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app.auth import turnstile  # noqa: E402
from app.core import config  # noqa: E402
import _dbtest  # noqa: E402

turnstile.ENV_PATH = ""
config.ENV_PATH = ""

_db = _dbtest.PerModuleDb("sana_turnos_test_reset_token_hash", seed=lambda: db_accounts.get_user("1"))

_APP = FastAPI()
_APP.include_router(accounts_router.router)
_CLIENT = TestClient(_APP, base_url="http://127.0.0.1:7700", follow_redirects=False,
                     client=("127.0.0.1", 54321))

_DNI = "30222444"


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _one(query, params=()):
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone() if cur.description else None


def _all(query, params=()):
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()


def _sha(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


class _Base(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _CLIENT.cookies.clear()
        _one("TRUNCATE sessions, reset_tokens, users RESTART IDENTITY CASCADE")
        db_accounts.create_user(_DNI, "Carla", "Paciente", "1980-01-01", "F", "2254000003", "vieja1234",
                       email="carla@example.invalid")


class TokenIsNotStoredInTheClear(_Base):
    def test_the_database_holds_only_the_hash(self):
        raw = db_accounts.create_reset_token(_DNI)
        rows = _all("SELECT token FROM reset_tokens")
        self.assertEqual([r["token"] for r in rows], [_sha(raw)])
        self.assertIsNone(_one("SELECT 1 AS x FROM reset_tokens WHERE token=%s", (raw,)))

    def test_the_emailed_link_still_works_and_the_database_never_saw_it(self):
        sent = []
        with patch.object(accounts, "_send_email", lambda to, subject, body: sent.append(body) or True):
            h = _CLIENT.post("/api/account/recover", json={"dni": _DNI})
            accounts.drain_detached()
        self.assertEqual(h.status_code, 200, h.text)
        self.assertEqual(len(sent), 1)
        raw = sent[0].split("/account/reset?token=", 1)[1].split()[0]
        # the link carries the raw token; no column of any row equals it
        self.assertIsNone(_one("SELECT 1 AS x FROM reset_tokens WHERE token=%s", (raw,)))
        self.assertEqual(_one("SELECT count(*) AS n FROM reset_tokens WHERE token=%s", (_sha(raw),))["n"], 1)
        h = _CLIENT.post("/api/account/reset", json={"token": raw, "password": "nueva1234"})
        self.assertEqual(h.status_code, 200, h.text)
        self.assertIsNotNone(db_accounts.verify_password(_DNI, "nueva1234"))

    def test_the_stored_hash_is_not_a_usable_token(self):
        db_accounts.create_reset_token(_DNI)
        stored = _one("SELECT token FROM reset_tokens")["token"]
        self.assertIsNone(db_accounts.consume_reset_token(stored))
        h = _CLIENT.post("/api/account/reset", json={"token": stored, "password": "nueva1234"})
        self.assertEqual(h.status_code, 400)
        self.assertIsNotNone(db_accounts.verify_password(_DNI, "vieja1234"))

    def test_an_unknown_or_empty_token_is_refused(self):
        db_accounts.create_reset_token(_DNI)
        self.assertIsNone(db_accounts.consume_reset_token("no-es-un-token"))
        self.assertIsNone(db_accounts.consume_reset_token(""))
        self.assertIsNone(db_accounts.consume_reset_token(None))

    def test_a_token_works_once_and_not_after_expiry(self):
        raw = db_accounts.create_reset_token(_DNI)
        self.assertEqual(db_accounts.consume_reset_token(raw), _DNI)
        self.assertIsNone(db_accounts.consume_reset_token(raw))
        vencido = db_accounts.create_reset_token(_DNI)
        _one("UPDATE reset_tokens SET expires = now() - interval '1 minute' WHERE token=%s", (_sha(vencido),))
        self.assertIsNone(db_accounts.consume_reset_token(vencido))


class ChangingThePasswordVoidsOtherLinks(_Base):
    def test_an_old_link_is_dead_after_the_password_changes_by_another_path(self):
        viejo = db_accounts.create_reset_token(_DNI)
        db_accounts.set_password(_DNI, "otra12345")      # p. ej. recuperación por código OTP
        self.assertIsNone(db_accounts.consume_reset_token(viejo))
        h = _CLIENT.post("/api/account/reset", json={"token": viejo, "password": "robada123"})
        self.assertEqual(h.status_code, 400)
        self.assertIsNotNone(db_accounts.verify_password(_DNI, "otra12345"))
        self.assertIsNone(db_accounts.verify_password(_DNI, "robada123"))

    def test_the_second_link_is_dead_after_the_first_is_used(self):
        primero = db_accounts.create_reset_token(_DNI)
        segundo = db_accounts.create_reset_token(_DNI)
        h = _CLIENT.post("/api/account/reset", json={"token": primero, "password": "nueva1234"})
        self.assertEqual(h.status_code, 200, h.text)
        h = _CLIENT.post("/api/account/reset", json={"token": segundo, "password": "robada123"})
        self.assertEqual(h.status_code, 400)
        self.assertIsNotNone(db_accounts.verify_password(_DNI, "nueva1234"))
        self.assertIsNone(db_accounts.verify_password(_DNI, "robada123"))

    def test_only_this_accounts_links_are_voided(self):
        db_accounts.create_user("30222555", "Otro", "Paciente", "1981-01-01", "M", "2254000004", "clave1234",
                       email="otro@example.invalid")
        mio = db_accounts.create_reset_token(_DNI)
        ajeno = db_accounts.create_reset_token("30222555")
        db_accounts.set_password(_DNI, "nueva1234")
        self.assertIsNone(db_accounts.consume_reset_token(mio))
        self.assertEqual(db_accounts.consume_reset_token(ajeno), "30222555")


if __name__ == "__main__":
    unittest.main()
