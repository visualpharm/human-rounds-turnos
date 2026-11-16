"""test_ip_limiters_contract.py - the three daily per-IP limits that prune every 500 calls (2026-10-28).

`ai/abuse_guard.allowed`, `modules/turnos/searches.allowed` and `auth/panel_login.permitido` each carried
the same fifteen lines: count the call, prune the stale IPs every 500th call, filter the IP's hits to the
last 24 hours, refuse when the limit is reached (without recording), record otherwise. `abuse_guard` adds
an hourly limit to the daily one; the other two are a copy of each other with different names.

What is pinned, for each one with a fake clock:

  - the answers and the dict (`_HITS`) after thirty calls on blank and repeated keys, stepping 400 s or
    one hour and a bit, with the limits cut to a handful;
  - the cadence of the pruning: how many calls pass between two prunes (500), found by planting a stale
    key before every call and seeing when it goes - so the count does not depend on how many calls the
    process made before the test;
  - that a refused call is not recorded and a blank key is the bucket «?».

Nothing here knows where the counter lives, so the file stays valid when the copies become one. The
recorded file `tests/golden/ip_limiters.json` is written with the code that existed before and is not
touched by the change that joins them. Re-record only from code whose answers are known to be right:
`python3 tests/test_ip_limiters_contract.py --write`.

Run: python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_ip_limiters_contract.py
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

_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

GOLDEN = os.path.join(ROOT, "tests", "golden", "ip_limiters.json")

# (module, function, {limit function: value})
LIMITERS = [("app.ai.abuse_guard", "allowed", {"_per_hour": 3, "_per_day": 5}),
            ("app.modules.turnos.searches", "allowed", {"_per_day": 3}),
            ("app.auth.panel_login", "permitido", {"_per_day": 3})]

DAY = 86400.0


def _sweep_gap(fn, hits, clock, limit=2000):
    """Calls until the stale key planted before each call is gone (it is the prune that removes it)."""
    for n in range(1, limit + 1):
        hits["stale"] = [clock["now"] - 2 * DAY]
        fn("probe%d" % (n % 5))
        clock["now"] += 1.0
        if "stale" not in hits:
            return n
    return None


def _run(module, name, limits):
    mod = importlib.import_module(module)
    fn = getattr(mod, name)
    clock = {"now": 1_000_000.0}
    patches = [mock.patch.object(mod, attr, lambda value=value: value) for attr, value in limits.items()]
    patches.append(mock.patch("time.time", lambda: clock["now"]))
    for p in patches:
        p.start()
    try:
        mod._HITS.clear()
        _sweep_gap(fn, mod._HITS, clock)           # lines the counter up: the next prune is 500 calls away
        # How long that took depends on the calls the process made before this test (a full backend
        # run makes some); the clock goes back to where a fresh process ends it, the golden's timeline.
        clock["now"] = 1_000_500.0
        mod._HITS.clear()
        results = []
        keys = [None, "", "a", "b", "a"]
        for i in range(30):
            clock["now"] += 400.0 if i % 7 else 3700.0
            key = keys[i % len(keys)]
            results.append([key, fn(key)])
        results.append(["hits", {k: v for k, v in sorted(mod._HITS.items(), key=lambda kv: str(kv[0]))}])
        for i in range(8):
            clock["now"] += 1.0
            results.append(["limit", i, fn("same")])
        results.append(["refused-not-recorded", len(mod._HITS["same"])])
        mod._HITS.clear()
        _sweep_gap(fn, mod._HITS, clock)
        gaps = [_sweep_gap(fn, mod._HITS, clock) for _ in range(4)]
        results.append(["gaps", gaps])
        # a prune keeps the IP with a hit inside the day and drops the one without
        mod._HITS.clear()
        _sweep_gap(fn, mod._HITS, clock)
        mod._HITS.clear()
        mod._HITS["old"] = [clock["now"] - DAY - 5]
        mod._HITS["fresh"] = [clock["now"] - 10]
        for _ in range(499):
            fn("tick")
        results.append(["before-the-prune", sorted(mod._HITS)])
        fn("tick")
        results.append(["at-the-prune", sorted(mod._HITS)])
        return results
    finally:
        for p in reversed(patches):
            p.stop()
        mod._HITS.clear()


def _record():
    out = {"%s.%s" % (module, name): _run(module, name, limits) for module, name, limits in LIMITERS}
    return json.loads(json.dumps(out, sort_keys=True, ensure_ascii=False, default=repr))


def _write():
    os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
    with open(GOLDEN, "w", encoding="utf-8") as fh:
        json.dump(_record(), fh, sort_keys=True, ensure_ascii=False, indent=1)
        fh.write("\n")


class IpLimitersContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(GOLDEN, encoding="utf-8") as fh:
            cls.want = json.load(fh)
        cls.got = _record()

    def test_each_limiter_answers_what_it_answered_before_they_were_joined(self):
        self.assertEqual(sorted(self.got), sorted(self.want))
        for name in sorted(self.want):
            self.assertEqual(self.got[name], self.want[name], name)

    def test_the_prune_comes_every_500_calls(self):
        for name, results in self.got.items():
            gaps = next(r[1] for r in results if r[0] == "gaps")
            self.assertEqual(gaps, [500, 500, 500, 500], name)

    def test_what_is_pinned_apart_stays_apart(self):
        # the abuse guard also has an hourly limit: three calls inside the hour, the fourth is refused
        # even though the daily limit (5) still has room
        guard = [r for r in self.got["app.ai.abuse_guard.allowed"] if r[0] == "limit"]
        self.assertEqual([r[2] for r in guard], [True, True, True, False, False, False, False, False])
        daily = [r for r in self.got["app.modules.turnos.searches.allowed"] if r[0] == "limit"]
        self.assertEqual([r[2] for r in daily], [True, True, True, False, False, False, False, False])


if __name__ == "__main__":
    if "--write" in sys.argv:
        _write()
        print("wrote", GOLDEN)
    else:
        unittest.main()
