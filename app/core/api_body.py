"""api_body.py — the one place a native JSON route reads its request body
(TASK-24: Pydantic models at the routes' edge).

Every JSON route used to carry its own copy of the same few lines: read the
raw body, `json.loads(raw.decode("utf-8")) if raw else {}`, and on failure
answer 400 with its family's flat `{"error": "JSON inválido…"}`. The copies
agree on the decoding and differ only in the answer — with or without the
decoder's reason, a trailing period, through i18n, gzip or not, a `Result`
or a `json_response` — so the answer travels as a `BodyContract` and the
decoding lives here once.

Why not a FastAPI body parameter (`body: Model` in the signature): FastAPI
would read and validate the body BEFORE the endpoint runs, i.e. before the
route's own id-format and session checks — an unauthenticated request with
a malformed body would get a body error instead of its 401. It would also
decode differently (`request.json()` accepts a UTF-8 BOM and UTF-16, skips
non-JSON content types, and rejects an empty body). A route declares its body
as a DEPENDENCY instead (`body()` and its siblings below, TASK-76 item E):
FastAPI resolves dependencies in the order of the signature, so the gate and
every check that answers first are dependencies declared ahead of it
(`core.deps.EarlyResponse` for a check with an answer of its own), and the
body decodes exactly where the route used to call `json.loads`. Where the
checks need the handler (the request row it loaded, a rate limit it spent),
the dependency only reads the bytes and the handler decodes them
(`deferred()`); `tests/test_body_query_deps.py` lists those routes and why.

The Pydantic model of a route (`BodyModel`: strict JSON types, unknown
fields allowed — they were always ignored) does NOT replace what the
domain receives: `read_model()` hands the route the decoded value
untouched. A body that does not match the model is logged once per route
and field (`body_contract_drift`, field path and error type only, never a
value) and, with `STRICT` on (TASK-76 item F, the strict boundary), is
answered like a body that does not decode: at the same point, 400 with the
family's `{"error": "JSON inválido: campo <campo>: <qué se esperaba>"}`
(`BodyMismatch`), and the domain never sees it. A route without a family
contract (`watch()`, `watched()`, `raw(None, …)`) answers every body
itself and only observes. `STRICT = False` is the rollback: the models
observe again, the domain answers a mismatch as it always did, and the one
answer the models changed before — a body that does NOT match the model
and makes the domain crash (`run()`) is the family's 400, not a bare 500
«Internal Server Error» (hospital's decision, 26.10: no 500 on garbage
input) — stays. A crash on a body that matches the model is a real bug and
still propagates.

A body that does not decode raises `LegacyBodyError` and a crash on a
mismatched body raises `BodyMismatch`, both `RequestValidationError`s;
`render_validation_error` (installed by `core.deps.install_gate_handlers`)
answers them with the route family's own bytes. Any OTHER
`RequestValidationError` — the stock 422 FastAPI gives a typed path
parameter or a multipart form today — keeps FastAPI's default answer
unchanged (tests/routes/test_validation_contract.py).

A client that hangs up before its body arrives (`ClientDisconnect` out of
`request.body()`) is not a failure of ours: `render_client_gone` answers 499
into the void, so there is no 500 and nothing for Sentry (2026-10-28, a
`POST /api/account/update` per dropped phone connection).

Two domains read the body themselves and keep doing so, because WHEN they
read it is part of their answer: one that decodes only after its own
cheaper checks (rate limits first — reschedule, waitlist, the referral
analysis) gets a `LazyBody` (`lazy()`, its bytes read by the handler where
they always were), a callable that decodes exactly as before and checks
the model when the domain calls it; one that
takes the raw bytes (slot hold, search log) gets them as before (`raw()`), and
`observe()` checks the model on a copy.

The models also document the API: `docs(post=(BodyIn, Out))` is the
`openapi_extra` of a route, read by `routers/api_docs.py` per method (one
route object can serve GET and POST with different bodies). A body that is
not JSON (a file) is a `RawBody` there. The query parameters a route reads
are typed parameters of the signature (`days: str | None = Query(None,
description=...)`, TASK-76 item G): declared as text, so FastAPI never
refuses one, and parsed as leniently as before (a value the route cannot use
falls back to its default, never a 422). `query_docs(get={"days": Query(...)})`
still documents a parameter the signature cannot describe as it is: one of a
route object serving several methods (FastAPI would list it on every method),
or a number read leniently from text — the signature then declares it with
`include_in_schema=False`.
"""

