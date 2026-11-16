"""text_fold.py - text folded to comparable forms (2026-10-25, primitives 2026-10-28).

Two idioms were written out by hand in about thirty places, each wrapped in its own `_norm`, `_plain`,
`_fold`, `_slug`...: «decompose, drop the combining marks» (`strip_accents`) and «decompose, keep only
ASCII» (`ascii_fold`). They live here once. What stays in each caller is the part that really differs:
the coercion of its input (`str(s or "")`, `None` only), whether it lowercases before or after, how it
trims or collapses spaces and what it does with punctuation. Those wrappers do NOT fold alike (a
search or a match would move if two were merged), so each keeps its own; `tests/test_text_folders_contract.py`
pins every one of them, and `tests/test_text_fold.py` pins the primitives against the hand-written idioms.

`fold_words`, the one wrapper two modules shared (`ai/ai_referral._norm`, `modules/intake/redflags._norm`),
and `alnum_words`, the «punctuation as a space» half of it, also live here.
"""

import unicodedata


def strip_accents(text, form="NFKD"):
    """`text` decomposed with `form` and without its combining marks (accents, tildes, diaeresis)."""
    return "".join(c for c in unicodedata.normalize(form, text) if not unicodedata.combining(c))


def strip_nonspacing(text, form="NFD"):
    """Like `strip_accents`, but drops the characters of category `Mn` instead of those with a combining
    class: the two agree on every Latin letter and differ on a few marks of other scripts, and each caller
    keeps the one it had."""
    return "".join(c for c in unicodedata.normalize(form, text) if unicodedata.category(c) != "Mn")


def ascii_fold(text):
    """`text` decomposed (NFKD) and cut down to the ASCII characters that survive: accents go,
    letters of other alphabets are deleted."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def alnum_words(text):
    """The words of `text` (letters and digits, Unicode `isalnum`) joined by one space."""
    return " ".join("".join(c if c.isalnum() else " " for c in text).split())


def clean(value):
    """`value` as text without the spaces around it, or None when nothing is left (None, "", "  ")."""
    value = str(value or "").strip()
    return value or None


def plain(text):
    """Lowercase first, then without accents (`strip_accents`); `""` for a falsy value."""
    return strip_accents(str(text or "").lower())


def same_name_tokens(a, b):
    """Do two sets of name tokens name the same person? Neither empty, and one contained in the other (a
    middle or second name missing on one side) or at least two tokens in common (last-first order)."""
    if not a or not b:
        return False
    return a <= b or b <= a or len(a & b) >= 2


def fold_words(text):
    """Lowercase, no accents, punctuation as a space, spaces collapsed."""
    return alnum_words(strip_accents(str(text or "").lower()))


def ascii_words(text):
    """Lowercase, cut down to ASCII (`ascii_fold`), words joined by one space; `""` for a falsy value. Letters
    of other alphabets are deleted, punctuation stays."""
    return " ".join(ascii_fold(str(text or "")).lower().split())
