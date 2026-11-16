"""test_admin_users_steps.py - pins what admin_users.edit_user() does, step by step.

edit_user() validates a PUT body in a fixed order and then applies it: the
first failing check raises an ApiError, the lockout guard runs before anything
is written, then the operator row, the per-user overrides and the 2FA deadline
are written in that order. These tests go through edit_user() only and pin what
reaches the collaborators (database, permissions, catalog), in which order, and
what the caller gets back. The collaborators are mocks, so no database is
needed; the end-to-end behaviour over a real database stays in
test_admin_users.py.

The 2FA deadline arithmetic runs against a frozen datetime installed in
admin_users, so no test reads the machine clock.

Run:  python3 -m unittest discover -s tests -p "test_admin_users_steps.py"
"""

import contextlib
import io
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, call, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import db  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_auth  # noqa: E402
from app.core import permissions  # noqa: E402
from app.admin_panel import admin_users  # noqa: E402
from app.admin_panel.native_json import ApiError  # noqa: E402

_NOW = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
_WEEK = _NOW + timedelta(days=7)

_ROLES = ("medico", "admisionista", "reportes", "admin")
_DOCTORS = ("med-1", "med-2")

_MSG_ROL = "rol inválido"
_MSG_EMAIL = "Ingresá un email válido."
_MSG_MEDICO = "Elegí un médico del catálogo para esta cuenta."
_MSG_PORTAL = "Ese email ya es de otra cuenta activa."
_MSG_TOO_SHORT = "La contraseña tiene que tener al menos 8 caracteres."
_MSG_TOTP = "totp inválido (usar 'generar' o 'quitar')"
_MSG_FORZAR = "No se puede generar un código y pedir otro nuevo al próximo ingreso."


class _FrozenDatetime:
    """Stands in for ``datetime`` inside admin_users: ``now`` is always _NOW."""

    @staticmethod
    def now(tz=None):
        return _NOW if tz is None else _NOW.astimezone(tz)


