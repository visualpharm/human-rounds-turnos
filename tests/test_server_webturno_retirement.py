"""test_server_webturno_retirement.py — pins the server.py retirement slice
that deleted the WebTurno patient-client and owner-tools surface (the
"interfaz de paciente para WebTurno" the module docstring used to describe):

  - GET  /api/whoami /api/availability /api/appointments /api/watch
         /api/watch/state /api/calendar/status /api/confirmation /api/logs
         /api/vault /api/owner/coverage[/*]  and the /app + /panel rewrites
  - POST /api/login /api/refresh /api/book /api/cancel /api/watch
         /api/calendar /api/vault /api/owner/coverage/<doc>
  - DELETE /api/vault
  - Handler helpers: _req_creds, _req_wt, _require_owner, _vault_proxy and
    the seven _handle_* bodies; module helpers: is_owner, make_wt, the
    _AVAIL cache, availability_for, _save_db, watch/coverage/vault
    load/save + their paths/locks, _tail_log, WT, OWNER_DNI/OWNER_PASS,
    REMOTE_URL; the boot-time WebTurno owner login + ops/watcher start in
    main(); the `from app.connectors.webturno import webturno` import.
  - The orphans those routes fed: ops/watcher.py (only server.main()
    started it) and notifications/gmail_confirm.py (only /api/confirmation
    called it).

Why this was dead surface (same reachability argument as the round-1
cobertura slice and the round-2 directorio slice):

  * The ONLY clients were app.html and panel.html, and `patient_web/site_*.py`
    302s /app, /app.html, /panel and /panel.html to "/" (the de-linking
    recorded in docs/architecture/routing-y-modulos.md). Those HTML
    implementations, plus ui-core.js / specialty-browser.js which only they
    loaded, are physically deleted. The public page routes stay as 302s.
  * On the public demo, `ops/demo_installation._blocks_legacy` already
    hard-404s every one of these API routes and 302s the pages — that
    deny-list is untouched by this slice and remains the canonical record
    of the retired surface (rollback keeps the same denial).
  * The surface violated the platform invariant "identity always comes from
    the session, never from the request body/headers" (X-WT-DNI/X-WT-Pass):
    its modern owners are cuentas/accounts + auth (identity), and
    modules/turnos (booking).  The WebTurno integration itself stays owned
    by connectors/webturno (slot-source connector, manage.py conn) — only
    server.py's client of it is retired.

Shape (same as the two prior retirement tests): (1) STATIC — removed
constructs stay out of server.py's source, live symbols stay in;
(2) RUNTIME — the names are gone from the module and the Handler class;
(3) DISPATCH — the retired API paths are claimed by nobody (they fall to
server.py's generic /api/ 404), while /app + /panel stay intercepted with
a 302 to "/" before server.py's own route code; (4) DEMO PARITY — every
retired route is still denied by the demo's own deny-list; (5) DATA PATH —
the availability *snapshot* read path (_load_db → public_medicos /
especialidades_tree) survives: the scraper that WROTE the snapshot died,
the reader that feeds /api/specialties and /<esp>/<name> did not;
(6) ORPHANS — watcher.py / gmail_confirm.py are gone, connectors/webturno
is not; (7) CONNECTOR OWNERSHIP (review follow-up on this slice) — the
connector's metadata/docstrings claim no consumer the retirement deleted,
its _load_env() reads exactly its two declared config_keys.

Ported (native-migration final wave, docs/roadmap/
native-migration-campaign-20260826.md): `server.py`,
`routers/legacy_http_transport.py` and the dark dispatch chain
(`core/routing.py`, `core/module_router.py`) are DELETED — there is no
`server`/`server.Handler` to grep or import and no `routing.dispatch()`
chain to run a `_FakeHandler` through. Classes (1)/(2)'s source/hasattr
checks are retired as moot (no file left to check); classes (3)/(4)/(5)/
(6)/(7) port cleanly against the live composed app — verified empirically,
not assumed: every retired API path still answers the generic JSON 404
(nobody claims it, same observable behavior as before, now produced by
`routers/native_terminal.py`'s catch-all instead of server.py's fallback),
and /app + /panel (+ their .html twins) are THEMSELVES native routes now
(`patient_web/patient_site_router.py` registers all six literal paths
directly with `_redirect_home`), so no gap here — unlike the trailing-
slash regression test_server_directorio_retirement.py found in the
PAGE-redirect family owned by `core/legacy_urls_router.py`.
"""

