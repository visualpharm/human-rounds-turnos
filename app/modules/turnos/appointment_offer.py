"""appointment_offer.py — the offer of a released appointment slot, the click side.

Owner of this module:
  GET  /appointment/offer/<token>     server-rendered page (site_shell._shell,
                                       "focus" chrome): which slot opened up +
                                       "Tomar este turno" (primary button) /
                                       "No lo quiero" (secondary link). The
                                       token IS the authentication, same as
                                       calendar_feed.py's ICS feed — it arrived
                                       via waitlist._create_offer's
                                       email, no one else has it.
  POST /api/offer/<token>/accept      claims the slot (waitlist.claim):
                                       reassigns the patient's request's
                                       turno_dt to the released slot and
                                       pushes it to HSI through the durable
                                       journal (best-effort — the
                                       reassignment ALWAYS persists locally
                                       even if HSI fails).
  POST /api/offer/<token>/reject      "I don't want it": releases the spot
                                       before the window expires (waitlist.
                                       reject_offer) and moves to the next.

Who TRIGGERS an offer run (release_slot) is booking_requests.py on cancel, or the
periodic sweep (waitlist.start(), server.py:main) when a window expires with
no response — this module only serves the page and resolves the patient's
click on an offer that already exists.

"No longer available" is NEVER a dead end (2026-07-16, the product owner after seeing the
screen in production): `_alternatives_html` knows the `servicio_id` of the
expired/taken row and offers that service's CURRENT availability with the
same verb+date CTA as the /service/<id> band — only when the token is
invalid or the row doesn't exist (no service to offer) does it fall back to
the general directory.
"""

import re
from datetime import datetime, timezone

from app.core import static_fingerprint, theme
from app.modules.turnos import waitlist
from app.modules.turnos import waitlist_store as store
from app.modules.turnos import booking_requests_store as booking_store
from app.modules.turnos.appointment_page_support import (
    center_name as _center_name, data_token_attribute as _data_token_attribute,
    friendly_appointment as _friendly_appointment, service_name as _service_name,
)

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ---- typed errors for the extracted domain functions ------------------------
#
# (docs/roadmap/native-migration-campaign-20260826.md)
# `accept_offer`/`reject_offer_action` below take parsed arguments and either
# return the success payload or raise ONE of these. The native POST endpoints
# in `appointment_offer_router.py` build their responses from that same
# decision tree.

class TokenInvalid(Exception):
    """Bad/missing token shape (-> 400 token_invalido, both accept and reject)."""


class OfferClaimFailed(Exception):
    """`waitlist.claim()` declined the offer; carries the reason string
    waitlist itself returns (-> 404 for "no_existe", 410 for anything else,
    body {"error": reason})."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


# ---- side reads (catalog/names) --------------------------------

def _offer_for(token):
    return store.offer_by_token(token)


# ---- GET /appointment/offer/<token> ----------------------------------------------

def _alternatives_html(servicio_id):
    """What to offer instead of leaving the patient at a dead end (patient-
    flow-design rule 4: no "can't continue" ends on a sign). If we know the
    service (the expired/taken offer HAD a row), the SAME availability the
    wizard offers — `site_listing._appointment_href` + `_service_next` are the
    same hold-aware sources as the /service/<id> band and the listing
    cells: concrete slot -> verb+date CTA (same language as
    `_get_label`/`_cta_bucket`) + a quieter "More times" next to it; no
    real slot -> back to the service page, where a request can still be
    made (the hospital confirms the date). No known service (invalid token
    or no row) -> the general directory, never just "My requests"."""
    from app.patient_web import site_availability, site_base, site_catalog, site_listing, site_studies
    s = site_catalog._service_by_id(servicio_id) if servicio_id else None
    if not s:
        return '<p class="lede"><a href="/">Ver todos los servicios y centros</a></p>'

    nombre = site_base._esc(s.get("nombre") or "")
    turno_href = site_listing._appointment_href(s)
    px = site_availability._service_next(s)
    if px.get("kind") == "turno" and px.get("dia") and px.get("hora"):
        slot = f'{px.get("dia", "")}T{px.get("hora", "")}'
        return f"""
  <p class="lede">Pero todavía podés sacar otro turno de {nombre}.</p>
  <p style="margin-top:22px;">
    <a class="mp-cta mp-cta--{site_studies._cta_bucket(px.get("dia"))}" href="{turno_href}?slot={site_base._esc(slot)}">{site_base._esc(site_studies._get_label(px.get("v")))}</a>
  </p>
  <p style="margin-top:14px;font-size:15px;">
    <a class="mp-cta-2" href="{turno_href}?slots=1">Más horarios</a>
  </p>
