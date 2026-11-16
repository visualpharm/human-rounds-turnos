"""reports_funnel.py — «Recorrido del paciente»: how far the requests of a period got, step by step.

GET  /api/admin/reports/funnel?days=7|30|90&center=<slug>&service=<id>&doctor=<id>
POST /api/admin/reports/funnel/steps   {"steps": [{"key": "<step>", "on": true|false}, ...]}

The unit is a booking request created in the period (the only record that follows a patient from asking to
being seen). Every step is a fact the database keeps about that request — nothing is inferred from a side
signal:

  pedido      the request exists                                  booking_requests row
  aprobada    it was given a turno (even if cancelled later)       status 'aprobada' or an «aprobada» /
                                                                   «restaurada» row in appointment_events
  confirmada  the patient answered «sí, voy»                       confirmation = 'si'
  turno_paso  the day of the turno arrived and it still stood      appointment_at < now(), status 'aprobada'
  presente    the desk marked the patient as present               attendance = 'presente'

Which steps the report shows, and in what order, is a setting (`catalog` config/report_funnel,
`{"steps": [{"key", "on"}]}`) the admin edits from the report itself; a step's count is the requests that
pass it AND every earlier step that is on. Where the patient's path has no record at all (visits to the
site or a service page, a booking started and abandoned, signing up) the report names the gap instead of
inventing a number (`SIN_DATOS`).

Counts only: the payload carries no DNI, name, contact or request id (`tests/test_reports_funnel.py`).
"""

from urllib.parse import parse_qs

from app.core import db_catalog
from app.core import db_reports
from app.core import i18n
from app.admin_panel import reports

CONFIG_ID = "report_funnel"

# (key, on by default). The order is the default order of the report.
STEPS = (
    ("pedido", True),
    ("aprobada", True),
    ("confirmada", False),
    ("turno_paso", True),
    ("presente", True),
)
_KEYS = tuple(k for k, _ in STEPS)

_STEP_LABELS = {
    "pedido": "Pidieron turno",
    "aprobada": "Les dieron turno",
    "confirmada": "Confirmaron que van",
    "turno_paso": "Les llegó el turno",
    "presente": "Fueron al turno",
}

# Reasons that are not losses: the request may still move on (pending, turno ahead), has hung without an
# answer, or is a past turno whose attendance nobody marked (unknown, not «did not come»).
_EN_CURSO = ("pendiente", "por_venir", "colgada", "sin_marcar")

# Of those, the ones that are on their way: counted together in the hero, apart from the losses.
_EN_CAMINO = ("pendiente", "por_venir")

# A pending request older than `db_reports.COLGADA_DIAS` days has hung: the same constant is the SQL
# interval and the `{dias}` of the phrase.

# reason -> (one, other) Spanish phrase with the count.
_MOTIVOS = {
    "rechazada": ("{n} fue rechazada", "{n} fueron rechazadas"),
    "cancelada": ("{n} se canceló", "{n} se cancelaron"),
    "pendiente": ("{n} sigue pendiente", "{n} siguen pendientes"),
    "colgada": ("{n} lleva más de {dias} días sin respuesta", "{n} llevan más de {dias} días sin respuesta"),
    "dijo_que_no": ("{n} avisó que no va", "{n} avisaron que no van"),
    "sin_respuesta": ("{n} no respondió", "{n} no respondieron"),
    "por_venir": ("{n} tiene el turno más adelante", "{n} tienen el turno más adelante"),
    "ausente": ("{n} no fue", "{n} no fueron"),
    "sin_marcar": ("{n} sin asistencia marcada", "{n} sin asistencia marcada"),
    "otro": ("{n} por otro motivo", "{n} por otro motivo"),
}

# What the desk can do about the main reason a step loses people.
_ACCIONES = {
    "rechazada": "Los motivos de rechazo están en «Gestión».",
    "cancelada": "Los turnos cancelados vuelven a ofrecerse a la lista de espera.",
    "sin_respuesta": "Muchos no contestan la confirmación: el recordatorio puede ir por otro canal.",
    "dijo_que_no": "Los que avisan que no van liberan su turno para otro paciente.",
    "ausente": "Un recordatorio más cerca del turno ayuda a que no se pierda.",
    "sin_marcar": "Asistencia sin marcar: {n}. Se marca en {pantalla}.",
}

