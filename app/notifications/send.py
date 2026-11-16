"""notifications.py — received/confirmation/rejection/reminder email.

On SEND (booking_requests.py:_create), approve or reject (_aprobar/_rechazar),
and the day before an approved appointment (reminders.py:tick), notifies by
email the account HOLDER (solicitante_dni — the parent/guardian if the
patient is a dependent with no account of their own, the patient themself
otherwise). Transport: notifications.mail.send() — the installation's
`email_transport` connector (Mailgun by default, Mailjet when selected).
This module is ONLY the copy/decision layer: to whom, with what
subject/body, when to skip. ALWAYS best-effort: never raises, never blocks
the approve/reject/creation (same contract as hsi_client — those operations
persist no matter what happens here). Called with a lazy import from
booking_requests.py / reminders.py.

Language (2026-08-04): there is no request bound when any of this fires —
approve/reject run off an admin action with no visitor language to read, and
the day-before daemon (reminders.py:tick) has no request at all — so
`i18n.active()` is the wrong source here, unlike almost everywhere else in
the codebase. `_destination_lang(row)` reads the STORED account preference
(`users.lang`, set at sign-up from the visitor's language and editable from
Mi cuenta — accounts.py) and falls back to the canonical language when
nothing was ever saved, which is every account that existed before this
column did. Every literal below goes through `i18n.t_or(key, "<castellano>",
lang)` with that resolved language, namespace `mail.*`
(locales/{es,en}/mail.json) — never `i18n.active()`.
"""

import re
import sys
from datetime import datetime
from html import escape as _esc

from app.core import db_booking
from app.core import db_accounts
from app.core import db_clinical
from app.notifications import mail
from app.core import i18n
from app.core import launch_signals
from app.core import log_pii
from app.core import tz as _tz
from app.core import ids as _ids


def _log(msg):
    print(f"[notificaciones] {msg}", file=sys.stderr, flush=True)


def _log_failed(what, row, reason):
    """A notice that raised after the action it reports had already persisted: one line with the request id and
    `reason` — `log_pii.exc(exc)`, the exception's type, never its text nor the patient. The caller adds its
    closed launch-signal code (core/launch_signals.py) and the request that called stays unbroken."""
    _log(f"{what} id={(row or {}).get('id')} no salio: {reason}")

# Tier 2 batch D (docs/roadmap/tier2-physical-modularization-plan.md):
# `booking_requests.py`, `prep_model.py` and `remedies.py` physically moved
# into modules/turnos/ — a disable-able MODULE. A domain package like this
# one must never import FROM a module (dependency-direction rule, see
# test_domain_dependency_direction.py's own docstring, which flagged this
# EXACT spot when batch B first landed: "Revisit when batch D turns
# booking_requests.py into modules/turnos/ ... should be resolved, not
# re-excepted"). The allowed direction is the reverse — a module may import
# a domain package — so each of the three registers itself here once, at
# its own import time, instead of this file reaching backward for them.
# Every call site below degrades to its pre-existing best-effort fallback
# (str(id), no CTA, no prep section, no remedy) if registration never ran
# (e.g. a test importing this file standalone) — same "never breaks the
# mail" contract this module already had before Tier 2. Live module
# objects (not captured function references) are stored so
# booking_requests.py's OWN tests can keep monkeypatching
# `booking_requests._entrevista_field` etc. after registration and have it
# still take effect (attribute lookups happen at call time, off the same
# object).
_turnos_booking_requests = None
_turnos_prep_model = None
_turnos_remedies = None


def register_turnos(booking_requests=None, prep_model=None, remedies=None):
    """Called once each by modules/turnos/booking_requests.py,
    modules/turnos/prep_model.py and modules/turnos/remedies.py's own
    module bodies."""
    global _turnos_booking_requests, _turnos_prep_model, _turnos_remedies
    if booking_requests is not None:
        _turnos_booking_requests = booking_requests
    if prep_model is not None:
        _turnos_prep_model = prep_model
    if remedies is not None:
        _turnos_remedies = remedies

_ART = _tz.tzinfo()  # installation timezone (zoneinfo, DST-safe — see tz.py)
                      # (same gotcha as calendar_feed.py: turno_dt is a
                      # timestamptz inserted from a naive one -> ALWAYS
                      # convert before showing it)

# Fallback day/month names (the catalog's own canonical Spanish values —
# `locales/es/date.json`'s `date.day.*`/`date.month_full.*`), passed as the
# `default` argument to `i18n.t_or()` below. This is NOT a second source of
# truth for what a day is called: it is the same "default" every i18n.t_or()
# call site in the repo carries (site_identity.py, patient_web/site_*.py's own
# `_DAYS_FULL`/`_MONTH_FULL` do the same) — a catalog miss degrades to the
# exact Spanish that shipped before extraction, it never invents a new list.
# What used to be a THIRD hardcoded day-name table (duplicating
# site_schedule._DAYS_FULL and dates.mjs's DIAS) is gone: rendering now goes
# through the same `date.day.*`/`date.month_full.*` keys those two already
# fill (landed 2026-08-04 in PR14's date-layer batch — that pass still had
# `_friendly_appointment_time` hardcoded to `i18n.CANONICAL_LANG`, since
# nothing yet stored a language to use instead; this module is the "instead").
# Now with an explicit `lang` — the account's stored preference, since
# neither `_day_name` nor `i18n.active()` can help here (see module docstring).
_DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre"]

_SITE_URL_DEFAULT = "http://127.0.0.1:7700"


def _day_name(index, lang=None):
    return i18n.t_or(f"date.day.{_DAY_KEYS[index]}", _DIAS[index], lang)


def _month_name(month, lang=None):
    return i18n.t_or(f"date.month_full.{month}", _MESES[month - 1], lang)


def _friendly_appointment_time(turno_dt, lang=None):
    """'Martes 14 de julio, 08:00' — no relative terms (Today/Tomorrow): an
    email is read later, relative terms rot (same criterion as
    fechas.js:larga). Renders in `lang` (the account's stored preference),
    reusing the SAME `date.long` catalog template site_schedule._friendly_date
    builds its own sentence from — never concatenating a translated word
    with the formatted date/time (that IS the defect class this batch
    started from): the whole "day n de month" shape, and the final
    "date, time" shape, are each one catalog entry with named variables."""
    if turno_dt is None:
        return ""
    if isinstance(turno_dt, str):
        try:
            turno_dt = datetime.fromisoformat(turno_dt.replace("Z", "+00:00"))
        except ValueError:
            return str(turno_dt)
    if turno_dt.tzinfo is not None:
        turno_dt = turno_dt.astimezone(_ART)
    fecha = i18n.t_or("date.long", "{day} {n} de {month}", lang,
                       day=_day_name(turno_dt.weekday(), lang), n=turno_dt.day,
                       month=_month_name(turno_dt.month, lang))
    return i18n.t_or("mail.date_time", "{date}, {time}", lang,
                      date=fecha, time=turno_dt.strftime("%H:%M"))


def _base_url(handler):
    # Origin pinned by env in prod (site_identity.base_url) — links in emails
    # (approved, waitlist offer) don't come from the client's Host.
    try:
        from app.core import site_identity
        return site_identity.base_url(handler)
    except Exception:  # noqa: BLE001
        return _SITE_URL_DEFAULT


