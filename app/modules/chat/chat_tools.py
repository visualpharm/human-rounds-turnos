"""chat_tools.py — registro COMPARTIDO de herramientas del asistente de Sana
Turnos: buscar servicios, ver turnos disponibles, pedir/cancelar un turno,
"mis solicitudes", reconocer la identidad de la cuenta (whoami), leer los
"resultados de laboratorio" propios (my_lab_results) y leer una orden médica
(analyze_order). Dos consumidores comparten este único
registro en vez de triplicar la lógica:

  mcp_api.py     — el conector MCP (protocolo JSON-RPC, auth por Bearer)
  chat_agent.py — el motor de chat multi-turno (tool-calling de OpenRouter,
                  usado por el widget del sitio y por WhatsApp)

Cada tool es una función `fn(usuario, args, context) -> dict` — `usuario` YA
resuelto por el canal (Bearer / sesión web / teléfono matcheado — nunca sale
del body). Booking and cancellation call the canonical transport-free
operations directly; no request facade crosses into the booking module.
`TOOLS` es la lista de specs {name,title,description,
inputSchema} en JSON-Schema; cada consumidor la traduce a su wire format
(mcp_api la sirve tal cual en tools/list; chat_agent la envuelve en
{"type":"function","function":{...}} para el body de OpenRouter).

`analyze_order` corre su propio enfriamiento escalonado por identidad
(ai_cooldown.py, pedido por el responsable del producto 2026-07-23: "protect image analysis
with an escalating per-identity cooldown, on top of the existing coarse
limits") ANTES de gastar el análisis — `analizar_orden_para` es la función
que centraliza esa puerta, y la reusa también whatsapp_chat.py para las
fotos de orden que llegan por WhatsApp.
"""

import json
import re
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlparse
from urllib.parse import urlencode
from time import time as clock_time

from app.ai import ai_cooldown
from app.core import search_exclusions
from app.core import text_fold
from app.modules.chat import chat_store
from app.core import ids as _ids
from app.cuentas import claim_gate

_MODO_HUMANO = {
    "solicitud": "turno programado (se pide por acá; el hospital lo confirma)",
    "solicitud_con_orden": "turno programado que requiere ORDEN MÉDICA (se puede pedir por acá si ya se analizó la orden con analyze_order; si no, se sube en la web)",
    "walkin": "sin turno: por orden de llegada, en el horario de atención",
    "walkin_con_orden": "sin turno: por orden de llegada, llevando la orden médica",
}

_OPENAI_FILE_HOST_SUFFIX = ".oaiusercontent.com"
_FILE_DOWNLOAD_TIMEOUT = 20


@dataclass(frozen=True)
class ToolInvocation:
    """Domain-only execution metadata shared by tool consumers."""

    mcp_ai_guarded: bool = False
    client_ip: str | None = None
    channel: str | None = None
    conversation_key: str | None = None
    # Only the trusted transport sets this after a deterministic patient reply.
    confirmed_booking: dict | None = None
    # The conversation's language (chat_agent's turn language); None = the request's.
    language: str | None = None


def _openai_file_schema():
    """Shape required by ChatGPT's `_meta["openai/fileParams"]` contract.

    MCP itself keeps tool arguments as JSON. ChatGPT resolves an image attached
    to the conversation into this short-lived file reference, which lets this
    server receive real scans without forcing the model to copy megabytes of
    base64 into a tool call.
    """
    return {
        "type": "object",
        "properties": {
            "download_url": {"type": "string"},
            "file_id": {"type": "string"},
            "mime_type": {"type": "string"},
            "file_name": {"type": "string"},
        },
        "required": ["download_url", "file_id"],
        "additionalProperties": False,
    }


def _iso(v):
    if v is None:
        return None
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


# ---- idioma del catálogo ---------------------------------------------------
#
# Las tools leían catalog.services()/catalog.doctors() en crudo — texto del
# pack, nunca pasado por pack_l10n.localize() como sí hace patient_web/site_*.py
# desde 2026-08-04. Resultado: un visitante en inglés (idioma resuelto por
# i18n.active(), la MISMA cookie/nav switcher que ve el navegador) mandaba
# una consulta en inglés contra un catálogo que seguía en español — ni
# "gynecologist" aparecía en la ficha de Ginecología, ni el modelo podía
# devolver un nombre en inglés aunque la traducción ya estuviera en caché.
#
# `_es_activo()` es el atajo que mantiene el camino en español byte a byte
# igual: cuando el idioma activo coincide con el del pack (instalación de un
# solo idioma, o nadie tocó el selector) estas funciones son no-ops y todo
# el resto de este archivo corre exactamente como antes de este cambio.

def _idioma_activo():
    try:
        from app.core import i18n
        return i18n.active()
    except Exception:  # noqa: BLE001
        return None


def _es_activo(lang=None):
    try:
        from app.core import translate
        lang = lang if lang is not None else _idioma_activo()
        return translate._primary(lang) == translate._primary(translate.pack_language())
    except Exception:  # noqa: BLE001 — sin poder resolverlo, no tocar nada
        return True


def _nombres_localizados(servicios, lang=None):
    """id -> nombre en el idioma activo. `translate.cached_text()` semantics
    heredadas de pack_l10n.localize(): nunca llama al modelo durante una
    conversación en curso, y una entrada sin traducir en caché degrada al
    nombre del pack (nunca vacío). {} en español — nada que traducir."""
    if _es_activo(lang):
        return {}
    try:
        from app.core import pack_l10n
        localizados = pack_l10n.localize("servicios", servicios, lang)
        return {s.get("id"): loc.get("nombre") for s, loc in zip(servicios, localizados)}
    except Exception:  # noqa: BLE001
        return {}


def _especialidades_localizadas(medicos, lang=None):
    """Igual que `_nombres_localizados` pero para `especialidad` de medicos.py
    — el único campo de un profesional que es contenido (su nombre de persona
    nunca se traduce, pack_l10n.FIELDS lo excluye a propósito)."""
    if _es_activo(lang):
        return {}
    try:
        from app.core import pack_l10n
        localizados = pack_l10n.localize("medicos", medicos, lang)
        return {str(m.get("id") or ""): loc.get("especialidad")
                for m, loc in zip(medicos, localizados)}
    except Exception:  # noqa: BLE001
        return {}


def _stems_compatibles(query_stems, hay_stems):
    """True cuando CADA stem de la consulta tiene un correlato compatible en
    el stemming del contenido — igual o uno prefijo del otro desde 4
    caracteres. La misma tolerancia que ya usaba el bonus de ranking por
    nombre, generalizada acá al gate completo de búsqueda en un idioma no
    canónico (donde el contains literal de siempre ya no alcanza: "gynecologist"
    nunca es substring de "Gynecology")."""
    return all(
        any(h == p or (len(p) >= 4 and (h.startswith(p) or p.startswith(h)))
            for h in hay_stems)
        for p in query_stems
    )


# ---- catálogo -----------------------------------------------------------------

def _centros_nombre(lang=None):
    """slug -> nombre de centro. Localized off the pack's own language via
    pack_l10n (centros.name/nombre is translatable content since 2026-09-11):
    before, the demo pack's English centre names went straight into ru/pt
    tool output and prose (residual leak 2). Same cache-only contract as
    `_nombres_localizados` — a centre without a cached translation degrades
    to the pack's own spelling, never empty."""
    try:
        from app.patient_web import catalog
        if _es_activo(lang):
            return {c.get("slug"): c.get("name") for c in catalog.centers()}
        from app.core import pack_l10n
        filas = pack_l10n.localize("centros", catalog.centers(), lang)
        return {f.get("slug"): f.get("name") for f in filas}
    except Exception:  # noqa: BLE001
        return {}


def _slots_de(s):
    """Los turnos elegibles del servicio, aplanados [{dia,hora},...] — la MISMA
    fuente que la grilla del wizard (site_availability._appointments_for_service)."""
    try:
        from app.patient_web import site_availability, site_base
        data = site_availability._appointments_for_service(s) or {}
    except Exception:  # noqa: BLE001
        return []
    today = site_base._today_art().isoformat()
    out = []
    for m in data.get("medicos") or []:
        for t in m.get("turnos") or []:
            if t.get("dia") and t.get("hora") and str(t.get("dia")) >= today:
                out.append({"dia": t["dia"], "hora": t["hora"]})
    out.sort(key=lambda t: (t["dia"], t["hora"]))
    return out


def _turnos_humanos(slots):
    """Offer useful choices instead of consecutive machine-generated slots.

    The first two available dates get two well-separated options, preferring
    clock times that were not already shown for the previous date.  Repeating
    the same 08:00 / 11:40 pair on both days looks machine-generated and gives
    the patient less useful choice even when the diary contains alternatives.
    The complete availability remains authoritative in _slots_de() for booking.
    """
    por_dia = {}
    for slot in slots:
        dia = slot.get("dia")
        if dia:
            por_dia.setdefault(dia, []).append(slot)
    elegidos = []
    horas_usadas = set()
    for dia in sorted(por_dia)[:2]:
        day = sorted(por_dia[dia], key=lambda item: item.get("hora") or "")
        if len(day) == 1:
            picks = day
        else:
            disponibles = [item for item in day
                           if item.get("hora") not in horas_usadas]
            # Prefer a distinct early and late time on the second date.  If the
            # diary has no alternatives, fall back to the real endpoints rather
            # than inventing or hiding availability.
            base = disponibles if len(disponibles) >= 2 else day
            picks = [base[0], base[-1]]
        for item in picks:
            row = dict(item)
            try:
                from app.patient_web import site_schedule
                row["fecha_etiqueta"] = site_schedule._friendly_date(
                    row.get("dia"))
                row["etiqueta"] = site_schedule._friendly_date(
                    row.get("dia"), row.get("hora"))
            except Exception:  # noqa: BLE001 - ISO fields still remain usable
                pass
            elegidos.append(row)
            horas_usadas.add(row.get("hora"))
    return elegidos


def _base_url():
    from app.core import site_identity
    return site_identity.base_url()


