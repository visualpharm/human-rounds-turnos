"""admin_appointments.py — el operador saca un turno por el paciente (2026-07-24).

The product owner: "the operator fills the DNI of the patient or name that the system
suggests as he's typing… then searches for the specialization or service…
it proposes the next time. If the patient doesn't like this time, the operator
can see the whole calendar".

Es el turno que se saca EN LA MESA o POR TELÉFONO: no hay solicitud que
revisar, porque quien la revisaría es la persona que la está cargando. Por eso
nace ya APROBADO — misma persistencia que la aprobación de la cola y que la
confirmación automática (`booking_decisions._persist_approval`): fila local +
push a HSI + liberación del hold + email al paciente. `resuelta_por` guarda
`mesa:<usuario>`, así el turno cargado a mano se distingue en los reportes de
uno que salió de la web.

Rutas (rol admin, operador o admisionista — la mesa de admisión también saca
turnos; lo que no puede es crear médicos, servicios ni centros):
  POST /api/admin/appointments       {dni|nota, servicio_id, dia, hora, medico?, sobreturno?}
  GET  /api/admin/appointment-slots  ?servicio_id=<id>[&medico=<nombre>][&derivacion=1]

Los pacientes salen de `/api/admin/patients?q=` y el catálogo de
`/api/admin/content/services`: nada de eso se duplica acá.

Los horarios sí tienen ruta propia, y por un motivo: los LIBRES son los mismos
que ve el paciente (`site_availability._appointments_for_service` / `_appointments_for_doctor`, la misma
función detrás de `/api/service-slots/<id>`, así la mesa nunca ofrece un
horario que la web no tenga; desde 2026-11-11 la mesa suma además las horas
ocultas a la web, `cupos.web: 0`, dentro de `booking_horizon.staff()`), pero los SOBRETURNOS no pueden salir por una
ruta pública — son capacidad interna que el generador de turnos del sitio
esconde a propósito (ver `booking_capacity.overbooking_capacity`). Un endpoint de admin
es el único lugar donde pueden convivir.

The native FastAPI router in `admin_appointments_router.py` owns the HTTP
boundary. This module contains only its synchronous domain functions.
"""

import re
import secrets
from datetime import date, datetime, timedelta, timezone

from app.auth import admin_auth
from app.patient_web import catalog
from app.core import db_booking
from app.core import db_accounts
from app.core import db_slots
from app.core import format
from app.core import hsi_extension
from app.admin_panel.native_json import ApiError
from app.core import ids as _ids

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")

# Tope de la nota del turno sin ficha: es un rótulo para reconocer a quien
# viene, no un campo de observaciones.
_NOTE_MAX = 80

# El turno se guarda en hora de Argentina, siempre — el proceso puede correr
# con cualquier TZ (en dev se vio America/New_York) y confiar en el offset del
# sistema es el bug que ya nos mordió en el .ics y en los recordatorios.
_ART = "-03:00"
_ARTZ = timezone(timedelta(hours=-3))

# Bounded suggestion scan only; staff-created appointments have no public horizon.
_STAFF_SUGGESTION_DAYS = 365


def _service(sid):
    for s in catalog.services():
        if str(s.get("id")) == str(sid):
            return s
    return None


def _schedule_row(servicio):
    """Lo mínimo que `booking_capacity._doctor_appointment_in_schedule` mira de una
    solicitud (el centro, para elegir la agenda del médico en ESE centro).
    No hay solicitud todavía — el turno de mostrador nace aprobado."""
    return {"centro_slug": servicio.get("centro"), "servicio_id": servicio.get("id")}


