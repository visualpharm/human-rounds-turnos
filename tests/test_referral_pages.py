"""test_referral_pages.py — la orden escaneada de los DOS LADOS es UNA orden.

Subir el frente y el dorso de la misma orden (dos páginas de un PDF) es lo más
común que hace la gente: el frente trae el paciente y las prácticas, el dorso el
sello, la firma y la matrícula. Antes leíamos SÓLO la página 1 y esos datos se
perdían — la orden salía "dudosa" o sin firma, sin motivo real (responsable del producto,
2026-07-20: "si sube el archivo con las dos caras, tratalo como normal").

Acá se prueba la mecánica (cuántas páginas se convierten, en qué orden, y que
todas viajan en el mismo mensaje). Lo que el modelo entiende de ellas lo fija el
prompt y se verificó a mano con una orden real de dos caras.
"""

import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai import ai_referral  # noqa: E402

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


def _pdf_de(n_paginas):
    """PDF de `n_paginas` hojas distintas, o None si no hay PIL en el entorno."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    # El encoder de PDF de PIL guarda las páginas como JPEG; sin init() el
    # plugin no está registrado todavía y save() explota con KeyError: 'JPEG'
    # (sólo pasa cuando el proceso no abrió antes ningún JPEG).
    Image.init()
    hojas = []
    for i in range(n_paginas):
        img = Image.new("RGB", (1200, 1600), "white")
        ImageDraw.Draw(img).text((100, 100), f"HOJA {i + 1}", fill="black")
        hojas.append(img)
    path = os.path.join(tempfile.mkdtemp(prefix="orden-pgs-"), "orden.pdf")
    hojas[0].save(path, save_all=True, append_images=hojas[1:])
    return path


@unittest.skipUnless(shutil.which("pdftoppm"), "pdftoppm (poppler) no está instalado")
class TestPaginasDelPdf(unittest.TestCase):
    def setUp(self):
        self.pdf = _pdf_de(3)
        if not self.pdf:
            self.skipTest("PIL no disponible para armar el PDF de prueba")

    def tearDown(self):
        shutil.rmtree(os.path.dirname(self.pdf), ignore_errors=True)

    def test_convierte_todas_las_hojas_no_solo_la_primera(self):
        self.assertEqual(len(ai_referral._pdf_pages(self.pdf)), 3)

    def test_respeta_el_tope_de_paginas(self):
        self.assertEqual(len(ai_referral._pdf_pages(self.pdf, max_pages=2)), 2)

    def test_vienen_en_orden_numerico(self):
        """El frente tiene que ser la PRIMERA imagen que ve el modelo. Ordenar
        alfabéticamente pondría "-10" antes que "-2"."""
        pgs = ai_referral._pdf_pages(self.pdf)
        nums = [int(p.rsplit("-", 1)[1].split(".")[0]) for p in pgs]
        self.assertEqual(nums, sorted(nums))
        self.assertEqual(nums[0], 1)

    def test_las_paginas_pesan_poco(self):
        """JPEG y no PNG: en PNG tres hojas arman un POST de varios MB que se
        corta solo (BrokenPipe) además de costar más tokens."""
        for p in ai_referral._pdf_pages(self.pdf):
            self.assertLess(os.path.getsize(p), 1_000_000, p)


class TestContenidoMultiPagina(unittest.TestCase):
    def test_las_caras_extra_viajan_en_el_mismo_mensaje(self):
        """Una sola llamada con N imágenes — el modelo tiene que ver el frente y
        el dorso juntos para combinarlos en un JSON."""
        c = ai_referral._build_content("p", "data:image/jpeg;base64,AA", "image/jpeg",
                                    "o.jpg", ["data:image/jpeg;base64,BB",
                                              "data:image/jpeg;base64,CC"])
        self.assertEqual(sum(1 for x in c if x["type"] == "image_url"), 3)
        self.assertEqual(c[0]["type"], "text")

    def test_sin_extras_el_contenido_no_cambia(self):
        """Compatibilidad: ocr_dni.py llama a _build_content sin el parámetro."""
        self.assertEqual(
            ai_referral._build_content("p", "data:image/jpeg;base64,AA", "image/jpeg", "o.jpg"),
            ai_referral._build_content("p", "data:image/jpeg;base64,AA", "image/jpeg", "o.jpg", []),
        )


@unittest.skipUnless(HAVE_PIL, "Pillow no está instalado")
class TestNormalizacionGemini(unittest.TestCase):
    def _image(self, size, content_box=None):
        from PIL import Image, ImageDraw
        path = os.path.join(tempfile.mkdtemp(prefix="orden-img-"), "orden.png")
        img = Image.new("RGB", size, "white")
        if content_box:
            ImageDraw.Draw(img).rectangle(content_box, fill="black")
        else:
            ImageDraw.Draw(img).text((20, 20), "ORDEN", fill="black")
        img.save(path)
        self.addCleanup(shutil.rmtree, os.path.dirname(path), True)
        return path

    def test_769_se_ajusta_al_borde_del_mosaico(self):
        path = self._image((769, 769), (0, 0, 768, 768))
        _raw, mime, meta = ai_referral._image_for_model(path, "image/png")
        self.assertEqual(mime, "image/jpeg")
        self.assertLessEqual(meta["after"][0], 768)
        self.assertLessEqual(meta["after"][1], 768)
        self.assertEqual(meta["tiles_after"], 1)

    def test_orden_vertical_usa_como_maximo_dos_mosaicos(self):
        path = self._image((1800, 2600), (0, 0, 1799, 2599))
        _raw, _mime, meta = ai_referral._image_for_model(path, "image/png")
        self.assertLessEqual(meta["after"][0], 768)
        self.assertLessEqual(meta["after"][1], 1536)
        self.assertLessEqual(meta["tiles_after"], 2)

    def test_recorta_margen_blanco_sin_cortar_el_contenido(self):
        path = self._image((1600, 2200), (500, 700, 1100, 1500))
        _raw, _mime, meta = ai_referral._image_for_model(path, "image/png")
        self.assertLess(meta["after"][0], 800)
        self.assertLess(meta["after"][1], 1000)
        self.assertLess(meta["tiles_after"], meta["tiles_before"])


class TestPromptDosCaras(unittest.TestCase):
    def test_el_prompt_dice_que_dos_caras_son_una_orden(self):
        """Guarda contra que alguien recorte el prompt: sin esta instrucción el
        modelo trata el dorso como un documento aparte o marca es_orden=false.
        (El prompt es editable desde el panel y su canónico está en inglés —
        ver prompts.py.)"""
        p = ai_referral._prompt().lower()
        self.assertIn("both sides", p)
        self.assertIn("es_orden_medica=true", p)


if __name__ == "__main__":
    unittest.main()
