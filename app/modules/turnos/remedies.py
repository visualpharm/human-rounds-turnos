"""remedies.py — when something goes wrong, the patient gets the REMEDY.

A rejection that only says "your referral is expired" leaves the patient
exactly where they started: with a problem and no idea what to do. This
module translates each rejection cause into the concrete NEXT STEP — which
service to go to, what slots are open, and a link that goes STRAIGHT to that
appointment (never to the home page: "use deep links, not to the homepage",
The product owner 2026-07-27).

Example of the case that motivated it: the referral expired. The patient
doesn't need to be told it expired, they need a new referral — i.e. an
appointment with General practice. The rejection email/WhatsApp arrives with
three open slots and one click per slot.

Two consumers:
  - notifications.notify_rejected() -> email + WhatsApp to the patient.
  - booking_requests._admin_row() -> `servicio_sugerido`, so the admin queue
    prefills the "Refer to" combo when the referral is for another specialty.

The service matcher (`buscar_servicio`) is the Python mirror of
search.js:puntuar/variantes — same morphological variants and same tiers. It
lives here and not on the client because both paths (the admin combo and the
rejection email) have to suggest the SAME service: if they diverge, the
operator refers to one and the patient gets the other's link.

Best-effort at every edge: no catalog, no schedule, or no match, each
function returns None/[] and the caller sends the usual plain notice. A
rejection never breaks because we couldn't find the remedy.

i18n (2026-08-04): `remedy()`/`email_lines()`/`whatsapp_text()` take an
optional `lang`, defaulting to `i18n.active()` — the language of whoever is
driving the CURRENT request. That default is right for the admin combo
(`suggested_service`, always Spanish) but wrong the moment a caller wants the
PATIENT's own language instead of the operator's — an approval/rejection
happens inside the STAFF's request, not the patient's. A future sender that
knows the patient's saved language preference should pass it explicitly:
`remedies.remedy(row, motivo, handler, lang=paciente_lang)`, and the same
`lang` through to `email_lines`/`whatsapp_text` so the whole message (titles,
body, the "Horarios libres" line, the WhatsApp greeting) comes out in one
language. The service/center NAMES embedded in the text go through
`pack_l10n.localize()` too, so they read in the visitor's language once the
translation cache has them — a cold cache degrades to the pack's own Spanish
name for that one string, same as every other pack-content read in the site.
"""

import re

from app.core import i18n
from app.core import search_exclusions
from app.core import text_fold
from app.modules.turnos import appointment_page_support

# Canonical causes. The key comes from the standard reason's text
# (admin.html:MOTIVOS_ORDEN/MOTIVOS_GENERAL) — a closed, curated list, so
# recognizing its phrase is enough; a free-text reason ("Otro motivo…")
# matches none of them and the patient gets the plain notice, with no
# invented remedy.
EXPIRED = "vencida"
NOT_A_REFERRAL = "no_es_orden"
UNREADABLE = "ilegible"
WRONG_PERSON = "otra_persona"
DNI_MISMATCH = "dni_distinto"
WRONG_SERVICE = "otro_servicio"
NO_SIGNATURE = "sin_firma"
NO_DATE = "sin_fecha"
NO_SLOTS = "sin_turnos"
NOT_PERFORMED_HERE = "no_se_realiza"
WRONG_JURISDICTION = "otra_jurisdiccion"

_REASON_TO_CAUSE = [
    (r"vencid", EXPIRED),
    (r"no es una orden", NOT_A_REFERRAL),
    (r"no se lee", UNREADABLE),
    (r"nombre de otra persona", WRONG_PERSON),
    (r"dni de la orden no coincide", DNI_MISMATCH),
    (r"no corresponde al servicio", WRONG_SERVICE),
    (r"firma o la matr", NO_SIGNATURE),
    # Careful with the order: "no tiene fecha" must not be swallowed by
    # `vencid` above — an undated order is not an expired one, and the way
    # out is different (a new order, not "the same one, more recent").
    (r"no tiene fecha", NO_DATE),
    (r"no hay turnos disponibles", NO_SLOTS),
    (r"no se realiza en este centro", NOT_PERFORMED_HERE),
    (r"otra jurisdicci", WRONG_JURISDICTION),
]

# Service whoever needs a new or properly filled-out referral gets sent to.
# It's the clinician: at the municipal hospital that's who prescribes and refers.
_GENERAL_PRACTICE = "medicina general"

_MAX_SLOTS = 3   # three slots in the email: enough to choose from without


#                                                    looking like a full grid

