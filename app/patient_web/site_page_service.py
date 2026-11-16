"""patient_web/site_page_service.py — The service pages of the public site.

The appointment page and the service page, the booking wizard steps and the information request.

Split out of the old `patient_web/patient_site.py` (TASK-32). What lives in another domain module is reached
through that module (`site_catalog._services()`), looked up at CALL time, so a test patching
`site_catalog._services` patches what this module uses too. The modules form layers: an import never
points back up (`tests/test_patient_site_domain_modules.py`).
"""

import urllib.parse

from app.core import i18n
from app.core import site_identity
from app.core import static_fingerprint
from app.core import theme
from app.modules.turnos import schedule_policy

from app.patient_web import site_availability
from app.patient_web import site_base
from app.patient_web import site_catalog
from app.patient_web import site_centers
from app.patient_web import site_doctors
from app.patient_web import site_listing
from app.patient_web import site_preparation
from app.patient_web import site_shell
from app.patient_web import site_studies


def _estimated_steps(modo, query_string="", current_user=None):
    """Steps this visitor is expected to actually see, in interaction order.

    Query params resolve order/time before JavaScript starts and the already
    resolved session user controls the account step. The client can still
    reinsert a step if that optimistic estimate becomes stale.
    """
    qs = urllib.parse.parse_qs(query_string or "")
    tiene_slot = bool((qs.get("slot") or [""])[0])
    tiene_orden = bool((qs.get("order") or [""])[0])
    elegir_primero = modo == "solicitud_con_orden" and (
        (qs.get("slots") or [""])[0] == "1")
    tiene_cuenta = bool(current_user)

    pasos = []
    if modo == "solicitud_con_orden":
        if elegir_primero:
            if not tiene_slot:
                pasos.append("horario")
            if not tiene_orden:
                pasos.append("orden")
        else:
            if not tiene_orden:
                pasos.append("orden")
            if not tiene_slot:
                pasos.append("horario")
    elif modo == "solicitud" and not tiene_slot:
        pasos.append("horario")
    if not tiene_cuenta:
        pasos.append("cuenta")
    pasos.append("confirm")
    return pasos


def _h1_wizard(modo, query_string="", current_user=None):
    """Wizard title on the FIRST paint.

    Arriving with the appointment already chosen (?slot=, the button from
    the listings and the band) means the "When" step is already resolved:
    the wizard only has to confirm who you are and lands on Confirmá — or on
    Autorizá if there's no session. We know that HERE, so the h1 comes out
    already written for that step and doesn't need correcting once the
    first fetch answers (the product owner, 2026-07-27: two screens used to show for a
    fraction of a second before the right one). The rare case — that it was
    just taken — is fixed by service.js along with the notice explaining why."""
    base = (i18n.t_or("page.wizard.pick_slot", "Elegí un turno", i18n.active())
            if modo == "solicitud"
            else i18n.t_or("page.wizard.upload_order", "Subí tu orden", i18n.active()))
    qs = urllib.parse.parse_qs(query_string or "")
    if not (qs.get("slot") or [""])[0]:
        return base
    if modo != "solicitud" and not (qs.get("order") or [""])[0]:
        return base  # with a referral needed and none preloaded, step 1 is still uploading it
    return (i18n.t_or("page.wizard.confirm", "Confirmá tu solicitud", i18n.active())
            if current_user else i18n.t_or("page.wizard.authorize", "Autorizá", i18n.active()))


_WZ_CAMERA_ICON = ('<svg width="40" height="40" viewBox="0 0 24 24" fill="none" '
                   'stroke="currentColor" stroke-width="1.8" stroke-linecap="round" '
                   'stroke-linejoin="round" aria-hidden="true"><path d="M14.5 4h-5L8 6H4a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1h-4l-1.5-2Z"></path>'
                   '<circle cx="12" cy="13" r="3.6"></circle></svg>')
_WZ_CHEVRON = ('<svg width="22" height="22" viewBox="0 0 24 24" fill="none" '
               'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
               'stroke-linejoin="round" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>')


