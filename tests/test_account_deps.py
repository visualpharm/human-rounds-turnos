"""test_account_deps.py — the signed-in patient is a FastAPI dependency
(`cuentas/account_deps.py`), not a hand-written read in each route.

  - the routes that read the session first declare it in their signature:
    `current_account` (401 without one) or `optional_account` (None);
  - what is left reading `accounts.current_user` by hand in the routers is an
    inventory with its reason; a new hand-written read is red here;
  - the 401 is byte for byte the `Result` the routes built by hand, and the
    session is read once per request.

DB-less: the session read is replaced by a stub.
"""
import ast
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (ROOT, TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _env  # noqa: E402
_env.process_defaults(TURNOS_ENV_FILE="/dev/null")
_env.process_defaults(DATABASE_URL="")  # boot without Postgres
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import deps  # noqa: E402
from app.cuentas import account_deps, accounts  # noqa: E402
from app.cuentas.native_result import Result  # noqa: E402

# (path, method) -> the dependency the route declares.
DECLARED = {
    ("/api/account/dependents", "GET"): account_deps.current_account,
    ("/api/account/dependents", "POST"): account_deps.current_account,
    ("/api/account/update", "POST"): account_deps.current_account,
    ("/api/account/dependents/delete", "POST"): account_deps.current_account,
    ("/api/chat/history", "GET"): account_deps.optional_account,
    ("/api/chat/new", "POST"): account_deps.optional_account,
    # These answer the anonymous visitor themselves (their own 401 body), so
    # they take the optional account.
    ("/api/account/mcp", "GET"): account_deps.optional_account,
    ("/api/account/mcp/", "GET"): account_deps.optional_account,
    ("/api/account/mcp/regenerate", "POST"): account_deps.optional_account,
    ("/api/account/mcp/revoke", "POST"): account_deps.optional_account,
    ("/api/account/mcp/regenerate/", "POST"): account_deps.optional_account,
    ("/api/account/mcp/revoke/", "POST"): account_deps.optional_account,
    ("/api/account/lab-results", "GET"): account_deps.optional_account,
}

# The router files that still read the session by hand, how many times, and why
# a dependency would not answer the same.
HAND_READS = {
    # GET /account (and the other HTML pages of the family): a valid ?lang=
    # switch answers first, without reading the session.
    "app/cuentas/accounts_router.py": 1,
    # A chat turn: POST /api/chat/stream answers 413 to an oversized body
    # before it reads the session.
    "app/modules/chat/chat_api_router.py": 1,
    # «Ver resultados del laboratorio»: with no provider key the route answers
    # 404 before it reads the session. The upload and the delete: FastAPI's 422
    # (no file, a non-numeric id) comes before the session, and on a portal
    # installation reading the session can create or bind the account.
    "app/ai/lab_results_router.py": 3,
    # /service/<id>/book and the account redirects: the language switch and the
    # turnos module gate answer first.
    "app/patient_web/patient_site_router.py": 2,
}


def _router_files():
    for top in sorted(os.listdir(ROOT)):
        base = os.path.join(ROOT, top)
        if top in ("tests", "node_modules", "vendor", "docs", "scripts", "tools") or not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in ("node_modules", "__pycache__")]
            for name in filenames:
                if name.endswith(("_router.py", "_screen.py")):
                    yield os.path.relpath(os.path.join(dirpath, name), ROOT)


def _hand_reads(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    return sum(1 for node in ast.walk(tree)
               if isinstance(node, ast.Attribute) and node.attr == "current_user"
               and isinstance(node.value, ast.Name) and node.value.id == "accounts")


def _calls(dependant):
    out = set()
    for dep in dependant.dependencies:
        out.add(dep.call)
        out |= _calls(dep)
    return out


def _routes(routes):
    for route in routes:
        if type(route).__name__ == "_IncludedRouter":
            yield from _routes(route.original_router.routes)
        else:
            yield route


class TestRoutesDeclareTheAccount(unittest.TestCase):
    def test_the_routes_that_read_the_session_first_declare_it(self):
        from app import main as app_module
        found = {}
        for route in _routes(app_module.app.routes):
            if not isinstance(route, APIRoute):
                continue
            for method in route.methods:
                key = (route.path, method)
                if key in DECLARED and key not in found:
                    found[key] = _calls(route.dependant)
        self.assertEqual(set(found), set(DECLARED))
        for key, dependency in DECLARED.items():
            with self.subTest(route=key):
                self.assertIn(dependency, found[key])

    def test_no_router_reads_the_session_by_hand_beyond_the_inventory(self):
        reads = {rel: n for rel in _router_files() if (n := _hand_reads(rel))}
        self.assertEqual(reads, HAND_READS)

    def test_the_hand_written_session_helper_is_gone(self):
        from app.cuentas import accounts_router
        self.assertFalse(hasattr(accounts_router, "_require_session"))


class TestTheDependency(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        deps.install_gate_handlers(app)

        @app.get("/required")
        async def required(usuario: dict = Depends(account_deps.current_account)):
            return {"dni": usuario["dni"]}

        @app.get("/optional")
        async def optional(usuario: dict | None = Depends(account_deps.optional_account),
                           again: dict | None = Depends(account_deps.current_account)):
            return {"same": usuario is again}

        @app.get("/maybe")
        async def maybe(usuario: dict | None = Depends(account_deps.optional_account)):
            return {"usuario": usuario}

        self.client = self.enterContext(TestClient(app))

    def test_without_a_session_the_answer_is_the_routes_own_401_byte_for_byte(self):
        with mock.patch.object(accounts, "current_user", return_value=None):
            got = self.client.get("/required")
        want = Result.json({"error": "no_autenticado"}, status=401).to_response()
        self.assertEqual(got.status_code, 401)
        self.assertEqual(got.content, want.body)
        self.assertEqual(got.content, b'{"error": "no_autenticado"}')
        self.assertEqual([(k.lower(), v) for k, v in got.headers.items()],
                         [(k.decode(), v.decode()) for k, v in want.raw_headers])

    def test_with_a_session_the_route_gets_the_account(self):
        with mock.patch.object(accounts, "current_user", return_value={"dni": "30111222"}):
            got = self.client.get("/required")
        self.assertEqual(got.json(), {"dni": "30111222"})

    def test_the_optional_account_is_none_for_an_anonymous_visitor(self):
        with mock.patch.object(accounts, "current_user", return_value=None):
            self.assertEqual(self.client.get("/maybe").json(), {"usuario": None})

    def test_the_session_is_read_once_per_request(self):
        with mock.patch.object(accounts, "current_user", return_value={"dni": "30111222"}) as read:
            self.assertEqual(self.client.get("/optional").json(), {"same": True})
        self.assertEqual(read.call_count, 1)

    def test_the_session_cookie_is_the_documented_security_scheme(self):
        self.assertEqual(account_deps.PATIENT_SESSION.model.name, accounts.COOKIE_NAME)
        self.assertEqual(account_deps.PATIENT_SESSION.scheme_name, "patientSession")


if __name__ == "__main__":
    unittest.main()
