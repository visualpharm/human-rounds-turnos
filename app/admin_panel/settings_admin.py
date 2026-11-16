"""settings_admin.py — sección «Settings» del panel (fase 4 del rework de
open-sourcing, 2026-07-30): un único lugar admin-editable, en vivo, para lo
que hasta ahora sólo se podía tocar redeployando con otro `.env` — Location
(qué paquete de datos y qué identidad), Languages (i18n.py) y las API keys
de cada integración externa.

Persistencia: config.py's DB layer (`catalogo` tipo="config" id="settings"),
la MISMA fila que ya usa cualquier `config.get()` del resto del código — este
módulo no inventa un storage nuevo, sólo un formulario sobre el que ya
existe. Todo lo que se guarda acá por lo tanto también puede setearse por
env/.env (o congelarse con `TURNOS_CONFIG_LOCK=1`, ver config.py).

Secretos: NUNCA se devuelven en el GET. Cada campo "secret" del registro de
abajo se reporta como `{"configurado": bool, "last4": "..."}`, nunca el
valor real. El POST sólo pisa un campo secreto si vino un valor no-vacío en
`valores`; para BORRAR uno hay que nombrarlo en `borrar` (una lista aparte) —
así un campo vacío en el formulario nunca se interpreta por accidente como
"borrar la key que ya estaba guardada".

Gate: `core.deps.require_admin(solo_admin=True)` en el router nativo —
ve infraestructura (dominios de webhook, ids de bucket), no sólo "quién es
médico puede pasar".
"""

import re
from urllib.parse import urlparse

from app.core import config, backup_lock, backup_settings
from app.core import data_packs
from app.core import i18n
from app.auth import ip_exempt
from app.core import site_identity
from app.ai import ai_client
from app.ai import ai_health
from app.admin_panel import system_status
from app import connectors

_API = "/api/admin/settings"
_API_PROBAR = "/api/admin/settings/probar"


# ============================================================== registro

