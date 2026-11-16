"""lab_analysis.py — el motor de «Resultados de laboratorio» del paciente
(docs/architecture/laboratorio-paciente.md, 2026-08-06).

`lab_results.py` le da al paciente su propia lectura de la historia clínica
digitalizada; acá vive el pase de IA que hace esa lectura posible, con el
mismo espíritu de costo que `chart_summary.py`: UNA llamada por lote, cacheada
para siempre hasta que algo realmente cambie.

Tres pasadas en una sola llamada, no cuatro por separado (por informe):
clasificación del tipo de estudio, resumen cauteloso en una línea, veredicto
de sensibilidad, y extracción de determinaciones del texto OCR — todo en el
mismo JSON de respuesta, para pagar una sola vez por informe.

Cache: `lab_report_analysis` (dni, report_hash) guarda la lectura. Un informe
igual (mismo `report_hash`, calculado sobre fecha/sistema/servicio/texto/
valores) con la lectura hecha bajo el MISMO `prompt_hash` (los dos prompts
editables, `laboratorio_paciente` + `laboratorio_sensible`, resueltos y
concatenados) es una fila fresca: no vuelve a pagar IA. Editar cualquiera de
los dos prompts invalida las filas viejas, pero la re-lectura es LAZY y
acotada (`_BATCH` informes por llamada, `_MAX_BATCHES` llamadas por pase)
para que un edit nunca dispare un backfill sin límite — el costo extra queda
acotado a unas pocas llamadas por apertura de la pestaña, nunca a golpear la
IA por cada informe viejo de una sola vez.

El GET del paciente NUNCA espera al modelo: si hace falta IA, la respuesta
sale YA con el fallback determinístico (o la fila stale) y el pase corre en
un hilo de fondo; el front reintenta una vez cuando `analisis_pendiente > 0`.
`analizar(..., sync=True)` conserva el camino bloqueante para tests.

Piso de sensibilidad NO editable (mismo patrón que la bandera roja de
`modules/intake`): un match por palabra clave sobre el texto del informe y
las etiquetas extraídas fuerza `sensible=True` aunque el modelo diga lo
contrario — la IA puede fallar en marcar algo sensible, pero una vez que el
código lo detecta por palabra, no hay forma de que se filtre.

Fallback determinístico (sin IA — `TURNOS_FAKE_AI=1`, sin clave, o el modelo
falla/no parsea): el tipo sale del campo `servicio` (o de la composición de
determinaciones catalogadas si el servicio no dice nada útil), el resumen
sale de contar cuántos valores catalogados están fuera de rango, y NUNCA se
muestra nada extraído del texto OCR crudo sin que la IA lo haya visto — sólo
los valores estructurados que ya trae el conector (`_SERIES_DEF`). Estas
filas quedan marcadas con `modelo` = el motivo ('fake'/'sin_clave_api'/
'fallback') y se reintentan solas, dentro de los mismos topes de lote, la
próxima vez que haya una clave real.
"""
from __future__ import annotations

import hashlib
import json
import sys
import threading

from app.ai import ai_client
from app.ai import chart_summary
from app.core import config
from app.core import db_clinical
from app.core import i18n
from app.core import log_pii
from app.core import pii
from app.core import text_fold
from app.core import theme
from app.ai import prompts

_BATCH = 10
_MAX_BATCHES = 2

# Un solo pase de IA en vuelo por DNI — dos aperturas seguidas de la pestaña
# no disparan dos backfills concurrentes del mismo paciente.
_INFLIGHT = set()
_INFLIGHT_LOCK = threading.Lock()

# Los tres motivos posibles de `chart_summary._deterministic` reaparecen acá
# tal cual — una fila con cualquiera de estos `modelo` nunca cuenta como
# "fresca": siempre es candidata a una relectura real la próxima vez que haya
# clave, tope de lote mediante.
_FALLBACK_MARKERS = ("fake", "sin_clave_api", "fallback")

