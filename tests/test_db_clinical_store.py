"""test_db_clinical_store.py — the clinical statements of `core/db.py` against a real Postgres.

Stage 4 of the SQLAlchemy Core move converts the clinical group of `core/db.py`: the pre-visit
interviews and their consultations (`interview_*`, `consultation_*`, the red-triage bell), the
patient's health profile, the pre-generated chart summaries, the lab results (`lab_analysis_*`,
`patient_lab_upload_*`), the imported clinical history, «Mis pacientes» and the clinical access log
(`*_clinical_access*`, whose append-only trigger only lets the retention purge delete). This file
pins each of those functions row for row — returned shapes, ordering, windows, the locks of
`interview_bind` / `consultation_confirm`, the trigger — so the conversion is proven to change
nothing. It was written and run green on the hand-written SQL first, and passes unedited on Core.
"""
import hashlib
import json
import os
import sys
import threading
import unittest
from datetime import date, datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db, tz  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_clinical  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_db_clinical_store")

UTC = timezone.utc


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _sql(sql, params=None):
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


class DbCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("TRUNCATE patient_summaries, lab_report_analysis, clinical_history_records, "
                        "patient_lab_uploads, patient_health_profile, clinical_access_log, "
                        "hsi_appointments, hsi_patients, hsi_doctor_schedules RESTART IDENTITY")
            cur.execute("DELETE FROM consultations")
            cur.execute("DELETE FROM interviews")
            _dbtest.delete_booking_requests(cur)
            cur.execute("DELETE FROM users")


def _booking(dni="30111222", **cols):
    _dbtest.ensure_user(dni)
    sid = db_booking.create_booking_request(dni, "Ana", "Paz", "1990-01-01", "2255123456", "cardio", "hospital")
    if cols:
        sets = ", ".join(f"{c}=%s" for c in cols)
        _sql(f"UPDATE booking_requests SET {sets} WHERE id=%s", (*cols.values(), sid))
    return sid


def _interview(doctor_id="7", **kw):
    return db_clinical.interview_create(doctor_id=doctor_id, **kw)


def _set_interview(iid, **cols):
    sets = ", ".join(f"{c}=%s" for c in cols)
    _sql(f"UPDATE interviews SET {sets} WHERE id=%s", (*cols.values(), iid))


def _noon(day):
    """Noon of the installation-timezone day `day` (YYYY-MM-DD)."""
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, 12, tzinfo=tz.tzinfo())


def _consultation(interview_id, doctor_id="7", **kw):
    args = dict(diagnoses=[{"cie": "J00"}], exams=["rx"], meds=[{"n": "ibu"}], notes="n",
                receta_json={"r": 1}, orden_json={"o": 1}, receta_token=None, orden_token=None,
                created_by="dr")
    args.update(kw)
    return db_clinical.consultation_create(interview_id, doctor_id, args["diagnoses"], args["exams"],
                                  args["meds"], args["notes"], args["receta_json"],
                                  args["orden_json"], args["receta_token"], args["orden_token"],
                                  args["created_by"])


# ---- chart summaries -----------------------------------------------------------------

class TestSummaries(DbCase):
    def test_get_of_nothing_and_upsert_then_get(self):
        self.assertIsNone(db_clinical.summary_get("30111222", "7"))
        self.assertIsNone(db_clinical.summary_upsert("30111222", 7, "cardio", {"a": ["ñ", 1]}, "fp1", "model-a"))
        row = db_clinical.summary_get(30111222, "7")
        self.assertEqual({k: row[k] for k in ("dni", "doctor_id", "especialidad", "resumen", "fingerprint",
                                              "ai_model")},
                         {"dni": "30111222", "doctor_id": "7", "especialidad": "cardio",
                          "resumen": {"a": ["ñ", 1]}, "fingerprint": "fp1", "ai_model": "model-a"})
        self.assertEqual(set(row), {"id", "dni", "doctor_id", "especialidad", "resumen", "fingerprint",
                                    "ai_model", "generated_at"})
        self.assertIsInstance(row["generated_at"], datetime)
        self.assertIsNone(db_clinical.summary_get("30111222", "8"))
        self.assertIsNone(db_clinical.summary_get("30999999", "7"))

    def test_upsert_replaces_the_whole_summary(self):
        db_clinical.summary_upsert("1", "7", "cardio", {"a": 1, "b": 2}, "fp1", "m1")
        first = db_clinical.summary_get("1", "7")
        db_clinical.summary_upsert("1", "7", None, {"c": 3}, None, None)
        row = db_clinical.summary_get("1", "7")
        self.assertEqual((row["id"], row["resumen"], row["especialidad"], row["fingerprint"], row["ai_model"]),
                         (first["id"], {"c": 3}, None, None, None))
        self.assertGreaterEqual(row["generated_at"], first["generated_at"])
        self.assertEqual(len(_sql("SELECT 1 FROM patient_summaries")), 1)

    def test_none_arguments_become_empty_keys_and_a_json_null(self):
        db_clinical.summary_upsert(None, None, "x", None, "fp", "m")
        row = db_clinical.summary_get(None, None)
        self.assertEqual((row["dni"], row["doctor_id"], row["resumen"]), ("", "", None))
        self.assertEqual(_sql("SELECT resumen IS NULL AS n FROM patient_summaries"), [{"n": False}])

    def test_upcoming_lists_approved_appointments_with_a_doctor_inside_the_horizon(self):
        now = datetime.now(UTC)
        ok = _booking("30000001", status="aprobada", appointment_at=now + timedelta(hours=5),
                      assigned_doctor_id="7")
        _booking("30000001", status="aprobada", appointment_at=now + timedelta(hours=6),
                 assigned_doctor_id="7")  # the same (dni, doctor) twice -> once
        _booking("30000002", status="aprobada", appointment_at=now + timedelta(hours=30),
                 assigned_doctor_id="8")
        _booking("30000003", status="aprobada", appointment_at=now + timedelta(hours=40),
                 assigned_doctor_id="9")  # beyond the default horizon
        _booking("30000004", status="aprobada", appointment_at=now - timedelta(hours=1),
                 assigned_doctor_id="7")  # past
        _booking("30000005", status="pendiente", appointment_at=now + timedelta(hours=5),
                 assigned_doctor_id="7")
        _booking("30000006", status="aprobada", appointment_at=now + timedelta(hours=5))  # no doctor
        _booking("30000007", status="aprobada", appointment_at=now + timedelta(hours=5),
                 assigned_doctor_id="")
        _booking("30000008", status="aprobada", assigned_doctor_id="7")  # no time
        self.assertIsNotNone(ok)
        self.assertEqual(sorted(db_clinical.upcoming_for_summaries()), [("30000001", "7"), ("30000002", "8")])
        self.assertEqual(sorted(db_clinical.upcoming_for_summaries(horizon_hours=48)),
                         [("30000001", "7"), ("30000002", "8"), ("30000003", "9")])
        self.assertEqual(db_clinical.upcoming_for_summaries(horizon_hours="1"), [])


# ---- lab ----------------------------------------------------------------------------