def _overbookings(servicio, medico):
    """Minutos en que ese médico ya tiene turno y su franja todavía admite
    otro paciente encima. Sin médico no hay sobreturno posible: la capacidad
    vive en la franja de una persona concreta, no en el servicio."""
    if not medico:
        return []
    from app.patient_web import site_base
    from app.modules.turnos import booking_capacity

    from app.modules.turnos import doctor_appointments

    fila = _schedule_row(servicio)
    tope = datetime.now(_ARTZ) + timedelta(days=_STAFF_SUGGESTION_DAYS)
    # El médico se reconoce por id (2026-10-23); el nombre sólo para filas
    # viejas sin id — misma regla que booking_capacity._doctor_appointments_at_time.
    nombre_cat, medico_id = doctor_appointments.resolve_pair(medico)
    buscado = site_base._norm_name(nombre_cat)

    # Una sola pasada por las aprobadas futuras: agrupar por minuto acá evita
    # una consulta por horario candidato (de ahí el parámetro `usados`).
    # `hasta=tope` (2026-08-06 audit): el matcheo es por NOMBRE libre, no por
    # id, así que sigue siendo hospital-wide — pero el horizonte de
    # sobreturnos ya se conoce acá, así que el corte de fecha va a SQL en vez
    # de traer también lo que cae después de `tope` para descartarlo abajo.
    por_minuto = {}
    for row in db_booking.list_approved_future(hasta=tope):
        dt = row.get("turno_dt")
        if dt is None or not doctor_appointments.same_doctor(
                row, medico_id, buscado, site_base._norm_name):
            continue
        local = (dt.astimezone(_ARTZ) if dt.tzinfo else dt.replace(tzinfo=_ARTZ))
        if local > tope:
            continue
        clave = local.replace(second=0, microsecond=0)
        por_minuto[clave] = por_minuto.get(clave, 0) + 1
    # An appointment created straight in HSI counts as a plain busy one (hsi_busy).
    if medico_id:
        from app.modules.turnos import hsi_busy
        for dia, hora in hsi_busy.busy_of_doctor(medico_id):
            clave = datetime.fromisoformat(f"{dia}T{hora}").replace(tzinfo=_ARTZ)
            if clave <= tope:
                por_minuto[clave] = por_minuto.get(clave, 0) + 1

    out = []
    for dt in sorted(por_minuto):
        restantes, _motivo = booking_capacity.overbooking_capacity(medico, dt, fila, por_minuto[dt])
        if restantes > 0:
            out.append({"dia": dt.strftime("%Y-%m-%d"), "hora": dt.strftime("%H:%M"),
                        "medico": medico, "restantes": restantes})
    return out


def _own_doctor_name(identity):
    """El nombre del médico dueño de la sesión, "" si el rol no es 'medico'.

    Raises `ApiError` (400, `cuenta_sin_medico`) cuando la cuenta médica no
    está enlazada a ninguna ficha del catálogo. Sin ficha no hay agenda
    propia que acotar, y dejar pasar ese caso sería darle la agenda de
    todos — el contrato de dos estados (string vacío = "no es médico, ve
    todo" vs. error = "es médico pero no pude resolverlo") existe para que
    esos dos casos nunca se confundan.
    """
    if (identity or {}).get("rol") != "medico":
        return ""
    from app.modules.turnos import doctor_appointments
    nombre = ((doctor_appointments.own_doctor(identity) or {}).get("nombre") or "").strip()
    if not nombre:
        raise ApiError({"error": "cuenta_sin_medico"}, status=400)
    return nombre


def _doctor_attends(servicio, medico_doc):
    """¿Ese servicio es de la agenda de ese médico?

    UNA sola regla (2026-10-23): el pool del servicio, `schedule_policy.belongs`
    — la etiqueta de su franja, o cluster + centro (y la especialidad en los
    clusters comodín) —, la misma con la que la web ofrece, el calendario del
    médico bloquea y la cola asigna. Antes esta función y el modal repetían
    «cluster + centro» a mano y en `otras`/`diagnostico`/`grupos` le ponían
    servicios de otra especialidad.
    """
    from app.modules.turnos import schedule_policy
    return bool(medico_doc) and schedule_policy.belongs(medico_doc, servicio)