"""
    return (f'<p class="lede">Todavía podés pedir un turno de '
            f'<a href="/service/{site_base._esc(s.get("id") or "")}">{nombre}</a>.</p>')


def _unavailable_page(servicio_id=None):
    from app.core import jinja_env
    from markupsafe import Markup
    from app.patient_web import site_shell
    body = jinja_env.render("fragments/turno-unavailable.html", alternatives=Markup(_alternatives_html(servicio_id)))
    return site_shell._shell(f"Turno no disponible | {theme.brand_name()}", "solicitudes", body,
                           "Este turno ya no está disponible. Buscá otra fecha para el mismo servicio.",
                           chrome="focus", index=False)


def _database_unavailable_page():
    from app.core import jinja_env
    from app.patient_web import site_shell
    body = jinja_env.render("fragments/turno-database-unavailable.html")
    return site_shell._shell(
        f"Turno no disponible | {theme.brand_name()}",
        "solicitudes",
        body,
        "El sistema está temporalmente fuera de servicio.",
        chrome="focus",
        index=False,
    )


def _confirmed_page(servicio, centro, cuando):
    from app.patient_web import site_base, site_shell
    lugar = f" en {site_base._esc(centro)}" if centro else ""
    body = f"""
  <h1 class="h1">¡Listo! Tu turno quedó confirmado</h1>
  <p class="lede">{site_base._esc(servicio)}{lugar}, {site_base._esc(cuando)}.</p>
  <p class="sd-ref"><a href="/my-requests">Mis turnos</a></p>
"""
    return site_shell._shell(f"Turno confirmado | {theme.brand_name()}", "solicitudes", body,
                           "Tu turno quedó confirmado. Consultá el servicio, el centro y la fecha.",
                           chrome="focus", index=False)


def _offer_available_page(token, servicio, centro, cuando):
    from app.patient_web import site_base, site_shell
    lugar = f" · {site_base._esc(centro)}" if centro else ""
    body = f"""
  <h1 class="h1">Se liberó un turno de {site_base._esc(servicio)}</h1>
  <p class="lede">{site_base._esc(cuando)}{lugar}. Por 5 minutos este turno es tuyo, garantizado.</p>
  <p style="margin-top:26px;">
    <button type="button" id="oferta-aceptar" class="mp-cta" style="border:none;cursor:pointer;font-family:inherit;">Tomar este turno</button>
  </p>
  <p style="margin-top:16px;font-size:16px;">
    <a href="#" id="oferta-rechazar" class="stz-link">No lo quiero</a>
  </p>
  <div id="oferta-msg" style="margin-top:16px;font-size:16px;color:var(--text-2);" role="status"></div>
  <script {_data_token_attribute(token)}>
  (function () {{
    var script = document.currentScript;
    var token = script && script.getAttribute("data-token");
    var accept = document.getElementById("oferta-aceptar");
    var reject = document.getElementById("oferta-rechazar");
    var msg = document.getElementById("oferta-msg");
    var pending = null;
    var stopped = false;
    var ready = false;
    var controller = null;
    function disable() {{
      if (accept) accept.disabled = true;
      if (reject) reject.style.pointerEvents = "none";
    }}
    function fail() {{
      if (stopped) return;
      disable();
      if (!msg) return;
      msg.textContent = "No pudimos cargar esta pantalla. Recargá para intentar de nuevo.";
      msg.style.color = "#a3352f";
      if (msg.querySelector("[data-offer-retry]")) return;
      var retry = document.createElement("button");
      retry.type = "button";
      retry.className = "mp-cta";
      retry.setAttribute("data-offer-retry", "");
      retry.textContent = "Recargar";
      retry.style.display = "block";
      retry.style.marginTop = "16px";
      retry.style.border = "none";
      retry.style.cursor = "pointer";
      retry.style.fontFamily = "inherit";
      retry.addEventListener("click", function () {{ location.reload(); }});
      msg.appendChild(retry);
    }}
    function capture(action) {{
      return function (event) {{
        if (ready) return;
        event.preventDefault();
        if (stopped || pending) return;
        pending = action;
      }};
    }}
    if (accept) accept.addEventListener("click", capture("accept"));
    if (reject) reject.addEventListener("click", capture("reject"));
    window.addEventListener("pagehide", function () {{
      stopped = true;
      if (controller) controller.destroy();
    }});
    window.addEventListener("pageshow", function (event) {{
      if (event.persisted && stopped) location.reload();
    }});
    import("{static_fingerprint.module_url("modules/turnos/offer.mjs")}").then(function (module) {{
      if (stopped) return;
      controller = module.mount({{ token: token, initialAction: pending }});
      ready = true;
      pending = null;
    }}).catch(fail);
  }})();
  </script>
