"""otp_send.py — OTP code sending. The one production transport is Meta's
approved Authentication template (`OTP_PROVIDER=meta` + `META_WA_OTP_TEMPLATE`,
2026-11-16); without that variable nothing leaves: the WAHA sender reaches
loopback dev only and SMS is a future stub.

Context (tracking card 2026-07-11-contact-sms-provider-to-enable-outgoing-sms-for-
sana-turnos-): The product owner wanted outgoing SMS to gate registration and password
recovery. The July plan was WhatsApp-OTP via a WAHA instance
(free, no vendor signup, ~100% AR deliverability — vs Twilio/Vonage at
~US$0.05+/msg plus A2P/campaign registration) implemented NOW, reversible,
with the seam left open for a paid SMS provider later. What shipped is the
SEAM — and since the 2026-08-14 transport contract WAHA can only ever be a
loopback dev target; the production path is Meta's Authentication template
(below).

Seam provider-agnostic: env OTP_PROVIDER=whatsapp|sms|meta (default whatsapp —
the WAHA sender, which only reaches a loopback dev URL; in production the
default sends nothing).
Same best-effort pattern as notifications/mailgun.py (`email_mailgun.py`
pre-split) — never raises; missing
configuration degrades to a logged no-op (local dev — a production
WAHA instance is no longer a possible target at all, transport contract
2026-08-14: production OTP goes through Meta's Authentication template).

Env (WhatsApp via WAHA — LOCAL DEV ONLY, transport contract 2026-08-14: the
URL must point at loopback; a remote WAHA instance is refused by the code and
carries zero traffic, so it can never be a production target. Never reuse the
Icons8/Il Buco/personal instances — a patient's OTP must not come from
another project's number):
  OTP_WAHA_URL       loopback base url, e.g. http://127.0.0.1:3005
  OTP_WAHA_API_KEY   that instance's X-Api-Key
  OTP_WAHA_SESSION   WAHA session name (default "default")
Without all 3 configured -> logged no-op, never breaks the calling flow.

SMS (future): OTP_PROVIDER=sms is today a stub that logs and returns False —
really enabling it means picking Twilio/Vonage/other + AR pricing first.

Outbound free-text from the turnos CHAT (2026-07-23, `enviar_texto` — NOT
the OTP: that routes via OTP_PROVIDER above): The product owner wants
to try the bot with Meta's official test number (WhatsApp Cloud API) without
losing WAHA — `TURNOS_WA_PROVIDER=meta|waha` picks the transport. (Contract,
2026-08-14: unset means Meta when META_WA_TOKEN is set, otherwise a logged
no-op — auto-detect NEVER selects WAHA, and a pinned `waha` additionally
requires a loopback OTP_WAHA_URL; a remote WAHA URL is refused outright.)
The two transports coexist: which one `enviar_texto` uses is a deployment
decision (the env), never a code one.

Env Meta WhatsApp Cloud API (free text — the patient writes first and opens
the 24h service window, so NO approved template is needed, unlike the OTP):
  META_WA_TOKEN              access token (a system user in prod)
  META_WA_PHONE_NUMBER_ID    sender number id (WhatsApp API Setup)
  META_WA_GRAPH_VERSION      Graph version (default v25.0)
  META_WA_OTP_TEMPLATE       approved Authentication template for the OTP
                             (es_AR, copy-code button; empty = no OTP by Meta)
Without token or phone_number_id -> logged no-op, same as unconfigured WAHA.
Logs NEVER print the token; the phone is masked to its last 4 digits
(`_mask_phone`) — and since 2026-10-21 so does every other line of this
module (WAHA, the SMS stub): logs are anonymous, `core/log_pii.py`.

Transport contract (2026-08-14, closing the runbook
docs/meta-whatsapp-adoption-runbook.md §2 — "fail-closed invariant"):

- Production is Meta-only. WAHA is an unofficial transport (permanent-ban
  risk, no recourse) and must NEVER carry patient traffic.
- WAHA is restricted to local development: it requires an EXPLICIT
  `TURNOS_WA_PROVIDER=waha` AND an `OTP_WAHA_URL` pointing at loopback
  (127.0.0.0/8, localhost, ::1). A remote URL sends nothing — zero network,
  logged no-op — even with the opt-in set.
- Auto-detect no longer falls back to WAHA: without `TURNOS_WA_PROVIDER`
  there is Meta when a token exists, otherwise the result is "" (a logged
  no-op). With no provider configured nothing goes out; never a silent
  fallback to WAHA.
- The OTP travels over Meta ONLY as the approved Authentication template
  named by `META_WA_OTP_TEMPLATE` (`_enviar_otp_meta`: the code is the body's
  one parameter and the «Copiar código» button's parameter; never free
  text). `OTP_PROVIDER=meta` without that variable, or any unknown value, is
  a logged no-op. `OTP_PROVIDER=whatsapp` stays WAHA and inherits the
  loopback restriction above.

Meta limits and errors (2026-11-16): every Cloud API POST goes through
`_meta_post`. A rate limit (codes 4, 80007, 130429, 131048, 131056, HTTP 429,
or any `Retry-After`) pauses ALL Meta sends for `Retry-After` seconds (60 s
without it, 15 min at most); the pause and the last error live in
`<data dir>/private/meta-wa-state.json` (numbers only), shared with the queue
worker and read by «Sistema». An error code is logged with its cause in words
(`meta_error_reason`, i18n with a Spanish fallback), never with Meta's own
message (it can echo what we sent).
"""

import re

from app.core import format
import email.utils
import fcntl
import math
import os
import sys
import json
import threading
import urllib.error
import urllib.parse
import urllib.request

