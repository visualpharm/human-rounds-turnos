"""Validates every modules/<id>/module.json against the real code it
describes (Phase 6a; routing.py's dispatch() actually gates these routes at
runtime — see test_module_runtime.py for that half of the acceptance gate).
Every assertion here is a drift check: it fails the moment a manifest and
routing.py/admin.html/db.py/nav.js disagree, in EITHER direction (a route a
manifest forgot, or one a manifest claims that no longer exists).

Tier 2 module-contract framework (2026-08-05, docs/roadmap/module-contract-
design.md): manifests are now v2-shaped (routes: [{module, methods}],
admin_sections/nav_entries: richer dicts, + requires/entrypoint/assets/
locales) and routing.py no longer hand-lists an app module's route names as
literal imports — it BUILDS its GET/POST/PUT/DELETE registries from the
manifests via app_modules.route_modules(). `_routing_registered_names()`
below therefore imports routing.py for real and inspects the live
registries, instead of regex-scanning its source text (which would only
ever see the platform-core literals now).

Originally single-manifest (turnos); generalized when modules/intake/ became
the second app module (2026-07-31) — classification checks now run against
the UNION of all manifests, plus pairwise-disjointness so two modules can't
claim the same route/table.
"""
import glob
import importlib
import json
import os
import sys
import unittest

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")  # boot the loader without Postgres
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import app_modules, jinja_env  # noqa: E402
import snapshots  # noqa: E402

# Platform-core allowlist — kept in sync with the manifests by hand (small,
# rarely-changing lists; a single source-of-truth
# constant isn't worth the indirection for six short lists). Any module/
# table/section that is neither in a module.json NOR in the matching
# allowlist here fails a test below — that's the drift signal.
PLATFORM_ROUTES = {
    # Tier 2 batch K (LAST batch): health.py, legacy_urls.py,
    # static_fingerprint.py, i18n.py, site_identity.py and app_modules.py
    # live in core/ now (`from core import health`, etc.) — dotted path,
    # not the local alias, same shape as every batch below.
    "app.core.health",
    # Tier 2 batch J: setup.py and demo_installation.py live in ops/ now
    # (`from ops import setup`/`from ops import demo_installation`) —
    # dotted path, not the local alias, same shape as every batch below.
    "app.ops.setup", "app.core.legacy_urls", "app.core.static_fingerprint", "app.ops.demo_installation", "app.core.i18n",
    # Tier 2 batch G: these two live in cuentas/ now (`from cuentas import
    # accounts`/`from cuentas import auth_renaper`) — routing.py's
    # dispatch() gates on `module.__name__`, which for a package submodule
    # is its dotted path ("cuentas.accounts"), not the local alias.
    "app.cuentas.accounts", "app.cuentas.auth_renaper",
    # Tier 2 batch C: these three live in auth/ now (`from auth import
    # password_reset as admin_password_reset`, etc.) — routing.py's
    # dispatch() gates on `module.__name__`, which for a package submodule
    # is its dotted path ("auth.password_reset"), not the local alias.
    "app.auth.password_reset", "app.auth.passkeys", "app.auth.panel_login",
    "app.core.site_identity",
    # Tier 2 batch H: branding.py and legal.py live in patient_web/ now
    # (`from patient_web import branding`/`from patient_web import legal`)
    # — routing.py's dispatch() gates on `module.__name__`, which for a
    # package submodule is its dotted path ("patient_web.branding"), not
    # the local alias.
    "app.patient_web.branding",
    # Tier 2 batch I: these five platform-core admin/settings files live in
    # admin_panel/ now (`from admin_panel import admin_users`, etc.) —
    # dotted path, not the local alias, same shape as every batch above.
    "app.admin_panel.admin_users", "app.admin_panel.admin_preferences",
    "app.admin_panel.admin_account", "app.admin_panel.settings_admin",
    "app.admin_panel.system_status",
    "app.core.app_modules",
    # legal.py: los términos y la política de privacidad son del sitio, no
    # del módulo de turnos — una instalación sin `turnos` los sigue teniendo.
    "app.patient_web.legal",
}
PLATFORM_ADMIN_SECTIONS = {"usuarios", "cuenta", "settings", "sistema", "configuracion", "plan",
                           "legal"}