def _first_wizard_step(modo, pasos=None):
    """Server-owned first screen for a user-visible booking step.

    Availability stays client/API-owned. The server only paints the known
    first task and shared progress structure, avoiding a blank host while
    deferred canonical modules load.
    """
    pasos = pasos or _estimated_steps(modo)
    if not pasos:
        return "", "", "", ""
    items = "".join(
        f'<li class="wz-st{" on" if i == 1 else ""}"><span class="wz-st-n">{i}</span></li>'
        for i in range(1, len(pasos) + 1)
    )
    lang = i18n.active()
    stepper = (
        f'<ol class="wz-stepper" aria-label="{site_base._esc(i18n.t_or("service.step.aria", "Paso {n} de {total}", lang, n=1, total=len(pasos)))}">{items}</ol>'
        if len(pasos) > 1 else ""
    )
    if modo == "solicitud" and pasos[0] == "horario":
        sub = (i18n.t_or("service.slots.pick_lede", "Elegí el turno que mejor te queda. ", lang)
               + i18n.t_or("service.slots.pick_note_account", "Lo pedís con tu cuenta y el hospital te lo confirma.", lang))
        loading = i18n.t_or("service.slots.loading", "Buscando turnos disponibles…", lang)
        return stepper, sub, (
            '<div class="sec svc-first-schedule" aria-live="polite">'
            '<div class="wz-card"><div class="wz-spin"><i></i> '
            + site_base._esc(loading) + "</div></div></div>"
        ), "svc-form-host--schedule-boot"
    if modo != "solicitud_con_orden" or pasos[0] != "orden":
        return "", "", "", ""
    return stepper, i18n.t("wizard.upload.sub", i18n.active()), f'''<div class="sec"><p data-ai-notice="order" data-first-paint="1" style="font-size:16px;color:var(--text);">{site_base._esc(i18n.t_or("ai.notice.order", "La inteligencia artificial lee tu orden. La imagen completa se envía al proveedor; el equipo revisa la solicitud.", i18n.active()))}</p><div class="wz-uploadzone" id="wz-upzone" role="button" tabindex="0" aria-label="Subir la orden">
      <input id="wz-file" type="file" accept="image/*,.pdf" style="display:none" data-first-paint="1">
      <div class="wz-cam">{_WZ_CAMERA_ICON}</div>
      <div class="wz-up-h"><span class="wz-up-touch">{site_base._esc(i18n.t_or("upload.zone.heading", "Tomá una foto de la orden", i18n.active()))}</span><span class="wz-up-desk">{site_base._esc(i18n.t_or("upload.zone.heading_file", "Elegí la foto o el PDF de la orden", i18n.active()))}</span></div>
      <div class="wz-up-sub">{site_base._esc(i18n.t_or("upload.zone.sub", "o elegí un archivo: foto o PDF", i18n.active()))}</div>
      <div class="wz-up-paste">{site_base._esc(i18n.t_or("upload.zone.paste", "También podés pegarla con {keys}", i18n.active(), keys="Ctrl/⌘V"))}</div>
    </div>
    <div id="wz-ts" style="margin-top:16px;display:none;"></div>
    <a class="wz-altcard" id="wz-noorden" href="#" role="button">
      <span class="wz-altcard-txt"><span class="wz-altcard-t">No tengo la orden todavía</span><span class="wz-altcard-s">Te explico cómo conseguirla</span></span>{_WZ_CHEVRON}
    </a><div id="wz-result"></div></div>''', ""


def _service_breadcrumb(s, c, include_service=False):
    """Canonical service hierarchy: center › area [› service].

    The information page omits the service because its H1 already names it.
    Wizard steps include the linked service because their H1 names the current
    action instead.
    """
    cl = site_catalog._cluster_by_id(s.get("cluster")) or {}
    bits = []
    if c.get("name"):
        bits.append(
            f'<a href="/center/{site_base._esc(c["slug"])}">{site_base._esc(c["name"])}</a>'
            if c.get("slug") else site_base._esc(c["name"])
        )
    # On the information page the H1 names the service: an area whose name
    # contains it, or is contained in it («Clínica y medicina general» over
    # «Medicina general»), says it twice (hr-patient review #22, 15.11).
    srv_n = site_base._norm_name(s.get("nombre"))
    cl_n = site_base._norm_name(cl.get("nombre"))
    repite_h1 = (not include_service and srv_n and cl_n
                 and (srv_n in cl_n or cl_n in srv_n))
    if cl.get("nombre") and s.get("cluster") and not repite_h1:
        bits.append(f'<a href="/d/{site_base._esc(s["cluster"])}">{site_base._esc(cl["nombre"])}</a>')
    # A cluster and its only service often share the same visible name
    # ("Pediatría › Pediatría"). The hierarchy is still real in data, but
    # repeating it in the breadcrumb adds no orientation.
    if (include_service and s.get("nombre")
            and site_base._norm_name(s["nombre"]) != site_base._norm_name(cl.get("nombre"))):
        bits.append(f'<a href="/service/{site_base._esc(s["id"])}">{site_base._esc(s["nombre"])}</a>')
    sep = ' <span class="svc-crumb-sep">›</span> '
    return ('<nav class="svc-crumb" aria-label="'
            + site_base._esc(i18n.t_or("page.breadcrumb", "Ubicación", i18n.active()))
            + '">' + sep.join(bits) + "</nav>")


