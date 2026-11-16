"""interview_patient.py — domain and rendering logic for the patient intake
routes, mounted by ``interview_patient_router.py`` when the ``intake`` module
is enabled.

Rutas (fase 2, post-reserva):
  GET  /entrevista/<request_id>[?t=<patient_token>]   shell post-reserva.
                                           Dos formas de autenticarse
                                           (2026-08-03 — "el link del mail
                                           tiene que entrar directo al
                                           chat, sin login"):
                                           - `?t=<patient_token>`: el token
                                             opaco ES la autorización de
                                             ESA entrevista puntual, mismo
                                             modelo que appointment_offers.token
                                             y booking_requests.confirm_token
                                             — nunca sesión, nunca chequeo de
                                             dueño. Token ajeno o de otra
                                             solicitud -> 404 (nunca revela
                                             si `request_id` existe). Es el
                                             camino de los links salientes
                                             (mail/WhatsApp,
                                             notifications.py:_entrevista_cta),
                                             y NO crea la fila (ya existe:
                                             la creó _entrevista_patient_token
                                             al armar el link) — un GET con
                                             token nunca muta nada.
                                           - Sin token: sesión (mismo
                                             criterio que GET
                                             /api/requests/<id>); sin sesión
                                             -> 401, solicitud ajena o
                                             inexistente -> 403 (nunca 404:
                                             acá no hace falta el matiz "no
                                             confirmamos que existe" de la
                                             API JSON, la página ya requiere
                                             sesión). Es el camino de
                                             "Mis solicitudes"/el detalle,
                                             visitados dentro de la cuenta.
                                           Cualquiera de los dos caminos:
                                           entrevista deshabilitada para el
                                           médico/sistema -> redirect a
                                           /my-requests (solución, nunca
                                           error) — el camino con token NO
                                           aplica este chequeo (la
                                           entrevista ya existía cuando se
                                           armó el link; revalidar acá sólo
                                           rompería un link ya enviado si el
                                           hospital apaga el toggle después).
  GET  /api/intake/t/<patient_token>       {status, mensajes} para
                                           reconexión — si la entrevista
                                           está activa y todavía no tiene
                                           historial, siembra la primera
                                           pregunta del modelo ANTES de
                                           responder (el paciente nunca ve
                                           una caja vacía).
  POST /api/intake/t/<patient_token>/message   un turno del chat (delegado a
                                           interview_agent.run_turn) ->
                                           {texto, fin, opciones}. Body
                                           opcional {texto, adjunto:
                                           {filename, mime, data_b64}} — una
                                           foto, sólo imagen, tope
                                           _MAX_IMAGEN_BYTES (2026-08-01,
                                           componente de chat compartido con
                                           static/platform/chat-widget.mjs, ver
                                           docs/architecture/chat-y-whatsapp.md).
  POST /api/intake/t/<patient_token>/reopen   "Actualizar entrevista"
                                           (2026-08-01): una entrevista
                                           'done' vuelve a 'active' —
                                           mismo gate que el resto de estas
                                           rutas (el token opaco ES la
                                           autorización, sin chequeo de
                                           sesión adicional). Ya activa ->
                                           200 idempotente (doble click o
                                           carrera, nunca un error). 'done'
                                           -> db_clinical.interview_reopen +
                                           interview_agent.run_turn(...,
                                           reabierta=True) para sembrar la
                                           pregunta de actualización, y
                                           devuelve {status, mensajes} —
                                           mismo shape que el GET de arriba,
                                           así el cliente puede repintar la
                                           charla igual que una reconexión.

Rutas (fase 6, QR de puerta — un médico, un cartel; login primero, DNI de
respaldo — rediseñado 2026-08-01, ver docs/architecture/entrevista-previa.md):
  GET  /e/<door_token>                     resuelve el médico por
                                           `intake_admin.doctor_by_door_token`
                                           (el token vive en el overlay del
                                           médico, nunca en la URL de otro
                                           lado). Con sesión de cuenta
                                           activa (`booking_requests._get_user`)
                                           SALTEA el paso de identidad
                                           entero: matchea por DNI
                                           (`_match_today_dni`) y arma el
                                           shell con `data-token` — derecho
                                           al chat, igual que post-reserva.
                                           Sin sesión, arma el shell con
                                           `data-qr-start` +
                                           `data-account-next` —
                                           entrevista.js pinta "Ingresá a tu
                                           cuenta" (vuelve acá con
                                           ?next=/e/<door_token>) como
                                           acción primaria, y un DNI solo
                                           como respaldo debajo. Token
                                           desconocido o entrevista
                                           deshabilitada -> página amigable
                                           (nunca un error crudo: el
                                           paciente no tiene a quién
                                           reportarle un 404 en la puerta
                                           del consultorio).
  POST /api/intake/qr/<door_token>/start   {dni} (SOLO el visitante
                                           anónimo sin sesión — con sesión
                                           el GET de arriba ya resuelve todo
                                           server-side) -> crea la
                                           entrevista y devuelve
                                           {patient_token}. Matchea contra
                                           los turnos aprobados DE ESE
                                           MÉDICO hoy (`_match_today_dni`) —
                                           exactamente un turno con el mismo
                                           DNI (dígitos exactos) ->
                                           `matched=true` (copia
                                           request_id); cero o varios ->
                                           entrevista igual, sin turno (la ve
                                           el médico en "Entrevistas sin
                                           turno", identificada por DNI). La
                                           respuesta NUNCA dice si matcheó —
                                           alguien tipeando DNI ajenos no
                                           debe poder inferir si existen
                                           turnos con ese documento.

Import-safe: stdlib + db/config/catalog lazy — nunca bloquea el boot.
"""

