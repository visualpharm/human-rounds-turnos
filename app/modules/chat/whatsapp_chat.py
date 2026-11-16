"""whatsapp_chat.py — canal de WhatsApp del asistente de turnos (mismo motor
que el widget del sitio, chat_agent.py). Dos proveedores empujan mensajes
al MISMO webhook, `/api/whatsapp/webhook`: WAHA (self-hosted, en uso) y la
WhatsApp Cloud API oficial de Meta (responsable del producto probando con su número de test,
2026-07-23) — el envío tiene su propio seam de proveedor en otp_send.py
(`TURNOS_WA_PROVIDER`); acá sólo hace falta reconocer y normalizar los DOS
formatos de entrada. Identidad = el TELÉFONO del remitente matcheado contra
usuarios.contacto (db_chat.user_by_phone) — escribir desde el número de la
cuenta ES la autorización, mismo espíritu que el token del feed ICS o el
Bearer del MCP, sólo que acá la "credencial" es el aparato.

Rutas nativas de este módulo, registradas por ``whatsapp_chat_router.py``:
  GET  /api/whatsapp/webhook   handshake de verificación de Meta
  POST /api/whatsapp/webhook   evento entrante (WAHA o Meta, se distingue por forma)

Auth del webhook: dos materiales, uno por proveedor. Sin NINGUNO de los dos
seteado el router devuelve el 404 terminal, nunca queda abierto por accidente.
  POST (Meta): `X-Hub-Signature-256: sha256=<hmac>` — HMAC-SHA256 del cuerpo
        crudo con `META_WA_APP_SECRET` (el app secret del panel de Meta).
        Es lo ÚNICO que Meta manda: no manda `X-Webhook-Secret` ni
        `Authorization: Bearer`, así que antes de esto un POST suyo sólo
        pasaba con un `?secret=` pegado a la callback URL — el secreto
        compartido a la vista en el panel y en los logs de cada proxy.
  POST (WAHA): secreto compartido `TURNOS_WA_WEBHOOK_SECRET` en el header
        `X-Webhook-Secret`, `Authorization: Bearer <secret>`, o `?secret=` —
        WAHA permite configurar cualquiera según la versión. Las tres formas
        siguen aceptadas para cualquier proveedor: la firma de Meta se suma,
        no reemplaza (una instalación con WAHA loopback no tiene app secret).
  GET  (Meta, sólo en el alta de la suscripción del webhook en su panel):
        `?hub.mode=subscribe&hub.verify_token=<secret>&hub.challenge=<n>` —
        si `hub.verify_token` coincide, respondemos el `hub.challenge` tal
        cual (200 text/plain); si no, 403. Meta exige este handshake antes
        de aceptar la URL como webhook — WAHA no lo usa (nunca manda GET acá).

Los dos proveedores llegan con formas MUY distintas; se normalizan ANTES de
`_procesar_mensaje` a un único shape interno
  {"from": "<dígitos, sin sufijo>", "from_me": bool, "is_group": bool,
   "body": "<texto>", "media": None | {"provider": "waha"|"meta", ...}}
así el resto del módulo (traspaso, motor de chat, respuesta) no vuelve a
mirar de dónde vino el mensaje. `_es_meta(body)` distingue por forma: Meta
siempre manda `{"object": "whatsapp_business_account", "entry": [...]}` en
la raíz; WAHA no.

Traspaso desde el chat del sitio (responsable del producto, 2026-07-23): un mensaje que contenga
un código "ST-" + 12 chars (chat_api.py lo ofrece como link wa.me con el texto
prellenado) consume ese código (db_chat.chat_handoff_use) y copia el historial
de esa conversación WEB a la conversación de WhatsApp — así la persona no
tiene que repetir lo que ya escribió. Intentos de código inválido se limitan
por teléfono (anti-enumeración). `_CODE_RE` acepta 6-12 chars (no sólo 12):
un código de 6 emitido ANTES de la ronda 2026-07-23 (db_chat._generate_handoff_code
lengthened 6->12, ~30->~60 bits de entropía) sigue bindeando mientras esté
vigente (TTL 48h) — no rompemos códigos ya en vuelo por endurecer el formato.

Autorización vía código (responsable del producto, 2026-08-02: "si ya estoy logueado en la web
no me pidas de nuevo mi identidad por WhatsApp"): redimir un código emitido
por una sesión LOGUEADA ("dni:<dni>...", nunca uno de una conversación
anónima "ip:...") también autoriza reservas desde ese teléfono, no sólo
copia texto — `db_chat.user_by_handoff_phone` resuelve esto, con una ventana
propia después de la redención (`TURNOS_WA_HANDOFF_GRANT_DAYS`, default 30
días) para no repetir el código en cada mensaje siguiente. Es un fallback:
el match directo por TELÉFONO contra `users.contact` (más abajo) sigue
siendo la vía primaria y no cambia. El código sigue siendo de un solo uso y
con el mismo límite anti-enumeración de intentos inválidos.

Arribo narrado (ronda 2, 2026-07-23, responsable del producto): al redimir un código con éxito
YA NO se contesta con un saludo fijo — se corre el MOTOR para ese primer
mensaje con una instrucción tejida en el texto (mismo patrón que el path de
media más abajo: la instrucción se resuelve ANTES de llamar al motor) para
que el modelo salude, resuma en una línea dónde quedó la conversación y
repita la última pregunta pendiente — el modelo YA tiene el historial recién
copiado en el contexto, así que puede resumirlo de verdad en vez de repetir
un texto genérico. Un código inválido/vencido sigue siendo un aviso de texto
fijo (no hay nada que narrar: no hubo traspaso). Un teléfono NO registrado
sigue recibiendo únicamente la invitación a registrarse — el traspaso nunca
concede autorización de reserva, regla sin tocar.

Media (foto/PDF de una orden): WAHA trae una URL de descarga directa
(payload.media.url, mismo X-Api-Key que otp_send.py usa para los OTP); Meta
en cambio manda sólo un `id` de media — hace falta un paso extra
(GET /<media_id> con Bearer -> {"url":...}, después GET esa url con el MISMO
Bearer -> los bytes). Ambos casos terminan pasando por el MISMO pipeline que
analyze_order (chat_tools.analizar_orden_para, con su enfriamiento por
identidad "tel:<tel>"), anotando el resultado como contexto en el texto que
ve el modelo — ninguno de los dos proveedores tiene un "tool call" nativo
para adjuntos, así que el adjunto se resuelve ANTES de invocar al motor.

Import-safe: nunca tumba el arranque del server. Best-effort en cada paso —
el webhook SIEMPRE responde 200 rápido (Meta reintenta ante cualquier
respuesta que no sea 2xx; WAHA también); el procesamiento real es best-effort
después de ese 200 lógico (no async de verdad — corre inline, pero ningún
paso levanta hacia afuera)."""

