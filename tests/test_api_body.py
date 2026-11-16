"""test_api_body.py — `core.api_body`: the one reader of native JSON request
bodies and the shared `RequestValidationError` answer (TASK-24, stage 1).

The decoding must be the legacy `_read_json()` one byte for byte (empty →
`{}`, UTF-8 text → `json.loads`, so a BOM or UTF-16 still fails), a body
that does not decode must be answered by the route family's contract, and
every other validation error must keep FastAPI's stock 422.
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import ClientDisconnect  # noqa: E402

from app.core import api_body  # noqa: E402
from app.core.http_json import json_response  # noqa: E402


def _legacy_read_json(raw):
    """The copy every route carried before core.api_body."""
    return json.loads(raw.decode("utf-8")) if raw else {}


_FAMILY = api_body.BodyContract(
    message=lambda exc: f"JSON inválido: {exc}",
    respond=lambda _request, payload, status: json_response(payload, status))
_NARROW = api_body.BodyContract(
    message=lambda _exc: "JSON inválido",
    respond=lambda _request, payload, status: json_response(payload, status),
    catches=(ValueError, UnicodeError))


def _app():
    app = FastAPI()
    api_body.install(app)

    @app.post("/family")
    async def family(request: Request):
        return json_response({"got": await api_body.read_body(request, _FAMILY)})

    @app.post("/narrow")
    async def narrow(request: Request):
        return json_response({"got": await api_body.read_body(request, _NARROW)})

    @app.get("/typed/{n}")
    async def typed(n: int):
        return json_response({"n": n})

    return app


_BODIES = [
    b"", b"{}", b'{"a": 1}', b"[]", b"null", b'"x"', b"123", b"{bad", b"\xff\xfe",
    "﻿{}".encode("utf-8"), "{}".encode("utf-16"), b"  {\"a\": [1, 2]}  ",
    b'{"a": 1} trailing', b"{\"a\": NaN}",
]


class TestDecode(unittest.TestCase):

    def test_same_value_or_same_exception_as_the_legacy_copy(self):
        for raw in _BODIES:
            with self.subTest(raw=raw):
                try:
                    want = ("ok", _legacy_read_json(raw))
                except Exception as exc:  # noqa: BLE001
                    want = ("raise", type(exc), str(exc))
                try:
                    got = ("ok", api_body.decode(raw))
                except Exception as exc:  # noqa: BLE001
                    got = ("raise", type(exc), str(exc))
                if want[0] == "ok" and want[1] != want[1]:  # NaN
                    continue
                self.assertEqual(got, want)

    def test_bom_and_utf16_still_fail(self):
        for raw in ("﻿{}".encode("utf-8"), "{}".encode("utf-16")):
            with self.subTest(raw=raw):
                with self.assertRaises(ValueError):
                    api_body.decode(raw)


class TestSharedHandler(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(_app(), raise_server_exceptions=False)

    def tearDown(self):
        self.client.close()

    def test_a_body_that_decodes_reaches_the_route(self):
        r = self.client.post("/family", content=b'{"a": 1}')
        self.assertEqual((r.status_code, r.json()), (200, {"got": {"a": 1}}))
        r = self.client.post("/family", content=b"")
        self.assertEqual((r.status_code, r.json()), (200, {"got": {}}))
        r = self.client.post("/family", content=b"[1]", headers={"Content-Type": "text/plain"})
        self.assertEqual((r.status_code, r.json()), (200, {"got": [1]}))

    def test_a_body_that_does_not_decode_gets_the_family_answer(self):
        for raw in (b"{bad", b"\xff\xfe", "﻿{}".encode("utf-8")):
            with self.subTest(raw=raw):
                try:
                    _legacy_read_json(raw)
                except Exception as exc:  # noqa: BLE001
                    want = json_response({"error": f"JSON inválido: {exc}"}, 400)
                r = self.client.post("/family", content=raw)
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.content, want.body)
                self.assertEqual(r.headers["content-type"], "application/json; charset=utf-8")
                self.assertEqual(r.headers["cache-control"], "no-store")

    def test_what_the_family_did_not_catch_still_propagates(self):
        deep = b"[" * 200000 + b"]" * 200000
        r = self.client.post("/narrow", content=deep)
        self.assertEqual(r.status_code, 500)
        r = self.client.post("/family", content=deep)
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/narrow", content=b"{bad")
        self.assertEqual((r.status_code, r.json()), (400, {"error": "JSON inválido"}))

    def test_other_validation_errors_keep_the_stock_422(self):
        r = self.client.get("/typed/abc")
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.headers["content-type"], "application/json")
        self.assertEqual(r.json(), {"detail": [{
            "type": "int_parsing", "loc": ["path", "n"],
            "msg": "Input should be a valid integer, unable to parse string as an integer",
            "input": "abc"}]})

    def test_the_app_installs_the_shared_handler(self):
        from fastapi.exceptions import RequestValidationError
        from app.core import deps
        app = FastAPI()
        deps.install_gate_handlers(app)
        self.assertIs(app.exception_handlers[RequestValidationError],
                      api_body.render_validation_error)


async def _post_and_hang_up(app, path):
    """Drives the ASGI app by hand: the client sends the headers, promises a
    body, and disconnects before it arrives. Returns (sent messages, error)."""
    sent = []
    events = iter([{"type": "http.disconnect"}])

    async def receive():
        return next(events, {"type": "http.disconnect"})

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "POST", "scheme": "http", "path": path, "raw_path": path.encode(),
             "query_string": b"", "root_path": "", "server": ("test", 80),
             "client": ("test", 1),
             "headers": [(b"content-type", b"application/json"), (b"content-length", b"40")]}
    try:
        await app(scope, receive, send)
    except Exception as exc:  # noqa: BLE001 — the point of the test
        return sent, exc
    return sent, None


class TestClientHangsUp(unittest.TestCase):
    """A dropped connection mid-POST is not a server error (the municipal
    Sentry showed `ClientDisconnect` on `POST /api/account/update`)."""

    def test_the_app_installs_the_handler(self):
        from app.core import deps
        app = FastAPI()
        deps.install_gate_handlers(app)
        self.assertIs(app.exception_handlers[ClientDisconnect], api_body.render_client_gone)

    def test_a_hang_up_while_the_body_is_read_raises_nothing(self):
        import asyncio
        app = _app()

        @app.post("/lazy")
        async def lazy(request: Request):
            return json_response({"got": (await api_body.lazy_model(request, _FAMILY, _ThingIn))()})

        for path in ("/family", "/lazy"):
            with self.subTest(path=path):
                sent, error = asyncio.run(_post_and_hang_up(app, path))
                self.assertIsNone(error)
                starts = [m for m in sent if m["type"] == "http.response.start"]
                self.assertEqual([m["status"] for m in starts], [499])

    def test_a_hang_up_is_not_reported_to_sentry(self):
        import asyncio
        import sentry_sdk
        from sentry_sdk.transport import Transport

        class Capture(Transport):
            def __init__(self, options=None):
                super().__init__(options)
                self.events = []

            def capture_envelope(self, envelope):
                self.events.append(envelope)

        transport = Capture()
        sentry_sdk.init(dsn="http://key@localhost/1", transport=transport, traces_sample_rate=0.0)
        try:
            _sent, error = asyncio.run(_post_and_hang_up(_app(), "/family"))
            sentry_sdk.flush()
        finally:
            sentry_sdk.get_global_scope().set_client(None)
        self.assertIsNone(error)
        sent = [item.type for envelope in transport.events for item in envelope.items]
        self.assertNotIn("event", sent)


class _ThingIn(api_body.BodyModel):
    name: str | None = None
    tags: list[str] | None = None


class TestReadModel(unittest.TestCase):
    """The model never changes what the domain gets; it only decides whether
    a domain crash on the body is a 400 instead of a 500."""

    def setUp(self):
        app = FastAPI()
        api_body.install(app)

        def domain(body):
            return {"upper": body.get("name", "").upper()}  # crashes on non-dicts / non-str

        @app.post("/thing")
        async def thing(request: Request):
            body = await api_body.read_model(request, _FAMILY, _ThingIn)
            return json_response(await api_body.run(body, domain, body.value))

        @app.post("/thing-db-down")
        async def thing_db_down(request: Request):
            from app.core import db
            body = await api_body.read_model(request, _FAMILY, _ThingIn)

            def down(_value):
                raise db.DbUnavailable("x")
            return json_response(await api_body.run(body, down, body.value))

        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self):
        self.client.close()

    def test_matching_body_reaches_the_domain_untouched(self):
        r = self.client.post("/thing", json={"name": "ana", "zz": 1})
        self.assertEqual((r.status_code, r.json()), (200, {"upper": "ANA"}))

    def test_mismatch_that_the_domain_survives_is_answered_by_the_domain_when_not_strict(self):
        # tags is not a list of strings, but the domain never reads it (TestStrictBoundary: the switch on).
        from unittest import mock
        with mock.patch.object(api_body, "STRICT", False):
            r = self.client.post("/thing", json={"name": "ana", "tags": [1]})
        self.assertEqual((r.status_code, r.json()), (200, {"upper": "ANA"}))

    def test_mismatch_that_crashes_the_domain_is_the_family_400(self):
        for raw, reason in ((b"[]", "el cuerpo: se esperaba un objeto"),
                            (b"null", "el cuerpo: se esperaba un objeto"),
                            (b'{"name": 5}', "campo name: se esperaba texto"),
                            (b'{"name": ["a"]}', "campo name: se esperaba texto")):
            with self.subTest(raw=raw):
                r = self.client.post("/thing", content=raw)
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.json(), {"error": f"JSON inválido: {reason}"})

    def test_a_crash_on_a_matching_body_is_still_a_500(self):
        app = FastAPI()
        api_body.install(app)

        @app.post("/bug")
        async def bug(request: Request):
            body = await api_body.read_model(request, _FAMILY, _ThingIn)
            return json_response(await api_body.run(body, lambda _v: 1 / 0, body.value))

        with TestClient(app, raise_server_exceptions=False) as client:
            self.assertEqual(client.post("/bug", json={"name": "ok"}).status_code, 500)

    def test_database_outage_is_never_a_body_error(self):
        # Strict, a mismatched body never reaches the domain; `run()` still decides when the switch is off.
        from unittest import mock
        with mock.patch.object(api_body, "STRICT", False):
            r = self.client.post("/thing-db-down", content=b"[]")
        self.assertEqual(r.status_code, 500)  # no app handler here: it propagates

    def test_drift_is_logged_once_without_values(self):
        api_body._DRIFT_SEEN.clear()
        with self.assertLogs("app.core.api_body", level="WARNING") as logs:
            self.client.post("/thing", json={"name": "ana", "tags": ["x", 7, "secreto-123"]})
            self.client.post("/thing", json={"name": "ana", "tags": [8]})
        drift = [m for m in logs.output if "body_contract_drift" in m]
        self.assertEqual(len(drift), 1)
        self.assertIn("loc=tags.#", drift[0])
        self.assertNotIn("secreto", " ".join(logs.output))
        self.assertNotIn("7", drift[0].split("loc=")[1])


class TestBodyInTheSignature(unittest.TestCase):
    """The body as a dependency (TASK-76 item E): after what the signature declares ahead of it, decoded and
    answered like `read_model()`, only on the methods that carry it, or decoded later by the handler."""

    @classmethod
    def setUpClass(cls):
        from fastapi import Depends
        from app.core import deps
        app = FastAPI()
        deps.install_gate_handlers(app)

        async def gate(request: Request):
            if request.headers.get("x-pass") != "1":
                raise deps.EarlyResponse(json_response({"error": "no_autenticado"}, 401))

        @app.api_route("/eager", methods=["GET", "POST"])
        async def eager(request: Request, _gate: None = Depends(gate),
                        body: api_body.Body = Depends(api_body.body(_FAMILY, _ThingIn, methods={"POST"}))):
            return json_response({"value": body.value, "matches": body.errors is None,
                                  "none": body is api_body.NO_BODY})

        @app.post("/deferred")
        async def deferred(request: Request,
                           pending: api_body.PendingBody = Depends(api_body.deferred(_FAMILY, _ThingIn))):
            if request.headers.get("x-pass") != "1":
                return json_response({"error": "no_autenticado"}, 401)
            body = await pending.read()
            return json_response({"value": body.value, "matches": body.errors is None})

        @app.post("/lazy")
        async def lazy(request: Request,
                       pending: api_body.PendingBody = Depends(api_body.lazy(_FAMILY, _ThingIn))):
            if request.headers.get("x-pass") != "1":
                return json_response({"error": "no_autenticado"}, 401)
            body = await pending.lazy()
            return json_response({"got": body(), "matches": body.errors is None})

        @app.post("/raw")
        async def raw(body: api_body.RawRead = Depends(api_body.raw(_FAMILY, _ThingIn))):
            return json_response({"raw": body.raw.decode("utf-8", "replace"), "value": body.value})

        cls.app = app
        cls.client = TestClient(app)

    def _asgi(self, path, headers=()):
        """POST `path` straight through ASGI with a body that never finishes: (status, receive calls)."""
        import asyncio
        sent, calls = [], []

        async def receive():
            calls.append(1)
            return {"type": "http.request", "body": b'{"na', "more_body": len(calls) < 3}

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
                 "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
                 "root_path": "", "server": ("test", 80), "client": ("127.0.0.1", 1),
                 "headers": [(b"content-type", b"application/json"), (b"content-length", b"100000")]
                 + [(k.encode(), v.encode()) for k, v in headers]}
        asyncio.run(self.app(scope, receive, send))
        return next(m["status"] for m in sent if m["type"] == "http.response.start"), len(calls)

    def test_what_answers_first_still_answers_first(self):
        for path in ("/eager", "/deferred", "/lazy"):
            with self.subTest(path=path):
                r = self.client.post(path, content=b"{not json")
                self.assertEqual((r.status_code, r.json()), (401, {"error": "no_autenticado"}))

    def test_a_refusal_ahead_of_the_handler_read_never_reads_the_body(self):
        # deferred()/lazy() read nothing in the dependency: the handler's 401 goes out before a byte of the
        # body is asked for (the base order of the 12 routes of TASK-76 №4); body() reads it, behind its gate.
        for path in ("/deferred", "/lazy"):
            with self.subTest(path=path):
                self.assertEqual(self._asgi(path), (401, 0))
        self.assertEqual(self._asgi("/eager"), (401, 0))
        self.assertEqual(self._asgi("/deferred", [("x-pass", "1")])[1], 3)

    def test_lazy_hands_the_domain_its_lazy_body(self):
        r = self.client.post("/lazy", content=b'{"name": "x"}', headers={"x-pass": "1"})
        self.assertEqual(r.json(), {"got": {"name": "x"}, "matches": True})

    def test_then_the_body_answers_like_read_model(self):
        for path in ("/eager", "/deferred"):
            with self.subTest(path=path):
                r = self.client.post(path, content=b"{not json", headers={"x-pass": "1"})
                self.assertEqual(r.status_code, 400)
                self.assertTrue(r.json()["error"].startswith("JSON inválido: "))
                r = self.client.post(path, content=b'{"name": "x"}', headers={"x-pass": "1"})
                self.assertEqual(r.json(), {"value": {"name": "x"}, "matches": True} | (
                    {"none": False} if path == "/eager" else {}))

    def test_a_method_without_a_body_reads_none(self):
        r = self.client.request("GET", "/eager", content=b"{not json", headers={"x-pass": "1"})
        self.assertEqual(r.json(), {"value": None, "matches": True, "none": True})

    def test_raw_hands_the_bytes_and_never_answers(self):
        self.assertEqual(self.client.post("/raw", content=b"{not json").json(), {"raw": "{not json", "value": None})


class _Recorder:
    """A domain that records whether it was reached."""

    def __init__(self):
        self.calls = 0

    def __call__(self, value):
        self.calls += 1
        return {"value": value}


class TestStrictBoundary(unittest.TestCase):
    """TASK-76 item F: with `api_body.STRICT` on, a body that does not match its route's model is answered like
    a body that does not decode — 400 with the family's «JSON inválido: <reason>», at the same point — and never
    reaches the domain, whichever way the route reads it. Off (`STRICT = False`, the rollback) the models only
    observe again: the domain gets the body and answers it."""

    _MISMATCHES = ((b"[]", "el cuerpo: se esperaba un objeto"),
                   (b"null", "el cuerpo: se esperaba un objeto"),
                   (b'{"name": 5}', "campo name: se esperaba texto"),
                   (b'{"name": "a", "tags": [1]}', "campo tags.#: se esperaba texto"))

    @classmethod
    def setUpClass(cls):
        from fastapi import Depends
        from app.core import deps
        cls.domain = domain = _Recorder()
        app = FastAPI()
        deps.install_gate_handlers(app)

        async def gate(request: Request):
            if request.headers.get("x-pass") != "1":
                raise deps.EarlyResponse(json_response({"error": "no_autenticado"}, 401))

        @app.post("/body")
        async def eager(_gate: None = Depends(gate),
                        body: api_body.Body = Depends(api_body.body(_FAMILY, _ThingIn))):
            return json_response(domain(body.value))

        @app.post("/deferred")
        async def deferred(pending: api_body.PendingBody = Depends(api_body.deferred(_FAMILY, _ThingIn))):
            return json_response(domain((await pending.read()).value))

        @app.post("/lazy")
        async def lazy(pending: api_body.PendingBody = Depends(api_body.lazy(_FAMILY, _ThingIn))):
            # As the lazy domains do: the handler reads the bytes, then their own 400 for whatever reading
            # the body raises.
            body = await pending.lazy()
            try:
                value = body()
            except Exception as exc:  # noqa: BLE001
                return json_response({"error": f"JSON inválido: {exc}"}, 400)
            return json_response(domain(value))

        @app.post("/raw")
        async def raw(body: api_body.RawRead = Depends(api_body.raw(_FAMILY, _ThingIn))):
            return json_response(domain(body.raw.decode("utf-8", "replace")))

        def malformed_is_empty(raw):
            try:
                return api_body.decode(raw)
            except ValueError:
                return {}

        @app.post("/lenient")
        async def lenient(body: api_body.Body = Depends(api_body.lenient(_FAMILY, _ThingIn, malformed_is_empty))):
            return json_response(domain(body.value))

        @app.post("/read-model")
        async def read_model(request: Request):
            return json_response(domain((await api_body.read_model(request, _FAMILY, _ThingIn)).value))

        @app.post("/no-family")
        async def no_family(body: api_body.RawRead = Depends(api_body.raw(None, _ThingIn))):
            return json_response(domain(body.raw.decode("utf-8", "replace")))

        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    _PATHS = ("/body", "/deferred", "/lazy", "/raw", "/lenient", "/read-model")

    def _post(self, path, raw):
        before = self.domain.calls
        r = self.client.post(path, content=raw, headers={"x-pass": "1"})
        return r, self.domain.calls - before

    def test_the_switch_is_on(self):
        self.assertIs(api_body.STRICT, True)

    def test_a_mismatch_is_the_family_400_and_never_reaches_the_domain(self):
        for path in self._PATHS:
            for raw, reason in self._MISMATCHES:
                with self.subTest(path=path, raw=raw):
                    r, reached = self._post(path, raw)
                    self.assertEqual((r.status_code, r.json(), reached),
                                     (400, {"error": f"JSON inválido: {reason}"}, 0))
                    self.assertEqual(r.headers["cache-control"], "no-store")

    def test_a_matching_body_reaches_the_domain_unknown_fields_and_all(self):
        for path in self._PATHS:
            with self.subTest(path=path):
                r, reached = self._post(path, b'{"name": "a", "zz": [1]}')
                self.assertEqual((r.status_code, reached), (200, 1))

    def test_what_answers_first_still_answers_first(self):
        r = self.client.post("/body", content=b"[]")
        self.assertEqual((r.status_code, r.json()), (401, {"error": "no_autenticado"}))

    def test_a_route_without_a_family_only_observes(self):
        r, reached = self._post("/no-family", b'{"name": 5}')
        self.assertEqual((r.status_code, reached), (200, 1))

    def test_watch_only_observes(self):
        from starlette.requests import Request as StarletteRequest
        request = StarletteRequest({"type": "http", "method": "POST", "path": "/watched", "headers": []})
        self.assertIsNone(api_body.watch(request, {"name": 5}, _ThingIn))

    def test_switched_off_the_domain_answers_the_mismatch_again(self):
        from unittest import mock
        with mock.patch.object(api_body, "STRICT", False):
            for path in self._PATHS:
                with self.subTest(path=path):
                    r, reached = self._post(path, b'{"name": 5}')
                    self.assertEqual((r.status_code, reached), (200, 1))


class TestWatch(unittest.TestCase):
    """`watch()`: the model check alone — the drift is logged, the value is not touched."""

    def test_drift_is_logged_and_nothing_else_happens(self):
        from starlette.requests import Request
        request = Request({"type": "http", "method": "POST", "path": "/watched", "headers": []})
        value = {"name": 5}
        api_body._DRIFT_SEEN.clear()
        with self.assertLogs("app.core.api_body", level="WARNING") as logs:
            self.assertIsNone(api_body.watch(request, value, _ThingIn))
        self.assertEqual(value, {"name": 5})
        self.assertEqual(logs.output, [
            "WARNING:app.core.api_body:body_contract_drift route=/watched loc=name type=string_type"])


class TestDocs(unittest.TestCase):

    def tearDown(self):
        api_body._MODELS.pop("_ThingIn", None)

    def test_docs_names_the_models_per_method(self):
        extra = api_body.docs(post=(_ThingIn, api_body.ApiError), get=(None, api_body.ApiError))
        self.assertEqual(extra, {"x-turnos-docs": {
            "post": {"body": "_ThingIn", "ok": "ApiError"}, "get": {"ok": "ApiError"}}})
        schemas = api_body.component_schemas()
        self.assertEqual(schemas["ApiError"]["required"], ["error"])
        self.assertIn("_ThingIn", schemas)

    def test_two_models_with_one_name_are_refused(self):
        class ApiError(api_body.ResponseModel):  # noqa: F811 — the point of the test
            other: int
        with self.assertRaises(ValueError):
            api_body.docs(post=(None, ApiError))

    def test_a_body_that_is_not_json_is_documented_by_its_media_types(self):
        extra = api_body.docs(post=(api_body.RawBody("A CSV file", "text/csv"), None))
        self.assertEqual(extra, {"x-turnos-docs": {"post": {
            "raw": {"description": "A CSV file", "media_types": ["text/csv"]}}}})
        self.assertEqual(api_body.RawBody("A file").media_types, ("application/octet-stream",))

    def test_query_docs_lists_each_method_s_parameters(self):
        extra = api_body.query_docs(
            get={"q": "Search", "days": api_body.Query("Days", {"type": "integer"})}, delete={})
        self.assertEqual(extra, {"x-turnos-query": {
            "get": [{"name": "q", "description": "Search", "schema": {"type": "string"}},
                    {"name": "days", "description": "Days", "schema": {"type": "integer"}}],
            "delete": []}})


if __name__ == "__main__":
    unittest.main()
