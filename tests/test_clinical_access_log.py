"""test_clinical_access_log.py — el registro de accesos a datos clínicos y la
regla «el médico ve sólo a sus pacientes» (K10, 2026-10-23; core/access_log.py,
revisión Alembic 0026).

Cubre: la tabla es de sólo agregado (UPDATE y DELETE rechazados por la base,
la purga borra sólo lo viejo y nunca menos de un año), el DNI nunca se guarda
(HMAC con clave), quién es «paciente propio» (asignado, reservado en la
agenda, entrevista dirigida al médico — de otro médico no), qué roles quedan
acotados, la retención configurable y el barrido de housekeeping.

Correr:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning \
         -m unittest discover -s tests -p "test_clinical_access_log.py"
"""

import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.core import config  # noqa: E402
from app.core import access_log  # noqa: E402
from app.core import tz  # noqa: E402
config.ENV_PATH = ""
from app.ops import housekeeping  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_access_log", seed=lambda: db_operators.operators_list())

_DNI = "30111222"
_OTRO = "30111223"


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _truncate():
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE clinical_access_log, booking_requests, interviews, "
                    "operators, operator_access RESTART IDENTITY CASCADE")


def _insert(ts=None, actor="ana", dni=_DNI):
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO clinical_access_log (ts, actor, actor_role, action, dni_hash)
               VALUES (COALESCE(%s, now()), %s, 'admin', 'ficha', %s)""",
            (ts, actor, access_log.dni_hash(dni)))


def _count():
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM clinical_access_log")
        return cur.fetchone()["n"]


class DbCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON)
        _truncate()


class TestAppendOnly(DbCase):
    def test_update_is_refused_by_the_database(self):
        _insert()
        with self.assertRaises(Exception) as ctx:
            with db._connect() as conn, conn.cursor() as cur:
                cur.execute("UPDATE clinical_access_log SET actor = 'otro'")
        self.assertIn("sólo agregado", str(ctx.exception))
        self.assertEqual(access_log.history(_DNI)[0]["actor"], "ana")

    def test_delete_is_refused_outside_the_purge(self):
        _insert(ts=datetime.now(tz.tzinfo()) - timedelta(days=4000))
        with self.assertRaises(Exception):
            with db._connect() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM clinical_access_log")
        self.assertEqual(_count(), 1)

    def test_even_the_purge_flag_cannot_delete_a_row_younger_than_a_year(self):
        _insert(ts=datetime.now(tz.tzinfo()) - timedelta(days=100))
        with self.assertRaises(Exception):
            with db._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT set_config('turnos.access_log_purge', 'on', true)")
                cur.execute("DELETE FROM clinical_access_log")
        self.assertEqual(_count(), 1)

    def test_purge_deletes_only_what_is_older_than_the_retention(self):
        ahora = datetime.now(tz.tzinfo())
        _insert(ts=ahora - timedelta(days=4000), actor="viejo")
        _insert(ts=ahora - timedelta(days=500), actor="medio")
        _insert(actor="nuevo")
        self.assertEqual(db_clinical.purge_clinical_access_log(3650), 1)
        # Una retención por debajo de un año se trata como un año.
        self.assertEqual(db_clinical.purge_clinical_access_log(30), 1)
        self.assertEqual([r["actor"] for r in access_log.history(_DNI)], ["nuevo"])

    def test_housekeeping_runs_the_purge_with_the_configured_retention(self):
        _insert(ts=datetime.now(tz.tzinfo()) - timedelta(days=800))
        with _env.patched_env({"TURNOS_HOUSEKEEPING_ACCESS_LOG_DAYS": "3650"}):
            self.assertEqual(housekeeping.tick_purges()["clinical_access_log"], 0)
        with _env.patched_env({"TURNOS_HOUSEKEEPING_ACCESS_LOG_DAYS": "730"}):
            self.assertEqual(housekeeping.tick_purges()["clinical_access_log"], 1)


class TestHashAndRecord(DbCase):
    def test_the_dni_is_never_stored_only_its_keyed_hash(self):
        access_log.record({"usuario": "ana", "rol": "admin"}, "ficha", "30.111.222", target_id=7)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM clinical_access_log")
            fila = dict(cur.fetchone())
        self.assertNotIn(_DNI, repr(fila))
        self.assertEqual(fila["dni_hash"], access_log.dni_hash(_DNI))
        self.assertEqual((fila["actor"], fila["actor_role"], fila["action"], fila["target_id"]),
                         ("ana", "admin", "ficha", "7"))
        # La clave cambia el hash: sin la clave, el DNI no se recupera por fuerza bruta
        # sobre un sha256 pelado.
        with _env.patched_env({"TURNOS_ACCESS_LOG_KEY": "otra-clave"}):
            self.assertNotEqual(access_log.dni_hash(_DNI), fila["dni_hash"])

    def test_unknown_action_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            access_log.record({"usuario": "ana", "rol": "admin"}, "cualquiera", _DNI)

    def test_a_log_that_cannot_be_written_refuses_the_read(self):
        with mock.patch.object(db_clinical, "insert_clinical_access", side_effect=db.DbUnavailable("x")):
            with self.assertRaises(access_log.AccessLogUnavailable):
                access_log.check({"usuario": "ana", "rol": "admin"}, "ficha", _DNI)

    def test_a_list_writes_each_patient_once_a_day(self):
        access_log._seen.clear()
        quien = {"usuario": "mesa", "rol": "admisionista"}

        class _Dia(datetime):
            hoy = datetime(2026, 10, 23, 9, 0, tzinfo=tz.tzinfo())

            @classmethod
            def now(cls, tzinfo=None):
                return cls.hoy

        with mock.patch.object(access_log, "datetime", _Dia):
            for _ in range(3):   # la cola se refresca sola
                access_log.record_many(quien, "cola", [(_DNI, 1), (_OTRO, 2)])
            self.assertEqual(_count(), 2)
            _Dia.hoy = datetime(2026, 10, 24, 8, 0, tzinfo=tz.tzinfo())
            access_log.record_many(quien, "cola", [(_DNI, 1)])
        self.assertEqual(_count(), 3)

    def test_a_failed_list_write_is_not_remembered_as_written(self):
        # Crítica 2026-10-23: marcar «visto» antes del INSERT dejaba que otra
        # lectura de la misma lista se saltara su fila mientras este fallaba.
        access_log._seen.clear()
        quien = {"usuario": "mesa", "rol": "admisionista"}
        with mock.patch.object(db_clinical, "insert_clinical_access_many", side_effect=db.DbUnavailable("x")):
            with self.assertRaises(access_log.AccessLogUnavailable):
                access_log.record_many(quien, "cola", [(_DNI, 1)])
        access_log.record_many(quien, "cola", [(_DNI, 1)])
        self.assertEqual(_count(), 1)
        # Otro rol de la misma cuenta el mismo día: otra fila.
        access_log.record_many({"usuario": "mesa", "rol": "gestor_agendas"}, "cola", [(_DNI, 1)])
        self.assertEqual(_count(), 2)

    def test_history_is_newest_first_and_per_patient(self):
        _insert(ts=datetime.now(tz.tzinfo()) - timedelta(days=1), actor="ayer")
        _insert(actor="hoy")
        _insert(actor="de-otro", dni=_OTRO)
        self.assertEqual([r["actor"] for r in access_log.history(_DNI)], ["hoy", "ayer"])


class TestOwnPatient(DbCase):
    def _medico(self, usuario="dra", medico_id="med-1", rol="medico"):
        db_operators.operator_create(usuario, "clave1234", rol, medico_id=medico_id)
        return {"usuario": usuario, "rol": rol}

    def _solicitud(self, dni=_DNI, asignado=None, slot=None):
        sid = db_booking.create_booking_request(dni, "Ana", "Paz", "1980-01-01", "ana@example.com",
                                        "cardiologia", "hospital-pinamar", slot_medico_id=slot)
        if asignado:
            db_booking.approve(sid, datetime.now(tz.tzinfo()) + timedelta(days=1),
                       medico_asignado_id=asignado, resuelta_por="admin")
        return sid

    def test_assigned_booked_or_interviewed_is_own_any_other_doctor_is_not(self):
        dra = self._medico()
        self.assertFalse(access_log.may_see(dra, _DNI))
        self._solicitud(asignado="med-2")
        self.assertFalse(access_log.may_see(dra, _DNI))
        self._solicitud(asignado="med-1")
        self.assertTrue(access_log.may_see(dra, _DNI))
        self.assertFalse(access_log.may_see(dra, _OTRO))
        self._solicitud(dni=_OTRO, slot="med-1")
        self.assertTrue(access_log.may_see(dra, _OTRO))
        tercero = "30111224"
        db_clinical.interview_create(doctor_id="med-2", dni=tercero)
        self.assertFalse(access_log.may_see(dra, tercero))
        # an interview typed in by a visitor and never matched to a turn is no relationship (M-2)
        db_clinical.interview_create(doctor_id="med-1", dni=tercero)
        self.assertFalse(access_log.may_see(dra, tercero))
        db_clinical.interview_create(doctor_id="med-1", dni=tercero, matched=True)
        self.assertTrue(access_log.may_see(dra, tercero))

    def test_chats_list_finds_whose_interview_each_conversation_is(self):
        iv = db_clinical.interview_create(doctor_id="med-1", dni=_DNI)
        otra = db_clinical.interview_create(doctor_id="med-2")
        got = db_clinical.interviews_by_session_keys([iv["session_key"], otra["session_key"], "web:s-1"])
        self.assertEqual(got[iv["session_key"]], {"id": iv["id"], "dni": _DNI})
        self.assertEqual(got[otra["session_key"]]["dni"], None)
        self.assertNotIn("web:s-1", got)
        self.assertEqual(db_clinical.interviews_by_session_keys([]), {})

    def test_a_requester_is_not_the_patient(self):
        # Quien saca el turno para su hijo no pasa a ser paciente del médico.
        dra = self._medico()
        sid = db_booking.create_booking_request("40999888", "Hijo", "Paz", "2015-01-01", "x@example.com",
                                        "pediatria", "hospital-pinamar", solicitante_dni=_DNI)
        db_booking.approve(sid, datetime.now(tz.tzinfo()) + timedelta(days=1),
                   medico_asignado_id="med-1", resuelta_por="admin")
        self.assertTrue(access_log.may_see(dra, "40999888"))
        self.assertFalse(access_log.may_see(dra, _DNI))

    def test_a_doctor_account_without_a_linked_doctor_sees_nobody(self):
        sin = self._medico("sin", medico_id=None)
        self._solicitud(asignado="med-1")
        self.assertFalse(access_log.may_see(sin, _DNI))

    def test_admin_sees_all_desk_by_role_custom_clinical_roles_are_scoped(self):
        admin = {"usuario": "jefa", "rol": "admin"}
        desk = {"usuario": "mesa", "rol": "admisionista"}
        guardia = {"usuario": "guardia", "rol": "medico_guardia"}
        self.assertTrue(access_log.may_see(admin, _DNI))
        # Las pantallas de la mesa (solicitudes, cola, órdenes) van por rol.
        self.assertTrue(access_log.may_see(desk, _DNI, clinical=False))
        # Las del médico (resumen, entrevista, historia): sólo el admin ve a todos.
        self.assertFalse(access_log.may_see(desk, _DNI))
        self.assertFalse(access_log.may_see(guardia, _DNI))
        # En las de la mesa, un rol propio sigue yendo por sus permisos.
        self.assertTrue(access_log.may_see(guardia, _DNI, clinical=False))
        # Un médico, en cualquier pantalla, sólo a los suyos.
        dra = self._medico()
        self.assertFalse(access_log.may_see(dra, _DNI, clinical=False))

    def test_todos_los_pacientes_lifts_the_hold_and_is_off_by_default(self):
        # The product owner 2026-10-23 (q781376, variante B).
        from app.core import permissions
        for slug, preset in permissions.BUILTIN_ROLES.items():
            if slug != "admin":
                self.assertNotIn("todos_los_pacientes", preset["permissions"], slug)
        dra = self._medico()
        self.assertFalse(access_log.may_see(dra, _DNI))
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO operator_access (username, granted) VALUES ('dra', %s)",
                        (["todos_los_pacientes"],))
        self.assertTrue(access_log.may_see(dra, _DNI))
        self.assertTrue(access_log.may_see(dra, _DNI, clinical=False))
        # Without a readable permission store the hold stays.
        with mock.patch("app.core.permissions.access_for", side_effect=RuntimeError("db down")):
            self.assertFalse(access_log.may_see(dra, _DNI))

    def test_denied_attempts_are_recorded_too(self):
        dra = self._medico()
        with self.assertRaises(access_log.AccessDenied):
            access_log.check(dra, "entrevista", _DNI, target_id=5)
        self.assertEqual([(r["actor"], r["action"], r["allowed"], r["target_id"])
                          for r in access_log.history(_DNI)],
                         [("dra", "entrevista", False, "5")])


class TestRetentionSetting(unittest.TestCase):
    def test_default_is_ten_years_and_never_under_one(self):
        with mock.patch.object(config, "get", return_value=None):
            self.assertEqual(access_log.retention_days(), 3650)
        with mock.patch.object(config, "get", return_value="30"):
            self.assertEqual(access_log.retention_days(), 365)
        with mock.patch.object(config, "get", return_value="2000"):
            self.assertEqual(access_log.retention_days(), 2000)
        with mock.patch.object(config, "get", return_value="no"):
            self.assertEqual(access_log.retention_days(), 3650)


if __name__ == "__main__":
    unittest.main()