import re
from app.core import config
from app.core import log_pii
import sys
import time
import hmac
import hashlib
import json
import urllib.request
import urllib.error

from app.core.ids import digits as _digits

_CODE_RE = re.compile(r"\bST-[A-Z0-9]{6,12}\b", re.I)

_INTENTOS_FALLIDOS = {}   # tel -> [epoch, ...] de códigos inválidos recientes
_INTENTOS_VENTANA = 3600

_MEDIA_TIPOS = ("image", "document", "audio", "video", "sticker")

# Webhook envelopes carry metadata and provider media references, never the
# media bytes themselves. `whatsapp_chat_router.py` enforces this explicit
# bound so ASGI cannot buffer an arbitrarily large request.
MAX_WEBHOOK_BODY_BYTES = 1024 * 1024


def _env(key, default=""):
    return config.get(key, default)


_int_env = config.get_int


def _log(msg):
    print(f"[whatsapp_chat] {msg}", file=sys.stderr, flush=True)


def _secret():
    return _env("TURNOS_WA_WEBHOOK_SECRET")


def _app_secret():
    """El app secret de Meta — el ÚNICO material con el que Meta autentica sus
    POSTs, vía `X-Hub-Signature-256`. Meta no manda `X-Webhook-Secret` ni
    `Authorization: Bearer` nunca, así que sin esto la única forma de que un
    POST suyo pase `_secret_ok_headers` es un `?secret=` pegado a la callback
    URL — que deja el secreto compartido escrito en el panel de Meta y en los
    logs de acceso de cualquier proxy del camino."""
    return _env("META_WA_APP_SECRET")


