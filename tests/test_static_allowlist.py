"""test_static_allowlist.py — the static fallback serves an explicit list, nothing else (2026-11-12).

Until this change the terminal fallback served any file of the checkout whose extension looked
public: the raw source of templates (`/templates/about.html`), test files
(`/test/e2e/lib/fixtures.mjs`), story fixtures, `core/static_recover.js`. It now serves only what
the site uses:

  /static/..., /vendor/...          the public asset trees
  /webdata/..., /data/...           the data packs and the installation's public files (own rules)
  /<root web asset>                 core/paths.py ROOT_WEB_ASSETS (`/admin-app.js`, `/logo.png`)
  /modules/<id>/<file>              only a module asset its module.json declares

Everything else is 404 — source, tests and documents included, whether or not the file exists, so
the answer no longer tells a scanner which files the checkout has; inside the asset trees too, a
document or a source (`static/platform/AGENTS.md`, a vendored kit's `.py`/`.ts`/`.toml`) is not served. A dot segment (`/.env`,
`/x/.git/config`) stays 403, and so does a directory of the asset trees.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1")

from app.core import app_modules, paths  # noqa: E402
from app.services import static_assets  # noqa: E402


def outcome(path):
    result = static_assets.resolve_static_request(path)
    try:
        return type(result).__name__
    finally:
        if isinstance(result, static_assets.StaticFile):
            result.close()


class TestStaticAllowlist(unittest.TestCase):
    def test_source_tests_and_documents_are_not_found(self):
        for path in ("/templates/about.html", "/templates/admin/base.html",
                     "/test/e2e/lib/fixtures.mjs", "/stories/navigation-fixture.mjs",
                     "/core/static_recover.js", "/core/db.py", "/asgi_main.py", "/app.py",
                     "/Dockerfile", "/README.md", "/docs/", "/docs/modules.md",
                     "/playwright.config.mjs", "/extensions/hsi-humanizador/README.md",
                     "/modules/intake/admin_consult.html", "/modules/turnos/study.py",
                     "/packs/global/sitio.json", "/charts/human-rounds/Chart.yaml",
                     "/no-such-file.js", "/no-such-dir/x.css",
                     # documents and sources kept inside the asset trees
                     "/static/platform/AGENTS.md", "/static/fonts/README.md",
                     "/vendor/usability-kit/python/usability_kit/__init__.py",
                     "/vendor/usability-kit/python/pyproject.toml",
                     "/vendor/zxing-wasm/es/bindings/barcodeFormat.d.ts",
                     # a root web asset has one URL, its root one (the file sits in static/root/)
                     "/static/root/admin-app.js", "/static/root/tokens.css"):
            with self.subTest(path=path):
                self.assertEqual(outcome(path), "StaticNotFound")

    def test_hidden_paths_stay_forbidden(self):
        for path in ("/.env", "/.git/config", "/static/.secret.js", "/foo/.env", "/data/ordenes/"):
            with self.subTest(path=path):
                self.assertEqual(outcome(path), "StaticDenied")

    def test_the_site_assets_are_served(self):
        for path in ("/admin-app.js", "/admin-direct.css", "/human-rounds.css", "/tokens.css",
                     "/logo.png", "/logo.svg", "/pinamar-emblema.svg", "/static/favicon.svg",
                     "/static/platform/nav.mjs", "/vendor/lottie/lottie_light.min.js"):
            with self.subTest(path=path):
                self.assertEqual(outcome(path), "StaticFile")

    def test_every_declared_module_asset_is_served_and_nothing_else_of_the_module(self):
        declared = sorted(app_modules.module_asset_paths())
        self.assertTrue(declared)
        for name in declared:
            with self.subTest(name=name):
                self.assertEqual(outcome("/" + name), "StaticFile")
        self.assertEqual(outcome("/modules/turnos/module.json"), "StaticNotFound")

    def test_every_root_web_asset_is_allowed_and_only_those(self):
        for name in sorted(paths.ROOT_WEB_ASSETS):
            self.assertTrue(static_assets.is_public_static("/" + name), name)
        for path in ("/nav.js", "/format.mjs", "/foto-1.jpg", "/about.html", "/x/y.css"):
            self.assertFalse(static_assets.is_public_static(path), path)


if __name__ == "__main__":
    unittest.main()
