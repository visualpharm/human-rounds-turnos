"""Tier 4 slice 6, batch 3 — the client manifest, page by page.

`core/static_fingerprint.py` publishes the CLIENT_LOADED cohort (the assets a
STATIC JS file loads by URL at runtime) to the browser as
`window.SANA_STATIC`, through `manifest_script()`. The rule this file pins:
**a page that emits any runtime loader emits the manifest — once, in
<head>, before any deferred loader can run — with exactly the CLIENT_LOADED
keys resolved to their CURRENT fingerprinted URLs.** Two layers:

  * source level — every Jinja template that references a loader asset
    carries the manifest call/token, directly or through the admin base
    template it extends;
  * rendered level — the home, the three static pages, the account and
    account-reset pages, and every `_shell()` chrome variant (the wizard,
    /my-requests, the offer page and the interview page all go through it)
    carry the exact `manifest_script()` output once, before `</head>`.

The engine itself (manifest() == {name: url(name)}, the `{{static.manifest}}`
render token, serving contracts) is pinned in test_cache_headers.py; the
loaders' fail-closed lookups in tests/static-manifest-loaders.test.mjs; the
direct /admin/solicitudes screen in test_admin_screen_contract.py.

Run: python3 -m unittest discover -s tests -p "test_static_manifest.py"
"""

import json
import os
import re
import sys
import unittest
from starlette.requests import Request

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _patient_site_source  # noqa: E402
from app.core import site_identity, static_fingerprint  # noqa: E402
from app.cuentas import accounts  # noqa: E402
from app.patient_web import site_shell

# The loaders: static JS files that load a CLIENT_LOADED asset by URL at
# runtime. A page that emits any of these emits the manifest.
LOADERS = ("platform/account.mjs", "platform/account-requests.mjs", "platform/account-labs.mjs",
           "platform/admin-account.mjs")
CHAT_BOOTSTRAP_INCLUDE = '{% include "_chat_bootstrap.html" %}'

_MANIFEST_RE = re.compile(r"<script>window\.SANA_STATIC=(\{.*?\});</script>")


def _text(rendered):
    # site_identity.render_page() returns (body_bytes, ctype); the account
    # renderers return bytes; _shell() returns bytes.
    if isinstance(rendered, tuple):
        rendered = rendered[0]
    return rendered.decode("utf-8") if isinstance(rendered, bytes) else rendered


def _request(path="/account"):
    return Request({
        "type": "http", "method": "GET", "scheme": "http", "path": path,
        "raw_path": path.encode("ascii"), "query_string": b"", "headers": [],
        "client": ("127.0.0.1", 0), "server": ("testserver", 80),
    })


class _ManifestAssertions:
    def assertManifestOnce(self, html, page):
        html = _text(html)
        hits = _MANIFEST_RE.findall(html)
        self.assertEqual(len(hits), 1, f"{page}: window.SANA_STATIC must appear exactly once")
        self.assertIn(static_fingerprint.manifest_script(), html,
                      f"{page}: the manifest must be the exact manifest_script() output")
        payload = json.loads(hits[0])
        self.assertEqual(payload, static_fingerprint.manifest(), page)
        self.assertEqual(set(payload), static_fingerprint.CLIENT_LOADED, page)
        for name, url in payload.items():
            self.assertEqual(url, static_fingerprint.url(name), f"{page}: {name}")
        # In <head>, before </head> — so it has run before any deferred or
        # dynamically appended loader can look it up.
        self.assertIn("</head>", html, page)
        self.assertLess(html.index("window.SANA_STATIC="), html.index("</head>"), page)
        # And before the first loader <script> the page emits, if any.
        loader_positions = [
            html.index(static_fingerprint.url(name))
            for name in LOADERS
            if f'src="{static_fingerprint.url(name)}"' in html
        ]
        if loader_positions:
            self.assertLess(html.index("window.SANA_STATIC="), min(loader_positions), page)
        # No `?v=` alias for any client-loaded asset survives on the page.
        for name in static_fingerprint.CLIENT_LOADED:
            self.assertNotIn(f"/{name}?v=", html, page)


