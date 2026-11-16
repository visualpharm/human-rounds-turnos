"""tests/routes/test_body_contract_account.py — the patient account's JSON
routes (`cuentas/accounts_router.py`, `cuentas/account_photo_router.py`):
every answer to the body battery of `_body_contract.py`, pinned against
`tests/golden/body_contract_account.json` (TASK-24, Pydantic stage 2).
"""

import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TEST_DIR)
for _p in (_ROOT, _HERE, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _routes_fixture import RouteTestCase  # noqa: E402
import _body_contract as bc  # noqa: E402

from app.core import db_accounts  # noqa: E402
from app.cuentas import api_models as m  # noqa: E402
from app.cuentas.health_coverage import revision  # noqa: E402

_DNI = "30999800"
_PASS = "clave-segura-1"


def _png_b64():
    import base64
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (200, 30, 30)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")

_GOLDEN = bc.Golden("account")

_PROFILE = {
    "nombre": "Ana", "apellido": "Paz", "sexo": "F", "nacimiento": "1990-01-02",
    "contacto": "+542254000801", "email": "ana@example.com",
    "cobertura": {"obras_sociales": [{"nombre": "IOMA", "afiliado": "123"}]},
    "domicilio": {"calle": "Mitre", "numero": "10"},
}


class TestAccountBodyContract(RouteTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from app.cuentas import account_photo
        cls._photos_before = account_photo.PHOTOS_DIR
        cls._photos_tmp = tempfile.mkdtemp(prefix="body-contract-photos-")
        account_photo.PHOTOS_DIR = cls._photos_tmp

    @classmethod
    def tearDownClass(cls):
        from app.cuentas import account_photo
        account_photo.PHOTOS_DIR = cls._photos_before
        shutil.rmtree(cls._photos_tmp, ignore_errors=True)
        if bc.WRITE:
            _GOLDEN.save()
        super().tearDownClass()

    def _clean(self):
        from app.cuentas import accounts
        self.db.truncate_all()
        accounts._REGISTERED.clear()
        accounts._LOGIN_ATTEMPTS.clear()
        for name in os.listdir(self._photos_tmp):
            os.remove(os.path.join(self._photos_tmp, name))

    def _anonymous(self):
        self._clean()
        db_accounts.create_user(_DNI, "Ana", "Paz", "1990-01-02", "F", "+542254000800", _PASS)
        return {}

    def _session(self):
        self._anonymous()
        return {"Cookie": "st_ses=" + db_accounts.create_session(_DNI)}

    def _pin(self, path, valid, docs, session=False):
        bc.pin_route(self, _GOLDEN, "POST", path, valid,
                     reset=self._session if session else self._anonymous,
                     auth=True if session else None, docs=docs)

    def test_no_body_crashes_a_route(self):
        """Since stage 2 (hospital, 26.10) a body the pages never send is a 400 of
        the family, not a 500: the golden file holds no 500 at all."""
        crashes = [(route, case) for route, cases in _GOLDEN.sections.items()
                   for case, got in cases.items() if got["status"] == 500]
        self.assertEqual(crashes, [])

    def test_register(self):
        self._pin("/api/account/register", dict(_PROFILE, dni="30999801", password=_PASS),
                  (m.RegisterIn, m.AccountOut))

    def test_login(self):
        self._pin("/api/account/login", {"dni": _DNI, "password": _PASS},
                  (m.LoginIn, m.AccountOut))

    def test_test_login(self):
        self._pin("/api/account/test-login", {"provider": "renaper", "dni": _DNI, "sexo": "F"},
                  (m.TestLoginIn, m.AccountOut))

    def test_recover(self):
        self._pin("/api/account/recover", {"dni": _DNI, "email": "ana@example.com"},
                  (m.RecoverIn, m.OkOut))

    def test_reset(self):
        self._pin("/api/account/reset", {"token": "no-such-token", "password": "clave-nueva-12"},
                  (m.ResetIn, m.AccountOut))

    def test_reset_otp(self):
        self._pin("/api/account/reset_otp",
                  {"dni": _DNI, "code": "123456", "password": "clave-nueva-12"},
                  (m.ResetOtpIn, m.AccountOut))

    def test_update(self):
        self._pin("/api/account/update",
                  dict(_PROFILE, avisos={"whatsapp": True, "email": False}, lang="es",
                       coverage_revision=revision(None)),
                  (m.UpdateIn, m.AccountOut), session=True)

    def test_add_dependent(self):
        self._pin("/api/account/dependents",
                  {"dni": "40999801", "nombre": "Leo", "apellido": "Paz",
                   "nacimiento": "2015-05-05", "sexo": "M", "vinculo": "hijo",
                   "declaracion_jurada": True},
                  (m.DependentIn, m.DependentOut), session=True)

    def test_delete_dependent(self):
        self._pin("/api/account/dependents/delete", {"dni": "40999801"},
                  (m.DependentRefIn, m.OkOut), session=True)

    def test_save_test_identity(self):
        self._pin("/api/account/test-identities",
                  {"identidad": {"dni": "20999801", "nombre": "Test"}, "original_dni": None},
                  (m.TestIdentityIn, m.TestIdentityOut))

    def test_otp_send(self):
        self._pin("/api/account/otp/send", {"contacto": "+542254000801", "proposito": "registro"},
                  (m.OtpSendIn, m.OkOut))

    def test_otp_verify(self):
        self._pin("/api/account/otp/verify",
                  {"contacto": "+542254000801", "proposito": "registro", "code": "123456"},
                  (m.OtpVerifyIn, m.OkOut))

    def test_photo_upload(self):
        self._pin("/api/account/photo", {"data_b64": "data:image/png;base64," + _png_b64()},
                  (m.PhotoIn, m.OkOut), session=True)

if __name__ == "__main__":
    unittest.main()