def _page_service_appointment(
        sid, *, query_string="", current_user=None, auth_mode="password"):
    """Booking wizard (/service/<id>/book) — a focused flow (mobile-first,
    designed for older patients); service.js builds the wizard: with a
    referral it starts on the upload, without one (scheduled appointment) it
    starts choosing a time. Listings link STRAIGHT here (the product owner 2026-07-12: no
    middle page); the service's reference page lives at /service/<id>."""
    s = site_catalog._service_by_id(sid)
    if not s or not site_availability._offers_appointments(s):
        return None
    c = site_centers._centers_by_slug().get(s.get("centro")) or {}
    modo = s.get("modo")
    if modo in site_catalog._NO_APPOINTMENT_MODES:
        # Same-day opening: for the wizard it's an appointment like any
        # other (with or without referral depending on the original walk-in
        # mode). Translated here and not in service.js so the client
        # doesn't need to know about this.
        modo = "solicitud_con_orden" if modo == "walkin_con_orden" else "solicitud"
        s = dict(s, modo=modo)

    # wizard-design (2026-07-10): the upload step does NOT show the next
    # appointment or a center reference — just the process context and that
    # step's task. The "when" only shows up on the choose-time step.

    # Link to the clinician who issues the referral: the LIST of Clínica /
    # general medicine (every option and center), not one specific
    # professional — the patient chooses where to book (the product owner 2026-07-17:
    # "instead of transferring to some particular one, transfer to all physicians").
    clinico_href = ""
    gen = next((x for x in site_catalog._services() if site_availability._is_clinical(x)), None)
    if gen and gen.get("cluster"):
        clinico_href = f'/d/{site_base._esc(gen["cluster"])}'
    elif c.get("slug"):
        clinico_href = f'/center/{c["slug"]}'

    # Escape-hatch contact for the minimal footer (help_contact_html):
    # the installation's help line, else ONE WhatsApp for the center or its
    # phone — never a made-up number. Replaces the old "How to get there and
    # contact" dropdown (the reference page lives at /center/…).
    contacto = site_centers.help_contact_html(
        c, i18n.t_or("wizard.contact.center_fallback", "el centro", i18n.active()))

    pasos_estimados = _estimated_steps(modo, query_string, current_user)
    cl = site_catalog._cluster_by_id(s.get("cluster")) or {}
    data = site_base._json_inline({
        "servicio": s,
        "centro": c,
        "cluster": cl,
        "clinico_href": clinico_href,
        # Confirmá says "the account you verified with RENAPER" and shows the
        # "Verified account" stamp: both are true ONLY in federated mode.
        # Travels server-side so the screen doesn't change after the first paint.
        "auth_mode": auth_mode,
        "puede_cambiar_centro": site_catalog._can_change_center(s),
        "pasos_estimados": pasos_estimados,
    })
    h1 = _h1_wizard(modo, query_string, current_user)
    initial_steps, initial_sub, initial_form, initial_host_class = _first_wizard_step(modo, pasos_estimados)
    # If the open account is outside the age range, we don't show the
    # referral upload even for an instant. The client checks the
    # dependents and paints the selector/sign-up before accepting any document.
    try:
        from app.cuentas import accounts
        edad_actual = accounts._age_years((current_user or {}).get("nacimiento"))
    except Exception:
        edad_actual = None
    limites_edad = s.get("edad") or {}
    fuera_edad = (
        edad_actual is not None and
        ((limites_edad.get("min") is not None and edad_actual < int(limites_edad["min"])) or
         (limites_edad.get("max") is not None and edad_actual > int(limites_edad["max"])))
    )
    if initial_form and fuera_edad:
        initial_steps = ""
        # The age gate replaces the schedule boot shell altogether. Keeping
        # its reservation class here would leave an empty 600px region below
        # the real, short age-check message.
        initial_host_class = ""
        initial_form = (f'<div class="sec"><p class="svc-sub">'
                         f'{i18n.t("wizard.age_check.checking", i18n.active())}</p></div>')
        initial_sub = ""
        h1 = i18n.t("wizard.age_check.h1", i18n.active())
    titulo = i18n.t("wizard.title.solicitud", i18n.active()) if modo == "solicitud" else i18n.t("wizard.title.orden", i18n.active())
    context = _service_breadcrumb(s, c, include_service=True)
    initial_host_attr = f' class="{initial_host_class}"' if initial_host_class else ""
    body = f"""
  <div class="svc-head">
    {context}
    <h1 class="h1 h1--focus" id="svc-title">{h1}</h1>
    <div id="svc-steps">{initial_steps}</div>
  </div>
  <p id="svc-sub" class="svc-sub">{initial_sub}</p>
  <div id="svc-form-host"{initial_host_attr}>{initial_form}</div>
  {site_shell._booking_launcher_anchor()}
  <script id="svc-data" type="application/json">{data}</script>
  <script type="module" src="{static_fingerprint.url('platform/photo-zoom.mjs')}"></script>
  {site_listing._turnos_controller_scripts("service.mjs", host_id="svc-form-host", capture_upload=(modo == "solicitud_con_orden"))}
"""
    desc = (f'{titulo} para {s["nombre"]} en '
            f'{c.get("name") or site_identity.get("city")}. Completá el trámite online.')
    # No menu item is lit on a wizard page (it is a task, not the Centros
    # section): an empty section also keeps nav.mjs's path fallback empty.
    return site_shell._shell(f'{titulo} · {s["nombre"]} | {theme.brand_name()}', "", body,
                  desc, chrome="focus", contacto=contacto, index=False)


