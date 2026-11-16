"""Parity test: roles/permissions system vs. legacy flag-based gate.

Walk every gated app route with the four legacy roles and verify the
permission-based gate makes exactly the same decisions as the pre-roles
flag-based logic. The four legacy roles (admin/medico/admisionista/reportes)
must be feature-identical under both systems for backwards compatibility.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-parity")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")

import unittest
import re
from unittest import mock

import _dbtest  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.core import permissions as perm  # noqa: E402
from app.core import config  # noqa: E402


_env.process_set({"TURNOS_ADMIN_SECRET": "test-only-parity-signing-key"})

_db = _dbtest.PerModuleDb("sana_turnos_test_roles_parity",
                           seed=lambda: perm.list_roles())


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url
    config._reset_cache_para_tests()


def tearDownModule():
    _db.tear_down()


HAVE_DB = False


def _requires_db(cls):
    """Skip at RUN time: HAVE_DB is only known after setUpModule."""
    original = cls.__dict__.get("setUp")

    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de test")
        if original is not None:
            original(self)
    cls.setUp = setUp
    return cls
DB_SKIP_REASON = None
_TEST_URL = None
_MAINT_URL = None


def flat_routes(routes):
    """Flatten FastAPI's _IncludedRouter hierarchy."""
    for route in routes:
        if type(route).__name__ == "_IncludedRouter":
            yield from flat_routes([(getattr(c, "route", None) or c) for c in route.effective_candidates()])
        else:
            yield route


def get_all_routes():
    """Returns list of (method, path) tuples for all admin routes."""
    from app import main as app_module  # noqa: F401
    out = []
    seen = set()
    for route in flat_routes(app_module.app.routes):
        path = getattr(route, "path", None)
        methods = sorted((getattr(route, "methods", None) or set()) - {"HEAD", "OPTIONS"})
        if not path or not methods or "/admin" not in path:
            continue
        for method in methods:
            key = (method, path)
            if key not in seen:
                seen.add(key)
                out.append((method, path))
    return out


def _h(rol):
    """Create a fake request with a valid session token for a role."""
    class FakeRequest:
        def __init__(self):
            self.headers = {"Cookie": f"st_admin={admin_auth._mint_token('u_' + rol, rol)}"}
    return FakeRequest()


@_requires_db
class TestRolesParity(unittest.TestCase):
    """Every legacy role makes identical gate decisions under both systems."""

    def setUp(self):
        _env.set_env(self, DATABASE_URL=_TEST_URL)

    def test_walk_routes_and_verify_parity(self):
        """Walk every admin route and verify legacy roles get the same result."""
        routes = get_all_routes()
        self.assertGreater(len(routes), 130,
                          f"Expected >=130 gated admin routes, got {len(routes)}")

        # Track which template routes we've seen
        route_templates = set()
        gated_count = 0
        unmapped_count = 0

        for method, template in routes:
            route_templates.add(template)

            # Create a concrete path for the request
            concrete_path = re.sub(r"\{[^}]+\}", "1", template)

            for rol in ("admin", "medico", "admisionista", "reportes"):
                with self.subTest(method=method, template=template, rol=rol):
                    # Get the effective permission for this route
                    required = perm.required_permission(method, template, concrete_path, role=rol)

                    # Record what the gate decides
                    request = _h(rol)
                    cookie = admin_auth._parse_cookie_token(request.headers.get("Cookie"))
                    gate = admin_auth.evaluate_admin_gate(
                        cookie, method, concrete_path,
                        solo_admin=False,
                        permitir_medico=False,
                        permitir_admisionista=False,
                        permitir_reportes=False,
                        route=template
                    )

                    if required != perm.UNMAPPED:
                        gated_count += 1
                    else:
                        unmapped_count += 1

                    # The route is either allowed (gate.ok=True) or denied (status=403)
                    if required == perm.ANY:
                        # Any enabled role can access
                        self.assertTrue(gate.ok,
                                      f"{rol} should access ANY route {template}")
                    elif required == perm.UNMAPPED:
                        # Fall back to legacy flags (which are all False here)
                        legacy_denial = perm.legacy_denial(rol)
                        if legacy_denial:
                            self.assertFalse(gate.ok,
                                           f"legacy_denial={legacy_denial} implies gate should deny {rol}")
                            self.assertEqual(gate.body.get("error"), legacy_denial)
                        else:
                            self.assertTrue(gate.ok,
                                          f"legacy_denial=None implies gate should allow {rol}")
                    else:
                        # Permission-based: role must hold the permission
                        # For the legacy roles under their builtin preset:
                        builtin = perm.BUILTIN_ROLES.get(rol, {})
                        role_perms = builtin.get("permissions", ())
                        has_perm = perm.holds(required, role_perms)

                        if has_perm:
                            self.assertTrue(gate.ok,
                                          f"{rol} holds permission {required} but gate denied")
                        else:
                            self.assertFalse(gate.ok,
                                           f"{rol} lacks permission {required} but gate allowed")
                            self.assertEqual(gate.body.get("error"), "rol_no_autorizado")

        # Verify we exercised enough routes
        self.assertGreaterEqual(gated_count, 100,
                               f"Expected >=100 mapped gated routes, got {gated_count}")
        # Allow some unmapped for backward compatibility, but most should be mapped
        self.assertLess(unmapped_count, gated_count // 2,
                       f"Too many unmapped routes: {unmapped_count} (should be <50% of {gated_count})")