def _external_calendar(servicio):
    """¿Los minutos de este servicio salen de un calendario externo que ya
    nombra a sus médicos (slot_source, MSD)? Entonces la agenda del catálogo
    no es la de esos turnos. Un servicio con agenda propia nunca (`_slot_source_info`
    devuelve {} ahí)."""
    from app.patient_web import site_availability
    return bool((site_availability._slot_source_info(servicio) or {}).get("medicos"))


def _desk_slots_by_doctor(servicio):
    """The desk's grid for a service built from its doctors' own agendas,
    one block per doctor WITH the name (the reference-deployment counterpart, 2026-10-19: «les ahorraría
    tiempo si le aparecieran el nombre del profesional además del caps y los
    horarios»). The patient grid merges those agendas anonymously
    (`_appointments_for_service`); the desk assigns the doctor when it gives
    the turno, so it sees whose each time is. {} when the service has its own
    schedule (no doctor owns those times) or no doctor offers anything — the
    caller then falls back to the merged grid."""
    from app.modules.turnos import schedule_policy
    from app.patient_web import site_availability, site_catalog
    if schedule_policy.has_schedule(servicio):
        return {}
    if _external_calendar(servicio):
        return {}   # an external calendar already names its doctors
    bloques = []
    for doc in schedule_policy.pool(servicio, site_catalog._doctors(), keep=schedule_policy.offering):
        if not doc.get("id"):
            continue
        bloques += (site_availability._appointments_for_doctor(servicio, doc["id"]) or {}).get("medicos") or []
    return {"medicos": bloques, "fuente": "agenda-medico"} if bloques else {}


def _minus_doctor_busy(libres):
    """Drops every minute its named doctor already has, in ANY service (slot-409):
    the service grid only subtracts this service's bookings, while approving or
    booking with that doctor answers `409 turno_medico_tomado`. Same owner as
    that check — `booking_capacity.doctor_busy_minutes`."""
    from app.modules.turnos import booking_capacity
    nombres = {t["medico"] for t in libres if t["medico"]}
    if not nombres:
        return libres
    dias = [date.fromisoformat(t["dia"]) for t in libres]
    desde, hasta = min(dias), max(dias)
    ocupado = {n: booking_capacity.doctor_busy_minutes(n, desde, hasta) for n in nombres}
    return [t for t in libres
            if not t["medico"] or (t["dia"], t["hora"]) not in ocupado[t["medico"]]]


def slots_data(identity, qs):
    """Pure domain function — `identity` is the already-authenticated dict
    (caller ran `require_admin(permitir_admisionista=True,
    permitir_medico=True)`); `qs` is a parsed query mapping."""
    sid = (qs.get("servicio_id") or [""])[0].strip()
    medico = (qs.get("medico") or [""])[0].strip()
    propio = _own_doctor_name(identity)
    if propio:
        medico = propio   # el ?medico= que haya mandado no cuenta

    servicio = _service(sid)
    if not servicio:
        raise ApiError({"error": "servicio_id inválido"}, status=400)
    # «Derivar» (Solicitudes) pide los horarios del servicio al que se manda la
    # solicitud, y ese servicio suele ser de OTRO centro: la mesa del Hospital
    # deriva a un CAPS (contraparte técnica, 2026-10-29). Son los mismos horarios que ve
    # cualquiera en la web, y la solicitud sale de su alcance al aprobarse: acá
    # el acotamiento por centro no los esconde. Cargar un turno (POST) sí sigue
    # cerrado a lo ajeno.
    derivando = (qs.get("derivacion") or [""])[0] == "1"
    centros_ok = None if derivando else admin_auth.centros_permitidos(identity)
    if centros_ok is not None and (servicio.get("centro") or "") not in centros_ok:
        return {"libres": [], "sobreturnos": []}
    from app.patient_web import site_availability
    if not site_availability._offers_appointments(servicio):
        return {"libres": [], "sobreturnos": []}

    from app.modules.turnos import booking_horizon, booking_requests
    if medico and booking_requests._doctor_serves_service(medico, servicio) is False:
        # A doctor outside the service's pool has no minutes here: the offers
        # follow the same pool the approval and the doctor's calendar use.
        return {"libres": [], "sobreturnos": [], "motivo": "medico_no_atiende_servicio"}
    # The desk books on the doctor's agenda, not inside the patient window.
    with booking_horizon.staff():
        data = (site_availability._appointments_for_doctor(servicio, medico) if medico
                else _desk_slots_by_doctor(servicio)
                or site_availability._appointments_for_service(servicio))
        # Solicitudes asks `respaldo=servicio`: a doctor with no minutes of
        # their own here is offered the service's grid, booked with them.
        if medico and not data.get("medicos") and (qs.get("respaldo") or [""])[0] == "servicio":
            data = site_availability._appointments_for_service(servicio)
    libres = []
    for m in (data.get("medicos") or []):
        nombre = m.get("medico") or medico or ""
        for t in (m.get("turnos") or []):
            if t.get("dia") and t.get("hora"):
                libres.append({"dia": t["dia"], "hora": t["hora"], "medico": nombre})
    libres = _minus_doctor_busy(libres)
    libres.sort(key=lambda t: (t["dia"], t["hora"]))
    out = {"libres": libres, "sobreturnos": _overbookings(servicio, medico)}
    # Minutes that name nobody (a service that owns its schedule) are booked
    # with a doctor the desk chooses (launch plan P1: «Médico no asignado» for
    # a service with two doctors). The choice is the service's pool — the
    # same `_doctor_pool` the queue's approval offers; a lab assigns no doctor.
    if not medico and any(not t["medico"] for t in libres) and not booking_requests._is_lab(servicio):
        out["medicos"] = [m["nombre"] for m in booking_requests._doctor_pool(servicio, catalog.doctors() or [])
                          if m.get("nombre")]
    return out


