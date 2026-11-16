"""chart_summary.py — «Resumen del paciente»: el resumen pre-visita que el
médico ve al abrir la ficha (docs/roadmap/resumen-historia-clinica.md, hecho
realidad el 2026-08-01).

Tres fuentes, en orden de cercanía (el diseño de tres niveles del roadmap):
  1. Lo nuestro: solicitudes del paciente (db_booking.list_by_dni — servicio, fechas,
     asistencia, y los campos que la IA ya leyó de la orden subida, ai_json).
  2. La entrevista previa (modules/intake): triage, resumen, brief, banderas
     rojas — si el módulo está apagado o falla, se sigue sin ella.
  3. La historia clínica digitalizada (capability clinical_history_lookup:
     connectors/alepo + connectors/marqui). Contrato de degradación: un
     conector no configurado o caído (ProviderUnavailable) se saltea en
     silencio y el resumen queda en el nivel "sólo lo nuestro" — nunca un
     error.

Los NÚMEROS de los gráficos nunca los escribe la IA: las series se extraen
por código de los `valores` estructurados de los laboratorios digitalizados
(_series). La IA sólo ELIGE qué series mirar (por id) y redacta la lectura —
si alucina un id, se descarta. Regla dataviz: una serie con <3 puntos no se
grafica.

Pre-generación (pedido del responsable del producto, captura 2026-08-01): un daemon (start(),
mismo patrón que hsi_resync) recorre cada hora los turnos aprobados de las
próximas TURNOS_SUMMARY_HORIZON_H horas (36 por defecto) y deja el resumen
listo — al abrir la ficha es instantáneo. Generar a demanda queda como
fallback para el mismo día/walk-in. `fingerprint` (hash de las fuentes)
evita regenerar lo que no cambió.

Rutas (server.py delega vía routing.py):
  GET  /api/admin/patient-summary?dni=<dni>[&medico=<id>]
       rol medico → SIEMPRE su propio medico_id (query param ignorado, mismo
       criterio que doctor_patients.py); admin/operador eligen con ?medico=.
       Si no hay resumen fresco lo genera en el momento (bloquea unos
       segundos; el panel muestra "Generando…").
  POST /api/admin/patient-summary  {dni, medico?}  → regeneración forzada.

Config: TURNOS_SUMMARY_PREGEN=0 apaga el daemon (en la demo viene apagado salvo PREGEN=1); TURNOS_SUMMARY_HORIZON_H,
TURNOS_SUMMARY_TICK_MIN ajustan ventana e intervalo. TURNOS_FAKE_AI=1 (o la
falta de clave de OpenRouter) usa el armado determinístico, sin IA.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
from datetime import datetime

from app import connectors
from app.core import db_booking
from app.core import db_clinical
from app.core.status_error import StatusError
from app.ai import ai_client
from app.ai import ai_health
from app.core import log_pii
from app.core import pii
from app.core import theme
from app.core import config
from app.ai import prompts
from app.core import tz as _tz

_ART = _tz.tzinfo()

from app.core.ai_policy import DEFAULT_MODEL

_ROUTE_RE = re.compile(r"^/api/admin/patient-summary$")

# Catálogo de series graficables: clave canónica de `valores` → metadatos.
# `rango_ok` es la banda de referencia sombreada del gráfico (None = sin
# banda, p. ej. peso). Mantener chico y explícito: una clave que no está acá
# no se grafica.
_SERIES_DEF = {
    "glucemia_mg_dl": {"label": "Glucemia", "unidad": "mg/dL", "rango_ok": [70, 110]},
    "hba1c_pct": {"label": "HbA1c", "unidad": "%", "rango_ok": [4.0, 5.7]},
    "colesterol_total_mg_dl": {"label": "Colesterol total", "unidad": "mg/dL", "rango_ok": [120, 200]},
    "ldl_mg_dl": {"label": "LDL", "unidad": "mg/dL", "rango_ok": [50, 130]},
    "hdl_mg_dl": {"label": "HDL", "unidad": "mg/dL", "rango_ok": [40, 100]},
    "trigliceridos_mg_dl": {"label": "Triglicéridos", "unidad": "mg/dL", "rango_ok": [50, 150]},
    "creatinina_mg_dl": {"label": "Creatinina", "unidad": "mg/dL", "rango_ok": [0.6, 1.2]},
    "hb_g_dl": {"label": "Hemoglobina", "unidad": "g/dL", "rango_ok": [12, 16]},
    "tsh_mui_l": {"label": "TSH", "unidad": "mUI/L", "rango_ok": [0.4, 4.5]},
    "peso_kg": {"label": "Peso", "unidad": "kg", "rango_ok": None},
    "imc": {"label": "IMC", "unidad": "kg/m²", "rango_ok": [18.5, 25]},
    "ta_sistolica": {"label": "TA sistólica", "unidad": "mmHg", "rango_ok": [90, 130]},
    "ta_diastolica": {"label": "TA diastólica", "unidad": "mmHg", "rango_ok": [60, 85]},
}

_MIN_PUNTOS = 3          # dataviz: menos de 3 puntos no es una tendencia
_MAX_STR = 400
_LIMITES = {"pendientes": 5, "preguntas": 6, "medicacion": 10,
            "antecedentes": 8, "tendencias": 3, "indicadores": 4,
            "advertencias": 3}


def _log(msg):
    print(f"[chart_summary] {msg}", file=sys.stderr, flush=True)


def _model():
    from app.core import ai_policy
    return ai_policy.model()


# ---- fuentes -----------------------------------------------------------------

_HISTORY_LABELS = {"alepo": "Alepo", "marqui": "Marqui",
                   "local_history": "Archivo clínico",
                   "patient_uploads": "Aportado por el paciente"}

# Cómo se le presenta al médico (y al modelo) una entrada `tercero: true`:
# la trajo el paciente, nadie del sistema de salud la verificó.
_TERCERO_NOTA = "aportado por el paciente, sin verificar"


def _history_connector_ids():
    """Every discovered clinical_history_lookup connector, in the stable
    fan-out order: `local_history` (the installation's own archive) first,
    then the rest sorted by id — which reproduces the historical hard-coded
    order `local_history, alepo, marqui` exactly, and lets a newly registered
    archive join the fan-out with no code change here. A fan-out, never a
    single "active" connector: every archive answers. Order matters only for
    the provenance line — `_fingerprint` is order-insensitive, so cached
    summaries survive a re-ordering."""
    ids = sorted(m["id"] for m in connectors.connectors_for_capability(
        connectors.CLINICAL_HISTORY_LOOKUP))
    if "local_history" in ids:
        ids.remove("local_history")
        ids.insert(0, "local_history")
    return ids


def _history_label(sistema):
    """Provenance label for a history source id: the curated label for the
    in-tree archives, a generic one (`some_archive` → "Some Archive") for any
    other discovered id — never a KeyError on an id this file never heard of."""
    return _HISTORY_LABELS.get(sistema) or str(sistema).replace("_", " ").title()


def _historia_clinica(dni):
    """Entradas de los conectores clinical_history_lookup disponibles.

    Alepo y Marqui son siempre consultados porque cada uno tiene un modo
    real honesto (no configurado) y un fake para la demo.  El archivo local
    sólo se agrega cuando la instalación tiene su JSON privado configurado
    o montado en el volumen persistente: así ni la demo ni una instalación
    nueva mencionan una fuente que no eligieron.

    Cada conector caído/no configurado se saltea — contrato de degradación
    de connectors/__init__.py. Each archive is reached through its registry
    adapter (`connectors._instantiate_adapter(id)`, registry rewire
    2026-08-20); an adapter that cannot be instantiated counts as
    `no_disponible` and is logged, exactly like the failed import it
    replaces. Devuelve (entradas, estado_por_sistema).

    Los conectores corren en paralelo: el GET de labs / el resumen del
    médico no paga la suma de latencias, sólo la del más lento."""
    from concurrent.futures import ThreadPoolExecutor

    def _uno(conn_id):
        adapter = connectors._instantiate_adapter(conn_id)
        if adapter is None:
            return conn_id, [], "no_disponible", "adapter could not be instantiated"
        try:
            return conn_id, list(adapter.lookup_by_dni(dni) or []), "ok", None
        except connectors.ProviderUnavailable:
            return conn_id, [], "no_disponible", None  # not configured / down: silent by contract
        except Exception as e:  # noqa: BLE001 — anything else is worth a log line
            return conn_id, [], "no_disponible", f"failed: {e!r}"

    entradas, estados = [], {}
    conn_ids = list(_history_connector_ids())
    if conn_ids:
        with ThreadPoolExecutor(max_workers=len(conn_ids)) as pool:
            futs = [pool.submit(_uno, c) for c in conn_ids]
            # Se recogen en orden de registro (no as_completed): el orden de
            # las entradas y los estados queda determinístico aunque los
            # conectores corran en paralelo.
            for fut in futs:
                conn_id, filas, estado, err = fut.result()
                entradas.extend(filas)
                estados[conn_id] = estado
                if err is not None:
                    _log(f"connector {conn_id}: {err}")
    entradas.sort(key=lambda x: x.get("fecha") or "")
    return entradas, estados


def _turnos(dni):
    """Historial de solicitudes propio, compactado para el prompt."""
    try:
        filas = db_booking.list_by_dni(dni)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in filas:
        ai = r.get("ai_json") or {}
        campos = ai.get("campos") or ai
        turno_dt = r.get("turno_dt")
        out.append({
            "fecha": (turno_dt or r.get("created")).astimezone(_ART).date().isoformat()
            if (turno_dt or r.get("created")) else None,
            "servicio": r.get("servicio_id"),
            "estado": r.get("estado"),
            "asistencia": r.get("asistencia"),
            "medico": r.get("medico_asignado"),
            "orden": {k: campos.get(k) for k in
                      ("diagnostico", "practicas", "especialidad_solicitada",
                       "medico_firmante")
                      if isinstance(campos, dict) and campos.get(k)} or None,
        })
    return out


def _entrevistas(dni):
    """The patient's pre-consult interviews (intake module), compacted —
    asked by NAME ("interviews_compact_by_dni", the hook that
    modules/intake/app.py registers). Best-effort: module off or table
    missing → empty list (hooks.call() fires nothing while the module is
    off and swallows an exception from a body that blows up)."""
    try:
        from app.core import app_modules
        res = app_modules.hooks.call("interviews_compact_by_dni", dni)
        return res[0] if res else []
    except Exception:  # noqa: BLE001
        return []


def _series(historia):
    """Series numéricas extraídas POR CÓDIGO de los `valores` de la historia
    digitalizada. La IA nunca aporta puntos — sólo elige ids de acá."""
    crudo = {}
    for e in historia:
        fecha = e.get("fecha")
        valores = e.get("valores")
        if not fecha or not isinstance(valores, dict):
            continue
        for k, v in valores.items():
            if k not in _SERIES_DEF:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            crudo.setdefault(k, {})[fecha] = v  # misma fecha → último gana
    out = []
    for k, puntos in crudo.items():
        if len(puntos) < _MIN_PUNTOS:
            continue
        meta = _SERIES_DEF[k]
        out.append({
            "id": k, "label": meta["label"], "unidad": meta["unidad"],
            "rango_ok": meta["rango_ok"],
            "puntos": sorted([[f, v] for f, v in puntos.items()]),
        })
    out.sort(key=lambda s: len(s["puntos"]), reverse=True)
    return out


def _fingerprint(turnos, entrevistas, historia, especialidad):
    """Hash estable de las fuentes: si nada cambió, no se regenera."""
    base = {
        "t": [(t.get("fecha"), t.get("estado"), t.get("asistencia")) for t in turnos],
        "e": [(e.get("fecha"), e.get("triage"), e.get("estado")) for e in entrevistas],
        "h": [len(historia), historia[-1].get("fecha") if historia else None],
        "esp": especialidad or "",
        "prompt_editado": bool(prompts.is_edited("resumen_paciente")),
    }
    return hashlib.sha256(
        json.dumps(base, ensure_ascii=False, sort_keys=True, default=str)
        .encode()).hexdigest()[:32]


# ---- generación --------------------------------------------------------------

def _coerce_str(v, maxlen=_MAX_STR):
    return str(v).strip()[:maxlen] if isinstance(v, (str, int, float)) else ""


def _coerce(parsed, series_ids):
    """Valida/recorta el JSON del modelo al contrato. Un id de tendencia que
    no existe en las series reales se descarta (la IA no inventa datos)."""
    if not isinstance(parsed, dict):
        parsed = {}
    out = {"titular": _coerce_str(parsed.get("titular"), 200)}

    pendientes = []
    for p in (parsed.get("pendientes") or [])[: _LIMITES["pendientes"]]:
        if not isinstance(p, dict):
            continue
        prioridad = str(p.get("prioridad") or "").lower()
        pendientes.append({
            "prioridad": prioridad if prioridad in ("alta", "media", "baja") else "media",
            "titulo": _coerce_str(p.get("titulo"), 120),
            "sub": _coerce_str(p.get("sub"), 160),
            "kpi_valor": _coerce_str(p.get("kpi_valor"), 24),
            "kpi_unidad": _coerce_str(p.get("kpi_unidad"), 16),
            "kpi_estado": p.get("kpi_estado") if p.get("kpi_estado") in
            ("ok", "atencion", "alerta") else "atencion",
            "detalle": _coerce_str(p.get("detalle"), 500),
            "fuente": _coerce_str(p.get("fuente"), 80),
        })
    out["pendientes"] = [p for p in pendientes if p["titulo"]]

    out["preguntas"] = [_coerce_str(q, 240) for q in
                        (parsed.get("preguntas") or [])[: _LIMITES["preguntas"]]
                        if _coerce_str(q)]

    meds = []
    for m in (parsed.get("medicacion") or [])[: _LIMITES["medicacion"]]:
        if not isinstance(m, dict) or not _coerce_str(m.get("nombre")):
            continue
        meds.append({
            "nombre": _coerce_str(m.get("nombre"), 80),
            "dosis": _coerce_str(m.get("dosis"), 60),
            "desde": _coerce_str(m.get("desde"), 20),
            "estado": m.get("estado") if m.get("estado") in
            ("activo", "suspendido", "verificar") else "verificar",
            "fuente": _coerce_str(m.get("fuente"), 80),
        })
    out["medicacion"] = meds

    out["antecedentes"] = [
        {"titulo": _coerce_str(a.get("titulo"), 90),
         "texto": _coerce_str(a.get("texto"), 400)}
        for a in (parsed.get("antecedentes") or [])[: _LIMITES["antecedentes"]]
        if isinstance(a, dict) and _coerce_str(a.get("titulo"))]

    tendencias = []
    for t in (parsed.get("tendencias") or [])[: _LIMITES["tendencias"]]:
        if isinstance(t, dict) and t.get("id") in series_ids:
            tendencias.append({"id": t["id"],
                               "comentario": _coerce_str(t.get("comentario"), 240)})
    out["tendencias"] = tendencias

    out["advertencias"] = [_coerce_str(a, 200) for a in
                           (parsed.get("advertencias") or [])[: _LIMITES["advertencias"]]
                           if _coerce_str(a)]
    return out


def _extract_json(content):
    content = (content or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```[a-z]*\s*|\s*```$", "", content)
    try:
        return json.loads(content)
    except (ValueError, TypeError):
        m = re.search(r"\{.*\}", content, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except (ValueError, TypeError):
                return None
    return None


def _deterministic(turnos, entrevistas, historia, series, motivo):
    """Resumen sin IA (TURNOS_FAKE_AI / sin clave / fallo del modelo): datos
    reales, armado mecánico — nunca texto inventado."""
    partes = []
    if historia:
        anios = sorted({str(e.get("fecha") or "")[:4] for e in historia if e.get("fecha")})
        partes.append(f"Historia digitalizada: {len(historia)} registros"
                      + (f" ({anios[0]}–{anios[-1]})" if anios else ""))
    if turnos:
        partes.append(f"{len(turnos)} solicitudes en este sistema")
    if entrevistas:
        partes.append(f"entrevista previa {entrevistas[0].get('triage') or 'sin triage'}")
    titular = "; ".join(partes) or "Sin datos previos en los sistemas conectados."

    pendientes = []
    for s in series:
        rango = s.get("rango_ok")
        if not rango or not s["puntos"]:
            continue
        fecha, valor = s["puntos"][-1]
        if valor < rango[0] or valor > rango[1]:
            pendientes.append({
                "prioridad": "media",
                "titulo": f"{s['label']} fuera de rango",
                "sub": f"Último valor registrado ({fecha[:4]})",
                "kpi_valor": f"{valor:g}", "kpi_unidad": s["unidad"],
                "kpi_estado": "atencion",
                "detalle": f"{s['label']} {valor:g} {s['unidad']} el {fecha}; "
                           f"rango de referencia {rango[0]}–{rango[1]}.",
                "fuente": "laboratorios digitalizados",
            })
    pendientes = pendientes[: _LIMITES["pendientes"]]

    meds, vistos = [], set()
    for e in reversed(historia):
        for m in (e.get("medicamentos") or []):
            nombre = str(m.get("nombre") or "").strip()
            if not nombre or nombre.lower() in vistos:
                continue
            vistos.add(nombre.lower())
            anio = str(e.get("fecha") or "")[:4]
            meds.append({"nombre": nombre, "dosis": str(m.get("dosis") or ""),
                         "desde": anio, "estado": "verificar",
                         "fuente": f"{e.get('sistema') or 'historia'} {anio}"})
    meds = meds[: _LIMITES["medicacion"]]

    preguntas = [f"¿Sigue tomando {m['nombre']}?" for m in meds[:3]]
    if entrevistas and entrevistas[0].get("banderas_rojas"):
        preguntas.append("Revisar las banderas rojas de la entrevista previa.")

    antecedentes = []
    tipos_utiles = {"evolucion", "epicrisis", "imagen", "estudio", "consulta", "diagnostico"}
    for e in reversed(historia):
        tipo = str(e.get("tipo") or "").lower()
        texto = " ".join(str(e.get("texto") or "").split())
        if tipo not in tipos_utiles or not texto:
            continue
        fecha = str(e.get("fecha") or "")
        servicio = str(e.get("servicio") or tipo.capitalize())
        if e.get("tercero"):
            # Lo trajo el paciente, no un sistema de salud: el médico tiene
            # que leerlo como dato aportado, nunca como registro verificado.
            texto = f"{_TERCERO_NOTA}. {texto}"
        antecedentes.append({
            "titulo": f"{servicio} · {fecha[:4]}" if fecha else servicio,
            "texto": texto[:400],
        })
        if len(antecedentes) >= _LIMITES["antecedentes"]:
            break

    return {
        "titular": titular,
        "pendientes": pendientes,
        "preguntas": preguntas[: _LIMITES["preguntas"]],
        "medicacion": meds,
        "antecedentes": antecedentes,
        "tendencias": [{"id": s["id"], "comentario": ""} for s in series[:3]],
        "advertencias": [],
        "_motivo": motivo,
    }


def _datos_prompt(turnos, entrevistas, historia, series):
    """El blob DATOS del prompt. La historia va con texto OCR incluido (es la
    gracia), pero recortado por entrada para que el prompt no explote."""
    hist = []
    for e in historia:
        if e.get("sistema") == "hsi_external":
            continue
        hist.append({k: e.get(k) for k in
                     ("fecha", "tipo", "servicio", "texto", "valores",
                      "medicamentos", "sistema", "calidad_ocr") if e.get(k)})
        if hist[-1].get("texto"):
            hist[-1]["texto"] = str(hist[-1]["texto"])[:600]
        if e.get("tercero"):
            # El modelo tiene que saber que esta entrada la trajo el
            # paciente: se cita distinto que un registro del hospital.
            hist[-1]["origen"] = _TERCERO_NOTA
    return json.dumps({
        # Allowlist the local source contracts. A new UI/source field must not
        # silently become external-AI input (in particular external history).
        "turnos_en_este_sistema": [{k: row.get(k) for k in
            ("fecha", "servicio", "estado", "asistencia", "medico", "orden")
            if k in row} for row in turnos],
        "entrevistas_previas": [{k: row.get(k) for k in
            ("fecha", "triage", "resumen", "brief", "banderas_rojas", "estado", "medico_id")
            if k in row} for row in entrevistas[:5]],
        "historia_digitalizada": hist,
        "series_disponibles": [
            {"id": s["id"], "label": s["label"], "unidad": s["unidad"],
             "puntos": s["puntos"]} for s in series],
    }, ensure_ascii=False, default=str)


def _indicadores(series, tendencias):
    """Choose up to four code-derived indicator cards for the quick scan.

    The model may select only three trend series, and the paired blood
    pressure series consume two of those slots while rendering as one card.
    Start with those clinically selected series, then fill the grid with the
    newest out-of-range measurements. Every point and range remains the
    closed, code-extracted series contract; AI cannot add a number here.
    """
    por_id = {s["id"]: s for s in series}
    comentarios = {t.get("id"): t.get("comentario") or ""
                   for t in tendencias if t.get("id")}
    elegidas = []
    ids = set()
    tarjetas = 0

    def agregar(s):
        nonlocal tarjetas
        if not s or s["id"] in ids or not s.get("puntos"):
            return
        ids.add(s["id"])
        elegidas.append(dict(s, comentario=comentarios.get(s["id"], "")))
        par_ta_disponible = {"ta_sistolica", "ta_diastolica"}.issubset(por_id)
        if s["id"] not in ("ta_sistolica", "ta_diastolica") or not par_ta_disponible:
            tarjetas += 1
        elif not ({"ta_sistolica", "ta_diastolica"} - ids):
            tarjetas += 1

    for t in tendencias:
        agregar(por_id.get(t.get("id")))

    def fuera(s):
        rango = s.get("rango_ok")
        valor = s["puntos"][-1][1] if s.get("puntos") else None
        return bool(rango and valor is not None and
                    (valor < rango[0] or valor > rango[1]))

    def fecha_orden(s):
        raw = re.sub(r"\D", "", str(s["puntos"][-1][0])) if s.get("puntos") else ""
        return -int(raw or 0)

    restantes = sorted((s for s in series if s["id"] not in ids),
                       key=lambda s: (not fuera(s), fecha_orden(s)))
    for s in restantes:
        if tarjetas >= _LIMITES["indicadores"]:
            break
        # A blood-pressure card needs both real series or it is clearer as a
        # standalone metric. Complete the pair when it exists.
        agregar(s)
        if s["id"] == "ta_sistolica":
            agregar(por_id.get("ta_diastolica"))
        elif s["id"] == "ta_diastolica":
            agregar(por_id.get("ta_sistolica"))
    return elegidas


def generate(dni, medico_id, force=False):
    """Genera (o reusa) el resumen de (dni, médico). Devuelve la fila
    persistida como dict {resumen, generado, modelo, especialidad} o None si
    ni siquiera el armado determinístico fue posible (DB caída)."""
    dni = str(dni or "").strip()
    medico_id = str(medico_id or "").strip()
    if not dni or not medico_id:
        return None

    from app.patient_web import catalog
    doctor = catalog.doctor_doc(medico_id, tolerant=True) or {}
    especialidad = doctor.get("especialidad") or ""

    turnos = _turnos(dni)
    entrevistas = _entrevistas(dni)
    historia, estados = _historia_clinica(dni)
    series = _series(historia)

    fp = _fingerprint(turnos, entrevistas, historia, especialidad)
    if not force:
        try:
            fila = db_clinical.summary_get(dni, medico_id)
        except Exception:  # noqa: BLE001
            fila = None
        if (fila and fila.get("fingerprint") == fp and
                isinstance(fila.get("resumen"), dict) and
                "indicadores" in fila["resumen"]):
            return _wire_out(fila, estados)

    fake = config.environ_text("TURNOS_FAKE_AI") == "1"
    tiene_clave = ai_client.available()
    # The shared breaker: skip the call outright when AI is already known
    # down (402 quota / 5xx / timeouts reported by any surface) instead of
    # paying the timeout again here — this is a pre-visit convenience, never
    # something a booking or the queue waits on.
    ai_ok = ai_health.available()
    modelo = _model()

    resumen = None
    if not fake and tiene_clave and ai_ok:
        try:
            prompt = prompts.text(
                "resumen_paciente", especialidad=especialidad or "Medicina general",
                hoy=datetime.now(_ART).strftime("%d/%m/%Y"))
            # La historia clínica es lo más sensible que manda este sistema y
            # sale a un proveedor externo. El contenido clínico tiene que
            # salir — es lo que se le pide resumir —, la identidad no: el
            # nombre, el DNI, el teléfono y el domicilio se cambian por
            # etiquetas y vuelven al parsear la respuesta (pii.py).
            anon = pii.for_dni(dni)
            datos = anon.scrub(
                _datos_prompt(turnos, entrevistas, historia, series))
            body = {
                "model": modelo,
                "messages": [{"role": "user", "content":
                              prompt + "\n\nDATOS:\n" + datos}],
                "temperature": 0.15,
                "max_tokens": 2000,
                "response_format": {"type": "json_object"},
                "usage": {"include": True},
            }
            data = ai_client.call(body, timeout=60, headers_extra={
                "X-Title": theme.brand_name() + " - resumen del paciente"})
            ai_health.report(None)
            content = anon.restore(
                data["choices"][0]["message"].get("content") or "")
            parsed = _extract_json(content)
            if parsed:
                resumen = _coerce(parsed, {s["id"] for s in series})
                try:
                    from app.ai import ai_costs
                    c = (data.get("usage") or {}).get("cost")
                    ai_costs.record("resumen_paciente", cost_usd=float(c or 0.0),
                                    model=modelo, recognized=True)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001 — red/HTTP/parse: degradar
            ai_health.report(e)
            _log(f"modelo falló para {log_pii.dni(dni)}/{medico_id}: {log_pii.exc(e)}")

    if resumen is None:
        if fake:
            motivo = "fake"
        elif not tiene_clave:
            motivo = "sin_clave_api"
        elif not ai_ok:
            motivo = "ai_no_disponible"
        else:
            motivo = "fallback"
        resumen = _deterministic(turnos, entrevistas, historia, series, motivo)
        modelo = motivo

    # Las series reales viajan JUNTO al resumen (el frontend no re-consulta
    # los conectores): cada tendencia elegida se resuelve a su serie completa.
    por_id = {s["id"]: s for s in series}
    elegidas = [t for t in resumen.get("tendencias") or [] if t.get("id") in por_id]
    # La TA es UN gráfico de dos líneas: si el modelo eligió una sola mitad
    # del par sistólica/diastólica y la otra existe, se completa sola.
    ids = {t["id"] for t in elegidas}
    for una, otra in (("ta_sistolica", "ta_diastolica"),
                      ("ta_diastolica", "ta_sistolica")):
        if una in ids and otra not in ids and otra in por_id:
            elegidas.append({"id": otra, "comentario": ""})
    resumen["tendencias"] = [
        dict(por_id[t["id"]], comentario=t.get("comentario") or "")
        for t in elegidas]
    resumen["indicadores"] = _indicadores(series, resumen["tendencias"])
    resumen["fuentes"] = _fuentes_linea(turnos, entrevistas, historia, estados)

    try:
        db_clinical.summary_upsert(dni, medico_id, especialidad, resumen, fp, modelo)
        fila = db_clinical.summary_get(dni, medico_id)
        if fila:
            return _wire_out(fila, estados)
    except Exception as e:  # noqa: BLE001
        _log(f"no se pudo persistir el resumen {log_pii.dni(dni)}/{medico_id}: {log_pii.exc(e)}")
    return {"resumen": resumen, "generado": None, "modelo": modelo,
            "especialidad": especialidad, "fuentes_estado": estados}


def _fuentes_linea(turnos, entrevistas, historia, estados):
    """La línea de procedencia del pie del resumen — cada afirmación tiene
    que poder rastrearse a una fuente (formato del dashboard de referencia)."""
    partes = []
    labels = {}  # an entry's own `sistema_label` beats the curated/generic one
    sistemas = []
    for e in historia:
        sistema = str(e.get("sistema") or "").strip()
        if sistema and sistema not in sistemas:
            sistemas.append(sistema)
        if sistema and e.get("sistema_label"):
            labels[sistema] = str(e["sistema_label"])
    for sistema in sistemas:
        n = sum(1 for e in historia if e.get("sistema") == sistema)
        if n:
            fechas = sorted(str(e.get("fecha"))[:4] for e in historia
                            if e.get("sistema") == sistema and e.get("fecha"))
            partes.append(f"{labels.get(sistema) or _history_label(sistema)}: {n} registros"
                          + (f" {fechas[0]}–{fechas[-1]}" if fechas else ""))
    # `estados` is keyed in the fan-out order `_historia_clinica` consulted
    # the archives in (local_history first, then sorted) — the same order
    # for every discovered id, not a hard-coded tuple, so a newly registered
    # archive gets its "no disponible" line too.
    for sistema, estado in (estados or {}).items():
        if estado == "no_disponible" and sistema not in sistemas:
            partes.append(f"{labels.get(sistema) or _history_label(sistema)}: no disponible")
    if turnos:
        partes.append(f"Este sistema: {len(turnos)} solicitudes")
    if entrevistas:
        partes.append(f"Entrevista previa: {len(entrevistas)}")
    return partes


def _wire_out(fila, estados=None):
    generado = fila.get("generated_at")
    return {
        "resumen": fila.get("resumen"),
        "generado": _tz.iso(generado),
        "modelo": fila.get("ai_model"),
        "especialidad": fila.get("especialidad"),
        "fuentes_estado": estados or {},
        "external_history_enabled": config.external_history_enabled(),
    }


# ---- HTTP --------------------------------------------------------------------

class PatientSummaryError(StatusError):
    """A typed 4xx the domain layer raises instead of writing a response
    itself — the native router catches this and writes the status/body, so
    the HTTP layer stays the only place a response is built."""


def resolve_medico_id(identity, medico_param):
    """Pure: which médico_id a `/api/admin/patient-summary` request resolves
    to. A 'medico' role ALWAYS gets their own médico_id (the query/body
    param is ignored — same criterion as doctor_patients.py); admin/operador
    must supply one explicitly. Raises `PatientSummaryError` for the
    400 cases the endpoint reports. Since K10 (2026-10-23) every role but the
    administrator is held to its own linked doctor, like a 'medico'; holding
    `todos_los_pacientes` lifts that hold, and without a ?medico= such an
    account still reads as its own doctor (the doctor's screens never send
    one)."""
    from app.core import access_log
    if access_log.scoped(identity):
        medico_id = access_log.doctor_id(identity)
        if not medico_id:
            raise PatientSummaryError(400, {"error": "cuenta_sin_medico"})
        return medico_id
    medico_id = medico_param
    if not medico_id and identity.get("rol") != "admin":
        medico_id = access_log.doctor_id(identity)
    if not medico_id:
        raise PatientSummaryError(400, {"error": "falta medico"})
    return medico_id


# ---- pre-generación ----------------------------------------------------------

def tick(now_utc=None, stop=None):
    """Una pasada del batch: resumen fresco para cada (dni, médico) con turno
    aprobado dentro de la ventana. Devuelve cuántos generó/actualizó.

    `stop` (`core.worker_stop.check()`, solo desde el worker de la cola): se
    consulta antes de cada resumen; pedida la parada, la pasada termina y los
    pares que faltan quedan sin resumen fresco para la próxima (su contrato de
    re-ejecución), así un SIGTERM no espera a toda la ventana."""
    from app.core import worker_stop
    horizonte = int(config.environ_text("TURNOS_SUMMARY_HORIZON_H") or 36)
    try:
        pares = db_clinical.upcoming_for_summaries(horizonte)
    except Exception:  # noqa: BLE001 — DB caída: la próxima pasada reintenta
        return 0
    hechos = 0
    for dni, medico_id in pares:
        if worker_stop.halted(stop):
            _log(f"parada pedida: {hechos} resumen(es) hechos, el resto en la próxima pasada")
            break
        try:
            antes = db_clinical.summary_get(dni, medico_id)
            out = generate(dni, medico_id)
            if out and (not antes or out.get("generado") !=
                        (antes.get("generated_at") and
                         antes["generated_at"].astimezone(_ART).isoformat())):
                hechos += 1
        except Exception as e:  # noqa: BLE001
            _log(f"pre-generación falló para {log_pii.dni(dni)}/{medico_id}: {log_pii.exc(e)}")
    return hechos


def _loop(interval_s):
    import time
    while True:
        try:
            n = tick()
            if n:
                _log(f"pre-generados {n} resúmenes")
        except Exception as e:  # noqa: BLE001
            _log(f"tick falló: {log_pii.exc(e)}")
        time.sleep(interval_s)


def _pregen_enabled():
    """THE one truth for 'does this process/worker own the pre-generation
    sweep' — start() and the worker registry (worker._chart_summary_enabled)
    both read it here, never a second parse of the flag. Plain-environ
    resolution (os.environ), exactly as this module always resolved it:
    it never went through core.config."""
    flag = config.environ_text("TURNOS_SUMMARY_PREGEN")
    if flag in (None, ""):
        # The demo re-seeds its fictional agenda every night (new dates, so new
        # fingerprints): a sweep there is ~130 paid calls a day for invented
        # patients. Its summaries are made on open; PREGEN=1 brings the sweep back.
        from app.ops import demo_installation
        return not demo_installation.enabled()
    return flag != "0"


def _tick_min():
    """THE one resolution of TURNOS_SUMMARY_TICK_MIN — the thread loop and
    the queue registration (worker.py) both derive their cadence from
    here, never a second parse (same contract as
    housekeeping._interval_h()). A malformed value falls back to the
    default like every sibling interval parser (waitlist._interval_min,
    reminders._interval_min) — without this, `worker.py --jobs`, the one
    tool an operator runs to DEBUG a bad env var, crashed on it
    (batch-3 adversarial review, finding 1)."""
    try:
        return int(config.environ_text("TURNOS_SUMMARY_TICK_MIN") or 60)
    except ValueError:
        return 60


def start():
    """Starts the pre-generation daemon (daemon thread, same pattern as
    hsi_resync.start). TURNOS_SUMMARY_PREGEN=0 turns it off.

    Tier 4 worker-topology (periodic-daemon batch 3): with
    TURNOS_QUEUE_WORKER=1 the SEPARATE worker
    process owns the sweep via ai/chart_summary_queue.py and no thread
    starts here — exactly one mechanism per topology
    (TestDelegationMatrix). The OFF flag wins over delegation, same as
    the batch-2 twins. On-demand generation in the request path
    (generate()) is unchanged in every topology."""
    if not _pregen_enabled():
        _log("pre-generación desactivada (TURNOS_SUMMARY_PREGEN=0, o la demo sin PREGEN=1)")
        return None
    from app.core import queue_infra
    modo_worker = queue_infra.worker_mode()
    if modo_worker:
        _log(f"pre-generación delegada al worker "
             f"(TURNOS_QUEUE_WORKER={modo_worker}) — este proceso no barre")
        return None
    t = threading.Thread(target=_loop, args=(_tick_min() * 60,),
                         name="chart-summary-pregen", daemon=True)
    t.start()
    return t