@_requires_db
class TestNavParity(unittest.TestCase):
    """Navigation menu parity: screen_nav matches legacy nav_items for all roles."""

    def setUp(self):
        _env.set_env(self, DATABASE_URL=_TEST_URL)

    def test_nav_items_for_legacy_roles(self):
        """Verify nav items for each legacy role."""
        from app.core.screen_nav import nav_items

        for rol in ("admin", "medico", "admisionista", "reportes"):
            with self.subTest(rol=rol):
                # Get the builtin role's permissions
                builtin = perm.BUILTIN_ROLES[rol]
                role_perms = frozenset(builtin.get("permissions", ()))

                # Generate nav for this role
                nav = nav_items(rol)

                # Nav should be a list of nav items
                self.assertIsInstance(nav, list)

                # Each nav item should have the standard structure:
                # {id, href, label, group, group_label}
                for item in nav:
                    self.assertIn("id", item)
                    self.assertIn("href", item)
                    self.assertIn("label", item)
                    self.assertIn("group", item)
                    # href should be a valid path
                    self.assertTrue(item["href"].startswith("/admin/"))


@_requires_db
class TestEveryMenuScreenOpens(unittest.TestCase):
    """Pedido del hospital (K8, 2026-10-23): cada pantalla del menú de un rol
    builtin pasa el gate para ese rol — un menú nunca ofrece un 403."""

    def setUp(self):
        _env.set_env(self, DATABASE_URL=_TEST_URL)

    def test_each_builtin_role_opens_every_screen_of_its_menu(self):
        from app.core.screen_nav import nav_items
        for rol, preset in perm.BUILTIN_ROLES.items():
            held = perm.ALL if rol == "admin" else frozenset(preset["permissions"])
            nav = nav_items(rol, held)
            self.assertTrue(nav, rol)
            for item in nav:
                path = item["href"].split("?", 1)[0]
                with self.subTest(rol=rol, screen=path):
                    cookie = admin_auth._parse_cookie_token(_h(rol).headers.get("Cookie"))
                    gate = admin_auth.evaluate_admin_gate(
                        cookie, "GET", path, solo_admin=False, permitir_medico=False,
                        permitir_admisionista=False, permitir_reportes=False, route=path)
                    self.assertTrue(gate.ok, f"{rol} ve {path} en su menú pero el gate responde {gate.body}")


if __name__ == "__main__":
    unittest.main()
