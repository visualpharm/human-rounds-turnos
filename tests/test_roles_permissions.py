"""Comprehensive permissions and roles scenarios.

Test role creation, assignment, overrides, lockout, and all access control
decision paths: lookup failure (503), unknown role (403), disabled role (403),
retired tokens (401), permission grants/denies, role CRUD operations.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-perms")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")

import json
import unittest

import _dbtest  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.core import permissions as perm  # noqa: E402
from app.core import db, config  # noqa: E402
from app.core import db_operators  # noqa: E402


_db = _dbtest.PerModuleDb("sana_turnos_test_roles_permissions",
                           seed=lambda: perm.list_roles())


_env.process_set({"TURNOS_ADMIN_SECRET": "test-only-permissions-signing-key"})

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


def _mint_token_for(usuario, rol):
    """Create a session token for a user with a specific role."""
    return admin_auth._mint_token(usuario, rol)


def _gate(usuario, rol, method="GET", path="/admin/users", route="/admin/users", **flags):
    """Call evaluate_admin_gate with a token for (usuario, rol)."""
    token = _mint_token_for(usuario, rol)
    return admin_auth.evaluate_admin_gate(
        token, method, path, route=route,
        solo_admin=flags.get("solo_admin", False),
        permitir_medico=flags.get("permitir_medico", False),
        permitir_admisionista=flags.get("permitir_admisionista", False),
        permitir_reportes=flags.get("permitir_reportes", False)
    )


@_requires_db
class TestLookupFailure(unittest.TestCase):
    """DB errors during access_for degrade to the pre-roles, role-based gate.

    Decisión del dueño (2026-09-12): con el almacén de permisos ilegible cada
    endpoint sigue respondiendo lo que su contrato ya promete; los permisos
    por usuario no aplican hasta que vuelva."""

    def setUp(self):
        _env.set_env(self, DATABASE_URL=_TEST_URL)

    def test_lookup_failure_degrades_to_role(self):
        """When access_for raises, an admin still gets through."""
        with self.patch_access_for_to_raise():
            gate = _gate("some_user", "admin", solo_admin=True)
            self.assertTrue(gate.ok)
            self.assertEqual(gate.identity.get("rol"), "admin")

    def test_lookup_failure_keeps_role_denial(self):
        """A role the pre-roles gate denied is still denied, with its text."""
        with self.patch_access_for_to_raise():
            gate = _gate("some_user", "medico", solo_admin=True)
            self.assertFalse(gate.ok)
            self.assertEqual(gate.status, 403)
            self.assertEqual(gate.body.get("error"), "solo_admin")

    def patch_access_for_to_raise(self):
        """Context manager that patches access_for to fail the way the real
        permission store fails: `db.DbUnavailable`. The gate degrades on THAT
        and only that — a bare `except Exception` there would turn any bug in
        `access_for` into a silently granted permission, so the simulation has
        to use the real outage class instead of a generic error."""
        from app.core import db as _db

        class PatchContext:
            def __enter__(self):
                self.original = perm.access_for
                perm.access_for = lambda u, r: (_ for _ in ()).throw(
                    _db.DbUnavailable("test db error"))
                return self
            def __exit__(self, *args):
                perm.access_for = self.original
        return PatchContext()


@_requires_db
class TestRoleStates(unittest.TestCase):
    """Unknown role (403 rol_no_autorizado) and disabled role (403 rol_deshabilitado)."""

    def setUp(self):
        _env.set_env(self, DATABASE_URL=_TEST_URL)
        # Create a test operator (idempotent)
        try:
            db_operators.operator_create("u_test", "password", "admin", "test@example.com")
        except db_operators.OperatorConflict:
            pass

    def test_unknown_role_returns_403(self):
        """A role that doesn't exist in the database -> 403."""
        gate = _gate("u_unknown", "unknown_role")
        self.assertFalse(gate.ok)
        self.assertEqual(gate.status, 403)
        self.assertEqual(gate.body.get("error"), "rol_no_autorizado")

    def test_disabled_role_returns_403(self):
        """A role that exists but is disabled -> 403."""
        # Create an operator with admisionista role (use unique username)
        u_disabled = "u_disabled_%d" % id(self)
        try:
            db_operators.operator_create(u_disabled, "password", "admisionista", "disabled@example.com")
        except db_operators.OperatorConflict:
            pass
        # Disable the admisionista role
        try:
            perm.update_role("admisionista", {"enabled": False})
            gate = _gate(u_disabled, "admisionista")
            self.assertFalse(gate.ok)
            self.assertEqual(gate.status, 403)
            self.assertEqual(gate.body.get("error"), "rol_deshabilitado")
        finally:
            perm.update_role("admisionista", {"enabled": True})


