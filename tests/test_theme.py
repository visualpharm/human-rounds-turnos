"""test_theme.py — switchable visual theme (green "pinamar" vs Human Rounds paper+vermillion).

theme.py serves the active theme's id (default via env, or the one saved in
the DB) and injects it into <html data-theme="…"> by replacing the {{tema}}
placeholder.

The tests don't depend on DB state: the default logic is tested with
`_default()` (pure), and current()/inject() are forced by seeding the local
cache (the seam current() checks before hitting the DB).
"""

import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
from app.core import theme  # noqa: E402


class TestTema(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, TURNOS_THEME_DEFAULT=None)

    def tearDown(self):
        theme._cache["v"] = None
        theme._cache["t"] = 0.0

    # ---- default (pure, no DB) ----------------------------------------------
    def test_default_is_the_neutral_engine_theme(self):
        self.assertEqual(theme._default(), "humanrounds")

    def test_default_configurable_por_env(self):
        _env.set_env(self, TURNOS_THEME_DEFAULT="humanrounds")
        self.assertEqual(theme._default(), "humanrounds")

    def test_invalid_env_falls_back_to_the_neutral_engine_theme(self):
        _env.set_env(self, TURNOS_THEME_DEFAULT="no-existe")
        self.assertEqual(theme._default(), "humanrounds")

    # ---- save validates the id -----------------------------------------------
    def test_guardar_rechaza_id_invalido(self):
        with self.assertRaises(ValueError):
            theme.save("azul")
        with self.assertRaises(ValueError):
            theme.save("")

    # ---- current() caches (seam so it doesn't depend on the DB) --------------
    def test_actual_usa_cache_fresco(self):
        theme._cache["v"] = "humanrounds"
        theme._cache["t"] = time.time()
        self.assertEqual(theme.current(), "humanrounds")

    # ---- inject replaces the placeholder --------------------------------------
    def test_inject_reemplaza_placeholder_str(self):
        theme._cache["v"] = "humanrounds"
        theme._cache["t"] = time.time()
        html = '<html lang="es" data-theme="{{tema}}">'
        self.assertEqual(theme.inject(html), '<html lang="es" data-theme="humanrounds">')

    def test_inject_reemplaza_placeholder_bytes(self):
        theme._cache["v"] = "pinamar"
        theme._cache["t"] = time.time()
        out = theme.inject(b'<html data-theme="{{tema}}">')
        self.assertEqual(out, b'<html data-theme="pinamar">')

    def test_inject_sin_placeholder_no_toca(self):
        self.assertEqual(theme.inject("<html>hola</html>"), "<html>hola</html>")
        self.assertEqual(theme.inject(b"<html>x</html>"), b"<html>x</html>")

    # ---- theme catalog for the admin selector ---------------------------------
    def test_themes_catalogo(self):
        self.assertIn("pinamar", theme.THEMES)
        self.assertIn("humanrounds", theme.THEMES)
        for t in theme.THEMES.values():
            self.assertIn("id", t)
            self.assertIn("label", t)
            self.assertIn("desc", t)

    # ---- marca visible por tema (theme.brand_name) ----------------------------
    def test_brand_name_por_tema(self):
        self.assertEqual(theme.brand_name("pinamar"), "Turnos de salud pública en Pinamar")
        self.assertEqual(theme.brand_name("humanrounds"), "Human Rounds")

    def test_la_marca_sola_es_el_titulo_de_portada_solo_en_pinamar(self):
        self.assertTrue(theme.brand_is_page_title("pinamar"))
        self.assertFalse(theme.brand_is_page_title("humanrounds"))
        self.assertFalse(theme.brand_is_page_title("no-existe"))

    def test_brand_name_usa_el_tema_activo_por_default(self):
        theme._cache["v"] = "humanrounds"
        theme._cache["t"] = time.time()
        self.assertEqual(theme.brand_name(), "Human Rounds")

    def test_unknown_brand_id_falls_back_to_the_neutral_engine_brand(self):
        self.assertEqual(theme.brand_name("no-existe"), "Human Rounds")

    def test_ai_request_titles_use_the_active_deployment_brand(self):
        paths = (
            "app/admin_panel/admin_content.py",
            "app/ai/ai_referral.py",
            "app/ai/chart_summary.py",
            "app/ai/lab_analysis.py",
            "app/modules/chat/chat_agent.py",
            "app/modules/intake/analysis.py",
            "app/modules/intake/consult_compose.py",
            "app/modules/intake/health_profile.py",
            "app/modules/intake/interview_agent.py",
        )
        for relative_path in paths:
            with self.subTest(path=relative_path):
                with open(os.path.join(ROOT, relative_path), encoding="utf-8") as source:
                    text = source.read()
                self.assertIn('"X-Title":', text)
                self.assertIn("theme.brand_name()", text)
                self.assertNotIn('"X-Title": "Pinamar Turnos', text)

    def test_humanrounds_usa_las_tipografias_de_la_marca(self):
        # Manrope + IBM Plex Mono son las que pide humanrounds.org: el tema
        # "humanrounds" ES esa marca, no una aproximación (antes acá había
        # Inter + JetBrains Mono, que el sitio público ya no usa).
        self.assertEqual(
            theme._FONT_FAMILIES["humanrounds"],
            ("Manrope:wght@400;500;600;700;800", "IBM+Plex+Mono:wght@400;500"))

    # ---- favicon por tema --------------------------------------------------
    # Las dos instalaciones (pinamar.humanrounds.org y demo.humanrounds.org)
    # salen del MISMO build, así que el ícono de la pestaña no se puede elegir
    # en el deploy: lo resuelve el tema activo, server-side.
    def test_favicon_pinamar_es_el_svg_del_municipio(self):
        self.assertIn("/static/favicon.svg", theme.favicon("pinamar"))

    def test_favicon_humanrounds_es_la_marca_publica(self):
        fav = theme.favicon("humanrounds")
        self.assertIn("/static/human-rounds-mark-32.png", fav)
        self.assertIn("/static/human-rounds-mark-180.png", fav)
        self.assertIn('rel="apple-touch-icon"', fav)
        self.assertNotIn("favicon.svg", fav)

    # La marca es una silueta sin fondo: negra desaparece sobre una barra
    # oscura y blanca sobre una clara, así que se sirven las dos.
    def test_favicon_humanrounds_tiene_variante_para_barra_oscura(self):
        fav = theme.favicon("humanrounds")
        self.assertIn("/static/human-rounds-mark-32-dark.png", fav)
        self.assertIn("(prefers-color-scheme: dark)", fav)
        self.assertIn("(prefers-color-scheme: light)", fav)

    def test_favicon_humanrounds_deja_la_negra_ultima(self):
        # El navegador se queda con el último <link rel="icon"> que aplique, y
        # uno que ignore `media` aplica los dos: el default seguro es la negra.
        fav = theme.favicon("humanrounds")
        self.assertLess(fav.index("human-rounds-mark-32-dark.png"),
                        fav.index("human-rounds-mark-32.png?"))

    def test_favicon_id_desconocido_no_deja_la_pagina_sin_icono(self):
        self.assertEqual(theme.favicon("no-existe"), theme.favicon("humanrounds"))

    def test_inject_reemplaza_favicon_en_str_y_en_bytes(self):
        theme._cache["v"] = "humanrounds"
        theme._cache["t"] = time.time()
        esperado = theme.favicon("humanrounds")
        self.assertEqual(theme.inject("<head>{{favicon}}"), "<head>" + esperado)
        self.assertEqual(theme.inject(b"<head>{{favicon}}"),
                         b"<head>" + esperado.encode("utf-8"))

    def test_ninguna_pagina_quedo_con_el_favicon_escrito_a_mano(self):
        """El <link rel=icon> a mano volvía a poner el ícono de Pinamar en la
        demo pública. Toda página que ya resuelve tema tiene que pasar por
        {{favicon}} / theme.favicon() — si alguien vuelve a pegar el link
        literal, esto lo levanta."""
        import glob
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        literal = 'href="/static/favicon.svg"'
        culpables = []
        for path in (glob.glob(os.path.join(root, "*.html"))
                     + glob.glob(os.path.join(root, "*.py"))):
            with open(path, encoding="utf-8") as f:
                texto = f.read()
            if literal not in texto:
                continue
            # theme.py ES el lugar donde vive la tabla.
            base = os.path.basename(path)
            if base == "theme.py":
                continue
            culpables.append(base)
        self.assertEqual(culpables, [], "favicon escrito a mano en: %s" % culpables)

    def test_ninguna_pagina_pide_tipografias_a_mano(self):
        """Mismo error que el favicon, un renglón más abajo: admin.html tenía
        las CUATRO familias de los dos temas escritas a mano, así que al
        cambiarle la tipografía a "humanrounds" el panel se quedó sin ella y
        cayó a system-ui. Las familias las elige theme.fonts() vía
        {{fuentes}}; nadie las vuelve a listar en una página."""
        import glob
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        culpables = []
        for path in (glob.glob(os.path.join(root, "*.html"))
                     + glob.glob(os.path.join(root, "*.py"))):
            base = os.path.basename(path)
            # theme.py ES quien arma el href.
            if base == "theme.py":
                continue
            with open(path, encoding="utf-8") as f:
                texto = f.read()
            # Material Symbols es un set de íconos, no la tipografía del tema.
            for familia in ("family=Inter", "family=Ubuntu", "family=Manrope",
                            "family=JetBrains", "family=IBM+Plex"):
                if familia in texto:
                    culpables.append("%s (%s)" % (base, familia))
        self.assertEqual(culpables, [],
                         "tipografía pedida a mano en: %s" % culpables)

    def test_las_copias_del_accent_siguen_al_token(self):
        """--accent está escrito a mano en TRES lugares fuera del CSS, cada uno
        con su motivo (el selector del panel no puede leer tokens del tema que
        NO está activo; el mail no tiene CSS; branding.py muestra el color de
        arranque). Cuando el tema cambió de color, dos de esos tres se
        quedaron con el terracota viejo. Esto los ata al token."""
        import os
        import re
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        def _leer(nombre):
            with open(os.path.join(root, nombre), encoding="utf-8") as f:
                return f.read()

        # Tier 5 slice 1: los bloques de tema viven en tokens.css (extraídos
        # verbatim de human-rounds.css, que ahora sólo los @import-ea).
        css = _leer("static/root/tokens.css")
        canon = {}
        for tema, patron in (("pinamar", r':root, :root\[data-theme="pinamar"\]\{(.*?)\n\}'),
                             ("humanrounds", r':root\[data-theme="humanrounds"\]\{(.*?)\n\}')):
            bloque = re.search(patron, css, re.S)
            self.assertIsNotNone(bloque, "bloque de tema %r no encontrado" % tema)
            canon[tema] = re.search(r"--accent:(#[0-9a-fA-F]{6})",
                                    bloque.group(1)).group(1).lower()

        from app.patient_web import branding
        from app.notifications import send as notifications
        for tema, esperado in canon.items():
            self.assertEqual(branding._ACCENT_BY_THEME[tema].lower(), esperado,
                             "branding.py quedó con otro --accent para %r" % tema)
            # The native Appearance owner renders both theme swatches, including
            # the inactive theme. Check its own rule, not a matching color
            # elsewhere in the module (and never restore the retired classic).
            source = _leer("static/platform/admin-appearance.mjs").lower()
            swatch = re.search(r"\.cfg-sw--" + re.escape(tema) + r"\{([^}]*)\}", source)
            self.assertIsNotNone(swatch, "missing native swatch for %r" % tema)
            self.assertIn(esperado, swatch.group(1),
                          "native Appearance swatch has an old accent for %r" % tema)
        self.assertIn(canon["humanrounds"], _leer("app/notifications/send.py").lower(),
                      "el accent del mail quedó viejo para 'humanrounds'")
        self.assertTrue(hasattr(notifications, "_accent"))

    # ---- per-theme avatar tokens -----------------------------------------------
    # The initials circles (.lst-av--N, .mp-av--N, .avx--N, .cnt-foto-av--N)
    # and the My-account rail avatar read var(--avN-*) / var(--av-hero-*) from
    # human-rounds.css. If a theme block loses these tokens, that theme inherits
    # the other one's palette — this test requires the full set in EACH theme
    # block.
    def test_avatar_tokens_en_ambos_temas(self):
        import os, re
        # Tier 5 slice 1: los bloques de tema viven en tokens.css.
        css_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "static", "root", "tokens.css")
        with open(css_path, encoding="utf-8") as f:
            css = f.read()
        bloques = {
            "pinamar": re.search(r':root, :root\[data-theme="pinamar"\]\{(.*?)\}', css, re.S),
            "humanrounds": re.search(r':root\[data-theme="humanrounds"\]\{(.*?)\}', css, re.S),
        }
        tokens = (["--av%d-bg" % i for i in range(6)] + ["--av%d-fg" % i for i in range(6)]
                  + ["--av-ph", "--av-hero-a", "--av-hero-b", "--av-hero-sh"])
        for nombre, m in bloques.items():
            self.assertIsNotNone(m, "bloque de tema %r no encontrado" % nombre)
            for tok in tokens:
                self.assertIn(tok + ":", m.group(1),
                              "falta %s en el bloque del tema %r" % (tok, nombre))
        # y las dos paletas son de verdad distintas (no un copy-paste)
        self.assertNotEqual(
            re.search(r"--av0-bg:(#[0-9a-f]+)", bloques["pinamar"].group(1)).group(1),
            re.search(r"--av0-bg:(#[0-9a-f]+)", bloques["humanrounds"].group(1)).group(1))

    # ---- contraste AA en LOS DOS temas -----------------------------------------
    # El repaso de contraste del 2026-07-28 se le hizo sólo a "pinamar"; el
    # bloque de "humanrounds" quedó con --text-3 en 2,9:1 y --text-4 en 2,2:1,
    # ilegibles, y nadie se enteró hasta que el tema se volvió el de la demo
    # pública (2026-08-01). Esto vale para los dos: cada color de texto tiene
    # que pasar 4,5:1 contra TODAS las superficies donde se lo usa, así el
    # próximo tema no puede repetirlo.
    def test_texto_pasa_AA_en_los_dos_temas(self):
        import os
        import re

        def _luminancia(hex_):
            h = hex_.lstrip("#")
            canales = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
            lineal = [(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
                      for c in canales]
            return 0.2126 * lineal[0] + 0.7152 * lineal[1] + 0.0722 * lineal[2]

        def _contraste(a, b):
            la, lb = _luminancia(a), _luminancia(b)
            hi, lo = max(la, lb), min(la, lb)
            return (hi + 0.05) / (lo + 0.05)

        # Tier 5 slice 1: los bloques de tema viven en tokens.css.
        css_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "static", "root", "tokens.css")
        with open(css_path, encoding="utf-8") as f:
            css = f.read()
        bloques = {
            "pinamar": re.search(r':root, :root\[data-theme="pinamar"\]\{(.*?)\n\}', css, re.S),
            "humanrounds": re.search(r':root\[data-theme="humanrounds"\]\{(.*?)\n\}', css, re.S),
        }
        # --panel vive en el :root compartido, no en el bloque de cada tema.
        PANEL = "#ffffff"
        # Colores de texto -> superficies sobre las que se pintan.
        SOBRE = {
            "--text": ("--bg", "--bg-2", "--accent-soft"),
            "--text-2": ("--bg", "--bg-2", "--accent-soft"),
            "--text-3": ("--bg", "--bg-2", "--accent-soft"),
            "--text-4": ("--bg", "--bg-2", "--accent-soft"),
            # --accent es link (texto) y también relleno de botón con texto
            # blanco encima: las dos direcciones tienen que pasar.
            "--accent": ("--bg", "--bg-2", "--accent-soft"),
            "--warn": ("--bg", "--bg-2", "--warn-soft"),
            "--done": ("--bg", "--bg-2", "--state-done-bg"),
            # Familia de chips/triage recoloreada 2026-08-05 (--info es el 4º
            # acento de estado, "pizarra"; naranja/amarillo son los dos
            # niveles de triage que no pueden compartir token con --warn/
            # --danger sin perder la distinción entre niveles).
            "--info": ("--bg", "--bg-2", "--info-soft"),
            "--triage-naranja": ("--bg", "--bg-2", "--triage-naranja-soft"),
            "--triage-amarillo": ("--bg", "--bg-2", "--triage-amarillo-soft"),
        }
        for tema, m in bloques.items():
            self.assertIsNotNone(m, "bloque de tema %r no encontrado" % tema)
            cuerpo = m.group(1)
            tok = dict(re.findall(r"(--[a-z0-9-]+):(#[0-9a-fA-F]{6})", cuerpo))
            # Escala de grises v2 de "humanrounds" (responsable del producto, 2026-09-16, ver
            # tokens.css): --text-2/3/4 son TINTA con opacidad
            # (color-mix(... N%, transparent)), no un hex. Su color real
            # depende del fondo, así que se compone sobre cada superficie.
            # Excepción CONSCIENTE del responsable del producto al piso AA (50% de contraste, "NOT
            # MORE"): en vez de exigir 4,5:1 se fija un piso apenas debajo de
            # lo medido, para que aclararla otra vez haga fallar el test.
            tinta = {k: int(v) / 100 for k, v in re.findall(
                r"(--text-[234]):color-mix\(in srgb,var\(--text\) (\d+)%,transparent\)", cuerpo)}
            PISO_TINTA = {"--text-2": 3.2, "--text-3": 2.7, "--text-4": 2.3}
            if tema == "humanrounds":
                self.assertEqual(set(tinta), set(PISO_TINTA),
                                 "humanrounds: la escala gris dejó de ser tinta con opacidad")

            def _rgb(h):
                return [int(h.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]

            def _color_sobre(color, fondo):
                """Color efectivo de `color` pintado sobre `fondo`."""
                if color not in tinta:
                    return tok[color]
                a = tinta[color]
                return "#%02x%02x%02x" % tuple(
                    round(a * t + (1 - a) * f) for t, f in zip(_rgb(tok["--text"]), _rgb(fondo)))

            for color, superficies in SOBRE.items():
                self.assertTrue(color in tok or color in tinta, "%s: falta %s" % (tema, color))
                piso = PISO_TINTA[color] if color in tinta else 4.5
                for sup in superficies + ("__panel",):
                    fondo = PANEL if sup == "__panel" else tok.get(sup)
                    if sup == "__panel" and tema == "humanrounds":
                        fondo = tok["--panel"]
                    self.assertIsNotNone(fondo, "%s: falta %s" % (tema, sup))
                    efectivo = _color_sobre(color, fondo)
                    ratio = _contraste(efectivo, fondo)
                    self.assertGreaterEqual(
                        round(ratio, 2), piso,
                        "%s: %s (%s) sobre %s (%s) da %.2f:1, reprueba el piso de %.1f:1"
                        % (tema, color, efectivo, sup, fondo, ratio, piso))
            if tema == "humanrounds":
                # "Gris de verdad" (responsable del producto, 2026-09-16, 36a97c06): el gris
                # secundario tiene que VERSE gris, no casi tinta. El viejo
                # #55504a daba 7,9:1 sobre blanco y se leía negro; toda la
                # escala secundaria queda por debajo de 6,5:1 sobre panel,
                # además del piso de arriba.
                for color in ("--text-2", "--text-3", "--text-4"):
                    efectivo = _color_sobre(color, tok["--panel"])
                    ratio = _contraste(efectivo, tok["--panel"])
                    self.assertLess(
                        round(ratio, 2), 6.5,
                        "%s: %s (%s) da %.2f:1 sobre panel, se lee casi negro"
                        % (tema, color, efectivo, ratio))
            # y el texto blanco de los botones sobre el acento
            ratio = _contraste("#ffffff", tok["--accent"])
            self.assertGreaterEqual(
                round(ratio, 2), 4.5,
                "%s: blanco sobre --accent (%s) da %.2f:1" % (tema, tok["--accent"], ratio))


if __name__ == "__main__":
    unittest.main()