import importlib.util
import json
import os
import pathlib
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Hermetic: never pull the install's local .env into the test process — set
# BEFORE any import so config loading sees no file (TURNOS_ENV_FILE=/dev/null).
import _env  # noqa: E402
_env.process_set(TURNOS_ENV_FILE="/dev/null")
_env.process_defaults(DATABASE_URL="")  # import/run without requiring Postgres
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")
# the pre-launch gate (site_auth.py) only fires with BOTH set — off here so
# this file never depends on the local .env.
_env.process_set(TURNOS_BASIC_AUTH_USER=None, TURNOS_BASIC_AUTH_PASS=None)
# The demo deny-list is part of what this file pins, but the dispatch tests
# below model the NON-demo (production) configuration.
_env.process_set(TURNOS_DEMO_MODE=None)

from app.patient_web import availability_directory  # noqa: E402

_DIRECTORY_SOURCE = pathlib.Path(ROOT, "app", "patient_web", "availability_directory.py").read_text(encoding="utf-8")

# The retired API surface, method-keyed. /api/owner/coverage is prefix-shaped
# on both methods (a bare GET and a POST /api/owner/coverage/<doc>).
_RETIRED_API = {
    "GET": ["/api/whoami", "/api/availability", "/api/appointments",
            "/api/watch", "/api/watch/state", "/api/calendar/status",
            "/api/confirmation", "/api/logs", "/api/vault",
            "/api/owner/coverage", "/api/owner/coverage/dr-x"],
    "POST": ["/api/login", "/api/refresh", "/api/book", "/api/cancel",
             "/api/watch", "/api/calendar", "/api/vault",
             "/api/owner/coverage/dr-x"],
    "DELETE": ["/api/vault"],
}

_RETIRED_PAGES = ["/app", "/panel"]


class TestLiveWebturnoDataSurfaceOwnedNatively(unittest.TestCase):
    """`server.py` and `routers/legacy_http_transport.py` are both gone
    entirely (test_asgi_main_dark_mode_retirement.py's
    `TestLegacyEntrypointRetired` pins the topology-level deletion), so the
    "removed constructs absent from source" / `hasattr(server, ...)` /
    `hasattr(server.Handler, ...)` checks this slice used to run are now
    moot by construction. What remains provable: the live availability-
    snapshot READ path this slice kept survives, owned by
    patient_web.availability_directory."""

    def test_live_read_path_symbols_owned_by_availability_directory(self):
        for needle in ("def _load_db(", "def _json_cacheado(",
                       "def public_medicos(", "def especialidades_tree(",
                       "def medico_by_url("):
            self.assertIn(needle, _DIRECTORY_SOURCE)

    def test_live_read_path_symbols_importable(self):
        for name in ("public_medicos", "especialidades_tree", "medico_by_url"):
            self.assertTrue(hasattr(availability_directory, name))


class TestRetiredApiRoutesUnclaimedNative(unittest.TestCase):
    """In the composed native app NO router claims the retired API paths:
    every one starts with /api/ and falls to
    `routers/native_terminal.py`'s catch-all, which answers the generic
    JSON 404 for any unclaimed /api/ path. The retired surface answers 404
    instead of scraping WebTurno with header-supplied credentials — same
    observable outcome as dark mode, verified live."""

    @classmethod
    def setUpClass(cls):
        from app.core import config
        config.ENV_PATH = ""
        from app import main as app_module
        from fastapi.testclient import TestClient
        cls.client = TestClient(app_module.app)

    def test_retired_api_paths_fall_to_the_generic_404(self):
        for method, paths in _RETIRED_API.items():
            for path in paths:
                with self.subTest(method=method, path=path):
                    r = self.client.request(method, path, follow_redirects=False)
                    self.assertEqual(r.status_code, 404)
                    self.assertEqual(r.json(), {"error": "not found"})


