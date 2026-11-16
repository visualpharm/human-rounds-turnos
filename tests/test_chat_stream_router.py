"""Native ASGI contract for the on-site chat SSE transport.

All identities and payloads are synthetic. The tests use fake chat results,
deny the legacy fallback, and never call a provider or external service.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
import _wait  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

from app.core import app_modules, config  # noqa: E402

_ORIGINAL_ENV_PATH = config.ENV_PATH
config.ENV_PATH = ""

from app.modules.chat import chat_api, chat_api_router  # noqa: E402


def tearDownModule():
    config.ENV_PATH = _ORIGINAL_ENV_PATH
    config._reset_cache_para_tests()


def _events(raw):
    parsed = []
    for block in raw.decode("utf-8").strip().split("\n\n"):
        if not block:
            continue
        lines = block.splitlines()
        event = next(line[7:] for line in lines if line.startswith("event: "))
        data = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))
        parsed.append((event, data))
    return parsed


def _request(body):
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/chat/stream",
            "raw_path": b"/api/chat/stream",
            "query_string": b"",
            "headers": [(b"content-length", str(len(body)).encode("ascii"))],
            "client": ("127.0.0.1", 4242),
            "server": ("testserver", 80),
        },
        receive,
    )


def _keepalive_gate():
    """(patch, wait): a fake slow turn calls `wait()` to hold until the stream has sent its first
    keepalive (`estado`), instead of sleeping a guessed number of seconds."""
    sent = threading.Event()
    real_event = chat_api_router._event

    def spy(event, payload):
        if event == "estado":
            sent.set()
        return real_event(event, payload)

    return (patch.object(chat_api_router, "_event", spy),
            lambda: _wait.until(sent.is_set, "the first keepalive is sent"))


async def _collect(iterator):
    chunks = []
    async for chunk in iterator:
        chunks.append(chunk)
    return b"".join(chunks)


class TestNativeChatStreamRouter(unittest.TestCase):
    def setUp(self):
        self.addCleanup(config._reset_cache_para_tests)  # registered first: runs once the env is back
        _env.set_env(self, ENABLED_MODULES="turnos,chat")
        config._reset_cache_para_tests()
        chat_api._HITS.clear()
        self._delay = patch.object(chat_api, "_STREAM_DELAY", 0)
        self._delay.start()
        self._network = patch.object(
            socket.socket,
            "connect",
            side_effect=AssertionError("external network access is forbidden"),
        )
        self._network.start()

        app = FastAPI()
        app.include_router(chat_api_router.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self._network.stop()
        self._delay.stop()
        chat_api._HITS.clear()

    def test_fast_success_is_real_sse_and_never_buffered_by_a_shim(self):
        body = json.dumps({"mensaje": "hola"}).encode("utf-8")
        # "Fast" means the turn finishes before the first keepalive poll (_ESTADO_POLL, 4 s). A loaded
        # runner can take longer than that for the turn itself, and the answer then opens with "estado"
        # events (the slow path, pinned by the tests below). The poll is made longer than any runner so
        # this test pins the fast path's shape, not the machine's speed.
        with patch.object(chat_api, "_ESTADO_POLL", 600.0):
            native = self.client.post("/api/chat/stream", content=body)

        self.assertEqual(native.status_code, 200)
        self.assertIn("text/event-stream", native.headers.get("content-type", ""))
        self.assertEqual(native.headers.get("cache-control"), "no-cache")
        self.assertEqual(native.headers.get("x-accel-buffering"), "no")
        self.assertIsNone(native.headers.get("content-length"))
        eventos = _events(native.content)
        self.assertGreaterEqual(len(eventos), 2)
        self.assertTrue(all(e[0] == "chunk" for e in eventos[:-1]))
        self.assertEqual(eventos[-1][0], "done")
        reconstruido = "".join(d["texto"] for _, d in eventos[:-1])
        self.assertEqual(reconstruido, eventos[-1][1]["respuesta"])

        source = Path(chat_api_router.__file__).read_text(encoding="utf-8")
        self.assertNotIn("LegacyHandlerShim", source)
        self.assertIn("StreamingResponse", source)
        # The whole-file grep above used to be enough when this module only
        # held the streaming transport; native-migration campaign family
        # W2a added real (non-streaming) endpoints to the SAME file
        # (POST /api/chat, GET /api/chat/whatsapp|history, GET
        # /api/chat-transcripts) that legitimately read the full body via
        # `request.body()`. The real invariant this test protects is narrower:
        # the SSE path specifically must never buffer the whole body before
        # it can start streaming, so scope the check to that function.
        stream_source = (
            inspect.getsource(chat_api_router.stream_chat)
            + inspect.getsource(chat_api_router._bounded_body)
        )
        self.assertNotIn("request.body()", stream_source)

    def test_fast_json_errors_keep_exact_status_body_and_no_stream_headers(self):
        for body, expected_error in ((b"{}", "falta el mensaje"),
                                     (b"{", "JSON inválido: ")):
            with self.subTest(body=body):
                native = self.client.post("/api/chat/stream/", content=body)
                self.assertEqual(native.status_code, 400)
                self.assertTrue(str(native.json().get("error", "")).startswith(
                    expected_error))
                self.assertEqual(native.content, json.dumps(
                    native.json(), ensure_ascii=False).encode("utf-8"))
                self.assertEqual(native.headers.get("cache-control"), "no-store")
                self.assertNotIn("text/event-stream", native.headers.get("content-type", ""))

    def test_slow_turn_emits_keepalive_then_ordered_chunks_and_done(self):
        output = {"respuesta": "abcdefghijklmnopqrstuvwxyz", "autenticado": False}

        spy, first_keepalive_sent = _keepalive_gate()

        def slow(_exchange):
            first_keepalive_sent()
            return None, output

        with (
            patch.object(chat_api, "_ESTADO_POLL", 0.01),
            patch.object(chat_api, "compute_output", side_effect=slow),
            spy,
        ):
            response = self.client.post(
                "/api/chat/stream", json={"mensaje": "fixture"}
            )
        events = _events(response.content)
        kinds = [kind for kind, _data in events]
        first_chunk = kinds.index("chunk")
        self.assertGreater(first_chunk, 0)
        self.assertTrue(all(kind == "estado" for kind in kinds[:first_chunk]))
        self.assertNotIn("estado", kinds[first_chunk:])
        self.assertEqual(kinds[-1], "done")
        self.assertEqual(
            "".join(data["texto"] for kind, data in events if kind == "chunk"),
            output["respuesta"],
        )
        self.assertEqual(events[-1][1], output)

    def test_first_keepalive_arrives_after_one_poll_not_after_the_turn(self):
        async def scenario():
            release = threading.Event()

            def blocked(_exchange):
                release.wait(1)
                return None, {"respuesta": "done", "autenticado": False}

            with (
                patch.object(chat_api, "_ESTADO_POLL", 0.01),
                patch.object(chat_api, "compute_output", side_effect=blocked),
            ):
                started = time.monotonic()
                response = await chat_api_router.stream_chat(
                    _request(b'{"mensaje":"fixture"}')
                )
                first = await anext(response.body_iterator)
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 0.08)
                self.assertEqual(_events(first)[0][0], "estado")
                self.assertTrue(chat_api_router._ACTIVE_STREAM_TASKS)
                release.set()
                remainder = await _collect(response.body_iterator)
                self.assertEqual(_events(remainder)[-1][0], "done")
                self.assertFalse(chat_api_router._ACTIVE_STREAM_TASKS)

        asyncio.run(scenario())

    def test_undeclared_oversized_stream_stops_incrementally(self):
        class StreamedRequest:
            headers = {}

            async def stream(self):
                yield b"x" * chat_api.MAX_CHAT_STREAM_BODY_BYTES
                yield b"x"
                self.consumed_tail = True
                yield b"must not be consumed"

        request = StreamedRequest()
        request.consumed_tail = False
        self.assertIsNone(asyncio.run(chat_api_router._bounded_body(request)))
        self.assertFalse(request.consumed_tail)

    def test_declared_oversize_returns_413_without_starting_a_turn(self):
        with patch.object(chat_api, "compute_output") as compute:
            response = self.client.post(
                "/api/chat/stream",
                content=b"{}",
                headers={
                    "Content-Length": str(chat_api.MAX_CHAT_STREAM_BODY_BYTES + 1)
                },
            )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json(), {"error": "body demasiado grande"})
        compute.assert_not_called()

    def test_disconnect_before_first_byte_leaves_no_untracked_task(self):
        async def scenario():
            release = threading.Event()

            def blocked(_exchange):
                release.wait(1)
                return None, {"respuesta": "done", "autenticado": False}

            with (
                patch.object(chat_api, "_ESTADO_POLL", 0.5),
                patch.object(chat_api, "compute_output", side_effect=blocked),
            ):
                endpoint = asyncio.create_task(
                    chat_api_router.stream_chat(_request(b'{"mensaje":"fixture"}'))
                )
                await asyncio.sleep(0.02)
                endpoint.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await endpoint
                self.assertEqual(len(chat_api_router._ACTIVE_STREAM_TASKS), 1)
                release.set()
                for _ in range(100):
                    if not chat_api_router._ACTIVE_STREAM_TASKS:
                        break
                    await asyncio.sleep(0.01)
                self.assertFalse(chat_api_router._ACTIVE_STREAM_TASKS)

        asyncio.run(scenario())

    def test_disconnect_midstream_stops_emission_and_task_is_reaped(self):
        async def scenario():
            release = threading.Event()

            def blocked(_exchange):
                release.wait(1)
                return None, {"respuesta": "done", "autenticado": False}

            with (
                patch.object(chat_api, "_ESTADO_POLL", 0.01),
                patch.object(chat_api, "compute_output", side_effect=blocked),
            ):
                response = await chat_api_router.stream_chat(
                    _request(b'{"mensaje":"fixture"}')
                )
                first = await anext(response.body_iterator)
                self.assertEqual(_events(first)[0][0], "estado")
                await response.body_iterator.aclose()
                release.set()
                for _ in range(100):
                    if not chat_api_router._ACTIVE_STREAM_TASKS:
                        break
                    await asyncio.sleep(0.01)
                self.assertFalse(chat_api_router._ACTIVE_STREAM_TASKS)

        asyncio.run(scenario())

    def test_late_generator_error_is_one_terminal_error_event(self):
        spy, first_keepalive_sent = _keepalive_gate()

        def explode(_exchange):
            first_keepalive_sent()
            raise RuntimeError("synthetic failure")

        async def scenario():
            with (
                patch.object(chat_api, "_ESTADO_POLL", 0.01),
                patch.object(chat_api, "compute_output", side_effect=explode),
                spy,
            ):
                response = await chat_api_router.stream_chat(
                    _request(b'{"mensaje":"fixture"}')
                )
                raw = await _collect(response.body_iterator)
            events = _events(raw)
            self.assertTrue(all(kind == "estado" for kind, _ in events[:-1]))
            self.assertEqual(events[-1], (
                "error",
                {"error": "excepcion", "mensaje": "Ocurrió un error. Probá de nuevo."},
            ))
            self.assertFalse(chat_api_router._ACTIVE_STREAM_TASKS)

        asyncio.run(scenario())

    def test_disabled_module_is_indistinguishable_404(self):
        with patch.object(app_modules, "is_enabled", return_value=False):
            response = self.client.post("/api/chat/stream", json={"mensaje": "hola"})
        self.assertEqual(response.status_code, 404)

    def test_real_composition_claims_both_slash_shapes_without_fallback(self):
        from app import main as app_module

        self.assertFalse(os.path.exists(os.path.join(ROOT, "app", "services", "legacy_proxy.py")))

        client = TestClient(app_module.app)
        self.addCleanup(client.close)
        for path in ("/api/chat/stream", "/api/chat/stream/"):
            with self.subTest(path=path):
                response = client.post(path, json={"mensaje": "hola"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(_events(response.content)[-1][0], "done")


if __name__ == "__main__":
    unittest.main()