# Table ownership is generated: a module owns the tables its module.json
# `db_tables` claims, every other table is platform-core, and the result lives in
# tests/snapshots.json (section `owners`; `python3 tests/snapshots.py --write`).
# A NEW table needs nothing here. This set is only the deny-list for module
# claims — platform-core tables no module may take over (the panel's access
# model, sessions, the translation cache, shared chat messages, the backup
# journal...) — so a manifest edit cannot silently move one of them.
PLATFORM_ONLY_TABLES = {
    # roles/operator_access: el modelo de acceso del panel (core/permissions.py)
    # es platform-core — una instalación sin `turnos` igual tiene su personal.
    "operators", "roles", "operator_access", "admin_preferences", "admin_reset_tokens",
    "operator_username_history", "admin_account_security", "admin_session_revocations", "passkeys", "passkey_challenges", "users", "sessions",
    "reset_tokens", "otp_codes", "otp_verified", "catalog",
    "login_panel_events", "legal_docs",
    # clinical_access_log: who opened a patient's clinical data, whichever
    # module served it (core/access_log.py, K10 2026-10-23) — platform core.
    "clinical_access_log",
    # data_subject_requests / _events: the ledger of access, correction and
    # erasure requests (core/subject_requests.py, Ley 25.326 arts. 14-16) —
    # platform core, like the access log it sits next to.
    "data_subject_requests", "data_subject_request_events",
    # translations: the machine-translation cache for pack content
    # (translate.py, docs/architecture/i18n.md) — platform i18n core. It
    # used to be created only by the pre-Alembic migration 002 and was
    # invisible to this file's regex scan of db.py; since the legacy-runner
    # retirement (2026-08-16) its DDL lives in `_SCHEMA_SQL`.
    "translations",
    # chat_messages became platform-shared when modules/intake/ started
    # persisting interview transcripts in it (channel='interview') next to
    # turnos's web-widget + WhatsApp channels — see
    # docs/architecture/chat-intake-manifests.md, DB tables.
    "chat_messages",
    # alert_claims: durable at-most-once claims for daily operational
    # alerts (housekeeping's disk alert today, Codex gate 2026-08-08) —
    # platform housekeeping, not a feature module's table.
    "alert_claims",
    # Durable off-box-backup state belongs to ops.data_backups and its
    # operator-only recovery tooling, not to an app module feature.
    "backup_journal", "backup_versions", "backup_version_tombstones", "backup_runs",
    "backup_purge_state",
    # Operational counters for the launch digest (core/launch_signals.py): platform state.
    "launch_signal_counters",
    # Journal of files the retention deleted on purpose (the B2 sweep deletes
    # their remote copies; restore refuses them): ops.data_backups state too.
    "purged_files",
    # What the 0030 date/time conversion could not keep in its column (ops-only).
    "date_time_conversion_leftovers",
    # Demo refresh completion is deployment-observability state owned by
    # ops.demo_refresh, not by a feature module.
    "demo_refresh_completion",
}


def _load_manifests():
    """{module_id: normalized-v2-manifest} for every modules/*/module.json
    on disk. Loads the RAW json (not app_modules.list_modules(), which also
    decorates with runtime status/error) and runs it through the same
    normalizer the loader uses, so a test here fails exactly the way a real
    boot would if a manifest were malformed."""
    out = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "app", "modules", "*", "module.json"))):
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        out[raw["id"]] = app_modules._normalize_manifest(raw, raw["id"])
    return out


