"""test_db_null_semantics.py — a NULL parameter never matches a row, in the accounts, operators, auth and
permissions stores.

The hand-written SQL compared with `col = %s`: a Python None became `col = NULL`, which is never true. SQLAlchemy
Core renders `col == None` as `col IS NULL`, which MATCHES every row whose column is NULL (an account without a
feed token, an operator without a doctor link, a challenge without a username). Stage 5 of the SQLAlchemy Core move
converted those lookups and bound the None as a value where the column is nullable (`sa.literal`, `_eq`); this
file pins that for every lookup and write of the three stores and `core/permissions.py`: each call is made with
every argument replaced by None, over rows whose nullable columns ARE NULL, and the outcome is exactly what the
old SQL gave (recorded from it before the conversion: a lookup finds nothing, a write touches nothing, a
mistyped argument raises). It needs a database.
"""
import datetime
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import permissions  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_auth  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_db_null_semantics")


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


_IGNORED = ("id", "created", "creado", "updated", "last_seen", "token", "creado_en", "ultimo_acceso", "expires",
            "expira")


def norm(v):
    if isinstance(v, dict):
        return "{" + ",".join("%s=%s" % (k, norm(v[k])) for k in sorted(v) if k not in (
            "id", "created", "creado", "updated", "last_seen", "token", "creado_en", "ultimo_acceso", "expires", "expira")) + "}"
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(norm(x) for x in v) + "]"
    if isinstance(v, (datetime.datetime, datetime.date)):
        return "<dt>"
    if isinstance(v, (bytes, memoryview)):
        return "<bytes>"
    if isinstance(v, str) and len(v) > 20:
        return "<str%d>" % len(v)
    if isinstance(v, (set, frozenset)):
        return "set" + str(sorted(v))
    return repr(v)


