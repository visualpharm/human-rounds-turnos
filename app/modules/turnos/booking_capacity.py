"""booking_capacity.py — is the doctor free, and is there room: the ONE owner of «is this doctor busy»
(local approved appointments), the doctor's own schedule check, and the overbooking capacity of a slot.

Split out of booking_requests.py (TASK-60, 2026-11-06). The approval, the panel's moves and offers
(booking_decisions, admin_appointments) and the patient's reschedule (booking_patient) all ask here. The catalog stays in booking_requests.py and is called as `bkr.<name>` at call time, so a test that
patches it there still reaches this module.
"""

from datetime import datetime, timedelta

from app.modules.turnos import booking_requests_store as store
from app.modules.turnos import booking_requests as bkr


def _hsi_minutes(medico_id, desde, hasta, center_slug):
    """Synthetic busy rows for the appointments created straight in HSI
    (`hsi_busy`): they take the doctor's minute like any local approved one.
    A request's own HSI turn is linked to it and `hsi_busy` never counts a
    linked one, so no `exclude_sid` is needed. HSI knows no center, so a
    center-scoped check does not use them (accepted: such a minute is busy for
    the doctor but not for the center's own count)."""
    if not medico_id or center_slug is not None:
        return []
    from app.modules.turnos import hsi_busy
    out = []
    for dia, hora in hsi_busy.busy_of_doctor(medico_id):
        m = datetime.fromisoformat(f"{dia}T{hora}").replace(tzinfo=bkr._ART)
        if desde <= m < hasta:
            out.append((m, {"id": None, "hsi": True, "turno_dt": m}))
    return out


def _doctor_appointments_between(medico, desde, hasta, exclude_sid=None, center_slug=None, medico_id=None):
    """[(minute in ART, row)] of a clinician's local approved appointments in
    [desde, hasta). The ONE owner of «is this doctor busy»: the approval's
    check (`_doctor_appointments_at_time`) and the desk's offers
    (`doctor_busy_minutes`, slot-409) both read it, so a minute the desk offers
    for a doctor is a minute the approval accepts for that doctor.

    The doctor is identified by catalog id (2026-10-23): `medico_id`, or the
    one `medico` resolves to. Two spellings of the same doctor collide; a row
    carrying another doctor's id never does, whatever its stored name says.
    The name only matches legacy rows without an id (or when the wanted
    doctor itself resolves to no catalog id).
    """
    if not (medico or medico_id) or desde is None or hasta is None:
        return []
    try:
        from app.patient_web import site_base
        from app.modules.turnos import doctor_appointments
        out = []
        if medico_id:
            medico = doctor_appointments.display_name({"medico_asignado": medico,
                                                       "medico_asignado_id": medico_id})
        else:
            medico, medico_id = doctor_appointments.resolve_pair(medico)
        wanted_doctor = site_base._norm_name(medico)
        # Only the ones approved in the range (estado+turno_dt index), not the
        # whole history: the doctor match runs over a handful of rows.
        for other in store.approved_in_range(desde, hasta):
            if exclude_sid is not None and other.get("id") == exclude_sid:
                continue
            if center_slug is not None and other.get("centro_slug") != center_slug:
                continue
            if not doctor_appointments.same_doctor(other, medico_id, wanted_doctor,
                                                   site_base._norm_name):
                continue
            dt = other.get("turno_dt")
            if dt is None:
                continue
            actual = dt.astimezone(bkr._ART) if dt.tzinfo else dt.replace(tzinfo=bkr._ART)
            out.append((actual.replace(second=0, microsecond=0), other))
        out += _hsi_minutes(medico_id, desde, hasta, center_slug)
    except store.DbUnavailable:
        raise
    except Exception:  # noqa: BLE001 — normalisation must not break approval
        return []
    return out


