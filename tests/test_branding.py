"""test_branding.py — color y logo del municipio, editables sin tocar código
(reunión hospital 2026-07-24: "un permiso tipo custom para editar
colores/logo sin modificar backend").

Run: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_branding.py"
(WATCH OUT: `python3 -m unittest test.test_x` does NOT resolve — see CONTRIBUTING.md.)

No database needed: the color is saved in `catalogo` but here the pure
pieces are tested (derivation, CSS, image sniffing, role gate) with the logo
directory redirected to a temp dir.
"""

import io
import os
import sys
import shutil
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
from app.patient_web import branding      # noqa: E402
from app.auth import admin_auth   # noqa: E402

from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.auth import turnstile          # noqa: E402
from app.core import config  # noqa: E402
turnstile.ENV_PATH = ""   # no leer el .env real
config.ENV_PATH = ""  # idem: no leer el .env real del checkout de dev


class ColorTestCase(unittest.TestCase):

    def test_acepta_el_hex_como_lo_escriba_la_persona(self):
        for entrada in ("#0E8B50", "0e8b50", "  #0e8b50  ", "0E8B50"):
            self.assertEqual(branding.normalize_color(entrada), "#0e8b50", entrada)

    def test_rechaza_lo_que_no_es_un_color(self):
        for entrada in ("rojo", "#abc", "#12345", "#1234567", "", None, "javascript:x"):
            self.assertIsNone(branding.normalize_color(entrada), entrada)

    def test_los_tres_tokens_se_derivan_del_acento(self):
        # Uno solo se elige; hover/soft/bd salen de él. Pedir cuatro colores
        # garantiza que alguien elija cuatro que no combinan.
        d = branding._derived_tokens("#0e8b50")
        self.assertEqual(d["accent"], "#0e8b50")
        # hover más oscuro, soft y bd más claros que el acento
        self.assertLess(sum(branding._rgb(d["accent_hover"])), sum(branding._rgb(d["accent"])))
        self.assertGreater(sum(branding._rgb(d["accent_soft"])), sum(branding._rgb(d["accent"])))
        self.assertGreater(sum(branding._rgb(d["accent_soft"])), sum(branding._rgb(d["accent_bd"])))
        for k in ("accent_hover", "accent_soft", "accent_bd"):
            self.assertRegex(d[k], r"^#[0-9a-f]{6}$")


class EstiloTestCase(unittest.TestCase):
    """`style()` es lo que termina en el <head> de cada página."""

    def setUp(self):
        self._dir = branding.BRANDING_DIR
        branding.BRANDING_DIR = tempfile.mkdtemp(prefix="marca-test-")
        branding.invalidate()
        self._cache = dict(branding._cache)

    def tearDown(self):
        shutil.rmtree(branding.BRANDING_DIR, ignore_errors=True)
        branding.BRANDING_DIR = self._dir
        branding.invalidate()

    def _fijar(self, color=None, logo=False):
        """Evita la base: se escribe el cache directamente."""
        branding._cache["v"] = {"color": color, "logo": logo}
        branding._cache["t"] = float("inf")

    def test_sin_nada_configurado_no_hay_style(self):
        self._fijar()
        self.assertEqual(branding.style(), "")

    def test_el_color_pisa_al_del_tema(self):
        # human-rounds.css define los tokens bajo :root[data-theme=...] y este
        # <style> va ANTES del link: sin !important perdería.
        self._fijar(color="#1d4ed8")
        css = branding.style()
        self.assertIn("--accent:#1d4ed8!important", css)
        self.assertIn("--accent-hover:", css)
        self.assertIn("--accent-soft:", css)
        self.assertIn("--accent-bd:", css)

    def test_el_logo_propio_le_gana_al_emblema_del_tema(self):
        # Los temas pisan sus marcas por CSS; si el municipio sube su logo,
        # tiene que ganar en cada superficie, incluido el sidebar directo.
        self._fijar(logo=True)
        css = branding.style()
        self.assertIn(".stznav-mark", css)
        self.assertIn(".adm5-brand::before", css)
        self.assertIn("url(/logo.png)!important", css)

    def test_color_y_logo_conviven_en_un_solo_style(self):
        self._fijar(color="#1d4ed8", logo=True)
        css = branding.style()
        self.assertEqual(css.count("<style"), 1)
        self.assertIn("--accent:#1d4ed8", css)
        self.assertIn("url(/logo.png)", css)


