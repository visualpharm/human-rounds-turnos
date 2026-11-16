"""test_meta_cassette.py — Area 3 (record-replay cassettes) slice 6: replay-
mode tests for the Meta WhatsApp Cloud API pilot cassette (tests/cassettes/
meta/enviar_texto_ok.json), mechanical repeat of test_hsi_cassette.py's
slice-5 pattern against otp_send.py's `_enviar_meta` — the `connectors/meta/
adapter.py` capability's implementation (see that adapter's docstring and
tests/cassettes/meta/enviar_texto_ok.json's own "source" note for why this
cassette is hand-authored rather than genuinely recorded, unlike PUCO/
RENAPER's slice-6 cassettes).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_*.py"

No network, no live Meta Graph API — `_enviar_meta` never touches the DB or
demo_installation state beyond the guard it already has (test_whatsapp_chat.py's
existing suite covers the inbound-webhook/provider-selection flow around it;
this file is specifically about the HTTP-shape cassette layer, one level
below that).
"""

import json
import os
import shutil
import subprocess
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

from app.notifications import otp_send   # noqa: E402
# _vcr.py loaded by file path, not `sys.path.insert(CASSETTES_DIR) + import
# _vcr` — see test_hsi_cassette.py's own comment on this same block for why
# (putting tests/cassettes/ on sys.path exposes its per-connector
# subdirectories as importable top-level namespace packages, e.g. `puco`/
# `renaper`, since Tier 2 batch L retired the repo-root shims of those
# names). Registered in sys.modules so `mock.patch("_vcr_meta.<attr>", ...)`
# string targets in this file keep resolving it.
import importlib.util as _importlib_util  # noqa: E402
_vcr_spec = _importlib_util.spec_from_file_location('_vcr_meta', os.path.join(CASSETTES_DIR, "_vcr.py"))
_vcr = _importlib_util.module_from_spec(_vcr_spec)
sys.modules['_vcr_meta'] = _vcr  # unique per file — see comment above; a shared "_vcr" key would let one
# file's mock.patch("_vcr...") land on ANOTHER file's _vcr module object
_vcr_spec.loader.exec_module(_vcr)

CASSETTE_PATH = os.path.join(CASSETTES_DIR, "meta", "enviar_texto_ok.json")
FIELDS_PATH = os.path.join(CASSETTES_DIR, "meta", "fields.py")

# Same fixture destino test_whatsapp_chat.py's own suite already uses.
_DESTINO = "5492255111222"
_TEXTO = "Tu turno de Cardiología es el 9 de julio a las 09:00."


def _enviar():
    with _env.patched_env(META_WA_TOKEN="meta-test-token", META_WA_PHONE_NUMBER_ID="999999999999999",
                          META_WA_GRAPH_VERSION="v25.0"):
        with _vcr.replay("meta", "enviar_texto_ok"):
            return otp_send._enviar_meta(_DESTINO, _TEXTO)


class TestEnviarMetaReplay(unittest.TestCase):
    """The pilot acceptance bar, same shape as slice 5's HSI test: replay
    answers _enviar_meta() with the recorded send confirmation, no network
    involved."""

    def test_replay_confirms_the_send(self):
        self.assertTrue(_enviar())

    def test_replay_is_deterministic_across_repeated_runs(self):
        first = _enviar()
        for _ in range(2):
            self.assertEqual(_enviar(), first)