def cause_of(motivo):
    """Rejection-reason text -> canonical cause (or None if it's free text)."""
    t = _norm(motivo)
    for patron, causa in _REASON_TO_CAUSE:
        if re.search(patron, t):
            return causa
    return None


# ---------------------------------------------------------------- matching
# Mirror of search.js (variantes/tierPalabra/puntuar). See the note above on
# why this lives server-side.

_STOP = {"de", "del", "la", "las", "el", "los", "un", "una", "y", "o",
         "para", "con", "en", "al", "dr", "dra", "lic"}
# Words about the PROCEDURE, not the service. A phrase that degrades down to
# just these isn't searched: "Consulta cardiológica" degraded to "consulta"
# used to match any service that had that loose word and returned a
# suggestion unrelated to the referral.
_PROCEDURAL_WORDS = {"turno", "turnos", "sacar", "pedir", "cita", "hora", "consulta",
         "consultas", "control", "orden", "derivacion", "solicito", "solicita"}
_IRREGULAR_STEMS = {
    "obstetra": "obstetr", "obstetras": "obstetr", "obstetricia": "obstetr",
    "medico": "medic", "medica": "medic", "medicos": "medic", "medicas": "medic",
    "diabetes": "diabet", "diabetico": "diabet", "diabetica": "diabet",
    "diabeticos": "diabet", "diabeticas": "diabet",
    "enfermero": "enfermer", "enfermera": "enfermer", "enfermeria": "enfermer",
    "enfermeros": "enfermer", "enfermeras": "enfermer",
}


def _norm(s):
    return " ".join(text_fold.strip_nonspacing(str(s or "").lower()).split())


def _word_edges(s):
    return " " + re.sub(r"[^a-z0-9]+", " ", _norm(s)) + " "


def _variants(w):
    out = [w]
    if w in _IRREGULAR_STEMS:
        out.append(_IRREGULAR_STEMS[w])
    m = re.match(r"^(.{2,})olog(?:[oa]s?|ias?)$", w)
    if m:
        out.append(m.group(1) + "olog")
    m = re.match(r"^(.{2,})iatr(?:as?|ias?)$", w)
    if m:
        out.append(m.group(1) + "iatr")
    m = re.match(r"^(.{2,})graf(?:ias?|istas?)$", w)
    if m:
        out.append(m.group(1) + "graf")
    m = re.match(r"^(.{4,})ic[oa]s?$", w)
    if m:
        out.append(m.group(1) + "ic")
    if len(w) > 4 and re.search(r"[a-z]es$", w):
        out.append(w[:-2])
    if len(w) > 3 and re.search(r"[aeiou]s$", w):
        out.append(w[:-1])
    return out


def _service_fields(s):
    """Same fields as the home search (search.js): the name leads, the rest
    (category/words/schedule) breaks ties."""
    principal = str(s.get("nombre") or "")
    resto = " ".join(str(x) for x in [
        s.get("categoria") or "", s.get("sede") or "",
        " ".join(s.get("palabras") or []),
    ])
    return {"pt": _norm(principal), "pb": _word_edges(principal),
            "rt": _norm(resto), "rb": _word_edges(resto),
            "exc": [_norm(x) for x in (s.get("palabras_excluidas") or []) if x]}


def _tier(w, campos):
    vs = _variants(w)
    for v in vs:
        p = campos["pb"].find(" " + v)
        if p == 0:
            return 0
        if p > 0:
            return 1
    for v in vs:
        if v in campos["pt"]:
            return 2
    for v in vs:
        if (" " + v) in campos["rb"]:
            return 3
    for v in vs:
        if v in campos["rt"]:
            return 4
    return None


def _score(campos, q):
    palabras = [w for w in re.sub(r"[.,;:!?¿¡()\"“”«»]+", " ", _norm(q)).split()
                if w not in _STOP]
    if not palabras:
        return None
    if search_exclusions.is_excluded(_norm(q), palabras, campos["exc"], _variants):
        return None
    score = 0
    for w in palabras:
        t = _tier(w, campos)
        if t is None:
            return None
        score += t
    return score


