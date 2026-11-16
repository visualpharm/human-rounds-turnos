"""table_export.py — the panel's one «Descargar» for small tables (2026-11-07).

The product owner, 2026-11-07: an export for every SMALL part of the panel — the reports,
today's patients, a doctor's day, the catalog lists —
and never for the big places (all of Pacientes, the whole log).

The browser already holds exactly what the screen shows: the columns in their
order, the rows of the current filter and date. The shared table engine
(`static/platform/admin-table.mjs`, `exportTable`) reads that visible table and
posts it here; this module is the gate, not a second query:

* **only listed screens** export (`SCREENS`): a big list has no entry, so it
  has no button and the server refuses it too;
* **the screen's own permission** decides (`permissions.required_permission`
  on the screen path) — a role that cannot open the screen cannot download it;
* **a row limit** (`TURNOS_EXPORT_MAX_ROWS`, default 1000) refuses with a
  sentence that says what to do;
* **every patient in the file is recorded** in `clinical_access_log`
  (`descarga`, target = the screen path), before the file is answered: a
  file whose access rows cannot be written is not given.

The server reads no patient data here: it echoes what the session's own
screen already received, so it cannot widen what the session sees.

CSV for Excel: UTF-8 with BOM, CRLF, `;` where the decimal mark is a comma
(es, pt, fr, it, de, ru, uk) and `,` elsewhere; a cell that would start a
spreadsheet formula gets a leading apostrophe (numbers, phones and times stay
untouched). The file is named after the screen and its day:
«Pacientes de hoy 2026-11-07.csv».
"""

import csv
import io
import re
import urllib.parse
from datetime import datetime

from app.core import config, i18n, tz
from app.core import permissions

ACCESS_ACTION = "descarga"
DEFAULT_MAX_ROWS = 1000
MAX_COLUMNS = 60
MAX_CELL = 4000
MAX_TITLE = 80

# path -> (title key, Spanish title): the screens with a small table. Not
# here, on purpose: Pacientes and the logs (big lists), Solicitudes (100 rows
# a page behind «Cargar más», folded groups — the file would not be what the
# screen shows), Roles and Preparaciones (two columns, nothing to sum).
# A row that names a patient carries the patient's DNI (`patients`), on any
# screen — the doctor's day inside Médicos too — and every one is recorded in
# the access log as `descarga`.
SCREENS = {
    "/admin/cola": ("admin.shell.export.screen.cola", "Pacientes de hoy"),
    "/admin/mis-turnos": ("admin.shell.export.screen.mis_turnos", "Mis turnos"),
    "/admin/my-patients": ("admin.shell.export.screen.my_patients", "Mis pacientes"),
    "/admin/reportes": ("admin.shell.export.screen.reportes", "Reportes"),
    "/admin/doctors": ("admin.shell.export.screen.doctors", "Médicos"),
    "/admin/services": ("admin.shell.export.screen.services", "Servicios"),
    "/admin/centers": ("admin.shell.export.screen.centers", "Centros de salud"),
    "/admin/users": ("admin.shell.export.screen.users", "Usuarios"),
}

# Languages whose spreadsheets read «1,5» as a number: Excel there splits a
# CSV on `;`. English, Japanese and Chinese split on `,`.
_SEMICOLON_LANGS = frozenset({"es", "pt", "fr", "it", "de", "ru", "uk"})
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
# Digits, signs, spaces and the separators of dates, times, phones and money:
# nothing a spreadsheet can turn into a function call or a reference.
_PLAIN_NUMBER = re.compile(r"[+\-]?[\d\s.,:/()$%-]*")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_FILENAME_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')


class ExportError(Exception):
    """A refusal with its status and the sentence the screen shows."""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def body(self):
        return {"error": self.message, "code": self.code}


def max_rows():
    n = config.get_int("TURNOS_EXPORT_MAX_ROWS", DEFAULT_MAX_ROWS)
    return n if n > 0 else DEFAULT_MAX_ROWS


