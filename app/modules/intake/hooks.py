"""modules/intake/hooks.py — intake's named integration hooks.

The bodies of the "entrevista previa" integration points the four
production callers used to reach inline behind their own
`app_modules.is_enabled("intake")` gate — the two turnos files by importing
`modules.intake` directly (booking_requests: `intake_admin` +
`interview_patient`; doctor_appointments: `intake_admin`), while
admin_panel's doctor_patients and ai's chart_summary never imported it at
all and read the interview tables straight through `core.db`: extracted
here 2026-08-16 (the hook-boundary slice that closed docs/modules.md's
"remaining scatter sites" list) and exposed under the names app.py's
startup() registers. A caller now does `app_modules.hooks.call(name, ...)`
and nothing else — the registry itself filters by intake being enabled AND
healthy RIGHT NOW (same live, no-restart contract every other module gate
has) and swallows any exception a body raises, which replaces the
best-effort fallback each caller used to hand-roll around its own gate.

Behavior-preserving, NOT a verbatim move. The exact deltas:
  * each caller's `is_enabled("intake")` gate is gone — the registry's
    enabled-and-healthy filter is the one gate now (so a caller whose body
    returns a fallback value returns it via "no hook fired", not via a
    private early return);
  * the two identical five-field `entrevista` dict literals the day and
    request views hand-built became one `_estado_publico()` (the QR view's
    own dict adds `nombre`/`hora` on top and stays hand-rolled — see
    `interviews_unmatched_for_day`);
  * `booking_requests._entrevista_field` still resolves the doctor doc
    caller-side (`catalog.doctor_doc(..., tolerant=True)`) and passes it IN — the offer
    body takes the doc, it does not resolve it;
  * `interview_patient_token_for_request` gained registry-level enablement
    it never had as a direct import; reachable behavior is unchanged
    because its only caller path (notifications/send.py `_entrevista_cta`)
    calls `_entrevista_field` first and never reaches the token when the
    module is off;
  * the db-read granularity is preserved: a failing read inside a body
    degrades exactly that one piece of data (e.g. `completada: False` with
    the offer still attached), and the registry's swallow replaces the
    caller's old try/except — a body that dies whole returns no result,
    and the caller's `res[0] if res else FALLBACK` is the old fallback.

Dependency direction: everything in here may import platform core (db,
config, tz) and `modules.turnos` — intake's manifest declares
`requires: ["turnos", "chat"]` (turnos: `doctor_of` below;
chat: `interview_agent.py`'s chat-language import), so both are declared
edges in the ALLOWED direction (it used to be backwards: turnos imported
intake). The doctor-doc resolution stays
`doctor_appointments.doctor_of` (resolved by attribute at call time, so a
`mock.patch("modules.turnos.doctor_appointments.doctor_of", ...)` in a
test keeps working) because that is the exact tolerant resolver
`_add_triage` always used — same for `intake_admin.enabled_for`, patched
by test_cola_dia today.

Cost: every `hooks.call()` re-runs `ensure_started() → list_modules() →
_raw_manifests()`, an UNCACHED walk of every module.json — measured
2026-08-16 at ~2.6 ms per integration point on this repo's 3 modules
(vs ~0.16 ms for the old inline gate; ~7 ms pure-registry for #cola's
3-hook request). The db-backed bodies cost the same as before (same
queries). Bound recorded in docs/modules.md; the lever if it ever matters
is memoizing the manifest walk without breaking the live enable/disable
re-check contract.
"""

from app.core import tz as _tz

_ART = _tz.tzinfo()  # installation timezone (zoneinfo, DST-safe — see tz.py)


def _estado_publico(iv, confirmadas=None):
    """The interview state dict every appointment-facing consumer attaches
    (`_add_triage` / `_add_triage_by_request` / `_entrevistas_sin_turno`
    always built the same five fields — one shape now that they live
    together). `confirmadas` is `db_clinical.consultations_confirmed_for` over the
    same interviews."""
    return {
        "id": iv.get("id"),
        "triage": iv.get("triage"),
        "resumen": iv.get("summary"),
        "estado": iv.get("status"),
        # "Actualizar entrevista" (2026-08-01): the patient added new
        # information after the first close — the summary shown here is
        # already the re-analyzed one.
        "actualizada": bool(iv.get("reopened_at")),
        # The doctor already confirmed the consultation (problems.md №1): the
        # queue says «Atendido» and opens it read-only, never «Iniciar
        # consulta» again. Read from `consultations`, the one source.
        "consulta_confirmada": iv.get("id") in (confirmadas or {}),
    }


