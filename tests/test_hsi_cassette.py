"""test_hsi_cassette.py — Area 3 (record-replay cassettes) slice 5: replay-
mode tests for the HSI pilot cassette (tests/cassettes/hsi/push_turno_ok.json),
proving tests/cassettes/_vcr.py's cassette engine actually drives the
assertion rather than incidentally passing.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_*.py"

No network, no live HSI, no Postgres writes — push_turno() itself never
touches the DB (the callers that push a REAL booking, e.g. booking_requests._
push_hsi, are covered by test_hsi_resync.py / test_booking_requests.py with
their own module-level fakes; this file is specifically about the HTTP-shape
cassette layer, one level below those).
"""

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
_env.process_set(TURNOS_FAKE_AI="1")

from app.connectors.hsi import hsi_client  # noqa: E402
# _vcr.py loaded by file path, NOT `sys.path.insert(CASSETTES_DIR) + import _vcr`:
# tests/cassettes/ holds a bare subdirectory per connector (puco/, renaper/, ...)
# with no __init__.py, so putting that directory on sys.path makes those
# subdirectories resolve as importable top-level namespace packages named
# `puco`/`renaper` — harmless while the repo-root compat shims of the same
# name existed (a real module always wins the lookup over a namespace
# package), but silently masks their absence since Tier 2 batch L retired
# those shims (2026-08-07). Same rationale _connector_fields() below already
# used for fields.py. Registered in sys.modules so `mock.patch("_vcr.<attr>",
# ...)` string targets in this file keep resolving it.
import importlib.util as _importlib_util  # noqa: E402
_vcr_spec = _importlib_util.spec_from_file_location('_vcr_hsi', os.path.join(CASSETTES_DIR, "_vcr.py"))
_vcr = _importlib_util.module_from_spec(_vcr_spec)
sys.modules['_vcr_hsi'] = _vcr  # unique per file — see comment above; a shared "_vcr" key would let one
# file's mock.patch("_vcr...") land on ANOTHER file's _vcr module object
_vcr_spec.loader.exec_module(_vcr)

CASSETTE_PATH = os.path.join(CASSETTES_DIR, "hsi", "push_turno_ok.json")
FIELDS_PATH = os.path.join(CASSETTES_DIR, "hsi", "fields.py")

# Same inputs the pilot cassette was recorded for (docs/HSI-API-NOTES.md's own
# "Verified end-to-end" example) — see tests/cassettes/hsi/push_turno_ok.json's
# "source" note for why this is a same-shape stub recording, not a live one.
_ARGS = dict(dni="99887766", nombre="JUAN", apellido="PRUEBA",
             nacimiento_iso="1980-05-05", sexo="M", especialidad="Cardiología",
             when_iso="2026-07-09T09:00:00")


def _push():
    # Dozens of other test modules set TURNOS_SKIP_HSI=1 at import time and
    # never restore it (test_hsi_resync.py's own docstring warns about this:
    # "otros módulos de test setean TURNOS_SKIP_HSI=1 a nivel proceso") —
    # `unittest discover` runs every module's top-level code during
    # collection, before any test executes, so by the time OUR tests run the
    # env is "1" regardless of what this module did at import time. Pop/
    # restore per call, the same pattern test_hsi_resync.py and
    # test_hsi_resolver.py already use.
    with _env.patched_env(TURNOS_SKIP_HSI=None), _vcr.replay("hsi", "push_turno_ok"):
        return hsi_client.push_turno(**_ARGS)


class TestPushTurnoReplay(unittest.TestCase):
    """The pilot acceptance bar from the design's execution slice 5: replay
    answers push_turno() with the recorded booking, no network involved."""

    def test_replay_returns_the_recorded_booking(self):
        result = _push()
        self.assertEqual(result["estado"], "ok")
        self.assertEqual(result["appointment_id"], 2)
        self.assertEqual(result["patient_id"], 136)
        self.assertEqual(result["diary_id"], 1)
        self.assertEqual(result["specialty"], "Cardiología")
        self.assertEqual(result["date"], "2026-07-09")
        self.assertEqual(result["hour"], "09:00:00")
        self.assertFalse(result["slot_changed"])
        self.assertFalse(result["sobreturno"])

    def test_replay_is_deterministic_across_repeated_runs(self):
        # A fresh _vcr.replay() context rebuilds its match queues from the
        # committed file every time — nothing carries state between calls, so
        # this must be byte-identical on the 1st, 2nd, and 3rd call alike.
        first = _push()
        for _ in range(2):
            self.assertEqual(_push(), first)


class _ScratchCassetteCase(unittest.TestCase):
    """Copies the real pilot cassette into a scratch dir so a test can delete
    or hand-edit it without ever touching the committed fixture on disk."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="hsi_cassette_test_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        hsi_dir = os.path.join(self.tmpdir, "hsi")
        os.makedirs(hsi_dir)
        shutil.copy(CASSETTE_PATH, os.path.join(hsi_dir, "push_turno_ok.json"))
        shutil.copy(FIELDS_PATH, os.path.join(hsi_dir, "fields.py"))
        patcher = mock.patch(
            "_vcr_hsi._cassette_dir", lambda connector: os.path.join(self.tmpdir, connector))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.scratch_cassette = os.path.join(hsi_dir, "push_turno_ok.json")

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
            _push()


class TestCorruptedCassette(_ScratchCassetteCase):
    """A hand-edited response body must flip the test red — proves the
    caller's parsing is actually exercised, not incidentally passing
    (Area 3's own test plan, tier3-harness-design.md)."""

    def test_corrupted_appointment_id_breaks_the_booking(self):
        data = self._load()
        booking = data["interactions"][-1]
        self.assertEqual(booking["path"],
                         "/api/institutions/1/medicalConsultations/appointments")
        booking["response"]["body"] = "not-an-id"
        self._save(data)
        with self.assertRaises(hsi_client.HsiError):
            _push()

    def test_corrupted_diary_specialty_breaks_the_match(self):
        data = self._load()
        diaries = data["interactions"][4]
        self.assertEqual(diaries["path"],
                         "/api/institutions/1/medicalConsultations/diary")
        diaries["response"]["body"][0]["clinicalSpecialtyName"] = "Otra especialidad"
        self._save(data)
        with self.assertRaises(hsi_client.HsiError):
            _push()


class TestUnmatchedCallNeverHitsNetwork(unittest.TestCase):
    """The concrete proof Area 3's test plan asks for: point HSI_URL at a
    REACHABLE local server, force a call replay never recorded, and assert
    the server counted zero connections — CassetteError must be what stops
    the call, not an unreachable host silently swallowing it."""

    def setUp(self):
        self.hits = []
        hits = self.hits

        class _CountingHandler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
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
            with _vcr.replay("hsi", "push_turno_ok"):
                req = urllib.request.Request(self.base_url + "/api/does/not/exist")
                urllib.request.urlopen(req)
        self.assertEqual(self.hits, [])

    def test_missing_scenario_raises_before_any_call(self):
        import urllib.request
        with self.assertRaises(_vcr.CassetteError):
            with _vcr.replay("hsi", "no_such_scenario"):
                req = urllib.request.Request(self.base_url + "/api/auth", method="POST")
                urllib.request.urlopen(req)
        self.assertEqual(self.hits, [])


if __name__ == "__main__":
    unittest.main()
