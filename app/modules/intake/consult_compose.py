"""consult_compose.py — arma receta_json/orden_json para
consult_admin.py's POST /confirm (fase 5).

Dos pasos, siempre en este orden:
  1. Composición DETERMINÍSTICA (compose_deterministic): junta lo que el
     médico eligió (diagnósticos/estudios/medicación/notas, ya coercionados
     por consult_admin) en la forma exacta que imprime printable.py — nunca
     falla, no pega a la red.
  2. Un pase OPCIONAL de redacción con IA sobre CADA documento por
     separado (una sola lectura por documento, no iterativo como el
     análisis de la orden de 3 pasadas): puede reformular "lineas"/
     "estudios"/"diagnosticos" (p.ej. nombre de droga por principio activo)
     y ajustar `espacio_superior_cm`, pero nunca puede inventar un dato
     nuevo — el resultado se recorta de vuelta al esquema del original
     (_coerce_back). Se salta entero con TURNOS_FAKE_AI=1 o sin API key.
     Además (fase 8) se salta si nadie editó las instrucciones del panel:
     el gate real es `prompts.is_edited("entrevista_receta"/"entrevista_
     orden")` — instalación de fábrica = compose 100% determinístico, sin
     costo de IA en cada confirmación; el día que el hospital escribe una
     regla propia ("usar la sustancia activa", "dejar 3 cm arriba"), el
     pase se prende solo, sin deploy. Cualquier falla (red, JSON roto, lo
     que sea) deja parada la versión determinística — compose() jamás
     lanza.

printable.py nunca importa este módulo: lee receta_json/orden_json YA
guardados, sin volver a tocar la IA ni el catálogo al imprimir.
"""

import sys
import json
from datetime import datetime

from app.core import ai_policy, config, pii
from app.core import theme
from app.ai import ai_client
from app.core import tz as _tz
from app.core import site_identity
from app.core import log_pii
from app.ai.json_extract import extract_object as _extract_json

_ART = _tz.tzinfo()

_MAX_NOTAS = 4000


def _log(msg):
    print(f"[intake.consult_compose] {msg}", file=sys.stderr, flush=True)


def _fecha_hoy():
    return datetime.now(_ART).strftime("%d/%m/%Y")


def _pie():
    hospital = site_identity.get("hospital")
    return f"Documento generado por {hospital} — validalo con tu médico."


def _linea_medicacion(m):
    partes = [m.get("nombre"), m.get("dosis"), m.get("frecuencia"), m.get("duracion")]
    return " — ".join(str(p) for p in partes if p)


def _linea_diagnostico(d):
    label = str(d.get("label") or "").strip()
    codigo = d.get("codigo")
    sistema = d.get("sistema") or "CIE-10"
    if codigo:
        return f"{label} ({sistema} {codigo})"
    return label


def review_choices(diagnosticos, estudios, medicacion, notas):
    """The exact human-readable choices that deterministic drafts preserve."""
    return {
        "diagnosticos": [_linea_diagnostico(d) for d in diagnosticos],
        "estudios": [e.get("label") or "" for e in estudios],
        "medicacion": [_linea_medicacion(m) for m in medicacion],
        "notas": notas,
    }


def _header(doctor, paciente):
    doctor = doctor or {}
    paciente = paciente or {}
    return {
        "medico": {
            "nombre": doctor.get("nombre") or "",
            "matricula": doctor.get("matricula") or "",
            "especialidad": doctor.get("especialidad") or "",
        },
        "paciente": {
            "nombre": paciente.get("nombre") or "",
            "dni": paciente.get("dni") or "",
        },
        "fecha": _fecha_hoy(),
    }


def _compose_deterministic(doctor, paciente, diagnosticos, estudios, medicacion, notas):
    notas = (notas or "").strip()[:_MAX_NOTAS]
    hospital = site_identity.get("hospital")

    receta = _header(doctor, paciente)
    receta.update({
        "espacio_superior_cm": 0,
        "titulo": hospital,
        "lineas": [_linea_medicacion(m) for m in (medicacion or []) if _linea_medicacion(m)],
        "notas": notas,
        "pie": _pie(),
    })

    orden = _header(doctor, paciente)
    orden.update({
        "espacio_superior_cm": 0,
        "titulo": hospital,
        "estudios": [str(e.get("label") or "").strip() for e in (estudios or []) if e.get("label")],
        "diagnosticos": [_linea_diagnostico(d) for d in (diagnosticos or []) if _linea_diagnostico(d)],
        "notas": notas,
        "pie": _pie(),
    })
    return receta, orden


# ---- pase opcional de redacción con IA -------------------------------------

def _clave_modifier(kind):
    return "entrevista_receta" if kind == "receta" else "entrevista_orden"


def _modifier(kind):
    """Instrucción editable desde el panel para el pase de redacción sobre
    `kind` ('receta'|'orden'), conectada a los prompts editables de
    prompts.py (entrevista_receta / entrevista_orden). Best-effort: si
    prompts.py no se puede importar o falla por lo que sea, el pase sigue
    sólo con la instrucción base de _REWRITE_PROMPT."""
    try:
        from app.ai import prompts
        return prompts.text(_clave_modifier(kind))
    except Exception:  # noqa: BLE001
        return ""


