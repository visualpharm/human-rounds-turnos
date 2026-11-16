"""admin_patients.py — pacientes como entidad de la consola admin (2026-07-24).

The product owner: "all the entities could be created, edited or archived… users and
patients". Los pacientes viven en la tabla `usuarios` (la misma cuenta con la
que se loguean); acá la mesa/administración puede darlos de alta (p.ej. alguien
sin celular en la recepción), corregir datos de contacto y ARCHIVAR una cuenta
(activo=false: no entra más y sus sesiones dejan de resolver — db_accounts.get_session y
db_accounts.verify_password lo cortan; el historial de solicitudes queda intacto).

Rutas (rol admin u operador — la mesa de admisión NO: un admisionista sólo ve
la cola del día):
  GET    /api/admin/patients[?q=]      lista/búsqueda (dni, nombre, apellido)
  GET    /api/admin/patients/<dni>     la ficha COMPLETA: cobertura, domicilio,
                                       los chicos a cargo y los adultos a cargo
                                       de quien es. La lista no trae nada de
                                       eso a propósito (son 200 filas).
  POST   /api/admin/patients           alta {dni,nombre,apellido,...,password?}
                                       sin password → se genera una temporal y
                                       viene UNA vez en la respuesta
  PUT    /api/admin/patients/<dni>     edición parcial (campos presentes)
  DELETE /api/admin/patients/<dni>     archiva (db_accounts.DNI_ON_ARCHIVE); con turnos
                                       vivos 409 y la lista, salvo
                                       ?cancelar_turnos=1; PUT activo=true
                                       lo restaura

Personas a cargo (the product owner, 2026-07-24: "make the kids editable… and kids could be
linked to one or two parents"). Un chico es una fila por adulto en
`dependientes` (UNIQUE adulto_dni+dni), así que estar a cargo de padre Y madre
es tener dos filas — no hace falta esquema nuevo, sí una vista que muestre a
los dos y deje agregar al segundo:
  POST   /api/admin/patients/<dni>/dependents            alta/edición del chico
  DELETE /api/admin/patients/<dni>/dependents/<dni_dep>  lo desvincula de ESE
                                       adulto (si tiene otro, sigue a cargo de
                                       ese otro; el chico no se borra)

Historia clínica digitalizada (2026-08-03, seeding tooling — ver
scripts/seed_patient_account.py): reemplaza por HTTP lo que hasta ahora sólo
hacía scripts/import_clinical_history.py con acceso directo a la base. Un
despliegue real (Coolify) no expone su Postgres hacia afuera, así que cargar
la historia clínica de una cuenta real (p.ej. la del responsable del producto en producción) tiene
que poder hacerse por API — igual que el resto de esta consola. `solo_admin`
a propósito: es el mismo tipo de operación irreversible/sensible que crear un
rol nuevo, no algo que la mesa de admisión deba poder disparar.
  POST /api/admin/patients/<dni>/clinical-history  {records:[...], source_system?}
                                       reemplaza ATÓMICAMENTE los registros de
                                       ESA fuente para ESE DNI (mismo contrato
                                       que db_clinical.clinical_history_replace/
                                       scripts/import_clinical_history.py)

The native FastAPI router in `admin_patients_router.py` owns the HTTP
boundary. This module contains only its synchronous domain functions.
"""

import re
import secrets

from app.core import access_log
from app.core import db_accounts
from app.core import db_booking
from app.core import db_clinical
from app.core import db_slots
from app.cuentas import accounts
from app.admin_panel.native_json import ApiError
from app.core import ids as _ids
from app.core import tz

_DNI_RE = re.compile(r"^\d{7,9}$")
_SEXOS = ("M", "F", "X")


def _public(u):
    """Un paciente listo para el cable. `db` devuelve las filas tal cual salen
    de Postgres — `nacimiento`/`created` son date/datetime, y json.dumps (el
    _send_json del server no lleva `default=`) revienta con ellos: el request
    moría con la conexión cerrada y la sección mostraba "No se pudo cargar".
    Se serializa con el MISMO criterio que usa la cuenta del propio paciente
    (accounts._user_json) para que el paciente no viaje de dos formas
    distintas según quién lo pida."""
    from app.cuentas import accounts
    return accounts._user_json(u)