def _appointment_patient(body):
    """(nota, dni, paciente) of the appointment: the account holder's ficha, or the free NOTE the desk typed.
    Raises `ApiError` for an invalid, unknown or archived DNI."""
    # Dos formas de decir para quién es el turno. La de siempre, un paciente
    # con ficha; y una NOTA suelta (responsable del producto, 2026-07-27): el mostrador escribe lo
    # que sea — un apellido, "señora de Lazcano", "prueba" — y el turno se
    # ocupa igual. Es lo que hoy hacen en HCI ("le pongo una letra y confirmo,
    # y ya está en el listado"): mientras atienden gente no hay tiempo de
    # cargar una ficha completa, y un turno anotado a medias sirve más que un
    # turno que no se cargó. La ficha se completa después.
    nota = " ".join(str(body.get("nota") or "").split())[:_NOTE_MAX]
    dni = _ids.dni(body.get("dni"))
    paciente = None
    if dni or not nota:
        if not format.dni_valid_length(dni):
            raise ApiError({"error": "DNI inválido (%d a %d dígitos)." %
                             (format.DNI["length_min"], format.DNI["length_max"])}, status=400)
        paciente = db_accounts.get_user(dni)
        if not paciente:
            # Deliberado: acá no se crea el paciente al pasar. La mesa lo da de
            # alta en Pacientes (con su contacto y su fecha de nacimiento) y
            # después le saca el turno — si no, quedarían fichas a medias.
            raise ApiError({"error": "paciente_no_encontrado"}, status=404)
        if paciente.get("activo") is False:
            raise ApiError({"error": "Ese paciente está archivado."}, status=400)
    return nota, dni, paciente


def _appointment_service(identity, sid_servicio):
    """The catalog service the desk may book: it exists, belongs to the account's center scope and gives turnos."""
    servicio = _service(sid_servicio)
    if not servicio:
        raise ApiError({"error": "servicio_id inválido"}, status=400)
    # Alcance por centro: una mesa acotada carga turnos de SU centro nomás. Lo
    # de otro centro no existe para esa cuenta: 404, no 403 (misma convención
    # que la cola y la asistencia, `_staff_row_or_error`).
    centros_ok = admin_auth.centros_permitidos(identity)
    if centros_ok is not None and (servicio.get("centro") or "") not in centros_ok:
        raise ApiError({"error": "Ese servicio no es de tu centro de salud."}, status=404)

    from app.patient_web import site_availability
    if not site_availability._offers_appointments(servicio):
        # Invariante duro del proyecto: un walk-in NUNCA ofrece turnos, en
        # ninguna superficie — tampoco en la mesa. La única excepción es el
        # que abre su listado el mismo día, y ahí el turno tiene que ser HOY.
        raise ApiError({"error": "Ese servicio se atiende por orden de llegada, sin turno."},
                        status=400)
    return servicio


