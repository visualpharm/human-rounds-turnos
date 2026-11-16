"""tests/cassettes/_vcr.py — record/replay layer for the four connectors that
speak HTTP through stdlib `urllib.request` (HSI, PUCO, RENAPER, Meta — see
"Area 3 — Record-replay cassettes" in docs/roadmap/tier3-harness-design.md).
Wraps exactly TWO call sites — both dispatch paths stdlib urllib has — so
there is no new pip dependency and no per-connector adapter to write:

  - `urllib.request.urlopen`, the module function every direct caller uses;
  - `urllib.request.OpenerDirector.open`, the method every `build_opener()`
    opener dispatches through. Patching only the first is what let PUCO's
    public GWT-RPC fallback (`connectors/puco/puco.py`'s `_consultar_publico`
    → `build_opener(HTTPCookieProcessor(...)).open(...)`) escape a replay
    context to the real sisa.msal.gov.ar on 2026-09-08 while its test stayed
    green: the fallback swallows its own errors, so the escape surfaced as
    nothing worse than a passing assertFalse.

Two modes, chosen by `TURNOS_CASSETTE_MODE`:

  - "record" — talks to the REAL network, captures every interaction, PII-
    scrubs it (pii.py's `Scrubber` — the same reversible pseudonymization used
    before any external AI call — plus a per-connector field-name denylist),
    and writes it to `tests/cassettes/<connector>/<scenario>.json`. This is a
    deliberate, manual, human-reviewed act: run `scripts/record_cassette.py`,
    then read the diff before committing. **No test and no CI job ever sets
    this env var** — the guard below turns a `record` attempt under `CI` into
    a hard failure instead of a silent live-network hit in a pipeline.
  - anything else (default: "replay") — the ONLY mode any test uses. Loads
    the committed cassette and answers both call shapes from it. A call with
    no matching recorded interaction is a hard failure (`CassetteError`) — it
    NEVER falls through to the real network — and because connector code
    legitimately swallows transport errors (`except Exception: return ok=False`
    is PUCO's documented degradation contract), the player ALSO remembers
    every unmatched call and re-raises from `__exit__` if the context ended
    "cleanly" with unmatched calls behind it. A caller that eats the ordinary
    exception cannot turn a transport escape into a green test.
    `replay()` below ignores the env var entirely so a stray
    `TURNOS_CASSETTE_MODE=record` in a shell can never make a test quietly
    hit the network either.

Both patches are taken on `__enter__` and restored on `__exit__` (whichever
way the body exits), so nested or constructed-out-of-order contexts each
restore exactly the function they replaced — an inner context's exit must
never un-patch an outer one mid-flight.

Two extra safety nets beyond the design's own minimum ("request headers minus
Authorization"): the `Cookie` request header is stripped too (HSI's JWT rides
there, not just in `Authorization`), and any `Set-Cookie` response header has
its `token=`/`refreshToken=` VALUE redacted at capture time — a JWT is
high-entropy text that pii.py's generic sweep (built for emails/DNIs/phones)
would never recognise as a credential.
"""

import importlib.util
import io
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import Message

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.core import pii  # noqa: E402 — reversible pseudonymization, see pii.py's own docstring


class CassetteError(Exception):
    """Replay found no matching recorded interaction, or the cassette file is
    missing, or a record attempt was made somewhere it must never run. Always
    raised, never swallowed — a cassette test that quietly fell through to the
    real network would be a worse bug than a red test (Area 3's test plan)."""


_EXCLUDED_REQUEST_HEADERS = {"authorization", "cookie"}
_COOKIE_VALUE_RE = re.compile(r"(?i)\b(token|refreshToken)=([^;]+)")


def _redact_cookie_value(header_value):
    return _COOKIE_VALUE_RE.sub(lambda m: "%s=<redacted>" % m.group(1), header_value)


def _cassette_dir(connector):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), connector)