class LogoTestCase(unittest.TestCase):

    def setUp(self):
        self._dir = branding.BRANDING_DIR
        branding.BRANDING_DIR = tempfile.mkdtemp(prefix="marca-logo-test-")

    def tearDown(self):
        shutil.rmtree(branding.BRANDING_DIR, ignore_errors=True)
        branding.BRANDING_DIR = self._dir
        branding.invalidate()

    def test_el_formato_sale_de_los_magic_bytes_no_del_nombre(self):
        self.assertEqual(branding._sniff(b"\x89PNG\r\n\x1a\n rest")[0], "png")
        self.assertEqual(branding._sniff(b"\xff\xd8\xff rest")[0], "jpg")
        self.assertEqual(branding._sniff(b"RIFF1234WEBPxx")[0], "webp")
        self.assertEqual(branding._sniff(b'<svg xmlns="...">')[0], "svg")
        self.assertEqual(branding._sniff(b'  <?xml version="1.0"?><svg/>')[0], "svg")

    def test_un_svg_con_codigo_no_entra(self):
        """Un SVG es XML y puede ejecutar código. Servido desde nuestro dominio,
        abrir /logo.png lo correría como script de primera parte — y el rol
        'marca' es de bajo privilegio a propósito, así que sería una escalada
        a admin. Se rechaza en la subida (y `_serve_logo` además manda un CSP
        que no lo deja ejecutar nada)."""
        casos = [
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            b'<svg onload="alert(1)"></svg>',
            b'<svg><a href="javascript:alert(1)"><text>x</text></a></svg>',
            b'<svg><foreignObject><body xmlns="http://www.w3.org/1999/xhtml">'
            b"<script>alert(1)</script></body></foreignObject></svg>",
            b'<?xml version="1.0"?><svg><SCRIPT>alert(1)</SCRIPT></svg>',  # mayúsculas
            b'<svg><image onerror="alert(1)" href="x"/></svg>',
        ]
        for svg in casos:
            self.assertIsNone(branding._sniff(svg), svg[:40])
            with self.assertRaises(ValueError, msg=svg[:40]):
                branding.save_logo(svg)

    def test_un_svg_limpio_si_entra(self):
        # No se rompe el caso legítimo: un logo vectorial normal se acepta.
        svg = (b'<?xml version="1.0" encoding="UTF-8"?>'
               b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
               b'<circle cx="32" cy="32" r="30" fill="#0e8b50"/>'
               b'<path d="M10 32h44" stroke="#fff" stroke-width="4"/></svg>')
        self.assertEqual(branding._sniff(svg)[0], "svg")
        self.assertEqual(branding.save_logo(svg), "svg")

    def test_un_archivo_que_no_es_imagen_se_rechaza(self):
        self.assertIsNone(branding._sniff(b"%PDF-1.4"))
        self.assertIsNone(branding._sniff(b"<html><script>alert(1)</script>"))
        with self.assertRaises(ValueError):
            branding.save_logo(b"%PDF-1.4 no soy un logo")

    def test_subir_reemplaza_no_acumula(self):
        branding.save_logo(b"\x89PNG\r\n\x1a\n uno")
        branding.save_logo(b'<svg xmlns="x"/>')
        quedan = sorted(os.listdir(branding.BRANDING_DIR))
        self.assertEqual(quedan, ["logo.svg"])  # el png anterior no queda colgado

    def test_quitar_vuelve_al_logo_del_sitio(self):
        branding.save_logo(b"\x89PNG\r\n\x1a\n uno")
        self.assertIsNotNone(branding._logo_path())
        branding.remove_logo()
        self.assertIsNone(branding._logo_path())


class FakeHandler:
    def __init__(self, path, method="GET", cookie=None):
        self.path = path
        self.command = method
        self.headers = {"Cookie": "st_admin=" + cookie} if cookie else {}
        self.json = None
        self.status = None
        self.wfile = io.BytesIO()
        self.sent_headers = {}

    def _send_json(self, obj, status=200):
        self.json, self.status = obj, status

    def _read_json(self):
        return {}

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.sent_headers[k] = v

    def end_headers(self):
        pass


