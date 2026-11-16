"""_body_contract.py — golden pins of how a JSON route answers the request
bodies it does NOT expect (TASK-24: Pydantic models at the routes' edge).

Moving a route's body onto `core.api_body.read_body` and a Pydantic model
must not change a single answer the route gives today, so before a route is
touched its answers are recorded on the OLD code, for a fixed battery of
bodies: empty, malformed, not UTF-8, a UTF-8 BOM, every JSON value that is
not an object, valid JSON sent as text/plain, the valid body, the valid body
plus an unknown field, and every field of the valid body replaced by a
value of each other JSON type. Routes behind a session are also asked the
malformed and the valid body WITHOUT the session (the session check comes
first). Each case runs on a freshly truncated database, so the answers are
deterministic.

What is pinned per case: status, `Content-Type`, `Cache-Control`,
`Content-Encoding` and the body text (volatile values — timestamps, the day of an
edit and long tokens — normalized). The golden file is `tests/golden/body_contract_<domain>.json`;
`TURNOS_GOLDEN_WRITE=1` rewrites it (only ever on the commit that pins the
old code, or on one that changes an answer on purpose — the diff of that
file IS the behaviour change).

Not named test_*.py: unittest discovery must not collect it.
"""

import json
import os
import re
import unicodedata

_GOLDEN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "golden")
WRITE = os.environ.get("TURNOS_GOLDEN_WRITE") == "1"

# Values that differ on every run: random tokens, revisions, timestamps.
# `actualizado` is the one date-only field the answers fill from the clock
# (the legal texts: the day of the edit, from Postgres `now()` — so a frozen
# Python clock cannot pin it); the other dates in the golden files are fixed
# fixture values (birth dates) and stay exact.
_DATE_KEY_RE = re.compile(r"^actualizado$")
_DATE_RE = re.compile(r"\d{4}-\d\d-\d\d")
_VOLATILE_KEY_RE = re.compile(r"token|revision|hash|secret|otpauth|^password_|^(created|updated|ts)$|_at$")
_TS_RE = re.compile(r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(\.\d+)?(Z|[+-]\d\d:?\d\d)?")
_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])")
_EPOCH_RE = re.compile(r"(?<![0-9])1[6-9]\d{8}(\.\d+)?(?![0-9])")

_OTHER_VALUES = (("int", 12345), ("str", "x"), ("bool", True), ("list", []),
                 ("object", {}), ("null", None))


def _kind(value):
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    return "null" if value is None else "other"


def battery(valid):
    """[(case name, raw body bytes, extra headers)] for one route."""
    enc = lambda obj: json.dumps(obj).encode("utf-8")  # noqa: E731
    cases = [
        ("empty", b"", {}),
        ("malformed", b"{bad", {}),
        ("not utf-8", b"\xff\xfe{}", {}),
        ("utf-8 bom", "\ufeff{}".encode("utf-8"), {}),
        ("array", b"[]", {}),
        ("null", b"null", {}),
        ("string", b'"x"', {}),
        ("number", b"123", {}),
        ("valid as text/plain", enc(valid), {"Content-Type": "text/plain"}),
        ("valid", enc(valid), {}),
        ("valid + unknown field", enc(dict(valid, zz_unknown=1)), {}),
    ]
    for field, value in valid.items():
        for kind, other in _OTHER_VALUES:
            if kind != _kind(value):
                cases.append((f"{field}={kind}", enc(dict(valid, **{field: other})), {}))
    return cases


def _normalize_text(text):
    text = _TS_RE.sub("<ts>", text)
    text = _TOKEN_RE.sub("<token>", text)
    return _EPOCH_RE.sub("<epoch>", text)


def _normalize_value(key, value):
    if isinstance(value, str) and value:
        if _VOLATILE_KEY_RE.search(key):
            return "<volatile>"
        if _DATE_KEY_RE.search(key) and _DATE_RE.fullmatch(value):
            return "<date>"
    return _normalize_json(value)


def _normalize_json(node):
    if isinstance(node, dict):
        return {k: _normalize_value(k, v) for k, v in node.items()}
    if isinstance(node, list):
        return [_normalize_json(v) for v in node]
    if isinstance(node, str):
        return _normalize_text(node)
    return node


def normalize(text):
    """JSON bodies keep their exact text when nothing in them is volatile;
    otherwise the volatile values are replaced and the JSON re-dumped the
    way the app dumps it (`json.dumps(..., ensure_ascii=False)`)."""
    try:
        parsed = json.loads(text)
    except ValueError:
        return _normalize_text(text)
    normalized = _normalize_json(parsed)
    if normalized == parsed:
        return text
    return json.dumps(normalized, ensure_ascii=False)


def request(base_url, method, path, raw, headers):
    req_headers = {"Content-Type": "application/json"}
    req_headers.update(headers)
    # Lazy: a golden-only reader (test_body_contract_clock.py) imports this
    # file without `tests/` on sys.path; a live request always has it.
    import _livehttp
    return _livehttp.call(base_url, method, path, data=raw, headers=req_headers)