def _doctor_appointments_at_time(medico, turno_dt, exclude_sid=None, center_slug=None, medico_id=None):
    """Whether a clinician already has a local approved appointment then."""
    if turno_dt is None:
        return []
    wanted_dt = turno_dt.astimezone(bkr._ART) if turno_dt.tzinfo else turno_dt.replace(tzinfo=bkr._ART)
    wanted_key = wanted_dt.replace(second=0, microsecond=0)
    return [row for minuto, row in _doctor_appointments_between(
                medico, wanted_key, wanted_key + timedelta(minutes=1),
                exclude_sid, center_slug, medico_id=medico_id)
            if minuto == wanted_key]


def doctor_busy_minutes(medico, desde, hasta):
    """{("YYYY-MM-DD", "HH:MM")} in ART the clinician already has between the two
    dates (inclusive) — exactly the minutes the approval refuses with
    `turno_medico_tomado`. Read without the 3 s cache of `slot_holds.taken()`:
    the desk asks right after its own approval."""
    if not medico or desde is None or hasta is None:
        return set()
    inicio = datetime.combine(desde, datetime.min.time(), tzinfo=bkr._ART)
    fin = datetime.combine(hasta + timedelta(days=1), datetime.min.time(), tzinfo=bkr._ART)
    return {(m.strftime("%Y-%m-%d"), m.strftime("%H:%M"))
            for m, _ in _doctor_appointments_between(medico, inicio, fin)}


def _doctor_appointment_busy(medico, turno_dt, exclude_sid=None, center_slug=None, medico_id=None):
    """Whether a clinician has a confirmed appointment at the exact minute."""
    return bool(_doctor_appointments_at_time(medico, turno_dt, exclude_sid, center_slug,
                                             medico_id=medico_id))