def webhook_enabled():
    """El endpoint existe si hay ALGÚN material de autenticación: el secreto
    compartido (WAHA, y el verify token del handshake GET de Meta) o el app
    secret de Meta. Sin ninguno de los dos -> 404, fail closed como siempre:
    el webhook nunca queda abierto por descuido."""
    return bool(_secret() or _app_secret())


def meta_signature_ok(raw, headers):
    """True si `X-Hub-Signature-256` es el HMAC-SHA256 del cuerpo CRUDO con el
    app secret. Sobre los bytes tal como llegaron: reserializar el JSON cambia
    la firma, así que el caller pasa el body sin tocar. Fail closed — sin app
    secret configurado, sin header, o con un prefijo que no sea `sha256=`,
    devuelve False y el POST cae al secreto compartido o a 401."""
    secreto = _app_secret()
    recibido = (headers.get("X-Hub-Signature-256") or "").strip()
    if not secreto or not recibido.lower().startswith("sha256="):
        return False
    esperado = hmac.new(secreto.encode("utf-8"), raw or b"", hashlib.sha256).hexdigest()
    return hmac.compare_digest(recibido[len("sha256="):].lower(), esperado)


def _secret_ok_headers(headers, qs):
    secreto = _secret()
    if not secreto:
        return False
    recibido = headers.get("X-Webhook-Secret") or ""
    if not recibido:
        auth = headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            recibido = auth[7:].strip()
    if not recibido:
        recibido = (qs.get("secret") or [""])[0]
    return bool(recibido) and hmac.compare_digest(recibido, secreto)


def _declared_body_too_large(headers):
    """Return whether a valid declared body size exceeds the shared limit."""
    try:
        return int(headers.get("Content-Length") or 0) > MAX_WEBHOOK_BODY_BYTES
    except (TypeError, ValueError):
        return False


def _es_grupo(wa_id):
    return str(wa_id or "").endswith("@g.us")


def _tel_de(wa_id):
    """'5492255xxxxxx@c.us' -> '5492255xxxxxx' (sólo dígitos)."""
    return _digits(str(wa_id or "").split("@")[0])


def _sitio_url():
    from app.core import site_identity
    return site_identity.base_url()


def _mensaje_sin_cuenta():
    """Reply to a number with no account. Without TURNOS_PUBLIC_ORIGIN the
    registration link would point at this machine, so it is left out."""
    from app.core import theme
    from app.core import site_identity
    link = "" if site_identity.origin_is_loopback() else f" en {_sitio_url()}/account"
    return (f"No encontramos ninguna cuenta de {theme.brand_name()} con este número. "
            f"Para pedir un turno por acá primero registrate con este mismo "
            f"teléfono{link} — es gratis y rápido.")


# ---- verificación GET de Meta (alta del webhook en su panel) -----------------

def meta_verification_result(qs):
    """Return the provider-visible status and body for Meta's handshake."""
    modo = (qs.get("hub.mode") or [""])[0]
    token = (qs.get("hub.verify_token") or [""])[0]
    challenge = (qs.get("hub.challenge") or [""])[0]
    secreto = _secret()
    if modo == "subscribe" and secreto and token and hmac.compare_digest(token, secreto):
        return 200, challenge
    return 403, ""


# ---- normalización de los dos formatos de entrada ----------------------------

def _es_meta(body):
    """Meta siempre trae `object: 'whatsapp_business_account'` en la raíz;
    WAHA nunca lo manda — es la única distinción de forma que hace falta."""
    return isinstance(body, dict) and body.get("object") == "whatsapp_business_account"


