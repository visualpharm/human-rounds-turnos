"""msd_data.py — bridge between Pinamar Turnos' servicios and the live Mi Salud Digital
slot dump produced by tools/msd_pull.py (data/msd_turnos.json).

Pure read side: no network, no AI. patient_web/site_*.py calls turnos_for_servicio() to
decide whether the "Cuándo" step shows real bookable slots or falls back to the
request-with-order preference. Missing / stale file → returns empty, UI degrades
cleanly to solicitud-con-orden.

Moved here from repo-root msd_data.py in the Phase 6b connector extraction
(2026-07-30) — this is now (one of two implementations of) the `slot_source`
capability (see connectors/msd/connector.json, connectors/msd/adapter.py).
The repo-root `msd_data.py` compat shim was retired in Tier 2 batch L
(2026-08-07): import `connectors.msd.msd_data` directly. Only
change from the move: `_HERE` now resolves the repo root (via
`connectors.ROOT`), not this file's own connectors/msd/ directory, so
`data/msd_turnos.json` keeps resolving to the same repo-root-relative
LaunchAgent-refreshed dump it always has.
"""
import json
import os
import re
from datetime import datetime, timezone

from app.core import sitedata
from app.core import paths
from app.core import text_fold
from app.core import data_packs
from app import connectors as _connectors_pkg

# Repo root, NOT this file's own directory — see renaper.py's identical fix.
_HERE = paths.PROJECT_ROOT
# data/ es runtime local (LaunchAgent cada 30 min, excluido de git); webdata/ es el
# snapshot COMMITEADO que ve el deploy (Coolify no corre el refresco — sin él, el
# cloud mostraba "Te confirma el centro" en todos lados). Refrescarlo a mano:
#   cp data/msd_turnos.json webdata/msd_turnos.json   (y commitear)
_DUMPS = [os.path.join(_HERE, "data", "msd_turnos.json"),
          sitedata.path("msd_turnos.json")]
_CACHE = None  # (path, mtime, parsed)

# Sana centro slug -> Mi Salud Digital institución id (Pinamar, partido 60).
# Hospital de Pinamar is NOT in MSD's online system, so it has no live agenda.
# Vive en packs/ar-b/msd-institutions.json (fase 2b, 2026-07-30) — leído una
# vez al importar, igual que format.py con country.json; el literal de acá
# es el respaldo de última instancia si el pack no está disponible.
_FALLBACK_SLUG_TO_MSD = {
    "diabetpin": 924,
    "caps-ostende": 926,
    "caps-valeria": 927,
    "caps-pinamar": 928,
}


def _load_slug_to_msd():
    mapping = data_packs.value("msd-institutions.json", "slug_to_msd")
    return mapping if isinstance(mapping, dict) and mapping else dict(_FALLBACK_SLUG_TO_MSD)


SLUG_TO_MSD = _load_slug_to_msd()


# Sana service names differ from MSD especialidad names (Sana tags modality in
# parens, uses shorter synonyms). Map a Sana service's core name → MSD nombre.
_ALIAS = {
    "medicina general": "medicina general y/o medicina de familia",
    "medicina general en el centro cultural": "medicina general y/o medicina de familia",
    "clinica medica": "medicina general y/o medicina de familia",
    "medicina de familia": "medicina general y/o medicina de familia",
    "traumatologia": "ortopedia y traumatologia",
    "ortopedia": "ortopedia y traumatologia",
    "ortopedia y traumatologia": "ortopedia y traumatologia",
    "pediatria": "pediatria",
    "diabetologia": "diabetologia",
    "endocrinologia": "endocrinologia",
    "neonatologia": "neonatologia",
    "cardiologia": "cardiologia",
}
_PARENS = re.compile(r"\(.*?\)")


def _norm(s):
    s = _PARENS.sub("", text_fold.ascii_fold(str(s or "")).lower())  # drop modality suffixes: "(demanda espontánea)"
    return " ".join(s.split())


def _load():
    global _CACHE
    for path in _DUMPS:
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if _CACHE and _CACHE[0] == path and _CACHE[1] == mtime:
            return _CACHE[2]
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        _CACHE = (path, mtime, data)
        return data
    return None


def _age_hours(generado):
    try:
        t = datetime.fromisoformat(str(generado))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
            now = datetime.now()
        else:
            now = datetime.now(timezone.utc)
        return round((now - t).total_seconds() / 3600, 1)
    except Exception:
        return None


_TITLES = {"dr", "dra", "lic", "med", "prof", "dres", "sr", "sra"}


def _name_tokens(s):
    s = text_fold.ascii_fold(str(s or "")).lower()
    s = re.sub(r"[^a-z ,]", " ", s)
    return [t for t in s.replace(",", " ").split() if len(t) >= 3 and t not in _TITLES]


def _msd_surnames(msd_name):
    """MSD viene 'APELLIDO, Nombre' → apellido(s) = antes de la coma."""
    s = text_fold.ascii_fold(str(msd_name or "")).lower()
    s = re.sub(r"[^a-z ,]", " ", s)
    head = s.split(",")[0] if "," in s else s
    return [t for t in head.split() if len(t) >= 3 and t not in _TITLES]


