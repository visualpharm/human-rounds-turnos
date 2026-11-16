"""test_api_docs_schema.py — what the OpenAPI schema (`routers/api_docs.py`,
served at `/openapi.json` to an admin, written by `scripts/export_openapi.py`)
tells a reader of the API.

  - every operation is in exactly one group (tag) — the package or app module
    that owns its route — and the groups are listed with what they hold;
  - `info` says what the API is, how it signs in and how it answers errors;
  - every operation has a readable summary;
  - the two session cookies are security schemes, and each operation behind a
    staff gate or the patient's account says so, with the answers the gate gives
    (401/403 for the APIs, 303/403 for the panel's screens, 401 for the patient);
  - every `/api` operation documents the 503 of a database that does not answer;
  - every write operation documents its request body, or is listed below with
    the reason it has none; and every `/api` operation documents exactly the
    query parameters its handler reads — both checked against the handler's
    source (`_request_reads.py`), not against a list kept by hand.

The gate of the page itself is `tests/routes/test_api_docs_gate.py`. DB-less.
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (ROOT, TEST_DIR, os.path.join(ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _env  # noqa: E402
_env.process_defaults(TURNOS_ENV_FILE="/dev/null")
_env.process_defaults(DATABASE_URL="")  # boot without Postgres
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app import main as app_module  # noqa: E402
from app.core import version  # noqa: E402
from app.routers import api_docs  # noqa: E402

_METHODS = ("get", "put", "post", "delete", "patch", "head", "options")


def _operations(schema):
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if method in _METHODS:
                yield path, method, operation


class _Schema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = api_docs.build_schema(app_module.app)
        cls.ops = list(_operations(cls.schema))


class TestTags(_Schema):
    def test_every_operation_is_in_exactly_one_listed_group(self):
        names = [tag["name"] for tag in self.schema["tags"]]
        self.assertEqual(names, [name for name, _packages, _about in api_docs.TAGS])
        untagged = [(p, m) for p, m, o in self.ops if len(o.get("tags") or []) != 1]
        self.assertEqual(untagged, [])
        unknown = {o["tags"][0] for _p, _m, o in self.ops} - set(names)
        self.assertEqual(unknown, set())

    def test_every_group_is_used_and_says_what_it_holds(self):
        used = {o["tags"][0] for _p, _m, o in self.ops}
        for tag in self.schema["tags"]:
            with self.subTest(tag=tag["name"]):
                self.assertIn(tag["name"], used)
                self.assertTrue(tag["description"].strip())

    def test_an_operation_is_in_the_group_of_the_package_that_owns_it(self):
        expected = {
            ("/api/account/register", "post"): "Patient accounts",
            ("/api/requests", "post"): "Appointments",
            ("/api/chat", "post"): "Chat",
            ("/api/account/lab-results", "get"): "AI",
            ("/api/admin/login", "post"): "Staff sign-in",
            ("/api/admin/users", "get"): "Staff panel",
            ("/api/services", "get"): "Patient site",
            ("/health", "get"): "Platform",
            ("/cuenta", "get"): "Legacy URLs",
        }
        got = {(p, m): o["tags"][0] for p, m, o in self.ops if (p, m) in expected}
        self.assertEqual(got, expected)

    def test_a_package_without_a_group_is_an_error_not_an_untagged_operation(self):
        with self.assertRaises(ValueError):
            api_docs.tag_for("brand_new_package.some_router")
        self.assertEqual(api_docs.tag_for("app.core.legacy_urls_router"), "Legacy URLs")
        self.assertEqual(api_docs.tag_for("app.core.site_identity_router"), "Platform")
        self.assertEqual(api_docs.tag_for("app.modules.turnos.booking_requests_router"), "Appointments")


class TestInfoAndSummaries(_Schema):
    def test_info_says_what_the_api_is(self):
        info = self.schema["info"]
        self.assertEqual(info["title"], "Human Rounds — Turnos")
        self.assertEqual(info["version"], version.short())
        self.assertEqual(info["license"]["name"], "AGPL-3.0")
        for needle in ("st_admin", "st_ses", '{"error":', "503"):
            with self.subTest(needle=needle):
                self.assertIn(needle, info["description"])

    def test_every_operation_has_a_readable_summary(self):
        for path, method, operation in self.ops:
            summary = operation.get("summary") or ""
            with self.subTest(op=(method, path)):
                self.assertTrue(summary)
                self.assertEqual(summary, summary.strip())
                self.assertFalse(summary.endswith("Endpoint"), summary)
                self.assertFalse(summary.lower().startswith("api "), summary)

    def test_summaries_come_from_the_route_names(self):
        by_op = {(p, m): o["summary"] for p, m, o in self.ops}
        self.assertEqual(by_op[("/api/account/me", "get")], "Me")
        self.assertEqual(by_op[("/api/admin/login", "post")], "Admin login")
        # A generic factory name gives no words: the path says what it is.
        self.assertEqual(by_op[("/account", "get")], "/account")


class TestSecurityAndCommonAnswers(_Schema):
    def test_the_two_session_cookies_are_the_security_schemes(self):
        self.assertEqual(self.schema["components"]["securitySchemes"], {
            "staffSession": {"type": "apiKey", "in": "cookie", "name": "st_admin",
                             "description": self.schema["components"]["securitySchemes"]["staffSession"]["description"]},
            "patientSession": {"type": "apiKey", "in": "cookie", "name": "st_ses",
                               "description": self.schema["components"]["securitySchemes"]["patientSession"]["description"]},
        })

    def _op(self, path, method):
        return self.schema["paths"][path][method]

    def test_a_staff_api_documents_its_session_and_the_gate_answers(self):
        op = self._op("/api/admin/users", "get")
        self.assertIn({"staffSession": []}, op["security"])
        for code in ("401", "403"):
            self.assertEqual(op["responses"][code]["content"]["application/json"]["schema"],
                             {"$ref": "#/components/schemas/ApiError"})

    def test_a_staff_screen_documents_the_redirect_to_the_login(self):
        op = self._op("/admin/users", "get")
        self.assertIn({"staffSession": []}, op["security"])
        self.assertIn("303", op["responses"])
        self.assertIn("403", op["responses"])
        self.assertNotIn("401", op["responses"])

    def test_a_patient_route_documents_its_session_and_the_401(self):
        op = self._op("/api/account/update", "post")
        self.assertIn({"patientSession": []}, op["security"])
        self.assertEqual(op["responses"]["401"]["content"]["application/json"]["schema"],
                         {"$ref": "#/components/schemas/ApiError"})
        # A route an anonymous visitor may use too: the cookie is optional, no 401.
        history = self._op("/api/chat/history", "get")
        self.assertEqual(history["security"], [{}, {"patientSession": []}])
        self.assertNotIn("401", history["responses"])

    def test_every_operation_behind_a_staff_gate_documents_it(self):
        # The gate a route declares (`Depends`, on the route or its router); a
        # handler that gates by hand carries a marker instead (the probe below).
        gated = api_docs.gated_operations(app_module.app)
        self.assertGreater(len(gated["staff_api"]), 70)
        self.assertGreater(len(gated["staff_screen"]), 25)
        for kind in ("staff_api", "staff_screen"):
            for path, method in gated[kind]:
                with self.subTest(op=(method, path)):
                    self.assertIn({"staffSession": []}, self._op(path, method)["security"])

    def test_every_api_operation_documents_the_database_outage(self):
        for path, method, operation in self.ops:
            if path.startswith("/api/"):
                with self.subTest(op=(method, path)):
                    self.assertIn("503", operation["responses"])

    def test_every_reference_resolves(self):
        refs = set()

        def _walk(node):
            if isinstance(node, dict):
                if isinstance(node.get("$ref"), str):
                    refs.add(node["$ref"])
                for value in node.values():
                    _walk(value)
            elif isinstance(node, list):
                for value in node:
                    _walk(value)
        _walk(self.schema)
        known = {"#/components/schemas/" + name for name in self.schema["components"]["schemas"]}
        self.assertEqual(refs - known, set())


# Every API operation and panel screen; the chat stream is left out (it streams to an anonymous visitor).
_PROBED = ("/api/", "/admin", "/mcp")
_NOT_PROBED = {("/api/chat/stream", "post")}


class TestTheDocumentedSessionIsTheRealOne(_Schema):
    """Every staff and patient operation gets an anonymous request (offline: no database, no network).
    One that answers 401 documents a session — declared by its gate, or by the marker of a handler that
    gates by hand (`deps.STAFF_GATE_DOCS`, `deps.PATIENT_SESSION_DOCS`); one that documents a required
    session does not answer an anonymous visitor with a success."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import re
        import socket
        from unittest import mock

        import _probe_rows
        from fastapi.testclient import TestClient

        def deny(*_args, **_kwargs):
            raise OSError("network is disabled during the probe")

        cls.answers = {}
        with (
            _probe_rows.offline_environment(),
            mock.patch.object(socket.socket, "connect", new=deny),
            mock.patch.object(socket.socket, "connect_ex", new=deny),
            mock.patch("app.ops.web_runtime.start_background_services"),
            mock.patch("app.ops.web_runtime.stop_app_modules"),
            TestClient(app_module.app, client=("127.0.0.1", 12345), raise_server_exceptions=False) as client,
        ):
            for path, method, _operation in cls.ops:
                if method == "head" or not path.startswith(_PROBED) or (path, method) in _NOT_PROBED:
                    continue
                concrete = re.sub(r"\{[^}]+\}", "1", path.replace("{tipo}", "services"))
                kwargs = {"follow_redirects": False, "headers": {"Host": "testserver"}}
                if method in ("post", "put", "patch"):
                    kwargs["json"] = {}
                cls.answers[(path, method)] = client.request(method.upper(), concrete, **kwargs).status_code

    def _documented(self, path, method):
        security = self.schema["paths"][path][method].get("security") or []
        return any(requirement for requirement in security), {} in security

    def test_the_probe_reached_the_operations(self):
        self.assertGreater(len(self.answers), 200)
        self.assertGreater(sum(1 for status in self.answers.values() if status == 401), 120)

    def test_an_operation_that_answers_401_documents_its_session(self):
        undocumented = sorted((method.upper(), path) for (path, method), status in self.answers.items()
                              if status == 401 and not self._documented(path, method)[0])
        self.assertEqual(undocumented, [])

    def test_an_operation_that_documents_a_required_session_does_not_serve_an_anonymous_visitor(self):
        served = sorted((method.upper(), path, status) for (path, method), status in self.answers.items()
                        if 200 <= status < 300 and self._documented(path, method) == (True, False))
        self.assertEqual(served, [])