# (clave, etiqueta, tipo) — tipo ∈ "text" | "secret" | "profile". "secret" nunca se
# devuelve en el GET y sólo se pisa si `valores` trae un string no vacío.
INTEGRATIONS = [
    {"id": "backup", "titulo": "Copias de seguridad", "probar": "backup", "campos": [
        (key, labels[1], "secret" if key in backup_settings.PROTECTED else
         "toggle" if key in backup_settings.BOOL_KEYS else
         "number" if key.endswith(("_MIN", "_DAILY", "_WEEKLY", "_MONTHLY", "_HOURS", "_DAYS")) else "text")
        for key, labels in backup_settings.FIELD_LABELS.items()
    ]},
    {"id": "openrouter", "titulo": "AI inference", "probar": "openrouter", "campos": [
        ("AI_INFERENCE_PROFILE", "Inference profile", "profile"),
        ("TURNOS_AI_DISABLED", "Desactivar toda la inferencia de IA", "toggle"),
        ("AI_INFERENCE_BASE_URL", "Endpoint URL (required for non-cloud profiles)", "text"),
        ("AI_INFERENCE_API_KEY", "Dedicated primary key", "secret"),
        ("AI_INFERENCE_API_KEY_BACKUP", "Dedicated backup key (retries after 402/403)", "secret"),
        ("OPENROUTER_API_KEY", "Cloud shared legacy primary key", "secret"),
        ("OPENROUTER_API_KEY_BACKUP", "Cloud shared legacy backup key (retries after 402/403)", "secret"),
        ("OPENROUTER_MANAGEMENT_KEY", "Management key (opcional — saldo de la cuenta)", "secret"),
        ("TURNOS_AI_MODEL", "Modelo principal", "text"),
        ("TURNOS_AI_MODEL_FALLBACK", "Modelo de respaldo (gratis, best-effort)", "text"),
    ]},
    # One email card: the provider choice (`email_transport` connector —
    # empty = Mailgun, the default) plus both providers' keys; «Probar»
    # checks whichever provider is selected.
    {"id": "email", "titulo": "Email", "probar": "email", "campos": [
        ("CONNECTOR_EMAIL_TRANSPORT", "Proveedor", "choice"),
        ("MAIL_FROM", "Remitente (nombre <email>)", "text"),
        ("MAIL_REPLY_TO", "Responder a (email)", "text"),
        ("MAILGUN_API_KEY", "Mailgun — API key", "secret"),
        ("MAILGUN_DOMAIN", "Mailgun — dominio de envío", "text"),
        ("MAILGUN_API_BASE", "Mailgun — región (vacío = US; EU: https://api.eu.mailgun.net)", "text"),
        ("MAILJET_API_KEY", "Mailjet — API key", "secret"),
        ("MAILJET_API_SECRET", "Mailjet — secret key", "secret"),
    ]},
    {"id": "turnstile", "titulo": "Turnstile (anti-bot)", "probar": None, "campos": [
        ("TURNOS_TURNSTILE_SITEKEY", "Site key", "text"),
        ("TURNOS_TURNSTILE_SECRET", "Secret key", "secret"),
    ]},
    {"id": "fal", "titulo": "Fal (dictado del chat)", "probar": None, "campos": [
        ("FAL_KEY", "API key", "secret"),
    ]},
    {"id": "whatsapp", "titulo": "WhatsApp (OTP + chat)", "probar": None, "campos": [
        ("TURNOS_WA_PROVIDER", "Proveedor (meta en producción; waha sólo dev loopback; vacío = Meta si hay token, si no nada)", "text"),
        ("META_WA_TOKEN", "Meta Cloud API — token", "secret"),
        ("META_WA_PHONE_NUMBER_ID", "Meta Cloud API — número emisor", "text"),
        ("TURNOS_WA_WEBHOOK_SECRET", "Meta Cloud API — secreto del webhook", "secret"),
        ("META_WA_APP_SECRET", "Meta Cloud API — app secret (firma de los webhooks entrantes)", "secret"),
        ("META_WA_OTP_TEMPLATE", "Meta Cloud API — plantilla de Authentication aprobada para el código (con OTP_PROVIDER=meta; vacío = el código no sale)", "text"),
        ("OTP_WAHA_URL", "WAHA — gateway URL", "text"),
        ("OTP_WAHA_API_KEY", "WAHA — API key", "secret"),
        ("OTP_WAHA_SESSION", "WAHA — sesión", "text"),
        ("TURNOS_WA_NUMERO", "Número público (handoff en el chat)", "text"),
        ("TURNOS_WA_NOTIFY", "Avisos a pacientes: confirmación y recordatorio (1 = encendidos; vacío = apagados)", "text"),
        ("TURNOS_WA_TEST_RECIPIENTS", "Avisos sólo a estos teléfonos de prueba (separados por coma)", "text"),
    ]},
    {"id": "puco", "titulo": "SISA / PUCO (obra social por DNI)", "probar": None, "campos": [
        ("TURNOS_SISA_URL", "URL del servicio WS131 (vacío = el oficial)", "text"),
        ("TURNOS_PUCO_PUBLIC", "Consulta pública GWT-RPC de respaldo (1=on, 0=off)", "text"),
    ]},
    {"id": "renaper", "titulo": "RENAPER / Mi Argentina (login federado)", "probar": None, "campos": [
        ("TURNOS_RENAPER_CLIENT_ID", "Client ID", "text"),
        ("TURNOS_RENAPER_CLIENT_SECRET", "Client secret", "secret"),
        ("TURNOS_RENAPER_AUTHORIZE_URL", "Authorize URL", "text"),
        ("TURNOS_RENAPER_TOKEN_URL", "Token URL", "text"),
        ("TURNOS_RENAPER_USERINFO_URL", "Userinfo URL", "text"),
        ("TURNOS_RENAPER_LABEL", "Texto del botón", "text"),
    ]},
    # `capability` marks an integration card that belongs to a connector
    # capability: the card only exists while that capability routes a
    # selected connector (resolve() — explicit-only for emr_sync). Without
    # a selection the card is not exposed and its probe refuses to run
    # (EMR boundary, 2026-09-01).
    {"id": "hsi", "titulo": "HSI (sistema de turnos del hospital)", "probar": "hsi",
     "capability": "emr_sync", "connector_id": "hsi", "campos": [
        ("HSI_URL", "URL", "text"),
        ("HSI_EMAIL", "Usuario", "text"),
        ("HSI_PASS", "Contraseña", "secret"),
        ("HSI_INSTITUTION_ID", "ID de institución", "text"),
    ]},
    {"id": "emr_bridge", "titulo": "EMR bridge", "probar": "emr_bridge",
     "capability": "emr_sync", "connector_id": "emr_bridge", "campos": [
        ("EMR_BRIDGE_URL", "Bridge URL", "text"),
        ("EMR_BRIDGE_TOKEN", "Bridge token", "secret"),
    ]},
    {"id": "b2", "titulo": "Backblaze B2 (backup de data/)", "probar": None, "campos": [
        ("TURNOS_B2_KEY_ID", "Key ID", "text"),
        ("TURNOS_B2_APP_KEY", "Application key", "secret"),
        ("TURNOS_B2_BUCKET_ID", "Bucket ID", "text"),
        ("TURNOS_B2_PREFIX", "Prefijo dentro del bucket (default data/)", "text"),
    ]},
]