# call -> expected outcome, recorded on the hand-written SQL
EXPECTED = {
    ('get_user', 'valid'): "{activo=True,apellido='Paz',avisos=None,cobertura=None,contacto='2255000001',dni='50000001',domicilio=None,email=None,identity_kind='dni',lang=None,nacimiento=<dt>,nombre='Ana',renaper=None,sexo='F'}",
    ('get_user', 'None@0'): 'None',
    ('get_user_by_email', 'valid'): "{activo=True,apellido='Paz',avisos=None,cobertura=None,contacto='2255000002',dni='50000002',domicilio=None,email='b@x.org',identity_kind='dni',lang=None,nacimiento=<dt>,nombre='Beto',renaper=None,sexo='M'}",
    ('get_user_by_email', 'None@0'): 'None',
    ('get_session', 'valid'): 'None',
    ('get_session', 'None@0'): 'None',
    ('delete_session', 'valid'): 'None',
    ('delete_session', 'None@0'): 'None',
    ('verify_password', 'valid'): 'None',
    ('verify_password', 'None@0'): 'None',
    ('verify_password', 'None@1'): 'None',
    ('user_by_feed_token', 'valid'): 'None',
    ('user_by_feed_token', 'None@0'): 'None',
    ('user_by_mcp_token', 'valid'): 'None',
    ('user_by_mcp_token', 'None@0'): 'None',
    ('feed_token_issue', 'valid'): 'None',
    ('feed_token_issue', 'None@0'): 'None',
    ('feed_token_regenerate', 'valid'): 'None',
    ('feed_token_regenerate', 'None@0'): 'None',
    ('mcp_token_issue', 'valid'): 'None',
    ('mcp_token_issue', 'None@0'): 'None',
    ('mcp_token_regenerate', 'valid'): 'None',
    ('mcp_token_regenerate', 'None@0'): 'None',
    ('user_by_portal_id', 'valid'): 'None',
    ('user_by_portal_id', 'None@0'): 'None',
    ('user_claim_portal', 'valid'): "{activo=True,apellido=None,avisos=None,cobertura=None,contacto=None,dni='50000099',domicilio=None,email='x@x.org',identity_kind='dni',lang=None,nacimiento=None,nombre=None,renaper=None,sexo=None}",
    ('user_claim_portal', 'None@0'): 'None',
    ('user_claim_portal', 'None@1'): 'None',
    ('user_claim_portal', 'None@2'): 'None',
    ('consume_reset_token', 'valid'): 'None',
    ('consume_reset_token', 'None@0'): 'None',
    ('set_password', 'valid'): 'None',
    ('set_password', 'None@0'): 'None',
    ('set_password', 'None@1'): 'EXC TypeError',
    ('verify_otp', 'valid'): 'False',
    ('verify_otp', 'None@0'): 'False',
    ('verify_otp', 'None@1'): 'False',
    ('verify_otp', 'None@2'): 'False',
    ('consume_verified_otp', 'valid'): 'False',
    ('consume_verified_otp', 'None@0'): 'False',
    ('consume_verified_otp', 'None@1'): 'False',
    ('otp_recent', 'valid'): 'False',
    ('otp_recent', 'None@0'): 'False',
    ('otp_recent', 'None@1'): 'False',
    ('user_admin_update', 'valid'): "{activo=True,apellido=None,avisos=None,cobertura=None,contacto=None,dni='50000099',domicilio=None,email='x@x.org',identity_kind='dni',lang=None,nacimiento=None,nombre='Z',renaper=None,sexo=None}",
    ('user_admin_update', 'None@0'): 'None',
    ('user_admin_update', 'None@1'): 'EXC AttributeError',
    ('update_user', 'valid'): 'True',
    ('update_user', 'None@0'): 'False',
    ('update_user', 'None@1'): 'True',
    ('update_user', 'None@2'): 'True',
    ('update_user', 'None@3'): 'True',
    ('update_user', 'None@4'): 'True',
    ('update_user', 'None@5'): 'True',
    ('update_user', 'None@6'): 'True',
    ('update_self_booking_coverage', 'valid'): 'False',
    ('update_self_booking_coverage', 'None@0'): 'False',
    ('update_self_booking_coverage', 'None@1'): 'False',
    ('update_self_booking_coverage', 'None@2'): 'False',
    ('update_self_booking_coverage', 'None@3'): 'False',
    ('archive_user', 'valid'): "{activo=False,apellido='B',avisos=None,cobertura=None,contacto='1',dni='50000099',domicilio=None,email=None,identity_kind='dni',lang=None,nacimiento=<dt>,nombre='A',renaper=None,sexo='F'}",
    ('archive_user', 'None@0'): 'None',
    ('users_admin_list', 'valid'): "[[{activo=True,apellido='Paz',cobertura=None,contacto='2255000001',dni='50000001',domicilio=None,email=None,nacimiento=<dt>,nombre='Ana',sexo='F'}],1]",
    ('users_admin_list', 'None@0'): "[[{activo=False,apellido='B',cobertura=None,contacto='1',dni='50000099',domicilio=None,email=None,nacimiento=<dt>,nombre='A',sexo='F'},{activo=True,apellido='Paz',cobertura=None,contacto='2255000001',dni='50000001',domicilio=None,email=None,nacimiento=<dt>,nombre='Ana',sexo='F'},{activo=True,apellido='Paz',cobertura=None,contacto='2255000002',dni='50000002',domicilio=None,email='b@x.org',nacimiento=<dt>,nombre='Beto',sexo='M'}],3]",
    ('operator_get', 'valid'): 'None',
    ('operator_get', 'None@0'): 'None',
    ('operator_get_public', 'valid'): 'None',
    ('operator_get_public', 'None@0'): 'None',
    ('operator_by_email', 'valid'): 'None',
    ('operator_by_email', 'None@0'): 'None',
    ('operator_active_email_taken', 'valid'): 'False',
    ('operator_active_email_taken', 'None@0'): 'False',
    ('operator_active_email_taken', 'None@1'): 'False',
    ('operator_verify_password', 'valid'): 'None',
    ('operator_verify_password', 'None@0'): 'None',
    ('operator_verify_password', 'None@1'): 'None',
    ('operator_mark_access', 'valid'): 'None',
    ('operator_mark_access', 'None@0'): 'None',
    ('operator_by_portal_id', 'valid'): 'None',
    ('operator_by_portal_id', 'None@0'): 'None',
    ('operator_claim_portal', 'valid'): 'None',
    ('operator_claim_portal', 'None@0'): 'None',
    ('operator_claim_portal', 'None@1'): 'None',
    ('operator_claim_portal', 'None@2'): 'None',
    ('operator_unbind_portal', 'valid'): 'False',
    ('operator_unbind_portal', 'None@0'): 'False',
    ('operator_doctor_id', 'valid'): 'None',
    ('operator_doctor_id', 'None@0'): 'None',
    ('operator_holds_doctor_id', 'valid'): 'False',
    ('operator_holds_doctor_id', 'None@0'): 'False',
    ('operator_by_doctor_id', 'valid'): 'None',
    ('operator_by_doctor_id', 'None@0'): 'None',
    ('operator_feed_token_issue', 'valid'): 'None',
    ('operator_feed_token_issue', 'None@0'): 'None',
    ('operator_revoke_feed_token', 'valid'): 'False',
    ('operator_revoke_feed_token', 'None@0'): 'False',
    ('operator_rotate_feed_token', 'valid'): 'None',
    ('operator_rotate_feed_token', 'None@0'): 'None',
    ('operator_by_feed_token', 'valid'): 'None',
    ('operator_by_feed_token', 'None@0'): 'None',
    ('operator_deactivate', 'valid'): 'None',
    ('operator_deactivate', 'None@0'): 'None',
    ('operator_complete_onboarding', 'valid'): 'False',
    ('operator_complete_onboarding', 'None@0'): 'False',
    ('operator_update', 'valid'): 'None',
    ('operator_update', 'None@0'): 'None',
    ('passkey_by_credential', 'valid'): 'None',
    ('passkey_by_credential', 'None@0'): 'None',
    ('passkeys_of', 'valid'): '[]',
    ('passkeys_of', 'None@0'): '[]',
    ('passkey_mark_use', 'valid'): 'False',
    ('passkey_mark_use', 'None@0'): 'False',
    ('passkey_mark_use', 'None@1'): 'False',
    ('passkey_delete', 'valid'): 'False',
    ('passkey_delete', 'None@0'): 'False',
    ('passkey_delete', 'None@1'): 'False',
    ('passkeys_delete_of', 'valid'): '0',
    ('passkeys_delete_of', 'None@0'): '0',
    ('passkey_challenge_consume', 'valid'): 'False',
    ('passkey_challenge_consume', 'None@0'): 'EXC TypeError',
    ('passkey_challenge_consume', 'None@1'): 'False',
    ('passkey_challenge_consume', 'None@2'): 'False',
    ('admin_security_get', 'valid'): 'None',
    ('admin_security_get', 'None@0'): 'None',
    ('admin_security_owner_access', 'valid'): 'None',
    ('admin_security_owner_access', 'None@0'): 'EXC NotNullViolation',
    ('admin_security_set_deadline', 'valid'): "{owner_email=None,owner_pass_hash=None,owner_totp_secret=None,owner_totp_set=False,owner_ultimo_acceso=<dt>,totp_deadline=None,usuario='nadie'}",
    ('admin_security_set_deadline', 'None@0'): 'EXC NotNullViolation',
    ('admin_security_set_deadline', 'None@1'): "{owner_email=None,owner_pass_hash=None,owner_totp_secret=None,owner_totp_set=False,owner_ultimo_acceso=<dt>,totp_deadline=None,usuario='nadie'}",
    ('admin_security_verify_owner_password', 'valid'): 'False',
    ('admin_security_verify_owner_password', 'None@0'): 'False',
    ('admin_security_verify_owner_password', 'None@1'): 'False',
    ('admin_preferences', 'valid'): '{}',
    ('admin_preferences', 'None@0'): '{}',
    ('admin_reset_token_status', 'valid'): 'False',
    ('admin_reset_token_status', 'None@0'): 'False',
    ('admin_reset_token_username', 'valid'): 'None',
    ('admin_reset_token_username', 'None@0'): 'None',
    ('admin_reset_token_consume', 'valid'): 'None',
    ('admin_reset_token_consume', 'None@0'): 'None',
    ('admin_reset_token_consume', 'None@1'): 'None',
    ('access_for', 'valid'): "{permissions=set['cola', 'crear_turnos', 'pacientes', 'solicitudes'],slug='admisionista',state='ok'}",
    ('access_for', 'None@0'): "{permissions=set['cola', 'crear_turnos', 'pacientes', 'solicitudes'],slug='admisionista',state='ok'}",
    ('access_for', 'None@1'): "{permissions=set[],slug=None,state='unknown'}",
    ('get_overrides', 'valid'): '[[],[]]',
    ('get_overrides', 'None@0'): '[[],[]]',
    ('get_role', 'valid'): 'None',
    ('get_role', 'None@0'): 'None',
    ('role_is_assignable', 'valid'): 'False',
    ('role_is_assignable', 'None@0'): 'False',
    ('list_roles', 'valid'): "[{builtin=True,description=<str73>,enabled=True,locked=True,name='Administrador',permissions=['solicitudes','cola','crear_turnos','pacientes','historia_clinica','atencion_medica','todos_los_pacientes','medicos','servicios','reportes','asistente','apariencia','configuracion','hsi','sistema','legal','usuarios'],slug='admin',users=0},{builtin=True,description=<str66>,enabled=True,locked=False,name='Mesa de admisión',permissions=['solicitudes','cola','crear_turnos','pacientes'],slug='admisionista',users=3},{builtin=True,description=<str107>,enabled=True,locked=False,name='Gestor de agendas',permissions=['solicitudes','cola','crear_turnos','pacientes','medicos'],slug='gestor_agendas',users=0},{builtin=True,description=<str78>,enabled=True,locked=False,name='Profesional',permissions=['solicitudes','cola','crear_turnos','atencion_medica'],slug='medico',users=0},{builtin=True,description=<str76>,enabled=True,locked=False,name=<str26>,permissions=['reportes'],slug='reportes',users=0}]",
}


