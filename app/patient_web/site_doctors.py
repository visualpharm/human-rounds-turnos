"""patient_web/site_doctors.py — Doctors of the public site.

Name matching, sex guess, the avatar atlas and portrait style, specialty labels, the referral rule and the doctor-to-service tagging.

Split out of the old `patient_web/patient_site.py` (TASK-32). What lives in another domain module is reached
through that module (`site_catalog._services()`), looked up at CALL time, so a test patching
`site_catalog._services` patches what this module uses too. The modules form layers: an import never
points back up (`tests/test_patient_site_domain_modules.py`).
State that a function rebinds with `global` (`_sprite_memo`, `_sprite_deadline`) stays private to
this module and is not re-exported: a copy on `patient_web/site_*.py` would go stale.
"""

import re
import time

from app.patient_web import avatar_sprite
from app.patient_web import doctor_photos

from app.patient_web import site_base
from app.patient_web import site_catalog
from app.patient_web import site_centers


def _avatar(nombre):
    """(initials, color index) for a professional's avatar."""
    parts = [p for p in str(nombre or "").split()
             if p and p[0].isalpha() and p not in ("Dr./Dra.", "Dr.", "Dra.", "Lic.")]
    ini = ((parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()
           if parts else "?")
    return ini, sum(ord(ch) for ch in str(nombre or "")) % 6


def _name_tokens(s):
    """Order-independent name tokens for HSI/MSD's ``APELLIDO, Nombre`` form.

    The public catalog stores natural-order full names and may include a
    middle name, so exact normalized-string equality loses real matches such
    as ``BUSTOS, VICTORIA`` ↔ ``Victoria Mailén Bustos``.
    """
    ignored = {"dr", "dra", "lic", "doctor", "doctora"}
    return {token for token in re.findall(r"[a-z0-9]+", site_base._norm_name(s))
            if token not in ignored}


def _doctor_catalog_by_name(nombre, servicio=None):
    """Best catalog doctor for an external schedule name, or ``None``."""
    wanted = _name_tokens(nombre)
    if len(wanted) < 2:
        return None
    candidates = []
    for medico in site_catalog._doctors():
        if servicio:
            centro = servicio.get("centro")
            centros = {medico.get("centro")}
            centros.update(
                item.get("centro") for item in (medico.get("centros") or [])
                if item.get("centro")
            )
            if centro and centro not in centros:
                continue
            if servicio.get("cluster") and servicio["cluster"] not in site_catalog._doctor_clusters(medico):
                continue
        tokens = _name_tokens(medico.get("nombre"))
        if wanted <= tokens or tokens <= wanted:
            candidates.append(medico)
    return candidates[0] if len(candidates) == 1 else None


def _first_name(nombre):
    """First given-name token (skipping titles) of a full name."""
    for p in str(nombre or "").split():
        if p and p[0].isalpha() and p not in ("Dr./Dra.", "Dr.", "Dra.", "Lic."):
            return p
    return ""


# Common Argentine given names whose ending isn't enough to infer sex (or
# contradicts it): only used to pick Dr./Dra. — when in doubt, no title.
_NAMES_F = {
    "solange", "carmen", "isabel", "mercedes", "beatriz", "raquel", "ines", "pilar",
    "rocio", "azucena", "noelia", "abril", "ester", "esther", "belen", "soledad",
    "mailen", "guadalupe", "consuelo", "dolores", "iris", "ruth", "nair",
}
_NAMES_M = {
    "victor", "simon", "jose", "juan", "nicolas", "tomas", "matias", "lucas", "andres",
    "sebastian", "cristian", "german", "hernan", "adrian", "fabian", "damian", "julian",
    "gaston", "ivan", "joaquin", "agustin", "martin", "ramon", "hector", "ruben",
    "dante", "cosme", "felix", "ariel", "gabriel", "rafael", "manuel", "miguel",
    "daniel", "ezequiel", "emanuel", "nahuel", "elias", "jesus", "luca", "bautista",
}
# End in 'a' but are masculine.
_NAMES_A_MALE = {"luca", "bautista", "elias", "matias", "tobias", "jonas", "andrea_m"}


def _guess_sex(nombre):
    """'f' / 'm' / None from the given name. Conservative: only returns a sex
    when there's a strong signal (curated list or classic ending), None
    otherwise — so the Dr./Dra. title is never set at random."""
    key = site_base._norm_name(_first_name(nombre))
    if not key:
        return None
    if key in _NAMES_M:
        return "m"
    if key in _NAMES_F:
        return "f"
    if key.endswith("a") and key not in _NAMES_A_MALE:
        return "f"
    if key.endswith("o"):
        return "m"
    return None


def _doctor_by_id(mid):
    for m in site_catalog._doctors():
        if m.get("id") == mid:
            return m
    return None


# The atlas lookup, memoised for the same 5 s window `avatar_sprite.styles()`
# holds its own result for, and dropped by the same `invalidate()` (its epoch
# counter is the shared signal, so an admin's photo upload still lands
# immediately). This is NOT a duplicate of that cache: `styles()` is O(1) once
# warm, but the *argument* it needs is the whole roster, and building that list
# means re-reading medicos.json and re-merging the admin overlay. The callers
# ask per face — 90 doctors on /api/doctors, 62 across the directory's clusters
# — so the roster was being rebuilt ~90 times to paint one page: ~1400 stat()
# and ~200 listdir() syscalls per request. Cheap on a warm laptop, and the
# reason the demo's directory took a minute to appear while the box was busy.
_sprite_memo = None       # (epoch, {mid: css})
_sprite_deadline = 0.0


def _avatar_sprite():
    """{mid: css} for the shared 96 px atlas — one image for every small round
    portrait on the site. Built over the WHOLE roster on purpose, so the home
    page's directory and /medicos reference the same file and the second page
    costs nothing. {} whenever it can't be built; the caller falls back."""
    global _sprite_memo, _sprite_deadline
    memo = _sprite_memo
    if memo and memo[0] == avatar_sprite._epoch and time.monotonic() < _sprite_deadline:
        return memo[1]
    epoca = avatar_sprite._epoch
    try:
        out = avatar_sprite.styles([m.get("id") for m in site_catalog._professionals()],
                                   extra=site_centers._center_faces())
    except Exception:
        return {}
    _sprite_memo = (epoca, out)
    _sprite_deadline = time.monotonic() + avatar_sprite.TTL
    return out


def warm_avatar_sprite():
    """Builds the atlas once, outside any request. Called from server.py's
    startup in a background thread so the FIRST visitor after a deploy never
    pays for it — a rebuild always costs something (fractions of a second
    locally, more on a small box), and there is no reason the person who
    happens to load the page first has to be the one who pays it. Same {}
    on failure as `_avatar_sprite()`; nobody is waiting on this call."""
    _avatar_sprite()


def _photo_style(mid, chico=False):
    """Portrait's inline style: the admin's upload (doctor_photos.py, saved in
    data/medico-fotos/) if there is one, otherwise the offline sprite
    (webdata/medico-photo-sprite.webp, positioned in %, works at any avatar
    size). '' = no photo (initials avatar).

    `chico=True` = this is one of the little round avatars (44 px in the
    directory cards, 34-44 px in listing.js), so it comes out of the shared
    runtime atlas instead of its own request: the home page was firing 48
    separate full-resolution portrait requests to paint 44 px circles. The
    full-size portrait on a doctor's own page keeps `chico=False` and its own
    file — cropping that one out of a 96 px tile would look terrible."""
    if chico:
        s = _avatar_sprite().get(mid)
        if s:
            return s
    admin_url = doctor_photos.photo_url(mid)
    if admin_url:
        return f"background-image:url('{admin_url}');background-size:cover;background-position:center"
    man = site_base._load("medico-photo-sprite.json") or {}
    e = (man.get("entries") or {}).get(mid)
    if not e:
        return ""
    tile = man.get("tile") or 128
    cols = max(1, int((man.get("width") or tile) // tile))
    rows = max(1, int((man.get("height") or tile) // tile))
    px = (e["x"] // tile) / (cols - 1) * 100 if cols > 1 else 0
    py = (e["y"] // tile) / (rows - 1) * 100 if rows > 1 else 0
    img = man.get("image") or "/webdata/medico-photo-sprite.webp"
    return (f"background-image:url('{img}?v=30');"
            f"background-size:{cols * 100}% {rows * 100}%;"
            f"background-position:{px:.3f}% {py:.3f}%")


def center_circle_style(slug):
    """Inline `background-*` style of center `slug`'s small round picture, cut
    out of the shared atlas around its focus. '' when the center has no photo
    or the atlas can't be built — the caller decides the fallback (the whole
    photo, centred), the only case a circle may request the full file."""
    return _avatar_sprite().get("center:" + str(slug)) or ""


def _doctor_avatar_fields(m):
    # listing.js draws this one at 34-44 px -> shared atlas, not its own file.
    ph = _photo_style(m.get("id") or "", chico=True)
    if ph:
        return {"avatar": ph}
    ini, color = _avatar(m.get("nombre"))
    return {"avatar_ini": ini, "avatar_c": color}


# Specialty in the patient's own words (the product owner, 2026-07-16): in the directory
# people look for the PROFESSIONAL, not the field — they say "I have an
# appointment with a cardiologist", not "with cardiology". The specialty's
# name is shown as the noun for whoever practices it. Base rule "...ology →
# ...ologist" (Cardiología → Cardiólogo, Neurología infantil → Neurólogo
# infantil) + irregulars; whatever ISN'T a person's profession (Laboratorio,
# Diagnóstico por imágenes, Demanda espontánea, Enfermería) stays as-is.
_SPECIALTY_AS_DOCTOR = {
    "Medicina general": "Médico general",
    "Clínica médica": "Médico general",
    "Cirugía": "Cirujano",
    "Pediatría": "Pediatra",
    "Obstetricia": "Obstetra",
    "Psiquiatría": "Psiquiatra",
    "Ecografía": "Ecografista",
    "Nutrición": "Nutricionista",
    "Terapia ocupacional": "Terapista ocupacional",
    "Psicopedagogía": "Psicopedagogo",
    "Lic. Psicomotricidad": "Psicomotricista",
}


# Looked up case-insensitively (the demo writes «Ginecología y Obstetricia»,
# Pinamar «Ginecología y obstetricia»). A two-field specialty names both
# professions, and «Diagnóstico por imágenes» is a department, not a person
# (demo review 2026-11-15, D14/P16: «Ginecólogo y obstetricia»).
_SPECIALTY_AS_DOCTOR_LOWER = dict(zip(map(str.lower, _SPECIALTY_AS_DOCTOR), _SPECIALTY_AS_DOCTOR.values()))
_SPECIALTY_AS_DOCTOR_LOWER.update({
    "ginecología y obstetricia": "Ginecólogo y obstetra",
    "diagnóstico por imágenes": "Especialista en imágenes",
})
# The feminine title, for a doctor whose pack record says `genero: "f"`.
# Never guessed from the name: a wrong «Cardióloga» is worse than the generic
# title. Words ending in -a/-ista are the same for both; «…ólogo» → «…óloga».
_SPECIALTY_AS_DOCTOR_F = {
    "medicina general": "Médica general",
    "clínica médica": "Médica general",
    "cirugía": "Cirujana",
    "psicopedagogía": "Psicopedagoga",
    "ginecología y obstetricia": "Ginecóloga y obstetra",
}


def _specialty_as_doctor(esp, genero=None):
    """The specialty as the title of whoever practices it («Cardiología» →
    «Cardiólogo», «Cardióloga» with `genero="f"`). A translated specialty
    («Cardiology») matches none of the Spanish rules and stays as it is, so
    English is neutral by construction."""
    esp = (esp or "").strip()
    if not esp:
        return ""
    low = esp.lower()
    if genero == "f" and low in _SPECIALTY_AS_DOCTOR_F:
        return _SPECIALTY_AS_DOCTOR_F[low]
    if low in _SPECIALTY_AS_DOCTOR_LOWER:
        return _SPECIALTY_AS_DOCTOR_LOWER[low]
    i = low.find("ología")
    if i >= 0:
        return esp[:i] + ("óloga" if genero == "f" else "ólogo") + esp[i + len("ología"):]
    return esp


def _doctor_genero(m):
    """'f' / 'm' / None — the pack's `genero` field, never a guess."""
    g = str((m or {}).get("genero") or "").strip().lower()
    return g if g in ("f", "m") else None


def _doctor_specialties(m):
    """The doctor's specialty names as the pack writes them, the first one
    (falling back to its cluster's name) then the optional `especialidad_2`."""
    cl = site_catalog._cluster_by_id(m.get("cluster")) or {}
    out = [m.get("especialidad") or cl.get("nombre") or ""]
    if m.get("especialidad_2"):
        out.append(m["especialidad_2"])
    return [x for x in out if x]


def _doctor_specialty_labels(m, gendered=True):
    """The doctor's specialties as the patient reads them («Cardiólogo»,
    «Cardióloga» for a doctor whose record says `genero: "f"`), the first one
    then the optional second (`especialidad_2`). `gendered=False` gives the
    generic title — the value of the Especialidad filter, so one specialty is
    one filter option whoever practices it."""
    genero = _doctor_genero(m) if gendered else None
    return [x for x in (_specialty_as_doctor(e, genero) for e in _doctor_specialties(m)) if x]


def _doctor_row_specialty(m, servicio=None):
    """The specialty cell of a doctor's row: their title(s), plus the name of
    what the row books when it belongs to another area than the doctor's own
    (Pinamar's cardiologist whose schedule is all Ergometría read
    «Cardiólogo» while «Subí tu orden» booked an ergometry — demo review
    2026-11-15, P4): «Cardiólogo · Ergometría»."""
    texto = " · ".join(_doctor_specialty_labels(m))
    sv = servicio or {}
    propias = {str(x).strip().lower() for x in _doctor_specialties(m)}
    if (sv.get("nombre") and sv.get("cluster") and sv["cluster"] not in site_catalog._doctor_clusters(m)
            and str(sv["nombre"]).strip().lower() not in propias):
        texto = f'{texto} · {sv["nombre"]}' if texto else str(sv["nombre"])
    return texto


def _doctor_tagged_services(m, by_id=None):
    """EVERY service the staff tagged on the doctor's own schedule, in the
    order of the schedule, without repeats. A doctor who does several things
    (Cardiología + Cirugía + Electrocardiograma) is found under each of them,
    not only under the first (on-site tester, 2026-10-21). `by_id` (services
    by id, built once by a caller walking many doctors) spares a catalog copy
    per tag."""
    centros = m.get("centros") or []
    if not centros:
        ag = m.get("agenda") or {}
        franjas = ag.get("franjas") or ([ag] if ag.get("dias") else [])
        centros = [{"centro": m.get("centro"), "franjas": franjas}] if franjas else []
    propios, vistos = [], set()
    for c in centros:
        for fr in ((c or {}).get("franjas") or []):
            sid = str((fr or {}).get("servicio") or "").strip()
            if not sid or sid in vistos:
                continue
            vistos.add(sid)
            s = by_id.get(sid) if by_id is not None else site_catalog._service_by_id(sid)
            if s:
                propios.append(s)
    return propios


def _cluster_doctors_context():
    """What `_cluster_doctors` reads, read ONCE for a page that asks for many
    specialties (the home's cards): the doctors, their clusters and their
    tagged services."""
    by_id = {s.get("id"): s for s in site_catalog._services()}
    pros = site_catalog._professionals()
    return [(m, site_catalog._doctor_clusters(m), _doctor_tagged_services(m, by_id)) for m in pros]


def _cluster_doctors(cid, context=None):
    """The doctors `/d/<cid>` lists, sorted by name, and ``{id(m): service}``
    for those of another specialty who are listed because their schedule
    names a service of this one (a cardiologist who also does Cirugía). The
    ONE answer to «how many doctors does this specialty have»: the page and
    the home's count read it (demo review 2026-11-15, D12/P18). `context`:
    `_cluster_doctors_context()`, shared across the specialties of one page."""
    if context is None:
        context = _cluster_doctors_context()
    meds = [m for m, cls, _t in context if cid in cls]
    extra = {}
    for m, cls, tagged in context:
        if cid in cls or m.get("demo"):
            continue
        propio = next((t for t in tagged if t.get("cluster") == cid), None)
        if propio:
            meds.append(m)
            extra[id(m)] = propio
    return sorted(meds, key=lambda m: m["nombre"]), extra


def _doctor_tagged_service(m):
    """The service the staff tagged on the doctor's own schedule
    (`franjas[].servicio`), or None when no window is tagged. The doctor's
    center first, then any of their centers; within a pool a bookable service
    wins over a walk-in one and otherwise the order of the schedule stands.
    A tag is a stated fact, so it outranks every inference by specialty or
    cluster (hospital report, 2026-10-08: a gynecologist tagged "Ginecología"
    booked into a sibling service that requires a referral)."""
    propios = _doctor_tagged_services(m)
    en_centro = [s for s in propios if s.get("centro") == m.get("centro")]
    for pool in (en_centro, propios):
        for s in pool:
            if s.get("modo") not in site_catalog._NO_APPOINTMENT_MODES:
                return s
        if pool:
            return pool[0]
    return None


# Which of a doctor's candidate services they are booked into when their
# schedule names none. Among the services of the doctor's OWN specialty
# («Ginecología» and «Ginecología — embarazo de alto riesgo»), the
# consultation without an order first (TASK-56, 26.10; the hospital's
# report of 08.10). In the cluster fallback, where other specialties mix in
# («Medicina general» next to the geriatric check-up), the old order stays:
# no-order there would hand a geriatrician to general practice.
_SPECIALTY_MODE_ORDER = ("solicitud", "solicitud_con_orden", "walkin", "walkin_con_orden")
_CLUSTER_MODE_ORDER = ("solicitud_con_orden", "solicitud", "walkin_con_orden", "walkin")


def _doctor_service(m):
    """The service a doctor books into. First by SPECIALTY (service name ≈
    doctor's specialty, diacritic-folded, same center then any): the
    catch-all 'otras' cluster mixes different specialties, and choosing by
    cluster+mode used to send the patient to the wrong service (2026-07-14
    bug: Castagnino, an ophthalmologist, came out with a 'Book appointment' in
    Dermatología). With no name match, falls back to the old criterion: same
    cluster and center (we prefer the bookable one); if none, the cluster's
    in any center."""
    propio = _doctor_tagged_service(m)
    if propio:
        return propio
    esps = [site_base._norm_name(e) for e in (m.get("especialidad"), m.get("especialidad_2")) if e]
    if esps:
        por_esp = [s for s in site_catalog._services() if any(site_base._norm_name(s.get("nombre")) == esp
                                                 or site_base._norm_name(s.get("nombre")).startswith(esp + " ")
                                                 for esp in esps)]
        en_centro_esp = [s for s in por_esp if s.get("centro") == m.get("centro")]
        for pool in (en_centro_esp, por_esp):
            for modo in _SPECIALTY_MODE_ORDER:
                for s in pool:
                    if s.get("modo") == modo:
                        return s
    same = [s for cid in site_catalog._doctor_clusters(m) for s in site_catalog._services() if s.get("cluster") == cid]
    en_centro = [s for s in same if s.get("centro") == m.get("centro")]
    for pool in (en_centro, same):
        for modo in _CLUSTER_MODE_ORDER:
            for s in pool:
                if s.get("modo") == modo:
                    return s
    return None


def _doctor_referral(m):
    """Derives whether the doctor requires a referral from the service that
    matches their own EXACT cluster+center (without the fallback to another
    center in the cluster that _doctor_service does use, because here "don't
    know" must be None, not another center's data). None if no service
    exists with that cluster+center — the cell stays empty instead of lying
    "No referral" (responsable del producto, 2026-07-12)."""
    propio = _doctor_tagged_service(m)
    if propio:
        return site_catalog.requires_referral(propio.get("modo"))
    # The doctor's own specialty in their center first, as _doctor_service
    # does — a cluster like «otras» mixes every specialty, and the first
    # service of the cluster said nothing about this doctor (TASK-56).
    esps = [site_base._norm_name(e) for e in (m.get("especialidad"), m.get("especialidad_2")) if e]
    en_centro = [s for s in site_catalog._services() if s.get("centro") == m.get("centro")]
    por_esp = [s for s in en_centro if any(site_base._norm_name(s.get("nombre")) == esp
                                           or site_base._norm_name(s.get("nombre")).startswith(esp + " ")
                                           for esp in esps)]
    cands = [s for cid in site_catalog._doctor_clusters(m) for s in en_centro if s.get("cluster") == cid]
    for pool, orden in ((por_esp, _SPECIALTY_MODE_ORDER), (cands, _CLUSTER_MODE_ORDER)):
        for modo in orden:
            for s in pool:
                if s.get("modo") == modo:
                    return site_catalog.requires_referral(modo)
    return None