def _normalizar_waha(body):
    """[{...normalizado...}] — WAHA manda UN mensaje por webhook call."""
    evento = str(body.get("event") or "")
    if evento and not evento.startswith("message"):
        return []
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else body
    wa_id = payload.get("from") or payload.get("chatId") or ""
    media = None
    if payload.get("hasMedia"):
        m = payload.get("media") if isinstance(payload.get("media"), dict) else {}
        url = m.get("url") or payload.get("mediaUrl")
        if url:
            media = {"provider": "waha", "url": url, "mime_type": m.get("mimetype"),
                     "filename": m.get("filename")}
    return [{
        "from": _tel_de(wa_id),
        "from_me": bool(payload.get("fromMe")),
        "is_group": _es_grupo(wa_id),
        "body": str(payload.get("body") or "").strip(),
        "media": media,
    }]


def _normalizar_meta(body):
    """[{...normalizado...}, ...] — un webhook de Meta puede traer varios
    `entry`/`changes`, y cada `changes[].value` puede traer varios mensajes.
    Sólo se toman los `changes` con campo "messages" — un `value` con sólo
    "statuses" (delivered/read/sent de un mensaje NUESTRO) se ignora entero,
    y dentro de "messages" cualquier entrada sin "from" también (forma
    inesperada = mejor ignorar que adivinar)."""
    out = []
    for entry in body.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            for msg in value.get("messages") or []:
                tel = _digits(msg.get("from"))
                if not tel:
                    continue
                tipo = msg.get("type")
                texto, media = "", None
                if tipo == "text":
                    texto = str((msg.get("text") or {}).get("body") or "").strip()
                elif tipo == "button":
                    # Quick reply of a template (the day-before reminder's
                    # «Sí, voy» / «No puedo ir»): its text is the answer.
                    texto = str((msg.get("button") or {}).get("text") or "").strip()
                elif tipo in _MEDIA_TIPOS:
                    info = msg.get(tipo) or {}
                    media = {"provider": "meta", "media_id": info.get("id"),
                             "mime_type": info.get("mime_type"),
                             "filename": info.get("filename")}
                    texto = str(info.get("caption") or "").strip()
                out.append({
                    "from": tel,
                    "from_me": False,   # Cloud API no ecoa mensajes propios acá
                    "is_group": False,  # no existen chats de grupo en Cloud API
                    "body": texto,
                    "media": media,
                })
    return out


# ---- descarga de media (un backend por proveedor) ---------------------------

def _descargar_media_waha(url):
    """Baja el media de la instancia WAHA propia -> (bytes, mime) o
    (None, None). Best-effort: algunas versiones de WAHA sirven la url con el
    mismo X-Api-Key, otras la sirven pública — probamos con el header, y si
    igual falla, sin él."""
    from app.notifications import otp_send
    _waha_url, key, _session = otp_send.waha_config()
    intentos = [{"X-Api-Key": key}] if key else []
    intentos.append({})
    for headers in intentos:
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.read(), r.headers.get("Content-Type")
        except Exception as e:  # noqa: BLE001
            # Never the headers (X-Api-Key) nor the url (its query can be a
            # signed token): which attempt failed, and why.
            _log(f"no se pudo bajar el media WAHA ({'con' if headers else 'sin'} "
                 f"X-Api-Key) {log_pii.text(url.split('?', 1)[0])}: {log_pii.exc(e)}")
    return None, None


def _descargar_media_meta(media_id):
    """Dos pasos, Bearer META_WA_TOKEN en ambos: GET /<media_id> -> {"url":...}
    -> GET esa url -> los bytes. (None, None) ante cualquier fallo."""
    token = _env("META_WA_TOKEN")
    version = (_env("META_WA_GRAPH_VERSION", "v25.0") or "v25.0").lstrip("/")
    if not token or not media_id:
        return None, None
    meta_url = f"https://graph.facebook.com/{version}/{media_id}"
    req = urllib.request.Request(meta_url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            info = json.loads(r.read().decode("utf-8") or "{}")
    except Exception as e:  # noqa: BLE001
        _log(f"no se pudo resolver el media {media_id!r} de Meta: {log_pii.exc(e)}")
        return None, None
    url = info.get("url")
    if not url:
        return None, None
    req2 = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req2, timeout=15) as r:
            return r.read(), r.headers.get("Content-Type")
    except Exception as e:  # noqa: BLE001
        _log(f"no se pudo bajar el media {media_id!r} de Meta: {log_pii.exc(e)}")
        return None, None