def _service_name(servicio_id):
    try:
        if _turnos_booking_requests is None:
            return str(servicio_id or "")
        return _turnos_booking_requests._service_name(servicio_id)
    except Exception:  # noqa: BLE001
        return str(servicio_id or "")


def _center(centro_slug):
    try:
        from app.patient_web import catalog
        for c in (catalog.centers() or []):
            if isinstance(c, dict) and str(c.get("slug") or c.get("id")) == str(centro_slug):
                return c
    except Exception:  # noqa: BLE001
        pass
    return {}


def _maps_link(centro):
    """The center address's Google Maps URL ('' without an address): the data's
    own maps_url, else the one the patient pages build (the same link)."""
    if not centro.get("address"):
        return ""
    try:
        from app.patient_web import site_centers
        return site_centers._maps_url(centro)
    except Exception:  # noqa: BLE001
        return str(centro.get("maps_url") or "")


def _where(centro, lang):
    """«Dónde: Hospital (Av. Shaw 255).» as (plain-text lines, HTML block): the
    address is a link to its Maps page in the HTML, and the plain text carries
    the same link on its own line (the product owner, 2026-10-29: addresses link to Maps)."""
    direccion = f" ({centro['address']})" if centro.get("address") else ""
    linea = i18n.t_or("mail.location", "Dónde: {name}{address}.", lang,
                      name=centro["name"], address=direccion)
    block = {"type": "p", "text": linea}
    lineas = [linea]
    url = _maps_link(centro)
    if url:
        block["link"] = {"text": centro["address"], "url": url}
        lineas.append(f"{i18n.t_or('mail.link.map', 'Cómo llegar', lang)}: {url}")
    return lineas, block


def _account_user(row):
    """The account that made the request (solicitante_dni: a dependent has no
    account of their own), or None when there is none or it cannot be read."""
    dni_cuenta = row.get("solicitante_dni") or row.get("dni")
    if not dni_cuenta:
        return None
    try:
        return db_accounts.get_user(re.sub(r"\D", "", str(dni_cuenta)))
    except Exception:  # noqa: BLE001
        return None


def _channel_off(user, channel):
    """True only when the account switched `channel` ("email" or "whatsapp")
    off in «Contacto y avisos» (`avisos[channel]` is False). Default = send:
    avisos null or without the key is not an opt-out."""
    avisos = (user or {}).get("avisos")
    return isinstance(avisos, dict) and avisos.get(channel) is False


def _destination_email(row):
    """Always to the account that made the request (solicitante_dni) — a
    dependent has no account/email of their own. No email on the account, or
    email switched off in «Contacto y avisos» -> None (skip)."""
    u = _account_user(row)
    if _channel_off(u, "email"):
        return None
    email = (u or {}).get("email")
    return email.strip() if email else None


def _destination_lang(row):
    """The account's stored language preference (`users.lang`, accounts.py),
    or the canonical language when nothing was ever saved — the ONLY correct
    source for what language a notification renders in (see module
    docstring: no request is bound when any of these fire). Also degrades to
    canonical if the stored value names a language the installation no
    longer has enabled (an admin turned a language back off after some
    accounts had already picked it). Never raises: DB trouble degrades to
    canonical, same fallback contract as the rest of this module."""
    dni_cuenta = row.get("solicitante_dni") or row.get("dni")
    if not dni_cuenta:
        return i18n.CANONICAL_LANG
    try:
        u = db_accounts.get_user(re.sub(r"\D", "", str(dni_cuenta)))
    except Exception:  # noqa: BLE001
        return i18n.CANONICAL_LANG
    lang = (u or {}).get("lang")
    return lang if lang in i18n.enabled_languages() else i18n.CANONICAL_LANG


def _patient_name(row):
    return " ".join(x for x in (row.get("nombre"), row.get("apellido")) if x).strip()


def _greeting(paciente, lang=None):
    if paciente:
        return i18n.t_or("mail.greeting_named", "Hola, {name}.", lang, name=paciente)
    return i18n.t_or("mail.greeting_plain", "Hola.", lang)


def _prep_lines(prep, lang=None):
    """ai_json.preparacion (a list of entries from
    webdata/estudios-preparacion.json) -> plain-text lines, per study. What
    sections a preparation has and what they're called is decided by
    prep_model.py, the same model that renders the web page: the email
    isn't a summary of the site, it IS the site in plain text — prep_model's
    OWN output (resumen/ayuno/secciones/SIN_NADA) is pack CONTENT, not a UI
    string, so it stays out of this module's `mail.*` catalog (it's
    translate.py's domain, not wired to a live page yet — see
    docs/architecture/i18n.md). Only the "Cómo prepararte"/"Para X" headers
    this module itself writes go through the catalog. [] or None -> []
    (nothing to add; the caller doesn't send an empty section)."""
    pm = _turnos_prep_model
    if pm is None:  # best-effort: no prep_model registered -> no prep section
        return []
    entradas = [e for e in (prep or []) if isinstance(e, dict)]
    if not entradas:
        return []
    multi = len(entradas) > 1
    lineas = [i18n.t_or("mail.prep.header", "Cómo prepararte:", lang)]
    for e in entradas:
        nombre = e.get("nombre") or ""
        if multi and nombre:
            lineas.append(i18n.t_or("mail.prep.for_name", "Para {name}:", lang, name=nombre))
        resumen = pm.resumen(e)
        if resumen:
            lineas.append(resumen)
        ayuno = pm.ayuno(e)
        if ayuno["hay"]:
            lineas.append(f'- {ayuno["texto"]}')
        if ayuno["nota"]:
            lineas.append(f'  {ayuno["nota"]}')
        secciones = pm.secciones(e)
        for sec in secciones:
            lineas.append(f'{sec["titulo"]}:')
            for item in sec["items"]:
                lineas.append(f"- {item}")
        if not (secciones or ayuno["hay"] or resumen):
            lineas.append(f"- {pm.SIN_NADA}")
        if multi:
            lineas.append("")  # separator between studies
    while lineas and lineas[-1] == "":
        lineas.pop()
    return lineas


def _prep_blocks(prep, lang=None):
    """Same content as _prep_lines, structured for the HTML layout instead
    of flat '- ' text lines: a bold mini-header per study plus a real
    bullet list (<ul>), so it reads as a scannable list in an inbox instead
    of a wall of dashes. Mirrors _prep_lines section-by-section on purpose —
    same source data (prep_model.py), just two different renderings for the
    two mail formats."""
    pm = _turnos_prep_model
    if pm is None:  # best-effort: no prep_model registered -> no prep section
        return []
    entradas = [e for e in (prep or []) if isinstance(e, dict)]
    if not entradas:
        return []
    multi = len(entradas) > 1
    out = [{"type": "p", "text": i18n.t_or("mail.prep.header", "Cómo prepararte:", lang), "bold": True}]
    for e in entradas:
        nombre = e.get("nombre") or ""
        if multi and nombre:
            out.append({"type": "p", "text": i18n.t_or("mail.prep.for_name", "Para {name}:", lang, name=nombre), "bold": True})
        resumen = pm.resumen(e)
        if resumen:
            out.append({"type": "p", "text": resumen})
        ayuno = pm.ayuno(e)
        items = []
        if ayuno["hay"]:
            items.append(ayuno["texto"] + (f' {ayuno["nota"]}' if ayuno["nota"] else ""))
        secciones = pm.secciones(e)
        for sec in secciones:
            if items:
                out.append({"type": "list", "items": items})
                items = []
            out.append({"type": "p", "text": sec["titulo"], "bold": True})
            items = list(sec["items"])
        if items:
            out.append({"type": "list", "items": items})
        if not (secciones or ayuno["hay"] or resumen):
            out.append({"type": "p", "text": pm.SIN_NADA})
    return out