def _info_servicio(s, centros, con_slots=False, nombres=None):
    """`nombres` es el mapa id -> nombre localizado que arma la búsqueda
    (`_nombres_localizados`) — nunca se recalcula acá adentro: esta función
    corre una vez por resultado y no tiene sentido pagar el lookup de nuevo
    por cada uno. {} o None cuando el visitante está en el idioma del pack:
    devuelve exactamente `s.get("nombre")`, como siempre."""
    from app.patient_web import site_availability
    reservable = site_availability._offers_appointments(s)
    sid = s.get("id")
    centro_slug = s.get("centro")
    info = {
        "servicio_id": sid,
        "nombre": (nombres or {}).get(sid) or s.get("nombre"),
        "centro": centros.get(centro_slug) or centro_slug,
        "como_se_atiende": _MODO_HUMANO.get(s.get("modo"), s.get("modo")),
        "requiere_orden": s.get("modo") in (
            "solicitud_con_orden", "walkin_con_orden"),
        "se_pide_por_aca": reservable,
        "href": f"/service/{sid}",
    }
    if centro_slug:
        info["centro_href"] = f"/center/{centro_slug}"
    if reservable:
        info["turno_href"] = f"/service/{sid}/book"
    if s.get("sede"):
        info["sede"] = s["sede"]
    edad = s.get("edad") or {}
    if edad.get("min") is not None or edad.get("max") is not None:
        info["edad"] = edad
    if s.get("horario"):
        info["horario"] = s["horario"]
    if s.get("modo") == "solicitud_con_orden":
        info["como_seguir"] = f"/service/{s.get('id')}/book (subir la orden médica, o analyze_order acá mismo)"
    if con_slots and reservable and not info["requiere_orden"]:
        # Un turno real, YA en el resultado de la búsqueda, para que el
        # modelo pueda ofrecer horarios concretos sin una segunda llamada a
        # available_appointments (responsable del producto, 2026-08-06: la búsqueda sola dejaba
        # cotizar horas sin ningún dato estructurado detrás — ver
        # chat_agent._slots_de_las_tools). `solicitud_con_orden` queda afuera
        # a propósito: ese servicio no se reserva sin pasar por la orden, la
        # puerta de entrada (_general_medicine_entry_path) es quien decide
        # si corresponde prometer un horario ahí.
        turnos_reales = _turnos_humanos(_slots_de(s))
        for turno in turnos_reales:
            turno["href"] = f"/service/{sid}/book?slot={turno.get('dia')}T{turno.get('hora')}"
        if turnos_reales:
            info["proximos_turnos"] = turnos_reales
    return info


def _info_medico(m, centros, especialidades=None):
    """Patient-safe doctor data for the chat.

    The public profile remains the source of truth for schedules and details.
    `especialidades` es el mapa id -> especialidad localizada
    (`_especialidades_localizadas`); el NOMBRE de la persona nunca se toca —
    pack_l10n.FIELDS lo excluye a propósito, un profesional se llama igual en
    cualquier idioma.
    """
    mid = str(m.get("id") or "").strip()
    centro_slugs = []
    for item in m.get("centros") or []:
        slug = str((item or {}).get("centro") or "").strip()
        if slug and slug not in centro_slugs:
            centro_slugs.append(slug)
    principal = str(m.get("centro") or "").strip()
    if principal and principal not in centro_slugs:
        centro_slugs.insert(0, principal)
    return {
        "medico_id": mid,
        "nombre": m.get("nombre"),
        "especialidad": (especialidades or {}).get(mid) or m.get("especialidad"),
        "centros": [centros.get(slug) or slug for slug in centro_slugs],
        "href": f"/doctor/{mid}" if mid else "/doctors",
    }


def _general_medicine_entry_path(services, centros, limit=2, nombres=None, lang=None):
    """Concrete first step when a specialty requires a medical referral.

    The public network's bureaucracy is deterministic: a patient without an
    order starts in Medicina general.  Put a small, real choice of bookable
    times directly in the search result so the model can lead with action
    instead of describing the limitation or asking permission to look again.

    The "medicina general" match below is INTENTIONALLY against `services`'
    raw (un-localized) `nombre` — it is a code-coupled identity check, the
    same class of thing pack_l10n.py's own docstring flags for `horario`:
    comparing it to a localized display string would silently stop matching
    for every non-canonical language. `nombres` (id -> localized nombre) only
    reaches the DISPLAY side, via `_info_servicio` and the `especialidad`
    label below.
    """
    bookable = []
    walkins = []
    for service in services or []:
        if text_fold.ascii_words(service.get("nombre")) != "medicina general":
            continue
        info = _info_servicio(service, centros, nombres=nombres)
        if info.get("requiere_orden"):
            continue
        if info.get("se_pide_por_aca"):
            choices = _turnos_humanos(_slots_de(service))
            if choices:
                bookable.append((info, choices))
        elif info.get("horario"):
            walkins.append({
                "nombre": info.get("nombre"),
                "centro": info.get("centro"),
                "horario": info.get("horario"),
                "href": info.get("href"),
            })

    options = []
    # First offer one real time per centre, then a second one.  This is more
    # useful than 4 consecutive slots from the first diary.
    round_index = 0
    while len(options) < limit and any(round_index < len(rows) for _, rows in bookable):
        for info, rows in bookable:
            if round_index >= len(rows):
                continue
            slot = rows[round_index]
            options.append({
                "servicio_id": info.get("servicio_id"),
                "nombre": info.get("nombre"),
                "centro": info.get("centro"),
                "dia": slot.get("dia"),
                "hora": slot.get("hora"),
                "etiqueta": slot.get("etiqueta"),
                "href": info.get("href"),
            })
            if len(options) >= limit:
                break
        round_index += 1

    # Una key FIJA sobre el texto literal, no el nombre de un servicio
    # puntual: dos filas "Medicina general" en centros distintos cachean su
    # traducción por separado (pack_l10n.key es por id), así que tomar
    # `bookable[0][0]["nombre"]` haría depender el label de CUÁL fila llegó
    # primera al armar `bookable` — inconsistente entre corridas y, en el
    # peor caso, entre dos turnos de la misma respuesta (code review,
    # 2026-08-05). Una sola key = una sola traducción, sin importar cuántas
    # filas de "Medicina general" tenga el pack.
    especialidad_label = "Medicina general"
    try:
        from app.core import translate
        especialidad_label = translate.cached_text(
            "chat_tools.especialidad_medicina_general", "Medicina general",
            lang if lang is not None else _idioma_activo())
    except Exception:  # noqa: BLE001 — degrada al literal español
        pass

    return {
        "especialidad": especialidad_label,
        "motivo": ("La especialidad requiere orden médica. Si la persona todavía "
                   "no la tiene, Medicina general es el primer paso y puede emitir "
                   "la derivación."),
        "turnos": options,
        "sin_turno": walkins[:2],
    }


def _etiquetas_de_especialidad():
    """[(label, /d/<cluster>)] for every specialty this installation has.

    A specialty is named in three shapes in a real answer — the specialty
    ("Pediatría"), the professional ("Pediatra") and, when the visitor is not
    reading Spanish, its translation — and all three lead to the same
    specialty page. The list is built per call because the language is
    request-scoped: a module-level constant would freeze the first visitor's
    language for everyone (see docs/architecture/i18n.md).
    """
    salida = []
    try:
        from app.patient_web import catalog
        from app.patient_web import site_catalog
        clusters = {str((c or {}).get("id") or "")
                    for c in site_catalog._clusters()}
    except Exception:  # noqa: BLE001 — links are an enhancement, never a gate
        return salida
    traducir = None
    try:
        from app.core import pack_l10n
        from app.core import translate
        lang = _idioma_activo()
        pack_lang = translate.pack_language()
        if translate._primary(lang) != translate._primary(pack_lang):
            def traducir(nombre):
                return translate.cached_text(
                    pack_l10n.key("especialidades", nombre, "nombre"),
                    nombre, lang, pack_lang=pack_lang)
    except Exception:  # noqa: BLE001 — degrades to the Spanish name
        traducir = None
    for item in catalog.specialties() or []:
        nombre = str((item or {}).get("nombre") or "").strip()
        cluster = str((item or {}).get("cluster") or "").strip()
        if not nombre or cluster not in clusters:
            continue
        href = f"/d/{cluster}"
        etiquetas = [nombre]
        try:
            from app.patient_web import site_doctors
            profesional = site_doctors._specialty_as_doctor(nombre)
            if profesional and profesional != nombre:
                etiquetas.append(profesional)
        except Exception:  # noqa: BLE001
            pass
        if traducir:
            try:
                traducido = traducir(nombre)
                if traducido and traducido != nombre:
                    etiquetas.append(traducido)
            except Exception:  # noqa: BLE001
                pass
        for etiqueta in etiquetas:
            salida.append((etiqueta, href))
    # Longest first: "Medicina general" must win over a shorter specialty
    # whose name is contained in it.
    salida.sort(key=lambda par: -len(par[0]))
    return salida


def _enlaces_de_especialidades(respuesta_norm, add):
    for etiqueta, href in _etiquetas_de_especialidad():
        if text_fold.ascii_words(etiqueta) in respuesta_norm:
            add(etiqueta, href)


def _norm_i18n(s):
    """`text_fold.ascii_words` for text that is NOT necessarily Latin.

    `text_fold.ascii_words` encodes to ASCII and drops whatever doesn't fit, which turns
    the Russian "My requests" into "" — fine while every label it compared was Spanish, useless
    for the localized section names a Russian or Japanese answer actually
    contains. This folds accents and case without deleting the alphabet."""
    return " ".join(text_fold.strip_accents(str(s or "")).casefold().split())


# The site sections the assistant names by their screen name, and the catalog
# key each name is rendered from. The model speaks these names in the
# CONVERSATION language (ai/prompts.py says so, and `_system_prompt` hands it
# the current language's spellings), so the injector has to recognise them in
# that language too — until 2026-09-11 it only matched the Spanish, which is
# why an English, Russian or Portuguese answer carried a raw "Mis solicitudes"
# with no link on it.
_SECCIONES = (
    ("nav.my_appointments", "Mis turnos", "/my-requests"),
    # The tab's former Spanish name (renamed 2026-11-05): a prompt edited in
    # the panel before the rename still makes the model say it, and the
    # section keeps its link. Its localized label is the line above's.
    ("nav.my_appointments", "Mis solicitudes", "/my-requests"),
    ("account.mine.lab.default_study", "Laboratorio", "/account#labs"),
    ("nav.upload_order", "Subí tu orden", "/upload-order"),
    ("nav.account.dependents", "Personas a cargo", "/account/dependents"),
    ("page.doctors.title", "Médicos y profesionales", "/doctors"),
    ("home.centers.heading", "Centros de salud", "/centers"),
    ("nav.my_account", "Mi cuenta", "/account"),
    ("nav.sign_in", "Ingresar", "/account"),
)


