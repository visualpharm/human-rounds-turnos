"""intake_admin.py — configuración de la entrevista previa + cartel de puerta.

Rutas:
  GET      /api/admin/intake/settings      toggle sistema + lista de médicos
                                           (override, efectivo, cartel, si
                                           ya tienen token) — solo_admin.
  POST     /api/admin/intake/settings      {"sistema": bool} pisa el default
                                           del sistema (INTAKE_ENABLED), o
                                           {"medico_id", "override": bool|
                                           null} pisa/borra el override de
                                           UN médico (null = heredar) —
                                           solo_admin.
  GET      /api/admin/intake/my-settings   {"override","efectivo",
                                           "cartel_url"} del médico DUEÑO de
                                           la sesión (permitir_medico).
  POST     /api/admin/intake/my-settings   {"override": bool|null} — sólo
                                           escribe el registro del propio
                                           médico (nunca acepta medico_id
                                           del body); permitir_medico.
  GET      /api/admin/intake/door/<medico_id>              (fase 6) cartel
                                           imprimible con UN QR a
                                           /e/<entrevista_qr_token> — crea el
                                           token la primera vez que se pide.
                                           Admin o el propio médico (404 si
                                           es de otro médico, mismo criterio
                                           que consult_admin.py).
  POST     /api/admin/intake/door/<medico_id>/regenerate   (fase 6) token
                                           nuevo — el cartel viejo deja de
                                           resolver (vía de revocación).

La habilitación efectiva vive acá (enabled_for) y la usan todas las
superficies: CTA post-reserva, /e/<token>, columna de la cola, cartel.

El token de puerta vive como campo aditivo del overlay del médico
(`entrevista_qr_token`, catalog.py:_validate_doctor) — no tiene tabla
propia, mismo criterio que `entrevista_habilitada`. `db_catalog.catalog_upsert`
reemplaza el doc ENTERO (no mergea parcial), así que escribirlo siempre
parte del doc YA MERGEADO (`catalog.doctors()`, que siempre es válido)
en vez de mandar un fragmento — ver `_write_door_token`.
"""

import secrets
import io
from pathlib import Path

from app.core import config
from app.core import paths
from app.core.status_error import StatusError
from app.patient_web import catalog
from app.patient_web import site_base
from app.core import tz as _tz


# Optional columns from Pacientes de hoy. Hora, Paciente (with the history
# bars and the referral) and Médico (with the service) are the fixed spine of
# the queue and cannot be hidden; DNI, attendance, practices and diagnosis
# live in the appointment card (the product owner, 2026-11-09). This list is shared with
# the doctor settings tab so the preference saved for a linked medical
# account uses the exact keys admin-queue.mjs reads (its COLS_ALL).
DAY_QUEUE_COLUMNS = (
    ("confirmo", "Confirmó", False),
    ("solicitado", "Solicitado", False),
    ("cobertura", "Cobertura", False),
    ("entrevista", "Síntomas", False),
)
_DAY_QUEUE_COLUMN_KEYS = {key for key, _label, _default in DAY_QUEUE_COLUMNS}


def enabled_for(doctor_doc):
    """Habilitación efectiva de la entrevista previa para un médico:
    override del médico (campo `entrevista_habilitada` del catálogo, si
    está seteado) o el default del sistema (INTAKE_ENABLED, OFF por
    defecto desde 2026-10-21 — la entrevista se ofrece sólo donde alguien
    la encendió: el médico con su override explícito `true`, o el admin
    con el toggle Sistema). Los overrides explícitos `true`/`false` de
    cada médico SIEMPRE ganan sobre el default heredado."""
    system_default = str(config.get("INTAKE_ENABLED", "0")).strip() != "0"
    if not isinstance(doctor_doc, dict):
        return system_default
    override = doctor_doc.get("entrevista_habilitada")
    if override is None:
        return system_default
    return bool(override)


# ---- token del cartel de puerta (fase 6) -----------------------------------

def doctor_by_door_token(token):
    """Médico cuyo `entrevista_qr_token` es `token`, o None. Recorre el
    catálogo entero (son pocos médicos, no hace falta índice) porque la
    identidad viene del token opaco, nunca de algo que el cliente afirme
    directamente — mismo espíritu que `interview_by_patient_token`."""
    token = str(token or "").strip()
    if not token:
        return None
    for m in catalog.doctors():
        if str(m.get("entrevista_qr_token") or "") == token:
            return m
    return None