def _cassette_path(connector, scenario):
    return os.path.join(_cassette_dir(connector), scenario + ".json")


def _connector_fields(connector):
    """PII_FIELDS / MATCH_FIELDS from tests/cassettes/<connector>/fields.py,
    defined once per connector next to its cassette directory (per the design's
    "each connector's own local names for these"). Missing file or missing
    names both degrade to empty rather than raising — a connector can land its
    first cassette before its denylist is tuned.

    Loaded by file path, not by a dotted `test.cassettes.<connector>.fields`
    import — this repo's `tests/` has no `__init__.py` and the stdlib ships its
    own `test` package, so a name-based import can silently resolve to the
    WRONG module depending on sys.path order (the same trap CONTRIBUTING.md documents
    for `python -m unittest test.X`)."""
    path = os.path.join(_cassette_dir(connector), "fields.py")
    if not os.path.exists(path):
        return {}, set()
    spec = importlib.util.spec_from_file_location(
        "turnos_cassette_fields_%s" % connector, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return dict(getattr(mod, "PII_FIELDS", {})), set(getattr(mod, "MATCH_FIELDS", ()))


def _split_url(url):
    parsed = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
    return parsed.path, query


def _decode_body(raw_bytes):
    if not raw_bytes:
        return None, None
    text = raw_bytes.decode("utf-8", "replace")
    try:
        return json.loads(text), "json"
    except ValueError:
        return text, "text"


def _encode_body(value, kind):
    if value is None:
        return b""
    if kind == "json":
        return json.dumps(value).encode("utf-8")
    return str(value).encode("utf-8")


def _match_key(method, path, query, body, body_kind, match_fields):
    """(method, path, whitelisted query fields, whitelisted body fields) — the
    PII-carrying fields (dni, nombre, ...) are deliberately never part of this
    key: they get scrubbed to placeholder tokens before the cassette is
    written, so comparing them against a live request's real values would
    never match. Volatile fields (timestamps, nonces) are excluded the same
    way, by simply never being added to a connector's MATCH_FIELDS."""
    q = tuple(sorted((k, v) for k, v in (query or {}).items() if k in match_fields))
    b = ()
    if body_kind == "json" and isinstance(body, dict):
        b = tuple(sorted((k, json.dumps(v, sort_keys=True))
                         for k, v in body.items() if k in match_fields))
    return (method.upper(), path, q, b)


def _request_parts(fullurl, data):
    """(method, url, body) for every call shape stdlib accepts — a `Request`
    object (the shape all four connectors build), or a bare string/bytes URL
    optionally with a `data=` argument, which `urlopen(url, data)` and
    `OpenerDirector.open(url, data)` both normalize the same way (a fresh
    Request, POST iff data is not None). Read-only: the caller's Request is
    never mutated, so matching a `open(req, data)` call needs no copy."""
    if isinstance(fullurl, urllib.request.Request):
        body = data if data is not None else fullurl.data
        method = getattr(fullurl, "method", None) or ("POST" if body is not None else "GET")
        return method, fullurl.full_url, body
    url = fullurl if isinstance(fullurl, str) else str(fullurl, "utf-8", "replace")
    return ("POST" if data is not None else "GET"), url, data


class _StubResponse:
    """Just enough of `http.client.HTTPResponse` for hsi_client.py's (and the
    other three connectors') `_request`-style helpers: `.read()`,
    `.headers.get_all(name)`, `.getcode()`.

    Slice 6 addition: `__enter__`/`__exit__` (plain context-manager pass-
    through — no PEP 343 magic needed, `urlopen()`'s real return value
    already supports `with` since Python 3.9). HSI's pilot never needed this
    (`hsi_client.py:219` calls `urlopen()` bare, no `with`), but PUCO
    (`connectors/puco/puco.py`'s `_consultar_ws131`), RENAPER
    (`auth_renaper.py`'s `_post_form`) and Meta (`otp_send.py`'s
    `_enviar_meta`/`_enviar_whatsapp`) all write `with urllib.request.urlopen(...)
    as resp:` — without this, replay raises `TypeError: '_StubResponse'
    object does not support the context manager protocol` before the
    connector's own logic ever runs. Documented per the design's "reuse
    _vcr.py UNCHANGED unless a connector's shape genuinely requires an
    extension" rule (Area 3, tier3-harness-design.md, execution slice 6)."""

    def __init__(self, status, headers, body_bytes):
        self.status = status
        self.code = status
        self._body = body_bytes
        self.headers = Message()
        for k, v in headers or []:
            self.headers[k] = v

    def read(self):
        return self._body

    def getcode(self):
        return self.status

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _scrub_interaction(interaction, scrubber, pii_fields, match_fields):
    """Every string leaf through pii.py's generic sweep (catches emails and
    DNI/phone-shaped digit runs wherever they appear), PLUS a hard override
    for any dict key named in `pii_fields`: that net catches a value the
    generic sweep would never flag on its own — a bare first name ("JUAN")
    matches no regex, but under the key "firstName" it's still a patient's
    name.

    Two things are deliberately kept BYTE-IDENTICAL rather than run through
    the generic sweep: a bare ISO calendar date ("2026-07-09") and any value
    under a key the connector's own fields.py lists in MATCH_FIELDS. Both
    reflect the real interaction — a diary window bound the caller parses
    with `date.fromisoformat`, or the very field replay matches calls on —
    and pii.py's digit-count classifier (`_clasificar`, built for free-text
    chat/clinical prose) treats an 8-digit YYYY-MM-DD date exactly like an
    8-digit DNI; its own date exemption only recognises DD-MM-YYYY. A
    genuinely identifying date (birthDate) is still always caught — it's in
    PII_FIELDS, checked BEFORE this branch, regardless of its shape."""
    def walk(obj, key=None):
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if isinstance(v, str) and v and k in pii_fields:
                    out[k] = scrubber.token(v, pii_fields[k])
                elif isinstance(v, str) and (k in match_fields or _ISO_DATE_RE.match(v)):
                    out[k] = v
                else:
                    out[k] = walk(v, k)
            return out
        if isinstance(obj, list):
            return [walk(v, key) for v in obj]
        if isinstance(obj, str):
            return obj if _ISO_DATE_RE.match(obj) else scrubber.scrub(obj)
        return obj
    return walk(interaction)


# ---------------------------------------------------------------- recording

class _Recorder:
    def __init__(self, connector, scenario, source_note):
        self.connector = connector
        self.scenario = scenario
        self.source_note = source_note
        self.interactions = []
        # >0 while a patched `urlopen` is delegating to the REAL one: real
        # urlopen dispatches internally through `OpenerDirector.open`, which
        # this recorder also intercepts, so without this guard the same
        # interaction would be captured twice (outer envelope + inner hop).
        self._depth = 0

    def _capture_request_headers(self, fullurl, data):
        if not isinstance(fullurl, urllib.request.Request):
            fullurl = urllib.request.Request(fullurl, data)
        return {k: v for k, v in fullurl.header_items()
                if k.lower() not in _EXCLUDED_REQUEST_HEADERS}

    def _capture_response_headers(self, items):
        out = []
        for k, v in items:
            if k.lower() == "set-cookie":
                v = _redact_cookie_value(v)
            out.append([k, v])
        return out

    def _record_call(self, fullurl, data, call):
        """One capture envelope around a real-transport call, shared by both
        patched surfaces (`call()` performs the actual delegation)."""
        method, url, body = _request_parts(fullurl, data)
        path, query = _split_url(url)
        req_body, req_kind = _decode_body(body)
        req_headers = self._capture_request_headers(fullurl, data)
        try:
            resp = call()
        except urllib.error.HTTPError as e:
            try:
                raw = e.read()
            finally:
                e.close()
            body_out, kind = _decode_body(raw)
            self.interactions.append({
                "method": method, "path": path, "query": query,
                "request_body": req_body, "request_body_kind": req_kind,
                "request_headers": req_headers,
                "response": {
                    "status": e.code,
                    "headers": self._capture_response_headers(list((e.headers or {}).items())),
                    "body": body_out, "body_kind": kind,
                },
            })
            raise urllib.error.HTTPError(url, e.code, e.msg, e.headers, io.BytesIO(raw))
        try:
            raw = resp.read()
        finally:
            resp.close()  # the stub owns the bytes, including failed-read cleanup
        resp_headers = list(resp.headers.items()) if hasattr(resp, "headers") else []
        body_out, kind = _decode_body(raw)
        self.interactions.append({
            "method": method, "path": path, "query": query,
            "request_body": req_body, "request_body_kind": req_kind,
            "request_headers": req_headers,
            "response": {
                "status": resp.getcode(),
                "headers": self._capture_response_headers(resp_headers),
                "body": body_out, "body_kind": kind,
            },
        })
        return _StubResponse(resp.getcode(), resp_headers, raw)

    def _urlopen(self, fullurl, data=None, *args, **kwargs):
        self._depth += 1
        try:
            return self._record_call(
                fullurl, data,
                lambda: self._real_urlopen(fullurl, data, *args, **kwargs))
        finally:
            self._depth -= 1

    def _record_open(self, opener, fullurl, data, args, kwargs):
        if self._depth:
            # Inner hop of a urlopen we are already capturing above.
            return self._real_open(opener, fullurl, data, *args, **kwargs)
        return self._record_call(
            fullurl, data,
            lambda: self._real_open(opener, fullurl, data, *args, **kwargs))

    def __enter__(self):
        self._real_urlopen = urllib.request.urlopen
        self._real_open = urllib.request.OpenerDirector.open
        recorder = self

        def _rec_open(opener, fullurl, data=None, *args, **kwargs):
            return recorder._record_open(opener, fullurl, data, args, kwargs)

        self._patched_open = _rec_open
        urllib.request.urlopen = self._urlopen
        urllib.request.OpenerDirector.open = _rec_open
        return self

    def __exit__(self, exc_type, exc, tb):
        urllib.request.urlopen = self._real_urlopen
        urllib.request.OpenerDirector.open = self._real_open
        if exc_type is None:
            self._write()
        return False

    def _write(self):
        scrubber = pii.Scrubber()
        pii_fields, match_fields = _connector_fields(self.connector)
        scrubbed = [_scrub_interaction(i, scrubber, pii_fields, match_fields)
                    for i in self.interactions]
        path = _cassette_path(self.connector, self.scenario)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "connector": self.connector,
            "scenario": self.scenario,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "source": self.source_note,
            "interactions": scrubbed,
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")


# ------------------------------------------------------------------ replay

class _Player:
    """Answers BOTH stdlib dispatch paths from the cassette. An unmatched
    call fails closed twice: it raises `CassetteError` at the call site, and
    — because connector code legitimately swallows transport errors (PUCO's
    `except Exception` around its public fallback is a documented
    degradation contract, not a bug) — it is remembered and re-raised from
    `__exit__` whenever the body ends without an exception of its own. A
    replay context can therefore never end green with unmatched calls
    hidden behind a swallowed exception."""

    def __init__(self, connector, scenario):
        self.connector = connector
        self.scenario = scenario
        path = _cassette_path(connector, scenario)
        if not os.path.exists(path):
            raise CassetteError(
                "no cassette at %s — record one first with "
                "scripts/record_cassette.py --connector %s --scenario %s"
                % (path, connector, scenario))
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        _, self.match_fields = _connector_fields(connector)
        self._queues = {}
        for interaction in data.get("interactions", []):
            key = _match_key(interaction["method"], interaction["path"],
                             interaction.get("query"),
                             interaction.get("request_body"),
                             interaction.get("request_body_kind"),
                             self.match_fields)
            self._queues.setdefault(key, []).append(interaction)
        self._unmatched = []

    def _serve(self, fullurl, data=None):
        method, url, body = _request_parts(fullurl, data)
        path, query = _split_url(url)
        req_body, req_kind = _decode_body(body)
        key = _match_key(method, path, query, req_body, req_kind, self.match_fields)
        queue = self._queues.get(key)
        if not queue:
            self._unmatched.append("%s %s" % (method, url))
            raise CassetteError(
                "unmatched call in replay mode: %s %s (cassette %s/%s) — no "
                "recorded interaction matches; refusing to hit the real "
                "network" % (method, url, self.connector, self.scenario))
        interaction = queue.pop(0)
        if not queue:
            del self._queues[key]
        resp = interaction["response"]
        body_bytes = _encode_body(resp.get("body"), resp.get("body_kind"))
        status = resp.get("status", 200)
        headers = [tuple(h) for h in resp.get("headers", [])]
        if status >= 400:
            raise urllib.error.HTTPError(
                url, status, "cassette replay", Message(), io.BytesIO(body_bytes))
        return _StubResponse(status, headers, body_bytes)

    def _urlopen(self, fullurl, data=None, *args, **kwargs):
        return self._serve(fullurl, data)

    def __enter__(self):
        # Saved on ENTER, not on construction: a player built before another
        # one entered must still restore whatever is current when IT exits,
        # or an inner exit would silently un-patch an outer context.
        self._saved_urlopen = urllib.request.urlopen
        self._saved_open = urllib.request.OpenerDirector.open
        player = self

        def _replay_open(_opener, fullurl, data=None, *args, **kwargs):
            return player._serve(fullurl, data)

        self._patched_open = _replay_open
        urllib.request.urlopen = self._urlopen
        urllib.request.OpenerDirector.open = _replay_open
        return self

    def __exit__(self, exc_type, exc, tb):
        # Restore FIRST, whatever happens next — the process-global patches
        # must never outlive the context, not even on the failing path.
        urllib.request.urlopen = self._saved_urlopen
        urllib.request.OpenerDirector.open = self._saved_open
        if exc_type is None and self._unmatched:
            # Every unmatched call already raised at its call site; reaching
            # here means each one was swallowed on the way out (an
            # `except Exception` in the code under test). Fail the context
            # itself — do not let the test pass as offline when replay never
            # answered these. An exception of the body's own propagates
            # untouched instead of being masked by this one.
            raise CassetteError(
                "replay context ended cleanly but %d call(s) went unmatched "
                "and were swallowed by the code under test: %s (cassette "
                "%s/%s) — the test would have passed as offline while "
                "replay never answered them"
                % (len(self._unmatched), "; ".join(self._unmatched),
                   self.connector, self.scenario))
        return False


@contextmanager
def cassette(connector, scenario, source_note=None):
    """Public entrypoint for BOTH modes, selected by `TURNOS_CASSETTE_MODE`.
    Tests should use `replay()` below instead — it can't be flipped into
    record mode by an ambient env var."""
    mode = os.environ.get("TURNOS_CASSETTE_MODE", "replay")
    if mode == "record":
        if os.environ.get("CI"):
            raise CassetteError(
                "TURNOS_CASSETTE_MODE=record must never run in CI — recording "
                "is a manual, human-reviewed act (Area 3, "
                "docs/roadmap/tier3-harness-design.md)")
        with _Recorder(connector, scenario, source_note or "recorded live") as rec:
            yield rec
    else:
        with _Player(connector, scenario) as player:
            yield player


def replay(connector, scenario):
    """What every test uses: always replay, regardless of
    `TURNOS_CASSETTE_MODE` — a stray env var can never make a test quietly
    hit the network."""
    return _Player(connector, scenario)