class TestLabAnalysis(DbCase):
    def _upsert(self, **kw):
        args = dict(dni="30111222", report_hash="h1", prompt_hash="p1", fecha="2026-02-15",
                    sistema="alepo", tipo_estudio="hemograma", resumen="todo bien", tono="ok",
                    sensible=False, valores=[{"n": "Hb", "v": 13}], modelo="m")
        args.update(kw)
        return db_clinical.lab_analysis_upsert(args["dni"], args["report_hash"], args["prompt_hash"], args["fecha"],
                                      args["sistema"], args["tipo_estudio"], args["resumen"], args["tono"],
                                      args["sensible"], args["valores"], args["modelo"])

    def test_for_returns_the_rows_of_one_patient_with_the_day_as_text(self):
        self.assertEqual(db_clinical.lab_analysis_for("30111222"), [])
        self.assertIsNone(self._upsert())
        self._upsert(report_hash="h2", fecha=None, sensible=1)
        self._upsert(dni="30999999", report_hash="h3")
        rows = {r["report_hash"]: r for r in db_clinical.lab_analysis_for(" 30111222 ")}
        self.assertEqual(set(rows), {"h1", "h2"})
        self.assertEqual(set(rows["h1"]), {"id", "dni", "report_hash", "prompt_hash", "fecha", "sistema",
                                           "tipo_estudio", "resumen", "tono", "sensible", "valores",
                                           "modelo", "created"})
        self.assertEqual(rows["h1"]["fecha"], "2026-02-15")
        self.assertIsNone(rows["h2"]["fecha"])
        self.assertEqual((rows["h1"]["valores"], rows["h1"]["sensible"]), ([{"n": "Hb", "v": 13}], False))
        self.assertIs(rows["h2"]["sensible"], True)
        self.assertEqual(db_clinical.lab_analysis_for(None), [])
        self.assertEqual(db_clinical.lab_analysis_for("  "), [])

    def test_upsert_coerces_the_day_and_replaces_the_whole_reading(self):
        for given, stored in (("2026-02-15T10:00:00", "2026-02-15"), (date(2026, 3, 1), "2026-03-01"),
                              (datetime(2026, 4, 2, 23, 0), "2026-04-02"), ("2024-13-45", None),
                              ("15/02/2024", None), ("", None), (None, None)):
            with self.subTest(given=given):
                self._upsert(report_hash="d", fecha=given)
                self.assertEqual(db_clinical.lab_analysis_for("30111222")[0]["fecha"], stored)
        first = db_clinical.lab_analysis_for("30111222")[0]
        self._upsert(report_hash="d", prompt_hash="p2", sistema=None, tipo_estudio=None, resumen=None,
                     tono=None, sensible=True, valores={"x": 1}, modelo=None)
        row = db_clinical.lab_analysis_for("30111222")[0]
        self.assertEqual((row["id"], row["prompt_hash"], row["sistema"], row["resumen"], row["sensible"],
                          row["valores"], row["modelo"]), (first["id"], "p2", None, None, True, {"x": 1}, None))
        self.assertGreaterEqual(row["created"], first["created"])
        self.assertEqual(len(db_clinical.lab_analysis_for("30111222")), 1)

    def test_none_values_are_a_stored_json_null(self):
        self._upsert(valores=None)
        self.assertEqual(_sql("SELECT valores IS NULL AS sql_null, valores = 'null'::jsonb AS json_null "
                              "FROM lab_report_analysis"), [{"sql_null": False, "json_null": True}])

    def test_delete_reports_how_many_rows_went(self):
        self._upsert()
        self.assertEqual(db_clinical.lab_analysis_delete("30999999", "h1"), 0)
        self.assertEqual(db_clinical.lab_analysis_delete("30111222", "other"), 0)
        self.assertEqual(db_clinical.lab_analysis_delete("30111222", "h1"), 1)
        self.assertEqual(db_clinical.lab_analysis_delete("30111222", "h1"), 0)
        self.assertEqual(db_clinical.lab_analysis_for("30111222"), [])
        self.assertEqual(db_clinical.lab_analysis_delete(None, "h1"), 0)

    def test_fecha_or_none(self):
        self.assertEqual(db_clinical._fecha_or_none("2026-02-15"), date(2026, 2, 15))
        self.assertIsNone(db_clinical._fecha_or_none("2026-02-30"))


class TestClinicalHistory(DbCase):
    def test_by_dni_orders_by_day_with_the_undated_last(self):
        self.assertEqual(db_clinical.clinical_history_by_dni("30111222"), [])
        db_clinical.clinical_history_replace("30111222", [
            {"fecha": "2026-03-01", "texto": "tercero"}, {"fecha": "2025-01-01", "texto": "primero"},
            {"texto": "sin fecha"}, {"fecha": "no es fecha", "texto": "mala fecha"},
            {"fecha": "2025-06-01T10:00", "texto": "segundo"}, "no es dict", None])
        got = db_clinical.clinical_history_by_dni("30.111.222")
        self.assertEqual([r["texto"] for r in got][:3], ["primero", "segundo", "tercero"])
        self.assertEqual({r["texto"] for r in got[3:]}, {"sin fecha", "mala fecha"})
        self.assertEqual([r["texto"] for r in got[3:]], ["sin fecha", "mala fecha"])  # same NULL day: by id
        self.assertEqual(got[0], {"fecha": "2025-01-01", "texto": "primero", "sistema": "local_history"})

    def test_record_keeps_its_own_system_when_it_has_one(self):
        db_clinical.clinical_history_replace("1", [{"sistema": "alepo", "x": 1}, {"x": 2}], source_system="import")
        self.assertEqual(sorted(r["sistema"] for r in db_clinical.clinical_history_by_dni("1")), ["alepo", "import"])

    def test_blank_dni_has_no_history(self):
        for dni in (None, "", "sin dígitos"):
            self.assertEqual(db_clinical.clinical_history_by_dni(dni), [])

    def test_replace_swaps_one_dni_and_source_and_counts_the_dicts(self):
        self.assertEqual(db_clinical.clinical_history_replace("1", [{"a": 1}, {"a": 2}]), 2)
        db_clinical.clinical_history_replace("1", [{"b": 1}], source_system="other")
        db_clinical.clinical_history_replace("2", [{"c": 1}])
        self.assertEqual(db_clinical.clinical_history_replace("1", [{"a": 3}]), 1)
        self.assertEqual([r["a"] for r in db_clinical.clinical_history_by_dni("1") if "a" in r], [3])
        self.assertEqual(len(db_clinical.clinical_history_by_dni("1")), 2)  # the other source stays
        self.assertEqual(len(db_clinical.clinical_history_by_dni("2")), 1)
        self.assertEqual(db_clinical.clinical_history_replace("1", None), 0)
        self.assertEqual([r["b"] for r in db_clinical.clinical_history_by_dni("1")], [1])

    def test_replace_stores_the_record_its_hash_and_its_day(self):
        record = {"fecha": "2026-03-04", "nota": "ñandú", "z": [1, 2], "a": {"k": None}}
        db_clinical.clinical_history_replace("30.111.222", [record], source_system=" alepo ")
        row = _sql("SELECT * FROM clinical_history_records")[0]
        payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual((row["dni"], row["source_system"], row["occurred_on"]),
                         ("30111222", "alepo", date(2026, 3, 4)))
        self.assertEqual(row["record_hash"], hashlib.sha256(payload.encode("utf-8")).hexdigest())
        self.assertEqual(row["record"], record)

    def test_replace_requires_a_dni_and_a_source(self):
        for args in (("", [{}], "x"), ("abc", [{}], "x"), ("1", [{}], ""), ("1", [{}], None)):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    db_clinical.clinical_history_replace(*args)

    def test_replace_is_atomic(self):
        db_clinical.clinical_history_replace("1", [{"a": 1}])
        with self.assertRaises(Exception):
            db_clinical.clinical_history_replace("1", [{"b": 1}, {"b": 1}])  # same hash twice: unique violation
        self.assertEqual([r["a"] for r in db_clinical.clinical_history_by_dni("1")], [1])