def _write_door_token(medico_id, doc, token):
    from app.core import db_catalog
    nuevo = dict(doc)
    nuevo["entrevista_qr_token"] = token
    db_catalog.catalog_upsert("medicos", str(medico_id), nuevo, borrado=False)
    catalog.invalidate()
    return nuevo


def get_or_create_door_token(medico_id, *, require_active=False):
    """(token, doc) del médico — crea el token la primera vez que se pide
    el cartel (idempotente: una vez creado, siempre devuelve el mismo).
    (None, None) si el médico no existe en el catálogo."""
    doc = catalog.doctor_doc(medico_id)
    if doc is None:
        return None, None
    if require_active and not enabled_for(doc):
        raise IntakeAdminError(404, {"error": "no encontrado"})
    token = doc.get("entrevista_qr_token")
    if token:
        return token, doc
    token = secrets.token_urlsafe(12)
    doc = _write_door_token(medico_id, doc, token)
    return token, doc


def _write_override(medico_id, doc, override):
    """Mismo patrón que `_write_door_token`: parte del doc YA MERGEADO,
    pisa/borra UNA clave, `db_catalog.catalog_upsert` con el doc entero.
    `override=None` borra la clave (heredar el default del sistema);
    True/False la escribe tal cual (fase 7)."""
    from app.core import db_catalog
    nuevo = dict(doc)
    if override is None:
        nuevo.pop("entrevista_habilitada", None)
    else:
        nuevo["entrevista_habilitada"] = bool(override)
    db_catalog.catalog_upsert("medicos", str(medico_id), nuevo, borrado=False)
    catalog.invalidate()
    return nuevo


def regenerate_door_token(medico_id):
    """Token nuevo — el viejo deja de resolver en `doctor_by_door_token`.
    ES la vía de revocación: quien tenga el cartel impreso viejo pierde el
    acceso apenas se regenera. None si el médico no existe."""
    doc = catalog.doctor_doc(medico_id)
    if doc is None:
        return None
    token = secrets.token_urlsafe(12)
    _write_door_token(medico_id, doc, token)
    return token


# ---- gate: admin o el propio médico ----------------------------------------

class IntakeAdminError(StatusError):
    """A typed 4xx/5xx raised by the domain layer for the native router."""


def guard_medico_or_404(identity, medico_id):
    """An already-authenticated `identity` may view or regenerate
    `medico_id`'s door poster. Admin/operador pass through untouched; a
    'medico' role only its own — raises
    `IntakeAdminError(404)` (not 403) on the ajeno, same criterion as
    `consult_admin`'s own scoping (we never confirm that another médico's id
    exists)."""
    if identity.get("rol") == "medico":
        from app.core import db_operators
        propio = db_operators.operator_doctor_id(identity["usuario"])
        if not propio or str(propio) != str(medico_id):
            raise IntakeAdminError(404, {"error": "no encontrado"})

# ---- cartel imprimible ------------------------------------------------------

def _qr_svg(url):
    """SVG inline del QR (segno, puro Python, sin dependencias nativas) o
    None si segno no está instalado — `_poster_html` cae a la URL en letra
    grande en ese caso, nunca deja el cartel sin forma de entrar."""
    try:
        import segno
    except ImportError:
        return None
    import io
    qr = segno.make(url, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="svg", xmldecl=False, svgns=True, scale=10,
            dark="#0b2231", light=None)
    return buf.getvalue().decode("utf-8")


