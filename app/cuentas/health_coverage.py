"""health_coverage.py — which of a person's coverages is THE one that counts.

A person can appear in more than one registry (IOMA + a deregulated private
plan, PAMI + a union health insurer) and can also declare others by hand.
When there's more than one, we have to pick one: it's the one told to the
patient, the one on the appointment, and the one the hospital later bills
against (see the cost-recovery report).

The product owner (2026-07-24): *"if I have two obras sociales and Swiss Medical, obviously
Swiss Medical is my default one… educate yourself about which ones are better
and choose by default the best one. But people could set the default to
another one as well."*

So there are two separate things, in this order:

  1. CHOSEN `principal` — what the person (or whoever's attending them)
     marked. If it's set and points at something that exists, it always wins.
     No heuristic overrides an explicit decision.
  2. If nothing was chosen, the best one is proposed using the ranking below.

The ranking, and where it comes from
-------------------------------------
- **A private plan beats a union insurer.** That's the case the product owner raised, and
  it's also the structural difference: a private plan is contracted for its
  own provider network and gives direct access to clinics; a union insurer
  operates through its own network. (argentina.gob.ar, "Tengo obra social o
  prepaga")
- **Among private plans**, the order comes from measured member satisfaction
  in 2026: Swiss Medical 3.85 · OSDE 3.73 · Federada Salud 3.70 · Medicus 3.6x
  (2026 ranking from miobrasocial.com.ar, republished by diagnosticsnews.com).
  The differences are small: this just orders a default, it doesn't judge
  anyone.
- **PAMI and IOMA come last** among union insurers. It's not a quality
  judgment: they're the state and provincial circuits, and when someone ALSO
  has another coverage, that other one is the one worth declaring. Someone
  who only has PAMI or IOMA still sees it as their principal one — it's the
  only one there is.

None of this is shown to the patient as a ranking, nor written into the UI:
it's only how a value gets preselected, which can then be changed.
"""

import hashlib
import json


def revision(coverage):
    """Opaque concurrency revision, not an authentication credential."""
    raw = json.dumps(coverage, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# Normalized (no accents, lowercase) -> score. Anything not in the table
# gets `_UNION_DEFAULT`: an unknown union insurer isn't penalized.
_PRIVATE_PLANS = {
    "swiss medical": 98,
    "osde": 96,
    "federada salud": 94,
    "federada": 94,
    "medicus": 92,
    "omint": 90,
    "hospital italiano": 90,
    "galeno": 88,
    "medife": 88,
    "sancor salud": 86,
    "avalian": 84,
    "aca salud": 84,
    "premedic": 82,
    "prevencion salud": 82,
}

_UNION_DEFAULT = 50       # national/union insurer with no particular data
_UNION_INSURERS = {
    "ioma": 30,           # provincial
    "pami": 25,           # INSSJP, state-run
    "inssjp": 25,
    "instituto nacional de servicios sociales para jubilados y pensionados": 25,
}


def _normalize(s):
    """Lowercase, no accents and NO punctuation. That last part matters: the
    national registry writes "O.S.P. BUENOS AIRES (I.O.M.A.)", and searching
    for "ioma" in there didn't match with the dots in the middle."""
    import re
    from app.core import text_fold
    t = text_fold.strip_nonspacing(str(s or "").strip().lower())
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", t)).strip()


def score(nombre, es_prepaga=False):
    """How much that coverage weighs when choosing the principal one. Higher = better."""
    n = _normalize(nombre)
    if not n:
        return 0
    if es_prepaga:
        # a private plan always outranks any union insurer
        return _PRIVATE_PLANS.get(n, 80)
    # OSDE/Swiss also show up in the registry as a union insurer: if the name
    # is one of those, it's still recognized (without the private-plan floor).
    if n in _PRIVATE_PLANS:
        return _PRIVATE_PLANS[n] - 20
    # Also without spaces: "I.O.M.A." becomes "i o m a" once the dots are
    # stripped, and "ioma" only shows up there once the letters are joined.
    compacto = n.replace(" ", "")
    for clave, val in _UNION_INSURERS.items():
        if clave in n or clave.replace(" ", "") in compacto:
            return val
    return _UNION_DEFAULT


def _union_plans(cob):
    o = (cob or {}).get("obras_sociales")
    return [x for x in o if isinstance(x, dict) and x.get("nombre")] if isinstance(o, list) else []


def _private_plan(cob):
    p = (cob or {}).get("prepaga")
    return p if isinstance(p, dict) and p.get("nombre") else None


def options(cob):
    """The eligible coverages, with their stable key. A private plan's key is
    the literal 'prepaga' (there's only one); a union insurer's is its name."""
    out = []
    p = _private_plan(cob)
    if p:
        out.append({"clave": "prepaga", "tipo": "prepaga", "nombre": p["nombre"],
                    "puntaje": score(p["nombre"], True)})
    for o in _union_plans(cob):
        out.append({"clave": o["nombre"], "tipo": "obra_social", "nombre": o["nombre"],
                    "origen": o.get("origen"), "puntaje": score(o["nombre"])})
    return out


def choose(cob):
    """The principal coverage's key: the marked one if valid, else the best
    one. None if the person has none loaded."""
    ops = options(cob)
    if not ops:
        return None
    marcada = (cob or {}).get("principal")
    if marcada:
        for o in ops:
            if o["clave"] == marcada:
                return marcada
    # tie: the first one wins, in load order
    return max(ops, key=lambda o: o["puntaje"])["clave"]


def primary(cob):
    """The full principal coverage (to show or to bill), or None."""
    clave = choose(cob)
    if clave is None:
        return None
    if clave == "prepaga":
        p = _private_plan(cob) or {}
        return {"clave": clave, "tipo": "prepaga", "nombre": p.get("nombre"),
                "afiliado": p.get("afiliado"), "plan": p.get("plan")}
    for o in _union_plans(cob):
        if o["nombre"] == clave:
            return {"clave": clave, "tipo": "obra_social", "nombre": o["nombre"],
                    "afiliado": o.get("afiliado"), "origen": o.get("origen")}
    return None


def normalize(cob):
    """Leaves `principal` pointing at something that exists. Called on save,
    so what's persisted doesn't depend on the ranking never changing again."""
    if not isinstance(cob, dict) or not cob:
        return cob
    clave = choose(cob)
    if clave is None:
        cob.pop("principal", None)
    else:
        cob["principal"] = clave
    return cob