@_requires_db
class TestRetiredRoles(unittest.TestCase):
    """Retired token roles (operador/marca) -> 401."""

    def test_retired_operador_role_returns_401(self):
        """A cookie with retired 'operador' role is rejected."""
        token = _mint_token_for("u_op", "operador")
        gate = admin_auth.evaluate_admin_gate(token, "GET", "/admin/users", route="/admin/users")
        self.assertFalse(gate.ok)
        self.assertEqual(gate.status, 401)

    def test_retired_marca_role_returns_401(self):
        """A cookie with retired 'marca' role is rejected."""
        token = _mint_token_for("u_mk", "marca")
        gate = admin_auth.evaluate_admin_gate(token, "GET", "/admin/users", route="/admin/users")
        self.assertFalse(gate.ok)
        self.assertEqual(gate.status, 401)


@_requires_db
class TestGestorAgendas(unittest.TestCase):
    """Gestor de agendas role: admission + doctor editor, no settings/system/users."""

    def setUp(self):
        _env.set_env(self, DATABASE_URL=_TEST_URL)
        try:
            db_operators.operator_create("gestor", "pass", "admisionista", "gestor@test.com")
        except db_operators.OperatorConflict:
            pass
        # Assign gestor_agendas role via operator_access
        perm.set_overrides("gestor", [], [])
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO operator_access (username, role_slug) VALUES (%s, %s)"
                " ON CONFLICT (username) DO UPDATE SET role_slug = EXCLUDED.role_slug",
                ("gestor", "gestor_agendas")
            )

    def test_gestor_accesses_doctors(self):
        """Gestor can edit doctors (medicos permission)."""
        gate = _gate("gestor", "gestor_agendas", "GET", "/admin/doctors", "/admin/doctors")
        self.assertTrue(gate.ok)
        self.assertIn("medicos", gate.permissions)

    def test_gestor_accesses_admision(self):
        """Gestor can handle admision (solicitudes, cola, crear_turnos)."""
        gate = _gate("gestor", "gestor_agendas", "GET", "/admin/solicitudes", "/admin/solicitudes")
        self.assertTrue(gate.ok)
        self.assertIn("solicitudes", gate.permissions)

    def test_gestor_denied_settings(self):
        """Gestor cannot access settings."""
        gate = _gate("gestor", "gestor_agendas", "GET", "/admin/settings", "/admin/settings")
        self.assertFalse(gate.ok)
        self.assertEqual(gate.status, 403)
        self.assertEqual(gate.body.get("error"), "rol_no_autorizado")

    def test_gestor_denied_system(self):
        """Gestor cannot access system."""
        gate = _gate("gestor", "gestor_agendas", "GET", "/admin/system", "/admin/system")
        self.assertFalse(gate.ok)
        self.assertEqual(gate.status, 403)

    def test_gestor_denied_users(self):
        """Gestor cannot manage users."""
        gate = _gate("gestor", "gestor_agendas", "GET", "/admin/users", "/admin/users")
        self.assertFalse(gate.ok)
        self.assertEqual(gate.status, 403)

    def test_gestor_reads_doctor_stats(self):
        """Hallazgo K8 del mapa del hospital: la lista de Médicos del gestor
        quedaba con las métricas vacías porque /api/admin/doctors/stats pedía
        `reportes`. Son datos de agenda (sin pacientes): alcanza `medicos`."""
        gate = _gate("gestor", "gestor_agendas", "GET", "/api/admin/doctors/stats",
                     "/api/admin/doctors/stats", permitir_reportes=True)
        self.assertTrue(gate.ok, gate.body)

    def test_doctor_stats_still_denied_without_medicos_or_reportes(self):
        """Ampliar a `medicos` no abre las métricas a la mesa ni al médico."""
        for usuario, rol in (("mesa_k8", "admisionista"), ("medico_k8", "medico")):
            try:
                db_operators.operator_create(usuario, "pass", rol, usuario + "@test.com")
            except db_operators.OperatorConflict:
                pass
            with self.subTest(rol=rol):
                gate = _gate(usuario, rol, "GET", "/api/admin/doctors/stats",
                             "/api/admin/doctors/stats", permitir_reportes=True)
                self.assertFalse(gate.ok)
                self.assertEqual(gate.status, 403)

    def test_any_of_requirement_is_held_by_either_permission(self):
        required = perm.required_permission("GET", "/api/admin/doctors/stats")
        self.assertTrue(perm.holds(required, {"medicos"}))
        self.assertTrue(perm.holds(required, {"reportes"}))
        self.assertFalse(perm.holds(required, {"solicitudes", "cola"}))