_ALL_FIELDS = {k: (label, tipo) for grupo in INTEGRATIONS for (k, label, tipo) in grupo["campos"]}

# Closed option lists of the "choice" fields: (stored value, label). The
# empty value stores nothing, so the env/.env value or the connector default
# applies — a Mailgun installation never pins "mailgun" in the DB, where it
# would silently outrank a later CONNECTOR_EMAIL_TRANSPORT=mailjet in env.
_CHOICES = {
    "CONNECTOR_EMAIL_TRANSPORT": (("", "Mailgun"), ("mailjet", "Mailjet")),
}
_CHOICE_ALIASES = {"CONNECTOR_EMAIL_TRANSPORT": {"mailgun": ""}}
_INFERENCE_FIELDS = ("AI_INFERENCE_PROFILE", "AI_INFERENCE_BASE_URL")

LANG_KEYS = ("LANGUAGES", "LANG_DEFAULT", "LANG_SWITCHER_MODE")
LOCATION_KEYS = ("PACK_CHAIN",) + tuple("SITE_ID_" + k.upper() for k in site_identity.OVERRIDABLE_KEYS)
CHAT_GREETING_MAX = 1200


# ============================================================== helpers

def _mask_last4(value):
    v = str(value or "")
    return v[-4:] if len(v) >= 4 else None


def _capability_selected(capability, connector_id=None):
    """True when the registry routes a connector for `capability` — the
    generic connector registry's own answer, never a local parse of the
    CONNECTOR_* key. Gates capability-owned integration cards (emr_sync
    today): an installation that selected nothing shows no HSI settings
    surface at all."""
    selected, _source = connectors.resolve(capability)
    return bool(selected) and (connector_id is None or selected == connector_id)


def _connectors_payload():
    """Public, non-secret connector state for the Settings UI.

    Institutional integrations are explicit-only.  The UI therefore exposes
    one deliberate switch for the Pinamar HSI adapter instead of inferring it
    from installed packages, credentials, or the legacy emergency flag.
    """
    from app.admin_panel import hsi_status

    selected, source = connectors.resolve(connectors.EMR_SYNC)
    return {
        "emr_sync": {
            "selected": selected,
            "source": source,
            "hsi_enabled": selected == "hsi",
        },
        # How a turno reaches HSI here, in words — the same one reading
        # «Sistema» and /admin/hsi show (admin_panel/hsi_status.py), so the
        # three can never disagree. In server mode it carries the journal's
        # last transmission, queue and errors.
        "hsi": hsi_status.snapshot(con_diario=True),
    }


def _api_keys_payload():
    # Etiquetas/títulos NO se leen directo de INTEGRATIONS (eso sería una
    # constante de módulo congelada en español al importar — mismo bug ya
    # visto en patient_web/site_*.py): se resuelven acá, por request, con la clave
    # de config/id de integración como sufijo del key de i18n (en minúsculas,
    # no hace falta mantener una lista aparte de nombres).
    lang = i18n.active()
    grupos = []
    for grupo in INTEGRATIONS:
        capability = grupo.get("capability")
        if capability and not _capability_selected(capability, grupo.get("connector_id")):
            continue
        titulo = i18n.t_or(f"admin.srv.settings.integration.{grupo['id']}.titulo", grupo["titulo"], lang)
        campos = []
        for key, label, tipo in grupo["campos"]:
            etiqueta = i18n.t_or(f"admin.srv.settings.field.{key.lower()}", label, lang)
            actual = config.get(key, backup_settings.DEFAULTS.get(key))
            if tipo == "secret":
                campos.append({"clave": key, "etiqueta": etiqueta, "tipo": tipo,
                               "configurado": bool(actual), "last4": _mask_last4(actual)})
            elif tipo == "choice":
                valor = _CHOICE_ALIASES.get(key, {}).get(actual or "", actual or "")
                opciones = [{"valor": v, "etiqueta": e} for v, e in _CHOICES[key]]
                if valor not in [o["valor"] for o in opciones]:
                    opciones.append({"valor": valor, "etiqueta": valor})  # a stored off/typo stays visible
                campos.append({"clave": key, "etiqueta": etiqueta, "tipo": tipo,
                               "valor": valor, "opciones": opciones})
            else:
                campos.append({"clave": key, "etiqueta": etiqueta, "tipo": tipo,
                               "valor": actual or ""})
        result = {"id": grupo["id"], "titulo": titulo,
                  "probar": grupo["probar"], "campos": campos}
        if grupo["id"] == "backup":
            result["key_status"] = backup_settings.key_status()
        grupos.append(result)
    return grupos


