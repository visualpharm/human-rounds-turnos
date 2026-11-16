"""OAuth 2.1 for the MCP server: what ChatGPT needs to connect (discovery, DCR, consent, PKCE token).

Everything is synthetic; the account session and the token store are patched."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import config  # noqa: E402
from app.modules.chat import mcp_api, mcp_oauth, mcp_oauth_router  # noqa: E402

REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"
VERIFIER = "v" * 50
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
PERSON = {"dni": "30111222", "nombre": "Ana"}


class TestMcpOauth(unittest.TestCase):
    def setUp(self):
        self.addCleanup(config._reset_cache_para_tests)
        _env.set_env(self, ENABLED_MODULES="turnos,chat", TURNOS_ADMIN_SECRET="oauth-test-key",
                     TURNOS_PUBLIC_ORIGIN="https://demo.example.org")
        config._reset_cache_para_tests()
        self.claimed = set()  # the single-use claims; the real table is pinned in test_mcp_oauth_once.py
        p = patch("app.core.db_analytics.claim_daily_alert",
                  side_effect=lambda key, day: not ({key} & self.claimed) and not self.claimed.add(key))
        p.start()
        self.addCleanup(p.stop)
        app = FastAPI()
        app.include_router(mcp_oauth_router.router)
        self.client = TestClient(app, follow_redirects=False)
        self.addCleanup(self.client.close)
        self.issued = []

        def issue(dni):
            self.issued.append(dni)
            return "tok-" + dni
        p = patch("app.core.db_accounts.mcp_token_regenerate", side_effect=issue)
        p.start()
        self.addCleanup(p.stop)

    def _session(self, person):
        p = patch("app.cuentas.accounts.current_user", return_value=person)
        p.start()
        self.addCleanup(p.stop)

    def _client_id(self):
        r = self.client.post("/oauth/register", json={"redirect_uris": [REDIRECT], "client_name": "ChatGPT"})
        self.assertEqual(r.status_code, 201)
        return r.json()["client_id"]

    def _query(self, client_id, **over):
        q = {"client_id": client_id, "redirect_uri": REDIRECT, "response_type": "code", "state": "st1",
             "code_challenge": CHALLENGE, "code_challenge_method": "S256",
             "resource": "https://demo.example.org/mcp"}
        q.update(over)
        return urlencode(q)

    def test_discovery_documents(self):
        pr = self.client.get("/.well-known/oauth-protected-resource").json()
        self.assertEqual(pr["resource"], "https://demo.example.org/mcp")
        self.assertEqual(pr["authorization_servers"], ["https://demo.example.org"])
        as_ = self.client.get("/.well-known/oauth-authorization-server").json()
        self.assertEqual(as_["authorization_endpoint"], "https://demo.example.org/oauth/authorize")
        self.assertEqual(as_["token_endpoint"], "https://demo.example.org/oauth/token")
        self.assertEqual(as_["registration_endpoint"], "https://demo.example.org/oauth/register")
        self.assertIn("S256", as_["code_challenge_methods_supported"])

    def test_401_points_at_the_resource_metadata(self):
        h = mcp_api.process_rpc_body({}, json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                      "params": {"name": "whoami"}}).encode())
        self.assertEqual(h.status, 401)
        header = dict(h.headers)["WWW-Authenticate"]
        self.assertIn('resource_metadata="https://demo.example.org/.well-known/oauth-protected-resource"', header)

    def test_tools_declare_oauth(self):
        h = mcp_api.process_rpc_body({}, json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode())
        tools = json.loads(h.body)["result"]["tools"]
        self.assertTrue(tools)
        self.assertTrue(all(t["securitySchemes"] == [{"type": "oauth2"}] for t in tools))

    def test_registration_refuses_unknown_hosts(self):
        for uris in (["https://evil.example.com/cb"], ["http://chatgpt.com/cb"], [], "x"):
            r = self.client.post("/oauth/register", json={"redirect_uris": uris})
            self.assertEqual(r.status_code, 400, uris)

    def test_registration_accepts_loopback_for_native_clients(self):
        # RFC 8252: a desktop client (Codex CLI) listens on loopback, any port and path.
        for uri in ("http://127.0.0.1:41234/callback", "http://localhost:8080/cb", "http://[::1]:5000/x/y",
                    "http://127.0.0.1/cb", "http://localhost/"):
            r = self.client.post("/oauth/register", json={"redirect_uris": [uri]})
            self.assertEqual(r.status_code, 201, uri)

    def test_registration_refuses_loopback_lookalikes_and_other_schemes(self):
        for uri in ("http://127.0.0.1.evil.example.com/cb", "http://localhost.evil.example.com:80/cb",
                    "http://user@127.0.0.1:80/cb", "http://127.0.0.2:80/cb", "http://0.0.0.0:80/cb",
                    "ftp://127.0.0.1/cb", "myapp://127.0.0.1/cb", "http://127.0.0.1:80/cb#f",
                    "http://127.0.0.1:80\\@evil.example.com/cb", "http://127.0.0.1:99999/cb",
                    "http://localhost:80/ cb", "http://[::1]:80@evil.example.com/cb"):
            r = self.client.post("/oauth/register", json={"redirect_uris": [uri]})
            self.assertEqual(r.status_code, 400, uri)

    def test_authorize_without_session_goes_to_login_and_comes_back(self):
        self._session(None)
        cid = self._client_id()
        r = self.client.get("/oauth/authorize?" + self._query(cid))
        self.assertEqual(r.status_code, 302)
        loc = urlsplit(r.headers["location"])
        self.assertEqual(loc.path, "/account")
        nxt = parse_qs(loc.query)["next"][0]
        self.assertTrue(nxt.startswith("/oauth/authorize?"))
        self.assertIn("code_challenge=", nxt)

    def test_authorize_rejects_unregistered_redirect(self):
        self._session(PERSON)
        cid = self._client_id()
        r = self.client.get("/oauth/authorize?" + self._query(cid, redirect_uri="https://chatgpt.com/other"))
        self.assertEqual(r.status_code, 400)
        r = self.client.get("/oauth/authorize?" + self._query(cid, code_challenge_method="plain"))
        self.assertEqual(r.status_code, 400)

    def _consent(self, cid):
        r = self.client.get("/oauth/authorize?" + self._query(cid))
        self.assertEqual(r.status_code, 200)
        self.assertIn("Permitir", r.text)
        return r.text.split('name="consent" value="')[1].split('"')[0]

    def _approve(self):
        self._session(PERSON)
        cid = self._client_id()
        consent = self._consent(cid)
        r = self.client.post("/oauth/authorize", content=urlencode({"consent": consent, "decision": "allow"}))
        self.assertEqual(r.status_code, 302)
        loc = urlsplit(r.headers["location"])
        self.assertEqual(f"{loc.scheme}://{loc.netloc}{loc.path}", REDIRECT)
        q = parse_qs(loc.query)
        self.assertEqual(q["state"], ["st1"])
        return cid, q["code"][0]

    def _token(self, cid, code, verifier=VERIFIER):
        return self.client.post("/oauth/token", content=urlencode({
            "grant_type": "authorization_code", "code": code, "client_id": cid,
            "redirect_uri": REDIRECT, "code_verifier": verifier}))

    def test_full_flow_issues_the_mcp_token_once(self):
        cid, code = self._approve()
        r = self._token(cid, code)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["access_token"], "tok-30111222")
        self.assertEqual(r.json()["token_type"], "Bearer")
        self.assertEqual(self._token(cid, code).status_code, 400)  # a code works once
        self.assertEqual(self.issued, ["30111222"])

    def test_wrong_pkce_verifier_issues_nothing(self):
        cid, code = self._approve()
        r = self._token(cid, code, verifier="w" * 50)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["error"], "invalid_grant")
        self.assertEqual(self.issued, [])

    def test_deny_redirects_with_access_denied(self):
        self._session(PERSON)
        cid = self._client_id()
        consent = self._consent(cid)
        r = self.client.post("/oauth/authorize", content=urlencode({"consent": consent, "decision": "deny"}))
        q = parse_qs(urlsplit(r.headers["location"]).query)
        self.assertEqual(q["error"], ["access_denied"])
        self.assertNotIn("code", q)

    def test_consent_of_another_patient_is_refused(self):
        self._session(PERSON)
        consent = self._consent(self._client_id())
        p = patch("app.cuentas.accounts.current_user", return_value={"dni": "20999888", "nombre": "Otro"})
        p.start()
        self.addCleanup(p.stop)
        r = self.client.post("/oauth/authorize", content=urlencode({"consent": consent, "decision": "allow"}))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.issued, [])

    def test_registration_refuses_hosts_that_parse_differently_in_a_browser(self):
        for uri in ("https://evil.example.com\\.chatgpt.com/cb", "https://chatgpt.com\\@evil.example.com/cb",
                    "https://chatgpt.com:443@evil.example.com/cb", "https://chatgpt.com/ cb",
                    "https://chatgpt.com", "https://chatgpt.com/cb\n"):
            r = self.client.post("/oauth/register", json={"redirect_uris": [uri]})
            self.assertEqual(r.status_code, 400, uri)

    def test_a_consent_decides_once_even_after_cancel(self):
        self._session(PERSON)
        consent = self._consent(self._client_id())
        first = self.client.post("/oauth/authorize", content=urlencode({"consent": consent, "decision": "deny"}))
        self.assertEqual(first.status_code, 302)
        again = self.client.post("/oauth/authorize", content=urlencode({"consent": consent, "decision": "allow"}))
        self.assertEqual(again.status_code, 400)

    def test_a_resource_other_than_this_server_is_refused(self):
        self._session(PERSON)
        cid = self._client_id()
        r = self.client.get("/oauth/authorize?" + self._query(cid, resource="https://evil.example.com/other"))
        self.assertEqual(r.status_code, 400)

    def test_a_malformed_resource_is_a_page_not_a_500(self):
        self._session(PERSON)
        cid = self._client_id()
        for resource in ("http://[x", "https://[::1/mcp", "http://[bad]:99999999/mcp"):
            r = self.client.get("/oauth/authorize?" + self._query(cid, resource=resource))
            self.assertEqual(r.status_code, 400, resource)
            self.assertIn("No se pudo conectar", r.text)

    def test_a_non_ascii_challenge_is_refused_at_authorize(self):
        self._session(PERSON)
        cid = self._client_id()
        for challenge in ("é" * 50, CHALLENGE[:-1] + "ñ", CHALLENGE + "="):
            r = self.client.get("/oauth/authorize?" + self._query(cid, code_challenge=challenge))
            self.assertEqual(r.status_code, 400, challenge)

    def test_a_signed_code_with_a_non_ascii_challenge_is_a_400_not_a_500(self):
        cid = self._client_id()
        code = mcp_oauth.sign("code", {"dni": "30111222", "cid": cid, "ru": REDIRECT, "cc": "é" * 50, "res": ""}, 60)
        r = self._token(cid, code)
        self.assertEqual((r.status_code, r.json()["error"]), (400, "invalid_grant"))
        r = self._token(cid, code, verifier="é" * 50)
        self.assertEqual((r.status_code, r.json()["error"]), (400, "invalid_grant"))
        self.assertEqual(self.issued, [])

    def test_the_claim_is_a_hash_key_and_a_refusing_database_refuses_the_code(self):
        cid, code = self._approve()
        self.assertEqual(len(self.claimed), 1)  # the consent; the code is claimed when exchanged
        self.assertTrue(all(k.startswith("mcp-oauth:") and code not in k for k in self.claimed))
        with patch("app.core.db_analytics.claim_daily_alert", side_effect=RuntimeError("db down")):
            self.assertEqual(self._token(cid, code).status_code, 400)
        self.assertEqual(self.issued, [])

    def test_the_consent_screen_names_where_the_approval_goes(self):
        self._session(PERSON)
        r = self.client.get("/oauth/authorize?" + self._query(self._client_id()))
        self.assertIn("chatgpt.com", r.text.split("<body>")[1])

    def test_the_consent_screen_is_a_page_of_the_site(self):
        self._session(PERSON)
        r = self.client.get("/oauth/authorize?" + self._query(self._client_id()))
        html = r.text
        self.assertIn('<header id="stznav"', html)
        self.assertIn('rel="stylesheet" href="/static/human-rounds', html)
        self.assertIn('data-theme="', html)
        self.assertNotIn("{{", html)
        self.assertNotIn("system-ui", html)
        self.assertEqual(html.count('class="sc-cta"'), 1)
        self.assertIn("Permitir", html)

    def test_the_error_screens_are_pages_of_the_site_too(self):
        self._session(PERSON)
        r = self.client.get("/oauth/authorize?client_id=x")
        self.assertEqual(r.status_code, 400)
        self.assertIn('<header id="stznav"', r.text)
        self.assertIn("No se pudo conectar", r.text)

    def test_the_account_assistant_block_reads_dark_and_short(self):
        with open(os.path.join(ROOT, "static", "platform", "account.mjs"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('class="dcard-p acc-mcp-lede"', src)
        self.assertEqual(src.count('class="acc-sec-s acc-mcp-note"'), 3)
        self.assertIn("En ChatGPT pegá el servidor y elegí «OAuth».", src)
        self.assertNotIn("sin token.", src)

    def _portal_login(self, mode="private-gateway-v1"):
        p = patch("app.cuentas.portal_identity.patient_login_enabled", return_value=True)
        p.start()
        self.addCleanup(p.stop)
        q = patch("app.cuentas.portal_identity._env",
                  side_effect=lambda name, default="": mode if name == "TURNOS_PORTAL_IDENTITY_MODE" else default)
        q.start()
        self.addCleanup(q.stop)

    def test_the_test_portal_keeps_the_connection(self):
        self._portal_login(mode="test-portal-v1")
        self.assertEqual(self.client.get("/.well-known/oauth-protected-resource").status_code, 200)
        self.assertEqual(self.client.post("/oauth/register", json={"redirect_uris": [REDIRECT]}).status_code, 201)

    def test_portal_login_installation_closes_discovery_registration_and_token(self):
        self._portal_login()
        self.assertEqual(self.client.get("/.well-known/oauth-protected-resource").status_code, 404)
        self.assertEqual(self.client.get("/.well-known/oauth-authorization-server").status_code, 404)
        self.assertEqual(self.client.post("/oauth/register", json={"redirect_uris": [REDIRECT]}).status_code, 404)
        r = self._token("x", "y")
        self.assertEqual((r.status_code, r.json()["error"]), (400, "unauthorized_client"))
        page = self.client.get("/oauth/authorize?client_id=x")
        self.assertIn("No disponible en este sitio", page.text)

    def test_the_account_routes_issue_nothing_on_a_portal_login_installation(self):
        from app.modules.chat import mcp_api_router
        self._portal_login()
        app = FastAPI()
        app.include_router(mcp_api_router.router)
        self._session(PERSON)
        with TestClient(app) as client, patch("app.core.db_accounts.mcp_token_issue") as issue:
            self.assertEqual(client.get("/api/account/mcp").status_code, 403)
            self.assertEqual(client.post("/api/account/mcp/regenerate").status_code, 403)
            issue.assert_not_called()
        self.assertEqual(self.issued, [])

    def test_portal_login_installation_issues_no_token_through_oauth(self):
        self._session(PERSON)
        cid = self._client_id()
        consent = self._consent(cid)
        self._portal_login()
        self.assertEqual(self.client.get("/oauth/authorize?" + self._query(cid)).status_code, 400)
        r = self.client.post("/oauth/authorize", content=urlencode({"consent": consent, "decision": "allow"}))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.issued, [])

    def test_portal_login_installation_issues_no_token_from_the_account(self):
        self._portal_login()
        for regen in (False, True):
            with patch("app.core.db_accounts.mcp_token_issue", return_value={"token": "x"}) as issue, \
                    patch("app.core.db_accounts.mcp_token_regenerate", return_value="x") as regen_fn:
                r = mcp_api.process_account_request(PERSON, "https://demo.example.org", regen)
                self.assertEqual(r.status, 403)
                issue.assert_not_called()
                regen_fn.assert_not_called()

    def test_expired_or_forged_blobs_are_refused(self):
        forged = mcp_oauth.sign("code", {"dni": "1"}, -5)
        self.assertIsNone(mcp_oauth.verify("code", forged))
        self.assertIsNone(mcp_oauth.verify("code", mcp_oauth.sign("client", {"dni": "1"}, 60)))


if __name__ == "__main__":
    unittest.main()
