"""Exclusive catalog schedule ownership; no new persisted mode flag."""
import json
from copy import deepcopy

from app.core import text_fold

# In the themed clusters (cardiologia, salud-mental…) sharing a cluster IS
# sharing a specialty; in the catch-all buckets different specialties coexist
# (in `otras` a pulmonologist does NOT see dermatology patients; in
# `diagnostico` each doctor covers a different study), so there the doctor's
# specialty must additionally match the service name. This is the rule the
# automatic doctor assignment already used; ownership has to use the SAME one,
# otherwise a service in `otras` claims (and, on a confirmed save, wipes the
# schedules of) every doctor of the hospital in that bucket.
CATCHALL_CLUSTERS = frozenset({"otras", "diagnostico", "grupos"})


def norm_specialty(text):
    return text_fold.strip_nonspacing(str(text or "").lower()).strip()


def specialty_matches_service(especialidad, nombre_servicio):
    a, b = norm_specialty(especialidad), norm_specialty(nombre_servicio)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    # common root ("ecografia"/"ecografista"): 6+ characters of shared prefix
    shared = 0
    for x, y in zip(a, b):
        if x != y:
            break
        shared += 1
    return shared >= 6


def has_schedule(doc):
    agenda = (doc or {}).get("agenda") or {}
    return bool(agenda.get("dias") or agenda.get("franjas"))


# ---------------------------------------------------------------- same schedule?
# The editors re-send a stored schedule with their own defaults filled in
# (`cupos: {web: 1, reservado: 0, sobreturnos: 0}`), a single window as
# `franjas`, lists in another order. None of that is a schedule change, and a
# save that does not change the schedule must never ask about — or touch — the
# doctors it supersedes. Compare MEANING, with the defaults the booking engine
# itself applies (`desde` 09:00, `hasta` 12:00, `cada_min` 20, `cupos.web` 1).

_NOT_SCHEDULE = ("excepciones", "dias_anticipacion", "apertura_turnos")
# What an absent quota means to the engine. The editor writes `overbooking_percent: 0`
# back for a window that never had it (seen live on prod, 2026-10-14).
_DEFAULT_QUOTAS = {"web": 1, "reservado": 0, "sobreturnos": 0, "overbooking_percent": 0}


def _blank(value):
    return value is None or value == "" or value == [] or value == {}


def _clean(value):
    """Absent, null, "" , [] and {} are the same thing."""
    if isinstance(value, dict):
        cleaned = {k: _clean(v) for k, v in value.items()}
        return {k: v for k, v in cleaned.items() if not _blank(v)}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _entries(band):
    """One canonical entry per DAY of the window: two windows with the same
    hours (Tue, Thu) and one window with both days are the same schedule."""
    quotas = dict(_DEFAULT_QUOTAS)
    for key, value in _clean(band.get("cupos") or {}).items():
        quotas[key] = _int(value, value) if key in _DEFAULT_QUOTAS else value
    rest = _clean({k: v for k, v in band.items() if k not in _NOT_SCHEDULE and k not in ("dias", "cupos")})
    base = dict(rest, desde=rest.get("desde") or "09:00", hasta=rest.get("hasta") or "12:00",
                cada_min=_int(rest.get("cada_min"), 20) or 20, cupos=quotas)
    days = sorted({d for d in (_int(x, None) for x in band.get("dias") or []) if d is not None}) or [None]
    return [json.dumps(dict(base, dia=day), sort_keys=True, ensure_ascii=False, default=str) for day in days]


def canonical_agenda(agenda):
    agenda = agenda if isinstance(agenda, dict) else {}
    bands = agenda.get("franjas") or ([agenda] if agenda.get("dias") else [])
    exceptions = agenda.get("excepciones") or []
    return {
        "franjas": sorted({entry for b in bands if isinstance(b, dict) for entry in _entries(b)}),
        "excepciones": sorted(json.dumps(_clean(e), sort_keys=True, ensure_ascii=False, default=str)
                              for e in exceptions if isinstance(e, dict)),
    }


def agenda_unchanged(new, stored):
    """True when `new` describes the same weekly schedule and closures as `stored`."""
    return canonical_agenda(new) == canonical_agenda(stored)


def _centers(doctor):
    centers = {c.get("centro") for c in doctor.get("centros") or []}
    centers.add(doctor.get("centro"))
    return centers


def _tagged(doctor, service):
    """A window of the doctor's own schedule names this service (a stated fact)."""
    agenda = doctor.get("agenda") or {}
    bands = agenda.get("franjas") or ([agenda] if agenda.get("dias") else [])
    if doctor.get("centro") == service.get("centro") and any(f.get("servicio") == service.get("id") for f in bands):
        return True
    return any(c.get("centro") == service.get("centro")
               and any(f.get("servicio") == service.get("id") for f in c.get("franjas") or [])
               for c in doctor.get("centros") or [])