def _descargar_media(media):
    if media.get("provider") == "meta":
        return _descargar_media_meta(media.get("media_id"))
    return _descargar_media_waha(media.get("url"))


# ---- traspaso desde el chat del sitio (código ST-XXXXXXXXXXXX) ---------------

def _extraer_code(texto):
    m = _CODE_RE.search(str(texto or "").upper())
    return m.group(0) if m else None


def _intento_permitido(tel):
    now = time.time()
    limite = _int_env("TURNOS_WA_HANDOFF_PER_HOUR", 8)
    xs = [t for t in _INTENTOS_FALLIDOS.get(tel, []) if now - t < _INTENTOS_VENTANA]
    _INTENTOS_FALLIDOS[tel] = xs
    return len(xs) < limite


def _marcar_intento_fallido(tel):
    now = time.time()
    xs = [t for t in _INTENTOS_FALLIDOS.get(tel, []) if now - t < _INTENTOS_VENTANA]
    xs.append(now)
    _INTENTOS_FALLIDOS[tel] = xs


def _copiar_historial_web(clave_web, tel):
    from app.core import db_chat
    from app.modules.chat import chat_agent
    mensajes = db_chat.chat_history("web", clave_web, chat_agent.MAX_CONTEXT_TURNS)
    clave_wa = f"tel:{tel}"
    for m in mensajes:
        db_chat.chat_save_message("whatsapp", clave_wa, m)


_ARRIVAL_BRIEFING = (
    "[la persona llega desde el chat de la web con el historial de arriba — "
    "dale la bienvenida, resumile en una línea dónde quedó la conversación y "
    "repetile la última pregunta pendiente para que siga desde acá]"
)


def _narrar_arrival(texto):
    """Teje la instrucción de bienvenida en el texto que ve el motor — MISMO
    patrón que chat_tools.narrar_adjunto (la instrucción se resuelve ANTES de
    llamar al motor, no después): el modelo YA tiene el historial recién
    copiado en su contexto (chat_history lo carga en cada turno), así que
    puede resumir de verdad en vez de repetir un saludo genérico."""
    texto = str(texto or "").strip()
    if texto:
        return texto + "\n\n" + _ARRIVAL_BRIEFING
    return _ARRIVAL_BRIEFING


def _procesar_handoff(texto, tel):
    """Si `texto` trae un código de traspaso válido, copia el historial web a
    esta conversación de WhatsApp. Devuelve (estado, mensaje):
      ("ok", None)         código válido, historial copiado (o el intento de
                           copia falló en silencio — igual seguimos: el
                           arribo se narra con lo que haya, ver _narrar_arrival)
      ("invalido", texto)  código inexistente/vencido/ya usado
      (None, None)         no hay código en el texto, o hay demasiados
                           intentos fallidos recientes (no reaccionamos)
    Esta función sólo copia mensajes viejos; QUIÉN queda autorizado a
    reservar se resuelve aparte, en `_procesar_mensaje`, vía
    `db_chat.user_by_handoff_phone` sobre el `used_phone`/`web_key` que
    `db_chat.chat_handoff_use` ya dejó grabado acá arriba."""
    code = _extraer_code(texto)
    if not code:
        return None, None
    if not _intento_permitido(tel):
        return None, None  # demasiados intentos fallidos recientes: no reaccionamos
    try:
        from app.core import db_chat
        fila = db_chat.chat_handoff_use(code, tel)
    except Exception:  # noqa: BLE001
        fila = None
    if not fila:
        _marcar_intento_fallido(tel)
        return "invalido", "Ese código no es válido o ya venció."
    try:
        _copiar_historial_web(fila["clave_web"], tel)
    except Exception:  # noqa: BLE001 — el arribo se narra igual aunque la copia falle
        pass
    return "ok", None


