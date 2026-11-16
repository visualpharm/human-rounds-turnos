"""Standing contract for the closed route inventory and the offline probe of the composed app.

`tests/_probe_rows.py` holds the inventory (request scenarios, smoke paths, the declared native and
terminal rows) and the probe. What has to hold, and what fails it:

* every row is served by a real route or is declared terminal - a row nobody declared that falls to
  the catch-all fails, and so does a declared terminal row that a route starts to serve (a retired
  `*.js` alias coming back);
* every route of the composition is exactly one registration of its (method, path);
* the catch-all is physically last (also pinned by `test_app_composition`);
* the probe sends exactly the rows it should, with the database and the network unreachable, and
  fails on a connection attempt even when the app swallows the error, on an unexpected server error,
  and on any change of the known offline 503s.

The counters of the inventory are generated (`tests/snapshots.json`, section `census`:
`python3 tests/snapshots.py --write`); the tests below assert what they have to mean.
"""

from collections import Counter
from pathlib import Path
import unittest

import _env
import _probe_rows as probe
import snapshots


ROOT = Path(__file__).resolve().parents[1]


class RouteInventoryTest(unittest.TestCase):
    def _app(self):
        with probe.offline_environment():
            from app import main as app
            return app.app

    def test_sources_are_derived_and_current(self):
        composition = probe.composition_rows()
        parity = probe.parity_rows()
        smoke = probe.smoke_rows()
        self.assertGreater(len(composition), 220)
        self.assertGreater(len(parity), 150)
        self.assertGreater(len(smoke), 15)
        self.assertTrue(all(row.source == "composition" for row in composition))
        self.assertTrue(all(row.source == "route-parity" for row in parity))
        self.assertTrue(all(row.source == "smoke" for row in smoke))

    def test_build_derived_module_assets_are_not_stable_route_rows(self):
        source = (ROOT / "scripts/smoke_demo.py").read_text(encoding="utf-8")
        self.assertIn("fetch_fingerprinted_asset(base, path)", source)
        self.assertNotIn("fetch(base, path)\n        ok = code == 200", source)
        self.assertFalse(any("app/modules/intake/" in row.path for row in probe.smoke_rows()))

    def test_closed_protocol_inventory_is_exact(self):
        self.assertEqual(probe.PROTOCOL_EXCEPTIONS, set())
        self.assertEqual(probe.EXPLICIT_NATIVE_ROWS, {
            ("GET", "/datos"),
            ("GET", "/datos/"),
            ("GET", "/api/demo"),
            ("GET", "/account/demo-access"),
            ("GET", "/admin/demo-doctor-access"),
            ("GET", "/admin/demo-access"),
            ("GET", "/api/whatsapp/webhook"),
            ("POST", "/api/whatsapp/webhook"),
            ("GET", "/api/whatsapp/webhook/"),
            ("POST", "/api/whatsapp/webhook/"),
            ("GET", "/mcp"),
            ("POST", "/mcp"),
            ("DELETE", "/mcp"),
            ("GET", "/api/account/mcp"),
            ("POST", "/api/account/mcp/regenerate"),
            ("POST", "/api/account/mcp/revoke"),
            ("GET", "/mcp/"),
            ("POST", "/mcp/"),
            ("DELETE", "/mcp/"),
            ("GET", "/api/account/mcp/"),
            ("POST", "/api/account/mcp/regenerate/"),
            ("POST", "/api/account/mcp/revoke/"),
            ("POST", "/api/chat/stream"),
            ("POST", "/api/chat/stream/"),
        })
        self.assertTrue({
            ("GET", "/api/whatsapp/webhook//"),
            ("POST", "/api/whatsapp/webhook//"),
            ("GET", "/mcp//"),
            ("POST", "/mcp//"),
            ("DELETE", "/mcp//"),
            ("GET", "/api/account/mcp//"),
            ("POST", "/api/account/mcp/regenerate//"),
            ("POST", "/api/account/mcp/revoke//"),
            ("POST", "/api/chat/stream//"),
        }.issubset(probe.TERMINAL_REPRESENTATIVES))

    def test_live_report_distinguishes_terminal_compatibility(self):
        app = self._app()
        report, classified = probe.build_report(app)
        self.assertEqual(
            set(report["categories"]),
            {"native", "terminal-compatibility", "protocol-exception"},
        )
        self.assertEqual(sum(report["categories"].values()), len(classified))
        # The counters move with every route, so they are generated (tests/snapshots.json,
        # section `census`: python3 tests/snapshots.py --write) instead of typed in here. What they
        # have to mean is asserted from independent sources below.
        self.assertEqual(report["categories"]["protocol-exception"], 0)
        self.assertEqual(report["unique_categories"]["protocol-exception"], 0)
        # Composition rows are exactly the live app's (method, path) pairs, the terminal excluded -
        # not whatever the fixture file claims.
        live = probe.flat_routes(app.routes, [])
        self.assertEqual(
            report["sources"]["composition"],
            sum(len(route.methods or ()) for route in live[:-1]),
        )
        # Terminal-compatibility rows are exactly the declared terminal sets; classification
        # already failed above if one stopped reaching the terminal or another one started to.
        terminal = probe.TERMINAL_REPRESENTATIVES | probe.TERMINAL_SMOKE_ROWS
        self.assertEqual(
            report["categories"]["terminal-compatibility"],
            sum(
                (row.method, probe.request_path(row.path)) in terminal
                for row in probe.all_rows()
                if row.source != "composition"
            ),
        )
        self.assertEqual(report["sources"]["explicit"], len(probe.explicit_rows()))
        self.assertEqual(report["sources"]["route-parity"], len(probe.parity_rows()))
        self.assertEqual(report["sources"]["smoke"], len(probe.smoke_rows()))
        self.assertEqual(
            report,
            snapshots.load()["census"],
            "the route census moved; if that is intended: python3 tests/snapshots.py --write",
        )

    def test_patient_retirement_removes_only_classic_aliases_not_the_live_api(self):
        app = self._app()
        _report, classified = probe.build_report(app)
        composition = {(row.method, row.path) for row in probe.composition_rows()}
        self.assertNotIn(("GET", "/admin-pacientes.js"), composition)
        self.assertNotIn(("GET", "/admin-pacientes.js/"), composition)
        for method, path in (("GET", "/admin/pacientes"), ("GET", "/api/admin/patients")):
            matches = [(row, category) for row, category in classified
                       if row.method == method and row.path == path]
            self.assertTrue(matches, (method, path))
            self.assertTrue(all(category == "native" for _row, category in matches))

    def test_preparations_retirement_preserves_live_screen_and_api(self):
        app = self._app()
        composition = {(row.method, row.path) for row in probe.composition_rows()}
        for path in ("/admin-preparaciones.js", "/admin-preparaciones.js/"):
            self.assertNotIn(("GET", path), composition)
        for path in ("/admin/preparations", "/api/admin/content/preparations"):
            self.assertEqual(
                probe.classify(app, probe.Row("adversarial", 0, "GET", path)), "native", path)
        self.assertIn(("GET", "/api/admin/content/{tipo}"), composition)

    def test_day_queue_retirement_preserves_live_screen_and_api(self):
        app = self._app()
        composition = {(row.method, row.path) for row in probe.composition_rows()}
        for path in ("/admin-cola.js", "/admin-cola.js/"):
            self.assertNotIn(("GET", path), composition)
        for path in ("/admin/cola", "/api/admin/day-queue"):
            self.assertEqual(
                probe.classify(app, probe.Row("adversarial", 0, "GET", path)), "native", path)
            self.assertIn(("GET", path), composition)

    def _assert_family(self, expected, terminal_suffix=None):
        _report, classified = probe.build_report(self._app())
        observed = {
            (row.method, row.path): category
            for row, category in classified
            if (row.method, row.path) in expected
        }
        self.assertEqual(set(observed), expected)
        for pair, category in observed.items():
            self.assertEqual(
                category,
                "terminal-compatibility"
                if terminal_suffix and pair[1].endswith(terminal_suffix) else "native",
                pair,
            )

    def test_demo_routes_are_source_derived_and_native(self):
        self._assert_family({
            ("GET", "/api/demo"),
            ("GET", "/account/demo-access"),
            ("GET", "/admin/demo-doctor-access"),
            ("GET", "/admin/demo-access"),
        })

    def test_whatsapp_routes_are_source_derived_and_native(self):
        self._assert_family({
            ("GET", "/api/whatsapp/webhook"),
            ("POST", "/api/whatsapp/webhook"),
            ("GET", "/api/whatsapp/webhook/"),
            ("POST", "/api/whatsapp/webhook/"),
            ("GET", "/api/whatsapp/webhook//"),
            ("POST", "/api/whatsapp/webhook//"),
        }, "//")

    def test_mcp_routes_are_source_derived_and_native(self):
        self._assert_family({
            ("GET", "/mcp"),
            ("POST", "/mcp"),
            ("DELETE", "/mcp"),
            ("GET", "/api/account/mcp"),
            ("POST", "/api/account/mcp/regenerate"),
            ("POST", "/api/account/mcp/revoke"),
            ("GET", "/mcp/"),
            ("POST", "/mcp/"),
            ("DELETE", "/mcp/"),
            ("GET", "/mcp//"),
            ("POST", "/mcp//"),
            ("DELETE", "/mcp//"),
            ("GET", "/api/account/mcp/"),
            ("GET", "/api/account/mcp//"),
            ("POST", "/api/account/mcp/regenerate/"),
            ("POST", "/api/account/mcp/revoke/"),
            ("POST", "/api/account/mcp/regenerate//"),
            ("POST", "/api/account/mcp/revoke//"),
        }, "//")

    def test_chat_stream_routes_are_source_derived_and_native(self):
        self._assert_family({
            ("POST", "/api/chat/stream"),
            ("POST", "/api/chat/stream/"),
            ("POST", "/api/chat/stream//"),
        }, "//")

    def test_unknown_fallback_fails_closed(self):
        row = probe.Row("adversarial", 0, "POST", "/not-in-the-closed-inventory")
        with self.assertRaisesRegex(probe.CensusError, "unclassified fallback"):
            probe.classify(self._app(), row)

    def test_declared_terminal_row_served_by_a_route_fails(self):
        from fastapi import FastAPI

        app = FastAPI()
        # A retired `*.js` alias coming back as a real route: it was declared terminal.
        app.get("/fallback-census.css")(lambda: "back")
        app.get("/{full_path:path}")(lambda: "terminal")
        row = probe.Row("explicit", 0, "GET", "/fallback-census.css")
        with self.assertRaisesRegex(probe.CensusError, "no longer reaches the native terminal"):
            probe.classify(app, row)

    def test_composition_row_must_be_exactly_one_registration(self):
        from fastapi import FastAPI

        app = FastAPI()
        app.get("/twice")(lambda: "one")
        app.get("/twice")(lambda: "two")
        app.get("/{full_path:path}")(lambda: "terminal")
        row = probe.Row("composition", 0, "GET", "/twice")
        with self.assertRaisesRegex(probe.CensusError, "does not map to one native route"):
            probe.classify(app, row)

    def test_catch_all_is_physically_last(self):
        app = self._app()
        flat = probe.flat_routes(app.routes, [])
        self.assertEqual(flat[-1].path, probe.CATCH_ALL_PATH)
        self.assertNotIn(probe.CATCH_ALL_PATH, [route.path for route in flat[:-1]])

    def test_catch_all_not_last_fails_the_classification(self):
        from fastapi import FastAPI

        app = FastAPI()
        app.get("/{full_path:path}")(lambda: "terminal")
        app.get("/after")(lambda: "unreachable")
        row = probe.Row("composition", 0, "GET", "/after")
        with self.assertRaisesRegex(probe.CensusError, "catch-all is not physically last"):
            probe.classify(app, row)

    def test_disabled_module_route_stays_native_404(self):
        from unittest import mock

        with probe.offline_environment(), _env.patched_env(ENABLED_MODULES="chat,intake"):
            from app.core import config
            config._reset_cache_para_tests()
            from app import main as app
            from fastapi.testclient import TestClient

            try:
                with (
                    mock.patch("app.ops.web_runtime.start_background_services"),
                    mock.patch("app.ops.web_runtime.stop_app_modules"),
                    TestClient(app.app, client=("127.0.0.1", 12345)) as client,
                ):
                    response = client.get("/api/specialties", headers={"Host": "testserver"})
                self.assertEqual(response.status_code, 404)
            finally:
                config._reset_cache_para_tests()