_WRITES = ("post", "put", "patch", "delete")

# Why a write operation documents no request body. The first two read none — checked: the walk of the
# handler must not find a body read for that method, nor a call it cannot follow; the others read a body
# that is not this API's to describe.
_ACTION = "An action: the path and the session say everything; no body is read"
_CATCH_ALL = "The catch-all of paths no route serves: the terminal's 404/405, no body is read"
_MCP = "The MCP connection: JSON-RPC messages defined by the Model Context Protocol"
_WHATSAPP = "WhatsApp's webhook: the provider's own payload (WAHA or Meta), checked by its secret"
_SETUP = "The first-run installer: HTML forms, served only until the first admin exists"
_READS_NO_BODY = (_ACTION, _CATCH_ALL)

NO_REQUEST_BODY = {
    ("POST", "/api/account/logout"): _ACTION,
    ("POST", "/api/account/mcp/regenerate"): _ACTION,
    ("POST", "/api/account/mcp/revoke"): _ACTION,
    ("POST", "/api/account/mcp/regenerate/"): _ACTION,
    ("POST", "/api/account/mcp/revoke/"): _ACTION,
    ("DELETE", "/api/account/photo"): _ACTION,
    ("DELETE", "/api/account/lab-results/upload/{upload_id}"): _ACTION,
    ("POST", "/api/account/test-identities/random"): _ACTION,
    ("DELETE", "/api/admin/analytics/keys"): _ACTION,
    ("DELETE", "/api/admin/branding/logo"): _ACTION,
    ("DELETE", "/api/admin/content/{tipo}/{item_id}"): _ACTION,
    ("POST", "/api/admin/content/{tipo}/{item_id}/restaurar"): _ACTION,
    ("POST", "/api/admin/day-queue/changes/{req_id}/hide"): _ACTION,
    ("POST", "/api/admin/hsi-sync/resolver"): _ACTION,
    ("POST", "/api/admin/intake/alerts/{aid}/ack"): _ACTION,
    ("POST", "/api/admin/intake/door/{code}/regenerate"): _ACTION,
    ("DELETE", "/api/admin/legal/{doc}"): _ACTION,
    ("POST", "/api/admin/logout"): _ACTION,
    ("POST", "/api/admin/my-appointments/feed/rotate"): _ACTION,
    ("POST", "/api/admin/onboarding/complete"): _ACTION,
    ("POST", "/api/admin/passkeys/login-options"): _ACTION,
    ("POST", "/api/admin/passkeys/register-options"): _ACTION,
    ("DELETE", "/api/admin/patients/{dni}"): _ACTION,
    ("DELETE", "/api/admin/patients/{dni}/dependents/{dni_dep}"): _ACTION,
    ("POST", "/api/admin/patients/{dni}/temp-password"): _ACTION,
    ("DELETE", "/api/admin/prompts/{clave}"): _ACTION,
    ("POST", "/api/admin/requests/{req_id}/cancel"): _ACTION,
    ("POST", "/api/admin/requests/{req_id}/restore"): _ACTION,
    ("POST", "/api/admin/requests/{req_id}/retry-hsi"): _ACTION,
    ("DELETE", "/api/admin/roles/{slug}"): _ACTION,
    ("DELETE", "/api/admin/users/{usuario}"): _ACTION,
    ("DELETE", "/api/admin/{entity}/{eid}/photo"): _ACTION,
    ("POST", "/api/calendar/feed/regenerate"): _ACTION,
    ("POST", "/api/chat/new"): _ACTION,
    ("POST", "/api/confirm/{token}/{answer}"): _ACTION,
    ("POST", "/api/intake/case/start"): _ACTION,
    ("POST", "/api/intake/case/{token}/finish"): _ACTION,
    ("POST", "/api/intake/t/{token}/reopen"): _ACTION,
    ("DELETE", "/api/mi/foto"): _ACTION,
    ("POST", "/api/offer/{token}/accept"): _ACTION,
    ("POST", "/api/offer/{token}/reject"): _ACTION,
    ("POST", "/api/requests/{req_id}/cancel"): _ACTION,
    ("DELETE", "/api/slot-hold/{token}"): _ACTION,
    ("DELETE", "/mcp"): _ACTION,
    ("DELETE", "/mcp/"): _ACTION,
    ("PUT", "/setup"): _ACTION,
    ("DELETE", "/setup"): _ACTION,
    ("PUT", "/setup/{sub}"): _ACTION,
    ("DELETE", "/setup/{sub}"): _ACTION,
    ("POST", "/{full_path}"): _CATCH_ALL,
    ("PUT", "/{full_path}"): _CATCH_ALL,
    ("PATCH", "/{full_path}"): _CATCH_ALL,
    ("DELETE", "/{full_path}"): _CATCH_ALL,
    ("POST", "/mcp"): _MCP,
    ("POST", "/mcp/"): _MCP,
    ("POST", "/api/whatsapp/webhook"): _WHATSAPP,
    ("POST", "/api/whatsapp/webhook/"): _WHATSAPP,
    ("POST", "/setup"): _SETUP,
    ("POST", "/setup/{sub}"): _SETUP,
}