class TestNullNeverMatches(unittest.TestCase):
    def test_every_call_with_a_none_argument_gives_what_the_old_sql_gave(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        # rows with NULL in every nullable lookup column
        db_accounts.create_user("50000001", "Ana", "Paz", "1990-01-01", "F", "2255000001", "Clave-123456-x")
        db_accounts.create_user("50000002", "Beto", "Paz", "1990-01-01", "M", "2255000002", "Clave-123456-x", email="b@x.org")
        db_accounts.create_session("50000001")
        db_accounts.create_otp("2255000001", "registro")
        db_accounts.create_reset_token("50000001")
        db_operators.operator_create("opnull", "Clave-123456-x", rol="admisionista")
        db_operators.operator_create("opfull", "Clave-123456-x", rol="admisionista", email="o@x.org")
        db_operators.operator_create("opmed", "Clave-123456-x", rol="admisionista")
        db_auth.passkey_create("opnull", "cred-1", "pk", 0, None)
        db_auth.passkey_challenge_issue(b"dg-null", "login", None)
        db_auth.passkey_challenge_issue(b"dg-user", "login", "opnull")
        db_auth.admin_security_ensure_deadline("opnull", False)
        db_auth.admin_preference_save("opnull", "tema", "oscuro")
        db_auth.admin_reset_token_create("opnull")
        permissions.set_overrides("opnull", [], [])

        calls = [
            ("get_user", db_accounts.get_user, ["50000001"]),
            ("get_user_by_email", db_accounts.get_user_by_email, ["b@x.org"]),
            ("get_session", db_accounts.get_session, ["tok"]),
            ("delete_session", db_accounts.delete_session, ["tok"]),
            ("verify_password", db_accounts.verify_password, ["50000001", "mala"]),
            ("user_by_feed_token", db_accounts.user_by_feed_token, ["tok"]),
            ("user_by_mcp_token", db_accounts.user_by_mcp_token, ["tok"]),
            ("feed_token_issue", db_accounts.feed_token_issue, ["50000099"]),
            ("feed_token_regenerate", db_accounts.feed_token_regenerate, ["50000099"]),
            ("mcp_token_issue", db_accounts.mcp_token_issue, ["50000099"]),
            ("mcp_token_regenerate", db_accounts.mcp_token_regenerate, ["50000099"]),
            ("user_by_portal_id", db_accounts.user_by_portal_id, ["pid"]),
            ("user_claim_portal", db_accounts.user_claim_portal, ["pid", "50000099", "x@x.org"]),
            ("consume_reset_token", db_accounts.consume_reset_token, ["tok"]),
            ("set_password", db_accounts.set_password, ["50000099", "Clave-123456-y"]),
            ("verify_otp", db_accounts.verify_otp, ["2255000009", "registro", "000000"]),
            ("consume_verified_otp", db_accounts.consume_verified_otp, ["2255000009", "registro"]),
            ("otp_recent", db_accounts.otp_recent, ["2255000009", "registro"]),
            ("user_admin_update", db_accounts.user_admin_update, ["50000099", {"nombre": "Z"}]),
            ("update_user", db_accounts.update_user, ["50000099", "A", "B", "1990-01-01", "F", "1", "e@x.org"]),
            ("update_self_booking_coverage", db_accounts.update_self_booking_coverage, [1, "50000099", None, {"a": 1}]),
            ("archive_user", db_accounts.archive_user, ["50000099"]),
            ("users_admin_list", db_accounts.users_admin_list, ["Ana"]),
            ("operator_get", db_operators.operator_get, ["nadie"]),
            ("operator_get_public", db_operators.operator_get_public, ["nadie"]),
            ("operator_by_email", db_operators.operator_by_email, ["nadie@x.org"]),
            ("operator_active_email_taken", db_operators.operator_active_email_taken, ["nadie@x.org", "otro"]),
            ("operator_verify_password", db_operators.operator_verify_password, ["nadie", "x"]),
            ("operator_mark_access", db_operators.operator_mark_access, ["nadie"]),
            ("operator_by_portal_id", db_operators.operator_by_portal_id, ["pid"]),
            ("operator_claim_portal", db_operators.operator_claim_portal, ["nadie@x.org", "pid", "20123456789"]),
            ("operator_unbind_portal", db_operators.operator_unbind_portal, ["nadie"]),
            ("operator_doctor_id", db_operators.operator_doctor_id, ["nadie"]),
            ("operator_holds_doctor_id", db_operators.operator_holds_doctor_id, ["77"]),
            ("operator_by_doctor_id", db_operators.operator_by_doctor_id, ["77"]),
            ("operator_feed_token_issue", db_operators.operator_feed_token_issue, ["nadie"]),
            ("operator_revoke_feed_token", db_operators.operator_revoke_feed_token, ["nadie"]),
            ("operator_rotate_feed_token", db_operators.operator_rotate_feed_token, ["nadie"]),
            ("operator_by_feed_token", db_operators.operator_by_feed_token, ["tok"]),
            ("operator_deactivate", db_operators.operator_deactivate, ["nadie"]),
            ("operator_complete_onboarding", db_operators.operator_complete_onboarding, ["nadie"]),
            ("operator_update", db_operators.operator_update, ["nadie"]),
            ("passkey_by_credential", db_auth.passkey_by_credential, ["nada"]),
            ("passkeys_of", db_auth.passkeys_of, ["nadie"]),
            ("passkey_mark_use", db_auth.passkey_mark_use, ["nada", 3]),
            ("passkey_delete", db_auth.passkey_delete, ["nada", "nadie"]),
            ("passkeys_delete_of", db_auth.passkeys_delete_of, ["nadie"]),
            ("passkey_challenge_consume", db_auth.passkey_challenge_consume, [b"dg-x", "login", "nadie"]),
            ("admin_security_get", db_auth.admin_security_get, ["nadie"]),
            ("admin_security_owner_access", db_auth.admin_security_owner_access, ["nadie"]),
            ("admin_security_set_deadline", db_auth.admin_security_set_deadline, ["nadie", None]),
            ("admin_security_verify_owner_password", db_auth.admin_security_verify_owner_password, ["nadie", "x"]),
            ("admin_preferences", db_auth.admin_preferences, ["nadie"]),
            ("admin_reset_token_status", db_auth.admin_reset_token_status, ["raw"]),
            ("admin_reset_token_username", db_auth.admin_reset_token_username, ["raw"]),
            ("admin_reset_token_consume", db_auth.admin_reset_token_consume, ["raw", "Clave-123456-x"]),
            ("access_for", permissions.access_for, ["nadie", "admisionista"]),
            ("get_overrides", permissions.get_overrides, ["nadie"]),
            ("get_role", permissions.get_role, ["nadie"]),
            ("role_is_assignable", permissions.role_is_assignable, ["nadie"]),
            ("list_roles", permissions.list_roles, []),
        ]
        seen = set()
        for name, fn, args in calls:
            variants = [("valid", args)] + [("None@%d" % i, args[:i] + [None] + args[i + 1:]) for i in range(len(args))]
            for label, a in variants:
                try:
                    out = norm(fn(*a))
                except Exception as exc:  # noqa: BLE001
                    out = "EXC " + type(exc).__name__
                seen.add((name, label))
                with self.subTest(call=name, arg=label):
                    self.assertEqual(out, EXPECTED[(name, label)])
        self.assertEqual(seen, set(EXPECTED))


if __name__ == "__main__":
    unittest.main()
