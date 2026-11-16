"""booking_queue.py — the desk's review queue: GET /api/admin/requests (with the suggested doctor and the
slot the patient asked for) and GET /api/admin/refeps (license lookup).

Split out of booking_requests.py (TASK-60, 2026-11-06). The row shape (`_admin_row`), the catalog and the
settings stay in booking_requests.py and are called as `bkr.<name>` at call time, so a test that patches
them there still reaches this module.
"""

import re
from datetime import timezone

from app.modules.turnos import booking_requests_store as store
from app.auth import admin_auth
from app import connectors
from app.connectors.refeps import refeps  # RefepsError + record shaping (mejor_match/simplify)
from app.core import format
from app.core import i18n
from app.modules.turnos import schedule_policy as _schedule_policy
from app.modules.turnos import booking_requests as bkr


# ---- GET /api/admin/requests -------------------------------------------------
#
# medico_sugerido (2026-07-17, handoff: "always assign one automatically —
# the one that has an available appointment"): for each PENDING request, the
# first doctor from the service's cluster+center with a REAL schedule
# covering the default appointment time gets suggested (same criterion as
# admin.html:turnoDefault) and with no clash against an already-approved
# request — a free slot is never "guessed". A single batched query to
# listar_aprobadas_futuras, not one per request.

def _approved_by_doctor_art():
    """{"id:<id>" | "nombre:<normalized>": {(dia_iso, hora)}} in ART of approved requests with
    a future appointment — a single batched query (see _admin_list), same
    pattern as slot_holds._approved_future_slots. Best-effort: DB down
    (shouldn't happen, the native router maps DB failures to 503) -> empty
    dict."""
    from app.patient_web import site_base
    out = {}
    try:
        rows = store.list_approved_future()
    except store.DbUnavailable:
        return out
    for r in rows:
        nombre = r.get("medico_asignado")
        mid = r.get("medico_asignado_id")
        dt = r.get("turno_dt")
        if not (nombre or mid) or dt is None:
            continue
        local = dt.astimezone(bkr._ART) if dt.tzinfo else dt.replace(tzinfo=timezone.utc).astimezone(bkr._ART)
        # By id when the row has one (2026-10-23); the name only for legacy
        # rows without it — _doctor_busy_slots reads both keys.
        key = f"id:{mid}" if mid else "nombre:" + site_base._norm_name(nombre)
        out.setdefault(key, set()).add((local.strftime("%Y-%m-%d"), local.strftime("%H:%M")))
    return out


def _doctor_busy_slots(ocupados_por_medico, medico):
    """Taken (dia, hora) of a catalog doctor: its id's rows plus legacy
    name-only rows with its exact name."""
    from app.patient_web import site_base
    return (set(ocupados_por_medico.get(f"id:{medico.get('id')}", ()))
            | set(ocupados_por_medico.get("nombre:" + site_base._norm_name(medico.get("nombre")), ())))


# In the themed clusters (cardiologia, salud-mental…) sharing a cluster IS
# sharing a specialty; in the catch-all buckets, different specialties
# coexist (in `otras` a pulmonologist does NOT see Neurology patients; in
# `diagnostico` each doctor covers a different study — the same reason
# agenda appointments go without a name), so there it's additionally
# required that the doctor's specialty matches the service name. The rule
# lives in `schedule_policy` so assignment and schedule ownership can never
# disagree about which doctors a service has.
_CATCHALL_CLUSTERS = set(_schedule_policy.CATCHALL_CLUSTERS)
_specialty_matches_service = _schedule_policy.specialty_matches_service