class TestLabUploads(DbCase):
    def _insert(self, dni="30111222", by="30111222", **kw):
        _dbtest.ensure_user(by)
        return db_clinical.patient_lab_upload_insert(dni, by, **kw)

    def test_insert_returns_the_id_and_by_dni_hides_the_bytes(self):
        uid = self._insert(filename="lab.pdf", mime="application/pdf", archivo=b"%PDF\x00\xff",
                           texto="Hb 13", fecha="2026-02-15", sin_lectura=1)
        self.assertIsInstance(uid, int)
        row = db_clinical.patient_lab_uploads_by_dni("30.111.222")[0]
        self.assertEqual({k: row[k] for k in ("id", "dni", "uploaded_by_dni", "filename", "mime", "texto",
                                              "fecha", "sin_lectura")},
                         {"id": uid, "dni": "30111222", "uploaded_by_dni": "30111222",
                          "filename": "lab.pdf", "mime": "application/pdf", "texto": "Hb 13",
                          "fecha": "2026-02-15", "sin_lectura": True})
        self.assertEqual(set(row), {"id", "dni", "uploaded_by_dni", "filename", "mime", "texto", "fecha",
                                    "sin_lectura", "created"})
        self.assertIs(type(row), dict)
        self.assertEqual(_sql("SELECT archivo FROM patient_lab_uploads")[0]["archivo"], b"%PDF\x00\xff")

    def test_insert_cleans_the_dnis_and_the_day(self):
        uid = self._insert("30.111.222", "30111222", fecha="no es fecha")
        row = db_clinical.patient_lab_upload_get(uid, "30111222")
        self.assertEqual((row["dni"], row["fecha"], row["sin_lectura"]), ("30111222", None, False))

    def test_insert_needs_both_dnis(self):
        _dbtest.ensure_user("1")
        for args in (("", "1"), ("1", ""), (None, "1"), ("abc", "1")):
            with self.assertRaises(ValueError):
                db_clinical.patient_lab_upload_insert(*args)

    def test_by_dni_lists_the_live_ones_by_day_with_the_undated_last(self):
        late = self._insert(fecha="2026-03-01")
        early = self._insert(fecha="2026-01-01")
        undated = self._insert()
        gone = self._insert(fecha="2025-01-01")
        self._insert("30999999", "30999999", fecha="2025-01-01")
        db_clinical.patient_lab_upload_delete(gone, "30111222")
        self.assertEqual([r["id"] for r in db_clinical.patient_lab_uploads_by_dni("30111222")], [early, late, undated])
        self.assertEqual(db_clinical.patient_lab_uploads_by_dni(None), [])
        self.assertEqual(db_clinical.patient_lab_uploads_by_dni("abc"), [])

    def test_get_is_for_the_uploader_only(self):
        uid = self._insert("30111222", "30555555", texto="t")
        self.assertEqual(db_clinical.patient_lab_upload_get(uid, "30555555")["texto"], "t")
        self.assertEqual(db_clinical.patient_lab_upload_get(str(uid), "30.555.555")["id"], uid)
        self.assertIsNone(db_clinical.patient_lab_upload_get(uid, "30111222"))  # the patient it is about
        self.assertIsNone(db_clinical.patient_lab_upload_get(uid + 100, "30555555"))
        self.assertIsNone(db_clinical.patient_lab_upload_get("x", "30555555"))
        self.assertIsNone(db_clinical.patient_lab_upload_get(None, "30555555"))
        self.assertIsNone(db_clinical.patient_lab_upload_get(uid, ""))
        db_clinical.patient_lab_upload_delete(uid, "30555555")
        self.assertIsNone(db_clinical.patient_lab_upload_get(uid, "30555555"))

    def test_delete_wipes_the_file_the_text_and_the_name_but_keeps_the_row(self):
        uid = self._insert(filename="lab.pdf", archivo=b"bytes", texto="clinical", mime="application/pdf")
        self.assertFalse(db_clinical.patient_lab_upload_delete(uid, "30999999"))  # not the uploader
        self.assertEqual(_sql("SELECT archivo IS NOT NULL AS a FROM patient_lab_uploads"), [{"a": True}])
        self.assertTrue(db_clinical.patient_lab_upload_delete(uid, "30.111.222"))
        row = _sql("SELECT * FROM patient_lab_uploads")[0]
        self.assertEqual((row["archivo"], row["texto"], row["filename"]), (None, None, None))
        self.assertEqual((row["dni"], row["uploaded_by_dni"], row["mime"]), ("30111222", "30111222", "application/pdf"))
        self.assertIsInstance(row["deleted"], datetime)
        self.assertFalse(db_clinical.patient_lab_upload_delete(uid, "30111222"))  # already gone
        for args in (("x", "1"), (None, "1"), (uid, ""), (uid, None)):
            self.assertFalse(db_clinical.patient_lab_upload_delete(*args))

    def test_purge_expires_old_files_and_keeps_the_text_and_the_row(self):
        old1, old2, old3, fresh, empty = (self._insert(archivo=b"x", texto="t%d" % n) for n in range(5))
        _sql("UPDATE patient_lab_uploads SET archivo=NULL WHERE id=%s", (empty,))
        for uid in (old1, old2, old3, empty):
            _sql("UPDATE patient_lab_uploads SET created = now() - interval '40 days' WHERE id=%s", (uid,))
        self.assertEqual(db_clinical.purge_lab_upload_files(30, limit=2), 2)
        self.assertEqual(db_clinical.purge_lab_upload_files(30), 1)  # the third; `empty` is not a candidate
        self.assertEqual(db_clinical.purge_lab_upload_files(30), 0)  # idempotent
        rows = {r["id"]: r for r in _sql("SELECT id, archivo IS NULL AS no_file, texto FROM patient_lab_uploads")}
        self.assertEqual([rows[u]["no_file"] for u in (old1, old2, old3, fresh)], [True, True, True, False])
        self.assertEqual(rows[old1]["texto"], "t0")
        self.assertEqual(len(rows), 5)

    def test_purge_does_nothing_for_a_non_positive_or_unparsable_window(self):
        uid = self._insert(archivo=b"x")
        _sql("UPDATE patient_lab_uploads SET created = now() - interval '400 days' WHERE id=%s", (uid,))
        for args in ((0,), (-1,), ("x",), (None,), (30, 0), (30, -5), (30, "x")):
            with self.subTest(args=args):
                self.assertEqual(db_clinical.purge_lab_upload_files(*args), 0)
        self.assertEqual(_sql("SELECT archivo IS NOT NULL AS a FROM patient_lab_uploads"), [{"a": True}])
        self.assertEqual(db_clinical.purge_lab_upload_files("30"), 1)


# ---- access log -----------------------------------------------------------------------

