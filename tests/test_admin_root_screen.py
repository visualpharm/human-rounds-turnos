"""Runtime contracts for the direct-screen ``GET /admin`` entrypoint."""

import os
import sys
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults({"TURNOS_ADMIN_SECRET": "admin-root-screen-test-secret"})

from app import main as app_module  # noqa: E402
from app.admin_panel import admin_root_screen  # noqa: E402
from app.admin_panel import admin_navigation  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.modules.turnos import booking_requests  # noqa: E402


class TestAdminRootScreen(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app_module.app, follow_redirects=False)

    def _cookie(self, role):
        return {admin_auth.COOKIE_NAME: admin_auth._mint_token(
            f"root-{role}", role,
        )}

    def test_anonymous_request_redirects_to_direct_login(self):
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/admin/login")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_roles_editor_is_a_safe_login_continuation(self):
        for target in ("/admin/roles?role=gestor_agendas", "/admin/roles?new=1"):
            with self.subTest(target=target):
                self.assertEqual(admin_navigation.target_for_role("admin", target), target)
        self.assertIsNone(admin_navigation.canonical_admin_target("/admin/roles?role=../../users"))
        self.assertIsNone(admin_navigation.canonical_admin_target("/admin/roles?new=2"))

    def test_each_staff_role_redirects_to_its_authorized_direct_screen(self):
        with patch.object(admin_auth, "account_security", return_value={
            "tiene_2fa": False,
            "2fa_deadline": None,
            "2fa_vencido": False,
        }):
            for role, target in admin_root_screen.ROLE_LANDING.items():
                with self.subTest(role=role):
                    response = self.client.get("/admin", cookies=self._cookie(role))
                    self.assertEqual(response.status_code, 307)
                    self.assertEqual(response.headers["location"], target)
                    self.assertEqual(response.headers["cache-control"], "no-store")

    def test_expired_two_factor_session_lands_on_the_repair_screen(self):
        with patch.object(admin_auth, "account_security", return_value={
            "tiene_2fa": False,
            "2fa_deadline": "2026-09-01T00:00:00Z",
            "2fa_vencido": True,
        }):
            response = self.client.get("/admin", cookies=self._cookie("admin"))
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/admin/account?setup=2fa")

    def test_unknown_role_is_denied_instead_of_guessed(self):
        with patch.object(admin_auth, "_verify_token", return_value={
            "usuario": "retired-role",
            "rol": "retired",
        }):
            response = self.client.get(
                "/admin", cookies={admin_auth.COOKIE_NAME: "fixture"},
            )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": "rol_no_autorizado"})

    def test_explicit_role_safe_next_beats_a_saved_screen(self):
        identity = {"usuario": "root-admin", "rol": "admin"}
        with patch.object(admin_navigation.admin_preferences, "get_items",
                          return_value={"nav.last_screen": "/admin/cola"}):
            target = admin_navigation.resolve_target(
                identity, "/admin/legal?doc=privacidad",
            )
        self.assertEqual(target, "/admin/legal?doc=privacidad")

    def test_saved_path_is_bare_current_role_navigation_only(self):
        identity = {"usuario": "root-reportes", "rol": "reportes"}
        bad_values = (
            "/admin/solicitudes",  # current admin-only page, wrong role
            "/admin/reportes?view=weekly",  # query state never persists
            "#reportes",  # retired hash state
            "/admin/unknown",  # removed module
        )
        for saved in bad_values:
            with self.subTest(saved=saved), patch.object(
                    admin_navigation.admin_preferences, "get_items",
                    return_value={"nav.last_screen": saved}):
                self.assertEqual(
                    admin_navigation.resolve_target(identity), "/admin/reportes",
                )

    def test_stale_module_preference_falls_back_to_current_account_navigation(self):
        identity = {"usuario": "root-admin", "rol": "admin"}
        current_nav = [{"id": "cuenta", "href": "/admin/account", "label": "Account"}]
        with patch.object(admin_navigation.admin_preferences, "get_items",
                          return_value={"nav.last_screen": "/admin/knowledge"}), patch.object(
                              admin_navigation.screen_nav, "nav_items", return_value=current_nav):
            self.assertEqual(admin_navigation.resolve_target(identity), "/admin/account")

    def test_unknown_query_and_wrong_role_next_cannot_choose_a_screen(self):
        self.assertIsNone(admin_navigation.target_for_role(
            "reportes", "/admin/solicitudes",
        ))
        self.assertIsNone(admin_navigation.target_for_role(
            "admin", "/admin/solicitudes?next=/admin/users",
        ))
        self.assertEqual(admin_navigation.target_for_role(
            "medico", "/admin/consult?interview=consult-42",
        ), "/admin/consult?interview=consult-42")

    def test_invalid_or_wrong_role_explicit_next_skips_saved_preference(self):
        identity = {"usuario": "root-reportes", "rol": "reportes"}
        with patch.object(admin_navigation.admin_preferences, "get_items",
                          return_value={"nav.last_screen": "/admin/account"}):
            self.assertEqual(
                admin_navigation.resolve_target(identity, "/admin/solicitudes"),
                "/admin/reportes",
            )
            self.assertEqual(
                admin_navigation.resolve_target(identity, "/admin/reportes?doc=x"),
                "/admin/reportes",
            )

    def test_composed_root_rejects_a_wrong_role_next_without_restoring_saved_state(self):
        """A report-only operator cannot turn a copied admin URL into a return.

        This exercises the composed ASGI boundary, not just the pure resolver:
        an explicit but unauthorized continuation must select the current
        role's default even when that account has a valid saved screen.
        """
        with patch.object(admin_navigation.admin_preferences, "get_items",
                          return_value={"nav.last_screen": "/admin/account"}), patch.object(
                              admin_auth, "account_security", return_value={
                                  "tiene_2fa": False, "2fa_deadline": None,
                                  "2fa_vencido": False,
                              }):
            response = self.client.get(
                "/admin?next=/admin/solicitudes",
                cookies=self._cookie("reportes"),
            )
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/admin/reportes")

    def test_composed_root_ignores_a_saved_path_when_its_module_or_theme_is_gone(self):
        """A current server nav, not yesterday's saved page, owns return."""
        current_nav = [{"id": "cuenta", "href": "/admin/account", "label": "Account"}]
        with patch.object(admin_navigation.admin_preferences, "get_items",
                          return_value={"nav.last_screen": "/admin/plan"}), patch.object(
                              admin_navigation.screen_nav, "nav_items", return_value=current_nav), patch.object(
                                  admin_auth, "account_security", return_value={
                                      "tiene_2fa": False, "2fa_deadline": None,
                                      "2fa_vencido": False,
                                  }):
            response = self.client.get("/admin", cookies=self._cookie("admin"))
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/admin/account")

    def test_duplicate_or_unknown_outer_query_cannot_select_or_restore_a_screen(self):
        with patch.object(admin_navigation.admin_preferences, "get_items",
                          return_value={"nav.last_screen": "/admin/account"}), patch.object(
                              admin_auth, "account_security", return_value={
                                  "tiene_2fa": False, "2fa_deadline": None,
                                  "2fa_vencido": False,
                              }):
            for query in (
                "?next=/admin/legal&next=/admin/account",
                "?next=/admin/legal&other=1",
                "?other=1",
            ):
                with self.subTest(query=query):
                    response = self.client.get(
                        "/admin" + query, cookies=self._cookie("admin"),
                    )
                    self.assertEqual(response.status_code, 307)
                    self.assertEqual(response.headers["location"], "/admin/solicitudes")

    def test_only_successful_authenticated_html_nav_is_persisted(self):
        saved = []
        with patch.object(admin_navigation.admin_preferences, "save_item",
                          side_effect=lambda *args: saved.append(args)):
            response = self.client.get(
                "/admin/solicitudes?irrelevant=1", cookies=self._cookie("admin"),
            )
            self.assertEqual(response.status_code, 200)
            denied = self.client.get(
                "/admin/solicitudes", cookies=self._cookie("reportes"),
            )
            self.assertEqual(denied.status_code, 403)
            api = self.client.get(
                "/api/admin/whoami", cookies=self._cookie("admin"),
            )
            self.assertIn(api.status_code, (200, 503))
            anonymous = self.client.get("/admin/login")
            self.assertEqual(anonymous.status_code, 200)
        self.assertEqual(saved, [(
            "root-admin", admin_navigation.NAV_LAST_SCREEN, "/admin/solicitudes",
        )])

    def test_composition_has_one_platform_owned_admin_root(self):
        flat = []
        for route in app_module.app.routes:
            if type(route).__name__ == "_IncludedRouter":
                flat.extend((getattr(candidate, "route", None) or candidate)
                            for candidate in route.effective_candidates())
            else:
                flat.append(route)
        routes = [route for route in flat
                  if getattr(route, "path", None) == "/admin"
                  and "GET" in getattr(route, "methods", set())]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].endpoint.__module__,
                         "app.admin_panel.admin_root_screen")

    def test_runtime_no_longer_exposes_the_legacy_shell_renderer(self):
        self.assertFalse(hasattr(booking_requests, "_admin_shell_response"))
        self.assertFalse(hasattr(booking_requests, "ADMIN_HTML"))

    def test_legacy_admin_static_document_is_not_public(self):
        response = self.client.get("/admin.html")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "not found"})
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("Human Rounds", response.text)


if __name__ == "__main__":
    unittest.main()