import base64
import re

from app.core import config
from app.core import static_fingerprint
from app.core import rate_window
from app.core import ids as _ids
from app.core.status_error import StatusError

# Nombre y bajada de cara al paciente (2026-08-03 — el responsable del producto: la "Entrevista
# previa" burocrática pasa a ser un beneficio explícito). El nombre interno
# (módulo `intake`, tablas, docs) NO cambia — sólo lo que el paciente lee:
# el <title> de esta página, el bloque de la barra superior
# (entrevista.js), y los mismos dos textos reutilizados en el CTA de mail
# (notifications.py:_ENTREVISTA_LEAD/_ENTREVISTA_CTA_LABEL).
_PAGE_TITLE = "Contale al médico cómo te sentís"
_PAGE_DESC = ("Contestás unas preguntas antes de la consulta y tu médico te "
              "atiende mejor informado.")

# Shape validators for an already-split native route segment. FastAPI's path
# converter accepts any non-slash string, so the router uses these to retain
# the public grammar and decline malformed values with a terminal 404.
TOKEN_SHAPE_RE = re.compile(r"^[A-Za-z0-9_-]+$")
DOOR_CODE_SHAPE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_MAX_MESSAGE = 2000
_MAX_DNI_RAW = 20  # antes de limpiar no-dígitos; format.dni_valid_length() decide el largo real
_MAX_IMAGEN_BYTES = 6 * 1024 * 1024  # mismo orden que voice.py's MAX_AUDIO_BYTES
_HITS = {}
_HOUR = 3600


class InterviewApiError(StatusError):
    """A typed 4xx raised by the domain layer for the native router."""


class InterviewPageError(Exception):
    """Carries the friendly denied/error page's status and rendered fields."""

    def __init__(self, status, mensaje, href, label):
        super().__init__(mensaje)
        self.status = status
        self.mensaje = mensaje
        self.href = href
        self.label = label


class InterviewRedirect(Exception):
    """Signals the policy redirect when the interview is disabled."""

    def __init__(self, location):
        self.location = location


def _env(key, default=""):
    return config.get(key, default)