def _location_payload():
    disponibles = data_packs.list_packs()
    chain = data_packs.active_chain()
    active_ids = set(chain or [])
    # Scaffold/example packs belong in their own demo installation, not in a
    # real deployment's selectable chain. Keep the active one visible so a
    # demo can still edit its own configuration without losing its source.
    disponibles = [p for p in disponibles if not p.get("example") or p.get("id") in active_ids]
    identidad = {k: site_identity.get(k) for k in site_identity.OVERRIDABLE_KEYS}
    return {
        "modo_paquetes": chain is not None,
        "cadena_actual": list(chain) if chain else [],
        "paquetes_disponibles": disponibles,
        "identidad": identidad,
        "centros": _center_choices(),
    }


def _center_choices():
    """The installation's centers for «Centro principal»: [{slug, nombre}]."""
    from app.patient_web import site_centers
    try:
        rows = site_centers._centers()
    except Exception:  # noqa: BLE001 — no catalog: the field just offers nothing
        return []
    return [{"slug": c["slug"], "nombre": c.get("name") or c["slug"]}
            for c in rows if isinstance(c, dict) and c.get("slug")]


def _languages_payload():
    return {
        "disponibles": i18n.available_locales(),
        "nombres": {code: i18n.language_name(code) for code in i18n.available_locales()},
        "habilitados": i18n.enabled_languages(),
        "default": i18n._configured_default(),
        "modo_switcher": i18n.switcher_mode(),
    }


def _chat_key(lang):
    return f"CHAT_GREETING_{str(lang or '').strip().upper()}"


def _chat_payload():
    greetings = []
    for lang in i18n.available_locales():
        key = _chat_key(lang)
        custom = config.get(key)
        greetings.append({
            "lang": lang,
            "key": key,
            "value": custom if custom not in (None, "") else i18n.raw("chat.greeting", lang),
            "custom": custom not in (None, ""),
        })
    return {"greetings": greetings, "max_length": CHAT_GREETING_MAX}


def _guardar_chat(valores):
    allowed = {_chat_key(lang) for lang in i18n.available_locales()}
    out = {}
    for key, raw in valores.items():
        if key not in allowed:
            continue
        value = str(raw or "").strip()
        if len(value) > CHAT_GREETING_MAX:
            return None, i18n.t_or(
                "admin.srv.settings.chat.saludo_largo",
                "el saludo del chat puede tener hasta {max} caracteres",
                i18n.active(), max=CHAT_GREETING_MAX)
        out[key] = value
    config.guardar(out)
    return _chat_payload(), None


def _turnos_payload():
    from app.modules.turnos import booking_limits
    from app.modules.turnos import booking_horizon
    return {"max_activos": booking_limits.site_default(),
            "dias_anticipacion": booking_horizon.site_default(),
            "apertura_turnos": config.get("BOOKING_OPENS_AT", ""),
            "photo_auto_enhance": config.get("TURNOS_PHOTO_AUTO_ENHANCE", "1") != "0"}


