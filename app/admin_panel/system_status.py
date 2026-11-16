"""system_status.py — «Sistema»: página de setup / estado de la integración.

Muestra, agrupado en clusters lógicos, si el despliegue está bien integrado y
qué falta todavía. Pensado para que la Secretaría (o quien monte esto en otro
municipio) vea de un vistazo qué está conectado y qué no:

1. Aplicación      — servidor, base de datos, esquema y daemons de fondo.
2. HSI             — sincronización de turnos: conexión, % agendado, conflictos.
3. Dominio         — el dominio definitivo (turnos.pinamar.gob.ar) vs el actual.
4. WhatsApp        — gateway de verificación (producción vs desarrollo).
5. Email           — el proveedor elegido (Mailgun o Mailjet) conectado y el remitente verificado.
6. Almacenamiento  — el volumen de datos es escribible y tiene espacio.
7. OpenRouter      — key conectada, tope y consumo (y saldo/días si hay mgmt key).

Route: GET /api/admin/system. Its native FastAPI dependency requires an admin
role because the payload exposes infrastructure state.

Cada chequeo degrada con gracia: un servicio externo caído o lento da estado
'error'/'warn' con su detalle, nunca un 500 ni un cuelgue (timeouts cortos). El
endpoint NO guarda secretos en la respuesta: sólo dice presente/ausente y datos
públicos (dominio, estado, montos de consumo).
"""

import json
import os
import shutil
import socket
import ipaddress
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from app.ai import ai_client
from app.core import config, i18n, site_identity, tz
from app.core import paths

ROOT = paths.PROJECT_ROOT

_TIMEOUT = 6  # segundos por llamada externa; un servicio caído no cuelga la página


# ----------------------------------------------------------------------------- utils

def _t(key, es, **kw):
    """«Sistema» en el idioma del panel; el castellano es el texto exacto de
    siempre (locales/*/admin.system.status.json)."""
    return i18n.t_or("admin.system.status." + key, es, i18n.active(), **kw)


def _env(name, default=""):
    """El entorno gana; el .env local completa (mismo patrón que db/notifications.mail)."""
    return config.get(name, default)


def _env_bool_off(nuevo, viejo=None):
    """True si el daemon está APAGADO (…_OFF == '1'). Lee el nombre nuevo en
    inglés y cae al viejo, igual que el resto del código (rename 2026-07-17)."""
    v = _env(nuevo) or (_env(viejo) if viejo else "")
    return v == "1"


def _probe_json(url, headers=None, timeout=_TIMEOUT):
    """GET url y devuelve (data|None, error|None). Nunca levanta."""
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw), None
    except urllib.error.HTTPError as e:
        e.close()  # swallowed without reading: close the body it holds
        return None, f"HTTP {e.code}"
    except (urllib.error.URLError, socket.timeout) as e:
        return None, _t("no_connection", "sin conexión ({reason})", reason=getattr(e, 'reason', e))
    except (ValueError, OSError) as e:  # JSON inválido, etc.
        return None, str(e)


def _money(x):
    """Formatea un monto de USD de OpenRouter en texto corto."""
    try:
        return f"US$ {float(x):.2f}"
    except (TypeError, ValueError):
        return "—"


def _worst(estados):
    """Estado agregado: error > warn > ok > na (na = neutro, no baja el color)."""
    orden = {"error": 3, "warn": 2, "ok": 1, "na": 0}
    peor = "na"
    for e in estados:
        if orden.get(e, 0) > orden[peor]:
            peor = e
    return peor


def _cluster(clave, titulo, items, resumen=""):
    return {
        "clave": clave,
        "titulo": titulo,
        "estado": _worst([i.get("estado", "na") for i in items]),
        "resumen": resumen,
        "items": items,
    }


# ----------------------------------------------------------------------------- 1. app

def _is_schema_refusal(exc):
    try:
        from app.core import db
    except Exception:  # noqa: BLE001 — no driver: nothing was refused
        return False
    return isinstance(exc, db.SchemaRefused)


def probe_db():
    """(conectado, tabla, migraciones_al_dia, detalle) para "¿Postgres
    responde?" — SELECT 1, la tabla base del esquema, y que Alembic esté en
    `head` (sin migraciones pendientes). Nunca levanta.

    Compartido por `_cluster_app()` (panel «Sistema», abajo) y `health.py`
    (`/ready`, Tier 4 slice 7, docs/roadmap/tier4-replatform-design.md) —
    una sola implementación de "¿la base está arriba y al día?", no dos
    (el diseño de slice 7 lo pide explícitamente: reusar el probe de acá en
    vez de escribir un segundo chequeo de conectividad)."""
    try:
        from sqlalchemy import func, literal_column, select
        from app.core import db, sqlcore
        with db._connect() as conn, conn.cursor() as cur:
            sqlcore.run(cur, select(literal_column("1"))).fetchone()
            # El cursor usa dict_row: leer por nombre de columna, no por índice.
            fila = sqlcore.run(cur, select(func.to_regclass("public.booking_requests").label("existe"))).fetchone()
            tabla = bool(fila.get("existe") if isinstance(fila, dict) else (fila[0] if fila else None))
            al_dia, actual, head = db.migrations_status(conn)
        detalle = None
        if not tabla:
            detalle = _t("app.no_table", "La tabla 'booking_requests' no existe.")
        elif not al_dia:
            detalle = _t("app.migrations_detail", "Migraciones pendientes (actual={actual}, head={head}).",
                         actual=actual, head=head)
        return True, tabla, al_dia, detalle
    except Exception as e:  # noqa: BLE001  (DbUnavailable, driver ausente, red)
        if _is_schema_refusal(e):
            # A refused schema (unknown Alembic revision): the whole message, never cut —
            # it names the revision, the newest one known and what to do.
            return False, False, False, str(e)
        return False, False, False, str(e)[:200]


_DEMO_REFRESH_MAX_AGE = timedelta(hours=27)
# The application process and PostgreSQL may be a fraction of a second apart
# even on the same host.  A small bounded tolerance prevents a just-written
# DB timestamp from being falsely called stale; a materially future record is
# still invalid rather than silently green.
_DEMO_REFRESH_MAX_FUTURE_SKEW = timedelta(minutes=5)


