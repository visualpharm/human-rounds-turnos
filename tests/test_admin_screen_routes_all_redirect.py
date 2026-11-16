"""B9 guard over the WHOLE route table: no admin HTML screen answers a
no-session browser GET with JSON.

The hand-written lists in the sibling redirect tests pin the screens that
existed when B9 was fixed. This one walks ``app.routes`` so a screen added
later cannot quietly ship the old 401 JSON again.
"""

import os
import re
import sys
import unittest

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="test-admin-screen-routes-pass")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# Screens a visitor must reach WITHOUT a session, by design.
_PUBLIC_SCREENS = {"/admin/login"}
# Retired addresses that only forward (308) to a live screen; that screen is
# the one that asks for the login (checked below), the old address holds no page.
_LEGACY_FORWARDS = {"/admin/users/import": "/admin/doctors?import=1"}
_SCREEN = re.compile(r"^/admin/[a-z][a-z0-9-]*(/[a-z][a-z0-9-]*)*$")


def _leaf_routes(routes, prefix=""):
    # FastAPI wraps every include_router() in an _IncludedRouter whose own
    # .routes is empty; the real endpoints live on .original_router.
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            ctx = getattr(route, "include_context", None)
            yield from _leaf_routes(inner.routes, prefix + (getattr(ctx, "prefix", "") or ""))
            continue
        sub = getattr(route, "routes", None)
        if sub and not hasattr(route, "endpoint"):
            yield from _leaf_routes(sub, prefix + (getattr(route, "path", "") or ""))
            continue
        yield prefix + (getattr(route, "path", "") or ""), getattr(route, "methods", None) or set()


def _admin_screen_paths():
    paths = set()
    for path, methods in _leaf_routes(app_module.app.routes):
        if "GET" not in methods or not _SCREEN.match(path):
            continue
        if path in _PUBLIC_SCREENS or "reset" in path or "password" in path:
            continue
        paths.add(path)
    return sorted(paths)


class TestEveryAdminScreenRedirectsWithoutSession(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app_module.app)

    def test_route_table_has_screens(self):
        # Guards the walk itself: an empty set would make the check vacuous.
        self.assertGreaterEqual(len(_admin_screen_paths()), 15)

    def test_no_screen_answers_json_401(self):
        failures = []
        for path in _admin_screen_paths():
            resp = self.client.get(path, follow_redirects=False)
            if resp.status_code == 404:
                continue  # module switched off in this composition
            location = resp.headers.get("location", "")
            if path in _LEGACY_FORWARDS:
                if resp.status_code != 308 or location != _LEGACY_FORWARDS[path]:
                    failures.append(f"{path} -> {resp.status_code} {location!r}")
                    continue
                resp = self.client.get(location, follow_redirects=False)
                location = resp.headers.get("location", "")
            if resp.status_code != 303 or not location.startswith("/admin/login"):
                failures.append(f"{path} -> {resp.status_code} {location!r}")
        self.assertEqual(failures, [], "admin screens without a login redirect")


if __name__ == "__main__":
    unittest.main()
