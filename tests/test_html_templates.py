"""Pages whose markup moved from a Python string to a Jinja template must keep their bytes.

Each case holds the string the Python code built before the move (copied here, frozen) and compares it
with what the template renders now. Inputs are the real ones the callers pass; a value with markup
characters is escaped now (it was not before) and is pinned separately.
"""
import os
import sys
import unittest
from unittest.mock import patch

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1", DATABASE_URL="")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.auth import forbidden_screen  # noqa: E402
from app.core import jinja_env  # noqa: E402
from app.core import static_fingerprint, theme  # noqa: E402
from app.cuentas import auth_renaper  # noqa: E402
from markupsafe import Markup  # noqa: E402
from app.modules.turnos import appointment_confirm, appointment_offer  # noqa: E402
from app.patient_web import error_pages  # noqa: E402

_RENAPER_ERROR_BEFORE = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>%s</title>
<style>body{font-family:system-ui,sans-serif;max-width:480px;margin:80px auto;padding:0 20px;color:#1f1f1f}
h1{font-size:22px}p{color:#5a625b;line-height:1.5}
a{color:#567b42;text-decoration:none}a:hover{color:#6c9553}</style></head>
<body><h1>%s</h1><p>%s</p>
<p><a href="/account?mode=login">Volver a ingresar</a></p></body></html>"""

# Every (title, detail) pair auth_renaper.py / auth_renaper_router.py can pass.
_RENAPER_ERRORS = [
    ("Demasiados intentos", "Probá de nuevo en un rato."),
    ("No pudimos completar el ingreso",
     "El enlace venció o no es válido. Empezá de nuevo desde Ingresar."),
    ("Ingreso cancelado", "No autorizaste el ingreso. Podés intentarlo de nuevo cuando quieras."),
    ("No pudimos completar el ingreso",
     "Faltó el código de autorización. Empezá de nuevo desde Ingresar."),
    ("No pudimos hablar con Mi Argentina",
     "El servicio nacional no respondió. Probá de nuevo en unos minutos o ingresá con tu contraseña."),
    ("No pudimos confirmar tu identidad",
     "La respuesta del servicio nacional no trajo tu DNI. "
     "Podés crear una cuenta con contraseña mientras tanto."),
    ("Base de datos no disponible", "Probá de nuevo en unos minutos."),
]


class TestRenaperErrorPage(unittest.TestCase):
    def test_every_real_message_renders_the_same_bytes_as_before(self):
        for titulo, detalle in _RENAPER_ERRORS:
            expected = (_RENAPER_ERROR_BEFORE % (titulo, titulo, detalle)).encode("utf-8")
            self.assertEqual(auth_renaper._pagina_error_body(titulo, detalle), expected, titulo)

    def test_markup_in_a_message_is_escaped(self):
        body = auth_renaper._pagina_error_body("<b>x</b>", 'a "q" & <i>').decode("utf-8")
        self.assertNotIn("<b>x</b>", body)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", body)
        self.assertIn("a &#34;q&#34; &amp; &lt;i&gt;", body)


def _text(page):
    return page.decode("utf-8") if isinstance(page, bytes) else page


_NOT_FOUND_BEFORE = (
    '<section class="sec">'
    '<h1 class="h1">No encontramos esa página</h1>'
    '<p class="lede">Puede que el link esté roto o que la página se haya movido.</p>'
    '<p class="lede"><a class="stz-link" href="/">Volver al inicio</a>'
    # The same destinations and words as the header (demo review 2026-11-15:
    # «Ver médicos» pointed at the old /medicos, one 301 away).
    ' &middot; <a class="stz-link" href="/doctors">Médicos</a>'
    ' &middot; <a class="stz-link" href="/d/diagnostico">Estudios</a>'
    ' &middot; <a class="stz-link" href="/centers">Centros</a></p>'
    "</section>"
)


def _expired_before(alternatives):
    return f"""
  <h1 class="h1">Este link ya no sirve</h1>
  <p class="lede">Puede que el turno ya haya pasado, o que el link sea de un correo viejo.</p>
  {alternatives}
  <p class="sd-ref"><a href="/my-requests">Todos mis turnos</a></p>
"""


def _cancelled_before(alternatives):
    return f"""
  <h1 class="h1">Ese turno está cancelado</h1>
  <p class="lede">No hace falta que confirmes nada.</p>
  {alternatives}
  <p class="sd-ref"><a href="/my-requests">Todos mis turnos</a></p>
"""


def _unavailable_before(alternatives):
    return f"""
  <h1 class="h1">Este turno ya no está disponible</h1>
  <p class="lede">Puede que ya lo hayan tomado, o que el tiempo para reservarlo haya pasado.</p>
  {alternatives}
  <p class="sd-ref"><a href="/my-requests">Mis turnos</a></p>
"""


_DATABASE_UNAVAILABLE_BEFORE = """
  <h1 class="h1">No pudimos cargar este turno</h1>
  <p class="lede">El sistema está temporalmente fuera de servicio. Probá de nuevo en unos minutos.</p>
  <p class="sd-ref"><a href="/">Servicios y centros</a></p>
"""

_ALTERNATIVES = [
    '<p class="lede"><a href="/">Ver todos los servicios y centros</a></p>',
    '<p class="lede">Todavía podés pedir un turno de <a href="/service/x">Nombre &amp; Co</a>.</p>',
]


class TestDeadEndPageBodies(unittest.TestCase):
    """The patient's dead-end pages: not found, an expired/cancelled/taken turno, the database down."""

    def test_not_found_body_keeps_its_markup(self):
        self.assertEqual(jinja_env.render("fragments/not-found.html"), _NOT_FOUND_BEFORE)
        self.assertIn(_NOT_FOUND_BEFORE, _text(error_pages.not_found_body()))

    def test_not_found_footer_follows_the_message(self):
        """No screen-high empty band between the three lines and the footer
        (demo review 2026-11-15)."""
        page = _text(error_pages.not_found_body())
        self.assertIn("<style>body>main.wrap>footer,#stzc-page>main.wrap>footer{margin-top:0;}</style>", page)
        self.assertLess(page.index("margin-top:0;}</style>"), page.index("</head>"))

    def test_link_expired_cancelled_and_unavailable_keep_their_markup(self):
        for alternatives in _ALTERNATIVES:
            marked = Markup(alternatives)
            self.assertEqual(jinja_env.render("fragments/link-expired.html", alternatives=marked),
                             _expired_before(alternatives))
            self.assertEqual(jinja_env.render("fragments/turno-cancelled.html", alternatives=marked),
                             _cancelled_before(alternatives))
            self.assertEqual(jinja_env.render("fragments/turno-unavailable.html", alternatives=marked),
                             _unavailable_before(alternatives))

    def test_the_pages_carry_those_bodies(self):
        alternatives = appointment_offer._alternatives_html(None)
        self.assertIn(_expired_before(alternatives), _text(appointment_confirm._expired_page()))
        self.assertIn(_cancelled_before(alternatives), _text(appointment_confirm._cancelled_page()))
        self.assertIn(_unavailable_before(alternatives), _text(appointment_offer._unavailable_page()))
        self.assertIn(_DATABASE_UNAVAILABLE_BEFORE, _text(appointment_offer._database_unavailable_page()))
        self.assertEqual(jinja_env.render("fragments/turno-database-unavailable.html"),
                         _DATABASE_UNAVAILABLE_BEFORE)


def _forbidden_before(tid, shown):
    """auth/forbidden_screen.page_html as the f-string built it (head and body, CSS elided to its first rule)."""
    brand = theme.brand_name(tid)
    return f"""<!doctype html>
<html lang="es" data-theme="{tid}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>No tenés permiso · {brand}</title>
<meta name="robots" content="noindex,nofollow">
{theme.fonts(tid)}
<link rel="stylesheet" href="{static_fingerprint.url('human-rounds.css')}">
<link rel="stylesheet" href="{static_fingerprint.url('admin-gate.css')}">
<style>
body{{background:#f6f7f5;margin:0;color:#20231f}}
.forb{{width:min(100% - 32px,460px);margin:10vh auto;background:#fff;border:1px solid #dfe3dd;border-radius:16px;padding:28px;box-sizing:border-box}}
.forb h1{{font-size:26px;margin:0 0 10px}} .forb p{{line-height:1.5;color:#5b625b}}
.forb .forb-path{{font-family:ui-monospace,monospace;font-size:14px;color:#7b827b;word-break:break-all}}
.forb .btn{{margin-top:18px}}
</style></head><body>
<main class="forb">
<h1>No tenés permiso para esta pantalla</h1>
<p>Tu cuenta puede entrar al panel, pero no a<span class="forb-path">&nbsp;{shown}</span>.</p>
<p>Si necesitás este acceso, pedile a administración que lo habilite.</p>
<a class="btn btn-ok btn-lg" href="/admin">Ir al panel</a>
</main></body></html>""".encode()


class TestForbiddenScreen(unittest.TestCase):
    def test_the_page_keeps_its_bytes_for_both_themes(self):
        for tid in ("humanrounds", "pinamar"):
            self.enterContext(patch.object(theme, "current", lambda tid=tid: tid))
            for path, shown in (("/admin/users", "/admin/users"), ("", ""), (None, ""),
                                ("/admin//a///b", "/admin/a/b"), ("/admin/ñandú", "/admin/ñandú"),
                                ("/" + "x" * 300, "/" + "x" * 120)):
                self.assertEqual(forbidden_screen.page_html(path), _forbidden_before(tid, shown), (tid, path))

    def test_a_path_with_markup_is_escaped(self):
        page = forbidden_screen.page_html("/admin/<script>alert(1)</script>").decode("utf-8")
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)


if __name__ == "__main__":
    unittest.main()