def _demo_refresh_item(db_available=True, now=None):
    """Safe completion evidence for the opted-in public demo, or ``None``.

    This stays on the already admin-only System surface rather than adding a
    public status endpoint.  It exposes no runtime config, credentials, demo
    records, or error text.  A prior successful row is insufficient: the
    latest attempt must be successful, cover today's installation date, and
    be plausibly fresh.  Any ambiguity is visibly non-green.
    """
    try:
        from app.ops import demo_installation
        demo = demo_installation.enabled()
    except Exception:  # noqa: BLE001 — never infer a demo from a failed guard
        demo = False
    if not demo:
        return None
    label = _t("demo.label", "Actualización de la demo")
    if os.getenv("TURNOS_DEMO_REFRESH_OFF") == "1":
        return {
            "label": label, "valor": _t("demo.disabled", "Apagada"), "estado": "warn",
            "detalle": _t("demo.disabled_detail",
                          "Los datos de la demo no se renuevan solos cada día."),
        }
    if not db_available:
        return {
            "label": label, "valor": _t("unavailable", "No disponible"), "estado": "error",
            "detalle": _t("demo.db_detail",
                          "Sin base de datos no se puede comprobar la actualización."),
        }
    try:
        from app.core import db_backup
        row = db_backup.demo_refresh_completion_status()
    except Exception:  # noqa: BLE001 — status is not cached in process memory
        return {
            "label": label, "valor": _t("unavailable", "No disponible"), "estado": "error",
            "detalle": _t("demo.read_failed",
                          "No se pudo leer el resultado de la actualización."),
        }
    if not row:
        return {
            "label": label, "valor": _t("demo.none", "Sin actualización completa"), "estado": "error",
            "detalle": _t("demo.none_detail",
                          "Ninguna actualización terminó todavía: la demo puede mostrar datos viejos."),
        }
    if row.get("outcome") != "success":
        return {
            "label": label, "valor": _t("demo.incomplete", "Incompleta"), "estado": "error",
            "detalle": _t("demo.incomplete_detail",
                          "La última actualización no terminó todos sus pasos."),
        }

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    today = now.astimezone(tz.tzinfo()).date()
    completed_for = row.get("success_for")
    completed_at = row.get("last_success_at")
    if completed_for != today or not isinstance(completed_at, datetime):
        return {
            "label": label, "valor": _t("demo.stale", "Desactualizada"), "estado": "error",
            "detalle": _t("demo.stale_day", "Ninguna actualización completa cubre el día de hoy."),
        }
    if completed_at.tzinfo is None:
        completed_at = completed_at.replace(tzinfo=timezone.utc)
    age = now - completed_at.astimezone(timezone.utc)
    if age < -_DEMO_REFRESH_MAX_FUTURE_SKEW or age > _DEMO_REFRESH_MAX_AGE:
        return {
            "label": label, "valor": _t("demo.stale", "Desactualizada"), "estado": "error",
            "detalle": _t("demo.stale_window", "La última actualización completa es demasiado vieja."),
        }
    # A just-committed database timestamp may lead this process clock by a
    # few milliseconds.  It is within the bounded tolerance above, and the
    # only honest human-readable age is "just now", never "-1 minutes".
    age = max(age, timedelta(0))
    minutes = int(age.total_seconds() // 60)
    hours = minutes // 60
    when = (_t("demo.just_now", "recién") if minutes < 1
            else _t("demo.minute", "hace 1 minuto") if minutes == 1
            else _t("demo.minutes", "hace {n} minutos", n=minutes) if minutes < 60
            else _t("demo.hour", "hace 1 hora") if hours == 1
            else _t("demo.hours", "hace {n} horas", n=hours))
    return {
        "label": label, "valor": _t("demo.ok", "Datos de hoy completos"), "estado": "ok",
        "detalle": _t("demo.done", "Completada {when}.", when=when),
    }


def _cluster_app():
    items = [{"label": _t("app.server", "Servidor"), "valor": _t("app.responding", "Respondiendo"), "estado": "ok"}]

    # Base de datos + esquema + migraciones (probe_db(), compartido con /ready).
    conectado, tabla, al_dia, detalle = probe_db()
    if not conectado:
        items.append({"label": _t("app.db", "Base de datos"), "valor": _t("unavailable", "No disponible"),
                      "estado": "error", "detalle": detalle})
    else:
        items.append({"label": _t("app.db", "Base de datos"), "valor": _t("connected", "Conectada"), "estado": "ok"})
        if not tabla:
            items.append({"label": _t("app.schema", "Esquema"), "valor": _t("app.missing_tables", "Faltan tablas"),
                          "estado": "error", "detalle": detalle})
        elif not al_dia:
            items.append({"label": _t("app.schema", "Esquema"), "valor": _t("app.pending_migrations", "Migraciones pendientes"),
                          "estado": "warn", "detalle": detalle})
        else:
            items.append({"label": _t("app.schema", "Esquema"), "valor": _t("app.tables_created", "Tablas creadas"), "estado": "ok"})

    demo_refresh = _demo_refresh_item(db_available=conectado)
    if demo_refresh is not None:
        items.append(demo_refresh)

    # Paquete de datos de la localidad (webdata/ o TURNOS_WEBDATA_DIR): a row
    # only when it is broken. Healthy, its value was the pack's folder name
    # («example-town»), an internal key on screen (ui-expert 08.11).
    try:
        from app.core import sitedata
        if not os.path.exists(sitedata.path("servicios.json")):
            items.append({
                "label": _t("app.pack", "Paquete de datos"),
                "valor": os.path.basename(os.path.normpath(sitedata.webdata_dir())),
                "estado": "error",
                "detalle": _t("app.pack_missing", "No se encontró servicios.json en el paquete de datos."),
            })
    except Exception as e:  # noqa: BLE001
        items.append({"label": _t("app.pack", "Paquete de datos"), "valor": "Error",
                      "estado": "warn", "detalle": str(e)[:200]})

    # Daemons de fondo. Apagado no es un error (puede ser intencional en dev),
    # pero en un despliegue "listo" deberían estar prendidos → warn si off.
    # La sincronización con HSI tiene su propio cluster (abajo), no se repite acá.
    daemons = [
        (_t("app.reminders", "Recordatorios"), _env_bool_off("TURNOS_REMINDERS_OFF", "TURNOS_RECORDATORIOS_OFF")),
        (_t("app.waitlist", "Lista de espera"), _env_bool_off("TURNOS_WAITLIST_OFF", "TURNOS_LISTA_ESPERA_OFF")),
    ]
    for nombre, apagado in daemons:
        items.append({
            "label": nombre,
            "valor": _t("off", "Apagado") if apagado else _t("active", "Activo"),
            "estado": "warn" if apagado else "ok",
        })

    if not conectado:
        resumen = _t("app.db_down", "La base de datos no responde: no se pueden guardar ni leer turnos.")
    elif demo_refresh and demo_refresh["estado"] == "error":
        # The DB may be healthy while this independent, demo-only admission
        # signal is red.  Do not relabel that as a database outage.
        resumen = _t("demo.review", "La actualización de la demo no terminó: revisá el ítem marcado.")
    elif _worst([i["estado"] for i in items]) == "error":
        resumen = _t("app.review", "La aplicación necesita revisión: revisá los ítems marcados.")
    else:
        resumen = _t("app.ok", "Servidor, base de datos y esquema en marcha.")
    return _cluster("app", _t("app.title", "Aplicación"), items, resumen)


# ----------------------------------------------------------------------------- 2. dominio

_PROVISIONAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "devbox")


def _is_provisional(host):
    if not host or host in _PROVISIONAL_HOSTS:
        return True
    if host.endswith(".local") or "." not in host:
        return True
    if "brieflysites.com" in host:  # dominio temporal de Coolify
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback
    except ValueError:
        return False


def _cluster_domain(request):
    configured_origin = urlparse(site_identity.base_url()).hostname or ""
    esperado = _env("TURNOS_DOMINIO") or configured_origin
    host_hdr = (request.headers.get("Host") or "").split(":")[0].strip().lower()
    proto = (request.headers.get("X-Forwarded-Proto") or "").strip().lower()
    https = proto == "https"

    if not host_hdr:
        est_dom, det = "warn", _t("domain.no_host", "No llegó el header Host.")
    elif _is_provisional(host_hdr):
        est_dom = "warn"
        det = _t("domain.provisional", "Estás en un dominio provisorio; falta apuntar el definitivo.")
    elif host_hdr == esperado.lower():
        est_dom, det = "ok", None
    else:
        est_dom = "warn"
        det = _t("domain.mismatch", "El dominio actual no coincide con el definitivo.")

    # Serving on the definitive domain: one «Dominio» row, not the same name
    # twice under two labels (critical review 08.11, D10).
    if est_dom == "ok":
        items = [{"label": _t("domain.title", "Dominio"), "valor": host_hdr, "estado": "ok"}]
    else:
        items = [
            {"label": _t("domain.expected", "Dominio definitivo"), "valor": esperado, "estado": "ok"},
            {"label": _t("domain.current", "Dominio actual"), "valor": host_hdr or "—", "estado": est_dom, "detalle": det},
        ]
    # HTTPS is a row only when it is missing: «HTTPS Sí» under a healthy
    # domain said nothing the green block does not (ui-expert 08.11).
    if not https:
        items.append({"label": "HTTPS", "valor": _t("no", "No"),
                      "estado": "na" if _is_provisional(host_hdr) else "warn",
                      "detalle": _t("domain.no_https", "El sitio no llega por HTTPS.")})
    # The links' address is a row only when it says something new: missing, or
    # on another host than the definitive domain. On a healthy install it was
    # the same domain a third time in a row (2026-11-04).
    origin_ok = not site_identity.origin_is_loopback()
    links_url = site_identity.base_url()
    links_host = (urlparse(links_url).hostname or "").lower()
    if not origin_ok or (esperado and links_host != str(esperado).lower()):
        items.append({"label": _t("domain.links_label", "Dirección de los enlaces"),
                      "valor": links_url, "estado": "ok" if origin_ok else "warn",
                      "detalle": None if origin_ok else _t(
                          "domain.links_missing",
                          "Falta la dirección pública del sitio: los correos con enlaces no se envían.")})
    resumen = (_t("domain.ok", "Sirviendo en el dominio definitivo.") if est_dom == "ok"
               else _t("domain.not_yet", "El dominio actual todavía no coincide con el configurado."))
    return _cluster("dominio", _t("domain.title", "Dominio"), items, resumen)


