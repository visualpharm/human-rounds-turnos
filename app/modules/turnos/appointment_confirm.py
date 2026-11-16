"""appointment_confirm.py — "¿vas a poder venir?", the click side.

Owner of this module:
  GET  /appointment/confirm/<token>   server-rendered page (site_shell._shell,
                                       "focus" chrome): which appointment it is
                                       + "Sí, voy a ir" (primary button) /
                                       "No voy a poder ir" (secondary link).
                                       `?r=si` resolves the yes on the spot;
                                       `?r=no` opens the release step.
  POST /api/confirm/<token>/si        records the yes (confirmations.record).
  POST /api/confirm/<token>/no        records the no AND frees the slot, which
                                       is exactly what the reminder promised —
                                       same path as a manual cancellation, so
                                       the waitlist gets its offer.

The token IS the authentication, same as appointment_offer.py: it arrived by
email to the account that requested the appointment. The person opening the
reminder on their phone the night before has no session, and making them log
in to say "sí" is exactly the friction that gets no answer at all.

Why the answer is never resolved by the GET itself: a link scanner (the
patient's own mail client, a corporate filter) fetches every URL in an email,
and a GET that cancels an appointment would let a scanner free a slot nobody
released. Every state change is a POST fired by confirm.mjs — scanners fetch
HTML, they don't run JS. `?r=si` still means one click for the patient: the
page loads and posts on its own. `?r=no` deliberately does NOT auto-post: it
asks once before giving the appointment away.

Nothing here is ever a dead end (patient-flow-design rule 4): an expired
token, an appointment that already passed, one already answered — each screen
carries the next real step, never just a sign saying no.
"""

import re
from datetime import datetime, timezone
from urllib.parse import parse_qs

from app.core import db_booking
from app.core import static_fingerprint
from app.core import theme
from app.modules.turnos.appointment_page_support import (
    center_name as _center_name, data_token_attribute as _data_token_attribute,
    friendly_appointment as _friendly_appointment, service_name as _service_name,
)

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ---- typed errors for the extracted domain functions ------------------------
#
# (docs/roadmap/native-migration-campaign-20260826.md)
# `resolve_answer` below takes parsed arguments and either returns the
# patient-facing message or raises ONE of these. The native POST endpoint in
# `appointment_confirm_router.py` turns that decision tree into a response.

class TokenInvalid(Exception):
    """No matching row for the token (-> 404 token_invalido)."""


class NotConfirmable(Exception):
    """The row isn't in an 'aprobada' state (-> 410 no_confirmable)."""


class AppointmentPassed(Exception):
    """turno_dt is missing or already past (-> 410 turno_pasado)."""


class RecordFailed(Exception):
    """confirmations.record() couldn't save the answer (-> 500 no_se_pudo)."""


# ---- side reads (catalog/names) --------------------------------

def _row_for(token):
    if not token or not _TOKEN_RE.match(token):
        return None
    try:
        return db_booking.booking_request_by_confirm_token(token)
    except Exception:  # noqa: BLE001
        return None


def _requested(param, query):
    v = (parse_qs(query or "").get(param) or [""])[0].strip().lower()
    return v if v in ("si", "no") else ""


# ---- the pages -------------------------------------------------------------

def _page(title, body, desc):
    from app.patient_web import site_shell
    return site_shell._shell(f"{title} | {theme.brand_name()}", "solicitudes",
                               body, desc, chrome="focus", index=False)


def _confirm_controller_script(token, auto=""):
    """Native confirmation bootstrap with ownership of load recovery.

    The inline script retains the old data-token seam while it captures a
    patient click made before the fingerprinted controller finishes loading.
    A rejected controller never mutates the appointment.
    """
    from app.core import static_fingerprint
    url = static_fingerprint.module_url("modules/turnos/confirm.mjs")
    auto_attr = ' data-auto="si"' if auto == "si" else ""
    return f'''<script {_data_token_attribute(token)}{auto_attr}>
(function () {{
  var script = document.currentScript, token = script && script.getAttribute("data-token");
  var auto = script && script.getAttribute("data-auto"), controls = [].slice.call(document.querySelectorAll("[data-confirm-action]"));
  var msg = document.getElementById("confirm-msg"), pending = null, stopped = false, ready = false, controller = null;
  function action(el) {{ return el.getAttribute("data-confirm-action") === "no" ? "no" : "si"; }}
  function fail() {{
    if (!msg) return;
    controls.forEach(function (el) {{ el.disabled = true; el.style.pointerEvents = "none"; el.style.opacity = "0.6"; }});
    msg.textContent = "No pudimos cargar esta pantalla. Recargá para intentar de nuevo.";
    msg.style.color = "#a3352f";
    if (!msg.querySelector("[data-confirm-retry]")) {{
      var retry = document.createElement("button");
      retry.type = "button"; retry.setAttribute("data-confirm-retry", ""); retry.textContent = "Recargá";
      retry.className = "mp-cta"; retry.style.display = "block"; retry.style.marginTop = "12px";
      retry.style.minHeight = "44px"; retry.style.fontFamily = "inherit"; retry.style.border = "none";
      retry.addEventListener("click", function () {{ location.reload(); }}); msg.appendChild(retry);
    }}
  }}
  controls.forEach(function (el) {{ el.addEventListener("click", function (event) {{
    if (ready) return; event.preventDefault(); if (stopped || pending !== null) return; pending = action(el);
  }}); }});
  window.addEventListener("pagehide", function () {{ stopped = true; if (controller) controller.destroy(); }}, {{ once: true }});
  window.addEventListener("pageshow", function (event) {{ if (event.persisted && stopped) location.reload(); }});
  import("{url}").then(function (module) {{
    if (stopped) return;
    ready = true;
    controller = module.mount({{ token: token, autoAction: auto, initialAction: pending }});
    pending = null;
    return controller;
  }}).catch(function () {{ if (!stopped) fail(); }});
}})();
</script>'''


