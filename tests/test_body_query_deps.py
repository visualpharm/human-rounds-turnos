"""test_body_query_deps.py — routes take their body and query through their signature (TASK-76, items E and G).

A route declares what it reads from the request: the JSON body as a dependency of its signature
(`Depends(api_body.body(CONTRACT, BodyIn))` and its siblings in `core/api_body.py`), the query parameters as
typed `Query()` parameters. FastAPI then resolves them in the order of the signature, after whatever
must answer first — the gate, a path that names no item (`core.deps.EarlyResponse`).

This test reads the shipped Python and lists every function that still reads the body or the query by
hand: a call of one of `core.api_body`'s readers or of `core.native_gate.read_json_body` (however it was
imported), `request.body()` / `.json()` / `.stream()` / `.form()`, `request.query_params`,
`request.url.query` or the ASGI `query_string`. Nested functions count on their own, an `if
request.method == ...` branch counts like any other line, and a reader handed to `run_in_threadpool`
without being called counts too. `core/api_body.py` is the one place that reads a body.

What stays by hand is the list below, each with why. A function that is not listed may not read by hand
at all; a listed function that no longer does must leave the list.
"""
import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SKIP_TOP = {"tests", "alembic", "node_modules", "vendor", "static", "stories", "tools", "__pycache__"}
_READER_MODULE = "app/core/api_body.py"
# The readers a handler used to call: `core.api_body`'s and the native routers' JSON helper.
_READERS = {"read_model", "read_body", "lazy_model", "observe", "checked", "watch", "read_json_body"}
_READER_HOMES = {"app.core.api_body", "app.core.native_gate", "app.admin_panel._native_support",
                 "app.modules.intake._native_support"}
_BODY_METHODS = {"body", "json", "stream", "form"}
_REQUEST_NAMES = {"request", "req", "http_request"}

_PARSE_QS = ("the domain takes the `parse_qs` mapping of the query: the FIRST non-blank value of a repeated "
             "parameter, where a typed `str` parameter takes the last — the answer to a repeated parameter would "
             "change; documented by `api_body.query_docs`")
_QUERY_TEXT = "the domain takes the whole query string as text, with its own parsing of it"
_LANG = ("the language switch: `?lang=` answers a 302 to the same path, the rest of the query kept verbatim "
         "(`core.i18n.lang_switch_redirect`)")
_ONLY_NEXT = ("a post-sign-in redirect boundary: exactly one `next` and nothing else is a continuation, a duplicate "
              "or any other parameter is not — it reads every pair, which typed parameters cannot say")
_SIZE_CAP = "its size cap answers 413 before the body is read, so the body cannot be read by a dependency ahead of it"
_BOUNDED = ("a bounded reader: it streams the body and stops at its cap, answering on its own (413, its own 400 or "
            "422) before the whole body is in memory")