# ----------------------------------------------------------------------------- 3. whatsapp

_WA_NUMBER_TTL = 6 * 3600   # a number only changes when the sender is re-registered
_WA_NUMBER_RETRY = 300      # after a failure, do not ask Meta again on every page view
_wa_number_cache = {}       # phone_number_id -> (fetched_at, display number | None)


def _wa_display_number(phone_id, token):
    """The human number («+1 555-450-2857») behind Meta's phone_number_id, via
    Graph `GET /<id>?fields=display_phone_number`. Cached; never raises; None
    when there is no token or Meta does not answer (the caller then shows the
    id, the only thing it has)."""
    if not phone_id or not token:
        return None
    import time
    now = time.monotonic()
    hit = _wa_number_cache.get(phone_id)
    if hit and now - hit[0] < (_WA_NUMBER_TTL if hit[1] else _WA_NUMBER_RETRY):
        return hit[1]
    version = (_env("META_WA_GRAPH_VERSION", "v25.0") or "v25.0").lstrip("/")
    data, _err = _probe_json(
        f"https://graph.facebook.com/{version}/{phone_id}?fields=display_phone_number",
        headers={"Authorization": f"Bearer {token}"})
    numero = data.get("display_phone_number") if isinstance(data, dict) else None
    numero = numero.strip() if isinstance(numero, str) and numero.strip() else None
    _wa_number_cache[phone_id] = (now, numero)
    return numero


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


_WA_META_SHOW_S = 24 * 3600   # a pause that ended, or an error, stays on the card for a day


def _wa_meta_items(otp_send):
    """The rows only Meta has: the approved template of the verification code, the pause after a Meta rate
    limit (and when it ended), and the last error Meta returned with its cause in words. The state is the
    file both processes write (`otp_send.meta_estado`)."""
    from datetime import datetime
    from app.core import clock

    def _hora(ts):
        return datetime.fromtimestamp(float(ts), tz.tzinfo()).strftime("%Y-%m-%d %H:%M")

    items = []
    plantilla = otp_send.otp_plantilla()
    otp_por_meta = otp_send.otp_provider() == "meta"
    if plantilla or otp_por_meta:
        items.append({"label": _t("wa.otp_template", "Plantilla del código"),
                      "valor": plantilla or _t("missing", "Falta"),
                      "estado": "ok" if plantilla else "error",
                      "campo": "META_WA_OTP_TEMPLATE",
                      "detalle": (None if plantilla else
                                  _t("wa.no_otp_template",
                                     "El código de verificación sale por Meta, pero falta la plantilla "
                                     "aprobada: no sale ningún código."))})

    estado = otp_send.meta_estado()
    ahora = clock.utcnow().timestamp()
    try:
        hasta = float(estado.get("pausa_hasta") or 0)
    except (TypeError, ValueError):
        hasta = 0
    codigo_pausa = estado.get("pausa_codigo")
    if otp_send.meta_pausa_restante() > 0:
        items.append({"label": _t("wa.meta_sends", "Envíos por Meta"),
                      "valor": _t("wa.paused_until", "En pausa hasta {when}", when=_hora(hasta)),
                      "estado": "warn",
                      "detalle": _t("wa.pause_reason",
                                    "Meta frenó los envíos ({code}: {reason}). Mientras dura no sale ningún mensaje por WhatsApp.",
                                    code=codigo_pausa, reason=otp_send.meta_error_reason(codigo_pausa))})
    elif hasta and ahora - hasta < _WA_META_SHOW_S:
        items.append({"label": _t("wa.meta_sends", "Envíos por Meta"),
                      "valor": _t("wa.pause_ended", "La pausa terminó: {when}", when=_hora(hasta)),
                      "estado": "ok",
                      "detalle": _t("wa.pause_reason_past", "Meta había frenado los envíos ({code}: {reason}).",
                                    code=codigo_pausa, reason=otp_send.meta_error_reason(codigo_pausa))})

    try:
        error_en = float(estado.get("error_en") or 0)
        ok_en = float(estado.get("ok_en") or 0)
    except (TypeError, ValueError):
        error_en = ok_en = 0
    if error_en and ahora - error_en < _WA_META_SHOW_S:
        codigo = estado.get("error_codigo")
        if codigo is not None:
            detalle = f"{codigo}: {otp_send.meta_error_reason(codigo)}"
        else:
            detalle = _t("wa.meta_error_http", "Meta respondió HTTP {status} sin código de error.",
                         status=estado.get("error_http") or "—")
        grave = codigo in otp_send.META_CODIGOS_GRAVES and error_en > ok_en
        items.append({"label": _t("wa.meta_last_error", "Último error de Meta"),
                      "valor": _hora(error_en),
                      "estado": "error" if grave else "na",
                      "detalle": detalle})
    return items