from app.core import clock
from app.core import config
from app.core import i18n
from app.core import launch_signals
from app.core import log_pii
from app.ops import demo_installation


def _log(msg):
    print(f"[otp_send] {msg}", file=sys.stderr, flush=True)


def _wa_digits(destino):
    """Dígitos con código de país para WhatsApp, con la MISMA regla que usa
    todo el sitio para armar un link wa.me: `format.tel_wa`.

    Importa porque el `contacto` guardado llega de muchas formas y desde
    2026-07-28 se guarda canónico (`+54 2254 40-1234`). Las dos versiones que
    había acá se rompían con eso: `_chat_id` mandaba los dígitos pelados (sin
    país) y `_meta_digits` devolvía tal cual lo que empezara con '+', así que
    un número guardado como `+542254123456` salía SIN el 9 y no llegaba. Un
    celular argentino es siempre 549 + los 10 dígitos nacionales, venga escrito
    como venga."""
    return format.tel_wa(destino)


def _chat_id(destino):
    return f"{_wa_digits(destino)}@c.us"


def _mensaje(code):
    from app.core import theme
    return (
        f"Tu código de verificación de {theme.brand_name()} es {code}. "
        "Vence en 10 minutos. Si no lo pediste vos, ignorá este mensaje."
    )


def _cfg_whatsapp():
    return config.get_text("OTP_WAHA_URL"), config.get_text("OTP_WAHA_API_KEY"), config.get_text("OTP_WAHA_SESSION", "default")


def _waha_es_loopback(url):
    """True sólo si la URL de WAHA apunta a la propia máquina (127.0.0.0/8,
    localhost, ::1). WAHA es un transporte no oficial: fuera de un loopback de
    desarrollo no se usa nunca (contrato de transporte, docstring del módulo).
    Una URL vacía o imparseable NO es loopback — fail closed."""
    try:
        host = (urllib.parse.urlsplit(url).hostname or "").strip("[]").lower()
    except ValueError:
        return False
    if not host:
        return False
    if host in ("localhost", "::1"):
        return True
    return bool(re.match(r"^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$", host))


def waha_config():
    """(url, api_key, session) de la instancia WAHA propia — acceso público
    para módulos que necesitan la MISMA instancia para otra cosa que no es un
    OTP (whatsapp_chat.py: bajar el media de un mensaje entrante con el mismo
    X-Api-Key). Sin URL/key configuradas, url/key vienen vacíos — el caller
    decide cómo degradar."""
    return _cfg_whatsapp()