# function ("<path>:<qualname>") -> why it still reads the request by hand
STAYS_BY_HAND = {
    # ---- the query, by hand
    "app/admin_panel/admin_appointments_router.py:native_appointment_slots": _PARSE_QS,
    "app/admin_panel/admin_patients_router.py:native_patients_collection": _PARSE_QS,
    "app/admin_panel/reports_router.py:reports_cost_recovery": _PARSE_QS + " (`reports._days_param`)",
    "app/admin_panel/reports_router.py:reports_cost_recovery_csv": _PARSE_QS + " (`reports._days_param`)",
    "app/admin_panel/reports_router.py:reports_login_panel": _PARSE_QS + " (`reports._days_param`)",
    "app/admin_panel/reports_router.py:reports_management": _PARSE_QS + " (`reports._days_param`)",
    "app/admin_panel/reports_router.py:reports_patient_path": _PARSE_QS + " (`reports_funnel.params`)",
    "app/admin_panel/reports_router.py:reports_patients": _PARSE_QS + " (`reports._days_param`)",
    "app/admin_panel/reports_router.py:reports_requests_csv": _PARSE_QS + " (`reports._days_param`)",
    "app/admin_panel/reports_router.py:reports_searches": _PARSE_QS + " (`reports._days_param`)",
    "app/admin_panel/reports_router.py:reports_statistics": _PARSE_QS + " (`reports._month_param`)",
    "app/admin_panel/reports_router.py:reports_statistics_csv": _PARSE_QS + " (`reports._month_param`)",
    "app/admin_panel/reports_router.py:reports_sumar": _PARSE_QS + " (`reports._days_param`)",
    "app/admin_panel/reports_router.py:reports_sumar_csv": _PARSE_QS + " (`reports._days_param`)",
    "app/admin_panel/reports_router.py:reports_visits": _PARSE_QS + " (`reports._days_param`)",
    "app/modules/chat/mcp_oauth_router.py:authorize": "the OAuth authorize request: the whole parameter mapping goes to "
                                                      "`mcp_oauth.check_authorize`, which validates each pair itself",
    "app/modules/turnos/analytics_api_router.py:_events": "the export's own cursor and filter validation, answering "
                                                          "its own 400 for each unknown or malformed parameter",
    "app/modules/turnos/analytics_api_router.py:_schedule": "the export's own cursor and window validation, answering "
                                                            "its own 400 for each unknown or malformed parameter",
    "app/modules/turnos/analytics_api_router.py:keys_revoke": "one `id` the handler parses itself, answering its own 400 "
                                                              "(documented by `api_body.query_docs`)",
    "app/modules/turnos/booking_requests_router.py:_qs_like_legacy": _PARSE_QS + " (the queue and REFEPS)",
    "app/modules/turnos/preparation_router.py:_upload_order": _PARSE_QS,
    "app/patient_web/patient_site_router.py:_api_service_slots": _PARSE_QS,
    "app/cuentas/auth_renaper_router.py:get_auth_renaper": _PARSE_QS + " (the identity provider's redirect)",
    "app/cuentas/auth_renaper_router.py:get_auth_renaper_callback": _PARSE_QS + " (the identity provider's callback)",
    "app/modules/chat/whatsapp_chat_router.py:meta_handshake": "Meta's webhook handshake (`hub.mode`, `hub.challenge`, "
                                                          "...): a protocol of its own, " + _PARSE_QS,
    "app/modules/chat/whatsapp_chat_router.py:incoming_webhook": "the webhook's shared secret may come in the query "
                                                            "(WAHA); a protocol of its own, " + _PARSE_QS,
    "app/patient_web/patient_site_router.py:_service_page": _QUERY_TEXT + " (the wizard's steps and title)",
    "app/modules/turnos/appointment_confirm_router.py:confirm_page": _QUERY_TEXT + " (the reminder's answer link)",
    "app/modules/intake/interview_patient_router.py:_entrevista_page": "`embed=1` anywhere in the query string turns "
                                                                  "on the embedded view (a substring test)",
    "app/core/legacy_urls_router.py:_make_literal_endpoint._endpoint": "an old URL redirects with its query kept verbatim",
    "app/core/legacy_urls_router.py:_make_prefix_endpoint._endpoint": "an old URL redirects with its query kept verbatim",
    "app/core/deps.py:render_screen_gate_error": "an exception handler, not a route: the login redirect keeps the "
                                             "screen's validated query as its `next`",
    "app/core/host_redirect_middleware.py:_request_target": "ASGI middleware, not a route: the canonical-host "
                                                        "redirect keeps the query",
    "app/routers/native_terminal.py:terminal": "the terminal answer of a path no route serves: it keeps the query of "
                                           "the static fallback",
    "app/cuentas/test_portal.py:TestPortalGateway._portal_page": "the in-process test portal, an ASGI app of its own "
                                                            "that imitates the municipal portal's query protocol",
    "app/core/site_identity_router.py:_maybe_lang_switch": _LANG,
    "app/cuentas/accounts_router.py:lang_switch_response": _LANG,
    "app/patient_web/health_data_router.py:data_response": _LANG,
    "app/patient_web/patient_site_router.py:_prelude": _LANG,
    "app/admin_panel/admin_login_screen.py:login_screen": _ONLY_NEXT,
    "app/admin_panel/admin_root_screen.py:_requested_next": _ONLY_NEXT,
    # ---- the body, by hand
    "app/admin_panel/admin_account_router.py:post_account_2fa_start": "the body is optional (no body or not JSON means "
                                                                      "no payload), after the gated identity",
    "app/ai/lab_results_router.py:account_lab_results_upload": "a multipart upload: its content-length cap answers "
                                                               "411/413 before the form is read, then max_files=1",
    "app/modules/chat/mcp_oauth_router.py:_body": _BOUNDED + " (the OAuth token and consent forms)",
    "app/admin_panel/client_errors_router.py:_client_errors": _SIZE_CAP,
    "app/admin_panel/table_export_router.py:_table_export": _SIZE_CAP,
    "app/core/native_gate.py:read_json_body": "the JSON helper the two size-capped routes above call after their cap",
    "app/admin_panel/admin_content_router.py:_doctors_import": _BOUNDED + " (a file, not JSON)",
    "app/modules/turnos/study_router.py:body": _BOUNDED + "; the routes take it as a dependency (`study_body`)",
    "app/patient_web/doctor_photos_router.py:_read_json_lenient_or_400": _BOUNDED,
    "app/patient_web/doctor_photos_router.py:_admin_photo_item": "the photo's own bounded reader (above), after the "
                                                            "doctor's ownership check of its handler",
    "app/patient_web/doctor_photos_router.py:_mine_photo": "the photo's own bounded reader (above), after the "
                                                      "session's doctor lookup of its handler",
    "app/modules/chat/chat_api_router.py:_bounded_body": _BOUNDED + " (a chat turn, streamed or not)",
    "app/modules/chat/chat_api_router.py:_watch": "the chat turn's model check on what the bounded reader decoded",
    "app/modules/chat/chat_api_router.py:send_message": "the chat turn: its bytes go to the typed turn input "
                                                    "(`_turn_input`) the streamed route shares, which decodes them",
    "app/modules/chat/mcp_api_router.py:_bounded_body": _BOUNDED + " (the MCP protocol's JSON-RPC)",
    "app/modules/chat/whatsapp_chat_router.py:_bounded_body": _BOUNDED + " (the WhatsApp webhook; the signature is "
                                                                     "checked on the raw bytes)",
    "app/modules/chat/voice_router.py:transcribe": "its rate limit answers 429 first, and a body that does not decode "
                                               "gets its own 400 texts",
    "app/ops/setup_router.py:_form_first_values": "the first-run installer posts an HTML form, not JSON",
    "app/ops/setup_router.py:_form_multi_values": "the first-run installer posts an HTML form, not JSON",
    "app/admin_panel/ai_register_screen.py:record_notification": "decodes with Starlette's `request.json()` inside the "
                                                            "try whose except is its own 400 notice",
    "app/admin_panel/admin_usability_screen.py:update": "a typed path parameter (`finding_id: int`): FastAPI validates "
                                                    "it after the dependencies, so a body dependency would answer a "
                                                    "malformed body ahead of the 422 of a malformed id",
}


