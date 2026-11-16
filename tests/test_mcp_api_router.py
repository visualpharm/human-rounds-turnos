"""Native ASGI contract for the stateless MCP transport.

Every identity, token, payload, and destination in this file is synthetic.
Network access is denied and the native responses are pinned directly for
the protocol branches that do not require a live DB. Born as byte-parity
proofs against the retired dark-handler transport (``mcp_api.handle()``,
deleted 2026-08-30); the absolute expectations below are the shapes that
parity used to guarantee.
"""

from __future__ import annotations

import asyncio
import ast
import json
import os
import socket
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app.core import app_modules, config  # noqa: E402

_ORIGINAL_ENV_PATH = config.ENV_PATH
config.ENV_PATH = ""

from app.auth import mcp_guard  # noqa: E402
from app.modules.chat import mcp_api, mcp_api_router  # noqa: E402


def tearDownModule():
    config.ENV_PATH = _ORIGINAL_ENV_PATH
    config._reset_cache_para_tests()


def _rpc(method, params=None, request_id=1):
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    return json.dumps(payload).encode("utf-8")


class TestNativeMcpRouter(unittest.TestCase):
    def setUp(self):
        # Cleanups run last-in first-out: the client closes, the environment is
        # put back, then the config cache and the hit counters are reset.
        self.addCleanup(mcp_api._HITS.clear)
        self.addCleanup(config._reset_cache_para_tests)
        _env.set_env(self, ENABLED_MODULES="turnos,chat", TURNOS_MCP_PER_HOUR=None)
        config._reset_cache_para_tests()
        mcp_api._HITS.clear()

        app = FastAPI()
        app.include_router(mcp_api_router.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def test_discovery_methods(self):
        native = self.client.post(
            "/mcp", content=_rpc("initialize", {"protocolVersion": "2025-06-18"}))
        self.assertEqual(native.status_code, 200)
        result = native.json()["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertFalse(result["capabilities"]["tools"]["listChanged"])
        self.assertIn("serverInfo", result)
        self.assertIn("instructions", result)
        self.assertEqual(native.headers.get("cache-control"), "no-store")

        native = self.client.post("/mcp", content=_rpc("ping"))
        self.assertEqual(native.status_code, 200)
        self.assertEqual(native.json()["result"], {})

        native = self.client.post("/mcp", content=_rpc("tools/list"))
        self.assertEqual(native.status_code, 200)
        # only the tools the guard admits are offered (search_doctors / my_lab_results are refused)
        self.assertEqual(native.json()["result"]["tools"],
                         [dict(t, securitySchemes=[{"type": "oauth2"}])
                          for t in mcp_api.TOOLS if t["name"] in mcp_guard.WEIGHTS])

    def test_get_delete_and_trailing_slash_are_405(self):
        expected = json.dumps(
            {"error": "método no soportado (servidor MCP sin estado: sólo POST)"},
            ensure_ascii=False).encode("utf-8")
        for method in ("GET", "DELETE"):
            for path in ("/mcp", "/mcp/"):
                with self.subTest(method=method, path=path):
                    native = self.client.request(method, path, follow_redirects=False)
                    self.assertEqual(native.status_code, 405)
                    self.assertEqual(native.content, expected)
                    self.assertEqual(
                        native.headers.get("content-type"),
                        "application/json; charset=utf-8")

    def test_get_never_reads_a_request_body(self):
        consumed = []

        async def receive():
            consumed.append(True)
            return {"type": "http.request", "body": b"ignored", "more_body": False}

        request = Request(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/mcp",
                "raw_path": b"/mcp",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 4242),
                "server": ("testserver", 80),
            },
            receive,
        )

        async def scenario():
            response = await mcp_api_router.mcp_endpoint(request)
            return response.status_code

        self.assertEqual(asyncio.run(scenario()), 405)
        self.assertFalse(consumed)

    def test_invalid_batch_and_notification_shapes(self):
        native = self.client.post("/mcp", content=b"{not-json")
        self.assertEqual(native.status_code, 400)
        self.assertEqual(native.json()["error"]["code"], -32700)
        self.assertEqual(native.json()["error"]["message"], "JSON inválido")

        native = self.client.post("/mcp", content=b"[]")
        self.assertEqual(native.status_code, 400)
        self.assertEqual(native.json()["error"]["code"], -32600)
        self.assertEqual(native.json()["error"]["message"], "batch no soportado")

        native = self.client.post(
            "/mcp/", content=_rpc("notifications/initialized", request_id=None))
        self.assertEqual(native.status_code, 202)
        self.assertEqual(native.content, b"")

    def test_tools_call_requires_a_bearer_token(self):
        body = _rpc("tools/call", {
            "name": "search_services",
            "arguments": {"query": "fixture"},
        })
        native = self.client.post("/mcp", content=body)
        self.assertEqual(native.status_code, 401)
        self.assertEqual(native.json()["error"]["code"], -32001)
        challenge = native.headers.get("www-authenticate")
        self.assertTrue(challenge.startswith('Bearer realm="sana-turnos", resource_metadata="'))
        self.assertTrue(challenge.endswith('/.well-known/oauth-protected-resource"'))

    def test_tools_call_database_failure_is_503(self):
        body = _rpc("tools/call", {
            "name": "search_services",
            "arguments": {"query": "fixture"},
        })

        def unavailable(_token):
            from app.core import db
            raise db.DbUnavailable("synthetic DB failure")

        headers = {"Authorization": "Bearer fixture-token"}
        with patch.object(mcp_api, "_usuario_por_token", unavailable):
            native = self.client.post("/mcp", content=body, headers=headers)
        self.assertEqual(native.status_code, 503)
        self.assertEqual(native.json()["error"]["code"], -32000)

    def test_bounded_body_declared_limit_is_413(self):
        raw = b"{" + b" " * mcp_api.MAX_MCP_BODY_BYTES + b"}"
        headers = {"Content-Length": str(len(raw))}
        native = self.client.post("/mcp", content=raw, headers=headers)
        self.assertEqual(native.status_code, 413)
        self.assertEqual(native.json(), {"error": "body demasiado grande"})

    def test_incremental_reader_rejects_an_undeclared_oversized_stream(self):
        class StreamedRequest:
            headers = {}

            async def stream(self):
                yield b"x" * mcp_api.MAX_MCP_BODY_BYTES
                yield b"x"
                self.fail_if_consumed = True
                yield b"must not be consumed"

        request = StreamedRequest()
        request.fail_if_consumed = False
        self.assertIsNone(asyncio.run(mcp_api_router._bounded_body(request)))
        self.assertFalse(request.fail_if_consumed)

    def test_renamed_fake_handler_protocol_cannot_return(self):
        forbidden = {
            "send_response", "send_header", "end_headers", "wfile", "write",
            "_read_body", "_read_json",
        }
        for module in (mcp_api, mcp_api_router):
            with self.subTest(module=module.__name__):
                source = Path(module.__file__).read_text(encoding="utf-8")
                tree = ast.parse(source)
                exposed = {
                    node.name
                    for node in ast.walk(tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
                exposed.update(
                    node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute))
                self.assertTrue(forbidden.isdisjoint(exposed), forbidden & exposed)
                self.assertNotIn("McpExchange", source)
                self.assertNotIn("McpRequestContext", source)
        router_source = Path(mcp_api_router.__file__).read_text(encoding="utf-8")
        self.assertIn("async for chunk in request.stream()", router_source)

    def test_request_facade_shape_cannot_hide_behind_a_new_name(self):
        request_fields = {"method", "path", "headers", "client_address", "command"}
        offenders = []
        for module in (mcp_api, mcp_api_router):
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                assigned = {
                    target.attr
                    for item in ast.walk(node)
                    if isinstance(item, (ast.Assign, ast.AnnAssign))
                    for target in (
                        item.targets if isinstance(item, ast.Assign) else [item.target]
                    )
                    if isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                }
                if len(assigned & request_fields) >= 2:
                    offenders.append((module.__name__, node.name, assigned & request_fields))
        self.assertEqual(offenders, [])

    def test_domain_accepts_explicit_transport_values(self):
        self.assertEqual(
            tuple(mcp_api.process_mcp_request.__code__.co_varnames[:3]),
            ("method", "headers", "raw"),
        )
        self.assertEqual(
            tuple(mcp_api.process_account_request.__code__.co_varnames[:3]),
            ("usuario", "base_url", "regenerate"),
        )

    def test_domain_returns_typed_result_without_an_http_recorder(self):
        result = mcp_api.process_mcp_request("POST", {}, _rpc("ping"))

        self.assertIsInstance(result, mcp_api.McpResult)
        self.assertEqual(result.status, 200)
        self.assertEqual(json.loads(result.body)["result"], {})

    def test_account_get_regenerate_and_slash(self):
        from app.cuentas import accounts
        from app.core import site_identity
        from app.core import db_accounts

        cases = (
            ("GET", "/api/account/mcp", False),
            ("GET", "/api/account/mcp/", False),
            ("POST", "/api/account/mcp/regenerate", True),
            ("POST", "/api/account/mcp/regenerate/", True),
        )
        with (
            patch.object(accounts, "current_user", return_value={"dni": "40000000"}),
            # An account that already has a token: the database holds its hash, so GET
            # shows only the last 4 characters; regenerating shows the new token once.
            patch.object(db_accounts, "mcp_token_issue", return_value={"token": None, "hint": "rent"}),
            patch.object(db_accounts, "mcp_token_regenerate", return_value="fixture-new"),
            patch.object(site_identity, "base_url", return_value="https://example.invalid"),
            patch.object(socket.socket, "connect", side_effect=AssertionError("network")),
        ):
            for method, path, regenerate in cases:
                with self.subTest(method=method, path=path):
                    native = self.client.request(method, path, follow_redirects=False)
                    self.assertEqual(native.status_code, 200)
                    shown = ({"token": "fixture-new", "hint": "-new", "last_used": None} if regenerate
                             else {"token": None, "hint": "rent", "last_used": None})
                    self.assertEqual(native.json(), {
                        "ok": True,
                        "url": "https://example.invalid/mcp",
                        **shown,
                    })

    def test_account_get_shows_a_fresh_token_once(self):
        from app.cuentas import accounts
        from app.core import site_identity
        from app.core import db_accounts

        with (
            patch.object(accounts, "current_user", return_value={"dni": "40000000"}),
            patch.object(db_accounts, "mcp_token_issue", return_value={"token": "fixture-made", "hint": "made"}),
            patch.object(site_identity, "base_url", return_value="https://example.invalid"),
        ):
            native = self.client.get("/api/account/mcp")
        self.assertEqual(native.status_code, 200)
        self.assertEqual(native.json(), {
            "ok": True, "url": "https://example.invalid/mcp", "token": "fixture-made", "hint": "made",
            "last_used": None})

    def test_account_without_session_is_401(self):
        from app.cuentas import accounts

        with patch.object(accounts, "current_user", return_value=None):
            native = self.client.get("/api/account/mcp")
        self.assertEqual(native.status_code, 401)
        self.assertEqual(native.json(), {"error": "no_autenticado"})

    def test_disabled_module_is_indistinguishable_404(self):
        with patch.object(app_modules, "is_enabled", return_value=False):
            for method, path in (
                ("POST", "/mcp"),
                ("GET", "/api/account/mcp"),
                ("POST", "/api/account/mcp/regenerate"),
            ):
                with self.subTest(method=method, path=path):
                    response = self.client.request(method, path, content=b"{}")
                    self.assertEqual(response.status_code, 404)

    def test_real_composition_claims_every_mcp_shape_without_fallback(self):
        from app import main as app_module

        self.assertFalse(os.path.exists(os.path.join(ROOT, "app", "services", "legacy_proxy.py")))

        client = TestClient(app_module.app)
        self.addCleanup(client.close)
        cases = (
            ("POST", "/mcp", _rpc("ping"), 200),
            ("POST", "/mcp/", _rpc("ping"), 200),
            ("GET", "/mcp", b"", 405),
            ("GET", "/mcp/", b"", 405),
            ("DELETE", "/mcp", b"", 405),
            ("DELETE", "/mcp/", b"", 405),
            ("GET", "/api/account/mcp", b"", 401),
            ("GET", "/api/account/mcp/", b"", 401),
            ("POST", "/api/account/mcp/regenerate", b"", 401),
            ("POST", "/api/account/mcp/regenerate/", b"", 401),
        )
        for method, path, body, status in cases:
            with self.subTest(method=method, path=path):
                response = client.request(
                    method,
                    path,
                    content=body,
                    follow_redirects=False,
                )
                self.assertEqual(response.status_code, status)


if __name__ == "__main__":
    unittest.main()
