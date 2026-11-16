"""prep_model.py — what sections a preparation has and what they're called.

An entry from `webdata/estudios-preparacion.json` is read in FIVE different
places (the service page, the appointment wizard, "Subí tu orden", "Mis
solicitudes" and the emails), and until 2026-07-29 each one had its own idea
of what to show: one hid the instructions when `sin_preparacion` was true,
another didn't, and none knew about the newer sections. This module is the
single answer to "what does this preparation say"; each surface only
decides how to draw it.

Twin in the browser: `prep.mjs` (named exports, same functions and same
titles — keep the Spanish public function names (antes/resumen/ayuno/
secciones/vacia) and the SECCIONES keys identical to prep.mjs and to the
JSON data's own field names; that's what makes the pair copy-pasteable).
`tests/prep-modelo.test.mjs` runs both implementations against the same
inputs and compares — parity is the whole point of the pair, so if you
touch one, touch the other.

Import-safe, no dependencies: imported by patient_web/site_*.py and notifications.py,
and neither one can break because of this module.
"""

import html as _html
import unicodedata

# Order in which a preparation is read aloud, which is the order it's drawn
# in. `traer` (what to bring) sits right after `antes` (before coming)
# because packing things IS something you do before leaving the house.
SECCIONES = (
    ("antes",         "Antes de venir",   "normal"),
    ("traer",         "Qué llevar",       "normal"),
    ("avisar",        "Avisá al llegar si", "aviso"),
    ("no_hace_falta", "No hace falta",    "no"),
    ("despues",       "Después",          "normal"),
)

# Text for an entry that truly has nothing to say.
SIN_NADA = "No necesita preparación especial."


def normalize(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return " ".join(s.split())


def _field_list(e, key):
    v = e.get(key)
    return [str(x) for x in v if str(x or "").strip()] if isinstance(v, list) else []


def antes(e):
    """`antes` is the new name for what was called `preparacion` until
    2026-07-29. The old name lives on inside the ai_json of requests from
    before that day, so it's still read the same way."""
    return _field_list(e, "antes") or _field_list(e, "preparacion")


def resumen(e):
    """The line that says how much preparation there ACTUALLY is ('casi no
    hay que prepararse'), before the list of items. '' if the entry doesn't
    carry one."""
    return str(e.get("resumen") or "").strip()


def ayuno(e):
    """{"hay", "texto", "nota"} — fasting is only announced when it IS a
    requirement. When it isn't needed, that fact already travels as a
    `no_hace_falta` item: announcing it again up top would say it twice. The
    `nota` (caveats like 'sólo el transesofágico pide ayuno') is kept in
    both cases, because it's the one thing that saves someone whose referral
    says exactly the rare variant."""
    ay = e.get("ayuno") or {}
    nota = str(ay.get("nota") or "").strip()
    if not ay.get("required"):
        return {"hay": False, "texto": "", "nota": nota}
    horas = ay.get("horas")
    texto = f"Ayuno de {horas} horas." if horas else "Se hace en ayunas."
    # Don't say "podés tomar agua" twice in a row: if `antes` already
    # explains it in more detail, the detail wins.
    if not any("agua" in normalize(p) for p in antes(e)):
        if ay.get("agua_permitida") is True:
            texto += " Podés tomar agua."
        elif ay.get("agua_permitida") is False:
            texto += " Tampoco tomes agua."
    return {"hay": True, "texto": texto, "nota": nota}


def secciones(e):
    """[{clave, titulo, items, tono}] — only the ones that have something.
    `tono` is for whoever draws it: 'no' are things NOT to do and 'aviso'
    are conditions the person has to mention."""
    if not isinstance(e, dict):
        return []
    out = []
    for clave, titulo, tono in SECCIONES:
        items = antes(e) if clave == "antes" else _field_list(e, clave)
        if items:
            out.append({"clave": clave, "titulo": titulo, "items": items, "tono": tono})
    return out


def vacia(e):
    """True if there's truly nothing to tell the person."""
    if not isinstance(e, dict):
        return True
    ay = ayuno(e)
    return not (secciones(e) or ay["hay"] or ay["nota"] or resumen(e))


def _esc(s):
    return _html.escape("" if s is None else str(s), quote=True)


def cuerpo_html(e):
    """How a preparation is DRAWN, the one markup every web surface that
    shows the whole preparation uses (the service page, its /preparation
    page and the wizard's «Cómo prepararte» card): the summary, the fasting,
    then each section as a bold heading over its list — a single item is a
    sentence, not a one-bullet list. The `prep-*` classes are styled once, in
    the page CSS. Twin: `prep.mjs`'s `cuerpoHtml` (same string, pinned by
    tests/prep-modelo.test.mjs) — the product owner 26.10: on «Solicitud enviada» the
    headings «Antes de venir», «Qué llevar» did not even look bold."""
    if not e:
        return ""
    partes = []
    res = resumen(e)
    if res:
        partes.append(f'<p class="prep-resumen">{_esc(res)}</p>')
    ay = ayuno(e)
    if ay["hay"]:
        partes.append(f'<div class="prep-key">{_esc(ay["texto"])}</div>')
    if ay["nota"]:
        partes.append(f'<p class="prep-nota">{_esc(ay["nota"])}</p>')
    secs = []
    for sec in secciones(e):
        if len(sec["items"]) == 1:
            items = (f'<p class="prep-single prep-single--{sec["tono"]}">'
                     f'{_esc(sec["items"][0])}</p>')
        else:
            lis = "".join(f"<li>{_esc(i)}</li>" for i in sec["items"])
            items = f'<ul class="prep-list">{lis}</ul>'
        secs.append(f'<section class="prep-sec prep-sec--{sec["tono"]}">'
                    f'<h3 class="prep-sec-h">{_esc(sec["titulo"])}</h3>'
                    f'{items}</section>')
    if secs:
        partes.append('<div class="prep-cols">' + "".join(secs) + "</div>")
    return "".join(partes)


# Tier 2 batch D dependency-direction fix (see notifications/send.py's own
# comment): notifications/ is a domain package and must never statically
# import a module, even one as "import-safe, no dependencies" as this one
# (module docstring above) — so this module registers ITSELF there instead,
# the allowed direction (a module importing a domain package). Runs once,
# at import time (preparation.py imports this unconditionally at boot).
import sys
from app.notifications import send as _notifications_send
_notifications_send.register_turnos(prep_model=sys.modules[__name__])
