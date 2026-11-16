#!/usr/bin/env python3
"""scripts/search_examples.py — picks the home page's example search terms
("Probá: Cardiología, Ecografía…") FROM the active data package, keeping only
the ones that actually return results.

Why a tool and not a hand-written list: the six chips under the searchbox are
a teaching device — each one demonstrates a different thing the search
understands (a specialty, a study, a keyword nobody names in the catalog, a
place, a doctor's surname). A chip that returns "we found nothing" teaches the
opposite. The list was hardcoded to Pinamar's own data ("Ostende", "Garrido"),
so the public demo — a different town, a different catalog — showed two chips
that dead-ended. Any new installation would have inherited the same problem.

What it does:

  1. Builds the SAME two corpora the home page searches (patient_web/site_*.py's
     /api/services and /api/doctors payloads) for the active pack chain.
  2. Proposes candidates in five flavors, ranked inside each flavor by how
     much of the catalog they cover.
  3. Asks the REAL matcher how many results each one gets — static/platform/search.mjs, via
     scripts/search_probe.mjs. Not a reimplementation (CLAUDE.md: one engine).
  4. Keeps the best verified candidate per flavor and writes the list to the
     pack's sitio.json as `search_examples`.

`site_identity.get("search_examples_html")` turns that list into the chips,
and tests/search-examples.test.mjs fails the build if one of them ever stops
returning results (a service renamed, a doctor retired).

Per-language chips (2026-08-04): a visitor reading the site in English must
search, and get results, in English — chips in Spanish under English chrome
teach nothing and half of them dead-end (an English query against a Spanish
catalog). So this script repeats the WHOLE pipeline above once per enabled
language (`i18n.enabled_languages()`), not just once for the pack's own
language:

  - The pack's own language writes `search_examples`, unchanged.
  - Every OTHER enabled language binds that language (`i18n.bind_lang`) before
    building the two corpora, so `site_listing._services_enriched()` /
    `_doctors_enriched()` come back with whatever pack_l10n.py has translated
    and cached for it — then candidates are proposed and probed against THAT
    corpus, in THAT language, exactly like the canonical run. A term that
    finds nothing in its own language is dropped, exactly as today; the
    result (however short) is written to `search_examples_<lang>`.
  - A language whose translation cache is still cold — `_translated_ratio()`
    finds that not even one service name differs from the pack's own
    language — is SKIPPED with a warning instead of silently writing Spanish
    words into the English slot (see `python3 scripts/translate_pack.py
    --lang <lang>`, which has to run first).

Usage:
    python3 scripts/search_examples.py                     # active pack chain
    python3 scripts/search_examples.py --pack example-town # one pack
    python3 scripts/search_examples.py --pack example-town --dry-run
"""
import argparse
import collections
import json
import os
import subprocess
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PROBE = os.path.join(ROOT, "scripts", "search_probe.mjs")

# One chip per flavor, in the order they render. The point of the row is the
# VARIETY (each chip teaches a different affordance), so the picker never
# fills two slots from the same flavor even when one flavor has better
# candidates than another has any.
FLAVORS = ("specialty", "specialty2", "study", "keyword", "place", "surname")

# Words that make a bad chip: too generic to teach anything, or they'd read as
# instructions rather than as an example query.
_STOP = {
    "de", "del", "la", "el", "los", "las", "y", "e", "o", "u", "a", "al", "en",
    "con", "sin", "por", "para", "un", "una", "que", "se", "su", "sus",
    "turno", "turnos", "consulta", "consultorio", "atencion", "atención",
    "centro", "centros", "salud", "hospital", "caps", "medico", "médico",
    "medica", "médica", "medicos", "médicos", "general", "adultos", "niños",
    "control", "controles", "estudio", "estudios", "servicio", "servicios",
    "primera", "vez", "demanda", "espontanea", "espontánea", "dr", "dra",
    "medicina", "clinica", "clínica", "clinico", "clínico",
}

# A chip that matches almost the whole catalog teaches nothing about WHERE —
# the town's own name (e.g. "Sampleton") would return every service in town.
# Place chips have to narrow the list down; the other flavors are free to be broad.
_PLACE_MAX_SHARE = 0.5


def _norm(s):
    return "".join(
        c for c in unicodedata.normalize("NFD", str(s or "").lower())
        if unicodedata.category(c) != "Mn"
    ).strip()