def _appointment_minute(servicio, body):
    """(dia, hora, turno_dt) of the requested minute, in Argentina time."""
    from app.patient_web import site_availability
    dia = str(body.get("dia") or "").strip()
    hora = str(body.get("hora") or "").strip()
    if not _DAY_RE.match(dia) or not _TIME_RE.match(hora):
        raise ApiError({"error": "Falta el día o la hora del turno."}, status=400)
    try:
        turno_dt = datetime.fromisoformat(f"{dia}T{hora}:00{_ART}")
    except ValueError:
        raise ApiError({"error": "Día u hora inválidos."}, status=400)
    if site_availability._opens_same_day(servicio) and dia != datetime.now(_ARTZ).date().isoformat():
        # El listado de un servicio por orden de llegada se abre HOY y se
        # cierra hoy: cargarle un turno para mañana lo volvería otra cosa.
        raise ApiError({"error": "Ese listado se abre sólo para el día de hoy."},
                        status=400)
    return dia, hora, turno_dt


def _appointment_doctor(identity, propio, servicio, body, sobreturno, turno_dt):
    """The doctor the turno is booked with (None for none): the session's own doctor, none for a lab, always one of the
    service's pool and, outside an overbooking, inside their agenda. Raises `ApiError` otherwise."""
    medico = str(body.get("medico") or "").strip() or None
    from app.modules.turnos import booking_capacity
    from app.modules.turnos import booking_requests
    if propio:
        # Una sesión médica agenda en su propio renglón y en ninguno más: el
        # nombre que venga en el body se ignora. Un laboratorio no asigna
        # médico, así que ahí el turno no caería en ninguna agenda — y un turno
        # que no es de nadie no es lo que este permiso vino a habilitar.
        if booking_requests._is_lab(servicio):
            raise ApiError({"error": "Un médico sólo puede dar turnos de su propia agenda."},
                            status=403)
        from app.modules.turnos import doctor_appointments
        if not _doctor_attends(servicio, doctor_appointments.own_doctor(identity)):
            # Lo ajeno no existe para quien no lo ve: 404, no 403 (como la
            # asistencia de un turno ajeno).
            raise ApiError({"error": "Ese servicio no es de tu agenda."}, status=404)
        medico = propio
    if medico and booking_requests._is_lab(servicio):
        medico = None   # un laboratorio no asigna médico (responsable del producto, 2026-07-19)
    # Nunca un médico de otra especialidad (2026-10-23): el nombrado tiene que
    # estar en el pool del servicio, el mismo corte que la aprobación de la cola
    # (`booking_decisions._approve_core`). Sin médico se sigue cargando, como siempre.
    if medico and booking_requests._doctor_serves_service(medico, servicio) is False:
        raise ApiError({"error": "medico_no_atiende_servicio"}, status=409)
    # Y el turno de un médico nombrado cae DENTRO de su agenda (plan de
    # lanzamiento P12): el mismo corte que la aprobación de la cola
    # (`_approve_core` → `_doctor_appointment_in_schedule`). La grilla de la mesa
    # ya ofrece sólo esos minutos; una pantalla vieja o un pedido armado a mano
    # cargaban a las 13:00, un sábado o fuera de su intervalo. El sobreturno
    # tiene su propio corte (`_overbooking_allowed`, abajo). Con un calendario
    # externo (MSD) los minutos son de ese calendario, no de la agenda del
    # catálogo: ahí no se mide, como antes. El médico en su propia sesión SÍ
    # puede dar un turno fuera de su ventana: «booking outside the window — only the doctor
    # themself» (responsable del producto, Codex 21.10 item 14) — la mesa no.
    if medico and not sobreturno and not propio and not _external_calendar(servicio):
        _, motivo_horario = booking_capacity._doctor_appointment_in_schedule(
            medico, turno_dt, _schedule_row(servicio))
        if motivo_horario:
            raise ApiError({"error": motivo_horario}, status=409)
    return medico


