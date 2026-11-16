"""test_app_modules.py — app_modules.py's enable/disable/route-ownership
engine (Phase 6a runtime slice, 2026-07-30). config.guardar()/_db_overrides()
are monkeypatched to an in-memory dict (same pattern test_config.py and
test_manage_cli.py use) so this runs without a reachable DATABASE_URL.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_app_modules.py"
"""
import os
import sys
import json
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")  # importar sin exigir Postgres
_env.process_defaults({"TURNOS_FAKE_AI": "1", "TURNOS_ADMIN_SECRET": "test-only-secret"})
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
from app.core import app_modules  # noqa: E402
from app.core import app_modules_router  # noqa: E402
from app.core import static_fingerprint  # noqa: E402
from app.auth import admin_auth  # noqa: E402


class _FakeConfigStore(unittest.TestCase):
    """Backs config.get/config.guardar with an in-memory dict instead of a
    real Postgres catalog row — enable()/disable() call config.guardar()
    for real, but nothing touches the network."""

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

    def tearDown(self):
        self._p2.stop()
        self._p1.stop()
        config._reset_cache_para_tests()


class TestDiscovery(_FakeConfigStore):
    def test_discovers_turnos(self):
        self.assertIn("turnos", app_modules.all_ids())

    def test_load_manifest_known(self):
        m = app_modules.load_manifest("turnos")
        self.assertIsNotNone(m)
        self.assertEqual(m["id"], "turnos")

    def test_load_manifest_unknown_returns_none(self):
        self.assertIsNone(app_modules.load_manifest("not-a-real-module"))


class TestPermissionNavigation(_FakeConfigStore):
    def test_custom_grants_show_module_screens_without_admin_only_siblings(self):
        self._store["ENABLED_MODULES"] = "turnos"
        sections = app_modules.sections_payload_for_role(
            "synthetic_scheduler", permissions=frozenset({"medicos", "servicios"}))
        ids = _section_ids(sections)
        self.assertIn("medicos", ids)
        self.assertIn("servicios", ids)
        self.assertIn("centros", ids)
        self.assertIn("preparaciones", ids)
        self.assertNotIn("roles", ids)
        self.assertNotIn("my-schedules", ids)

    def test_explicit_permissions_filter_module_screen_paths(self):
        self._store["ENABLED_MODULES"] = "turnos"
        ids = _section_ids(app_modules.sections_payload_for_role(
            "admin", permissions=frozenset({"medicos"})))
        self.assertIn("medicos", ids)
        self.assertNotIn("servicios", ids)

    def test_bound_request_permissions_reach_the_same_sidebar(self):
        self._store["ENABLED_MODULES"] = "turnos"
        with patch.object(admin_auth, "active_role", return_value="synthetic_scheduler"), \
                patch.object(admin_auth, "active_permissions", return_value=["medicos"]):
            ids = _section_ids(app_modules.sections_payload_for_role("synthetic_scheduler"))
        self.assertIn("medicos", ids)
        self.assertNotIn("servicios", ids)

    def test_empty_grants_and_unrelated_bound_role_do_not_show_custom_screens(self):
        self._store["ENABLED_MODULES"] = "turnos"
        self.assertEqual(_section_ids(app_modules.sections_payload_for_role(
            "synthetic_scheduler", permissions=frozenset())), ["cuenta"])
        with patch.object(admin_auth, "active_role", return_value="admin"), \
                patch.object(admin_auth, "active_permissions", return_value=["medicos"]):
            self.assertEqual(_section_ids(app_modules.sections_payload_for_role(
                "synthetic_scheduler")), ["cuenta"])


class TestEnabledIds(_FakeConfigStore):
    def test_unset_config_means_every_module_enabled(self):
        self.assertEqual(app_modules.enabled_ids(), set(app_modules.all_ids()))
        self.assertTrue(app_modules.is_enabled("turnos"))

    def test_config_naming_a_different_module_disables_turnos(self):
        # Simulates a future second module being the only one enabled —
        # today there's no second real module, so this proves the parsing/
        # gating logic without needing one to exist yet.
        self._store["ENABLED_MODULES"] = "intake"
        self.assertEqual(app_modules.enabled_ids(), {"intake"})
        self.assertFalse(app_modules.is_enabled("turnos"))

    def test_config_takes_effect_without_reimporting(self):
        # No restart needed — same contract every config.py setting has.
        self.assertTrue(app_modules.is_enabled("turnos"))
        self._store["ENABLED_MODULES"] = "intake"
        self.assertFalse(app_modules.is_enabled("turnos"))
        del self._store["ENABLED_MODULES"]
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0
        self.assertTrue(app_modules.is_enabled("turnos"))


