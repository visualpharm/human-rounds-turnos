"""patient_web/site_catalog.py — The catalog read model of the public site.

The memoised service list, services, clusters, doctors and preparations with their overlay, the walk-in services and the study classification.

Split out of the old `patient_web/patient_site.py` (TASK-32). What lives in another domain module is reached
through that module (`site_catalog._services()`), looked up at CALL time, so a test patching
`site_catalog._services` patches what this module uses too. The modules form layers: an import never
points back up (`tests/test_patient_site_domain_modules.py`).
"""

import re
import unicodedata

from app.core import data_packs
from app.core import i18n
from app.core import pack_l10n
from app.core import site_identity

from app.patient_web import site_base
from app.patient_web import site_centers


def _doctor_clusters(m):
    """Every cluster a doctor belongs to (catalog.doctor_clusters); the catalog
    module is imported lazily, as everywhere else in this file."""
    from app.patient_web import catalog
    return catalog.doctor_clusters(m)


# ---------------------------------------------------------------- services memo
#
# A page pair calls _services() hundreds of times — once per slot
# computation, per candidate service, per section. Every call used to redo
# the WHOLE pipeline: the overlay merge (~80 dict copies plus the rows_by_id
# index — only the raw DB read underneath is TTL-cached in catalog.py), the
# "clínica médica" normalization and, off the pack language, the
# localization pass. That work is identical whenever the inputs didn't move,
# and within a request they almost never do.
#
# So the final list is memoized per (localize, lang) and re-validated on
# EVERY call against a fingerprint of everything the pipeline reads:
#
#   source  — the very object `_load` handed back. Its mtime cache re-parses
#             an edited pack file into a NEW object and a pack-chain change
#             resolves to a different path (different object); both flip this
#             leg. (It also means a test monkeypatching `_load` is picked up
#             for free: a fresh object per call simply never hits.)
#   overlay — catalog.overlay_state(): the admin-overlay snapshot, so an
#             admin write (catalog.invalidate()), a TTL expiry or a DB
#             failure/cooldown flips it too — a fallback-to-base result is
#             never served past the DB's own recovery.
#   l10n    — pack_l10n.localize_state(): the translation-memo snapshot, so a
#             cache-fill or invalidate_memo() shows up exactly as soon as
#             pack_l10n's own 30 s bound says it should — no longer.
#   seams   — the identity of _with_overlay / catalog.merge_with_overlay /
#             pack_l10n.localize themselves, so a swapped implementation
#             (tests patch these constantly; production swaps them in a
#             deploy) never serves the previous implementation's output.
#
# EVERY return is mutation-isolated — hits, published misses, and the
# uncached paths alike (unreadable fingerprint, sustained churn
# fallthrough) — WITHOUT a per-hit recursive copy: published rows are
# FROZEN once (_freeze_jsonish: nested lists/dicts become _FrozList/
# _FrozDict, whose mutators raise) and every hand-out thaws to fresh
# shallow row dicts (_thaw_rows). So a caller's top-level field writes die
# with its own call, and a nested mutation RAISES instead of silently
# reaching the memo or the source/overlay caches the raw pipeline output
# shares its nested fields with — read-only sharing, fail-closed. (Before
# the memo the nested lists were shared straight out of the mtime cache
# with no protection at all, so this is strictly safer than what shipped;
# a per-hit deep copy of the whole catalog was tried first and was SLOWER
# than the legacy pipeline on the real 120-row pack — 0.847 s vs 0.210 s
# per 2,000 calls, 2026-08-16 review — which is why the boundary is
# freeze/thaw and not copy-on-hit.)
#
# Any leg that cannot be read (catalog import blowing up, servicios.json
# missing entirely) bypasses the memo: compute and return, never store —
# unfingerprintable means unverifiable, and unverifiable must not be cached.
# Publishing is additionally gated on pre/post stability and on both legs
# carrying deadline-bearing state (see _services_fingerprint_publishable):
# a result is only ever stored under a fingerprint its own pipeline observed
# unchanged, never under a transient snapshot no later hit would refresh.

_SERVICES_MEMO = {}  # (localize, lang) -> (fingerprint, frozen rows)


class _FrozList(list):
    """A list that refuses mutation — the memo's storage format for a row's
    nested lists (`palabras`, `jobs`...). Instances are built once at publish
    time and SHARED by every hit: reads go through the plain C list ops at
    full list speed, while the mutation surface a caller would need to poison
    the memo (or the mtime/overlay caches) raises instead. Copying idioms
    stay fine and return ordinary lists: `list(x)`, `x + [...]`, `sorted(x)`.
    Unpickles as a plain list — nothing downstream ever needs it frozen."""
    __slots__ = ()

    def _readonly(self, *args, **kwargs):
        raise TypeError(
            "a memoized pack row's nested list is read-only — copy it first (list(x))")

    append = extend = insert = remove = pop = clear = sort = reverse = _readonly
    __setitem__ = __delitem__ = __iadd__ = __imul__ = _readonly

    def __reduce__(self):
        return (list, (list(self),))