def _modifier_edited(kind):
    """Si el hospital escribió su propia instrucción para `kind` en el
    panel. Es el gate del pase de IA (ver `compose`): sin esto, instalar de
    fábrica correría un pase de IA en CADA confirmación de consulta sólo
    para reproducir lo que la composición determinística ya hace sola."""
    try:
        from app.ai import prompts
        return prompts.is_edited(_clave_modifier(kind))
    except Exception:  # noqa: BLE001
        return False


_REWRITE_PROMPT = """You are formatting a printable Argentine medical document (a {kind}) for \
a hospital. Below is the ALREADY-COMPOSED JSON content. Return the SAME JSON shape, with only \
wording/formatting adjustments allowed: you may rephrase entries in the list fields (e.g. use \
the active-substance name of a drug instead of a brand name), reorder short lists, and adjust \
"espacio_superior_cm" (a number from 0 to 10, extra top padding in cm the printed slip should \
reserve — leave it 0 unless there's a good reason). Never invent a medication, exam, diagnosis \
or fact that isn't already there; never change "medico", "paciente", "fecha", "titulo" or \
"pie". Every string must stay in Spanish.
{modifier}
JSON:
{payload}

Return ONLY the JSON object, no extra text."""


def _coerce_back(original, rewritten, list_keys):
    """Recorta la respuesta del modelo de vuelta al esquema de `original`:
    claves desconocidas afuera, listas recortadas a como mucho el largo
    original (nunca agrega una línea de la nada), número de
    espacio_superior_cm clampeado 0-10. Nunca confía en el modelo para un
    dato NUEVO, sólo para la redacción de lo que ya estaba."""
    if not isinstance(rewritten, dict):
        return original
    out = dict(original)
    for key in list_keys:
        orig_list = original.get(key) or []
        if not orig_list:
            continue  # nada que reformular si la lista original está vacía
        new_list = rewritten.get(key)
        if isinstance(new_list, list):
            cleaned = [str(x).strip() for x in new_list if str(x or "").strip()]
            if cleaned:
                out[key] = cleaned[:len(orig_list)]
    notas = rewritten.get("notas")
    if isinstance(notas, str) and original.get("notas"):
        out["notas"] = notas.strip()[:_MAX_NOTAS]
    esp = rewritten.get("espacio_superior_cm")
    try:
        if esp is not None:
            out["espacio_superior_cm"] = max(0.0, min(10.0, float(esp)))
    except (TypeError, ValueError):
        pass
    return out


def _ai_rewrite(kind, payload, list_keys):
    """Pase opcional sobre UN documento ya compuesto. Se salta entero con
    TURNOS_FAKE_AI=1 o sin clave configurada; ante CUALQUIER falla (red,
    timeout, JSON ilegible) devuelve `payload` sin tocar — nunca lanza,
    nunca bloquea confirm()."""
    fake = str(config.get("TURNOS_FAKE_AI", "") or "") == "1"
    tiene_api_key = ai_client.available()
    if fake or not tiene_api_key:
        return payload
    try:
        model = ai_policy.model()
        anon = pii.for_dni((payload.get("paciente") or {}).get("dni"))
        anon.learn((payload.get("paciente") or {}).get("nombre"), "nombre")
        anon.learn((payload.get("medico") or {}).get("nombre"), "nombre")
        anon.learn((payload.get("medico") or {}).get("matricula"), "matricula")
        prompt = _REWRITE_PROMPT.format(
            kind=kind, modifier=_modifier(kind),
            payload=json.dumps(payload, ensure_ascii=False))
        body = {
            "model": model,
            "messages": [{"role": "user", "content": anon.scrub(prompt)}],
            "temperature": 0.2,
            "max_tokens": 900,
            "response_format": {"type": "json_object"},
            "usage": {"include": True},
        }
        data = ai_client.call(
            body,
            timeout=30,
            headers_extra={"X-Title": f"{theme.brand_name()} - redacción de {kind}"},
        )
        content = data["choices"][0]["message"].get("content") or ""
        cost = 0.0
        try:
            c = (data.get("usage") or {}).get("cost")
            if c is not None:
                cost = float(c)
        except (AttributeError, TypeError, ValueError):
            cost = 0.0
        try:
            from app.ai import ai_costs
            ai_costs.record("redactar_consulta", cost_usd=cost or 0.0, model=model, recognized=True)
        except Exception:  # noqa: BLE001 — analítica, nunca debe romper la redacción
            pass
        rewritten = _extract_json(anon.restore(content))
        return _coerce_back(payload, rewritten, list_keys)
    except Exception as e:  # noqa: BLE001 — la redacción NUNCA debe romper confirm()
        _log(f"pase de redacción falló ({kind}): {log_pii.exc(e)}")
        return payload


def compose(doctor, paciente, diagnosticos, estudios, medicacion, notas, *, allow_ai=True):
    """Arma (receta_json, orden_json): composición determinística primero,
    después un pase de redacción opcional POR DOCUMENTO (nunca lanza).

    El pase de IA sólo corre cuando HAY una instrucción de panel editada
    para ese documento (`_modifier_edited`) — instalación de fábrica queda
    100% determinística, cero IA en cada confirmación; el hospital
    editando "entrevista_receta"/"entrevista_orden" la prende sin deploy.
    """
    receta, orden = _compose_deterministic(doctor, paciente, diagnosticos, estudios, medicacion, notas)
    if allow_ai and _modifier_edited("receta"):
        receta = _ai_rewrite("receta", receta, ["lineas"])
    if allow_ai and _modifier_edited("orden"):
        orden = _ai_rewrite("orden", orden, ["estudios", "diagnosticos"])
    return receta, orden
