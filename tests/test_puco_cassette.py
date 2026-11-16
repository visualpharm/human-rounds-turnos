"""test_puco_cassette.py — Area 3 (record-replay cassettes) slice 6: replay-
mode tests for the PUCO pilot cassette (tests/cassettes/puco/consulta_ok.json),
mechanical repeat of test_hsi_cassette.py's slice-5 pattern against
connectors/puco/puco.py's WS131 REST client (`_consultar_ws131`, called from
`consultar()`).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_*.py"

No network, no live SISA, no Postgres writes — puco.consultar() only touches
its own in-memory cache (test_puco.py's existing suite covers the fake-mode/
DB-backed cobertura flow; this file is specifically about the HTTP-shape
cassette layer, one level below that, same split HSI's own pilot drew).
"""

import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
CASSETTES_DIR = os.path.join(ROOT, "tests", "cassettes")

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
config.ENV_PATH = ""  # deterministic — never read the dev checkout's real .env

from app.connectors.puco import puco       # noqa: E402
# _vcr.py loaded by file path, not `sys.path.insert(CASSETTES_DIR) + import
# _vcr` — see test_hsi_cassette.py's own comment on this same block for why
# (putting tests/cassettes/ on sys.path exposes its per-connector
# subdirectories as importable top-level namespace packages, e.g. `puco`/
# `renaper`, since Tier 2 batch L retired the repo-root shims of those
# names — this file's own connector!). Registered in sys.modules so
# `mock.patch("_vcr_puco.<attr>", ...)` string targets in this file keep
# resolving it.
import importlib.util as _importlib_util  # noqa: E402
_vcr_spec = _importlib_util.spec_from_file_location('_vcr_puco', os.path.join(CASSETTES_DIR, "_vcr.py"))
_vcr = _importlib_util.module_from_spec(_vcr_spec)
sys.modules['_vcr_puco'] = _vcr  # unique per file — see comment above; a shared "_vcr" key would let one
# file's mock.patch("_vcr...") land on ANOTHER file's _vcr module object
_vcr_spec.loader.exec_module(_vcr)

puco.ENV_PATH = ""

CASSETTE_PATH = os.path.join(CASSETTES_DIR, "puco", "consulta_ok.json")
FIELDS_PATH = os.path.join(CASSETTES_DIR, "puco", "fields.py")

# Same DNI the pilot cassette was recorded for — see consulta_ok.json's
# "source" note for why this is a repo test fixture, not a real citizen.
_DNI = "99887766"


def _consultar():
    puco._CACHE.clear()
    with _env.patched_env(TURNOS_SISA_USER="sisa-test-user", TURNOS_SISA_PASS="sisa-test-pass",
                          TURNOS_FAKE_PUCO=None):
        with _vcr.replay("puco", "consulta_ok"):
            return puco.consultar(_DNI)


class TestConsultarReplay(unittest.TestCase):
    """The pilot acceptance bar, same shape as slice 5's HSI test: replay
    answers consultar() with the recorded coverage, no network involved."""

    def test_replay_returns_the_recorded_coverage(self):
        ok, obras = _consultar()
        self.assertTrue(ok)
        self.assertEqual(len(obras), 1)
        self.assertEqual(obras[0]["nombre"], "IOMA")  # amigable() translation
        self.assertEqual(obras[0]["rnos"], "902001")
        self.assertEqual(obras[0]["nombre_padron"], "O.S.P. BUENOS AIRES (I.O.M.A.)")

    def test_replay_is_deterministic_across_repeated_runs(self):
        first = _consultar()
        for _ in range(2):
            self.assertEqual(_consultar(), first)


class _ScratchCassetteCase(unittest.TestCase):
    """Copies the real pilot cassette into a scratch dir so a test can delete
    or hand-edit it without ever touching the committed fixture on disk —
    same pattern test_hsi_cassette.py uses."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="puco_cassette_test_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        puco_dir = os.path.join(self.tmpdir, "puco")
        os.makedirs(puco_dir)
        shutil.copy(CASSETTE_PATH, os.path.join(puco_dir, "consulta_ok.json"))
        shutil.copy(FIELDS_PATH, os.path.join(puco_dir, "fields.py"))
        patcher = mock.patch(
            "_vcr_puco._cassette_dir", lambda connector: os.path.join(self.tmpdir, connector))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.scratch_cassette = os.path.join(puco_dir, "consulta_ok.json")

    def _load(self):
        with open(self.scratch_cassette, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _save(self, data):
        with open(self.scratch_cassette, "w", encoding="utf-8") as fh:
            json.dump(data, fh)


class TestMissingCassette(_ScratchCassetteCase):
    def test_deleted_cassette_fails_loudly(self):
        os.remove(self.scratch_cassette)
        with self.assertRaises(_vcr.CassetteError):
            _consultar()


class TestCorruptedCassette(_ScratchCassetteCase):
    """A hand-edited response body must flip the test red — proves the
    caller's parsing is actually exercised, not incidentally passing."""

    def test_corrupted_resultado_breaks_the_coverage_read(self):
        data = self._load()
        booking = data["interactions"][0]
        self.assertEqual(booking["path"], "/sisa/services/rest/puco/99887766")
        booking["response"]["body"] = booking["response"]["body"].replace(
            "<resultado>OK</resultado>", "<resultado>ERROR_INESPERADO</resultado>")
        self._save(data)
        # Scoped to the WS131 contract: a non-OK `resultado` must read as
        # "no se pudo consultar", never a false "no figura" (puco.py's own
        # docstring). The public GWT-RPC fallback is OFF for this one test
        # only — `consultar()` would otherwise fire its (uncassette'd)
        # /dispatch bootstrap, which replay now correctly fails closed on
        # (see TestPublicFallbackEscapeIsClosed for that path's own proof).
        with mock.patch.object(puco, "_publico_on", return_value=False):
            ok, obras = _consultar()
        self.assertFalse(ok)
        self.assertEqual(obras, [])

    def test_corrupted_cobertura_name_changes_the_translation(self):
        data = self._load()
        booking = data["interactions"][0]
        booking["response"]["body"] = booking["response"]["body"].replace(
            "O.S.P. BUENOS AIRES (I.O.M.A.)", "OSDE")
        self._save(data)
        ok, obras = _consultar()
        self.assertTrue(ok)
        self.assertEqual(obras[0]["nombre"], "OSDE")