def _cluster_whatsapp():
    """Lee las variables REALES que usan otp_send.py (OTP + chat) y
    whatsapp_chat.py (webhook/handoff) — no las legacy TURNOS_WHATSAPP_*/
    WAHA_URL/WHATSAPP_* que nada en el código setea (bug: la tarjeta mostraba
    'No configurado' con WhatsApp andando de verdad, 2026-07-29)."""
    from app.notifications import otp_send

    meta_token = _env("META_WA_TOKEN")
    meta_phone = _env("META_WA_PHONE_NUMBER_ID")
    waha_url = _env("OTP_WAHA_URL")
    waha_key = _env("OTP_WAHA_API_KEY")
    waha_session = _env("OTP_WAHA_SESSION", "default")
    numero_pub = _env("TURNOS_WA_NUMERO")

    # La MISMA selección que hace el código al enviar — otp_send.wa_provider()
    # es la única fuente de verdad (contrato de transporte 2026-08-14: Meta en
    # producción, WAHA sólo con opt-in explícito + URL loopback de desarrollo,
    # sin proveedor = no-op, nunca fallback silencioso a WAHA).
    activo = otp_send.wa_provider()

    if not activo:
        if waha_url:
            detalle = _t("wa.waha_disabled",
                         "WAHA está configurado pero deshabilitado: sirve sólo para "
                         "pruebas locales. En producción se usa Meta Cloud API; sin "
                         "proveedor no sale ningún mensaje.")
        else:
            detalle = _t("wa.meta_missing",
                         "Falta conectar Meta Cloud API: ni el código por WhatsApp ni "
                         "el chat envían nada. Cargalo en Ajustes › Integraciones.")
        items = [{
            "label": _t("provider", "Proveedor"), "valor": _t("not_configured", "No configurado"), "estado": "na",
            "detalle": detalle,
        }]
        return _cluster("whatsapp", "WhatsApp", items,
                        _t("wa.not_connected", "WhatsApp sin conectar: falta Meta Cloud API."))

    items = [{"label": _t("wa.active_provider", "Proveedor activo"),
              "valor": "Meta Cloud API" if activo == "meta" else "WAHA",
              "estado": "ok"}]

    # The same line reads the same in both rows (one formatter per datum): the
    # public number takes Meta's own spelling when it is the sender, the
    # shared phone formatter otherwise (it was «+1 555-450-2857» four rows
    # above «15554502857», 2026-11-04).
    emisor = _wa_display_number(meta_phone, meta_token) if activo == "meta" else None
    if emisor and _digits(emisor) == _digits(numero_pub):
        numero_pub_txt = emisor
    else:
        from app.core import format as display_format
        numero_pub_txt = display_format.tel_text(numero_pub) if numero_pub else ""

    if activo == "meta":
        items.append({"label": _t("wa.meta_token", "Token de Meta"), "valor": _t("present", "Presente") if meta_token else _t("missing", "Falta"),
                      "estado": "ok" if meta_token else "error",
                      "detalle": None if meta_token else _t("wa.missing_token", "Falta el token de Meta: cargalo en Ajustes › Integraciones.")})
        items.append({"label": _t("wa.sender", "Número emisor"),
                      "valor": emisor or meta_phone or "—",
                      "estado": "ok" if meta_phone else "error",
                      "detalle": None if meta_phone else _t("wa.missing_phone", "Falta el número emisor de Meta: cargalo en Ajustes › Integraciones.")})
        webhook_secret = _env("TURNOS_WA_WEBHOOK_SECRET")
        items.append({"label": _t("wa.webhook_secret", "Secreto del webhook"), "valor": _t("present", "Presente") if webhook_secret else _t("missing", "Falta"),
                      "estado": "ok" if webhook_secret else "warn",
                      "detalle": (None if webhook_secret else
                                  _t("wa.no_webhook_secret",
                                     "Falta el secreto del webhook: Meta no puede registrar "
                                     "la dirección que recibe los mensajes."))})
        # El app secret es lo que autentica los POST de Meta: es el único
        # material que Meta manda (X-Hub-Signature-256). Sin él, los mensajes
        # entrantes sólo pasan con el secreto compartido en el query string de
        # la callback URL — funciona, pero deja el secreto a la vista.
        app_secret = _env("META_WA_APP_SECRET")
        items.append({"label": _t("wa.app_secret", "App secret"), "valor": _t("present", "Presente") if app_secret else _t("missing", "Falta"),
                      "estado": "ok" if app_secret else "warn",
                      "detalle": (None if app_secret else
                                  _t("wa.no_app_secret",
                                     "Falta el app secret de Meta: los mensajes entrantes sólo "
                                     "se aceptan con el secreto del webhook a la vista en su "
                                     "dirección, donde queda en el panel y en los registros."))})
        items.extend(_wa_meta_items(otp_send))
    else:  # waha — sólo posible con opt-in explícito + loopback (dev)
        host = urlparse(waha_url).hostname or waha_url
        items.append({"label": "Gateway WAHA", "valor": host or "—",
                      "estado": "warn",
                      "detalle": _t("wa.waha_dev", "WAHA sirve sólo para pruebas locales. "
                                    "En producción se usa Meta Cloud API.")})
        items.append({"label": "API key", "valor": _t("present", "Presente") if waha_key else _t("missing", "Falta"),
                      "estado": "ok" if waha_key else "error",
                      "detalle": None if waha_key else _t("wa.missing_waha_key",
                                                          "Falta la API key de WAHA: cargala en Ajustes › Integraciones.")})
        items.append({"label": _t("wa.session", "Sesión"), "valor": waha_session or "default", "estado": "ok"})

    items.append({"label": _t("wa.public_number", "Número público (handoff)"), "valor": numero_pub_txt or "—",
                  "estado": "ok" if numero_pub else "warn",
                  "detalle": (None if numero_pub else
                              _t("wa.no_public_number", "Falta el número público: el chat no muestra el enlace a WhatsApp."))})

    # Avisos iniciados por el hospital (confirmación + recordatorio): apagados
    # es un estado válido, no un error — Pinamar los tiene apagados a propósito.
    avisos_on = (_env("TURNOS_WA_NOTIFY") or "").lower() in ("1", "true", "yes", "on")
    prueba = otp_send._destinatarios_prueba()
    if not avisos_on or (not prueba and otp_send.demo_installation.enabled()):
        valor = "Apagados"
    elif prueba:
        valor = f"Sólo a {len(prueba)} teléfono{'s' if len(prueba) != 1 else ''} de prueba"
    else:
        valor = "A todos los pacientes"
    items.append({"label": "Avisos a pacientes", "valor": valor, "estado": "ok"})

    peor = _worst([i["estado"] for i in items])
    resumen = {"error": _t("wa.summary_error", "Falta configuración de WhatsApp: revisá los detalles."),
              "warn": _t("wa.summary_warn", "WhatsApp conectado, con avisos."),
              "ok": _t("wa.summary_ok", "WhatsApp conectado.")}[peor]
    return _cluster("whatsapp", "WhatsApp", items, resumen)


# ----------------------------------------------------------------------------- 4. email

_EMAIL_PROVIDER_NAMES = {"mailgun": "Mailgun", "mailjet": "Mailjet"}


def _cluster_email():
    """The SELECTED email provider (`email_transport` connector, Mailgun by
    default): missing keys, the live state of its sending identity (Mailgun:
    the domain, in its configured region; Mailjet: the MAIL_FROM sender) and
    the From line."""
    from app.core import theme
    from app.notifications import mail
    adapter, provider, source = mail.transport()
    if adapter is None:
        valor = _env("CONNECTOR_EMAIL_TRANSPORT") or "—"
        items = [{"label": _t("provider", "Proveedor"), "valor": valor, "estado": "error",
                  "campo": "CONNECTOR_EMAIL_TRANSPORT",
                  "detalle": _t("email.no_provider", "No hay proveedor de email seleccionado: los emails no se envían.")}]
        return _cluster("email", "Email", items, _t("email.not_connected", "Email sin conectar."))
    nombre = _EMAIL_PROVIDER_NAMES.get(provider, provider)
    faltan = adapter.missing_config()
    if faltan:
        items = [{"label": nombre, "valor": _t("unconfigured", "Sin configurar"), "estado": "error",
                  "campo": faltan[0],
                  "detalle": _t("email.missing_keys",
                                "Falta la clave de {provider}: cargala en Ajustes › Integraciones; "
                                "hasta entonces los emails no se envían.", provider=nombre)}]
        return _cluster("email", "Email", items, _t("email.not_connected", "Email sin conectar."))

    items = [{"label": _t("provider", "Proveedor"), "valor": nombre, "estado": "ok"}]
    if provider == "mailgun":
        dominio = _env("MAILGUN_DOMAIN")
        frm = _env("MAIL_FROM") or f"{theme.brand_name()} <no-responder@{dominio}>"
        items.append({"label": _t("email.domain", "Dominio de envío"), "valor": dominio, "estado": "ok"})
    else:
        frm = _env("MAIL_FROM")

    # Chequeo en vivo de la identidad de envío en el proveedor.
    vivo = adapter.probe()
    estado = vivo.get("state")
    if vivo.get("error"):
        est_vivo, val_vivo, det_vivo = ("warn", _t("unverifiable", "No verificable"),
                                        _t("email.probe_error", "{error} (el envío puede seguir andando).", error=vivo['error']))
    elif provider == "mailgun" and "sandbox" in (vivo.get("target") or ""):
        est_vivo, val_vivo = "warn", "Sandbox"
        det_vivo = _t("email.sandbox", "Dominio sandbox: sólo llega a destinatarios autorizados.")
    elif estado == "active":
        est_vivo, val_vivo, det_vivo = "ok", _t("active", "Activo"), None
    elif estado == "not_found":
        est_vivo, val_vivo = "warn", _t("email.not_found", "No encontrado")
        det_vivo = _t("email.sender_unknown", "El remitente {sender} no está dado de alta en {provider}.",
                      sender=vivo.get('target'), provider=nombre)
    elif estado in ("unverified", "disabled", "inactive"):
        est_vivo, val_vivo = "warn", estado.capitalize()
        det_vivo = _t("email.dns", "Faltan verificar los registros DNS de envío.")
    else:
        est_vivo, val_vivo, det_vivo = "warn", estado or _t("email.unknown", "Desconocido"), None
    items.append({"label": _t("email.state_in", "Estado en {provider}", provider=nombre), "valor": val_vivo, "estado": est_vivo, "detalle": det_vivo})
    items.append({"label": _t("email.sender", "Remitente"), "valor": frm or "—", "estado": "ok" if frm else "warn"})

    resumen = (_t("email.ok", "Email conectado y el remitente verificado.") if est_vivo == "ok"
               else _t("email.review", "Email configurado; revisá el remitente en {provider}.", provider=nombre))
    return _cluster("email", "Email", items, resumen)


# ----------------------------------------------------------------------------- 5. almacenamiento

def data_dir():
    """Carpeta de datos activa — TURNOS_DATA_DIR o ./data. Único lugar que
    resuelve esto para que housekeeping.py (y cualquier otro caller) mida el
    MISMO volumen que ve el panel."""
    return os.path.abspath(_env("TURNOS_DATA_DIR") or os.path.join(ROOT, "data"))


