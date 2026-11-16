"""Characterization of `availability_pages.render_doctor_page` before it was cut into steps.

The page is one big f-string; every case below renders it through the public entry point and pins the
whole output (length + sha256) plus the fragments a reader can check by eye. Page bytes must not move
when the function is restructured: a hash that changes means the output changed, not that the test is
stale. The page has no per-language branches (it is Spanish only); the branches it does have are the
avatar (sprite / photo / initials, with or without zoom), the coverage card (all insurers / list /
particular, with or without copay), the availability band (friendly time / raw fallback / none), the
online badge and the optional JSON-LD image.
"""

import hashlib
import json
import pathlib
import re
import sys
import unittest
from unittest.mock import patch

import _env

_env.process_defaults(TURNOS_ENV_FILE="/dev/null", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.patient_web import availability_pages  # noqa: E402


def _row(**over):
    base = {
        "nombre": "Dr. BETO LOPEZ",
        "slug": "beto-lopez",
        "especialidad": "Cardiología",
        "clinica": "Centro Modelo",
        "grupos": ["Cardiología"],
        "online": False,
        "obras_sociales": [],
        "rating": 4.25,
    }
    base.update(over)
    return base


# A date far in the past or future gives a fixed label whatever day the suite runs:
# "Hoy 10:30" for the past, "<weekday> dd/mm · HH:MM" beyond a week.
_PAST = "2001-02-03 10:30:00"
_FUTURE = "2999-01-01 08:15:00"  # a Tuesday

_SPRITE = {
    "image": "/sprites/doc.webp?v=3",
    "bg_size": "1200px 800px",
    "bg_pos": "-100px -200.5px",
}

CASES = {
    "initials_only": _row(),
    "male_online_future_osde": _row(
        nombre="Dr. CARLOS GOMEZ", slug="carlos-gomez", online=True, obras_sociales=["OSDE"],
        proximo_turno=_FUTURE),
    "female_photo_zoom_past_turno": _row(
        nombre="Dra. ANA PEREZ", slug="ana-perez", gender="f", online=True, foto="/fotos/ana.jpg",
        photo_zoom="/fotos/ana-big.jpg", obras_sociales=["Swiss Medical", "Mutual Rara", "IOMA"],
        proximo_turno=_PAST),
    "photo_zoom_falls_back_to_foto": _row(foto="/fotos/beto.jpg", display_name="Dr. Beto L."),
    "sprite_avatar": _row(
        gender="f", profile_sprite=_SPRITE, foto="/fotos/x.jpg", photo_zoom="/fotos/x-big.jpg"),
    "sprite_without_photo_or_zoom": _row(profile_sprite=_SPRITE),
    "sprite_with_zoom_only": _row(profile_sprite=_SPRITE, photo_zoom="/fotos/zoom-only.jpg"),
    "all_insurers_with_copay_amount": _row(
        acepta_todas=True, obras_sociales=["OSDE"], copago=True, copago_monto="$ 4.500"),
    "all_insurers_with_copay_no_amount": _row(acepta_todas=True, copago=True),
    "list_with_copay_amount": _row(obras_sociales=["PAMI", "Galeno"], copago=True, copago_monto="$ 2.000"),
    "lowercase_unknown_insurer_letter": _row(obras_sociales=["obra del sur"]),
    "particular_with_copay_no_amount": _row(copago=True),
    "particular_plain": _row(obras_sociales=None),
    "friendly_empty_uses_raw_fmt": _row(
        proximo_turno="not a date", proximo_turno_fmt="Lunes 12/10 9:00", online=True),
    "friendly_and_fmt_both_empty": _row(proximo_turno="not a date", online=True),
    "friendly_wins_over_fmt": _row(proximo_turno=_FUTURE, proximo_turno_fmt="ignored text"),
    "explicit_esp_slug_known_gendered": _row(esp_slug="ginecologia", gender="f", grupos=[]),
    "explicit_esp_slug_unknown_male": _row(esp_slug="algo-raro", grupos=["Grupo A", "Grupo B"]),
    "derived_slug_from_accented_especialidad": _row(
        especialidad="Traumatología y Kinesiología", clinica="Clínica Ñandú"),
    "escaping_everywhere": _row(
        nombre='Dr. <B>"&" ZETA', display_name='Dra. <i>"Q"</i> & Co', gender="f", foto='/f/"x".jpg',
        clinica='Clínica <script>&"x"', obras_sociales=['<Obra> & "Co"'], copago=True,
        copago_monto="<$1>", online=True, proximo_turno_fmt="<b>pronto</b>", proximo_turno="x"),
    "blank_name_initials_fallback": _row(nombre="  "),
    "single_word_name": _row(nombre="Madonna", slug="madonna"),
    "three_word_name_initials": _row(nombre="Juan Carlos de la Cruz", slug="jc"),
    "rating_rounds": _row(rating=3.96),
}

# length, sha256 of the full page for each case, taken from the code before the cut
EXPECTED = {
    'lowercase_unknown_insurer_letter': (12875, '40329b959978956bd157046a599042cee4148db3ed4b4495ea81e21e4fa60011'),
    'initials_only': (12820, '2a46a8f1fe7289210a74593fd37b13ebd89e9d0f57ee4057b01a3dc4e536e94d'),
    'male_online_future_osde': (13075, '71d3f2d00c21aad1c4d9d0ae4a8be9e1a1680e7f935897a242c5b6c9c90e891a'),
    'female_photo_zoom_past_turno': (13713, '8665be85effd3fce9fe20885a7fa434bfafd80562000087ef2d5b8ad1f52721d'),
    'photo_zoom_falls_back_to_foto': (13025, '4184db0baf0ab410b9a83c0f366843b04b5a9e37f15dcba5064b86a869e1101a'),
    'sprite_avatar': (13168, 'ee7a043b80c945bd41e5beebd5c229a6e5e2424c44246978b8c343fde60441dd'),
    'sprite_without_photo_or_zoom': (12975, '0e68bd86595610002bf0c3416212c414f5610da4f5f7cc39d1ba77248ad93919'),
    'sprite_with_zoom_only': (13142, 'e7fca9064c14d728146c7a28aadc2e45b882b033379a929e4b507d6f126377c1'),
    'all_insurers_with_copay_amount': (12966, '397023323a0714b7f81e0fbc665ba094865b5c7c58dc7a81f13dc47e288d5d91'),
    'all_insurers_with_copay_no_amount': (12955, 'df6e400cfa8ff2b31d573003ff8b435d18d76d84f68a36b806d04cd6ba1a6b85'),
    'list_with_copay_amount': (13489, 'a92f06199d80dda37a3c3277a6135fc497ef61e5b19600b19d4f0c2be2a29445'),
    'particular_with_copay_no_amount': (12955, 'd122104c264fda7d6e7735ad4d9ec62f3da388e0e63b898b0d149182da8faa96'),
    'particular_plain': (12820, '2a46a8f1fe7289210a74593fd37b13ebd89e9d0f57ee4057b01a3dc4e536e94d'),
    'friendly_empty_uses_raw_fmt': (12828, 'b05e68d8d47eb8c3f19c17754d2c05f626392adc99c7ce7e5930af6f670bc5b6'),
    'friendly_and_fmt_both_empty': (12839, 'b97ba0c19d28dc6f1a085c9273b010cd4e6dea62d5e26ee8e74f876fcb7708ec'),
    'friendly_wins_over_fmt': (12811, 'bcca57731374e7ba997aa10169a4d5e43c3eafcd0a33f3307824224f132b0355'),
    'explicit_esp_slug_known_gendered': (12825, '068d32142745ec6f06d28088321cc71b971f270e1648fef0841a986f9f89c431'),
    'explicit_esp_slug_unknown_male': (12779, '9aaf444d3fee9433b30eb9ab40613298503bc86ed74ab4ce4ba1fcd739a3abaf'),
    'derived_slug_from_accented_especialidad': (12946, 'b652806cc5a31ad3f207730f0052e226915d3548a45d37ad58a72e1736726ff8'),
    'escaping_everywhere': (13624, '1c4cc768b363deaf69d9a9b800281327385a584ebf65245f18fa235429145330'),
    'blank_name_initials_fallback': (12747, 'b7d411bab1a2321e68c0c7166b73a0c861ae3c434a6ae14a81b62cbe11f5e7c2'),
    'single_word_name': (12771, '560b52a64b5e0b1fa52f76c17f6f1401643282cdbd41ebf3cea8294167882d7e'),
    'three_word_name_initials': (12852, '56aa33c82f76999cae3977c2089437a23e2bc7f05220450fc36211adc39a1ce8'),
    'rating_rounds': (12820, '57563bcfda872887611aae08b8478792b5e35647994e47f392e3712ab31a03ed'),
}


_TEST_LOGODEV_KEY = "pk_test-logo"


def _render(row):
    stubs = (
        patch.object(
            availability_pages.site_identity, "get",
            side_effect=lambda key: {"city": "Villa Ejemplo", "province": "Provincia Modelo"}.get(key, "")),
        patch.object(availability_pages.theme, "brand_name", return_value="Human Rounds"),
        patch.object(availability_pages.theme, "current", return_value="human-rounds"),
        patch.object(availability_pages.theme, "favicon", return_value="<fav>"),
        patch.object(availability_pages.theme, "fonts", return_value="<fonts>"),
        patch.object(
            availability_pages.seo, "canonical_url", side_effect=lambda path: "https://example.test" + path),
        patch.object(availability_pages.seo, "head", side_effect=lambda t, d, p: "<seo|%s|%s|%s>" % (t, d, p)),
        patch.object(availability_pages.static_fingerprint, "url", side_effect=lambda path: "/static/" + path),
        patch.object(availability_pages, "_logodev_key", return_value=_TEST_LOGODEV_KEY),
    )
    for stub in stubs:
        stub.start()
    try:
        return availability_pages.render_doctor_page(row)
    finally:
        for stub in reversed(stubs):
            stub.stop()


def _text(name):
    return _render(CASES[name]).decode("utf-8")


class TestDoctorPageBytes(unittest.TestCase):
    def test_every_case_is_pinned_byte_for_byte(self):
        got = {}
        for name, row in CASES.items():
            body = _render(dict(row))
            got[name] = (len(body), hashlib.sha256(body).hexdigest())
        if not EXPECTED:
            self.fail("fill EXPECTED:\n" + "\n".join("    %r: %r," % (k, v) for k, v in got.items()))
        self.assertEqual(sorted(got), sorted(EXPECTED))
        for name in CASES:
            with self.subTest(name):
                self.assertEqual(got[name], EXPECTED[name])

    def test_render_is_pure_and_returns_utf8_bytes(self):
        row = dict(CASES["female_photo_zoom_past_turno"])
        before = json.dumps(row, sort_keys=True)
        first = _render(row)
        self.assertIsInstance(first, bytes)
        self.assertEqual(json.dumps(row, sort_keys=True), before)
        self.assertEqual(_render(row), first)
        self.assertTrue(first.startswith(b"<!DOCTYPE html>\n<html lang=\"es\" data-theme=\"human-rounds\">"))
        self.assertTrue(first.endswith(b"</body>\n</html>"))

    def test_missing_required_key_raises_keyerror(self):
        row = _row()
        del row["clinica"]
        with self.assertRaises(KeyError):
            _render(row)
        row = _row()
        del row["rating"]
        with self.assertRaises(KeyError):
            _render(row)


class TestDoctorPageSections(unittest.TestCase):
    """Readable assertions on the pieces, so a failing hash comes with a reason."""

    def test_avatar_initials_without_photo(self):
        page = _text("initials_only")
        self.assertIn('<div class="pf-av pf-av-i">DB</div>', page)
        self.assertNotIn("pf-photo-btn\" type", page)

    def test_avatar_photo_with_zoom_button(self):
        page = _text("female_photo_zoom_past_turno")
        self.assertIn(
            '<button class="pf-photo-btn" type="button" data-photo-zoom="/fotos/ana-big.jpg" '
            'data-photo-alt="Dra. ANA PEREZ"', page)
        self.assertIn('<img class="pf-av" src="/fotos/ana.jpg" alt="Dra. ANA PEREZ" loading="lazy"></button>', page)
        ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page).group(1))
        self.assertEqual(ld["image"], "/fotos/ana.jpg")
        self.assertEqual(ld["name"], "Dra. Dra. ANA PEREZ")

    def test_zoom_falls_back_to_foto_and_display_name_wins(self):
        page = _text("photo_zoom_falls_back_to_foto")
        self.assertIn('data-photo-zoom="/fotos/beto.jpg" data-photo-alt="Dr. Beto L."', page)
        self.assertIn("<h1", page)

    def test_sprite_avatar_scales_tiles_to_76px(self):
        page = _text("sprite_avatar")
        self.assertIn(
            'style="background-image:url(/sprites/doc.webp?v=3);'
            'background-size:912.00px 608.00px;background-position:-76.00px -152.38px;"', page)
        self.assertIn('role="img"', page)
        self.assertNotIn('<img class="pf-av"', page)

    def test_sprite_without_zoom_has_no_button(self):
        page = _text("sprite_without_photo_or_zoom")
        self.assertIn('<div class="pf-av" role="img"', page)
        self.assertNotIn("data-photo-zoom=", page)

    def test_sprite_with_zoom_only_zooms_that_source(self):
        page = _text("sprite_with_zoom_only")
        self.assertIn('data-photo-zoom="/fotos/zoom-only.jpg"', page)
        self.assertIn('<div class="pf-av" role="img"', page)

    def test_online_badge_both_ways(self):
        self.assertIn('<span class="pf-badge pf-badge-on"><span class="pf-dot"></span>Tiene turnos online</span>',
                      _text("male_online_future_osde"))
        self.assertIn('<span class="pf-badge pf-badge-off">Sin turnos online por ahora</span>', _text("initials_only"))

    def test_coverage_all_insurers_and_copay(self):
        page = _text("all_insurers_with_copay_amount")
        self.assertIn('<div class="pf-cob-wrap"><span class="pf-todas">Todas las obras sociales</span></div>', page)
        self.assertIn("Coseguro/co-pago de $ 4.500 en la consulta</div>", page)
        self.assertNotIn("OSDE", page)
        self.assertIn("Coseguro/co-pago en la consulta</div>", _text("all_insurers_with_copay_no_amount"))

    def test_coverage_list_chips_logo_and_letter(self):
        page = _text("female_photo_zoom_past_turno")
        self.assertIn('href="/cobertura/swiss-medical"><img src="https://img.logo.dev/swissmedical.com.ar?', page)
        self.assertIn('<a class="pf-cob" href="/cobertura/mutual-rara"><span class="pf-cob-i">M</span><span>Mutual Rara</span></a>', page)
        self.assertNotIn("pf-copay", page.split("</style>")[1])
        self.assertIn('<span class="pf-cob-i">O</span><span>obra del sur</span>', _text("lowercase_unknown_insurer_letter"))

    def test_logo_token_comes_from_the_environment(self):
        self.assertIn("token=%s&amp;" % _TEST_LOGODEV_KEY, _text("female_photo_zoom_past_turno"))
        _env.set_env(self, TURNOS_LOGODEV_KEY=None)
        self.assertEqual(availability_pages._insurer_logo_tag("Swiss Medical"), "")
        _env.set_env(self, TURNOS_LOGODEV_KEY=" pk_from-env ")
        self.assertIn("token=pk_from-env&amp;size=28", availability_pages._insurer_logo_tag("Swiss Medical"))

    def test_coverage_particular_with_and_without_copay(self):
        plain = _text("particular_plain")
        self.assertIn("Atiende de forma particular.</div>", plain)
        self.assertNotIn("Coseguro", plain)
        both = _text("particular_with_copay_no_amount")
        self.assertIn("Atiende de forma particular.</div><div class=\"pf-copay\"", both)

    def test_band_variants(self):
        future = _text("male_online_future_osde")
        self.assertIn('<div class="pf-band"><span class="pf-band-ico"', future)
        self.assertIn('<div class="pf-band-k">Próximo turno disponible</div>', future)
        self.assertIn('<div class="pf-band-v">Mar 01/01 · 08:15</div>', future)
        past = _text("female_photo_zoom_past_turno")
        self.assertIn('<div class="pf-band-v">Hoy 10:30</div>', past)
        raw = _text("friendly_empty_uses_raw_fmt")
        self.assertIn('<div class="pf-band-v">Lunes 12/10 9:00</div>', raw)
        none = _text("friendly_and_fmt_both_empty")
        self.assertIn('<div class="pf-band pf-band-off">', none)
        self.assertIn('<div class="pf-band-k">Turnos online</div>', none)
        self.assertIn('<div class="pf-band-v">Sin turnos online por ahora</div>', none)
        self.assertIn('<div class="pf-band-v">Mar 01/01 · 08:15</div>', _text("friendly_wins_over_fmt"))

    def test_title_description_and_paths(self):
        page = _text("explicit_esp_slug_known_gendered")
        self.assertIn("<seo|Dra. Dr. BETO LOPEZ — Ginecóloga en Villa Ejemplo, Provincia Modelo | Human Rounds|", page)
        self.assertIn("|/ginecologia/beto-lopez>", page)
        self.assertIn('<a href="/ginecologia/">Cardiología</a>', page)
        other = _text("explicit_esp_slug_unknown_male")
        self.assertIn("Dr. Dr. BETO LOPEZ — Médico en Villa Ejemplo", other)
        self.assertIn('<a href="/algo-raro/">Grupo A</a>', other)
        derived = _text("derived_slug_from_accented_especialidad")
        self.assertIn("|/traumatologia-y-kinesiologia/beto-lopez>", derived)
        self.assertIn('href="/clinica/clinica-nandu"', derived)
        self.assertIn("traumatólogo", derived)

    def test_escaping_and_initials_edge_cases(self):
        page = _text("escaping_everywhere")
        body = page.split("</style>")[1]
        self.assertNotIn("<script>&", body)
        self.assertNotIn("<i>", body)
        self.assertIn("Clínica &lt;script&gt;&amp;&quot;x&quot;</a>", body)
        self.assertIn('src="/f/&quot;x&quot;.jpg"', body)
        self.assertIn("&lt;b&gt;pronto&lt;/b&gt;", body)
        self.assertIn("&lt;$1&gt;", page)
        self.assertIn('<div class="pf-av pf-av-i">', _text("blank_name_initials_fallback"))
        self.assertIn('<div class="pf-av pf-av-i">M</div>', _text("single_word_name"))
        self.assertIn('<div class="pf-av pf-av-i">JC</div>', _text("three_word_name_initials"))
        self.assertIn("★ 4.0</span> Google", _text("rating_rounds"))
        self.assertIn("★ 4.2</span> Google", _text("initials_only"))


if __name__ == "__main__":
    unittest.main()