# ---- opciones tocables: mapear "1"/"a" a la opción que las trajo -----------
# El motor (chat_agent.py) ya cierra un mensaje con `[opciones: A | B | C]`
# cuando ofrece una elección concreta — la marca queda RAW en chat_mensajes
# (el modelo la necesita en su propio historial para entender una respuesta
# como "1"). En la web esas opciones se pintan como botones que mandan el
# TEXTO completo de la opción; por WhatsApp llegan como lista numerada
# (chat_agent.chat()) y la persona suele responder sólo el número o una
# letra — acá se resuelve ese número/letra a la opción real ANTES de
# pasárselo al motor, así la respuesta no depende de que el modelo adivine
# bien a partir del historial (2026-08-01).

_INDICE_RE = re.compile(r"^\s*([0-9]{1,2}|[a-zA-Z])[.)\-]?\s*$")


def _indice_opcion(texto):
    """"1", "1.", "1)" -> 0; "a", "A)" -> 0; "b" -> 1; cualquier otra cosa
    (una frase, texto vacío) -> None. Pura, sin base — fácil de testear."""
    m = _INDICE_RE.match(str(texto or ""))
    if not m:
        return None
    tok = m.group(1)
    if tok.isdigit():
        n = int(tok)
        return n - 1 if n >= 1 else None
    return ord(tok.lower()) - ord("a")


def _mapear_opcion(texto, opciones):
    """Reemplaza `texto` por `opciones[indice]` si `texto` es un número o
    letra bare que cae DENTRO del rango de opciones ofrecidas; si no,
    devuelve `texto` sin tocar (una frase normal nunca se reinterpreta)."""
    idx = _indice_opcion(texto)
    if idx is None or not opciones or not (0 <= idx < len(opciones)):
        return texto
    return opciones[idx]


def _ultimas_opciones(clave):
    """Las opciones del ÚLTIMO mensaje de asistente persistido en esta
    conversación (chat_mensajes, canal 'whatsapp') — [] si no hay ninguna o
    si la base no responde (best-effort, nunca rompe el turno)."""
    try:
        from app.modules.chat import chat_agent
        from app.core import db_chat
        historial = db_chat.chat_history("whatsapp", clave, chat_agent.MAX_CONTEXT_TURNS) or []
    except Exception:  # noqa: BLE001
        return []
    for m in reversed(historial):
        if m.get("role") != "assistant":
            continue
        contenido = m.get("content")
        if not isinstance(contenido, str):
            return []
        return chat_agent._parse_markers(contenido)["opciones"]
    return []


# ---- el mensaje entrante (ya normalizado) ------------------------------------