def _find_with_average(q, campos, centro=None):
    """(servicio, tier promedio) del mejor match, o None. Degrada por la COLA
    de la frase igual que search.js:buscarFrase — la orden nombra el estudio
    primero y la letra del médico alrededor haría cero con un AND estricto."""
    palabras = [w for w in _norm(q).split(" ") if w]
    mejor = None
    while palabras:
        # Una frase degradada hasta quedar sólo en palabras del trámite no se
        # busca: "Consulta cardiológica" reducido a "consulta" matcheaba
        # cualquier servicio que tuviera esa palabra y devolvía una sugerencia
        # sin relación con la orden (visto con una orden de CARDIOLOGIA que
        # terminaba proponiendo Psicología).
        utiles = [w for w in palabras if w not in _STOP and w not in _PROCEDURAL_WORDS]
        if utiles:
            query = " ".join(palabras)
            puntajes = []
            for s, c in campos:
                p = _score(c, query)
                if p is not None:
                    puntajes.append((p, s))
            if puntajes:
                promedio = min(p for p, _ in puntajes) / len(utiles)
                if mejor is None or promedio < mejor[1]:
                    # Empate de puntaje: gana el centro del paciente, después
                    # el de más horas semanales (misma señal de popularidad
                    # que la búsqueda pública).
                    puntajes.sort(key=lambda ps: (
                        ps[0],
                        0 if centro and ps[1].get("centro") == centro else 1,
                        -_hours(ps[1]),
                    ))
                    mejor = (puntajes[0][1], promedio)
        palabras.pop()
    return mejor


def find_service(q, servicios=None, centro=None, solo_turnos=True):
    """Mejor servicio del catálogo para una frase ("Ergometría de 12
    derivaciones", "cardiólogo"), o None.

    `centro` (slug) desempata a favor del centro donde el paciente ya pidió:
    de dos Psiquiatrías, la de su CAPS. `solo_turnos` descarta los walk-in —
    sugerir "andá a este servicio" con un link de turno que no existe sería
    prometer algo que el sitio no puede cumplir.
    """
    campos = _catalog_fields(servicios, solo_turnos)
    if not campos:
        return None
    r = _find_with_average(q, campos, centro)
    return r[0] if r else None


def _catalog_fields(servicios=None, solo_turnos=True):
    filas = servicios if servicios is not None else _catalog_services()
    if solo_turnos:
        filas = [s for s in filas if _bookable(s)]
    return [(s, _service_fields(s)) for s in filas]


def _hours(s):
    try:
        from app.patient_web import site_availability
        return site_availability._week_hours(s) or 0
    except Exception:  # noqa: BLE001
        return 0


def _catalog_services():
    try:
        from app.patient_web import catalog
        return [s for s in (catalog.services() or []) if isinstance(s, dict)]
    except Exception:  # noqa: BLE001
        return []


def _bookable(s):
    """Servicio que ofrece turnos online (el único al que tiene sentido mandar
    un deep link de turno). Reusa el portero de patient_web/site_*.py para no divergir
    del sitio; sin patient_web/site_*.py importable cae a los modos de solicitud."""
    try:
        from app.patient_web import site_availability
        return bool(site_availability._offers_appointments(s))
    except Exception:  # noqa: BLE001
        return s.get("modo") in ("solicitud", "solicitud_con_orden")


def _service_by_id(sid):
    for s in _catalog_services():
        if str(s.get("id")) == str(sid):
            return s
    return None


# --------------------------------------------- servicio sugerido por la orden

def referral_phrases(ai_json):
    """Lo que la orden PIDE, como frases SUELTAS: cada práctica por su lado
    ("Ergometría de 12 derivaciones") y la especialidad por el suyo.

    Sueltas y no concatenadas a propósito: el matcher degrada por la cola, así
    que en una sola frase la especialidad —que va al final— se descarta en el
    primer paso y nunca llega a buscarse. Cada frase compite por su cuenta y
    gana el mejor tier promedio, que es lo que deja ganar a la práctica cuando
    nombra un estudio concreto y a la especialidad cuando la práctica es
    genérica."""
    ai = ai_json or {}
    campos = ai.get("campos") or {}
    out = []
    practicas = (campos.get("practicas") or {})
    valores = practicas.get("valor") if isinstance(practicas, dict) else practicas
    if isinstance(valores, str):
        valores = [valores]
    for p in (valores or []):
        if p:
            out.append(str(p))
    esp = campos.get("especialidad") or {}
    esp_val = esp.get("valor") if isinstance(esp, dict) else esp
    if esp_val:
        out.append(str(esp_val))
    return out


