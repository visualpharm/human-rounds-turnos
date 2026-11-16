"""test_app_composition.py — mechanical before/after guard for the Tier 4
composition extraction (2026-08-08).

The extraction moved app.py's implementation into `routers/` and
`services/` and left app.py a composition root. The whole point of the
slice is ZERO routing-behavior change, so the guard is mechanical, not
narrative: `tier4_composition_inventory.json` (sibling file) is the FULL
ordered route inventory — every (path, sorted methods) pair, flattened in
registration order — plus the middleware list, generated from the
PRE-extraction app.py (commit 051f81a9) with the exact `_inventory()`
below. The composed app must reproduce it identically, order included:
Starlette matches by registration order, so order IS the routing contract
(platform routers before module routers before the terminal).

Also pinned here:
  - the terminal stays the LAST route (everything after it would be
    unreachable);
  - app.py stays a genuinely small composition root (<100 lines — the
    extraction's own acceptance bar, so proxy/transport detail can't
    quietly creep back in).

Regenerated 2026-10-14 (usability study): GET /study, /study/s/{token},
POST /api/study/session and /api/study/s/{token}/{event,result,finish};
GET /admin/usability and /api/admin/usability; POST
/api/admin/usability/{analyze,findings} and findings/{finding_id}.
The existing AI-register routes and middleware keep their registration order.

To regenerate the fixture after a DELIBERATE routing change (a new slice
mounting a new router — never as a way to green a failed run):
    python3 tests/snapshots.py --write
which also refreshes the route census counters and the admin-access map that
move with the same route. (`--write-fixture` below is the old spelling of it.)

Regenerated 2026-10-14 (HSI external history): preserve every existing
AI-register route and add the manifest-owned read-only external-history GET.

Regenerated 2026-10-23 (K10 clinical access log): adds the admin-only
GET /api/admin/patients/{dni}/access-log; every other route keeps its place.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_app_composition.py"
"""
import json
import os
import sys
import unittest

import _env  # noqa: E402
_env.process_defaults(TURNOS_ENV_FILE="/dev/null")
_env.process_defaults(DATABASE_URL="")  # boot without Postgres
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import main as app_module  # noqa: E402

_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "tier4_composition_inventory.json")


def _flat(routes, acc):
    """Flatten the app's route table in registration order. Newer FastAPI
    wraps `include_router` results in a lazy `_IncludedRouter`; older
    versions flatten eagerly into plain APIRoutes — handle both so this
    guard doesn't couple to one FastAPI release."""
    for r in routes:
        if type(r).__name__ == "_IncludedRouter":
            for c in r.effective_candidates():
                rr = getattr(c, "route", None) or c
                if hasattr(rr, "path") and hasattr(rr, "methods"):
                    acc.append(rr)
                elif type(rr).__name__ == "_IncludedRouter":
                    _flat([rr], acc)
                else:
                    # LOUD, never a silent under-count: an unrecognized
                    # candidate type means FastAPI changed its internals —
                    # update this flattener, don't let the guard shrink.
                    raise AssertionError(
                        "unrecognized route candidate type %r — FastAPI "
                        "internals changed; update _flat()" % type(rr).__name__)
        else:
            acc.append(r)
    return acc


def _inventory():
    routes = [
        {"path": r.path, "methods": sorted(r.methods or [])}
        for r in _flat(app_module.app.routes, [])
    ]
    middleware = [str(m) for m in app_module.app.user_middleware]
    return {"routes": routes, "middleware": middleware}


class AppCompositionTestCase(unittest.TestCase):
    def test_route_inventory_matches_pre_extraction(self):
        with open(_FIXTURE, encoding="utf-8") as f:
            expected = json.load(f)
        got = _inventory()
        # Compare in pieces so a failure names the divergence instead of
        # dumping two 200-entry lists.
        self.assertEqual(expected["middleware"], got["middleware"])
        exp_routes, got_routes = expected["routes"], got["routes"]
        for i, (e, g) in enumerate(zip(exp_routes, got_routes)):
            self.assertEqual(
                e, g,
                "route order/inventory diverges at index %d: expected %r, "
                "got %r" % (i, e, g))
        self.assertEqual(
            len(exp_routes), len(got_routes),
            "route count changed: expected %d, got %d — a route was added "
            "or dropped; if DELIBERATE, regenerate the fixture (see module "
            "docstring)" % (len(exp_routes), len(got_routes)))

    def test_native_terminal_is_the_last_route(self):
        flat = _flat(app_module.app.routes, [])
        self.assertEqual(flat[-1].path, "/{full_path:path}")
        self.assertEqual(flat[-1].endpoint.__module__, "app.routers.native_terminal")
        # ...and nowhere else: a second terminal earlier in the table
        # would shadow every route after it.
        earlier = [r.path for r in flat[:-1]]
        self.assertNotIn("/{full_path:path}", earlier)

    def test_app_py_stays_a_small_composition_root(self):
        with open(os.path.join(ROOT, "app", "main.py"), encoding="utf-8") as f:
            lines = f.readlines()
        self.assertLess(
            len(lines), 100,
            "app.py grew to %d lines — implementation detail is creeping "
            "back into the composition root; move it into routers/ or "
            "services/" % len(lines))


if __name__ == "__main__":
    if "--write-fixture" in sys.argv:
        import snapshots
        sys.exit(snapshots.main(["--write"]))
    unittest.main()
