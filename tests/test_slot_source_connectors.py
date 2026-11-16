"""Phase 6b: the slot_source connectors (msd, webturno) — manifests, adapters,
and (post Tier 2 batch L, 2026-08-07) proof the repo-root compat shims
(`msd_data.py`, `webturno.py`) are gone and direct `connectors.<id>.*`
imports carry the same monkeypatch story the shims used to guarantee."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


class TestMsdShimsRetiredAndManifest(unittest.TestCase):
    def test_msd_data_has_no_root_module(self):
        with self.assertRaises(ImportError):
            __import__("msd_data")

    def test_dumps_paths_are_repo_root_relative_not_connector_dir(self):
        from app.connectors.msd import msd_data
        # Other tests temporarily replace the mutable lookup order. Rebuild
        # the declared production paths so this structural assertion cannot
        # depend on test execution order.
        declared = [
            os.path.join(msd_data._HERE, "data", "msd_turnos.json"),
            msd_data.sitedata.path("msd_turnos.json"),
        ]
        for path in declared:
            self.assertNotIn(os.path.join("connectors", "msd"), path)
        self.assertEqual(msd_data._DUMPS, declared)
        self.assertTrue(declared[0].endswith(os.path.join("data", "msd_turnos.json")))

    def test_manifest_matches_capability(self):
        from app import connectors
        manifest = connectors.load_manifest("msd")
        self.assertEqual(manifest["capability"], connectors.SLOT_SOURCE)

    def test_adapter_degrades_to_empty_without_a_dump(self):
        from app.connectors.msd.adapter import MsdConnector
        conn = MsdConnector()
        self.assertEqual(conn.slots_for_service({"centro": "no-such-center"}), {})
        self.assertEqual(conn.slots_for_service("not-a-dict"), {})


class TestWebturnoShimRetiredAndManifest(unittest.TestCase):
    def test_webturno_has_no_root_module(self):
        with self.assertRaises(ImportError):
            __import__("webturno")

    def test_runtime_config_lookup_is_canonical_and_safe(self):
        from app.connectors.webturno import webturno
        env = webturno._load_env()
        # `_load_env()` delegates to core.config; this asserts only its public
        # shape and the independent repo-root path used by the CLI output.
        self.assertTrue(os.path.isdir(webturno._ROOT))
        # repo-root sanity marker: asgi_main.py is the surviving entrypoint
        # (server.py retired with the legacy topology, final wave).
        self.assertTrue(os.path.isfile(os.path.join(webturno._ROOT, "app", "asgi_main.py")))
        self.assertIsInstance(env, dict)

    def test_manifest_matches_capability_but_not_the_default_msd_choice(self):
        from app import connectors
        manifest = connectors.load_manifest("webturno")
        self.assertEqual(manifest["capability"], connectors.SLOT_SOURCE)
        # Two connectors implement slot_source; neither is "active" by
        # default (no CONNECTOR_SLOT_SOURCE configured for this deployment).
        found_ids = {m["id"] for m in connectors.connectors_for_capability(connectors.SLOT_SOURCE)}
        self.assertEqual(found_ids, {"msd", "webturno"})
        self.assertIsNone(connectors.active_connector_id(connectors.SLOT_SOURCE))

    def test_adapter_degrades_to_empty_without_a_clinic_token(self):
        from app.connectors.webturno.adapter import WebturnoConnector
        conn = WebturnoConnector()
        self.assertEqual(conn.slots_for_service({}), {})
        self.assertEqual(conn.slots_for_service(None), {})


if __name__ == "__main__":
    unittest.main()