def _secciones_localizadas(lang=None):
    """[(label, href)] in `lang` (default: the active request language) —
    longest label first so a long section name wins over a shorter name contained
    in it. Built per call: the language is request-scoped and a module constant
    would freeze the first visitor's (docs/architecture/i18n.md, defect
    class 1)."""
    lang = lang or _idioma_activo()
    salida = []
    vistos = set()
    for clave, castellano, href in _SECCIONES:
        etiquetas = [castellano]
        try:
            from app.core import i18n
            etiquetas.append(i18n.t_or(clave, castellano, lang))
        except Exception:  # noqa: BLE001 — links are an enhancement
            pass
        for etiqueta in etiquetas:
            etiqueta = str(etiqueta or "").strip()
            norm = _norm_i18n(etiqueta)
            if not norm or (norm, href) in vistos:
                continue
            vistos.add((norm, href))
            salida.append((etiqueta, href))
    salida.sort(key=lambda par: -len(par[0]))
    return salida


def _etiqueta_seccion(clave, castellano, lang=None):
    """A section label in the CONVERSATION's language — every app-owned chip
    the answer attaches resolves here, never a raw Spanish literal. Same
    `t_or` + canonical-fallback contract as `_secciones_localizadas` (the
    `_SECCIONES` keys are the shared vocabulary); before 2026-09-11 a
    Portuguese or Russian booking turn closed with literal "Mis turnos" /
    "Mi cuenta" chips (residual leak 3 of the 2026-09-11 simulation)."""
    try:
        from app.core import i18n
        return i18n.t_or(clave, castellano, lang)
    except Exception:  # noqa: BLE001 — links are an enhancement
        return castellano


def _tokens(s):
    return " " + " ".join(re.findall(r"\w+", _norm_i18n(s))) + " "


def published_hours(textos, lang=None, limite=4):
    """`_info_servicio` rows (with `horario`) for the services the patient
    NAMED, read from the catalog without any model — what the chat can still
    say when the assistant is down (QA P18, 2026-10-25).

    `textos` newest first; the first one naming something wins. A service
    matches by its own name, or by the specialty it belongs to («dermatología»,
    «dermatólogo», its translation) when the service's name starts with it.
    Never raises: [] on any failure."""
    try:
        from app.patient_web import catalog
        from app.patient_web import site_doctors
        servicios = [s for s in catalog.services() if s.get("horario")]
        centros = _centros_nombre(lang)
        nombres = _nombres_localizados(servicios, lang)
        especialidades = []
        for item in catalog.specialties() or []:
            nombre = str((item or {}).get("nombre") or "").strip()
            if not nombre:
                continue
            etiquetas = {nombre, site_doctors._specialty_as_doctor(nombre)}
            especialidades.append((_tokens(nombre), [_tokens(e) for e in etiquetas if e]))
        for texto in textos or []:
            hay = _tokens(texto)
            if not hay.strip():
                continue
            elegidos = []
            for s in servicios:
                propio = [_tokens(s.get("nombre")), _tokens(nombres.get(s.get("id")))]
                nombre_s = _tokens(s.get("nombre"))
                for raiz, etiquetas in especialidades:
                    if nombre_s.startswith(raiz):
                        propio.extend(etiquetas)
                if any(e.strip() and len(e.strip()) >= 4 and e in hay for e in propio):
                    elegidos.append(s)
            if elegidos:
                return [_info_servicio(s, centros, nombres=nombres)
                        for s in elegidos[:limite]]
    except Exception:  # noqa: BLE001 — hours are an enhancement of a degraded reply
        return []
    return []


def _acumulador_de_enlaces():
    """(enlaces, add): the list being built and the one way into it. `add`
    says whether the link went in."""
    enlaces = []
    etiquetas = set()

    def add(label, href):
        # Deduped by LABEL, not by destination: the service and the centre
        # where it is offered are two different words in the same sentence and
        # both deserve their link, even though they open the same page. One
        # label can only mean one destination, and the first (most specific)
        # one wins. Compared with `_norm_i18n`: `text_fold.ascii_words` drops whatever is not
        # ASCII, so every Russian, Ukrainian, Japanese or Chinese label was ""
        # and only the first chip of such an answer survived.
        label = str(label or "").strip()
        href = str(href or "").strip()
        etiqueta = _norm_i18n(label)
        if not label or not href.startswith("/") or etiqueta in etiquetas:
            return False
        etiquetas.add(etiqueta)
        enlaces.append({"label": label, "href": href})
        return True

    return enlaces, add


def _filas_de_resultado(name, result):
    if name in ("search_services", "search_doctors"):
        return result.get("resultados") or []
    if name == "available_appointments":
        return [result]
    return []


def _enlaces_de_filas(name, filas, respuesta_norm, add):
    mencionadas = [
        fila for fila in filas
        if isinstance(fila, dict) and text_fold.ascii_words(fila.get("nombre")) and
        text_fold.ascii_words(fila.get("nombre")) in respuesta_norm
    ]
    if mencionadas:
        filas = mencionadas
    for fila in filas:
        if not isinstance(fila, dict):
            continue
        nombre = fila.get("nombre")
        centro = fila.get("centro")
        etiqueta = f"{nombre} — {centro}" if nombre and centro else nombre
        # A centre named inside a service answer is the patient's route to
        # that service at that centre, not to the generic centre page.
        # E.g. "Hospital de Pinamar" in a cardiology answer opens
        # /service/cardiologia-hospital.
        if name == "search_services":
            add(centro or etiqueta, fila.get("href"))
        else:
            add(etiqueta, fila.get("href"))
            add(centro, fila.get("centro_href"))


def _enlaces_de_puerta_de_entrada(result, add):
    entry = result.get("puerta_de_entrada") or {}
    for option in entry.get("turnos") or []:
        if not isinstance(option, dict):
            continue
        label = " — ".join(filter(None, (
            option.get("nombre"), option.get("centro"))))
        add(label, option.get("href"))
    for option in entry.get("sin_turno") or []:
        if not isinstance(option, dict):
            continue
        label = " — ".join(filter(None, (
            option.get("nombre"), option.get("centro"))))
        add(label, option.get("href"))


def _enlaces_de_cuenta(name, result, add, lang):
    if result.get("confirmation_required") and name in ("request_appointment", "cancel_request"):
        add(result.get("confirmation_label"), result.get("href"))
    if name == "request_appointment" and result.get("ok"):
        add(_etiqueta_seccion("nav.my_appointments", "Mis turnos", lang),
            "/my-requests")
    elif name == "my_requests":
        add(_etiqueta_seccion("nav.my_appointments", "Mis turnos", lang),
            "/my-requests")
    elif name == "my_lab_results":
        add(_etiqueta_seccion("account.mine.lab.default_study",
                              "Laboratorio", lang), "/account#labs")
    elif name == "whoami":
        add(_etiqueta_seccion("nav.my_account", "Mi cuenta", lang),
            "/account")


def _enlaces_de_tools(resultados_tools, respuesta_norm, add, lang):
    """The links the tools of this turn hand over, in the order they ran."""
    for name, result in (resultados_tools or []):
        if not isinstance(result, dict):
            continue
        if name == "search_doctors":
            add(_etiqueta_seccion("page.doctors.title",
                                  "Médicos y profesionales", lang),
                result.get("directorio_href") or "/doctors")
        _enlaces_de_filas(name, _filas_de_resultado(name, result),
                          respuesta_norm, add)
        if name == "search_services":
            _enlaces_de_puerta_de_entrada(result, add)
        _enlaces_de_cuenta(name, result, add, lang)


def _enlaces_de_servicios_nombrados(resultados_tools, respuesta_norm, add):
    # A service the answer NAMES is a destination too, not just the centre
    # where it is offered: "Medicina general" opens the service, "Pediatría"
    # opens the specialty. Only unambiguous names link to a single service —
    # a name offered at several centres goes to its specialty page below.
    for name, result in (resultados_tools or []):
        if name != "search_services" or not isinstance(result, dict):
            continue
        # Keyed by the NORMALISED name: "Medicina general" at three CAPS and a
        # "Medicina General" at the hospital are the same word to the reader,
        # so that word must not link to whichever row happened to come first —
        # it falls through to its specialty page below.
        destinos = {}
        for fila in result.get("resultados") or []:
            if not isinstance(fila, dict):
                continue
            nombre = str(fila.get("nombre") or "").strip()
            href = str(fila.get("href") or "")
            if nombre and href.startswith("/"):
                destinos.setdefault(text_fold.ascii_words(nombre), (nombre, set()))[1].add(href)
        for clave, (nombre, hrefs) in destinos.items():
            if len(hrefs) == 1 and clave in respuesta_norm:
                add(nombre, next(iter(hrefs)))


def _enlaces_de_centros(texto, respuesta, add, lang):
    # A named centre or the emergency department may be answered without a
    # tool call. Resolve those destinations against the catalog in code.
    hay = text_fold.ascii_words(f"{texto or ''} {respuesta or ''}")
    try:
        from app.core import site_identity
        from app.patient_web import catalog
        from app.patient_web import site_centers
        centros_localizados = _centros_nombre(lang)
        for centro in catalog.centers():
            nombre = centro.get("name")
            slug = centro.get("slug")
            if not (nombre and slug):
                continue
            if text_fold.ascii_words(nombre) in hay:
                add(nombre, site_centers.center_href(slug))
            # The answer may name the centre in the CONVERSATION's language
            # (centros.name is translatable since 2026-09-11) — link that
            # spelling too, with its own label.
            localizado = centros_localizados.get(slug)
            if localizado and text_fold.ascii_words(localizado) in hay:
                add(localizado, site_centers.center_href(slug))
        if "guardia" in hay or "emergencia" in hay:
            hospital = str(site_identity.get("hospital") or "").strip()
            main_center = str(site_identity.get("main_center") or "").strip()
            if hospital and main_center:
                add(hospital, f"/center/{main_center}")
    except Exception:  # noqa: BLE001 — links are an enhancement
        pass


def _enlaces_de_secciones(respuesta, add, lang):
    """The sections the answer names in the conversation language; returns the
    destinations that got a link."""
    # A section the answer names IN THE CONVERSATION LANGUAGE gets its real
    # destination. One link per section: the first (longest) label that matches
    # wins, so an English answer gets "My requests" and not also a phantom
    # Spanish twin pointing at the same page.
    secciones = set()
    mencionado_i18n = _norm_i18n(respuesta)
    for etiqueta, href in _secciones_localizadas(lang):
        if href in secciones or _norm_i18n(etiqueta) not in mencionado_i18n:
            continue
        if add(etiqueta, href):
            secciones.add(href)
    return secciones


