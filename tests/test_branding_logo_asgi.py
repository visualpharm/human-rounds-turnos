"""test_branding_logo_asgi.py — Tier 4 slice 9: native ASGI logo-fallback parity.

`branding_router.py` serves `GET /logo.png` and `GET /logo` natively (a
three-way fallback: admin upload -> shipped `logo.png` -> 404), so those two
URLs no longer depend on the legacy reverse-proxy catch-all. This file proves
each leg of that chain against a minimal FastAPI app mounting ONLY
`branding_router.router` — not the full `app.app` — so the logo endpoints are
exercised in isolation. A reachability test at the end also mounts the full
`app.app` to prove the native route remains reachable in the final composition.

The contracts pinned here are the ones the legacy dispatch chain gave these
URLs (see branding_router.py's module docstring and the design doc's logo-parity
section):

  - custom-logo: served with CSP/nosniff/no-cache (`branding.CUSTOM_LOGO_HEADERS`),
    Content-Type from the REAL format (magic bytes, all four: svg/png/jpg/webp),
    Content-Length exact, body == the uploaded bytes — and NO ETag, NO
    Accept-Ranges, NO Last-Modified (the legacy `_serve_logo` sends none of those).
  - shipped-default: `image/png` + one-day cache (`branding.STATIC_IMAGE_CACHE`,
    byte-for-byte what `Handler.end_headers` puts on an unversioned image) +
    `Last-Modified`, and the SAME conditional-GET contract
    `SimpleHTTPRequestHandler.send_head` honored — again NO ETag, NO
    Accept-Ranges (the legacy handler sends neither; this is why it's a plain
    `Response`, not `FileResponse`). That conditional is `send_head` exactly:
    `If-None-Match` SUPPRESSES the `If-Modified-Since` check (-> 200); only an
    IMS that parses to a naive (read as UTC) or UTC datetime is compared, a
    non-UTC offset is left un-compared (-> 200), and an ill-formed value is
    ignored (-> 200). A 304 carries the one-day Cache-Control but NOT
    `Last-Modified` — `send_head`'s NOT_MODIFIED branch sends only the status
    line; `Last-Modified` is the 200 branch's header.
  - missing-default: a no-store 404 (errors are never cached).
  - `/logo` (no extension) has NO shipped default — admin upload or 404.
  - unreadable custom logo: `custom_logo_payload()` swallows the OSError and
    returns None, so `/logo.png` falls through to the shipped default and `/logo`
    to the no-store 404 — the chain never 500s on a logo the upload left
    unreadable.

And the parity guarantee itself: the custom-logo headers the ASGI endpoint
sends are the SAME constant `_serve_logo` (the legacy path) sends — proven by
asserting against `branding.CUSTOM_LOGO_HEADERS` directly, not a hand-copy.

Run:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest discover -s tests -p "test_branding_logo_asgi.py"
"""

import os
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", DATABASE_URL="")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.patient_web import branding  # noqa: E402
from app.patient_web import branding_router  # noqa: E402


# One clean image PER supported format — each passes `_sniff` by magic bytes
# (svg by its XML header + `_svg_safe`), proving the custom-logo leg serves the
# right MIME for the REAL format under the fixed /logo.png URL. The exact bytes
# round-trip is asserted, so a format whose MIME drifts but whose body doesn't
# still fails the test.
_CLEAN_SVG = (b'<?xml version="1.0" encoding="UTF-8"?>'
              b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
              b'<circle cx="32" cy="32" r="30" fill="#0e8b50"/></svg>')
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 120          # magic: \x89PNG\r\n\x1a\n
_JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 120           # magic: \xff\xd8\xff
_WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 120  # magic: RIFF....WEBP

# (bytes, expected_mime, expected_extension-after-save)
_FORMATS = [
    ("svg", _CLEAN_SVG, "image/svg+xml"),
    ("png", _PNG, "image/png"),
    ("jpg", _JPG, "image/jpeg"),
    ("webp", _WEBP, "image/webp"),
]

# Headers the custom-logo response must NOT carry — the legacy `_serve_logo`
# never sends them, so the ASGI endpoint (a plain `Response`) must not either.
# A `FileResponse` would add `etag` (and `accept-ranges`); this guards the
# plain-Response choice against a future "helpful" swap.
_FORBIDDEN_CUSTOM_HEADERS = ("etag", "accept-ranges", "last-modified")
_FORBIDDEN_DEFAULT_HEADERS = ("etag", "accept-ranges")


