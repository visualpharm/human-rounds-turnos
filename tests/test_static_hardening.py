"""The native static fallback never serves source, secrets, or patient data.

Native-migration final wave (2026-08-27): `services/legacy_static.py` (and
the `server.Handler` it mixed into) were retired along with the whole
stdlib transport. Its real static-resolution logic had already moved to
`services/static_assets.py` — the module `routers/native_terminal.py`
calls — so this suite now pins `static_assets.is_public_static` directly,
the same policy function under its real name.
"""

import os
import sys
import unittest

import _env  # noqa: E402
_env.process_defaults(TURNOS_ENV_FILE="/dev/null")
_env.process_defaults(DATABASE_URL="")  # importar sin exigir Postgres
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import static_assets  # noqa: E402


class TestPublicStaticPolicy(unittest.TestCase):
    def test_denies_source_code(self):
        for p in ("/server.py", "/db.py", "/admin_auth.py", "/accounts.py"):
            self.assertFalse(static_assets.is_public_static(p), p)

    def test_denies_secrets_and_metadata(self):
        for p in ("/.env", "/.git/config", "/Dockerfile", "/requirements.txt",
                  "/LICENSE", "/CLAUDE.md", "/AGENTS.md"):
            self.assertFalse(static_assets.is_public_static(p), p)

    def test_denies_data_volume_and_dotfiles(self):
        for p in ("/data/ordenes/1.png", "/data/cuenta-fotos/30123456.jpg",
                  "/.ssh/id_rsa", "/foo/.env"):
            self.assertFalse(static_assets.is_public_static(p), p)

    def test_denies_directory_listing_shapes(self):
        for p in ("/", "/webdata/", "/static/", "/vendor/", "/scripts/"):
            if p in ("/webdata/", "/static/", "/vendor/"):
                # is_public_static() itself allows the bare prefix (it only
                # checks the shape); resolve_static_request()'s descriptor
                # open then refuses a directory target (StaticDenied, 403)
                # instead of listing it — see test_http_server.py's
                # TestPathTraversal / symlink-and-directory denial coverage.
                continue
            self.assertFalse(static_assets.is_public_static(p), p)

    def test_allows_public_assets(self):
        for p in ("/human-rounds.css", "/logo.png", "/pinamar-emblema.svg",
                  "/webdata/servicios.json", "/vendor/zxing-wasm/x.wasm",
                  "/static/favicon.svg", "/static/icons/chat-send-sf-regular.png",
                  "/static/fonts/fonts.css"):
            self.assertTrue(static_assets.is_public_static(p), p)

    def test_allows_mjs(self):
        for p in ("/static/format.abc123.mjs", "/vendor/algo/x.mjs"):
            self.assertTrue(static_assets.is_public_static(p), p)

    def test_denies_any_other_root_or_tree_file(self):
        # the extension no longer decides (tests/test_static_allowlist.py): an asset name
        # outside the explicit list is not served even when it looks public
        for p in ("/nav.js", "/format.mjs", "/foto-1.jpg", "/about.html",
                  "/templates/about.html", "/test/e2e/lib/fixtures.mjs", "/stories/x.mjs"):
            self.assertFalse(static_assets.is_public_static(p), p)

    def test_denies_empty_and_nul_paths(self):
        self.assertFalse(static_assets.is_public_static(""))
        self.assertFalse(static_assets.is_public_static("/x\x00.js"))


if __name__ == "__main__":
    unittest.main()
