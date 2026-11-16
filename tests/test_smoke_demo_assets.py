"""The demo smoke verifies the served build against the local checkout, offline."""

import json
from pathlib import Path
import unittest
from unittest.mock import patch

from app.core import static_fingerprint
from scripts import smoke_demo

IMMUTABLE = "public, max-age=31536000, immutable"


class TestSmokeDemoBuildMatch(unittest.TestCase):
    def run_build_match(self, *, corrupt=None, cache=IMMUTABLE, status=200,
                        manifest_override=None, home_status=200):
        root = Path(smoke_demo.ROOT)
        # Independent disk paths, not the production resolver.
        files = {
            "human-rounds.css": root / "static/root/human-rounds.css",
            "platform/i18n.mjs": root / "static/platform/i18n.mjs",
        }
        module_name = "modules/intake/entrevista.css"
        with (
            patch.object(static_fingerprint, "CLIENT_LOADED", frozenset(files)),
            patch.object(static_fingerprint, "MIGRATED", frozenset(files)),
            patch.object(static_fingerprint, "_module_assets",
                         return_value=frozenset({module_name})),
        ):
            manifest = static_fingerprint.manifest()
            shown = manifest if manifest_override is None else manifest_override
            home = ("<script>window.SANA_STATIC=" + json.dumps(shown)
                    + ";</script>").encode()
            responses = {manifest[name]: path.read_bytes() for name, path in files.items()}
            module_url = static_fingerprint.module_url(module_name)
            responses[module_url] = (root / "app" / module_name).read_bytes()
            if corrupt == "module":
                responses[module_url] += b"stale-build"
            elif corrupt == "platform":
                responses[manifest["platform/i18n.mjs"]] += b"stale-build"
            fetched = []

            def fetch_asset(base, path):
                fetched.append(path)
                return status, {"cache-control": cache}, responses[path]

            with (
                patch.object(smoke_demo, "fetch", return_value=(home_status, home)),
                patch.object(smoke_demo, "fetch_fingerprinted_asset", side_effect=fetch_asset),
            ):
                fallas, lines = smoke_demo.build_match("http://offline.test")
            return fallas, "\n".join(lines), fetched, module_url

    def test_matching_build_passes_and_probes_module_assets(self):
        fallas, output, fetched, module_url = self.run_build_match()
        self.assertEqual(fallas, 0, output)
        self.assertIn(module_url, fetched)
        self.assertIn("window.SANA_STATIC on home", output)

    def test_stale_module_bytes_fail(self):
        fallas, output, _, module_url = self.run_build_match(corrupt="module")
        self.assertEqual(fallas, 1, output)
        self.assertIn(f"[FALLA] asset fingerprinted {module_url}", output)

    def test_stale_platform_bytes_fail(self):
        fallas, output, _, _ = self.run_build_match(corrupt="platform")
        self.assertEqual(fallas, 1, output)

    def test_mutable_cache_policy_fails(self):
        fallas, output, _, _ = self.run_build_match(cache="no-store")
        self.assertGreater(fallas, 0, output)

    def test_status_200_alone_is_not_enough(self):
        fallas, output, _, _ = self.run_build_match(status=404)
        self.assertGreater(fallas, 0, output)

    def test_manifest_from_an_older_build_fails(self):
        fallas, output, _, _ = self.run_build_match(
            manifest_override={"human-rounds.css": "/static/human-rounds.00000000.css"})
        self.assertGreater(fallas, 0, output)
        self.assertIn("[FAIL] window.SANA_STATIC", output)

    def test_unreachable_home_fails(self):
        fallas, output, fetched, _ = self.run_build_match(home_status=502)
        self.assertEqual(fallas, 1, output)
        self.assertEqual(fetched, [])


if __name__ == "__main__":
    unittest.main()