def _confirmadas(entrevistas):
    """Same db-read granularity as the rest of this file: a failing read
    degrades only this piece (no «Atendido» mark), never the triage."""
    from app.core import db_clinical
    try:
        return db_clinical.consultations_confirmed_for([iv.get("id") for iv in entrevistas])
    except Exception:  # noqa: BLE001
        return {}


def interview_enabled_for(medico_doc):
    """Hook "interview_enabled_for": True when the pre-consult interview is
    on for THIS doctor — their own override or the system default
    (`intake_admin.enabled_for`). Extracted from
    doctor_appointments._intake_on_for (body unchanged; the caller's old
    module-level gate moved into the registry); decides whether the
    columns that only make sense with it (triage, summary) are offered
    at all."""
    from app.modules.intake import intake_admin
    return bool(intake_admin.enabled_for(medico_doc))


def interview_state_for_day(grupos, dia):
    """Hook "interview_state_for_day": attaches `entrevista` (triage +
    summary) to every appointment of the day whose doctor has the effective
    toggle on, in ONE query for the whole day — extracted from
    doctor_appointments._add_triage (phase 4; the hand-built dict literal
    became `_estado_publico()`). Doctor docs are resolved once
    via the catalog, not per row, then `enabled_for` decides per doctor.
    Best-effort by contract — the day view must never go down over an
    auxiliary piece of data (an exception here is swallowed by the
    registry, same as the caller's old try/except)."""
    from app.core import db_clinical
    doctor_ids = sorted({g.get("medico_id") for g in grupos if g.get("medico_id")})
    if not doctor_ids:
        return
    from app.modules.intake import intake_admin
    from app.modules.turnos import doctor_appointments
    entrevistas = db_clinical.interviews_for_day(doctor_ids, dia)
    if not entrevistas:
        return
    by_request = {}
    for iv in entrevistas:
        rid = iv.get("request_id")
        if rid is not None:
            by_request[int(rid)] = iv
    if not by_request:
        return
    docs_por_medico = {mid: doctor_appointments.doctor_of(mid) for mid in doctor_ids}
    confirmadas = _confirmadas(by_request.values())
    for g in grupos:
        mid = g.get("medico_id")
        if not mid or not intake_admin.enabled_for(docs_por_medico.get(mid)):
            continue
        for t in g.get("turnos") or []:
            iv = by_request.get(t.get("id"))
            if not iv:
                continue
            t["entrevista"] = _estado_publico(iv, confirmadas)


def interview_state_by_requests(turnos, medico_doc):
    """Hook "interview_state_by_requests": attaches `entrevista` to each
    appointment, keyed by request id instead of by day — "Mis turnos" spans
    14 days, so the day-bounded query `interview_state_for_day` uses would
    need 14 round trips. Extracted from
    doctor_appointments._add_triage_by_request (the caller's module-level
    gate now lives in the registry; the per-doctor `enabled_for` check
    below is the one it always had). Best-effort by contract:
    the list must never go down over an auxiliary piece of data."""
    if not turnos:
        return
    from app.modules.intake import intake_admin
    if not intake_admin.enabled_for(medico_doc):
        return
    from app.core import db_clinical
    por_turno = db_clinical.interviews_by_request_ids([t.get("id") for t in turnos])
    if not por_turno:
        return
    confirmadas = _confirmadas(por_turno.values())
    for t in turnos:
        iv = por_turno.get(t.get("id"))
        if not iv:
            continue
        t["entrevista"] = _estado_publico(iv, confirmadas)


