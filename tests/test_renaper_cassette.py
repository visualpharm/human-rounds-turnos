"""test_renaper_cassette.py — Area 3 (record-replay cassettes) slice 6:
replay-mode tests for the RENAPER pilot cassette (tests/cassettes/renaper/
login_callback_ok.json), mechanical repeat of test_hsi_cassette.py's slice-5
pattern against auth_renaper.py's real OIDC client (`_claims_reales`, the
ONE network-touching path in the identity_verification capability — see
that cassette's "source" note and tests/cassettes/renaper/fields.py's
docstring for why `auth_renaper.py`, not `connectors/renaper/renaper.py`'s
local "stub" provider, is the file under test here).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_*.py"

No network, no live IDP, no Postgres writes — `_claims_reales`/
`_extraer_datos` never touch the DB (test_auth_renaper.py's existing suite
covers the fake-mode/session/cookie flow around them; this file is
specifically about the HTTP-shape cassette layer, one level below that).
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
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
config.ENV_PATH = ""   # deterministic — never read the dev checkout's real .env

from app.cuentas import auth_renaper    # noqa: E402
# _vcr.py loaded by file path, not `sys.path.insert(CASSETTES_DIR) + import
# _vcr` — see test_hsi_cassette.py's own comment on this same block for why
# (putting tests/cassettes/ on sys.path exposes its per-connector
# subdirectories as importable top-level namespace packages, e.g. `puco`/
# `renaper`, since Tier 2 batch L retired the repo-root shims of those
# names — this file's own connector!). Registered in sys.modules so
# `mock.patch("_vcr_renaper.<attr>", ...)` string targets in this file keep
# resolving it.
import importlib.util as _importlib_util  # noqa: E402
_vcr_spec = _importlib_util.spec_from_file_location('_vcr_renaper', os.path.join(CASSETTES_DIR, "_vcr.py"))
_vcr = _importlib_util.module_from_spec(_vcr_spec)
sys.modules['_vcr_renaper'] = _vcr  # unique per file — see comment above; a shared "_vcr" key would let one
# file's mock.patch("_vcr...") land on ANOTHER file's _vcr module object
_vcr_spec.loader.exec_module(_vcr)

CASSETTE_PATH = os.path.join(CASSETTES_DIR, "renaper", "login_callback_ok.json")
FIELDS_PATH = os.path.join(CASSETTES_DIR, "renaper", "fields.py")

_CODE = "test-authorization-code"


def _claims():
    # `_claims_reales(code)` — native-migration campaign, family W3b
    # (docs/roadmap/native-migration-campaign-20260826.md): the `handler`
    # parameter this call used to pass was ALREADY dead by the time it was
    # dropped — `_redirect_uri()`'s only use of it, `site_identity.
    # base_url(handler)`, ignores its argument entirely (reads
    # `TURNOS_PUBLIC_ORIGIN`/the loopback dev origin, never a Host header;
    # see that function's own docstring: "Client-controlled Host headers
    # never select an application link destination"). No fake handler
    # needed here anymore.
    with _env.patched_env({"TURNOS_RENAPER_CLIENT_ID": "test-client-id", "TURNOS_RENAPER_CLIENT_SECRET": "test-client-secret", "TURNOS_FAKE_RENAPER": None}):
        with _vcr.replay("renaper", "login_callback_ok"):
            return auth_renaper._claims_reales(_CODE)


class TestClaimsReplay(unittest.TestCase):
    """The pilot acceptance bar, same shape as slice 5's HSI test: replay
    answers _claims_reales() with the recorded identity, no network
    involved — proving the two-call token+userinfo exchange is walked and
    merged into one `claims` dict correctly.

    The committed cassette's identity fields (dni/given_name/family_name/
    birthdate/email) are PII-scrubbed placeholder tokens, same as any other
    connector's fields.py denylist would force on a live recording (see
    login_callback_ok.json's "source" note) — so these assertions check
    _claims_reales()'s own plumbing (right keys, right non-PII values,
    consistent across replays), NOT `_extraer_datos()`'s DNI-validation
    happy path. That parsing is unit-tested directly (no network, no
    cassette needed) in test_auth_renaper.py; TestCorruptedCassette below
    exercises it against a realistic-shaped SCRATCH-only response instead."""

    def test_replay_returns_the_recorded_claims(self):
        claims = _claims()
        self.assertEqual(claims["sub"], "renaper-test-subject")
        self.assertEqual(claims["dni"], "[documento 1]")
        self.assertEqual(claims["given_name"], "[nombre 1]")
        self.assertEqual(claims["family_name"], "[nombre 2]")
        self.assertEqual(claims["birthdate"], "[nacimiento 1]")
        self.assertEqual(claims["gender"], "F")  # not PII-shaped, left as-is
        self.assertEqual(claims["email"], "[email 1]")

    def test_extraer_datos_rejects_the_scrubbed_placeholder_dni(self):
        # A scrubbed token ("[documento 1]") strips to a single non-DNI-shaped
        # digit — _extraer_datos() must reject it, not silently accept
        # garbage as an identity. (The realistic-shaped happy path is
        # test_corrupted_cassette... below, against a scratch-only copy.)
        self.assertIsNone(auth_renaper._extraer_datos(_claims()))

    def test_replay_is_deterministic_across_repeated_runs(self):
        first = _claims()
        for _ in range(2):
            self.assertEqual(_claims(), first)


class _ScratchCassetteCase(unittest.TestCase):
    """Copies the real pilot cassette into a scratch dir so a test can delete
    or hand-edit it without ever touching the committed fixture on disk —
    same pattern test_hsi_cassette.py uses."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="renaper_cassette_test_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        renaper_dir = os.path.join(self.tmpdir, "renaper")
        os.makedirs(renaper_dir)
        shutil.copy(CASSETTE_PATH, os.path.join(renaper_dir, "login_callback_ok.json"))
        shutil.copy(FIELDS_PATH, os.path.join(renaper_dir, "fields.py"))
        patcher = mock.patch(
            "_vcr_renaper._cassette_dir", lambda connector: os.path.join(self.tmpdir, connector))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.scratch_cassette = os.path.join(renaper_dir, "login_callback_ok.json")

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
            _claims()