def medir_disco(base=None):
    """Espacio libre del volumen de datos. `base` por defecto es `data_dir()`.
    Devuelve {"libre_gb", "total_gb", "usado_pct", "libre_pct"} o None si
    `shutil.disk_usage` falla (montaje no disponible, etc.) — nunca levanta.
    Único medidor: housekeeping.py lo importa en vez de duplicar la llamada a
    `shutil.disk_usage`."""
    try:
        du = shutil.disk_usage(base or data_dir())
    except OSError:
        return None
    libre_gb = du.free / (1024 ** 3)
    total_gb = du.total / (1024 ** 3)
    usado_pct = (du.used / du.total * 100) if du.total else 0
    return {
        "libre_gb": libre_gb,
        "total_gb": total_gb,
        "usado_pct": usado_pct,
        "libre_pct": 100 - usado_pct,
    }


def _cluster_storage():
    base = data_dir()
    ordenes = os.path.join(base, "ordenes")
    items = []

    # Escritura: tocar y borrar un archivo temporal en la carpeta de datos.
    escribible, det_w = True, None
    try:
        os.makedirs(base, exist_ok=True)
        probe = os.path.join(base, ".setup-probe")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
    except OSError as e:
        escribible, det_w = False, str(e)[:200]
    items.append({"label": _t("storage.folder", "Carpeta de datos"), "valor": base, "estado": "ok" if escribible else "error"})
    items.append({"label": _t("storage.writing", "Escritura"),
                  "valor": _t("storage.writable", "Escribible") if escribible else _t("storage.read_only", "Sólo lectura"),
                  "estado": "ok" if escribible else "error", "detalle": det_w})

    # Espacio libre.
    medida = medir_disco(base)
    if medida is not None:
        est_disco = "ok" if medida["libre_gb"] >= 1 else "warn"
        items.append({
            "label": _t("storage.free", "Espacio libre"),
            "valor": _t("storage.free_value", "{free} GB libres de {total} GB ({used}% usado)",
                        free=f"{medida['libre_gb']:.1f}", total=f"{medida['total_gb']:.0f}",
                        used=f"{medida['usado_pct']:.0f}"),
            "estado": est_disco,
            "detalle": _t("storage.low", "Queda poco espacio para archivos nuevos.") if est_disco == "warn" else None,
        })

    # Órdenes guardadas (best-effort, no crítico).
    try:
        n = sum(1 for _ in os.scandir(ordenes)) if os.path.isdir(ordenes) else 0
        items.append({"label": _t("storage.orders", "Órdenes guardadas"), "valor": str(n), "estado": "ok"})
    except OSError:
        pass

    resumen = (_t("storage.ok", "Volumen de datos escribible y con espacio.") if escribible
               else _t("storage.not_writable", "La carpeta de datos no es escribible: los adjuntos fallarían."))
    return _cluster("storage", _t("storage.title", "Almacenamiento"), items, resumen)


# ----------------------------------------------------------------------------- 6. openrouter

def _cluster_openrouter():
    snapshot = ai_client.transport_snapshot()
    profile = snapshot["profile"]
    titulo = _t("or.ai_title", "Inteligencia artificial")
    if not snapshot["configured"]:
        items = [{"label": titulo, "valor": _t("not_configured_f", "No configurada"), "estado": "error",
                  "campo": "AI_INFERENCE_PROFILE",
                  "detalle": _t("or.not_configured_detail",
                                "La IA no está configurada: cargala en Ajustes › Integraciones.")}]
        return _cluster("openrouter", titulo, items,
                        _t("or.not_connected", "La lectura de órdenes con IA no está conectada."))

    # OpenRouter's account/spend API only applies to the shared cloud profile.
    # A dedicated non-cloud endpoint is configured and probed through the
    # profile transport; never send its key to OpenRouter for status.
    if profile != "cloud_shared":
        items = [{"label": titulo, "valor": _t("configured_f", "Configurada"), "estado": "ok",
                  "campo": "AI_INFERENCE_PROFILE"}]
        return _cluster("openrouter", titulo, items, _t("or.configured_summary", "IA configurada."))

    key = snapshot["primary_key"] or snapshot["backup_key"]

    data, err = _probe_json("https://openrouter.ai/api/v1/key",
                            headers={"Authorization": f"Bearer {key}"})
    if err:
        items = [{"label": "API key", "valor": _t("unverifiable", "No verificable"), "estado": "warn",
                  "detalle": f"OpenRouter: {err}."}]
        return _cluster("openrouter", "OpenRouter", items, _t("or.query_failed", "No se pudo consultar a OpenRouter."))

    d = (data or {}).get("data", {})
    limite = d.get("limit")            # tope en USD (None = sin tope)
    reset = d.get("limit_reset")       # 'daily' | 'monthly' | None
    restante = d.get("limit_remaining")
    uso_dia = d.get("usage_daily") or 0
    uso_sem = d.get("usage_weekly") or 0
    uso_mes = d.get("usage_monthly") or 0

    items = [{"label": "API key", "valor": _t("connected", "Conectada"), "estado": "ok"}]

    if limite is None:
        items.append({"label": _t("or.cap", "Tope"), "valor": _t("or.no_cap", "Sin tope"), "estado": "ok"})
        est_tope = "ok"
    else:
        tope = {"daily": _t("or.cap_daily", "{amount} por día", amount=_money(limite)),
                "monthly": _t("or.cap_monthly", "{amount} por mes", amount=_money(limite))}.get(reset, _money(limite))
        items.append({"label": _t("or.cap", "Tope"), "valor": tope, "estado": "ok"})
        # Cerca del tope del período → warn.
        est_tope = "ok"
        if restante is not None and limite:
            if restante <= 0:
                est_tope = "error"
            elif restante < 0.2 * limite:
                est_tope = "warn"
        items.append({
            "label": _t("or.used_today", "Consumido hoy"),
            "valor": (_t("or.used_of", "{used} de {limit}", used=_money(uso_dia), limit=_money(limite))
                      if reset == "daily" and limite else _money(uso_dia)),
            "estado": "ok",
        })
        if restante is not None:
            items.append({
                "label": _t("or.available", "Disponible en el período"),
                "valor": _money(restante),
                "estado": est_tope,
                "detalle": (_t("or.cap_hit", "Se tocó el tope del período.") if est_tope == "error"
                            else _t("or.cap_near", "Cerca del tope del período.") if est_tope == "warn" else None),
            })

    items.append({"label": _t("or.week", "Consumo 7 días"), "valor": _money(uso_sem), "estado": "ok"})
    items.append({"label": _t("or.month", "Consumo 30 días"), "valor": _money(uso_mes), "estado": "ok"})

    # «¿Para cuántos días tenemos crédito?» — con un tope que se renueva por día
    # no hay saldo que se agote (se rellena cada día); el riesgo es tocar el tope
    # de un día. Si además hay una management key (opcional, NO se guarda en la
    # app por default), se muestra el saldo real de la cuenta y los días de runway.
    ritmo = (uso_sem / 7.0) if uso_sem else uso_dia
    if reset == "daily":
        items.append({
            "label": _t("or.credit_days", "Días de crédito"),
            "valor": _t("or.renews_daily", "Se renueva cada día"),
            "estado": "ok",
            "detalle": _t("or.daily_detail", "Tope diario que se rellena solo; ritmo ~{rate}/día. El riesgo es tocar el tope de un día, no quedarse sin saldo.",
                          rate=_money(ritmo)),
        })
    mgmt = _env("OPENROUTER_MANAGEMENT_KEY")
    if mgmt:
        cred, err2 = _probe_json("https://openrouter.ai/api/v1/credits",
                                 headers={"Authorization": f"Bearer {mgmt}"})
        if not err2:
            c = (cred or {}).get("data", {})
            saldo = (c.get("total_credits") or 0) - (c.get("total_usage") or 0)
            items.append({"label": _t("or.balance", "Saldo de la cuenta"), "valor": _money(saldo), "estado": "ok"})
            if ritmo > 0:
                dias = saldo / ritmo
                items.append({
                    "label": _t("or.credit_days_account", "Días de crédito (cuenta)"),
                    "valor": i18n.tn_or("admin.system.status.or.days", f"{dias:.0f}", "~{n} día", "~{n} días",
                                     i18n.active()) if dias >= 0 else "—",
                    "estado": "ok" if dias >= 14 else "warn",
                    "detalle": _t("or.rate", "Al ritmo de ~{rate}/día.", rate=_money(ritmo)),
                })

    resumen = {"error": _t("or.summary_error", "Se tocó el tope de OpenRouter."),
               "warn": _t("or.summary_warn", "OpenRouter cerca del tope del período."),
               "ok": _t("or.summary_ok", "IA conectada y con crédito.")}[_worst([i["estado"] for i in items])]
    return _cluster("openrouter", "OpenRouter", items, resumen)