class TestAccessLog(DbCase):
    def test_insert_one_row(self):
        self.assertIsNone(db_clinical.insert_clinical_access("ana", "medico", "ver_ficha", "hash1", target_id=42,
                                                    allowed=0))
        db_clinical.insert_clinical_access("bea", None, "ver_resumen", "hash1")
        rows = {r["actor"]: r for r in _sql("SELECT * FROM clinical_access_log")}
        self.assertEqual((rows["ana"]["actor_role"], rows["ana"]["action"], rows["ana"]["dni_hash"],
                          rows["ana"]["target_id"], rows["ana"]["allowed"]),
                         ("medico", "ver_ficha", "hash1", "42", False))
        self.assertEqual((rows["bea"]["actor_role"], rows["bea"]["target_id"], rows["bea"]["allowed"]),
                         ("", None, True))
        self.assertIsInstance(rows["ana"]["ts"], datetime)

    def test_insert_many_in_one_trip(self):
        self.assertIsNone(db_clinical.insert_clinical_access_many("ana", "medico", "ver_lista", []))
        self.assertIsNone(db_clinical.insert_clinical_access_many("ana", None, "ver_lista", None))
        self.assertEqual(_sql("SELECT 1 FROM clinical_access_log"), [])
        db_clinical.insert_clinical_access_many("ana", "medico", "ver_lista", [("h1", "a"), ("h2", None), ("h3", 7)])
        rows = _sql("SELECT actor, actor_role, action, dni_hash, target_id, allowed FROM clinical_access_log "
                    "ORDER BY id")
        self.assertEqual(rows, [
            {"actor": "ana", "actor_role": "medico", "action": "ver_lista", "dni_hash": "h1",
             "target_id": "a", "allowed": True},
            {"actor": "ana", "actor_role": "medico", "action": "ver_lista", "dni_hash": "h2",
             "target_id": None, "allowed": True},
            {"actor": "ana", "actor_role": "medico", "action": "ver_lista", "dni_hash": "h3",
             "target_id": "7", "allowed": True}])
        db_clinical.insert_clinical_access_many("bea", None, "x", [("h1", None)])
        self.assertEqual(_sql("SELECT actor_role FROM clinical_access_log WHERE actor='bea'"), [{"actor_role": ""}])

    def test_list_is_newest_first_for_one_patient(self):
        for n in range(4):
            _sql("INSERT INTO clinical_access_log (ts, actor, action, dni_hash) VALUES (%s, %s, 'a', 'h')",
                 (datetime(2026, 1, 1 + (n if n < 3 else 2), tzinfo=UTC), "u%d" % n))  # u2 and u3 tie on ts
        db_clinical.insert_clinical_access("otro", "r", "a", "h2")
        got = db_clinical.list_clinical_access("h")
        self.assertEqual([r["actor"] for r in got], ["u3", "u2", "u1", "u0"])
        self.assertEqual(set(got[0]), {"ts", "actor", "actor_role", "action", "target_id", "allowed"})
        self.assertIs(type(got[0]), dict)
        self.assertEqual([r["actor"] for r in db_clinical.list_clinical_access("h", limit=2)], ["u3", "u2"])
        self.assertEqual(len(db_clinical.list_clinical_access("h", limit=0)), 1)  # clamped to 1
        self.assertEqual(len(db_clinical.list_clinical_access("h", limit=10 ** 6)), 4)
        self.assertEqual(db_clinical.list_clinical_access("nobody"), [])

    def test_the_log_is_append_only(self):
        db_clinical.insert_clinical_access("ana", "medico", "a", "h")
        for sql in ("UPDATE clinical_access_log SET actor='x'", "DELETE FROM clinical_access_log"):
            with self.assertRaises(Exception) as caught:
                _sql(sql)
            self.assertIn("sólo agregado", str(caught.exception))
        self.assertEqual(len(_sql("SELECT 1 FROM clinical_access_log")), 1)

    def test_purge_deletes_only_what_is_more_than_a_year_old(self):
        for n, days in enumerate((800, 700, 500, 400, 100, 5)):
            _sql("INSERT INTO clinical_access_log (ts, actor, action, dni_hash) "
                 "VALUES (now() - make_interval(days => %s), %s, 'a', 'h')", (days, "d%d" % days))
        self.assertEqual(db_clinical.purge_clinical_access_log(450, limit=1), 1)  # the oldest first
        self.assertEqual(db_clinical.purge_clinical_access_log(450), 2)  # 700 and 500 days
        self.assertEqual(db_clinical.purge_clinical_access_log(1), 1)  # `days` below 365 is raised to 365: only d400 goes
        self.assertEqual(db_clinical.purge_clinical_access_log(365), 0)
        self.assertEqual(sorted(r["actor"] for r in _sql("SELECT actor FROM clinical_access_log")), ["d100", "d5"])

    def test_purge_is_the_only_deleter(self):
        _sql("INSERT INTO clinical_access_log (ts, actor, action, dni_hash) "
             "VALUES (now() - interval '800 days', 'old', 'a', 'h')")
        with self.assertRaises(Exception):
            _sql("DELETE FROM clinical_access_log")  # no purge setting: the trigger refuses
        self.assertEqual(db_clinical.purge_clinical_access_log(365), 1)


# ---- interviews --------------------------------------------------------------------------

class TestInterviewCreate(DbCase):
    def test_create_returns_the_whole_row_with_its_session_key_and_token(self):
        row = db_clinical.interview_create(doctor_id=7, entry_mode="qr", dni="30111222", entered_name="Ana",
                                  entered_surname="Paz", matched=1)
        self.assertEqual(row["session_key"], "interview:%d" % row["id"])
        self.assertGreaterEqual(len(row["patient_token"]), 30)
        self.assertEqual((row["doctor_id"], row["entry_mode"], row["request_id"], row["dni"],
                          row["entered_name"], row["entered_surname"], row["matched"], row["status"]),
                         ("7", "qr", None, "30111222", "Ana", "Paz", True, "active"))
        self.assertEqual(set(row), {"id", "doctor_id", "request_id", "entry_mode", "entered_name",
                                    "entered_surname", "matched", "dni", "session_key", "patient_token",
                                    "status", "triage", "summary", "brief", "differential",
                                    "suggested_exams", "red_flags", "ai_model", "analyzed_at", "created",
                                    "updated", "detached_request_id", "alert_seen_at", "alert_seen_by",
                                    "reopened_at"})
        self.assertEqual(db_clinical.interview_get(row["id"]), row)
        other = db_clinical.interview_create()
        self.assertIsNone(other["doctor_id"])
        self.assertEqual((other["entry_mode"], other["matched"]), ("qr", False))
        self.assertNotEqual(other["patient_token"], row["patient_token"])

    def test_get_and_the_lookups(self):
        row = _interview()
        self.assertEqual(db_clinical.interview_get(str(row["id"])), row)
        self.assertIsNone(db_clinical.interview_get(row["id"] + 1000))
        self.assertEqual(db_clinical.interview_by_patient_token(" %s " % row["patient_token"]), row)
        for token in (None, "", "  ", "nope"):
            self.assertIsNone(db_clinical.interview_by_patient_token(token))
        self.assertEqual(db_clinical.interviews_by_session_keys([row["session_key"], "interview:0", "", None]),
                         {row["session_key"]: {"id": row["id"], "dni": None}})
        self.assertEqual(db_clinical.interviews_by_session_keys([]), {})
        self.assertEqual(db_clinical.interviews_by_session_keys(None), {})
        self.assertEqual(db_clinical.interviews_by_session_keys(["", None]), {})

    def test_set_status(self):
        row = _interview()
        self.assertIsNone(db_clinical.interview_set_status(row["id"], "done"))
        got = db_clinical.interview_get(row["id"])
        self.assertEqual(got["status"], "done")
        self.assertGreaterEqual(got["updated"], row["updated"])