class TestManifestSchemas(unittest.TestCase):
    def setUp(self):
        self.manifests = _load_manifests()

    def test_expected_modules_exist(self):
        self.assertIn("turnos", self.manifests)
        self.assertIn("intake", self.manifests)

    def test_every_manifest_has_the_v2_shape(self):
        for mid, manifest in self.manifests.items():
            for key in ("id", "name", "description", "requires", "entrypoint",
                        "routes", "admin_sections", "nav_entries", "assets",
                        "locales", "db_tables", "config_keys_prefixes"):
                self.assertIn(key, manifest, f"{mid}: falta {key!r} tras normalizar")
            self.assertEqual(manifest["id"], mid)

    def test_every_route_entry_is_v2_shape(self):
        for mid, manifest in self.manifests.items():
            for route in manifest["routes"]:
                self.assertIsInstance(route, dict, f"{mid}: {route!r}")
                self.assertIn("module", route)
                self.assertIn("methods", route)
                self.assertIn("router_attr", route)  # Tier 4 slice 3 — None for a plain route
                self.assertTrue(route["methods"], f"{mid}: {route['module']} sin methods")
                self.assertTrue(set(route["methods"]) <= {"GET", "POST", "PUT", "DELETE"},
                                 f"{mid}: {route['module']} tiene methods inválidos {route['methods']}")

    def test_every_admin_section_entry_is_v2_shape(self):
        for mid, manifest in self.manifests.items():
            for section in manifest["admin_sections"]:
                self.assertIsInstance(section, dict, f"{mid}: {section!r}")
                self.assertIn("id", section)
                for key in ("label_key", "label", "roles", "js"):
                    self.assertIn(key, section)

    def test_every_nav_entry_is_v2_shape(self):
        for mid, manifest in self.manifests.items():
            for entry in manifest["nav_entries"]:
                self.assertIsInstance(entry, dict, f"{mid}: {entry!r}")
                self.assertIn("path", entry)
                self.assertIn("label", entry)
                self.assertIn("label_key", entry)
                self.assertIn("order", entry)


class TestManifestFieldsAreRead(unittest.TestCase):
    """A manifest field nobody reads is dead text that drifts (the `*_note` prose, an `icon` no screen drew, an `order`
    no sort used). Every key of every raw module.json must be one the loader (`core/app_modules.py`) reads; prose
    belongs in docs/architecture/ (turnos-manifest.md, chat-intake-manifests.md). A new field = a reader first."""

    TOP = {"id", "name", "description", "requires", "entrypoint", "routes", "admin_sections", "nav_entries",
           "assets", "locales", "db_tables", "config_keys_prefixes"}
    ROUTE = {"module", "methods", "router_attr", "after"}
    SECTION = {"id", "label_key", "label", "roles", "js", "screen", "nav_hidden_for"}
    SCREEN = {"path", "module", "router_attr", "template", "after", "requires_capability", "requires_connector",
              "requires_settings"}
    NAV = {"path", "label", "label_key", "order"}
    ASSET = {"path", "fingerprint"}

    def _raw(self):
        for path in sorted(glob.glob(os.path.join(ROOT, "app", "modules", "*", "module.json"))):
            with open(path, encoding="utf-8") as f:
                yield os.path.relpath(path, ROOT), json.load(f)

    def _unread(self, where, entry, allowed):
        return [f"{where}: {key!r}" for key in entry if key not in allowed]

    def test_no_manifest_field_is_unread(self):
        unread = []
        for path, raw in self._raw():
            unread += self._unread(path, raw, self.TOP)
            for route in raw.get("routes") or []:
                unread += self._unread(f"{path} route {route.get('module')}", route, self.ROUTE)
            for section in raw.get("admin_sections") or []:
                unread += self._unread(f"{path} section {section.get('id')}", section, self.SECTION)
                unread += self._unread(f"{path} screen {section.get('id')}", section.get("screen") or {}, self.SCREEN)
            for nav in raw.get("nav_entries") or []:
                unread += self._unread(f"{path} nav {nav.get('path')}", nav, self.NAV)
            for asset in raw.get("assets") or []:
                if isinstance(asset, dict):
                    unread += self._unread(f"{path} asset {asset.get('path')}", asset, self.ASSET)
        self.assertEqual(unread, [], "manifest fields the loader does not read (prose goes to docs/architecture/)")

    def test_the_normalizer_keeps_every_field_the_allowlists_name(self):
        """The allowlists are not a second schema: each name is one the normalizer carries into the manifest."""
        route = app_modules._normalize_route({"module": "m", "router_attr": "r", "after": "a"})
        self.assertLessEqual(self.ROUTE, set(route))
        section = app_modules._normalize_admin_section(
            {"id": "x", "label_key": "k", "label": "L", "roles": ["admin"], "js": "j.mjs",
             "screen": {"path": "/admin/x", "router_attr": "r", "module": "m"}})
        self.assertLessEqual(self.SECTION, set(section))
        self.assertLessEqual({"path", "module", "router_attr", "template", "after"}, set(section["screen"]))
        nav = app_modules._normalize_nav_entry({"path": "/x", "label": "L", "label_key": "k", "order": 1})
        self.assertLessEqual(self.NAV, set(nav))