def _enlaces_de_navegacion(texto, respuesta, secciones, add, lang):
    intent = text_fold.ascii_words(texto)
    mentioned = text_fold.ascii_words(respuesta)
    # Strict content-link contract: if the assistant names a site section,
    # return the application-owned destination even when the model did not
    # call the matching search tool in that turn.
    if "/doctors" not in secciones and any(term in mentioned for term in
           ("medicos y profesionales", "directorio de medicos", "lista de profesionales")):
        add(_etiqueta_seccion("page.doctors.title",
                              "Médicos y profesionales", lang), "/doctors")
    if "/upload-order" not in secciones and "subi tu orden" in mentioned:
        add(_etiqueta_seccion("nav.upload_order", "Subí tu orden", lang),
            "/upload-order")
    if "/centers" not in secciones and any(
            term in mentioned for term in ("centros de salud", "lista de centros")):
        add(_etiqueta_seccion("home.centers.heading",
                              "Centros de salud", lang), "/centers")
    if any(term in mentioned for term in ("estudios y diagnostico", "lista de estudios")):
        add(_etiqueta_seccion("nav.studies", "Estudios", lang), "/d/diagnostico")
    if any(term in mentioned for term in ("talleres y grupos", "lista de talleres")):
        add(_etiqueta_seccion("nav.groups", "Talleres", lang), "/d/grupos")
    if ("/upload-order" not in secciones and "orden" in intent and any(
            term in intent for term in ("sub", "adjunt", "archivo", "foto", "pdf"))):
        add(_etiqueta_seccion("nav.upload_order", "Subí tu orden", lang),
            "/upload-order")
    if "/account" not in secciones and any(term in intent for term in
           ("contrasena", "iniciar sesion", "ingresar", "mi cuenta")):
        add(_etiqueta_seccion("nav.sign_in", "Ingresar", lang), "/account")
    if "/my-requests" not in secciones and any(term in intent for term in
           ("cancelar", "mis turnos", "mis solicitudes", "cambiar la fecha",
            "reprogramar")):
        add(_etiqueta_seccion("nav.my_appointments",
                              "Mis turnos", lang), "/my-requests")


def enlaces_para(texto, respuesta, resultados_tools=None, limite=12, lang=None):
    """Build safe, app-owned links from catalog/tool data, never model URLs.

    `lang` is the CONVERSATION's language (chat_agent locks it to the first
    user message), which is not always the page's: someone browsing in Spanish
    can hold the chat in Portuguese. It decides which spelling of a section
    name the answer is searched for."""
    enlaces, add = _acumulador_de_enlaces()

    respuesta_norm = text_fold.ascii_words(respuesta)
    _enlaces_de_tools(resultados_tools, respuesta_norm, add, lang)
    _enlaces_de_servicios_nombrados(resultados_tools, respuesta_norm, add)
    _enlaces_de_especialidades(respuesta_norm, add)
    _enlaces_de_centros(texto, respuesta, add, lang)
    secciones = _enlaces_de_secciones(respuesta, add, lang)
    _enlaces_de_navegacion(texto, respuesta, secciones, add, lang)
    try:
        limite = max(1, int(limite or 12))
    except (TypeError, ValueError):
        limite = 12
    if not enlaces:
        add(_etiqueta_seccion("chat.link_home", "Servicios y turnos", lang), "/")
    return enlaces[:limite]


# ---- tools ----------------------------------------------------------------------

TOOLS = [
    {
        "name": "search_doctors",
        "title": "Buscar médicos y profesionales",
        "description": ("Busca médicos y profesionales de la red de salud activa "
                        "por nombre, apellido, especialidad o centro. Si "
                        "no encuentra el nombre, devuelve el directorio completo: "
                        "no hay que inferir una especialidad ni sugerir centros."),
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string",
                                     "description": "Nombre, apellido o especialidad; ej. 'Martínez', 'cardiólogo'."}},
            "required": ["query"],
        },
    },
    {
        "name": "search_services",
        "title": "Buscar servicios",
        "description": ("Busca en el catálogo de servicios del hospital y los CAPS "
                        "(especialidades, estudios, talleres). Devuelve cómo se "
                        "atiende cada uno: con turno pedible por acá, con orden "
                        "médica desde la web, o sin turno por orden de llegada."),
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string",
                                     "description": "Qué buscás: 'cardiología', 'ecografía', 'pediatra'…"}},
            "required": ["query"],
        },
    },
    {
        "name": "available_appointments",
        "title": "Turnos disponibles",
        "description": ("Los próximos horarios elegibles de un servicio (los mismos "
                        "que ofrece la web). El primero es el más próximo, no el único."),
        "inputSchema": {
            "type": "object",
            "properties": {"service_id": {"type": "string"}},
            "required": ["service_id"],
        },
    },
    {
        "name": "request_appointment",
        "title": "Solicitar un turno",
        "description": ("Pide un turno a nombre del dueño de esta cuenta (o de un "
                        "hijo/a ya vinculado, pasando patient_dni). Con date+time "
                        "reserva ese horario; sin ellos, el hospital asigna y "
                        "'preference' (texto libre, ej. 'mañana temprano') lo "
                        "orienta. Si el servicio requiere orden médica, primero hay "
                        "que leerla con analyze_order y pasar el orden_token que "
                        "devuelve. La primera llamada propone el horario exacto; "
                        "no crea la solicitud. La persona debe responder Confirmar "
                        "en el chat o confirmar en el enlace web. Las reglas del "
                        "servicio pueden confirmarla automáticamente; nunca "
                        "apruebes una solicitud pendiente por decisión propia."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "service_id": {"type": "string"},
                "date": {"type": "string", "description": "YYYY-MM-DD, de available_appointments"},
                "time": {"type": "string", "description": "HH:MM, de available_appointments"},
                "preference": {"type": "string", "description": "preferencia horaria en texto libre, si no se eligió un horario concreto"},
                "patient_dni": {"type": "string", "description": "DNI del hijo/a a cargo si el turno no es para el titular"},
                "orden_token": {"type": "string", "description": "token que devolvió analyze_order, si el servicio requiere orden médica"},
            },
            "required": ["service_id"],
        },
    },
    {
        "name": "my_requests",
        "title": "Mis turnos",
        "description": ("Las solicitudes de turno del dueño de esta cuenta, con su "
                        "estado (pendiente / aprobada / rechazada / cancelada) en "
                        "`estado` y en `estado_texto` la etiqueta para MOSTRAR al "
                        "paciente, ya en el idioma de la conversación — nunca cites "
                        "`estado` crudo."),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "cancel_request",
        "title": "Cancelar una solicitud",
        "description": "Ofrece el enlace para que el paciente confirme la cancelación de una solicitud propia. Nunca cancela por una decisión del modelo.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
    {
        "name": "whoami",
        "title": "Quién sos",
        "description": "La identidad de la cuenta autenticada (nombre, DNI, si tiene contacto/email cargado, personas a cargo) — para saber a quién se le está hablando antes de pedir un turno.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "my_lab_results",
        "title": "Resultados de laboratorio",
        "description": ("Los informes de laboratorio digitalizados del dueño de esta "
                        "cuenta (o de un hijo/a a cargo, pasando patient_dni), más "
                        "nuevo primero: tipo de estudio, resumen cauteloso y valores. "
                        "Un informe marcado sensible sólo informa su fecha: el médico "
                        "lo comenta en la consulta. Requiere sesión iniciada."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "patient_dni": {"type": "string", "description": "DNI del hijo/a a cargo si los resultados no son del titular"},
            },
        },
    },
    {
        "name": "analyze_order",
        "title": "Leer una orden médica",
        "description": ("Lee una imagen adjunta o el PDF de una orden médica con IA y devuelve "
                        "un orden_token para pasarle a request_appointment en "
                        "servicios que requieren orden. Sujeto a un enfriamiento "
                        "por persona: al superar la ráfaga inicial hay que esperar "
                        "cada vez más entre lecturas."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": _openai_file_schema(),
                "data_b64": {
                    "type": "string",
                    "description": ("alternativa compatible con otros clientes MCP: "
                                    "el archivo en base64 (puede traer el prefijo "
                                    "data:...;base64,)"),
                },
                "filename": {"type": "string"},
                "mime": {"type": "string", "description": "ej. image/jpeg, application/pdf"},
                "service_id": {"type": "string", "description": "el servicio para el que es la orden, si se sabe"},
            },
            "anyOf": [
                {"required": ["file"]},
                {"required": ["data_b64"]},
            ],
        },
        "_meta": {"openai/fileParams": ["file"]},
    },
]


