"""test_health_ready.py — Tier 4 slice 7 (docs/roadmap/
tier4-replatform-design.md, "`/health` + `/ready` + graceful shutdown +
optional Sentry").

Covers the acceptance gate's `/ready` half through the sole native
transport. Unit tests exercise ``health.readiness()``/``health.liveness()``;
router tests use ``TestClient``; and ``LiveServer`` boots the composed
FastAPI app on a real socket.

`system_status.probe_db()` is monkeypatched (same `_fake_db`-style
isolation `test_system_status.py` already uses) so this file's assertions
don't depend on a real reachable Postgres — the ONE real-DB case
(readiness flips to unavailable when Postgres is genuinely unreachable) is
covered by config.py's public effective-env-file seam plus `DATABASE_URL`
directly instead, so that specific scenario is a genuine fault injection,
not a mock.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_health_ready.py"
"""
import json
import os
import sys
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")  # boot without Postgres
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""  # no depender del .env real del checkout corriendo esto

from app.core import health  # noqa: E402
from app.core import version  # noqa: E402
from app.routers import health as health_router  # noqa: E402
from app.auth import site_auth  # noqa: E402
from app.admin_panel import system_status  # noqa: E402
from _livehttp import LiveServer  # noqa: E402


def _get(base_url, path, headers=None):
    req = urllib.request.Request(base_url + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        with e:
            return e.code, json.loads(e.read().decode("utf-8"))


LIVE = {"status": "ok", "version": version.short()}
SHA = "17eb4f5a0c2d9e1b3f4a5c6d7e8f9a0b1c2d3e4f"
# What a deploy platform puts in the environment next to the commit: none of
# it may reach /health (responsable del producto 2026-10-23: no environment, no hosts, no error
# text).
_RUNTIME_ENV = {
    "SOURCE_COMMIT": SHA,
    "HOSTNAME": "web-7f9c-abcde",
    "RAILWAY_ENVIRONMENT": "production",
    "RAILWAY_PUBLIC_DOMAIN": "salud.example.gob.ar",
    "COOLIFY_FQDN": "pinamar.example.org",
    "DATABASE_URL": "postgresql://u:p@db.internal:5432/turnos",
}


def _assert_health_body(test, body):
    """/health is exactly {status, version}: status "ok", version the
    7-character build commit or "unknown"."""
    test.assertEqual(set(body), {"status", "version"})
    test.assertEqual(body["status"], "ok")
    v = body["version"]
    test.assertTrue(v == "unknown" or (len(v) == 7 and all(c in "0123456789abcdef" for c in v)), v)


class TestLivenessUnit(unittest.TestCase):
    def test_liveness_body(self):
        self.assertEqual(health.liveness(), LIVE)
        _assert_health_body(self, health.liveness())

    def test_version_is_the_build_commit_or_unknown(self):
        self.assertEqual(version.commit({"SOURCE_COMMIT": SHA.upper()}), SHA)
        self.assertEqual(version.commit({"SOURCE_COMMIT": "", "RAILWAY_GIT_COMMIT_SHA": SHA}), SHA)
        self.assertEqual(version.commit({}), "unknown")
        for raro in ("main", "<placeholder>", "abc", "x" * 41):
            self.assertEqual(version.commit({"SOURCE_COMMIT": raro}), "unknown")
            self.assertEqual(version.short({"SOURCE_COMMIT": raro}), "unknown")
        self.assertEqual(version.short({"SOURCE_COMMIT": SHA}), "17eb4f5")
        self.assertEqual(version.short({"COOLIFY_GIT_COMMIT_SHA": "17eb4f5"}), "17eb4f5")

    def test_health_is_exactly_status_and_short_commit(self):
        with _env.patched_env(_RUNTIME_ENV):
            body = health.liveness()
        self.assertEqual(body, {"status": "ok", "version": "17eb4f5"})
        _assert_health_body(self, body)
        texto = json.dumps(body)
        for valor in _RUNTIME_ENV.values():
            if valor != SHA:
                self.assertNotIn(valor, texto)
        self.assertNotIn(SHA, texto)  # the short form only

    def test_health_without_a_commit_says_unknown(self):
        with _env.patched_env({"SOURCE_COMMIT": "", "COOLIFY_GIT_COMMIT_SHA": "",
                                     "RAILWAY_GIT_COMMIT_SHA": ""}):
            self.assertEqual(health.liveness(), {"status": "ok", "version": "unknown"})


class TestReadinessUnit(unittest.TestCase):
    def test_ok_when_db_healthy(self):
        with patch.object(system_status, "probe_db", return_value=(True, True, True, None)):
            ok, body = health.readiness()
        self.assertTrue(ok)
        self.assertEqual(body, {"status": "ok"})

    def test_unavailable_when_db_unreachable(self):
        with patch.object(system_status, "probe_db",
                           return_value=(False, False, False, "sin conexión")):
            ok, body = health.readiness()
        self.assertFalse(ok)
        self.assertEqual(body["status"], "unavailable")
        self.assertIn("sin conexión", body["detail"])

    def test_unavailable_when_schema_missing(self):
        with patch.object(system_status, "probe_db",
                           return_value=(True, False, False, "La tabla 'booking_requests' no existe.")):
            ok, body = health.readiness()
        self.assertFalse(ok)

    def test_unavailable_when_migrations_pending(self):
        with patch.object(system_status, "probe_db",
                           return_value=(True, True, False, "Migraciones pendientes (actual=x, head=y).")):
            ok, body = health.readiness()
        self.assertFalse(ok)


class TestNativeDispatch(unittest.TestCase):
    """The router is the sole HTTP transport for liveness/readiness."""

    def setUp(self):
        app = FastAPI()
        app.include_router(health_router.router)
        self.client = TestClient(app)

    def test_ignores_other_methods(self):
        self.assertEqual(self.client.post("/health").status_code, 405)

    def test_ignores_other_paths(self):
        self.assertEqual(self.client.get("/nope").status_code, 404)

    def test_health_answers_directly(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), LIVE)

    def test_ready_answers_503_when_unhealthy(self):
        with patch.object(system_status, "probe_db",
                           return_value=(False, False, False, "boom")):
            response = self.client.get("/ready")
        self.assertEqual(response.status_code, 503)

    def test_slow_endpoint_absent_without_env_flag(self):
        self.assertEqual(self.client.get("/__test/slow").status_code, 404)


