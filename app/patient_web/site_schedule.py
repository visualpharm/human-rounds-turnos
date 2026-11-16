"""patient_web/site_schedule.py — Opening hours and dates of the public site.

Weekly schedule text, hour windows, friendly dates and appointment formatting.

Split out of the old `patient_web/patient_site.py` (TASK-32). What lives in another domain module is reached
through that module (`site_catalog._services()`), looked up at CALL time, so a test patching
`site_catalog._services` patches what this module uses too. The modules form layers: an import never
points back up (`tests/test_patient_site_domain_modules.py`).
"""

import re

from app.core import i18n

from app.patient_web import site_base


def _days_text(dias):
    """[1,2,3] -> "Lunes a miércoles" ("Monday to Wednesday"); [1,4] -> "Lunes
    y jueves" ("Monday and Thursday"). Days arrive in `Date.getDay()`
    convention (0=Sunday) like the rest of the project; the week reads Monday
    to Sunday, the way a person reads it. Consecutive runs of 3 or more
    contract with the range connector.

    The connector and the mid-phrase lowercase are LANGUAGE-DEPENDENT (same
    rule as `_horario_display()` above and listing.js's withWeekdayArticle():
    Spanish joins with "a"/"y" and lowercases the day AFTER the first one
    mid-sentence; English always capitalizes and joins with "to"/"and").
    This function hardcoded the Spanish shape unconditionally until the
    live-browser i18n audit caught it, 2026-08-05: the doctor page's own
    "Cuándo atiende" table (`_schedule_table_html()`) kept saying "Monday a
    friday" in English, because `_horario_display()`'s fix never reached this
    SEPARATE day-range renderer — that one only feeds the workshops page's
    free-text `horario`, this one builds the doctor table's day column from
    structured day numbers."""
    lang = i18n.active()
    lowers_midphrase = bool(i18n.t_or("date.article", "", lang))
    to_word = i18n.t_or("date.to", "a ", lang).strip()
    and_word = i18n.t_or("date.and", "y ", lang).strip()
    pos = sorted({(int(d) + 6) % 7 for d in (dias or []) if str(d).lstrip("-").isdigit()})
    if not pos:
        return ""
    tramos, actual = [], [pos[0]]
    for p in pos[1:]:
        if p == actual[-1] + 1:
            actual.append(p)
        else:
            tramos.append(actual)
            actual = [p]
    tramos.append(actual)
    partes = []
    for t in tramos:
        if len(t) >= 3:
            last = _day_name(t[-1])
            if lowers_midphrase:
                last = last.lower()
            partes.append(f"{_day_name(t[0])} {to_word} {last}")
        else:
            partes += [_day_name(x) for x in t]
    if lowers_midphrase:
        partes = [p if i == 0 else p[0].lower() + p[1:] for i, p in enumerate(partes)]
    if len(partes) == 1:
        return partes[0]
    return ", ".join(partes[:-1]) + f" {and_word} " + partes[-1]


def _to_min(hhmm):
    try:
        h, mm = str(hhmm).split(":")[:2]
        return int(h) * 60 + int(mm)
    except (ValueError, AttributeError):
        return None


def _fmt_min(v):
    # «8:00», the hour as platform/week.mjs and hours.mjs print it: a doctor's
    # strip said «07:00» next to a service's «8:00» (hr-patient review #22).
    return f"{v // 60}:{v % 60:02d}"


def _day_hours_text(tramos):
    """One day's hours as shown in a block: overlapping or contained windows
    collapse into their union (a doctor loaded "08:00-14:00" AND "10:00-12:00"
    for the same Wednesday must read once, "08:00 – 14:00"); separate windows
    join with a comma, never a dash."""
    rangos, puntuales = [], set()
    for desde, hasta in tramos:
        a, b = _to_min(desde), _to_min(hasta)
        if a is None:
            continue
        if b is None or b <= a:
            puntuales.add(a)
        else:
            rangos.append([a, b])
    rangos.sort()
    unidos = []
    for a, b in rangos:
        if unidos and a <= unidos[-1][1]:
            unidos[-1][1] = max(unidos[-1][1], b)
        else:
            unidos.append([a, b])
    partes = [(a, f"{_fmt_min(a)} – {_fmt_min(b)}") for a, b in unidos]
    partes += [(a, _fmt_min(a)) for a in puntuales
               if not any(x <= a < y for x, y in unidos)]
    return ", ".join(t for _, t in sorted(partes))