def _tool_search(usuario, args, _invocation):
    consulta = text_fold.ascii_words(args.get("query"))
    if not consulta:
        return {"error": "decime qué buscás (ej. 'cardiología')"}
    from app.patient_web import catalog
    lang = _idioma_activo()
    centros = _centros_nombre(lang)
    es_activo = _es_activo(lang)
    servicios_todos = catalog.services()
    # {} en español: cada lookup de abajo degrada a s.get("nombre"), o sea el
    # camino de siempre — nada se traduce ni se recalcula por gusto.
    nombres_i18n = _nombres_localizados(servicios_todos, lang)
    stemmer = _catalog_stem if es_activo else _catalog_stem_en
    palabras = consulta.split()
    query_stems = [stemmer(p) for p in palabras]
    matches = []
    for s in servicios_todos:
        # palabras_excluidas (reunión municipal 2026-07-17): el servicio no
        # aparece en ESA búsqueda — mismo criterio que search.mjs.
        exc = [text_fold.ascii_words(e) for e in (s.get("palabras_excluidas") or []) if text_fold.ascii_words(e)]
        if exc and search_exclusions.is_excluded(consulta, palabras, exc):
            continue
        nombre_localizado = nombres_i18n.get(s.get("id")) or s.get("nombre")
        campos_hay = [s.get(k) or "" for k in
                      ("nombre", "palabras", "categoria", "cluster", "sede")]
        if nombre_localizado != s.get("nombre"):
            campos_hay.append(nombre_localizado)
        hay = text_fold.ascii_words(" ".join(str(c) for c in campos_hay)
                    + " " + (centros.get(s.get("centro")) or ""))
        if es_activo:
            # Camino de siempre, sin tocar — el contains literal ya alcanza
            # porque la consulta y el catálogo comparten el mismo idioma.
            gate = all(p in hay for p in palabras)
        else:
            # Un idioma no canónico necesita el gate por stem, no el contains
            # literal: "gynecologist" nunca es substring de "Gynecology". El
            # contains literal queda como alternativa (ej. "blood" SÍ aparece
            # tal cual en "Blood test").
            hay_stems = [stemmer(tok) for tok in hay.split()]
            gate = all(p in hay for p in palabras) or _stems_compatibles(query_stems, hay_stems)
        if gate:
            nombre_norm = text_fold.ascii_words(nombre_localizado)
            name_stems = [stemmer(p) for p in nombre_norm.split()]
            stem_hit = _stems_compatibles(query_stems, name_stems)
            rank = 0 if nombre_norm.startswith(consulta) else (
                1 if consulta in nombre_norm or stem_hit else 2)
            matches.append((rank, s))
    matches.sort(key=lambda x: (x[0], text_fold.ascii_words(x[1].get("nombre"))))
    # Medical orders often contain a more specific wording than the public
    # catalogue.  For example, the generic hospital Ecografía service covers
    # "ecografía abdominal completa", while the catalogue deliberately does
    # not turn every clinical variant into a separate service.  Keep the
    # strict search above as the normal behaviour, then make a small,
    # explainable relaxation only when it found nothing: ignore generic
    # qualifiers and normalise the common abdomen/abdominal wording.
    #
    # This relaxation is Spanish-only by construction: it exists for text
    # extracted from a scanned medical order (analyze_order -> _tool_search),
    # and an order issued in this jurisdiction is always written in Spanish
    # regardless of the visitor's site language — see orientar_orden_anonima.
    if not matches and len(palabras) > 1:
        genericos = {
            "completa", "completo", "simple", "bilateral", "unilateral",
            "con", "sin", "contraste", "total", "frente", "perfil", "y",
            "ap", "pa", "lateral", "oblicua", "oblicuo", "derecha",
            "izquierda",
        }
        equivalencias = {
            "abdominal": "abdomen", "abdomino": "abdomen",
            "eco": "ecografia",
        }
        nucleos = [equivalencias.get(p, p) for p in palabras if p not in genericos]
        if nucleos:
            for s in servicios_todos:
                exc = [text_fold.ascii_words(e) for e in (s.get("palabras_excluidas") or []) if text_fold.ascii_words(e)]
                if exc and search_exclusions.is_excluded(consulta, nucleos, exc):
                    continue
                hay = text_fold.ascii_words(" ".join(str(s.get(k) or "") for k in
                                     ("nombre", "palabras", "categoria", "cluster", "sede"))
                            + " " + (centros.get(s.get("centro")) or ""))
                # `palabras` is a JSON list, so its string representation has
                # quotes and commas.  Tokenise it instead of splitting on
                # spaces; otherwise "abdomen'," fails to match "abdomen".
                hay_tokens = {
                    equivalencias.get(p, p)
                    for p in re.findall(r"[a-z0-9]+", hay)
                }
                if all(p in hay_tokens for p in nucleos):
                    # A public service whose name itself starts with the core
                    # study is the clearest default before more specialised
                    # variants such as paediatric ecography.
                    nombre = text_fold.ascii_words(s.get("nombre"))
                    rank = 0 if nombre.startswith(nucleos[0]) else 1
                    matches.append((rank, s))
            matches.sort(key=lambda x: (x[0], text_fold.ascii_words(x[1].get("nombre"))))
    services = [s for _, s in matches[:10]]
    out = [_info_servicio(s, centros, con_slots=True, nombres=nombres_i18n) for s in services]
    if not out:
        return {"resultados": [], "nota": "sin resultados; probá con otra palabra (ej. la especialidad)"}
    result = {"resultados": out, "total": len(matches)}
    if all(item.get("requiere_orden") for item in out):
        result["puerta_de_entrada"] = _general_medicine_entry_path(
            servicios_todos, centros, nombres=nombres_i18n, lang=lang)
    return result


def _doctor_stem(palabra):
    palabra = text_fold.ascii_words(palabra)
    if len(palabra) >= 6 and palabra.endswith("s"):
        palabra = palabra[:-1]
    if len(palabra) >= 6 and palabra.endswith("ia"):
        palabra = palabra[:-2]
    elif len(palabra) >= 6 and palabra.endswith(("o", "a")):
        palabra = palabra[:-1]
    return palabra


def _catalog_stem(palabra):
    palabra = text_fold.ascii_words(palabra)
    if len(palabra) >= 7 and palabra.endswith("s"):
        palabra = palabra[:-1]
    for suffix in ("amiento", "imiento", "aciones", "acion", "atorios",
                   "atorio"):
        if len(palabra) > len(suffix) + 3 and palabra.endswith(suffix):
            return palabra[:-len(suffix)]
    return _doctor_stem(palabra)


# English counterparts, selected by active language (chat_tools never touches
# search.js — this mirrors the SAME vocabulary decisions unit 2 makes there,
# implemented independently in Python since this matcher is a real duplicate,
# not a shared engine).
def _doctor_stem_en(palabra):
    palabra = text_fold.ascii_words(palabra)
    # British/American folding first, so both spellings collapse to one stem
    # before the plural/suffix rules run.
    palabra = (palabra.replace("paediatr", "pediatr")
                       .replace("gynaecolog", "gynecolog")
                       .replace("orthopaedic", "orthopedic")
                       .replace("haematolog", "hematolog"))
    if len(palabra) > 5 and palabra.endswith("ies"):
        return palabra[:-3] + "y"
    if len(palabra) > 6 and palabra.endswith("es") and palabra[-3] in "sxzh":
        return palabra[:-2]
    if len(palabra) >= 5 and palabra.endswith("s") and not palabra.endswith("ss"):
        return palabra[:-1]
    return palabra


def _catalog_stem_en(palabra):
    palabra = _doctor_stem_en(palabra)
    if palabra.endswith("ologist"):
        return palabra[:-len("ologist")] + "ology"
    if palabra.endswith("iatrician"):
        return palabra[:-len("iatrician")] + "iatrics"
    return palabra


_STOPWORDS_MEDICO_ES = {"dr", "dra", "doctor", "doctora", "medico", "medica",
                         "profesional", "hay", "esta", "atiende"}
_STOPWORDS_MEDICO_EN = {"dr", "doctor", "physician", "medic", "medical",
                        "professional", "is", "there", "a", "an", "the",
                        "does", "see", "sees", "available", "any"}
_TODOS_TOKENS = {"todos", "all"}


def _tool_search_doctors(usuario, args, _invocation):
    consulta = text_fold.ascii_words(args.get("query"))
    lang = _idioma_activo()
    es_activo = _es_activo(lang)
    stopwords = _STOPWORDS_MEDICO_ES if es_activo else _STOPWORDS_MEDICO_EN
    palabras = [p for p in consulta.split() if p not in stopwords]
    from app.patient_web import catalog
    centros = _centros_nombre(lang)
    if not palabras or set(palabras) <= _TODOS_TOKENS:
        return {
            "resultados": [],
            "directorio_href": "/doctors",
            "nota": "consultá el directorio completo de médicos y profesionales",
        }
    medicos_todos = catalog.public_doctors()
    especialidades_i18n = _especialidades_localizadas(medicos_todos, lang)
    # _catalog_stem_en, no _doctor_stem_en: an English query for a doctor
    # ("gynecologist", "pediatrician") needs the SAME -ologist/-iatrician
    # folding as the service search, since `especialidad` is the same
    # translated vocabulary either way (_doctor_stem_en alone only folds
    # plurals/British spelling, not the profession-noun suffix).
    stemmer = _doctor_stem if es_activo else _catalog_stem_en
    matches = []
    for m in medicos_todos:
        if m.get("rol"):
            continue
        centro_texto = " ".join(
            centros.get(str((item or {}).get("centro") or ""), "")
            for item in (m.get("centros") or [])
        )
        especialidad_localizada = (especialidades_i18n.get(str(m.get("id") or ""))
                                    or m.get("especialidad"))
        campos = (m.get("nombre"), m.get("nombre_pila"), m.get("apellido"),
                  especialidad_localizada)
        hay = text_fold.ascii_words(" ".join(str(c or "") for c in campos) + " " + centro_texto)
        tokens_hay = [stemmer(p) for p in hay.split()]
        stems = [stemmer(p) for p in palabras]
        if all(any(h == p or (len(p) >= 4 and (h.startswith(p) or p.startswith(h)))
                   for h in tokens_hay) for p in stems):
            nombre = text_fold.ascii_words(m.get("nombre"))
            consulta_limpia = " ".join(palabras)
            rank = 0 if nombre == consulta_limpia else (
                1 if nombre.startswith(consulta_limpia) else
                2 if consulta_limpia in nombre else 3)
            matches.append((rank, m))
    matches.sort(key=lambda x: (x[0], text_fold.ascii_words(x[1].get("nombre"))))
    return {
        "resultados": [_info_medico(m, centros, especialidades=especialidades_i18n)
                       for _, m in matches[:10]],
        "total": len(matches),
        "directorio_href": "/doctors",
        "nota": ("sin coincidencias; ofrecé el directorio completo, no infieras "
                 "la especialidad ni sugieras centros")
        if not matches else "el directorio completo también está disponible",
    }


def _tool_slots(usuario, args, _invocation):
    sid = str(args.get("service_id") or "").strip()
    from app.patient_web import site_availability, site_catalog
    s = site_catalog._service_by_id(sid)
    if not s:
        return {"error": f"no existe el servicio '{sid}' — buscalo con search_services"}
    centros = _centros_nombre(_idioma_activo())
    info = _info_servicio(s, centros)
    if not site_availability._offers_appointments(s):
        info["nota"] = "este servicio no reserva turnos: se atiende por orden de llegada en el horario de atención"
        return info
    if site_availability._opens_same_day(s):
        info["nota"] = ("se atiende por orden de llegada, pero el listado del día se "
                        "abre y se puede reservar SÓLO para hoy")
    slots = _slots_de(s)
    if s.get("modo") == "solicitud_con_orden":
        info["nota"] = ("requiere orden médica: leela con analyze_order y pasá el "
                        "orden_token a request_appointment (o subila en la web) — "
                        + _base_url() + f"/service/{sid}/book")
    if not slots:
        info["turnos"] = []
        info["nota"] = info.get("nota") or ("sin agenda publicada: pedí el turno igual "
                                            "(request_appointment con 'preference') y el centro confirma la fecha")
        return info
    info["turnos"] = _turnos_humanos(slots)
    for turno in info["turnos"]:
        turno["href"] = (
            _base_url().rstrip('/') + f'/service/{sid}/book?slot={turno.get("dia")}T{turno.get("hora")}')
    info["total_turnos_disponibles"] = len(slots)
    info["nota"] = info.get("nota") or (
        "opciones iniciales variadas de los primeros dos días, evitando repetir "
        "las mismas horas cuando hay alternativas; copiá `etiqueta` tal como "
        "está. Hay más horarios "
        "si la persona necesita otro. Nada queda reservado hasta que la persona confirma la propuesta de request_appointment")
    return info


