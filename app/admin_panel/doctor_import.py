"""Bulk doctor import: «Cargar lista» in Médicos, the one path for a roster.

A doctor and its account are ONE entity (2026-10-19): every row goes through
`admin_content.create_item("medicos", ...)`, the same call as the Médicos
editor, so the doctor and its account commit together, the invitation is the
same one («email → sent, none → a link to copy») and nothing here knows how an
account is made. A dry run (the preview) never writes and never sends mail.

The list arrives as a file (CSV, TSV, TXT or Excel .xlsx) or as text pasted
into Médicos, sent as a .txt. A first row naming its columns is a header
(Nombre, Apellido, Email, Especialidad, Centro — or one «Nombre completo»);
without one, the email is the cell with an "@", the specialty and the center
are the cells naming one, and the rest is the name and the surname. Only a
name or an email is required: an incomplete doctor never reaches a public
surface. No DNI is read or stored. Rows are independent: a bad row never stops
the others, and a repeated upload creates nothing twice (2026-11-03: the old
`/admin/users/import` page merged into «Cargar lista»).
"""
import csv
import io
import re
import unicodedata
import zipfile
from itertools import islice

from app.admin_panel import admin_content
from app.admin_panel.native_json import ApiError
from app.auth import staff_portal
from app.core import i18n
from app.core import db_operators
from app.core.format import person_name
from app.patient_web import catalog

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 500
MAX_COLUMNS = 20
MAX_EXPANDED_BYTES = 16 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 1000
TEXT_TYPES = (".csv", ".tsv", ".txt")
FIELDS = ("nombre", "nombre_pila", "apellido", "email", "especialidad", "centro")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Header cell (folded) → field. A «nombre» column is the first name when the
# table also has an «apellido» one, else the whole name.
_ALIASES = {
    "nombre": "nombre_pila", "nombres": "nombre_pila", "name": "nombre_pila", "first name": "nombre_pila",
    "nombre completo": "nombre", "nombre y apellido": "nombre", "full name": "nombre",
    "apellido": "apellido", "apellidos": "apellido", "surname": "apellido", "last name": "apellido",
    "email": "email", "e-mail": "email", "mail": "email", "correo": "email", "correo electronico": "email",
    "especialidad": "especialidad", "specialty": "especialidad",
    "centro": "centro", "center": "centro",
}


class _Semicolon(csv.excel):
    delimiter = ";"


def _t(key, fallback, **kwargs):
    return i18n.t_or("admin.doctor_import." + key, fallback, i18n.active(), **kwargs)


def oversize_message():
    return _t("oversize", "El archivo supera 2 MB.")


def _fail(key, fallback, status=400):
    return ApiError({"error": _t(key, fallback)}, status)


def _key(value):
    folded = unicodedata.normalize("NFKD", str(value or "").strip().casefold())
    return " ".join("".join(c for c in folded if not unicodedata.combining(c)).split())


def _text(value):
    return str(value if value is not None else "").strip()


def _matrix(raw, filename):
    name = filename.lower()
    if name.endswith(TEXT_TYPES):
        text = raw.decode("utf-8-sig")
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel_tab if "\t" in text else _Semicolon if ";" in text else csv.excel
        return list(islice(csv.reader(io.StringIO(text), dialect), MAX_ROWS + 2))
    if name.endswith(".xlsx"):
        # Bound the decompression before openpyxl reads shared strings and styles.
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES or sum(x.file_size for x in entries) > MAX_EXPANDED_BYTES:
                raise _fail("xlsx_too_big", "El Excel descomprimido es demasiado grande.", 413)
        from openpyxl import load_workbook
        book = load_workbook(io.BytesIO(raw), read_only=True, data_only=False, keep_links=False)
        try:
            sheet = book.active
            if sheet.max_column and sheet.max_column > MAX_COLUMNS:
                raise _fail("too_many_columns", "La tabla tiene demasiadas columnas.")
            return list(islice(sheet.iter_rows(values_only=True, max_col=MAX_COLUMNS), MAX_ROWS + 2))
        finally:
            book.close()
    raise _fail("bad_type", "Elegí un archivo CSV o Excel (.xlsx).")