def _service_info_page_link_html(s):
    """Link from a service page to the information page whose
    `page.related_clusters` lists the service's cluster (info_pages.py) --
    e.g. the vacunatorio service -> /vacunacion. Empty when there is none."""
    try:
        from app.patient_web import info_pages
        found = info_pages.related_page_for_service(s)
    except Exception:  # noqa: BLE001 -- a broken pack never breaks the service page
        return ""
    if not found:
        return ""
    slug, h1 = found
    lead = i18n.t_or("service.info_page_lead", "Más información:", i18n.active())
    return (f'<p class="svc-lede">{site_base._esc(lead)} '
            f'<a class="stz-link" href="/{site_base._esc(slug)}">{site_base._esc(h1)}</a></p>')


def _service_request_band(s, turno_href):
    """The next-appointment band of a request-based service's page, with its ONE action (the wizard)."""
    # Next-appointment band. The usability study found people read the
    # band's appointment as the ONLY one available (the product owner, 2026-07-12). Now
    # the first free appointment IS the button, and next to it there's a
    # second, deliberately quieter, explicit way out ("Choose other times"):
    # two buttons make it visible there's more than one option and which is
    # the fastest. The time is said ONCE — in the button; the band's text
    # doesn't repeat it.
    px = site_availability._service_next(s)
    hay_turno = px.get("kind") == "turno"
    band_off = "" if hay_turno else " mp-band--off"
    # No "Con orden" label on the band (responsable del producto, 2026-07-29): the "Cómo
    # funciona" card right below already explains the full referral — here
    # it would be the same word for the fourth time on one screen. The
    # DOCTOR's band keeps it: that page has no sign, so there it's the only mention.
    if hay_turno:
        # The button carries the preselected appointment to the wizard
        # (?slot=…): one click and it's already chosen — the wizard skips
        # the "When" step. The button speaks the SAME language as the
        # listing cell (the product owner, 2026-07-14, "make the interface consistent in
        # general"): verb + date ("Sacar el viernes 17 de julio, 13:00"),
        # color by proximity, and the secondary one is called "Más
        # horarios" everywhere — one action, one name.
        slot = f'{px.get("dia", "")}T{px.get("hora", "")}'
        # «Con orden» (the reference-deployment counterpart, 19/10): the button is «Subí tu orden» and the
        # first appointment moves into the label next to it.
        con_orden = s.get("modo") == "solicitud_con_orden"
        next_label = i18n.t_or("service.band.next_label", "Primer turno libre", i18n.active())
        band_k = f'{next_label}: {site_base._esc(px.get("v"))}' if con_orden and px.get("v") else next_label
        return (
            f'<div class="mp-band mp-band--turno">'
            f'<span class="mp-band-ico" aria-hidden="true">{site_shell._CAL_ICON}</span>'
            f'<div class="mp-band-txt">'
            f'<div class="mp-band-k">{band_k}</div>'
            f'</div>'
            f'<div class="mp-band-actions">'
            f'<a class="mp-cta mp-cta--{site_studies._cta_bucket(px.get("dia"))}" '
            f'href="{turno_href}?slot={site_base._esc(slot)}">{site_studies._order_cta_label() if con_orden else site_base._esc(site_studies._get_label(px.get("v")))}</a>'
            f'<a class="mp-more" href="{turno_href}?slots=1">{i18n.t_or("listing.more_times", "Más horarios", i18n.active())}</a>'
            f'</div></div>')
    elif px.get("habilita"):
        return site_studies._habilita_band(px["habilita"], turno_href)
    else:
        # With no online schedule there's no appointment to offer.
        # Consistent with the listings' cell (the product owner, 2026-07-14: "same
        # layout, colors, texts"): INK-colored "Sacar turno" button + the
        # SAME plain-text note "La fecha la confirma el centro". No "Turnos"
        # eyebrow (ui-rules) and no sub that repeats what the note already says.
        return (
            f'<div class="mp-band{band_off}">'
            f'<span class="mp-band-ico" aria-hidden="true">{site_shell._CAL_ICON}</span>'
            f'<div class="mp-band-txt">'
            f'<div class="mp-band-note">{i18n.t_or("listing.date_confirmed", "La fecha la confirma el centro", i18n.active())}</div></div>'
            f'<div class="mp-band-actions">'
            f'<a class="mp-cta mp-cta--ink" href="{turno_href}">'
            f'{site_studies._order_cta_label() if s.get("modo") == "solicitud_con_orden" else i18n.t_or("listing.book", "Sacar turno", i18n.active())}</a></div>'
            f'</div>')