def _enviar_whatsapp(destino, texto):
    url, key, session = _cfg_whatsapp()
    if not url or not key:
        _log(f"WAHA no configurado — no-op, no se envió a {_mask_phone(destino)}")
        return False
    if not _waha_es_loopback(url):
        _log(f"WAHA remoto deshabilitado (sólo loopback de desarrollo; "
             f"producción = Meta Cloud API) — no-op, no se envió a {_mask_phone(destino)}")
        return False
    chat_id = _chat_id(destino)
    if not chat_id or chat_id == "@c.us":
        _log(f"destino inválido: {_mask_phone(destino)}")
        return False
    body = json.dumps({"session": session, "chatId": chat_id, "text": texto}).encode("utf-8")
    req = urllib.request.Request(
        url.rstrip("/") + "/api/sendText",
        data=body,
        headers={"X-Api-Key": key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            ok = 200 <= r.status < 300
            _log(f"WhatsApp OTP -> {_mask_phone(destino)} HTTP {r.status}")
            _signal_whatsapp(ok)
            return ok
    except Exception as exc:  # noqa: BLE001 — best-effort, nunca rompe el caller
        _log(f"WhatsApp OTP FAILED -> {_mask_phone(destino)}: {log_pii.exc(exc)}")
        _signal_whatsapp(False)
        return False


def _enviar_sms(destino, texto):
    _log(
        f"proveedor SMS pago aún no elegido/configurado (stub) — no se envió a "
        f"{_mask_phone(destino)}; ver tracking card sms-provider para negociar pricing"
    )
    return False


_PROVIDERES = {"whatsapp": _enviar_whatsapp, "sms": _enviar_sms}


# ---- WhatsApp Cloud API de Meta (texto libre, chat de turnos) ---------------

def _mask_phone(destino):
    """Enmascara todo menos los últimos 4 dígitos — para loguear sin exponer
    el teléfono completo (convención pedida por el responsable del producto para el transporte Meta,
    hoy de todo log: `core/log_pii.py`)."""
    return log_pii.phone(destino)


def _signal_whatsapp(accepted):
    """The launch digest's WhatsApp numbers (core/launch_signals.py): one count per message the transport
    answered for, `whatsapp_failed` also a Sentry code. A transport that is not configured never gets here."""
    if accepted:
        launch_signals.count("whatsapp_sent")
    else:
        launch_signals.handled("whatsapp_failed")


def _meta_config():
    return {
        "token": config.get_text("META_WA_TOKEN"),
        "phone_number_id": config.get_text("META_WA_PHONE_NUMBER_ID"),
        "graph_version": (config.get_text("META_WA_GRAPH_VERSION", "v25.0") or "v25.0").lstrip("/"),
    }


def _meta_digits(destino):
    """Dígitos con código de país para la Cloud API — la misma regla que WAHA
    y que los links wa.me del sitio (ver _wa_digits)."""
    return _wa_digits(destino)


def _meta_request(destino, mensaje):
    """The POST to the Cloud API's /messages for `destino`, or None (after logging why) when nothing can be
    sent: Meta not configured, or no usable number. `mensaje` is the part of the body that depends on the
    kind of message ({"type": "text", "text": ...} or {"type": "template", "template": ...})."""
    cfg = _meta_config()
    if not cfg["token"] or not cfg["phone_number_id"]:
        _log(f"Meta Cloud API no configurada — no-op, no se envió a {_mask_phone(destino)}")
        return None
    to = _meta_digits(destino)
    if not to:
        _log(f"destino inválido: {_mask_phone(destino)}")
        return None
    endpoint = (f"https://graph.facebook.com/{cfg['graph_version']}/"
                f"{cfg['phone_number_id']}/messages")
    body = json.dumps({"messaging_product": "whatsapp", "to": to, **mensaje}).encode("utf-8")
    return urllib.request.Request(
        endpoint, data=body, method="POST",
        headers={"Authorization": f"Bearer {cfg['token']}",
                 "Content-Type": "application/json"},
    )


# ---- Meta: límites de envío, motivos de error y estado compartido -------------
#
# Meta frena a un número que manda demasiado (códigos 4, 80007, 130429, 131048, 131056 o un HTTP 429, a veces
# con `Retry-After`). Seguir mandando durante un freno no entrega nada y empeora la calidad del número, así que el
# primer freno PAUSA TODA salida por Meta — chat, avisos y códigos — por lo que pide `Retry-After` (sin el
# encabezado, `_PAUSA_SIN_RETRY_AFTER_S`; nunca más de `_PAUSA_MAX_S`). La pausa y el último error viven en un
# archivo chico del volumen de datos (`private/meta-wa-state.json`): el worker de la cola es otro proceso y los
# recordatorios salen de ahí, así que la memoria de un solo proceso no alcanzaría; «Sistema» lee el mismo
# archivo. Guarda sólo números (códigos, estado HTTP, instantes): nunca el teléfono ni el texto de Meta.

# Códigos de Meta que significan «estás mandando demasiado» (Graph API rate limits + los de WhatsApp).
_META_CODIGOS_LIMITE = frozenset({4, 80007, 130429, 131048, 131056})
_PAUSA_SIN_RETRY_AFTER_S = 60
_PAUSA_MAX_S = 15 * 60
# Un error de estos códigos deja la línea en rojo en «Sistema»: token, permisos, cuenta o plantilla — hasta que
# alguien actúe no sale nada (un 132001 con la plantilla del código mal escrita frena todos los códigos).
META_CODIGOS_GRAVES = frozenset({0, 10, 190, 200, 368, 131005, 131031, 131042, 132001, 132015, 132016})
_ESTADO_ARCHIVO = "meta-wa-state.json"
_estado_lock = threading.Lock()
# Copia en memoria: lo que vale cuando el volumen no se puede escribir (nunca rompe el envío).
_estado_memoria = {}

# Código de error de Meta -> la causa en palabras (log en castellano, «Sistema» en el idioma del panel).
# Claves enteras y literales (el escáner de traducciones no lee claves armadas). Fuente: «Error codes» de la
# WhatsApp Cloud API y los errores de Graph API.
_META_MOTIVOS = {
    0: ("admin.system.status.wa.meta_error.0",
        "Meta no pudo autenticar la cuenta: revisá el token"),
    3: ("admin.system.status.wa.meta_error.3",
        "La app de Meta no tiene permiso para esta función"),
    4: ("admin.system.status.wa.meta_error.4",
        "Meta frenó los envíos: la app pasó su límite de llamadas"),
    10: ("admin.system.status.wa.meta_error.10",
         "El token no tiene el permiso de WhatsApp que hace falta"),
    100: ("admin.system.status.wa.meta_error.100",
          "Meta rechazó un parámetro del mensaje"),
    190: ("admin.system.status.wa.meta_error.190",
          "El token de Meta venció o fue revocado: hay que generar uno nuevo"),
    200: ("admin.system.status.wa.meta_error.200",
          "El token no tiene permiso sobre este número de WhatsApp"),
    # Not a Graph code: the HTTP status, kept as the pause's code when Meta answered 429 with no body.
    429: ("admin.system.status.wa.meta_error.429",
          "Meta frenó los envíos: demasiadas llamadas seguidas"),
    368: ("admin.system.status.wa.meta_error.368",
          "Meta bloqueó el número por un tiempo por incumplir sus políticas"),
    80007: ("admin.system.status.wa.meta_error.80007",
            "Meta frenó los envíos: la cuenta de WhatsApp pasó su límite de llamadas"),
    130429: ("admin.system.status.wa.meta_error.130429",
             "Meta frenó los envíos: el número pasó su límite de mensajes por segundo"),
    131000: ("admin.system.status.wa.meta_error.131000",
             "Error interno de Meta: el mensaje no salió"),
    131005: ("admin.system.status.wa.meta_error.131005",
             "Meta negó el acceso: el token no tiene permiso para mandar"),
    131008: ("admin.system.status.wa.meta_error.131008",
             "Al mensaje le falta un dato obligatorio"),
    131009: ("admin.system.status.wa.meta_error.131009",
             "Un dato del mensaje no es válido para Meta"),
    131016: ("admin.system.status.wa.meta_error.131016",
             "El servicio de WhatsApp de Meta no está disponible en este momento"),
    131021: ("admin.system.status.wa.meta_error.131021",
             "El destino es el mismo número que manda"),
    131026: ("admin.system.status.wa.meta_error.131026",
             "El mensaje no se pudo entregar: el número no usa WhatsApp o no acepta el mensaje"),
    131030: ("admin.system.status.wa.meta_error.131030",
             "El número no está en la lista de destinatarios de prueba de Meta"),
    131031: ("admin.system.status.wa.meta_error.131031",
             "Meta bloqueó la cuenta de WhatsApp del hospital"),
    131042: ("admin.system.status.wa.meta_error.131042",
             "Hay un problema con el medio de pago de la cuenta de WhatsApp"),
    131047: ("admin.system.status.wa.meta_error.131047",
             "Pasaron más de 24 horas desde el último mensaje del paciente: solo sale una plantilla aprobada"),
    131048: ("admin.system.status.wa.meta_error.131048",
             "Meta frenó los envíos: muchos mensajes del número fueron marcados como spam"),
    131049: ("admin.system.status.wa.meta_error.131049",
             "Meta decidió no entregar el mensaje para cuidar la experiencia del paciente"),
    131051: ("admin.system.status.wa.meta_error.131051",
             "Meta no admite este tipo de mensaje"),
    131052: ("admin.system.status.wa.meta_error.131052",
             "No se pudo bajar el archivo que mandó el paciente"),
    131053: ("admin.system.status.wa.meta_error.131053",
             "No se pudo subir el archivo del mensaje"),
    131056: ("admin.system.status.wa.meta_error.131056",
             "Meta frenó los envíos: demasiados mensajes seguidos al mismo paciente"),
    132000: ("admin.system.status.wa.meta_error.132000",
             "La cantidad de datos no coincide con la plantilla aprobada"),
    132001: ("admin.system.status.wa.meta_error.132001",
             "La plantilla no existe en ese idioma o todavía no está aprobada"),
    132005: ("admin.system.status.wa.meta_error.132005",
             "El texto de la plantilla con sus datos es demasiado largo"),
    132007: ("admin.system.status.wa.meta_error.132007",
             "El contenido de la plantilla no cumple las políticas de Meta"),
    132012: ("admin.system.status.wa.meta_error.132012",
             "Un dato de la plantilla no tiene el formato que espera"),
    132015: ("admin.system.status.wa.meta_error.132015",
             "Meta pausó la plantilla por baja calidad"),
    132016: ("admin.system.status.wa.meta_error.132016",
             "Meta deshabilitó la plantilla"),
    133010: ("admin.system.status.wa.meta_error.133010",
             "El número que manda no está registrado en la Cloud API"),
}
_META_MOTIVO_DESCONOCIDO = ("admin.system.status.wa.meta_error.unknown",
                            "Error de Meta sin causa conocida")


def meta_error_reason(code, lang=None):
    """La causa en palabras de un código de error de Meta (`lang` None = el idioma activo). Un código que no está
    en la lista da una frase genérica: el número va siempre al lado, en el log y en «Sistema»."""
    try:
        clave, es = _META_MOTIVOS.get(int(code), _META_MOTIVO_DESCONOCIDO)
    except (TypeError, ValueError):
        clave, es = _META_MOTIVO_DESCONOCIDO
    return i18n.t_or(clave, es, lang or i18n.active())


def _estado_path():
    """`<TURNOS_DATA_DIR or checkout/data>/private/meta-wa-state.json`, the process key read through settings."""
    from app.core import paths, settings
    base = settings.InstallationSettings.current().data_dir or os.path.join(paths.PROJECT_ROOT, "data")
    return os.path.join(os.path.abspath(base), "private", _ESTADO_ARCHIVO)


def _leer_estado(path):
    try:
        with open(path, encoding="utf-8") as f:
            estado = json.load(f)
        return estado if isinstance(estado, dict) else {}
    except (OSError, ValueError):
        return dict(_estado_memoria)


def meta_estado():
    """El estado compartido de los envíos por Meta: `pausa_hasta` (epoch), `pausa_codigo`, `pausa_fin_avisada`,
    el último error (`error_codigo`, `error_http`, `error_en`) y el último envío bueno (`ok_en`). {} si nunca
    pasó nada."""
    return _leer_estado(_estado_path())


def _actualizar_estado(cambiar):
    """Lee, cambia y guarda el estado bajo un candado entre procesos (`flock` sobre `<archivo>.lock`; el web y el
    worker escriben el mismo archivo) y uno entre hilos; escritura atómica. `cambiar(estado)` devuelve el dict de
    cambios (vacío o None = no escribe). Nunca levanta: sin volumen escribible queda en la memoria del proceso."""
    path = _estado_path()
    with _estado_lock:
        candado = None
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            candado = open(f"{path}.lock", "a+", encoding="utf-8")
            fcntl.flock(candado.fileno(), fcntl.LOCK_EX)
        except OSError:
            if candado is not None:
                candado.close()
                candado = None
        try:
            estado = _leer_estado(path)
            cambios = cambiar(estado) or {}
            if not cambios:
                return estado
            estado.update(cambios)
            _estado_memoria.clear()
            _estado_memoria.update(estado)
            try:
                tmp = f"{path}.{os.getpid()}.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(estado, f, sort_keys=True)
                os.replace(tmp, path)
            except OSError as exc:
                _log(f"estado de Meta sin guardar en disco: {log_pii.exc(exc)}")
            return estado
        finally:
            if candado is not None:
                candado.close()  # cerrar suelta el flock


def _numero(valor):
    try:
        return float(valor or 0)
    except (TypeError, ValueError):
        return 0.0


def _restante(estado):
    return max(0, math.ceil(_numero(estado.get("pausa_hasta")) - clock.utcnow().timestamp()))


def meta_pausa_restante():
    """Segundos que faltan de la pausa de Meta (0 = se puede mandar)."""
    return _restante(meta_estado())


def meta_pausa_reset():
    """Levanta la pausa (tests y una intervención manual)."""
    _actualizar_estado(lambda _e: {"pausa_hasta": 0, "pausa_fin_avisada": True})


def _retry_after(headers):
    """Segundos de `Retry-After` (número o fecha HTTP), o None si no vino o no se entiende."""
    try:
        valor = (headers.get("Retry-After") if headers is not None else None) or ""
    except Exception:  # noqa: BLE001
        return None
    valor = str(valor).strip()
    if not valor:
        return None
    try:
        segundos = float(valor)
    except ValueError:
        try:
            cuando = email.utils.parsedate_to_datetime(valor)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
        if cuando is None or cuando.tzinfo is None:
            return None
        segundos = (cuando - clock.utcnow()).total_seconds()
    if not math.isfinite(segundos) or segundos < 0:  # NaN, infinito o en el pasado
        return None
    return segundos


def _segundos_de_pausa(retry_after):
    if retry_after is None:
        return _PAUSA_SIN_RETRY_AFTER_S
    return max(1, min(_PAUSA_MAX_S, math.ceil(retry_after)))


def _pausa(segundos, codigo):
    """Los cambios que abren una pausa de `segundos`: un segundo freno durante la pausa nunca la acorta."""
    def cambiar(estado):
        hasta = clock.utcnow().timestamp() + segundos
        if hasta <= _numero(estado.get("pausa_hasta")):
            return {}
        return {"pausa_hasta": hasta, "pausa_codigo": codigo, "pausa_fin_avisada": False}
    return cambiar


def _abrir_pausa(segundos, codigo):
    _actualizar_estado(_pausa(segundos, codigo))
    _log(f"Meta frenó los envíos (código {codigo}): pausa de toda salida por Meta por {segundos} s")


def _registrar_error(codigo, http, pausa_s):
    """El último error y, si es un freno, la pausa: una sola escritura bajo el candado."""
    abrir = _pausa(pausa_s, codigo if codigo is not None else http) if pausa_s else None

    def cambiar(estado):
        cambios = {"error_codigo": codigo, "error_http": http, "error_en": clock.utcnow().timestamp()}
        if abrir is not None:
            cambios.update(abrir(estado))
        return cambios
    _actualizar_estado(cambiar)


def _meta_leer_error(exc):
    """El código de error del cuerpo de un HTTPError de Graph, como entero o None. Siempre cierra el error."""
    try:
        try:
            cuerpo = json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            return None
        error = cuerpo.get("error") if isinstance(cuerpo, dict) else None
        if not isinstance(error, dict):
            return None
        texto = log_pii.provider_code(error.get("code"))
        return int(texto) if texto else None
    finally:
        exc.close()


def _marcar_envio_ok(estado):
    """Un envío aceptado después de un error: «Sistema» deja de mostrar ese error en rojo (el token ya se
    arregló, la cuenta volvió); el primer envío tras una pausa vencida deja el aviso de fin en el log. Escribe
    sólo cuando hay algo que cambiar (el caso de todos los días no toca el disco)."""
    pendiente_error = _numero(estado.get("error_en")) > _numero(estado.get("ok_en"))
    pendiente_fin = bool(estado.get("pausa_hasta")) and not estado.get("pausa_fin_avisada")
    if not (pendiente_error or pendiente_fin):
        return

    avisar = []

    def cambiar(actual):
        cambios = {}
        if _numero(actual.get("error_en")) > _numero(actual.get("ok_en")):
            cambios["ok_en"] = clock.utcnow().timestamp()
        if actual.get("pausa_hasta") and not actual.get("pausa_fin_avisada") and _restante(actual) == 0:
            cambios["pausa_fin_avisada"] = True
            avisar.append(True)  # this process flipped it: only one log line across processes
        return cambios
    _actualizar_estado(cambiar)
    if avisar:
        _log("terminó la pausa de Meta — los envíos siguen")


def _meta_post(destino, mensaje, etiqueta):
    """El único POST a /messages: pausa, envío, motivo del error y señal de lanzamiento. Nunca levanta."""
    req = _meta_request(destino, mensaje)
    if req is None:
        return False
    estado = meta_estado()
    restante = _restante(estado)
    if restante > 0:
        # Ni la red ni la señal de lanzamiento: Meta no respondió por este mensaje (lo frenamos nosotros).
        _log(f"{etiqueta} en pausa por un freno de Meta ({restante} s más) — no se envió a {_mask_phone(destino)}")
        return False
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read().decode("utf-8") or "{}")
            msg_id = ((result.get("messages") or [{}])[0]).get("id")
            ok = 200 <= r.status < 300 and bool(msg_id)
            _log(f"{etiqueta} -> {_mask_phone(destino)} HTTP {r.status} "
                 f"({'ok' if ok else 'sin message id'})")
            if ok:
                _marcar_envio_ok(estado)
            _signal_whatsapp(ok)
            return ok
    except urllib.error.HTTPError as exc:
        # HTTPError holds the response body: _meta_leer_error always closes it (ResourceWarning otherwise).
        codigo = _meta_leer_error(exc)
        retry = _retry_after(exc.headers)
        limite = exc.code == 429 or codigo in _META_CODIGOS_LIMITE or retry is not None
        pausa_s = _segundos_de_pausa(retry) if limite else 0
        _registrar_error(codigo, exc.code, pausa_s)
        # Only the sanitized number reaches the log (test_logs_pii's static gate), never a value read off exc.
        numero = log_pii.provider_code(codigo if codigo is not None else "")
        motivo = meta_error_reason(numero, "es") if numero else ""
        _log(f"{etiqueta} FAILED -> {_mask_phone(destino)}: {log_pii.exc(exc)} {numero}".rstrip()
             + (f" — {motivo}" if motivo else ""))
        if limite:
            _log(f"Meta frenó los envíos (código {numero or log_pii.provider_code(exc.code)}): "
                 f"pausa de toda salida por Meta por {meta_pausa_restante()} s")
        _signal_whatsapp(False)
        return False
    except Exception as exc:  # noqa: BLE001 — best-effort, nunca rompe el caller
        _log(f"{etiqueta} FAILED -> {_mask_phone(destino)}: {type(exc).__name__}")
        _signal_whatsapp(False)
        return False


def _enviar_meta(destino, texto):
    """Mensaje de texto LIBRE por la WhatsApp Cloud API de Meta — válido
    sólo dentro de la ventana de servicio de 24h que abre el paciente al
    escribir primero (nunca lo usamos para iniciar una conversación, así que
    no hace falta una plantilla aprobada). Best-effort: nunca levanta."""
    return _meta_post(destino, {"type": "text", "text": {"body": texto}}, "Meta WhatsApp")


def _param_plantilla(valor):
    """Un parámetro de plantilla tal como Meta lo acepta: sin saltos de línea
    ni tabs, sin más de cuatro espacios seguidos, nunca vacío (error 131008/
    132000 si se rompe cualquiera de las tres) y con un tope de largo para que
    el cuerpo entero no pase los 1024 caracteres."""
    t = re.sub(r"\s+", " ", str(valor or "")).strip()
    if len(t) > 300:
        t = t[:299].rstrip() + "…"
    return t or "-"


def _enviar_meta_plantilla(destino, nombre, idioma, params):
    """Mensaje de PLANTILLA aprobada por la Cloud API de Meta — lo único que
    Meta entrega cuando lo inicia el hospital (confirmación, recordatorio) y
    el paciente no escribió en las últimas 24 h. `params` llena el cuerpo en
    orden ({{1}}, {{2}}, …). Los botones de respuesta rápida de la plantilla
    no llevan parámetros: el texto del botón tocado vuelve por el webhook
    como un mensaje más. Best-effort: nunca levanta."""
    componentes = [{"type": "body", "parameters": [
        {"type": "text", "text": _param_plantilla(p)} for p in (params or [])]}]
    return _meta_post(destino, {"type": "template",
                                "template": {"name": nombre, "language": {"code": idioma},
                                             "components": componentes}},
                      f"Meta WhatsApp plantilla {nombre}")


def otp_plantilla():
    """Nombre de la plantilla de Authentication aprobada para el código (`META_WA_OTP_TEMPLATE`), o ""."""
    return (config.get_text("META_WA_OTP_TEMPLATE") or "").strip()


def _enviar_otp_meta(destino, code):
    """El código de verificación por la plantilla de Authentication aprobada en el WABA (`META_WA_OTP_TEMPLATE`,
    castellano rioplatense; botón «Copiar código»). Meta arma el texto: el cuerpo lleva el código como único
    dato y el botón lo lleva otra vez (lo exige la plantilla de copiar código). Sin la variable, no-op logueado:
    un texto libre con un código Meta no lo entrega fuera de la ventana de 24 h ni lo permite su política."""
    nombre = otp_plantilla()
    if not nombre:
        _log(f"OTP_PROVIDER='meta' sin plantilla de Authentication (META_WA_OTP_TEMPLATE) — no-op, "
             f"no se envió a {_mask_phone(destino)}")
        return False
    codigo = re.sub(r"\s+", "", str(code or ""))
    componentes = [
        {"type": "body", "parameters": [{"type": "text", "text": codigo}]},
        {"type": "button", "sub_type": "url", "index": "0",
         "parameters": [{"type": "text", "text": codigo}]},
    ]
    return _meta_post(destino, {"type": "template",
                                "template": {"name": nombre, "language": {"code": PLANTILLA_IDIOMA},
                                             "components": componentes}},
                      f"Meta WhatsApp código ({nombre})")


_MD_HEADING_RE = re.compile(r"^(#{1,3})[ \t]+(.+)$", re.MULTILINE)
# Bullet + bold, the shape the model uses for an INLINE catalog item:
#   "...agosto: * **Cardiología** en el Hospital... * **Oftalmología** ..."
# Applied only to lines that hold 2+ such items (see _reparar_listas_inline)
# and BEFORE plain `**x**` -> `*x*`, or we leave a stray bullet asterisk
# (`* *Cardiología*`) that WhatsApp shows as a literal star stuck to the bold.
_MD_BULLET_BOLD_RE = re.compile(r"(?m)(?:^|(?<=\s))\*\s+\*\*(\S(?:[^*]*\S)?)\*\*")
# Same shape after a previous partial conversion (`* *Cardiología*`).
_MD_BULLET_BOLD_WA_RE = re.compile(r"(?m)(?:^|(?<=\s))\*\s+\*(\S(?:[^*]*\S)?)\*")
_MD_BOLD_RE = re.compile(r"\*\*(\S(?:[^*]*\S)?)\*\*")
_MD_UNDERLINE_RE = re.compile(r"__(\S(?:[^_]*\S)?)__")
_MD_STRIKE_RE = re.compile(r"~~(\S(?:[^~]*\S)?)~~")
_MD_BULLET_RE = re.compile(r"^([ \t]*)\*[ \t]+", re.MULTILINE)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^\s)]+)\)")
# A "- *X*" item that still shares a line -> starts on its own line so
# WhatsApp renders the dash bullet. Applied per line inside the 2+ guard.
_MD_INLINE_DASH_RE = re.compile(r"(?<!\n)\s+(-\s+\*)")
# One inline bullet item in ANY of the three shapes the model emits: a bullet
# marker (`*` or `-`) + whitespace + an emphasized span. Used ONLY to count
# items on a line -- the >= 2 count is the structural signal that separates a
# genuine inline catalog from a lone arithmetic `2 * **3**` or a prose em-dash
# `08:00 - *sujeto a disponibilidad*`, both of which carry exactly one.
_MD_LIST_ITEM_RE = re.compile(r"(?:^|(?<=\s))(?:\*\s+\*\*\S|\*\s+\*\S|-\s+\*\S)")


