"""test_text_fold.py - the shared primitives of core/text_fold.py are the idioms they replaced (2026-10-28).

Each primitive is compared with the hand-written line that stood in thirty places before it, over a sweep
of code points (alone, between spaces, inside a word) and a few strings. The wrappers that call them keep
their own rules and are pinned by `test_text_folders_contract.py`.

Run: python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_text_fold.py
"""
import os
import sys
import unicodedata
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import text_fold  # noqa: E402

RANGES = ((0x20, 0x3400), (0xF900, 0x10000), (0x1D400, 0x1D800), (0x1F100, 0x1F200), (0xE0000, 0xE0100))
WORDS = ["", "Cardiolog\u00eda", "  Ni\u00f1o-Sano, \u00bfok?  ", "\ufb01nal", "\u00aa \u00b2 \u2460", "A\u030a \u00c5", "x\u0301",
         "\u0439 \u0457", "\u0926\u0947\u0935\u0928\u093e\u0917\u0930\u0940 \u0915\u093f", "O.S.P. (I.O.M.A.)", "\u01c5 \u017f"]


def samples():
    for text in WORDS:
        yield text
    for lo, hi in RANGES:
        for cp in range(lo, hi):
            if 0xD800 <= cp <= 0xDFFF:
                continue
            c = chr(cp)
            yield c
            yield " " + c + " "
            yield "A" + c + "b ,."


# the lines the primitives replaced, as they were written in the callers
def _combining(form):
    return lambda s: "".join(c for c in unicodedata.normalize(form, s) if not unicodedata.combining(c))


def _nonspacing(form):
    return lambda s: "".join(c for c in unicodedata.normalize(form, s) if unicodedata.category(c) != "Mn")


def _ascii(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def _chat_tools_norm(s):
    # modules/chat/chat_tools._norm before it became text_fold.ascii_words
    return " ".join(unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower().split())


def _intake_catalog_norm(s):
    # modules/intake/intake_catalog._norm before it became text_fold.plain(...).strip()
    t = unicodedata.normalize("NFKD", str(s or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c)).strip()


def _fold_words(text):
    s = unicodedata.normalize("NFKD", str(text or "").lower())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join("".join(c if c.isalnum() else " " for c in s).split())


class TextFoldPrimitives(unittest.TestCase):
    def check(self, new, old):
        for s in samples():
            self.assertEqual(new(s), old(s), repr(s))

    def test_strip_accents_nfkd(self):
        self.check(text_fold.strip_accents, _combining("NFKD"))

    def test_strip_accents_nfd(self):
        self.check(lambda s: text_fold.strip_accents(s, "NFD"), _combining("NFD"))

    def test_strip_nonspacing_nfd(self):
        self.check(text_fold.strip_nonspacing, _nonspacing("NFD"))

    def test_strip_nonspacing_nfkd(self):
        self.check(lambda s: text_fold.strip_nonspacing(s, "NFKD"), _nonspacing("NFKD"))

    def test_ascii_fold(self):
        self.check(text_fold.ascii_fold, _ascii)

    def test_fold_words(self):
        self.check(text_fold.fold_words, _fold_words)

    def test_ascii_words_is_the_chat_tools_norm(self):
        self.check(text_fold.ascii_words, _chat_tools_norm)
        for value in (None, 0, False, 5, "", "  A\tB\n"):
            self.assertEqual(text_fold.ascii_words(value), _chat_tools_norm(value), repr(value))

    def test_plain_then_strip_is_the_intake_catalog_norm(self):
        self.check(lambda s: text_fold.plain(s).strip(), _intake_catalog_norm)
        for value in (None, 0, False, 5, "", "  A\tB\n"):
            self.assertEqual(text_fold.plain(value).strip(), _intake_catalog_norm(value), repr(value))

    def test_the_two_strippers_are_not_one(self):
        # a Devanagari vowel sign has category Mn and combining class 0
        sign = "\u0941"
        self.assertEqual(text_fold.strip_accents(sign, "NFD"), sign)
        self.assertEqual(text_fold.strip_nonspacing(sign), "")

    def test_a_non_string_is_the_callers_business(self):
        for fn in (text_fold.strip_accents, text_fold.strip_nonspacing, text_fold.ascii_fold):
            with self.assertRaises(TypeError):
                fn(None)


if __name__ == "__main__":
    unittest.main()
