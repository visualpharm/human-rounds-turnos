"""tests/routes/test_body_contract_staff.py — the staff member's own JSON
routes: login and passkeys (`auth/admin_auth_router.py`,
`auth/passkeys_router.py`), the password reset, the login panel's events,
the own account (`admin_panel/admin_account_router.py`) and the panel
preferences: every answer to the body battery of `_body_contract.py`, pinned
against `tests/golden/body_contract_staff.json` (TASK-24, Pydantic stage 4).
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TEST_DIR)
for _p in (_ROOT, _HERE, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _routes_fixture import RouteTestCase, admin_cookie  # noqa: E402
import _body_contract as bc  # noqa: E402

from app.admin_panel import api_models as p  # noqa: E402
from app.auth import api_models as a  # noqa: E402
from app.core import db_operators  # noqa: E402

_USUARIO = "staff.pin"
_PASS = "clave-staff-12"
_GOLDEN = bc.Golden("staff")


class TestStaffBodyContract(RouteTestCase):

    @classmethod
    def tearDownClass(cls):
        if bc.WRITE:
            _GOLDEN.save()
        super().tearDownClass()

    def _clean(self):
        from app.auth import admin_auth, panel_login, password_reset
        self.db.truncate_all()
        admin_auth._ATTEMPTS.clear()
        panel_login._HITS.clear()
        password_reset._REQUESTS.clear()
        db_operators.operator_create(_USUARIO, password=_PASS, rol="admin")

    def _anonymous(self):
        self._clean()
        return {}

    def _staff(self):
        self._clean()
        return admin_cookie(usuario=_USUARIO, rol="admin")

    def _pin(self, path, valid, reset, method="POST", session=False, docs=None):
        bc.pin_route(self, _GOLDEN, method, path, valid, reset=reset,
                     auth=True if session else None, docs=docs)

    def test_no_body_crashes_a_route(self):
        """Since stage 4 a body the panel never sends is a 400 of the family, not
        a 500: the golden file holds no 500 at all."""
        crashes = [(route, case) for route, cases in _GOLDEN.sections.items()
                   for case, got in cases.items() if got["status"] == 500]
        self.assertEqual(crashes, [])

    def test_login(self):
        self._pin("/api/admin/login",
                  {"usuario": _USUARIO, "password": _PASS, "codigo": "", "turnstile_token": ""},
                  self._anonymous,
                  docs=(a.StaffLoginIn, a.StaffLoginOut))

    def test_passkey_register(self):
        self._pin("/api/admin/passkeys/register",
                  {"challenge": "no-such-challenge", "nombre": "llave", "id": "cred-1"},
                  self._staff, session=True,
                  docs=(a.PasskeyRegisterIn, a.PasskeyOut))

    def test_passkey_delete(self):
        self._pin("/api/admin/passkeys/delete", {"credential_id": "cred-1"},
                  self._staff, session=True,
                  docs=(a.PasskeyDeleteIn, a.StaffOkOut))

    def test_passkey_reset(self):
        self._pin("/api/admin/passkeys/reset", {"usuario": _USUARIO}, self._staff, session=True,
                  docs=(a.PasskeyResetIn, a.PasskeyResetOut))

    def test_passkey_login(self):
        self._pin("/api/admin/passkeys/login", {"challenge": "no-such-challenge", "id": "cred-1"},
                  self._anonymous,
                  docs=(a.PasskeyLoginIn, a.StaffLoginOut))

    def test_reset_password(self):
        self._pin("/api/admin/reset-password",
                  {"token": "no-such-token", "password": "clave-nueva-12"}, self._anonymous,
                  docs=(a.ResetPasswordIn, None))

    def test_login_panel_event(self):
        self._pin("/api/login-panel/event",
                  {"sid": "a1b2c3d4", "variante": "humor", "evento": "tab", "tab": "consejos",
                   "item": "meme-1", "ms": 10},
                  self._anonymous,
                  docs=(a.PanelEventIn, None))

    def test_account_profile(self):
        self._pin("/api/admin/account/profile", {"email": "staff@example.com"},
                  self._staff, session=True,
                  docs=(p.AccountProfileIn, p.AccountProfileOut))

    def test_account_password(self):
        self._pin("/api/admin/account/password",
                  {"actual": _PASS, "nueva": "otra-clave-123", "usuario": _USUARIO},
                  self._staff, session=True,
                  docs=(p.AccountPasswordIn, p.AccountPasswordOut))

    def test_account_2fa_confirm(self):
        self._pin("/api/admin/account/2fa/confirm", {"setup_token": "x", "codigo": "123456"},
                  self._staff, session=True,
                  docs=(p.TotpConfirmIn, p.TotpOut))

    def test_account_2fa_remove(self):
        self._pin("/api/admin/account/2fa/remove", {"actual": _PASS}, self._staff, session=True,
                  docs=(p.TotpRemoveIn, p.TotpOut))

    def test_preferences(self):
        self._pin("/api/admin/preferences", {"key": "tema", "value": "oscuro"},
                  self._staff, method="PUT", session=True,
                  docs=(p.PreferenceIn, p.PreferenceOut))


if __name__ == "__main__":
    unittest.main()
