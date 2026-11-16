"""avatar_sprite.py — the shared atlas behind every small round portrait.

What is worth asserting here is not "an image came out" but the three things
that would silently break the home page:

  1. ONE image for every face (the whole point — it was 48 requests).
  2. The crop math. background-position is a percentage of the difference
     between image and box, not of the image, and getting it wrong shows
     every doctor with somebody else's face — which looks fine in a unit test
     and is a disaster on the page.
  3. The fallback. No Pillow, no sources, a broken file: styles() returns {}
     and patient_web/site_*.py goes back to per-photo URLs. It must never raise.
"""

import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.patient_web import avatar_sprite  # noqa: E402
from app.patient_web import doctor_photos  # noqa: E402

try:
    from PIL import Image
    HAY_PIL = True
except ImportError:  # pragma: no cover
    HAY_PIL = False


def _reset():
    avatar_sprite._cache = None
    avatar_sprite._deadline = 0.0
    avatar_sprite._man_cache = None
    avatar_sprite.invalidate()


@unittest.skipUnless(HAY_PIL, "Pillow no instalado")
class TestAtlas(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.fotos = os.path.join(self.tmp, "medico-fotos")
        os.makedirs(self.fotos)
        # Each portrait is a flat, unmistakable colour: after cropping the
        # atlas tile back out we can assert WHICH doctor landed where.
        self.colores = {
            "uno": (255, 0, 0), "dos": (0, 255, 0), "tres": (0, 0, 255),
            "cuatro": (255, 255, 0), "cinco": (255, 0, 255),
        }
        for mid, c in self.colores.items():
            Image.new("RGB", (400, 600), c).save(
                os.path.join(self.fotos, f"{mid}.png"))

        self._fotos_orig = doctor_photos.PHOTOS_DIR
        self._sprite_orig = avatar_sprite.SPRITE_DIR
        doctor_photos.PHOTOS_DIR = self.fotos
        avatar_sprite.SPRITE_DIR = os.path.join(self.tmp, "avatar-sprite")
        _reset()

    def tearDown(self):
        doctor_photos.PHOTOS_DIR = self._fotos_orig
        avatar_sprite.SPRITE_DIR = self._sprite_orig
        _reset()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _url(self, css):
        return re.search(r"url\('([^']+)'\)", css).group(1)

    def _pos(self, css):
        m = re.search(r"background-position:([\d.]+)% ([\d.]+)%", css)
        return float(m.group(1)), float(m.group(2))

    def _grid(self, css):
        m = re.search(r"background-size:(\d+)% (\d+)%", css)
        return int(m.group(1)) // 100, int(m.group(2)) // 100

    def test_una_sola_imagen_para_todas_las_caras(self):
        """The reason this module exists: N portraits, ONE request."""
        st = avatar_sprite.styles(list(self.colores))
        self.assertEqual(len(st), 5)
        urls = {self._url(c) for c in st.values()}
        self.assertEqual(len(urls), 1, f"debería haber UNA imagen, hay {urls}")

    def test_cada_medico_recorta_SU_cara(self):
        """The crop math, checked against the pixels — not against itself.

        Reproduces exactly what the browser does with `background-size` +
        `background-position` and asserts the colour that comes out is the
        colour that doctor's portrait went in as. An off-by-one here is
        invisible to a shape-only assertion and shows the wrong person's
        face on the page."""
        st = avatar_sprite.styles(list(self.colores))
        ruta = os.path.join(avatar_sprite.SPRITE_DIR,
                            os.path.basename(self._url(next(iter(st.values())))))
        atlas = Image.open(ruta).convert("RGB")
        for mid, esperado in self.colores.items():
            cols, rows = self._grid(st[mid])
            px, py = self._pos(st[mid])
            # background-position P% aligns the P% point of the image with the
            # P% point of the box, so the tile's left edge sits at
            # P/100 * (cols-1) tiles in. Same for rows.
            cx = round(px / 100 * (cols - 1)) if cols > 1 else 0
            cy = round(py / 100 * (rows - 1)) if rows > 1 else 0
            t = avatar_sprite.TILE
            centro = atlas.getpixel((cx * t + t // 2, cy * t + t // 2))
            for canal, (a, b) in enumerate(zip(centro, esperado)):
                self.assertLess(abs(a - b), 40,
                                f"{mid} cayó en la celda equivocada: "
                                f"({cx},{cy}) es {centro}, esperaba {esperado}")

    def test_la_url_cambia_cuando_cambia_una_foto(self):
        """Content-addressed: replacing a portrait has to move the URL, or the
        `immutable` cache header would pin the old face forever."""
        antes = self._url(avatar_sprite.styles(list(self.colores))["uno"])
        Image.new("RGB", (400, 600), (10, 10, 10)).save(
            os.path.join(self.fotos, "uno.png"))
        avatar_sprite.invalidate()
        despues = self._url(avatar_sprite.styles(list(self.colores))["uno"])
        self.assertNotEqual(antes, despues)

    def test_recopiar_las_mismas_fotos_NO_mueve_la_url(self):
        """The one that cost the live demo every face on it. demo_refresh.py
        re-copies the portraits nightly: same bytes, new mtimes. Keyed on the
        mtimes, that minted a new URL for an unchanged image every night and
        orphaned every page already open. Keyed on the atlas, nothing moves."""
        antes = self._url(avatar_sprite.styles(list(self.colores))["uno"])
        for mid, c in self.colores.items():  # idéntico contenido, mtime nuevo
            Image.new("RGB", (400, 600), c).save(
                os.path.join(self.fotos, f"{mid}.png"))
            os.utime(os.path.join(self.fotos, f"{mid}.png"), (10 ** 9, 10 ** 9))
        avatar_sprite.invalidate()
        self.assertEqual(antes,
                         self._url(avatar_sprite.styles(list(self.colores))["uno"]))

    def test_el_atlas_anterior_sobrevive_al_siguiente(self):
        """A page already open still points at the URL it was served. Deleting
        the old atlas the moment a new one exists is what 404'd the demo."""
        viejo = self._url(avatar_sprite.styles(list(self.colores))["uno"])
        Image.new("RGB", (400, 600), (10, 10, 10)).save(
            os.path.join(self.fotos, "uno.png"))
        avatar_sprite.invalidate()
        nuevo = self._url(avatar_sprite.styles(list(self.colores))["uno"])
        self.assertNotEqual(viejo, nuevo)
        self.assertTrue(os.path.isfile(os.path.join(
            avatar_sprite.SPRITE_DIR, os.path.basename(viejo))),
            "el atlas viejo se borró: las páginas abiertas se quedan sin caras")

    def test_el_atlas_responde_aunque_el_archivo_desaparezca(self):
        """Another container sharing the volume, a disk sweep, a wiped volume:
        whatever the page was just told to fetch has to be fetchable — the
        router's `atlas_bytes()` serves memory first (transport coverage:
        test_avatar_sprite_router.py)."""
        st = avatar_sprite.styles(list(self.colores))
        url = self._url(st["uno"])
        shutil.rmtree(avatar_sprite.SPRITE_DIR, ignore_errors=True)
        clave = os.path.basename(url)[: -len(".webp")]
        self.assertTrue(avatar_sprite.atlas_bytes(clave).startswith(b"RIFF"))

    def test_un_medico_sin_foto_no_entra(self):
        """No portrait = not in the atlas, so the caller still draws initials
        (or counts them in the card's "+N")."""
        st = avatar_sprite.styles(list(self.colores) + ["fantasma"])
        self.assertNotIn("fantasma", st)
        self.assertEqual(len(st), 5)

    def test_sin_fuentes_devuelve_vacio(self):
        self.assertEqual(avatar_sprite.styles([]), {})
        self.assertEqual(avatar_sprite.styles(["nadie", "tampoco"]), {})

    def test_catalogo_gigante_degrada(self):
        """Past MAX_TILES the sheet stops being a win — fall back, don't ship
        a multi-megabyte atlas to show six faces."""
        muchos = [f"m{i}" for i in range(avatar_sprite.MAX_TILES + 1)]
        self.assertEqual(avatar_sprite.styles(muchos), {})

    def test_una_foto_ilegible_solo_se_cuesta_a_si_misma(self):
        """A corrupt file costs THAT doctor his face, not everyone theirs."""
        with open(os.path.join(self.fotos, "roto.png"), "wb") as f:
            f.write(b"esto no es un png")
        st = avatar_sprite.styles(list(self.colores) + ["roto"])
        self.assertNotIn("roto", st)  # falls back to his own file / initials
        self.assertEqual(len(st), 5)  # everybody else keeps the atlas

    def test_el_atlas_ya_en_disco_recuerda_los_huecos(self):
        """On a restart the .webp is already there and _build never runs, so
        which slots came out empty has to survive next to it — otherwise the
        doctor with the broken photo gets a position pointing at filler."""
        with open(os.path.join(self.fotos, "roto.png"), "wb") as f:
            f.write(b"esto no es un png")
        ids = list(self.colores) + ["roto"]
        primera = avatar_sprite.styles(ids)
        _reset()  # como si el proceso arrancara de nuevo: atlas en disco
        segunda = avatar_sprite.styles(ids)
        self.assertEqual(primera, segunda)
        self.assertNotIn("roto", segunda)

    def _color_de(self, st, key):
        ruta = os.path.join(avatar_sprite.SPRITE_DIR, os.path.basename(self._url(st[key])))
        atlas = Image.open(ruta).convert("RGB")
        cols, rows = self._grid(st[key])
        px, py = self._pos(st[key])
        cx = round(px / 100 * (cols - 1)) if cols > 1 else 0
        cy = round(py / 100 * (rows - 1)) if rows > 1 else 0
        t = avatar_sprite.TILE
        return atlas.getpixel((cx * t + t // 2, cy * t + t // 2))

    def _cuadrantes(self):
        """400x400 photo, one flat colour per quadrant: whichever colour the
        tile's centre shows says which part of the photo the focus kept."""
        ruta = os.path.join(self.tmp, "centro.png")
        im = Image.new("RGB", (400, 400), (255, 0, 0))            # arriba izq.
        im.paste((0, 255, 0), (200, 0, 400, 200))                 # arriba der.
        im.paste((0, 0, 255), (0, 200, 200, 400))                 # abajo izq.
        im.paste((255, 255, 0), (200, 200, 400, 400))             # abajo der.
        im.save(ruta)
        return ruta

    def _cerca(self, a, b):
        self.assertTrue(all(abs(x - y) < 40 for x, y in zip(a, b)), f"{a} != {b}")

    def test_la_foto_de_un_centro_se_recorta_en_su_foco(self):
        """A center's photo goes into the SAME atlas as the doctors (one image
        for the whole home page) cut around its focus — the walk-in card's
        circles show a fragment of the building, not the whole block shrunk
        to 44 px (the product owner, 2026-10-27)."""
        ruta = self._cuadrantes()
        st = avatar_sprite.styles(["uno"], extra=[
            ("center:a", ruta, (0.75, 0.75, 2.0)),
            ("center:b", ruta, (0.25, 0.25, 2.0)),
        ])
        self.assertEqual(self._url(st["uno"]), self._url(st["center:a"]))
        self._cerca(self._color_de(st, "uno"), (255, 0, 0))
        self._cerca(self._color_de(st, "center:a"), (255, 255, 0))
        self._cerca(self._color_de(st, "center:b"), (255, 0, 0))

    def test_solo_centros_tambien_arma_el_atlas(self):
        ruta = self._cuadrantes()
        st = avatar_sprite.styles([], extra=[("center:a", ruta, (0.75, 0.25, 2.0))])
        self._cerca(self._color_de(st, "center:a"), (0, 255, 0))

    def test_un_centro_sin_foto_o_con_foco_roto_no_entra(self):
        ruta = self._cuadrantes()
        st = avatar_sprite.styles(["uno"], extra=[
            ("center:sin", os.path.join(self.tmp, "no-existe.png"), (0.5, 0.5, 1)),
            ("center:roto", ruta, ("x", 0.5, 1)),
            ("", ruta, (0.5, 0.5, 1)),
        ])
        self.assertIn("uno", st)
        self.assertNotIn("center:sin", st)
        self.assertNotIn("center:roto", st)

    def test_el_recorte_no_sale_de_la_foto(self):
        """A focus on the edge slides the square inward instead of cutting
        past the photo (PIL would pad that with black)."""
        self.assertEqual(avatar_sprite._focus_box(400, 300, 1.0, 1.0, 2.0),
                         (250, 150, 400, 300))
        self.assertEqual(avatar_sprite._focus_box(400, 300, 0.0, 0.0, 2.0),
                         (0, 0, 150, 150))
        # zoom 1 = the whole short side, centred on the focus horizontally.
        self.assertEqual(avatar_sprite._focus_box(400, 300, 0.5, 0.5, 1.0),
                         (50, 0, 350, 300))

    def test_el_atlas_se_sirve_y_404_lo_inexistente(self):
        """Domain half of the route's contract — the transport half (headers,
        shared terminal 404s) lives in test_avatar_sprite_router.py."""
        avatar_sprite.styles(list(self.colores))

        st = avatar_sprite.styles(list(self.colores))
        url = self._url(st["uno"])
        clave = os.path.basename(url)[: -len(".webp")]
        self.assertTrue(avatar_sprite.atlas_bytes(clave).startswith(b"RIFF"))

        with self.assertRaises(avatar_sprite.AtlasNotFound):
            avatar_sprite.atlas_bytes("0" * 16)

        # No path traversal through the hash: the regex only admits 16 hex
        # (the native router re-validates against this SAME regex).
        for malo in ("/avatar-sprite/../../etc/passwd.webp",
                     "/avatar-sprite/ABCDEF0123456789.webp",
                     "/avatar-sprite/short.webp"):
            self.assertIsNone(avatar_sprite._HASH_RE.match(malo), malo)


class TestSinPillow(unittest.TestCase):
    def test_sin_pillow_no_revienta(self):
        """The atlas is an optimisation. Without Pillow the site loses it and
        keeps working — it does NOT lose its faces."""
        real = sys.modules.pop("PIL", None)
        sys.modules["PIL"] = None  # forces ImportError inside _build
        try:
            _reset()
            self.assertEqual(avatar_sprite._build([("a", "file", "/no")]), None)
        finally:
            if real is not None:
                sys.modules["PIL"] = real
            else:
                sys.modules.pop("PIL", None)
            _reset()


if __name__ == "__main__":
    unittest.main()