def _week_blocks(filas, hoy_pos):
    """The week Monday→Sunday as blocks: consecutive days with the SAME hours
    (closed days included) merge into one block whose width is its day count
    — the Brila / platform/week.mjs pattern (the product owner, 2026-10-06)."""
    por_dia = [[] for _ in range(7)]
    for f in filas:
        for d in f["dias"]:
            por_dia[(d + 6) % 7].append((f["desde"], f["hasta"]))
    textos = [_day_hours_text(t) for t in por_dia]
    bloques = []
    for pos, txt in enumerate(textos):
        if bloques and bloques[-1]["horas"] == txt:
            bloques[-1]["pos"].append(pos)
        else:
            bloques.append({"horas": txt, "pos": [pos]})
    lang = i18n.active()
    hoy_word = i18n.t_or("date.today", "Hoy", lang)
    lowers = bool(i18n.t_or("date.article", "", lang))
    for b in bloques:
        b["label"] = _days_text([(p + 1) % 7 for p in b["pos"]])
        b["hoy"] = ""
        if hoy_pos in b["pos"]:
            if len(b["pos"]) == 1:
                b["hoy"] = hoy_word
            else:
                dia = _day_name(hoy_pos)
                b["hoy"] = f"{hoy_word}, {dia.lower() if lowers else dia}"
    return bloques


def _week_strip_html(filas, hoy_pos):
    bloques = _week_blocks(filas, hoy_pos)
    cerrado = i18n.t_or("date.closed", "No atiende", i18n.active())
    cols = " ".join(f"{len(b['pos'])}fr" for b in bloques)
    celdas = []
    for b in bloques:
        # «Hoy» marks a day the doctor attends; on a «No atiende» block it
        # was a black chip repeated on every strip (hr-patient review #23).
        hoy = (f' <span class="mp-ag-hoy">{site_base._esc(b["hoy"])}</span>'
               if b["hoy"] and b["horas"] else "")
        clase = "mp-ag-b mp-ag-b--on" if b["horas"] else "mp-ag-b"
        celdas.append(
            f'<div class="{clase}"><div class="mp-ag-d">{site_base._esc(b["label"])}{hoy}</div>'
            f'<div class="mp-ag-h">{site_base._esc(b["horas"] or cerrado)}</div></div>')
    return (f'<div class="mp-ag-week" style="grid-template-columns:{cols}">'
            + "".join(celdas) + "</div>")


_DAYS_ABBR = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
_MONTH_ABBR = ["ene", "feb", "mar", "abr", "may", "jun",
             "jul", "ago", "sep", "oct", "nov", "dic"]


def _fmt_appointment(dia, hora):
    """'2026-07-27','08:00' -> 'lun 27/07 08:00' (best-effort)."""
    try:
        import datetime
        d = datetime.date.fromisoformat(str(dia)[:10])
        return f"{_DAYS_ABBR[d.weekday()]} {d.day:02d}/{d.month:02d}" + (f" {hora}" if hora else "")
    except Exception:
        return str(dia or "")


_DAYS_FULL = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
_DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _day_name(index):
    """Weekday name in the visitor's language. The DATA always says the
    Spanish one — hours.mjs and platform/week.mjs parse Spanish day names out of a
    pack's `horario` — so the translation happens here, at render."""
    return i18n.t_or(f"date.day.{_DAY_KEYS[index]}", _DAYS_FULL[index],
                     i18n.active())


