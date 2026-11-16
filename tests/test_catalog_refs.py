"""The read-only report of ids the database keeps and the catalog no longer
has (patient_web/catalog_refs.py, scripts/auditar_referencias_catalogo.py;
hospital schema review №10, 2026-10-24)."""

import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1")

import _dbtest  # noqa: E402
from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.patient_web import catalog, catalog_refs  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_catalog_refs", seed=lambda: db_booking.get_booking_request(-1))
HAVE_DB = False
DB_SKIP_REASON = ""


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


KNOWN = {"servicios": {"cardio"}, "centros": {"hospital"}, "medicos": {"clara"}}


def _insert_request(cur, service, center, assigned=None, slot=None):
    cur.execute(
        "INSERT INTO booking_requests (dni, service_id, center_slug, assigned_doctor_id,"
        " slot_doctor_id, status) VALUES ('1', %s, %s, %s, %s, 'pendiente')",
        (service, center, assigned, slot))


class TestBrokenReferences(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as conn, conn.cursor() as cur:
            for table, _, _ in catalog_refs.REFERENCES:
                cur.execute(f"DELETE FROM {table}")

    def _broken(self):
        return {(r["table"], r["column"], r["id"]): r["rows"]
                for r in catalog_refs.broken_references(known=KNOWN)}

    def test_everything_present_reports_nothing(self):
        with db._connect() as conn, conn.cursor() as cur:
            _insert_request(cur, "cardio", "hospital", assigned="clara", slot="clara")
            _insert_request(cur, "cardio", None)   # a request without a center is not a broken link
        self.assertEqual(self._broken(), {})

    def test_an_id_the_catalog_lost_is_listed_with_its_row_count(self):
        with db._connect() as conn, conn.cursor() as cur:
            _insert_request(cur, "cardio-viejo", "hospital", assigned="clara")
            _insert_request(cur, "cardio-viejo", "hospital", assigned="clara")
            _insert_request(cur, "cardio", "hospital-viejo", assigned="tomas", slot="inventado")
            cur.execute("INSERT INTO interviews (session_key, doctor_id) VALUES ('s1', 'tomas')")
            cur.execute("INSERT INTO waitlist (dni, service_id, center_slug) VALUES ('1', 'cardio', 'sin-centro')")
        self.assertEqual(self._broken(), {
            ("booking_requests", "service_id", "cardio-viejo"): 2,
            ("booking_requests", "center_slug", "hospital-viejo"): 1,
            ("booking_requests", "assigned_doctor_id", "tomas"): 1,
            ("booking_requests", "slot_doctor_id", "inventado"): 1,
            ("interviews", "doctor_id", "tomas"): 1,
            ("waitlist", "center_slug", "sin-centro"): 1,
        })

    def test_the_check_writes_nothing(self):
        with db._connect() as conn, conn.cursor() as cur:
            _insert_request(cur, "cardio-viejo", "hospital")
        catalog_refs.broken_references(known=KNOWN)
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM booking_requests")
            self.assertEqual(cur.fetchone()["n"], 1)

    def test_most_rows_first_then_by_id_in_registry_order(self):
        """Pinned on the hand-written SQL before the move to SQLAlchemy Core: the list order and
        the whole entry of each broken id."""
        with db._connect() as conn, conn.cursor() as cur:
            _insert_request(cur, "zeta", "hospital")
            _insert_request(cur, "beta", "hospital")
            _insert_request(cur, "alfa", "hospital")
            _insert_request(cur, "zeta", "lejos")
        self.assertEqual(catalog_refs.broken_references(known=KNOWN), [
            {"table": "booking_requests", "column": "service_id", "tipo": "servicios", "id": "zeta", "rows": 2},
            {"table": "booking_requests", "column": "service_id", "tipo": "servicios", "id": "alfa", "rows": 1},
            {"table": "booking_requests", "column": "service_id", "tipo": "servicios", "id": "beta", "rows": 1},
            {"table": "booking_requests", "column": "center_slug", "tipo": "centros", "id": "lejos", "rows": 1},
        ])

    def test_blank_ids_are_not_links_and_an_empty_catalog_knows_nothing(self):
        with db._connect() as conn, conn.cursor() as cur:
            _insert_request(cur, "", "", assigned="", slot=None)
            _insert_request(cur, "cardio", "hospital")
        self.assertEqual(self._broken(), {})
        empty = {"servicios": set(), "centros": set(), "medicos": set()}
        self.assertEqual({(r["column"], r["id"]): r["rows"] for r in catalog_refs.broken_references(known=empty)},
                         {("service_id", "cardio"): 1, ("center_slug", "hospital"): 1})
        several = {"servicios": {"alfa", "cardio", "zeta"}, "centros": {"hospital", "lejos"}, "medicos": set()}
        self.assertEqual(catalog_refs.broken_references(known=several), [])   # known = equal to ANY of them

    def test_an_unreadable_catalog_raises_instead_of_answering_all_good(self):
        with patch.object(catalog, "known_ids", side_effect=RuntimeError("catálogo ilegible")):
            with self.assertRaises(RuntimeError):
                catalog_refs.broken_references()

    def test_the_live_catalog_counts_disabled_and_incomplete_doctors_as_present(self):
        calls = []

        def fake_merged(tipo, **kw):
            calls.append((tipo, kw))
            key = "slug" if tipo == "centros" else "id"
            return [{key: "x"}, {key: "y", "_borrado": True}], {}

        with patch.object(catalog, "_merged", side_effect=fake_merged):
            self.assertEqual(catalog.known_ids("medicos"), {"x", "y"})
            catalog.known_ids("centros")
        self.assertTrue(all(kw.get("incluir_borrados") and kw.get("strict") for _, kw in calls))
        self.assertTrue(calls[0][1].get("incluir_incompletos"))


class TestRegistryIsComplete(unittest.TestCase):
    """A new column that stores a service / center / doctor id must join
    REFERENCES — otherwise the report silently skips it."""

    _CATALOG_COLUMNS = {"service_id", "center_slug", "doctor_id", "assigned_doctor_id", "slot_doctor_id"}

    def test_every_catalog_id_column_of_the_schema_is_registered(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT table_name, column_name FROM information_schema.columns"
                " WHERE table_schema = 'public' AND column_name = ANY(%s)", (sorted(self._CATALOG_COLUMNS),))
            real = {(r["table_name"], r["column_name"]) for r in cur.fetchall()}
        registered = {(t, c) for t, c, _ in catalog_refs.REFERENCES}
        self.assertEqual(real - registered, set(), "columns holding catalog ids missing from REFERENCES")
        self.assertEqual(registered - real, set(), "REFERENCES names a column the schema does not have")


class TestAuditScript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "auditar_referencias_catalogo", os.path.join(ROOT, "scripts", "auditar_referencias_catalogo.py"))
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def _run(self, rotas=None, boom=None, argv=()):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        out, err = io.StringIO(), io.StringIO()
        kw = {"side_effect": boom} if boom else {"return_value": rotas}
        with patch.object(catalog_refs, "broken_references", **kw), \
                redirect_stdout(out), redirect_stderr(err):
            code = self.mod.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_exit_0_when_nothing_is_broken(self):
        code, out, _ = self._run([])
        self.assertEqual(code, 0)
        self.assertIn("Todas las referencias", out)

    def test_exit_1_and_the_list_when_something_is_broken(self):
        rota = {"table": "interviews", "column": "doctor_id", "tipo": "medicos", "id": "tomas", "rows": 3}
        code, out, _ = self._run([rota])
        self.assertEqual(code, 1)
        self.assertIn("interviews.doctor_id  tomas  (3 filas)", out)
        code, out, _ = self._run([rota], argv=["--json"])
        self.assertEqual(json.loads(out)["referencias_rotas"], [rota])

    def test_exit_2_when_it_cannot_read(self):
        code, out, err = self._run(boom=RuntimeError("sin catálogo"))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("No se pudo revisar", err)


if __name__ == "__main__":
    unittest.main()