class TestInterviewBind(DbCase):
    def test_a_request_gets_its_first_interview(self):
        sid = _booking()
        row = db_clinical.interview_bind(sid, doctor_id=7, dni="30111222")
        self.assertEqual((row["request_id"], row["entry_mode"], row["matched"], row["doctor_id"]),
                         (sid, "post_booking", True, "7"))
        self.assertEqual(db_clinical.interview_for_request(sid), row)
        self.assertEqual(db_clinical.interview_for_request(str(sid)), row)
        self.assertIsNone(db_clinical.interview_for_request(sid + 1000))

    def test_create_with_a_request_goes_through_the_gate(self):
        sid = _booking()
        first = db_clinical.interview_create(doctor_id=7, request_id=sid, dni="30111222", matched=True)
        self.assertEqual((first["request_id"], first["entry_mode"]), (sid, "qr"))
        door = db_clinical.interview_create(doctor_id=7, request_id=sid, dni="30111222", matched=True)
        self.assertEqual((door["request_id"], door["matched"]), (None, False))  # one per request
        mine = db_clinical.interview_create(doctor_id=7, request_id=sid, entry_mode="post_booking", matched=True)
        self.assertEqual((mine["request_id"], mine["entry_mode"]), (sid, "post_booking"))
        self.assertEqual(db_clinical.interview_get(first["id"])["detached_request_id"], sid)  # the QR one made room
        again = db_clinical.interview_create(doctor_id=7, request_id=sid, entry_mode="post_booking")
        self.assertEqual(again["id"], mine["id"])  # the patient's own entry continues the interview

    def test_the_patients_own_entry_returns_the_existing_non_qr_interview(self):
        sid = _booking()
        first = db_clinical.interview_bind(sid, entry_mode="post_booking")
        self.assertEqual(db_clinical.interview_bind(sid, entry_mode="post_booking")["id"], first["id"])
        # the logged-in door scan continues it as well
        self.assertEqual(db_clinical.interview_bind(sid, entry_mode="qr", authenticated=True)["id"], first["id"])
        self.assertEqual(len(_sql("SELECT 1 FROM interviews")), 1)

    def test_an_anonymous_door_entry_never_gets_the_existing_interview(self):
        sid = _booking()
        first = db_clinical.interview_bind(sid, entry_mode="post_booking")
        door = db_clinical.interview_bind(sid, entry_mode="qr", dni="30111222", matched=True)
        self.assertNotEqual(door["id"], first["id"])
        self.assertEqual((door["request_id"], door["matched"], door["entry_mode"]), (None, False, "qr"))
        self.assertEqual(db_clinical.interview_get(first["id"])["request_id"], sid)

    def test_a_qr_interview_bound_to_the_request_is_detached_by_the_patients_entry(self):
        sid = _booking()
        qr = db_clinical.interview_bind(sid, entry_mode="qr", authenticated=False)
        self.assertEqual(qr["request_id"], sid)
        mine = db_clinical.interview_bind(sid, entry_mode="post_booking")
        self.assertNotEqual(mine["id"], qr["id"])
        self.assertEqual(mine["request_id"], sid)
        detached = db_clinical.interview_get(qr["id"])
        self.assertEqual((detached["request_id"], detached["detached_request_id"]), (None, sid))
        self.assertGreaterEqual(detached["updated"], qr["updated"])

    def test_a_second_qr_entry_starts_unbound_when_a_qr_interview_holds_the_request(self):
        sid = _booking()
        first = db_clinical.interview_bind(sid, entry_mode="qr")
        second = db_clinical.interview_bind(sid, entry_mode="qr", matched=True)
        self.assertEqual((second["request_id"], second["matched"]), (None, False))
        self.assertEqual(db_clinical.interview_get(first["id"])["request_id"], sid)
        third = db_clinical.interview_bind(sid, entry_mode="qr", authenticated=True, matched=True)
        self.assertEqual((third["request_id"], third["matched"]), (None, False))

    def test_it_waits_for_the_requests_advisory_lock(self):
        """The key `interview_request:<id>` serializes two entries for the same request."""
        sid = _booking()
        result = {}
        with db._connect() as other, other.cursor() as ocur:
            ocur.execute("SELECT pg_advisory_xact_lock(hashtext(%s)::bigint)", ("interview_request:%d" % sid,))
            thread = threading.Thread(target=lambda: result.update(row=db_clinical.interview_bind(sid)))
            thread.start()
            thread.join(1.0)
            self.assertTrue(thread.is_alive(), "must wait for the lock of its own request")
            other.commit()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["row"]["request_id"], sid)

    def test_it_locks_the_existing_interview_row(self):
        sid = _booking()
        first = db_clinical.interview_bind(sid)
        result = {}
        with db._connect() as other, other.cursor() as ocur:
            ocur.execute("SELECT id FROM interviews WHERE id=%s FOR UPDATE", (first["id"],))
            thread = threading.Thread(target=lambda: result.update(row=db_clinical.interview_bind(sid)))
            thread.start()
            thread.join(1.0)
            self.assertTrue(thread.is_alive(), "must wait for the row lock")
            other.commit()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["row"]["id"], first["id"])


class TestInterviewFlow(DbCase):
    def test_health_profile(self):
        self.assertEqual(db_clinical.health_profile_get("30111222"), {})
        self.assertEqual(db_clinical.health_profile_get(None), {})
        self.assertEqual(db_clinical.health_profile_get("  "), {})
        self.assertIsNone(db_clinical.health_profile_save("", {"a": 1}))
        self.assertIsNone(db_clinical.health_profile_save(None, {"a": 1}))
        self.assertEqual(_sql("SELECT 1 FROM patient_health_profile"), [])
        row = _interview(dni="30111222")
        db_clinical.health_profile_save(" 30111222 ", {"alergias": ["pen"], "nota": "ñ"}, row["id"])
        got = db_clinical.health_profile_get("30111222")
        self.assertEqual((got["dni"], got["datos"], got["source_interview_id"]),
                         ("30111222", {"alergias": ["pen"], "nota": "ñ"}, row["id"]))
        self.assertEqual(set(got), {"dni", "datos", "source_interview_id", "updated"})
        db_clinical.health_profile_save("30111222", None)
        got = db_clinical.health_profile_get("30111222")
        self.assertEqual((got["datos"], got["source_interview_id"]), ({}, None))
        self.assertEqual(len(_sql("SELECT 1 FROM patient_health_profile")), 1)

    def test_update_analysis_stores_the_reading_and_resets_the_bell_only_for_red(self):
        row = _interview()
        _set_interview(row["id"], alert_seen_at=datetime(2026, 1, 1, tzinfo=UTC), alert_seen_by="ana")
        self.assertIsNone(db_clinical.interview_update_analysis(
            row["id"], "AMARILLO", "resumen", {"b": 1}, [{"d": 1}], ["rx"], ["fiebre"], "model-x"))
        got = db_clinical.interview_get(row["id"])
        self.assertEqual((got["triage"], got["summary"], got["brief"], got["differential"],
                          got["suggested_exams"], got["red_flags"], got["ai_model"]),
                         ("AMARILLO", "resumen", {"b": 1}, [{"d": 1}], ["rx"], ["fiebre"], "model-x"))
        self.assertIsInstance(got["analyzed_at"], datetime)
        self.assertIsNotNone(got["alert_seen_at"])  # not red: the ack stays
        self.assertEqual(got["alert_seen_by"], "ana")
        db_clinical.interview_update_analysis(row["id"], "ROJO", "s", None, None, None, None, None)
        got = db_clinical.interview_get(row["id"])
        self.assertEqual((got["triage"], got["alert_seen_at"], got["alert_seen_by"]), ("ROJO", None, None))
        self.assertEqual((got["brief"], got["differential"], got["suggested_exams"], got["red_flags"],
                          got["ai_model"]), (None, None, None, None, None))
        self.assertEqual(_sql("SELECT brief IS NULL AS n FROM interviews WHERE id=%s", (row["id"],)),
                         [{"n": True}])

    def test_reopen_only_a_finished_interview(self):
        row = _interview()
        self.assertIsNone(db_clinical.interview_reopen(row["id"]))  # still active
        db_clinical.interview_set_status(row["id"], "done")
        got = db_clinical.interview_reopen(str(row["id"]))
        self.assertEqual((got["id"], got["status"]), (row["id"], "active"))
        self.assertIsInstance(got["reopened_at"], datetime)
        self.assertIsNone(db_clinical.interview_reopen(row["id"]))  # a second click: nothing to reopen

    def test_for_day_matched_interviews_of_those_doctors(self):
        s1, s2, s3 = _booking("30000001"), _booking("30000002"), _booking("30000003")
        a = db_clinical.interview_bind(s1, doctor_id=7)
        b = db_clinical.interview_bind(s2, doctor_id=8)
        c = db_clinical.interview_bind(s3, doctor_id=9)
        unmatched = _interview(doctor_id="7")
        for row in (a, b, c, unmatched):
            _set_interview(row["id"], created=_noon("2026-03-10"))
        _set_interview(c["id"], created=_noon("2026-03-11"))
        qr = db_clinical.interview_bind(_booking("30000004"), doctor_id=7, entry_mode="qr", authenticated=False)
        _set_interview(qr["id"], created=_noon("2026-03-10"))
        got = db_clinical.interviews_for_day(["7", 8, "", None, " "], "2026-03-10")
        self.assertEqual([r["id"] for r in got], [qr["id"], a["id"], b["id"]])  # QR rows first, then by id
        self.assertEqual([r["id"] for r in db_clinical.interviews_for_day([9], "2026-03-11")], [c["id"]])
        self.assertEqual(db_clinical.interviews_for_day([], "2026-03-10"), [])
        self.assertEqual(db_clinical.interviews_for_day(None, "2026-03-10"), [])
        self.assertEqual(db_clinical.interviews_for_day(["7"], "2026-03-12"), [])

    def test_day_boundaries_are_the_installation_timezone(self):
        row = _interview(doctor_id="7")
        sid = _booking()
        bound = db_clinical.interview_bind(sid, doctor_id=7)
        local_midnight = datetime(2026, 3, 10, 0, 0, tzinfo=tz.tzinfo())
        _set_interview(bound["id"], created=local_midnight)
        self.assertEqual([r["id"] for r in db_clinical.interviews_for_day(["7"], "2026-03-10")], [bound["id"]])
        _set_interview(bound["id"], created=local_midnight - timedelta(seconds=1))
        self.assertEqual(db_clinical.interviews_for_day(["7"], "2026-03-10"), [])
        self.assertEqual([r["id"] for r in db_clinical.interviews_for_day(["7"], "2026-03-09")], [bound["id"]])
        self.assertIsNotNone(row)

    def test_by_request_ids_maps_each_request_to_its_interview(self):
        s1, s2 = _booking("30000001"), _booking("30000002")
        a, b = db_clinical.interview_bind(s1), db_clinical.interview_bind(s2)
        self.assertEqual(db_clinical.interviews_by_request_ids([s1, str(s2), "x", "", None, s2 + 1000]),
                         {s1: a, s2: b})
        self.assertEqual(db_clinical.interviews_by_request_ids([]), {})
        self.assertEqual(db_clinical.interviews_by_request_ids(None), {})
        self.assertEqual(db_clinical.interviews_by_request_ids(["x"]), {})

    def test_unmatched_for_day(self):
        a, b, c = _interview(doctor_id="7"), _interview(doctor_id="7"), _interview(doctor_id="8")
        bound = db_clinical.interview_bind(_booking(), doctor_id=7)
        for row in (a, b, c, bound):
            _set_interview(row["id"], created=_noon("2026-03-10"))
        _set_interview(_interview(doctor_id="7")["id"], created=_noon("2026-03-11"))
        self.assertEqual([r["id"] for r in db_clinical.interviews_unmatched_for_day(7, "2026-03-10")], [a["id"], b["id"]])
        self.assertEqual([r["id"] for r in db_clinical.interviews_unmatched_for_day("8", "2026-03-10")], [c["id"]])
        self.assertEqual(db_clinical.interviews_unmatched_for_day("9", "2026-03-10"), [])