def _accent():
    """Brand accent hex for the button in HTML emails. A configured custom
    color wins; with none, the active theme's own accent (green "pinamar" /
    vermillion "humanrounds") — same pair branding.py keeps privately for
    its own admin API, mirrored here since editing that file is out of this
    change's scope. test_theme.py pins both copies to the CSS token, so a
    theme recolour can't leave the email button on the old brand again. Best-effort: any failure (DB down, theme unreadable)
    falls back to the pinamar green, never breaks the email."""
    try:
        from app.patient_web import branding
        from app.core import theme
        custom = (branding.current() or {}).get("color")
        if custom:
            return custom
        return {"pinamar": "#0d7d48", "humanrounds": "#874f2e"}.get(theme.current(), "#0d7d48")
    except Exception:  # noqa: BLE001
        return "#0d7d48"


def _brand():
    try:
        from app.core import theme
        return theme.brand_name()
    except Exception:  # noqa: BLE001
        return "Human Rounds"


_FONT_STACK = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _html_block(b):
    t = b.get("type")
    if t == "p":
        weight = "700" if b.get("bold") else "400"
        text = _esc(str(b.get("text") or ""))
        lk = b.get("link") or {}
        if lk.get("text") and lk.get("url") and _esc(str(lk["text"])) in text:
            # a link INSIDE the paragraph (the address → Maps): the accent colour,
            # and the mail client's own default underline (ui-evals eval 8 keeps
            # an explicit underline out of every served file)
            text = text.replace(_esc(str(lk["text"])),
                                '<a href="%s" style="color:%s;">%s</a>'
                                % (_esc(str(lk["url"])), _accent(), _esc(str(lk["text"]))), 1)
        return ('<tr><td style="padding:0 32px 14px;font:%s 15px/1.55 %s;color:#1f1f1f;">%s</td></tr>'
                % (weight, _FONT_STACK, text))
    if t == "list":
        items = "".join('<li style="margin:0 0 6px;">%s</li>' % _esc(str(i))
                         for i in (b.get("items") or []) if str(i).strip())
        if not items:
            return ""
        return ('<tr><td style="padding:0 32px 14px;font:400 15px/1.5 %s;color:#1f1f1f;">'
                '<ul style="margin:0;padding-left:20px;">%s</ul></td></tr>' % (_FONT_STACK, items))
    if t == "button":
        url = b.get("url") or ""
        if not url:
            return ""
        return ('<tr><td style="padding:6px 32px 22px;">'
                '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
                '<td style="border-radius:10px;background:%s;">'
                '<a href="%s" style="display:inline-block;padding:14px 26px;font:700 16px %s;'
                'color:#ffffff;text-decoration:none;border-radius:10px;">%s</a>'
                '</td></tr></table></td></tr>'
                % (_accent(), _esc(url), _FONT_STACK, _esc(str(b.get("label") or ""))))
    if t == "link":
        url = b.get("url") or ""
        if not url:
            return ""
        return ('<tr><td style="padding:0 32px 14px;font:400 15px/1.5 %s;">'
                '<a href="%s" style="color:%s;">%s</a></td></tr>'
                % (_FONT_STACK, _esc(url), _accent(), _esc(str(b.get("label") or ""))))
    if t == "muted":
        return ('<tr><td style="padding:16px 32px 0;font:400 12.5px/1.5 %s;color:#767676;">%s</td></tr>'
                % (_FONT_STACK, _esc(str(b.get("text") or ""))))
    return ""


def _html_layout(title, blocks, lang=None):
    """The ONE HTML email shell (bulletproof: table-based ~560px container,
    inline styles only, system font stack) every migrated email builds its
    content into — one formatter, one place. `blocks` is a list of dicts,
    each {"type": "p"|"list"|"button"|"link"|"muted", ...} (see _html_block
    for the fields each type reads). `lang` sets `<html lang>` and the footer
    sentence; defaults to canonical when the caller doesn't have one yet.
    Never raises: a bad block is dropped, never breaks the send — the
    caller's text part goes out regardless."""
    try:
        lang = lang or i18n.CANONICAL_LANG
        rows = "".join(_html_block(b) for b in (blocks or []) if isinstance(b, dict))
        brand = _esc(_brand())
        titulo = _esc(title)
        pie = _esc(i18n.t_or("mail.footer.auto_sent",
                              "Este mensaje se envió automáticamente; no hace falta responderlo.",
                              lang))
        return (
            f'<!doctype html><html lang="{_esc(lang)}"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{titulo}</title></head>'
            f'<body style="margin:0;padding:24px 12px;background:#f2f2ef;font:15px {_FONT_STACK};">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td align="center">'
            '<table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" '
            'style="max-width:560px;width:100%;background:#ffffff;border-radius:14px;overflow:hidden;">'
            f'<tr><td style="padding:26px 32px 6px;font:700 15px {_FONT_STACK};color:#5a605b;letter-spacing:-.01em;">{brand}</td></tr>'
            f'<tr><td style="padding:2px 32px 20px;"><h1 style="margin:0;font:700 21px/1.3 {_FONT_STACK};color:#1f1f1f;">{titulo}</h1></td></tr>'
            f'{rows}'
            '<tr><td style="height:8px;"></td></tr>'
            '</table>'
            '<table role="presentation" width="560" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;width:100%;">'
            f'<tr><td style="padding:14px 32px 0;font:400 12px/1.5 {_FONT_STACK};color:#8a8f8b;">'
            f'{brand} &middot; {pie}'
            '</td></tr></table>'
            '</td></tr></table></body></html>'
        )
    except Exception:  # noqa: BLE001 — the text part is what must never break
        return None


# Canonical Spanish, kept as module constants (not just inline defaults):
# appointment_confirm.py's already-confirmed page reuses `_ENTREVISTA_CTA_LABEL`
# directly — that page is a real GET a patient's browser opens (unlike the
# emails below, it DOES have a request bound) but hasn't been wired into the
# `i18n.t_or(..., i18n.active())` pipeline yet, so it stays on the plain
# Spanish constant rather than silently breaking on this change.
_ENTREVISTA_LEAD = ("Contestás unas preguntas antes de la consulta y le dejás "
                     "al médico las tuyas: te atiende mejor informado.")
_ENTREVISTA_CTA_LABEL = "Contale al médico cómo te sentís"


def _entrevista_lead(lang=None):
    return i18n.t_or("mail.interview.lead", _ENTREVISTA_LEAD, lang)


def _entrevista_cta_label(lang=None):
    return i18n.t_or("mail.interview.cta_label", _ENTREVISTA_CTA_LABEL, lang)


