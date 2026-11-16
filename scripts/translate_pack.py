#!/usr/bin/env python3
"""translate_pack.py — fills translate.py's cache with the whole data
package, so a visitor reading the site in another language sees translated
CONTENT (service names, cluster titles, specialties, center descriptions),
not just translated chrome.

Why a sweep and not on-demand: a page renders hundreds of pack strings, and
translating them inside the request would mean hundreds of sequential
OpenRouter round trips before the first byte. So the render only ever reads
the cache (`translate.cached_text`, via pack_l10n.py) and this script is what
puts things in it. Anything it hasn't reached yet shows in the pack's own
language — a partial sweep degrades string by string, never as a failure.

Run it after changing pack content, and after enabling a new language. Safe
to re-run at any time: an entry whose source text hasn't changed since the
last sweep costs zero model calls.

Usage:
    python3 scripts/translate_pack.py --lang en
    python3 scripts/translate_pack.py --lang en --dry-run   # count only
    python3 scripts/translate_pack.py --lang en --only servicios,centros

Runs against DATABASE_URL from the environment/.env — same convention as
every other `scripts/*.py` in this repo (NOT a test DB).

Note (2026-08-04): this used to also sweep `catalog.preparations()`'s
`resumen` field ("how do I prepare for this study" summaries, the phase 3a
proof surface `translate.localized_preparation_summary()` exists for).
Dropped — the field doesn't exist at all in the demo pack
(`packs/example-town/estudios-preparacion.json`, 0 of 30 entries) and the
proof surface is called from no live page, so that branch was silently
contributing zero strings on the installation that matters while its
printed line implied coverage. See docs/architecture/i18n.md, "Filling the
cache", for the full reasoning; `localized_preparation_summary()` itself is
untouched and still proven by test_translate.py against a synthetic item.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import pack_l10n  # noqa: E402
from app.patient_web import site_catalog, site_news
from app.core import site_identity  # noqa: E402
from app.core import translate  # noqa: E402


def _rows(kind):
    """The canonical rows for one kind — `site_catalog.canonical_rows()` is
    the single definition of what the render will look up, so the sweep can
    never cache a translation under text the render doesn't have."""
    return site_catalog.canonical_rows(kind)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lang", required=True,
                        help="Target language code (e.g. en).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only report how many entries would be "
                             "translated, without calling the model.")
    parser.add_argument("--only",
                        help="Comma-separated subset of kinds to sweep "
                             "(servicios, centros, medicos, clusters, sitio, "
                             "news).")
    args = parser.parse_args()

    lang = args.lang.strip().lower()
    pack_lang = translate.pack_language()
    # /news has its OWN fixed source language (site_news.NEWS_SOURCE_LANG,
    # always "es" — it's literal Spanish in the code, not pack content), so
    # this early exit only applies when there's truly nothing to do for
    # EITHER surface — today the two always coincide, but a future
    # non-Spanish pack must not silently skip the news sweep.
    if lang == pack_lang.split("-", 1)[0] == site_news.NEWS_SOURCE_LANG:
        print(f"'{lang}' is already this pack's own language ({pack_lang}) "
              f"— nothing to do.")
        return

    kinds = list(pack_l10n.FIELDS) + ["especialidades", "sitio", "news"]
    if args.only:
        wanted = {k.strip() for k in args.only.split(",") if k.strip()}
        unknown = wanted - set(kinds)
        if unknown:
            parser.error(f"unknown kind(s): {', '.join(sorted(unknown))}")
        kinds = [k for k in kinds if k in wanted]

    print(f"pack language '{pack_lang}', target '{lang}'")
    before = translate.call_count()
    total = pending = 0

    for kind in kinds:
        if kind == "sitio":
            entries = site_identity.sitio_entries()
        elif kind == "news":
            entries = site_news.news_translation_entries()
        elif kind == "especialidades":
            # Same cache-only keys the chat reads (chat_agent
            # ._specialty_names, chat_tools._etiquetas_de_especialidad) —
            # ops/translate_warmup.py sweeps these too; this is the by-hand
            # twin (residual leak 1 of the 2026-09-11 simulation).
            from app.patient_web import catalog
            entries = [(pack_l10n.key("especialidades",
                                      str((item or {}).get("nombre") or "").strip(),
                                      "nombre"),
                        str((item or {}).get("nombre") or "").strip())
                       for item in catalog.specialties() or []
                       if str((item or {}).get("nombre") or "").strip()]
        else:
            entries = pack_l10n.entries(kind, _rows(kind))
        # /news entries are literal Spanish in the code, independent of the
        # active pack's own language field — always NEWS_SOURCE_LANG.
        kind_lang = site_news.NEWS_SOURCE_LANG if kind == "news" else pack_lang
        stale = [(k, t) for k, t in entries
                 if translate.needs_translation(k, t, lang, pack_lang=kind_lang)]
        total += len(entries)
        pending += len(stale)
        print(f"  {kind:<14} {len(entries):>4} strings, {len(stale):>4} to translate")
        if args.dry_run or not stale:
            continue
        done = translate.translate_batch(stale, lang)
        missed = len(stale) - len(done)
        if missed:
            print(f"  {'':<14} {missed} could not be translated "
                  f"(they keep showing in {kind_lang})")

    calls = translate.call_count() - before
    print(f"{total} strings, {pending} needed work, {calls} model call(s).")
    if args.dry_run:
        print("(dry run — nothing written)")


if __name__ == "__main__":
    main()