def _pami_applies(current_user):
    """Whether the PAMI note speaks to this visitor (hr-patient review #5,
    15.11): an anonymous visitor, or one whose coverage is unknown, may be a
    PAMI member; a signed-in patient whose stored coverage names insurers,
    none of them PAMI, gets no note addressed to someone else."""
    if not current_user:
        return True
    cob = current_user.get("cobertura")
    cob = cob if isinstance(cob, dict) else {}
    # Both stored shapes: the list `obras_sociales` and the old single one.
    os_ = cob.get("obras_sociales")
    os_ = os_ if isinstance(os_, list) else [cob.get("obra_social")]
    nombres = [str(x.get("nombre") or "") if isinstance(x, dict) else str(x or "") for x in os_]
    prepaga = cob.get("prepaga")
    if isinstance(prepaga, dict):
        nombres.append(str(prepaga.get("nombre") or ""))
    elif prepaga:
        nombres.append(str(prepaga))
    nombres = [n for n in nombres if n.strip()]
    if not nombres:
        return True
    # The registry also writes it «INSSJP» or «...JUBILADOS Y PENSIONADOS».
    from app.cuentas import health_coverage
    for n in nombres:
        t = " " + health_coverage._normalize(n) + " "
        if " pami " in t or "inssjp" in t.replace(" ", "") or "jubilados y pensionados" in t:
            return True
    return False


def _service_request_cartel_html(s, current_user=None):
    """The referral that's needed (the sign) and the PAMI note, in the same card; '' without either."""
    # The referral that's needed (sign) + PAMI note, in the same card. The
    # heading is "Cómo funciona" (not "Necesitás una orden médica"): the
    # authored sign almost always starts with "Necesitás una orden…" and
    # would end up repeated word for word.
    pami_txt = s.get("pami") if _pami_applies(current_user) else ""
    if not (s.get("cartel") or pami_txt):
        return ""
    pami = f'<p>{site_base._esc(pami_txt)}</p>' if pami_txt else ""
    return (f'<div class="cartel">{site_availability._BANNER_ICON}<div><p class="ct-h">'
            f'{i18n.t_or("service.how_it_works", "Cómo funciona", i18n.active())}</p>'
            f'<p>{site_base._esc(s.get("cartel", ""))}</p>{pami}</div></div>')


