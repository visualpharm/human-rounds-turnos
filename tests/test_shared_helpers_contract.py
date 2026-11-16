"""test_shared_helpers_contract.py — los ayudantes que se juntaron dicen lo mismo que antes (2026-10-25).

Auditoría de arquitectura (2026-10-25, punto 5): la misma pequeña función estaba escrita varias veces
con el mismo nombre. «Mismo nombre» no es «misma regla», así que antes de juntar nada se fijó, para
cada grupo y con el código previo, lo que cada copia devuelve (`tests/golden/shared_helpers.json`), y
sólo se juntaron las que dan exactamente lo mismo:

  - `digits`/`dni` (`core/ids.py`): las copias `re.sub(r"\\D", "", str(v or ""))`. NO entran
    `format._digits` (`0` da «0», no «») ni `ai_referral._digits`/`lab_results._digits` (`isdigit()`
    deja pasar «²»): se fijan acá como lo que son.
  - `extract_object` (`ai/json_extract.py`): `_extract_json` de `analysis` y de `consult_compose`
    (idénticas). NO entran `chart_summary._extract_json` (devuelve `None`, acepta listas) ni
    `ai_referral.extract_json` (elige entre varios documentos).
  - `allow` y `sweep` (`core/rate_window.py`): los límites por IP en ventana deslizante (`_allowed`/
    `_permitido`) y la poda de IP vencidas (`_sweep`). NO entran los `_sweep` de la cola (tareas de
    procrastinate), `ai_cooldown._sweep` (otra estructura) ni `mcp_api._permitido` (delega en otro guardia).
  - `fold_words` (`core/text_fold.py`): `_norm` de `ai_referral` y de `intake/redflags` (idénticas). Las
    otras nueve `_norm` normalizan distinto (NFD/NFKD, ASCII, paréntesis, orden de minúsculas…) y quedan.

El archivo grabado no lo toca el cambio que junta los ayudantes. Regrabar sólo sobre código cuya salida
ya se sabe correcta: `python3 tests/test_shared_helpers_contract.py --write`.

Correr: python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_shared_helpers_contract.py
"""
import importlib
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1")

GOLDEN = os.path.join(ROOT, "tests", "golden", "shared_helpers.json")

DIGIT_INPUTS = [None, "", 0, 0.0, False, 12345678, "12.345.678", " 12 345 678 ", "DNI: 30.123.456-9",
                "+54 9 2254 12-3456", "٣٤٥", "²³", "①2", "1 2", "ab", "٠١٢x3"]
DIGIT_FUNCTIONS = [
    ("app.core.log_pii", "_digits"), ("app.cuentas.ocr_dni", "_digits"), ("app.modules.chat.whatsapp_chat", "_digits"),
    ("app.core.patient_contact", "_dni"), ("app.core.pii", "_digitos"), ("app.cuentas.accounts", "_norm_dni"),
    ("app.core.log_pii", "dni"),
    ("app.core.format", "_digits"), ("app.ai.ai_referral", "_digits"), ("app.ai.lab_results", "_digits"),
]

JSON_INPUTS = [None, "", "   ", '{"a": 1}', '```json\n{"a": 1}\n```', '```\n{"a": 2}\n```', "```JSON\n{\"a\": 3}\n```",
               'Claro: {"a": 4} listo', '[{"a": 5}]', '{"a": {"b": [1, 2]}}', '{"a": ', "}{", "nada",
               'texto ```json\n{"a": 6}\n``` cola', '{"a": "}"} y {"b": 2}', '"solo texto"', "42", '{"a": 7}\n```',
               '```json\n{"a": 8}', "[1, 2, 3]", '{"a": 1} {"b": 2}']
JSON_FUNCTIONS = [("app.modules.intake.analysis", "_extract_json"), ("app.modules.intake.consult_compose", "_extract_json"),
                  ("app.ai.chart_summary", "_extract_json"), ("app.ai.lab_analysis", "_extract_json"),
                  ("app.ai.ai_referral", "extract_json")]

NORM_INPUTS = [None, "", "Cardiología", "  Niño-Sano, ¿ok?  ", "1º A", "ﬁnal", "ÁÉÍÓÚ ñ ü", "O'Brien",
               "x́", 5, "tab\tsep\n", "Dr./Dra. Pérez (guardia)", "ß ı İ", "Sí!", "２０２６"]
NORM_FUNCTIONS = [("app.ai.ai_referral", "_norm"), ("app.modules.intake.redflags", "_norm")]

# per-IP limits: (module, function, limit-function-or-None, hard limit)
LIMITERS = [("app.connectors.puco.puco", "_permitido", "_per_hour"), ("app.cuentas.auth_renaper", "_permitido", "_per_hour"),
            ("app.cuentas.ocr_dni", "_permitido", "_per_hour"), ("app.modules.chat.chat_api", "_allowed", "_per_hour"),
            ("app.modules.intake.interview_patient", "_allowed", "_per_hour"),
            ("app.modules.turnos.slot_holds", "_allowed", "_per_hour"), ("app.modules.chat.voice", "_allowed", None)]

# daily / hourly pruners over {ip: [epoch, ...]}: (module, hits-name, window-name, entry-factory)
SWEEPERS = [("app.ai.abuse_guard", "_HITS", "_DAY", "plain"), ("app.auth.panel_login", "_HITS", "_DAY", "plain"),
            ("app.modules.turnos.searches", "_HITS", "_DAY", "plain"), ("app.ai.ai_costs", "_WINDOW", "_HOUR", "pairs"),
            ("app.ai.ai_cooldown", "_STATE", "_DAY", "state")]


