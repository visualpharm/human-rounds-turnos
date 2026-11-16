"""tests/routes/test_settings_response_models.py — every operation of the
staff panel's settings, content, users and roles, staff sign-in and the
platform (`tests/_settings_area.py`) answered by the live app, and the answer
checked against the model the OpenAPI schema names for it (TASK-76, OpenAPI
stage D, area d): the success status the schema documents, a JSON body that
matches the model, and no top-level key the model does not name.

The excluded operations outside /api/ that take no path parameter are asked
too: a success there is never a JSON document (a JSON one would need a
model). `test_every_operation_of_the_area_is_answered_here` keeps the list
complete: a new documented operation of the area needs its answer here, or a
pinned success in the golden files.
"""

import base64
import hashlib
import io
import json
import os
import secrets
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TEST_DIR)
for _p in (_ROOT, _HERE, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _env  # noqa: E402
from _routes_fixture import RouteTestCase, admin_cookie  # noqa: E402
import _response_models as rm  # noqa: E402
import _settings_area as area  # noqa: E402

from app.core import api_body, db_operators  # noqa: E402

_ADMIN = "staff.settings"
_MEDICO_ID = "gonzalez-juan"

# (method, path template) -> the test that answers it.
_ANSWERED = {}


def _png_b64():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (30, 120, 60)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _row_id(row):
    """The id of a catalog row as the panel lists it (a centre's is its `slug`)."""
    return row.get("id") or row["slug"]


class _Authenticator:
    """A passkey made up for the test: its own P-256 key, signing as a device would
    (`tests/test_passkeys.py` has the same one)."""

    def __init__(self, rp_id="127.0.0.1"):
        from cryptography.hazmat.primitives.asymmetric import ec
        self.rp_id = rp_id
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(16)

    def _cose(self):
        numbers = self.key.public_key().public_numbers()
        return {1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")}

    def _auth_data(self, with_credential):
        import cbor2
        data = hashlib.sha256(self.rp_id.encode()).digest()
        data += bytes([0x01 | (0x40 if with_credential else 0)]) + (0).to_bytes(4, "big")
        if with_credential:
            data += b"\x00" * 16 + len(self.credential_id).to_bytes(2, "big") + self.credential_id
            data += cbor2.dumps(self._cose())
        return data

    @staticmethod
    def _client_data(kind, challenge, origin):
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin,
                           "crossOrigin": False}).encode("utf-8")

    def create(self, challenge, origin):
        import cbor2
        from app.auth import passkeys
        client = self._client_data("webauthn.create", challenge, origin)
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": self._auth_data(True)})
        return {"challenge": challenge, "id": passkeys.b64u(self.credential_id),
                "response": {"clientDataJSON": passkeys.b64u(client),
                             "attestationObject": passkeys.b64u(attestation)}}

    def get(self, challenge, origin):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from app.auth import passkeys
        client = self._client_data("webauthn.get", challenge, origin)
        data = self._auth_data(False)
        signature = self.key.sign(data + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {"challenge": challenge, "id": passkeys.b64u(self.credential_id),
                "response": {"clientDataJSON": passkeys.b64u(client),
                             "authenticatorData": passkeys.b64u(data),
                             "signature": passkeys.b64u(signature)}}


def answers(*operations):
    def mark(fn):
        for operation in operations:
            _ANSWERED[operation] = fn.__name__
        return fn
    return mark


def _schema_and_app():
    from app import main as app_module
    from app.routers import api_docs
    return api_docs.build_schema(app_module.app), app_module.app


class _AreaCase(RouteTestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        schema, app = _schema_and_app()
        cls.operations = {(method, path): operation
                          for method, path, operation in area.area_operations(schema, app)}
        cls.documented = {key: rm.success_answer(operation) for key, operation in cls.operations.items()}
        cls.pinned = rm.pinned_by_template(app)

    def setUp(self):
        super().setUp()
        from app.auth import mode as auth_mode
        from app.core import config
        # Settings are cached for a few seconds: the class before this one read its own.
        for reset in (lambda: auth_mode._cache.update(v=None, t=0.0), config._reset_cache_para_tests):
            reset()
            self.addCleanup(reset)

    def _ask(self, method, path, headers=None, body=None):
        """A JSON body as `_request` sends it; bytes as they are (a CSV file)."""
        if not isinstance(body, bytes):
            return self._request(method.upper(), path, headers=headers, body=body)
        from _livehttp import call
        status, resp_headers, raw = call(self._base_url, method.upper(), path, data=body,
                                         headers=dict(headers or {}, **{"Content-Type": "text/csv"}))
        return status, {k.title(): v for k, v in dict(resp_headers or {}).items()}, raw

    def _answer(self, method, template, path=None, headers=None, body=None):
        """Ask `method path` (`template` filled in) and check the answer against the
        documented model; returns the decoded body."""
        status, hdrs, raw = self._ask(method, path or template, headers=headers, body=body)
        code, name = self.documented[(method, template)]
        self.assertEqual(status, int(code), f"{method.upper()} {path or template}: {raw[:300]!r}")
        if name is None:
            self.assertEqual(raw, b"")
            return None
        self.assertTrue(hdrs.get("Content-Type", "").startswith("application/json"),
                        hdrs.get("Content-Type"))
        value = json.loads(raw.decode("utf-8"))
        with self.subTest(operation=(method, path or template)):
            rm.check_answer(self, api_body.models()[name], value)
        return value

    def _staff(self, usuario=_ADMIN, rol="admin", **extra):
        """A staff account in the database and its session cookie."""
        db_operators.operator_create(usuario, password="throwaway-12345", rol=rol, **extra)
        return admin_cookie(usuario=usuario, rol=rol)


class TestPlatformAnswers(_AreaCase):

    @answers(("get", "/health"), ("get", "/ready"), ("get", "/api/nav"), ("get", "/api/i18n/{rest}"),
             ("get", "/api/login-panel"), ("get", "/api/admin/config"))
    def test_public_answers(self):
        from app.admin_panel import system_status
        self.assertEqual(self._answer("get", "/health")["status"], "ok")
        with patch.object(system_status, "probe_db", return_value=(True, True, True, "")):
            self.assertEqual(self._answer("get", "/ready"), {"status": "ok"})
        self._answer("get", "/api/nav")
        catalog = self._answer("get", "/api/i18n/{rest}", "/api/i18n/es.json")
        self.assertGreater(len(catalog), 100)
        self._answer("get", "/api/login-panel")
        self._answer("get", "/api/admin/config")

    @answers(("get", "/api/admin/modules"))
    def test_the_modules_by_role(self):
        everything = self._answer("get", "/api/admin/modules", headers=self._staff())
        self.assertIn("modules", everything)
        doctor = self._answer("get", "/api/admin/modules",
                              headers=self._staff("dr.modulos", "medico", medico_id=_MEDICO_ID))
        self.assertNotIn("modules", doctor)


class TestDemoAnswers(_AreaCase):
    """The demo installation's entry points exist only on a demo installation."""

    @classmethod
    def setUpClass(cls):
        _env.set_env(cls, TURNOS_DEMO_MODE="1")
        super().setUpClass()

    @answers(("get", "/api/demo"))
    def test_the_demo(self):
        self.assertTrue(self._answer("get", "/api/demo")["demo"])


class TestStaffSignInAnswers(_AreaCase):

    @answers(("get", "/api/admin/whoami"), ("get", "/api/admin/account"), ("post", "/api/admin/logout"))
    def test_the_session(self):
        cookie = self._staff()
        self.assertEqual(self._answer("get", "/api/admin/whoami", headers=cookie)["usuario"], _ADMIN)
        self._answer("get", "/api/admin/account", headers=cookie)
        self._answer("post", "/api/admin/logout", headers=cookie, body={})

    @answers(("post", "/api/admin/account/2fa/start"), ("post", "/api/admin/account/2fa/confirm"))
    def test_two_factor_enrolment(self):
        import pyotp
        cookie = self._staff()
        started = self._answer("post", "/api/admin/account/2fa/start", headers=cookie, body={})
        self._answer("post", "/api/admin/account/2fa/confirm", headers=cookie, body={
            "setup_token": started["setup_token"], "codigo": pyotp.TOTP(started["secret"]).now()})

    @answers(("post", "/api/admin/passkeys/register-options"), ("post", "/api/admin/passkeys/register"),
             ("get", "/api/admin/passkeys"), ("post", "/api/admin/passkeys/login-options"),
             ("post", "/api/admin/passkeys/login"), ("post", "/api/admin/passkeys/delete"))
    def test_a_passkey(self):
        from app.auth import admin_auth
        owner = admin_auth._admin_user()
        cookie = admin_cookie(usuario=owner, rol="admin")
        key = _Authenticator()
        origin = f"http://127.0.0.1:{self._port}"
        challenge = self._answer("post", "/api/admin/passkeys/register-options", headers=cookie,
                                 body={})["challenge"]
        saved = self._answer("post", "/api/admin/passkeys/register", headers=cookie,
                             body=dict(key.create(challenge, origin), nombre="Llave de prueba"))
        listed = self._answer("get", "/api/admin/passkeys", headers=cookie)
        self.assertEqual(len(listed["passkeys"]), 1)
        challenge = self._answer("post", "/api/admin/passkeys/login-options", body={})["challenge"]
        self.assertEqual(self._answer("post", "/api/admin/passkeys/login",
                                      body=key.get(challenge, origin))["usuario"], owner)
        self._answer("post", "/api/admin/passkeys/delete", headers=cookie,
                     body={"credential_id": saved["passkey"]["credential_id"]})

    @answers(("post", "/api/admin/reset-password"))
    def test_a_password_reset(self):
        from app.auth import password_reset
        from app.core import db_auth
        self._staff(email="staff@example.com")
        # The link first: a request mints a new one, which retires this.
        token = db_auth.admin_reset_token_create(_ADMIN)
        done = self._answer("post", "/api/admin/reset-password", body={
            "token": token, "password": "una-clave-nueva-123"})
        self.assertTrue(done["redirect"])
        with patch.object(password_reset, "_allow_request", return_value=True):
            self.assertTrue(self._answer("post", "/api/admin/reset-password", body={
                "action": "request", "usuario": _ADMIN})["message"])


class TestUsersAndRolesAnswers(_AreaCase):

    @answers(("get", "/api/admin/users"), ("delete", "/api/admin/users/{usuario}"),
             ("get", "/api/admin/preferences"))
    def test_the_accounts(self):
        cookie = self._staff()
        self._staff("mesa.tarde", "admisionista")
        listed = self._answer("get", "/api/admin/users", headers=cookie)
        self.assertIn("mesa.tarde", [row.get("usuario") for row in listed["items"]])
        self._answer("delete", "/api/admin/users/{usuario}", "/api/admin/users/mesa.tarde", headers=cookie)
        self._request("PUT", "/api/admin/preferences", headers=cookie, body={"key": "cola.vista", "value": "lista"})
        self._answer("get", "/api/admin/preferences", headers=cookie)

    @answers(("get", "/api/admin/roles"), ("delete", "/api/admin/roles/{slug}"),
             ("get", "/api/admin/permissions"))
    def test_the_roles(self):
        from app.core import permissions
        cookie = self._staff()
        slug = permissions.create_role("Mesa de tarde", "recepcion", [])["slug"]
        roles = self._answer("get", "/api/admin/roles", headers=cookie)
        self.assertIn(slug, [role["slug"] for role in roles])
        self._answer("delete", "/api/admin/roles/{slug}", f"/api/admin/roles/{slug}", headers=cookie)
        self.assertTrue(self._answer("get", "/api/admin/permissions", headers=cookie))


class TestSettingsAnswers(_AreaCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from app.patient_web import branding
        cls._branding_attrs = {k: getattr(branding, k) for k in dir(branding)
                               if k.endswith("_DIR") and isinstance(getattr(branding, k), str)}
        cls._tmp = tempfile.mkdtemp(prefix="settings-response-models-branding-")
        for k in cls._branding_attrs:
            setattr(branding, k, cls._tmp)

    @classmethod
    def tearDownClass(cls):
        from app.patient_web import branding
        for k, v in cls._branding_attrs.items():
            setattr(branding, k, v)
        shutil.rmtree(cls._tmp, ignore_errors=True)
        super().tearDownClass()

    @answers(("get", "/api/admin/settings"), ("get", "/api/admin/theme"), ("get", "/api/admin/auth-mode"),
             ("get", "/api/admin/turnstile"), ("post", "/api/admin/client-errors"))
    def test_the_settings(self):
        cookie = self._staff()
        self._answer("get", "/api/admin/settings", headers=cookie)
        self._answer("get", "/api/admin/theme", headers=cookie)
        self._answer("get", "/api/admin/auth-mode", headers=cookie)
        self._answer("get", "/api/admin/turnstile", headers=cookie)
        self._answer("post", "/api/admin/client-errors", headers=cookie, body={"message": "prueba"})

    @answers(("get", "/api/admin/branding"), ("delete", "/api/admin/branding/logo"))
    def test_the_branding(self):
        cookie = self._staff()
        self._answer("get", "/api/admin/branding", headers=cookie)
        status, _hdrs, raw = self._request("POST", "/api/admin/branding/logo", headers=cookie,
                                           body={"data_b64": _png_b64()})
        self.assertEqual(status, 200, raw)
        self.assertTrue(self._answer("get", "/api/admin/branding", headers=cookie)["logo"])
        self._answer("delete", "/api/admin/branding/logo", headers=cookie)

    @answers(("get", "/api/admin/legal"), ("get", "/api/admin/legal/{doc}"),
             ("delete", "/api/admin/legal/{doc}"))
    def test_the_legal_texts(self):
        cookie = self._staff()
        docs = self._answer("get", "/api/admin/legal", headers=cookie)["items"]
        doc = docs[0]["id"]
        self._answer("get", "/api/admin/legal/{doc}", f"/api/admin/legal/{doc}", headers=cookie)
        status, _hdrs, raw = self._request("PUT", f"/api/admin/legal/{doc}", headers=cookie,
                                           body={"cuerpo": "Texto de prueba."})
        self.assertEqual(status, 200, raw)
        edited = self._answer("get", "/api/admin/legal/{doc}", f"/api/admin/legal/{doc}", headers=cookie)
        self.assertTrue(edited["editado"])
        self._answer("delete", "/api/admin/legal/{doc}", f"/api/admin/legal/{doc}", headers=cookie)


class TestContentAnswers(_AreaCase):

    def setUp(self):
        super().setUp()
        from app.patient_web import catalog, doctor_photos
        catalog.invalidate()
        self.addCleanup(catalog.invalidate)
        photos = tempfile.mkdtemp(prefix="settings-response-models-photos-")
        self.addCleanup(shutil.rmtree, photos, True)
        stored = patch.object(doctor_photos, "PHOTOS_DIR", photos)
        stored.start()
        self.addCleanup(stored.stop)

    @answers(("get", "/api/admin/content/{tipo}"))
    def test_every_catalog_list(self):
        from app.admin_panel import admin_content
        cookie = self._staff()
        for tipo in sorted(admin_content.TYPE_MAP):
            with self.subTest(tipo=tipo):
                self._answer("get", "/api/admin/content/{tipo}", f"/api/admin/content/{tipo}", headers=cookie)

    @answers(("post", "/api/admin/content/{tipo}"), ("delete", "/api/admin/content/{tipo}/{item_id}"),
             ("post", "/api/admin/content/{tipo}/{item_id}/restaurar"))
    def test_a_doctor_and_its_account(self):
        cookie = self._staff()
        made = self._answer("post", "/api/admin/content/{tipo}", "/api/admin/content/doctors",
                            headers=cookie, body={"nombre_pila": "Carla", "apellido": "Nunez"})
        self.assertTrue(made["cuenta"])
        doctor = made["item"]["id"]
        self._answer("delete", "/api/admin/content/{tipo}/{item_id}",
                     f"/api/admin/content/doctors/{doctor}", headers=cookie)
        base = _row_id(self._answer("get", "/api/admin/content/{tipo}", "/api/admin/content/centers",
                                    headers=cookie)["items"][0])
        self._answer("delete", "/api/admin/content/{tipo}/{item_id}",
                     f"/api/admin/content/centers/{base}", headers=cookie)
        restored = self._answer("post", "/api/admin/content/{tipo}/{item_id}/restaurar",
                                f"/api/admin/content/centers/{base}/restaurar", headers=cookie, body={})
        self.assertTrue(restored["item"])

    @answers(("post", "/api/admin/content/{tipo}/{sub}"))
    def test_the_wiki_checks(self):
        cookie = self._staff()
        self._answer("post", "/api/admin/content/{tipo}/{sub}", "/api/admin/content/knowledge/properties",
                     headers=cookie, body={"content": "La guardia atiende las 24 horas."})
        checked = self._answer("post", "/api/admin/content/{tipo}/{sub}", "/api/admin/content/knowledge/golden",
                               headers=cookie, body={})
        self.assertIn("total", checked)

    @answers(("post", "/api/admin/doctors/import"))
    def test_a_doctors_file(self):
        cookie = self._staff()
        center = self._answer("get", "/api/admin/content/{tipo}", "/api/admin/content/centers",
                              headers=cookie)["items"][0]["name"]
        roster = ("nombre,especialidad,centro,email\n"
                  f"Ana Prueba,Clinica medica,{center},ana@example.test\n").encode("utf-8")
        preview = self._answer("post", "/api/admin/doctors/import",
                               "/api/admin/doctors/import?filename=medicos.csv", headers=cookie, body=roster)
        self.assertTrue(preview["dry_run"])
        made = self._answer("post", "/api/admin/doctors/import",
                            "/api/admin/doctors/import?filename=medicos.csv&dry_run=false",
                            headers=cookie, body=roster)
        self.assertFalse(made["dry_run"])

    @answers(("get", "/api/admin/{entity}/{eid}/photo"), ("post", "/api/admin/{entity}/{eid}/photo"),
             ("delete", "/api/admin/{entity}/{eid}/photo"))
    def test_the_photos(self):
        cookie = self._staff()
        for entity in ("doctors", "centers"):
            eid = _row_id(self._answer("get", "/api/admin/content/{tipo}", f"/api/admin/content/{entity}",
                                       headers=cookie)["items"][0])
            path = f"/api/admin/{entity}/{eid}/photo"
            with self.subTest(entity=entity):
                self._answer("get", "/api/admin/{entity}/{eid}/photo", path, headers=cookie)
                self.assertTrue(self._answer("post", "/api/admin/{entity}/{eid}/photo", path, headers=cookie,
                                             body={"data_b64": _png_b64()})["foto_url"])
                self.assertTrue(self._answer("get", "/api/admin/{entity}/{eid}/photo", path,
                                             headers=cookie)["has_upload"])
                self._answer("delete", "/api/admin/{entity}/{eid}/photo", path, headers=cookie)


class TestSettingsAreaAnswers(_AreaCase):

    def test_every_operation_of_the_area_is_answered_here(self):
        """...or its success is pinned in a golden file (checked by test_settings_response_docs.py)."""
        described = {key for key, operation in self.operations.items()
                     if not area.excluded(key[0], key[1], operation)}
        expected = described - self.pinned
        self.assertEqual(sorted(expected - set(_ANSWERED)), [])
        self.assertEqual(sorted(set(_ANSWERED) - described), [])

    def test_the_excluded_pages_answer_no_json(self):
        """Every excluded GET outside /api/ without a path parameter, asked
        anonymously and as an admin: when it succeeds, it is not JSON."""
        import http.client
        asked = 0
        for (method, path), operation in sorted(self.operations.items()):
            if method != "get" or path.startswith("/api/") or "{" in path \
                    or not area.excluded(method, path, operation):
                continue
            for headers in ({}, admin_cookie()):
                conn = http.client.HTTPConnection("127.0.0.1", self._port, timeout=60)
                try:
                    conn.request("GET", path, headers=headers)
                    answer = conn.getresponse()
                    answer.read()
                finally:
                    conn.close()
                with self.subTest(path=path, admin=bool(headers)):
                    if 200 <= answer.status < 300:
                        self.assertFalse((answer.getheader("Content-Type") or "").startswith(
                            "application/json"), "a JSON success needs a model, not an exclusion")
                    if 300 <= answer.status < 400:
                        self.assertTrue(answer.getheader("Location"))
                asked += 1
        self.assertGreater(asked, 100)


if __name__ == "__main__":
    unittest.main()