# ---- consultations -------------------------------------------------------------------------

class TestConsultations(DbCase):
    def test_create_stores_the_documents_and_returns_the_row(self):
        iv = _interview()
        row = _consultation(iv["id"], receta_token="rt", orden_token="ot")
        self.assertEqual((row["interview_id"], row["doctor_id"], row["diagnoses"], row["exams"],
                          row["meds"], row["notes"], row["receta_json"], row["orden_json"],
                          row["receta_token"], row["orden_token"], row["created_by"]),
                         (iv["id"], "7", [{"cie": "J00"}], ["rx"], [{"n": "ibu"}], "n", {"r": 1}, {"o": 1},
                          "rt", "ot", "dr"))
        self.assertEqual(set(row), {"id", "interview_id", "doctor_id", "diagnoses", "exams", "meds", "notes",
                                    "receta_json", "orden_json", "receta_token", "orden_token", "created",
                                    "created_by", "superseded_at", "superseded_by"})
        self.assertIsNone(row["superseded_at"])

    def test_create_keeps_missing_documents_as_sql_null(self):
        iv = _interview()
        row = _consultation(iv["id"], receta_json=None, orden_json=None, notes=None, created_by=None)
        self.assertEqual((row["receta_json"], row["orden_json"], row["notes"], row["created_by"]),
                         (None, None, None, None))
        self.assertEqual(_sql("SELECT receta_json IS NULL AS r FROM consultations"), [{"r": True}])

    def test_by_token_matches_either_document(self):
        iv = _interview()
        row = _consultation(iv["id"], receta_token="rt", orden_token="ot")
        self.assertEqual(db_clinical.consultation_by_token("rt"), row)
        self.assertEqual(db_clinical.consultation_by_token(" ot "), row)
        for token in (None, "", "  ", "nope"):
            self.assertIsNone(db_clinical.consultation_by_token(token))

    def test_confirm_is_one_current_consultation_per_interview(self):
        iv = _interview()
        first = db_clinical.consultation_confirm(iv["id"], 7, [1], [2], [3], "n", {"r": 1}, {"o": 1}, "rt1", "ot1", "dr")
        self.assertEqual((first["interview_id"], first["doctor_id"], first["diagnoses"], first["created_by"]),
                         (iv["id"], "7", [1], "dr"))
        with self.assertRaises(db_clinical.ConsultationExists) as caught:
            db_clinical.consultation_confirm(iv["id"], 7, [9], [], [], None, None, None, "rt2", "ot2", "dr")
        self.assertEqual(caught.exception.vigente, first)
        with self.assertRaises(db_clinical.ConsultationExists) as caught:  # names another consultation
            db_clinical.consultation_confirm(iv["id"], 7, [9], [], [], None, None, None, "rt2", "ot2", "dr",
                                    reemplaza_id=first["id"] + 99)
        self.assertEqual(caught.exception.vigente, first)
        self.assertEqual(len(_sql("SELECT 1 FROM consultations")), 1)

    def test_confirm_replaces_the_named_current_one(self):
        iv = _interview()
        first = db_clinical.consultation_confirm(iv["id"], 7, [1], [], [], None, None, None, "rt1", "ot1", "dr")
        second = db_clinical.consultation_confirm(iv["id"], 7, [2], [], [], None, None, None, "rt2", "ot2", "dr",
                                         reemplaza_id=str(first["id"]))
        self.assertNotEqual(second["id"], first["id"])
        self.assertIsNone(second["superseded_at"])
        old = _sql("SELECT superseded_at, superseded_by FROM consultations WHERE id=%s", (first["id"],))[0]
        self.assertEqual(old["superseded_by"], second["id"])
        self.assertIsInstance(old["superseded_at"], datetime)
        self.assertEqual(db_clinical.consultation_current_for_interview(iv["id"])["id"], second["id"])

    def test_confirm_serializes_on_the_interview_row(self):
        iv = _interview()
        result = {}
        with db._connect() as other, other.cursor() as ocur:
            ocur.execute("SELECT id FROM interviews WHERE id=%s FOR UPDATE", (iv["id"],))
            thread = threading.Thread(target=lambda: result.update(row=db_clinical.consultation_confirm(
                iv["id"], 7, [1], [], [], None, None, None, "rt", "ot", "dr")))
            thread.start()
            thread.join(1.0)
            self.assertTrue(thread.is_alive(), "must wait for the interview row")
            other.commit()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["row"]["interview_id"], iv["id"])

    def test_current_for_interview_counts_the_replacements(self):
        iv = _interview()
        self.assertIsNone(db_clinical.consultation_current_for_interview(iv["id"]))
        first = db_clinical.consultation_confirm(iv["id"], 7, [1], [], [], None, None, None, "a1", "b1", "dr")
        self.assertEqual(db_clinical.consultation_current_for_interview(str(iv["id"]))["reemplazos"], 0)
        second = db_clinical.consultation_confirm(iv["id"], 7, [2], [], [], None, None, None, "a2", "b2", "dr",
                                         reemplaza_id=first["id"])
        third = db_clinical.consultation_confirm(iv["id"], 7, [3], [], [], None, None, None, "a3", "b3", "dr",
                                        reemplaza_id=second["id"])
        got = db_clinical.consultation_current_for_interview(iv["id"])
        self.assertEqual((got["id"], got["reemplazos"]), (third["id"], 2))
        self.assertEqual({k for k in got} - set(third), {"reemplazos"})

    def test_replacement_of_follows_the_chain(self):
        iv = _interview()
        first = db_clinical.consultation_confirm(iv["id"], 7, [1], [], [], None, None, None, "a1", "b1", "dr")
        second = db_clinical.consultation_confirm(iv["id"], 7, [2], [], [], None, None, None, "a2", "b2", "dr",
                                         reemplaza_id=first["id"])
        third = db_clinical.consultation_confirm(iv["id"], 7, [3], [], [], None, None, None, "a3", "b3", "dr",
                                        reemplaza_id=second["id"])
        old = _sql("SELECT * FROM consultations WHERE id=%s", (first["id"],))[0]
        self.assertEqual(db_clinical.consultation_replacement_of(old)["id"], third["id"])
        self.assertIsNone(db_clinical.consultation_replacement_of(third))  # still current
        self.assertIsNone(db_clinical.consultation_replacement_of(None))
        self.assertIsNone(db_clinical.consultation_replacement_of({}))

    def test_replacement_of_falls_back_to_the_current_one_when_the_chain_is_cut(self):
        iv = _interview()
        first = db_clinical.consultation_confirm(iv["id"], 7, [1], [], [], None, None, None, "a1", "b1", "dr")
        second = db_clinical.consultation_confirm(iv["id"], 7, [2], [], [], None, None, None, "a2", "b2", "dr",
                                         reemplaza_id=first["id"])
        old = dict(_sql("SELECT * FROM consultations WHERE id=%s", (first["id"],))[0])
        old["superseded_by"] = None
        self.assertEqual(db_clinical.consultation_replacement_of(old)["id"], second["id"])
        old["superseded_by"] = 987654  # points nowhere
        self.assertEqual(db_clinical.consultation_replacement_of(old)["id"], second["id"])

    def test_replacement_of_gives_up_on_a_loop(self):
        iv = _interview()
        a = _consultation(iv["id"], receta_token="a", orden_token="b")
        _sql("UPDATE consultations SET superseded_at=now() WHERE id=%s", (a["id"],))
        b = _consultation(iv["id"], receta_token="c", orden_token="d")
        _sql("UPDATE consultations SET superseded_at=now(), superseded_by=%s WHERE id=%s", (b["id"], a["id"]))
        _sql("UPDATE consultations SET superseded_at=now(), superseded_by=%s WHERE id=%s", (a["id"], b["id"]))
        row = _sql("SELECT * FROM consultations WHERE id=%s", (a["id"],))[0]
        self.assertIsNone(db_clinical.consultation_replacement_of(row, hops=5))

    def test_confirmed_for_maps_interviews_with_a_current_consultation(self):
        a, b, c = _interview(), _interview(), _interview()
        ca = db_clinical.consultation_confirm(a["id"], 7, [1], [], [], None, None, None, "a1", "b1", "dr")
        db_clinical.consultation_confirm(a["id"], 7, [2], [], [], None, None, None, "a2", "b2", "dr",
                                reemplaza_id=ca["id"])
        db_clinical.consultation_confirm(b["id"], 7, [1], [], [], None, None, None, "a3", "b3", "dr")
        got = db_clinical.consultations_confirmed_for([a["id"], str(b["id"]), c["id"], "x", "", None])
        self.assertEqual(set(got), {a["id"], b["id"]})
        self.assertIsInstance(got[a["id"]], datetime)
        self.assertEqual(db_clinical.consultations_confirmed_for([]), {})
        self.assertEqual(db_clinical.consultations_confirmed_for(None), {})
        self.assertEqual(db_clinical.consultations_confirmed_for(["x"]), {})

    def test_latest_for_interview_includes_the_superseded_ones(self):
        iv = _interview()
        self.assertIsNone(db_clinical.consultation_latest_for_interview(iv["id"]))
        first = db_clinical.consultation_confirm(iv["id"], 7, [1], [], [], None, None, None, "a1", "b1", "dr")
        self.assertEqual(db_clinical.consultation_latest_for_interview(str(iv["id"]))["id"], first["id"])
        second = db_clinical.consultation_confirm(iv["id"], 7, [2], [], [], None, None, None, "a2", "b2", "dr",
                                         reemplaza_id=first["id"])
        self.assertEqual(db_clinical.consultation_latest_for_interview(iv["id"])["id"], second["id"])