def _procesar_mensaje(msg):
    """`msg` YA normalizado y filtrado (no from_me, no grupo). Nunca levanta —
    el webhook siempre responde 200 pase lo que pase acá adentro."""
    tel = msg.get("from")
    if not tel:
        return

    from app.notifications import otp_send
    from app.core import ai_register
    marker = "AI disclosure delivered v1"
    try:
        from app.core import db_chat
        disclosed = db_chat.chat_disclosure_sent("whatsapp", f"tel:{tel}")
    except Exception:
        disclosed = False

    def record_disclosure():
        nonlocal disclosed
        disclosed = True
        try:
            db_chat.chat_save_message("whatsapp", f"tel:{tel}", {"role": "ai_disclosure", "content": marker})
        except Exception:
            pass

    def send_reply(number, text):
        reply = text if disclosed else ai_register.notice("ai.notice.chat") + "\n\n" + str(text)
        if otp_send.enviar_texto(number, reply) and not disclosed:
            record_disclosure()

    def prior_notice(document=False):
        notices = [] if disclosed else [ai_register.notice("ai.notice.chat")]
        if document:
            notices.append(ai_register.notice("ai.notice.order"))
        if not notices:
            return True
        if not otp_send.enviar_texto(tel, "\n\n".join(notices)):
            return False
        if not disclosed:
            record_disclosure()
        return True
    try:
        usuario = db_chat.user_by_phone(tel)
    except Exception:  # noqa: BLE001
        usuario = None

    texto_original = str(msg.get("body") or "").strip()
    handoff_estado, handoff_msg = _procesar_handoff(texto_original, tel)

    if not usuario:
        try:
            usuario = db_chat.user_by_handoff_phone(
                tel, grant_days=_int_env("TURNOS_WA_HANDOFF_GRANT_DAYS", 30))
        except Exception:  # noqa: BLE001
            usuario = None

    if not usuario:
        msg_out = _mensaje_sin_cuenta()
        if handoff_estado == "invalido":
            msg_out = handoff_msg + " " + msg_out
        send_reply(tel, msg_out)
        return

    texto = texto_original
    media = msg.get("media")
    if media:
        if not prior_notice(document=True):
            return
        data, mime = _descargar_media(media)
        if data:
            from app.modules.chat import chat_tools
            resultado = chat_tools.analizar_orden_para(
                f"tel:{tel}", data, mime or media.get("mime_type"), media.get("filename"))
            if resultado.get("error") == "enfriamiento":
                send_reply(tel, resultado.get("mensaje"))
                return
            texto = chat_tools.narrar_adjunto(texto, resultado)

    if handoff_estado == "ok":
        texto = _narrar_arrival(texto)

    # Respuesta al recordatorio del día previo ("¿vas a poder venir?"). Un sí o
    # un no a secas se resuelven acá, sin llamar al modelo: la respuesta queda
    # guardada en la solicitud y la mesa la ve en la cola del día. Cualquier
    # cosa más larga que eso sigue de largo hacia el motor.
    if not media:
        try:
            from app.notifications import confirmations
            # A reply to a proposed new booking must not confirm an unrelated
            # day-before reminder. The chat transport owns that exact proposal.
            pending = db_chat.chat_metadata_get('whatsapp', f'tel:{tel}', 'booking_proposal')
            directa = None if pending else confirmations.respond(usuario, texto, canal="whatsapp")
        except Exception:  # noqa: BLE001
            directa = None
        if directa:
            if handoff_estado == "invalido":
                directa = handoff_msg + "\n\n" + directa
            try:
                from app.modules.chat import chat_agent
                chat_agent.record_exchange(f"tel:{tel}", "whatsapp", texto, directa)
            except Exception:  # noqa: BLE001
                pass
            send_reply(tel, directa)
            return

    # Un "1"/"a" bare, respondiendo a una lista numerada que el motor mismo
    # ofreció el turno anterior, se resuelve a la opción real ANTES de
    # entrar al motor — salvo en el turno de arribo del traspaso (ese texto
    # ya lleva la instrucción tejida por _narrar_arrival, no una respuesta
    # bare a una lista).
    if not media and handoff_estado != "ok":
        texto = _mapear_opcion(texto, _ultimas_opciones(f"tel:{tel}"))

    from app.modules.chat import chat_agent
    if not prior_notice():
        return
    respuesta = chat_agent.chat(usuario, texto or "(mensaje vacío)",
                                 clave=f"tel:{tel}", canal="whatsapp")
    if handoff_estado == "invalido":
        respuesta = handoff_msg + "\n\n" + respuesta
    send_reply(tel, respuesta)


# ---- shared webhook application boundary ------------------------------------

# ---- una app de Meta, un callback, varias instalaciones ------------------------
#
# Meta entrega TODAS las respuestas de la app a UNA sola callback URL. La demo y
# pinamar.humanrounds.org comparten app y número, así que un «Sí/No» a un
# recordatorio de la demo caía en el webhook de pinamar (y podía confirmar el
# turno equivocado). Cada instalación procesa sólo lo suyo:
#   - TURNOS_WA_FORWARD_NUMBERS + TURNOS_WA_FORWARD_URL (la que tiene la callback):
#     los mensajes de esos teléfonos son de la instalación hermana; se le
#     reenvían y acá no se procesan;
#   - una demo con lista de destinatarios (TURNOS_WA_TEST_RECIPIENTS) procesa
#     sólo a esos teléfonos.

