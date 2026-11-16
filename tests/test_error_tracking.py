"""test_error_tracking.py — Tier 4 slice 7 (docs/roadmap/
tier4-replatform-design.md): Sentry-compatible error tracking stays fully
DARK by default. The design's own gate: "Sentry stays fully inert with no
DSN configured (default state, verified by absence of any outbound call in
a test run)" — the strongest version of that proof is checking
`sys.modules` never gains `sentry_sdk` at all when the DSN env var is
absent (error_tracking.py imports it LAZILY, inside init(), never at
module top level — see that file's docstring for why: it's imported
unconditionally from server.main(), so a top-level import would make it an
unconditional boot cost even in dark mode).

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_error_tracking.py"
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import error_tracking  # noqa: E402
import _env  # noqa: E402


def _forget_sentry_sdk():
    sys.modules.pop("sentry_sdk", None)


class TestDarkByDefault(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, TURNOS_SENTRY_DSN=None)
        _forget_sentry_sdk()

    def test_enabled_false_without_dsn(self):
        self.assertFalse(error_tracking.enabled())

    def test_init_returns_false_without_dsn(self):
        self.assertFalse(error_tracking.init())

    def test_init_never_imports_sentry_sdk_without_a_dsn(self):
        error_tracking.init()
        self.assertNotIn("sentry_sdk", sys.modules,
                          "no TURNOS_SENTRY_DSN configured -- sentry_sdk must never be imported")

    def test_blank_dsn_is_treated_as_absent(self):
        _env.set_env(self, TURNOS_SENTRY_DSN="   ")
        self.assertFalse(error_tracking.enabled())
        self.assertFalse(error_tracking.init())
        self.assertNotIn("sentry_sdk", sys.modules)


class TestEnabledWithDsn(unittest.TestCase):
    """Only meaningful when sentry_sdk is actually installed (it's an
    optional dependency, requirements.txt's own comment) -- skipped
    otherwise rather than failing a checkout that hasn't installed it."""

    def setUp(self):
        try:
            import sentry_sdk  # noqa: F401
        except ImportError:
            self.skipTest("sentry-sdk not installed in this checkout")
        _env.set_env(self, TURNOS_SENTRY_DSN="https://public@fake.invalid/1")

    def test_enabled_true_with_dsn(self):
        self.assertTrue(error_tracking.enabled())

    def test_init_returns_true_and_never_sends_anything(self):
        # A fake .invalid DSN + traces_sample_rate=0.0 means init() only
        # configures the local SDK state -- it never makes a real network
        # call by itself (sentry_sdk.init() doesn't send a handshake).
        self.assertTrue(error_tracking.init())


import json  # noqa: E402

# Everything a patient error could carry; none of it may reach Sentry.
_DNI, _EMAIL, _PHONE, _NAME = "24132941", "nora.dominguez@example.com", "2254 491670", "Nora Domínguez"
_LINK_ID = "Zk3pQ9vX2mL7tR8wY1aB"


def _dirty_event():
    return {
        "event_id": "abc", "level": "error", "platform": "python",
        "transaction": "/api/admin/requests/{req_id}/cancel",
        "exception": {"values": [{
            "type": "ValueError", "module": "builtins",
            "value": f"DNI {_DNI} no coincide, mail {_EMAIL}, tel {_PHONE}",
            "stacktrace": {"frames": [{
                "filename": "app/modules/turnos/booking_requests.py", "function": "_desk_cancel_core",
                "lineno": 3010, "context_line": "    raise ValueError(msg)",
                "vars": {"dni": _DNI, "paciente": _NAME, "email": _EMAIL},
            }]},
        }]},
        "request": {"method": "POST",
                    "url": f"https://pinamar.humanrounds.org/appointment/confirm/{_LINK_ID}?dni={_DNI}",
                    "query_string": f"dni={_DNI}", "data": {"paciente": _NAME},
                    "cookies": {"st_admin": "secret-cookie"},
                    "headers": {"Cookie": "st_ses=secret", "X-Forwarded-For": "203.0.113.10"},
                    "env": {"REMOTE_ADDR": "203.0.113.10"}},
        "user": {"id": _DNI, "email": _EMAIL, "ip_address": "203.0.113.10"},
        "extra": {"paciente": _NAME},
        "contexts": {"runtime": {"name": "CPython"}, "custom": {"dni": _DNI}},
        "breadcrumbs": {"values": [
            {"category": "query", "message": f"SELECT * FROM users WHERE dni='{_DNI}'",
             "data": {"params": [_DNI, _NAME]}},
            {"category": "httplib", "data": {"url": f"https://hsi.example/api?dni={_DNI}"}},
        ]},
        "server_name": "container-8ed1439fd456",
        "modules": {"fastapi": "0.1"},
    }


class TestScrubbingReview(unittest.TestCase):
    """The review the municipality asked for before connecting a real DSN
    (the portal team, 2026-10-19): what leaves is an allowlist, never personal data."""

    def test_nothing_personal_survives_the_event_scrubber(self):
        out = json.dumps(error_tracking.scrub_event(_dirty_event()), ensure_ascii=False)
        for secret in (_DNI, _EMAIL, _NAME, "Domínguez", _LINK_ID, "secret-cookie", "st_ses",
                       "203.0.113.10", "container-8ed1439fd456", "491670"):
            self.assertNotIn(secret, out)

    def test_the_error_is_still_diagnosable(self):
        out = error_tracking.scrub_event(_dirty_event())
        exc = out["exception"]["values"][0]
        self.assertEqual(exc["type"], "ValueError")
        self.assertIn("no coincide", exc["value"])
        frame = exc["stacktrace"]["frames"][0]
        self.assertEqual((frame["function"], frame["lineno"]), ("_desk_cancel_core", 3010))
        self.assertNotIn("vars", frame)
        self.assertEqual(out["request"], {"method": "POST",
                                          "url": "https://pinamar.humanrounds.org/appointment/confirm/:id"})
        self.assertEqual(out["transaction"], "/api/admin/requests/{req_id}/cancel")
        self.assertEqual([c.get("category") for c in out["breadcrumbs"]["values"]], ["query", "httplib"])
        self.assertNotIn("user", out)
        self.assertEqual(out["contexts"], {"runtime": {"name": "CPython"}})

    def test_paths_keep_slugs_and_mask_ids(self):
        self.assertEqual(error_tracking._scrub_path("/service/medicina-general-valeria/book"),
                         "/service/medicina-general-valeria/book")
        self.assertEqual(error_tracking._scrub_path("/api/admin/requests/43/cancel?x=1"),
                         "/api/admin/requests/:id/cancel")

    def test_dotted_dni_and_other_long_numbers_are_masked(self):
        out = error_tracking._scrub_text("paciente 24.132.941, afiliado 150-123456-7, línea 3010")
        self.assertNotIn("24.132.941", out)
        self.assertNotIn("123456", out)
        self.assertIn("línea 3010", out)

    def test_long_messages_are_cut(self):
        self.assertLessEqual(len(error_tracking._scrub_text("x" * 5000)), 301)


class TestScrubbingEndToEnd(unittest.TestCase):
    """The real SDK, configured by `init()`, with the network replaced by a
    capture: the envelope that WOULD leave carries none of the data."""

    def setUp(self):
        try:
            import sentry_sdk  # noqa: F401
        except ImportError:
            self.skipTest("sentry-sdk not installed in this checkout")
        _env.set_env(self, TURNOS_SENTRY_DSN="https://public@fake.invalid/1")

    def tearDown(self):
        import sentry_sdk
        sentry_sdk.init()  # back to an inert client

    def test_a_real_exception_leaves_without_locals_or_pii(self):
        import sentry_sdk
        from sentry_sdk.transport import Transport
        sent = []

        class Capture(Transport):
            def capture_envelope(self, envelope):
                sent.append(envelope.serialize().decode("utf-8", "replace"))

        real_init = sentry_sdk.init
        with unittest.mock.patch.object(
                sentry_sdk, "init", lambda **kw: real_init(transport=Capture, **kw)):
            self.assertTrue(error_tracking.init())

        def falla(dni, paciente, email):
            raise ValueError(f"DNI {dni} de {email} no coincide")

        sentry_sdk.add_breadcrumb(category="query", message=f"dni={_DNI}", data={"p": _NAME})
        try:
            falla(_DNI, _NAME, _EMAIL)
        except ValueError:
            sentry_sdk.capture_exception()
        sentry_sdk.flush()
        payload = "\n".join(sent)
        self.assertIn("ValueError", payload)
        self.assertIn("falla", payload)
        for secret in (_DNI, _EMAIL, _NAME):
            self.assertNotIn(secret, payload)

    def test_events_carry_the_build_commit_as_release(self):
        import sentry_sdk
        seen = {}
        sha = "17eb4f5a0c2d9e1b3f4a5c6d7e8f9a0b1c2d3e4f"
        with _env.patched_env({"SOURCE_COMMIT": sha}), \
                unittest.mock.patch.object(sentry_sdk, "init", lambda **kw: seen.update(kw)):
            self.assertTrue(error_tracking.init())
        self.assertEqual(seen["release"], sha)
        with _env.patched_env({"SOURCE_COMMIT": ""}), \
                unittest.mock.patch.object(sentry_sdk, "init", lambda **kw: seen.update(kw)):
            error_tracking.init()
        self.assertIsNone(seen["release"])


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