def _horario_display(texto):
    """A pack `horario` ("Miércoles de 13 a 14 h") shown to a visitor.

    The stored string stays canonical because it is PARSED (pack_l10n.py's
    fourth rule), so translating it in the data would break the weekly grid.
    Here it is only being printed, so the day name and the two connectors get
    swapped for the visitor's language and nothing else is touched — a
    substitution narrow enough that a schedule it doesn't recognise comes out
    exactly as it went in."""
    texto = str(texto or "")
    if not texto or i18n.active() == i18n.CANONICAL_LANG:
        return texto
    lang = i18n.active()
    out = texto
    # Castellano baja el SEGUNDO día de un rango a minúscula mid-frase ("Lunes
    # a viernes"); inglés capitaliza los dos siempre ("Monday to Friday") —
    # misma regla que platform/week.mjs/bloques() (responsable del producto, 2026-08-04). Sin este flag el
    # segundo día heredaba la minúscula del castellano de origen ("viernes"
    # -> "friday" en vez de "Friday"): gramática prestada, no la del idioma
    # de salida (bug real, demo, 2026-08-05).
    lowers_midphrase = bool(i18n.t_or("date.article", "", lang))
    # El conector entre DOS NOMBRES DE DÍA — "a" para un RANGO ("Lunes a
    # viernes") e "y" para una LISTA ("Martes y jueves", "Lunes, martes y
    # miércoles de…", ambos reales en el pack) — se traduce ANTES de
    # reemplazar los días: el lookahead de dígito de más abajo es para horas
    # ("a las 10"), nunca matchea el "a"/"y" que separa un día de otro, así
    # que ese conector quedaba en castellano aunque los dos días ya
    # estuvieran traducidos (bug real, demo, 2026-08-05: "Monday a friday";
    # revisión de código el mismo día encontró que "y" — más común que "a"
    # en el pack real, "Martes y jueves de…" — tenía el mismo agujero).
    day_alt = "|".join(re.escape(d) for d in _DAYS_FULL)
    _CONECTORES = {"a": "date.to", "y": "date.and"}

    def _conector(m):
        clave = _CONECTORES[m.group(3).lower()]
        defecto = "a " if clave == "date.to" else "y "
        return m.group(1) + m.group(2) + i18n.t_or(clave, defecto, lang).strip() + m.group(4)
    out = re.sub(
        rf"\b((?:{day_alt}))\b(\s+)(a|y)(\s+)(?=(?:{day_alt})\b)",
        _conector, out, flags=re.IGNORECASE)
    for n, day in enumerate(_DAYS_FULL):
        translated = _day_name(n)
        if translated == day:
            continue
        low_translated = translated.lower() if lowers_midphrase else translated
        out = re.sub(rf"\b{day}\b", translated, out)
        out = re.sub(rf"\b{re.escape(day.lower())}\b", low_translated, out)
    # "de 13 a 14" / "a las 10:30": el campo horario nunca es prosa libre
    # (siempre `Servicio.horario`, pack_l10n.py regla 4), así que un walk-in
    # SIN día ("14:00 a 18:00") es tan legítimo como uno con día — una guarda
    # vieja ("sólo si ya apareció un día") lo dejaba intacto (bug real, demo,
    # 2026-08-05). Pero sacarla del todo (cualquier "de"/"a" seguido de un
    # dígito) traducía también prosa ajena a un horario ("Servicio de 24
    # horas" -> "Servicio from 24 horas", visto en revisión de código): el
    # reemplazo ahora exige el RANGO completo "de HH(:MM) a HH(:MM)" como una
    # sola unidad, que es la única forma que aparece en `horario` de verdad.
    _HORA = r"\d{1,2}(?:[.:]\d{2})?"

    def _rango_horas(m):
        de = i18n.t_or("date.from", "de ", lang)
        a = i18n.t_or("date.to", "a ", lang).strip()
        return f"{de}{m.group(1)} {a} {m.group(2)}"
    out = re.sub(rf"\bde\s+({_HORA})\s*a\s+({_HORA})", _rango_horas, out)
    # El mismo rango SIN "de" adelante ("14:00 a 18:00", un walk-in sin día)
    # es igual de legítimo — corre DESPUÉS del anterior: para cuando llega
    # acá, un "de X a Y" ya perdió su "a" bajo _rango_horas, así que esto
    # sólo agarra los rangos que nunca tuvieron "de".
    out = re.sub(rf"\b({_HORA})\s+a\s+({_HORA})\b",
                 lambda m: f"{m.group(1)} {i18n.t_or('date.to', 'a ', lang).strip()} {m.group(2)}",
                 out)
    out = re.sub(r"\ba\s+las\s+(?=\d)",
                 i18n.t_or("date.at", "a las ", lang), out)
    return out
_MONTH_FULL = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _friendly_date(dia, hora=None, hoy=None):
    """Date/time IN TEXT the way a person would say it (wizard-design,
    2026-07-10): 'Hoy, 14:40' / 'Mañana, miércoles 11, 14:40' / 'Martes 14 de julio, 14:40'
    (year only if not the current one). Twin of dates.mjs — same contract. ''
    if the day doesn't parse."""
    import datetime
    try:
        d = datetime.date.fromisoformat(str(dia)[:10])
    except Exception:
        return ""
    hoy = hoy or site_base._today_art()
    lang = i18n.active()
    if d == hoy:
        base = i18n.t_or("date.today", "Hoy", lang)
    elif d == hoy + datetime.timedelta(days=1):
        # Tomorrow always carries its weekday and number: a bare «Mañana»
        # over the morning hours reads as «in the morning» (2026-11-10).
        # Twin of dates.mjs diaFechado().
        day = _day_name(d.weekday())
        base = i18n.t_or("date.tomorrow_dated", "Mañana, {day_lower} {n}", lang,
                         day=day, day_lower=day[:1].lower() + day[1:], n=d.day)
    else:
        # The whole shape is one catalog string, not day + " de " + month
        # (+ " de " + year): a language that puts the month first, or has no
        # preposition, needs to move the PIECES, not just translate the
        # words — the year suffix used to be bolted on with a hardcoded
        # " de {year}" (Spanish, always), which is the same bug class as
        # _get_label's article (the product owner, 2026-08-04).
        day = _day_name(d.weekday())
        month = i18n.t_or(f"date.month_full.{d.month}",
                          _MONTH_FULL[d.month - 1], lang)
        if d.year != hoy.year:
            base = i18n.t_or("date.long_year", "{day} {n} de {month} de {year}",
                              lang, day=day, n=d.day, month=month, year=d.year)
        else:
            base = i18n.t_or("date.long", "{day} {n} de {month}", lang,
                              day=day, n=d.day, month=month)
    h = str(hora or "").strip()
    return f"{base}, {h}" if h else base