def _app():
    """A minimal FastAPI app mounting only branding_router — isolates the logo
    endpoints from the full composition (no legacy proxy, no module routers)."""
    api = FastAPI()
    api.include_router(branding_router.router)
    return api


class _CustomLogoFixture(unittest.TestCase):
    """Shared setUp/tearDown: point branding.BRANDING_DIR at a temp dir so the
    custom-logo leg can be turned on/off without touching the repo."""

    def setUp(self):
        self._dir = branding.BRANDING_DIR
        branding.BRANDING_DIR = tempfile.mkdtemp(prefix="marca-asgi-")
        branding.invalidate()

    def tearDown(self):
        shutil.rmtree(branding.BRANDING_DIR, ignore_errors=True)
        branding.BRANDING_DIR = self._dir
        branding.invalidate()


class TestCustomLogoAllFormats(_CustomLogoFixture):
    """Leg 1: an admin-uploaded logo wins on BOTH /logo.png and /logo, served
    natively with the CSP/nosniff/no-cache headers, the right MIME for its REAL
    format, exact Content-Length, the exact uploaded bytes — for EVERY supported
    format (svg/png/jpg/webp), not just one."""

    def setUp(self):
        super().setUp()
        self.client = TestClient(_app())

    def _assert_custom_contract(self, url, data, mime):
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200, r.text)
        # Body is the uploaded bytes, unchanged.
        self.assertEqual(r.content, data)
        # MIME is the REAL format (magic bytes), not the .png in the URL.
        self.assertEqual(r.headers.get("content-type"), mime)
        # Content-Length is EXACT — not chunked, not absent, not off-by-one.
        self.assertEqual(r.headers.get("content-length"), str(len(data)))
        # Parity: the exact constant _serve_logo (legacy) sends — not a copy.
        for k, v in branding.CUSTOM_LOGO_HEADERS:
            self.assertEqual(r.headers.get(k), v, k)
        # The fixed URL carries no version: revalidate every load.
        self.assertEqual(r.headers.get("cache-control"), "no-cache, must-revalidate")
        # Legacy `_serve_logo` sends none of these — neither may we.
        for h in _FORBIDDEN_CUSTOM_HEADERS:
            self.assertNotIn(h, {k.lower() for k in r.headers.keys()}, h)

    def test_every_format_on_logo_png(self):
        for name, data, mime in _FORMATS:
            with self.subTest(format=name):
                branding.save_logo(data)  # save_logo replaces, one at a time
                self._assert_custom_contract("/logo.png", data, mime)

    def test_every_format_on_logo_sin_extension(self):
        # /logo has no shipped default; with a custom logo it answers for every
        # format too, same contract as /logo.png.
        for name, data, mime in _FORMATS:
            with self.subTest(format=name):
                branding.save_logo(data)
                self._assert_custom_contract("/logo", data, mime)

    def test_el_svg_con_script_nunca_llega_a_esta_ruta(self):
        # Defense stays on upload (save_logo rejects); _serve_logo/this endpoint
        # only ever see what save_logo accepted. A scripted SVG can't get here.
        with self.assertRaises(ValueError):
            branding.save_logo(b'<svg onload="alert(1)"></svg>')