class _FrozDict(dict):
    """The same contract as _FrozList for every dict in the stored value —
    the row dicts included (the hit path's `dict(r)` copies those into
    ordinary per-call dicts anyway) and the nested ones especially (`info`,
    `ayuno`...), which are shared across hits as-is. Reads at full dict
    speed, writes raise. `dict(x)` / `{**x}` copy it into an ordinary
    mutable dict."""
    __slots__ = ()

    def _readonly(self, *args, **kwargs):
        raise TypeError(
            "a memoized pack row's nested dict is read-only — copy it first (dict(x))")

    __setitem__ = __delitem__ = clear = pop = popitem = update = setdefault = _readonly
    # `|=` is its own C-level slot inherited from dict (NOT routed through
    # update()/__setitem__), so without this line an in-place merge silently
    # mutated the shared memo storage — the one mutator the review found open.
    __ior__ = _readonly

    def __reduce__(self):
        return (dict, (dict(self),))


def _freeze_jsonish(value):
    """Publish-time storage conversion: list -> _FrozList of frozen
    children, dict -> _FrozDict of frozen children, leaf scalars pass
    through. Every container the stored value reaches becomes read-only,
    because every one of them below the row level is SHARED by every later
    hand-out (_thaw_rows copies only the row dicts). One recursive walk per
    PUBLISH, never per hit — a per-hit recursive copy of the whole catalog
    is what made the memo slower than the legacy pipeline it replaced
    (2026-08-16 review: 0.847 s vs 0.210 s per 2,000 calls on the real
    120-row pack). Unknown object types pass through SHARED — pack data is
    JSON-shaped (json.load / jsonb), the same assumption every mtime cache
    here has always made."""
    if isinstance(value, list):
        return _FrozList(_freeze_jsonish(v) for v in value)
    if isinstance(value, dict):
        return _FrozDict({k: _freeze_jsonish(v) for k, v in value.items()})
    return value


def _thaw_rows(frozen_rows):
    """Hit-time hand-out: a fresh list of fresh shallow copies of the frozen
    rows. The row dicts are new, so a caller's TOP-LEVEL field writes
    (`row["nombre"] = ...`) die with its own call — exactly like the fresh
    dicts the legacy pipeline built on every call; the nested values are the
    shared read-only _FrozList/_FrozDict instances. That split keeps a hit at
    ~one C dict copy per row instead of a recursive walk, and still leaves no
    path for a caller mutation to reach the memo or the source caches."""
    return [dict(r) for r in frozen_rows]


def _services_fingerprint(source, lang):
    """The memo's validation token, or None when a leg can't be read (then
    the caller must not use NOR fill the memo). Shape:
    (source, _with_overlay, catalog.merge_with_overlay, pack_l10n.localize,
     overlay_snapshot, l10n_snapshot)."""
    try:
        from app.patient_web import catalog
        overlay = catalog.overlay_state("servicios")
        l10n = pack_l10n.localize_state("servicios", lang) if lang else ("off", None)
    except Exception:  # noqa: BLE001  (same tolerance as _with_overlay)
        return None
    return (source, site_base._with_overlay, catalog.merge_with_overlay,
            pack_l10n.localize, overlay, l10n)


def _services_fingerprint_eq(a, b):
    # Payloads inside the snapshots are compared by IDENTITY (labels by ==):
    # each is the very object its cache holds, so a refill always produces a
    # different one — and `==` on a rows list would deep-compare the whole
    # catalog on every call, the exact cost this memo exists to skip.
    return (a is not None and b is not None
            and a[0] is b[0]              # the loaded source doc object
            and a[1] is b[1]              # _with_overlay (module global)
            and a[2] is b[2]              # catalog.merge_with_overlay
            and a[3] is b[3]              # pack_l10n.localize
            and a[4][0] == b[4][0] and a[4][1] is b[4][1]   # overlay snapshot
            and a[5][0] == b[5][0] and a[5][1] is b[5][1])  # l10n snapshot


