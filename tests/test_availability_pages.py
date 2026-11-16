"""Parity and ownership contracts for public availability pages.

Native-migration final wave (2026-08-27): `server.py` and
`routers/legacy_http_transport.py` were retired along with the whole
stdlib transport, so the `TestOwnership` class that used to assert the
legacy handler delegated to `availability_pages.py` (source-text checks
against those two now-deleted files) is gone too — there is only one
transport left to own these pages. `availability_pages.render_doctor_page`/
`render_specialty_page` (TestRenderedParity) and the late matcher
`availability_pages.match()` (TestMatchDispatch) are unaffected: neither
ever imported `server.py`; the native transport that calls `match()` is
`routers/native_terminal.py` (covered by test_native_terminal.py).
"""

import hashlib
import pathlib
import sys
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_set(TURNOS_ENV_FILE="/dev/null")
_env.process_defaults(DATABASE_URL="")
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.patient_web import availability_directory  # noqa: E402
from app.patient_web import availability_pages  # noqa: E402


_DB = {
    "medicos": [
        {
            "nombre": "Dra. ANA PEREZ",
            "especialidad": "Cardiología",
            "clinica": "Clínica Ejemplo",
            "online": True,
            "obras_sociales": ["OSDE"],
            "rating": 4.7,
            "proximo_turno": "2026-08-20 10:30:00",
        },
        {
            "nombre": "Dr. BETO LOPEZ",
            "especialidad": "Cardiología",
            "clinica": "Centro Modelo",
            "online": False,
            "obras_sociales": [],
            "rating": 4.2,
        },
    ]
}


class TestRenderedParity(unittest.TestCase):
    def test_frozen_doctor_and_specialty_bytes(self):
        with patch.object(availability_directory, "_load_db", return_value=_DB):
            doctors = availability_directory.public_medicos()
            with (
                patch.object(
                    availability_pages.site_identity,
                    "get",
                    side_effect=lambda key: {
                        "city": "Villa Ejemplo",
                        "province": "Provincia Modelo",
                    }.get(key, ""),
                ),
                patch.object(availability_pages.theme, "brand_name", return_value="Human Rounds"),
                patch.object(availability_pages.theme, "current", return_value="human-rounds"),
                patch.object(availability_pages.theme, "favicon", return_value="<fav>"),
                patch.object(availability_pages.theme, "fonts", return_value="<fonts>"),
                patch.object(
                    availability_pages.seo,
                    "canonical_url",
                    side_effect=lambda path: "https://example.test" + path,
                ),
                patch.object(availability_pages.seo, "head", return_value="<seo>"),
                patch.object(
                    availability_pages.static_fingerprint,
                    "url",
                    side_effect=lambda path: "/static/" + path,
                ),
            ):
                doctor = availability_pages.render_doctor_page(doctors[0])
                specialty = availability_pages.render_specialty_page("cardiologia")
        # Exact reviewed consumer cutover only: keep the original byte pins
        # after reversing the one native tag, not normalising arbitrary HTML.
        native_nav = b'<script type="module" src="/static/platform/nav.mjs"></script>'
        classic_nav = b'<script src="/nav.js"></script>'
        for rendered in (doctor, specialty):
            self.assertEqual(rendered.count(native_nav), 1)
            self.assertNotIn(classic_nav, rendered)
        doctor = doctor.replace(native_nav, classic_nav)
        specialty = specialty.replace(native_nav, classic_nav)
        # Regenerated 2026-09-15 (native-nav merge): shared assets moved on
        # main between the Sept-10 snapshot and this cutover (hover tokens,
        # account-requests and filter-control fingerprints); reversing the
        # nav tag still isolates exactly the cutover's own bytes.
        # Re-pinned 2026-11-09: 2b0d35329 moved the photo close button onto
        # the shared `.hr-x` icon (-72 bytes: the × glyph and its own size and
        # font rules); the Spanish label through i18n renders the same bytes.
        self.assertEqual(len(doctor), 12474)
        self.assertEqual(
            hashlib.sha256(doctor).hexdigest(),
            "6da4b42312b5325942f20609e801dd35fc810ec9e0a43de8f161d533f441cee6",
        )
        self.assertEqual(len(specialty), 4392)
        self.assertEqual(
            hashlib.sha256(specialty).hexdigest(),
            "e5987b70021a488725177b981a521a7d679daf7a9bd6e89f2379ed4e313e720a",
        )

    def test_photo_close_button_is_named_through_i18n_in_the_pages_language(self):
        """The doctor photo's close button is named through i18n, not a literal (09.11).

        The page is a fixed-Spanish document (lang="es", every visible string a
        literal), so the label stays Spanish whatever the request bound: one
        pronunciation voice for the whole page, no lone control switching it."""
        from app.core import i18n

        with patch.object(availability_directory, "_load_db", return_value=_DB):
            doctors = availability_directory.public_medicos()
            for lang in ("es", "en"):
                _, token = i18n.bind_lang(lang)
                try:
                    page = availability_pages.render_doctor_page(doctors[0])
                finally:
                    i18n.unbind(token)
                self.assertIn(b'<button class="pf-lightbox-x hr-x" type="button" aria-label="Cerrar foto">', page)


class TestMatchDispatch(unittest.TestCase):
    """The late matcher's own dispatch contract. The native transport that
    calls `match()` (always with the ORIGINAL request path, never a
    rewritten one) is `routers/native_terminal.py` — its half lives in
    test_native_terminal.py."""

    def test_doctor_route_resolves_and_renders(self):
        row = {"slug": "ana-perez"}
        with (
            patch.object(availability_directory, "medico_by_url", return_value=row) as lookup,
            patch.object(availability_pages, "render_doctor_page", return_value=b"doctor") as render,
        ):
            result = availability_pages.match("GET", "/cardiologia/ana-perez")
        lookup.assert_called_once_with("cardiologia", "ana-perez")
        render.assert_called_once_with(row)
        self.assertEqual(result, availability_pages.HtmlResult(b"doctor"))

    def test_specialty_route_decodes_and_renders(self):
        with patch.object(
            availability_pages,
            "render_specialty_page",
            return_value=b"specialty",
        ) as render:
            result = availability_pages.match("GET", "/cardiolog%C3%ADa/")
        render.assert_called_once_with("cardiología")
        self.assertEqual(result, availability_pages.HtmlResult(b"specialty"))

    def test_unknown_known_prefix_and_non_get_decline(self):
        for method, path in (
            ("POST", "/cardiologia/ana"),
            ("GET", "/api/unknown"),
            ("GET", "/static/unknown"),
            ("GET", "/unknown"),
            ("GET", "/one/two/three"),
        ):
            with self.subTest(method=method, path=path):
                with (
                    patch.object(availability_directory, "medico_by_url", return_value=None) as lookup,
                    patch.object(availability_pages, "render_specialty_page", return_value=None),
                ):
                    self.assertIsNone(availability_pages.match(method, path))
                lookup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