def suggested_service(row):
    """El servicio que la ORDEN pide, cuando NO es el que el paciente eligió.

    Devuelve {id, nombre, centro, centro_nombre, motivo} o None. None cuando
    la orden coincide con lo solicitado, cuando no se pudo leer qué pide, o
    cuando el match cae en el mismo servicio: sugerir lo ya elegido sería
    ruido, y peor, haría dudar al operador de una solicitud correcta.
    """
    try:
        ai = row.get("ai_json") or {}
        if not ai:
            return None
        campos = ai.get("campos") or {}
        esp = campos.get("especialidad") or {}
        # Sólo cuando la lectura CONTRADICE al servicio pedido. `coincide` es
        # None cuando no se pudo comparar (ai_referral nunca inventa un conflicto):
        # ahí no hay nada que corregir.
        if not (isinstance(esp, dict) and esp.get("coincide") is False):
            return None
        frases = referral_phrases(ai)
        if not frases:
            return None
        if _asks_for_something_we_dont_do(frases):
            return None
        actual = str(row.get("servicio_id") or "")
        campos = _catalog_fields()
        if not campos:
            return None
        mejor = None
        for q in frases:
            r = _find_with_average(q, campos, row.get("centro_slug"))
            if r and r[1] < _MAX_TIER_FOR_SUGGESTION and (mejor is None or r[1] < mejor[1]):
                mejor = r
        s = mejor[0] if mejor else None
        if not s or str(s.get("id")) == actual:
            return None
        return {
            "id": s.get("id"),
            "nombre": s.get("nombre") or s.get("id"),
            "centro": s.get("centro") or "",
            "centro_nombre": _center_name(s.get("centro")),
            "motivo": (esp.get("valor") or "").strip(),
        }
    except Exception:  # noqa: BLE001
        return None


# A match with average tier >= 3 came from the service's WORDS, not its name.
# That's enough for a search (where the user sees the list and picks), but
# not to prefill a referral: here the suggestion changes what gets approved,
# so it's only suggested when the referral NAMES the service. With this
# floor, "Diagnóstico por imágen" (a category, not a service) stops
# proposing Ecografía infantil, and "CARDIOLOGIA" keeps proposing Cardiología.
_MAX_TIER_FOR_SUGGESTION = 3


def _not_offered():
    """Sinónimos de los estudios que el hospital NO realiza.

    Dos archivos desde la fase 2b de data packs: la base genérica
    (`estudios-preparacion.json`, L1 packs/global/ — el catálogo de estudios
    con sus `match`) y el overlay de la localidad activa
    (`estudios-preparacion-meta.json`, currently in the private deployment pack — the
    lista `no_ofrecidos_actualmente_hospital_pinamar`, ver su
    `no_ofrecidos_nota`). Una localidad sin overlay (packs/example-town/,
    una localidad nueva) degrada a "no hay veto" — este veto es dato LOCAL,
    no de la base.

    Se descarta todo sinónimo con algún token de menos de 3 letras: "rm de"
    aporta el token "rm", subcadena de enfeRMo y de deRMatología — con él, una
    orden de dermatología se quedaría sin sugerencia. Lo cubren igual
    "resonancia" y "rmn". Tres letras SÍ entran: "rmn", "eeg" y "dxa" son
    siglas inequívocas y son justo como las escribe una orden."""
    try:
        import json
        from app.core import sitedata
        with open(sitedata.path("estudios-preparacion.json"), encoding="utf-8") as f:
            d = json.load(f)
        try:
            with open(sitedata.path("estudios-preparacion-meta.json"), encoding="utf-8") as f:
                overlay_meta = (json.load(f) or {}).get("_meta") or {}
        except OSError:
            overlay_meta = {}
        ids = set(overlay_meta.get("no_ofrecidos_actualmente_hospital_pinamar") or [])
        out = []
        for e in (d.get("estudios") or []):
            if isinstance(e, dict) and e.get("id") in ids:
                for m in (e.get("match") or []):
                    n = _norm(m)
                    # Los artículos no cuentan: lo que importa es que ninguna
                    # palabra CON contenido sea un fragmento ambiguo.
                    tokens = [t for t in n.split(" ") if t not in _STOP]
                    if tokens and all(len(t) >= 3 for t in tokens):
                        out.append(n)
        return out
    except Exception:  # noqa: BLE001
        return []


def _asks_for_something_we_dont_do(frases):
    """True si alguna frase de la orden nombra un estudio que no realizamos.
    Ante eso NO se sugiere nada: el paciente necesita un prestador externo, y
    proponerle el servicio 'más parecido' del catálogo (una ecografía por una
    resonancia) sería mandarlo a un estudio que no es el que le pidieron."""
    no = _not_offered()
    if not no:
        return False
    texto = _norm(" ".join(frases))
    return any(m in texto for m in no)


def _center_name(slug):
    if not slug:
        return ""
    try:
        from app.patient_web import catalog
        for c in (catalog.centers() or []):
            if isinstance(c, dict) and str(c.get("slug") or c.get("id")) == str(slug):
                return c.get("name") or ""
    except Exception:  # noqa: BLE001
        pass
    return ""