import json
import logging

from fastapi import HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.responses import Response

_log = logging.getLogger(__name__)

STRICT = True
"""The strict boundary (TASK-76 item F): a body that does not match its route's model is the family's 400
before the domain runs. `False` turns every model back into an observer (see the module docstring)."""


class BodyContract:
    """How one family of routes answers a request body it cannot read.

    ``message(reason)`` builds the ``error`` text from the decoder's
    exception or from a model mismatch's description, ``respond(request,
    payload, status)`` builds the response with the family's own builder
    (headers, gzip), ``catches`` is what the family's former ``except``
    caught — anything else still propagates (a 500, as before) — and
    ``decoder`` the family's own decoding when it is not `decode()` (the
    legal texts decode the raw bytes, BOM and UTF-16 included)."""

    __slots__ = ("message", "respond", "status", "catches", "decoder")

    def __init__(self, message, respond, status=400, catches=(Exception,), decoder=None):
        self.message = message
        self.respond = respond
        self.status = status
        self.catches = catches
        self.decoder = decoder

    def answer(self, request, reason):
        return self.respond(request, {"error": self.message(reason)}, self.status)


class BodyModel(BaseModel):
    """Base of every request-body model: JSON types exactly (no coercion of
    "5" into 5), unknown fields allowed — the routes always ignored them."""

    model_config = ConfigDict(strict=True, extra="allow")


class ResponseModel(BaseModel):
    """Base of every documented answer. Routes return ready `Response`s, so
    FastAPI never serializes through these; they document the API, and the
    golden tests check every pinned answer against them. Fields a model does
    not name are allowed (a new column of `users` is not a broken contract)."""

    model_config = ConfigDict(extra="allow")


class Body:
    """What `read_model()` hands the route: the decoded value the domain gets,
    untouched, and the model's errors (None when it matches)."""

    __slots__ = ("value", "errors", "contract", "route")

    def __init__(self, value, errors, contract, route):
        self.value = value
        self.errors = errors
        self.contract = contract
        self.route = route


NO_BODY = Body(None, None, None, None)
"""For a domain call on a method without a body (GET of a GET+POST route)."""


class LegacyBodyError(RequestValidationError):
    """The body did not decode; carries the family contract that answers it."""

    def __init__(self, contract, cause):
        super().__init__([{"type": "json_invalid", "loc": ("body",),
                           "msg": "JSON decode error", "input": {},
                           "ctx": {"error": str(cause)}}])
        self.contract = contract
        self.cause = cause


class BodyMismatch(RequestValidationError):
    """A body that does not match its model: refused (`STRICT`), or it made the domain crash."""

    def __init__(self, body):
        super().__init__([{k: e[k] for k in ("type", "loc", "msg")} for e in body.errors])
        self.body = body

    def __str__(self):
        # What a domain that reads its body itself puts after its own «JSON inválido: » (`LazyBody`).
        return mismatch_reason(self.body.errors)


def decode(raw):
    """The legacy `_read_json()` contract: an EMPTY body is `{}`, anything
    else must be UTF-8 JSON — decoded as text, so a BOM or UTF-16 fails the
    same way it always did. Any JSON value comes back as it is (a list, a
    string, null): what a route does with a non-object is the route's own
    business."""
    return json.loads(raw.decode("utf-8")) if raw else {}


def _decode(raw, contract):
    try:
        return (contract.decoder or decode)(raw)
    except contract.catches as exc:
        raise LegacyBodyError(contract, exc) from None


async def read_body(request, contract):
    """Read and decode the request body, or raise `LegacyBodyError`."""
    return _decode(await request.body(), contract)


_DRIFT_SEEN = set()
_DRIFT_SEEN_MAX = 2000


def _loc(err):
    """Field path of an error; list positions as `#` (bounded, and no value)."""
    return ".".join("#" if isinstance(p, int) else str(p) for p in err["loc"])


def _route(request):
    return getattr(request.scope.get("route"), "path", None) or request.url.path


def _check(model, value, route):
    """The model's errors for `value` (None when it matches), drift logged."""
    try:
        model.model_validate(value)
        return None
    except ValidationError as exc:
        errors = exc.errors(include_url=False, include_input=False, include_context=False)
    for err in errors:
        key = (route, _loc(err), err["type"])
        if key not in _DRIFT_SEEN and len(_DRIFT_SEEN) < _DRIFT_SEEN_MAX:
            _DRIFT_SEEN.add(key)
            _log.warning("body_contract_drift route=%s loc=%s type=%s",
                         route, key[1] or "<body>", err["type"])
    return errors