def _entrevista_cta(row, handler=None):
    """Resolves the pre-consult interview CTA once (its absolute URL), or
    None when it doesn't apply — same criterion the post-booking CTA uses
    (`booking_requests._entrevista_field`: app module on + a known doctor
    whose effective toggle is on, `intake_admin.enabled_for`). Best-effort: the
    booking/approval already persisted before any caller of this runs, so a
    broken intake module can never break the email.

    The URL carries `?t=<patient_token>` (2026-08-03) so the link opens the
    chat directly, no login — `booking_requests._entrevista_patient_token`
    lazily creates the interview for this; a failure there just means the
    link falls back to session-only, same as before this date."""
    try:
        sid = row.get("id")
        if not sid or _turnos_booking_requests is None:
            return None
        entrevista = _turnos_booking_requests._entrevista_field(sid, row.get("medico_asignado_id"))
        if not entrevista:
            return None
        url = entrevista["url"]
        token = _turnos_booking_requests._entrevista_patient_token(sid)
        if token:
            url = f"{url}?t={token}"
        return {"url": f"{_base_url(handler)}{url}"}
    except Exception:  # noqa: BLE001
        return None


def _entrevista_lineas(row, lang, handler=None):
    """Text-part lines inviting the pre-consult interview (received/approved
    emails) — [] when it doesn't apply."""
    cta = _entrevista_cta(row, handler)
    if not cta:
        return []
    return [_entrevista_lead(lang), f"{_entrevista_cta_label(lang)}: {cta['url']}"]


def _entrevista_blocks(row, lang, handler=None):
    """Same CTA as HTML blocks: the lead sentence + the primary button."""
    cta = _entrevista_cta(row, handler)
    if not cta:
        return []
    return [
        {"type": "p", "text": _entrevista_lead(lang)},
        {"type": "button", "label": _entrevista_cta_label(lang), "url": cta["url"]},
    ]


def notify_received(row, handler=None):
    """Sent as soon as the patient submits the request (booking_requests.py:_create),
    with the full detail: service, center, the time preference if one was
    chosen, and how to prepare. The hospital hasn't confirmed anything yet —
    the concrete-appointment notice arrives later via notify_approved."""
    try:
        if not row:
            return
        to = _destination_email(row)
        if not to:
            return
        lang = _destination_lang(row)
        servicio = _service_name(row.get("servicio_id"))
        centro = _center(row.get("centro_slug"))
        paciente = _patient_name(row)
        preferencia = (row.get("preferencia_horario") or "").strip()
        prep = ((row.get("ai_json") or {}).get("preparacion")) or []

        saludo = _greeting(paciente, lang)
        recibimos = i18n.t_or("mail.received.body", "Recibimos tu solicitud de {service}.",
                               lang, service=servicio)
        lineas = [saludo, recibimos]
        blocks = [{"type": "p", "text": saludo}, {"type": "p", "text": recibimos}]
        if centro.get("name"):
            donde_lineas, donde_block = _where(centro, lang)
            lineas.extend(donde_lineas)
            blocks.append(donde_block)
        if preferencia:
            linea = i18n.t_or("mail.received.time_pref", "Horario que elegiste: {time}.",
                               lang, time=preferencia)
            lineas.append(linea)
            blocks.append({"type": "p", "text": linea})
        lineas.extend(_prep_lines(prep, lang))
        blocks.extend(_prep_blocks(prep, lang))
        lineas.extend(_entrevista_lineas(row, lang, handler))
        blocks.extend(_entrevista_blocks(row, lang, handler))
        sigue = i18n.t_or("mail.received.next_step",
                           "El hospital revisa tu solicitud y te confirma el turno por email.", lang)
        lineas.append(sigue)
        blocks.append({"type": "p", "text": sigue})
        url_ms = f"{_base_url(handler)}/my-requests"
        etiqueta_ms = i18n.t_or("mail.link.my_requests", "Todos mis turnos", lang)
        lineas.append(f"{etiqueta_ms}: {url_ms}")
        blocks.append({"type": "link", "label": etiqueta_ms, "url": url_ms})
        asunto = i18n.t_or("mail.received.subject", "Recibimos tu solicitud de {service}",
                            lang, service=servicio)
        mail.send(to, asunto, "\n".join(lineas), html=_html_layout(asunto, blocks, lang))
    except Exception as exc:  # noqa: BLE001 — the request was ALREADY created before this, never break here
        _log_failed("aviso de solicitud recibida", row, log_pii.exc(exc))
        launch_signals.handled("notify_received_failed")


def notify_approved(row, handler=None):
    """The appointment is confirmed: the email to the account, and the
    WhatsApp confirmation to the request's phone — independent of each other
    (no email on the account still sends the WhatsApp, and back)."""
    if not row:
        return
    _email_approved(row, handler)
    try:
        _whatsapp_confirmation(row, _destination_lang(row), handler)
    except Exception as exc:  # noqa: BLE001 — the approve ALREADY persisted before this
        _log_failed("WhatsApp de turno confirmado", row, log_pii.exc(exc))
        launch_signals.handled("notify_approved_failed")


def _email_approved(row, handler=None):
    try:
        to = _destination_email(row)
        if not to:
            return
        lang = _destination_lang(row)
        servicio = _service_name(row.get("servicio_id"))
        centro = _center(row.get("centro_slug"))
        turno = _friendly_appointment_time(row.get("turno_dt"), lang)
        paciente = _patient_name(row)
        medico = _doctor_name(row)
        prep = ((row.get("ai_json") or {}).get("preparacion")) or []

        saludo = _greeting(paciente, lang)
        if turno:
            confirmado = i18n.t_or("mail.approved.confirmed_with_time",
                                    "Tu turno de {service} quedó confirmado para el {time}.",
                                    lang, service=servicio, time=turno)
        else:
            confirmado = i18n.t_or("mail.approved.confirmed_no_time",
                                    "Tu turno de {service} quedó confirmado.", lang, service=servicio)
        lineas = [saludo, confirmado]
        blocks = [{"type": "p", "text": saludo}, {"type": "p", "text": confirmado}]
        # Al calendario justo al lado de la fecha/hora (2026-08-03): un .ics
        # tokenizado por turno, sin sesión — ver _calendar_ics_url.
        ics_url = _calendar_ics_url(row, handler)
        if ics_url:
            etiqueta_cal = i18n.t_or("mail.link.calendar", "Al calendario", lang)
            lineas.append(f"{etiqueta_cal}: {ics_url}")
            blocks.append({"type": "link", "label": etiqueta_cal, "url": ics_url})
        if centro.get("name"):
            donde_lineas, donde_block = _where(centro, lang)
            lineas.extend(donde_lineas)
            blocks.append(donde_block)
        if medico:
            # Same line and key as the day-before reminder (2026-10-28).
            linea = i18n.t_or("mail.reminder.doctor", "Te atiende: {doctor}.", lang, doctor=medico)
            lineas.append(linea)
            blocks.append({"type": "p", "text": linea})
        # The preparation goes IN FULL here (not a link to click): it's what
        # the patient needs to know to go to the appointment, it shouldn't
        # depend on them going into Mis solicitudes to look it up.
        lineas.extend(_prep_lines(prep, lang))
        blocks.extend(_prep_blocks(prep, lang))
        lineas.extend(_entrevista_lineas(row, lang, handler))
        blocks.extend(_entrevista_blocks(row, lang, handler))
        url_ms = f"{_base_url(handler)}/my-requests"
        etiqueta_ms = i18n.t_or("mail.link.my_requests", "Todos mis turnos", lang)
        lineas.append(f"{etiqueta_ms}: {url_ms}")
        blocks.append({"type": "link", "label": etiqueta_ms, "url": url_ms})
        asunto = i18n.t_or("mail.approved.subject", "Tu turno de {service} está confirmado",
                            lang, service=servicio)
        mail.send(to, asunto, "\n".join(lineas), html=_html_layout(asunto, blocks, lang))
    except Exception as exc:  # noqa: BLE001 — the approve ALREADY persisted before this, never break here
        _log_failed("email de turno confirmado", row, log_pii.exc(exc))
        launch_signals.handled("notify_approved_failed")