def answer(status, headers, raw):
    if headers.get("Content-Encoding") == "gzip":
        import gzip
        raw = gzip.decompress(raw)
    return {
        "status": status,
        "content-type": headers.get("Content-Type"),
        "cache-control": headers.get("Cache-Control"),
        "content-encoding": headers.get("Content-Encoding"),
        "body": normalize(raw.decode("utf-8", "replace")),
    }


class Golden:
    """One golden file; tests read their section, the writer collects all."""

    def __init__(self, domain):
        self.path = os.path.join(_GOLDEN_DIR, f"body_contract_{domain}.json")
        self.sections = {}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as fh:
                self.sections = json.load(fh)

    def section(self, key):
        return self.sections.get(key)

    def put(self, key, answers):
        self.sections[key] = answers

    def save(self):
        os.makedirs(_GOLDEN_DIR, exist_ok=True)
        text = json.dumps(dict(sorted(self.sections.items())), ensure_ascii=False, indent=1)
        # The repo keeps no Cyrillic literals outside locales/ru and locales/uk
        # (tests/test_no_cyrillic.py): a JSON escape reads back as the same string.
        text = "".join(
            ("\\u%04x" % ord(c) if ord(c) < 0x10000 else json.dumps(c)[1:-1])
            if unicodedata.name(c, "").startswith("CYRILLIC") else c
            for c in text)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")


def check_docs(testcase, key, valid, answers, docs):
    """The route's documented models tell the truth: the valid body matches
    the request model, and every 2xx JSON answer matches the answer model."""
    body_model, ok_model = docs
    if body_model is not None:
        body_model.model_validate(valid)
    if ok_model is None:
        return
    for name, got in answers.items():
        if 200 <= got["status"] < 300 and (got["content-type"] or "").startswith("application/json"):
            with testcase.subTest(route=key, case=name, check="answer model"):
                ok_model.model_validate(json.loads(got["body"]))


def check_refusals(testcase, key, cases, answers, body_model, reads=None):
    """The strict boundary (TASK-76 item F, `core.api_body.STRICT`): every case whose body decodes to a value
    that does not match the request model is the family's 400 — a flat `{"error": "JSON inválido…"}` —
    never an answer of the domain. `reads` is the family's own decoding when it is not `api_body.decode`
    (a route that reads `null` or a malformed body as `{}`); a body that does not decode is the family's own
    business (pinned)."""
    from pydantic import ValidationError
    from app.core import api_body
    if not api_body.STRICT:
        return
    for name, raw, _headers in cases:
        try:
            value = (reads or api_body.decode)(raw)
        except ValueError:
            continue
        try:
            body_model.model_validate(value)
            continue
        except ValidationError:
            pass
        got = answers[name]
        with testcase.subTest(route=key, case=name, check="refused"):
            testcase.assertEqual(got["status"], 400)
            testcase.assertTrue(json.loads(got["body"])["error"].startswith("JSON inv"), got["body"])


def pin_route(testcase, golden, method, path, valid, *, reset, auth=None,
              extra_cases=(), docs=None, key=None, refuses=True, reads=None):
    """Run the battery against `method path`, every case after `reset()`
    (which returns the session headers when the route needs one, via
    `auth`), and compare with — or, in write mode, record into — `golden`.
    `path` may be a callable, read after each reset (a token minted by the
    reset); then `key` names the section. `docs` = (request model, 2xx
    answer model) — see `check_docs`; with `refuses` (a route with a family
    contract, `core.api_body`), also `check_refusals` (`reads`: the family's
    own decoding)."""
    key = key or f"{method} {path}"
    current = path if callable(path) else (lambda: path)
    got = {}
    cases = list(battery(valid)) + list(extra_cases)
    for name, raw, headers in cases:
        session = reset()
        hdrs = dict(session or {})
        hdrs.update(headers)
        got[name] = answer(*request(testcase._base_url, method, current(), raw, hdrs))
    if auth is not None:
        for name, raw in (("no session: malformed", b"{bad"),
                          ("no session: valid", json.dumps(valid).encode("utf-8"))):
            reset()
            got[name] = answer(*request(testcase._base_url, method, current(), raw, {}))
    if docs is not None:
        check_docs(testcase, key, valid, got, docs)
        if refuses and docs[0] is not None:
            check_refusals(testcase, key, cases, got, docs[0], reads)
    if WRITE:
        golden.put(key, got)
        return got
    want = golden.section(key)
    testcase.assertIsNotNone(want, f"no golden for {key}: run with TURNOS_GOLDEN_WRITE=1 on the old code")
    testcase.assertEqual(sorted(got), sorted(want), key)
    for name in want:
        with testcase.subTest(route=key, case=name):
            testcase.assertEqual(got[name], want[name])
    return got