def door_poster_pdf(doc, url):
    """A high resolution, single-page A4 poster with the same patient copy
    and real QR target as the HTML poster.  Pillow writes a genuine PDF so the
    endpoint can safely advertise ``application/pdf`` without a browser print
    round-trip.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        import segno
    except ImportError:
        raise IntakeAdminError(503, {"error": "pdf no disponible"})

    # 150 dpi is sharp enough for a door QR while keeping downloads small.
    width, height = 1240, 1754
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    # Portable TTF copies of this application's existing Ubuntu webfont
    # subsets. Pillow's bundled fallback lacks Spanish accented glyphs.
    fonts = Path(paths.PROJECT_ROOT) / "static" / "fonts"
    regular = ImageFont.truetype(str(fonts / "ubuntu-400-latin.ttf"), 31)
    bold = ImageFont.truetype(str(fonts / "ubuntu-700-latin.ttf"), 42)
    title = ImageFont.truetype(str(fonts / "ubuntu-700-latin.ttf"), 52)

    def center(text, y, font, fill="#0b2231"):
        box = draw.textbbox((0, 0), text, font=font)
        draw.text(((width - (box[2] - box[0])) / 2, y), text, font=font, fill=fill)

    def wrapped(text, y, font, max_width, fill="#0b2231", line_height=None):
        """Center word-wrapped text, splitting an unbroken URL if needed."""
        words = str(text).split()
        lines, current = [], ""
        for word in words:
            candidate = (current + " " + word).strip()
            if current and draw.textlength(candidate, font=font) > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        expanded = []
        for line in lines:
            while draw.textlength(line, font=font) > max_width:
                cut = max(1, len(line) - 1)
                while cut > 1 and draw.textlength(line[:cut], font=font) > max_width:
                    cut -= 1
                expanded.append(line[:cut])
                line = line[cut:]
            expanded.append(line)
        step = line_height or int(font.size * 1.3)
        for index, line in enumerate(expanded):
            center(line, y + index * step, font, fill)
        return y + len(expanded) * step

    nombre = str(doc.get("nombre") or "")
    detail = "  ·  ".join(x for x in [str(doc.get("especialidad") or ""),
                              ("Mat. " + str(catalog.license_number(doc)) if catalog.license_number(doc) else "")] if x)
    y = wrapped(nombre, 115, bold, 940)
    if detail:
        y = wrapped(detail, y + 8, regular, 940, "#55606c")
    y = wrapped("Mientras esperás, contanos qué te pasa", max(300, y + 70), title, 980)
    copy = ["Escaneá el código con la cámara del celular y respondé unas preguntas.",
            "El médico las lee durante la consulta: esto no la reemplaza ni da consejos médicos."]
    for line in copy:
        y = wrapped(line, y + 12, regular, 960, "#33404c")
    qr = segno.make(url, error="m")
    qr_buffer = io.BytesIO()
    scale = max(1, min(12, 690 // qr.symbol_size()[0]))
    qr.save(qr_buffer, kind="png", scale=scale, dark="#0b2231", light="#ffffff")
    qr_image = Image.open(io.BytesIO(qr_buffer.getvalue())).convert("RGB")
    # Never resize QR modules: interpolation makes some scanner cameras fail.
    qr_y = max(540, y + 45)
    image.paste(qr_image, ((width - qr_image.width) // 2, qr_y))
    wrapped(url, qr_y + qr_image.height + 35, regular, 1040, "#55606c")
    output = io.BytesIO()
    image.save(output, format="PDF", resolution=150.0)
    return output.getvalue()


def _poster_css():
    return """