def notify_reminder(row, handler=None):
    """One day before an approved appointment (reminders.py:tick). Never
    breaks the reminder run (same contract as the rest).

    Answers one of `mail.deliver`'s three words (the product owner's hub, 2026-11-10):
    `mail.SENT` when a channel accepted it (one is enough — a retry would
    repeat the other); `mail.FAILED` when something failed: it could not be
    built, or every channel that was tried refused it — the sweep gives its
    claim back and tries again later; `mail.SKIPPED` when there was nothing
    to send (no address, no phone, mail or WhatsApp off by configuration —
    the mail's reason is `mail.deliver`'s own answer) — not a failure, and
    not a reminder sent either."""
    outcomes = []
    try:
        if not row:
            return mail.SKIPPED
        lang = _destination_lang(row)
        servicio = _service_name(row.get("servicio_id"))
        centro = _center(row.get("centro_slug"))
        turno = _friendly_appointment_time(row.get("turno_dt"), lang)
        paciente = _patient_name(row)
        medico = _doctor_name(row)
        prep = ((row.get("ai_json") or {}).get("preparacion")) or []

        to = _destination_email(row)
        if to:
            saludo = _greeting(paciente, lang)
            if turno:
                cuando_txt = i18n.t_or("mail.reminder.body_with_time",
                                       "Mañana, {time}, tenés tu turno de {service}.",
                                       lang, time=turno, service=servicio)
            else:
                cuando_txt = i18n.t_or("mail.reminder.body_no_time",
                                       "Mañana tenés tu turno de {service}.", lang, service=servicio)
            lineas = [saludo, cuando_txt]
            blocks = [{"type": "p", "text": saludo}, {"type": "p", "text": cuando_txt}]
            # Al calendario justo al lado de la fecha/hora, igual que en el
            # email de aprobación (2026-08-03) — ver _calendar_ics_url.
            ics_url = _calendar_ics_url(row, handler)
            if ics_url:
                etiqueta_cal = i18n.t_or("mail.link.calendar", "Al calendario", lang)
                lineas.append(f"{etiqueta_cal}: {ics_url}")
                blocks.append({"type": "link", "label": etiqueta_cal, "url": ics_url})
            if centro.get("name"):
                donde_lineas, donde_block = _where(centro, lang)
                lineas.extend(donde_lineas)
                blocks.append(donde_block)
            if medico:
                linea = i18n.t_or("mail.reminder.doctor", "Te atiende: {doctor}.", lang, doctor=medico)
                lineas.append(linea)
                blocks.append({"type": "p", "text": linea})
            lineas.extend(_prep_lines(prep, lang))
            blocks.extend(_prep_blocks(prep, lang))
            llevar = i18n.t_or("mail.reminder.bring",
                                "Llevá tu DNI (y la orden médica, si tu turno la necesita).", lang)
            lineas.append(llevar)
            blocks.append({"type": "p", "text": llevar})
            confirmar = _confirm_url(row, handler)
            if confirmar:
                pregunta = i18n.t_or(
                    "mail.reminder.confirm_question",
                    "¿Vas a poder venir? Contestanos con un clic: nos ordena el día, y si no "
                    "podés el turno queda libre para otra persona.", lang)
                boton_si = i18n.t_or("mail.reminder.confirm_yes", "Sí, voy a ir", lang)
                link_no = i18n.t_or("mail.reminder.confirm_no", "No voy a poder ir", lang)
                lineas.append(pregunta)
                lineas.append(f"{boton_si}: {confirmar}?r=si")
                lineas.append(f"{link_no}: {confirmar}?r=no")
                blocks.append({"type": "p", "text": pregunta})
                blocks.append({"type": "button", "label": boton_si, "url": f"{confirmar}?r=si"})
                blocks.append({"type": "link", "label": link_no, "url": f"{confirmar}?r=no"})
            url_ms = f"{_base_url(handler)}/my-requests"
            etiqueta_ms = i18n.t_or("mail.link.my_requests", "Todos mis turnos", lang)
            lineas.append(f"{etiqueta_ms}: {url_ms}")
            blocks.append({"type": "link", "label": etiqueta_ms, "url": url_ms})
            asunto = i18n.t_or("mail.reminder.subject", "Mañana: tu turno de {service}",
                                lang, service=servicio)
            outcomes.append(mail.deliver(to, asunto, "\n".join(lineas), html=_html_layout(asunto, blocks, lang)))

        outcomes.append(_whatsapp_reminder(row, servicio, centro, turno, paciente, lang))
    except Exception as exc:  # noqa: BLE001 — the sweep goes on; reminders.tick gives the claim back
        _log_failed("recordatorio del dia antes", row, log_pii.exc(exc))
        outcomes.append(mail.FAILED)
    if mail.SENT in outcomes:
        return mail.SENT
    return mail.FAILED if mail.FAILED in outcomes else mail.SKIPPED


def _calendar_ics_url(row, handler=None):
    """Absolute URL of the "Al calendario" .ics download for THIS
    appointment (2026-08-03) — reuses booking_requests.confirm_token
    (db_booking.booking_request_confirm_token, lazy-mint, same idempotent pattern as
    _confirm_url just below: reforwarding the email must not break a link
    the patient already has). Unlike _confirm_url this one does NOT skip
    once the patient already answered "sí"/"no" — the calendar link stays
    useful for the whole life of the appointment, not just until the
    day-before question is settled. None when there's no concrete time yet
    or the token can't be minted — best-effort, never breaks the email; the
    route itself (calendar_feed._token_ics) re-checks the LIVE estado at
    download time, so a cancellation after the email went out just 404s."""
    try:
        if not row.get("turno_dt"):
            return None
        sid = row.get("id")
        if not sid:
            return None
        token = db_booking.booking_request_confirm_token(sid)
        if not token:
            return None
        return f"{_base_url(handler)}/appointment/{token}.ics"
    except Exception:  # noqa: BLE001
        return None


def _confirm_url(row, handler=None):
    """Absolute URL of the one-click "¿vas a poder venir?" page for this
    request, or None when it doesn't apply (2026-08-02).

    Until today the question only went out by WhatsApp, and the institutional
    Meta number is still in process — so nobody was actually being asked. The
    token IS the authentication (same model as the waitlist offer): the person
    opening the reminder on their phone has no session, and making them log in
    to say "sí" is exactly the friction that gets no answer.

    None when the request already carries an answer (asking again after the
    patient replied is noise) or when the token can't be created —
    best-effort, the reminder goes out either way."""
    try:
        if (row.get("confirmacion") or "").strip():
            return None
        sid = row.get("id")
        if not sid:
            return None
        token = db_booking.booking_request_confirm_token(sid)
        if not token:
            return None
        return f"{_base_url(handler)}/appointment/confirm/{token}"
    except Exception:  # noqa: BLE001
        return None