def _requested_doctor_name(s, pedido):
    """Name (catalog, by id) of the doctor the PATIENT asked for, or "" when
    nobody is known. Never `medico_asignado`: on a pending request that is
    often the desk's own suggestion, not the patient's choice (the reference-deployment counterpart,
    2026-10-24: the panel must say what she tried to book). The owner of the
    chosen slot wins (resolved server-side, the client-sent id is forgeable);
    without a slot, the block the patient picked (`slot_medico_id`)."""
    try:
        from app.patient_web import catalog
        mid = None
        if pedido and pedido.get("dia") and pedido.get("hora"):
            mid = bkr._resolve_slot_doctor(s.get("servicio_id"), pedido["dia"], pedido["hora"])
        mid = mid or s.get("slot_medico_id")
        medico = catalog.doctor_by_id(mid) if mid else None
        return format.person_name((medico or {}).get("nombre")) or ""
    except Exception:  # noqa: BLE001 — the queue never breaks over a label
        return ""


def _suggested_doctor(s, ocupados_por_medico, pedido=None):
    """{"nombre","slot_iso"} of the service's next available doctor.

    `pedido` ({"dia","hora"}, the slot the patient picked in the wizard)
    wins over everything while some doctor still has it free — the result
    then carries `"pedido": True`, preferring the doctor already assigned.
    Otherwise the patient's preference (morning/afternoon/night) wins when
    there's at least one opening in that band; within the band, the earliest
    chronological appointment wins. If there's no preferred opening, the
    first available one is used. Lab work stays without a doctor by design.
    """
    from app.patient_web import site_availability, site_base
    try:
        from app.patient_web import catalog
        medicos = catalog.doctors() or []
    except Exception:  # noqa: BLE001
        return None
    servicio = bkr._service_full(s.get("servicio_id"))
    if not servicio:
        return None
    if bkr._is_lab(servicio):
        # Explicit rule, not an accident of "no candidates": lab work has
        # no doctor, it has the roster's technician.
        return None
    from app.modules.turnos import doctor_windows, schedule_policy
    candidatos = [m for m in schedule_policy.pool(servicio, medicos, keep=schedule_policy.person)
                  if schedule_policy.has_schedule(servicio) or schedule_policy.doctor_has_schedule(m)]
    if servicio.get("cluster") in _CATCHALL_CLUSTERS:
        candidatos = [m for m in candidatos
                      if _specialty_matches_service(m.get("especialidad"), servicio.get("nombre"))]
    disponibles = []
    pref = bkr._norm_specialty(s.get("preferencia_horario"))

    def _franja(hora):
        try:
            h = int(str(hora).split(":", 1)[0])
        except (TypeError, ValueError):
            return ""
        if h >= 19 or h < 6:
            return "noche"
        return "tarde" if h >= 13 else "manana"

    pref_franja = "noche" if "noche" in pref else "tarde" if "tarde" in pref else "manana" if "manana" in pref else ""
    for m in candidatos:
        try:
            slots = [slot for filtered in doctor_windows.for_service(m, servicio)
                     for slot in site_availability._agenda_slots(filtered, service=servicio,
                         doctor=None if schedule_policy.has_schedule(servicio) else m)]
        except Exception:  # noqa: BLE001
            continue
        ocupados = _doctor_busy_slots(ocupados_por_medico, m)
        for slot in slots:
            dia_iso, hora = slot.get("dia"), slot.get("hora")
            if not dia_iso or not hora or (dia_iso, hora) in ocupados:
                continue
            disponibles.append((dia_iso, hora, str(m.get("nombre") or ""), _franja(hora)))
    if not disponibles:
        return None
    if pedido and pedido.get("dia") and pedido.get("hora"):
        asignado = site_base._norm_name(s.get("medico_asignado"))
        exactos = [d for d in disponibles if (d[0], d[1]) == (pedido["dia"], pedido["hora"])]
        if exactos:
            exactos.sort(key=lambda d: (0 if asignado and site_base._norm_name(d[2]) == asignado else 1, d[2]))
            dia_iso, hora, nombre, _ = exactos[0]
            return {"nombre": nombre, "slot_iso": f"{dia_iso}T{hora}:00-03:00", "pedido": True}
    disponibles.sort(key=lambda x: (0 if pref_franja and x[3] == pref_franja else 1, x[0], x[1], x[2]))
    dia_iso, hora, nombre, _ = disponibles[0]
    return {"nombre": nombre, "slot_iso": f"{dia_iso}T{hora}:00-03:00"}