def _untitled(label, pool):
    """The pool entry a chosen label came from (labels are capitalized)."""
    for term in pool:
        if _titled(term) == label:
            return term
    return label


def _titled(word):
    """Chip label: a single word gets a capital, a real name keeps its own."""
    return word[:1].upper() + word[1:] if word else word


# ------------------------------------------------------------------ corpora
def _corpora(overlay=False):
    """(services, doctors, centers_by_slug) exactly as the home page gets
    them — the enriched API payloads, not the raw pack JSON, because the
    search reads enriched fields (`horario`, the center's name).

    The admin's catalog overlay (catalog.py, a Postgres table on top of the
    pack) is OFF by default, and that is the whole point: what this tool
    writes goes back into the PACK, so it has to be true of the pack alone.
    With the overlay on, a developer's local database — which is Pinamar's,
    not the demo's — leaked its own doctors into the demo's candidate pool
    and produced a chip ("Gómez") that returns nothing on the real demo."""
    from app.patient_web import site_base, site_centers, site_listing
    if not overlay:
        site_base._with_overlay = lambda _tipo, base: base
    services = site_listing._services_enriched()
    doctors = site_listing._doctors_enriched()
    centers = {c["slug"]: c for c in site_centers._centers_web()}
    return services, doctors, centers


# --------------------------------------------------------------- candidates
def _specialty_names():
    """[(name, cluster)] from the pack's canonical specialty list
    (especialidades.json), which names the DISCIPLINE ("Cardiología") — what a
    patient types — rather than the practitioner ("Cardiólogo"), which is what
    the doctor records carry."""
    from app.patient_web import site_base
    data = site_base._load("especialidades.json") or {}
    return [(str(x.get("nombre") or ""), str(x.get("cluster") or ""))
            for x in (data.get("specialties") or []) if x.get("nombre")]


def _specialty_candidates(services, doctors, use_named=True):
    """Specialty names, one word each. The single most common query shape: a
    patient knows what kind of doctor they need. Ordered later by how many
    results each actually returns.

    `use_named=False` skips `especialidades.json` on purpose — that file has
    no per-language variant (it isn't part of `pack_l10n.FIELDS`), so a
    request for a non-canonical language would otherwise hand back the same
    Spanish word regardless of which corpus is bound, and it would "verify"
    for the wrong reason (a Spanish specialty word can still turn up a hit
    against an untranslated field elsewhere on the row). Falling back to the
    service names — which DO have a translated variant, via `pack_l10n.py`,
    once cached — means the specialty word itself comes from the same
    language the rest of that language's chips are probed in."""
    named = (_specialty_names() if use_named else None) or \
        [(s.get("nombre") or "", s.get("cluster") or "") for s in services]
    out, cluster_of = [], {}
    for name, cluster in named:
        # imaging and lab belong to the "study" chip; letting them also fill a
        # specialty slot spends two of six chips on the same idea
        if cluster in ("diagnostico", "laboratorio"):
            continue
        # one word reads best in a chip row; "Ginecología y Obstetricia"
        # would take the width of three chips
        word = name.split()[0].strip(",")
        if _norm(word) in _STOP or len(word) < 5 or word in out:
            continue
        out.append(word)
        cluster_of[word] = cluster
    return out, cluster_of


def _study_candidates(services):
    """Diagnostic studies — the second query shape: the patient is holding a
    referral and types what's written on it. A word shared by several studies
    ("Ecografía" — general, abdominal, obstétrica) beats a one-off."""
    counts = collections.Counter()
    for s in services:
        if s.get("cluster") not in ("diagnostico", "laboratorio"):
            continue
        for word in (s.get("nombre") or "").split():
            word = word.strip(",()")
            if _norm(word) in _STOP or len(word) < 6:
                continue
            counts[word] += 1
    return [w for w, _n in counts.most_common()]