def _destination_phone(row):
    """The request's contact phone (digits only), or None if it doesn't look
    like a phone number (the field also accepts emails) or the account that
    made the request switched WhatsApp off in «Contacto y avisos»."""
    tel = _ids.digits(row.get("contacto"))
    if len(tel) < 8 or _channel_off(_account_user(row), "whatsapp"):
        return None
    return tel


def _interview_pending(row, handler=None):
    """The pre-consult interview CTA for this row, only when it's still
    ACTIONABLE right now: intake enabled for the request's doctor (never
    with no doctor known — same rule as `_entrevista_cta`) AND no completed
    interview yet
    (`interviews.status='done'`, by request_id — `db_clinical.interview_for_request`
    already exists for the doctor confirm/day-queue surfaces, reused as-is
    per the "one query helper, not duplicated in modules/intake" rule).
    None when it doesn't apply. Best-effort: never breaks the caller."""
    cta = _entrevista_cta(row, handler)
    if not cta:
        return None
    try:
        sid = row.get("id")
        iv = db_clinical.interview_for_request(sid) if sid else None
        if iv and iv.get("status") == "done":
            return None
    except Exception:  # noqa: BLE001
        pass
    return cta


def _doctor_name(row):
    """Who sees the patient: the catalog's current name behind
    `medico_asignado_id` (2026-10-23 — the id is the doctor; a renamed doctor
    reads the same in every email), else the stored name (legacy rows,
    doctors outside the catalog). '' when nobody is assigned yet."""
    nombre = (row.get("medico_asignado") or "").strip()
    mid = row.get("medico_asignado_id")
    if not mid:
        return nombre
    try:
        from app.patient_web import catalog
        for d in (catalog.doctors() or []):
            if isinstance(d, dict) and str(d.get("id")) == str(mid) and d.get("nombre"):
                return str(d["nombre"]).strip()
    except Exception:  # noqa: BLE001
        pass
    return nombre


def _needs_referral(row):
    """True when the appointment came with (or its service asks for) a
    referral — the patient has to bring the paper."""
    if row.get("orden_path") or row.get("orden_mime"):
        return True
    try:
        from app.patient_web import catalog
        srv = catalog.service_by_id(row.get("servicio_id")) or {}
        return "orden" in str(srv.get("modo") or "")
    except Exception:  # noqa: BLE001
        return False


def _wa_bring(row, prep, lang):
    """'Qué llevar' as one line: the DNI always, the referral when the
    appointment has one, and whatever the preparation says to bring."""
    llevar = [i18n.t_or("mail.whatsapp.bring_dni", "Tu DNI", lang)]
    orden = _needs_referral(row)
    if orden:
        llevar.append(i18n.t_or("mail.whatsapp.bring_referral", "la orden médica", lang))
    pm = _turnos_prep_model
    if pm is not None:
        vistos = set()
        for e in (prep or []):
            if not isinstance(e, dict):
                continue
            for it in pm._field_list(e, "traer"):
                clave = pm.normalize(it).rstrip(". ")
                # The DNI is always first and the referral already listed:
                # the preparation saying them again is not a second item.
                if clave in vistos or "dni" in clave.split() or (orden and "orden" in clave):
                    continue
                vistos.add(clave)
                item = it.rstrip(". ")
                llevar.append(item[:1].lower() + item[1:] if item[1:2].islower() else item)
    if len(llevar) == 1:
        return llevar[0]
    return i18n.t_or("mail.whatsapp.bring_list", "{items} y {last}", lang,
                     items=", ".join(llevar[:-1]), last=llevar[-1])


def _wa_prep(prep, lang):
    """'Preparación' as one line: fasting and the before-coming steps."""
    pm = _turnos_prep_model
    partes = []
    if pm is not None:
        for e in (prep or []):
            if not isinstance(e, dict):
                continue
            ay = pm.ayuno(e)
            if ay["hay"]:
                partes.append(ay["texto"])
            partes.extend(p.rstrip(".") + "." for p in pm.antes(e))
    if not partes:
        return i18n.t_or("mail.whatsapp.no_prep", "No necesita preparación especial.", lang)
    return " ".join(partes)


def _wa_fields(row, lang, handler=None):
    """The appointment as the eight values both WhatsApp templates carry, in
    template order: patient, service, when, where, doctor, what to bring,
    preparation, link to cancel or change it. Never an empty value — Meta
    rejects a template with an empty parameter."""
    servicio = _service_name(row.get("servicio_id"))
    centro = _center(row.get("centro_slug"))
    donde = centro.get("name") or ""
    if donde and centro.get("address"):
        donde = f"{donde} ({centro['address']})"
    prep = ((row.get("ai_json") or {}).get("preparacion")) or []
    return [
        _patient_name(row) or i18n.t_or("mail.whatsapp.no_name", "qué tal", lang),
        servicio,
        _friendly_appointment_time(row.get("turno_dt"), lang)
        or i18n.t_or("mail.whatsapp.no_time", "a confirmar", lang),
        donde or i18n.t_or("mail.whatsapp.no_center", "a confirmar", lang),
        _doctor_name(row)
        or i18n.t_or("mail.whatsapp.no_doctor", "el equipo de {service}", lang, service=servicio),
        _wa_bring(row, prep, lang),
        _wa_prep(prep, lang),
        f"{_base_url(handler)}/my-requests",
    ]


def _wa_text(fields, lead, closing, lang):
    """Free-text version of a template (WAHA in development, or Meta inside
    a window the patient opened): the same lines the template carries."""
    paciente, _servicio, cuando, donde, medico, llevar, prep, link = fields
    lineas = [i18n.t_or("mail.greeting_named", "Hola, {name}.", lang, name=paciente) + " " + lead, ""]
    for clave, defecto, valor in (
            ("when", "Cuándo", cuando), ("where", "Dónde", donde),
            ("doctor", "Te atiende", medico), ("bring", "Qué llevar", llevar),
            ("prep", "Preparación", prep)):
        etiqueta = i18n.t_or(f"mail.whatsapp.label_{clave}", defecto, lang)
        lineas.append(f"*{etiqueta}:* {valor}")
    lineas += ["", i18n.t_or("mail.whatsapp.change_link",
                             "Para cancelar o cambiar el turno: {url}", lang, url=link),
               "", closing]
    return "\n".join(lineas)


def _whatsapp_confirmation(row, lang, handler=None):
    """The appointment is confirmed (auto-confirmed at booking, approved by
    the desk, created by the desk or rescheduled): one WhatsApp with every
    detail — who, when, where, what to bring — and the way to cancel or
    change it (the product owner, 2026-10-21, q708829). Hospital-initiated, so it goes as
    the approved `turnos_confirmacion` template and only when the
    installation allows it (`otp_send.notificacion_permitida`). Best-effort:
    the approval already persisted."""
    try:
        if not row.get("turno_dt"):
            return
        tel = _destination_phone(row)
        if not tel:
            return
        from app.notifications import otp_send
        if not otp_send.notificacion_permitida(tel):
            return
        fields = _wa_fields(row, lang, handler)
        texto = _wa_text(
            fields,
            i18n.t_or("mail.whatsapp.confirmed_lead", "Tu turno de {service} quedó confirmado.",
                      lang, service=fields[1]),
            i18n.t_or("mail.whatsapp.confirmed_closing",
                      "Si no vas a poder venir, cancelalo desde ese link así el turno lo "
                      "aprovecha otra persona.", lang),
            lang)
        otp_send.enviar_notificacion(tel, texto, otp_send.PLANTILLA_CONFIRMACION, fields)
    except Exception as exc:  # noqa: BLE001 — the approval already persisted
        _log_failed("WhatsApp de turno confirmado", row, log_pii.exc(exc))
        launch_signals.handled("notify_approved_failed")


