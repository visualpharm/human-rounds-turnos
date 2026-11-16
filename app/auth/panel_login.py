"""panel_login.py — panel de la pantalla de login del admin (Pinamar Turnos):
"Humor de guardia" + "Consejos de uso", con analytics de exposición.

La pantalla de acceso a /admin muestra, junto al formulario, un panel con dos
pestañas: memes médicos curados (traducidos al castellano en el epígrafe) y
consejos cortos de uso del sistema. Cada visitante del gate queda asignado a
una VARIANTE (qué pestaña ve abierta por defecto, 50/50 client-side, persistida
en localStorage) y el reporte admin "Panel de login" compara ambas cohortes:
uso de cada pestaña, interacción y efecto en el login. UI en admin.html
(sección gate); reporte en reports.py + admin-reports.js.

Rutas nativas de este módulo, registradas por ``panel_login_router.py``:
  GET  /api/login-panel         contenido del panel: {humor:[...], consejos:[...]}
                                Sin auth (es ANTES del login). El humor sale de
                                static/humor/humor.json y SÓLO se sirven los
                                niveles appropriate + borderline — un ítem
                                marcado inappropriate nunca llega al cliente.
  POST /api/login-panel/event   beacon de analytics {evento, variante, tab?,
                                item?, ms?, sid} → tabla panel_login_eventos.
                                Mismo contrato que searches.py: sin PII, sin
                                IP guardada, 204 siempre (DB caída → no-op).

Anti-abuso PROPIO (mismo patrón que searches.py, NO abuse_guard.py/Turnstile: esto
es un INSERT barato): tope en memoria por IP, 600/día por defecto (env
TURNOS_LOGIN_PANEL_PER_DAY). Al tope: 204 sin persistir.

El humor es contenido del MOTOR, no del paquete de datos de la localidad
(sitedata.py/TURNOS_WEBDATA_DIR): los memes y consejos son los mismos para
cualquier municipio, igual que logo-admin.png que admin.html referencia por
/static/ directo. Por eso acá se lee static/humor/ del repo a propósito,
sin sitedata.path().

Import-safe: sólo stdlib + db (lazy por diseño).
"""

import json
import os
import re

from app.core import db_analytics
from app.core import paths
from app.core import rate_window

# Tier 2 batch C: panel_login.py now lives in auth/, one level deeper than
# the repo root — HUMOR_JSON has to keep pointing at the repo's static/
# tree (see the module docstring above: humor is engine content, read from
# static/humor/ on purpose, not a data-pack asset), so go up TWO dirnames,
# not one.
ROOT = paths.PROJECT_ROOT
HUMOR_JSON = os.path.join(ROOT, "static", "humor", "humor.json")

# Niveles publicables. "inappropriate" queda deliberadamente afuera: el lote
# inicial (2026-07-18) fue aprobado completo por el responsable del producto incluidos los borderline
# suaves; si algún día hay que endurecer, sacar "borderline" de acá.
NIVELES_PUBLICADOS = ("appropriate", "borderline")