class TestTemplatesThatEmitALoaderEmitTheManifest(unittest.TestCase):
    """Source level: loader ⇒ manifest, for every template in templates/."""

    def _templates(self):
        for dirpath, _dirs, files in os.walk(os.path.join(ROOT, "templates")):
            for fn in sorted(files):
                if fn.endswith(".html"):
                    yield os.path.join(dirpath, fn)

    def test_every_loader_template_carries_the_manifest_call(self):
        base_path = os.path.join(ROOT, "templates", "admin", "base.html")
        with open(base_path, encoding="utf-8") as fh:
            base = fh.read()
        self.assertEqual(base.count("{{ static_manifest() }}"), 1,
                         "templates/admin/base.html must emit the manifest exactly once")
        self.assertLess(base.index("{{ static_manifest() }}"), base.index("</head>"))
        loader_ref = re.compile(
            r"static\('(" + "|".join(map(re.escape, LOADERS)) + r")'\)")
        extends_base = re.compile(r"\{%\s*extends\s+[\"']admin/base\.html[\"']\s*%\}")
        seen = []
        for path in self._templates():
            rel = os.path.relpath(path, ROOT)
            with open(path, encoding="utf-8") as fh:
                src = fh.read()
            loaders = sorted(set(loader_ref.findall(src)))
            has_chat_bootstrap = CHAT_BOOTSTRAP_INCLUDE in src
            if not loaders and not has_chat_bootstrap:
                continue
            seen.append(rel)
            with self.subTest(template=rel, loaders=loaders):
                if extends_base.search(src):
                    # Inherits base.html's <head>, which carries it.
                    self.assertNotIn("static_manifest()", src,
                                     f"{rel}: the base template already emits it; no second copy")
                else:
                    self.assertEqual(src.count("{{ static_manifest() }}"), 1, rel)
                    self.assertLess(src.index("{{ static_manifest() }}"), src.index("</head>"), rel)
            for name in static_fingerprint.CLIENT_LOADED:
                self.assertNotIn(f"/{name}?v=", src, rel)
        # The pages the design names plus Account's admin screen (admin-account.js).
        # Direct Requests/Queue/Doctors no longer static() the overlay: they
        # inherit the shared admin manifest and dynamic-import
        # platform/admin-create-appointment.mjs. /account/reset imports
        # platform/account-form.mjs through SANA_STATIC rather than
        # static('…'), so it is pinned by the native-import test below.
        self.assertEqual(set(seen), {
            "templates/index.html", "templates/about.html", "templates/privacy.html",
            "templates/terms.html", "templates/account.html",
            "templates/admin/account.html",
        })

    def test_account_screens_emit_the_manifest_and_import_the_native_module(self):
        for rel, marker in (
                ("templates/account.html", "Account.renderAuth"),
                ("templates/account-reset.html", "Account.renderReset"),
        ):
            with self.subTest(template=rel):
                path = os.path.join(ROOT, rel)
                with open(path, encoding="utf-8") as fh:
                    src = fh.read()
                self.assertEqual(src.count("{{ static_manifest() }}"), 1, rel)
                self.assertLess(src.index("{{ static_manifest() }}"), src.index("</head>"), rel)
                if rel == "templates/account.html":
                    self.assertIn("static('platform/account.mjs')", src, rel)
                    self.assertIn("Account.mount(host", src, rel)
                    with open(os.path.join(ROOT, "static/platform/account.mjs"), encoding="utf-8") as controller:
                        src += controller.read()
                self.assertIn('window.SANA_STATIC["platform/account-form.mjs"]', src, rel)
                self.assertIn(marker, src, rel)
                self.assertNotIn("static('account-form.js')", src, rel)
                self.assertNotIn("SanaAccount", src, rel)
                if rel == "templates/account.html":
                    self.assertIn('window.SANA_STATIC["platform/dependents.mjs"]', src, rel)
                    self.assertIn("Dep.renderCuenta", src, rel)
                    self.assertNotIn("static('dependents.js')", src, rel)
                    self.assertIn(CHAT_BOOTSTRAP_INCLUDE, src, rel)
                    self.assertNotIn("static('chat-widget.js')", src, rel)
                for name in static_fingerprint.CLIENT_LOADED:
                    self.assertNotIn(f"/{name}?v=", src, rel)

    def test_login_screen_emits_the_manifest_and_imports_the_native_controller(self):
        path = os.path.join(ROOT, "templates", "admin", "login.html")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        self.assertEqual(src.count("{{ static_manifest() }}"), 1)
        self.assertLess(src.index("{{ static_manifest() }}"), src.index("</head>"))
        self.assertIn('window.SANA_STATIC["platform/admin-login.mjs"]', src)
        self.assertIn("m.boot({ pendingSubmit: pending })", src)
        self.assertNotIn("static('admin-login.js')", src)
        self.assertNotIn("SanaAdminLogin", src)
        for name in static_fingerprint.CLIENT_LOADED:
            self.assertNotIn(f"/{name}?v=", src)

    def test_patient_shell_source_emits_the_manifest_in_head(self):
        src = _patient_site_source.source()
        self.assertEqual(src.count("{static_fingerprint.manifest_script()}"), 1)
        # Right after the i18n bootstrap, inside <head>.
        self.assertIn("{i18n.bootstrap_script(lang=lang)}\n{static_fingerprint.manifest_script()}\n</head>", src)