def _refuse(body):
    """With `STRICT`, a body that does not match its model is the family's 400 (`BodyMismatch`); a route
    without a family contract answers every body itself."""
    if STRICT and body.errors is not None and body.contract is not None:
        raise BodyMismatch(body)
    return body


def _model_body(value, contract, model, route):
    """`Body` for a decoded value: the model check, then `_refuse()`."""
    return _refuse(Body(value, _check(model, value, route), contract, route))


async def read_model(request, contract, model):
    """`read_body()` plus the check against `model` (see the module docstring)."""
    value = await read_body(request, contract)
    return _model_body(value, contract, model, _route(request))


class LazyBody(Body):
    """For a domain that decodes the body itself, once its own cheaper checks
    passed (rate limits first) and with its own answer to a body that does
    not decode: it gets this callable instead of a reader of its own.
    Calling it decodes like `decode()` — raising exactly what it always
    raised — and checks the model (`_refuse()`: a mismatch raises
    `BodyMismatch`, whose text is the reason the family's 400 gives)."""

    __slots__ = ("raw", "model")

    def __init__(self, raw, contract, model, route):
        super().__init__(None, None, contract, route)
        self.raw = raw
        self.model = model

    def __call__(self):
        self.value = decode(self.raw)
        self.errors = _check(self.model, self.value, self.route)
        _refuse(self)
        return self.value


async def lazy_model(request, contract, model):
    """`LazyBody` over the request body (read now, decoded when called)."""
    return LazyBody(await request.body(), contract, model, _route(request))


def checked(request, value, contract, model):
    """`Body` for a value the route decoded its own way (a route that reads a
    malformed body as `{}`): only the model check."""
    return _model_body(value, contract, model, _route(request))


def watch(request, value, model):
    """The model check alone, for a route that decodes the body its own way and
    answers every body itself — none makes its domain crash: drift is logged,
    `value` is not touched and nothing is answered differently."""
    _check(model, value, _route(request))


def observe(request, raw, contract, model):
    """For a domain that takes the raw bytes and decodes them itself: the model
    check on a copy, without touching what the domain gets (a body that does
    not decode is the domain's own answer, and no model check; one that
    decodes and does not match is refused, `_refuse()`)."""
    route = _route(request)
    try:
        value = decode(raw)
    except Exception:  # noqa: BLE001 — the domain answers it
        return Body(None, None, contract, route)
    return _model_body(value, contract, model, route)


# ---- the body as a dependency of the route -----------------------------------
#
# A route declares its body in its signature, `body: Annotated[api_body.Body, Depends(api_body.body(CONTRACT,
# BodyIn))]`, instead of calling a reader in its handler. FastAPI resolves a route's dependencies before the
# handler, in the order of the signature (after the route's own `dependencies=`), so whatever must answer
# before the body — the gate, a path that names no item, a closed entry (`core.deps.EarlyResponse`) — is a
# dependency declared ahead of it. Where the route's own checks need what only the handler has (the request
# row it loaded, a rate limit it spent), the body is `deferred()` or `lazy()`: the dependency reads nothing,
# and the handler reads the bytes with `await .read()` / `await .lazy()` at the point it always did — so a
# refusal ahead of that point still answers before a byte of the body arrives, and never buffers it.


class PendingBody:
    """The body of a route whose own checks answer before it is read: nothing is read until the handler asks.
    `await load()` reads the bytes (once); `await read()` loads and decodes them like `read_model()` —
    `LegacyBodyError` when they do not decode — and checks the model; `await lazy()` loads them into a
    `LazyBody` for a domain that decodes them itself."""

    __slots__ = ("request", "raw", "contract", "model", "route")

    def __init__(self, request, contract, model, route):
        self.request = request
        self.raw = None
        self.contract = contract
        self.model = model
        self.route = route

    async def load(self):
        if self.raw is None:
            self.raw = await self.request.body()
        return self.raw

    async def read(self):
        return _model_body(_decode(await self.load(), self.contract), self.contract, self.model, self.route)

    async def lazy(self):
        return LazyBody(await self.load(), self.contract, self.model, self.route)


class RawRead(Body):
    """`observe()` with the bytes it observed, for a domain that decodes them itself."""

    __slots__ = ("raw",)

    def __init__(self, raw, observed):
        super().__init__(observed.value, observed.errors, observed.contract, observed.route)
        self.raw = raw


