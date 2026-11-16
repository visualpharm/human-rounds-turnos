"""test_module_contract_asgi.py — Tier 4 slice 3 (docs/roadmap/
tier4-replatform-design.md, "Module contract adapter (slice 3)"): the ASGI
equivalent of test_module_runtime.py's TestIntakeModuleGating — the SAME
enable/disable/404 acceptance property, proven through the NEW mechanism
(a FastAPI `Depends(require_module_enabled(...))` gate on a router mounted
once and for all, never route re-registration) instead of through
`routing.dispatch()`'s `app_modules.route_enabled()` check.

Uses `fastapi.testclient.TestClient` against the real `app.app` instance
(in-process, no socket — the "genuine simplification over the existing
worker-DB-pool socket dance" the design's own "Test harness extension"
section names for a route that's already migrated) instead of
`tests/_livehttp.py`'s `LiveServer`, because there's no legacy socket in
this picture at all: every scenario below is served by `app.app` alone,
never proxied to a real legacy process. Same `config._db_overrides`
monkeypatch pattern test_module_runtime.py already uses, so
`ENABLED_MODULES` can be flipped mid-test with no reachable Postgres and no
process restart — proving "no restart needed" for the ASGI path too.

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_module_contract_asgi.py"
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")  # boot without Postgres
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="module-contract-asgi-fixture-pass")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""  # no depender del .env real del checkout corriendo esto

from app import main as app_module  # noqa: E402
from app.core import app_modules  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _enabled_setup(test_case):
    """Same config._db_overrides monkeypatch test_module_runtime.py uses —
    lets ENABLED_MODULES flip mid-test with no Postgres reachable."""
    config._reset_cache_para_tests()
    store = {}

    def fake_overrides():
        return dict(store)

    patcher = patch.object(config, "_db_overrides", side_effect=fake_overrides)
    patcher.start()
    test_case.addCleanup(patcher.stop)
    test_case.addCleanup(config._reset_cache_para_tests)

    def _set_enabled(raw):
        store["ENABLED_MODULES"] = raw
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0

    return _set_enabled


class TestIntakeRouterGating(unittest.TestCase):
    """(a)/(b)/toggle from test_module_runtime.py's TestIntakeModuleGating,
    driven through app.app's router-mounted paths instead of routing.py's
    dispatch(). `app.app` mounts intake's four `_router.py` routers
    UNCONDITIONALLY at import time (module import happens once, at process
    start, same as routing.py's own GET_PLATFORM/merge_routes()) — every
    scenario below is about the PER-REQUEST `require_module_enabled` gate,
    never about whether the router itself got mounted."""

    def setUp(self):
        self._set_enabled = _enabled_setup(self)
        self.client = TestClient(app_module.app)

    def test_intake_disabled_404s_before_domain_helpers(self):
        self._set_enabled("turnos")  # intake NOT listed -> disabled
        from app.modules.intake import interview_patient, printable
        with patch.object(interview_patient, "resolve_entrevista_page") as spy_ip, \
             patch.object(printable, "_consulta_para") as spy_pr:
            self.assertEqual(self.client.get("/entrevista/1").status_code, 404)
            self.assertEqual(self.client.get("/api/intake/t/xyz").status_code, 404)
            self.assertEqual(self.client.get("/r/abc").status_code, 404)
            self.assertEqual(self.client.get("/o/abc").status_code, 404)
        # Indistinguishable from a path that never existed (the product owner,
        # 2026-08-05, docs/modules.md) — same doctrine as legacy's static
        # fallback, ported to the ASGI dependency gate.
        self.assertEqual(
            self.client.get("/entrevista/1").json(), {"detail": {"error": "no_encontrado"}})
        spy_ip.assert_not_called()
        spy_pr.assert_not_called()

    def test_intake_enabled_routes_to_the_real_domain_function(self):
        """The native interview endpoint calls its domain function directly."""
        self._set_enabled("turnos,intake")
        from app.modules.intake import interview_patient
        with patch.object(interview_patient, "resolve_entrevista_page",
                           wraps=interview_patient.resolve_entrevista_page) as spy:
            # No session cookie -> 401 from the real endpoint (never the 404
            # a disabled module or an unclaimed path would give) — same
            # expectation test_module_runtime.py's legacy-path sibling
            # test asserts for the exact same URL.
            self.assertEqual(self.client.get("/entrevista/1").status_code, 401)
        spy.assert_called()

    def test_toggling_intake_live_flips_router_gating_without_remount(self):
        self._set_enabled("turnos")
        self.assertEqual(self.client.get("/entrevista/1").status_code, 404)
        self._set_enabled("turnos,intake")
        self.assertEqual(self.client.get("/entrevista/1").status_code, 401)
        self._set_enabled("turnos")
        self.assertEqual(self.client.get("/entrevista/1").status_code, 404)

    def test_other_migrated_routers_are_unaffected_by_intakes_enablement(self):
        # Sanity: disabling intake must not collaterally break the OTHER
        # already-migrated routers from slices 1/2 (no shared state beyond
        # the FastAPI app instance itself).
        self._set_enabled("turnos")  # intake disabled
        self.assertEqual(self.client.get("/entrevista/1").status_code, 404)
        self.assertEqual(self.client.get("/").status_code, 200)  # site_identity_router (slice 2)
        self.assertEqual(self.client.get("/api/admin/whoami").status_code, 401)  # admin_auth_router (slice 1)


class TestChatRouterGating(unittest.TestCase):
    """Same molde as TestIntakeRouterGating above, for modules/chat/'s two
    router_attr files (chat_api_router.py, voice_router.py) — added as a
    fix, not part of the original Tier 2 batch F commit: an independent
    review (2026-08-07) found both routers still gated on
    `Depends(require_module_enabled("turnos"))`, a leftover from when
    chat_api.py/voice.py were turnos-owned bare root files, so disabling
    `chat` under TURNOS_ASGI=1 left every one of these paths fully
    reachable — the exact bug this class exists to catch. Zero tests
    exercised `require_module_enabled("chat")` before this class."""

    def setUp(self):
        self._set_enabled = _enabled_setup(self)
        self.client = TestClient(app_module.app)

    def test_chat_disabled_404s_before_domain_helpers(self):
        self._set_enabled("turnos")  # chat NOT listed -> disabled
        from app.modules.chat import chat_api, voice
        with patch.object(chat_api, "compute_output") as spy_ca, \
             patch.object(voice, "public_status_payload") as spy_v:
            self.assertEqual(self.client.post("/api/chat", json={"mensaje": "hola"}).status_code, 404)
            self.assertEqual(self.client.get("/api/chat/whatsapp").status_code, 404)
            self.assertEqual(self.client.get("/api/chat/history").status_code, 404)
            self.assertEqual(self.client.get("/api/chat-transcripts").status_code, 404)
            self.assertEqual(self.client.get("/api/chat/voice").status_code, 404)
            self.assertEqual(self.client.post("/api/chat/transcribe", json={}).status_code, 404)
            self.assertEqual(self.client.get("/api/admin/chat-voice").status_code, 404)
        # Indistinguishable from a path that never existed (the product owner,
        # 2026-08-05, docs/modules.md) — same doctrine TestIntakeRouterGating
        # proves for intake.
        self.assertEqual(
            self.client.get("/api/chat/voice").json(), {"detail": {"error": "no_encontrado"}})
        spy_ca.assert_not_called()
        spy_v.assert_not_called()

    def test_chat_enabled_routes_to_the_real_domain_function(self):
        self._set_enabled("turnos,chat")
        from app.modules.chat import voice
        # Native-migration campaign family W2a moved /api/chat/voice off
        # the real endpoint calls `voice.public_status_payload()` directly.
        with patch.object(voice, "public_status_payload",
                           wraps=voice.public_status_payload) as spy:
            # /api/chat/voice needs no auth ("public capability status") —
            # a clean 200 proves the router forwarded to the real handler,
            # never the 404 a disabled module or an unclaimed path would give.
            self.assertEqual(self.client.get("/api/chat/voice").status_code, 200)
        spy.assert_called()

    def test_toggling_chat_live_flips_router_gating_without_remount(self):
        self._set_enabled("turnos")
        self.assertEqual(self.client.get("/api/chat/voice").status_code, 404)
        self._set_enabled("turnos,chat")
        self.assertEqual(self.client.get("/api/chat/voice").status_code, 200)
        self._set_enabled("turnos")
        self.assertEqual(self.client.get("/api/chat/voice").status_code, 404)

    def test_other_migrated_routers_are_unaffected_by_chats_enablement(self):
        # Sanity: disabling chat must not collaterally break the OTHER
        # already-migrated routers (no shared state beyond the FastAPI app
        # instance itself).
        self._set_enabled("turnos")  # chat disabled
        self.assertEqual(self.client.get("/api/chat/voice").status_code, 404)
        self.assertEqual(self.client.get("/").status_code, 200)  # site_identity_router
        self.assertEqual(self.client.get("/api/admin/whoami").status_code, 401)  # admin_auth_router


class TestLiveEnableDisableCallsFlipRouterGating(unittest.TestCase):
    """The explicit app_modules.enable()/disable() call path (not just an
    ENABLED_MODULES config edit) — mirrors test_module_runtime.py's
    TestLiveEnableLoadsRoutesWithoutRestart, through the ASGI router
    instead of routing.py's dispatch()."""

    def setUp(self):
        config._reset_cache_para_tests()
        self._store = {}

        def fake_overrides():
            return dict(self._store)

        def fake_guardar(overrides):
            for k, v in overrides.items():
                if v in (None, ""):
                    self._store.pop(k, None)
                else:
                    self._store[k] = v
            config._db_cache["v"] = None
            config._db_cache["t"] = 0.0
            return dict(self._store)

        self._p1 = patch.object(config, "_db_overrides", side_effect=fake_overrides)
        self._p2 = patch.object(config, "guardar", side_effect=fake_guardar)
        self._p1.start()
        self._p2.start()
        self.addCleanup(self._p2.stop)
        self.addCleanup(self._p1.stop)
        self.addCleanup(config._reset_cache_para_tests)
        self.client = TestClient(app_module.app)

    def test_enable_call_brings_the_router_gate_up_live(self):
        # intake also requires "chat" since Tier 2 batch F (interview_agent.py
        # imports modules.chat.chat_language) — pre-enable it so enable("intake")
        # doesn't refuse on the unmet dependency this test isn't exercising.
        config.guardar({"ENABLED_MODULES": "turnos,chat"})
        self.assertEqual(self.client.get("/entrevista/1").status_code, 404)
        app_modules.enable("intake")
        self.assertEqual(self.client.get("/entrevista/1").status_code, 401)

    def test_disable_call_takes_the_router_gate_down_live(self):
        config.guardar({"ENABLED_MODULES": "turnos,intake"})
        self.assertEqual(self.client.get("/entrevista/1").status_code, 401)
        app_modules.disable("intake")
        self.assertEqual(self.client.get("/entrevista/1").status_code, 404)


class TestMergeAppRoutersSpliceContract(unittest.TestCase):
    """merge_app_routers()'s own splice algorithm, exercised directly
    against SYNTHETIC modules — same MODULES_DIR-swap pattern
    test_module_runtime.py's TestBrokenModuleBootSurvival uses — so the
    "deterministic order semantics where they matter" property (the
    design's own phrase) is proven for real, not just asserted true
    because intake's own paths happen not to collide with anything."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="turnos_router_order_mod_")
        self.pkgdir = tempfile.mkdtemp(prefix="turnos_router_order_pkg_")
        sys.path.insert(0, self.pkgdir)
        self._orig_modules_dir = app_modules.MODULES_DIR
        app_modules.MODULES_DIR = self.tmpdir
        app_modules._RUNTIME_BROKEN.clear()
        app_modules._invalidate_route_owner_map()

    def tearDown(self):
        app_modules.MODULES_DIR = self._orig_modules_dir
        app_modules._RUNTIME_BROKEN.clear()
        app_modules._invalidate_route_owner_map()
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        try:
            sys.path.remove(self.pkgdir)
        except ValueError:
            pass
        shutil.rmtree(self.pkgdir, ignore_errors=True)
        for name in ("_fixture_router_mod_a", "_fixture_router_mod_b"):
            sys.modules.pop(name, None)

    def _write_manifest(self, module_id, doc):
        d = os.path.join(self.tmpdir, module_id)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "module.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f)

    def _write_router_module(self, dotted_name, marker):
        """A standalone top-level module, written into a throwaway
        directory prepended to sys.path (never the real repo root) — this
        test proves `merge_app_routers()`'s ORDERING algorithm only, which
        never inspects the router object's type, so a bare string stands
        in fine for a real `fastapi.APIRouter`."""
        path = os.path.join(self.pkgdir, f"{dotted_name}.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(f'router = "{marker}"\n')

    def test_router_with_no_after_appends_at_tail(self):
        self._write_router_module("_fixture_router_mod_a", "A")
        self._write_manifest("modb", {
            "id": "modb", "name": "B", "routes": [
                {"module": "_fixture_router_mod_a", "methods": ["GET"], "router_attr": "router"},
            ],
            "admin_sections": [], "nav_entries": [], "db_tables": [],
            "config_keys_prefixes": [],
        })
        order = app_modules.merge_app_routers()
        self.assertEqual([mid for mid, _r in order], ["modb"])

    def test_router_anchored_after_a_sibling_module_is_spliced_in_place(self):
        self._write_router_module("_fixture_router_mod_a", "A")
        self._write_router_module("_fixture_router_mod_b", "B")
        self._write_manifest("mod_base", {
            "id": "mod_base", "name": "Base", "routes": [
                {"module": "_fixture_router_mod_a", "methods": ["GET"], "router_attr": "router"},
            ],
            "admin_sections": [], "nav_entries": [], "db_tables": [],
            "config_keys_prefixes": [],
        })
        self._write_manifest("mod_anchored", {
            "id": "mod_anchored", "name": "Anchored", "routes": [
                {"module": "_fixture_router_mod_b", "methods": ["GET"],
                 "router_attr": "router", "after": "mod_base"},
            ],
            "admin_sections": [], "nav_entries": [], "db_tables": [],
            "config_keys_prefixes": [],
        })
        order = app_modules.merge_app_routers()
        # mod_anchored spliced immediately AFTER mod_base, not appended
        # blindly at the tail by declaration/sort order (mod_anchored <
        # mod_base alphabetically, so a tail-only implementation would get
        # this backwards).
        self.assertEqual([mid for mid, _r in order], ["mod_base", "mod_anchored"])

    def test_unresolved_router_anchor_degrades_to_tail_not_a_crash(self):
        self._write_router_module("_fixture_router_mod_a", "A")
        self._write_manifest("mod_orphan", {
            "id": "mod_orphan", "name": "Orphan", "routes": [
                {"module": "_fixture_router_mod_a", "methods": ["GET"],
                 "router_attr": "router", "after": "no-existe"},
            ],
            "admin_sections": [], "nav_entries": [], "db_tables": [],
            "config_keys_prefixes": [],
        })
        order = app_modules.merge_app_routers()  # never raises
        self.assertEqual([mid for mid, _r in order], ["mod_orphan"])


class TestBrokenRouterModuleBootSurvival(unittest.TestCase):
    """Loader contract point 7 (docs/roadmap/module-contract-design.md),
    extended to router_attr (Tier 4 slice 3): a manifest whose router_attr
    module can't be imported, or whose named attribute doesn't exist,
    never takes app.py's mount_module_routers() down — it's reported
    `broken` and contributes nothing, exactly like a plain route module
    that fails to import (test_module_runtime.py's
    TestBrokenModuleBootSurvival)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="turnos_broken_router_mod_")
        self._orig_modules_dir = app_modules.MODULES_DIR
        app_modules.MODULES_DIR = self.tmpdir
        app_modules._RUNTIME_BROKEN.clear()
        app_modules._invalidate_route_owner_map()

    def tearDown(self):
        app_modules.MODULES_DIR = self._orig_modules_dir
        app_modules._RUNTIME_BROKEN.clear()
        app_modules._invalidate_route_owner_map()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_manifest(self, module_id, doc):
        d = os.path.join(self.tmpdir, module_id)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "module.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f)

    def test_unimportable_router_module_is_broken_not_a_crash(self):
        self._write_manifest("ghost_router", {
            "id": "ghost_router", "name": "Ghost router", "routes": [
                {"module": "this_module_definitely_does_not_exist_xyz",
                 "methods": ["GET"], "router_attr": "router"},
            ],
            "admin_sections": [], "nav_entries": [], "db_tables": [],
            "config_keys_prefixes": [],
        })
        got = app_modules.app_router_entries()  # never raises
        self.assertEqual(got, [])  # contributes NOTHING
        modules = app_modules.list_modules()
        ghost = next(m for m in modules if m["id"] == "ghost_router")
        self.assertEqual(ghost["status"], "broken")
        self.assertIn("this_module_definitely_does_not_exist_xyz", ghost["error"])

    def test_missing_router_attribute_is_broken_not_a_crash(self):
        self._write_manifest("no_attr", {
            "id": "no_attr", "name": "No attr", "routes": [
                # "core.i18n" imports fine (a real, always-importable
                # platform module) but has no `router` attribute at all.
                {"module": "app.core.i18n", "methods": ["GET"], "router_attr": "router"},
            ],
            "admin_sections": [], "nav_entries": [], "db_tables": [],
            "config_keys_prefixes": [],
        })
        got = app_modules.app_router_entries()
        self.assertEqual(got, [])
        modules = app_modules.list_modules()
        entry = next(m for m in modules if m["id"] == "no_attr")
        self.assertEqual(entry["status"], "broken")
        self.assertIn("core.i18n.router", entry["error"])

    def test_a_healthy_sibling_router_is_unaffected_by_a_broken_one(self):
        self._write_manifest("ghost_router", {
            "id": "ghost_router", "name": "Ghost router", "routes": [
                {"module": "this_module_definitely_does_not_exist_xyz",
                 "methods": ["GET"], "router_attr": "router"},
            ],
            "admin_sections": [], "nav_entries": [], "db_tables": [],
            "config_keys_prefixes": [],
        })
        self._write_manifest("intake_router_copy", {
            # Reuses the REAL intake router file — proves a genuinely
            # importable router_attr module survives a broken sibling.
            "id": "intake_router_copy", "name": "Healthy", "routes": [
                {"module": "app.modules.intake.printable_router",
                 "methods": ["GET"], "router_attr": "router"},
            ],
            "admin_sections": [], "nav_entries": [], "db_tables": [],
            "config_keys_prefixes": [],
        })
        got = app_modules.app_router_entries()  # [(module_id, router, after), ...]
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0][0], "intake_router_copy")
        modules = app_modules.list_modules()
        self.assertEqual(next(m for m in modules if m["id"] == "ghost_router")["status"], "broken")
        self.assertEqual(next(m for m in modules if m["id"] == "intake_router_copy")["status"],
                          "enabled")


if __name__ == "__main__":
    unittest.main()