def _header(cells):
    """[field or None] per column when the row names its columns, else None."""
    fields = [_ALIASES.get(_key(c)) for c in cells]
    if not any(fields):
        return None
    if "apellido" not in fields:
        fields = ["nombre" if f == "nombre_pila" else f for f in fields]
    if any(fields.count(f) > 1 for f in set(fields) if f):
        raise _fail("columns_repeated", "Hay una columna repetida.")
    if not {"nombre", "nombre_pila", "apellido", "email"} & set(fields):
        raise _fail("columns_missing", "Falta la columna del nombre o del email.")
    return fields


def _guess(cells, specialties, centers):
    """A headerless row: "@" → email, a known specialty or center, the rest name and surname."""
    row, rest = {}, []
    for cell in cells:
        if "@" in cell and not row.get("email"):
            row["email"] = cell
        elif not row.get("especialidad") and any(_key(s.get("nombre")) == _key(cell) for s in specialties):
            row["especialidad"] = cell
        elif not row.get("centro") and _center(cell, centers):
            row["centro"] = cell
        else:
            rest.append(cell)
    if len(rest) >= 2:
        row["nombre_pila"], row["apellido"] = rest[0], " ".join(rest[1:])
    elif rest:
        row["nombre"] = rest[0]
    return row


def parse_file(raw, filename, specialties=(), centers=()):
    """[(line number, {nombre, nombre_pila, apellido, email, especialidad, centro})] from the first sheet."""
    if len(raw) > MAX_BYTES:
        raise _fail("oversize", "El archivo supera 2 MB.", 413)
    try:
        matrix = _matrix(raw, filename)
    except ApiError:
        raise
    except Exception:
        raise _fail("unreadable", "No se pudo leer la tabla. Usá CSV UTF-8 o Excel (.xlsx).") from None
    matrix = [[_text(v) for v in values] for values in matrix]
    filled = [(n, cells) for n, cells in enumerate(matrix, 1) if any(cells)]
    if not filled:
        raise _fail("empty", "El archivo está vacío.")
    fields = _header(filled[0][1])
    if fields is not None:
        filled = filled[1:]
    if len(filled) > MAX_ROWS:
        raise _fail("too_many_rows", "La lista supera 500 filas.", 413)
    rows = []
    for number, cells in filled:
        if fields is None:
            found = _guess([c for c in cells if c], specialties, centers)
        else:
            found = {f: cells[i] for i, f in enumerate(fields) if f and i < len(cells) and cells[i]}
        rows.append((number, {f: found.get(f, "") for f in FIELDS}))
    if not rows:
        raise _fail("no_rows", "La tabla no tiene filas.")
    return rows


def _center(value, centers):
    found = [c for c in centers if _key(value) in {_key(c.get("slug")), _key(c.get("name"))}]
    return found[0] if len(found) == 1 else None


def _specialty(value):
    """The catalog's own spelling of a specialty typed in any case or accent."""
    for known in catalog.specialties():
        if _key(known.get("nombre")) == _key(value):
            return known["nombre"]
    return None


def _full_name(row):
    return row["nombre"] or " ".join(x for x in (row["nombre_pila"], row["apellido"]) if x)