def _for(methods, make, kind, *, reads=True):
    """The dependency: `make(request, raw)` on the methods that carry the body, `NO_BODY` on the others (one
    route object serving GET and POST — the route-object count is pinned); with `reads=False`,
    `make(request, None)` and the handler reads. `kind` names the factory, for the inventory of the routes
    that read in their handler (`tests/test_body_query_deps.py`)."""
    async def request_body(request: Request):
        if methods is not None and request.method not in methods:
            return NO_BODY
        return make(request, await request.body() if reads else None)
    request_body.body_kind = kind
    request_body.body_methods = None if methods is None else frozenset(methods)
    return request_body


def body(contract, model, *, methods=None):
    """The body, read and checked before the handler runs: a `Body` (see `read_model()`)."""
    return _for(methods, lambda request, raw_: _model_body(_decode(raw_, contract), contract, model, _route(request)),
                "body")


def deferred(contract, model, *, methods=None):
    """The body of a route whose own checks answer first: a `PendingBody`, read and decoded by the handler
    (`await pending.read()`)."""
    return _for(methods, lambda request, _raw: PendingBody(request, contract, model, _route(request)),
                "deferred", reads=False)


def lazy(contract, model, *, methods=None):
    """The body for a domain that decodes it itself after its own checks: a `PendingBody` the handler turns
    into the domain's `LazyBody` where it always read the bytes (`await pending.lazy()`, `lazy_model()`)."""
    return _for(methods, lambda request, _raw: PendingBody(request, contract, model, _route(request)),
                "lazy", reads=False)


def raw(contract, model, *, methods=None):
    """The bytes for a domain that takes them as they came: a `RawRead` (`observe()` on a copy)."""
    return _for(methods, lambda request, raw_: RawRead(raw_, observe(request, raw_, contract, model)), "raw")


def lenient(contract, model, decoder, *, methods=None):
    """The body of a route that reads one that does not decode its own way (as `{}`, say): `decoder(raw)`,
    then the model check (`checked()`). For a route where nothing answered before its reader did."""
    return _for(methods, lambda request, raw_: checked(request, decoder(raw_), contract, model), "lenient")


def watched(model, reader):
    """The body as `reader(request)` reads it — a family's own bounded reader, with its own answers — and
    the drift against `model` logged (`watch()`)."""
    async def request_body(request: Request):
        value = await reader(request)
        watch(request, value, model)
        return value
    request_body.body_kind = "watched"
    request_body.body_methods = None
    return request_body


# What a crash means by itself — the app's own answers, never a body error.
_NOT_A_CRASH = (HTTPException, RequestValidationError)


def _crash(body, exc, expected):
    """`BodyMismatch` for a crash on a body that does not match its model, else None."""
    from app.core import db
    if isinstance(exc, _NOT_A_CRASH) or body.errors is None \
            or isinstance(exc, (db.DbUnavailable,) + tuple(expected)):
        return None
    _log.warning("body_contract_reject route=%s error=%s", body.route, type(exc).__name__)
    return BodyMismatch(body)