class _WithReads(_Schema):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        import _request_reads
        index = api_docs._route_index(app_module.app)
        cls.reads = {(p, m): _request_reads.reads(index[(p, m)].endpoint, m, index[(p, m)].dependant)
                     for p, m, _o in cls.ops}


class TestRequestBodies(_WithReads):
    """What a client may send: every write operation's body is in the schema, or listed with its reason."""

    def _writes(self):
        return [(m.upper(), p, o) for p, m, o in self.ops if m in _WRITES]

    def test_every_write_operation_documents_its_body_or_is_listed(self):
        undocumented = sorted((m, p) for m, p, o in self._writes()
                              if "requestBody" not in o and (m, p) not in NO_REQUEST_BODY)
        self.assertEqual(undocumented, [])
        documented_anyway = sorted((m, p) for m, p, o in self._writes()
                                   if "requestBody" in o and (m, p) in NO_REQUEST_BODY)
        self.assertEqual(documented_anyway, [])
        writes = {(m, p) for m, p, _o in self._writes()}
        self.assertEqual(sorted(set(NO_REQUEST_BODY) - writes), [], "a listed operation no longer exists")
        self.assertGreaterEqual(len(writes) - len(NO_REQUEST_BODY), 90)

    def test_a_listed_action_reads_no_body(self):
        for (method, path), reason in sorted(NO_REQUEST_BODY.items()):
            got = self.reads[(path, method.lower())]
            with self.subTest(op=(method, path)):
                if reason in _READS_NO_BODY:
                    self.assertFalse(got.body, "it reads a body: document it")
                    self.assertEqual(got.opaque, set(), "a call the walk cannot follow")
                else:
                    self.assertTrue(got.body or got.opaque, "it reads no body: it is an action")

    def test_a_documented_json_body_is_read_by_its_handler(self):
        for method, path, operation in self._writes():
            content = (operation.get("requestBody") or {}).get("content", {})
            if "application/json" in content:
                with self.subTest(op=(method, path)):
                    self.assertTrue(self.reads[(path, method.lower())].body)


