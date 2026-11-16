"""Public study is consent-gated, demo-only, separate from admin write guards."""
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.core import deps
from app.modules.turnos import study_router
from app.admin_panel import admin_usability_screen


class StudyRoutes(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        deps.install_gate_handlers(self.app)
        self.app.include_router(study_router.router)
        self.app.include_router(admin_usability_screen.screen_router)
        self.app.include_router(admin_usability_screen.api_router)
        self.client = TestClient(self.app)
        self.gate = patch("app.modules.turnos.study.enabled", return_value=True)
        self.gate.start()
        self.addCleanup(self.gate.stop)
        self.module = patch("app.core.app_modules.is_enabled", return_value=True)
        self.module.start()
        self.addCleanup(self.module.stop)

    def test_gate_off_every_route_is_404_before_auth_or_storage(self):
        with patch("app.modules.turnos.study.enabled", return_value=False):
            for method, path in (("GET", "/study"), ("POST", "/api/study/session"),
                ("GET", "/study/s/token"), ("POST", "/api/study/s/token/event"),
                ("POST", "/api/study/s/token/result"), ("POST", "/api/study/s/token/finish"),
                ("GET", "/admin/usability"), ("GET", "/api/admin/usability"),
                ("POST", "/api/admin/usability/analyze"), ("POST", "/api/admin/usability/findings"),
                ("POST", "/api/admin/usability/findings/1")):
                self.assertEqual(self.client.request(method, path).status_code, 404, path)

    def test_consent_required_and_public_write_not_admin_authenticated(self):
        with patch("app.modules.turnos.study_store.create_session", return_value="opaque") as create:
            self.assertEqual(self.client.post("/api/study/session", json={"role": "patient"}).status_code, 422)
            create.assert_not_called()
            result = self.client.post("/api/study/session", json={"role": "patient", "consent": True})
            self.assertEqual(result.status_code, 201)
            self.assertEqual(result.json()["url"], "/study/s/opaque")
        with patch("app.modules.turnos.study_store.add_events", return_value=1):
            self.assertEqual(self.client.post("/api/study/s/opaque/event", json={"events": []}).status_code, 200)

    def test_unknown_token_is_404_and_bad_payload_is_422(self):
        with patch("app.modules.turnos.study_store.session", return_value=None):
            self.assertEqual(self.client.get("/study/s/unknown").status_code, 404)
        with patch("app.modules.turnos.study_store.add_events", side_effect=ValueError("invalid")):
            self.assertEqual(self.client.post("/api/study/s/token/event", json={"events": []}).status_code, 422)

    def test_admin_routes_have_role_gate_and_demo_mutations_stay_closed(self):
        from app.auth import admin_auth
        from app.core import permissions
        for role in ("medico", "admisionista", "reportes"):
            with patch.object(admin_auth, "_verify_token", return_value={"usuario": "example", "rol": role}), \
                 patch.object(admin_auth, "account_security", return_value={}), \
                 patch.object(permissions, "access_for", return_value={"slug": role, "state": "ok", "permissions": frozenset()}):
                self.assertEqual(self.client.get("/api/admin/usability").status_code, 403, role)
                self.assertEqual(self.client.post("/api/admin/usability/analyze", json={}).status_code, 403, role)
        self.assertEqual(self.client.get("/api/admin/usability").status_code, 401)

    def test_demo_can_read_findings_but_only_owner_can_write(self):
        from app.auth import admin_auth
        from app.ops import demo_installation
        with patch.object(admin_auth, "_admin_user", return_value="owner"), \
             patch.object(admin_auth, "account_security", return_value={}), \
             patch.object(demo_installation, "enabled", return_value=True), \
             patch("app.modules.turnos.study_store.report", return_value={"sessions": [], "results": [], "findings": []}):
            with patch.object(admin_auth, "_verify_token", return_value={"usuario": demo_installation.demo_user(), "rol": "admin"}):
                response = self.client.get("/api/admin/usability")
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.json()["editable"])
                for path in ("/api/admin/usability/analyze", "/api/admin/usability/findings", "/api/admin/usability/findings/1"):
                    self.assertEqual(self.client.post(path, json={}).status_code, 403)
            with patch.object(admin_auth, "_verify_token", return_value={"usuario": "owner", "rol": "admin"}), \
                 patch("app.modules.turnos.study_store.run_analysis", return_value=2):
                self.assertTrue(self.client.get("/api/admin/usability").json()["editable"])
                self.assertEqual(self.client.post("/api/admin/usability/analyze", json={}).json(), {"candidates": 2})

    def test_public_study_posts_ignore_staff_cookie_and_reject_cross_origin(self):
        with patch("app.auth.admin_auth.evaluate_admin_gate", side_effect=AssertionError("public endpoint used staff gate")), \
             patch("app.modules.turnos.study_store.create_session", return_value="opaque"):
            response = self.client.post("/api/study/session", json={"role": "doctor", "consent": True},
                                        headers={"cookie": "st_admin=public-demo"})
            self.assertEqual(response.status_code, 201)
            self.assertEqual(self.client.post("/api/study/session", json={"role": "doctor", "consent": True},
                headers={"origin": "https://other.invalid"}).status_code, 403)
            self.assertEqual(self.client.post("/api/study/session", json={"role": "doctor", "consent": True},
                headers={"origin": "https://testserver", "x-forwarded-proto": "https"}).status_code, 201)

    def test_module_off_is_404_and_runner_uses_existing_demo_entries(self):
        with patch("app.core.app_modules.is_enabled", return_value=False):
            self.assertEqual(self.client.get("/study").status_code, 404)
        for role, entry in (("patient", "/"), ("doctor", "/admin/demo-doctor-access"), ("admin", "/admin/demo-access")):
            with patch("app.modules.turnos.study_store.session", return_value={"role": role, "locale": "es", "finished_at": None}), \
                 patch("app.modules.turnos.study_store.completed", return_value=[]):
                response = self.client.get("/study/s/anonymous-token")
                self.assertEqual(response.status_code, 200)
                self.assertIn(entry, response.text)
                self.assertIn("study-runner.", response.text)
                # The shared loader owns the persisted-pageshow remount and
                # the late-load deadline (static/platform/screen-boot.mjs).
                self.assertIn("screen-boot.", response.text)
                self.assertIn("bootScreen({", response.text)

    def test_disabled_study_does_not_leave_a_dead_sidebar_destination(self):
        from app.core import app_modules
        section = {"screen": {"path": "/admin/usability", "module": "app.admin_panel.admin_usability_screen",
            "router_attr": "screen_router", "requires_settings": {"TURNOS_STUDY": "1", "TURNOS_DEMO_MODE": "1"}}}
        normalized = app_modules._normalize_screen(section["screen"])
        self.assertIn("requires_settings", normalized)
        with patch("app.core.config.get", return_value="0"):
            self.assertFalse(app_modules._section_connector_available(section))
        with patch("app.core.config.get", return_value="1"):
            self.assertTrue(app_modules._section_connector_available(section))

    def test_landing_first_question_and_privacy_headers(self):
        response = self.client.get("/study")
        self.assertEqual(response.status_code, 200)
        self.assertIn("¿Quién sos?", response.text)
        self.assertIn("consent", response.text)
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
