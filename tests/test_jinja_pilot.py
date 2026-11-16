"""Contracts for the four canonical public Jinja templates.

The suite compares ``site_identity.render_page`` (the one function both the
native router and the (now-retired) dark transport called) against a manual,
step-by-step re-derivation of its own pipeline, in Spanish and English, and
verifies that English actually resolves. It also prevents root-level HTML
copies from reappearing. Both `_render_shared` and
`_render_new` below exercise only native code — the comparison still has
value as an absolute pin of `render_page`'s internal step order (a future
refactor that silently drops a step, e.g. `theme.inject`, breaks this test).
HTTP transport parity remains covered by ``scripts/route_parity_check.py``.
"""

import os
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
from app.ops import demo_installation  # noqa: E402
from app.core import i18n  # noqa: E402
from app.core import jinja_env  # noqa: E402
from app.core import site_identity  # noqa: E402
from app.core import theme  # noqa: E402

PAGES = ("index.html", "about.html", "privacy.html", "terms.html")


def _render_shared(page, lang):
    _lang, token = i18n.bind_lang(lang)
    try:
        body, ctype = site_identity.render_page(page, lang=_lang)
        return body, ctype
    finally:
        i18n.unbind(token)


def _render_new(page, lang):
    """Manually re-derives `render_page`'s own HTML pipeline step by step —
    same steps, same order, same arguments — minus the FastAPI
    Request/Response wrapping, which is what `route_parity_check.py`'s
    HTTP-level scenarios exist to prove instead."""
    _lang, token = i18n.bind_lang(lang)
    try:
        active_lang = i18n.active()
        rendered = jinja_env.render(page)
        rendered = i18n.inject_html_lang(rendered, active_lang)
        rendered = i18n.inject_bootstrap(rendered, None, active_lang)
        rendered = theme.inject(rendered)
        rendered = demo_installation.inject_html(
            rendered, "public", lang=active_lang, switchable=True)
        return rendered.encode("utf-8"), "text/html; charset=utf-8"
    finally:
        i18n.unbind(token)


class TestJinjaPilotByteIdentity(unittest.TestCase):
    """Canonical language (Spanish, LANGUAGES unset) — i18n is a true
    no-op here (docs/architecture/i18n.md's own invariant), so this is the
    strictest possible check: zero translation machinery involved, pure
    template-mechanism parity."""

    def setUp(self):
        self.addCleanup(i18n.invalidate)  # both registered before the env: they run once it is back
        self.addCleanup(config._reset_cache_para_tests)
        _env.set_env(self, LANGUAGES=None)
        config._reset_cache_para_tests()
        i18n.invalidate()

    def test_all_four_pages_byte_identical_in_spanish(self):
        for page in PAGES:
            with self.subTest(page=page):
                shared_body, shared_ctype = _render_shared(page, "es")
                direct_body, direct_ctype = _render_new(page, "es")
                self.assertEqual(shared_ctype, direct_ctype)
                self.assertEqual(shared_body, direct_body)


class TestJinjaPilotNonCanonicalLanguage(unittest.TestCase):
    """English (LANGUAGES=es,en, cookie lang=en) — the design's own gate
    only requires "identical resolved strings" here; this suite clears the
    stronger byte-identical bar too (see module docstring)."""

    def setUp(self):
        self.addCleanup(i18n.invalidate)  # both registered before the env: they run once it is back
        self.addCleanup(config._reset_cache_para_tests)
        _env.set_env(self, LANGUAGES="es,en")
        config._reset_cache_para_tests()
        i18n.invalidate()

    def test_all_four_pages_byte_identical_in_english(self):
        for page in PAGES:
            with self.subTest(page=page):
                shared_body, shared_ctype = _render_shared(page, "en")
                direct_body, direct_ctype = _render_new(page, "en")
                self.assertEqual(shared_ctype, direct_ctype)
                self.assertEqual(shared_body, direct_body)

    def test_english_pages_are_actually_in_english(self):
        """A byte-identical bug that happens to match itself in BOTH
        implementations (e.g. both silently falling back to Spanish because
        the cookie wasn't honored) would pass the test above for the wrong
        reason — this asserts the English render actually resolved some
        real English text, not just that both paths agree on Spanish."""
        body, _ctype = _render_new("about.html", "en")
        html = body.decode("utf-8")
        self.assertIn("About", html)
        self.assertNotIn("Acerca de", html)


class TestPublicTemplateConvergence(unittest.TestCase):
    """Both transports must now consume the one canonical Jinja source."""

    def test_root_level_html_duplicates_are_retired(self):
        for page in PAGES:
            with self.subTest(page=page):
                self.assertFalse((Path(ROOT) / page).exists())
                self.assertTrue((Path(ROOT) / "templates" / page).is_file())

    def test_dark_render_calls_the_shared_jinja_renderer(self):
        with mock.patch.object(
                jinja_env, "render", wraps=jinja_env.render) as render:
            result = site_identity.render_page("about.html")
        self.assertIsNotNone(result)
        render.assert_called_once_with("about.html")


if __name__ == "__main__":
    unittest.main()