def _reparar_listas_inline(out):
    """Rebuild INLINE catalog bullet lists the model crams onto a single line
    into one-item-per-line dash lists, which is what WhatsApp renders.

    Structural guard against false positives (Codex review): a line is treated
    as a list ONLY when it holds TWO OR MORE bullet items. A single inline
    bullet is ambiguous -- arithmetic (`2 * **3** = 6`) and a prose em-dash
    before an emphasized note (`Horario 08:00 - *sujeto a disponibilidad*`)
    each carry exactly one bullet-shaped token and must NOT become lists.
    Genuine model catalogs are multi-item by definition, so the per-line count
    is the narrow, structural signal. Proper one-bullet-per-line Markdown lists
    are left alone here -- the line-start bullet step further down handles
    those (a single inline item that is not a list falls through to the normal
    bold conversion, never to a dash list).
    """
    def _por_renglon(m):
        renglon = m.group(0)
        if len(_MD_LIST_ITEM_RE.findall(renglon)) < 2:
            return renglon
        renglon = _MD_BULLET_BOLD_RE.sub(r"- *\1*", renglon)
        renglon = _MD_BULLET_BOLD_WA_RE.sub(r"- *\1*", renglon)
        renglon = _MD_INLINE_DASH_RE.sub(r"\n\1", renglon)
        return renglon
    return re.sub(r"[^\n]+", _por_renglon, out)