def _whatsapp_reminder(row, servicio, centro, turno, paciente, lang):
    """The day-before reminder also goes by WhatsApp, asking for a reply
    (hospital meeting 2026-07-24: "if a message reaches them the day before,
    that'd be great" — and the historical no-show rate runs around 30%,
    18-20% here). The SÍ/NO reply (typed, or one of the template's two quick
    replies) comes back through whatsapp_chat's webhook into
    `confirmations.respond`. Since 2026-10-21 it carries every detail of the
    appointment, like the confirmation, and goes as the approved
    `turnos_recordatorio` template — a free text the day before reaches only
    someone who wrote in the last 24h. Gated like every hospital-initiated
    notice (`otp_send.notificacion_permitida`: off unless TURNOS_WA_NOTIFY).
    The pre-consult interview invite stays a free-text line: a template
    can't grow a conditional line, so it rides only in the free-text
    version. Returns "sent", "skipped" (no phone, notices off, no provider,
    outbound kill switch) or "failed" — what notify_reminder decides a retry on."""
    try:
        from app.notifications import otp_send
        tel = _destination_phone(row)
        if not tel or not otp_send.notificacion_permitida(tel):
            return "skipped"
        fields = _wa_fields(row, lang)
        cierre = i18n.t_or(
            "mail.whatsapp.confirm_question",
            "¿Vas a poder venir? Respondé SÍ para confirmar, o NO para liberar el turno "
            "y que lo aproveche otra persona.", lang)
        cta = _interview_pending(row)
        if cta:
            cierre += "\n" + i18n.t_or("mail.whatsapp.interview_cta",
                                       "Antes de venir, contanos cómo te sentís: {url}",
                                       lang, url=cta["url"])
        texto = _wa_text(
            fields,
            i18n.t_or("mail.whatsapp.reminder_lead",
                      "Te recordamos que mañana tenés turno de {service}.",
                      lang, service=fields[1]),
            cierre, lang)
        if otp_send.enviar_notificacion(tel, texto, otp_send.PLANTILLA_RECORDATORIO, fields):
            return "sent"
        from app.core import outbound_guard  # the outbound kill switch is configuration, like no provider
        return "failed" if otp_send.wa_provider() and not outbound_guard.armed() else "skipped"
    except Exception as exc:  # noqa: BLE001
        _log_failed("WhatsApp del recordatorio", row, log_pii.exc(exc))
        return "failed"


def notify_interview_reminder(row, handler=None):
    """SEPARATE day-before email (deliverable 3, 2026-08-01): a dedicated
    nudge to complete the pre-consult interview, independent from
    notify_reminder's appointment reminder — reminders.py:tick_interview()
    calls this on its OWN sweep, gated by the `interview_reminder_at` column
    (never `reminder_at`: a hospital can run one reminder without the
    other). Self-gates via `_interview_pending`: intake off for this
    doctor, or the interview already done, and this is a silent no-op.
    Returns False when the invitation could not be built or the provider
    refused it — the sweep then gives its claim back for a retry, as for the
    appointment reminder (the product owner's hub, 2026-11-10); True otherwise (sent, or
    nothing to send, or mail off by configuration: `mail.deliver`'s reason)."""
    try:
        if not row:
            return True
        cta = _interview_pending(row, handler)
        if not cta:
            return True
        to = _destination_email(row)
        if not to:
            return True
        lang = _destination_lang(row)
        servicio = _service_name(row.get("servicio_id"))
        turno = _friendly_appointment_time(row.get("turno_dt"), lang)
        paciente = _patient_name(row)

        saludo = _greeting(paciente, lang)
        if turno:
            cuando_txt = i18n.t_or("mail.reminder.body_with_time",
                                   "Mañana, {time}, tenés tu turno de {service}.",
                                   lang, time=turno, service=servicio)
        else:
            cuando_txt = i18n.t_or("mail.reminder.body_no_time",
                                   "Mañana tenés tu turno de {service}.", lang, service=servicio)
        lead = _entrevista_lead(lang)
        cta_label = _entrevista_cta_label(lang)
        lineas = [saludo, cuando_txt, lead, f"{cta_label}: {cta['url']}"]
        blocks = [
            {"type": "p", "text": saludo},
            {"type": "p", "text": cuando_txt},
            {"type": "p", "text": lead},
            {"type": "button", "label": cta_label, "url": cta["url"]},
        ]
        asunto = i18n.t_or("mail.interview_reminder.subject",
                            "Mañana es tu turno — contale al médico cómo te sentís", lang)
        return mail.deliver(to, asunto, "\n".join(lineas), html=_html_layout(asunto, blocks, lang)) != mail.FAILED
    except Exception as exc:  # noqa: BLE001 — the sweep goes on; reminders.tick_interview gives the claim back
        _log_failed("invitacion a la entrevista previa", row, log_pii.exc(exc))
        return False


def notify_offer(cand, slot_dt, token, handler=None):
    """A sooner appointment opened up and it's this waitlist patient's turn.
    The link with the token reserves the spot within the window. Never
    breaks the offer run (same contract as the rest)."""
    try:
        to = (cand.get("email") or "").strip() or _destination_email(cand)
        if not to:
            return
        lang = _destination_lang(cand)
        servicio = _service_name(cand.get("servicio_id"))
        centro = _center(cand.get("centro_slug"))
        cuando = _friendly_appointment_time(slot_dt, lang)
        url = f"{_base_url(handler)}/appointment/offer/{token}"
        saludo = _greeting("", lang)
        if cuando:
            liberado = i18n.t_or("mail.offer.body_with_time",
                                 "Se liberó un turno de {service} para el {time}.",
                                 lang, service=servicio, time=cuando)
        else:
            liberado = i18n.t_or("mail.offer.body_no_time",
                                 "Se liberó un turno de {service}.", lang, service=servicio)
        lineas = [saludo, liberado]
        blocks = [{"type": "p", "text": saludo}, {"type": "p", "text": liberado}]
        if centro.get("name"):
            linea = i18n.t_or("mail.location", "Dónde: {name}{address}.", lang,
                               name=centro["name"], address="")
            lineas.append(linea)
            blocks.append({"type": "p", "text": linea})
        cta_label = i18n.t_or("mail.offer.cta_label", "Tomar este turno", lang)
        lineas.append(i18n.t_or("mail.offer.claim_text",
                                 "Por 5 minutos este turno es tuyo, garantizado. Reservalo acá: {url}",
                                 lang, url=url))
        blocks.append({"type": "button", "label": cta_label, "url": url})
        siguiente = i18n.t_or("mail.offer.expiry_note",
                               "Pasados los 5 minutos se ofrece a la siguiente persona de la lista.", lang)
        lineas.append(siguiente)
        blocks.append({"type": "muted", "text": siguiente})
        asunto = i18n.t_or("mail.offer.subject", "Se liberó un turno más cercano de {service}",
                            lang, service=servicio)
        mail.send(to, asunto, "\n".join(lineas), html=_html_layout(asunto, blocks, lang))
    except Exception:  # noqa: BLE001
        pass