def _services_fingerprint_publishable(fp):
    """True only when both cache legs carry information with a DEADLINE:
    overlay 'live'/'db-fail' (both age out or cool down by themselves) and
    l10n 'canonical'/'live'/'off'. The informationless transients — overlay
    'none' (right after catalog.invalidate()) and 'expired', l10n 'absent' —
    must never OWN a memo entry: a hit never re-runs the pipeline, so a value
    published under one of those would sit there with nothing to expire it
    and pin whatever was computed (2026-08-16 review: a value published under
    'none' served the pre-invalidate DB rows indefinitely)."""
    return fp[4][0] in ("live", "db-fail") and fp[5][0] in ("canonical", "live", "off")


def _services(localize=True):
    """The catalog's services. The patient-facing (localized) rows carry
    their `{{sitio.*}}` placeholders resolved — the Guardia's «llamá al
    {{sitio.emergency_number}}» says the installation's own number. After the
    memo, so a Settings change shows on the next request; the canonical rows
    (`localize=False`, what the translation sweep hashes) keep the
    placeholder. Thawed rows are fresh dicts: top-level writes are safe."""
    rows = _services_rows(localize)
    if localize:
        for row in rows:
            for k, v in row.items():
                if isinstance(v, str) and "{{sitio." in v:
                    row[k] = site_identity.expand(v)
    return rows


def _services_rows(localize=True):
    lang = i18n.active() if localize else None
    key = (localize, lang)
    # Publishing discipline (2026-08-16 review): a computed result may only
    # be cached under a fingerprint that was IDENTICAL before and after the
    # pipeline that produced it (pre/post stability check). Equal snapshots
    # mean no input moved while the merge/localize ran — and every payload
    # inside them is a unique-per-refill object, so a leg cannot change away
    # and back (no ABA) without being caught. Anything else — the pack file
    # replaced, an admin write, a DB recovery, a translation refresh landing
    # mid-pipeline — discards the attempt; ONE retry recomputes under
    # whatever moved, and a second move serves that result uncached. A
    # result is never cached under state it did not read, and never under a
    # state nothing would ever refresh.
    for _attempt in (0, 1):
        d = site_base._load("servicios.json") or {}
        fp = _services_fingerprint(d, lang) if d else None
        if fp is not None:
            hit = _SERVICES_MEMO.get(key)
            if hit is not None and _services_fingerprint_eq(hit[0], fp):
                return _thaw_rows(hit[1])
        out = site_base._with_overlay("servicios", d.get("servicios", []))
        normalizados = []
        for s in out:
            if str(s.get("nombre") or "").strip().casefold() in ("clínica médica", "clinica medica"):
                s = dict(s, nombre="Medicina General",
                         palabras=list(s.get("palabras") or []) + ["clínica médica", "médico clínico"])
            normalizados.append(s)
        result = pack_l10n.localize("servicios", normalizados) if localize else normalizados
        if fp is None:
            # unreadable legs / missing file: honest, uncached — but the SAME
            # isolation contract as a hit. `result` still shares its nested
            # lists/dicts with the source/overlay caches, so it must never
            # leave this function unfrozen.
            return _thaw_rows(_freeze_jsonish(result))
        post = _services_fingerprint(site_base._load("servicios.json") or {}, lang)
        if (post is not None and _services_fingerprint_eq(post, fp)
                and _services_fingerprint_publishable(fp)):
            if len(_SERVICES_MEMO) >= 16:  # paranoia: langs come from the bounded
                _SERVICES_MEMO.clear()     # enabled set, never the visitor's whim
            frozen = _freeze_jsonish(result)
            _SERVICES_MEMO[key] = (post, frozen)
            # thawed on the miss path too: this very object now lives in the
            # memo, so the caller must not get to mutate it
            return _thaw_rows(frozen)
        # a leg moved mid-pipeline (or the state is unpublishable) — loop
        # around ONCE and recompute under what moved; a second move falls
        # through and serves the result uncached. That fallthrough result is
        # a raw pipeline output too (nested fields shared with the caches —
        # and, localized, with pack_l10n's own memo), so it gets the same
        # freeze/thaw treatment: no path out of here may let a caller's
        # mutation reach the caches or later memoized reads.
    return _thaw_rows(_freeze_jsonish(result))


# stopwords to dedupe facts against what the page already shows
_FACT_STOP = {"de", "la", "el", "los", "las", "un", "una", "y", "con", "en", "del",
              "al", "a", "se", "su", "sus", "para", "por", "o", "e", "que", "es",
              "son", "the", "of", "lo", "les", "tu", "vos"}