# Consejos de uso del sistema — copy del motor (como los strings del admin),
# no del paquete de datos. Uno por día por persona (igual que el humor).
# `texto` admite <b> para las palabras-ancla del escaneo (writing-for-web):
# lo escribe ESTE módulo, no un usuario — el cliente lo inyecta confiando en
# que el server es la única fuente. `donde` (opcional) = en qué parte del
# panel vive lo descripto; se muestra como línea aparte atenuada.
# `src` = mnemónico dibujado (static/consejos/, mismo estilo cálido que
# talleres/): puente de recuperación, no decoración — la escena reconstruye la
# regla aunque se tape el rótulo (método adaptado de Italiano Brutale a
# registro inocente; generados con gpt-image-2, 2026-07-19).
CONSEJOS = [
    {
        "id": "ia-primero",
        "titulo": "La orden ya viene leída",
        "texto": "La IA marca el <b>veredicto</b> y los <b>campos dudosos</b> de cada orden. Empezá por lo marcado, no releas todo.",
        "donde": "Solicitudes, tarjeta de cada pedido",
        "src": "/static/consejos/ia-primero.jpg",
        "alt": "Un robot secretario entrega una hoja con tres franjas resaltadas; la persona lee sólo lo marcado con lupa y la pila de papeles queda gris, intacta.",
    },
    {
        "id": "derivar-al-aprobar",
        "titulo": "Derivá y asigná al aprobar",
        "texto": "En el mismo paso de aprobar podés <b>derivar a otro servicio</b> y <b>asignar médico</b>; el turno impacta en HSI ya corregido.",
        "donde": "Solicitudes, junto al botón Aprobar",
        "src": "/static/consejos/derivar-al-aprobar.jpg",
        "alt": "Un guardavías tira una sola palanca y a la vez: un trencito con forma de ticket recibe un sello verde, el cambio de vías se tuerce y un médico espera saludando en el andén de destino.",
    },
    {
        "id": "motivos-cerrados",
        "titulo": "Rechazá con motivo de la lista",
        "texto": "El motivo elegido le llega <b>por email al paciente</b>, redactado para que sepa cómo seguir. “Otro motivo” es la excepción, no la regla.",
        "donde": "Solicitudes, selector de rechazo",
        "src": "/static/consejos/motivos-cerrados.jpg",
        "alt": "Una empleada saca UNA tarjeta preimpresa de un fichero de madera y se la da a una paloma mensajera; la paciente la recibe en su buzón y sigue camino con rumbo.",
    },
    {
        "id": "lista-de-espera",
        "titulo": "La lista de espera trabaja sola",
        "texto": "Si un paciente <b>cancela</b>, el turno liberado se le ofrece automáticamente <b>al siguiente de la cola</b>. Nada que reasignar a mano.",
        "src": "/static/consejos/lista-de-espera.jpg",
        "alt": "Un paciente se va saludando y la silla liberada, sobre una cinta transportadora, trae sola a la siguiente persona de la cola; la recepcionista toma mate tranquila.",
    },
    {
        "id": "agenda-medico",
        "titulo": "Agendas y vacaciones",
        "texto": "La <b>agenda semanal</b> de cada médico se edita en su ficha; <b>vacaciones y feriados</b> van como excepciones, no borrando horarios.",
        "donde": "Médicos → Horarios de atención",
        "src": "/static/consejos/agenda-medico.jpg",
        "alt": "Un médico con sombrero de paja y valija pega un sticker redondo de sombrilla SOBRE la agenda semanal intacta: la grilla sigue ahí, nada tachado ni borrado.",
    },
    {
        "id": "feed-medico",
        "titulo": "El médico tiene su calendario",
        "texto": "Una cuenta con rol médico ve <b>sus turnos aprobados</b> y puede suscribir su calendario personal por <b>feed ICS</b>.",
        "donde": "Mis turnos, al pie",
        "src": "/static/consejos/feed-medico.jpg",
        "alt": "Un médico toma café en un banco de plaza mirando su teléfono; un hilo punteado conecta la agenda de pared del hospital con la pantalla, que muestra los mismos bloques de colores.",
    },
]

# Misma estructura que CONSEJOS para los visitors no-es del gate: los
# mnemónicos dibujados (static/consejos/) son una sola pieza con rótulos
# castellanos, pero el copy que SÍ es HTML (título, cuerpo, dónde) no puede
# salir en español en una interfaz inglesa. Mismos ids: el beacon de
# analytics reporta el id, y ambas cohortes tienen que agregarse juntas.
CONSEJOS_EN = [
    {
        "id": "ia-primero",
        "titulo": "The order arrives pre-read",
        "texto": "The AI flags each order's <b>verdict</b> and <b>questionable fields</b>. Start from the flags — don't re-review everything.",
        "donde": "Requests, on each request card",
        "src": "/static/consejos/ia-primero.jpg",
        "alt": "A robot secretary hands over a sheet with three highlighted strips; the person reads only what is marked, magnifying glass in hand, and the paper pile stays grey and untouched.",
    },
    {
        "id": "derivar-al-aprobar",
        "titulo": "Refer and assign while approving",
        "texto": "In the same step as approving you can <b>refer to another service</b> and <b>assign a doctor</b>; the appointment reaches HSI already corrected.",
        "donde": "Requests, next to the Approve button",
        "src": "/static/consejos/derivar-al-aprobar.jpg",
        "alt": "A signal operator pulls a single lever and at once: a ticket-shaped little train gets a green stamp, the tracks bend toward its destination, and a doctor waits waving at the platform.",
    },
    {
        "id": "motivos-cerrados",
        "titulo": "Reject with a listed reason",
        "texto": "The reason you pick is <b>emailed to the patient</b>, worded so they know how to proceed. \u201cOther\u201d is the exception, not the rule.",
        "donde": "Requests, rejection picker",
        "src": "/static/consejos/motivos-cerrados.jpg",
        "alt": "An employee takes ONE pre-printed card from a wooden filing drawer and hands it to a carrier pigeon; the patient finds it in her mailbox and walks on with a direction to follow.",
    },
    {
        "id": "lista-de-espera",
        "titulo": "The waitlist works on its own",
        "texto": "If a patient <b>cancels</b>, the freed slot is offered automatically <b>to the next person in the queue</b>. Nothing to reassign by hand.",
        "src": "/static/consejos/lista-de-espera.jpg",
        "alt": "A patient waves goodbye while the freed chair, riding a conveyor belt, brings the next person in line by itself; the receptionist calmly sips mate.",
    },
    {
        "id": "agenda-medico",
        "titulo": "Schedules and time off",
        "texto": "Each doctor's <b>weekly schedule</b> is edited on their profile; <b>vacations and holidays</b> go in as exceptions — no deleting slots.",
        "donde": "Doctors → Office hours",
        "src": "/static/consejos/agenda-medico.jpg",
        "alt": "A doctor in a straw hat with a suitcase sticks a round umbrella sticker ON TOP of the intact weekly schedule: the grid is still there, nothing crossed out or deleted.",
    },
    {
        "id": "feed-medico",
        "titulo": "Doctors get their own calendar",
        "texto": "An account with the doctor role sees <b>its approved appointments</b> and can subscribe to a personal calendar via an <b>ICS feed</b>.",
        "donde": "My appointments, at the bottom",
        "src": "/static/consejos/feed-medico.jpg",
        "alt": "A doctor sips coffee on a park bench, looking at their phone; a dotted thread connects the hospital's wall schedule with the screen, which shows the same colored blocks.",
    },
]