# The screen where the desk acts on a reason: (path, i18n key of its name in the menu, Spanish name). The
# phrase names the screen and carries the link, so the reader goes straight to it.
_PANTALLAS = {
    "sin_marcar": ("/admin/cola", "admin.shell.nav.cola", "Pacientes de hoy"),
}

# Parts of the path the database does not record, named in the report.
SIN_DATOS = (
    ("visitas", "Entrar al sitio o a la página de un servicio"),
    ("abandono", "Empezar a elegir un turno y dejarlo a mitad de camino"),
    ("cuenta", "Crear la cuenta o iniciar sesión"),
)


def _lang():
    return i18n.active()


def _step_label(key):
    return i18n.t_or("admin.srv.reports.recorrido.paso." + key, _STEP_LABELS[key], _lang())


# ---------------------------------------------------------------- the setting

def _normalize(steps):
    """Known keys in the given order, then any step the saved order lacks (a step added to the code later)
    with its default; unknown keys and repeats dropped."""
    defaults = dict(STEPS)
    out, seen = [], set()
    for item in steps or []:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        if key not in defaults or key in seen:
            continue
        seen.add(key)
        out.append({"key": key, "on": bool(item.get("on"))})
    for key, on in STEPS:
        if key not in seen:
            out.append({"key": key, "on": on})
    return out


def steps_config():
    """The saved steps, or the default order; never raises (a setting is never why a report fails)."""
    try:
        row = db_catalog.catalog_get("config", CONFIG_ID)
        saved = ((row or {}).get("doc") or {}).get("steps")
    except Exception:  # noqa: BLE001 — DB down/missing => default order
        saved = None
    cfg = _normalize(saved if isinstance(saved, list) else [])
    if not any(s["on"] for s in cfg):
        cfg = _normalize([])
    return cfg


def save_steps(steps):
    """Validates and stores the admin's order. Raises ValueError (shown as 400) on an unknown step, a repeat
    or no step on."""
    if not isinstance(steps, list) or not steps:
        raise ValueError(i18n.t_or("admin.srv.reports.recorrido.error.lista", "Falta la lista de pasos.", _lang()))
    keys = [s.get("key") if isinstance(s, dict) else None for s in steps]
    if any(k not in _KEYS for k in keys) or len(set(keys)) != len(keys):
        raise ValueError(i18n.t_or("admin.srv.reports.recorrido.error.paso", "Hay un paso que no existe.", _lang()))
    cfg = _normalize(steps)
    if not any(s["on"] for s in cfg):
        raise ValueError(i18n.t_or("admin.srv.reports.recorrido.error.ninguno",
                                   "Dejá al menos un paso encendido.", _lang()))
    db_catalog.catalog_upsert("config", CONFIG_ID, {"steps": cfg})
    return cfg


# ---------------------------------------------------------------- the counts

def _passes(key, r):
    if key == "pedido":
        return True
    if key == "aprobada":
        return r["aprobada"]
    if key == "confirmada":
        return r["confirmation"] == "si"
    if key == "turno_paso":
        # Still approved when its hour came: a turno cancelled, or rejected after the approval (the EMR
        # conflict resolver does that), did not stand.
        return r["paso"] and r["status"] == "aprobada"
    if key == "presente":
        return r["attendance"] == "presente"
    return False


def _motivo(key, r):
    """Why a request that reached the previous step did not pass `key`."""
    status = r["status"]
    if status == "rechazada":
        return "rechazada"
    if status == "cancelada":
        return "cancelada"
    if not r["aprobada"] and status == "pendiente":
        return "colgada" if r.get("colgada") else "pendiente"
    if key == "confirmada":
        if r["confirmation"] == "no":
            return "dijo_que_no"
        # Approved, turno still ahead, not confirmed yet: on its way, not a loss.
        return "sin_respuesta" if r["paso"] else "por_venir"
    if key in ("turno_paso", "presente") and not r["paso"]:
        return "por_venir"
    if key == "presente":
        return "ausente" if r["attendance"] == "ausente" else "sin_marcar"
    return "otro"