def _check_overbooking(servicio, medico, turno_dt):
    """El sobreturno es lo contrario de un horario libre: se agrega ENCIMA
    de un turno que ya existe, así que acá no corre el chequeo de
    ocupados — es exactamente lo que la excepción viene a saltear. Lo
    que sí se valida es que la excepción esté habilitada: un médico
    concreto, un turno previo suyo en ese minuto, y cupo de sobreturnos
    en esa franja de su agenda (mismas reglas que aprobar desde la cola)."""
    from app.modules.turnos import booking_capacity
    if not medico:
        raise ApiError({"error": "sobreturno_requiere_medico"}, status=400)
    if not booking_capacity._doctor_appointment_busy(
            medico, turno_dt, center_slug=servicio.get("centro")):
        raise ApiError({"error": "sobreturno_requiere_turno_previo"}, status=409)
    permitido, motivo = booking_capacity._overbooking_allowed(
        medico, turno_dt, _schedule_row(servicio))
    if not permitido:
        raise ApiError({"error": motivo or "sobreturno_no_habilitado"}, status=409)


def _take_desk_hold(sid_servicio, medico, dia, hora, turno_dt):
    """Ese horario puede haberse ocupado mientras el operador hablaba por
    teléfono — con un paciente TODAVÍA en el wizard, sosteniendo un hold
    vigente sobre este mismo slot. `slot_holds.taken()` no alcanza acá:
    es una caché de 3 s pensada para la grilla pública, y en esa
    ventana la mesa podría pisar exactamente el turno que el paciente
    está por confirmar (data-integrity guards, 2026-08-05, tier 14).

    En su lugar se toma un hold PROPIO con `try_take` — la MISMA
    primitiva atómica (INSERT ... ON CONFLICT, sin caché) que usa el
    paciente al elegir horario en `booking_requests._create`. Si hay un
    hold vigente de OTRO token sobre este slot, `try_take` no lo
    consigue -> el mismo 409 "turno_tomado" que ya usa la grilla, y NO
    se crea nada. Si nadie lo tiene, la mesa lo toma para sí (con un
    token propio, nadie más lo ve) y lo devuelve: el llamador lo sostiene
    hasta después de persistir el turno y lo libera en su `finally`."""
    from app.modules.turnos import booking_capacity
    from app.modules.turnos import slot_holds
    desk_hold_token = "mesa:" + secrets.token_urlsafe(12)
    try:
        tomado = slot_holds.try_take(sid_servicio, dia, hora, desk_hold_token)
    except Exception:  # noqa: BLE001 — fail CLOSED: misma convención ya
        # adoptada para holds (booking_requests._create, 2026-08-05
        # data-integrity guards plan item 7) — un hiccup de slot_holds/DB
        # se trata como "no lo pude tomar", nunca como "seguí sin
        # protección". `db_slots.hold_acquire` (debajo de try_take) ya degrada
        # a False si la base está caída; esto cubre cualquier otra falla
        # inesperada del lado de slot_holds/patient_site.
        tomado = False
    if not tomado:
        raise ApiError({"error": "turno_tomado"}, status=409)
    # Un médico puede estar libre para ESTE servicio y ocupado en otro (la
    # grilla de arriba mira el servicio, no la persona).
    if medico and booking_capacity._doctor_appointment_busy(medico, turno_dt):
        db_slots.hold_release(desk_hold_token)
        raise ApiError({"error": "turno_medico_tomado"}, status=409)
    return desk_hold_token