_CONFIRM_WORDS = ('confirmar', 'confirmo', 'si confirmo', 'confirm', 'yes confirm')


def _confirm_fold(text):
    """A reply folded for the confirmation match in any script: NFKC, case and
    accents gone, spaces collapsed, punctuation and quotes («», 「」, “”, «!»,
    «。») trimmed from both ends. Every letter stays, Cyrillic and CJK too
    (ascii_words deletes them, so «confirm» + a refusal in another script read
    as «confirm»), and so does an emoji: «Confirmar ❌» is not a confirmation."""
    folded = " ".join(text_fold.strip_accents(
        unicodedata.normalize("NFKC", str(text or "")).casefold()).split())
    return folded.strip("".join({c for c in folded if unicodedata.category(c)[0] in "PZ"}))


def _is_confirm_reply(text, lang):
    """The patient's explicit «confirm»: the Spanish and English words in every
    language (the words this check always took), plus the word the proposal
    asked for in the patient's language (`ai.booking.confirm_word`)."""
    from app.core import i18n
    reply = _confirm_fold(text)
    words = {_confirm_fold(w) for w in _CONFIRM_WORDS}
    if lang:
        words.add(_confirm_fold(i18n.t_or('ai.booking.confirm_word', 'Confirmar', lang)))
    return bool(reply) and reply in words


def confirm_booking_reply(usuario, text, channel, key, lang=None):
    """Transport-only confirmation, before another model turn can run.

    A model argument cannot authorize a booking. A short explicit user reply
    consumes the exact stored proposal, bound to account + channel + key. The
    reply is read in the proposal's language; `lang` (the conversation's) and
    then the request's stand in for a proposal stored without one.
    """
    try:
        proposal = chat_store.chat_metadata_get(channel, key, 'booking_proposal')
        if not proposal:
            return None
        from app.core import i18n
        yes = _is_confirm_reply(text, proposal.get('lang') or lang or i18n.active())
        # Any intervening message retires the proposal rather than applying a
        # later ambiguous yes to a slot the patient is no longer discussing.
        proposal = chat_store.chat_take_booking_proposal(channel, key)
        if not yes or not proposal:
            return None
        dni = _ids.dni((usuario or {}).get('dni'))
        if proposal.get('patient_dni') != dni or clock_time() - proposal.get('created', 0) > 600:
            return {'error':'La propuesta venció. Elegí el horario de nuevo.'}
        intent = proposal['args']
        return _tool_request(usuario, intent, ToolInvocation(channel=channel, conversation_key=key, confirmed_booking=intent))
    except Exception:  # noqa: BLE001 — storage failure cannot authorize a booking
        return None


def _booking_order_error(modo, orden_token, sid):
    if modo == "solicitud_con_orden" and not orden_token:
        return {"error": "este servicio requiere ORDEN MÉDICA: leela primero con analyze_order y volvé a llamar con el orden_token, o subila desde la web",
                "como_seguir": _base_url().rstrip('/') + f"/service/{sid}/book"}
    if orden_token:
        from app.modules.turnos import booking_referrals
        if not booking_referrals._load_pending(orden_token):
            return {"error": "ese orden_token no existe o venció (los tokens duran 48h) — volvé a leer la orden con analyze_order"}
    return None


def _tool_request(usuario, args, _invocation):
    sid = str(args.get("service_id") or "").strip()
    from app.patient_web import site_availability, site_catalog, site_schedule
    s = site_catalog._service_by_id(sid)
    if not s:
        return {"error": f"no existe el servicio '{sid}' — buscalo con search_services"}
    modo = s.get("modo")
    if not site_availability._offers_appointments(s):
        return {"error": "este servicio no reserva turnos: se atiende por orden de llegada",
                "horario": s.get("horario") or "consultar en el centro"}
    if modo in ("walkin", "walkin_con_orden"):
        # abre el mismo día: para el resto del flujo es un turno común, con o
        # sin orden según el walk-in del que viene
        modo = "solicitud_con_orden" if modo == "walkin_con_orden" else "solicitud"

    dia = str(args.get("date") or "").strip()
    hora = str(args.get("time") or "").strip()
    preferencia = str(args.get("preference") or "").strip()
    pd = _ids.dni(args.get("patient_dni"))
    titular_dni = _ids.dni((usuario or {}).get("dni"))
    paciente_dni = pd or titular_dni
    invocation = _invocation or ToolInvocation()
    intent = {key: args.get(key) for key in ('service_id','date','time','preference','patient_dni','orden_token') if args.get(key)}
    confirmed = invocation.confirmed_booking == intent
    orden_token = str(args.get("orden_token") or "").strip() or None
    if dia or hora:
        try:
            datetime.strptime(dia + 'T' + hora, '%Y-%m-%dT%H:%M')
        except ValueError:
            return {'error':'Elegí un día y horario válidos.'}
    if not confirmed:
        order_error = _booking_order_error(modo, orden_token, sid)
        if order_error:
            return order_error
        href = _base_url().rstrip('/') + f'/service/{sid}/book'
        if dia and hora:
            href += '?' + urlencode({'slot': f'{dia}T{hora}'})
        from app.core import i18n
        note = i18n.t_or('ai.booking.confirm', 'Elegí y confirmá el turno en la página del servicio.', i18n.active())
        if invocation.channel and invocation.conversation_key and dia and hora:
            # Store a proposal only. Arguments such as confirmed/approve are ignored.
            person = chat_store.get_user(paciente_dni) if paciente_dni == titular_dni else chat_store.get_dependent(titular_dni, paciente_dni)
            name = ' '.join(str((person or {}).get(k) or '') for k in ('nombre','apellido')).strip() or paciente_dni
            # {when} is the shared «day, hour» phrase («Hoy, 14:40» / «Mañana, sábado 11, 09:00» /
            # «Martes 14 de julio, 09:00»), its own sentence: no article, no «a las» around it.
            # The proposal is a line the code writes to the patient: in the
            # conversation's language, the date included, not the page's.
            lang, token = i18n.bind_lang(invocation.language or i18n.active())
            try:
                when = site_schedule._friendly_date(dia, hora)
                # {word} is the very word _is_confirm_reply accepts in this language.
                note = i18n.t_or('ai.booking.proposal', '¿Confirmás la solicitud para {patient}, {service}? {when}. Respondé «{word}».',
                                 lang, patient=name, service=s.get('nombre') or sid, when=when,
                                 word=i18n.t_or('ai.booking.confirm_word', 'Confirmar', lang))
            finally:
                i18n.unbind(token)
            chat_store.chat_save_message(invocation.channel, invocation.conversation_key,
                {'role':'booking_proposal','args':intent,'patient_dni':titular_dni,'created':clock_time(),
                 'lang':lang})
        return {'confirmation_required':True, 'href':href, 'confirmation_label':s.get('nombre') or sid, 'nota':note}
    puede_recuperar = bool(paciente_dni)
    if pd and pd != titular_dni:
        # Do not use an arbitrary DNI as a recovery lookup key. The normal
        # create path below owns the patient_no_vinculado response.
        try:
            puede_recuperar = bool(chat_store.get_dependent(titular_dni, pd))
        except Exception:  # noqa: BLE001
            puede_recuperar = False

    if dia or hora:
        if not (dia and hora):
            return {"error": "para elegir un horario concreto mandá dia Y hora (los dos)"}
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", dia) or not re.match(r"^\d{2}:\d{2}$", hora):
            return {"error": "el día o la hora no tienen el formato esperado"}

        # If the durable create completed but the browser lost the response,
        # recover the same request before checking availability.  The existing
        # hold intentionally makes the slot disappear from _slots_de().
        existente = None
        if puede_recuperar:
            try:
                existente = chat_store.active_booking_request_by_slot(
                    paciente_dni, sid, dia, hora)
            except Exception:  # noqa: BLE001 — recovery must never block booking
                existente = None
        if existente:
            return {
                "ok": True,
                "solicitud_id": existente.get("id"),
                "estado": existente.get("estado") or "pendiente",
                "turno_elegido": {"dia": dia, "hora": hora},
                "recuperada": True,
                "nota": "la solicitud ya estaba creada; recuperamos la confirmación",
            }

    order_error = _booking_order_error(modo, orden_token, sid)
    if order_error:
        return order_error

    body = {"servicio_id": sid, "centro_slug": s.get("centro") or ""}
    if orden_token:
        body["orden_token"] = orden_token

    if dia or hora:
        slots = _slots_de(s)
        if slots and not any(t["dia"] == dia and t["hora"] == hora for t in slots):
            return {"error": f"el horario {dia} {hora} no está entre los elegibles",
                    "turnos_disponibles": slots[:20]}
        body["slot_dia"], body["slot_hora"] = dia, hora
    if preferencia:
        body["preferencia"] = preferencia

    if pd:
        body["paciente_dni"] = pd

    from app.modules.turnos import booking_create
    # Trusted transport metadata, never a model-provided argument. This also
    # protects a service whose auto-confirm policy changes after our read.
    st, j = booking_create.create_request(usuario, body, allow_auto_confirmation=True)
    if st == 200 and j.get("ok"):
        state = j.get('estado') or ('aprobada' if j.get('auto_confirmada') else 'pendiente')
        out = {"ok": True, "solicitud_id": j.get("id"), "estado": state,
               "nota": "Turno confirmado por las reglas del servicio." if state == 'aprobada' else "Solicitud enviada; el hospital la revisa y avisa al paciente."}
        if dia and hora:
            out["turno_elegido"] = {"dia": dia, "hora": hora}
        return out
    err = j.get("error") or "no se pudo crear la solicitud"
    if err == "turno_tomado":
        return {"error": "justo alguien tomó ese horario — elegí otro",
                "turnos_disponibles": _slots_de(s)[:20]}
    if err == "edad_fuera_de_rango":
        return {"error": "el paciente no está en el rango de edad de este servicio",
                "edad": s.get("edad")}
    if err == "paciente_no_vinculado":
        return {"error": "ese DNI no está vinculado como persona a cargo de esta cuenta — se agrega desde Mi cuenta en la web"}
    return {"error": err}


# Las mismas etiquetas que pinta "Mis solicitudes" en la cuenta
# (account-requests.mjs, locales/*/account.mine.json): el chat las reusa para
# que un estado no se lea «pendiente» en crudo dentro de una conversación en
# ruso o inglés — el valor crudo queda en `estado`, el texto hablado es
# `estado_texto`.
_ESTADO_KEYS = {
    "pendiente": ("account.mine.requests.status_pending",
                  "El hospital la revisa"),
    "aprobada": ("account.mine.requests.status_approved", "Aprobada"),
    "rechazada": ("account.mine.requests.status_rejected", "Rechazada"),
}


