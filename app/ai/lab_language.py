"""lab_language.py — el idioma en que el paciente lee su informe de laboratorio
(docs/architecture/laboratorio-paciente.md, «Language»; TASK-20, 2026-10-28).

La lectura de IA (`tipo_estudio` y `resumen`) se guarda UNA vez, en castellano,
tal como salió del modelo: es el dato clínico. Qué idioma ve cada visitante se
resuelve al mostrar, nunca al guardar — la traducción no se escribe en
`lab_report_analysis`:

  - Lecturas de IA: la traducción sale de la caché de `core/translate.py`
    (tabla `translations`, clave `labs.reading.<report_hash>.<campo>`, el hash
    del texto fuente decide si sigue vigente). El GET sólo LEE la caché —
    `translate.cached_text`, cero llamadas al modelo —; la caché la llenan
    `calentar()` (el mismo pase de fondo que hizo la lectura) y el barrido
    periódico de `ops/translate_warmup.py` (`entradas_barrido()`). Una lectura
    todavía sin traducir se muestra en castellano y el próximo barrido la trae.
  - Lecturas de fallback (`modelo` fake/sin_clave_api/fallback): no tienen
    modelo detrás, así que no se traducen: el texto se vuelve a ARMAR al
    mostrar con `i18n.t_or` según cuántos valores están fuera de rango
    (`lab_analysis.fallback_textos`).
  - Los nombres de las determinaciones del catálogo cerrado (Glucemia, HbA1c…)
    también salen del catálogo de idiomas; los que la IA extrajo del papel
    quedan como los imprime el laboratorio.

Los informes sensibles nunca llegan acá: ni se muestran ni se mandan a
traducir. El texto que viaja al traductor es la frase cautelosa de una línea
y la etiqueta del tipo de estudio que el prompt le pide al modelo (sin nombre
ni documento del paciente); las claves van por hash de contenido, sin DNI.
"""
from __future__ import annotations

import re
import sys

from app.ai import lab_analysis
from app.core import log_pii
from app.core import translate

_PREFIJO = "labs.reading"
_CAMPOS = ("tipo_estudio", "resumen")


def _log(msg):
    print(f"[lab_language] {msg}", file=sys.stderr, flush=True)


def clave(report_hash, campo):
    return f"{_PREFIJO}.{report_hash}.{campo}"


def prefijo_de(report_hash):
    return f"{_PREFIJO}.{report_hash}."


def aplica(lang):
    """True cuando `lang` no es el idioma de la instalación — el único caso
    en que hay algo que traducir o rearmar."""
    return translate._primary(lang) != translate._primary(translate.pack_language())


def textos(entrada, fila, lang):
    """(tipo_estudio, resumen) de la lectura `fila` para quien lee en `lang`."""
    tipo, resumen = fila.get("tipo_estudio"), fila.get("resumen")
    if not aplica(lang):
        return tipo, resumen
    if lab_analysis.es_fallback(fila):
        t, r, _tono = lab_analysis.fallback_textos(entrada, lang)
        return t, r
    pack = translate.pack_language()
    rhash = fila.get("report_hash")
    if not rhash:
        return tipo, resumen
    return (_sin_marca(translate.cached_text(clave(rhash, "tipo_estudio"), tipo, lang,
                                             pack_lang=pack), tipo),
            _sin_marca(translate.cached_text(clave(rhash, "resumen"), resumen, lang,
                                             pack_lang=pack), resumen))


_MARCA = re.compile(r"^\[[A-Za-z]{2,3}(?:-[A-Za-z0-9]+)?\] ")


def _sin_marca(traducido, original):
    """El proveedor falso (`TURNOS_FAKE_AI=1` sin `TURNOS_FAKE_TRANSLATE=0`)
    antepone la marca del idioma, p. ej. «[en] ». Esa marca es una prueba de la
    caché, nunca texto para un paciente: si llega, se lee el original."""
    if traducido and _MARCA.match(traducido) and not _MARCA.match(original or ""):
        return original
    return traducido


def items_en(items, lang):
    """Los valores con el nombre del catálogo en `lang`. No toca los que la IA
    extrajo del papel (`clave` nula)."""
    if not aplica(lang):
        return items
    out = []
    for it in items:
        if it.get("clave"):
            it = dict(it, nombre=lab_analysis.etiqueta_de(it["clave"], lang))
        out.append(it)
    return out


def series_en(series, lang):
    if not aplica(lang):
        return series
    return [dict(s, label=lab_analysis.etiqueta_de(s.get("id"), lang)) for s in series]


def _entradas(filas):
    """[(clave, texto)] de las lecturas con modelo detrás y no sensibles."""
    out = []
    for fila in filas:
        if fila.get("sensible") or lab_analysis.es_fallback(fila):
            continue
        rhash = fila.get("report_hash")
        if not rhash:
            continue
        for campo in _CAMPOS:
            texto = str(fila.get(campo) or "").strip()
            if texto:
                out.append((clave(rhash, campo), texto))
    return out


def calentar(filas, idiomas=None):
    """Traduce a cada idioma habilitado las lecturas `filas` (dicts de
    `lab_report_analysis`) que su caché aún no tiene. Devuelve cuántas frases
    se tradujeron."""
    entradas = _entradas(filas)
    if not entradas:
        return 0
    if idiomas is None:
        from app.ops import translate_warmup
        idiomas = translate_warmup.pending_languages()
    pack = translate.pack_language()
    hechas = 0
    for lang in idiomas:
        viejas = [(k, t) for k, t in entradas
                  if translate.needs_translation(k, t, lang, pack_lang=pack)]
        if viejas:
            hechas += len(translate.translate_batch(viejas, lang))
    return hechas


def entradas_barrido():
    """Las lecturas de todos los pacientes para el barrido periódico — sólo
    hash y frase, sin DNI (`db_clinical.lab_analysis_lecturas`)."""
    from app.core import db_clinical
    try:
        return _entradas(db_clinical.lab_analysis_lecturas())
    except Exception as e:  # noqa: BLE001 — el barrido sigue con lo demás
        _log(f"no se pudieron leer las lecturas para traducir: {log_pii.exc(e)}")
        return []


def olvidar(report_hash):
    """Borra las traducciones de una lectura que el paciente borró: son el
    mismo dato clínico que la lectura."""
    from app.core import db_catalog
    db_catalog.translations_delete_prefix(prefijo_de(report_hash))
    translate.invalidate_rows()