# ----------------------------------------------------------------------------- 6b. renaper

def _cluster_renaper():
    """RENAPER / Mi Argentina (login federado, auth_renaper.py) — opcional: sin
    credenciales el botón de login federado simplemente no aparece, así que
    'na' (neutro) en vez de 'error' cuando no está configurado."""
    client_id = _env("TURNOS_RENAPER_CLIENT_ID")
    client_secret = _env("TURNOS_RENAPER_CLIENT_SECRET")
    if not (client_id and client_secret):
        items = [{"label": _t("renaper.label", "Login federado"), "valor": _t("not_configured", "No configurado"), "estado": "na",
                  "campo": "TURNOS_RENAPER_CLIENT_ID",
                  "detalle": _t("renaper.missing", "Sin las credenciales de Mi Argentina el sitio no "
                                 "ofrece ese inicio de sesión (opcional).")}]
        return _cluster("renaper", "RENAPER / Mi Argentina", items,
                        _t("renaper.not_connected", "Login federado sin conectar (opcional)."))
    items = [
        {"label": "Client ID", "valor": _t("present", "Presente"), "estado": "ok"},
        {"label": "Client secret", "valor": _t("present", "Presente"), "estado": "ok"},
    ]
    for label, key in (("Authorize URL", "TURNOS_RENAPER_AUTHORIZE_URL"),
                       ("Token URL", "TURNOS_RENAPER_TOKEN_URL"),
                       ("Userinfo URL", "TURNOS_RENAPER_USERINFO_URL")):
        v = _env(key)
        items.append({"label": label, "valor": v or _t("renaper.default", "(default oficial)"), "estado": "ok"})
    return _cluster("renaper", "RENAPER / Mi Argentina", items, _t("renaper.connected", "Login federado conectado."))


# ----------------------------------------------------------------------------- 6c. puco

def _cluster_puco():
    """SISA/PUCO (obra social por DNI, puco.py) — WS131 es un servicio
    público del Ministerio de Salud, no necesita key: 'ok' de por sí salvo
    que un admin haya pisado la URL con algo raro."""
    url = _env("TURNOS_SISA_URL") or "https://sisa.msal.gov.ar/sisa/services/rest/puco/"
    publico = (_env("TURNOS_PUCO_PUBLIC", "1") or "1") != "0"
    items = [
        {"label": _t("puco.ws", "Servicio WS131"), "valor": urlparse(url).hostname or url, "estado": "ok"},
        {"label": _t("puco.fallback", "Consulta pública de respaldo"),
         "valor": _t("active_f", "Activa") if publico else _t("off_f", "Apagada"),
         "estado": "ok" if publico else "warn",
         "detalle": None if publico else _t("puco.off_detail", "Si WS131 no responde no hay respaldo: "
                                                "el DNI se carga a mano.")},
    ]
    resumen = (_t("puco.ok", "Obra social por DNI conectada.") if publico
              else _t("puco.no_fallback", "Obra social por DNI conectada, sin respaldo si el servicio principal falla."))
    return _cluster("puco", "SISA / PUCO", items, resumen)


# ----------------------------------------------------------------------------- 6d. backblaze b2

def _cluster_b2():
    """Backups de data/ a Backblaze B2 (data_backups.py) — igual que RENAPER,
    opcional: sin credenciales el backup queda dormido pero el sitio anda
    igual, así que 'warn' (no 'error') cuando falta."""
    key_id = _env("TURNOS_B2_KEY_ID")
    app_key = _env("TURNOS_B2_APP_KEY")
    bucket = _env("TURNOS_B2_BUCKET_ID")
    if not (key_id and app_key and bucket):
        items = [{"label": "Backup a B2", "valor": "Sin credenciales", "estado": "warn",
                  "campo": "TURNOS_B2_KEY_ID",
                  "detalle": "Faltan TURNOS_B2_KEY_ID/_APP_KEY/_BUCKET_ID: data/ (adjuntos, "
                             "fotos) no se respalda fuera del volumen local."}]
        return _cluster("b2", "Backblaze B2", items, "Backup de data/ dormido (opcional).")
    prefijo = _env("TURNOS_B2_PREFIX") or "data/"
    items = [
        {"label": "Key ID", "valor": "Presente", "estado": "ok"},
        {"label": "Application key", "valor": "Presente", "estado": "ok"},
        {"label": "Bucket", "valor": bucket, "estado": "ok"},
        {"label": "Prefijo", "valor": prefijo, "estado": "ok"},
    ]
    resumen = "Backup de data/ conectado."
    purga = _b2_purge_items()
    if purga:
        items += purga
        if purga[-1].get("estado") == "warn":
            from app.ops import purge_guard
            if purge_guard.remote_purge_ignored():
                resumen = _t("b2.purge_ignored.summary",
                             "Backup de data/ conectado; la purga remota pedida se ignora en esta instalación.")
            else:
                resumen = _t("b2.denied.summary", "Backup de data/ conectado, pero la clave no puede borrar archivos.")
    return _cluster("b2", "Backblaze B2", items, resumen)


def _b2_purge_items():
    """Qué hizo el último pase de la purga remota (lo deja el barrido en
    `backup_purge_state`): archivos que esperan, último pase sin rechazos y,
    si B2 rechazó el borrado, el aviso de que la clave no tiene deleteFiles.
    Si se pidió la purga donde está bloqueada (`*.gob.ar`), sólo el aviso de
    que se ignora. Vacío si la purga remota está apagada o la base no responde."""
    from app.ops import purge_guard
    if purge_guard.remote_purge_ignored():
        return [{
            "label": _t("b2.purge_ignored.label", "Purga remota"),
            "valor": _t("b2.purge_ignored.value", "Ignorada en esta instalación"), "estado": "warn",
            "detalle": _t("b2.purge_ignored.detail",
                          "TURNOS_BACKUP_PURGE_REMOTE=1 se ignora en esta instalación: la aplicación no borra "
                          "copias de los backups (la purga de las copias remotas la hace el municipio por su "
                          "cuenta). Quitar la variable o ponerla en 0."),
        }]
    if not purge_guard.remote_purge_enabled():
        return []
    from app.core import db_backup
    try:
        estado = db_backup.backup_purge_state()
    except Exception:  # noqa: BLE001 - la base ya tiene su propio chequeo
        return []
    ultimo = estado["last_success_at"]
    ultimo = (ultimo.astimezone(tz.tzinfo()).strftime("%Y-%m-%d %H:%M")
              if isinstance(ultimo, datetime) else _t("b2.never", "Todavía no"))
    items = [
        {"label": _t("b2.pending.label", "Archivos por borrar"),
         "valor": str(estado["pending"]), "estado": "ok"},
        {"label": _t("b2.last_clean.label", "Último borrado limpio"),
         "valor": ultimo, "estado": "ok"},
    ]
    if estado["denied"] > 0:
        items.append({
            "label": _t("b2.denied.label", "Permiso de borrado"),
            "valor": _t("b2.denied.value", "Falta deleteFiles"), "estado": "warn",
            "campo": "TURNOS_B2_KEY_ID",
            "detalle": i18n.tn_or(
                "admin.system.status.b2.denied.detail", estado["denied"],
                "La clave de aplicación de B2 no tiene el permiso deleteFiles: {n} archivo que la "
                "retención ya borró sigue guardado en B2. Cargar una clave con ese permiso "
                "(TURNOS_B2_KEY_ID / TURNOS_B2_APP_KEY); el próximo barrido lo borra.",
                "La clave de aplicación de B2 no tiene el permiso deleteFiles: {n} archivos que la "
                "retención ya borró siguen guardados en B2. Cargar una clave con ese permiso "
                "(TURNOS_B2_KEY_ID / TURNOS_B2_APP_KEY); el próximo barrido los borra.",
                i18n.active())})
    return items


