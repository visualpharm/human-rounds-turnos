"""test_db_unavailable_answer.py — every 503 about the database goes through `core.deps.answer_db_unavailable`.

The product owner's hub, 2026-11-10: the routes that catch `db.DbUnavailable` themselves answered their own 503 and the
`db_unavailable` launch signal never heard of their outage. Now one function answers that 503 and sends the
signal itself (`core/deps.py`), so a new route cannot forget it. The inventory below is the guard: the number
503 — a literal, an `HTTPStatus.SERVICE_UNAVAILABLE`, a `HTTP_503_…` name — may appear in the server code only
inside that function, and `signal_db_unavailable` is called only from `core/deps.py`. The 503s that are not
about the database are listed in `_NOT_THE_DATABASE` by file and function, with how many and why; a listed one
that is gone is red too, so the list stays the truth.

Fix a red result by answering `deps.answer_db_unavailable(...)` (JSON by default, `request=` for the gzip
variant, `build=lambda status: …` for any other shape), not by adding a line to the list — unless the 503 is
really not about the database.

A database that stops answering under a statement (Postgres restarting, a dropped connection) is one too: the
pooled `with` raises it as `db.DbConnectionLost` (`TestTheConnectionExit`).

The second half pins the answers themselves: the same status, headers and body as before the change on a
route of every kind (plain JSON, gzip JSON, HTML, the accounts `Result`, a domain `AuthResult`, an
`HTTPException`, the uncaught handler), and the signal leaving once per call.
"""
import ast
import asyncio
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_THE_FUNCTION = ("app/core/deps.py", "answer_db_unavailable")
# The default JSON for a commit whose outcome is unknown (`db.DbCommitUnknown`, the product owner's hub review, 2026-11-11).
_COMMIT_UNKNOWN = {"error": "la base no respondió al guardar, revisá si quedó antes "
                            "de repetir", "outcome": "unknown"}
_SIGNAL_HOME = "app/core/deps.py"

# (file, function) -> (how many 503s, why it is not the database). A module-level 503 is function "".
_NOT_THE_DATABASE = {
    ("app/routers/health.py", "_ready_endpoint"): (1, "/ready: the readiness probe's own «not ready», read by the orchestrator"),
    ("app/worker.py", "_start_health_server._Salud.do_GET"): (1, "the worker's /ready probe on its own port"),
    ("app/modules/chat/voice.py", "transcribe_result"): (2, "dictation switched off or without its provider key"),
    ("app/ai/prompts.py", "save_prompt"): (1, "the translation provider did not answer"),
    ("app/admin_panel/admin_content.py", "knowledge_properties"): (1, "no AI configured"),
    ("app/modules/intake/intake_admin.py", "door_poster_pdf"): (1, "the PDF libraries are not installed"),
    ("app/ops/demo_installation.py", "patient_access_state"): (1, "the demo patient is not seeded"),
    ("app/ops/demo_installation.py", "doctor_access_identity"): (1, "the demo doctor is not seeded"),
    ("app/modules/turnos/booking_create.py", "_check_catalog_refs"): (2, "the catalog file cannot be read (`catalogo_no_disponible`)"),
    ("app/modules/turnos/booking_decisions.py", "_approve_core"): (1, "the catalog file cannot be read for «Derivar a»"),
    ("app/cuentas/accounts.py", "_guarded"): (1, "the patient portal is misconfigured (`autenticacion_no_disponible`)"),
    ("app/cuentas/accounts.py", "_login_test_identity_core"): (1, "the identity provider did not answer"),
    ("app/admin_panel/admin_account.py", "totp_start"): (1, "an AccountError the router turns into the answer: the account row could not be read, so no 2FA restart without the password (staff-auth review 2026-11, finding 11)"),
    ("app/ai/ai_health.py", ""): (1, "a status an AI provider answers us, not one we answer"),
    ("app/core/backup_s3.py", "S3.request"): (1, "a status the storage answers us, retried"),
    ("app/ops/data_backups.py", "_upload_b2"): (1, "a status B2 answers us, retried"),
}

_SKIP_TOP = {"tests", "scripts", "tools", "alembic", "vendor", "static", "stories", "node_modules", "docs",
             "extensions", "locales", "packs", "templates", "design-plans"}


def _is_503(node):
    if isinstance(node, ast.Constant) and type(node.value) is int and node.value == 503:
        return True
    name = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else None
    return bool(name) and ("503" in name or name == "SERVICE_UNAVAILABLE")


class _Scan(ast.NodeVisitor):
    def __init__(self):
        self.stack = []
        self.found_503 = []     # function qualname per 503
        self.signal_calls = 0

    def _scope(self, node):
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope

    def generic_visit(self, node):
        if _is_503(node):
            self.found_503.append(".".join(self.stack))
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
            if name == "signal_db_unavailable":
                self.signal_calls += 1
        super().generic_visit(node)