# ---- «Mis pacientes» and the red-triage bell ----------------------------------------------

class TestMyPatients(DbCase):
    def test_patients_of_doctor_by_assignment_or_booked_slot_newest_first(self):
        now = datetime.now(UTC)
        a = _booking("30000001", assigned_doctor_id="7", created=now - timedelta(days=10))
        b = _booking("30000002", slot_doctor_id="7", created=now - timedelta(days=5))
        c = _booking("30000003", assigned_doctor_id="7", slot_doctor_id="8", created=now - timedelta(days=1))
        _booking("30000004", assigned_doctor_id="8", created=now - timedelta(days=1))
        old = _booking("30000005", assigned_doctor_id="7", created=now - timedelta(days=400))
        got = db_clinical.patients_of_doctor(7)
        self.assertEqual([r["id"] for r in got], [c, b, a])
        self.assertEqual((got[0]["nombre"], got[0]["estado"], got[0]["medico_asignado_id"]),
                         ("Ana", "pendiente", "7"))  # wired
        self.assertEqual([r["id"] for r in db_clinical.patients_of_doctor("7", completo=True)], [c, b, a, old])
        self.assertEqual([r["id"] for r in db_clinical.patients_of_doctor("7", meses=1)], [c, b, a])
        self.assertEqual([r["id"] for r in db_clinical.patients_of_doctor("7", meses=0)], [])
        self.assertEqual(db_clinical.patients_of_doctor(None), [])
        self.assertEqual(db_clinical.patients_of_doctor(""), [])

    def test_patients_of_doctor_pages_with_a_keyset_cursor(self):
        now = datetime.now(UTC).replace(microsecond=0)
        same = now - timedelta(days=1)
        ids = [_booking("3000000%d" % n, assigned_doctor_id="7",
                        created=same if n in (1, 2) else now - timedelta(days=2 + n)) for n in range(5)]
        first = db_clinical.patients_of_doctor("7", limit=2)
        self.assertEqual(len(first), 2)
        page2 = db_clinical.patients_of_doctor("7", limit=2, cursor=(first[-1]["created"], first[-1]["id"]))
        page3 = db_clinical.patients_of_doctor("7", limit=2, cursor=(page2[-1]["created"], page2[-1]["id"]))
        self.assertEqual([r["id"] for r in first + page2 + page3], [ids[2], ids[1], ids[0], ids[3], ids[4]])
        self.assertEqual(db_clinical.patients_of_doctor("7", cursor=(None, None)), first + page2 + page3)
        self.assertEqual(db_clinical.patients_of_doctor("7", cursor="bad"), first + page2 + page3)
        self.assertEqual(db_clinical.patients_of_doctor("7", limit=0)[0]["id"], ids[2])  # 0 -> 500
        self.assertEqual(len(db_clinical.patients_of_doctor("7", limit=-3)), 1)  # clamped to 1

    def test_doctor_has_patient_by_booking_slot_or_interview(self):
        _booking("30000001", assigned_doctor_id="7")
        _booking("30000002", slot_doctor_id="7")
        _interview(doctor_id="7", dni="30000003", matched=True)
        _booking("30000004", assigned_doctor_id="8")
        for dni in ("30000001", "30000002", "30000003"):
            self.assertIs(db_clinical.doctor_has_patient("7", dni), True, dni)
            self.assertIs(db_clinical.doctor_has_patient(7, int(dni)), True, dni)
        self.assertIs(db_clinical.doctor_has_patient("7", "30000004"), False)
        self.assertIs(db_clinical.doctor_has_patient("8", "30000001"), False)
        for args in (("", "30000001"), ("7", ""), (None, None)):
            self.assertIs(db_clinical.doctor_has_patient(*args), False)

    def test_a_rejected_or_cancelled_request_does_not_make_the_patient_the_doctors(self):
        # Security review 2026-11 (M-2): a request assigned to a doctor and then refused or
        # cancelled never reached the consulting room; an attended one still counts.
        attended = _booking("30000011", assigned_doctor_id="7", status="aprobada", attendance="presente")
        _booking("30000012", assigned_doctor_id="7", status="rechazada")
        _booking("30000013", slot_doctor_id="7", status="cancelada")
        _booking("30000014", assigned_doctor_id="7", status="pendiente")
        self.assertTrue(attended)
        self.assertIs(db_clinical.doctor_has_patient("7", "30000011"), True)
        self.assertIs(db_clinical.doctor_has_patient("7", "30000014"), True)
        self.assertIs(db_clinical.doctor_has_patient("7", "30000012"), False)
        self.assertIs(db_clinical.doctor_has_patient("7", "30000013"), False)
        _booking("30000012", assigned_doctor_id="7", status="aprobada")
        self.assertIs(db_clinical.doctor_has_patient("7", "30000012"), True)

    def test_an_unmatched_qr_interview_does_not_make_the_patient_the_doctors(self):
        # Security review 2026-11, bypass of M-2: the QR door takes any DNI from an anonymous
        # visitor (matched=False); that typed number is not a doctor-patient relationship.
        _interview(doctor_id="7", dni="30000021", entry_mode="qr", matched=False)
        _interview(doctor_id="7", dni="30000022", entry_mode="qr", matched=True)
        self.assertIs(db_clinical.doctor_has_patient("7", "30000021"), False)
        self.assertIs(db_clinical.doctor_has_patient("7", "30000022"), True)

    def test_the_roster_leaves_out_rejected_and_cancelled_requests(self):
        # Same bypass: «Mis pacientes» kept the rows the access check already refuses.
        live = _booking("30000031", assigned_doctor_id="7", status="aprobada")
        _booking("30000032", assigned_doctor_id="7", status="rechazada")
        _booking("30000033", slot_doctor_id="7", status="cancelada")
        self.assertEqual([r["id"] for r in db_clinical.patients_of_doctor("7", completo=True)], [live])

    def test_interviews_of_doctor_like_patients_of_doctor(self):
        now = datetime.now(UTC)
        a = _interview(doctor_id="7")
        b = _interview(doctor_id="7")
        c = _interview(doctor_id="7")
        _interview(doctor_id="8")
        _set_interview(a["id"], created=now - timedelta(days=3))
        _set_interview(b["id"], created=now - timedelta(days=2))
        _set_interview(c["id"], created=now - timedelta(days=500))
        got = db_clinical.interviews_of_doctor(7)
        self.assertEqual([r["id"] for r in got], [b["id"], a["id"]])
        self.assertEqual([r["id"] for r in db_clinical.interviews_of_doctor("7", completo=True)], [b["id"], a["id"], c["id"]])
        self.assertEqual([r["id"] for r in db_clinical.interviews_of_doctor("7", limit=1)], [b["id"]])
        self.assertEqual([r["id"] for r in db_clinical.interviews_of_doctor("7", limit=1,
                                                                   cursor=(got[0]["created"], got[0]["id"]))],
                         [a["id"]])
        self.assertEqual(db_clinical.interviews_of_doctor("7", cursor=(None, 1)), got)
        self.assertEqual(db_clinical.interviews_of_doctor("7", cursor="bad"), got)  # not a (created, id) pair
        self.assertEqual(db_clinical.interviews_of_doctor("7", meses=0), [])
        self.assertEqual(db_clinical.interviews_of_doctor(None), [])
        self.assertEqual(db_clinical.interviews_of_doctor(""), [])

    def test_users_by_dnis_is_one_public_user_per_dni(self):
        for dni in ("30000001", "30000002"):
            _dbtest.ensure_user(dni)
        got = db_clinical.users_by_dnis(["30000002", 30000001, "30000009", "", None, "30000002"])
        self.assertEqual(sorted(got), ["30000001", "30000002"])
        self.assertEqual((got["30000001"]["dni"], got["30000001"]["nombre"]), ("30000001", "Test"))  # wired
        self.assertNotIn("pass_hash", got["30000001"])
        self.assertEqual(db_clinical.users_by_dnis([]), {})
        self.assertEqual(db_clinical.users_by_dnis(None), {})
        self.assertEqual(db_clinical.users_by_dnis(["", None]), {})