@_requires_db
class TestCustomRole(unittest.TestCase):
    """Create a custom role and verify access control."""

    def setUp(self):
        _env.set_env(self, DATABASE_URL=_TEST_URL)

    def test_create_custom_role_with_reportes_only(self):
        """Create a role that only has reportes permission."""
        # Use a unique role name per test run to avoid duplicates
        role_name = "Reportero Personalizado"
        roles = perm.list_roles()
        existing = [r for r in roles if r["name"] == role_name]
        if existing:
            role = existing[0]
        else:
            role = perm.create_role(role_name, "Solo ve reportes", ["reportes"])
        self.assertEqual(role["slug"], "reportero_personalizado")
        self.assertEqual(role["permissions"], ["reportes"])

        # Assign it to a user and verify access
        try:
            db_operators.operator_create("reporter", "pass", "admisionista", "rep@test.com")
        except db_operators.OperatorConflict:
            pass
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO operator_access (username, role_slug) VALUES (%s, %s)"
                " ON CONFLICT (username) DO UPDATE SET role_slug = EXCLUDED.role_slug",
                ("reporter", "reportero_personalizado")
            )

        # Should access reports
        gate = _gate("reporter", "reportero_personalizado", "GET", "/admin/reportes", "/admin/reportes")
        self.assertTrue(gate.ok)
        self.assertIn("reportes", gate.permissions)

        # Should NOT access requests
        gate = _gate("reporter", "reportero_personalizado", "GET", "/admin/solicitudes", "/admin/solicitudes")
        self.assertFalse(gate.ok)

    def test_role_name_with_script_tags_is_escaped(self):
        """Role name with XSS payload is stored and returned as plain text."""
        role = perm.create_role("<script>alert(1)</script>", "Test", ["reportes"])
        self.assertEqual(role["name"], "<script>alert(1)</script>")
        retrieved = perm.get_role(role["slug"])
        self.assertEqual(retrieved["name"], "<script>alert(1)</script>")


@_requires_db
class TestOverrides(unittest.TestCase):
    """Per-user grant/deny overrides on top of role preset."""

    def setUp(self):
        _env.set_env(self, DATABASE_URL=_TEST_URL)
        try:
            db_operators.operator_create("u_override", "pass", "admisionista", "o@test.com")
        except db_operators.OperatorConflict:
            pass

    def test_grant_override_adds_permission(self):
        """granted: adds permission to effective set."""
        perm.set_overrides("u_override", ["reportes"], [])
        gate = _gate("u_override", "admisionista", "GET", "/admin/reportes", "/admin/reportes")
        self.assertTrue(gate.ok)
        self.assertIn("reportes", gate.permissions)

    def test_deny_override_removes_permission(self):
        """denied: removes permission from effective set."""
        # admisionista has 'solicitudes' by default
        perm.set_overrides("u_override", [], ["solicitudes"])
        gate = _gate("u_override", "admisionista", "GET", "/admin/solicitudes", "/admin/solicitudes")
        self.assertFalse(gate.ok)

    def test_grant_and_deny_together_fails(self):
        """A permission can't be both granted and denied."""
        with self.assertRaises(perm.RoleError) as cm:
            perm.set_overrides("u_override", ["reportes"], ["reportes"])
        self.assertEqual(cm.exception.code, "override_conflicto")

    def test_overrides_apply_next_request(self):
        """Role change applies on next request, no re-login needed."""
        perm.set_overrides("u_override", ["reportes"], [])
        gate = _gate("u_override", "admisionista", "GET", "/admin/reportes", "/admin/reportes")
        self.assertTrue(gate.ok, "Granted permission should work immediately")


@_requires_db
class TestLockout(unittest.TestCase):
    """Lockout: demoting/denying usuarios on the last manager fails."""

    def setUp(self):
        _env.set_env(self, DATABASE_URL=_TEST_URL)
        try:
            db_operators.operator_create("u_admin", "pass", "admin", "admin@test.com")
        except db_operators.OperatorConflict:
            pass

    def test_cannot_demote_last_usuarios_manager(self):
        """Can't change the admin role: it's locked and always has all permissions."""
        # admin role is immutable
        with self.assertRaises(perm.RoleError) as cm:
            perm.update_role("admin", {"permissions": []})
        self.assertEqual(cm.exception.code, "rol_bloqueado")

    def test_cannot_disable_last_usuarios_manager_role(self):
        """Can't disable the admin role: it's locked and immutable."""
        with self.assertRaises(perm.RoleError) as cm:
            perm.update_role("admin", {"enabled": False})
        self.assertEqual(cm.exception.code, "rol_bloqueado")

    def test_can_remove_permission_if_another_manager_exists(self):
        """Can remove usuarios permission from an operator if another holds it."""
        try:
            db_operators.operator_create("u_second_admin", "pass", "admin", "sa@test.com")
        except db_operators.OperatorConflict:
            pass
        # Now two people have usuarios (via admin role)
        # We should be able to deny usuarios from u_admin via overrides
        # since u_second_admin still has it
        perm.set_overrides("u_admin", [], ["usuarios"])
        # Verify u_admin no longer has usuarios permission
        granted, denied = perm.get_overrides("u_admin")
        self.assertIn("usuarios", denied)