def _plan(row, doctors, centers):
    """('crear', body) or ('existente', doctor); ValueError(message) for a bad row."""
    if not _full_name(row) and not row["email"]:
        raise ValueError(_t("row_incomplete", "Falta el nombre o el email."))
    if any(len(v) > 254 or v.startswith(("=", "+", "@")) for v in row.values()):
        raise ValueError(_t("row_formula", "Hay un valor demasiado largo o una fórmula."))
    email = row["email"].casefold()
    if email and not _EMAIL.fullmatch(email):
        raise ValueError(_t("row_email", "Email inválido."))
    center = None
    if row["centro"]:
        center = _center(row["centro"], centers)
        if center is None:
            raise ValueError(_t("row_center", "Centro desconocido o repetido."))
    specialty = None
    if row["especialidad"]:
        specialty = _specialty(row["especialidad"])
        if specialty is None:
            raise ValueError(_t("row_specialty", "Especialidad desconocida."))
    first, last = person_name(row["nombre_pila"]), person_name(row["apellido"])
    name = person_name(row["nombre"]) if row["nombre"] else " ".join(x for x in (first, last) if x)
    live = [d for d in doctors if not d.get("_borrado") and not d.get("rol")]
    same_email = [d for d in live if email and _key(d.get("email")) == _key(email)]
    same_person = [d for d in live if name and _key(d.get("nombre")) == _key(name)
                   and (not specialty or _key(d.get("especialidad")) == _key(specialty))
                   and (not center or center["slug"] in {c.get("centro") for c in d.get("centros") or []}
                        | {d.get("centro")})
                   and (not email or not d.get("email") or _key(d.get("email")) == _key(email))]
    if same_email and not name:
        return "existente", same_email[0]
    if same_email and not any(d in same_person for d in same_email):
        raise ValueError(_t("row_email_taken", "Ese email ya es de otro médico."))
    if same_person:
        return "existente", same_person[0]
    if email and staff_portal.enabled() and db_operators.operator_active_email_taken(email, except_usuario=None):
        raise ValueError(_t("row_email_taken", "Ese email ya es de otro médico."))
    if not email and staff_portal.enabled():
        raise ValueError(_t("row_portal_email", "En esta instalación hace falta el email: es la cuenta del portal."))
    body = {"nombre_pila": first, "apellido": last} if first and last else {"nombre": name}
    if specialty:
        body["especialidad"] = specialty
    if center:
        body["centro"] = center["slug"]
    if email:
        body["email"] = email
    _, error = catalog.validate("medicos", dict(body), is_new=True)
    if error:
        raise ValueError(error)
    return "crear", dict(body, _invitar=True)


def _create(body):
    """The Médicos editor's own create: doctor + account + invitation."""
    try:
        result = admin_content.create_item("medicos", body)
    except admin_content.ContentError as exc:
        raise ValueError(str(exc)) from None
    return result["item"], result.get("cuenta") or {}


def import_file(raw, filename, dry_run=True):
    centers = catalog.centers()
    rows = parse_file(raw, filename, catalog.specialties(), centers)
    report, seen_email, seen_person = [], set(), set()
    for number, row in rows:
        full = _full_name(row)
        out = {"fila": number, "nombre": person_name(full) if full else "", "email": row["email"],
               "especialidad": row["especialidad"], "centro": row["centro"]}
        try:
            person = (_key(full), _key(row["especialidad"]), _key(row["centro"]))
            email = _key(row["email"])
            if (full and person in seen_person) or (email and email in seen_email):
                raise ValueError(_t("row_duplicate", "Está repetido en la lista."))
            if full:
                seen_person.add(person)
            if email:
                seen_email.add(email)
            catalog.invalidate()
            state, found = _plan(row, catalog.admin_list("medicos"), centers)
            if state == "existente":
                out.update(estado="existente", medico_id=found.get("id"),
                           detalle=_t("row_exists", "Ya está entre los médicos; no se modifica."))
            elif dry_run:
                out.update(estado="crear", detalle=_t(
                    "row_will_invite_email" if row["email"] else "row_will_invite_link",
                    "Crear médico y enviar el acceso." if row["email"]
                    else "Crear médico y entregar un enlace de acceso."))
            else:
                doctor, account = _create(found)
                out.update(estado="creado", medico_id=doctor.get("id"), usuario=account.get("usuario"))
                if account.get("email_enviado"):
                    out["detalle"] = _t("row_invited", "Médico creado. Acceso enviado por email.")
                elif account.get("enlace"):
                    out["enlace"] = account["enlace"]
                    out["detalle"] = _t("row_link", "Médico creado. Entregale este enlace de acceso.")
                else:
                    out["detalle"] = _t("row_created", "Médico creado. Invitalo desde su pestaña Usuario.")
        except ValueError as exc:
            out.update(estado="error", detalle=str(exc))
        except Exception:
            # Never expose provider messages, passwords, hashes or reset tokens.
            out.update(estado="error", detalle=_t("row_failed", "No se pudo crear. Revisá Médicos y reintentá."))
        report.append(out)
    count = lambda s: sum(r["estado"] == s for r in report)  # noqa: E731
    return {"dry_run": dry_run, "rows": report, "validas": count("crear"), "creadas": count("creado"),
            "existentes": count("existente"), "errores": count("error")}
