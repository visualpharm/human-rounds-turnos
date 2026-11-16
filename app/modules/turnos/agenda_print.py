"""agenda_print.py — one doctor's day on paper: «Turnos programados».

The hospital prints the day of each professional from HSI and hands it to the
desk and the doctor (the reference-deployment counterpart, after the Reportes meeting of
2026-11-07). The panel prints the same sheet: a header (professional, the day
asked for, when it was printed), then one block per window of the doctor's
agenda that day («Turnos programados (17:00 a 19:20)»), rows by time, the
columns HSI's sheet has — time, HSI patient id, document, first names, last
names, coverage, credential number, phone, appointment state. A field we do
not know is an empty cell, never a dash or a zero.

Who may print what is the day queue's rule (`/admin/cola`, permission `cola`):
the desk prints any doctor of its centers; a session held to its own doctor
(`access_log.scoped(identity, clinical=False)` — the medico role) prints only
that doctor, and asking for another one is refused (404) with a denied row in
the access log. Every patient on the sheet leaves its row (`record_many`,
action `agenda_impresa`) before the sheet is built.
"""

from datetime import date, datetime

from app.core import access_log
from app.core import db_slots
from app.core import format as display_format
from app.core import i18n
from app.core import tz as _tz
from app.auth import admin_auth
from app.cuentas import health_coverage
from app.modules.turnos import doctor_appointments as _da

ACTION = "agenda_impresa"


class PrintError(Exception):
    """A page without a sheet and the status the route answers it with (200 no
    doctor chosen yet, 400 account not linked to a doctor, 404 unknown or
    foreign doctor)."""

    def __init__(self, status, code):
        super().__init__(code)
        self.status = status
        self.code = code


def _t(key, fallback, **variables):
    return i18n.t_or(key, fallback, i18n.active(), **variables)


def parse_day(raw):
    """The ISO day of `?dia=`, today (installation time) when absent; None when it does not parse."""
    raw = str(raw or "").strip()
    if not raw:
        return _da._today_art_iso()
    try:
        return date.fromisoformat(raw[:10]).isoformat() if len(raw) == 10 else None
    except ValueError:
        return None


# ---- the doctor's windows that day ----------------------------------------------

def day_windows(medico, dia):
    """[{"desde", "hasta", "centro"}] the doctor's agenda opens on `dia`, in
    order — read from the one source of a doctor's windows (`doctor_windows`:
    the services that manage the doctor, else each center's windows, else the
    legacy flat `agenda`; a closed day drops its source, an `extra` exception
    adds a window, the slot generator's day resolution)."""
    from app.modules.turnos import doctor_windows
    fuentes = doctor_windows.sources(medico or {})[0]
    seen = {(v["desde"], v["hasta"], v["centro"] or "")
            for v in doctor_windows.windows_of_day(fuentes, dia)}
    return [{"desde": a, "hasta": b, "centro": c or None} for a, b, c in sorted(seen)]


# ---- one row of the sheet ----------------------------------------------------------

def _state(row):
    """The appointment state in the words of HSI's own sheet."""
    if row.get("asistencia") == "presente":
        return _t("admin.queue.print.state.atendido", "Atendido")
    if row.get("asistencia") == "ausente":
        return _t("admin.queue.print.state.ausente", "Ausente")
    if row.get("confirmacion") == "si":
        return _t("admin.queue.print.state.confirmado", "Confirmado")
    if row.get("confirmacion") == "no":
        return _t("admin.queue.print.state.avisa_no_viene", "Avisó que no viene")
    return _t("admin.queue.print.state.asignado", "Asignado")


def _phone(contact):
    """The first contact that is a phone, as the panel writes phones (an
    incomplete one as it was typed — the desk still dials it); '' otherwise."""
    for value in (contact.get("contacto"), contact.get("contacto_cuenta")):
        value = str(value or "").strip()
        if value and "@" not in value:
            return display_format.tel_text(value) if display_format.tel_valido(value) else value
    return ""


def _coverage(row):
    primary = health_coverage.primary(row.get("cobertura_usuario") or {}) or {}
    return str(primary.get("nombre") or "").strip(), str(primary.get("afiliado") or "").strip()


def _document(dni):
    digits = access_log.clean_dni(dni)
    return _t("admin.queue.print.document", "DNI {n}", n=digits) if digits else ""


def print_row(row, cuentas=None):
    """The nine cells of one appointment, every unknown one an empty string."""
    from app.core import patient_contact
    hora = row["turno_dt"].astimezone(_tz.tzinfo()).strftime("%H:%M") if row.get("turno_dt") else ""
    cobertura, credencial = _coverage(row)
    contact = patient_contact.contacto(row.get("dni"), row.get("solicitante_dni"),
                                       row.get("contacto"), cuentas)
    return {
        "id": row.get("id"),
        "dni": row.get("dni"),
        "centro_slug": row.get("centro_slug") or "",
        "hora": hora,
        "hsi_id": str(row.get("hsi_patient_id") or "").strip(),
        "documento": _document(row.get("dni")),
        "nombres": display_format.person_name(row.get("nombre") or "") or "",
        "apellidos": display_format.person_name(row.get("apellido") or "") or "",
        "cobertura": cobertura,
        "credencial": credencial,
        "telefono": _phone(contact),
        "estado": _state(row),
    }