class TestEnableDisable(_FakeConfigStore):
    def test_disable_unknown_id_raises(self):
        with self.assertRaises(ValueError):
            app_modules.disable("not-a-real-module")

    def test_enable_unknown_id_raises(self):
        with self.assertRaises(ValueError):
            app_modules.enable("not-a-real-module")

    def test_disable_the_only_enabled_module_is_refused(self):
        # Sin configurar, TODOS los módulos están habilitados (turnos,
        # intake desde 2026-07-31, chat desde Tier 2 batch F 2026-08-07) —
        # para probar la guarda dejamos uno solo.
        self.assertEqual(app_modules.enabled_ids(), {"turnos", "intake", "chat"})
        self._store["ENABLED_MODULES"] = "turnos"
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0
        self.assertEqual(app_modules.enabled_ids(), {"turnos"})
        with self.assertRaises(ValueError) as ctx:
            app_modules.disable("turnos")
        self.assertIn("turnos", str(ctx.exception))
        # Refused means nothing was persisted — still enabled.
        self.assertTrue(app_modules.is_enabled("turnos"))

    def test_disable_intake_which_nothing_depends_on_succeeds(self):
        # intake has no dependents, and turnos stays enabled afterwards --
        # the "not the last module" case (three real modules since Tier 2
        # batch F added chat; intake genuinely requires turnos and chat, see
        # docs/architecture/chat-intake-manifests.md, Requires). This test never calls
        # enable("intake") again, so chat's absence from the store here
        # doesn't matter — disable()'s own requires check is disable-side
        # (dependents of intake), not intake's own unmet dependencies.
        self._store["ENABLED_MODULES"] = "turnos,intake"
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0
        app_modules.disable("intake")
        self.assertFalse(app_modules.is_enabled("intake"))
        self.assertTrue(app_modules.is_enabled("turnos"))

    def test_disable_turnos_is_refused_while_intake_depends_on_it(self):
        # Codex monitor review (P0, 2026-08-05): intake.requires == ["turnos"]
        # is now real, not declarative-only -- disabling turnos while intake
        # is still enabled must be REFUSED, with a human-readable reason
        # naming intake, same dependents-guard enable()/disable() already
        # enforce for a synthetic fixture (test_module_runtime.py's
        # TestDependencyRefusalWithReason).
        self._store["ENABLED_MODULES"] = "turnos,intake"
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0
        with self.assertRaises(ValueError) as ctx:
            app_modules.disable("turnos")
        self.assertIn("intake", str(ctx.exception))
        # Refused for real: nothing persisted, both still enabled.
        self.assertTrue(app_modules.is_enabled("turnos"))
        self.assertTrue(app_modules.is_enabled("intake"))

    def test_disable_turnos_succeeds_once_intake_is_disabled_first(self):
        self._store["ENABLED_MODULES"] = "turnos,intake"
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0
        app_modules.disable("intake")
        # turnos is now the ONLY enabled module -- the last-module guard
        # (not the dependents guard) refuses this, same as
        # test_disable_the_only_enabled_module_is_refused above. Disabling
        # turnos "successfully" is therefore never reachable while intake
        # is the only sibling module -- this documents that interplay
        # instead of asserting a scenario that can't exist with just these
        # two real modules.
        with self.assertRaises(ValueError) as ctx:
            app_modules.disable("turnos")
        self.assertIn("turnos", str(ctx.exception))

    def test_enable_is_idempotent(self):
        app_modules.enable("turnos")
        app_modules.enable("turnos")
        self.assertIn("turnos", app_modules.enabled_ids())

    def test_disable_already_disabled_module_is_a_noop_not_an_error(self):
        self._store["ENABLED_MODULES"] = "turnos"
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0
        # "intake" existe pero ya está deshabilitado — deshabilitarlo de
        # nuevo es un no-op; un id INEXISTENTE sí sigue levantando.
        self.assertEqual(app_modules.disable("intake"), {"turnos"})
        with self.assertRaises(ValueError):
            app_modules.disable("not-a-real-module")