def _markdown_a_whatsapp(texto):
    """Convierte el Markdown que escribe el modelo a la sintaxis de WhatsApp.

    WhatsApp usa UN solo delimitador para negrita (`*x*`), no dos — mandarle
    `**x**` tal cual lo deja renderizado en negrita con un asterisco de más
    pegado a cada lado (the product owner, 2026-08-06: "you are sending double stars,
    bolds to WhatsApp"). Éste es el único choke point de texto libre saliente
    (`enviar_texto`), así que arreglarlo acá alcanza para todo lo que manda
    el asistente, no sólo el chat.

    Idempotente: un texto que YA está en formato WhatsApp (`*x*` simple) pasa
    sin tocarse, y aplicar la función dos veces da el mismo resultado — cada
    regex de abajo deja de matchear su propia salida (un asterisco simple no
    es `**`, un renglón que ya empieza con "- " no es "* ").
    """
    out = str(texto or "")
    # 1) Encabezados Markdown -> negrita de WhatsApp.
    out = _MD_HEADING_RE.sub(lambda m: "*" + m.group(2).strip() + "*", out)
    # 2) INLINE catalog bullet lists (2+ items crammed onto one line) ->
    #    one dash item per line. Narrow by construction: a single inline
    #    bullet (arithmetic, a prose em-dash) is left for the steps below.
    out = _reparar_listas_inline(out)
    # 3) Remaining double delimiter -> single.
    out = _MD_BOLD_RE.sub(r"*\1*", out)
    out = _MD_UNDERLINE_RE.sub(r"_\1_", out)
    out = _MD_STRIKE_RE.sub(r"~\1~", out)
    # 4) Markdown "* " bullet at the start of a line -> "- " (WhatsApp renders
    #    dashes; a bare "*" there reads as a half-open broken bold).
    out = _MD_BULLET_RE.sub(r"\1- ", out)
    # 5) Links: absolute destination -> "label: url" (so WhatsApp builds the
    #    link, which only detects bare URLs); relative destination (an app
    #    route, e.g. /mis-solicitudes) is useless outside the browser -> label only.
    def _link(m):
        label, url = m.group(1), m.group(2)
        if re.match(r"^https?://", url, re.IGNORECASE):
            return f"{label}: {url}"
        return label
    out = _MD_LINK_RE.sub(_link, out)
    return out