class _EditUserCase(unittest.TestCase):
    def setUp(self):
        m = self.m = Mock()
        self.existing = {"usuario": "op", "rol": "admisionista", "activo": True,
                         "email": "op@example.com", "medico_id": None}
        self.updated = {"usuario": "op", "rol": "admisionista", "activo": True,
                        "tiene_2fa": False}
        m.operator_get.return_value = self.existing
        m.operator_update.return_value = self.updated
        m.role_is_assignable.side_effect = lambda rol: rol in _ROLES
        m.valid_doctor.side_effect = lambda mid: mid in _DOCTORS
        m.portal_enabled.return_value = False
        m.email_taken.return_value = False
        m.gen_secret.return_value = "NEWSECRET"
        m.otpauth.side_effect = lambda usuario, secret: "otpauth://%s/%s" % (usuario, secret)

        patches = [
            patch.object(admin_users.i18n, "active", return_value="es"),
            patch.object(admin_users.admin_auth, "owner_usuario", return_value="the-owner"),
            patch.object(db_operators, "operator_get", m.operator_get),
            patch.object(db_operators, "operator_update", m.operator_update),
            patch.object(db_operators, "operator_active_email_taken", m.email_taken),
            patch.object(db_auth, "admin_security_set_deadline", m.set_deadline),
            patch.object(permissions, "role_is_assignable", m.role_is_assignable),
            patch.object(permissions, "assert_user_manager_remains", m.guard),
            patch.object(permissions, "set_overrides", m.set_overrides),
            patch.object(admin_users.staff_portal, "enabled", m.portal_enabled),
            patch.object(admin_users, "_valid_doctor", m.valid_doctor),
            patch.object(admin_users, "_gen_totp_secret", m.gen_secret),
            patch.object(admin_users, "_otpauth_uri", m.otpauth),
            patch.object(admin_users, "_with_2fa_status",
                         lambda item: dict(item, saw_2fa=True)),
            patch.object(admin_users, "datetime", _FrozenDatetime),
            patch("app.patient_web.catalog.centers",
                  return_value=[{"slug": "c1"}, {"slug": "c2"}]),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def edit(self, body, usuario="op"):
        return admin_users.edit_user(usuario, body)

    def error(self, body, usuario="op"):
        with self.assertRaises(ApiError) as ctx:
            admin_users.edit_user(usuario, body)
        return ctx.exception

    def update_kwargs(self):
        self.m.operator_update.assert_called_once()
        return self.m.operator_update.call_args.kwargs

    def names(self):
        return [c[0] for c in self.m.mock_calls]

    def assert_nothing_written(self):
        self.assertNotIn("operator_update", self.names())
        self.assertNotIn("set_overrides", self.names())
        self.assertNotIn("set_deadline", self.names())


class TestEditUserLookup(_EditUserCase):
    def test_owner_cannot_be_edited_and_nothing_is_read(self):
        err = self.error({"rol": "admin"}, usuario="the-owner")
        self.assertEqual((err.status, err.body), (400, {
            "error": "El usuario dueño no se edita desde acá."}))
        self.assertEqual(self.m.mock_calls, [])

    def test_unknown_user_is_a_404(self):
        self.m.operator_get.return_value = None
        err = self.error({"rol": "admin"}, usuario="ghost")
        self.assertEqual((err.status, err.body), (404, {"error": "no encontrado"}))
        self.assertEqual(self.m.mock_calls, [call.operator_get("ghost")])

    def test_empty_body_changes_nothing_and_returns_the_wrapped_item(self):
        out = self.edit({})
        kwargs = self.update_kwargs()
        self.assertEqual(self.m.operator_update.call_args.args, ("op",))
        self.assertEqual(kwargs, {
            "rol": None, "activo": None, "password": None,
            "totp_secret": db_operators.NO_CHANGE, "medico_id": db_operators.NO_CHANGE,
            "email": db_operators.NO_CHANGE, "centros": db_operators.NO_CHANGE})
        self.assertIs(kwargs["totp_secret"], db_operators.NO_CHANGE)
        self.assertIs(kwargs["medico_id"], db_operators.NO_CHANGE)
        self.assertIs(kwargs["email"], db_operators.NO_CHANGE)
        self.assertIs(kwargs["centros"], db_operators.NO_CHANGE)
        self.assertEqual(out, {"ok": True, "item": dict(self.updated, saw_2fa=True,
                                                         es_dueno=False)})
        self.assertEqual(self.names(), ["operator_get", "portal_enabled", "operator_update"])
        self.assertNotIn("guard", self.names())
        self.assertNotIn("set_deadline", self.names())


class TestEditUserFields(_EditUserCase):
    def test_role_must_be_assignable(self):
        err = self.error({"rol": "nope"})
        self.assertEqual((err.status, err.body), (400, {"error": _MSG_ROL, "field": "rol"}))
        self.m.role_is_assignable.assert_called_once_with("nope")
        self.assert_nothing_written()

    def test_a_role_is_only_checked_when_it_is_in_the_body(self):
        self.edit({"activo": True})
        self.m.role_is_assignable.assert_not_called()
        self.edit({"rol": None})
        self.m.role_is_assignable.assert_not_called()

    def test_role_is_passed_through(self):
        self.edit({"rol": "reportes"})
        self.assertEqual(self.update_kwargs()["rol"], "reportes")

    def test_activo_is_coerced_to_bool_only_when_present(self):
        for raw, expected in ((0, False), ("", False), ("x", True), (True, True), (None, None)):
            with self.subTest(raw=raw):
                self.m.operator_update.reset_mock()
                self.edit({"activo": raw})
                self.assertIs(self.update_kwargs()["activo"], expected)

    def test_email_is_stripped_and_blank_becomes_none(self):
        for raw, expected in ((" new@example.com ", "new@example.com"), ("   ", None),
                              ("", None), (None, None), (0, None)):
            with self.subTest(raw=raw):
                self.m.operator_update.reset_mock()
                self.edit({"email": raw})
                self.assertEqual(self.update_kwargs()["email"], expected)

    def test_invalid_email_is_refused_with_its_field(self):
        for raw in ("nope", "a@b", "a b@c.d", "@x.co"):
            with self.subTest(raw=raw):
                err = self.error({"email": raw})
                self.assertEqual((err.status, err.body),
                                 (400, {"error": _MSG_EMAIL, "field": "email"}))
        self.assert_nothing_written()

    def test_password_is_stripped_and_blank_means_no_change(self):
        for raw, expected in (("  longenough  ", "longenough"), ("   ", None), ("", None),
                              (None, None), (12345678, "12345678")):
            with self.subTest(raw=raw):
                self.m.operator_update.reset_mock()
                self.edit({"password": raw})
                self.assertEqual(self.update_kwargs()["password"], expected)

    def test_short_password_is_refused_after_stripping(self):
        for raw in ("1234567", "  1234567  "):
            with self.subTest(raw=raw):
                err = self.error({"password": raw})
                self.assertEqual((err.status, err.body),
                                 (400, {"error": _MSG_TOO_SHORT, "field": "password"}))
        self.assert_nothing_written()

    def test_eight_characters_are_enough_for_a_password(self):
        self.edit({"password": "12345678"})
        self.assertEqual(self.update_kwargs()["password"], "12345678")


class TestEditUserDoctorAndCenters(_EditUserCase):
    def test_medico_id_is_stripped_and_blank_becomes_none(self):
        for raw, expected in ((" med-1 ", "med-1"), ("", None), (None, None)):
            with self.subTest(raw=raw):
                self.m.operator_update.reset_mock()
                self.edit({"medico_id": raw})
                self.assertEqual(self.update_kwargs()["medico_id"], expected)

    def test_medico_id_is_emptied_by_a_non_medico_role_and_untouched_otherwise(self):
        self.edit({"rol": "admisionista"})
        self.assertIsNone(self.update_kwargs()["medico_id"])
        self.m.operator_update.reset_mock()
        self.edit({"activo": True})
        self.assertIs(self.update_kwargs()["medico_id"], db_operators.NO_CHANGE)

    def test_role_other_than_medico_empties_a_medico_id_from_the_body(self):
        self.edit({"rol": "reportes", "medico_id": "med-1"})
        self.assertIsNone(self.update_kwargs()["medico_id"])
        self.m.valid_doctor.assert_not_called()

    def test_medico_role_needs_a_catalog_doctor_from_the_body(self):
        err = self.error({"rol": "medico", "medico_id": "med-9"})
        self.assertEqual((err.status, err.body),
                         (400, {"error": _MSG_MEDICO, "field": "medico_id"}))
        self.m.valid_doctor.assert_called_once_with("med-9")
        self.assert_nothing_written()

    def test_medico_role_without_any_id_is_refused(self):
        err = self.error({"rol": "medico"})  # the account has no doctor yet
        self.assertEqual((err.status, err.body["field"]), (400, "medico_id"))
        self.m.valid_doctor.assert_called_once_with(None)
        self.assert_nothing_written()

    def test_medico_role_keeps_a_valid_existing_doctor_untouched(self):
        self.existing["medico_id"] = "med-2"
        self.edit({"rol": "medico"})
        self.m.valid_doctor.assert_called_once_with("med-2")
        kwargs = self.update_kwargs()
        self.assertEqual(kwargs["rol"], "medico")
        self.assertIs(kwargs["medico_id"], db_operators.NO_CHANGE)

    def test_account_that_is_already_medico_is_validated_even_without_a_role_change(self):
        self.existing.update(rol="medico", medico_id="med-1")
        self.edit({"activo": True})
        self.m.valid_doctor.assert_called_once_with("med-1")
        self.assertIsNone(self.update_kwargs()["rol"])

    def test_existing_medico_cannot_be_emptied_by_a_blank_id(self):
        self.existing.update(rol="medico", medico_id="med-1")
        err = self.error({"medico_id": ""})
        self.assertEqual((err.status, err.body["field"]), (400, "medico_id"))
        self.m.valid_doctor.assert_called_once_with(None)

    def test_existing_medico_with_a_new_valid_id(self):
        self.existing.update(rol="medico", medico_id="med-1")
        self.edit({"medico_id": "med-2"})
        self.m.valid_doctor.assert_called_once_with("med-2")
        self.assertEqual(self.update_kwargs()["medico_id"], "med-2")

    def test_centers_are_normalised_through_the_catalog(self):
        self.edit({"centros": [" c1 ", "c1", "", None, "c2"]})
        self.assertEqual(self.update_kwargs()["centros"], ["c1", "c2"])

    def test_centers_null_means_empty_not_no_change(self):
        self.edit({"centros": None})
        self.assertEqual(self.update_kwargs()["centros"], [])

    def test_centers_must_be_a_list(self):
        err = self.error({"centros": "c1"})
        self.assertEqual((err.status, err.body),
                         (400, {"error": "centros debe ser una lista de centros"}))
        self.assert_nothing_written()

    def test_unknown_center_is_refused_by_name(self):
        err = self.error({"centros": ["c1", "zz"]})
        self.assertEqual((err.status, err.body),
                         (400, {"error": "El centro «zz» no existe."}))
        self.assert_nothing_written()

    def test_admin_is_never_scoped_to_centers(self):
        self.edit({"rol": "admin", "centros": ["c1"]})
        self.assertEqual(self.update_kwargs()["centros"], [])

    def test_an_account_that_is_already_admin_gets_empty_centers_even_without_the_key(self):
        self.existing["rol"] = "admin"
        self.edit({"activo": True})
        self.assertEqual(self.update_kwargs()["centros"], [])

    def test_an_invalid_center_list_is_refused_even_for_an_admin(self):
        self.existing["rol"] = "admin"
        err = self.error({"centros": "c1"})
        self.assertEqual(err.status, 400)


class TestEditUserPortalEmail(_EditUserCase):
    def setUp(self):
        super().setUp()
        self.m.portal_enabled.return_value = True

    def test_taken_email_on_a_portal_installation_is_a_409(self):
        self.m.email_taken.return_value = True
        err = self.error({"email": "other@example.com"})
        self.assertEqual((err.status, err.body),
                         (409, {"error": _MSG_PORTAL, "field": "email"}))
        self.m.email_taken.assert_called_once_with("other@example.com", except_usuario="op")
        self.assert_nothing_written()

    def test_unchanged_email_is_checked_with_the_stored_one(self):
        self.edit({"rol": "reportes"})
        self.m.email_taken.assert_called_once_with("op@example.com", except_usuario="op")

    def test_activo_in_the_body_overrides_the_stored_flag(self):
        self.existing["activo"] = False
        self.edit({"rol": "reportes"})
        self.m.email_taken.assert_not_called()  # inactive accounts do not claim an email
        self.edit({"activo": True})
        self.m.email_taken.assert_called_once_with("op@example.com", except_usuario="op")

    def test_deactivating_skips_the_email_check(self):
        self.edit({"activo": False})
        self.m.email_taken.assert_not_called()

    def test_clearing_the_email_skips_the_check(self):
        self.edit({"email": ""})
        self.m.email_taken.assert_not_called()


class TestEditUserOverrides(_EditUserCase):
    def test_overrides_are_cleaned_applied_after_the_update_and_echoed(self):
        out = self.edit({"permisos_agregados": ["pacientes", "cola"],
                         "permisos_quitados": ["solicitudes"]})
        # Cleaning sorts into the canonical permission order.
        expected = (["cola", "pacientes"], ["solicitudes"])
        self.m.set_overrides.assert_called_once_with("op", *expected)
        self.assertEqual(out["item"]["permisos_agregados"], expected[0])
        self.assertEqual(out["item"]["permisos_quitados"], expected[1])
        self.assertLess(self.names().index("operator_update"),
                        self.names().index("set_overrides"))

    def test_one_override_key_is_enough_and_the_other_defaults_to_empty(self):
        out = self.edit({"permisos_quitados": ["cola"]})
        self.m.set_overrides.assert_called_once_with("op", [], ["cola"])
        self.assertEqual(out["item"]["permisos_agregados"], [])
        self.assertEqual(out["item"]["permisos_quitados"], ["cola"])

    def test_without_override_keys_nothing_is_set_or_echoed(self):
        out = self.edit({"rol": "reportes"})
        self.m.set_overrides.assert_not_called()
        self.assertNotIn("permisos_agregados", out["item"])
        self.assertNotIn("permisos_quitados", out["item"])

    def test_unknown_permission_is_refused_with_the_error_field(self):
        err = self.error({"permisos_agregados": ["nope"]})
        self.assertEqual((err.status, err.body),
                         (400, {"error": "Hay un permiso que no existe.",
                                "field": "permissions"}))
        self.assert_nothing_written()

    def test_permission_in_both_lists_is_refused(self):
        err = self.error({"permisos_agregados": ["cola"], "permisos_quitados": ["cola"]})
        self.assertEqual((err.status, err.body["field"]), (400, "overrides"))
        self.assert_nothing_written()

    def test_a_role_error_without_a_field_falls_back_to_permisos(self):
        boom = permissions.RoleError("code", "custom message", None, status=422)
        with patch.object(permissions, "clean_overrides", side_effect=boom):
            err = self.error({"permisos_agregados": []})
        self.assertEqual((err.status, err.body),
                         (422, {"error": "custom message", "field": "permisos"}))


class TestEditUserLockoutGuard(_EditUserCase):
    def test_guard_is_skipped_when_nothing_it_protects_is_touched(self):
        self.edit({"email": "x@example.com", "password": "longenough", "centros": ["c1"]})
        self.m.guard.assert_not_called()

    def test_role_change_goes_to_the_guard(self):
        self.edit({"rol": "reportes"})
        self.m.guard.assert_called_once_with(
            operator_change={"usuario": "op", "slug": "reportes"})

    def test_activo_false_goes_to_the_guard(self):
        self.edit({"activo": False})
        self.m.guard.assert_called_once_with(
            operator_change={"usuario": "op", "active": False})

    def test_overrides_go_to_the_guard_as_granted_and_denied(self):
        self.edit({"permisos_agregados": ["cola"], "permisos_quitados": ["pacientes"]})
        self.m.guard.assert_called_once_with(
            operator_change={"usuario": "op", "granted": ["cola"], "denied": ["pacientes"]})

    def test_everything_together_keeps_all_keys(self):
        self.edit({"rol": "reportes", "activo": True, "permisos_quitados": ["cola"]})
        self.m.guard.assert_called_once_with(operator_change={
            "usuario": "op", "slug": "reportes", "active": True,
            "granted": [], "denied": ["cola"]})

    def test_guard_runs_before_anything_is_written(self):
        self.edit({"rol": "reportes", "permisos_agregados": ["cola"]})
        names = self.names()
        self.assertLess(names.index("guard"), names.index("operator_update"))

    def test_guard_refusal_stops_the_update(self):
        for field, expected in ((None, "rol"), ("usuarios", "usuarios")):
            with self.subTest(field=field):
                boom = permissions.RoleError("ultimo", "Debe quedar alguien.", field, status=409)
                self.m.guard.side_effect = boom
                err = self.error({"activo": False})
                self.assertEqual((err.status, err.body),
                                 (409, {"error": "Debe quedar alguien.", "field": expected}))
                self.assert_nothing_written()

    def test_unreadable_store_degrades_with_one_log_line_and_the_update_goes_on(self):
        self.m.guard.side_effect = db.DbUnavailable("down")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out = self.edit({"activo": False})
        self.assertEqual(
            buf.getvalue(),
            "[usuarios] permisos ilegibles en assert_user_manager_remains: "
            "se degrada a los roles de siempre\n")
        self.assertTrue(out["ok"])
        self.assertEqual(self.update_kwargs()["activo"], False)

    def test_any_other_error_from_the_guard_is_not_swallowed(self):
        self.m.guard.side_effect = RuntimeError("bug")
        with self.assertRaises(RuntimeError):
            self.edit({"activo": False})
        self.assert_nothing_written()


class TestEditUserTotp(_EditUserCase):
    def test_generate_stores_a_new_secret_and_returns_it_once(self):
        out = self.edit({"totp": "generar"})
        self.assertEqual(self.update_kwargs()["totp_secret"], "NEWSECRET")
        self.assertEqual(out["totp"], {"secret": "NEWSECRET",
                                       "otpauth_url": "otpauth://op/NEWSECRET"})
        self.m.set_deadline.assert_called_once_with("op", None)

    def test_remove_clears_the_secret_and_opens_a_week_to_set_a_new_one(self):
        out = self.edit({"totp": "quitar"})
        self.assertIsNone(self.update_kwargs()["totp_secret"])
        self.assertNotIn("totp", out)
        self.m.set_deadline.assert_called_once_with("op", _WEEK)

    def test_action_is_stripped(self):
        self.edit({"totp": "  generar "})
        self.assertEqual(self.update_kwargs()["totp_secret"], "NEWSECRET")

    def test_empty_or_falsy_action_changes_nothing(self):
        for raw in ("", "   ", None, 0, False):
            with self.subTest(raw=raw):
                self.m.operator_update.reset_mock()
                self.m.set_deadline.reset_mock()
                self.edit({"totp": raw})
                self.assertIs(self.update_kwargs()["totp_secret"], db_operators.NO_CHANGE)
                self.m.set_deadline.assert_not_called()

    def test_unknown_action_is_refused_without_a_field(self):
        err = self.error({"totp": "borrar"})
        self.assertEqual((err.status, err.body), (400, {"error": _MSG_TOTP}))
        self.assert_nothing_written()

    def test_forcing_2fa_revokes_the_secret_and_sets_the_deadline_now(self):
        self.edit({"forzar_2fa": True})
        self.assertIsNone(self.update_kwargs()["totp_secret"])
        self.m.set_deadline.assert_called_once_with("op", _NOW)

    def test_forcing_2fa_with_a_remove_action_revokes_once_and_uses_now(self):
        self.edit({"forzar_2fa": True, "totp": "quitar"})
        self.assertIsNone(self.update_kwargs()["totp_secret"])
        self.m.set_deadline.assert_called_once_with("op", _NOW)

    def test_forcing_2fa_while_generating_is_a_conflict(self):
        err = self.error({"forzar_2fa": True, "totp": "generar"})
        self.assertEqual((err.status, err.body), (400, {"error": _MSG_FORZAR}))
        self.assert_nothing_written()

    def test_forcing_2fa_with_a_bad_action_reports_the_bad_action(self):
        err = self.error({"forzar_2fa": True, "totp": "borrar"})
        self.assertEqual(err.body, {"error": _MSG_TOTP})

    def test_forcing_off_without_2fa_gives_a_week(self):
        self.updated["tiene_2fa"] = False
        self.edit({"forzar_2fa": False})
        self.assertIs(self.update_kwargs()["totp_secret"], db_operators.NO_CHANGE)
        self.m.set_deadline.assert_called_once_with("op", _WEEK)

    def test_forcing_off_with_2fa_clears_the_deadline(self):
        self.updated["tiene_2fa"] = True
        self.edit({"forzar_2fa": False})
        self.m.set_deadline.assert_called_once_with("op", None)

    def test_forcing_off_while_generating_keeps_the_new_secret(self):
        self.updated["tiene_2fa"] = True
        out = self.edit({"forzar_2fa": False, "totp": "generar"})
        self.assertEqual(self.update_kwargs()["totp_secret"], "NEWSECRET")
        self.m.set_deadline.assert_called_once_with("op", None)  # only one write
        self.assertEqual(out["totp"]["secret"], "NEWSECRET")

    def test_forzar_key_wins_over_a_generate_action_for_the_deadline(self):
        self.updated["tiene_2fa"] = False
        self.edit({"forzar_2fa": False, "totp": "generar"})
        self.m.set_deadline.assert_called_once_with("op", _WEEK)

    def test_no_2fa_keys_means_no_deadline_write(self):
        self.edit({"rol": "reportes"})
        self.m.set_deadline.assert_not_called()


class TestEditUserFailures(_EditUserCase):
    def test_operator_conflict_becomes_a_409_chained_to_the_cause(self):
        exc = db_operators.OperatorConflict("medico_con_usuario")
        self.m.operator_update.side_effect = exc
        err = self.error({"rol": "reportes"})
        self.assertEqual(err.status, 409)
        self.assertEqual(err.body, admin_users._operator_conflict(exc).body)
        self.assertIs(err.__cause__, exc)
        self.assertNotIn("set_overrides", self.names())
        self.assertNotIn("set_deadline", self.names())

    def test_other_update_errors_propagate(self):
        self.m.operator_update.side_effect = RuntimeError("bug")
        with self.assertRaises(RuntimeError):
            self.edit({"rol": "reportes"})

    def test_first_failing_check_wins(self):
        cases = [
            ("role before overrides", {"rol": "nope", "permisos_agregados": ["nope"]}, "rol"),
            ("overrides before email", {"permisos_agregados": ["nope"], "email": "bad"},
             "permissions"),
            ("email before doctor", {"email": "bad", "rol": "medico"}, "email"),
            ("doctor before centers", {"rol": "medico", "centros": "c1"}, "medico_id"),
            ("password before totp", {"password": "short", "totp": "borrar"}, "password"),
        ]
        for label, body, field in cases:
            with self.subTest(label):
                self.assertEqual(self.error(body).body.get("field"), field)
        self.assert_nothing_written()

    def test_centers_error_comes_before_the_portal_conflict_and_the_password(self):
        self.m.portal_enabled.return_value = True
        self.m.email_taken.return_value = True
        err = self.error({"centros": "c1", "email": "other@example.com", "password": "x"})
        self.assertEqual(err.body, {"error": "centros debe ser una lista de centros"})

    def test_portal_conflict_comes_before_the_password_and_the_totp(self):
        self.m.portal_enabled.return_value = True
        self.m.email_taken.return_value = True
        err = self.error({"email": "other@example.com", "password": "x", "totp": "borrar"})
        self.assertEqual((err.status, err.body["field"]), (409, "email"))

    def test_totp_checks_come_before_the_lockout_guard(self):
        err = self.error({"activo": False, "totp": "borrar"})
        self.assertEqual(err.body, {"error": _MSG_TOTP})
        self.m.guard.assert_not_called()


class TestEditUserFullRun(_EditUserCase):
    def test_every_side_effect_runs_in_the_documented_order(self):
        self.updated.update(rol="medico", tiene_2fa=False)
        out = self.edit({
            "rol": "medico", "medico_id": " med-1 ", "centros": ["c2", "c1"],
            "activo": True, "email": " doc@example.com ", "password": " longenough ",
            "totp": "generar", "forzar_2fa": False,
            "permisos_agregados": ["cola"], "permisos_quitados": ["pacientes"]})
        self.assertEqual(self.names(), [
            "operator_get", "role_is_assignable", "valid_doctor", "portal_enabled",
            "gen_secret", "guard", "operator_update", "set_overrides",
            "set_deadline", "otpauth"])
        self.m.guard.assert_called_once_with(operator_change={
            "usuario": "op", "slug": "medico", "active": True,
            "granted": ["cola"], "denied": ["pacientes"]})
        self.assertEqual(self.m.operator_update.call_args, call(
            "op", rol="medico", activo=True, password="longenough",
            totp_secret="NEWSECRET", medico_id="med-1", email="doc@example.com",
            centros=["c2", "c1"]))
        self.m.set_overrides.assert_called_once_with("op", ["cola"], ["pacientes"])
        self.m.set_deadline.assert_called_once_with("op", _WEEK)
        self.assertEqual(out, {
            "ok": True,
            "item": dict(self.updated, saw_2fa=True, es_dueno=False,
                         permisos_agregados=["cola"], permisos_quitados=["pacientes"]),
            "totp": {"secret": "NEWSECRET", "otpauth_url": "otpauth://op/NEWSECRET"}})


if __name__ == "__main__":
    unittest.main()