def blocks(windows, rows):
    """Rows grouped under the window that holds their time (the one of their
    own center first); rows outside every window — an overbooking, an agenda
    edited after the booking — go to a last block without hours. Empty
    windows are not printed."""
    out = [{"desde": w["desde"], "hasta": w["hasta"], "centro": w.get("centro"), "filas": []}
           for w in windows]
    fuera = {"desde": None, "hasta": None, "centro": None, "filas": []}
    for r in sorted(rows, key=lambda r: (r["hora"], r["apellidos"], r["nombres"])):
        cands = [b for b in out if r["hora"] and b["desde"] <= r["hora"] < b["hasta"]]
        own = [b for b in cands if b["centro"] and b["centro"] == r.get("centro_slug")]
        target = (own or cands or [fuera])[0]
        target["filas"].append(r)
    result = [b for b in out if b["filas"]]
    if fuera["filas"]:
        result.append(fuera)
    return result


def _center_label(slug):
    return _da._center_name(slug) if slug else None


def _block_titles(bloques):
    """Each block's title; the center joins it only when the day spans more than one."""
    centros = {b["centro"] for b in bloques if b["centro"]}
    for b in bloques:
        if b["desde"]:
            title = _t("admin.queue.print.block", "Turnos programados ({desde} a {hasta})",
                       desde=b["desde"], hasta=b["hasta"])
        else:
            title = _t("admin.queue.print.block_outside", "Turnos fuera de la agenda")
        nombre = _center_label(b["centro"]) if len(centros) > 1 else None
        b["titulo"] = _t("admin.queue.print.block_center", "{block}, {center}",
                         block=title, center=nombre) if nombre else title
    return bloques


# ---- the sheet ---------------------------------------------------------------------

def _absolute_date(dia):
    """'Martes 25 de noviembre de 2026' — always absolute, with the year: a
    printed sheet is read days later, where «Hoy» rots (dates.mjs `larga`)."""
    from app.patient_web import site_schedule
    d = date.fromisoformat(str(dia)[:10])
    lang = i18n.active()
    month = i18n.t_or(f"date.month_full.{d.month}", site_schedule._MONTH_FULL[d.month - 1], lang)
    return i18n.t_or("date.long_year", "{day} {n} de {month} de {year}", lang,
                     day=site_schedule._day_name(d.weekday()), n=d.day, month=month, year=d.year)


def _target_doctor(identity, pedido):
    """The doctor id this session prints. A session held to its own doctor
    gets its own whatever it asks; asking for another one is recorded as a
    denied read and refused as not found."""
    pedido = str(pedido or "").strip()
    if access_log.scoped(identity, clinical=False):
        propio = access_log.doctor_id(identity)
        if not propio:
            raise PrintError(400, "cuenta_sin_medico")
        if pedido and pedido != propio:
            access_log.record(identity, ACTION, None, target_id="medico:" + pedido, allowed=False)
            raise PrintError(404, "medico_desconocido")
        return propio
    if not pedido:
        # Not an error: the desk opened the page without choosing — the page
        # asks for the doctor (a held screen opens, journey J28).
        raise PrintError(200, "falta_medico")
    return pedido


def sheet_data(identity, dia, pedido, now=None):
    """Everything the print page shows for one doctor's day. Raises PrintError."""
    medico_id = _target_doctor(identity, pedido)
    medico = _da.doctor_of(medico_id, incluir_incompletos=True)
    if not medico:
        raise PrintError(404, "medico_desconocido")
    rows = db_slots.list_approved_day(dia)
    centros_ok = admin_auth.centros_permitidos(identity)
    if centros_ok is not None:
        rows = [r for r in rows if (r.get("centro_slug") or "") in centros_ok]
    nombre = medico.get("nombre") or ""
    propios = [r for r in rows if _da._belongs_to(r, medico_id, nombre)]
    # The read is recorded before the sheet exists: a row that cannot be
    # written refuses the print (AccessLogUnavailable, 503).
    access_log.record_many(identity, ACTION, [(r.get("dni"), r.get("id")) for r in propios])
    cuentas = _da._cuentas_de_contacto(propios)
    filas = [print_row(r, cuentas) for r in propios]
    bloques = _block_titles(blocks(day_windows(medico, dia), filas))
    ahora = (now or datetime.now(_tz.tzinfo())).astimezone(_tz.tzinfo())
    return {
        "medico_id": medico_id,
        "profesional": display_format.person_name(nombre) or nombre,
        "dia": dia,
        "fecha_consultada": _absolute_date(dia),
        "impreso": _t("admin.queue.print.printed_at_value", "{date}, {time}",
                      date=_absolute_date(ahora.date().isoformat()), time=ahora.strftime("%H:%M")),
        "bloques": bloques,
        "total": len(filas),
    }