DEFAULT_PER_DAY = 600
_DAY = 86400

_HITS = rate_window.Hits(_DAY)  # ip -> [epoch, ...] (mismo esquema de poda oportunista que searches.py)
_sweep = _HITS.sweep

_CACHE = {"mtime": None, "items": [], "items_en": []}
_DIMS = {}  # src -> (w, h) | None — los assets son estáticos, se mide una vez


def _jpeg_dims(path):
    """(w, h) de un JPEG (marcadores SOF) o PNG (IHDR) — stdlib, sin PIL.
    Algún .jpg del lote es en realidad un PNG, por eso se mira la firma."""
    try:
        with open(path, "rb") as f:
            head = f.read(2)
            if head == b"\x89P":  # PNG: IHDR fijo tras la firma de 8 bytes
                data = f.read(22)  # resto de la firma (6) + largo (4) + "IHDR" (4) + w (4) + h (4)
                if data[10:14] != b"IHDR":
                    return None
                ancho = int.from_bytes(data[14:18], "big")
                alto = int.from_bytes(data[18:22], "big")
                return (ancho, alto) if ancho and alto else None
            if head != b"\xff\xd8":
                return None
            while True:
                b = f.read(1)
                if not b:
                    return None
                if b != b"\xff":
                    continue
                marker = f.read(1)
                while marker == b"\xff":
                    marker = f.read(1)
                if not marker:
                    return None
                m = marker[0]
                if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
                    f.read(3)  # largo del segmento (2) + precisión (1)
                    alto = int.from_bytes(f.read(2), "big")
                    ancho = int.from_bytes(f.read(2), "big")
                    return (ancho, alto) if ancho and alto else None
                if m in (0x01, 0xD8) or 0xD0 <= m <= 0xD7:
                    continue
                seg = int.from_bytes(f.read(2), "big")
                if seg < 2:
                    return None
                f.seek(seg - 2, 1)
    except OSError:
        return None


def _dims(src):
    """Dimensiones del asset en `src` (/static/... del repo), cacheadas. Van al
    payload como w/h: el cliente las pone en el <img> para que el navegador
    reserve el alto ANTES de descargar la imagen (sin salto de layout en el
    gate). Imagen ilegible → sin w/h, el cliente degrada como hasta ahora."""
    if src not in _DIMS:
        _DIMS[src] = _jpeg_dims(os.path.join(ROOT, src.lstrip("/")))
    return _DIMS[src]


def _con_dims(item):
    d = dict(item)
    wh = _dims(d["src"]) if d.get("src") else None
    if wh:
        d["w"], d["h"] = wh
    return d

# "item" como EVENTO murió con el scroller (2026-07-18: un meme por día, sin
# "Otro"); item quedó como CAMPO de exposicion/tab = qué tarjeta se mostró.
_EVENTOS = ("exposicion", "tab", "login_ok", "login_error")
_VARIANTES = ("humor", "consejos")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SID_RE = re.compile(r"^[a-f0-9]{8,32}$")