FORWARD_HEADER = "X-Turnos-WA-Forwarded"
_FORWARD_TIMEOUT = 5


def _wa_digits(valor):
    from app.notifications import otp_send
    return otp_send._wa_digits(valor)


def _phones(key):
    crudo = config.get_text(key) or ""
    return {d for d in (_wa_digits(p) for p in crudo.split(",") if p.strip()) if d}


def _plain_http_ok(url):
    """http only to the machine itself or to a one-label Docker network alias (no dot): the
    body carries the patient's number and text, so it never crosses the open internet in clear."""
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "http" and bool(host) and ("." not in host or host == "127.0.0.1")


def _forward_target():
    """(url, teléfonos) o (None, set()) si la instalación no reenvía. Sólo https, o http hacia
    un alias interno de Docker: la URL sale del entorno, pero un esquema raro nunca se abre."""
    url = (config.get_text("TURNOS_WA_FORWARD_URL") or "").strip()
    numbers = _phones("TURNOS_WA_FORWARD_NUMBERS")
    if not (url.lower().startswith("https://") or _plain_http_ok(url)) or not numbers:
        return None, set()
    return url, numbers


def _only_messages_from(body, numbers):
    """Copia del sobre de Meta que conserva sólo los mensajes de `numbers`."""
    out = {k: v for k, v in body.items() if k != "entry"}
    out["entry"] = []
    for entry in body.get("entry") or []:
        changes = []
        for change in entry.get("changes") or []:
            value = dict(change.get("value") or {})
            kept = [m for m in value.get("messages") or []
                    if _wa_digits(m.get("from")) in numbers]
            if kept:
                value.pop("statuses", None)
                value["messages"] = kept
                changes.append({**change, "value": value})
        if changes:
            out["entry"].append({**entry, "changes": changes})
    return out


def _send_forward(url, raw, headers):
    """POST best-effort al webhook de la instalación hermana. Nunca levanta."""
    try:
        req = urllib.request.Request(url, data=raw, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=_FORWARD_TIMEOUT) as resp:  # noqa: S310
            return 200 <= resp.status < 300
    except Exception as exc:  # noqa: BLE001
        _log(f"webhook forward failed: {log_pii.exc(exc)}")
        return False


def _forward_to_sibling(body, url, numbers):
    raw = json.dumps(_only_messages_from(body, numbers), ensure_ascii=False,
                     separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json", FORWARD_HEADER: "1"}
    secreto = _app_secret()
    if secreto:
        headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(
            secreto.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    _send_forward(url, raw, headers)


def _demo_own_numbers():
    """Teléfonos que esta instalación atiende cuando es una demo con lista;
    None = sin restricción."""
    from app.ops import demo_installation
    if not demo_installation.enabled():
        return None
    return _phones("TURNOS_WA_TEST_RECIPIENTS") or None


def process_webhook_payload(body, forwarded=False):
    """Normalize and process one provider envelope without transport I/O.
    `forwarded`: the request came from the sibling installation (never forwarded again)."""
    try:
        mensajes = _normalizar_meta(body) if _es_meta(body) else _normalizar_waha(body)
    except Exception as exc:  # noqa: BLE001
        _log(f"webhook normalization failed: {log_pii.exc(exc)}")
        mensajes = []

    own = _demo_own_numbers()
    url, theirs = (None, set()) if forwarded or not _es_meta(body) else _forward_target()
    if any(_wa_digits(m.get("from")) in theirs for m in mensajes):
        try:
            _forward_to_sibling(body, url, theirs)
        except Exception as exc:  # noqa: BLE001
            _log(f"webhook forward failed: {log_pii.exc(exc)}")

    for msg in mensajes:
        if msg.get("from_me") or msg.get("is_group"):
            continue
        tel = _wa_digits(msg.get("from"))
        if tel in theirs:
            continue
        if own is not None and tel not in own:
            _log("reply from a number outside this demo's recipients — not processed")
            continue
        try:
            _procesar_mensaje(msg)
        except Exception as exc:  # noqa: BLE001
            _log(f"message processing failed: {log_pii.exc(exc)}")