class TestRouteOwnership(_FakeConfigStore):
    def test_platform_core_route_is_always_enabled(self):
        self._store["ENABLED_MODULES"] = "nothing-real"
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0
        self.assertTrue(app_modules.route_enabled("app.cuentas.accounts"))
        self.assertTrue(app_modules.route_enabled("app.core.i18n"))
        self.assertTrue(app_modules.route_enabled("app.admin_panel.settings_admin"))
        self.assertTrue(app_modules.route_enabled("app.core.app_modules"))

    def test_turnos_owned_route_follows_turnos_enabled_state(self):
        self.assertTrue(app_modules.route_enabled("app.patient_web.site_shell"))
        self._store["ENABLED_MODULES"] = "nothing-real"
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0
        self.assertFalse(app_modules.route_enabled("app.patient_web.site_shell"))
        self.assertFalse(app_modules.route_enabled("app.modules.turnos.doctor_appointments_router"))

    def test_owner_map_is_memoized_across_route_enabled_calls(self):
        # route_enabled() used to rebuild the owner map (list_modules(): a
        # full disk listdir + open/json.load per manifest + cycle
        # detection) on EVERY call — routing.dispatch() calls it once per
        # registered route module PER REQUEST. Prove the memo actually
        # cuts that down to a single rebuild.
        app_modules._invalidate_route_owner_map()
        calls = []
        real_list_modules = app_modules.list_modules

        def counting():
            calls.append(1)
            return real_list_modules()

        with patch.object(app_modules, "list_modules", side_effect=counting):
            app_modules.route_enabled("app.patient_web.site_shell")
            app_modules.route_enabled("app.core.i18n")
            app_modules.route_enabled("app.modules.turnos.doctor_appointments")
        self.assertEqual(len(calls), 1)

    def test_owner_map_cache_invalidates_on_enable_and_disable(self):
        # Populate the memo, confirm it's set, then confirm enable()/
        # disable() drop it (config.py's own _db_cache pattern) — and that
        # route_enabled() still resolves correctly once it rebuilds, so
        # the caching never stales out live enable/disable behavior.
        # "chat" also enabled: intake requires it since Tier 2 batch F, and
        # this test re-enables intake below.
        self._store["ENABLED_MODULES"] = "turnos,intake,chat"
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0
        app_modules._invalidate_route_owner_map()

        self.assertTrue(app_modules.route_enabled("app.patient_web.site_shell"))
        self.assertIsNotNone(app_modules._owner_map_cache)

        app_modules.disable("intake")
        self.assertIsNone(app_modules._owner_map_cache)
        self.assertTrue(app_modules.route_enabled("app.patient_web.site_shell"))  # rebuilds fine
        self.assertIsNotNone(app_modules._owner_map_cache)

        app_modules.enable("intake")
        self.assertIsNone(app_modules._owner_map_cache)
        self.assertTrue(app_modules.route_enabled("app.patient_web.site_shell"))


