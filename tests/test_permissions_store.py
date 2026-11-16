"""test_permissions_store.py — the storage statements of `core/permissions.py` against a real Postgres.

Stage 5 of the SQLAlchemy Core move converts the permission store: the `roles` CRUD
(`list_roles`, `get_role`, `role_is_assignable`, `create_role`, `update_role`, `delete_role`
and their helpers), the per-account overrides (`get_overrides`, `set_overrides`), the effective
access of an account that the gate reads on EVERY panel request (`access_for`) and the
last-user-manager guard (`_snapshot`, `_manager_count`, `assert_user_manager_remains`). This is
a security boundary — who sees what in the admin panel — so this file pins the exact returned
shapes and every deny path (disabled account, disabled / unknown role, denied and granted
overrides, the admin role that cannot be edited, a role that still has people, the last manager)
row for row, so the conversion is proven to change nothing. It was written and run green on the
hand-written SQL first, and passes unedited on Core.

`test_roles_permissions.py` holds the gate-level scenarios; this file does not repeat them.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db  # noqa: E402
from app.core import db_schema  # noqa: E402
from app.core import permissions as perm  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_permissions_store")

LONG_AGO = datetime(2020, 1, 1, tzinfo=timezone.utc)


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _sql(sql, params=None):
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else []


def _reset():
    """No accounts, no overrides, only the five built-in roles at their preset."""
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE operators, operator_access RESTART IDENTITY CASCADE")
        cur.execute("DELETE FROM roles")
    with db._connect() as conn:
        db_schema._seed_builtin_roles(conn)


def _role(slug, name=None, permissions=(), enabled=True, builtin=False, description="d"):
    _sql("INSERT INTO roles (slug, name, description, builtin, enabled, permissions) "
         "VALUES (%s, %s, %s, %s, %s, %s)",
         (slug, name or slug, description, builtin, enabled, list(permissions)))


def _op(username, role="admisionista", active=True, slug=None, granted=None, denied=None):
    """One staff account, optionally with an access row (custom slug and/or overrides)."""
    _sql("INSERT INTO operators (username, pass_hash, role, active) VALUES (%s, 'x', %s, %s)",
         (username, role, active))
    if slug is not None or granted or denied:
        _sql("INSERT INTO operator_access (username, role_slug, granted, denied) VALUES (%s, %s, %s, %s)",
             (username, slug, list(granted or ()), list(denied or ())))


def _stored(slug):
    rows = _sql("SELECT * FROM roles WHERE slug=%s", (slug,))
    return rows[0] if rows else None


class StoreCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset()

    def assertRoleError(self, code, field, status, fn, *args, **kw):
        with self.assertRaises(perm.RoleError) as cm:
            fn(*args, **kw)
        self.assertEqual((cm.exception.code, cm.exception.field, cm.exception.status),
                         (code, field, status))
        return cm.exception


# ---- access_for: the effective access read on every panel request -----------

class TestAccessFor(StoreCase):
    def test_active_legacy_admin_holds_every_permission(self):
        _op("ana", "admin")
        got = perm.access_for("ana", "ignored-token-role")
        self.assertEqual(got, {"slug": "admin", "state": "ok", "permissions": perm.ALL})
        self.assertIsInstance(got["permissions"], frozenset)

    def test_the_database_role_wins_over_the_signed_one(self):
        _op("ana", "reportes")
        got = perm.access_for("ana", "admin")
        self.assertEqual(got, {"slug": "reportes", "state": "ok", "permissions": frozenset({"reportes"})})

    def test_the_preset_of_a_builtin_role_is_what_the_row_stores(self):
        _op("mesa", "admisionista")
        self.assertEqual(perm.access_for("mesa", "x")["permissions"],
                         frozenset({"solicitudes", "cola", "crear_turnos", "pacientes"}))
        _sql("UPDATE roles SET permissions=%s WHERE slug='admisionista'", (["reportes"],))
        self.assertEqual(perm.access_for("mesa", "x")["permissions"], frozenset({"reportes"}))

    def test_a_deactivated_account_is_inactive_with_no_permissions(self):
        _op("off", "admin", active=False)
        self.assertEqual(perm.access_for("off", "admin"),
                         {"slug": "admin", "state": "inactive", "permissions": frozenset()})

    def test_a_deactivated_account_reports_its_table_role_not_its_custom_slug(self):
        _role("jefatura", permissions=["usuarios"])
        _op("off", "admisionista", active=False, slug="jefatura", granted=["reportes"])
        self.assertEqual(perm.access_for("off", "jefatura"),
                         {"slug": "admisionista", "state": "inactive", "permissions": frozenset()})

    def test_deactivated_stays_inactive_even_when_its_role_is_disabled_or_gone(self):
        _op("off", "reportes", active=False)
        _sql("UPDATE roles SET enabled=false WHERE slug='reportes'")
        self.assertEqual(perm.access_for("off", "reportes")["state"], "inactive")
        _sql("DELETE FROM roles WHERE slug='reportes'")
        self.assertEqual(perm.access_for("off", "reportes")["state"], "inactive")

    def test_custom_role_slug_replaces_the_table_role(self):
        _role("jefatura", permissions=["usuarios", "reportes"])
        _op("jefe", "admisionista", slug="jefatura")
        self.assertEqual(perm.access_for("jefe", "admisionista"),
                         {"slug": "jefatura", "state": "ok",
                          "permissions": frozenset({"usuarios", "reportes"})})

    def test_an_access_row_without_slug_keeps_the_table_role(self):
        _op("mesa", "admisionista", granted=["reportes"])
        got = perm.access_for("mesa", "x")
        self.assertEqual(got["slug"], "admisionista")
        self.assertEqual(got["permissions"],
                         frozenset({"solicitudes", "cola", "crear_turnos", "pacientes", "reportes"}))

    def test_granted_adds_and_denied_removes(self):
        _op("mesa", "admisionista", granted=["reportes", "hsi"], denied=["solicitudes", "cola"])
        self.assertEqual(perm.access_for("mesa", "x")["permissions"],
                         frozenset({"crear_turnos", "pacientes", "reportes", "hsi"}))

    def test_unknown_permission_ids_never_reach_the_set(self):
        _role("raro", permissions=["reportes", "inexistente"])
        _op("u", "admisionista", slug="raro", granted=["fantasma"], denied=["otro"])
        self.assertEqual(perm.access_for("u", "x")["permissions"], frozenset({"reportes"}))

    def test_denied_wins_over_the_role_even_for_admin(self):
        _op("ana", "admin", denied=["usuarios"])
        got = perm.access_for("ana", "admin")
        self.assertEqual(got["state"], "ok")
        self.assertEqual(got["permissions"], perm.ALL - {"usuarios"})

    def test_admin_ignores_what_its_row_stores(self):
        _sql("UPDATE roles SET permissions=%s WHERE slug='admin'", ([],))
        _op("ana", "admin")
        self.assertEqual(perm.access_for("ana", "admin")["permissions"], perm.ALL)

    def test_disabled_role_denies_even_with_grants(self):
        _op("mesa", "admisionista", granted=["usuarios"])
        _sql("UPDATE roles SET enabled=false WHERE slug='admisionista'")
        self.assertEqual(perm.access_for("mesa", "admisionista"),
                         {"slug": "admisionista", "state": "disabled", "permissions": frozenset()})

    def test_a_disabled_custom_role_denies(self):
        _role("jefatura", permissions=["usuarios"], enabled=False)
        _op("jefe", "admisionista", slug="jefatura")
        self.assertEqual(perm.access_for("jefe", "x"),
                         {"slug": "jefatura", "state": "disabled", "permissions": frozenset()})

    def test_the_table_role_with_no_roles_row_is_unknown(self):
        _op("rep", "reportes")
        _sql("DELETE FROM roles WHERE slug='reportes'")
        self.assertEqual(perm.access_for("rep", "reportes"),
                         {"slug": "reportes", "state": "unknown", "permissions": frozenset()})

    def test_no_operator_row_keeps_the_signed_role_without_overrides(self):
        self.assertEqual(perm.access_for("fantasma", "reportes"),
                         {"slug": "reportes", "state": "ok", "permissions": frozenset({"reportes"})})
        self.assertEqual(perm.access_for("fantasma", "admin")["permissions"], perm.ALL)

    def test_no_operator_row_with_an_unknown_signed_role_is_unknown(self):
        self.assertEqual(perm.access_for("fantasma", "nada"),
                         {"slug": "nada", "state": "unknown", "permissions": frozenset()})

    def test_no_operator_row_with_a_disabled_signed_role_is_disabled(self):
        _sql("UPDATE roles SET enabled=false WHERE slug='medico'")
        self.assertEqual(perm.access_for("fantasma", "medico"),
                         {"slug": "medico", "state": "disabled", "permissions": frozenset()})

    def test_the_username_is_matched_exactly(self):
        _op("Ana", "admin")
        self.assertEqual(perm.access_for("ana", "reportes")["permissions"], frozenset({"reportes"}))
        self.assertEqual(perm.access_for("Ana", "reportes")["permissions"], perm.ALL)

    def test_a_role_change_applies_on_the_next_call(self):
        _op("mesa", "admisionista")
        self.assertEqual(perm.access_for("mesa", "x")["slug"], "admisionista")
        _sql("UPDATE operators SET role='reportes' WHERE username='mesa'")
        self.assertEqual(perm.access_for("mesa", "x")["slug"], "reportes")
        _sql("UPDATE operators SET active=false WHERE username='mesa'")
        self.assertEqual(perm.access_for("mesa", "x")["state"], "inactive")


# ---- get_role / list_roles: the public shape -------------------------------------

class TestRolePublicShape(StoreCase):
    def test_get_role_returns_exactly_these_keys(self):
        _role("jefatura", "Jefatura", ["reportes", "usuarios"], description="Manda")
        got = perm.get_role("jefatura", lang="es")
        self.assertEqual(got, {
            "slug": "jefatura", "name": "Jefatura", "description": "Manda",
            "builtin": False, "enabled": True, "locked": False,
            "permissions": ["reportes", "usuarios"], "users": 0})
        self.assertEqual(list(got), ["slug", "name", "description", "builtin", "enabled",
                                     "locked", "permissions", "users"])

    def test_missing_role_is_none(self):
        self.assertIsNone(perm.get_role("nada"))

    def test_permissions_come_out_in_catalog_order_whatever_is_stored(self):
        _role("desorden", permissions=["usuarios", "cola", "reportes", "solicitudes"])
        self.assertEqual(perm.get_role("desorden")["permissions"],
                         ["solicitudes", "cola", "reportes", "usuarios"])

    def test_unknown_stored_permissions_are_dropped_from_the_output(self):
        _role("raro", permissions=["reportes", "inexistente"])
        self.assertEqual(perm.get_role("raro")["permissions"], ["reportes"])

    def test_admin_is_locked_and_lists_every_permission_whatever_is_stored(self):
        _sql("UPDATE roles SET permissions=%s WHERE slug='admin'", ([],))
        got = perm.get_role("admin", lang="es")
        self.assertTrue(got["locked"])
        self.assertTrue(got["builtin"])
        self.assertEqual(got["permissions"], list(perm.PERMISSIONS))
        self.assertFalse(perm.get_role("medico")["locked"])

    def test_builtin_text_still_at_the_preset_is_translated_per_language(self):
        self.assertEqual(perm.get_role("admin", lang="es")["name"], "Administrador")
        en = perm.get_role("admin", lang="en")
        self.assertEqual(en["name"], "Administrator")
        self.assertEqual(en["description"], "Can do everything: care, catalog, users, settings and reports.")

    def test_builtin_text_edited_by_staff_is_shown_verbatim_in_every_language(self):
        _sql("UPDATE roles SET name='Jefes', description='Los jefes' WHERE slug='medico'")
        got = perm.get_role("medico", lang="en")
        self.assertEqual((got["name"], got["description"]), ("Jefes", "Los jefes"))

    def test_only_the_name_edited_translates_the_description_alone(self):
        _sql("UPDATE roles SET name='Jefes' WHERE slug='admin'")
        got = perm.get_role("admin", lang="en")
        self.assertEqual(got["name"], "Jefes")
        self.assertEqual(got["description"], "Can do everything: care, catalog, users, settings and reports.")

    def test_a_custom_role_text_is_never_translated(self):
        _role("jefatura", "Administrador", description=perm.BUILTIN_ROLES["admin"]["description"])
        got = perm.get_role("jefatura", lang="en")
        self.assertEqual(got["name"], "Administrador")
        self.assertEqual(got["description"], perm.BUILTIN_ROLES["admin"]["description"])

    def test_without_lang_the_active_language_is_used(self):
        self.assertEqual(perm.get_role("admin")["name"], "Administrador")

    def test_enabled_and_builtin_are_real_booleans(self):
        _role("off", enabled=False)
        got = perm.get_role("off")
        self.assertIs(got["enabled"], False)
        self.assertIs(got["builtin"], False)
        self.assertIs(perm.get_role("medico")["builtin"], True)

    def test_users_counts_every_account_holding_the_role(self):
        _role("jefatura", permissions=["usuarios"])
        _op("a", "admisionista", slug="jefatura")
        _op("b", "reportes", slug="jefatura")
        _op("c", "reportes")
        _op("d", "reportes", active=False)
        self.assertEqual(perm.get_role("jefatura")["users"], 2)
        self.assertEqual(perm.get_role("reportes")["users"], 2)
        self.assertEqual(perm.get_role("admisionista")["users"], 0)
        self.assertEqual(perm.get_role("admin")["users"], 0)


class TestListRoles(StoreCase):
    def test_builtins_first_in_preset_order_then_custom_by_name(self):
        _role("zeta", "Zeta")
        _role("alfa", "alfa")
        _role("beta", "Beta")
        got = [r["slug"] for r in perm.list_roles()]
        self.assertEqual(got, ["admin", "admisionista", "gestor_agendas", "medico", "reportes",
                               "alfa", "beta", "zeta"])

    def test_custom_roles_sort_case_insensitively_by_name(self):
        _role("r1", "banana")
        _role("r2", "Apple")
        _role("r3", "cherry")
        self.assertEqual([r["slug"] for r in perm.list_roles()][5:], ["r2", "r1", "r3"])

    def test_the_builtin_order_does_not_depend_on_the_names(self):
        _sql("UPDATE roles SET name='Zzz' WHERE slug='admin'")
        _sql("UPDATE roles SET name='Aaa' WHERE slug='reportes'")
        self.assertEqual([r["slug"] for r in perm.list_roles()][:5],
                         ["admin", "admisionista", "gestor_agendas", "medico", "reportes"])

    def test_enabled_only_hides_disabled_roles(self):
        _role("off", "Off", enabled=False)
        _role("on", "On")
        _sql("UPDATE roles SET enabled=false WHERE slug='reportes'")
        every = [r["slug"] for r in perm.list_roles()]
        self.assertIn("off", every)
        self.assertIn("reportes", every)
        only = [r["slug"] for r in perm.list_roles(enabled_only=True)]
        self.assertEqual(only, ["admin", "admisionista", "gestor_agendas", "medico", "on"])

    def test_every_entry_has_the_public_shape_with_its_user_count(self):
        _role("jefatura", "Jefatura", ["usuarios"])
        _op("a", "admin")
        _op("b", "admisionista", slug="jefatura")
        _op("c", "admisionista", active=False)
        by = {r["slug"]: r for r in perm.list_roles(lang="es")}
        self.assertEqual(by["jefatura"], perm.get_role("jefatura", lang="es"))
        self.assertEqual({k: r["users"] for k, r in by.items()},
                         {"admin": 1, "admisionista": 1, "gestor_agendas": 0, "medico": 0,
                          "reportes": 0, "jefatura": 1})
        self.assertEqual(set(by["admin"]), {"slug", "name", "description", "builtin", "enabled",
                                            "locked", "permissions", "users"})

    def test_lang_is_applied_to_builtin_names(self):
        names = {r["slug"]: r["name"] for r in perm.list_roles(lang="en")}
        self.assertEqual(names["admin"], "Administrator")

    def test_counts_for_slugs_without_a_role_row_are_ignored(self):
        _op("a", "reportes")
        _sql("DELETE FROM roles WHERE slug='reportes'")
        self.assertNotIn("reportes", [r["slug"] for r in perm.list_roles()])

    def test_no_accounts_means_zero_everywhere(self):
        self.assertEqual({r["users"] for r in perm.list_roles()}, {0})


# ---- role_is_assignable -------------------------------------------------------

class TestRoleIsAssignable(StoreCase):
    def test_an_enabled_role_is_assignable(self):
        _role("jefatura")
        self.assertIs(perm.role_is_assignable("jefatura"), True)
        self.assertIs(perm.role_is_assignable("medico"), True)

    def test_a_disabled_role_is_not(self):
        _role("off", enabled=False)
        self.assertIs(perm.role_is_assignable("off"), False)

    def test_an_unknown_role_is_not(self):
        self.assertIs(perm.role_is_assignable("nada"), False)

    def test_what_is_not_a_slug_is_refused_without_looking_it_up(self):
        for bad in (None, 5, b"medico", ["medico"], "", "Medico", "1medico", "med ico", "med-ico",
                    "m" * 41):
            with self.subTest(bad=bad):
                self.assertIs(perm.role_is_assignable(bad), False)

    def test_a_row_stored_with_an_invalid_slug_is_still_refused(self):
        _role("Mayus")
        self.assertIs(perm.role_is_assignable("Mayus"), False)

    def test_forty_chars_is_the_longest_slug(self):
        slug = "a" * 40
        _role(slug)
        self.assertIs(perm.role_is_assignable(slug), True)

    def test_disabling_takes_effect_immediately(self):
        self.assertIs(perm.role_is_assignable("reportes"), True)
        _sql("UPDATE roles SET enabled=false WHERE slug='reportes'")
        self.assertIs(perm.role_is_assignable("reportes"), False)


# ---- create_role -----------------------------------------------------------------

class TestCreateRole(StoreCase):
    def test_creates_an_enabled_custom_role_and_returns_the_public_shape(self):
        got = perm.create_role("Jefatura", "Manda", ["usuarios", "reportes"])
        self.assertEqual(got, {
            "slug": "jefatura", "name": "Jefatura", "description": "Manda",
            "builtin": False, "enabled": True, "locked": False,
            "permissions": ["reportes", "usuarios"], "users": 0})
        row = _stored("jefatura")
        self.assertEqual((row["name"], row["description"], row["builtin"], row["enabled"],
                          row["permissions"]), ("Jefatura", "Manda", False, True, ["reportes", "usuarios"]))

    def test_whitespace_is_collapsed_and_trimmed(self):
        got = perm.create_role("  Mesa \t  de   noche ", " Hace\n  cosas ", [])
        self.assertEqual((got["name"], got["description"], got["slug"]),
                         ("Mesa de noche", "Hace cosas", "mesa_de_noche"))

    def test_permissions_are_stored_deduplicated_in_catalog_order(self):
        perm.create_role("Uno", "desc", ("usuarios", "cola", "usuarios", "solicitudes"))
        self.assertEqual(_stored("uno")["permissions"], ["solicitudes", "cola", "usuarios"])

    def test_an_empty_permission_list_is_allowed(self):
        self.assertEqual(perm.create_role("Vacio", "desc", [])["permissions"], [])
        self.assertEqual(_stored("vacio")["permissions"], [])

    def test_slug_is_the_ascii_name(self):
        self.assertEqual(perm.create_role("Cirugía Mayor", "dd", [])["slug"], "cirugia_mayor")
        self.assertEqual(perm.create_role("Ñandú & Co.", "dd", [])["slug"], "nandu_co")

    def test_slug_is_cut_at_thirty_chars(self):
        got = perm.create_role("a" * 50, "dd", [])
        self.assertEqual(got["slug"], "a" * 30)

    def test_a_name_without_letters_in_ascii_gets_the_generic_slug(self):
        self.assertEqual(perm.create_role("医者", "dd", [])["slug"], "rol")
        self.assertEqual(perm.create_role("!!", "dd", [])["slug"], "rol_2")

    def test_a_name_starting_with_a_digit_is_prefixed(self):
        self.assertEqual(perm.create_role("24 horas", "dd", [])["slug"], "rol_24_horas")

    def test_a_name_that_would_collide_with_a_builtin_or_retired_slug_is_prefixed(self):
        self.assertEqual(perm.create_role("Medico", "dd", [])["slug"], "rol_medico")
        self.assertEqual(perm.create_role("Reportes", "dd", [])["slug"], "rol_reportes")
        self.assertEqual(perm.create_role("Operador", "dd", [])["slug"], "rol_operador")
        self.assertEqual(perm.create_role("marca", "dd", [])["slug"], "rol_marca")

    def test_a_taken_slug_gets_a_counter(self):
        self.assertEqual(perm.create_role("Rol A", "dd", [])["slug"], "rol_a")
        self.assertEqual(perm.create_role("Rol-A", "dd", [])["slug"], "rol_a_2")
        self.assertEqual(perm.create_role("Rol_A", "dd", [])["slug"], "rol_a_3")
        self.assertEqual(perm.create_role("rol.a", "dd", [])["slug"], "rol_a_4")

    def test_a_counter_on_a_long_slug_cuts_the_base_at_thirty_six(self):
        base = "b" * 30
        _role(base, "Otro nombre")
        self.assertEqual(perm.create_role("b" * 40, "dd", [])["slug"], base + "_2")

    def test_the_slug_taken_by_a_disabled_role_is_not_reused(self):
        _role("jefatura", "Otro", enabled=False)
        self.assertEqual(perm.create_role("Jefatura", "dd", [])["slug"], "jefatura_2")

    def test_the_same_name_in_any_case_is_refused(self):
        perm.create_role("Jefatura", "dd", [])
        for name in ("Jefatura", "jefatura", "JEFATURA"):
            with self.subTest(name=name):
                self.assertRoleError("rol_existe", "name", 409, perm.create_role, name, "dd", [])
        self.assertEqual(len(_sql("SELECT 1 FROM roles WHERE lower(name)='jefatura'")), 1)

    def test_a_builtin_name_is_refused_but_only_in_the_stored_spelling(self):
        self.assertRoleError("rol_existe", "name", 409, perm.create_role, "administrador", "dd", [])
        # "Medico" (no accent) is not the stored name "Médico": allowed, slug prefixed.
        self.assertEqual(perm.create_role("Medico", "dd", [])["slug"], "rol_medico")

    def test_a_name_collision_with_a_disabled_role_is_refused(self):
        _role("off", "Apagado", enabled=False)
        self.assertRoleError("rol_existe", "name", 409, perm.create_role, "apagado", "dd", [])

    def test_validation_errors_write_nothing(self):
        cases = [
            (("", "dd", []), "texto_requerido", "name", 400),
            (("x", "dd", []), "texto_requerido", "name", 400),
            ((None, "dd", []), "texto_requerido", "name", 400),
            (("n" * 61, "dd", []), "texto_largo", "name", 400),
            (("Nombre", "", []), "texto_requerido", "description", 400),
            (("Nombre", " x ", []), "texto_requerido", "description", 400),
            (("Nombre", "d" * 301, []), "texto_largo", "description", 400),
            (("Nombre", "dd", "reportes"), "permisos_invalidos", "permissions", 400),
            (("Nombre", "dd", None), "permisos_invalidos", "permissions", 400),
            (("Nombre", "dd", {"reportes"}), "permisos_invalidos", "permissions", 400),
            (("Nombre", "dd", ["fantasma"]), "permiso_desconocido", "permissions", 400),
            (("Nombre", "dd", ["reportes", 5]), "permiso_desconocido", "permissions", 400),
        ]
        for args, code, field, status in cases:
            with self.subTest(args=args):
                self.assertRoleError(code, field, status, perm.create_role, *args)
        self.assertEqual(len(_sql("SELECT 1 FROM roles")), 5)

    def test_messages_are_the_spanish_texts(self):
        exc = self.assertRoleError("texto_requerido", "name", 400, perm.create_role, "", "dd", [])
        self.assertEqual(str(exc), "Escribí un nombre para el rol.")
        exc = self.assertRoleError("texto_requerido", "description", 400, perm.create_role, "Nom", "", [])
        self.assertEqual(str(exc), "Escribí qué hace una persona con este rol.")
        exc = self.assertRoleError("rol_existe", "name", 409, perm.create_role, "Administrador", "dd", [])
        self.assertEqual(str(exc), "Ya hay un rol con ese nombre.")

    def test_the_limits_are_inclusive(self):
        got = perm.create_role("n" * 60, "d" * 300, [])
        self.assertEqual((len(got["name"]), len(got["description"])), (60, 300))
        self.assertEqual(perm.create_role("ab", "cd", [])["name"], "ab")

    def test_errors_come_in_order_name_then_description_then_permissions(self):
        self.assertRoleError("texto_requerido", "name", 400, perm.create_role, "", "", "mal")
        self.assertRoleError("texto_requerido", "description", 400, perm.create_role, "Nom", "", "mal")

    def test_a_new_role_has_nobody_and_does_not_touch_accounts(self):
        _op("a", "admisionista")
        got = perm.create_role("Jefatura", "dd", ["usuarios"])
        self.assertEqual(got["users"], 0)
        self.assertEqual(_sql("SELECT count(*) AS n FROM operator_access")[0]["n"], 0)


# ---- update_role ----------------------------------------------------------------------

class TestUpdateRole(StoreCase):
    def setUp(self):
        super().setUp()
        _role("jefatura", "Jefatura", ["reportes"], description="Manda")

    def _age(self, slug):
        _sql("UPDATE roles SET updated=%s WHERE slug=%s", (LONG_AGO, slug))

    def test_missing_role_is_404(self):
        self.assertRoleError("no_encontrado", None, 404, perm.update_role, "nada", {"name": "x"})

    def test_missing_wins_over_any_validation(self):
        self.assertRoleError("no_encontrado", None, 404, perm.update_role, "nada", {"name": ""})

    def test_the_admin_role_is_locked_for_every_change(self):
        for body in ({"name": "Otro"}, {"description": "otra"}, {"permissions": []},
                     {"enabled": False}, {}):
            with self.subTest(body=body):
                exc = self.assertRoleError("rol_bloqueado", None, 400, perm.update_role, "admin", body)
                self.assertEqual(str(exc), "El rol Administrador no se edita: siempre puede hacer todo.")
        row = _stored("admin")
        self.assertEqual((row["name"], row["enabled"]), ("Administrador", True))

    def test_an_empty_body_returns_the_current_role_and_writes_nothing(self):
        self._age("jefatura")
        got = perm.update_role("jefatura", {})
        self.assertEqual(got, perm.get_role("jefatura"))
        self.assertEqual(_stored("jefatura")["updated"], LONG_AGO)

    def test_unknown_keys_are_ignored(self):
        self._age("jefatura")
        got = perm.update_role("jefatura", {"slug": "otro", "builtin": True, "users": 9})
        self.assertEqual(got["slug"], "jefatura")
        self.assertFalse(got["builtin"])
        self.assertEqual(_stored("jefatura")["updated"], LONG_AGO)
        self.assertIsNone(_stored("otro"))

    def test_updates_the_name_and_returns_the_new_public_shape(self):
        self._age("jefatura")
        got = perm.update_role("jefatura", {"name": "  Jefes   de  sala "})
        self.assertEqual(got["name"], "Jefes de sala")
        self.assertEqual(got["slug"], "jefatura")
        row = _stored("jefatura")
        self.assertEqual((row["name"], row["description"], row["permissions"], row["enabled"]),
                         ("Jefes de sala", "Manda", ["reportes"], True))
        self.assertGreater(row["updated"], LONG_AGO)

    def test_a_role_can_change_only_the_case_of_its_own_name(self):
        self.assertEqual(perm.update_role("jefatura", {"name": "JEFATURA"})["name"], "JEFATURA")
        self.assertEqual(perm.update_role("jefatura", {"name": "JEFATURA"})["name"], "JEFATURA")

    def test_a_name_taken_by_another_role_is_refused_in_any_case(self):
        _role("otro", "Otro Rol")
        self.assertRoleError("rol_existe", "name", 409, perm.update_role, "jefatura", {"name": "otro rol"})
        self.assertRoleError("rol_existe", "name", 409, perm.update_role, "jefatura",
                             {"name": "administrador"})
        self.assertEqual(_stored("jefatura")["name"], "Jefatura")

    def test_updates_the_description(self):
        got = perm.update_role("jefatura", {"description": " Nueva\n descripción "})
        self.assertEqual(got["description"], "Nueva descripción")
        self.assertEqual(_stored("jefatura")["name"], "Jefatura")

    def test_updates_the_permissions_in_catalog_order(self):
        _op("otro_gestor", "admin")
        got = perm.update_role("jefatura", {"permissions": ["usuarios", "cola", "cola"]})
        self.assertEqual(got["permissions"], ["cola", "usuarios"])
        self.assertEqual(_stored("jefatura")["permissions"], ["cola", "usuarios"])

    def test_updates_enabled_with_a_boolean_coercion(self):
        _op("otro_gestor", "admin")
        self.assertIs(perm.update_role("jefatura", {"enabled": False})["enabled"], False)
        self.assertIs(_stored("jefatura")["enabled"], False)
        self.assertIs(perm.update_role("jefatura", {"enabled": 1})["enabled"], True)
        self.assertIs(perm.update_role("jefatura", {"enabled": 0})["enabled"], False)
        self.assertIs(perm.update_role("jefatura", {"enabled": None})["enabled"], False)

    def test_all_fields_at_once(self):
        _op("otro_gestor", "admin")
        got = perm.update_role("jefatura", {"name": "Nuevo", "description": "Otra", "permissions": ["hsi"],
                                            "enabled": False})
        self.assertEqual(got, {"slug": "jefatura", "name": "Nuevo", "description": "Otra",
                               "builtin": False, "enabled": False, "locked": False,
                               "permissions": ["hsi"], "users": 0})

    def test_a_builtin_role_can_be_edited_and_disabled_but_keeps_its_slug(self):
        got = perm.update_role("reportes", {"name": "Ver informes", "enabled": False})
        self.assertEqual((got["slug"], got["name"], got["enabled"], got["builtin"]),
                         ("reportes", "Ver informes", False, True))
        self.assertIsNotNone(_stored("reportes"))

    def test_validation_errors_leave_the_row_untouched(self):
        before = _stored("jefatura")
        cases = [
            ({"name": ""}, "texto_requerido", "name"),
            ({"name": "n" * 61}, "texto_largo", "name"),
            ({"description": ""}, "texto_requerido", "description"),
            ({"description": "d" * 301}, "texto_largo", "description"),
            ({"permissions": "reportes"}, "permisos_invalidos", "permissions"),
            ({"permissions": ["fantasma"]}, "permiso_desconocido", "permissions"),
            ({"name": "Valido", "description": "x"}, "texto_requerido", "description"),
            ({"name": "Valido", "permissions": None}, "permisos_invalidos", "permissions"),
        ]
        for body, code, field in cases:
            with self.subTest(body=body):
                self.assertRoleError(code, field, 400, perm.update_role, "jefatura", body)
        self.assertEqual(_stored("jefatura"), before)

    def test_the_name_conflict_is_checked_before_the_other_fields(self):
        _role("otro", "Otro")
        self.assertRoleError("rol_existe", "name", 409, perm.update_role, "jefatura",
                             {"name": "Otro", "description": ""})

    # -- the last-manager guard ----------------------------------------------------

    def _only_manager(self):
        _sql("UPDATE roles SET permissions=%s WHERE slug='jefatura'", (["usuarios", "reportes"],))
        _op("jefe", "admisionista", slug="jefatura")

    def test_removing_usuarios_from_the_role_of_the_last_manager_is_refused(self):
        self._only_manager()
        exc = self.assertRoleError("ultimo_gestor_usuarios", "permissions", 409,
                                   perm.update_role, "jefatura", {"permissions": ["reportes"]})
        self.assertEqual(str(exc), "Tiene que quedar al menos un usuario activo que pueda crear usuarios "
                                   "y editar roles.")
        self.assertEqual(_stored("jefatura")["permissions"], ["usuarios", "reportes"])

    def test_disabling_the_role_of_the_last_manager_is_refused(self):
        self._only_manager()
        self.assertRoleError("ultimo_gestor_usuarios", "permissions", 409,
                             perm.update_role, "jefatura", {"enabled": False})
        self.assertIs(_stored("jefatura")["enabled"], True)

    def test_a_refused_change_writes_none_of_its_fields(self):
        self._only_manager()
        self.assertRoleError("ultimo_gestor_usuarios", "permissions", 409, perm.update_role, "jefatura",
                             {"name": "Otro nombre", "description": "otra", "permissions": []})
        row = _stored("jefatura")
        self.assertEqual((row["name"], row["description"]), ("Jefatura", "Manda"))

    def test_the_change_is_allowed_when_another_active_manager_remains(self):
        self._only_manager()
        _op("otro", "admin")
        perm.update_role("jefatura", {"permissions": ["reportes"]})
        self.assertEqual(_stored("jefatura")["permissions"], ["reportes"])
        perm.update_role("jefatura", {"enabled": False})
        self.assertIs(_stored("jefatura")["enabled"], False)

    def test_an_inactive_manager_does_not_count_as_the_one_that_remains(self):
        self._only_manager()
        _op("viejo", "admin", active=False)
        self.assertRoleError("ultimo_gestor_usuarios", "permissions", 409,
                             perm.update_role, "jefatura", {"enabled": False})

    def test_a_manager_by_override_counts_as_the_one_that_remains(self):
        self._only_manager()
        _op("mesa", "admisionista", granted=["usuarios"])
        perm.update_role("jefatura", {"permissions": []})
        self.assertEqual(_stored("jefatura")["permissions"], [])

    def test_with_no_manager_at_all_the_change_is_allowed(self):
        perm.update_role("jefatura", {"permissions": ["cola"], "enabled": False})
        self.assertEqual(_stored("jefatura")["permissions"], ["cola"])

    def test_name_and_description_are_never_checked_against_the_guard(self):
        self._only_manager()
        got = perm.update_role("jefatura", {"name": "Otro", "description": "Otra"})
        self.assertEqual((got["name"], got["description"]), ("Otro", "Otra"))

    def test_enabling_or_widening_never_trips_the_guard(self):
        self._only_manager()
        perm.update_role("jefatura", {"enabled": True})
        perm.update_role("jefatura", {"permissions": ["usuarios", "reportes", "hsi"]})
        self.assertEqual(_stored("jefatura")["permissions"], ["reportes", "hsi", "usuarios"])

    def test_the_guard_reads_the_roles_it_is_about_not_other_roles(self):
        self._only_manager()
        _role("otro", "Otro", ["usuarios"])
        perm.update_role("otro", {"permissions": []})
        self.assertEqual(_stored("otro")["permissions"], [])
        self.assertEqual(_stored("jefatura")["permissions"], ["usuarios", "reportes"])


# ---- delete_role ---------------------------------------------------------------------------

class TestDeleteRole(StoreCase):
    def test_missing_role_is_404(self):
        self.assertRoleError("no_encontrado", None, 404, perm.delete_role, "nada")

    def test_a_builtin_role_is_never_deleted_not_even_when_nobody_holds_it(self):
        for slug in perm.BUILTIN_ROLES:
            with self.subTest(slug=slug):
                exc = self.assertRoleError("rol_de_fabrica", None, 400, perm.delete_role, slug)
                self.assertEqual(str(exc), "Los roles de fábrica no se borran: se pueden desactivar.")
                self.assertIsNotNone(_stored(slug))

    def test_a_role_with_people_is_refused(self):
        _role("jefatura")
        _op("a", "admisionista", slug="jefatura")
        exc = self.assertRoleError("rol_en_uso", None, 400, perm.delete_role, "jefatura")
        self.assertEqual(str(exc), "Hay usuarios con este rol. Cambiales el rol o desactivalo.")
        self.assertIsNotNone(_stored("jefatura"))

    def test_a_deactivated_account_still_holds_the_role(self):
        _role("jefatura")
        _op("a", "admisionista", active=False, slug="jefatura")
        self.assertRoleError("rol_en_uso", None, 400, perm.delete_role, "jefatura")

    def test_a_role_nobody_holds_is_deleted_and_returns_none(self):
        _role("jefatura")
        _role("otro")
        _op("a", "admisionista", slug="otro")
        self.assertIsNone(perm.delete_role("jefatura"))
        self.assertIsNone(_stored("jefatura"))
        self.assertIsNone(perm.get_role("jefatura"))
        self.assertIsNotNone(_stored("otro"))
        self.assertEqual(len(_sql("SELECT 1 FROM roles")), 6)

    def test_a_disabled_custom_role_without_people_can_be_deleted(self):
        _role("jefatura", enabled=False)
        perm.delete_role("jefatura")
        self.assertIsNone(_stored("jefatura"))

    def test_overrides_alone_do_not_hold_a_role(self):
        _role("jefatura")
        _op("a", "admisionista", granted=["reportes"])
        perm.delete_role("jefatura")
        self.assertIsNone(_stored("jefatura"))

    def test_the_second_delete_is_404(self):
        _role("jefatura")
        perm.delete_role("jefatura")
        self.assertRoleError("no_encontrado", None, 404, perm.delete_role, "jefatura")


# ---- overrides ------------------------------------------------------------------------------

class TestOverrides(StoreCase):
    def test_get_overrides_of_an_account_without_a_row_is_two_empty_lists(self):
        _op("a", "admisionista")
        self.assertEqual(perm.get_overrides("a"), ([], []))
        self.assertEqual(perm.get_overrides("fantasma"), ([], []))

    def test_get_overrides_returns_lists_as_stored(self):
        _op("a", "admisionista", granted=["reportes", "hsi"], denied=["solicitudes"])
        got = perm.get_overrides("a")
        self.assertEqual(got, (["reportes", "hsi"], ["solicitudes"]))
        self.assertIsInstance(got, tuple)
        self.assertIsInstance(got[0], list)
        self.assertIsInstance(got[1], list)

    def test_get_overrides_of_a_row_that_only_carries_a_role_slug_is_empty(self):
        _role("jefatura")
        _op("a", "admisionista", slug="jefatura")
        self.assertEqual(perm.get_overrides("a"), ([], []))

    def test_get_overrides_matches_the_username_exactly(self):
        _op("Ana", "admisionista", granted=["hsi"])
        self.assertEqual(perm.get_overrides("ana"), ([], []))

    def test_set_overrides_inserts_the_row_without_a_role_slug(self):
        _op("a", "admisionista")
        self.assertIsNone(perm.set_overrides("a", ["hsi"], ["cola"]))
        row = _sql("SELECT * FROM operator_access WHERE username='a'")[0]
        self.assertEqual((row["role_slug"], row["granted"], row["denied"]), (None, ["hsi"], ["cola"]))
        self.assertEqual(perm.get_overrides("a"), (["hsi"], ["cola"]))

    def test_set_overrides_for_an_account_that_does_not_exist_fails_and_writes_nothing(self):
        import psycopg
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            perm.set_overrides("fantasma", ["hsi"], [])
        self.assertEqual(_sql("SELECT 1 FROM operator_access"), [])

    def test_set_overrides_stores_catalog_order_without_duplicates(self):
        _op("a")
        perm.set_overrides("a", ["usuarios", "cola", "usuarios"], ["reportes", "solicitudes"])
        self.assertEqual(perm.get_overrides("a"), (["cola", "usuarios"], ["solicitudes", "reportes"]))

    def test_set_overrides_replaces_the_previous_lists_and_keeps_the_role_slug(self):
        _role("jefatura")
        _op("a", "admisionista", slug="jefatura", granted=["hsi"], denied=["cola"])
        _sql("UPDATE operator_access SET updated=%s WHERE username='a'", (LONG_AGO,))
        perm.set_overrides("a", ["reportes"], [])
        row = _sql("SELECT * FROM operator_access WHERE username='a'")[0]
        self.assertEqual((row["role_slug"], row["granted"], row["denied"]), ("jefatura", ["reportes"], []))
        self.assertGreater(row["updated"], LONG_AGO)
        self.assertEqual(len(_sql("SELECT 1 FROM operator_access")), 1)

    def test_set_overrides_none_means_empty(self):
        _op("a")
        perm.set_overrides("a", ["hsi"], ["cola"])
        perm.set_overrides("a", None, None)
        self.assertEqual(perm.get_overrides("a"), ([], []))

    def test_set_overrides_accepts_tuples(self):
        _op("a")
        perm.set_overrides("a", ("hsi",), ("cola",))
        self.assertEqual(perm.get_overrides("a"), (["hsi"], ["cola"]))

    def test_set_overrides_refuses_a_permission_both_granted_and_denied(self):
        _op("a")
        perm.set_overrides("a", ["hsi"], [])
        exc = self.assertRoleError("override_conflicto", "overrides", 400,
                                   perm.set_overrides, "a", ["reportes", "cola"], ["cola"])
        self.assertEqual(str(exc), "Un permiso no puede estar agregado y quitado a la vez.")
        self.assertEqual(perm.get_overrides("a"), (["hsi"], []))

    def test_set_overrides_refuses_bad_input_and_writes_nothing(self):
        _op("a")
        for granted, denied, code in (("hsi", [], "permisos_invalidos"),
                                      ([], "hsi", "permisos_invalidos"),
                                      (["fantasma"], [], "permiso_desconocido"),
                                      ([], ["fantasma"], "permiso_desconocido"),
                                      ({"hsi"}, [], "permisos_invalidos")):
            with self.subTest(granted=granted, denied=denied):
                self.assertRoleError(code, "permissions", 400, perm.set_overrides, "a", granted, denied)
        self.assertEqual(_sql("SELECT 1 FROM operator_access"), [])

    def test_set_overrides_takes_effect_in_access_for(self):
        _op("a", "admisionista")
        perm.set_overrides("a", ["hsi"], ["solicitudes"])
        self.assertEqual(perm.access_for("a", "x")["permissions"],
                         frozenset({"cola", "crear_turnos", "pacientes", "hsi"}))
        perm.set_overrides("a", [], [])
        self.assertEqual(perm.access_for("a", "x")["permissions"],
                         frozenset({"solicitudes", "cola", "crear_turnos", "pacientes"}))

    def test_clean_overrides_is_validated_canonical_lists(self):
        self.assertEqual(perm.clean_overrides(None, None), ([], []))
        self.assertEqual(perm.clean_overrides(["usuarios", "cola"], ("hsi",)), (["cola", "usuarios"], ["hsi"]))
        self.assertRoleError("override_conflicto", "overrides", 400,
                             perm.clean_overrides, ["cola"], ["cola"])
        self.assertRoleError("permisos_invalidos", "permissions", 400, perm.clean_overrides, "x", [])


# ---- the last-manager guard: snapshot, count, assert ----------------------------------------

class TestSnapshot(StoreCase):
    def test_snapshot_returns_every_operator_row_and_every_role(self):
        _role("jefatura", permissions=["usuarios"], enabled=False)
        _op("a", "admin")
        _op("b", "reportes", active=False, slug="jefatura", granted=["hsi"], denied=["cola"])
        with db._connect() as conn, conn.cursor() as cur:
            ops, roles = perm._snapshot(cur)
        by = {o["username"]: o for o in ops}
        self.assertEqual(by["a"], {"username": "a", "active": True, "role": "admin",
                                   "role_slug": None, "granted": None, "denied": None})
        self.assertEqual(by["b"], {"username": "b", "active": False, "role": "reportes",
                                   "role_slug": "jefatura", "granted": ["hsi"], "denied": ["cola"]})
        self.assertEqual(len(ops), 2)
        self.assertEqual(set(roles), {"admin", "admisionista", "gestor_agendas", "medico", "reportes",
                                      "jefatura"})
        self.assertEqual(roles["jefatura"], {"slug": "jefatura", "enabled": False,
                                             "permissions": ["usuarios"]})
        self.assertEqual(roles["admin"]["enabled"], True)
        self.assertIsInstance(ops[0], dict)
        self.assertIsInstance(roles["admin"], dict)

    def test_snapshot_of_an_empty_installation(self):
        with db._connect() as conn, conn.cursor() as cur:
            ops, roles = perm._snapshot(cur)
        self.assertEqual(ops, [])
        self.assertEqual(len(roles), 5)


class TestManagerCount(unittest.TestCase):
    """Pure counting over a snapshot; no database."""

    ROLES = {
        "admin": {"slug": "admin", "enabled": True, "permissions": list(perm.PERMISSIONS)},
        "jefatura": {"slug": "jefatura", "enabled": True, "permissions": ["usuarios"]},
        "reportes": {"slug": "reportes", "enabled": True, "permissions": ["reportes"]},
        "off": {"slug": "off", "enabled": False, "permissions": ["usuarios"]},
    }

    @staticmethod
    def _o(username, role="reportes", active=True, slug=None, granted=None, denied=None):
        return {"username": username, "active": active, "role": role, "role_slug": slug,
                "granted": granted, "denied": denied}

    def test_counts_active_accounts_whose_effective_set_has_usuarios(self):
        ops = [self._o("a", "admin"), self._o("b", slug="jefatura"), self._o("c"),
               self._o("d", "admin", active=False), self._o("e", slug="off"),
               self._o("f", slug="inexistente"), self._o("g", granted=["usuarios"]),
               self._o("h", "admin", denied=["usuarios"])]
        self.assertEqual(perm._manager_count(ops, self.ROLES), 3)

    def test_operator_change_overrides_the_state_of_that_account_only(self):
        ops = [self._o("a", "admin"), self._o("b", slug="jefatura")]
        count = lambda change: perm._manager_count(ops, self.ROLES, operator_change=change)  # noqa: E731
        self.assertEqual(count({"usuario": "a", "active": False}), 1)
        self.assertEqual(count({"usuario": "a", "slug": "reportes"}), 1)
        self.assertEqual(count({"usuario": "a", "denied": ["usuarios"]}), 1)
        self.assertEqual(count({"usuario": "a", "slug": "off"}), 1)
        self.assertEqual(count({"usuario": "a", "slug": "inexistente"}), 1)
        self.assertEqual(count({"usuario": "c", "active": False}), 2)
        self.assertEqual(count({"usuario": "a"}), 2)
        self.assertEqual(count({"usuario": "a", "active": None, "slug": None, "granted": None}), 2)
        self.assertEqual(perm._manager_count([self._o("z")], self.ROLES,
                                             operator_change={"usuario": "z", "granted": ["usuarios"]}), 1)
        self.assertEqual(perm._manager_count([self._o("z")], self.ROLES,
                                             operator_change={"usuario": "z", "slug": "jefatura"}), 1)
        self.assertEqual(perm._manager_count([self._o("z", active=False)], self.ROLES,
                                             operator_change={"usuario": "z", "active": True,
                                                              "slug": "jefatura"}), 1)

    def test_role_change_overrides_the_role_only_in_the_hypothesis(self):
        ops = [self._o("a", slug="jefatura"), self._o("b", slug="jefatura"), self._o("c", "admin")]
        count = lambda change: perm._manager_count(ops, self.ROLES, role_change=change)  # noqa: E731
        self.assertEqual(count({"slug": "jefatura", "permissions": ["reportes"]}), 1)
        self.assertEqual(count({"slug": "jefatura", "enabled": False}), 1)
        self.assertEqual(count({"slug": "jefatura", "permissions": None, "enabled": None}), 3)
        self.assertEqual(count({"slug": "reportes", "permissions": ["usuarios"]}), 3)
        self.assertEqual(count({"slug": "fantasma", "permissions": []}), 3)
        self.assertEqual(count({"slug": "admin", "permissions": [], "enabled": True}), 3)
        self.assertEqual(count({"slug": "jefatura", "enabled": True, "permissions": ["usuarios"]}), 3)
        self.assertEqual(self.ROLES["jefatura"]["permissions"], ["usuarios"])
        self.assertTrue(self.ROLES["jefatura"]["enabled"])

    def test_an_empty_installation_counts_zero(self):
        self.assertEqual(perm._manager_count([], self.ROLES), 0)
        self.assertEqual(perm._manager_count([self._o("a", "admin")], {}), 0)


class TestAssertUserManagerRemains(StoreCase):
    MSG = "Tiene que quedar al menos un usuario activo que pueda crear usuarios y editar roles."

    def test_no_change_never_raises(self):
        _op("a", "admin")
        self.assertIsNone(perm.assert_user_manager_remains())
        self.assertIsNone(perm.assert_user_manager_remains(None, None))

    def test_with_no_manager_nothing_is_refused(self):
        _op("a", "reportes")
        self.assertIsNone(perm.assert_user_manager_remains(operator_change={"usuario": "a", "active": False}))
        self.assertIsNone(perm.assert_user_manager_remains(role_change={"slug": "reportes", "enabled": False}))

    def test_with_nobody_at_all_nothing_is_refused(self):
        self.assertIsNone(perm.assert_user_manager_remains(operator_change={"usuario": "a", "active": False}))

    def test_deactivating_the_last_manager_is_refused_on_the_overrides_field(self):
        _op("a", "admin")
        _op("b", "reportes")
        exc = self.assertRoleError("ultimo_gestor_usuarios", "overrides", 409,
                                   perm.assert_user_manager_remains,
                                   operator_change={"usuario": "a", "active": False})
        self.assertEqual(str(exc), self.MSG)

    def test_denying_usuarios_to_the_last_manager_is_refused(self):
        _op("a", "admin")
        self.assertRoleError("ultimo_gestor_usuarios", "overrides", 409, perm.assert_user_manager_remains,
                             operator_change={"usuario": "a", "denied": ["usuarios"]})

    def test_moving_the_last_manager_to_another_role_is_refused(self):
        _op("a", "admin")
        for slug in ("reportes", "inexistente"):
            with self.subTest(slug=slug):
                self.assertRoleError("ultimo_gestor_usuarios", "overrides", 409,
                                     perm.assert_user_manager_remains,
                                     operator_change={"usuario": "a", "slug": slug})

    def test_moving_the_last_manager_to_a_disabled_role_is_refused(self):
        _role("off", permissions=["usuarios"], enabled=False)
        _op("a", "admin")
        self.assertRoleError("ultimo_gestor_usuarios", "overrides", 409, perm.assert_user_manager_remains,
                             operator_change={"usuario": "a", "slug": "off"})

    def test_a_change_of_another_account_is_fine(self):
        _op("a", "admin")
        _op("b", "reportes")
        self.assertIsNone(perm.assert_user_manager_remains(operator_change={"usuario": "b", "active": False}))

    def test_a_second_manager_makes_any_single_change_fine(self):
        _op("a", "admin")
        _op("b", "admin")
        self.assertIsNone(perm.assert_user_manager_remains(operator_change={"usuario": "a", "active": False}))
        self.assertIsNone(perm.assert_user_manager_remains(operator_change={"usuario": "b",
                                                                            "denied": ["usuarios"]}))

    def test_only_active_accounts_count_as_managers(self):
        _op("a", "admin")
        _op("b", "admin", active=False)
        self.assertRoleError("ultimo_gestor_usuarios", "overrides", 409, perm.assert_user_manager_remains,
                             operator_change={"usuario": "a", "active": False})

    def test_a_manager_by_grant_is_a_manager(self):
        _op("a", "admisionista", granted=["usuarios"])
        self.assertRoleError("ultimo_gestor_usuarios", "overrides", 409, perm.assert_user_manager_remains,
                             operator_change={"usuario": "a", "granted": []})

    def test_a_manager_by_a_custom_role_is_a_manager(self):
        _role("jefatura", permissions=["usuarios"])
        _op("a", "reportes", slug="jefatura")
        self.assertRoleError("ultimo_gestor_usuarios", "overrides", 409, perm.assert_user_manager_remains,
                             operator_change={"usuario": "a", "slug": "reportes"})

    def test_role_change_to_the_role_of_the_last_manager_is_refused_on_the_permissions_field(self):
        _role("jefatura", permissions=["usuarios"])
        _op("a", "reportes", slug="jefatura")
        for change in ({"slug": "jefatura", "permissions": ["reportes"]},
                       {"slug": "jefatura", "enabled": False},
                       {"slug": "jefatura", "permissions": [], "enabled": True}):
            with self.subTest(change=change):
                exc = self.assertRoleError("ultimo_gestor_usuarios", "permissions", 409,
                                           perm.assert_user_manager_remains, role_change=change)
                self.assertEqual(str(exc), self.MSG)

    def test_role_change_that_keeps_usuarios_or_touches_another_role_is_fine(self):
        _role("jefatura", permissions=["usuarios"])
        _op("a", "reportes", slug="jefatura")
        self.assertIsNone(perm.assert_user_manager_remains(
            role_change={"slug": "jefatura", "permissions": ["usuarios", "hsi"], "enabled": True}))
        self.assertIsNone(perm.assert_user_manager_remains(
            role_change={"slug": "reportes", "permissions": [], "enabled": False}))
        self.assertIsNone(perm.assert_user_manager_remains(role_change={"slug": "jefatura"}))

    def test_when_both_changes_are_given_the_field_is_overrides(self):
        _op("a", "admin")
        self.assertRoleError("ultimo_gestor_usuarios", "overrides", 409, perm.assert_user_manager_remains,
                             operator_change={"usuario": "a", "active": False},
                             role_change={"slug": "reportes", "permissions": []})

    def test_the_guard_writes_nothing(self):
        _op("a", "admin")
        with self.assertRaises(perm.RoleError):
            perm.assert_user_manager_remains(operator_change={"usuario": "a", "active": False})
        self.assertTrue(_sql("SELECT active FROM operators WHERE username='a'")[0]["active"])


if __name__ == "__main__":
    unittest.main()
