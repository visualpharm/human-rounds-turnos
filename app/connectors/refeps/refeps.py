"""refeps.py — consulta al Buscador Nacional de Profesionales de la Salud
(REFEPS, argentina.gob.ar) por número de matrícula.

Es un formulario Drupal que responde por POST; nada de JS/browser ni CAPTCHA:
GET la página (Cloudflare la cachea ~8h, el form_build_id embebido sirve para
toda esa ventana), POST la búsqueda, parsear el JSON que Drupal embebe en la
respuesta (`Drupal.settings.refepsProfesionales.allItems`) — verificado con
curl el 2026-07-11. Sin sesión, sin cookies.

Un mismo número de matrícula puede pertenecer a varias personas (distinta
profesión/jurisdicción) — buscar_matricula() devuelve TODOS los candidatos;
mejor_match() los rankea contra el nombre del médico leído de la orden.

Moved here from repo-root refeps.py in the Phase 6b connector extraction
(2026-07-30) — this is now the `license_lookup` capability's implementation
(see connectors/refeps/connector.json, connectors/refeps/adapter.py).
The repo-root `refeps.py` compat shim was retired in Tier 2 batch L
(2026-08-07): import `connectors.refeps.refeps` directly. No colmed9
(Colegio de Médicos PBA Distrito IX) connector exists in this repo — that
lookup lives entirely in the `doctor-lookup` operator skill (external
tooling, queried by a human/agent, never called at runtime by the app) and
is documented, not implemented, in docs/connectors.md.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from app.core import data_packs
from app.core import text_fold

# env override (TURNOS_REFEPS_URL) > packs/ar/endpoints.json ("refeps.search_url")
# > literal fallback — same precedence + read-once pattern as puco.py's
# SISA_URL (Phase 2c, 2026-07-30, see docs/architecture/data-packs.md).
_FALLBACK_URL = "https://www.argentina.gob.ar/salud/buscador-nacional-de-profesionales-de-la-salud"


def _load_url():
    return data_packs.pack_setting("TURNOS_REFEPS_URL", _FALLBACK_URL, "endpoints.json", "refeps", "search_url")


URL = _load_url()
_FORM_ID = "argobar_consulta_refeps_profesionales"
_TIMEOUT = 12
_UA = "Mozilla/5.0 (compatible; SanaTurnos/1.0; +https://pinamar.gob.ar/salud/)"
# 2026-07-15: dos búsquedas reales fallaron con 502 (RefepsError) en el admin y
# funcionaron segundos/horas después sin ningún cambio de código — blip de red
# o de Cloudflare contra argentina.gob.ar, no un bug de parseo. Un solo intento
# tira la consulta entera cuando el sitio de terceros tarda o hiccupea; con un
# reintento corto la mayoría se resuelve sola (ver docs/solutions).
_RETRIES = 2
_RETRY_DELAY = 1.5


class RefepsError(Exception):
    """La consulta a REFEPS falló (red, timeout, o no se pudo parsear)."""


def _get(url, data=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def _form_build_id(html):
    for m in re.finditer(
        r'name="form_build_id" value="([^"]+)"\s*/>\s*'
        r'<input[^>]*name="form_id" value="' + re.escape(_FORM_ID) + r'"', html):
        return m.group(1)
    return None


def _extract_items(html):
    marker = "refepsProfesionales.allItems = "
    i = html.find(marker)
    if i == -1:
        return []  # "No se encuentran resultados" no emite este bloque
    try:
        return json.JSONDecoder().raw_decode(html, i + len(marker))[0]
    except (ValueError, json.JSONDecodeError) as e:
        raise RefepsError(f"no se pudo parsear la respuesta: {e}")


def _buscar_matricula_once(matricula_digits):
    try:
        html = _get(URL)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RefepsError(f"no se pudo abrir el buscador: {e}")
    token = _form_build_id(html)
    if not token:
        raise RefepsError("no se encontró form_build_id en la página")
    body = urllib.parse.urlencode({
        "searchBy": "matricula",
        "matricula": matricula_digits,
        "form_build_id": token,
        "form_id": _FORM_ID,
        "op": "Consultar",
    }).encode("utf-8")
    try:
        html2 = _get(URL, data=body)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RefepsError(f"no se pudo consultar la matrícula: {e}")
    return _extract_items(html2)


def buscar_matricula(matricula_digits):
    """Lista cruda de personas con esa matrícula (puede ser [] si no hay
    resultados). Levanta RefepsError si la consulta en sí falla tras
    reintentar — un solo intento le pega demasiado seguido a blips
    transitorios del sitio de terceros (ver 2026-07-15 arriba)."""
    last_err = None
    for attempt in range(_RETRIES + 1):
        try:
            return _buscar_matricula_once(matricula_digits)
        except RefepsError as e:
            last_err = e
            if attempt < _RETRIES:
                time.sleep(_RETRY_DELAY)
    raise last_err


# ---- desambiguar por nombre del médico (varias personas comparten número) --

def _strip_accents(s):
    return text_fold.strip_nonspacing(s or "")


def _ckey(s):
    s = _strip_accents(str(s or "")).upper()
    s = re.sub(r"[^A-Z ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def mejor_match(items, nombre_medico=""):
    """Rankea los candidatos de buscar_matricula() contra el nombre leído de
    la orden (texto libre, p.ej. 'Dr. Gómez'). Devuelve (mejor_o_None, todos)
    con `todos` ordenados por score desc — el llamador decide si mostrar sólo
    el mejor o avisar que hay más de una persona con esa matrícula."""
    if not items:
        return None, []
    nk = _ckey(nombre_medico)
    ntoks = set(t for t in nk.split() if len(t) >= 3)

    def score(it):
        full = _ckey(f"{it.get('apellido','')} {it.get('nombre','')}")
        ftoks = set(full.split())
        if not ntoks or not ftoks:
            return 0
        hits = len(ntoks & ftoks)
        return hits + (0.5 if hits and full.startswith(_ckey(it.get('apellido', ''))) else 0)

    ranked = sorted(items, key=score, reverse=True)
    top = ranked[0] if ranked else None
    return (top if top and (not ntoks or score(top) > 0) else (ranked[0] if len(ranked) == 1 else None)), ranked


def simplify(it):
    """Subconjunto compacto de un ítem de REFEPS para mostrar en el admin."""
    mats = []
    for p in it.get("profesiones", []) or []:
        for m in p.get("matriculas", []) or []:
            mats.append({
                "matricula": m.get("matricula"),
                "profesion": p.get("profesionReferencia"),
                "especialidad": p.get("refepsEspecialidad"),
                "origen": m.get("origenEmite"),
                "provincia": m.get("provinciaMatricula"),
                "situacion": m.get("situacionMatricula"),
                "sancion": m.get("tipoSancion"),
            })
    return {
        "apellido": it.get("apellido"), "nombre": it.get("nombre"),
        "dni": it.get("nroDoc"), "sexo": it.get("sexo"), "activo": it.get("activo"),
        "matriculas": mats,
    }