def interviews_unmatched_for_day(medico_id, medico_doc, dia):
    """Hook "interviews_unmatched_for_day": [{"id","nombre","hora",
    "triage","resumen","estado","actualizada"}, ...] — QR interviews for
    THIS doctor on that day that matched no appointment
    (`db_clinical.interviews_unmatched_for_day` — same name as this hook, it is the
    read the body wraps). Only called for the 'medico' role, with the
    module enabled and the effective toggle on (same gates as
    `interview_state_for_day`). Extracted from
    doctor_appointments._entrevistas_sin_turno (body unchanged — this
    view's dict stayed hand-rolled, it has `nombre`/`hora` fields of its
    own). Best-effort — never takes down
    the queue (an exception is swallowed by the registry → the caller's [])."""
    from app.modules.intake import intake_admin
    if not intake_admin.enabled_for(medico_doc):
        return []
    from app.core import db_clinical
    filas = db_clinical.interviews_unmatched_for_day(medico_id, dia)
    confirmadas = _confirmadas(filas)
    out = []
    for iv in filas:
        creado = iv.get("created")
        # The QR identity step has been DNI-only since 2026-08-01 (see
        # docs/architecture/entrevista-previa.md): a name is only known if
        # the visitor entered logged in (entered_name/entered_surname come
        # from their account) — the anonymous fallback-DNI visitor has no
        # name on the row, so the doctor still has to be able to identify
        # them here.
        nombre = " ".join(x for x in (iv.get("entered_name"), iv.get("entered_surname")) if x).strip()
        if not nombre and iv.get("dni"):
            nombre = f"DNI {iv.get('dni')}"
        out.append({
            "id": iv.get("id"),
            "nombre": nombre or None,
            "hora": creado.astimezone(_ART).strftime("%H:%M") if creado else None,
            "triage": iv.get("triage"),
            "resumen": iv.get("summary"),
            "estado": iv.get("status"),
            "actualizada": bool(iv.get("reopened_at")),
            "consulta_confirmada": iv.get("id") in confirmadas,
            # Never leaves the server: the caller writes the access-log row with it and drops it
            # (`doctor_appointments._entrevistas_sin_turno`).
            "_dni": iv.get("dni"),
        })
    return out


def interview_config(medico_id, medico_doc):
    """Hook "interview_config": {"efectivo","override","cartel_url"} for the
    compact #cola header (phase 7, 'medico' role only) — unlike
    `interviews_unmatched_for_day`, this is computed ALWAYS while the
    module is on (even with the effective toggle off: that is exactly
    what lets it be turned on from here). None if the module is
    disabled entirely — that None is the CALLER's "no result"
    now: when intake is off the hook never fires, so this body only ever
    runs enabled. Extracted from
    doctor_appointments._entrevista_config. Best-effort — never takes down
    the queue."""
    from app.modules.intake import intake_admin
    override = (medico_doc or {}).get("entrevista_habilitada")
    if not isinstance(override, bool):
        override = None
    return {
        "efectivo": intake_admin.enabled_for(medico_doc),
        "override": override,
        "cartel_url": f"/api/admin/intake/door/{medico_id}",
    }


def interview_offer_for_request(sid, doctor_doc, incluir_estado=False):
    """Hook "interview_offer_for_request": {"ofrecer","url"} (+
    "completada" on the detail) for the pre-consult interview CTA — None
    when the effective toggle (doctor override or system default,
    `intake_admin.enabled_for`) is off, so callers just do
    `if resp.entrevista?.ofrecer`. Extracted from
    booking_requests._entrevista_field (the doctor doc arrives already
    resolved — the caller's `catalog.doctor_doc` lookup stays
    with it). Best-effort: intake being absent/broken must never break a
    booking response (the registry swallows; the caller falls back to
    None).

    No known doctor (nobody assigned and no chosen block, or an id outside
    the catalog) -> no offer, whatever the system default says (2026-11-10):
    the interview is read by the appointment's doctor, the panel shows the
    switch per doctor only, and a hospital that had switched every doctor
    off was still inviting the patients of doctor-less appointments."""
    from app.modules.intake import intake_admin
    if not isinstance(doctor_doc, dict) or not intake_admin.enabled_for(doctor_doc):
        return None
    out = {"ofrecer": True, "url": f"/entrevista/{sid}"}
    if incluir_estado:
        try:
            from app.core import db_clinical
            existente = db_clinical.interview_for_request(sid)
        except Exception:  # noqa: BLE001
            existente = None
        out["completada"] = bool(existente and existente.get("status") == "done")
    return out


