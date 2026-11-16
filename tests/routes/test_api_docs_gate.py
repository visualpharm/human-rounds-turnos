"""tests/routes/test_api_docs_gate.py — `/docs` and `/openapi.json` answer
exactly what the native terminal answers to anyone who is not a signed-in
admin (TASK-24, Pydantic stage 1).

Before the API documentation existed these paths fell through to
`routers/native_terminal.py`: both are the HTML 404 page (`/docs` is the repo's
`docs/` directory, which the static allowlist does not serve — it was a 403
until 2026-11-12, tests/test_static_allowlist.py). The documentation routes must not change that for an
anonymous visitor, a patient or any staff role short of admin — the paths do
not even reveal that documentation exists.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TEST_DIR)
for _p in (_ROOT, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _livehttp  # noqa: E402
from _routes_fixture import RouteTestCase, admin_cookie  # noqa: E402

_SAME_AS_TERMINAL_HEADERS = ("Content-Type", "Content-Length", "Cache-Control")


class TestApiDocsHiddenFromNonAdmins(RouteTestCase):

    def _raw(self, method, path, headers=None, data=None):
        return _livehttp.call(self._base_url, method, path, data=data, headers=headers)

    def _assert_same(self, got, want):
        self.assertEqual(got[0], want[0])
        for name in _SAME_AS_TERMINAL_HEADERS:
            self.assertEqual(got[1].get(name), want[1].get(name), name)
        self.assertEqual(got[2], want[2])

    def _visitors(self):
        return {
            "anonymous": {},
            "medico": admin_cookie(usuario="docs.medico", rol="medico"),
            "admisionista": admin_cookie(usuario="docs.admision", rol="admisionista"),
            "forged cookie": {"Cookie": "st_admin=not-a-token"},
        }

    def test_docs_is_the_html_404_page(self):
        # `docs/` is not on the static allowlist (tests/test_static_allowlist.py): the
        # terminal answers the same 404 page as for a path that does not exist.
        for who, headers in self._visitors().items():
            for path in ("/docs", "/docs/"):
                with self.subTest(who=who, path=path):
                    got = self._raw("GET", path, headers)
                    want = self._raw("GET", "/docs-no-such-page", headers)
                    self.assertEqual(got[0], 404)
                    self.assertEqual(got[1].get("Content-Type"), "text/html; charset=utf-8")
                    self._assert_same(got, want)

    def test_openapi_json_is_the_html_404_page(self):
        for who, headers in self._visitors().items():
            with self.subTest(who=who):
                got = self._raw("GET", "/openapi.json", headers)
                want = self._raw("GET", "/openapi-no-such-page.json", headers)
                self.assertEqual(got[0], 404)
                self.assertEqual(got[1].get("Content-Type"), "text/html; charset=utf-8")
                self._assert_same(got, want)

    def test_other_methods_still_reach_the_terminal(self):
        for path in ("/docs", "/openapi.json"):
            for method in ("POST", "PUT", "DELETE"):
                with self.subTest(path=path, method=method):
                    status, headers, body = self._raw(method, path, admin_cookie(), data=b"{}")
                    self.assertEqual(status, 404)
                    self.assertEqual(body, b'{"error": "not found"}')


class TestApiDocsForAdmin(RouteTestCase):

    def _raw(self, path):
        return _livehttp.call(self._base_url, "GET", path, headers=admin_cookie())

    def test_openapi_json_documents_the_app(self):
        import json
        status, headers, raw = self._raw("/openapi.json")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "application/json; charset=utf-8")
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        schema = json.loads(raw)
        self.assertEqual(schema["info"]["title"], "Human Rounds — Turnos")
        self.assertIn("post", schema["paths"]["/api/account/register"])
        self.assertIn("get", schema["paths"]["/api/requests"])
        self.assertIn("post", schema["paths"]["/api/requests"])
        # The documentation routes do not document themselves.
        self.assertNotIn("/openapi.json", schema["paths"])
        self.assertNotIn("/docs", schema["paths"])

    def test_every_operation_id_is_unique_and_names_its_method(self):
        import json
        _, _, raw = self._raw("/openapi.json")
        ids = []
        for path, item in json.loads(raw)["paths"].items():
            for method, operation in item.items():
                ids.append(operation["operationId"])
                self.assertTrue(operation["operationId"].endswith("_" + method),
                                (path, method, operation["operationId"]))
        self.assertEqual(len(ids), len(set(ids)))
        # One endpoint on two methods: two ids, one per method.
        _, _, raw = self._raw("/openapi.json")
        requests = json.loads(raw)["paths"]["/api/requests"]
        self.assertNotEqual(requests["get"]["operationId"], requests["post"]["operationId"])

    def test_documented_bodies_and_answers_are_in_the_schema(self):
        import json
        _, _, raw = self._raw("/openapi.json")
        schema = json.loads(raw)
        register = schema["paths"]["/api/account/register"]["post"]
        self.assertEqual(
            register["requestBody"]["content"]["application/json"]["schema"],
            {"$ref": "#/components/schemas/RegisterIn"})
        self.assertEqual(
            register["responses"]["200"]["content"]["application/json"]["schema"],
            {"$ref": "#/components/schemas/AccountOut"})
        self.assertEqual(
            register["responses"]["400"]["content"]["application/json"]["schema"],
            {"$ref": "#/components/schemas/ApiError"})
        self.assertNotIn("x-turnos-docs", register)
        # One route object, two methods: only the POST takes the body.
        dependents = schema["paths"]["/api/account/dependents"]
        self.assertIn("requestBody", dependents["post"])
        self.assertNotIn("requestBody", dependents["get"])
        components = schema["components"]["schemas"]
        for name in ("RegisterIn", "AccountOut", "AccountUser", "CoverageIn", "ApiError"):
            self.assertIn(name, components)
        # Every reference resolves.
        refs = set()

        def _walk(node):
            if isinstance(node, dict):
                if isinstance(node.get("$ref"), str):
                    refs.add(node["$ref"].rsplit("/", 1)[1])
                for v in node.values():
                    _walk(v)
            elif isinstance(node, list):
                for v in node:
                    _walk(v)
        _walk(schema)
        self.assertEqual(refs - set(components), set())

    def test_docs_is_swagger_ui_pinned_with_integrity(self):
        status, headers, raw = self._raw("/docs")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "text/html; charset=utf-8")
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        html = raw.decode("utf-8")
        self.assertIn('url: "/openapi.json"', html)
        self.assertIn("swagger-ui-dist@5.34.0/swagger-ui-bundle.js", html)
        self.assertEqual(html.count('integrity="sha384-'), 2)

    def test_docs_with_a_trailing_slash_is_still_the_terminal(self):
        status, _, raw = self._raw("/docs/")
        self.assertEqual(status, 404)
        self.assertEqual(raw, self._raw("/docs-no-such-page")[2])


if __name__ == "__main__":
    unittest.main()
