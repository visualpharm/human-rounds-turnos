"""test_text_folders_contract.py - every text folder says what it said before they share one primitive (2026-10-28).

Second pass of the architecture audit (item 5): about thirty places spell out the same two idioms by
hand - «decompose, drop the combining marks» and «decompose, keep only ASCII» - each wrapped in its own
`_norm`, `_plain`, `_fold`, `_slug`... Their wrappers are NOT the same function (lowercase before or after,
`str(s or "")` or `None` only, NFD or NFKD, combining class or category `Mn`, punctuation kept or turned
into a space), so what is pinned here is each wrapper as it is, never «accents are removed»:

  - a curated list of inputs (None, `0`, blanks, accents, compatibility forms, punctuation, other
    scripts) with the exact answer of every function, written out;
  - a digest over a sweep of code points (alone, between spaces, and inside a word), which is where the
    NFD/NFKD and `combining`/`Mn` differences live.

Nothing here knows how a function is written, so the file stays valid when its body changes.
The recorded file is written with the code that exists today and is not touched by the change that
shares the primitive. Re-record only from code whose answers are known to be right:
`python3 tests/test_text_folders_contract.py --write`.

Run: python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_text_folders_contract.py
"""
import hashlib
import importlib
import json
import os
import re
import sys
import unicodedata
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

GOLDEN = os.path.join(ROOT, "tests", "golden", "text_folders.json")
# The sweep reaches code points whose category or case changed between Unicode releases, so its digest is
# recorded per release of the interpreter's Unicode database (Python 3.12: 15.0.0, 3.14: 16.0.0); on a release
# with no digest the sweep is skipped, and the curated inputs and `test_text_fold.py` (which compares against the
# hand-written idioms at run time) still hold.
UNICODE = unicodedata.unidata_version

FUNCTIONS = [
    ("app.admin_panel.admin_users", "_ascii_word"), ("app.admin_panel.reports", "_strip_accents"),
    ("app.ai.lab_analysis", "_normalizar"), ("app.connectors.hsi.hsi_client", "_norm"),
    ("app.connectors.msd.msd_data", "_norm"), ("app.connectors.msd.msd_data", "_name_tokens"),
    ("app.connectors.msd.msd_data", "_msd_surnames"), ("app.connectors.msd.msd_data", "_msd_givens"),
    ("app.connectors.puco.puco", "_norm_txt"), ("app.connectors.refeps.refeps", "_strip_accents"),
    ("app.core.permissions", "_slugify"), ("app.core.text_fold", "fold_words"),
    ("app.cuentas.health_coverage", "_normalize"), ("app.modules.chat.chat_agent", "_norm_text"),
    ("app.core.text_fold", "plain"),
    ("app.core.text_fold", "ascii_words"), ("app.modules.chat.chat_tools", "_norm_i18n"),
    ("app.modules.intake.dialogue", "norm"),
    ("app.modules.turnos.doctor_appointments", "_norm"), ("app.modules.turnos.remedies", "_norm"),
    ("app.modules.turnos.schedule_policy", "norm_specialty"), ("app.notifications.confirmations", "_norm"),
    ("app.ops.setup", "_name_key"), ("app.ops.setup", "_slugify"), ("app.patient_web.catalog", "_slug"),
    ("app.patient_web.knowledge", "_plain"),
]

INPUTS = [
    None, "", " ", 0, False, 5, "Cardiología", "  Niño-Sano, ¿ok?  ", "1º A", "ﬁnal", "ÁÉÍÓÚ ñ ü", "O'Brien", "x́",
    "tab\tsep\n", "Dr./Dra. Pérez (guardia)", "ß ı İ", "Sí!", "２０２６", "  A   B  ", "Ñandú Çà", "x (demanda espontánea) y",
    "O.S.P. Buenos Aires (I.O.M.A.)", "Buenos Aires (provincia)", "\u041c\u043e\u0438 \u0437\u0430\u044f\u0432\u043a\u0438", "\u0439 \u0457 \u0419 \u0407", "ǅ ſ ª ² ① ａｂｃ",
    "GARCÍA-LÓPEZ, José María", " nbsp ", "a​b", "Å", "Å", "\ud55c\uad6d\uc5b4 \u3072\u3089\u304c\u306a", "\u0623\u062d\u0645\u062f", "\u0926\u0947\u0935\u0928\u093e\u0917\u0930\u0940 \u0915\u093f",
    "especialidad", "Especialid\u00e1d", "una ESPECIALIDAD m\u00e9dica", "\u0441\u043f\u0435\u0446\u0438\u0430\u043b\u044c\u043d\u043e\u0441\u0442\u044c",
    "\u0432\u0440\u0430\u0447\u0435\u0431\u043d\u044b\u0439 \u043e\u0441\u043c\u043e\u0442\u0440", "\u0432\u0440\u0430\u0447\u0435\u0431\u043d\u044b\u0438",
    "ARGENTINA", "i̇stanbul", "Ǆ", "ǈ", "a,b;c", "__x__", "123 abc", "-_-", "\x1f\x1c a",
]


def _sweep_points():
    for lo, hi in ((0x20, 0x3400), (0xF900, 0x10000), (0x1D400, 0x1D800), (0x1F100, 0x1F200), (0xE0000, 0xE0100)):
        for cp in range(lo, hi):
            if 0xD800 <= cp <= 0xDFFF:
                continue
            yield chr(cp)