def _service_request_prep_html(s):
    """How to prepare: the catalog's preparation and the entry point to the assistant that reads the referral."""
    # The catalog's preparation + the entry point to the assistant that
    # reads the referral. Until 2026-07-29 only the assistant lived here,
    # because on a generic page like "Ecografía" an approximate instruction
    # is worse than none (the abdominal and the breast ones ask for
    # different things). With attendance preparations — vaccination,
    # consult, dentistry, mental health — that rule left mute exactly the
    # ones that DON'T depend on any referral. Now both show: the catalog's
    # says what's already known (and when it truly depends on the
    # procedure, its own summary says so), and the assistant is still the
    # path to the exact one.
    prep_cuerpo, prep_teaser = site_preparation._prep_section_html(s), site_preparation._prep_teaser_html(s)
    if not (prep_cuerpo or prep_teaser):
        return ""   # a title alone isn't a section
    return (
        '<section class="prep prep--pagina" aria-labelledby="svc-prep-title">'
        f'<h2 class="prep-h" id="svc-prep-title">{i18n.t_or("service.prep_heading", "Cómo prepararte", i18n.active())}</h2>'
        f'{prep_cuerpo}{prep_teaser}'
        "</section>"
    )


def _service_request_place_html(s, c):
    """Where it is and when (center, address, phone, office, age, schedule): the block and the weekly grid."""
    # Where it is: center + address + phone + office + schedule + age,
    # together and in plain sight (same block as the doctor's "Atiende en").
    ref = []
    if c.get("address"):
        ref.append(
            f'<div class="ci"><a class="stz-link ci-ico" href="{site_base._esc(site_centers._maps_url(c))}" '
            f'target="_blank" rel="noopener">{site_shell._PIN_ICON}{site_base._esc(c["address"])}</a></div>')
    ref.extend(f'<div class="ci">{a}</div>' for a in site_centers._center_call_links(c, "stz-link ci-ico"))
    if s.get("consultorio"):
        ref.append(site_centers._room_line_html(s["consultorio"]))
    # The service's real schedule → weekly grid (platform/week.mjs, same as on the
    # doctor's page). "Con turno, según agenda" isn't a schedule (it's a
    # spreadsheet placeholder): in that case, the center's own schedule instead.
    horario_svc = (s.get("horario") or "").strip()
    if "según agenda" in horario_svc.lower():
        horario_svc = ""
    semana_html = ""
    if horario_svc:
        semana_html = site_listing._week_markup(horario_svc, i18n.t_or("page.service.when", "Cuándo se atiende", i18n.active()))
    elif c.get("horario"):
        ref.append(f'<div class="ci"><span class="ci-k">{i18n.t_or("page.hours.time", "Horario", i18n.active())}</span> {site_base._esc(c["horario"])}</div>')
    et = site_centers._age_text(s)
    if et:
        ref.append(f'<div class="ci"><span class="ci-k">{i18n.t_or("service.age_label", "Edad", i18n.active())}</span> {site_base._esc(et)}</div>')
    lugar = c.get("name") or ""
    if s.get("sede"):
        lugar = f'{lugar} · {s["sede"]}' if lugar else s["sede"]
    if not lugar:
        return "", semana_html
    return (
        f'<div class="mp-lugar">'
        f'<h2 class="mp-lugar-h">{i18n.t_or("service.seen_at", "Se atiende en", i18n.active())} '
        f'<a href="/center/{site_base._esc(c.get("slug", ""))}">{site_base._esc(lugar)}</a></h2>'
        f'{"".join(ref)}</div>'), semana_html


def _service_request_doctors_html(s):
    """Who sees you: the doctors of the service, with the scripts their listing needs; ('', '') without any."""
    # Who sees you: only on specialty clusters (there the cluster+center
    # match IS the specialty); in "diagnostico" different studies coexist
    # and it would list professionals from another study — better nothing
    # than a doubtful fact.
    # A doctor whose schedule names THIS service is a stated fact on any
    # cluster, "diagnostico" included, even when it is not their main one
    # (probador en sitio, 2026-10-21: a doctor with Cardiología, Cirugía and
    # Electrocardiograma only showed under Cardiología).
    people = [m for m in site_catalog._doctors() if schedule_policy.person(m)]
    tagged = [m for m in people if any(t.get("id") == s.get("id") for t in site_doctors._doctor_tagged_services(m))]
    inferred = []
    if s.get("cluster") and s["cluster"] != "diagnostico":
        inferred = [m for m in people
                    if s["cluster"] in site_catalog._doctor_clusters(m) and m.get("centro") == s.get("centro")
                    and (site_doctors._doctor_service(m) or {}).get("id") == s.get("id")
                    and m not in tagged]
    meds = sorted(tagged + inferred, key=lambda m: m.get("nombre", ""))
    if not meds:
        return "", ""
    rows = [site_listing._doctor_listing_row(m, s if m in tagged else None) for m in meds]
    meds_html = site_listing._listing_block(
        "service-doctors", site_listing._cols("medico", "proximo"), rows,
        {"title": i18n.t_or("service.who_sees", "Quién atiende", i18n.active()), "rowHref": "href",
         "rank": i18n.t_or("page.service.who", "popularidad", i18n.active())},
        in_column=True)
    return meds_html, site_listing._LISTING_SCRIPT