def _keyword_candidates(services):
    """Synonyms from `palabras` that no service NAME contains — the chip that
    proves the search knows words the catalog never uses (a patient who types
    "libreta sanitaria" doesn't know which service issues it).

    Matched on a 6-letter stem, not on equality: "vacunatorio" and
    "vacuna" both belong to the service called "Vacunación", so neither is
    demonstrating anything the specialty chip doesn't already."""
    stems = {_norm(w)[:6] for s in services
             for w in (s.get("nombre") or "").split() if len(w) >= 6}
    counts = collections.Counter()
    for s in services:
        for palabra in (s.get("palabras") or []):
            # whole entries only, and only the one-word ones: chopping
            # "salud mental" into words yields "Mental", which is a fragment,
            # not something anybody types into a search box.
            word = str(palabra).strip(",()")
            n = _norm(word)
            if len(word) < 6 or " " in word or n in _STOP or n[:6] in stems:
                continue
            counts[word] += 1
    return [w for w, _n in counts.most_common()]


def _place_candidates(services, centers):
    """A place: the locality or the center's distinguishing word. Teaches
    that you can search by WHERE instead of by what."""
    counts = collections.Counter()
    for slug, c in centers.items():
        for word in (c.get("name") or "").split():
            word = word.strip(",")
            if _norm(word) in _STOP or len(word) < 5:
                continue
            counts[word] += sum(1 for s in services if s.get("centro") == slug)
    for s in services:
        sede = (s.get("sede") or "").strip()
        for word in sede.split():
            word = word.strip(",")
            if _norm(word) in _STOP or len(word) < 5:
                continue
            counts[word] += 1
    from app.patient_web import site_base
    localities = ((site_base._load("example-localities.json") or {})
                  .get("localities") or [])
    ordered = [w for w, _n in counts.most_common()]
    # a real locality name beats a word lifted out of a center's name
    for loc in localities:
        word = loc.split()[-1]
        if len(word) >= 5 and _norm(word) not in _STOP and word in ordered:
            ordered.remove(word)
            ordered.insert(0, word)
    return ordered


def _surname_candidates(doctors):
    """A doctor's surname — the chip that says "you can look up a person"."""
    counts = collections.Counter()
    for d in doctors:
        parts = (d.get("nombre") or "").split()
        if len(parts) < 2:
            continue
        surname = parts[-1].strip(",")
        if len(surname) < 5 or _norm(surname) in _STOP:
            continue
        counts[surname] += 1
    # a surname shared by two doctors shows more than a unique one, but a
    # very common one stops looking like a name
    return [w for w, _n in counts.most_common()]


# -------------------------------------------------------------------- probe
def _probe(terms, services, doctors, centers, with_keys=False):
    """{term: result-count} straight from static/platform/search.mjs, or
    {term: (result-count, frozenset of matched rows)} with `with_keys`."""
    if not terms:
        return {}
    payload = json.dumps({
        "services": services, "doctors": doctors, "centers": centers,
        "terms": terms,
    }, ensure_ascii=False)
    proc = subprocess.run(["node", PROBE], input=payload.encode("utf-8"),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=False)
    if proc.returncode != 0:
        raise RuntimeError("search_probe.mjs failed: "
                           + proc.stderr.decode("utf-8", "replace"))
    out = json.loads(proc.stdout)
    if with_keys:
        return {k: (v["total"], frozenset(v.get("keys") or ())) for k, v in out.items()}
    return {k: v["total"] for k, v in out.items()}


# Flavors whose chips can be synonyms of each other: a keyword like «Dentista»
# finds the same rows as the specialty «Odontología», «Child» the same as
# «Pediatric». A place or a surname naturally falls inside a specialty's
# results and still teaches something else, so they are not compared.
_SYNONYM_FLAVORS = ("specialty", "specialty2", "study", "keyword")
# Share of a candidate's results already found by the chips chosen before it
# above which it is the same search in other words. «Dentista» finds two
# dentistry services and the children's dentist: all three are already under
# «Odontología» or «Pediatría».
_SYNONYM_SHARE = 0.8


def _is_synonym(rows, chosen_rows):
    """True when `rows` mostly repeats the results of the chips already chosen."""
    if not rows:
        return False
    seen = frozenset().union(*chosen_rows) if chosen_rows else frozenset()
    return len(rows & seen) >= _SYNONYM_SHARE * len(rows)