def _doctor_appointment_in_schedule(medico, turno_dt, row, medico_id=None, *, staff_capacity=False):
    """Return the exact doctor window for an operator-selected appointment.

    Public availability deliberately hides reserved and overbook capacity, but
    every *named* approval still has to land on an active ART agenda boundary.
    Otherwise a skipped/unavailable HSI handoff could persist a time that HSI
    must reject in exact-slot mode.
    """
    if not medico or turno_dt is None:
        return None, "turno_medico_sin_agenda"
    try:
        from app.patient_web import catalog
        from app.patient_web import site_availability, site_base
        wanted = site_base._norm_name(medico)
        doctors = catalog.doctors() or []
        doctor = (next((m for m in doctors if medico_id and str(m.get("id") or "") == str(medico_id)), None)
                  or next((m for m in doctors if site_base._norm_name(m.get("nombre")) == wanted), None))
        if not doctor:
            return None, "turno_medico_sin_agenda"
        local = turno_dt.astimezone(bkr._ART) if turno_dt.tzinfo else turno_dt.replace(tzinfo=bkr._ART)
        dia, hora = local.date(), local.strftime("%H:%M")
        from app.modules.turnos import doctor_windows, schedule_policy
        service = bkr._service_full(row.get("servicio_id")) or {}
        agendas = []
        if service and (service.get("centro") or schedule_policy.has_schedule(service)):
            # ONE owner resolution, the same one every offer surface uses (the
            # wizard's grid, the panel's «Otro horario…» select): schedule_policy,
            # anchored on the SERVICE's center. The row's stored centro_slug is
            # not an owner signal — a request derived to another service, or
            # created before the service moved centers, keeps a stale slug, and
            # resolving by it swapped in another center's bands and rejected the
            # very slots the panel had just offered (the reference-deployment counterpart, 19/10: a Thursday
            # 09:00 of the doctor's real calendar came back «fuera de agenda»).
            if schedule_policy.has_schedule(service) and staff_capacity:
                # Service ownership controls appointment times. Historic
                # doctor bands can still carry the staff-only extra capacity;
                # they may never authorize a time outside the service agenda.
                own = schedule_policy.doctor_own_agendas(doctor, service)
                service_band, error = _doctor_appointment_in_schedule(medico, turno_dt, row, medico_id)
                if error:
                    return None, error
                if any(k in (service_band.get("cupos") or {}) for k in ("sobreturnos", "overbooking_percent")) or not own:
                    return service_band, None
                agendas = own
            else:
                agendas = schedule_policy.doctor_agendas(doctor, service)
        else:
            # Deleted service, or a legacy one with neither a center nor a
            # schedule: the request's stored center is the only center signal.
            centro = row.get("centro_slug")
            for c in (doctor.get("centros") or []):
                if not centro or c.get("centro") == centro:
                    agendas.append({"franjas": c.get("franjas") or [],
                                    "excepciones": c.get("excepciones") or []})
            if not agendas and doctor.get("agenda"):
                agendas.append(doctor["agenda"])
        agendas = [a for ag in agendas if (a := doctor_windows.service_schedule(ag, row.get("servicio_id")))]
        if not agendas:
            return None, "turno_medico_sin_agenda"
        for agenda in agendas:
            excs = site_availability._day_exceptions(agenda.get("excepciones") or [], dia.isoformat())
            if any(e.get("tipo") == "cerrado" for e in excs):
                return None, "turno_medico_horario_cerrado"
            bands = agenda.get("franjas") or ([agenda] if agenda.get("dias") else [])
            ventanas = [fr for fr in bands
                        if (dia.isoweekday() % 7) in (fr.get("dias") or [])]
            ventanas += [e for e in excs if e.get("tipo") == "extra"]
            for franja in ventanas:
                if not (str(franja.get("desde") or "") <= hora < str(franja.get("hasta") or "")):
                    continue
                if site_availability._time_blocked(hora, excs):
                    return None, "turno_medico_horario_bloqueado"
                inicio_h, inicio_m = (int(x) for x in str(franja["desde"]).split(":"))
                cada = int(franja.get("cada_min") or 20)
                minuto = local.hour * 60 + local.minute
                if cada > 0 and (minuto - (inicio_h * 60 + inicio_m)) % cada == 0:
                    # Keep the day exceptions with this otherwise catalog-owned
                    # window: percentage capacity must omit partially blocked
                    # regular slots as the public generator does.
                    candidate = dict(franja, _overbooking_exceptions=excs)
                    active_percent = int(((candidate.get("cupos") or {}).get("overbooking_percent") or 0)) > 0
                    candidate["_overbooking_overlap"] = any(
                        other is not franja
                        and (active_percent or int(((other.get("cupos") or {}).get("overbooking_percent") or 0)) > 0)
                        and str(other.get("desde") or "") < str(franja.get("hasta") or "")
                        and str(franja.get("desde") or "") < str(other.get("hasta") or "")
                        for other in ventanas)
                    return candidate, None
        return None, "turno_medico_fuera_de_agenda"
    except store.DbUnavailable:
        raise
    except Exception:  # noqa: BLE001 — malformed/missing agenda fails closed
        return None, "turno_medico_sin_agenda"


def _percentage_overbooking_policy(medico, turno_dt, franja, row=None, medico_id=None):
    """Return the atomic database policy for a percentage-configured band.

    The percentage applies to every normal generated appointment in this
    exact band on its ART date, not separately to each minute.  The returned
    window and stable key let persistence serialize competing approvals across
    those different minutes.
    """
    cupos = franja.get("cupos") or {}
    if franja.get("_overbooking_overlap"):
        return False
    if "overbooking_percent" not in cupos:
        return None
    if not medico_id:
        try:
            from app.modules.turnos import doctor_appointments
            medico_id = doctor_appointments.resolve_doctor_id(medico)
        except Exception:  # noqa: BLE001 — free-text legacy doctors use their name
            medico_id = None
    percent = int(cupos.get("overbooking_percent") or 0)
    if percent <= 0:
        return False
    local = turno_dt.astimezone(bkr._ART) if turno_dt.tzinfo else turno_dt.replace(tzinfo=bkr._ART)
    try:
        start_h, start_m = (int(x) for x in str(franja["desde"]).split(":"))
        end_h, end_m = (int(x) for x in str(franja["hasta"]).split(":"))
        cada = int(franja.get("cada_min") or 20)
    except (KeyError, TypeError, ValueError):
        return False
    excs = franja.get("_overbooking_exceptions") or []
    regular_slots = []
    for minute in range(start_h * 60 + start_m, end_h * 60 + end_m, cada):
        hora = f"{minute // 60:02d}:{minute % 60:02d}"
        try:
            from app.patient_web import site_availability
            if site_availability._time_blocked(hora, excs):
                continue
        except Exception:  # noqa: BLE001 — malformed exceptions fail closed below
            return False
        regular_slots.append(hora)
    capacity = (len(regular_slots) * percent) // 100
    if capacity <= 0:
        return False
    start = local.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end = local.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    # All bands for this clinician/center/day share one transaction lock.
    # A narrower band key would let overlapping catalog bands race while each
    # performs an independent count over their intersecting windows.
    key = "turnos:overbooking:%s:%s:%s" % (
        str(medico_id or medico).strip().casefold(),
        str((row or {}).get("centro_slug") or ""), local.date().isoformat(),
    )
    return {"overbooking_capacity": capacity,
            "overbooking_window": (start, end),
            "overbooking_lock_key": key,
            "overbooking_center_slug": (row or {}).get("centro_slug"),
            "_overbooking_doctor_id": medico_id}