def _localized(s, lang):
    """`s` con su `nombre` en el idioma de lectura, para lo que el PACIENTE ve.

    El catálogo que usa el buscador (`_catalog_fields`/`find_service`) queda
    intacto en español a propósito: la orden que lo alimenta la lee la IA en
    español, y el operador de la cola también trabaja en español (i18n.md: "el
    admin sigue siendo español-first"), así que traducir esos campos rompería
    el matcheo sin beneficiar a nadie. Sólo la copia que se muestra cambia de
    idioma — nunca la que se compara. Un centro NO pasa por acá: su nombre es
    un topónimo y pack_l10n.FIELDS ya lo deja afuera (nunca se traduce)."""
    if not s:
        return s
    try:
        from app.core import pack_l10n
        return pack_l10n.localize("servicios", [s], lang)[0]
    except Exception:  # noqa: BLE001
        return s


def _mismo_servicio_otro_centro(actual, actual_id):
    """El mismo servicio (mismo nombre) en un centro distinto al que ya se
    pidió — la salida para NO_SLOTS/NOT_PERFORMED_HERE ("hay turnos en otro
    centro"). Ejemplo real del catálogo: Psiquiatría se atiende en CAPS
    Pinamar y en CAPS DiabetPin, dos filas con el mismo nombre y `centro`
    distinto."""
    if not actual:
        return None
    nombre = _norm(actual.get("nombre") or "")
    if not nombre:
        return None
    for s in _catalog_services():
        if str(s.get("id")) == str(actual_id):
            continue
        if _norm(s.get("nombre") or "") != nombre:
            continue
        if s.get("centro") == actual.get("centro"):
            continue
        if _bookable(s):
            return s
    return None


# ------------------------------------------------------------------- turnos

# Día/mes es un duplicado local a propósito (mismo criterio de
# notifications._friendly_appointment_time, que tiene el suyo): el nombre del
# día/mes es un "algoritmo acoplado al idioma" que el repo todavía no
# centralizó (docs/architecture/i18n.md, "language-coupled algorithms" —
# no diseñado aún). Acá alcanza con las dos variantes que el sitio soporta hoy.
_WEEKDAYS = {
    "es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
}
_MONTHS = {
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
           "septiembre", "octubre", "noviembre", "diciembre"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December"],
}


def _when(dia, hora, lang=None):
    """'Martes 29 de julio, 08:00' / 'Tuesday, July 29, 08:00' — sin
    relativos: un mail se lee más tarde (mismo criterio que
    notifications._friendly_appointment_time). `lang=None` (o cualquier cosa
    que no sea inglés) da exactamente el formato de siempre — nada cambia
    para una instalación que no habilitó un segundo idioma."""
    try:
        import datetime
        d = datetime.date.fromisoformat(str(dia))
        primary = str(lang or "es").split("-")[0].lower()
        if primary == "en":
            return (f"{_WEEKDAYS['en'][d.weekday()]}, "
                    f"{_MONTHS['en'][d.month - 1]} {d.day}, {hora}")
        wd = _WEEKDAYS["es"][d.weekday()].capitalize()
        return f"{wd} {d.day} de {_MONTHS['es'][d.month - 1]}, {hora}"
    except Exception:  # noqa: BLE001
        return f"{dia} {hora}".strip()


def slots_for(servicio, limite=_MAX_SLOTS, lang=None):
    """Primeros horarios REALMENTE libres del servicio, ya con su deep link.
    Sale de site_availability._appointments_for_service — la misma función detrás de la grilla
    que ve el paciente, así que el mail no puede ofrecer un turno que la web
    después no deja elegir (mismo invariante que la banda y las pastillas)."""
    if not servicio:
        return []
    try:
        from app.patient_web import site_availability
        data = site_availability._appointments_for_service(servicio) or {}
    except Exception:  # noqa: BLE001
        return []
    out = []
    for m in (data.get("medicos") or []):
        for t in (m.get("turnos") or []):
            dia, hora = t.get("dia"), t.get("hora")
            if dia and hora:
                out.append({"dia": dia, "hora": hora,
                            "cuando": _when(dia, hora, lang),
                            "medico": m.get("medico") or ""})
    out.sort(key=lambda t: (t["dia"], t["hora"]))
    return out[:limite]


# -------------------------------------------------------------------- links

def _base(handler):
    try:
        from app.core import site_identity
        return site_identity.base_url(handler)
    except Exception:  # noqa: BLE001
        return "http://127.0.0.1:7700"