class TestRetiredPagesStayInterceptedNative(unittest.TestCase):
    """/app, /panel, /directorio (and their .html twins) are native routes
    (`patient_web/patient_site_router.py` registers the literal paths
    directly), 302ing to "/" — the same observable behavior dark mode's
    de-link block produced. Query strings are dropped; no Cache-Control.
    The on-disk implementations are gone."""

    @classmethod
    def setUpClass(cls):
        from app.core import config
        config.ENV_PATH = ""
        from app import main as app_module
        from fastapi.testclient import TestClient
        cls.client = TestClient(app_module.app)

    def test_pages_302_to_home_natively(self):
        for path in ("/app", "/app/", "/app.html", "/app.html?foo=bar",
                     "/panel", "/panel/", "/panel.html", "/panel.html?x=1",
                     "/directorio", "/directorio/", "/directorio.html",
                     "/directorio.html?q=1"):
            with self.subTest(path=path):
                r = self.client.get(path, follow_redirects=False)
                self.assertEqual(r.status_code, 302, msg=path)
                self.assertEqual(r.headers["location"], "/", msg=path)
                self.assertFalse(r.headers.get("cache-control"), msg=path)

    def test_implementation_files_are_physically_gone(self):
        for name in ("app.html", "panel.html", "directorio.html",
                     "ui-core.js", "specialty-browser.js"):
            with self.subTest(name=name):
                self.assertFalse(os.path.exists(os.path.join(ROOT, name)), name)


class TestRetiredClientAssetsStayClosedNative(unittest.TestCase):
    """ui-core.js and specialty-browser.js had no live consumers besides the
    deleted HTML shells. A stale query or resurrected on-disk copy must not
    become a public static hit; slash-shaped paths stay the static deny."""

    @classmethod
    def setUpClass(cls):
        from app.core import config
        config.ENV_PATH = ""
        from app import main as app_module
        from fastapi.testclient import TestClient
        cls.client = TestClient(app_module.app)

    def test_retired_scripts_are_no_store_misses(self):
        for path, expected_status in (
            ("/ui-core.js", 404),
            ("/ui-core.js?v=1", 404),
            ("/ui-core.js/", 404),
            ("/specialty-browser.js", 404),
            ("/specialty-browser.js?v=1", 404),
            ("/specialty-browser.js/", 404),
        ):
            with self.subTest(path=path):
                r = self.client.get(path, follow_redirects=False)
                self.assertEqual(r.status_code, expected_status, msg=path)
                self.assertEqual(r.headers.get("cache-control"), "no-store", msg=path)
                self.assertNotIn("location", r.headers)
                if expected_status == 404 and not path.endswith("/"):
                    self.assertEqual(r.content, b'{"error": "not found"}')


class TestDemoDenyListParity(unittest.TestCase):
    """The public demo's deny-list (ops/demo_installation._blocks_legacy) is
    deliberately untouched by this slice: it keeps hard-404ing every retired
    API route and 302ing the pages, so demo behavior is byte-identical and a
    rollback of server.py re-lands behind the same denial."""

    def test_every_retired_route_still_denied_on_the_demo(self):
        from app.ops import demo_installation
        for method, paths in _RETIRED_API.items():
            for path in paths:
                self.assertTrue(
                    demo_installation._blocks_legacy(path, method),
                    msg=f"{method} {path} must stay in the demo deny-list")
        for path in _RETIRED_PAGES:
            self.assertTrue(demo_installation._blocks_legacy(path, "GET"),
                            msg=path)


class TestAvailabilitySnapshotReadPathSurvives(unittest.TestCase):
    """The scraper that WROTE data/availability.json (availability_for /
    _save_db / webturno.build_availability) is gone; the READER that feeds
    the live directory data path is not.  Synthetic _load_db, same pattern
    as the two prior retirement tests: no live server, no pack dependency."""

    _SYNTHETIC_DB = {
        "medicos": [
            {"nombre": "Dra. Ana Pérez", "especialidad": "Cardiología",
             "clinica": "Clínica X", "online": True,
             "obras_sociales": ["OSDE"]},
        ]
    }

    def setUp(self):
        patcher = patch.object(availability_directory, "_load_db", return_value=self._SYNTHETIC_DB)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_public_medicos_still_reads_the_snapshot(self):
        meds = availability_directory.public_medicos()
        self.assertEqual(len(meds), 1)
        m = meds[0]
        self.assertEqual(availability_directory.medico_by_url(m["esp_slug"], m["slug"]), m)

    def test_especialidades_tree_still_owns_the_grouping(self):
        tree = availability_directory.especialidades_tree()
        self.assertTrue(tree.get("groups"))
        self.assertEqual(tree.get("total_medicos"), 1)