def _answer(fn, value):
    try:
        return fn(value)
    except Exception as exc:  # noqa: BLE001 - the exception type is part of what is pinned
        return "raises " + type(exc).__name__


def _sweep_digest(fn):
    digest = hashlib.sha256()
    for c in _sweep_points():
        for sample in (c, " " + c + " ", "A" + c + "b ,."):
            digest.update(repr(_answer(fn, sample)).encode("utf-8", "surrogatepass") + b"\n")
    return digest.hexdigest()


def _callers():
    """Functions that fold text in the middle of a bigger job: pinned through the call that reaches the fold."""
    from app.modules.chat import chat_agent
    from app.patient_web import catalog
    pattern = re.compile("especialidad|\\u0441\\u043f\\u0435\\u0446\\u0438\\u0430\\u043b\\u044c\\u043d\\u043e\\u0441\\u0442\\u044c")
    russian = re.compile("\\u0432\\u0440\\u0430\\u0447\\u0435\\u0431\\u043d\\u044b\\u0439")  # ends in the letter NFKD splits
    return {
        "modules.chat.chat_agent._busca[especialidad]": lambda v: chat_agent._busca(pattern, v),
        "modules.chat.chat_agent._busca[russian]": lambda v: chat_agent._busca(russian, v),
        "patient_web.catalog._validate_preparation[match list]":
            lambda v: catalog._validate_preparation({"nombre": "X", "match": [v]}, True),
        "patient_web.catalog._validate_preparation[match text]":
            lambda v: catalog._validate_preparation({"nombre": "X", "match": v}, True),
    }


def _record():
    out = {}
    folders = [("%s.%s" % (module, name), getattr(importlib.import_module(module), name)) for module, name in FUNCTIONS]
    for name, fn in folders + sorted(_callers().items()):
        out[name] = {
            "inputs": {repr(v): _answer(fn, v) for v in INPUTS},
            "sweep": {UNICODE: _sweep_digest(fn)},
        }
    return json.loads(json.dumps(out, sort_keys=True, default=repr))


def _write():
    """Record from code known to be right; the digests of the other Unicode releases already in the file stay."""
    recorded = _record()
    if os.path.exists(GOLDEN):
        with open(GOLDEN, encoding="utf-8") as fh:
            before = json.load(fh)
        for name, entry in recorded.items():
            entry["sweep"] = {**before.get(name, {}).get("sweep", {}), **entry["sweep"]}
    with open(GOLDEN, "w", encoding="utf-8") as fh:
        json.dump(recorded, fh, sort_keys=True, indent=1)
        fh.write("\n")


class TextFoldersContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(GOLDEN, encoding="utf-8") as fh:
            cls.want = json.load(fh)
        cls.got = _record()

    def test_every_function_is_recorded(self):
        self.assertEqual(sorted(self.got), sorted(self.want))

    def test_answers_on_the_curated_inputs(self):
        for name in sorted(self.want):
            self.assertEqual(self.got[name]["inputs"], self.want[name]["inputs"], name)

    def test_answers_over_the_code_point_sweep(self):
        known = sorted({v for entry in self.want.values() for v in entry["sweep"]})
        if UNICODE not in known:
            self.skipTest("no sweep recorded for Unicode %s (recorded: %s); record it with --write from trusted code"
                          % (UNICODE, ", ".join(known)))
        for name in sorted(self.want):
            self.assertEqual(self.got[name]["sweep"][UNICODE], self.want[name]["sweep"][UNICODE], name)

    def test_what_differs_stays_different(self):
        """Wrappers that look alike and are not: a merge that «fixed» one of them would move a match."""
        def of(name, value):
            return self.got[name]["inputs"][repr(value)]
        russian = "\u041c\u043e\u0438 \u0437\u0430\u044f\u0432\u043a\u0438"
        compat = "\u01c5 \u017f \u00aa \u00b2 \u2460 \uff41\uff42\uff43"
        # `0` is a value for the hsi client, «nothing» for `plain`
        self.assertEqual(of("app.connectors.hsi.hsi_client._norm", 0), "0")
        self.assertEqual(of("app.core.text_fold.plain", 0), "")
        # the ASCII folders drop what is not Latin, the others keep the alphabet
        self.assertEqual(of("app.core.text_fold.ascii_words", russian), "")
        self.assertEqual(of("app.modules.chat.chat_tools._norm_i18n", russian), russian.lower())
        # NFKD turns the feminine ordinal into a letter, NFD leaves it
        self.assertIn("\u00aa", of("app.modules.turnos.doctor_appointments._norm", compat))
        self.assertNotIn("\u00aa", of("app.core.text_fold.fold_words", compat))
        # whitespace: one function collapses it, its twin only lowercases and folds
        self.assertEqual(of("app.modules.turnos.remedies._norm", "  A   B  "), "a b")
        self.assertEqual(of("app.core.text_fold.plain", "  A   B  "), "  a   b  ")


if __name__ == "__main__":
    if "--write" in sys.argv:
        _write()
        print("grabado", GOLDEN)
    else:
        unittest.main()