def _motivo_texto(key, n):
    one, other = _MOTIVOS[key]
    return i18n.tn_or("admin.srv.reports.recorrido.motivo." + key, n, one, other, _lang(),
                      dias=db_reports.COLGADA_DIAS)


def count_steps(rows, config):
    """The steps that are on, in order: [{key, label, n, pct, perdidos, en_curso, motivos}]. `rows` are the
    grouped rows of `db_reports.reports_patient_path`; `pct` is over the first step."""
    activos = [s["key"] for s in config if s["on"]]
    vivos = list(rows)
    out = []
    for i, key in enumerate(activos):
        pasan = [r for r in vivos if _passes(key, r)]
        n = sum(r["n"] for r in pasan)
        motivos = {}
        if i:
            for r in vivos:
                if not _passes(key, r):
                    m = _motivo(key, r)
                    motivos[m] = motivos.get(m, 0) + r["n"]
        en_curso = sum(v for k, v in motivos.items() if k in _EN_CURSO)
        perdidos = sum(v for k, v in motivos.items() if k not in _EN_CURSO)
        ordenados = sorted(motivos.items(), key=lambda kv: (kv[0] in _EN_CURSO, -kv[1], kv[0]))
        out.append({
            "key": key,
            "label": _step_label(key),
            "n": n,
            "perdidos": perdidos,
            "en_curso": en_curso,
            "motivos": [{"key": k, "n": v, "texto": _motivo_texto(k, v), "en_curso": k in _EN_CURSO}
                        for k, v in ordenados if v],
        })
        vivos = pasan
    total = out[0]["n"] if out else 0
    for s in out:
        s["pct"] = round(100.0 * s["n"] / total, 1) if total else None
    return out


def _mayor_perdida(pasos):
    """The transition that really lost most requests: rejected, cancelled, said no, did not come. What is
    still on its way (pending, turno ahead), hung or without attendance marked is not lost and is said
    apart (`_en_camino`, the step's own reasons). A tie goes to the bigger share."""
    best = None
    for prev, cur in zip(pasos, pasos[1:]):
        n = cur["perdidos"]
        if n <= 0 or not prev["n"]:
            continue
        pct = round(100.0 * n / prev["n"])
        if best is None or (n, pct) > (best["n"], best["pct"]):
            reales = [m for m in cur["motivos"] if not m["en_curso"]]
            best = {
                "desde": prev["key"],
                "hasta": cur["key"],
                "n": n,
                "pct": pct,
                "motivo": reales[0]["key"] if reales else None,
            }
    return best


def _en_camino(pasos):
    """How many requests of the period are still on their way: pending or with the turno ahead."""
    return sum(m["n"] for p in pasos for m in p["motivos"] if m["key"] in _EN_CAMINO)