def _estado_texto(estado, cancelada_por=None):
    # A cancellation reads by who cancelled — one text with the account page and the email.
    if str(estado or "").strip().lower() == "cancelada":
        from app.modules.turnos import booking_requests
        par = booking_requests.CANCELLED_LABELS.get(cancelada_por) or booking_requests.CANCELLED_LABELS[None]
    else:
        par = _ESTADO_KEYS.get(str(estado or "").strip().lower())
    if not par:
        return estado
    clave, default = par
    try:
        from app.core import i18n
        return i18n.t_or(clave, default, i18n.active())
    except Exception:  # noqa: BLE001 — degrada al español del pack
        return default


def _tool_mine(usuario, args, _invocation):
    from app.modules.turnos import booking_requests
    dni = _ids.dni(usuario.get("dni"))
    rows = [] if claim_gate.hidden(dni) else chat_store.list_by_dni(dni)
    nombres = {}
    try:
        from app.patient_web import catalog
        servicios = catalog.services()
        nombres = {s.get("id"): s.get("nombre") for s in servicios}
        nombres.update({k: v for k, v in _nombres_localizados(servicios).items() if v})
    except Exception:  # noqa: BLE001
        pass
    out = []
    max_rows = 100
    for r in rows[:max_rows]:
        row = booking_requests._public_row(r)
        row["servicio"] = (nombres.get(row.get("servicio_id"))
                           or booking_requests._service_name(row.get("servicio_id")))
        row.pop("preparacion", None)
        row["estado_texto"] = _estado_texto(row.get("estado"), row.get("cancelada_por"))
        out.append(row)
    return {"solicitudes": out, "truncated": len(rows) > max_rows}


def _tool_cancel(usuario, args, _invocation):
    from app.core import i18n
    try:
        sid = int(args.get("id"))
    except (TypeError, ValueError):
        return {"error": "id inválido"}
    return {"confirmation_required": True, "href": _base_url().rstrip('/') + f"/request/{sid}",
            "confirmation_label": i18n.t_or("ai.cancel.link", "el turno", i18n.active()),
            "nota": i18n.t_or("ai.cancel.confirm", "Para cancelar, abrí el turno y confirmá la cancelación.", i18n.active())}


def _tool_whoami(usuario, args, _invocation):
    u = usuario or {}
    dni = _ids.dni(u.get("dni"))
    out = {
        "dni": dni or None,
        "nombre": u.get("nombre"),
        "apellido": u.get("apellido"),
        "nacimiento": _iso(u.get("nacimiento")),
        "sexo": u.get("sexo"),
        "tiene_contacto": bool(u.get("contacto")),
        "tiene_email": bool(u.get("email")),
    }
    out["dependientes"] = []
    if dni:
        try:
            for d in (chat_store.list_dependents(dni) or []):
                out["dependientes"].append({
                    "dni": d.get("dni"), "nombre": d.get("nombre"),
                    "apellido": d.get("apellido"), "nacimiento": _iso(d.get("nacimiento")),
                    "sexo": d.get("sexo"),
                    "vinculo": d.get("vinculo"),
                })
        except Exception:  # noqa: BLE001
            pass
    return out


def _tool_lab_results(usuario, args, _invocation):
    """Los informes de laboratorio del titular autenticado (ai.lab_results.
    informes_de — la MISMA fuente que la pestaña Laboratorio de la cuenta).
    Un informe sensible ya sale redactado del lado del servidor: {"fecha",
    "sensible": true} y NADA más cruza el cable."""
    from app.ai import lab_results
    titular = _ids.dni((usuario or {}).get("dni"))
    if not titular:
        return {"error": "hay que iniciar sesión para ver los resultados de "
                         "laboratorio"}
    if claim_gate.hidden(titular):
        return {"informes": [], "total": 0, "analisis_pendiente": 0,
                "nota": "la cuenta todavía no fue verificada por el mostrador"}
    paciente_dni = _ids.dni(args.get("patient_dni"))
    if paciente_dni and paciente_dni != titular:
        try:
            vinculados = {
                str(d.get("dni") or "")
                for d in (chat_store.list_dependents(titular) or [])
            }
        except Exception:  # noqa: BLE001 — sin poder verificar, no se pasa
            vinculados = set()
        if paciente_dni not in vinculados:
            return {"error": "ese DNI no está vinculado como persona a cargo "
                             "de esta cuenta — se agrega desde Mi cuenta en la web"}
    informes, _fuentes, _series, pendiente = lab_results.informes_de(
        paciente_dni or titular)
    return {
        "informes": informes[:10],
        "total": len(informes),
        "analisis_pendiente": pendiente,
        "nota": ("un informe `sensible` sólo informa su fecha: el médico lo "
                 "comenta en la consulta"),
    }


def _identidad_ia(usuario, client_ip=None):
    """Identidad para el enfriamiento por imagen: el DNI de la cuenta si hay
    una autenticada; si no (no debería pasar — analyze_order queda excluido
    de las superficies anónimas), cae a una IP resuelta por el transporte."""
    dni = _ids.dni((usuario or {}).get("dni"))
    if dni:
        return f"dni:{dni}"
    return f"ip:{client_ip}" if client_ip else None


def analizar_orden_para(identity, orden_bytes, orden_mime, orden_filename,
                        servicio_id="", especialidad="", before_ai=None):
    """Gate de ai_cooldown + booking_referrals.analyze_referral_bytes, en un solo lugar
    — lo usan la tool analyze_order (MCP y chat) y whatsapp_chat.py para las
    fotos de orden que llegan como adjunto. Nunca levanta estructuralmente:
    siempre devuelve un dict con "ok" o "error"."""
    ok, espera = ai_cooldown.attempt(identity)
    if not ok:
        return {"error": "enfriamiento", "retry_after_seconds": espera,
                "mensaje": ai_cooldown.message(espera)}
    if before_ai is not None:
        blocked = before_ai()
        if blocked:
            return blocked
    from app.modules.turnos import booking_referrals
    token, ai_json, ia_disponible = booking_referrals.analyze_referral_bytes(
        orden_bytes, orden_mime, orden_filename, servicio_id, especialidad)
    out = {
        "ok": True, "orden_token": token,
        "veredicto": ai_json.get("verdict"), "resumen": ai_json.get("resumen"),
        "campos": ai_json.get("campos"),
        # documento.jurisdiccion_valida no viaja en el verdict (a diferencia
        # de es_orden_medica, que sí fuerza verdict='documento_equivocado'):
        # una orden de otra jurisdicción sigue siendo una orden real, así que
        # necesita su propia señal para que narrar_adjunto/orientar_orden_anonima
        # la puedan usar más abajo.
        "documento": ai_json.get("documento"),
    }
    if not ia_disponible:
        out["ia_disponible"] = False
    return out


def narrar_adjunto(texto, resultado):
    """Teje el resultado de analizar_orden_para en el texto que ve el motor —
    compartido por whatsapp_chat.py y chat_api.py (adjunto en el chat del
    sitio, 2026-07-23): ninguno de los dos canales tiene un "tool call"
    nativo para adjuntos, así que el adjunto se resuelve ANTES de invocar al
    motor y su resultado se narra en el texto del usuario para que el modelo
    pueda actuar (pasar el orden_token a request_appointment, por ejemplo)."""
    texto = str(texto or "").strip()
    if resultado.get("ok"):
        campos = resultado.get("campos") or {}
        utiles = {}
        for key in ("especialidad", "especialidad_solicitada", "practicas"):
            value = campos.get(key)
            if isinstance(value, dict):
                value = value.get("valor")
            if value:
                utiles[key] = value
        campos_texto = json.dumps(utiles, ensure_ascii=False, separators=(",", ":"))
        documento = resultado.get("documento") or {}
        aviso_jurisdiccion = ""
        if documento.get("jurisdiccion_valida") is False:
            detectada = documento.get("jurisdiccion_detectada")
            aviso_jurisdiccion = (
                " ADVERTENCIA: la orden parece emitida en otra jurisdicción"
                + (f" ({detectada})" if detectada else "")
                + ", no en la de este hospital — decíselo al paciente, no la uses "
                  "para reservar el turno, y ofrecele un turno con Medicina "
                  "general (no necesita orden) para conseguir una orden válida.")
        return (texto + "\n\n[Adjuntó la foto/PDF de una orden médica; "
                f"ya se leyó: orden_token={resultado.get('orden_token')}, "
                f"veredicto={resultado.get('veredicto')}, "
                f"campos={campos_texto}.{aviso_jurisdiccion} "
                "No hace falta volver a analizarla.]").strip()
    return (texto + "\n\n[Adjuntó una orden pero no se pudo leer: "
            f"{resultado.get('error')}]").strip()


def _campo_orden(campos, *keys):
    for key in keys:
        value = (campos or {}).get(key)
        if isinstance(value, dict):
            value = value.get("valor")
        if value:
            return value
    return None


def _descartar_pending(token):
    """Remove an informational-only upload once no booking can consume it."""
    try:
        import os
        from app.modules.turnos import booking_referrals
        meta = booking_referrals._load_pending(token) or {}
        path = os.path.realpath(str(meta.get("orden_path") or ""))
        base = os.path.realpath(booking_referrals._pending_dir())
        if path and os.path.commonpath((path, base)) == base:
            try:
                os.remove(path)
            except OSError:
                pass
        booking_referrals._discard_pending_meta(token)
    except Exception:  # noqa: BLE001 — privacy cleanup is best-effort
        pass


