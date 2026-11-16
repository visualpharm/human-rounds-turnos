"""Deshabilitar un médico: nunca deja registros apuntando a la nada (2026-10-23).

Hallazgo (problems.md №6): `count_appointments_of` miraba sólo
`booking_requests.assigned_doctor_id`/`assigned_doctor`. Un médico cargado en
el panel sin turnos ASIGNADOS pero con entrevistas, consultas, resúmenes,
turnos reservados en su agenda (`slot_doctor_id`) o una cuenta se borraba de
la base del todo, y esos registros quedaban apuntando a un id inexistente. Y
un médico con turnos por venir se deshabilitaba igual: los pacientes llegaban
a un turno con un médico que ya no existe en ninguna lista.

Ahora:
  - cualquier registro que lo nombre (solicitudes por id, nombre o agenda,
    entrevistas, consultas, resúmenes, cola del día, su cuenta) lo conserva
    deshabilitado, nunca borrado;
  - con turnos aprobados por venir (o pendientes con su lugar reservado en
    su agenda), deshabilitarlo se rechaza (409) diciendo cuántos: la mesa los
    reasigna o cancela antes. Las pendientes que sólo lo tienen asignado no
    bloquean: se liberan a «sin asignar» (regla del responsable del producto, 271a39ab).
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")

from app.core import db  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_slots  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.admin_panel import admin_content  # noqa: E402
from app.patient_web import catalog  # noqa: E402
from app.patient_web import doctor_photos  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app import main as app_module  # noqa: E402
from app.auth import turnstile  # noqa: E402
from app.core import config  # noqa: E402
import _dbtest  # noqa: E402

turnstile.ENV_PATH = ""
config.ENV_PATH = ""
_CLIENT = TestClient(app_module.app)

_db = _dbtest.PerModuleDb("sana_turnos_test_doctor_delete_refs", seed=lambda: db_operators.operators_list())

_PANEL_DOCTOR = {"id": "med-panel", "nombre": "Nora Panel", "especialidad": "Clínica médica",
                 "centro": "hospital"}
_FUTURE = datetime.now(timezone.utc) + timedelta(days=3)
_PAST = datetime.now(timezone.utc) - timedelta(days=3)


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON, _TEST_URL = _db.have_db, _db.skip_reason, _db.test_url


def tearDownModule():
    _db.tear_down()


def _admin():
    return {"Cookie": "st_admin=" + admin_auth._mint_token(admin_auth._admin_user(), "admin")}


def _sql(query, params=()):
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(query, params)


class _Base(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self._tmp = tempfile.mkdtemp(prefix="doctor-refs-")
        webdir = os.path.join(self._tmp, "webdata")
        os.makedirs(webdir)
        with open(os.path.join(webdir, "medicos.json"), "w", encoding="utf-8") as f:
            json.dump({"medicos": [{"id": "med-base", "nombre": "Ana Base",
                                    "especialidad": "Clínica médica"}]}, f)
        _env.set_env(self, TURNOS_WEBDATA_DIR=webdir)
        # Portraits and center photos land in the test's own folders, never
        # in the checkout's data/ that other test files and workers share.
        self.photos = os.path.join(self._tmp, "medico-fotos")
        self.center_photos = os.path.join(self._tmp, "centro-fotos")
        self.enterContext(unittest.mock.patch.object(doctor_photos, "PHOTOS_DIR", self.photos))
        self.enterContext(unittest.mock.patch.object(doctor_photos, "CENTER_PHOTOS_DIR", self.center_photos))
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        self._reset_rows()

    def _reset_rows(self):
        """Tablas en cero y el médico del panel de nuevo, sin tocar el
        entorno (setUp lo fija una sola vez, con _env.set_env)."""
        for table in ("operators", "catalog", "booking_requests", "interviews", "consultations",
                      "patient_summaries", "day_queue_events"):
            _sql("TRUNCATE %s RESTART IDENTITY CASCADE" % table)
        db_catalog.catalog_upsert("medicos", "med-panel", dict(_PANEL_DOCTOR))
        catalog.invalidate()

    def tearDown(self):
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _request(self, status, when, **cols):
        names = ["dni", "service_id", "status", "appointment_at"] + list(cols)
        values = ["30000001", "clinica", status, when] + list(cols.values())
        _sql("INSERT INTO booking_requests (%s) VALUES (%s)"
             % (", ".join(names), ", ".join(["%s"] * len(names))), values)


class KeptWhenAnythingNamesIt(_Base):
    REFERENCES = {
        "entrevista": ("INSERT INTO interviews (doctor_id, session_key) VALUES ('med-panel', 'k1')",),
        "consulta": ("INSERT INTO interviews (doctor_id, session_key) VALUES ('med-panel', 'k2')",
                     "INSERT INTO consultations (interview_id, doctor_id) VALUES "
                     "((SELECT id FROM interviews WHERE session_key='k2'), 'med-panel')"),
        "resumen": ("INSERT INTO patient_summaries (dni, doctor_id, resumen) "
                    "VALUES ('30000001', 'med-panel', '{}')",),
        "cola del día": ("INSERT INTO day_queue_events (appointment_day, assigned_doctor_id) "
                         "VALUES (current_date, 'med-panel')",),
        "cuenta": ("INSERT INTO operators (username, pass_hash, role, doctor_id, active) "
                   "VALUES ('nora.panel', 'x', 'medico', 'med-panel', true)",),
    }

    def test_each_reference_keeps_the_doctor_disabled_not_deleted(self):
        for name, statements in self.REFERENCES.items():
            with self.subTest(name):
                self._reset_rows()
                for statement in statements:
                    _sql(statement)
                result = admin_content.delete_item("medicos", "med-panel")
                self.assertTrue(result["conservado"], name)
                row = db_catalog.catalog_get("medicos", "med-panel")
                self.assertIsNotNone(row, name)
                self.assertTrue(row["borrado"], name)

    def test_a_past_appointment_booked_on_its_agenda_keeps_it(self):
        self._request("aprobada", _PAST, slot_doctor_id="med-panel")
        result = admin_content.delete_item("medicos", "med-panel")
        self.assertTrue(result["conservado"])
        self.assertEqual(result["turnos"], 1)
        self.assertIsNotNone(db_catalog.catalog_get("medicos", "med-panel"))

    def test_nothing_names_it_so_it_is_really_deleted(self):
        result = admin_content.delete_item("medicos", "med-panel")
        self.assertFalse(result["conservado"])
        self.assertIsNone(db_catalog.catalog_get("medicos", "med-panel"))


def _portrait(photos, mid):
    """What `doctor_photos._save_portrait` leaves for `mid`: a private bundle
    (original, portrait, metadata) under `.originals/<mid>/`, its lock, and
    the public `<mid>.webp` pointer into the bundle."""
    bundle = os.path.join(photos, ".originals", mid, "portrait-test")
    os.makedirs(bundle)
    for name in ("original", "portrait.webp", "metadata.json"):
        with open(os.path.join(bundle, name), "wb") as f:
            f.write(b"x")
    open(os.path.join(photos, ".originals", mid, "lock"), "a").close()
    os.symlink(os.path.relpath(os.path.join(bundle, "portrait.webp"), photos),
               os.path.join(photos, mid + ".webp"))


class PhotosGoWithTheRecord(_Base):
    """A doctor or center deleted for good takes its photos along (2026-11-06):
    the id is derived from the name, so a new doctor with the same name used to
    show the previous person's face, and the private original of someone no
    longer in the catalog stayed on disk. A record that is only disabled keeps
    them, so restoring it brings the photo back."""

    def setUp(self):
        super().setUp()
        _portrait(self.photos, "med-panel")
        _portrait(self.photos, "med-otro")
        os.makedirs(self.center_photos)
        for slug in ("centro-panel", "centro-otro"):
            with open(os.path.join(self.center_photos, slug + ".jpg"), "wb") as f:
                f.write(b"x")
        db_catalog.catalog_upsert("centros", "centro-panel", {"slug": "centro-panel", "nombre": "Centro Panel"})
        catalog.invalidate()

    def _doctor_files(self, mid):
        return [p for p in (os.path.join(self.photos, mid + ".webp"),
                            os.path.join(self.photos, ".originals", mid)) if os.path.lexists(p)]

    def test_a_doctor_deleted_for_good_loses_portrait_and_original(self):
        result = admin_content.delete_item("medicos", "med-panel")
        self.assertFalse(result["conservado"])
        self.assertEqual(self._doctor_files("med-panel"), [])
        self.assertEqual(len(self._doctor_files("med-otro")), 2, "another doctor's photo is untouched")

    def test_a_disabled_doctor_keeps_its_photos(self):
        for name, statements in KeptWhenAnythingNamesIt.REFERENCES.items():
            with self.subTest(name):
                self._reset_rows()
                for statement in statements:
                    _sql(statement)
                self.assertTrue(admin_content.delete_item("medicos", "med-panel")["conservado"])
                self.assertEqual(len(self._doctor_files("med-panel")), 2, name)

    def test_a_doctor_with_past_appointments_keeps_its_photos(self):
        self._request("aprobada", _PAST, assigned_doctor_id="med-panel")
        self.assertTrue(admin_content.delete_item("medicos", "med-panel")["conservado"])
        self.assertEqual(len(self._doctor_files("med-panel")), 2)

    def test_a_doctor_without_photos_is_deleted_all_the_same(self):
        shutil.rmtree(self.photos)
        self.assertFalse(admin_content.delete_item("medicos", "med-panel")["conservado"])
        self.assertIsNone(db_catalog.catalog_get("medicos", "med-panel"))

    def test_a_photo_that_cannot_be_removed_keeps_the_record(self):
        with unittest.mock.patch.object(doctor_photos.shutil, "rmtree", side_effect=PermissionError):
            with self.assertRaises(admin_content.ContentError) as raised:
                admin_content.delete_item("medicos", "med-panel")
        self.assertEqual(raised.exception.status, 507)
        self.assertIsNotNone(db_catalog.catalog_get("medicos", "med-panel"))

    def test_a_center_deleted_for_good_loses_its_photo(self):
        self.assertFalse(admin_content.delete_item("centros", "centro-panel")["conservado"])
        self.assertFalse(os.path.exists(os.path.join(self.center_photos, "centro-panel.jpg")))
        self.assertTrue(os.path.exists(os.path.join(self.center_photos, "centro-otro.jpg")))

    def test_a_center_with_appointments_keeps_its_photo(self):
        _sql("INSERT INTO booking_requests (dni, service_id, status, center_slug) "
             "VALUES ('30000001', 'clinica', 'aprobada', 'centro-panel')")
        self.assertTrue(admin_content.delete_item("centros", "centro-panel")["conservado"])
        self.assertTrue(os.path.exists(os.path.join(self.center_photos, "centro-panel.jpg")))

    def test_an_id_never_reaches_outside_its_folder(self):
        outside = os.path.join(self._tmp, "escape.webp")
        open(outside, "wb").close()
        os.makedirs(os.path.join(self._tmp, "escape"))
        for bad in ("../escape", "..", "", "med-panel/../med-otro", "med-panel\n", ".originals", None):
            with self.subTest(bad):
                self.assertEqual(doctor_photos.erase(bad), 0)
                self.assertEqual(doctor_photos.erase(bad, "centers"), 0)
        self.assertTrue(os.path.exists(outside))
        self.assertTrue(os.path.isdir(os.path.join(self._tmp, "escape")))
        self.assertEqual(len(self._doctor_files("med-panel")), 2)
        self.assertEqual(len(self._doctor_files("med-otro")), 2)


class EveryDoctorColumnIsChecked(_Base):
    def test_live_schema_has_no_doctor_id_column_outside_the_registry(self):
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("""SELECT table_name, column_name FROM information_schema.columns
                           WHERE table_schema = current_schema()
                             AND column_name LIKE '%%doctor_id'""")
            live = {(r["table_name"], r["column_name"]) for r in cur.fetchall()}
        known = set(db_slots.DOCTOR_ID_COLUMNS) | {("booking_requests", "assigned_doctor_id"),
                                             ("booking_requests", "slot_doctor_id")}
        self.assertEqual(live - known, set(),
                         "a new column holding a doctor id must go into db_slots.DOCTOR_ID_COLUMNS")


class RefusedWithAppointmentsAhead(_Base):
    def test_future_approved_appointment_refuses(self):
        self._request("aprobada", _FUTURE, assigned_doctor_id="med-panel")
        with self.assertRaises(admin_content.ContentError) as raised:
            admin_content.delete_item("medicos", "med-panel")
        self.assertEqual(raised.exception.status, 409)
        self.assertEqual(raised.exception.payload["turnos_futuros"], 1)
        row = db_catalog.catalog_get("medicos", "med-panel")
        self.assertFalse(row.get("borrado"))

    def test_pending_request_with_a_slot_on_its_agenda_refuses(self):
        self._request("pendiente", _FUTURE, slot_doctor_id="med-panel")
        with self.assertRaises(admin_content.ContentError):
            admin_content.delete_item("medicos", "med-panel")

    def test_pending_request_only_assigned_is_released_not_refused(self):
        """Ilya's rule (271a39ab): disabling hands pending requests back to
        «sin asignar»; only a booked time with the doctor blocks."""
        self._request("pendiente", None, assigned_doctor_id="med-panel", assigned_doctor="Nora Panel")
        self._request("pendiente", _FUTURE, assigned_doctor_id="med-panel", assigned_doctor="Nora Panel")
        result = admin_content.delete_item("medicos", "med-panel")
        self.assertTrue(result["conservado"])
        self.assertTrue(db_catalog.catalog_get("medicos", "med-panel")["borrado"])
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM booking_requests WHERE assigned_doctor_id='med-panel'")
            self.assertEqual(cur.fetchone()["n"], 0)

    def test_cancelled_future_appointment_does_not_refuse(self):
        self._request("cancelada", _FUTURE, assigned_doctor_id="med-panel")
        result = admin_content.delete_item("medicos", "med-panel")
        self.assertTrue(result["conservado"])

    def test_http_answers_409_with_the_reason_and_keeps_the_account_active(self):
        _sql("INSERT INTO operators (username, pass_hash, role, doctor_id, active) "
             "VALUES ('nora.panel', 'x', 'medico', 'med-panel', true)")
        self._request("aprobada", _FUTURE, assigned_doctor="Nora Panel")
        response = _CLIENT.delete("/api/admin/content/doctors/med-panel", headers=_admin())
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("1 turno", response.json()["error"])
        # The remedy: where the desk moves or cancels them (remedies.py rule).
        self.assertEqual(response.json()["enlace"],
                         "/admin/solicitudes?medico=med-panel&estado=aprobada&proximos=1")
        self.assertTrue(db_operators.operator_by_doctor_id("med-panel")["activo"])

    def test_once_the_desk_cancels_it_the_doctor_is_disabled(self):
        self._request("aprobada", _FUTURE, assigned_doctor_id="med-panel")
        with self.assertRaises(admin_content.ContentError):
            admin_content.delete_item("medicos", "med-panel")
        _sql("UPDATE booking_requests SET status='cancelada' WHERE assigned_doctor_id='med-panel'")
        self.assertTrue(admin_content.delete_item("medicos", "med-panel")["conservado"])
        self.assertTrue(db_catalog.catalog_get("medicos", "med-panel")["borrado"])


class AnAlreadyDisabledDoctorStaysDisabled(_Base):
    """DuFau (prod, 23.10) was disabled before this change: disabling it
    again never brings it back to a public list."""

    def test_disabling_again_keeps_it_disabled(self):
        self._request("aprobada", _PAST, assigned_doctor_id="med-panel")
        self.assertTrue(admin_content.delete_item("medicos", "med-panel")["conservado"])
        catalog.invalidate()
        self.assertNotIn("med-panel", {m.get("id") for m in catalog.doctors()})
        self.assertTrue(admin_content.delete_item("medicos", "med-panel")["conservado"])
        catalog.invalidate()
        self.assertTrue(db_catalog.catalog_get("medicos", "med-panel")["borrado"])
        self.assertNotIn("med-panel", {m.get("id") for m in catalog.doctors()})


if __name__ == "__main__":
    unittest.main()