def _per_hour():
    try:
        return int(_env("INTAKE_PER_HOUR") or 12)
    except ValueError:
        return 12


def _client_ip(headers, client_host):
    from app.auth import turnstile
    return turnstile.client_ip_from(headers, client_host)


def _allowed(ip):
    """Sliding-window per-IP limit — mismo patrón que chat_api._allowed,
    bucket propio (la entrevista no compite por cupo con el chat de
    turnos)."""
    return rate_window.allow(_HITS, ip, _per_hour(), _HOUR)


_CASE_HITS = {}


def _case_per_hour():
    try:
        return int(_env("INTAKE_CASE_PER_HOUR") or 20)
    except ValueError:
        return 20


def allowed_case_start(ip):
    """«Contanos tu caso»: new conversations per IP per hour, in their own
    bucket. Only a start counts — the answers inside one conversation are
    already capped by code (interview_agent._case_turn), and counting
    them in the interview's 12/h left a third patient behind one mobile
    carrier's shared address without the chat."""
    return rate_window.allow(_CASE_HITS, ip, _case_per_hour(), _HOUR)


# ---- GET /entrevista/<request_id> ------------------------------------------

def _esc(s):
    return (str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def denied_html(mensaje, href, label):
    """Pure render of the friendly denied/error page body — página angosta
    con la salida en positivo (nunca un error crudo), mismo espíritu que
    request-detail.js:renderNoEncontrada. Returns bytes (`patient_web/site_*.py.
    _shell` already encodes)."""
    from app.patient_web import site_shell
    from app.core import theme
    body = (f'<h1 class="h1">{_esc(mensaje)}</h1>'
            f'<p class="sd-ref"><a href="{_esc(href)}">{_esc(label)}</a></p>')
    return site_shell._shell(
        f"{_PAGE_TITLE} | {theme.brand_name()}", "", body, _PAGE_DESC, index=False)


def _find_or_create_interview(row, sid):
    from app.core import db_clinical
    interview = db_clinical.interview_for_request(sid)
    if interview and interview.get("entry_mode") != "qr":
        return interview
    # Pendiente sin asignación, la entrevista nace con el médico del bloque
    # elegido — así ya aparece en la cola de ese médico (interviews_for_day
    # matchea por doctor_id); si el hospital después asigna a otro, manda la
    # asignación de la solicitud, que es lo que la cola del día muestra.
    # `interview_bind` es el único que decide bajo lock: devuelve la del
    # paciente si otra pestaña la creó recién, y si la atada es una de QR
    # (alguien tipeó el DNI en la puerta) la desata y toma su lugar.
    doctor_id = row.get("medico_asignado_id") or row.get("slot_medico_id")
    return db_clinical.interview_bind(
        sid, doctor_id=doctor_id, entry_mode="post_booking",
        dni=row.get("dni"), matched=True)


def interview_shell_body(interview):
    """Pure HTML fragment shared by the session path and the
    `?t=<patient_token>` path (2026-08-03): both terminate here with the
    entrevista already resolved, only HOW it was authorized differs above."""
    token = _esc(interview.get("patient_token") or "")
    return f"""
  <div id="iv-host" data-token="{token}" data-mode="post_booking">
    <p style="color:var(--text-3);font-size:14px;">Cargando…</p>
  </div>
  <script src="{static_fingerprint.module_url('modules/intake/entrevista.js')}" defer></script>
"""


def interview_shell_embed_html(body):
    """Desde Mi cuenta la entrevista vive en un panel lateral. El iframe
    carga solamente la conversación: no duplica el header, footer, banner
    demo ni el asistente de reservas que ya están detrás en la página madre.
    Este documento se arma a mano en vez de pasar por `_shell()` (el embed es
    deliberadamente sin chrome) — pero eso lo dejó afuera del wiring de
    idioma que `_shell()` sí tiene, así que quedó hardcodeado a castellano sin
    importar el idioma del sitio (encontrado 2026-08-05: `entrevista.js` ya
    traduce vía T() from the canonical platform i18n module, but
    acá nunca llegaba ni `<html lang>` correcto ni el catálogo). Replicamos
    el mínimo que `_shell()` hace para esto: `lang` real en <html>, the
    bootstrap inline (window.SANA_I18N configuration data, including the
    catalog) and the client manifest so entrevista.js can import
    platform/i18n.mjs. Returns a str (the caller encodes)."""
    from app.core import i18n
    from app.core import theme
    lang = i18n.active()
    return f"""<!DOCTYPE html>
<html lang="{lang}" data-theme="{theme.current()}">
<head>{theme._branding_style()}
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{theme.favicon()}{theme.fonts()}
<link rel="stylesheet" href="{static_fingerprint.url('human-rounds.css')}">
<link rel="stylesheet" href="{static_fingerprint.module_url('modules/intake/entrevista.css')}">
{i18n.bootstrap_script(lang=lang)}
{static_fingerprint.manifest_script()}
</head>
<body class="iv-chat-body iv-embed-body">{body}</body>
</html>"""


def interview_shell_full_html(body):
    """Pure render of the full-chrome shell (the non-embed path)."""
    from app.patient_web import site_shell
    from app.core import theme
    return site_shell._shell(
        f"{_PAGE_TITLE} | {theme.brand_name()}", "", body, _PAGE_DESC,
        chrome="chat", index=False, body_class="iv-chat-body",
        head_extra=("<link rel=\"stylesheet\" href=\"" +
                    static_fingerprint.module_url("modules/intake/entrevista.css") + "\">"))


def resolve_entrevista_page(sid_str, link_token, usuario):
    """Pure decision for GET /entrevista/<sid>[?t=<token>]. `usuario` is the
    ALREADY-resolved session user (or None) — session resolution needs the
    real request, so the caller resolves it (and skips resolving it
    entirely when `link_token` is set, exactly like the original). Returns
    the `interview` row to render the shell for; raises `InterviewPageError`
    (401/403/404, friendly page) or `InterviewRedirect` (module disabled ->
    /my-requests), or lets `db.DbUnavailable` propagate (503, caught
    uniformly by the caller — every one of the three original catch sites
    used the identical message/href/label)."""
    from app.core import db_booking
    from app.core import db_clinical
    from app.modules.turnos import booking_requests
    from app.modules.intake import intake_admin

    sid = int(sid_str)

    if link_token:
        # Link con token (mail del día antes, CTA de "solicitud enviada"/
        # aprobada — notifications.py:_entrevista_cta): el token opaco ES
        # la autorización de ESA entrevista puntual, sin chequeo de sesión
        # ni de dueño — mismo modelo que appointment_offers.token. Nunca
        # confirma si `sid` existe: token ajeno, vencido, o de la
        # entrevista de OTRA solicitud -> la misma página amigable de "ya
        # no es válido" (nunca un 403/404 crudo). Puro GET: la fila ya
        # existe (la creó _entrevista_patient_token al armar el link), así
        # que acá no se muta nada.
        interview = db_clinical.interview_by_patient_token(link_token)
        if not interview or str(interview.get("request_id")) != str(sid):
            raise InterviewPageError(
                404, "Este enlace ya no es válido.", "/my-requests", "Mis turnos")
        return interview

    if not usuario:
        raise InterviewPageError(401, "Iniciá sesión para ver esto.", "/account", "Ingresar")

    dni = _ids.dni(booking_requests._user_dict(usuario).get("dni"))
    row = db_booking.get_booking_request(sid)
    if not row or dni not in (row.get("dni"), row.get("solicitante_dni")):
        raise InterviewPageError(
            403, "No encontramos esa entrevista.", "/my-requests", "Mis turnos")

    # Sin médico asignado todavía (solicitud pendiente), decide el médico del
    # bloque que el paciente eligió (slot_medico_id) — el mismo criterio que
    # mostró la CTA en "Solicitud enviada"; si no, la CTA llevaba a un
    # redirect mudo con el default del sistema apagado. Con médico asignado,
    # manda el asignado (la asignación real puede diferir del bloque).
    from app.patient_web import catalog
    doc = catalog.doctor_doc(row.get("medico_asignado_id") or row.get("slot_medico_id"), tolerant=True)
    if not intake_admin.enabled_for(doc):
        raise InterviewRedirect("/my-requests")

    return _find_or_create_interview(row, sid)


# ---- GET /e/<door_token> ---------------------------------------------------

def _match_today_dni(doctor_id, doctor_nombre, dni):
    """La solicitud aprobada de HOY de este médico cuyo DNI (comparado
    dígito a dígito) matchea exactamente el que se está usando para
    entrar, si hay EXACTAMENTE una — None con cero o con varias (mismo
    criterio que el viejo match por nombre: ambigüedad no matchea, la
    entrevista sigue igual, sin turno). Reemplaza a `_match_today`
    (nombre+apellido, retirado 2026-08-01: "Name fields are GONE from this
    flow" — el DNI es mucho menos ambiguo y es lo que YA tiene tanto la
    sesión de cuenta como el turno aprobado). `_belongs_to` es el mismo
    criterio id-primero/nombre-de-respaldo que usa la cola del día
    (doctor_appointments.py)."""
    from app.core import db
    from app.core import db_slots
    from app.modules.turnos import doctor_appointments as _da
    from app.core import tz as _tz
    from datetime import datetime as _dt

    obj = _ids.dni(dni)
    if not obj:
        return None
    dia = _dt.now(_tz.tzinfo()).date().isoformat()
    try:
        filas = db_slots.list_approved_day(dia)
    except db.DbUnavailable:
        return None
    propias = [r for r in filas if _da._belongs_to(r, doctor_id, doctor_nombre or "")]
    matches = [r for r in propias if _ids.dni(r.get("dni")) == obj]
    return matches[0] if len(matches) == 1 else None


def _crear_entrevista_qr_de_sesion(doc, usuario):
    """Visitante YA logueado en el sitio (site_auth de cuenta, no el Basic
    Auth de pre-lanzamiento) que escanea el cartel: identidad = sesión, así
    que el paso de identidad entero se saltea server-side — nunca se le
    vuelve a pedir nada a quien ya se identificó (patient-flow-design regla
    12). Matchea por DNI contra los turnos aprobados de HOY de este médico
    (`_match_today_dni`); sin matchear, la entrevista se crea igual, sin
    turno (matched=False) — el paciente entra al chat en cualquier caso, la
    IA nunca necesitó saber si matcheó."""
    from app.core import db_clinical
    from app.modules.turnos import booking_requests
    u = booking_requests._user_dict(usuario)
    dni = _ids.dni(u.get("dni"))
    doctor_id = doc.get("id")
    fila = _match_today_dni(doctor_id, doc.get("nombre"), dni) if dni else None
    nombre = str(u.get("nombre") or "").strip() or None
    apellido = str(u.get("apellido") or "").strip() or None
    if fila:
        # Identity = session: may continue the patient's own interview for
        # this appointment (never a QR one somebody else started).
        return db_clinical.interview_bind(
            fila.get("id"), doctor_id=doctor_id, entry_mode="qr",
            dni=dni or fila.get("dni"), matched=True,
            entered_name=nombre, entered_surname=apellido, authenticated=True)
    return db_clinical.interview_create(
        doctor_id=doctor_id, entry_mode="qr",
        request_id=fila.get("id") if fila else None,
        dni=dni or (fila.get("dni") if fila else None),
        entered_name=nombre, entered_surname=apellido,
        matched=bool(fila))


def resolve_qr_page(door_token, usuario):
    """Pure decision for GET /e/<door_token>. `usuario` is the already-
    resolved session user (or None).
    Returns the page's inner `#iv-host` HTML fragment (str); raises
    `InterviewPageError` (404, friendly page) for an unknown/disabled token,
    or lets `db.DbUnavailable` propagate (503, caught by the caller) from
    `_crear_entrevista_qr_de_sesion`.

    Con sesión de cuenta activa: arma `#iv-host` con `data-token` (como
    post-reserva) — la identidad YA se resolvió acá arriba, entrevista.js
    ni pinta el paso de identidad. Sin sesión: `data-qr-start` +
    `data-account-next="/e/<door_token>"` para que el botón "Ingresá a tu
    cuenta" vuelva acá después del login (accounts.py's `?next=`)."""
    from app.modules.intake import intake_admin
    doc = intake_admin.doctor_by_door_token(door_token)
    if doc is None or not intake_admin.enabled_for(doc):
        raise InterviewPageError(404, "No encontramos ese cartel.", "/", "Ir al inicio")

    if usuario:
        interview = _crear_entrevista_qr_de_sesion(doc, usuario)
        token = _esc(interview.get("patient_token") or "")
        return f"""
  <div id="iv-host" data-token="{token}" data-mode="qr">
    <p style="color:var(--text-3);font-size:14px;">Cargando…</p>
  </div>
  <script src="{static_fingerprint.module_url('modules/intake/entrevista.js')}" defer></script>
"""
    token = _esc(door_token)
    next_url = _esc(f"/e/{door_token}")
    return f"""
  <div id="iv-host" data-qr-start="{token}" data-account-next="{next_url}" data-mode="qr">
    <p style="color:var(--text-3);font-size:14px;">Cargando…</p>
  </div>
  <script src="{static_fingerprint.module_url('modules/intake/entrevista.js')}" defer></script>
"""


def qr_page_html(body):
    """Pure render of the QR shell's full-chrome document around `body`
    (the fragment `resolve_qr_page` returns)."""
    from app.patient_web import site_shell
    from app.core import theme
    head_extra = ("<link rel=\"stylesheet\" href=\"" +
                  static_fingerprint.module_url("modules/intake/entrevista.css") + "\">")
    return site_shell._shell(
        f"{_PAGE_TITLE} | {theme.brand_name()}", "", body, _PAGE_DESC,
        chrome="chat", index=False, body_class="iv-chat-body",
        head_extra=head_extra)


# ---- POST /api/intake/qr/<door_token>/start --------------------------------
# Sólo para el visitante ANÓNIMO (sin sesión) — el respaldo "empezá con tu
# DNI" debajo del botón de login. Con sesión, the page resolves everything
# server-side and this route is never called.

def qr_start_payload(door_token, body):
    """Pure domain body of POST /api/intake/qr/<door_token>/start — takes
    the ALREADY-PARSED JSON body; may raise `InterviewApiError` (404/400) or
    `db.DbUnavailable` (503, caught by the caller)."""
    from app.modules.intake import intake_admin
    doc = intake_admin.doctor_by_door_token(door_token)
    if doc is None or not intake_admin.enabled_for(doc):
        raise InterviewApiError(404, {"error": "no_encontrado"})

    from app.core import format
    dni = re.sub(r"\D", "", str((body or {}).get("dni") or "")[:_MAX_DNI_RAW])
    if not format.dni_valid_length(dni):
        raise InterviewApiError(400, {"error": "dni_invalido"})

    doctor_id = doc.get("id")
    fila = _match_today_dni(doctor_id, doc.get("nombre"), dni)

    from app.core import db_clinical
    interview = db_clinical.interview_create(
        doctor_id=doctor_id, entry_mode="qr",
        request_id=fila.get("id") if fila else None,
        dni=dni, entered_name=None, entered_surname=None,
        matched=bool(fila))

    # NUNCA se devuelve si matcheó ni datos de la solicitud — alguien
    # tipeando DNI ajenos no debe poder usar la respuesta para inferir si
    # existe (o no) un turno con ese documento.
    return {"patient_token": interview.get("patient_token")}


# ---- POST /api/intake/t/<token>/message + GET /api/intake/t/<token> -------

def load_interview(token):
    """Pure port of `_load_interview_or_404`'s lookup — returns the
    interview row, raises `InterviewApiError(404)` if missing, or lets
    `db.DbUnavailable` propagate (503, caught by the caller)."""
    from app.core import db_clinical
    interview = db_clinical.interview_by_patient_token(token)
    if not interview:
        raise InterviewApiError(404, {"error": "no_encontrada"})
    return interview


def _texto_de_contenido(content):
    """`content` es casi siempre un string; cuando el paciente adjuntó una
    foto (2026-08-01) es una lista OpenAI-style [texto, image_url] — acá
    sólo interesa el texto para mostrarlo al reconectar, nunca la imagen
    cruda de vuelta al cliente."""
    if isinstance(content, list):
        for parte in content:
            if isinstance(parte, dict) and parte.get("type") == "text":
                return str(parte.get("text") or "")
        return ""
    return str(content or "")


def _mensajes_publicos(clave):
    """Sólo texto de paciente/asistente, para el reconector — nunca el
    contenido crudo de chat_messages (acá no hay tool_calls, pero el
    contrato queda igual de explícito que en chat_agent). El [fin] y el
    [opciones: ...] que un mensaje de asistente ya persistido pueda traer se
    sacan acá también (mismo criterio que interview_agent.run_turn: el
    paciente nunca ve un marcador) — `opciones` viaja aparte SOLO en el
    mensaje que las trajo, así una reconexión puede repintar los botones de
    la última pregunta sin perderlos. Best-effort: una lectura caída
    degrada a [] en vez de romper la reconexión."""
    from app.core import db_chat
    from app.modules.intake import interview_agent
    out = []
    try:
        historial = db_chat.chat_history("interview", clave, 200) or []
    except Exception:  # noqa: BLE001
        return out
    for m in historial:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        texto = _texto_de_contenido(m.get("content"))
        opciones = []
        if role == "assistant" and texto:
            texto, opciones = interview_agent.sin_marcadores(texto)
        if not texto.strip():
            continue
        fila = {"role": role, "texto": texto}
        if opciones:
            fila["opciones"] = opciones
        out.append(fila)
    return out


def transcript_payload(interview, ip_allowed):
    """Pure(ish) port of `_transcript`'s whole body after the interview is
    loaded. `ip_allowed` is the caller's OWN rate-limit decision
    (`_allowed(_client_ip(...))`, request-dependent) for the ONE
    model turn a first-ever visit may spend seeding the opening question —
    this is the ONLY read path that can spend a model call, so it counts
    against the same per-IP cupo as sending a message (red-team 2026-08-01:
    this was the one route without a limit). Past the cupo it's not an
    error: the conversation is returned as-is and the greeting appears on
    the next attempt. Reads `_mensajes_publicos` twice on purpose, exactly
    like the original: once to decide whether seeding is needed, once more
    (fresh) for the response after a possible seed."""
    clave = interview.get("session_key") or f"interview:{interview.get('id')}"
    status = interview.get("status") or "active"

    # Primera visita: todavía sin historial -> sembrar la primera pregunta
    # ANTES de responder, así el paciente nunca ve una caja vacía (ni el
    # primer GET de la página ni el primer GET de esta API se quedan
    # esperando a que el paciente escriba algo primero).
    if status == "active" and not _mensajes_publicos(clave):
        if ip_allowed:
            from app.modules.intake import interview_agent
            interview_agent.run_turn(interview, "")

    return {"status": status, "mensajes": _mensajes_publicos(clave)}


def _decode_imagen(adjunto):
    """{"filename","mime","data_b64"} -> {"mime","data_uri"}, validada y
    lista para interview_agent.run_turn — o levanta ValueError con el
    motivo. A diferencia de booking_referrals._decode_referral (que acepta
    PDF, es una orden) acá SOLO se acepta una imagen: es una foto clínica
    (una erupción, un estudio), nunca un documento. Devuelve un data URI
    (no bytes sueltos) porque eso es exactamente lo que el content part
    `image_url` de la API espera — nada más que armar del lado del agente."""
    mime = str((adjunto or {}).get("mime") or "").strip().lower()
    data_b64 = str((adjunto or {}).get("data_b64") or "")
    if data_b64.strip().startswith("data:") and "," in data_b64:
        prefijo, data_b64 = data_b64.split(",", 1)
        if not mime:
            m = re.match(r"data:([^;,]+)", prefijo)
            if m:
                mime = m.group(1).strip().lower()
    if not mime.startswith("image/"):
        raise ValueError("sólo se puede adjuntar una foto")
    try:
        crudo = base64.b64decode(data_b64, validate=False)
    except Exception as e:  # noqa: BLE001
        raise ValueError("la foto no se pudo leer") from e
    if not crudo:
        raise ValueError("la foto no se pudo leer")
    if len(crudo) > _MAX_IMAGEN_BYTES:
        raise ValueError("la foto es muy pesada")
    data_uri = f"data:{mime};base64,{base64.b64encode(crudo).decode('ascii')}"
    return {"mime": mime, "data_uri": data_uri}


def message_payload(interview, body):
    """Pure domain body of POST .../message — takes the ALREADY-PARSED JSON
    body, called only after the caller loaded the interview and confirmed
    it's 'active' (see `_message`); may raise `InterviewApiError` (400)."""
    body = body if isinstance(body, dict) else {}
    texto = str(body.get("texto") or "").strip()[:_MAX_MESSAGE]
    imagen = None
    adjunto = body.get("adjunto")
    if isinstance(adjunto, dict) and adjunto.get("data_b64"):
        try:
            imagen = _decode_imagen(adjunto)
        except ValueError:
            raise InterviewApiError(400, {"error": "adjunto_invalido"})
    if not texto and not imagen:
        raise InterviewApiError(400, {"error": "falta el mensaje"})

    from app.modules.intake import interview_agent
    resultado = interview_agent.run_turn(interview, texto, imagen=imagen)
    return {
        "texto": resultado["texto"], "fin": bool(resultado["fin"]),
        "opciones": resultado.get("opciones") or [],
    }


# ---- POST /api/intake/t/<token>/reopen -------------------------------------
# "Actualizar entrevista" (2026-08-01): reusa el mismo rate limit por IP que
# /message (un bucket, no dos) — reabrir corre un turno real del modelo, el
# mismo costo que mandar un mensaje.

def reopen_payload(interview):
    """Pure domain body of POST .../reopen — called only after the caller
    loaded the interview (see `_reopen`); may raise `db.DbUnavailable` (503,
    caught by the caller)."""
    clave = interview.get("session_key") or f"interview:{interview.get('id')}"
    if (interview.get("status") or "active") == "active":
        # Ya estaba abierta (doble click, o alguien la reabrió justo antes)
        # — idempotente, nunca un error: el paciente sólo quiere seguir
        # escribiendo.
        return {"status": "active", "mensajes": _mensajes_publicos(clave)}

    from app.core import db_clinical
    reabierta = db_clinical.interview_reopen(interview.get("id"))
    if reabierta is None:
        # Carrera: alguien más la reabrió entre el load de arriba y el
        # UPDATE — esa otra request ya sembró la pregunta de actualización;
        # acá sólo devolvemos el estado actual, nunca corremos OTRO turno
        # (evita una segunda pregunta duplicada).
        return {"status": "active", "mensajes": _mensajes_publicos(clave)}

    from app.modules.intake import interview_agent
    interview_agent.run_turn(reabierta, "", reabierta=True)

    return {"status": "active", "mensajes": _mensajes_publicos(clave)}