def notify_cancelled(row, handler=None):
    """The desk cancelled the appointment (the reference-deployment counterpart, 2026-10-19). One plain line
    that it is cancelled by the centre (the same wording as «Cancelada por el
    centro» on the account page — the product owner, 2026-10-26), and the way back: request
    another one from the same service. The patient who asked the desk to cancel
    reads the same email."""
    try:
        if not row:
            return
        to = _destination_email(row)
        if not to:
            return
        lang = _destination_lang(row)
        servicio = _service_name(row.get("servicio_id"))
        turno = _friendly_appointment_time(row.get("turno_dt"), lang)
        saludo = _greeting(_patient_name(row), lang)
        if turno:
            cancelado = i18n.t_or("mail.cancelled.body_with_time",
                                   "Tu turno de {service} del {time} fue cancelado por el centro.",
                                   lang, service=servicio, time=turno)
        else:
            cancelado = i18n.t_or("mail.cancelled.body", "Tu solicitud de {service} fue cancelada por el centro.",
                                   lang, service=servicio)
        lineas = [saludo, cancelado]
        blocks = [{"type": "p", "text": saludo}, {"type": "p", "text": cancelado}]
        if row.get("servicio_id"):
            url_srv = f"{_base_url(handler)}/service/{row['servicio_id']}"
            etiqueta = i18n.t_or("mail.cancelled.request_again", "Pedir otro turno", lang)
            lineas.append(f"{etiqueta}: {url_srv}")
            blocks.append({"type": "button", "label": etiqueta, "url": url_srv})
        url_ms = f"{_base_url(handler)}/my-requests"
        etiqueta_ms = i18n.t_or("mail.link.my_requests", "Todos mis turnos", lang)
        lineas.append(f"{etiqueta_ms}: {url_ms}")
        blocks.append({"type": "link", "label": etiqueta_ms, "url": url_ms})
        asunto = i18n.t_or("mail.cancelled.subject", "Se canceló tu turno de {service}",
                            lang, service=servicio)
        mail.send(to, asunto, "\n".join(lineas), html=_html_layout(asunto, blocks, lang))
    except Exception as exc:  # noqa: BLE001 — the cancel ALREADY persisted before this, never break here
        _log_failed("aviso de turno cancelado", row, log_pii.exc(exc))
        launch_signals.handled("notify_cancelled_failed")


def notify_rejected(row, handler=None):
    """The rejection is told backwards from an error: WHAT TO DO first, the
    why after (the product owner, 2026-07-27: "if something is off, send the solution to
    the patients, not errors"). remedies.remedy() translates the cause into
    the next step with open slots and deep links — an expired referral goes
    out with three General Medicine times a click away, not with the news
    that it expired. With no remedy for that cause, the usual plain notice
    stays.

    `remedies.remedy()`/`email_lines()`/`whatsapp_text()` take their own
    `lang` (merged 2026-08-04, PR7) — passed the SAME account-stored
    preference `_destination_lang()` resolves here, so the remedy's own
    copy (`rem["titulo"]`/`rem["detalle"]`/each action's `texto`) renders in
    the same language as the sentences around it instead of a half-Spanish,
    half-English email."""
    try:
        if not row:
            return
        lang = _destination_lang(row)
        servicio = _service_name(row.get("servicio_id"))
        paciente = _patient_name(row)
        motivo = row.get("motivo_rechazo") or ""

        rem = None
        try:
            if _turnos_remedies is not None:
                rem = _turnos_remedies.remedy(row, motivo, handler, lang=lang)
        except Exception:  # noqa: BLE001
            rem = None

        to = _destination_email(row)
        if to:
            saludo = _greeting(paciente, lang)
            no_aprobada = i18n.t_or("mail.rejected.body", "Tu solicitud de {service} no pudo aprobarse.",
                                     lang, service=servicio)
            lineas = [saludo, no_aprobada]
            blocks = [{"type": "p", "text": saludo}, {"type": "p", "text": no_aprobada}]
            if motivo:
                linea = i18n.t_or("mail.rejected.reason", "Motivo: {reason}", lang, reason=motivo)
                lineas.append(linea)
                blocks.append({"type": "p", "text": linea})
            if rem and _turnos_remedies is not None:
                lineas.extend(_turnos_remedies.email_lines(rem, lang=lang))
                blocks.append({"type": "p", "text": f'{rem["titulo"]}:', "bold": True})
                if rem.get("detalle"):
                    blocks.append({"type": "p", "text": rem["detalle"]})
                acciones = rem.get("acciones") or []
                if acciones:
                    blocks.append({"type": "button", "label": acciones[0]["texto"], "url": acciones[0]["url"]})
                    for a in acciones[1:]:
                        blocks.append({"type": "link", "label": a["texto"], "url": a["url"]})
            else:
                otra = i18n.t_or("mail.rejected.retry",
                                  "Podés volver a solicitar el turno corrigiendo lo que haga falta.", lang)
                lineas.append(otra)
                blocks.append({"type": "p", "text": otra})
            lineas.append("")
            url_ms = f"{_base_url(handler)}/my-requests"
            etiqueta_mi = i18n.t_or("mail.rejected.more_info", "Más información en Mis turnos", lang)
            lineas.append(f"{etiqueta_mi}: {url_ms}")
            blocks.append({"type": "link", "label": etiqueta_mi, "url": url_ms})
            # The subject line also carries the way out, not the problem:
            # it's the only thing that gets read in the phone's inbox.
            if rem:
                asunto = i18n.t_or("mail.rejected.subject_with_remedy", "{title} — tu solicitud de {service}",
                                    lang, title=rem["titulo"], service=servicio)
            else:
                asunto = i18n.t_or("mail.rejected.subject", "Tu solicitud de {service} no fue aprobada",
                                    lang, service=servicio)
            mail.send(to, asunto, "\n".join(lineas), html=_html_layout(asunto, blocks, lang))

        _whatsapp_rejection(row, rem, paciente, servicio, lang)
    except Exception as exc:  # noqa: BLE001 — the rejection ALREADY persisted before this, never break here
        _log_failed("aviso de solicitud rechazada", row, log_pii.exc(exc))
        launch_signals.handled("notify_rejected_failed")


def _whatsapp_rejection(row, rem, paciente, servicio, lang=None):
    """The solution also goes by WhatsApp: it's the channel people actually
    read (the hospital's email can take days to open, and the alternative
    appointment fills up). Only when there IS a remedy — with no concrete
    way out, a WhatsApp that only delivers bad news is noise; the email is
    enough for that. Hospital-initiated, so gated like the confirmation and
    the reminder (`otp_send.notificacion_permitida`); free text only — there
    is no approved template for it yet, so on Meta it reaches only someone
    with an open 24h window. Best-effort: never raises."""
    try:
        if not rem:
            return
        from app.notifications import otp_send
        tel = _destination_phone(row)
        if not tel or not otp_send.notificacion_permitida(tel):
            return
        if _turnos_remedies is None:
            return
        texto = _turnos_remedies.whatsapp_text(rem, paciente, servicio, lang=lang)
        if texto:
            otp_send.enviar_notificacion(tel, texto)
    except Exception as exc:  # noqa: BLE001 — the rejection already persisted
        _log_failed("WhatsApp de la solucion al rechazo", row, log_pii.exc(exc))
        launch_signals.handled("notify_rejected_failed")