def wa_provider():
    """Qué transporte usa enviar_texto(). Contrato de transporte (docstring
    del módulo): "meta" con `TURNOS_WA_PROVIDER=meta` explícito o, sin
    preferencia explícita, cuando hay META_WA_TOKEN; "waha" SOLO con
    `TURNOS_WA_PROVIDER=waha` explícito Y una OTP_WAHA_URL de loopback (el
    opt-in de desarrollo); "" en cualquier otro caso — incluida una URL de
    WAHA remota, que NO habilita nada. Nunca auto-detecta WAHA: sin proveedor
    el resultado es no-op, jamás un fallback silencioso a un transporte no
    oficial. Expuesto para que whatsapp_chat.py y system_status.py puedan
    loguear/mostrar la elección sin adivinarla de nuevo."""
    v = (config.get_text("TURNOS_WA_PROVIDER") or "").strip().lower()
    if v == "meta":
        return "meta"
    if v == "waha":
        return "waha" if _waha_es_loopback(config.get_text("OTP_WAHA_URL")) else ""
    if config.get_text("META_WA_TOKEN"):
        return "meta"
    return ""


def enviar_texto(destino, texto):
    """Manda un texto de WhatsApp LIBRE (no un OTP) — lo usa whatsapp_chat.py
    para las respuestas del asistente de turnos. El transporte lo decide
    wa_provider() (Meta Cloud API o WAHA); mismo contrato best-effort en
    ambos: nunca levanta, False si no está configurado o si falla el envío.
    No pasa por OTP_PROVIDER (eso es sólo para el código de verificación).
    El texto pasa por `_markdown_a_whatsapp` antes de salir por cualquiera de
    los dos transportes — el modelo escribe Markdown (`**negrita**`) y acá es
    donde se traduce a lo que WhatsApp realmente interpreta."""
    if demo_installation.enabled():
        _log("modo demo — salida de WhatsApp bloqueada")
        return False
    texto = _markdown_a_whatsapp(texto)
    provider = wa_provider()
    if provider == "meta":
        return _enviar_meta(destino, texto)
    if provider == "waha":
        return _enviar_whatsapp(destino, texto)
    _log(f"ningún proveedor de WhatsApp configurado (Meta/WAHA) — no-op, "
        f"no se envió a {_mask_phone(destino)}")
    return False