# Piso de sensibilidad por palabra clave — no editable desde el panel (ver
# docstring del módulo). Comparación sin mayúsculas ni acentos.
_PALABRAS_SENSIBLES = ("vih", "hiv", "biopsia", "anatomia patologica",
                       "marcador tumoral", "toxicolog")

_MAX_TEXTO = 3000   # tope de texto OCR por informe que viaja al prompt


def _log(msg):
    print(f"[lab_analysis] {msg}", file=sys.stderr, flush=True)


def _model():
    from app.core import ai_policy
    return ai_policy.model()


# ---- hashing -------------------------------------------------------------

def _canonical(entrada):
    return {
        "fecha": entrada.get("fecha"),
        "sistema": entrada.get("sistema"),
        "servicio": entrada.get("servicio"),
        "texto": entrada.get("texto"),
        "valores": entrada.get("valores"),
    }


def report_hash(entrada):
    """sha256 del contenido canónico del informe — un informe corregido
    (cualquier cambio de contenido) es OTRO informe, con su propia fila."""
    blob = json.dumps(_canonical(entrada), ensure_ascii=False,
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _catalogo_texto():
    partes = []
    for clave, meta in chart_summary._SERIES_DEF.items():
        rango = (f"{meta['rango_ok'][0]}-{meta['rango_ok'][1]}"
                 if meta.get("rango_ok") else "sin rango de referencia fijo")
        partes.append(f"{clave} = {meta['label']} ({meta['unidad']}, {rango})")
    return "; ".join(partes)


def prompt_hash():
    """sha256 del texto resuelto de LOS DOS prompts editables — cambiar
    cualquiera de los dos invalida el cache (docstring del módulo)."""
    texto = (prompts.text("laboratorio_paciente", catalogo=_catalogo_texto())
            + "\n" + prompts.text("laboratorio_sensible"))
    return hashlib.sha256(texto.encode()).hexdigest()


# ---- piso de sensibilidad por palabra clave -------------------------------

def _normalizar(s):
    return text_fold.ascii_fold(str(s or "")).lower()


def _forzar_sensible(entrada, resultado):
    etiquetas = " ".join(str(v.get("nombre") or "")
                         for v in (resultado.get("valores") or []))
    campo = _normalizar(" ".join([
        str(entrada.get("texto") or ""),
        str(resultado.get("tipo_estudio") or ""),
        etiquetas,
    ]))
    return any(p in campo for p in _PALABRAS_SENSIBLES)


# ---- valores estructurados (SIEMPRE del conector, nunca del OCR crudo) ----

def _valor_item(clave, valor):
    """Un valor catalogado a la forma de cable (clave/nombre/valor/unidad/
    rango/fuera) — la misma forma que persiste `valores` en la fila."""
    meta = chart_summary._SERIES_DEF.get(clave)
    if not meta:
        return None
    rango = meta.get("rango_ok")
    fuera = bool(rango is not None and (valor < rango[0] or valor > rango[1]))
    return {"clave": clave, "nombre": meta["label"], "valor": valor,
            "unidad": meta["unidad"], "rango": rango, "fuera": fuera}


def _valores_estructurados(entrada):
    """Los `valores` que YA trae el conector, filtrados al catálogo cerrado
    — la única fuente que el fallback determinístico puede mostrar (nunca
    algo que dependa de que la IA haya leído el texto OCR)."""
    out = []
    valores = entrada.get("valores")
    if not isinstance(valores, dict):
        return out
    for clave, crudo in valores.items():
        try:
            numero = float(crudo)
        except (TypeError, ValueError):
            continue
        item = _valor_item(clave, numero)
        if item:
            out.append(item)
    return out


# ---- fallback determinístico ----------------------------------------------

def _t(key, default, lang, **variables):
    """Una frase del fallback. `lang=None` es el texto que se GUARDA (castellano
    fijo, sin pasar por el catálogo); con idioma es el que se MUESTRA, con el
    castellano como respaldo. Cada frase es una clave entera — nunca una
    palabra pegada a otra (docs/architecture/i18n.md)."""
    if lang is None:
        texto = default
        for k, v in variables.items():
            texto = texto.replace("{" + k + "}", str(v))
        return texto
    return i18n.t_or(key, default, lang, **variables)


def etiqueta_de(clave, lang=None):
    """El nombre de una determinación del catálogo cerrado en `lang`."""
    meta = chart_summary._SERIES_DEF.get(clave)
    if not meta:
        return clave
    return _t(f"account.mine.lab.analyte.{clave}", meta["label"], lang)


def _tipo_heuristico(entrada, claves_estructuradas, lang=None):
    servicio = str(entrada.get("servicio") or "").strip()
    if servicio and servicio.lower() not in ("laboratorio", "laboratorio central"):
        return servicio
    # El orden sale de las etiquetas en castellano: el mismo informe lista lo
    # mismo en cualquier idioma, sólo cambia cada palabra.
    ordenadas = sorted({chart_summary._SERIES_DEF[c]["label"]: c
                        for c in claves_estructuradas
                        if c in chart_summary._SERIES_DEF}.items())
    if ordenadas:
        return ", ".join(etiqueta_de(c, lang) for _l, c in ordenadas[:3])
    return servicio or _t("account.mine.lab.fallback_study",
                          "Análisis de laboratorio", lang)


def fallback_textos(entrada, lang=None):
    """(tipo_estudio, resumen, tono) del fallback determinístico, en `lang`
    (`None` = el texto que se guarda). Sólo depende del conteo de valores
    catalogados fuera de rango, por eso se puede volver a escribir al mostrar
    en cualquier idioma sin traducir nada."""
    valores = _valores_estructurados(entrada)
    fuera_n = sum(1 for v in valores if v["fuera"])
    if not valores:
        resumen = _t("account.mine.lab.fallback_unreadable",
                     "Todavía no pudimos leer los valores de este informe.", lang)
        tono = "revisar"
    elif fuera_n:
        uno = "Hay {n} valor para revisar con tu médico en la próxima consulta."
        varios = "Hay {n} valores para revisar con tu médico en la próxima consulta."
        if lang is None:
            resumen = _t("", uno if fuera_n == 1 else varios, None, n=fuera_n)
        else:
            resumen = i18n.tn_or("account.mine.lab.fallback_review", fuera_n,
                                 uno, varios, lang)
        tono = "revisar"
    else:
        resumen = _t("account.mine.lab.fallback_all_ok",
                     "Todos los valores dentro del rango de referencia.", lang)
        tono = "ok"
    tipo = _tipo_heuristico(entrada, {v["clave"] for v in valores}, lang)
    return tipo, resumen, tono


def es_fallback(fila):
    return (fila or {}).get("modelo") in _FALLBACK_MARKERS


def _fallback_row(entrada, motivo):
    tipo, resumen, tono = fallback_textos(entrada)
    return {"tipo_estudio": tipo, "resumen": resumen, "tono": tono,
            "sensible": False, "valores": _valores_estructurados(entrada),
            "modelo": motivo}


# ---- coerción de la respuesta del modelo ----------------------------------

def _coerce_valor(v):
    if not isinstance(v, dict):
        return None
    try:
        valor = float(v.get("valor"))
    except (TypeError, ValueError):
        return None
    clave = v.get("clave")
    clave = clave if clave in chart_summary._SERIES_DEF else None
    nombre = (chart_summary._coerce_str(v.get("nombre"), 80)
             or (chart_summary._SERIES_DEF[clave]["label"] if clave else "")
             or "Determinación")
    unidad = (chart_summary._coerce_str(v.get("unidad"), 20)
             or (chart_summary._SERIES_DEF[clave]["unidad"] if clave else ""))
    rango = v.get("rango")
    if isinstance(rango, (list, tuple)) and len(rango) == 2:
        try:
            rango = [float(rango[0]), float(rango[1])]
        except (TypeError, ValueError):
            rango = None
    else:
        rango = None
    if rango is None and clave:
        rango = chart_summary._SERIES_DEF[clave].get("rango_ok")
    # Con rango numérico, `fuera` se CALCULA — el bool del modelo sólo vale
    # cuando no hay rango contra el cual verificar (un "en rango" alucinado
    # sobre un valor fuera de banda no puede llegar al paciente).
    if rango:
        fuera = bool(valor < rango[0] or valor > rango[1])
    else:
        fuera = bool(v.get("fuera")) if isinstance(v.get("fuera"), bool) else False
    return {"clave": clave, "nombre": nombre, "valor": valor,
            "unidad": unidad, "rango": rango, "fuera": fuera}


def _coerce_informe(item):
    tono = str(item.get("tono") or "").strip().lower()
    tono = tono if tono in ("ok", "revisar") else "revisar"
    sensible = bool(item.get("sensible")) if isinstance(item.get("sensible"), bool) else False
    valores = []
    for v in (item.get("valores") or [])[:40]:
        cv = _coerce_valor(v)
        if cv:
            valores.append(cv)
    return {
        "tipo_estudio": chart_summary._coerce_str(item.get("tipo_estudio"), 100)
                       or "Estudio de laboratorio",
        "resumen": chart_summary._coerce_str(item.get("resumen"), 300)
                  or "Resultados registrados.",
        "tono": tono,
        "sensible": sensible,
        "valores": valores,
    }


def _extract_json(content):
    return chart_summary._extract_json(content)


# ---- la llamada a la IA (un lote de hasta _BATCH informes) ----------------

def _llamar_ia(dni, lote, modelo):
    """Un pase de IA sobre `lote` (lista de (entrada, report_hash)).
    Devuelve una lista alineada a `lote`: un dict coercido por posición, o
    None cuando ese informe puntual no vino en la respuesta (degrada sólo
    ESE informe al fallback, no todo el lote)."""
    informes_in = [
        {"idx": i, "fecha": entrada.get("fecha"),
         "servicio": entrada.get("servicio"),
         "texto": chart_summary._coerce_str(entrada.get("texto"), _MAX_TEXTO),
         # Los valores estructurados del conector también viajan: sin ellos,
         # un informe SIN texto OCR (fuente estructurada) llegaría al modelo
         # vacío y el resumen/tono saldría escrito a ciegas.
         "valores": entrada.get("valores") if isinstance(entrada.get("valores"), dict) else None}
        for i, (entrada, _h) in enumerate(lote)
    ]
    prompt = (prompts.text("laboratorio_paciente", catalogo=_catalogo_texto())
             + "\n\n" + prompts.text("laboratorio_sensible"))
    anon = pii.for_dni(dni)
    datos = anon.scrub(json.dumps({"informes": informes_in},
                                  ensure_ascii=False, default=str))
    body = {
        "model": modelo,
        "messages": [{"role": "user", "content":
                      prompt + "\n\nINFORMES:\n" + datos}],
        "temperature": 0.15,
        "max_tokens": 4000,
        "response_format": {"type": "json_object"},
        "usage": {"include": True},
    }
    data = ai_client.call(body, timeout=60, headers_extra={
        "X-Title": theme.brand_name() + " - laboratorio del paciente"})
    content = anon.restore(data["choices"][0]["message"].get("content") or "")
    parsed = _extract_json(content)
    try:
        from app.ai import ai_costs
        costo = (data.get("usage") or {}).get("cost")
        ai_costs.record("laboratorio_paciente", cost_usd=float(costo or 0.0),
                        model=modelo, recognized=True)
    except Exception:  # noqa: BLE001
        pass
    por_idx = {}
    if isinstance(parsed, dict):
        for item in parsed.get("informes") or []:
            if isinstance(item, dict) and isinstance(item.get("idx"), (int, float)):
                por_idx[int(item["idx"])] = item
    return [_coerce_informe(por_idx[i]) if i in por_idx else None
           for i in range(len(lote))]


# ---- orquestación: cache, lotes, topes ------------------------------------

def _fresca(fila, phash):
    return bool(fila and fila.get("prompt_hash") == phash
               and fila.get("modelo") not in _FALLBACK_MARKERS)


def _persistir_fila(dni, rhash, phash, entrada, fila_datos, cache):
    """Upsert + espejo en el dict `cache` de esta pasada. Best-effort DB."""
    if _forzar_sensible(entrada, fila_datos):
        fila_datos["sensible"] = True
    try:
        db_clinical.lab_analysis_upsert(
            dni, rhash, phash, entrada.get("fecha"), entrada.get("sistema"),
            fila_datos["tipo_estudio"], fila_datos["resumen"],
            fila_datos["tono"], fila_datos["sensible"],
            fila_datos["valores"], fila_datos["modelo"])
    except Exception as e:  # noqa: BLE001 — igual se muestra esta vez
        _log(f"no se pudo persistir el análisis {log_pii.dni(dni)}/{rhash[:8]}: {log_pii.exc(e)}")
    cache[rhash] = dict(fila_datos, report_hash=rhash, prompt_hash=phash)


def _procesar_lotes(dni, a_procesar, modelo, fake, tiene_clave, phash, cache):
    """Corre hasta `_MAX_BATCHES` lotes (IA o fallback) y persiste. Mutates cache."""
    for inicio in range(0, len(a_procesar), _BATCH):
        lote = a_procesar[inicio:inicio + _BATCH]
        resultados = None
        if not fake and tiene_clave:
            try:
                resultados = _llamar_ia(dni, lote, modelo)
            except Exception as e:  # noqa: BLE001 — red/HTTP/parse: degradar
                _log(f"lote falló para {log_pii.dni(dni)}: {log_pii.exc(e)}")
                resultados = None
        for (entrada, rhash), resultado in zip(
                lote, resultados or [None] * len(lote)):
            if resultado is None:
                motivo = ("fake" if fake else
                         ("sin_clave_api" if not tiene_clave else "fallback"))
                fila_datos = _fallback_row(entrada, motivo)
            else:
                fila_datos = dict(resultado, modelo=modelo)
            _persistir_fila(dni, rhash, phash, entrada, fila_datos, cache)


def _sembrar_fallback(dni, pendientes, phash, cache):
    """Filas determinísticas YA, sin esperar al modelo — valores estructurados
    del conector + resumen mecánico. La IA las pisa en el hilo de fondo."""
    for entrada, rhash in pendientes:
        if cache.get(rhash):
            continue
        fila_datos = _fallback_row(entrada, "fallback")
        _persistir_fila(dni, rhash, phash, entrada, fila_datos, cache)


def _calentar_traducciones(filas):
    """Llena la caché de traducción de las lecturas recién hechas, en este
    mismo pase (ya corre fuera del request), para que el paciente que lee en
    otro idioma las vea traducidas apenas están. Best-effort: sin traducción
    se muestra el castellano."""
    try:
        from app.ai import lab_language
        lab_language.calentar(filas)
    except Exception as e:  # noqa: BLE001 — la lectura ya quedó guardada
        _log(f"no se pudo calentar la traducción: {log_pii.exc(e)}")


def _schedule_ia(dni):
    """Un pase sync en background; no-op si ese DNI ya está en vuelo."""
    with _INFLIGHT_LOCK:
        if dni in _INFLIGHT:
            return False
        _INFLIGHT.add(dni)

    def _run():
        try:
            analizar(dni, sync=True)
        except Exception as e:  # noqa: BLE001 — el GET ya respondió
            _log(f"pase de fondo falló para {log_pii.dni(dni)}: {log_pii.exc(e)}")
        finally:
            with _INFLIGHT_LOCK:
                _INFLIGHT.discard(dni)

    threading.Thread(target=_run, name=f"lab-analysis-{dni}",
                     daemon=True).start()
    return True


def analizar(dni, *, sync=False):
    """Punto de entrada: (resueltos, analisis_pendiente, fuentes_estado).

    `resueltos` es una lista de (entrada_historia, fila_analysis) — un par
    por cada informe de laboratorio que YA tiene una lectura persistida
    (fresca o no; un informe que nunca se llegó a leer, por el tope de lote,
    no aparece acá — sólo cuenta en `analisis_pendiente`).

    Por defecto (`sync=False`) la respuesta del paciente no espera al modelo:
    si hace falta IA real, se siembran fallbacks, se marca `analisis_pendiente`
    y el pase corre en un hilo. `sync=True` (tests / worker de fondo) bloquea
    hasta procesar hasta `_BATCH * _MAX_BATCHES` informes."""
    dni = str(dni or "").strip()
    if not dni:
        return [], 0, {}

    historia, estados = chart_summary._historia_clinica(dni)
    labs = [e for e in historia
           if str(e.get("tipo") or "").strip().lower() == "laboratorio"]
    if not labs:
        return [], 0, estados

    try:
        filas = db_clinical.lab_analysis_for(dni)
    except Exception as e:  # noqa: BLE001 — sin base: se sigue sin cache
        _log(f"no se pudo leer el cache de {log_pii.dni(dni)}: {log_pii.exc(e)}")
        filas = []
    cache = {f["report_hash"]: f for f in filas}

    phash = prompt_hash()
    entradas_hash = [(e, report_hash(e)) for e in labs]
    pendientes = [(e, h) for e, h in entradas_hash
                 if not _fresca(cache.get(h), phash)]
    total_pendiente = len(pendientes)

    modelo = _model()
    fake = config.environ_text("TURNOS_FAKE_AI") == "1"
    tiene_clave = ai_client.available()
    necesita_ia = bool(pendientes) and (not fake) and tiene_clave

    # Camino rápido del GET: no pagar el timeout del modelo en el request.
    # Sembrar fallbacks (barato) + hilo de fondo; el front reintenta una vez.
    if necesita_ia and not sync:
        _sembrar_fallback(dni, pendientes, phash, cache)
        _schedule_ia(dni)
        resueltos = [(entrada, cache[h]) for entrada, h in entradas_hash
                    if cache.get(h)]
        return resueltos, total_pendiente, estados

    a_procesar = pendientes[: _BATCH * _MAX_BATCHES]
    _procesar_lotes(dni, a_procesar, modelo, fake, tiene_clave, phash, cache)
    _calentar_traducciones([cache[h] for _e, h in a_procesar if cache.get(h)])

    procesados = len(a_procesar)
    analisis_pendiente = max(0, total_pendiente - procesados)

    resueltos = [(entrada, cache[h]) for entrada, h in entradas_hash
                if cache.get(h)]
    return resueltos, analisis_pendiente, estados


# ---- lo que lab_results.py arma con `resueltos` ---------------------------

def merge_valores(entrada, fila):
    """Valores estructurados del conector ∪ valores extraídos por la IA —
    un valor estructurado (catalogado) le gana a uno de la IA con la misma
    `clave` (docs/architecture/laboratorio-paciente.md, API)."""
    estructurados = _valores_estructurados(entrada)
    claves = {v["clave"] for v in estructurados}
    extra = [v for v in (fila.get("valores") or [])
            if not (v.get("clave") and v["clave"] in claves)]
    return estructurados + extra


def series_de(resueltos):
    """Serie graficable por catálogo — código, no IA: cada punto sale de un
    valor estructurado o de un valor de IA cuya `clave` ya está validada
    contra `_SERIES_DEF`. Los informes sensibles NUNCA aportan un punto."""
    sintetica = []
    for entrada, fila in resueltos:
        if fila.get("sensible"):
            continue
        mapa = {v["clave"]: v["valor"] for v in merge_valores(entrada, fila)
               if v.get("clave")}
        if mapa:
            sintetica.append({"fecha": entrada.get("fecha"), "valores": mapa})
    return chart_summary._series(sintetica)