class TestShippedDefault(_CustomLogoFixture):
    """Leg 2: no admin upload -> the repo's shipped logo.png is served natively
    with image/png, the one-day image cache, a Last-Modified, and NO ETag /
    Accept-Ranges — header-for-header the legacy `SimpleHTTPRequestHandler`
    response, served from a plain Response (not FileResponse)."""

    def setUp(self):
        super().setUp()
        self.client = TestClient(_app())

    def test_logo_png_cae_al_default_con_headers_legacy(self):
        shipped = branding.default_logo_path()
        self.assertIsNotNone(shipped, "this repo ships a logo.png at its root")
        with open(shipped, "rb") as f:
            on_disk = f.read()
        r = self.client.get("/logo.png")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("content-type"), "image/png")
        # The one-day image cache Handler.end_headers puts on an unversioned
        # image — reproduced byte-for-byte, no more, no less.
        self.assertEqual(r.headers.get("cache-control"), "public, max-age=86400")
        # Last-Modified is present (the stdlib always sent it).
        self.assertIsNotNone(r.headers.get("last-modified"))
        # Content-Length exact, body byte-for-byte the file on disk.
        self.assertEqual(r.headers.get("content-length"), str(len(on_disk)))
        self.assertEqual(r.content, on_disk)
        # The legacy static response sent NO etag and NO accept-ranges — a
        # FileResponse would have added both. This is the FileResponse guard.
        for h in _FORBIDDEN_DEFAULT_HEADERS:
            self.assertNotIn(h, {k.lower() for k in r.headers.keys()}, h)

    def test_logo_sin_extension_no_cae_al_default(self):
        # Asymmetry preserved: /logo has NO shipped default, so even with
        # logo.png at the repo root and no upload, /logo is a no-store 404 —
        # NOT the shipped png the .png URL falls back to.
        self.assertIsNotNone(branding.default_logo_path())
        r = self.client.get("/logo")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.headers.get("cache-control"), "no-store")

    def test_conditional_get_304_si_no_cambia(self):
        # If-Modified-Since matching the Last-Modified -> 304, the one
        # conditional SimpleHTTPRequestHandler honored. The 304 keeps the
        # one-day Cache-Control, carries no body, and — matching send_head's
        # NOT_MODIFIED branch — does NOT carry Last-Modified (that header is
        # the 200 branch's alone).
        r1 = self.client.get("/logo.png")
        lm = r1.headers.get("last-modified")
        self.assertIsNotNone(lm)
        r2 = self.client.get("/logo.png", headers={"If-Modified-Since": lm})
        self.assertEqual(r2.status_code, 304)
        self.assertEqual(r2.headers.get("cache-control"), "public, max-age=86400")
        self.assertIsNone(r2.headers.get("last-modified"))
        self.assertEqual(r2.content, b"")

    def test_conditional_get_200_si_es_mas_nuevo(self):
        # An If-Modified-Since OLDER than the file's mtime -> 200 (re-served),
        # never a spurious 304.
        from email.utils import formatdate
        shipped = branding.default_logo_path()
        self.assertIsNotNone(shipped)
        # One day into the past, so the file is "newer" than the condition.
        old = formatdate(os.path.getmtime(shipped) - 86400, usegmt=True)
        r = self.client.get("/logo.png", headers={"If-Modified-Since": old})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("content-type"), "image/png")

    def test_if_none_match_suprime_el_ims(self):
        # send_head skips the If-Modified-Since check ENTIRELY when
        # If-None-Match is present (it has no ETag to compare), so the resource
        # is re-served as 200 — even when If-Modified-Since would otherwise
        # match. The previous implementation ignored If-None-Match and returned
        # 304 here; that broadened the stdlib contract.
        r1 = self.client.get("/logo.png")
        lm = r1.headers.get("last-modified")
        self.assertIsNotNone(lm)
        r2 = self.client.get("/logo.png", headers={
            "If-Modified-Since": lm,
            "If-None-Match": '"deadbeef"',
        })
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.headers.get("content-type"), "image/png")

    def test_ims_offset_no_utc_es_200(self):
        # send_head compares ONLY when the parsed IMS has no timezone (read as
        # UTC) or an explicit UTC timezone. A non-UTC offset is left
        # un-compared -> 200. The previous implementation normalized any offset
        # to a timestamp via .timestamp() and could return 304 here; that
        # broadened the stdlib contract (this is the case that would have 304'd
        # before and 200s now).
        import datetime
        from email.utils import format_datetime
        shipped = branding.default_logo_path()
        self.assertIsNotNone(shipped)
        mtime = os.path.getmtime(shipped)
        # The SAME instant as the file's mtime, but expressed in a -03:00
        # offset: a non-UTC tzinfo stdlib refuses to compare.
        offset = datetime.timezone(datetime.timedelta(hours=-3))
        ims_dt = datetime.datetime.fromtimestamp(mtime, offset).replace(microsecond=0)
        r = self.client.get("/logo.png",
                            headers={"If-Modified-Since": format_datetime(ims_dt)})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("content-type"), "image/png")

    def test_ims_naive_sin_tz_se_trata_como_utc(self):
        # A naive IMS (no timezone) is read as UTC per RFC 7231's obsolete
        # format — send_head replaces tzinfo with UTC and compares. The Last-
        # Modified is the mtime as GMT, so the same instant written WITHOUT the
        # trailing GMT must still 304.
        r1 = self.client.get("/logo.png")
        lm = r1.headers.get("last-modified")
        self.assertIsNotNone(lm)
        naive = lm.replace(" GMT", "")
        self.assertNotIn("GMT", naive)  # sanity: we really stripped the tz
        r2 = self.client.get("/logo.png", headers={"If-Modified-Since": naive})
        self.assertEqual(r2.status_code, 304)
        self.assertIsNone(r2.headers.get("last-modified"))

    def test_ims_malformado_es_200(self):
        # An ill-formed If-Modified-Since is IGNORED (parsedate_to_datetime
        # raises; send_head catches TypeError/IndexError/OverflowError/
        # ValueError) -> the resource is re-served as 200. Never a crash, never
        # a spurious 304.
        r = self.client.get("/logo.png",
                            headers={"If-Modified-Since": "esto no es una fecha"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("content-type"), "image/png")


class TestUnreadableCustomFallback(_CustomLogoFixture):
    """Edge: a custom logo that EXISTS on disk but can't be READ (the upload left
    a 000-mode file, or the volume dropped) must not 500.
    `custom_logo_payload()` swallows the OSError and returns None, so /logo.png
    falls through to the shipped default and /logo to the no-store 404."""

    def setUp(self):
        super().setUp()
        branding.save_logo(_CLEAN_SVG)  # a custom logo IS configured...
        # ...but make it unreadable. _logo_path() still finds it (os.path.exists
        # ignores mode), and custom_logo_payload()'s open() then raises OSError.
        logo = branding._logo_path()
        self.assertIsNotNone(logo)
        os.chmod(logo, 0)
        # root ignores the 000 mode (CI containers run as root), so the
        # denial the mode stands for is also raised at branding's own open().
        def denied_open(path, *args, **kwargs):
            if os.fspath(path) == logo:
                raise PermissionError(13, "Permission denied", logo)
            return open(path, *args, **kwargs)
        patcher = mock.patch.object(branding, "open", denied_open, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(_app())
        self._logo = logo

    def tearDown(self):
        # Restore readability so tearDown's rmtree can clean the temp dir even
        # on platforms where rmtree doesn't ignore the mode.
        try:
            os.chmod(self._logo, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        super().tearDown()

    def test_logo_png_cae_al_default_si_el_custom_no_se_puede_leer(self):
        shipped = branding.default_logo_path()
        self.assertIsNotNone(shipped)
        r = self.client.get("/logo.png")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("content-type"), "image/png")
        with open(shipped, "rb") as f:
            self.assertEqual(r.content, f.read())

    def test_logo_es_404_si_el_custom_no_se_puede_leer(self):
        # /logo has no shipped default to fall to; unreadable custom -> 404.
        r = self.client.get("/logo")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.headers.get("cache-control"), "no-store")


class TestMissingDefault(_CustomLogoFixture):
    """Leg 3: an install with NO shipped logo.png and no admin upload -> a 404
    that is never cached, on BOTH /logo.png and /logo."""

    def setUp(self):
        super().setUp()
        self._orig = branding.default_logo_path
        branding.default_logo_path = lambda: None  # install with no shipped logo
        self.client = TestClient(_app())

    def tearDown(self):
        branding.default_logo_path = self._orig
        super().tearDown()

    def test_logo_png_sin_default_es_404_no_store(self):
        r = self.client.get("/logo.png")
        self.assertEqual(r.status_code, 404)
        # Errors are never cached — same as Handler.end_headers on code >= 400.
        self.assertEqual(r.headers.get("cache-control"), "no-store")


class TestFullAppNativeReachability(_CustomLogoFixture):
    """The native route is reachable in the real `app.app` composition."""

    def setUp(self):
        super().setUp()
        branding.save_logo(_CLEAN_SVG)
        # Import app lazily so the branding-only tests above don't pay for the
        # full composition import. Boot env mirrors test_app_composition.py.
        if "TURNOS_SKIP_HSI" not in os.environ:
            _env.set_env(self, TURNOS_SKIP_HSI="1")
        from app import main as app_module
        self.client = TestClient(app_module.app)

    def test_logo_png_alcanza_el_endpoint_nativo_en_la_app_completa(self):
        r = self.client.get("/logo.png")
        self.assertEqual(r.status_code, 200)
        # The custom SVG body proves the native branding route answered.
        self.assertEqual(r.content, _CLEAN_SVG)
        self.assertEqual(r.headers.get("content-type"), "image/svg+xml")
        for k, v in branding.CUSTOM_LOGO_HEADERS:
            self.assertEqual(r.headers.get(k), v, k)


if __name__ == "__main__":
    unittest.main()