def scan(root):
    """({(file, function): number of 503s}, {file: number of signal_db_unavailable calls}) for the server code."""
    found, signals = {}, {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        top = "" if rel_dir == "." else rel_dir.split(os.sep)[0]
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and not (top == "" and d in _SKIP_TOP)
                       and d not in ("node_modules", "__pycache__")]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            try:
                with open(path, encoding="utf-8") as f:
                    tree = ast.parse(f.read(), path)
            except (SyntaxError, UnicodeDecodeError):
                continue
            visitor = _Scan()
            visitor.visit(tree)
            for where in visitor.found_503:
                found[(rel, where)] = found.get((rel, where), 0) + 1
            if visitor.signal_calls:
                signals[rel] = visitor.signal_calls
    return found, signals


def violations(found, signals, allowed=None):
    allowed = _NOT_THE_DATABASE if allowed is None else allowed
    out = []
    for (path, where), n in sorted(found.items()):
        if (path, where) == _THE_FUNCTION:
            continue
        if (path, where) not in allowed:
            out.append("%s:%s answers %d 503(s) of its own; answer deps.answer_db_unavailable(...) instead"
                       % (path, where or "<module>", n))
        elif n != allowed[(path, where)][0]:
            out.append("%s:%s has %d 503(s), the list says %d" % (path, where or "<module>", n,
                                                                  allowed[(path, where)][0]))
    for key in sorted(set(allowed) - set(found)):
        out.append("%s:%s is listed but answers no 503 any more; drop it from _NOT_THE_DATABASE" % key)
    if found.get(_THE_FUNCTION) != 1:
        out.append("%s:%s must hold the one 503" % _THE_FUNCTION)
    for path, n in sorted(signals.items()):
        if path != _SIGNAL_HOME:
            out.append("%s calls signal_db_unavailable %d time(s); answer deps.answer_db_unavailable(...), "
                       "which sends it" % (path, n))
    return out


class TestInventory(unittest.TestCase):
    def test_no_503_outside_the_one_answer(self):
        self.assertEqual([], violations(*scan(ROOT)))

    def test_every_exception_says_why(self):
        for key, (n, why) in _NOT_THE_DATABASE.items():
            self.assertGreater(n, 0, key)
            self.assertTrue(why.strip(), key)