class PermisosTestCase(unittest.TestCase):
    """Branding is part of Administrator; the old custom role is retired.
    Gate coverage runs over the native app (the legacy `branding.handle()`
    transport was retired 2026-08-30) — the router's
    `Depends(require_admin(solo_admin=True, permitir_marca=True))` is the
    same gate the old dispatch called."""

    def setUp(self):
        # Its own signing key: minting the session cookies must not depend on
        # a sibling module having left one in the environment.
        _env.set_env(self, {"TURNOS_ADMIN_SECRET": "test-only-branding-signing-key"})
        config._reset_cache_para_tests()
        self.addCleanup(config._reset_cache_para_tests)
        self.client = TestClient(app_module.app)
        self.addCleanup(self.client.close)

    def _h(self, rol, path="/api/admin/branding", method="GET"):
        return FakeHandler(path, method, cookie=admin_auth._mint_token("u_" + rol, rol))

    def _cookie(self, rol):
        return {"st_admin": admin_auth._mint_token("u_" + rol, rol)}

    def test_el_rol_marca_ya_no_es_una_sesion_valida(self):
        r = self.client.get("/api/admin/branding", cookies=self._cookie("marca"))
        self.assertEqual(r.status_code, 401)

    def test_el_admin_tambien(self):
        r = self.client.get("/api/admin/branding", cookies=self._cookie("admin"))
        self.assertEqual(r.status_code, 200)

    def test_el_rol_marca_no_entra_a_ningun_otro_lado(self):
        for kwargs in ({}, {"solo_admin": True}, {"permitir_medico": True},
                       {"permitir_admisionista": True}, {"permitir_reportes": True}):
            h = self._h("marca")
            gate = admin_auth.evaluate_admin_gate(
                admin_auth._parse_cookie_token(h.headers.get("Cookie")),
                h.command,
                h.path,
                **kwargs,
            )
            self.assertIsNone(gate.identity, kwargs)
            self.assertEqual(gate.status, 401, kwargs)

    def test_los_demas_roles_no_cambian_la_identidad_visual(self):
        for rol in ("medico", "admisionista", "reportes"):
            r = self.client.get("/api/admin/branding", cookies=self._cookie(rol))
            self.assertEqual(r.status_code, 403, rol)

    def test_sin_sesion_401(self):
        r = self.client.get("/api/admin/branding")
        self.assertEqual(r.status_code, 401)

    def test_el_logo_se_sirve_sin_permiso_para_ejecutar_nada(self):
        """Segunda defensa: aunque un SVG con script llegue al disco por otra
        vía, la respuesta no le deja correr código en nuestro dominio."""
        d = branding.BRANDING_DIR
        branding.BRANDING_DIR = tempfile.mkdtemp(prefix="marca-csp-")
        try:
            with open(os.path.join(branding.BRANDING_DIR, "logo.svg"), "wb") as f:
                f.write(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
            branding.invalidate()
            r = self.client.get("/logo.png")
            self.assertEqual(r.status_code, 200)
            csp = r.headers.get("Content-Security-Policy", "")
            self.assertIn("sandbox", csp)
            self.assertIn("default-src 'none'", csp)
            self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")
        finally:
            shutil.rmtree(branding.BRANDING_DIR, ignore_errors=True)
            branding.BRANDING_DIR = d
            branding.invalidate()

    def test_logo_sin_subir_lo_sirve_el_estatico(self):
        # Sin subida, la ruta nativa cae al logo.png del repo (el fallback
        # que antes resolvía la cadena estática de server.py) — no un 404.
        d = branding.BRANDING_DIR
        branding.BRANDING_DIR = tempfile.mkdtemp(prefix="marca-vacio-")
        try:
            branding.invalidate()
            r = self.client.get("/logo.png")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.headers.get("Content-Type"), "image/png")
        finally:
            shutil.rmtree(branding.BRANDING_DIR, ignore_errors=True)
            branding.BRANDING_DIR = d
            branding.invalidate()


if __name__ == "__main__":
    unittest.main()