def _booking_person(paciente, dni, nota):
    """The patient columns of the new request: the ficha's, or the note in the name's place."""
    if paciente:
        sexo = (str(paciente.get("sexo") or "").strip().upper()) or None
        if sexo not in ("M", "F", "X"):
            sexo = None
        nombre = (paciente.get("nombre") or "").strip()
        apellido = (paciente.get("apellido") or "").strip()
        return {
            "contacto": (paciente.get("contacto") or paciente.get("email") or "").strip() or None,
            "nac": paciente.get("nacimiento"), "sexo": sexo,
            "nombre": nombre, "apellido": apellido,
            "etiqueta": f"{apellido}, {nombre}".strip(", "),
            "dni_fila": dni, "solicitante": dni,
        }
    # La nota ocupa el lugar del nombre: es lo que la mesa escribió y lo
    # que la cola del día tiene que mostrar. Sin DNI no hay ficha, no hay
    # email y no hay handoff a HSI — y eso está bien, no es un turno a
    # medio hacer sino uno anotado a mano.
    return {"contacto": None, "nac": None, "sexo": None, "nombre": nota, "apellido": "", "etiqueta": nota,
            "dni_fila": None, "solicitante": None}


def _persist_desk_booking(identity, propio, person, sid_servicio, servicio, turno_dt, medico, sobreturno):
    """Creates the request and approves it at once; every conflict of the approval is a 409. Returns (id, fila)."""
    from app.modules.turnos import booking_decisions
    # Front desk stays UNLIMITED on purpose (product rule, see
    # booking_limits.py's own docstring) — no idem_key either: a counter
    # booking isn't a retryable web submit. `create_booking_request` never
    # went through the atomic limit guard and doesn't need to.
    nuevo = db_booking.create_booking_request(
        person["dni_fila"], person["nombre"], person["apellido"],
        person["nac"], person["contacto"], sid_servicio, servicio.get("centro"),
        sexo=person["sexo"], solicitante_dni=person["solicitante"],
        actor=("medico:" if propio else "mesa:") + str(identity.get("usuario") or "admin"),
    )

    try:
        # `handler=None`: `booking_decisions._persist_approval` only
        # forwards it to `notifications.notify_approved(updated,
        # handler)`, which only forwards it to
        # `core.site_identity.base_url(handler)` — that function never
        # reads its `handler` argument at all (the public origin is
        # pinned by env, never derived from a client Host header), so
        # there is no handler-shaped object to build here.
        fila = booking_decisions._persist_approval(
            nuevo, turno_dt, medico=medico,
            # Auditoría: quién lo cargó a mano. "medico:" distingue el turno que dio
            # el propio profesional en su consultorio del que dio el mostrador.
            resuelta_por=("medico:" if propio else "mesa:") + str(identity.get("usuario") or "admin"),
            sobreturno=sobreturno, handler=None,
        )
    except db_booking.SlotConflict:
        # The pre-check (`_doctor_appointment_busy` in `_take_desk_hold`) is
        # UX only — this is the guard that actually closes the race: another
        # approval (another operator, or the queue) won this exact (médico,
        # horario) in the instant between that check and this approve
        # (2026-08-05 data-integrity guards, plan item 2). Same surface the
        # pre-check already shows, never a 500. The row `nuevo` just created
        # stays 'pendiente' — nothing is lost, it shows up in the queue for
        # staff to pick a new time.
        raise ApiError({"error": "turno_medico_tomado"}, status=409)
    except db_booking.OverbookingCapacityReached:
        # The schedule-list preflight is informational; persistence owns
        # the cross-minute, transactionally serialized percentage budget.
        raise ApiError({"error": "sobreturno_cupo_agotado"}, status=409)
    except booking_decisions.OverbookingPolicyDenied:
        raise ApiError({"error": "sobreturno_no_configurado"}, status=409)
    except db_booking.AlreadyProcessed:
        # Defensive: `nuevo` was just created 'pendiente' by the INSERT
        # above, so this shouldn't fire — but if it somehow did, the same
        # surface the queue uses for it.
        raise ApiError({"error": "La solicitud ya fue resuelta."}, status=409)
    return nuevo, fila