# endpoint ("<module>:<qualname>") -> what its handler checks before it decodes its `api_body.deferred()` body
DECODED_IN_THE_HANDLER = {
    "app.admin_panel.admin_patients_router:native_patients_collection":
        "a doctor's session gets 403 on POST alone (GET lets it in), before the body",
    "app.admin_panel.admin_content_router:_content_root":
        "the content type's 404, then a gate whose flags depend on the type and the method",
    "app.admin_panel.admin_content_router:_content_item":
        "the content type's 404, then a gate whose flags depend on the type and the method",
    "app.admin_panel.admin_content_router:_content_sub_action":
        "the sub-action's 404 and the gate; the golden-set run on the same route takes no body",
    "app.modules.intake.interview_patient_router:_qr_start": "the door code's shape (404) and the per-IP limit (429)",
    "app.modules.intake.interview_patient_router:_message":
        "the token's shape, the per-IP limit, the interview row (404/503) and its state (409)",
    "app.modules.intake.interview_patient_router:_case_message": "the case chat's own guard (token, session, state)",
    "app.modules.turnos.booking_requests_router:api_requests":
        "the 401 of a visitor without a session, on POST alone (GET answers an anonymous visitor)",
    "app.modules.turnos.booking_requests_router:api_request_coverage":
        "the request id's shape, the session and the 16 KB cap on the bytes",
}