def _int_qs(qs, key, default):
    """Un entero de query string, con fallback silencioso — nunca 500 por un
    ?offset=abc mal armado."""
    raw = (qs.get(key, [""])[0] or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# ---- GET /api/admin/patients -------------------------------------------

def list_patients(identity, qs):
    """Pure domain function — `identity` is the already-authenticated
    `{"usuario","rol"}` dict (the native router ran the role gate via
    `Depends(deps.require_admin(...))`). `qs` is a parsed query mapping."""
    q = (qs.get("q", [""])[0] or "").strip()
    rol_medico = (identity or {}).get("rol") == "medico"
    if rol_medico:
        # Un médico no pagina el padrón entero — su universo ya lo recorta
        # _scope_medico a los suyos (o al DNI exacto que tiene enfrente), así
        # que acá alcanza con una sola página del padrón general y el total
        # es simplemente cuántos quedaron después de filtrar.
        offset, limit = 0, 200
        rows, _total_padron = db_accounts.users_admin_list(q, limit=limit, offset=offset)
        items = [_public(u) for u in rows]
        items = _scope_medico(identity, q, items)
        total = len(items)
    elif q and _doctors_named(q):
        # Buscar por médico desde el mismo buscador (responsable del producto, 2026-11-02: «no
        # hace falta un filtro de médicos, que lo escriban en la búsqueda»):
        # los pacientes de los médicos cuyo nombre contiene lo escrito (mismo
        # padrón que «Mis pacientes», db_clinical.patients_of_doctor), más los
        # que coinciden por DNI o nombre, en una sola lista alfabética.
        items = _search_with_doctors(q, _doctors_named(q))
        offset, limit = 0, len(items)
        total = len(items)
    else:
        # Antes esto se cortaba en 200 filas SIEMPRE, sin OFFSET y sin decir
        # que había más (auditoría 2026-08-06): un padrón de 300+ pacientes
        # mostraba sólo los primeros 200 y la mesa no tenía forma de saber
        # que faltaban 100. `total` es el conteo real (sin el LIMIT) para que
        # la consola pueda mostrar "N de M" y pedir la página siguiente.
        #
        # Sin búsqueda, la pantalla default ya NO es el padrón entero
        # (reunión hospital 2026-08-27, contraparte técnica: "que le aparezcan a pedido, no
        # que aparezcan todos" — "hacemos últimos y no todos"): son los
        # CONTACTOS RECIENTES, ordenados por último turno/solicitud o ficha
        # editada. Escribir algo en el buscador sigue recorriendo el padrón
        # completo por DNI o nombre, como siempre.
        offset = max(0, _int_qs(qs, "offset", 0))
        limit = _int_qs(qs, "limit", 200)
        rows, total = db_accounts.users_admin_list(q, limit=limit, offset=offset, recientes=not q)
        items = [_public(u) for u in rows]
    # El ranking de asistencia sale en UNA consulta para toda la página: es la
    # columna que la mesa ordena para ver a quién conviene recordarle el
    # turno. por_dnis es best-effort — si falla, cada fila vuelve neutra.
    from app.core import access_log
    # Security review 2026-11: the list left no row, and a doctor who typed someone else's exact DNI got
    # that person's services, doctors, appointment counts and attendance ranking too. Every listed patient
    # is recorded (once a day, a list that cannot be recorded is not shown), and a doctor gets only the
    # identity of a patient who is not theirs.
    ajenos = set()
    if rol_medico:
        ajenos = {str(i.get("dni") or "") for i in items
                  if not access_log.may_see(identity, i.get("dni"), clinical=False)}
    if items:
        access_log.record_many(identity, "ficha", [(i.get("dni"), "lista") for i in items])
    from app.modules.turnos import patient_ranking
    rank = patient_ranking.by_dnis([i.get("dni") for i in items])
    # «Turnos»: los turnos que se le otorgaron (aprobados, pasados o por venir),
    # no los de la fórmula de asistencia — esos dejan afuera el que todavía no
    # llegó y la lista decía «—» de alguien con turno mañana. Best-effort.
    try:
        otorgados = db_booking.granted_counts_by_dnis([i.get("dni") for i in items])
    except Exception:  # noqa: BLE001 — un dato auxiliar no tumba la pantalla
        otorgados = {}
    medicos = {} if rol_medico else _doctors_of(items)
    servicios = _main_services(items)
    for item in items:
        if str(item.get("dni") or "") in ajenos:
            item["servicio"] = ""
            item["ranking"] = patient_ranking.neutral()
            item["medicos"] = []
            item["turnos"] = {"otorgados": 0, "proximo": None}
            _summarize_coverage(item)
            continue
        item["servicio"] = servicios.get(str(item.get("dni") or ""), "")
        item["ranking"] = rank.get(str(item.get("dni") or ""),
                                   patient_ranking.neutral())
        item["medicos"] = medicos.get(str(item.get("dni") or ""), [])
        g = otorgados.get(str(item.get("dni") or ""), {})
        proximo = g.get("proximo")
        item["turnos"] = {
            "otorgados": g.get("otorgados", 0),
            "proximo": proximo.astimezone(tz.tzinfo()).date().isoformat() if proximo else None,
        }
        _summarize_coverage(item)
    payload = {"items": items, "total": total, "offset": offset, "limit": limit}
    # Le dice al cliente CUÁL de las dos listas está mirando, para el texto
    # del estado vacío/encabezado ("estos son los recientes" vs "esto es lo
    # que encontré"). Sólo tiene sentido en la vista sin scope de médico.
    if not rol_medico:
        payload["modo"] = "busqueda" if q else "recientes"
    return payload


def _scope_medico(identity, q, items):
    """Recorta la búsqueda de pacientes cuando quien busca es un médico.

    Un médico ve a los SUYOS (los que ya pasaron o van a pasar por su agenda) y,
    además, a quien identifique por DNI exacto — que es el caso real: la persona
    está parada enfrente y dice su documento. Buscar por apellido en el padrón
    completo del hospital sigue siendo cosa del mostrador. Cualquier otro rol
    pasa entero por acá sin tocar nada.
    """
    if (identity or {}).get("rol") != "medico":
        return items
    from app.core import format, ids
    # «30.111.222» como lo muestra la cola vale lo mismo que 30111222.
    digitos = ids.dni_query(q)
    if digitos and format.dni_valid_length(digitos):
        hallados = [i for i in items if str(i.get("dni") or "") == digitos]
        # K10: el hallazgo por DNI exacto de alguien que NO es su paciente
        # sigue permitido (sólo identidad, para darle un turno libre a quien
        # tiene enfrente), pero queda en el registro de accesos. Sus datos
        # clínicos siguen cerrados hasta que el turno lo haga suyo.
        from app.core import access_log
        for i in hallados:
            if not access_log.may_see(identity, digitos, clinical=False):
                access_log.record(identity, "busqueda", digitos)
        return hallados
    from app.modules.turnos import doctor_appointments
    medico = doctor_appointments.own_doctor(identity)
    mid = (medico or {}).get("id")
    if not mid:
        return []
    try:
        # completo=True: este es el padrón (Pacientes), no «Mis pacientes» —
        # el recorte a 12 meses de patients_of_doctor (2026-08-06) es el
        # default de ESE otro screen; acá el médico sigue viendo a cualquier
        # paciente propio, sin importar hace cuánto fue la última vez.
        propios = {str(r.get("dni") or "")
                   for r in db_clinical.patients_of_doctor(mid, completo=True)}
    except Exception:  # noqa: BLE001
        return []
    return [i for i in items if str(i.get("dni") or "") in propios]


def _doctors_of(items):
    """`{dni: [{"nombre", "n"}, ...]}` de la página: con qué médicos tiene turnos
    cada paciente, el de más turnos primero (columna «Médico» de la lista).
    Una consulta para toda la página; best-effort, un dato auxiliar no tumba
    la pantalla."""
    try:
        from app.patient_web import catalog
        por_dni = db_booking.doctors_by_dnis([i.get("dni") for i in items])
        if not por_dni:
            return {}
        nombres = {str(m.get("id")): m.get("nombre") for m in catalog.doctors(incluir_deshabilitados=True)}
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for dni, lista in por_dni.items():
        hallados = [{"nombre": nombres[str(mid)], "n": n}
                    for mid, n in lista if nombres.get(str(mid))]
        if hallados:
            out[str(dni)] = hallados
    return out


#: «Servicio principal» de una persona (responsable del producto, 2026-11-04: «si son todos por
#: poco, no mostrar nada»): el servicio con más turnos, si tiene al menos 2 y al
#: menos la mitad de sus turnos vivos (aprobados o pedidos pendientes). Un
#: empate en el primer puesto no es un líder.
MAIN_SERVICE_MIN = 2
MAIN_SERVICE_SHARE = 0.5


def main_service_id(counts):
    """El id del servicio principal de `[(service_id, n), ...]` (el de más
    turnos primero), o `""` cuando ninguno lidera con claridad."""
    if not counts:
        return ""
    total = sum(n for _, n in counts)
    sid, n = counts[0]
    if n < MAIN_SERVICE_MIN or n < total * MAIN_SERVICE_SHARE:
        return ""
    if len(counts) > 1 and counts[1][1] == n:
        return ""
    return sid


def _main_services(items):
    """`{dni: nombre}` del servicio principal de cada paciente de la página
    (columna «Servicio» de la lista). Una consulta; best-effort."""
    try:
        from app.patient_web import catalog
        por_dni = db_booking.services_by_dnis([i.get("dni") for i in items])
        out = {}
        for dni, lista in por_dni.items():
            sid = main_service_id(lista)
            nombre = (catalog.service_name(sid) or sid) if sid else ""
            if nombre:
                out[str(dni)] = nombre
        return out
    except Exception:  # noqa: BLE001 — un dato auxiliar no tumba la pantalla
        return {}


def _doctors_named(q):
    """Ids de los médicos cuyo nombre O especialidad contiene TODAS las
    palabras de `q` (sin mayúsculas ni tildes): «cardio» trae a los pacientes
    de los cardiólogos. Una búsqueda sólo de números es un DNI, no un médico."""
    from app.admin_panel import doctor_patients
    from app.core import text_fold
    words = text_fold.fold_words(q).split()
    if not words or all(w.isdigit() for w in words):
        return []
    from app.patient_web import catalog
    out = []
    for m in doctor_patients._medicos_opciones():
        espec = (catalog.doctor_doc(m["id"], tolerant=True) or {}).get("especialidad") or ""
        name = text_fold.fold_words(f"{m.get('nombre') or ''} {espec}")
        if all(w in name for w in words):
            out.append(m["id"])
    return out


def _search_with_doctors(q, medico_ids):
    """Los pacientes de esos médicos (mismo padrón que «Mis pacientes») unidos
    a las coincidencias normales por DNI o nombre, sin repetir a nadie."""
    rows, _total = db_accounts.users_admin_list(q, limit=200, offset=0)
    by_dni = {str(u.get("dni") or ""): _public(u) for u in rows}
    dnis = set()
    for mid in medico_ids:
        try:
            propias = db_clinical.patients_of_doctor(mid, completo=True)
        except Exception:  # noqa: BLE001 — un médico sin lectura no tumba la búsqueda
            continue
        dnis |= {str(r.get("dni") or "") for r in propias if r.get("dni")}
    dnis -= set(by_dni)
    if dnis:
        for u in db_clinical.users_by_dnis(dnis).values():
            by_dni[str(u.get("dni") or "")] = _public(u)
    items = list(by_dni.values())
    items.sort(key=lambda i: ((i.get("apellido") or "").lower(), (i.get("nombre") or "").lower()))
    return items


def _summarize_coverage(item):
    """Cobertura y domicilio de la LISTA se reducen a un texto cada uno.

    Sirven para una columna y para un filtro; el objeto entero es de la ficha.
    Mandar el jsonb completo en 200 filas fue exactamente el bug de 2026-07-24:
    el formulario se armaba filtrando la lista y guardaba la cobertura a medio
    cargar. Que la lista no lo tenga hace que ese error no se pueda repetir.
    """
    from app.cuentas import health_coverage as _cob
    p = _cob.primary(item.pop("cobertura", None)) or {}
    item["cobertura_nombre"] = p.get("nombre") or ""
    dom = item.pop("domicilio", None) or {}
    if isinstance(dom, dict):
        item["localidad"] = (dom.get("localidad") or "").strip()
    else:
        item["localidad"] = ""


# ---- GET /api/admin/patients/<dni> --------------------------------------

def patient_detail(dni):
    u = db_accounts.get_user(dni)
    if not u:
        raise ApiError({"error": "no encontrado"}, status=404)
    from app.cuentas import health_coverage
    from app.modules.turnos import patient_ranking
    deps = [_public(d) for d in db_slots.list_dependents(dni)]
    # De cada chico, TODOS sus adultos: es lo que hace visible que puede estar
    # a cargo de dos (y que sacarlo de uno no lo borra del otro).
    for d in deps:
        d["adultos"] = [_public(a) for a in db_slots.adults_of_dependent(d["dni"])]
    return {
        "item": _public(u),
        # cuál cobertura manda, ya resuelta por el server: el cliente no
        # rehace el ranking (ver health_coverage.py)
        "cobertura_principal": health_coverage.primary(u.get("cobertura")),
        # Qué tan probable es que venga, con el porqué al lado: el número solo
        # no sirve para atender a nadie (ver patient_ranking.py).
        "ranking": patient_ranking.by_dni(dni),
        "dependientes": deps,
        "a_cargo_de": [_public(a) for a in db_slots.adults_of_dependent(dni)],
    }


def patient_turnos(dni, identity):
    """Los turnos de esta persona para su ficha (Salud, 30.10: «cuesta mucho
    encontrar qué turnos tiene cada paciente»): todos, pendientes y resueltos.
    Primero lo que la mesa puede atender (pendientes y aprobados por venir),
    del más cercano al más lejano; después lo demás, el más reciente primero
    (`_orden_ficha`). Solo lo que la cuenta puede ver
    (`booking_requests.staff_can_see`: un médico, los suyos; una cuenta de
    centro, los de su centro). Un pedido sin horario muestra el turno que se
    había pedido (`requested_at`)."""
    from datetime import datetime, timezone
    from app.core import tz
    from app.modules.turnos import booking_requests
    from app.patient_web import catalog
    out = []
    ahora = datetime.now(timezone.utc)
    held = archivado = None
    for f in db_booking.requests_of_patient(dni):
        if not booking_requests.staff_can_see(identity or {}, f):
            continue
        when = f.get("turno_dt") or f.get("requested_at")
        local = when.astimezone(tz.tzinfo()) if when else None
        # «Restaurar» solo si vuelve tal cual (2026-11-05); si no, la ficha
        # ofrece «Reprogramar» y nada más. Lo tomado se lee una vez por ficha.
        restaurable = False
        if f.get("estado") == "cancelada" and f.get("cancelled_from") == "aprobada" \
                and f.get("turno_dt") and f["turno_dt"] > ahora:
            from app.modules.turnos import booking_decisions
            if held is None:
                from app.modules.turnos import slot_holds
                held = slot_holds.held_or_offered()
                archivado = booking_decisions._patient_archived(dni)
            restaurable = booking_decisions.restore_blocker(f, held, archived=archivado) is None
        out.append({
            "id": f.get("id"), "estado": f.get("estado"),
            "servicio": catalog.service_name(f.get("servicio_id")) or f.get("servicio_id") or "",
            # Para «Reprogramar»: el turno nuevo sale con el mismo servicio y médico.
            "servicio_id": f.get("servicio_id") or "",
            "restaurable": restaurable,
            "centro": catalog.center_name(f.get("centro_slug")),
            "medico": f.get("medico_asignado") or "",
            "dia": local.strftime("%Y-%m-%d") if local else None,
            "hora": local.strftime("%H:%M") if local else None,
            "pedido": not f.get("turno_dt"),
            # Por venir: se cancela; ya pasó: se marca la asistencia (lo mismo
            # que decide `_desk_cancel_core`).
            "futuro": bool(f.get("turno_dt") and f["turno_dt"] > ahora),
            "asistencia": f.get("asistencia") or "",
        })
    return _orden_ficha(out)


def _orden_ficha(turnos):
    """Lo que se puede atender arriba (2026-11-05): con el orden por fecha
    descendente, un turno de diciembre quedaba antes que el de mañana y un
    pedido pendiente sin fecha, entre los turnos viejos. Un pendiente sin
    fecha va primero de todo. `turnos` llega del más reciente al más viejo
    (`db_booking.requests_of_patient`) y lo pasado conserva ese orden."""
    def atender(t):
        return t["estado"] == "pendiente" or (t["estado"] == "aprobada" and t["futuro"])
    proximos = sorted((t for t in turnos if atender(t)),
                      key=lambda t: (t["dia"] or "", t["hora"] or ""))
    return proximos + [t for t in turnos if not atender(t)]


# ---- POST /api/admin/patients ------------------------------------------

def _telefono_valido(v):
    """Sin letras ni «@» y con al menos 6 dígitos. Misma regla que
    admin-patients.mjs:telefonoValido."""
    s = str(v or "").strip()
    return not re.search(r"[^\W\d_]|@", s) and len(re.sub(r"\D", "", s)) >= 6


def _body_fields(body):
    """Normaliza los campos editables comunes de alta/edición. Devuelve
    (campos, error)."""
    campos = {}
    for k in ("nombre", "apellido", "contacto", "email"):
        if k in body:
            campos[k] = str(body.get(k) or "").strip() or None
    # El teléfono que carga la mesa se guarda igual que el que carga el
    # paciente: forma canónica, decidida en un solo lugar (accounts._norm_phone →
    # format.tel_e164). Sin esto la misma persona quedaba con dos formas
    # distintas según por dónde entró su ficha.
    # Un email en «Teléfono» (25/10, demo) se normalizaba a nada y borraba el
    # dato en silencio: se rechaza, igual que una fecha que no se entiende.
    if campos.get("contacto"):
        if not _telefono_valido(campos["contacto"]):
            return None, "Esto no es un teléfono. Escribí el número, por ejemplo 2254 44-5566."
        campos["contacto"] = accounts._norm_phone(campos["contacto"])
    if "sexo" in body:
        sexo = (str(body.get("sexo") or "").strip().upper()) or None
        if sexo is not None and sexo not in _SEXOS:
            return None, "sexo inválido (M, F o X)"
        campos["sexo"] = sexo
    if "nacimiento" in body:
        nac = str(body.get("nacimiento") or "").strip() or None
        if nac is not None and not re.match(r"^\d{4}-\d{2}-\d{2}$", nac):
            return None, "nacimiento inválido (usar AAAA-MM-DD)"
        campos["nacimiento"] = nac
    if "activo" in body:
        campos["activo"] = bool(body.get("activo"))
    return campos, None


def _coverage_body(body, dni, existente=None):
    """La cobertura se normaliza con la MISMA función que la cuenta del
    paciente (accounts._norm_coverage): quién figura en el padrón lo decide
    puco.py del lado del server, no el body — que la carga la mesa en vez del
    paciente no cambia esa regla. `principal` sale de health_coverage.py."""
    if "cobertura" not in body:
        return None
    from app.cuentas import accounts
    return accounts._norm_coverage(body.get("cobertura"), dni=dni,
                                   existente=(existente or {}).get("cobertura"))


def create_patient(body):
    dni = _ids.dni(body.get("dni"))
    if not _DNI_RE.match(dni):
        raise ApiError({"error": "DNI inválido (7 a 9 dígitos)."}, status=400)
    campos, err = _body_fields(body)
    if err:
        raise ApiError({"error": err}, status=400)
    if not campos.get("nombre") or not campos.get("apellido"):
        raise ApiError({"error": "Faltan nombre y apellido."}, status=400)
    # HSI's own new-patient screen needs document, sex, names and birth date.
    # The desk has the person in front of them once: ask now, not at the
    # moment the appointment has to reach HSI.
    if not campos.get("nacimiento") or not campos.get("sexo"):
        raise ApiError({"error": "Faltan fecha de nacimiento y sexo."}, status=400)
    if db_accounts.get_user(dni):
        raise ApiError({"error": "Ya existe un paciente con ese DNI."}, status=409)

    password = str(body.get("password") or "").strip()
    generada = None
    if not password:
        # temporal legible para dictar en la mesa; se muestra UNA vez
        generada = secrets.token_urlsafe(9)
        password = generada
    elif len(password) < 8:
        raise ApiError({"error": "La contraseña tiene que tener al menos 8 caracteres."}, status=400)

    db_accounts.create_user(dni, campos.get("nombre"), campos.get("apellido"),
                     campos.get("nacimiento"), campos.get("sexo"),
                     campos.get("contacto"), password, email=campos.get("email"),
                     cobertura=_coverage_body(body, dni))
    out = {"ok": True, "item": _public(db_accounts.get_user(dni))}
    if generada:
        out["password_temporal"] = generada
    return out


# ---- POST /api/admin/patients/<dni>/temp-password ----------------------

def temp_password(dni):
    """A new temporary password for an existing patient, from the desk
    (TASK-50, 26.10): the way back in for someone without an email who
    forgot theirs — recovery only goes by email. Same readable temporary as
    the one creating a patient prints, shown ONCE; db_accounts.set_password closes
    every open session and voids unused recovery links."""
    if not db_accounts.get_user(dni):
        raise ApiError({"error": "Paciente no encontrado."}, status=404)
    generada = secrets.token_urlsafe(9)
    db_accounts.set_password(dni, generada)
    return {"ok": True, "password_temporal": generada}


# ---- PUT /api/admin/patients/<dni> -------------------------------------

def edit_patient(dni, body):
    actual = db_accounts.get_user(dni)
    if not actual:
        raise ApiError({"error": "no encontrado"}, status=404)
    campos, err = _body_fields(body)
    if err:
        raise ApiError({"error": err}, status=400)
    # mismo contrato que el resto: la key que no viene, no se toca (mandar
    # `cobertura` vacía SÍ es un borrado deliberado)
    if "cobertura" in body:
        campos["cobertura"] = _coverage_body(body, dni, actual)
    if ("nombre" in campos and not campos["nombre"]) or \
       ("apellido" in campos and not campos["apellido"]):
        raise ApiError({"error": "Nombre y apellido no pueden quedar vacíos."}, status=400)
    # Archivar entra SÓLO por `archive_patient` (2026-10-23): un PUT con
    # activo=false se saltearía los turnos vivos, la lista de espera y las
    # sesiones. Restaurar (activo=true) sigue siendo este PUT.
    if campos.get("activo") is False and actual.get("activo") is not False:
        del campos["activo"]
        out = archive_patient(dni)  # con turnos vivos: 409 antes de escribir nada
        out["item"] = _public(db_accounts.user_admin_update(dni, campos))
        return out
    item = db_accounts.user_admin_update(dni, campos)
    return {"ok": True, "item": _public(item)}


# ---- personas a cargo ---------------------------------------------------

_RELATIONSHIPS = ("hijo", "hija", "tutelado", "tutelada", "otro")


def dependent_save(adulto_dni, body):
    """Alta o corrección de un chico a cargo. Es idempotente por (adulto, dni):
    el mismo POST con otro nombre lo edita. Para que quede a cargo de los DOS
    padres se llama una vez por cada adulto — misma fila, otro adulto_dni."""
    if not db_accounts.get_user(adulto_dni):
        raise ApiError({"error": "no encontrado"}, status=404)

    dni = _ids.dni(body.get("dni"))
    if not _DNI_RE.match(dni):
        raise ApiError({"error": "DNI inválido (7 a 9 dígitos)."}, status=400)
    if dni == adulto_dni:
        raise ApiError({"error": "Una persona no puede estar a cargo de sí misma."}, status=400)
    nombre = str(body.get("nombre") or "").strip()
    apellido = str(body.get("apellido") or "").strip()
    if not nombre or not apellido:
        raise ApiError({"error": "Faltan nombre y apellido."}, status=400)
    nacimiento = str(body.get("nacimiento") or "").strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", nacimiento):
        raise ApiError({"error": "Falta la fecha de nacimiento (AAAA-MM-DD)."}, status=400)
    sexo = (str(body.get("sexo") or "").strip().upper()) or None
    if sexo is None:
        # Never default it: HSI asks for the sex on the document.
        raise ApiError({"error": "Falta el sexo."}, status=400)
    if sexo not in _SEXOS:
        raise ApiError({"error": "sexo inválido (M, F o X)"}, status=400)
    vinculo = (str(body.get("vinculo") or "").strip().lower()) or "hijo"
    if vinculo not in _RELATIONSHIPS:
        vinculo = "otro"

    item = db_slots.save_dependent(adulto_dni, dni, nombre, apellido,
                                  nacimiento, sexo, vinculo)
    out = _public(item)
    out["adultos"] = [_public(a) for a in db_slots.adults_of_dependent(dni)]
    return {"ok": True, "item": out}


def dependent_delete(adulto_dni, dep_dni):
    """Lo saca de ESTE adulto. Si está a cargo de otro, sigue estándolo: la
    respuesta devuelve con quién quedó, para poder decirlo en pantalla."""
    ok = db_slots.delete_dependent(adulto_dni, dep_dni)
    if not ok:
        raise ApiError({"error": "no encontrado"}, status=404)
    return {"ok": True,
            "adultos": [_public(a) for a in db_slots.adults_of_dependent(dep_dni)]}


# ---- DELETE /api/admin/patients/<dni> ----------------------------------

def archive_patient(dni, identity=None, cancelar_turnos=False, request=None):
    """Archivar es coherente en cada tabla con su DNI (`db_accounts.DNI_ON_ARCHIVE`,
    2026-10-23, problems.md №6). Con turnos vivos (pendientes o por venir)
    responde 409 con la lista y no cambia nada; la mesa confirma y vuelve
    con `cancelar_turnos`: se cancelan uno por uno por el mismo camino que
    «Cancelar turno» (`_desk_cancel_core`: libera el lugar, avisa al
    paciente) y recién entonces se archiva. Uno que esta cuenta no puede
    cancelar (otro centro) deja al paciente sin archivar, con el mismo 409."""
    if not db_accounts.get_user(dni):
        raise ApiError({"error": "no encontrado"}, status=404)
    vivos = db_slots.live_requests_of(dni)
    cancelados = 0
    if vivos and cancelar_turnos:
        from app.modules.turnos import booking_decisions, booking_requests
        for fila in vivos:
            row, err = booking_requests._staff_row_or_error(identity or {}, int(fila["id"]))
            if err is not None:
                continue
            status, _payload = booking_decisions._desk_cancel_core(
                identity or {}, row, int(fila["id"]), request)
            if status == 200:
                cancelados += 1
        vivos = db_slots.live_requests_of(dni)
    if vivos:
        from app.core import tz
        from app.modules.turnos import booking_requests
        from app.patient_web import catalog
        turnos = []
        ajenos = 0
        for f in vivos:
            # What this account cannot see is not described (404 convention,
            # admin-cola-y-agenda.md): another center's turno is only counted.
            _row, err = booking_requests._staff_row_or_error(identity or {}, int(f["id"]))
            if err is not None:
                ajenos += 1
                continue
            local = f["turno_dt"].astimezone(tz.tzinfo()) if f.get("turno_dt") else None
            turnos.append({
                "id": f.get("id"), "estado": f.get("estado"),
                "servicio": catalog.service_name(f.get("servicio_id")) or f.get("servicio_id") or "",
                "dia": local.strftime("%Y-%m-%d") if local else None,
                "hora": local.strftime("%H:%M") if local else None,
            })
        # `turnos_cancelados`: los que SÍ se cancelaron en este pedido antes de
        # toparse con uno ajeno — la mesa tiene que saber que ya no están.
        raise ApiError({"error": "turnos_activos", "turnos": turnos,
                        "turnos_ajenos": ajenos,
                        "turnos_cancelados": cancelados}, status=409)
    item = db_accounts.archive_user(dni)
    return {"ok": True, "item": _public(item), "turnos_cancelados": cancelados}


# ---- POST /api/admin/patients/<dni>/clinical-history --------------------

def save_clinical_history(dni, body, identity=None):
    """Reemplazo atómico de la historia clínica digitalizada de UNA fuente
    para este DNI — la puerta HTTP que scripts/seed_patient_account.py usa en
    vez de tocar Postgres directo (prod no expone su base hacia afuera).
    `solo_admin` (checked by the caller): mismo criterio que admin_users.py —
    alta/reemplazo de datos clínicos no es tarea de mesa de admisión."""
    if not db_accounts.get_user(dni):
        raise ApiError({"error": "no encontrado"}, status=404)
    records = body.get("records")
    if not isinstance(records, list):
        raise ApiError({"error": "records tiene que ser una lista"}, status=400)
    source_system = str(body.get("source_system") or "local_history").strip()
    # The import replaces a patient's clinical record: it leaves its access
    # row first, and without the row nothing is written (security review
    # 2026-11, M-3).
    try:
        access_log.record(identity, "edicion", dni, target_id="clinical-history")
    except access_log.AccessLogUnavailable as exc:
        status, payload = access_log.error_body(exc)
        raise ApiError(payload, status=status)
    except access_log.AccessLogFailed:
        raise ApiError({"error": "registro_de_accesos_no_disponible"}, status=500)
    try:
        count = db_clinical.clinical_history_replace(dni, records, source_system)
    except ValueError as e:
        raise ApiError({"error": str(e)}, status=400)
    return {"ok": True, "dni": dni, "source_system": source_system, "count": count}