def _add_case_summaries(admin_rows):
    """«Contanos tu caso» (2026-11-07): each request that came with a case
    chat carries its summary (what the patient wants, urgency, why, key
    facts) as `caso_resumen` and `caso_chat: true`; the conversation itself
    is opened one request at a time (GET .../case-chat, a logged read).
    One batched read; best-effort — the queue never goes down over it."""
    try:
        from app.core import db_chat
        resumenes = db_chat.case_chat_summaries([r.get("id") for r in admin_rows])
    except Exception:  # noqa: BLE001
        return
    for r in admin_rows:
        resumen = resumenes.get(r.get("id"))
        if resumen:
            r["caso_resumen"] = resumen
            r["caso_chat"] = True


def staff_case_chat(identity, sid):
    """GET /api/admin/requests/<id>/case-chat — «Ver conversación»: the
    whole «Contanos tu caso» chat of one request. Clinical data, so the
    open — or the refused attempt on a request this account can't work —
    lands in the clinical access log (`caso`), same rule as the referral."""
    row, err = bkr._staff_row_or_error(identity, sid)
    vista = row if row is not None else store.get_booking_request(sid)
    if vista:
        from app.core import access_log
        access_log.record(identity, "caso", vista.get("dni"), target_id=sid,
                          allowed=err is None)
    if err is not None:
        return err
    from app.core import db_chat
    data = db_chat.case_chat_get(sid) or {}
    if not data.get("mensajes"):
        return 404, {"error": i18n.t_or("admin.srv.error.caso_sin_chat",
                                        "esta solicitud no tiene conversación", i18n.active())}
    return 200, {"resumen": data.get("resumen") or {}, "mensajes": data["mensajes"]}


