"""Private-gateway boundary tests using synthetic peers and existing accounts."""

import json
import os
import socket
import sys
import threading
import types
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from app.cuentas import accounts, accounts_router, portal_identity
import _env  # noqa: E402
import _wait  # noqa: E402

_USER = {"dni": "12345678", "nombre": "Fixture", "email": "saved@example.invalid"}
_PEER = "fd12::10"
_HEADERS = {"X-User-Id": "portal-fixture", "X-User-Cuit": "20123456788",
            "X-User-Email": "portal@example.invalid", "X-User-Roles": "admin"}


class TestPortalIdentity(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, {
            "TURNOS_PORTAL_IDENTITY_MODE": "private-gateway-v1",
            "TURNOS_PORTAL_IDENTITY_TRUSTED_PEERS": json.dumps([_PEER]),
            "TURNOS_PORTAL_IDENTITY_GATEWAY_HOST": "",
            "TURNOS_PUBLIC_ORIGIN": "https://portal.example.invalid",
            "TURNOS_PORTAL_IDENTITY_BINDINGS": json.dumps([
                {"subject": "portal-fixture", "cuit": "20123456788", "dni": "12345678"}]),
        })
        self.enterContext(patch.object(portal_identity.config, "dotenv_values", return_value={}))
        self.user = self.enterContext(patch.object(accounts.store, "get_user", return_value=dict(_USER)))
        self.session = self.enterContext(patch.object(accounts.store, "get_session"))
        self.create = self.enterContext(patch.object(accounts.store, "create_user"))
        self.update = self.enterContext(patch.object(accounts.store, "update_user"))
        self.change_dni = self.enterContext(patch.object(accounts.store, "change_dni"))
        self.app = FastAPI()
        self.app.include_router(accounts_router.router)
        self.client = self.enterContext(TestClient(portal_identity.SocketPeerApp(self.app), client=(_PEER, 50000)))

    def denied(self, headers=None, client=None):
        response = (client or self.client).get("/api/account/me", headers=_HEADERS if headers is None else headers)
        self.assertEqual(response.status_code, 401)
        self.user.assert_not_called()
        self.session.assert_not_called()

    def test_existing_bound_patient_without_cookie_or_provisioning(self):
        response = self.client.get("/api/account/me", headers=_HEADERS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"usuario": _USER})
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn("set-cookie", response.headers)
        self.user.assert_called_once_with("12345678")
        self.session.assert_not_called()
        self.create.assert_not_called()
        self.update.assert_not_called()

    def test_informational_headers_optional_and_never_grant_roles_or_edit_email(self):
        for headers in (_HEADERS, {k: v for k, v in _HEADERS.items() if k in {"X-User-Id", "X-User-Cuit"}}):
            with self.subTest(headers=list(headers)):
                self.assertEqual(self.client.get("/api/account/me", headers=headers).json(), {"usuario": _USER})
        self.update.assert_not_called()

    def test_staff_auth_remains_separate(self):
        from app.admin_panel import admin_account_router
        app = FastAPI()
        app.include_router(admin_account_router.router)
        with TestClient(portal_identity.SocketPeerApp(app), client=(_PEER, 1)) as client:
            self.assertEqual(client.get("/api/admin/account", headers=_HEADERS).status_code, 401)
        self.user.assert_not_called()

    def test_sibling_public_loopback_and_missing_transport_denied_despite_forwarded_headers(self):
        headers = dict(_HEADERS, **{"X-Forwarded-For": _PEER, "Forwarded": 'for="' + _PEER + '"', "Host": "gateway.railway.internal"})
        for peer in ("fd12::11", "10.0.0.2", "8.8.8.8", "127.0.0.1"):
            with self.subTest(peer=peer), TestClient(portal_identity.SocketPeerApp(self.app), client=(peer, 1)) as client:
                self.denied(headers, client)
        with TestClient(self.app, client=(_PEER, 1)) as client:
            self.denied(headers, client)

    def test_rewritten_client_cannot_replace_captured_socket_peer(self):
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
        rewritten = ProxyHeadersMiddleware(self.app, trusted_hosts="*")
        with TestClient(portal_identity.SocketPeerApp(rewritten), client=("fd12::11", 1)) as client:
            self.denied(dict(_HEADERS, **{"X-Forwarded-For": _PEER}), client)

    def test_native_cookie_cannot_bypass_portal_identity(self):
        self.session.return_value = {"usuario": _USER}
        self.denied({"Cookie": "st_ses=fixture"})

    def test_portal_binding_overrides_other_native_cookie(self):
        self.session.return_value = {"usuario": {"dni": "87654321"}}
        response = self.client.get("/api/account/me", headers=dict(_HEADERS, Cookie="st_ses=other"))
        self.assertEqual(response.json()["usuario"]["dni"], "12345678")
        self.session.assert_not_called()

    def test_duplicate_headers_denied(self):
        for name in _HEADERS:
            with self.subTest(name=name):
                self.denied(list(_HEADERS.items()) + [(name.lower(), _HEADERS[name])])

    def test_bad_subject_cuit_or_missing_identity_denied(self):
        for changed in ({"X-User-Id": "unknown"}, {"X-User-Cuit": "20123456789"},
                        {"X-User-Cuit": "20 12345678 8"}, {"X-User-Cuit": ""}, {"X-User-Id": ""}):
            with self.subTest(changed=changed):
                self.denied(dict(_HEADERS, **changed))
        for name in ("X-User-Id", "X-User-Cuit"):
            self.denied({k: v for k, v in _HEADERS.items() if k != name})

    def test_without_the_patient_portal_login_a_reviewed_binding_keeps_its_old_rule(self):
        # The person-CUIT check belongs to the provisioning login only: a
        # reviewed binding is accepted exactly as before, whatever its prefix.
        bindings = json.dumps([{"subject": "portal-fixture", "cuit": "30123456788", "dni": "12345678"}])
        with _env.patched_env(TURNOS_PORTAL_IDENTITY_BINDINGS=bindings, TURNOS_PATIENT_PORTAL_LOGIN=""):
            self.assertEqual(self.client.get("/api/account/me", headers=dict(_HEADERS, **{"X-User-Cuit": "30123456788"})).status_code, 200)
        self.create.assert_not_called()

    def test_hyphenated_cuit_normalized_to_reviewed_binding(self):
        self.assertEqual(self.client.get("/api/account/me", headers=dict(_HEADERS, **{"X-User-Cuit": "20-12345678-8"})).status_code, 200)

    def test_inactive_or_missing_account_denied_no_provisioning(self):
        for user in (None, dict(_USER, activo=False)):
            self.user.return_value = user
            self.assertEqual(self.client.get("/api/account/me", headers=_HEADERS).status_code, 401)
        self.create.assert_not_called()

    def test_storage_outage_is_503(self):
        self.user.side_effect = accounts.store.DbUnavailable("private detail")
        response = self.client.get("/api/account/me", headers=_HEADERS)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private detail", response.text)

    def test_patient_write_requires_exact_browser_origin_and_bound_identity(self):
        @self.app.post("/fixture-write")
        async def write(request: Request):
            user = accounts.current_user(request)
            return {"dni": user["dni"] if user else None}
        headers = dict(_HEADERS, Origin="https://portal.example.invalid")
        self.assertEqual(self.client.post("/fixture-write", headers=headers, json={"dni": "87654321"}).json(), {"dni": "12345678"})
        for origin in (None, "null", "https://other.invalid", "https://portal.example.invalid/"):
            h = dict(_HEADERS)
            if origin is not None:
                h["Origin"] = origin
            self.assertEqual(self.client.post("/fixture-write", headers=h).json(), {"dni": None})
        self.assertEqual(self.client.post("/fixture-write", headers=list(headers.items()) + [("origin", headers["Origin"])]).json(), {"dni": None})

    def test_profile_dni_change_is_blocked_before_store_write(self):
        response = self.client.post("/api/account/update", headers=dict(_HEADERS, Origin="https://portal.example.invalid"), json={"dni": "87654321"})
        self.assertEqual(response.status_code, 409)
        self.change_dni.assert_not_called()
        self.update.assert_not_called()

    def test_gateway_service_dns_resolves_exact_private_peers(self):
        answers = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (_PEER, 0, 0, 0))]
        with _env.patched_env(TURNOS_PORTAL_IDENTITY_TRUSTED_PEERS="[]", TURNOS_PORTAL_IDENTITY_GATEWAY_HOST="gateway.railway.internal"), patch.object(portal_identity.socket, "getaddrinfo", return_value=answers) as resolve:
            self.assertEqual(self.client.get("/api/account/me", headers=_HEADERS).status_code, 200)
            resolve.assert_called_once_with("gateway.railway.internal", None, type=socket.SOCK_STREAM)

    def test_gateway_dns_failure_empty_or_public_answer_fails_closed(self):
        with _env.patched_env(TURNOS_PORTAL_IDENTITY_TRUSTED_PEERS="[]", TURNOS_PORTAL_IDENTITY_GATEWAY_HOST="gateway.railway.internal"):
            for answers in ([], [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))]):
                with patch.object(portal_identity.socket, "getaddrinfo", return_value=answers):
                    self.assertEqual(self.client.get("/api/account/me", headers=_HEADERS).status_code, 503)
            with patch.object(portal_identity.socket, "getaddrinfo", side_effect=OSError("private detail")):
                self.assertEqual(self.client.get("/api/account/me", headers=_HEADERS).status_code, 503)
        self.user.assert_not_called()

    def test_bad_configuration_fails_closed_without_cookie_fallback(self):
        configs = [{"TURNOS_PORTAL_IDENTITY_MODE": value} for value in ("", "typo", "signed-ed25519-v1")]
        configs += [{"TURNOS_PORTAL_IDENTITY_TRUSTED_PEERS": json.dumps([value])} for value in ("*", "10.0.0.0/8", "127.0.0.1", "8.8.8.8", "::", "fe80::1", "fd12::1%eth0")]
        configs += [{"TURNOS_PORTAL_IDENTITY_TRUSTED_PEERS": value} for value in ("[]", "{}", "bad-json")]
        configs += [{"TURNOS_PORTAL_IDENTITY_GATEWAY_HOST": "gateway.railway.internal"}, {"TURNOS_PORTAL_IDENTITY_BINDINGS": "[]"}, {"TURNOS_PUBLIC_ORIGIN": "https://portal.example.invalid/path"}]
        for changes in configs:
            with self.subTest(changes=changes), _env.patched_env(changes):
                response = self.client.get("/api/account/me", headers=dict(_HEADERS, Cookie="st_ses=fixture"))
                self.assertEqual(response.status_code, 503)
        self.session.assert_not_called()
        self.user.assert_not_called()

    def test_invalid_or_duplicate_bindings_are_configuration_errors(self):
        rows = [{"subject": "portal-fixture", "cuit": "20123456788", "dni": "12345678"}]
        values = [json.dumps(rows + rows), json.dumps([dict(rows[0], dni="x")]),
                  json.dumps([dict(rows[0], cuit="20-12345678-8")]),
                  '[{"subject":"a","subject":"b","cuit":"20123456788","dni":"12345678"}]']
        for value in values:
            with self.subTest(value=value), _env.patched_env(TURNOS_PORTAL_IDENTITY_BINDINGS=value):
                self.assertEqual(self.client.get("/api/account/me", headers=_HEADERS).status_code, 503)
        self.user.assert_not_called()

    def test_ipv4_mapped_peer_is_normalized(self):
        with _env.patched_env(TURNOS_PORTAL_IDENTITY_TRUSTED_PEERS='["10.0.0.2"]'), TestClient(portal_identity.SocketPeerApp(self.app), client=("::ffff:10.0.0.2", 1)) as client:
            self.assertEqual(client.get("/api/account/me", headers=_HEADERS).status_code, 200)

    def test_dns_setting_rejects_wildcard_url_and_other_domains(self):
        for host in ("*.railway.internal", "https://gateway.railway.internal", "gateway.other.invalid", "railway.internal"):
            with self.subTest(host=host), _env.patched_env(TURNOS_PORTAL_IDENTITY_TRUSTED_PEERS="[]", TURNOS_PORTAL_IDENTITY_GATEWAY_HOST=host):
                self.assertEqual(self.client.get("/api/account/me", headers=_HEADERS).status_code, 503)
        self.user.assert_not_called()

    def test_file_only_mode_not_sufficient_to_enable_trust(self):
        with _env.patched_env(TURNOS_PORTAL_IDENTITY_MODE=None):
            with patch.object(portal_identity.config, "dotenv_values", return_value={"TURNOS_PORTAL_IDENTITY_MODE": "private-gateway-v1"}):
                self.assertEqual(self.client.get("/api/account/me", headers=_HEADERS).status_code, 503)

    def test_mode_off_keeps_native_cookie_auth(self):
        self.session.return_value = {"usuario": _USER}
        with _env.patched_env(TURNOS_PORTAL_IDENTITY_MODE="off"):
            self.assertEqual(self.client.get("/api/account/me", headers={"Cookie": "st_ses=fixture"}).status_code, 200)
        self.user.assert_not_called()
        self.session.assert_called_once_with("fixture")

    def test_trust_never_reads_mutable_db_configuration(self):
        real_get = portal_identity.config.get

        def guarded(key, *args, **kwargs):
            # the DNI-claims switch is unrelated to trust; every portal-identity key must stay out of the DB
            if str(key).startswith("TURNOS_PORTAL_IDENTITY") or str(key).startswith("TURNOS_STAFF_PORTAL"):
                raise AssertionError("DB trust forbidden")
            return real_get(key, *args, **kwargs)

        with patch.object(portal_identity.config, "get", side_effect=guarded):
            self.assertEqual(self.client.get("/api/account/me", headers=_HEADERS).status_code, 200)

    def test_runtime_disables_proxy_rewriting_even_with_wildcard_environment(self):
        from app import asgi_main
        fake_app = types.SimpleNamespace(app=object())
        fake_uvicorn = types.SimpleNamespace(run=lambda *a, **kw: None)
        from app.ops import demo_installation
        with patch.dict(sys.modules, {"app.main": fake_app, "uvicorn": fake_uvicorn}), patch.object(asgi_main, "_verify_required_packs"), patch.object(demo_installation, "apply_safety_defaults"), patch.object(fake_uvicorn, "run") as run, _env.patched_env(FORWARDED_ALLOW_IPS="*"):
            asgi_main._asgi_mode()
            self.assertIsInstance(run.call_args.args[0], portal_identity.SocketPeerApp)
            self.assertIs(run.call_args.args[0].app, fake_app.app)
            self.assertIs(run.call_args.kwargs["proxy_headers"], False)
            run.reset_mock()
            with _env.patched_env(TURNOS_PORTAL_IDENTITY_MODE="off"):
                asgi_main._asgi_mode()
            self.assertIs(run.call_args.args[0], fake_app.app)
            self.assertNotIn("proxy_headers", run.call_args.kwargs)
            run.reset_mock()
            with _env.patched_env(TURNOS_PORTAL_IDENTITY_TRUSTED_PEERS="[]"):
                with self.assertRaises(portal_identity.ConfigurationError):
                    asgi_main._asgi_mode()
            run.assert_not_called()

    def test_real_socket_cannot_spoof_gateway_via_forwarded_header(self):
        import http.client
        import uvicorn
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(portal_identity.SocketPeerApp(self.app), proxy_headers=False, forwarded_allow_ips="*", lifespan="off", log_level="critical"))
        worker = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        worker.start()
        try:
            _wait.until(lambda: server.started or not worker.is_alive(), "uvicorn started", timeout=5)
            self.assertTrue(server.started)
            connection = http.client.HTTPConnection("127.0.0.1", listener.getsockname()[1], timeout=5)
            try:
                connection.request("GET", "/api/account/me", headers=dict(_HEADERS, **{"X-Forwarded-For": _PEER}))
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, 401)
            finally:
                connection.close()
        finally:
            server.should_exit = True
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.user.assert_not_called()