@_requires_db
class TestRolesCRUD(unittest.TestCase):
    """Roles API: create, list, get, update, delete."""

    def setUp(self):
        _env.set_env(self, DATABASE_URL=_TEST_URL)

    def test_list_roles_includes_builtins(self):
        """list_roles returns the 5 built-in roles."""
        roles = perm.list_roles()
        slugs = {r["slug"] for r in roles}
        self.assertTrue({"admin", "medico", "admisionista", "reportes", "gestor_agendas"}.issubset(slugs))

    def test_create_role_generates_unique_slug(self):
        """create_role generates a slug from the name."""
        r1 = perm.create_role("Test Role", "A test", ["reportes"])
        self.assertIn(r1["slug"], ["test_role", "rol_test_role"])
        self.assertTrue(r1["slug"].islower())

    def test_create_role_with_duplicate_name_fails(self):
        """Can't create two roles with the same name."""
        # Use a unique name per test run to avoid conflicts
        unique_name = "UniqueTest%d" % id(self)
        perm.create_role(unique_name, "First", ["reportes"])
        with self.assertRaises(perm.RoleError) as cm:
            perm.create_role(unique_name, "Second", ["reportes"])
        self.assertEqual(cm.exception.code, "rol_existe")

    def test_delete_role_only_if_unused(self):
        """Can only delete custom roles that nobody holds."""
        role = perm.create_role("Temp Role", "Temporary", ["reportes"])
        u_temp = "u_temp_%d" % id(self)
        try:
            db_operators.operator_create(u_temp, "pass", "admisionista", "t@test.com")
        except db_operators.OperatorConflict:
            pass
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO operator_access (username, role_slug) VALUES (%s, %s)"
                " ON CONFLICT (username) DO UPDATE SET role_slug = EXCLUDED.role_slug",
                (u_temp, role["slug"])
            )

        # Can't delete while in use
        with self.assertRaises(perm.RoleError) as cm:
            perm.delete_role(role["slug"])
        self.assertEqual(cm.exception.code, "rol_en_uso")

        # Can delete once unused
        with db._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM operator_access WHERE username = %s", (u_temp,))
        perm.delete_role(role["slug"])
        retrieved = perm.get_role(role["slug"])
        self.assertIsNone(retrieved)

    def test_cannot_delete_builtin_role(self):
        """Built-in roles can't be deleted."""
        with self.assertRaises(perm.RoleError) as cm:
            perm.delete_role("admin")
        self.assertEqual(cm.exception.code, "rol_de_fabrica")

    def test_cannot_edit_admin_role_name(self):
        """Admin role is locked and can't be edited."""
        with self.assertRaises(perm.RoleError) as cm:
            perm.update_role("admin", {"name": "Different"})
        self.assertEqual(cm.exception.code, "rol_bloqueado")


@_requires_db
class TestFreshSchemaRoles(unittest.TestCase):
    """Fresh schema roles equal permissions.BUILTIN_ROLES."""

    def setUp(self):
        _env.set_env(self, DATABASE_URL=_TEST_URL)

    def test_schema_seeds_all_builtins(self):
        """Fresh schema has all 5 built-in roles."""
        roles = {r["slug"]: r for r in perm.list_roles()}
        for slug, expected in perm.BUILTIN_ROLES.items():
            with self.subTest(role=slug):
                self.assertIn(slug, roles)
                role = roles[slug]
                self.assertEqual(set(role["permissions"]), set(expected["permissions"]))
                self.assertTrue(role["builtin"])
                self.assertTrue(role["enabled"])

    def test_admin_role_is_always_locked(self):
        """Admin role can't be edited; always holds all permissions."""
        role = perm.get_role("admin")
        self.assertTrue(role["locked"])
        self.assertEqual(set(role["permissions"]), perm.ALL)


if __name__ == "__main__":
    unittest.main()