def _specialties(doctor):
    """The doctor's specialty names: the first, then the optional second one
    (`especialidad_2`, hospital TASK-35)."""
    return [e for e in (doctor.get("especialidad"), doctor.get("especialidad_2")) if e]


def _cluster_member(doctor, service):
    cluster = service.get("cluster")
    if not (cluster and service.get("centro") in _centers(doctor)):
        return False
    # Each of the doctor's specialties brings its own cluster; in a catch-all
    # cluster the SPECIALTY of that very cluster has to match the service name.
    for cl, esp in ((doctor.get("cluster"), doctor.get("especialidad")),
                    (doctor.get("cluster_2"), doctor.get("especialidad_2"))):
        if cl != cluster:
            continue
        if cluster not in CATCHALL_CLUSTERS or specialty_matches_service(esp, service.get("nombre")):
            return True
    return False


def _stated_elsewhere(doctor, service):
    """Every window the doctor keeps at the service's center names ANOTHER
    service: the doctor has said what they do there, and it is not this.
    Only `franjas` count: a single flat window (`agenda.dias`) is offered to
    every service whatever it says (site_availability._service_schedule), so
    reading its tag here would take minutes away."""
    where = service.get("centro")
    bands = [f for c in doctor.get("centros") or [] if c.get("centro") == where for f in c.get("franjas") or []]
    if not bands and doctor.get("centro") == where:
        bands = (doctor.get("agenda") or {}).get("franjas") or []
    return bool(bands) and all(f.get("servicio") and f.get("servicio") != service.get("id") for f in bands)


_PEERS_MEMO = {"state": None, "peers": None, "kept": {}}


def _catalog_peers():
    """The live doctors, memoized on the catalog overlay snapshot (the same
    invalidation site_catalog._services uses) — `belongs` runs per doctor and
    per service, the merge must not. The same list the desk's assignable pool
    reads (booking_queue._doctor_pool_by_service), whose 409 is the
    guarantee an emptied pool would break."""
    from app.patient_web import catalog
    state = catalog.overlay_state("medicos")
    memo = _PEERS_MEMO["state"]
    if memo is None or memo[0] != state[0] or memo[1] is not state[1]:
        _PEERS_MEMO.update(peers=catalog.doctors() or [], state=state, kept={})
    return _PEERS_MEMO["peers"], _PEERS_MEMO["kept"]


def person(doctor):
    """A catalog row that is a PERSON: not a demo row and not a role-row
    («Enfermería», «Técnico/a»: flagged `rol`, they feed hours but are nobody's
    doctor). What the directory-style lists and the automatic suggestion take."""
    return isinstance(doctor, dict) and not doctor.get("rol") and not doctor.get("demo")


def assignable(doctor):
    """A person the desk can name as a service's doctor: `person` with an id
    that is not a disabled row. The cut before `belongs` for the desk's pool
    (the combo «Médico», «Derivar a»)."""
    return person(doctor) and bool(doctor.get("id")) and not doctor.get("_borrado")


def offering(doctor):
    """A catalog row whose windows may feed what the patient is OFFERED: every
    row but the demo ones. Role-rows stay — their time windows still feed the
    offers and the desk's grid (site_catalog._professionals) — which is why
    this cut is wider than `person`; both are on purpose."""
    return not doctor.get("demo")


def pool(service, doctors, keep=assignable):
    """The service's doctors out of `doctors` (catalog rows), in their order:
    `belongs` over the rows `keep` lets through — `assignable` (the desk's
    pool, the default), `person` (the automatic suggestion, which never
    needed an id) or `offering` (what the patient is offered, the desk's grid).
    One owner for «who are this service's doctors»; call sites no longer
    re-write the cut before `belongs`."""
    return [m for m in doctors if keep(m) and belongs(m, service)]


def _someone_stays(service, peers):
    # the same cut as the desk's assignable pool (`pool`)
    return any(assignable(m)
               and (_tagged(m, service) or (_cluster_member(m, service) and not _stated_elsewhere(m, service)))
               for m in peers)


def _keeps_someone(service, peers):
    """Some person stays in the pool once the stated-elsewhere doctors leave."""
    if peers is not None:
        return _someone_stays(service, peers)
    try:
        peers, kept = _catalog_peers()
    except Exception:  # noqa: BLE001 — no catalog: keep the broad pool
        return False
    key = tuple(str(service.get(k) or "") for k in ("id", "centro", "cluster", "nombre"))
    if key not in kept:
        kept[key] = _someone_stays(service, peers)
    return kept[key]