def call(body, fn, *args, expected=(), **kwargs):
    """`run()` for a domain call the route makes on the event loop itself."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        mismatch = _crash(body, exc, expected)
        if mismatch is None:
            raise
        raise mismatch from exc


async def run(body, fn, *args, expected=(), **kwargs):
    """`run_in_threadpool(fn, *args, **kwargs)` for the domain call that gets
    the body: an exception it raises on a body that does not match the model
    becomes `BodyMismatch` (400 in the family's format); everything else —
    and `expected`, the exceptions the route answers itself — propagates as
    before."""
    try:
        return await run_in_threadpool(fn, *args, **kwargs)
    except Exception as exc:
        mismatch = _crash(body, exc, expected)
        if mismatch is None:
            raise
        raise mismatch from exc


_EXPECTED = {
    "model_type": "se esperaba un objeto",
    "dict_type": "se esperaba un objeto",
    "model_attributes_type": "se esperaba un objeto",
    "string_type": "se esperaba texto",
    "int_type": "se esperaba un número entero",
    "float_type": "se esperaba un número",
    "bool_type": "se esperaba true o false",
    "list_type": "se esperaba una lista",
    "missing": "falta",
}


def mismatch_reason(errors):
    """`campo email: se esperaba texto` — the first error, in Spanish, no value."""
    err = errors[0]
    expected = _EXPECTED.get(err["type"], "valor inválido")
    loc = _loc(err)
    return f"campo {loc}: {expected}" if loc else f"el cuerpo: {expected}"


async def render_validation_error(request, exc):
    if isinstance(exc, LegacyBodyError):
        return exc.contract.answer(request, exc.cause)
    if isinstance(exc, BodyMismatch):
        return exc.body.contract.answer(request, mismatch_reason(exc.body.errors))
    return await request_validation_exception_handler(request, exc)


async def render_client_gone(request, exc):
    """The client closed the connection before its body arrived (a phone that
    lost signal mid-POST, a tab closed on submit). Nobody is left to read an
    answer and nothing failed on our side: no 500, no error report. 499 only
    lands in the access log. The exception has no `status_code`, so Sentry's
    Starlette integration does not capture it either."""
    return Response(status_code=499)


def install(app) -> None:
    app.add_exception_handler(RequestValidationError, render_validation_error)
    # Every `await request.body()` / `request.stream()` of the app, not only
    # the readers above: one handler, so a route cannot forget it.
    app.add_exception_handler(ClientDisconnect, render_client_gone)


# ---- documentation ----------------------------------------------------------

_MODELS = {}


class ApiError(ResponseModel):
    """Every error the JSON API answers: one flat, human-readable `error`."""

    error: str


class OkOut(ResponseModel):
    """A done action with nothing more to say: `{"ok": true}`."""

    ok: bool


def _register(model):
    name = model.__name__
    known = _MODELS.setdefault(name, model)
    if known is not model:
        raise ValueError(f"two API models named {name}: {known.__module__}, {model.__module__}")
    return name


class RawBody:
    """A request body that is not JSON — a file sent as it is — for `docs()`:
    what it holds and the media types the route accepts."""

    __slots__ = ("description", "media_types")

    def __init__(self, description, *media_types):
        self.description = description
        self.media_types = media_types or ("application/octet-stream",)


def docs(**methods):
    """`openapi_extra` for a route: `docs(post=(BodyIn, Out), get=(None, ListOut))`
    — per method, the JSON body model (or a `RawBody`, or None) and the
    success answer model (or None), answered with 200; a third item names
    another success status: `post=(None, Out, 201)`, or
    `post=(BodyIn, None, 204)` for an answer with no body.
    `routers/api_docs.py` turns it into requestBody/responses."""
    spec = {}
    for method, (body, ok, *status) in methods.items():
        entry = {}
        if isinstance(body, RawBody):
            entry["raw"] = {"description": body.description, "media_types": list(body.media_types)}
        elif body is not None:
            entry["body"] = _register(body)
        if ok is not None:
            entry["ok"] = _register(ok)
        if status and status[0] != 200:
            entry["status"] = str(status[0])
        spec[method.lower()] = entry
    return {"x-turnos-docs": spec}


class Query:
    """One query parameter, for `query_docs()`: what it does and its JSON
    schema (a string unless said otherwise — every value arrives as text)."""

    __slots__ = ("description", "schema")

    def __init__(self, description, schema=None):
        self.description = description
        self.schema = dict(schema or {"type": "string"})


LANG_SWITCH = Query(
    "Switch the interface language: one the installation offers answers 302 to the same path, without the "
    "query, with the language cookie set (`core.i18n.lang_switch_redirect`); anything else is ignored")
"""`?lang=` of the routes that answer the language switch before anything else."""


def query_docs(**methods):
    """`openapi_extra` for a route: `query_docs(get={"days": Query(...)})` — per
    method, the query parameters the route reads (a bare string is a
    `Query` of text with that description). Documentation only: see the
    module docstring. `routers/api_docs.py` turns it into the operation's
    `parameters`."""
    spec = {}
    for method, params in methods.items():
        spec[method.lower()] = [
            {"name": name, "description": q.description, "schema": q.schema}
            for name, q in ((name, q if isinstance(q, Query) else Query(q)) for name, q in params.items())]
    return {"x-turnos-query": spec}


def models():
    """Every documented model by name (for the OpenAPI components and tests)."""
    return dict(_MODELS)


_register(ApiError)


def component_schemas():
    """JSON schemas of every documented model, references into components."""
    out = {}
    for name, model in sorted(_MODELS.items()):
        mode = "validation" if issubclass(model, BodyModel) else "serialization"
        schema = model.model_json_schema(ref_template="#/components/schemas/{model}", mode=mode)
        out.update(schema.pop("$defs", {}))
        out[name] = schema
    return out
