"""test_scripts_sql_pins.py — what the maintenance scripts' guarded statements do, pinned before
their SQL moved to SQLAlchemy Core (stage 7).

`scripts/medico_por_id.py` and `scripts/medico_de_web.py` write with an optimistic guard (the UPDATE
only hits a row that still looks the way the dry run saw it), `scripts/fixes/purge_renaper_account_copies.py`
asks which DNIs have an account / imported history, and `worker._db_ok` is the liveness probe of the
queue worker. The end-to-end runs of both medico scripts live in `test_medico_por_id.py`; this file
holds the guards that those runs cannot reach.

Run with discover (see CONTRIBUTING.md); own database `sana_turnos_test_scripts_sql`.
"""
import importlib.util
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_clinical  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_scripts_sql")
HAVE_DB, DB_SKIP_REASON, _TEST_URL = False, None, None


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON, _TEST_URL = _db.have_db, _db.skip_reason, _db.test_url


def tearDownModule():
    _db.tear_down()


def _load(*relpath):
    path = os.path.join(ROOT, *relpath)
    spec = importlib.util.spec_from_file_location(os.path.basename(path)[:-3], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sql(query, params=()):
    import psycopg
    with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c, c.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall() if cur.description else None


class DbCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _sql("TRUNCATE booking_requests, users, clinical_history_records, catalog RESTART IDENTITY CASCADE")

    def _request(self, dni="40111111", medico=None, medico_id=None):
        sid = db_booking.create_booking_request(dni, "Paciente", "Prueba", "1980-01-01", "wa:11",
                                        "serv-cardio", "centro-a")
        _sql("UPDATE booking_requests SET assigned_doctor=%s, assigned_doctor_id=%s WHERE id=%s",
             (medico, medico_id, sid))
        return sid

    def _pair(self, sid):
        return _sql("SELECT assigned_doctor, assigned_doctor_id FROM booking_requests WHERE id=%s",
                    (sid,))[0]


class TestMedicoPorIdGuard(DbCase):
    """`_aplicar` rewrites the (name, id) pair only on a row that still holds the pair the dry run
    read — the comparison is `IS NOT DISTINCT FROM`, so a NULL on both sides matches."""

    def setUp(self):
        super().setUp()
        self.script = _load("scripts", "medico_por_id.py")

    def _apply(self, sid, viejo_nombre, viejo_id):
        return self.script._aplicar(db, [{"id": sid, "nombre": "Carlos Gómez", "medico_id": "med-2",
                                         "viejo_nombre": viejo_nombre, "viejo_id": viejo_id}])

    def test_the_same_pair_is_rewritten(self):
        sid = self._request(medico="Gomez", medico_id="med-9")
        self.assertEqual(self._apply(sid, "Gomez", "med-9"), 1)
        self.assertEqual(tuple(self._pair(sid)), ("Carlos Gómez", "med-2"))

    def test_a_null_id_matches_a_null_old_id(self):
        sid = self._request(medico="Gomez", medico_id=None)
        self.assertEqual(self._apply(sid, "Gomez", None), 1)
        self.assertEqual(tuple(self._pair(sid)), ("Carlos Gómez", "med-2"))

    def test_a_row_changed_in_between_is_left_alone(self):
        sid = self._request(medico="Otro Nombre", medico_id="med-9")
        self.assertEqual(self._apply(sid, "Gomez", "med-9"), 0)
        self.assertEqual(self._apply(sid, "Otro Nombre", "med-1"), 0)
        self.assertEqual(self._apply(sid, "Otro Nombre", None), 0)
        self.assertEqual(tuple(self._pair(sid)), ("Otro Nombre", "med-9"))

    def test_a_null_name_is_not_a_blank_name(self):
        sid = self._request(medico=None, medico_id="med-9")
        self.assertEqual(self._apply(sid, "", "med-9"), 0)
        self.assertEqual(self._apply(sid, None, "med-9"), 1)

    def test_only_the_named_row_changes_and_the_count_is_the_rows_written(self):
        a = self._request("40111111", medico="Gomez", medico_id=None)
        b = self._request("40111112", medico="Gomez", medico_id=None)
        n = self.script._aplicar(db, [
            {"id": a, "nombre": "Carlos Gómez", "medico_id": "med-2", "viejo_nombre": "Gomez", "viejo_id": None},
            {"id": b, "nombre": "Carlos Gómez", "medico_id": "med-2", "viejo_nombre": "Nadie", "viejo_id": None},
        ])
        self.assertEqual(n, 1)
        self.assertEqual(tuple(self._pair(a)), ("Carlos Gómez", "med-2"))
        self.assertEqual(tuple(self._pair(b)), ("Gomez", None))

    def test_the_read_lists_the_rows_with_a_doctor_in_id_order(self):
        sin = self._request("40111111")
        solo_nombre = self._request("40111112", medico="Gomez")
        solo_id = self._request("40111113", medico_id="med-1")
        ambos = self._request("40111114", medico="Carlos Gómez", medico_id="med-2")
        filas = self.script._leer(db)
        self.assertEqual([f["id"] for f in filas], [solo_nombre, solo_id, ambos])
        self.assertNotIn(sin, [f["id"] for f in filas])
        self.assertEqual(sorted(filas[0]), ["assigned_doctor", "assigned_doctor_id", "id"])


class TestMedicoDeWebGuard(DbCase):
    """`_aplicar` completes a row only while it still has no doctor (no id and a NULL or blank
    name); `_leer` selects the future auto-confirmed web rows that carry the slot's doctor."""

    def setUp(self):
        super().setUp()
        self.script = _load("scripts", "medico_de_web.py")

    def _apply(self, sid):
        return self.script._aplicar(db, [{"id": sid, "nombre": "María José Fernández", "medico_id": "med-1"}])

    def test_a_row_without_doctor_is_completed_whether_the_name_is_null_or_blank(self):
        a = self._request("40111111", medico=None, medico_id=None)
        b = self._request("40111112", medico="", medico_id=None)
        self.assertEqual(self._apply(a), 1)
        self.assertEqual(self._apply(b), 1)
        self.assertEqual(tuple(self._pair(a)), ("María José Fernández", "med-1"))
        self.assertEqual(tuple(self._pair(b)), ("María José Fernández", "med-1"))

    def test_a_row_that_got_a_doctor_in_between_is_not_overwritten(self):
        con_nombre = self._request("40111111", medico="Carlos Gómez", medico_id=None)
        con_id = self._request("40111112", medico=None, medico_id="med-2")
        self.assertEqual(self._apply(con_nombre), 0)
        self.assertEqual(self._apply(con_id), 0)
        self.assertEqual(tuple(self._pair(con_nombre)), ("Carlos Gómez", None))
        self.assertEqual(tuple(self._pair(con_id)), (None, "med-2"))

    def test_the_count_is_the_rows_written(self):
        a = self._request("40111111")
        b = self._request("40111112", medico="Carlos Gómez", medico_id="med-2")
        n = self.script._aplicar(db, [
            {"id": a, "nombre": "María José Fernández", "medico_id": "med-1"},
            {"id": b, "nombre": "María José Fernández", "medico_id": "med-1"},
        ])
        self.assertEqual(n, 1)

    def test_the_read_selects_future_auto_web_approvals_with_a_slot_doctor_and_no_assignment(self):
        def aprobada(dni, resuelta_por, slot, horas, medico=None, medico_id=None, estado="aprobada"):
            sid = self._request(dni, medico=medico, medico_id=medico_id)
            _sql("UPDATE booking_requests SET status=%s, resolved_by=%s, slot_doctor_id=%s, "
                 "appointment_at = now() + (%s * interval '1 hour') WHERE id=%s",
                 (estado, resuelta_por, slot, horas, sid))
            return sid
        ok = aprobada("40111111", "auto:web", "med-1", 48)
        blanco = aprobada("40111112", "auto:web", "med-1", 50, medico="")
        aprobada("40111113", "auto:web", "med-1", -48)                       # past
        aprobada("40111114", "mesa:alguien", "med-1", 48)                    # not auto:web
        aprobada("40111115", "auto:web", None, 48)                           # no slot doctor
        aprobada("40111116", "auto:web", "med-1", 48, medico="Carlos Gómez")  # has a name
        aprobada("40111117", "auto:web", "med-1", 48, medico_id="med-2")      # has an id
        aprobada("40111118", "auto:web", "med-1", 48, estado="pendiente")     # not approved
        filas = self.script._leer(db)
        self.assertEqual([f["id"] for f in filas], [ok, blanco])
        self.assertEqual(sorted(filas[0]), ["appointment_at", "assigned_doctor", "assigned_doctor_id",
                                            "center_slug", "id", "service_id", "slot_doctor_id"])


class TestPurgeDnisWith(DbCase):
    def setUp(self):
        super().setUp()
        self.purge = _load("scripts", "fixes", "purge_renaper_account_copies.py")

    def test_users_and_history_are_looked_up_by_dni(self):
        db_accounts.create_user("30000011", "Real", "Paciente", "1970-01-01", "F", "2254111111", "x")
        db_accounts.create_user("30000013", "Otra", "Paciente", "1970-01-01", "F", "2254111112", "x")
        db_clinical.clinical_history_replace("30000012", [{"fecha": "2020-01-01", "texto": "consulta"}])
        dnis = {"30000011", "30000012", "30000099"}
        self.assertEqual(self.purge._dnis_with("users", dnis), {"30000011"})
        self.assertEqual(self.purge._dnis_with("clinical_history_records", dnis), {"30000012"})
        self.assertEqual(self.purge._dnis_with("users", {"30000013"}), {"30000013"})

    def test_no_dnis_asks_nothing_and_finds_nothing(self):
        self.assertEqual(self.purge._dnis_with("users", set()), set())
        self.assertEqual(self.purge._dnis_with("clinical_history_records", set()), set())


class TestWorkerLiveness(DbCase):
    def test_the_probe_is_true_with_a_database_and_false_without(self):
        from app import worker
        self.assertTrue(worker._db_ok())
        with mock.patch.object(db, "_connect", side_effect=db.DbUnavailable("sin base")):
            self.assertFalse(worker._db_ok())


if __name__ == "__main__":
    unittest.main()