def _alternatives_html(servicio_id):
    """What to offer instead of a dead end — same source the waitlist offer
    uses (`appointment_offer._alternatives_html`), so both "this link no
    longer works" screens send the patient to the same next step."""
    try:
        from app.modules.turnos import appointment_offer
        return appointment_offer._alternatives_html(servicio_id)
    except Exception:  # noqa: BLE001
        return '<p class="lede"><a href="/">Ver todos los servicios y centros</a></p>'


def _expired_page(servicio_id=None):
    from app.core import jinja_env
    from markupsafe import Markup
    body = jinja_env.render("fragments/link-expired.html", alternatives=Markup(_alternatives_html(servicio_id)))
    return _page("Link vencido", body,
                 "Este link de confirmación ya no sirve. Mirá tus solicitudes o pedí otro turno.")


def _cancelled_page(servicio_id=None):
    from app.core import jinja_env
    from markupsafe import Markup
    body = jinja_env.render("fragments/turno-cancelled.html", alternatives=Markup(_alternatives_html(servicio_id)))
    return _page("Turno cancelado", body, "Ese turno está cancelado.")


def _interview_cta_html(row, handler=None):
    """The pre-consult symptoms CTA on the already-confirmed page
    (2026-08-03: "Contale al médico cómo te sentís" — see
    docs/architecture/entrevista-previa.md). A large primary button — same
    style as "Sí, voy a ir" — while the interview is still pending; a quiet
    update link once it's done; nothing at all when intake doesn't apply
    (module off, doctor/system toggle off, no row id). Reuses
    notifications.py's own CTA URL (`_entrevista_cta`, which already appends
    the login-free patient token) and pending/done criterion
    (`_interview_pending`) so this page degrades exactly like the reminder
    emails do — one check, not a second copy of it. GET-only page, so this
    never mutates anything."""
    try:
        from app.notifications import send as notifications
        cta = notifications._entrevista_cta(row, handler)
        if not cta:
            return ""
        from app.patient_web import site_base
        url = site_base._esc(cta["url"])
        if notifications._interview_pending(row, handler):
            return (f'\n  <p style="margin-top:22px;">'
                    f'<a class="mp-cta" href="{url}">{notifications._ENTREVISTA_CTA_LABEL}</a></p>\n')
        return (f'\n  <p style="margin-top:16px;font-size:15px;">'
                f'<a href="{url}" class="stz-link">¿Cambió algo? Actualizá lo que le '
                f'contaste al médico</a></p>\n')
    except Exception:  # noqa: BLE001
        return ""


def _already_page(valor, servicio, cuando, token, servicio_id, row=None, handler=None):
    """Already answered. Not a dead end either: someone who said yes and then
    can't come has to be able to say so from the same link — that's the whole
    point of asking."""
    from app.patient_web import site_base
    cuando_txt = f" {site_base._esc(cuando)}" if cuando else ""
    if valor == "si":
        interview_html = _interview_cta_html(row, handler) if row else ""
        body = f"""
  <h1 class="h1">Confirmado: te esperamos</h1>
  <p class="lede">{site_base._esc(servicio)}{cuando_txt}.</p>
  {interview_html}
  <p style="margin-top:20px;font-size:15px;">
    <a href="#" class="stz-link" data-confirm-action="no">Cambió mi plan: no voy a poder ir</a>
  </p>
  <div id="confirm-msg" style="margin-top:16px;font-size:14.5px;color:var(--text-2);" role="status"></div>
  <p class="sd-ref"><a href="/my-requests">Todos mis turnos</a></p>
  {_confirm_controller_script(token)}
"""
        return _page("Turno confirmado", body, "Ya confirmaste tu turno.")

    body = f"""
  <h1 class="h1">Ya nos dijiste que no podés venir</h1>
  <p class="lede">Liberamos el turno para otra persona.</p>
  {_alternatives_html(servicio_id)}
  <p class="sd-ref"><a href="/my-requests">Todos mis turnos</a></p>
"""
    return _page("Turno liberado", body, "Ya avisaste que no podés venir a ese turno.")


