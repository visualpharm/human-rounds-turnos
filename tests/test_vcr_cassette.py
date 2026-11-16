"""test_vcr_cassette.py — ENGINE-level regressions for tests/cassettes/_vcr.py,
one level below the four per-connector cassette suites: these prove the
player's own interception surface, not any single connector's flow.

The 2026-09-08 escape this file regresses: `_Player` patched only
`urllib.request.urlopen`, but stdlib urllib has a SECOND dispatch path —
`urllib.request.build_opener(...)` hands back an `OpenerDirector`, and its
`.open()` never passes through the module-level `urlopen` the patch owned.
connectors/puco/puco.py's public GWT-RPC fallback (`_consultar_publico` →
`_pub_opener()` → `build_opener(HTTPCookieProcessor(...))` → `opener.open`)
speaks exactly that second path, so `test_corrupted_resultado_breaks_the_
coverage_read` (corrupted `resultado` → WS131 "no disponible" → fallback
fires) reached the real sisa.msal.gov.ar and the test still "passed" with
an HTTP 500 and an unclosed response behind it.

Every test here is offline BY CONSTRUCTION, including a RED baseline run:
`http.client`'s connection classes — the lowest transport any urllib
opener can reach — are replaced with fail-loud stubs that raise in
`__init__`, before any DNS lookup or socket exists.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_*.py"
"""

import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
CASSETTES_DIR = os.path.join(ROOT, "tests", "cassettes")

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

# _vcr.py loaded by file path, not sys.path + `import _vcr` — see
# test_hsi_cassette.py's own comment on this block for why (tests/cassettes/
# has bare per-connector subdirectories that would become importable
# namespace packages). Registered in sys.modules so mock.patch string
# targets in this file keep resolving it.
import importlib.util as _importlib_util  # noqa: E402
_vcr_spec = _importlib_util.spec_from_file_location(
    '_vcr_engine', os.path.join(CASSETTES_DIR, "_vcr.py"))
_vcr = _importlib_util.module_from_spec(_vcr_spec)
sys.modules['_vcr_engine'] = _vcr  # unique per file — same convention as the
# four per-connector suites (_vcr_hsi/_vcr_puco/...): a shared "_vcr" key
# would let one file's mock.patch land on ANOTHER file's _vcr module object
_vcr_spec.loader.exec_module(_vcr)

# ".invalid" never resolves (RFC 2606) — one more layer between a broken
# engine and the real network; the transport stubs below are the real fence.
_BASE = "http://engine.invalid"

# Hand-authored fixture: one interaction per call shape the engine must
# answer — GET (string URL), POST with a JSON body matched on `tipo`
# (fields.py whitelists it), and a 500 that must replay as HTTPError.
_SHAPES_CASSETTE = {
    "connector": "engine",
    "scenario": "shapes",
    "recorded_at": "2026-09-08T00:00:00+00:00",
    "source": "hand-authored fixture for _vcr engine regressions — no "
              "recording, no PII, synthetic host",
    "interactions": [
        {
            "method": "GET", "path": "/srv/get", "query": {},
            "request_body": None, "request_body_kind": None,
            "request_headers": {},
            "response": {"status": 200,
                         "headers": [["Content-Type", "text/plain"]],
                         "body": "pong", "body_kind": "text"},
        },
        {
            "method": "POST", "path": "/srv/post", "query": {},
            "request_body": {"tipo": "x"}, "request_body_kind": "json",
            "request_headers": {},
            "response": {"status": 200, "headers": [],
                         "body": {"ok": True}, "body_kind": "json"},
        },
        {
            "method": "GET", "path": "/srv/boom", "query": {},
            "request_body": None, "request_body_kind": None,
            "request_headers": {},
            "response": {"status": 500, "headers": [],
                         "body": "boom-body", "body_kind": "text"},
        },
    ],
}

_FIELDS_PY = 'PII_FIELDS = {}\nMATCH_FIELDS = {"tipo"}\n'