class TestRedAlerts(DbCase):
    def _red(self, doctor_id, minutes_ago=0, **cols):
        row = _interview(doctor_id=doctor_id)
        db_clinical.interview_update_analysis(row["id"], "ROJO", "s", {}, [], [], [], None)
        _set_interview(row["id"], created=datetime.now(UTC) - timedelta(minutes=minutes_ago), **cols)
        return row["id"]

    def test_alerts_are_the_unseen_red_interviews_newest_first(self):
        a, b, c = self._red("7", 30), self._red("7", 10), self._red("8", 20)
        seen = self._red("7", 5, alert_seen_at=datetime.now(UTC), alert_seen_by="ana")
        yellow = _interview(doctor_id="7")
        db_clinical.interview_update_analysis(yellow["id"], "AMARILLO", "s", {}, [], [], [], None)
        self.assertEqual([r["id"] for r in db_clinical.red_alerts()], [b, c, a])
        self.assertEqual([r["id"] for r in db_clinical.red_alerts(None)], [b, c, a])
        self.assertEqual([r["id"] for r in db_clinical.red_alerts(["7"])], [b, a])
        self.assertEqual([r["id"] for r in db_clinical.red_alerts([7, "8", "", None])], [b, c, a])
        self.assertEqual(db_clinical.red_alerts([]), [])
        self.assertEqual(db_clinical.red_alerts(["", None]), [])
        self.assertEqual(db_clinical.red_alerts(["9"]), [])
        self.assertNotIn(seen, [r["id"] for r in db_clinical.red_alerts()])
        self.assertEqual(set(db_clinical.red_alerts()[0]) >= {"id", "triage", "doctor_id"}, True)

    def test_count_matches_the_list(self):
        self._red("7", 30)
        self._red("7", 10)
        self._red("8", 20)
        self._red("7", 5, alert_seen_at=datetime.now(UTC))
        self.assertEqual(db_clinical.red_alert_count(), 3)
        self.assertEqual(db_clinical.red_alert_count(None), 3)
        self.assertEqual(db_clinical.red_alert_count(["7"]), 2)
        self.assertEqual(db_clinical.red_alert_count([7, "8"]), 3)
        self.assertEqual(db_clinical.red_alert_count([]), 0)
        self.assertEqual(db_clinical.red_alert_count(["", None]), 0)
        self.assertEqual(db_clinical.red_alert_count(["9"]), 0)

    def test_mark_seen_once(self):
        rid = self._red("7")
        got = db_clinical.interview_mark_alert_seen(rid, "ana")
        self.assertEqual((got["id"], got["alert_seen_by"]), (rid, "ana"))
        self.assertIsInstance(got["alert_seen_at"], datetime)
        self.assertIsNone(db_clinical.interview_mark_alert_seen(rid, "bea"))  # the first one to see it stays
        self.assertEqual(db_clinical.interview_get(rid)["alert_seen_by"], "ana")
        self.assertEqual(db_clinical.red_alert_count(), 0)
        other = self._red("7")
        self.assertIsNone(db_clinical.interview_mark_alert_seen(other + 1000, "ana"))
        self.assertIsNone(db_clinical.interview_mark_alert_seen(other, "")["alert_seen_by"])  # '' -> NULL
        again = self._red("7")
        self.assertIsNone(db_clinical.interview_mark_alert_seen(again, None)["alert_seen_by"])


if __name__ == "__main__":
    unittest.main()