def screen_of(path):
    """The SCREENS key for a panel path (query and trailing slash dropped), or None."""
    clean = str(path or "").split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return clean if clean in SCREENS else None


def allowed(screen, role, held):
    """Does a session with these effective permissions open this screen?"""
    required = permissions.required_permission("GET", screen, screen, role=role)
    return permissions.holds(required, held)


def delimiter(lang=None):
    return ";" if (lang or i18n.active() or "es") in _SEMICOLON_LANGS else ","


def safe_cell(value):
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")[:MAX_CELL]
    if text.startswith(_FORMULA_START) and not _PLAIN_NUMBER.fullmatch(text):
        return "'" + text
    return text


def _lang():
    return i18n.active() or "es"


def _t(key, fallback, **kw):
    return i18n.t_or(key, fallback, _lang(), **kw)


def invalid():
    return ExportError(400, "datos_invalidos", _t(
        "admin.shell.export.error.invalid", "No se pudo armar el archivo. Recargá la página y volvé a descargar."))


def _text_list(value, limit):
    if not isinstance(value, list) or len(value) > limit:
        raise invalid()
    return ["" if v is None else str(v) for v in value]


def parse(body):
    """The validated export request: {screen, title, day, columns, rows, patients}.

    Raises ExportError for an unknown screen, malformed data or too many rows."""
    if not isinstance(body, dict):
        body = {}
    screen = screen_of(body.get("screen"))
    if screen is None:
        raise ExportError(404, "pantalla_sin_descarga", _t(
            "admin.shell.export.error.screen", "Esta pantalla no se descarga: es una lista grande. Filtrala y descargá la parte que necesitás."))
    columns = _text_list(body.get("columns"), MAX_COLUMNS)
    raw_rows = body.get("rows")
    if not columns or not isinstance(raw_rows, list):
        raise invalid()
    limit = max_rows()
    if len(raw_rows) > limit:
        raise ExportError(413, "demasiadas_filas", _t(
            "admin.shell.export.error.too_many",
            "Son {n} filas y se descargan hasta {max}. Elegí un día, un médico o un filtro más chico y volvé a descargar.",
            n=len(raw_rows), max=limit))
    rows = []
    for row in raw_rows:
        cells = _text_list(row, MAX_COLUMNS)
        cells = (cells + [""] * len(columns))[:len(columns)]
        rows.append(cells)
    patients = body.get("patients") or []
    if not isinstance(patients, list):
        patients = []
    patients = [str(p or "") for p in patients[:len(rows)]]
    day = str(body.get("day") or "")
    if not _DATE.fullmatch(day):
        day = datetime.now(tz.tzinfo()).date().isoformat()
    key, fallback = SCREENS[screen]
    title = clean_title(body.get("title")) or _t(key, fallback)
    return {"screen": screen, "title": title, "day": day, "columns": columns,
            "rows": rows, "patients": patients}


def clean_title(value):
    text = _FILENAME_BAD.sub(" ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip(" .")
    return text[:MAX_TITLE].strip()


def access_items(req):
    """[(dni, screen)] for the access log: one per patient named in the file."""
    seen = {}
    for dni in req["patients"]:
        digits = re.sub(r"\D", "", dni)
        if digits:
            seen.setdefault(digits, None)
    return [(d, req["screen"]) for d in seen]


def csv_bytes(columns, rows, lang=None):
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter(lang), lineterminator="\r\n",
                        quoting=csv.QUOTE_MINIMAL)
    writer.writerow([safe_cell(c) for c in columns])
    for row in rows:
        writer.writerow([safe_cell(c) for c in row])
    return buf.getvalue().encode("utf-8-sig")


def filename(title, day):
    return "%s %s.csv" % (title, day)


def content_disposition(name):
    ascii_name = name.encode("ascii", "ignore").decode("ascii").replace('"', "") or "export.csv"
    return "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (
        ascii_name, urllib.parse.quote(name, safe=""))