@contextmanager
def _no_real_transport():
    """Replace `http.client`'s connection classes — the lowest transport any
    urllib entrypoint (urlopen or an opener's handler chain) can reach —
    with fail-loud stubs. Yields the list of construction attempts, so a
    test can assert not just "no exception" but ZERO transport objects."""
    made = []
    # Class attributes urllib's handler chain reads while build_opener()
    # wires itself (AbstractHTTPHandler/HTTPSHandler __init__) — the stub
    # must carry them so opener CONSTRUCTION stays mock-safe too.
    real_http_vsn = http.client.HTTPSConnection._http_vsn

    class _NoTransport:
        debuglevel = 0
        _http_vsn = real_http_vsn

        def __init__(self, *a, **k):
            made.append((a, k))
            raise AssertionError(
                "urllib reached the real HTTP transport inside cassette "
                "replay — the engine must answer this call itself")

    with mock.patch.object(http.client, "HTTPConnection", _NoTransport), \
            mock.patch.object(http.client, "HTTPSConnection", _NoTransport):
        yield made


class _ScratchEngineCassette(unittest.TestCase):
    """Scratch 'engine' connector dir in a tmpdir — the committed fixture
    trees under tests/cassettes/ are never touched."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="vcr_engine_test_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        engine_dir = os.path.join(self.tmpdir, "engine")
        os.makedirs(engine_dir)
        with open(os.path.join(engine_dir, "shapes.json"), "w", encoding="utf-8") as fh:
            json.dump(_SHAPES_CASSETTE, fh)
        with open(os.path.join(engine_dir, "fields.py"), "w", encoding="utf-8") as fh:
            fh.write(_FIELDS_PY)
        patcher = mock.patch(
            "_vcr_engine._cassette_dir",
            lambda connector: os.path.join(self.tmpdir, connector))
        patcher.start()
        self.addCleanup(patcher.stop)


class TestReplayCoversEveryUrlEntrypoint(_ScratchEngineCassette):
    """The escape itself: replay must answer calls made through BOTH stdlib
    dispatch paths — the `urlopen` module function AND the OpenerDirector
    a `build_opener()` call returns (the exact shape puco.py's public
    fallback builds, cookie processor included)."""

    def test_urlopen_plays_a_recorded_get_from_a_bare_string_url(self):
        with _vcr.replay("engine", "shapes"):
            resp = urllib.request.urlopen(_BASE + "/srv/get")
            self.assertEqual(resp.getcode(), 200)
            self.assertEqual(resp.read(), b"pong")
            self.assertEqual(resp.headers.get("Content-Type"), "text/plain")

    def test_urlopen_string_with_data_plays_the_recorded_post(self):
        body = json.dumps({"tipo": "x"}).encode("utf-8")
        with _vcr.replay("engine", "shapes"):
            resp = urllib.request.urlopen(_BASE + "/srv/post", data=body)
            self.assertEqual(resp.getcode(), 200)
            self.assertEqual(json.loads(resp.read()), {"ok": True})

    def test_request_data_override_matches_stdlib_without_mutating_input(self):
        body = json.dumps({"tipo": "x"}).encode("utf-8")
        for original in (None, b'{"tipo":"old"}'):
            with self.subTest(original=original), _no_real_transport() as made:
                req = urllib.request.Request(_BASE + "/srv/post", data=original)
                with _vcr.replay("engine", "shapes"):
                    with urllib.request.build_opener().open(req, data=body) as resp:
                        self.assertEqual(json.loads(resp.read()), {"ok": True})
                self.assertEqual(req.data, original)
                self.assertEqual(made, [])

    def test_explicit_request_method_survives_data_override(self):
        req = urllib.request.Request(_BASE + "/srv/get", method="GET")
        method, url, body = _vcr._request_parts(req, b"override")
        self.assertEqual((method, url, body), ("GET", req.full_url, b"override"))

    def test_opener_open_plays_a_recorded_interaction(self):
        import http.cookiejar
        # Same construction puco.py's _pub_opener() uses — a cookie-aware
        # opener whose .open() the old urlopen-only patch never saw.
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with _no_real_transport() as made:
            with _vcr.replay("engine", "shapes"):
                req = urllib.request.Request(_BASE + "/srv/get")
                with opener.open(req, timeout=6) as resp:
                    self.assertEqual(resp.getcode(), 200)
                    self.assertEqual(resp.read(), b"pong")
            self.assertEqual(made, [])

    def test_opener_open_from_a_bare_string_url_plays_too(self):
        with _no_real_transport() as made:
            with _vcr.replay("engine", "shapes"):
                resp = urllib.request.build_opener().open(_BASE + "/srv/get")
                self.assertEqual(resp.read(), b"pong")
            self.assertEqual(made, [])

    def test_error_status_plays_as_httperror_through_the_opener(self):
        with _no_real_transport() as made:
            with _vcr.replay("engine", "shapes"):
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.build_opener().open(_BASE + "/srv/boom")
                # HTTPError is file-like and holds the replayed body — close
                # it like otp_send._enviar_meta does, or its deallocator
                # raises the ResourceWarning noise the repo gates on.
                self.assertEqual(ctx.exception.code, 500)
                self.assertEqual(ctx.exception.read(), b"boom-body")
                ctx.exception.close()
            self.assertEqual(made, [])

    def test_matched_replay_never_constructs_a_real_transport(self):
        # Both entrypoints, three shapes, zero transport objects: replay is
        # fully synthetic — no DNS, no socket, not even a connection class.
        with _no_real_transport() as made:
            with _vcr.replay("engine", "shapes"):
                urllib.request.urlopen(_BASE + "/srv/get")
                urllib.request.urlopen(
                    _BASE + "/srv/post",
                    data=json.dumps({"tipo": "x"}).encode("utf-8"))
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.build_opener().open(_BASE + "/srv/boom")
                ctx.exception.close()
            self.assertEqual(made, [])


class TestUnmatchedCallsFailClosed(_ScratchEngineCassette):
    """An unmatched call must never fall through to real transport, and a
    caller that swallows the ordinary CassetteError (puco.py's
    `except Exception` around the public fallback does exactly that) must
    not get a green context out of it."""

    def test_unmatched_opener_call_raises_and_touches_no_transport(self):
        with _no_real_transport() as made:
            with self.assertRaises(_vcr.CassetteError):
                with _vcr.replay("engine", "shapes"):
                    urllib.request.build_opener().open(_BASE + "/srv/other")
            self.assertEqual(made, [])

    def test_unmatched_urlopen_string_call_raises(self):
        with _no_real_transport() as made:
            with self.assertRaises(_vcr.CassetteError):
                with _vcr.replay("engine", "shapes"):
                    urllib.request.urlopen(_BASE + "/srv/other")
            self.assertEqual(made, [])

    def test_swallowed_unmatched_call_fails_the_context_at_exit(self):
        # The swallow shape that shipped the 2026-09-08 escape: the call
        # site's `except Exception` eats the CassetteError, the with-block
        # ends "cleanly", and only the exit check can turn it red.
        with _no_real_transport() as made:
            with self.assertRaises(_vcr.CassetteError):
                with _vcr.replay("engine", "shapes"):
                    try:
                        urllib.request.urlopen(_BASE + "/srv/other")
                    except Exception:  # noqa: BLE001 — the swallow under test
                        pass
            self.assertEqual(made, [])


class TestPatchesAreRestored(_ScratchEngineCassette):
    """The patched functions are process-global state; every exit path
    (clean, exceptional, nested, constructed-out-of-order) must leave them
    exactly as it found them."""

    def setUp(self):
        super().setUp()
        self.real_urlopen = urllib.request.urlopen
        self.real_open = urllib.request.OpenerDirector.open

    def _assert_restored(self):
        self.assertIs(urllib.request.urlopen, self.real_urlopen)
        self.assertIs(urllib.request.OpenerDirector.open, self.real_open)

    def test_both_surfaces_patched_inside_and_restored_after(self):
        with _vcr.replay("engine", "shapes"):
            self.assertIsNot(urllib.request.urlopen, self.real_urlopen)
            self.assertIsNot(urllib.request.OpenerDirector.open, self.real_open)
        self._assert_restored()

    def test_exception_inside_the_context_still_restores(self):
        class _Boom(Exception):
            pass

        with self.assertRaises(_Boom):
            with _vcr.replay("engine", "shapes"):
                raise _Boom("body failed")
        self._assert_restored()

    def test_consecutive_with_blocks_nest_correctly(self):
        with _vcr.replay("engine", "shapes") as outer:
            outer_patch = urllib.request.urlopen
            with _vcr.replay("engine", "shapes"):
                inner_patch = urllib.request.urlopen
                self.assertIsNot(inner_patch, outer_patch)
                # The inner player serves from its OWN queue copies, so the
                # same interaction is answerable in both contexts.
                self.assertEqual(
                    urllib.request.urlopen(_BASE + "/srv/get").read(), b"pong")
            # Inner's exit restored the OUTER's patch, not the real function.
            self.assertIs(urllib.request.urlopen, outer_patch)
            self.assertEqual(
                urllib.request.urlopen(_BASE + "/srv/get").read(), b"pong")
        self._assert_restored()

    def test_players_constructed_before_entering_still_nest_correctly(self):
        # The ordering the old engine got wrong: both players' __init__ ran
        # while the real function was still in place, so the inner's exit
        # restored the REAL function and silently destroyed the outer's
        # patch (the outer context then ran on real transport).
        outer = _vcr.replay("engine", "shapes")
        inner = _vcr.replay("engine", "shapes")
        with _no_real_transport() as made:
            with outer:
                outer_patch = urllib.request.urlopen
                with inner:
                    self.assertIsNot(urllib.request.urlopen, outer_patch)
                    self.assertEqual(
                        urllib.request.urlopen(_BASE + "/srv/get").read(),
                        b"pong")
                self.assertIs(urllib.request.urlopen, outer_patch)
                self.assertEqual(
                    urllib.request.urlopen(_BASE + "/srv/get").read(), b"pong")
            self.assertEqual(made, [])
        self._assert_restored()


class TestRecorderCapturesBothEntrypoints(_ScratchEngineCassette):
    """Recording must be symmetric with replay: a connector that speaks
    through an opener (PUCO's public fallback) would have its interactions
    SILENTLY MISSED by a urlopen-only recorder, producing a cassette whose
    replay then fails unmatched. Runs against a synthetic loopback server
    only — never an external host.

    The exactly-once assertion is the double-capture guard: with both
    surfaces patched, real `urlopen` delegates internally to an
    `OpenerDirector.open` the recorder also intercepts, so the guard that
    suppresses the inner capture while a patched `urlopen` is delegating is
    what keeps this at one interaction per call."""

    def test_urlopen_and_opener_calls_each_record_exactly_once(self):
        hits = []

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"pong")

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:%d" % server.server_address[1]

        real_urlopen = urllib.request.urlopen
        real_open = urllib.request.OpenerDirector.open
        with _vcr._Recorder("engine", "rec", "engine regression") as rec:
            resp = urllib.request.urlopen(urllib.request.Request(base + "/one"))
            self.assertEqual(resp.read(), b"pong")
            with urllib.request.build_opener().open(
                    urllib.request.Request(base + "/two"), timeout=5) as resp2:
                self.assertEqual(resp2.read(), b"pong")
            # Both calls captured in memory, exactly once each — one via
            # urlopen, one via the opener the old recorder never saw. The
            # file itself is written only at __exit__.
            self.assertEqual(len(rec.interactions), 2)
            self.assertFalse(os.path.exists(
                os.path.join(self.tmpdir, "engine", "rec.json")))
        self.assertIs(urllib.request.urlopen, real_urlopen)
        self.assertIs(urllib.request.OpenerDirector.open, real_open)
        self.assertEqual(hits, ["/one", "/two"])

        with open(os.path.join(self.tmpdir, "engine", "rec.json"),
                  encoding="utf-8") as fh:
            recorded = json.load(fh)
        self.assertEqual([i["path"] for i in recorded["interactions"]],
                         ["/one", "/two"])
        for interaction in recorded["interactions"]:
            self.assertEqual(interaction["method"], "GET")
            self.assertEqual(interaction["response"]["status"], 200)
            self.assertEqual(interaction["response"]["body"], "pong")

    def test_recording_closes_original_http_error_body(self):
        import io
        fp = io.BytesIO(b"synthetic failure")
        original = urllib.error.HTTPError(_BASE + "/boom", 500, "synthetic", {}, fp)
        def fail():
            raise original
        recorder = _vcr._Recorder("engine", "unused", "mock-only")
        try:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                recorder._record_call(_BASE + "/boom", None, fail)
            try:
                self.assertTrue(fp.closed, "consumed original HTTPError must be closed")
                self.assertEqual(caught.exception.read(), b"synthetic failure")
            finally:
                caught.exception.close()
        finally:
            original.close()


if __name__ == "__main__":
    unittest.main()