*{box-sizing:border-box;}
html,body{margin:0;padding:0;background:#eef1f4;}
body{font-family:Arial,Helvetica,sans-serif;color:#0b2231;}
.print-bar{position:sticky;top:0;background:#0b2231;color:#fff;padding:10px 16px;
  display:flex;justify-content:flex-end;gap:10px;z-index:9;}
.print-bar button{font:600 13px Arial,sans-serif;padding:8px 16px;border:0;border-radius:6px;
  background:#fff;color:#0b2231;cursor:pointer;}
.hoja-wrap{display:flex;justify-content:center;padding:18px 0 40px;}
.hoja{width:210mm;min-height:297mm;background:#fff;box-shadow:0 4px 24px rgba(0,0,0,.15);
  padding:20mm;display:flex;flex-direction:column;align-items:center;text-align:center;}
.cartel-doc{font:700 26px/1.25 Arial;margin:0;}
.cartel-esp{font:400 15px Arial;color:#55606c;margin:2px 0 0;}
.cartel-titulo{font:800 30px/1.25 Arial;margin:16mm 0 10mm;max-width:150mm;}
.cartel-copete{font:400 16px/1.5 Arial;max-width:140mm;margin:0 0 10mm;}
.cartel-qr{margin:0;}
.cartel-qr svg{width:100mm;height:100mm;}
.cartel-qr-fallback{width:100mm;padding:10mm;border:2px dashed #c2ccd6;border-radius:12px;
  font:700 15px Arial;color:#55606c;}
.cartel-url{font:700 14px/1.4 monospace;color:#55606c;margin:4mm 0 0;word-break:break-all;}
@media print{
  .print-bar{display:none;}
  body{background:#fff;}
  .hoja-wrap{padding:0;}
  .hoja{box-shadow:none;}
}
"""


def _print_bar():
    return '<div class="print-bar"><button type="button" onclick="window.print()">Imprimir</button></div>'


def _poster_html(doc, url):
    nombre = doc.get("nombre") or ""
    especialidad = doc.get("especialidad") or ""
    mat = catalog.license_number(doc)
    detalle = "  ·  ".join(x for x in [especialidad, (f"Mat. {mat}" if mat else "")] if x)

    svg = _qr_svg(url)
    if svg:
        qr_html = f'<div class="cartel-qr">{svg}</div><p class="cartel-url">{site_base._esc(url)}</p>'
    else:
        qr_html = (f'<div class="cartel-qr"><div class="cartel-qr-fallback">'
                   f'Instalá segno para el QR — mientras tanto, entrá con la '
                   f'dirección de abajo.</div></div><p class="cartel-url">{site_base._esc(url)}</p>')

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cartel de entrevista previa — {site_base._esc(nombre)}</title>
<style>
@page {{ size: A4 portrait; margin: 0; }}
{_poster_css()}
</style>
</head>
<body>
{_print_bar()}
<div class="hoja-wrap"><div class="hoja">
  <p class="cartel-doc">{site_base._esc(nombre)}</p>
  <p class="cartel-esp">{site_base._esc(detalle)}</p>
  <p class="cartel-titulo">Mientras esperás, contanos qué te pasa</p>
  <p class="cartel-copete">Escaneá el código con la cámara del celular y
  respondé unas preguntas — el médico las lee durante la consulta: esto no
  la reemplaza ni da consejos médicos.</p>
  {qr_html}
</div></div>
</body>
</html>"""


def regenerate_payload(medico_id):
    """Pure domain body of POST .../door/<id>/regenerate — may raise
    `db.DbUnavailable` (503, caught by the caller) or `IntakeAdminError`
    (404) if the doctor doesn't exist."""
    token = regenerate_door_token(medico_id)
    if not token:
        raise IntakeAdminError(404, {"error": "no encontrado"})
    return {"ok": True, "url": f"/api/admin/intake/door/{medico_id}"}

# ---- settings (fase 7): toggle sistema + tabla de médicos ------------------


def _medico_summary(doc):
    """Fila de la tabla de #entrevistas: id, nombre, especialidad, el
    override tal cual está guardado (True/False/None = heredar), el
    efectivo (lo que `enabled_for` realmente decide), el link al cartel
    (mismo endpoint que ya usa el propio médico) y si ya tiene token
    generado (si no, "Ver cartel" lo crea al vuelo)."""
    medico_id = doc.get("id")
    override = doc.get("entrevista_habilitada")
    if not isinstance(override, bool):
        override = None
    from app.core import db_operators
    from app.core import db_auth
    cuenta = db_operators.operator_by_doctor_id(medico_id)
    columnas = None
    if cuenta:
        guardadas = db_auth.admin_preferences(cuenta["usuario"]).get("day_queue.columns")
        if isinstance(guardadas, list):
            columnas = [k for k in guardadas if k in _DAY_QUEUE_COLUMN_KEYS]
    return {
        "id": medico_id,
        "nombre": doc.get("nombre") or "",
        "especialidad": doc.get("especialidad") or "",
        "override": override,
        "efectivo": enabled_for(doc),
        "cartel_url": f"/api/admin/intake/door/{medico_id}",
        "pdf_url": f"/api/admin/intake/door/{medico_id}?format=pdf" if enabled_for(doc) else None,
        "tiene_token": bool(doc.get("entrevista_qr_token")),
        "cuenta": cuenta.get("usuario") if cuenta else None,
        "columnas": columnas,
    }


def settings_payload():
    """Pure domain body of GET .../settings — may raise `db.DbUnavailable`
    (503, caught by the caller)."""
    medicos = sorted(catalog.doctors(), key=lambda d: d.get("nombre") or "")
    return {
        "enabled_system": str(config.get("INTAKE_ENABLED", "0")).strip() != "0",
        "columnas_disponibles": [
            {"key": key, "label": label, "default": default}
            for key, label, default in DAY_QUEUE_COLUMNS
        ],
        "medicos": [_medico_summary(m) for m in medicos],
    }


def set_system_default(enabled):
    """{"sistema": bool} — default del sistema entero (config.py, gana el
    override del médico si está seteado, ver enabled_for)."""
    config.guardar({"INTAKE_ENABLED": "1" if enabled else "0"})
    return {"ok": True}


def set_columns_preference(medico_id, columnas):
    """The doctor editor can also set the linked account's Pacientes de hoy
    columns. The preference is persisted in admin_preferences, the same
    database-backed store the queue's column gear uses when the doctor edits
    it personally. May raise `IntakeAdminError` (404/409) or
    `db.DbUnavailable`."""
    from app.core import db_operators
    from app.core import db_auth
    if catalog.doctor_doc(medico_id) is None:
        raise IntakeAdminError(404, {"error": "no encontrado"})
    cuenta = db_operators.operator_by_doctor_id(medico_id)
    if not cuenta:
        raise IntakeAdminError(409, {"error": "el médico no tiene una cuenta vinculada"})
    # Preserve UI order while removing duplicates.
    limpias = list(dict.fromkeys(columnas))
    db_auth.admin_preference_save(cuenta["usuario"], "day_queue.columns", limpias)
    return {"ok": True, "columnas": limpias}


def set_doctor_override(medico_id, override):
    """{"medico_id", "override": bool|null} — override de UN médico. May
    raise `IntakeAdminError` (404) or `db.DbUnavailable`."""
    doc = catalog.doctor_doc(medico_id)
    if doc is None:
        raise IntakeAdminError(404, {"error": "no encontrado"})
    _write_override(medico_id, doc, override)
    return {"ok": True}


def set_doctors_effective(medico_ids, enabled):
    """Set a visible selection to one effective state in one atomic write.

    The UI deliberately sends only visible ids.  It never treats a filter as
    permission to alter hidden doctors, and it never changes the system
    default or migrates unrelated inherited rows.
    """
    if not isinstance(medico_ids, list) or not medico_ids:
        raise IntakeAdminError(400, {"error": "medico_ids inválidos"})
    if not isinstance(enabled, bool):
        raise IntakeAdminError(400, {"error": "enabled debe ser booleano"})
    ids = [str(item).strip() for item in medico_ids]
    if any(not item for item in ids) or len(set(ids)) != len(ids):
        raise IntakeAdminError(400, {"error": "medico_ids inválidos"})
    doctors = {str(doc.get("id")): doc for doc in (catalog.doctor_doc(mid) for mid in ids) if doc is not None}
    # A doctor outside the intake catalog (incomplete: no center) is skipped and counted,
    # like the other bulk actions' «con datos pendientes» — it used to fail the whole
    # batch with 404 and save nobody (form audit, decided 2026-10-25). Only a selection
    # with no catalog doctor at all is still a 404.
    found = [medico_id for medico_id in ids if medico_id in doctors]
    if not found:
        raise IntakeAdminError(404, {"error": "no encontrado"})
    from app.core import db_catalog
    try:
        db_catalog.catalog_set_boolean_many(
            "medicos", [(medico_id, doctors[medico_id]) for medico_id in found],
            "entrevista_habilitada", enabled)
    except LookupError:
        raise IntakeAdminError(409, {"error": "El catálogo cambió. Volvé a cargarlo antes de guardar."})
    catalog.invalidate()
    return {"ok": True, "medico_ids": found, "enabled": enabled, "errores": len(ids) - len(found)}


def settings_post_payload(body):
    """Pure port of `_settings_post`'s whole body/branch decision — takes the
    ALREADY-PARSED JSON body, returns the payload dict, or raises
    `IntakeAdminError`/`db.DbUnavailable`. One source of truth for the
    validation order the native router uses."""
    if "sistema" in body:
        return set_system_default(bool(body.get("sistema")))

    if "medico_ids" in body:
        return set_doctors_effective(body.get("medico_ids"), body.get("enabled"))

    medico_id = str(body.get("medico_id") or "").strip()
    if not medico_id:
        raise IntakeAdminError(400, {"error": "falta sistema o medico_id"})

    if "columnas" in body:
        columnas = body.get("columnas")
        if not isinstance(columnas, list) or any(k not in _DAY_QUEUE_COLUMN_KEYS for k in columnas):
            raise IntakeAdminError(400, {"error": "columnas inválidas"})
        return set_columns_preference(medico_id, columnas)

    override = body.get("override")
    if override is not None and not isinstance(override, bool):
        raise IntakeAdminError(400, {"error": "override debe ser true, false o null"})
    return set_doctor_override(medico_id, override)


def my_settings_payload(identity):
    """Pure domain body of GET .../my-settings — may raise
    `IntakeAdminError` (404) or `db.DbUnavailable`."""
    from app.core import db_operators
    medico_id = db_operators.operator_doctor_id(identity["usuario"])
    doc = catalog.doctor_doc(medico_id) if medico_id else None
    if doc is None:
        raise IntakeAdminError(404, {"error": "no encontrado"})
    override = doc.get("entrevista_habilitada")
    if not isinstance(override, bool):
        override = None
    return {
        "override": override,
        "efectivo": enabled_for(doc),
        "cartel_url": f"/api/admin/intake/door/{medico_id}",
        "pdf_url": f"/api/admin/intake/door/{medico_id}?format=pdf" if enabled_for(doc) else None,
    }


def my_settings_post_payload(identity, body):
    """Pure domain body of POST .../my-settings — takes the ALREADY-PARSED
    JSON body; SIEMPRE el médico de la sesión, el body nunca puede nombrar
    otro (a diferencia de /settings, que sí acepta medico_id porque ahí
    quien pide ya es solo_admin). May raise `IntakeAdminError` (400/404) or
    `db.DbUnavailable`."""
    override = (body or {}).get("override")
    if override is not None and not isinstance(override, bool):
        raise IntakeAdminError(400, {"error": "override debe ser true, false o null"})
    from app.core import db_operators
    medico_id = db_operators.operator_doctor_id(identity["usuario"])
    doc = catalog.doctor_doc(medico_id) if medico_id else None
    if doc is None:
        raise IntakeAdminError(404, {"error": "no encontrado"})
    _write_override(medico_id, doc, override)
    return {"ok": True}


# ---- red-triage alert bell (2026-08-01) ------------------------------------
#
# GET  /api/admin/intake/alerts/count   {"count"} — the cheap poll (~60s),
#                                        no row data at all.
# GET  /api/admin/intake/alerts         the full list, fetched only when the
#                                        bell is opened.
# POST /api/admin/intake/alerts/<id>/ack  marks ONE alert seen.
#
# Scope: rol medico -> only their own doctor_id (404 to ack a foreign one,
# same "if you can't see it, it doesn't exist for you" convention as the
# rest of this module); admin/operador -> every doctor. `_alert_doctor_ids`
# returns None for "no filter" (admin/operador) or a concrete (possibly
# empty) list for a scoped medico — db_clinical.red_alerts/red_alert_count treat
# those two shapes differently on purpose (see their docstrings).

def _alert_doctor_ids(identity):
    """(ids_or_None, error_response_ya_enviada). ids_or_None es lo que
    db_clinical.red_alerts/red_alert_count esperan: None = sin filtro (admin/
    operador), lista = sólo esos médicos (siempre un único id). Desde K10
    (2026-10-23) acota a todo rol que no sea admin — antes sólo a «medico», y
    un rol propio con «atención médica» veía las alertas de todos."""
    from app.core import access_log
    if not access_log.scoped(identity):
        return None, False
    medico_id = access_log.doctor_id(identity)
    return ([medico_id] if medico_id else []), False


def alerts_count_payload(identity):
    """Pure domain body of GET .../alerts/count — may raise
    `db.DbUnavailable`."""
    from app.core import db_clinical
    ids, _ = _alert_doctor_ids(identity)
    return {"count": db_clinical.red_alert_count(ids)}


def _alert_public(iv):
    """Una alerta lista para el cable: quién es, qué servicio, cuándo, con
    qué médico (si el que mira es admin/operador y ve varios) — best-effort
    en cada dato ajeno (booking_requests/usuarios/catálogo pueden no tener
    lo que se busca; una alerta nunca desaparece por eso)."""
    from app.core import db_booking
    from app.core import db_accounts
    nombre = None
    contacto = None
    dni = str(iv.get("dni") or "").strip() or None
    servicio = None
    request_id = iv.get("request_id")
    if request_id:
        try:
            row = db_booking.get_booking_request(int(request_id))
        except Exception:  # noqa: BLE001
            row = None
        if row:
            nombre = " ".join(x for x in (row.get("nombre"), row.get("apellido")) if x).strip() or None
            contacto = row.get("contacto")
            dni = dni or (str(row.get("dni")) if row.get("dni") else None)
            servicio = _service_name(row.get("servicio_id"))
    if not nombre:
        nombre = " ".join(x for x in (iv.get("entered_name"), iv.get("entered_surname")) if x).strip() or None
    if not contacto and dni:
        try:
            u = db_accounts.get_user(dni)
        except Exception:  # noqa: BLE001
            u = None
        if u:
            contacto = u.get("contacto")
            nombre = nombre or " ".join(x for x in (u.get("nombre"), u.get("apellido")) if x).strip() or None

    doctor_id = iv.get("doctor_id")
    doc = catalog.doctor_doc(doctor_id) if doctor_id else None
    return {
        "id": iv.get("id"),
        "triage": iv.get("triage"),
        "paciente": nombre or (f"DNI {dni}" if dni else "Sin nombre"),
        "dni": dni,
        "contacto": contacto,
        "servicio": servicio,
        "medico_id": doctor_id,
        "medico": (doc or {}).get("nombre") if doc else None,
        "fecha": _tz.iso(iv.get("created")),
        "request_id": request_id,
    }


def _service_name(sid):
    if not sid:
        return None
    try:
        # Deleted services included, and never the bare id (catalog.py).
        return catalog.service_name(sid) or sid
    except Exception:  # noqa: BLE001
        return sid


def alerts_list_payload(identity):
    """Pure domain body of GET .../alerts — may raise `db.DbUnavailable`."""
    from app.core import access_log
    from app.core import db_clinical
    ids, _ = _alert_doctor_ids(identity)
    filas = db_clinical.red_alerts(ids)
    items = [_alert_public(iv) for iv in filas]
    # Cada paciente que la lista mostró queda en el registro de accesos (K10).
    access_log.record_many(identity, "alertas", [(i["dni"], i["id"]) for i in items])
    return {"items": items}


def alert_ack_scope_check(identity, iid):
    """Pure port of `_alert_ack`'s medico-scope check — 404, not 403: same
    convention as the rest of the doctor-scoped endpoints in this module, we
    don't confirm a foreign alert exists. Deliberately NOT `db.DbUnavailable`-
    guarded here, matching `_alert_ack`'s original: its own
    `db_operators.operator_doctor_id`/`db_clinical.interview_get` calls sat OUTSIDE `_db_guard`'s
    `_run()`, so a DB outage on this specific check 500s — a pre-existing
    quirk this migration preserves rather than "fixes"."""
    from app.core import access_log
    from app.core import db_clinical
    if not access_log.scoped(identity):
        return
    propio = access_log.doctor_id(identity)
    iv = db_clinical.interview_get(iid)
    if not iv or not propio or str(iv.get("doctor_id") or "") != str(propio):
        raise IntakeAdminError(404, {"error": "no encontrado"})


def alert_ack_payload(identity, iid):
    """The `_db_guard`-wrapped body — may raise `IntakeAdminError` (404) or
    `db.DbUnavailable` (503, caught by the caller)."""
    from app.core import db_clinical
    row = db_clinical.interview_mark_alert_seen(iid, identity["usuario"])
    if not row:
        raise IntakeAdminError(404, {"error": "no encontrado"})
    return {"ok": True}
