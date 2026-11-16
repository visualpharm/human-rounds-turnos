"""Archivar un paciente es coherente en cada tabla con su DNI (2026-10-23).

Hallazgo (problems.md №6): archivar era sólo `users.active=false`. Sus turnos
pendientes y por venir seguían en la cola sin aviso a la mesa, su lugar en la
lista de espera seguía recibiendo ofertas, y el token de su calendario y el
del asistente de IA (que pide turnos EN SU NOMBRE) seguían valiendo.

Ahora:
  - con turnos vivos, archivar responde 409 con la lista y no cambia nada;
    la mesa confirma y el pedido con `?cancelar_turnos=1` los cancela por el
    camino de siempre (`_desk_cancel_core`: libera el lugar, avisa al
    paciente) y recién entonces archiva;
  - al archivar: lista de espera desactivada y sus ofertas vivas vencidas,
    sesiones y links de recuperación borrados, tokens de calendario y de
    asistente sin efecto; la historia (turnos pasados, entrevistas…) queda;
  - `db_accounts.DNI_ON_ARCHIVE` dice qué pasa con CADA columna con DNI, y no puede
    faltar ninguna de las que `change_dni` conoce.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")

from app.core import db  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app import main as app_module  # noqa: E402
from app.auth import turnstile  # noqa: E402
from app.core import config  # noqa: E402
import _dbtest  # noqa: E402

turnstile.ENV_PATH = ""
config.ENV_PATH = ""
_CLIENT = TestClient(app_module.app)

_db = _dbtest.PerModuleDb("sana_turnos_test_patient_archive", seed=lambda: db_accounts.get_user("1"))

_DNI = "30222333"
_FUTURE = datetime.now(timezone.utc) + timedelta(days=4)
_PAST = datetime.now(timezone.utc) - timedelta(days=4)


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _admin():
    return {"Cookie": "st_admin=" + admin_auth._mint_token(admin_auth._admin_user(), "admin")}


def _one(query, params=()):
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone() if cur.description else None


class _Base(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        for table in ("users", "sessions", "reset_tokens", "booking_requests", "waitlist",
                      "appointment_offers", "appointment_events", "interviews"):
            _one("TRUNCATE %s RESTART IDENTITY CASCADE" % table)
        db_accounts.create_user(_DNI, "Carla", "Paciente", "1980-01-01", "F", "2254000002", "x",
                       email="carla@example.invalid")
        self.mail = patch("app.notifications.mailgun.send", return_value=True)
        self.mail.start()
        self.addCleanup(self.mail.stop)

    def _request(self, status, when):
        return _one("INSERT INTO booking_requests (dni, requester_dni, first_name, last_name, "
                    "service_id, center_slug, status, appointment_at) "
                    "VALUES (%s, %s, 'Carla', 'Paciente', 'clinica', 'hospital', %s, %s) RETURNING id",
                    (_DNI, _DNI, status, when))["id"]

    def _status(self, sid):
        return _one("SELECT status FROM booking_requests WHERE id=%s", (sid,))["status"]

    def _archive(self, query=""):
        return _CLIENT.delete("/api/admin/patients/" + _DNI + query, headers=_admin())


class _Live(_Base):
    """Un turno aprobado por venir, uno pendiente sin horario, uno pasado, y
    la lista de espera del primero con una oferta viva."""

    def setUp(self):
        super().setUp()
        self.future = self._request("aprobada", _FUTURE)
        self.pending = self._request("pendiente", None)
        self.past = self._request("aprobada", _PAST)
        self.wait = _one("INSERT INTO waitlist (request_id, dni, service_id, center_slug, "
                         "appointment_at) VALUES (%s, %s, 'clinica', 'hospital', %s) RETURNING id",
                         (self.future, _DNI, _FUTURE))["id"]
        _one("INSERT INTO appointment_offers (slot_at, service_id, center_slug, waitlist_id, "
             "token, sent_at, expires_at) VALUES (%s, 'clinica', 'hospital', %s, 'tok-oferta', "
             "now(), now() + interval '1 hour')", (_FUTURE - timedelta(days=1), self.wait))


class ArchiveWithLiveAppointments(_Live):
    def test_without_confirmation_it_refuses_and_changes_nothing(self):
        response = self._archive()
        self.assertEqual(response.status_code, 409, response.text)
        body = response.json()
        self.assertEqual(body["error"], "turnos_activos")
        self.assertEqual(sorted(t["id"] for t in body["turnos"]), sorted([self.future, self.pending]))
        self.assertTrue(db_accounts.get_user(_DNI)["activo"])
        self.assertEqual(self._status(self.future), "aprobada")

    def test_confirmed_archive_cancels_live_ones_and_keeps_history(self):
        token = db_accounts.create_session(_DNI)
        db_accounts.create_reset_token(_DNI)
        response = self._archive("?cancelar_turnos=1")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["turnos_cancelados"], 2)
        self.assertFalse(db_accounts.get_user(_DNI)["activo"])
        self.assertEqual(self._status(self.future), "cancelada")
        self.assertEqual(self._status(self.pending), "cancelada")
        self.assertEqual(self._status(self.past), "aprobada")
        self.assertFalse(_one("SELECT active FROM waitlist WHERE id=%s", (self.wait,))["active"])
        # Vencida, no borrada: el barrido de la lista de espera la cierra y
        # ofrece el lugar al siguiente.
        self.assertEqual(_one("SELECT COUNT(*) AS n FROM appointment_offers "
                              "WHERE status='ofrecida' AND expires_at > now()")["n"], 0)
        self.assertIsNone(db_accounts.get_session(token))
        self.assertEqual(_one("SELECT COUNT(*) AS n FROM sessions WHERE dni=%s", (_DNI,))["n"], 0)
        self.assertEqual(_one("SELECT COUNT(*) AS n FROM reset_tokens WHERE dni=%s", (_DNI,))["n"], 0)


class ArchiveFromAnotherCenter(_Live):
    """A desk scoped to another center cannot see this center's turnos: the
    409 only counts them (404 convention), and nothing is cancelled."""

    def test_foreign_turnos_are_counted_not_described(self):
        with patch("app.auth.admin_auth.centros_permitidos", return_value=["otro-centro"]):
            response = self._archive("?cancelar_turnos=1")
        self.assertEqual(response.status_code, 409, response.text)
        body = response.json()
        self.assertEqual(body["turnos"], [])
        self.assertEqual(body["turnos_ajenos"], 2)
        self.assertEqual(body["turnos_cancelados"], 0)
        self.assertNotIn("clinica", response.text)
        self.assertTrue(db_accounts.get_user(_DNI)["activo"])
        self.assertEqual(self._status(self.future), "aprobada")
        self.assertEqual(self._status(self.pending), "pendiente")


class ArchiveThroughPut(_Live):
    def test_put_activo_false_cannot_skip_the_live_appointments(self):
        response = _CLIENT.put("/api/admin/patients/" + _DNI, json={"activo": False}, headers=_admin())
        self.assertEqual(response.status_code, 409, response.text)
        self.assertTrue(db_accounts.get_user(_DNI)["activo"])
        self.assertEqual(self._status(self.future), "aprobada")


class ArchiveWithoutLiveAppointments(_Base):
    def test_put_activo_false_archives_through_the_same_path(self):
        token = db_accounts.create_session(_DNI)
        response = _CLIENT.put("/api/admin/patients/" + _DNI, json={"activo": False}, headers=_admin())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(db_accounts.get_user(_DNI)["activo"])
        self.assertEqual(_one("SELECT COUNT(*) AS n FROM sessions WHERE dni=%s", (_DNI,))["n"], 0)
        self.assertIsNone(db_accounts.get_session(token))

    def test_archives_directly_and_its_tokens_stop_working(self):
        feed = db_accounts.feed_token_issue(_DNI)["token"]
        mcp = db_accounts.mcp_token_issue(_DNI)["token"]
        self._request("aprobada", _PAST)
        response = self._archive()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(db_accounts.get_user(_DNI)["activo"])
        self.assertIsNone(db_accounts.user_by_mcp_token(mcp))
        self.assertIsNone(db_accounts.user_by_feed_token(feed))

    def test_restoring_brings_the_tokens_back(self):
        mcp = db_accounts.mcp_token_issue(_DNI)["token"]
        self.assertEqual(self._archive().status_code, 200)
        _CLIENT.put("/api/admin/patients/" + _DNI, json={"activo": True}, headers=_admin())
        self.assertEqual(db_accounts.user_by_mcp_token(mcp)["dni"], _DNI)


class EveryDniColumnHasAnArchiveRule(unittest.TestCase):
    def test_registry_covers_every_dni_column_change_dni_knows(self):
        known = set(db_accounts.DNI_MOVED_COLUMNS) | set(db_accounts.DNI_KEPT_COLUMNS)
        self.assertEqual(set(db_accounts.DNI_ON_ARCHIVE), known)


if __name__ == "__main__":
    unittest.main()