def _admin_list_core(identity, qs):
    """GET /api/admin/requests — the review queue. Typed `identity`
    (already resolved by the caller, the native `_require_admin` gate in
    `booking_requests_router.py`) and `qs` (parsed query dict, the same
    `parse_qs`/`{key: [values]}` shape the router's `_qs_like_legacy`
    builds) in, `(status, payload)` out."""
    estado = (qs.get("status", [""])[0] or "").strip() or None
    if identity.get("rol") == "medico":
        estado = "pendiente"
    # limit/offset are optional (the default view is bounded on its own —
    # see list_admin's docstring); a caller that wants a page of a
    # single-status tab can ask for it explicitly.
    try:
        limit = int(qs.get("limit", [""])[0] or 0) or None
    except (TypeError, ValueError):
        limit = None
    try:
        offset = int(qs.get("offset", [""])[0] or 0)
    except (TypeError, ValueError):
        offset = 0
    rows = store.list_admin(estado, limit=limit, offset=offset)
    total = store.count_admin(estado)
    if identity.get("rol") == "medico":
        from app.modules.turnos import doctor_appointments
        medico_id = store.operator_doctor_id(identity["usuario"])
        medico = doctor_appointments.doctor_of(medico_id) if medico_id else None
        nombre = (medico or {}).get("nombre") or ""
        rows = [r for r in rows
                if medico_id and doctor_appointments._belongs_to(r, medico_id, nombre)]
    # Center-scoped access (2026-08-31): filter rows to permitted centers.
    # Same pattern as _mark_attendance_core and _staff_row_or_error.
    centros_ok = admin_auth.centros_permitidos(identity)
    if centros_ok is not None:
        rows = [r for r in rows if (r.get("centro_slug") or "") in centros_ok]
        total = len(rows)  # Update total after filtering
    admin_rows = [bkr._admin_row(r) for r in rows]
    from app.modules.turnos import doctor_appointments as _da
    nombres = _da.resolver_names(r.get("resuelta_por") for r in admin_rows)
    for r in admin_rows:
        if r.get("resuelta_por") in nombres:
            r["resuelta_por_nombre"] = nombres[r["resuelta_por"]]
    # A single batched read so the table shows no-shows, the block, and
    # history without one request per patient. The current mark stays on the
    # request, the log lives under ``asistencia.historial``.
    try:
        asistencia = store.attendance_summary_by_dnis(
            [r.get("dni") for r in admin_rows], bkr._no_show_limit())
        for r in admin_rows:
            r["asistencia_resumen"] = asistencia.get(
                str(r.get("dni") or ""), {"visitas": 0, "presentes": 0, "faltas": 0,
                                            "bloqueado_web": False, "historial": []})
            r["no_show_limit"] = bkr._no_show_limit()
    except Exception:  # noqa: BLE001 — the clinical queue never goes down over the auxiliary summary
        for r in admin_rows:
            r["asistencia_resumen"] = {"visitas": 0, "presentes": 0, "faltas": 0,
                                        "bloqueado_web": False, "historial": []}
            r["no_show_limit"] = bkr._no_show_limit()
    # The same ranking that orders the patient list, so the queue and the
    # profile say the same thing about the same patient. Best-effort by contract.
    from app.modules.turnos import patient_ranking
    rank = patient_ranking.by_dnis([r.get("dni") for r in admin_rows])
    for r in admin_rows:
        r["ranking"] = rank.get(str(r.get("dni") or ""), patient_ranking.neutral())
    pendientes = [r for r in admin_rows if r.get("estado") == "pendiente"]
    if pendientes:
        ocupados_por_medico = _approved_by_doctor_art()
        for r in pendientes:
            # The exact slot the patient picked (its hold). The panel shows it
            # and preselects it while free; taken → says so, preselects nothing
            # (journey 2026-09-16: approval booked the first free slot instead).
            try:
                pedido = store.hold_of_booking_request(r["id"])
            except Exception:  # noqa: BLE001 — best-effort, like the suggestion
                pedido = None
            r["turno_pedido"] = None
            r["medico_pedido"] = _requested_doctor_name(r, pedido)
            try:
                r["medico_sugerido"] = _suggested_doctor(r, ocupados_por_medico, pedido)
                if pedido and pedido.get("dia") and pedido.get("hora"):
                    r["turno_pedido"] = {
                        "dia": pedido["dia"], "hora": pedido["hora"],
                        "libre": bool(r["medico_sugerido"] and r["medico_sugerido"].get("pedido"))}
                # A GET never assigns: the assignment makes the patient the doctor's, so it leaves its
                # access-log row first and runs through `_assign_core` (POST, CSRF) — the panel preselects
                # this suggestion and approving applies it (security review 2026-11, M-2 bypass).
            except Exception:  # noqa: BLE001 — the suggestion never breaks the queue
                r["medico_sugerido"] = None
    # Connector visibility comes from the same explicit registry that owns
    # outbound synchronization.  The legacy emergency flag alone must never
    # resurrect vendor UI after the connector was disabled.
    # `total` is the count BEFORE the doctor-role's extra client-side filter
    # (count_admin doesn't know about "belongs to this doctor") — it's
    # the same approximation the medico view already lived with when the
    # list itself had no LIMIT, so a doctor's own count was always "how many
    # pending exist", not "how many are mine". `len(admin_rows)` next to it
    # tells the UI exactly how many actually rendered.
    # K10: cada solicitud lleva la lectura IA de su orden (diagnóstico,
    # prácticas) — una fila del registro de accesos por paciente mostrado.
    _add_case_summaries(admin_rows)
    from app.core import access_log
    access_log.record_many(identity, "solicitudes",
                           [(r.get("dni"), r.get("id")) for r in admin_rows])

    # Pool de médicos por servicio (2026-10-23): el combo «Médico» y «Derivar
    # a» del panel listan sólo esto — la misma regla que la aprobación verifica.
    # Sin envío del servidor a HSI, la mesa carga las aprobadas con su
    # extensión (2026-10-27, d1122568): «hsi_extension» es la tienda cuando
    # este sitio es uno de los suyos, y entonces el panel ofrece el botón.
    emr = bkr._emr_sync_enabled()
    from app.core import hsi_extension
    return 200, {"solicitudes": admin_rows, "total": total,
                "emr_sync_active": emr,
                "hsi_extension": None if emr else hsi_extension.store_url(),
                # dónde se instala, mientras la computadora no la tenga
                # (the reference-deployment counterpart, 2026-10-27)
                "hsi_instalar": None if emr else hsi_extension.install(),
                "medicos_por_servicio": _doctor_pool_by_service()}