_DAYS_SHORT = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
_MONTH_SHORT = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _short_date(dia, hora=None, hoy=None):
    """The availability column's ONE date shape (review 2026-11-15, P21):
    'Hoy, 08:00' / 'Mañana, 08:00' / 'Mié 18 nov, 08:00' (year only if not
    the current one). Twin of dates.mjs corta() — same catalog keys
    (date.day_short.*, date.month.*). '' if the day doesn't parse."""
    import datetime
    try:
        d = datetime.date.fromisoformat(str(dia)[:10])
    except Exception:
        return ""
    hoy = hoy or site_base._today_art()
    lang = i18n.active()
    if d == hoy:
        base = i18n.t_or("date.today", "Hoy", lang)
    elif d == hoy + datetime.timedelta(days=1):
        base = i18n.t_or("date.tomorrow", "Mañana", lang)
    else:
        wd = d.weekday()
        base = (f'{i18n.t_or(f"date.day_short.{_DAY_KEYS[wd]}", _DAYS_SHORT[wd], lang)} {d.day} '
                f'{i18n.t_or(f"date.month.{d.month}", _MONTH_SHORT[d.month - 1], lang)}')
        if d.year != hoy.year:
            base += f" {d.year}"
    h = str(hora or "").strip()
    return f"{base}, {h}" if h else base


def _short_date_in_phrase(dia, hora=None, hoy=None):
    """`_short_date` placed inside a sentence: 'mañana, 08:00' /
    'el mié 18 nov, 08:00' in Spanish, 'Wed 18 Nov, 08:00' in English (no
    article, case kept). Twin of listing.mjs fechaEnFrase(). '' if the day
    doesn't parse."""
    import datetime
    label = _short_date(dia, hora, hoy)
    if not label:
        return ""
    lang = i18n.active()
    if not i18n.t_or("date.article", "el", lang):
        return label
    low = label[:1].lower() + label[1:]
    try:
        d = datetime.date.fromisoformat(str(dia)[:10])
    except Exception:
        return low
    hoy = hoy or site_base._today_art()
    if d in (hoy, hoy + datetime.timedelta(days=1)):
        return low
    return i18n.t_or("date.weekday_with_article", "el {day}", lang, day=low)


def _day_month(dia, hoy=None):
    """The short form of `_friendly_date` for a run of dates in one sentence:
    '9 de noviembre' (year only if not the current one), no weekday. ''
    if the day doesn't parse."""
    import datetime
    try:
        d = datetime.date.fromisoformat(str(dia)[:10])
    except Exception:
        return ""
    hoy = hoy or site_base._today_art()
    lang = i18n.active()
    month = i18n.t_or(f"date.month_full.{d.month}", _MONTH_FULL[d.month - 1], lang)
    if d.year != hoy.year:
        return i18n.t_or("date.day_month_year", "{n} de {month} de {year}", lang,
                         n=d.day, month=month, year=d.year)
    return i18n.t_or("date.day_month", "{n} de {month}", lang, n=d.day, month=month)


def _window_hours(fr):
    """Weekly hours of ONE window {dias,desde,hasta,cada_min} (cada_min
    doesn't count, only the window's width * number of days). 0.0 if it
    doesn't parse or the window isn't positive."""
    try:
        dh, dm = (int(x) for x in (fr.get("desde") or "").split(":"))
        hh, hm = (int(x) for x in (fr.get("hasta") or "").split(":"))
    except (ValueError, AttributeError):
        return 0.0
    horas = (hh * 60 + hm - (dh * 60 + dm)) / 60.0
    if horas <= 0:
        return 0.0
    return horas * len(fr.get("dias") or [])


def _schedule_hours(agenda):
    """Total weekly hours of an `agenda` — accepts the flat form
    {dias,desde,hasta,cada_min} OR {franjas:[...]} (same duality as
    _demo_next). 0.0 if there's nothing usable."""
    if not isinstance(agenda, dict):
        return 0.0
    if "franjas" in agenda:
        return sum(_window_hours(fr) for fr in (agenda.get("franjas") or []))
    if agenda.get("dias"):
        return _window_hours(agenda)
    return 0.0


def _week_hours_doctor(m):
    """A doctor's own weekly hours: their real `agenda` if they have one,
    otherwise the sum of windows across ALL their centers (`centros[].franjas`)."""
    h = _schedule_hours(m.get("agenda") or {})
    if h:
        return h
    total = 0.0
    for c in (m.get("centros") or []):
        total += sum(_window_hours(fr) for fr in (c.get("franjas") or []))
    return total