class TestManifestDerivedUiData(_FakeConfigStore):
    def test_fingerprinted_module_assets_remain_registered_when_disabled(self):
        self._store["ENABLED_MODULES"] = "turnos,chat"
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0
        assets = app_modules.module_asset_paths()
        self.assertIsInstance(assets, frozenset)
        self.assertIn("modules/intake/entrevista.js", assets)

    def test_fingerprinted_module_assets_ignore_runtime_startup_health(self):
        static_fingerprint.invalidate()
        app_modules._RUNTIME_BROKEN["intake"] = "synthetic startup failure"
        self.addCleanup(app_modules._RUNTIME_BROKEN.pop, "intake", None)
        self.addCleanup(static_fingerprint.invalidate)
        self.assertIn("modules/intake/entrevista.js", app_modules.module_asset_paths())
        # Populate the static cache while runtime status is broken, then make
        # the status healthy again: asset membership must not depend on call
        # order around startup.
        self.assertIn("modules/intake/entrevista.js", static_fingerprint._module_assets())
        app_modules._RUNTIME_BROKEN.pop("intake")
        self.assertIn("modules/intake/entrevista.js", static_fingerprint._module_assets())

    def test_hardlinked_declared_asset_is_broken_and_not_registered(self):
        with tempfile.TemporaryDirectory() as root:
            module_dir = os.path.join(root, "modules", "probe")
            os.makedirs(module_dir)
            asset = os.path.join(module_dir, "client.js")
            with open(asset, "wb") as f:
                f.write(b"probe")
            os.link(asset, os.path.join(root, "external-alias.js"))
            with open(os.path.join(module_dir, "module.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "id": "probe", "assets": [{
                        "path": "modules/probe/client.js", "fingerprint": True,
                    }],
                }, f)
            with (
                patch.object(app_modules, "CODE_ROOT", root),
                patch.object(app_modules, "MODULES_DIR", os.path.join(root, "modules")),
            ):
                status = app_modules.list_modules()
                self.assertEqual(status[0]["status"], "broken")
                self.assertEqual(app_modules.module_asset_paths(), frozenset())