def _service_info_request(s, c, current_user=None):
    """Info page for a request-based service (/service/<id>) — mirrors the
    doctor's page (the product owner 2026-07-12: "a page like the doctor's"): next-
    appointment band + ONE action (the wizard), what referral is needed,
    where it is and its schedule, and at the end how to prepare (the product owner,
    2026-07-29: preparation only matters once you already have the
    appointment). The ACTION lives at /service/<id>/book and the listings go
    STRAIGHT there; this page is the reference (wizard breadcrumb,
    my-requests, /upload-order, search)."""
    turno_href = f'/service/{site_base._esc(s["id"])}/book'

    # The current service is already the H1, so the breadcrumb carries only
    # its ancestors. The wizard adds the service as the final linked ancestor
    # because its H1 is the current step action.
    crumb = _service_breadcrumb(s, c, include_service=False)
    band = _service_request_band(s, turno_href)
    cartel_html = _service_request_cartel_html(s, current_user)
    prep_html = _service_request_prep_html(s)
    ref_html, semana_html = _service_request_place_html(s, c)
    meds_html, meds_scripts = _service_request_doctors_html(s)

    # Age warning (if the browser's account doesn't fit): service.js with no
    # #svc-form-host falls straight into renderGateOnly.
    data = site_base._json_inline({"servicio": s, "centro": c})
    semana_scripts = site_listing._WEEK_SCRIPT if semana_html else ""
    body = f"""
  {crumb}
  <h1 class="h1">{site_base._esc(s["nombre"])}</h1>
  {site_preparation._service_announcements_html(s)}
  {_service_info_page_link_html(s)}
  {band}
  {cartel_html}
  {ref_html}
  {semana_html}
  {meds_html}
  {prep_html}
  <div id="svc-gate-host"></div>
  <script id="svc-data" type="application/json">{data}</script>
  {site_listing._turnos_controller_scripts("service.mjs", host_id="svc-gate-host", capture_upload=False)}
  {semana_scripts}
  {meds_scripts}
"""
    desc = (s.get("descripcion") or s.get("cartel") or
            i18n.t_or("service.meta.desc_request",
                      "Cómo atenderte en {nombre}, dónde es, qué necesitás y cómo sacar turno.",
                      i18n.active(), nombre=s["nombre"]))
    return site_shell._shell(
        f'{i18n.t_or("service.title_at", "{nombre} en {lugar}", i18n.active(), nombre=s["nombre"], lugar=c.get("name") or site_identity.get("city"))} | {theme.brand_name()}',
        _service_section(s), body, desc, canonical=f'/service/{s["id"]}')


def _service_section(s):
    """The nav section a service's page belongs to: «Estudios» for a study
    (the same rule as its cluster page, /d/diagnostico), none otherwise. A
    service is not a center: «Centros» lit up only because the breadcrumb
    starts there (the product owner 26.10)."""
    return "estudios" if s.get("cluster") == "diagnostico" else ""