def _cluster_backup():
    from app.core import backup_settings, backup_journal, i18n
    cfg = backup_settings.snapshot()
    unavailable = False
    try:
        rows = backup_journal.Store().list()
    except Exception:
        rows, unavailable = [], True
    plaintext = all(_env(k) for k in ("TURNOS_B2_KEY_ID", "TURNOS_B2_APP_KEY", "TURNOS_B2_BUCKET_ID")) and _env("TURNOS_BACKUP_OFF") != "1"
    evidence = backup_settings.system_state(rows, cfg, plaintext=bool(plaintext))
    def tr(key, fallback):
        return i18n.t_or("backup." + key, fallback, i18n.active())
    labels = {"unconfigured": tr("unconfigured", "Sin configurar"), "overdue": tr("overdue", "Atrasada"),
              "partial": tr("partial", "Copia parcial"), "ok": tr("current", "Al día")}
    evidence["journal_unavailable"] = unavailable
    value = labels[evidence["state"]]
    facts = []
    if evidence["last_success"]:
        facts.append(f'{evidence["size"] / 1024**2:.1f} MiB')
    if evidence["destination_host"]:
        facts.append(evidence["destination_host"])
    for key, yes, no in (
        ("uploaded", tr("uploaded", "Subida"), tr("not_uploaded", "Sin subir")),
        ("integrity_verified", tr("integrity", "Integridad verificada"), tr("not_integrity", "Integridad pendiente")),
        ("restore_tested", tr("restored", "Restauración probada"), tr("not_restored", "Restauración pendiente"))):
        facts.append(yes if evidence[key] else no)
    if plaintext:
        facts.append(tr("plaintext", "copia sin cifrar activa"))
    if evidence["last_error"] == "pg_dump_missing":
        facts.append(tr("pg_dump_missing", "Falta pg_dump en el servidor"))
    item = {"label": tr("last_backup", "Última copia"), "valor": value,
            "estado": "ok" if evidence["state"] == "ok" and not plaintext and not unavailable else "warn",
            "campo": "TURNOS_BACKUP_S3_ENDPOINT", "detalle": " · ".join(facts), "backup": evidence}
    items = [item]
    from app.ops import purge_guard
    if purge_guard.rotation_ignored():
        # The municipal installation deletes no backup (core/backup_lock): the
        # switch is ignored there, and says so.
        items.append({"label": tr("rotation_ignored.label", "Rotación de copias"),
                      "valor": tr("rotation_ignored.value", "Ignorada en esta instalación"), "estado": "warn",
                      "campo": "TURNOS_BACKUP_RETENTION_APPLY",
                      "detalle": tr("rotation_ignored.detail",
                                    "TURNOS_BACKUP_RETENTION_APPLY=1 se ignora en esta instalación: la aplicación no "
                                    "borra ni sobrescribe copias (la rotación la hace el municipio por su cuenta). "
                                    "Ponerla en 0.")})
    return _cluster("backup", tr("title", "Copias de seguridad"), items)


def _cluster_ai_register():
    from app.core import ai_register, i18n
    label = i18n.t_or('ai.register.title', 'Registro de IA', i18n.active())
    try:
        state = ai_register.dossier()['estado_actual']
        count = state['pending_notifications']
        value = i18n.tn_or('ai.register.system_count', count, '{count} notificación pendiente',
                            '{count} notificaciones pendientes', i18n.active(), count=count)
        status = 'warn' if count else 'ok'
    except Exception:
        value = i18n.t_or('ai.register.unavailable', 'Registro no disponible; pendientes no verificados', i18n.active())
        status = 'warn'
    return _cluster('ai-register', label, [{'label': label, 'valor': value, 'estado': status,
                                          'ai_register': True}])


# ----------------------------------------------------------------------------- 7. hsi