def _guardar_turnos(valores):
    from app.modules.turnos import booking_limits
    out = {}
    if 'TURNOS_PHOTO_AUTO_ENHANCE' in valores:
        value = str(valores['TURNOS_PHOTO_AUTO_ENHANCE'])
        if value not in ('0', '1'):
            return None, i18n.t_or('admin.doctor.invalid_setting', 'Configuración de fotos inválida.', i18n.active())
        out['TURNOS_PHOTO_AUTO_ENHANCE'] = value
    if "MAX_ACTIVE_REQUESTS" in valores:
        raw = str(valores.get("MAX_ACTIVE_REQUESTS") or "").strip()
        try:
            n = int(raw)
        except ValueError:
            return None, i18n.t_or("admin.srv.settings.turnos_no_numero",
                                    "el máximo de turnos tiene que ser un número", i18n.active())
        if not 1 <= n <= booking_limits.MAX_LIMIT:
            return None, i18n.t_or("admin.srv.settings.turnos_rango", "el máximo de turnos va de 1 a {max}",
                                    i18n.active(), max=booking_limits.MAX_LIMIT)
        out["MAX_ACTIVE_REQUESTS"] = str(n)
    from app.modules.turnos import booking_horizon
    if "BOOKING_HORIZON_DAYS" in valores:
        raw = str(valores.get("BOOKING_HORIZON_DAYS") or "").strip()
        n = booking_horizon._clamp(raw)
        if n is None or str(n) != raw:
            return None, i18n.t_or("admin.settings.horizon_range", "Los días de anticipación van de 1 a 365.", i18n.active())
        out["BOOKING_HORIZON_DAYS"] = str(n)
    if "BOOKING_OPENS_AT" in valores:
        value = str(valores.get("BOOKING_OPENS_AT") or "").strip()
        if value and not booking_horizon._TIME.fullmatch(value):
            return None, i18n.t_or("admin.settings.opening_time_format", "La hora de apertura debe tener formato HH:MM.", i18n.active())
        out["BOOKING_OPENS_AT"] = value
    config.guardar(out)
    return _turnos_payload(), None


def _feriados_payload():
    """Site holidays (site_holidays.py) with how many appointments each date
    already holds — they are never cancelled automatically, the desk moves
    them — and whether the country's official list is available."""
    from app.modules.turnos import site_holidays
    items = site_holidays.items()
    counts = site_holidays.appointment_counts([h["fecha"] for h in items])
    return {"items": [dict(h, turnos=counts.get(h["fecha"], 0)) for h in items],
            "nacionales": bool(site_holidays.national()["items"])}


def _guardar_feriados(valores):
    """`{"items": [...]}` replaces the whole list; with `"nacionales": true`
    the official holidays from today on that the list still lacks are added
    to it (to the stored list when no `items` come along)."""
    from app.modules.turnos import site_holidays
    items = valores.get("items") if "items" in valores else None
    try:
        if valores.get("nacionales") is True:
            base = site_holidays.items() if items is None else site_holidays.clean(items)
            items = site_holidays.merge_national(base)
        elif items is None:
            raise site_holidays.HolidayError("admin.settings.holidays.invalid", "La lista de feriados no es válida.")
        site_holidays.save(items)
    except site_holidays.HolidayError as exc:
        return None, i18n.t_or(exc.key, exc.fallback, i18n.active(), **exc.vars)
    return _feriados_payload(), None


def _seguridad_payload():
    return {"ips_exentas": ip_exempt.list_exempt()}


def _guardar_seguridad(valores):
    out = {}
    if "IPS_EXENTAS" in valores:
        ips, err = ip_exempt.parse_lista(valores.get("IPS_EXENTAS"))
        if err:
            return None, err
        out[ip_exempt.CONFIG_KEY] = ",".join(ips)
    config.guardar(out)
    return _seguridad_payload(), None


def _payload():
    return {
        "lock": config.locked(),
        "location": _location_payload(),
        "languages": _languages_payload(),
        "chat": _chat_payload(),
        "turnos": _turnos_payload(),
        "feriados": _feriados_payload(),
        "seguridad": _seguridad_payload(),
        "connectors": _connectors_payload(),
        "api_keys": _api_keys_payload(),
    }


def _guardar_connectors(valores):
    enabled = valores.get("HSI_ENABLED")
    if not isinstance(enabled, bool):
        return None, i18n.t_or(
            "admin.srv.settings.connectors.invalid_state",
            "el estado del conector tiene que ser verdadero o falso",
            i18n.active())
    if enabled:
        connectors.enable("hsi")
    else:
        connectors.disable(connectors.EMR_SYNC)
    return _connectors_payload(), None


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _valid_pack_chain(raw):
    """A pack chain the way PACK_CHAIN is parsed elsewhere: comma-separated
    ids, only slug-shaped tokens (no path traversal, no whitespace tricks)."""
    ids = [p.strip() for p in (raw or "").split(",") if p.strip()]
    return all(_SLUG_RE.match(i) for i in ids)