def _page_service(sid, current_user=None):
    """Service page (/service/<id>): ALWAYS informational. Request-based →
    mirrors the doctor's page (band + CTA to the wizard); walk-in → the
    classic page with the sign. The wizard lives at /service/<id>/book."""
    s = site_catalog._service_by_id(sid)
    if not s:
        return None
    c = site_centers._centers_by_slug().get(s.get("centro")) or {}
    modo = s.get("modo")
    if modo in ("solicitud_con_orden", "solicitud"):
        return _service_info_request(s, c, current_user)

    # ---- walk-in / walk-in with referral: one single info hierarchy ----
    # We don't repeat mode or schedule across sign + metadata + live status.
    # The page says once what's needed, once where, and once when.
    requisitos = [i18n.t_or("page.req.no_appointment", "No necesitás turno.", i18n.active())]
    requisitos.append(
        i18n.t_or("page.req.bring_order", "Traé una orden médica.", i18n.active())
        if modo == "walkin_con_orden"
        else i18n.t_or("page.req.no_order", "No necesitás orden médica.", i18n.active())
    )
    et = site_centers._age_text(s)
    if et:
        requisitos.append(et)
    if s.get("pami") and _pami_applies(current_user):
        requisitos.append(s["pami"])
    requisitos_html = "".join(f"<li>{site_base._esc(x)}</li>" for x in requisitos)

    donde = c.get("name") or ""
    if s.get("sede"):
        donde = f'{donde} · {s["sede"]}' if donde else s["sede"]
    contacto = []
    if donde:
        contacto.append(
            f'<a class="stz-link svc-contact-place" href="/center/{site_base._esc(c.get("slug",""))}">'
            f'{site_base._esc(donde)}</a>'
        )
    if c.get("address"):
        contacto.append(
            f'<a class="stz-link" href="{site_base._esc(site_centers._maps_url(c))}" target="_blank" '
            f'rel="noopener">{site_shell._PIN_ICON}{site_base._esc(c["address"])}</a>'
        )
    contacto.extend(site_centers._center_call_links(c, "stz-link"))
    if s.get("consultorio"):
        contacto.append(f'<span>{site_base._esc(site_centers._room_text(s["consultorio"]))}</span>')
    contacto_html = "".join(f"<div>{x}</div>" for x in contacto)

    prep_content = site_preparation._prep_section_html(s) or (
        f'<p class="svc-hours-note">'
        f'{i18n.t_or("service.no_prep_note", "No hay indicaciones especiales publicadas para esta atención.", i18n.active())}'
        f'</p>')

    data = site_base._json_inline({"servicio": s, "centro": c})
    descripcion_html = (
        f'<p class="svc-lede">{site_base._esc(s["descripcion"])}</p>'
        if s.get("descripcion") else ""
    )
    horario_nota_html = (
        f'<p class="svc-hours-note">{site_base._esc(s["horario_nota"])}</p>'
        if s.get("horario_nota") else ""
    )
    horas_html = (
        f'<div class="svc-hours">{site_listing._week_markup(s["horario"], i18n.t_or("service.hours_title", "Horarios", i18n.active()))}</div>'
        f'{horario_nota_html}'
        if s.get("horario") else ""
    )
    horas_scripts = (
        site_listing._WEEK_SCRIPT if horas_html else ""
    )
    body = f"""
  <h1 class="h1">{site_base._esc(s["nombre"])}</h1>
  {descripcion_html}
  {site_preparation._service_announcements_html(s)}
  {_service_info_page_link_html(s)}
  <div class="svc-major-grid">
    <section class="svc-major" aria-labelledby="svc-place-title">
      <h2 class="svc-section-h" id="svc-place-title">{i18n.t_or("service.where_seen", "Dónde se atiende", i18n.active())}</h2>
      <div class="svc-contact">{contacto_html}</div>
      {horas_html}
    </section>
    <section class="svc-major" aria-labelledby="svc-req-title">
      <h2 class="svc-section-h" id="svc-req-title">{i18n.t_or("service.requirements", "Requisitos", i18n.active())}</h2>
      <ul class="svc-req">{requisitos_html}</ul>
    </section>
    <section class="svc-major svc-major--prep" aria-labelledby="svc-prep-title">
      <h2 class="svc-section-h" id="svc-prep-title">{i18n.t_or("service.prep_heading", "Cómo prepararte", i18n.active())}</h2>
      {prep_content}
    </section>
  </div>
  <div id="svc-gate-host"></div>
  <script id="svc-data" type="application/json">{data}</script>
  {site_listing._turnos_controller_scripts("service.mjs", host_id="svc-gate-host", capture_upload=False)}
  {horas_scripts}
"""
    desc = (s.get("descripcion") or s.get("cartel") or
            i18n.t_or("service.meta.desc_walkin",
                      "Cómo atenderte en {nombre}, dónde es, qué necesitás y en qué horarios.",
                      i18n.active(), nombre=s["nombre"]))
    return site_shell._shell(
        f'{i18n.t_or("service.title_at", "{nombre} en {lugar}", i18n.active(), nombre=s["nombre"], lugar=c.get("name") or site_identity.get("city"))} | {theme.brand_name()}',
        _service_section(s), body, desc, canonical=f'/service/{s["id"]}')