_TRUE = {"1", "true", "yes", "on"}


def _destinatarios_prueba():
    """Teléfonos de `TURNOS_WA_TEST_RECIPIENTS` (separados por coma), con la
    misma normalización que el envío (`format.tel_wa`)."""
    crudo = config.get_text("TURNOS_WA_TEST_RECIPIENTS")
    return {d for d in (_wa_digits(p) for p in crudo.split(",") if p.strip()) if d}


def notificacion_permitida(destino):
    """¿Puede salir un aviso del HOSPITAL (confirmación, recordatorio,
    solución a un rechazo) a este teléfono? Distinto de las respuestas del
    chat: ésas las abre el paciente al escribir; éstas las inicia el sistema,
    así que necesitan permiso explícito de la instalación.

    - `TURNOS_WA_NOTIFY=1` las enciende; sin eso no sale ninguna (default:
      apagado — Pinamar no tiene todavía número propio ni el acuerdo del
      municipio para escribirle a los pacientes).
    - `TURNOS_WA_TEST_RECIPIENTS` (lista de teléfonos) las restringe a esos
      números: cualquier otro destino es un no-op logueado.
    - En la demo la lista es OBLIGATORIA: sus pacientes son ficticios y un
      número inventado puede ser el de una persona real. Demo sin lista = no
      sale nada."""
    if (config.get_text("TURNOS_WA_NOTIFY") or "").lower() not in _TRUE:
        return False
    prueba = _destinatarios_prueba()
    if demo_installation.enabled() and not prueba:
        return False
    if prueba and _wa_digits(destino) not in prueba:
        return False
    return True