def pick(services, doctors, centers, verbose=False,
         use_named_specialty=True, keyword_ok=True):
    """The verified chip list for this catalog, one per flavor, in order.

    `use_named_specialty` / `keyword_ok` only matter when generating a
    NON-canonical language (see `pick_all_languages()`): `especialidades.json`
    (specialty names) and `palabras` (keyword synonyms) have no per-language
    variant today, so a caller building the English list passes
    `use_named_specialty=False` (fall back to the — translatable — service
    names instead) and `keyword_ok=False` when `palabras` hasn't actually
    been translated (dropping the flavor rather than showing a lone Spanish
    word inside an otherwise-English chip row)."""
    specialties, cluster_of = _specialty_candidates(
        services, doctors, use_named=use_named_specialty)
    pools = {
        "specialty": specialties,
        "study": _study_candidates(services),
        "keyword": _keyword_candidates(services) if keyword_ok else [],
        "place": _place_candidates(services, centers),
        "surname": _surname_candidates(doctors),
    }

    # probe every candidate of every pool in ONE node round trip
    everything = sorted({t for pool in pools.values() for t in pool[:40]})
    probed = _probe(everything, services, doctors, centers, with_keys=True)
    counts = {t: n for t, (n, _rows) in probed.items()}
    rows_of = {t: rows for t, (_n, rows) in probed.items()}

    # Specialties are the one pool with no natural order of its own
    # (especialidades.json is alphabetical-ish by cluster), so the catalog
    # decides: the specialty this town offers most of goes first.
    pools["specialty"].sort(key=lambda t: -counts.get(t, 0))
    # the second specialty comes from a DIFFERENT cluster than the first, so
    # the row doesn't spend two of its six chips on the same corner of the
    # catalog (two flavors of diagnostic imaging, say)
    pools["specialty2"] = pools["specialty"]

    cap = max(1, int(len(services) * _PLACE_MAX_SHARE))
    chosen, chosen_rows = [], []
    for flavor in FLAVORS:
        for term in pools.get(flavor, []):
            label, hits = _titled(term), counts.get(term, 0)
            if label in chosen or hits < 1:
                continue
            if flavor == "place" and hits > cap:
                continue
            if flavor == "specialty2" and chosen and \
                    cluster_of.get(term) == cluster_of.get(_untitled(chosen[0], specialties)):
                continue
            synonym_flavor = flavor in _SYNONYM_FLAVORS
            if synonym_flavor and _is_synonym(rows_of.get(term, frozenset()), chosen_rows):
                continue
            chosen.append(label)
            if synonym_flavor:
                chosen_rows.append(rows_of.get(term, frozenset()))
            if verbose:
                print(f"  {flavor:<10} {label:<20} {hits} results")
            break
    return chosen


# ------------------------------------------------------------- per-language
def _target_languages():
    """(pack_lang, [other_enabled_lang, ...]) — the pack's own language
    (unaffected, always generated) plus every OTHER admin-enabled language
    (`LANGUAGES` config), in the order `i18n.enabled_languages()` returns
    them. On an installation that hasn't enabled a second language this is
    just `(pack_lang, [])` — same single-list output as before this slice."""
    from app.core import i18n
    from app.core import translate
    pack_lang = translate._primary(translate.pack_language())
    seen = {pack_lang}
    others = []
    for lang in i18n.enabled_languages():
        lang = translate._primary(lang)
        if lang not in seen:
            seen.add(lang)
            others.append(lang)
    return pack_lang, others


def _translated_ratio(canonical_services, localized_services, field="nombre"):
    """Share of `field` that actually changed between the pack's own corpus
    and the one built with a language bound.

    Two different gates read this, on two different fields:

    - `field="nombre"` — the guard against a cold translation cache.
      `pack_l10n.localize()` degrades SILENTLY to the pack's own language on
      a cache miss (by design: a render must never wait on a model call),
      which is exactly right for a visitor and exactly wrong for this
      script — probing an untranslated corpus would "verify" chips that are
      really Spanish words about to render into an English slot. 0% here
      means don't generate this language at all this run.
    - `field="palabras"` — `palabras` (the keyword-flavor chip's source) is
      deliberately EXCLUDED from `pack_l10n.FIELDS` (search vocabulary stays
      in the pack's own language, i18n.md) and has no translated variant
      yet. 0% here means the keyword flavor specifically should be dropped
      for this language (fewer chips), not that the whole language is
      cold — `nombre` can be fully translated while `palabras` never is."""
    total = changed = 0
    for a, b in zip(canonical_services, localized_services):
        va = a.get(field)
        if not va:
            continue
        total += 1
        if b.get(field) != va:
            changed += 1
    return (changed / total) if total else 0.0