class TestStrictModuleAssetValidation(_FakeConfigStore):
    def _status_for(self, assets, *, admin_sections=None, prepare=None, migrated=()):
        with tempfile.TemporaryDirectory() as root:
            module_dir = os.path.join(root, "modules", "probe")
            os.makedirs(module_dir)
            if prepare is None:
                with open(os.path.join(module_dir, "client.js"), "wb") as f:
                    f.write(b"probe")
            else:
                prepare(root, module_dir)
            with open(os.path.join(module_dir, "module.json"), "w", encoding="utf-8") as f:
                json.dump({"id": "probe", "assets": assets,
                           "admin_sections": admin_sections or []}, f)
            with (
                patch.object(app_modules, "CODE_ROOT", root),
                patch.object(app_modules, "MODULES_DIR", os.path.join(root, "modules")),
                patch.object(static_fingerprint, "MIGRATED",
                             static_fingerprint.MIGRATED | frozenset(migrated)),
            ):
                entry = app_modules.list_modules()[0]
                registered = app_modules.module_asset_paths()
            return entry, registered

    def test_unsafe_owner_traversal_missing_and_extension_declarations_break(self):
        cases = (
            [{"path": "modules/other/client.js", "fingerprint": True}],
            [{"path": "modules/probe/../client.js", "fingerprint": True}],
            [{"path": "modules/probe/missing.js", "fingerprint": True}],
            [{"path": "modules/probe/client.txt", "fingerprint": True}],
        )
        for assets in cases:
            with self.subTest(assets=assets):
                entry, registered = self._status_for(assets)
                self.assertEqual(entry["status"], "broken")
                self.assertEqual(registered, frozenset())

    def test_directory_symlink_duplicate_platform_overlap_and_undeclared_admin_break(self):
        def as_directory(_root, module_dir):
            os.mkdir(os.path.join(module_dir, "client.js"))

        def as_symlink(root, module_dir):
            outside = os.path.join(root, "outside.js")
            with open(outside, "wb") as f:
                f.write(b"outside")
            os.symlink(outside, os.path.join(module_dir, "client.js"))

        cases = (
            ([{"path": "modules/probe/client.js", "fingerprint": True}], as_directory, (), []),
            ([{"path": "modules/probe/client.js", "fingerprint": True}], as_symlink, (), []),
            ([{"path": "modules/probe/client.js", "fingerprint": True},
              {"path": "modules/probe/client.js", "fingerprint": True}], None, (), []),
            ([{"path": "modules/probe/client.js", "fingerprint": True}], None,
             ("modules/probe/client.js",), []),
            ([{"path": "modules/probe/client.js", "fingerprint": True}], None, (),
             [{"id": "admin", "js": "modules/probe/not-declared.js"}]),
        )
        for assets, prepare, migrated, sections in cases:
            with self.subTest(assets=assets, migrated=migrated, sections=sections):
                entry, registered = self._status_for(
                    assets, prepare=prepare, migrated=migrated, admin_sections=sections)
                self.assertEqual(entry["status"], "broken")
                self.assertEqual(registered, frozenset())

    def test_enabled_admin_sections_includes_platform_and_module_sections(self):
        sections = app_modules.enabled_admin_sections()
        self.assertIn("solicitudes", sections)  # turnos
        self.assertIn("settings", sections)  # platform-core

    def test_disabling_turnos_drops_its_sections_but_keeps_platform(self):
        self._store["ENABLED_MODULES"] = "nothing-real"
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0
        sections = app_modules.enabled_admin_sections()
        self.assertNotIn("solicitudes", sections)
        self.assertIn("settings", sections)

    def test_disabling_a_module_removes_its_section_from_the_served_payload(self):
        # Item 5 (Codex monitor review, P0, 2026-08-05): the exact data
        # GET /api/admin/modules serves (sections_payload_for_role()) has
        # to drop a module's section the moment it's disabled — this is
        # the backend half of the admin-sections.test.mjs pair proving
        # the same thing client-side.
        ids_before = _section_ids(app_modules.sections_payload_for_role("admin"))
        self.assertIn("solicitudes", ids_before)
        self._store["ENABLED_MODULES"] = "intake"  # turnos disabled
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0
        ids_after = _section_ids(app_modules.sections_payload_for_role("admin"))
        self.assertNotIn("solicitudes", ids_after)
        self.assertIn("settings", ids_after)  # platform-core survives

    def test_disabling_intake_removes_the_real_entrevistas_section_from_the_sidebar(self):
        # Tier 2 pilot (2026-08-05, docs/roadmap/module-contract-design.md
        # "Pilot", acceptance (a)): unlike the synthetic-shaped assertion
        # above, this reads modules/intake/module.json's REAL v2
        # admin_sections entry (label_key/order/js, no admin.html literal
        # backing it any more) and proves it's gone from an admin session's
        # sidebar payload the moment intake is disabled — and back the
        # moment it's re-enabled — with zero edits outside modules/intake/
        # needed to make either direction work.
        # "chat" also enabled: intake requires it since Tier 2 batch F, and
        # this test re-enables intake below.
        self._store["ENABLED_MODULES"] = "turnos,intake,chat"
        config._db_cache["v"] = None
        config._db_cache["t"] = 0.0
        before = app_modules.sections_payload_for_role("admin")
        entrevistas = next((s for s in before if isinstance(s, dict) and s.get("id") == "entrevistas"), None)
        self.assertIsNotNone(entrevistas, "entrevistas debe aparecer con intake habilitado")
        self.assertEqual(
            entrevistas["js"],
            static_fingerprint.module_url("modules/intake/admin-entrevistas.mjs"),
        )
        self.assertEqual(entrevistas["label_key"], "intake.nav.entrevistas")

        app_modules.disable("intake")
        after_disable = app_modules.sections_payload_for_role("admin")
        self.assertNotIn("entrevistas", _section_ids(after_disable))

        app_modules.enable("intake")
        after_enable = app_modules.sections_payload_for_role("admin")
        self.assertIn("entrevistas", _section_ids(after_enable))

        # A non-admin role that "entrevistas" doesn't opt in (no `roles` key
        # -> admin-only default, same as the old applyRol()'s esAdmin) never
        # sees it, enabled or not.
        medico_sections = app_modules.sections_payload_for_role("medico")
        self.assertNotIn("entrevistas", _section_ids(medico_sections))

    def test_enabled_nav_entries_has_doctors(self):
        paths = [e["path"] for e in app_modules.enabled_nav_entries()]
        self.assertIn("/doctors", paths)

    def test_modules_status_shape(self):
        status = app_modules.modules_status()
        turnos = next(m for m in status if m["id"] == "turnos")
        self.assertTrue(turnos["enabled"])
        self.assertIn("name", turnos)
        self.assertIn("description", turnos)

    def test_modules_status_projects_the_same_module_script_url_as_sidebar(self):
        status = app_modules.modules_status()
        intake = next(m for m in status if m["id"] == "intake")
        section = next(s for s in intake["admin_sections"] if s["id"] == "entrevistas")
        self.assertEqual(
            section["js"],
            static_fingerprint.module_url("modules/intake/admin-entrevistas.mjs"),
        )

    def test_runtime_broken_status_keeps_valid_module_script_fingerprinted(self):
        static_fingerprint.invalidate()
        app_modules._RUNTIME_BROKEN["intake"] = "synthetic startup failure"
        self.addCleanup(app_modules._RUNTIME_BROKEN.pop, "intake", None)
        self.addCleanup(static_fingerprint.invalidate)
        intake = next(module for module in app_modules.modules_status()
                      if module["id"] == "intake")
        section = next(section for section in intake["admin_sections"]
                       if section["id"] == "entrevistas")
        self.assertEqual(intake["status"], "broken")
        self.assertEqual(
            section["js"],
            static_fingerprint.module_url("modules/intake/admin-entrevistas.mjs"),
        )

    def test_fingerprint_projection_error_omits_raw_script_from_sidebar_and_status(self):
        with patch.object(static_fingerprint, "module_url", side_effect=ValueError("broken registry")):
            sidebar = app_modules.sections_payload_for_role("admin")
            status = app_modules.modules_status()
        sidebar_section = next(section for section in sidebar
                               if isinstance(section, dict) and section["id"] == "entrevistas")
        intake = next(module for module in status if module["id"] == "intake")
        status_section = next(section for section in intake["admin_sections"]
                              if section["id"] == "entrevistas")
        self.assertNotIn("js", sidebar_section)
        self.assertNotIn("js", status_section)


