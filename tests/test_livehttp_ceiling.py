"""test_livehttp_ceiling.py - the routes tier waits for an answer up to a ceiling, not a fixed 5 s.

`_livehttp.call()` (used by `_routes_fixture.RouteTestCase._request` and the routes files that talk
HTTP themselves) and `LiveServerMixin._get()` wait up to `_livehttp.response_ceiling()` for the server.
A slow answer inside the ceiling is just an answer; a server that never answers fails with `NoAnswer`,
an AssertionError that names the request and how long it waited. No database needed: a tiny stdlib
server on 127.0.0.1 plays the slow and the hung server.
"""
import http.server
import os
import sys
import threading
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _env  # noqa: E402
import _livehttp  # noqa: E402

_SLOW_S = 1.5   # slower than nothing, well inside any sane ceiling


class _Server:
    """`/slow` answers after `_SLOW_S`, `/hang` only once the test releases it, `/missing` is a 404."""

    def __init__(self):
        self.release = threading.Event()
        release = self.release

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path == "/slow":
                    threading.Event().wait(_SLOW_S)
                elif self.path == "/hang":
                    release.wait(60)
                code = 404 if self.path == "/missing" else 200
                body = b"answered " + self.path.encode()
                try:
                    self.send_response(code)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass   # the client gave up on /hang and closed its socket

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.base_url = "http://127.0.0.1:%d" % self.port
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.release.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(10)


class _MixinUser(_livehttp.LiveServerMixin):
    """`LiveServerMixin._get` without booting the app: only `_port` is needed."""

    def __init__(self, port):
        self._port = port


class TestResponseCeiling(unittest.TestCase):

    def setUp(self):
        self.server = _Server()
        self.addCleanup(self.server.close)

    def test_a_slow_answer_inside_the_ceiling_passes(self):
        status, _, body = _livehttp.call(self.server.base_url, "GET", "/slow", ceiling=30)
        self.assertEqual(status, 200)
        self.assertEqual(body, b"answered /slow")

    def test_the_default_ceiling_outlasts_the_old_five_seconds(self):
        with _env.patched_env(TURNOS_TEST_HTTP_CEILING=None):
            self.assertGreaterEqual(_livehttp.response_ceiling(), 60)
        status, _, body = _livehttp.call(self.server.base_url, "GET", "/slow")
        self.assertEqual((status, body), (200, b"answered /slow"))

    def test_a_hung_server_fails_naming_the_request_and_the_wait(self):
        with self.assertRaises(_livehttp.NoAnswer) as caught:
            _livehttp.call(self.server.base_url, "GET", "/hang", ceiling=0.5)
        message = str(caught.exception)
        self.assertIn("no answer to GET /hang within the 0.5 s ceiling", message)
        self.assertIn("waited ", message)
        self.assertIn("load average", message)
        self.assertIn("TURNOS_TEST_HTTP_CEILING", message)
        self.assertIsInstance(caught.exception, AssertionError)   # reported as a FAIL, not an ERROR

    def test_the_mixin_get_shares_the_ceiling_and_the_message(self):
        user = _MixinUser(self.server.port)
        self.assertEqual(user._get("/slow")[0], 200)
        with _env.patched_env(TURNOS_TEST_HTTP_CEILING="0.5"):
            with self.assertRaises(_livehttp.NoAnswer) as caught:
                user._get("/hang")
        self.assertIn("no answer to GET /hang within the 0.5 s ceiling", str(caught.exception))

    def test_a_non_2xx_status_is_an_answer(self):
        status, _, body = _livehttp.call(self.server.base_url, "GET", "/missing", ceiling=30)
        self.assertEqual((status, body), (404, b"answered /missing"))


class TestCeilingSetting(unittest.TestCase):

    def test_the_variable_overrides_and_nonsense_falls_back(self):
        cases = {"7": 7.0, "0.25": 0.25, "abc": 120.0, "0": 120.0, "-3": 120.0, "": 120.0}
        for raw, want in cases.items():
            with self.subTest(raw=raw), _env.patched_env(TURNOS_TEST_HTTP_CEILING=raw):
                self.assertEqual(_livehttp.response_ceiling(), want)


if __name__ == "__main__":
    unittest.main()