def interview_patient_token_for_request(sid):
    """Hook "interview_patient_token_for_request": `patient_token` for an
    EMAIL/WhatsApp link (2026-08-03: "click → straight into the interview
    chat, no login"). Lazily creates (or reuses) the interview so the
    outbound message can carry the same opaque-token-is-authentication the
    chat routes already trust (`interviews.patient_token`). Only ever
    reached from notifications/send.py's outbound-message builders
    (`_entrevista_cta`), and only
    after `interview_offer_for_request` already said yes — the offer gate
    is what keeps this path dark while intake is disabled (same pairing
    the direct imports had; see the module docstring for the one delta:
    the module-level gate is the registry's now). Best-effort: None on
    any failure (missing row, DB down) — the caller degrades to a
    session-only link. Extracted from
    booking_requests._entrevista_patient_token."""
    from app.core import db_booking
    row = db_booking.get_booking_request(sid)
    if not row:
        return None
    from app.modules.intake import interview_patient
    interview = interview_patient._find_or_create_interview(row, sid)
    return interview.get("patient_token")


def interviews_of_doctor(medico_id):
    """Hook "interviews_of_doctor": the raw interview rows whose doctor_id
    is this doctor — includes the ones that never matched a request (QR
    with no appointment). Extracted from the gated read in
    admin_panel/doctor_patients._build («Mis pacientes»); the caller's
    `_intake_disponible` — a bare `is_enabled` check, doctor_patients
    never imported `modules.intake` — is what the registry replaced (the
    `db_clinical.interviews_of_doctor` call itself moved from the caller into this
    body). Best-effort: if
    intake is off or this read fails, the roster keeps showing the
    patients with appointments, only without the interview column (an
    exception is swallowed by the registry → the caller's [])."""
    from app.core import db_clinical
    return db_clinical.interviews_of_doctor(medico_id)


def interviews_compact_by_dni(dni):
    """Hook "interviews_compact_by_dni": the patient's pre-consult
    interviews, compacted for the chart summary's AI context. Extracted
    from ai/chart_summary._entrevistas. Best-effort: module
    off or a failed read → empty list (the registry swallows → the
    caller's [])."""
    from app.core import db_slots
    filas = db_slots.interviews_by_dni(dni)
    out = []
    for iv in filas:
        creado = iv.get("created")
        out.append({
            "fecha": creado.astimezone(_ART).date().isoformat() if creado else None,
            "triage": iv.get("triage"),
            "resumen": iv.get("summary"),
            "brief": iv.get("brief"),
            "banderas_rojas": iv.get("red_flags"),
            "estado": iv.get("status"),
            "medico_id": iv.get("doctor_id"),
        })
    return out


def case_chat_bind(token, request_id, usuario=None):
    """«Contanos tu caso» (2026-11-07): the booking request just created
    takes the case chat its wizard sent (`caso_chat` token) — summary and
    transcript into `booking_requests.case_chat`. Returns the patient's
    words for the request's note, or None (no chat; module off → no hook
    fired → the request keeps whatever `caso` text it had)."""
    from app.modules.intake import case_chat
    return case_chat.bind(token, request_id, usuario)


# The ONE registration table: app.py's startup() walks it, so a hook name
# and its body can never drift apart (adding an integration point = add
# the function + one line here).
REGISTRY = (
    ("interview_enabled_for", interview_enabled_for),
    ("interview_state_for_day", interview_state_for_day),
    ("interview_state_by_requests", interview_state_by_requests),
    ("interviews_unmatched_for_day", interviews_unmatched_for_day),
    ("interview_config", interview_config),
    ("interview_offer_for_request", interview_offer_for_request),
    ("interview_patient_token_for_request", interview_patient_token_for_request),
    ("interviews_of_doctor", interviews_of_doctor),
    ("interviews_compact_by_dni", interviews_compact_by_dni),
    ("case_chat_bind", case_chat_bind),
)