def appointment_link(base, servicio_id, dia=None, hora=None):
    """Deep link al wizard. Con día y hora llega con el turno YA elegido y
    salta al paso siguiente (?slot=, ver site_listing._slot_fields)."""
    url = f"{base}/service/{servicio_id}/book"
    if dia and hora:
        url += f"?slot={dia}T{hora}"
    return url


def referral_link(base, servicio_id):
    """Subir la orden de nuevo, con el servicio ya puesto."""
    return f"{base}/upload-order?service={servicio_id}"


# ------------------------------------------------------------------ remedio

def _remedy_reupload(causa, base, lang, actual, actual_id):
    """NOT_A_REFERRAL / UNREADABLE / WRONG_PERSON / DNI_MISMATCH: the booking
    is fine, only the paper is wrong."""
    # El turno está bien pedido; lo que falta es el papel correcto.
    # Volver a subirlo NO exige rehacer nada más.
    titulo_por_causa = {
        NOT_A_REFERRAL: ("remedy.titulo.subir_orden", "Subí la orden médica y listo"),
        UNREADABLE: ("remedy.titulo.foto_nitida",
                      "Subí una foto más nítida de la orden y listo"),
        WRONG_PERSON: ("remedy.titulo.orden_a_tu_nombre",
                       "Subí la orden que está a tu nombre y listo"),
        DNI_MISMATCH: ("remedy.titulo.orden_a_tu_nombre",
                       "Subí la orden que está a tu nombre y listo"),
    }
    titulo_key, titulo_default = titulo_por_causa[causa]
    actual_l = _localized(actual, lang)
    nombre = (actual_l or {}).get("nombre") or _service_name(actual_id)
    return {
        "titulo": i18n.t_or(titulo_key, titulo_default, lang),
        "detalle": i18n.t_or(
            "remedy.detalle.reupload",
            ("Tu pedido de {nombre} ya está armado: sólo falta la orden. "
             "Sacale una foto donde se lea todo (incluida la fecha) y "
             "subila acá — no tenés que empezar de nuevo."),
            lang, nombre=nombre),
        "acciones": [{"texto": i18n.t_or("remedy.accion.subir_orden",
                                          "Subir la orden", lang),
                      "url": referral_link(base, actual_id)}],
        "turnos": [],
    }


def _remedy_new_referral(causa, row, base, lang):
    """EXPIRED / NO_SIGNATURE / NO_DATE / WRONG_JURISDICTION: None when the
    catalog has no general practitioner to send the patient to."""
    # Necesita una orden nueva. Eso es una consulta con el clínico —
    # y el clínico se saca sin orden, así que el paciente puede
    # resolverlo entero desde el mail.
    clinico = find_service(_GENERAL_PRACTICE, centro=row.get("centro_slug"))
    if not clinico:
        return None
    turnos = slots_for(clinico, lang=lang)
    detalle_key = "remedy.detalle.orden_nueva"
    detalle_default = ("Para que te den una orden nueva necesitás una consulta con "
                        "Medicina general. No hace falta orden para sacar ese turno.")
    if causa == NO_SIGNATURE:
        detalle_key = "remedy.detalle.sin_firma"
        detalle_default = ("Para que la orden sirva le falta la firma o la matrícula "
                            "del médico. Si podés, pedile que te la complete; si no, "
                            "sacá un turno con Medicina general y te hacen una nueva.")
    elif causa == NO_DATE:
        # Sin fecha no hay forma de saber si sigue vigente, y una orden
        # vieja ya no vale. Mismo camino que la firma: primero pedirle
        # al médico que la complete, y si no, una orden nueva acá.
        detalle_key = "remedy.detalle.sin_fecha"
        detalle_default = ("La orden no tiene fecha, así que no podemos saber si sigue "
                            "vigente. Si podés, pedile al médico que le ponga la fecha; "
                            "si no, sacá un turno con Medicina general y te hacen una "
                            "nueva.")
    elif causa == WRONG_JURISDICTION:
        # No es que la orden esté mal escrita: es de otro sistema de
        # salud (otra provincia u otro país) y este centro no puede
        # usarla. La salida real no es "conseguí la misma orden de
        # nuevo" — es que te evalúen acá y, si corresponde, te den
        # una propia dentro de esta red.
        detalle_key = "remedy.detalle.otra_jurisdiccion"
        detalle_default = ("La orden que subiste fue emitida en otra jurisdicción y no "
                            "corresponde a este centro. Sacá un turno con Medicina "
                            "general: no hace falta orden, y ahí te evalúan y te dan una "
                            "orden válida para esta red si corresponde.")
    return {
        "titulo": i18n.t_or("remedy.titulo.medicina_general",
                            "Sacá un turno con Medicina general", lang),
        "detalle": i18n.t_or(detalle_key, detalle_default, lang),
        "acciones": _slot_actions(base, _localized(clinico, lang), turnos, lang),
        "turnos": turnos,
        "servicio": _service_ref(clinico),
    }