def _reachable(url, timeout=_TIMEOUT):
    """¿Responde algo en `url`? Cualquier respuesta HTTP (incluso 401/404/302)
    cuenta como alcanzable — el server contestó. HSI no expone un endpoint de
    salud limpio (la raíz es la SPA), así que sólo nos importa que conteste.
    Timeout/URLError = caído. Nunca levanta."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout):
            return True, None
    except urllib.error.HTTPError as e:
        e.close()  # swallowed without reading: close the body it holds
        return True, None  # contestó, aunque sea un error de aplicación
    except (urllib.error.URLError, socket.timeout) as e:
        return False, _t("no_connection", "sin conexión ({reason})", reason=getattr(e, 'reason', e))
    except OSError as e:  # noqa: BLE001
        return False, str(e)[:120]


def _hsi_centers_item(estado_hsi):
    """The row that names each center's institution number in HSI, or None
    without centers. The number is read by the desk's browser extension
    (the server-side push still sends to one institution per installation),
    so a center without it is a warning only where the extension is the way
    in; elsewhere the row still names it, in neutral."""
    from app.admin_panel import hsi_status

    if not estado_hsi["total"]:
        return None
    faltan = estado_hsi["con_numero"] < estado_hsi["total"]
    if not faltan:
        estado = "na" if estado_hsi["modo"] == hsi_status.OFF else "ok"
    else:
        estado = "warn" if estado_hsi["modo"] == hsi_status.EXTENSION else "na"
    return {
        "label": _t("hsi.centers", "Instituciones en HSI"),
        "valor": estado_hsi["centros_texto"],
        "estado": estado,
        "detalle": estado_hsi["centros_detalle"],
        "enlace": {"href": "/admin/hsi", "texto": _t("hsi.centers_link", "Ver por centro →")},
    }


def _cluster_hsi():
    """HSI es el sistema de turnos de referencia; acá se ve si la sincronización
    está conectada, cuántos turnos futuros ya están agendados en HSI y cuántos
    conflictos esperan una persona. En la nube HSI vive en la red del hospital
    (TURNOS_SKIP_HSI): ahí el cluster es neutro, no un error.

    Sin conector EMR seleccionado (CONNECTOR_EMR_SYNC sin valor, `off` o
    inválido) el cluster es neutro y NO se probea nada: sin selección no hay
    URL por defecto ni pedido de red alguno — Core no habla con ningún EMR
    que nadie haya elegido. El Enterprise bridge tampoco se presenta como HSI:
    su ciclo de vida usa el journal genérico, pero su salud no puede heredar
    una URL, una sonda ni métricas de HSI (EMR boundary, 2026-09-01). La
    selección se lee del registro genérico de conectores, nunca de HSI_*.

    The card is always there (2026-11-10, slice 7 «HSI is visible in the
    panel»): its first row names HOW a turno reaches HSI on this installation
    — the server sends it, the desk loads it with the browser extension, or
    nothing does — and why; its last row names each center's institution
    number in HSI. Without the server-side push nothing is probed: those two
    rows read the registry, two settings, the extension's files and the
    catalog (`admin_panel/hsi_status.py`). Only a selected Enterprise bridge
    hides it: that one has its own card."""
    from app.admin_panel import hsi_status

    estado_hsi = hsi_status.snapshot()
    if estado_hsi["conector"] and estado_hsi["conector"] != "hsi":
        # The selected Enterprise bridge has its own URL, health contract and
        # credential boundary. Its generic EMR cluster is built separately;
        # never display an HSI card with a fabricated HSI_URL/default.
        return None

    modo = estado_hsi["modo"]
    items = [{
        "label": _t("hsi.mode", "Modo"),
        "valor": estado_hsi["modo_texto"],
        # «Apagado» is neutral, not a fault: an installation may not use HSI.
        "estado": "na" if modo == hsi_status.OFF else "ok",
        "detalle": estado_hsi["motivo_texto"],
    }]
    centros = _hsi_centers_item(estado_hsi)

    if modo != hsi_status.SERVER:
        if centros:
            items.append(centros)
        resumen = (_t("hsi.summary_extension", "La recepción carga los turnos en HSI con la extensión.")
                   if modo == hsi_status.EXTENSION
                   else _t("hsi.summary_off", "Los turnos no llegan a HSI."))
        return _cluster("hsi", _t("hsi.title", "HSI (turnos)"), items, resumen)

    url = _env("HSI_URL") or "http://localhost:8280"
    daemon_off = _env_bool_off("TURNOS_HSI_SYNC_OFF")

    items.append({
        "label": _t("hsi.sync", "Sincronización"),
        "valor": _t("off_f", "Apagada") if daemon_off else _t("active_f", "Activa"),
        "estado": "warn" if daemon_off else "ok",
        "detalle": _t("hsi.sync_off", "La sincronización automática con HSI está apagada.") if daemon_off else None,
    })

    # Conectividad con la instancia de HSI (probe propio, corto — no usamos
    # hsi_client, que tiene timeout de 30 s y colgaría la página).
    ok_red, det_red = _reachable(url)
    host = urlparse(url).hostname or url
    items.append({
        "label": _t("connectivity", "Conectividad"),
        "valor": (_t("hsi.responds", "Responde ({host})", host=host) if ok_red
                  else _t("hsi.no_response", "No responde ({host})", host=host)),
        "estado": "ok" if ok_red else "error",
        "campo": None if ok_red else "HSI_URL",
        "detalle": det_red if not ok_red else None,
    })

    # Métricas de agendado + cola durable (requieren la base; si está caída,
    # degradá a warn, nunca 500).
    try:
        # One reading of the journal, the same one «Conectores» shows.
        diario = hsi_status.read_journal()
        m = diario["metricas"]
        futuras = m.get("futuras") or 0
        futuras_ok = m.get("futuras_ok") or 0
        ultima = diario["ultima"]
        if ultima:
            # `cuando` is the instant with the installation's offset; the
            # screen writes it «hace 5 minutos» in the reader's language, or
            # the day and the hour of the installation, never the reader's
            # clock (one formatter, platform/dates.mjs `instanteServidor`).
            items.append({
                "label": _t("hsi.last", "Última transmisión"),
                "valor": ultima.strftime("%Y-%m-%d %H:%M"),
                "cuando": ultima.isoformat(), "estado": "ok",
            })
        else:
            items.append({"label": _t("hsi.last", "Última transmisión"),
                          "valor": _t("hsi.last_none", "Todavía ninguna"), "estado": "na"})
        if futuras:
            pct = round(100 * futuras_ok / futuras)
            est = "ok" if futuras_ok >= futuras else "warn"
            items.append({
                "label": _t("hsi.future", "Turnos futuros en HSI"),
                "valor": _t("hsi.future_value", "{ok} de {total} ({pct}%)", ok=futuras_ok, total=futuras, pct=pct),
                "estado": est,
                "detalle": None if est == "ok" else _t("hsi.retrying", "El sistema reintenta solo los que faltan cada unos minutos."),
            })
        else:
            items.append({"label": _t("hsi.future", "Turnos futuros en HSI"), "valor": _t("hsi.no_future", "Sin turnos futuros"),
                          "estado": "ok"})

        pendientes = diario["en_cola"]
        errores = diario["errores"]
        if pendientes or errores:
            val = (_t("hsi.queued_errors", "{n} en cola, {errors} con error", n=pendientes, errors=errores) if errores
                   else _t("hsi.queued", "{n} en cola", n=pendientes))
            items.append({"label": _t("hsi.queue", "Cola de reintentos"), "valor": val,
                          "estado": "warn" if errores else "ok",
                          "detalle": _t("hsi.queue_detail", "Turnos esperando reintento hacia HSI.")})
        else:
            items.append({"label": _t("hsi.queue", "Cola de reintentos"), "valor": _t("hsi.up_to_date", "Al día"), "estado": "ok"})

        abiertos = diario["conflictos"]
        items.append({
            "label": _t("hsi.conflicts", "Conflictos sin resolver"),
            "valor": str(abiertos) if abiertos else _t("hsi.none", "Ninguno"),
            "estado": "warn" if abiertos else "ok",
            "detalle": _t("hsi.conflicts_detail", "Requieren revisión humana en la sección «HSI» del panel.") if abiertos else None,
        })
    except Exception:  # noqa: BLE001 — base caída/driver ausente: no rompas la página
        items.append({"label": _t("hsi.metrics", "Métricas"), "valor": _t("hsi.metrics_unavailable", "No disponibles"),
                      "estado": "warn",
                      "detalle": _t("hsi.metrics_detail", "No se pudo leer la base para las métricas de HSI.")})

    if centros:
        items.append(centros)

    resumen = {"error": _t("hsi.summary_error", "No hay conexión con HSI."),
               "warn": _t("hsi.summary_warn", "HSI conectado; hay turnos o conflictos por resolver."),
               "ok": _t("hsi.summary_ok", "Turnos sincronizados con HSI."),
               "na": _t("hsi.summary_na", "HSI conectado.")}[_worst([i["estado"] for i in items])]
    return _cluster("hsi", _t("hsi.title", "HSI (turnos)"), items, resumen)


def _cluster_emr_bridge():
    """Status for the explicitly selected Enterprise bridge, never HSI.

    Missing bridge configuration is an operator-visible degraded state, but
    cannot generate a network call: the endpoint and token are required
    before the generic adapter's health method is reached.
    """
    from app import connectors

    if connectors.resolve(connectors.EMR_SYNC)[0] != "emr_bridge":
        return None
    url = _env("EMR_BRIDGE_URL")
    token = _env("EMR_BRIDGE_TOKEN")
    if not url or not token:
        # `campo` keeps the setting name for the «Configurar →» deep link;
        # what staff read names the thing, never the setting.
        missing = []
        if not url:
            missing.append(("EMR_BRIDGE_URL", _t("emr.url", "la dirección del bridge")))
        if not token:
            missing.append(("EMR_BRIDGE_TOKEN", _t("emr.token", "el token del bridge")))
        return _cluster("emr_bridge", "EMR bridge", [{
            "label": _t("emr.config", "Configuración"),
            "valor": _t("emr.incomplete", "Incompleta"),
            "estado": "warn",
            "campo": missing[0][0],
            "detalle": (_t("emr.missing_two", "Faltan {a} y {b}: cargalos en Ajustes › Integraciones. "
                           "No se hizo ninguna conexión.", a=missing[0][1], b=missing[1][1])
                        if len(missing) > 1 else
                        _t("emr.missing_one", "Falta {a}: cargalo en Ajustes › Integraciones. "
                           "No se hizo ninguna conexión.", a=missing[0][1])),
        }], _t("emr.not_configured", "El bridge EMR está seleccionado pero todavía no está configurado."))
    adapter = connectors.adapter_for(connectors.EMR_SYNC)
    if adapter is None:
        return _cluster("emr_bridge", "EMR bridge", [{
            "label": _t("emr.connector", "Conector"),
            "valor": _t("unavailable", "No disponible"),
            "estado": "error",
            "detalle": _t("emr.load_failed", "No se pudo cargar el adaptador EMR bridge seleccionado."),
        }], _t("emr.unavailable", "El bridge EMR seleccionado no está disponible."))
    try:
        adapter.health()
    except connectors.ProviderUnavailable:
        return _cluster("emr_bridge", "EMR bridge", [{
            "label": _t("connectivity", "Conectividad"),
            "valor": _t("emr.no_response", "No responde"),
            "estado": "error",
            "campo": "EMR_BRIDGE_URL",
            "detalle": _t("emr.no_response_detail", "El bridge no respondió o devolvió un estado inválido."),
        }], _t("emr.no_connection", "No hay conexión con el bridge EMR."))
    return _cluster("emr_bridge", "EMR bridge", [{
        "label": _t("connectivity", "Conectividad"),
        "valor": _t("emr.responds", "Responde"),
        "estado": "ok",
    }], _t("emr.ok", "Bridge EMR conectado."))


# ----------------------------------------------------------------------------- payload

def status(request):
    hsi_cluster = _cluster_hsi()
    emr_bridge_cluster = _cluster_emr_bridge()
    clusters = [
        _cluster_app(),
        _cluster_domain(request),
        _cluster_whatsapp(),
        _cluster_email(),
        _cluster_storage(),
        _cluster_openrouter(),
        _cluster_renaper(),
        _cluster_puco(),
        _cluster_backup(),
        _cluster_ai_register(),
    ]
    if hsi_cluster is not None:
        clusters.insert(1, hsi_cluster)
    elif emr_bridge_cluster is not None:
        clusters.insert(1, emr_bridge_cluster)
    ok = sum(1 for c in clusters if c["estado"] == "ok")
    return {
        "estado": _worst([c["estado"] for c in clusters]),
        "ok": ok,
        "total": len(clusters),
        "clusters": clusters,
    }