# Plantillas Utility aprobadas en el WABA de la instalación (mismo nombre en
# cada instalación; el texto y los {{n}} están en docs/architecture/
# notificaciones-y-lista-espera.md). Castellano rioplatense.
PLANTILLA_CONFIRMACION = "turnos_confirmacion"
PLANTILLA_RECORDATORIO = "turnos_recordatorio"
PLANTILLA_IDIOMA = "es_AR"


def enviar_notificacion(destino, texto, plantilla=None, params=None):
    """Aviso iniciado por el hospital. Por Meta sale como la `plantilla`
    aprobada (con `params`), porque un texto libre fuera de la ventana de 24 h
    Meta lo rechaza; por WAHA (desarrollo en loopback) o sin plantilla sale
    `texto`. Gate: `notificacion_permitida`. Nunca levanta; False si no salió.

    No pasa por el bloqueo de demo de `enviar_texto`: en la demo el permiso
    lo da la lista de destinatarios de prueba, obligatoria ahí."""
    try:
        if not notificacion_permitida(destino):
            _log(f"aviso de WhatsApp no habilitado para {_mask_phone(destino)} "
                 f"(TURNOS_WA_NOTIFY / TURNOS_WA_TEST_RECIPIENTS) — no-op")
            return False
        provider = wa_provider()
        if provider == "meta" and plantilla:
            return _enviar_meta_plantilla(destino, plantilla, PLANTILLA_IDIOMA, params or [])
        texto = _markdown_a_whatsapp(texto)
        if provider == "meta":
            return _enviar_meta(destino, texto)
        if provider == "waha":
            return _enviar_whatsapp(destino, texto)
        _log(f"ningún proveedor de WhatsApp configurado (Meta/WAHA) — no-op, "
             f"no se envió a {_mask_phone(destino)}")
        return False
    except Exception as exc:  # noqa: BLE001
        _log(f"aviso de WhatsApp FAILED -> {_mask_phone(destino)}: {type(exc).__name__}")
        return False


def otp_provider():
    """El proveedor del código (`OTP_PROVIDER`, default whatsapp), como lo elige `enviar_otp`: «Sistema» lee
    éste, nunca su propia lectura de la variable."""
    return (config.get_text("OTP_PROVIDER", "whatsapp") or "whatsapp").strip().lower() or "whatsapp"


def enviar_otp(destino, code):
    """Envía el código OTP al `destino` (teléfono) por el proveedor activo
    (env OTP_PROVIDER, default whatsapp). Devuelve True/False, nunca levanta.
    accounts.py llama esto SIN importar el proveedor concreto — cambiar de
    WhatsApp a SMS pago el día de mañana es sólo esta función, cero cambios
    en accounts.py ni en el schema de otp_codigos.

    `OTP_PROVIDER=meta` manda el código SOLO por la plantilla de
    Authentication aprobada que nombra `META_WA_OTP_TEMPLATE`
    (`_enviar_otp_meta`); sin esa variable es un no-op logueado, como antes.
    Un OTP_PROVIDER desconocido tampoco cae más a WAHA — no-op logueado."""
    if demo_installation.enabled():
        _log("modo demo — envío de OTP bloqueado")
        return False
    provider = otp_provider()
    if provider == "meta":
        return _enviar_otp_meta(destino, code)
    fn = _PROVIDERES.get(provider)
    if fn is None:
        _log(f"OTP_PROVIDER={provider!r} no soportado (proveedor desconocido) — no-op, "
             f"no se envió a {_mask_phone(destino)}")
        return False
    return fn(destino, _mensaje(code))