class TestTheScan(unittest.TestCase):
    """The guard itself: a router with its own 503 is red, in any spelling."""

    def _violations(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        files = dict(files)
        files.setdefault("app/core/deps.py", "def answer_db_unavailable():\n    status = 503\n"
                                         "    signal_db_unavailable()\n")
        for rel, body in files.items():
            path = os.path.join(tmp.name, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(body)
        return violations(*scan(tmp.name), allowed={})

    def test_the_one_answer_alone_is_green(self):
        self.assertEqual([], self._violations({}))

    def test_a_new_router_with_its_own_503_is_red(self):
        spellings = [
            "    return json_response({'error': 'x'}, status=503)\n",
            "    raise HTTPException(503, detail={})\n",
            "    return Response(status_code=503)\n",
            "    return JSONResponse({}, status_code=HTTPStatus.SERVICE_UNAVAILABLE)\n",
            "    return JSONResponse({}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)\n",
            "    return 503, {'error': 'x'}\n",
        ]
        for line in spellings:
            out = self._violations({"modules/new/new_router.py": "async def route():\n" + line})
            self.assertEqual(len(out), 1, line)
            self.assertIn("modules/new/new_router.py:route", out[0])

    def test_a_route_that_signals_by_hand_is_red(self):
        out = self._violations({"admin_panel/x_router.py": "def f():\n    deps.signal_db_unavailable()\n"})
        self.assertEqual(len(out), 1)
        self.assertIn("admin_panel/x_router.py calls signal_db_unavailable", out[0])

    def test_tests_and_scripts_are_outside_the_scan(self):
        self.assertEqual([], self._violations({"tests/test_x.py": "X = 503\n", "scripts/load.py": "X = 503\n"}))

    def test_a_listed_503_that_is_gone_is_red(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.makedirs(os.path.join(tmp.name, "app", "core"))
        with open(os.path.join(tmp.name, "app", "core", "deps.py"), "w", encoding="utf-8") as f:
            f.write("def answer_db_unavailable():\n    return 503\n")
        out = violations(*scan(tmp.name), allowed={("app/routers/health.py", "ready"): (1, "probe")})
        self.assertEqual(["app/routers/health.py:ready is listed but answers no 503 any more; "
                          "drop it from _NOT_THE_DATABASE"], out)


def _join_signal_threads():
    import threading
    for thread in threading.enumerate():
        if thread.name == "db-unavailable-signal":
            thread.join(5)


class TestTheAnswers(unittest.TestCase):
    """Same status, headers and body as before, on a route of every kind; the signal leaves once per answer."""

    def setUp(self):
        from app.core import deps
        self.deps = deps
        patcher = mock.patch.object(deps, "signal_db_unavailable")
        self.signal = patcher.start()
        self.addCleanup(patcher.stop)

    def _request(self, headers=()):
        from starlette.requests import Request
        return Request({"type": "http", "method": "GET", "path": "/", "query_string": b"",
                        "headers": [(k.encode(), v.encode()) for k, v in headers]})

    def _wire(self, response):
        return response.status_code, dict(response.headers), bytes(response.body)

    def test_the_default_is_the_json_every_route_sent(self):
        from app.core import http_json
        before = http_json.json_response({"error": "base de datos no disponible"}, status=503)
        self.assertEqual(self._wire(before), self._wire(self.deps.answer_db_unavailable()))
        self.signal.assert_called_once_with()

    def test_with_the_request_it_is_the_gzip_json(self):
        from app.core import http_json
        request = self._request([("accept-encoding", "gzip")])
        body = {"error": "x" * 2000}   # big enough for gzip_bridge to compress
        before = http_json.gzip_json_response(request, body, status=503)
        after = self.deps.answer_db_unavailable(body=body, request=request)
        self.assertEqual(self._wire(before), self._wire(after))
        self.assertEqual(after.headers.get("content-encoding"), "gzip")

    def test_build_gets_the_status_and_its_answer_is_returned(self):
        self.assertEqual((503, {"e": 1}), self.deps.answer_db_unavailable(lambda status: (status, {"e": 1})))
        self.signal.assert_called_once_with()

    def test_the_uncaught_handler_keeps_retry_after(self):
        from app.core import db
        response = asyncio.run(self.deps.render_db_unavailable(None, db.DbUnavailable("pool")))
        self.assertEqual(503, response.status_code)
        self.assertEqual(b'{"error": "base de datos no disponible"}', bytes(response.body))
        self.assertEqual("3", response.headers["Retry-After"])
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.signal.assert_called_once_with()

    _UNKNOWN = _COMMIT_UNKNOWN

    def test_a_commit_of_unknown_outcome_does_not_promise_a_safe_retry(self):
        """The product owner's hub review, 2026-11-11: a broken commit may have written. The default answer says that the
        outcome is unknown (`outcome: unknown`, and a phrase that says check before repeating) instead of the
        plain «base de datos no disponible»; still 503 and still the signal."""
        from app.core import db
        try:
            raise db.DbCommitUnknown("la base dejó de responder al confirmar (OperationalError)")
        except db.DbUnavailable:
            response = self.deps.answer_db_unavailable()
        self.assertEqual(503, response.status_code)
        self.assertEqual(self._UNKNOWN, json.loads(bytes(response.body)))
        self.signal.assert_called_once_with()

    def test_the_uncaught_handler_drops_retry_after_for_an_unknown_commit(self):
        from app.core import db
        response = asyncio.run(self.deps.render_db_unavailable(None, db.DbCommitUnknown("commit")))
        self.assertEqual(503, response.status_code)
        self.assertEqual(self._UNKNOWN, json.loads(bytes(response.body)))
        self.assertNotIn("Retry-After", response.headers)
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.assertEqual(str(len(bytes(response.body))), response.headers["Content-Length"])
        self.signal.assert_called_once_with()

    _PATIENT_UNKNOWN = {"error": "No sabemos si quedó guardado. Revisá antes de repetir.",
                        "detalle": "No sabemos si quedó guardado. Revisá antes de repetir.", "outcome": "unknown",
                        "accion": {"texto": "Ver mis turnos", "url": "/my-requests"}}

    def _at(self, path):
        from starlette.requests import Request
        return Request({"type": "http", "method": "POST", "path": path, "query_string": b"", "headers": []})

    def test_a_patient_route_gets_the_way_to_check_mis_turnos(self):
        """The patient gets the solution, not the error: on a patient route the unknown outcome names the next
        step — look at «Mis turnos» — with the link, in the booking limit's `detalle` + `accion` shape."""
        from app.core import db
        for path in ("/api/requests", "/api/requests/7/cancel", "/api/offer/tok/accept", "/api/confirm/tok/si"):
            with self.subTest(path=path):
                try:
                    raise db.DbCommitUnknown("commit")
                except db.DbUnavailable:
                    response = self.deps.answer_db_unavailable(request=self._at(path))
                self.assertEqual(self._PATIENT_UNKNOWN, json.loads(bytes(response.body)))

    def test_a_staff_route_keeps_the_staff_sentence(self):
        from app.core import db
        for path in ("/api/admin/requests", "/admin/queue", "/api/requestsx", "/api/account/me"):
            with self.subTest(path=path):
                try:
                    raise db.DbCommitUnknown("commit")
                except db.DbUnavailable:
                    response = self.deps.answer_db_unavailable(request=self._at(path))
                self.assertEqual(self._UNKNOWN, json.loads(bytes(response.body)))

    def test_the_uncaught_handler_on_a_patient_route(self):
        from app.core import db
        response = asyncio.run(self.deps.render_db_unavailable(self._at("/api/requests"),
                                                               db.DbCommitUnknown("commit")))
        self.assertEqual(self._PATIENT_UNKNOWN, json.loads(bytes(response.body)))

    def test_the_booking_routes_answer_an_unknown_commit_not_no_disponible(self):
        """The booking router passes its own «no disponible» body, which promises a safe retry; a broken commit
        answers the patient's unknown outcome instead. A database that simply does not answer keeps the old body."""
        from app.core import db
        from app.modules.turnos import booking_requests_router as router
        try:
            raise db.DbCommitUnknown("commit")
        except db.DbUnavailable:
            unknown = router._db_unavailable_response(self._at("/api/requests"))
        try:
            raise db.DbUnavailable("pool")
        except db.DbUnavailable:
            down = router._db_unavailable_response(self._at("/api/requests"))
        self.assertEqual((503, self._PATIENT_UNKNOWN), (unknown.status_code, json.loads(bytes(unknown.body))))
        self.assertEqual((503, {"error": "base de datos no disponible"}), (down.status_code, json.loads(bytes(down.body))))

    def test_a_route_with_its_own_body_keeps_it_for_an_unknown_commit(self):
        from app.core import db
        try:
            raise db.DbCommitUnknown("commit")
        except db.DbUnavailable:
            self.assertEqual((503, {"e": 1}), self.deps.answer_db_unavailable(lambda status: (status, {"e": 1})))

    def test_a_plain_json_route_prompts(self):
        from app.ai import prompts, prompts_router
        from app.core import db
        with mock.patch.object(prompts, "list_prompts", side_effect=db.DbUnavailable("pool")):
            response = asyncio.run(prompts_router.prompts_list(identity={}))
        self.assertEqual((503, b'{"error": "base de datos no disponible"}'), (response.status_code, bytes(response.body)))
        self.assertEqual("application/json; charset=utf-8", response.headers["Content-Type"])
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.signal.assert_called_once_with()

    def test_a_gzip_json_route_calendar_feed(self):
        from app.core import db
        from app.modules.turnos import calendar_feed, calendar_feed_router
        with mock.patch.object(calendar_feed, "resolve_feed_ics", side_effect=db.DbUnavailable("pool")):
            response = asyncio.run(calendar_feed_router.feed_ics(self._request(), "tok"))
        self.assertEqual((503, b'{"error": "base de datos no disponible"}'), (response.status_code, bytes(response.body)))
        self.assertEqual("no-store", response.headers["Cache-Control"])
        self.signal.assert_called_once_with()

    def test_an_html_route_the_appointment_offer(self):
        from app.core import db
        from app.modules.turnos import appointment_offer, appointment_offer_router
        with mock.patch.object(appointment_offer, "build_offer_page", side_effect=db.DbUnavailable("pool")), \
                mock.patch.object(appointment_offer, "_database_unavailable_page", return_value="<p>sin base</p>"):
            response = asyncio.run(appointment_offer_router.offer_page(self._request(), "tok"))
        self.assertEqual((503, b"<p>sin base</p>"), (response.status_code, bytes(response.body)))
        self.assertEqual("text/html; charset=utf-8", response.headers["Content-Type"])
        self.signal.assert_called_once_with()

    def test_the_accounts_result(self):
        from app.cuentas import accounts

        def down():
            raise accounts.store.DbUnavailable("pool")
        result = accounts._guarded(down)
        self.assertEqual(503, result.status)
        self.assertEqual({"error": "base de datos no disponible"}, result.json_body)
        self.assertEqual({"Cache-Control": "no-store"}, result.extra_headers)
        self.signal.assert_called_once_with()

    def test_a_domain_auth_result_passkeys(self):
        from app.auth import passkeys
        from app.core import db
        with mock.patch.object(passkeys.admin_auth.staff_portal, "enabled", return_value=False), \
                mock.patch.object(passkeys, "_issue_reto", side_effect=db.DbUnavailable("pool")):
            result = passkeys.login_options_result("turnos.example")
        self.assertEqual((503, {"error": "base_no_disponible"}), (result.status, result.body))
        self.signal.assert_called_once_with()

    def test_an_http_exception_the_study_route(self):
        from fastapi import HTTPException
        from app.core import db
        from app.modules.turnos import study_router

        def down():
            raise db.DbUnavailable("pool")
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(study_router.call(down))
        self.assertEqual((503, {"error": "study_unavailable"}), (caught.exception.status_code, caught.exception.detail))
        self.signal.assert_called_once_with()


_PERSONAL = "ana.perez@gmail.com 30111222"


def _in_a_pooled_connection(exc, *, on_commit=False):
    """A database call whose statement (or, `on_commit`, whose commit) raises `exc` inside the real pooled `with`
    of `db._connect()` (`db._PooledConn`): only the pool and the psycopg connection are stand-ins, the exit that
    decides what the error means is the real one."""
    from app.core import db

    def call(*_args, **_kwargs):
        conn = mock.MagicMock()
        if on_commit:
            conn.__exit__.side_effect = exc
            with db._PooledConn(mock.Mock(), conn):
                return None
        with db._PooledConn(mock.Mock(), conn):
            raise exc
    call.__name__ = "%s%s" % (type(exc).__name__, " on commit" if on_commit else "")
    call.on_commit = on_commit
    return call


def _lost_connections():
    """What Postgres restarting, crashing or dropping the connection raises under a statement (the product owner's hub review,
    2026-11-10), each inside a pooled connection; empty without the driver."""
    try:
        import psycopg
        from psycopg import errors
    except Exception:  # noqa: BLE001 — no driver in this environment
        return []
    return [_in_a_pooled_connection(psycopg.OperationalError("server closed the connection unexpectedly")),
            _in_a_pooled_connection(errors.AdminShutdown("terminating connection due to administrator command")),
            _in_a_pooled_connection(errors.CannotConnectNow("the database system is starting up")),
            _in_a_pooled_connection(psycopg.OperationalError("the connection is lost"), on_commit=True)]


def _answered_errors():
    """Errors the database ANSWERED with, inside a pooled connection: not an outage."""
    try:
        from psycopg import errors
    except Exception:  # noqa: BLE001
        return []
    return [_in_a_pooled_connection(errors.DeadlockDetected("deadlock detected"))]


class TestOnlyTheDatabaseSignals(unittest.TestCase):
    """The product owner's hub, 2026-11-10: the places that caught any exception as «database unavailable» (chat voice, MCP,
    the brand colour, logout, the passkey counter) answer 503 and report `db_unavailable` only for a database
    that does not answer (`deps.DB_UNAVAILABLE_ERRORS`); any other failure logs its type and answers its own
    honest error, without the signal."""

    def setUp(self):
        from app.core import deps
        self.deps = deps
        patcher = mock.patch.object(deps, "signal_db_unavailable")
        self.signal = patcher.start()
        self.addCleanup(patcher.stop)

    def _down(self):
        """The failures that mean «the database does not answer»."""
        from app.core import db
        out = [db.DbUnavailable("pool")]
        try:
            from psycopg import errors
        except Exception:  # noqa: BLE001 — no driver in this environment
            return out
        return (out + [errors.QueryCanceled("statement timeout"), errors.LockNotAvailable("lock timeout")]
                + _lost_connections())

    def _check(self, answer, down_answer, other_answer, commit_answer=None):
        """`answer(exc)` → what the place answers when its database call raises `exc` (an exception, or a
        callable that raises it from inside a pooled connection); `commit_answer`, when the place answers a
        connection lost under the commit differently (the default JSON says the outcome is unknown)."""
        for exc in self._down():
            name = getattr(exc, "__name__", type(exc).__name__)
            expected = commit_answer if commit_answer is not None and getattr(exc, "on_commit", False) else down_answer
            self.signal.reset_mock()
            self.assertEqual(expected, answer(exc), name)
            self.signal.assert_called_once_with()
        for exc, type_name in [(RuntimeError(_PERSONAL), "RuntimeError")] + [
                (call, call.__name__) for call in _answered_errors()]:
            self.signal.reset_mock()
            with self.assertLogs(level="ERROR") as logs:
                got = answer(exc)
            self.assertEqual(other_answer, got, type_name)
            self.signal.assert_not_called()
            self.assertIn(type_name, "\n".join(logs.output))
            self.assertNotIn("30111222", "\n".join(logs.output))

    def _post(self, raw, path="/api/admin/chat-voice"):
        from starlette.requests import Request
        sent = []

        async def receive():
            if sent:
                return {"type": "http.disconnect"}
            sent.append(1)
            return {"type": "http.request", "body": raw, "more_body": False}
        return Request({"type": "http", "method": "POST", "path": path, "query_string": b"",
                        "headers": [(b"content-type", b"application/json")]}, receive)

    @staticmethod
    def _run(endpoint, request, **kwargs):
        """The handler as FastAPI calls it: its `core.api_body` dependency resolved on the same request
        first (the body is a dependency of the signature)."""
        import inspect

        async def call():
            for name, param in inspect.signature(endpoint).parameters.items():
                dependency = getattr(param.default, "dependency", None)
                if getattr(dependency, "body_kind", None) and name not in kwargs:
                    kwargs[name] = await dependency(request)
            return await endpoint(request, **kwargs)
        return asyncio.run(call())

    def test_a_body_that_is_not_an_object_is_the_clients_400(self):
        """A JSON string or number where the panel sends an object is neither the database nor a save that
        failed: it is the body family's 400 (`api_body.BodyMismatch`), nothing is saved, no signal, no ERROR."""
        from app.core import api_body
        from app.modules.chat import voice, voice_router
        from app.patient_web import branding, branding_router
        admin = {"usuario": "admin", "rol": "admin"}
        for raw in (b"123", b'"x"'):
            with mock.patch.object(voice, "save_model") as save, self.assertNoLogs(level="ERROR"):
                with self.assertRaises(api_body.BodyMismatch, msg=f"chat-voice {raw!r}"):
                    self._run(voice_router.admin_chat_voice, self._post(raw), identity=admin)
            save.assert_not_called()
            with mock.patch.object(branding, "save_color") as save, self.assertNoLogs(level="ERROR"):
                with self.assertRaises(api_body.BodyMismatch, msg=f"branding {raw!r}"):
                    self._run(branding_router._branding_state, self._post(raw, "/api/admin/branding"),
                              identity=admin)
            save.assert_not_called()
        self.signal.assert_not_called()

    def test_chat_voice_model(self):
        from app.modules.chat import voice, voice_router

        def answer(exc):
            with mock.patch.object(voice, "save_model", side_effect=exc):
                response = self._run(voice_router.admin_chat_voice, self._post(b'{"model": "fal-ai/wizper"}'),
                                     identity={"usuario": "admin", "rol": "admin"})
            return response.status_code, json.loads(bytes(response.body))
        self._check(answer, (503, {"error": "base de datos no disponible"}),
                    (500, {"error": "no se pudo guardar, probá de nuevo"}), commit_answer=(503, _COMMIT_UNKNOWN))

    def test_brand_colour(self):
        from app.patient_web import branding

        def answer(exc):
            with mock.patch.object(branding, "save_color", side_effect=exc):
                with self.assertRaises(branding.BrandingError) as caught:
                    branding.apply_color({"color": "#123456"})
            return caught.exception.status, caught.exception.body
        self._check(answer, (503, {"error": "base de datos no disponible"}),
                    (500, {"error": "no se pudo guardar, probá de nuevo"}))

    def test_mcp_tool_call_token(self):
        from app.modules.chat import mcp_api
        raw = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                          "params": {"name": "search_services", "arguments": {}}}).encode("utf-8")

        def answer(exc):
            with mock.patch.object(mcp_api, "_usuario_por_token", side_effect=exc):
                result = mcp_api.process_rpc_body({"Authorization": "Bearer tok-123"}, raw)
            return result.status, json.loads(result.body)
        self._check(answer,
                    (503, {"jsonrpc": "2.0", "id": 7,
                           "error": {"code": -32000, "message": "servicio no disponible, probá en un rato"}}),
                    (500, {"jsonrpc": "2.0", "id": 7,
                           "error": {"code": -32603, "message": "error interno del servidor de turnos"}}))

    def test_mcp_account_token(self):
        from app.core import db_accounts
        from app.modules.chat import mcp_api

        def answer(exc):
            with mock.patch.object(db_accounts, "mcp_token_issue", side_effect=exc):
                result = mcp_api._cuenta_mcp({"dni": "30111222"}, "https://turnos.example", False)
            return result.status, json.loads(result.body)
        self._check(answer, (503, {"error": "base de datos no disponible"}),
                    (500, {"error": "error interno del servidor de turnos"}))

    def test_staff_logout(self):
        from app.auth import admin_auth

        def answer(exc):
            with mock.patch.object(admin_auth.staff_portal, "enabled", return_value=False), \
                    mock.patch.object(admin_auth, "_parse_cookie_token", return_value="tok"), \
                    mock.patch.object(admin_auth, "revoke_session", side_effect=exc):
                result = admin_auth.logout_result({"Cookie": "x=tok"})
            self.assertTrue(result.set_cookie, "the cookie is cleared whatever happened")
            return result.status, result.body
        self._check(answer, (503, {"ok": False, "error": "base_datos", "revoked": False}),
                    (500, {"ok": False, "error": "cierre_no_registrado", "revoked": False}))

    def test_passkey_counter(self):
        from app.auth import admin_auth, passkeys
        from app.core import db_auth
        verified = mock.Mock(new_sign_count=5)
        adapter = mock.Mock()
        adapter.verify_authentication.return_value = verified

        def answer(exc):
            with mock.patch.object(admin_auth.staff_portal, "enabled", return_value=False), \
                    mock.patch.object(admin_auth, "_throttled", return_value=False), \
                    mock.patch.object(passkeys, "_validated_reto_wire", return_value="reto"), \
                    mock.patch.object(db_auth, "passkey_by_credential",
                                      return_value={"usuario": "ana", "public_key": b"k", "sign_count": 1}), \
                    mock.patch.object(passkeys, "_webauthn_adapter", return_value=adapter), \
                    mock.patch.object(passkeys, "_authentication_credential", return_value={}), \
                    mock.patch.object(passkeys, "b64u_dec", return_value=b"c"), \
                    mock.patch.object(passkeys, "_consume_reto", return_value=True), \
                    mock.patch.object(passkeys, "_rol_de", return_value="admin"), \
                    mock.patch.object(db_auth, "passkey_mark_use", side_effect=exc):
                result = passkeys.login_result({"Host": "turnos.example"}, "127.0.0.1",
                                               b'{"challenge": "c", "id": "cred"}')
            return result.status, result.body
        self._check(answer, (503, {"error": "base_no_disponible"}), (500, {"error": "contador_no_guardado"}))

    def test_the_honest_errors_are_not_503s(self):
        # a 500 is the place's own error: the inventory above holds no exception for it
        found, _ = scan(ROOT)
        for key in (("app/modules/chat/voice_router.py", "admin_chat_voice"), ("app/patient_web/branding.py", "apply_color"),
                    ("app/modules/chat/mcp_api.py", "_cuenta_mcp"), ("app/auth/admin_auth.py", "logout_result"),
                    ("app/auth/passkeys.py", "login_result")):
            self.assertNotIn(key, found)


class TestTheConnectionExit(unittest.TestCase):
    """The product owner's hub review, 2026-11-10: a Postgres restart or a dropped connection under a statement raises psycopg's
    `OperationalError` (`AdminShutdown`, `CannotConnectNow`, no SQLSTATE at all for a closed socket). The pooled
    `with` of `db._connect()` — the exit every statement of the app leaves through — turns it into
    `db.DbConnectionLost`, a `DbUnavailable`; an error the database answered with (a deadlock, a serialization
    failure, a program limit) stays what it is."""

    def setUp(self):
        try:
            import psycopg
            from psycopg import errors
        except Exception:  # noqa: BLE001
            self.skipTest("psycopg is not installed")
        self.psycopg, self.errors = psycopg, errors

    def _raised(self, exc, on_commit=False):
        try:
            _in_a_pooled_connection(exc, on_commit=on_commit)()
        except Exception as got:  # noqa: BLE001
            return got
        self.fail("nothing raised")

    def test_a_database_that_stops_answering_is_unavailable(self):
        from app.core import db, deps
        e = self.errors
        for exc in (self.psycopg.OperationalError("server closed the connection unexpectedly"),
                    e.AdminShutdown("x"), e.CrashShutdown("x"), e.CannotConnectNow("x"), e.DatabaseDropped("x"),
                    e.ConnectionFailure("x"), e.TooManyConnections("x"), e.DiskFull("x"), e.IoError("x")):
            for on_commit in (False, True):
                got = self._raised(exc, on_commit)
                self.assertIsInstance(got, db.DbConnectionLost, (type(exc).__name__, on_commit))
                self.assertIsInstance(got, deps.DB_UNAVAILABLE_ERRORS)
                self.assertIs(got.__cause__, exc)
                self.assertEqual(on_commit, isinstance(got, db.DbCommitUnknown), (type(exc).__name__, on_commit))
                said = "la base dejó de responder al confirmar (%s)" if on_commit else "la base dejó de responder (%s)"
                self.assertEqual(said % type(exc).__name__, str(got))

    def test_a_protocol_violation_is_the_clients_own_error(self):
        """The product owner's hub review, 2026-11-11: 08P01 is in the connection class, but it is the client sending what the
        protocol does not allow (a bug of ours), not a database that stopped answering: no 503, no signal."""
        from app.core import deps
        exc = self.errors.ProtocolViolation("invalid message format")
        self.assertEqual("08P01", exc.sqlstate)
        for on_commit in (False, True):
            got = self._raised(exc, on_commit)
            self.assertIs(got, exc, on_commit)
            self.assertNotIsInstance(got, deps.DB_UNAVAILABLE_ERRORS)

    def test_a_commit_that_broke_does_not_know_what_it_wrote(self):
        """The product owner's hub review, 2026-11-11: a connection lost under the commit itself leaves the write unknown — it may
        have landed. It is `DbCommitUnknown`: still a database that does not answer (503, `db_unavailable`), but the
        answer does not promise that asking again is safe (`TestTheAnswers`). A commit the database refused (a
        serialization failure) is known not to have written and stays what it is."""
        from app.core import db, deps
        lost = self._raised(self.psycopg.OperationalError("server closed the connection unexpectedly"), True)
        self.assertIsInstance(lost, db.DbCommitUnknown)
        self.assertIsInstance(lost, deps.DB_UNAVAILABLE_ERRORS)
        refused = self.errors.SerializationFailure("could not serialize access")
        self.assertIs(refused, self._raised(refused, True))
        timeout = self._raised(self.errors.QueryCanceled("x"), True)
        self.assertIsInstance(timeout, db.DbTimeout)
        self.assertNotIsInstance(timeout, db.DbCommitUnknown)

    def _rollback_fails(self, body_error, rollback_error):
        """The body raises `body_error`; the rollback that the pooled exit runs for it raises `rollback_error`."""
        from app.core import db
        pool, conn = mock.Mock(), mock.MagicMock()
        conn.__exit__.side_effect = rollback_error
        try:
            with db._PooledConn(pool, conn):
                raise body_error
        except Exception as got:  # noqa: BLE001
            pool.putconn.assert_called_once_with(conn)
            return got
        self.fail("nothing raised")

    def test_a_failed_rollback_keeps_the_error_of_the_body(self):
        """The product owner's hub review, 2026-11-11: the rollback after the body's own error failing too must not replace that
        error — the log has to show why the request failed. The body's error goes on; the rollback's is attached
        to it as a note naming its type."""
        rollback = self.psycopg.OperationalError("server closed the connection unexpectedly")
        body = ValueError("the body's own bug")
        got = self._rollback_fails(body, rollback)
        self.assertIs(got, body)
        self.assertIn("rollback failed too: OperationalError", getattr(got, "__notes__", []))

    def test_a_failed_rollback_after_a_lost_connection_is_still_that_outage(self):
        from app.core import db
        body = self.errors.AdminShutdown("terminating connection due to administrator command")
        got = self._rollback_fails(body, self.psycopg.OperationalError("the connection is lost"))
        self.assertIsInstance(got, db.DbConnectionLost)
        self.assertNotIsInstance(got, db.DbCommitUnknown)
        self.assertIs(got.__cause__, body)
        self.assertIn("rollback failed too: OperationalError", getattr(body, "__notes__", []))

    def test_the_limits_stay_timeouts(self):
        from app.core import db
        for exc in (self.errors.QueryCanceled("x"), self.errors.LockNotAvailable("x")):
            self.assertIsInstance(self._raised(exc), db.DbTimeout)

    def test_what_the_database_answered_is_not_an_outage(self):
        from app.core import deps
        e = self.errors
        for exc in (e.DeadlockDetected("x"), e.SerializationFailure("x"), e.TransactionRollback("x"),
                    e.ProgramLimitExceeded("x"), e.ObjectInUse("x"), e.UniqueViolation("x"), ValueError("x")):
            got = self._raised(exc)
            self.assertIs(got, exc, type(exc).__name__)
            self.assertNotIsInstance(got, deps.DB_UNAVAILABLE_ERRORS)

    def test_the_connection_goes_back_to_the_pool_whatever_happened(self):
        from app.core import db
        for exc in (self.psycopg.OperationalError("x"), self.errors.DeadlockDetected("x")):
            pool, conn = mock.Mock(), mock.MagicMock()
            with self.assertRaises(Exception):
                with db._PooledConn(pool, conn):
                    raise exc
            pool.putconn.assert_called_once_with(conn)


class TestTheAccessLogRefusal(unittest.TestCase):
    """The product owner's hub review, 2026-11-10: a clinical read whose access row cannot be written is refused, but only a
    database that does not answer is `AccessLogUnavailable` (a `DbUnavailable`: 503 and `db_unavailable`); any
    other failure of the insert is `AccessLogFailed`, the server's own error, with the exception type alone."""

    _WHO = {"usuario": "ana", "rol": "admin"}

    def _record(self, exc):
        from app.core import access_log, db_clinical
        with mock.patch.object(db_clinical, "insert_clinical_access", side_effect=exc):
            access_log.record(self._WHO, "ficha", "30111222")

    def _record_many(self, exc):
        from app.core import access_log, db_clinical
        with mock.patch.object(db_clinical, "insert_clinical_access_many", side_effect=exc):
            access_log.record_many(self._WHO, "alertas", [("30111222", "refusal-%s" % id(exc))])

    def test_a_database_that_does_not_answer_refuses_as_unavailable(self):
        from app.core import access_log, db
        for write in (self._record, self._record_many):
            for exc in [db.DbUnavailable("pool")] + _lost_connections():
                with self.assertRaises(access_log.AccessLogUnavailable):
                    write(exc)

    def test_any_other_failure_refuses_as_the_server_s_own_error(self):
        from app.core import access_log, deps
        for write in (self._record, self._record_many):
            for exc in [RuntimeError(_PERSONAL), ValueError("bad row")] + _answered_errors():
                with self.assertRaises(Exception) as caught:
                    write(exc)
                self.assertNotIsInstance(caught.exception, deps.DB_UNAVAILABLE_ERRORS)
                self.assertEqual("AccessLogFailed", type(caught.exception).__name__)
                self.assertNotIn("30111222", str(caught.exception))


class TestTheSignalLeaves(unittest.TestCase):
    def test_one_signal_per_window_whoever_answers(self):
        from app.core import deps, launch_signals
        now = [5000.0]
        deps._db_signal_last.clear()
        self.addCleanup(deps._db_signal_last.clear)
        with mock.patch.object(deps, "_clock", lambda: now[0]), \
                mock.patch.object(launch_signals, "handled") as handled:
            deps.answer_db_unavailable()
            deps.answer_db_unavailable(lambda status: (status, {}))
            _join_signal_threads()
            self.assertEqual([("db_unavailable",)], [c.args for c in handled.call_args_list])
            now[0] += deps._DB_UNAVAILABLE_SIGNAL_EVERY_S
            json.loads(bytes(deps.answer_db_unavailable().body))
            _join_signal_threads()
        self.assertEqual([("db_unavailable",)] * 2, [c.args for c in handled.call_args_list])


if __name__ == "__main__":
    unittest.main()