class TestCorruptedCassette(_ScratchCassetteCase):
    """A hand-edited response body must flip the test red — proves the
    caller's parsing is actually exercised, not incidentally passing."""

    def test_realistic_userinfo_response_parses_end_to_end(self):
        # The COMMITTED cassette is properly PII-scrubbed (dni/names are
        # placeholder tokens — see login_callback_ok.json's "source" note),
        # which is correct for what gets checked in, but means it can't by
        # itself prove _extraer_datos()'s happy path. This edits the
        # SCRATCH-only copy (never the committed fixture) back to a
        # realistic-shaped response — same category of value as HSI's own
        # test fixtures — to prove the parsing chain genuinely works, per
        # Area 3's acceptance bar ("proves the caller still parses the
        # connector's real, current response shape correctly").
        data = self._load()
        userinfo = data["interactions"][1]
        self.assertEqual(userinfo["path"], "/userinfo/")
        userinfo["response"]["body"].update({
            "dni": "30123456", "given_name": "Ana", "family_name": "Gómez",
            "birthdate": "1990-02-03", "gender": "F", "email": "ana@example.com",
        })
        self._save(data)
        datos = auth_renaper._extraer_datos(_claims())
        self.assertEqual(datos, {
            "dni": "30123456", "nombre": "Ana", "apellido": "Gómez",
            "nacimiento": "1990-02-03", "sexo": "F", "email": "ana@example.com",
        })

    def test_corrupted_userinfo_dni_breaks_extraer_datos(self):
        data = self._load()
        userinfo = data["interactions"][1]
        userinfo["response"]["body"]["dni"] = "not-a-dni"
        self._save(data)
        datos = auth_renaper._extraer_datos(_claims())
        self.assertIsNone(datos)  # format.dni_valid_length() rejects it

    def test_missing_id_token_still_reaches_userinfo(self):
        data = self._load()
        token_call = data["interactions"][0]
        self.assertEqual(token_call["path"], "/token/")
        token_call["response"]["body"].pop("id_token", None)
        self._save(data)
        claims = _claims()
        # No id_token -> _jwt_payload({}) contributes nothing, but the
        # access_token still drives the userinfo call, which is where this
        # scenario's identity actually comes from.
        self.assertEqual(claims["dni"], "[documento 1]")


class TestUnmatchedCallNeverHitsNetwork(unittest.TestCase):
    """Point the token/userinfo URLs at a REACHABLE local server, force a
    call replay never recorded, and assert the server counted zero
    connections — CassetteError must be what stops the call, not an
    unreachable host silently swallowing it."""

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
            with _vcr.replay("renaper", "login_callback_ok"):
                req = urllib.request.Request(
                    self.base_url + "/revoke/", data=b"x", method="POST")
                urllib.request.urlopen(req)
        self.assertEqual(self.hits, [])

    def test_missing_scenario_raises_before_any_call(self):
        import urllib.request
        with self.assertRaises(_vcr.CassetteError):
            with _vcr.replay("renaper", "no_such_scenario"):
                req = urllib.request.Request(
                    self.base_url + "/token/", data=b"x", method="POST")
                urllib.request.urlopen(req)
        self.assertEqual(self.hits, [])


if __name__ == "__main__":
    unittest.main()
