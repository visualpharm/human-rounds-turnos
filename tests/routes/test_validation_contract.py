"""tests/routes/test_validation_contract.py — pins the answers FastAPI's OWN
request validation gives today, before the app grows a shared
`RequestValidationError` handler (TASK-24, Pydantic stage 1).

Two routes declare a typed path parameter — `DELETE
/api/account/lab-results/upload/{upload_id}` and the usability finding
update, whose module gate answers first — and one declares a typed
multipart body (`POST /api/account/lab-results/upload`). A bad value
there is answered by FastAPI's stock handler: 422 with the `{"detail": [...]}`
envelope. That is the current wire contract of those routes, so the shared
handler must leave it byte-for-byte as it is — only bodies read through
`core.api_body.read_body` get the flat `{"error": ...}` answer of their
family.
"""

import json
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
from _routes_fixture import RouteTestCase  # noqa: E402


class TestStockValidationAnswers(RouteTestCase):

    def _raw(self, method, path, data=None, headers=None):
        status, resp_headers, raw = _livehttp.call(
            self._base_url, method, path, data=data, headers=headers)
        return status, resp_headers.get("Content-Type"), raw

    def test_non_numeric_upload_id_is_the_stock_422(self):
        status, ctype, raw = self._raw("DELETE", "/api/account/lab-results/upload/abc")
        self.assertEqual(status, 422)
        self.assertEqual(ctype, "application/json")
        self.assertEqual(json.loads(raw), {"detail": [{
            "type": "int_parsing", "loc": ["path", "upload_id"],
            "msg": "Input should be a valid integer, unable to parse string as an integer",
            "input": "abc"}]})

    def test_anonymous_upload_is_a_401_before_the_body_is_read(self):
        # Security review 2026-11 (finding 17): the session answers before any multipart parsing,
        # so an anonymous request never gets its body spooled to disk. The stock 422 for a
        # signed-in request without a file is pinned in tests/test_lab_results.py.
        for data, headers in ((b"", {}),
                              (b"--x--\r\n", {"Content-Type": "multipart/form-data; boundary=x"})):
            with self.subTest(headers=headers):
                status, ctype, raw = self._raw(
                    "POST", "/api/account/lab-results/upload", data=data, headers=headers)
                self.assertEqual(status, 401)
                self.assertEqual(ctype, "application/json; charset=utf-8")
                self.assertEqual(json.loads(raw), {"error": "auth_requerida"})


if __name__ == "__main__":
    unittest.main()