"""
    return site_shell._shell(f"Se liberó un turno de {servicio} | {theme.brand_name()}", "solicitudes",
                           body, f"Se liberó un turno más cercano de {servicio}.",
                           chrome="focus", index=False)


def build_offer_page(token):
    """Pure domain function behind GET /appointment/offer/<token>: builds and
    returns the HTML body (str, same as `site_shell._shell()` itself
    returns) for whichever screen applies — never touches a handler/request
    at all, unlike `appointment_confirm.build_confirm_page` (this page's
    content never depends on the caller's session/cookies). Native-migration
    campaign (docs/roadmap/native-migration-campaign-20260826.md):
    `appointment_offer_router.py`'s native GET endpoint calls this function
    directly; it is the only path that renders this page."""
    if not token or not _TOKEN_RE.match(token):
        return _unavailable_page()

    o = _offer_for(token)
    if not o:
        return _unavailable_page()

    servicio = _service_name(o.get("servicio_id"))
    centro = _center_name(o.get("centro_slug"))
    cuando = _friendly_appointment(o.get("slot_dt"))

    if o.get("estado") == "aceptada":
        return _confirmed_page(servicio, centro, cuando)

    ahora = datetime.now(timezone.utc)
    vencida = o.get("estado") != "ofrecida" or not o.get("expires_at") or ahora >= o["expires_at"]
    if vencida:
        return _unavailable_page(o.get("servicio_id"))

    return _offer_available_page(token, servicio, centro, cuando)


# ---- POST /api/offer/<token>/accept ---------------------------------------

def _reassign_or_expire(oferta, sid, hsi_result):
    """Moves the request onto the offered slot, recorded in
    `appointment_events` as the patient's waitlist acceptance. If another
    approval already holds that slot (`SlotConflict`), the request stays
    where it was and the offer expires — same 410 as a stale offer."""
    try:
        return booking_store.reassign_appointment(
            sid, oferta["slot_dt"], hsi_result, actor="paciente:lista_espera",
            slot_capacity=1)
    except booking_store.SlotConflict:
        try:
            if oferta.get("id") is not None:
                store.expire_offer(oferta["id"])
        except Exception:  # noqa: BLE001
            pass
        raise OfferClaimFailed("vencida")


def accept_offer(request, token):
    """Domain function behind POST /api/offer/<token>/accept:
    `request` is only forwarded to `notifications.notify_approved()`
    (best-effort, failures swallowed exactly like today — the reassignment
    already persisted by then) — nothing here writes through `handler`
    itself. Returns the solicitud_id (`None` when the offer's own waitlist
    row has no request to reassign — accepted anyway, idempotent for anyone
    retrying the link, shouldn't happen in production) on success; raises
    TokenInvalid/OfferClaimFailed otherwise."""
    if not token or not _TOKEN_RE.match(token):
        raise TokenInvalid()

    ok, oferta = waitlist.claim(token)
    if not ok:
        raise OfferClaimFailed(oferta)

    le = store.waitlist_row(oferta["lista_espera_id"])
    sid = le.get("solicitud_id") if le else None
    row = booking_store.get_booking_request(sid) if sid else None
    if not row:
        return None
    if row.get("estado") != "aprobada":
        # Cancelled or rejected since the offer went out: never move a
        # terminal request nor create anything for it in the EMR.
        try:
            if oferta.get("id") is not None:
                store.expire_offer(oferta["id"])
        except Exception:  # noqa: BLE001
            pass
        raise OfferClaimFailed("vencida")

    from app.modules.turnos import booking_requests
    if row.get("hsi_appointment_id"):
        # Linked EMR appointments move through their own remote record
        # (same contract as booking_patient._reschedule_core); a create
        # here duplicated the appointment in HSI.
        emr = booking_requests._emr_sync_enabled()
        updated = _reassign_or_expire(
            oferta, sid, {"estado": "pendiente" if emr else "skip"})
        if emr:
            try:
                from app.modules.turnos import hsi_journal
                hsi_journal.enqueue_reassign(row, oferta["slot_dt"])
                hsi_journal.replay(limit=1)
                updated = booking_store.get_booking_request(sid) or updated
            except Exception:  # noqa: BLE001 — journal persists; retry handles outage
                pass
    else:
        # Not linked yet: the journal lands the request's one create on the
        # accepted slot — a direct create here duplicated it in the EMR when
        # the approval's response had been lost (hsi.md, 2026-09-24).
        emr = booking_requests._emr_sync_enabled()
        updated = _reassign_or_expire(
            oferta, sid, {"estado": "pendiente" if emr else "skip"})
        if emr:
            try:
                from app.modules.turnos import hsi_journal
                hsi_journal.replay_unlinked_move(updated)
                updated = booking_store.get_booking_request(sid) or updated
            except Exception:  # noqa: BLE001 — the durable intent retries
                pass
    try:
        from app.notifications import send as notifications
        notifications.notify_approved(updated, request)
    except Exception:  # noqa: BLE001 — the reassignment ALREADY persisted, never break here
        pass

    return sid


# ---- POST /api/offer/<token>/reject --------------------------------------

def reject_offer_action(request, token):
    """Domain function behind POST /api/offer/<token>/reject:
    `request` is only forwarded to `waitlist.reject_offer()`. Returns the ok
    boolean `waitlist.
    reject_offer()` itself returns (a `False` here still lands as a 200,
    not an error status — exactly like today); raises TokenInvalid for a
    bad/missing token."""
    if not token or not _TOKEN_RE.match(token):
        raise TokenInvalid()
    ok, _motivo = waitlist.reject_offer(token, handler=request)
    return ok