def _hero(pasos, mayor):
    lang = _lang()
    if not pasos or not pasos[0]["n"]:
        return []
    first, last = pasos[0], pasos[-1]
    frases = []
    camino = _en_camino(pasos)
    # Out of the requests whose path is decided: one with the turno still
    # ahead cannot have come yet (the demo read «4188 de 10238» with 4 000
    # turnos to come); those are said apart, in the «Aparte» phrase.
    decididas = first["n"] - camino
    if len(pasos) > 1 and decididas > 0:
        frases.append({"tono": "ok", "texto": i18n.tn_or(
            "admin.srv.reports.recorrido.hero.lead", last["n"],
            "{n} de {total} llegó hasta «{paso}».", "{n} de {total} llegaron hasta «{paso}».", lang,
            total=i18n.format_number(decididas, 0), paso=last["label"]),
            # The share the summary card shows over this same phrase: one
            # denominator for the number and the fraction under it.
            "pct": round(100.0 * last["n"] / decididas, 1)})
    else:
        frases.append({"tono": "ok", "texto": i18n.tn_or(
            "admin.srv.reports.recorrido.hero.solo", first["n"],
            "{n} solicitud en el período.", "{n} solicitudes en el período.", lang)})
    if mayor:
        labels = {p["key"]: p["label"] for p in pasos}
        frases.append({"tono": "atencion", "texto": i18n.tn_or(
            "admin.srv.reports.recorrido.hero.perdida", mayor["n"],
            "Donde más se pierde: entre «{desde}» y «{hasta}» se quedó {n}, el {pct}%.",
            "Donde más se pierde: entre «{desde}» y «{hasta}» se quedaron {n}, el {pct}%.", lang,
            desde=labels[mayor["desde"]], hasta=labels[mayor["hasta"]], pct=i18n.format_percent(mayor["pct"] or 0).rstrip("%"))})
    if camino and len(pasos) > 1:
        frases.append({"tono": "info", "texto": i18n.tn_or(
            "admin.srv.reports.recorrido.hero.en_camino", camino,
            "Aparte: {n} sigue en curso, entre pendientes y con el turno más adelante.",
            "Aparte: {n} siguen en curso, entre pendientes y con el turno más adelante.", lang)})
    if mayor and mayor["motivo"] in _ACCIONES:
        frases.append(_accion(mayor["motivo"], lang))
    return frases


def _accion(motivo, lang, n=None):
    """What the desk can do about a reason, as a phrase; with `enlace` when it is done on a screen.
    `motivo` names the reason, so the same action is recognised whatever the language of its words.
    `n` is required when the phrase counts (`{n}`): without it the phrase would print «0»."""
    if n is None and "{n}" in _ACCIONES[motivo]:
        raise ValueError("the action %r counts requests: pass n" % motivo)
    frase, variables = {"tono": "info", "motivo": motivo}, {} if n is None else {"n": n}
    if motivo in _PANTALLAS:
        href, key, es = _PANTALLAS[motivo]
        variables["pantalla"] = i18n.t_or(key, es, lang)
        frase["enlace"] = {"href": href, "texto": variables["pantalla"]}
    frase["texto"] = i18n.t_or("admin.srv.reports.recorrido.accion." + motivo, _ACCIONES[motivo], lang,
                               **variables)
    return frase


def _acciones_de_pantalla(pasos, frases, lang, de_paso=True):
    """{step key: phrase}: the reasons the desk fixes on a screen of its own (attendance not marked), for
    the steps that have them, unless the hero already says it — the action is offered whenever there is
    something to do, not only when it is the biggest loss, and never twice. `de_paso`: the phrase stands in
    for its reason in Recorrido's step list; Gestión shows it alone, as a whole sentence."""
    dichas = {f.get("motivo") for f in frases}
    out = {}
    for p in pasos:
        for m in p["motivos"]:
            if m["key"] in _PANTALLAS:
                if m["key"] not in dichas:
                    out[p["key"]] = _accion_de_paso(m, lang) if de_paso else _accion(m["key"], lang, m["n"])
    return out


# Next to a step the reason is already listed with its count: the action only adds where it is done, in the
# same phrase («3 sin asistencia marcada — se marca en Pacientes de hoy»), never a second count of the same
# people (ui-expert 15.11: «3 sin asistencia marcada. Asistencia sin marcar: 3. Se marca en …»).
_ACCIONES_DE_PASO = {
    "sin_marcar": "{motivo} — se marca en {pantalla}",
}


def _accion_de_paso(m, lang):
    """The step's reason `m` (a `motivos` item) with what the desk does about it; `motivo` names the reason
    the phrase replaces in the step's list. A reason with a screen and no step phrase keeps the whole one."""
    if m["key"] not in _ACCIONES_DE_PASO:
        return _accion(m["key"], lang, m["n"] if "{n}" in _ACCIONES[m["key"]] else None)
    href, key, es = _PANTALLAS[m["key"]]
    pantalla = i18n.t_or(key, es, lang)
    texto = i18n.t_or("admin.srv.reports.recorrido.accion_paso." + m["key"], _ACCIONES_DE_PASO[m["key"]], lang,
                      motivo=m["texto"], pantalla=pantalla)
    return {"tono": "info", "motivo": m["key"], "texto": texto, "enlace": {"href": href, "texto": pantalla}}