class TestV1ToV2NormalizationRoundTrip(unittest.TestCase):
    """`_normalize_manifest()` is the ONE place v1 -> v2 happens — a v1
    manifest (bare-string routes/admin_sections, {path,label} nav_entries,
    no requires/entrypoint/assets/locales) must normalize cleanly and stay
    semantically identical, so an installation's old manifest keeps working
    untouched."""

    def _v1(self, **overrides):
        base = {"id": "x", "name": "X", "description": "d", "routes": [],
                "admin_sections": [], "nav_entries": [], "db_tables": [],
                "config_keys_prefixes": []}
        base.update(overrides)
        return base

    def test_v1_bare_string_routes_become_v2_all_methods(self):
        m = app_modules._normalize_manifest(self._v1(routes=["foo", "bar"]), "x")
        self.assertEqual(m["routes"], [
            {"module": "foo", "methods": ["GET", "POST", "PUT", "DELETE"], "after": None,
             "router_attr": None},
            {"module": "bar", "methods": ["GET", "POST", "PUT", "DELETE"], "after": None,
             "router_attr": None},
        ])

    def test_v2_route_dict_form_passes_through(self):
        m = app_modules._normalize_manifest(
            self._v1(routes=[{"module": "foo", "methods": ["GET"]}]), "x")
        self.assertEqual(m["routes"], [{"module": "foo", "methods": ["GET"], "after": None,
                                         "router_attr": None}])

    def test_v2_route_router_attr_passes_through(self):
        # Tier 4 slice 3 — a route entry naming router_attr keeps it, and
        # still defaults `after` to None like any other route entry.
        m = app_modules._normalize_manifest(self._v1(routes=[
            {"module": "foo_router", "methods": ["GET"], "router_attr": "router"},
        ]), "x")
        self.assertEqual(m["routes"], [
            {"module": "foo_router", "methods": ["GET"], "after": None, "router_attr": "router"},
        ])

    def test_v2_route_after_anchor_passes_through(self):
        # String form (same anchor for every method) and dict form
        # (per-method anchor — Codex monitor P0 fix, 2026-08-05) both
        # survive normalization untouched.
        m = app_modules._normalize_manifest(self._v1(routes=[
            {"module": "foo", "methods": ["GET"], "after": "i18n"},
            {"module": "bar", "methods": ["GET", "POST"],
             "after": {"GET": "i18n", "POST": "accounts"}},
        ]), "x")
        self.assertEqual(m["routes"][0]["after"], "i18n")
        self.assertEqual(m["routes"][1]["after"], {"GET": "i18n", "POST": "accounts"})

    def test_v1_bare_string_admin_sections_become_v2_dicts(self):
        m = app_modules._normalize_manifest(self._v1(admin_sections=["cola"]), "x")
        self.assertEqual(m["admin_sections"][0]["id"], "cola")
        self.assertIsNone(m["admin_sections"][0]["label_key"])

    def test_v1_nav_entries_keep_path_and_label(self):
        m = app_modules._normalize_manifest(
            self._v1(nav_entries=[{"path": "/p", "label": "P"}]), "x")
        self.assertEqual(m["nav_entries"][0]["path"], "/p")
        self.assertEqual(m["nav_entries"][0]["label"], "P")
        self.assertIsNone(m["nav_entries"][0]["label_key"])
        self.assertIsNone(m["nav_entries"][0]["order"])

    def test_missing_new_fields_default_empty(self):
        m = app_modules._normalize_manifest(self._v1(), "x")
        self.assertEqual(m["requires"], [])
        self.assertIsNone(m["entrypoint"])
        self.assertEqual(m["assets"], [])
        self.assertIsNone(m["locales"])

    def test_v2_fingerprinted_assets_require_exact_object_keys(self):
        manifest = self._v1(assets=[{
            "path": "modules/x/client.js", "fingerprint": True,
        }])
        normalized = app_modules._normalize_manifest(manifest, "x")
        self.assertEqual(normalized["assets"], [{
            "path": "modules/x/client.js", "fingerprint": True,
        }])
        for invalid in (
            {"path": "modules/x/client.js"},
            {"path": "modules/x/client.js", "fingerprint": True, "extra": "no"},
            {"path": "modules/x/client.js", "fingerprint": False},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    app_modules._normalize_manifest(self._v1(assets=[invalid]), "x")
        for invalid_container in (
            {"path": "modules/x/client.js", "fingerprint": True},
            "modules/x/*.js",
        ):
            with self.subTest(invalid_container=invalid_container):
                with self.assertRaises(ValueError):
                    app_modules._normalize_manifest(self._v1(assets=invalid_container), "x")

    def test_malformed_manifest_raises(self):
        with self.assertRaises(ValueError):
            app_modules._normalize_manifest({"routes": ["x"]}, module_id=None)  # sin id

    def test_not_a_dict_raises(self):
        with self.assertRaises(ValueError):
            app_modules._normalize_manifest(["not", "a", "dict"], "x")

    def test_bad_route_shape_raises(self):
        with self.assertRaises(ValueError):
            app_modules._normalize_manifest(self._v1(routes=[123]), "x")


class TestRoutesMatchRoutingPy(unittest.TestCase):
    """Manifest route-ownership integrity. The two cross-checks against the
    legacy dark registry (`core/routing.py`'s built GET/POST/PUT/DELETE
    lists) retired with the registry itself (native-migration campaign,
    final wave) — what remains is the manifest-side contract: no surface is
    claimed twice, and every declared module really imports. The native
    mounting contract for `router_attr` entries is pinned by
    `TestRouterAttrEntriesResolve` below and `test_app_composition.py`'s
    live route inventory."""

    def setUp(self):
        self.manifests = _load_manifests()

    def test_no_route_is_claimed_by_both_module_and_platform(self):
        for mid, manifest in self.manifests.items():
            overlap = {r["module"] for r in manifest["routes"]} & PLATFORM_ROUTES
            self.assertEqual(overlap, set(), f"{mid} overlaps platform routes: {overlap}")

    def test_no_route_is_claimed_by_two_modules(self):
        seen = {}
        for mid, manifest in self.manifests.items():
            for route in manifest["routes"]:
                # One file may hold several routers (a public one and an admin one): the
                # surface is the module plus the router it mounts.
                name = (route["module"], route.get("router_attr")) if route.get("router_attr") else route["module"]
                self.assertNotIn(name, seen,
                                  f"route {name!r} claimed by both {seen.get(name)!r} and {mid!r}")
                seen[name] = mid

    def test_every_declared_route_module_is_importable(self):
        for mid, manifest in self.manifests.items():
            for route in manifest["routes"]:
                try:
                    importlib.import_module(route["module"])
                except Exception as e:  # noqa: BLE001
                    self.fail(f"{mid}: {route['module']!r} no importa: {e!r}")


class TestRouterAttrEntriesResolve(unittest.TestCase):
    """Tier 4 slice 3 (docs/roadmap/tier4-replatform-design.md, "Module
    contract adapter"): a route entry's `router_attr` names a REAL
    `fastapi.APIRouter` on the SAME dotted module — a manifest/code drift
    check for the new field, same spirit as
    TestRoutesMatchRoutingPy.test_every_declared_route_module_is_importable
    above (which already covers that the module itself imports; this class
    is the extra half `router_attr` needs: the NAMED ATTRIBUTE exists and
    is the right type). Imports `fastapi` — acceptable here because
    test_module_manifest.py already isn't part of the dark-mode boot path
    (it's a test file, not something routing.py imports)."""

    def setUp(self):
        self.manifests = _load_manifests()

    def test_every_router_attr_resolves_to_a_real_api_router(self):
        from fastapi import APIRouter

        found_any = False
        for mid, manifest in self.manifests.items():
            for route in manifest["routes"]:
                attr = route.get("router_attr")
                if not attr:
                    continue
                found_any = True
                mod = importlib.import_module(route["module"])
                self.assertTrue(hasattr(mod, attr),
                                 f"{mid}: {route['module']} no tiene atributo {attr!r}")
                self.assertIsInstance(getattr(mod, attr), APIRouter,
                                      f"{mid}: {route['module']}.{attr} no es un APIRouter")
        self.assertTrue(found_any, "ningún manifiesto declara router_attr — ¿test obsoleto?")

    def test_router_attr_module_is_never_also_the_legacy_handler_module(self):
        # interview_patient_router.py's own docstring: a router_attr entry
        # MUST live in its own file, never share a dotted module with a
        # plain (legacy) route entry — sharing one would mean the legacy
        # per-method registry either imports fastapi unconditionally (if
        # _validate_routes() didn't skip it) or the router file would need
        # to also expose handle(), neither of which this design wants.
        for mid, manifest in self.manifests.items():
            plain = {r["module"] for r in manifest["routes"] if not r.get("router_attr")}
            routed = {r["module"] for r in manifest["routes"] if r.get("router_attr")}
            overlap = plain & routed
            self.assertEqual(overlap, set(),
                              f"{mid}: {overlap} es tanto ruta legacy como router_attr")


class TestScreenRouterEntriesResolve(unittest.TestCase):
    """Tier 5 slice 4 (docs/roadmap/tier5-frontend-design.md, "Module
    contract extension"): an `admin_sections[].screen` entry's
    `router_attr` names a REAL `fastapi.APIRouter` on the SAME dotted
    `module` -- the screen-router sibling of
    TestRouterAttrEntriesResolve above, same manifest/code drift check,
    just reading `admin_sections[].screen` instead of `routes`."""

    def setUp(self):
        self.manifests = _load_manifests()

    def test_every_screen_router_attr_resolves_to_a_real_api_router(self):
        from fastapi import APIRouter

        found_any = False
        for mid, manifest in self.manifests.items():
            for section in manifest["admin_sections"]:
                screen = section.get("screen")
                if not screen:
                    continue
                found_any = True
                mod = importlib.import_module(screen["module"])
                attr = screen["router_attr"]
                self.assertTrue(hasattr(mod, attr),
                                 f"{mid}: {screen['module']} no tiene atributo {attr!r}")
                self.assertIsInstance(getattr(mod, attr), APIRouter,
                                      f"{mid}: {screen['module']}.{attr} no es un APIRouter")
        self.assertTrue(found_any, "ningún manifiesto declara admin_sections[].screen — ¿test obsoleto?")

    def test_screen_path_matches_the_router_attr_endpoint(self):
        for mid, manifest in self.manifests.items():
            for section in manifest["admin_sections"]:
                screen = section.get("screen")
                if not screen:
                    continue
                mod = importlib.import_module(screen["module"])
                router = getattr(mod, screen["router_attr"])
                paths = {r.path for r in router.routes}
                self.assertIn(screen["path"], paths,
                              f"{mid}: screen.path {screen['path']!r} no está entre las "
                              f"rutas de {screen['module']}.{screen['router_attr']}: {paths}")

    def test_screen_template_file_exists_and_extends_the_admin_base(self):
        for mid, manifest in self.manifests.items():
            for section in manifest["admin_sections"]:
                screen = section.get("screen")
                if not screen or not screen.get("template"):
                    continue
                # found the way the loader finds it: a module's own template is named inside the
                # code package ("modules/chat/x.html"), a shared one from the project root
                found = [os.path.join(d, screen["template"]) for d in jinja_env.TEMPLATE_DIRS
                         if os.path.isfile(os.path.join(d, screen["template"]))]
                self.assertTrue(found, f"{mid}: template {screen['template']!r} no existe en "
                                       f"{jinja_env.TEMPLATE_DIRS!r}")
                path = found[0]
                with open(path, "r", encoding="utf-8") as f:
                    text = f.read()
                self.assertIn('extends "admin/base.html"', text,
                              f"{mid}: {screen['template']!r} no extiende el shell admin común")


class TestRequiresAndCycles(unittest.TestCase):
    def setUp(self):
        self.manifests = _load_manifests()

    def test_requires_refer_to_real_modules(self):
        ids = set(self.manifests)
        for mid, manifest in self.manifests.items():
            for dep in manifest["requires"]:
                self.assertIn(dep, ids, f"{mid} depende de un módulo inexistente {dep!r}")

    def test_no_dependency_cycles_today(self):
        cyclic = app_modules._find_cycle_members(
            {mid: m["requires"] for mid, m in self.manifests.items()})
        self.assertEqual(cyclic, {}, f"ciclo de dependencias detectado: {cyclic}")

    def test_cycle_detection_catches_a_synthetic_cycle(self):
        cyclic = app_modules._find_cycle_members({"a": ["b"], "b": ["a"]})
        self.assertIn("a", cyclic)
        self.assertIn("b", cyclic)

    def test_cycle_detection_ignores_edges_to_unknown_modules(self):
        # requires pointing outside the graph is a SEPARATE error
        # (test_requires_refer_to_real_modules above) — not a cycle.
        cyclic = app_modules._find_cycle_members({"a": ["ghost"]})
        self.assertEqual(cyclic, {})


class TestAdminSectionsHaveDirectScreens(unittest.TestCase):
    """Every manifest-declared admin section must have a real direct screen.

    The old source-level registry check scanned ``admin.html`` plus the
    controllers.  That only proved a hash route *could* be registered; it did
    not prove the route could be reached after the shell was retired.  The
    manifest is the authoritative declaration consumed by the native screen
    router, so pin the actual public replacement instead.
    """

    def setUp(self):
        self.manifests = _load_manifests()

    def test_every_manifest_section_declares_a_direct_screen(self):
        for mid, manifest in self.manifests.items():
            for section in manifest["admin_sections"]:
                sid = section["id"]
                screen = section.get("screen")
                self.assertIsInstance(screen, dict,
                                      f"{mid}: admin section {sid!r} has no direct screen")
                self.assertRegex(screen.get("path") or "", r"^/admin/[a-z0-9-]+$")
                self.assertTrue(screen.get("router_attr"),
                                f"{mid}: admin section {sid!r} has no screen router")


class TestDbTablesMatchDbPy(unittest.TestCase):
    def setUp(self):
        self.manifests = _load_manifests()
        self.real_tables = self._db_table_names()

    def _db_table_names(self):
        return snapshots.db_tables()

    def test_every_manifest_table_actually_exists(self):
        for mid, manifest in self.manifests.items():
            missing = set(manifest["db_tables"]) - self.real_tables
            self.assertEqual(missing, set(),
                              f"{mid}'s module.json claims tables db.py doesn't create: {missing}")

    def test_every_real_table_has_one_owner(self):
        claims = {mid: set(manifest["db_tables"])
                  for mid, manifest in self.manifests.items()}
        owners = snapshots.table_owners(self.real_tables, claims)
        self.assertEqual(
            owners, snapshots.load()["owners"],
            "table ownership changed (a new table, or a manifest claim); "
            "run: python3 tests/snapshots.py --write")

    def test_no_table_is_claimed_by_both_module_and_platform(self):
        for mid, manifest in self.manifests.items():
            overlap = set(manifest["db_tables"]) & PLATFORM_ONLY_TABLES
            self.assertEqual(overlap, set(), f"{mid} overlaps platform tables: {overlap}")

    def test_no_table_is_claimed_by_two_modules(self):
        seen = {}
        for mid, manifest in self.manifests.items():
            for table in manifest["db_tables"]:
                self.assertNotIn(table, seen,
                                  f"table {table!r} claimed by both {seen.get(table)!r} and {mid!r}")
                seen[table] = mid


class TestNavEntriesMatchNavJs(unittest.TestCase):
    def setUp(self):
        self.manifests = _load_manifests()
        with open(os.path.join(ROOT, "static/platform/nav.mjs"), encoding="utf-8") as f:
            self.nav_src = f.read()

    def test_every_nav_entry_path_is_a_real_href_in_nav_js(self):
        # Substring check, not an exact `href: "path"` match — nav.js
        # renders some entries (e.g. /my-requests) via a `path === "..."`
        # comparison rather than a literal ITEMS href, so the honest check
        # is just "this path string appears somewhere nav.js recognizes it".
        for mid, manifest in self.manifests.items():
            for entry in manifest["nav_entries"]:
                path = entry["path"]
                self.assertIn(f'"{path}"', self.nav_src,
                               f"{mid}: nav entry {path!r} not found anywhere in nav.js")


class TestConfigKeyPrefixesAreReal(unittest.TestCase):
    def setUp(self):
        self.manifests = _load_manifests()

    def _all_py_source(self):
        paths = [os.path.join(ROOT, f) for f in os.listdir(ROOT) if f.endswith(".py")]
        # Tier 2 batch K: config.py/db.py (where most config-key prefixes are
        # actually read/checked) moved off ROOT into core/ — walk it too, same
        # shape the `modules/` walk below already had.
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "app", "core")):
            paths += [os.path.join(dirpath, f) for f in files if f.endswith(".py")]
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "app", "modules")):
            paths += [os.path.join(dirpath, f) for f in files if f.endswith(".py")]
        # 2026-08-16 (WebTurno-client retirement): the WEBTURNO_* reads now
        # live ONLY in connectors/webturno/ — server.py stopped being a
        # client of the connector, so the scan must cover connectors/ for
        # the declared prefixes to keep matching real usage.
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "app", "connectors")):
            paths += [os.path.join(dirpath, f) for f in files if f.endswith(".py")]
        combined = ""
        for path in paths:
            with open(path, encoding="utf-8", errors="ignore") as f:
                combined += f.read()
        return combined

    def test_every_prefix_has_at_least_one_real_usage(self):
        combined = self._all_py_source()
        for mid, manifest in self.manifests.items():
            for prefix in manifest["config_keys_prefixes"]:
                self.assertIn(prefix, combined,
                               f"{mid}: prefix {prefix!r} has no usage in repo-root or modules/ .py files")

    def test_every_connector_config_key_matches_a_declared_prefix(self):
        # Ties the manifests to the connector registry built in Phase 6b:
        # every config_keys entry any connector.json declares must fall under
        # one of the declared module prefixes — catches a future connector
        # that invents an unprefixed, undocumented env var. (Connectors are
        # integration capabilities of turnos today, so its prefixes are the
        # relevant set; a connector used by another module would just need
        # that module's prefix added here.)
        connectors_dir = os.path.join(ROOT, "app", "connectors")
        prefixes = []
        for manifest in self.manifests.values():
            prefixes += list(manifest["config_keys_prefixes"])
        prefixes = tuple(prefixes)
        checked = 0
        for entry in os.listdir(connectors_dir):
            manifest_path = os.path.join(connectors_dir, entry, "connector.json")
            if not os.path.isfile(manifest_path):
                continue
            with open(manifest_path, encoding="utf-8") as f:
                doc = json.load(f)
            for key in doc.get("config_keys") or []:
                checked += 1
                self.assertTrue(key.startswith(prefixes),
                                 f"{entry}'s config key {key!r} doesn't match any declared module prefix")
        self.assertGreater(checked, 0, "no connector config_keys were found to check")


if __name__ == "__main__":
    unittest.main()