class TestRenderedPagesCarryTheManifest(_ManifestAssertions, unittest.TestCase):
    """Rendered level: the real HTML every transport serves."""

    def test_home_and_the_three_static_pages(self):
        for filename in ("index.html", "about.html", "privacy.html", "terms.html"):
            with self.subTest(page=filename):
                html = site_identity.render_page(filename)
                self.assertManifestOnce(html, filename)
                page = _text(html)
                self.assertIn('id="stz-chat-launcher"', page)
                self.assertIn("platform/chat-widget.mjs", page)
                self.assertNotIn("chat-widget.js", page)

    def test_account_and_account_reset_pages(self):
        self.assertManifestOnce(accounts.render_account_body(_request("/account")), "account")
        self.assertManifestOnce(accounts.render_reset_body(_request("/account/reset")), "account-reset")

    def test_every_shell_chrome_variant(self):
        # Default chrome (patient pages), "chat" (the interview page) and
        # "focus" (the offer page, the wizard's focused steps): one shared
        # <head>, one manifest.
        for chrome in (None, "chat", "focus"):
            with self.subTest(chrome=chrome):
                kwargs = {} if chrome is None else {"chrome": chrome}
                html = site_shell._shell("Manifest | test", "", "<p>body</p>",
                                           "desc", index=False, **kwargs)
                self.assertManifestOnce(html, f"_shell(chrome={chrome!r})")
                page = _text(html)
                if chrome == "chat":
                    self.assertNotIn('id="stz-chat-launcher"', page)
                    self.assertNotIn('id="chat-boot-error"', page)
                else:
                    self.assertIn('id="stz-chat-launcher"', page)
                    self.assertIn("platform/chat-widget.mjs", page)
                    self.assertNotIn("chat-widget.js", page)

    def test_home_manifest_urls_are_what_the_serving_side_resolves_today(self):
        html = _text(site_identity.render_page("index.html"))
        payload = json.loads(_MANIFEST_RE.search(html).group(1))
        for name, url in payload.items():
            with self.subTest(name=name):
                self.assertEqual(static_fingerprint._resolve(url), name)
                self.assertEqual(static_fingerprint.transport_target(url)[2],
                                 "public, max-age=31536000, immutable")


if __name__ == "__main__":
    unittest.main()