# ---- GET /api/admin/refeps -----------------------------------------------------
#
# Verifies a license number against REFEPS (argentina.gob.ar). Cached by
# license number in Postgres — a handful of doctors is expected, and it
# doesn't change often, so it almost never hits the third-party site again;
# ?force=1 forces a fresh lookup (refresh button in the admin).

def _refeps_payload(items, nombre, checked, source, error=None):
    match, ranked = refeps.mejor_match(items, nombre)
    out = {
        "source": source,
        "checked": checked.isoformat() if hasattr(checked, "isoformat") else checked,
        "found": bool(ranked),
        "match": refeps.simplify(match) if match else None,
        "candidatos": len(ranked),
        "otros": [refeps.simplify(x) for x in ranked if x is not match][:4],
    }
    if error:
        out["error"] = error
    return out


def _admin_refeps_core(qs):
    """GET /api/admin/refeps — verifies a license number against REFEPS
    (auth already checked by the caller). Typed `qs` in, `(status, payload)`
    out."""
    # A referral can carry TWO license numbers in the same stamp ("M.N.
    # 104512 - M.P. 90871"): the FIRST number is searched for (the
    # thousands-separator dots are part of the number: "57.093" → 57093),
    # not the concatenation of all the digits.
    raw = (qs.get("matricula", [""])[0] or "")
    nums = re.findall(r"\d(?:[\d.]*\d)?", raw)
    matricula = nums[0].replace(".", "") if nums else ""
    if not matricula:
        return 400, {"error": i18n.t_or("admin.srv.refeps.falta_matricula", "falta matricula", i18n.active())}
    nombre = (qs.get("medico", [""])[0] or "").strip()
    force = (qs.get("force", [""])[0] or "") in ("1", "true")

    cached = None if force else store.refeps_get(matricula)
    if cached is not None:
        return 200, {"matricula": matricula,
                    **_refeps_payload(cached["items"] or [], nombre,
                                       cached["checked"], "cache")}

    try:
        # The license_lookup connector (refeps by default — registry rewire
        # 2026-08-20). No connector routed (`conn disable license_lookup`) is
        # exactly "the lookup failed": the stale-cache-or-502 branch below.
        adapter = connectors.adapter_for(connectors.LICENSE_LOOKUP)
        if adapter is None:
            raise connectors.ProviderUnavailable("license_lookup: no connector active")
        items = adapter.search_by_license(matricula)
    except (refeps.RefepsError, connectors.ProviderUnavailable) as e:
        stale = store.refeps_get(matricula)
        if stale is not None:  # prefer stale data over nothing at all
            return 200, {"matricula": matricula,
                        **_refeps_payload(stale["items"] or [], nombre,
                                           stale["checked"], "cache-stale", str(e))}
        return 502, {"error": str(e)}
    row = store.refeps_set(matricula, items, ok=True)
    return 200, {"matricula": matricula,
                **_refeps_payload(items, nombre, row["checked"], "live")}


def _doctor_pool_by_service():
    """{servicio_id: [ids de médico]} para el panel: la lista del combo
    «Médico» y la de «Derivar a» salen de acá. Best-effort: sin catálogo,
    dict vacío (el panel deja el médico «a asignar», nunca adivina)."""
    try:
        from app.patient_web import catalog
        medicos = catalog.doctors() or []
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for sid in (bkr._services() or {}):
        out[sid] = [str(m["id"]) for m in bkr._doctor_pool(bkr._service_full(sid), medicos)]
    return out