def _guardar_location(valores):
    out = {}
    if "PACK_CHAIN" in valores:
        raw = str(valores.get("PACK_CHAIN") or "").strip()
        if raw and not _valid_pack_chain(raw):
            return None, i18n.t_or("admin.srv.settings.location.cadena_invalida",
                                    "cadena de paquetes inválida", i18n.active())
        out["PACK_CHAIN"] = raw
    if "SITE_ID_MAIN_CENTER" in valores:
        slug = str(valores.get("SITE_ID_MAIN_CENTER") or "").strip()
        if slug and slug not in {c["slug"] for c in _center_choices()}:
            return None, i18n.t_or("admin.srv.settings.location.centro_desconocido",
                                    "centro principal desconocido", i18n.active())
    for k in site_identity.OVERRIDABLE_KEYS:
        env_key = "SITE_ID_" + k.upper()
        if env_key in valores:
            out[env_key] = str(valores.get(env_key) or "").strip()
    config.guardar(out)
    data_packs.invalidate()
    return _location_payload(), None


def _guardar_languages(valores):
    out = {}
    if "LANGUAGES" in valores:
        codes = [c.strip().lower() for c in str(valores.get("LANGUAGES") or "").split(",") if c.strip()]
        have = set(i18n.available_locales())
        malas = [c for c in codes if c not in have]
        if malas:
            return None, i18n.tn_or("admin.srv.settings.languages.sin_catalogo", len(malas),
                                    "idioma sin catálogo: {codigos}", "idiomas sin catálogo: {codigos}",
                                    i18n.active(), codigos=", ".join(malas))
        out["LANGUAGES"] = ",".join(codes)
    if "LANG_DEFAULT" in valores:
        out["LANG_DEFAULT"] = str(valores.get("LANG_DEFAULT") or "").strip().lower()
    if "LANG_SWITCHER_MODE" in valores:
        modo = str(valores.get("LANG_SWITCHER_MODE") or "off").strip().lower()
        if modo not in ("off", "nav", "secret"):
            return None, i18n.t_or("admin.srv.settings.languages.modo_invalido",
                                    "modo de switcher inválido", i18n.active())
        out["LANG_SWITCHER_MODE"] = modo
    config.guardar(out)
    return _languages_payload(), None


def _guardar_api_keys(valores, borrar):
    touched = set(valores or {}) | set(borrar or [])
    if any(k.startswith("TURNOS_BACKUP_") for k in touched) and config.locked():
        return None, i18n.t_or("backup.locked", "La configuración de copias está bloqueada", i18n.active())
    out = {}
    for key in borrar or []:
        if key in _ALL_FIELDS:
            out[key] = ""  # config.guardar(): "" borra
    for key, val in (valores or {}).items():
        if key not in _ALL_FIELDS or key in out:
            continue
        _, tipo = _ALL_FIELDS[key]
        if key == 'TURNOS_AI_DISABLED':
            if val is True:
                val = '1'
            elif val is False:
                val = '0'
            if str(val or '') not in ('', '0', '1'):
                return None, i18n.t_or('ai.register.invalid_switch', 'La desactivación de IA debe ser 1 o 0.', i18n.active())
        val = str(val or "")
        if tipo == "secret" and not val:
            continue  # campo vacío en un secreto = "no lo toques"
        if tipo == "choice":
            val = _CHOICE_ALIASES.get(key, {}).get(val.strip().lower(), val.strip().lower())
            if val not in [v for v, _ in _CHOICES[key]]:
                return None, i18n.t_or("admin.srv.settings.opcion_invalida",
                                        "valor no permitido para {campo}", i18n.active(), campo=key)
        out[key] = val
    candidate = {key: config.get(key) or "" for key in _INFERENCE_FIELDS}
    candidate.update({key: value for key, value in out.items() if key in candidate})
    profile = str(candidate["AI_INFERENCE_PROFILE"] or "cloud_shared").strip().lower()
    base_url = str(candidate["AI_INFERENCE_BASE_URL"] or "").strip()
    if profile == "cloud_shared" and base_url:
        if "AI_INFERENCE_BASE_URL" in valores:
            return None, "Cloud shared endpoint cannot be changed."
        # A former non-cloud endpoint is inactive under cloud_shared. Clear it
        # when the profile switches so Settings never stores an editable cloud
        # destination for a later accidental reuse.
        candidate["AI_INFERENCE_BASE_URL"] = ""
        out["AI_INFERENCE_BASE_URL"] = ""
        base_url = ""
    error = ai_client.validate_destination(profile, base_url)
    if error:
        return None, error
    if "AI_INFERENCE_PROFILE" in out:
        out["AI_INFERENCE_PROFILE"] = profile
    if "AI_INFERENCE_BASE_URL" in out:
        out["AI_INFERENCE_BASE_URL"] = base_url
    try:
        backup_settings.validate({k: v for k, v in out.items() if k.startswith("TURNOS_BACKUP_")})
    except ValueError:
        return None, i18n.t_or("backup.invalid_settings", "Revisá el endpoint HTTPS, el prefijo y los intervalos", i18n.active())
    try:
        backup_settings.refuse_locked(out)
    except backup_lock.BackupWriteForbidden:
        return None, i18n.t_or("backup.forbidden", backup_lock.FORBIDDEN_ES, i18n.active())
    config.guardar(out)
    return _api_keys_payload(), None