def belongs(doctor, service, peers=None):
    """The service's pool of doctors for OFFERS and automatic assignment:
    tagged, or sharing its cluster at its center (in the catch-all clusters the
    specialty must match too). Broad on purpose — a pool, not a claim.

    A cluster mate whose windows at that center all name OTHER services
    leaves the pool, but only while someone else stays in it (the reference-deployment counterpart
    2026-10-23: Berisso does only Ergometría and was named under Cardiología;
    an emptied pool would leave the desk unable to assign anyone, so those
    services keep the broad pool until the hospital says who sees them).
    Only for a service WITHOUT hours of its own: there the doctor's minutes
    come from their own windows, so one tagged elsewhere offers nothing here
    and leaving changes no offer, calendar or capacity. A service that runs
    its own schedule keeps its whole pool (its takeover leaves its doctors
    exactly the windows tagged for other services — decisions 2026-10-14).
    `peers` is the doctor list to judge that by; None reads the catalog."""
    if _tagged(doctor, service):
        return True
    if not _cluster_member(doctor, service):
        return False
    if (not has_schedule(service) and _stated_elsewhere(doctor, service)
            and _keeps_someone(service, peers)):
        return False
    return True


def serves(doctor, service):
    """The strict relation «this doctor IS this service»: a window tagged with
    it, or the doctor's specialty is the service's name at one of the doctor's
    centers. It decides what is DESTRUCTIVE — who the «reiniciará los horarios»
    dialog lists and whose own windows a confirmed service save removes.
    Colleagues of a neighbouring specialty in the same cluster (psychomotricity /
    psychopedagogy, diabetology / nutrition) share the pool with the service
    but are not its doctors, and their own windows are never wiped."""
    if _tagged(doctor, service):
        return True
    return bool(service.get("centro") in _centers(doctor)
                and any(specialty_matches_service(esp, service.get("nombre")) for esp in _specialties(doctor)))


def managed_services(doctor, services):
    """Services whose schedule applies to this doctor at runtime (calendar,
    capacity, read-only form): the POOL relation, the same one the booking
    engine uses — locking and offering must agree."""
    return [s for s in services if not s.get("_borrado") and has_schedule(s) and belongs(doctor, s)]


def doctor_has_schedule(doctor):
    return has_schedule(doctor) or any(c.get("franjas") for c in doctor.get("centros") or [])


def reset_doctor(doctor, service=None):
    """The doctor's catalog document with the schedule the service takes over
    removed. With a `service`, only what that service covers goes: the windows
    of ITS center that are untagged or tagged with it (a window tagged with
    another service of the same center, and every other center, stay). Without
    one — the legacy call — the whole schedule goes."""
    doc = deepcopy({k: v for k, v in doctor.items() if not k.startswith("_")})
    if not service:
        doc.pop("agenda", None)
        for center in doc.get("centros") or []:
            center["franjas"] = []
            center["excepciones"] = []
        return doc
    where, sid = service.get("centro"), service.get("id")

    def foreign(band):
        return str((band or {}).get("servicio") or "") not in ("", str(sid))

    flat = doc.get("agenda") or {}
    if doc.get("centro") == where and flat:
        bands = flat.get("franjas") or ([flat] if flat.get("dias") else [])
        kept_flat = [b for b in bands if foreign(b)]
        if kept_flat:
            doc["agenda"] = {"franjas": kept_flat, "excepciones": flat.get("excepciones") or []}
        else:
            doc.pop("agenda", None)
    for center in doc.get("centros") or []:
        if center.get("centro") != where:
            continue
        kept = [f for f in center.get("franjas") or [] if foreign(f)]
        center["franjas"] = kept
        if not kept:
            center["excepciones"] = []
    return doc


def reset_changes(doctor, service):
    """Whether resetting for `service` would actually remove something."""
    clean = {k: v for k, v in doctor.items() if not k.startswith("_")}
    return reset_doctor(doctor, service) != clean


def doctor_own_agendas(doctor, service):
    """The doctor's OWN windows at the service's center, ignoring schedule
    ownership and service closures: the doctor half of `doctor_agendas`. Needed
    on its own where a service-owned schedule and the doctor's historic bands
    coexist (the staff-only extra capacity at approval)."""
    agendas = [{"franjas": c.get("franjas") or [], "excepciones": c.get("excepciones") or []}
               for c in doctor.get("centros") or [] if c.get("centro") == service.get("centro")]
    if not agendas and doctor.get("centro") == service.get("centro") and doctor.get("agenda"):
        agendas = [doctor["agenda"]]
    return agendas


def doctor_agendas(doctor, service):
    """Exactly one owner, including historic conflicting catalog documents.

    A service without hours of its own can still be CLOSED from the panel
    (Ergometría del Hospital, 15/10–14/11: «ya dieron esos turnos», la contraparte técnica
    2026-10-23); its closures apply on top of every doctor agenda feeding it."""
    if has_schedule(service):
        return [service["agenda"]] if belongs(doctor, service) else []
    agendas = doctor_own_agendas(doctor, service)
    closed = ((service.get("agenda") or {}).get("excepciones")) or []
    if closed:
        agendas = [dict(a, excepciones=list(a.get("excepciones") or []) + list(closed)) for a in agendas]
    return agendas