def _name_matches(msd_name, sana_name):
    """True si el apellido del médico MSD aparece en el nombre Sana
    ('ARIAS, MATIAS' ↔ 'Matias Arias'; 'MONTES, Hugo' ↔ 'Hugo Montes')."""
    sana = set(_name_tokens(sana_name))
    return any(t in sana for t in _msd_surnames(msd_name))


def _best_px(medicos, accept=None):
    """Earliest slot from matching MSD rows, optionally accepted by caller.

    The connector owns matching and center preference; a public consumer owns
    whether a timestamp is presently offerable in its installation timezone.
    Checking every published ``turnos`` entry avoids letting an old ``proximo``
    hide a later valid appointment from that consumer.
    """
    best = None
    for m in medicos:
        candidates = list(m.get("turnos") or [])
        legacy = m.get("proximo") if isinstance(m.get("proximo"), dict) else {}
        if not candidates and legacy:
            candidates = [legacy]
        for px in candidates:
            if not isinstance(px, dict) or not px.get("dia"):
                continue
            # Older dumps sometimes omit a turn's hour while their matching
            # ``proximo`` still supplies it.  Preserve that established shape
            # without using an unrelated legacy date as a second candidate.
            hora = px.get("hora") or (legacy.get("hora") if legacy.get("dia") == px.get("dia") else "")
            cand = (px["dia"], hora)
            if accept is not None and not accept(*cand):
                continue
            if best is None or cand < best:
                best = cand
    return best


def _msd_givens(msd_name):
    """Nombres de pila MSD: lo que viene después de la coma."""
    s = text_fold.ascii_fold(str(msd_name or "")).lower()
    s = re.sub(r"[^a-z ,]", " ", s)
    tail = s.split(",", 1)[1] if "," in s else ""
    return [t for t in tail.split() if len(t) >= 3 and t not in _TITLES]


def _own_match(msd_name, sana_name):
    """Match FUERTE para atribuir un turno como propio: todos los apellidos MSD
    y (si los hay) al menos un nombre de pila presentes en el nombre Sana.
    'BUSTOS, DIEGO' ↔ 'Diego Bustos' sí; 'OLMEDO, RAUL' ↔ 'Ramiro Olmedo' no."""
    sana = set(_name_tokens(sana_name))
    surs = _msd_surnames(msd_name)
    if not surs or not all(t in sana for t in surs):
        return False
    givens = _msd_givens(msd_name)
    return not givens or any(t in sana for t in givens)


def proximo_own_anywhere(medico_nombre, centro_slug=None, accept=None):
    """(dia, hora) del próximo turno PROPIO del médico buscando en TODO el dump —
    cualquier centro y cualquier especialidad. MSD a veces publica la agenda de un
    profesional bajo otra especialidad (Bustos es pediatra pero su agenda online
    está en 'Medicina general'), así que la búsqueda por cluster sola lo pierde.
    Prefiere el centro del médico; None si nada matchea con nombre completo."""
    dump = _load()
    if not dump:
        return None
    pref = SLUG_TO_MSD.get(centro_slug)
    best = None  # (ajeno-al-centro, dia, hora)
    for c in dump.get("centros", []):
        for e in c.get("especialidades", []):
            for m in e.get("medicos", []):
                if not m.get("turnos") or not _own_match(m.get("medico"), medico_nombre):
                    continue
                px = _best_px([m], accept=accept)
                if not px:
                    continue
                cand = (0 if c.get("id") == pref else 1, px[0], px[1])
                if best is None or cand < best:
                    best = cand
    return (best[1], best[2]) if best else None


def proximo_for_medico(servicio, medico_nombre, strict=False, accept=None):
    """(dia, hora) del próximo turno online de ESE médico en ese servicio, por
    match de apellido normalizado contra el dump MSD. strict=True → sólo devuelve
    el turno si el nombre matchea (no atribuye el turno de otro médico); si no,
    cae al próximo turno del servicio. None si no hay agenda / no matchea."""
    info = turnos_for_servicio(servicio)
    if not info:
        return None
    medicos = info.get("medicos", [])
    mine = [m for m in medicos if _name_matches(m.get("medico"), medico_nombre)]
    if strict:
        return _best_px(mine, accept=accept)
    return _best_px(mine, accept=accept) or _best_px(medicos, accept=accept)


def turnos_for_servicio(servicio):
    """Live slots for a Sana servicio dict, or {} when there's no online agenda.

    Returns {"medicos":[{medico, proximo, turnos:[...]}], "centro", "especialidad",
             "generado", "edad_horas"} — empty medicos ⇒ caller falls back.
    """
    if not isinstance(servicio, dict):
        return {}
    inst = SLUG_TO_MSD.get(servicio.get("centro"))
    if not inst:
        return {}
    dump = _load()
    if not dump:
        return {}
    core = _norm(servicio.get("nombre"))
    want = _ALIAS.get(core, core)
    for c in dump.get("centros", []):
        if c.get("id") != inst:
            continue
        for e in c.get("especialidades", []):
            if _norm(e.get("nombre")) != want:
                continue
            medicos = [m for m in e.get("medicos", []) if m.get("turnos")]
            if not medicos:
                return {}
            return {
                "centro": c.get("nombre"),
                "especialidad": e.get("nombre"),
                "medicos": medicos,
                "generado": dump.get("generado"),
                "edad_horas": _age_hours(dump.get("generado")),
            }
    return {}