class TestOrphansRetiredOwnersAlive(unittest.TestCase):
    """The two modules only this surface fed are gone; the connector that
    OWNS the WebTurno integration is not."""

    def test_watcher_module_gone(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "app", "ops", "watcher.py")))

    def test_gmail_confirm_module_gone(self):
        self.assertFalse(os.path.exists(
            os.path.join(ROOT, "app", "notifications", "gmail_confirm.py")))

    def test_webturno_connector_still_owned(self):
        # Ownership of the WebTurno integration stays at the connector layer
        # (connectors/webturno) — importable, no network touched at import.
        spec = importlib.util.find_spec("app.connectors.webturno.webturno")
        self.assertIsNotNone(spec)


class TestConnectorOwnershipConsistency(unittest.TestCase):
    """Review follow-up (2026-08-16): the connector's metadata/docstrings
    must not claim consumers the retirement deleted. server.py's WebTurno
    client — the module-scope import, the routes calling Webturno()/
    build_availability()/refresh_doctors(), the boot owner login — is gone,
    so the connector's only live consumer is its own offline CLI (the
    dormant slot_source adapter, adapter.py, still imports and wraps it).
    Its env loader therefore reads exactly its two declared credentials (the
    owner-calendar key OWNER_CALENDAR_ICS went with modules/turnos/calendar_busy,
    removed 2026-11-06 — the connector must not re-adopt it)."""

    _SURFACES = (
        pathlib.Path(ROOT, "app", "connectors", "webturno", "connector.json"),
        pathlib.Path(ROOT, "app", "connectors", "webturno", "__init__.py"),
        pathlib.Path(ROOT, "app", "connectors", "webturno", "webturno.py"),
        pathlib.Path(ROOT, "docs", "connectors.md"),
    )

    def test_metadata_and_docstrings_claim_no_retired_consumer(self):
        # The stale ownership phrases this review found — server.py's
        # module-scope import and the owner/admin calendar-sync surface —
        # must stay out of every surface that describes the connector, and
        # so must literal sole-consumption claims: the CLI is the only LIVE
        # consumer, but the dormant slot_source adapter (adapter.py) still
        # imports and wraps the connector, so "only consumer" is never true.
        for path in self._SURFACES:
            text = path.read_text(encoding="utf-8")
            for needle in ("server.py still", "owner/admin calendar-sync",
                           "owner tooling", "only consumer",
                           "one remaining consumer",
                           "only remaining consumer"):
                self.assertNotIn(
                    needle, text,
                    msg=f"{path.name}: {needle!r} claims a retired consumer")

    def test_manifest_declares_exactly_the_two_credentials(self):
        from app import connectors
        manifest = connectors.load_manifest("webturno")
        self.assertEqual(list(manifest["config_keys"]),
                         ["WEBTURNO_DNI", "WEBTURNO_PASS"])

    def test_load_env_reads_exactly_the_declared_credentials(self):
        # The loader requests (and returns) ONLY the manifest's config_keys —
        # the three keys the deleted server.py surface used to read through
        # here (OWNER_DNI, OWNER_CALENDAR_ICS, SACOTURNO_REMOTE_URL) must
        # not creep back in.
        from app.connectors.webturno import webturno
        manifest = json.loads(
            pathlib.Path(ROOT, "app", "connectors", "webturno", "connector.json")
            .read_text(encoding="utf-8"))
        requested = []

        def record(key):
            requested.append(key)
            return f"synthetic-{key.lower().replace('_', '-')}"

        with patch.object(webturno.config, "get", record):
            env = webturno._load_env()
        self.assertEqual(requested, list(manifest["config_keys"]))
        self.assertEqual(sorted(env), sorted(manifest["config_keys"]))

    def test_rollback_only_public_config_stays_documented(self):
        # .env.example keeps listing the retired keys (commented) — they are
        # the rollback record; this slice corrected only their ownership
        # story, it removed nothing.
        example = pathlib.Path(ROOT, ".env.example").read_text(encoding="utf-8")
        for key in ("OWNER_DNI=", "SACOTURNO_REMOTE_URL=", "WEBTURNO_DNI=", "WEBTURNO_PASS="):
            self.assertIn(f"# {key}", example, msg=key)


if __name__ == "__main__":
    unittest.main()