# ============================================================== probar

def _probar_openrouter():
    lang = i18n.active()
    ai_client.reset_sticky_for_tests()
    ok = ai_health.probe()
    return {"ok": ok, "key_activa": ai_client.active_key_kind(),
            # On success the server's line, like HSI and the mail provider
            # (reuses the HSI «{host}: responde.» key, all languages exist).
            "detalle": i18n.t_or(
                "admin.srv.settings.probar.hsi_ok", "{host}: responde.", lang, host="OpenRouter")
            if ok else i18n.t_or(
                "admin.srv.settings.probar.openrouter_sin_respuesta",
            "AI inference probe failed. Check the profile and credentials.", lang)}


def _probar_email():
    """Checks the SELECTED provider (`email_transport`), never a fixed one:
    Mailgun's domain on its configured region (MAILGUN_API_BASE — the probe
    used to hit the US endpoint even for an EU domain), or Mailjet's
    MAIL_FROM sender."""
    from app.notifications import mail
    lang = i18n.active()
    adapter, provider, _source = mail.transport()
    if adapter is None:
        return {"ok": False, "detalle": i18n.t_or(
            "admin.srv.settings.probar.email_sin_proveedor",
            "no hay proveedor de email seleccionado (CONNECTOR_EMAIL_TRANSPORT).", lang)}
    nombre = system_status._EMAIL_PROVIDER_NAMES.get(provider, provider)
    faltan = adapter.missing_config()
    if faltan:
        return {"ok": False, "detalle": i18n.t_or(
            "admin.srv.settings.probar.email_faltan_datos", "{proveedor}: faltan {claves}.",
            lang, proveedor=nombre, claves=", ".join(faltan))}
    vivo = adapter.probe()
    if vivo.get("error"):
        return {"ok": False, "detalle": i18n.t_or("admin.srv.settings.probar.email_error",
                                                    "{motivo}.", lang, motivo=vivo["error"])}
    estado = vivo.get("state")
    if estado == "not_found":
        return {"ok": False, "detalle": i18n.t_or(
            "admin.srv.settings.probar.email_remitente_no_encontrado",
            "{proveedor}: el remitente {remitente} no está dado de alta.",
            lang, proveedor=nombre, remitente=vivo.get("target") or "")}
    desconocido = i18n.t_or("admin.srv.settings.probar.estado_desconocido", "desconocido", lang)
    return {"ok": estado == "active", "detalle": i18n.t_or(
        "admin.srv.settings.probar.email_estado", "{proveedor} — {objetivo}: {estado}.",
        lang, proveedor=nombre, objetivo=vivo.get("target") or "", estado=estado or desconocido)}


def _probar_hsi():
    # Without a selected EMR connector there is nothing to probe and NO
    # default URL to invent — a probe to an unselected localhost HSI is
    # exactly the accidental-vendor-default this slice removes. The card
    # itself is already hidden in that state; this guards the API surface
    # for a direct POST.
    if not _capability_selected("emr_sync", "hsi"):
        lang = i18n.active()
        return {"ok": False, "detalle": i18n.t_or(
            "admin.srv.settings.probar.hsi_sin_conector",
            "no hay ningún conector EMR seleccionado (CONNECTOR_EMR_SYNC).", lang)}
    url = config.get("HSI_URL", default="http://localhost:8280")
    ok, err = system_status._reachable(url)
    host = urlparse(url).hostname or url
    if ok:
        return {"ok": True, "detalle": i18n.t_or(
            "admin.srv.settings.probar.hsi_ok", "{host}: responde.", i18n.active(), host=host)}
    return {"ok": False, "detalle": f"{host}: {err}"}