def create_appointment(identity, propio, body):
    """Pure domain function — `identity` is the already-authenticated dict
    (caller ran `require_admin(permitir_admisionista=True,
    permitir_medico=True)`); `propio` is `_own_doctor_name(identity)`,
    resolved by the caller BEFORE parsing the body — same order the legacy
    `_create()` used (a médico account missing its catalog link 400s before
    a malformed JSON body ever gets read). Raises `ApiError` for every other
    validation/conflict branch the legacy version used to write straight to
    the handler. The checks run in a fixed order, one step each (patient,
    service, minute, overbooking flag, doctor, then the slot), and the first
    that fails answers."""
    nota, dni, paciente = _appointment_patient(body)
    sid_servicio = str(body.get("servicio_id") or "").strip()
    servicio = _appointment_service(identity, sid_servicio)
    dia, hora, turno_dt = _appointment_minute(servicio, body)

    sobreturno = body.get("sobreturno", False)
    if not isinstance(sobreturno, bool):
        raise ApiError({"error": "sobreturno_invalido"}, status=400)

    medico = _appointment_doctor(identity, propio, servicio, body, sobreturno, turno_dt)

    # Sostiene el hold propio de la mesa hasta el `finally` que envuelve el
    # resto de la función — así el slot queda protegido desde el chequeo hasta
    # después de persistir el turno, no sólo en el instante del chequeo. Sigue
    # None en la rama de sobreturno (no hay chequeo de ocupados ahí, así que no
    # hay nada que liberar).
    desk_hold_token = None
    if sobreturno:
        _check_overbooking(servicio, medico, turno_dt)
    else:
        desk_hold_token = _take_desk_hold(sid_servicio, medico, dia, hora, turno_dt)

    try:
        person = _booking_person(paciente, dni, nota)
        nuevo, fila = _persist_desk_booking(
            identity, propio, person, sid_servicio, servicio, turno_dt, medico, sobreturno)
    finally:
        # El hold propio de la mesa (si se tomó uno más arriba) ya cumplió su
        # función — proteger el slot entre el chequeo y la persistencia — así
        # que se libera acá pase lo que pase: turno creado, choque contra el
        # médico, SlotConflict, o cualquier excepción no prevista. Sostenerlo
        # más allá de este punto sólo dejaría un hold fantasma bloqueando ese
        # slot para el resto de su TTL, con la mesa ya afuera de la request.
        if desk_hold_token:
            db_slots.hold_release(desk_hold_token)

    from app.modules.turnos import booking_requests
    return {
        "ok": True,
        "id": nuevo,
        "sobreturno": sobreturno,
        "sin_ficha": paciente is None,
        "turno_dt": (fila or {}).get("turno_dt").isoformat() if (fila or {}).get("turno_dt") else None,
        "paciente": person["etiqueta"],
        "servicio": servicio.get("nombre"),
        # Cómo quedó en HSI (contraparte técnica, 2026-10-19: la mesa no encontraba la
        # sincronización). Conectado: ya se intentó solo, sin que nadie
        # apriete nada; sin conexión, el modal ofrece cargarlo con la extensión.
        "hsi": _hsi_status(fila, booking_requests._emr_sync_enabled()),
    }


def _hsi_extension_url():
    """The store page of the desk's HSI extension when THIS site is one of its
    sites, else None (`core.hsi_extension`, shared with Solicitudes)."""
    return hsi_extension.store_url()


def _hsi_status(fila, conectado):
    """Cómo quedó el turno en HSI. Conectado: ya se intentó solo. Sin conexión:
    la mesa lo carga con la extensión, si este sitio es uno de los suyos."""
    return {"conectado": bool(conectado),
            "estado": (fila or {}).get("hsi_estado") or "skip",
            "extension": None if conectado else _hsi_extension_url(),
            "instalar": None if conectado else hsi_extension.install()}
