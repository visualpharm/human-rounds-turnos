"""/about.html: los números de bloque («01», «02»…) llegan a 4,5:1 en los dos temas
(estudio de usabilidad 2026-10-16: #9aa29b daba 2,44:1 en pinamar y 2,32:1 en humanrounds)."""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _lum(hex_):
    h = hex_.lstrip("#")
    ch = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    f = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in ch]
    return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]


def _ratio(a, b):
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _theme_block(css, selector_start):
    start = css.index(selector_start)
    return css[start:css.index("}", start)]


class AboutIndexContrastTest(unittest.TestCase):
    def setUp(self):
        self.tokens = (ROOT / "static" / "root" / "tokens.css").read_text()
        self.about = (ROOT / "templates/about.html").read_text()

    def test_index_uses_the_theme_token_not_a_literal_grey(self):
        rule = re.search(r"\.about-index\{[^}]*\}", self.about).group(0)
        self.assertIn("color:var(--index-ink)", rule)
        self.assertNotRegex(rule, r"color:#")

    def test_both_themes_pass_aa_on_their_page_background_and_secondary_fill(self):
        themes = {
            "pinamar": _theme_block(self.tokens, ':root, :root[data-theme="pinamar"]{'),
            "humanrounds": _theme_block(self.tokens, ':root[data-theme="humanrounds"]{'),
        }
        for name, block in themes.items():
            with self.subTest(theme=name):
                ink = re.search(r"--index-ink:(#[0-9a-fA-F]{6})", block).group(1)
                bg = re.search(r"--bg:(#[0-9a-fA-F]{6})", block).group(1)
                bg2 = re.search(r"--bg-2:(#[0-9a-fA-F]{6})", block).group(1)
                self.assertGreaterEqual(_ratio(ink, bg), 4.5, f"{ink} on {bg}")
                if name == "humanrounds":
                    self.assertGreaterEqual(_ratio(ink, bg2), 4.5, f"{ink} on {bg2}")


if __name__ == "__main__":
    unittest.main()
