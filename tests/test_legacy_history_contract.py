"""test_legacy_history_contract.py - Alepo and Marqui answer what they answered before they share their code (2026-10-28).

Second pass of the architecture audit (section 6): `connectors/alepo/alepo.py` and `connectors/marqui/marqui.py`
are the same 118 lines but for the system name, the fake-mode variable and the sample file next to them. Before
the code moves to one module, this pins what each of them does, with the same inputs:

  - fake mode: the entries for the curated patients, for a DNI nobody knows (the deterministic template), for
    blank and odd DNIs (None, "", 0, spaces, an int), stamped and sorted;
  - the switch: `1` (also with spaces) turns the fake on, anything else leaves the real mode, which raises
    `ProviderUnavailable` with the system's own name in the message;
  - the command line (`main`): usage text and exit code, and what it prints;
  - the adapter classes, the manifests and the sample files that stay where they are.

The recorded file is written with the code that exists today and is not touched by the change that merges
the two: `python3 tests/test_legacy_history_contract.py --write`.

Run: python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_legacy_history_contract.py
"""
import contextlib
import hashlib
import importlib
import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app import connectors  # noqa: E402

GOLDEN = os.path.join(ROOT, "tests", "golden", "legacy_history.json")

SYSTEMS = ("alepo", "marqui")
ODD_DNIS = [None, "", 0, "  ", " 30111222 ", 30111222, "no-es-un-dni", "999999999", "1", "00000001", False]
SWITCH_VALUES = [None, "", "1", " 1 ", "0", "true", "yes", "11"]


def _env_name(system):
    return "TURNOS_FAKE_" + system.upper()


def _digest(entries):
    return {"count": len(entries), "sha256": hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest(),
            "first": [(e.get("fecha"), e.get("tipo"), e.get("sistema")) for e in entries[:2]],
            "dates_sorted": [e.get("fecha") or "" for e in entries] == sorted(e.get("fecha") or "" for e in entries)}


def _answer(fn, *args):
    try:
        return _digest(fn(*args))
    except Exception as exc:  # noqa: BLE001 - the exception type and its text are pinned
        return "raises %s: %s" % (type(exc).__name__, exc)


def _sample_dnis(system):
    path = os.path.join(ROOT, "app", "connectors", system, "sample_historias.json")
    with open(path, encoding="utf-8") as fh:
        known = sorted((json.load(fh).get("pacientes") or {}))
    return known


def _record_system(system, set_switch):
    mod = importlib.import_module("%s.%s.%s" % (connectors.__name__, system, system))
    adapter = importlib.import_module("%s.%s.adapter" % (connectors.__name__, system))
    out = {"fake": {}, "switch": {}, "cli": {}}
    set_switch(system, "1")
    dnis = _sample_dnis(system)
    out["known_dnis"] = dnis
    for dni in dnis + ODD_DNIS:
        out["fake"][repr(dni)] = _answer(mod.lookup_by_dni, dni)
    connector = [c for c in vars(adapter).values() if isinstance(c, type) and issubclass(c, connectors.ClinicalHistoryLookupConnector)
                 and c is not connectors.ClinicalHistoryLookupConnector]
    out["adapter"] = {"classes": sorted(c.__name__ for c in connector),
                      "answers": {repr(dni): _answer(connector[0]().lookup_by_dni, dni) for dni in (dnis[:1] + ["999999999"])}}
    for value in SWITCH_VALUES:
        set_switch(system, value)
        out["switch"][repr(value)] = _answer(mod.lookup_by_dni, "999999999")
    set_switch(system, None)
    out["cli"]["usage"] = _cli(mod, [])
    out["cli"]["usage_two"] = _cli(mod, ["1", "2"])
    set_switch(system, None)
    out["cli"]["known"] = _cli(mod, dnis[:1])
    set_switch(system, None)
    out["cli"]["unknown"] = _cli(mod, ["999999999"])
    set_switch(system, None)
    out["manifest"] = connectors.load_manifest(system)
    out["sample_file_keys"] = sorted(json.load(open(os.path.join(ROOT, "app", "connectors", system, "sample_historias.json"),
                                                    encoding="utf-8")))
    return out


def _cli(mod, argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            code = mod.main(argv)
        except Exception as exc:  # noqa: BLE001
            code = "raises %s" % type(exc).__name__
    text = buf.getvalue()
    return {"code": code, "stdout_sha256": hashlib.sha256(text.encode()).hexdigest(), "stdout_head": text[:80]}


def _record(set_switch):
    out = {system: _record_system(system, set_switch) for system in SYSTEMS}
    return json.loads(json.dumps(out, sort_keys=True, default=repr))


def _write():
    case = unittest.TestCase()  # only to hold the undo of the environment writes
    _env.set_env(case, **{_env_name(s): None for s in SYSTEMS})

    def set_switch(system, value):
        _env.set_env(case, **{_env_name(system): value})
    try:
        with open(GOLDEN, "w", encoding="utf-8") as fh:
            json.dump(_record(set_switch), fh, sort_keys=True, indent=1)
            fh.write("\n")
    finally:
        case.doCleanups()


class LegacyHistoryContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(GOLDEN, encoding="utf-8") as fh:
            cls.want = json.load(fh)

    def setUp(self):
        # the switches start unset and are put back after each test, whatever the CLI wrote
        _env.set_env(self, **{_env_name(s): None for s in SYSTEMS})

    def _record(self):
        def set_switch(system, value):
            _env.set_env(self, **{_env_name(system): value})
        return _record(set_switch)

    def test_each_system_answers_the_same(self):
        got = self._record()
        self.assertEqual(sorted(got), sorted(self.want))
        for system in SYSTEMS:
            for part in sorted(self.want[system]):
                self.assertEqual(got[system][part], self.want[system][part], "%s %s" % (system, part))

    def test_the_two_systems_stay_two(self):
        """Guards the guard: what makes them different is the system's name and its own sample file."""
        want = self.want
        self.assertNotEqual(want["alepo"]["known_dnis"] + want["marqui"]["known_dnis"], [])
        unknown = "'999999999'"
        a, m = want["alepo"]["fake"][unknown], want["marqui"]["fake"][unknown]
        self.assertEqual({e[2] for e in a["first"]}, {"alepo"})
        self.assertEqual({e[2] for e in m["first"]}, {"marqui"})
        self.assertIn("alepo", want["alepo"]["switch"]["None"])
        self.assertIn("marqui", want["marqui"]["switch"]["None"])
        self.assertEqual(want["alepo"]["switch"]["'1'"]["count"], a["count"])


if __name__ == "__main__":
    if "--write" in sys.argv:
        _write()
        print("grabado", GOLDEN)
    else:
        unittest.main()