class TestSiteAuthExemption(unittest.TestCase):
    """/health and /ready must be reachable without the shared Basic Auth
    credential -- see site_auth.py's _EXENTAS docstring for why."""

    def test_health_and_ready_are_exempt_paths(self):
        self.assertIn("/health", site_auth._EXENTAS)
        self.assertIn("/ready", site_auth._EXENTAS)

    def test_policy_bypasses_gate_even_with_credentials_configured(self):
        _env.set_env(self, TURNOS_BASIC_AUTH_USER="u", TURNOS_BASIC_AUTH_PASS="p")
        for path in ("/health", "/ready"):
            self.assertTrue(site_auth.authorize(path, ""),
                            f"{path} should bypass the gate")


class TestNativeLive(unittest.TestCase):
    """The composed FastAPI app over a real socket."""

    def setUp(self):
        self._live = LiveServer()
        self.base_url = self._live.__enter__()

    def tearDown(self):
        self._live.__exit__(None, None, None)

    def test_health_ok_no_db_needed(self):
        # probe_db is never called for /health -- liveness only.
        status, body = _get(self.base_url, "/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, LIVE)

    def test_ready_reflects_probe_db_ok(self):
        with patch.object(system_status, "probe_db", return_value=(True, True, True, None)):
            status, body = _get(self.base_url, "/ready")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_ready_flips_unhealthy_when_db_unreachable(self):
        with patch.object(system_status, "probe_db",
                           return_value=(False, False, False, "no reachable")):
            status, body = _get(self.base_url, "/ready")
        self.assertEqual(status, 503)
        self.assertEqual(body["status"], "unavailable")

    def test_health_reachable_behind_prelaunch_gate(self):
        _env.set_env(self, TURNOS_BASIC_AUTH_USER="u", TURNOS_BASIC_AUTH_PASS="p")
        status, body = _get(self.base_url, "/health")
        self.assertEqual(status, 200, "a healthcheck must not need the shared site credential")


class TestFaultInjectionRealDb(unittest.TestCase):
    """A genuinely unreachable Postgres (no mock) — config.ENV_PATH pointed
    at nothing and DATABASE_URL cleared, matching the design gate's own
    wording: '/ready correctly flips to unhealthy when Postgres is
    unreachable (fault-injection test)'."""

    def test_ready_503_with_no_database_url_anywhere(self):
        self.addCleanup(config._reset_cache_para_tests)  # registered first: runs once the env is back
        _env.set_env(self, TURNOS_ENV_FILE=None, DATABASE_URL="")
        self.addCleanup(setattr, config, "ENV_PATH", config.ENV_PATH)
        config.ENV_PATH = "/nonexistent/.env"
        config._reset_cache_para_tests()
        ok, body = health.readiness()
        self.assertFalse(ok)
        self.assertEqual(body["status"], "unavailable")


class TestComposedAppFastAPI(unittest.TestCase):
    """``app.py``'s native health routes in-process via TestClient."""

    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient
        from app import main as app_module
        cls.client = TestClient(app_module.app)

    def test_health_ok(self):
        r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), LIVE)

    def test_health_route_answers_exactly_status_and_short_commit(self):
        with _env.patched_env(_RUNTIME_ENV):
            r = self.client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok", "version": "17eb4f5"})
        _assert_health_body(self, r.json())

    def test_ready_ok_when_db_healthy(self):
        with patch.object(system_status, "probe_db", return_value=(True, True, True, None)):
            r = self.client.get("/ready")
        self.assertEqual(r.status_code, 200)

    def test_ready_503_when_db_unreachable(self):
        with patch.object(system_status, "probe_db",
                           return_value=(False, False, False, "no reachable")):
            r = self.client.get("/ready")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["status"], "unavailable")

    def test_health_head_ok(self):
        # Tier 4 slice 8 (orchestrator finding): `@app.get("/health")` only
        # registered GET -- FastAPI's APIRoute (unlike Starlette's plain
        # Route) does NOT auto-add HEAD to a GET-only route, so a HEAD
        # FastAPI's APIRoute does not auto-add HEAD to a GET-only route.
        # `app.py` therefore declares `methods=["GET", "HEAD"]` explicitly.
        r = self.client.head("/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"")  # HEAD never carries a body

    def test_ready_head_ok_when_db_healthy(self):
        with patch.object(system_status, "probe_db", return_value=(True, True, True, None)):
            r = self.client.head("/ready")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"")

    def test_ready_head_503_when_db_unreachable(self):
        with patch.object(system_status, "probe_db",
                           return_value=(False, False, False, "no reachable")):
            r = self.client.head("/ready")
        self.assertEqual(r.status_code, 503)


if __name__ == "__main__":
    unittest.main()