def pick_all_languages(overlay=False, verbose=False):
    """{lang: chosen_list} — the pack's own language plus every other
    enabled one that isn't cold. Each language gets its OWN corpus (built
    with that language bound, so pack_l10n.py's cached translations apply)
    and its OWN probe run: an English chip is never a translation of a
    Spanish one, it is generated from and verified against the English
    corpus directly, the same way the Spanish list always has been."""
    from app.core import i18n

    pack_lang, other_langs = _target_languages()

    services, doctors, centers = _corpora(overlay=overlay)
    if verbose:
        print(f"{len(services)} services, {len(doctors)} doctors, "
              f"{len(centers)} centers — pack language '{pack_lang}'")
    out = {pack_lang: pick(services, doctors, centers, verbose=verbose)}

    for lang in other_langs:
        if verbose:
            print(f"\n-- {lang} --")
        _, token = i18n.bind_lang(lang)
        try:
            l_services, l_doctors, l_centers = _corpora(overlay=overlay)
        finally:
            i18n.unbind(token)
        ratio = _translated_ratio(services, l_services, field="nombre")
        if ratio <= 0:
            print(f"  {lang}: translation cache looks cold (0 of "
                  f"{len(services)} service names differ from '{pack_lang}') "
                  f"— run `python3 scripts/translate_pack.py --lang {lang}` "
                  f"first. Not writing {lang} chips this run.")
            continue
        keyword_ratio = _translated_ratio(services, l_services, field="palabras")
        keyword_ok = keyword_ratio > 0
        if verbose and not keyword_ok:
            print(f"  {lang}: 'palabras' not yet translated (0 of "
                  f"{len(services)} differ) — dropping the keyword chip for "
                  f"{lang} rather than showing a Spanish word; re-run once "
                  f"the palabras index is translated and warmed.")
        chosen = pick(l_services, l_doctors, l_centers, verbose=verbose,
                      use_named_specialty=False, keyword_ok=keyword_ok)
        if verbose:
            pct = int(ratio * 100)
            print(f"  {lang}: {len(chosen)}/{len(FLAVORS)} chips verified "
                  f"({pct}% of service names came back translated)")
        out[lang] = chosen
    return out, pack_lang


# --------------------------------------------------------------------- main
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", help="Pack id to write into (e.g. example-town). "
                                       "Also sets PACK_CHAIN to global,<pack> "
                                       "unless PACK_CHAIN is already set.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the picked terms without writing sitio.json.")
    parser.add_argument("--with-overlay", action="store_true",
                        help="Include the admin's catalog overlay from the "
                             "database. Off by default — see _corpora().")
    args = parser.parse_args()

    if args.pack and not os.environ.get("PACK_CHAIN"):
        os.environ["PACK_CHAIN"] = f"global,{args.pack}"
    os.environ.setdefault("TURNOS_FAKE_AI", "1")

    print(f"chain {os.environ.get('PACK_CHAIN', '(default)')}")
    results, pack_lang = pick_all_languages(overlay=args.with_overlay, verbose=True)
    print()
    for lang, chosen in results.items():
        field = "search_examples" if lang == pack_lang else f"search_examples_{lang}"
        print(f"{field}: {', '.join(chosen) if chosen else '(none)'}")

    if args.dry_run:
        return
    if not args.pack:
        print("\nNo --pack given: not writing. Re-run with --pack <id> to save.")
        return

    path = os.path.join(ROOT, "packs", args.pack, "sitio.json")
    with open(path, encoding="utf-8") as fh:
        sitio = json.load(fh)
    changed = False
    for lang, chosen in results.items():
        field = "search_examples" if lang == pack_lang else f"search_examples_{lang}"
        if lang != pack_lang and not chosen:
            # Nothing verified for this language this run (every candidate
            # came up empty, even though the cache wasn't cold enough to be
            # skipped a level up) — leave the key ABSENT rather than write
            # `[]`. `site_identity._search_examples()` already treats a
            # missing/empty list as "fall back to canonical," so this is the
            # honest outcome, never a stale or empty list under this
            # language's key.
            if field in sitio:
                del sitio[field]
                changed = True
            continue
        if sitio.get(field) != chosen:
            sitio[field] = chosen
            changed = True
    if not changed:
        print(f"\n{path}: already up to date.")
        return
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(sitio, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"{path}: written.")


if __name__ == "__main__":
    main()