def _client():
    app = FastAPI()
    app.include_router(app_modules_router.router)
    return TestClient(app)


def _section_ids(sections):
    """A `sections` payload entry is either a bare platform-core string or
    a rich v2 module dict (Codex monitor review, P0/P1, 2026-08-05) — this
    normalizes either shape down to its id for assertions."""
    return [s if isinstance(s, str) else s["id"] for s in sections]


class TestAdminModulesEndpoint(_FakeConfigStore):
    def test_unauthenticated_request_is_gated(self):
        with _client() as client:
            response = client.get("/api/admin/modules")
        self.assertEqual(response.status_code, 401)

    def test_admin_request_returns_modules_sections_nav(self):
        with _client() as client:
            response = client.get(
                "/api/admin/modules",
                cookies={"st_admin": admin_auth._mint_token("admin1", "admin")},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        ids = [m["id"] for m in payload["modules"]]
        self.assertIn("turnos", ids)
        self.assertIn("solicitudes", _section_ids(payload["sections"]))
        self.assertIn("settings", _section_ids(payload["sections"]))

    def test_admin_api_projects_the_module_fingerprinted_section_url(self):
        with _client() as client:
            response = client.get(
                "/api/admin/modules",
                cookies={"st_admin": admin_auth._mint_token("admin1", "admin")},
            )
        payload = response.json()
        intake = next(module for module in payload["modules"] if module["id"] == "intake")
        status_section = next(section for section in intake["admin_sections"]
                              if section["id"] == "entrevistas")
        sidebar_section = next(section for section in payload["sections"]
                               if isinstance(section, dict) and section["id"] == "entrevistas")
        expected = static_fingerprint.module_url("modules/intake/admin-entrevistas.mjs")
        self.assertEqual(status_section["js"], expected)
        self.assertEqual(sidebar_section["js"], expected)

    def test_medico_request_gets_its_allowed_sections_and_no_module_status(self):
        # Codex monitor review (P1, 2026-08-05): a medico session used to
        # get a flat 403 from solo_admin=True. It must now reach the
        # endpoint, see exactly the sections its role is allowed
        # (including the direct Solicitudes screen, hidden only in nav,
        # and slice 5's onboarding and own schedules),
        # and get NOTHING about enable/disable controls (no "modules" key
        # at all — that's the admin/enable-disable-relevant data).
        with _client() as client:
            response = client.get(
                "/api/admin/modules",
                cookies={"st_admin": admin_auth._mint_token("dr1", "medico")},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        ids = set(_section_ids(payload["sections"]))
        self.assertEqual(ids, {"solicitudes", "cola", "mis-turnos", "mis-pacientes", "cuenta", "onboarding", "my-schedules", "consulta"})
        self.assertNotIn("modules", payload)
        # Admin-only sections stay invisible to medico.
        self.assertNotIn("servicios", ids)
        self.assertNotIn("usuarios", ids)
        self.assertNotIn("settings", ids)

    def test_post_is_not_handled(self):
        with _client() as client:
            response = client.post("/api/admin/modules")
        self.assertEqual(response.status_code, 405)

    def test_unknown_path_is_not_handled(self):
        with _client() as client:
            response = client.get("/api/admin/other")
        self.assertEqual(response.status_code, 404)


class TestAdminModulesEndpointRealAuthGate(_FakeConfigStore):
    """Same endpoint, but through the REAL admin_auth.require_admin() —
    no mocking of the function under test (Codex monitor review, P1,
    2026-08-05). A signed token (admin_auth._mint_token(), the same
    DB-free helper every other admin-HTTP test file uses) proves the
    ACTUAL gate change: `solo_admin=True` used to 403 a medico session
    outright; `permitir_medico=True` (no solo_admin) is what actually
    lets it through in production, not just in this file's own payload
    logic above."""

    def _get_as(self, usuario, rol):
        with _client() as client:
            return client.get(
                "/api/admin/modules",
                cookies={"st_admin": admin_auth._mint_token(usuario, rol)},
            )

    def test_medico_session_reaches_the_endpoint_for_real(self):
        response = self._get_as("dr1", "medico")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertNotIn("modules", payload)
        self.assertIn("mis-turnos", _section_ids(payload["sections"]))

    def test_admin_session_reaches_the_endpoint_for_real(self):
        response = self._get_as("owner1", "admin")
        self.assertEqual(response.status_code, 200)
        self.assertIn("modules", response.json())

    def test_no_cookie_is_401_for_real(self):
        with _client() as client:
            response = client.get("/api/admin/modules")
        self.assertEqual(response.status_code, 401)


class TestAppRouterEntriesFailLoud(_FakeConfigStore):
    """Batch C (Tier 2, moving the auth/*.py files under auth/) shipped
    with no code path that would have made a stale `router_attr` string
    (one still naming a module's OLD pre-move location) visible anywhere
    but a manual GET /api/admin/modules — `app_router_entries()`/
    `_base_app_routes()` recorded the failure in `_RUNTIME_BROKEN` and
    moved on in total silence, matching neither this repo's fail-loud
    doctrine nor the "make it loud" fix that landed alongside this test.
    Doesn't touch a real broken manifest (none exist today, and
    test_module_manifest.py's TestRouterAttrEntriesResolve already proves
    every real router_attr resolves) — proves the MECHANISM instead, by
    forcing exactly the failure a moved-but-not-updated router_attr would
    produce."""

    def tearDown(self):
        app_modules._RUNTIME_BROKEN.pop("turnos", None)
        super().tearDown()

    def test_a_broken_app_router_is_marked_broken_and_printed_to_stderr(self):
        import io
        import contextlib

        real_validate_app_routers = app_modules._validate_app_routers

        def _boom(manifest):
            if manifest["id"] == "turnos":
                return [], "no se pudo montar router turnos.foo_router_movido: ModuleNotFoundError(...)"
            return real_validate_app_routers(manifest)

        buf = io.StringIO()
        with patch.object(app_modules, "_validate_app_routers", side_effect=_boom):
            with contextlib.redirect_stderr(buf):
                entries = app_modules.app_router_entries()

        self.assertNotIn("turnos", [mid for mid, _r, _after in entries])
        self.assertIn("turnos", app_modules._RUNTIME_BROKEN)
        self.assertIn("no se pudo montar router turnos", buf.getvalue())

    def test_a_broken_legacy_route_module_is_marked_broken_and_printed_to_stderr(self):
        import io
        import contextlib

        real_validate_routes = app_modules._validate_routes

        def _boom(manifest):
            if manifest["id"] == "turnos":
                return {}, "no se pudo importar 'turnos_modulo_movido': ModuleNotFoundError(...)"
            return real_validate_routes(manifest)

        buf = io.StringIO()
        with patch.object(app_modules, "_validate_routes", side_effect=_boom):
            with contextlib.redirect_stderr(buf):
                out = app_modules._base_app_routes("GET")

        self.assertNotIn("turnos", [m.__name__.split(".")[0] for m, _after in out] or [])
        self.assertIn("turnos", app_modules._RUNTIME_BROKEN)
        self.assertIn("ruta legacy de 'turnos' no se pudo importar", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
