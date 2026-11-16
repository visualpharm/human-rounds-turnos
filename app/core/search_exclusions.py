"""search_exclusions.py — when a row's `palabras_excluidas` hides it from a
query, for the Python searches that mirror the home search
(`static/platform/search.mjs`: `excluida()` + `fraseExcluida()`).

Two kinds of exclusion, one rule each:

- A single word ("pap") hides the row when a query word starts with it or,
  from three letters on, is the start of it ("pap" and "papanicolaou").
- A phrase ("fiebre con confusión", "tose sangre" — Pinamar's guardia
  exclusions) hides the row only when the query says the whole phrase, or a
  query of two words or more is already typing it. Testing the phrase word by
  word hid "fiebre" and "tos" on their own and took Telemedicina out of the
  symptom search (the reference-deployment counterpart, 2026-10-19).

Callers pass text already normalized their own way (lowercase, no accents);
this module only splits on punctuation.
"""

import re

_SEP = re.compile(r"[^a-z0-9]+")


def _edges(text):
    return _SEP.sub(" ", str(text or "")).strip()


def is_excluded(query, words, exclusions, variants=None):
    """True when `exclusions` (normalized) hide the row from `query`.

    `words` are the query's content words (stopwords already out);
    `variants(w)` returns a word's morphological variants (default: itself).
    """
    variants = variants or (lambda w: [w])
    singles = [e for e in exclusions if e and " " not in e.strip()]
    phrases = [_edges(e) for e in exclusions if e and " " in e.strip()]
    for w in words:
        for v in variants(w):
            for e in singles:
                if v.startswith(e) or (len(v) >= 3 and e.startswith(v)):
                    return True
    q = _edges(query)
    if not q:
        return False
    for f in phrases:
        if (" " + f) in (" " + q) or (" " in q and f.startswith(q)):
            return True
    return False