class OfflineProbeTest(unittest.TestCase):
    def test_probe_drives_every_concrete_claimed_row(self):
        with probe.offline_environment():
            from app import main as app
            _report, classified = probe.build_report(app.app)
            result = probe.probe_native_rows(app.app, classified)
        expected = Counter(
            (row.method, row.path) for row, _category in classified
            if row.source in {"route-parity", "smoke", "explicit"}
        )
        self.assertEqual(result["probed"], sum(expected.values()))
        # The requests that were sent are the rows, one for one - a count alone would let one
        # request be skipped and another sent twice.
        self.assertEqual(Counter(result["requests"]), expected)
        self.assertEqual(result["expected_offline_503"], len(probe.EXPECTED_OFFLINE_503_ROWS))

    def test_probe_fails_if_the_known_offline_surface_expands(self):
        from fastapi import FastAPI
        from fastapi.responses import Response

        probe_app = FastAPI()

        @probe_app.get("/native")
        def native():
            return Response(content=probe.EXPECTED_OFFLINE_503_BODY, status_code=503)

        rows = [(probe.Row("route-parity", 0, "GET", "/native"), "native")]
        with self.assertRaisesRegex(probe.CensusError, "unexpected server error 503"):
            probe.probe_native_rows(probe_app, rows)

    def test_probe_rejects_wrong_body_on_a_known_offline_row(self):
        from fastapi import FastAPI
        from fastapi.responses import Response

        probe_app = FastAPI()

        @probe_app.get("/api/intake/t/faketoken")
        def native():
            return Response(content=b"wrong", status_code=503)

        rows = [(probe.Row("route-parity", 0, "GET", "/api/intake/t/faketoken"), "native")]
        with self.assertRaisesRegex(probe.CensusError, "unexpected offline 503 body"):
            probe.probe_native_rows(probe_app, rows)

    def test_probe_fails_loud_on_outbound_network(self):
        import socket
        from fastapi import FastAPI

        probe_app = FastAPI()

        @probe_app.get("/native")
        def native():
            sock = socket.socket()
            try:
                sock.connect(("203.0.113.1", 9))
            finally:
                sock.close()
            return {"ok": True}

        rows = [(probe.Row("route-parity", 0, "GET", "/native"), "native")]
        with self.assertRaisesRegex(probe.CensusError, "attempted outbound network"):
            probe.probe_native_rows(probe_app, rows)

    def test_probe_fails_even_when_the_app_swallows_the_connection_error(self):
        import socket
        from fastapi import FastAPI

        probe_app = FastAPI()

        @probe_app.get("/native")
        def native():
            sock = socket.socket()
            try:
                sock.connect(("203.0.113.1", 9))
            except Exception:  # noqa: BLE001 - the point: the app hides it and answers 200
                pass
            finally:
                sock.close()
            return {"ok": True}

        @probe_app.get("/native-ex")
        def native_ex():
            sock = socket.socket()
            try:
                sock.connect_ex(("203.0.113.1", 9))
            except Exception:  # noqa: BLE001
                pass
            finally:
                sock.close()
            return {"ok": True}

        for path in ("/native", "/native-ex"):
            with self.subTest(path=path):
                rows = [(probe.Row("route-parity", 0, "GET", path), "native")]
                with self.assertRaisesRegex(probe.CensusError, "attempted outbound network"):
                    probe.probe_native_rows(probe_app, rows)

    def test_probe_fails_loud_on_unhandled_server_error(self):
        from fastapi import FastAPI

        probe_app = FastAPI()

        @probe_app.get("/native")
        def native():
            raise RuntimeError("hidden crash")

        rows = [(probe.Row("route-parity", 0, "GET", "/native"), "native")]
        with self.assertRaisesRegex(
            probe.CensusError,
            r"unexpected server error 500 \(raised RuntimeError: hidden crash at .*test_route_probe\.py:\d+ in native\)",
        ):
            probe.probe_native_rows(probe_app, rows)


if __name__ == "__main__":
    unittest.main()
