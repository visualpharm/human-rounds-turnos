"""test_table_export.py — «Descargar» of a small panel table (2026-11-07):
`admin_panel/table_export.py` and `POST /api/admin/table-export`.

Covers: which screens download (small parts only — no Pacientes, no logs,
no Solicitudes), columns and order kept as sent, the row limit from
`TURNOS_EXPORT_MAX_ROWS` refusing with a sentence, the CSV shape Excel opens
(BOM, `;` in Spanish, `,` in English, CRLF, formula cells neutralised), the
human file name (screen + day), the screen's own permission deciding, and the
patients of the file written to the clinical access log once each.

No database: the gate, the session's permissions and the access log are
replaced at the router's edges; the domain module runs for real.

Run:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning \
      -m unittest discover -s tests -p "test_table_export.py"
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.admin_panel import table_export, table_export_router  # noqa: E402
from app.admin_panel._native_support import ApiError  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.core import access_log, config, permissions  # noqa: E402

config.ENV_PATH = ""
BOM = b"\xef\xbb\xbf"


def _body(screen="/admin/cola", rows=None, patients=None, **extra):
    body = {
        "screen": screen,
        "columns": ["Hora", "Paciente", "DNI"],
        "rows": rows if rows is not None else [["08:00", "Ana Pérez", "30.111.222"],
                                               ["08:20", "Juan Gómez", "28.333.444"]],
        "patients": patients if patients is not None else ["30111222", "28333444"],
    }
    body.update(extra)
    return body


class TestScreens(unittest.TestCase):
    def test_only_small_parts_download(self):
        for screen in ("/admin/cola", "/admin/mis-turnos", "/admin/my-patients", "/admin/reportes",
                       "/admin/doctors", "/admin/services", "/admin/centers", "/admin/users"):
            self.assertEqual(table_export.screen_of(screen), screen)
        # The big places have no button and the server refuses them too.
        for big in ("/admin/pacientes", "/admin/solicitudes", "/admin/access-log", "/admin/roles", "/admin/x"):
            self.assertIsNone(table_export.screen_of(big))

    def test_query_and_trailing_slash_are_the_same_screen(self):
        self.assertEqual(table_export.screen_of("/admin/reportes?view=busquedas"), "/admin/reportes")
        self.assertEqual(table_export.screen_of("/admin/doctors/?item=7"), "/admin/doctors")

    def test_every_screen_is_a_mapped_panel_screen(self):
        for screen in table_export.SCREENS:
            required = permissions.required_permission("GET", screen, screen)
            self.assertIsNot(required, permissions.UNMAPPED, screen)

    def test_route_is_open_to_any_staff_and_to_the_demo_session(self):
        self.assertEqual(permissions.required_permission("POST", "/api/admin/table-export"), permissions.ANY)
        self.assertIn("/api/admin/table-export", admin_auth._DEMO_MUTATION_ALLOW)
        self.assertIn(table_export.ACCESS_ACTION, access_log.ACTIONS)


class TestParse(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, TURNOS_CONFIG_LOCK="1", TURNOS_EXPORT_MAX_ROWS=None)

    def test_columns_and_order_are_kept(self):
        req = table_export.parse(_body(rows=[["09:00", "B"], ["08:00", "A", "1", "extra"]]))
        self.assertEqual(req["columns"], ["Hora", "Paciente", "DNI"])
        # Short rows are padded, long rows cut to the header; the order is the screen's.
        self.assertEqual(req["rows"], [["09:00", "B", ""], ["08:00", "A", "1"]])

    def test_unknown_screen_is_refused_with_a_sentence(self):
        with self.assertRaises(table_export.ExportError) as cm:
            table_export.parse(_body(screen="/admin/pacientes"))
        self.assertEqual(cm.exception.status, 404)
        self.assertEqual(cm.exception.code, "pantalla_sin_descarga")
        self.assertIn("lista grande", cm.exception.message)

    def test_row_limit_comes_from_the_setting(self):
        rows = [["08:00", "P%d" % i, ""] for i in range(6)]
        with _env.patched_env(TURNOS_EXPORT_MAX_ROWS="5"):
            with self.assertRaises(table_export.ExportError) as cm:
                table_export.parse(_body(rows=rows, patients=[]))
            self.assertEqual(cm.exception.status, 413)
            self.assertEqual(cm.exception.code, "demasiadas_filas")
            self.assertIn("Son 6 filas", cm.exception.message)
            self.assertIn("hasta 5", cm.exception.message)
            self.assertEqual(len(table_export.parse(_body(rows=rows[:5], patients=[]))["rows"]), 5)

    def test_default_limit_and_a_bad_setting(self):
        self.assertEqual(table_export.max_rows(), table_export.DEFAULT_MAX_ROWS)
        with _env.patched_env(TURNOS_EXPORT_MAX_ROWS="0"):
            self.assertEqual(table_export.max_rows(), table_export.DEFAULT_MAX_ROWS)
        with _env.patched_env(TURNOS_EXPORT_MAX_ROWS="many"):
            self.assertEqual(table_export.max_rows(), table_export.DEFAULT_MAX_ROWS)

    def test_malformed_bodies(self):
        for bad in (None, [], {"screen": "/admin/cola"}, _body(columns=[]), _body(rows="x"),
                    _body(rows=[["a"], "b"]), _body(columns=["c"] * (table_export.MAX_COLUMNS + 1))):
            with self.assertRaises(table_export.ExportError, msg=repr(bad)[:60]) as cm:
                table_export.parse(bad)
            self.assertIn(cm.exception.status, (400, 404))

    def test_title_and_day(self):
        req = table_export.parse(_body(title='Pacientes de hoy · Dra. "Pérez"/CAPS', day="2026-11-07"))
        self.assertEqual(req["title"], "Pacientes de hoy · Dra. Pérez CAPS")
        self.assertEqual(table_export.filename(req["title"], req["day"]), "Pacientes de hoy · Dra. Pérez CAPS 2026-11-07.csv")
        # No title: the screen's name; no valid day: today.
        req = table_export.parse(_body(screen="/admin/mis-turnos", day="mañana"))
        self.assertEqual(req["title"], "Mis turnos")
        self.assertRegex(req["day"], r"^\d{4}-\d{2}-\d{2}$")

    def test_access_items_once_per_patient_digits_only(self):
        req = table_export.parse(_body(rows=[["a"], ["b"], ["c"], ["d"]], patients=["30.111.222", "30111222", "", "28333444"]))
        self.assertEqual(table_export.access_items(req), [("30111222", "/admin/cola"), ("28333444", "/admin/cola")])
        # Patients beyond the rows sent are ignored.
        req = table_export.parse(_body(rows=[["a"]], patients=["1", "2"]))
        self.assertEqual(table_export.access_items(req), [("1", "/admin/cola")])


class TestCsv(unittest.TestCase):
    def test_spanish_file_for_excel(self):
        data = table_export.csv_bytes(["Hora", "Paciente"], [["08:00", "Ana; Pérez"], ["", 'Dice "hola"\nchau']], lang="es")
        self.assertTrue(data.startswith(BOM))
        text = data[len(BOM):].decode("utf-8")
        self.assertEqual(text, 'Hora;Paciente\r\n08:00;"Ana; Pérez"\r\n;"Dice ""hola""\nchau"\r\n')

    def test_english_uses_commas(self):
        text = table_export.csv_bytes(["Day", "Total"], [["Mon", "1,5"]], lang="en")[len(BOM):].decode("utf-8")
        self.assertEqual(text, 'Day,Total\r\nMon,"1,5"\r\n')
        self.assertEqual(table_export.delimiter("ja"), ",")
        self.assertEqual(table_export.delimiter("de"), ";")

    def test_formula_cells_are_neutralised_numbers_are_not(self):
        self.assertEqual(table_export.safe_cell("=HYPERLINK(\"x\")"), "'=HYPERLINK(\"x\")")
        self.assertEqual(table_export.safe_cell("@SUM(A1)"), "'@SUM(A1)")
        self.assertEqual(table_export.safe_cell("+54 9 2254 123456"), "+54 9 2254 123456")
        self.assertEqual(table_export.safe_cell("-12,5"), "-12,5")
        self.assertEqual(table_export.safe_cell("-"), "-")
        self.assertEqual(table_export.safe_cell(None), "")

    def test_content_disposition_has_an_ascii_and_a_utf8_name(self):
        cd = table_export.content_disposition("Médicos 2026-11-07.csv")
        self.assertIn('filename="Mdicos 2026-11-07.csv"', cd)
        self.assertIn("filename*=UTF-8''M%C3%A9dicos%202026-11-07.csv", cd)


ADMIN = {"usuario": "ana", "rol": "admin"}
DOCTOR = {"usuario": "dr", "rol": "medico"}
REPORTS = {"usuario": "rep", "rol": "reportes"}


class TestRouter(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, TURNOS_CONFIG_LOCK="1", TURNOS_EXPORT_MAX_ROWS=None)
        app = FastAPI()
        app.include_router(table_export_router.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.identity = ADMIN
        self.held = list(permissions.BUILTIN_ROLES["admin"]["permissions"])

        async def gate(request, **flags):
            self.flags = flags
            if self.identity is None:
                raise ApiError(401, {"error": "login"})
            return self.identity

        for target, attr, value in ((table_export_router, "gate", gate),
                                    (admin_auth, "active_permissions", lambda: self.held)):
            p = mock.patch.object(target, attr, value)
            p.start()
            self.addCleanup(p.stop)
        self.record = mock.patch.object(access_log, "record_many").start()
        self.addCleanup(mock.patch.stopall)

    def _as(self, identity, role):
        self.identity = identity
        self.held = list(permissions.BUILTIN_ROLES[role]["permissions"])

    def post(self, body):
        return self.client.post("/api/admin/table-export", json=body)

    def test_file_with_human_name_and_access_rows(self):
        r = self.post(_body(title="Pacientes de hoy", day="2026-11-07"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertTrue(r.headers["content-type"].startswith("text/csv"))
        self.assertIn("filename*=UTF-8''Pacientes%20de%20hoy%202026-11-07.csv", r.headers["content-disposition"])
        self.assertTrue(r.content.startswith(BOM))
        lines = r.content[len(BOM):].decode("utf-8").split("\r\n")
        self.assertEqual(lines[0].split(table_export.delimiter()), ["Hora", "Paciente", "DNI"])
        self.assertEqual(len([ln for ln in lines if ln]), 3)
        self.record.assert_called_once_with(ADMIN, "descarga", [("30111222", "/admin/cola"), ("28333444", "/admin/cola")])
        self.assertEqual(self.flags, {"permitir_medico": True, "permitir_admisionista": True, "permitir_reportes": True})

    def test_a_catalog_without_patients_writes_no_access_row(self):
        r = self.post(_body(screen="/admin/services", rows=[["Kinesiología", "", ""]], patients=[]))
        self.assertEqual(r.status_code, 200)
        self.record.assert_not_called()

    def test_the_screen_permission_decides(self):
        self._as(REPORTS, "reportes")
        self.assertEqual(self.post(_body(screen="/admin/reportes", patients=[])).status_code, 200)
        r = self.post(_body(screen="/admin/cola"))
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "rol_no_autorizado")
        self.record.assert_not_called()
        self._as(DOCTOR, "medico")
        self.assertEqual(self.post(_body(screen="/admin/mis-turnos")).status_code, 200)
        self.assertEqual(self.post(_body(screen="/admin/users", patients=[])).status_code, 403)

    def test_no_session_no_file(self):
        self.identity = None
        r = self.post(_body())
        self.assertEqual(r.status_code, 401)
        self.record.assert_not_called()

    def test_limit_and_unknown_screen_answer_with_a_sentence(self):
        with _env.patched_env(TURNOS_EXPORT_MAX_ROWS="1"):
            r = self.post(_body())
        self.assertEqual(r.status_code, 413)
        self.assertEqual(r.json()["code"], "demasiadas_filas")
        self.assertTrue(r.json()["error"])
        r = self.post(_body(screen="/admin/pacientes"))
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["code"], "pantalla_sin_descarga")
        r = self.client.post("/api/admin/table-export", content=b"{not json", headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 400)
        self.record.assert_not_called()

    def test_access_log_down_means_no_file(self):
        self.record.side_effect = access_log.AccessLogUnavailable("down")
        r = self.post(_body())
        self.assertEqual(r.status_code, 503)
        self.assertNotIn(b"Ana", r.content)


if __name__ == "__main__":
    unittest.main()
