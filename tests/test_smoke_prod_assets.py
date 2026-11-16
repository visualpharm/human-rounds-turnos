"""Run the deployment smoke against real root and nested asset bytes, offline."""

import contextlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from app.core import static_fingerprint
from scripts import smoke_prod


class TestSmokeProdAssets(unittest.TestCase):
    def run_smoke(self, *, corrupt=False, cache="public, max-age=31536000, immutable",
                  status=200, admin_css_served=True):
        root = Path(smoke_prod.ROOT)
        # Independent disk paths: do not use the production path resolver to
        # manufacture the expected responses for the code under test.
        files = {
            "human-rounds.css": root / "static/root/human-rounds.css",
            "platform/i18n.mjs": root / "static/platform/i18n.mjs",
        }
        # Registered, linked only behind the panel login — never on the home.
        panel_only = {"admin-direct.css": root / "static/root/admin-direct.css"}
        with (
            patch.object(static_fingerprint, "CLIENT_LOADED", frozenset(files)),
            patch.object(static_fingerprint, "MIGRATED", frozenset(files) | frozenset(panel_only)),
            patch.object(static_fingerprint, "_module_assets", return_value=frozenset()),
        ):
            manifest = static_fingerprint.manifest()
            home = ("<script>window.SANA_STATIC=" + json.dumps(manifest)
                    + ";</script>").encode()
            self.assertNotIn(b"admin-direct", home)
            responses = {manifest[name]: path.read_bytes() for name, path in files.items()}
            nested_url = manifest["platform/i18n.mjs"]
            if corrupt:
                responses[nested_url] += b"stale-build"
            admin_css_url = static_fingerprint.url("admin-direct.css")
            responses[admin_css_url] = panel_only["admin-direct.css"].read_bytes()

            def fetch_headers(base, ref, auth):
                if ref == admin_css_url and not admin_css_served:
                    # The previous container does not know this hash.
                    return 404, {}, b"Not Found"
                return status, {"cache-control": cache}, responses[ref]

            output = io.StringIO()
            with (
                patch.object(smoke_prod.sys, "argv", ["smoke_prod.py", "http://offline.test"]),
                patch.object(smoke_prod, "SECURITY", {}),
                patch.object(smoke_prod, "PAGES_200", []),
                patch.object(smoke_prod, "consistencia", return_value=(0, "consistent")),
                patch.object(smoke_prod, "fetch", return_value=(200, home)),
                patch.object(smoke_prod, "fetch_con_headers", side_effect=fetch_headers),
                # Offline: the browser half of the smoke has its own tests.
                patch.object(smoke_prod.smoke_layout, "listing_layout", return_value=(0, [])),
                contextlib.redirect_stdout(output),
            ):
                result = smoke_prod.main()
            return result, output.getvalue()

    def test_root_and_nested_platform_bytes_pass(self):
        result, output = self.run_smoke()
        self.assertEqual(result, 0, output)
        self.assertIn("platform/i18n.mjs", output)
        self.assertIn("human-rounds.css", output)
        self.assertIn("admin-direct.", output)

    def test_previous_build_of_panel_only_css_fails(self):
        # 2026-11-03: a deploy that changed only the panel's CSS passed the
        # smoke while the previous build was still served — the home never
        # links admin-direct.css. Every registered asset is checked now.
        result, output = self.run_smoke(admin_css_served=False)
        self.assertEqual(result, 1, output)
        self.assertIn("[FALLA] asset fingerprinted /static/admin-direct.", output)

    def test_changed_nested_bytes_fail(self):
        result, output = self.run_smoke(corrupt=True)
        self.assertEqual(result, 1, output)
        self.assertIn("[FALLA] asset fingerprinted /static/platform/i18n.", output)

    def test_wrong_cache_policy_fails(self):
        result, output = self.run_smoke(cache="no-store")
        self.assertEqual(result, 1, output)

    def test_non_success_response_fails_even_with_matching_bytes(self):
        result, output = self.run_smoke(status=404)
        self.assertEqual(result, 1, output)