def _resultados_para_orden(rows, query):
    """Keep an uploaded order on the safest specific catalogue route.

    Search intentionally returns related services for browsing.  That is useful
    on a directory page but unsafe in the first answer to an order: an
    "ecografía infantil" must not be diluted with Medicina general merely
    because that service mentions paediatrics in its keywords, and an
    obstetric study must not default to the IVE-ILE service.  This only narrows
    a result when the order itself supplies that distinction; otherwise it
    leaves the catalogue result untouched.
    """
    rows = list(rows or [])
    query_norm = text_fold.ascii_words(query)

    def nombre(row):
        # `row["nombre"]` may already be localized (this narrows the SAME
        # "resultados" a chat visitor sees, per _tool_search's language-aware
        # nombre) — the classifiers below are hardcoded Spanish substrings, so
        # they need the CANONICAL (raw, un-localized) name via servicio_id,
        # never the display one. `catalog.services()` — not
        # site_catalog._service_by_id, which defaults to the LOCALIZED row.
        sid = str((row or {}).get("servicio_id") or "").strip()
        if sid:
            try:
                from app.patient_web import catalog
                canon = next((c for c in catalog.services() if c.get("id") == sid), None)
            except Exception:  # noqa: BLE001
                canon = None
            if canon is not None:
                return text_fold.ascii_words(canon.get("nombre"))
        return text_fold.ascii_words((row or {}).get("nombre"))

    is_ive = any(term in query_norm for term in ("ive", "ile", "interrupcion"))
    is_embarazo = any(term in query_norm for term in
                      ("obstetr", "embaraz", "gestacion", "prenatal", "morfologic"))
    is_infantil = any(term in query_norm for term in
                      ("infantil", "pediatr", "nino", "nina", "bebe", "cadera"))
    if is_ive:
        elegidos = [row for row in rows if "ive" in nombre(row) or "ile" in nombre(row)]
    elif is_embarazo:
        elegidos = [row for row in rows if "obstetrica" in nombre(row)]
    elif is_infantil:
        elegidos = [row for row in rows if "infantil" in nombre(row)]
    else:
        # A broad adult order such as "ecografía abdominal completa" maps to
        # the generic Ecografía service.  Do not distract an adult with the
        # separate paediatric option simply because both share keywords.
        elegidos = [row for row in rows if nombre(row) == "ecografia"]
    return elegidos or rows


def orientar_orden_anonima(resultado):
    """Deterministic first answer for an anonymous uploaded order.

    It never books and never asks for login. It uses the catalogue directly,
    so the first response cannot hallucinate a centre, schedule or preparation.
    """
    if not resultado.get("ok"):
        return {
            "texto": "No pude leer esa orden. Probá con una foto más clara o un PDF.",
            "tools": [], "login": False,
        }
    if resultado.get("veredicto") == "documento_equivocado":
        return {
            "texto": ("Ese archivo no parece una orden médica. Adjuntá la orden "
                      "correcta y la vuelvo a leer."),
            "tools": [], "login": False, "subir_otra_orden": True,
        }
    documento = resultado.get("documento") or {}
    if documento.get("jurisdiccion_valida") is False:
        # A diferencia de "documento_equivocado", NO pedimos subir otra
        # orden: la orden está bien, sólo que no la puede usar este centro —
        # volver a subir la misma foto no cambia nada.
        detectada = documento.get("jurisdiccion_detectada")
        return {
            "texto": ("Esa orden fue emitida en otra jurisdicción"
                      + (f" ({detectada})" if detectada else "")
                      + " y no corresponde a este hospital. Para atenderte acá "
                        "necesitás una orden de esta red — podés sacar un turno "
                        "con Medicina general, no hace falta orden para esa "
                        "consulta."),
            "tools": [], "login": False,
        }

    campos = resultado.get("campos") or {}
    practicas = _campo_orden(campos, "practicas") or []
    if isinstance(practicas, str):
        practicas = [practicas]
    especialidad = _campo_orden(
        campos, "especialidad", "especialidad_solicitada")
    query = next((str(x).strip() for x in practicas if str(x).strip()),
                 str(especialidad or "").strip())
    if not query:
        return {
            "texto": ("Pude leer el archivo, pero no identificar qué estudio o "
                      "especialidad pide. Adjuntá una foto más clara o consultá "
                      "el nombre escrito en la orden."),
            "tools": [], "login": False,
        }

    search = _tool_search(None, {"query": query}, None)
    tools = [("search_services", search)]
    rows = search.get("resultados") or []
    rows = _resultados_para_orden(rows, query)
    if not rows:
        from app.core import site_identity
        network = str(site_identity.get("network_long") or
                      "la red de salud configurada")
        return {
            "texto": (f"La orden pide **{query}**, pero no encuentro ese estudio "
                      f"en el catálogo de {network}. "
                      "Consultá el lugar de derivación con el centro que emitió "
                      "la orden."),
            "tools": tools, "login": False,
        }

    lines = [f"La orden pide **{query}**.", "", "Podés atenderte así:"]
    reservable = False
    for row in rows[:5]:
        nombre = row.get("nombre") or query
        centro = row.get("centro") or "centro a confirmar"
        if row.get("se_pide_por_aca"):
            modo = ("con turno programado y orden médica"
                    if row.get("requiere_orden") else "con turno programado")
        else:
            modo = row.get("como_se_atiende") or ""
        horario = row.get("horario") or ""
        detail = ". ".join(x for x in (modo, horario) if x)
        lines.append(f"* **{nombre} — {centro}:** {detail}".rstrip())
        if row.get("se_pide_por_aca"):
            reservable = True
            slots = _tool_slots(None, {"service_id": row.get("servicio_id")}, None)
            tools.append(("available_appointments", slots))
            turnos = slots.get("turnos") or []
            if turnos:
                from app.patient_web import site_schedule
                opciones = [
                    site_schedule._friendly_date(t.get("dia"), t.get("hora"))
                    for t in turnos[:3]
                ]
                opciones = [x for x in opciones if x]
                if opciones:
                    lines.append("  Próximos: " + "; ".join(opciones) + ".")

    if reservable:
        lines += [
            "",
            ("Elegí un horario. Recién después te voy a pedir que ingreses para "
             "confirmar el turno; no hace falta iniciar sesión para ver opciones."),
        ]
    else:
        lines += [
            "",
            ("Estos servicios son por orden de llegada: acercate con la orden en "
             "el horario publicado. No necesitás iniciar sesión ni guardar el archivo."),
        ]
        _descartar_pending(resultado.get("orden_token"))
    return {"texto": "\n".join(lines), "tools": tools, "login": False}


def _url_archivo_openai(url):
    """Only accept ChatGPT's HTTPS file CDN, never an arbitrary fetch target."""
    try:
        parsed = urlparse(str(url or ""))
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and port in (None, 443)
        and (host == _OPENAI_FILE_HOST_SUFFIX.lstrip(".")
             or host.endswith(_OPENAI_FILE_HOST_SUFFIX))
    )


class _OpenAIFileRedirect(urllib.request.HTTPRedirectHandler):
    """Keep redirects inside the same trusted file-CDN boundary."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _url_archivo_openai(newurl):
            raise urllib.error.HTTPError(
                newurl, 403, "redirección de archivo no permitida", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_openai_file(file_ref, max_bytes):
    """Resolve a ChatGPT file-param reference into the existing byte pipeline."""
    if not isinstance(file_ref, dict):
        raise ValueError("la imagen adjunta no tiene un formato válido")
    url = str(file_ref.get("download_url") or "").strip()
    if not _url_archivo_openai(url):
        raise ValueError("la URL de la imagen adjunta no es válida")

    req = urllib.request.Request(
        url, headers={"User-Agent": "Sana-Turnos-MCP/1.0", "Accept": "*/*"})
    opener = urllib.request.build_opener(_OpenAIFileRedirect())
    try:
        with opener.open(req, timeout=_FILE_DOWNLOAD_TIMEOUT) as resp:
            final_url = resp.geturl()
            if not _url_archivo_openai(final_url):
                raise ValueError("la URL final de la imagen adjunta no es válida")
            declared_len = resp.headers.get("Content-Length")
            if declared_len:
                try:
                    declared_len = int(declared_len)
                except (TypeError, ValueError):
                    declared_len = None
                if declared_len is not None and declared_len > max_bytes:
                    raise ValueError("el archivo es muy pesado")
            data = resp.read(max_bytes + 1)
            response_mime = str(resp.headers.get("Content-Type") or "").split(";", 1)[0].strip()
    except ValueError:
        raise
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        raise ValueError("no se pudo descargar la imagen adjunta")

    if len(data) > max_bytes:
        raise ValueError("el archivo es muy pesado")
    if not data:
        raise ValueError("la imagen adjunta está vacía")
    filename = str(file_ref.get("file_name") or "").strip() or None
    mime = str(file_ref.get("mime_type") or "").strip() or response_mime or None
    return data, mime, filename


def _tool_analyze_order(usuario, args, invocation):
    file_ref = args.get("file")
    data_b64 = args.get("data_b64") or ""
    filename = str(args.get("filename") or "").strip() or None
    mime = str(args.get("mime") or "").strip() or None
    from app.modules.turnos import booking_referrals
    from app.modules.turnos import booking_requests
    if file_ref:
        try:
            orden_bytes, orden_mime, orden_filename = _download_openai_file(
                file_ref, booking_requests.MAX_REFERRAL_BYTES)
        except ValueError as e:
            return {"error": str(e)}
    elif data_b64:
        try:
            orden_bytes, orden_mime, orden_filename = booking_referrals._decode_referral(
                {"filename": filename, "mime": mime, "data_b64": data_b64})
        except ValueError as e:
            return {"error": str(e)}
    else:
        return {"error": "falta la imagen o el PDF de la orden (file o data_b64)"}
    if len(orden_bytes) > booking_requests.MAX_REFERRAL_BYTES:
        return {"error": "el archivo es muy pesado"}
    servicio_id = str(args.get("service_id") or "").strip()
    invocation = invocation or ToolInvocation()
    identity = _identidad_ia(usuario, invocation.client_ip)

    def _reserve_mcp():
        if not invocation.mcp_ai_guarded:
            return None
        # Sin modelo disponible (o modo fake) no hay tokens pagos que reservar.
        from app.ai import ai_referral
        from app.ai import ai_health
        if ai_referral._env("TURNOS_FAKE_AI") == "1" or not ai_health.available():
            return None
        from app.auth import mcp_guard
        try:
            ok, used, limit = mcp_guard.reserve_ai_daily()
        except Exception:  # DB caída: el presupuesto duro falla cerrado
            return {"error": "no se pudo verificar el presupuesto diario de IA; probá en un rato"}
        if not ok:
            return {"error": "se alcanzó el presupuesto diario de análisis por MCP",
                    "usos_hoy": used, "limite_diario": limit}
        return None

    return analizar_orden_para(identity, orden_bytes, orden_mime, orden_filename,
                               servicio_id, before_ai=_reserve_mcp)


TOOL_FNS = {
    "search_doctors": _tool_search_doctors,
    "search_services": _tool_search,
    "available_appointments": _tool_slots,
    "request_appointment": _tool_request,
    "my_requests": _tool_mine,
    "cancel_request": _tool_cancel,
    "whoami": _tool_whoami,
    "my_lab_results": _tool_lab_results,
    "analyze_order": _tool_analyze_order,
}