class _ScratchCassetteCase(unittest.TestCase):
    """Copies the real pilot cassette into a scratch dir so a test can delete
    or hand-edit it without ever touching the committed fixture on disk —
    same pattern test_hsi_cassette.py uses."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="meta_cassette_test_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        # A failed send records Meta's last error on the data volume (otp_send.meta_estado): keep it in the
        # scratch dir, never in the checkout's data/ (the subprocess probe inherits it too).
        _env.set_env(self, TURNOS_DATA_DIR=os.path.join(self.tmpdir, "data"))
        meta_dir = os.path.join(self.tmpdir, "meta")
        os.makedirs(meta_dir)
        shutil.copy(CASSETTE_PATH, os.path.join(meta_dir, "enviar_texto_ok.json"))
        shutil.copy(FIELDS_PATH, os.path.join(meta_dir, "fields.py"))
        patcher = mock.patch(
            "_vcr_meta._cassette_dir", lambda connector: os.path.join(self.tmpdir, connector))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.scratch_cassette = os.path.join(meta_dir, "enviar_texto_ok.json")

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
            _enviar()


class TestCorruptedCassette(_ScratchCassetteCase):
    """A hand-edited response body must flip the send from True to False —
    proves _enviar_meta()'s own parsing (`messages[0].id`) is actually
    exercised, not incidentally passing."""

    def test_missing_message_id_flips_the_send_to_false(self):
        data = self._load()
        send = data["interactions"][0]
        self.assertEqual(send["path"], "/v25.0/999999999999999/messages")
        send["response"]["body"]["messages"] = []
        self._save(data)
        self.assertFalse(_enviar())

    def test_error_status_flips_the_send_to_false(self):
        data = self._load()
        send = data["interactions"][0]
        send["response"]["status"] = 401
        send["response"]["body"] = {
            "error": {"message": "Invalid OAuth access token", "code": 190}}
        self._save(data)
        # _enviar_meta() catches HTTPError and returns False, never raises —
        # same best-effort contract as a real Graph API auth failure.
        self.assertFalse(_enviar())


class TestUnmatchedCallNeverHitsNetwork(unittest.TestCase):
    """Point the Graph endpoint at a REACHABLE local server, force a call
    replay never recorded, and assert the server counted zero connections —
    CassetteError must be what stops the call, not an unreachable host
    silently swallowing it."""

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
            with _vcr.replay("meta", "enviar_texto_ok"):
                req = urllib.request.Request(
                    self.base_url + "/v25.0/000000000000000/messages",
                    data=b"{}", method="POST")
                urllib.request.urlopen(req)
        self.assertEqual(self.hits, [])

    def test_missing_scenario_raises_before_any_call(self):
        import urllib.request
        with self.assertRaises(_vcr.CassetteError):
            with _vcr.replay("meta", "no_such_scenario"):
                req = urllib.request.Request(
                    self.base_url + "/v25.0/999999999999999/messages",
                    data=b"{}", method="POST")
                urllib.request.urlopen(req)
        self.assertEqual(self.hits, [])


# The one-shot script TestReplayShutdownIsClean runs in a REAL subprocess: the
# defect it guards (an HTTPError swallowed unclosed surfacing as an "Exception
# ignored" deallocator traceback) only manifests at interpreter shutdown, so
# it can't be asserted in-process. Mirrors this file's own _vcr-by-path load;
# the scratch cassette dir arrives via TURNOS_TEST_SCRATCH_CASSETTES.
_SHUTDOWN_PROBE = """\
import importlib.util, os, sys
sys.path.insert(0, os.getcwd())
os.environ["TURNOS_FAKE_AI"] = "1"
os.environ["TURNOS_SKIP_HSI"] = "1"
from app.core import config
config.ENV_PATH = ""
spec = importlib.util.spec_from_file_location(
    "_vcr_sub", os.path.join(os.getcwd(), "tests", "cassettes", "_vcr.py"))
vcr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vcr)
scratch = os.environ["TURNOS_TEST_SCRATCH_CASSETTES"]
vcr._cassette_dir = lambda connector: os.path.join(scratch, connector)
from app.notifications import otp_send
with vcr.replay("meta", "enviar_texto_ok"):
    ok = otp_send._enviar_meta("5492255111222", "hola")
print("RESULT=%r" % ok)
"""

class TestReplayShutdownIsClean(_ScratchCassetteCase):
    """Regression gate for the 2026-08-09 finding: cassette replay of a
    caught 401 exited 0 but left the HTTPError's body open, and Python 3.14
    printed an ignored deallocator traceback at interpreter shutdown
    (`_TemporaryFileCloser.__del__` → ResourceWarning; same class of noise
    as `ConnectionPool.__del__`'s PythonFinalizationError, whose own gate
    lives in test_dbtest_run_isolation.py). A nominally green gate must not
    hide an unclosed response or thread — so this asserts the WHOLE stderr
    of a real subprocess is free of those markers, not just the exit code."""

    def test_caught_401_replay_exits_with_marker_free_stderr(self):
        data = self._load()
        send = data["interactions"][0]
        send["response"]["status"] = 401
        send["response"]["body"] = {
            "error": {"message": "Invalid OAuth access token", "code": 190}}
        self._save(data)
        env = dict(os.environ)
        env.update({
            "TURNOS_TEST_SCRATCH_CASSETTES": self.tmpdir,
            "META_WA_TOKEN": "meta-test-token",
            "META_WA_PHONE_NUMBER_ID": "999999999999999",
            "META_WA_GRAPH_VERSION": "v25.0",
        })
        out = subprocess.run(
            [sys.executable, "-W", "error::ResourceWarning", "-c",
             _SHUTDOWN_PROBE],
            capture_output=True, text=True, cwd=ROOT, env=env, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("RESULT=False", out.stdout,
                      "the 401 must still read as a failed send")
        for marker in ("PythonFinalizationError", "Exception ignored",
                       "Traceback", "ResourceWarning"):
            self.assertNotIn(marker, out.stderr,
                             "interpreter shutdown must be silent, got:\n"
                             + out.stderr)


if __name__ == "__main__":
    unittest.main()