# endpoint -> what its handler checks before it reads its `api_body.lazy()` body (the domain decodes it)
LOADED_IN_THE_HANDLER = {
    "app.modules.turnos.booking_requests_router:api_request_reprogramar": "the request id's shape (404), the session",
    "app.modules.turnos.booking_requests_router:api_request_waitlist": "the request id's shape (404), the session",
    "app.modules.turnos.booking_requests_router:api_analyze_order": "the client's address, for the domain's limits",
    "app.modules.turnos.booking_requests_router:api_admin_request_assign": "nothing: the gate and the row are dependencies",
    "app.modules.turnos.booking_requests_router:api_admin_request_attendance":
        "the request id's shape (404) and the staff gate (401/403)",
}


def _imported_readers(tree):
    """Local names of the readers (`from app.core.api_body import read_model as rm`) and of the modules that hold
    them (`from app.core import api_body as ab`), from imports anywhere in the file (function-level ones too)."""
    names, modules = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                full = f"{node.module}.{alias.name}"
                if node.module in _READER_HOMES and alias.name in _READERS:
                    names.add(alias.asname or alias.name)
                elif full in _READER_HOMES:
                    modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _READER_HOMES:
                    modules.add(alias.asname or alias.name)
    return names, modules


def _is_request_annotation(annotation):
    for node in ast.walk(annotation) if annotation is not None else ():
        if isinstance(node, ast.Name) and node.id == "Request":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "Request":
            return True
    return False


class _Scan(ast.NodeVisitor):
    def __init__(self, path, tree):
        self.path = path
        self.names, self.modules = _imported_readers(tree)
        self.stack = []        # qualname parts
        self.requests = [set(_REQUEST_NAMES)]
        self.found = {}        # "<path>:<qualname>" -> sorted kinds

    # ---- scopes
    def _function(self, node):
        own = {a.arg for a in node.args.posonlyargs + node.args.args + node.args.kwonlyargs
               if _is_request_annotation(a.annotation)}
        self.stack.append(node.name)
        self.requests.append(self.requests[-1] | own)
        for statement in node.body:   # the body only: a signature's `Depends(...)` is the declared way
            self.visit(statement)
        self.requests.pop()
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = _function

    def visit_Lambda(self, node):
        self.visit(node.body)

    def visit_ClassDef(self, node):
        self.stack.append(node.name)
        for statement in node.body:
            self.visit(statement)
        self.stack.pop()

    def _hit(self, kind):
        if self.stack:
            key = f"{self.path}:{'.'.join(self.stack)}"
            self.found.setdefault(key, set()).add(kind)

    def _is_request(self, node):
        return isinstance(node, ast.Name) and node.id in self.requests[-1]

    # ---- reads
    def visit_Attribute(self, node):
        if node.attr in _READERS and isinstance(node.value, ast.Name) and node.value.id in self.modules:
            self._hit("body reader")
        elif node.attr in _BODY_METHODS and self._is_request(node.value):
            self._hit(f"request.{node.attr}")
        elif node.attr == "query_params" and self._is_request(node.value):
            self._hit("query_params")
        elif node.attr == "query" and isinstance(node.value, ast.Attribute) and node.value.attr == "url":
            self._hit("url.query")
        self.generic_visit(node)

    def visit_Name(self, node):
        if node.id in self.names:
            self._hit("body reader")

    def visit_Constant(self, node):
        if node.value == "query_string":
            self._hit("query_string")


def manual_reads(root):
    """{"<path>:<qualname>": [kinds]} for the shipped Python of `root`."""
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        top = rel_dir == "."
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and not (top and d in _SKIP_TOP)
                       and d not in ("node_modules", "__pycache__")]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if rel == _READER_MODULE:
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    tree = ast.parse(f.read(), path)
            except (SyntaxError, UnicodeDecodeError):
                continue
            scan = _Scan(rel, tree)
            scan.visit(tree)
            found.update({key: sorted(kinds) for key, kinds in scan.found.items()})
    return found


