"""Regression gate for native admin auth without request-shaped peer clones."""

import ast
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.auth import admin_auth, admin_auth_router, passkeys_router  # noqa: E402


_REQUEST_FIELDS = frozenset({"headers", "client_address", "path", "command"})
_AUTH_SOURCES = (
    "app/auth/admin_auth.py",
    "app/auth/admin_auth_router.py",
    "app/auth/passkeys.py",
    "app/auth/passkeys_router.py",
    "app/auth/turnstile.py",
    "app/auth/ip_exempt.py",
    "app/core/proxy_trust.py",
)


def _class_fields(node):
    fields = set()
    for item in ast.walk(node):
        if isinstance(item, ast.AnnAssign):
            target = item.target
            if isinstance(target, ast.Name):
                fields.add(target.id)
            elif (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                fields.add(target.attr)
        elif isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name) and target.id == "__slots__":
                    values = item.value.elts if isinstance(item.value, (ast.List, ast.Tuple)) else ()
                    fields.update(
                        value.value for value in values
                        if isinstance(value, ast.Constant) and isinstance(value.value, str)
                    )
                elif (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                ):
                    fields.add(target.attr)
    return fields


def _request_clones(source):
    offenders = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef):
            fields = _class_fields(node) & _REQUEST_FIELDS
            if len(fields) >= 2:
                offenders.append(("class", node.name, tuple(sorted(fields))))
        elif isinstance(node, ast.Dict):
            keys = {
                key.value for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            } & _REQUEST_FIELDS
            if len(keys) >= 2:
                offenders.append(("dict", node.lineno, tuple(sorted(keys))))
    return offenders


class TestAdminPeerRetirementStructure(unittest.TestCase):
    def test_renamed_class_and_dict_request_clones_are_detected(self):
        sample = """
class InnocentNewName:
    __slots__ = ("headers", "client_address")

def another_name():
    return {"path": "/admin", "headers": {}}
"""
        offenders = _request_clones(sample)
        self.assertEqual([item[0] for item in offenders], ["class", "dict"])

    def test_admin_auth_sources_define_no_request_clone(self):
        offenders = {}
        for relative in _AUTH_SOURCES:
            found = _request_clones((ROOT / relative).read_text(encoding="utf-8"))
            if found:
                offenders[relative] = found
        self.assertEqual(offenders, {})
        self.assertFalse(hasattr(admin_auth, "_Peer"))
        self.assertFalse(hasattr(admin_auth, "_peer_context"))


class TestNativeAdminAuthRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = FastAPI()
        app.include_router(admin_auth_router.router)
        app.include_router(passkeys_router.router)
        cls.client = TestClient(
            app,
            base_url="http://localhost:7700",
            client=("127.0.0.1", 43123),
        )

    def setUp(self):
        admin_auth._ATTEMPTS.clear()

    def test_config_uses_headers_without_a_peer_object(self):
        with (
            patch.object(admin_auth.turnstile, "sitekey_from", return_value="fixture-site") as sitekey,
            patch.object(admin_auth, "owner_tiene_totp", return_value=True),
            patch.object(admin_auth, "_admin_user", return_value="fixture-admin"),
        ):
            response = self.client.get("/api/admin/config")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "turnstile_sitekey": "fixture-site",
            "totp": True,
            "usuario": "fixture-admin",
        })
        headers = sitekey.call_args.args[0]
        self.assertEqual(headers.get("host"), "localhost:7700")

    def test_login_uses_explicit_headers_and_socket_host(self):
        identity = {"usuario": "fixture-admin", "rol": "admin", "totp_secret": None}
        with (
            patch.object(admin_auth, "_turnstile_ok_from", return_value=True) as turnstile_ok,
            patch.object(admin_auth.turnstile, "client_ip_from", return_value="127.0.0.1"),
            # login_result autentica por _authenticate_ex (identity,
            # base_caída) desde 97c52240: el seam es ése, con la base arriba.
            patch.object(admin_auth, "_authenticate_ex", return_value=(identity, False)),
            patch.object(admin_auth, "marcar_acceso"),
            patch.object(admin_auth, "_mint_token", return_value="fixture-token"),
        ):
            response = self.client.post(
                "/api/admin/login",
                json={"usuario": "fixture-admin", "password": "fixture-pass"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["usuario"], "fixture-admin")
        self.assertIn("st_admin=fixture-token", response.headers["set-cookie"])
        headers, client_host, token = turnstile_ok.call_args.args
        self.assertEqual(headers.get("host"), "localhost:7700")
        self.assertEqual(client_host, "127.0.0.1")
        self.assertEqual(token, "")

    def test_whoami_and_logout_build_cookies_from_headers(self):
        gate = admin_auth.GateResult(identity={"usuario": "fixture-admin", "rol": "admin"})
        with (
            patch.object(admin_auth, "evaluate_admin_gate", return_value=gate),
            patch.object(admin_auth, "_debe_renovar", return_value=True),
            patch.object(admin_auth, "_mint_token", return_value="renewed-token"),
            patch.object(admin_auth, "account_security", return_value={
                "tiene_2fa": True, "2fa_deadline": None, "2fa_vencido": False,
            }),
        ):
            whoami = self.client.get(
                "/api/admin/whoami", headers={"Cookie": "st_admin=old-token"},
            )
        self.assertEqual(whoami.status_code, 200)
        self.assertEqual(whoami.json()["rol"], "admin")
        self.assertIn("st_admin=renewed-token", whoami.headers["set-cookie"])

        logout = self.client.post("/api/admin/logout")
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(logout.json(), {"ok": True})
        self.assertIn("Max-Age=0", logout.headers["set-cookie"])

    def test_passkey_login_resolves_ip_from_explicit_values(self):
        with patch.object(
            admin_auth.turnstile, "client_ip_from", return_value="127.0.0.1",
        ) as client_ip:
            response = self.client.post("/api/admin/passkeys/login", json={})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "desafio_invalido"})
        headers, client_host = client_ip.call_args.args
        self.assertEqual(headers.get("host"), "localhost:7700")
        self.assertEqual(client_host, "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