def _fact_tokens(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return [t for t in re.findall(r"[a-z0-9]+", s) if len(t) > 1 and t not in _FACT_STOP]


def _dedupe_facts(datos, corpus, thresh=0.7):
    """Drops a fact if its meaningful words are already said in `corpus`
    (lede + jobs + service names): never repeat a fact on screen."""
    cset = set(_fact_tokens(corpus))
    out = []
    for d in datos:
        toks = set(_fact_tokens(d))  # unique: don't inflate by repeated words
        if not toks:
            continue
        cov = sum(1 for t in toks if t in cset) / len(toks)
        if cov >= thresh:
            continue
        out.append(d)
    return out


def _service_by_id(sid):
    for s in _services():
        if s.get("id") == sid:
            return s
    return None


def _can_change_center(servicio):
    """True only when the catalogue contains a genuinely equivalent centre."""
    edad = servicio.get("edad") or {}
    firma_edad = (edad.get("min"), edad.get("max"))
    for candidato in _services():
        c_edad = candidato.get("edad") or {}
        if candidato.get("id") == servicio.get("id"):
            continue
        if candidato.get("centro") == servicio.get("centro"):
            continue
        if candidato.get("modo") != servicio.get("modo"):
            continue
        if (c_edad.get("min"), c_edad.get("max")) != firma_edad:
            continue
        if site_base._norm_name(candidato.get("nombre")) != site_base._norm_name(servicio.get("nombre")):
            continue
        if (servicio.get("cluster") and candidato.get("cluster")
                and candidato.get("cluster") != servicio.get("cluster")):
            continue
        return True
    return False


def canonical_rows(kind):
    """The rows `pack_l10n.localize()` is handed for `kind`, WITHOUT
    localization — i.e. exactly the text the translation cache must be keyed
    on. The sweeps (scripts/translate_pack.py, translate_warmup.py) go through
    here rather than reading the pack files themselves.

    They used to read the raw JSON, and that produced a silent half-translated
    page: `_doctors()` rewrites "Clínica médica" to "Medicina general" before
    localizing, so the sweep cached a translation of the raw text and the
    render then looked it up by the hash of the rewritten text and missed
    (2026-08-04 — a specialty filter listing both "Cardiólogo" and
    "Cardiology"). One function, one answer, no drift."""
    if kind == "servicios":
        return _services(localize=False)
    if kind == "centros":
        return site_centers._centers(localize=False)
    if kind == "medicos":
        return _doctors(localize=False)
    if kind == "clusters":
        return data_packs.compose("clusters.json")
    if kind == "preparaciones":
        return _preparations(localize=False)
    return []


def _clusters():
    """The directory's cluster catalog — a COMPOSABLE file (data_packs.py's
    compose registry, phase 3 2026-07-30): merged across the active pack
    chain by `id`, not a whole-file override — see data_packs.py's module
    docstring."""
    return pack_l10n.localize("clusters", data_packs.compose("clusters.json"))


def _doctors(localize=True):
    """The public site's doctors: the pack with the admin overlay, never a test
    doctor (catalog.is_test_doctor) — every list, count, search, page and slot
    pool of the patient site reads through here."""
    from app.patient_web import catalog
    d = site_base._load("medicos.json") or {}
    out = site_base._with_overlay("medicos", d.get("medicos", []))
    rows = [
        dict(m, especialidad="Medicina general")
        if str(m.get("especialidad") or "").strip().casefold() in ("clínica médica", "clinica medica")
        else m
        for m in out
        if not catalog.is_test_doctor(m)
    ]
    return pack_l10n.localize("medicos", rows) if localize else rows


def _professionals():
    """Doctors who are PEOPLE — what the directory, center/cluster pages and
    search show. The official spreadsheet carries role-rows ("Enfermería",
    "Técnico/a", "Administrativo.") flagged `rol:true` in medicos.json: their
    time windows still feed hours and appointments (_week_hours,
    _appointments_for_service), but they aren't a professional to list."""
    return [m for m in _doctors() if not m.get("rol")]


def home_sections():
    """[(id, name)] of the `/d/<id>` pages the home's browse block links to,
    straight from the pack's cluster catalog: a cluster with at least one
    service or professional (the rule `_page_cluster` uses to serve it, so no
    link is a 404), plus `grupos`, whose page lists workshops of any cluster.
    Empty when the catalog cannot be read: the block then drops the column."""
    try:
        svs = _services()
        con_contenido = {s.get("cluster") for s in svs}
        con_contenido |= {cid for m in _professionals() for cid in _doctor_clusters(m)}
        con_contenido |= {"grupos" for s in svs if s.get("categoria") == "grupos"}
        hay_walkin = bool(_walkin_services())
        visibles = [c for c in _clusters()
                    if c.get("id") in con_contenido
                    or (c.get("kind") == _WALKIN_KIND and hay_walkin)]
        # The catch-all («Otras especialidades») closes the list here too,
        # even when a pack appends its own clusters after it.
        visibles.sort(key=lambda c: bool(c.get("catch_all")))
        return [(c["id"], c.get("nombre") or c["id"]) for c in visibles]
    except Exception:  # noqa: BLE001 — the home never fails for a link block
        return []


def _cluster_by_id(cid):
    for c in _clusters():
        if c.get("id") == cid:
            return c
    return None


# A cluster with `"kind": "walkin"` (clusters.json) is not a specialty: its
# page gathers every walk-in consultorio of the installation — no order, not
# booked on the web, first come first served — center by center, after the
# informational links it names in `"links"` (Pinamar's «Demanda espontánea»:
# Telemedicina first, press office 2026-10-22). Groups and workshops are
# walk-in too but have their own page (/d/grupos), so they stay out.
_WALKIN_KIND = "walkin"
_CSS_URL_RE = re.compile(r"^/[A-Za-z0-9/_.%=&?-]+$")


def _walkin_services():
    """Walk-in consultorios ordered by center (the pack's center order, the
    main hospital first) and then by name — «ordenados por efectores»."""
    orden = {c.get("slug"): i for i, c in enumerate(site_centers._centers()) if c.get("slug")}
    svs = [s for s in _services()
           if s.get("modo") == "walkin"
           and s.get("categoria") != "grupos" and s.get("cluster") != "grupos"]
    return sorted(svs, key=lambda s: (orden.get(s.get("centro"), len(orden)),
                                      str(s.get("nombre") or "").lower()))


def _walkin_description(cl, fallback_key, fallback):
    """The one sentence that says what the walk-in cluster is for — the pack's
    own `descripcion` on the cluster (translatable, so another installation
    summarizes ITS services), else a generic line. The directory card and the
    cluster page read this same text."""
    own = str((cl or {}).get("descripcion") or "").strip()
    return own or i18n.t_or(fallback_key, fallback, i18n.active())


_NO_APPOINTMENT_MODES = ("walkin", "walkin_con_orden")


def requires_referral(modo):
    """THE referral rule of the public site (hr-patient review #5, 15.11): a
    service needs a medical order exactly when its mode ends in `_con_orden`.
    The listings, the doctors' «Orden» cell and the study pages all ask here,
    so a center, its doctors and its service page cannot disagree."""
    return str(modo or "").endswith("_con_orden")


def _preparations(localize=True):
    """The base JSON with the admin's overlay on top (same pattern as
    _centers()/_services()). With no database it returns the JSON alone:
    the service page never stops saying how to prepare because of a
    connection problem.

    `localize=False` is for the sweeps (`canonical_rows("preparaciones")`):
    they need the UNTRANSLATED rows, the same text the cache is keyed on —
    same reason `_services(localize=False)` exists (2026-08-04: this surface
    had no `pack_l10n.localize()` call at all, so "Cómo prepararte" stayed in
    Spanish on an English page no matter how many languages were enabled)."""
    base = site_base._load("estudios-preparacion.json") or {}
    base = base.get("estudios", []) if isinstance(base, dict) else []
    rows = site_base._with_overlay("preparaciones", base)
    return site_identity.expand(pack_l10n.localize("preparaciones", rows)) if localize else rows
_SEX_F = ("obstetric", "gineco", "mama", "mamografia", "papanicolaou", "colposcop",
           "ive", "ile", "embarazo", "prenatal")
_SEX_M = ("prostat",)


def _study_sex(nombre):
    """Study's sex restriction: 'F', 'M', or None (for everyone)."""
    t = site_base._norm_name(nombre)
    if any(k in t for k in _SEX_F):
        return "F"
    if any(k in t for k in _SEX_M):
        return "M"
    return None


def _study_audience(group):
    """'ninos' | 'adultos' | 'todos' for a group of services of the same study."""
    t = " ".join(site_base._norm_name(s.get("nombre")) for s in group)
    if any(k in t for k in ("infantil", "pediatr", "neonat", "nino", "del nino")):
        return "ninos"
    # reproductive / sex-restricted studies are inherently adult-only
    if any(k in t for k in _SEX_F) or any(k in t for k in _SEX_M):
        return "adultos"
    edades = [s.get("edad") or {} for s in group]
    maxs = [e.get("max") for e in edades if e.get("max") is not None]
    mins = [e.get("min") for e in edades if e.get("min") is not None]
    if maxs and max(maxs) <= 15:
        return "ninos"
    if mins and min(mins) >= 18:
        return "adultos"
    return "todos"