# ---------------------------------------------------------------- filters

def _param(qs, name):
    v = (qs.get(name) or [""])[0].strip()
    return v[:120] or None


def params(query):
    """(dias, centro, servicio, medico) from the raw query string."""
    qs = parse_qs(query or "")
    return reports._days_param(query), _param(qs, "center"), _param(qs, "service"), _param(qs, "doctor")


def _doctor_names():
    try:
        from app.patient_web import catalog
        return {m.get("id"): m.get("nombre") for m in catalog.doctors()
                if m.get("id") and m.get("nombre") and not m.get("rol")}
    except Exception:  # noqa: BLE001 — catalog unreadable => ids stay out of the list
        return {}


def _options(rows, filtros):
    """The filter lists: what has requests in the period, with names from the catalog, by count; a value
    already chosen stays in its list even with no requests."""
    centros_n, servicios_n, medicos_n = {}, {}, {}
    for r in rows:
        for bucket, key in ((centros_n, r["centro"]), (servicios_n, r["servicio"]), (medicos_n, r["medico"])):
            if key:
                bucket[key] = bucket.get(key, 0) + r["n"]
    for bucket, chosen in ((centros_n, filtros["centro"]), (servicios_n, filtros["servicio"]),
                           (medicos_n, filtros["medico"])):
        if chosen and chosen not in bucket:
            bucket[chosen] = 0
    centros = reports._center_map()
    servicios = {k: v["nombre"] for k, v in reports._service_map().items()}
    medicos = _doctor_names()

    def lista(bucket, names, chosen):
        items = [{"id": k, "nombre": names.get(k) or k, "n": n} for k, n in bucket.items()
                 if k in names or k == chosen]
        return sorted(items, key=lambda o: (-o["n"], (o["nombre"] or "").lower()))

    return {
        "centros": lista(centros_n, centros, filtros["centro"]),
        "servicios": lista(servicios_n, servicios, filtros["servicio"]),
        "medicos": lista(medicos_n, medicos, filtros["medico"]),
    }


# ---------------------------------------------------------------- payload

def payload_config(config):
    return [{"key": s["key"], "on": s["on"], "label": _step_label(s["key"])} for s in config]


def summary(dias):
    """The same booking path, unfiltered, for the Gestión report: its funnel shows these steps with
    these names and numbers, never a second count of its own (critical review 08.11, E7).
    `aprobadas` is every request that was given an appointment, whatever the step setup."""
    rows = db_reports.reports_patient_path(dias, None, None, None)
    pasos = count_steps(rows, steps_config())
    mayor = _mayor_perdida(pasos)
    frases = [f for f in _hero(pasos, mayor) if f["tono"] != "ok"]
    if mayor and mayor["motivo"] == "rechazada":
        # That advice sends the reader to Gestión, where this funnel already is.
        frases = [f for f in frases if f.get("motivo") != "rechazada"]
    frases += _acciones_de_pantalla(pasos, frases, _lang(), de_paso=False).values()
    return {
        "pasos": pasos,
        "frases": frases,
        "aprobadas": sum(r["n"] for r in rows if r["aprobada"]),
    }


def payload(dias, centro=None, servicio=None, medico=None):
    lang = _lang()
    filtros = {"centro": centro, "servicio": servicio, "medico": medico}
    config = steps_config()
    rows = db_reports.reports_patient_path(dias, centro, servicio, medico)
    pasos = count_steps(rows, config)
    mayor = _mayor_perdida(pasos)
    hero = _hero(pasos, mayor)
    return {
        "dias": dias,
        "filtros": filtros,
        "opciones": _options(db_reports.reports_patient_path_options(dias), filtros),
        "pasos_config": payload_config(config),
        "pasos": pasos,
        "mayor_perdida": mayor,
        "hero": hero,
        "acciones": _acciones_de_pantalla(pasos, hero, lang),
        "sin_datos": [{"key": k, "label": i18n.t_or("admin.srv.reports.recorrido.sin_datos." + k, es, lang)}
                      for k, es in SIN_DATOS],
    }
