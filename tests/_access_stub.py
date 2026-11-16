"""Stub for permissions.access_for used in DB-less tests.

Resolves roles through BUILTIN_ROLES presets instead of the database.
"""
from app.core import permissions as perm


def access_for_stub(usuario, token_rol):
    """Return mock access data for a role.

    Resolves token_rol through BUILTIN_ROLES. Unknown slugs return
    state="unknown". Built-in roles are never disabled in this stub.
    """
    if token_rol not in perm.BUILTIN_ROLES:
        return {"slug": token_rol, "state": "unknown", "permissions": frozenset()}

    role_def = perm.BUILTIN_ROLES[token_rol]
    permissions = frozenset(role_def.get("permissions", ()))

    return {
        "slug": token_rol,
        "state": "ok",
        "permissions": permissions,
    }


def patch_access_for_with_stub():
    """Context manager that patches permissions.access_for with the stub.

    Use in DB-less tests that call the admin gate.
    """
    class StubContext:
        def __enter__(self):
            self.original = perm.access_for
            perm.access_for = access_for_stub
            return self

        def __exit__(self, *args):
            perm.access_for = self.original

    return StubContext()