def _humor_items(lang="es"):
    """Ítems publicables del manifest, cacheados por mtime. Manifest roto o
    ausente → lista vacía (el panel degrada a sólo-consejos, nunca un 500).

    En castellano se sirve `file` (la versión traducida, texto reescrito
    dentro de la imagen). En CUALQUIER otro idioma se sirve el ORIGINAL
    `file_original` — los memes fuente vinieron en inglés y su traducción
    al castellano es parte del asset, no del cliente (responsable del producto, 2026-09-18:
    "a Spanish meme for the English interface is unacceptable"). Sin
    original anotado, el mismo `file` se sirve tal cual."""
    try:
        mtime = os.stat(HUMOR_JSON).st_mtime
    except OSError:
        return []
    if _CACHE["mtime"] != mtime:
        try:
            with open(HUMOR_JSON, encoding="utf-8") as f:
                data = json.load(f)
            items, items_en = [], []
            for it in data.get("items", []):
                if it.get("nivel") not in NIVELES_PUBLICADOS:
                    continue
                base = {
                    "id": it["id"],
                    "texto": it["texto"],
                }
                items.append(_con_dims({**base,
                    "src": "/static/humor/" + it["file"],
                    "alt": it.get("alt") or "",
                }))
                items_en.append(_con_dims({**base,
                    "src": "/static/humor/" + (it.get("file_original") or it["file"]),
                    "alt": it.get("alt_en") or it.get("alt") or "",
                }))
            _CACHE.update(mtime=mtime, items=items, items_en=items_en)
        except Exception:  # noqa: BLE001  (JSON roto: no tumbar el gate)
            return _CACHE.get("items", [])
    return _CACHE.get("items_en", []) if lang != "es" else _CACHE.get("items", [])


# ---- rate limit (calco de searches.py) -------------------------------------

def _per_day():
    try:
        v = os.environ.get("TURNOS_LOGIN_PANEL_PER_DAY")
        return int(v) if v else DEFAULT_PER_DAY
    except ValueError:
        return DEFAULT_PER_DAY


def permitido(ip):
    return _HITS.allow(ip, _per_day())


# ---- GET /api/login-panel ----------------------------------------------------

def contenido_payload(lang="es"):
    """Payload puro de GET /api/login-panel — extraído para que el
    transporte nativo (panel_login_router.py) lo llame sin pasar por un
    handler-shaped object. `lang` resuelve el idioma del visitante
    (core.i18n.resolve_lang en el router): castellano sirve los memes
    traducidos; cualquier otro idioma, los originales en inglés."""
    return {"humor": _humor_items(lang),
            "consejos": [_con_dims(c) for c in (CONSEJOS if lang == "es" else CONSEJOS_EN)]}


# ---- POST /api/login-panel/event --------------------------------------------

class EventoInvalido(ValueError):
    """Mensaje EXACTO que el legacy handle() le manda al cliente en 400."""


def validar_evento(body):
    """Valida el payload de POST /api/login-panel/event. Devuelve
    (sid, variante, evento, tab, item, ms) listo para db_analytics.log_panel_login, o
    levanta EventoInvalido(mensaje) — mismo mensaje/orden que el legacy
    handle() de más abajo. Pura: no toca ningún handler."""
    if not isinstance(body, dict):
        raise EventoInvalido("JSON inválido")

    evento = str(body.get("evento") or "")
    variante = str(body.get("variante") or "")
    sid = str(body.get("sid") or "")
    if evento not in _EVENTOS or variante not in _VARIANTES or not _SID_RE.match(sid):
        raise EventoInvalido("evento inválido")

    tab = str(body.get("tab") or "")
    if tab and tab not in _VARIANTES:
        raise EventoInvalido("tab inválido")

    item = str(body.get("item") or "")
    if item and not _ID_RE.match(item):
        raise EventoInvalido("item inválido")

    ms = body.get("ms")
    if ms is not None:
        try:
            ms = int(ms)
        except (TypeError, ValueError):
            ms = None
        else:
            if not (0 <= ms <= 3_600_000):  # >1h en el gate no es una medición útil
                ms = None

    return sid, variante, evento, tab or None, item or None, ms


def registrar_evento(sid, variante, evento, tab, item, ms):
    """Persiste el evento ya validado — mismo `db_analytics.log_panel_login()` que
    llamaba el legacy handle()."""
    db_analytics.log_panel_login(sid, variante, evento, tab, item, ms)