class TestPublicFallbackEscapeIsClosed(_ScratchCassetteCase):
    """The proven 2026-09-08 offline-test escape, regressed at the lowest
    transport. A corrupted `resultado` flips WS131 to ok=False, so
    consultar() falls through to the public GWT-RPC fallback — which speaks
    through urllib.request.build_opener(...).open(...), a dispatch path a
    urlopen-only patch never sees. The call reached the real
    sisa.msal.gov.ar and the test still "passed" (assertFalse(ok)) with an
    HTTP 500 and an unclosed response behind it.

    Offline by construction even on a RED baseline: http.client's
    connection classes — the lowest transport urllib's opener chain can
    reach — are replaced with fail-loud stubs that raise in __init__,
    before any DNS lookup or socket. And because puco.py swallows the
    fallback's errors with `except Exception`, only the player's exit check
    (unmatched calls recorded at raise time, re-raised when the context
    ends cleanly) can turn this red."""

    def setUp(self):
        super().setUp()
        # Deterministic bootstrap: no opener left over from another test's
        # fallback attempt, so the first unmatched call is /dispatch.
        puco._PUB_SESSION.update(opener=None, ts=0.0)

    def test_fallback_after_corrupted_resultado_never_reaches_transport(self):
        data = self._load()
        booking = data["interactions"][0]
        booking["response"]["body"] = booking["response"]["body"].replace(
            "<resultado>OK</resultado>", "<resultado>ERROR_INESPERADO</resultado>")
        self._save(data)
        made = []

        class _NoTransport:
            # Class attributes urllib's handler chain reads while
            # build_opener() wires itself — the stub must carry them so
            # opener CONSTRUCTION stays mock-safe too (puco's _pub_opener
            # builds its cookie opener INSIDE the replay).
            debuglevel = 0
            _http_vsn = http.client.HTTPSConnection._http_vsn

            def __init__(self, *a, **k):
                made.append((a, k))
                raise AssertionError(
                    "urllib reached the real HTTP transport during PUCO "
                    "replay — the fallback escaped the cassette engine")

        with mock.patch.object(http.client, "HTTPConnection", _NoTransport), \
                mock.patch.object(http.client, "HTTPSConnection", _NoTransport):
            # CassetteError, not (False, []): the unmatched fallback call was
            # swallowed inside consultar(), so the exit check owns the red.
            with self.assertRaises(_vcr.CassetteError):
                _consultar()
        self.assertEqual(made, [])


class TestUnmatchedCallNeverHitsNetwork(unittest.TestCase):
    """Point SISA_URL at a REACHABLE local server, force a call replay never
    recorded, and assert the server counted zero connections —
    CassetteError must be what stops the call, not an unreachable host
    silently swallowing it (same proof test_hsi_cassette.py's own pilot
    established for slice 5)."""

    def setUp(self):
        self.hits = []
        hits = self.hits

        class _CountingHandler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                hits.append(self.path)
                self.send_response(200)
                self.end_headers()

        self.server = HTTPServer(("127.0.0.1", 0), _CountingHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = "http://127.0.0.1:%d" % self.server.server_address[1]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_unmatched_path_raises_and_never_reaches_the_server(self):
        import urllib.request
        # assertRaises wraps the WHOLE replay context: the player fails an
        # unmatched call twice — at the call site, and (because a caller
        # may swallow the ordinary exception) again from __exit__.
        with self.assertRaises(_vcr.CassetteError):
            with _vcr.replay("puco", "consulta_ok"):
                req = urllib.request.Request(
                    self.base_url + "/sisa/services/rest/puco/00000000",
                    data=b"{}", method="POST")
                urllib.request.urlopen(req)
        self.assertEqual(self.hits, [])

    def test_missing_scenario_raises_before_any_call(self):
        import urllib.request
        with self.assertRaises(_vcr.CassetteError):
            with _vcr.replay("puco", "no_such_scenario"):
                req = urllib.request.Request(
                    self.base_url + "/sisa/services/rest/puco/99887766",
                    data=b"{}", method="POST")
                urllib.request.urlopen(req)
        self.assertEqual(self.hits, [])


if __name__ == "__main__":
    unittest.main()