class TestQueryParameters(_WithReads):
    """Every `/api` operation documents the query parameters its handler reads — no fewer, no more. When the
    walk cannot see every name (the query handed somewhere it cannot follow), the documented ones must
    include what it does see. Pages (`?lang=` on every page) are not the API and are left out."""

    def test_every_api_operation_documents_the_query_it_reads(self):
        for path, method, operation in self.ops:
            if not path.startswith("/api/"):
                continue
            got = self.reads[(path, method)]
            documented = sorted(p["name"] for p in operation.get("parameters", []) if p["in"] == "query")
            with self.subTest(op=(method.upper(), path)):
                if got.query_unknown:
                    self.assertTrue(documented, "it reads the query: document the parameters")
                    self.assertEqual(sorted(got.query - set(documented)), [])
                else:
                    self.assertEqual(documented, sorted(got.query))

    def test_a_query_parameter_is_optional_and_says_what_it_does(self):
        seen = 0
        for path, method, operation in self.ops:
            for param in operation.get("parameters", []):
                if param["in"] == "query":
                    seen += 1
                    with self.subTest(op=(method.upper(), path), param=param["name"]):
                        self.assertFalse(param["required"])
                        self.assertTrue(param["description"].strip())
                        self.assertIn(param["schema"].get("type"), ("string", "integer", "boolean"))
        self.assertGreater(seen, 60)

    def test_a_query_parameter_is_documented_once(self):
        with self.assertRaises(ValueError):
            api_docs.apply_query_docs({"paths": {"/x": {"get": {
                "parameters": [{"name": "q", "in": "query"}],
                "x-turnos-query": {"get": [{"name": "q", "description": "d", "schema": {}}]}}}}})


class TestExportScript(unittest.TestCase):
    def test_the_script_writes_the_schema_the_page_serves(self):
        import export_openapi
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "openapi.json")
            self.assertEqual(export_openapi.main(["--out", out]), 0)
            with open(out, encoding="utf-8") as f:
                written = json.load(f)
        self.assertEqual(written, json.loads(json.dumps(api_docs.build_schema(app_module.app))))


if __name__ == "__main__":
    unittest.main()