def _remedy_wrong_service(row, base, lang):
    """WRONG_SERVICE: the specialty the referral asks for, or None when the
    reading does not say which one."""
    sug = suggested_service(row) or {}
    destino = _service_by_id(sug.get("id")) if sug.get("id") else None
    if not destino:
        # Sabemos que la orden es de otra cosa pero no de qué: mejor
        # dejar el aviso simple que mandarlo a un servicio al azar.
        return None
    turnos = slots_for(destino, lang=lang)
    centro = _center_name(destino.get("centro"))
    destino_l = _localized(destino, lang)
    nombre = destino_l.get("nombre")
    if centro:
        detalle = i18n.t_or(
            "remedy.detalle.otro_servicio_centro",
            "Sacá el turno en {nombre} ({centro}) y usá la misma orden: "
            "sirve tal cual está.",
            lang, nombre=nombre, centro=centro)
    else:
        detalle = i18n.t_or(
            "remedy.detalle.otro_servicio",
            "Sacá el turno en {nombre} y usá la misma orden: sirve tal cual está.",
            lang, nombre=nombre)
    return {
        "titulo": i18n.t_or("remedy.titulo.otro_servicio", "Tu orden es para {nombre}",
                            lang, nombre=nombre),
        "detalle": detalle,
        "acciones": _slot_actions(base, destino_l, turnos, lang),
        "turnos": turnos,
        "servicio": _service_ref(destino),
    }


def _remedy_other_center(base, lang, actual, actual_id):
    """NO_SLOTS / NOT_PERFORMED_HERE: the same service at another center, or
    None when there is no twin or it has no free slot either."""
    # El mismo servicio en OTRO centro. Los centros del partido están a
    # ~10 minutos entre sí, así que viajar un poco le gana a esperar
    # semanas (mismo criterio que la elección de estudio por espera).
    otro = _mismo_servicio_otro_centro(actual, actual_id)
    if not otro:
        return None
    turnos = slots_for(otro, lang=lang)
    if not turnos:
        return None
    centro = _center_name(otro.get("centro"))
    otro_l = _localized(otro, lang)
    nombre = otro_l.get("nombre")
    centro_txt = centro or i18n.t_or("remedy.centro_generico", "otro centro", lang)
    if centro:
        detalle = i18n.t_or(
            "remedy.detalle.otro_centro_nombrado",
            "{nombre} también se atiende en {centro}. Los centros del partido "
            "están a pocos minutos entre sí.",
            lang, nombre=nombre, centro=centro)
    else:
        detalle = i18n.t_or(
            "remedy.detalle.otro_centro_generico",
            "{nombre} también se atiende en otro centro. Los centros del partido "
            "están a pocos minutos entre sí.",
            lang, nombre=nombre)
    return {
        "titulo": i18n.t_or("remedy.titulo.otro_centro", "Hay turnos en {centro}",
                            lang, centro=centro_txt),
        "detalle": detalle,
        "acciones": _slot_actions(base, otro_l, turnos, lang),
        "turnos": turnos,
        "servicio": _service_ref(otro),
    }


def remedy(row, motivo, handler=None, lang=None):
    """Causa del rechazo -> qué hacer, con links que resuelven en un click.

    {"titulo", "detalle", "acciones": [{"texto","url"}], "turnos": [...]}
    o None si para esa causa no tenemos una salida mejor que el aviso simple
    (nunca se inventa una: preferimos el mail corto de siempre a mandar a
    alguien a un servicio que no le sirve).

    `lang` (default `i18n.active()`): ver la nota de idioma en el docstring
    del módulo — quien llama sin pasarlo obtiene el idioma de la request
    ACTUAL, que para el flujo de hoy (rechazo disparado por el operador) es el
    del operador, no necesariamente el del paciente.
    """
    try:
        causa = cause_of(motivo)
        if not causa:
            return None
        lang = lang or i18n.active()
        base = _base(handler)
        actual_id = row.get("servicio_id")
        actual = _service_by_id(actual_id)

        if causa in (NOT_A_REFERRAL, UNREADABLE, WRONG_PERSON, DNI_MISMATCH):
            return _remedy_reupload(causa, base, lang, actual, actual_id)
        if causa in (EXPIRED, NO_SIGNATURE, NO_DATE, WRONG_JURISDICTION):
            return _remedy_new_referral(causa, row, base, lang)
        if causa == WRONG_SERVICE:
            return _remedy_wrong_service(row, base, lang)
        if causa in (NO_SLOTS, NOT_PERFORMED_HERE):
            return _remedy_other_center(base, lang, actual, actual_id)
        return None
    except Exception:  # noqa: BLE001 — el rechazo YA persistió; nunca romper acá
        return None


