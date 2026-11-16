"""tests/routes/test_refusal_before_body.py — the routes whose own checks answer before they read the body
(`api_body.deferred()` / `api_body.lazy()`, TASK-76) refuse before the body arrives, on the live server.

Each case opens a raw HTTP/1.1 connection, announces a 100 000-byte body, sends 64 bytes of it and waits for
the answer: the refusal must come while the body is still incomplete (a route that read first would wait for
the rest and answer nothing here), with the status it always had; then the client hangs up without sending
the rest. Every one of the 12 routes has at least one case, with each refusal that needs no prepared data
(the closed interview's 409 needs one and stays with the interview's own tests).
"""

import os
import socket
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TEST_DIR)
for _p in (_ROOT, _HERE, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _livehttp import response_ceiling  # noqa: E402
from _routes_fixture import RouteTestCase, admin_cookie  # noqa: E402

from app.core import db_operators  # noqa: E402

_LENGTH = 100_000
_TOKEN = "a" * 32

# (method, path, who, status): who is "anon", "admin" or "medico".
CASES = [
    ("POST", "/api/requests", "anon", 401),
    ("POST", "/api/requests/abc/coverage", "anon", 404),
    ("POST", "/api/requests/abc/reprogramar", "anon", 404),
    ("POST", "/api/requests/abc/waitlist", "anon", 404),
    ("POST", "/api/intake/qr/" + "Z" * 80 + "/start", "anon", 404),
    ("POST", "/api/intake/t/!!/message", "anon", 404),
    ("POST", "/api/intake/t/" + _TOKEN + "/message", "anon", 404),
    ("POST", "/api/intake/case/!!/message", "anon", 404),
    ("POST", "/api/admin/content/nope", "anon", 404),
    ("POST", "/api/admin/content/services", "anon", 401),
    ("POST", "/api/admin/content/services", "medico", 403),
    ("PUT", "/api/admin/content/nope/x", "anon", 404),
    ("PUT", "/api/admin/content/services/x", "anon", 401),
    ("POST", "/api/admin/content/knowledge/nope", "anon", 404),
    ("POST", "/api/admin/content/knowledge/properties", "anon", 401),
    ("POST", "/api/admin/content/knowledge/golden", "admin", 200),
    ("POST", "/api/admin/patients", "medico", 403),
    ("POST", "/api/admin/requests/abc/attendance", "anon", 404),
    ("POST", "/api/admin/requests/1/attendance", "anon", 401),
]

# The per-IP limits the intake routes check before the body.
LIMITED = [
    ("POST", "/api/intake/qr/ABCD2345/start", "app.modules.intake.interview_patient._allowed"),
    ("POST", "/api/intake/t/" + _TOKEN + "/message", "app.modules.intake.interview_patient._allowed"),
]


def _status_before_the_body(port, method, path, headers):
    """Headers and 64 bytes of a 100 000-byte body; the status line read back, then the client hangs up."""
    sock = socket.create_connection(("127.0.0.1", port), timeout=response_ceiling())
    try:
        lines = ["%s %s HTTP/1.1" % (method, path), "Host: 127.0.0.1:%d" % port,
                 "Content-Type: application/json", "Content-Length: %d" % _LENGTH, "Connection: close"]
        lines += ["%s: %s" % kv for kv in headers.items()]
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + b'{"x":"' + b"a" * 58)
        answer = b""
        while b"\r\n" not in answer:
            chunk = sock.recv(4096)
            if not chunk:
                break
            answer += chunk
        return int(answer.split(b" ", 2)[1]) if answer.startswith(b"HTTP/1.1 ") else None
    except socket.timeout:
        return None
    finally:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:  # macOS: the server already hung up (ENOTCONN)
            pass
        sock.close()


class TestRefusalBeforeTheBody(RouteTestCase):

    def _headers(self, who):
        if who == "admin":
            return admin_cookie()
        if who == "medico":
            db_operators.operator_create("dr.body", "throwaway-12345", rol="medico", medico_id="gonzalez-juan",
                                         email="dr.body@example.com")
            return admin_cookie("dr.body", "medico")
        return {}

    def test_every_refusal_comes_while_the_body_is_incomplete(self):
        for method, path, who, status in CASES:
            with self.subTest(method=method, path=path, who=who):
                self.db.truncate_all()
                self.assertEqual(_status_before_the_body(self._port, method, path, self._headers(who)), status)

    def test_the_intake_limits_refuse_before_the_body(self):
        for method, path, limit in LIMITED:
            with self.subTest(path=path), mock.patch(limit, return_value=False):
                self.assertEqual(_status_before_the_body(self._port, method, path, {}), 429)


if __name__ == "__main__":
    unittest.main()