def _call_all(functions, inputs):
    out = {}
    for module, name in functions:
        fn = getattr(importlib.import_module(module), name)
        row = {}
        for value in inputs:
            try:
                result = fn(value)
            except Exception as exc:  # noqa: BLE001 — the exception type is part of what is pinned
                result = "raises " + type(exc).__name__
            row[repr(value)] = result
        out["%s.%s" % (module, name)] = row
    return out


def _limiter_run(module, name, per_hour):
    mod = importlib.import_module(module)
    fn = getattr(mod, name)
    patches = [mock.patch.object(mod, per_hour, lambda: 3)] if per_hour else []
    clock = {"now": 1_000_000.0}
    patches.append(mock.patch("time.time", lambda: clock["now"]))
    results = []
    mod._HITS.clear()
    for p in patches:
        p.start()
    try:
        keys = [None, "", "a", "b", "a"]
        for i in range(30):
            clock["now"] += 400.0 if i % 7 else 3700.0
            key = keys[i % len(keys)]
            results.append([key, fn(key)])
        results.append(["hits", {k: v for k, v in sorted(mod._HITS.items(), key=lambda kv: str(kv[0]))}])
        # the opportunistic pruning: > 5000 keys, all stale
        mod._HITS.clear()
        for n in range(5001):
            mod._HITS["stale%d" % n] = [clock["now"] - 9000.0]
        results.append(["burst", fn("fresh"), len(mod._HITS)])
        for i in range(25):
            clock["now"] += 1.0
            results.append(["limit", i, fn("same")])
    finally:
        for p in reversed(patches):
            p.stop()
        mod._HITS.clear()
    return results


def _sweep_run(module, hits_name, window_name, kind):
    mod = importlib.import_module(module)
    hits = getattr(mod, hits_name)
    window = getattr(mod, window_name)
    now = 5_000_000.0
    stamps = {"old": [now - window - 5], "fresh": [now - 10], "mixed": [now - window - 5, now - 10],
              "edge": [now - window], "empty": []}
    hits.clear()
    for key, values in stamps.items():
        if kind == "plain":
            hits[key] = list(values)
        elif kind == "pairs":
            hits[key] = [(t, True) for t in values]
        else:
            if values:
                hits[key] = {"last": max(values)}
    try:
        mod._sweep(now)
        return sorted(hits)
    finally:
        hits.clear()


def _record():
    out = {"digits": _call_all(DIGIT_FUNCTIONS, DIGIT_INPUTS), "json": _call_all(JSON_FUNCTIONS, JSON_INPUTS),
           "norm": _call_all(NORM_FUNCTIONS, NORM_INPUTS), "limiters": {}, "sweepers": {}}
    for module, name, per_hour in LIMITERS:
        out["limiters"]["%s.%s" % (module, name)] = _limiter_run(module, name, per_hour)
    for module, hits_name, window_name, kind in SWEEPERS:
        out["sweepers"]["%s.%s" % (module, hits_name)] = _sweep_run(module, hits_name, window_name, kind)
    return json.loads(json.dumps(out, sort_keys=True, ensure_ascii=False, default=repr))


def _write():
    os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
    with open(GOLDEN, "w", encoding="utf-8") as fh:
        json.dump(_record(), fh, sort_keys=True, ensure_ascii=False, indent=1)
        fh.write("\n")


class SharedHelpersContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(GOLDEN, encoding="utf-8") as fh:
            cls.want = json.load(fh)
        cls.got = _record()

    def _same(self, group):
        self.assertEqual(sorted(self.got[group]), sorted(self.want[group]))
        for name in sorted(self.want[group]):
            self.assertEqual(self.got[group][name], self.want[group][name], name)

    def test_digit_extractors(self):
        self._same("digits")

    def test_json_extractors(self):
        self._same("json")

    def test_text_folders(self):
        self._same("norm")

    def test_per_ip_limiters(self):
        self._same("limiters")

    def test_ip_pruners(self):
        self._same("sweepers")

    def test_what_is_pinned_apart_stays_apart(self):
        digits = self.got["digits"]
        # `0` is a value, not «nothing», for the formatter; for the rest it is
        self.assertEqual(digits["app.core.format._digits"]["0"], "0")
        self.assertEqual(digits["app.core.pii._digitos"]["0"], "")
        # `isdigit()` keeps superscripts, `\D` does not
        self.assertEqual(digits["app.ai.ai_referral._digits"]["'²³'"], "²³")
        self.assertEqual(digits["app.core.pii._digitos"]["'²³'"], "")
        # the intake pair always answers a dict; the summary one answers None, and takes lists
        js = self.got["json"]
        self.assertEqual(js["app.modules.intake.analysis._extract_json"]["'nada'"], {})
        self.assertIsNone(js["app.ai.chart_summary._extract_json"]["'nada'"])
        self.assertEqual(js["app.modules.intake.analysis._extract_json"]["'[1, 2, 3]'"], {})
        self.assertEqual(js["app.ai.chart_summary._extract_json"]["'[1, 2, 3]'"], [1, 2, 3])


if __name__ == "__main__":
    if "--write" in sys.argv:
        _write()
        print("grabado", GOLDEN)
    else:
        unittest.main()