def _service_ref(s):
    return {"id": s.get("id"), "nombre": s.get("nombre") or s.get("id"),
            "centro": s.get("centro") or "", "centro_nombre": _center_name(s.get("centro"))}


_service_name = appointment_page_support.service_name


def _slot_actions(base, servicio, turnos, lang=None):
    """Un link por horario libre — el click ES la reserva del turno, no una
    invitación a buscarlo. Sin horarios (agenda vacía), un link al servicio:
    sigue siendo directo, sólo que sin la hora ya elegida."""
    sid = servicio.get("id")
    if not turnos:
        nombre = servicio.get("nombre")
        texto = i18n.t_or("remedy.accion.ver_turnos", "Ver turnos de {nombre}", lang,
                           nombre=nombre)
        return [{"texto": texto, "url": appointment_link(base, sid)}]
    return [{"texto": t["cuando"], "url": appointment_link(base, sid, t["dia"], t["hora"])}
            for t in turnos]


# ------------------------------------------------------------------- textos

def email_lines(rem, lang=None):
    """Bloque de texto plano para el mail del rechazo. [] si no hay remedio.

    `lang` sólo localiza el chrome propio de esta función (la línea
    "Horarios libres…") — `rem["titulo"]`/`rem["detalle"]` ya vienen en el
    idioma con el que se llamó a `remedy()`; pasarle acá un `lang` distinto
    mezclaría dos idiomas en el mismo mail, así que quien arma el envío debe
    usar el MISMO valor en ambas llamadas."""
    if not rem:
        return []
    lang = lang or i18n.active()
    lineas = ["", rem["titulo"] + ":"]
    if rem.get("detalle"):
        lineas.append(rem["detalle"])
    if rem.get("turnos"):
        lineas.append("")
        lineas.append(i18n.t_or("remedy.email.horarios_libres",
                                 "Horarios libres (el link te deja el turno elegido):", lang))
    for a in rem.get("acciones") or []:
        lineas.append(f"- {a['texto']}: {a['url']}")
    return lineas


def whatsapp_text(rem, paciente="", servicio="", lang=None):
    """Mismo remedio, en el formato corto de WhatsApp. None si no hay remedio
    (sin solución que ofrecer no mandamos un WhatsApp sólo para dar una mala
    noticia — para eso ya está el mail).

    `lang` — mismo trato que en `email_lines`: sólo el saludo y la primera
    línea son de esta función, el resto ya viene resuelto en `rem`."""
    if not rem:
        return None
    lang = lang or i18n.active()
    if paciente:
        saludo = i18n.t_or("remedy.whatsapp.saludo_nombre", "Hola, {paciente}.", lang,
                            paciente=paciente)
    else:
        saludo = i18n.t_or("remedy.whatsapp.saludo", "Hola.", lang)
    partes = [saludo]
    if servicio:
        partes.append(i18n.t_or("remedy.whatsapp.rechazo_servicio",
                                 "No pudimos aprobar tu pedido de {servicio}.", lang,
                                 servicio=servicio))
    else:
        partes.append(i18n.t_or("remedy.whatsapp.rechazo", "No pudimos aprobar tu pedido.", lang))
    partes.append(rem["titulo"] + ".")
    if rem.get("detalle"):
        partes.append(rem["detalle"])
    for a in rem.get("acciones") or []:
        partes.append(f"{a['texto']}: {a['url']}")
    return "\n".join(partes)


# Tier 2 batch D dependency-direction fix (see notifications/send.py's own
# comment): notifications/ is a domain package and must never statically
# import a module — this one's own docstring already names
# notifications.notify_rejected() as a consumer, so it registers ITSELF
# there instead, the allowed direction (a module importing a domain
# package). Runs once, at import time (booking_requests.py imports this
# unconditionally at boot for `servicio_sugerido`).
import sys
from app.notifications import send as _notifications_send
_notifications_send.register_turnos(remedies=sys.modules[__name__])
