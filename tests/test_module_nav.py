"""test_module_nav.py — Tier 2 module-contract framework (2026-08-05):
GET /api/nav (new, public, platform-core — enabled modules' nav_entries,
i18n-resolved, merged and ordered) and the v2 fields GET /api/admin/modules
now carries per module (status/error/requires/admin_sections in their full
v2 shape). config.guardar()/_db_overrides() are monkeypatched to an
in-memory dict (same pattern test_app_modules.py/test_module_runtime.py
use) so this runs without a reachable DATABASE_URL.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_module_nav.py"
"""
import os
import sys
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
# The admin-modules tests mint a session cookie, signed with the admin secret;
# the suite used to get one from whichever module happened to be imported first.
_env.process_defaults({"TURNOS_ADMIN_SECRET": "test-only-module-nav-signing-key"})
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
from app.core import app_modules  # noqa: E402
from app.core import app_modules_router  # noqa: E402
from app.core import i18n  # noqa: E402


def _client():
    app = FastAPI()
    app.include_router(app_modules_router.router)
    return TestClient(app)


class _FakeConfigStore(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self._store = {}

        def fake_overrides():
            return dict(self._store)

        self._patch = patch.object(config, "_db_overrides", side_effect=fake_overrides)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        config._reset_cache_para_tests()

    def _set_enabled(self, raw):
        self._store["ENABLED_MODULES"] = raw
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0


class TestNavEndpoint(_FakeConfigStore):
    def test_is_public_no_auth_required(self):
        with _client() as client:
            response = client.get("/api/nav")
        self.assertEqual(response.status_code, 200)

    def test_reflects_turnos_nav_entries_when_enabled(self):
        self._set_enabled("turnos,intake")
        with _client() as client:
            payload = client.get("/api/nav").json()
        paths = [e["path"] for e in payload["nav_entries"]]
        self.assertIn("/doctors", paths)
        self.assertIn("/centers", paths)

    def test_disabling_turnos_drops_its_nav_entries(self):
        self._set_enabled("nothing-real")
        with _client() as client:
            payload = client.get("/api/nav").json()
        self.assertEqual(payload["nav_entries"], [])

    def test_entries_are_ordered_by_the_manifest_order_field(self):
        self._set_enabled("turnos,intake")
        with _client() as client:
            payload = client.get("/api/nav").json()
        paths = [e["path"] for e in payload["nav_entries"]]
        # turnos's manifest declares /doctors (order 10) before /centers
        # (order 30) — ordering is a real, tested contract, not incidental
        # dict/list order.
        self.assertLess(paths.index("/doctors"), paths.index("/centers"))

    def test_labels_resolve_through_i18n_for_the_bound_language(self):
        self._set_enabled("turnos,intake")
        lang, token = i18n.bind_lang("en")
        try:
            with _client() as client:
                payload = client.get("/api/nav").json()
        finally:
            i18n.unbind(token)
        doctors = next(e for e in payload["nav_entries"] if e["path"] == "/doctors")
        self.assertEqual(doctors["label"], "Doctors")

    def test_my_requests_carries_the_account_tab_name(self):
        self._set_enabled("turnos,intake")
        with _client() as client:
            payload = client.get("/api/nav").json()
        # /my-requests is the account's «Mis turnos» tab (2026-11-05): one
        # name for the same screen everywhere, from the same catalog key.
        mis = next(e for e in payload["nav_entries"] if e["path"] == "/my-requests")
        self.assertEqual(mis["label"], "Mis turnos")

    def test_only_get_is_handled(self):
        with _client() as client:
            response = client.post("/api/nav")
        self.assertEqual(response.status_code, 405)

    def test_unknown_path_is_not_handled(self):
        with _client() as client:
            response = client.get("/api/navvy")
        self.assertEqual(response.status_code, 404)


class TestAdminModulesV2Fields(_FakeConfigStore):
    def _fetch(self):
        from app.auth import admin_auth
        with _client() as client:
            response = client.get(
                "/api/admin/modules",
                cookies={"st_admin": admin_auth._mint_token("admin1", "admin")},
            )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_each_module_carries_status_error_requires(self):
        data = self._fetch()
        turnos = next(m for m in data["modules"] if m["id"] == "turnos")
        intake = next(m for m in data["modules"] if m["id"] == "intake")
        self.assertEqual(turnos["status"], "enabled")
        self.assertIsNone(turnos["error"])
        self.assertEqual(turnos["requires"], [])
        # Codex monitor review (P0, 2026-08-05): intake's `requires` is real
        # now — modules/intake/module.json declares `["turnos"]` for good,
        # no longer deferred. The dependency-refusal machinery is proven
        # BOTH against a synthetic fixture (test_module_runtime.py's
        # TestDependencyRefusalWithReason) and this real pair
        # (test_app_modules.py's TestEnableDisable,
        # test_module_lifecycle.py's TestRealIntakeTurnosEdgeHttp).
        # Tier 2 batch F (2026-08-07): "chat" joined the list —
        # interview_agent.py imports modules.chat.chat_language, a real
        # physical dependency now that chat_language.py lives inside a
        # disable-able module (see docs/architecture/chat-intake-manifests.md, Requires).
        self.assertEqual(intake["requires"], ["turnos", "chat"])

    def test_admin_sections_are_the_full_v2_dict_shape(self):
        data = self._fetch()
        turnos = next(m for m in data["modules"] if m["id"] == "turnos")
        self.assertTrue(turnos["admin_sections"])
        section = turnos["admin_sections"][0]
        for key in ("id", "label_key", "label", "roles", "js"):
            self.assertIn(key, section)

    def test_a_disabled_module_reports_status_disabled(self):
        self._set_enabled("turnos")
        data = self._fetch()
        intake = next(m for m in data["modules"] if m["id"] == "intake")
        self.assertEqual(intake["status"], "disabled")
        self.assertFalse(intake["enabled"])


if __name__ == "__main__":
    unittest.main()
