"""test_cache_headers.py — the fingerprinted-asset cache contract.

Regresión de prod 2026-07-28: el 401 del Basic Auth de pre-lanzamiento
(site_auth.py) salía por la centralized `Handler.end_headers()` blanket
override, que sólo miraba la extensión del path — así que un bundle
versionado sin credenciales devolvía 401 con `max-age=31536000, immutable`.
El browser lo guardaba por un año y desde ahí ese asset daba 401 para
siempre, aun con las credenciales correctas (curl con la credencial correcta devolvía 200
mientras Chrome insistía "Authentication Failed").

Native-migration final wave (2026-08-27): `server.py` (and its
`Handler.end_headers()` blanket "status >= 400 -> no-store" override) was
retired along with the whole stdlib transport. There is no single
post-hoc override left — the native ASGI composition sets Cache-Control per
response, explicitly, at each call site — so the `TestCacheControl` class
this file used to carry (a synthetic `_H(path, code)` construction probing
that one function in isolation) has no native analog and is gone. Its real
regressions are covered live, against the composed app, by:
  - `tests/test_native_terminal.py` — static 200/304 cache headers by
    extension/version, dynamic-page `no-cache`, and `no-store` on the
    static-fallback's own 403/404/API-miss paths.
  - `tests/test_site_auth_middleware.py` — the 401-never-caches contract
    itself (`auth/site_auth_middleware.py`'s `_unauthorized_response()` now
    owns the explicit `Cache-Control: no-store` this regression needs).

What DOES survive here is `core/static_fingerprint.py`'s own hashing/
resolution logic (`_hash8()`, `url()`, `manifest()`, …) plus the native
terminal that serves those resolved URLs. The tests drive that real ASGI
boundary; no handler-shaped compatibility transport remains.

Correr:  python3 -m unittest discover -s tests -p "test_cache_headers.py"
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")  # importar sin exigir Postgres
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.core import paths, static_fingerprint  # noqa: E402
from app.routers import native_terminal  # noqa: E402
from app.services import static_assets  # noqa: E402
from _fstime import restore_mtime_after_ctime_moves  # noqa: E402


def _request(path, method="GET"):
    app = FastAPI()
    app.include_router(native_terminal.router)
    with TestClient(app) as client:
        return client.request(method, path, follow_redirects=False)


def _dir_status(path):
    """A directory-shaped probe (`/x.js/`): 403 inside a served asset tree, where the descriptor
    open refuses the directory; 404 elsewhere, where the static allowlist never opens it
    (tests/test_static_allowlist.py)."""
    return 403 if path.startswith(static_assets.PUBLIC_STATIC_PREFIXES) else 404


class TestCacheControlFingerprinted(unittest.TestCase):
    """Tier 4 slice 6 (static_fingerprint.py) — a fingerprinted URL is
    `immutable`-cacheable BY CONSTRUCTION (the name IS the content hash), and
    a stale/forged hash must never get that treatment: `handle()` never
    resolves it as a miss, so the native terminal returns an ordinary static
    404 with `no-store` — proving a
    migrated asset can NEVER be cached under a URL that doesn't match its
    current bytes, closing exactly the class of bug `?v=` drift used to
    risk."""

    def test_url_actual_es_inmutable(self):
        response = _request(static_fingerprint.url("platform/nav.mjs"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"),
                         "public, max-age=31536000, immutable")
        with open(os.path.join(ROOT, "static/platform/nav.mjs"), "rb") as f:
            self.assertEqual(response.content, f.read())

    def test_native_nav_retires_classic_paths_without_redirecting_script_types(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "nav.js")))
        self.assertIn("platform/nav.mjs", static_fingerprint.CLIENT_LOADED)
        self.assertNotIn("nav.js", static_fingerprint.MIGRATED)
        for path in ("/nav.js", "/static/nav.js", "/static/nav.6a4e39bf.js",
                     "/static/nav.deadbeef.js", "/static/platform/nav.deadbeef.mjs"):
            with self.subTest(path=path):
                response = _request(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.headers.get("cache-control"), "no-store")
                self.assertNotIn("location", response.headers)

    def test_center_webp_is_inline_without_a_system_mime_database(self):
        pack = os.path.join(ROOT, "packs", "example-town")
        photo = os.path.join(pack, "centro-photos", "sampleton-hospital.webp")
        with open(photo, "rb") as source:
            expected = source.read()
        # Minimal deployment images need not carry the host's /etc/mime.types.
        # Exercise the actual ASGI response, not just the extension helper.
        with patch.object(static_assets, "WEBDATA_DIR", pack), \
                patch.object(static_assets.sitedata, "path", return_value=photo), \
                patch.object(static_assets.mimetypes, "guess_type",
                             return_value=(None, None)):
            for method in ("GET", "HEAD"):
                with self.subTest(method=method):
                    response = _request(
                        "/webdata/centro-photos/sampleton-hospital.webp", method)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["content-type"], "image/webp")
                    self.assertNotIn("content-disposition", response.headers)
                    self.assertEqual(response.headers["content-length"], str(len(expected)))
                    self.assertEqual(response.headers["cache-control"], "public, max-age=86400")
                    self.assertEqual(response.content, expected if method == "GET" else b"")

    def test_hash_desactualizado_no_se_sirve_ni_se_cachea(self):
        response = _request("/static/nav.deadbeef.js")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_module_asset_is_registered_but_not_platform_owned(self):
        name = "modules/intake/entrevista.js"
        self.assertNotIn(name, static_fingerprint.MIGRATED)
        self.assertIn(name, static_fingerprint._registered_names())
        with self.assertRaises(ValueError):
            static_fingerprint.url(name)
        response = _request(static_fingerprint.module_url(name))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"),
                         "public, max-age=31536000, immutable")

    def test_offer_module_is_immutable_by_bytes_and_all_classic_or_stale_shapes_fail_closed(self):
        name = "modules/turnos/offer.mjs"
        url = static_fingerprint.module_url(name)
        response = _request(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"),
                         "public, max-age=31536000, immutable")
        with open(static_fingerprint._physical_path(name), "rb") as f:
            self.assertEqual(response.content, f.read())
        for path in ("/offer.js", "/static/offer.js", "/static/offer.deadbeef.js",
                     "/static/modules/turnos/offer.deadbeef.mjs"):
            with self.subTest(path=path):
                stale = _request(path)
                self.assertEqual(stale.status_code, 404)
                self.assertEqual(stale.headers.get("cache-control"), "no-store")

    def test_unknown_module_asset_never_gets_a_fingerprint_url(self):
        with self.assertRaises(ValueError):
            static_fingerprint.module_url("modules/intake/not-declared.js")

    def test_metodo_no_get_nunca_se_maneja(self):
        response = _request(static_fingerprint.url("human-rounds.css"), "POST")
        self.assertEqual(response.status_code, 404)


class TestModuleFingerprintDescriptorSafety(unittest.TestCase):
    """Module bytes are hashed through no-follow descriptors, not a path
    checked earlier during manifest discovery and reopened later by name."""

    def _assert_swap_is_refused(self, swap_part):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            module_dir = os.path.join(root, paths.CODE_DIR, "modules", "intake")
            os.makedirs(module_dir)
            asset = os.path.join(module_dir, "client.js")
            with open(asset, "wb") as f:
                f.write(b"safe module bytes")
            outside_file = os.path.join(outside, "outside.js")
            with open(outside_file, "wb") as f:
                f.write(b"outside bytes must never be hashed")

            real_open = os.open
            swapped = False

            def swapping_open(path, flags, mode=0o777, *, dir_fd=None):
                nonlocal swapped
                if path == swap_part and dir_fd is not None and not swapped:
                    swapped = True
                    if swap_part == "client.js":
                        os.unlink(asset)
                        os.symlink(outside_file, asset)
                    else:
                        held = module_dir + ".held"
                        os.rename(module_dir, held)
                        os.symlink(outside, module_dir)
                return real_open(path, flags, mode, dir_fd=dir_fd)

            static_fingerprint._CACHE.clear()
            try:
                with (
                    patch.object(static_fingerprint, "_ROOT", root),
                    patch.object(static_fingerprint.os, "open", side_effect=swapping_open),
                ):
                    with self.assertRaises(OSError):
                        static_fingerprint._hash8("modules/intake/client.js")
                # the refusal came from the swap, not from a module directory missing in the temp root
                self.assertTrue(swapped)
            finally:
                static_fingerprint._CACHE.clear()

    def test_target_swap_to_symlink_never_hashes_outside_bytes(self):
        self._assert_swap_is_refused("client.js")

    def test_parent_swap_to_symlink_never_hashes_outside_bytes(self):
        self._assert_swap_is_refused("intake")

    def test_same_size_rewrite_with_restored_mtime_rehashes(self):
        with tempfile.TemporaryDirectory() as root:
            asset_dir = os.path.join(root, paths.CODE_DIR, "modules", "intake")
            os.makedirs(asset_dir)
            asset = os.path.join(asset_dir, "client.js")
            with open(asset, "wb") as f:
                f.write(b"AAAA")
            static_fingerprint._CACHE.clear()
            try:
                with patch.object(static_fingerprint, "_ROOT", root):
                    first = static_fingerprint._hash8("modules/intake/client.js")
                    prior = os.stat(asset)
                    with open(asset, "wb") as f:
                        f.write(b"BBBB")
                    restore_mtime_after_ctime_moves(asset, prior)
                    second = static_fingerprint._hash8("modules/intake/client.js")
                self.assertNotEqual(first, second)
            finally:
                static_fingerprint._CACHE.clear()

    def test_hardlinked_module_asset_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            asset_dir = os.path.join(root, paths.CODE_DIR, "modules", "intake")
            os.makedirs(asset_dir)
            asset = os.path.join(asset_dir, "client.js")
            with open(asset, "wb") as f:
                f.write(b"safe")
            os.link(asset, os.path.join(root, "outside-alias.js"))
            static_fingerprint._CACHE.clear()
            try:
                with patch.object(static_fingerprint, "_ROOT", root):
                    with self.assertRaises(OSError):
                        static_fingerprint._hash8("modules/intake/client.js")
            finally:
                static_fingerprint._CACHE.clear()

    def test_held_descriptor_rejects_mutation_between_verification_and_serving(self):
        with tempfile.TemporaryDirectory() as root:
            asset_dir = os.path.join(root, paths.CODE_DIR, "modules", "intake")
            os.makedirs(asset_dir)
            asset = os.path.join(asset_dir, "client.js")
            with open(asset, "wb") as f:
                f.write(b"AAAA")
            prior = os.stat(asset)
            real_lseek = os.lseek
            seeks = 0

            def mutate_before_second_read(fd, offset, whence):
                nonlocal seeks
                result = real_lseek(fd, offset, whence)
                if offset == 0 and whence == os.SEEK_SET:
                    seeks += 1
                    if seeks == 2:
                        with open(asset, "wb") as f:
                            f.write(b"BBBB")
                        # Same size: on coarse timestamps the rewrite can share
                        # the original tick; only a moved ctime can reveal it.
                        restore_mtime_after_ctime_moves(asset, prior)
                return result

            with patch.object(static_fingerprint, "_ROOT", root):
                fd = static_fingerprint._open_module_asset("modules/intake/client.js")
                try:
                    with patch.object(static_fingerprint.os, "lseek", side_effect=mutate_before_second_read):
                        with self.assertRaises(OSError):
                            static_fingerprint._read_verified_module_fd(fd)
                finally:
                    os.close(fd)


class TestCacheControlFingerprintedAdminJavascript(unittest.TestCase):
    """Every migrated root admin bundle has the current-bytes-or-no-store
    contract proven by the original nav.js fingerprint pilot."""

    def test_the_current_admin_assets_are_byte_identical_and_immutable(self):
        # Classic producers leave this cohort when their canonical ES modules
        # replace them; the set remains closed against disk.
        self.assertEqual(static_fingerprint.ADMIN_ROOT_JAVASCRIPT, {
            "admin-app.js", "admin-shell.js",
        })
        for name in sorted(static_fingerprint.ADMIN_ROOT_JAVASCRIPT):
            with self.subTest(name=name):
                response = _request(static_fingerprint.url(name))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers.get("cache-control"),
                                 "public, max-age=31536000, immutable")
                with open(static_fingerprint._physical_path(name), "rb") as f:
                    self.assertEqual(response.content, f.read())

    def test_hash_viejo_admin_falla_cerrado_y_el_404_es_no_store(self):
        response = _request("/static/admin-router.deadbeef.js")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers.get("cache-control"), "no-store")

        retired = _request("/admin-combo.js")
        self.assertEqual(retired.status_code, 404)
        self.assertEqual(retired.headers.get("cache-control"), "no-store")
        retired = _request("/admin-patient-summary.js")
        self.assertEqual(retired.status_code, 404)
        self.assertEqual(retired.headers.get("cache-control"), "no-store")
        for path in ("/admin-preferences.js", "/static/admin-preferences.js",
                     "/admin-content.js", "/static/admin-content.js",
                     "/admin-queue.js", "/static/admin-queue.js", "/admin-cola.js",
                     "/admin-session.js", "/static/admin-session.js",
                     "/admin-table.js", "/static/admin-table.js",
                     "/admin-charts.js", "/static/admin-charts.js",
                     "/admin-login.js", "/static/admin-login.js",
                     "/admin-users.js", "/static/admin-users.js",
                     "/admin-prompts.js", "/static/admin-prompts.js",
                     "/admin-legal.js", "/static/admin-legal.js",
                     "/admin-knowledge.js", "/static/admin-knowledge.js",
                     "/static/admin-table.deadbeef.js"):
            with self.subTest(path=path):
                retired = _request(path)
                self.assertEqual(retired.status_code, 404)
                self.assertEqual(retired.headers.get("cache-control"), "no-store")

    def test_native_catalog_entry_serves_exact_bytes_and_rejects_stale_hash(self):
        name = "platform/admin-content.mjs"
        self.assertNotIn(name, static_fingerprint.CLIENT_LOADED)
        with open(os.path.join(ROOT, "static", name), "rb") as source:
            expected = source.read()
        for method in ("GET", "HEAD"):
            with self.subTest(method=method):
                response = _request(static_fingerprint.url(name), method)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"],
                                 "public, max-age=31536000, immutable")
                self.assertEqual(response.content, expected if method == "GET" else b"")
                stale = _request("/static/platform/admin-content.deadbeef.mjs", method)
                self.assertEqual(stale.status_code, 404)
                self.assertEqual(stale.headers["cache-control"], "no-store")


class TestCacheControlFingerprintedAdminShellUtilities(unittest.TestCase):
    """The non-admin-* scripts loaded directly by the admin shell use the
    same immutable-or-no-store contract as the admin bundles."""

    def test_the_utilities_are_byte_identical_and_immutable(self):
        self.assertEqual(static_fingerprint.ADMIN_SHELL_UTILITIES, set())
        for name in sorted(static_fingerprint.ADMIN_SHELL_UTILITIES):
            with self.subTest(name=name):
                response = _request(static_fingerprint.url(name))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers.get("cache-control"),
                                 "public, max-age=31536000, immutable")
                with open(os.path.join(ROOT, name), "rb") as f:
                    self.assertEqual(response.content, f.read())

    def test_a_stale_utility_hash_fails_closed_with_a_no_store_404(self):
        for name in sorted(static_fingerprint.ADMIN_SHELL_UTILITIES):
            with self.subTest(name=name):
                stem, extension = os.path.splitext(name)
                stale = f"/static/{stem}.deadbeef{extension}"
                response = _request(stale)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.headers.get("cache-control"), "no-store")

class TestCacheControlFingerprintedClientLoaded(unittest.TestCase):
    """Tier 4 slice 6, batch 3 — the client manifest. CLIENT_LOADED is the
    cohort a STATIC JS file loads by URL at runtime (listing.js, directory.mjs,
    platform/account-form.mjs, dni-barcode.mjs, dni-scan.mjs, platform/age.mjs, hours.mjs,
    platform/prep.mjs, islands.mjs); the shell publishes their
    current fingerprinted URLs as `window.SANA_STATIC` (`manifest_script()`)
    and every loader resolves through that map, fail-closed. Serving is the
    same current-bytes-or-no-store contract as every other MIGRATED asset;
    the manifest itself is pinned here as an exact function of CLIENT_LOADED
    and `url()`, so it can never publish a URL that would 404."""

    _NAMES = ("platform/nav.mjs", "platform/directory.mjs", "platform/account-form.mjs", "platform/dependents.mjs",
              "platform/account-requests.mjs", "platform/account-labs.mjs",
              "platform/dni-barcode.mjs", "platform/dni-scan.mjs", "platform/age.mjs",
              "platform/calendar-grid.mjs", "platform/hours.mjs", "platform/dates.mjs", "platform/format.mjs",
              "platform/prep.mjs", "platform/islands.mjs", "platform/turnstile.mjs", "platform/passkeys.mjs",
              "platform/admin-combo.mjs", "platform/admin-filter-controls.mjs", "platform/admin-patient-summary.mjs",
              "platform/admin-preferences.mjs", "platform/admin-session.mjs", "platform/admin-table.mjs",
              "platform/admin-prompts.mjs", "platform/admin-legal.mjs", "platform/admin-knowledge.mjs",
              "platform/i18n.mjs", "platform/week.mjs", "platform/admin-charts.mjs", "platform/search.mjs",
              "platform/admin-solicitudes.mjs",
              "platform/admin-login.mjs", "platform/listing.mjs", "platform/admin-hours-editor.mjs",
              "platform/admin-users.mjs",
              "platform/chat-widget.mjs",
              "platform/chat-ui.mjs",
              "platform/admin-create-appointment.mjs", "platform/backup-key.mjs",
              "platform/hsi-sync.mjs",
              "platform/attendance-forecast.mjs",
              "platform/intake-explainer.mjs",
              "platform/print-head.mjs", "platform/analytics.mjs")

    def test_the_cohort_is_closed_and_inside_migrated(self):
        self.assertEqual(static_fingerprint.CLIENT_LOADED, set(self._NAMES))
        self.assertTrue(static_fingerprint.CLIENT_LOADED <= static_fingerprint.MIGRATED)
        self.assertTrue(static_fingerprint.CLIENT_LOADED.isdisjoint(
            static_fingerprint.ADMIN_ROOT_JAVASCRIPT | static_fingerprint.ADMIN_SHELL_UTILITIES))
    def test_the_client_loaded_assets_are_byte_identical_and_immutable(self):
        for name in sorted(static_fingerprint.CLIENT_LOADED):
            with self.subTest(name=name):
                response = _request(static_fingerprint.url(name))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers.get("cache-control"),
                                 "public, max-age=31536000, immutable")
                with open(static_fingerprint._physical_path(name), "rb") as f:
                    self.assertEqual(response.content, f.read())

    def test_a_stale_batch_3_hash_fails_closed_with_a_no_store_404(self):
        for name in sorted(static_fingerprint.CLIENT_LOADED):
            with self.subTest(name=name):
                stem, extension = os.path.splitext(name)
                stale = f"/static/{stem}.deadbeef{extension}"
                response = _request(stale)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_moved_directory_and_tiles_old_root_shapes_are_no_store_404(self):
        """2026-09-05 placement: directory.mjs and tiles.mjs physically moved
        into static/platform/. Every shape a stale bookmark, cached shell or
        hand-built URL could still carry — the bare old root module path, the
        old root name under /static/, and a stale/forged hash on the old root
        stem — must fail closed with a no-store 404, never serve raw source
        and never become a cacheable miss. The genuine legacy alias redirects
        (`_LEGACY_ASSET_ALIASES`) are a different mechanism and stay intact:
        neither file ever had one."""
        for path in ("/directory.mjs", "/static/directory.mjs",
                     "/static/directory.deadbeef.mjs",
                     "/tiles.mjs", "/static/tiles.mjs",
                     "/static/tiles.deadbeef.mjs"):
            with self.subTest(path=path):
                response = _request(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.headers.get("cache-control"), "no-store")

        # The new placement serves the exact current bytes immutably (the
        # CLIENT_LOADED cohort test above already pins platform/directory.mjs;
        # tiles is MIGRATED-only, so its serving shape is pinned here).
        tiles_url = static_fingerprint.url("platform/tiles.mjs")
        self.assertRegex(tiles_url, r"^/static/platform/tiles\.[0-9a-f]{8}\.mjs$")
        served = _request(tiles_url)
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.headers.get("cache-control"),
                         "public, max-age=31536000, immutable")
        with open(static_fingerprint._physical_path("platform/tiles.mjs"), "rb") as f:
            self.assertEqual(served.content, f.read())

    def test_auth_primitives_reject_old_root_paths_and_serve_native_relative_imports(self):
        # Actual c588c809 pre-placement fingerprints, not only forged hashes.
        for name, old_hash in (("passkeys", "d906a021"), ("turnstile", "1feaee44")):
            for path in (f"/{name}.mjs", f"/static/{name}.mjs",
                         f"/static/{name}.{old_hash}.mjs"):
                with self.subTest(path=path):
                    response = _request(path)
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.headers.get("cache-control"), "no-store")
                    self.assertEqual(_request(path + "/").status_code, _dir_status(path + "/"))
            source_path = static_fingerprint._physical_path(f"platform/{name}.mjs")
            with open(source_path, "rb") as source:
                expected = source.read()
            raw = _request(f"/static/platform/{name}.mjs")
            self.assertEqual(raw.status_code, 200)
            self.assertEqual(raw.headers.get("cache-control"), "no-cache")
            self.assertEqual(raw.content, expected)

    def test_native_auth_simulator_retires_classic_paths_and_serves_exact_module(self):
        # 839b46eb is the actual frozen pre-migration classic fingerprint.
        for path, status in (("/auth-test.js", 404), ("/auth-test.js/", 404),
                             ("/static/auth-test.js", 404), ("/static/auth-test.js/", 403),
                             ("/static/auth-test.839b46eb.js", 404),
                             ("/static/auth-test.839b46eb.js/", 403),
                             ("/static/platform/auth-test.mjs/", 403),
                             ("/static/platform/auth-test.deadbeef.mjs", 404)):
            with self.subTest(path=path):
                response = _request(path)
                self.assertEqual(response.status_code, status or _dir_status(path + suffix))
                self.assertEqual(response.headers.get("cache-control"), "no-store")
        url = static_fingerprint.url("platform/auth-test.mjs")
        self.assertRegex(url, r"^/static/platform/auth-test\.[0-9a-f]{8}\.mjs$")
        served = _request(url)
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.headers.get("cache-control"),
                         "public, max-age=31536000, immutable")
        with open(static_fingerprint._physical_path("platform/auth-test.mjs"), "rb") as source:
            self.assertEqual(served.content, source.read())
        # Existing platform modules permit a current raw URL with revalidation
        # (i18n/format use relative imports). It must be the SAME implementation,
        # never retired classic bytes or falsely immutable cache metadata.
        raw = _request("/static/platform/auth-test.mjs")
        self.assertEqual(raw.status_code, 200)
        self.assertEqual(raw.headers.get("cache-control"), "no-cache")
        self.assertEqual(raw.content, served.content)

    def test_native_reports_is_template_only_and_retired_paths_fail_closed(self):
        name = "platform/admin-reports.mjs"
        self.assertIn(name, static_fingerprint.MIGRATED)
        self.assertNotIn(name, static_fingerprint.CLIENT_LOADED)
        self.assertNotIn("admin-reports.js", static_fingerprint.ADMIN_ROOT_JAVASCRIPT)
        paths = ("/admin-reports.js", "/static/admin-reports.js",
                 # Exact SHA256 prefix at the lane base625e86a9.
                 "/static/admin-reports.91d1ed4a.js",
                 "/static/admin-reports.deadbeef.js",
                 "/static/platform/admin-reports.deadbeef.mjs")
        for path in paths:
            for suffix, status in (("", 404), ("/", None)):
                with self.subTest(path=path + suffix):
                    response = _request(path + suffix)
                    self.assertEqual(response.status_code, status or _dir_status(path + suffix))
                    self.assertEqual(response.headers.get("cache-control"), "no-store")
        response = _request(static_fingerprint.url(name))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"),
                         "public, max-age=31536000, immutable")
        with open(static_fingerprint._physical_path(name), "rb") as source:
            self.assertEqual(response.content, source.read())
        raw = _request("/static/" + name)
        self.assertEqual(raw.status_code, 200)
        self.assertEqual(raw.headers.get("cache-control"), "no-cache")
        self.assertEqual(raw.content, response.content)

    def test_native_appearance_is_template_only_and_retired_paths_fail_closed(self):
        name = "platform/admin-appearance.mjs"
        self.assertIn(name, static_fingerprint.MIGRATED)
        self.assertNotIn(name, static_fingerprint.CLIENT_LOADED)
        self.assertNotIn("admin-theme.js", static_fingerprint.ADMIN_ROOT_JAVASCRIPT)
        for path in ("/admin-theme.js", "/static/admin-theme.js",
                     "/static/admin-theme.2eaabaf7.js",  # exact removed bytes at2d197aaf
                     "/static/admin-theme.deadbeef.js",
                     "/static/platform/admin-appearance.deadbeef.mjs"):
            for suffix, status in (("", 404), ("/", None)):
                with self.subTest(path=path + suffix):
                    response = _request(path + suffix)
                    self.assertEqual(response.status_code, status or _dir_status(path + suffix))
                    self.assertEqual(response.headers.get("cache-control"), "no-store")
        response = _request(static_fingerprint.url(name))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"),
                         "public, max-age=31536000, immutable")
        with open(static_fingerprint._physical_path(name), "rb") as source:
            self.assertEqual(response.content, source.read())
        raw = _request("/static/" + name)
        self.assertEqual(raw.status_code, 200)
        self.assertEqual(raw.headers.get("cache-control"), "no-cache")
        self.assertEqual(raw.content, response.content)

    def test_native_hsi_is_template_only_and_retired_paths_fail_closed(self):
        name = "platform/admin-hsi.mjs"
        self.assertIn(name, static_fingerprint.MIGRATED)
        self.assertNotIn(name, static_fingerprint.CLIENT_LOADED)
        self.assertNotIn("admin-hsi.js", static_fingerprint.ADMIN_ROOT_JAVASCRIPT)
        for path in ("/admin-hsi.js", "/static/admin-hsi.js",
                     "/static/admin-hsi.aee08932.js",  # exact retired bytes at d4513310
                     "/static/admin-hsi.deadbeef.js",
                     "/static/platform/admin-hsi.deadbeef.mjs"):
            for suffix, status in (("", 404), ("/", None)):
                with self.subTest(path=path + suffix):
                    response = _request(path + suffix)
                    self.assertEqual(response.status_code, status or _dir_status(path + suffix))
                    self.assertEqual(response.headers.get("cache-control"), "no-store")
        response = _request(static_fingerprint.url(name))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"),
                         "public, max-age=31536000, immutable")
        with open(static_fingerprint._physical_path(name), "rb") as source:
            self.assertEqual(response.content, source.read())

    def test_native_patient_roster_retires_english_and_spanish_assets(self):
        name = "platform/admin-patients.mjs"
        self.assertIn(name, static_fingerprint.MIGRATED)
        self.assertNotIn(name, static_fingerprint.CLIENT_LOADED)
        self.assertNotIn("admin-patients.js", static_fingerprint.ADMIN_ROOT_JAVASCRIPT)
        for path in ("/admin-patients.js", "/admin-pacientes.js",
                     "/static/admin-patients.js", "/static/admin-pacientes.js",
                     "/static/admin-patients.81e4c121.js",  # exact bytes at 82fe90d5
                     "/static/admin-patients.deadbeef.js",
                     "/static/platform/admin-patients.deadbeef.mjs"):
            for suffix, status in (("", 404), ("/", None)):
                with self.subTest(path=path + suffix):
                    response = _request(path + suffix)
                    self.assertEqual(response.status_code, status or _dir_status(path + suffix))
                    self.assertEqual(response.headers.get("cache-control"), "no-store")
        response = _request(static_fingerprint.url(name))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"),
                         "public, max-age=31536000, immutable")
        with open(static_fingerprint._physical_path(name), "rb") as source:
            self.assertEqual(response.content, source.read())

    def test_native_preparations_is_template_only_and_retired_paths_fail_closed(self):
        name = "platform/admin-preparations.mjs"
        self.assertIn(name, static_fingerprint.MIGRATED)
        self.assertNotIn(name, static_fingerprint.CLIENT_LOADED)
        self.assertNotIn("admin-preparations.js", static_fingerprint.ADMIN_ROOT_JAVASCRIPT)
        for path in ("/admin-preparations.js", "/static/admin-preparations.js",
                     "/static/admin-preparations.11b389d1.js",  # exact retired bytes at 82fe90d5
                     "/static/admin-preparations.deadbeef.js",
                     "/static/platform/admin-preparations.deadbeef.mjs",
                     "/admin-preparaciones.js"):
            for suffix, status in (("", 404), ("/", None)):
                with self.subTest(path=path + suffix):
                    response = _request(path + suffix)
                    self.assertEqual(response.status_code, status or _dir_status(path + suffix))
                    self.assertEqual(response.headers.get("cache-control"), "no-store")
        response = _request(static_fingerprint.url(name))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"),
                         "public, max-age=31536000, immutable")
        with open(static_fingerprint._physical_path(name), "rb") as source:
            self.assertEqual(response.content, source.read())

    def test_native_doctor_controllers_retire_exact_old_assets(self):
        for stem, old_hash in (("admin-my-appointments", "b1433d96"),
                               ("admin-doctor-patients", "9c4a3ec5")):
            name = "platform/" + stem + ".mjs"
            self.assertIn(name, static_fingerprint.MIGRATED)
            self.assertNotIn(name, static_fingerprint.CLIENT_LOADED)
            self.assertNotIn(stem + ".js", static_fingerprint.ADMIN_ROOT_JAVASCRIPT)
            for retired in ("/" + stem + ".js", "/static/" + stem + ".js",
                            "/static/" + stem + "." + old_hash + ".js",
                            "/static/platform/" + stem + ".deadbeef.mjs"):
                for suffix, status in (("", 404), ("/", None)):
                    with self.subTest(path=retired + suffix):
                        response = _request(retired + suffix)
                        self.assertEqual(response.status_code, status or _dir_status(retired + suffix))
                        self.assertEqual(response.headers.get("cache-control"), "no-store")
            served = _request(static_fingerprint.url(name))
            self.assertEqual(served.status_code, 200)
            self.assertEqual(served.headers.get("cache-control"),
                             "public, max-age=31536000, immutable")
            with open(static_fingerprint._physical_path(name), "rb") as source:
                self.assertEqual(served.content, source.read())
            raw = _request("/static/" + name)
            self.assertEqual(raw.status_code, 200)
            self.assertEqual(raw.headers.get("cache-control"), "no-cache")
            self.assertEqual(raw.content, served.content)

    def test_native_plan_is_template_only_and_retired_paths_fail_closed(self):
        name = "platform/admin-roadmap.mjs"
        self.assertIn(name, static_fingerprint.MIGRATED)
        self.assertNotIn(name, static_fingerprint.CLIENT_LOADED)
        self.assertNotIn("admin-roadmap.js", static_fingerprint.ADMIN_ROOT_JAVASCRIPT)
        for path in ("/admin-roadmap.js", "/static/admin-roadmap.js",
                     # Exact SHA256 prefix of the deleted controller at b4c6550d.
                     "/static/admin-roadmap.484c44af.js",
                     "/static/admin-roadmap.deadbeef.js",
                     "/static/platform/admin-roadmap.deadbeef.mjs"):
            with self.subTest(path=path):
                response = _request(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.headers.get("cache-control"), "no-store")
        response = _request(static_fingerprint.url(name))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"),
                         "public, max-age=31536000, immutable")
        with open(static_fingerprint._physical_path(name), "rb") as source:
            self.assertEqual(response.content, source.read())
        raw = _request("/static/" + name)
        self.assertEqual(raw.status_code, 200)
        self.assertEqual(raw.headers.get("cache-control"), "no-cache")
        self.assertEqual(raw.content, response.content)

    def test_moved_photo_viewer_old_root_shapes_are_no_store_404(self):
        for path, status in (("/photo-zoom.js", 404), ("/photo-zoom.js/", 404),
                             ("/static/photo-zoom.js", 404), ("/static/photo-zoom.js/", 403),
                             ("/static/photo-zoom.deadbeef.js", 404),
                             ("/static/photo-zoom.deadbeef.js/", 403)):
            with self.subTest(path=path):
                response = _request(path)
                # Directory-shaped probes hit the existing static security
                # guard (403); neither route shape may expose retired bytes.
                self.assertEqual(response.status_code, status or _dir_status(path + suffix))
                self.assertEqual(response.headers.get("cache-control"), "no-store")
        viewer_url = static_fingerprint.url("platform/photo-zoom.mjs")
        self.assertRegex(viewer_url, r"^/static/platform/photo-zoom\.[0-9a-f]{8}\.mjs$")
        response = _request(viewer_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"),
                         "public, max-age=31536000, immutable")
        with open(static_fingerprint._physical_path("platform/photo-zoom.mjs"), "rb") as source:
            self.assertEqual(response.content, source.read())

    def test_moved_dni_modules_old_root_shapes_are_no_store_404(self):
        """DNI scanner and photo decoder are platform modules, never root aliases."""
        for stem in ("dni-scan", "dni-barcode"):
            for path in (f"/{stem}.mjs", f"/static/{stem}.mjs", f"/static/{stem}.deadbeef.mjs"):
                with self.subTest(path=path):
                    response = _request(path)
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_manifest_is_exactly_client_loaded_resolved_through_url(self):
        manifest = static_fingerprint.manifest()
        self.assertEqual(list(manifest), sorted(static_fingerprint.CLIENT_LOADED))
        for name, value in manifest.items():
            with self.subTest(name=name):
                self.assertEqual(value, static_fingerprint.url(name))
                self.assertRegex(value, r"^/static/(?:[a-z0-9-]+/)*[a-z0-9-]+\.[0-9a-f]{8}\.(?:m?js)$")
                # Every published URL is a CURRENT fingerprint — resolvable
                # by the serving side right now, never a 404-to-be.
                self.assertEqual(static_fingerprint._resolve(value), name)

    def test_manifest_script_has_the_exact_bootstrap_shape(self):
        import json
        # The manifest tag is followed by the one session-recovery tag
        # (core/static_recover.js); the manifest tag itself keeps its exact shape.
        script = static_fingerprint.manifest_script()
        self.assertTrue(script.endswith(static_fingerprint.recover_script()))
        script = script[:-len(static_fingerprint.recover_script())]
        self.assertTrue(script.startswith("<script>window.SANA_STATIC="))
        self.assertTrue(script.endswith(";</script>"))
        payload = script[len("<script>window.SANA_STATIC="):-len(";</script>")]
        self.assertEqual(json.loads(payload), static_fingerprint.manifest())
        # Compact, sorted, attribute-safe: the charset is fingerprinted URLs
        # plus JSON punctuation, so no escaping and no `</script>` possible.
        self.assertEqual(payload, json.dumps(static_fingerprint.manifest(),
                                             separators=(",", ":"), sort_keys=True))
        entry = r'"(?:[a-z0-9-]+/)*[a-z0-9-]+\.(?:m?js)":"/static/(?:[a-z0-9-]+/)*[a-z0-9-]+\.[0-9a-f]{8}\.(?:m?js)"'
        self.assertRegex(payload, rf'^\{{({entry},?)+\}}$')
        self.assertEqual(script.count("<script>"), 1)

    def test_recover_script_follows_the_manifest_once_and_is_inline_safe(self):
        # Behind the municipal gateway every /static file answers 401 without a
        # session, so the recovery code cannot be a static file: it rides inline
        # right after the manifest, once per page (behavior → tests/static-recover.test.mjs).
        recover = static_fingerprint.recover_script()
        self.assertTrue(recover.startswith("<script>") and recover.endswith("</script>"))
        body = recover[len("<script>"):-len("</script>")]
        self.assertNotIn("</script", body.lower())
        self.assertNotIn("<!--", body)
        self.assertIn("w.sanaRecoverStatic = function", body)
        self.assertTrue(body.startswith("(function (w)"), "the file's header comment is not shipped to every page")
        page = static_fingerprint.render("<head>{{static.manifest}}</head>")
        self.assertEqual(page.count(recover), 1)
        self.assertEqual(page.count("window.SANA_STATIC="), 1)
        self.assertLess(page.index("window.SANA_STATIC="), page.index("w.sanaRecoverStatic = function"))

    def test_render_resolves_the_reserved_manifest_token_and_nothing_else_changes(self):
        text = ('<head>{{static.manifest}}</head>x'
                '{{static.manifest}}'
                '<script src="{{static.platform/nav.mjs}}"></script>'
                '{{static.not-migrated.js}}')
        out = static_fingerprint.render(text)
        # The reserved token resolves to the whole manifest <script> tag (it
        # is NOT a URL), every occurrence, and the ordinary placeholders keep
        # their exact pre-existing behavior around it: migrated -> URL,
        # unknown -> left visibly intact.
        self.assertEqual(
            out,
            '<head>' + static_fingerprint.manifest_script() + '</head>x'
            + static_fingerprint.manifest_script()
            + f'<script src="{static_fingerprint.url("platform/nav.mjs")}"></script>'
            + '{{static.not-migrated.js}}')
        # `{{static.manifest}}` has no extension, so the placeholder regex
        # never matched it before this batch and still doesn't — a template
        # that spells it wrong stays visibly literal.
        self.assertEqual(static_fingerprint.render("{{static.manifest.js}}"),
                         "{{static.manifest.js}}")


class TestCacheControlFingerprintedMjs(unittest.TestCase):
    """Tier 5 slice 2 — `.mjs` facades join MIGRATED. Same immutable-or-404
    guarantee as the `.js`/`.css` cases above, proven against `format.mjs`
    (the slice 0 pilot facade), not a re-derivation of the mechanism."""

    def test_format_mjs_url_tiene_la_forma_esperada(self):
        url = static_fingerprint.url("platform/format.mjs")
        self.assertRegex(url, r"^/static/platform/format\.[0-9a-f]{8}\.mjs$")

    def test_format_mjs_fingerprinted_es_inmutable(self):
        response = _request(static_fingerprint.url("platform/format.mjs"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"),
                         "public, max-age=31536000, immutable")
        self.assertEqual(response.headers.get("content-type"), "text/javascript")
        with open(os.path.join(ROOT, "static/platform/format.mjs"), "rb") as f:
            self.assertEqual(response.content, f.read())

    def test_format_mjs_hash_desactualizado_no_se_sirve(self):
        response = _request("/static/platform/format.deadbeef.mjs")
        self.assertEqual(response.status_code, 404)


class TestRetiredFormatJsPath(unittest.TestCase):
    """The deleted classic formatter cannot survive as an unhashed path."""

    def test_static_format_js_is_closed(self):
        response = _request("/static/format.js")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_static_tokens_css_sigue_andando(self):
        # No regression: the single-name special case generalized into a
        # table still serves tokens.css exactly as slice 1 left it.
        response = _request("/static/tokens.css")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("content-type"), "text/css")

    def test_static_format_js_no_get_no_se_maneja(self):
        response = _request("/static/format.js", "POST")
        self.assertEqual(response.status_code, 404)

    def test_retired_tiles_js_paths_are_closed(self):
        for path in ("/tiles.js", "/static/tiles.js"):
            with self.subTest(path=path):
                response = _request(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_moved_age_and_prep_modules_old_root_shapes_are_no_store_404(self):
        """Shared public-flow models are platform modules, never root aliases."""
        for stem in ("age", "prep"):
            for path in (f"/{stem}.mjs", f"/static/{stem}.mjs", f"/static/{stem}.deadbeef.mjs"):
                with self.subTest(path=path):
                    response = _request(path)
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_retired_directory_js_paths_are_closed(self):
        for path in ("/directory.js", "/static/directory.js"):
            with self.subTest(path=path):
                response = _request(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_retired_control_fit_js_paths_are_closed(self):
        for path in ("/admin-control-fit.js", "/static/admin-control-fit.js"):
            with self.subTest(path=path):
                response = _request(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_retired_islands_js_paths_are_closed(self):
        for path in ("/islands.js", "/static/islands.js"):
            with self.subTest(path=path):
                response = _request(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_retired_turnstile_js_paths_are_closed(self):
        for path in ("/turnstile.js", "/static/turnstile.js"):
            with self.subTest(path=path):
                response = _request(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_retired_passkeys_js_paths_are_closed(self):
        for path in ("/passkeys.js", "/static/passkeys.js"):
            with self.subTest(path=path):
                response = _request(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_retired_root_primitive_mjs_paths_are_closed(self):
        # Rows 1.1/4.1: already-native primitives now live under
        # static/platform. Old public root and un-nested fingerprint
        # shapes must not keep serving them.
        names = (
            "format", "dates", "hours", "calendar-grid", "islands",
            "admin-session", "admin-preferences", "admin-table",
            "admin-combo", "admin-filter-controls", "admin-control-fit",
            "admin-patient-summary",
        )
        for name in names:
            for path in (f"/{name}.mjs", f"/static/{name}.mjs",
                         f"/static/{name}.deadbeef.mjs"):
                with self.subTest(path=path):
                    response = _request(path)
                    self.assertEqual(response.status_code, 404)
                    self.assertEqual(response.headers.get("cache-control"),
                                     "no-store")

    def test_retired_turnos_classic_controllers_are_closed_in_the_composed_router(self):
        # Native modules own these controllers.  Neither their old root shape
        # nor a stale static URL may become a source-serving fallback.
        for name in ("service", "wz-upload", "upload-order"):
            for path in (f"/{name}.js", f"/static/{name}.js"):
                for suffix in ("", "?stale=1"):
                    with self.subTest(path=path + suffix):
                        response = _request(path + suffix)
                        self.assertEqual(response.status_code, 404)
                        self.assertEqual(response.headers.get("cache-control"),
                                         "no-store")

    def test_turnos_native_raw_aliases_redirect_to_current_fingerprints(self):
        for raw, name in (
            ("/static/modules/turnos/wz-upload.mjs", "modules/turnos/wz-upload.mjs"),
            ("/static/modules/turnos/service.mjs", "modules/turnos/service.mjs"),
            ("/static/modules/turnos/upload-order.mjs", "modules/turnos/upload-order.mjs"),
            ("/modules/turnos/wz-upload.mjs", "modules/turnos/wz-upload.mjs"),
            ("/modules/turnos/service.mjs", "modules/turnos/service.mjs"),
            ("/modules/turnos/upload-order.mjs", "modules/turnos/upload-order.mjs"),
        ):
            with self.subTest(raw=raw):
                response = _request(raw + "?stale=1")
                self.assertEqual(response.status_code, 301)
                self.assertEqual(response.headers.get("location"),
                                 static_fingerprint.module_url(name))
                self.assertEqual(response.headers.get("cache-control"), "no-store")


class TestStudyAssetCacheContract(unittest.TestCase):
    def test_study_assets_serve_current_bytes_and_reject_stale_hashes(self):
        for name, current_url in (
            ("platform/admin-usability.mjs", static_fingerprint.url("platform/admin-usability.mjs")),
            ("modules/turnos/study-runner.mjs", static_fingerprint.module_url("modules/turnos/study-runner.mjs")),
        ):
            with self.subTest(name=name):
                response = _request(current_url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers.get("cache-control"),
                                 "public, max-age=31536000, immutable")
                with open(static_fingerprint._physical_path(name), "rb") as source:
                    self.assertEqual(response.content, source.read())
                stale_url = current_url.rsplit(".", 2)[0] + ".deadbeef.mjs"
                stale = _request(stale_url)
                self.assertEqual(stale.status_code, 404)
                self.assertEqual(stale.headers.get("cache-control"), "no-store")
                self.assertNotIn(name, static_fingerprint.CLIENT_LOADED)


class TestVendorPathPrepared(unittest.TestCase):
    """Tier 5 slice 2 — the vendor/ subdirectory + compound-suffix
    (`preact.module.js`) mechanism is prepared for slice 3's real vendored
    Preact/HTM files, which don't exist yet. Proven here with a throwaway
    fixture file under `static/vendor/`, monkeypatching MIGRATED for the
    duration of the test only — this is NOT a real shipped vendor asset."""

    VENDOR_NAME = "vendor/fixture.module.js"

    def setUp(self):
        self.vendor_dir = os.path.join(ROOT, "static", "vendor")
        self.fixture_path = os.path.join(self.vendor_dir, "fixture.module.js")
        with open(self.fixture_path, "wb") as f:
            f.write(b"export default 1;\n")
        self._migrated_real = static_fingerprint.MIGRATED
        static_fingerprint.MIGRATED = self._migrated_real | {self.VENDOR_NAME}
        static_fingerprint.invalidate()

    def tearDown(self):
        static_fingerprint.MIGRATED = self._migrated_real
        static_fingerprint.invalidate()
        os.remove(self.fixture_path)

    def test_vendor_url_pone_el_hash_antes_del_sufijo_compuesto(self):
        # /static/vendor/fixture.<hash8>.module.js — NOT
        # /static/vendor/fixture.module.<hash8>.js.
        url = static_fingerprint.url(self.VENDOR_NAME)
        self.assertRegex(url, r"^/static/vendor/fixture\.[0-9a-f]{8}\.module\.js$")

    def test_vendor_fingerprinted_se_sirve_desde_static_vendor(self):
        response = _request(static_fingerprint.url(self.VENDOR_NAME))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"),
                          "public, max-age=31536000, immutable")
        with open(self.fixture_path, "rb") as f:
            self.assertEqual(response.content, f.read())

    def test_vendor_stale_hash_no_se_sirve(self):
        response = _request("/static/vendor/fixture.deadbeef.module.js")
        self.assertEqual(response.status_code, 404)


class TestSlice3VendorAndIslandsShipped(unittest.TestCase):
    """Tier 5 slice 3 — the REAL shipped assets (not a throwaway fixture like
    TestVendorPathPrepared above): the four vendored Preact/HTM files under
    static/vendor/, the pilot island static/islands/MedCal.mjs, and the
    canonical calendar-grid.mjs module."""

    def test_los_cuatro_archivos_vendored_sirven_inmutables(self):
        for name in (
            "vendor/preact.module.js",
            "vendor/hooks.module.js",
            "vendor/htm.module.js",
            "vendor/htm-preact.module.js",
        ):
            with self.subTest(name=name):
                response = _request(static_fingerprint.url(name))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers.get("cache-control"),
                                  "public, max-age=31536000, immutable")
                self.assertGreater(len(response.content), 0)

    def test_el_island_pilot_sirve_inmutable_con_la_forma_del_diseño(self):
        url = static_fingerprint.url("islands/MedCal.mjs")
        self.assertRegex(url, r"^/static/islands/MedCal\.[0-9a-f]{8}\.mjs$")
        response = _request(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"),
                          "public, max-age=31536000, immutable")

    def test_calendar_grid_mjs_canonical_sirve_inmutable(self):
        response = _request(static_fingerprint.url("platform/calendar-grid.mjs"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"),
                          "public, max-age=31536000, immutable")

    def test_deleted_calendar_grid_js_no_longer_has_a_passthrough(self):
        response = _request("/static/calendar-grid" + ".js")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_render_con_placeholder_anidado_para_el_import_map_de_admin_html(self):
        # {{static.vendor/preact.module.js}} / {{static.islands/MedCal.mjs}} —
        # los tokens que admin.html usa de verdad para su <script type="importmap">.
        text = static_fingerprint.render(
            "{{static.vendor/preact.module.js}} {{static.islands/MedCal.mjs}}"
        )
        self.assertRegex(text, r"^/static/vendor/preact\.[0-9a-f]{8}\.module\.js "
                                r"/static/islands/MedCal\.[0-9a-f]{8}\.mjs$")


if __name__ == "__main__":
    unittest.main()