def overbooking_capacity(medico, turno_dt, row, usados):
    """``(restantes, motivo)`` for the configured staff-overbooking policy.

    ``cupos.sobreturnos`` is the number of additional patients allowed above
    the normal occupied slot. It is never used by the public slot generator;
    only staff paths may consume it. Returning a stable machine error lets the
    admin UI explain why the explicit exception cannot proceed.

    Legacy ``sobreturnos`` uses ``usados`` at one minute.  A present
    ``overbooking_percent`` instead returns its configured band-level budget;
    persistence performs the definitive shared count atomically.
    """
    if not medico or turno_dt is None:
        return 0, "sobreturno_requiere_medico"
    try:
        franja, motivo = _doctor_appointment_in_schedule(medico, turno_dt, row, staff_capacity=True)
        if not franja:
            return 0, motivo.replace("turno_medico_", "sobreturno_")
        percent_policy = _percentage_overbooking_policy(medico, turno_dt, franja, row)
        if percent_policy is False:
            return 0, "sobreturno_no_configurado"
        if percent_policy:
            # The definitive count is made atomically through store.approve(). This
            # pre-flight only tells callers whether the schedule configured a
            # positive shared allowance.
            usados_bloque = store.overbooking_count(
                *percent_policy["overbooking_window"], medico,
                medico_id=percent_policy.get("_overbooking_doctor_id"),
                center_slug=percent_policy.get("overbooking_center_slug"), exclude_sid=None)
            restantes = percent_policy["overbooking_capacity"] - usados_bloque
            return (restantes, None) if restantes > 0 else (0, "sobreturno_cupo_agotado")
        maximo = int((franja.get("cupos") or {}).get("sobreturnos") or 0)
        if maximo <= 0:
            return 0, "sobreturno_no_configurado"
        # one ordinary booking already exists (checked by the caller);
        # additional capacity is measured after that original patient.
        if usados > maximo:
            return 0, "sobreturno_cupo_agotado"
        return maximo - usados + 1, None
    except store.DbUnavailable:
        raise
    except Exception:  # noqa: BLE001 — an unknown agenda must fail closed
        return 0, "sobreturno_no_habilitado"


def _overbooking_allowed(medico, turno_dt, row, exclude_sid=None, usados=None):
    """Validate an intentional staff overbook against the doctor's agenda.

    ``usados`` skips the per-minute lookup for callers that already counted
    (listing every overbookable slot would otherwise be one query per minute).
    """
    if not medico or turno_dt is None:
        return False, "sobreturno_requiere_medico"
    if usados is None:
        usados = len(_doctor_appointments_at_time(medico, turno_dt, exclude_sid))
    restantes, motivo = overbooking_capacity(medico, turno_dt, row, usados)
    if restantes <= 0:
        return False, motivo
    return True, None