def _probar_emr_bridge():
    """Probe the selected bridge through its generic health contract.

    Its adapter refuses missing URL/token before building an HTTP request, so
    a selected-but-unconfigured bridge is visible as degraded without an HSI
    fallback, localhost default, or network traffic.
    """
    if not _capability_selected("emr_sync", "emr_bridge"):
        return {"ok": False, "detalle": "el conector EMR bridge no está seleccionado."}
    from app import connectors
    adapter = connectors.adapter_for(connectors.EMR_SYNC)
    if adapter is None:
        return {"ok": False, "detalle": "el conector EMR bridge no está disponible."}
    try:
        adapter.health()
    except connectors.ProviderUnavailable:
        return {"ok": False, "detalle": "el bridge EMR no respondió o no está configurado."}
    return {"ok": True, "detalle": i18n.t_or(
        "admin.srv.settings.probar.emr_bridge_ok", "El bridge EMR responde.", i18n.active())}


def _probar_backup():
    """Write, read back and (except on the municipal installation, which
    deletes nothing: `core.backup_lock`) remove a test object."""
    ok = backup_settings.client().probe(backup_settings.snapshot()["TURNOS_BACKUP_S3_PREFIX"])
    if not ok:
        return {"ok": False, "detalle": None}  # the panel's own "No funciona."
    if backup_lock.locked():
        return {"ok": True, "detalle": i18n.t_or("backup.probe_ok_kept", backup_lock.PROBE_KEPT_ES, i18n.active())}
    return {"ok": True, "detalle": i18n.t_or("backup.probe_ok", "Conexión comprobada", i18n.active())}


_PROBES = {
    "backup": _probar_backup,
    "openrouter": _probar_openrouter,
    "email": _probar_email,
    "hsi": _probar_hsi,
    "emr_bridge": _probar_emr_bridge,
}


# ======================================================== pure domain fns

class SettingsError(Exception):
    """400 payload — `str(exc)` is the exact `{"error": ...}` message the
    legacy handler already returns for this branch."""


def resolve_save(grupo, valores, borrar):
    """Pure port of `_save`'s dispatch (grupo -> `_guardar_*`). Returns the
    `resultado` payload on success, or raises `SettingsError` for every 400
    branch the legacy handler has (invalid `valores`, unknown `grupo`, or a
    `_guardar_*` validation error) — the caller always answers 400 for those,
    same as today."""
    if not isinstance(valores, dict):
        raise SettingsError(i18n.t_or("admin.srv.settings.valores_invalidos",
                                       "valores inválidos", i18n.active()))
    if grupo == "backup_key":
        try:
            return backup_settings.generate_key(rotate=valores.get("rotate") is True)
        except Exception:
            raise SettingsError(i18n.t_or("backup.key_error", "No se pudo guardar la clave. Revisá el bloqueo y el archivo de configuración.", i18n.active())) from None
    if grupo == "location":
        resultado, err = _guardar_location(valores)
    elif grupo == "languages":
        resultado, err = _guardar_languages(valores)
    elif grupo == "chat":
        resultado, err = _guardar_chat(valores)
    elif grupo == "turnos":
        resultado, err = _guardar_turnos(valores)
    elif grupo == "seguridad":
        resultado, err = _guardar_seguridad(valores)
    elif grupo == "connectors":
        resultado, err = _guardar_connectors(valores)
    elif grupo == "api_keys":
        resultado, err = _guardar_api_keys(valores, borrar if isinstance(borrar, list) else [])
    elif grupo == "feriados":
        resultado, err = _guardar_feriados(valores)
    else:
        raise SettingsError(i18n.t_or("admin.srv.settings.grupo_desconocido",
                                       "grupo desconocido", i18n.active()))
    if err:
        raise SettingsError(err)
    return resultado


def resolve_probe(integracion):
    """Pure port of `_probar`'s body — always returns a dict, status 200
    (a probe result of `ok: False` is still a 200 in the legacy handler;
    only the gate/JSON-parse can fail with a different status, and those
    stay in the transport layer)."""
    fn = _PROBES.get(integracion)
    if not fn:
        return {"ok": False, "detalle": i18n.t_or(
            "admin.srv.settings.sin_prueba",
            "sin prueba en vivo para esta integración — revisá el cluster «Sistema».",
            i18n.active())}
    try:
        return fn()
    except Exception:  # noqa: BLE001 — una prueba nunca debe tumbar el panel
        return {"ok": False, "detalle": i18n.t_or(
            "admin.srv.settings.probar.fallo", "The probe could not complete.", i18n.active())}