class TestBodyAndQueryThroughTheSignature(unittest.TestCase):
    def test_nothing_reads_the_request_by_hand_outside_the_list(self):
        found = manual_reads(ROOT)
        unlisted = {key: kinds for key, kinds in sorted(found.items()) if key not in STAYS_BY_HAND}
        self.assertEqual({}, unlisted, "these read the body or the query by hand: declare it in the signature "
                         "(core/api_body.py dependencies, typed Query() parameters), or list the function in "
                         "STAYS_BY_HAND with why")

    def test_every_listed_function_still_reads_by_hand(self):
        found = manual_reads(ROOT)
        self.assertEqual([], sorted(set(STAYS_BY_HAND) - set(found)),
                         "no longer reads by hand: remove it from STAYS_BY_HAND")

    def test_every_entry_says_why(self):
        self.assertEqual([], [key for key, why in STAYS_BY_HAND.items() if len(why.strip()) < 20])


def _routes(routes):
    from fastapi.routing import APIRoute
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            yield from _routes(inner.routes)
        elif isinstance(route, APIRoute):
            yield route


def body_kinds():
    """{"<module>:<qualname>" of an endpoint: {the `core.api_body` dependency kinds it declares}}."""
    from app.main import app
    out = {}
    for route in _routes(app.routes):
        stack, kinds = [route.dependant], set()
        while stack:
            dependant = stack.pop()
            kind = getattr(dependant.call, "body_kind", None)
            if kind:
                kinds.add(kind)
            stack += dependant.dependencies
        if kinds:
            out[f"{route.endpoint.__module__}:{route.endpoint.__qualname__}"] = kinds
    return out


class TestTheBodyIsDeclared(unittest.TestCase):
    def test_every_route_that_decodes_in_its_handler_says_why(self):
        deferred = sorted(key for key, kinds in body_kinds().items() if "deferred" in kinds)
        self.assertEqual(sorted(DECODED_IN_THE_HANDLER), deferred)

    def test_every_route_that_reads_a_lazy_body_in_its_handler_says_why(self):
        lazy = sorted(key for key, kinds in body_kinds().items() if "lazy" in kinds)
        self.assertEqual(sorted(LOADED_IN_THE_HANDLER), lazy)


class TestTheScanSeesThrough(unittest.TestCase):
    """The scan itself, on small sources: aliases, local imports, closures, branches, references."""

    def _scan(self, source):
        tree = ast.parse(source)
        scan = _Scan("m.py", tree)
        scan.visit(tree)
        return {key: sorted(kinds) for key, kinds in scan.found.items()}

    def test_aliases_and_local_imports(self):
        found = self._scan(
            "async def a(request):\n"
            "    from app.core import api_body as ab\n"
            "    return await ab.read_model(request, C, M)\n"
            "async def b(request):\n"
            "    from app.core.api_body import read_model as rm\n"
            "    return await rm(request, C, M)\n"
            "async def c(request):\n"
            "    from app.core.native_gate import read_json_body\n"
            "    return await read_json_body(request)\n")
        self.assertEqual({"m.py:a", "m.py:b", "m.py:c"}, set(found))

    def test_branches_closures_and_references(self):
        found = self._scan(
            "from starlette.concurrency import run_in_threadpool\n"
            "from fastapi import Request\n"
            "async def a(r: Request):\n"
            "    if r.method == 'POST':\n"
            "        return await r.json()\n"
            "async def b(request):\n"
            "    async def inner():\n"
            "        return await request.body()\n"
            "    return await inner()\n"
            "async def c(request):\n"
            "    return await run_in_threadpool(parse, request.query_params)\n"
            "async def d(request):\n"
            "    return request.url.query, request.scope.get('query_string')\n")
        self.assertEqual({"m.py:a": ["request.json"], "m.py:b.inner": ["request.body"],
                          "m.py:c": ["query_params"], "m.py:d": ["query_string", "url.query"]}, found)

    def test_the_signature_is_the_declared_way(self):
        found = self._scan(
            "from app.core import api_body\n"
            "async def a(request, body=Depends(api_body.body(C, M))):\n"
            "    return body.value\n")
        self.assertEqual({}, found)


if __name__ == "__main__":
    unittest.main()