def _ask_page(token, servicio, centro, cuando, modo=""):
    """The question itself. `modo="no"` is the release step: same screen, but
    what it asks is "do you want to give the appointment away", because that
    click is the one that can't be taken back."""
    from app.patient_web import site_base
    lugar = f" · {site_base._esc(centro)}" if centro else ""
    cuando_txt = site_base._esc(cuando) if cuando else "Mañana"
    if modo == "no":
        body = f"""
  <h1 class="h1">¿Liberamos tu turno?</h1>
  <p class="lede">{site_base._esc(servicio)} · {cuando_txt}{lugar}. Si no vas a poder venir, lo damos de baja y queda disponible para otra persona.</p>
  <p style="margin-top:26px;">
    <button type="button" class="mp-cta" data-confirm-action="no" style="border:none;cursor:pointer;font-family:inherit;">No voy a poder ir</button>
  </p>
  <p style="margin-top:16px;font-size:15px;">
    <a href="#" class="stz-link" data-confirm-action="si">Me equivoqué: sí voy a ir</a>
  </p>
  <div id="confirm-msg" style="margin-top:16px;font-size:14.5px;color:var(--text-2);" role="status"></div>
  {_confirm_controller_script(token)}
"""
        return _page("Liberar el turno", body,
                     f"Liberá tu turno de {servicio} si no vas a poder venir.")

    body = f"""
  <h1 class="h1">¿Vas a poder venir?</h1>
  <p class="lede">{site_base._esc(servicio)} · {cuando_txt}{lugar}.</p>
  <p style="margin-top:26px;">
    <button type="button" class="mp-cta" data-confirm-action="si" style="border:none;cursor:pointer;font-family:inherit;">Sí, voy a ir</button>
  </p>
  <p style="margin-top:16px;font-size:15px;">
    <a href="#" class="stz-link" data-confirm-action="no">No voy a poder ir</a>
  </p>
  <div id="confirm-msg" style="margin-top:16px;font-size:14.5px;color:var(--text-2);" role="status"></div>
  {_confirm_controller_script(token, modo)}
"""
    return _page("Confirmá tu turno", body,
                 f"Confirmá si vas a poder venir a tu turno de {servicio}.")


# ---- GET /appointment/confirm/<token> --------------------------------------

def build_confirm_page(handler, token, query=""):
    """Pure domain function behind GET /appointment/confirm/<token>: builds
    and returns the HTML body (bytes, same as `site_shell._shell()`
    itself returns) for whichever screen applies — never touches `handler`
    for output, only forwards it to `_already_page()`'s own CTA lookup
    (`_interview_cta_html`, which only reads `.headers` transitively, same
    duck type as `accounts.current_user`). `query` is the raw query string
    (`?r=si`/`?r=no`), parsed via
    `_requested()`."""
    row = _row_for(token)
    if not row:
        return _expired_page()

    servicio = _service_name(row.get("servicio_id"))
    centro = _center_name(row.get("centro_slug"))
    cuando = _friendly_appointment(row.get("turno_dt"))
    servicio_id = row.get("servicio_id")

    if (row.get("estado") or "") == "cancelada":
        return _cancelled_page(servicio_id)

    turno_dt = row.get("turno_dt")
    if not turno_dt or turno_dt <= datetime.now(timezone.utc):
        return _expired_page(servicio_id)

    ya = (row.get("confirmacion") or "").strip()
    if ya in ("si", "no"):
        return _already_page(ya, servicio, cuando, token, servicio_id, row, handler)

    modo = _requested("r", query)
    return _ask_page(token, servicio, centro, cuando, modo)


# ---- POST /api/confirm/<token>/{si,no} -------------------------------------

def resolve_answer(request, token, valor):
    """Domain function behind POST /api/confirm/<token>/{si,no}.

    The request remains only for unrelated notification compatibility;
    cancellation itself now calls the canonical typed booking operation and
    never reads it. Returns the patient-facing message on
    success; raises TokenInvalid/NotConfirmable/AppointmentPassed/
    RecordFailed otherwise."""
    row = _row_for(token)
    if not row:
        raise TokenInvalid()

    if (row.get("estado") or "") != "aprobada":
        raise NotConfirmable()
    turno_dt = row.get("turno_dt")
    if not turno_dt or turno_dt <= datetime.now(timezone.utc):
        raise AppointmentPassed()

    from app.notifications import confirmations
    mensaje = confirmations.record(row, valor, canal="email", handler=request)
    if not mensaje:
        raise RecordFailed()
    return mensaje
